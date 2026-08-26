import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { DEFAULT_NEXT_PATH, loginHref, safeNextPath } from "../lib/authRedirect.ts";
import { DEFAULT_LANE_M, dividerOffsetCoord, laneBoundaryOffsets, offsetPolyline, screenConstantSize } from "../lib/roadWidth.ts";
import { messageWithoutSubject } from "../lib/findingMessage.ts";
import { readReportManifest } from "../lib/reportManifest.ts";
import {
  BASIS_BOUNDARY_CUT,
  BASIS_RUNWAY_DEFICIT,
  PREFILL_KIND_MANUAL_REGION_EDIT,
  REGION_PREFILL_STORAGE_KEY,
  stashRegionPrefill,
  takeRegionPrefill,
} from "../lib/region-prefill.ts";
import {
  CALIBRATION_STATIONS,
  DEFAULT_STATION,
  MRWA_STEP_BY_TOOL,
  STATION_BY_STEP,
  STATION_LABELS,
  groupProposals,
  groupSteps,
  placeProposal,
  stationForStep,
  stationResolverFor,
  stationRowsShown,
} from "../lib/calibrationStations.ts";
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
 * The calibration workspace's DOCTRINE, as a file (#50-d, 2026-08-20).
 *
 * Read from the repo root rather than from `frontend/`, because it is not a
 * frontend asset: `docs/product/calibration-doctrine.md` is the single source of
 * truth for every sentence the user's 2026-08-20 decision evicted from the
 * /calibration screen, and it has three readers — this suite, the /docs page
 * (through lib/help-content.tsx), and the assistant's RAG corpus, which is
 * rooted at `docs/` (backend/services/rag_documents.py). Pinning the file itself
 * is what makes "the sentence moved" a checkable claim instead of a promise: a
 * sweep that DELETED a doctrine sentence rather than moving it fails here, and
 * a sweep that quietly put one back on the screen fails in the ledger below.
 */
function readCalibrationDoctrine(): string {
  return readFileSync(
    path.join(repoRoot, "docs", "product", "calibration-doctrine.md"),
    "utf-8",
  );
}

/**
 * The approval surface, which is now TWO files.
 *
 * Unit #50 lifted unit #47's approval and diagnosis cards out of
 * FloatingAssistant.tsx into components/agent/ApprovalCards.tsx so the
 * calibration workspace renders the same cards instead of a second copy of
 * them. The drawer kept what only a drawer has -- the stream and
 * `runAgentApproval`, the handler that POSTs the decision -- so a pin that
 * describes "what the approval surface does" legitimately spans both files
 * now, and reading them as one source is what keeps those pins meaning the
 * same thing they meant before the move.
 *
 * This makes `doesNotMatch` pins STRICTER, not looser: a banned pattern is now
 * banned from the card module too. Pins that must stay scoped to one file
 * still call `read` directly.
 */
function readApprovalSurface(): string {
  return `${read("components/FloatingAssistant.tsx")}\n${read("components/agent/ApprovalCards.tsx")}`;
}

/** Every `.ts`/`.tsx` under `app/` and `components/`, repo-relative.
 *
 * The suite reads files BY NAME everywhere else, on purpose: a named pin says
 * which file it is about. This walker exists for the one shape a named list
 * cannot express -- "and nowhere ELSE in the app" -- which is what bounds an
 * escape hatch to a known inventory instead of letting it spread silently into
 * the next screen somebody writes. */
function appSourceFiles(): string[] {
  const found: string[] = [];
  const walk = (relative: string) => {
    for (const entry of readdirSync(path.join(rootDir, relative), { withFileTypes: true })) {
      const child = `${relative}/${entry.name}`;
      if (entry.isDirectory()) walk(child);
      else if (/\.tsx?$/.test(entry.name)) found.push(child);
    }
  };
  walk("app");
  walk("components");
  return found.sort();
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
 * ONE function's source — declaration through the `}` that closes it at column
 * 0 — CRLF-safe, and `""` when either end is missing.
 *
 * `sliceBetween(source, declaration, "\n}\n")` is the idiom this replaces, and
 * on a Windows checkout it is a trap: the file's line endings are `\r\n`, the
 * end marker is not, and the slice comes back EMPTY. Measured 2026-08-26 on
 * unit #59's own pins. An empty haystack makes every `pin.match` on it fail
 * loudly (which is merely noisy) and every `pin.doesNotMatch` pass VACUOUSLY
 * (which is the failure a contract pin may never have: green over a live
 * violation). `topLevelFunction` above learned this in 2026-08-21; this is the
 * same lesson for a function that is NOT exported, and for the `doesNotMatch`
 * side, where the cost of getting it wrong is silence rather than noise.
 */
function functionSource(source: string, declaration: string): string {
  const start = source.indexOf(declaration);
  if (start === -1) return "";
  const close = /\r?\n\}\r?\n/.exec(source.slice(start));
  return close ? source.slice(start, start + close.index + close[0].length) : "";
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

/**
 * A `t(ko, en, ja)` call's three arguments, quote-boundary anchored (the
 * #55b/#56 idiom) so a partial or reworded string cannot satisfy the pin.
 *
 * `quote` is the character each argument is wrapped in at the source --
 * `"` for a plain string literal, a backtick for a template literal that
 * splices a value in (`${nodeId}` and friends survive the same escaping
 * `literalPattern` uses, since `$`, `{` and `}` are in its class too, so the
 * LITERAL characters `${nodeId}` in the source are what this matches -- never
 * an evaluated value). Loose `\s*,\s*` between arguments tolerates
 * reformatting; the quoted CONTENT itself must be byte-exact, which is what a
 * blanked or reworded language argument breaks.
 */
function tripleArgs(ko: string, en: string, ja: string, quote: '"' | "`" = '"'): RegExp {
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const q = quote === "`" ? "`" : '"';
  return new RegExp(`${q}${esc(ko)}${q}\\s*,\\s*${q}${esc(en)}${q}\\s*,\\s*${q}${esc(ja)}${q}`);
}

/**
 * `border-sky-300` -- the dark-remap fall-through, in ONE place.
 *
 * globals.css's dark `@theme` remaps sky 50/200/400/500/600 and nothing else, so
 * a `border-sky-300` keeps Tailwind's stock #7dd3fc: a bright cyan hairline
 * nothing else in this app draws. The genesis screens were swept for it first;
 * the calibration surface followed on 2026-08-21 after the same class arrived on
 * a new component (#51's start control).
 *
 * Anchored on a pair of quotes with no newline between them, so it fires on a
 * class STRING -- a className attribute or a classes-returning helper -- and not
 * on the doc comments that legitimately name the shade while explaining why it
 * is banned. Both users of it pass `withoutComments`-free source for that reason.
 *
 * One definition, two tests: a sweep that "fixed" this by loosening the pattern
 * would have to loosen it for both surfaces at once, which is visible here
 * rather than buried in one test body.
 */
const UNMAPPED_SKY_BORDER = /"[^"\n]*border-sky-300[^"\n]*"/;

/**
 * The source of ONE exported top-level function, so a `doesNotMatch` can mean
 * "not in THIS component" instead of "not in this file".
 *
 * Needed because "no start control unless the runner is wired" is a statement
 * about `ParameterSearchTrack`, and the file it lives in legitimately contains
 * other buttons (the rail's own station rows). A whole-file pin would either be
 * vacuous or wrong.
 *
 * The slice runs from the declaration to the first line that is exactly `}` --
 * this codebase's formatting for a top-level close, and Prettier's. Feed it
 * `withoutComments(...)` when the pin is a `doesNotMatch`: a JSX comment inside
 * the function collapses to an indented `{}`, never to a `}` at column 0, so the
 * boundary survives stripping.
 *
 * CRLF-tolerant on purpose (measured 2026-08-21: a tool that rewrote one of
 * these files with Windows line endings made the boundary unfindable and the
 * whole pin failed with a message about formatting rather than about the thing
 * it guards). A pin that a line-ending change can silence is not a pin.
 */
function topLevelFunction(source: string, name: string): string {
  const start = source.indexOf(`export function ${name}(`);
  assert.ok(start >= 0, `expected ${name} to be an exported top-level function`);
  const close = /\r?\n\}\r?\n/.exec(source.slice(start));
  assert.ok(close, `expected ${name} to close with a } at column 0`);
  return source.slice(start, start + close.index + close[0].length);
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
  // RE-PINNED, unit #50 (the user's 2026-08-19 page-cleanup decision):
  // /base-model stopped being a nav route. The SCREEN did not go anywhere --
  // it is the scoring station inside /calibration
  // (components/calibration/BaseModelStation.tsx) and /base-model 308s there
  // (next.config.ts) -- but the sidebar row it used to have is now 보정.
  pin.match(shell, /href: "\/calibration"/, "expected the calibration workspace route in top navigation");
  pin.doesNotMatch(
    shell,
    /href: "\/base-model"/,
    "expected /base-model to have no nav row -- it was absorbed into /calibration and redirects there",
  );
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
  // RE-PINNED, unit #67 (2026-08-26 DECISIONS): /simulation is listed in top
  // navigation for the FIRST time -- not "back". It was never there before
  // (git -S 'href: "/simulation"' on this file turns up nothing before this
  // unit's own commit; only the doesNotMatch pin this replaces existed,
  // since the initial import). It lands as ③'s own first row -- where a run
  // actually gets created -- and #65's zero-runs banner on /calibration now
  // sends readers here by name, so a hidden nav entry and a named CTA target
  // would disagree about how to find this screen.
  pin.match(shell, /href: "\/simulation"/, "expected simulation route to be present in top navigation");
  pin.match(simulationPage, /SimulationPage/, "expected simulation page implementation to remain available in the codebase");
  pin.doesNotMatch(shell, /Intersection Comparison/, "expected separate intersection comparison label to be removed");
  // RE-PINNED, unit #50: the row this label named is now 보정 / Calibration,
  // and the base-model screen it pointed at is that page's scoring station.
  pin.match(
    shell,
    /label: t\("보정", "Calibration", "補正"\)/,
    "expected the calibration workspace label in top navigation",
  );
  pin.doesNotMatch(
    shell,
    /Base Model Validation/,
    "expected the base-model row's label to be gone with its route",
  );
});

// Unit #68 (2026-08-26): a workspace whose active dataset was uploaded as a
// bare .inpx (no project.vissimpdb / Scenarios/) used to be refused at the
// simulation door with a message claiming a full project ZIP carrying
// scenario (.vissimpdb) data was required. Measured false: the runner's
// default (no scenario_id) run path never reads project.vissimpdb/Scenarios
// at all (routers/simulations.py's `_simulation_capabilities`, traced through
// `_run_simulation_locked` -> `build_simulation_network` ->
// `services.scenario_project.build_scenario_tree`). The honest gate is "an
// uploaded dataset, zip OR inpx, whose resolved source .inpx exists" -- these
// pins hold that door open and keep the old zip-only, .vissimpdb-naming copy
// from coming back.
contractTest("#68: an uploaded bare-inpx dataset opens the simulation run door like a zip", (pin) => {
  const page = read("app/simulation/page.tsx");

  pin.match(
    page,
    /const hasUploadedDataset = dataset\?\.source === "uploaded";/,
    "expected the simulation page's run-door gate to accept any uploaded dataset (zip or inpx), not just zip",
  );
  pin.match(
    page,
    /const requiresUploadedDataset = capabilities\?\.requires_uploaded_dataset \?\? true;/,
    "expected the simulation page to read the renamed, truthful capabilities field",
  );
  // The combinator itself, not just its two inputs: hardcoding
  // datasetReady=true still passed every OTHER pin in this file (the Run
  // button stays correctly guarded by runtimeReady/can_run either way), but
  // it silently breaks which troubleshooting message the reader sees --
  // pinned directly per refuter finding 2026-08-26.
  pin.match(
    page,
    /const datasetReady = !requiresUploadedDataset \|\| hasUploadedDataset;/,
    "expected datasetReady to be satisfied by EITHER no dataset being required OR an uploaded dataset being present",
  );
  pin.doesNotMatch(
    page,
    /requires_uploaded_zip/,
    "expected the retired, now-inaccurate `requires_uploaded_zip` capabilities field name to be gone from the simulation page",
  );
  pin.match(
    page,
    /실행하려면 VISSIM 데이터셋\(INPX 파일 또는 프로젝트 ZIP\)을 먼저 업로드해야 합니다\./,
    "expected the truthful dataset-required refusal message naming both accepted kinds",
  );
  // Comment-stripped, matching this file's own withoutComments convention:
  // the fix's explanatory comments legitimately NAME project.vissimpdb (to
  // say the run path does NOT need it) -- only a live claim in code or
  // user-facing copy should fail this pin, never the comment that explains it.
  pin.doesNotMatch(
    withoutComments(page),
    /vissimpdb/i,
    "expected no live claim that .vissimpdb is required to run a simulation -- the default run path never reads it (unit #68)",
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
  // Unit #50 (user decision, 2026-08-19): 모델 검진 answers "is this network
  // coded correctly", which is MRWA §2.12.1's step ① and the first thing the
  // calibration flow rail reads -- so it sits with the screens that BUILD the
  // network, not with the ones that score runs.
  pin.match(networkGroup, /href: "\/health"/, "expected 모델 검진 under ① 네트워크");
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
  // RE-PINNED, unit #50 (user decision, 2026-08-19): 베이스모델 검증 became 보정's
  // scoring station and 모델 검진 moved up to ①, both pinned in their new homes
  // above. RE-PINNED AGAIN, unit #67 (2026-08-26 DECISIONS): ③ is three rows
  // now, not two -- /simulation joins the sidebar for the first time (it was
  // never here before, see the ROUTE_STAGE comment on AppShell.tsx), as its
  // own first row, pipeline order (실행 -> 조회 -> 채점) ahead of 대시보드 and
  // 보정, both of which need a completed run to already exist.
  pin.match(simGroup, /href: "\/simulation"/, "expected the simulation runner under ③ 시뮬·보정");
  pin.match(simGroup, /href: "\/dashboard"/, "expected the dashboard under ③ 시뮬·보정");
  pin.match(simGroup, /href: "\/calibration"/, "expected the calibration workspace under ③ 시뮬·보정");
  pin.doesNotMatch(simGroup, /href: "\/health"/, "expected 모델 검진 to have moved out of ③ into ① 네트워크");
  // Unit #67 (D4 fix): the LABEL, not just the route, in all three languages --
  // #65's zero-runs CTA sends readers here by the name "③ 시뮬레이션", so the
  // row's own label drifting away from that name (while its href stays put)
  // would leave the CTA pointing at a name nothing in the sidebar carries any
  // more. Same idiom the source itself uses (`label: t(ko, en, ja)`).
  pin.match(
    simGroup,
    /label: t\("시뮬레이션", "Simulation", "シミュレーション"\)/,
    "expected the simulation row's label to read 시뮬레이션 / Simulation / シミュレーション, byte-exact in all three languages",
  );
  // RE-PINNED as a computed-value pin (unit #67 fix, D2): the previous plain
  // `assert.ok` here is exactly the trap CLAUDE.md names -- a bare assert
  // throws on the first failure and hides every pin after it in this same
  // test (2026-08-05 precedent: one stale pin like it hid 2 broken + 53
  // healthy ones). Worse, `.indexOf` returns -1 for a MISSING href, and -1 is
  // less than any real index, so the old ordering check `simIdx < dashIdx`
  // silently PASSED when the /simulation row was deleted outright -- fail-
  // open, not fail-closed. `inOrder` below requires all three indices to be
  // non-negative AND increasing, so a deleted or reordered row shows up as
  // `inOrder=false` in the pin's own output rather than as a false pass, and
  // `pin.match` reports it alongside every other pin above instead of
  // aborting the test.
  const simIdx = simGroup.indexOf('href: "/simulation"');
  const dashIdx = simGroup.indexOf('href: "/dashboard"');
  const calIdx = simGroup.indexOf('href: "/calibration"');
  const inOrder = simIdx >= 0 && dashIdx >= 0 && calIdx >= 0 && simIdx < dashIdx && dashIdx < calIdx;
  pin.match(
    `simIdx=${simIdx} dashIdx=${dashIdx} calIdx=${calIdx} inOrder=${inOrder}`,
    /inOrder=true$/,
    "expected ③'s rows in pipeline order: 시뮬레이션, then 대시보드, then 보정 (all three present, in order)",
  );

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
  const page = read("components/calibration/BaseModelStation.tsx");

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
  const page = read("components/calibration/BaseModelStation.tsx");

  // Quote-boundary anchored (refuter NIT 3, #56): an unanchored substring
  // match survives a rename to e.g. "vissim:page-context-v2" -- a REAL break,
  // since FloatingAssistant's listener is bound to the exact string. Anchored
  // to the literal dispatch statement all four publishers share.
  pin.match(
    page,
    /new CustomEvent\("vissim:page-context", \{/,
    "expected base-model page to dispatch the EXACT event name FloatingAssistant listens for -- a suffixed rename must fail this pin",
  );
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
  const page = read("components/calibration/BaseModelStation.tsx");

  pin.match(page, /mt-4 grid gap-3 lg:grid-cols-\[1\.6fr_1fr\]/, "expected base-model setup controls to align in one card row");
  pin.match(page, /<div className="grid gap-3 sm:grid-cols-2">/, "expected the dataset and workbook cards to pair up inside that row");
  pin.match(page, /fullWidth className="h-full"/, "expected base-model dataset control to match adjacent setup-card sizing");
});

contractTest("base model page keeps the Category 1 GEH and travel-time checks", (pin) => {
  const page = read("components/calibration/BaseModelStation.tsx");
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
  const page = read("components/calibration/BaseModelStation.tsx");
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
  const page = read("components/calibration/BaseModelStation.tsx");

  pin.match(page, /fetchActiveDataset/, "expected base-model page to check whether a VISSIM dataset is uploaded");
  pin.match(page, /shouldLoadScenarioSelection \? fetchScenarios\(\) : Promise\.resolve<ScenarioMeta\[\]>\(\[\]\)/, "expected default dataset state to keep scenario dropdowns empty");
  pin.match(page, /disabled=\{scenarios\.length === 0\}/, "expected scenario dropdowns disabled until a dataset is uploaded");
  pin.match(page, /setPeriod\("AM"\)/, "expected dataset restore to reset the analysis-period dropdown");
});

contractTest("base model page renders the intersection-by-direction count matrix", (pin) => {
  const page = read("components/calibration/BaseModelStation.tsx");

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
  const page = read("components/calibration/BaseModelStation.tsx");

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
  // Quote-boundary anchored (refuter NIT 3, #56) -- see the base-model
  // publisher pin's own comment for why an unanchored substring match is a
  // real gap (a "-v2" rename would still contain "vissim:page-context").
  pin.match(
    page,
    /new CustomEvent\("vissim:page-context", \{/,
    "expected dashboard to dispatch the EXACT event name FloatingAssistant listens for",
  );
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

  // Quote-boundary anchored (refuter NIT 3, #56) -- see the base-model
  // publisher pin's own comment.
  pin.match(
    page,
    /new CustomEvent\("vissim:page-context", \{/,
    "expected network preview page to dispatch the EXACT event name FloatingAssistant listens for",
  );
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
  const floatingAssistant = readApprovalSurface();
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
  const floatingAssistant = readApprovalSurface();
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
  const floatingAssistant = readApprovalSurface();
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
  const floatingAssistant = readApprovalSurface();

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
  // #50-d took the same road, five units later: the calibration workspace's
  // doctrine is a fifth builder here rather than seven ⓘs and six folds on the
  // screen it describes.
  pin.match(help, /export function calibrationHelpEntries\(/, "expected the calibration doctrine to live in the shared module too");
  pin.doesNotMatch(help, /await fetch|apiFetch|apiMutationFetch/, "doctrine is static -- the module must never fetch");
  pin.doesNotMatch(docs, /await fetch|apiFetch|apiMutationFetch/, "the documentation page renders static doctrine only -- it must never fetch");

  // The page renders ALL FIVE areas, not whichever subset survived a refactor.
  pin.match(docs, /genesisHelpEntries\(t\)/, "expected /docs to render the hub's entries");
  pin.match(docs, /regionHelpEntries\(t\)/, "expected /docs to render the region entries (no cap passed -- the ?? 64 fallback is the honest browser-side number)");
  pin.match(docs, /phasesHelpEntries\(t\)/, "expected /docs to render the phases entries");
  pin.match(docs, /jobHelpEntries\(t\)/, "expected /docs to render the job-detail entries");
  pin.match(docs, /calibrationHelpEntries\(t\)/, "expected /docs to render the calibration entries");
  // ...and the calibration section carries the way back to the screen it is
  // about, the same as every other section here. A doctrine page a reader
  // cannot leave for the work is a dead end.
  pin.match(
    docs,
    /id: "calibration",[\s\S]{0,300}?href: "\/calibration"/,
    "expected the calibration section to link to the screen it documents",
  );

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
  // (The listing became a nonce-keyed resource, lib/asyncResource.ts, so the
  // call moved out of an async `loadFetches` and into the effect that answers
  // its key -- same endpoint, same entries, same state filled from them.)
  pin.match(
    page,
    /fetchRegionFetches\(\)\s*\n\s*\.then\(\(entries\) => \{/,
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
// #60a (2026-08-25, live-test feedback) -- two of the "화면 정리 5건": the
// polygon/bbox explainer sentence is gone, and the provenance record moves
// behind a fold. Its own test, single-concern, so a future style pass on
// either of these two cannot hide inside an unrelated failure.
// ---------------------------------------------------------------------------

contractTest("#60a: the region page drops the polygon/bbox sentence and folds provenance behind 자세히", (pin) => {
  const page = read("app/genesis/region/page.tsx");

  // THE POLYGON/BBOX EXPLAINER, gone in all three languages -- the user's own
  // words were "지워". `withoutComments` because the removal note left in the
  // source legitimately names the retired sentence while explaining why it
  // left (this file's own convention for exactly this shape of pin).
  pin.doesNotMatch(
    withoutComments(page),
    /폴리곤 \$\{selected\.polygon\.vertices\.length\}점으로 질의했고|Queried as a \$\{selected\.polygon\.vertices\.length\}-corner polygon|ポリゴン \$\{selected\.polygon\.vertices\.length\} 点で問い合わせ/,
    "expected the polygon/bounding-box explainer sentence to be gone in ko, en AND ja -- #60a retired it, and a partial removal in one language is not a removal",
  );
  // `selected.polygon` itself is untouched -- this is a prose deletion, not a
  // data one. The lasso payload, its trim and its model-boundary decision are
  // still pinned as the drawn ring in the B2 lasso test below.
  pin.match(
    page,
    /return vertices\.length >= 3 \? \{ polygon: vertices \} : null;/,
    "expected the lasso's own polygon payload to be untouched -- #60a removed a sentence about it, not the shape it describes",
  );

  // THE PROVENANCE BLOCK, still whole, now behind a fold. `withoutFolds`
  // (this file's own helper -- see its doc comment, "removes fold CONTENTS
  // and keeps the surface") is reused rather than a bespoke slice: it already
  // balances nested `<details>` and fails closed (keeps the whole file) when
  // it cannot, so a mutation that unbalances the markup surfaces as a pin
  // failure instead of a silently-vacuous check.
  const surface = withoutFolds(page);
  for (const field of ["endpoint", "fetched_at", "bytes", "sha256", "licence", "query"]) {
    pin.doesNotMatch(
      surface,
      new RegExp(`selected\\.provenance\\.${field}`),
      `expected the extract's ${field} to render ONLY inside the provenance fold -- #60a moved the record one click away, it did not put a second copy on the surface`,
    );
  }
  // ...and every one of those fields is STILL in the raw file -- #60a moved
  // data, it did not cut it. (The same six-field loop already runs against
  // raw `page` earlier in this file, "Provenance is the product thesis" --
  // this is the fold-scoped half of that same claim.)
  for (const field of ["endpoint", "fetched_at", "bytes", "sha256", "licence", "query"]) {
    pin.match(
      page,
      new RegExp(`selected\\.provenance\\.${field}`),
      `expected the extract's ${field} to still exist in the raw source -- folded, never deleted`,
    );
  }

  // THE FOLD ITSELF is a real `<details>`, and its own heading is the
  // `<summary>` trigger -- so what a reader always sees is the LABEL
  // ("출처 기록 (provenance)"), never the seven lines under it.
  pin.match(
    page,
    /<details className="mb-5 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-\[14px\] leading-relaxed text-slate-700">\s*\n\s*<summary className="cursor-pointer text-\[15px\] font-semibold text-slate-800">\s*\n\s*\{t\("출처 기록 \(provenance\)", "Provenance", "出所の記録 \(provenance\)"\)\}/,
    "expected the provenance record's own container to be the <details> element, with its heading as the <summary> trigger",
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
  // corners, the measure effect returns on null `parsed` BEFORE it arms the
  // debounce timer -- so opening this page fires no Overpass-limits preview for
  // a shape nobody drew yet -- and no measurement is readable for it either.
  // That second half is now a DERIVATION (`idle`, lib/asyncResource.ts) instead
  // of a setState the effect ran before its own call: it also closes the window
  // in which an EDITED ring still read the previous ring's within_region_cap
  // verdict, which is the verdict the extract button is enabled by.
  pin.match(
    page,
    /const \{ data: measurement, error: measureFailure \} = resolve\(measureLoaded, measureKey, \{\s*\n\s*idle: !parsed,/,
    "expected the measurement to be readable ONLY for the ring currently drawn, and idle before three corners",
  );
  pin.match(
    page,
    /if \(!parsed\) return;\s*\n\s*let cancelled = false;\s*\n\s*const timer = setTimeout\(/,
    "expected an unfinished shape to return BEFORE the preview is scheduled -- zero corners must fire no request",
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
  // while `vertices` (and the map) already hold the correct value there. The
  // resync is a RENDER-TIME read of a draft tagged with the vertex it was typed
  // against (it used to be an effect, which landed one frame late); an invalid
  // draft still survives, because an invalid draft never changes `lat`/`lon`.
  pin.match(
    page,
    /const boundTo = `\$\{lat\}\|\$\{lon\}`;/,
    "expected a row's draft to be tagged with the vertex it was typed against",
  );
  pin.match(
    page,
    /const latText = draft && draft\.boundTo === boundTo \? draft\.lat : String\(lat\);\s*\n\s*const lonText = draft && draft\.boundTo === boundTo \? draft\.lon : String\(lon\);/,
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
  // (`RegionLassoMap`'s `if (current.closed) return;`). What the map is handed
  // is DERIVED from the user's closed-intent and the corner count, so "closed"
  // means "a real ring" in the same render that shrank the array -- as an
  // effect the degenerate polygon was drawn for one frame before it corrected.
  pin.match(
    page,
    /const ringClosed = ringClosedIntent && vertices\.length >= 3;/,
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
  // RE-PINNED 2026-08-19 (the app-wide set-state-in-effect pass). Both findings
  // above are about state the EFFECT ITSELF SET: `loading` could not be a
  // dependency because the effect raised it, and `loadError` had to sit in the
  // guard because the effect cleared it. GenesisTileMap now holds one keyed
  // answer (`Loaded<DraftMap>`, lib/asyncResource.ts) from which `loading` and
  // `loadError` are DERIVED during render, so neither is state and neither can
  // re-fire the effect. The two measured failures are not merely still fixed --
  // they are no longer expressible.
  //
  // So these pins now protect the PROPERTY rather than the old spelling: the
  // fetch effect must not raise a loading flag, and must not re-fetch a job it
  // has already answered.
  const fetchEffectDeps = sliceBetween(tileMap, "}, [hasArtifact, jobId,", "]);");
  pin.match(fetchEffectDeps, /\S/, "expected the fetch effect's dependency array in GenesisTileMap.tsx (markers moved? the doesNotMatch below would pass vacuously on empty input)");
  pin.doesNotMatch(
    tileMap,
    /setLoading\(/,
    "the fetch effect must not raise a loading flag of its own -- `loading` is derived from the keyed answer, which is what makes the eternal-spinner race unexpressible",
  );
  pin.match(
    tileMap,
    /if \(!hasArtifact \|\| mapLoaded\?\.key === jobId\) return;/,
    "expected the guard to be 'nothing to fetch, or this job is already answered' -- a failure stores an answer under the same key, so it stays terminal for the job without a separate error flag",
  );
  pin.match(
    tileMap,
    /const \{ data: draft, error: loadError, loading \} = resolve\(mapLoaded, jobId/,
    "expected loading/loadError to be DERIVED from the keyed answer rather than kept as their own state",
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
  // #55 moved the drawn diagram -- ambiguity note included -- out of the page
  // and into its own component (docs/phase2/DECISIONS.md, "2026-08-24 --
  // 사용자 결정: 현시도 화면은 엑셀 업로드로 통합 ... (#55)"). The honesty core this
  // pin exists for did not move with it; only its FILE did, so the pin follows.
  const sketch = read("components/genesis/PhaseDiagramSketch.tsx");

  // The load-bearing sentence. A proposal (the vision path) is a machine
  // READING; the only claim of correctness on this page belongs to the person
  // who confirms the BINDING, never to the reading itself.
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
  // Re-anchored at its NEW location (components/genesis/PhaseDiagramSketch.tsx):
  // the strip renders every phase's ambiguity note -- the model naming what it
  // could NOT resolve -- verbatim, beside the drawing it qualifies.
  // VACUITY MEASURED 2026-08-24 (refuter, #55b round 2): `{false && phase.
  // ambiguity && (` keeps this pin green, because the bare substring
  // `phase.ambiguity && (` is still PRESENT inside the longer, dead chain --
  // a substring pin cannot tell rendered code from dead code. Anchored on the
  // `{` that opens the JSX expression immediately preceding `phase.ambiguity`:
  // a prepended `X &&` of any kind (not just `false` or `0`) inserts text
  // between that `{` and `phase.ambiguity`, breaking this exact adjacency.
  pin.match(
    sketch,
    /\{phase\.ambiguity && \(/,
    "expected every phase's ambiguity note to be rendered -- it is the model saying what it could NOT resolve (phase.ambiguity must be the FIRST operand of its JSX expression, not short-circuited behind another condition)",
  );
  // A confidence score would invite a threshold, and a threshold is how a
  // low-confidence guess gets promoted to a reading. Banned on BOTH files now
  // that the reading has two rendering surfaces.
  pin.doesNotMatch(page, /confidence/i, "expected no confidence score anywhere on the review screen");
  pin.doesNotMatch(sketch, /confidence/i, "expected no confidence score anywhere on the drawn Φ strip either");

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

contractTest(
  "unit #69: the staged-token panel offers a regenerate CTA, gated on stagedToken && jobId, and never signs anything",
  (pin) => {
    const page = read("app/genesis/phases/page.tsx");

    // THE USER'S ASK, verbatim (DECISIONS.md, "2026-08-26 — 사용자 지시 2건
    // 추가: #69 재생성 CTA"): "현시도 업뎃이 생기면 자동으로 재생성을 제안하는
    // CTA 넣는게 좋지 않을까? 자동으로 현시도 넣어서." An OFFER whose click IS
    // the execution -- a regeneration is a job, not a signing act, so one
    // click may start it with no extra confirmation gate -- but nothing may be
    // silent: the result has to land where the click happened.

    // 1. THE CTA LIVES INSIDE THE TOKEN-PANEL CONDITIONAL. Single-line markers
    //    only (a literal embedded "\n" in a marker is the CRLF trap this
    //    Windows checkout springs -- see functionSource's own note above).
    const tokenPanel = sliceBetween(page, "{stagedToken && (", "</Panel>");
    pin.match(tokenPanel, /\S/, "expected to find the token panel's conditional slice (markers moved?)");
    pin.match(
      tokenPanel,
      /이 signals 입력으로 재생성/,
      "expected the CTA's ko label to live INSIDE the stagedToken-gated block -- it renders only with a token",
    );
    // The CTA must not ALSO exist outside that block (a second, ungated
    // rendering would defeat "renders only with a token").
    const ctaMentions = (page.match(/이 signals 입력으로 재생성/g) ?? []).length;
    pin.match(`ctaMentions=${ctaMentions}`, /^ctaMentions=1$/, "expected the CTA's ko label to appear exactly once in the file");

    // 2. ENABLEMENT IS `stagedToken && jobId`, named so a reader (and this
    //    pin) can see which half is missing -- not folded into a bare
    //    `Boolean(...)` that hides the shape.
    pin.match(
      page,
      /const canRegenerateWithSignals =\s*\n?\s*Boolean\(stagedToken && jobId\) && !selectedJobHasSideInputs && !busy;/,
      "expected the CTA's enablement to be the stagedToken && jobId shape, plus the disclosed side-inputs guard and the in-flight guard",
    );
    pin.match(
      tokenPanel,
      /disabled=\{!canRegenerateWithSignals\}/,
      "expected the button's disabled state to read off the named enablement constant",
    );
    // Refuter finding 1(c) (2026-08-26): the selected 생성 작업's OWN
    // side_inputs is read off the SAME `jobs` list the picker already holds
    // -- no second fetch, and the disclosure is derived BEFORE any click.
    pin.match(
      page,
      /const selectedJob = jobs\.find\(\(job\) => job\.id === jobId\) \?\? null;/,
      "expected the selected job to be read off the existing jobs list, not a fresh fetch",
    );
    pin.match(
      page,
      /const selectedJobHasSideInputs = Boolean\(selectedJob\?\.side_inputs\);/,
      "expected the disclosure condition to be named and derived from the selected job's own side_inputs",
    );
    // Disabled is never silent: a title names exactly what is missing --
    // EITHER no job chosen, OR (refuter finding 1(c)) the chosen job already
    // carries a side input, which used to be discoverable only as a 409
    // AFTER the click.
    pin.match(
      tokenPanel,
      /title=\{\s*!jobId\s*\?/,
      "expected the disabled button to carry a title naming the missing jobId, not a bare greyed-out control",
    );
    pin.match(
      tokenPanel,
      /: selectedJobHasSideInputs\s*\n?\s*\?/,
      "expected the title to ALSO disclose the already-composed case, not just the no-job-chosen one",
    );
    // The disclosure is not just in a hover-only title -- a visible line
    // says it too, gated on the SAME condition, so it survives on a device
    // with no hover.
    pin.match(
      tokenPanel,
      /selectedJobHasSideInputs && \(/,
      "expected a visible disclosure line gated on selectedJobHasSideInputs, not hover-only",
    );

    // 3. THE CLICK HANDLER CALLS THE REGENERATE API FUNCTION, AND NEITHER OF
    //    the page's two signing/staging calls (a difference assertion: this
    //    is not just "confirmPhaseDiagram appears once, file-wide" -- it is
    //    "THIS handler in particular never reaches either of them").
    const handler = sliceBetween(
      page,
      "const regenerateWithStagedSignals = useCallback(async () => {",
      "}, [stagedToken, jobId, busy]);",
    );
    pin.match(handler, /\S/, "expected to find the regenerate-with-signals handler (markers moved?)");
    pin.match(
      handler,
      /regenerateGenesisJobWithSideInputs\(jobId, \{ signals_token: stagedToken \}\)/,
      "expected the click to call the typed regenerate-with-side-inputs helper with the parent job and the staged token",
    );
    pin.doesNotMatch(
      handler,
      /confirmPhaseDiagram\(|stageSignals\(/,
      "expected the CTA's own handler to call neither the page's signing act nor the plain staging call",
    );
    // No double-fire: the handler bails when already busy, and disables via
    // the shared `busy` flag like every other mutation on this page.
    pin.match(
      handler,
      /if \(!stagedToken \|\| !jobId \|\| busy\) return;/,
      "expected the handler to refuse to start a second request while one is already in flight",
    );
    // GAP D4 (refuter, 2026-08-26): the success state is a LINK the user
    // clicks, never a redirect the handler fires on their behalf -- a
    // structural pin over the handler's own source, not just "a Link exists
    // somewhere in the panel" (pin 4 below), so a future edit that added
    // `router.push`/`navigate` alongside the Link would still be caught here.
    pin.doesNotMatch(
      handler,
      /router\.push|\bnavigate\(/,
      "expected the CTA's handler to never auto-navigate -- the started result is a link the user clicks, not a redirect",
    );

    // 4. RESULT RENDERING IS WHERE THE CLICK HAPPENED, AND VERBATIM.
    //    Started: names the new job id and points at it -- unit #57's
    //    detail-carries-nav-target idiom, not a silent redirect.
    pin.match(
      tokenPanel,
      /signalsRegenerateJob && \(/,
      "expected a started regeneration to render its own result block",
    );
    pin.match(
      tokenPanel,
      /href=\{`\/genesis\/jobs\/\$\{signalsRegenerateJob\.id\}`\}/,
      "expected the started result to link to the new job's own detail page, not auto-navigate away from this screen",
    );
    // Refused: the server's own sentence, never a client paraphrase. The
    // reasons array is rendered bare -- `{reason}` -- with no template
    // literal wrapping it in this client's own words.
    pin.match(
      tokenPanel,
      /signalsRegenerateError\.reasons\.map\(\(reason\) => \(/,
      "expected the refusal's reasons to be mapped straight off the server's own array",
    );
    pin.match(
      tokenPanel,
      /<li key=\{reason\}>\{reason\}<\/li>/,
      "expected each reason to render bare -- verbatim pass-through, never a client-authored paraphrase",
    );
    pin.match(
      tokenPanel,
      /refusalHeadline\(\s*signalsRegenerateError\.code,/,
      "expected the refusal headline to translate the server's CODE, not invent new wording",
    );

    // 5. ko/en/ja TRIPLES.
    for (const triple of [
      /t\("이 signals 입력으로 재생성", "Regenerate with this signals input", "このsignals入力で再生成"\)/,
      /t\(\s*"재생성 시작됨", "Regeneration started", "再生成を開始しました"\s*\)/,
      /t\(\s*"재생성을 시작하지 못했습니다",\s*"The regeneration could not be started",\s*"再生成を開始できませんでした",?\s*\)/,
      /t\(\s*"여기서 확인", "view it here", "ここで確認"\s*\)/,
      // Refuter finding 1(c) (2026-08-26) -- the already-composed disclosure,
      // in both its forms (the title, and the visible line).
      /t\(\s*"이 생성 작업은 이미 side input을 가지고 있어, 두 번째 층을 추가하는 것은 아직 지원되지 않습니다\.",\s*\n\s*"This generation already carries a side input -- adding a second layer is not supported yet\.",\s*\n\s*"この生成ジョブは既にside inputを持っているため、二つ目のレイヤーの追加はまだサポートされていません。",\s*\n\s*\)/,
      /t\(\s*"선택한 생성 작업은 이미 side input을 가지고 있습니다/,
    ]) {
      pin.match(page, triple, `expected a real ko/en/ja triple: ${triple}`);
    }
  },
);

contractTest(
  "the reading column carries no CONFIRMATION affordance -- #55 removed that click, and #58's timing entry is not a relocation of it",
  (pin) => {
    const page = read("app/genesis/phases/page.tsx");
    const timing = read("components/genesis/PhaseTimingTable.tsx");

    // THE REGION BOUNDARY, RE-SCOPED 2026-08-25 (unit #58). Both authorities,
    // because this pin sits exactly where they meet:
    //
    //   * docs/phase2/DECISIONS.md, "2026-08-24 — 계약 핀의 위협 모델 경계
    //     (#55b 3패스에서 명시)" -- "위치 우회 → 슬라이스가 아니라 영역 전체.
    //     판독 패널은 설계상 표시 전용이므로 '상호작용 어포던스 0개'가 구조
    //     불변식이다."
    //   * docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도 카드
    //     단순화 — 도식+시간표, 짧은 확인 (#58)" -- the card now renders the
    //     stored 신호 시간 as an EDITABLE table, because "editing a timing
    //     value on screen is the uploader amending their own statement".
    //
    // WHAT #55b's invariant WAS ACTUALLY GUARDING, stated plainly so the
    // re-scope is not a quiet weakening: a CONFIRMATION-SHAPED CONTROL in the
    // reading column -- the per-intersection "이 판독을 확인합니다" click #55
    // deleted. Data entry is the opposite act. A person typing their own
    // 황색시간 into their own record is not confirming a machine's reading;
    // there is no machine reading in an uploaded workbook at all.
    //
    // SO THE INVARIANT KEEPS ITS TEETH AND NARROWS ITS CLAIM. page.tsx's own
    // reading column stays STRUCTURALLY input-free (the two pins below are
    // unchanged, and the measured tag set is byte-for-byte what #55b
    // measured); the ONE data-entry component it hosts is named by a
    // set-equality pin over the region's COMPONENT vocabulary -- so
    // componentisation cannot become the hole that "zero lowercase tags"
    // would otherwise leave -- and that component's own file is pinned
    // separately, below, to carry data-entry affordances and NOTHING that
    // confirms. The confirmation-WORDING ban stays file-wide and now covers
    // the new file too.
    //
    // GAP MEASURED 2026-08-24 round 2 (refuter): a wording ban scoped to a
    // slice starting at an INTERIOR comment survives a `<button>` placed just
    // above that comment's own start marker (still inside the same JSX
    // region a reader would call "the reading panel", but outside the text
    // window the pin actually read) -- and a wording-only ban survives
    // `role="button"` div with novel wording anyway. Per the recorded
    // threat-model boundary (docs/phase2/DECISIONS.md, "2026-08-24 — 계약
    // 핀의 위협 모델 경계 (#55b 3패스에서 명시)"): "위치 우회 → 슬라이스가 아니라
    // 영역 전체" -- the reading column is display-only BY DESIGN, so "zero
    // interactive affordances" is a STRUCTURAL invariant over the whole
    // region, not a wording sweep over a fragment of it.
    //
    // REGION, measured: starts at the `) : (` / `<>` that opens the
    // "review selected" render (the true branch boundary, not an interior
    // comment a few lines into the first Panel inside it) and runs through
    // everything display-only before "#55 — THE SECONDARY ACT" -- the
    // comment page.tsx's own author left marking where the interactive
    // binding UI begins. This covers FOUR panels (the scan/image, the stale-
    // reasons banner, the reading/Φ-strip panel, and the machine cross-check
    // panel), not just the one the round-2 mutation happened to target.
    const startMarker = ") : (\n            <>\n              <Panel";
    const endMarker = "{/* #55 — THE SECONDARY ACT.";
    const readingColumn = sliceBetween(page, startMarker, endMarker);
    pin.match(readingColumn, /\S/, "expected to find the reading column's own source slice (markers moved?)");

    // MEASURED 2026-08-24: the region legitimately contains exactly ONE
    // interactive element -- the per-review "엑셀로 내보내기 (미확인)" export
    // link (`<a href={phaseDiagramExportUrl(review.review_id)}>`), inside the
    // scan/image panel. It is a download control, not a confirmation
    // affordance, and it is carved by its own exact href rather than banning
    // `<a` less precisely -- every OTHER `<a`, every `<button>`,
    // `role="button"`, `onClick=`, `onKeyDown=`, and `tabIndex=` is still
    // banned, so a rogue interactive element anywhere else in the region,
    // under any wording, still fails here.
    pin.doesNotMatch(
      readingColumn,
      /<button\b|role=["']button|onClick=|onKeyDown=|onChange=|onInput=|onSubmit=|tabIndex=|<a\b(?!\s*href=\{phaseDiagramExportUrl\()/i,
      "expected zero interactive affordances in the reading column, except the one carved export link -- this region is display-only BY DESIGN, so ANY <button>/role=button/onClick/onKeyDown/onChange/onInput/onSubmit/tabIndex/<a> reintroduces the confirmation click #55 removed, under any wording",
    );

    // SELF-EVASION FOUND 2026-08-24, closed the same day: `<input
    // type="checkbox" onChange={...} />` survived the attribute-name ban
    // above in its FIRST form (onClick/onKeyDown only) -- a native form
    // control reached for its own natural event (onChange), not the button
    // family's. Rather than keep enumerating event-handler names one at a
    // time, closed the same way as the SVG vocabulary in
    // PhaseDiagramSketch.tsx: a set-equality pin over every host-element tag
    // this region actually uses. MEASURED 2026-08-24: exactly seven --
    // a (the carved export link), div, img, li, p, span, ul -- none of them
    // natively interactive. A tag entering (input, select, textarea, label,
    // button, details, canvas, video, iframe, ...) fails here even under an
    // event-handler name this suite has not named yet.
    const readingColumnTags = new Set<string>();
    for (const m of readingColumn.matchAll(/<([a-z][a-zA-Z0-9]*)/g)) readingColumnTags.add(m[1]);
    const actualReadingColumnTags = [...readingColumnTags].sort().join(",");
    pin.match(
      `tags=${actualReadingColumnTags}`,
      /^tags=a,div,img,li,p,span,ul$/,
      "expected the reading column's entire host-element vocabulary to equal this measured set exactly -- a NEW tag entering (input/select/textarea/label/button/details/canvas/...) fails here even under an event-handler name no attribute pin has named",
    );

    // UNIT #58 -- THE COMPONENT VOCABULARY, which is the hole the two pins
    // above would otherwise leave. Both of them read only LOWERCASE host
    // elements and literal attribute names, so a capitalised component -- with
    // any props it likes, under any prop NAME (`onAmend`, `onPick`, `onPress`)
    // -- passes them without being looked at. That is precisely how #58's
    // editable timing table entered this region, and pretending it did not
    // would make the invariant a naming convention instead of a structure.
    // MEASURED 2026-08-25: exactly three components render in this region --
    // Panel (chrome), PhaseDiagramSketch (#55b's Φ strip, display-only by its
    // own pinned rules), and PhaseTimingTable (#58's data entry, pinned below
    // in its own file). A FOURTH entering fails here, whatever it is called.
    const readingColumnComponents = new Set<string>();
    for (const m of readingColumn.matchAll(/<([A-Z][a-zA-Z0-9]*)/g)) readingColumnComponents.add(m[1]);
    pin.match(
      `components=${[...readingColumnComponents].sort().join(",")}`,
      /^components=Panel,PhaseDiagramSketch,PhaseTimingTable$/,
      "expected the reading column's component vocabulary to equal this measured set exactly -- a new component entering the display-only region is invisible to the host-tag and attribute pins above, so it is named here or it is not there",
    );

    // UNIT #58 -- AND THE ADMITTED COMPONENT MUST STAY DATA ENTRY. Its file
    // may carry <input>, onChange and onBlur (a person typing their own
    // number); it may carry NOTHING that confirms -- no <button>, no
    // role="button", no onClick/onKeyDown/onSubmit, no <a>, no tabIndex. The
    // single 확인 control on this screen belongs to the BINDING section, and
    // #58 changed that button's LABEL, not which section owns it.
    pin.match(timing, /<input\b/, "expected the timing table to be real data entry -- an <input> a person types into");
    pin.doesNotMatch(
      withoutComments(timing),
      /<button\b|role=["']button|onClick=|onKeyDown=|onSubmit=|tabIndex=|<a\b/i,
      "expected the timing table to carry no confirmation-shaped affordance -- data entry is <input>/onChange/onBlur, and a button in this file would be the reading-column confirm click #55 removed, wearing a new name",
    );
    pin.doesNotMatch(
      withoutComments(timing),
      /확인|confirm|確認/i,
      "expected no confirmation wording inside the timing table either -- the word 확인 on this screen belongs to the BINDING alone",
    );

    // SECOND LAYER, kept: the wording sweep, widened to the WHOLE FILE
    // (MEASURED 2026-08-24: zero occurrences of this pattern exist anywhere
    // in page.tsx today, including inside the binding/attestation sections,
    // so no carve is needed to go file-wide). Catches a confirmation-shaped
    // affordance built from something other than the five HTML mechanisms
    // above (e.g. a styled <span> or <li> with a synthetic keyboard handler
    // this suite has not named yet) by what it SAYS instead of what tag it
    // uses.
    pin.doesNotMatch(
      withoutComments(page),
      /판독\s*을?\s*확인|confirm this reading|判読\s*を?\s*確認/i,
      "expected no per-reading confirmation wording anywhere in the file -- 판독 확인 클릭은 제거 (#55), and only the BINDING keeps a 확인 control",
    );

    // Scope check, not a weakening: the BINDING section's own legitimate
    // confirm button must still exist and still be reachable. A pin broad
    // enough to catch a reintroduced reading-confirm button but blind to this
    // would be scoped wrong in the other direction.
    //
    // REWRITTEN 2026-08-25 for unit #58. AUTHORITY: docs/phase2/DECISIONS.md,
    // "2026-08-24 — 사용자 결정: 현시도 카드 단순화 — 도식+시간표, 짧은 확인
    // (#58)" -- the user, testing the live #55 page: "'이 결박을 확인합니다'
    // 같은 긴 버튼 말고 '확인' 버튼 하나만". That decision's own execution rule
    // is what this pin now asserts: "The binding's one short '확인' button
    // changes the LABEL and surface, not the act" -- so the label moved from
    // `이 결박을 확인합니다` to `확인`, and the ACT is pinned beside it (same
    // `submit`, same `canSubmit`) rather than left to the label to imply.
    pin.match(
      page,
      /\{t\("확인", "Confirm", "確認"\)\}/,
      "expected the 네트워크 결박 section's confirm control to carry #58's SHORT ko/en/ja label, unaffected by the reading-column sweep",
    );
    pin.doesNotMatch(
      withoutComments(page),
      /이 결박을 확인합니다|I confirm this binding|この結縛を確認します/,
      "expected the pre-#58 long button label to stay retired in all three languages -- not restored beside, or instead of, the short one",
    );
    pin.match(
      page,
      /onClick=\{submit\}\s*\n\s*disabled=\{!canSubmit\}/,
      "expected the short button to be the SAME act: `submit` behind it and `canSubmit` gating it -- #58 changed the label and the surface, never what is recorded",
    );
  },
);

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

  // SINCE #55 (docs/phase2/DECISIONS.md, "2026-08-24 -- 사용자 결정: 현시도 화면은
  // 엑셀 업로드로 통합 ... (#55)") these three words describe the BINDING
  // (attestation) state, demoted into its own section, not the reading -- a
  // reading is complete the moment somebody uploads their own sheet. Pinned on
  // the function's OWN signature so the claim is mechanical: `reviewStateLabel`
  // must be computed from the attestation's `confirmed`/`stale_reasons` fields,
  // never from anything on `review.proposal`. Moving the computation onto the
  // reading breaks this line even if the three words survive untouched.
  pin.match(
    page,
    /function reviewStateLabel\(row: \{ confirmed: boolean; stale_reasons: string\[\] \}, t: Translate\)/,
    "expected the three status words to be computed from the BINDING record's own fields (confirmed / stale_reasons), not from the reading",
  );

  // Roadmap C1 option (b): a refused or absent proposal is the SAME screen with
  // a blank form, not a separate workflow.
  pin.match(
    page,
    /!review\.proposal \|\| review\.proposal\.outcome === "refused"/,
    "expected a refused OR missing proposal to take the same branch -- the blank manual-entry form",
  );
  // #55 moved 네트워크 결박 (the binding form) below the fold; the blank-form
  // branch now points there by name instead of at "아래 양식" generically.
  // Anchored on the full sentence (the "판독 결과가 없습니다" prefix is unique to
  // THIS branch) rather than the bare "아래 네트워크 결박 절의 표에 직접 입력하십시오"
  // fragment, which also appears verbatim in noImageSentence() -- an unanchored
  // pin would pass even if this branch's own copy were rewritten.
  pin.match(
    page,
    /판독 결과가 없습니다 — 아래 네트워크 결박 절의 표에 직접 입력하십시오/,
    "expected the blank-form branch to say so in ko, naming the 네트워크 결박 section, rather than rendering an empty panel",
  );
});

// ---------------------------------------------------------------------------
// Unit #52 -- 현시도 입력: a template out, a workbook in, a blank one made here.
// Three new doors into the SAME review store, and the pins here are what keeps
// them doors rather than ways around the confirmation.
// ---------------------------------------------------------------------------

contractTest("the 현시도 screen offers all three ways in, and each one produces a proposal", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const api = read("lib/api.ts");

  // ① the template, ② the upload, ③ the blank creation.
  pin.match(page, /phaseDiagramTemplateUrl\(\)/, "expected the template download to be offered on the screen");
  pin.match(page, /템플릿 다운로드/, "expected the template control to be named in ko");
  pin.match(page, /uploadPhaseDiagramTemplate\(uploadFile\)/, "expected the upload to send the chosen workbook");
  pin.match(page, /accept="\.xlsx"/, "expected the upload control to ask for a workbook");
  pin.match(page, /createBlankPhaseDiagram\(trimmed/, "expected in-page creation to POST a named intersection");
  pin.match(page, /새 현시도 입력/, "expected the blank-creation control to be named in ko");
  pin.match(
    page,
    /blankName\.trim\(\) === "" \|\| busy/,
    "expected a blank name to disable creation -- a review nobody can identify cannot be bound to a node",
  );

  // A refused row is RENDERED, not counted away: a row nobody sees is a row
  // nobody fixes, and its whole intersection did not come in.
  pin.match(page, /uploadResult\.refusals\.map\(/, "expected every refused row to be rendered verbatim");
  pin.match(page, /refusal\.row != null/, "expected a refused row to name its Excel row number");

  // The client never assembles a proposal or an attestation of its own: it
  // hands the file to the server and renders what came back.
  pin.doesNotMatch(
    withoutComments(page),
    /outcome:\s*"proposed"/,
    "expected the page never to build a proposal itself -- it uploads a file and renders the server's reading",
  );
  pin.match(
    api,
    /export async function uploadPhaseDiagramTemplate\(/,
    "expected one upload call in the api module, so the raw-body shape has a single home",
  );
});

contractTest("a 현시도 review with no drawing says so and never fakes one", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const api = read("lib/api.ts");
  // #55 (docs/phase2/DECISIONS.md, "2026-08-24 -- 사용자 결정: 현시도 화면은 엑셀
  // 업로드로 통합 ... (#55)") split the rendering surface: the SCAN placeholder
  // stays here, but the drawn Φ strip moved into its own component. VACUITY
  // MEASURED 2026-08-24 (unit #55b refuter): a `<img src="placeholder.png">`
  // planted inside PhaseDiagramSketch.tsx made this test pass unchanged,
  // because it never read that file -- the no-stand-in-image invariant had
  // silently narrowed to half the surface it claims to cover. `sketch` closes
  // that gap. The strip's OWN <svg> is real, generated markup (not a stand-in
  // for a scan the app never made), which is exactly why it does not trip this
  // pin -- see components/genesis/PhaseDiagramSketch.tsx's own header comment.
  const sketch = read("components/genesis/PhaseDiagramSketch.tsx");

  // The three media, and the fact that only one of them HAS an image.
  pin.match(
    api,
    /medium: "pdf_band" \| "excel_upload" \| "blank_entry"/,
    "expected the render to declare which of the three input paths made it",
  );
  pin.match(api, /image_sha256: string \| null/, "expected the image sha to be nullable -- two media have none");
  pin.match(
    page,
    /review\?\.render\.medium === "pdf_band" && review\.render\.image_sha256 != null/,
    "expected the image band to render ONLY when this review actually holds one",
  );
  pin.match(page, /hasImage \? \(/, "expected the <img> to sit inside that branch");
  pin.match(page, /noImageSentence\(review\.render\.medium, t\)/, "expected the absence to be NAMED where the drawing would be");
  pin.match(page, /이미지 없음 — 엑셀 업로드로 만든 검토입니다/, "expected the excel placeholder to say in ko that there is no drawing");

  // A stand-in picture would make the attestation's binding -- "the drawing
  // this person actually looked at" -- unfalsifiable. Checked on BOTH files
  // that render this screen now.
  pin.doesNotMatch(
    withoutComments(page),
    /data:image|placeholder\.(png|svg|jpg)/,
    "expected no stand-in image anywhere on the review screen",
  );
  pin.doesNotMatch(
    withoutComments(sketch),
    /data:image|placeholder\.(png|svg|jpg)|<img\b/,
    "expected no stand-in image (or <img> at all) inside the drawn Φ strip -- it is a generated <svg>, never a scan substitute",
  );
  // The confirmation still sends the sha VERBATIM from the render, null and all.
  pin.match(
    page,
    /image_sha256: review\.render\.image_sha256/,
    "expected the confirmation to carry the stored sha unchanged -- never a synthesised one",
  );
});

contractTest("an uploaded sheet is provenance, and the panel says whose reading it is", (pin) => {
  const page = read("app/genesis/phases/page.tsx");

  // #55's core distinction (docs/phase2/DECISIONS.md, "2026-08-24 -- 사용자
  // 결정: 현시도 화면은 엑셀 업로드로 통합 ... (#55)"): an uploaded workbook is no
  // longer a proposal awaiting a click -- it is THAT PERSON'S OWN RECORD of the
  // reading. Two sources, two honest titles, ONE status word left (신뢰 is never
  // split into "우리가 검증한" vs "우리가 안 한" -- neither source is verified by
  // this app). The vision title must survive: an uploaded sheet was not read by
  // a model, and a model's reading was not uploaded by a person.
  pin.match(page, /모델의 판독 \(제안\)/, "expected the vision branch to keep its own title");
  pin.match(
    page,
    /업로드된 시트 \(올린 사람의 기록\)/,
    "expected an uploaded sheet to be titled as the UPLOADER'S OWN RECORD -- per #55, no longer a 제안 awaiting confirmation",
  );
  pin.match(
    page,
    /uploadedRecord\s*\n?\s*\?\s*t\("업로드된 시트 \(올린 사람의 기록\)"/,
    "expected the title to be chosen by the proposal's SOURCE (uploadedRecord), not by which branch was written last",
  );

  // HALF ONE -- 올린 사람 is that person's OWN WORD about the READING: named as
  // authorship, and the screen says outright that it does not referee it.
  pin.match(page, /올린 사람: \$\{review\.proposal\.uploaded_by/, "expected the uploader to be named as the author of the reading");
  pin.match(
    page,
    /이 앱은 기록의 정오를 판단하지 않습니다/,
    "expected the screen to say, in ko, that this app does not judge whether the uploader's record is right",
  );
  pin.match(
    page,
    /그 시트 자체가 그 사람의 진술입니다/,
    "expected the body to state plainly that the sheet IS that person's own statement of the reading",
  );

  // HALF TWO -- provenance must still never be dressed as the BINDING's
  // attestation. 확인자 names who attested the draft node/link binding
  // (attestation.attested_by); uploaded_by must never sit beside that word,
  // and the 확인자 line must be sourced from the attestation and nothing else.
  pin.doesNotMatch(
    withoutComments(page),
    /확인자[^\n]{0,80}uploaded_by/,
    "expected the uploader never to be printed as the 확인자 -- that name comes only from the binding attestation",
  );
  pin.match(
    page,
    /t\("확인자", "attested by", "確認者"\)\}: <b>\{review\.attestation\.attested_by\}<\/b>/,
    "expected 확인자 to be sourced only from the binding attestation's attested_by, never from proposal.uploaded_by",
  );
});

contractTest(
  "the PhaseDiagramProposal docstring states #55's reconciliation, and the retired wording cannot silently return",
  (pin) => {
    const api = read("lib/api.ts");

    // DEFECT MEASURED 2026-08-24 (refuter, #55b round 2): nothing in this test
    // file grepped for `PhaseDiagramProposal` or any docstring excerpt at all --
    // the refuter restored the ENTIRE pre-#55 docstring (the retired
    // "uploaded_by is PROVENANCE ... must never render beside the word 확인"
    // wording) and the suite stayed 203/203 green. A single-source-of-truth
    // doctrine that can silently revert is the exact problem #55 was fixing.
    const docstring = sliceBetween(
      api,
      "/** A READING. An artifact, never a fact",
      "export type PhaseDiagramProposal = {",
    );
    pin.match(docstring, /\S/, "expected to find the PhaseDiagramProposal docstring (boundary moved?)");

    // HALF ONE -- uploaded_by IS the authoring word for the READING (#55's
    // affirmative claim, not present in the pre-#55 wording at all).
    pin.match(
      docstring,
      /it is the authoring word for THIS reading/,
      "expected the docstring to state uploaded_by is the authoring word for the reading",
    );
    pin.match(
      docstring,
      /it is still never the 확인자 of anything/,
      "expected the docstring to state uploaded_by is never a 확인자 claim on its own",
    );

    // HALF TWO -- the reconciliation's prohibition, stated as two explicit,
    // opposite-direction bans (never printed beside; never the SOURCE of).
    pin.match(
      docstring,
      /`uploaded_by` must never render beside that\s*\n?\s*\*?\s*attested_by/,
      "expected the docstring to forbid uploaded_by ever rendering beside attested_by",
    );
    pin.match(
      docstring,
      /`attested_by` must never be filled from `uploaded_by`/,
      "expected the docstring to forbid attested_by ever being filled FROM uploaded_by",
    );

    // THE RETIRED WORDING must not silently return -- this is the exact
    // string the refuter restored wholesale.
    pin.doesNotMatch(
      docstring,
      /`uploaded_by` is PROVENANCE -- who supplied the sheet -- which the review\s*\n\s*\* screen must never render beside the word 확인/,
      "expected the pre-#55 'uploaded_by is PROVENANCE ... must never render beside the word 확인' wording to stay retired, not restored wholesale beside (or instead of) its #55 replacement",
    );
    pin.doesNotMatch(
      docstring,
      /Two sources since unit #52, one status\./,
      "expected the pre-#55 'Two sources since unit #52, one status.' opening sentence to stay retired",
    );

    // ROUND 3 -- CLOSING THE CLASS: the fragment pins above all still matched
    // after the refuter APPENDED a contradicting sentence right before the
    // closing `*/`. Per the recorded threat-model boundary
    // (docs/phase2/DECISIONS.md, "2026-08-24 — 계약 핀의 위협 모델 경계 (#55b
    // 3패스에서 명시)"), "문서 우회 → 문장 조각이 아니라 독스트링 블록 전체를
    // 핀": the whole doc-comment block, line for line, from its opening `/**`
    // through the closing `*/` immediately followed by the type it documents
    // -- so ANY inserted, appended, or reworded sentence anywhere in the
    // block breaks the match. MEASURED 2026-08-24 (extracted programmatically
    // via the same sliceBetween markers above, LF-only, 20 lines) -- built
    // with `\r?\n` between lines rather than a hardcoded `\n`, the same
    // CRLF-tolerance discipline `topLevelFunction` above documents.
    const DOCSTRING_LINES = [
      "/** A READING. An artifact, never a fact -- no field here asserts correctness,",
      " * and no string in this UI may imply one does.",
      " *",
      " * Two sources. `vision` is a model reading a rendered band (`model` names it,",
      " * `image_sha256` binds it) -- still a proposal, and the screen labels it one.",
      " * `excel_upload` is a spreadsheet somebody filled in and sent: no model read",
      " * it, it has no image, and since unit #55 (docs/phase2/DECISIONS.md,",
      ' * "2026-08-24 -- 사용자 결정: 현시도 화면은 엑셀 업로드로 통합 ... (#55)") it is no',
      " * longer a proposal awaiting a per-intersection click -- the upload itself IS",
      " * that person's own statement of the reading.",
      " *",
      " * `uploaded_by` therefore carries TWO things that must not be collapsed into",
      " * one: it is the authoring word for THIS reading (who read the sheet and typed",
      " * it in -- a claim this app makes about the record's origin, never about",
      " * whether it is correct), and it is still never the 확인자 of anything. The",
      " * word 확인 on this screen belongs exclusively to the BINDING -- the human act",
      " * of tying this reading's approaches to a generated draft's node and link ids",
      " * (MappingAttestation.attested_by), reached from each card's 네트워크 결박",
      " * section at signals-build time. `uploaded_by` must never render beside that",
      " * attested_by, and `attested_by` must never be filled from `uploaded_by`. */",
    ];
    const docstringBlockPattern = new RegExp(
      DOCSTRING_LINES.map((line) => literalPattern(line).source).join("\\r?\\n") +
        '\\r?\\nexport type PhaseDiagramProposal = \\{',
    );
    pin.match(
      api,
      docstringBlockPattern,
      "expected the PhaseDiagramProposal docstring to equal this EXACT block, line for line, immediately followed by the type it documents -- any inserted, appended, or reworded sentence anywhere in it fails here even where every fragment pin above stays green",
    );

    // SELF-EVASION FOUND 2026-08-24, closed the same day: the block-equality
    // pin above is scoped to the DOC COMMENT only -- it says nothing about the
    // TYPE BODY that follows. A contradicting field-level `//` comment on
    // `uploaded_by` itself (an entirely ordinary TypeScript idiom, not
    // obscure obfuscation -- plausible honest drift from a well-meaning
    // future editor) sits outside every pin above and survives. Widened to
    // the whole type declaration -- docstring through the closing `};` -- and
    // banned the "this is handled automatically, don't worry about it"
    // contradiction family this unit has now measured TWICE (once here, once
    // in phasesHelpEntries' 결박이 사람의 몫입니다 entry): #55's whole point is
    // that the binding is NOT automatic and IS a human act, so no phrasing
    // anywhere near this type may say otherwise.
    const typeStart = api.indexOf("export type PhaseDiagramProposal = {");
    const typeEnd = api.indexOf("\n};", typeStart);
    pin.match(`typeBounds=${typeStart}/${typeEnd}`, /^typeBounds=\d+\/\d+$/, "expected to find PhaseDiagramProposal's closing `};` (boundary moved?) -- a -1 from indexOf fails this digits-only pattern");
    const wholeDeclaration = api.slice(api.indexOf("/** A READING. An artifact, never a fact"), typeEnd + 3);
    pin.doesNotMatch(
      wholeDeclaration,
      /usually.{0,40}(?:automatic|infers?|handles?|figures? out|connects?)|rarely matters|doesn'?t really matter|대부분(?:의 경우)?.{0,20}(?:자동|알아서)|특별히 신경 쓰지 않아도|신경 쓰지 않아도 됩니다/i,
      "expected no 'this is handled automatically / rarely matters' contradiction anywhere in the PhaseDiagramProposal docstring OR its type body -- #55's whole reconciliation is that the binding is NOT automatic",
    );
  },
);

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Unit #58 -- the 현시도 card becomes 도식 + 시간표 + one short 확인.
//
// AUTHORITY for every pin below: docs/phase2/DECISIONS.md, "2026-08-24 — 사용자
// 결정: 현시도 카드 단순화 — 도식+시간표, 짧은 확인 (#58)". The user, testing the
// live #55 page with a real upload: the machine cross-check that says it
// compared NOTHING is noise ("이건 필요없지 않아? 지워"); the Excel already
// carries 신호 시간 ("이런것도 같이 표로 렌더해줘"); the times must be editable
// ("황색시간 및 시간 같은거가 수정가능하게"); the complex fields go ("수준 계획
// 선언 황색시간 근거 확인 메모 이런 복잡한거 빼고"); and one short confirm
// ("'이 결박을 확인합니다' 같은 긴 버튼 말고 '확인' 버튼 하나만").
// ---------------------------------------------------------------------------

contractTest(
  "unit #58: the 기계 대조 section renders only where the machine compared something, and the SERVER is what says so",
  (pin) => {
    const page = read("app/genesis/phases/page.tsx");
    const api = read("lib/api.ts");

    // DIRECTION ONE -- ABSENT where nothing was compared. The gate is the
    // server's own `compared` flag, and the section is not rendered at all
    // (not collapsed, not emptied): for an 엑셀 업로드 / 직접 입력 review the
    // whole panel could say nothing but "대조할 시트 텍스트도 도면 이미지도
    // 없습니다", which is the sentence the user struck out.
    pin.match(
      page,
      /\{review\.cross_check\?\.compared && \(/,
      "expected the cross-check PANEL itself to be gated on the server's `compared` answer -- absent, not collapsed, for a review the machine never compared",
    );

    // DIRECTION TWO -- PRESENT WITH ITS CONTENT for a vision review. Deleting a
    // comparison that HAPPENED would be hiding evidence, so the panel's body
    // is untouched: the three-tier word, the verbatim message, and the
    // disclaimer all still render inside it.
    pin.match(page, /findingWord\(finding\.severity, t\)/, "expected each finding to keep its own tier word inside the surviving panel");
    pin.match(page, /\{finding\.message\}/, "expected the server's finding message to keep rendering verbatim");
    pin.match(
      page,
      /배정의 정오는 여기서 판단하지 않습니다/,
      "expected the surviving cross-check panel to keep its own disclaimer -- a vision review's section is unchanged by #58",
    );

    // THE ANSWER IS THE SERVER'S. A browser that decided for itself which
    // sections to hide would be editing what the machine said -- and the first
    // medium added to the vocabulary would be hidden or shown by a client-side
    // guess nobody measured.
    pin.match(
      api,
      /export type PhaseDiagramCrossCheck = \{\s*\n\s*checked_at: string;\s*\n\s*findings: PhaseDiagramFinding\[\];\s*\n\s*compared: boolean;\s*\n\};/,
      "expected `compared` to be a field the SERVER sends on the cross-check payload, not a value the page derives",
    );
    // SCOPED to the cross-check panel's own source, because `medium ===
    // "excel_upload"` legitimately appears elsewhere in this file
    // (`noImageSentence`, naming the absent SCAN -- an unrelated claim). Inside
    // the panel that #58 gated, no medium test and no re-spelling of the
    // finding it hides may appear: the answer arrives as `cross_check.compared`
    // and nowhere else.
    const crossCheckPanel = withoutComments(
      sliceBetween(
        page,
        "{review.cross_check?.compared && (",
        "{/* #55 — THE SECONDARY ACT.",
      ),
    );
    pin.match(crossCheckPanel, /\S/, "expected to find the cross-check panel's own source slice (markers moved?)");
    pin.doesNotMatch(
      crossCheckPanel,
      /medium ===|MEDIA_WITHOUT_IMAGE|대조할 시트 텍스트도|phase_diagram_no_text_layer/,
      "expected the cross-check panel never to re-derive 'did the machine compare anything' from the medium, nor to re-spell the finding it hides",
    );
    pin.doesNotMatch(
      withoutComments(page),
      /const compared\s*=|compared:\s*(?:true|false)|compared\s*=\s*review\./,
      "expected the page never to compute, default or override `compared` -- it is the server's answer, read and nothing else",
    );
  },
);

contractTest("unit #58: the timing table renders the SERVER's stored times, and can fabricate none", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const timing = read("components/genesis/PhaseTimingTable.tsx");

  // Fed from the review the server sent, field for field -- the same rule
  // #55b's Φ strip is pinned to, applied to the numbers beside it.
  pin.match(page, /timing=\{review\.sheet\.timing\}/, "expected the table to be drawn from the review's OWN stored timing");
  pin.match(page, /declaredAmberS=\{review\.sheet\.declared_amber_s\}/, "expected the amber cell to show the stored declaration");
  pin.match(page, /amberNote=\{review\.sheet\.amber_declaration_note\}/, "expected the amber's 근거 to come from the server too");

  // THE ONE PATH from a stored value to a cell, pinned as a whole block: null
  // becomes an EMPTY box and everything else becomes its own string. A
  // fabricated time -- a default, a fill, a computed sum -- has to pass through
  // here, and any edit to this function breaks this match.
  const TIMING_TEXT_LINES = [
    "function timingText(value: number | string | null | undefined): string {",
    '  return value == null ? "" : String(value);',
    "}",
  ];
  pin.match(
    timing,
    new RegExp(TIMING_TEXT_LINES.map((line) => literalPattern(line).source).join("\\r?\\n")),
    "expected timingText to be EXACTLY this -- an absent value renders as an empty box, never as a plausible number this app invented",
  );
  // The input's fallback is the SERVER's value, never a literal. Pinned on the
  // exact expression: `drafts[id] ?? timingText(value)` -- a draft while
  // somebody types, the stored value otherwise, and no third source.
  pin.match(
    timing,
    /value=\{drafts\[id\] \?\? timingText\(value\)\}/,
    "expected the editable cell to fall back to the SERVER's own value -- not to a placeholder, a default, or a remembered draft",
  );
  // MUTATION THIS EXISTS FOR (measured 2026-08-25): the table computing a time
  // of its own -- a cycle summed from the greens, a clearance derived from the
  // amber. No arithmetic may touch a timing field anywhere in this file.
  pin.doesNotMatch(
    withoutComments(timing),
    /(green_s|clearance_s|cycle_s|offset_s|min_green_s|max_green_s|declared_amber_s)\s*[-+*/]\s|[-+*/]\s*(green_s|clearance_s|cycle_s|offset_s|min_green_s|max_green_s|declared_amber_s)\b|\.reduce\(/,
    "expected NO arithmetic on any timing value in this file -- the card shows what the server stored, and a number this table computed would be a claim it made",
  );

  // A COLUMN ONLY WHERE THE DATA IS (#58: "columns only for data that exists").
  // Both optional columns are gated on the plan actually carrying the value,
  // and 주기/옵셋/시간대 render only where they are non-null.
  pin.match(timing, /const hasMinGreen = plan\.phases\.some\(\(phase\) => phase\.min_green_s != null\);/, "expected the 최소녹색 column to exist only where a phase carries one");
  pin.match(timing, /const hasMaxGreen = plan\.phases\.some\(\(phase\) => phase\.max_green_s != null\);/, "expected the 최대녹색 column to exist only where a phase carries one");
  pin.match(timing, /\{plan\.cycle_s != null && \(/, "expected 주기 to render only where the plan states one");
  pin.match(timing, /\{plan\.offset_s != null && \(/, "expected 옵셋 to render only where the plan states one");
  pin.match(timing, /\{plan\.time_of_day != null && \(/, "expected 시간대 to render only where the plan states one");

  // An empty timing set is a NAMED absence with its consequence attached, not
  // a blank strip: the mapping is still confirmable, and the signals build is
  // what will refuse.
  pin.match(
    timing,
    /신호 시간이 기록되어 있지 않습니다 — 이 교차로로는 signals 입력을 만들 수 없습니다\./,
    "expected an empty timing set to be named in ko, with what it costs",
  );
});

contractTest("unit #58: an amended time is stamped by the SERVER, and the browser has no field for the stamp", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const api = read("lib/api.ts");
  const timing = read("components/genesis/PhaseTimingTable.tsx");

  // Same discipline as `PhaseDiagramConfirmation`, whose attester the server
  // takes from the session: an amendment says WHO changed a number and WHEN,
  // so the browser is given no field to write either into. (The server side is
  // measured in backend/tests/test_genesis_phase_diagram.py --
  // `test_the_timing_editor_is_the_session_never_the_request_body` sends
  // `edited_by` and asserts a 422 from extra="forbid".)
  const amendCall = sliceBetween(
    api,
    "export async function amendPhaseDiagramTiming(",
    "// ---------------------------------------------------------------------------",
  );
  pin.match(amendCall, /\S/, "expected to find the amend call in the api module (boundary moved?)");
  pin.doesNotMatch(
    amendCall,
    /edited_by|edited_at|amended_by|amended_at/,
    "expected the amendment payload to carry no editor name and no timestamp -- both are the server's, taken from the session and its own clock",
  );
  pin.match(amendCall, /method: "PATCH"/, "expected the amendment to be a PATCH on the review's own timing, not a re-upload");

  // The page stores what came BACK, unmodified -- it never patches its own
  // copy and hopes the server agreed.
  pin.match(
    page,
    /const updated = await amendPhaseDiagramTiming\(review\.review_id, amendment\);\s*\n\s*setReview\(updated\);/,
    "expected the amended review to be re-read from the server's answer, on the very next line -- no local patch, no optimistic merge",
  );
  // And the draft is DROPPED once the request settles, so a value nobody
  // stored can never keep sitting in a cell that looks stored.
  pin.match(
    timing,
    /\} finally \{\s*\n\s*setDrafts\(\(current\) => \{/,
    "expected the typed draft to be cleared in a finally -- on a refusal too, where the number must snap back to what is stored",
  );
});

contractTest("unit #58: the table's 황색시간 IS the declaration, and no second 선언 field may return", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const timing = read("components/genesis/PhaseTimingTable.tsx");

  // #58's doctrine reconciliation, verbatim: "The edited 황색시간 IS the amber
  // declaration (the Daejeon sheets print 황색+전적색 combined and can never
  // supply amber alone — the person typing the number was always the
  // declaration; the separate 선언 field was ceremony once the table itself is
  // editable)."
  pin.match(
    timing,
    /field: "declared_amber_s"/,
    "expected the table's amber cell to amend the declaration itself, through the same stamped path as every other value",
  );
  // REWRITTEN under #60a authority (docs/phase2/DECISIONS.md, "2026-08-25 —
  // 사용자 테스트 1차 피드백 ... (#60a)"): the user's live test ordered this
  // exact sentence OFF the screen ("황색 선언의 두 안내 문장 ... 제거 -- 표가
  // 곧 선언이라는 규칙은 유지하되 산문은 걷어낸다"). The RULE this pin used to
  // guard survives unowned by prose -- it is the `field: "declared_amber_s"`
  // structural pin immediately above, which #60a does not touch -- so the
  // sentence itself is now pinned ABSENT, in all three languages (the #55b/
  // threat-model idiom: a partial ban gets bypassed by translating around it).
  pin.doesNotMatch(
    timing,
    /이 값이 아래 네트워크 결박에 기록되는 선언 황색시간입니다|This is the declared amber the binding below records|この値が下のネットワーク結縛に記録される申告黄色時間です/,
    "expected the #58 explainer sentence to stay OFF the screen in every language -- #60a retired the prose, not the rule it explained",
  );

  // #60a's SECOND retired sentence: the SERVER-composed provenance line
  // (`amber_declaration_note`, "yunha님이 …에 화면에서 선언한 황색시간입니다
  // (이전 값: 없음)."). Unlike the explainer above, this is not a string this
  // file can quote-match -- the sentence is composed server-side per amendment
  // and never appears verbatim in the frontend source -- so the pin is on the
  // JSX SHAPE that used to print it: no branch reads `amberNote` inside a
  // render at all any more.
  pin.doesNotMatch(
    withoutComments(timing),
    /\{amberNote/,
    "expected no JSX branch to render `amberNote` any more -- #60a stops the UI from printing the server's provenance sentence",
  );
  // THE WIRE STAYS (the DECISIONS authority's own words: "the wire field
  // stays, the UI stops rendering it"). `amber_declaration_note` is still a
  // real prop on this component, still typed, and app/genesis/phases/page.tsx
  // still threads `review.sheet.amber_declaration_note` into it (pinned
  // separately, "the timing table renders the SERVER's stored times" above) --
  // #60a deleted a paragraph, not the data path underneath it.
  pin.match(
    timing,
    /amberNote,\s*\n\s*amberDeclaredBy,/,
    "expected `amberNote` to stay a real destructured prop on PhaseTimingTable -- the wire stays even though the render does not",
  );
  pin.match(
    timing,
    /amberNote: string \| null;/,
    "expected `amberNote` to stay typed on PhaseTimingTable's props -- retiring the sentence must not retire the field",
  );

  // ROUND 3 — THE LOST-HISTORY DISCLOSURE, AND ITS REACHABILITY. MEASURED
  // (refuter): `{false && quarantine && (` silenced the disclosure and the
  // whole suite stayed green — every pin on it matched a substring, and a
  // substring cannot tell rendered code from code behind a falsy operand. The
  // same ban the Φ strip and the binding map carry, applied here, plus a
  // structural anchor so the branch cannot be re-gated behind anything else.
  pin.doesNotMatch(
    withoutComments(read("components/genesis/PhaseTimingTable.tsx")),
    /(?<![=!<>]\s{0,3})\b(?:false|0|null|undefined)\s*&&|&&\s*(?:false|0|null|undefined)\b(?!\s{0,3}[=!<>])/,
    "expected no dead-branch short-circuit (false/0/null/undefined && ...) anywhere in the timing table -- that is how a rendered disclosure becomes a pinned string nobody sees",
  );
  pin.match(
    read("components/genesis/PhaseTimingTable.tsx"),
    /\{quarantine && \(/,
    "expected the quarantine disclosure to be gated on the server's own answer and NOTHING else -- `quarantine` must be the FIRST operand of its JSX expression",
  );
  pin.match(
    read("components/genesis/PhaseTimingTable.tsx"),
    /\{t\(quarantine\.message\.ko, quarantine\.message\.en, quarantine\.message\.ja\)\}/,
    "expected the SERVER's own trilingual sentence to render verbatim -- the screen writes the heading, never the account of what a machine did to a file",
  );

  // THE WIRE: the attestation's declared_amber_s comes from the stored table
  // value, verbatim. MUTATION THIS KILLS (measured 2026-08-25): the confirm
  // handler sending a locally-held amber again, or `null`, while the table
  // goes on displaying a number nobody recorded.
  pin.match(
    page,
    /declared_amber_s: review\.sheet\.declared_amber_s,\s*\n\s*amber_declaration_note: review\.sheet\.amber_declaration_note,/,
    "expected the confirmation to take the amber AND its 근거 straight from the stored review -- the table's value is the declaration",
  );

  // THE SECOND FIELD MAY NOT COME BACK. The old 선언 황색시간 / 황색시간 근거 /
  // 확인 메모 inputs and their state are gone from the default surface; a
  // returning `useState` for any of them fails here even under a new label.
  pin.doesNotMatch(
    withoutComments(page),
    /선언 황색시간|declared amber \(s\)|申告黄色時間|황색시간 근거|amber declaration note|확인 메모|confirmation note/,
    "expected the separate 선언 황색시간 / 황색시간 근거 / 확인 메모 inputs to stay off the default surface -- #58 struck them, and the table carries the amber now",
  );
  pin.doesNotMatch(
    withoutComments(page),
    /setAmber\b|setAmberNote\b|<textarea\b/,
    "expected the retired amber/note inputs to leave their STATE behind too -- a re-added box with a new label would otherwise pass the wording ban above",
  );
});

contractTest("unit #58: 수준/계획 resolves ONLY when the sheet offers exactly one", (pin) => {
  const page = read("app/genesis/phases/page.tsx");

  // #54's only-unique-candidate idiom, applied where #58 asks for it. THE
  // MUTATION THIS EXISTS FOR: auto-picking among SEVERAL -- e.g. resolving
  // `plan_choices[0]` whenever the list is non-empty -- which would be this app
  // deciding which 시간분할 a human's confirmation belongs to.
  pin.match(
    page,
    /const onlyPlan = loaded\.sheet\.plan_choices\.length === 1 \? loaded\.sheet\.plan_choices\[0\] : null;/,
    "expected the pair to be resolved ONLY when the sheet offers exactly one -- `length === 1`, never `length > 0`",
  );
  // Both directions on the render side: ONE is shown as a resolved value,
  // SEVERAL brings the picker back, NONE still asks for the two numbers.
  pin.match(
    page,
    /\{review\.sheet\.plan_choices\.length === 1 \? \(/,
    "expected exactly one pair to render as the resolved VALUE, not as a one-option picker",
  );
  pin.match(
    page,
    /\) : review\.sheet\.plan_choices\.length > 1 \? \(/,
    "expected SEVERAL pairs to render the picker -- the only-unique idiom is a two-way rule, and a one-way pin would pass a version that auto-picks the first of five",
  );
  pin.doesNotMatch(
    withoutComments(page),
    /plan_choices\.length > 0 \?/,
    "expected no `length > 0` branch -- that is how 'the only candidate' silently becomes 'the first candidate'",
  );
  // EVERY `plan_choices[0]` read must sit inside a `length === 1` guard. A
  // regex ban alone cannot say that (the guarded reads are legitimate and there
  // are three of them), so it is MEASURED: each occurrence is checked against
  // the source that precedes it, and an unguarded one -- anywhere, present or
  // future -- fails on the count.
  const planSource = withoutComments(page);
  const unguardedFirstPlan = [...planSource.matchAll(/plan_choices\[0\]/g)].filter(
    (match) => !/plan_choices\.length === 1/.test(planSource.slice(Math.max(0, (match.index ?? 0) - 500), match.index)),
  ).length;
  pin.match(
    `unguarded=${unguardedFirstPlan}`,
    /^unguarded=0$/,
    "expected every plan_choices[0] read to be guarded by `plan_choices.length === 1` -- an unguarded first-element read IS auto-picking among several",
  );
  // The gate on 확인 is unchanged: an unresolved pair still cannot confirm.
  pin.match(
    page,
    /nodeId != null &&\s*\n\s*planChosen &&/,
    "expected canSubmit to still require a chosen plan -- #58 resolved the unique case, it did not remove the requirement",
  );
});

contractTest("unit #58: the binding's node map is the draft's own map, and a click on it selects nothing else", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const pickMap = read("components/genesis/DraftNodePickMap.tsx");
  const localMap = read("components/genesis/DraftLocalMap.tsx");

  // REUSE, NOT REIMPLEMENT. The renderer is unit #29's DraftLocalMap and the
  // data is the SAME census fetch GenesisTileMap makes for the job-detail map.
  // A second map implementation would be a second answer to "where is this
  // draft", and the two would drift.
  pin.match(
    pickMap,
    /import DraftLocalMap from "@\/components\/genesis\/DraftLocalMap";/,
    "expected the binding map to REUSE the existing draft renderer rather than draw its own",
  );
  pin.match(
    pickMap,
    /fetchGenesisDraftNetwork\(jobId\)/,
    "expected the binding map to read the SAME draft census the job-detail map reads -- one endpoint, one shape",
  );
  pin.doesNotMatch(
    withoutComments(pickMap),
    /<svg\b|<polyline\b|<circle\b|leaflet/i,
    "expected the wrapper to draw NOTHING itself -- it is a data path and an absence-namer around the map this repo already has",
  );

  // NO DEAD MAP: it renders only when a 생성 작업 is selected, and the empty
  // state is a sentence rather than a basemap with nothing on it.
  pin.match(page, /\{jobId \? \(\s*\n\s*<DraftNodePickMap/, "expected the map to render only once a generation is chosen");
  pin.match(
    page,
    /생성 작업을 고르면 그 초안의 지도가 여기에 나타나고, 노드를 눌러 고를 수 있습니다\./,
    "expected the no-generation state to be a named line, never a blank map box",
  );

  // ONE SELECTION, TWO VIEWS. The map's click lands in the SAME `bindNode` the
  // candidate list's own button calls, and the map is told which node is bound
  // by the SAME `nodeId` the chip and the list highlight from. THE MUTATION
  // THIS KILLS: a second piece of selection state for the map, whose first
  // symptom would be a confirmation recorded against a node the person was no
  // longer looking at.
  pin.match(
    page,
    /const candidate = candidates\.find\(\(entry\) => entry\.node_id === candidateNodeId\);\s*\n\s*if \(candidate\) bindNode\(candidate\);/,
    "expected a map click to route into the SAME bindNode the candidate list uses",
  );
  pin.match(page, /selectedNodeId=\{nodeId\}/, "expected the map to read the page's own nodeId, not a copy of it");
  pin.doesNotMatch(
    withoutComments(page),
    /useState<number \| null>\(null\)[\s\S]{0,40}mapNode|setMapSelected|selectedMapNode/,
    "expected no second selection state for the map -- one selection, two views",
  );
  pin.doesNotMatch(
    withoutComments(pickMap),
    /useState<number/,
    "expected the map wrapper to hold no selection state of its own either -- the page owns it",
  );
  pin.doesNotMatch(
    withoutComments(localMap),
    /useState<number \| null>/,
    "expected the renderer to hold no node-selection state either -- `selectedNodeId` is a prop, the same discipline its link/connector selection already follows",
  );

  // A CLICK IS SELECTION, NEVER CONFIRMATION. Neither map file may carry the
  // confirming act, and the renderer says so where the prop is declared.
  pin.doesNotMatch(
    withoutComments(pickMap),
    /confirmPhaseDiagram|MappingAttestation|attested_by/,
    "expected the map never to reach the confirming call -- the 확인 button in the binding section is the only act that records anything",
  );
  pin.match(
    localMap,
    /onSelectNode\?: \(nodeId: number\) => void;/,
    "expected the node click to be a SELECT callback the caller owns, with no confirm shape anywhere near it",
  );

  // THE DRAFT'S OWN NODES. The map draws census entries, narrowed to what the
  // SERVER offered as candidates -- it invents none and places none.
  pin.match(
    pickMap,
    /const nodes: GenesisDraftNode\[\] = data\.nodes\.filter\(\(node\) => wanted\.has\(node\.id\)\);/,
    "expected every dot to be one of the DRAFT's own nodes, narrowed by the server's candidate ids -- never a synthesised point",
  );
  // A draft with no geometry degrades to a NAMED absence, never a blank box.
  pin.match(pickMap, /function MapAbsence\(/, "expected one shape for every named absence this map can have");
  pin.match(pickMap, /data\.links\.length === 0/, "expected a draft with nothing to draw to say so rather than render an empty canvas");
});

contractTest("#60a: the binding map's background is the theme's own surface token, never a literal light-mode hex", (pin) => {
  const localMap = read("components/genesis/DraftLocalMap.tsx");

  // REPORTED FROM LIVE USE (the user, 2026-08-25, screenshot of the 결박
  // section's node-pick map): a white box on a dark screen. MEASURED root
  // cause: the SVG's own background `<rect>` carried a LITERAL light-mode
  // fill (`#f8fafc`, Tailwind's stock slate-50), left over from before this
  // app's single dark theme locked in (globals.css, "PALETTE LOCK") -- it
  // painted OVER the container div's own `bg-surface` one line above it,
  // opaque, on every generation's map. `withoutComments`: the fix's own
  // comment legitimately quotes the retired hex while explaining why it left.
  pin.doesNotMatch(
    withoutComments(localMap),
    /fill="#f8fafc"|fill="#fff"|fill="#ffffff"|fill="#fafafa"|fill="#fefefe"/i,
    "expected NO literal light-mode hex fill on the background rect -- that is exactly the white-box-on-dark-theme defect the user reported",
  );
  // THE FIX, structurally: the rect carries the SAME `fill-surface` utility
  // the container next to it already resolves through Tailwind v4's
  // `--color-surface` theme token (globals.css) -- one source of truth, so
  // the rect can never drift from the container's own colour again. Anchored
  // on the full element (x/y/width/height through the pointer handlers) so a
  // mutation cannot keep the className while re-adding a `fill=` attribute
  // beside it -- SVG's `fill` PRESENTATION ATTRIBUTE would still win the
  // paint over the `fill-surface` CLASS in that case, and only a structural
  // pin that also bans the literal attribute (above) catches it.
  pin.match(
    localMap,
    /<rect\s*\n\s*x=\{0\}\s*\n\s*y=\{0\}\s*\n\s*width=\{VIEW_W\}\s*\n\s*height=\{VIEW_H\}\s*\n\s*className="fill-surface"\s*\n\s*onPointerDown=\{handleBackgroundPointerDown\}/,
    "expected the background rect to carry the SAME fill-surface token the container around it uses -- measured, not invented, from DraftLocalMap's own container className",
  );
  // The container this rect sits inside is the thing it must MATCH -- pinned
  // on both sides so a future edit cannot drift the two apart again.
  pin.match(
    localMap,
    /className="overflow-hidden rounded-lg border border-slate-200 bg-surface"/,
    "expected the container DraftLocalMap's rect must match to still carry bg-surface -- the token this fix measures itself against",
  );
});

// ---------------------------------------------------------------------------
// Unit #58, SECOND PASS — the pins the refuter's surviving mutations demanded.
// Every one below is written against a mutation that PASSED the first pass.
// ---------------------------------------------------------------------------

contractTest("unit #58: the confirming act has exactly ONE caller, and a map click is not it", (pin) => {
  const page = withoutComments(read("app/genesis/phases/page.tsx"));

  // MEASURED (refuter, 2026-08-25): wiring `submit()` into
  // `selectCandidateNode` — so that clicking a dot on the map RECORDS the
  // attestation — survived the entire suite. Every pin this unit had asked
  // whether the button still existed and whether the map still selected; none
  // asked how many things could CONFIRM. That directly violates the recorded
  // rule (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도 카드
  // 단순화 — 도식+시간표, 짧은 확인 (#58)"): a click is selection, and the
  // confirming act is the one 확인 button.
  //
  // ROUND 3 — THE NAME WAS THE WRONG THING TO COUNT. MEASURED (refuter):
  // the pin below counted mentions of `submit`, so DUPLICATING that callback's
  // BODY under a fresh name — a second literal `confirmPhaseDiagram(...)` call
  // wired to the map — kept the whole suite green while a map click recorded a
  // real attestation. What must be single-sited is the ACT, not the identifier
  // that happens to wrap it today.
  //
  // So both mutating calls this page can make are counted FILE-WIDE, and each
  // is anchored to the one body that may make it. MEASURED 2026-08-25: exactly
  // one call site each.
  const confirmCalls = (page.match(/\bconfirmPhaseDiagram\(/g) ?? []).length;
  pin.match(
    `confirmCalls=${confirmCalls}`,
    /^confirmCalls=1$/,
    "expected exactly ONE confirmPhaseDiagram( call site in the page -- the confirming ACT is what must be single-sited, and a duplicated body under a new name is a second way to attest",
  );
  pin.match(
    page,
    /const updated = await confirmPhaseDiagram\(review\.review_id, \{/,
    "expected the one confirming call to be the one inside `submit` -- the count cannot be held at 1 by moving it somewhere else",
  );
  const amendCalls = (page.match(/\bamendPhaseDiagramTiming\(/g) ?? []).length;
  pin.match(
    `amendCalls=${amendCalls}`,
    /^amendCalls=1$/,
    "expected exactly ONE amendPhaseDiagramTiming( call site -- an amendment is stamped with somebody's name too, so a second caller is a second way to write one",
  );
  pin.match(
    page,
    /const updated = await amendPhaseDiagramTiming\(review\.review_id, amendment\);/,
    "expected the one amending call to be the one inside `amendTiming`",
  );

  // The identifier count is KEPT as a second layer -- it is what catches a
  // second caller of the SAME callback, which the act-count above cannot see.
  // `submit` appears exactly TWICE: its definition, and the 확인 button.
  const submitMentions = (page.match(/\bsubmit\b/g) ?? []).length;
  pin.match(
    `submitMentions=${submitMentions}`,
    /^submitMentions=2$/,
    "expected `submit` to appear exactly twice in the page -- its own definition and the ONE 확인 button that calls it. A third mention is a second way to confirm, whatever it is attached to",
  );
  // ...and the two are the ones we think they are, so the count cannot be kept
  // at two by renaming the definition or moving the call somewhere else.
  pin.match(
    page,
    /const submit = useCallback\(async \(\) => \{/,
    "expected mention one to be the definition of the confirming act",
  );
  pin.match(
    page,
    /onClick=\{submit\}\s*\n\s*disabled=\{!canSubmit\}/,
    "expected mention two to be the 확인 button's own onClick, gated by canSubmit",
  );
  // The selection path must reach `bindNode` and NOTHING that records.
  const selectBody = sliceBetween(
    read("app/genesis/phases/page.tsx"),
    "const selectCandidateNode = useCallback(",
    "const setRow = useCallback(",
  );
  pin.match(selectBody, /\S/, "expected to find selectCandidateNode's body (boundary moved?)");
  pin.doesNotMatch(
    withoutComments(selectBody),
    /submit|confirmPhaseDiagram|amendPhaseDiagramTiming/,
    "expected selecting a node to record NOTHING -- no confirmation, and not even an amendment",
  );
});

contractTest("unit #58: the confirmation's 수준/계획 is the one on screen, unaltered", (pin) => {
  const page = read("app/genesis/phases/page.tsx");

  // MEASURED (refuter, 2026-08-25): `level_no: Number(levelRaw) + 1` survived.
  // With SEVERAL real TOD plans that silently confirms a DIFFERENT plan that
  // also exists — which no server check can catch, because there is no second
  // source for what the screen was showing. (A pair that exists NOWHERE is now
  // refused server-side: `phase_diagram_attestation_unknown_plan`. This pin
  // holds the other half.)
  //
  // So the payload's two numbers are pinned to read the SAME state the display
  // reads, with nothing between the read and the send.
  pin.match(
    page,
    /const \[levelRaw, planRaw\] = planChoice\.split\(":"\);/,
    "expected the payload's pair to be split off the very `planChoice` state the picker and the resolved-value text render",
  );
  pin.match(
    page,
    /level_no: Number\(levelRaw\),\s*\n\s*plan_id: Number\(planRaw\),/,
    "expected level_no/plan_id to be the parsed halves VERBATIM -- no offset, no fallback, no recomputation between the state and the wire",
  );
  pin.doesNotMatch(
    withoutComments(page),
    /Number\(levelRaw\)\s*[-+*/]|Number\(planRaw\)\s*[-+*/]|levelRaw\s*[-+*/]\s*\d|planRaw\s*[-+*/]\s*\d/,
    "expected no arithmetic on the confirmed plan's numbers -- an off-by-one here signs a plan the person never chose, and with several real plans nothing downstream can tell",
  );
  // The same `planChoice` is what the display reads, both ways.
  pin.match(
    page,
    /const \[planLevelRaw, planIdRaw\] = planChoice\.split\(":"\);/,
    "expected the displayed pair to be derived from the SAME state the payload is -- two sources here is how the screen and the record disagree",
  );
});

contractTest("unit #58: no spread may smuggle an attribute past the affordance pins", (pin) => {
  // MEASURED (refuter, 2026-08-25): `{...{onClick: submit}}` on an ALLOWED
  // element passed BOTH affordance pins -- the reading column's and the timing
  // table's -- because every one of them matches literal attribute names, and
  // a spread has none. In scope per the recorded threat-model boundary
  // (docs/phase2/DECISIONS.md, "2026-08-24 — 계약 핀의 위협 모델 경계 (#55b
  // 3패스에서 명시)"): honest `...rest` forwarding is plausible drift from a
  // well-meaning editor, not obfuscation.
  //
  // A JSX ATTRIBUTE spread is banned; an ordinary object-literal spread is not,
  // and telling them apart is done by POSITION rather than by guessing: an
  // object literal is opened after `(`, `=`, `[` or `,`, while an attribute
  // spread sits in attribute position, after whitespace inside a tag.
  // POSITION, widened round 3. The first cut looked at two characters and so
  // misread `return {\n  ...x` as attribute position -- it would have failed an
  // honest refactor. It now looks back past whitespace to the first real
  // character: an object literal is opened after `=`, `(`, `[`, `,`, `:`, `?`,
  // `{`, `=>` (its `>`) or the word `return`; an attribute spread's brace
  // follows a tag name, an attribute name, a quote, or a closing brace.
  //
  // KNOWN, and covered elsewhere: a props object handed to
  // `React.createElement` reads as object position here, because it IS one.
  // That escape is caught by the total-spread COUNT pins below -- a new spread
  // of any kind in either file has to be re-measured -- and by the
  // host-element vocabulary pins, which no createElement call can satisfy.
  const attributeSpreads = (source: string): number =>
    [...source.matchAll(/\{\s*\.\.\./g)].filter((match) => {
      const before = source.slice(0, match.index).replace(/\s+$/, "");
      return !("=([,:?{>".includes(before.slice(-1)) || /\breturn$/.test(before));
    }).length;

  const page = read("app/genesis/phases/page.tsx");
  const readingColumn = withoutComments(
    sliceBetween(page, ") : (\n            <>\n              <Panel", "{/* #55 — THE SECONDARY ACT."),
  );
  pin.match(readingColumn, /\S/, "expected to find the reading column's own source slice (markers moved?)");
  // MEASURED 2026-08-25: the reading column carries NO spread of any kind, so
  // the ban there is flat.
  pin.match(
    `regionSpreads=${(readingColumn.match(/\{\s*\.\.\./g) ?? []).length}`,
    /^regionSpreads=0$/,
    "expected the display-only reading column to carry no spread at all -- an attribute spread there is invisible to every tag and attribute pin above it",
  );

  // MEASURED 2026-08-25: the timing table carries exactly FOUR spreads, all of
  // them ordinary object literals in its draft bookkeeping (`{ ...current }`,
  // `{ ...amendment }`). None is in attribute position, and none may be.
  const timing = withoutComments(read("components/genesis/PhaseTimingTable.tsx"));
  pin.match(
    `timingObjectSpreads=${(timing.match(/\{\s*\.\.\./g) ?? []).length}`,
    /^timingObjectSpreads=4$/,
    "expected the timing table's four object-literal spreads and no more -- a fifth is either new bookkeeping (re-measure) or an attribute spread",
  );
  pin.match(
    `timingAttrSpreads=${attributeSpreads(timing)}`,
    /^timingAttrSpreads=0$/,
    "expected NO spread in attribute position in the timing table -- `{...{onClick: submit}}` on an <input> is a confirmation affordance that no attribute-name pin can see",
  );
  const pickMap = withoutComments(read("components/genesis/DraftNodePickMap.tsx"));
  pin.match(
    `mapAttrSpreads=${attributeSpreads(pickMap)}`,
    /^mapAttrSpreads=0$/,
    "expected NO spread in attribute position on the binding map either -- it forwards props to a renderer, which is exactly where a smuggled handler would sit",
  );
});

contractTest("unit #58: every string this unit added is a real ko/en/ja triple", (pin) => {
  // THE #50-d CLASS AGAIN. `t(ko, en, ja?)` types `ja` as optional, so blanking
  // it passes typecheck and every behavioural pin. MEASURED (refuter,
  // 2026-08-25): blanking the ja argument of the timing table's headers and of
  // the map's absence lines survived the whole suite, and the no-timing
  // sentence was pinned in ko ONLY -- an en reword would not have been caught
  // either. Quote-boundary anchored (the #55b/#57 idiom): only the exact ko AND
  // en AND ja text, in that order, satisfies these.
  const timing = withoutComments(read("components/genesis/PhaseTimingTable.tsx"));
  const map = withoutComments(read("components/genesis/DraftNodePickMap.tsx"));
  const page = withoutComments(read("app/genesis/phases/page.tsx"));

  const TIMING_TRIPLES: [string, string, string][] = [
    ["신호 시간", "Signal timing", "信号時間"],
    ["황색시간(초)", "amber (s)", "黄色時間(秒)"],
    ["선언", "declared", "申告"],
    ["수준", "level", "水準"],
    ["계획", "plan", "計画"],
    ["주기", "cycle", "周期"],
    ["옵셋", "offset", "オフセット"],
    ["시간대", "time of day", "時間帯"],
    ["현시", "phase", "現示"],
    ["녹색(초)", "green (s)", "青(秒)"],
    ["소거(초)", "clearance (s)", "クリアランス(秒)"],
    ["최소녹색", "min green", "最小青"],
    ["최대녹색", "max green", "最大青"],
    // #60a (2026-08-25) RETIRED this triple's own entry -- the explainer
    // sentence it pinned is now pinned ABSENT instead, in the "no second 선언
    // field" test above. Removing the array entry rather than leaving a
    // stale one is deliberate: TIMING_TRIPLES states "every t() this file
    // still calls is a full triple", and a retired call has no business in
    // that inventory any more.
    [
      "아래 시간은 의뢰인 시트의 텍스트에서 읽은 값이라 화면에서 고칠 수 없습니다. 고치려면 엑셀로 내보내 수정한 뒤 다시 올리십시오.",
      "The times below were read out of the client's own sheet, so they cannot be edited here. Export to Excel, correct it there and upload it again.",
      "以下の時間は依頼元シートの本文から読み取った値のため、画面では修正できません。エクセルに書き出して修正のうえ再度アップロードしてください。",
    ],
    [
      "신호 시간이 기록되어 있지 않습니다 — 이 교차로로는 signals 입력을 만들 수 없습니다.",
      "No signal timing is recorded — no signals input can be built from this intersection.",
      "信号時間が記録されていません — この交差点からは signals 入力を作成できません。",
    ],
    [
      "수정 기록 일부 없음",
      "Part of the amendment history is missing",
      "修正記録の一部が欠落",
    ],
  ];
  for (const [ko, en, ja] of TIMING_TRIPLES) {
    pin.match(timing, tripleArgs(ko, en, ja), `expected the timing table's exact ko/en/ja triple for ${ko}`);
  }

  const MAP_TRIPLES: [string, string, string][] = [
    ["초안 네트워크를 불러오는 중입니다…", "Loading the draft network…", "草案ネットワークを読み込み中…"],
    [
      "이 생성 작업의 초안 네트워크를 읽지 못해 지도를 그릴 수 없습니다. 아래 후보 목록으로 선택하십시오.",
      "The draft network for this generation could not be read, so no map is drawn. Pick from the candidate list below.",
      "この生成ジョブの草案ネットワークを読めなかったため、地図は描けません。下の候補一覧から選択してください。",
    ],
    [
      "이 초안에는 그릴 도형이 없습니다 — 링크가 한 개도 없는 생성입니다.",
      "This draft has nothing to draw — the generation produced no links at all.",
      "この草案には描く図形がありません — リンクが一つもない生成です。",
    ],
    [
      "이 초안에서 이 교차로의 후보 노드를 찾지 못했습니다 — 지도에 표시할 점이 없습니다.",
      "No candidate node for this intersection was found in this draft — there is no dot to show.",
      "この草案でこの交差点の候補ノードは見つかりませんでした — 地図に表示する点がありません。",
    ],
  ];
  for (const [ko, en, ja] of MAP_TRIPLES) {
    pin.match(map, tripleArgs(ko, en, ja), `expected the binding map's exact ko/en/ja triple for ${ko}`);
  }
  // The interpolated one takes backticks, so it is anchored on those.
  pin.match(
    map,
    tripleArgs(
      "점 ${nodes.length}개는 이 초안이 가진 노드입니다. 점을 누르면 선택될 뿐이며, 기록되는 것은 아래 확인 단추뿐입니다.",
      "The ${nodes.length} dot(s) are nodes this draft holds. Clicking one only SELECTS it; the only act that records anything is the 확인 button below.",
      "${nodes.length}個の点はこの草案が持つノードです。点を押しても選択されるだけで、記録されるのは下の確認ボタンだけです。",
      "`",
    ),
    "expected the map's dot-count line to be a full triple too -- it is the sentence that says a click records nothing",
  );

  const PAGE_TRIPLES: [string, string, string][] = [
    ["확인", "Confirm", "確認"],
    [
      "엑셀은 접근로↔현시를 담지만, 어느 초안 노드의 어느 링크인지는 담지 못합니다 — 그 연결이 이 절입니다.",
      "The workbook carries approach↔phase, but not WHICH draft node and which link ids — that connection is what this section is.",
      "エクセルは流入路↔現示は持ちますが、どの草案ノードのどのリンクかは持てません — その接続がこの節です。",
    ],
    [
      "생성 작업을 고르면 그 초안의 지도가 여기에 나타나고, 노드를 눌러 고를 수 있습니다.",
      "Pick a generation and its draft's map appears here, with its nodes clickable.",
      "生成ジョブを選ぶとその草案の地図がここに表示され、ノードを押して選べます。",
    ],
    // ROUND 3 RESIDUE: #58 rewrote this label into TWO branches (the resolved
    // value, and the picker) and neither was pinned, so its `ja` was blankable.
    ["수준 / 계획 ID", "level / plan", "水準 / 計画ID"],
  ];
  for (const [ko, en, ja] of PAGE_TRIPLES) {
    pin.match(page, tripleArgs(ko, en, ja), `expected the page's exact ko/en/ja triple for ${ko}`);
  }
  // BOTH branches carry it -- the resolved-value text and the several-plans
  // picker -- so blanking either one's ja fails on the count as well.
  pin.match(
    `levelPlanLabels=${(page.match(/수준 \/ 계획 ID/g) ?? []).length}`,
    /^levelPlanLabels=2$/,
    "expected the 수준/계획 label in BOTH branches (the resolved value and the picker) -- one of them losing its triple is how a language quietly drops out of half the states",
  );

  // ROUND 3 RESIDUE: the class-level blank-argument ban never reached page.tsx.
  // MEASURED 2026-08-25: zero blank `t()` arguments exist there today, so the
  // ban goes file-wide with no carve.
  pin.doesNotMatch(
    page,
    /t\(\s*""|,\s*""\s*\)|,\s*""\s*,/,
    "expected no blank language argument anywhere in the phases page either -- `ja` is optional in the type, which is exactly why every surface that renders it is pinned",
  );

  // AND THE CLASS, not just this inventory: no `t()` call in either new file
  // may carry a BLANK argument in any position.
  for (const [label, source] of [
    ["PhaseTimingTable.tsx", timing],
    ["DraftNodePickMap.tsx", map],
  ] as const) {
    pin.doesNotMatch(
      source,
      /t\(\s*""|,\s*""\s*\)|,\s*""\s*,/,
      `expected no blank language argument anywhere in ${label} -- \`ja\` is optional in the type, which is exactly why it is pinned here`,
    );
  }
});

contractTest("unit #58: the binding map's named absences are reachable, not decorative", (pin) => {
  const map = read("components/genesis/DraftNodePickMap.tsx");

  // THE #55b VACUITY CLASS. The three absence sentences are pinned by
  // substring, and a substring pin cannot tell rendered code from code
  // disabled by a falsy operand — MEASURED (refuter, 2026-08-25): wrapping a
  // branch in `if (false && ...)` kept every one of them green. Same ban the Φ
  // strip carries, same reason, comments stripped because this very comment
  // names the family.
  pin.doesNotMatch(
    withoutComments(map),
    /(?<![=!<>]\s{0,3})\b(?:false|0|null|undefined)\s*&&|&&\s*(?:false|0|null|undefined)\b(?!\s{0,3}[=!<>])/,
    "expected no dead-branch short-circuit (false/0/null/undefined && ...) anywhere in the binding map -- that is how a named-absence pin is left matching a branch that never renders",
  );
  // ...and each branch is anchored STRUCTURALLY on its own guard, so the
  // sentence cannot be kept while the condition that reaches it is rewritten.
  pin.match(map, /if \(!jobId\) return null;/, "expected the no-generation case to be the first guard, returning nothing at all");
  pin.match(map, /if \(loading\) \{/, "expected the loading branch to be reached by the resolved loading flag");
  pin.match(map, /if \(error \|\| !data\) \{/, "expected the failed-read branch to cover BOTH a refusal and a missing census");
  pin.match(map, /if \(data\.links\.length === 0\) \{/, "expected the nothing-to-draw branch to be reached by the census's own emptiness");
  // unit #62 RE-ANCHOR: this line used to be the OUTER condition (`{nodes.length
  // === 0`) -- it is now the inner branch of a three-state ternary, reached
  // only once `candidatesStatus === "fetched"`. Still anchored on the exact
  // `nodes.length === 0 ? t(` shape, so the pin cannot be kept green by a flag
  // that never flips.
  pin.match(
    map,
    /: nodes\.length === 0\s*\n?\s*\? t\(/,
    "expected the no-candidate line (once a fetch has actually run) to be chosen by the measured node count, not by a flag somebody can leave true",
  );
  // unit #62 FINDING 3 RE-ANCHOR: the ladder grew a fourth rung (`failed`),
  // so the outer condition is no longer a single `!== "fetched"` check --
  // each state now tests its own literal, `loading` first.
  pin.match(
    withoutComments(map),
    /\{candidatesStatus === "loading"\s*\n\s*\? t\(/,
    "expected the four-state ladder (loading/failed/idle/fetched) to lead with the loading check, gated structurally ahead of every other branch",
  );
});

// ---------------------------------------------------------------------------
// Unit #62 — the node cap cannot silently hide a node, and the map cannot
// claim a search ran before one did.
// ---------------------------------------------------------------------------

contractTest("unit #62: candidates auto-load on jobId, a stale response cannot win a race, and a failed search cannot claim it found nothing", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const pageNoComments = withoutComments(page);
  const map = withoutComments(read("components/genesis/DraftNodePickMap.tsx"));

  // (1) THE AUTO-LOAD EFFECT. MEASURED DEFECT: the old page fetched
  // candidates ONLY from a button's onClick, so the map rendered its
  // zero-candidate absence sentence for every reader who had not yet clicked
  // it -- a claim about a search that had never run. `node_candidates` never
  // writes anything, so running it the moment a 생성 작업 is picked needs no
  // deliberate click first.
  pin.match(
    pageNoComments,
    /useEffect\(\(\) => \{\s*\n\s*if \(!jobId\) \{[\s\S]{0,700}?\n\s*\}\s*\n\s*void loadCandidates\(\);\s*\n\s*\}, \[jobId, loadCandidates\]\);/,
    "expected an effect that auto-runs loadCandidates when jobId changes, resetting to idle when it is cleared",
  );
  // loadCandidates' own deps still cover selectedId, so switching the
  // intersection re-fetches too (the effect above depends on the callback
  // identity, not on selectedId directly).
  pin.match(
    pageNoComments,
    /const loadCandidates = useCallback\(async \(\) => \{[\s\S]*?\}, \[selectedId, jobId\]\);/,
    "expected loadCandidates to still depend on [selectedId, jobId] -- the auto-load effect rides on THIS identity changing",
  );

  // (2) FINDING 2 (refuter, HIGH) -- THE REQUEST-GENERATION TOKEN. MEASURED:
  // switching reviews/jobs quickly left two fetches in flight with no guard,
  // and the LAST TO RESOLVE won regardless of which was requested most
  // recently -- a slow review A's candidates could land after a fast review
  // B's and leave B's screen showing A's ranking, which a person could bind
  // against the wrong intersection. Anchored on the ref's declaration, its
  // capture at call start, BOTH guarded return points (success and catch --
  // a guard on only one still lets the other overwrite a newer request), the
  // guarded busy-reset, and the jobId-cleared path bumping the token too
  // (nothing else supersedes an abandoned request there to invalidate it).
  pin.match(
    pageNoComments,
    /const candidatesRequestRef = useRef\(0\);/,
    "expected a request-generation ref -- without one, two in-flight candidate fetches let the LAST TO RESOLVE win regardless of which was requested most recently",
  );
  pin.match(
    pageNoComments,
    /const requestId = \+\+candidatesRequestRef\.current;/,
    "expected loadCandidates to capture its OWN request id at the start of every call, before the fetch",
  );
  const guardOccurrences = (pageNoComments.match(/candidatesRequestRef\.current !== requestId\) return;/g) ?? []).length;
  pin.match(
    `guardOccurrences=${guardOccurrences}`,
    /^guardOccurrences=2$/,
    "expected the stale-response guard on BOTH the success path and the catch path -- a guard on only one still lets a stale response overwrite a newer request's screen",
  );
  pin.match(
    pageNoComments,
    /if \(candidatesRequestRef\.current === requestId\) setBusy\(false\);/,
    "expected the busy flag's own reset to be guarded too -- an unguarded reset would clear busy while a NEWER request is still in flight",
  );
  pin.match(
    pageNoComments,
    /candidatesRequestRef\.current \+= 1;\s*\n\s*setCandidatesStatus\("idle"\);/,
    "expected the jobId-cleared branch to bump the token too -- no NEW request follows a cleared jobId to invalidate an abandoned one on its own",
  );

  // (3) FINDING 3 (refuter, MEDIUM) -- FOUR STATES, four sentences, no two
  // shared. Sliced on the ladder's own literal conditions (the state machine
  // itself), so a mutation cannot pass by reusing one sentence across states.
  const loadingBranch = sliceBetween(map, 'candidatesStatus === "loading"', ': candidatesStatus === "failed"');
  const failedBranch = sliceBetween(map, ': candidatesStatus === "failed"', ': candidatesStatus === "idle"');
  const idleBranch = sliceBetween(map, ': candidatesStatus === "idle"', ': nodes.length === 0');
  const fetchedBranch = map.slice(map.indexOf(': nodes.length === 0'));
  for (const [name, branch] of [
    ["loading", loadingBranch],
    ["failed", failedBranch],
    ["idle", idleBranch],
    ["fetched", fetchedBranch],
  ] as const) {
    pin.match(branch, /\S/, `expected to find the ${name} branch's own source (markers moved?)`);
  }

  pin.match(
    failedBranch,
    /탐색이 실패했습니다/,
    "expected the FAILED branch to carry its own 실패 (failed) wording -- a search DID run and did not come back, a different fact from idle's 'not yet'",
  );
  pin.doesNotMatch(
    failedBranch,
    /찾지 못했습니다/,
    "expected the FAILED branch never to reuse the fetched-zero 'not found' sentence -- a failure is not a completed empty search",
  );
  pin.doesNotMatch(
    failedBranch,
    /아직 노드 후보를 찾지 않았습니다/,
    "expected the FAILED branch never to reuse the idle 'not yet searched' sentence -- a search DID run here, and failed",
  );
  pin.doesNotMatch(
    idleBranch,
    /실패했습니다/,
    "expected the IDLE branch never to claim a search failed -- none has run yet",
  );
  pin.doesNotMatch(
    idleBranch,
    /찾지 못했습니다/,
    "expected the IDLE branch to never contain the fetched-zero 'not found' sentence -- no search has run yet to report a result from",
  );
  pin.doesNotMatch(
    loadingBranch,
    /찾지 못했습니다|실패했습니다|아직 노드 후보를 찾지 않았습니다/,
    "expected the LOADING branch to claim neither a failure, an empty result, nor 'not yet' -- it is its own, fourth, distinct state",
  );
  pin.match(
    fetchedBranch,
    /찾지 못했습니다/,
    "expected the FETCHED branch to still carry the 찾지 못했습니다 sentence -- only fetched-and-zero may claim a search came back empty",
  );
  pin.doesNotMatch(
    fetchedBranch,
    /탐색이 실패했습니다|아직 노드 후보를 찾지 않았습니다/,
    "expected the FETCHED branch never to also carry the failed or idle sentence",
  );

  // (4) FINDING 4 (refuter, LOW) -- THE DISCLOSURE GUARD ITSELF. MEASURED:
  // removing `returnedNodes < totalNodes` (rendering a nonsense "8 of 8"
  // every time a fetch succeeds, truncated or not) passed the entire 248-test
  // suite before this pin existed -- gating on candidatesStatus alone is not
  // enough, the ACTUAL comparison must be present.
  pin.match(
    map,
    /candidatesStatus === "fetched" &&\s*\n\s*totalNodes != null &&\s*\n\s*returnedNodes != null &&\s*\n\s*returnedNodes < totalNodes && \(/,
    "expected the disclosure paragraph to be gated on an ACTUAL truncation (returnedNodes < totalNodes), not merely on candidatesStatus -- the weaker guard renders a nonsense 'N of N' line every time a fetch succeeds without cutting anything",
  );

  // (5) THE BACKEND. FINDING 1's fix (refuter, BLOCKER) and the disclosure
  // fields, read the same way the U-1-map test above reads backend source:
  // raw file, no import needed.
  const router = readFileSync(path.join(repoRoot, "backend", "routers", "genesis.py"), "utf-8");
  const phaseDiagramService = readFileSync(
    path.join(repoRoot, "backend", "services", "genesis", "phase_diagram.py"),
    "utf-8",
  );
  pin.match(
    router,
    /"total_nodes": len\(getattr\(draft, "nodes", \[\]\)\),\s*\n\s*"returned_nodes": len\(candidates\),/,
    "expected the candidates route to disclose total_nodes/returned_nodes -- additive fields a real truncation can be named through",
  );
  pin.match(
    phaseDiagramService,
    /discriminates = any\(real_signal for _score, real_signal, _entry in ranked\)/,
    "expected `discriminates` to be driven by the REAL-SIGNAL flag, not by score alone -- FINDING 1: a shared DIGIT between a 교차로명 and an OSM 'Node <osmid>' name produced a positive score on the fallback tier, which flipped this flag on pure noise and dropped the correct node from the top 8",
  );
  // ROUND 5 (refuter, MEDIUM, round 4): tier 2.5's coefficient (0.6) can
  // exceed a WEAK true containment match's score, so a SCORE-ONLY sort
  // listed a wrong-intersection sibling above the genuine name match --
  // cap safety was intact (real_signal still gated `discriminates`), but the
  // visual scan order lied. Anchored on the whole three-part sort key, not
  // just the presence of `real_signal` somewhere, so a revert to
  // score-only sorting (dropping the leading `-int(item[1])` term) fails
  // loud instead of passing because the identifier still appears elsewhere.
  pin.match(
    phaseDiagramService,
    /ranked\.sort\(key=lambda item: \(-int\(item\[1\]\), -item\[0\], item\[2\]\["node_id"\]\)\)/,
    "expected the ranking sort key to be (-real_signal, -score, node_id) -- real name signal must outrank every hint tier in the RETURNED ORDER regardless of either tier's own score formula, current or future",
  );
  pin.match(
    phaseDiagramService,
    /def _name_overlap\(target: str, candidate: str\) -> tuple\[float, str, bool\]:/,
    "expected _name_overlap to report a THIRD value -- whether its score is a REAL name signal at all. Only exact-match and substring-containment are; the character-overlap fallback never is, however high its score",
  );
  // ROUND 3 FINDING A (refuter, HIGH -- CLASS CLOSURE): round 2's digit
  // exclusion only reached the FALLBACK tier -- tier 2 (containment) was
  // left completely unconditional, so an all-digit `intersection_name` like
  // "18" was a literal substring of "node<osmid>" for most osmids (MEASURED:
  // 25 nodes -> 8 returned). Round 3 closed that by running containment ON
  // stripped strings.
  //
  // ROUND 4 (orchestrator-decided semantics, the round-3 refuter's ONE
  // surviving HIGH): running containment ON stripped strings was ITSELF
  // wrong -- it promotes digit SIBLINGS ("원신흥4" vs "원신흥네거리", four
  // distinct "신흥들1/2/4/5" against target "신흥들3") into full name matches.
  // RE-ANCHORED to the three-tier layout this produced: tier 2 (containment)
  // is back on the RAW strings, with the round-3 residue guard kept AHEAD of
  // it rather than folded into the containment test; a NEW tier 2.5
  // (digit-sibling) catches exactly the promoted shape and reports it
  // honestly (real_signal False, its own reason, ranked above fallback).
  pin.match(
    phaseDiagramService,
    /target_stripped = "".join\(ch for ch in target if ch not in digits\)\s*\n\s*candidate_stripped = "".join\(ch for ch in candidate if ch not in digits\)/,
    "expected digits to be stripped into strings ahead of tier 2's residue guard, tier 2.5's stripped-containment check, and tier 3's fallback -- one digit-exclusion mechanism feeding all three, not three separate ones that could drift apart",
  );
  // TIER 2 (round 4): containment on the RAW strings, gated by the
  // non-empty-residue guard -- NOT folded into the containment test itself
  // (that was round 3's mistake). An all-digit target/candidate still blocks
  // this outright regardless of what the raw strings contain.
  pin.match(
    phaseDiagramService,
    /if target_stripped and candidate_stripped and \(target in candidate or candidate in target\):/,
    "expected tier 2 (containment) to run on the RAW, UNSTRIPPED strings -- running it on stripped strings (round 3) promoted digit siblings like \"원신흥4\"/\"원신흥네거리\" into full real_signal=True matches with no real containment behind them",
  );
  // TIER 2.5 (new, round 4): the STRIPPED containment check round 3 used to
  // gate tier 2 itself now gates a SEPARATE, honestly-labelled tier instead.
  pin.match(
    phaseDiagramService,
    /target_stripped\s*\n\s*and candidate_stripped\s*\n\s*and \(target_stripped in candidate_stripped or candidate_stripped in target_stripped\)/,
    "expected a DIGIT-SIBLING tier gated by stripped containment with a non-empty residue on both sides -- reached only once tier 1 and tier 2 (both on raw strings) have failed",
  );
  pin.match(
    phaseDiagramService,
    /0\.6 \* len\(common\) \/ max\(len\(set\(target_stripped\)\), len\(set\(candidate_stripped\)\)\),\s*\n\s*"숫자만 다른 이름입니다 — 같은 계열의 다른 번호일 수 있습니다\.",\s*\n\s*False,/,
    "expected the digit-sibling tier's own reason string and a real_signal of False -- a digit-only difference is information for a human, never a name match, so it must not be able to activate the truncation cap",
  );
  pin.match(
    phaseDiagramService,
    /common = set\(target_stripped\) & set\(candidate_stripped\)/,
    "expected the digit-sibling AND fallback tiers to share the SAME stripped-character-overlap computation -- one digit-exclusion mechanism, not several that could drift apart",
  );
  // THE RECORDED DECISION (refuter asked for one): an all-digit EXACT match
  // still counts as real signal -- both strings committed to their FULL
  // name, so there is no shorter fragment doing the work. Pinned so the
  // decision cannot silently flip along with a future refactor.
  pin.match(
    phaseDiagramService,
    /if target == candidate:\s*\n\s*# DECISION[\s\S]{0,200}?\n\s*return 1\.0, "이름이 정확히 일치합니다\.", True/,
    "expected the exact-match tier's all-digit decision to stay recorded in a comment directly above the return it documents",
  );
  pin.match(
    phaseDiagramService,
    /if not discriminates or len\(entries\) <= limit:\s*\n\s*return entries/,
    "expected a non-discriminating ranking to return EVERY node, not just the top `limit`",
  );
  pin.doesNotMatch(
    withoutComments(phaseDiagramService),
    /return \[entry for _score, entry in ranked\[:limit\]\]/,
    "expected the OLD unconditional ranked[:limit] truncation to be gone -- that line was the silent cap this unit measured (25-node draft, 17 unofferable)",
  );

  // (6) ROUND 3 FINDING B (refuter, HIGH) -- THE BUSY-FREEZE CLASS. MEASURED:
  // the jobId-cleared branch bumped candidatesRequestRef but never reset
  // `busy`, so the orphaned fetch's OWN guarded `setBusy(false)` in
  // loadCandidates' `finally` was left permanently skipped (token mismatch,
  // by design) -- `busy` never cleared, and canSubmit/확인 froze silently.
  // CLASS CLOSURE, not a respelled fix: every `candidatesRequestRef.current
  // += 1` OUTSIDE loadCandidates (the `++candidatesRequestRef.current`
  // prefix form loadCandidates itself uses is a DIFFERENT literal pattern
  // and is not counted here) must be immediately followed, in the SAME
  // block, by `setBusy(false)`. Pinned as a COUNT EQUALITY (the act-count
  // idiom unit #58 uses for confirmPhaseDiagram/amendPhaseDiagramTiming) so
  // a future unpaired bump site fails on the count rather than needing its
  // own new pin.
  const bumpSites = (pageNoComments.match(/candidatesRequestRef\.current \+= 1;/g) ?? []).length;
  const pairedBumpSites = [...pageNoComments.matchAll(/candidatesRequestRef\.current \+= 1;[\s\S]{0,300}?setBusy\(false\);/g)].length;
  pin.match(
    `candidatesRefBumpsOutsideLoadCandidates=${bumpSites} pairedWithBusyFalse=${pairedBumpSites}`,
    /^candidatesRefBumpsOutsideLoadCandidates=([1-9]\d*) pairedWithBusyFalse=\1$/,
    "expected EVERY candidatesRequestRef bump site outside loadCandidates to be immediately paired with setBusy(false) in the same block -- an unpaired bump orphans a fetch whose own guarded busy-reset will never fire, freezing canSubmit/확인 silently",
  );

  // (7) ROUND 3 FINDING C (refuter, HIGH) -- THE CATCH BLOCK, STRUCTURALLY.
  // MEASURED (refuter): reverting the catch block to `setCandidatesStatus(
  // "idle")` passed the entire 248-test suite, because the ladder pins in
  // section (3) above only guard DraftNodePickMap's OWN rendering of each
  // sentence -- nothing anchored page.tsx's error path actually REACHING
  // "failed". Sliced to the catch block's own body (via loadCandidates'
  // established boundary), not just a file-wide string search, so a mutation
  // cannot pass by leaving "failed" somewhere else in the file.
  const loadCandidatesBody = sliceBetween(
    pageNoComments,
    "const loadCandidates = useCallback(async () => {",
    "}, [selectedId, jobId]);",
  );
  pin.match(loadCandidatesBody, /\S/, "expected to find loadCandidates' own body (boundary moved?)");
  const catchBody = sliceBetween(loadCandidatesBody, "catch (caught) {", "} finally {");
  pin.match(catchBody, /\S/, "expected to find the catch block's own body (boundary moved?)");
  pin.match(
    catchBody,
    /setCandidatesStatus\("failed"\);/,
    'expected the catch block to set candidatesStatus to "failed" -- a search DID run here and did not come back',
  );
  pin.doesNotMatch(
    catchBody,
    /setCandidatesStatus\("idle"\)/,
    'expected the catch block never to fall back to "idle" -- that claims no search has run, which is false once one has failed',
  );
});

// #55's new component -- components/genesis/PhaseDiagramSketch.tsx. The Φ
// strip draws the STORED reading's own parsed rows, and nothing else. These
// pins are the three rules the component's own header comment names.
// ---------------------------------------------------------------------------

contractTest("the Φ strip is fed the review's own parsed rows, and nothing else", (pin) => {
  const page = read("app/genesis/phases/page.tsx");

  pin.match(
    page,
    /phases=\{review\.proposal\.phases\}/,
    "expected the strip to be drawn from the review's OWN parsed phases, not a separately-fetched or synthesised list",
  );
  pin.match(
    page,
    /approaches=\{review\.proposal\.approaches\}/,
    "expected the strip's slot order to come from the review's OWN parsed approaches",
  );
  pin.match(
    page,
    /notes=\{review\.proposal\.notes\}/,
    "expected the intersection notes under the strip to come from the review's OWN parsed notes",
  );

  // DEFECT MEASURED 2026-08-24 (refuter, #55b round 2): the three pins above
  // only verify the RENDER CALL SITE -- they say nothing about where `review`
  // itself came from. The refuter rewrote `loadReview` to overwrite every
  // phase's `movements` with a fabricated `[{approach_label:"MUTATED", ...}]`
  // array before calling `setReview`, and every pin above stayed green
  // because `review.proposal.phases` was still the prop wired to the strip --
  // it just no longer held what the server sent. FIX: pin the STORE itself.
  const loadReviewBody = sliceBetween(
    page,
    "const loadReview = useCallback(async (reviewId: string) => {",
    "}, []);",
  );
  pin.match(loadReviewBody, /\S/, "expected to find loadReview's body (boundary moved?)");
  pin.match(
    loadReviewBody,
    /const loaded = await fetchPhaseDiagram\(reviewId\);\s*\n\s*setReview\(loaded\);/,
    "expected setReview to receive `loaded` UNMODIFIED, on the very next line after the fetch -- no transform, spread-and-patch, or rebuild may sit between the fetch and the store",
  );
  pin.doesNotMatch(
    withoutComments(loadReviewBody),
    /\.movements\s*=|\.phases\s*=|movements\s*:\s*\[|phases\s*:\s*\[/,
    "expected loadReview never to reassign or fabricate a .movements/.phases array -- the stored review must be exactly the server's own payload, so the Φ strip can never be fed data no fetch produced",
  );

  // ROUND 3 -- CLOSING THE CLASS: the loadReview-scoped ban above only covers
  // ONE of THREE `setReview(...)` call sites in this file (`setReview(loaded)`
  // in loadReview, `setReview(null)` in its catch, `setReview(updated)` in
  // `submit`'s confirm handler). Round 2's refuter fabricated the phases
  // before the OTHER call site -- `submit`, not `loadReview` -- and the
  // scoped pin above could not see it. The page NEVER constructs
  // phases/movements anywhere -- it only displays what the server already
  // returned (from `fetchPhaseDiagram` or from `confirmPhaseDiagram`) -- so
  // the ban belongs on the WHOLE FILE, not one function, and it covers every
  // present AND future `setReview` call site by construction. MEASURED
  // 2026-08-24: none of these shapes -- including the literal field name
  // `approach_label:`, which only a hand-built movement object would ever
  // write -- appear anywhere in page.tsx today.
  pin.doesNotMatch(
    withoutComments(page),
    /\.movements\s*=|\.phases\s*=|movements\s*:\s*\[|phases\s*:\s*\[|approach_label:/,
    "expected the page to never CONSTRUCT a phases/movements array anywhere -- it only ever displays server data, from ANY setReview call site, present or future",
  );
});

contractTest("`unknown` can never reach an arrow, by type -- not by convention", (pin) => {
  const sketch = read("components/genesis/PhaseDiagramSketch.tsx");

  // THE DISCRIMINATING PATH. `VehicleTurn` is the closed set of turns that
  // HAVE a direction -- `unknown` and `pedestrian` are deliberately absent
  // from it, so a heading table typed over it structurally cannot have an
  // entry for either. Adding a sixth turn to the vocabulary without updating
  // this set fails to COMPILE (verified separately by `npm run typecheck`);
  // this pin guards the runtime routing on top of that.
  pin.match(
    sketch,
    /type VehicleTurn = "through" \| "left" \| "right" \| "u_turn";/,
    "expected the four turns WITH a direction to be a closed set that excludes unknown and pedestrian",
  );
  pin.match(
    sketch,
    /function movementGlyph\(turn: SketchTurn\): Glyph \{/,
    "expected one function to be the sole place a turn is classified into a glyph",
  );
  pin.match(
    sketch,
    /if \(turn === "unknown"\) return \{ kind: "chip" \};/,
    "expected unknown to be routed to the chip arm before any arrow logic runs",
  );
  pin.match(
    sketch,
    /const TURN_ROTATION_DEG: Record<VehicleTurn, number> = \{/,
    "expected the arrow heading table to be typed over VehicleTurn only -- there is no heading for unknown to acquire",
  );
  // The rendering side must actually respect the discriminated union: an arrow
  // is drawn only in the "arrow" arm, never as a fallback default.
  pin.match(
    sketch,
    /glyph\.kind === "arrow" \? \(/,
    "expected ArrowMark to be reached only through the explicit 'arrow' branch of the glyph union",
  );

  // VACUITY MEASURED 2026-08-24 (refuter, #55b round 2): the type guarantee
  // above is real for the ArrowMark PATH, but says nothing about a hand-rolled
  // JSX shape planted anywhere else in the file -- the refuter added an inline
  // `<svg><polygon .../></svg>` arrowhead beside the 미상 chip text (~line
  // 333-340) and every pin above stayed green, because none of them look past
  // ArrowMark/CrossingMark's own bodies at the REST of the file. FIX DIRECTION
  // (the coordinator's, not a chip-specific patch): the file may carry
  // exactly ONE arrowhead-drawing <polygon>, and it must live inside
  // ArrowMark's own body -- proven by COUNTING, so a second one anywhere
  // (a chip, a slot label, the notes block, or any future JSX) fails here
  // regardless of which element it hides inside.
  const totalPolygons = (sketch.match(/<polygon\b/g) ?? []).length;
  const arrowMarkBody = sliceBetween(sketch, "function ArrowMark(", "function CrossingMark(");
  pin.match(arrowMarkBody, /\S/, "expected to find ArrowMark's body (function boundary moved?)");
  const arrowMarkPolygons = (arrowMarkBody.match(/<polygon\b/g) ?? []).length;
  pin.match(
    `polygons=${totalPolygons}/inArrowMark=${arrowMarkPolygons}`,
    /^polygons=1\/inArrowMark=1$/,
    "expected the file's ONLY <polygon> (an arrowhead) to be the one inside ArrowMark -- any second <polygon> anywhere else in the component (a chip, a slot label, the notes block) is an ungoverned second arrowhead emitter",
  );
  // The other SVG arrowhead mechanism this file could reach for instead of a
  // <polygon> -- a <marker>/markerEnd -- is banned outright, file-wide: this
  // component draws its one legitimate arrowhead by hand in ArrowMark and has
  // no legitimate use for the marker mechanism at all.
  pin.doesNotMatch(
    withoutComments(sketch),
    /<marker\b|markerEnd\s*=|markerStart\s*=|markerWidth\s*=|markerHeight\s*=/,
    "expected no SVG <marker>/markerEnd arrow mechanism anywhere in the file -- ArrowMark's own <polygon> head is the only arrow-drawing mechanism this component may use",
  );

  // ROUND 3 -- CLOSING THE CLASS, not the two reproductions. Round 2's
  // refuter redrew the SAME lie with different markup and the polygon-count
  // pin above stayed green: a `<path d="M0,0 L10,5 L0,10 Z">` triangle (a
  // NEW use of an ALREADY-legitimate tag -- `path` is ArrowMark's own stroke)
  // and a `<use href="#...">` reusing ArrowMark's polygon by reference. Per
  // the recorded threat-model boundary (docs/phase2/DECISIONS.md, "2026-08-24
  // — 계약 핀의 위협 모델 경계 (#55b 3패스에서 명시)"): a partial ban
  // (`<polygon>` only) is always evadable by a different primitive: the fix
  // is a CLOSED VOCABULARY, set-equality, the #50-d idiom.
  //
  // MEASURED 2026-08-24: this pure-display component's entire lowercase
  // (host-element) tag vocabulary is exactly these 13 -- svg/g/title (SVG
  // structure), line/polygon/path (SVG marks -- `path` is ArrowMark's OWN
  // connecting stroke, not an arrowhead), text (leg numerals), and
  // div/p/span/ul/ol/li (chrome). Anchored BOTH directions: a tag entering
  // (`use`, `marker`, `circle`, `image`, `foreignObject`, `a`, `button`,
  // `input`, `canvas`, `iframe`, ...) fails, and a tag leaving (e.g. losing
  // <title> a11y support) fails too -- the same "files=" idiom
  // MICROTASK_DEFERRAL_FILES uses above, anchored so a superset does not pass.
  const tagSet = new Set<string>();
  for (const m of sketch.matchAll(/<([a-z][a-zA-Z0-9]*)/g)) tagSet.add(m[1]);
  const actualTags = [...tagSet].sort().join(",");
  pin.match(
    `tags=${actualTags}`,
    /^tags=div,g,li,line,ol,p,path,polygon,span,svg,text,title,ul$/,
    "expected the Φ strip's entire host-element vocabulary to equal this measured set exactly -- a NEW tag entering (use/marker/circle/image/foreignObject/a/button/...) fails here even where the polygon-count pin above cannot see it, and a tag disappearing fails too",
  );

  // `path` was ALREADY legitimate (ArrowMark's stroke) before this pin
  // existed, so the tag-SET pin above cannot catch a SECOND <path> reusing
  // that already-open door -- exactly the refuter's triangle. Closed the same
  // way as <polygon>: total count in the file must equal the count inside
  // ArrowMark's own body.
  const totalPaths = (sketch.match(/<path\b/g) ?? []).length;
  const arrowMarkPaths = (arrowMarkBody.match(/<path\b/g) ?? []).length;
  pin.match(
    `paths=${totalPaths}/inArrowMark=${arrowMarkPaths}`,
    /^paths=1\/inArrowMark=1$/,
    "expected the file's ONLY <path> (ArrowMark's own connecting stroke) to be the one inside ArrowMark -- a second <path> anywhere else is an ungoverned shape, triangle-drawing or otherwise, that the polygon-only pin cannot see",
  );

  // Arrow-shaped UNICODE glyphs in JSX text -- a third, markup-free way to
  // draw a direction beside 미상 that no tag-vocabulary pin can see at all.
  // MEASURED 2026-08-24: zero legitimate uses exist anywhere in this file (no
  // arrow emoji/dingbat currently appears, directional turns are drawn as
  // <path>/<polygon> geometry and named in prose, never as a glyph) -- so the
  // ban is flat, not carved.
  pin.doesNotMatch(
    sketch,
    /[←-⇿➔-➿⬀-⯿▲-▾◀-◅▶-▻]/u,
    "expected no arrow-shaped unicode glyph (→ ▶ ▸ ➤ etc.) anywhere in the file -- a fourth way to draw a direction beside a movement this component must never give one",
  );

  // SELF-EVASION FOUND 2026-08-24, closed the same day: a CSS-only
  // "border-triangle" (`<div style={{width:0, height:0, borderLeft:"8px
  // solid currentColor", ...}} />`) draws a visible arrowhead using ONLY the
  // already-legitimate `div` tag -- it is invisible to the tag-set pin (no
  // new tag), the path/polygon-count pins (neither element), and the
  // unicode-glyph ban (no glyph). This is a well-known, ordinary CSS idiom,
  // not obscure obfuscation -- plausible honest drift, per the recorded
  // threat-model boundary -- so it gets a pin rather than a shrug. MEASURED:
  // this pure-display component uses zero inline `style=` anywhere (every
  // visual property is a Tailwind class or an SVG presentation attribute), so
  // the ban is flat. It also forecloses the sibling idioms (conic-gradient
  // arrows, clip-path polygons, box-shadow arrows) that reach for the exact
  // same door.
  pin.doesNotMatch(
    sketch,
    /style\s*=\s*\{/,
    "expected no inline `style=` prop anywhere in the file -- a CSS-only shape (a border-triangle arrowhead, a clip-path polygon, ...) drawn through it would be invisible to every tag- and glyph-based pin above",
  );
});

contractTest("보행 draws a headless crossing bar, never a vehicle arrow", (pin) => {
  const sketch = read("components/genesis/PhaseDiagramSketch.tsx");

  pin.match(
    sketch,
    /if \(turn === "pedestrian"\) return \{ kind: "crossing" \};/,
    "expected pedestrian to be routed to the crossing arm, never to the arrow arm",
  );
  pin.match(
    sketch,
    /glyph\.kind === "crossing" \? \(/,
    "expected CrossingMark to be reached only through the explicit 'crossing' branch of the glyph union",
  );
  // CrossingMark itself must carry no arrowhead: ArrowMark's head is a
  // <polygon>, and a crossing mark that grew one would be misreadable as a
  // fifth arrow beside the four real ones.
  const crossingMarkBody = sliceBetween(sketch, "function CrossingMark(", "function PhaseCell(");
  pin.match(crossingMarkBody, /\S/, "expected to find CrossingMark's body (function boundary moved?)");
  pin.doesNotMatch(
    crossingMarkBody,
    /<polygon/,
    "expected the crossing mark to carry no arrowhead polygon -- it must not be misreadable as a fifth arrow",
  );
  pin.match(
    crossingMarkBody,
    /<line\b/,
    "expected the crossing mark to be drawn as bars (<line>), not a directional stroke with a head",
  );
});

contractTest("the Φ strip's placement-is-not-a-bearing caption renders unconditionally with it", (pin) => {
  const sketch = read("components/genesis/PhaseDiagramSketch.tsx");

  // MANDATORY per the component's own header comment (rule 1): the v1
  // template carries no 방위 column, so a leg's position on this drawing is a
  // SCREEN LAYOUT, never a claim about which way it actually faces. Anchored
  // directly after the strip's opening <div> so a conditional wrapper inserted
  // between them (hiding the caption in some branch) breaks this pin.
  pin.match(
    sketch,
    /<div>\s*\{\/\* MANDATORY, and it is not decoration/,
    "expected the caption to render unconditionally, immediately inside the strip's root element -- not gated behind a branch",
  );
  // VACUITY MEASURED 2026-08-24 (refuter, #55b round 2): the anchor above only
  // reads up to where the comment STARTS -- it never reads past the comment's
  // own close. Wrapping the caption in `{false && ( ... )}` AFTER the comment
  // (leaving `<div>{/* MANDATORY... */}` itself untouched) kept that pin green
  // AND kept the ko literal below matching, because a substring pin cannot
  // tell rendered code from dead code. THE OTHER HALF of the structural
  // anchor: the comment's closing `*/}` must be immediately followed (only
  // whitespace between) by the caption's OWN `<p>` -- no `{anything && (`
  // wrapper of any kind may sit between them. Together with the anchor above,
  // this pins the caption as the div's literal first and only-wrapped-by-
  // nothing child, which is a structural claim no single literal (`false`,
  // `0`, or any other falsy value) can dodge.
  pin.match(
    sketch,
    /order\. Saying so is the difference between a diagram and a claim\. \*\/\}\s*<p className="text-\[14px\] leading-relaxed text-slate-600">/,
    "expected the caption comment's close to be immediately followed by its own <p> -- no conditional wrapper (of any kind, not just `false &&`) may sit between the comment and the element it documents",
  );
  pin.match(
    sketch,
    /배치는 화면 배열이며 실제 방위가 아닙니다/,
    "expected the caption to state, in ko, that placement is a screen layout and not a real bearing",
  );

  // THE CLASS, not the two instances the refuter found: a substring pin
  // cannot distinguish rendered code from code disabled by a leading/trailing
  // falsy operand in an `&&` chain. Banned file-wide (comments stripped, since
  // this very doc comment legitimately NAMES the family while explaining it) --
  // `false`, `0`, `null`, `undefined`, on EITHER side of `&&`. Verified against
  // the file's own live code first: `drawn.length === 0 && chips.length === 0`
  // and similar numeric-equality checks do NOT trip this, because the
  // lookaround requires the `0`/`false`/etc. to be a BARE operand, not the
  // right-hand side of a comparison operator.
  pin.doesNotMatch(
    withoutComments(sketch),
    /(?<![=!<>]\s{0,3})\b(?:false|0|null|undefined)\s*&&|&&\s*(?:false|0|null|undefined)\b(?!\s{0,3}[=!<>])/,
    "expected no dead-branch short-circuit (false/0/null/undefined && ..., or ... && false/0/null/undefined) anywhere in the Φ strip -- that is how a live pin can be left matching a branch that never renders",
  );

  // ROUND 3 -- CLOSING THE CLASS: round 2's refuter drew the SAME "hide the
  // caption" lie a second way -- `style={{display:"none"}}` on the <p> --
  // which the JS-level `&&`-short-circuit ban above cannot see (there is no
  // `&&` at all). Per the recorded threat-model boundary
  // (docs/phase2/DECISIONS.md, "2026-08-24 — 계약 핀의 위협 모델 경계 (#55b
  // 3패스에서 명시)"): "가시성 억제 관용구 전면 금지" -- a pure-display component
  // has no legitimate reason to hide ANY of its own content, by ANY
  // mechanism, so every visibility-suppression idiom this app's own
  // conventions and CSS ecosystem offer is banned outright rather than
  // chased one literal at a time. MEASURED 2026-08-24: none of these strings
  // appear anywhere in the file today, so the ban is flat.
  pin.doesNotMatch(
    withoutComments(sketch),
    /display:\s*["']?none|visibility:\s*["']?hidden|opacity-0|\bsr-only\b|aria-hidden|\bhidden\b|\binvisible\b|visibility-collapse|\bcollapse\b/i,
    "expected no visibility-suppression idiom anywhere in the Φ strip (display:none, visibility:hidden, opacity-0, sr-only, aria-hidden, a bare `hidden` attribute/class, Tailwind's `invisible`/`collapse`) -- this pure-display component has no legitimate reason to hide any of its own content",
  );
});

contractTest("the 현시도 screen exports labelled drafts, one review and all of them", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const api = read("lib/api.ts");

  pin.match(page, /phaseDiagramExportUrl\(review\.review_id\)/, "expected a per-review export control");
  pin.match(page, /phaseDiagramExportsUrl\(\)/, "expected a bulk export control");
  pin.match(page, /엑셀로 내보내기 \(미확인\)/, "expected the per-review export to be labelled 미확인 on the button itself");
  pin.match(page, /전체 내보내기 \(미확인 초안\)/, "expected the bulk export to say the same");
  pin.match(api, /export function phaseDiagramExportUrl\(/, "expected the export URLs to live beside the image URL");
});

contractTest("a review with no 대전 TOD table is ASKED for 수준/계획 ID, never given a default", (pin) => {
  const page = read("app/genesis/phases/page.tsx");

  // REWRITTEN 2026-08-25 for unit #58. AUTHORITY: docs/phase2/DECISIONS.md,
  // "2026-08-24 — 사용자 결정: 현시도 카드 단순화 — 도식+시간표, 짧은 확인 (#58)"
  // -- "수준/계획 auto-resolve when unique (#54's only-unique idiom); a picker
  // appears only when several exist". The pin this replaces asserted
  // `plan_choices.length > 0 ? (` , which cannot tell ONE pair from SEVERAL
  // and so would have passed a version that silently picked the first of five.
  // The three-way is pinned in full by the test below
  // ("unit #58: 수준/계획 resolves ONLY when the sheet offers exactly one");
  // what stays here is this test's own claim -- that NONE still ASKS.
  pin.match(
    page,
    /review\.sheet\.plan_choices\.length > 1 \? \(/,
    "expected the select to be offered only when the sheet parsed SEVERAL (수준, ID) pairs -- one pair is resolved, none is asked for",
  );
  pin.match(page, /planChosen/, "expected an explicit check that both numbers were supplied");
  pin.match(
    page,
    /Number\.isFinite\(Number\(planLevelRaw\)\)/,
    "expected a pair of empty boxes NOT to count as a chosen plan -- planChoice !== \"\" accepted the ':' they make",
  );
  pin.match(
    page,
    /review\.sheet\.phase_ids\.length > 0 \? \(/,
    "expected a review with no known phase list to let the human type the phase id",
  );
});

contractTest("the network diagram links a selected node to its 현시도 without claiming a binding", (pin) => {
  const networkPage = read("app/network/page.tsx");
  const phasesPage = read("app/genesis/phases/page.tsx");

  pin.match(networkPage, /href=\{`\/genesis\/phases\?\$\{query\.toString\(\)\}`\}/, "expected the deep link to the phases screen");
  pin.match(networkPage, /query\.set\("name", nodeName\)/, "expected the node's NAME to travel with the link");
  pin.match(networkPage, /현시도 입력 →/, "expected the action to be named in ko, as an action");
  pin.match(
    networkPage,
    /selectedNodeIds\.map\(\(nodeId\) => \{/,
    "expected one entry link per selected node, in the existing selection UI",
  );

  // The two id spaces are NOT the same: /network draws the active Vissim
  // dataset, and the confirm form binds a node from a generation's draft. The
  // phases page must therefore treat ?node= as a breadcrumb, never as a match.
  pin.match(
    phasesPage,
    /rows\.find\(\(row\) => \(row\.intersection_name \?\? ""\)\.trim\(\) === wanted\)/,
    "expected the arriving link to be resolved by NAME",
  );
  pin.doesNotMatch(
    withoutComments(phasesPage),
    /node_id === Number\(nodeParam\)/,
    "expected a Vissim node id never to be matched against a draft attestation's node_id -- different id spaces",
  );
  // #55 demoted the confirm form into the collapsed 네트워크 결박 section
  // (docs/phase2/DECISIONS.md, "2026-08-24 -- 사용자 결정: 현시도 화면은 엑셀
  // 업로드로 통합 ... (#55)"); the old sentence's referent moved, so the sentence
  // pointing at it had to move too. It still names WHERE the binding is decided
  // -- the invariant above (never matched by number) is what keeps it honest.
  pin.match(
    phasesPage,
    /어느 노드에 결박되는지는 카드의 네트워크 결박 절에서 정해집니다/,
    "expected the screen to say the binding is still decided in the card's 네트워크 결박 section, by name",
  );
});

contractTest("an upload with no 신호시간 says so before the staging button does", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const api = read("lib/api.ts");
  const ui = read("components/genesis/ui.tsx");

  // The timing is what carries an uploaded 현시도 into a generation. A review
  // without it is still confirmable -- and the screen has to say which it is at
  // upload time, not at the staging button two panels away.
  pin.match(
    page,
    /uploadResult\.created\s*\n?\s*\.filter\(\(row\) => row\.plans\.length === 0\)/,
    "expected the upload result to name every intersection that arrived with no signal timing",
  );
  pin.match(
    page,
    /신호시간 없음 — 확인은 되지만 신호 입력은 만들 수 없습니다/,
    "expected that state to be stated in ko, as a consequence rather than a warning",
  );
  pin.match(api, /plans: \{ level_no: number; plan_id: number \}\[\]/, "expected the created rows to carry their timed plans");

  // The refusal a staging attempt gets is about the TIMING, not about the
  // confirmation -- and it has a headline in all three languages.
  pin.match(
    ui,
    /case "phase_diagram_no_sheet_text":/,
    "expected the timing refusal to have its own translated headline",
  );
  pin.match(
    ui,
    /확인은 되었지만 신호시간이 없어/,
    "expected that headline to say the confirmation stands and the timing is what is missing",
  );
});

contractTest("unit #52's doctrine lives at /docs, not on the phases screen", (pin) => {
  const help = read("lib/help-content.tsx");
  const page = read("app/genesis/phases/page.tsx");

  pin.match(help, /입력하는 세 가지 방법/, "expected the three input paths to be explained in the doctrine module");
  // #55 (docs/phase2/DECISIONS.md, "2026-08-24 -- 사용자 결정: 현시도 화면은 엑셀
  // 업로드로 통합 ... (#55)") retired the old "all three produce a PROPOSAL"
  // claim: an upload is now the uploader's own RECORD, not something awaiting a
  // click. BOTH directions matter here -- the new claim must be present, and
  // the retired one must not have quietly come back.
  pin.match(
    help,
    /세 가지 모두 결과는 올린 사람의 기록이며/,
    "expected the doctrine to state that all three produce THAT PERSON'S OWN RECORD, not a proposal",
  );
  pin.doesNotMatch(
    help,
    /세 가지 모두 결과는 '제안'이며/,
    "expected the retired #52-era 'all three produce a proposal' claim to be GONE, not left beside its #55 replacement",
  );
  pin.match(help, /가짜 이미지를 넣지 않고/, "expected the no-fake-image rule to be stated at /docs");

  // #55's two new doctrine claims: binding is the human act that survived, and
  // the drawn Φ strip is a layout, never a claim about real geography.
  pin.match(
    help,
    /export function phasesHelpEntries\(t: Translate\): HelpEntry\[\] \{[\s\S]*?결박이 사람의 몫입니다/,
    "expected a doctrine entry stating that binding the draft node/links stays a human act, inside phasesHelpEntries",
  );
  pin.match(
    help,
    /signals 입력을 만들 때 사람이 각 카드의 네트워크 결박 절에서 정하며/,
    "expected the binding entry to name WHEN it happens -- at signals-build time, in each card's own section",
  );
  pin.match(
    help,
    /export function phasesHelpEntries\(t: Translate\): HelpEntry\[\] \{[\s\S]*?그려진 도식은 배치이지 실제 지리가 아닙니다/,
    "expected a doctrine entry stating the drawn strip is a layout, not real geography, inside phasesHelpEntries",
  );
  pin.match(
    help,
    /실제 방위를 주장하지 않으며/,
    "expected the layout entry to disclaim any real-bearing claim, the same rule PhaseDiagramSketch.tsx enforces by type",
  );

  // Unit #20's rule: the screen carries actions, the doctrine page carries prose.
  pin.doesNotMatch(
    withoutComments(page),
    /결박 확인 기록은 사람이 실제로 본 그림의 해시에 묶이므로/,
    "expected the doctrine paragraph to stay at /docs rather than being pasted onto the screen",
  );
});

contractTest(
  "phasesHelpEntries' titles and bodies are pinned as EXACT triples, in ko AND en AND ja",
  (pin) => {
    const help = read("lib/help-content.tsx");

    // DEFECT MEASURED 2026-08-24 round 2 (refuter): a ko-only exact-string
    // pin (this test's prior form) still passed after the SAME contradicting
    // clause was appended to the EN body instead of the ko one -- #50-d's own
    // measured defect class, restated exactly: "fragment pins let en/ja
    // drift" applies just as much to a language-SCOPED exact pin as to a
    // substring one. Per the recorded threat-model boundary
    // (docs/phase2/DECISIONS.md, "2026-08-24 — 계약 핀의 위협 모델 경계 (#55b
    // 3패스에서 명시)"): "언어 우회 → ko만이 아니라 ko/en/ja 세 본문 전부". Widened
    // to all three languages AND to the titles (each entry's `title: t(ko,
    // en, ja)` also carries a per-language claim a fragment pin could drift
    // on unnoticed).
    //
    // IMPLEMENTATION: reuses this suite's own `TS_STRING_LITERAL` and
    // `unquoteTsString` (defined below, in scope by the time this callback
    // actually runs -- node:test executes test bodies after the whole module
    // has finished its synchronous top-level evaluation) to extract every
    // `title: t(...)` and `body: t(...)` triple from phasesHelpEntries, in
    // SOURCE ORDER, and compares the extracted set to a hardcoded expected
    // set via a single anchored string-equality pin per field -- the same
    // "count="/"files=" idiom MICROTASK_DEFERRAL_FILES and the U-19 resolver
    // count use above, generalised from a scalar to a whole array by
    // serialising it. An insertion, deletion, reorder, or per-language reword
    // ANYWHERE in either set changes the serialised string and fails.
    //
    // MEASURED 2026-08-24: extracted programmatically from the live file (a
    // small script, not hand-copied) to rule out a transcription error making
    // this pin permanently red or, worse, silently vacuous.
    function phasesEntryTriples(field: "title" | "body"): string {
      const start = help.indexOf("export function phasesHelpEntries(");
      if (start === -1) return "MISSING";
      const end = help.indexOf("\nexport function ", start + 1);
      const slice = help.slice(start, end === -1 ? undefined : end);
      const pattern = new RegExp(
        `${field}:\\s*t\\(\\s*(${TS_STRING_LITERAL.source})\\s*,\\s*(${TS_STRING_LITERAL.source})\\s*,\\s*(${TS_STRING_LITERAL.source})\\s*,?\\s*\\)`,
        "g",
      );
      const triples = [...slice.matchAll(pattern)].map((m) => [
        unquoteTsString(m[1]),
        unquoteTsString(m[2]),
        unquoteTsString(m[3]),
      ]);
      return JSON.stringify(triples);
    }

    const EXPECTED_TITLES = JSON.stringify([
      ["결박이 사람의 몫입니다", "Binding stays a human act", "結縛は人の仕事です"],
      ["signals 입력", "Signals input", "信号入力"],
      ["입력하는 세 가지 방법", "Three ways to add one", "入力する三つの方法"],
      ["엑셀에는 도면이 없습니다", "A spreadsheet carries no drawing", "エクセルに図面はありません"],
      [
        "그려진 도식은 배치이지 실제 지리가 아닙니다",
        "The drawn strip is a layout, not real geography",
        "描かれた図は配置であり実際の地理ではありません",
      ],
    ]);
    const EXPECTED_BODIES = JSON.stringify([
      [
        "현시도는 그림이므로 어떤 파싱도 접근로–현시 배정을 자동으로 만들어내지 못합니다. 템플릿을 채워 올린 엑셀은 올린 사람 자신의 판독 기록이며, 이 화면은 그 기록을 그대로 도식으로 그릴 뿐 정오를 판단하지 않습니다. 초안 네트워크의 어느 노드·링크에 묶이는지만은 signals 입력을 만들 때 사람이 각 카드의 네트워크 결박 절에서 정하며, 그 결박이 이 제품에서 사람 손으로 남아 있는 유일한 확인입니다.",
        "The 현시도 is a drawing, so no parse can produce the approach→phase mapping on its own. A workbook somebody filled in and uploaded is THAT PERSON'S OWN reading; this screen draws it exactly as written and judges nothing. Only which draft node and link ids it binds to is still decided by a human, in each card's 네트워크 결박 section, at the moment a signals input is built -- that binding is the one human-confirmed act left in this product.",
        "現示図は図面であり、解析だけでは流入路と現示の対応を自動的には作れません。テンプレートを記入して送られたエクセルは提出者自身の判読記録であり、この画面はそれをそのまま図として描くだけで正誤は判断しません。草案ネットワークのどのノード・リンクに結びつくかのみ、signals入力を作るときに人がカードの네트워크 결박で決めます — その結縛が、この製品に残る唯一の人による確認です。",
      ],
      [
        "네트워크 결박까지 확인된(CONFIRMED) 교차로만 signals 입력에 포함됩니다 — 업로드만 되고 결박이 없거나 재확인이 필요한 교차로는 빠집니다.",
        "Only intersections CONFIRMED all the way to their network binding are included in the signals input — an upload with no binding yet, or one needing re-confirmation, is left out.",
        "signals入力には、ネットワーク結縛まで確認済み（CONFIRMED）の交差点のみが含まれます — アップロードのみで結縛がない、または再確認が必要な交差点は除外されます。",
      ],
      [
        "템플릿을 내려받아 채운 뒤 올리거나, 화면에서 빈 양식을 새로 만들거나, 이미 있는 기록을 엑셀 초안으로 내보내 고친 뒤 다시 올릴 수 있습니다. 세 가지 모두 결과는 올린 사람의 기록이며, 그 기록이 어느 초안 노드·링크에 묶이는지는 이후 네트워크 결박에서 사람이 정합니다.",
        "Download the template and upload it filled in, create a blank form on the screen, or export an existing record as a draft workbook, correct it and send it back. All three produce THAT PERSON'S OWN RECORD -- which draft node and links it binds to is decided afterward, by a human, in 네트워크 결박.",
        "テンプレートを記入して送るか、画面で空の様式を作るか、既存の記録を草案として書き出し修正して再送します。三つとも結果は送った本人自身の記録であり、どの草案ノード・リンクに結びつくかはその後、人が네트워크 결박で決めます。",
      ],
      [
        "엑셀로 만든 검토와 직접 만든 빈 양식에는 현시도 그림이 없습니다. 이 앱은 그 자리에 가짜 이미지를 넣지 않고 '이미지 없음'이라고 적습니다 — 결박 확인 기록은 사람이 실제로 본 그림의 해시에 묶이므로, 없는 그림을 있는 것처럼 표시하면 그 기록이 거짓이 됩니다. 또한 한 줄이라도 거부되면 그 교차로 전체를 가져오지 않습니다.",
        "A review made from a spreadsheet, or created blank on the screen, has no 현시도 image. This app writes '이미지 없음' there instead of a stand-in picture: the binding attestation is bound to the hash of the drawing the person actually looked at, so a fake image would make that record a lie. And if one row is refused, the whole intersection is left out rather than half-imported.",
        "エクセルから作成した検討や画面で作成した空の様式には現示図の画像がありません。本アプリは代わりの画像を置かず「画像なし」と記します — 結縛確認の記録は人が実際に見た図面のハッシュに紐づくためです。また一行でも拒否されればその交差点全体を取り込みません。",
      ],
      [
        "업로드나 빈 양식에는 현시도 그림이 없으므로, 이 화면은 파싱된 접근로·현시 값으로 도식을 새로 그립니다. 이 도식은 방위 열이 없는 템플릿(v1)의 한계를 그대로 물려받아, 접근로의 위치를 시트에 적힌 순서로만 배치합니다 — 실제 방위를 주장하지 않으며, 판독되지 않은(미상) 이동류는 화살표가 아니라 글로 적힌 칩으로만 나타납니다.",
        "An upload or a blank form carries no 현시도 image, so this screen draws its own diagram from the parsed approach and phase values. That drawing inherits the v1 template's own limit -- no bearing column -- and places each leg only in the order the sheet lists it; it makes no claim about a real compass direction, and any movement the sheet left unresolved (미상) renders as a text chip, never as an arrow.",
        "アップロードや空の様式には現示図の画像がないため、この画面は解析された流入路・現示の値から独自に図を描きます。この図はv1テンプレート自体の制約(方位の列がない)をそのまま引き継ぎ、各流入路をシートに記載された順序のみで配置します — 実際の方位を主張せず、判読できなかった(不明な)移動流は矢印ではなく文字のチップとしてのみ表示されます。",
      ],
    ]);

    pin.match(
      `titles=${phasesEntryTriples("title")}`,
      new RegExp(`^titles=${literalPattern(EXPECTED_TITLES).source}$`),
      "expected phasesHelpEntries' 5 titles to equal this EXACT ko+en+ja set -- any insertion, appended clause, reorder, or per-language drift (ko OR en OR ja) fails here",
    );
    pin.match(
      `bodies=${phasesEntryTriples("body")}`,
      new RegExp(`^bodies=${literalPattern(EXPECTED_BODIES).source}$`),
      "expected phasesHelpEntries' 5 bodies to equal this EXACT ko+en+ja set -- same guarantee as titles, extended to the doctrine's actual claims (this is what caught the round-2 EN-body insertion the ko-only version of this pin missed)",
    );
  },
);

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
    // #55b (docs/phase2/DECISIONS.md, "2026-08-24 -- 사용자 결정: 현시도 화면은
    // 엑셀 업로드로 통합 ... (#55)") moved the drawn diagram into its own file.
    // VACUITY MEASURED 2026-08-24 (unit #55b refuter): the three rules below
    // (RULE 1 fill-not-ink, RULE 2 no-amber, RULE 3 12px floor) all walk this
    // `files` array, so a component created after this test was written got
    // none of them for free -- appended here so the sweep actually covers it
    // rather than passing by never looking. Fixed-index pins below
    // (files[0]/[1]/[2]) are unaffected: this entry is appended, not inserted.
    ["components/genesis/PhaseDiagramSketch.tsx", read("components/genesis/PhaseDiagramSketch.tsx")],
    // UNIT #58, and the SAME class this array was last widened for. The
    // refuter planted `text-[9px]` + an amber class in each of the two files
    // #58 added and the whole suite stayed green -- a new component gets none
    // of the three rules below for free, and the warning left above did not
    // stop it happening a second time. Appended, not inserted: the
    // fixed-index pins further down (files[0]/[1]/[2]) are unaffected.
    ["components/genesis/PhaseTimingTable.tsx", read("components/genesis/PhaseTimingTable.tsx")],
    ["components/genesis/DraftNodePickMap.tsx", read("components/genesis/DraftNodePickMap.tsx")],
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
  // `border-sky-300` is the same fall-through in a border. HOISTED to module
  // scope on 2026-08-21 so the calibration surface can be held to the identical
  // pattern instead of a second copy of it (see UNMAPPED_SKY_BORDER); the regex
  // itself is byte-for-byte what it always was.
  const unmappedSkyBorder = UNMAPPED_SKY_BORDER;

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
  // RE-PINNED 2026-08-19: the selection now carries the job it was made in
  // (`{ jobId, item }`), so switching jobs clears it during render instead of
  // through a reset effect one render later. Still exactly ONE, which is what
  // this pin has always been about.
  const selectionStateCount = (tileMap.match(/useState<\{ jobId: string; item: SelectedItem \}>/g) ?? []).length;
  pin.match(
    `count=${selectionStateCount}`,
    /^count=1$/,
    "expected exactly one selection state in GenesisTileMap.tsx -- FindingsTileLayer must not carry its own copy",
  );
  pin.match(
    tileMap,
    /const selected = selectedState\.jobId === jobId \? selectedState\.item : null;/,
    "expected the selection to be scoped to its job by derivation, so a stale selection cannot outlive the job it was made in",
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

test("unit #66: screenConstantSize keeps a node-pick dot's radius and label size constant on screen across zoom", () => {
  // At viewScale 1 the helper must reduce to the plain base size -- the same
  // "no zoom, no change" baseline dividerOffsetCoord's own value test above
  // asserts for a lane divider's offset.
  const baseViewUnits = 5.5; // NODE_R's own value
  assert.equal(screenConstantSize(baseViewUnits, 1), baseViewUnits);

  // THE INVARIANT: screenConstantSize(v, s) * s === v for any s -- the
  // ambient scale(view.scale) transform the caller sits inside undoes the
  // division exactly, at ANY zoom, low or high (MIN_SCALE 0.5 .. MAX_SCALE
  // 24 is this component's own real range).
  for (const viewScale of [0.5, 1, 1.18, 1.18 ** 3, 2, 24]) {
    const size = screenConstantSize(baseViewUnits, viewScale);
    assert.ok(
      Math.abs(size * viewScale - baseViewUnits) < 1e-9,
      `expected screenConstantSize(${baseViewUnits}, ${viewScale}) * ${viewScale} === ${baseViewUnits}, got ${size * viewScale}`,
    );
  }

  // THE DEFECT THIS CLOSES, MEASURED: at MAX_SCALE=24 with NO compensation
  // (the pre-#66 code rendered `r={NODE_R}` straight into the zoomed group),
  // the on-screen radius would have been NODE_R * 24 = 132 view units -- the
  // "100+ view units wide" the unit brief measured. The compensated radius
  // must instead render the SAME 5.5 on screen at that zoom.
  const atMaxZoom = screenConstantSize(baseViewUnits, 24);
  assert.ok(
    Math.abs(atMaxZoom * 24 - baseViewUnits) < 1e-9,
    `expected the on-screen radius at MAX_SCALE=24 to stay ${baseViewUnits}, got ${atMaxZoom * 24}`,
  );
});

contractTest("unit #66: node-pick dot radius and label size stay a constant on-screen size across zoom, and dense labels wait for it", (pin) => {
  const localMap = read("components/genesis/DraftLocalMap.tsx");

  // ---- The compensation itself: r and fontSize are EXPRESSIONS, not bare
  // constants -- structural, not merely "screenConstantSize appears
  // somewhere in the file". ------------------------------------------------
  pin.match(
    localMap,
    /const r = screenConstantSize\(NODE_R, viewScale\);/,
    "expected the dot radius to be computed via screenConstantSize against the CURRENT viewScale, not rendered as the bare NODE_R constant",
  );
  pin.match(
    localMap,
    /const labelFontSize = screenConstantSize\(NODE_LABEL_FONT_SIZE, viewScale\);/,
    "expected the label's font size to be computed via screenConstantSize against the CURRENT viewScale, not a bare numeric literal",
  );
  pin.match(
    localMap,
    /const labelGap = screenConstantSize\(NODE_LABEL_GAP, viewScale\);/,
    "expected the label's dot-to-baseline gap to be compensated the same way as r and fontSize, so it cannot drift into the dot as the dot's own on-screen radius changes with zoom",
  );
  pin.match(localMap, /r=\{r\}/, "expected the circle to render the COMPENSATED radius");
  pin.match(localMap, /fontSize=\{labelFontSize\}/, "expected the text to render the COMPENSATED font size");
  pin.match(
    localMap,
    /y=\{cy - r - labelGap\}/,
    "expected the label's y offset to be measured from the COMPENSATED radius and gap, not the raw fit-scale NODE_R/constant",
  );

  // ---- The ban: a bare NODE_R (or a raw fontSize={9}) rendered straight
  // into the zoomed group must never come back -- that IS the pre-#66
  // defect (a dot that grows 100+ view units wide at MAX_SCALE=24). ----------
  pin.doesNotMatch(
    withoutComments(localMap),
    /r=\{NODE_R\}/,
    "expected no bare r={NODE_R} inside the zoom group -- the radius must always be compensated by screenConstantSize, never the raw fit-scale constant",
  );
  pin.doesNotMatch(
    withoutComments(localMap),
    /fontSize=\{9\}|fontSize=\{NODE_LABEL_FONT_SIZE\}/,
    "expected no raw, un-compensated font size on the node label",
  );

  // ---- Zoom threading: viewScale reaches NodePickLayer from the SAME
  // view.scale state the rest of the map's pan/zoom already uses -- never a
  // second, duplicated zoom value. ------------------------------------------
  pin.match(
    localMap,
    /<NodePickLayer[\s\S]{0,200}viewScale=\{view\.scale\}/,
    "expected NodePickLayer to receive the map's own view.scale, not a second copy of zoom state",
  );

  // ---- The density gate: labels hide below LABEL_MIN_ZOOM, EXCEPT the
  // selected node's label, which must always render. -------------------------
  pin.match(
    localMap,
    /const showLabel = selected \|\| viewScale >= LABEL_MIN_ZOOM;/,
    "expected the label-visibility gate to exempt the selected node -- the user must always see what they picked, at any zoom",
  );
  pin.match(localMap, /\{showLabel && \(/, "expected the label element itself to be conditioned on the gate");

  // ---- The <title> hover tooltip is UNCONDITIONAL -- it must render at
  // every zoom regardless of the label-density gate, since it carries the
  // SAME id -- name a hidden label would have shown. Sliced from the g-key
  // marker up to <circle so the check is "nothing gates the title", not a
  // guessed character span. -------------------------------------------------
  const titleBlock = sliceBetween(localMap, "<g key={`node-${node.id}`}>", "<circle");
  pin.doesNotMatch(titleBlock, /showLabel/, "expected the <title> tooltip to stay unconditional, never gated behind showLabel");
  pin.match(
    titleBlock,
    /<title>\{`\$\{node\.id\} — \$\{node\.name\}`\}<\/title>/,
    "expected the id -- name hover tooltip to remain, unconditionally, at every zoom",
  );

  // ---- The dot itself is NEVER gated by the density threshold -- only the
  // label waits. The circle must sit outside any showLabel conditional. -----
  const circleBlock = sliceBetween(localMap, "<circle", "/>");
  pin.doesNotMatch(circleBlock, /LABEL_MIN_ZOOM|showLabel/, "expected the dot (the click target) to render at every zoom, never gated by the label-density threshold");

  // ---- STRUCTURAL hardening (refuter finding on this unit): the two pins
  // just above ban specific IDENTIFIERS (showLabel / LABEL_MIN_ZOOM) inside a
  // slice that starts AT the element's own opening tag ("<title>" / "<circle").
  // A mutation that wraps the WHOLE element -- opening tag included -- in an
  // inline `{cond && (...)}` or `{cond ? (...) : null}` inserts that wrapper
  // BEFORE the slice's own start marker, so it is invisible to a
  // doesNotMatch over the slice, no matter which identifier the condition is
  // spelled with (renaming `showLabel` or writing the raw comparison inline
  // both slip past the two pins above). These two positional pins do not
  // care what the wrapper is CALLED: they assert that <title> is the g
  // element's IMMEDIATE child right after `<g key=...>` (nothing but
  // whitespace between them) and that <circle is the IMMEDIATE sibling right
  // after `</title>` (same) -- ANY conditional wrapper, `&&` or ternary,
  // under any name, inserts non-whitespace there and breaks the match.
  pin.match(
    localMap,
    /<g key=\{`node-\$\{node\.id\}`\}>\s*<title>/,
    "expected <title> to be the g element's IMMEDIATE, unconditional child -- no {cond && (...)} or ternary wrapper may sit between <g> and <title>, under any name",
  );
  pin.match(
    localMap,
    /<\/title>\s*<circle/,
    "expected <circle> (the click target) to be the IMMEDIATE, unconditional sibling right after </title> -- no {cond && (...)} or ternary wrapper may sit between them, under any name, at any zoom",
  );
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
    // Unit #40 put a THIRD in -- the widened-region proposal, whose action left
    // this screen for the region page's own ask-first button. UNIT #48-b (user
    // decision, 2026-08-20) REMOVED it, so `{disposition.boundary_extension && (`
    // is struck from this list rather than re-aimed: there is no third treatment
    // block on a finding card any more. The RULE this test exists for is
    // untouched and now covers strictly MORE of the branch -- everything that
    // block used to occupy is refusal text again, and the no-<button pin below
    // runs over it.
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
  // `functionSource`, not a "\n}\n" marker: on a Windows checkout that marker
  // never matches, the slice comes back "", and a `doesNotMatch` over an empty
  // string passes VACUOUSLY -- green over a live violation. Measured 2026-08-26:
  // this pin was in exactly that state (unit #59, refuter round 3, R3-8).
  pin.doesNotMatch(
    functionSource(api, "export async function regenerateGenesisJob"),
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

contractTest(
  "unit #69: regenerating WITH a newly staged side input is a DIFFERENT typed call than the plain regeneration",
  (pin) => {
    const api = read("lib/api.ts");

    // Measured before this unit was built: unit #38's `POST .../regenerate`
    // structurally CANNOT carry this call -- its request model forbids every
    // field but `reason` and its eligibility gate requires an unrelated
    // REGENERATE_WITH_FIX finding. This is the sibling door instead, sharing
    // unit #38's source-verification codes and none of its "geometry only"
    // invariant. Pinned together with the plain route so neither can quietly
    // become the other.
    pin.match(
      api,
      /export async function regenerateGenesisJobWithSideInputs\(/,
      "expected a distinctly-named helper -- not an overload of regenerateGenesisJob",
    );
    pin.match(
      api,
      /export async function regenerateGenesisJobWithSideInputs[\s\S]*?apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/regenerate-with-side-inputs`/,
      "expected the call to go through apiMutationFetch, against the unit #69 route -- not the plain /regenerate one",
    );
    pin.match(
      api,
      /apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/regenerate`/,
      "expected the plain regenerate route to survive beside the side-inputs one -- two different claims, two different doors",
    );
    // The body carries only what the server's own request model accepts --
    // demand_token / signals_token / reason -- and nothing a caller could use
    // to invent a requester, an option or a source the door does not ask for.
    pin.match(
      api,
      /body: \{ demand_token\?: string \| null; signals_token\?: string \| null; reason\?: string \| null \}/,
      "expected the body type to name exactly the three fields the server's RegenerateWithSideInputsRequest accepts",
    );
    pin.doesNotMatch(
      functionSource(api, "export async function regenerateGenesisJobWithSideInputs("),
      /requested_by|exclude_service|options/,
      "expected the client to send no requester and no options -- both are the parent's own, carried server-side",
    );
    // Refusals throw through the same genesisError() every genesis mutation
    // uses, so `.code`/`.reasons` reach the UI verbatim rather than through a
    // second, hand-rolled error path.
    pin.match(
      functionSource(api, "export async function regenerateGenesisJobWithSideInputs("),
      /if \(!r\.ok\) throw await genesisError\(r\);/,
      "expected the same genesisError() translation every genesis mutation uses",
    );
  },
);

contractTest("unit #40: the region hand-off carries ONE shape, through ONE module", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const region = read("app/genesis/region/page.tsx");
  const handoff = read("lib/region-prefill.ts");

  // 0. PINS REMOVED HERE, WITH THE AUTHORITY THAT ORDERED IT (user decision,
  //    2026-08-20 -- unit #48-b). This test used to open on the per-finding
  //    treatment block: the `{disposition.boundary_extension && (` gate, the
  //    button inside it, and its ko label "경계를 넓혀 다시 생성". The automatic
  //    boundary-extension proposal UI was REMOVED after live use measured the
  //    geometry out -- every widened ring cuts new roads at the new edge, so the
  //    proposals were whack-a-mole -- and pins for a feature that no longer
  //    exists cannot be re-aimed, only struck. The convention is unchanged by
  //    this: a pin is re-aimed and never weakened, and a REMOVAL carries the
  //    decision that ordered it, in writing, at the site. What stands in its
  //    place is pinned by the two "unit #48-b" tests further down.
  //
  //    Everything below is untouched: this test was always about the HAND-OFF,
  //    which survives -- it now carries the job's OWN requested ring instead of
  //    a derived one, and every property pinned here is a property of the
  //    carrying, not of what is carried.

  // 1. THE HAND-OFF SPELLING. One module owns the key and the shape, and both
  //    sides import it -- a second copy of the string is how a writer and a
  //    reader drift into never meeting.
  pin.match(
    handoff,
    /export const REGION_PREFILL_STORAGE_KEY = "prism-region-prefill";/,
    "expected the sessionStorage key to be declared exactly once, in the hand-off module",
  );
  // Both sides, in whatever order and across however many lines Prettier wants
  // -- the load-bearing fact is WHERE each page gets its writer/reader, not the
  // shape of the import statement.
  pin.match(
    page,
    /import \{(?=[^}]*\bstashRegionPrefill\b)[^}]*\} from "@\/lib\/region-prefill";/,
    "expected the job page to write the hand-off through the shared module",
  );
  pin.match(
    region,
    /import \{(?=[^}]*\btakeRegionPrefill\b)(?=[^}]*\btype RegionPrefill\b)[^}]*\} from "@\/lib\/region-prefill";/,
    "expected the region page to read the hand-off through the same shared module",
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

  // 2. THE HAND-OFF LANDS IN THE PAGE'S OWN STATE, so the map, the editable
  //    table and the server-side measurement all keep reading one array -- and
  //    the page's own extract button stays the only thing that fetches.
  // (The one line between the guard and the write is the hydration
  // eslint-disable this read carries -- sessionStorage cannot be read during
  // render without desynchronising the server's HTML from the client's first
  // paint, which is why this is the page's only remaining state-setting effect.
  // See lib/asyncResource.ts's closing note.)
  pin.match(
    region,
    /const handed = takeRegionPrefill\(getWorkspaceId\(\)\);\s*\n\s*if \(!handed\) return;\s*\n(?:\s*\/\/[^\n]*\n)?\s*setPrefill\(handed\);\s*\n\s*setVertices\(handed\.polygon\);/,
    "expected the hand-off to become this page's own vertices rather than a second shape beside them",
  );
  const arrival = sliceBetween(region, "const handed = takeRegionPrefill(", "const loadFetches");
  pin.match(arrival, /\S/, "expected to find the arrival effect (markers moved?)");
  pin.doesNotMatch(
    arrival,
    /startRegionFetch|startGenesisJob/,
    "expected the hand-off to fetch NOTHING on arrival -- the extract button is the ask-first gate",
  );

  // 3. IT IS STAMPED WITH ITS 프로젝트, and the reader checks the stamp.
  //    sessionStorage is per-tab and survives the reload ProjectSelector
  //    switches projects with, so a write that lost its navigation would
  //    otherwise pre-fill ANOTHER project's extract with this one's polygon
  //    under a banner naming a job that does not exist there. (Behaviour --
  //    not just spelling -- is exercised by the imported-module test below.)
  pin.match(
    page,
    /workspace_id: getWorkspaceId\(\),/,
    "expected the job page to stamp the hand-off with the project it was taken in",
  );
  pin.match(
    handoff,
    /if \(\(workspaceId \?\? null\) !== \(currentWorkspaceId \?\? null\)\) return null;/,
    "expected takeRegionPrefill to refuse a payload stamped with another project",
  );

  // 4. THE BANNER says WHAT the ring is, so a reader can compare it against the
  //    map instead of trusting a polygon that appeared out of nowhere. Re-aimed
  //    by unit #48-b: the ring is no longer a derived widening with a deficit
  //    and an added area to quote, it is the parent job's own requested region,
  //    and the banner says exactly that.
  pin.match(
    region,
    /\{prefill && \(/,
    "expected the region page to explain itself when it opens on a shape nobody drew here",
  );
  const banner = sliceBetween(region, "{prefill && (", "<div className=\"mb-4 flex flex-col gap-3\">");
  pin.match(banner, /\S/, "expected to find the arrival banner (markers moved?)");
  pin.match(
    banner,
    /작업 \$\{prefill\.parent_job\}이 추출에 사용한 영역 그대로입니다/,
    "expected the banner to name the parent job and to say the ring is ITS OWN, not a widened one",
  );
  pin.doesNotMatch(
    withoutComments(banner),
    /넓힌 영역|was widened|広げた領域/,
    "the banner must not claim the ring was widened -- nothing widens it any more except the reader's own drag",
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
  // here against a fake store. Four properties, each of which was reachable as
  // a real defect before it was pinned:
  //   * one-shot -- a second read returns null, so a hand-off cannot reappear
  //     over a ring the user has since edited;
  //   * project-scoped -- a payload stamped in project A is REFUSED (and still
  //     removed) when read in project B. sessionStorage is per-tab and survives
  //     the reload a project switch does, so "written once" is not the same as
  //     "read in the project it came from";
  //   * re-validated -- a store a devtools user edited into nonsense yields
  //     null rather than a NaN vertex that throws inside Leaflet's tile layer;
  //   * kind-checked (unit #48-b) -- a payload written by the PREVIOUS build
  //     carried a ring this app had already widened, under this very key. An
  //     unconsumed one must not be read back as "the region this job was
  //     extracted with", and the sessionStorage entry outlives a deploy.
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
    const handOff = {
      polygon: [
        [36.35, 127.38],
        [36.35, 127.39],
        [36.34, 127.39],
      ] as Array<[number, number]>,
      workspace_id: "bfc69251bd04",
      parent_job: "5e550c739aed",
      kind: PREFILL_KIND_MANUAL_REGION_EDIT,
      covers: [],
    };

    assert.equal(stashRegionPrefill(handOff), true);
    const same = takeRegionPrefill("bfc69251bd04");
    assert.equal(same?.parent_job, "5e550c739aed");
    assert.equal(same?.polygon.length, 3);
    assert.equal(
      takeRegionPrefill("bfc69251bd04"),
      null,
      "one-shot: the second read must find nothing",
    );

    // Project A's hand-off, read in project B: refused, and gone afterwards.
    assert.equal(stashRegionPrefill(handOff), true);
    assert.equal(
      takeRegionPrefill("a9cbf243c5f4"),
      null,
      "a region taken in another 프로젝트 must never pre-fill this one's extract",
    );
    assert.equal(store.size, 0, "the refused payload must still be removed from the store");

    // The default project is `null` on both sides, and it is not a wildcard.
    assert.equal(stashRegionPrefill({ ...handOff, workspace_id: null }), true);
    assert.equal(takeRegionPrefill("bfc69251bd04"), null);
    assert.equal(stashRegionPrefill({ ...handOff, workspace_id: null }), true);
    assert.equal(takeRegionPrefill(null)?.workspace_id, null);

    // A store edited by hand -- or written by the build before 2026-08-20:
    // every one of these is dropped, never repaired.
    for (const broken of [
      "{not json",
      JSON.stringify({ ...handOff, polygon: [[36.35, 127.38]] }),
      JSON.stringify({ ...handOff, polygon: [[36.35, 127.38], [36.35, 127.39], ["x", 1]] }),
      JSON.stringify({ ...handOff, polygon: [[36.35, 127.38], [36.35, 127.39], [999, 1]] }),
      JSON.stringify({ ...handOff, parent_job: "" }),
      JSON.stringify({ ...handOff, workspace_id: 7 }),
      // The retired shape: no `kind`, and the derived scalars unit #40 used to
      // carry. Its `polygon` was a WIDENED ring, which is exactly why reading it
      // back under the new banner would be a lie about what the extract covers.
      JSON.stringify({
        polygon: handOff.polygon,
        workspace_id: "bfc69251bd04",
        parent_job: "5e550c739aed",
        finding_code: "v4_lane_drop_without_runway",
        subject: "90 (둔산로)",
        deficit_m: 147.85,
        added_area_km2: 0.012976,
        measured_source_m: 127.06,
        extrapolated_m: 50.36,
      }),
      JSON.stringify({ ...handOff, kind: "derived_extension" }),
      JSON.stringify({ ...handOff, covers: undefined }),
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
  // The cache key is now written once, as `fixesKey`, and used by both the
  // fetch and the render-side read below (lib/asyncResource.ts's shape, spelled
  // out by hand here because this screen needs the GenesisError instance rather
  // than a message string). It is still (job id, checked_at) and nothing else.
  pin.match(
    page,
    /const fixesKey = gatedJobId \? `\$\{gatedJobId\}\|\$\{gateCheckedAt \?\? ""\}` : "";/,
    "expected the fixes fetch to key its cache entry on (job id, checked_at)",
  );
  pin.match(
    page,
    /fetchGenesisJobFixes\(gatedJobId\)\s*\n\s*\.then\(\(payload\) => \{\s*\n\s*if \(!cancelled\) setFixesAnswer\(\{ key: fixesKey, payload, error: null \}\);/,
    "expected the fixes fetch to record its payload against that key",
  );
  pin.match(
    page,
    /\}, \[gatedJobId, gateCheckedAt, fixesKey\]\);/,
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
    /const fixesCurrent = fixesAnswer && fixesAnswer\.key === fixesKey \? fixesAnswer : null;/,
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
  // CO-EDITED by unit #69's refuter finding F3 (2026-08-26): the union grew
  // THREE more kind-bearing lineages (regenerate / dead_end_exclusion /
  // signals_regeneration) that already existed server-side but were never
  // typed here -- which is exactly how the page below narrowed on
  // `"kind" in derived_from` alone and rendered every one of them as a live
  // check. Pinned as five named members, not a collapsed count, so a future
  // sixth lineage kind has to be typed here or this pin catches the gap.
  pin.match(
    api,
    /export type GenesisJobLineage =\s*\n\s*\| GenesisFixLineage\s*\n\s*\| GenesisLiveCheckLineage\s*\n\s*\| GenesisRegenerateLineage\s*\n\s*\| GenesisDeadEndExclusionLineage\s*\n\s*\| GenesisSignalsRegenerationLineage;/,
    "expected the union to name all five lineage kinds, so a live-check job cannot be read as a fix job and a regenerate/dead-end/signals-regeneration job cannot be read as a live check",
  );
  for (const [kindLiteral, interfaceName] of [
    ["live_check", "GenesisLiveCheckLineage"],
    ["regenerate", "GenesisRegenerateLineage"],
    ["dead_end_exclusion", "GenesisDeadEndExclusionLineage"],
    ["signals_regeneration", "GenesisSignalsRegenerationLineage"],
  ] as const) {
    pin.match(
      api,
      new RegExp(`export interface ${interfaceName} \\{\\s*\\n\\s*kind: "${kindLiteral}";`),
      `expected ${interfaceName} to discriminate on the literal kind "${kindLiteral}"`,
    );
  }

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

  // FINDING F3, THE FIX ITSELF: the kind-bearing branch narrows on the
  // ACTUAL VALUE (`lineageKindLabel(selected.derived_from.kind, t)`), never
  // on a hard-coded ko/en/ja triple that was only ever true for live_check.
  // The bug this replaces rendered EVERY kind-bearing lineage -- regenerate,
  // dead_end_exclusion, signals_regeneration included -- as "실 Vissim 확인".
  pin.doesNotMatch(
    page,
    /\("kind" in selected\.derived_from \?[\s\S]{0,40}t\("실 Vissim 확인"/,
    "expected the kind-bearing branch to narrow by VALUE, not render a hard-coded live-check label for every kind",
  );
  pin.match(
    page,
    /\{lineageKindLabel\(selected\.derived_from\.kind, t\)\} ·\{" "\}/,
    "expected the kind-bearing branch to translate the ACTUAL kind value through the shared label helper",
  );
  pin.match(
    page,
    /lineageKindLabel,\s*\n\s*networkScaleLabel,/,
    "expected lineageKindLabel to be imported from the shared genesisArtifacts module, not defined locally (a second copy could name a kind differently than the label table other readers use)",
  );

  // lineageKindLabel ITSELF: every kind gets its OWN sentence, keyed by the
  // literal switch case -- a `default` fallback alone would let a typo in one
  // case silently reuse whichever label the switch fell through to.
  const genesisArtifacts = read("lib/genesisArtifacts.ts");
  pin.match(
    genesisArtifacts,
    /export function lineageKindLabel\(/,
    "expected lineageKindLabel to live beside statusLabel -- the SAME shared job-record-wording module",
  );
  for (const kindCase of [
    /case "live_check":[\s\S]*?t\("실 Vissim 확인", "live check", "実Vissim確認"\)/,
    /case "regenerate":[\s\S]*?t\("재생성", "regenerated", "再生成"\)/,
    /case "dead_end_exclusion":[\s\S]*?t\("막다른 길 제외 재생성", "dead-end exclusion", "行き止まり除外による再生成"\)/,
    /case "signals_regeneration":[\s\S]*?t\("signals 입력 추가 재생성", "regenerated with an added signals input", "signals入力を追加して再生成"\)/,
  ]) {
    pin.match(genesisArtifacts, kindCase, `expected lineageKindLabel's own case: ${kindCase}`);
  }
  pin.match(
    genesisArtifacts,
    /default:\s*\n\s*return kind;/,
    "expected an UNRECOGNISED kind to name itself raw, never silently borrow another kind's label",
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
  // RE-PINNED 2026-08-19 with its sibling above: the one state now carries the
  // job it belongs to, so the shape being counted changed while the claim --
  // exactly one, owned by the map -- did not.
  const selectionStates = (tileMap.match(/useState<\{ jobId: string; item: SelectedItem \}>/g) ?? []).length;
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
  // RE-AIMED by unit #48-b (user decision, 2026-08-20): there are TWO treatment
  // blocks on a finding card now, not three. The widen-boundary one was removed
  // with the rest of the automatic proposal UI; the shape rule it was counted
  // under is unchanged and still binds every block that remains, which is why
  // the count moved rather than the pin being dropped.
  const treatmentBoxes = (page.match(/mt-3 flex flex-col gap-2 border-l-2 border-slate-300 pl-3/g) ?? []).length;
  pin.match(
    `count=${treatmentBoxes}`,
    /^count=2$/,
    "expected both treatment blocks (regenerate, attest) to read as a left rule, not a nested box",
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
  // `functionSource` for the CRLF reason above (R3-8): a "\n}\n" marker slices
  // to "" on a Windows checkout, and the `doesNotMatch` below would pass over
  // nothing at all. (The `pin.match` that follows it would have failed loudly,
  // which is how this one stayed honest -- but the ban is the load-bearing half.)
  const body = functionSource(api, "export async function confirmGenesisDeadEnd");
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
  const floatingAssistant = readApprovalSurface();
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
  const floatingAssistant = readApprovalSurface();
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
  const floatingAssistant = readApprovalSurface();
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
  // Unit #51-c moved both behind `chain_grant_providers` (a SECOND grant species
  // now exists), so the pins follow the code: what they still say is that the
  // proposing gate resolves through the broker and the approve gate is PINNED to
  // the id the file named.
  pin.match(
    assistantRouter,
    /gate = ProposeWriteGate\(\s*\n\s*grant_provider=chain_grant_providers\(\s*\n\s*car_following_broker\.grant_provider\(/,
    "expected the proposing gate to resolve its grant through the broker",
  );
  pin.match(
    assistantRouter,
    /grant_provider=chain_grant_providers\(\s*\n\s*car_following_broker\.grant_provider\([\s\S]{0,300}?set_id=str\(car_following_set_ref\)/,
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

// PIN TEST REMOVED HERE, WITH THE AUTHORITY THAT ORDERED IT (user decision,
// 2026-08-20 -- unit #48-b). What stood here was
// `contractTest("unit #48: the orphan's region and the all-at-once region are
// the backend's, never a client guess")`: it pinned the orphan card's
// `{disposition.boundary_extension_patches && (` gate, the job-level
// `{fixes?.boundary_extension_all && (` proposal, the shared
// `BoundaryPatchesProposal` component, and the composite's named refusal block.
//
// Every one of those was REMOVED from the product, so the pins could not be
// re-aimed -- there is nothing left for them to point at. The measured reason is
// on the record: the composite widened ring still cut new roads at its own new
// edge, the user pressed it repeatedly on their own network and watched the
// boundary findings move rather than close (jobs 7f263915d770 and e2f654400d21),
// and the refusal prose went with the offer it explained ("이건 지워").
//
// The convention is intact and this note is what keeps it so: pins are RE-AIMED,
// never weakened, and a pin that is struck instead carries, at the site, the
// decision that struck it. Nothing here was softened -- the properties that
// still have a subject are pinned harder, by the "unit #48-b" tests below, which
// also pin that none of this can creep back.

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

contractTest("unit #48: the hand-off names every finding it covers, and never renames a cut", (pin) => {
  const region = read("app/genesis/region/page.tsx");
  const handoff = read("lib/region-prefill.ts");
  const job = read("app/genesis/jobs/[id]/page.tsx");

  // The banner NAMES every finding the hand-off carries, rather than counting
  // them. This is the screen that owns the extract button, and a reader who is
  // about to widen a region has to be able to see that THEIR finding is one of
  // the ones the boundary caused. (Re-aimed by unit #48-b: the list is now a set
  // of HINTS beside the job's own ring, not a manifest of what a derived shape
  // covers -- the property it pins is the same one.)
  const banner = sliceBetween(region, "{prefill && (", "<div className=\"mb-4 flex flex-col gap-3\">");
  pin.match(banner, /\S/, "expected to find the arrival banner (markers moved?)");
  pin.match(
    banner,
    /prefill\.covers\.map\(\(cover, index\) => \(/,
    "expected every carried finding to be named in the banner, not just counted",
  );
  pin.match(
    banner,
    /boundaryHintLine\(cover, t\)/,
    "expected each line to be worded by the one helper that knows a cut from a shortage",
  );

  // ONE carried finding is not the same as one KIND of measurement. An orphan's
  // metres are metres the boundary CUT; a V4's are a runway SHORTAGE. The
  // backend's own basis note refuses to "call every finger's own number a
  // deficit, because half of them are not one"; the banner over the extract
  // button is the last place that could undo the refusal, and it did once.
  // (Refuter, unit #48.)
  const hint = sliceBetween(region, "function boundaryHintLine(", "\nfunction VertexRow(");
  pin.match(hint, /\S/, "expected to find the hint-line helper (markers moved?)");
  pin.match(
    hint,
    /cover\.basis_kind === BASIS_BOUNDARY_CUT/,
    "expected the wording to branch on WHAT was measured, not on how many findings",
  );
  pin.doesNotMatch(
    // Comment-stripped and scoped to the CUT branch: this helper's own comment
    // says the word "shortage" in order to explain why the branch exists, and a
    // pin that cannot tell prose from rendered text is a pin on the prose.
    sliceBetween(withoutComments(region), "cover.basis_kind === BASIS_BOUNDARY_CUT", ": t("),
    /부족분|shortage|不足分/,
    "a boundary CUT must not be renamed a shortage on the screen that owns the extract button",
  );
  // ...and the distinction has to SURVIVE the hand-off to be worth branching on.
  pin.match(
    job,
    /basis_kind: BASIS_BOUNDARY_CUT,/,
    "expected a severed orphan's hint to be stamped as a CUT where it is built",
  );
  pin.match(
    job,
    /basis_kind: BASIS_RUNWAY_DEFICIT,/,
    "expected a runway shortage's hint to be stamped as a SHORTAGE where it is built",
  );
  pin.match(
    handoff,
    /if \(typeof row\.basis_kind !== "string"\) return null;/,
    "expected a hint with no basis_kind to drop the hand-off rather than be read as a deficit",
  );

  // The store is re-validated, and a BROKEN covers list drops the WHOLE
  // hand-off. Returning the ring without its hints would open the region editor
  // naming none of the findings the reader came here for.
  pin.match(
    handoff,
    /if \(!Array\.isArray\(candidate\.covers\)\) return null;/,
    "expected a missing or non-array covers list to drop the hand-off rather than be ignored",
  );
});

contractTest("unit #48: the hint list round-trips, and a broken one is dropped whole", () => {
  // BEHAVIOUR, not spelling -- the same fake-store harness unit #40's own
  // hand-off test uses, over the field this unit added and unit #48-b re-aimed.
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
    const handOff = {
      polygon: [
        [36.35, 127.38],
        [36.35, 127.39],
        [36.34, 127.39],
      ] as Array<[number, number]>,
      workspace_id: "bfc69251bd04",
      parent_job: "e84e57316ccb",
      kind: PREFILL_KIND_MANUAL_REGION_EDIT,
      covers: [
        {
          finding_code: "v3_orphan_link",
          subject: "10 (tertiary_link [FWD] w37972527s0)",
          basis_kind: BASIS_BOUNDARY_CUT,
          basis_m: 9.32,
          way_id: 37972527,
          boundary_points: [
            { end: "start", boundary_node: -9000001, metres_cut: 9.32 },
          ],
        },
        {
          finding_code: "v4_lane_drop_without_runway",
          subject: "108 (둔산로 [FWD] w1416532929s1)",
          basis_kind: BASIS_RUNWAY_DEFICIT,
          basis_m: 13.94,
          way_id: 1416532929,
          boundary_points: [{ end: null, boundary_node: 4718924755, metres_cut: null }],
        },
      ],
    };

    assert.equal(stashRegionPrefill(handOff), true);
    const read1 = takeRegionPrefill("bfc69251bd04");
    assert.equal(read1?.covers.length, 2);
    assert.equal(read1?.covers[1].finding_code, "v4_lane_drop_without_runway");
    assert.equal(read1?.covers[1].way_id, 1416532929);
    // REGRESSION (refuter, unit #48): `basis_kind` must survive the hand-off.
    // It is the ONLY thing that tells the region page an orphan's metres are
    // metres the boundary CUT and not a runway shortage, and the banner it
    // renders sits over the extract button. Dropping it printed
    // "v3_orphan_link shortage of 9.32 m" -- the exact rename the backend's own
    // basis note refuses to make.
    assert.equal(read1?.covers[0].basis_kind, BASIS_BOUNDARY_CUT);
    assert.equal(read1?.covers[1].basis_kind, BASIS_RUNWAY_DEFICIT);
    // A boundary node with NO cut length keeps its null. A zero there would be a
    // measurement nobody took -- a runway shortage cuts nothing.
    assert.equal(read1?.covers[1].boundary_points[0].metres_cut, null);
    assert.equal(read1?.covers[1].boundary_points[0].end, null);
    assert.equal(read1?.covers[0].boundary_points[0].metres_cut, 9.32);
    assert.equal(takeRegionPrefill("bfc69251bd04"), null, "one-shot still holds with hints");

    // NO hints is a real state: a reader may open the region editor on a job
    // with no boundary story at all, and that must hand over the ring, not
    // nothing.
    assert.equal(stashRegionPrefill({ ...handOff, covers: [] }), true);
    assert.equal(takeRegionPrefill("bfc69251bd04")?.covers.length, 0);

    // Present but broken: the WHOLE hand-off is dropped, never half-read.
    for (const broken of [
      JSON.stringify({ ...handOff, covers: "two" }),
      JSON.stringify({ ...handOff, covers: [{ finding_code: "x", subject: "y" }] }),
      JSON.stringify({ ...handOff, covers: [{ ...handOff.covers[0], basis_m: "9.32" }] }),
      JSON.stringify({ ...handOff, covers: [{ ...handOff.covers[0], subject: 7 }] }),
      JSON.stringify({ ...handOff, covers: [{ ...handOff.covers[0], way_id: "37972527" }] }),
      // A cover with no `basis_kind` cannot be told apart from a runway
      // shortage, so it is dropped with the rest rather than guessed at.
      JSON.stringify({ ...handOff, covers: [{ ...handOff.covers[0], basis_kind: undefined }] }),
      // A boundary point is a measurement or it is not carried: no node id, or a
      // cut length that is not a number, drops the lot.
      JSON.stringify({
        ...handOff,
        covers: [{ ...handOff.covers[0], boundary_points: [{ end: "start", metres_cut: 9.32 }] }],
      }),
      JSON.stringify({
        ...handOff,
        covers: [
          {
            ...handOff.covers[0],
            boundary_points: [{ end: "start", boundary_node: -9000001, metres_cut: "9.32" }],
          },
        ],
      }),
      JSON.stringify({ ...handOff, covers: [{ ...handOff.covers[0], boundary_points: {} }] }),
    ]) {
      store.set(REGION_PREFILL_STORAGE_KEY, broken);
      assert.equal(takeRegionPrefill("bfc69251bd04"), null, `expected to drop: ${broken}`);
      assert.equal(store.size, 0, "a dropped hand-off must still leave the store empty");
    }
  } finally {
    if (previousWindow === undefined) delete (globalThis as { window?: unknown }).window;
    else (globalThis as { window?: unknown }).window = previousWindow;
  }
});

// ---------------------------------------------------------------------------
// Unit #48-b -- the automatic boundary-extension proposals are removed, and one
// manual "영역 편집" button stands in their place (user decision, 2026-08-20).
//
// The measurement that ended the feature: a widened lasso boundary cuts NEW
// roads wherever it lands, so healing one boundary finding opens another. The
// user pressed "전부 한 번에 넓혀 다시 생성" repeatedly on their own network and
// the findings moved instead of closing (jobs 7f263915d770 and e2f654400d21).
//
// What stays is the RULER, not the proposal: every boundary attribution the
// backend measured still renders as prose on the finding it belongs to, and the
// backend's derivation machinery is retained un-exposed. What the reader gets
// is their own region, on a map, with the measurements listed beside it.
// ---------------------------------------------------------------------------

contractTest("unit #48-b: the automatic boundary-extension proposals cannot come back", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const live = withoutComments(page);

  // 1. THE OFFER IS GONE, in both of its homes -- the per-finding cards and the
  //    job-level composite. Comment-stripped: this file explains at length WHY
  //    they went, and a pin that cannot tell prose from markup is a pin on the
  //    prose.
  for (const gone of [
    /BoundaryPatchesProposal/,
    /boundary_extension_patches/,
    /boundary_extension_all/,
    /extended_polygon_wgs84/,
    /corridor_patch/,
    /added_area_km2/,
  ]) {
    pin.doesNotMatch(live, gone, `expected no live use of ${gone} -- the derived-region UI was removed`);
  }
  // The wording itself, so a re-introduction under the old copy fails here
  // rather than shipping.
  //
  // STRENGTHENED (refuter, unit #48-b): this banned the button label with its
  // arrow (`경계를 넓혀 다시 생성 →`), which the removed BUTTONS carried -- and
  // so it passed while the card's own one-line answer still read "자동 수정 없음
  // — 경계를 넓혀 다시 생성해야 합니다.", an imperative for the exact flow the
  // unit deleted, aimed at no control that exists. The arrow is dropped from the
  // pattern: no live string on this screen may tell a reader to widen the
  // boundary and generate again, in a label or in prose.
  pin.doesNotMatch(
    live,
    /경계를 넓혀 다시 생성|전부 한 번에 넓혀 다시 생성/,
    "the widen-and-regenerate flow was removed -- no label and no answer line may command it",
  );

  // 2. THE REFUSAL PROSE IS GONE TOO ("이건 지워"). A named reason for
  //    withholding an option nobody is offered any more is noise, not honesty.
  // Comment-stripped like the rest: the page NAMES the struck sentence where it
  // records why it went, and a pin that cannot tell prose from rendered text
  // would fire on that explanation forever (and would pass today only because
  // the comment happens to wrap mid-sentence, which is luck, not a pin).
  pin.doesNotMatch(
    live,
    /경계 소견을 한 번에 덮는 영역은 제안하지 않습니다/,
    "the composite's refusal prose was struck by the user and must not creep back",
  );
  pin.doesNotMatch(
    live,
    /boundary_extension_all_refusal|corridor_patch_undrawable/,
    "no code may render the composite refusal any more",
  );

  // 3. THE MEASUREMENT SURVIVES, AND THE ANSWER LINE POINTS SOMEWHERE REAL.
  //    This is the half that must NOT be deleted with the proposal: a
  //    boundary-attributed finding still says so, in the backend's own words, on
  //    its own card.
  //
  //    REGRESSION (refuter, unit #48-b). The pin that stood here matched the
  //    literal sentence "자동 수정 없음 — 경계를 넓혀 다시 생성해야 합니다." and
  //    called it "the boundary is the cause". It is not a cause statement, it is
  //    an IMPERATIVE -- and until 2026-08-20 the card carried a
  //    "경계를 넓혀 다시 생성 →" button directly beneath it, which is what the
  //    imperative meant. That button was removed with the rest of the proposal
  //    UI and the sentence was left behind, so the reader was told to perform the
  //    one flow this unit deleted, by a line naming no control on the screen.
  //    Unit #41-d already ruled on this exact shape of copy ("Telling that reader
  //    the fix '가 적용된다' describes a button they do not have"): an answer line
  //    describes a treatment the reader can REACH.
  //
  //    So the pin is re-aimed at the two properties, not the spelling: the line
  //    still names the boundary as the cause, AND it names the one control that
  //    can act on it -- by the label that control actually carries, which is
  //    pinned verbatim in the next test.
  const boundaryAnswer = sliceBetween(
    page,
    ": disposition.boundary_extension",
    '"자동 수정 없음 — 사람이 판단해야 합니다."',
  );
  pin.match(boundaryAnswer, /\S/, "expected to find the boundary card's one-line answer (markers moved?)");
  pin.match(
    boundaryAnswer,
    /경계가 원인입니다/,
    "expected a boundary-attributed refusal to still SAY the boundary is the cause",
  );
  for (const label of [/'영역 편집'/, /'Edit region'/, /「領域を編集」/]) {
    pin.match(
      boundaryAnswer,
      label,
      `expected the answer line to name the ONE control that can act on it (${label}) -- the widen-and-regenerate button it used to point at no longer exists`,
    );
  }
  // ...and it stays gated on the DERIVED extension's key, which is what makes
  // naming that control safe rather than a second dead-end instruction. The key
  // is absent unless the census recorded a requested POLYGON
  // (`derive_v4_boundary_extension` refuses `requested_region_not_a_polygon`
  // otherwise) -- the same field `requestedPolygonOf` needs before
  // `RegionEditInvitation` renders at all. Keying this line off unit #39's
  // attribution instead would print "영역 편집" on a bbox job that has no such
  // button. Only the key's PRESENCE is read; none of the derived metres are.
  pin.match(
    page,
    /disposition\.boundary_extension$/m,
    "expected that sentence to still key off the backend's own boundary derivation, not a client guess",
  );
  pin.doesNotMatch(
    boundaryAnswer,
    /added_area_km2|corridor_patch|provenance\.deficit_m|extended_polygon_wgs84/,
    "the answer line may read that the key is THERE and nothing else -- the derived shape is un-exposed",
  );
  pin.match(
    page,
    /<p className="font-mono leading-relaxed text-slate-700">\{rationale\}<\/p>/,
    "expected the backend's own rationale -- which carries the way ids and the cut distances -- to still render verbatim",
  );
});

contractTest("unit #48-b: 영역 편집 hands over THIS job's own region, and derives nothing", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const map = read("components/genesis/GenesisTileMap.tsx");
  const artifacts = read("lib/genesisArtifacts.ts");

  // 1. ONE BUTTON, ko-primary, with its en/ja beside it.
  pin.match(
    page,
    /\{t\("영역 편집 →", "Edit region →", "領域を編集 →"\)\}/,
    "expected the one manual action to carry all three languages",
  );

  // 1b. A BUTTON, NOT A PANEL -- 사용자 지시 2026-08-20 "버튼만 남겨" ("just
  //     delete everything and leave 영역편집 버튼 only"). What was struck: the
  //     heading "이 생성이 요청한 영역", the explanation sentence and its
  //     boundary-finding-count branch, the corner count "점 N개", and the
  //     caption under the button. Every one of them described the screen the
  //     button opens -- and THAT screen says it itself, in its own pre-filled
  //     banner (parent job, corner count, boundary hints), which is pinned by
  //     the hand-off test above. Said twice is noise, not honesty.
  //
  //     PIN LEDGER, for the record this convention requires: nothing was
  //     removed to let this through. Every pin in this test was already about
  //     the DATA FLOW -- the label, the render gate, the ring's validation, the
  //     job tagging, the stashed payload, the kind-first read, the one-shot --
  //     and the deleted prose was never pinned by any of them. So this deletion
  //     ADDS a pin and weakens none.
  //
  //     Comment-stripped: the component now records the struck wording verbatim
  //     where it explains why it went, and a pin that cannot tell prose from
  //     rendered text would fire on that explanation forever.
  pin.doesNotMatch(
    withoutComments(page),
    /이 생성이 요청한 영역|경계가 원인인 소견이|점 \$\{[^}]*\.length\}개|누르면 영역 화면이 이 영역으로 열립니다/,
    "the 영역 편집 panel's prose was struck by the user -- the button stands alone, and no heading, count or caption may creep back around it",
  );

  // 2. THE RING IT CARRIES IS THE JOB'S OWN REQUEST, read off the census the map
  //    already fetched -- never derived here, and never re-fetched.
  pin.match(
    artifacts,
    /export function requestedPolygonOf\(/,
    "expected the recorded request ring to be read in one named place",
  );
  pin.match(
    artifacts,
    /metadata\.boundary\?\.requested_polygon_wgs84/,
    "expected that reader to take the ring off the census's own boundary record",
  );
  pin.match(
    artifacts,
    /if \(typeof lat !== "number" \|\| !Number\.isFinite\(lat\) \|\| lat < -90 \|\| lat > 90\) return null;/,
    "expected a recorded vertex to be range-checked -- these coordinates end up in Leaflet, where a NaN throws inside the tile layer",
  );
  pin.match(
    map,
    /const requestedPolygon = useMemo\(\(\) => requestedPolygonOf\(network\), \[network\]\);/,
    "expected the ring to be derived once per census, so publishing it upward cannot loop",
  );
  pin.match(
    map,
    /onRequestedPolygonChange\?\.\(requestedPolygon\);/,
    "expected the map to publish the ring rather than the page fetching the census a second time",
  );
  pin.match(
    page,
    /onRequestedPolygonChange=\{handleRequestedPolygonChange\}/,
    "expected the page to be wired to that publication",
  );
  pin.match(
    page,
    /polygon=\{requestedPolygon\}/,
    "expected the button to hand over the published ring, not a shape composed here",
  );
  // Tagged with its job, exactly as the placeable set is: a different job is a
  // different region, and the previous one surviving one render into the next
  // would offer to edit an area this generation never asked for.
  pin.match(
    page,
    /requestedPolygonState && requestedPolygonState\.jobId === jobId/,
    "expected the published ring to be read back only while it still belongs to the job on screen",
  );
  // ...and there is no button at all when the census recorded no ring (an
  // uploaded .osm, a bbox fetch). Nothing to edit, so nothing is offered.
  pin.match(
    page,
    /\{requestedPolygon && \(\s*\n\s*<RegionEditInvitation/,
    "expected the invitation to render only once a real recorded ring is in hand",
  );

  // 3. IT DERIVES NOTHING AND FETCHES NOTHING. The region page's own extract
  //    button stays the ask-first gate, exactly as it was under unit #40.
  const invitation = sliceBetween(page, "function RegionEditInvitation(", "\n/**\n * ONE finding's disposition");
  pin.match(invitation, /\S/, "expected to find the invitation component (markers moved?)");
  pin.doesNotMatch(
    invitation,
    /startRegionFetch|startGenesisJob|applyGenesisFix|regenerateGenesisJob/,
    "expected the button to hand a region across and start nothing -- that page's extract button is the ask-first gate",
  );
  pin.match(
    invitation,
    /kind: PREFILL_KIND_MANUAL_REGION_EDIT,/,
    "expected the hand-off to name itself, so a stale widened payload cannot be read back as this job's own ring",
  );
  pin.match(
    invitation,
    /workspace_id: getWorkspaceId\(\),/,
    "expected the 프로젝트 stamp to travel with the region",
  );

  // 4. THE HINTS ARE THE BACKEND'S MEASUREMENT, gated on its own status word --
  //    this page is structurally unable to decide whose fault a boundary finding
  //    is, and a hint that guessed would send a reader to drag the wrong edge.
  const hints = sliceBetween(page, "function boundaryHintsOf(", "\n/** UNIT #48-b -- the ONE action");
  pin.match(hints, /\S/, "expected to find the hint builder (markers moved?)");
  pin.match(
    hints,
    /orphan\.status === "boundary_attributed"/,
    "expected an orphan hint to be gated on the backend's own attribution status",
  );
  pin.match(
    hints,
    /attributed\.status === "boundary_attributed"/,
    "expected a runway-shortage hint to be gated on the backend's own attribution status",
  );
  pin.match(
    hints,
    /typeof attributed\.deficit_m === "number"/,
    "expected a finding whose measurement is missing to be SKIPPED -- a printed 0 m is a measurement nobody took",
  );
  pin.doesNotMatch(
    withoutComments(hints),
    /\?\? 0/,
    "no hint may fall back to a fabricated zero",
  );
});

// ---------------------------------------------------------------------------
// Unit #50 — the calibration workspace: one screen where the whole flow is
// visible, in MRWA §2.12.1's own order, and NOTHING is shown as finished that
// no artifact says is finished.
//
// The page-cleanup half of this unit (the user's 2026-08-19 decision) is pinned
// in the nav tests above: ③ is 대시보드 + 보정, /base-model has no row and
// redirects, /health moved to ①.
// ---------------------------------------------------------------------------

contractTest("unit #50: the flow rail renders the guideline's thirteen steps and states nothing of its own", (pin) => {
  const rail = read("components/calibration/FlowRail.tsx");
  // RETARGETED by #50-b (2026-08-20), meaning unchanged. The rail became the
  // permanent left NAVIGATION and the per-step BASIS sentence moved to the
  // station panel with the evidence that produced it, so the two pins about
  // what the server (not the client) says now read the page. Both still assert
  // the same thing: the sentence and the manual-area naming are the backend's.
  const page = read("app/calibration/page.tsx");

  // The statuses are the SERVER's vocabulary. A word invented on the client
  // would be a status no backend rule can produce, and therefore a claim
  // nothing measured.
  //
  // WIDENED BY #53 (2026-08-23) from five words to six. CONFIRMED_BY_PERSON is
  // `services/calibration_rail.CONFIRMED_BY_PERSON`, added because nine of the
  // thirteen steps have no artifact at all and had no way to say a named person
  // had done the work. It is pinned in the SAME list as the other five, on
  // purpose: what makes it safe is that it arrives on the wire like every other
  // status, and the day it can be produced client-side is the day this pin
  // stops meaning anything. The backend pins the same set from its own side
  // (tests/test_calibration_rail.test_the_status_vocabulary_has_exactly_six_words),
  // and the union type it is spelled into lives in lib/api.ts.
  pin.match(
    rail,
    /const STATUS_LABELS: Record<CalibrationStepStatus, \[string, string, string\]>/,
    "expected the six statuses to be labelled from the wire type, not from a client-side list",
  );
  for (const status of [
    "DONE",
    "CONFIRMED_BY_PERSON",
    "IN_PROGRESS",
    "NEXT",
    "NOT_APPLICABLE",
    "NO_EVIDENCE",
  ]) {
    pin.match(rail, literalPattern(`${status}:`), `expected the rail to label the ${status} status`);
    pin.match(
      read("lib/api.ts"),
      literalPattern(`| "${status}"`),
      `expected ${status} to be a member of the CalibrationStepStatus wire union`,
    );
  }

  // The step's own basis sentence is the backend's, picked by language. It is
  // the one sentence #50-c left ON the surface of a step box, and deliberately:
  // it is the justification of the status chip beside it, which is what "no DONE
  // from silence" is made of. The `source · citation` line under it moved into
  // that step's fold -- see the #50-c citation ledger test.
  pin.match(page, /pick\(step\.basis, lang\)/, "expected the station to print the server's basis sentence, never its own");
  // RE-AIMED by #50-c (2026-08-20): §2.12.1's citation was a mono line under the
  // rail's heading and is now the first line INSIDE the fold that already
  // carried this guideline's ordering principles. Same claim -- the rail cites
  // the guideline it renders -- pinned where the citation now lives, so a sweep
  // that dropped it on the way into the fold fails here.
  pin.match(
    rail.slice(rail.indexOf("<details")),
    /\{rail\?\.citation\}/,
    "expected the rail to carry §2.12.1's citation, inside its own fold",
  );
  pin.match(rail, /rail\.principles\.map/, "expected the guideline's ordering principles to be shown, not just the order");

  // NO_EVIDENCE must read as a real state a human acts on, not as a disabled
  // row -- that is the whole honesty claim of this screen.
  //
  // RE-AIMED TWICE. #50-c turned the per-step caption into a reachable ⓘ; #50-d
  // removed that ⓘ (the user, 2026-08-20: doctrine leaves the screen), which
  // would have left this pin describing six identical tooltips that no longer
  // exist. The CLAIM did not move: a manual-check step is still named, and now
  // by something always visible rather than by a hover. Every `manual_area` step
  // is routed into the 수동 확인 영역 station by the SERVER's flag
  // (lib/calibrationStations.ts, pinned in the #50-b block), that station's
  // header names it, and its one-line verdict states the promise. So the pin is
  // aimed at the naming that survives, and at the ⓘ being gone rather than
  // duplicated somewhere else on the page.
  pin.match(
    withoutComments(page),
    /activeStation === "manual" && \(/,
    "expected the manual-check steps to have a station of their own that names them",
  );
  pin.match(
    withoutFolds(withoutComments(page)),
    /이 제품은 위 단계들을 측정하지 않습니다 — 완료로 표시하지 않습니다/,
    "expected that station's one-line verdict to stay on the SURFACE -- it is the promise the whole screen rests on, not doctrine",
  );
  pin.doesNotMatch(
    withoutComments(page),
    /step\.manual_area && \(\s*\n\s*<InfoTip/,
    "expected the per-step doctrine tooltip to be gone (#50-d), not moved to a second place on the same screen",
  );
  // TIGHTENED 2026-08-20 (#50-d). This pin read RAW source and FlowRail.tsx
  // carries "수동 확인 영역" four times in its doc comments, so the pin was
  // satisfied by prose and could not fail: deleting every rendered occurrence
  // would have left it green. That is the failure `withoutComments` exists for,
  // in the direction the /base-model redirect test carries its own scar about.
  //
  // Aimed at code now, in the two places the wording actually lives. The LABEL
  // itself is not FlowRail's to write -- it belongs to the shared station table
  // that also routes the steps -- and the rail renders it through that table, so
  // pinning only the rail would be pinning the wrong file. The one place the
  // rail writes the words itself is the collapsed row's own control.
  pin.match(
    withoutComments(read("lib/calibrationStations.ts")),
    /manual: \["수동 확인 영역", "Manual-check areas", "手動確認領域"\]/,
    "expected the manual-check station's own label to exist in the shared station table, in all three languages",
  );
  pin.match(
    withoutComments(rail),
    /\{stationLabel\(station, t\)\}/,
    "expected the rail to render that label rather than writing a second copy of it",
  );
  pin.match(
    withoutComments(rail),
    /t\("수동 확인 영역 접기"|t\("수동 확인 영역 펼치기"/,
    "expected the collapsed row's own control to name the group it opens -- the chevron has no other name",
  );

  // The rail is a control too: clicking a step goes to the station whose
  // evidence produced it.
  pin.match(rail, /onSelectStep\(step\)/, "expected a step to be clickable through to its work area");
});

contractTest("unit #50: the rail's step status is never computed on the client", (pin) => {
  const rail = read("components/calibration/FlowRail.tsx");
  const page = read("app/calibration/page.tsx");
  const source = withoutComments(`${rail}\n${page}`);

  // The one rule the whole screen rests on. Any client-side derivation of a
  // step status would be a second opinion about calibration state, competing
  // with services/calibration_rail.py -- and the two would eventually disagree
  // in front of a client.
  pin.doesNotMatch(
    source,
    /status: "DONE"/,
    "expected no client-side assignment of DONE -- statuses come from /api/calibration/rail",
  );
  // #53's word is the one most tempting to compute here: the browser POSTS the
  // signature, so it KNOWS a step was just confirmed and could paint the chip
  // without waiting. It must not -- the server decides whether that signature
  // upgrades anything (machine evidence outranks it, a stale one upgrades
  // nothing), so an optimistic chip would be a status no rule produced. The
  // POST response carries no status either; the screen re-reads the rail.
  pin.doesNotMatch(
    source,
    /status: "CONFIRMED_BY_PERSON"|status = "CONFIRMED_BY_PERSON"/,
    "expected no client-side assignment of CONFIRMED_BY_PERSON -- a signature's effect on a step is the rail's answer, re-read after the POST",
  );
  pin.doesNotMatch(
    source,
    /threshold = 5|geh < 5\b/,
    "expected no hard-coded GEH threshold -- the report carries its own",
  );
});

contractTest("unit #50: the workspace reads through the shared API bindings, never a bare fetch", (pin) => {
  const page = read("app/calibration/page.tsx");
  // #50-b renamed ProposalRail.tsx -> StationProposals.tsx when the collection
  // panel became per-station cards. Same module, same reads, same pins.
  const proposals = read("components/calibration/StationProposals.tsx");
  const api = read("lib/api.ts");

  for (const binding of [
    "fetchCalibrationRail",
    "fetchCalibrationRealityCheck",
    "fetchCalibrationAppliedRows",
    "fetchCalibrationPendingApprovals",
  ]) {
    pin.match(api, literalPattern(`export async function ${binding}(`), `expected a shared binding for ${binding}`);
  }

  // A bare fetch would skip withWorkspaceHeader and silently read the DEFAULT
  // project's calibration state -- the same hole the genesis section is pinned
  // against.
  pin.doesNotMatch(
    withoutComments(`${page}\n${proposals}`),
    /\n\s*(const|await) [a-zA-Z]+ = await fetch\(/,
    "calibration calls must not bypass the workspace-header fetch helpers",
  );
});

contractTest("unit #50: the approval card is shared with the drawer, never reimplemented", (pin) => {
  const cards = read("components/agent/ApprovalCards.tsx");
  const drawer = read("components/FloatingAssistant.tsx");
  const proposals = read("components/calibration/StationProposals.tsx");

  // The MOVE: the drawer no longer defines these, it imports them.
  pin.match(cards, /export function AgentApprovalCard\(/, "expected the approval card to live in the shared module");
  pin.match(cards, /export function CarFollowingSetPanel\(/, "expected the Table 5-6 set panel to be shared too");
  pin.match(cards, /export function AgentDiagnosisLadder\(/, "expected the #43 diagnosis ladder to be shared");
  pin.doesNotMatch(
    withoutComments(drawer),
    /function AgentApprovalCard\(/,
    "expected the drawer to import the approval card rather than declare a second one",
  );
  pin.match(
    drawer,
    /from "@\/components\/agent\/ApprovalCards"/,
    "expected the drawer to import the shared cards",
  );

  // The calibration page renders the SAME card, and the decision still goes to
  // the one endpoint that owns approvals.
  pin.match(proposals, /<AgentApprovalCard/, "expected the station's proposals to render the shared card");
  pin.match(proposals, /approveAgentAction\(/, "expected the station's proposals to approve through the existing endpoint");
  pin.doesNotMatch(
    withoutComments(proposals),
    /\/api\/calibration\/[a-z-]*approve/,
    "expected NO second approval endpoint -- #50 is a read-only surface",
  );
});

contractTest("unit #50: the scoring station scores the RUN, and says what it will not use", (pin) => {
  const table = read("components/calibration/NodeScoreTable.tsx");
  // app/calibration/page.tsx dropped out of this test in #50-d: the one claim it
  // carried here -- the workbook-column rule -- is now pinned in its two new
  // homes at the bottom of this test, and in the "#50-d ③" eviction ledger.

  // Pass/fail is the report's own threshold, and an undefined GEH is neither.
  pin.match(table, /report\.geh_threshold/, "expected the threshold to come from the report");
  pin.match(table, /passes === null/, "expected an undefined GEH to be neither pass nor fail");
  pin.match(table, /border-emerald-200 bg-emerald-50/, "expected a passing node to read emerald");
  pin.match(table, /border-rose-200 bg-rose-50/, "expected a failing node to read rose");

  // #43's rung, next action and its MRWA step -- the three things a modeller
  // acts on -- and the raw codes behind a fold (#41's doctrine).
  //
  // RE-AIMED by #50-c (2026-08-20). The rung now rides the FAIL tile itself
  // (`rungLabel(node.diagnosis.rung, t)` beside a "→"), which is a stronger
  // claim than the old row chip: it is on the tile a reader is already looking
  // at when they learn the node failed. The MRWA step and its citation moved
  // into the node detail's fold with the rest of the provenance, so that pin is
  // aimed there -- both are proved by the #50-c ledger test as well, which
  // checks the fold is where they render and NOT the surface.
  pin.match(table, /node\.diagnosis\.rung/, "expected the diagnosis rung on the node tile");
  pin.match(
    table.slice(table.indexOf("<details", table.indexOf("export function NodeScoreDetail"))),
    /nextAction\.mrwa_step/,
    "expected the next action to name its MRWA step, inside the node's own fold",
  );
  pin.match(table, /<details/, "expected the raw evidence to sit behind a fold");

  // A refusal is an ANSWER: the report's own tri-lingual sentence, not ours.
  pin.match(table, /report\.status !== "CHECKED"/, "expected a non-CHECKED report to render its own refusal sentence");
  pin.match(table, /pick\(report\.message, lang\)/, "expected the refusal wording to be the backend's");

  // The workbook's pasted model column is the thing reality_check refuses to
  // read, and the product still has to SAY so somewhere a reader can find it.
  //
  // RE-AIMED TWICE. #50-c moved the sentence into the scoring tab's own fold;
  // #50-d evicted it from the screen altogether (the user, 2026-08-20 — a rule
  // about the product, not a fact about this run). The claim is unchanged and is
  // now pinned where the sentence lives: verbatim in the knowledge file, and
  // verbatim in the module /docs renders. The eviction ledger below
  // ("#50-d ③") is what stops that from being satisfiable by a deletion --
  // it asserts BOTH the two homes and the absence from every surface file.
  pin.match(
    readCalibrationDoctrine(),
    /워크북에 붙여넣은 모델 열/,
    "expected the knowledge file to state that the workbook's model column is not used",
  );
  pin.match(
    read("lib/help-content.tsx"),
    /워크북에 붙여넣은 모델 열/,
    "expected /docs to render that same sentence, from the shared doctrine module",
  );
});

contractTest("unit #50: the Table 5-6 station cannot mistake the Vissim default for a calibration", (pin) => {
  const station = read("components/calibration/BehaviourStation.tsx");

  // The trap: stock Wiedemann-74 sits ON a published row (2050 pcu), so a row
  // match alone is true for a network nobody touched. The standstill distance
  // is what separates them, and it gets its own column and its own sentence.
  pin.match(station, /standstill_matches_guideline/, "expected the standstill verdict to be shown per behaviour");
  pin.match(
    station,
    /summary\.default_coincident > 0 && summary\.guideline_standstill === 0/,
    "expected the default-coincidence case to be called out explicitly",
  );
  // RE-AIMED by #50-d (2026-08-20). The three-sentence explanation of WHY a
  // default match is not evidence is doctrine and left for /docs; what a reader
  // must not lose is the WARN itself -- the count, and the refusal to call it
  // evidence. So the pin now asks for the short verdict line, ON THE SURFACE,
  // in the branch that renders it. Dropping the warn entirely would let a
  // network nobody calibrated read as one that matches the published table,
  // which is the single defect this component was built around.
  pin.match(
    withoutFolds(withoutComments(station)),
    /기본값 우연 일치 \$\{summary\.default_coincident\}건[\s\S]{0,80}?측정 증거 아님/,
    "expected the default-coincidence WARN to keep its count and its refusal on the surface",
  );
  pin.match(
    readCalibrationDoctrine(),
    /이 일치는 측정이 제출됐다는 증거가 아닙니다/,
    "expected the full explanation to be in the knowledge file -- the sentence moved, it was not dropped",
  );

  // #44's own `why` sentence, quoted -- it is the one that names a partially
  // applied set, and the UI must not paraphrase it.
  pin.match(station, /\{verdict\.why\}/, "expected verify_applied_row's own sentence, verbatim");
  // RE-AIMED by #50-c (2026-08-20): Table 5-6's citation closed a mono summary
  // line that the user ordered off the surface, and now closes the station's own
  // fold. "Travels with its numbers" still holds -- it is one click from them,
  // in the same component -- and this pin is aimed at the fold so a sweep that
  // deleted it instead of moving it fails here.
  pin.match(
    station.slice(station.indexOf("<details")),
    /\{rows\.citation\}/,
    "expected Table 5-6's citation to travel with its numbers, inside the station's fold",
  );
});

contractTest("unit #50: a bookmarked /base-model still lands on the work", (pin) => {
  // COMMENT-STRIPPED, and this one is not a precaution. Measured 2026-08-19
  // (refuter, by mutation): `next.config.ts` EXPLAINS itself with the sentence
  // "`permanent: true` (308) because this is not a trial", so a raw-source
  // `/permanent: true/` pin was satisfied by the prose. Flipping the actual
  // redirect to `permanent: false` -- a 307, i.e. the exact "this is a trial"
  // the comment denies -- left this test green. That is the failure mode
  // `withoutComments` exists for, in its other direction: a `match` pin a
  // comment answers for cannot fail, and the suite reports it as passing.
  const config = withoutComments(read("next.config.ts"));

  pin.match(config, /async redirects\(\)/, "expected a redirects() entry so the retired route is not a 404");
  pin.match(config, /source: "\/base-model"/, "expected /base-model to be the redirect source");
  pin.match(config, /destination: "\/calibration"/, "expected it to land on the calibration workspace");
  pin.match(config, /permanent: true/, "expected a permanent (308) redirect -- the page is not coming back");

  // The screen itself moved, it was not rewritten: every feature that had pins
  // on it (the workbook, the profiles, the travel times, the criteria card)
  // still lives in one file.
  const station = read("components/calibration/BaseModelStation.tsx");
  pin.match(station, /export default function BaseModelStation\(/, "expected the absorbed screen to keep one entry point");
  pin.match(
    read("app/calibration/page.tsx"),
    /<BaseModelStation \/>/,
    "expected the calibration workspace to render the absorbed screen rather than a partial copy of it",
  );
});

contractTest("unit #50: every new calibration string carries its own Japanese", (pin) => {
  // The newer pages are pinned to the 3-argument t(ko, en, ja) form because
  // lib/i18n-ja.ts has no generator -- a 2-argument call on a new string would
  // silently fall back to English for ja readers.
  for (const file of [
    "app/calibration/page.tsx",
    "components/calibration/FlowRail.tsx",
    "components/calibration/NodeScoreTable.tsx",
    "components/calibration/BehaviourStation.tsx",
    "components/calibration/StationProposals.tsx",
    "components/calibration/ParameterSearchStart.tsx",
  ]) {
    pin.doesNotMatch(
      read(file),
      /\bt\(\s*"[^"]*"\s*,\s*"[^"]*"\s*,?\s*\)/,
      `a two-argument t() in ${file} would fall back to English for ja`,
    );
  }
});

// ---------------------------------------------------------------------------
// Unit #51 — the range-bounded parameter search's step-⑫ track.
//
// The client's whole job here is to render two server-decided states and add
// nothing. The pins below exist because the two most tempting client-side
// "helpfulnesses" would each be a lie: deciding the lock locally, and offering
// a start button for a runner that is not wired.
// ---------------------------------------------------------------------------

contractTest("unit #51: the search track renders the server's lock, and never decides it", (pin) => {
  const rail = read("components/calibration/FlowRail.tsx");

  pin.match(
    rail,
    /export function ParameterSearchTrack\(/,
    "expected the #51 track to be one exported component, so the rail and the behaviour station share it",
  );
  // The lock is the SERVER's. A client that recomputed it would be a second
  // opinion about whether a search may start -- the same failure #50 pinned
  // against for step statuses.
  pin.match(rail, /track\.locked \|\| !card/, "expected the lock to come from the server's own field");
  pin.doesNotMatch(
    withoutComments(rail),
    /exhaust|preconditions|geh\s*[<>]=?\s*\d/i,
    "expected no client-side precondition logic -- the gate lives in parameter_search.py",
  );

  // A locked track must NAME what would open it. "Locked" alone teaches nothing,
  // and the server already sends the ladder-grammar refusals.
  //
  // MUTATION-CHECKED (2026-08-19): pinning only `track.refusals` was NOT enough —
  // wrapping the block in `{false && (...)}` left both the field reference and
  // the `pick(...)` call in the source and the pin still passed. So the pin is on
  // the RENDER — the mapped element and the binding it comes from — which a dead
  // branch cannot satisfy.
  pin.match(
    rail,
    /const refusals = track\.refusals \?\? \[\];/,
    "expected the refusal list to be bound from the server payload",
  );
  pin.match(
    rail,
    /\{refusals\.map\(\(refusal\) => \(/,
    "expected every named refusal to be rendered, unconditionally -- not behind a branch that can be turned off",
  );
  pin.match(
    rail,
    /pick\(refusal\.opens_when, lang\)/,
    "expected each refusal to show what would open it",
  );
});

contractTest("unit #51: the trigger card asks, and offers no button it cannot honour", (pin) => {
  const rail = read("components/calibration/FlowRail.tsx");
  const track = topLevelFunction(withoutComments(rail), "ParameterSearchTrack");

  // ask-first (the user's decision (2)): a question, never an auto-start. The
  // card asks; the control (when the runner is wired) is the ANSWER to it.
  pin.match(rail, /pick\(card\.question, lang\)/, "expected the ask-first question to be rendered");
  pin.match(rail, /pick\(card\.because, lang\)/, "expected the card to show WHY the ladder is exhausted");

  // RE-SCOPED 2026-08-21 (#51's live runner). Until then this pinned "no start
  // control anywhere", because `card.runner.wired` was a constant False and a
  // button would have promised something no code could do. The flag is now a
  // MEASUREMENT of the backend's machine, so the honest pin is no longer "no
  // button" -- it is "no button REACHABLE FROM ANYWHERE BUT the wired branch".
  //
  // That is enforced structurally rather than by a proximity regex (the old
  // `<button ...>[\s\S]{0,200}?card\.` would have been satisfied by moving the
  // markup 201 characters away): the control lives in its own component, and
  // `ParameterSearchTrack` -- BOTH branches of it, locked and open -- contains
  // no `<button>` at all.
  pin.doesNotMatch(
    track,
    /<button/,
    "expected NO button inside ParameterSearchTrack -- the start control is its own component, " +
      "so a button in the locked branch or the unwired branch cannot hide here",
  );
  // MUTATION-CHECKED (2026-08-21): without the leading `{` this pin passed on
  // `{!card.runner.wired ? (<ParameterSearchStart` -- the negation lives to the
  // LEFT of the field name, so a pin that starts at `card.` reads the inverted
  // branch as the true one and the control ends up on exactly the branch it must
  // never be on. The brace is what makes the expression whole.
  pin.match(
    rail,
    /\{card\.runner\.wired \? \(\s*<ParameterSearchStart/,
    "expected the start control to sit on the TRUE branch of the server's wired flag and nowhere else",
  );
  pin.doesNotMatch(
    withoutComments(rail),
    /<ParameterSearchStart[\s\S]*<ParameterSearchStart/,
    "expected exactly ONE render site for the start control -- a second one is a second lock state",
  );

  // The honest half is rendered, not tucked behind a disclosure -- and not
  // behind an `&&` a later edit could flip to `false` while leaving the field
  // reference in place (the mutation that got past the first cut of the refusal
  // pin above). A ternary always renders one of the two branches.
  pin.match(
    rail,
    /card\.runner\.wired\s*\?/,
    "expected the runner's real state to be stated in both directions, never conditionally hidden",
  );
  pin.match(
    rail,
    /No runner is wired in this build/,
    "expected the unwired-runner sentence to say so plainly",
  );

  // The published band and its page travel together. A range with no citation
  // would be this product asserting bounds of its own -- and a citation with no
  // range is a page cited for nothing.
  //
  // MUTATION-CHECKED (2026-08-19, refuter): pinning only the citation was NOT
  // enough. Replacing `card.plan.space.map(` with `card.plan.space.slice(0, 0)
  // .map(` emptied the band line and left `op §5.2.5.1, p.188` standing alone,
  // and every pin still passed. So the band RENDER is pinned beside it.
  pin.match(
    rail,
    /card\.plan\.space\[0\]\.citation/,
    "expected the MRWA citation to be printed beside the searched bands",
  );
  pin.match(
    rail,
    /\{card\.plan\.space\.map\(\(band\) =>/,
    "expected every searched band to be rendered, unsliced -- a citation with no range cites nothing",
  );
  pin.match(
    rail,
    /band\.low\}[^\n]*\$\{band\.high\}/,
    "expected both ends of each published band to be shown",
  );
  // Both sides of the holdout are shown -- a split a reader cannot see is a
  // split they cannot check.
  pin.match(rail, /card\.plan\.holdout_nodes/, "expected the held-out nodes to be named on the card");
  pin.match(
    rail,
    /card\.plan\.calibration_nodes/,
    "expected the calibration nodes to be named beside the held-out ones",
  );
});

contractTest("unit #51: the behaviour station shows the rail's track, not a second copy", (pin) => {
  const page = read("app/calibration/page.tsx");

  // #50-b widened the import (the station panel now renders the rail's own
  // chips and labels too), so the pin matches the binding rather than the exact
  // one-line form -- it still says: ONE ParameterSearchTrack, imported.
  pin.match(
    page,
    /import FlowRail, \{[\s\S]{0,200}?\bParameterSearchTrack,/,
    "expected the page to import the shared track component",
  );
  pin.match(
    page,
    /rail\?\.steps\.find\(\(step\) => step\.number === 12\)\?\.tracks\?\.\[0\]/,
    "expected the track to be read off the rail payload the page already has",
  );
  // ...and read off it ONLY. MUTATION-CHECKED (2026-08-19, refuter): appending
  // `?? ({ ... locked: false ... } as CalibrationRailTrack)` to that very
  // expression passed every pin here, which is precisely the hand-written
  // second copy of the lock state the next pin refuses. So the binding is
  // pinned WHOLE, ending at `?? null`.
  pin.match(
    page,
    /\?\.tracks\?\.\[0\] \?\? null;/,
    "expected the track binding to end at `?? null` -- an absent rail is nothing to say, never a locally invented track",
  );
  // The pre-#51 placeholder claimed the unit was not built. Now that it is, that
  // sentence would be false -- and a hand-written second copy of the lock state
  // is how the page and the rail would come to disagree on one screen.
  pin.doesNotMatch(
    page,
    /Not built yet|아직 만들어지지 않았습니다/,
    "expected no 'not built yet' placeholder -- #51 shipped, and the track states its real state",
  );
});

// ---------------------------------------------------------------------------
// Unit #51's LIVE RUNNER (2026-08-21) — the ⑫ track finally has an action.
//
// The whole risk profile of this control is that it looks like a normal button
// and is not: clicking it occupies the machine's only Vissim for its entire
// budget, and what comes back has to be believable enough to authorise a write
// to a shared driving behaviour. So the pins below are about the two ways the
// client could quietly take over: deciding a search's STATE, and deciding
// whether a search MAY start.
// ---------------------------------------------------------------------------

contractTest("unit #51 runner: the start control renders the server and decides nothing", (pin) => {
  const start = read("components/calibration/ParameterSearchStart.tsx");
  const source = withoutComments(start);

  // It really calls the API, and the API really POSTs the one route.
  pin.match(start, /startParameterSearch\(\{/, "expected the control to call the start endpoint");
  pin.match(
    read("lib/api.ts"),
    /export async function startParameterSearch\(/,
    "expected a client for the start route in lib/api.ts",
  );
  pin.match(
    read("lib/api.ts"),
    /apiMutationFetch\("\/api\/calibration\/parameter-search\/start", \{\s*method: "POST"/,
    "expected the start call to be a POST to the calibration router's one control",
  );

  // THE COST IS ON THE BUTTON. A search runs Vissim once per candidate for its
  // whole budget; a label that said only "시작" would be asking for a click
  // without saying what it spends.
  pin.match(
    start,
    /탐색 시작 \(예산 \$\{budgetMinutes\}분, Vissim 실행\)/,
    "expected the button to name the budget and that it runs Vissim, in Korean",
  );
  pin.match(
    start,
    /Start the search \(\$\{budgetMinutes\} min budget, runs Vissim\)/,
    "expected the same cost statement in English",
  );
  pin.match(
    start,
    /探索開始 \(予算\$\{budgetMinutes\}分、Vissimを実行\)/,
    "expected the same cost statement in Japanese",
  );
  pin.match(
    start,
    /card\.plan\.budget_s/,
    "expected the budget on the button to be the SERVER's number, not a client default",
  );

  // The job's words are the server's, all of them.
  pin.match(start, /pick\(job\.headline, lang\)/, "expected the job's sentence to come from the server");
  pin.match(start, /pick\(job\.status_text, lang\)/, "expected the job's status word to come from the server");
  pin.doesNotMatch(
    source,
    /=== "RUNNING"|=== "COMPLETE"|=== "PARTIAL"|=== "FAILED"|=== "QUEUED"/,
    "expected NO client-side status vocabulary -- `job.live` is a server field for exactly this reason",
  );
  pin.match(
    start,
    /if \(payload\.live\) \{/,
    "expected polling to continue on the server's own `live` field",
  );

  // ...and it decides nothing about whether a search may start.
  pin.doesNotMatch(
    source,
    /exhaust|preconditions|geh\s*[<>]=?\s*\d/i,
    "expected no client-side precondition logic -- the gate lives in search_job.evaluate_start",
  );
  pin.match(
    start,
    /outcome\.refusals\.map\(\(refusal\) => \(/,
    "expected every named start refusal to be rendered, unconditionally",
  );
  pin.match(
    start,
    /pick\(refusal\.opens_when, lang\)/,
    "expected a start refusal to say what would open it, like every other refusal on this screen",
  );

  // The behaviour choice is the modeller's, from the server's list. A client
  // that defaulted to `behaviours[0]` would be making the choice the backend
  // refuses to make (BEHAVIOUR_NOT_CHOSEN) and every candidate run would
  // inherit it.
  pin.match(
    start,
    /track\.default_behavior_no/,
    "expected the preselection to be the server's, offered only when there is nothing to choose",
  );
  pin.doesNotMatch(
    source,
    /behaviours\[0\]\.no(?!\s*:\s*"")/,
    "expected no client-side fallback to the first behaviour",
  );
});

contractTest("unit #51 runner: the calibration components stay inside the dark remap", (pin) => {
  // THE RIDER (2026-08-21). #51's start control arrived carrying
  // `border-sky-300` -- the fall-through the genesis screens were already swept
  // for -- and the sweep took FlowRail's five pre-existing occurrences with it,
  // all of them hairlines around a tint and none of them a fill.
  //
  // The pattern is the genesis pin's own (UNMAPPED_SKY_BORDER), not a second
  // copy: a future edit cannot loosen it for one surface without loosening it
  // for both.
  const files: [string, string][] = [
    ["components/calibration/FlowRail.tsx", read("components/calibration/FlowRail.tsx")],
    [
      "components/calibration/ParameterSearchStart.tsx",
      read("components/calibration/ParameterSearchStart.tsx"),
    ],
    ["components/calibration/BehaviourStation.tsx", read("components/calibration/BehaviourStation.tsx")],
    ["components/calibration/StationProposals.tsx", read("components/calibration/StationProposals.tsx")],
    ["components/calibration/NodeScoreTable.tsx", read("components/calibration/NodeScoreTable.tsx")],
  ];

  for (const [name, source] of files) {
    pin.doesNotMatch(
      source,
      UNMAPPED_SKY_BORDER,
      `${name}: sky-300 is outside the dark remap and renders as stock #7dd3fc -- use border-sky-200`,
    );
  }

  // Fails in BOTH directions: a sweep that DELETED the sky borders instead of
  // remapping them trips this, because the selection/proposal tint is the
  // three-colour rule's own vocabulary and must survive.
  pin.match(
    files[0][1],
    /"border-sky-200 bg-sky-50 text-sky-600"/,
    "expected the rail's NEXT chip to keep its sky tint with a remapped border",
  );
  pin.match(
    files[1][1],
    /border-l border-sky-200/,
    "expected the start control's left rule to be a remapped sky hairline",
  );

  // NOT IN THIS LIST, and said out loud rather than quietly omitted:
  // `app/calibration/page.tsx` still carries `border-sky-300` at five places
  // behind an explicit note (its own lines ~609-616) claiming stock #7dd3fc
  // "reads as a light hairline on dark, which is the selected look". That
  // contradicts the genesis rule this pin enforces, so ONE of the two is wrong
  // and it is not a builder's call to settle by sweeping a file the refuter did
  // not name. The disagreement is recorded here so the next reader finds it
  // instead of discovering that this pin has a silent hole.
  pin.match(
    read("app/calibration/page.tsx"),
    /The border stays sky-300 on purpose/,
    "expected the page's deliberate exception to still STATE itself -- if that note is gone, " +
      "the exception is gone with it and the page belongs in the list above",
  );
});

contractTest("unit #51 runner: the session the winner lands in is the drawer's own", (pin) => {
  const page = read("app/calibration/page.tsx");

  // MEASURED: the only agent session id a browser holds is the drawer's, and
  // FloatingAssistant already broadcasts it. Three pages mirror it exactly this
  // way; this is the fourth, not a fourth MECHANISM.
  pin.match(
    page,
    /window\.addEventListener\("vissim:agent-session", handleAgentSession\)/,
    "expected the calibration page to mirror the drawer's session id from the existing broadcast",
  );
  pin.match(
    page,
    /sessionId=\{agentSessionId\}/,
    "expected the session id to be threaded into the ⑫ track",
  );
  pin.match(
    page,
    /scenarioId=\{scenarioId\}/,
    "expected the START to name the same run the rail's gate was derived from",
  );
  // No invented session. A client that minted one would be authorising a write
  // against a conversation nobody had.
  pin.doesNotMatch(
    withoutComments(read("components/calibration/ParameterSearchStart.tsx")),
    /crypto\.randomUUID|Math\.random|sessionId \?\?\s*"/,
    "expected the control to pass the session through untouched -- never to invent or default one",
  );
});

// ---------------------------------------------------------------------------
// Unit #51-c — the searched winner reaches the approval machinery, and the
// user's decision of 2026-08-20 reaches the screen.
//
// Three things could go wrong here and each has its own test below, because
// each fails for a different reason:
//
//   * the WIRING could persist more than a reference (the #47 property, now for
//     a second grant species);
//   * the FORECAST could keep saying one thing where there are now two, which
//     since #51-b is misleading in whichever direction it picks;
//   * the EVIDENCE RANK could end up behind a disclosure, which is the one
//     thing DECISIONS.md 2026-08-20 asked for by name.
// ---------------------------------------------------------------------------

contractTest("unit #51-c: a searched winner reaches the card, and the file only NAMES it", (pin) => {
  const assistantRouter = readFileSync(
    path.join(repoRoot, "backend", "routers", "assistant.py"),
    "utf-8",
  );
  const searchedBroker = readFileSync(
    path.join(repoRoot, "backend", "services", "agent", "searched_behaviour_broker.py"),
    "utf-8",
  );
  const api = read("lib/api.ts");
  const approvalSurface = readApprovalSurface();

  // The turn publishes the session BOTH brokers are keyed by, and clears it.
  pin.match(
    assistantRouter,
    /searched_behaviour_broker\.set_active_session\(session_id\)/,
    "expected the turn to publish its session to the searched broker, beside the car-following one",
  );
  pin.match(
    assistantRouter,
    /searched_behaviour_broker\.set_active_session\(None\)/,
    "expected the pointer to be cleared when the turn ends",
  );
  // A REFERENCE, never a payload -- #47's property, for the second species.
  pin.match(
    assistantRouter,
    /pending_file_payload\["searched_search_id"\] = searched_search_id/,
    "expected only an opaque search id to be persisted beside the signatures",
  );
  pin.doesNotMatch(
    assistantRouter,
    /pending_file_payload\["searched_(behaviour_)?grant"\]|pending_file_payload\["consent_radius"\]/,
    "the pending-approval file must never carry a grant or the radius it was minted against",
  );
  // Which search names THIS write is the BROKER's answer, by canonical
  // signature -- never a tool-name sniff, which is how "the router thinks this
  // belongs to the search" and "the grant covers it" would come to disagree.
  pin.match(
    assistantRouter,
    /searched_search_id = searched_behaviour_broker\.search_id_covering\(\s*\n\s*action_entry, session_id=session_id\s*\n\s*\)/,
    "expected the router to ask the broker which search covers this action",
  );
  // Both species on one gate, and the approve leg PINNED to what the file named.
  pin.match(
    assistantRouter,
    /gate = ProposeWriteGate\(\s*\n\s*grant_provider=chain_grant_providers\([\s\S]{0,600}?searched_behaviour_broker\.grant_provider\(/,
    "expected the proposing gate to ask both species through the chain",
  );
  pin.match(
    assistantRouter,
    /grant_provider=chain_grant_providers\([\s\S]{0,900}?searched_behaviour_broker\.grant_provider\([\s\S]{0,300}?search_id=str\(searched_search_ref\)/,
    "expected the approve gate to be pinned to the search id the file named, and re-mint it",
  );
  // A declined search stops existing -- the store is the only place it lives.
  pin.match(
    assistantRouter,
    /searched_behaviour_broker\.forget\(declined_search_id, session_id=payload\.session_id\)/,
    "expected a declined approval to forget its search",
  );
  // The broker re-mints; it never rehydrates, and it never touches disk.
  pin.match(
    searchedBroker,
    /grant = parameter_search\.grant_from_search\(\*\*record\)\s*\n\s*except \(ValueError, TypeError, AttributeError, KeyError\):\s*\n\s*return \{\*\*result, "reason": REMINT_FAILED\}/,
    "expected mint() to re-run the only minting path rather than rehydrate a payload",
  );
  pin.doesNotMatch(
    withoutComments(searchedBroker),
    /write_text\(|json\.dump|open\(/,
    "the searched broker must never write a grant, or anything one can be rebuilt from, to disk",
  );
  // ...and the wire carries the CARD, not the grant.
  pin.match(
    api,
    /searched_search\?: AgentSearchedSearch;/,
    "expected the search to ride on the approval payload as a sibling of actions/warnings/focus",
  );
  pin.match(
    approvalSurface,
    /searchedSearch: event\.searched_search/,
    "expected the approval state to carry the search through from the wire event",
  );
  pin.match(
    approvalSurface,
    /<SearchedParameterPanel search=\{approval\.searchedSearch\} action=\{approval\.actions\[0\]\} \/>/,
    "expected the approval card to render the searched panel",
  );
  pin.match(
    read("components/calibration/StationProposals.tsx"),
    /searchedSearch: pending\.searched_search \?\? undefined/,
    "expected the ⑫ station's own listing to carry it too -- one card, both surfaces",
  );
});

// ---------------------------------------------------------------------------
// REMOVED BY THE USER, 2026-08-20 (#50-d): "unit #51-c: the guardrail speaks in
// TWO paths, and the decision is on the screen".
//
// THE AUTHORITY: the roadmap row for #50-d records "2경로 예보·결정 칩·mono 코드
// 바 전부 문서/지식 파일로 … #51-c 표시 핀 사용자 권한 제거(기록)" — the user's
// own words on the section itself were "developer side information... get rid of
// it". That test pinned a DISPLAY into existence: `GuardrailPathLines`, the
// resolution chips, and both `<TrackGuardrail>` call sites on the ⑫ track. The
// display is gone by the user's decision, so its pins go with it, recorded here
// rather than deleted quietly.
//
// WHAT WAS *NOT* REMOVED WITH IT, because none of it was ever display:
//
//   * the two-path KEY contract across the two languages -- the reason that test
//     read parameter_search.py at all -- is re-pinned in the "#50-d ④" test
//     below, on the backend side alone, where it is now the only side;
//   * the backend enforcement (the shared-behaviour refusal, the re-minted
//     single-use grant) is untouched by this unit and stays pinned by the
//     backend suite and by "#51-c: a searched winner reaches the card" above;
//   * the evidence-rank pin, which is approval-decision material rather than
//     developer information, survives in the trimmed test directly below.
// ---------------------------------------------------------------------------

contractTest("unit #51-c: the evidence rank is never folded away", (pin) => {
  const cards = read("components/agent/ApprovalCards.tsx");
  const panel = cards.slice(cards.indexOf("export function SearchedParameterPanel"));
  // Everything ABOVE this panel's one disclosure. DECISIONS.md 2026-08-20 made
  // showing the rank a closing requirement of the decision, so a reader who
  // never opens the fold must still have read it -- which is exactly what a
  // slice at `<details` can check and a plain "is it in the file" pin cannot.
  const aboveTheFold = panel.slice(0, panel.indexOf("<details"));

  // TRIMMED by #50-d with the recorded authority above: this test used to also
  // require `GuardrailPathLines` and `GuardrailResolutionChips` to render here
  // unfolded. Those two are removed from the product; the evidence rank is not,
  // and it is the reason this test exists.
  pin.match(
    aboveTheFold,
    /pickText\(search\.evidence_rank, lang\)/,
    "expected the evidence-rank sentence to render ABOVE the fold, never inside the disclosure",
  );
  // Slice sanity, because `indexOf` failing open would let the pin above pass on
  // the whole panel including its fold -- the exact failure the #51-c guardrail
  // slices carried a note about.
  pin.doesNotMatch(
    aboveTheFold,
    /Search ledger/,
    "slice sanity: the above-the-fold slice must end before the disclosure begins",
  );
  // The sentence is the SERVER's, in three languages -- this client never
  // writes its own version of "this evidence ranks below Table 5-6's".
  pin.match(
    read("lib/api.ts"),
    /evidence_rank\?: \{ ko: string; en: string; ja: string \};/,
    "expected the rank sentence to arrive from the backend in all three languages",
  );
  pin.doesNotMatch(
    withoutComments(cards),
    /한 단계 낮|one rank BELOW|一段低い/,
    "the client must never compose the evidence-rank sentence itself -- it quotes the server's",
  );
});

// ---------------------------------------------------------------------------
// Unit #50-b — the workspace becomes a rail plus ONE station.
//
// The user drove #50's real screen and could not read it: every station stacked
// down one column, so the thirteen-step rail was a list to scroll past rather
// than a place to stand. #50-b folds the thirteen rows into eight station
// groups, makes the rail permanent navigation, shows one station at a time, and
// DELETES the proposal collection panel so every card renders at the step it
// acts on.
//
// That last move is the one with teeth. A collection panel could not lose a
// proposal; per-station cards can — a tool nobody mapped, a station nobody
// opened. So the placement is a plain module (lib/calibrationStations.ts) that
// this suite RUNS rather than regex-pins: the claims below are executed against
// synthetic payloads, because "no proposal is orphaned" is not a claim a
// pattern match can make.
// ---------------------------------------------------------------------------

/** §2.12.1's steps the way the rail payload describes them, with the SERVER's
 * own manual-area list read out of calibration_rail.py — so this suite fails
 * when the two drift apart, which is exactly how step ⑨ nearly ended up
 * mislabelled as a manual-check area when it left that list. */
function railStepsFromBackend(): { number: number; manual_area: boolean }[] {
  const source = readFileSync(
    path.join(repoRoot, "backend", "services", "calibration_rail.py"),
    "utf-8",
  );
  const declaration = /STEPS_WITHOUT_EVIDENCE_SOURCE = \(([^)]*)\)/.exec(source);
  assert.ok(declaration, "expected calibration_rail.py to declare STEPS_WITHOUT_EVIDENCE_SOURCE");
  const manual = (declaration[1].match(/\d+/g) ?? []).map(Number);
  assert.ok(manual.length > 0, "expected the manual-area list to name at least one step");
  return Array.from({ length: 13 }, (_, index) => ({
    number: index + 1,
    manual_area: manual.includes(index + 1),
  }));
}

contractTest("unit #50-b: thirteen steps fold into eight rail rows, and none is lost", (pin) => {
  const steps = railStepsFromBackend();
  const grouped = groupSteps(steps);

  // Nothing may fall out of the fold.
  const placedCount = CALIBRATION_STATIONS.reduce(
    (total, station) => total + grouped.byStation[station].length,
    0,
  );
  assert.equal(
    placedCount + grouped.unplaced.length,
    steps.length,
    "every one of the thirteen steps must land in exactly one rail row",
  );
  assert.deepEqual(grouped.unplaced, [], "no step of the current rail may be left without a station");

  // EIGHT rows: 채점·진단 (no step of its own) plus the seven that carry steps.
  const rowsWithSteps = CALIBRATION_STATIONS.filter(
    (station) => grouped.byStation[station].length > 0,
  );
  assert.equal(rowsWithSteps.length, 7, "expected seven step-carrying rows beside 채점·진단");
  assert.equal(CALIBRATION_STATIONS.length, 8, "expected eight rail rows in total");
  assert.equal(CALIBRATION_STATIONS[0], DEFAULT_STATION, "expected 채점·진단 to lead the rail");

  // The manual row is the SERVER's list, whole and nothing else -- six rows
  // when it opens.
  assert.deepEqual(
    grouped.byStation.manual.map((step) => step.number),
    steps.filter((step) => step.manual_area).map((step) => step.number),
    "the 수동 확인 영역 row must contain exactly the steps the server flags manual",
  );
  assert.equal(grouped.byStation.manual.length, 6, "expected six manual-check steps behind the folded row");

  // ⑨ is the case this pin exists for: it left the manual list, so it must NOT
  // be folded in with the steps this product measures nothing for.
  assert.equal(stationForStep({ number: 9, manual_area: false }), "transit");
  assert.ok(
    !grouped.byStation.manual.some((step) => step.number === 9),
    "step 9 has an evidence source and must not sit inside 수동 확인 영역",
  );

  // Every station a step can be sent to is one the rail can actually draw.
  for (const station of Object.values(STATION_BY_STEP)) {
    assert.ok(
      (CALIBRATION_STATIONS as readonly string[]).includes(station),
      `step routing names a station the rail does not have: ${station}`,
    );
  }

  // And the labels the rail and the station header share carry all three
  // languages -- the same rule as every t() on this screen.
  for (const station of CALIBRATION_STATIONS) {
    const label = STATION_LABELS[station];
    assert.equal(label.length, 3, `expected ko/en/ja for the ${station} station label`);
    for (const text of label) assert.ok(text.trim().length > 0, `empty label for ${station}`);
  }

  pin.match(
    read("lib/calibrationStations.ts"),
    /if \(step\.manual_area\) return "manual";/,
    "expected the manual row to be the SERVER's flag, never a client-side list of step numbers",
  );
});

contractTest("unit #50-b: a step with no station is NAMED, never filed under 수동 확인 영역", (pin) => {
  // The distinction the whole screen rests on: "we have not placed this step"
  // and "this product measures nothing for this step" are different claims, and
  // only the server may make the second one.
  assert.equal(
    stationForStep({ number: 2, manual_area: true }),
    "manual",
    "a server-flagged manual step belongs in 수동 확인 영역",
  );
  assert.equal(
    stationForStep({ number: 2, manual_area: false }),
    null,
    "a step with no station and no manual flag must come back null, not 'manual'",
  );

  const grouped = groupSteps([
    { number: 1, manual_area: false },
    { number: 4, manual_area: false },
  ]);
  assert.deepEqual(
    grouped.unplaced.map((step) => step.number),
    [4],
    "an unplaced step must surface in its own list",
  );
  assert.equal(grouped.byStation.manual.length, 0, "an unplaced step must not be counted as manual");

  // ...and the rail must RENDER that list rather than dropping it.
  const rail = read("components/calibration/FlowRail.tsx");
  pin.match(rail, /grouped\.unplaced\.length > 0/, "expected the rail to show unplaced steps when there are any");
  pin.match(rail, /단계 미배치/, "expected the unplaced row to be named in Korean");
});

contractTest("unit #50-b: every pending proposal reaches exactly one station -- no orphans", (pin) => {
  const steps = railStepsFromBackend();
  const resolver = stationResolverFor(steps);
  const proposals = [
    { id: "demand", actions: [{ tool: "set_vehicle_input_volume", params: {} }] },
    { id: "signals", actions: [{ tool: "set_signal_cycle", params: {} }] },
    { id: "behaviour", actions: [{ tool: "set_driving_behavior_parameter", params: {} }] },
    { id: "assignment", actions: [{ tool: "set_route_relative_flow", params: {} }] },
    { id: "transit", actions: [{ tool: "add_pt_line", params: {} }] },
    { id: "network", actions: [{ tool: "add_link", params: {} }] },
    // A manual-check step (② 차량 속도) can still receive an agent edit. Its
    // card belongs in 수동 확인 영역, not at the default station.
    { id: "manual", actions: [{ tool: "set_des_speed_decision", params: {} }] },
    // A tool with no guideline step at all.
    { id: "unmapped", actions: [{ tool: "set_random_seed", params: {} }] },
    // A batch: the real tools are inside the plan, not in its own name.
    {
      id: "batch",
      actions: [
        {
          tool: "apply_edit_plan",
          params: { steps: [{ tool: "set_signal_offset" }, { tool: "set_vehicle_input_volume" }] },
        },
      ],
    },
    // No actions at all -- a malformed payload must still be visible.
    { id: "empty", actions: [] },
  ];

  const grouped = groupProposals(proposals, resolver);

  // THE INVARIANT. Every card lands somewhere, exactly once.
  assert.equal(grouped.total, proposals.length);
  const landed = CALIBRATION_STATIONS.flatMap((station) => grouped.byStation[station]);
  assert.equal(landed.length, proposals.length, "a proposal was dropped or duplicated");
  assert.deepEqual(
    [...new Set(landed.map((entry) => entry.proposal.id))].sort(),
    proposals.map((proposal) => proposal.id).sort(),
    "every proposal must appear at exactly one station",
  );

  // THE BADGE ARITHMETIC. What the rail prints adds up to what is waiting.
  const badgeTotal = CALIBRATION_STATIONS.reduce((sum, station) => sum + grouped.counts[station], 0);
  assert.equal(badgeTotal, grouped.total, "the sky badges must sum to the pending count");
  for (const station of CALIBRATION_STATIONS) {
    assert.equal(
      grouped.counts[station],
      grouped.byStation[station].length,
      `the ${station} badge must equal the cards that station shows`,
    );
  }

  // The placements themselves.
  const at = (id: string) => landed.find((entry) => entry.proposal.id === id)!;
  assert.equal(at("demand").station, "demand");
  assert.equal(at("signals").station, "signals");
  assert.equal(at("behaviour").station, "behaviour");
  assert.equal(at("assignment").station, "assignment");
  assert.equal(at("transit").station, "transit");
  assert.equal(at("network").station, "network");
  assert.equal(at("manual").station, "manual", "a manual step's proposal belongs at its own step");
  assert.equal(at("manual").step, 2);

  // A batch is placed by its FIRST real tool and names the other step it moves,
  // rather than being drawn twice.
  assert.equal(at("batch").station, "signals");
  assert.equal(at("batch").tool, "set_signal_offset");
  assert.deepEqual(at("batch").alsoSteps, [10]);

  pin.match(
    read("lib/calibrationStations.ts"),
    /export function actionTools\(/,
    "expected a batch plan's nested tools to be read, not its wrapper name",
  );
});

// ---------------------------------------------------------------------------
// REFUTER, 2026-08-20. The test above proves the invariant in the MODULE:
// `sum(counts) === total`, every card at exactly one station. It ran green
// while the SCREEN still lost cards, because it only ever asked the question
// with a fully loaded rail.
//
// The rail drew a row only for stations that carried steps. `groupProposals`
// places a card from the static step→station table, which answers whether or
// not the rail loaded. So whenever the rail had no steps for a station -- the
// request failed, or the pending listing simply arrived first, or the payload
// omitted a step -- the card at that station had no badge, no row, and no way
// to be opened at all (the open station is only ever set from a rail row).
// Meanwhile the station the reader WAS on printed "N are waiting at other
// steps -- see the blue badges on the left rail", pointing at badges that were
// not rendered. Measured before the fix: 4 of 5 orphaned on a failed rail.
//
// "Every proposal reaches exactly one station" is only true if the station can
// be REACHED, so the row rule now lives beside the placement rule and this test
// executes them together.
// ---------------------------------------------------------------------------
contractTest("unit #50-b: a station holding a proposal is a row, even before the rail loads", (pin) => {
  const pending = [
    { id: "network", actions: [{ tool: "add_link", params: {} }] },
    { id: "signals", actions: [{ tool: "set_signal_cycle", params: {} }] },
    { id: "transit", actions: [{ tool: "add_pt_line", params: {} }] },
    { id: "demand", actions: [{ tool: "set_vehicle_input_volume", params: {} }] },
    { id: "unmapped", actions: [{ tool: "set_random_seed", params: {} }] },
  ];

  /** Every station a reader can actually open, for a given rail payload. */
  const reachable = (railSteps: { number: number; manual_area: boolean }[]) => {
    const grouped = groupProposals(pending, stationResolverFor(railSteps));
    const rows = stationRowsShown(groupSteps(railSteps), grouped.counts);
    return { grouped, rows };
  };

  const full = railStepsFromBackend();
  for (const [name, railSteps] of [
    // The rail request failed, or has not answered yet. THE case this exists for.
    ["no rail at all", [] as typeof full],
    // A payload that omits steps -- ① and ⑨ here.
    ["a rail missing steps 1 and 9", full.filter((step) => step.number !== 1 && step.number !== 9)],
    // The happy path, which must not regress.
    ["the full rail", full],
  ] as const) {
    const { grouped, rows } = reachable(railSteps as { number: number; manual_area: boolean }[]);

    for (const station of CALIBRATION_STATIONS) {
      if (grouped.byStation[station].length === 0) continue;
      assert.ok(
        rows.includes(station),
        `${name}: ${grouped.byStation[station].length} proposal(s) placed at "${station}", which the rail draws no row for -- unreachable`,
      );
    }

    // The badge arithmetic a READER can perform: what is on screen adds up to
    // what is pending. A badge that is not drawn cannot be counted.
    const visible = rows.reduce((sum, station) => sum + grouped.counts[station], 0);
    assert.equal(
      visible,
      grouped.total,
      `${name}: only ${visible} of ${grouped.total} pending proposals wear a badge a reader can see`,
    );

    // The default station is always a row: it is the fallback placement's home
    // and the station the screen opens on.
    assert.ok(rows.includes(DEFAULT_STATION), `${name}: the default station must always be reachable`);
  }

  // A station with neither steps nor proposals stays OFF the rail -- the fix
  // must not turn the rule into "always draw everything", which would put eight
  // empty rows in front of a reader whose rail failed.
  const emptyRows = stationRowsShown(groupSteps([]), {});
  assert.deepEqual(emptyRows, [DEFAULT_STATION], "with nothing to show, only the default station is a row");

  // The rail must USE the shared rule rather than keeping its own guard: two
  // copies of "which rows exist" is how this defect happened in the first place.
  const rail = read("components/calibration/FlowRail.tsx");
  pin.match(rail, /stationRowsShown\(grouped, proposalCounts\)/, "expected the rail to draw rows by the shared rule");
  pin.doesNotMatch(
    withoutComments(rail),
    /steps\.length === 0 && !isScoring\) return null/,
    "expected no private row guard that can disagree with where the cards were placed",
  );
  // ...and a row drawn for its badge alone must say its steps are unread rather
  // than reading as "nothing to do here".
  pin.match(rail, /stepsUnread && <StepsUnreadNote/, "expected a steps-unread row to say so");
});

contractTest("unit #50-b: a proposal this product cannot place says so, and is never hidden", (pin) => {
  const unmapped = placeProposal({ actions: [{ tool: "set_random_seed", params: {} }] });
  assert.equal(unmapped.station, DEFAULT_STATION, "an unplaceable proposal goes to the station always shown");
  assert.equal(unmapped.step, null, "no guideline step may be invented for it");
  assert.deepEqual(unmapped.unmappedTools, ["set_random_seed"], "its tool must be named for the caption");
  assert.equal(unmapped.fallback, true, "a fallback placement must be flagged so the card can be captioned");

  // An empty payload is the same story: visible, and honest about knowing
  // nothing.
  const empty = placeProposal({ actions: [] });
  assert.equal(empty.station, DEFAULT_STATION);
  assert.equal(empty.step, null);
  assert.equal(empty.fallback, true);

  // A placed card carries NO caption -- the note is for the exceptions only.
  const placed = placeProposal({ actions: [{ tool: "set_vehicle_input_volume", params: {} }] });
  assert.equal(placed.fallback, false);
  assert.equal(placed.station, "demand");
  assert.deepEqual(placed.alsoSteps, []);

  // Every tool this module DOES place must name a step §2.12.1 actually has.
  for (const [tool, step] of Object.entries(MRWA_STEP_BY_TOOL)) {
    assert.ok(
      Number.isInteger(step) && step >= 1 && step <= 13,
      `${tool} is mapped to a step §2.12.1 does not have: ${step}`,
    );
  }

  const proposals = read("components/calibration/StationProposals.tsx");
  pin.match(proposals, /placed\.fallback/, "expected the fallback placement to be captioned, not silent");
  // RE-AIMED by #50-d (2026-08-20). The caption was a full sentence ending "--
  // the step is not invented"; that clause is doctrine about the product and is
  // at /docs now (D11 in docs/product/calibration-doctrine.md, pinned by the
  // "#50-d ③" ledger). What a reviewer needs above an approval card is WHICH
  // exception applies, so the pin is aimed at the marker that says it -- and the
  // marker must still distinguish "no step exists for this tool" from "a step
  // exists and its station did not resolve", which is the distinction the
  // deleted sentence carried and the one that could actually mislead.
  pin.match(
    withoutFolds(withoutComments(proposals)),
    /단계 미매핑/,
    "expected an unplaceable card to be MARKED unmapped on the surface, never placed silently at the default station",
  );
  pin.match(
    withoutFolds(withoutComments(proposals)),
    /스테이션 미해결/,
    "expected the station-fallback case to be told apart from the unmapped one",
  );
  // The empty state must not read as "nothing anywhere" when there is work at
  // another step.
  pin.match(proposals, /otherCount > 0/, "expected an empty station to say how many proposals wait elsewhere");
});

contractTest("unit #50-b: the rail navigates by switching stations, and never by scrolling", (pin) => {
  const rail = read("components/calibration/FlowRail.tsx");
  const page = read("app/calibration/page.tsx");
  const source = withoutComments(`${rail}\n${page}`);

  // #50 scrolled to an anchor. Measured on the real screen: with every station
  // stacked, a scroll left the reader unable to see WHAT the page contained --
  // and a smooth scroll silently did nothing at all on a non-compositing
  // browser. The rail switches the panel now, so nothing is below a fold.
  pin.doesNotMatch(source, /scrollIntoView/, "expected no scroll navigation -- the rail switches stations");
  pin.doesNotMatch(source, /getElementById/, "expected no anchor lookup -- the station is state, not a section id");
  pin.match(rail, /onSelectStation\(station\)/, "expected a rail row to switch the open station");

  // The open station is a DERIVED selection (lib/asyncResource.ts), never an
  // effect that corrects itself one painted frame later.
  pin.match(
    page,
    /resolveSelection\(stationIntent, CALIBRATION_STATIONS/,
    "expected the open station to be resolved during render from the reader's intent",
  );

  // One station is shown at a time: the panel bodies are keyed off the active
  // station, not stacked as sections.
  pin.doesNotMatch(page, /id="station-/, "expected no anchored station sections -- there is one panel now");
  for (const station of ["scoring", "network", "signals", "behaviour", "manual"]) {
    pin.match(
      page,
      literalPattern(`activeStation === "${station}"`),
      `expected the ${station} body to render only when its station is open`,
    );
  }
});

contractTest("unit #50-b: the context bar owns what belongs to no single step", (pin) => {
  const page = read("app/calibration/page.tsx");

  // Dataset, run, verdict, before/after -- global context, in one place, so no
  // step can appear to own it.
  pin.match(page, /fetchActiveDataset\(\)/, "expected the active dataset to be named in the context bar");
  pin.match(page, /데이터셋/, "expected the dataset label");
  pin.match(page, /채점할 실행/, "expected the run selector in the context bar");
  pin.match(page, /종합 판정/, "expected the one-line verdict in the context bar");
  pin.match(page, /전후 비교/, "expected the before/after toggle in the context bar");
  pin.match(page, /aria-pressed=\{compareOn\}/, "expected the comparison to be a real toggle control");

  // The verdict is the scoring table's OWN comparison, imported rather than
  // repeated: a second copy is how a header and the table under it would come
  // to disagree about which nodes passed.
  pin.match(
    page,
    /nodeGehPasses \} from "@\/components\/calibration\/NodeScoreTable"/,
    "expected the header verdict to reuse the table's pass rule",
  );
  pin.match(
    read("components/calibration/NodeScoreTable.tsx"),
    /export function nodeGehPasses\(/,
    "expected exactly one pass rule, exported",
  );
  // ...and it must state what it could NOT grade rather than shrinking the
  // denominator quietly.
  pin.match(page, /ungradedCount/, "expected ungradable nodes to be stated beside the pass count");
});

contractTest("unit #50-b: a map is drawn only where the server named the nodes", (pin) => {
  const page = read("app/calibration/page.tsx");

  // The user's decision: 채점 always, and the step stations only where node
  // context exists. Which nodes belong to a step is geh_ladder's OWN answer
  // (`next_action.mrwa_step`), never a classification made here.
  pin.match(
    page,
    /node\.diagnosis\?\.next_action\?\.mrwa_step/,
    "expected a station's nodes to come from the server's own step attribution",
  );
  pin.match(
    page,
    /if \(ids\.length === 0\) return null;/,
    "expected no map at all when the server named no node for this station",
  );
  // ⑫ has no per-node rung of its own, so its node context is the search plan's
  // own split -- also the server's.
  pin.match(
    page,
    /card\.plan\.calibration_nodes/,
    "expected the behaviour station's nodes to be the search plan's own split",
  );
  // The scoring station's map is unconditional: it is where a reader asks
  // where the failing nodes ARE.
  pin.match(page, /h-\[420px\]/, "expected the scoring station to keep its full-height map");
});

contractTest("unit #50-b: the proposal collection panel is gone, and nothing imports it", (pin) => {
  // The user's decision: a proposal read away from the step it acts on is a
  // decision made without its evidence. The panel is deleted -- not hidden --
  // and the cards live at their steps.
  for (const file of appSourceFiles()) {
    pin.doesNotMatch(
      read(file),
      /calibration\/ProposalRail/,
      `${file} still imports the deleted proposal collection panel`,
    );
  }
  assert.ok(
    !appSourceFiles().includes("components/calibration/ProposalRail.tsx"),
    "components/calibration/ProposalRail.tsx must be gone, not merely unused",
  );

  const page = read("app/calibration/page.tsx");
  pin.match(page, /<StationProposals/, "expected the open station to render its own proposals inline");
  pin.match(page, /이 단계의 제안/, "expected the inline proposals to be named as this step's");
  // One read for the whole screen: the badges and the cards are the same fact,
  // and two requests would let a badge say 2 where the station shows 1.
  pin.match(page, /usePendingProposals\(\{/, "expected one pending read, shared by the rail and the station");
  pin.match(
    read("components/calibration/StationProposals.tsx"),
    /fetchCalibrationPendingApprovals\(\)/,
    "expected the pending listing to stay in the module that renders it",
  );
});

// ---------------------------------------------------------------------------
// Unit #50-c — NUMBERS FIRST, PROSE FOLDED.
//
// The user drove #50-b's real screen and reported what was still wrong with it:
// "거의 모든 fine text는 크게/직관적 대안/없애", and of the mono provenance lines
// in particular ("saturation_flow.verify_applied_row · op §5.2.5.1, Table 5-6,
// p.187" and its kind): "이런것도 다 지워 — 화면을 포화시켜". Measured on that
// screen: every station panel opened with two to four sentences of RULE before
// the first number, and every step box closed with a mono line naming a source
// file and a paragraph number.
//
// So the surface of every tab is now numbers, verdicts and ONE next-action line,
// and every rule, citation and machine code lives inside a fold or an ⓘ.
//
// THE CONSTRAINT THAT MAKES THIS A RESTRUCTURE RATHER THAN A CULL, and the
// reason these tests are shaped the way they are: ZERO INFORMATION LOSS. A pin
// that only asked "is the citation off the surface" would pass just as happily
// over a deletion, and deleting an audit trail to tidy a screen is the one thing
// this codebase must not do. So the ledger test below asserts BOTH directions
// for every moved string: it still exists in the file, and it does not render on
// the surface. `withoutFolds` is what lets a source-regex suite tell those two
// apart at all.
// ---------------------------------------------------------------------------

/**
 * The source with every FOLD's contents removed — what a reader sees BEFORE
 * opening anything.
 *
 * Two openers, because this workspace has two fold spellings: the `<details>`
 * every calibration component uses directly, and `<BaseModelSection>`, the
 * absorbed base-model tab's own wrapper (which renders one -- see its doc
 * comment). `InfoTip` is not stripped: it takes its sentence as a `text` prop,
 * so a tooltip's contents never look like surface markup to begin with.
 *
 * FAILS CLOSED, and that is the whole design. When this helper cannot account
 * for a file's folds the WHOLE FILE IS KEPT rather than a region dropped: a
 * shrinking haystack makes every `doesNotMatch` pin over it pass vacuously,
 * which is the failure mode `withoutComments` above carries its own scars from.
 * Keeping the source means a malformed fold surfaces as a named pin failure
 * instead of as a green run over a citation that is still on the screen.
 *
 * TWO GUARDS, and the first draft had neither. Measured by mutation 2026-08-20
 * (refuter): the fail-closed promise above covered only an UNTERMINATED opener,
 * and a spurious opener that PAIRS with a real closer far below is the same
 * disease arriving through the front door. Planting one string literal —
 * `const FOLD_HINT = "<details";` — at the top of app/calibration/page.tsx made
 * this helper swallow 15,542 characters, 43% of that file's surface, and a
 * `text-[13px]` planted inside the swallowed region went unseen by the floor
 * pin while all 168 tests stayed green. So:
 *
 *   1. an opener is a TAG, not a substring — a real one is followed by
 *      whitespace or `>`, and `"<details"` in a string literal is followed by a
 *      quote;
 *   2. openers and closers must BALANCE, and any mismatch keeps the file whole.
 */
function withoutFolds(source: string): string {
  let current = source;
  for (const [opener, closer] of [
    ["<details", "</details>"],
    ["<BaseModelSection", "</BaseModelSection>"],
  ] as const) {
    const opens: number[] = [];
    for (let at = current.indexOf(opener); at !== -1; at = current.indexOf(opener, at + 1)) {
      if (/[\s>]/.test(current[at + opener.length] ?? "")) opens.push(at);
    }
    // Guard 2. A file this helper cannot account for is a file it must not
    // shrink -- including the unterminated case, which lands here as 1-vs-0.
    if (opens.length !== current.split(closer).length - 1) continue;

    let out = "";
    let index = 0;
    for (const start of opens) {
      if (start < index) continue; // already inside a stripped block
      const end = current.indexOf(closer, start);
      if (end === -1) break;
      out += current.slice(index, start);
      index = end + closer.length;
    }
    out += current.slice(index);
    current = out;
  }
  return current;
}

contractTest("contract helper: withoutFolds removes fold CONTENTS and keeps the surface", (pin) => {
  const sample = '<p className="a">surface</p>\n<details><summary>s</summary><p>FOLDED</p></details>\n<p>tail</p>';
  pin.doesNotMatch(withoutFolds(sample), /FOLDED/, "a fold's contents must not count as surface");
  pin.match(withoutFolds(sample), /surface/, "text before the fold must survive");
  pin.match(withoutFolds(sample), /tail/, "text after the fold must survive");

  // The BaseModelSection spelling is a fold too.
  pin.doesNotMatch(
    withoutFolds('<BaseModelSection title="x">\n<p>INSIDE</p>\n</BaseModelSection>'),
    /INSIDE/,
    "the base-model tab's own section wrapper must count as a fold",
  );

  // THE FAIL-CLOSED CASE. An opener with no closer must NOT swallow the file --
  // if it did, every doesNotMatch pin over this haystack would pass on nothing.
  pin.match(
    withoutFolds('<details>\n<p>UNCLOSED</p>\nconst c = "text-[13px]";'),
    /UNCLOSED[\s\S]*text-\[13px\]/,
    "an unterminated fold must keep the rest of the file, so the pin fails rather than passes vacuously",
  );

  // THE SAME FAILURE THROUGH THE FRONT DOOR (found by mutation 2026-08-20). A
  // `<details` inside a STRING LITERAL is not a fold, but it pairs with the next
  // real closer and deletes everything between -- and `withoutComments` cannot
  // help, because it copies string literals verbatim by design. Measured on the
  // real file before this guard existed: one such literal hid 43% of
  // app/calibration/page.tsx's surface, including a planted 13px violation,
  // with every pin green.
  pin.match(
    withoutFolds('const s = "<details";\nSURFACE text-[13px]\n<details>\n<p>FOLDED</p>\n</details>\nTAIL'),
    /SURFACE text-\[13px\][\s\S]*TAIL/,
    "a `<details` inside a string literal is not a fold -- pairing it with a real closer silently empties the haystack every surface pin scans",
  );
  pin.doesNotMatch(
    withoutFolds('const s = "<details";\nSURFACE\n<details>\n<p>FOLDED</p>\n</details>\nTAIL'),
    /FOLDED/,
    "and the REAL fold beside it must still be stripped -- guarding the haystack must not stop the helper working",
  );

  // Balance is the second guard: a stray closer is as unaccountable as a stray
  // opener, and an unaccountable file is one this helper must hand back whole.
  pin.match(
    withoutFolds("HEAD\n</details>\n<details>\n<p>X</p>\n</details>\nTAIL"),
    /HEAD[\s\S]*X[\s\S]*TAIL/,
    "an unbalanced fold count must keep the file whole rather than drop a region",
  );
});

/** The six files whose whole body IS the calibration surface.
 *
 * `BaseModelStation.tsx` is deliberately absent from the type-floor sweep and
 * present in the colour sweep, which is not an oversight: it is a 2,600-line
 * screen this project ABSORBED rather than rewrote (#50), its helper components
 * are declared at module top level while being rendered only from inside
 * folded sections, and a file-wide "nothing under 14px" claim over it would be
 * a claim the scan cannot actually make. What #50-c owed that tab was
 * STRUCTURE -- verdict first, every other section an accordion -- and that is
 * what its own test below checks.
 *
 * `ParameterSearchRefusalAction.tsx` (#57) joined 2026-08-25: the refuter
 * measured that the file existing was not enough to be SWEPT -- it had to be
 * NAMED here, or the amber-ban, fall-through-ink and 14px-floor tests below
 * simply never open it. Proven by planting all three violations in its
 * `CHIP_CLASS` at once and watching the (then five-file) sweep stay green. */
const CALIBRATION_SURFACE_FILES = [
  "app/calibration/page.tsx",
  "components/calibration/FlowRail.tsx",
  "components/calibration/NodeScoreTable.tsx",
  "components/calibration/BehaviourStation.tsx",
  "components/calibration/StationProposals.tsx",
  "components/calibration/ParameterSearchRefusalAction.tsx",
];

contractTest("unit #50-c: every citation and machine code is INSIDE a fold, and none of them was deleted", (pin) => {
  // BOTH DIRECTIONS, per moved string. The second pin is the one the user asked
  // for; the first is the one that stops it from being satisfied by a deletion.
  const moved = (file: string, pattern: RegExp, what: string) => {
    const source = withoutComments(read(file));
    pin.match(source, pattern, `${what} must still exist in ${file} -- #50-c moved provenance, it deleted none`);
    pin.doesNotMatch(
      withoutFolds(source),
      pattern,
      `${what} must not render on the surface of ${file} -- the user's 2026-08-20 decision put it one click away`,
    );
  };

  // THE LEDGER'S FRAMING, CORRECTED BY #50-d. This test's title says "none of
  // them was deleted", and as of 2026-08-20 that is no longer true of every row
  // it used to carry: TWO of them were deleted, by the user's own decision, and
  // saying so is the honest version of this ledger.
  //
  //   * `{guardrail.decision}` -- the ⑫ decision code -- went with the whole
  //     guardrail section ("#51-c 표시 핀 사용자 권한 제거(기록)", roadmap row
  //     #50-d). It is not folded away; it is not rendered at all.
  //   * the workbook-column RULE left the screen for the knowledge file, which
  //     is a MOVE and not a deletion -- but not a move into a fold, so it cannot
  //     be described by this helper. It is pinned by the "#50-d ③" eviction
  //     ledger below, which asserts the same two directions across three files
  //     instead of one.
  //
  // What remains here is the #50-c claim in its original form: provenance that
  // is still on this screen is one click away, and still there.
  const rail = "components/calibration/FlowRail.tsx";
  moved(rail, /\{rail\?\.citation\}/, "§2.12.1's own citation");
  moved(rail, /refusals\.map\(\(refusal\) => refusal\.code\)/, "the precondition gate's refusal CODES");
  moved(rail, /card\.plan\.space\[0\]\.citation/, "the page the searched bands are published on");

  const page = "app/calibration/page.tsx";
  moved(page, /\{step\.source\}/, "which artifact answered a step");

  const table = "components/calibration/NodeScoreTable.tsx";
  moved(table, /\{report\.reason_code\}/, "reality_check's own refusal code");
  moved(table, /nextAction\.mrwa_step != null/, "the next action's MRWA step and citation");

  const behaviour = "components/calibration/BehaviourStation.tsx";
  moved(behaviour, /\{rows\.citation\}/, "Table 5-6's citation");
  moved(behaviour, /\{verdict\.citation\}/, "each behaviour verdict's own citation");

  moved("components/calibration/StationProposals.tsx", /\.\.\.placed\.unmappedTools,/, "a placement note's tool ids");

  // AND THE HALF THAT MAY NEVER FOLD. #50-c shortened the empty state to one
  // line and folded the explanation, but the count of proposals waiting at
  // OTHER stations is not prose: without it every station's empty state reads
  // identically, and a reader concludes there is no work anywhere -- which is
  // the orphan-proposal failure #50-b was built to close, re-arriving as a
  // typography decision. The component's own comment claims this ("the
  // `otherCount` half is NOT prose and never folds") and nothing held it to it:
  // measured by mutation 2026-08-20, collapsing both arms of the ternary to the
  // bare sentence left all 168 tests green.
  pin.match(
    withoutFolds(withoutComments(read("components/calibration/StationProposals.tsx"))),
    /다른 단계 \$\{otherCount\}건/,
    "the count waiting at OTHER stations must stay on the surface -- folded, every station's empty state reads as 'no work anywhere'",
  );

  // AND NO NEW ONE MAY APPEAR. The shape the user named is a mono line at the
  // fold type size; on the surface there is now no such thing anywhere, so the
  // ban can be stated over the whole surface rather than site by site.
  for (const file of CALIBRATION_SURFACE_FILES) {
    pin.doesNotMatch(
      withoutFolds(withoutComments(read(file))),
      /font-mono text-\[13px\]/,
      `${file}: a mono 13px provenance line is exactly the shape that saturated this screen -- fold it`,
    );
  }
});

// Style/layout pins, in their own test per this file's own rule about keeping
// brittle restyle pins away from behavioural ones.
contractTest("unit #50-c: the calibration surface has a 14px floor, and 13px lives only inside a fold", (pin) => {
  for (const file of CALIBRATION_SURFACE_FILES) {
    const surface = withoutFolds(withoutComments(read(file)));
    pin.doesNotMatch(
      surface,
      /text-\[(?:[1-9]|1[0-3])(?:\.\d+)?px\]/,
      `${file}: nothing on the surface may be under 14px -- 13px is fold type`,
    );
    pin.doesNotMatch(surface, /\btext-xs\b/, `${file}: text-xs is 12px -- under this screen's floor`);
    // A `<summary>` is the fold's own TRIGGER: it is always visible, so it is
    // surface even though `withoutFolds` strips it with the block it opens.
    pin.doesNotMatch(
      withoutComments(read(file)),
      /<summary[^>]*text-\[13px\]/,
      `${file}: a fold's trigger is always on the screen -- it takes the surface floor, not the fold's`,
    );
  }

  // BOTH DIRECTIONS: the floor must have been raised, not the small type
  // deleted. 13px is still what fold CONTENT is set in.
  pin.match(
    read("components/calibration/NodeScoreTable.tsx"),
    /<details[\s\S]{0,600}?font-mono text-\[13px\]/,
    "expected 13px to survive as fold type -- the floor applies to the surface, not to raw evidence",
  );

  // KEY NUMBERS, at the size the question deserves: the node-total GEH on its
  // tile, and the totals in the node's own detail.
  pin.match(
    read("components/calibration/NodeScoreTable.tsx"),
    /font-mono text-\[24px\] font-bold/,
    "expected the node-total GEH to be the biggest thing on its tile",
  );
  pin.match(
    read("components/calibration/NodeScoreTable.tsx"),
    /font-mono text-\[20px\] font-bold/,
    "expected the detail's observed/modelled/GEH numbers to be read at a glance",
  );
});

contractTest("unit #50-c: the scoring surface is a TILE GRID, and the movement columns moved into the node's detail", (pin) => {
  const table = read("components/calibration/NodeScoreTable.tsx");
  const detailAt = table.indexOf("export function NodeScoreDetail");
  const grid = table.slice(table.indexOf("export default function NodeScoreTable"), detailAt);
  const detail = table.slice(detailAt);

  // Slice sanity, for the reason #51-c's guardrail slices carry one: a missed
  // anchor makes `slice` hand back the whole file and the pins below then pass
  // on the OTHER half of the component.
  pin.match(grid, /report\.status !== "CHECKED"/, "slice sanity: the grid slice must start at the grid component");
  pin.doesNotMatch(grid, /export function NodeScoreDetail/, "slice sanity: the grid slice must end before the detail");

  // THE TABLE IS GONE from the scoring surface -- it is a grid of tiles now.
  pin.doesNotMatch(withoutComments(table), /<table/, "expected no node TABLE -- the scoring surface is a tile grid");
  pin.match(grid, /<ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">/, "expected one tile per node in a grid");

  // The tile speaks the SAME cross-highlight contract the rows did, so the map
  // beside it keeps working: click carries (id, name), and the map's own click
  // comes back through `activeNodeId`/`selectedNodeIds`.
  pin.match(
    grid,
    /onClick=\{\(\) => onNodeClick\(node\.node_id, String\(node\.node_name \?\? node\.node_id\)\)\}/,
    "expected a tile click to speak the map's own (nodeId, nodeName) contract",
  );
  pin.match(grid, /const active = activeNodeId === node\.node_id;/, "expected the map's active node to light a tile");
  pin.match(grid, /const selected = selectedNodeIds\.includes\(node\.node_id\);/, "expected the map's selection to reach the tiles");

  // TWO CHANNELS, and this is the honesty pin of the restyle: the FILL is the
  // report's verdict and the RING is selection. One channel carrying both would
  // make a selected ungradable node look like a pass.
  pin.match(grid, /active \? "ring-2 ring-sky-400" : selected \? "ring-1 ring-sky-400"/, "expected selection to be a sky RING, never the tile's fill");
  // EACH BRANCH TO ITS OWN FILL, and the tightness is the point. This pin used
  // to read `passes === null[\s\S]{0,200}?bg-emerald-50[\s\S]{0,80}?bg-rose-50`,
  // which asked only that three tokens appear in that order -- so painting the
  // UNGRADABLE branch `bg-emerald-50` left it green (measured by mutation
  // 2026-08-20, refuter). A node this run could not grade reading as a pass is
  // the single thing the fill/ring split exists to prevent, and the pin that
  // named it could not fail for it.
  pin.match(
    grid,
    /passes === null\s+\? "border-slate-300 bg-surface[^"]*"\s+: passes\s+\? "border-emerald-200 bg-emerald-50"\s+: "border-rose-200 bg-rose-50"/,
    "expected the tile FILL to be neutral when ungradable, emerald on pass and rose on fail -- an ungradable node must never wear the pass colour",
  );
  // ...and never a fabricated number under it. `node_total_geh` is `None` on
  // the wire for a node reality_check could not grade, and `fmt` prints a dash
  // for it; a `?? 0` here would put a perfect GEH on an ungraded node's tile.
  pin.match(
    table,
    /return typeof value === "number" && Number\.isFinite\(value\) \? value\.toFixed\(digits\) : "-";/,
    "expected an ungradable GEH to print a dash",
  );
  pin.doesNotMatch(grid, /node_total_geh \?\?/, "expected no default to be substituted for a GEH this run could not compute");

  // A FAIL tile names its next rung; a passing one says nothing extra, because
  // there is nothing to climb.
  pin.match(grid, /passes === false && node\.diagnosis && \(/, "expected the rung link only where the node FAILED");
  pin.match(grid, /rungLabel\(node\.diagnosis\.rung, t\)\} →/, "expected the fail tile to name the diagnosis rung as a way onward");
  // ...and the label is geh_ladder.py's own word or the RAW RUNG, never a
  // sentence this file invented for a rung it does not recognise. A new backend
  // rung must arrive on screen as its own code, so it is obviously untranslated
  // rather than quietly filed under a word that means something else.
  pin.match(
    table,
    /function rungLabel\(rung: string, t: Translate\): string \{\s*\n\s*const entry = RUNG_LABELS\[rung\];\s*\n\s*return entry \? t\(entry\[0\], entry\[1\], entry\[2\]\) : rung;/,
    "expected an unknown rung to fall back to the backend's own code, never to an invented label",
  );

  // DEMOTED, NOT DELETED: the five old columns render in the node's detail, and
  // NOT on the grid -- which is what makes the grid a summary rather than a
  // second, disagreeing copy of the table.
  pin.match(detail, /node\.observed_total/, "expected the observed total in the node's own detail");
  pin.match(detail, /node\.modelled_total/, "expected the modelled total in the node's own detail");
  pin.match(detail, /rungLabel\(node\.diagnosis\.rung, t\)/, "expected the diagnosis to be readable in the detail too");
  pin.doesNotMatch(grid, /node\.observed_total/, "expected the observed/modelled columns to be OFF the grid -- a tile carries the verdict");

  // The verdict in the detail is still the REPORT's threshold, threaded in
  // rather than recomputed -- and optional, because the per-station node
  // context has no report to hand and must not invent a bar.
  pin.match(detail, /const passes = nodeGehPasses\(node, threshold\);/, "expected the detail to reuse the one pass rule");
  // AND THE RULE ITSELF, not only the call. `reality_check` fails a node at
  // `total_geh >= geh_threshold` (backend/services/agent/fixes.py) — a `<=`
  // here would paint a node sitting exactly ON the bar emerald while the report
  // beside it raised a proposal against that node. Unpinned until a mutation
  // 2026-08-20 flipped the operator and left all 168 tests green.
  pin.match(
    table,
    /return node\.node_total_geh < threshold;/,
    "expected the screen's pass rule to be the report's: reality_check fails a node at GEH >= threshold, so passing is strictly below it",
  );
  pin.match(
    read("app/calibration/page.tsx"),
    /<NodeScoreDetail\s+node=\{activeNode\}\s+threshold=\{report\.geh_threshold\}/,
    "expected the scoring station to hand the detail the report's own threshold",
  );
  // BOTH CALL SITES. The per-station node context holds the same report and
  // must thread the same bar: two panes rendering one node with two different
  // verdicts is the disagreement this whole shared component exists to stop.
  // (`?.` because the report may not have loaded — a missing bar prints the GEH
  // with no verdict colour, which `nodeGehPasses` returns `null` for.)
  pin.match(
    read("app/calibration/page.tsx"),
    /<NodeScoreDetail\s+node=\{activeNode\}\s+threshold=\{report\?\.geh_threshold\}/,
    "expected the per-station node context to hand the detail the same report's threshold",
  );

  // The report's coverage survives the loss of its mono line, as chips.
  pin.match(grid, /movements_ungradable \?\? 0/, "expected the ungradable count to stay beside the compared count");
});

contractTest(
  "unit #50-e: every tile in the scoring grid reserves the same TWO-LINE footer, capped so a wrapped label cannot grow it",
  (pin) => {
    const table = read("components/calibration/NodeScoreTable.tsx");
    const grid = table.slice(
      table.indexOf("export default function NodeScoreTable"),
      table.indexOf("export function NodeScoreDetail"),
    );

    // AUTHORITY: #50-e (2026-08-23), CORRECTED same day by the refuter's own
    // measurement on the live app: at 1280x900 the tile column is ~97px wide
    // (rail split x report/map split x xl:grid-cols-3), and at that width
    // EVERY rung label wraps -- ko/ja run two lines, en up to three ("Turn
    // distribution ->"). A bare min-h floored a PASS tile up to a one-line
    // FAIL tile, but nothing capped a FAIL tile that wrapped past one line,
    // so a wrapped tile grew past its unwrapped neighbours anyway -- the
    // uniform-height claim was false at the width that matters. Reserve AND
    // cap must hold TOGETHER, on the SAME element, so this is one regex
    // spanning both classes rather than two separate string checks a rewrite
    // could satisfy by putting them on different elements.
    pin.match(
      grid,
      /<span\s+className="mt-1\.5 block min-h-\[40px\] line-clamp-2 text-\[14px\] leading-5"/,
      "expected the SAME element to both reserve two lines (min-h-[40px]) and cap at two (line-clamp-2) -- either alone reproduces the refuter's measured break",
    );

    // THE FULL LABEL SURVIVES THE CLAMP. A rung label is an action a reader
    // must be able to read in full even when the box only shows two lines of
    // it -- title for a mouse, aria-label for assistive tech, neither of
    // which line-clamp's CSS-only truncation provides on its own.
    pin.match(
      grid,
      /title=\{rungFooterText \|\| undefined\}/,
      "expected the clamped label's full text to survive as a title, for when two lines still is not enough",
    );
    pin.match(
      grid,
      /aria-label=\{rungFooterText \|\| undefined\}/,
      "expected the clamped label's full text to survive for assistive tech too, not only sighted hover",
    );

    // ONE COMPUTATION per node, so the visible (possibly clamped) text and
    // its title/aria fallback can never fall out of sync with each other.
    pin.match(
      grid,
      /const rungFooterText =\s*passes === false && node\.diagnosis\s*\? `\$\{rungLabel\(node\.diagnosis\.rung, t\)\} →`\s*: passes === null\s*\? t\("등급 불가", "ungradable", "評価不可"\)\s*: "";/,
      "expected the footer text computed once per node, not recomputed separately for the visible span and its title/aria",
    );

    // UNCONDITIONAL -- the reserving/capping span itself is not a third
    // branch of a ternary that only renders for fail/ungradable, which would
    // bring back exactly the mismatch this test exists to catch. Comments
    // stripped first so the check is about the CODE, not whichever prose
    // happens to precede the span this week.
    const surface = withoutComments(grid);
    const footerAt = surface.indexOf('className="mt-1.5 block min-h-[40px] line-clamp-2');
    pin.doesNotMatch(
      surface.slice(Math.max(0, footerAt - 80), footerAt),
      /passes/,
      "expected the footer row to render unconditionally -- gating it behind `passes` reintroduces the height mismatch between a pass and a fail/ungradable tile",
    );

    // The two states that fill it are unchanged -- one line each, inside the
    // reserved footer rather than each defining its own.
    pin.match(grid, /passes === false && node\.diagnosis && \(/, "expected the rung link only where the node FAILED");
    pin.match(grid, /passes === null && \(/, "expected the ungradable label inside the same reserved footer");
  },
);

contractTest(
  "#60a: every tile's bordered box fills its stretched grid row -- the #50-e footer reserve alone was not enough",
  (pin) => {
    const table = read("components/calibration/NodeScoreTable.tsx");
    const grid = table.slice(
      table.indexOf("export default function NodeScoreTable"),
      table.indexOf("export function NodeScoreDetail"),
    );

    // REPORTED FROM LIVE USE (the user, 2026-08-25): "node 5 is shorter" --
    // a tile with a diagnosis line reads taller than one without.
    //
    // MEASURED (static reproduction of this exact markup, at the grid's own
    // real column width, the SAME discipline #50-e's own refuter measurement
    // used): #50-e's min-h-[40px]/line-clamp-2 footer already equalises a
    // PASS tile against a FAIL/ungradable one -- both measured 117px in the
    // repro. The remaining gap is a DIFFERENT mechanism entirely: the `<ul>`
    // is `grid`, and CSS Grid's default `align-items: stretch` already
    // equalises every `<li>` in a row to its tallest neighbour (a wrapped
    // node name, or a wrapped rung label past the footer's own 2-line cap)
    // -- but the visible bordered `<button>` inside each `<li>` was never
    // told to FILL that stretched height, so the invisible `<li>` grew while
    // the bordered box a reader actually sees stayed shrink-wrapped to its
    // own shorter content. Without `h-full` the repro measured 117/117/161px
    // across three tiles built to differ on purpose; with it, all three
    // measured 161px.
    pin.match(
      grid,
      /className=\{`w-full h-full rounded-lg border px-3 py-2\.5 text-left transition \$\{/,
      "expected the tile's bordered button to carry h-full -- the mechanism that lets it fill its grid-stretched <li> instead of shrink-wrapping to its own content",
    );

    // THE MECHANISM THIS DEPENDS ON, still in place: the grid must still
    // default to `align-items: stretch` (i.e. no `items-start` override) for
    // `h-full` to have a stretched parent height to fill in the first place.
    pin.match(
      grid,
      /<ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">/,
      "expected the tile grid to keep CSS Grid's default stretch (no items-start) -- h-full on the button has nothing to fill without it",
    );
    pin.doesNotMatch(
      grid,
      /<ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3 items-start">/,
      "expected the grid never to opt OUT of row stretch -- that would silently reopen the exact mismatch this pin exists to catch",
    );
  },
);

contractTest(
  "unit #50-e: the assistant-handoff event name agrees across FloatingAssistant's receiver and all five dispatchers",
  (pin) => {
    // AUTHORITY: the refuter, 2026-08-23 -- MEASURED: mutating
    // FloatingAssistant.tsx's addEventListener/removeEventListener string by
    // ONE CHARACTER fails ZERO existing tests while silently breaking every
    // dispatcher (app/health/page.tsx, CurationBar.tsx, #50-e's own node-detail
    // CTA, #53's ⑪ station CTA, and #57's TABLE_5_6_NOT_APPLIED chip) -- the
    // drawer simply never opens, with no error anywhere a reader would see it.
    // Each side declares the string independently BY DESIGN (see each file's
    // own comment on why this is not a shared import -- the event NAME is the
    // contract), which is exactly why nothing but a cross-file test can catch
    // the two drifting apart.
    const EVENT_NAME = "vissim:assistant-handoff";

    const listener = withoutComments(read("components/FloatingAssistant.tsx"));
    pin.match(
      listener,
      new RegExp(`window\\.addEventListener\\("${EVENT_NAME}", handleAssistantHandoff\\);`),
      "expected FloatingAssistant's REGISTER call to use the exact event name every dispatcher sends",
    );
    pin.match(
      listener,
      new RegExp(`window\\.removeEventListener\\("${EVENT_NAME}", handleAssistantHandoff\\);`),
      "expected FloatingAssistant's UNREGISTER call to use the exact SAME event name as the register call -- a mismatch here leaks the listener without even breaking the handoff loudly",
    );

    const dispatchers: [string, string][] = [
      ["app/health/page.tsx", "the finding/report handoff buttons"],
      ["components/dashboard/CurationBar.tsx", "the insight-card handoff button"],
      ["components/calibration/NodeScoreTable.tsx", "the next-action CTA this unit (#50-e) added"],
      ["app/calibration/page.tsx", "#53's ⑪ 경로 선택 station CTA -- the fourth dispatcher"],
      [
        "components/calibration/ParameterSearchRefusalAction.tsx",
        "#57's TABLE_5_6_NOT_APPLIED lock-card chip -- the fifth dispatcher",
      ],
    ];
    for (const [file, what] of dispatchers) {
      pin.match(
        withoutComments(read(file)),
        new RegExp(`const ASSISTANT_HANDOFF_EVENT = "${EVENT_NAME}";`),
        `expected ${file} (${what}) to declare the exact same event name FloatingAssistant listens for`,
      );
    }

    // THE EVENT NAME IS NOT THE WHOLE CONTRACT (refuter, 2026-08-25). Every
    // dispatcher above agreeing on "vissim:assistant-handoff" says nothing
    // about what's INSIDE `detail` -- FloatingAssistant reads `detail.prompt`
    // specifically, so a dispatcher that renamed its payload key (measured:
    // `{ detail: { prompt } }` -> `{ detail: { text: prompt } }` in
    // ParameterSearchRefusalAction.tsx) passed every pin above, dispatched a
    // real event under the right NAME, and the drawer opened with an empty
    // composer and no error anywhere -- silent breakage indistinguishable
    // from the event-name drift the pins above already guard, just one layer
    // deeper. Pinned the SAME way: the field name declared once, checked on
    // BOTH the receiver's three uses of it and every dispatcher's own object
    // literal, so a rename on EITHER side dies here.
    const PAYLOAD_FIELD = "prompt";

    pin.match(
      listener,
      new RegExp(`\\(event as CustomEvent<\\{ ${PAYLOAD_FIELD}\\?: string \\}>\\)\\.detail;`),
      `expected FloatingAssistant to TYPE its event detail around the field every dispatcher sends ("${PAYLOAD_FIELD}")`,
    );
    pin.match(
      listener,
      new RegExp(`if \\(!detail\\?\\.${PAYLOAD_FIELD}\\) return;`),
      `expected FloatingAssistant's guard to read the SAME field name it was typed around`,
    );
    pin.match(
      listener,
      new RegExp(`setPrompt\\(detail\\.${PAYLOAD_FIELD}\\);`),
      `expected FloatingAssistant to consume the SAME field name -- three call sites, same word, or a rename passing one still breaks the other two`,
    );

    for (const [file, what] of dispatchers) {
      // Shorthand (`{ prompt }`) and an explicit computed value
      // (`{ prompt: buildHandoffPrompt(...) }`, CurationBar's own shape) both
      // satisfy this -- the KEY is the contract, not which of the two
      // equivalent ways a file spells sending it.
      pin.match(
        withoutComments(read(file)),
        new RegExp(`detail: \\{ ${PAYLOAD_FIELD}(?::[^}]+)?\\s*\\}`),
        `expected ${file} (${what}) to send its payload under the SAME field name ("${PAYLOAD_FIELD}") FloatingAssistant reads -- a rename on either side opens the drawer blank with no error anywhere`,
      );
    }
  },
);

// #56 (a) -- DECISIONS.md "화면은 실행의 name을, 도구는 id를 쓴다": before this
// unit /calibration published NO page context at all, so a user who typed the
// SCORED run's on-screen display name into the drawer got a correct but
// nameless refusal -- the tools speak the picker's <option value> (an id like
// "SIM_…"), never the label the <option> shows ("agent_run_…"). dashboard and
// base-model already publish their own selected-scenario id/name pairs the
// same way (see the pins on those two pages elsewhere in this file); this is
// calibration's.
contractTest("calibration workspace publishes the scored run's tool-vocabulary id to the assistant (#56 (a))", (pin) => {
  const page = read("app/calibration/page.tsx");

  // Quote-boundary anchored (refuter NIT 3, #56): an unanchored
  // /vissim:page-context/ substring match survives a rename to
  // "vissim:page-context-v2" -- a REAL break, since FloatingAssistant's
  // listener is bound to the exact string. Anchored to the literal dispatch
  // statement (same fix applied to the other three publishers' pins above).
  pin.match(
    page,
    /new CustomEvent\("vissim:page-context", \{/,
    "expected calibration workspace to dispatch the EXACT event name FloatingAssistant listens for -- a suffixed rename must fail this pin",
  );
  pin.match(page, /route: "\/calibration"/, "expected the page context to publish under this page's own route");
  pin.match(
    page,
    /selectedScenarioIds: scenarioId \? \[scenarioId\] : \[\]/,
    "expected the SELECTED run's tool-vocabulary id -- the picker's own <option value>, never its display name -- to reach the assistant's selection context",
  );
  pin.match(
    page,
    /selectedScenarioNames: selectedScenario \? \[selectedScenario\.name\] : \[\]/,
    "expected the run's on-screen DISPLAY name to ride ALONGSIDE its id, never in place of it",
  );
});

contractTest("unit #50-e: the next-action box hands the user to the assistant, prefilled and node-specific", (pin) => {
  const table = read("components/calibration/NodeScoreTable.tsx");
  const detail = table.slice(table.indexOf("export function NodeScoreDetail"));

  // AUTHORITY: #50-e (2026-08-23) -- the next-action box used to be advice
  // with nowhere to go. MEASURED, not invented: this reuses the SAME
  // window-event handoff app/health/page.tsx and
  // components/dashboard/CurationBar.tsx already use to reach the
  // globally-mounted FloatingAssistant (components/AppShell.tsx) -- no new
  // mechanism, no new route.
  pin.match(
    table,
    /const ASSISTANT_HANDOFF_EVENT = "vissim:assistant-handoff";/,
    "expected the SAME handoff event name app/health/page.tsx and CurationBar.tsx dispatch -- a different string would not reach FloatingAssistant's listener",
  );
  pin.match(
    table,
    /window\.dispatchEvent\(new CustomEvent\(ASSISTANT_HANDOFF_EVENT, \{ detail: \{ prompt \} \}\)\);/,
    "expected the exact event shape FloatingAssistant listens for",
  );

  // THE CTA ITSELF, on the surface, inside the next-action box.
  pin.match(
    detail,
    /onClick=\{\(\) => requestAgentHandoff\(buildNextActionPrompt\(node\.node_id, nextAction\.text, t\)\)\}/,
    "expected the CTA to hand off through the shared mechanism, built from THIS node's id and THIS node's own next action",
  );
  pin.match(
    withoutFolds(detail),
    /\{t\("AI에게 물어보기", "Ask the AI", "AIに聞く"\)\}/,
    "expected the CTA to sit on the surface and reuse the health page's own handoff label, not a new one",
  );

  // NODE ID INTERPOLATED, not hardcoded -- the prompt builder must actually
  // splice `nodeId` into all three language templates, or a CTA opened from
  // node 7 could ask the assistant about node 4.
  const builderAt = table.indexOf("function buildNextActionPrompt");
  const builder = table.slice(builderAt, table.indexOf("\n}", builderAt));
  const nodeIdSplices = builder.match(/\$\{nodeId\}/g) ?? [];
  assert.ok(
    nodeIdSplices.length >= 3,
    `expected the node id interpolated into the ko, en AND ja templates -- found ${nodeIdSplices.length} splice(s)`,
  );
  pin.match(builder, /text\.ko/, "expected the ko template to carry the server's own ko next-action text, not a paraphrase");
  pin.match(builder, /text\.en/, "expected the en template to carry the server's own en next-action text");
  pin.match(builder, /text\.ja/, "expected the ja template to carry the server's own ja next-action text");
});

// ---------------------------------------------------------------------------
// Unit #57 — the #51 lock card's four NAMED reasons each get ONE action.
//
// User report, 2026-08-24: the four refusal codes were prose only. The pins
// below cover, per DECISIONS.md's 2026-08-24 threat-model note, CLASSES of
// mutation this suite can actually stop (careless drift and the shapes below,
// measured by running each one) rather than every re-expression a determined
// rewrite could reach:
//   * a chip's whole branch deleted (the closed-vocabulary set-equality pin);
//   * a chip pointed at the WRONG target (station name, href, or detail
//     field swapped for another code's);
//   * the chip file reading or restating the gate's own sentence instead of
//     leaving that to FlowRail;
//   * the cross-file handoff event name drifting (folded into #50-e's own
//     cross-file test above, which now names this file as its fifth
//     dispatcher).
// ---------------------------------------------------------------------------

contractTest("unit #57: the lock card renders exactly one action chip per reason it recognises, and none for the rest", (pin) => {
  const action = withoutComments(read("components/calibration/ParameterSearchRefusalAction.tsx"));

  // CLOSED VOCABULARY (DECISIONS.md 2026-08-24's own idiom against expression
  // bypass): every `refusal.code === "..."` branch this file has, as a SET.
  // Adding a fifth code, renaming one, or losing one changes this set and
  // fails the assertion below -- a `doesNotMatch`-per-code test would not
  // catch a fifth code appearing, and four separate `pin.match` calls would
  // not catch one of the four silently vanishing while the other three still
  // matched.
  const codes = [...action.matchAll(/refusal\.code === ([A-Z0-9_]+)\)/g)].map((m) => m[1]);
  assert.deepStrictEqual(
    [...codes].sort(),
    ["HOLDOUT_NOT_AVAILABLE", "LADDER_NOT_EXHAUSTED", "SIGNAL_STEP_UNVERIFIED", "TABLE_5_6_NOT_APPLIED"].sort(),
    `expected exactly the #51 gate's four navigable refusal codes, found: ${codes.join(", ")}`,
  );

  // The catch-all: a code this file does not recognise renders NOTHING, never
  // a guessed default action.
  //
  // `\r?\n`, NOT a bare `\n`, immediately before the closing brace -- the SAME
  // CRLF-tolerance `topLevelFunction` (above) already documents needing ("a
  // tool that rewrote one of these files with Windows line endings made the
  // boundary unfindable"). MEASURED 2026-08-25: this file is delivered with
  // CRLF line endings (the repo mixes both per-file -- NodeScoreTable.tsx and
  // app/calibration/page.tsx are CRLF, FlowRail.tsx is LF), and a bare `\n`
  // here made this exact pin fail against the ACTUAL delivered file while
  // every other #57 pin (which used `\s*` instead) stayed green.
  pin.match(
    action,
    /\r?\n\s*return null;\r?\n\}/,
    "expected an unrecognised refusal code to fall through to a bare `return null`",
  );

  // The chip file never reads `refusal.why` / `refusal.opens_when` -- those
  // sentences are FlowRail's job (rendered once, above where this component
  // is invoked). A chip that started reading them could start paraphrasing
  // the gate's own words instead of adding a short label beside them.
  pin.doesNotMatch(
    action,
    /refusal\.why|refusal\.opens_when/,
    "expected the action file to never touch the server's own sentence fields -- only `refusal.code` and `refusal.detail`",
  );
});

contractTest("unit #57: SIGNAL_STEP_UNVERIFIED navigates to the ⑥ signals station, with a same-page-only fallback", (pin) => {
  const action = withoutComments(read("components/calibration/ParameterSearchRefusalAction.tsx"));
  const block = sliceBetween(
    action,
    "refusal.code === SIGNAL_STEP_UNVERIFIED",
    "refusal.code === TABLE_5_6_NOT_APPLIED",
  );
  assert.ok(block, "expected a SIGNAL_STEP_UNVERIFIED branch before the TABLE_5_6_NOT_APPLIED one");

  // MUTATION-CHECKED (2026-08-25, builder): changing "signals" to "behaviour"
  // here (the ⑫ station, an easy typo one line below TABLE_5_6's own station
  // context) is caught by this pin and by NOTHING else in the suite -- no
  // other test names the ⑥ station's own key.
  pin.match(
    block,
    /onSelectStation\("signals"\)/,
    "expected the chip to switch to the SIGNALS station (§2.12.1 step ⑥), never another one",
  );
  pin.match(
    block,
    /href="\/genesis\/phases"/,
    "expected the no-callback fallback to link straight to the 현시도 review page",
  );
});

contractTest("unit #57: TABLE_5_6_NOT_APPLIED hands off to the assistant with the gate's own pending nodes, never a station jump", (pin) => {
  const action = withoutComments(read("components/calibration/ParameterSearchRefusalAction.tsx"));
  const block = sliceBetween(
    action,
    "refusal.code === TABLE_5_6_NOT_APPLIED",
    "refusal.code === LADDER_NOT_EXHAUSTED",
  );
  assert.ok(block, "expected a TABLE_5_6_NOT_APPLIED branch before the LADDER_NOT_EXHAUSTED one");

  // The roadmap row's own choice: a handoff, not `onSelectStation` -- the
  // reader is already inside the ⑫/behaviour station this evidence lives in.
  pin.doesNotMatch(block, /onSelectStation/, "expected NO station jump on this chip -- the reader is already there");
  pin.match(
    block,
    /const nodes = stringList\(detail\.nodes\);/,
    "expected the PENDING nodes to come from `detail.nodes` -- not `detail.blocking_nodes` (a different code's field)",
  );
  pin.match(block, /if \(nodes\.length === 0\) return null;/, "expected no chip when the gate named no pending node");
  pin.match(
    block,
    /requestAgentHandoff\(buildTable56PrescriptionPrompt\(nodes, citation, t\)\)/,
    "expected the handoff prompt built from THIS refusal's own nodes and citation",
  );

  // The prompt splices the server's OWN node ids into all three languages --
  // never a generic "some nodes" that could not tell the assistant which ones.
  const builderAt = action.indexOf("function buildTable56PrescriptionPrompt");
  const builder = action.slice(builderAt, action.indexOf("\n}", builderAt));
  const nodeSplices = builder.match(/\$\{nodes\}/g) ?? [];
  assert.ok(
    nodeSplices.length >= 3,
    `expected the joined node ids interpolated into the ko, en AND ja templates -- found ${nodeSplices.length}`,
  );
});

contractTest("unit #57: LADDER_NOT_EXHAUSTED selects the gate's FIRST blocking node, never a computed or hardcoded one", (pin) => {
  const action = withoutComments(read("components/calibration/ParameterSearchRefusalAction.tsx"));
  const block = sliceBetween(
    action,
    "refusal.code === LADDER_NOT_EXHAUSTED",
    "refusal.code === HOLDOUT_NOT_AVAILABLE",
  );
  assert.ok(block, "expected a LADDER_NOT_EXHAUSTED branch before the HOLDOUT_NOT_AVAILABLE one");

  pin.match(
    block,
    /const blockingNodes = stringList\(detail\.blocking_nodes\);/,
    "expected the blocking nodes to come from `detail.blocking_nodes` -- not `detail.nodes` (TABLE_5_6's own field)",
  );
  pin.match(block, /const firstBlockingNode = blockingNodes\[0\];/, "expected the FIRST node, not a sorted/filtered pick");
  pin.match(
    block,
    /if \(!firstBlockingNode \|\| !onSelectNode\) return null;/,
    "expected no chip with an empty blocking-node list or with no selection callback wired",
  );
  pin.match(
    block,
    /onSelectNode\(firstBlockingNode\)/,
    "expected the SAME onNodeClick-shaped callback NodeScoreTable and the map already use",
  );
});

contractTest("unit #57: HOLDOUT_NOT_AVAILABLE links to the observations page", (pin) => {
  const action = withoutComments(read("components/calibration/ParameterSearchRefusalAction.tsx"));
  // `sliceBetween` is a plain string `indexOf`, not a regex -- it cannot take
  // a `\r?\n` alternation, so the end marker was `"\n  return null;\n}"`
  // (matching the closing brace's exact formatting) until 2026-08-25, when
  // this file was measured delivered with CRLF line endings: the marker's
  // bare `\n` never occurs in the actual bytes (`\r\n` does), `indexOf`
  // returned -1, and `sliceBetween` fell back to `""` -- an EMPTY slice that
  // then failed the very next assertion, for a reason that had nothing to do
  // with whether a HOLDOUT_NOT_AVAILABLE branch exists. The catch-all's own
  // TEXT, "return null;", is exactly as unique a boundary (the HOLDOUT branch
  // itself never contains that string) and needs no newline at all to find.
  const block = sliceBetween(action, "refusal.code === HOLDOUT_NOT_AVAILABLE", "return null;");
  assert.ok(block, "expected a HOLDOUT_NOT_AVAILABLE branch before the file's own closing return null");

  pin.match(block, /href="\/observations"/, "expected the chip to link to the 실측 관측 page, and nowhere else");
});

contractTest("unit #57: the lock card wires the two navigation callbacks through to the chip renderer, unrenamed", (pin) => {
  const rail = withoutComments(read("components/calibration/FlowRail.tsx"));
  const track = topLevelFunction(rail, "ParameterSearchTrack");

  pin.match(
    track,
    /<ParameterSearchRefusalAction\s+refusal=\{refusal\}\s+t=\{t\}\s+onSelectStation=\{onSelectStation\}\s+onSelectNode=\{onSelectNode\}\s*\/>/,
    "expected every refusal to be handed to the action-chip component, with BOTH navigation callbacks threaded through unchanged",
  );
  // The server sentence and the chip coexist on the SAME reason -- a mutation
  // that replaced the sentence line with only the chip (or vice versa) fails
  // this, because the two are pinned as one bounded block. The gap between
  // them is a JSX comment (collapses to `{}` under `withoutComments`) plus a
  // `<br />`, so the window tolerates that rather than requiring bare
  // whitespace -- but stays short enough that it cannot be satisfied by a
  // chip anywhere else in the file.
  pin.match(
    track,
    /\{pick\(refusal\.opens_when, lang\)\}<\/span>[\s\S]{0,150}?<ParameterSearchRefusalAction/,
    "expected the chip to sit directly beside the server's own sentence, never replacing it",
  );

  const page = withoutComments(read("app/calibration/page.tsx"));
  pin.match(
    page,
    /onSelectStation=\{handleSelectStation\}\s*\n\s*onSelectNode=\{handleSelectScoringNode\}/,
    "expected the page to wire its OWN station/node handlers into the track -- no bespoke navigation invented for this unit",
  );
  pin.match(
    page,
    /const handleSelectScoringNode = useCallback\(\s*\(nodeId: string\) => \{\s*setScoringView\("nodes"\);\s*handleSelectStation\("scoring"\);\s*handleNodeClick\(nodeId\);/,
    "expected the node-select handler to force the SCORING station's node tab open before selecting the node -- otherwise the diagnosis card the chip promises is not on screen",
  );
});

contractTest(
  "unit #57: the server's own sentence renders unadorned -- nothing prepended, nothing appended",
  (pin) => {
    // ADJACENCY, not mere presence (refuter, 2026-08-25). The existing #51
    // pins only check that `refusal.why` and `refusal.opens_when` are READ
    // somewhere in this function -- never that nothing else sits beside them.
    // MEASURED: prepending a literal "참고: " immediately before
    // `{pick(refusal.why, lang)}` passed the full 214-test suite, because
    // every existing pin's `pick(refusal.why...)` reference was still
    // present and still reachable, just no longer the FIRST thing in the
    // <li>. This component's own header says "EVERY SENTENCE STAYS THE
    // SERVER'S" -- so both edges of the sentence pair are anchored here: the
    // `<li>` opening tag directly to `{pick(refusal.why, lang)}` (nothing but
    // whitespace between them), and the closing `</span>` of `opens_when`
    // directly to `<br />` (nothing appended after it either). A prefix OR a
    // suffix breaks one of the two anchors.
    const rail = withoutComments(read("components/calibration/FlowRail.tsx"));
    const track = topLevelFunction(rail, "ParameterSearchTrack");
    pin.match(
      track,
      /<li key=\{refusal\.code\}>\s*\{pick\(refusal\.why, lang\)\} <span className="text-slate-500">\{pick\(refusal\.opens_when, lang\)\}<\/span>\s*<br \/>/,
      "expected the server's why/opens_when sentence to be the SOLE content of the <li> -- nothing prepended before `why`, nothing appended after `opens_when`",
    );
  },
);

contractTest("unit #57: no refusal chip can smuggle a search start", (pin) => {
  // #51's own invariant -- no search-start control reachable from the LOCKED
  // branch -- is preserved TODAY only by the fact that every `<button>` in
  // this whole file happens to call something else. Nothing said it had to.
  // MEASURED (refuter, 2026-08-25): wiring the HOLDOUT_NOT_AVAILABLE chip's
  // `onClick` to `startParameterSearch(...)` instead of rendering a `Link`
  // passed the full suite AND typecheck -- #51's zero-button pin on
  // `ParameterSearchTrack` (unit #51: "the trigger card asks, and offers no
  // button it cannot honour") only ever swept THAT function, never this one,
  // and this file exists precisely because a real button had to live
  // somewhere outside it.
  // components/calibration/ParameterSearchStart.tsx is the SOLE intended
  // caller of `startParameterSearch` -- it is the only place the ask-first
  // cost sentence and the runner-wired measurement sit beside the button
  // that spends the search budget. A refusal chip on a LOCKED card reaching
  // that function directly would start a search the card is telling the
  // reader it CANNOT start.
  const action = withoutComments(read("components/calibration/ParameterSearchRefusalAction.tsx"));
  pin.doesNotMatch(
    action,
    /startParameterSearch/,
    "expected NO refusal chip to call startParameterSearch -- ParameterSearchStart.tsx is the sole caller, by #51's own ask-first/runner-wired design",
  );
});

contractTest("unit #57: every chip label is a real ko/en/ja triple, not a blank or a paraphrase", (pin) => {
  // THE #50-d CLASS OF GAP. `t(ko, en, ja?)` makes `ja` OPTIONAL in the type,
  // so a call that dropped or blanked it type-checks clean, and the existing
  // node-id-splice pin (LADDER_NOT_EXHAUSTED / TABLE_5_6_NOT_APPLIED) only
  // counts `${...}` interpolations -- it says nothing about which STRINGS
  // surround them. MEASURED (refuter, 2026-08-25): blanking a chip's `ja`
  // argument to `""` passed typecheck AND the full 214-test suite. Every
  // VISIBLE label and aria-label this file renders is pinned below as an
  // exact three-language triple, quote-boundary anchored (the #55b/#56
  // idiom) so neither a blank nor a reworded language argument can satisfy
  // it -- only the exact ko AND en AND ja text, in that order, can.
  const action = withoutComments(read("components/calibration/ParameterSearchRefusalAction.tsx"));

  pin.match(
    action,
    tripleArgs("⑥ 신호 단계로 이동", "Go to step ⑥ (signals)", "⑥信号段階へ移動"),
    "expected the SIGNAL station button's exact ko/en/ja triple",
  );
  pin.match(
    action,
    tripleArgs("현시도 검토 열기", "Open the 현시도 review", "現示図レビューを開く"),
    "expected the SIGNAL fallback link's exact ko/en/ja triple",
  );
  pin.match(
    action,
    tripleArgs(
      "Table 5-6 처방 요청 (노드 ${nodes.length}개)",
      "Ask for the Table 5-6 prescription (${nodes.length} node(s))",
      "Table 5-6処方を依頼 (ノード${nodes.length}件)",
      "`",
    ),
    "expected the TABLE_5_6 chip's aria-label exact ko/en/ja triple",
  );
  pin.match(
    action,
    tripleArgs(
      "AI에게 Table 5-6 처방 요청",
      "Ask the AI for the Table 5-6 prescription",
      "AIにTable 5-6処方を依頼",
    ),
    "expected the TABLE_5_6 chip's visible label exact ko/en/ja triple",
  );
  pin.match(
    action,
    tripleArgs(
      "노드 ${firstBlockingNode} 진단 보기",
      "See node ${firstBlockingNode}'s diagnosis",
      "ノード${firstBlockingNode}の診断を見る",
      "`",
    ),
    "expected the LADDER chip's exact ko/en/ja triple",
  );
  pin.match(
    action,
    tripleArgs("실측 관측 페이지로", "Go to observations", "実測観測ページへ"),
    "expected the HOLDOUT link's exact ko/en/ja triple",
  );
});

// #56 (b) -- DECISIONS.md "같은 날 추가 측정 — CTA는 후보 0 노드에 불가능한
// 행동을 지시한다": before this unit every target-less node got the IDENTICAL
// next-action sentence regardless of whether its counted approaches carried
// zero candidate vehicle inputs (naming one is impossible) or several
// (naming one is a human's choice, not this product's). The fix branches
// geh_ladder.py's own sentence server-side (_target_less_rung); this file's
// job stays exactly what it was -- print `next_action.text` verbatim and
// never grow an opinion of its own about which of the two sentences a node
// deserves.
contractTest("node score detail renders the server's branched next-action sentence verbatim (#56 (b))", (pin) => {
  const table = withoutComments(read("components/calibration/NodeScoreTable.tsx"));

  pin.match(
    table,
    /\{pick\(nextAction\.text, lang\)\}/,
    "expected the next-action box to print the SERVER's own text object, never a locally composed sentence",
  );
  pin.doesNotMatch(
    table,
    /NO_VEHICLE_INPUT_ON_APPROACH|MULTIPLE_VEHICLE_INPUT_CANDIDATES/,
    "expected the #56 (b) reason-code branch to live ONLY server-side (services/agent/geh_ladder.py's _target_less_rung) -- this file re-deriving which of the two sentences to show would let the card and the server disagree about the same node",
  );
});

// ---------------------------------------------------------------------------
// Unit #54 — 사람이 지정하는 수요 대상: the node the derivation REFUSES to
// resolve gets the one thing the refusal was asking for, a person's choice.
//
// AUTHORITY: the user, 2026-08-24, on the real client model — node 8 reads
// 분류 불가 / NO_DEMAND_TARGET on the calibration screen while
// backend/tools/calibration_loop_live_check.py reaches CAPACITY_LIMIT on the
// same node, because the harness hardcodes one human's graph analysis
// ({node 8 -> input 113, link 617}) that the product had nowhere to record.
//
// The failure THIS unit is one character away from is a picker that invents its
// own candidates, or that decides a status once the choice is made. Four pins:
//
//   1. every candidate on the control is the SERVER's (reality_check_targets'
//      own per-node list, the same one the store checks a choice against);
//   2. WHEN the control appears is the server's too — geh_ladder's own
//      NO_DEMAND_TARGET code, never a rung this file classified;
//   3. submitting decides nothing: the report and the rail are re-read, and the
//      node's new classification is whatever the server then says;
//   4. the act is stated in ONE line beside the button, and it says the word
//      that matters — this records an ASSUMPTION under your name.
// ---------------------------------------------------------------------------

contractTest("unit #54: the demand-target picker offers the SERVER's candidates and invents none", (pin) => {
  const table = read("components/calibration/NodeScoreTable.tsx");
  const pickerAt = table.indexOf("function DemandTargetPicker");
  assert.ok(pickerAt > 0, "expected the #54 picker to exist in the node detail's file");
  const picker = table.slice(pickerAt);

  // (1) THE CANDIDATES ARE THE SERVER'S. Rendered straight off the payload —
  // no filter, no sort, no client-side derivation from the shadow model. A
  // client-assembled list would show a modeller options the store then refuses,
  // which is the two-opinions failure in its most user-hostile form.
  //
  // UNIT #59 CHANGED WHAT IS OFFERED, and not who computes it. The user decided
  // that a link's 승용차/버스/트럭 trio is adjusted together with its mix
  // preserved, so the LINK is the unit of choice and the picker offers
  // link-GROUPS — two choices on the demo's node 8, where six raw inputs used
  // to be listed as six rivals. The folding is `reality_check_targets.
  // link_groups`' and is checked against the same list when the choice is
  // recorded; a group folded in this file would be exactly the second opinion
  // these pins exist to prevent.
  pin.match(
    picker,
    /const groups = demandTarget\?\.candidate_groups \?\? \[\];/,
    "expected the offered groups to come from the server payload, never from anything this file folded",
  );
  pin.match(
    picker,
    /groups\.map\(\(group\) => \(/,
    "expected every offered choice to be rendered from the server's own group list",
  );
  pin.match(
    picker,
    /group\.link_no/,
    "expected the LINK on each option -- since #59 the link is what gets named",
  );
  // AND ITS MEMBERS, under it. Choosing a link means moving every class on it,
  // so the classes are shown with the choice rather than discovered afterwards.
  pin.match(
    picker,
    /group\.members\.map\(\(member\) => \(/,
    "expected each group's vehicle-class members to be listed under it -- naming a link moves all of them",
  );
  pin.match(
    picker,
    /member\.vehicle_input_no/,
    "expected the input NUMBER on each member -- a modeller recognises it from Vissim",
  );
  // ...and the volume, which is what makes the list decidable rather than a set
  // of bare numbers. `null` prints as a WORD, never as 0: a zero here would read
  // as a measurement of no traffic. Pinned on BOTH levels, because the group's
  // total is `null` the moment any member's volume is unstated.
  pin.match(
    picker,
    /typeof group\.current_volume_total === "number"[\s\S]{0,200}?t\("교통량 미기재"/,
    "expected an unstated group total to print as a word, never as a fabricated 0",
  );
  pin.match(
    picker,
    /typeof member\.current_volume === "number"[\s\S]{0,200}?t\("교통량 미기재"/,
    "expected an unstated member volume to print as a word, never as a fabricated 0",
  );
  // THE FULL MEMBERSHIP IS SENT, exactly as it was shown. The server checks it
  // for set equality and refuses by name when a class arrived or left since, so
  // a trio nobody reviewed can never be adjusted under a signature given for a
  // different one — which only works if the client sends what it displayed.
  pin.match(
    picker,
    /vehicleInputNos: picked\.vehicle_input_nos,/,
    "expected the POST to assert the membership the reviewer was shown, not just the link",
  );

  // (2) WHEN IT APPEARS is the server's answer. geh_ladder's own code, off
  // `diagnosis.missing_evidence` -- not a rung this file classified, which would
  // put the control on nodes whose gap is something else entirely.
  pin.match(
    table,
    /const NO_DEMAND_TARGET = "NO_DEMAND_TARGET";/,
    "expected the picker's trigger to be geh_ladder's own missing-evidence code",
  );
  pin.match(
    picker,
    /\(node\.diagnosis\?\.missing_evidence \?\? \[\]\)\.includes\(NO_DEMAND_TARGET\)/,
    "expected the control to key off the server's missing-evidence list, never off a rung name",
  );
  // NO CANDIDATES, NO CONTROL -- a node whose rows do not resolve to a link
  // cannot name anything, and a picker there would offer a control that cannot
  // be used. An EXISTING mapping keeps the card alive after the gap closes, or a
  // named assumption would have no way to be withdrawn.
  pin.match(
    picker,
    /if \(groups\.length === 0 \|\| \(!missingTarget && !mapping\)\) return null;/,
    "expected no picker without candidates, and the card to survive once a mapping stands so it can still be revoked",
  );

  // (3) THE CLIENT DECIDES NOTHING. No status word is assigned here, and the
  // only thing that happens after a successful POST is a re-read.
  pin.match(picker, /await postCalibrationDemandTarget\(\{/, "expected the picker to POST through lib/api.ts");
  pin.match(picker, /onChanged\?\.\(\);/, "expected the picker to ask the page to RE-READ rather than to update anything itself");
  pin.doesNotMatch(
    withoutComments(picker),
    /CAPACITY_LIMIT|DEMAND_LEVEL|UNCLASSIFIABLE|setRung|rung =/,
    "expected no rung or status to be decided in the picker -- the server re-derives the node on the next read",
  );

  // (4) THE ACT, IN ONE LINE, and it says ASSUMPTION. #50-d evicted doctrine
  // paragraphs from this screen; what stays is what THIS button records, which a
  // reader about to put their name on something is entitled to know.
  const surface = withoutFolds(picker);
  pin.match(
    surface,
    /이 링크가 이 노드 총량을 좌우한다'는 가정이 로그인한 사용자의 이름·시각과 함께 기록됩니다/,
    "expected the act statement to say that naming records an ASSUMPTION under the signed-in name",
  );
  // #59: and the SECOND thing this act now commits to — the classes move
  // together at their current mix, and no single class is singled out. A
  // reviewer signing a link is signing for all of it, so the sentence beside
  // the button has to say so.
  pin.match(
    surface,
    /현재 구성비를 유지한 채 함께 조정되며, 어느 한 차종만 따로 조정하지 않습니다/,
    "expected the act statement to say the class trio moves together with its mix preserved",
  );
  pin.match(
    surface,
    /네트워크가 바뀌면 다시 묻습니다/,
    "expected the act statement to say the mapping expires with the network -- the staleness rule the server enforces",
  );
  pin.match(
    surface,
    /t\("진입 링크 지정", "Name the approach link", "接近リンクを指定"\)/,
    "expected the control to carry the user's own words for it (진입 링크 지정)",
  );
  // THE MAPPING THAT STANDS, on the surface: who and when, in their own words.
  pin.match(surface, /\{mapping\.named_by\}/, "expected the standing mapping to name its author on the surface");
  pin.match(surface, /\{mapping\.named_at\}/, "expected the standing mapping to carry its server timestamp");
  pin.match(surface, /\{mapping\.reason\}/, "expected the author's own stated reason to stay on the surface");
  pin.match(
    surface,
    /\{t\("지정 철회", "Revoke", "指定を撤回"\)\}/,
    "expected a standing mapping to be withdrawable from the same card",
  );

  // AND THE #50-e CTA COEXISTS. The two are different acts on the same gap --
  // assign the input directly, or hand the node to the assistant -- and #54 must
  // not have replaced one with the other.
  const detail = table.slice(table.indexOf("export function NodeScoreDetail"), pickerAt);
  pin.match(
    detail,
    /\{t\("AI에게 물어보기", "Ask the AI", "AIに聞く"\)\}/,
    "expected #50-e's next-action CTA to survive #54 -- the picker was added beside it, not over it",
  );
  pin.match(
    detail,
    /<DemandTargetPicker\s+node=\{node\}\s+demandTarget=\{demandTarget \?\? null\}/,
    "expected the picker to render inside the node detail, from the payload the page threaded in",
  );
});

// ---------------------------------------------------------------------------
// Unit #59 — 비례 공동 조정: the per-class split the reviewer approves.
//
// AUTHORITY: the user, 2026-08-24 (docs/phase2/DECISIONS.md), choosing (a)
// 셋을 비례로 함께 조정 — 승용차:버스:트럭 구성비 유지. Every approach link in
// the real client model carries a three-class trio, so the demand target is
// normally a COMPOSITE and an accepted proposal moves three vehicle inputs.
//
// The failure this unit is one character away from is a card that shows a
// division other than the one the plan would apply — either by recomputing the
// proportions client-side, or by hiding them behind the fold. Both make the
// reviewer approve something they did not see, which is the exact condition the
// user's rule was accepted under. Two pins, and they are opposite in sign:
// the split IS rendered from the server's own array, and it is NOT divided here.
// ---------------------------------------------------------------------------

contractTest("unit #59: the per-class split is the SERVER's array, on the surface", (pin) => {
  const table = read("components/calibration/NodeScoreTable.tsx");
  const pickerAt = table.indexOf("function DemandTargetPicker");
  const detail = table.slice(table.indexOf("export function NodeScoreDetail"), pickerAt);

  // THE SPLIT COMES OFF THE EVIDENCE the proposal already carries -- the same
  // array `fixes` put in `evidence.target.group.split` and the same numbers its
  // apply_edit_plan steps would write.
  pin.match(
    detail,
    /const group = \(target\.group \?\? null\) as CalibrationDemandGroupTarget \| null;/,
    "expected the grouping to be read off the proposal's own evidence block",
  );
  pin.match(
    detail,
    /const split = group\?\.split \?\? \[\];/,
    "expected the per-member split to come from the server, never from a division computed here",
  );
  pin.match(
    detail,
    /split\.map\(\(row\) => \(/,
    "expected every member of the split to be rendered from the server's own rows",
  );
  pin.match(
    detail,
    /\{fmt\(row\.current_volume, 0\)\} → \{fmt\(row\.proposed_volume, 0\)\} veh\/h/,
    "expected each class to show what it carries now and what it would carry -- the change being approved",
  );
  pin.match(
    detail,
    /row\.current_share/,
    "expected the mix being preserved to be shown as the number it is, not asserted in prose alone",
  );

  // ON THE SURFACE, NOT BEHIND THE FOLD. A division of demand one click away is
  // a division nobody approved.
  const surface = withoutFolds(detail);
  pin.match(
    surface,
    /t\(\s*"차종별 배분 \(구성비 유지\)"/,
    "expected the per-class split to sit on the reading surface, never inside the evidence fold",
  );
  pin.match(
    surface,
    /전부 적용되거나 전부 취소되며, 일부만 적용되지 않습니다/,
    "expected the card to say the trio applies all-or-nothing -- a partial apply is a mix nobody chose",
  );

  // AND NO CLIENT-SIDE ARITHMETIC. No proportion, no scaling, no re-derivation
  // of a member's share from the totals: the one thing that would let the card
  // and the plan disagree.
  //
  // THREAT MODEL, NAMED (refuter round 1, F5). This pin catches drift at the
  // RENDER POSITION -- an expression that recomputes a share where the row is
  // drawn. It does NOT catch an in-place mutation earlier in the component
  // (an `Object.assign` over the split array, say), because a static regex
  // over source cannot follow a value through aliasing. That is the recorded
  // contract-pin threat model working as designed, not a hole this pin should
  // grow to cover: these pins defend honest drift and named classes, and
  // catching a deliberate in-place recompute is a render-harness question
  // (DECISIONS.md, 2026-08-24 — the threat-model boundary entry). Named here
  // so the boundary is explicit rather than assumed.
  pin.doesNotMatch(
    withoutComments(detail),
    /current_volume_total\s*\*|\*\s*observed|proposed_volume\s*=|reduce\(/,
    "expected NO client-side split arithmetic -- the reviewer must see the numbers the plan applies",
  );
});

// ---------------------------------------------------------------------------
// Unit #59 (refuter F2) — the AGENT approval card must show EVERY step.
//
// MEASURED DEFECT: the chat-driven approval wire carries only tool/params/
// summary (no evidence block), `ApprovalCards.tsx` had no case for
// `apply_edit_plan`, and the generic fallback runs every param object through
// `formatParamValue`, which truncates at 80 characters. A three-class split's
// steps 2..N were therefore invisible on the card, and the reviewer approved a
// division of demand they could not see — the precise condition the user's
// decided rule was accepted under.
// ---------------------------------------------------------------------------

contractTest("unit #59: an edit plan's every step is on the approval card, never truncated", (pin) => {
  const cards = read("components/agent/ApprovalCards.tsx");

  // (1) THE DISPATCH REALLY REACHES THE BRANCH.
  //
  // Round 1's version of this pin asserted the branch appears somewhere above
  // the fallback, and appearing above is NOT the same as being reached: the
  // refuter (M13b) put an early `return` in front of it, turning the branch
  // into dead code, and this test stayed green. So the prefix before the branch
  // is asserted to contain no `return` at all except the single-write case's
  // own one -- a structural statement about control flow rather than a
  // positional one about text.
  const dispatch = withoutComments(functionSource(cards, "export function AgentActionDetail"));
  pin.match(
    dispatch,
    /if \(action\.tool === "apply_edit_plan"\) \{\s*return <EditPlanActionDetail/,
    "expected apply_edit_plan to have its own renderer in the action-detail dispatch",
  );
  const planBranchAt = dispatch.indexOf('if (action.tool === "apply_edit_plan")');
  const beforePlanBranch = planBranchAt === -1 ? dispatch : dispatch.slice(0, planBranchAt);
  const singleWriteReturn =
    /return <VehicleInputActionDetail action=\{action\} focus=\{focus\} modelEdited=\{modelEdited\} \/>;/;
  pin.match(
    beforePlanBranch,
    singleWriteReturn,
    "expected the single-write case to be the one branch that precedes the plan case",
  );
  pin.doesNotMatch(
    beforePlanBranch.replace(singleWriteReturn, ""),
    /\breturn\b/,
    "expected NOTHING ELSE to return before the apply_edit_plan branch -- an earlier return makes the branch dead code while every positional pin stays green (refuter M13b)",
  );

  const plan = functionSource(cards, "function EditPlanActionDetail");
  const planBody = withoutComments(plan);
  // A CRLF trap lived here: the old slice ended at a bare "\n}\n" marker, which
  // a Windows checkout never contains, so `plan` was "" and the two
  // `doesNotMatch` pins below passed over an empty string. See `functionSource`.
  pin.match(planBody, /return \(/, "expected the plan renderer's body to be readable at all");

  // (2) ONE ROW PER STEP, BY CONSTRUCTION.
  //
  // ROUND 2'S THEORY WAS WRONG, AND IT WAS MEASURED WRONG. That version pinned
  // ADJACENCY -- "a truncation is one more statement on the path from params to
  // rows, so require the statements to be consecutive". A truncation does not
  // have to be a statement. Three mutations survived the whole suite:
  //
  //   M-R2-1  `.filter((_, i) => i < 2)` folded into the TAIL of a pinned line
  //           (the old pin ended that line with `[^\n]*`, which is a hole).
  //   M-R2-2  `if (index >= 2) return null;` as the row callback's first line.
  //   M-R2-3  `if (steps.length > 2) return null;` inside the renderer itself.
  //
  // So the rule is now structural, and it is asserted over this ONE function's
  // extracted body rather than over the file (a file-wide ban on `.filter`
  // would be false -- narrowing is legitimate elsewhere in this card):
  //
  //   (a) `volumeSteps` is `steps.map(...)` and NOTHING else -- the whole line
  //       is pinned, so nothing can be appended to it;
  //   (b) the stated COUNT and the rendered ROWS read the SAME array, and each
  //       row carries its own params, so no second array can drift from it;
  //   (c) this body contains no array-shrinking call, no `return null`, no
  //       inequality on a step count or a row index, and no indexing into a
  //       step array. The one legitimate narrowing -- which baselines to fetch
  //       -- lives OUTSIDE the body, in `baselineInputNos`, where the worst it
  //       can cost a row is its "from" number.
  //
  // THE RESIDUAL LIMITS, MEASURED AND LISTED rather than papered over: a static
  // pin defends honest drift and NAMED classes of defect. It does not defend
  // against an arbitrary rewrite of this renderer (a hand-rolled loop that
  // pushes fewer rows; a helper in another file that returns a shorter array).
  // That boundary is the recorded decision in `docs/phase2/DECISIONS.md`
  // (2026-08-24, landed with #55b): static pins defend honest drift plus named
  // classes, and a render harness is a SEPARATE decision, not this test's to
  // make. What is below closes the classes the refuter actually executed, and
  // claims no more. Round 3's refuter measured what is still open, and it is
  // written down here rather than discovered again later:
  //
  //   R-1 (OPEN, by decision) A truncation that uses NO banned token: the
  //       measured one is `[0,1].includes(index)` in the row callback with an
  //       empty fragment for the rest -- no `.filter`, no `return null`, no
  //       inequality, no `steps[`. Sibling shapes (a `Set` of indices, a
  //       modulo) reach the same door. No token ban closes this class; only
  //       rendering the component and counting rows does, which is the
  //       separate decision above. It is also not honest drift: nobody caps a
  //       list by accident this way.
  //   R-2 (OPEN, narrow) A plan step that is NOT `set_vehicle_input_volume`
  //       renders through `agentActionDetail`, whose `formatParamValue`
  //       elides an OBJECT-valued param past 80 characters (measured: scalars
  //       pass through whole). The STEP still gets its own row -- which is
  //       this unit's claim -- but one such param VALUE can be shortened.
  //   R-3 (CLOSED THIS ROUND) The `showsCurrent` pin used to carry a `[^\n]*`
  //       wildcard through its own middle, so the two conditions that gate the
  //       arrow could be swapped for anything at all. The whole line is pinned
  //       below now.
  //   R-4 (CLOSED THIS ROUND) `baselineInputNos` was pinned by SIGNATURE only,
  //       so the one legitimate narrowing in this card could quietly widen or
  //       change which steps it claims a baseline for. Its body is pinned now.
  pin.match(
    planBody,
    /const rawSteps = \(action\.params \?\? \{\}\)\.steps;\r?\n\s*const steps = Array\.isArray\(rawSteps\)/,
    "expected the steps to come from the action's own params, with nothing in between -- the steps ARE the split",
  );
  pin.match(
    planBody,
    /const steps = Array\.isArray\(rawSteps\) \? \(rawSteps as \{ tool\?: unknown; params\?: unknown \}\[\]\) : \[\];\r?\n\s*const volumeSteps: PlanStepRow\[\] = steps\.map\(\(step\) => \{/,
    "expected volumeSteps to be steps.map(...) and NOTHING else -- the WHOLE line is pinned because the old pin's trailing wildcard is where a `.filter((_, i) => i < 2)` hid in plain sight (refuter M-R2-1)",
  );
  pin.match(
    planBody,
    /\{volumeSteps\.map\(\(step, index\) => \{/,
    "expected the list to be rendered from volumeSteps itself, never from something derived from it",
  );
  pin.doesNotMatch(
    planBody,
    /\.slice\(|\.shift\(|\.splice\(|\.pop\(|\.filter\(|\.findIndex\(|MAX_[A-Z_]*STEPS_SHOWN/,
    "expected NO array-shrinking call anywhere in this renderer's body -- the one legitimate narrowing (which baselines to fetch) is `baselineInputNos`, outside it. DELIBERATELY OVER-BROAD: a `.filter(` that shrinks nothing on screen would fail here too (refuter M-D4). Kept strict because the two errors are not symmetric -- a false positive stops a green suite and is fixed by moving the call out of this body, as `baselineInputNos` already was; a false negative is a card that silently shows a reviewer fewer changes than they are approving",
  );
  pin.doesNotMatch(
    planBody,
    /return null/,
    "expected NOTHING in this renderer to render nothing -- `if (index >= 2) return null` in the row callback, and `if (steps.length > 2) return null` in the body, both truncated the plan while every adjacency pin stayed green (refuter M-R2-2, M-R2-3)",
  );
  pin.doesNotMatch(
    planBody,
    /[sS]teps\.length\s*[<>]|\bindex\s*[<>]/,
    "expected no inequality on a step count or a row index -- that comparison is what a cap is made of",
  );
  pin.doesNotMatch(
    planBody,
    /[sS]teps\[/,
    "expected the rows to be the only array this renderer reads -- indexing back into the raw steps is a second array that can fall out of step with the first",
  );
  // (2b) THE COUNT AND THE ROWS ARE THE SAME ARRAY, IN ALL THREE LANGUAGES.
  // Stated from `volumeSteps`, not from `steps`: if anything ever did shrink
  // the rows, the card could no longer say "3 changes" above two of them -- it
  // would have to contradict the plan out loud, where a reader catches it
  // without this file's source.
  //
  // ROUND 3 PINNED THIS SENTENCE IN ko ALONE, and the refuter measured what
  // that is worth (M-D2): the en count could be changed to `length - 1` and the
  // en/ja all-or-nothing sentences could be swapped for each other's meaning,
  // and every pin here stayed green. A ko-only pin on a ko/en/ja triple is a
  // CLAUDE.md plumbing violation with a green test on top of it -- an en reader
  // is shown a number that does not match the rows they are looking at.
  //
  // So this is the #50-d idiom instead: a CLOSED, PARSED SET. Every t() call in
  // this renderer is inventoried below with all three of its languages, matched
  // through `tripleArgs` (which anchors on the quote characters, so only the
  // exact ko AND en AND ja text, in that order, satisfies one), and the NUMBER
  // of t() calls in the body is pinned to the size of the inventory. Anchored
  // both directions, like every other set-equality pin here: a sentence
  // entering fails (it is not in the inventory), a sentence leaving fails (the
  // count drops), and a reword in ANY of the three languages fails.
  const PLAN_CARD_TRIPLES: [string, string, string, '"' | "`"][] = [
    ["변경 단계가 없습니다.", "This plan lists no steps.", "変更ステップがありません。", '"'],
    [
      "${volumeSteps.length}개 변경을 한 번에 적용",
      "${volumeSteps.length} changes applied as one",
      "${volumeSteps.length}件の変更を一度に適用",
      "`",
    ],
    ["차량 입력 ${step.inputNo}", "Vehicle input ${step.inputNo}", "車両入力 ${step.inputNo}", "`"],
    ["시간 구간 ${step.intervalNo}", "interval ${step.intervalNo}", "期間${step.intervalNo}", "`"],
    [
      "전부 적용되거나 전부 취소됩니다 — 일부만 적용되는 상태는 없습니다.",
      "All of it applies or none of it does — there is no partially applied state.",
      "全て適用されるか全て取り消されるかで、一部だけが適用される状態はありません。",
      '"',
    ],
    [
      "현재값 미표시 · 이 대화에서 모델이 이미 편집되었습니다",
      "Current values hidden · this conversation has already edited the model",
      "現在値は非表示 · この会話で既にモデルを編集しています",
      '"',
    ],
  ];
  for (const [ko, en, ja, quote] of PLAN_CARD_TRIPLES) {
    pin.match(planBody, tripleArgs(ko, en, ja, quote), `expected the plan card's exact ko/en/ja triple for ${ko}`);
  }
  // `[^A-Za-z0-9_$.]` in front so this counts CALLS to the translator and not
  // the `t` at the end of some other identifier, nor `x.t(`.
  const planCardTCalls = (planBody.match(/[^A-Za-z0-9_$.]t\(/g) ?? []).length;
  pin.match(
    `planCardTriples=${planCardTCalls}/pinned=${PLAN_CARD_TRIPLES.length}`,
    /^planCardTriples=6\/pinned=6$/,
    "expected this renderer's translated sentences to be EXACTLY the six inventoried above -- a seventh (unpinned in en/ja, the #50-d class) fails here, and so does one disappearing",
  );
  pin.match(
    planBody,
    /if \(volumeSteps\.length === 0\) \{/,
    "expected the empty-plan case to test the rendered array too, so one array decides the count, the rows and the empty state",
  );
  // (2c) ...and the narrowing that IS legitimate is typed so it can never
  // become the row source: it returns ids, not rows.
  pin.match(
    cards,
    /function baselineInputNos\(rows: PlanStepRow\[\]\): string\[\] \{/,
    "expected the baseline selection to live outside the renderer and to return IDS -- a string[] cannot be mistaken for the list of rows",
  );
  // ...and WHAT it narrows to is pinned too (R-4 above). The signature alone
  // said "ids, not rows" and left the predicate free: widening it fetches a
  // baseline for a step that has no comparable "current" value, and narrowing
  // it drops a row's "from" number silently. Both are wrong in the same
  // direction -- a number on screen that was never measured for that row.
  pin.match(
    functionSource(cards, "function baselineInputNos"),
    /return rows\r?\n\s*\.filter\(\(row\) => row\.tool === "set_vehicle_input_volume" && row\.intervalNo === 1 && row\.inputNo !== ""\)\r?\n\s*\.map\(\(row\) => row\.inputNo\);/,
    "expected the one legitimate narrowing to be exactly 'interval-1 vehicle-input writes that name an input', mapped to their ids and nothing else",
  );
  pin.match(
    planBody,
    /const baselines = useVehicleInputBaselines\(baselineTrusted \? baselineInputNos\(volumeSteps\) : \[\]\);/,
    "expected the renderer to hand its rows to the baseline selection rather than narrowing them itself",
  );
  // (The all-or-nothing contract -- what makes N writes ONE act -- is the fifth
  // entry of the inventory above. It used to have its own ko-fragment pin here,
  // against the COMMENTED source, where this file's own explanation of the rule
  // could have satisfied it; the inventory pins all three languages against the
  // comment-stripped body instead.)

  // (3) THE "FROM" SIDE IS FETCHED OR ABSENT -- never invented.
  //
  // `?? null` is one character from `?? 0`, and the refuter (M12) made that
  // change and watched the suite pass. The difference on screen is a row
  // reading "0 → 327.28 veh/h": a measurement of no traffic, printed beside a
  // proposal, for an input whose baseline this card never managed to read. The
  // absence has to stay an absence, and the flag that gates the arrow has to be
  // the null test rather than anything derived from the number.
  pin.match(
    planBody,
    /const current = baselines\[step\.inputNo\]\?\.volume \?\? null;/,
    "expected an unfetched baseline to stay ABSENT -- `?? 0` prints a 'from 0 veh/h' this card never measured (refuter M12)",
  );
  // WHOLE LINE (R-3 above). The wildcard this replaces sat between the two ends
  // of the pin, so both conditions that gate the arrow -- the verb, and the
  // interval -- were free: a row for a LATER interval could show an interval-1
  // baseline as its "from" number, which is a measurement of a different hour.
  pin.match(
    planBody,
    /const showsCurrent = step\.tool === "set_vehicle_input_volume" && step\.intervalNo === 1 && current != null;/,
    "expected the arrow's left side to be shown only for an interval-1 vehicle-input write whose baseline actually came back -- all three conditions, pinned whole",
  );
  pin.doesNotMatch(
    planBody,
    /\?\?\s*0\b/,
    "expected no numeric fallback anywhere in this card -- 0 veh/h is a measurement, not an absence",
  );
  // ...AND THE SAME RULE ON THE PROPOSAL SIDE, which round 2 left unpinned.
  // Every pin above guarded the fetched "from" number; the refuter (M-R2-9)
  // changed the step's OWN volume fallback from `: null` to `: 0` and the whole
  // suite passed. On screen that reads "→ 0 veh/h": a proposal to shut an input
  // off, printed for a step whose params this card could not read as a number.
  // Both sides of the arrow have to be able to be ABSENT.
  pin.match(
    planBody,
    /volume: typeof params\.volume === "number" \? params\.volume : null,/,
    "expected an unreadable step volume to stay ABSENT -- `: 0` renders a proposal to zero an input the plan never said to zero (refuter M-R2-9)",
  );
  pin.match(
    planBody,
    /step\.volume == null \? "\?" : formatVolume\(step\.volume\)/,
    "expected the absent proposal volume to render as '?', never as a number",
  );
  pin.doesNotMatch(
    planBody,
    /\?[^?:\n]*:\s*0\b/,
    "expected no ternary in this card to fall back to 0 -- zero is a measurement, on either side of the arrow",
  );

  // (4) NO CLIENT-SIDE SPLIT ARITHMETIC. The card renders what the plan states;
  // a share recomputed on this side could disagree with what applies.
  pin.doesNotMatch(
    planBody,
    /current_share|proposed_volume\s*=|\/\s*total|reduce\(/,
    "expected the plan card to render the plan's own numbers, never a recomputed share",
  );

  // (5) AND THE 80-CHAR DUMP CAN NEVER BE THIS VERB'S RENDERER. `formatParamValue`
  // truncating an object is fine for a scalar-param tool and fatal for a plan,
  // so the generic switch must have no apply_edit_plan case to fall into.
  const generic = functionSource(cards, "export function agentActionDetail");
  pin.match(generic, /switch \(action\.tool\) \{/, "expected the one-line switch to be readable at all");
  pin.doesNotMatch(
    generic,
    /case "apply_edit_plan"/,
    "expected apply_edit_plan NOT to be rendered by the one-line string switch -- it needs rows",
  );
  pin.match(
    cards,
    /const PARAM_VALUE_MAX_CHARS = 80;/,
    "expected the truncation constant to still exist -- this pin is about what it may NOT render",
  );
});

contractTest("unit #54: one candidate list per screen, threaded down to both node-detail call sites", (pin) => {
  const page = withoutComments(read("app/calibration/page.tsx"));

  // ONE READ, threaded DOWN. The node detail renders in two places on this
  // screen (the scoring grid and the per-station node context); a fetch inside
  // the component would let the same node offer two different candidate lists.
  pin.match(
    page,
    /fetchCalibrationDemandTargets\(\)/,
    "expected the page to read the candidates once, not the detail component to fetch its own",
  );
  pin.doesNotMatch(
    withoutComments(read("components/calibration/NodeScoreTable.tsx")),
    /fetchCalibrationDemandTargets/,
    "expected the detail to be HANDED its candidates -- two fetches is two lists",
  );
  for (const call of [
    /demandTarget=\{demandTargets\?\.nodes\?\.\[activeNode\.node_id\] \?\? null\}\s*\n\s*onDemandTargetChanged=\{handleProposalResolved\}/,
    /demandTarget=\{demandTargets\?\.nodes\?\.\[activeNode\.node_id\] \?\? null\}\s*\n\s*onDemandTargetChanged=\{onDemandTargetChanged\}/,
  ]) {
    pin.match(page, call, "expected BOTH node-detail call sites to thread the same candidate payload");
  }

  // THE RE-READ IS THE WHOLE MECHANISM. Naming an input changes the REPORT (the
  // node's outcome, its D4 verdict and its rung), not only the rail -- so the
  // nonce that a mapping bumps has to be part of the report's request key too.
  // A screen that re-read only the rail would show the mapping recorded and the
  // node still 분류 불가 beside it.
  pin.match(
    page,
    /const reportKey = `\$\{scenarioId\}\|\$\{railNonce\}`;/,
    "expected the report's request key to include the nonce a demand-target change bumps",
  );
  pin.match(
    page,
    /const demandKey = `demand\|\$\{railNonce\}`;/,
    "expected the candidate list to be re-read on the same nonce",
  );
  pin.match(
    page,
    /resolve\(reportLoaded, reportKey, \{ idle: !scenarioId \}\)/,
    "expected the report to be resolved against that key -- a stale key silently keeps the pre-naming report on screen",
  );
});

// ---------------------------------------------------------------------------
// Unit #53 — 단계별 서명 증언: the nine steps with no machine evidence get an
// ACTION, and the action is a SIGNATURE rather than a checkbox.
//
// The failure this whole unit is one character away from is "mark as done". The
// pins below are aimed at the four things that keep it from being that:
//
//   1. WHICH steps may be signed is the server's flag, not a list kept here;
//   2. the browser cannot say WHO signed or WHEN — those are not on the body;
//   3. what a signature DOES to a step is the rail's answer, re-read after the
//      POST (pinned in the "never computed on the client" test above);
//   4. the 수동 확인 영역 promise (doctrine item D9) is untouched — a signed
//      step still is not shown as done, and the sentence that says so is still
//      unfolded on the station it belongs to.
// ---------------------------------------------------------------------------

contractTest("unit #53: every station with no machine evidence carries a signature control, and the SERVER says which steps", (pin) => {
  const page = withoutComments(read("app/calibration/page.tsx"));

  // THE FLAG, not a list. A client-side copy of ATTESTABLE_STEPS would be a
  // second opinion about which steps carry machine evidence, and it would drift
  // the first time one of them gained an artifact.
  pin.match(
    page,
    /steps\.filter\(\(step\) => step\.attestable\)/,
    "expected the signable steps to be filtered by the server's own `attestable` flag",
  );
  pin.doesNotMatch(
    page,
    /\[\s*2,\s*3,\s*4,\s*5,\s*8,\s*9,\s*10,\s*11,\s*13\s*\]/,
    "expected NO client-side copy of the signable-step list -- that set is services/calibration_rail.ATTESTABLE_STEPS",
  );

  // Every station whose steps have no machine evidence renders the rows. All
  // four, named, because a station that quietly lost them would leave its steps
  // exactly as unanswerable as they were before this unit.
  for (const station of ["transit", "demand", "assignment", "manual"]) {
    pin.match(
      page,
      new RegExp(`activeStation === "${station}" && \\(`),
      `expected the ${station} station to have a body of its own`,
    );
  }
  const signatureRows = page.match(/<StepSignatures\b/g) ?? [];
  assert.equal(
    signatureRows.length,
    4,
    `expected the signature rows at all four stations with signable steps (transit, demand, assignment, manual) -- found ${signatureRows.length}`,
  );

  // AND THE STATION'S OWN ACTION BESIDE THEM. Each of the three single-step
  // stations owes something more specific than "sign for it", and what it owes
  // is the thing its evidence gap actually points at.
  pin.match(page, /href="\/genesis\/demand"/, "expected ⑩ to link to where demand inputs are actually built");
  pin.match(page, /href="\/docs"/, "expected ⑨ to link to the doctrine that says why this product measures nothing for it");
  pin.match(
    page,
    /requestAgentHandoff\(buildRouteSplitPrompt\(step, lang, t\)\)/,
    "expected ⑪ to hand the user to the assistant, prefilled from THIS step",
  );

  // The prompt is built from the STEP, in all three languages -- same rule as
  // #50-e's node-id splice: a CTA opened on ⑪ must not ask about some other step.
  const builderAt = page.indexOf("function buildRouteSplitPrompt");
  const builder = page.slice(builderAt, page.indexOf("\n}", builderAt));
  const splices = builder.match(/\$\{number\}/g) ?? [];
  assert.ok(
    splices.length >= 3,
    `expected the step number interpolated into the ko, en AND ja templates -- found ${splices.length} splice(s)`,
  );
  pin.match(builder, /stepLabel\(step, lang\)/, "expected the prompt to use the SERVER's label for the step, not a paraphrase");
});

contractTest("unit #53: the browser cannot say who signed, when, or that a step is done", (pin) => {
  const api = read("lib/api.ts");

  for (const binding of ["postCalibrationStepAttestation", "revokeCalibrationStepAttestation"]) {
    pin.match(
      api,
      literalPattern(`export async function ${binding}(`),
      `expected a shared binding for ${binding} -- a bare fetch would skip withWorkspaceHeader and sign against the DEFAULT project`,
    );
  }

  // THE BODY, exactly. The attester is the session and the clock is the
  // server's; the backend's request model forbids extra fields, so sending
  // either is a 422 at the door -- and this pin is what stops the client from
  // sending one in the first place and getting a confusing 422 instead of a
  // signature.
  // Sliced by LENGTH, not to the next `\n}` -- these two bindings take an
  // inline object type, so their parameter list closes with a line-leading `}`
  // and a to-the-brace slice would stop before the body it is checking.
  const bindingAt = api.indexOf("export async function postCalibrationStepAttestation(");
  assert.ok(bindingAt > 0, "slice sanity: the signing binding must exist");
  const binding = api.slice(bindingAt, bindingAt + 900);
  pin.match(
    binding,
    /JSON\.stringify\(\{ step_number: params\.stepNumber, reason: params\.reason \}\)/,
    "expected the POST body to be exactly the two things a caller decides: which step, and why",
  );
  pin.doesNotMatch(
    binding,
    /attested_by|attested_at/,
    "expected NO attester and NO timestamp on the body -- a claim about who said what, dated by the client, is not an attestation",
  );

  // A REVOCATION IS A SIGNED ACT TOO, with a reason of its own.
  const revokeAt = api.indexOf("export async function revokeCalibrationStepAttestation(");
  assert.ok(revokeAt > 0, "slice sanity: the revocation binding must exist");
  const revoke = api.slice(revokeAt, revokeAt + 900);
  pin.match(revoke, /JSON\.stringify\(\{ reason: params\.reason \}\)/, "expected a revocation to carry its own reason");
});

contractTest("unit #53: a reason is mandatory on both acts, and the form states what signing records", (pin) => {
  const page = read("app/calibration/page.tsx");
  const surface = withoutFolds(withoutComments(page));

  // The server refuses an empty reason by name; the form must not let a reader
  // discover that by round trip. Both the disabled submit and the sentence that
  // explains the disablement are on the surface.
  pin.match(
    surface,
    /disabled=\{busy \|\| reason\.trim\(\)\.length === 0\}/,
    "expected the submit to be disabled until a reason is typed -- an empty one is a named refusal server-side",
  );
  pin.match(
    surface,
    /\{t\("이유를 적어야 합니다\.", "A reason is required\.", "理由の記入が必要です。"\)\}/,
    "expected the form to say WHY the button is disabled, in all three languages",
  );

  // WHAT THE ACT RECORDS, beside the control that performs it -- the 현시도
  // confirm form's own sentence, one screen over. This is an act statement, not
  // doctrine: it describes what this button does to this project's files. Why a
  // signature is never 완료 is the knowledge file's business (/docs), and the
  // #50-d eviction sweep is what holds that line.
  pin.match(
    surface,
    /로그인한 사용자의 이름과 시각, 그리고 지금 네트워크 파일의 해시가 함께 기록됩니다/,
    "expected the signing form to state what is recorded, on the surface",
  );
  pin.match(
    surface,
    /철회해도 기록은 지워지지 않습니다/,
    "expected the revocation form to state that nothing is deleted -- append-only is a promise a reader is entitled to before withdrawing",
  );
  for (const sentence of [
    "Confirming records the signed-in user's name",
    "Revoking deletes nothing",
    "確認すると、ログイン中の利用者名",
    "撤回しても記録は消えません",
  ]) {
    pin.match(surface, literalPattern(sentence), `expected the ${sentence.slice(0, 18)}… sentence in every language`);
  }
});

contractTest("unit #53: the signature row shows who signed and folds only its provenance", (pin) => {
  const page = read("app/calibration/page.tsx");
  const surface = withoutFolds(withoutComments(page));

  // WHO, WHEN, AND THEIR OWN WORDS -- on the surface. The reason is the
  // justification of the state beside it, which is exactly why `step.basis`
  // stayed on the surface in #50-c; a signature whose reason was folded away
  // would be a name and a date, which is the tick this record type replaces.
  pin.match(surface, /<b>\{attestation\.attested_by\}<\/b>/, "expected the attester's name on the surface, in bold");
  pin.match(surface, /\{attestation\.attested_at\}/, "expected the date it was signed on the surface");
  pin.match(surface, /\{attestation\.reason\}/, "expected the signer's own stated reason on the surface");

  // 재확인 필요 -- the SERVER's staleness answer, rendered as a state a reader
  // acts on. Never computed here: `stale` is the server comparing the .inpx it
  // was signed against with the one on disk now.
  pin.match(
    surface,
    /attestation\?\.stale &&/,
    "expected the stale state to be read off the server's flag",
  );
  pin.match(
    surface,
    /\{t\("재확인 필요", "Re-confirm", "再確認が必要"\)\}/,
    "expected a stale signature to say so where the control to renew it is",
  );

  // PROVENANCE FOLDS, the same split #50-c made everywhere else on this screen.
  pin.doesNotMatch(
    surface,
    /inpx_sha256/,
    "expected the network hash to live inside the fold -- it is provenance, and a mono hash on the surface is the exact shape the user's 2026-08-20 decision evicted",
  );
  pin.match(
    withoutComments(page),
    /\{t\("자세히 \(서명 이력\)", "More \(the signing history\)", "詳細（署名の履歴）"\)\}/,
    "expected the signing history to be reachable in one click -- append-only is worth nothing if nobody can read the appended events",
  );
});

contractTest("unit #53: the 수동 확인 영역 promise survives the signature control", (pin) => {
  // D9 IS THE ONE DOCTRINE SENTENCE THAT STAYS ON THIS SCREEN (the #50-d ③
  // ledger below owns that rule). #53 adds a control to the very station it
  // belongs to, which is exactly the edit that would drop it by accident -- so
  // it is pinned again HERE, inside the station branch this unit rewrote,
  // together with the rows that now sit under it.
  const page = withoutComments(read("app/calibration/page.tsx"));
  const manualAt = page.indexOf('activeStation === "manual" && (');
  assert.ok(manualAt > 0, "slice sanity: the manual station's branch must exist");
  const manual = page.slice(manualAt, manualAt + 1600);

  pin.match(
    withoutFolds(manual),
    /이 제품은 위 단계들을 측정하지 않습니다 — 완료로 표시하지 않습니다/,
    "expected the manual station's promise to stay UNFOLDED above the new controls -- a signature does not make this product measure anything",
  );
  pin.match(
    manual,
    /<StepSignatures\b/,
    "expected the six manual-check steps to carry the signature rows -- they are the steps with the least evidence and the most need to say a person did the work",
  );
  const promiseAt = manual.indexOf("이 제품은 위 단계들을 측정하지 않습니다");
  assert.ok(
    promiseAt >= 0 && promiseAt < manual.indexOf("<StepSignatures"),
    "the promise must be read BEFORE the control it qualifies, not underneath it",
  );
});

/**
 * One entry of FlowRail's `STATUS_LABELS` / `STATUS_CHIP` tables, as SOURCE TEXT.
 *
 * Reading the entries out instead of regex-ing for a literal is what makes the
 * DIFFERENCE assertions below possible: a pin can only say "this string is
 * still here", and the claim that actually matters — that two statuses do not
 * render the same — is a comparison between two entries, so both have to be
 * extracted before either can be checked.
 *
 * Fails closed: a renamed table, a missing status, or a table whose closing
 * `};` moved all raise here by name rather than yielding an empty string that
 * would make every comparison downstream vacuously true.
 */
function statusTableEntry(source: string, table: string, status: string): string {
  const at = source.indexOf(`const ${table}`);
  assert.ok(at > 0, `slice sanity: FlowRail.tsx must declare ${table}`);
  const end = source.indexOf("\n};", at);
  assert.ok(end > at, `slice sanity: ${table} must close with a line-leading };`);
  const match = new RegExp(`^\\s*${status}: (.+?),?\\s*$`, "m").exec(source.slice(at, end));
  assert.ok(match, `${table} carries no entry for ${status}`);
  return match![1].replace(/,$/, "").trim();
}

/** The double-quoted strings in one such entry, unescaped-as-written. */
function tsStringsIn(entry: string): string[] {
  return [...entry.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((match) => match[1]);
}

contractTest("unit #53: the sixth status word is not a second spelling of 완료", (pin) => {
  // AUTHORITY: the refuter, 2026-08-24. MEASURED, on a suite that was green:
  //
  //   * relabelling CONFIRMED_BY_PERSON to ["완료됨", "Done", "完了"] -- en and
  //     ja BYTE-IDENTICAL to DONE's own labels -- passed 195/195;
  //   * separately, refilling its chip with DONE's tint passed 195/195 too.
  //
  // Every pin this unit shipped asserted the KEY existed. A key is not a word.
  // The entire reason CONFIRMED_BY_PERSON exists rather than reusing DONE is
  // that a reader must never mistake "a person signed for this" for "an
  // artifact proved this" -- and that distinction lives nowhere in the code
  // except the label a reader reads and the colour it sits in. Both are pinned
  // LITERALLY here, and both are additionally asserted DIFFERENT from DONE's,
  // because a literal pin alone is satisfied by an edit that updates the pin
  // and the label together into the same word DONE already uses.
  const rail = withoutComments(read("components/calibration/FlowRail.tsx"));
  const done = tsStringsIn(statusTableEntry(rail, "STATUS_LABELS", "DONE"));
  const confirmed = tsStringsIn(statusTableEntry(rail, "STATUS_LABELS", "CONFIRMED_BY_PERSON"));

  assert.equal(done.length, 3, "slice sanity: DONE must be a ko/en/ja triple to compare against");
  assert.deepEqual(
    confirmed,
    ["확인됨(사람 증언)", "Confirmed (human attestation)", "確認済み（人の証言）"],
    "the sixth status word's label triple is the whole distinction this unit ships -- it says WHOSE claim it is, in every language",
  );

  // PER LANGUAGE, never per triple. The refuter's mutation left ko different
  // and made en and ja identical to DONE's, so a whole-triple comparison would
  // have passed it in two of the three languages a client reads this in.
  for (const [index, language] of (["ko", "en", "ja"] as const).entries()) {
    assert.notEqual(
      confirmed[index],
      done[index],
      `CONFIRMED_BY_PERSON and DONE render the SAME ${language} label (${confirmed[index]}). ` +
        "A signature is not a measurement, and a status word a reader cannot tell from 완료 is the " +
        "false completion this whole screen is arranged against",
    );
  }

  // THE OTHER DIRECTION. Difference alone is satisfied by two different words
  // neither of which says a PERSON is behind this one, so each language must
  // still carry the term that names the attester's involvement.
  pin.match(
    rail,
    /"DONE",\s*"CONFIRMED_BY_PERSON",/,
    "expected STATUS_ORDER to keep the SERVER's order (calibration_rail.STATUSES lists the signature word straight after DONE) -- the tally is printed in it",
  );
  assert.match(confirmed[0], /사람/, "the ko label must name that a PERSON is the source of this status");
  assert.match(confirmed[1], /attestation/i, "the en label must name that this is an attestation, not a verdict");
  assert.match(confirmed[2], /証言/, "the ja label must name that this is testimony, not a measurement");
});

// Style pin in a test of its own, per this file's rule about keeping brittle
// restyle pins away from behavioural ones.
contractTest("unit #53: the CONFIRMED_BY_PERSON chip is emerald, and is NOT DONE's chip", (pin) => {
  // The second of the refuter's two surviving mutations (2026-08-24): refilling
  // this chip with `bg-emerald-50` -- DONE's own tint -- made the two statuses
  // visually identical and failed nothing. At chip size the FILL is what a
  // reader distinguishes, so the fill is compared on its own as well as the
  // whole class string: an edit that changes only the border would otherwise
  // converge the two while keeping the strings unequal.
  const rail = withoutComments(read("components/calibration/FlowRail.tsx"));
  const done = statusTableEntry(rail, "STATUS_CHIP", "DONE");
  const confirmed = statusTableEntry(rail, "STATUS_CHIP", "CONFIRMED_BY_PERSON");

  assert.equal(
    confirmed,
    '"border-emerald-200 bg-surface text-emerald-700"',
    "the signed-step chip is emerald UNFILLED -- the pass family (three-colour rule) told apart from DONE by weight, never by a fourth hue",
  );
  pin.match(
    confirmed,
    /emerald/,
    "expected the signed-step chip to stay in the emerald family -- it is a positive state, and this screen has exactly one hue for that",
  );
  assert.notEqual(
    confirmed,
    done,
    "CONFIRMED_BY_PERSON and DONE render the SAME chip. A person's signature must not be paintable as a measured completion",
  );

  const fill = (chip: string) => (/\bbg-[a-z0-9-]+/.exec(chip) ?? ["(no bg- token)"])[0];
  assert.notEqual(
    fill(confirmed),
    fill(done),
    `CONFIRMED_BY_PERSON and DONE share the fill ${fill(done)} -- the tint is what a reader tells them apart by at chip size`,
  );
});

contractTest(
  "unit #50-e: diagnosis prose and delay evidence move behind the fold, and the next-action box stays on the surface",
  (pin) => {
    const table = read("components/calibration/NodeScoreTable.tsx");
    const detail = table.slice(table.indexOf("export function NodeScoreDetail"));

    // AUTHORITY: the user, 2026-08-23 -- "diagnosis prose duplicates the
    // next-action box; delay evidence is developer info." BOTH DIRECTIONS, per
    // moved string, same shape as the #50-c ledger above: still in the file,
    // and no longer on the surface.
    const moved = (pattern: RegExp, what: string) => {
      pin.match(detail, pattern, `${what} must still exist in the node detail -- #50-e moved it, it did not delete it`);
      pin.doesNotMatch(
        withoutFolds(detail),
        pattern,
        `${what} must not render on the surface -- #50-e (2026-08-23): diagnosis prose duplicates the next-action box; delay evidence is developer info`,
      );
    };

    moved(/pick\(node\.diagnosis\.why, lang\)/, "the diagnosis.why paragraph");
    moved(/\{t\("지체 증거", "Delay evidence", "遅延の根拠"\)\}/, "the delay-evidence line");

    // THE NEXT-ACTION BOX IS THE EXCEPTION -- it is the ACTION, not developer
    // info or duplicated prose, so #50-e keeps it on the surface (and gives it
    // the CTA pinned above).
    pin.match(
      withoutFolds(detail),
      /\{pick\(nextAction\.text, lang\)\}/,
      "expected the next-action sentence to stay ON the surface -- unlike diagnosis.why and delay evidence, it is the thing a reader acts on",
    );
  },
);

contractTest("unit #50-c: the rail's chips are the step chips, and a zero badge renders nothing", (pin) => {
  const rail = read("components/calibration/FlowRail.tsx");

  // THE WHOLE-RAIL TALLY IS GONE, by the user's own reading of it (2026-08-20:
  // "this stat does not help", roadmap row #50-d ⑥). #50-c had turned it from a
  // mono sentence into chips; #50-d removed it, because it counted thirteen rows
  // that are visible below it wearing those same chips. The pins that required
  // it are removed with that authority recorded, and the pin that survives is
  // the one that still has a subject: the COLLAPSED manual row's tally, which
  // describes six steps a reader cannot see (#50-b's rule that a folded group
  // states its own states).
  pin.match(
    rail,
    /\{statusTally\(steps\)\.map\(\(entry\) => \(/,
    "expected the collapsed 수동 확인 영역 row to keep stating its six steps' statuses",
  );
  pin.match(
    rail,
    /rounded-full border px-1\.5 py-0\.5 text-\[14px\] font-semibold \$\{statusChipClass\(entry\.status\)\}/,
    "expected that tally to reuse the step chip's own class, not a second palette",
  );
  pin.doesNotMatch(
    withoutComments(rail),
    /rail\.counts\[status\]/,
    "expected the whole-rail status tally to stay removed (#50-d, user 2026-08-20: 'this stat does not help')",
  );

  // A GAP THIS UNIT FOUND, and closed here rather than leaving named-only.
  // "A zero proposal badge renders NOTHING" has been a stated property of this
  // component since #50-b — a "0" badge reads as a state rather than as an
  // absence — and it had no pin at all: the #50-b tests execute the badge
  // ARITHMETIC in lib/calibrationStations.ts and never asked what the component
  // does with the number. Measured by mutation 2026-08-20: relaxing the guard
  // to `count < 0` left every existing test green. #50-c re-typed this badge, so
  // the property gets its pin on the way past.
  pin.match(
    rail,
    /function ProposalBadge\(\{ count, t \}: \{ count: number; t: Translate \}\) \{\s*\n\s*if \(count <= 0\) return null;/,
    "expected a zero proposal badge to render nothing at all -- a '0' badge reads as a state, not as an absence",
  );

  // DOCTRINE IS REACHABLE, not deleted -- and since #50-d it is reachable at
  // /docs rather than from an ⓘ on this rail. These two pins used to require the
  // tooltip; they now require the same sentences in the two files that own them.
  // The "#50-d ③" ledger below is what makes that a two-directional claim, by
  // pinning both of them ABSENT from every calibration surface file.
  pin.match(
    readCalibrationDoctrine(),
    /증거가 없는 단계는 완료로 표시하지 않고/,
    "expected the no-DONE-from-silence promise to survive verbatim in the knowledge file",
  );
  pin.match(
    read("lib/help-content.tsx"),
    /증거가 없는 단계는 완료로 표시하지 않고/,
    "expected /docs to render that promise, from the shared doctrine module",
  );
  pin.match(
    readCalibrationDoctrine(),
    /채점·진단은 MRWA 단계 아님/,
    "expected the not-an-MRWA-step caption to survive verbatim in the knowledge file",
  );
  pin.doesNotMatch(withoutComments(rail), /MRWA 단계 아님/, "expected the caption to have LEFT the rail row, not been duplicated");
});

contractTest("unit #50-c: the base-model tab leads with its verdict and folds every other section", (pin) => {
  const station = read("components/calibration/BaseModelStation.tsx");

  // THE ORDER IS THE POINT. The tab used to answer its own question last: a
  // reader scrolled a workbook card, a map, four donuts, an R² scatter and a
  // page of travel-time charts before reaching the sentence that says whether
  // this base model may be used.
  const verdictAt = station.indexOf("{buildAnalysisText({ countStats");
  const criteriaAt = station.indexOf("<CriteriaCoverageCard");
  const firstFoldAt = station.indexOf("<BaseModelSection");
  assert.ok(verdictAt > 0 && criteriaAt > 0 && firstFoldAt > 0, "expected a verdict, a criteria card and at least one folded section");
  assert.ok(verdictAt < firstFoldAt, "the overall verdict must render BEFORE the first folded section");
  assert.ok(criteriaAt < firstFoldAt, "the §5.6.3 criteria coverage must render before the first folded section");

  // ...and it must be on the SURFACE, not merely early.
  const surface = withoutFolds(withoutComments(station));
  pin.match(surface, /buildAnalysisText\(\{ countStats/, "expected the verdict sentence to render unfolded");
  pin.match(surface, /<CriteriaCoverageCard/, "expected the criteria/threshold table to render unfolded");
  pin.match(
    surface,
    /<ValidationCategorySelector category=\{category\} onChange=\{handleCategoryChange\} t=\{t\} \/>/,
    "expected the Category selector beside the verdict it is measured against, not inside a fold",
  );
  // The §5.6.3.1 override still flips the whole verdict -- the move must not
  // have quietly detached the panel from the evaluation that colours it.
  pin.match(
    surface,
    /analysisPanelClass\(countStats, travelStats, criticalEvaluation\)/,
    "expected the moved verdict panel to keep reading the critical-intersection evaluation",
  );

  // THREE sections, each folded, each stating its OWN numbers in the summary --
  // a fold whose summary is prose is a fold nobody can decide whether to open.
  assert.equal(
    (station.match(/<BaseModelSection\b/g) ?? []).length,
    3,
    "expected exactly three folded sections: setup, counts, travel time",
  );
  pin.match(station, /function BaseModelSection\(\{/, "expected one fold component, not three hand-rolled ones");
  pin.match(station, /summary=\{[\s\S]{0,400}?formatPct\(countStats\.under5Pct\)/, "expected the counts fold to state its own GEH share");
  pin.match(station, /summary=\{[\s\S]{0,300}?formatPct\(travelStats\.inBandPct\)/, "expected the travel-time fold to state its own in-band share");
  pin.match(station, /summary=\{[\s\S]{0,300}?workbook\.counts\.length/, "expected the setup fold to state what the workbook holds");

  // AND THE PROMOTED SURFACE OBEYS THE THREE-COLOUR RULE. Promoting the verdict
  // brought two amber boxes to the top of the tab with it -- the "not enough
  // data to judge" panel and the workbook-warnings notice -- and amber is not
  // one of this workspace's three accents (rose/emerald/sky). Both render MONO
  // now, distinct by a heavier hairline, exactly as the ⑫ station's own
  // default-coincidence sentence and the genesis screens do. WHAT IS DROPPED IS
  // A HUE, NEVER A CLAIM: both sentences are untouched.
  //
  // Scoped to the TAB'S OWN RENDER BODY, and the scope is the honest part. The
  // amber still inside this tab's folded sections -- route in-band badges, the
  // metric tones, the selection cap -- lives in helper components declared at
  // module top level while being rendered only from inside a fold, so a
  // file-wide `withoutFolds` cannot tell them from surface markup. That is
  // legacy from the absorbed screen and named debt, not this unit's work. What
  // #50-c owns is what its own return statement paints, which is what this
  // slice is: the component body, up to the first helper after it.
  const renderBody = station.slice(
    station.indexOf("export default function BaseModelStation("),
    station.indexOf("function BaseModelSection({"),
  );
  pin.match(renderBody, /<CriteriaCoverageCard/, "slice sanity: the render-body slice must contain the tab's own markup");
  pin.doesNotMatch(renderBody, /function ValidationMetricCard/, "slice sanity: the slice must end before the shared helpers");
  pin.doesNotMatch(
    withoutFolds(withoutComments(renderBody)),
    /(?:border|bg|text|ring)-amber-/,
    "amber is not one of this workspace's three accents -- a promoted warn state renders mono, distinct by weight",
  );
  pin.match(
    station,
    /return "border-slate-400 bg-slate-100 text-slate-800";/,
    "expected the insufficient-data verdict to keep a real WARN treatment, not to be flattened into the page",
  );
});

contractTest("unit #50-c: amber is not a verdict colour on any calibration surface", (pin) => {
  // THE THREE-COLOUR RULE, over the five files whose whole body IS the surface
  // -- rose = fail, emerald = pass, sky = selection/proposal, and every other
  // state told apart by WEIGHT and hairline on the neutral ramp. #50-c re-typed
  // this whole workspace and de-ambered the two boxes its restructure promoted
  // in the base-model tab, but the rule it was applying had a pin only inside
  // that one tab's render body: measured by mutation 2026-08-20, painting the
  // station heading in app/calibration/page.tsx `text-amber-700` left all 168
  // tests green. Amber survives ONLY in the absorbed screen's folded legacy
  // helpers, which is why BaseModelStation is scoped by its own test rather
  // than swept here.
  for (const file of CALIBRATION_SURFACE_FILES) {
    pin.doesNotMatch(
      withoutFolds(withoutComments(read(file))),
      /(?:border|bg|text|ring|from|to|via|divide|outline|decoration)-amber-\d{2,3}/,
      `${file}: a warn state on this screen renders on the neutral ramp with a heavier hairline -- amber would be a fourth verdict hue beside rose/emerald/sky`,
    );
  }

  // BOTH DIRECTIONS: the neutral WARN treatment those boxes moved to must still
  // exist, or "no amber" would be satisfiable by deleting the warning.
  pin.match(
    read("components/calibration/BehaviourStation.tsx"),
    /border-slate-400 bg-slate-100 px-2 py-1\.5 text-\[14px\] leading-6 text-slate-800/,
    "expected the default-coincidence warning to keep a real WARN treatment on the neutral ramp",
  );
});

// ---------------------------------------------------------------------------
// THE FALL-THROUGH SCAN (orchestrator decision, 2026-08-20, after #51-c's
// blue-on-blue fix).
//
// SCOPE, and why it is NOT U-1 RULE 1. The genesis screens ban same-family ink
// over a same-family tint outright, family-wide, because an allow-list means
// re-reading globals.css every time a box is added. The calibration workspace
// cannot take that rule: `bg-sky-50 + text-sky-600` is this app's own deliberate
// "navy fill, sky text" badge idiom here (measured 6.51:1), worn by the NEXT
// chip, the proposal badge, every selected chip and the ⑫ track. Banning it
// would delete the selection language of the whole screen.
//
// So the disease is named precisely instead: a `text-<family>-<shade>` over a
// `bg-<family>-50/100` fill where THAT SHADE IS OUTSIDE THE DARK REMAP. Those
// are the ones that fall through to Tailwind's stock light-mode value and land
// dark-on-dark -- `text-sky-700` (#0369a1) on a navy `bg-sky-50` (#032042) is
// exactly the bug #51-c had to fix.
//
// The remap table is READ OUT OF globals.css rather than copied here, so this
// scan cannot go stale: adding `--color-sky-700` to the theme legalises
// `text-sky-700` in the same commit, and removing one outlaws it.
// ---------------------------------------------------------------------------
contractTest("unit #50-c: no fall-through family ink on the calibration surface", (pin) => {
  const globals = read("app/globals.css");
  const themeBlock = sliceBetween(globals, "@theme {", "\n}");
  pin.match(themeBlock, /--color-sky-600:/, "slice sanity: the @theme dark remap block must have been found");

  const remapped = new Map<string, Set<number>>();
  for (const match of themeBlock.matchAll(/--color-([a-z]+)-(\d{2,3}):/g)) {
    const family = match[1];
    if (!remapped.has(family)) remapped.set(family, new Set());
    remapped.get(family)!.add(Number(match[2]));
  }

  // The parse is load-bearing, so it is checked against three shades this repo
  // knows the answer for: sky-600 IS remapped, sky-700 is NOT (that is the
  // whole bug), and the slate text ramp runs to 950.
  assert.ok(remapped.get("sky")?.has(600), "expected sky-600 in the dark remap");
  assert.ok(!remapped.get("sky")?.has(700), "expected sky-700 to be OUTSIDE the remap -- it is the blue-on-blue trap");
  assert.ok(remapped.get("slate")?.has(950), "expected the slate ramp to reach 950");

  const families = ["sky", "emerald", "blue", "rose", "amber", "slate"];
  const violations: string[] = [];
  for (const file of [
    ...CALIBRATION_SURFACE_FILES,
    "components/calibration/BaseModelStation.tsx",
    "components/agent/ApprovalCards.tsx",
  ]) {
    // Class STRINGS only -- anchored on a pair of quotes with no newline
    // between them, so the doc comments that legitimately name a bad pairing
    // while explaining it cannot trip this.
    for (const literal of read(file).match(/"[^"\n]*"/g) ?? []) {
      for (const family of families) {
        if (!new RegExp(`bg-${family}-(?:50|100)\\b`).test(literal)) continue;
        for (const shade of literal.match(new RegExp(`text-${family}-(\\d{2,3})\\b`, "g")) ?? []) {
          const value = Number(shade.split("-")[2]);
          if (remapped.get(family)?.has(value)) continue;
          violations.push(`${file}: ${shade} over a bg-${family}-50/100 fill`);
        }
      }
    }
  }

  // ANCHORED, for the reason the microtask-deferral pin above carries: an
  // unanchored compare passes on a prefix, which here would mean passing while
  // a violation sat in the list.
  pin.match(
    `violations=${violations.join(" | ")}`,
    /^violations=$/,
    "a same-family text shade outside globals.css's dark remap keeps Tailwind's stock LIGHT value and renders dark-on-dark over its own tint",
  );

  // BOTH DIRECTIONS: the deliberate idiom must still be there. A sweep that
  // "fixed" this by deleting the sky selection language would pass the scan
  // above and break the screen.
  //
  // TWO ANCHORS since #50-d, on purpose. The step chip is the idiom's oldest
  // carrier and #50-d's 지금 할 일 banner is its newest and largest -- the first
  // thing on the screen, in the same navy fill and remapped sky ink. Anchoring
  // on only one of them would let the scan stay green over a screen that had
  // lost the other.
  // The BORDER shade moved 300 -> 200 on 2026-08-21 (#51's rider): sky-300 is
  // the same fall-through in a hairline that this scan bans in ink, and the
  // genesis pin has always said so. The IDIOM is what this anchor guards -- navy
  // fill, remapped sky ink, sky hairline -- and it is unchanged; only the shade
  // that renders it correctly in dark mode is.
  pin.match(
    read("components/calibration/FlowRail.tsx"),
    /NEXT: "border-sky-200 bg-sky-50 text-sky-600"/,
    "expected the NEXT chip to keep the app's navy-fill/sky-text idiom -- this scan bans fall-through shades, not the idiom",
  );
  pin.match(
    withoutFolds(withoutComments(read("app/calibration/page.tsx"))),
    /border-sky-300 bg-sky-50[^"]*text-sky-600/,
    "expected the 지금 할 일 banner to wear the same idiom -- sky IS 'do this one' on this screen",
  );
});

// ---------------------------------------------------------------------------
// Unit #50-d — ACTION FIRST, EDUCATION EVICTED (user-confirmed 2026-08-20).
//
// The user drove #50-c's real screen — numbers first, prose folded — and named
// what it still would not answer: "뭘 해야 할지 모르겠다". The decision behind
// this unit, in the user's own words: 화면은 사용자에게 행동을 빚졌지 교육을
// 빚진 게 아니다 — the screen owes the reader an ACTION, not an education.
//
// So two things happened at once, and they have to be tested as one pair:
//
//   * the page opens with the single thing to do next, as a button;
//   * every doctrine, guard and education sentence LEFT the calibration
//     surfaces — seven ⓘ tooltips, six per-station folded rules, the empty
//     state's explanation, the W74 paragraph, and the whole ⑫ guardrail
//     section.
//
// THE FAILURE MODE THIS BLOCK EXISTS AGAINST is the same one #50-c's ledger
// named, arriving through a different door. A pin that only asked "is the
// sentence off the screen" passes just as happily over a DELETION, and deleting
// the sentences that say what this product refuses to do is exactly the thing
// this codebase must not do. #50-c could answer that with `withoutFolds`,
// because the sentence stayed in the same file. #50-d moved them out of the
// frontend entirely, so the checks below span three files:
//
//   (a) ABSENT from every calibration surface file, comments stripped;
//   (b)+(c) the knowledge file and lib/help-content.tsx carry the SAME triples —
//       docs/product/calibration-doctrine.md is the single source of truth and
//       the file the assistant's RAG corpus indexes, lib/help-content.tsx is
//       what /docs renders, and every sentence of every item must be byte-equal
//       in both, in ko AND en AND ja.
//
// CORRECTED 2026-08-20 (refuter, MAJOR). (b) and (c) used to be a hand-written
// list of short KOREAN FRAGMENTS, one per item, matched into each file. The
// comment here promised "a sweep that softened a sentence fails (b) and (c)",
// and the refuter proved that false in one edit: it reworded D5's ENGLISH
// sentence in lib/help-content.tsx into a meaning-changed hedge, outside the
// pinned Korean fragment, and the whole ledger stayed green. A ko-only fragment
// cannot see an en or a ja reword, and two of this product's three audiences
// read those.
//
// So the mechanism is now WHOLE SENTENCES, ALL THREE LANGUAGES, BOTH FILES, and
// it is derived rather than transcribed: the two files are parsed and their
// triples compared as sets, so a fourth hand-copy cannot itself go stale and a
// new doctrine item is covered the day it is written. Both parsers fail closed
// and are pinned by their own test, because a parser that silently returns
// nothing would make every comparison below pass vacuously — the lesson
// `withoutFolds` above carries its own scars from.
//
// WHAT THIS DOES AND DOES NOT CATCH, stated exactly:
//   * soften a sentence in ONE file, in any language → fails (the sets differ);
//   * delete a sentence from either file → fails (the item count is pinned);
//   * put a sentence back on the screen → fails (a);
//   * add an item to one file only → fails (the id list is pinned);
//   * edit BOTH files identically → PASSES, and deliberately: that is a change
//     to what the product promises, which the knowledge file's own header says
//     requires the user's approval. This suite is not the arbiter of that, and a
//     third copy here pretending to be would just be a third thing to update.
//     Three load-bearing sentences DO carry an independent hand-written copy in
//     the re-aimed #50/#50-c tests above, which is where that guard belongs.
// ---------------------------------------------------------------------------

/**
 * The knowledge file's items, in the order it writes them.
 *
 * PINNED AS A LIST, not counted, and the difference matters in both directions:
 * an item DELETED from the file fails here rather than quietly dropping out of
 * every check below it, and a NEW item fails here until it is registered — which
 * is correct, because a new doctrine item needs a decision about whether it is
 * evicted (absence pins) or stays on the surface, and that decision belongs to a
 * human, not to a loop.
 *
 * `D8`, `D11`, `D12` and `D16` are absent on purpose: they are container
 * headings whose sentences live in their `-a`/`-b`/`-c` children.
 */
const DOCTRINE_ITEM_IDS = [
  "D1", "D2", "D3", "D4", "D5", "D6", "D7",
  "D8-a", "D8-b", "D8-c", "D8-d", "D8-e", "D8-f",
  // D9 is the one item that KEEPS its place on the screen -- see the pins below.
  "D9",
  "D10",
  "D11-a", "D11-b", "D11-c",
  "D12-a", "D12-b",
  "D13", "D14", "D15",
  "D16-a", "D16-b", "D16-c",
  // D17 was found DURING this unit's build, on the scoring tab's map caption: an
  // ⓘ outside the spec's eviction list, overlapping D6's claim without being
  // verbatim (D6 says which nodes are highlighted; this says where a position
  // comes from and why one can be missing). The knowledge file registered it as
  // its own item rather than widening D6, and it was evicted with the rest.
  "D17",
  // D18 arrived with unit #53 (2026-08-24) rather than being evicted by #50-d:
  // it is the RULE behind the sixth status word (확인됨(사람 증언) is not 완료),
  // and it was written straight into the knowledge file instead of onto the
  // screen. The eviction sweep below therefore covers it exactly as it covers
  // the seventeen that were moved -- what a signature RECORDS is stated beside
  // the button that makes one; why it is never 완료 is read once, at /docs.
  "D18",
] as const;

/** The one item whose sentence is a VERDICT and stays on the screen. */
const DOCTRINE_STAYS_ON_SURFACE = "D9";

type DoctrineTriple = { id: string; ko: string; en: string; ja: string };

/**
 * The knowledge file, parsed into `{ id, ko, en, ja }`.
 *
 * FAILS CLOSED, in the two ways it can be wrong. A heading with NO sentences is
 * a container (`## D8.`, `## D16.`) and is dropped silently — that is the normal
 * shape of this file. A heading with ONE or TWO is a half-written item, which is
 * exactly the state a careless edit leaves behind, so it comes back in `partial`
 * and the caller fails on it by name rather than quietly checking two thirds of
 * a triple. Everything else is caught by the id list: this parser cannot invent
 * an item, and an item it fails to see goes missing from a pinned list.
 */
function parseDoctrineTriples(markdown: string): { items: DoctrineTriple[]; partial: string[] } {
  const byId = new Map<string, { ko?: string; en?: string; ja?: string }>();
  const order: string[] = [];
  let current: string | null = null;
  for (const line of markdown.split("\n")) {
    const heading = /^#{2,3} (D\d+(?:-[a-z])?)\./.exec(line);
    if (heading) {
      current = heading[1];
      if (!byId.has(current)) {
        byId.set(current, {});
        order.push(current);
      }
      continue;
    }
    // No `$` anchor: this file is read on two machines and git hands one of them
    // CRLF, so the line may end in a `\r` that `.` stops before but `$` would
    // refuse to match past.
    const sentence = /^- (ko|en|ja): (.*)/.exec(line);
    if (!sentence || !current) continue;
    byId.get(current)![sentence[1] as "ko" | "en" | "ja"] = sentence[2].trim();
  }

  const items: DoctrineTriple[] = [];
  const partial: string[] = [];
  for (const id of order) {
    const entry = byId.get(id)!;
    const filled = [entry.ko, entry.en, entry.ja].filter((text) => text !== undefined);
    if (filled.length === 3) items.push({ id, ko: entry.ko!, en: entry.en!, ja: entry.ja! });
    else if (filled.length > 0) partial.push(id);
  }
  return { items, partial };
}

/** A TypeScript string literal, either quoting style, escapes included. */
const TS_STRING_LITERAL = /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/;

const HELP_BODY_TRIPLE = new RegExp(
  `body: t\\(\\s*(${TS_STRING_LITERAL.source})\\s*,\\s*(${TS_STRING_LITERAL.source})\\s*,\\s*(${TS_STRING_LITERAL.source})\\s*,?\\s*\\)`,
  "g",
);

/** One string literal's actual text.
 *
 * REFUSES what it cannot decode rather than guessing. These doctrine strings
 * carry exactly one kind of escape -- `\'` inside a single-quoted sentence with
 * an apostrophe in it -- and a `\n` or a `\u00e9` slipping in later would decode
 * to the wrong characters here and make a comparison fail for a reason nobody
 * could read. It is a structural violation, so it throws by name. */
function unquoteTsString(literal: string): string {
  const body = literal.slice(1, -1);
  assert.ok(
    !/\\[^'"\\]/.test(body),
    `unsupported escape in a doctrine string literal: ${literal.slice(0, 60)}`,
  );
  return body.replace(/\\(['"\\])/g, "$1");
}

/**
 * `calibrationHelpEntries`'s triples, in source order.
 *
 * SCOPED TO THAT ONE BUILDER: `lib/help-content.tsx` holds six of them and the
 * other five describe screens this unit never touched, so a file-wide sweep
 * would compare the calibration doctrine against the region screen's. The slice
 * ends at the next top-level `export function`, and the caller sanity-checks the
 * count -- an `indexOf` that missed would hand back the rest of the file and
 * every comparison would then run over the wrong builder's sentences.
 *
 * `title:` is deliberately not read: the knowledge file writes its titles into
 * markdown HEADINGS, which are a different shape, and pinning a title would pin
 * a navigation label rather than a claim about the product.
 */
function calibrationHelpTriples(source: string): { ko: string; en: string; ja: string }[] {
  const start = source.indexOf("export function calibrationHelpEntries(");
  if (start === -1) return [];
  const end = source.indexOf("\nexport function ", start + 1);
  const body = source.slice(start, end === -1 ? undefined : end);
  return [...body.matchAll(HELP_BODY_TRIPLE)].map((match) => ({
    ko: unquoteTsString(match[1]),
    en: unquoteTsString(match[2]),
    ja: unquoteTsString(match[3]),
  }));
}

/**
 * The longest run of a sentence that carries no `{…}` placeholder.
 *
 * The ABSENCE half needs a different needle from the presence half, and this is
 * why. Both files write `주행행태 {N}개가 …` where the screen wrote
 * `주행행태 ${summary.default_coincident}개가 …`, so a whole-sentence absence pin
 * would pass over the sentence coming back interpolated — which is the only way
 * it CAN come back. The longest placeholder-free run appears byte-identically in
 * both the knowledge file and a resurfaced `t()` call, so it is the needle that
 * can actually fail.
 */
function placeholderFreeRun(sentence: string): string {
  return sentence
    .split(/\{[^}]*\}/)
    .map((part) => part.trim())
    .reduce((longest, part) => (part.length > longest.length ? part : longest), "");
}

contractTest("contract helper: the doctrine parsers fail closed, and read only what they claim to", (pin) => {
  // A CONTAINER heading (no sentences of its own) is dropped; a HALF-WRITTEN one
  // is reported. Getting these two confused is how a check silently stops
  // covering an item.
  const parsed = parseDoctrineTriples(
    [
      "# Title",
      "## D1. container-less item",
      "- ko: 하나",
      "- en: one",
      "- ja: いち",
      "## D2. a container",
      "prose only, no sentences",
      "### D2-a. the child",
      "- ko: 둘",
      "- en: two",
      "- ja: に",
      "## D3. half written",
      "- ko: 셋",
      "## Not an item heading",
      "- ko: 넷",
    ].join("\n"),
  );
  assert.deepEqual(parsed.items.map((item) => item.id), ["D1", "D2-a"], "a container heading must not become an item");
  assert.deepEqual(parsed.partial, ["D3"], "a half-written triple must be REPORTED, never silently two-thirds checked");
  assert.deepEqual(parsed.items[0], { id: "D1", ko: "하나", en: "one", ja: "いち" });
  assert.ok(
    !parsed.items.some((item) => item.ko === "넷"),
    "a sentence under a non-item heading must not be attributed to the previous item",
  );

  // CRLF, because one of the two machines this repo is developed on gets it.
  const crlf = parseDoctrineTriples("## D1. x\r\n- ko: 가\r\n- en: a\r\n- ja: あ\r\n");
  assert.deepEqual(crlf.items[0], { id: "D1", ko: "가", en: "a", ja: "あ" }, "a CRLF checkout must parse identically");

  // The help parser reads BODIES from ONE builder, and decodes `\'`.
  const help = calibrationHelpTriples(
    [
      "export function calibrationHelpEntries(t) {",
      '  { title: t("TITLE-KO", "TITLE-EN", "TITLE-JA"),',
      "    body: t(",
      '      "본문",',
      "      'the network\\'s own',",
      '      "ほんぶん",',
      "    ) },",
      "}",
      "export function otherHelpEntries(t) {",
      '  { body: t("다른", "other", "ほか") },',
      "}",
    ].join("\n"),
  );
  assert.equal(help.length, 1, "the slice must end at the next exported builder");
  assert.deepEqual(help[0], { ko: "본문", en: "the network's own", ja: "ほんぶん" });

  // A missing builder yields NOTHING rather than the whole file's triples -- and
  // the caller's count assertion is what turns that into a failure.
  assert.deepEqual(calibrationHelpTriples("export function somethingElse() {}"), []);
  // An escape this decoder cannot honour is refused, not guessed at.
  assert.throws(() => unquoteTsString('"line\\nbreak"'), /unsupported escape/);

  // The absence needle skips placeholders and keeps the longest real run.
  assert.equal(placeholderFreeRun("주행행태 {N}개가 공표 행과 일치하지만 그렇습니다"), "개가 공표 행과 일치하지만 그렇습니다");
  assert.equal(placeholderFreeRun("no placeholders here"), "no placeholders here");
  pin.match(
    read("lib/help-content.tsx"),
    /export function calibrationHelpEntries\(/,
    "slice sanity: the builder these parsers are scoped to must exist",
  );
});

/** Every file whose body is a calibration surface, INCLUDING the absorbed
 * base-model tab — which is out of the type-floor sweep for reasons that test
 * states, but is squarely in this one: two of its own ⓘs and one of its folds
 * were evicted by this unit. */
const CALIBRATION_DOCTRINE_FILES = [
  ...CALIBRATION_SURFACE_FILES,
  "components/calibration/BaseModelStation.tsx",
];

contractTest("unit #50-d ③: the knowledge file and /docs carry the SAME doctrine, sentence for sentence, in ko AND en AND ja", (pin) => {
  const doctrine = readCalibrationDoctrine();

  // Slice sanity for the knowledge file itself: an empty read (renamed, moved)
  // would make every comparison below fail loudly rather than quietly, but a
  // TRUNCATED one would not, and this check is only as good as its source.
  pin.match(doctrine, /^# 보정 워크스페이스 독트린/, "the knowledge file must be the calibration doctrine, read whole");
  pin.match(doctrine, /### D16-c\./, "expected the file to run through its last sub-item -- a truncated source under-checks everything here");
  pin.match(doctrine, /## D17\./, "expected the file to run through D17 -- likewise");

  const { items, partial } = parseDoctrineTriples(doctrine);
  assert.deepEqual(partial, [], "an item with one or two of its three languages is half-written, not a triple");
  assert.deepEqual(
    items.map((item) => item.id),
    [...DOCTRINE_ITEM_IDS],
    "the knowledge file's items must be exactly the registered list, in order -- a new item needs an eviction decision, and a lost one needs to be noticed",
  );
  // Nothing empty and nothing stubbed: a one-character "sentence" would satisfy
  // every comparison below while saying nothing.
  for (const item of items) {
    for (const [lang, text] of [["ko", item.ko], ["en", item.en], ["ja", item.ja]] as const) {
      assert.ok(text.length >= 10, `${item.id} (${lang}) is too short to be a doctrine sentence: ${JSON.stringify(text)}`);
    }
  }

  // THE COMPARISON. Both files hold the same 3 x N sentences, and set equality
  // is the whole claim: a reword on either side, in any language, moves one set
  // and not the other. This replaces a hand-written list of Korean fragments
  // that could not see an en or ja edit at all (refuter, 2026-08-20).
  const helpTriples = calibrationHelpTriples(read("lib/help-content.tsx"));
  assert.equal(
    helpTriples.length,
    items.length,
    `lib/help-content.tsx renders ${helpTriples.length} calibration entries for ${items.length} knowledge-file items -- every item is a section at /docs and nothing at /docs is unauthored`,
  );

  const shape = (triple: { ko: string; en: string; ja: string }) =>
    JSON.stringify([triple.ko, triple.en, triple.ja]);
  // Compared as SORTED sets: the reading ORDER is each file's own business (the
  // knowledge file numbers its items, /docs groups D17 beside the D6 it answers
  // with), and pinning order would fail for a rearrangement that changed no
  // claim.
  const fromDoctrine = items.map(shape).sort();
  const fromHelp = helpTriples.map(shape).sort();
  for (const [label, missing] of [
    ["lib/help-content.tsx", fromDoctrine.filter((entry) => !fromHelp.includes(entry))],
    ["docs/product/calibration-doctrine.md", fromHelp.filter((entry) => !fromDoctrine.includes(entry))],
  ] as const) {
    assert.deepEqual(
      missing,
      [],
      `${missing.length} doctrine triple(s) are not byte-identical in ${label}. #50-d moved these sentences and deleted none, and a softened copy in one file is exactly what this compares for:\n${missing.join("\n")}`,
    );
  }
});

contractTest("unit #50-d ③: every evicted doctrine sentence left the calibration surface, in all three languages", (pin) => {
  const { items } = parseDoctrineTriples(readCalibrationDoctrine());
  assert.equal(items.length, DOCTRINE_ITEM_IDS.length, "parser sanity: this sweep is vacuous over an empty item list");

  // Read once. This is 26 items x 3 languages x 6 files of regex, and re-reading
  // a 2,700-line file 78 times to do it would be the slowest test in the suite
  // for no gain.
  const surfaces = CALIBRATION_DOCTRINE_FILES.map(
    (file) => [file, withoutComments(read(file))] as const,
  );

  for (const item of items) {
    if (item.id === DOCTRINE_STAYS_ON_SURFACE) continue;
    for (const [lang, sentence] of [["ko", item.ko], ["en", item.en], ["ja", item.ja]] as const) {
      const needle = placeholderFreeRun(sentence);
      // A degenerate needle would make the pin below unfalsifiable (or, worse,
      // fire on unrelated text). A sentence chopped into nothing but short runs
      // by placeholders surfaces here by name instead.
      assert.ok(
        needle.length >= 16,
        `${item.id} (${lang}): the longest placeholder-free run is only ${needle.length} characters (${needle}) -- too short to identify this sentence on a surface`,
      );
      const pattern = literalPattern(needle);
      for (const [file, source] of surfaces) {
        pin.doesNotMatch(
          source,
          pattern,
          `${item.id} (${lang}): must not be back on ${file} -- the user's 2026-08-20 decision took it off this screen`,
        );
      }
    }
  }

  // THE ONE THAT STAYS, and it is here so "evicted" cannot quietly grow to mean
  // "all of it". 수동 확인 영역's one-line verdict is the promise this screen
  // rests on — it says what the product WILL NOT DO — so it is a verdict, not an
  // education, and it renders UNFOLDED on the station it belongs to.
  const stays = items.find((item) => item.id === DOCTRINE_STAYS_ON_SURFACE);
  assert.ok(stays, `${DOCTRINE_STAYS_ON_SURFACE} must be in the knowledge file -- it is the rule this screen keeps`);
  pin.match(
    withoutFolds(withoutComments(read("app/calibration/page.tsx"))),
    literalPattern(stays.ko),
    "the manual station's one-line verdict must stay on the SURFACE -- it is the screen's promise, not doctrine",
  );

  // ZERO TOOLTIPS, and this is the pin that keeps the eviction from being a
  // one-time sweep. Every item above left as a SENTENCE, and a sentence can come
  // back under a different wording that no fragment pin would catch -- the
  // vehicle it would come back in is an ⓘ, because that is the vehicle six of
  // the seven arrived in. `InfoTip` itself is untouched and the genesis screens
  // still use it correctly; what this bans is a doctrine tooltip returning to
  // THIS workspace, in any wording.
  for (const file of CALIBRATION_DOCTRINE_FILES) {
    pin.doesNotMatch(
      withoutComments(read(file)),
      /<InfoTip\b/,
      `${file}: the calibration workspace carries no tooltips after #50-d -- background belongs at /docs, and an ⓘ is how it would come back`,
    );
  }
  // BOTH DIRECTIONS: the primitive must still EXIST and still be used where it
  // belongs. "No ⓘ here" satisfied by deleting the component would be a change
  // to four other screens that nobody asked for.
  pin.match(
    read("components/genesis/ui.tsx"),
    /export function InfoTip\(/,
    "expected the ⓘ primitive to survive -- #50-d evicted doctrine from one workspace, it did not retire a control",
  );
  pin.match(
    read("app/genesis/jobs/[id]/page.tsx"),
    /<InfoTip\b/,
    "expected the genesis job detail to keep using it -- a phrase beside a control is what it is for",
  );

  // The doctrine module obeys the same three-language rule the calibration
  // screens do: a 2-argument t() here would fall back to English for ja readers,
  // and this module is now the ONLY copy of these sentences a reader can reach.
  pin.doesNotMatch(
    read("lib/help-content.tsx"),
    /\bt\(\s*"[^"]*"\s*,\s*"[^"]*"\s*,?\s*\)/,
    "a two-argument t() in lib/help-content.tsx would leave a ja reader with English doctrine",
  );
});

contractTest("unit #50-d ①: the workspace opens with the ONE THING TO DO, and it is a button", (pin) => {
  const page = read("app/calibration/page.tsx");
  const surface = withoutFolds(withoutComments(page));

  // DERIVED, NEVER DECIDED. There are exactly two legitimate sources for this
  // line -- the run selection this page already holds, and the SERVER's own NEXT
  // status -- and the binding is pinned WHOLE, ending at `?? null`, for the
  // reason #51's track binding is: appending a locally invented fallback to the
  // expression is the mutation that satisfies a looser pin.
  pin.match(
    page,
    /const nextStep = railSteps\.find\(\(step\) => step\.status === "NEXT"\) \?\? null;/,
    "expected the banner's step to be the first NEXT the server sent, and nothing else",
  );
  // The WORDING is the rail's own helper, so the banner and the rail row beside
  // it cannot describe the same step differently -- and a hardcoded Korean todo
  // string in this position fails here.
  pin.match(surface, /\{stepLabel\(nextStep, lang\)\}/, "expected the banner to print the rail's own label for that step");
  // It is an ACTION: pressing it opens that step's station, through the same
  // handler a rail row uses. A caption would be an education.
  pin.match(
    surface,
    /onClick=\{\(\) => handleSelectStep\(nextStep\)\}/,
    "expected the banner to navigate to the step it names, by the existing station-select path",
  );
  // The no-run branch, verbatim in three languages -- and an action too: it puts
  // the cursor in the selector it names, rather than pointing at it.
  pin.match(
    surface,
    literalPattern('t("채점할 실행을 선택하세요", "Choose a run to score", "採点する実行を選択してください")'),
    "expected the no-run branch to state the exact sentence, in all three languages",
  );
  pin.match(
    surface,
    /onClick=\{\(\) => runSelectRef\.current\?\.focus\(\)\}/,
    "expected the no-run banner to reach the run selector it names",
  );
  // ...and NOTHING when there is nothing. A rail that has not loaded, or a rail
  // with no NEXT step, must produce no banner at all: an invented todo is the
  // one lie a line in this position can tell.
  pin.match(
    page,
    /\) : nextStep \? \([\s\S]{0,2000}?\) : null\}/,
    "expected the banner to render nothing when no step is NEXT -- never a placeholder todo",
  );

  // FIRST ON THE PAGE. A next-action line below the context bar is a caption;
  // above it, it is the answer to the question the reader arrived with.
  const bannerAt = page.indexOf("지금 할 일:");
  const contextBarAt = page.indexOf('t("데이터셋", "Dataset", "データセット")');
  assert.ok(bannerAt > 0 && contextBarAt > 0, "expected both the banner and the context bar to be found");
  assert.ok(bannerAt < contextBarAt, "the 지금 할 일 banner must render ABOVE the context bar");

  // Read at a glance: 16-18px, which is above this screen's 14px floor and
  // above the 15px the context bar's verdict is set in.
  pin.match(surface, /text-\[17px\] font-semibold/, "expected the banner to be set at 17px -- the biggest sentence on the screen");
});

contractTest("unit #50-d ②: no card on the calibration surface carries more than one 자세히 fold", (pin) => {
  // COUNTED, not described. "At most one per card" is a claim about rendering,
  // and the closest a source-regex suite can honestly get is an EXACT count per
  // file with the card each fold belongs to named here. That makes a new
  // disclosure anywhere on this screen a failure that has to be argued for,
  // which is the point -- the shape the user's 2026-08-20 decision named was a
  // panel with seven of them.
  const expected: [string, number, string][] = [
    [
      "app/calibration/page.tsx",
      2,
      "the step box (one per step row, one fold each), plus #53's SIGNATURE row -- a separate card, " +
        "below the station's own action, carrying the signing history and the .inpx hash. Its surface " +
        "is who signed, when, and their reason; what folds is provenance, which is exactly the split " +
        "the step box makes",
    ],
    [
      "components/calibration/FlowRail.tsx",
      3,
      "the rail's own citation+principles fold, plus the ⑫ track's ONE fold in each of its two mutually exclusive states (locked: refusal codes / open: band citation)",
    ],
    ["components/calibration/BehaviourStation.tsx", 1, "one table-level fold carrying every verdict's why + citation and closing with Table 5-6's page"],
    ["components/calibration/StationProposals.tsx", 1, "the placement note's tool ids"],
    ["components/calibration/NodeScoreTable.tsx", 2, "one on the tile grid (reality_check's refusal code), one on the node detail -- separate cards"],
  ];

  for (const [file, count, why] of expected) {
    const opened = (withoutComments(read(file)).match(/<details\b/g) ?? []).length;
    assert.equal(opened, count, `${file}: expected ${count} fold(s) — ${why} — found ${opened}`);
  }

  // The two folds this unit MERGED and the one it DELETED, named so a rewrite
  // that re-splits them fails with the reason rather than with a count.
  const behaviour = withoutComments(read("components/calibration/BehaviourStation.tsx"));
  pin.doesNotMatch(
    behaviour,
    /자세히 \(판정 근거·출처\)/,
    "expected the PER-ROW verdict folds to be merged away -- eight behaviours meant nine triggers on one card",
  );
  pin.match(
    behaviour.slice(behaviour.indexOf("<details")),
    /\{verdict\.why\}[\s\S]{0,300}?\{verdict\.citation\}[\s\S]{0,600}?\{rows\.citation\}/,
    "expected the merged fold to carry every verdict's own sentence and citation, and to close with the table's",
  );
  pin.doesNotMatch(
    withoutComments(read("app/calibration/page.tsx")),
    /자세히 \(모델 값의 출처\)|자세히 \(이 단계의 근거 규칙\)/,
    "expected the scoring tab's model-value fold and the per-station doctrine fold to be gone, not renamed",
  );
});

contractTest("unit #50-d ④: the ⑫ guardrail section is off the screen, and the backend that enforces it is untouched", (pin) => {
  // AUTHORITY: the user, 2026-08-20 — "developer side information... get rid of
  // it", recorded in the roadmap row for #50-d as "2경로 예보·결정 칩·mono 코드
  // 바 전부 문서/지식 파일로 … #51-c 표시 핀 사용자 권한 제거(기록)".
  //
  // THE DISPLAY IS GONE — component, both ⑫ call sites, the two path constants,
  // and the per-path forecast sentences inside the approval card's fold. Swept
  // over the WHOLE app rather than the two files that had them, because
  // components/agent/ApprovalCards.tsx is rendered by FloatingAssistant on every
  // route: a third caller reintroducing either component would put the section
  // back on screens this unit never looked at.
  // The SYMBOLS and the two SENTENCES they rendered, because a hand-rolled
  // second copy would carry the words without importing the components.
  for (const file of appSourceFiles()) {
    pin.doesNotMatch(
      withoutComments(read(file)),
      /GuardrailPathLines|GuardrailResolutionChips|TrackGuardrail|GUARDRAIL_PATH_DIRECT|GUARDRAIL_PATH_APPROVED|통과 \(반경 전부 명명 시\)|해소 방안 — 사용자 결정/,
      `${file}: the ⑫ guardrail display was removed by the user's decision -- it may not come back through another caller`,
    );
  }

  // ...AND THE ENFORCEMENT IS UNTOUCHED, which is the half that matters. The
  // server still forecasts both paths, still refuses the direct write BY NAME,
  // and still re-mints a single-use grant on the approved one. #51-c pinned the
  // two path keys on BOTH sides because a rename would blank the client's
  // lookup; there is no client lookup any more, so the pin moves to the side
  // that is now the only one — the payload these keys are built into is still
  // the thing the approve leg is gated on.
  const parameterSearch = readFileSync(
    path.join(repoRoot, "backend", "services", "agent", "parameter_search.py"),
    "utf-8",
  );
  pin.match(
    parameterSearch,
    /PATH_DIRECT_EDIT = "direct_edit"\s*\nPATH_APPROVED_SET = "approved_set"/,
    "expected the backend to keep emitting both paths -- #50-d removed a rendering, not a forecast",
  );
  pin.match(
    parameterSearch,
    /SHARED_BEHAVIOR_REFUSAL = "driving_behavior_shared_object_edit"/,
    "expected the shared-behaviour refusal to still be the named constraint a direct write hits",
  );
  pin.match(
    parameterSearch,
    /"condition": "RADIUS_NAMED_IN_FULL"/,
    "expected the approved path to still carry its condition code -- the exception does not hold without the full radius",
  );
  // The wire still carries what the screen stopped drawing. A backend that
  // dropped the forecast would be a real change; this unit made none.
  pin.match(
    read("lib/api.ts"),
    /preflight_forecast\?:/,
    "expected the forecast to stay on the approval payload -- the screen stopped rendering it, the server did not stop sending it",
  );

  // WHERE IT WENT: D16-a/b/c of the knowledge file, and /docs. The CODES are
  // pinned here because they are the join between this test's backend half and
  // the documentation; the three entries' full ko/en/ja sentences are cross-
  // pinned byte-for-byte between the two files by the "#50-d ③: D16's guardrail
  // account" test, so a reword on either side fails there rather than sliding
  // past a code-only check.
  for (const [what, needle] of [
    ["the direct path's constraint", "SHARED_BEHAVIOR_REFUSAL"],
    ["the approved path's condition", "RADIUS_NAMED_IN_FULL"],
    ["the un-forecast qualifier", "통과 (반경 전부 명명 시)"],
    ["the three forecast codes", "GUARDRAIL_UNKNOWN"],
    ["the user's decision on the collision", "REJECTED_BY_USER"],
  ] as const) {
    pin.match(readCalibrationDoctrine(), literalPattern(needle), `${what} must be documented in the knowledge file`);
    pin.match(read("lib/help-content.tsx"), literalPattern(needle), `${what} must be readable at /docs`);
  }

  // WHAT STAYED ON THE CARD, because it is approval-decision material rather
  // than developer information: the evidence rank (its own test above) and the
  // radius the human is consenting to. And the fold keeps what stands behind
  // the NUMBER -- the ledger digest, the holdout, the citation.
  const cards = read("components/agent/ApprovalCards.tsx");
  pin.match(cards, /\{search\.consent_radius_phrase\}/, "expected the consent radius to stay on the card -- it is the SCOPE of the approval");
  const panel = cards.slice(cards.indexOf("export function SearchedParameterPanel"));
  pin.match(panel, /ledger\.digest/, "expected the search ledger's digest to stay in the fold");
  pin.match(panel, /holdout\.unseen_seed/, "expected the holdout seed to stay in the fold");
  pin.match(panel, /\{search\.citation \?\? ""\}/, "expected the citation to stay in the fold");
});

contractTest("unit #50-d ⑤⑥: the empty state has no fold, and the rail's expander is an icon", (pin) => {
  const proposals = read("components/calibration/StationProposals.tsx");
  const rail = read("components/calibration/FlowRail.tsx");

  // ⑤ A six-word absence does not need a disclosure under it. The fold is gone
  // and the LINE is untouched -- including the half that may never fold.
  const emptyState = sliceBetween(proposals, "if (placed.length === 0)", "flex flex-col gap-2");
  pin.match(emptyState, /승인 대기 제안 없음/, "slice sanity: the empty-state slice must contain the empty state");
  pin.doesNotMatch(
    withoutComments(emptyState),
    /<details\b/,
    "expected the empty state's fold to be gone (#50-d ⑤) -- the sentence it held is at /docs",
  );
  pin.match(
    withoutFolds(withoutComments(proposals)),
    /다른 단계 \$\{otherCount\}건/,
    "the count waiting at OTHER stations must still be on the surface -- without it every station's empty state reads as 'no work anywhere'",
  );

  // ⑥ The words became an arrow. `aria-expanded` survived and an `aria-label`
  // joined it, so what a screen reader is told did not shrink with the control.
  pin.doesNotMatch(
    withoutComments(rail),
    /t\("접기", "Collapse", "折りたたむ"\)|t\("펼치기", "Expand", "展開"\)/,
    "expected the 펼치기/접기 words to be replaced by a chevron (#50-d ⑥)",
  );
  pin.match(rail, /aria-expanded=\{manualOpen\}/, "expected the toggle to keep announcing its state");
  pin.match(
    rail,
    /aria-label=\{[\s\S]{0,400}?수동 확인 영역 펼치기[\s\S]{0,120}?手動確認領域を展開/,
    "expected a three-language aria-label -- an icon with no name is a control a screen reader cannot describe",
  );
  pin.match(
    rail,
    /<svg[\s\S]{0,700}?<path d="M5 7\.5 10 12\.5 15 7\.5" \/>/,
    "expected an inline chevron rather than an imported icon set",
  );
  pin.match(
    rail,
    /manualOpen \? "rotate-180" : ""/,
    "expected the chevron to turn over when the group opens -- the rotation IS the state",
  );
});

// ---------------------------------------------------------------------------
// The convention for state that comes from outside React (2026-08-19).
//
// `react-hooks/set-state-in-effect` used to have THREE answers living side by
// side in this tree: most files simply carried the error, three files switched
// the rule off wholesale in eslint.config.mjs (inherited from the original
// source import), and some screens deferred their setState through
// `Promise.resolve().then(...)` to slip past it. lib/asyncResource.ts replaces
// the first two outright.
//
// CORRECTED 2026-08-19 (refuter). This block used to end "lib/asyncResource.ts
// is the single answer now", and that was measurably false: the microtask
// deferral is still live in FOUR files / EIGHT sites, two of which this unit
// itself touched (app/dashboard/page.tsx, and the deferral that travelled into
// components/calibration/BaseModelStation.tsx with the absorbed screen). The
// lint gate cannot see them -- a deferred setState does not fire the rule at
// all -- so "the rule reports zero" was never evidence for "there is one
// convention". It is evidence for "nothing is left that the rule can see".
//
// So the deferral is now an INVENTORY rather than a claim: named below, bounded
// to those files, and unable to spread to a fifth without failing this suite.
// Converting them is the next unit's work, not a sentence in a comment.
// ---------------------------------------------------------------------------

/** The files still using `Promise.resolve().then(...)` to defer a setState out
 * of an effect body. Measured 2026-08-19; every one of them predates unit #50
 * (`git show HEAD:` confirms the same counts before the pass). */
const MICROTASK_DEFERRAL_FILES = [
  "app/dashboard/page.tsx",
  "app/genesis/demand/page.tsx",
  "app/genesis/phases/page.tsx",
  "components/calibration/BaseModelStation.tsx",
];

contractTest("the microtask deferral is a NAMED remainder, and cannot spread", (pin) => {
  const carriers = appSourceFiles().filter((file) =>
    /Promise\.resolve\(\)\.then\(/.test(withoutComments(read(file))),
  );

  // Both directions. A new file adopting the trick fails here, which is the
  // point; a listed file that got converted also fails here, which is how the
  // list stops being stale the moment the debt is actually paid.
  //
  // ANCHORED, and that is load-bearing: `literalPattern` builds an UNANCHORED
  // regex, so the first draft of this pin compared "files=A,B,C,D" against a
  // haystack of "files=A,B,C,D,E" and passed on the prefix -- it caught a
  // conversion and missed the one thing it was written to catch, a fifth file
  // adopting the trick. Measured by mutation before this line existed.
  const expected = MICROTASK_DEFERRAL_FILES.join(",").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  pin.match(
    `files=${carriers.join(",")}`,
    new RegExp(`^files=${expected}$`),
    "the microtask-deferral escape hatch must stay confined to the files that already carry it -- " +
      "add nothing, and strike a file from MICROTASK_DEFERRAL_FILES when it is converted",
  );

  // And the convention module must not describe itself as the only answer
  // while this list is non-empty -- the over-claim this pin was written for.
  pin.doesNotMatch(
    read("lib/asyncResource.ts"),
    /now the only one/,
    "lib/asyncResource.ts must not claim to be the app's only answer while the deferral inventory above is non-empty",
  );
});

contractTest("the async-state convention has exactly one home, and no file-wide escape hatch", (pin) => {
  const config = read("eslint.config.mjs");
  const helper = read("lib/asyncResource.ts");

  // THE POINT OF THE WHOLE PASS: no file may be exempted from the rule. A
  // per-file "off" is a second convention, and two conventions is the state
  // this unit existed to end.
  pin.doesNotMatch(
    config,
    /"react-hooks\/set-state-in-effect":\s*"off"/,
    "no file may switch the rule off -- the fix belongs in lib/asyncResource.ts, not in the config",
  );

  // The helper's four primitives. Named individually so deleting one shows up
  // here rather than as a mystery refactor somewhere downstream.
  for (const primitive of [
    "export type Loaded<T>",
    "export function ok<T>(",
    "export function failed<T>(",
    "export function failedWith<T>(",
    "export function resolve<T>(",
    "export function resolveSelection<T, K>(",
  ]) {
    pin.match(helper, literalPattern(primitive), `expected ${primitive} in the async-state convention`);
  }

  // `keepPrevious` must stay opt-in. Defaulting it on would silently show the
  // PREVIOUS selection's data under the new selection's heading -- which on the
  // calibration and observations screens means one run's or one survey's
  // numbers presented as another's.
  pin.match(
    helper,
    /if \(options\?\.keepPrevious && loaded\) \{/,
    "expected keepPrevious to be opt-in, never the default",
  );
  pin.match(
    helper,
    /deliberately NOT the default/,
    "expected the helper to say why keepPrevious is opt-in, not just that it is",
  );
});

contractTest("an effect may only set state after hydration, and says so on the line", (pin) => {
  // The ONE exception the convention allows: a value that cannot be read during
  // render without desynchronising the server's HTML from the client's first
  // render. Every suppression in the app must be this shape -- a single line,
  // with a reason -- so a blanket one cannot reappear disguised as a fix.
  for (const file of [
    "components/calibration/BaseModelStation.tsx",
    "components/LanguageProvider.tsx",
    "components/ProjectSelector.tsx",
    // The region page reads a one-shot hand-off out of sessionStorage, which
    // does not exist while the server renders the route -- same exception,
    // same one-line shape.
    "app/genesis/region/page.tsx",
    // R-5b MOVED the /report suppression rather than removing it: the browser-
    // local style profile that needs a post-hydration read now lives in the
    // demoted legacy builder (components/report/LegacyReportBuilder.tsx), and
    // the rebuilt page reads nothing from localStorage at all. Naming the file
    // that actually carries the suppression is what keeps this loop from going
    // vacuously green over a file with none.
    "components/report/LegacyReportBuilder.tsx",
  ]) {
    const source = read(file);
    const suppressions = source.match(/eslint-disable[^\n]*set-state-in-effect[^\n]*/g) ?? [];
    for (const line of suppressions) {
      pin.match(
        line,
        /eslint-disable-next-line react-hooks\/set-state-in-effect -- .+/,
        `a suppression in ${file} must be one line and carry its reason`,
      );
      pin.doesNotMatch(
        line,
        /eslint-disable\s+react-hooks\/set-state-in-effect/,
        `${file} must not disable the rule for a whole block or file`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// Track R unit R-5b — the /report rebuild.
//
// AUTHORITY for the layout and for every claim these pins make about what the
// screen owes: docs/product/report-engine-design-2026-08-24.md, section
// "UI (/report 재편)" -- "좌: 보고서 스파인 트리 (절별 포함/제외 토글, 절별
// 데이터 소스 상태 — '이 절의 증거 없음'은 회색+사유) / 우: 미리보기 (절 단위) /
// 상단: 실행/데이터셋 선택 + 스타일 프로파일 선택 (기본: Prism 기본 스타일) /
// '생성' → DOCX (+ 도판 원본 zip 아티팩트)".
//
// The mutation classes these close were measured on this unit's own source, in
// the shapes docs/phase2/DECISIONS.md ("2026-08-24 — 계약 핀의 위협 모델
// 경계") names as in scope: a well-meaning edit that filters the selection on
// the way out, one that swaps a server sentence for a client-written one, one
// that blanks a `ja` argument, and one that re-points the SVG injection at a
// string this file built.
// ---------------------------------------------------------------------------

/** The three files that ARE the /report surface.
 *
 * NAMED, not walked, for the reason #57 measured on
 * `ParameterSearchRefusalAction.tsx`: a file that exists but is not in the
 * array is not swept, and the sweep stays green over it. All three halves of
 * this screen are here -- the rebuilt page, the demoted legacy builder, and
 * (#64) the Markdown structure renderer the text-preview pane mounts -- so a
 * violation cannot hide by moving between them. `lib/reportMarkdownBlocks.ts`
 * is NOT in this list on purpose: it is a pure `lib/` module (mirroring
 * `lib/reportMarkdownPreview.ts`, also excluded here), not a rendered
 * surface, and gets its own purity-specific pins below instead of the
 * styling/spread pins this array feeds. */
const REPORT_SURFACE_FILES = [
  "app/report/page.tsx",
  "components/report/LegacyReportBuilder.tsx",
  "components/report/MarkdownStructurePreview.tsx",
];

contractTest("R-5b: /report is the design doc's layout, and the old workbook path is demoted, not deleted", (pin) => {
  const page = read("app/report/page.tsx");
  const legacy = read("components/report/LegacyReportBuilder.tsx");

  // 좌 스파인 트리 / 우 미리보기 -- the dashboard split idiom, reused.
  pin.match(
    page,
    /grid gap-4 xl:grid-cols-\[380px_minmax\(0,1fr\)\]/,
    "expected the spine tree / preview split to use the app's two-pane grid idiom",
  );

  // 상단: the run picker, the before/after pair the 전후 차트 needs, the style
  // profile (default = Prism 기본), the report language, and (R-5c,
  // docs/phase2/DECISIONS.md 2026-08-24 "승인 이력 세션 픽커는 위임") the
  // approval-history session picker R-5b's own gap report named.
  for (const [state, what] of [
    ["setScenarioId", "the run picker"],
    ["setBeforeScenarioId", "the before-run picker the 전후 chart needs"],
    ["setAfterScenarioId", "the after-run picker the 전후 chart needs"],
    ["setStyleProfileId", "the style-profile picker"],
    ["setReportLanguage", "the report-language picker"],
    ["setApprovalSessionId", "the R-5c approval-history session picker"],
  ] as const) {
    pin.match(page, literalPattern(state), `expected ${what} in the top bar`);
  }
  // #56's lesson, applied: the picker's <option value> is the tool-vocabulary
  // id (`scenario.id`, e.g. SIM_...), and the NAME is only the label -- the id
  // is what flows into the request.
  pin.match(
    page,
    /<option key=\{scenario\.id\} value=\{scenario\.id\}>\s*\{scenario\.name\}/,
    "expected the run pickers to carry the scenario ID as the option value and the name only as the label",
  );
  // The same idiom, on the R-5c picker: the option's VALUE is the raw
  // session_id field the backend measured, never a formatted display string
  // (the display text -- id + count + date -- sits in the JSX text node that
  // follows, not in the value). MEASURED mutation: swapping `value={session.
  // session_id}` for a display-label expression survives every other pin in
  // this file (nothing else regexes the SELECT's runtime value), so this is
  // the one guard against a picker that submits a label instead of an id.
  pin.match(
    page,
    /<option key=\{session\.session_id\} value=\{session\.session_id\}>/,
    "expected the approval-history session picker's <option value> to be the raw session_id field, never a formatted display string",
  );

  // Every endpoint R-1..R-5a publishes is wired, plus R-5c's session list.
  for (const call of [
    "fetchReportSpine()",
    "fetchReportFigures(figureParams)",
    "fetchReportGraphics(figureParams)",
    "fetchReportStyleProfiles()",
    "fetchReportAuditSessions()",
    "composeReport(requestBody)",
    "composeReportArtifacts(requestBody)",
  ]) {
    pin.match(page, literalPattern(call), `expected ${call} to be wired on the report screen`);
  }

  // THE DEMOTION, both directions. The legacy surfaces still exist and still
  // call their own endpoints; the page renders them inside a fold; and the two
  // paths are NOT merged -- the rebuilt page never calls the legacy endpoints.
  pin.match(legacy, /buildReport\(spec\)/, "the legacy Word export must still be callable -- it was demoted, not deleted");
  pin.match(
    legacy,
    /fetchReportBlockDataCsv\(blockType\)/,
    "the legacy per-block CSV export must still be callable -- it reads the validation workbook the typesetter does not",
  );
  pin.match(
    page,
    /<details[\s\S]{0,900}<LegacyReportBuilder \/>/,
    "expected the legacy builder to render INSIDE the page's fold -- demoted below the typesetter, not beside it",
  );
  pin.doesNotMatch(
    withoutComments(page),
    /buildReport|fetchReportBlockDataCsv/,
    "the rebuilt page must not call the legacy workbook endpoints itself -- two data paths, and only one of them is the typesetter's",
  );
});

contractTest("R-5b: the compose request body is the selection on screen, and the profile id only when picked", (pin) => {
  const page = read("app/report/page.tsx");
  const api = read("lib/api.ts");
  const builder = withoutComments(sliceBetween(page, "function composeRequestBody(", "\nfunction downloadBlob("));
  pin.match(builder, /\S/, "expected to find composeRequestBody's own source slice (marker moved?)");

  // THE SELECTION, VERBATIM. `selection.sections` is the checked list and
  // nothing else happens to it on the way out.
  pin.match(
    builder,
    /selection: \{ sections: args\.includedSectionIds \}/,
    "expected the request's section list to be the screen's included list, handed over unchanged",
  );
  pin.doesNotMatch(
    builder,
    /\.filter\(|\.slice\(|\.map\(/,
    "expected NO filtering, slicing or mapping inside composeRequestBody -- an evidence gap is typeset as a named absence, never dropped on the way to the server",
  );

  // The included list itself is the complement of what the user unchecked --
  // one derivation, pinned, so "hidden filtering" would have to happen here in
  // plain sight.
  pin.match(
    page,
    /treeSectionIds\.filter\(\(id\) => !excludedSectionIds\.includes\(id\)\)/,
    "expected the included list to be exactly the tree minus what the user unchecked",
  );

  // THE PROFILE ID: present only when the user picked one. Sending "" would be
  // an unknown-id refusal; omitting the key is how the Prism default is asked
  // for. Structural anchor, so a mutation that always sets it fails here.
  pin.match(
    builder,
    /if \(args\.styleProfileId\) body\.style_profile_id = args\.styleProfileId;/,
    "expected style_profile_id to be set ONLY when a stored profile was picked",
  );
  pin.doesNotMatch(
    builder,
    /style_profile_id: args\.styleProfileId,/,
    "expected style_profile_id NOT to be set unconditionally -- an empty id is a refusal, not a default",
  );

  // THE ONE-INPUT INVARIANT, at the CALL SITE. composeRequestBody's docstring
  // claims the document is built from the same params the preview was fetched
  // with, and until now nothing held the caller to it. MEASURED (refuter,
  // 2026-08-25): replacing the `figureParams` argument with an equal-fields
  // object literal survived the whole suite -- and once the caller owns its own
  // literal, adding `period: "AM"` to it makes the DOCUMENT score a window the
  // PREVIEW above it never showed, silently. Two pins close it: the argument
  // must be the IDENTIFIER, and the identifier must have exactly one definition.
  pin.match(
    page,
    /composeRequestBody\(\{ includedSectionIds, styleProfileId, language: effectiveLanguage, figureParams \}\)/,
    "expected the compose body to be built from the `figureParams` IDENTIFIER -- the same object the preview was fetched with, never a second literal that can diverge from it",
  );
  pin.match(
    page,
    /\[includedSectionIds, styleProfileId, effectiveLanguage, figureParams\]/,
    "expected the memoised request body to depend on `figureParams` itself, so a change to the preview's inputs rebuilds the document's",
  );
  // ONE construction site for the figure params, counted by the FOUR field
  // names appearing together -- R-5c (docs/phase2/DECISIONS.md, 2026-08-24
  // "승인 이력 세션 픽커는 위임") extended this same tuple with `session_id`
  // rather than forking a second params object for the approval-history
  // picker, precisely so this pin (and the two above it) keep holding: the
  // preview fetch and the compose body still read the ONE `figureParams`
  // identifier, so a session picked on screen cannot diverge from the
  // session the document is typeset from. A second equal-fields literal
  // anywhere in this file -- the refuter's original mutation, and equally a
  // NEW literal that sets `session_id` outside this one -- makes this count
  // 2 or drops the fourth clause from the match entirely.
  pin.match(
    `figureParamConstructions=${(withoutComments(page).match(/scenario_id:[\s\S]{0,200}?before_scenario_id:[\s\S]{0,200}?after_scenario_id:[\s\S]{0,200}?session_id:/g) ?? []).length}`,
    /^figureParamConstructions=1$/,
    "expected exactly ONE place on this screen to build the figure params, including R-5c's session_id field -- a second construction (or a session_id set outside this one) is how the preview and the document start describing different sessions",
  );
  // session_id's OWN single-source-of-truth pin, exact-string rather than a
  // bare word-boundary count: the picker's <option>/<select> legitimately
  // reference `session.session_id` (a LIST ITEM's field) several times to
  // build its options, which the generic per-field-count idiom below cannot
  // tell apart from a second WRITE into figureParams. This pin anchors on the
  // one shape that IS a write -- the `session_id: approvalSessionId || null,`
  // key -- so it stays meaningful even though the identifier `session_id`
  // itself appears more than once on this screen.
  pin.match(
    `sessionIdAssignments=${(withoutComments(page).match(/session_id: approvalSessionId \|\| null,/g) ?? []).length}`,
    /^sessionIdAssignments=1$/,
    "expected exactly ONE place this screen sets figureParams.session_id -- a second is a second source of truth for which session's approval history the report scores",
  );
  for (const field of ["scenario_id", "before_scenario_id", "after_scenario_id"]) {
    pin.match(
      `${field}=${(withoutComments(page).match(new RegExp(`(?<![_A-Za-z])${field}\\b`, "g")) ?? []).length}`,
      new RegExp(`^${field}=1$`),
      `expected \`${field}\` to be written exactly once on this screen -- a second mention is a second source of truth for what the report scores`,
    );
  }

  // AND THE WIRE. The api layer posts that object and builds no second one.
  const compose = withoutComments(
    sliceBetween(api, "export async function composeReport(", "export interface ComposedReportArtifacts"),
  );
  pin.match(compose, /body: JSON\.stringify\(body\),/, "expected composeReport to post the caller's body verbatim");
  pin.doesNotMatch(
    compose,
    /\.\.\.body|delete body|Object\.assign/,
    "expected composeReport to add no field and remove none -- what the screen shows is what the typesetter is asked for",
  );
});

contractTest("R-5c: the approval-history session picker is delegated, measured, and never invents", (pin) => {
  // Track R unit R-5c (docs/phase2/DECISIONS.md, 2026-08-24 "HWP는 v1 제외
  // 확정 · 승인 이력 세션 픽커는 위임"): R-5b named the gap -- /report had no
  // session_id input, so figures.approval_history_table rendered a permanent
  // named absence. This test extends R-5b's OWN pins above (the top-bar
  // affordance list, the endpoint-wiring list, and figureParamConstructions)
  // rather than duplicating them, and adds the shapes specific to this unit:
  // the server is the only source of which sessions exist, the picker's
  // value is the raw id, and the untouched default keeps today's
  // absent-with-reason behaviour exactly as it was.
  const page = read("app/report/page.tsx");
  const source = withoutComments(page);

  // ABSENT BY DEFAULT. `approvalSessionId` starts at "", the same idiom every
  // other figure-params field on this screen uses, and NOTHING seeds it from
  // the fetched list -- an untouched screen must keep asking for the figure
  // with no session_id, exactly as it did before this unit existed (R-2's
  // approval_history_table renders its own named absence for that case).
  pin.match(
    source,
    /const \[approvalSessionId, setApprovalSessionId\] = useState\(""\);/,
    "expected approvalSessionId to default to the empty string -- the same 'nothing picked yet' idiom every other figure-params field uses",
  );
  pin.doesNotMatch(
    source,
    /setApprovalSessionId\((?!""\)|event\.target\.value\))/,
    "expected setApprovalSessionId to be called ONLY by the picker's onChange (event.target.value) or a reset to \"\" -- anything else is the screen choosing a session on the user's behalf",
  );

  // NEVER INVENTED: the ONLY source of session rows on screen is the fetched
  // list. A well-meaning "helpfully" default -- e.g. auto-selecting
  // approvalSessions.data.sessions[0] -- would defeat the picker's whole
  // point (a session the SERVER did not measure as ledger-bearing must never
  // be sent), so this bans indexing into the fetched array anywhere outside
  // the options-rendering map.
  const topBar = sliceBetween(page, "{/* R-5c: the approval-history session picker.", "<div className=\"flex items-center gap-2\">");
  pin.match(topBar, /\S/, "expected to find the R-5c picker's own top-bar slice (markers moved?)");
  pin.doesNotMatch(
    withoutComments(topBar),
    /approvalSessions\.data\.sessions\[|approvalSessions\.data\.sessions\.find|approvalSessions\.data\.sessions\.at\(/,
    "expected the picker to render every fetched session as an <option> and pick none of them automatically -- selection is the user's act, not this screen's guess",
  );

  // THE THREE STATES, structurally: loading, a real non-empty list (the
  // <select>), and everything else (zero sessions OR an unread list) as one
  // named line -- never an empty <select> with only the placeholder option.
  pin.match(
    topBar,
    /approvalSessions\.loading \? \([\s\S]{0,400}approvalSessions\.data !== null && approvalSessions\.data\.sessions\.length > 0 \? \([\s\S]{0,2000}<select[\s\S]{0,2000}\) : \(/,
    "expected the picker to gate the <select> on a REAL non-empty fetched list, with loading handled as its own branch first",
  );
  pin.doesNotMatch(
    withoutComments(topBar),
    /<select[^>]*>\s*(?:\{[^}]*\}\s*)?<option value="">[^<]*<\/option>\s*<\/select>/,
    "expected the zero-sessions state to render a named line, not a <select> holding only the placeholder option",
  );

  // THE CAP DISCLOSURE (refuter fix, 2026-08-25): the backend caps this list
  // (default 20, ?limit= up to 200) and returns `total`, the count BEFORE
  // truncation, specifically so an older approval-bearing session going
  // missing is never silent. This pins the comparison structurally --
  // `total > sessions.length` -- so a mutation that always/never shows the
  // note, or that compares the wrong pair of numbers, fails here.
  pin.match(
    topBar,
    /approvalSessions\.data\.total > approvalSessions\.data\.sessions\.length \? \(/,
    "expected the truncation note to be gated on total EXCEEDING the returned count, not on any other condition",
  );
  pin.match(
    topBar,
    tripleArgs(
      "최근 ${approvalSessions.data.sessions.length}건만 표시됩니다 (전체 ${approvalSessions.data.total}건) — 더 오래된 세션은 목록에 없을 수 있습니다.",
      "Showing only the most recent ${approvalSessions.data.sessions.length} of ${approvalSessions.data.total} sessions — older ones may be missing from this list.",
      "直近${approvalSessions.data.sessions.length}件のみ表示（全${approvalSessions.data.total}件）— これより古いセッションは一覧にない場合があります。",
      "`",
    ),
    "expected the truncation note's exact ko/en/ja triple, each interpolating the SHOWN count and the TOTAL",
  );

  // ko/en/ja for the picker's own strings, and none of them may go blank --
  // the general per-file ban lower in this suite covers every t() call, this
  // just names the R-5c ones for a reader tracing this unit specifically.
  for (const [ko, en, ja] of [
    ["승인 이력 세션", "Approval-history session", "承認履歴セッション"],
    ["승인 이력 세션 목록을 불러오는 중…", "Loading approval-history sessions…", "承認履歴セッション一覧を読み込み中…"],
    ["승인 이력을 가진 세션이 없습니다.", "No session carries an approval history.", "承認履歴を持つセッションがありません。"],
  ] as const) {
    pin.match(source, tripleArgs(ko, en, ja), `expected the R-5c picker's exact ko/en/ja triple for ${ko}`);
  }
});

contractTest("R-5b: every absence and every limitation on /report is the server's own sentence", (pin) => {
  const page = read("app/report/page.tsx");
  const source = withoutComments(page);

  // Each of the five places this screen states something about the user's
  // model, and the server field it states it from.
  //
  // CLOSING-BOUNDARY ANCHORED (the #55b round-3 idiom), because presence alone
  // is not verbatim. MEASURED (refuter, 2026-08-25): appending `.slice(0, 20)`
  // to two of these five expressions truncated the backend's sentence on screen
  // and every pin here stayed green -- a substring pin matches the head of a
  // longer expression, so `pick(x, lang)` matched inside `pick(x, lang).slice(
  // 0, 20)`. Each pattern below therefore carries the expression's own closing
  // brace AND the JSX token that must follow it, so any inserted call, slice,
  // replace or concatenation breaks the anchor.
  for (const [pattern, what] of [
    [/\{pick\(item\.figure\.reason, lang\)\}\s*<\/span>/, "the spine tree's per-figure absence"],
    [/\{pick\(figure\.reason, lang\)\}<\/p>/, "the preview's per-figure absence"],
    [/\{pick\(limitation\.reason, lang\)\}\s*<\/p>/, "the typesetter's named limitations"],
    [/\{pick\(fallback\.reason, lang\)\}\s*\{fallback\.profile_reason/, "each style fallback's reason"],
    [/\{pick\(refusal\.message, lang\)\}<\/p>/, "a compose refusal"],
  ] as const) {
    pin.match(source, pattern, `expected ${what} to render the backend's own sentence WHOLE -- nothing may be appended to it`);
  }
  // BOTH SITES, by count. MEASURED (mutation, 2026-08-25): the NOT_AVAILABLE
  // output renders in TWO places -- the spine tree and the preview -- and a
  // bare substring pin stayed green while one of them was replaced with a
  // client-written "no reason" sentence, because the other one still matched.
  // The same shape #58's `수준 / 계획 ID` count pin exists for.
  //
  // RE-ANCHORED (#60b, docs/phase2/DECISIONS.md "2026-08-25 -- 사용자 테스트
  // 1차 피드백"): the two inline renders this pin used to count were folded
  // into ONE shared `NotAvailableFold` component -- R-1's own reason for a
  // NOT_AVAILABLE output runs 200-490 characters live, paragraph length, and
  // the user's own words named exactly this shape ("per-output NOT_AVAILABLE
  // reason paragraphs") as fine print to move behind the app's established
  // 자세히 fold. The literal `{output.name}: {output.reason}` line the OLD
  // pin counted now appears once, not twice, by construction -- sharing the
  // renderer is a STRONGER guarantee against "one site drifts, the other
  // doesn't" than duplicating it ever was, since there is only one
  // implementation left to drift. This pin now counts the TWO CALL SITES
  // instead, so a mutation that drops either call (or inlines a second,
  // divergent renderer past the shared one) still fails here.
  pin.match(
    source,
    /\{output\.name\}: \{output\.reason\}/,
    "expected NotAvailableFold's shared render to carry R-1's own measured reason for a NOT_AVAILABLE output, verbatim",
  );
  pin.match(
    `notAvailableFoldCallSites=${(source.match(/<NotAvailableFold notAvailable=\{notAvailable\} t=\{t\} \/>/g) ?? []).length}`,
    /^notAvailableFoldCallSites=2$/,
    "expected BOTH the tree row and the preview row to hand their own NOT_AVAILABLE list to the shared fold -- one of them drifting (a second inline renderer, or a dropped call) is how half a screen stops naming a gap",
  );
  pin.match(
    `absenceSentences=${(source.match(/\{pick\(figure\.reason, lang\)\}<\/p>/g) ?? []).length}`,
    /^absenceSentences=2$/,
    "expected BOTH figure kinds' absent branches to render the server's reason WHOLE -- table and graphic, counted with their closing boundary, not matched once as a substring",
  );

  // NO FABRICATED PREVIEW FOR AN ABSENT FIGURE. Both absent branches are
  // structurally anchored: they may show the figure id and the server's
  // reason, and they must not reach for rows, columns or an SVG that a
  // present figure would have had.
  // SINGLE-LINE SLICE MARKERS, on purpose. This repo's working tree flips
  // between LF and CRLF (core.autocrlf=true over `* text=auto`), and a marker
  // carrying an interior newline stops matching the moment a checkout writes
  // CRLF. Every slice below still carries its own `\S` guard, so a marker that
  // really did move fails loudly instead of emptying the haystack -- which is
  // the dangerous direction for the `doesNotMatch` pins that follow it.
  for (const [component, closer, tail] of [
    ["function TableFigureBlock(", "function GraphicFigureBlock(", "const columns = figure.columns"],
    ["function GraphicFigureBlock(", "/** The design doc's rule", "const svg = figure.svg;"],
  ] as const) {
    const absentBranch = withoutComments(
      sliceBetween(sliceBetween(page, component, closer), "if (figure.absent) {", tail),
    );
    pin.match(absentBranch, /\S/, `expected to find ${component}'s absent branch (markers moved?)`);
    pin.match(absentBranch, /pick\(figure\.reason, lang\)/, `${component}: an absent figure must state the server's reason`);
    pin.doesNotMatch(
      absentBranch,
      /figure\.rows|figure\.columns|figure\.svg|figure\.legend|figure\.meta/,
      `${component}: an absent figure has no rows, columns or picture -- nothing may be drawn in their place`,
    );
  }

  // THE CLASS, per the recorded threat-model boundary: a substring pin cannot
  // tell rendered code from code behind a falsy operand, so the whole family
  // is banned file-wide (comments stripped -- this one names it while
  // explaining it).
  pin.doesNotMatch(
    source,
    /(?<![=!<>]\s{0,3})\b(?:false|0|null|undefined)\s*&&|&&\s*(?:false|0|null|undefined)\b(?!\s{0,3}[=!<>])/,
    "expected no dead-branch short-circuit (false/0/null/undefined && ...) anywhere on the report screen -- that is how a named absence becomes a pinned string nobody sees",
  );
  // And the visibility half of the same family: on a screen whose whole job is
  // to publish what the document could NOT do, hiding any of it is the same
  // lie by another mechanism. MEASURED 2026-08-25: none of these appears in
  // the file, so the ban is flat.
  pin.doesNotMatch(
    source,
    /display:\s*["']?none|visibility:\s*["']?hidden|opacity-0\b|\bsr-only\b|aria-hidden|\binvisible\b/i,
    "expected no visibility-suppression idiom on the report screen -- a limitation that is rendered but unseeable is not disclosed",
  );
});

contractTest("R-5b: the receipt describes THIS compose, and cannot lag one behind", (pin) => {
  const page = read("app/report/page.tsx");
  const source = withoutComments(page);

  // MEASURED (refuter, 2026-08-25): caching the manifest in a `useRef` and
  // rendering the PREVIOUS one survived every pin -- the receipt then reports
  // the last document's limitations under this document's filename, which is
  // the exact class of lie this whole screen exists to prevent. Nothing here
  // asserted WHEN the receipt state is written, only what it contains, so the
  // fix is structural: the state machine's shape is pinned, not its output.

  // (1) THE CLEAR, first and unconditional. A compose that has begun must not
  // leave the previous compose's receipt on screen for even one frame.
  pin.match(
    source,
    /setBusy\(mode\);\s*setRefusal\(null\);\s*setFailure\(null\);\s*setOutcome\(null\);/,
    "expected every compose to clear the previous refusal, failure AND receipt before it starts -- adjacency, so a reordering that leaves a stale receipt up fails here",
  );

  // (2) THE SET, from THIS compose's own fetch result. The manifest handed to
  // setOutcome must be the `manifest` bound on the line immediately above it,
  // which is the one this call awaited -- no cache, no ref, no previous value
  // may sit between the read and the store. Same discipline the review-fetch
  // pin states elsewhere in this suite ("no transform, spread-and-patch, or
  // rebuild may sit between the fetch and the store").
  pin.match(
    source,
    /const manifest = await readReportManifest\(bundle\.blob\);\s*setOutcome\(\{ docx, bundle: \{ filename: bundle\.filename, artifacts: bundle\.artifacts \}, manifest \}\);/,
    "expected the receipt to be stored directly from THIS compose's manifest read, on the very next line",
  );

  // (3) TWO WRITERS AND NO MORE: the clear and the set. A third would be a
  // second story about what the last compose produced.
  pin.match(
    `outcomeWrites=${(source.match(/setOutcome\(/g) ?? []).length}`,
    /^outcomeWrites=2$/,
    "expected exactly two setOutcome call sites -- the clear at compose start and the store at compose end",
  );
  pin.match(
    `manifestReads=${(source.match(/readReportManifest\(/g) ?? []).length}`,
    /^manifestReads=1$/,
    "expected the manifest to be read in exactly one place -- a second read is a second answer about the same document",
  );

  // (4) AND NO OUT-OF-BAND STORE. MEASURED 2026-08-25: this file carries no
  // `useRef` at all, so the ban is flat and needs no carve. A ref is how the
  // refuter's stale-manifest cache was written, and on a screen whose state is
  // all derived-from-one-request there is no legitimate mutable box for it.
  pin.doesNotMatch(
    source,
    /useRef|MutableRefObject|\.current\s*=/,
    "expected no ref-shaped storage on the report screen -- a value that survives a re-render outside React's state is exactly where a previous compose's manifest hides",
  );
});

contractTest("R-5b: the inline SVG comes from the API payload and from nowhere else", (pin) => {
  const page = read("app/report/page.tsx");
  const source = withoutComments(page);

  // EXACTLY ONE injection on this screen, and its argument is the local bound
  // from the payload's own field one line earlier.
  pin.match(
    `reportInjections=${(source.match(/dangerouslySetInnerHTML/g) ?? []).length}`,
    /^reportInjections=1$/,
    "expected exactly ONE dangerouslySetInnerHTML on the report screen -- a second one is a second, unpinned source of markup",
  );
  pin.match(source, /const svg = figure\.svg;/, "expected the injected string to be bound directly from the payload's svg field");
  pin.match(
    source,
    /dangerouslySetInnerHTML=\{\{ __html: svg \}\}/,
    "expected the injection to be handed that binding and nothing else -- no concatenation, no template, no second source",
  );
  pin.doesNotMatch(source, /__html: (?!svg \}\})/, "expected no other __html source anywhere on the report screen");

  // The app-wide inventory, so a THIRD injection cannot appear unnoticed
  // somewhere else while this pin guards only /report. Measured 2026-08-25.
  const carriers = appSourceFiles().filter((file) => /dangerouslySetInnerHTML/.test(withoutComments(read(file))));
  pin.match(
    `injectors=${carriers.join(",")}`,
    /^injectors=app\/report\/page\.tsx,components\/DashboardMap\.tsx$/,
    "the raw-markup escape hatch must stay confined to the two files that carry it -- add nothing, and strike a file when it is converted",
  );

  // ...AND THE OTHER HALF OF THE CLASS, which the census above is blind to.
  // MEASURED (refuter, 2026-08-25): ADDING `ref.current.innerHTML =
  // props.citation` in a useEffect, beside the untouched
  // dangerouslySetInnerHTML, kept BOTH pins above green -- they regex one
  // literal token, and React's JSX prop is only one of the ways this app can
  // put a string into the DOM as markup. An ordinary-looking ref effect is
  // exactly the "plausible drift from a well-meaning editor" the recorded
  // threat-model boundary puts in scope (docs/phase2/DECISIONS.md,
  // "2026-08-24 — 계약 핀의 위협 모델 경계"), so the ban is stated over the DOM
  // API class rather than over the one spelling the JSX census can see.
  for (const file of appSourceFiles()) {
    pin.doesNotMatch(
      withoutComments(read(file)),
      /\.outerHTML\s*=|insertAdjacentHTML|document\.write\s*\(/,
      `${file}: outerHTML/insertAdjacentHTML/document.write put a string into the DOM as markup with no pin on its source -- there is no legitimate use of any of them in this app`,
    );
  }

  // `.innerHTML =` has exactly ONE legitimate shape here, and it injects
  // nothing: five map components clear their Leaflet container before
  // re-mounting it. Both halves are asserted -- the SHAPE (every assignment in
  // the app is the empty-string clear, so an assignment of any expression dies
  // wherever it is written) and the FILE SET (a sixth file adopting even the
  // clear has to be re-measured rather than inheriting the carve).
  const innerHtmlAssignments = appSourceFiles().flatMap((file) =>
    [...withoutComments(read(file)).matchAll(/[^\n]*\.innerHTML\s*=[^\n]*/g)].map((match) => ({
      file,
      line: match[0].trim(),
    })),
  );
  for (const { file, line } of innerHtmlAssignments) {
    pin.match(
      line,
      /^container\.innerHTML = "";$/,
      `${file}: the only permitted .innerHTML assignment in this app is the map container's empty-string clear -- assigning any expression is an unpinned markup injection`,
    );
  }
  pin.match(
    `innerHtmlFiles=${[...new Set(innerHtmlAssignments.map((entry) => entry.file))].join(",")}`,
    /^innerHtmlFiles=components\/BaseValidationMap\.tsx,components\/DashboardMap\.tsx,components\/IntersectionCompareMap\.tsx,components\/genesis\/GenesisTileMap\.tsx,components\/genesis\/RegionLassoMap\.tsx$/,
    "the container-clear idiom must stay confined to the five map components that carry it -- a sixth file is a re-measurement, not an inherited exemption",
  );
});

contractTest("R-5b: excluded-by-selection is the user's choice, and is never worded as an evidence gap", (pin) => {
  const page = withoutComments(read("app/report/page.tsx"));

  // THE THIRD STATE, in both places it renders: the tree's toggle and the
  // preview's collapsed card. Different sentences, both saying the same thing
  // -- that this is a selection, not an absence.
  pin.match(
    page,
    tripleArgs(
      "선택에서 제외됨 — 증거가 없어서가 아니라 사용자가 뺐습니다.",
      "Excluded by selection — the user left it out; it is not an evidence gap.",
      "選択から除外 — 証拠がないからではなく、利用者が外しました。",
    ),
    "expected the tree's excluded row to say the section was left out BY THE USER",
  );
  pin.match(
    page,
    tripleArgs(
      "선택에서 제외됨 — 이 절은 문서에 실리지 않습니다. 증거가 없다는 뜻이 아닙니다.",
      "Excluded by selection — this section is not typeset into the document. It does not mean there is no evidence.",
      "選択から除外 — この節は文書に載りません。証拠がないという意味ではありません。",
    ),
    "expected the preview's excluded card to deny the evidence-gap reading in its own words",
  );

  // AND THE EVIDENCE-GAP SENTENCE, which is a DIFFERENT one. If a future edit
  // collapses the two into one wording, one of these two pins fails.
  pin.match(
    page,
    tripleArgs("이 절의 증거 없음", "No evidence for this section", "この節の証拠なし"),
    "expected the evidence-absence wording the design doc names ('이 절의 증거 없음')",
  );
  // TWO sites carry it -- the tree row and the preview card -- so one of them
  // drifting fails on the count as well as on the text. Same measured shape as
  // the NOT_AVAILABLE row pair above.
  pin.match(
    `evidenceAbsentSites=${(page.match(/"이 절의 증거 없음", "No evidence for this section", "この節の証拠なし"/g) ?? []).length}`,
    /^evidenceAbsentSites=2$/,
    "expected the evidence-absence triple in BOTH the tree and the preview -- one losing it is how the two panes start disagreeing about the same section",
  );
  const excludedBranch = withoutComments(
    sliceBetween(
      read("app/report/page.tsx"),
      "  if (!included) {",
      '<h3 className="text-[15px] font-semibold text-white">{title}</h3>',
    ),
  );
  pin.match(excludedBranch, /\S/, "expected to find the preview's excluded branch (markers moved?)");
  pin.doesNotMatch(
    excludedBranch,
    /이 절의 증거 없음/,
    "the excluded branch must NOT borrow the evidence-absence sentence -- the whole point of the third state is that the two read differently",
  );

  // The manifest's own name for the state is what the receipt branches on --
  // never a string this file typed.
  pin.match(
    page,
    /section\.state === SECTION_STATE_EXCLUDED/,
    "expected the receipt to branch on the typesetter's own constant for the third state",
  );
  pin.match(
    read("lib/reportManifest.ts"),
    /export const SECTION_STATE_EXCLUDED = "excluded_by_selection";/,
    "expected the third state's id to match services/report/typeset.py's SECTION_STATE_EXCLUDED",
  );
});

contractTest("R-5b: every string this unit added is a real ko/en/ja triple", (pin) => {
  // THE #50-d / #57 / #58 CLASS. `t(ko, en, ja?)` types `ja` as optional, so
  // blanking it passes typecheck and every behavioural pin above. Quote-
  // boundary anchored: only the exact ko AND en AND ja text satisfies these.
  const page = withoutComments(read("app/report/page.tsx"));

  const PLAIN_TRIPLES: [string, string, string][] = [
    ["보고서", "Report", "レポート"],
    [
      "MRWA가 요구하는 절 구조에 이 제품의 도판을 채워 Word 보고서를 조판합니다.",
      "Typesets a Word report by filling the MRWA-required section structure with this product's figures.",
      "MRWAが要求する節構造にこの製品の図版を入れてWordレポートを組版します。",
    ],
    ["채점할 실행", "Run to score", "採点する実行"],
    ["전후 비교 — 전", "Before/after — before", "前後比較 — 前"],
    ["전후 비교 — 후", "Before/after — after", "前後比較 — 後"],
    ["스타일 프로파일", "Style profile", "スタイルプロファイル"],
    ["Prism 기본 스타일", "Prism default style", "Prism 既定スタイル"],
    ["보고서 언어", "Report language", "レポート言語"],
    ["화면 언어를 따름", "Follow the screen", "画面の言語に従う"],
    ["선택 안 함", "Not selected", "選択しない"],
    // R-5c (docs/phase2/DECISIONS.md, 2026-08-24 "승인 이력 세션 픽커는 위임"):
    // the approval-history session picker's own three states.
    ["승인 이력 세션", "Approval-history session", "承認履歴セッション"],
    [
      "승인 이력 세션 목록을 불러오는 중…",
      "Loading approval-history sessions…",
      "承認履歴セッション一覧を読み込み中…",
    ],
    [
      "승인 이력을 가진 세션이 없습니다.",
      "No session carries an approval history.",
      "承認履歴を持つセッションがありません。",
    ],
    [
      "승인 이력이 있는 세션 목록을 읽지 못했습니다 — 세션을 선택하지 않은 것과 동일하게 처리됩니다.",
      "The list of sessions with an approval history could not be read — treated the same as none selected.",
      "承認履歴のあるセッション一覧を読めませんでした — 選択なしと同様に扱われます。",
    ],
    ["생성 (DOCX + 도판 zip)", "Generate (DOCX + figure zip)", "生成 (DOCX + 図版zip)"],
    ["도판 원본 zip만", "Figure originals zip only", "図版原本zipのみ"],
    ["생성 중…", "Generating…", "生成中…"],
    ["내보내는 중…", "Exporting…", "書き出し中…"],
    ["보고서 스파인", "Report spine", "レポートスパイン"],
    ["절에 묶이지 않은 도판", "Figures bound to no section", "節に紐づかない図版"],
    ["증거 있음", "evidence present", "証拠あり"],
    ["추출 불가", "not extractable", "抽出不可"],
    ["출처 정보 없음", "No source caption", "出典情報なし"],
    ["보고서를 만들지 않았습니다", "No report was produced", "レポートは作成されませんでした"],
    ["생성 요청이 실패했습니다", "The compose request failed", "生成リクエストが失敗しました"],
    ["생성 완료", "Composed", "生成完了"],
    ["이 문서가 하지 못한 것", "What this document does not do", "この文書ができなかったこと"],
    ["절 상태", "Section states", "節の状態"],
    ["선택에서 제외됨", "excluded by selection", "選択から除外"],
    ["스타일", "Style", "スタイル"],
    ["이 도판에는 SVG가 실려 있지 않습니다.", "This figure carries no SVG.", "この図版にはSVGが含まれていません。"],
    [
      "저장된 스타일 프로파일 목록을 읽지 못했습니다 — Prism 기본 스타일로만 생성할 수 있습니다.",
      "The stored style profiles could not be read — only the Prism default style is available.",
      "保存済みスタイルプロファイル一覧を読めませんでした — Prism既定スタイルでのみ生成できます。",
    ],
    [
      "스파인을 읽지 못했습니다 — 절 목록 없이는 보고서를 만들 수 없습니다.",
      "The spine could not be read — no report can be built without the section list.",
      "スパインを読めませんでした — 節一覧なしではレポートを作成できません。",
    ],
    [
      "도판을 읽지 못했습니다 — 아래 미리보기는 읽어낸 것만 보여 줍니다.",
      "The figures could not be read — the preview below shows only what was read.",
      "図版を読めませんでした — 以下のプレビューは読み取れた分のみを表示します。",
    ],
    [
      "문서는 만들지 않았습니다 — 도판 원본 zip만 내보냈습니다.",
      "No document was produced — only the figure-originals zip was exported.",
      "文書は作成していません — 図版原本zipのみ書き出しました。",
    ],
    ["기존 내보내기 (구 워크북 경로)", "Legacy export (old workbook path)", "従来のエクスポート (旧ワークブック経路)"],
    [
      "이 브라우저는 zip 압축을 풀 수 없어 매니페스트를 읽지 못했습니다. zip 파일 자체는 정상적으로 내려받았습니다.",
      "This browser cannot inflate the bundle, so the manifest was not read. The zip itself downloaded fine.",
      "このブラウザはzipを展開できないためマニフェストを読めませんでした。zipファイル自体は正常にダウンロードされています。",
    ],
    [
      "내려받은 zip에 manifest.json이 없습니다.",
      "The downloaded zip carries no manifest.json.",
      "ダウンロードしたzipにmanifest.jsonがありません。",
    ],
    [
      "manifest.json이 이 화면이 풀 수 없는 방식으로 압축되어 있습니다.",
      "manifest.json is stored with a compression method this screen cannot read.",
      "manifest.jsonはこの画面が展開できない圧縮方式で格納されています。",
    ],
    [
      "manifest.json을 JSON으로 해석하지 못했습니다.",
      "manifest.json could not be parsed as JSON.",
      "manifest.jsonをJSONとして解釈できませんでした。",
    ],
    [
      "내려받은 파일을 zip으로 읽지 못했습니다.",
      "The downloaded file could not be read as a zip.",
      "ダウンロードしたファイルをzipとして読めませんでした。",
    ],
    // #60b (docs/phase2/DECISIONS.md, 2026-08-25 "/report 정리 + 프로파일
    // 문"): the four 자세히 folds that took the MRWA quotes, the NOT_AVAILABLE
    // paragraphs and the source-caption/citation lines off the default
    // surface, the profile-upload door's own three states, and the text
    // preview's own affordances.
    ["자세히 (스파인의 근거)", "More (the spine's own basis)", "詳細（スパインの根拠）"],
    ["자세히 (근거 조항 원문)", "More (the regulation clause text)", "詳細（根拠条文原文）"],
    [
      "자세히 (추출 불가 사유)",
      "More (why these could not be extracted)",
      "詳細（抽出できなかった理由）",
    ],
    ["자세히 (출처·인용)", "More (source, citation)", "詳細（出典・引用）"],
    ["프로파일 업로드", "Upload profile", "プロファイルのアップロード"],
    [
      "스타일 프로파일을 업로드하려면 로그인이 필요합니다.",
      "Signing in is required to upload a style profile.",
      "スタイルプロファイルをアップロードするにはログインが必要です。",
    ],
    ["로그인", "Sign in", "ログイン"],
    ["업로드 중…", "Uploading…", "アップロード中…"],
    ["미리보기 (텍스트)", "Preview (text)", "プレビュー（テキスト）"],
    ["다운로드 (.md)", "Download (.md)", "ダウンロード（.md）"],
    [
      "서식 없는 텍스트 사본입니다 — 스타일 미리보기가 아닙니다.",
      "A styling-free text copy — not a style preview.",
      "書式のないテキスト写しです — スタイルプレビューではありません。",
    ],
    // #64 (second live-test round, 2026-08-26): the text-preview pane's own
    // raw/structure toggle ("can u not show the raw md file but in preview
    // mode?"), and the FIFTH 자세히 fold -- the spine tree card's per-figure
    // evidence detail, folded away from the chip on the card face (below).
    ["원문 보기 (.md)", "View raw (.md)", "原文を見る（.md）"],
    ["구조로 보기", "View as structure", "構造で見る"],
    [
      "자세히 (도판별 증거 상세)",
      "More (per-figure evidence detail)",
      "詳細（図版別証拠詳細）",
    ],
  ];
  for (const [ko, en, ja] of PLAIN_TRIPLES) {
    pin.match(page, tripleArgs(ko, en, ja), `expected the report screen's exact ko/en/ja triple for ${ko}`);
  }

  // The interpolated ones take backticks, so they are anchored on those. Each
  // is a COUNT sentence, which is exactly where a blanked ja hides longest.
  const TEMPLATE_TRIPLES: [string, string, string][] = [
    ["절 ${docx.sections}개", "${docx.sections} section(s)", "節 ${docx.sections}個"],
    ["명명된 한계 ${docx.limitations}건", "${docx.limitations} named limitation(s)", "名前付き限界 ${docx.limitations}件"],
    ["도판 원본 ${bundle.artifacts}개", "${bundle.artifacts} figure original(s)", "図版原本 ${bundle.artifacts}個"],
    [
      "폴백 ${manifest.style.fallbacks.length}건",
      "${manifest.style.fallbacks.length} fallback(s)",
      "フォールバック ${manifest.style.fallbacks.length}件",
    ],
    [
      "실림 · 명명된 부재 ${section.named_absences.length}건",
      "included · ${section.named_absences.length} named absence(s)",
      "収録 · 名前付き不在 ${section.named_absences.length}件",
    ],
    // #60b: the upload door's own success line, naming the SAVED profile.
    [
      "새 프로파일을 저장했습니다: ${uploadedProfile.name}",
      "Saved a new profile: ${uploadedProfile.name}",
      "新しいプロファイルを保存しました: ${uploadedProfile.name}",
    ],
    // #64: the spine card's two evidence chips -- honest-absence doctrine
    // names the KIND and the COUNT, never a bare icon or a bare "있음/없음".
    [
      "증거 있음 ${presentFigures.length}건",
      "${presentFigures.length} present",
      "証拠あり ${presentFigures.length}件",
    ],
    [
      "증거 없음 ${absentFigures.length}건",
      "${absentFigures.length} absent",
      "証拠なし ${absentFigures.length}件",
    ],
  ];
  for (const [ko, en, ja] of TEMPLATE_TRIPLES) {
    pin.match(page, tripleArgs(ko, en, ja, "`"), `expected the report screen's exact ko/en/ja triple for ${ko}`);
  }

  // AND THE CLASS-LEVEL BAN: no `t()` call on this screen may take a blank or
  // missing third argument. A two-argument call is what a blanking mutation
  // degrades to, so both shapes are banned outright.
  pin.doesNotMatch(
    page,
    /\bt\((["`])(?:[^"`\\]|\\.)*\1\s*,\s*(["`])(?:[^"`\\]|\\.)*\2\s*\)/,
    "every t() on the report screen must carry a ja argument -- a two-argument call is how a language quietly drops out",
  );
  pin.doesNotMatch(page, /,\s*""\s*\)/, "no t() argument on the report screen may be the empty string");
});

contractTest("R-5b: the manifest reader names every way it can fail, and the screen has a sentence for each", (pin) => {
  const lib = read("lib/reportManifest.ts");
  const page = read("app/report/page.tsx");

  // SET EQUALITY between the reasons the reader can return and the reasons the
  // screen can say -- the #50-d idiom. A new reason with no sentence, or a
  // sentence for a reason that cannot happen, fails here. (`not_a_zip` is the
  // fall-through arm and is named in the union rather than in an `if`, so it
  // is read off the union alone and asserted to be exactly the remainder.)
  const union = sliceBetween(lib, "export type ManifestUnreadableReason =", "export type ManifestRead");
  pin.match(union, /\S/, "expected to find the failure-reason union (declaration moved?)");
  const declared = [...union.matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
  const answered = [...page.matchAll(/reason === "([a-z_]+)"/g)].map((match) => match[1]);
  pin.match(
    `declared=${declared.slice().sort().join(",")}`,
    /^declared=entry_missing,no_decompression_stream,not_a_zip,not_json,unsupported_compression$/,
    "the manifest reader's failure vocabulary must stay exactly these five -- a sixth needs a sentence on the screen too",
  );
  pin.match(
    `answered=${answered.slice().sort().join(",")}`,
    /^answered=entry_missing,no_decompression_stream,not_json,unsupported_compression$/,
    "the screen must answer every reason but the fall-through one by name",
  );

  // The parser's two load-bearing details, each a real bug if reversed.
  pin.match(
    lib,
    /const localNameLength = view\.getUint16\(localAt \+ 26, true\);/,
    "expected the LOCAL header's own name length to be read -- the central directory's differs and lands mid-payload",
  );
  pin.match(
    lib,
    /new Blob\(\[new Uint8Array\(payload\)\]\)/,
    "expected the entry's bytes to be COPIED before the Blob -- a Blob over the subarray's backing buffer carries the whole archive",
  );
  // It reads ONE entry. Anything that walks the archive for the caller is a
  // second product (an unpacker) hiding in a reader.
  pin.doesNotMatch(
    withoutComments(lib),
    /writeFile|showSaveFilePicker|createObjectURL/,
    "the manifest reader must not write or offer files -- the SVGs in the same bundle are the user's download, not this module's to unpack",
  );
});

test("R-5b: the manifest reader reads a real python zipfile bundle, and names what it cannot read", async () => {
  // THE FIXTURE IS THE PRODUCER'S OWN OUTPUT: base64 of a zip written by
  // Python's `zipfile.ZipFile(..., ZIP_DEFLATED)` -- the exact writer
  // services/report/typeset.py's compose_artifact_zip uses -- carrying an SVG
  // entry BEFORE the manifest, so the central-directory walk has to step past
  // a non-matching entry to find it. A hand-rolled fixture would only prove
  // the reader agrees with a writer this repo does not ship.
  const bundle =
    "UEsDBBQAAAAIADKWGV3bwxdcEAAAAJsBAAAWAAAAc3ZnL25ldHdvcmtfc2tldGNoLnN2Z7MpLku3qxgFgwrY6INiBQBQSwMEFAAAAAgAMpYZXTwVdn8kAQAAnQEAAA0AAABtYW5pZmVzdC5qc29udZDNSgMxFIX3fYpL1jLg1lcRCZnJbY2dSYZJLC2l0PgD0hZcqLiorrR0Z10oCFZ9mGEUnHkIM9FaN24u3HPPOSFfvwFA2kJysgUkzYROggxTlRlqeilqNDRhUjRRm6CzSTZqdywSYZgRSmoX2nYSQN9Pd4wUx7pKd1pUKkMxCZFz5FRIylXU9RXemSHTSjrvKus0rHciFaQxi3BXxRwzEAlrIQgNTdZG/lvg7Husthf2Jbd3H+fPxeFpbhfvk7fieJbby9y+5naaH5z9jbRVHfm8H1bzK6iuH8rFUzUflrMllHZZTSdQHt04AaqLUTV6LMcn5fg2ID/5QWM1dzwJjdE/GITnGbFYhJlHtf63duw8IuxG8X6NJuxRjfF3F1k/0Rh8AVBLAQIUABQAAAAIADKWGV3bwxdcEAAAAJsBAAAWAAAAAAAAAAAAAACAAQAAAABzdmcvbmV0d29ya19za2V0Y2guc3ZnUEsBAhQAFAAAAAgAMpYZXTwVdn8kAQAAnQEAAA0AAAAAAAAAAAAAAIABRAAAAG1hbmlmZXN0Lmpzb25QSwUGAAAAAAIAAgB/AAAAkwEAAAAA";
  const bytes = Uint8Array.from(Buffer.from(bundle, "base64"));

  const result = await readReportManifest(new Blob([new Uint8Array(bytes)]));
  assert.equal(result.ok, true, "a real ZIP_DEFLATED bundle must be readable");
  if (result.ok) {
    assert.equal(result.manifest.kind, "prism.report_typeset_manifest.v1");
    // The limitation SENTENCE survives the trip in all three languages -- this
    // is the whole reason the bundle is opened at all.
    assert.deepEqual(result.manifest.limitations[0].reason, {
      ko: "가짜 이미지를 끼워 넣지 않습니다.",
      en: "no placeholder image is faked",
      ja: "偽の画像は挿入しません",
    });
    assert.equal(result.manifest.sections[0].state, "excluded_by_selection");
  }

  // ...and the failures are NAMED, not swallowed. A bare `null` for both would
  // make "the manifest is unreadable" and "the manifest says nothing" the same
  // thing on screen.
  assert.deepEqual(await readReportManifest(new Blob([new Uint8Array([1, 2, 3])])), {
    ok: false,
    reason: "not_a_zip",
  });
  assert.deepEqual(await readReportManifest(new Blob([bytes.slice(0, 200)])), {
    ok: false,
    reason: "not_a_zip",
  });
});

contractTest("R-5b: no spread may smuggle an attribute past the report screen's pins", (pin) => {
  // The #58 detector, applied to this unit's files. An object-literal spread is
  // legitimate (composeRequestBody carries one); a JSX ATTRIBUTE spread is
  // banned, because every affordance and every wording pin above matches
  // literal attribute names, and a spread has none.
  const attributeSpreads = (source: string): number =>
    [...source.matchAll(/\{\s*\.\.\./g)].filter((match) => {
      const before = source.slice(0, match.index).replace(/\s+$/, "");
      return !("=([,:?{>".includes(before.slice(-1)) || /\breturn$/.test(before));
    }).length;

  for (const file of REPORT_SURFACE_FILES) {
    const source = withoutComments(read(file));
    pin.match(
      `attrSpreads=${attributeSpreads(source)}`,
      /^attrSpreads=0$/,
      `${file}: no spread may sit in attribute position -- \`{...{onClick: x}}\` is invisible to every attribute-name pin above`,
    );
  }
});

// Style/layout pins, in their own test per this file's rule about keeping
// brittle restyle pins away from behavioural ones.
contractTest("R-5b: the report surface has a 12px floor and no amber", (pin) => {
  for (const file of REPORT_SURFACE_FILES) {
    const source = withoutComments(read(file));
    pin.doesNotMatch(
      source,
      /text-\[(?:[0-9]|10|11)(?:\.5)?px\]/,
      `${file}: nothing on the report surface may go below the 12px floor`,
    );
    pin.doesNotMatch(source, UNMAPPED_SKY_BORDER, `${file}: border-sky-300 keeps Tailwind's stock cyan under the dark remap`);
  }

  // THE AMBER BAN, and the ONE measured exception. The rebuilt page carries no
  // amber; the demoted legacy builder does, in the pre-existing "no dataset
  // uploaded, using the bundled sample" banner it arrived with. That banner is
  // MOVED CODE, not new work -- R-5b lifted the old page into a component
  // without touching its body -- so it is named here rather than restyled by a
  // builder the user never asked for a colour decision from. Struck from this
  // exception the day that banner is redesigned.
  pin.doesNotMatch(
    withoutComments(read("app/report/page.tsx")),
    /amber|yellow/i,
    "app/report/page.tsx: no amber on new surface -- rose/emerald/slate are this app's accents",
  );
  pin.match(
    `legacyAmber=${(withoutComments(read("components/report/LegacyReportBuilder.tsx")).match(/amber-\d{2,3}/g) ?? []).length}`,
    /^legacyAmber=3$/,
    "the legacy builder's THREE amber classes are the moved banner and no more -- a fourth means new amber was written into demoted code",
  );
});

// ---------------------------------------------------------------------------
// Unit #60b (docs/phase2/DECISIONS.md, "2026-08-25 -- 사용자 테스트 1차
// 피드백", the #60b block): /report loses its fine print and gains the
// profile-upload door.
//
// User's own words: too many fine texts on the default screen; "if they are
// the paragraphs, dont show them. make a separate preview file and project
// texts as md format. text preview only, no style preview"; and "과거
// 보고서를 올릴 문이 없다" (no door to upload a past report). Two fixes:
//
//  1. DECLUTTER. MEASURED live on this build's own data (not assumed):
//     `section.basis` runs 200-380 characters per quote, a figure's own
//     absence reason runs 50-110 characters, R-1's NOT_AVAILABLE reason runs
//     200-490 characters, and a present figure's source-caption/citation
//     line runs 300-450 characters once `dataset_revision`/`reader` are in
//     it (which they always are). The first, third and fourth are paragraph
//     length and moved behind the app's established 자세히 fold; the second
//     is a genuine one-liner and is UNCHANGED (the R-5b pins above already
//     anchor it verbatim, unmoved).
//  2. THE DOOR. `POST /api/report/style-profiles` (R-4) existed on the
//     backend with nowhere to reach it from the screen -- R-5b only ever
//     READ stored profiles. A file input joins the style-profile picker,
//     authenticated the same way `NetworkDiffView`/`app/admin/page.tsx`
//     already gate on `useAuth()`.
// ---------------------------------------------------------------------------

contractTest(
  "#60b: the MRWA quote, the NOT_AVAILABLE paragraph and the caption/citation line moved behind 자세히",
  (pin) => {
    const page = read("app/report/page.tsx");
    const source = withoutComments(page);

    // THE SPINE-LEVEL QUOTE (`spine.data.minimum.sentence`): MEASURED live at
    // 350 characters -- inside a fold now, not sitting open above the tree.
    const minimumFold = sliceBetween(page, "자세히 (스파인의 근거)", "</details>");
    pin.match(minimumFold, /\S/, "expected to find the spine-level basis fold (markers moved?)");
    pin.match(
      minimumFold,
      /\{spine\.data\.minimum\.sentence\}/,
      "expected the spine's own basis sentence to render INSIDE its fold, not before it",
    );

    // THE PER-SECTION QUOTE: `BasisFold`, called once from the tree row and
    // once from the preview card -- MEASURED at 200-380 characters per quote.
    pin.match(
      `basisFoldCallSites=${(source.match(/<BasisFold basis=\{(?:section\.basis|basis)\} t=\{t\} \/>/g) ?? []).length}`,
      /^basisFoldCallSites=2$/,
      "expected BOTH the tree row and the preview card to hand their own basis array to the shared fold",
    );
    const basisFoldBody = sliceBetween(page, "function BasisFold(props", "function NotAvailableFold(");
    pin.match(basisFoldBody, /\S/, "expected to find BasisFold's own source slice (marker moved?)");
    pin.match(basisFoldBody, /<details/, "expected BasisFold's own body to be a <details> fold, not an open paragraph");
    pin.match(
      basisFoldBody,
      /\{line\}\s*<\/p>/,
      "expected BasisFold to still render each basis LINE verbatim WHOLE -- only its wrapper moved",
    );

    // THE NOT_AVAILABLE PARAGRAPH: `NotAvailableFold` -- its own call-site
    // count is re-anchored in the "every absence and every limitation" test
    // above; this only asserts its BODY is actually a fold.
    const notAvailableFoldBody = sliceBetween(page, "function NotAvailableFold(props", "function EvidenceStateList(");
    pin.match(notAvailableFoldBody, /\S/, "expected to find NotAvailableFold's own source slice (marker moved?)");
    pin.match(notAvailableFoldBody, /<details/, "expected NotAvailableFold's own body to be a <details> fold");

    // THE CAPTION/CITATION LINE: `CaptionFold`, called from both figure
    // kinds -- MEASURED at 300-450 characters once `dataset_revision`/
    // `reader` sit in the caption (they always do for a present figure).
    pin.match(
      `captionFoldCallSites=${(source.match(/<CaptionFold caption=\{figure\.source_caption\} citation=\{figure\.citation\} t=\{t\} \/>/g) ?? []).length}`,
      /^captionFoldCallSites=2$/,
      "expected BOTH TableFigureBlock and GraphicFigureBlock to hand their figure's caption/citation to the shared fold",
    );
    const captionFoldBody = sliceBetween(page, "function CaptionFold(props", "function SourceCaptionLine(props");
    pin.match(captionFoldBody, /\S/, "expected to find CaptionFold's own source slice (marker moved?)");
    pin.match(captionFoldBody, /<details/, "expected CaptionFold's own body to be a <details> fold");
    pin.match(
      captionFoldBody,
      /<SourceCaptionLine caption=\{caption\} citation=\{citation\} t=\{t\} \/>/,
      "expected CaptionFold to still render SourceCaptionLine INSIDE the fold -- its own verbatim pins (R-5b, elsewhere in this suite) are untouched",
    );
  },
);

contractTest(
  "#60b: the profile-upload door posts to R-4's own route, refreshes and selects on success, and refusals render verbatim",
  (pin) => {
    const page = read("app/report/page.tsx");
    const source = withoutComments(page);
    const api = read("lib/api.ts");

    // THE ROUTE. `uploadReportStyleProfile` is the only new mutation this
    // unit wires, and it must hit R-4's own POST /style-profiles -- a
    // different path is a different, unreviewed backend surface.
    const uploader = withoutComments(
      sliceBetween(api, "export async function uploadReportStyleProfile(", "\nexport "),
    );
    pin.match(uploader, /\S/, "expected to find uploadReportStyleProfile's own source slice (marker moved?)");
    pin.match(
      uploader,
      /apiMutationFetch\(`\/api\/report\/style-profiles\?\$\{params\.toString\(\)\}`, \{/,
      "expected the upload to POST R-4's own /api/report/style-profiles route",
    );
    pin.match(uploader, /method: "POST",/, "expected the upload to be a POST, matching the route's own method");
    pin.match(
      uploader,
      /body: file,/,
      "expected the raw FILE BYTES to be the request body -- this backend carries no multipart/form-data (routers/report.py's own docstring)",
    );

    // THE CALL SITE hands the picked File straight to the uploader.
    pin.match(
      source,
      /const record = await uploadReportStyleProfile\(file\);/,
      "expected the upload handler to call uploadReportStyleProfile with the picked File, unmodified",
    );

    // SUCCESS REFRESHES AND SELECTS. A locally-patched array could drift
    // from what the backend actually now holds, so the profile list is
    // RE-FETCHED from the server, and the new profile is SELECTED so the
    // very next compose already offers it. Adjacency-anchored (the same
    // idiom the compose-receipt pin above uses) so a mutation that drops
    // either call, or reorders select-before-refresh, fails here.
    pin.match(
      source,
      /const refreshed = await fetchReportStyleProfiles\(\);\s*setProfilesLoaded\(ok\("profiles", refreshed\)\);\s*setStyleProfileId\(record\.id\);/,
      "expected a successful upload to REFETCH the profile list from the server and then SELECT the new profile's id, in that order",
    );

    // THE REFUSAL IS THE SERVER'S OWN SENTENCE, verbatim -- same discipline
    // as every other refusal on this screen (closing-boundary anchored, the
    // #55b round-3 idiom, so an appended .slice cannot hide inside it).
    pin.match(
      source,
      /\{pick\(uploadRefusal\.message, lang\)\}<\/p>/,
      "expected an upload refusal (bomb-guard, not-a-docx) to render the server's own sentence WHOLE",
    );

    // THE THREE STATES: loading (no affordance either way -- a control that
    // might 401 the instant it renders is not a control), signed out (a
    // NAMED line, never a dead file input), signed in (the real control).
    // `useAuth()` is asked BEFORE the door renders, never after a doomed
    // upload attempt -- the same order app/admin/page.tsx already uses.
    pin.match(
      source,
      /authStatus === "loading" \? null : authStatus === "anonymous" \? \(/,
      "expected the upload door to branch on useAuth()'s own three states, loading checked first",
    );
    pin.match(
      source,
      /<Link href=\{loginHref\(pathname\)\} className="font-semibold underline">/,
      "expected the signed-out state to offer an actual sign-in link (the NetworkDiffView idiom), not just a sentence",
    );
    pin.match(
      source,
      /<input\s+type="file"\s+accept="\.docx"/,
      "expected the signed-in state to render the real file input, restricted to .docx",
    );

    // THE NOT_EXTRACTED LIST renders as short one-liners (the C1-b grammar:
    // extraction is a PROPOSAL, not a measurement) -- reusing the exact
    // "추출 불가" wording the NOT_AVAILABLE outputs already use elsewhere on
    // this screen, never a client-invented sentence.
    pin.match(
      source,
      /\{\(uploadedProfile\.original_profile\.not_extracted \?\? \[\]\)\.map\(\(item\) => \(/,
      "expected the upload receipt to render EVERY not_extracted entry the server returned",
    );
    pin.match(
      source,
      /\{t\("추출 불가", "not extractable", "抽出不可"\)\} — \{item\.field\}: \{pick\(item\.reason, lang\)\}/,
      "expected each not_extracted entry to name its field and the server's own reason verbatim",
    );
  },
);

contractTest("#60b: the Markdown text-preview builder is verbatim and invents nothing it was not handed", (pin) => {
  const lib = read("lib/reportMarkdownPreview.ts");
  const source = withoutComments(lib);

  // TRUNCATION IS BANNED, FILE-WIDE. Every reason/basis/citation sentence is
  // spliced through by reference; MEASURED (the #55b idiom): a `.slice(0, N)`
  // anywhere in this file is exactly how "verbatim" quietly becomes "the
  // first N characters".
  pin.doesNotMatch(
    source,
    /\.slice\(|\.substring\(|\.substr\(/,
    "expected NO truncation method anywhere in the Markdown builder -- a server sentence spliced through this file must arrive whole",
  );

  // THE ONE STRING TRANSFORM this file performs (mdCell's pipe-escape, so a
  // literal `|` in a TABLE CELL cannot masquerade as a column separator) is
  // confined to `mdCell` and nowhere else -- a second `.replace()` anywhere
  // outside it is how a reason/basis/citation sentence could quietly be
  // rewritten instead of spliced through.
  const mdCellBody = sliceBetween(source, "function mdCell(value: unknown): string {", "function sourceCaptionLine(");
  pin.match(mdCellBody, /\S/, "expected to find mdCell's own source slice (marker moved?)");
  pin.match(mdCellBody, /\.replace\(/, "expected mdCell itself to carry the file's one .replace() call");
  const outsideMdCell = source.replace(mdCellBody, "");
  pin.doesNotMatch(
    outsideMdCell,
    /\.replace\(/,
    "expected NO .replace() anywhere OUTSIDE mdCell -- a reason/basis/citation sentence must never be rewritten, only spliced through",
  );

  // NO NEW FETCH. This module is a pure projection of data the CALLER
  // already fetched (its own module comment states this); a request here
  // would be a second, unpinned source of report content.
  pin.doesNotMatch(
    source,
    /\bfetch\(|apiFetch\(|apiMutationFetch\(/,
    "expected the Markdown builder to make no network request of its own",
  );

  // EVERY reason/title/column/legend field this file reads is a `ReportText`
  // and MUST go through `pick(...)` -- never spliced in directly, which
  // would silently ignore `lang` and always print one language.
  pin.match(source, /pick\(item\.figure\.reason, lang\)/, "expected an absent figure's reason to be picked by language");
  pin.match(source, /pick\(section\.title, lang\)/, "expected a section heading to be picked by language");
  pin.match(source, /pick\(item\.figure\.title, lang\)/, "expected a figure's own title to be picked by language");
  pin.match(source, /pick\(column, lang\)/, "expected a table column heading to be picked by language");
  pin.match(source, /pick\(entry, lang\)/, "expected a legend entry to be picked by language");

  // NEW STRINGS, EXACT TRIPLES. The class-level ban first (a blank/missing
  // `ja` argument is the #50-d/#57/#58 class), then each string this file
  // adds beyond the ones it deliberately reuses verbatim from the page
  // (`출처 정보 없음`, `선택에서 제외됨…`, `이 절의 증거 없음`, `추출 불가`
  // -- already pinned on app/report/page.tsx above, and reused rather than
  // re-declared here for the same reason `unboundTitle` is passed in rather
  // than re-translated).
  pin.doesNotMatch(
    source,
    /\bt\((["`])(?:[^"`\\]|\\.)*\1\s*,\s*(["`])(?:[^"`\\]|\\.)*\2\s*\)/,
    "every t() in the Markdown builder must carry a ja argument -- a two-argument call is how a language quietly drops out",
  );
  pin.doesNotMatch(source, /,\s*""\s*\)/, "no t() argument in the Markdown builder may be the empty string");

  const NEW_TRIPLES: [string, string, string][] = [
    ["보고서 텍스트 미리보기", "Report text preview", "レポートのテキストプレビュー"],
    [
      "이 미리보기는 이 화면이 이미 읽어 온 데이터로만 만든 텍스트 사본입니다 — 서식이나 도판 이미지는 담지 않습니다.",
      "This preview is a text copy built only from data this screen already fetched — it carries no styling and no figure images.",
      "このプレビューはこの画面が既に取得したデータのみで作ったテキスト写しです — 書式や図版画像は含みません。",
    ],
    [
      "그래픽 원본(SVG)은 이 텍스트 미리보기에 담기지 않습니다 — 내려받은 문서 또는 도판 zip에서 확인하세요.",
      "The graphic original (SVG) is not carried in this text preview — see the downloaded document or the figure zip.",
      "グラフィック原本（SVG）はこのテキストプレビューには含まれません — ダウンロードした文書または図版zipで確認してください。",
    ],
    ["한계 목록", "Limitations", "制限一覧"],
    ["명명된 한계 없음", "No named limitation", "名前付き制限なし"],
  ];
  for (const [ko, en, ja] of NEW_TRIPLES) {
    pin.match(lib, tripleArgs(ko, en, ja), `expected the Markdown builder's exact ko/en/ja triple for ${ko}`);
  }
});

test("#60b: the Markdown text-preview builder passes server sentences through byte-exact, and fabricates no row", async () => {
  const { buildMarkdownPreview } = await import("../lib/reportMarkdownPreview.ts");

  const t = (_ko: string, en: string) => en;

  // Distinctive, ugly-on-purpose markers (pipes, asterisks, an em dash) so a
  // truncation, an escape, or a dropped character is easy to catch, and
  // globally unique so `includes()` cannot accidentally match unrelated text.
  const sectionA = {
    id: "sec_a",
    title: { ko: "가", en: "Section A", ja: "セクションA" },
    basis: ["BASIS QUOTE WITH | PIPE AND *STAR* CHARACTERS — VERBATIM MARKER Z9Q7"],
    source_keys: [],
    required_outputs: [
      {
        type: "GroupX",
        evaluation_method: null,
        time_period: null,
        outputs: [
          {
            name: "Output One",
            availability: "NOT_AVAILABLE" as const,
            extractor: null,
            reason: "REASON WITH | PIPE VERBATIM MARKER K3M1",
          },
          { name: "Output Two (available)", availability: "AVAILABLE" as const, extractor: null, reason: null },
        ],
      },
    ],
    regulation_derived: true,
  };
  const sectionExcluded = {
    id: "sec_b",
    title: { ko: "나", en: "Section B (excluded)", ja: "セクションB" },
    basis: ["THIS QUOTE MUST NEVER APPEAR -- EXCLUDED_SENTINEL_X1"],
    source_keys: [],
    required_outputs: [],
    regulation_derived: true,
  };
  const tableFigure = {
    figure_id: "fig_table_1",
    spine_section_id: "sec_a",
    absent: false,
    title: { ko: "표", en: "Table Figure", ja: "表" },
    columns: [
      { key: "a", ko: "가", en: "Col A", ja: "列A" },
      { key: "b", ko: "나", en: "Col B", ja: "列B" },
    ],
    rows: [
      { a: "r1c1", b: "r1c2" },
      { a: "r2c1", b: "r2c2" },
      { a: "r3c1", b: "pipe|value" },
    ],
    source_caption: { run_id: "RUN_VERBATIM_777" },
    citation: "CITATION VERBATIM 42",
  };
  const absentTableFigure = {
    figure_id: "fig_table_absent",
    spine_section_id: "sec_a",
    absent: true,
    reason: { ko: "가", en: "ABSENT REASON VERBATIM | PIPE Q8W4", ja: "な" },
  };
  const excludedSectionFigure = {
    figure_id: "fig_excluded_only",
    spine_section_id: "sec_b",
    absent: false,
    title: { ko: "제외", en: "Excluded-section Table SHOULD_NOT_APPEAR_MARKER_77", ja: "な" },
    columns: [{ key: "x", ko: "가", en: "X", ja: "な" }],
    rows: [{ x: "hidden" }],
  };
  const graphicFigure = {
    figure_id: "fig_graphic_1",
    spine_section_id: null,
    absent: false,
    title: { ko: "그림", en: "Graphic Figure", ja: "図" },
    svg: "<svg>SHOULD NOT APPEAR IN MD SVG_SENTINEL_9</svg>",
    legend: [{ key: "l1", ko: "가", en: "Legend One", ja: "な", fill: "#fff", stroke: "#000" }],
    source_caption: {},
    citation: null,
  };

  const md = buildMarkdownPreview({
    lang: "en",
    t,
    sections: [sectionA, sectionExcluded],
    tableFigures: [tableFigure, absentTableFigure, excludedSectionFigure],
    graphicFigures: [graphicFigure],
    unboundTitle: "Unbound figures",
    excludedSectionIds: ["sec_b"],
  });

  // VERBATIM PASSTHROUGH -- every server sentence survives byte-exact,
  // pipes and all.
  assert.ok(md.includes("BASIS QUOTE WITH | PIPE AND *STAR* CHARACTERS — VERBATIM MARKER Z9Q7"), "basis quote must survive verbatim");
  assert.ok(md.includes("REASON WITH | PIPE VERBATIM MARKER K3M1"), "a NOT_AVAILABLE reason must survive verbatim");
  assert.ok(md.includes("ABSENT REASON VERBATIM | PIPE Q8W4"), "an absent figure's reason must survive verbatim");
  assert.ok(md.includes("CITATION VERBATIM 42"), "a citation must survive verbatim");
  assert.ok(md.includes("RUN_VERBATIM_777"), "a source-caption value must survive verbatim");
  assert.ok(md.includes("Legend One"), "a legend entry must survive verbatim");

  // NO FABRICATION -- an excluded section contributes NOTHING, not even the
  // figure bound to it, and an AVAILABLE output is never listed as a gap.
  assert.ok(!md.includes("EXCLUDED_SENTINEL_X1"), "an excluded section's basis quote must never render");
  assert.ok(!md.includes("SHOULD_NOT_APPEAR_MARKER_77"), "a figure bound to an excluded section must never render");
  assert.ok(!md.includes("Output Two"), "an AVAILABLE output must never be listed among the gaps");
  assert.ok(md.includes("Excluded by selection"), "the excluded section must state its own excluded line");

  // NO IMAGE -- the graphic's raw SVG markup never enters the text preview.
  assert.ok(!md.includes("SVG_SENTINEL_9") && !md.includes("<svg"), "the graphic's raw SVG must never appear in the text preview");
  assert.ok(md.includes("The graphic original (SVG) is not carried"), "the graphic figure must explain its own absence from the text preview");

  // ROW FIDELITY -- exactly the caller's three rows, one Markdown row each,
  // and the pipe-bearing cell is ESCAPED (not dropped, not left unescaped).
  const tableStart = md.indexOf("| Col A | Col B |");
  assert.ok(tableStart >= 0, "expected the table figure's own header row");
  const bodyLines = md
    .slice(tableStart)
    .split("\n")
    .filter((line) => line.startsWith("| r"));
  assert.equal(bodyLines.length, 3, "expected exactly the caller's three rows, no more and no fewer");
  assert.ok(bodyLines.some((line) => line.includes("pipe\\|value")), "expected the pipe-bearing cell value to be ESCAPED, not dropped");

  // LIMITATIONS AT THE END -- both gap classes are aggregated, byte-exact.
  const limitationsAt = md.indexOf("## Limitations");
  assert.ok(limitationsAt >= 0, "expected a final Limitations section");
  const limitationsBody = md.slice(limitationsAt);
  assert.ok(limitationsBody.includes("REASON WITH | PIPE VERBATIM MARKER K3M1"), "a NOT_AVAILABLE reason must reach the Limitations section");
  assert.ok(limitationsBody.includes("ABSENT REASON VERBATIM | PIPE Q8W4"), "an absent figure's reason must reach the Limitations section");

  // UNBOUND FIGURES get their own heading, in the caller's own label.
  assert.ok(md.includes("## Unbound figures"), "expected the caller's own unbound-figures title, verbatim");

  // EMPTY INPUT -- no section, no figure -- still names its own limitations
  // state rather than rendering nothing or throwing.
  const empty = buildMarkdownPreview({
    lang: "en",
    t,
    sections: [],
    tableFigures: [],
    graphicFigures: [],
    unboundTitle: "Unbound figures",
    excludedSectionIds: [],
  });
  assert.ok(empty.includes("No named limitation"), "expected the empty-input case to name its own empty limitations list");
});

// ---------------------------------------------------------------------------
// Unit #64 (second live-test round, 2026-08-26): the /report text-preview
// pane RENDERS Markdown structure instead of showing the raw string, and the
// spine tree's per-figure absence one-liners fold behind a count chip.
//
// User's own words: "for the md file, can u not show the raw md file but in
// preview mode?" and (same session) "still too many fine text shown in the
// report page" -- the spine card's per-figure absence sentences specifically.
// ---------------------------------------------------------------------------

contractTest("#64: the Markdown structure parser is pure, and recognises only what buildMarkdownPreview actually emits", (pin) => {
  const lib = read("lib/reportMarkdownBlocks.ts");
  const source = withoutComments(lib);

  // NO NEW FETCH, NO REACT -- same discipline reportMarkdownPreview.ts is
  // pinned on: this module reads a string and returns data, nothing else. A
  // React import or a hook would mean this "pure function" secretly carries
  // state or a component identity, which the sibling presentational
  // component (components/report/MarkdownStructurePreview.tsx) is supposed
  // to own instead.
  pin.doesNotMatch(
    source,
    /\bfetch\(|apiFetch\(|apiMutationFetch\(/,
    "expected the Markdown structure parser to make no network request of its own",
  );
  pin.doesNotMatch(
    source,
    /from ["']react["']|"use client"|useState\(|useEffect\(|useMemo\(/,
    "expected no React import and no hook in the pure parser -- state and JSX belong to the sibling component, not this module",
  );

  // THE SLICE BAN, RE-DECIDED (this file's own module comment explains why):
  // every split in this file uses .replace()/.split(), never .slice()/
  // .substring()/.substr() picking a byte range that could stop short.
  pin.doesNotMatch(
    source,
    /\.slice\(|\.substring\(|\.substr\(/,
    "expected NO truncation method anywhere in the Markdown structure parser -- splitting a line into blocks/segments must never risk dropping its tail",
  );

  // KNOWN CONSTRUCTS ONLY -- exactly the two heading levels
  // buildMarkdownPreview is measured to emit (`# ` and `## `), not a general
  // `#{1,6}` heading rule. Recognising a level this app never writes would be
  // inventing support for a construct nothing on this screen produces.
  pin.match(source, /\/\^## \(\.\*\)\$\//, "expected the level-2 heading regex to match exactly '## ' -- no more, no fewer hashes");
  pin.match(source, /\/\^# \(\.\*\)\$\//, "expected the level-1 heading regex to match exactly '# '");
  pin.doesNotMatch(
    source,
    /#\{1,6\}|#\{2,6\}|#\+/,
    "expected no generic multi-level heading regex -- only the two levels this app's own generator writes are 'known'",
  );

  // THE HONEST FALLBACK, STRUCTURALLY: the LAST arm of the parse loop pushes
  // a paragraph block for the current line and returns straight to the loop
  // (`return blocks;` sits immediately after, past only the loop's own
  // closing brace) -- so no earlier branch's `continue` can skip it, and
  // nothing wraps it in a condition that could suppress it for some lines.
  pin.match(
    source,
    /blocks\.push\(\{ type: "paragraph", segments: parseInline\(line\) \}\);\s*i \+= 1;\s*\}\s*return blocks;/,
    "expected the parser's fallback arm to unconditionally push a paragraph block as the LAST statement in the loop, immediately before the function returns -- an unrecognised line must always reach this, never be silently skipped",
  );
});

test("#64: known Markdown constructs parse to the right block types, an unknown line survives as text, and no cell is ever truncated", async () => {
  const { parseMarkdownBlocks } = await import("../lib/reportMarkdownBlocks.ts");

  // EVERY KNOWN CONSTRUCT, one block each.
  const headings = parseMarkdownBlocks("# Title\n\n## Section");
  assert.deepEqual(
    headings.map((b) => (b.type === "heading" ? [b.type, b.level] : b.type)),
    [
      ["heading", 1],
      ["heading", 2],
    ],
    "expected '# '/'## ' to parse as heading blocks at levels 1 and 2",
  );

  const quote = parseMarkdownBlocks("> line one\n> line two");
  assert.equal(quote.length, 1, "expected consecutive '> ' lines to merge into ONE blockquote block");
  assert.equal(quote[0].type, "blockquote");
  if (quote[0].type === "blockquote") {
    assert.deepEqual(
      quote[0].lines.map((segments) => segments.map((s) => s.text).join("")),
      ["line one", "line two"],
      "expected both quote lines to survive, in order",
    );
  }

  const list = parseMarkdownBlocks("- alpha\n- beta\n- gamma");
  assert.equal(list.length, 1, "expected consecutive '- ' lines to merge into ONE list block");
  assert.equal(list[0].type, "list");
  if (list[0].type === "list") {
    assert.equal(list[0].items.length, 3, "expected all three list items, none dropped");
  }

  const table = parseMarkdownBlocks("| Col A | Col B |\n| --- | --- |\n| r1c1 | r1c2 |\n| r2c1 | r2c2 |");
  assert.equal(table.length, 1, "expected a header+separator+rows pipe table to parse as ONE table block");
  assert.equal(table[0].type, "table");
  if (table[0].type === "table") {
    assert.deepEqual(table[0].columns, ["Col A", "Col B"], "expected the table's own header cells as columns");
    assert.deepEqual(
      table[0].rows,
      [
        ["r1c1", "r1c2"],
        ["r2c1", "r2c2"],
      ],
      "expected exactly the two body rows, no more and no fewer",
    );
  }

  const bold = parseMarkdownBlocks("**fig_1** — some reason");
  assert.equal(bold.length, 1);
  assert.equal(bold[0].type, "paragraph");
  if (bold[0].type === "paragraph") {
    assert.deepEqual(
      bold[0].segments,
      [
        { text: "fig_1", bold: true },
        { text: " — some reason", bold: false },
      ],
      "expected '**fig_1**' to split into a bold segment and a plain-text tail, both surviving whole",
    );
  }

  // THE HONEST FALLBACK -- a line matching no known construct (here, a
  // heading LEVEL this app's own generator never writes) survives as a
  // plain-text paragraph, verbatim, rather than being dropped or throwing.
  const unknown = parseMarkdownBlocks("### deeper than this app writes\nHOSTILE <script>alert(1)</script> LINE");
  assert.equal(unknown.length, 2, "expected both unrecognised lines to survive as their own blocks");
  for (const block of unknown) assert.equal(block.type, "paragraph", "expected the honest fallback to be a paragraph block");
  assert.equal(
    unknown[0].type === "paragraph" && unknown[0].segments.map((s) => s.text).join(""),
    "### deeper than this app writes",
    "expected the unrecognised heading-like line to survive character-for-character",
  );
  assert.equal(
    unknown[1].type === "paragraph" && unknown[1].segments.map((s) => s.text).join(""),
    "HOSTILE <script>alert(1)</script> LINE",
    "expected a hostile-looking line to survive as inert TEXT, character-for-character -- this parser returns data, never markup",
  );

  // NO TRUNCATION -- a long table cell (well past any of this app's line-
  // length habits) survives whole.
  const longCell = "X".repeat(500) + " VERBATIM TAIL MARKER 8Q3";
  const wideTable = parseMarkdownBlocks(`| Col |\n| --- |\n| ${longCell} |`);
  assert.equal(wideTable[0].type, "table");
  if (wideTable[0].type === "table") {
    assert.equal(wideTable[0].rows[0][0], longCell, "expected a long cell value to survive whole, not truncated");
  }

  // THE ESCAPE INTEGRATION: buildMarkdownPreview's mdCell escapes a literal
  // '|' inside a cell as '\|' so it cannot masquerade as a column separator;
  // this parser un-escapes it back to '|' for display. Built through the
  // REAL producer, not a hand-written fixture, so the two modules are proven
  // to agree rather than merely individually self-consistent.
  const { buildMarkdownPreview } = await import("../lib/reportMarkdownPreview.ts");
  const md = buildMarkdownPreview({
    lang: "en",
    t: (_ko: string, en: string) => en,
    sections: [
      {
        id: "sec",
        title: { ko: "가", en: "Section", ja: "な" },
        basis: [],
        source_keys: [],
        required_outputs: [],
        regulation_derived: true,
      },
    ],
    tableFigures: [
      {
        figure_id: "fig",
        spine_section_id: "sec",
        absent: false,
        title: { ko: "가", en: "Table", ja: "な" },
        columns: [{ key: "a", ko: "가", en: "A", ja: "な" }],
        rows: [{ a: "pipe|value" }],
      },
    ],
    graphicFigures: [],
    unboundTitle: "Unbound",
    excludedSectionIds: [],
  });
  const blocks = parseMarkdownBlocks(md);
  const producedTable = blocks.find((b) => b.type === "table");
  assert.ok(producedTable, "expected the real builder's output to still contain a parseable table block");
  if (producedTable && producedTable.type === "table") {
    assert.equal(producedTable.rows[0][0], "pipe|value", "expected the escaped pipe to round-trip back to a literal '|' for display");
  }
});

contractTest(
  "#64: the spine card's evidence chips and their fold count the SAME arrays, one fold holds every sentence, and none is dropped",
  (pin) => {
    const page = read("app/report/page.tsx");
    const source = withoutComments(page);

    const evidenceBody = sliceBetween(page, "function EvidenceStateList(props", "function EvidenceChip(props");
    pin.match(evidenceBody, /\S/, "expected to find EvidenceStateList's own source slice (marker moved?)");

    // ONE FOLD, NOT ONE PER SENTENCE. The card face carries only the two
    // count chips; every one of this section's per-figure one-liners --
    // MEASURED at 50-110 characters each for an absent figure, unit #60b --
    // moves behind this ONE toggle.
    pin.match(
      `evidenceDetailsCount=${(withoutComments(evidenceBody).match(/<details\b/g) ?? []).length}`,
      /^evidenceDetailsCount=1$/,
      "expected exactly ONE 자세히 fold inside the evidence card -- the per-figure one-liners must not each carry their own toggle",
    );

    // ONE SOURCE, NO FORK: a chip's count and the fold's row count both read
    // `presentFigures`/`absentFigures` -- the SAME filtered arrays -- so a
    // mutation that recomputes one of them separately (and could drift from
    // the other) fails here.
    for (const kind of ["presentFigures", "absentFigures"]) {
      pin.match(
        evidenceBody,
        new RegExp(`\\$\\{${kind}\\.length\\}건`),
        `expected the ${kind} chip's label to read ${kind}.length directly`,
      );
      pin.match(
        evidenceBody,
        new RegExp(`${kind}\\.map\\(\\(item\\) =>`),
        `expected the fold to iterate ${kind} itself, not a second array recomputed from it`,
      );
    }
    pin.match(
      `evidenceArrayDeclarations=${(source.match(/const (?:presentFigures|absentFigures) = figures\.filter\(/g) ?? []).length}`,
      /^evidenceArrayDeclarations=2$/,
      "expected exactly one declaration each of presentFigures/absentFigures on the whole screen -- a second filter is a second, possibly-diverging count",
    );

    // REACHABLE: the fold still carries the SERVER's own absence reason,
    // verbatim -- the exact expression the card face printed directly before
    // this unit, only its wrapper moved.
    pin.match(
      evidenceBody,
      /\{pick\(item\.figure\.reason, lang\)\}/,
      "expected the fold to still render an absent figure's own reason verbatim -- moved behind the fold, never deleted",
    );
    // ...and the figure id each reason belongs to, so a folded sentence is
    // still attributable once opened, not an anonymous list.
    pin.match(
      evidenceBody,
      /\{item\.figure\.figure_id\} — \{pick\(item\.figure\.reason, lang\)\}/,
      "expected each folded absence line to keep naming ITS OWN figure_id, not just the sentence",
    );
  },
);

// ---------------------------------------------------------------------------
// #64 REFUTER FIX ROUND. Three real defects, all measured against the code
// landed above:
//
//  1. CRLF heading bug (dormant but unconditional): `parseMarkdownBlocks`
//     split on a bare '\n', so a CRLF-authored line ('...\r\n') left a '\r'
//     glued to its own end. Every `$'-anchored heading regex (`.` cannot
//     match '\r') then failed on a CRLF heading, and the line fell through to
//     the honest-fallback paragraph -- LEAKING the literal '# Title' Markdown
//     syntax to the reader instead of rendering a heading. The blockquote/
//     list prefix tests (`/^>\s?/`, `/^-\s+/`, no trailing `$`) tolerated the
//     same bug silently instead of failing loudly, which is exactly the
//     asymmetry that let it stay dormant. Fixed by splitting on `\r\n|\n`
//     ONCE, at the top of the function, so every construct is CRLF-safe by
//     construction rather than each regex needing its own carve-out.
//  2. The .md download's byte-identical-regardless-of-view claim (this
//     unit's own module comment, and the report-back's "download stays
//     byte-identical") had NO pin -- a mutation serializing the PARSED
//     structure instead of the raw string passed the whole suite clean.
//  3. The pane's default view (structure, not raw -- the whole point of this
//     unit) had no pin either -- flipping `mdRawView`'s initial state to
//     `true` also passed clean.
// ---------------------------------------------------------------------------

test("#64 refuter fix: a CRLF-terminated heading parses as a heading, and no construct carries a glued \\r", async () => {
  const { parseMarkdownBlocks } = await import("../lib/reportMarkdownBlocks.ts");

  // THE DEFECT ITSELF: a CRLF heading must still parse as a heading, not
  // fall through to the paragraph fallback with the literal '#'/'##' syntax
  // leaking into the reader's text.
  const crlfHeadings = parseMarkdownBlocks("# Title\r\n\r\n## Section\r\n");
  assert.equal(crlfHeadings.length, 2, "expected both CRLF heading lines to parse as their own blocks");
  assert.equal(crlfHeadings[0].type, "heading", "expected a CRLF-terminated '# ' line to parse as a heading, not fall through to a paragraph");
  if (crlfHeadings[0].type === "heading") {
    assert.equal(crlfHeadings[0].level, 1);
    assert.equal(
      crlfHeadings[0].segments.map((s) => s.text).join(""),
      "Title",
      "expected no glued '\\r' in the heading's own text",
    );
  }
  assert.equal(crlfHeadings[1].type, "heading", "expected a CRLF-terminated '## ' line to parse as a level-2 heading");
  if (crlfHeadings[1].type === "heading") {
    assert.equal(crlfHeadings[1].level, 2);
    assert.equal(crlfHeadings[1].segments.map((s) => s.text).join(""), "Section");
  }

  // THE SAME CHECK across every OTHER construct -- the prefix tests
  // (blockquote '>', list '-') tolerated a trailing '\r' in their own
  // start-anchored regex even under the ORIGINAL bug, which is the
  // asymmetry that let this stay dormant; proving the FIX holds for them
  // too (not only headings) is what closes the whole class, not just the
  // one symptom that was visibly broken.
  const crlfQuote = parseMarkdownBlocks("> line one\r\n> line two\r\n");
  assert.equal(crlfQuote[0].type, "blockquote");
  if (crlfQuote[0].type === "blockquote") {
    for (const lineSegments of crlfQuote[0].lines) {
      assert.ok(
        !lineSegments.some((s) => s.text.includes("\r")),
        "expected no blockquote line to carry a glued '\\r'",
      );
    }
  }

  const crlfList = parseMarkdownBlocks("- alpha\r\n- beta\r\n");
  assert.equal(crlfList[0].type, "list");
  if (crlfList[0].type === "list") {
    for (const itemSegments of crlfList[0].items) {
      assert.ok(!itemSegments.some((s) => s.text.includes("\r")), "expected no list item to carry a glued '\\r'");
    }
  }

  const crlfTable = parseMarkdownBlocks("| Col A |\r\n| --- |\r\n| val |\r\n");
  assert.equal(crlfTable[0].type, "table");
  if (crlfTable[0].type === "table") {
    assert.equal(crlfTable[0].columns[0], "Col A", "expected no glued '\\r' in a CRLF table's header cell");
    assert.equal(crlfTable[0].rows[0][0], "val", "expected no glued '\\r' in a CRLF table's body cell");
  }

  const crlfParagraph = parseMarkdownBlocks("plain fallback text\r\n");
  assert.equal(crlfParagraph[0].type, "paragraph");
  if (crlfParagraph[0].type === "paragraph") {
    assert.equal(
      crlfParagraph[0].segments.map((s) => s.text).join(""),
      "plain fallback text",
      "expected no glued '\\r' in a CRLF paragraph's own fallback text",
    );
  }
});

contractTest(
  "#64 refuter fix: the parser splits CRLF-safe at its source, the .md download stays raw and view-independent, and the default view is structure",
  (pin) => {
    const lib = withoutComments(read("lib/reportMarkdownBlocks.ts"));

    // FINDING 1, AT THE SOURCE: the split itself, not just its behaviour --
    // so a regression back to a bare '\n' split fails here even before the
    // behavioural test above would catch it on a specific input.
    pin.match(
      lib,
      /const lines = markdown\.split\(\/\\r\\n\|\\n\/\);/,
      "expected the parser to split on CRLF-or-LF, not a bare '\\n' -- a bare split leaves a '\\r' glued to every CRLF line, which every '$'-anchored regex below then fails to match",
    );
    pin.doesNotMatch(
      lib,
      /markdown\.split\("\\n"\)|markdown\.split\('\\n'\)/,
      "expected no bare '\\n'-only split anywhere in the parser -- that is exactly the CRLF defect this fix closes",
    );

    const page = read("app/report/page.tsx");
    const source = withoutComments(page);

    // FINDING 3: the raw/structure toggle's OWN initial value, anchored to
    // BOTH its own name and its setter's -- a bare `useState(false)` search
    // would also match unrelated state on this screen, so a mutation that
    // renamed the pair while flipping the value could still satisfy a
    // looser pin. This one cannot be satisfied by any OTHER declaration.
    pin.match(
      source,
      /const \[mdRawView, setMdRawView\] = useState\(false\);/,
      "expected the MD preview pane to default to STRUCTURE view (mdRawView = false) -- this is the unit's own motivating fix ('can u not show the raw md file but in preview mode?'); defaulting to raw silently reverts it",
    );

    // FINDING 2: the .md download ships `markdownPreview` RAW, byte-
    // identical, unconditionally -- independent of whichever view (structure
    // or raw) happens to be on screen when the user clicks download.
    const downloader = sliceBetween(page, "const downloadMarkdownPreview = useCallback(", "}, [markdownPreview]);");
    pin.match(downloader, /\S/, "expected to find downloadMarkdownPreview's own source slice (marker moved?)");
    pin.match(
      downloader,
      /downloadBlob\(new Blob\(\[markdownPreview\], \{ type: "text\/markdown;charset=utf-8" \}\), "report-preview\.md"\);/,
      "expected the .md download to ship the raw markdownPreview string, byte-identical, regardless of which view is on screen",
    );
    pin.doesNotMatch(
      downloader,
      /mdRawView|parseMarkdownBlocks|MarkdownStructurePreview/,
      "expected the download to be structurally INDEPENDENT of the raw/structure toggle -- it must never branch on mdRawView or reach for the parsed structure instead of the raw string",
    );
  },
);

contractTest("unit #65: the calibration banner and run picker name an empty run list honestly", (pin) => {
  const page = read("app/calibration/page.tsx");
  const surface = withoutComments(page);

  // THE GATE, structural: "zero runs" is decided ONLY once the fetch resolved
  // WITHOUT error -- loading and a failed fetch are not evidence of absence,
  // so both a mutation that drops the loading guard and one that drops the
  // error guard (inlining `scenarios.length === 0` alone) fail here.
  pin.match(
    surface,
    /const scenariosEmpty = !scenariosLoading && !scenariosError && scenarios\.length === 0;/,
    "expected 'zero runs' to require a RESOLVED, error-free fetch -- loading and a failed fetch must not read as absence",
  );

  // Unit #67 fix (D1): sliced from `surface` (comment-stripped), not raw
  // `page`. The style pins below this point assert real CSS classes appear
  // (or don't) in this branch's JSX -- but #67's own doc comment on the CTA
  // branch NAMES those exact classes in prose ("bg-accent/hover:bg-accent-
  // deep/text-white"), so a slice of raw `page` let the comment satisfy the
  // `pin.match`es on its own, with the actual `className` never inspected
  // (measured: removing the CTA classes entirely, or reverting the whole
  // branch to its pre-#67 bytes, left all four pins green either way).
  const banner = sliceBetween(surface, "{!scenarioId ? (", ") : nextStep ? (");
  pin.match(banner, /\S/, "expected to find the #65 banner's own source slice (markers moved?)");

  // FOUR STATES, not three (orchestrator correction, 2026-08-26): a fetch that
  // FAILED is its own absence, distinct from "still loading" and from "resolved
  // empty" -- the original two-branch banner let a failed fetch fall through to
  // the same neutral "choose a run" prompt an empty <select> could never
  // satisfy, which was the exact dead-end this unit exists to close, just moved
  // one branch over. `errorBranchStart` anchors the search so the later
  // `scenariosEmpty ? (` found is the one AFTER the error check, not some future
  // unrelated occurrence.
  const errorBranchStart = banner.indexOf("scenariosError ? (");
  const zeroBranchStart = banner.indexOf("scenariosEmpty ? (", errorBranchStart);
  const zeroBranchEnd = banner.indexOf(") : (", zeroBranchStart);
  // RE-PINNED as a computed-value pin (unit #67 fix round 2, mirrors #18's D2):
  // the previous plain `assert.ok` here is exactly the trap CLAUDE.md names --
  // a bare assert throws on the first failure and hides every pin after it in
  // this same test, including all four CTA pins and the D5 refusal pin below
  // (refuter M6, measured: swapping the branch condition order left only ONE
  // failing message visible instead of the whole set). `inOrder` requires all
  // three marker indices to be non-negative AND strictly increasing -- the
  // exact same boolean this assert guarded, unweakened -- so a moved or
  // reordered marker shows up as `inOrder=false` in the pin's own output
  // instead of aborting the test.
  const inOrder =
    errorBranchStart > -1 && zeroBranchStart > errorBranchStart && zeroBranchEnd > zeroBranchStart;
  pin.match(
    `errorBranchStart=${errorBranchStart} zeroBranchStart=${zeroBranchStart} zeroBranchEnd=${zeroBranchEnd} inOrder=${inOrder}`,
    /inOrder=true$/,
    "expected the banner's error/zero-runs/neutral ternary chain to be found, in that order (markers moved?)",
  );
  const errorBranch = banner.slice(errorBranchStart, zeroBranchStart);
  const zeroBranch = banner.slice(zeroBranchStart, zeroBranchEnd);
  const neutralBranch = banner.slice(zeroBranchEnd);

  // (a) THE ZERO-STATE SENTENCE -- states the absence AND where to fix it --
  // byte-exact in all three languages, and present ONLY in the branch that
  // fires once the fetch resolved to an empty list. Loading, a failed fetch,
  // AND a real non-empty list must never carry it: none of those is evidence
  // that the dataset has zero runs.
  pin.match(
    zeroBranch,
    tripleArgs(
      "이 데이터셋에는 완료된 실행이 없습니다 — ③ 시뮬레이션에서 먼저 실행하세요",
      "No completed runs in this dataset — run one on ③ Simulation first",
      "このデータセットには完了した実行がありません — まず③シミュレーションで実行してください",
    ),
    "expected the zero-runs branch to state the absence and name ③ Simulation as where to fix it, in all three languages",
  );
  pin.doesNotMatch(
    errorBranch + neutralBranch,
    /완료된 실행이 없습니다|No completed runs in this dataset|完了した実行がありません/,
    "expected the loading/error/non-empty branches to NEVER claim absence -- that sentence belongs only to the resolved, empty-list case",
  );

  // (b) THE ZERO-STATE BANNER'S NAVIGATION TARGET IS /simulation -- where a
  // run is actually created (POST /api/simulations, live Vissim via the job
  // queue). A plain `next/link` Link, this file's own idiom for a pure route
  // change that mutates no client state (contrast `onClick={() => runSelectRef
  // .current?.focus()}`, which the zero-runs branch must NOT keep -- there is
  // nothing useful to focus in a selector this page just said has no options).
  pin.match(
    zeroBranch,
    /<Link\s+href="\/simulation"/,
    "expected the zero-runs banner to navigate to /simulation, where runs are actually created",
  );
  pin.doesNotMatch(
    zeroBranch,
    /onClick=\{\(\) => runSelectRef\.current\?\.focus\(\)\}/,
    "expected the zero-runs banner to send the user to /simulation, not focus a selector it just said is empty",
  );

  // (e) THE ZERO-STATE LINK IS A REAL CTA BUTTON (unit #67, 2026-08-26
  // DECISIONS): a sentence that merely happens to be clickable is not
  // obviously clickable, so this branch wears the same filled
  // bg-accent/hover:bg-accent-deep/text-white idiom ConfirmDialog's primary
  // action button uses, instead of the neutral outline the "choose a run"
  // and "do this now" prompts wear. It must NOT keep the old neutral-banner
  // look, or a reader has no visual reason to believe this text is a button.
  // FIX (refuter round 2, M2): `\b` fires at a hyphen too (word char -> non-word
  // char), so `/bg-accent\b/` also matched INSIDE `hover:bg-accent-deep` --
  // a mutation that deleted the plain `bg-accent` fill and kept only the hover
  // variant (white text stranded on a white/transparent surface until hover)
  // left this pin green either way (measured). The negative lookahead requires
  // `bg-accent` NOT be followed by `-` or a word character, so it no longer
  // matches inside `bg-accent-deep` and only fires on the standalone fill class.
  pin.match(zeroBranch, /bg-accent(?![-\w])/, "expected the zero-runs banner to wear the filled CTA background");
  pin.match(zeroBranch, /hover:bg-accent-deep/, "expected the zero-runs banner's hover state to deepen the CTA fill");
  pin.match(zeroBranch, /text-white/, "expected the zero-runs banner's text to sit on the filled CTA background");
  // Unit #67 fix (D1, cause 2): the neutral outline's className never actually
  // contained `border-sky-300 bg-sky-50 text-sky-600` as one contiguous run --
  // `px-4 py-2.5 text-left text-[17px] font-semibold leading-7` always sat
  // between `bg-sky-50` and `text-sky-600` (see the "do this now" and "choose
  // a run" buttons below, unchanged) -- so that exact string never fired and
  // never could. `border-sky-` alone is what the neutral outline idiom is
  // built from anywhere in this file; its absence is what "no longer wears
  // the neutral outline" actually means.
  pin.doesNotMatch(
    zeroBranch,
    /border-sky-/,
    "expected the zero-runs banner to no longer wear the neutral outline treatment -- it reads as a button now, not a prompt",
  );

  // (d) THE FAILURE SENTENCE -- names the fetch FAILING, distinct from "zero
  // runs" -- byte-exact in all three languages, and present ONLY in the branch
  // that fires on a resolved, FAILED fetch. This is the orchestrator's own
  // correction: the zero-runs and neutral branches must never carry it either.
  pin.match(
    errorBranch,
    tripleArgs(
      "실행 목록을 불러오지 못했습니다 — 새로고침하거나 서버 상태를 확인하세요",
      "Could not load the run list — refresh the page, or check the server",
      "実行一覧を読み込めませんでした — 再読み込みするか、サーバーの状態を確認してください",
    ),
    "expected the error branch to name the fetch FAILURE and suggest a fix, in all three languages",
  );
  pin.doesNotMatch(
    zeroBranch + neutralBranch,
    /불러오지 못했습니다|Could not load the run list|読み込めませんでした/,
    "expected the failure sentence to appear ONLY in the scenariosError branch",
  );
  // The failure branch offers no click action: nothing a click can do fixes a
  // fetch failing (contrast the zero-runs branch's Link, and the neutral
  // branch's focus handler). It must not smuggle either.
  pin.doesNotMatch(
    errorBranch,
    /<Link\b|onClick=/,
    "expected the error branch to offer no navigation or click action -- there is nothing that fixes a fetch failure by clicking",
  );
  // Unit #67 fix (D5): the failure branch must not smuggle the CTA fill
  // either. It already has no click action (pin above), but a refusal that
  // LOOKS like a filled action button is its own lie -- a reader would
  // reasonably expect a filled button to do something. `errorBranch` is not
  // built from `withoutComments`-free source (it slices `banner`, itself
  // sliced from `surface`), so this one line is sound on real markup, not a
  // comment.
  pin.doesNotMatch(errorBranch, /bg-accent/, "a refusal is never a button");
  // ...and it must not fall back to claiming "choose a run" either -- that
  // sentence implies options exist to choose from, which a failed fetch never
  // established one way or the other.
  pin.doesNotMatch(
    errorBranch,
    /채점할 실행을 선택하세요|Choose a run to score|採点する実行を選択してください/,
    "expected the error branch to never fall back to the neutral 'choose a run' sentence -- a failed fetch is its own named absence",
  );

  // (c) THE RUNS-EXIST BRANCH IS UNCHANGED: #50-d's exact sentence and its
  // exact focus handler both still stand, reached whenever BOTH scenariosError
  // and scenariosEmpty are false -- covering loading AND a real non-empty list.
  pin.match(
    neutralBranch,
    literalPattern('t("채점할 실행을 선택하세요", "Choose a run to score", "採点する実行を選択してください")'),
    "expected the loading/non-empty branch to keep #50-d's exact neutral sentence, byte-identical",
  );
  pin.match(
    neutralBranch,
    /onClick=\{\(\) => runSelectRef\.current\?\.focus\(\)\}/,
    "expected the neutral branch to keep focusing the run selector",
  );

  // THE PICKER'S EMPTY STATE reads the SAME two gates the banner does, so the
  // banner and the <select> can never disagree about which of the three
  // absences (loading is not one) applies.
  const picker = sliceBetween(page, '<option value="">', "</option>");
  pin.match(picker, /\S/, "expected to find the run picker's placeholder <option> (markers moved?)");
  pin.match(
    picker,
    /scenariosError/,
    "expected the picker's placeholder to branch on the SAME scenariosError the banner reads",
  );
  pin.match(
    picker,
    /scenariosEmpty/,
    "expected the picker's placeholder to branch on the SAME scenariosEmpty the banner reads",
  );
  pin.match(
    picker,
    tripleArgs("목록을 불러오지 못함", "Couldn't load list", "リスト読み込み失敗"),
    "expected the placeholder to name the fetch failure once scenariosError, in all three languages",
  );
  pin.match(
    picker,
    tripleArgs("완료된 실행 없음", "No completed runs", "完了した実行なし"),
    "expected the placeholder to name the absence once scenariosEmpty, in all three languages",
  );
  pin.match(
    picker,
    tripleArgs("선택하세요", "Choose a run", "選択してください"),
    "expected the placeholder to keep its original prompt for every other state",
  );
  // MUTUALLY EXCLUSIVE, structurally: one nested ternary, scenariosError
  // checked first, scenariosEmpty second, the original prompt as the only
  // fallback -- so a mutation that checks them in an if/else outside the
  // ternary (letting two conditions both apply) or that reorders the checks
  // fails here, not just a mutation that deletes one branch's text.
  pin.match(
    picker,
    /scenariosError\s*\?[\s\S]{0,160}?:\s*scenariosEmpty\s*\?[\s\S]{0,200}?:\s*t\(/,
    "expected the placeholder to be ONE nested ternary -- scenariosError, then scenariosEmpty, then the default -- so the three labels are mutually exclusive by construction",
  );
  const errorLabelAt = picker.search(tripleArgs("목록을 불러오지 못함", "Couldn't load list", "リスト読み込み失敗"));
  const emptyLabelAt = picker.search(tripleArgs("완료된 실행 없음", "No completed runs", "完了した実行なし"));
  const defaultLabelAt = picker.search(tripleArgs("선택하세요", "Choose a run", "選択してください"));
  assert.ok(
    errorLabelAt > -1 && emptyLabelAt > -1 && defaultLabelAt > -1,
    "expected all three placeholder labels to be found in the picker slice (markers moved?)",
  );
  assert.ok(
    errorLabelAt < emptyLabelAt && emptyLabelAt < defaultLabelAt,
    "expected the placeholder's ternary to test scenariosError before scenariosEmpty before falling back to the default label",
  );

  // THE FETCH ITSELF now goes through this file's OWN async-resource
  // convention (lib/asyncResource.ts) instead of a bare `useState([])` whose
  // `.catch` collapsed a failed fetch into the same value an empty list
  // produces -- which is exactly what made "no runs yet" and "could not ask"
  // indistinguishable before this unit.
  pin.match(
    surface,
    /const \[scenariosLoaded, setScenariosLoaded\] = useState<Loaded<ScenarioMeta\[\]>\s*\|\s*null>\(null\);/,
    "expected the scenario list to be tracked as a Loaded<T>, not a bare useState([])",
  );
  pin.match(
    surface,
    /setScenariosLoaded\(ok\(SCENARIOS_KEY, list\)\)/,
    "expected a successful scenarios fetch to be recorded through ok(), not a bare setScenarios(list)",
  );
  pin.match(
    surface,
    /setScenariosLoaded\(failed\(SCENARIOS_KEY, caught\)\)/,
    "expected a failed scenarios fetch to be recorded through failed(), not silently collapsed into an empty list",
  );
  pin.doesNotMatch(
    surface,
    /\.catch\(\(\) => setScenarios\(\[\]\)\)/,
    "expected the old catch-to-empty-array shape to be gone -- that is exactly what made a failed fetch indistinguishable from zero runs",
  );
});

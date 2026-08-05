import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const rootDir = path.resolve(import.meta.dirname, "..");

function read(relativePath: string) {
  return readFileSync(path.join(rootDir, relativePath), "utf-8");
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

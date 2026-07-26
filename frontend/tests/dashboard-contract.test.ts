import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const rootDir = path.resolve(import.meta.dirname, "..");

function read(relativePath: string) {
  return readFileSync(path.join(rootDir, relativePath), "utf-8");
}

test("main dashboard keeps scenario-comparison core composition", () => {
  const page = read("app/dashboard/page.tsx");

  assert.match(
    page,
    /<DashboardScenarioPicker scenarios=\{scenarios\} selected=\{selected\} onChange=\{setSelected\} \/>/,
    "expected dashboard-specific scenario picker on the main dashboard",
  );
  assert.match(page, /<DashboardMap/, "expected map comparison panel on main dashboard");
  assert.match(page, /<NodeFlowPanel/, "expected node flow panel on main dashboard");
  assert.match(page, /fetchActiveDataset\(\)/, "expected dashboard to check whether a VISSIM dataset is uploaded");
  assert.match(page, /const hasUploadedDataset = activeDataset\?\.source === "uploaded"/, "expected dashboard to require an uploaded dataset");
  assert.match(page, /<DashboardDatasetGate \/>/, "expected scenario controls to be gated before upload");
  assert.match(page, /<DashboardUploadRequiredPanel \/>/, "expected dashboard analysis area to stay locked before upload");
  assert.match(page, /if \(!hasUploadedDataset \|\| !selected\.length\)/, "expected comparison fetches to skip default sample data");
  assert.match(
    page,
    /title=\{t\("네트워크 KPI 비교", "Network KPI Comparison"\)\}/,
    "expected network KPI comparison section on main dashboard",
  );
  assert.match(page, /교통 운영 분석/, "expected concise operations-analysis page title");
  assert.doesNotMatch(page, /시나리오 비교분석 \+ 교차로 비교분석/, "expected old combined title to be removed");
});

test("app shell exposes the merged dashboard routes", () => {
  const shell = read("components/AppShell.tsx");
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const simulationPage = read("app/simulation/page.tsx");

  assert.doesNotMatch(shell, /href: "\/intersections"/, "expected intersections route to be removed from top navigation");
  assert.doesNotMatch(shell, /href: "\/"/, "expected prompt home route to be removed from top navigation");
  assert.match(shell, /href: "\/base-model"/, "expected base-model validation route in top navigation");
  assert.match(shell, /href: "\/dashboard"/, "expected dashboard route in top navigation");
  assert.doesNotMatch(shell, /label: "Prompt"/, "expected prompt-only entry to be removed once assistant is floating");
  assert.match(shell, /FloatingAssistant/, "expected assistant chat launcher to be available across pages");
  // Streaming (not the older one-shot askAssistant) is the current call path
  // so answers render token-by-token instead of appearing all at once.
  assert.match(floatingAssistant, /streamAssistant/, "expected floating assistant to call the assistant query API");
  assert.match(
    floatingAssistant,
    /selected_scenario_ids: activePageContext\?\.selectedScenarioIds/,
    "expected floating assistant to send current page scenario selections to the assistant API",
  );
  assert.match(
    floatingAssistant,
    /selected_node_ids: activePageContext\?\.selectedNodeIds/,
    "expected floating assistant to send current page node selections to the assistant API",
  );
  assert.match(floatingAssistant, /PageContextSummary/, "expected floating assistant to show current page context");
  assert.match(floatingAssistant, /useLanguage/, "expected floating assistant to follow the app language toggle");
  assert.match(floatingAssistant, /response_language: lang/, "expected floating assistant responses to follow the active app language toggle");
  assert.doesNotMatch(floatingAssistant, /inferAssistantResponseLanguage/, "expected prompt text language inference not to override the app language toggle");
  assert.match(floatingAssistant, /buildRouteAssistantContext\(pathname, t\)/, "expected assistant route labels and starters to be localized");
  // Execution consent must never be inferred from the wording of the user's
  // message (a client-side keyword match let a suggested starter chip launch
  // a real VISSIM run with no confirmation). The first send always proposes;
  // running only happens after an explicit confirm click, which resends the
  // same prompt with allowExecution=true.
  assert.doesNotMatch(floatingAssistant, /shouldAllowPromptExecution/, "expected the client-side execution-consent heuristic to be removed");
  assert.match(
    floatingAssistant,
    /allow_tool_execution: allowExecution/,
    "expected execution consent to come from explicit user confirmation, not message wording",
  );
  assert.match(floatingAssistant, /function confirmPromptExecution/, "expected an explicit confirm action before a proposed simulation runs");
  assert.match(floatingAssistant, /function declinePromptExecution/, "expected an explicit decline action for a proposed simulation");
  assert.match(floatingAssistant, /requires_confirmation && !declined/, "expected a visible confirm/cancel control when the backend requires confirmation");
  assert.match(floatingAssistant, /AssistantAnswerContent/, "expected floating assistant to render structured answers");
  assert.match(floatingAssistant, /parseAssistantAnswer/, "expected assistant answers to be split into stable sections");
  assert.match(floatingAssistant, /PANEL_SIZE_STORAGE_KEY/, "expected assistant panel size to persist across sessions");
  assert.match(floatingAssistant, /--assistant-panel-width/, "expected assistant panel width to be driven by resize state");
  assert.match(floatingAssistant, /cursor-nwse-resize/, "expected assistant panel to expose a resize handle");
  assert.match(
    floatingAssistant,
    /vissim:assistant-simulation-complete/,
    "expected floating assistant to notify dashboard pages after prompt-run simulations",
  );
  assert.doesNotMatch(shell, /href: "\/simulation"/, "expected simulation route to be hidden from top navigation");
  assert.match(simulationPage, /SimulationPage/, "expected simulation page implementation to remain available in the codebase");
  assert.doesNotMatch(shell, /Intersection Comparison/, "expected separate intersection comparison label to be removed");
  assert.match(
    shell,
    /Base Model Validation/,
    "expected base model validation label in top navigation",
  );
});

test("root entry redirects to dashboard once assistant is floating", () => {
  const page = read("app/page.tsx");

  assert.match(page, /redirect/, "expected root route to redirect instead of rendering a prompt-only page");
  assert.match(page, /"\/dashboard"/, "expected root path to land on the dashboard");
  assert.doesNotMatch(page, /VissimPromptHome/, "expected prompt-only first screen to be removed from root");
});

test("base model page wires workbook validation and Category 1 checks", () => {
  const page = read("app/base-model/page.tsx");
  const api = read("lib/api.ts");
  const datasetBanner = read("components/VissimDatasetBanner.tsx");

  assert.match(page, /fetchBaseWorkbook\(\)/, "expected base workbook loading");
  assert.match(page, /uploadBaseWorkbook/, "expected standalone workbook upload support");
  assert.match(page, /resetBaseWorkbook/, "expected standalone workbook reset support");
  assert.match(page, /VissimDatasetBanner/, "expected Vissim dataset update control on base-model validation page");
  assert.match(page, /xl:grid-cols-\[1fr_1fr_0\.85fr_1\.15fr_1\.15fr\]/, "expected base-model setup controls to align in one card row");
  assert.match(page, /fullWidth className="h-full bg-slate-50\/70 shadow-none"/, "expected base-model dataset control to match adjacent setup-card sizing");
  assert.match(page, /handleDatasetChange/, "expected base-model page to reload validation inputs after dataset changes");
  assert.match(page, /vissim:page-context/, "expected base-model page to publish assistant page context");
  assert.match(page, /selectedScenarioIds: selectedScenarioId \? \[selectedScenarioId\] : \[\]/, "expected selected base scenario to be passed to floating assistant context");
  assert.match(page, /Validation workbook is not connected/, "expected workbook status to be included in assistant context");
  assert.match(page, /\[datasetRevision, selectedScenarioId, t\]/, "expected selected scenario results to refetch after dataset changes");
  assert.match(page, /handleWorkbookReset/, "expected reset button handler");
  assert.match(page, /fetchResults\(selectedScenarioId\)/, "expected selected base scenario result loading");
  assert.match(page, /GEH < 5/, "expected GEH band chart content");
  assert.match(page, /GehFormulaExplanation/, "expected GEH formula explanation in the Category 1 card");
  assert.match(page, /viewBox="0 0 230 58"/, "expected GEH formula to render as a fixed SVG");
  assert.match(page, /M78 31 L84 42 L94 13 L218 13/, "expected GEH radical path to be fixed");
  assert.match(page, /M \+ C/, "expected GEH formula denominator");
  assert.match(page, /x1="110" x2="203" y1="32" y2="32"/, "expected GEH formula fraction bar");
  assert.match(page, /C\/O/, "expected counted or observed volume definition");
  assert.match(page, /observed_travel_time_sec/, "expected observed travel time handling");
  assert.match(page, /model_travel_time_sec/, "expected model travel time handling");
  assert.match(page, /travelWithinPct: 100/, "expected Category 1 travel time threshold");
  assert.match(page, /CountComparisonOverview/, "expected visual count comparison overview");
  assert.match(page, /CountNodeDirectionMatrixRow/, "expected node rows in the intersection-by-direction matrix");
  assert.match(page, /CountDirectionMatrixCell/, "expected cardinal-direction comparison cells");
  assert.match(page, /fetchActiveDataset/, "expected base-model page to check whether a VISSIM dataset is uploaded");
  assert.match(page, /shouldLoadScenarioSelection \? fetchScenarios\(\) : Promise\.resolve<ScenarioMeta\[\]>\(\[\]\)/, "expected default dataset state to keep scenario dropdowns empty");
  assert.match(page, /disabled=\{scenarios\.length === 0\}/, "expected scenario dropdowns disabled until a dataset is uploaded");
  assert.match(page, /setPeriod\("AM"\)/, "expected dataset restore to reset the analysis-period dropdown");
  assert.match(page, /COUNT_DIRECTION_ORDER\.map/, "expected fixed north/east/south/west direction columns");
  assert.match(page, /grid-cols-\[190px_repeat\(4,minmax\(230px,1fr\)\)\]/, "expected stable intersection-by-direction matrix layout");
  assert.match(page, /buildCountDirectionGroups/, "expected count rows grouped into north/east/south/west approaches");
  assert.match(page, /ComparisonBarLine/, "expected observed-vs-model bar comparison");
  assert.match(page, /groupCountComparisonsByNode/, "expected count rows grouped by node");
  assert.doesNotMatch(page, /<table className="w-full text-sm">/, "expected old long count comparison table to be removed");
  assert.match(
    page,
    /Please check the travel time measurement sections defined in VISSIM are consistent with the observed delay/,
    "expected Vissim travel-time measurement warning message",
  );
  assert.match(page, /t\("노드", "Node"\)/, "expected node label to follow the language toggle");
  assert.match(page, /t\("루트", "Route"\)/, "expected route label to follow the language toggle");
  assert.match(page, /t\("통과", "PASS"\)/, "expected pass badge to follow the language toggle");
  assert.match(page, /t\("확인", "CHECK"\)/, "expected check badge to follow the language toggle");

  assert.match(api, /fetchBaseWorkbook/, "expected workbook API binding");
  assert.match(api, /uploadBaseWorkbook/, "expected workbook upload API binding");
  assert.match(api, /resetBaseWorkbook/, "expected workbook reset API binding");
  assert.match(api, /method: "DELETE"/, "expected workbook reset to delete only validation workbook data");
  assert.match(api, /traveltime_interval_class/, "expected traveltime interval class typing");

  assert.match(datasetBanner, /모델 선택/, "expected dataset card title in Korean mode");
  assert.match(datasetBanner, /"Model"/, "expected dataset card title in English mode");
  assert.match(datasetBanner, /파일\/ZIP 업로드/, "expected dataset upload button in Korean mode");
  assert.match(datasetBanner, /Load File \/ ZIP/, "expected dataset upload button in English mode");
  assert.match(datasetBanner, /기본값 복원/, "expected dataset reset button in Korean mode");
  assert.match(datasetBanner, /Restore Default/, "expected dataset reset button in English mode");
  assert.match(datasetBanner, /ui-button-primary inline-flex items-center px-3 py-2 text-xs/, "expected dataset upload button to match the Excel upload style");
  assert.match(datasetBanner, /ui-button-danger inline-flex items-center px-3 py-2 text-xs/, "expected dataset reset button to match the Excel reset style");
  assert.match(datasetBanner, /hasCustomBackground/, "expected dataset card to allow the base-model gray background override");
  assert.match(datasetBanner, /hasCustomShadow/, "expected dataset card to allow the base-model shadow override");
  assert.doesNotMatch(datasetBanner, /Vissim dataset/, "expected old mixed-case dataset label to be removed");
  assert.doesNotMatch(datasetBanner, /Load file \/ zip/, "expected old mixed-case upload label to be removed");
});

test("intersection comparison is merged into the main dashboard", () => {
  const page = read("app/dashboard/page.tsx");
  const proxy = read("proxy.ts");

  assert.match(page, /selectedNodeIds/, "expected multi-node comparison state on the main dashboard");
  assert.match(page, /vissim:page-context/, "expected dashboard to publish assistant page context");
  assert.match(page, /selectedScenarioIds: selected/, "expected selected dashboard scenarios to be passed to floating assistant context");
  assert.match(page, /selectedNodeIds,/, "expected selected dashboard nodes to be passed to floating assistant context");
  assert.match(page, /IntersectionPerformanceGrid/, "expected intersection performance cards on the main dashboard");
  assert.match(page, /DirectionDelayChart/, "expected cardinal-direction delay bars in intersection performance");
  assert.match(page, /flex h-\[520px\] min-h-0 flex-col overflow-hidden/, "expected selected-intersection panel to contain its own scroll area");
  assert.match(page, /mt-4 min-h-0 flex-1 overflow-hidden border-t/, "expected node detail panel to shrink within the card");
  assert.doesNotMatch(page, /h-\[calc\(100%-112px\)\]/, "expected fixed selected-panel height calculation to be removed");
  assert.match(page, /LOS_COLORS/, "expected delay bars to use LOS colors");
  assert.match(page, /IntersectionMetricBars/, "expected horizontal intersection metric bars");
  assert.match(page, /VolumeByIntersectionChart/, "expected volume composition chart by selected intersection");
  assert.match(page, /QLENMAX_MEAN/, "expected queue charts to consume aggregated VISSIM queue metrics");
  assert.match(page, /useSectionLabels/, "expected travel-time charts to use section labels when intervals collapse");
  // Travel-time chart must plot the real measured values, not a synthesized curve.
  assert.doesNotMatch(page, /stabilizeTravelTimeProfile/, "expected the fabricated travel-time easing to be removed");
  assert.doesNotMatch(page, /Math\.pow\(progress, 1\.18\)/, "expected no synthetic accelerated display profile on travel time");
  assert.match(page, /SectionJumpIndex/, "expected section shortcut index");
  assert.match(page, /xl:grid-cols-\[minmax\(0,1fr\)_460px\]/, "expected widened map/intersection grid (Console-density pass)");
  assert.doesNotMatch(
    page,
    /xl:grid-cols-\[148px_minmax\(0,1fr\)\]/,
    "expected shortcut tab not to reserve a layout column",
  );
  assert.match(
    page,
    /className="group absolute left-0 top-24 z-\[950\] h-11 w-10 -translate-x-3"/,
    "expected shortcut index root to reserve only a slim handle",
  );
  assert.match(
    page,
    /pointer-events-none absolute right-\[42px\] top-0 w-\[188px\][^"]*group-hover:pointer-events-auto[^"]*group-hover:opacity-100/,
    "expected shortcut panel to appear only on hover or focus",
  );
  assert.match(page, /aria-label=\{t\("분석 목차 열기", "Open analysis sections"\)\}/, "expected accessible side tab handle");
  assert.match(page, /id="intersection-performance"/, "expected intersection performance anchor");
  assert.match(page, /id="travel-time-comparison"/, "expected travel time anchor");
  assert.match(page, /id="network-kpi-comparison"/, "expected network KPI anchor");
  assert.match(page, /id="overall-results-analysis"/, "expected overall results anchor");
  assert.match(page, /TravelTimeComparison/, "expected travel-time comparison on the main dashboard");
  assert.match(page, /OverallResultsSection/, "expected overall result analysis on the main dashboard");
  assert.match(page, /결과 해석과 보고서 생성만 간단하게 제공합니다/, "expected simplified overall analysis copy");
  assert.match(page, /xl:grid-cols-\[minmax\(0,1fr\)_360px\]/, "expected simplified interpretation/report layout");
  assert.doesNotMatch(page, /t\("시나리오 순위"/, "expected scenario ranking UI to be removed from overall analysis");
  assert.doesNotMatch(page, /t\("주요 인사이트"/, "expected key insight UI to be removed from overall analysis");
  assert.match(page, /intersectionCards=\{intersectionCards\}/, "expected overall analysis to receive selected intersection evidence");
  assert.match(page, /buildCalibrationReportMarkdown/, "expected report markdown generator in overall analysis");
  assert.match(page, /handleDownloadWordReport/, "expected Word report download handler");
  assert.match(page, /base-model-calibration-validation-\$\{stamp\}\.doc/, "expected generated Word report filename");
  assert.match(proxy, /matcher: "\/intersections\/:path\*"/, "expected old intersections route to redirect");
  assert.match(proxy, /NextResponse\.redirect\(new URL\("\/dashboard", request\.url\)\)/, "expected redirect to the merged dashboard");
});

test("dashboard map signalized intersection uses traffic light artwork", () => {
  const map = read("components/DashboardMap.tsx");

  assert.match(map, /function buildSignalSignSvg/, "expected dedicated signal intersection icon");
  assert.match(map, /<rect x="20" y="3" width="24" height="58" rx="12"/, "expected vertical traffic light body");
  assert.match(map, /fill="#f3161d"/, "expected red signal lamp");
  assert.match(map, /fill="#ffd719"/, "expected yellow signal lamp");
  assert.match(map, /fill="#4aa72f"/, "expected green signal lamp");
});

test("dataset mutations prefer the same-origin API proxy", () => {
  const api = read("lib/api.ts");
  const nextConfig = read("next.config.ts");

  assert.match(api, /async function apiMutationFetch/, "expected shared mutation fetch helper");
  assert.match(api, /async function apiDirectMutationFetch/, "expected long-running assistant requests to bypass the proxy timeout");
  assert.match(api, /function listDirectMutationApiBases/, "expected direct assistant requests to use a proxy-free base list");
  assert.match(api, /isLikelyFrontendApiBase/, "expected stored frontend origins to be skipped for direct assistant requests");
  assert.match(api, /fetch\(normalizedPath, sameOriginRequestInit\(init\)\)/, "expected same-origin API proxy attempt first");
  assert.match(api, /askAssistant[\s\S]*apiDirectMutationFetch\("\/api\/assistant\/query"/, "expected assistant query to call the backend API directly");
  assert.match(api, /response_language\?: "ko" \| "en"/, "expected assistant requests to carry the active UI language");
  assert.match(api, /"settings" \| "capabilities"/, "expected assistant API typing to include capability/help questions");
  assert.match(api, /uploadInpxDataset[\s\S]*apiMutationFetch\(path/, "expected dataset upload to use API proxy");
  assert.match(api, /resetActiveDataset[\s\S]*apiMutationFetch\("\/api\/datasets\/reset"/, "expected dataset reset to use API proxy");
  assert.match(nextConfig, /proxyClientMaxBodySize/, "expected API proxy upload body limit override for VISSIM zip datasets");
  assert.match(nextConfig, /250 \* 1024 \* 1024/, "expected VISSIM zip upload proxy limit above the 10MB Next default");
  assert.match(nextConfig, /API_PROXY_MAX_BODY_SIZE_BYTES/, "expected configurable API proxy upload limit");
});

test("node direction table keeps all three comparison view modes", () => {
  const nodeTable = read("components/NodeDirectionTable.tsx");

  assert.match(nodeTable, /\{ key: "matrix", label:/, "expected matrix mode option");
  assert.match(nodeTable, /\{ key: "intersection", label:/, "expected intersection mode option");
  assert.match(nodeTable, /\{ key: "bars", label:/, "expected bars mode option");
});

test("selected intersection panel omits scenario total volume chart", () => {
  const nodeFlowPanel = read("components/NodeFlowPanel.tsx");

  assert.doesNotMatch(nodeFlowPanel, /Total Volume by Scenario/, "expected scenario total volume chart to be removed");
  assert.doesNotMatch(nodeFlowPanel, /function ScenarioVolumeBars/, "expected compact volume chart component to be removed");
  assert.doesNotMatch(
    nodeFlowPanel,
    /scenarioTotals/,
    "expected selected-intersection panel to avoid scenario total volume calculations",
  );
});

test("network preview page publishes assistant page context", () => {
  const page = read("app/network/page.tsx");

  assert.match(page, /vissim:page-context/, "expected network preview page to publish assistant page context");
  assert.match(
    page,
    /vissim:assistant-context-request/,
    "expected network preview page to republish context when the floating assistant asks for it",
  );
  assert.match(page, /route: "\/network"/, "expected network preview page context to identify its own route");
  // P6: diagram clicks publish STRUCTURED selection, not only a prose note —
  // these two keys are what FloatingAssistant turns into selected_link_ids /
  // selected_element on the request.
  assert.match(
    page,
    /selectedLinks: selectedLinkInfo \? \[selectedLinkInfo\] : \[\]/,
    "expected the network preview page to publish the clicked link as structured context",
  );
  assert.match(
    page,
    /selectedElement: selectedElementInfo/,
    "expected the network preview page to publish the clicked element as structured context",
  );
});

test("P6: diagram selection reaches the assistant request instead of stopping at a chip", () => {
  const networkPage = read("app/network/page.tsx");
  const dashboardPage = read("app/dashboard/page.tsx");
  const networkViewer = read("components/NetworkViewer.tsx");
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const api = read("lib/api.ts");

  // Element glyphs are clickable, guarded by the same click-vs-drag check the
  // link/node hit targets use, so a pan that ends over a glyph is not a click.
  assert.match(networkViewer, /onElementClick\?: \(ref: NetworkElementRef\) => void/, "expected the viewer to expose element clicks");
  assert.match(
    networkViewer,
    /sel\.style\("cursor", "pointer"\)\.on\("click", \(event: MouseEvent\) => \{\s*if \(isDragClick\(event\)\) return;\s*onElementClick\(ref\);/,
    "expected element clicks to reuse the drag guard before publishing a reference",
  );

  for (const [label, page] of [["network preview", networkPage], ["dashboard", dashboardPage]] as const) {
    assert.match(page, /onElementClick=\{handleElementClick\}/, `expected ${label} to wire element clicks`);
    assert.match(page, /selectedLinks: selectedLinkInfo \? \[selectedLinkInfo\] : \[\]/, `expected ${label} to publish selected links`);
    assert.match(page, /selectedElement: selectedElementInfo/, `expected ${label} to publish the selected element`);
  }

  assert.match(
    floatingAssistant,
    /selected_link_ids: activePageContext\?\.selectedLinks\?\.map\(\(link\) => link\.id\) \?\? \[\]/,
    "expected the floating assistant to send clicked links to the assistant API",
  );
  assert.match(
    floatingAssistant,
    /selected_element: activePageContext\?\.selectedElement \?\? null/,
    "expected the floating assistant to send the clicked element to the assistant API",
  );
  assert.match(api, /selected_link_ids\?: string\[\]/, "expected the assistant request type to carry clicked link ids");
  assert.match(api, /selected_element\?: \{/, "expected the assistant request type to carry the clicked element");
});

test("P2: agent session id is broadcast and consumed for the edited-network view", () => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const networkPage = read("app/network/page.tsx");

  assert.match(
    floatingAssistant,
    /window\.dispatchEvent\(new CustomEvent\("vissim:agent-session", \{ detail: \{ sessionId: agentSessionId \} \}\)\)/,
    "expected the floating assistant to broadcast its current agent session id whenever it changes",
  );
  assert.match(
    networkPage,
    /window\.addEventListener\("vissim:agent-session", handleAgentSession\)/,
    "expected the network preview page to listen for the broadcast agent session id",
  );
});

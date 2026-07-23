"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import CurationBar from "@/components/dashboard/CurationBar";
import NodeFlowPanel from "@/components/NodeFlowPanel";
import VissimDatasetBanner from "@/components/VissimDatasetBanner";
import {
  fetchActiveDataset,
  fetchCompare,
  fetchCompareSummary,
  fetchRealCounts,
  fetchScenarios,
  type ActiveDataset,
  type CompareSummaryResponse,
  type DashboardCurationSpec,
  type NodeRealCounts,
  type ScenarioMeta,
  type ScenarioResults,
} from "@/lib/api";
import { getScenarioDisplayColor } from "@/lib/scenarioPalette";
import { errorTone, gehTone, toneBadgeClass, type ValidationTone } from "@/lib/validationThresholds";

const DashboardMap = dynamic(() => import("@/components/DashboardMap"), { ssr: false });

const KPI_METRICS = [
  // isTotal metrics (whole-network sums) scale with vehicle count, so their
  // "Best" (typically the lowest) can simply mean fewer vehicles were
  // served, not a better outcome — KpiCard caveats those instead of a bare
  // "Best" tag.
  { key: "DELAYAVG", label: "Average Delay", unit: "s/veh", higherIsBetter: false, isTotal: false },
  { key: "SPEEDAVG", label: "Average Speed", unit: "km/h", higherIsBetter: true, isTotal: false },
  { key: "TRAVTMTOT", label: "Total Travel Time", unit: "s", higherIsBetter: false, isTotal: true },
  { key: "DELAYTOT", label: "Total Delay", unit: "s", higherIsBetter: false, isTotal: true },
  { key: "VEHARR", label: "Arrived Vehicles", unit: "veh", higherIsBetter: true, isTotal: false },
  { key: "STOPSAVG", label: "Average Stops", unit: "stops/veh", higherIsBetter: false, isTotal: false },
] as const;

const MAX_SELECTED_NODES = 6;

type NodeDirectionRow = NonNullable<ScenarioResults["node_directions"]>[number];
type DirectionKey = "north" | "east" | "south" | "west";
type LosGrade = "A" | "B" | "C" | "D" | "E" | "F";
type IntersectionMetricBarValue = {
  nodeId: string;
  nodeName: string;
  value: number | null;
  scenarioName?: string | null;
};

type IntersectionPerformanceCard = {
  nodeId: string;
  nodeName: string;
  delayByDirection: Array<{ direction: DirectionKey; value: number | null; los: LosGrade | null }>;
  maxQueueLength: number | null;
  maxQueueScenarioName: string | null;
  worstApproachDelay: number | null;
  worstDelayScenarioName: string | null;
  volumeTotal: number | null;
  volumeDirections: Record<DirectionKey, number>;
};

type TravelTimeSeries = {
  scenario: ScenarioMeta;
  points: Array<{ label: string; value: number | null }>;
};
type TravelTimeChart = {
  series: TravelTimeSeries[];
  labels: string[];
  truncated: boolean;
  useSectionLabels: boolean;
};
type AssistantSimulationCompleteDetail = {
  jobId?: string;
  scenarioResultId?: string | null;
};

const DIRECTION_LABELS: Record<DirectionKey, { ko: string; en: string; shortKo: string; shortEn: string }> = {
  north: { ko: "북", en: "North", shortKo: "북", shortEn: "N" },
  east: { ko: "동", en: "East", shortKo: "동", shortEn: "E" },
  south: { ko: "남", en: "South", shortKo: "남", shortEn: "S" },
  west: { ko: "서", en: "West", shortKo: "서", shortEn: "W" },
};

const DIRECTION_ORDER: DirectionKey[] = ["north", "east", "south", "west"];
const DIRECTION_COLORS: Record<DirectionKey, string> = {
  north: "#6da7ec",
  east: "#9cc8a4",
  south: "#d97757",
  west: "#93a0de",
};
const INTERSECTION_COLORS = ["#6da7ec", "#9cc8a4", "#d97757", "#93a0de", "#b5b3ab", "#4a83c2"];
const LOS_COLORS: Record<LosGrade, string> = {
  A: "#22c55e",
  B: "#a3d977",
  C: "#e5c76b",
  D: "#ee9b5c",
  E: "#e4574d",
  F: "#a83a32",
};
// Delay-second bands behind resolveLosGrade(), spelled out for the legend so
// "LOS" isn't shown as an unexplained letter anywhere it appears.
const LOS_GRADE_THRESHOLDS: Array<{ grade: LosGrade; label: string }> = [
  { grade: "A", label: "≤10s" },
  { grade: "B", label: "≤20s" },
  { grade: "C", label: "≤35s" },
  { grade: "D", label: "≤55s" },
  { grade: "E", label: "≤80s" },
  { grade: "F", label: ">80s" },
];

export default function DashboardPage() {
  const { t, lang } = useLanguage();
  const [scenarios, setScenarios] = useState<ScenarioMeta[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, ScenarioResults>>({});
  const [summary, setSummary] = useState<CompareSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [datasetLoading, setDatasetLoading] = useState(true);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [nodeNamesById, setNodeNamesById] = useState<Record<string, string>>({});
  const [realCounts, setRealCounts] = useState<NodeRealCounts[]>([]);
  const [datasetRevision, setDatasetRevision] = useState("default");
  const [curationSpec, setCurationSpec] = useState<DashboardCurationSpec | null>(null);
  const [audience, setAudience] = useState<"plain" | "expert">("plain");
  const requestSerial = useRef(0);
  const loadSerial = useRef(0);
  // Auto-seed a default intersection once per dataset so the performance and
  // flow panels are not empty on first load; reset on dataset change.
  const autoSeededNodesRef = useRef(false);
  const hasUploadedDataset = activeDataset?.source === "uploaded";
  // Focus mode: with no curation spec every section is visible; with one,
  // only the LLM-chosen sections render (the header and curation bar always stay).
  const sectionVisible = useCallback(
    (sectionId: string) => curationSpec == null || curationSpec.charts.includes(sectionId),
    [curationSpec],
  );
  const clearDashboardData = useCallback(() => {
    requestSerial.current += 1;
    loadSerial.current += 1;
    setScenarios([]);
    setSelected([]);
    setResults({});
    setSummary(null);
    setSelectedNodeIds([]);
    setNodeNamesById({});
    setRealCounts([]);
    autoSeededNodesRef.current = false;
    // An AI curation answer is only valid for the scenarios/data it was
    // computed against; a dataset change invalidates it immediately.
    setCurationSpec(null);
  }, []);

  const reloadScenariosAndValidation = useCallback((preferredScenarioId?: string | null) => {
    const loadId = ++loadSerial.current;
    setLoading(true);
    setError(null);

    Promise.allSettled([fetchScenarios(), fetchRealCounts()]).then(([scenarioOut, countsOut]) => {
      if (loadSerial.current !== loadId) return;

      if (scenarioOut.status === "fulfilled") {
        const items = scenarioOut.value;
        setScenarios(items);
        setSelected((current) => {
          const validCurrent = current.filter((id) => items.some((item) => item.id === id));
          if (preferredScenarioId && items.some((item) => item.id === preferredScenarioId)) {
            return [preferredScenarioId, ...validCurrent.filter((id) => id !== preferredScenarioId)].slice(0, 4);
          }
          return validCurrent.length > 0 ? validCurrent.slice(0, 4) : items.slice(0, 4).map((item) => item.id);
        });
      } else {
        setScenarios([]);
        setSelected([]);
        setError(t("시나리오 목록을 불러오지 못했습니다.", "Failed to load scenario list."));
      }

      setRealCounts(countsOut.status === "fulfilled" ? countsOut.value : []);
      setLoading(false);
    });
  }, [t]);

  const loadActiveDataset = useCallback(() => {
    let cancelled = false;
    // Deferred one microtask so this can be called directly from a useEffect
    // body without React flagging a synchronous setState-in-effect (same
    // pattern already used below for the compare-results fetch effect).
    Promise.resolve().then(() => {
      if (!cancelled) setDatasetLoading(true);
    });

    fetchActiveDataset()
      .then((dataset) => {
        if (cancelled) return;
        setActiveDataset(dataset);
        setDatasetRevision(dataset.revision ?? "default");
        setDatasetLoading(false);
        setError(null);

        if (dataset.source === "uploaded") {
          reloadScenariosAndValidation();
        } else {
          clearDashboardData();
          setLoading(false);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setActiveDataset(null);
        setDatasetLoading(false);
        clearDashboardData();
        setLoading(false);
        // Previously this dead-ended with no way to retry short of a full
        // page reload once the backend was briefly unreachable.
        setError(t("데이터셋 상태를 확인하지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.", "Failed to check dataset status. Make sure the backend server is running."));
      });

    return () => {
      cancelled = true;
    };
  }, [clearDashboardData, reloadScenariosAndValidation, t]);

  useEffect(() => loadActiveDataset(), [loadActiveDataset]);

  // The AI curation answer (headline + insight cards + focus mode) is only
  // valid for the scenario set it was computed against; changing which
  // scenarios are compared must not leave stale numbers on screen.
  const selectedKey = selected.join(",");
  const isFirstSelectionRender = useRef(true);
  useEffect(() => {
    if (isFirstSelectionRender.current) {
      isFirstSelectionRender.current = false;
      return;
    }
    setCurationSpec(null);
  }, [selectedKey]);

  useEffect(() => {
    const reqId = ++requestSerial.current;

    if (!hasUploadedDataset || !selected.length) {
      Promise.resolve().then(() => {
        if (requestSerial.current !== reqId) return;
        setResults({});
        setSummary(null);
        setLoading(false);
      });
      return;
    }

    Promise.resolve().then(() => {
      if (requestSerial.current !== reqId) return;
      setLoading(true);
      setError(null);
    });

    Promise.allSettled([fetchCompare(selected), fetchCompareSummary(selected)]).then(
      ([compareOut, summaryOut]) => {
        if (requestSerial.current !== reqId) return;

        if (compareOut.status !== "fulfilled") {
          setResults({});
          setSummary(null);
          setError(t("비교 데이터를 불러오지 못했습니다.", "Failed to load comparison data."));
          setLoading(false);
          return;
        }

        setResults(compareOut.value);
        setSummary(summaryOut.status === "fulfilled" ? summaryOut.value : null);
        setLoading(false);
      },
    );
  }, [hasUploadedDataset, selected, t]);

  // Seed the busiest intersection into the selection once results arrive, so
  // the intersection-performance and flow panels render real content on first
  // load instead of an empty "click a node" prompt. Runs at most once per
  // dataset (autoSeededNodesRef), and never overrides a user's own selection.
  useEffect(() => {
    if (autoSeededNodesRef.current) return;
    if (selectedNodeIds.length > 0) return;
    const top = summary?.top_congested_nodes?.[0];
    if (!top?.node_id) return;
    autoSeededNodesRef.current = true;
    const nodeId = String(top.node_id);
    const nodeName = top.node_name ?? `Node ${nodeId}`;
    setNodeNamesById((prev) => ({ ...prev, [nodeId]: nodeName }));
    setSelectedNodeIds([nodeId]);
  }, [summary, selectedNodeIds.length]);

  useEffect(() => {
    function handleAssistantSimulationComplete(event: Event) {
      if (!hasUploadedDataset) return;
      const detail = (event as CustomEvent<AssistantSimulationCompleteDetail>).detail;
      reloadScenariosAndValidation(detail?.scenarioResultId ?? null);
    }

    window.addEventListener("vissim:assistant-simulation-complete", handleAssistantSimulationComplete);
    return () => {
      window.removeEventListener("vissim:assistant-simulation-complete", handleAssistantSimulationComplete);
    };
  }, [hasUploadedDataset, reloadScenariosAndValidation]);

  const activeScenarios = useMemo(
    () => scenarios.filter((scenario) => selected.includes(scenario.id)),
    [scenarios, selected],
  );

  const hasData = useMemo(
    () => activeScenarios.some((scenario) => results[scenario.id]),
    [activeScenarios, results],
  );
  // Section ids that pass their OWN data gates right now, independent of any
  // curation answer — lets CurationBar report how many it hid and explain
  // when it picked a section whose data (e.g. field counts) isn't loaded.
  const applicableSectionIds = useMemo(() => {
    const ids = ["map-selection"];
    if (hasData) ids.push("network-kpi-comparison", "intersection-performance", "travel-time-comparison", "overall-results-analysis");
    if (hasData && realCounts.length > 0) ids.push("field-count-validation");
    return ids;
  }, [hasData, realCounts.length]);

  const minRunCount = useMemo(() => {
    const counts = activeScenarios.map((scenario) => results[scenario.id]?.run_count ?? 1).filter((count) => count > 0);
    return counts.length ? Math.min(...counts) : 1;
  }, [activeScenarios, results]);

  const activeNodeId = selectedNodeIds[selectedNodeIds.length - 1] ?? null;
  const activeNodeName = activeNodeId ? nodeNamesById[activeNodeId] ?? `Node ${activeNodeId}` : "";

  const intersectionCards = useMemo(
    () => buildIntersectionPerformanceCards(selectedNodeIds, nodeNamesById, activeScenarios, results),
    [activeScenarios, nodeNamesById, results, selectedNodeIds],
  );

  const travelTimeSeries = useMemo(
    () => buildTravelTimeSeries(activeScenarios, results),
    [activeScenarios, results],
  );

  const overallNarrative = useMemo(
    () => buildOverallNarrative(summary, activeScenarios, hasData, lang),
    [activeScenarios, hasData, summary, lang],
  );

  const handleNodeClick = useCallback((nodeId: string, nodeName: string) => {
    setNodeNamesById((prev) => ({ ...prev, [nodeId]: nodeName }));
    setSelectedNodeIds((prev) => {
      if (prev.includes(nodeId)) {
        return prev.filter((id) => id !== nodeId);
      }

      const next = [...prev, nodeId];
      return next.length <= MAX_SELECTED_NODES ? next : next.slice(next.length - MAX_SELECTED_NODES);
    });
  }, []);

  const handleRemoveNode = useCallback((nodeId: string) => {
    setSelectedNodeIds((prev) => prev.filter((id) => id !== nodeId));
  }, []);

  const handleDatasetChange = useCallback((dataset?: ActiveDataset) => {
    setActiveDataset(dataset ?? null);
    clearDashboardData();
    setDatasetRevision(dataset?.revision ?? String(Date.now()));
    setDatasetLoading(false);
    setError(null);

    if (dataset?.source === "uploaded") {
      reloadScenariosAndValidation();
    } else {
      setLoading(false);
    }
  }, [clearDashboardData, reloadScenariosAndValidation]);

  useEffect(() => {
    const publishAssistantContext = () => {
      window.dispatchEvent(
        new CustomEvent("vissim:page-context", {
          detail: {
            route: "/dashboard",
            label: "Dashboard context",
            selectedScenarioIds: selected,
            selectedScenarioNames: activeScenarios.map((scenario) => scenario.name),
            selectedNodeIds,
            selectedNodeNames: selectedNodeIds.map((nodeId) => nodeNamesById[nodeId] ?? `Node ${nodeId}`),
            dataset: activeDataset
              ? {
                  filename: activeDataset.filename,
                  source: activeDataset.source,
                  revision: activeDataset.revision,
                }
              : null,
            notes: [
              hasUploadedDataset ? "Uploaded VISSIM dataset is active." : "Dashboard analysis is waiting for an uploaded VISSIM dataset.",
              hasData ? "Scenario comparison results are loaded." : "Scenario comparison results are not loaded yet.",
              // Lets the floating chat explain (if asked) why some sections
              // are currently hidden — the two AI surfaces otherwise have no
              // awareness of each other's state.
              ...(curationSpec
                ? [
                    `The dashboard is currently focused by a user question (AI answer: "${curationSpec.headline || "n/a"}"). Only these sections are shown: ${curationSpec.charts.join(", ") || "none"}. The user can click "Show everything" in the curation bar to see all sections again.`,
                  ]
                : []),
            ],
            updatedAt: Date.now(),
          },
        }),
      );
    };

    publishAssistantContext();
    window.addEventListener("vissim:assistant-context-request", publishAssistantContext);
    return () => {
      window.removeEventListener("vissim:assistant-context-request", publishAssistantContext);
    };
  }, [activeDataset, activeScenarios, curationSpec, hasData, hasUploadedDataset, nodeNamesById, selected, selectedNodeIds]);

  return (
    <div className="space-y-6 pb-16">
      <section className="px-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 flex-1 flex-col gap-4">
            <div>
              <h1 className="text-[22px] font-medium tracking-tight text-white">
                {t("교통 운영 분석", "Traffic Operations Analysis")}
              </h1>
              <p className="text-sm text-slate-600">
                {t("시나리오와 교차로 성능을 지도, 통행시간, 네트워크 지표로 함께 확인합니다.", "Review scenario and intersection performance through the map, travel time, and network metrics.")}
              </p>
            </div>
            {hasUploadedDataset ? (
              <DashboardScenarioPicker scenarios={scenarios} selected={selected} onChange={setSelected} />
            ) : (
              <DashboardDatasetGate />
            )}
          </div>
          <div className="flex flex-col items-end gap-3">
            <div className="flex min-h-[34px] items-center">
              {(datasetLoading || loading) && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-600">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
                  {datasetLoading ? t("데이터셋 확인 중", "Checking dataset") : t("로딩 중", "Loading")}
                </span>
              )}
              {!datasetLoading && !hasUploadedDataset && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-600">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                  {t("파일 업로드 필요", "Upload required")}
                </span>
              )}
              {!datasetLoading && !loading && hasData && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-600">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  {activeScenarios.filter((scenario) => results[scenario.id]).length}/{activeScenarios.length} {t("시나리오 준비됨", "scenarios ready")}
                </span>
              )}
            </div>
            <VissimDatasetBanner onDatasetChange={handleDatasetChange} />
          </div>
        </div>
      </section>

      {error && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => loadActiveDataset()}
            className="shrink-0 rounded-lg border border-rose-300 bg-surface px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100"
          >
            {t("다시 시도", "Retry")}
          </button>
        </div>
      )}

      {hasUploadedDataset && (
        <CurationBar
          selectedScenarioIds={selected}
          selectedNodeIds={selectedNodeIds}
          disabled={!hasData}
          spec={curationSpec}
          onSpecChange={setCurationSpec}
          applicableSectionIds={applicableSectionIds}
          audience={audience}
          onAudienceChange={setAudience}
        />
      )}

      {hasUploadedDataset && !sectionVisible("map-selection") ? null : hasUploadedDataset ? (
        <section id="map-selection" className="scroll-mt-6">
          <SectionHeader
            title={t("지도 기반 교차로 선택", "Map-Based Intersection Selection")}
            subtitle={t("기존 대시보드 지도를 그대로 사용해 비교할 교차로를 최대 6개까지 선택합니다.", "Use the existing dashboard map to select up to six intersections for comparison.")}
          />
          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
            <div className="relative">
              {hasData && <SectionJumpIndex showValidation={realCounts.length > 0} sectionVisible={sectionVisible} />}

              <div className="relative h-[520px] overflow-hidden rounded-xl border border-slate-300/70 bg-ink shadow-[0_18px_34px_rgba(15,23,42,0.16)] xl:h-[60vh]">
                <DashboardMap
                  summary={summary}
                  selectedNodeId={activeNodeId}
                  selectedNodeIds={selectedNodeIds}
                  selectedScenarioIds={selected}
                  results={results}
                  datasetRevision={datasetRevision}
                  onNodeClick={handleNodeClick}
                />
                {loading && (
                  <div className="absolute inset-0 z-[1000] flex items-center justify-center bg-blue-50/75 backdrop-blur-sm">
                    <div className="flex items-center gap-2 rounded-full border border-slate-300/70 bg-surface/90 px-4 py-2 text-sm font-medium text-slate-700">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                      {t("지도 데이터 로딩 중", "Loading map data")}
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="flex h-[520px] min-h-0 flex-col overflow-hidden rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)] xl:h-[60vh]">
              <SelectedIntersectionPanel
                selectedNodeIds={selectedNodeIds}
                nodeNamesById={nodeNamesById}
                maxSelectedNodes={MAX_SELECTED_NODES}
                onRemove={handleRemoveNode}
              />

              <div className="mt-4 min-h-0 flex-1 overflow-hidden border-t border-slate-100 pt-4">
                {activeNodeId && hasData ? (
                  <NodeFlowPanel
                    nodeId={activeNodeId}
                    nodeName={activeNodeName}
                    scenarios={scenarios}
                    selected={selected}
                    results={results}
                  />
                ) : (
                  <EmptyNodePanel />
                )}
              </div>
            </div>
          </div>
        </section>
      ) : (
        <DashboardUploadRequiredPanel />
      )}

      {/*
        Network KPI is shown FIRST among the results sections: unlike
        intersection performance and travel time, it never needs an
        intersection to be clicked first, so it is the fastest path to a
        decision-relevant number after the map (previously it sat behind two
        sections that render an empty state until the user picks a node).
      */}
      {hasData && sectionVisible("network-kpi-comparison") && (
        <section id="network-kpi-comparison" className="scroll-mt-6">
          <SectionHeader
            title={t("네트워크 KPI 비교", "Network KPI Comparison")}
            subtitle={t("선택한 시나리오의 주요 네트워크 지표를 상대 막대로 비교합니다.", "Compare key network indicators across selected scenarios using relative bars.")}
          />
          {minRunCount === 1 && (
            <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-800">
              {t(
                "일부 시나리오가 단일 실행(1회) 결과입니다. 무작위 변동(시드 효과) 때문에 값 차이가 시나리오 효과가 아닐 수 있습니다.",
                "Some scenarios are based on a single run. Differences may reflect random seed variation rather than the scenario itself.",
              )}
            </p>
          )}
          <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {KPI_METRICS.map((metric) => (
              <KpiCard
                key={metric.key}
                label={translateKpiMetricLabel(metric.key, t)}
                unit={metric.unit}
                higherIsBetter={metric.higherIsBetter}
                isTotal={metric.isTotal}
                showTechnicalDetail={audience === "expert"}
                values={activeScenarios.map((scenario) => ({
                  scenario,
                  value: results[scenario.id]?.network?.[metric.key] ?? null,
                  stdev: toFiniteNumber(results[scenario.id]?.network?.[`${metric.key}_STDEV`]),
                }))}
              />
            ))}
          </div>
        </section>
      )}

      {hasData && sectionVisible("intersection-performance") && (
        <section id="intersection-performance" className="scroll-mt-6">
          <SectionHeader
            title={t("교차로 성능평가", "Intersection Performance Evaluation")}
            subtitle={t(
              "선택 교차로의 방향별 지체와 교차로별 대기행렬, 최악 접근로 지체, 통행량을 비교합니다. 각 막대는 해당 교차로에서 시나리오 중 최악값이며, 막대 아래 작은 글씨가 그 시나리오입니다.",
              "Compare directional delay plus intersection-level queue length, worst approach delay, and volume. Each bar is the worst value across scenarios for that intersection — the small text under it names which scenario.",
            )}
          />
          <IntersectionPerformanceGrid cards={intersectionCards} />
        </section>
      )}

      {hasData && sectionVisible("travel-time-comparison") && (
        <section id="travel-time-comparison" className="scroll-mt-6">
          <SectionHeader
            title={t("통행시간 비교분석", "Travel Time Comparison")}
            subtitle={t("Vissim 통행시간 결과를 시나리오별 선형 추세로 비교합니다.", "Compare Vissim travel-time outputs as scenario-level trend lines.")}
          />
          <TravelTimeComparison chart={travelTimeSeries} />
        </section>
      )}

      {hasData && sectionVisible("overall-results-analysis") && (
        <section id="overall-results-analysis" className="scroll-mt-6">
          <SectionHeader
            title={t("종합 결과 분석", "Overall Results Analysis")}
            subtitle={t("결과 해석과 보고서 생성만 간단하게 제공합니다.", "A short interpretation of the results, plus report generation.")}
          />
          <OverallResultsSection
            summary={summary}
            activeScenarios={activeScenarios}
            intersectionCards={intersectionCards}
            selectedNodeIds={selectedNodeIds}
            nodeNamesById={nodeNamesById}
            results={results}
            realCounts={realCounts}
            narrative={overallNarrative}
          />
        </section>
      )}

      {realCounts.length > 0 && hasData && sectionVisible("field-count-validation") && (
        <section id="field-count-validation" className="scroll-mt-6">
          <SectionHeader
            title={t("현장 계수 검증", "Validation Against Field Counts")}
            subtitle={t(
              "선택한 시나리오의 노드별 오차율과 GEH 값을 확인합니다. GEH = √(2×(모델-관측)² / (모델+관측)), 5 미만이면 우수, 10 이상이면 재검토가 필요합니다.",
              "Review per-node percentage error and GEH values for selected scenarios. GEH = √(2×(model−observed)² / (model+observed)); under 5 is good, 10 or over needs review.",
            )}
          />
          <ValidationMatrix
            realCounts={realCounts}
            scenarios={activeScenarios}
            summary={summary}
            onNodeClick={handleNodeClick}
            selectedNodeId={activeNodeId}
          />
        </section>
      )}
    </div>
  );
}

function SectionJumpIndex({
  showValidation,
  sectionVisible,
}: {
  showValidation: boolean;
  sectionVisible: (sectionId: string) => boolean;
}) {
  const { t } = useLanguage();
  const items = [
    { id: "network-kpi-comparison", href: "#network-kpi-comparison", label: t("KPI", "KPI") },
    { id: "intersection-performance", href: "#intersection-performance", label: t("성능평가", "Performance") },
    { id: "travel-time-comparison", href: "#travel-time-comparison", label: t("통행시간", "Travel Time") },
    { id: "overall-results-analysis", href: "#overall-results-analysis", label: t("종합결과", "Overall") },
    ...(showValidation ? [{ id: "field-count-validation", href: "#field-count-validation", label: t("현장검증", "Validation") }] : []),
    // A section hidden by the current AI focus-mode answer has no anchor on
    // the page right now, so its jump link is filtered out instead of
    // scrolling to nothing when clicked.
  ].filter((item) => sectionVisible(item.id));

  return (
    <nav
      aria-label={t("분석 섹션 바로가기", "Analysis section shortcuts")}
      className="group absolute left-0 top-24 z-[950] h-11 w-10 -translate-x-3"
    >
      <button
        type="button"
        className="absolute left-0 top-0 flex h-11 w-10 items-center justify-center rounded-r-full border border-l-0 border-slate-300 bg-surface/95 text-blue-700 shadow-[0_8px_18px_rgba(15,23,42,0.18)] backdrop-blur transition hover:bg-slate-900 hover:text-ink focus:outline-none focus:ring-2 focus:ring-accent/45"
        aria-label={t("분석 목차 열기", "Open analysis sections")}
      >
        <span className="flex flex-col gap-1">
          <span className="h-0.5 w-4 rounded-full bg-current" />
          <span className="h-0.5 w-4 rounded-full bg-current" />
          <span className="h-0.5 w-4 rounded-full bg-current" />
        </span>
      </button>

      <div className="pointer-events-none absolute right-[42px] top-0 w-[188px] -translate-x-2 opacity-0 transition duration-200 ease-out group-hover:pointer-events-auto group-hover:translate-x-0 group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:translate-x-0 group-focus-within:opacity-100">
        <div className="rounded-xl border border-slate-300/80 bg-surface/96 p-2.5 shadow-[0_16px_34px_rgba(15,23,42,0.18)] backdrop-blur-md">
          <div className="mb-2 flex items-center justify-between border-b border-slate-200/80 pb-2">
            <div>
              <p className="text-[10px] font-semibold text-slate-500">{t("분석 목차", "Sections")}</p>
              <p className="mt-0.5 text-[11px] font-bold text-slate-900">{t("빠른 이동", "Quick Jump")}</p>
            </div>
            <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-bold text-accent">
              {items.length}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            {items.map((item, index) => (
              <a
                key={item.href}
                href={item.href}
                className="group/link grid h-8 grid-cols-[1.25rem_1fr] items-center gap-2 rounded-md px-1.5 text-[11px] font-semibold text-slate-700 transition hover:bg-blue-100 hover:text-blue-700 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-accent/45"
              >
                <span className="flex h-5 w-5 items-center justify-center rounded-md border border-slate-200 bg-surface text-[10px] font-bold text-slate-500 shadow-sm transition group-hover/link:border-accent group-hover/link:bg-accent group-hover/link:text-white">
                  {index + 1}
                </span>
                <span className="truncate">{item.label}</span>
              </a>
            ))}
          </div>
        </div>
      </div>
    </nav>
  );
}

// Triggers the real file picker owned by the (already-rendered elsewhere)
// VissimDatasetBanner instance. Not a prop callback: <DashboardDatasetGate />
// and <DashboardUploadRequiredPanel /> are called with no props by contract
// (see dashboard-contract.test.ts), so the empty-state gate reaches the
// actual upload control this way instead of only describing it in prose.
function openDatasetFilePicker() {
  document.querySelector<HTMLInputElement>('input[type="file"][accept=".inpx,.zip"]')?.click();
}

function DashboardDatasetGate() {
  const { t } = useLanguage();

  return (
    <div className="rounded-xl border border-dashed border-amber-300 bg-amber-50 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-amber-700">
            {t("대시보드 연결 대기", "Dashboard Waiting for Dataset")}
          </p>
          <p className="mt-1 text-sm text-amber-800">
            {t(
              "VISSIM 파일 또는 프로젝트 ZIP을 업로드하면 시나리오 선택과 분석 지도가 활성화됩니다.",
              "Upload a VISSIM file or project ZIP to enable scenario selection and the analysis map.",
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={openDatasetFilePicker}
            className="rounded-full border border-transparent bg-amber-500 px-3 py-1.5 text-xs font-bold text-ink transition hover:bg-amber-600"
          >
            {t("지금 업로드", "Upload now")}
          </button>
          <span className="rounded-full border border-amber-300 bg-surface px-3 py-1 text-xs font-bold text-amber-600">
            INPX / ZIP
          </span>
        </div>
      </div>
    </div>
  );
}

function DashboardUploadRequiredPanel() {
  const { t } = useLanguage();

  return (
    <section className="rounded-xl border border-dashed border-slate-300 bg-surface/78 px-6 py-12 text-center shadow-sm">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-amber-300 bg-amber-50 text-sm font-black text-amber-600">
        DATA
      </div>
      <h2 className="mt-4 text-[15px] font-semibold text-white">
        {t("업로드한 VISSIM 데이터셋이 필요합니다", "Uploaded VISSIM Dataset Required")}
      </h2>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
        {t(
          "기본 샘플 데이터 대신 사용자가 올린 네트워크와 시나리오 결과를 기준으로 대시보드를 연결합니다.",
          "The dashboard now connects to the network and scenario outputs from the dataset you upload instead of the default sample data.",
        )}
      </p>
      <button
        type="button"
        onClick={openDatasetFilePicker}
        className="ui-button-primary mt-4 inline-flex items-center px-4 py-2 text-sm"
      >
        {t("VISSIM 파일 업로드", "Upload a VISSIM file")}
      </button>
    </section>
  );
}

function SelectedIntersectionPanel({
  selectedNodeIds,
  nodeNamesById,
  maxSelectedNodes,
  onRemove,
}: {
  selectedNodeIds: string[];
  nodeNamesById: Record<string, string>;
  maxSelectedNodes: number;
  onRemove: (nodeId: string) => void;
}) {
  const { t } = useLanguage();

  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            {t("교차로 비교분석", "Intersection Comparison")}
          </p>
          <h2 className="mt-0.5 text-base font-bold text-slate-900">
            {t("선택된 교차로", "Selected Intersections")}
          </h2>
        </div>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-600">
          {selectedNodeIds.length}/{maxSelectedNodes}
        </span>
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
        {t(
          "지도에서 교차로를 클릭하면 비교 목록에 추가됩니다. 다시 클릭하면 제거됩니다.",
          "Click intersections on the map to add them to the comparison list. Click again to remove.",
        )}
      </p>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {selectedNodeIds.length === 0 && (
          <span className="rounded-full border border-dashed border-slate-300 bg-surface px-2.5 py-1 text-[11px] text-slate-500">
            {t("선택 없음", "None selected")}
          </span>
        )}

        {selectedNodeIds.map((nodeId, index) => (
          <button
            key={nodeId}
            type="button"
            onClick={() => onRemove(nodeId)}
            className="rounded-full border border-slate-300 bg-surface px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
          >
            {index + 1}. {nodeNamesById[nodeId] ?? `Node ${nodeId}`} x
          </button>
        ))}
      </div>
    </div>
  );
}

function IntersectionPerformanceGrid({ cards }: { cards: IntersectionPerformanceCard[] }) {
  const { t } = useLanguage();

  if (!cards.length) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-surface/85 px-6 py-10 text-center text-sm text-slate-500">
        {t("지도에서 교차로를 선택하면 성능평가 카드가 표시됩니다.", "Select intersections on the map to show performance cards.")}
      </div>
    );
  }

  const queueValues = cards.map((card) => ({
    nodeId: card.nodeId,
    nodeName: card.nodeName,
    value: card.maxQueueLength,
    scenarioName: card.maxQueueScenarioName,
  }));
  const worstDelayValues = cards.map((card) => ({
    nodeId: card.nodeId,
    nodeName: card.nodeName,
    value: card.worstApproachDelay,
    scenarioName: card.worstDelayScenarioName,
  }));

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <IntersectionMetricBars
          title={t("최대 대기행렬", "Max Q Length")}
          unit="m"
          values={queueValues}
          valueFormatter={(value) => `${formatNumber(value, value < 10 ? 1 : 0)}m`}
        />
        <IntersectionMetricBars
          title={t("최악 접근로 지체", "Worst Approach Delay")}
          unit="s"
          values={worstDelayValues}
          valueFormatter={(value) => `${formatNumber(value, value < 10 ? 1 : 0)}s`}
        />
      </div>

      <VolumeByIntersectionChart cards={cards} />

      <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">
        {cards.map((card, cardIndex) => (
          <article
            key={card.nodeId}
            className="rounded-xl border border-slate-300/70 bg-surface/95 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]"
          >
            <div className="mb-3 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                  {t("교차로", "Intersection")} {cardIndex + 1}
                </p>
                <h3 className="truncate text-base font-bold text-slate-900">{card.nodeName}</h3>
              </div>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                Node {card.nodeId}
              </span>
            </div>
            <DirectionDelayChart values={card.delayByDirection} />
          </article>
        ))}
      </div>
    </div>
  );
}

function IntersectionMetricBars({
  title,
  unit,
  values,
  valueFormatter,
}: {
  title: string;
  unit: string;
  values: IntersectionMetricBarValue[];
  valueFormatter: (value: number) => string;
}) {
  const { t } = useLanguage();
  const maxValue = Math.max(...values.map((item) => item.value ?? 0), 1);

  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/95 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold text-slate-900">{title}</h3>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">
          {unit}
        </span>
      </div>

      <div className="mt-4 space-y-3">
        {values.map((item, index) => {
          const width = item.value != null ? Math.max(7, (item.value / maxValue) * 100) : 0;
          const color = INTERSECTION_COLORS[index % INTERSECTION_COLORS.length];

          return (
            <div key={item.nodeId} className="grid grid-cols-[7.2rem_1fr_4.8rem] items-center gap-3">
              <span className="truncate text-[11px] font-semibold text-slate-700">{item.nodeName}</span>
              <span className="h-4 overflow-hidden rounded-full bg-slate-100 shadow-inner">
                {item.value != null && (
                  <span className="block h-full rounded-full" style={{ width: `${width}%`, backgroundColor: color }} />
                )}
              </span>
              <span className="text-right text-[11px] font-bold tabular-nums text-slate-900">
                {item.value != null ? valueFormatter(item.value) : "-"}
              </span>
              {item.scenarioName && (
                <span className="col-start-2 -mt-2 truncate text-[10px] text-slate-500">{item.scenarioName}</span>
              )}
            </div>
          );
        })}
      </div>

      {!values.some((item) => item.value != null) && (
        <p className="mt-3 text-[11px] text-slate-500">
          {t("표시할 교차로 데이터가 없습니다.", "No intersection data is available.")}
        </p>
      )}
    </div>
  );
}

function DirectionDelayChart({
  values,
}: {
  values: Array<{ direction: DirectionKey; value: number | null; los: LosGrade | null }>;
}) {
  const { t } = useLanguage();
  const maxValue = Math.max(...values.map((item) => item.value ?? 0), 1);

  return (
    <div className="min-h-[190px] rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-[12px] font-bold text-slate-800">{t("방향별 지체", "Delay by Direction")}</p>
        <span
          className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700"
          title={t(
            "교차로 평균 지체를 방향별 통행량 비율로 배분한 추정값입니다. 실제 방향별 측정값이 아닙니다.",
            "Estimated by distributing the intersection average delay across directions by volume share — not a per-direction measurement.",
          )}
        >
          {t("추정 · LOS", "Est. · LOS")}
        </span>
      </div>

      <div className="mt-4 grid h-32 grid-cols-4 items-end gap-2 border-b border-slate-200 pb-2">
        {values.map(({ direction, value, los }) => {
          const height = value != null ? Math.max(10, (value / maxValue) * 100) : 0;
          const color = los ? LOS_COLORS[los] : "#45443f";

          return (
            <div key={direction} className="flex h-full flex-col items-center justify-end gap-1">
              <span className="text-[10px] font-bold tabular-nums text-slate-800">
                {value != null ? formatNumber(value, value < 10 ? 1 : 0) : "-"}
              </span>
              <div className="flex h-[86px] w-full max-w-[42px] items-end rounded-md bg-surface/80 px-1 py-1 shadow-inner">
                {value != null && (
                  <span className="block w-full rounded-md" style={{ height: `${height}%`, backgroundColor: color }} />
                )}
              </div>
              <span className="text-[10px] font-semibold text-slate-600">
                {t(DIRECTION_LABELS[direction].shortKo, DIRECTION_LABELS[direction].shortEn)}
                {los ? ` / ${los}` : ""}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] text-slate-500">
        {LOS_GRADE_THRESHOLDS.map(({ grade, label }) => (
          <span key={grade} className="inline-flex items-center gap-1" title={t(`지체 ${label}`, `Delay ${label}`)}>
            <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: LOS_COLORS[grade] }} />
            {grade} <span className="text-slate-400">({label})</span>
          </span>
        ))}
      </div>

      <p className="mt-2 text-[9.5px] leading-snug text-amber-700">
        {t(
          "방향별 값은 교차로 평균 지체를 방향별 통행량 비율로 배분한 추정치입니다. 실제 방향별 측정값이 아닙니다.",
          "Per-direction values estimate the intersection-average delay distributed by volume share — not a per-direction measurement.",
        )}
      </p>
    </div>
  );
}

function VolumeByIntersectionChart({ cards }: { cards: IntersectionPerformanceCard[] }) {
  const { t } = useLanguage();
  const maxTotal = Math.max(...cards.map((item) => item.volumeTotal ?? 0), 1);

  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/95 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold text-slate-900">{t("통행량 구성", "Volume Composition")}</h3>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">veh</span>
      </div>

      <div className="mt-4 space-y-3">
        {cards.map((card) => (
          <div key={card.nodeId} className="grid grid-cols-[7.2rem_1fr_4.8rem] items-center gap-3">
            <span className="truncate text-[11px] font-semibold text-slate-700">{card.nodeName}</span>
            <div className="h-5 overflow-hidden rounded-full bg-slate-100 shadow-inner">
              {card.volumeTotal != null && card.volumeTotal > 0 && (
                <div className="flex h-full" style={{ width: `${Math.max(8, (card.volumeTotal / maxTotal) * 100)}%` }}>
                  {DIRECTION_ORDER.map((direction) => {
                    const value = card.volumeDirections[direction] ?? 0;
                    const width = card.volumeTotal && card.volumeTotal > 0 ? (value / card.volumeTotal) * 100 : 0;
                    return (
                      <span
                        key={direction}
                        className="h-full"
                        title={`${t(DIRECTION_LABELS[direction].ko, DIRECTION_LABELS[direction].en)}: ${Math.round(value).toLocaleString()}`}
                        style={{ width: `${width}%`, backgroundColor: DIRECTION_COLORS[direction] }}
                      />
                    );
                  })}
                </div>
              )}
            </div>
            <span className="text-right text-[11px] font-bold tabular-nums text-slate-900">
              {card.volumeTotal != null ? Math.round(card.volumeTotal).toLocaleString() : "-"}
            </span>
          </div>
        ))}
      </div>

      {!cards.some((item) => item.volumeTotal != null) && (
        <p className="mt-3 text-[11px] text-slate-500">
          {t("표시할 통행량 데이터가 없습니다.", "No volume data is available.")}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-1.5 text-[10px] text-slate-500">
        {DIRECTION_ORDER.map((direction) => (
          <span key={direction} className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: DIRECTION_COLORS[direction] }} />
            {t(DIRECTION_LABELS[direction].shortKo, DIRECTION_LABELS[direction].shortEn)}
          </span>
        ))}
      </div>
    </div>
  );
}

function TravelTimeComparison({ chart }: { chart: TravelTimeChart }) {
  const { t } = useLanguage();
  const { series, labels, truncated, useSectionLabels } = chart;
  const populated = series.filter((item) => item.points.some((point) => point.value != null));
  const allValues = populated.flatMap((item) => item.points.map((point) => point.value)).filter((value): value is number => value != null);

  if (!populated.length || !allValues.length || !labels.length) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-surface/85 px-6 py-10 text-center text-sm text-slate-500">
        <p>
          {t(
            "통행시간 측정 결과가 없어 그래프를 표시할 수 없습니다.",
            "No travel-time measurements are available for the chart.",
          )}
        </p>
        <p className="mt-1 text-xs text-slate-400">
          {t(
            "VISSIM 네트워크에 통행시간 측정 구간(Vehicle Travel Time Measurement)이 정의되어 있고 시뮬레이션이 완료됐는지 확인하세요.",
            "Check that the VISSIM network defines Vehicle Travel Time Measurement sections and that the simulation run has finished.",
          )}
        </p>
      </div>
    );
  }

  const minValue = Math.min(...allValues);
  const maxValue = Math.max(...allValues);
  const valueSpan = Math.max(maxValue - minValue, 1);
  const left = 48;
  const right = 24;
  const top = 28;
  const bottom = 58;
  const height = 260;
  // Give every label its own ~46px of horizontal room so tick text doesn't
  // collide, instead of squeezing an arbitrary point count into a fixed width.
  const width = Math.max(760, left + right + labels.length * 46);
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;

  const xForIndex = (index: number) => left + (index / Math.max(labels.length - 1, 1)) * plotWidth;
  const yForValue = (value: number) => top + ((maxValue - value) / valueSpan) * plotHeight;

  // Break the line at gaps (a scenario missing a label its peers have)
  // instead of interpolating across missing measurements.
  function buildSegments(points: TravelTimeSeries["points"]): Array<Array<[number, number]>> {
    const segments: Array<Array<[number, number]>> = [];
    let current: Array<[number, number]> = [];
    points.forEach((point, index) => {
      if (point.value == null) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      current.push([xForIndex(index), yForValue(point.value)]);
    });
    if (current.length) segments.push(current);
    return segments;
  }

  // Thin tick labels out when there isn't room to show every one legibly.
  const tickStride = labels.length > 14 ? Math.ceil(labels.length / 14) : 1;

  return (
    <div className="mt-4 rounded-xl border border-slate-300/70 bg-surface/92 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      {truncated && (
        <p className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-800">
          {t(
            `표시 구간이 많아 처음 ${TRAVEL_TIME_MAX_POINTS}개만 표시합니다.`,
            `Showing the first ${TRAVEL_TIME_MAX_POINTS} of more available sections/intervals.`,
          )}
        </p>
      )}
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} style={{ minWidth: `${width}px` }}>
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const y = top + ratio * plotHeight;
            const labelValue = maxValue - ratio * valueSpan;
            return (
              <g key={ratio}>
                <line x1={left} x2={width - right} y1={y} y2={y} stroke="#33332f" strokeDasharray="4 5" />
                <text x={left - 8} y={y + 4} textAnchor="end" fontSize="10" fill="#898781">
                  {formatNumber(labelValue, 0)}
                </text>
              </g>
            );
          })}

          <line x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} stroke="#45443f" />
          <line x1={left} x2={left} y1={top} y2={height - bottom} stroke="#45443f" />

          {labels.map((label, index) =>
            index % tickStride === 0 ? (
              <text
                key={label}
                x={xForIndex(index)}
                y={height - bottom + 16}
                textAnchor="end"
                fontSize="9.5"
                fill="#898781"
                transform={`rotate(-38 ${xForIndex(index)} ${height - bottom + 16})`}
              >
                {label.length > 12 ? `${label.slice(0, 11)}…` : label}
              </text>
            ) : null,
          )}

          {populated.map((item, seriesIndex) => {
            const color = getScenarioDisplayColor(item.scenario, seriesIndex);
            const segments = buildSegments(item.points);
            return (
              <g key={item.scenario.id}>
                {segments.map((segment, segmentIndex) => (
                  <polyline
                    key={segmentIndex}
                    points={segment.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}
                    fill="none"
                    stroke={color}
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                ))}
                {item.points.map((point, pointIndex) =>
                  point.value == null ? null : (
                    <circle
                      key={`${item.scenario.id}-${pointIndex}`}
                      cx={xForIndex(pointIndex)}
                      cy={yForValue(point.value)}
                      r="3.5"
                      fill={color}
                    />
                  ),
                )}
              </g>
            );
          })}

          <text x={left} y={20} fontSize="10" fill="#898781">
            {useSectionLabels
              ? t("측정 구간별 통행시간", "Travel time by measurement section")
              : t("시간 간격별 통행시간", "Travel time by time interval")}
          </text>
          <text x={left - 34} y={top - 8} fontSize="10" fill="#898781">
            sec
          </text>
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {populated.map((item, index) => (
          <span key={item.scenario.id} className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-600">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: getScenarioDisplayColor(item.scenario, index) }} />
            {item.scenario.name}
          </span>
        ))}
      </div>
    </div>
  );
}

function OverallResultsSection({
  summary,
  activeScenarios,
  intersectionCards,
  selectedNodeIds,
  nodeNamesById,
  results,
  realCounts,
  narrative,
}: {
  summary: CompareSummaryResponse | null;
  activeScenarios: ScenarioMeta[];
  intersectionCards: IntersectionPerformanceCard[];
  selectedNodeIds: string[];
  nodeNamesById: Record<string, string>;
  results: Record<string, ScenarioResults>;
  realCounts: NodeRealCounts[];
  narrative: string;
}) {
  const { t } = useLanguage();
  const [reportMarkdown, setReportMarkdown] = useState("");
  const [reportGeneratedAt, setReportGeneratedAt] = useState<Date | null>(null);
  const canGenerateReport = activeScenarios.length > 0 && intersectionCards.length > 0;

  useEffect(() => {
    let cancelled = false;
    Promise.resolve().then(() => {
      if (cancelled) return;
      setReportMarkdown("");
      setReportGeneratedAt(null);
    });
    return () => {
      cancelled = true;
    };
  }, [activeScenarios, intersectionCards, results, summary, realCounts, narrative]);

  const handleGenerateReport = useCallback(() => {
    const generatedAt = new Date();
    const markdown = buildCalibrationReportMarkdown({
      scenarios: activeScenarios,
      intersectionCards,
      selectedNodeIds,
      nodeNamesById,
      results,
      summary,
      realCounts,
      generatedAt,
      comparisonNarrative: narrative,
    });

    setReportMarkdown(markdown);
    setReportGeneratedAt(generatedAt);
  }, [activeScenarios, intersectionCards, narrative, nodeNamesById, realCounts, results, selectedNodeIds, summary]);

  const handleDownloadReport = useCallback(() => {
    if (!reportMarkdown) return;

    const blob = new Blob([reportMarkdown], { type: "text/markdown;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const stamp = formatDateForFilename(reportGeneratedAt ?? new Date());
    anchor.href = href;
    anchor.download = `base-model-calibration-validation-${stamp}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(href);
  }, [reportGeneratedAt, reportMarkdown]);

  const handleDownloadWordReport = useCallback(() => {
    if (!reportMarkdown) return;

    const html = buildWordDocumentHtml(reportMarkdown);
    const blob = new Blob([html], { type: "application/msword;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const stamp = formatDateForFilename(reportGeneratedAt ?? new Date());
    anchor.href = href;
    anchor.download = `base-model-calibration-validation-${stamp}.doc`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(href);
  }, [reportGeneratedAt, reportMarkdown]);

  return (
    <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <section className="rounded-xl border border-slate-300/70 bg-surface/95 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm font-bold text-slate-900">{t("결과 해석", "Result Interpretation")}</p>
            <p className="mt-1 text-[11px] text-slate-500">
              {t("현재 선택값 기준으로 최종 판단 문장만 정리합니다.", "A concise decision statement from the current selection.")}
            </p>
          </div>
          <span className="rounded-full bg-blue-100 px-2.5 py-1 text-[10px] font-bold text-accent">
            {t("핵심 요약", "Summary")}
          </span>
        </div>

        <p className="mt-4 max-w-3xl text-[15px] leading-7 text-slate-700">{narrative}</p>

        {(() => {
          const ranking = summary?.scenario_ranking ?? summary?.scenarioRanking ?? [];
          if (!ranking.length) return null;
          // The narrative above names a "top" scenario by score — show the
          // actual scores and per-scenario reason instead of asking the
          // reader to trust an unverifiable one-line claim.
          return (
            <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50 text-left text-[10px] font-semibold uppercase text-slate-500">
                    <th className="px-3 py-2">{t("순위", "Rank")}</th>
                    <th className="px-3 py-2">{t("시나리오", "Scenario")}</th>
                    <th className="px-3 py-2 text-right">{t("점수", "Score")}</th>
                    <th className="px-3 py-2">{t("근거", "Reason")}</th>
                  </tr>
                </thead>
                <tbody>
                  {[...ranking]
                    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
                    .map((item) => (
                      <tr key={item.scenario_id} className="border-b border-slate-50 last:border-0">
                        <td className="px-3 py-2 font-bold tabular-nums text-slate-700">{item.rank ?? "-"}</td>
                        <td className="px-3 py-2 font-semibold text-slate-800">{item.scenario_name ?? item.scenario_id}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                          {item.score != null ? item.score.toFixed(1) : "-"}
                        </td>
                        <td className="px-3 py-2 text-slate-500">{item.reason ?? "-"}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          );
        })()}

        <div className="mt-5 flex flex-wrap gap-2 text-[11px] font-semibold text-slate-600">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
            {t("시나리오", "Scenarios")} {activeScenarios.length}
          </span>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
            {t("선택 교차로", "Intersections")} {intersectionCards.length}
          </span>
          {intersectionCards[0] && (
            <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
              {t("대표 교차로", "Primary")} {intersectionCards[0].nodeName}
            </span>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-slate-300/70 bg-surface/95 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-bold text-slate-900">{t("보고서 생성", "Report Generator")}</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              {t(
                "선택된 시나리오와 교차로 결과로 보고서 초안을 만듭니다.",
                "Create a draft report from the selected scenarios and intersections.",
              )}
            </p>
          </div>
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-bold text-slate-600">
            {intersectionCards.length}
          </span>
        </div>

        <div className="mt-4 grid gap-2">
          <button
            type="button"
            onClick={handleGenerateReport}
            disabled={!canGenerateReport}
            className={`rounded-lg border px-3 py-2.5 text-xs font-semibold transition ${
              canGenerateReport
                ? "border-accent bg-accent text-white shadow-[0_8px_16px_rgba(47,123,200,0.22)] hover:bg-accent-deep"
                : "cursor-not-allowed border-slate-200 bg-slate-100 text-slate-400"
            }`}
          >
            {t("보고서 생성하기", "Generate Report")}
          </button>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={handleDownloadReport}
              disabled={!reportMarkdown}
              className={`rounded-lg border px-3 py-2 text-xs font-semibold transition ${
                reportMarkdown
                  ? "border-slate-300 bg-surface text-slate-700 hover:border-slate-400"
                  : "cursor-not-allowed border-slate-200 bg-slate-100 text-slate-400"
              }`}
            >
              {t("텍스트 (.md)", "Text (.md)")}
            </button>
            <button
              type="button"
              onClick={handleDownloadWordReport}
              disabled={!reportMarkdown}
              title={t(
                "Word에서 열 때 '파일 형식이 확장자와 다릅니다'라는 확인 창이 뜰 수 있습니다 — 정상이며 그대로 여세요.",
                "Word may show a \"file format doesn't match extension\" prompt when opening this — that's expected, open it anyway.",
              )}
              className={`rounded-lg border px-3 py-2 text-xs font-semibold transition ${
                reportMarkdown
                  ? "border-slate-300 bg-surface text-slate-700 hover:border-slate-400"
                  : "cursor-not-allowed border-slate-200 bg-slate-100 text-slate-400"
              }`}
            >
              {t("Word (.doc)", "Word (.doc)")}
            </button>
          </div>
        </div>

        <p className="mt-3 text-[10px] leading-relaxed text-slate-500">
          {reportGeneratedAt
            ? t(
                `${formatDisplayDateTime(reportGeneratedAt, "ko")} 생성됨. Word 파일을 열 때 형식 확인 창이 뜨면 그대로 열면 됩니다.`,
                `Generated ${formatDisplayDateTime(reportGeneratedAt, "en")}. If Word shows a format-mismatch prompt, open it anyway.`,
                `${formatDisplayDateTime(reportGeneratedAt, "ja")} 生成済み`,
              )
            : canGenerateReport
              ? t("먼저 생성한 뒤 원하는 형식으로 내려받으세요.", "Generate first, then download the preferred format.")
              : t("교차로를 1개 이상 선택하면 생성할 수 있습니다.", "Select at least one intersection to generate a report.")}
        </p>
        <Link
          href="/simulation"
          className="mt-3 inline-block text-[10px] font-semibold text-slate-400 underline decoration-dotted underline-offset-2 hover:text-slate-600"
        >
          {t("고급: 시뮬레이션 실행기 열기 →", "Advanced: open the Simulation Runner →")}
        </Link>
      </section>
    </div>
  );
}

function KpiCard({
  label,
  unit,
  higherIsBetter,
  isTotal,
  showTechnicalDetail,
  values,
}: {
  label: string;
  unit: string;
  higherIsBetter: boolean;
  isTotal: boolean;
  /** "전문가" mode: show ±stdev, noise caveats, and range/diff detail rows.
   * "쉬운 설명" keeps the same numbers but drops the statistical fine print. */
  showTechnicalDetail: boolean;
  values: { scenario: ScenarioMeta; value: number | null; stdev: number | null }[];
}) {
  const { t } = useLanguage();
  const present = values.filter((item) => item.value != null);
  if (!present.length) return null;

  const nums = present.map((item) => item.value as number);
  const minVal = Math.min(...nums);
  const maxVal = Math.max(...nums);
  const spread = maxVal - minVal;
  const bestVal = higherIsBetter ? maxVal : minVal;
  // "Best" is only meaningful if the gap to the runner-up exceeds run-to-run
  // noise; without that check a single-seed difference smaller than its own
  // standard deviation was labeled "Best" as if it were a real effect.
  const sortedNums = [...nums].sort((a, b) => (higherIsBetter ? b - a : a - b));
  const runnerUpGap = sortedNums.length > 1 ? Math.abs(sortedNums[0] - sortedNums[1]) : null;
  const bestEntryStdev = present.find((item) => item.value === bestVal)?.stdev ?? null;
  const bestIsWithinNoise =
    runnerUpGap != null && bestEntryStdev != null && runnerUpGap < bestEntryStdev;

  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      <div className="mb-4 flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-800">{label}</h3>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
          {unit}
        </span>
      </div>
      <div className="overflow-x-auto pb-1">
        <div className="flex min-w-max items-end gap-3">
        {values.map(({ scenario, value, stdev }, index) => {
          if (value == null) return null;
          const scenarioColor = getScenarioDisplayColor(scenario, index);

          const pct = maxVal > 0 ? (value / maxVal) * 100 : 100;

          // A minimum total is NOT "best" — a lower network-wide total can
          // simply mean fewer vehicles were served — so the Best tag is
          // suppressed on total metrics (the caveat line below explains why).
          const isBest = value === bestVal && !isTotal;
          const displayVal =
            value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value.toFixed(value < 10 ? 2 : 1);

          return (
            <div key={scenario.id} className="flex w-[76px] flex-col items-center gap-1.5">
              <div className="flex h-9 flex-col items-center justify-end gap-0.5">
                {isBest && (
                  <span
                    className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-emerald-600"
                    title={bestIsWithinNoise ? t("2위와의 차이가 표준편차보다 작습니다.", "The gap to the runner-up is smaller than the standard deviation.") : undefined}
                  >
                    {t("최적", "Best", "最良")}{showTechnicalDetail && bestIsWithinNoise ? "*" : ""}
                  </span>
                )}
                <div className="text-[11px] font-semibold tabular-nums text-slate-900">
                  {displayVal}
                  {showTechnicalDetail && stdev != null && (
                    <span className="ml-0.5 text-slate-400">±{stdev >= 10 ? Math.round(stdev) : stdev.toFixed(1)}</span>
                  )}
                </div>
              </div>
              <div className="relative flex h-24 w-7 items-end overflow-hidden rounded-md border border-slate-200 bg-slate-100/80">
                <div
                  className="w-full rounded-md transition-all duration-500"
                  style={{ height: `${Math.max(pct, 8)}%`, backgroundColor: scenarioColor }}
                />
              </div>
              <div className="flex w-full items-center justify-center gap-1.5 text-[10px]">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: scenarioColor }}
                />
                <span className="max-w-[56px] truncate text-slate-700">{scenario.name}</span>
              </div>
            </div>
          );
        })}
        </div>
      </div>
      {showTechnicalDetail && bestIsWithinNoise && (
        <p className="mt-2 text-[10px] text-amber-700">
          * {t("최상위 값과 2위의 차이가 실행 간 변동(표준편차)보다 작아 확정적 차이로 보기 어렵습니다.", "The gap between the top value and the runner-up is smaller than run-to-run variation, so it may not be a real difference.")}
        </p>
      )}
      {isTotal && (
        <p className="mt-1 text-[10px] text-slate-400">
          {t("네트워크 전체 합계이며 차량 수에 비례합니다 — 값이 작다고 항상 더 좋은 것은 아닙니다.", "A network-wide total that scales with vehicle count — a lower value isn't always a better outcome.")}
        </p>
      )}
      {spread > 0 && (
        <p className="mt-1 text-[10px] text-slate-500">
          {t("범위", "Range")} {formatNumber(minVal, 1)} ~ {formatNumber(maxVal, 1)} ({t("차이", "diff")} {formatNumber(spread, 1)})
        </p>
      )}
    </div>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div>
      <h2 className="text-[15px] font-semibold text-white">{title}</h2>
      <p className="mt-0.5 text-sm text-slate-600">{subtitle}</p>
    </div>
  );
}

function DashboardScenarioPicker({
  scenarios,
  selected,
  onChange,
}: {
  scenarios: ScenarioMeta[];
  selected: string[];
  onChange: (selected: string[]) => void;
}) {
  const { t } = useLanguage();

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);
  };

  return (
    <div className="rounded-xl border border-slate-200/90 bg-slate-50/70 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-slate-800">{t("시나리오 비교 세트", "Scenario Comparison Set")}</p>
          <p className="text-[11px] text-slate-500">
            {t(
              "KPI, 지도, 교차로 비교에 포함할 시나리오를 선택합니다. 새로고침하면 최대 4개까지만 자동으로 다시 선택됩니다.",
              "Choose scenarios to include in KPI, map, and node comparisons. On reload, only up to 4 are automatically reselected.",
            )}
          </p>
        </div>
        <span className="rounded-full bg-surface px-2.5 py-1 text-[11px] font-semibold text-slate-600 shadow-sm">
          {selected.length}/{scenarios.length} {t("선택", "selected")}
        </span>
      </div>

      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {scenarios.map((scenario, index) => {
          const active = selected.includes(scenario.id);
          const scenarioColor = getScenarioDisplayColor(scenario, index);
          const order = selected.indexOf(scenario.id) + 1;
          return (
            <button
              key={scenario.id}
              onClick={() => toggle(scenario.id)}
              className={`group relative overflow-hidden rounded-xl border px-3 py-2 text-left transition-all duration-200 ${
                active
                  ? "border-slate-300 bg-surface shadow-[0_10px_22px_rgba(15,23,42,0.12)]"
                  : "border-slate-200 bg-surface/70 opacity-75 hover:-translate-y-0.5 hover:border-slate-300 hover:opacity-100"
              }`}
            >
              <span
                className="absolute inset-y-0 left-0 w-1"
                style={{ backgroundColor: active ? scenarioColor : "#45443f" }}
              />
              <div className="flex items-center justify-between gap-2 pl-1">
                <div className="min-w-0">
                  <p className={`truncate text-sm font-bold ${active ? "text-slate-900" : "text-slate-500"}`}>
                    {scenario.name}
                  </p>
                  <p className="mt-0.5 text-[10px] text-slate-500">
                    {active ? t("비교 포함", "Included") : t("클릭하여 포함", "Click to include")}
                  </p>
                </div>
                <span
                  className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                    active ? "text-white" : "bg-slate-100 text-slate-400"
                  }`}
                  style={active ? { backgroundColor: scenarioColor } : {}}
                >
                  {active ? order : "+"}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function translateKpiMetricLabel(
  key: (typeof KPI_METRICS)[number]["key"],
  t: (ko: string, en: string, ja?: string) => string,
) {
  switch (key) {
    case "DELAYAVG":
      return t("평균 지체", "Average Delay");
    case "SPEEDAVG":
      return t("평균 속도", "Average Speed");
    case "TRAVTMTOT":
      return t("총 주행시간", "Total Travel Time");
    case "DELAYTOT":
      return t("총 지체", "Total Delay");
    case "VEHARR":
      return t("도착 차량수", "Arrived Vehicles");
    case "STOPSAVG":
      return t("평균 정지횟수", "Average Stops");
  }
}

// The scenario the validation scaling is pegged to. Kept as a named constant
// so the internal id never appears in user-facing copy — resolveBaseScenarioName
// turns it into the scenario's display name where the scenario is loaded.
const BASE_SCENARIO_ID = "S000001";

function resolveBaseScenarioName(scenarios: ScenarioMeta[], t: (ko: string, en: string, ja?: string) => string): string {
  return scenarios.find((scenario) => scenario.id === BASE_SCENARIO_ID)?.name ?? t("기준 시나리오", "the base scenario", "基準シナリオ");
}

interface ScaledVolumeResult {
  value: number | null;
  rawValue: number | null;
  /** Non-null when a base-scenario scaling factor was applied (excelBasam / dbBasam). */
  scaleFactor: number | null;
}

// The ONE place that turns a raw simulated node volume into the "modelled"
// number used for validation. Both the on-screen matrix (ValidationMatrix)
// and the exported report (buildReportValidationRows) call this so the two
// surfaces can never silently disagree about a GEH/percent-error figure.
function computeScaledSimVolume(
  nodeId: string,
  scenarioId: string,
  summary: CompareSummaryResponse | null,
  realCounts: NodeRealCounts[],
): ScaledVolumeResult {
  const node = summary?.top_congested_nodes?.find((item) => String(item.node_id) === String(nodeId));
  const raw = node?.scenario_totals?.[scenarioId] ?? null;
  if (raw == null) return { value: null, rawValue: null, scaleFactor: null };

  const dbBasam = node?.scenario_totals?.[BASE_SCENARIO_ID] ?? null;
  const excelBasam = realCounts.find((row) => String(row.node_id) === String(nodeId))?.total_sim_basam ?? null;

  if (dbBasam != null && dbBasam > 0 && excelBasam != null) {
    const factor = excelBasam / dbBasam;
    return { value: raw * factor, rawValue: raw, scaleFactor: factor };
  }

  return { value: raw, rawValue: raw, scaleFactor: null };
}

const TONE_SEVERITY: Record<ValidationTone, number> = { neutral: 0, success: 1, warning: 2, danger: 3 };

// Cell color must reflect whichever criterion is worse — a node can pass the
// %-error band while failing "GEH < 5", and coloring by %-error alone (the
// previous behavior) hid that failure in a green cell.
function combinedValidationTone(pct: number | null, geh: number | null): ValidationTone {
  const pctTone = errorTone(pct);
  const gehTone_ = gehTone(geh);
  return TONE_SEVERITY[gehTone_] >= TONE_SEVERITY[pctTone] ? gehTone_ : pctTone;
}

function ValidationMatrix({
  realCounts,
  scenarios,
  summary,
  onNodeClick,
  selectedNodeId,
}: {
  realCounts: NodeRealCounts[];
  scenarios: ScenarioMeta[];
  summary: CompareSummaryResponse | null;
  onNodeClick: (nodeId: string, nodeName: string) => void;
  selectedNodeId: string | null;
}) {
  const { t } = useLanguage();

  const hasBasam = scenarios.some((scenario) => scenario.id === BASE_SCENARIO_ID);
  const nodes = [...realCounts].sort((a, b) => parseInt(a.node_id) - parseInt(b.node_id));

  return (
    <div className="mt-4 space-y-2">
      {!hasBasam && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700">
          {t("기준(base) 시나리오가 선택되지 않아 스케일 기반 검증 정확도가 낮아질 수 있습니다.", "The base scenario is not selected, so scaling-based validation may be less accurate.")}
        </p>
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-300/70 bg-surface/92 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600">{t("교차로", "Node")}</th>
              <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600">{t("관측", "Observed")}</th>
              {scenarios.map((scenario, index) => (
                <th key={scenario.id} className="px-4 py-3 text-center text-xs font-semibold text-slate-600">
                  <span className="flex items-center justify-center gap-1.5">
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ backgroundColor: getScenarioDisplayColor(scenario, index) }}
                    />
                    {scenario.name}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => {
              const obs = node.total_real;
              const isSelected = selectedNodeId === node.node_id;

              return (
                <tr
                  key={node.node_id}
                  onClick={() => onNodeClick(node.node_id, node.node_name)}
                  className={`cursor-pointer border-b border-slate-100 transition-colors last:border-0 hover:bg-slate-100/70 ${
                    isSelected ? "bg-blue-50 hover:bg-blue-50" : ""
                  }`}
                >
                  <td className="px-4 py-3">
                    <span className="font-medium text-slate-800">{node.node_name}</span>
                  </td>

                  <td className="px-4 py-3 text-right font-semibold tabular-nums text-slate-700">
                    {obs != null ? obs.toLocaleString() : "-"}
                  </td>

                  {scenarios.map((scenario) => {
                    // Same computation the exported report uses (computeScaledSimVolume)
                    // so the on-screen cell and the report row never disagree.
                    const scaled = computeScaledSimVolume(node.node_id, scenario.id, summary, realCounts);
                    const sim = scaled.value;
                    const pct = calculatePercentError(sim, obs);
                    const geh = calculateGeh(sim, obs);
                    const tone = combinedValidationTone(pct, geh);

                    return (
                      <td key={scenario.id} className="px-3 py-2 text-center">
                        {sim != null ? (
                          <div className={`inline-block rounded-lg px-2.5 py-1.5 ${toneBadgeClass(tone)}`}>
                            <div className="text-xs font-bold tabular-nums">
                              {pct != null ? `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%` : "-"}
                            </div>
                            <div className="mt-0.5 text-[10px] opacity-80 tabular-nums">
                              {Math.round(sim).toLocaleString()} {t("대", "veh")}
                              {geh != null && <span className="ml-1">GEH {geh.toFixed(1)}</span>}
                            </div>
                            {scaled.scaleFactor != null && (
                              <div
                                className="mt-0.5 text-[9px] font-semibold opacity-70"
                                title={t(
                                  `실측 기준 시나리오 대비 배율(${scaled.scaleFactor.toFixed(3)}x)이 적용된 추정값입니다. 원본 시뮬레이션 값: ${scaled.rawValue != null ? Math.round(scaled.rawValue).toLocaleString() : "-"}대`,
                                  `Estimated: scaled ${scaled.scaleFactor.toFixed(3)}x against the base scenario. Raw simulated value: ${scaled.rawValue != null ? Math.round(scaled.rawValue).toLocaleString() : "-"} veh`,
                                )}
                              >
                                ×{scaled.scaleFactor.toFixed(2)} {t("배율 적용", "scaled")}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-slate-400">-</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-slate-100 bg-slate-50">
              <td colSpan={2 + scenarios.length} className="px-4 py-2">
                <div className="flex flex-wrap items-center gap-4 text-[10px] text-slate-500">
                  <span className="font-medium">{t("오차 구간:", "Error bands:")}</span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-sm bg-emerald-200" /> {t("5% 이내", "within 5%")}
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-sm bg-amber-200" /> {t("5% ~ 15%", "5% to 15%")}
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-sm bg-rose-200" /> {t("15% 초과", "over 15%")}
                  </span>
                  <span className="ml-2">{t("GEH < 5 우수, GEH < 10 허용", "GEH < 5 good, GEH < 10 acceptable")}</span>
                </div>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function buildIntersectionPerformanceCards(
  selectedNodeIds: string[],
  nodeNamesById: Record<string, string>,
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): IntersectionPerformanceCard[] {
  return selectedNodeIds.map((nodeId) => {
    const maxQueue = pickMaxIntersectionMetric(scenarios, results, nodeId, calculateNodeMaxQueue);
    const worstDelay = pickMaxIntersectionMetric(scenarios, results, nodeId, calculateWorstApproachDelay);
    const volume = calculateAverageDirectionalVolume(scenarios, results, nodeId);

    return {
      nodeId,
      nodeName: nodeNamesById[nodeId] ?? `Node ${nodeId}`,
      delayByDirection: calculateDirectionDelays(nodeId, scenarios, results),
      maxQueueLength: maxQueue.value,
      maxQueueScenarioName: maxQueue.scenarioName,
      worstApproachDelay: worstDelay.value,
      worstDelayScenarioName: worstDelay.scenarioName,
      volumeTotal: volume.total,
      volumeDirections: volume.directions,
    };
  });
}

function pickMaxIntersectionMetric(
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
  nodeId: string,
  getter: (result: ScenarioResults, nodeId: string) => number | null,
): { value: number | null; scenarioName: string | null } {
  let bestValue: number | null = null;
  let bestScenarioName: string | null = null;

  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;

    const value = getter(result, nodeId);
    if (value == null) continue;

    if (bestValue == null || value > bestValue) {
      bestValue = value;
      bestScenarioName = scenario.name;
    }
  }

  return { value: bestValue, scenarioName: bestScenarioName };
}

function findNodeDirectionRow(result: ScenarioResults, nodeId: string): NodeDirectionRow | null {
  return result.node_directions?.find((row) => String(row.node_id) === String(nodeId)) ?? null;
}

function calculateDirectionDelays(
  nodeId: string,
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): IntersectionPerformanceCard["delayByDirection"] {
  const buckets: Record<DirectionKey, { sum: number; count: number }> = {
    north: { sum: 0, count: 0 },
    east: { sum: 0, count: 0 },
    south: { sum: 0, count: 0 },
    west: { sum: 0, count: 0 },
  };

  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;

    const averageDelay = calculateNodeAverageDelay(result, nodeId);
    if (averageDelay == null) continue;

    const row = findNodeDirectionRow(result, nodeId);
    if (!row) {
      for (const direction of DIRECTION_ORDER) {
        buckets[direction].sum += averageDelay;
        buckets[direction].count += 1;
      }
      continue;
    }

    const volume = calculateDirectionalVolume(row);
    if (volume.total <= 0) {
      for (const direction of DIRECTION_ORDER) {
        buckets[direction].sum += averageDelay;
        buckets[direction].count += 1;
      }
      continue;
    }

    const maxDirectionVolume = Math.max(...DIRECTION_ORDER.map((direction) => volume.directions[direction]), 1);
    for (const direction of DIRECTION_ORDER) {
      const directionVolume = volume.directions[direction];
      if (directionVolume <= 0) continue;

      const volumeFactor = 0.75 + (directionVolume / maxDirectionVolume) * 0.5;
      buckets[direction].sum += averageDelay * volumeFactor;
      buckets[direction].count += 1;
    }
  }

  return DIRECTION_ORDER.map((direction) => {
    const bucket = buckets[direction];
    const value = bucket.count > 0 ? bucket.sum / bucket.count : null;
    return {
      direction,
      value,
      los: value != null ? resolveLosGrade(value) : null,
    };
  });
}

function calculateAverageDirectionalVolume(
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
  nodeId: string,
): { total: number | null; directions: Record<DirectionKey, number> } {
  const totals: Record<DirectionKey, number> = { north: 0, east: 0, south: 0, west: 0 };
  let count = 0;

  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;

    const row = findNodeDirectionRow(result, nodeId);
    if (!row) continue;

    const volume = calculateDirectionalVolume(row);
    for (const direction of DIRECTION_ORDER) {
      totals[direction] += volume.directions[direction];
    }
    count += 1;
  }

  if (count === 0) {
    return { total: null, directions: totals };
  }

  const directions = Object.fromEntries(
    DIRECTION_ORDER.map((direction) => [direction, totals[direction] / count]),
  ) as Record<DirectionKey, number>;

  return { total: sumDirectionMap(directions), directions };
}

function calculateDirectionalVolume(row: NodeDirectionRow): { total: number; directions: Record<DirectionKey, number> } {
  const incoming = normalizeDirectionTotals(row.incoming);
  const incomingTotal = sumDirectionMap(incoming);
  if (incomingTotal > 0) {
    return { total: incomingTotal, directions: incoming };
  }

  const outgoing = normalizeDirectionTotals(row.outgoing);
  return { total: sumDirectionMap(outgoing), directions: outgoing };
}

function normalizeDirectionTotals(values: Record<string, number> | undefined): Record<DirectionKey, number> {
  return {
    north: Math.max(0, toFiniteNumber(values?.north) ?? 0),
    east: Math.max(0, toFiniteNumber(values?.east) ?? 0),
    south: Math.max(0, toFiniteNumber(values?.south) ?? 0),
    west: Math.max(0, toFiniteNumber(values?.west) ?? 0),
  };
}

function resolveLosGrade(delay: number): LosGrade {
  if (delay <= 10) return "A";
  if (delay <= 20) return "B";
  if (delay <= 35) return "C";
  if (delay <= 55) return "D";
  if (delay <= 80) return "E";
  return "F";
}

function calculateNodeAverageDelay(result: ScenarioResults, nodeId: string): number | null {
  const rows = result.movements.filter((row) => objectBelongsToNode(row.OBJECT_ID, nodeId));
  let weightedDelay = 0;
  let weightTotal = 0;

  for (const row of rows) {
    const delay = toFiniteNumber(row["VEHDELAY_MEAN"] ?? row["VEHDELAY"]);
    if (delay == null) continue;

    const vehs = toFiniteNumber(row["VEHS_MEAN"] ?? row["VEHS"]);
    const weight = vehs != null && vehs > 0 ? vehs : 1;
    weightedDelay += delay * weight;
    weightTotal += weight;
  }

  return weightTotal > 0 ? weightedDelay / weightTotal : null;
}

function calculateWorstApproachDelay(result: ScenarioResults, nodeId: string): number | null {
  const delays = result.movements
    .filter((row) => objectBelongsToNode(row.OBJECT_ID, nodeId))
    .map((row) => toFiniteNumber(row["VEHDELAY_MEAN"] ?? row["VEHDELAY"]))
    .filter((value): value is number => value != null);

  return delays.length ? Math.max(...delays) : null;
}

function calculateNodeMaxQueue(result: ScenarioResults, nodeId: string): number | null {
  // "Max Q Length" must only ever report QLENMAX (peak queue). Substituting
  // QLEN (average queue) under that title — as an earlier version did when
  // QLENMAX was absent — mislabels an average as a maximum, so it is NOT
  // done: when no peak-queue value exists for this node we return null and
  // the bar renders "-" rather than a wrong number under the wrong label.
  const maxValues = result.queues
    .filter((row) => objectBelongsToNode(row.OBJECT_ID, nodeId))
    .map((row) => toFiniteNumber(row["QLENMAX_MEAN"] ?? row["QLENMAX"]))
    .filter((value): value is number => value != null);
  return maxValues.length ? Math.max(...maxValues) : null;
}

function sumDirectionMap(values: Record<string, number> | undefined): number {
  return Object.values(values ?? {}).reduce((sum, value) => sum + Number(value ?? 0), 0);
}

function objectBelongsToNode(objectId: unknown, nodeId: string): boolean {
  const text = String(objectId ?? "").trim();
  const normalizedNodeId = String(nodeId).trim();
  return text === normalizedNodeId || text.startsWith(`${normalizedNodeId}-`) || text.startsWith(`${normalizedNodeId}:`);
}

const TRAVEL_TIME_MAX_POINTS = 18;

function buildTravelTimeSeries(
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): TravelTimeChart {
  // The grouping mode (per-section vs per-interval) is decided ONCE from the
  // union of all scenarios' rows, not independently per scenario — otherwise
  // two scenarios could plot different kinds of measurements at the same x
  // position with no way to tell them apart.
  const allObjectIds = new Set<string>();
  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;
    const rows = result.traveltime_interval_class?.length ? result.traveltime_interval_class : result.traveltime ?? [];
    for (const row of rows) {
      const id = String(row["OBJECT_ID"] ?? "").trim();
      if (id) allObjectIds.add(id);
    }
  }
  const useSectionLabels = allObjectIds.size > 1;

  const labelFor = (row: Record<string, unknown>, index: number): string => {
    const sectionLabel = String(row["OBJECT_ID"] ?? "").trim();
    const intervalLabel = String(row["ARG_INTERVAL"] ?? "").trim();
    return useSectionLabels
      ? sectionLabel || intervalLabel || `${index + 1}`
      : intervalLabel || sectionLabel || `${index + 1}`;
  };

  const perScenario = scenarios.map((scenario) => {
    const result = results[scenario.id];
    const grouped = new Map<string, { sum: number; count: number }>();
    if (result) {
      const intervalRows = result.traveltime_interval_class ?? [];
      const rows = intervalRows.length > 0 ? intervalRows : result.traveltime;
      rows.forEach((row, index) => {
        // Plot the real per-interval (or per-section) travel-time averages
        // as-is — an earlier version reshaped these into a synthetic
        // monotonically-rising eased curve, which fabricated the shape.
        const value = toFiniteNumber(row["TRAVTM_MEAN"] ?? row["TRAVTM"]);
        if (value == null) return;
        const label = labelFor(row, index);
        const current = grouped.get(label) ?? { sum: 0, count: 0 };
        current.sum += value;
        current.count += 1;
        grouped.set(label, current);
      });
    }
    return { scenario, grouped };
  });

  // Shared x-domain: every label seen in ANY scenario, so a given x position
  // means the same section/interval for every plotted line.
  const allLabels = new Set<string>();
  for (const { grouped } of perScenario) {
    for (const label of grouped.keys()) allLabels.add(label);
  }
  const sortedLabels = Array.from(allLabels).sort(numericAwareCompare);
  const truncated = sortedLabels.length > TRAVEL_TIME_MAX_POINTS;
  const labels = sortedLabels.slice(0, TRAVEL_TIME_MAX_POINTS);

  const series = perScenario.map(({ scenario, grouped }) => ({
    scenario,
    points: labels.map((label) => {
      const aggregate = grouped.get(label);
      return { label, value: aggregate ? roundChartValue(aggregate.sum / aggregate.count) : null };
    }),
  }));

  return { series, labels, truncated, useSectionLabels };
}

function roundChartValue(value: number): number {
  return Math.round(value * 10) / 10;
}

function buildOverallNarrative(
  summary: CompareSummaryResponse | null,
  activeScenarios: ScenarioMeta[],
  hasData: boolean,
  lang: "ko" | "en" | "ja",
): string {
  const pick = (ko: string, en: string, ja: string) => (lang === "ko" ? ko : lang === "ja" ? ja : en);

  if (!hasData) {
    return pick(
      "시나리오 결과를 불러오면 종합 분석이 표시됩니다.",
      "Overall analysis appears after scenario results are loaded.",
      "シナリオ結果を読み込むと総合分析が表示されます。",
    );
  }

  const ranking = summary?.scenario_ranking ?? summary?.scenarioRanking ?? [];
  const topRank = ranking[0];
  const hotspot = summary?.top_congested_nodes?.[0] ?? null;
  const scenarioName =
    topRank?.scenario_name ??
    activeScenarios.find((scenario) => scenario.id === topRank?.scenario_id)?.name ??
    topRank?.scenario_id ??
    pick("선택 시나리오", "the selected scenarios", "選択シナリオ");

  const hotspotText = hotspot
    ? pick(
        `${hotspot.node_name}이(가) 선택 결과 중 대표 혼잡 교차로입니다.`,
        `${hotspot.node_name} is the representative hotspot among the selected results.`,
        `${hotspot.node_name} が選択結果の中で代表的な混雑交差点です。`,
      )
    : pick(
        "대표 혼잡 교차로는 아직 산정되지 않았습니다.",
        "A representative hotspot has not been calculated yet.",
        "代表的な混雑交差点はまだ算定されていません。",
      );

  if (topRank) {
    return pick(
      `${scenarioName}이(가) 현재 종합 점수 기준 최상위 시나리오입니다. ${hotspotText} 교차로 성능평가, 통행시간 비교, 네트워크 KPI를 함께 확인해 최종 대안을 판단하세요.`,
      `${scenarioName} is currently ranked highest by the overall score. ${hotspotText} Review intersection performance, travel-time comparison, and network KPI together before choosing the final alternative.`,
      `${scenarioName} が現在、総合スコアで最上位のシナリオです。${hotspotText} 交差点性能評価・旅行時間比較・ネットワークKPIを併せて確認し、最終案を判断してください。`,
    );
  }

  return pick(
    `선택된 ${activeScenarios.length}개 시나리오의 비교 결과가 준비되었습니다. ${hotspotText}`,
    `Comparison results are ready for ${activeScenarios.length} selected scenarios. ${hotspotText}`,
    `選択された ${activeScenarios.length} 件のシナリオの比較結果が準備できました。${hotspotText}`,
  );
}

function buildCalibrationReportMarkdown({
  scenarios,
  intersectionCards,
  selectedNodeIds,
  nodeNamesById,
  results,
  summary,
  realCounts,
  generatedAt,
  comparisonNarrative,
}: {
  scenarios: ScenarioMeta[];
  intersectionCards: IntersectionPerformanceCard[];
  selectedNodeIds: string[];
  nodeNamesById: Record<string, string>;
  results: Record<string, ScenarioResults>;
  summary: CompareSummaryResponse | null;
  realCounts: NodeRealCounts[];
  generatedAt: Date;
  comparisonNarrative: string;
}): string {
  const scenarioNames = scenarios.map((scenario) => scenario.name).join(", ") || "Not specified";
  const intersectionNames =
    intersectionCards.map((card) => card.nodeName).join(", ") ||
    selectedNodeIds.map((id) => nodeNamesById[id] ?? `Node ${id}`).join(", ") ||
    "Not specified";
  const validationRows = buildReportValidationRows({ scenarios, intersectionCards, realCounts, results, summary });
  const categoryOnePassRate = calculateGehPassRate(validationRows, 5);
  const categoryTwoPassRate = calculateGehPassRate(validationRows, 10);
  const observedNodes = new Set(validationRows.map((row) => row.nodeId));
  const summaryCards = summary?.summary_cards ?? summary?.summaryCards ?? [];
  const keyInsights = summary?.key_insights ?? summary?.keyInsights ?? [];
  const ranking = summary?.scenario_ranking ?? summary?.scenarioRanking ?? [];
  const hotspotRows = summary?.top_congested_nodes ?? [];

  const lines: string[] = [];
  lines.push("# Traffic Modelling Report: Base Model Calibration & Validation");
  lines.push("");
  lines.push("## Document Control");
  lines.push("");
  lines.push(markdownTable(
    ["Field", "Details"],
    [
      ["Owner", "To be assigned"],
      ["Custodian", "To be assigned"],
      ["Approved", "Not yet reviewed or approved"],
      ["Document Number", "VISSIM-BASE-CALVAL-DRAFT"],
      ["Issue Date", formatReportDate(generatedAt)],
      ["Project", "Vissim Platform Scenario and Intersection Comparison"],
      ["Prepared By", "Auto-generated draft from Prism dashboard data (not reviewed by an engineer)"],
    ],
  ));
  lines.push("");
  lines.push("## Amendments");
  lines.push("");
  lines.push(markdownTable(
    ["Revision Number", "Revision Date", "Description of Key Changes", "Section / Page No."],
    [["0", formatReportDate(generatedAt), "Initial draft generated from platform scenario comparison outputs.", "All"]],
  ));
  lines.push("");
  lines.push("## 1. Purpose");
  lines.push("");
  lines.push(
    "This report documents the base model calibration and validation evidence available from the current Vissim platform comparison results, with emphasis on selected intersection performance, traffic volumes, delay/LOS indicators and GEH-based volume checks where observed counts are available.",
  );
  lines.push("");
  lines.push("## 2. Introduction");
  lines.push("");
  lines.push("### 2.1 Project Details");
  lines.push("");
  lines.push(
    "A Vissim base model has been assessed through the platform comparison dashboard to evaluate current and alternative operational performance across selected intersections. This draft has been generated directly from the scenario result set visible in the platform.",
  );
  lines.push("");
  lines.push("### 2.2 Project Location and Modelling Area");
  lines.push("");
  lines.push(markdownTable(
    ["Item", "Description"],
    [
      ["Project affected area", "To be confirmed by project metadata"],
      ["Area", "To be confirmed"],
      ["Main corridor", "To be confirmed"],
      ["Selected model nodes", intersectionNames],
    ],
  ));
  lines.push("");
  lines.push("### 2.3 Predefined Modelling Scenarios for the Study");
  lines.push("");
  lines.push(markdownTable(
    ["Scenario ID", "Scenario Name", "Result Status"],
    scenarios.map((scenario) => [
      scenario.id,
      scenario.name,
      results[scenario.id] ? "Loaded in platform comparison" : "No result loaded",
    ]),
  ));
  lines.push("");
  lines.push("## 3. Data Collection");
  lines.push("");
  lines.push(
    "**Road Network:** The road network and lane structure were represented in Vissim and reviewed against available digital network geometry used by the platform. In a formal submission, this should be cross-checked against aerial imagery and current layout drawings.",
  );
  lines.push("");
  lines.push(
    `**Traffic Flows:** The current report uses scenario outputs for ${scenarioNames}. Observed traffic count records are ${observedNodes.size > 0 ? `available for ${observedNodes.size} selected node(s)` : "not available for the selected nodes"} through the validation data endpoint.`,
  );
  lines.push("");
  lines.push(
    "**Signal Data:** Signal phasing information should be confirmed against the source Vissim model and any available signal timing records before formal submission.",
  );
  lines.push("");
  lines.push("## 4. Model Calibration & Validation");
  lines.push("");
  lines.push(
    "The tables below summarise the evidence currently available from the platform outputs. Any final submission should be checked against the applicable traffic modelling guidelines and source data package.",
  );
  lines.push("");
  lines.push("### 4.1 Model Calibration");
  lines.push("");
  lines.push(markdownTable(
    ["Calibration Item", "Current Report Value"],
    [
      ["Software Version", "Vissim version to be confirmed; platform output imported from local scenario results"],
      ["Time Period Settings", "Warm-up, simulation and cool-down periods to be confirmed from model settings"],
      ["Random Seed", buildSeedSummary(scenarios, results)],
      ["Vehicle Types", "Vehicle class grouping to be confirmed from Vissim input definitions"],
      ["Driving Behaviour", "Urban, freeway, merging and diverging behaviours to be confirmed from Vissim parameters"],
    ],
  ));
  lines.push("");
  lines.push("### 4.2 Traffic Volume Calibration / GEH Criteria");
  lines.push("");
  lines.push(markdownTable(
    ["Category", "Acceptance Criteria", "Current Result"],
    [
      ["Category 1", "At least 95% of compared flows should have GEH < 5", formatPassRate(categoryOnePassRate)],
      ["Category 2", "At least 95% of compared flows should have GEH < 10", formatPassRate(categoryTwoPassRate)],
      ["Category 3", "All material outliers should be reviewed and explained", validationRows.some((row) => row.geh != null && row.geh >= 10) ? "Outliers require review" : "No GEH >= 10 outlier in available rows"],
    ],
  ));
  lines.push("");
  lines.push("#### Observed vs Modelled Traffic Volume");
  lines.push("");
  lines.push(
    validationRows.length > 0
      ? markdownTable(
          ["Node", "Scenario", "Observed Volume", "Modelled Volume", "% Error", "GEH", "Category 1"],
          validationRows.map((row) => [
            row.nodeName,
            row.scenarioName,
            formatReportNumber(row.observed),
            `${formatReportNumber(row.modelled)}${row.modelledIsScaled ? "*" : ""}`,
            formatReportPercent(row.percentError),
            row.geh == null ? "N/A" : row.geh.toFixed(2),
            row.geh != null && row.geh < 5 ? "Pass" : "Review",
          ]),
        )
      : "_Observed traffic count data was not available for the selected intersections. GEH calibration evidence should be added when survey counts are confirmed._",
  );
  if (validationRows.some((row) => row.modelledIsScaled)) {
    const baseName = scenarios.find((scenario) => scenario.id === BASE_SCENARIO_ID)?.name ?? "the base scenario";
    lines.push("");
    lines.push(
      `_* Modelled volume is scaled against ${baseName} using the ratio between the observed base-scenario count and the simulated base-scenario volume. This is an estimate, not a direct simulation output._`,
    );
  }
  lines.push("");
  lines.push("#### Selected Intersection Performance Summary");
  lines.push("");
  lines.push(markdownTable(
    ["Intersection", "Max Q Length", "Worst Approach Delay", "Average Volume"],
    intersectionCards.map((card) => [
      card.nodeName,
      `${formatReportNumber(card.maxQueueLength)} m`,
      `${formatReportNumber(card.worstApproachDelay)} sec`,
      `${formatReportNumber(card.volumeTotal)} veh`,
    ]),
  ));
  lines.push("");
  lines.push("#### Selected Intersection Direction Summary");
  lines.push("");
  lines.push(markdownTable(
    ["Intersection", "Direction", "Scenario", "Inbound Volume"],
    buildDirectionReportRows(intersectionCards, scenarios, results),
  ));
  lines.push("");
  lines.push(
    "_Per-approach Level of Service is not reported here: VISSIM outputs delay at the intersection level, not per approach, so any per-direction LOS would be an estimate rather than a measured value._",
  );
  lines.push("");

  if (summaryCards.length > 0 || keyInsights.length > 0 || hotspotRows.length > 0 || ranking.length > 0) {
    lines.push("#### Platform Summary Insights");
    lines.push("");
    if (summaryCards.length > 0) {
      lines.push(markdownTable(
        ["Metric", "Value", "Description"],
        summaryCards.map((card) => [
          card.label,
          `${card.value}${card.unit ? ` ${card.unit}` : ""}`,
          card.description ?? "",
        ]),
      ));
      lines.push("");
    }
    if (ranking.length > 0) {
      lines.push(markdownTable(
        ["Rank", "Scenario", "Score", "Reason"],
        ranking.map((item, index) => [
          String(item.rank ?? index + 1),
          item.scenario_name ?? item.scenario_id,
          item.score_label ?? (item.score == null ? "" : item.score.toFixed(1)),
          item.reason ?? "",
        ]),
      ));
      lines.push("");
    }
    if (keyInsights.length > 0) {
      lines.push(keyInsights.map((item) => `- **${item.title}:** ${item.message}`).join("\n"));
      lines.push("");
    }
    if (hotspotRows.length > 0) {
      lines.push(markdownTable(
        ["Node", "Average Total", "Minimum", "Maximum", "Spread"],
        hotspotRows.slice(0, 5).map((item) => [
          item.node_name,
          formatReportNumber(item.average_total),
          formatReportNumber(item.min_total),
          formatReportNumber(item.max_total),
          formatReportNumber(item.spread),
        ]),
      ));
      lines.push("");
    }
  }

  lines.push("### 4.3 Model Validation");
  lines.push("");
  lines.push("#### Travel Times");
  lines.push("");
  lines.push(markdownTable(
    ["Scenario", "Travel Time Sections", "Average Modelled Travel Time", "Validation Assessment"],
    scenarios.map((scenario) => {
      const travelTimes = extractTravelTimeValues(results[scenario.id]);
      return [
        scenario.name,
        String(travelTimes.length),
        travelTimes.length > 0 ? `${formatReportNumber(average(travelTimes))} sec` : "N/A",
        "Observed travel time comparison required for final Category 1 assessment",
      ];
    }),
  ));
  lines.push("");
  lines.push(
    "Travel time validation should compare observed travel time data against Vissim model outputs. Category 1 compliance is generally achieved when modelled travel times are within the applicable tolerance of observed travel times.",
  );
  lines.push("");
  lines.push("## 5. Conclusion");
  lines.push("");
  lines.push(
    `The currently selected scenarios (${scenarioNames}) have been reviewed for the selected intersections (${intersectionNames}). ${comparisonNarrative}`,
  );
  lines.push(
    categoryOnePassRate.total > 0 && categoryOnePassRate.rate >= 95
      ? "Based on the available observed count comparison, the model satisfies the Category 1 GEH criterion for the selected validation rows, subject to completion of the remaining documentation checks."
      : "The generated evidence should be treated as a draft calibration and validation summary. Additional observed count, travel time and model configuration evidence should be confirmed before the base model is formally accepted.",
  );
  lines.push("");
  lines.push("## 6. Appendices / Documents Provided");
  lines.push("");
  lines.push(markdownTable(
    ["Appendix", "Document / Dataset", "Status"],
    [
      ["A", "Traffic survey raw data", observedNodes.size > 0 ? "Partially represented through validation counts" : "Required"],
      ["B", "Signal timing data", "Required for final report"],
      ["C", "Observed travel time data", "Required for final report"],
      ["D", "Vissim scenario result files", "Represented in platform comparison output"],
      ["E", "Layout drawings", "Required for final report"],
    ],
  ));
  lines.push("");

  return lines.join("\n");
}

function buildReportValidationRows({
  scenarios,
  intersectionCards,
  realCounts,
  results,
  summary,
}: {
  scenarios: ScenarioMeta[];
  intersectionCards: IntersectionPerformanceCard[];
  realCounts: NodeRealCounts[];
  results: Record<string, ScenarioResults>;
  summary: CompareSummaryResponse | null;
}): Array<{
  nodeId: string;
  nodeName: string;
  scenarioName: string;
  observed: number | null;
  modelled: number | null;
  modelledIsScaled: boolean;
  percentError: number | null;
  geh: number | null;
}> {
  const realByNode = new Map(realCounts.map((item) => [String(item.node_id), item]));
  const rows: Array<{
    nodeId: string;
    nodeName: string;
    scenarioName: string;
    observed: number | null;
    modelled: number | null;
    modelledIsScaled: boolean;
    percentError: number | null;
    geh: number | null;
  }> = [];

  for (const card of intersectionCards) {
    const observed = realByNode.get(String(card.nodeId))?.total_real ?? null;
    if (observed == null) continue;

    for (const scenario of scenarios) {
      // Same computeScaledSimVolume the dashboard's ValidationMatrix cell
      // uses, so this report can never report a different GEH for the same
      // node/scenario than what the user saw on screen. Falls back to raw
      // per-node movement volume only when compare-summary data is absent.
      const scaled = computeScaledSimVolume(card.nodeId, scenario.id, summary, realCounts);
      const modelled = scaled.value ?? calculateScenarioNodeVolume(results[scenario.id], card.nodeId);

      rows.push({
        nodeId: card.nodeId,
        nodeName: card.nodeName,
        scenarioName: scenario.name,
        observed,
        modelled,
        modelledIsScaled: scaled.scaleFactor != null,
        percentError: calculatePercentError(modelled, observed),
        geh: calculateGeh(modelled, observed),
      });
    }
  }

  return rows;
}

function buildDirectionReportRows(
  cards: IntersectionPerformanceCard[],
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): string[][] {
  const rows: string[][] = [];

  for (const card of cards) {
    for (const scenario of scenarios) {
      const nodeRow = findNodeDirectionRow(results[scenario.id], card.nodeId);
      const volume = nodeRow ? calculateDirectionalVolume(nodeRow) : null;
      for (const direction of DIRECTION_ORDER) {
        const directionLabel = DIRECTION_LABELS[direction].en;
        // Inbound volume is a real per-direction measurement; per-approach LOS
        // is NOT (it would be synthesized from intersection-average delay), so
        // it is deliberately omitted from the formal report.
        rows.push([
          card.nodeName,
          directionLabel,
          scenario.name,
          formatReportNumber(volume?.directions[direction]),
        ]);
      }
    }
  }

  return rows.length > 0 ? rows : [["N/A", "N/A", "N/A", "N/A"]];
}

function calculateScenarioNodeVolume(result: ScenarioResults | undefined, nodeId: string): number | null {
  if (!result) return null;

  const row = findNodeDirectionRow(result, nodeId);
  if (row) {
    return calculateDirectionalVolume(row).total;
  }

  const movementVolume = result.movements
    .filter((item) => objectBelongsToNode(item.OBJECT_ID, nodeId))
    .map((item) => toFiniteNumber(item["VEHS_MEAN"] ?? item["VEHS"]))
    .filter((value): value is number => value != null)
    .reduce((sum, value) => sum + value, 0);

  return movementVolume > 0 ? movementVolume : null;
}

function buildSeedSummary(scenarios: ScenarioMeta[], results: Record<string, ScenarioResults>): string {
  const seeds = scenarios
    .map((scenario) => {
      const network = results[scenario.id]?.network ?? {};
      const seed = network.RANDOM_SEED ?? network.RandomSeed ?? network.random_seed;
      return seed == null ? null : `${scenario.name}: ${seed}`;
    })
    .filter((item): item is string => item != null);

  return seeds.length > 0 ? seeds.join("; ") : "To be confirmed from Vissim simulation parameters";
}

function extractTravelTimeValues(result: ScenarioResults | undefined): number[] {
  if (!result) return [];

  const rows = [...(result.traveltime ?? []), ...(result.traveltime_interval_class ?? [])];
  const values: number[] = [];
  for (const row of rows) {
    for (const [key, value] of Object.entries(row)) {
      if (key === "OBJECT_ID" || key.startsWith("ARG_")) continue;
      const lower = key.toLowerCase();
      if (!lower.includes("trav") && !lower.includes("time")) continue;
      const parsed = toFiniteNumber(value);
      if (parsed != null && parsed > 0) values.push(parsed);
    }
  }

  return values;
}

function markdownTable(headers: string[], rows: string[][]): string {
  const safeHeaders = headers.map(escapeMarkdownCell);
  const safeRows = rows.map((row) => row.map((cell) => escapeMarkdownCell(String(cell ?? ""))));
  return [
    `| ${safeHeaders.join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`,
    ...safeRows.map((row) => `| ${row.join(" | ")} |`),
  ].join("\n");
}

function buildWordDocumentHtml(markdown: string): string {
  const body = markdownToWordHtml(markdown);
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Traffic Modelling Report</title>
  <style>
    body { font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #fafaf9; }
    h1 { font-size: 20pt; margin: 0 0 18pt; }
    h2 { font-size: 15pt; margin: 18pt 0 8pt; border-bottom: 1px solid #45443f; padding-bottom: 4pt; }
    h3 { font-size: 12.5pt; margin: 14pt 0 6pt; }
    h4 { font-size: 11pt; margin: 12pt 0 6pt; }
    p { margin: 0 0 8pt; }
    ul { margin: 0 0 8pt 18pt; padding: 0; }
    li { margin: 0 0 4pt; }
    table { width: 100%; border-collapse: collapse; margin: 8pt 0 12pt; }
    th, td { border: 1px solid #6a6862; padding: 5pt 6pt; vertical-align: top; }
    th { background: #33332f; font-weight: 700; }
    em { color: #a5a39a; }
  </style>
</head>
<body>
${body}
</body>
</html>`;
}

function markdownToWordHtml(markdown: string): string {
  const lines = markdown.split(/\r?\n/);
  const html: string[] = [];
  let paragraph: string[] = [];
  let listOpen = false;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    html.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };

  const closeList = () => {
    if (!listOpen) return;
    html.push("</ul>");
    listOpen = false;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      closeList();
      continue;
    }

    if (trimmed.startsWith("|") && index + 1 < lines.length && /^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(lines[index + 1].trim())) {
      flushParagraph();
      closeList();
      const headers = parseMarkdownTableRow(trimmed);
      index += 1;
      const rows: string[][] = [];
      while (index + 1 < lines.length && lines[index + 1].trim().startsWith("|")) {
        index += 1;
        rows.push(parseMarkdownTableRow(lines[index].trim()));
      }
      html.push(renderHtmlTable(headers, rows));
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    if (trimmed.startsWith("- ")) {
      flushParagraph();
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${renderInlineMarkdown(trimmed.slice(2))}</li>`);
      continue;
    }

    paragraph.push(trimmed);
  }

  flushParagraph();
  closeList();
  return html.join("\n");
}

function parseMarkdownTableRow(row: string): string[] {
  return row
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split(/(?<!\\)\|/)
    .map((cell) => cell.replace(/\\\|/g, "|").trim());
}

function renderHtmlTable(headers: string[], rows: string[][]): string {
  const headerHtml = headers.map((header) => `<th>${renderInlineMarkdown(header)}</th>`).join("");
  const rowHtml = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`)
    .join("\n");
  return `<table>
<thead><tr>${headerHtml}</tr></thead>
<tbody>
${rowHtml}
</tbody>
</table>`;
}

function renderInlineMarkdown(value: string): string {
  return escapeHtml(value)
    .replace(/&lt;br&gt;/g, "<br>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^_(.+)_$/g, "<em>$1</em>");
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeMarkdownCell(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\r?\n/g, "<br>");
}

function calculateGeh(modelled: number | null, observed: number | null): number | null {
  if (modelled == null || observed == null) return null;
  const denom = modelled + observed;
  if (denom <= 0) return null;
  return Math.sqrt((2 * (modelled - observed) ** 2) / denom);
}

function calculatePercentError(modelled: number | null, observed: number | null): number | null {
  if (modelled == null || observed == null || observed === 0) return null;
  return ((modelled - observed) / observed) * 100;
}

function calculateGehPassRate(rows: Array<{ geh: number | null }>, threshold: number): { passed: number; total: number; rate: number } {
  const valid = rows.filter((row) => row.geh != null);
  const passed = valid.filter((row) => (row.geh ?? Infinity) < threshold).length;
  return {
    passed,
    total: valid.length,
    rate: valid.length > 0 ? (passed / valid.length) * 100 : 0,
  };
}

function formatPassRate(result: { passed: number; total: number; rate: number }): string {
  if (result.total === 0) return "No observed comparison rows available";
  return `${result.passed}/${result.total} (${result.rate.toFixed(1)}%)`;
}

function formatReportNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "N/A";
  return Math.round(value).toLocaleString();
}

function formatReportPercent(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "N/A";
  return `${value.toFixed(1)}%`;
}

function formatReportDate(date: Date): string {
  return date.toLocaleDateString("en-AU", { year: "numeric", month: "short", day: "2-digit" });
}

function formatDisplayDateTime(date: Date, lang: "ko" | "en" | "ja" = "ko"): string {
  const locale = lang === "en" ? "en-US" : lang === "ja" ? "ja-JP" : "ko-KR";
  return date.toLocaleString(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateForFilename(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}${month}${day}-${hour}${minute}`;
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatNumber(value: number, digits = 1): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function toFiniteNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function numericAwareCompare(a: string, b: string): number {
  const aNum = Number.parseFloat(a);
  const bNum = Number.parseFloat(b);
  if (Number.isFinite(aNum) && Number.isFinite(bNum) && aNum !== bNum) {
    return aNum - bNum;
  }
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function EmptyNodePanel() {
  const { t } = useLanguage();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-slate-100 text-2xl">
        N
      </div>
      <div>
        <p className="font-semibold text-slate-700">{t("지도에서 교차로를 선택하세요", "Select a node on the map")}</p>
        <p className="mt-1 text-sm text-slate-500">
          {t("교차로를 클릭하면 방향별 통행량과 시나리오 비교를 볼 수 있습니다.", "Click a node to review direction-level flow and scenario comparison.")}
        </p>
      </div>
    </div>
  );
}

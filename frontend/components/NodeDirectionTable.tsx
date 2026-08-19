"use client";

import { useDeferredValue, useEffect, useMemo, useState } from "react";

import { resolveSelection } from "@/lib/asyncResource";

import type { ScenarioMeta, ScenarioResults } from "@/lib/api";

interface Props {
  scenarios: ScenarioMeta[];
  selected: string[];
  results: Record<string, ScenarioResults>;
}

const DIRECTIONS = ["north", "east", "south", "west"] as const;
type Direction = (typeof DIRECTIONS)[number];
type NodeDirectionRow = NonNullable<ScenarioResults["node_directions"]>[number];
type LaneFlowRow = NonNullable<NodeDirectionRow["lane_flows"]>[number];
type ViewMode = "matrix" | "intersection" | "bars";
type SortKey = "spread" | "volume" | "name" | "baselineDelta";
type DirectionFilter = "all" | Direction;
type ComparisonBaseline = "average" | string;

interface NodeSummary {
  node_id: string;
  node_name: string;
  searchKey: string;
  representative: NodeDirectionRow;
  totalsByScenario: Record<string, number>;
  avgTotal: number;
  maxTotal: number;
  minTotal: number;
  spread: number;
  directionTotals: Record<Direction, number>;
}

const DIR_KR: Record<Direction, string> = { north: "북", east: "동", south: "남", west: "서" };
const DIR_ARROW: Record<Direction, string> = { north: "↑", east: "→", south: "↓", west: "←" };
const VIEW_MODE_HELP: Record<ViewMode, string> = {
  matrix: "방향별 유입·유출과 시나리오 점유율을 한 번에 비교합니다.",
  intersection: "교차로 형태 다이어그램으로 방향·차로별 흐름을 직관적으로 확인합니다.",
  bars: "시나리오별 막대 길이로 유입·유출 차이를 빠르게 읽습니다.",
};
const TOP_BOTTLENECK_LIMIT = 5;

interface ScenarioComparisonRow {
  scenario: ScenarioMeta;
  total: number;
  differenceFromBaseline: number;
  differencePercentFromBaseline: number | null;
  isBaseline: boolean;
}

interface DirectionInsight {
  direction: Direction;
  totalAcross: number;
  spread: number;
  incomingTotal: number;
  outgoingTotal: number;
  leaderScenario: ScenarioMeta;
  leaderTotal: number;
  rows: {
    scenario: ScenarioMeta;
    total: number;
  }[];
}

function intensityStyle(ratio: number) {
  if (ratio >= 0.7)
    return { bg: "bg-red-50", text: "text-red-700", bar: "bg-red-400", border: "border-red-200" };
  if (ratio >= 0.35)
    return { bg: "bg-amber-50", text: "text-amber-700", bar: "bg-amber-400", border: "border-amber-200" };
  return { bg: "bg-emerald-50", text: "text-emerald-700", bar: "bg-emerald-400", border: "border-emerald-200" };
}

function formatVolume(value: number) {
  return Math.round(value).toLocaleString();
}

function formatSignedVolume(value: number) {
  const rounded = Math.round(value);
  if (rounded > 0) return `+${rounded.toLocaleString()}`;
  if (rounded < 0) return rounded.toLocaleString();
  return "±0";
}

function formatSignedPercent(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  const rounded = Math.round(value * 10) / 10;
  if (rounded > 0) return `+${rounded.toFixed(1)}%`;
  if (rounded < 0) return `${rounded.toFixed(1)}%`;
  return "0.0%";
}

export default function NodeDirectionTable({ scenarios, selected, results }: Props) {
  const [viewMode, setViewMode] = useState<ViewMode>("matrix");
  const [sortKey, setSortKey] = useState<SortKey>("spread");
  const [query, setQuery] = useState("");
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>("all");
  // Resolved during render: a baseline scenario that leaves the selection
  // falls back to "average" immediately, instead of one render later (which
  // showed a column comparing against a scenario no longer on screen).
  const [comparisonBaselineIntent, setComparisonBaseline] = useState<ComparisonBaseline>("average");
  const [showChangedOnly, setShowChangedOnly] = useState(false);
  const [activeNodeIdIntent, setActiveNodeId] = useState<string | null>(null);
  const deferredQuery = useDeferredValue(query);

  const resetFilters = () => {
    setQuery("");
    setDirectionFilter("all");
    setShowChangedOnly(false);
  };

  const activeScenarios = useMemo(
    () => scenarios.filter((scenario) => selected.includes(scenario.id)),
    [scenarios, selected],
  );

  const comparisonBaseline: ComparisonBaseline =
    comparisonBaselineIntent === "average" ||
    activeScenarios.some((scenario) => scenario.id === comparisonBaselineIntent)
      ? comparisonBaselineIntent
      : "average";

  const nodes = useMemo(
    () => buildNodeSummaries(activeScenarios, results),
    [activeScenarios, results],
  );

  const baselineStatsByNodeId = useMemo(() => {
    const stats = new Map<string, { baselineTotal: number; maxAbsDelta: number }>();
    for (const node of nodes) {
      stats.set(node.node_id, getNodeBaselineStats(node, activeScenarios, comparisonBaseline));
    }
    return stats;
  }, [activeScenarios, comparisonBaseline, nodes]);

  const filteredNodes = useMemo(() => {
    const keyword = deferredQuery.trim().toLowerCase();
    const searched = nodes.filter((node) => {
      if (keyword && !node.searchKey.includes(keyword)) {
        return false;
      }
      const baselineStats = baselineStatsByNodeId.get(node.node_id);
      const maxAbsDelta = baselineStats?.maxAbsDelta ?? 0;
      if (showChangedOnly && maxAbsDelta <= 0) {
        return false;
      }
      if (directionFilter !== "all" && getDominantDirection(node.directionTotals) !== directionFilter) {
        return false;
      }
      return true;
    });

    return [...searched].sort((a, b) => {
      if (sortKey === "name") {
        return a.node_name.localeCompare(b.node_name, "ko");
      }
      if (sortKey === "volume") {
        return b.avgTotal - a.avgTotal;
      }
      if (sortKey === "baselineDelta") {
        const deltaA = baselineStatsByNodeId.get(a.node_id)?.maxAbsDelta ?? 0;
        const deltaB = baselineStatsByNodeId.get(b.node_id)?.maxAbsDelta ?? 0;
        return deltaB - deltaA;
      }
      return b.spread - a.spread;
    });
  }, [
    baselineStatsByNodeId,
    deferredQuery,
    directionFilter,
    nodes,
    showChangedOnly,
    sortKey,
  ]);
  const topBottleneckNodes = useMemo(
    () =>
      [...filteredNodes]
        .sort((a, b) => {
          if (b.avgTotal !== a.avgTotal) {
            return b.avgTotal - a.avgTotal;
          }
          if (b.maxTotal !== a.maxTotal) {
            return b.maxTotal - a.maxTotal;
          }
          return b.spread - a.spread;
        })
        .slice(0, TOP_BOTTLENECK_LIMIT),
    [filteredNodes],
  );
  const topBottleneckRanks = useMemo(
    () => new Map(topBottleneckNodes.map((node, index) => [node.node_id, index + 1])),
    [topBottleneckNodes],
  );
  const topBottleneckIds = useMemo(
    () => new Set(topBottleneckNodes.map((node) => node.node_id)),
    [topBottleneckNodes],
  );
  const topBottleneckCount = topBottleneckNodes.length;

  // Same derivation as the baseline above: the row the table highlights is the
  // user's intent resolved against the rows that actually exist right now.
  const activeNodeId = resolveSelection(activeNodeIdIntent, filteredNodes, (node) => node.node_id);

  if (!activeScenarios.length) return null;

  if (!nodes.length) {
    return (
      <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-6 py-10 text-center text-[15px] text-slate-400">
        노드별 방향 차량 수 데이터가 없습니다.
      </div>
    );
  }

  const hasFilteredNodes = filteredNodes.length > 0;
  const selectedNode = hasFilteredNodes
    ? filteredNodes.find((node) => node.node_id === activeNodeId) ?? filteredNodes[0]
    : null;
  const busiestNode = hasFilteredNodes
    ? filteredNodes.reduce((best, node) => (node.avgTotal > best.avgTotal ? node : best))
    : null;
  const widestGapNode = hasFilteredNodes
    ? filteredNodes.reduce((best, node) => (node.spread > best.spread ? node : best))
    : null;
  const averageSpread = hasFilteredNodes
    ? filteredNodes.reduce((sum, node) => sum + node.spread, 0) / filteredNodes.length
    : 0;
  const dominantDirection = selectedNode ? getDominantDirection(selectedNode.directionTotals) : null;
  const scenarioComparisons = selectedNode
    ? buildScenarioComparisons(activeScenarios, selectedNode, comparisonBaseline)
    : [];
  const selectedNodeBaselineTotal = selectedNode
    ? getNodeBaselineTotal(selectedNode, comparisonBaseline)
    : 0;
  const baselineLabel =
    comparisonBaseline === "average"
      ? "평균"
      : activeScenarios.find((scenario) => scenario.id === comparisonBaseline)?.name ?? "평균";
  const directionInsights = selectedNode
    ? buildDirectionInsights(selectedNode.node_id, activeScenarios, results)
    : [];
  const changedNodeCount = nodes.filter((node) => {
    const baselineStats = baselineStatsByNodeId.get(node.node_id);
    return (baselineStats?.maxAbsDelta ?? 0) > 0;
  }).length;
  const dominantDirectionCounts = DIRECTIONS.reduce(
    (acc, direction) => {
      acc[direction] = nodes.filter((node) => getDominantDirection(node.directionTotals) === direction).length;
      return acc;
    },
    { north: 0, east: 0, south: 0, west: 0 } as Record<Direction, number>,
  );
  const hasActiveFilters = query.trim().length > 0 || directionFilter !== "all" || showChangedOnly;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          label="Visible nodes"
          value={`${filteredNodes.length}개`}
          hint={`${nodes.length} total nodes after scenario loading`}
        />
        <SummaryCard
          label="평균 편차"
          value={`${formatVolume(averageSpread)}대`}
          hint="시나리오 간 최대-최소 편차 평균"
        />
        <SummaryCard
          label="최다 교통 노드"
          value={busiestNode?.node_name ?? "현재 조건에 맞는 노드 없음"}
          hint={busiestNode ? `평균 ${formatVolume(busiestNode.avgTotal)}대` : "필터를 조정해 노드를 다시 표시하세요"}
        />
        <SummaryCard
          label="최대 편차 노드"
          value={widestGapNode?.node_name ?? "현재 조건에 맞는 노드 없음"}
          hint={
            widestGapNode
              ? `시나리오 간 ${formatVolume(widestGapNode.spread)}대 차이`
              : "필터를 조정해 노드를 다시 표시하세요"
          }
        />
      </div>

      <div className="rounded-[1.5rem] border border-rose-200 bg-rose-50/60 px-4 py-4 shadow-[0_18px_40px_-28px_rgba(15,23,42,0.18)]">
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-[13px] font-semibold uppercase tracking-[0.22em] text-rose-500">상위 병목 노드</p>
          <span className="rounded-full border border-rose-200 bg-surface px-2.5 py-0.5 text-[13px] font-medium text-rose-600">
            상위 {topBottleneckCount}
          </span>
        </div>
        {topBottleneckNodes.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {topBottleneckNodes.map((node, index) => (
              <button
                key={`bottleneck-${node.node_id}`}
                onClick={() => setActiveNodeId(node.node_id)}
                className={`rounded-full border px-3 py-1 text-[14px] font-medium transition ${
                  selectedNode?.node_id === node.node_id
                    ? "border-rose-400 bg-rose-500 text-white"
                    : "border-rose-200 bg-surface text-rose-700 hover:border-rose-300"
                }`}
                title={`평균 교통량: ${formatVolume(node.avgTotal)}`}
              >
                #{index + 1} {node.node_name} ({formatVolume(node.avgTotal)})
              </button>
            ))}
          </div>
        ) : (
          <p className="text-[14px] text-rose-600/80">현재 필터 조건에서 병목 후보가 없습니다.</p>
        )}
      </div>

      {topBottleneckCount > 0 && (
        <section className="rounded-[1.5rem] border border-rose-200 bg-gradient-to-r from-rose-50 via-surface to-amber-50 px-4 py-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3 border-b border-rose-100 pb-3">
            <div>
              <p className="text-[13px] font-semibold uppercase tracking-[0.22em] text-rose-500">병목 집중</p>
              <h3 className="mt-1 text-[15px] font-semibold text-slate-900">교통량 기준 병목 상위 {topBottleneckCount}</h3>
              <p className="mt-1 text-[13px] text-slate-500">
                Jump directly to the busiest nodes in the current filtered result set and compare their directional flows first.
              </p>
            </div>
            <div className="rounded-full border border-rose-200 bg-surface px-3 py-1 text-[13px] font-medium text-rose-600">
              Avg total volume
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-5">
            {topBottleneckNodes.map((node, index) => {
              const isActive = selectedNode?.node_id === node.node_id;
              const direction = getDominantDirection(node.directionTotals);

              return (
                <button
                  key={`top-bottleneck-${node.node_id}`}
                  type="button"
                  onClick={() => setActiveNodeId(node.node_id)}
                  className={`rounded-xl border px-3.5 py-3 text-left transition ${
                    isActive
                      ? "border-rose-300 bg-rose-100/80 shadow-sm shadow-rose-100"
                      : "border-slate-200 bg-surface/90 hover:border-rose-200 hover:bg-surface"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="inline-flex h-7 min-w-7 items-center justify-center rounded-full bg-slate-900 px-2 text-[14px] font-semibold text-ink">
                      {index + 1}
                    </span>
                    <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[12px] font-semibold text-slate-600">
                      {DIR_ARROW[direction]} {DIR_KR[direction]}
                    </span>
                  </div>

                  <div className="mt-3 min-w-0">
                    <p className="truncate text-[15px] font-semibold text-slate-900">{node.node_name}</p>
                    <p className="font-mono text-[13px] text-slate-400">노드 {node.node_id}</p>
                  </div>

                  <div className="mt-3 grid gap-1 text-[13px] text-slate-500">
                    <div className="flex items-center justify-between gap-2">
                      <span>평균 교통량</span>
                      <span className="font-semibold text-slate-700">{formatVolume(node.avgTotal)}대</span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span>시나리오 차이</span>
                      <span className="font-semibold text-rose-500">{formatVolume(node.spread)}대</span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      )}

      <div className="rounded-[1.5rem] border border-slate-200 bg-surface px-4 py-4 shadow-[0_18px_40px_-28px_rgba(15,23,42,0.22)]">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-3">
          <div>
            <p className="text-[13px] font-semibold uppercase tracking-[0.22em] text-slate-400">작업 제어</p>
            <h3 className="mt-1 text-[15px] font-semibold text-slate-900">필터 및 비교 도구</h3>
          </div>
          <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[13px] font-medium text-slate-500">
            {activeScenarios.length}개 시나리오 · {filteredNodes.length}개 노드 표시 중
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <div className="relative min-w-[210px] flex-1">
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="노드 이름 또는 노드 ID 검색"
                className="w-full rounded-lg border border-slate-200 bg-surface px-3 py-2 text-[15px] text-slate-700 outline-none ring-blue-500/30 transition focus:border-blue-400 focus:ring-2"
              />
            </div>
            <div className="flex items-center rounded-lg border border-slate-200 bg-surface p-0.5 text-[14px] shadow-sm">
              {[
                { key: "spread", label: "편차" },
                { key: "volume", label: "교통량" },
                { key: "baselineDelta", label: "기준 대비 Δ" },
                { key: "name", label: "이름" },
              ].map((option) => (
                <button
                  key={option.key}
                  onClick={() => setSortKey(option.key as SortKey)}
                  className={`rounded-md px-3 py-1 font-medium transition-colors ${
                    sortKey === option.key
                      ? "bg-slate-800 text-ink shadow-sm"
                      : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 rounded-lg border border-slate-200 bg-surface px-2.5 py-2 text-[13px] text-slate-500">
              <span className="font-medium text-slate-400">기준</span>
              <select
                value={comparisonBaseline}
                onChange={(event) => setComparisonBaseline(event.target.value)}
                className="max-w-[170px] rounded border border-slate-200 bg-surface px-2 py-1 text-[13px] font-medium text-slate-600 outline-none ring-blue-500/30 transition focus:border-blue-400 focus:ring-2"
              >
                <option value="average">평균</option>
                {activeScenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => setShowChangedOnly((prev) => !prev)}
              className={`rounded-lg border px-3 py-2 text-[14px] font-medium transition ${
                showChangedOnly
                  ? "border-rose-200 bg-rose-50 text-rose-600"
                  : "border-slate-200 bg-surface text-slate-500 hover:text-slate-700"
              }`}
            >
              변화 있는 항목만
              <span className="ml-1 rounded-full bg-surface/80 px-1.5 py-0.5 text-[12px] font-semibold">
                {changedNodeCount}
              </span>
            </button>
          </div>

          <div className="flex items-center rounded-lg border border-slate-200 bg-surface p-0.5 text-[14px] shadow-sm">
            {[
              { key: "matrix", label: "방향 매트릭스" },
              { key: "intersection", label: "교차로 도식" },
              { key: "bars", label: "막대 비교" },
            ].map((option) => (
              <button
                key={option.key}
                onClick={() => setViewMode(option.key as ViewMode)}
                className={`rounded-md px-3 py-1 font-medium transition-colors ${
                  viewMode === option.key
                    ? "bg-slate-800 text-ink shadow-sm"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="w-full border-t border-slate-100 pt-3 text-[13px] text-slate-400">
            {VIEW_MODE_HELP[viewMode]}
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <span className="text-[13px] font-medium text-slate-400">우세 방향</span>
            <button
              onClick={() => setDirectionFilter("all")}
              className={`rounded-full border px-2.5 py-1 text-[13px] font-medium transition ${
                directionFilter === "all"
                  ? "border-slate-800 bg-slate-800 text-ink"
                  : "border-slate-200 bg-surface text-slate-500 hover:text-slate-700"
              }`}
            >
              전체
              <span className="ml-1 opacity-80">{nodes.length}</span>
            </button>
            {DIRECTIONS.map((direction) => (
              <button
                key={direction}
                onClick={() => setDirectionFilter(direction)}
                className={`rounded-full border px-2.5 py-1 text-[13px] font-medium transition ${
                  directionFilter === direction
                    ? "border-slate-800 bg-slate-800 text-ink"
                    : "border-slate-200 bg-surface text-slate-500 hover:text-slate-700"
                }`}
              >
                {DIR_ARROW[direction]} {DIR_KR[direction]}
                <span className="ml-1 opacity-80">{dominantDirectionCounts[direction]}</span>
              </button>
            ))}
          </div>
          {hasActiveFilters && (
            <div className="flex w-full flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-[13px]">
              <span className="font-medium text-slate-400">적용 필터</span>
              {query.trim().length > 0 && (
                <span className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 text-sky-600">
                  검색: {query.trim()}
                </span>
              )}
              {directionFilter !== "all" && (
                <span className="rounded-full border border-slate-200 bg-surface px-2.5 py-1 text-slate-600">
                  방향: {DIR_ARROW[directionFilter]} {DIR_KR[directionFilter]}
                </span>
              )}
              {showChangedOnly && (
                <span className="rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-rose-600">
                  변화 있는 항목만
                </span>
              )}
              <button
                onClick={resetFilters}
                className="rounded-full border border-slate-200 bg-surface px-2.5 py-1 font-medium text-slate-500 transition hover:text-slate-700"
              >
                필터 초기화
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="rounded-[1.5rem] border border-slate-200 bg-surface p-4 shadow-[0_18px_40px_-28px_rgba(15,23,42,0.24)]">
          <div className="mb-3 flex items-center justify-between border-b border-slate-100 pb-3">
            <div>
              <p className="text-[13px] font-semibold uppercase tracking-[0.22em] text-slate-400">탐색</p>
              <h3 className="mt-1 text-[15px] font-semibold text-slate-900">노드 목록</h3>
            </div>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[13px] font-medium text-slate-500">
              {filteredNodes.length}개
            </span>
          </div>
          {hasFilteredNodes ? (
            <div className="max-h-[680px] space-y-2 overflow-y-auto pr-1">
              {filteredNodes.map((node) => {
                const isActive = node.node_id === selectedNode?.node_id;
                const direction = getDominantDirection(node.directionTotals);
                const bottleneckRank = topBottleneckRanks.get(node.node_id);

                return (
                  <button
                    key={node.node_id}
                    onClick={() => setActiveNodeId(node.node_id)}
                    className={`w-full rounded-xl border px-3 py-2.5 text-left transition ${
                      isActive
                        ? "border-violet-200 bg-violet-50/80 shadow-sm shadow-violet-100/60"
                        : bottleneckRank
                          ? "border-rose-200 bg-rose-50/60 hover:border-rose-300 hover:bg-rose-50"
                          : "border-slate-200 bg-surface hover:border-slate-300 hover:bg-slate-50"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-[15px] font-semibold text-slate-800">{node.node_name}</p>
                          {topBottleneckIds.has(node.node_id) ? (
                            <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[12px] font-semibold text-rose-600">
                              상위
                            </span>
                          ) : null}
                        </div>
                        <p className="font-mono text-[13px] text-slate-400">노드 {node.node_id}</p>
                      </div>
                      <span className="rounded-full border border-slate-200 bg-surface px-2 py-0.5 text-[12px] font-semibold text-slate-600">
                        {DIR_ARROW[direction]} {DIR_KR[direction]}
                      </span>
                    </div>

                    {bottleneckRank && (
                      <div className="mt-2">
                        <span className="inline-flex items-center rounded-full border border-rose-200 bg-surface px-2 py-0.5 text-[12px] font-semibold text-rose-600">
                          병목 상위 {bottleneckRank}
                        </span>
                      </div>
                    )}

                    <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                      {activeScenarios.map((scenario) => (
                        <div
                          key={scenario.id}
                          className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-2 py-1"
                        >
                          <span className="flex min-w-0 items-center gap-1.5 text-[12px] text-slate-500">
                            <span
                              className="h-1.5 w-1.5 shrink-0 rounded-full"
                              style={{ backgroundColor: scenario.color }}
                            />
                            <span className="truncate">{scenario.name}</span>
                          </span>
                          <span className="text-[13px] font-semibold text-slate-700">
                            {formatVolume(node.totalsByScenario[scenario.id] ?? 0)}
                          </span>
                        </div>
                      ))}
                    </div>

                    <div className="mt-2 flex items-center justify-between text-[13px] text-slate-500">
                      <span>평균 {formatVolume(node.avgTotal)}대</span>
                      <span className="font-semibold text-rose-500">편차 {formatVolume(node.spread)}대</span>
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center">
              <p className="text-[15px] font-medium text-slate-600">현재 검색 조건에 맞는 노드가 없습니다.</p>
              <p className="mt-1 text-[14px] text-slate-400">검색어나 필터를 조정해 노드 목록을 다시 표시하세요.</p>
              {hasActiveFilters && (
                <button
                  onClick={resetFilters}
                  className="mt-3 rounded-full border border-slate-200 bg-surface px-3 py-1.5 text-[14px] font-medium text-slate-600 transition hover:text-slate-800"
                >
                  필터 초기화
                </button>
              )}
            </div>
          )}
        </aside>

        <section className="rounded-[1.5rem] border border-slate-200 bg-surface p-4 shadow-[0_18px_40px_-28px_rgba(15,23,42,0.24)]">
          {selectedNode ? (
            <>
              <header className="mb-4 rounded-[1.25rem] border border-slate-200 bg-gradient-to-br from-slate-50 via-surface to-slate-50 px-4 py-4 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-[13px] font-semibold uppercase tracking-[0.2em] text-slate-400">선택 노드</p>
                    <h3 className="text-base font-semibold text-slate-900">{selectedNode.node_name}</h3>
                    <p className="font-mono text-[14px] text-slate-400">노드 {selectedNode.node_id}</p>
                  </div>
                  {dominantDirection && (
                    <span className="rounded-full border border-slate-200 bg-surface px-2.5 py-0.5 text-[14px] font-semibold text-slate-600">
                      우세 방향: {DIR_ARROW[dominantDirection]} {DIR_KR[dominantDirection]}
                    </span>
                  )}
                </div>
                <SelectedScenarioLegend scenarios={activeScenarios} />
                <div className="mt-4 grid gap-2 text-[14px] text-slate-500 sm:grid-cols-4">
                  <div className="rounded-xl border border-slate-100 bg-surface px-3 py-2.5 shadow-sm">
                    평균 교통량 <span className="ml-1 font-semibold text-slate-700">{formatVolume(selectedNode.avgTotal)}대</span>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-surface px-3 py-2.5 shadow-sm">
                    최대 교통량 <span className="ml-1 font-semibold text-slate-700">{formatVolume(selectedNode.maxTotal)}대</span>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-surface px-3 py-2.5 shadow-sm">
                    시나리오 편차 <span className="ml-1 font-semibold text-rose-500">{formatVolume(selectedNode.spread)}대</span>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-surface px-3 py-2.5 shadow-sm">
                    기준({baselineLabel}){" "}
                    <span className="ml-1 font-semibold text-indigo-600">{formatVolume(selectedNodeBaselineTotal)}대</span>
                  </div>
                </div>
              </header>

              <div className="mb-4 space-y-3">
                <ScenarioComparisonOverview rows={scenarioComparisons} baselineLabel={baselineLabel} />
                <DirectionInsightStrip insights={directionInsights} />
              </div>

              {viewMode === "matrix" && (
                <DirectionMatrixView
                  nodeId={selectedNode.node_id}
                  activeScenarios={activeScenarios}
                  results={results}
                />
              )}

              {viewMode === "intersection" && (
                <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
                  {activeScenarios.map((scenario) => (
                    <ScenarioIntersectionCard
                      key={`${selectedNode.node_id}-${scenario.id}`}
                      scenario={scenario}
                      row={getNodeRow(results, scenario.id, selectedNode.node_id)}
                    />
                  ))}
                </div>
              )}

              {viewMode === "bars" && (
                <DirectionBarsView
                  node={selectedNode.representative}
                  activeScenarios={activeScenarios}
                  results={results}
                />
              )}
            </>
          ) : (
            <div className="flex min-h-[420px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 px-6 py-10 text-center">
              <p className="text-[15px] font-semibold text-slate-600">조건에 맞는 노드를 먼저 선택해 주세요.</p>
              <p className="mt-2 text-[14px] text-slate-400">
                Current filters are hiding the node workspace. Adjust the search or direction filter to bring it back.
              </p>
              {hasActiveFilters && (
                <button
                  onClick={resetFilters}
                  className="mt-4 rounded-full border border-slate-200 bg-surface px-3 py-1.5 text-[14px] font-medium text-slate-600 transition hover:text-slate-800"
                >
                  필터 초기화
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-slate-200 bg-surface px-4 py-4 shadow-sm">
      <div className="pointer-events-none absolute -right-6 -top-6 h-20 w-20 rounded-full bg-slate-100 blur-2xl" />
      <p className="text-[13px] font-medium text-slate-400">{label}</p>
      <p className="relative mt-2 truncate text-[15px] font-semibold text-slate-800">{value}</p>
      <p className="relative mt-2 text-[13px] leading-5 text-slate-500">{hint}</p>
    </div>
  );
}

function SelectedScenarioLegend({ scenarios }: { scenarios: ScenarioMeta[] }) {
  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-surface px-3 py-2">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-[13px] font-semibold text-slate-500">Selected scenarios</p>
        <span className="text-[12px] font-medium text-slate-400">
          first scenario is the delta baseline
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {scenarios.map((scenario) => (
          <span
            key={scenario.id}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[12px] font-semibold text-slate-600"
          >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: scenario.color }} />
            <span className="truncate">{scenario.name}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function ScenarioComparisonOverview({
  rows,
  baselineLabel,
}: {
  rows: ScenarioComparisonRow[];
  baselineLabel: string;
}) {
  const peak = Math.max(...rows.map((row) => row.total), 1);

  return (
    <section className="rounded-[1.5rem] border border-slate-200 bg-gradient-to-br from-slate-50 via-surface to-slate-50 px-4 py-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="sr-only">
          Scenario totals for this intersection. Compare selected scenarios against the chosen baseline.
        </div>
        <div>
          <h4 className="text-[15px] font-semibold text-slate-700">선택 노드 시나리오 비교</h4>
          <p className="text-[13px] text-slate-400">
            총 교통량과 기준({baselineLabel}) 대비 증감을 바로 읽을 수 있게 요약했습니다.
          </p>
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-3">
        {rows.map((row) => {
          const ratio = Math.min(100, (row.total / peak) * 100);
          const deltaTone =
            row.differenceFromBaseline > 0
              ? "text-rose-500"
              : row.differenceFromBaseline < 0
                ? "text-sky-500"
                : "text-slate-400";
          const deltaSummary = row.isBaseline
            ? "기준 시나리오"
            : `${baselineLabel} 대비 ${formatSignedVolume(row.differenceFromBaseline)}대 (${formatSignedPercent(row.differencePercentFromBaseline)})`;

          const readableDeltaSummary = row.isBaseline
            ? "Baseline scenario"
            : `${formatSignedVolume(row.differenceFromBaseline)} veh vs ${baselineLabel} (${formatSignedPercent(row.differencePercentFromBaseline)})`;

          return (
            <div key={row.scenario.id} className="rounded-xl border border-slate-200 bg-surface/95 px-3.5 py-3.5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full shadow-sm"
                      style={{ backgroundColor: row.scenario.color }}
                    />
                    <p className="truncate text-[15px] font-semibold text-slate-700">{row.scenario.name}</p>
                  </div>
                  <p className="mt-1 text-[14px] text-slate-400">{readableDeltaSummary}</p>
                </div>
                <span className={`shrink-0 text-[14px] font-semibold ${deltaTone}`}>
                  {row.total > 0 ? `${formatVolume(row.total)}대` : "0대"}
                </span>
              </div>

              <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${ratio}%`, backgroundColor: row.scenario.color }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function DirectionInsightStrip({ insights }: { insights: DirectionInsight[] }) {
  const peak = Math.max(...insights.map((insight) => insight.totalAcross), 1);

  return (
    <section className="rounded-[1.5rem] border border-slate-200 bg-surface px-4 py-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h4 className="text-[15px] font-semibold text-slate-700">방향별 비교 하이라이트</h4>
          <p className="text-[13px] text-slate-400">방향별 총량, 시나리오 편차, 유입·유출 우세를 압축해서 보여줍니다.</p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
        {insights.map((insight) => {
          const ratio = Math.min(100, (insight.totalAcross / peak) * 100);
          const flowBalance =
            insight.incomingTotal === insight.outgoingTotal
              ? "균형"
              : insight.incomingTotal > insight.outgoingTotal
                ? "유입 우세"
                : "유출 우세";

          return (
            <div key={insight.direction} className="rounded-xl border border-slate-200 bg-slate-50/80 px-3.5 py-3.5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-[14px] font-bold text-ink">
                    {DIR_KR[insight.direction]}
                  </span>
                  <div>
                    <p className="text-[15px] font-semibold text-slate-700">
                      {DIR_ARROW[insight.direction]} {DIR_KR[insight.direction]} 방향
                    </p>
                    <p className="text-[13px] text-slate-400">{flowBalance}</p>
                  </div>
                </div>
                <span className="text-[14px] font-semibold text-slate-600">{formatVolume(insight.totalAcross)}대</span>
              </div>

              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full rounded-full bg-slate-700" style={{ width: `${ratio}%` }} />
              </div>

              <div className="mt-3 grid grid-cols-2 gap-2 text-[13px] text-slate-500">
                <div className="rounded-lg border border-slate-200 bg-surface px-2.5 py-2">
                  <p className="text-slate-400">시나리오 편차</p>
                  <p className="mt-0.5 font-semibold text-rose-500">{formatVolume(insight.spread)}대</p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-surface px-2.5 py-2">
                  <p className="text-slate-400">최다 시나리오</p>
                  <p className="mt-0.5 truncate font-semibold text-slate-700">{insight.leaderScenario.name}</p>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {insight.rows.map((row) => {
                  const width = Math.min(100, (row.total / Math.max(insight.leaderTotal, 1)) * 100);
                  return (
                    <div
                      key={row.scenario.id}
                     className="min-w-[110px] flex-1 rounded-xl border border-slate-200 bg-surface px-2.5 py-2 shadow-sm"
                    >
                      <div className="flex items-center justify-between gap-2 text-[12px]">
                        <span className="flex min-w-0 items-center gap-1.5 text-slate-500">
                          <span
                            className="h-2 w-2 shrink-0 rounded-full"
                            style={{ backgroundColor: row.scenario.color }}
                          />
                          <span className="truncate">{row.scenario.name}</span>
                        </span>
                        <span className="font-semibold text-slate-700">{formatVolume(row.total)}</span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${width}%`, backgroundColor: row.scenario.color }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function DirectionMatrixView({
  nodeId,
  activeScenarios,
  results,
}: {
  nodeId: string;
  activeScenarios: ScenarioMeta[];
  results: Record<string, ScenarioResults>;
}) {
  const directions = DIRECTIONS.map((direction) => {
    const rows = activeScenarios.map((scenario) => {
      const node = getNodeRow(results, scenario.id, nodeId);
      const incoming = Number(node?.incoming[direction] ?? 0);
      const outgoing = Number(node?.outgoing[direction] ?? 0);
      const total = incoming + outgoing;
      return { scenario, incoming, outgoing, total };
    });
    const totalAcross = rows.reduce((sum, row) => sum + row.total, 0);
    return { direction, rows, totalAcross };
  });

  const peakDirectionTotal = Math.max(...directions.map((entry) => entry.totalAcross), 1);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {directions.map(({ direction, rows, totalAcross }) => {
        const scenarioPeak = Math.max(...rows.map((row) => row.total), 1);
        const directionRatio = Math.min(100, (totalAcross / peakDirectionTotal) * 100);

        return (
          <section
            key={direction}
            className="rounded-xl border border-slate-200 bg-gradient-to-br from-slate-50 via-surface to-slate-50 p-3.5 shadow-sm"
          >
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-slate-900 text-[14px] font-bold text-ink shadow-sm">
                  {DIR_KR[direction]}
                </span>
                <p className="text-[15px] font-semibold text-slate-700">
                  {DIR_ARROW[direction]} {DIR_KR[direction]} 방향
                </p>
              </div>
                  <span className="rounded-full border border-slate-200 bg-surface px-2.5 py-1 text-[13px] font-semibold text-slate-600">
                    {formatVolume(totalAcross)}대
                  </span>
            </div>

            <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-slate-400" style={{ width: `${directionRatio}%` }} />
            </div>

            <div className="space-y-2">
              {rows.map(({ scenario, incoming, outgoing, total }) => {
                const width = Math.min(100, (total / scenarioPeak) * 100);
                const share = totalAcross > 0 ? (total / totalAcross) * 100 : 0;

                return (
                  <div key={scenario.id} className="rounded-xl border border-slate-200 bg-surface/95 px-3 py-2.5 shadow-sm">
                    <div className="flex items-center justify-between">
                      <span className="flex min-w-0 items-center gap-2 text-[14px] font-medium text-slate-600">
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ backgroundColor: scenario.color }}
                        />
                        <span className="truncate">{scenario.name}</span>
                      </span>
                      <span className="text-[14px] font-semibold text-slate-700">{formatVolume(total)}대</span>
                    </div>

                    <div className="mt-2 grid grid-cols-3 gap-2 text-[13px]">
                      <MetricChip label="유입" value={incoming} />
                      <MetricChip label="유출" value={outgoing} />
                      <MetricChip label="점유율" valueLabel={`${share.toFixed(1)}%`} />
                    </div>

                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${width}%`, backgroundColor: scenario.color }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function MetricChip({
  label,
  value,
  valueLabel,
}: {
  label: string;
  value?: number;
  valueLabel?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50/70 px-2 py-1.5 text-center">
      <div className="text-[12px] text-slate-400">{label}</div>
      <div className="text-[13px] font-semibold text-slate-700">
        {valueLabel ?? `${formatVolume(value ?? 0)}대`}
      </div>
    </div>
  );
}

function ScenarioIntersectionCard({
  scenario,
  row,
}: {
  scenario: ScenarioMeta;
  row: NodeDirectionRow | undefined;
}) {
  const incoming = row?.incoming ?? {};
  const outgoing = row?.outgoing ?? {};
  const incomingLanes = row?.incoming_lanes ?? {};
  const outgoingLanes = row?.outgoing_lanes ?? {};
  const laneFlows = normalizeLaneFlows(row?.lane_flows);
  const { incomingByLane, outgoingByLane } = buildDirectionLaneVolumes({
    laneFlows,
    incomingTotals: incoming,
    outgoingTotals: outgoing,
    incomingLaneCounts: incomingLanes,
    outgoingLaneCounts: outgoingLanes,
  });
  const peak = Math.max(
    ...DIRECTIONS.flatMap((direction) => [Number(incoming[direction] ?? 0), Number(outgoing[direction] ?? 0)]),
    1,
  );
  const total = DIRECTIONS.reduce(
    (sum, direction) => sum + Number(incoming[direction] ?? 0) + Number(outgoing[direction] ?? 0),
    0,
  );

  return (
    <div className="rounded-[1.5rem] border border-slate-200 bg-surface px-4 py-4 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full shadow-sm"
            style={{ backgroundColor: scenario.color }}
          />
          <span className="text-[15px] font-semibold text-slate-800">{scenario.name}</span>
        </div>
        <span className="text-[14px] text-slate-400">{formatVolume(total)}&thinsp;대</span>
      </div>

      <NodeIntersectionGrid
        incoming={incoming}
        outgoing={outgoing}
        incomingByLane={incomingByLane}
        outgoingByLane={outgoingByLane}
        peak={peak}
      />
    </div>
  );
}

function NodeIntersectionGrid({
  incoming,
  outgoing,
  incomingByLane,
  outgoingByLane,
  peak,
}: {
  incoming: Record<string, number>;
  outgoing: Record<string, number>;
  incomingByLane: Record<Direction, number[]>;
  outgoingByLane: Record<Direction, number[]>;
  peak: number;
}) {
  return (
    <div className="relative overflow-hidden rounded-[1.5rem] border border-slate-100 bg-gradient-to-br from-slate-50 via-surface to-slate-50 px-4 py-5 shadow-inner">
      <div className="relative mx-auto grid w-full max-w-[336px] grid-cols-[104px_88px_104px] grid-rows-[80px_88px_80px] gap-2">
        <div className="pointer-events-none absolute left-1/2 top-2 h-[calc(100%-16px)] w-3.5 -translate-x-1/2 rounded-full bg-slate-200/95 shadow-[0_0_0_1px_rgba(255,255,255,0.55)]" />
        <div className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-[calc(100%-16px)] -translate-y-1/2 rounded-full bg-slate-200/95 shadow-[0_0_0_1px_rgba(255,255,255,0.55)]" />

        <DirectionArmCell
          className="col-start-2 row-start-1"
          orientation="horizontal"
          incoming={Number(incoming.north ?? 0)}
          outgoing={Number(outgoing.north ?? 0)}
          incomingLaneVolumes={incomingByLane.north}
          outgoingLaneVolumes={outgoingByLane.north}
          peak={peak}
        />
        <DirectionArmCell
          className="col-start-1 row-start-2"
          orientation="vertical"
          incoming={Number(incoming.west ?? 0)}
          outgoing={Number(outgoing.west ?? 0)}
          incomingLaneVolumes={incomingByLane.west}
          outgoingLaneVolumes={outgoingByLane.west}
          peak={peak}
        />
        <div className="relative z-20 col-start-2 row-start-2 flex items-center justify-center">
          <div className="flex h-[80px] w-[80px] flex-col items-center justify-center rounded-xl border-4 border-slate-100 bg-surface shadow-xl">
            <span className="text-[12px] font-bold uppercase tracking-widest text-slate-300">Node</span>
            <div className="mt-1 flex gap-1">
              {DIRECTIONS.map((direction) => {
                const volume = Number(incoming[direction] ?? 0) + Number(outgoing[direction] ?? 0);
                const maxVolume = Math.max(
                  ...DIRECTIONS.map((item) => Number(incoming[item] ?? 0) + Number(outgoing[item] ?? 0)),
                  1,
                );
                const ratio = volume / maxVolume;
                const { bar } = intensityStyle(ratio);
                return (
                  <span
                    key={direction}
                    title={`${DIR_KR[direction]} ${DIR_ARROW[direction]} ${Math.round(volume)}`}
                    className={`h-1.5 w-1.5 rounded-full ${volume > 0 ? bar : "bg-slate-200"}`}
                  />
                );
              })}
            </div>
          </div>
        </div>
        <DirectionArmCell
          className="col-start-3 row-start-2"
          orientation="vertical"
          incoming={Number(incoming.east ?? 0)}
          outgoing={Number(outgoing.east ?? 0)}
          incomingLaneVolumes={incomingByLane.east}
          outgoingLaneVolumes={outgoingByLane.east}
          peak={peak}
        />
        <DirectionArmCell
          className="col-start-2 row-start-3"
          orientation="horizontal"
          incoming={Number(incoming.south ?? 0)}
          outgoing={Number(outgoing.south ?? 0)}
          incomingLaneVolumes={incomingByLane.south}
          outgoingLaneVolumes={outgoingByLane.south}
          peak={peak}
        />

        <span className="pointer-events-none absolute left-1/2 top-0 -translate-x-1/2 -translate-y-full pb-0.5 text-[9px] font-bold tracking-widest text-slate-400">
          N
        </span>
        <span className="pointer-events-none absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-full pt-0.5 text-[9px] font-bold tracking-widest text-slate-400">
          S
        </span>
        <span className="pointer-events-none absolute left-0 top-1/2 -translate-x-full -translate-y-1/2 pr-0.5 text-[9px] font-bold tracking-widest text-slate-400">
          W
        </span>
        <span className="pointer-events-none absolute right-0 top-1/2 -translate-y-1/2 translate-x-full pl-0.5 text-[9px] font-bold tracking-widest text-slate-400">
          E
        </span>
      </div>
    </div>
  );
}

function DirectionArmCell({
  className,
  orientation,
  incoming,
  outgoing,
  incomingLaneVolumes,
  outgoingLaneVolumes,
  peak,
}: {
  className: string;
  orientation: "horizontal" | "vertical";
  incoming: number;
  outgoing: number;
  incomingLaneVolumes: number[];
  outgoingLaneVolumes: number[];
  peak: number;
}) {
  const layout = orientation === "horizontal" ? "grid-cols-2" : "grid-cols-1 grid-rows-2";

  return (
    <div className={`${className} relative z-10 grid ${layout} gap-1.5`}>
      <FlowBox label="유입" value={incoming} laneVolumes={incomingLaneVolumes} ratio={incoming / peak} />
      <FlowBox label="유출" value={outgoing} laneVolumes={outgoingLaneVolumes} ratio={outgoing / peak} />
    </div>
  );
}

function FlowBox({
  label,
  value,
  laneVolumes,
  ratio,
}: {
  label: string;
  value: number;
  laneVolumes: number[];
  ratio: number;
}) {
  const style = intensityStyle(ratio);

  return (
    <div className={`rounded-xl border px-2 py-1.5 shadow-sm backdrop-blur-sm ${style.bg} ${style.border}`}>
      <div className={`flex items-center justify-between text-[12px] font-semibold ${style.text}`}>
        <span>{label}</span>
        <span>{Math.round(value)}</span>
      </div>
      <div className="mt-1 min-h-[18px]">
        {laneVolumes.length > 0 ? (
          <div className="flex gap-px overflow-hidden rounded-[4px]">
            {laneVolumes.map((laneVolume, index) => (
              <div
                key={index}
                className="flex-1 bg-slate-200/80 py-0.5 text-center text-[8px] font-medium leading-none text-slate-600"
              >
                {Math.round(laneVolume)}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center text-[9px] text-slate-300">—</div>
        )}
      </div>
      <div className="mt-1 h-1.5 rounded-full bg-surface/60">
        <div
          className={`h-full rounded-full transition-all duration-300 ${style.bar}`}
          style={{ width: `${Math.min(100, ratio * 100)}%` }}
        />
      </div>
    </div>
  );
}

function DirectionBarsView({
  node,
  activeScenarios,
  results,
}: {
  node: NodeDirectionRow;
  activeScenarios: ScenarioMeta[];
  results: Record<string, ScenarioResults>;
}) {
  const allValues = activeScenarios.flatMap((scenario) => {
    const row = getNodeRow(results, scenario.id, node.node_id);
    return DIRECTIONS.flatMap((direction) => [
      Number(row?.incoming[direction] ?? 0),
      Number(row?.outgoing[direction] ?? 0),
    ]);
  });
  const peak = Math.max(...allValues, 1);

  const activeDirections = DIRECTIONS.filter((direction) =>
    activeScenarios.some((scenario) => {
      const row = getNodeRow(results, scenario.id, node.node_id);
      return Number(row?.incoming[direction] ?? 0) + Number(row?.outgoing[direction] ?? 0) > 0;
    }),
  );

  if (!activeDirections.length) {
    return (
      <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-[15px] text-slate-400">
        선택한 노드의 방향별 데이터가 없습니다.
      </div>
    );
  }

  return (
    <div className="px-2 py-1">
      <div className="mb-4 flex items-center gap-5 text-[13px] text-slate-400">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm bg-slate-400 opacity-90" />
          실선 = 유입(In)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm bg-slate-400 opacity-40" />
          반투명 = 유출(Out)
        </span>
      </div>

      <div className="space-y-5">
        {activeDirections.map((direction) => (
            <div key={direction} className="rounded-xl border border-slate-200 bg-surface px-4 py-4 shadow-sm">
              <div className="mb-2 flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-xl bg-slate-900 text-[13px] font-bold text-ink shadow-sm">
                  {DIR_KR[direction]}
                </span>
                <span className="text-[14px] font-semibold text-slate-600">
                  {DIR_ARROW[direction]} {DIR_KR[direction]} 방향
                </span>
              </div>

              <div className="space-y-2 pl-8">
                {activeScenarios.map((scenario) => {
                  const row = getNodeRow(results, scenario.id, node.node_id);
                  const incoming = Number(row?.incoming[direction] ?? 0);
                  const outgoing = Number(row?.outgoing[direction] ?? 0);
                  const inPct = Math.min(100, (incoming / peak) * 100);
                  const outPct = Math.min(100, (outgoing / peak) * 100);

                  return (
                    <div key={scenario.id} className="flex items-start gap-3">
                      <div className="flex w-28 shrink-0 items-center gap-1.5 pt-1">
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ backgroundColor: scenario.color }}
                        />
                        <span className="truncate text-[13px] text-slate-500">{scenario.name}</span>
                      </div>

                      <div className="flex min-w-0 flex-1 flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <span className="w-8 shrink-0 text-right text-[12px] font-medium text-slate-400">
                            유입
                          </span>
                          <div className="relative h-5 flex-1 overflow-hidden rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full transition-all duration-500"
                              style={{ width: `${inPct}%`, backgroundColor: scenario.color, opacity: 0.9 }}
                            />
                            {incoming > 0 && (
                              <span
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-[12px] font-semibold"
                                style={{ color: inPct > 50 ? "#fff" : "#475569" }}
                              >
                                {formatVolume(incoming)}
                              </span>
                            )}
                          </div>
                        </div>

                        <div className="flex items-center gap-2">
                          <span className="w-8 shrink-0 text-right text-[12px] font-medium text-slate-400">
                            유출
                          </span>
                          <div className="relative h-5 flex-1 overflow-hidden rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full transition-all duration-500"
                              style={{ width: `${outPct}%`, backgroundColor: scenario.color, opacity: 0.4 }}
                            />
                            {outgoing > 0 && (
                              <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[12px] font-semibold text-slate-500">
                                {formatVolume(outgoing)}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
        ))}
      </div>
    </div>
  );
}

function buildNodeSummaries(
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): NodeSummary[] {
  const nodeMap = new Map<
    string,
    {
      node_id: string;
      node_name: string;
      representative: NodeDirectionRow;
      totalsByScenario: Record<string, number>;
      directionTotals: Record<Direction, number>;
    }
  >();

  for (const scenario of scenarios) {
    for (const node of results[scenario.id]?.node_directions ?? []) {
      const total = totalNodeVolume(node);
      const existing = nodeMap.get(node.node_id);

      if (!existing) {
        nodeMap.set(node.node_id, {
          node_id: node.node_id,
          node_name: node.node_name,
          representative: node,
          totalsByScenario: { [scenario.id]: total },
          directionTotals: collectDirectionTotals(node),
        });
        continue;
      }

      existing.totalsByScenario[scenario.id] = total;
      existing.node_name = node.node_name;
      if (totalNodeVolume(node) > totalNodeVolume(existing.representative)) {
        existing.representative = node;
      }
      for (const direction of DIRECTIONS) {
        existing.directionTotals[direction] +=
          Number(node.incoming[direction] ?? 0) + Number(node.outgoing[direction] ?? 0);
      }
    }
  }

  return Array.from(nodeMap.values()).map((entry) => {
    const totals = scenarios.map((scenario) => entry.totalsByScenario[scenario.id] ?? 0);
    const maxTotal = Math.max(...totals);
    const minTotal = Math.min(...totals);
    const avgTotal = totals.reduce((sum, value) => sum + value, 0) / totals.length;

    return {
      node_id: entry.node_id,
      node_name: entry.node_name,
      searchKey: `${entry.node_name} ${entry.node_id}`.toLowerCase(),
      representative: entry.representative,
      totalsByScenario: entry.totalsByScenario,
      avgTotal,
      maxTotal,
      minTotal,
      spread: maxTotal - minTotal,
      directionTotals: entry.directionTotals,
    };
  });
}

function buildScenarioComparisons(
  scenarios: ScenarioMeta[],
  node: NodeSummary,
  comparisonBaseline: ComparisonBaseline,
): ScenarioComparisonRow[] {
  const baselineTotal = getNodeBaselineTotal(node, comparisonBaseline);
  return scenarios.map((scenario) => {
    const total = node.totalsByScenario[scenario.id] ?? 0;
    const differenceFromBaseline = total - baselineTotal;
    return {
      scenario,
      total,
      differenceFromBaseline,
      differencePercentFromBaseline:
        Math.abs(baselineTotal) > 0.0001 ? (differenceFromBaseline / baselineTotal) * 100 : null,
      isBaseline: comparisonBaseline !== "average" && scenario.id === comparisonBaseline,
    };
  });
}

function getNodeBaselineTotal(node: NodeSummary, comparisonBaseline: ComparisonBaseline): number {
  if (comparisonBaseline === "average") {
    return node.avgTotal;
  }
  return node.totalsByScenario[comparisonBaseline] ?? 0;
}

function getNodeBaselineStats(
  node: NodeSummary,
  scenarios: ScenarioMeta[],
  comparisonBaseline: ComparisonBaseline,
): { baselineTotal: number; maxAbsDelta: number } {
  const baselineTotal = getNodeBaselineTotal(node, comparisonBaseline);
  const maxAbsDelta = scenarios.reduce((maxDelta, scenario) => {
    const scenarioTotal = node.totalsByScenario[scenario.id] ?? 0;
    const delta = Math.abs(scenarioTotal - baselineTotal);
    return delta > maxDelta ? delta : maxDelta;
  }, 0);
  return { baselineTotal, maxAbsDelta };
}

function buildDirectionInsights(
  nodeId: string,
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): DirectionInsight[] {
  return DIRECTIONS.map((direction) => {
    const rows = scenarios.map((scenario) => {
      const node = getNodeRow(results, scenario.id, nodeId);
      const total = Number(node?.incoming[direction] ?? 0) + Number(node?.outgoing[direction] ?? 0);
      return { scenario, total };
    });
    const leader = rows.reduce((best, row) => (row.total > best.total ? row : best), rows[0]);
    const laggard = rows.reduce((best, row) => (row.total < best.total ? row : best), rows[0]);
    const incomingTotal = scenarios.reduce((sum, scenario) => {
      const node = getNodeRow(results, scenario.id, nodeId);
      return sum + Number(node?.incoming[direction] ?? 0);
    }, 0);
    const outgoingTotal = scenarios.reduce((sum, scenario) => {
      const node = getNodeRow(results, scenario.id, nodeId);
      return sum + Number(node?.outgoing[direction] ?? 0);
    }, 0);

    return {
      direction,
      totalAcross: rows.reduce((sum, row) => sum + row.total, 0),
      spread: leader.total - laggard.total,
      incomingTotal,
      outgoingTotal,
      leaderScenario: leader.scenario,
      leaderTotal: leader.total,
      rows,
    };
  });
}

function collectDirectionTotals(node: NodeDirectionRow) {
  return DIRECTIONS.reduce(
    (acc, direction) => {
      acc[direction] = Number(node.incoming[direction] ?? 0) + Number(node.outgoing[direction] ?? 0);
      return acc;
    },
    { north: 0, east: 0, south: 0, west: 0 } as Record<Direction, number>,
  );
}

function getDominantDirection(directionTotals: Record<Direction, number>): Direction {
  return DIRECTIONS.reduce((best, direction) =>
    directionTotals[direction] > directionTotals[best] ? direction : best,
  );
}

function getNodeRow(
  results: Record<string, ScenarioResults>,
  scenarioId: string,
  nodeId: string,
): NodeDirectionRow | undefined {
  return results[scenarioId]?.node_directions?.find((row) => row.node_id === nodeId);
}

function totalNodeVolume(node: NodeDirectionRow) {
  return DIRECTIONS.reduce(
    (sum, direction) => sum + Number(node.incoming[direction] ?? 0) + Number(node.outgoing[direction] ?? 0),
    0,
  );
}

function normalizeLaneFlows(laneFlows: NodeDirectionRow["lane_flows"] | undefined): LaneFlowRow[] {
  if (!laneFlows?.length) return [];
  const valid = new Set<string>(DIRECTIONS);
  return laneFlows.filter(
    (flow) =>
      valid.has(String(flow.from_direction)) &&
      valid.has(String(flow.to_direction)) &&
      Number.isFinite(Number(flow.from_lane)) &&
      Number.isFinite(Number(flow.to_lane)) &&
      Number.isFinite(Number(flow.volume)),
  );
}

function buildDirectionLaneVolumes({
  laneFlows,
  incomingTotals,
  outgoingTotals,
  incomingLaneCounts,
  outgoingLaneCounts,
}: {
  laneFlows: LaneFlowRow[];
  incomingTotals: Record<string, number>;
  outgoingTotals: Record<string, number>;
  incomingLaneCounts: Record<string, number>;
  outgoingLaneCounts: Record<string, number>;
}) {
  const incomingByLane = {} as Record<Direction, number[]>;
  const outgoingByLane = {} as Record<Direction, number[]>;

  for (const direction of DIRECTIONS) {
    const incomingTotal = Number(incomingTotals[direction] ?? 0);
    const outgoingTotal = Number(outgoingTotals[direction] ?? 0);
    const incomingCount = Math.max(
      Math.round(Number(incomingLaneCounts[direction] ?? 0)),
      incomingTotal > 0 ? 1 : 0,
    );
    const outgoingCount = Math.max(
      Math.round(Number(outgoingLaneCounts[direction] ?? 0)),
      outgoingTotal > 0 ? 1 : 0,
    );
    incomingByLane[direction] = Array.from({ length: incomingCount }, () => 0);
    outgoingByLane[direction] = Array.from({ length: outgoingCount }, () => 0);
  }

  for (const flow of laneFlows) {
    const fromDirection = flow.from_direction as Direction;
    const toDirection = flow.to_direction as Direction;
    if (!DIRECTIONS.includes(fromDirection) || !DIRECTIONS.includes(toDirection)) continue;
    const volume = Math.max(0, Number(flow.volume ?? 0));
    if (incomingByLane[fromDirection].length > 0) {
      incomingByLane[fromDirection][
        clampLaneIndex(Number(flow.from_lane ?? 1), incomingByLane[fromDirection].length)
      ] += volume;
    }
    if (outgoingByLane[toDirection].length > 0) {
      outgoingByLane[toDirection][
        clampLaneIndex(Number(flow.to_lane ?? 1), outgoingByLane[toDirection].length)
      ] += volume;
    }
  }

  for (const direction of DIRECTIONS) {
    const incomingTotal = Number(incomingTotals[direction] ?? 0);
    const outgoingTotal = Number(outgoingTotals[direction] ?? 0);
    if (
      incomingByLane[direction].length > 0 &&
      incomingByLane[direction].every((value) => value <= 0) &&
      incomingTotal > 0
    ) {
      const share = incomingTotal / incomingByLane[direction].length;
      incomingByLane[direction] = incomingByLane[direction].map(() => share);
    }
    if (
      outgoingByLane[direction].length > 0 &&
      outgoingByLane[direction].every((value) => value <= 0) &&
      outgoingTotal > 0
    ) {
      const share = outgoingTotal / outgoingByLane[direction].length;
      outgoingByLane[direction] = outgoingByLane[direction].map(() => share);
    }
  }

  return { incomingByLane, outgoingByLane };
}

function clampLaneIndex(value: number, laneCount: number) {
  return Math.min(laneCount, Math.max(1, Math.round(value))) - 1;
}

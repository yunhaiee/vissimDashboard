"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchSignals, type ScenarioMeta, type ScenarioResults, type SignalNodeInfo } from "@/lib/api";
import { useLanguage } from "@/components/LanguageProvider";
import { getScenarioDisplayColor } from "@/lib/scenarioPalette";

const CARDINAL_DIRECTIONS = ["north", "east", "south", "west"] as const;
type CardinalDirection = (typeof CARDINAL_DIRECTIONS)[number];
type TurnKind = "left" | "straight" | "right" | "uturn" | "other";
type PhaseTurnKind = "left" | "straight" | "right" | "uturn";
type PhaseMovementView = { from: CardinalDirection; turn: PhaseTurnKind };

type NodeDirectionRow = NonNullable<ScenarioResults["node_directions"]>[number];

interface Props {
  nodeId: string;
  nodeName: string;
  scenarios: ScenarioMeta[];
  selected: string[];
  results: Record<string, ScenarioResults>;
}

interface ApproachBreakdown {
  total: number;
  incomingTotal: number;
  outgoingTotal: number;
  left: number;
  straight: number;
  right: number;
  uturn: number;
  other: number;
  movementCount: number;
  estimated: boolean;
}

const DIR_SHORT: Record<CardinalDirection, string> = {
  north: "북",
  east: "동",
  south: "남",
  west: "서",
};

export default function NodeFlowPanel({ nodeId, nodeName, scenarios, selected, results }: Props) {
  const { t } = useLanguage();
  const [activeId, setActiveId] = useState<string>(selected[0] ?? "");
  const [signalNodes, setSignalNodes] = useState<SignalNodeInfo[]>([]);

  useEffect(() => {
    if (selected.length === 0) {
      setActiveId("");
      return;
    }
    if (!selected.includes(activeId)) {
      setActiveId(selected[0]);
    }
  }, [activeId, selected]);

  useEffect(() => {
    let mounted = true;
    fetchSignals()
      .then((items) => {
        if (mounted) setSignalNodes(items);
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  const scenarioData = useMemo(() => {
    const output: Record<string, NodeDirectionRow | null> = {};
    for (const id of selected) {
      output[id] = results[id]?.node_directions?.find((row) => row.node_id === nodeId) ?? null;
    }
    return output;
  }, [nodeId, results, selected]);

  const activeData = scenarioData[activeId] ?? null;
  const activeScenario = scenarios.find((scenario) => scenario.id === activeId);
  const signalInfo = useMemo(
    () => signalNodes.find((item) => item.node_id === nodeId) ?? null,
    [nodeId, signalNodes],
  );

  const approachBreakdown = useMemo(() => buildApproachBreakdown(activeData), [activeData]);

  const maxApproachTotal = useMemo(
    () =>
      Math.max(
        ...CARDINAL_DIRECTIONS.map((dir) =>
          Math.max(approachBreakdown[dir].total, approachBreakdown[dir].incomingTotal),
        ),
        1,
      ),
    [approachBreakdown],
  );

  const activeTotal = CARDINAL_DIRECTIONS.reduce(
    (sum, dir) => sum + approachBreakdown[dir].total,
    0,
  );

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto pr-1">
      <div>
        <p className="text-[12px] font-semibold uppercase tracking-widest text-slate-500">{t("교차로", "Node")}</p>
        <h2 className="mt-0.5 truncate text-base font-bold text-slate-900">{nodeName}</h2>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {selected.map((id) => {
          const scenario = scenarios.find((item) => item.id === id);
          if (!scenario) return null;
          // Index into the FULL `scenarios` list (matching the picker legend),
          // not the position within `selected` -- that position shifts
          // depending on which OTHER scenarios happen to be selected too.
          const scenarioColor = getScenarioDisplayColor(scenario, scenarios.indexOf(scenario));

          const active = id === activeId;
          return (
            <button
              key={id}
              onClick={() => setActiveId(id)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[14px] font-medium transition-colors ${
                active
                  ? "border-slate-300 bg-white/[0.06] text-white"
                  : "border-slate-200 text-slate-500 hover:text-slate-800"
              }`}
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: scenarioColor }} />
              {scenario.name}
            </button>
          );
        })}
      </div>

      {activeData ? (
        <>
          <DetailedCompass
            color={
              // Same full-list indexing as the scenario chips above --
              // selected.findIndex() here previously colored this compass by
              // the scenario's position among the SELECTED scenarios only.
              activeScenario
                ? getScenarioDisplayColor(activeScenario, scenarios.indexOf(activeScenario))
                : "#4a83c2"
            }
            nodeName={nodeName}
            total={activeTotal}
            maxApproachTotal={maxApproachTotal}
            approachBreakdown={approachBreakdown}
          />

          <SignalPhasePanelContent
            nodeId={nodeId}
            nodeName={nodeName}
            scenarios={scenarios}
            selected={selected}
            results={results}
            signalNodes={signalNodes}
            activeId={activeId}
            onChangeActiveId={setActiveId}
            showScenarioSelector={false}
            phaseLayout="stacked"
          />
        </>
      ) : (
        <div className="flex-1 rounded-xl border border-dashed border-slate-300 p-6 text-center text-[15px] text-slate-500">
          {t("선택한 시나리오에 대한 교차로 방향 데이터가 없습니다.", "No node direction data is available for the selected scenario.")}
        </div>
      )}

    </div>
  );
}

export function NodeSignalPhasePanel({
  nodeId,
  nodeName,
  scenarios,
  selected,
  results,
}: Props) {
  const [activeId, setActiveId] = useState<string>(selected[0] ?? "");
  const [signalNodes, setSignalNodes] = useState<SignalNodeInfo[]>([]);

  useEffect(() => {
    if (selected.length === 0) {
      setActiveId("");
      return;
    }
    if (!selected.includes(activeId)) {
      setActiveId(selected[0]);
    }
  }, [activeId, selected]);

  useEffect(() => {
    let mounted = true;
    fetchSignals()
      .then((items) => {
        if (mounted) setSignalNodes(items);
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <SignalPhasePanelContent
      nodeId={nodeId}
      nodeName={nodeName}
      scenarios={scenarios}
      selected={selected}
      results={results}
      signalNodes={signalNodes}
      activeId={activeId}
      onChangeActiveId={setActiveId}
      showScenarioSelector={false}
      phaseLayout="split"
    />
  );
}

function SignalPhasePanelContent({
  nodeId,
  scenarios,
  selected,
  results,
  signalNodes,
  activeId,
  onChangeActiveId,
  showScenarioSelector = true,
  phaseLayout = "split",
}: Props & {
  signalNodes: SignalNodeInfo[];
  activeId: string;
  onChangeActiveId: (id: string) => void;
  showScenarioSelector?: boolean;
  phaseLayout?: "split" | "stacked";
}) {
  const { t } = useLanguage();
  const activeData =
    activeId && nodeId
      ? results[activeId]?.node_directions?.find((row) => row.node_id === nodeId) ?? null
      : null;
  const signalInfo = useMemo(
    () => signalNodes.find((item) => item.node_id === nodeId) ?? null,
    [nodeId, signalNodes],
  );

  return (
    <section className="rounded-xl border border-slate-200 bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[15px] font-semibold tracking-tight text-slate-900">{t("신호 현시", "Signal Phase")}</p>
        {showScenarioSelector && (
          <div className="flex flex-wrap justify-end gap-1.5">
            {selected.map((id) => {
              const scenario = scenarios.find((item) => item.id === id);
              if (!scenario) return null;
              // Full-list index again (see the scenario chips above) so this
              // selector's dots match the picker legend regardless of selection.
              const scenarioColor = getScenarioDisplayColor(scenario, scenarios.indexOf(scenario));
              const active = id === activeId;
              return (
                <button
                  key={`signal-phase-${id}`}
                  onClick={() => onChangeActiveId(id)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[12px] font-medium transition-colors ${
                    active ? "border-slate-300 bg-white/[0.06] text-white" : "border-slate-200 text-slate-500 hover:text-slate-800"
                  }`}
                >
                  <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: scenarioColor }} />
                  {scenario.name}
                </button>
              );
            })}
          </div>
        )}
      </div>
      <SignalPhaseDonut signalInfo={signalInfo} laneFlows={activeData?.lane_flows ?? []} layout={phaseLayout} />
    </section>
  );
}

function SignalPhaseDonut({
  signalInfo,
  laneFlows,
  layout,
}: {
  signalInfo: SignalNodeInfo | null;
  laneFlows: NonNullable<NodeDirectionRow["lane_flows"]>;
  layout: "split" | "stacked";
}) {
  const { t } = useLanguage();
  if (!signalInfo || !signalInfo.has_signal || !signalInfo.cycle_time || signalInfo.cycle_time <= 0) {
    return (
      <p className="text-[13px] text-slate-500">
        {t("이 교차로에 표시 가능한 신호 페이즈 정보가 없습니다.", "No displayable signal phase data is available for this node.")}
      </p>
    );
  }

  const cycle = signalInfo.cycle_time;
  const sorted = [...(signalInfo.phases ?? [])]
    .map((phase, index) => ({
      index,
      start: Math.max(0, Math.min(cycle, Number(phase.start ?? 0))),
      duration: Math.max(0, Number(phase.duration ?? 0)),
      signalGroups: phase.signal_groups ?? [],
      signalGroupIds: phase.signal_group_ids ?? [],
      movements: phase.movements ?? [],
    }))
    .sort((a, b) => a.start - b.start);

  const palette = ["#5f95d5", "#9cc8a4", "#d97757", "#93a0de", "#ecac9c", "#7fc0ca", "#b5b3ab"];
  const baseStart = sorted[0]?.start ?? 0;
  const segments = sorted
    .map((phase, idx) => {
      const nextStartRaw = sorted[(idx + 1) % sorted.length]?.start ?? cycle;
      const nextStart = idx === sorted.length - 1 ? nextStartRaw + cycle : nextStartRaw;
      const windowDuration = sorted.length === 1 ? cycle : Math.max(0, nextStart - phase.start);
      const effectiveGreen = Math.min(windowDuration, Math.max(0, phase.duration));
      const startOffset = ((phase.start - baseStart + cycle) % cycle) / cycle;
      const from = startOffset * 100;
      const sweep = (windowDuration / cycle) * 100;
      const end = (phase.start + windowDuration) % cycle;
      return {
        ...phase,
        phaseLabel: idx + 1,
        windowDuration,
        effectiveGreen,
        end,
        from,
        to: from + sweep,
        pct: (windowDuration / cycle) * 100,
        color: palette[idx % palette.length],
        signalGroups: phase.signalGroups,
        signalGroupIds: phase.signalGroupIds,
        movements: normalizeSignalPhaseMovements(phase.movements ?? []),
      };
    })
    .filter((segment) => segment.windowDuration > 0);
  const fallbackPhaseMovements = buildPhaseFallbackMovements(
    laneFlows,
    segments.map((segment) => ({
      signalGroups: segment.signalGroups,
      signalGroupIds: segment.signalGroupIds ?? [],
    })),
  );

  const gradientStops: string[] = [];
  for (const segment of segments) {
    gradientStops.push(`${segment.color} ${segment.from}% ${Math.min(segment.to, 100)}%`);
  }
  const gradient = `conic-gradient(from -90deg, ${gradientStops.join(", ") || "#33332f 0% 100%"})`;

  return (
    <div>
      <p className="mb-2 text-[13px] text-slate-500">
        {cycle}s {t("주기", "cycle")} · Phase {segments.length}{t("개", "")}
      </p>

      <div
        className={
          layout === "stacked"
            ? "flex flex-col items-center gap-3"
            : "grid gap-4 md:grid-cols-[180px_minmax(0,1fr)] md:items-start md:justify-start"
        }
      >
        <div className={layout === "stacked" ? "flex justify-center" : "flex justify-center md:justify-start"}>
          <div
            className={`relative h-[150px] w-[150px] shrink-0 overflow-hidden rounded-full ${
              layout === "stacked" ? "" : "md:ml-2 md:mt-5"
            }`}
          >
            <div className="h-full w-full rounded-full border border-slate-200" style={{ background: gradient }} />
            {segments.map((segment, idx) => {
              const angle = -90 + ((segment.from + segment.to) / 2) * 3.6;
              const radius = 34;
              const x = 50 + radius * Math.cos((angle * Math.PI) / 180);
              const y = 50 + radius * Math.sin((angle * Math.PI) / 180);
              return (
                <div
                  key={`phase-pct-${idx}`}
                  className="absolute rounded bg-surface/90 px-1 text-[9px] font-semibold text-slate-700 shadow-sm"
                  style={{ left: `${x}%`, top: `${y}%`, transform: "translate(-50%, -50%)" }}
                >
                  {Math.round(segment.pct)}%
                </div>
              );
            })}
            <div className="absolute inset-[25%] flex items-center justify-center rounded-full bg-surface text-center">
              <div>
                <p className="text-[12px] font-semibold text-slate-700">{cycle}s</p>
                <p className="text-[9px] text-slate-500">Phase+IG</p>
              </div>
            </div>
          </div>
        </div>

        <div className="w-full space-y-2">
          {segments.length === 0 && (
            <p className="text-[13px] text-slate-500">{t("유효한 페이즈 구간이 없습니다.", "No valid phase segment was found.")}</p>
          )}
          {segments.length > 0 && (
            <div className="flex h-full flex-col gap-2">
              <div className="rounded-lg border border-slate-200/80 bg-surface/80 p-2">
                <div className="space-y-0.5">
                  {segments.map((segment, idx) => {
                    const wraps = segment.windowDuration > 0 && segment.start + segment.windowDuration > cycle;
                    return (
                      <div key={`phase-${idx}`} className="grid grid-cols-[auto_1fr_auto] items-center gap-1.5 rounded-md px-1 py-0.5">
                        <span className="mt-0.5 h-2 w-2 rounded-full" style={{ backgroundColor: segment.color }} />

                        <div className="min-w-0">
                          <p className="truncate text-[12px] font-semibold text-slate-700">Phase {segment.phaseLabel}</p>
                          <p className="truncate text-[9px] text-slate-500">
                            {Math.round(segment.start)}s → {Math.round(segment.end)}s
                            {wraps ? ` (${t("다음", "next")})` : ""}
                          </p>
                        </div>

                        <div className="text-right leading-tight">
                          <p className="whitespace-nowrap text-[12px] font-semibold text-slate-800">
                            {Math.round(segment.windowDuration)}s · {Math.round(segment.pct)}%
                          </p>
                          <p className="whitespace-nowrap text-[9px] text-slate-500">
                            {t("녹색", "Green")} {Math.round(segment.effectiveGreen)}s
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="flex flex-1 flex-col rounded-lg border border-slate-200/80 bg-surface/85 p-1.5">
                <p className="text-[12px] font-semibold text-slate-700">{t("페이즈별 현시", "Phase Movement")}</p>
                <div
                  className="mt-1 grid flex-1 overflow-hidden rounded-md border border-slate-200/80"
                  style={{ gridTemplateColumns: `repeat(${Math.max(segments.length, 1)}, minmax(0, 1fr))` }}
                >
                  {segments.map((segment, idx) => {
                    const inferredFromGroups = inferPhaseMovements(segment.signalGroups);
                    const rawMovements =
                      segment.movements.length > 0
                        ? segment.movements
                        : inferredFromGroups.length > 0
                          ? inferredFromGroups
                          : (fallbackPhaseMovements[idx] ?? []);
                    const display = buildMovementDisplay(rawMovements);
                    return (
                      <div
                        key={`phase-matrix-${idx}`}
                        className={`${idx < segments.length - 1 ? "border-r border-slate-200/80" : ""}`}
                      >
                        <div className="border-b border-slate-200/80 bg-slate-50 px-1 py-1 text-center text-[12px] font-semibold text-slate-700">
                          Phase {segment.phaseLabel}
                        </div>
                        <div className="flex min-h-[64px] flex-nowrap items-center justify-center overflow-hidden p-1">
                          <PhaseMovementSketchBoard phaseLabel={segment.phaseLabel} display={display} />
                        </div>
                      </div>
                    );
                  })}
                </div>
                <p className="mt-1 text-[9px] text-slate-500">
                  {t("화살표는 각 페이즈의 진행 방향(현시)을 표시합니다.", "Arrows indicate movement direction for each phase.")}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DetailedCompass({
  nodeName,
  total,
  color,
  maxApproachTotal,
  approachBreakdown,
}: {
  nodeName: string;
  total: number;
  color: string;
  maxApproachTotal: number;
  approachBreakdown: Record<CardinalDirection, ApproachBreakdown>;
}) {
  const { t } = useLanguage();
  return (
    <div className="relative">
      <div className="grid grid-cols-3 grid-rows-3 gap-2">
        <div />
        <DirectionDetailCard
          dir="north"
          color={color}
          maxApproachTotal={maxApproachTotal}
          data={approachBreakdown.north}
        />
        <div />

        <DirectionDetailCard
          dir="west"
          color={color}
          maxApproachTotal={maxApproachTotal}
          data={approachBreakdown.west}
        />
        <div className="relative flex items-center justify-center rounded-xl bg-slate-900 p-2 text-center text-ink">
          <div>
            <p className="text-[12px] font-semibold text-slate-300">{t("교차로", "Node")}</p>
            <p className="mt-0.5 text-[14px] font-bold text-white">{nodeName}</p>
            <p className="mt-0.5 text-[13px] text-slate-200">{Math.round(total).toLocaleString()} {t("대", "veh")}</p>
          </div>
        </div>
        <DirectionDetailCard
          dir="east"
          color={color}
          maxApproachTotal={maxApproachTotal}
          data={approachBreakdown.east}
        />

        <div />
        <DirectionDetailCard
          dir="south"
          color={color}
          maxApproachTotal={maxApproachTotal}
          data={approachBreakdown.south}
        />
        <div />
      </div>
    </div>
  );
}

function DirectionDetailCard({
  dir,
  data,
  color,
  maxApproachTotal,
}: {
  dir: CardinalDirection;
  data: ApproachBreakdown;
  color: string;
  maxApproachTotal: number;
}) {
  const { t } = useLanguage();
  const rows: Array<{
    key: TurnKind;
    value: number;
    target: CardinalDirection;
  }> = [
    {
      key: "left",
      value: data.left,
      target: getTargetDirection(dir, "left"),
    },
    {
      key: "straight",
      value: data.straight,
      target: getTargetDirection(dir, "straight"),
    },
    {
      key: "right",
      value: data.right,
      target: getTargetDirection(dir, "right"),
    },
  ];

  if (data.uturn > 0) {
    rows.push({ key: "uturn", value: data.uturn, target: dir });
  }

  if (data.other > 0) {
    rows.push({ key: "other", value: data.other, target: dir });
  }

  const orderedRows = orderMovementRowsForDirection(rows, dir);
  const useVerticalGauge = dir === "north" || dir === "south";

  return (
    <div className="rounded-xl border border-slate-300/80 bg-surface/95 p-2">
      <div className="mb-1.5 flex items-center justify-between">
        <p className="text-[13px] font-semibold text-slate-700">{directionLabel(dir, t)}</p>
        <p className="text-[13px] font-bold text-slate-900">{formatCompactVolume(data.total, t)}</p>
      </div>

      {useVerticalGauge ? (
        <div className="grid grid-cols-4 gap-1">
          {orderedRows.map((row) => {
            const pct = maxApproachTotal > 0 ? (row.value / maxApproachTotal) * 100 : 0;
            return (
              <div key={`${dir}-${row.key}`} className="flex min-w-0 flex-col items-center gap-0.5">
                <MovementTurnIcon from={dir} turn={row.key} />
                <span className="text-[9px] font-semibold tabular-nums text-slate-800">{Math.round(row.value)}</span>
                <div className="flex h-12 w-1.5 items-end overflow-hidden rounded-full bg-slate-200">
                  <div
                    className="w-full rounded-full"
                    style={{
                      height: `${Math.max(pct, row.value > 0 ? 8 : 0)}%`,
                      backgroundColor: color,
                      opacity: row.key === "straight" ? 1 : row.key === "left" ? 0.85 : 0.7,
                    }}
                  />
                </div>
                <span className="max-w-full truncate text-[9px] text-slate-500">{movementShortName(row.key, t)}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="space-y-0.5">
          {orderedRows.map((row) => {
            const pct = maxApproachTotal > 0 ? (row.value / maxApproachTotal) * 100 : 0;
            return (
              <div key={`${dir}-${row.key}`}>
                <div className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-1 text-[12px] text-slate-600">
                  <MovementTurnIcon from={dir} turn={row.key} />
                  <span className="truncate text-[12px] text-slate-500">{movementName(row.key, t)}</span>
                  <span className="font-semibold text-slate-800">{Math.round(row.value)}</span>
                </div>
                <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                  <div
                    className="h-1.5 rounded-full"
                    style={{
                      width: `${Math.max(pct, row.value > 0 ? 4 : 0)}%`,
                      backgroundColor: color,
                      opacity: row.key === "straight" ? 1 : row.key === "left" ? 0.85 : 0.7,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-1 text-[12px] text-slate-500">
        {t("진입", "In")} {formatCompactVolume(data.incomingTotal, t)} / {t("진출", "Out")} {formatCompactVolume(data.outgoingTotal, t)}
      </div>
    </div>
  );
}

function formatCompactVolume(value: number, t: (ko: string, en: string, ja?: string) => string): string {
  return `${Math.round(value).toLocaleString()}${t("대", " veh")}`;
}

function buildApproachBreakdown(
  row: NodeDirectionRow | null,
): Record<CardinalDirection, ApproachBreakdown> {
  const output = Object.fromEntries(
    CARDINAL_DIRECTIONS.map((dir) => [
      dir,
      {
        total: 0,
        incomingTotal: Number(row?.incoming?.[dir] ?? 0),
        outgoingTotal: Number(row?.outgoing?.[dir] ?? 0),
        left: 0,
        straight: 0,
        right: 0,
        uturn: 0,
        other: 0,
        movementCount: 0,
        estimated: false,
      } satisfies ApproachBreakdown,
    ]),
  ) as Record<CardinalDirection, ApproachBreakdown>;

  if (!row) {
    return output;
  }

  const laneFlows = row.lane_flows ?? [];

  if (laneFlows.length === 0) {
    for (const dir of CARDINAL_DIRECTIONS) {
      const fallback = output[dir].incomingTotal;
      output[dir].straight = fallback;
      output[dir].total = fallback;
      output[dir].estimated = fallback > 0;
    }
    return output;
  }

  for (const flow of laneFlows) {
    const fromDirection = normalizeDirection(flow.from_direction);
    const toDirection = normalizeDirection(flow.to_direction);

    if (!fromDirection) continue;

    const volume = Number(flow.volume ?? 0);
    const target = output[fromDirection];

    if (!toDirection) {
      target.other += volume;
      target.total += volume;
      target.movementCount += 1;
      target.estimated = target.estimated || Boolean(flow.estimated);
      continue;
    }

    const turn = getTurnKind(fromDirection, toDirection);
    target[turn] += volume;
    target.total += volume;
    target.movementCount += 1;
    target.estimated = target.estimated || Boolean(flow.estimated);
  }

  for (const dir of CARDINAL_DIRECTIONS) {
    if (output[dir].total <= 0 && output[dir].incomingTotal > 0) {
      output[dir].straight = output[dir].incomingTotal;
      output[dir].total = output[dir].incomingTotal;
      output[dir].estimated = true;
    }
  }

  return output;
}

function normalizeDirection(value: string | undefined): CardinalDirection | null {
  if (!value) return null;
  const direction = value.toLowerCase().trim();
  if ((CARDINAL_DIRECTIONS as readonly string[]).includes(direction)) {
    return direction as CardinalDirection;
  }
  return null;
}

function getTurnKind(from: CardinalDirection, to: CardinalDirection): TurnKind {
  if (from === to) return "uturn";
  const fromIndex = CARDINAL_DIRECTIONS.indexOf(from);
  const leftTarget = CARDINAL_DIRECTIONS[(fromIndex + 1) % 4];
  const straightTarget = CARDINAL_DIRECTIONS[(fromIndex + 2) % 4];
  const rightTarget = CARDINAL_DIRECTIONS[(fromIndex + 3) % 4];

  if (to === leftTarget) return "left";
  if (to === straightTarget) return "straight";
  if (to === rightTarget) return "right";
  return "other";
}

function getTargetDirection(from: CardinalDirection, turn: "left" | "straight" | "right"): CardinalDirection {
  const fromIndex = CARDINAL_DIRECTIONS.indexOf(from);
  if (turn === "left") return CARDINAL_DIRECTIONS[(fromIndex + 1) % 4];
  if (turn === "straight") return CARDINAL_DIRECTIONS[(fromIndex + 2) % 4];
  return CARDINAL_DIRECTIONS[(fromIndex + 3) % 4];
}

function directionArrow(direction: CardinalDirection): string {
  if (direction === "north") return "↑";
  if (direction === "east") return "→";
  if (direction === "south") return "↓";
  return "←";
}

function directionLabel(dir: CardinalDirection, t: (ko: string, en: string, ja?: string) => string): string {
  if (dir === "north") return t("북(N)", "North (N)");
  if (dir === "east") return t("동(E)", "East (E)");
  if (dir === "south") return t("남(S)", "South (S)");
  return t("서(W)", "West (W)");
}

function movementName(kind: TurnKind, t: (ko: string, en: string, ja?: string) => string): string {
  if (kind === "left") return t("좌회전", "Left");
  if (kind === "straight") return t("직진", "Straight");
  if (kind === "right") return t("우회전", "Right");
  if (kind === "uturn") return t("유턴", "U-turn");
  return t("기타", "Other");
}

function movementShortName(kind: TurnKind, t: (ko: string, en: string, ja?: string) => string): string {
  if (kind === "left") return t("좌", "L");
  if (kind === "straight") return t("직", "S");
  if (kind === "right") return t("우", "R");
  if (kind === "uturn") return t("유", "U");
  return t("기", "O");
}

function orderMovementRowsForDirection<T extends { key: TurnKind }>(rows: T[], dir: CardinalDirection): T[] {
  if (dir !== "east") return rows;
  const order: TurnKind[] = ["right", "straight", "left", "uturn", "other"];
  return [...rows].sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
}

function MovementTurnIcon({ from, turn }: { from: CardinalDirection; turn: TurnKind }) {
  const title =
    turn === "left"
      ? "Left turn"
      : turn === "straight"
        ? "Straight"
        : turn === "right"
          ? "Right turn"
          : turn === "uturn"
            ? "U-turn"
            : "Other movement";
  const path =
    turn === "left"
      ? "M11 12V7.6C11 5.9 9.7 4.5 8 4.5H5.2"
      : turn === "straight"
        ? "M8 13V3.8"
        : turn === "right"
          ? "M5 12V7.6C5 5.9 6.3 4.5 8 4.5h2.8"
          : turn === "uturn"
            ? "M11 12V7.2C11 5.4 9.6 4 7.8 4S4.6 5.4 4.6 7.2V12"
            : "M5 11h6";
  const head =
    turn === "left"
      ? "M5.4 2.4 3.2 4.5l2.2 2.1"
      : turn === "straight"
        ? "M5.8 6 8 3.8 10.2 6"
        : turn === "right"
          ? "M10.6 2.4l2.2 2.1-2.2 2.1"
          : turn === "uturn"
            ? "M2.6 9.8 4.6 12l2.1-2.2"
            : "M8 8h.01";

  return (
    <span className="inline-flex h-[18px] w-[18px] items-center justify-center rounded-md bg-slate-100 text-slate-900 ring-1 ring-slate-300">
      <svg
        viewBox="0 0 16 16"
        className="h-4 w-4"
        role="img"
        aria-label={title}
        style={{ transform: `rotate(${movementIconRotation(from)}deg)` }}
      >
        <title>{title}</title>
        <path
          d={path}
          fill="none"
          stroke="currentColor"
          strokeWidth="2.1"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d={head}
          fill="none"
          stroke="currentColor"
          strokeWidth="2.1"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

function movementIconRotation(from: CardinalDirection): number {
  if (from === "north") return 180;
  if (from === "west") return 90;
  if (from === "east") return -90;
  return 0;
}

function normalizeSignalPhaseMovements(
  movements: Array<{ from_direction: string; to_direction: string; turn: string }>,
): PhaseMovementView[] {
  const output: PhaseMovementView[] = [];

  for (const movement of movements) {
    const from = normalizeDirection(movement.from_direction);
    if (!from) continue;

    const rawTurn = (movement.turn || "").toLowerCase().trim();
    if (rawTurn === "left" || rawTurn === "straight" || rawTurn === "right" || rawTurn === "uturn") {
      output.push({ from, turn: rawTurn });
      continue;
    }

    const to = normalizeDirection(movement.to_direction);
    if (!to) continue;
    const inferred = getTurnKind(from, to);
    if (inferred === "left" || inferred === "straight" || inferred === "right" || inferred === "uturn") {
      output.push({ from, turn: inferred });
    }
  }

  const dedup = new Map<string, PhaseMovementView>();
  for (const item of output) {
    dedup.set(`${item.from}-${item.turn}`, item);
  }
  return Array.from(dedup.values());
}

function inferPhaseMovements(signalGroups: string[]): PhaseMovementView[] {
  const items: PhaseMovementView[] = [];

  for (const raw of signalGroups) {
    const text = raw.toLowerCase();
    const directions = parseDirections(text);
    const turns = parseTurns(text);

    if (directions.length === 0) continue;
    const resolvedTurns = turns.length > 0 ? turns : (["straight"] as PhaseTurnKind[]);

    for (const from of directions) {
      for (const turn of resolvedTurns) {
        items.push({ from, turn });
      }
    }
  }

  const dedup = new Map<string, { from: CardinalDirection; turn: PhaseTurnKind }>();
  for (const item of items) {
    dedup.set(`${item.from}-${item.turn}`, item);
  }
  return Array.from(dedup.values());
}

function parseDirections(text: string): CardinalDirection[] {
  const list: CardinalDirection[] = [];
  const push = (dir: CardinalDirection) => {
    if (!list.includes(dir)) list.push(dir);
  };

  if (/(남북|북남|north.?south|south.?north|n\/s|n-s|\bns\b)/i.test(text)) {
    push("north");
    push("south");
  }
  if (/(동서|서동|east.?west|west.?east|e\/w|e-w|\bew\b)/i.test(text)) {
    push("east");
    push("west");
  }

  if (/(북|north|\bnb\b)/i.test(text)) push("north");
  if (/(동|east|\beb\b)/i.test(text)) push("east");
  if (/(남|south|\bsb\b)/i.test(text)) push("south");
  if (/(서|west|\bwb\b)/i.test(text)) push("west");

  return list;
}

function parseTurns(text: string): PhaseTurnKind[] {
  const turns: PhaseTurnKind[] = [];
  const push = (turn: PhaseTurnKind) => {
    if (!turns.includes(turn)) turns.push(turn);
  };

  if (/(유턴|u[-\s]?turn)/i.test(text)) push("uturn");
  if (/(좌|left|\blt\b)/i.test(text)) push("left");
  if (/(직|straight|through|thru)/i.test(text)) push("straight");
  if (/(우|right|\brt\b)/i.test(text)) push("right");

  return turns;
}

function phaseMovementLabel(item: { from: CardinalDirection; turn: PhaseTurnKind }): string {
  if (item.turn === "uturn") {
    return `${DIR_SHORT[item.from]} 유턴`;
  }

  const target = getTargetDirection(item.from, item.turn);
  return `${DIR_SHORT[item.from]} ${directionArrow(target)} ${DIR_SHORT[target]}`;
}

function buildPhaseFallbackMovements(
  laneFlows: NonNullable<NodeDirectionRow["lane_flows"]>,
  phaseMeta: Array<{ signalGroups: string[]; signalGroupIds: string[] }>,
): PhaseMovementView[][] {
  const count = Math.max(phaseMeta.length, 1);
  const output: PhaseMovementView[][] = Array.from({ length: count }, () => []);
  if (!laneFlows || laneFlows.length === 0) return output;

  type TurnVolume = { straight: number; left: number; right: number; uturn: number; total: number };
  const perApproach = Object.fromEntries(
    CARDINAL_DIRECTIONS.map((dir) => [
      dir,
      { straight: 0, left: 0, right: 0, uturn: 0, total: 0 } satisfies TurnVolume,
    ]),
  ) as Record<CardinalDirection, TurnVolume>;

  for (const flow of laneFlows) {
    const from = normalizeDirection(flow.from_direction);
    const to = normalizeDirection(flow.to_direction);
    if (!from || !to) continue;

    const turn = getTurnKind(from, to);
    const volume = Number(flow.volume ?? 0);
    if (!Number.isFinite(volume) || volume <= 0) continue;
    if (turn !== "left" && turn !== "straight" && turn !== "right" && turn !== "uturn") continue;
    perApproach[from][turn] += volume;
    perApproach[from].total += volume;
  }

  const rankedApproaches = [...CARDINAL_DIRECTIONS].sort(
    (a, b) => perApproach[b].total - perApproach[a].total,
  );

  for (let idx = 0; idx < count; idx += 1) {
    const meta = phaseMeta[idx];
    const inferredDirs = inferApproachesFromSignalGroups(meta?.signalGroups ?? [], meta?.signalGroupIds ?? []);

    if (inferredDirs.length === 1) {
      output[idx] = buildPairForApproach(inferredDirs[0], perApproach);
      continue;
    }

    if (inferredDirs.length >= 2) {
      const [first, second] = [...inferredDirs]
        .sort((a, b) => perApproach[b].total - perApproach[a].total)
        .slice(0, 2);
      const opposite =
        getOppositeDirection(first) === second || getOppositeDirection(second) === first;

      if (opposite && perApproach[first].straight > 0 && perApproach[second].straight > 0) {
        output[idx] = [
          { from: first, turn: "straight" },
          { from: second, turn: "straight" },
        ];
        continue;
      }

      output[idx] = [
        pickDominantMovement(first, perApproach),
        pickDominantMovement(second, perApproach),
      ].filter((item): item is PhaseMovementView => Boolean(item));
      continue;
    }

    const fallbackDir = rankedApproaches[idx % rankedApproaches.length] ?? CARDINAL_DIRECTIONS[idx % 4];
    output[idx] = buildPairForApproach(fallbackDir, perApproach);
  }

  return output.map((items) => {
    const dedup = new Map<string, PhaseMovementView>();
    for (const item of items) {
      dedup.set(`${item.from}-${item.turn}`, item);
    }
    return Array.from(dedup.values()).slice(0, 2);
  });
}

function inferApproachesFromSignalGroups(
  groups: string[],
  groupIds: string[],
): CardinalDirection[] {
  const nums = new Set<number>();

  for (const idText of groupIds) {
    const parsed = Number.parseInt(String(idText), 10);
    if (Number.isFinite(parsed)) nums.add(parsed);
  }

  for (const group of groups) {
    const matches = String(group).match(/sg\s*([0-9]+)/gi) ?? [];
    for (const token of matches) {
      const parsed = Number.parseInt(token.replace(/[^\d]/g, ""), 10);
      if (Number.isFinite(parsed)) nums.add(parsed);
    }
  }

  const out = new Set<CardinalDirection>();
  for (const num of nums) {
    if (num === 1 || num === 2) out.add("north");
    if (num === 5 || num === 6) out.add("south");
    if (num === 3 || num === 7) out.add("east");
    if (num === 4 || num === 8) out.add("west");
  }

  return Array.from(out.values());
}

function buildPairForApproach(
  from: CardinalDirection,
  stats: Record<CardinalDirection, { straight: number; left: number; right: number; uturn: number; total: number }>,
): PhaseMovementView[] {
  const row = stats[from];
  const items: PhaseMovementView[] = [];

  if (row.straight > 0) {
    items.push({ from, turn: "straight" });
  }

  const sideTurn = row.left >= row.right ? "left" : "right";
  const sideVolume = sideTurn === "left" ? row.left : row.right;
  if (sideVolume > 0) {
    items.push({ from, turn: sideTurn });
  }

  if (items.length === 0 && row.uturn > 0) {
    items.push({ from, turn: "uturn" });
  }

  if (items.length === 1 && row.straight > 0) {
    const opposite = getOppositeDirection(from);
    if (stats[opposite].straight > 0) {
      items.push({ from: opposite, turn: "straight" });
    }
  }

  return items.slice(0, 2);
}

function pickDominantMovement(
  from: CardinalDirection,
  stats: Record<CardinalDirection, { straight: number; left: number; right: number; uturn: number; total: number }>,
): PhaseMovementView | null {
  const row = stats[from];
  const candidates: Array<{ turn: PhaseTurnKind; volume: number }> = [
    { turn: "straight", volume: row.straight },
    { turn: "left", volume: row.left },
    { turn: "right", volume: row.right },
    { turn: "uturn", volume: row.uturn },
  ];

  const best = candidates.sort((a, b) => b.volume - a.volume)[0];
  if (!best || best.volume <= 0) return null;
  return { from, turn: best.turn };
}

function buildMovementDisplay(
  movements: PhaseMovementView[],
): { items: PhaseMovementView[]; layout: "stack" | "split" | "parallel" } {
  const dedup = new Map<string, PhaseMovementView>();
  for (const movement of movements) {
    dedup.set(`${movement.from}-${movement.turn}`, movement);
  }
  let unique = Array.from(dedup.values());
  if (unique.length === 0) {
    return { items: [], layout: "stack" };
  }

  const byFrom = new Map<
    CardinalDirection,
    { straight?: PhaseMovementView; left?: PhaseMovementView; right?: PhaseMovementView; uturn?: PhaseMovementView }
  >();
  for (const item of unique) {
    const slot = byFrom.get(item.from) ?? {};
    if (item.turn === "straight") slot.straight = item;
    if (item.turn === "left") slot.left = item;
    if (item.turn === "right") slot.right = item;
    if (item.turn === "uturn") slot.uturn = item;
    byFrom.set(item.from, slot);
  }

  const straightMoves = unique.filter((item) => item.turn === "straight");
  const northStraight = straightMoves.find((item) => item.from === "north");
  const southStraight = straightMoves.find((item) => item.from === "south");
  if (northStraight && southStraight) {
    return { items: [northStraight, southStraight], layout: "parallel" };
  }

  const eastStraight = straightMoves.find((item) => item.from === "east");
  const westStraight = straightMoves.find((item) => item.from === "west");
  if (eastStraight && westStraight) {
    return { items: [eastStraight, westStraight], layout: "parallel" };
  }

  // Same-approach straight + turn pair.
  for (const dir of CARDINAL_DIRECTIONS) {
    const slot = byFrom.get(dir);
    if (!slot?.straight) continue;

    if (slot.left) {
      return { items: sortStackByVisual([slot.left, slot.straight]), layout: "stack" };
    }
    if (slot.right) {
      return { items: sortStackByVisual([slot.straight, slot.right]), layout: "stack" };
    }
  }

  const northLeft = unique.find((item) => item.from === "north" && item.turn === "left");
  const southLeft = unique.find((item) => item.from === "south" && item.turn === "left");
  if (northLeft && southLeft) {
    return { items: [northLeft, southLeft], layout: "stack" };
  }

  const northRight = unique.find((item) => item.from === "north" && item.turn === "right");
  const southRight = unique.find((item) => item.from === "south" && item.turn === "right");
  if (northRight && southRight) {
    return { items: [northRight, southRight], layout: "stack" };
  }

  const rightMove = unique.find((item) => item.turn === "right");
  const leftMove = unique.find((item) => item.turn === "left");
  if (rightMove && leftMove) {
    return { items: [rightMove, leftMove], layout: "stack" };
  }

  const sorted = [...unique].sort((a, b) => {
    const turnWeight = (turn: PhaseTurnKind) =>
      turn === "straight" ? 0 : turn === "left" ? 1 : turn === "right" ? 2 : 3;
    const diff = turnWeight(a.turn) - turnWeight(b.turn);
    if (diff !== 0) return diff;
    return CARDINAL_DIRECTIONS.indexOf(a.from) - CARDINAL_DIRECTIONS.indexOf(b.from);
  });
  const picked = sorted.slice(0, 2);
  return { items: sortStackByVisual(picked), layout: "stack" };
}

function getOppositeDirection(direction: CardinalDirection): CardinalDirection {
  const idx = CARDINAL_DIRECTIONS.indexOf(direction);
  return CARDINAL_DIRECTIONS[(idx + 2) % 4];
}

function movementExitDirection(item: PhaseMovementView): CardinalDirection {
  if (item.turn === "uturn") return item.from;
  if (item.turn === "straight") return getOppositeDirection(item.from);
  return getTargetDirection(item.from, item.turn);
}

function sortStackByVisual(items: PhaseMovementView[]): PhaseMovementView[] {
  const order: Record<CardinalDirection, number> = {
    north: 0,
    west: 1,
    east: 2,
    south: 3,
  };

  return [...items].sort((a, b) => {
    const aExit = movementExitDirection(a);
    const bExit = movementExitDirection(b);
    const byExit = order[aExit] - order[bExit];
    if (byExit !== 0) return byExit;

    const byFrom = CARDINAL_DIRECTIONS.indexOf(a.from) - CARDINAL_DIRECTIONS.indexOf(b.from);
    if (byFrom !== 0) return byFrom;

    const turnWeight = (turn: PhaseTurnKind) =>
      turn === "straight" ? 0 : turn === "left" ? 1 : turn === "right" ? 2 : 3;
    return turnWeight(a.turn) - turnWeight(b.turn);
  });
}

function PhaseMovementSketchBoard({
  phaseLabel,
  display,
}: {
  phaseLabel: number;
  display: { items: PhaseMovementView[]; layout: "stack" | "split" | "parallel" };
}) {
  if (display.items.length === 0) {
    return <span className="text-[12px] text-slate-400">-</span>;
  }

  const combined = resolveCombinedMovement(display.items);
  if (combined) {
    return (
      <svg viewBox="0 0 96 72" className="h-[72px] w-[92px]" role="img" aria-label={`Phase ${phaseLabel}`}>
        <title>{`Phase ${phaseLabel}`}</title>
        {renderCombinedMovement(combined.from, combined.branchTurn, `${phaseLabel}-combined`)}
      </svg>
    );
  }

  if (display.layout === "parallel" && display.items.length >= 2) {
    return (
      <svg viewBox="0 0 96 72" className="h-[72px] w-[92px]" role="img" aria-label={`Phase ${phaseLabel}`}>
        <title>{`Phase ${phaseLabel}`}</title>
        {renderParallelStraightSet(display.items[0], display.items[1], `${phaseLabel}-parallel`)}
      </svg>
    );
  }

  if (display.layout === "split" && display.items.length >= 2) {
    return (
      <svg viewBox="0 0 96 72" className="h-[72px] w-[92px]" role="img" aria-label={`Phase ${phaseLabel}`}>
        <title>{`Phase ${phaseLabel}`}</title>
        {renderSketchMovement(display.items[0], "left", `${phaseLabel}-left`)}
        {renderSketchMovement(display.items[1], "right", `${phaseLabel}-right`)}
      </svg>
    );
  }

  if (display.items.length === 1 && display.items[0].turn === "straight") {
    const pts = buildCombinedStraightStroke(display.items[0].from);
    return (
      <svg viewBox="0 0 96 72" className="h-[72px] w-[92px]" role="img" aria-label={`Phase ${phaseLabel}`}>
        <title>{`Phase ${phaseLabel}`}</title>
        {renderStrokeArrow(pts[0], pts[1], `${phaseLabel}-single-straight`)}
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 96 72" className="h-[72px] w-[92px]" role="img" aria-label={`Phase ${phaseLabel}`}>
      <title>{`Phase ${phaseLabel}`}</title>
      {display.items[0] ? renderSketchMovement(display.items[0], "top", `${phaseLabel}-top`) : null}
      {display.items[1] ? renderSketchMovement(display.items[1], "bottom", `${phaseLabel}-bottom`) : null}
    </svg>
  );
}

function renderParallelStraightSet(
  first: PhaseMovementView,
  second: PhaseMovementView,
  key: string,
) {
  const vertical =
    (first.from === "north" || first.from === "south") &&
    (second.from === "north" || second.from === "south");

  const strokes = vertical
    ? [
        buildParallelStraightStroke(first.from, { x: 27, y: 36 }),
        buildParallelStraightStroke(second.from, { x: 69, y: 36 }),
      ]
    : [
        buildParallelStraightStroke(first.from, { x: 48, y: 14 }),
        buildParallelStraightStroke(second.from, { x: 48, y: 58 }),
      ];

  return (
    <g key={key}>
      {strokes.map((points, index) =>
        renderStrokeArrow(points[0], points[points.length - 1], `${key}-${index}`),
      )}
    </g>
  );
}

function buildParallelStraightStroke(
  from: CardinalDirection,
  center: { x: number; y: number },
): Array<{ x: number; y: number }> {
  switch (from) {
    case "north":
      return [
        { x: center.x, y: center.y - 22 },
        { x: center.x, y: center.y + 22 },
      ];
    case "south":
      return [
        { x: center.x, y: center.y + 22 },
        { x: center.x, y: center.y - 22 },
      ];
    case "east":
      return [
        { x: center.x + 30, y: center.y },
        { x: center.x - 30, y: center.y },
      ];
    case "west":
      return [
        { x: center.x - 30, y: center.y },
        { x: center.x + 30, y: center.y },
      ];
  }
}

const PHASE_ARROW_COLOR = "#e3e2da";
const PHASE_ARROW_STROKE = 4.0;
const PHASE_ARROW_HEAD_SIZE = 8.1;
const PHASE_ARROW_HEAD_WIDTH_FACTOR = 0.6;

function renderStrokeArrow(
  from: { x: number; y: number },
  to: { x: number; y: number },
  key: string,
) {
  const arrow = buildArrowHead(from, to);
  const arrowPoints = `${arrow[0].x},${arrow[0].y} ${arrow[1].x},${arrow[1].y} ${arrow[2].x},${arrow[2].y}`;
  return (
    <g key={key}>
      <polyline
        points={`${from.x},${from.y} ${to.x},${to.y}`}
        fill="none"
        stroke={PHASE_ARROW_COLOR}
        strokeWidth={PHASE_ARROW_STROKE}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polygon points={arrowPoints} fill={PHASE_ARROW_COLOR} />
    </g>
  );
}

function resolveCombinedMovement(
  items: PhaseMovementView[],
): { from: CardinalDirection; branchTurn: "left" | "right" } | null {
  if (items.length < 2) return null;
  const [first, second] = items;
  if (first.from !== second.from) return null;

  const turns = new Set([first.turn, second.turn]);
  if (!turns.has("straight")) return null;
  if (turns.has("left")) return { from: first.from, branchTurn: "left" };
  if (turns.has("right")) return { from: first.from, branchTurn: "right" };
  return null;
}

function renderCombinedMovement(
  from: CardinalDirection,
  branchTurn: "left" | "right",
  key: string,
) {
  const straight = buildCombinedStraightStroke(from);
  const branch = buildCombinedBranchStroke(from, branchTurn);
  const branchArrow = buildArrowHead(branch[branch.length - 2], branch[branch.length - 1]);

  return (
    <g key={key}>
      {renderStrokeArrow(straight[0], straight[straight.length - 1], `${key}-straight`)}
      <polyline
        points={branch.map((p) => `${p.x},${p.y}`).join(" ")}
        fill="none"
        stroke={PHASE_ARROW_COLOR}
        strokeWidth={PHASE_ARROW_STROKE}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polygon points={branchArrow.map((p) => `${p.x},${p.y}`).join(" ")} fill={PHASE_ARROW_COLOR} />
    </g>
  );
}

function buildCombinedStraightStroke(from: CardinalDirection): Array<{ x: number; y: number }> {
  switch (from) {
    case "north":
      return [
        { x: 48, y: 12 },
        { x: 48, y: 60 },
      ];
    case "south":
      return [
        { x: 48, y: 60 },
        { x: 48, y: 12 },
      ];
    case "east":
      return [
        { x: 80, y: 36 },
        { x: 16, y: 36 },
      ];
    case "west":
      return [
        { x: 16, y: 36 },
        { x: 80, y: 36 },
      ];
  }
}

function buildCombinedBranchStroke(
  from: CardinalDirection,
  turn: "left" | "right",
): Array<{ x: number; y: number }> {
  const target = getTargetDirection(from, turn);
  switch (from) {
    case "north":
      return target === "east"
        ? [
            { x: 48, y: 36 },
            { x: 48, y: 24 },
            { x: 78, y: 24 },
          ]
        : [
            { x: 48, y: 36 },
            { x: 48, y: 24 },
            { x: 18, y: 24 },
          ];
    case "south":
      return target === "east"
        ? [
            { x: 48, y: 36 },
            { x: 48, y: 48 },
            { x: 78, y: 48 },
          ]
        : [
            { x: 48, y: 36 },
            { x: 48, y: 48 },
            { x: 18, y: 48 },
          ];
    case "east":
      return target === "north"
        ? [
            { x: 48, y: 36 },
            { x: 60, y: 36 },
            { x: 60, y: 12 },
          ]
        : [
            { x: 48, y: 36 },
            { x: 60, y: 36 },
            { x: 60, y: 60 },
          ];
    case "west":
      return target === "north"
        ? [
            { x: 48, y: 36 },
            { x: 36, y: 36 },
            { x: 36, y: 12 },
          ]
        : [
            { x: 48, y: 36 },
            { x: 36, y: 36 },
            { x: 36, y: 60 },
          ];
  }
}

function renderSketchMovement(
  movement: PhaseMovementView,
  slot: "top" | "bottom" | "left" | "right",
  key: string,
) {
  const { points, end } = buildSketchStroke(movement, slot);
  if (movement.turn === "straight") {
    return renderStrokeArrow(points[0], points[points.length - 1], key);
  }
  const arrow = buildArrowHead(points[points.length - 2], points[points.length - 1]);
  const arrowPoints = `${arrow[0].x},${arrow[0].y} ${arrow[1].x},${arrow[1].y} ${arrow[2].x},${arrow[2].y}`;
  return (
    <g key={key}>
      <polyline
        points={points.map((p) => `${p.x},${p.y}`).join(" ")}
        fill="none"
        stroke={PHASE_ARROW_COLOR}
        strokeWidth={PHASE_ARROW_STROKE}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polygon points={arrowPoints} fill={PHASE_ARROW_COLOR} />
      {movement.turn === "uturn" && (
        <circle cx={end.x} cy={end.y} r="0.9" fill={PHASE_ARROW_COLOR} />
      )}
    </g>
  );
}

function buildSketchStroke(
  movement: PhaseMovementView,
  slot: "top" | "bottom" | "left" | "right",
): {
  points: Array<{ x: number; y: number }>;
  end: { x: number; y: number };
} {
  const anchor =
    slot === "top"
      ? { x: 48, y: 21 }
      : slot === "bottom"
        ? { x: 48, y: 51 }
        : slot === "left"
          ? { x: 32, y: 36 }
          : { x: 64, y: 36 };

  const edge: Record<CardinalDirection, { x: number; y: number }> = {
    north: { x: anchor.x, y: anchor.y - 20 },
    east: { x: anchor.x + 24, y: anchor.y },
    south: { x: anchor.x, y: anchor.y + 20 },
    west: { x: anchor.x - 24, y: anchor.y },
  };

  const start = edge[movement.from];
  if (movement.turn === "uturn") {
    const end =
      movement.from === "north"
        ? { x: anchor.x - 8, y: anchor.y - 10 }
        : movement.from === "south"
          ? { x: anchor.x + 8, y: anchor.y + 10 }
          : movement.from === "east"
            ? { x: anchor.x + 10, y: anchor.y - 8 }
            : { x: anchor.x - 10, y: anchor.y + 8 };
    const bend1 =
      movement.from === "north" || movement.from === "south"
        ? { x: anchor.x, y: anchor.y }
        : { x: anchor.x, y: anchor.y };
    const bend2 =
      movement.from === "north"
        ? { x: anchor.x - 8, y: anchor.y }
        : movement.from === "south"
          ? { x: anchor.x + 8, y: anchor.y }
          : movement.from === "east"
            ? { x: anchor.x, y: anchor.y - 8 }
            : { x: anchor.x, y: anchor.y + 8 };
    return { points: [start, bend1, bend2, end], end };
  }

  const target = edge[getTargetDirection(movement.from, movement.turn)];
  if (movement.turn === "straight") {
    return { points: [start, target], end: target };
  }

  const bend =
    movement.from === "north" || movement.from === "south"
      ? { x: start.x, y: anchor.y }
      : { x: anchor.x, y: start.y };
  return { points: [start, bend, target], end: target };
}

function buildArrowHead(
  from: { x: number; y: number },
  to: { x: number; y: number },
): Array<{ x: number; y: number }> {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.max(Math.hypot(dx, dy), 0.001);
  const ux = dx / len;
  const uy = dy / len;
  const size = PHASE_ARROW_HEAD_SIZE;
  const backX = to.x - ux * size;
  const backY = to.y - uy * size;
  const perpX = -uy * (size * PHASE_ARROW_HEAD_WIDTH_FACTOR);
  const perpY = ux * (size * PHASE_ARROW_HEAD_WIDTH_FACTOR);
  return [
    { x: to.x, y: to.y },
    { x: backX + perpX, y: backY + perpY },
    { x: backX - perpX, y: backY - perpY },
  ];
}

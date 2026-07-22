"use client";

import { useEffect, useMemo, useState } from "react";
import { useLanguage } from "@/components/LanguageProvider";

import type { Incident, LinkFeature, NodeConnection, NodeFeature } from "./MapViewer";

interface Props {
  link: LinkFeature | null;
  node: NodeFeature | null;
  incidents: Incident[];
  onSelectLink: (link: LinkFeature) => void;
  onAdd: (incident: Incident) => void;
  onRemove: (linkId: string) => void;
  onClearAll?: () => void;
  onRunScenario: () => void;
  running: boolean;
  progress: number;
  history?: Array<{ scenarioName: string; jobId: string }>;
}

export default function IncidentPanel({
  link,
  node,
  incidents,
  onSelectLink,
  onAdd,
  onRemove,
  onClearAll,
  onRunScenario,
  running,
  progress,
  history = [],
}: Props) {
  const { t } = useLanguage();
  const [type, setType] = useState<Incident["type"]>("lane_closure");
  const [closedLanes, setClosedLanes] = useState(1);
  const [speedLimit, setSpeedLimit] = useState(30);
  const [duration, setDuration] = useState(300);

  const existingIncident = link ? incidents.find((incident) => incident.linkId === link.id) : null;
  const groupedConnections = useMemo(() => {
    const groups: Record<string, NodeConnection[]> = {
      north: [],
      east: [],
      south: [],
      west: [],
    };
    for (const connection of node?.connectedLinks ?? []) {
      groups[connection.direction].push(connection);
    }
    return groups;
  }, [node]);
  const queueStats = useMemo(() => {
    const summary = {
      lane_closure: 0,
      full_closure: 0,
      speed_limit: 0,
    };
    for (const incident of incidents) {
      summary[incident.type] += 1;
    }
    return summary;
  }, [incidents]);
  const incidentTypeOptions = useMemo(
    () => [
      {
        value: "lane_closure" as const,
        label: t("차로 차단", "Lane Closure"),
        desc: t("선택 링크의 사용 가능한 차로를 줄입니다.", "Reduce available lanes on the selected link."),
      },
      {
        value: "full_closure" as const,
        label: t("전면 차단", "Full Closure"),
        desc: t("선택 링크를 완전히 차단합니다.", "Block the selected link completely."),
      },
      {
        value: "speed_limit" as const,
        label: t("속도 제한", "Speed Limit"),
        desc: t("선택 링크의 목표 속도를 낮춥니다.", "Lower the speed target on the selected link."),
      },
    ],
    [t],
  );

  useEffect(() => {
    if (!link) {
      return;
    }
    setClosedLanes(Math.min(closedLanes, link.lanes));
  }, [closedLanes, link]);

  const handleQueue = () => {
    if (!link) {
      return;
    }

    onAdd({
      linkId: link.id,
      type,
      closedLanes: type === "lane_closure" ? closedLanes : link.lanes,
      totalLanes: link.lanes,
      speedLimit,
      duration,
    });
  };

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-3xl border border-slate-200/80 bg-surface/95 shadow-[0_22px_48px_rgba(15,23,42,0.16)] backdrop-blur">
      <div className="border-b border-slate-200/80 px-6 py-5">
        <div className="text-sm font-semibold text-slate-900">{t("시나리오 큐", "Scenario Queue")}</div>
        <p className="mt-1 text-xs text-slate-600">
          {t("노드를 선택하고 링크를 고른 뒤, 변경사항을 큐에 추가하세요.", "Pick a node, select one of its links, then add changes to the modification queue.")}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
        <section className="rounded-2xl border border-slate-200/80 bg-slate-50/85 p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("노드 정보", "Node Details")}</div>
          {node ? (
            <div className="mt-3 space-y-3">
                <div>
                  <div className="text-lg font-semibold text-slate-900">{node.name}</div>
                  <div className="text-xs text-slate-500">{node.connectedLinks.length} {t("개 링크 연결", "connected links")}</div>
                </div>
              <NodeMiniMap
                groupedConnections={groupedConnections}
                selectedLinkId={link?.id ?? null}
                onSelectLink={onSelectLink}
              />
              <div className="space-y-2">
                {node.connectedLinks.map((connectedLink) => {
                  const active = link?.id === connectedLink.id;
                  return (
                    <button
                      key={connectedLink.id}
                      onClick={() => onSelectLink(connectedLink)}
                      className={`w-full rounded-2xl border px-4 py-3 text-left transition-colors ${
                        active
                          ? "border-blue-200 bg-blue-50 text-slate-800"
                          : "border-slate-200/80 bg-surface text-slate-700 hover:border-slate-400"
                      }`}
                    >
                      <div className="text-sm font-semibold">Link {connectedLink.id}</div>
                      <div className="mt-1 text-xs text-slate-600">
                        {connectedLink.lanes} {t("차로", "lanes")} · {connectedLink.length.toFixed(1)} m
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="mt-3 rounded-2xl border border-dashed border-slate-200 bg-surface px-4 py-6 text-center text-sm text-slate-400">
              {t("그래프 노드를 클릭해 연결 링크를 확인하세요.", "Click a graph node to inspect its connected links.")}
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-slate-200/80 bg-surface p-4 shadow-sm">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("선택 링크", "Selected Link")}</div>
          {link ? (
            <div className="mt-3 space-y-4">
              <div className="rounded-2xl border border-blue-200/80 bg-blue-50 px-4 py-3">
                <div className="text-sm font-semibold text-slate-800">Link {link.id}</div>
                <div className="mt-1 text-xs text-slate-600">
                  {link.lanes} {t("차로", "lanes")} · {link.length.toFixed(1)} m
                </div>
              </div>

              <div className="space-y-2">
                {incidentTypeOptions.map((option) => (
                  <label
                    key={option.value}
                    className={`block rounded-2xl border px-4 py-3 transition-colors ${
                      type === option.value
                        ? "border-blue-200 bg-blue-50"
                        : "border-slate-200 hover:border-slate-400"
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <input
                        type="radio"
                        name="incident-type"
                        value={option.value}
                        checked={type === option.value}
                        onChange={() => setType(option.value)}
                        className="mt-1 accent-[#5f7fa4]"
                      />
                      <div>
                        <div className="text-sm font-semibold text-slate-900">{option.label}</div>
                        <div className="mt-1 text-xs text-slate-600">{option.desc}</div>
                      </div>
                    </div>
                  </label>
                ))}
              </div>

              <div className="grid gap-3">
                {type === "lane_closure" && (
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">{t("차단 차로 수", "Closed lanes")}</span>
                    <div className="mt-2 flex items-center gap-3">
                      <input
                        type="range"
                        min={1}
                        max={link.lanes}
                        value={closedLanes}
                        onChange={(event) => setClosedLanes(Number(event.target.value))}
                        className="flex-1"
                      />
                      <span className="w-6 text-center text-sm font-semibold text-slate-900">{closedLanes}</span>
                    </div>
                  </label>
                )}

                {type === "speed_limit" && (
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">{t("속도 제한 (km/h)", "Speed limit (km/h)")}</span>
                    <input
                      type="number"
                      min={10}
                      max={80}
                      value={speedLimit}
                      onChange={(event) => setSpeedLimit(Number(event.target.value))}
                      className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
                    />
                  </label>
                )}

                <label className="block">
                  <span className="text-xs font-medium text-slate-500">{t("지속 시간 (s)", "Duration (s)")}</span>
                  <input
                    type="number"
                    min={60}
                    max={1800}
                  value={duration}
                  onChange={(event) => setDuration(Number(event.target.value))}
                  className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
                />
              </label>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={handleQueue}
                  className="ui-button-primary flex-1 px-4 py-3 text-sm"
                >
                  {existingIncident ? t("큐 항목 업데이트", "Update Queue Item") : t("수정 큐에 추가", "Add to Modification Queue")}
                </button>
                {existingIncident && (
                  <button
                    onClick={() => onRemove(link.id)}
                    className="ui-button-danger px-4 py-3 text-sm"
                  >
                    {t("삭제", "Remove")}
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="mt-3 rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-400">
              {t("연결 링크를 선택해 변경사항을 설정하세요.", "Select a connected link to configure a modification.")}
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-slate-200/80 bg-surface p-4 shadow-sm">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("수정 큐", "Modification Queue")}</div>
            {onClearAll && incidents.length > 0 && (
              <button
                onClick={onClearAll}
                className="text-[11px] font-semibold text-slate-500 transition-colors hover:text-slate-800"
              >
                {t("전체 삭제", "Clear all")}
              </button>
            )}
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
            <QueueMiniStat label={t("차로", "Lane")} value={queueStats.lane_closure} tone="amber" />
            <QueueMiniStat label={t("전면", "Full")} value={queueStats.full_closure} tone="rose" />
            <QueueMiniStat label={t("속도", "Speed")} value={queueStats.speed_limit} tone="cyan" />
          </div>
          <div className="mt-3 space-y-2">
            {incidents.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-400">
                {t("대기 중인 수정사항이 없습니다.", "No queued modifications yet.")}
              </div>
            )}
            {incidents.map((incident) => (
              <div key={incident.linkId} className="flex items-center justify-between rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3">
                <div>
                  <div className="text-sm font-semibold text-amber-900">Link {incident.linkId}</div>
                  <div className="mt-1 text-xs text-amber-700">
                    {incident.type === "lane_closure"
                      ? t(`${incident.closedLanes}/${incident.totalLanes}개 차로 차단`, `${incident.closedLanes}/${incident.totalLanes} lanes closed`, `${incident.closedLanes}/${incident.totalLanes} 車線封鎖`)
                      : incident.type === "full_closure"
                        ? t("전면 차단", "Full closure")
                        : t(`속도 제한 ${incident.speedLimit} km/h`, `Speed limit ${incident.speedLimit} km/h`, `速度制限 ${incident.speedLimit} km/h`)}
                  </div>
                </div>
                <button
                  onClick={() => onRemove(incident.linkId)}
                  className="text-sm font-semibold text-amber-700 hover:text-amber-900"
                >
                  {t("삭제", "Remove")}
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200/80 bg-surface p-4 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("시나리오 이력", "Scenario History")}</div>
            <div className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-500">
              {history.length}
            </div>
          </div>
          <div className="mt-3 space-y-2">
            {history.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-5 text-center text-sm text-slate-400">
                {t("완료된 시나리오 실행 이력이 여기에 표시됩니다.", "Completed scenario runs will appear here.")}
              </div>
            )}
            {history.map((item) => (
              <div key={`${item.scenarioName}-${item.jobId}`} className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
                <div className="text-sm font-semibold text-slate-800">{item.scenarioName}</div>
                <div className="mt-1 font-mono text-xs text-slate-500">{item.jobId}</div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="border-t border-slate-200/80 px-6 py-5">
        <button
          onClick={onRunScenario}
          disabled={incidents.length === 0 || running}
          className="ui-button-primary w-full px-4 py-3 text-sm disabled:cursor-not-allowed"
        >
          {running ? t(`변경사항 적용 중... ${progress}%`, `Applying Changes... ${progress}%`, `変更を適用中... ${progress}%`) : t("모든 변경 적용", "Apply All Changes")}
        </button>
      </div>
    </div>
  );
}

function NodeMiniMap({
  groupedConnections,
  selectedLinkId,
  onSelectLink,
}: {
  groupedConnections: Record<string, NodeConnection[]>;
  selectedLinkId: string | null;
  onSelectLink: (link: LinkFeature) => void;
}) {
  const { t } = useLanguage();
  return (
    <div className="rounded-3xl border border-slate-100 bg-surface p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("노드 다이어그램", "Node Diagram")}</div>
      <div className="relative mt-4 mx-auto h-48 w-48">
        <DirectionStack
          title={t("북", "North")}
          className="absolute left-1/2 top-0 -translate-x-1/2"
          items={groupedConnections.north}
          selectedLinkId={selectedLinkId}
          onSelectLink={onSelectLink}
        />
        <DirectionStack
          title={t("서", "West")}
          className="absolute left-0 top-1/2 -translate-y-1/2"
          items={groupedConnections.west}
          selectedLinkId={selectedLinkId}
          onSelectLink={onSelectLink}
          horizontal
        />
        <DirectionStack
          title={t("동", "East")}
          className="absolute right-0 top-1/2 -translate-y-1/2"
          items={groupedConnections.east}
          selectedLinkId={selectedLinkId}
          onSelectLink={onSelectLink}
          horizontal
        />
        <DirectionStack
          title={t("남", "South")}
          className="absolute left-1/2 bottom-0 -translate-x-1/2"
          items={groupedConnections.south}
          selectedLinkId={selectedLinkId}
          onSelectLink={onSelectLink}
        />

        <div className="absolute inset-1/2 h-16 w-16 -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-blue-200 bg-blue-50" />
      </div>
    </div>
  );
}

function DirectionStack({
  title,
  className,
  items,
  selectedLinkId,
  onSelectLink,
  horizontal = false,
}: {
  title: string;
  className: string;
  items: NodeConnection[];
  selectedLinkId: string | null;
  onSelectLink: (link: LinkFeature) => void;
  horizontal?: boolean;
}) {
  return (
    <div className={className}>
      <div className="mb-1 text-center text-[10px] font-semibold uppercase tracking-wide text-slate-400">{title}</div>
      <div className={`flex ${horizontal ? "flex-row" : "flex-col"} gap-1.5`}>
        {items.map((item) => {
          const selected = item.id === selectedLinkId;
          return (
            <button
              key={item.id}
              onClick={() => onSelectLink(item)}
              className={`rounded-lg border px-2 py-1 text-[10px] font-semibold transition-colors ${
                selected
                  ? "border-blue-500 bg-blue-500 text-white"
                  : "border-slate-200 bg-surface text-slate-600 hover:border-slate-300"
              }`}
            >
              {item.id}
            </button>
          );
        })}
        {items.length === 0 && <div className="h-7 w-7 rounded-lg border border-dashed border-slate-200 bg-slate-50" />}
      </div>
    </div>
  );
}

function QueueMiniStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "amber" | "rose" | "cyan";
}) {
  const toneClass =
    tone === "rose"
      ? "border-rose-100 bg-rose-50 text-rose-700"
      : tone === "amber"
        ? "border-amber-100 bg-amber-50 text-amber-700"
        : "border-cyan-100 bg-cyan-50 text-cyan-700";

  return (
    <div className={`rounded-xl border px-2 py-1.5 text-center ${toneClass}`}>
      <div className="text-[10px] font-medium uppercase tracking-wide">{label}</div>
      <div className="mt-0.5 text-sm font-bold">{value}</div>
    </div>
  );
}


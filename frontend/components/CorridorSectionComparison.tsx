"use client";

import { useEffect, useMemo, useState } from "react";

import { resolveSelection } from "@/lib/asyncResource";

import type { ScenarioMeta, ScenarioResults } from "@/lib/api";

type MetricKey = "speed" | "delay" | "travelTime";
type SortKey = "objectId" | "spread";
const TOP_SEGMENT_COUNT = 5;

interface Props {
  scenarios: ScenarioMeta[];
  selected: string[];
  results: Record<string, ScenarioResults>;
}

const METRICS: Record<
  MetricKey,
  {
    label: string;
    unit: string;
    extract: (result: ScenarioResults, segmentId: string) => number | null;
  }
> = {
  speed: {
    label: "?? ??",
    unit: "km/h",
    extract: (result, segmentId) => {
      const row = result.datacollection.find((item) => String(item.OBJECT_ID) === segmentId);
      return row?.SPEEDAVGARITH_MEAN != null ? Number(row.SPEEDAVGARITH_MEAN) : null;
    },
  },
  delay: {
    label: "?? ??",
    unit: "s",
    extract: (result, segmentId) => {
      const row = result.datacollection.find((item) => String(item.OBJECT_ID) === segmentId);
      return row?.QUEUEDELAY_MEAN != null ? Number(row.QUEUEDELAY_MEAN) : null;
    },
  },
  travelTime: {
    label: "?? ??",
    unit: "s",
    extract: (result, segmentId) => {
      const row = result.traveltime.find((item) => String(item.OBJECT_ID) === segmentId);
      return row?.TRAVTM_MEAN != null ? Number(row.TRAVTM_MEAN) : null;
    },
  },
};

function formatValue(value: number | null) {
  if (value == null || Number.isNaN(value)) {
    return "-";
  }
  return value.toFixed(1);
}

function compareSegmentIds(left: string, right: string) {
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  const leftIsNumeric = Number.isFinite(leftNumber) && left.trim() !== "";
  const rightIsNumeric = Number.isFinite(rightNumber) && right.trim() !== "";

  if (leftIsNumeric && rightIsNumeric) {
    return leftNumber - rightNumber;
  }

  if (leftIsNumeric) return -1;
  if (rightIsNumeric) return 1;
  return left.localeCompare(right, undefined, { numeric: true });
}

export default function CorridorSectionComparison({ scenarios, selected, results }: Props) {
  const [metric, setMetric] = useState<MetricKey>("speed");
  const [sortBy, setSortBy] = useState<SortKey>("objectId");
  const [activeSegmentIdIntent, setActiveSegmentId] = useState<string | null>(null);
  const activeScenarios = useMemo(
    () => scenarios.filter((scenario) => selected.includes(scenario.id)),
    [scenarios, selected],
  );

  const segmentIds = useMemo(() => {
    const ids = new Set<string>();
    for (const scenario of activeScenarios) {
      const result = results[scenario.id];
      if (!result) continue;
      for (const row of result.traveltime) {
        ids.add(String(row.OBJECT_ID));
      }
      for (const row of result.datacollection) {
        ids.add(String(row.OBJECT_ID));
      }
    }
    return Array.from(ids);
  }, [activeScenarios, results]);

  const rows = useMemo(() => {
    const extractor = METRICS[metric].extract;
    return segmentIds
      .map((segmentId) => {
        const values = activeScenarios.map((scenario) => ({
          scenario,
          value: results[scenario.id] ? extractor(results[scenario.id], segmentId) : null,
        }));
        const numbers = values
          .map((item) => item.value)
          .filter((value): value is number => value != null);
        const spread = numbers.length > 0 ? Math.max(...numbers) - Math.min(...numbers) : 0;
        return { segmentId, values, spread };
      })
      .sort((left, right) => {
        if (sortBy === "spread") {
          const spreadDiff = right.spread - left.spread;
          if (spreadDiff !== 0) return spreadDiff;
        }
        return compareSegmentIds(left.segmentId, right.segmentId);
      });
  }, [activeScenarios, metric, results, segmentIds, sortBy]);

  const topSegments = useMemo(
    () => [...rows].sort((left, right) => right.spread - left.spread).slice(0, TOP_SEGMENT_COUNT),
    [rows],
  );

  // Resolved during render rather than corrected by an effect one frame later.
  const activeSegmentId = resolveSelection(
    activeSegmentIdIntent,
    topSegments,
    (row) => row.segmentId,
  );

  if (activeScenarios.length === 0) return null;

  return (
    <div className="space-y-3.5">
      <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-3.5 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {(Object.keys(METRICS) as MetricKey[]).map((key) => (
                <button
                  key={key}
                  onClick={() => setMetric(key)}
                  className={`rounded-full border px-3 py-1.5 text-[14px] font-semibold transition-colors ${
                    metric === key
                      ? "border-slate-900 bg-slate-900 text-ink"
                      : "border-slate-200 bg-surface text-slate-600 hover:border-slate-300 hover:text-slate-800"
                  }`}
                >
                  {METRICS[key].label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] font-semibold uppercase tracking-[0.18em] text-slate-400">??</span>
              {(
                [
                  { key: "objectId", label: "???? ??" },
                  { key: "spread", label: "?? ? ?" },
                ] as const
              ).map((option) => (
                <button
                  key={option.key}
                  onClick={() => setSortBy(option.key)}
                  className={`rounded-full border px-3 py-1.5 text-[14px] font-semibold transition-colors ${
                    sortBy === option.key
                      ? "border-slate-900 bg-slate-900 text-ink"
                      : "border-slate-200 bg-surface text-slate-600 hover:border-slate-300 hover:text-slate-800"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <p className="max-w-sm text-[14px] leading-5 text-slate-500">
            ??? ? ???? ??? ????, ?? ?? ?? ?? ? ??? ??????.
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-[13px] font-semibold uppercase tracking-[0.18em] text-amber-700">?? ? ??</p>
          <span className="text-[13px] text-amber-700">?? {TOP_SEGMENT_COUNT}</span>
        </div>
        {topSegments.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {topSegments.map((row, index) => (
              <button
                key={`top-segment-${row.segmentId}`}
                onClick={() => setActiveSegmentId(row.segmentId)}
                className={`rounded-full border px-3 py-1 text-[14px] font-semibold transition ${
                  activeSegmentId === row.segmentId
                    ? "border-amber-500 bg-amber-500 text-white"
                    : "border-amber-200 bg-surface text-amber-700 hover:border-amber-300"
                }`}
              >
                #{index + 1} {row.segmentId} (?? {row.spread.toFixed(1)})
              </button>
            ))}
          </div>
        ) : (
          <p className="text-[14px] text-amber-700/80">??? ?? ??? ????.</p>
        )}
      </div>

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-surface">
        <table className="w-full min-w-[760px] text-[15px]">
          <thead className="bg-slate-50/90">
            <tr>
              <th className="px-3 py-2.5 text-left text-[13px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                ??
              </th>
              {activeScenarios.map((scenario) => (
                <th
                  key={scenario.id}
                  className="px-3 py-2.5 text-right text-[13px] font-semibold uppercase tracking-[0.18em]"
                  style={{ color: scenario.color }}
                >
                  {scenario.name}
                </th>
              ))}
              <th className="px-3 py-2.5 text-right text-[13px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                ??
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 30).map((row) => (
              <tr
                key={row.segmentId}
                className={`border-t border-slate-100 ${activeSegmentId === row.segmentId ? "bg-amber-50/60" : ""}`}
              >
                <td className="px-3 py-2.5 font-mono text-slate-700">{row.segmentId}</td>
                {row.values.map((item) => (
                  <td key={`${row.segmentId}-${item.scenario.id}`} className="px-3 py-2.5 text-right text-slate-800">
                    {formatValue(item.value)}
                  </td>
                ))}
                <td className="px-3 py-2.5 text-right font-semibold text-slate-700">
                  {row.spread.toFixed(1)} {METRICS[metric].unit}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={activeScenarios.length + 2} className="px-4 py-10 text-center text-[15px] text-slate-400">
                  ?? ?? ???? ????.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-[14px] text-slate-500">?? ??: {METRICS[metric].label} ({METRICS[metric].unit})</p>
    </div>
  );
}

"use client";
import { ScenarioMeta, ScenarioResults } from "@/lib/api";

interface Props {
  scenarios: ScenarioMeta[];
  selected: string[];
  results: Record<string, ScenarioResults>;
}

const KPI_DEFS = [
  { key: "DELAYAVG", label: "평균지연", unit: "초", lower: true },
  { key: "SPEEDAVG", label: "평균속도", unit: "km/h", lower: false },
  { key: "STOPSAVG", label: "정지횟수", unit: "회", lower: true },
  { key: "TRAVTMTOT", label: "총통행시간", unit: "초", lower: true },
];

export default function KpiCards({ scenarios, selected, results }: Props) {
  const active = scenarios.filter((s) => selected.includes(s.id));
  if (active.length === 0) return null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {KPI_DEFS.map((kpi) => (
        <div key={kpi.key} className="bg-surface rounded-xl border border-slate-100 shadow-sm p-4">
          <p className="text-xs text-slate-500 font-medium mb-2">{kpi.label}</p>
          <div className="space-y-1.5">
            {active.map((s) => {
              const val = results[s.id]?.network?.[kpi.key];
              return (
                <div key={s.id} className="flex items-center justify-between gap-2">
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: s.color }} />
                  <span className="text-xs text-slate-500 flex-1 truncate">{s.name}</span>
                  <span className="text-base font-semibold tabular-nums text-slate-900">
                    {val != null ? val.toFixed(1) : "—"}
                    <span className="text-xs font-normal text-slate-400 ml-0.5">{kpi.unit}</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

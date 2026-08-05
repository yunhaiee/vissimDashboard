"use client";
import { ScenarioMeta, ScenarioResults } from "@/lib/api";

interface Props {
  scenarios: ScenarioMeta[];
  selected: string[];
  results: Record<string, ScenarioResults>;
}

// Units match app/dashboard/page.tsx's KPI_METRICS (UX #24, unit 1.5 D6).
// TRAVTMTOT is a whole-network TOTAL: Vissim emits it in seconds, and it is
// reported in VEHICLE-HOURS, so it carries a display divisor. DELAYAVG and
// STOPSAVG are per-vehicle AVERAGES and are deliberately NOT converted — only
// their units are spelled out per-vehicle, as on the main dashboard.
const SECONDS_PER_HOUR = 3600;

const KPI_DEFS = [
  { key: "DELAYAVG", label: "평균지연", unit: "초/대", lower: true, displayDivisor: 1 },
  { key: "SPEEDAVG", label: "평균속도", unit: "km/h", lower: false, displayDivisor: 1 },
  { key: "STOPSAVG", label: "정지횟수", unit: "회/대", lower: true, displayDivisor: 1 },
  { key: "TRAVTMTOT", label: "총통행시간", unit: "대·시", lower: true, displayDivisor: SECONDS_PER_HOUR },
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
              const raw = results[s.id]?.network?.[kpi.key];
              const val = raw != null ? raw / kpi.displayDivisor : raw;
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

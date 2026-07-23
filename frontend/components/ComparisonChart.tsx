"use client";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  RadarChart, PolarGrid, PolarAngleAxis, Radar,
} from "recharts";
import { ScenarioMeta, ScenarioResults } from "@/lib/api";

interface Props {
  scenarios: ScenarioMeta[];
  selected: string[];
  results: Record<string, ScenarioResults>;
}

const METRICS = [
  { key: "DELAYAVG", label: "평균지연(초)", unit: "초" },
  { key: "SPEEDAVG", label: "평균속도(km/h)", unit: "km/h" },
  { key: "STOPSAVG", label: "정지횟수", unit: "회" },
  { key: "DELAYSTOPAVG", label: "정지지연(초)", unit: "초" },
];

export default function ComparisonChart({ scenarios, selected, results }: Props) {
  const activeMetas = scenarios.filter((s) => selected.includes(s.id));

  const barData = METRICS.map((m) => {
    const entry: Record<string, string | number> = { metric: m.label };
    activeMetas.forEach((s) => {
      const val = results[s.id]?.network?.[m.key];
      entry[s.name] = val != null ? +val.toFixed(2) : 0;
    });
    return entry;
  });

  // Radar: normalize each metric 0-1 across selected scenarios
  const radarMetrics = ["DELAYAVG", "SPEEDAVG", "STOPSAVG", "DELAYSTOPAVG", "TRAVTMTOT"];
  const radarLabels = ["지연", "속도", "정지", "정지지연", "통행시간"];
  const radarData = radarMetrics.map((key, i) => {
    const vals = activeMetas.map((s) => results[s.id]?.network?.[key] ?? 0);
    const max = Math.max(...vals) || 1;
    const entry: Record<string, string | number> = { metric: radarLabels[i] };
    activeMetas.forEach((s) => {
      const val = results[s.id]?.network?.[key] ?? 0;
      entry[s.name] = +(val / max * 100).toFixed(1);
    });
    return entry;
  });

  if (activeMetas.length === 0) {
    return <p className="text-slate-400 text-sm">시나리오를 선택하세요.</p>;
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
      <div>
        <h3 className="text-sm font-semibold text-slate-600 mb-3">지표별 비교 (막대)</h3>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={barData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#33332f" />
            <XAxis dataKey="metric" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {activeMetas.map((s) => (
              <Bar key={s.id} dataKey={s.name} fill={s.color} radius={[4, 4, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-slate-600 mb-3">다지표 레이더 (정규화 0-100)</h3>
        <ResponsiveContainer width="100%" height={280}>
          <RadarChart data={radarData}>
            <PolarGrid stroke="#33332f" />
            <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11 }} />
            {activeMetas.map((s) => (
              <Radar key={s.id} name={s.name} dataKey={s.name} stroke={s.color} fill={s.color} fillOpacity={0.18} />
            ))}
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Tooltip />
          </RadarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

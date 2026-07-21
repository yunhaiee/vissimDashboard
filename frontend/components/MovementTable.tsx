"use client";
import { ScenarioMeta, ScenarioResults } from "@/lib/api";

interface Props {
  scenarios: ScenarioMeta[];
  selected: string[];
  results: Record<string, ScenarioResults>;
}

export default function MovementTable({ scenarios, selected, results }: Props) {
  const active = scenarios.filter((s) => selected.includes(s.id));
  if (active.length === 0) return null;

  const allIds = new Set<string>();
  active.forEach((s) => {
    results[s.id]?.movements?.forEach((m) => allIds.add(String(m.OBJECT_ID)));
  });
  const ids = Array.from(allIds).sort((a, b) => Number(a) - Number(b));

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-100 shadow-sm">
      <table className="w-full text-xs">
        <thead className="bg-slate-50 text-slate-600">
          <tr>
            <th className="px-3 py-2 text-left font-semibold">Movement ID</th>
            {active.map((s) => (
              <th key={s.id} colSpan={2} className="px-3 py-2 text-center font-semibold" style={{ color: s.color }}>
                {s.name}
              </th>
            ))}
          </tr>
          <tr className="text-slate-400">
            <th className="px-3 pb-2" />
            {active.flatMap((s) => [
              <th key={s.id + "_d"} className="px-2 pb-2 text-center">지연(초)</th>,
              <th key={s.id + "_s"} className="px-2 pb-2 text-center">정지횟수</th>,
            ])}
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-slate-50">
          {ids.slice(0, 30).map((id) => (
            <tr key={id} className="hover:bg-slate-50 transition-colors">
              <td className="px-3 py-1.5 font-mono text-slate-700">{id}</td>
              {active.flatMap((s) => {
                const m = results[s.id]?.movements?.find((x) => String(x.OBJECT_ID) === id);
                return [
                  <td key={s.id + "_d"} className="px-2 py-1.5 text-center text-slate-600">
                    {m?.VEHDELAY_MEAN != null ? (m.VEHDELAY_MEAN as number).toFixed(1) : "—"}
                  </td>,
                  <td key={s.id + "_s"} className="px-2 py-1.5 text-center text-slate-600">
                    {m?.STOPS_MEAN != null ? (m.STOPS_MEAN as number).toFixed(2) : "—"}
                  </td>,
                ];
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {ids.length > 30 && (
        <p className="text-center text-xs text-slate-400 py-2">... 총 {ids.length}개 중 30개 표시</p>
      )}
    </div>
  );
}

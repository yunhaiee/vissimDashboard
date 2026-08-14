"use client";
import { ScenarioMeta } from "@/lib/api";
import { getScenarioDisplayColor } from "@/lib/scenarioPalette";

interface Props {
  scenarios: ScenarioMeta[];
  selected: string[];
  onChange: (selected: string[]) => void;
}

export default function ScenarioSelector({ scenarios, selected, onChange }: Props) {
  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
  };

  return (
    <div className="flex gap-3 flex-wrap">
      {scenarios.map((s, index) => {
        const active = selected.includes(s.id);
        const scenarioColor = getScenarioDisplayColor(s, index);
        return (
          <button
            key={s.id}
            onClick={() => toggle(s.id)}
            className={`px-4 py-2 rounded-full text-[15px] font-semibold border transition-all duration-200 ${
              active
                ? "text-white shadow-[0_12px_24px_rgba(15,23,42,0.2)]"
                : "bg-surface/80 text-slate-600 border-slate-300/70 hover:-translate-y-0.5 hover:border-slate-400 hover:text-slate-800"
            }`}
            style={active ? { backgroundColor: scenarioColor, borderColor: scenarioColor } : { borderColor: "#d9e2ec" }}
          >
            {s.name}
          </button>
        );
      })}
    </div>
  );
}

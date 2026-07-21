import type { ScenarioMeta } from "@/lib/api";

const SCENARIO_PALETTE = [
  "#5f7fa4",
  "#7a6b94",
  "#7e9463",
  "#9a7a58",
  "#6c89ab",
  "#8d8662",
  "#8b6f7f",
] as const;

function hashScenarioId(id: string): number {
  let hash = 0;
  for (let index = 0; index < id.length; index += 1) {
    hash = (hash * 31 + id.charCodeAt(index)) >>> 0;
  }
  return hash;
}

export function getScenarioDisplayColor(
  scenario: Pick<ScenarioMeta, "id">,
  fallbackIndex = 0,
): string {
  const id = scenario.id.trim();
  if (!id) {
    return SCENARIO_PALETTE[Math.abs(fallbackIndex) % SCENARIO_PALETTE.length];
  }

  const numericPart = Number.parseInt(id.replace(/\D/g, ""), 10);
  if (Number.isFinite(numericPart) && numericPart > 0) {
    return SCENARIO_PALETTE[numericPart % SCENARIO_PALETTE.length];
  }

  return SCENARIO_PALETTE[hashScenarioId(id) % SCENARIO_PALETTE.length];
}

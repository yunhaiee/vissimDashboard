import type { ScenarioMeta } from "@/lib/api";

// 10 vivid hues in the spirit of the Claude Console graphing palette
// (blue/coral/green/violet/amber/cyan/pink/sky/orange/gray), saturated for
// the dark theme so scenario keys read as distinct, confident colors rather
// than the previous washed-out set. The direction palette (DIRECTION_COLORS in
// app/dashboard/page.tsx) was moved to a different hue family (amber/teal/
// magenta/bronze) and the intersection metric bars to a single neutral steel,
// so nothing that renders on the same screen can be mistaken for a scenario key.
const SCENARIO_PALETTE = [
  "#4c8dff",
  "#f2704d",
  "#46c08a",
  "#9b7ef0",
  "#e9b54c",
  "#3bbfd1",
  "#e56ca6",
  "#6fb6f5",
  "#e08a5a",
  "#9aa0ab",
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
  // Prefer the render-position index over an id-derived hash: it reflects
  // which scenarios are actually shown together on THIS screen, so two
  // scenarios visible at the same time can never collide just because their
  // ids happen to differ by a multiple of the palette length (e.g. S000001
  // and S000008 previously landed on the exact same color).
  if (Number.isInteger(fallbackIndex) && fallbackIndex >= 0) {
    return SCENARIO_PALETTE[fallbackIndex % SCENARIO_PALETTE.length];
  }

  const id = scenario.id.trim();
  if (!id) return SCENARIO_PALETTE[0];

  const numericPart = Number.parseInt(id.replace(/\D/g, ""), 10);
  if (Number.isFinite(numericPart) && numericPart > 0) {
    return SCENARIO_PALETTE[numericPart % SCENARIO_PALETTE.length];
  }

  return SCENARIO_PALETTE[hashScenarioId(id) % SCENARIO_PALETTE.length];
}

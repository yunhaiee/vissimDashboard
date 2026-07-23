import type { ScenarioMeta } from "@/lib/api";

// 10 hues drawn from the Claude Console graphing palette (blue/coral/sage/
// periwinkle/ivory/teal/mauve/sky/salmon/gray, tuned for the dark theme) and
// deliberately NOT reusing any hex from DIRECTION_COLORS or
// INTERSECTION_COLORS in app/dashboard/page.tsx — those palettes render on
// the same screens as scenario colors (direction bars, intersection markers)
// and must stay visually distinguishable from them.
const SCENARIO_PALETTE = [
  "#5f95d5",
  "#dc8373",
  "#88ba92",
  "#7d8fd6",
  "#e3e0d5",
  "#7fc0ca",
  "#b58ebc",
  "#a9cbf4",
  "#ecac9c",
  "#898781",
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

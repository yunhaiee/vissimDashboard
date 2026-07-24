// Shared Level-of-Service (LOS) palette, delay thresholds, and per-node delay
// aggregation. Single source of truth so every consumer that colors a node or
// bar by LOS (dashboard intersection panels, NetworkViewer's diagram nodes)
// agrees on the same grade boundaries and colors — see docs/phase2/DECISIONS.md
// for the decision that put per-node (not per-link) delay behind this.
import type { ScenarioResults } from "@/lib/api";

export type LosGrade = "A" | "B" | "C" | "D" | "E" | "F";

export const LOS_COLORS: Record<LosGrade, string> = {
  A: "#22c55e",
  B: "#a3d977",
  C: "#e5c76b",
  D: "#ee9b5c",
  E: "#e4574d",
  F: "#a83a32",
};

// Delay-second bands behind resolveLosGrade(), spelled out for legends so
// "LOS" isn't shown as an unexplained letter anywhere it appears.
export const LOS_GRADE_THRESHOLDS: Array<{ grade: LosGrade; label: string }> = [
  { grade: "A", label: "≤10s" },
  { grade: "B", label: "≤20s" },
  { grade: "C", label: "≤35s" },
  { grade: "D", label: "≤55s" },
  { grade: "E", label: "≤80s" },
  { grade: "F", label: ">80s" },
];

export function resolveLosGrade(delay: number): LosGrade {
  if (delay <= 10) return "A";
  if (delay <= 20) return "B";
  if (delay <= 35) return "C";
  if (delay <= 55) return "D";
  if (delay <= 80) return "E";
  return "F";
}

// A movement's OBJECT_ID is shaped "{node_id}-{from_link}-{pos}-{to_link}-{pos}"
// (occasionally "{node_id}:..." from other exporters) — never a bare link id.
// This is the only reliable way to attribute a movement row to its node.
export function objectBelongsToNode(objectId: unknown, nodeId: string): boolean {
  const text = String(objectId ?? "").trim();
  const normalizedNodeId = String(nodeId).trim();
  return text === normalizedNodeId || text.startsWith(`${normalizedNodeId}-`) || text.startsWith(`${normalizedNodeId}:`);
}

function toFiniteNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

// VEHS-weighted mean delay across every movement attributed to this node
// (falls back to an unweighted mean when VEHS is missing, weight 1 per row).
export function calculateNodeAverageDelay(result: ScenarioResults, nodeId: string): number | null {
  const rows = result.movements.filter((row) => objectBelongsToNode(row.OBJECT_ID, nodeId));
  let weightedDelay = 0;
  let weightTotal = 0;

  for (const row of rows) {
    const delay = toFiniteNumber(row["VEHDELAY_MEAN"] ?? row["VEHDELAY"]);
    if (delay == null) continue;

    const vehs = toFiniteNumber(row["VEHS_MEAN"] ?? row["VEHS"]);
    const weight = vehs != null && vehs > 0 ? vehs : 1;
    weightedDelay += delay * weight;
    weightTotal += weight;
  }

  return weightTotal > 0 ? weightedDelay / weightTotal : null;
}

// U-19 round 2 -- the genesis draft map's road width formula, extracted from
// `components/NetworkViewer.tsx` (the registered-network schematic) so a
// draft in genesis and a registered network on the dashboard/network tab draw
// the SAME ribbon width from the SAME formula. This is a pure extraction, not
// a rewrite: `linkWidthMeters` below is byte-identical logic to what
// NetworkViewer.tsx defined locally before this unit, and NetworkViewer.tsx
// now imports it from here instead of defining its own copy -- so there is
// exactly ONE width formula in this codebase, not two that happen to agree
// today and can silently drift tomorrow.
//
// `TrajectoryLayer.tsx` also imports these three (it has to reproduce the
// EXACT on-screen road width NetworkViewer draws in order to size cars
// proportionally to their lane -- see that file's own top-of-file comment),
// so this module has TWO pre-existing consumers already; genesis's map
// renderers become the third and fourth, not a special case.
//
// WHY THE INPUT TYPE IS NARROWER than NetworkViewer's own `NetworkLink`: this
// function only ever reads three fields (`width_total`, `lane_widths`,
// `lanes`), so the shared module asks for exactly those three rather than the
// dashboard's full link record (id, name, geometry, connector linkage, …).
// That is what makes genesis's `GenesisDraftLink`/`GenesisDraftConnector`
// (`lib/genesisArtifacts.ts`) reachable through a THIN adapter instead of a
// second implementation: neither carries `width_total`/`lane_widths` (the
// genesis generator emits one measured `lane_width_m` per element instead),
// so `genesisElementWidthMeters` in `lib/genesisArtifacts.ts` builds a
// `lane_widths` array of that one value repeated per lane and lets THIS
// function's existing sum-and-fallback logic do the rest -- no arithmetic is
// duplicated, only the shape is adapted.
export interface RoadWidthInput {
  /** An explicit total roadway width in metres, when the source already
   * measured one. */
  width_total?: number;
  /** Per-lane widths in metres, left-to-right. Summed when `width_total` is
   * absent. */
  lane_widths?: number[];
  /** Lane count, used for the final `lanes x DEFAULT_LANE_M` fallback and as
   * the array length a caller without `width_total`/`lane_widths` should
   * still be able to approximate from. */
  lanes?: number;
}

/** Fallback lane width when a link carries neither an explicit total nor a
 * per-lane breakdown. */
export const DEFAULT_LANE_M = 3.5;

/** Asphalt floor at base fit / zoom 1:1 (screen px, or the equivalent "view
 * unit" in a renderer whose local coordinate space is not literally CSS
 * pixels -- see `DraftLocalMap.tsx`'s own use of this constant). Below this,
 * a road reads as a hairline no matter how few lanes it measures, and (genesis
 * round 2) a hairline is also a poor click target -- the same floor that keeps
 * the dashboard's roads legible at low zoom keeps genesis's clickable too. */
export const MIN_ROAD_PX = 2.5;

/** Total roadway width in metres, tolerant of the three shapes a caller can
 * supply (explicit total -> summed lane widths -> lane-count x default).
 * Extracted verbatim from `components/NetworkViewer.tsx`'s prior local
 * definition (see this file's header comment) -- the branching order and the
 * numbers are unchanged, only the location and the parameter type moved. */
export function linkWidthMeters(l: RoadWidthInput): number {
  if (l.width_total != null && l.width_total > 0) return l.width_total;
  if (l.lane_widths && l.lane_widths.length) {
    const sum = l.lane_widths.reduce((a, b) => a + b, 0);
    if (sum > 0) return sum;
  }
  return Math.max(l.lanes ?? 1, 1) * DEFAULT_LANE_M;
}

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

// ---------------------------------------------------------------------------
// Lane-marking geometry -- unit #29 ("port lane markings to the genesis draft
// maps"), moved here from `components/NetworkViewer.tsx`'s local definitions
// for the SAME reason `linkWidthMeters`/`DEFAULT_LANE_M`/`MIN_ROAD_PX` moved
// here in U-19 round 2: the genesis draft maps need to draw lane dividers
// with the SAME metre-true geometry the dashboard's NetworkViewer draws, not
// a second formula/palette that happens to agree today. Every constant and
// function below is byte-identical logic to NetworkViewer.tsx's prior local
// copy -- NetworkViewer now imports all of it instead, so its own rendering
// is unchanged pixel-for-pixel; only the file that owns the definitions moved.
// ---------------------------------------------------------------------------

/** Muted mono slate NetworkViewer's own road-surface palette uses for every
 * road marking (lane dividers, connector centre-dashes, chevrons, stop lines,
 * the junction outline) -- see that file's "Road-surface palette" comment.
 * Lives here, not only in NetworkViewer, so a genesis lane divider draws in
 * the exact SAME hue rather than a second slate chosen to "look about right"
 * -- this app's three-colour rule (rose/emerald/sky, mono for everything
 * else) makes a road marking's colour a mono decision by construction, and
 * this is the one mono value NetworkViewer already made for it. */
export const MARKING_COLOR = "#94a3b8";

/** Lane-divider dash length, metres. */
export const LANE_DASH_M = 3;
/** Lane-divider gap length, metres. */
export const LANE_GAP_M = 9;
/** Lane-divider stroke width, metres. */
export const LANE_DASH_STROKE_M = 0.15;
/** Lane-divider stroke screen floor, px -- below this the stroke reads as
 * nothing no matter how far the metre-true width has shrunk on screen. */
export const LANE_DASH_STROKE_MIN_PX = 0.7;
/** Connector centre-dash length, metres -- shorter than a lane divider's dash
 * so the two read as visually distinct rhythms. */
export const CONNECTOR_DASH_M = 1.5;
/** Connector centre-dash gap length, metres. */
export const CONNECTOR_GAP_M = 4.5;
/** Connector centre-dash stroke width, metres. */
export const CONNECTOR_DASH_STROKE_M = 0.12;
/** Connector centre-dash stroke screen floor, px. */
export const CONNECTOR_DASH_STROKE_MIN_PX = 0.7;

/** Signed interior lane-boundary offsets from a link's centreline, in metres.
 * A link with L lanes yields L-1 dividers; a single-lane link yields none.
 * For a UNIFORM lane width -- every real caller today: NetworkViewer's own
 * `NetworkLink` when it carries no per-lane breakdown, and every genesis
 * element via `lib/genesisArtifacts.ts`'s `genesisElementLaneOffsetsM`
 * adapter (neither genesis type carries a `lane_widths` array, only one
 * measured `lane_width_m`, mirroring `genesisElementWidthMeters`'s own
 * adapter shape) -- this reduces exactly to `(i/L - 0.5) * width` for
 * divider `i` in `1..L-1`. The cumulative-sum form below is the strict
 * generalisation that also handles a link whose `lane_widths` are NOT
 * uniform, which is why it -- not the simpler closed form -- is what moved
 * out of NetworkViewer.tsx verbatim: NetworkViewer's own `NetworkLink` can
 * carry a real per-lane breakdown, and this must keep honouring it exactly
 * as it did before the move. */
export function laneBoundaryOffsets(l: RoadWidthInput): number[] {
  const L = Math.round(l.lanes ?? 1);
  if (L < 2) return [];
  const total = linkWidthMeters(l);
  const widths = l.lane_widths && l.lane_widths.length === L ? l.lane_widths : new Array(L).fill(total / L);
  const sum = widths.reduce((a, b) => a + b, 0) || total;
  const out: number[] = [];
  let cum = 0;
  for (let k = 0; k < L - 1; k++) {
    cum += widths[k];
    out.push(cum - sum / 2);
  }
  return out;
}

type Pt = [number, number];

// Internal to offsetPolyline only -- NOT NetworkViewer.tsx's own file-scope
// `EPS` (that file keeps its own copy; sampleAlong() and the detector/element
// glyph math there still need it for unrelated geometry having nothing to do
// with offsetting a polyline). `MITER_COS_MIN` had no other caller in
// NetworkViewer and moved here in full, unexported, beside the one function
// that uses it. Same numbers as before the move.
const OFFSET_EPS = 1e-6;
const OFFSET_MITER_COS_MIN = 0.5; // clamp miter to <= 2x so sharp bends never spike offsets

/** Offset a polyline sideways by `dist` (same units as `pts`) using averaged,
 * miter-limited segment normals. Guards: zero-length segments (duplicate
 * vertices) contribute no normal; a ~180deg reversal falls back to one side's
 * normal instead of dividing by a ~zero miter; the miter factor is clamped so
 * tight bends never produce spikes. 2-point inputs and all-degenerate inputs
 * pass through safely (endpoints just use the single/zero available normal).
 *
 * The ONE offset helper in this codebase -- reused, not reimplemented, by
 * both genesis map renderers (`DraftLocalMap.tsx`, `GenesisTileMap.tsx`) to
 * draw a lane divider as a path parallel to a link's own centreline. Pure
 * vector geometry with no assumption about which axis is "north" or "down",
 * so it works unchanged in any consistent 2-D coordinate system: NetworkViewer's
 * own screen px, a genesis draft map's fit-scale view-units, or leaflet's
 * projected pixel space (`map.project`/`map.unproject`). */
export function offsetPolyline(pts: Pt[], dist: number): Pt[] {
  const n = pts.length;
  if (n < 2 || !Number.isFinite(dist)) return pts.slice();
  const segN: (Pt | null)[] = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = pts[i + 1][0] - pts[i][0];
    const dy = pts[i + 1][1] - pts[i][1];
    const len = Math.hypot(dx, dy);
    segN.push(len < OFFSET_EPS ? null : [-dy / len, dx / len]);
  }
  const out: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const prev = i > 0 ? segN[i - 1] : null;
    const next = i < n - 1 ? segN[i] : null;
    let nx = 0;
    let ny = 0;
    if (prev && next) {
      let mx = prev[0] + next[0];
      let my = prev[1] + next[1];
      const mlen = Math.hypot(mx, my);
      if (mlen < OFFSET_EPS) {
        // near-180° reversal: no valid miter direction, use one normal as-is.
        nx = next[0];
        ny = next[1];
      } else {
        mx /= mlen;
        my /= mlen;
        const cos = Math.max(mx * next[0] + my * next[1], OFFSET_MITER_COS_MIN);
        nx = mx / cos;
        ny = my / cos;
      }
    } else if (next) {
      nx = next[0];
      ny = next[1];
    } else if (prev) {
      nx = prev[0];
      ny = prev[1];
    }
    out.push([pts[i][0] + nx * dist, pts[i][1] + ny * dist]);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Unit #29 zoom fix (adversarial finding #1) -- `DraftLocalMap.tsx` bakes a
// lane divider's lateral offset into VERTEX POSITIONS (`offsetPolyline`
// above moves points sideways), not into a stroke-width property, so
// `vectorEffect="non-scaling-stroke"` -- which freezes a STROKE'S on-screen
// WIDTH regardless of the ancestor `<g>`'s zoom transform -- does nothing for
// it. Those vertices sit inside the SAME `<g transform="scale(view.scale)">`
// as everything else the fallback canvas draws, so an offset baked in once
// at fit scale (`offM * projectionScale`) stretches right along with the
// group's own scale as the user zooms in, while the ribbon it is supposed to
// sit inside stays a constant on-screen width (that is what non-scaling-
// stroke is for) -- so the outermost divider of an L-lane road walks outside
// its own ribbon once zoom exceeds roughly L / (L - 2).
// ---------------------------------------------------------------------------

/** The layer-coordinate offset to hand a lane divider's vertices so that,
 * after the caller's own `scale(viewScale)` ancestor transform is applied,
 * the ON-SCREEN lateral offset from the centreline is the constant
 * `offM * projectionScale` at ANY `viewScale` -- the same screen-space
 * quantity `vectorEffect="non-scaling-stroke"` holds a ribbon's WIDTH to,
 * reached here by arithmetic instead, because non-scaling-stroke has no
 * equivalent for a vertex's position. The whole function is this one
 * division, undone by the caller's own multiplication by `viewScale`:
 * `dividerOffsetCoord(offM, s, v) * v === offM * s` for any `v`, asserted by
 * value in `dashboard-contract.test.ts`. */
export function dividerOffsetCoord(offM: number, projectionScale: number, viewScale: number): number {
  return (offM * projectionScale) / viewScale;
}

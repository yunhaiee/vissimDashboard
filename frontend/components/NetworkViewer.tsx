"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { NetworkData, NetworkElements, ScenarioResults } from "@/lib/api";
import { LOS_COLORS, LOS_GRADE_THRESHOLDS, calculateNodeAverageDelay, resolveLosGrade, type LosGrade } from "@/lib/los";
import { ELEMENT_TYPE_LABELS, type NetworkElementRef } from "@/lib/networkElements";
import { useLanguage } from "@/components/LanguageProvider";

// Phase 3 P4 — the viewer's screen projection, published so an overlay (the
// canvas vehicle-playback layer) can draw in EXACTLY the same space without
// duplicating the fit math or reaching into the SVG. Everything here is plain
// numbers (no d3 objects), so consumers need no d3 dependency:
//
//   screenX = tx + k * (offsetX + (x    - xMin) * s)
//   screenY = ty + k * (offsetY + (yMax - y   ) * s)     // s === px per metre
//
// `s`/`offsetX`/`offsetY`/`xMin`/`yMax` are the base uniform fit (recomputed on
// redraw + resize); `k`/`tx`/`ty` are the live d3.zoom transform. On-screen
// px-per-metre is therefore `s * k`.
export interface ViewProjection {
  xMin: number;
  yMax: number;
  s: number;
  offsetX: number;
  offsetY: number;
  k: number;
  tx: number;
  ty: number;
}

interface Props {
  network: NetworkData;
  results?: Record<string, ScenarioResults>;
  selectedScenario?: string;
  selectedNodeIds?: string[];
  selectedLinkId?: string | null;
  onNodeClick?: (nodeId: string, nodeName: string) => void;
  onLinkClick?: (linkId: string, linkName: string, isConnector: boolean) => void;
  // P6 (optional, purely additive): a click on any static element glyph
  // (signal head, detector, …). Absent, the element hit targets keep their
  // pre-P6 hover-only behaviour — tooltips, default cursor, no click handler.
  onElementClick?: (ref: NetworkElementRef) => void;
  // P6: `{type, id}` of the element currently selected by the page, drawn as a
  // thin accent ring around its hit shape. Null/absent draws nothing.
  selectedElement?: { type: string; id: string } | null;
  // P4 (optional, purely additive): receives the current ViewProjection after
  // every redraw/resize and on every zoom/pan frame (rAF-throttled), and null
  // when there is nothing drawn. Held in a ref internally so passing it never
  // becomes a redraw dependency — with the prop absent nothing below changes.
  onProjectionChange?: (projection: ViewProjection | null) => void;
  // Approval-focus flash (2026-07-28, optional + purely additive). The links
  // and elements the agent's pending write touches, recoloured FOCUS_FLASH.
  // `pulsing` runs a short attention pulse; false settles into the same
  // colour, held statically until the caller clears it. Applied as CSS
  // classes over the EXISTING data-elkey / data-linkid attributes — no extra
  // drawing pass, and no redraw dependency (held in a ref, reapplied after
  // every redraw exactly like the layer-visibility flags).
  focusFlash?: { linkIds: string[]; elementKeys: string[]; pulsing: boolean } | null;
  // Programmatic fit-to-region, in RAW NETWORK METRES. Driven through the
  // existing d3.zoom behaviour (never by writing the group transform behind
  // its back), so zoomTransformRef, the ViewProjection emit the vehicle
  // overlay consumes, and the semantic-zoom redraw all stay consistent.
  // `nonce` lets the caller re-trigger the same region — string or number,
  // whatever the caller's own re-trigger token happens to be (the approval
  // handoff's is the URL's `fseq`); it is only ever stringified into the
  // effect key below, never interpreted.
  zoomToBbox?: { x_min: number; y_min: number; x_max: number; y_max: number; nonce?: number | string } | null;
}

// Road-surface palette — all drawn from the file's existing slate family; no new
// saturated hues, gradients or shadows (Vissim-style flat asphalt ribbons).
const ASPHALT_FILL = "#3a4150";   // road surface (mid slate) — links AND connectors
const ROAD_CASING = "#232a36";    // darker slate under the asphalt (road edge)
const MARKING_COLOR = "#94a3b8";  // muted light slate — lane dividers, chevrons, stop lines, junction outline
const NODE_FILL = "#1e293b";
const NODE_STROKE = "#f8fafc";
const ACCENT = "#4c8dff";
// Defect 2 (element hover/selection recolor) — a lighter relative of ACCENT
// (blended ~40% toward white), used ONLY for the element-glyph hover state so
// hover and selection read as two distinct strengths of the same accent
// family. Selection keeps ACCENT itself.
const ACCENT_HOVER = "#94bbff";

// Approval-focus flash (2026-07-28) — "the agent wants you to look HERE",
// arriving from the approval card's 네트워크에서 보기 button.
//
// Was a magenta (#ff3ec8) chosen for maximum hue separation; changed to this
// electric azure on direct user instruction after seeing it live. Blue is not
// a free hue in this file — ACCENT means "you selected this" and ACCENT_HOVER
// "you are pointing at this" — so the separation has to come from WITHIN the
// blues instead of from the hue wheel: #00c2ff is a fully saturated,
// cyan-leaning, markedly brighter blue than either (CIE76 ΔE 43.7 from ACCENT,
// 23.5 from ACCENT_HOVER — both an order of magnitude past the ~2.3 JND, and
// measurably so with both on screen at once), while TTS_CYAN is a desaturated
// slate that shares nothing with it but the quadrant. The remaining "focus,
// not selection" signal is carried by the pulse animation and the on-screen
// banner, which selection has neither of. Contrast against the #0b0b0b ink
// canvas is 9.5:1 (up from magenta's 6.3:1), so hairline-width road fills stay
// legible.
const FOCUS_FLASH = "#00c2ff";
// Same colour as an rgb() triple, for the drop-shadow rgba()s below — kept
// beside its hex so the two can never drift.
const FOCUS_FLASH_RGB = "0, 194, 255";
// Screen-px breathing room left around the focus bbox when zooming to it.
const FOCUS_ZOOM_PAD_PX = 72;
const FOCUS_ZOOM_MS = 650;

// Static-element glyph palette. Restricted, muted variants that read inside the
// dark theme. Signal-head + stop-sign bars use a restrained red (Vissim draws
// stop bars red, but DashboardMap's loud #f3161d is dropped for a muted family);
// reduced-speed a very muted amber; travel-time a cyan-leaning slate; everything
// else a light slate a touch brighter than MARKING_COLOR so furniture reads above
// the lane lines. These three named hues are the spec-sanctioned additions beyond
// the slate family — see Unit C notes.
const ELEMENT_SLATE = "#9aa7ba"; // light slate — most element furniture
const SIGNAL_RED = "#c25450";    // muted red — signal-head + stop-sign bars
const RSA_AMBER = "#b9975a";     // muted amber — reduced-speed band
const TTS_CYAN = "#6f97a3";      // muted cyan-slate — travel-time gate lines
const PED_FILL = "#b9b2a3";      // warm light slate — pedestrian areas

// Geometry constants for the road/marking math. DEFAULT_LANE_M / MIN_ROAD_PX
// and linkWidthMeters() below are exported because the P5 vehicle overlay
// (TrajectoryLayer) has to reproduce the EXACT on-screen road width this file
// draws in order to size cars proportionally to their lane — re-deriving them
// there would let the two drift apart silently.
export const DEFAULT_LANE_M = 3.5; // fallback lane width when a link carries none
const EPS = 1e-6;
const MITER_COS_MIN = 0.5;         // clamp miter to ≤ 2× so sharp bends never spike offsets
export const MIN_ROAD_PX = 2.5;    // asphalt floor at base fit (scales up with zoom)
const MAX_CHEVRONS = 8;            // cap per link (keeps SVG node count modest)

// Road-marking geometry (Defect B, 2026-07-27). Chevrons/lane dividers/connector
// dashlines used to bake pixel floors and clamps into BASE-fit geometry, drawn once
// outside drawZoomDependent — so the d3 zoom transform's k multiplied the floors
// right along with the metre-true terms, and at the deeper max zoom (see the P1
// scaleExtent note above drawZoomDependent) they rendered as enormous arrows/dashes
// drowning the roads. Now: metre-true primary terms (scale naturally with k via the
// group transform, same as road width) with SCREEN-space floors (divide by k, same
// convention as the element glyphs below), drawn INSIDE drawZoomDependent so they
// rebuild on the same k-changed schedule instead of being frozen at base fit.
const CHEVRON_SPACING_M = 25;             // metre-true spacing target between chevrons
const CHEVRON_SPACING_MIN_SCREEN_PX = 40; // spacing floor, screen px (keeps density sane zoomed out)
const CHEVRON_MAX_M = 4;                  // chevron size cap, metre-true (0.5 x road width, capped)
const CHEVRON_MIN_SCREEN_PX = 3;          // below this on-screen size, skip the chevron entirely
const CHEVRON_STROKE_MIN_PX = 0.9;        // chevron stroke screen floor
const LANE_DASH_M = 3;                    // lane-divider dash length, metres
const LANE_GAP_M = 9;                     // lane-divider gap length, metres
const LANE_DASH_STROKE_M = 0.15;          // lane-divider stroke width, metres
const LANE_DASH_STROKE_MIN_PX = 0.7;      // lane-divider stroke screen floor
const CONNECTOR_DASH_M = 1.5;             // connector centre-dash length, metres (shorter than lane dividers)
const CONNECTOR_GAP_M = 4.5;              // connector centre-dash gap, metres
const CONNECTOR_DASH_STROKE_M = 0.12;     // connector centre-dash stroke width, metres
const CONNECTOR_DASH_STROKE_MIN_PX = 0.7; // connector centre-dash stroke screen floor

// Element-glyph size floors (P1 junction-polish pass; raised 2026-07-27, Defect C —
// user feedback found the P1 floors too stingy for legibility). These only bite when
// pxPerMeter x k is small; everything above the floor is still metre-true and scales
// with zoom.
const GLYPH_MIN_PX = 4;            // half-widths, radii (was 2.5)
const GLYPH_MIN_STROKE_PX = 2;     // glyph stroke widths (was 1.5)
const GLYPH_HAIRLINE_PX = 1.4;     // fixed outline hairlines (was inline ~1 / 1.1 / 1.2)
const HIT_POINT_MIN_PX = 11;       // point hit-target floor, screen px (was 9)
const HIT_SEGMENT_MIN_PX = 14;     // segment hit-target floor, screen px (unchanged)
const SIGNAL_BAR_OFFSET_M = 0.8;   // red signal bar sits this far downstream of its stop line

type NetworkLink = NetworkData["links"][number];
type Pt = [number, number];

// Total roadway width in metres, tolerant of the three shapes Unit A can emit
// (explicit total → summed lane widths → lane-count × default).
export function linkWidthMeters(l: NetworkLink): number {
  if (l.width_total != null && l.width_total > 0) return l.width_total;
  if (l.lane_widths && l.lane_widths.length) {
    const sum = l.lane_widths.reduce((a, b) => a + b, 0);
    if (sum > 0) return sum;
  }
  return Math.max(l.lanes ?? 1, 1) * DEFAULT_LANE_M;
}

// Signed interior lane-boundary offsets from the centreline, in metres. A link
// with L lanes yields L-1 dividers; single-lane links yield none.
function laneBoundaryOffsets(l: NetworkLink): number[] {
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

// Offset a polyline sideways by `dist` px using averaged, miter-limited segment
// normals. Guards: zero-length segments (duplicate vertices) contribute no
// normal; a ~180° reversal falls back to one side's normal instead of dividing
// by a ~zero miter; the miter factor is clamped (MITER_COS_MIN) so tight bends
// never produce spikes. 2-point links and all-degenerate inputs pass through
// safely (endpoints just use the single/zero available normal).
function offsetPolyline(pts: Pt[], dist: number): Pt[] {
  const n = pts.length;
  if (n < 2 || !Number.isFinite(dist)) return pts.slice();
  const segN: (Pt | null)[] = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = pts[i + 1][0] - pts[i][0];
    const dy = pts[i + 1][1] - pts[i][1];
    const len = Math.hypot(dx, dy);
    segN.push(len < EPS ? null : [-dy / len, dx / len]);
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
      if (mlen < EPS) {
        // near-180° reversal: no valid miter direction, use one normal as-is.
        nx = next[0];
        ny = next[1];
      } else {
        mx /= mlen;
        my /= mlen;
        const cos = Math.max(mx * next[0] + my * next[1], MITER_COS_MIN);
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

// Sample `count` evenly-spaced points along a polyline, each with a local unit
// travel direction. Degenerate (zero-length) segments are skipped; a landed
// direction that collapses to ~zero falls back to the overall link direction.
function sampleAlong(pts: Pt[], count: number): { p: Pt; dir: Pt }[] {
  if (count < 1) return [];
  const segLen: number[] = [];
  let total = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
    segLen.push(d);
    total += d;
  }
  if (total < EPS) return [];
  const first = pts[0];
  const last = pts[pts.length - 1];
  const fallLen = Math.hypot(last[0] - first[0], last[1] - first[1]) || 1;
  const fallDir: Pt = [(last[0] - first[0]) / fallLen, (last[1] - first[1]) / fallLen];

  const res: { p: Pt; dir: Pt }[] = [];
  for (let c = 0; c < count; c++) {
    const target = (total * (c + 0.5)) / count;
    let acc = 0;
    let idx = 0;
    while (idx < segLen.length - 1 && acc + segLen[idx] < target) {
      acc += segLen[idx];
      idx++;
    }
    const l = segLen[idx] || 1;
    const tt = Math.min(1, Math.max(0, (target - acc) / l));
    const a = pts[idx];
    const b = pts[idx + 1];
    const p: Pt = [a[0] + (b[0] - a[0]) * tt, a[1] + (b[1] - a[1]) * tt];
    let dir: Pt = [(b[0] - a[0]) / l, (b[1] - a[1]) / l];
    if (Math.hypot(dir[0], dir[1]) < EPS) dir = fallDir;
    res.push({ p, dir });
  }
  return res;
}

// --- Static network elements (Phase 3 Tier 1, Unit C) -----------------------

// Per-lane width in metres for one link (total roadway / lane count).
function laneWidthMeters(l: NetworkLink): number {
  return linkWidthMeters(l) / Math.max(Math.round(l.lanes ?? 1), 1);
}

// Screen-space unit vectors from a RAW element angle. angle is math convention
// (0 = +x, CCW positive) in raw network coords; the viewer flips Y (projY uses
// yMax - y), which reflects the plane and NEGATES the angle on screen. So the
// screen travel direction is (cos a, -sin a) — the -sin is the Y-flip. `across`
// is the on-screen 90° perpendicular used to draw stop bars / gate lines across
// the road. Sanity: the Harbor EB signal head (angle 0) → along = (1, 0) (east,
// the EB travel direction), across = (0, 1) (vertical), i.e. a vertical bar
// perpendicular across the horizontal eastbound road — as required.
function dirsFromAngle(angleDeg: number | null | undefined): { along: Pt; across: Pt } {
  const rad = ((Number.isFinite(angleDeg as number) ? (angleDeg as number) : 0) * Math.PI) / 180;
  const along: Pt = [Math.cos(rad), -Math.sin(rad)];
  const across: Pt = [-along[1], along[0]];
  return { along, across };
}

// Element layer catalogue — panel order + i18n labels (ko primary, en via t();
// ja is appended to JA_UI). One row per class; labels double as the tooltip type
// label ("<label> <id> · <name>"). The catalogue itself lives in
// lib/networkElements so the pages and the floating assistant can label a
// clicked element without importing this (d3, ssr:false) module.
const ELEMENT_LAYERS = ELEMENT_TYPE_LABELS;

// Non-element ("map furniture") layer rows: road markings drawn by the base
// render rather than by a NetworkElements class, so they have no count column.
const LANE_MARKINGS_KEY = "lane_markings";
const CONNECTOR_MARKS_KEY = "connector_marks";
const NODE_AREAS_KEY = "node_areas";
const MAP_LAYERS: { key: string; ko: string; en: string }[] = [
  { key: LANE_MARKINGS_KEY, ko: "차선 표시", en: "Lane markings" },
  { key: CONNECTOR_MARKS_KEY, ko: "연결로 표시", en: "Connector markers" },
  { key: NODE_AREAS_KEY, ko: "교차로 영역", en: "Node areas" },
];

const ALL_LAYER_KEYS: string[] = [...ELEMENT_LAYERS.map((l) => l.key), ...MAP_LAYERS.map((l) => l.key)];

// Layer rows that start unchecked. Connectors now render as continuous pavement
// in the road passes, so the junction footprint outline is diagnostic-only
// chrome — opt-in rather than the default. Every other layer defaults visible.
const DEFAULT_HIDDEN_LAYERS: readonly string[] = [NODE_AREAS_KEY];

// Bottom-to-top z within the element layer: area fills, then bands/bays, then
// line/point glyphs, with signal/stop bars on top. Each key becomes its own
// data-layer sub-group so visibility toggling is a display flip, not a redraw.
const ELEMENT_Z_ORDER: (keyof NetworkElements)[] = [
  "ped_areas",
  "reduced_speed_areas",
  "pt_stops",
  "traveltime_sections_geo",
  "detectors",
  "des_speed_decisions",
  "data_collection_points",
  "queue_counters",
  "vehicle_inputs",
  "signal_heads",
  "stop_signs",
];

// Flip each layer group's display attribute from a hidden-key Set. Selects by the
// data-layer tag so it works on freshly-redrawn groups too — called both after a
// redraw (with the current ref) and on toggle (from the [hidden] effect).
function applyLayerVisibility(svgEl: SVGSVGElement | null, hidden: Set<string>) {
  if (!svgEl) return;
  const sel = d3.select(svgEl);
  for (const key of ALL_LAYER_KEYS) {
    sel.selectAll(`[data-layer='${key}']`).attr("display", hidden.has(key) ? "none" : null);
  }
}

const FLASH_CLASS = "focus-flash";
const FLASH_PULSE_CLASS = "focus-flash-pulse";

// Approval-focus flash, applied the same way layer visibility is: by
// re-selecting the CURRENT DOM through the attributes the draw passes already
// stamp (`data-linkid` on road fills, `data-elkey` on element glyph groups)
// and toggling classes the <style> block below turns into a recolor. Called
// both on prop change and after every redraw (with the ref), so a
// semantic-zoom rebuild of the element layer never drops the highlight.
function applyFocusFlash(
  svgEl: SVGSVGElement | null,
  flash: { linkIds: string[]; elementKeys: string[]; pulsing: boolean } | null | undefined,
) {
  if (!svgEl) return;
  const sel = d3.select(svgEl);
  sel.selectAll(`.${FLASH_CLASS}`).classed(FLASH_CLASS, false).classed(FLASH_PULSE_CLASS, false);
  if (!flash) return;
  const mark = (selector: string) => {
    sel.selectAll(selector).classed(FLASH_CLASS, true).classed(FLASH_PULSE_CLASS, flash.pulsing);
  };
  for (const linkId of flash.linkIds) mark(`[data-linkid='${CSS.escape(linkId)}']`);
  for (const key of flash.elementKeys) mark(`g[data-elkey='${CSS.escape(key)}']`);
}

export default function NetworkViewer({
  network,
  results,
  selectedScenario,
  selectedNodeIds,
  selectedLinkId,
  onNodeClick,
  onLinkClick,
  onElementClick,
  selectedElement,
  onProjectionChange,
  focusFlash,
  zoomToBbox,
}: Props) {
  const { t } = useLanguage();
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [sizeNonce, setSizeNonce] = useState(0);

  // Per-node average delay + LOS grade, from real movement data only. Per-link
  // metrics do not exist anywhere in this system (a movement OBJECT_ID is
  // shaped "{node_id}-{from_link}-{pos}-{to_link}-{pos}" and can never equal a
  // link id), so links never carry a metric color — see
  // docs/phase2/DECISIONS.md. This mirrors calculateNodeAverageDelay, the same
  // VEHS-weighted helper the dashboard's intersection panels use, so the
  // diagram never disagrees with them.
  const nodeLosMap = useMemo(() => {
    const map = new Map<string, { delay: number; los: LosGrade }>();
    const result = results && selectedScenario ? results[selectedScenario] : undefined;
    if (!result) return map;
    for (const node of network.nodes) {
      const nodeId = String(node.id);
      const delay = calculateNodeAverageDelay(result, nodeId);
      if (delay == null) continue;
      map.set(nodeId, { delay, los: resolveLosGrade(delay) });
    }
    return map;
  }, [network, results, selectedScenario]);

  // Persist zoom across redraws (LOS data/selection/resize); reset when the
  // underlying network changes.
  const zoomTransformRef = useRef<d3.ZoomTransform>(d3.zoomIdentity);
  const prevNetworkRef = useRef<NetworkData | null>(null);

  const selectedNodeSet = useMemo(() => new Set((selectedNodeIds ?? []).map(String)), [selectedNodeIds]);
  // Stable scalar identity for the selected element, so a page re-rendering with
  // a fresh `{type, id}` object literal never forces a full redraw.
  const selectedElementKey = selectedElement ? `${selectedElement.type}:${selectedElement.id}` : "";
  const isEmpty = network.links.length === 0;

  // Layer visibility. `hidden` holds hidden layer keys — seeded from
  // DEFAULT_HIDDEN_LAYERS so a row can ship default-off without any extra
  // per-row state (the checkbox is `!hidden.has(key)`, so seeding the set is
  // the whole mechanism). hiddenRef mirrors it so the draw effect can reapply
  // visibility after a redraw WITHOUT taking `hidden` as a dependency (toggling
  // must not re-render the data). panelOpen drives the collapsible panel.
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(DEFAULT_HIDDEN_LAYERS));
  const [panelOpen, setPanelOpen] = useState(true);
  const hiddenRef = useRef(hidden);

  const elementCounts = useMemo(() => {
    const els = network.elements;
    const out: Record<string, number> = {};
    for (const { key } of ELEMENT_LAYERS) out[key] = els?.[key]?.length ?? 0;
    return out;
  }, [network]);
  const availableLayers = useMemo(() => ELEMENT_LAYERS.filter((l) => elementCounts[l.key] > 0), [elementCounts]);

  const toggleLayer = (key: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // Reapply visibility on toggle only (no data redraw); keep the ref current.
  useEffect(() => {
    hiddenRef.current = hidden;
    applyLayerVisibility(svgRef.current, hidden);
  }, [hidden]);

  // Approval-focus flash — exactly the hiddenRef pattern above: mirror the
  // prop into a ref (so the draw effect can reapply it after a redraw without
  // taking it as a dependency) and apply it to the live DOM on change. A
  // scalar key keeps a parent re-rendering with a fresh object literal from
  // re-running this.
  const focusFlashRef = useRef(focusFlash);
  const focusFlashKey = focusFlash
    ? `${focusFlash.pulsing ? "p" : "s"}|${focusFlash.linkIds.join(",")}|${focusFlash.elementKeys.join(",")}`
    : "";
  useEffect(() => {
    focusFlashRef.current = focusFlash;
    applyFocusFlash(svgRef.current, focusFlash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusFlashKey]);

  // Set by the draw effect so the fit-to-bbox effect below can drive the SAME
  // d3.zoom behaviour the user's own gestures drive (rather than writing the
  // group transform directly, which would desync zoomTransformRef and the
  // ViewProjection the vehicle overlay reads).
  const zoomBehaviorRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const lastUserGestureRef = useRef(0);
  const fitRef = useRef<{
    s: number;
    offsetX: number;
    offsetY: number;
    xMin: number;
    yMax: number;
    width: number;
    height: number;
    minK: number;
    maxK: number;
  } | null>(null);

  // P4 projection publishing. The callback lives in a ref so the draw effect
  // never takes it as a dependency (a parent re-creating the callback must not
  // force a full d3 redraw); the rAF handle throttles zoom/pan emissions to one
  // per frame.
  const onProjectionChangeRef = useRef(onProjectionChange);
  useEffect(() => {
    onProjectionChangeRef.current = onProjectionChange;
  }, [onProjectionChange]);
  const projectionRafRef = useRef(0);
  // Defect 1 (semantic zoom) — throttles the element-glyph/node-dot redraw to
  // one per animation frame during a zoom gesture, same cancel-and-reschedule
  // discipline as projectionRafRef (see the zoom handler below).
  const elementsRafRef = useRef(0);
  useEffect(
    () => () => {
      if (projectionRafRef.current) cancelAnimationFrame(projectionRafRef.current);
      if (elementsRafRef.current) cancelAnimationFrame(elementsRafRef.current);
    },
    [],
  );

  // Re-render on container resize (rAF-throttled).
  useEffect(() => {
    const el = svgRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        setSizeNonce((n) => n + 1);
      });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    if (!network.links.length) {
      onProjectionChangeRef.current?.(null);
      zoomBehaviorRef.current = null;
      fitRef.current = null;
      return;
    }

    if (prevNetworkRef.current !== network) {
      zoomTransformRef.current = d3.zoomIdentity;
      prevNetworkRef.current = network;
    }

    const allPts = network.links.flatMap((l) => l.points ?? []);
    const xs = allPts.map((p) => p[0]);
    const ys = allPts.map((p) => p[1]);
    const xMin = xs.length ? Math.min(...xs) : 0;
    const xMax = xs.length ? Math.max(...xs) : 0;
    const yMin = ys.length ? Math.min(...ys) : 0;
    const yMax = ys.length ? Math.max(...ys) : 0;
    // Guard degenerate extents so a single-point (or empty) network never
    // yields NaN scales.
    const spanX = xMax - xMin || 1;
    const spanY = yMax - yMin || 1;

    const W = svgRef.current.clientWidth || 900;
    const H = svgRef.current.clientHeight || 600;
    const pad = 40;
    const availW = Math.max(W - 2 * pad, 1);
    const availH = Math.max(H - 2 * pad, 1);

    // Uniform (isotropic) fit: one scale for both axes so the network keeps its
    // true proportions — the earlier per-axis scaleLinear pair stretched it.
    // pxPerMeter feeds every road/marking width. Y stays flipped (world-up ->
    // screen-up); offsets centre the drawing in the viewport.
    const s = Math.min(availW / spanX, availH / spanY);
    const pxPerMeter = s;
    const offsetX = pad + (availW - spanX * s) / 2;
    const offsetY = pad + (availH - spanY * s) / 2;
    const projX = (x: number) => offsetX + (x - xMin) * s;
    const projY = (y: number) => offsetY + (yMax - y) * s;

    // P4 — same fit params the projX/projY closures above use, packaged for the
    // overlay layer. Emitted once at the end of this redraw and again on every
    // (rAF-throttled) zoom frame.
    const emitProjection = (transform: d3.ZoomTransform) => {
      onProjectionChangeRef.current?.({ xMin, yMax, s, offsetX, offsetY, k: transform.k, tx: transform.x, ty: transform.y });
    };

    const linkLabel = t("링크", "Link");
    const nodeLabel = t("노드", "Node");
    const connectorLabel = t("연결로", "Connector");
    const delayLabel = t("평균지연", "Avg delay");
    const secondsUnit = t("초", "s");

    const g = svg.append("g");
    // Z-order layers (bottom -> top): casings -> asphalt fills -> lane markings
    // & chevrons -> connector centre dashes -> node outlines -> selection rings
    // & dots -> invisible hit targets. Casings and fills carry BOTH links and
    // connectors: drawing every casing before every fill lets touching surfaces
    // merge into one continuous pavement across a junction instead of showing
    // casing seams or a separate connector treatment.
    const gCasing = g.append("g");
    const gFill = g.append("g");
    const gMarking = g.append("g").attr("data-layer", LANE_MARKINGS_KEY);
    const gConnector = g.append("g").attr("data-layer", CONNECTOR_MARKS_KEY);
    const gNodePoly = g.append("g").attr("data-layer", NODE_AREAS_KEY);
    const gElements = g.append("g"); // static elements: above roads/junctions, below selection
    const gOverlay = g.append("g");
    const gHit = g.append("g"); // link hit targets only (metre-true, drawn once)
    // Node hit circles get their own group, separate from gHit's link polylines,
    // because node hit radius is a pure fixed-pixel floor (Defect 1) and has to
    // be redrawn every time the zoom factor k changes — the link polylines above
    // do not.
    const gHitNodes = g.append("g");
    // Element hover targets sit ABOVE gHit/gHitNodes so a glyph's tooltip wins
    // over the wide (invisible) link/node hit shapes it overlaps. Same
    // data-layer tags as the visible groups, so one visibility flip hides both.
    const gElementsHit = g.append("g");

    // Click-vs-drag guard: d3.zoom swallows most post-drag clicks, but track the
    // pointer-down position as a belt-and-suspenders check.
    const down = { x: 0, y: 0 };
    svg.on("mousedown.guard", (event: MouseEvent) => {
      down.x = event.clientX;
      down.y = event.clientY;
    });
    const isDragClick = (event: MouseEvent) =>
      event.defaultPrevented || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5;

    // Pre-project every drawable link once; each layer below reuses this.
    const drawn = network.links
      .filter((l) => l.points && l.points.length >= 2)
      .map((l) => {
        const pts = l.points.map((p) => [projX(p[0]), projY(p[1])] as Pt);
        const isConn = !!l.is_connector;
        const isSel = selectedLinkId != null && String(selectedLinkId) === String(l.id);
        const widthPx = linkWidthMeters(l) * pxPerMeter;
        // Connectors use the SAME width rule and floor as links — they are
        // pavement, not annotation, so a junction reads as one road surface.
        const roadW = Math.max(widthPx, MIN_ROAD_PX);
        const linkName = l.name || `${isConn ? connectorLabel : linkLabel} ${l.id}`;
        const tooltipText = isConn
          ? l.from_link != null && l.to_link != null
            ? `${connectorLabel} ${l.id}: ${l.from_link} → ${l.to_link}`
            : `${connectorLabel} ${l.id}`
          : linkName;
        return { link: l, pts, pointsAttr: pts.map((p) => p.join(",")).join(" "), isConn, isSel, roadW, linkName, tooltipText };
      });

    const roads = drawn.filter((d) => !d.isConn);
    const connectors = drawn.filter((d) => d.isConn);

    // Layer 1 — casings for every surface (links AND connectors) first, so
    // junction interiors merge cleanly instead of showing internal edges.
    drawn.forEach(({ pointsAttr, roadW }) => {
      gCasing.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", ROAD_CASING)
        .attr("stroke-width", roadW + 2)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("pointer-events", "none");
    });

    // Layer 2 — asphalt fills for every surface, same colour for links and
    // connectors. Selection recolors the fill stroke to accent (connectors
    // included — that is now their selected treatment).
    drawn.forEach(({ pointsAttr, roadW, isSel, link }) => {
      gFill.append("polyline")
        // Approval-focus flash addressing. Links are drawn by the road passes
        // rather than as data-elkey element glyphs, so this is their
        // equivalent handle — one attribute, no extra drawing pass.
        .attr("data-linkid", String(link.id))
        .attr("points", pointsAttr)
        .attr("stroke", isSel ? ACCENT : ASPHALT_FILL)
        .attr("stroke-width", isSel ? roadW + 1.5 : roadW)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("pointer-events", "none");
    });

    // Layer 3 (lane dividers + chevrons) and Layer 4 (connector centre dashlines)
    // moved into drawZoomDependent (Defect B, 2026-07-27) — their screen-space
    // floors need the live zoom factor k, so they now redraw on the same
    // k-changed schedule as the element glyphs below instead of being baked once
    // at base-fit scale. See drawZoomDependent for the implementation.

    // Layer 5 — node areas (junction footprints): outline ONLY, never filled.
    // The translucent box used to dominate the junction and fight the pavement,
    // so LOS colour moved back to the node centre dot (below) and what remains
    // here is a thin, non-scaling light-slate boundary. This whole group is a
    // default-off layer row (NODE_AREAS_KEY) — opt-in diagnostic chrome.
    network.nodes.forEach((node) => {
      const poly = node.polygon && node.polygon.length >= 3 ? node.polygon.map((p) => [projX(p[0]), projY(p[1])] as Pt) : null;
      if (!poly) return;
      const selected = selectedNodeSet.has(String(node.id));
      gNodePoly.append("polygon")
        .attr("points", poly.map((p) => p.join(",")).join(" "))
        .attr("fill", "none")
        .attr("stroke", selected ? ACCENT : MARKING_COLOR)
        .attr("stroke-opacity", selected ? 0.9 : 0.55)
        .attr("stroke-width", selected ? 1.6 : 1)
        .attr("stroke-linejoin", "round")
        .attr("vector-effect", "non-scaling-stroke")
        .style("pointer-events", "none");
    });

    // Element layer — static network furniture (signal heads, detectors, PT
    // stops, …), above roads/junctions and below selection. Each class is a
    // data-layer sub-group so the layers panel toggles it by display flip.
    // Everything is projected with the SAME uniform fit as the roads (projX/
    // projY, pxPerMeter) and sized in metres so it scales with zoom like road
    // furniture. angle handling: dirsFromAngle() returns SCREEN-space vectors
    // (the Y-flip negates the raw angle). Guards: skip non-finite coords; fall
    // back to angle 0 / default lane width; never crash on missing lists.
    //
    // Defect 1 (semantic zoom) — every pixel FLOOR below used to be baked once
    // at the base-fit scale and then geometrically magnified by the d3 zoom
    // transform, so a floored glyph could never separate from its neighbours no
    // matter how far you zoomed in (the floor grew right along with the metre-
    // true geometry). The fix: this whole block — element glyphs/hits AND the
    // node dots/rings/hit-circles below it — is now `drawZoomDependent(k)`,
    // parameterized by the LIVE zoom factor and re-run whenever k changes
    // (see the zoom handler further down). Every fixed-pixel floor divides by
    // k so it lands at the same floor ON SCREEN after the transform's *k
    // multiplication; metre-true terms (pxM-derived) are untouched.
    const els = network.elements;
    const linkById = new Map(network.links.map((l) => [String(l.id), l]));
    const laneWidthMFor = (linkId?: string | null): number => {
      const l = linkId != null ? linkById.get(String(linkId)) : undefined;
      return l ? laneWidthMeters(l) : DEFAULT_LANE_M;
    };
    const roadWidthMFor = (linkId?: string | null): number => {
      const l = linkId != null ? linkById.get(String(linkId)) : undefined;
      return l ? linkWidthMeters(l) : DEFAULT_LANE_M;
    };
    const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
    const P = (x: number, y: number): Pt => [projX(x), projY(y)];

    // Type labels for tooltips (also the panel labels).
    const typeLabel: Record<string, string> = {};
    for (const l of ELEMENT_LAYERS) typeLabel[l.key] = t(l.ko, l.en);

    // Redraws the element glyphs + hit targets and the node dots/rings/hit-
    // circles for a given live zoom factor k. Called once synchronously below
    // (with whatever k the viewer is currently at) and again from the zoom
    // handler whenever k changes meaningfully.
    function drawZoomDependent(k: number) {
      gMarking.selectAll("*").remove();
      gConnector.selectAll("*").remove();
      gElements.selectAll("*").remove();
      gElementsHit.selectAll("*").remove();
      gOverlay.selectAll("*").remove();
      gHitNodes.selectAll("*").remove();

      // Layer 3 — lane dividers + direction chevrons. Defect B (2026-07-27):
      // metre-true sizing with screen-space floors/caps (see the constants near
      // the top of the file) instead of base-fit-baked pixel sizes, so this no
      // longer magnifies into oversized paint at deep zoom.
      roads.forEach(({ link, pts }) => {
        // Per-lane dividers (interior boundaries only; edge lines are
        // intentionally omitted — the casing already reads as the road edge, and
        // dropping them also avoids the largest, spike-prone ±width/2 offsets on
        // curved arcs). Dash pattern is metre-true (scales with k via the group
        // transform, like real paint); stroke width metre-true with a screen floor.
        for (const offM of laneBoundaryOffsets(link)) {
          const line = offsetPolyline(pts, offM * pxPerMeter);
          gMarking.append("polyline")
            .attr("points", line.map((p) => p.join(",")).join(" "))
            .attr("stroke", MARKING_COLOR)
            .attr("stroke-width", Math.max(LANE_DASH_STROKE_M * pxPerMeter, LANE_DASH_STROKE_MIN_PX / k))
            .attr("stroke-dasharray", `${LANE_DASH_M * pxPerMeter} ${LANE_GAP_M * pxPerMeter}`)
            .attr("stroke-opacity", 0.55)
            .attr("fill", "none")
            .attr("stroke-linecap", "round")
            .style("pointer-events", "none");
        }

        // Direction chevrons: sparse, muted, oriented by local travel direction,
        // sized off the road width — metre-true, capped at CHEVRON_MAX_M, with a
        // screen-space minimum below which the link simply draws none (cleaner
        // than a shrinking dot). Spacing/count are recomputed every call since
        // both the metre target and the screen floor depend on the live k.
        const total = pts.reduce((sum, p, i) => (i === 0 ? 0 : sum + Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1])), 0);
        const widthM = linkWidthMeters(link);
        const size = Math.min(widthM * 0.5, CHEVRON_MAX_M) * pxPerMeter;
        if (size * k < CHEVRON_MIN_SCREEN_PX) return;
        const spacing = Math.max(CHEVRON_SPACING_M * pxPerMeter, CHEVRON_SPACING_MIN_SCREEN_PX / k);
        const count = Math.min(Math.max(Math.floor(total / spacing), 1), MAX_CHEVRONS);
        const strokeW = Math.max(size * 0.12, CHEVRON_STROKE_MIN_PX / k);
        for (const { p, dir } of sampleAlong(pts, count)) {
          const nrm: Pt = [-dir[1], dir[0]];
          const tip: Pt = [p[0] + dir[0] * size * 0.6, p[1] + dir[1] * size * 0.6];
          const back: Pt = [p[0] - dir[0] * size * 0.4, p[1] - dir[1] * size * 0.4];
          const lft: Pt = [back[0] + nrm[0] * size * 0.55, back[1] + nrm[1] * size * 0.55];
          const rgt: Pt = [back[0] - nrm[0] * size * 0.55, back[1] - nrm[1] * size * 0.55];
          gMarking.append("path")
            .attr("d", `M${lft[0]},${lft[1]} L${tip[0]},${tip[1]} L${rgt[0]},${rgt[1]}`)
            .attr("stroke", MARKING_COLOR)
            .attr("stroke-width", strokeW)
            .attr("stroke-opacity", 0.5)
            .attr("fill", "none")
            .attr("stroke-linecap", "round")
            .attr("stroke-linejoin", "round")
            .style("pointer-events", "none");
        }
      });

      // Layer 4 — connector identity cue only. The connector's pavement is
      // already drawn in the casing/fill passes above; all that is left here is
      // a thin, muted centre dashline so an engineer can still tell which
      // pavement inside a junction is a connector. Defect B: metre-true dash
      // pattern — shorter than the lane dividers' so it stays visually distinct
      // — with a screen-space stroke floor, same treatment as above.
      connectors.forEach(({ pointsAttr }) => {
        gConnector.append("polyline")
          .attr("points", pointsAttr)
          .attr("stroke", MARKING_COLOR)
          .attr("stroke-width", Math.max(CONNECTOR_DASH_STROKE_M * pxPerMeter, CONNECTOR_DASH_STROKE_MIN_PX / k))
          .attr("stroke-dasharray", `${CONNECTOR_DASH_M * pxPerMeter} ${CONNECTOR_GAP_M * pxPerMeter}`)
          .attr("stroke-opacity", 0.42)
          .attr("fill", "none")
          .attr("stroke-linecap", "round")
          .attr("stroke-linejoin", "round")
          .style("pointer-events", "none");
      });

      // Defect 2 — every elkey (e.g. "signal_heads:42") maps to the visible
      // <g>(s) carrying its glyph primitives — usually one, two for a travel-
      // time section's paired start/end gates. attachTip's hover/selection
      // toggling looks nodes up here instead of re-querying the DOM.
      const elkeyGroups = new Map<string, SVGGElement[]>();
      const markElkey = (key: string, node: SVGGElement) => {
        const arr = elkeyGroups.get(key);
        if (arr) arr.push(node);
        else elkeyGroups.set(key, [node]);
      };
      const setElClass = (ref: NetworkElementRef, cls: string, on: boolean) => {
        const arr = elkeyGroups.get(`${ref.type}:${ref.id}`);
        if (!arr) return;
        for (const node of arr) node.classList.toggle(cls, on);
      };

      if (els) {
      const pxM = pxPerMeter;

      // One visible sub-group per class in gElements, plus a matching hover-target
      // sub-group per class in gElementsHit — both tagged with the same data-layer
      // key so a single visibility flip toggles the glyph and its hit shape.
      const sub: Record<string, d3.Selection<SVGGElement, unknown, null, undefined>> = {};
      const subHit: Record<string, d3.Selection<SVGGElement, unknown, null, undefined>> = {};
      for (const key of ELEMENT_Z_ORDER) {
        sub[key] = gElements.append("g").attr("data-layer", key);
        subHit[key] = gElementsHit.append("g").attr("data-layer", key);
      }

      // Defect 2 — wraps one element's visible primitives in a per-element
      // <g data-elkey="type:id">, so hover/selection can recolor exactly that
      // glyph via CSS (see the <style> block in the JSX return) instead of the
      // old getBBox() ring. Registers the node in elkeyGroups so attachTip can
      // find it again without touching the DOM.
      const visibleGroup = (key: keyof NetworkElements, id: unknown) => {
        const elkey = `${String(key)}:${String(id ?? "")}`;
        const gEl = sub[key].append("g").attr("data-elkey", elkey);
        markElkey(elkey, gEl.node() as SVGGElement);
        return gEl;
      };

      // P6: every element hit target now also carries a click that publishes a
      // {type,id,name,link} reference (the same guard link/node clicks use, so a
      // pan that ends over a glyph never registers as a selection). With
      // onElementClick absent this is a no-op — hover-only, exactly as before.
      const elementRef = (
        key: keyof NetworkElements,
        id: unknown,
        name: unknown,
        link?: unknown,
      ): NetworkElementRef => ({
        type: String(key),
        id: String(id ?? ""),
        name: String(name ?? ""),
        link: link == null ? null : String(link),
      });
      const isSelectedElement = (ref: NetworkElementRef) =>
        selectedElementKey !== "" && selectedElementKey === `${ref.type}:${ref.id}`;
      // Defect 2 — hover/selection recolors the glyph itself: a data-elkey'd <g>
      // gains an .el-hover / .el-selected class that the <style> block in the
      // JSX return turns into a stroke/fill/drop-shadow override. Replaces the
      // old getBBox() ring (deleted, not supplemented).
      function attachTip<GE extends d3.BaseType>(
        sel: d3.Selection<GE, unknown, null, undefined>,
        text: string,
        ref?: NetworkElementRef,
      ) {
        if (ref) sel.attr("data-elkey", `${ref.type}:${ref.id}`);
        sel
          .on("mousemove", (event: MouseEvent) => {
            setTooltip({ x: event.clientX, y: event.clientY, text });
            if (ref) setElClass(ref, "el-hover", true);
          })
          .on("mouseleave", () => {
            setTooltip(null);
            if (ref) setElClass(ref, "el-hover", false);
          });
        if (!ref) return;
        if (onElementClick) {
          sel.style("cursor", "pointer").on("click", (event: MouseEvent) => {
            if (isDragClick(event)) return;
            onElementClick(ref);
          });
        }
        if (isSelectedElement(ref)) setElClass(ref, "el-selected", true);
      }
      const hitPoint = (key: keyof NetworkElements, pt: Pt, rPx: number, text: string, ref: NetworkElementRef) => {
        attachTip(
          subHit[key].append("circle").attr("cx", pt[0]).attr("cy", pt[1]).attr("r", Math.max(rPx, HIT_POINT_MIN_PX / k)).attr("fill", "transparent"),
          text,
          ref,
        );
      };
      const hitSegment = (key: keyof NetworkElements, a: Pt, b: Pt, text: string, ref: NetworkElementRef) => {
        attachTip(
          subHit[key]
            .append("line")
            .attr("x1", a[0])
            .attr("y1", a[1])
            .attr("x2", b[0])
            .attr("y2", b[1])
            .attr("stroke", "transparent")
            .attr("stroke-width", HIT_SEGMENT_MIN_PX / k)
            .attr("stroke-linecap", "round"),
          text,
          ref,
        );
      };

      // signal_heads — a light painted stop line across the lane at the head
      // position, plus a slim red signal bar SIGNAL_BAR_OFFSET_M downstream of
      // it along the travel direction. The old single chunky red bar read as a
      // cartoon marker; splitting it into "pavement marking + thin signal bar"
      // matches how the Vissim editor draws a stop line + signal head.
      for (const s of els.signal_heads ?? []) {
        if (!finite(s.x) || !finite(s.y)) continue;
        const c = P(s.x, s.y);
        const { along, across } = dirsFromAngle(s.angle);
        const half = Math.max((laneWidthMFor(s.link) * pxM) / 2, GLYPH_MIN_PX / k);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        const gEl = visibleGroup("signal_heads", s.id);
        gEl
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", MARKING_COLOR)
          .attr("stroke-width", Math.max(pxM * 0.3, GLYPH_HAIRLINE_PX / k))
          .attr("stroke-opacity", 0.8)
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        const barOff = SIGNAL_BAR_OFFSET_M * pxM;
        gEl
          .append("line")
          .attr("x1", a[0] + along[0] * barOff)
          .attr("y1", a[1] + along[1] * barOff)
          .attr("x2", b[0] + along[0] * barOff)
          .attr("y2", b[1] + along[1] * barOff)
          .attr("stroke", SIGNAL_RED)
          .attr("stroke-width", Math.max(pxM * 0.35, GLYPH_MIN_STROKE_PX / k))
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        hitSegment("signal_heads", a, b, `${typeLabel.signal_heads} ${s.id} · ${s.name}`, elementRef("signal_heads", s.id, s.name, s.link));
      }

      // vehicle_inputs — small filled entry arrow at link start, pointing along
      // travel. Scaled ~30% down from Unit C so it reads as a cue, not a badge.
      for (const v of els.vehicle_inputs ?? []) {
        if (!finite(v.x) || !finite(v.y)) continue;
        const c = P(v.x, v.y);
        const { along, across } = dirsFromAngle(v.angle);
        const L = Math.max(roadWidthMFor(v.link) * pxM * 0.49, 10 / k);
        const w = L * 0.42;
        const tip: Pt = [c[0] + along[0] * L, c[1] + along[1] * L];
        const bl: Pt = [c[0] + across[0] * w, c[1] + across[1] * w];
        const br: Pt = [c[0] - across[0] * w, c[1] - across[1] * w];
        visibleGroup("vehicle_inputs", v.id)
          .append("polygon")
          .attr("class", "el-fillable")
          .attr("points", `${tip[0]},${tip[1]} ${bl[0]},${bl[1]} ${br[0]},${br[1]}`)
          .attr("fill", ELEMENT_SLATE)
          .attr("fill-opacity", 0.85)
          .style("pointer-events", "none");
        hitPoint("vehicle_inputs", [(c[0] + tip[0]) / 2, (c[1] + tip[1]) / 2], Math.max(w, HIT_POINT_MIN_PX / k), `${typeLabel.vehicle_inputs} ${v.id} · ${v.name}`, elementRef("vehicle_inputs", v.id, v.name, v.link));
      }

      // reduced_speed_areas — translucent amber band along the lane (x,y)->(end).
      for (const r of els.reduced_speed_areas ?? []) {
        if (!finite(r.x) || !finite(r.y)) continue;
        const p1 = P(r.x, r.y);
        let p2: Pt;
        if (finite(r.end_x) && finite(r.end_y)) {
          p2 = P(r.end_x as number, r.end_y as number);
        } else if (r.length != null) {
          const { along } = dirsFromAngle(r.angle);
          p2 = [p1[0] + along[0] * r.length * pxM, p1[1] + along[1] * r.length * pxM];
        } else {
          continue;
        }
        visibleGroup("reduced_speed_areas", r.id)
          .append("line")
          .attr("x1", p1[0])
          .attr("y1", p1[1])
          .attr("x2", p2[0])
          .attr("y2", p2[1])
          .attr("stroke", RSA_AMBER)
          .attr("stroke-width", Math.max(laneWidthMFor(r.link) * pxM, 4 / k))
          .attr("stroke-opacity", 0.22)
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        hitSegment("reduced_speed_areas", p1, p2, `${typeLabel.reduced_speed_areas} ${r.id} · ${r.name}`, elementRef("reduced_speed_areas", r.id, r.name, r.link));
      }

      // des_speed_decisions — thin dashed gate line across the full roadway.
      for (const d of els.des_speed_decisions ?? []) {
        if (!finite(d.x) || !finite(d.y)) continue;
        const c = P(d.x, d.y);
        const { across } = dirsFromAngle(d.angle);
        const half = Math.max((roadWidthMFor(d.link) * pxM) / 2, GLYPH_MIN_PX / k);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        visibleGroup("des_speed_decisions", d.id)
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", GLYPH_HAIRLINE_PX / k)
          .attr("stroke-dasharray", `${4 / k} ${3 / k}`)
          .attr("stroke-opacity", 0.85)
          .attr("stroke-linecap", "round")
          .style("pointer-events", "none");
        hitSegment("des_speed_decisions", a, b, `${typeLabel.des_speed_decisions} ${d.id} · ${d.name}`, elementRef("des_speed_decisions", d.id, d.name, d.link));
      }

      // detectors — small outlined rectangle on the lane. The ALONG dimension
      // stays metre-true (it is real geometry: end_x/end_y or `length`); only the
      // across dimension, which is a drawing choice, is scaled ~30% down.
      for (const dt of els.detectors ?? []) {
        if (!finite(dt.x) || !finite(dt.y)) continue;
        const p1 = P(dt.x, dt.y);
        let alongVec = dirsFromAngle(dt.angle).along;
        let lenPx: number;
        if (finite(dt.end_x) && finite(dt.end_y)) {
          const pe = P(dt.end_x as number, dt.end_y as number);
          const dxp = pe[0] - p1[0];
          const dyp = pe[1] - p1[1];
          lenPx = Math.hypot(dxp, dyp);
          if (lenPx > EPS) alongVec = [dxp / lenPx, dyp / lenPx];
        } else {
          lenPx = (dt.length ?? 2) * pxM;
        }
        lenPx = Math.max(lenPx, 4 / k);
        const acrossVec: Pt = [-alongVec[1], alongVec[0]];
        const half = Math.max((laneWidthMFor(dt.link) * pxM) / 2 * 0.7, GLYPH_MIN_PX / k);
        const center: Pt = [p1[0] + alongVec[0] * (lenPx / 2), p1[1] + alongVec[1] * (lenPx / 2)];
        const hx = alongVec[0] * (lenPx / 2);
        const hy = alongVec[1] * (lenPx / 2);
        const wx = acrossVec[0] * half;
        const wy = acrossVec[1] * half;
        const corners: Pt[] = [
          [center[0] - hx - wx, center[1] - hy - wy],
          [center[0] + hx - wx, center[1] + hy - wy],
          [center[0] + hx + wx, center[1] + hy + wy],
          [center[0] - hx + wx, center[1] - hy + wy],
        ];
        visibleGroup("detectors", dt.id)
          .append("polygon")
          .attr("points", corners.map((p) => p.join(",")).join(" "))
          .attr("fill", "none")
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", GLYPH_HAIRLINE_PX / k)
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        hitPoint("detectors", center, Math.max(half, HIT_POINT_MIN_PX / k), `${typeLabel.detectors} ${dt.id} · ${dt.name}`, elementRef("detectors", dt.id, dt.name, dt.link));
      }

      // data_collection_points — short solid tick across the lane.
      for (const dc of els.data_collection_points ?? []) {
        if (!finite(dc.x) || !finite(dc.y)) continue;
        const c = P(dc.x, dc.y);
        const { across } = dirsFromAngle(dc.angle);
        const half = Math.max((laneWidthMFor(dc.link) * pxM) / 2, GLYPH_MIN_PX / k);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        visibleGroup("data_collection_points", dc.id)
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", Math.max(pxM * 0.28, 1.3 / k))
          .attr("stroke-linecap", "round")
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        hitSegment("data_collection_points", a, b, `${typeLabel.data_collection_points} ${dc.id} · ${dc.name}`, elementRef("data_collection_points", dc.id, dc.name, dc.link));
      }

      // traveltime_sections_geo — paired gates across the road: start = single
      // solid line, end = doubled (second parallel line) to distinguish it.
      for (const tt of els.traveltime_sections_geo ?? []) {
        const drawGate = (ep: NetworkElements["traveltime_sections_geo"][number]["start"] | undefined, doubled: boolean) => {
          if (!ep || !finite(ep.x) || !finite(ep.y)) return;
          const c = P(ep.x, ep.y);
          const { across, along } = dirsFromAngle(ep.angle);
          const half = Math.max((roadWidthMFor(ep.link) * pxM) / 2, GLYPH_MIN_PX / k);
          const gEl = visibleGroup("traveltime_sections_geo", tt.id);
          const line = (off: number): [Pt, Pt] => {
            const cc: Pt = [c[0] + along[0] * off, c[1] + along[1] * off];
            const a: Pt = [cc[0] - across[0] * half, cc[1] - across[1] * half];
            const b: Pt = [cc[0] + across[0] * half, cc[1] + across[1] * half];
            gEl
              .append("line")
              .attr("x1", a[0])
              .attr("y1", a[1])
              .attr("x2", b[0])
              .attr("y2", b[1])
              .attr("stroke", TTS_CYAN)
              .attr("stroke-width", GLYPH_HAIRLINE_PX / k)
              .attr("stroke-opacity", 0.9)
              .attr("stroke-linecap", "round")
              .style("pointer-events", "none");
            return [a, b];
          };
          const [a, b] = line(0);
          if (doubled) line(Math.max(pxM * 1.2, 2.5 / k));
          hitSegment("traveltime_sections_geo", a, b, `${typeLabel.traveltime_sections_geo} ${tt.id} · ${tt.name}`, elementRef("traveltime_sections_geo", tt.id, tt.name, ep.link));
        };
        drawGate(tt.start, false);
        drawGate(tt.end, true);
      }

      // queue_counters — small hollow circle + short upstream stem (~30% down).
      for (const q of els.queue_counters ?? []) {
        if (!finite(q.x) || !finite(q.y)) continue;
        const c = P(q.x, q.y);
        const { along } = dirsFromAngle(q.angle);
        const laneW = laneWidthMFor(q.link);
        const r = Math.max(laneW * pxM * 0.196, 3 / k);
        const stem = Math.max(laneW * pxM * 0.35, 5 / k);
        const gEl = visibleGroup("queue_counters", q.id);
        gEl
          .append("circle")
          .attr("cx", c[0])
          .attr("cy", c[1])
          .attr("r", r)
          .attr("fill", "none")
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", GLYPH_HAIRLINE_PX / k)
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        const s0: Pt = [c[0] - along[0] * r, c[1] - along[1] * r];
        const s1: Pt = [c[0] - along[0] * (r + stem), c[1] - along[1] * (r + stem)];
        gEl
          .append("line")
          .attr("x1", s0[0])
          .attr("y1", s0[1])
          .attr("x2", s1[0])
          .attr("y2", s1[1])
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", GLYPH_HAIRLINE_PX / k)
          .attr("stroke-opacity", 0.9)
          .attr("stroke-linecap", "round")
          .style("pointer-events", "none");
        hitPoint("queue_counters", c, Math.max(r, HIT_POINT_MIN_PX / k), `${typeLabel.queue_counters} ${q.id} · ${q.name}`, elementRef("queue_counters", q.id, q.name, q.link));
      }

      // stop_signs — short bar across the lane, muted red. Slimmed with the same
      // pass as the signal bar but kept a step thicker + shorter, so the two
      // still read differently without either looking chunky.
      for (const st of els.stop_signs ?? []) {
        if (!finite(st.x) || !finite(st.y)) continue;
        const c = P(st.x, st.y);
        const { across } = dirsFromAngle(st.angle);
        const half = Math.max(((laneWidthMFor(st.link) * pxM) / 2) * 0.7, GLYPH_MIN_PX / k);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        visibleGroup("stop_signs", st.id)
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", SIGNAL_RED)
          .attr("stroke-width", Math.max(pxM * 0.5, 2.2 / k))
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        hitSegment("stop_signs", a, b, `${typeLabel.stop_signs} ${st.id} · ${st.name}`, elementRef("stop_signs", st.id, st.name, st.link));
      }

      // pt_stops — elongated hollow rectangle (bus bay) alongside the lane.
      for (const pt of els.pt_stops ?? []) {
        if (!finite(pt.x) || !finite(pt.y)) continue;
        const p1 = P(pt.x, pt.y);
        let alongVec = dirsFromAngle(pt.angle).along;
        let lenPx: number;
        if (finite(pt.end_x) && finite(pt.end_y)) {
          const pe = P(pt.end_x as number, pt.end_y as number);
          const dxp = pe[0] - p1[0];
          const dyp = pe[1] - p1[1];
          lenPx = Math.hypot(dxp, dyp);
          if (lenPx > EPS) alongVec = [dxp / lenPx, dyp / lenPx];
        } else {
          lenPx = (pt.length ?? 15) * pxM;
        }
        lenPx = Math.max(lenPx, 6 / k);
        const acrossVec: Pt = [-alongVec[1], alongVec[0]];
        const laneW = laneWidthMFor(pt.link);
        const bayW = Math.max(laneW * pxM * 0.8, 3 / k);
        const off = Math.max(laneW * pxM * 0.55, 3 / k); // offset sideways so it sits alongside the lane
        const ov: Pt = [acrossVec[0] * off, acrossVec[1] * off];
        const base1: Pt = [p1[0] + ov[0], p1[1] + ov[1]];
        const base2: Pt = [p1[0] + alongVec[0] * lenPx + ov[0], p1[1] + alongVec[1] * lenPx + ov[1]];
        const w2: Pt = [acrossVec[0] * (bayW / 2), acrossVec[1] * (bayW / 2)];
        const corners: Pt[] = [
          [base1[0] - w2[0], base1[1] - w2[1]],
          [base2[0] - w2[0], base2[1] - w2[1]],
          [base2[0] + w2[0], base2[1] + w2[1]],
          [base1[0] + w2[0], base1[1] + w2[1]],
        ];
        visibleGroup("pt_stops", pt.id)
          .append("polygon")
          .attr("points", corners.map((p) => p.join(",")).join(" "))
          .attr("fill", "none")
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", GLYPH_HAIRLINE_PX / k)
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        hitSegment("pt_stops", base1, base2, `${typeLabel.pt_stops} ${pt.id} · ${pt.name}`, elementRef("pt_stops", pt.id, pt.name, pt.link));
      }

      // ped_areas — translucent warm-slate polygon + dotted outline (distinct from
      // the node junction polygons). A matching transparent polygon in the top hit
      // layer carries the tooltip (the visible one stays pointer-events:none).
      for (const pa of els.ped_areas ?? []) {
        if (!pa.polygon || pa.polygon.length < 3) continue;
        const proj = pa.polygon.filter((p) => finite(p[0]) && finite(p[1])).map((p) => P(p[0], p[1]));
        if (proj.length < 3) continue;
        const pointsAttr = proj.map((p) => p.join(",")).join(" ");
        visibleGroup("ped_areas", pa.id)
          .append("polygon")
          .attr("class", "el-fillable")
          .attr("points", pointsAttr)
          .attr("fill", PED_FILL)
          .attr("fill-opacity", 0.12)
          .attr("stroke", PED_FILL)
          .attr("stroke-opacity", 0.85)
          .attr("stroke-width", GLYPH_HAIRLINE_PX / k)
          .attr("stroke-dasharray", `${2 / k} ${2 / k}`)
          .attr("stroke-linejoin", "round")
          .style("pointer-events", "none");
        attachTip(
          subHit.ped_areas.append("polygon").attr("points", pointsAttr).attr("fill", "transparent"),
          `${typeLabel.ped_areas} ${pa.id} · ${pa.name}`,
          elementRef("ped_areas", pa.id, pa.name),
        );
      }
      }

      // Layer 6 — selection rings + center dots. LOS colour lives on the dot for
      // EVERY node again (the pre-polygon behaviour): dot fill = the node's LOS
      // colour when a metric exists, else the neutral NODE_FILL. With node areas
      // default-off the dot is the only LOS carrier, so it is no longer shrunk
      // for polygon nodes. Defect 1: radii divide by k so the ring/dot stay a
      // constant size on screen instead of ballooning into 60-70px blobs at
      // high zoom; stroke-width is untouched here — vector-effect=
      // "non-scaling-stroke" already keeps it constant regardless of the
      // group's transform, so only the geometry (r) needs the /k treatment.
      network.nodes.forEach((node) => {
        const cx = projX(node.x);
        const cy = projY(node.y);
        const selected = selectedNodeSet.has(String(node.id));
        const nodeMetric = nodeLosMap.get(String(node.id));

        if (selected) {
          gOverlay.append("circle")
            .attr("cx", cx)
            .attr("cy", cy)
            .attr("r", 11 / k)
            .attr("fill", "none")
            .attr("stroke", ACCENT)
            .attr("stroke-width", 2)
            .attr("vector-effect", "non-scaling-stroke")
            .style("pointer-events", "none");
        }

        const dotFill = nodeMetric ? LOS_COLORS[nodeMetric.los] : NODE_FILL;
        gOverlay.append("circle")
          .attr("cx", cx)
          .attr("cy", cy)
          .attr("r", (selected ? 7 : 6) / k)
          .attr("fill", dotFill)
          .attr("stroke", selected ? ACCENT : NODE_STROKE)
          .attr("stroke-width", 1.5)
          .attr("vector-effect", "non-scaling-stroke")
          .style("pointer-events", "none");
      });

      // Node hit circles — also a fixed-pixel floor (Defect 1), same /k
      // treatment (r=14 unscaled would become a 140px invisible hover zone at
      // k=10). Lives in gHitNodes (its own group) so it can be cleared and
      // rebuilt on every zoom-driven redraw without touching the link hit
      // polylines below, which are metre-true and drawn once.
      network.nodes.forEach((node) => {
        const cx = projX(node.x);
        const cy = projY(node.y);
        const nodeName = node.name || `${nodeLabel} ${node.id}`;
        const nodeMetric = nodeLosMap.get(String(node.id));
        const nodeTooltipText = nodeMetric
          ? `${nodeName} · ${delayLabel} ${nodeMetric.delay.toFixed(1)}${secondsUnit} · LOS ${nodeMetric.los}`
          : nodeName;

        const hit = gHitNodes.append("circle")
          .attr("cx", cx)
          .attr("cy", cy)
          .attr("r", 14 / k)
          .attr("fill", "transparent")
          .style("cursor", onNodeClick ? "pointer" : "default")
          .on("mousemove", (event: MouseEvent) => {
            setTooltip({ x: event.clientX, y: event.clientY, text: nodeTooltipText });
          })
          .on("mouseleave", () => setTooltip(null));
        if (onNodeClick) {
          hit.on("click", (event: MouseEvent) => {
            if (isDragClick(event)) return;
            onNodeClick(String(node.id), nodeName);
          });
        }
      });

      applyLayerVisibility(svgRef.current, hiddenRef.current);
      // Same reason as the line above: this pass just rebuilt every element
      // glyph group, so any focus flash on one of them has to be restamped.
      // Road-fill flashes are re-marked here too (harmless — applyFocusFlash
      // clears and reapplies the whole set) so one call covers both.
      applyFocusFlash(svgRef.current, focusFlashRef.current);
      lastDrawnK = k;
    }

    // Initial draw at whatever k the viewer is currently persisted at (identity
    // on first mount / a new network; the previous zoom level on any other
    // redraw — e.g. a selection change while the user is already zoomed in).
    let lastDrawnK = zoomTransformRef.current.k;
    drawZoomDependent(lastDrawnK);

    // Layer 7 — invisible hit targets for links only (metre-true, drawn once).
    // Node hit circles live inside drawZoomDependent above (gHitNodes) because
    // their floor is a fixed pixel size, not metre-true.
    drawn.forEach(({ pointsAttr, roadW, tooltipText, isConn, link, linkName }) => {
      const hit = gHit.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", "transparent")
        .attr("stroke-width", Math.max(roadW + 8, 12))
        .attr("fill", "none")
        .style("cursor", onLinkClick ? "pointer" : "default")
        .on("mousemove", (event: MouseEvent) => {
          setTooltip({ x: event.clientX, y: event.clientY, text: tooltipText });
        })
        .on("mouseleave", () => setTooltip(null));
      if (onLinkClick) {
        hit.on("click", (event: MouseEvent) => {
          if (isDragClick(event)) return;
          onLinkClick(String(link.id), linkName, isConn);
        });
      }
    });

    // Zoom (preserve transform across redraws). Ceiling raised from a flat 10
    // to target ~30 px/metre on screen at max zoom (Defect 1's screen-constant
    // floors make deep zoom safe now — lane-level glyphs actually separate
    // instead of ballooning together).
    const maxZoomK = Math.max(10, 30 / pxPerMeter);
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, maxZoomK])
      .on("zoom", (event) => {
        g.attr("transform", event.transform);
        zoomTransformRef.current = event.transform;
        // `sourceEvent` is the DOM event for a real gesture and null for a
        // programmatic `zoom.transform`. Recording the last gesture lets the
        // fit-to-bbox effect below abandon its settle if the user has taken
        // the wheel in the meantime.
        if (event.sourceEvent) lastUserGestureRef.current = performance.now();
        // Projection emit — one per animation frame while zooming/panning, so
        // the vehicle-canvas overlay can never outpace the browser's paint
        // rate. Cancel-and-reschedule, NOT skip-if-pending: a rAF handle that
        // never fires (backgrounded tab, non-compositing pane) used to wedge
        // this closed forever — every later zoom/pan silently dropped its
        // emit because the stale handle still looked "pending". The latest
        // gesture always owns the pending slot now.
        if (onProjectionChangeRef.current) {
          if (projectionRafRef.current) cancelAnimationFrame(projectionRafRef.current);
          projectionRafRef.current = requestAnimationFrame(() => {
            projectionRafRef.current = 0;
            emitProjection(zoomTransformRef.current);
          });
        }
        // Defect 1 — redraw element glyphs/node dots only when k actually
        // changed since the last redraw (pan alone must never trigger it) and
        // past a small relative threshold, so a slow zoom drag does not redraw
        // every single frame. Same cancel-and-reschedule discipline as above.
        if (elementsRafRef.current) cancelAnimationFrame(elementsRafRef.current);
        elementsRafRef.current = requestAnimationFrame(() => {
          elementsRafRef.current = 0;
          const k = zoomTransformRef.current.k;
          if (Math.abs(k / lastDrawnK - 1) > 0.05) drawZoomDependent(k);
        });
      })
      .on("end", () => {
        // Defect A (2026-07-27) — d3-zoom fires start/end even for a
        // zero-movement plain click (mousedown+mouseup, no drag). Rebuilding
        // drawZoomDependent unconditionally here tore down and rebuilt every
        // element hit shape right as the browser was about to dispatch the
        // click event; the click's target node had just been detached from the
        // DOM, so the d3-attached "click" listener on the (now-orphaned) old
        // node never fired and the click was silently swallowed — hover still
        // worked (mousemove hits the live node) and link clicks still worked
        // (link hit shapes are not rebuilt by drawZoomDependent), matching the
        // reported symptom exactly. Fix: only redraw when k actually changed
        // since the last element draw. A plain click or a pure pan never
        // changes k, so they never rebuild; a real zoom gesture still gets its
        // final, exact settle-redraw regardless of the 0.05 relative threshold
        // the "zoom" handler above uses.
        const k = zoomTransformRef.current.k;
        if (Math.abs(k / lastDrawnK - 1) > 1e-6) drawZoomDependent(k);
      });
    svg.call(zoom);
    svg.call(zoom.transform, zoomTransformRef.current);
    // Publish this run's zoom behaviour + base fit so the fit-to-bbox effect
    // can retarget through them. Kept in refs (not state) for the same reason
    // the projection callback is: nothing here may become a redraw trigger.
    zoomBehaviorRef.current = zoom;
    fitRef.current = { s, offsetX, offsetY, xMin, yMax, width: W, height: H, minK: 0.3, maxK: maxZoomK };
    // Synchronous emit so the overlay has the fresh fit immediately after a
    // redraw/resize (the rAF path above only covers live zoom/pan gestures).
    emitProjection(zoomTransformRef.current);

    // Cancel any rAF this run's closure scheduled when the effect tears down
    // (network/selection/resize change, or unmount) — otherwise a stale handle
    // from an OLD closure (old fit params xMin/s/offsetX/…, old drawZoomDependent)
    // could fire after a fresh redraw and push/apply stale state over it.
    return () => {
      if (projectionRafRef.current) {
        cancelAnimationFrame(projectionRafRef.current);
        projectionRafRef.current = 0;
      }
      if (elementsRafRef.current) {
        cancelAnimationFrame(elementsRafRef.current);
        elementsRafRef.current = 0;
      }
    };
  }, [network, nodeLosMap, selectedNodeSet, selectedLinkId, selectedElementKey, onNodeClick, onLinkClick, onElementClick, t, sizeNonce]);

  // Programmatic fit-to-region (approval focus). Declared AFTER the draw
  // effect so React's in-order effect execution guarantees zoomBehaviorRef /
  // fitRef are populated for this same render before this runs.
  //
  // Everything goes through `zoom.transform`, never `g.attr("transform", …)`:
  // that is what keeps zoomTransformRef current, keeps the rAF ViewProjection
  // emit firing (so TrajectoryLayer's vehicles stay glued to the road through
  // the whole flight), and lets the k-changed semantic-zoom redraw fire on
  // arrival exactly as it does for a user gesture.
  const zoomBboxKey = zoomToBbox
    ? `${zoomToBbox.x_min},${zoomToBbox.y_min},${zoomToBbox.x_max},${zoomToBbox.y_max},${zoomToBbox.nonce ?? 0}`
    : "";
  useEffect(() => {
    const svgEl = svgRef.current;
    const zoom = zoomBehaviorRef.current;
    const fitParams = fitRef.current;
    if (!svgEl || !zoom || !fitParams || !zoomToBbox) return;

    const projX = (x: number) => fitParams.offsetX + (x - fitParams.xMin) * fitParams.s;
    const projY = (y: number) => fitParams.offsetY + (fitParams.yMax - y) * fitParams.s;
    const x0 = projX(zoomToBbox.x_min);
    const x1 = projX(zoomToBbox.x_max);
    const yTop = projY(zoomToBbox.y_max);
    const yBottom = projY(zoomToBbox.y_min);
    const widthPx = Math.max(Math.abs(x1 - x0), 1);
    const heightPx = Math.max(Math.abs(yBottom - yTop), 1);
    const availW = Math.max(fitParams.width - 2 * FOCUS_ZOOM_PAD_PX, 1);
    const availH = Math.max(fitParams.height - 2 * FOCUS_ZOOM_PAD_PX, 1);
    const k = Math.min(
      Math.max(Math.min(availW / widthPx, availH / heightPx), fitParams.minK),
      fitParams.maxK,
    );
    const cx = (x0 + x1) / 2;
    const cy = (yTop + yBottom) / 2;
    const target = d3.zoomIdentity.translate(fitParams.width / 2 - k * cx, fitParams.height / 2 - k * cy).scale(k);

    const reduceMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const selection = d3.select(svgEl);
    if (reduceMotion) {
      selection.call(zoom.transform, target);
      return;
    }
    const startedAt = performance.now();
    selection.transition().duration(FOCUS_ZOOM_MS).call(zoom.transform, target);
    // Guaranteed arrival. d3 transitions are driven by requestAnimationFrame,
    // which a backgrounded/non-compositing browser context can suspend
    // indefinitely — the same hazard the projection-emit rAF handling above
    // already documents. If that happens the flight silently never starts and
    // the user lands on an unzoomed network with a highlight they cannot see.
    // This settles the SAME transform through the SAME behaviour once the
    // flight should have finished; when the transition did run it is a no-op
    // (identical transform), and a user gesture in the meantime cancels it so
    // we never yank the view back out from under them.
    const settle = window.setTimeout(() => {
      if (lastUserGestureRef.current > startedAt) return;
      const current = zoomTransformRef.current;
      if (Math.abs(current.k - target.k) < 1e-6 && Math.abs(current.x - target.x) < 1e-6 && Math.abs(current.y - target.y) < 1e-6) {
        return;
      }
      d3.select(svgEl).interrupt().call(zoom.transform, target);
    }, FOCUS_ZOOM_MS + 120);
    return () => window.clearTimeout(settle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoomBboxKey, network, sizeNonce]);

  return (
    <div className="relative w-full h-full">
      {/* Defect 2 — element hover/selection recolor. Presentation attributes
          (.attr("stroke", ...) etc.) lose to CSS by cascade rules, so these
          rules override them without !important. Scoped to `g[data-elkey]`
          (a per-element wrapper the draw effect stamps on every glyph's
          primitives) so nothing outside the element layer is touched. The
          fill rule is further scoped to `.el-fillable` (vehicle-input /
          ped-area polygons only) so stroke-only glyphs — which already carry
          fill="none" as a presentation attribute — never pick up a fill. */}
      <style>{`
        g[data-elkey].el-hover :is(line, polyline, circle, polygon, path) {
          stroke: ${ACCENT_HOVER};
          stroke-opacity: 1;
        }
        g[data-elkey].el-hover .el-fillable {
          fill: ${ACCENT_HOVER};
          fill-opacity: 0.55;
        }
        g[data-elkey].el-hover {
          filter: drop-shadow(0 0 3px rgba(76, 141, 255, 0.55));
        }
        g[data-elkey].el-selected :is(line, polyline, circle, polygon, path) {
          stroke: ${ACCENT};
          stroke-opacity: 1;
        }
        g[data-elkey].el-selected .el-fillable {
          fill: ${ACCENT};
          fill-opacity: 0.65;
        }
        g[data-elkey].el-selected {
          filter: drop-shadow(0 0 5px rgba(76, 141, 255, 0.85));
        }

        /* Approval-focus flash (2026-07-28). Same cascade trick as the
           hover/selection rules above (CSS beats presentation attributes), and
           the same two address spaces: g[data-elkey] element groups and
           polyline[data-linkid] road fills. Listed AFTER the selection rules
           so a focused element that also happens to be selected still reads
           as "look here" rather than "you clicked this". */
        polyline[data-linkid].focus-flash {
          stroke: ${FOCUS_FLASH};
          filter: drop-shadow(0 0 4px rgba(${FOCUS_FLASH_RGB}, 0.75));
        }
        g[data-elkey].focus-flash :is(line, polyline, circle, polygon, path) {
          stroke: ${FOCUS_FLASH};
          stroke-opacity: 1;
        }
        g[data-elkey].focus-flash .el-fillable {
          fill: ${FOCUS_FLASH};
          fill-opacity: 0.7;
        }
        g[data-elkey].focus-flash {
          filter: drop-shadow(0 0 6px rgba(${FOCUS_FLASH_RGB}, 0.9));
        }
        /* A handful of cycles, then the class is swapped for the static one by
           the page — never an endless blink (annoying, and hostile to anyone
           sensitive to motion). */
        @keyframes prism-focus-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
        .focus-flash-pulse {
          animation: prism-focus-pulse 0.85s ease-in-out 0s infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .focus-flash-pulse { animation: none; }
        }
      `}</style>
      <svg ref={svgRef} className="w-full h-full bg-ink rounded-xl" />

      {isEmpty && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="text-sm text-slate-500">{t("표시할 네트워크가 없습니다", "No network to display")}</p>
        </div>
      )}

      {!isEmpty && (
        <div className="pointer-events-none absolute bottom-3 left-3 z-10 select-none rounded-lg border border-slate-200 bg-surface/80 px-3 py-2 text-[11px] text-slate-600 backdrop-blur-sm">
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <svg width="18" height="12" aria-hidden="true">
                <line x1="0" y1="6" x2="18" y2="6" stroke={ROAD_CASING} strokeWidth="8" strokeLinecap="round" />
                <line x1="0" y1="6" x2="18" y2="6" stroke={ASPHALT_FILL} strokeWidth="6" strokeLinecap="round" />
                <line x1="1" y1="6" x2="17" y2="6" stroke={MARKING_COLOR} strokeWidth="1" strokeDasharray="3 3" strokeOpacity="0.7" />
              </svg>
              <span>{t("도로", "Road")}</span>
            </div>
            <div className="flex items-center gap-2">
              <svg width="18" height="12" aria-hidden="true">
                <line x1="0" y1="6" x2="18" y2="6" stroke={ROAD_CASING} strokeWidth="8" strokeLinecap="round" />
                <line x1="0" y1="6" x2="18" y2="6" stroke={ASPHALT_FILL} strokeWidth="6" strokeLinecap="round" />
                <line x1="1" y1="6" x2="17" y2="6" stroke={MARKING_COLOR} strokeWidth="0.9" strokeDasharray="2 3" strokeOpacity="0.42" />
              </svg>
              <span>{t("연결로", "Connector")}</span>
            </div>
            <div className="flex items-center gap-2">
              <svg width="18" height="14" aria-hidden="true">
                <polygon points="3,7 5,3 13,3 15,7 13,11 5,11" fill="none" stroke={MARKING_COLOR} strokeWidth="1" strokeOpacity="0.55" strokeLinejoin="round" />
                <circle cx="9" cy="7" r="2.6" fill={NODE_FILL} stroke={NODE_STROKE} strokeWidth="0.8" />
              </svg>
              <span>{t("노드", "Node")}</span>
            </div>
            {nodeLosMap.size > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-200/70 pt-1.5">
                <span className="font-semibold text-slate-500">LOS</span>
                {LOS_GRADE_THRESHOLDS.map(({ grade }) => (
                  <span key={grade} className="inline-flex items-center gap-0.5">
                    <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: LOS_COLORS[grade] }} />
                    <span>{grade}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {!isEmpty && (
        <div className="absolute right-3 top-3 z-10 select-none rounded-lg border border-slate-200 bg-surface/80 text-[11px] text-slate-600 backdrop-blur-sm">
          <button
            type="button"
            onClick={() => setPanelOpen((o) => !o)}
            aria-expanded={panelOpen}
            className="flex w-full items-center justify-between gap-4 px-3 py-2 text-left font-semibold text-slate-500"
          >
            <span>{t("표시 레이어", "Layers")}</span>
            <span aria-hidden="true" className="text-slate-400">{panelOpen ? "−" : "+"}</span>
          </button>
          {panelOpen && (
            <div className="flex flex-col gap-1 border-t border-slate-200/70 px-3 py-2">
              {availableLayers.map(({ key, ko, en }) => (
                <label key={key} className="flex cursor-pointer items-center gap-2">
                  <input
                    type="checkbox"
                    className="h-3 w-3 accent-[#4c8dff]"
                    checked={!hidden.has(key)}
                    onChange={() => toggleLayer(key)}
                  />
                  <span>{t(ko, en)}</span>
                  <span className="ml-auto tabular-nums text-slate-400">{elementCounts[key]}</span>
                </label>
              ))}
              <div className="mt-0.5 flex flex-col gap-1 border-t border-slate-200/70 pt-1.5">
                {MAP_LAYERS.map(({ key, ko, en }) => (
                  <label key={key} className="flex cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      className="h-3 w-3 accent-[#4c8dff]"
                      checked={!hidden.has(key)}
                      onChange={() => toggleLayer(key)}
                    />
                    <span>{t(ko, en)}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tooltip && (
        <div
          className="fixed z-50 bg-slate-800 text-ink text-xs px-3 py-1.5 rounded shadow-lg pointer-events-none"
          style={{ left: tooltip.x + 12, top: tooltip.y - 10 }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  );
}

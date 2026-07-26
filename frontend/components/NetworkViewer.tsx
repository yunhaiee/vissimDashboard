"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { NetworkData, NetworkElements, ScenarioResults } from "@/lib/api";
import { LOS_COLORS, LOS_GRADE_THRESHOLDS, calculateNodeAverageDelay, resolveLosGrade, type LosGrade } from "@/lib/los";
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
  // P4 (optional, purely additive): receives the current ViewProjection after
  // every redraw/resize and on every zoom/pan frame (rAF-throttled), and null
  // when there is nothing drawn. Held in a ref internally so passing it never
  // becomes a redraw dependency — with the prop absent nothing below changes.
  onProjectionChange?: (projection: ViewProjection | null) => void;
}

// Road-surface palette — all drawn from the file's existing slate family; no new
// saturated hues, gradients or shadows (Vissim-style flat asphalt ribbons).
const ASPHALT_FILL = "#3a4150";   // road surface (mid slate) — links AND connectors
const ROAD_CASING = "#232a36";    // darker slate under the asphalt (road edge)
const MARKING_COLOR = "#94a3b8";  // muted light slate — lane dividers, chevrons, stop lines, junction outline
const NODE_FILL = "#1e293b";
const NODE_STROKE = "#f8fafc";
const ACCENT = "#4c8dff";

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

// Geometry constants for the road/marking math.
const DEFAULT_LANE_M = 3.5;        // fallback lane width when a link carries none
const EPS = 1e-6;
const MITER_COS_MIN = 0.5;         // clamp miter to ≤ 2× so sharp bends never spike offsets
const MIN_ROAD_PX = 2.5;           // asphalt floor at base fit (scales up with zoom)
const CHEVRON_SPACING_PX = 120;    // one direction chevron per ~this many base-fit px
const MAX_CHEVRONS = 8;            // cap per link (keeps SVG node count modest)

// Element-glyph size floors (P1 junction-polish pass). These only bite when
// pxPerMeter is small (base fit on a wide network); keeping them near-invisible
// is deliberate — at mid zoom the floors used to dominate the metre-true sizes
// and made the furniture louder than the roads. Everything above the floor is
// still metre-true and scales with zoom.
const GLYPH_MIN_PX = 2.5;          // half-widths, radii
const GLYPH_MIN_STROKE_PX = 1.5;   // glyph stroke widths
const SIGNAL_BAR_OFFSET_M = 0.8;   // red signal bar sits this far downstream of its stop line

type NetworkLink = NetworkData["links"][number];
type Pt = [number, number];

// Total roadway width in metres, tolerant of the three shapes Unit A can emit
// (explicit total → summed lane widths → lane-count × default).
function linkWidthMeters(l: NetworkLink): number {
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
// label ("<label> <id> · <name>").
const ELEMENT_LAYERS: { key: keyof NetworkElements; ko: string; en: string }[] = [
  { key: "signal_heads", ko: "신호등", en: "Signal head" },
  { key: "vehicle_inputs", ko: "차량 입력", en: "Vehicle input" },
  { key: "reduced_speed_areas", ko: "감속 구역", en: "Reduced speed area" },
  { key: "des_speed_decisions", ko: "희망속도 결정", en: "Desired speed decision" },
  { key: "detectors", ko: "검지기", en: "Detector" },
  { key: "data_collection_points", ko: "데이터 수집 지점", en: "Data collection point" },
  { key: "traveltime_sections_geo", ko: "통행시간 구간", en: "Travel time section" },
  { key: "queue_counters", ko: "대기행렬 카운터", en: "Queue counter" },
  { key: "stop_signs", ko: "정지 표지", en: "Stop sign" },
  { key: "pt_stops", ko: "정류장", en: "PT stop" },
  { key: "ped_areas", ko: "보행자 구역", en: "Pedestrian area" },
];

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

export default function NetworkViewer({
  network,
  results,
  selectedScenario,
  selectedNodeIds,
  selectedLinkId,
  onNodeClick,
  onLinkClick,
  onProjectionChange,
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

  // P4 projection publishing. The callback lives in a ref so the draw effect
  // never takes it as a dependency (a parent re-creating the callback must not
  // force a full d3 redraw); the rAF handle throttles zoom/pan emissions to one
  // per frame.
  const onProjectionChangeRef = useRef(onProjectionChange);
  useEffect(() => {
    onProjectionChangeRef.current = onProjectionChange;
  }, [onProjectionChange]);
  const projectionRafRef = useRef(0);
  useEffect(
    () => () => {
      if (projectionRafRef.current) cancelAnimationFrame(projectionRafRef.current);
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
    const gHit = g.append("g");
    // Element hover targets sit ABOVE gHit so a glyph's tooltip wins over the
    // wide (invisible) link/node hit shapes it overlaps. Same data-layer tags as
    // the visible groups, so one visibility flip hides both.
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
    drawn.forEach(({ pointsAttr, roadW, isSel }) => {
      gFill.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", isSel ? ACCENT : ASPHALT_FILL)
        .attr("stroke-width", isSel ? roadW + 1.5 : roadW)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("pointer-events", "none");
    });

    // Layer 3 — lane dividers + direction chevrons.
    roads.forEach(({ link, pts, roadW }) => {
      // Per-lane dividers (interior boundaries only; edge lines are intentionally
      // omitted — the casing already reads as the road edge, and dropping them
      // also avoids the largest, spike-prone ±width/2 offsets on curved arcs).
      for (const offM of laneBoundaryOffsets(link)) {
        const line = offsetPolyline(pts, offM * pxPerMeter);
        gMarking.append("polyline")
          .attr("points", line.map((p) => p.join(",")).join(" "))
          .attr("stroke", MARKING_COLOR)
          .attr("stroke-width", Math.max(roadW * 0.06, 0.7))
          .attr("stroke-dasharray", "5 5")
          .attr("stroke-opacity", 0.55)
          .attr("fill", "none")
          .attr("stroke-linecap", "round")
          .style("pointer-events", "none");
      }

      // Direction chevrons: sparse, muted, oriented by local travel direction,
      // sized off the road width. Read as markings, not icons.
      const total = pts.reduce((sum, p, i) => (i === 0 ? 0 : sum + Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1])), 0);
      const count = Math.min(Math.max(Math.floor(total / CHEVRON_SPACING_PX), 1), MAX_CHEVRONS);
      const size = Math.min(Math.max(roadW * 0.5, 3), 9);
      for (const { p, dir } of sampleAlong(pts, count)) {
        const nrm: Pt = [-dir[1], dir[0]];
        const tip: Pt = [p[0] + dir[0] * size * 0.6, p[1] + dir[1] * size * 0.6];
        const back: Pt = [p[0] - dir[0] * size * 0.4, p[1] - dir[1] * size * 0.4];
        const lft: Pt = [back[0] + nrm[0] * size * 0.55, back[1] + nrm[1] * size * 0.55];
        const rgt: Pt = [back[0] - nrm[0] * size * 0.55, back[1] - nrm[1] * size * 0.55];
        gMarking.append("path")
          .attr("d", `M${lft[0]},${lft[1]} L${tip[0]},${tip[1]} L${rgt[0]},${rgt[1]}`)
          .attr("stroke", MARKING_COLOR)
          .attr("stroke-width", Math.max(roadW * 0.12, 0.9))
          .attr("stroke-opacity", 0.5)
          .attr("fill", "none")
          .attr("stroke-linecap", "round")
          .attr("stroke-linejoin", "round")
          .style("pointer-events", "none");
      }
    });

    // Layer 4 — connector identity cue only. The connector's pavement is already
    // drawn in the casing/fill passes above; all that is left here is a thin,
    // muted centre dashline so an engineer can still tell which pavement inside
    // a junction is a connector. Its dash pattern is deliberately shorter and
    // fainter than the lane dividers so it never competes with them.
    connectors.forEach(({ pointsAttr, roadW }) => {
      gConnector.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", MARKING_COLOR)
        .attr("stroke-width", Math.max(roadW * 0.07, 0.7))
        .attr("stroke-dasharray", "3 4")
        .attr("stroke-opacity", 0.42)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("pointer-events", "none");
    });

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
    const els = network.elements;
    if (els) {
      const pxM = pxPerMeter;
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

      // One visible sub-group per class in gElements, plus a matching hover-target
      // sub-group per class in gElementsHit — both tagged with the same data-layer
      // key so a single visibility flip toggles the glyph and its hit shape.
      const sub: Record<string, d3.Selection<SVGGElement, unknown, null, undefined>> = {};
      const subHit: Record<string, d3.Selection<SVGGElement, unknown, null, undefined>> = {};
      for (const key of ELEMENT_Z_ORDER) {
        sub[key] = gElements.append("g").attr("data-layer", key);
        subHit[key] = gElementsHit.append("g").attr("data-layer", key);
      }

      function attachTip<GE extends d3.BaseType>(sel: d3.Selection<GE, unknown, null, undefined>, text: string) {
        sel
          .on("mousemove", (event: MouseEvent) => setTooltip({ x: event.clientX, y: event.clientY, text }))
          .on("mouseleave", () => setTooltip(null));
      }
      const hitPoint = (key: keyof NetworkElements, pt: Pt, rPx: number, text: string) => {
        attachTip(subHit[key].append("circle").attr("cx", pt[0]).attr("cy", pt[1]).attr("r", Math.max(rPx, 9)).attr("fill", "transparent"), text);
      };
      const hitSegment = (key: keyof NetworkElements, a: Pt, b: Pt, text: string) => {
        attachTip(
          subHit[key]
            .append("line")
            .attr("x1", a[0])
            .attr("y1", a[1])
            .attr("x2", b[0])
            .attr("y2", b[1])
            .attr("stroke", "transparent")
            .attr("stroke-width", 14)
            .attr("stroke-linecap", "round"),
          text,
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
        const half = Math.max((laneWidthMFor(s.link) * pxM) / 2, GLYPH_MIN_PX);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        sub.signal_heads
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", MARKING_COLOR)
          .attr("stroke-width", Math.max(pxM * 0.3, 1))
          .attr("stroke-opacity", 0.8)
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        const barOff = SIGNAL_BAR_OFFSET_M * pxM;
        sub.signal_heads
          .append("line")
          .attr("x1", a[0] + along[0] * barOff)
          .attr("y1", a[1] + along[1] * barOff)
          .attr("x2", b[0] + along[0] * barOff)
          .attr("y2", b[1] + along[1] * barOff)
          .attr("stroke", SIGNAL_RED)
          .attr("stroke-width", Math.max(pxM * 0.35, GLYPH_MIN_STROKE_PX))
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        hitSegment("signal_heads", a, b, `${typeLabel.signal_heads} ${s.id} · ${s.name}`);
      }

      // vehicle_inputs — small filled entry arrow at link start, pointing along
      // travel. Scaled ~30% down from Unit C so it reads as a cue, not a badge.
      for (const v of els.vehicle_inputs ?? []) {
        if (!finite(v.x) || !finite(v.y)) continue;
        const c = P(v.x, v.y);
        const { along, across } = dirsFromAngle(v.angle);
        const L = Math.max(roadWidthMFor(v.link) * pxM * 0.49, 7.5);
        const w = L * 0.42;
        const tip: Pt = [c[0] + along[0] * L, c[1] + along[1] * L];
        const bl: Pt = [c[0] + across[0] * w, c[1] + across[1] * w];
        const br: Pt = [c[0] - across[0] * w, c[1] - across[1] * w];
        sub.vehicle_inputs
          .append("polygon")
          .attr("points", `${tip[0]},${tip[1]} ${bl[0]},${bl[1]} ${br[0]},${br[1]}`)
          .attr("fill", ELEMENT_SLATE)
          .attr("fill-opacity", 0.85)
          .style("pointer-events", "none");
        hitPoint("vehicle_inputs", [(c[0] + tip[0]) / 2, (c[1] + tip[1]) / 2], Math.max(w, 9), `${typeLabel.vehicle_inputs} ${v.id} · ${v.name}`);
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
        sub.reduced_speed_areas
          .append("line")
          .attr("x1", p1[0])
          .attr("y1", p1[1])
          .attr("x2", p2[0])
          .attr("y2", p2[1])
          .attr("stroke", RSA_AMBER)
          .attr("stroke-width", Math.max(laneWidthMFor(r.link) * pxM, 4))
          .attr("stroke-opacity", 0.22)
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        hitSegment("reduced_speed_areas", p1, p2, `${typeLabel.reduced_speed_areas} ${r.id} · ${r.name}`);
      }

      // des_speed_decisions — thin dashed gate line across the full roadway.
      for (const d of els.des_speed_decisions ?? []) {
        if (!finite(d.x) || !finite(d.y)) continue;
        const c = P(d.x, d.y);
        const { across } = dirsFromAngle(d.angle);
        const half = Math.max((roadWidthMFor(d.link) * pxM) / 2, GLYPH_MIN_PX);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        sub.des_speed_decisions
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", 1.1)
          .attr("stroke-dasharray", "4 3")
          .attr("stroke-opacity", 0.85)
          .attr("stroke-linecap", "round")
          .style("pointer-events", "none");
        hitSegment("des_speed_decisions", a, b, `${typeLabel.des_speed_decisions} ${d.id} · ${d.name}`);
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
        lenPx = Math.max(lenPx, 4);
        const acrossVec: Pt = [-alongVec[1], alongVec[0]];
        const half = Math.max((laneWidthMFor(dt.link) * pxM) / 2 * 0.7, GLYPH_MIN_PX);
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
        sub.detectors
          .append("polygon")
          .attr("points", corners.map((p) => p.join(",")).join(" "))
          .attr("fill", "none")
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", 1)
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        hitPoint("detectors", center, Math.max(half, 9), `${typeLabel.detectors} ${dt.id} · ${dt.name}`);
      }

      // data_collection_points — short solid tick across the lane.
      for (const dc of els.data_collection_points ?? []) {
        if (!finite(dc.x) || !finite(dc.y)) continue;
        const c = P(dc.x, dc.y);
        const { across } = dirsFromAngle(dc.angle);
        const half = Math.max((laneWidthMFor(dc.link) * pxM) / 2, GLYPH_MIN_PX);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        sub.data_collection_points
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", Math.max(pxM * 0.28, 1.3))
          .attr("stroke-linecap", "round")
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        hitSegment("data_collection_points", a, b, `${typeLabel.data_collection_points} ${dc.id} · ${dc.name}`);
      }

      // traveltime_sections_geo — paired gates across the road: start = single
      // solid line, end = doubled (second parallel line) to distinguish it.
      for (const tt of els.traveltime_sections_geo ?? []) {
        const drawGate = (ep: NetworkElements["traveltime_sections_geo"][number]["start"] | undefined, doubled: boolean) => {
          if (!ep || !finite(ep.x) || !finite(ep.y)) return;
          const c = P(ep.x, ep.y);
          const { across, along } = dirsFromAngle(ep.angle);
          const half = Math.max((roadWidthMFor(ep.link) * pxM) / 2, GLYPH_MIN_PX);
          const line = (off: number): [Pt, Pt] => {
            const cc: Pt = [c[0] + along[0] * off, c[1] + along[1] * off];
            const a: Pt = [cc[0] - across[0] * half, cc[1] - across[1] * half];
            const b: Pt = [cc[0] + across[0] * half, cc[1] + across[1] * half];
            sub.traveltime_sections_geo
              .append("line")
              .attr("x1", a[0])
              .attr("y1", a[1])
              .attr("x2", b[0])
              .attr("y2", b[1])
              .attr("stroke", TTS_CYAN)
              .attr("stroke-width", 1.2)
              .attr("stroke-opacity", 0.9)
              .attr("stroke-linecap", "round")
              .style("pointer-events", "none");
            return [a, b];
          };
          const [a, b] = line(0);
          if (doubled) line(Math.max(pxM * 1.2, 2.5));
          hitSegment("traveltime_sections_geo", a, b, `${typeLabel.traveltime_sections_geo} ${tt.id} · ${tt.name}`);
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
        const r = Math.max(laneW * pxM * 0.196, 2.1);
        const stem = Math.max(laneW * pxM * 0.35, 3.5);
        sub.queue_counters
          .append("circle")
          .attr("cx", c[0])
          .attr("cy", c[1])
          .attr("r", r)
          .attr("fill", "none")
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", 1.1)
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        const s0: Pt = [c[0] - along[0] * r, c[1] - along[1] * r];
        const s1: Pt = [c[0] - along[0] * (r + stem), c[1] - along[1] * (r + stem)];
        sub.queue_counters
          .append("line")
          .attr("x1", s0[0])
          .attr("y1", s0[1])
          .attr("x2", s1[0])
          .attr("y2", s1[1])
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", 1.1)
          .attr("stroke-opacity", 0.9)
          .attr("stroke-linecap", "round")
          .style("pointer-events", "none");
        hitPoint("queue_counters", c, Math.max(r, 9), `${typeLabel.queue_counters} ${q.id} · ${q.name}`);
      }

      // stop_signs — short bar across the lane, muted red. Slimmed with the same
      // pass as the signal bar but kept a step thicker + shorter, so the two
      // still read differently without either looking chunky.
      for (const st of els.stop_signs ?? []) {
        if (!finite(st.x) || !finite(st.y)) continue;
        const c = P(st.x, st.y);
        const { across } = dirsFromAngle(st.angle);
        const half = Math.max(((laneWidthMFor(st.link) * pxM) / 2) * 0.7, GLYPH_MIN_PX);
        const a: Pt = [c[0] - across[0] * half, c[1] - across[1] * half];
        const b: Pt = [c[0] + across[0] * half, c[1] + across[1] * half];
        sub.stop_signs
          .append("line")
          .attr("x1", a[0])
          .attr("y1", a[1])
          .attr("x2", b[0])
          .attr("y2", b[1])
          .attr("stroke", SIGNAL_RED)
          .attr("stroke-width", Math.max(pxM * 0.5, 2.2))
          .attr("stroke-linecap", "butt")
          .style("pointer-events", "none");
        hitSegment("stop_signs", a, b, `${typeLabel.stop_signs} ${st.id} · ${st.name}`);
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
        lenPx = Math.max(lenPx, 6);
        const acrossVec: Pt = [-alongVec[1], alongVec[0]];
        const laneW = laneWidthMFor(pt.link);
        const bayW = Math.max(laneW * pxM * 0.8, 3);
        const off = Math.max(laneW * pxM * 0.55, 3); // offset sideways so it sits alongside the lane
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
        sub.pt_stops
          .append("polygon")
          .attr("points", corners.map((p) => p.join(",")).join(" "))
          .attr("fill", "none")
          .attr("stroke", ELEMENT_SLATE)
          .attr("stroke-width", 1)
          .attr("stroke-opacity", 0.9)
          .style("pointer-events", "none");
        hitSegment("pt_stops", base1, base2, `${typeLabel.pt_stops} ${pt.id} · ${pt.name}`);
      }

      // ped_areas — translucent warm-slate polygon + dotted outline (distinct from
      // the node junction polygons). A matching transparent polygon in the top hit
      // layer carries the tooltip (the visible one stays pointer-events:none).
      for (const pa of els.ped_areas ?? []) {
        if (!pa.polygon || pa.polygon.length < 3) continue;
        const proj = pa.polygon.filter((p) => finite(p[0]) && finite(p[1])).map((p) => P(p[0], p[1]));
        if (proj.length < 3) continue;
        const pointsAttr = proj.map((p) => p.join(",")).join(" ");
        sub.ped_areas
          .append("polygon")
          .attr("points", pointsAttr)
          .attr("fill", PED_FILL)
          .attr("fill-opacity", 0.12)
          .attr("stroke", PED_FILL)
          .attr("stroke-opacity", 0.85)
          .attr("stroke-width", 1)
          .attr("stroke-dasharray", "2 2")
          .attr("stroke-linejoin", "round")
          .style("pointer-events", "none");
        attachTip(
          subHit.ped_areas.append("polygon").attr("points", pointsAttr).attr("fill", "transparent"),
          `${typeLabel.ped_areas} ${pa.id} · ${pa.name}`,
        );
      }
    }

    // Layer 6 — selection rings + center dots. LOS colour lives on the dot for
    // EVERY node again (the pre-polygon behaviour): dot fill = the node's LOS
    // colour when a metric exists, else the neutral NODE_FILL. With node areas
    // default-off the dot is the only LOS carrier, so it is no longer shrunk
    // for polygon nodes.
    network.nodes.forEach((node) => {
      const cx = projX(node.x);
      const cy = projY(node.y);
      const selected = selectedNodeSet.has(String(node.id));
      const nodeMetric = nodeLosMap.get(String(node.id));

      if (selected) {
        gOverlay.append("circle")
          .attr("cx", cx)
          .attr("cy", cy)
          .attr("r", 11)
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
        .attr("r", selected ? 7 : 6)
        .attr("fill", dotFill)
        .attr("stroke", selected ? ACCENT : NODE_STROKE)
        .attr("stroke-width", 1.5)
        .attr("vector-effect", "non-scaling-stroke")
        .style("pointer-events", "none");
    });

    // Layer 7 — invisible hit targets (links then nodes). Contracts unchanged;
    // link hit width tracks the road width (min 12px).
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

    network.nodes.forEach((node) => {
      const cx = projX(node.x);
      const cy = projY(node.y);
      const nodeName = node.name || `${nodeLabel} ${node.id}`;
      const nodeMetric = nodeLosMap.get(String(node.id));
      const nodeTooltipText = nodeMetric
        ? `${nodeName} · ${delayLabel} ${nodeMetric.delay.toFixed(1)}${secondsUnit} · LOS ${nodeMetric.los}`
        : nodeName;

      const hit = gHit.append("circle")
        .attr("cx", cx)
        .attr("cy", cy)
        .attr("r", 14)
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

    // Zoom (preserve transform across redraws)
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 10])
      .on("zoom", (event) => {
        g.attr("transform", event.transform);
        zoomTransformRef.current = event.transform;
        // One projection emit per animation frame while zooming/panning, so an
        // overlay redraw can never outpace the browser's paint rate.
        if (onProjectionChangeRef.current && !projectionRafRef.current) {
          projectionRafRef.current = requestAnimationFrame(() => {
            projectionRafRef.current = 0;
            emitProjection(zoomTransformRef.current);
          });
        }
      });
    svg.call(zoom);
    svg.call(zoom.transform, zoomTransformRef.current);
    // Synchronous emit so the overlay has the fresh fit immediately after a
    // redraw/resize (the rAF path above only covers live zoom/pan gestures).
    emitProjection(zoomTransformRef.current);

    // Reapply layer visibility after this fresh redraw (groups were just rebuilt);
    // read the ref so this effect never depends on `hidden` (toggling stays a
    // pure display flip, not a data re-render).
    applyLayerVisibility(svgRef.current, hiddenRef.current);
  }, [network, nodeLosMap, selectedNodeSet, selectedLinkId, onNodeClick, onLinkClick, t, sizeNonce]);

  return (
    <div className="relative w-full h-full">
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

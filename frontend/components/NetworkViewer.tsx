"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { NetworkData, ScenarioResults } from "@/lib/api";
import { LOS_COLORS, LOS_GRADE_THRESHOLDS, calculateNodeAverageDelay, resolveLosGrade, type LosGrade } from "@/lib/los";
import { useLanguage } from "@/components/LanguageProvider";

interface Props {
  network: NetworkData;
  results?: Record<string, ScenarioResults>;
  selectedScenario?: string;
  selectedNodeIds?: string[];
  selectedLinkId?: string | null;
  onNodeClick?: (nodeId: string, nodeName: string) => void;
  onLinkClick?: (linkId: string, linkName: string, isConnector: boolean) => void;
}

// Road-surface palette — all drawn from the file's existing slate family; no new
// saturated hues, gradients or shadows (Vissim-style flat asphalt ribbons).
const ASPHALT_FILL = "#3a4150";   // road surface (mid slate)
const ROAD_CASING = "#232a36";    // darker slate under the asphalt (road edge)
const MARKING_COLOR = "#94a3b8";  // muted light slate — lane dividers, chevrons, junction outline
const CONNECTOR_COLOR = "#6a6862";
const NODE_POLY_FILL = "#475569"; // neutral translucent slate for junctions without LOS
const NODE_FILL = "#1e293b";
const NODE_STROKE = "#f8fafc";
const ACCENT = "#4c8dff";

// Geometry constants for the road/marking math.
const DEFAULT_LANE_M = 3.5;        // fallback lane width when a link carries none
const EPS = 1e-6;
const MITER_COS_MIN = 0.5;         // clamp miter to ≤ 2× so sharp bends never spike offsets
const MIN_ROAD_PX = 2.5;           // asphalt floor at base fit (scales up with zoom)
const CHEVRON_SPACING_PX = 120;    // one direction chevron per ~this many base-fit px
const MAX_CHEVRONS = 8;            // cap per link (keeps SVG node count modest)

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

export default function NetworkViewer({
  network,
  results,
  selectedScenario,
  selectedNodeIds,
  selectedLinkId,
  onNodeClick,
  onLinkClick,
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
    if (!network.links.length) return;

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

    const linkLabel = t("링크", "Link");
    const nodeLabel = t("노드", "Node");
    const connectorLabel = t("연결로", "Connector");
    const delayLabel = t("평균지연", "Avg delay");
    const secondsUnit = t("초", "s");

    const g = svg.append("g");
    // Z-order layers (bottom -> top): casings -> asphalt fills -> lane markings
    // & chevrons -> connectors -> node polygons -> selection rings & dots ->
    // invisible hit targets. Drawing all casings before all fills lets touching
    // links' surfaces merge at junctions instead of showing casing seams.
    const gCasing = g.append("g");
    const gFill = g.append("g");
    const gMarking = g.append("g");
    const gConnector = g.append("g");
    const gNodePoly = g.append("g");
    const gOverlay = g.append("g");
    const gHit = g.append("g");

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
        const roadW = isConn ? Math.max(widthPx * 0.85, 2) : Math.max(widthPx, MIN_ROAD_PX);
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

    // Layer 1 — casings (all roads first, so junctions merge cleanly).
    roads.forEach(({ pointsAttr, roadW }) => {
      gCasing.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", ROAD_CASING)
        .attr("stroke-width", roadW + 2)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("pointer-events", "none");
    });

    // Layer 2 — asphalt fills. Selection recolors the fill stroke to accent.
    roads.forEach(({ pointsAttr, roadW, isSel }) => {
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

    // Layer 4 — connectors: width-true, muted, dashed, slightly translucent.
    connectors.forEach(({ pointsAttr, roadW, isSel }) => {
      gConnector.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", isSel ? ACCENT : CONNECTOR_COLOR)
        .attr("stroke-width", roadW)
        .attr("stroke-dasharray", "6 5")
        .attr("opacity", isSel ? 0.95 : 0.7)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("pointer-events", "none");
    });

    // Layer 5 — node polygons (junction footprints), above the road layer.
    // LOS color moves here as a low-opacity fill where a polygon exists; a
    // neutral translucent slate stands in when the node has no metric.
    network.nodes.forEach((node) => {
      const poly = node.polygon && node.polygon.length >= 3 ? node.polygon.map((p) => [projX(p[0]), projY(p[1])] as Pt) : null;
      if (!poly) return;
      const selected = selectedNodeSet.has(String(node.id));
      const nodeMetric = nodeLosMap.get(String(node.id));
      gNodePoly.append("polygon")
        .attr("points", poly.map((p) => p.join(",")).join(" "))
        .attr("fill", nodeMetric ? LOS_COLORS[nodeMetric.los] : NODE_POLY_FILL)
        .attr("fill-opacity", nodeMetric ? 0.32 : 0.26)
        .attr("stroke", selected ? ACCENT : MARKING_COLOR)
        .attr("stroke-opacity", selected ? 0.95 : 0.7)
        .attr("stroke-width", selected ? 2 : 1.2)
        .attr("stroke-linejoin", "round")
        .attr("vector-effect", "non-scaling-stroke")
        .style("pointer-events", "none");
    });

    // Layer 6 — selection rings + center dots. Dot keeps its LOS fill only when
    // the node has no polygon; polygon nodes get a small neutral center marker.
    network.nodes.forEach((node) => {
      const cx = projX(node.x);
      const cy = projY(node.y);
      const hasPoly = !!(node.polygon && node.polygon.length >= 3);
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

      const dotFill = hasPoly ? NODE_FILL : nodeMetric ? LOS_COLORS[nodeMetric.los] : NODE_FILL;
      gOverlay.append("circle")
        .attr("cx", cx)
        .attr("cy", cy)
        .attr("r", hasPoly ? 4.5 : selected ? 7 : 6)
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
      });
    svg.call(zoom);
    svg.call(zoom.transform, zoomTransformRef.current);
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
                <line x1="0" y1="6" x2="18" y2="6" stroke={CONNECTOR_COLOR} strokeWidth="2" strokeDasharray="4 3" strokeLinecap="round" opacity="0.7" />
              </svg>
              <span>{t("연결로", "Connector")}</span>
            </div>
            <div className="flex items-center gap-2">
              <svg width="18" height="14" aria-hidden="true">
                <polygon points="3,7 5,3 13,3 15,7 13,11 5,11" fill={NODE_POLY_FILL} fillOpacity="0.32" stroke={MARKING_COLOR} strokeWidth="1" strokeLinejoin="round" />
                <circle cx="9" cy="7" r="1.7" fill={NODE_FILL} stroke={NODE_STROKE} strokeWidth="0.8" />
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

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

const REGULAR_LINK_COLOR = "#94a3b8";
const CONNECTOR_COLOR = "#6a6862";
const NODE_FILL = "#1e293b";
const NODE_STROKE = "#f8fafc";
const ACCENT = "#4c8dff";

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

    const scX = d3.scaleLinear().domain([xMin, xMin + spanX]).range([pad, W - pad]);
    const scY = d3.scaleLinear().domain([yMin, yMin + spanY]).range([H - pad, pad]);

    const linkLabel = t("링크", "Link");
    const nodeLabel = t("노드", "Node");
    const connectorLabel = t("연결로", "Connector");
    const delayLabel = t("평균지연", "Avg delay");
    const secondsUnit = t("초", "s");

    const g = svg.append("g");

    // Click-vs-drag guard: d3.zoom swallows most post-drag clicks, but track the
    // pointer-down position as a belt-and-suspenders check.
    const down = { x: 0, y: 0 };
    svg.on("mousedown.guard", (event: MouseEvent) => {
      down.x = event.clientX;
      down.y = event.clientY;
    });
    const isDragClick = (event: MouseEvent) =>
      event.defaultPrevented || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5;

    // Draw links
    network.links.forEach((link) => {
      if (!link.points || link.points.length < 2) return;

      const pts = link.points.map((p) => [scX(p[0]), scY(p[1])] as [number, number]);
      const pointsAttr = pts.map((p) => p.join(",")).join(" ");
      const isConn = !!link.is_connector;
      const isSel = selectedLinkId != null && String(selectedLinkId) === String(link.id);
      // Links always render neutral: per-link result metrics do not exist
      // anywhere in this system (see docs/phase2/DECISIONS.md), so there is no
      // real value to color a link by.
      const stroke = isSel ? ACCENT : isConn ? CONNECTOR_COLOR : REGULAR_LINK_COLOR;
      const baseWidth = isConn ? Math.max(1, link.lanes * 0.8) : Math.max(1.5, link.lanes * 1.2);
      const width = isSel ? baseWidth + 1.6 : baseWidth;

      const line = g.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", stroke)
        .attr("stroke-width", width)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("pointer-events", "none");
      if (isConn) {
        line.attr("stroke-dasharray", "6 5").attr("opacity", isSel ? 0.95 : 0.7);
      }

      const linkName = link.name || `${isConn ? connectorLabel : linkLabel} ${link.id}`;
      const tooltipText = () => {
        if (isConn) {
          return link.from_link != null && link.to_link != null
            ? `${connectorLabel} ${link.id}: ${link.from_link} → ${link.to_link}`
            : `${connectorLabel} ${link.id}`;
        }
        return linkName;
      };

      const hit = g.append("polyline")
        .attr("points", pointsAttr)
        .attr("stroke", "transparent")
        .attr("stroke-width", Math.max(12, width + 8))
        .attr("fill", "none")
        .style("cursor", onLinkClick ? "pointer" : "default")
        .on("mousemove", (event: MouseEvent) => {
          setTooltip({ x: event.clientX, y: event.clientY, text: tooltipText() });
        })
        .on("mouseleave", () => setTooltip(null));
      if (onLinkClick) {
        hit.on("click", (event: MouseEvent) => {
          if (isDragClick(event)) return;
          onLinkClick(String(link.id), linkName, isConn);
        });
      }
    });

    // Draw nodes
    network.nodes.forEach((node) => {
      const cx = scX(node.x);
      const cy = scY(node.y);
      const selected = selectedNodeSet.has(String(node.id));
      const nodeName = node.name || `${nodeLabel} ${node.id}`;
      const nodeMetric = nodeLosMap.get(String(node.id));
      const nodeFill = nodeMetric ? LOS_COLORS[nodeMetric.los] : NODE_FILL;
      const nodeTooltipText = nodeMetric
        ? `${nodeName} · ${delayLabel} ${nodeMetric.delay.toFixed(1)}${secondsUnit} · LOS ${nodeMetric.los}`
        : nodeName;

      if (selected) {
        g.append("circle")
          .attr("cx", cx)
          .attr("cy", cy)
          .attr("r", 11)
          .attr("fill", "none")
          .attr("stroke", ACCENT)
          .attr("stroke-width", 2)
          .style("pointer-events", "none");
      }

      g.append("circle")
        .attr("cx", cx)
        .attr("cy", cy)
        .attr("r", selected ? 7 : 6)
        .attr("fill", nodeFill)
        .attr("stroke", selected ? ACCENT : NODE_STROKE)
        .attr("stroke-width", 1.5)
        .style("pointer-events", "none");

      const hit = g.append("circle")
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
              <svg width="16" height="8" aria-hidden="true">
                <line x1="0" y1="4" x2="16" y2="4" stroke={REGULAR_LINK_COLOR} strokeWidth="2.5" strokeLinecap="round" />
              </svg>
              <span>{t("일반 링크", "Link")}</span>
            </div>
            <div className="flex items-center gap-2">
              <svg width="16" height="8" aria-hidden="true">
                <line x1="0" y1="4" x2="16" y2="4" stroke={CONNECTOR_COLOR} strokeWidth="2" strokeDasharray="4 3" strokeLinecap="round" />
              </svg>
              <span>{t("연결로", "Connector")}</span>
            </div>
            <div className="flex items-center gap-2">
              <svg width="16" height="12" aria-hidden="true">
                <circle cx="8" cy="6" r="4" fill={NODE_FILL} stroke={NODE_STROKE} strokeWidth="1.5" />
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

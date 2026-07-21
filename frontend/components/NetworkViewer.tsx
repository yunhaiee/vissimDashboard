"use client";
import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import { NetworkData, ScenarioResults } from "@/lib/api";

type Metric = "delay" | "speed" | "queue";

interface Props {
  network: NetworkData;
  results?: Record<string, ScenarioResults>;
  selectedScenario?: string;
  metric?: Metric;
}

const METRIC_LABEL: Record<Metric, string> = {
  delay: "평균지연 (초)",
  speed: "평균속도 (km/h)",
  queue: "대기열지연 (초)",
};

export default function NetworkViewer({ network, results, selectedScenario, metric = "delay" }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);

  useEffect(() => {
    if (!svgRef.current || !network.links.length) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const allPts = network.links.flatMap((l) => l.points);
    const xs = allPts.map((p) => p[0]);
    const ys = allPts.map((p) => p[1]);
    const [xMin, xMax] = [Math.min(...xs), Math.max(...xs)];
    const [yMin, yMax] = [Math.min(...ys), Math.max(...ys)];

    const W = svgRef.current.clientWidth || 900;
    const H = svgRef.current.clientHeight || 600;
    const pad = 40;

    const scX = d3.scaleLinear().domain([xMin, xMax]).range([pad, W - pad]);
    const scY = d3.scaleLinear().domain([yMin, yMax]).range([H - pad, pad]);

    // Build metric lookup: link_id → value
    const metricMap = new Map<string, number>();
    if (results && selectedScenario && results[selectedScenario]) {
      const r = results[selectedScenario];
      if (metric === "delay") {
        r.movements.forEach((m) => {
          metricMap.set(String(m.OBJECT_ID), m.VEHDELAY_MEAN as number);
        });
      } else if (metric === "speed") {
        r.datacollection.forEach((d) => {
          metricMap.set(String(d.OBJECT_ID), d.SPEEDAVGARITH_MEAN as number);
        });
      } else if (metric === "queue") {
        r.datacollection.forEach((d) => {
          metricMap.set(String(d.OBJECT_ID), d.QUEUEDELAY_MEAN as number);
        });
      }
    }

    const vals = Array.from(metricMap.values()).filter((v) => v != null && !isNaN(v));
    const colorScale = d3.scaleSequential(d3.interpolateRdYlGn).domain(
      metric === "speed"
        ? [Math.min(...vals) || 0, Math.max(...vals) || 60]
        : [Math.max(...vals) || 60, Math.min(...vals) || 0]
    );

    const g = svg.append("g");

    // Draw links
    network.links.forEach((link) => {
      const pts = link.points.map((p) => [scX(p[0]), scY(p[1])] as [number, number]);
      const val = metricMap.get(link.id);
      const color = val != null && vals.length > 0 ? colorScale(val) : "#94a3b8";
      const width = Math.max(1.5, link.lanes * 1.2);

      g.append("polyline")
        .attr("points", pts.map((p) => p.join(",")).join(" "))
        .attr("stroke", color)
        .attr("stroke-width", width)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("stroke-linejoin", "round")
        .style("cursor", "pointer")
        .on("mousemove", (event) => {
          const text = val != null
            ? `Link ${link.id} | ${METRIC_LABEL[metric]}: ${val.toFixed(2)}`
            : `Link ${link.id} | 데이터 없음`;
          setTooltip({ x: event.clientX, y: event.clientY, text });
        })
        .on("mouseleave", () => setTooltip(null));
    });

    // Draw nodes
    network.nodes.forEach((node) => {
      g.append("circle")
        .attr("cx", scX(node.x))
        .attr("cy", scY(node.y))
        .attr("r", 6)
        .attr("fill", "#1e293b")
        .attr("stroke", "#f8fafc")
        .attr("stroke-width", 1.5)
        .append("title")
        .text(node.name || `Node ${node.id}`);
    });

    // Zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 10])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);
  }, [network, results, selectedScenario, metric]);

  return (
    <div className="relative w-full h-full">
      <svg ref={svgRef} className="w-full h-full bg-slate-900 rounded-xl" />
      {tooltip && (
        <div
          className="fixed z-50 bg-slate-800 text-white text-xs px-3 py-1.5 rounded shadow-lg pointer-events-none"
          style={{ left: tooltip.x + 12, top: tooltip.y - 10 }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  );
}

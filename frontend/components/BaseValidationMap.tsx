"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { CircleMarker, LeafletMouseEvent, Map as LeafletMap, TileLayer } from "leaflet";

import { useTheme } from "@/components/ThemeProvider";
import { basemapTileUrl } from "@/lib/basemap";

import { useLanguage } from "@/components/LanguageProvider";
import { apiFetch } from "@/lib/api";

export type BaseValidationMapNode = {
  nodeId: string;
  nodeName: string;
  observedTotal: number | null;
  modelTotal: number | null;
  diff: number | null;
  pctError: number | null;
  geh: number | null;
  validCount: number;
  missingCount: number;
  issueScore: number;
};

type NodeFeature = { id: string; name: string; lat: number; lon: number };
type LinkFeature = { id: string; coords: [number, number][] };
type OverlayMode = "geh" | "flow_band";

type NodeRenderStyle = {
  fill: string;
  stroke: string;
  radius: number;
  weight: number;
  opacity: number;
};

type LegendRow = {
  color: string;
  border?: string;
  label: string;
};

type Domain = {
  min: number;
  max: number;
};

interface Props {
  nodes: BaseValidationMapNode[];
  selectedNodeIds: string[];
  activeNodeId: string | null;
  onNodeClick: (nodeId: string, nodeName: string) => void;
  datasetRevision?: number | string;
}

const GEO_PARAMS = "scale_x=1.178200&scale_y=1.178200&offset_x=14427.6419&offset_y=-4221.3566&rotation_deg=-0.45759";

const OVERLAY_OPTIONS: Array<{ value: OverlayMode; ko: string; en: string; ja: string }> = [
  { value: "geh", ko: "GEH", en: "GEH", ja: "GEH" },
  { value: "flow_band", ko: "교통량 절대값 비교", en: "Flow Difference Bands", ja: "交通量絶対値比較" },
];

export default function BaseValidationMap({
  nodes,
  selectedNodeIds,
  activeNodeId,
  onNodeClick,
  datasetRevision = 0,
}: Props) {
  const { t, lang } = useLanguage();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const { theme } = useTheme();
  const tileUrl = basemapTileUrl(theme);
  const tileUrlRef = useRef(tileUrl);
  const tileLayerRef = useRef<TileLayer | null>(null);

  // The basemap follows the theme without remounting the map: swapping the
  // URL on the live layer is a Leaflet primitive (TileLayer#setUrl), and the
  // ref lets a mount still awaiting the leaflet import pick up the latest
  // choice instead of the one captured when the effect was scheduled.
  useEffect(() => {
    tileUrlRef.current = tileUrl;
    tileLayerRef.current?.setUrl(tileUrl);
  }, [tileUrl]);
  const linksLayerRef = useRef<{ remove: () => void; addTo: (map: LeafletMap) => void } | null>(null);
  const nodesLayerRef = useRef<{ remove: () => void; addTo: (map: LeafletMap) => void } | null>(null);

  const [ready, setReady] = useState(false);
  const [mapNodes, setMapNodes] = useState<NodeFeature[]>([]);
  const [links, setLinks] = useState<LinkFeature[]>([]);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [overlayMode, setOverlayMode] = useState<OverlayMode>("geh");

  const metricByNodeId = useMemo(() => {
    const map = new Map<string, BaseValidationMapNode>();
    for (const node of nodes) {
      map.set(String(node.nodeId), node);
    }
    return map;
  }, [nodes]);
  const metricByNodeName = useMemo(() => {
    const map = new Map<string, BaseValidationMapNode>();
    for (const node of nodes) {
      const key = normalizeNodeName(node.nodeName);
      if (key && !map.has(key)) {
        map.set(key, node);
      }
    }
    return map;
  }, [nodes]);

  const selectedNodeSet = useMemo(() => new Set(selectedNodeIds.map(String)), [selectedNodeIds]);
  const volumeDomain = useMemo(() => buildDomain(nodes.map((node) => node.observedTotal ?? node.modelTotal)), [nodes]);
  const issueDomain = useMemo(() => buildDomain(nodes.map((node) => node.issueScore)), [nodes]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    let cancelled = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !containerRef.current) return;

      const container = containerRef.current;
      (container as { _leaflet_id?: number | null })._leaflet_id = null;
      container.innerHTML = "";

      const map = L.map(container, {
        zoomControl: true,
        preferCanvas: true,
        dragging: true,
        inertia: true,
        scrollWheelZoom: true,
        doubleClickZoom: true,
      });
      mapRef.current = map;
      map.setView([-32.0696, 115.8413], 15);
      map.getContainer().style.cursor = "grab";
      map.on("dragstart", () => {
        map.getContainer().style.cursor = "grabbing";
      });
      map.on("dragend", () => {
        map.getContainer().style.cursor = "grab";
      });

      tileLayerRef.current = L.tileLayer(tileUrlRef.current, {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org">OSM</a> &copy; <a href="https://carto.com">CARTO</a>',
        maxZoom: 20,
      }).addTo(map);

      if (!cancelled) setReady(true);
    })();

    return () => {
      cancelled = true;
      linksLayerRef.current?.remove();
      linksLayerRef.current = null;
      nodesLayerRef.current?.remove();
      nodesLayerRef.current = null;

      const map = mapRef.current;
      if (map) {
        map.off();
        map.stop();
        map.remove();
      }
      mapRef.current = null;
      if (containerRef.current) {
        (containerRef.current as { _leaflet_id?: number | null })._leaflet_id = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!ready) return;

    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(`/api/geojson/network?${GEO_PARAMS}&reload=${encodeURIComponent(String(datasetRevision))}`, {
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`Network API responded with ${res.status}`);
        if (cancelled) return;

        const geojson = await res.json();
        const features = Array.isArray(geojson?.features) ? geojson.features : [];

        const nextNodes: NodeFeature[] = features
          .filter((f: { properties: { type: string }; geometry: { type: string } }) =>
            f.properties.type === "node" && f.geometry.type === "Point",
          )
          .map((f: { properties: { id: string; name?: string }; geometry: { coordinates: number[] } }) => {
            const [lon, lat] = f.geometry.coordinates;
            return {
              id: String(f.properties.id),
              name: f.properties.name || `Node ${f.properties.id}`,
              lat,
              lon,
            };
          });

        const nextLinks: LinkFeature[] = features
          .filter((f: { properties: { type: string }; geometry: { type: string } }) =>
            f.properties.type === "link" && f.geometry.type === "LineString",
          )
          .map((f: { properties: { id: string }; geometry: { coordinates: number[][] } }) => ({
            id: String(f.properties.id),
            coords: f.geometry.coordinates.map(([lon, lat]) => [lat, lon] as [number, number]),
          }));

        if (!cancelled) {
          setMapNodes(nextNodes);
          setLinks(nextLinks);
          setNetworkError(null);
        }

        const L = (await import("leaflet")).default;
        if (!cancelled && mapRef.current && nextNodes.length > 0) {
          const bounds = L.latLngBounds(nextNodes.map((node) => [node.lat, node.lon]));
          mapRef.current.fitBounds(bounds, { padding: [56, 56] });
        }
      } catch (error) {
        if (cancelled) return;
        setMapNodes([]);
        setLinks([]);
        setNetworkError(error instanceof Error ? error.message : "Failed to fetch map data");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [datasetRevision, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    linksLayerRef.current?.remove();
    linksLayerRef.current = null;
    if (links.length === 0) return;

    let cancelled = false;
    let localLayer: { remove: () => void; addTo: (map: LeafletMap) => void } | null = null;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || mapRef.current !== map) return;

      const layer = L.layerGroup();
      localLayer = layer;
      for (const link of links) {
        L.polyline(link.coords, {
          color: "#94a3b8",
          weight: 3,
          opacity: 0.42,
          lineCap: "round",
          lineJoin: "round",
          interactive: false,
        }).addTo(layer);
      }
      if (cancelled || mapRef.current !== map) {
        layer.remove();
        return;
      }
      layer.addTo(map);
      linksLayerRef.current = layer;
    })();

    return () => {
      cancelled = true;
      localLayer?.remove();
      if (linksLayerRef.current === localLayer) {
        linksLayerRef.current = null;
      }
    };
  }, [links]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    nodesLayerRef.current?.remove();
    nodesLayerRef.current = null;
    if (mapNodes.length === 0) return;

    let cancelled = false;
    let localLayer: { remove: () => void; addTo: (map: LeafletMap) => void } | null = null;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || mapRef.current !== map) return;

      const layer = L.layerGroup();
      localLayer = layer;
      const orderedNodes = [...mapNodes].sort((a, b) => {
        const aHasMetric = metricByNodeId.has(a.id) ? 1 : 0;
        const bHasMetric = metricByNodeId.has(b.id) ? 1 : 0;
        if (aHasMetric !== bHasMetric) return aHasMetric - bHasMetric;
        return a.id.localeCompare(b.id, undefined, { numeric: true });
      });

      for (const node of orderedNodes) {
        if (cancelled || mapRef.current !== map) {
          layer.remove();
          return;
        }

        const metric = resolveMetricForFeature(node, metricByNodeId, metricByNodeName);
        const selected =
          node.id === activeNodeId ||
          selectedNodeSet.has(node.id) ||
          (metric != null && (metric.nodeId === activeNodeId || selectedNodeSet.has(metric.nodeId)));
        const render = resolveNodeRender(metric, overlayMode, selected, volumeDomain, issueDomain);
        const marker = L.circleMarker([node.lat, node.lon], {
          radius: render.radius,
          fillColor: render.fill,
          color: render.stroke,
          weight: render.weight,
          fillOpacity: render.opacity,
          opacity: 1,
          interactive: true,
        }) as CircleMarker;

        if (metric) {
          marker.on("mouseover", () => {
            if (!selected) marker.setStyle({ radius: render.radius + 2, fillOpacity: Math.min(1, render.opacity + 0.12) });
          });
          marker.on("mouseout", () => {
            if (!selected) marker.setStyle({ radius: render.radius, fillOpacity: render.opacity });
          });
          marker.on("click", (e: LeafletMouseEvent) => {
            L.DomEvent.stopPropagation(e.originalEvent);
            onNodeClick(metric.nodeId, metric.nodeName || node.name);
          });
        } else {
          marker.on("mouseover", () => {
            marker.setStyle({ radius: render.radius + 1.5, fillOpacity: Math.min(1, render.opacity + 0.1) });
          });
          marker.on("mouseout", () => {
            marker.setStyle({ radius: render.radius, fillOpacity: render.opacity });
          });
        }

        marker.bindTooltip(buildTooltip(node.name, metric, t), {
          direction: "top",
          offset: [0, -render.radius - 3],
          className: "map-node-tooltip",
        });

        layer.addLayer(marker);
      }

      if (cancelled || mapRef.current !== map) {
        layer.remove();
        return;
      }
      layer.addTo(map);
      nodesLayerRef.current = layer;
    })();

    return () => {
      cancelled = true;
      localLayer?.remove();
      if (nodesLayerRef.current === localLayer) {
        nodesLayerRef.current = null;
      }
    };
  }, [activeNodeId, issueDomain, mapNodes, metricByNodeId, metricByNodeName, onNodeClick, overlayMode, selectedNodeSet, t, volumeDomain]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !activeNodeId) return;
    const node = mapNodes.find((item) => {
      if (item.id === activeNodeId) return true;
      const metric = resolveMetricForFeature(item, metricByNodeId, metricByNodeName);
      return metric?.nodeId === activeNodeId;
    });
    if (!node) return;
    const zoom = Math.max(map.getZoom(), 16);
    map.setView([node.lat, node.lon], zoom, { animate: true });
  }, [activeNodeId, mapNodes, metricByNodeId, metricByNodeName]);

  const legend = useMemo(() => buildLegend(overlayMode, t), [overlayMode, t]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-xl">
      <div ref={containerRef} className="h-full w-full" />

      {networkError && (
        <div className="pointer-events-none absolute left-3 top-3 z-[650] rounded-lg border border-rose-200 bg-rose-50/95 px-3 py-2 text-[14px] text-rose-700 shadow-sm">
          {t("맵 데이터를 불러오지 못했습니다:", "Failed to load map data:")} {networkError}
        </div>
      )}

      <div className="pointer-events-auto absolute right-3 top-3 z-[700] min-w-[220px] rounded-xl border border-slate-200 bg-surface/95 px-4 py-3 text-[15px] shadow-lg backdrop-blur-sm">
        <label className="mb-1.5 block text-[15px] font-semibold text-slate-700">{t("검증 표시 기준", "Validation Overlay")}</label>
        <select
          value={overlayMode}
          onChange={(event) => setOverlayMode(event.target.value as OverlayMode)}
          className="w-full rounded-lg border border-slate-300 bg-surface px-3 py-2 text-[15px] text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
        >
          {OVERLAY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {lang === "ko" ? option.ko : lang === "ja" ? option.ja : option.en}
            </option>
          ))}
        </select>
      </div>

      <div className="pointer-events-none absolute bottom-3 left-3 z-[500] max-w-[260px] select-none rounded-xl border border-slate-200 bg-surface/92 px-3 py-2.5 text-[13px] text-slate-700 shadow-lg backdrop-blur-sm">
        <p className="mb-1.5 font-semibold text-slate-800">{legend.title}</p>
        <div className="space-y-1">
          {legend.rows.map((row) => (
            <div key={`${row.color}-${row.label}`} className="flex items-center gap-2">
              <span
                className="inline-block h-3.5 w-7 rounded-[5px] border shadow-[inset_0_0_0_1px_rgba(255,255,255,0.3)]"
                style={{ backgroundColor: row.color, borderColor: row.border ?? "rgba(71,85,105,0.7)" }}
              />
              <span>{row.label}</span>
            </div>
          ))}
        </div>
        {legend.footnote && <p className="mt-2 text-[12px] text-slate-400">{legend.footnote}</p>}
      </div>
    </div>
  );
}

function resolveNodeRender(
  metric: BaseValidationMapNode | null,
  mode: OverlayMode,
  selected: boolean,
  volumeDomain: Domain | null,
  issueDomain: Domain | null,
): NodeRenderStyle {
  if (!metric) {
    return {
      fill: "#f8fafc",
      stroke: "#64748b",
      radius: 7,
      weight: 2,
      opacity: 0.82,
    };
  }

  const issueNormalized = normalize(metric.issueScore, issueDomain);
  const baseRadius = 8 + Math.sqrt(issueNormalized) * 8;
  let style: NodeRenderStyle;

  if (mode === "flow_band") {
    const pass = evaluateFlowBand(metric.observedTotal, metric.modelTotal);
    if (pass == null) {
      style = { fill: "#94a3b8", stroke: "#64748b", radius: baseRadius, weight: 2, opacity: 0.78 };
    } else if (pass) {
      style = { fill: "#16a34a", stroke: "#166534", radius: baseRadius, weight: 2, opacity: 0.82 };
    } else {
      style = { fill: "#dc2626", stroke: "#7f1d1d", radius: baseRadius, weight: 2.2, opacity: 0.88 };
    }
  } else if (metric.geh == null) {
    style = { fill: "#94a3b8", stroke: "#64748b", radius: baseRadius, weight: 2, opacity: 0.78 };
  } else if (metric.geh < 5) {
    style = { fill: "#16a34a", stroke: "#166534", radius: baseRadius, weight: 2, opacity: 0.82 };
  } else if (metric.geh < 10) {
    style = { fill: "#f59e0b", stroke: "#92400e", radius: baseRadius, weight: 2.15, opacity: 0.88 };
  } else {
    style = { fill: "#dc2626", stroke: "#7f1d1d", radius: baseRadius, weight: 2.35, opacity: 0.92 };
  }

  if (!selected) return style;
  return {
    ...style,
    radius: style.radius + 2.5,
    stroke: "#0f172a",
    weight: Math.max(style.weight + 1.2, 3.2),
    opacity: 0.96,
  };
}

function resolveMetricForFeature(
  node: NodeFeature,
  metricByNodeId: Map<string, BaseValidationMapNode>,
  metricByNodeName: Map<string, BaseValidationMapNode>,
): BaseValidationMapNode | null {
  return metricByNodeId.get(node.id) ?? metricByNodeName.get(normalizeNodeName(node.name)) ?? null;
}

function normalizeNodeName(value: string): string {
  return value
    .normalize("NFKC")
    .replace(/\s+/g, "")
    .replace(/[()（）\[\]{}]/g, "")
    .toLowerCase();
}

function buildTooltip(nodeName: string, metric: BaseValidationMapNode | null, t: (ko: string, en: string, ja?: string) => string): string {
  if (!metric) {
    return [
      `<div style="font-weight:700">${escapeHtml(nodeName)}</div>`,
      `<div style="color:#64748b;font-size:10px">${t("검증 데이터 없음", "No validation data")}</div>`,
    ].join("");
  }

  const biasLabel =
    metric.pctError == null
      ? "-"
      : metric.pctError > 0
        ? t("모델 과대", "Model high")
        : metric.pctError < 0
          ? t("모델 과소", "Model low")
          : t("일치", "Matched");

  return [
    `<div style="font-weight:700">${escapeHtml(metric.nodeName || nodeName)}</div>`,
    `<div style="color:#64748b;font-size:10px">GEH ${formatNumber(metric.geh, 1)} · ${formatSignedPct(metric.pctError)}</div>`,
    `<div style="color:#64748b;font-size:10px">${t("실측", "Observed")} ${formatNumber(metric.observedTotal, 0)} / ${t("모델", "Model")} ${formatNumber(metric.modelTotal, 0)}</div>`,
    `<div style="color:#64748b;font-size:10px">${biasLabel} · ${metric.validCount}${t("개 비교", " compared")}</div>`,
  ].join("");
}

function buildLegend(mode: OverlayMode, t: (ko: string, en: string, ja?: string) => string): { title: string; rows: LegendRow[]; footnote: string } {
  if (mode === "flow_band") {
    return {
      title: t("교통량 절대값 비교", "Flow Difference Bands"),
      rows: [
        { color: "#16a34a", label: t("기준 충족", "Within criterion") },
        { color: "#dc2626", label: t("기준 초과", "Exceeds criterion") },
        { color: "#94a3b8", label: t("비교 불가", "Not comparable") },
        { color: "#f8fafc", border: "#64748b", label: t("검증 데이터 없음", "No validation data") },
      ],
      footnote: t(
        "< 700 veh/h: 차이 ±100 / 700–2,700 veh/h: 차이 ±15% / > 2,700 veh/h: 차이 ±400 (목표 >90%)",
        "< 700 veh/h: diff ±100 · 700–2,700 veh/h: diff ±15% · > 2,700 veh/h: diff ±400 (target >90%)",
      ),
    };
  }

  return {
    title: t("GEH 등급", "GEH Grade"),
    rows: [
      { color: "#16a34a", label: "GEH < 5" },
      { color: "#f59e0b", label: "GEH 5 - 10" },
      { color: "#dc2626", label: "GEH >= 10" },
      { color: "#94a3b8", label: t("비교 불가", "Not comparable") },
      { color: "#f8fafc", border: "#64748b", label: t("검증 데이터 없음", "No validation data") },
    ],
    footnote: t("마커 크기는 GEH와 오차율을 함께 반영합니다.", "Marker size combines GEH and error rate."),
  };
}

function evaluateFlowBand(observed: number | null, model: number | null): boolean | null {
  if (observed == null || model == null) return null;
  const diff = Math.abs(model - observed);
  if (observed < 700) return diff <= 100;
  if (observed <= 2700) {
    if (observed <= 0) return diff <= 100;
    return (diff / observed) * 100 <= 15;
  }
  return diff <= 400;
}

function buildDomain(values: Array<number | null | undefined>): Domain | null {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    if (value == null || !Number.isFinite(value)) continue;
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  return { min, max };
}

function normalize(value: number | null | undefined, domain: Domain | null): number {
  if (value == null || !Number.isFinite(value)) return 0;
  if (!domain) return 0.5;
  const span = domain.max - domain.min;
  if (span < 1e-6) return value > 0 ? 0.75 : 0;
  return Math.max(0, Math.min(1, (value - domain.min) / span));
}

function formatNumber(value: number | null | undefined, digits: number): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSignedPct(value: number | null): string {
  if (value == null) return "-";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value, 1)}%`;
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

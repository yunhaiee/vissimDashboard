"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { CircleMarker, LeafletMouseEvent, Map as LeafletMap, TileLayer } from "leaflet";

import { useTheme } from "@/components/ThemeProvider";
import { basemapTileUrl } from "@/lib/basemap";
import {
  apiFetch,
  fetchSignals,
  type CompareSummaryResponse,
  type ScenarioResults,
  type SignalNodeInfo,
} from "@/lib/api";
import { useLanguage } from "@/components/LanguageProvider";

type NodeFeature = { id: string; name: string; lat: number; lon: number };
type LinkFeature = { id: string; coords: [number, number][] };

type OverlayMode = "intersection_type" | "los_delay" | "volume";
type IntersectionType = "signal" | "roundabout" | "unsignal";
type LosGrade = "A" | "B" | "C" | "D" | "E" | "F";

type NodeMetric = {
  type: IntersectionType;
  volume: number | null;
  volumeBand: number | null;
  delay: number | null;
  los: LosGrade | null;
};

type TFn = (ko: string, en: string, ja?: string) => string;

type RectBox = {
  left: number;
  top: number;
  right: number;
  bottom: number;
};

type ViewportSize = {
  width: number;
  height: number;
};

type CalloutResolved = {
  rect: RectBox;
  elbow: { x: number; y: number };
  anchor: { x: number; y: number };
  size: { width: number; height: number };
};

type VolumeDomain = {
  min: number;
  max: number;
};

type LegendRow = {
  color?: string;
  label: string;
  sign?: IntersectionType;
};

type NodeRenderStyle = {
  fill: string;
  stroke: string;
  radius: number;
  weight: number;
  opacity: number;
  haloRadius?: number;
  haloWeight?: number;
  haloOpacity?: number;
};

interface Props {
  summary: CompareSummaryResponse | null;
  selectedNodeId: string | null;
  selectedNodeIds?: string[];
  onNodeClick: (nodeId: string, nodeName: string) => void;
  selectedScenarioIds: string[];
  results: Record<string, ScenarioResults>;
  datasetRevision?: number | string;
}

const GEO_PARAMS = "scale_x=1.178200&scale_y=1.178200&offset_x=14427.6419&offset_y=-4221.3566&rotation_deg=-0.45759";

const OVERLAY_OPTIONS: Array<{ value: OverlayMode; ko: string; en: string; ja: string }> = [
  { value: "intersection_type", ko: "교차로 타입", en: "Intersection Type", ja: "交差点タイプ" },
  { value: "los_delay", ko: "LOS/지체시간", en: "LOS/Delay", ja: "LOS/遅延時間" },
  { value: "volume", ko: "통행량(시간당)", en: "Hourly Volume", ja: "交通量(時間あたり)" },
];

const VOLUME_BAND_STOPS = [500, 1000, 2000, 3000, 4000, 5000] as const;
// Tuned for higher contrast and clearer visual separation on desaturated basemaps.
const VOLUME_BAND_COLORS = ["#cfe2ff", "#a9cbff", "#7fb2f6", "#5498e4", "#2f7bc8", "#1f629f", "#124777"];
const VOLUME_BAND_STROKES = ["#7d97be", "#6283b2", "#4f74a8", "#3c638f", "#2f5377", "#224460", "#17364c"];
const INTERSECTION_COLOR_MAP: Record<IntersectionType, { fill: string; stroke: string }> = {
  signal: { fill: "#55799a", stroke: "#3e5f7d" },
  roundabout: { fill: "#7a6b94", stroke: "#5f5078" },
  unsignal: { fill: "#9a7a58", stroke: "#7b6044" },
};
// Roundabout hint by node id. Signal presence is auto-detected from the VISSIM
// file, but the inpx carries no roundabout marker, so non-signal roundabouts are
// listed here (used only when a node has NO signal controller).
const INTERSECTION_TYPE_OVERRIDE: Record<string, IntersectionType> = {
  "4": "roundabout",
  "6": "roundabout",
  "9": "roundabout",
};
const LOS_COLOR_MAP: Record<LosGrade, { fill: string; stroke: string }> = {
  A: { fill: "#16a34a", stroke: "#166534" },
  B: { fill: "#84cc16", stroke: "#4d7c0f" },
  C: { fill: "#facc15", stroke: "#a16207" },
  D: { fill: "#f97316", stroke: "#c2410c" },
  E: { fill: "#ef4444", stroke: "#991b1b" },
  F: { fill: "#7f1d1d", stroke: "#450a0a" },
};

export default function DashboardMap({
  summary,
  selectedNodeId,
  selectedNodeIds = [],
  onNodeClick,
  selectedScenarioIds,
  results,
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
  const [nodes, setNodes] = useState<NodeFeature[]>([]);
  const [links, setLinks] = useState<LinkFeature[]>([]);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [overlayMode, setOverlayMode] = useState<OverlayMode>("intersection_type");
  const [signalNodes, setSignalNodes] = useState<SignalNodeInfo[]>([]);
  const [viewNonce, setViewNonce] = useState(0);
  const selectedNodeSet = useMemo(() => new Set(selectedNodeIds.map(String)), [selectedNodeIds]);

  useEffect(() => {
    let cancelled = false;
    fetchSignals()
      .then((items) => {
        if (!cancelled) setSignalNodes(items);
      })
      .catch(() => {
        if (!cancelled) setSignalNodes([]);
      });

    return () => {
      cancelled = true;
    };
  }, [datasetRevision]);

  const summaryVolumeMap = useMemo(() => {
    const map = new Map<string, number>();
    for (const n of summary?.top_congested_nodes ?? []) {
      if (n.average_total != null) {
        map.set(String(n.node_id), Number(n.average_total) / 2);
      }
    }
    return map;
  }, [summary]);

  const fallbackVolumeMap = useMemo(
    () => buildVolumeMapFromNodeDirections(results, selectedScenarioIds),
    [results, selectedScenarioIds],
  );

  const delayMap = useMemo(
    () => buildDelayMapFromMovements(results, selectedScenarioIds),
    [results, selectedScenarioIds],
  );

  const signalNodeSet = useMemo(() => {
    const set = new Set<string>();
    for (const node of signalNodes) {
      if (node.has_signal) set.add(String(node.node_id));
    }
    return set;
  }, [signalNodes]);

  const nodeMetricMap = useMemo(() => {
    const map = new Map<string, NodeMetric>();
    for (const node of nodes) {
      // Signal presence is detected automatically from the VISSIM file
      // (/api/geojson/signals), so it always wins. The inpx has no roundabout
      // marker, so for non-signal nodes we fall back to a roundabout hint
      // (override map) or the name/unsignalised heuristic.
      const hasSignal = signalNodeSet.has(node.id);
      const type: IntersectionType = hasSignal
        ? "signal"
        : INTERSECTION_TYPE_OVERRIDE[node.id] === "roundabout"
          ? "roundabout"
          : resolveIntersectionType(node.name, false);
      const summaryVol = summaryVolumeMap.get(node.id);
      const fallbackVol = fallbackVolumeMap.get(node.id);
      const volume = summaryVol ?? fallbackVol ?? null;
      const delay = delayMap.get(node.id) ?? null;

      map.set(node.id, {
        type,
        volume,
        volumeBand: volume != null ? resolveVolumeBand(volume) : null,
        delay,
        los: delay != null ? resolveLosGrade(delay) : null,
      });
    }
    return map;
  }, [delayMap, fallbackVolumeMap, nodes, signalNodeSet, summaryVolumeMap]);

  const volumeDomain = useMemo<VolumeDomain | null>(() => {
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const metric of nodeMetricMap.values()) {
      if (metric.volume == null) continue;
      min = Math.min(min, metric.volume);
      max = Math.max(max, metric.volume);
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    return { min, max };
  }, [nodeMetricMap]);

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
      map.dragging.enable();
      map.setView([-32.0696, 115.8413], 15);
      map.getContainer().style.cursor = "grab";
      map.on("dragstart", () => {
        map.getContainer().style.cursor = "grabbing";
      });
      map.on("dragend", () => {
        map.getContainer().style.cursor = "grab";
      });
      map.on("zoomend moveend", () => {
        setViewNonce((prev) => prev + 1);
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

        const nodeFeatures: NodeFeature[] = features
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

        const linkFeatures: LinkFeature[] = features
          .filter((f: { properties: { type: string }; geometry: { type: string } }) =>
            f.properties.type === "link" && f.geometry.type === "LineString",
          )
          .map((f: { properties: { id: string }; geometry: { coordinates: number[][] } }) => ({
            id: String(f.properties.id),
            coords: f.geometry.coordinates.map(([lon, lat]) => [lat, lon] as [number, number]),
          }));

        if (!cancelled) {
          setNodes(nodeFeatures);
          setLinks(linkFeatures);
          setNetworkError(null);
        }

        const L = (await import("leaflet")).default;
        if (!cancelled && mapRef.current && nodeFeatures.length > 0) {
          const bounds = L.latLngBounds(nodeFeatures.map((n) => [n.lat, n.lon]));
          mapRef.current.fitBounds(bounds, { padding: [60, 60] });
        }
      } catch (error) {
        if (cancelled) return;
        setNodes([]);
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
          opacity: 0.55,
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
    if (nodes.length === 0) return;

    let cancelled = false;
    let localLayer: { remove: () => void; addTo: (map: LeafletMap) => void } | null = null;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || mapRef.current !== map) return;

      const layer = L.layerGroup();
      localLayer = layer;
      const mapSize = map.getSize();
      const viewport: ViewportSize = { width: mapSize.x, height: mapSize.y };
      const placedRects: RectBox[] = [];

      // Scale the intersection sign markers down as the map zooms out so the
      // closely-spaced Murdoch Dr intersections don't overlap. Full size at
      // zoom >= 16, shrinking to ~30% when zoomed further out.
      const signZoom = map.getZoom();
      const signScale = Math.min(1, Math.max(0.3, Math.pow(1.7, signZoom - 16)));

      const orderedNodes = [...nodes].sort((a, b) => {
        if (a.lon === b.lon) return a.lat - b.lat;
        return a.lon - b.lon;
      });

      for (let nodeIndex = 0; nodeIndex < orderedNodes.length; nodeIndex += 1) {
        if (cancelled || mapRef.current !== map) {
          layer.remove();
          return;
        }
        const node = orderedNodes[nodeIndex];
        const metric = nodeMetricMap.get(node.id) ?? null;
        const selected = node.id === selectedNodeId || selectedNodeSet.has(node.id);
        const render = resolveNodeRender(metric, overlayMode, selected, lang, volumeDomain);

        if (overlayMode === "intersection_type" && metric) {
          const sign = buildIntersectionSignIcon(
            metric.type,
            selected,
            Math.round((selected ? 54 : 46) * signScale),
          );
          const marker = L.marker([node.lat, node.lon], {
            icon: L.divIcon({
              className: "dashboard-intersection-sign-icon",
              html: sign.html,
              iconSize: [sign.size, sign.size],
              iconAnchor: [sign.size / 2, sign.size / 2],
            }),
            interactive: true,
            keyboard: false,
            zIndexOffset: selected ? 1000 : 500,
          });

          marker.on("click", (e: LeafletMouseEvent) => {
            L.DomEvent.stopPropagation(e.originalEvent);
            onNodeClick(node.id, node.name);
          });

          marker.bindTooltip(buildNodeTooltip(node.name, metric, t), {
            direction: "top",
            offset: [0, -sign.size / 2 - 3],
            className: "map-node-tooltip",
          });

          layer.addLayer(marker);
          continue;
        }

        if (overlayMode === "volume" && render.style.haloRadius != null && render.style.haloRadius > render.style.radius) {
          const halo = L.circleMarker([node.lat, node.lon], {
            radius: render.style.haloRadius,
            fillColor: render.style.fill,
            color: render.style.stroke,
            weight: render.style.haloWeight ?? 1.5,
            fillOpacity: render.style.haloOpacity ?? 0.12,
            opacity: 0.5,
            interactive: false,
          });
          layer.addLayer(halo);
        }

        const marker = L.circleMarker([node.lat, node.lon], {
          radius: render.style.radius,
          fillColor: render.style.fill,
          color: render.style.stroke,
          weight: render.style.weight,
          fillOpacity: render.style.opacity,
          opacity: 1,
          interactive: true,
        }) as CircleMarker;

        marker.on("mouseover", () => {
          if (!selected) marker.setStyle({ radius: render.style.radius + 2, fillOpacity: 1 });
        });
        marker.on("mouseout", () => {
          if (!selected) marker.setStyle({ radius: render.style.radius, fillOpacity: render.style.opacity });
        });
        marker.on("click", (e: LeafletMouseEvent) => {
          L.DomEvent.stopPropagation(e.originalEvent);
          onNodeClick(node.id, node.name);
        });

        marker.bindTooltip(buildNodeTooltip(node.name, metric, t), {
          direction: "top",
          offset: [0, -render.style.radius - 2],
          className: "map-node-tooltip",
        });

        layer.addLayer(marker);

        if (overlayMode === "intersection_type" && render.badge) {
          const originPoint = map.latLngToContainerPoint([node.lat, node.lon]);
          const callout = resolveCalloutPlacement({
            origin: { x: originPoint.x, y: originPoint.y },
            radius: render.style.radius,
            label: render.badge,
            seed: nodeIndex,
            placedRects,
            viewport,
          });
          placedRects.push(callout.rect);

          const vecX = callout.elbow.x - originPoint.x;
          const vecY = callout.elbow.y - originPoint.y;
          const vecLen = Math.max(Math.hypot(vecX, vecY), 0.001);
          const startInset = Math.max(render.style.radius * 0.92, 8);
          const startPoint = {
            x: originPoint.x + (vecX / vecLen) * startInset,
            y: originPoint.y + (vecY / vecLen) * startInset,
          };

          const startLatLng = map.containerPointToLatLng([startPoint.x, startPoint.y]);
          const elbowLatLng = map.containerPointToLatLng([callout.elbow.x, callout.elbow.y]);
          const anchorLatLng = map.containerPointToLatLng([callout.anchor.x, callout.anchor.y]);

          const connector = L.polyline(
            [
              [startLatLng.lat, startLatLng.lng],
              [elbowLatLng.lat, elbowLatLng.lng],
              [anchorLatLng.lat, anchorLatLng.lng],
            ],
            {
              color: "#6f8194",
              weight: 1.35,
              opacity: 0.72,
              interactive: false,
            },
          );
          layer.addLayer(connector);

          const labelTopLeftLatLng = map.containerPointToLatLng([callout.rect.left, callout.rect.top]);
          const badgeMarker = L.marker([labelTopLeftLatLng.lat, labelTopLeftLatLng.lng], {
            icon: L.divIcon({
              className: "map-node-callout-anchor",
              html: `<span class="map-node-callout-chip" style="width:${Math.round(callout.size.width)}px"><i class="map-node-callout-dot" style="background:${render.style.fill}"></i><em>${escapeHtml(
                render.badge,
              )}</em></span>`,
              iconSize: [Math.round(callout.size.width), Math.round(callout.size.height)],
              iconAnchor: [0, 0],
            }),
            interactive: false,
            keyboard: false,
            zIndexOffset: 1000,
          });
          layer.addLayer(badgeMarker);
        }
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
  }, [lang, nodeMetricMap, nodes, onNodeClick, overlayMode, selectedNodeId, selectedNodeSet, t, viewNonce, volumeDomain]);

  const legend = useMemo(() => buildLegend(overlayMode, t), [overlayMode, t]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-xl">
      <div ref={containerRef} className="h-full w-full" />

      {networkError && (
        <div className="pointer-events-none absolute left-3 top-3 z-[650] rounded-lg border border-rose-200 bg-rose-50/95 px-3 py-2 text-[14px] text-rose-700 shadow-sm">
          {t("맵 데이터를 불러오지 못했습니다:", "Failed to load map data:")} {networkError}
        </div>
      )}

      <div className="pointer-events-auto absolute right-3 top-3 z-[700] rounded-xl border border-slate-200 bg-surface/95 px-3 py-2 text-[14px] shadow-lg backdrop-blur-sm">
        <label className="mb-1 block font-semibold text-slate-700">{t("지도 표시 기준", "Map Overlay")}</label>
        <select
          value={overlayMode}
          onChange={(event) => setOverlayMode(event.target.value as OverlayMode)}
          className="rounded-lg border border-slate-300 bg-surface px-2 py-1 text-[14px] text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
        >
          {OVERLAY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {lang === "ko" ? option.ko : lang === "ja" ? option.ja : option.en}
            </option>
          ))}
        </select>
      </div>

      <div className="pointer-events-none absolute bottom-3 left-3 z-[500] select-none rounded-xl border border-slate-200 bg-surface/92 px-3 py-2.5 text-[13px] text-slate-700 shadow-lg backdrop-blur-sm">
        <p className="mb-1.5 font-semibold text-slate-800">{legend.title}</p>
        <div className="space-y-1">
          {legend.rows.map((row) => (
            <LegendItem key={`${row.sign ?? row.color}-${row.label}`} row={row} />
          ))}
        </div>
        {legend.footnote && <p className="mt-2 text-[12px] text-slate-400">{legend.footnote}</p>}
      </div>
    </div>
  );
}

function resolveIntersectionType(nodeName: string, hasSignal: boolean): IntersectionType {
  if (/(회전|roundabout|rotary|로터리)/i.test(nodeName)) return "roundabout";
  if (hasSignal) return "signal";
  return "unsignal";
}

function resolveVolumeBand(volume: number): number {
  if (volume <= VOLUME_BAND_STOPS[0]) return 0;
  if (volume <= VOLUME_BAND_STOPS[1]) return 1;
  if (volume <= VOLUME_BAND_STOPS[2]) return 2;
  if (volume <= VOLUME_BAND_STOPS[3]) return 3;
  if (volume <= VOLUME_BAND_STOPS[4]) return 4;
  if (volume <= VOLUME_BAND_STOPS[5]) return 5;
  return 6;
}

function resolveLosGrade(delay: number): LosGrade {
  if (delay <= 10) return "A";
  if (delay <= 20) return "B";
  if (delay <= 35) return "C";
  if (delay <= 55) return "D";
  if (delay <= 80) return "E";
  return "F";
}

function resolveNodeRender(
  metric: NodeMetric | null,
  mode: OverlayMode,
  selected: boolean,
  lang: "ko" | "en" | "ja",
  volumeDomain: VolumeDomain | null,
): {
  style: NodeRenderStyle;
  badge: string;
} {
  let base: {
    style: NodeRenderStyle;
    badge: string;
  };
  if (!metric) {
    base = {
      style: { fill: "#b4c0cc", stroke: "#7d8d9f", radius: 9, weight: 1.9, opacity: 0.9 },
      badge: "-",
    };
    if (selected) {
      return {
        ...base,
        style: { ...base.style, radius: base.style.radius + 2, weight: 3, stroke: "#1e293b" },
      };
    }
    return base;
  }

  if (mode === "intersection_type") {
    if (metric.type === "signal") {
      base = {
        style: { ...INTERSECTION_COLOR_MAP.signal, radius: 10, weight: 2, opacity: 1 },
        badge: lang === "ko" ? "신호" : lang === "ja" ? "信号" : "SIG",
      };
      if (selected) {
        return {
          ...base,
          style: { ...base.style, radius: base.style.radius + 2, weight: 3, stroke: "#1e293b" },
        };
      }
      return base;
    }
    if (metric.type === "roundabout") {
      base = {
        style: { ...INTERSECTION_COLOR_MAP.roundabout, radius: 10, weight: 2, opacity: 1 },
        badge: lang === "ko" ? "회전" : lang === "ja" ? "環状" : "RB",
      };
      if (selected) {
        return {
          ...base,
          style: { ...base.style, radius: base.style.radius + 2, weight: 3, stroke: "#1e293b" },
        };
      }
      return base;
    }
    base = {
      style: { ...INTERSECTION_COLOR_MAP.unsignal, radius: 10, weight: 2, opacity: 1 },
      badge: lang === "ko" ? "비신" : lang === "ja" ? "非信号" : "UNS",
    };
    if (selected) {
      return {
        ...base,
        style: { ...base.style, radius: base.style.radius + 2, weight: 3, stroke: "#1e293b" },
      };
    }
    return base;
  }

  if (mode === "los_delay") {
    if (!metric.los) {
      base = {
        style: { fill: "#b4c0cc", stroke: "#7d8d9f", radius: 9, weight: 1.9, opacity: 0.9 },
        badge: "-",
      };
      if (selected) {
        return {
          ...base,
          style: { ...base.style, radius: base.style.radius + 2, weight: 3, stroke: "#1e293b" },
        };
      }
      return base;
    }

    const color = LOS_COLOR_MAP[metric.los];
    base = {
      style: { fill: color.fill, stroke: color.stroke, radius: 11, weight: 2.2, opacity: 1 },
      badge: metric.los,
    };
    if (selected) {
      return {
        ...base,
        style: { ...base.style, radius: base.style.radius + 2, weight: 3, stroke: "#1e293b" },
      };
    }
    return base;
  }

  if (metric.volume == null || metric.volumeBand == null) {
    base = {
      style: { fill: "#b4c0cc", stroke: "#7d8d9f", radius: 9, weight: 1.9, opacity: 0.9 },
      badge: "-",
    };
    if (selected) {
      return {
        ...base,
        style: { ...base.style, radius: base.style.radius + 2, weight: 3, stroke: "#1e293b" },
      };
    }
    return base;
  }
  const band = metric.volumeBand;
  const normalized = normalizeVolume(metric.volume, volumeDomain);
  const eased = Math.sqrt(normalized);
  const radius = 9.5 + eased * 10.5;
  const haloRadius = radius + 5 + eased * 4.5;
  const weight = 2 + eased * 0.85;
  const fillOpacity = 0.56 + eased * 0.34;
  base = {
    style: {
      fill: VOLUME_BAND_COLORS[band],
      stroke: VOLUME_BAND_STROKES[band],
      radius,
      weight,
      opacity: fillOpacity,
      haloRadius,
      haloWeight: 1.25 + eased * 0.8,
      haloOpacity: 0.1 + eased * 0.16,
    },
    badge: formatVolumeBadge(metric.volume),
  };
  if (selected) {
    return {
      ...base,
      style: {
        ...base.style,
        radius: base.style.radius + 2,
        haloRadius: (base.style.haloRadius ?? base.style.radius + 4) + 2,
        weight: 3,
        stroke: "#1e293b",
        opacity: 0.95,
      },
    };
  }
  return base;
}

function resolveCalloutPlacement(args: {
  origin: { x: number; y: number };
  radius: number;
  label: string;
  seed: number;
  placedRects: RectBox[];
  viewport: ViewportSize;
}): CalloutResolved {
  const { origin, radius, label, seed, placedRects, viewport } = args;
  const size = estimateCalloutSize(label);
  const baseCandidates = [
    { dx: 68, dy: -40, elbowDx: 24, elbowDy: -14 },
    { dx: 68, dy: 32, elbowDx: 24, elbowDy: 10 },
    { dx: -68, dy: -40, elbowDx: -24, elbowDy: -14 },
    { dx: -68, dy: 32, elbowDx: -24, elbowDy: 10 },
    { dx: 0, dy: -58, elbowDx: 0, elbowDy: -22 },
    { dx: 0, dy: 50, elbowDx: 0, elbowDy: 20 },
  ] as const;
  const preferredOrders = [
    [0, 1, 2, 3, 4, 5],
    [1, 0, 3, 2, 5, 4],
    [2, 3, 0, 1, 4, 5],
    [3, 2, 1, 0, 5, 4],
    [4, 0, 2, 1, 3, 5],
    [5, 1, 3, 0, 2, 4],
  ] as const;
  const order = preferredOrders[seed % preferredOrders.length];
  const candidates = order.map((idx) => baseCandidates[idx]);

  let fallback: CalloutResolved | null = null;
  for (const candidate of candidates) {
    const centerX = origin.x + candidate.dx;
    const centerY = origin.y + candidate.dy;
    const rect: RectBox = {
      left: centerX - size.width / 2,
      top: centerY - size.height / 2,
      right: centerX + size.width / 2,
      bottom: centerY + size.height / 2,
    };
    const elbow = { x: origin.x + candidate.elbowDx, y: origin.y + candidate.elbowDy };
    const anchor = nearestPointOnRect(rect, elbow);
    const resolved: CalloutResolved = { rect, elbow, anchor, size };

    if (!fallback) {
      fallback = {
        ...resolved,
        rect: clampRectToViewport(rect, viewport),
        anchor: nearestPointOnRect(clampRectToViewport(rect, viewport), elbow),
      };
    }

    if (!isRectInsideViewport(rect, viewport, 6)) continue;
    const hasOverlap = placedRects.some((placed) => rectIntersects(rect, placed, 4));
    if (!hasOverlap) {
      return resolved;
    }
  }

  if (fallback) {
    return fallback;
  }

  const centered: RectBox = {
    left: origin.x + radius + 8,
    top: origin.y - size.height / 2,
    right: origin.x + radius + 8 + size.width,
    bottom: origin.y + size.height / 2,
  };
  const clamped = clampRectToViewport(centered, viewport);
  const elbow = { x: origin.x + radius + 4, y: origin.y - 2 };
  return { rect: clamped, elbow, anchor: nearestPointOnRect(clamped, elbow), size };
}

function estimateCalloutSize(label: string): { width: number; height: number } {
  const charCount = [...label].length;
  const width = Math.max(52, 28 + charCount * 8);
  return { width, height: 24 };
}

function rectIntersects(a: RectBox, b: RectBox, gap: number): boolean {
  return !(
    a.right + gap < b.left ||
    a.left - gap > b.right ||
    a.bottom + gap < b.top ||
    a.top - gap > b.bottom
  );
}

function isRectInsideViewport(rect: RectBox, viewport: ViewportSize, margin: number): boolean {
  return (
    rect.left >= margin &&
    rect.top >= margin &&
    rect.right <= viewport.width - margin &&
    rect.bottom <= viewport.height - margin
  );
}

function clampRectToViewport(rect: RectBox, viewport: ViewportSize): RectBox {
  const margin = 6;
  const width = rect.right - rect.left;
  const height = rect.bottom - rect.top;
  const left = clamp(rect.left, margin, Math.max(margin, viewport.width - margin - width));
  const top = clamp(rect.top, margin, Math.max(margin, viewport.height - margin - height));
  return { left, top, right: left + width, bottom: top + height };
}

function nearestPointOnRect(rect: RectBox, point: { x: number; y: number }): { x: number; y: number } {
  const x = clamp(point.x, rect.left + 2, rect.right - 2);
  const y = clamp(point.y, rect.top + 2, rect.bottom - 2);

  const distances = [
    { side: "top", d: Math.abs(point.y - rect.top), x, y: rect.top },
    { side: "bottom", d: Math.abs(point.y - rect.bottom), x, y: rect.bottom },
    { side: "left", d: Math.abs(point.x - rect.left), x: rect.left, y },
    { side: "right", d: Math.abs(point.x - rect.right), x: rect.right, y },
  ];
  distances.sort((a, b) => a.d - b.d);
  return { x: distances[0].x, y: distances[0].y };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function normalizeVolume(volume: number, domain: VolumeDomain | null): number {
  if (!domain) return 0.5;
  const span = domain.max - domain.min;
  if (span < 1e-6) return 1;
  return clamp((volume - domain.min) / span, 0, 1);
}

function formatVolumeBadge(volume: number): string {
  if (volume < 1000) return `${Math.round(volume)}`;
  const thousands = volume / 1000;
  const text = thousands >= 10 ? thousands.toFixed(0) : thousands.toFixed(1);
  return `${text.replace(/\.0$/, "")}k`;
}

function buildLegend(mode: OverlayMode, t: TFn): { title: string; rows: LegendRow[]; footnote: string } {
  if (mode === "intersection_type") {
    return {
      title: t("교차로 타입", "Intersection Type"),
      rows: [
        { sign: "signal", label: t("신호 교차로", "Signalized") },
        { sign: "roundabout", label: t("회전교차로", "Roundabout") },
        { sign: "unsignal", label: t("비신호교차로", "Unsignalized") },
      ],
      footnote: "",
    };
  }

  if (mode === "los_delay") {
    return {
      title: t("LOS/지체시간 등급", "LOS/Delay Grade"),
      rows: [
        { color: LOS_COLOR_MAP.A.fill, label: "A (<=10s)" },
        { color: LOS_COLOR_MAP.B.fill, label: "B (<=20s)" },
        { color: LOS_COLOR_MAP.C.fill, label: "C (<=35s)" },
        { color: LOS_COLOR_MAP.D.fill, label: "D (<=55s)" },
        { color: LOS_COLOR_MAP.E.fill, label: "E (<=80s)" },
        { color: LOS_COLOR_MAP.F.fill, label: "F (>80s)" },
      ],
      footnote: t("원 안의 A~F는 노드별 평균 지체시간 기반 LOS입니다.", "A-F labels are LOS grades based on node average delay."),
    };
  }

  return {
    title: t("통행량(시간당) 구간", "Hourly Volume Band"),
    rows: [
      { color: VOLUME_BAND_COLORS[0], label: "<= 500" },
      { color: VOLUME_BAND_COLORS[1], label: "500 - 1,000" },
      { color: VOLUME_BAND_COLORS[2], label: "1,000 - 2,000" },
      { color: VOLUME_BAND_COLORS[3], label: "2,000 - 3,000" },
      { color: VOLUME_BAND_COLORS[4], label: "3,000 - 4,000" },
      { color: VOLUME_BAND_COLORS[5], label: "4,000 - 5,000" },
      { color: VOLUME_BAND_COLORS[6], label: "> 5,000" },
    ],
    footnote: "",
  };
}

function buildNodeTooltip(nodeName: string, metric: NodeMetric | null, t: TFn): string {
  if (!metric) {
    return `<div style="font-weight:600">${escapeHtml(nodeName)}</div>`;
  }

  const typeLabel =
    metric.type === "signal"
      ? t("신호 교차로", "Signalized")
      : metric.type === "roundabout"
        ? t("회전교차로", "Roundabout")
        : t("비신호교차로", "Unsignalized");

  const volumeLine =
    metric.volume != null
      ? `<div style="color:#64748b;font-size:10px">${t("통행량", "Volume")} ${Math.round(metric.volume).toLocaleString()} ${t("대/시", "veh/h")}</div>`
      : `<div style="color:#64748b;font-size:10px">${t("통행량 데이터 없음", "No volume data")}</div>`;

  const delayLine =
    metric.delay != null
      ? `<div style="color:#64748b;font-size:10px">${t("평균 지체", "Avg delay")} ${metric.delay.toFixed(1)}s${metric.los ? ` · LOS ${metric.los}` : ""}</div>`
      : `<div style="color:#64748b;font-size:10px">${t("지체 데이터 없음", "No delay data")}</div>`;

  return [
    `<div style="font-weight:600">${escapeHtml(nodeName)}</div>`,
    `<div style="color:#64748b;font-size:10px">${t("교차로 타입", "Type")}: ${escapeHtml(typeLabel)}</div>`,
    volumeLine,
    delayLine,
  ].join("");
}

function buildVolumeMapFromNodeDirections(
  results: Record<string, ScenarioResults>,
  selectedScenarioIds: string[],
): Map<string, number> {
  const aggregates = new Map<string, { sum: number; count: number }>();

  for (const scenarioId of selectedScenarioIds) {
    const rows = results[scenarioId]?.node_directions ?? [];
    for (const row of rows) {
      const nodeId = String(row.node_id ?? "").trim();
      if (!nodeId) continue;

      const incoming = Object.values(row.incoming ?? {}).reduce((sum, value) => sum + Number(value ?? 0), 0);
      const outgoing = Object.values(row.outgoing ?? {}).reduce((sum, value) => sum + Number(value ?? 0), 0);
      const normalizedVolume = (incoming + outgoing) / 2;

      const current = aggregates.get(nodeId) ?? { sum: 0, count: 0 };
      current.sum += normalizedVolume;
      current.count += 1;
      aggregates.set(nodeId, current);
    }
  }

  const volumeMap = new Map<string, number>();
  for (const [nodeId, value] of aggregates.entries()) {
    if (value.count > 0) {
      volumeMap.set(nodeId, value.sum / value.count);
    }
  }
  return volumeMap;
}

function buildDelayMapFromMovements(
  results: Record<string, ScenarioResults>,
  selectedScenarioIds: string[],
): Map<string, number> {
  const aggregates = new Map<string, { weightedDelay: number; weight: number }>();

  for (const scenarioId of selectedScenarioIds) {
    const rows = results[scenarioId]?.movements ?? [];
    for (const row of rows) {
      const objectId = String(row.OBJECT_ID ?? "");
      const match = objectId.match(/^(\d+)-/);
      if (!match) continue;
      const nodeId = match[1];

      const delay = toFiniteNumber(row["VEHDELAY_MEAN"] ?? row["VEHDELAY"]);
      if (delay == null) continue;

      const vehs = toFiniteNumber(row["VEHS_MEAN"] ?? row["VEHS"]);
      const weight = vehs != null && vehs > 0 ? vehs : 1;

      const current = aggregates.get(nodeId) ?? { weightedDelay: 0, weight: 0 };
      current.weightedDelay += delay * weight;
      current.weight += weight;
      aggregates.set(nodeId, current);
    }
  }

  const delayMap = new Map<string, number>();
  for (const [nodeId, value] of aggregates.entries()) {
    if (value.weight > 0) {
      delayMap.set(nodeId, value.weightedDelay / value.weight);
    }
  }
  return delayMap;
}

function toFiniteNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function buildIntersectionSignIcon(type: IntersectionType, selected: boolean, size = selected ? 54 : 46): { html: string; size: number } {
  const svg =
    type === "signal"
      ? buildSignalSignSvg()
      : type === "roundabout"
        ? buildRoundaboutSignSvg()
        : buildYieldSignSvg();
  const shadow = selected
    ? "drop-shadow(0 4px 8px rgba(15,23,42,0.35))"
    : "drop-shadow(0 2px 4px rgba(15,23,42,0.24))";
  const ring = selected ? "box-shadow:0 0 0 3px #1e293b,0 0 0 6px rgba(255,255,255,0.9);" : "";

  return {
    size,
    html: `<div style="width:${size}px;height:${size}px;display:block;${ring}border-radius:999px;filter:${shadow};">${svg}</div>`,
  };
}

function buildSignalSignSvg(): string {
  return `
    <svg viewBox="0 0 64 64" width="100%" height="100%" aria-hidden="true">
      <rect x="20" y="3" width="24" height="58" rx="12" fill="#030507" stroke="#ffffff" stroke-width="2"/>
      <circle cx="32" cy="16" r="9.2" fill="#f3161d"/>
      <circle cx="32" cy="32" r="9.2" fill="#ffd719"/>
      <circle cx="32" cy="48" r="9.2" fill="#4aa72f"/>
    </svg>
  `;
}

function buildRoundaboutSignSvg(): string {
  return `
    <svg viewBox="0 0 64 64" width="100%" height="100%" aria-hidden="true">
      <circle cx="32" cy="32" r="30" fill="#0755bd" stroke="#ffffff" stroke-width="3"/>
      <circle cx="32" cy="32" r="27" fill="none" stroke="#ffffff" stroke-width="1.4" opacity="0.9"/>
      <path d="M21 39a15 15 0 0 1 2-18" fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="butt"/>
      <path d="M23 21l1 13-12-5z" fill="#ffffff"/>
      <path d="M29 18a15 15 0 0 1 18 8" fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="butt"/>
      <path d="M47 26l-12 4 5-12z" fill="#ffffff"/>
      <path d="M47 34a15 15 0 0 1-16 13" fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="butt"/>
      <path d="M31 47l9-9 4 12z" fill="#ffffff"/>
    </svg>
  `;
}

function buildYieldSignSvg(): string {
  return `
    <svg viewBox="0 0 64 64" width="100%" height="100%" aria-hidden="true">
      <path d="M32 60 L4 8 Q3 5 6 4 H58 Q61 5 60 8 Z" fill="#ffffff" stroke="#e31b23" stroke-width="2" stroke-linejoin="round"/>
      <path d="M32 55 L8 10 H56 Z" fill="#e31b23"/>
      <path d="M32 43 L18 18 H46 Z" fill="#ffffff"/>
      <text x="32" y="27" text-anchor="middle" font-family="Arial, sans-serif" font-size="10" font-weight="800" fill="#202020">양보</text>
      <text x="32" y="37" text-anchor="middle" font-family="Arial, sans-serif" font-size="7.5" font-weight="700" fill="#202020">YIELD</text>
    </svg>
  `;
}

function LegendItem({ row }: { row: LegendRow }) {
  if (row.sign) {
    return (
      <div className="flex items-center gap-2">
        <span
          className="inline-flex h-7 w-7 items-center justify-center"
          dangerouslySetInnerHTML={{ __html: buildIntersectionSignIcon(row.sign, false, 26).html }}
        />
        <span>{row.label}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span
        className="inline-block h-3.5 w-7 rounded-[5px] border border-slate-500/70 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.3)]"
        style={{ backgroundColor: row.color }}
      />
      <span>{row.label}</span>
    </div>
  );
}

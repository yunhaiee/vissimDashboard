"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { CircleMarker, LayerGroup, LeafletMouseEvent, Map as LeafletMap, Polyline } from "leaflet";

import { apiFetch } from "@/lib/api";
import { useLanguage } from "@/components/LanguageProvider";

export interface LinkFeature {
  id: string;
  name: string;
  lanes: number;
  length: number;
}

export type NodeDirection = "north" | "east" | "south" | "west";

export interface NodeConnection extends LinkFeature {
  direction: NodeDirection;
  role: "incoming" | "outgoing";
}

export interface Incident {
  linkId: string;
  type: "lane_closure" | "full_closure" | "speed_limit";
  closedLanes: number;
  totalLanes: number;
  speedLimit: number;
  duration: number;
}

export type QueuedLinkMode = "volume" | "speed" | "mixed";

export interface NodeFeature {
  id: string;
  name: string;
  lat: number;
  lon: number;
  connectedLinks: NodeConnection[];
}

interface Props {
  onLinkClick: (link: LinkFeature) => void;
  onNodeClick: (node: NodeFeature) => void;
  incidents: Incident[];
  metricData?: Record<string, number>;
  metric: "none" | "delay" | "speed";
  nodeMetricData?: Record<string, number>;
  nodeMetricMode?: "none" | "delta";
  nodeMetricFocusedScenario?: string;
  nodeMetricBaseline?: string;
  scenarioId?: string;
  selectedLinkId?: string | null;
  queuedLinkModes?: Record<string, QueuedLinkMode>;
}

type GeoJsonFeatureCollection = {
  type: "FeatureCollection";
  features: GeoJsonFeature[];
};

type GeoJsonFeature = {
  type: "Feature";
  id?: string;
  geometry: {
    type: "LineString" | "Point";
    coordinates: number[][] | number[];
  };
  properties: {
    id: string;
    name?: string;
    lanes?: number;
    length?: number;
    type: "link" | "node";
  };
};

type DisplayLink = LinkFeature & {
  coordinates: [number, number][];
  originalCoordinates: [number, number][];
  sourceNodeId: string;
  targetNodeId: string;
};

type GraphData = {
  links: DisplayLink[];
  nodes: NodeFeature[];
  bounds: [number, number][];
};

const DEFAULT_LINK_COLOR = "#506982";
const INCIDENT_COLOR = "#ef4444";
const NODE_FILL = "#5f718b";
const NODE_STROKE = "#d8e1ef";
const NODE_SELECTED = "#f8b45a";
const LINK_SELECTED = "#7dd3fc";
const GRAPH_TILE = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const ENDPOINT_CLUSTER_DISTANCE_METERS = 35;
export default function MapViewer({
  onLinkClick,
  onNodeClick,
  incidents,
  metricData,
  metric,
  nodeMetricData,
  nodeMetricMode = "none",
  nodeMetricFocusedScenario,
  nodeMetricBaseline,
  scenarioId,
  selectedLinkId,
  queuedLinkModes = {},
}: Props) {
  const { t } = useLanguage();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const linksLayerRef = useRef<LayerGroup | null>(null);
  const nodesLayerRef = useRef<LayerGroup | null>(null);
  const viewportInitializedRef = useRef(false);
  const scenarioRef = useRef<string | undefined>(undefined);
  const [ready, setReady] = useState(false);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredLinkId, setHoveredLinkId] = useState<string | null>(null);

  const incidentSet = useMemo(() => new Set(incidents.map((incident) => incident.linkId)), [incidents]);
  const selectedNode = useMemo(
    () => graphData?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [graphData, selectedNodeId],
  );
  const selectedLink = useMemo(
    () => graphData?.links.find((link) => link.id === selectedLinkId) ?? null,
    [graphData, selectedLinkId],
  );
  const metricLegend = useMemo(() => legendForMetric(metric), [metric]);
  const hasMetricData = useMemo(() => Object.keys(metricData ?? {}).length > 0, [metricData]);
  const hasNodeMetricData = useMemo(() => Object.keys(nodeMetricData ?? {}).length > 0, [nodeMetricData]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }

    let cancelled = false;

    (async () => {
      const L = (await import("leaflet")).default;
      const container = containerRef.current!;
      if ((container as { _leaflet_id?: number | null })._leaflet_id) {
        (container as { _leaflet_id?: number | null })._leaflet_id = null;
      }

      const map = L.map(container, {
        zoomControl: false,
        preferCanvas: false,
        dragging: true,
        inertia: true,
        boxZoom: false,
        doubleClickZoom: true,
        scrollWheelZoom: true,
      });
      mapRef.current = map;
      map.dragging.enable();
      map.setView([-32.0696, 115.8413], 16);
      map.getContainer().style.cursor = "grab";
      map.on("dragstart", () => {
        map.getContainer().style.cursor = "grabbing";
      });
      map.on("dragend", () => {
        map.getContainer().style.cursor = "grab";
      });
      map.on("click", () => {
        setSelectedNodeId(null);
        setHoveredLinkId(null);
      });

      L.tileLayer(GRAPH_TILE, {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; CARTO',
        maxZoom: 20,
      }).addTo(map);
      L.control.zoom({ position: "bottomright" }).addTo(map);

      if (!cancelled) {
        setReady(true);
      }
    })();

    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
      if (containerRef.current) {
        (containerRef.current as { _leaflet_id?: number | null })._leaflet_id = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!ready || !containerRef.current || !mapRef.current) {
      return;
    }

    const map = mapRef.current;
    let disposed = false;
    let frameId: number | null = null;
    const invalidate = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        if (disposed || mapRef.current !== map || !containerRef.current?.isConnected) {
          return;
        }
        try {
          map.invalidateSize(false);
        } catch {
          // Leaflet can throw while React is replacing the map container.
        }
      });
    };
    invalidate();

    const observer = new ResizeObserver(invalidate);
    observer.observe(containerRef.current);
    window.addEventListener("resize", invalidate);

    return () => {
      disposed = true;
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      observer.disconnect();
      window.removeEventListener("resize", invalidate);
    };
  }, [ready]);

  useEffect(() => {
    if (!ready || !mapRef.current) {
      return;
    }

    let cancelled = false;

    (async () => {
      const query = new URLSearchParams();
      if (scenarioId) {
        query.set("scenario_id", scenarioId);
      }
      const params = query.size ? `?${query.toString()}` : "";

      const [centerRes, networkRes] = await Promise.all([
        apiFetch(`/api/geojson/center${params}`),
        apiFetch(`/api/geojson/network${params}`),
      ]);

      if (!centerRes.ok || !networkRes.ok || cancelled || !mapRef.current) {
        return;
      }

      const center = await centerRes.json();
      const geojson = (await networkRes.json()) as GeoJsonFeatureCollection;
      const graph = buildDisplayGraph(geojson);
      if (cancelled) {
        return;
      }

      setGraphData(graph);
      if (scenarioRef.current !== scenarioId) {
        const L = (await import("leaflet")).default;
        setSelectedNodeId(null);
        setHoveredLinkId(null);
        mapRef.current.setView([center.lat, center.lon], center.zoom);
        const bounds = L.latLngBounds(graph.bounds);
        if (bounds.isValid()) {
          mapRef.current.fitBounds(bounds, { padding: [48, 48] });
        }
        viewportInitializedRef.current = true;
        scenarioRef.current = scenarioId;
      } else if (!viewportInitializedRef.current) {
        viewportInitializedRef.current = true;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [ready, scenarioId]);

  useEffect(() => {
    if (!graphData || !mapRef.current) {
      return;
    }

    let cancelled = false;

    (async () => {
      const L = (await import("leaflet")).default;
      if (!cancelled && mapRef.current) {
        renderGraphLayers(L, mapRef.current, graphData, {
          linksLayerRef,
          nodesLayerRef,
          incidentSet,
          metricData: metricData ?? {},
          metric,
          queuedLinkModes,
          nodeMetricData: nodeMetricData ?? {},
          nodeMetricMode,
          selectedNodeId,
          selectedLinkId: selectedLinkId ?? null,
          hoveredLinkId,
          setHoveredLinkId,
          onLinkClick,
          onNodeClick: (node) => {
            setSelectedNodeId(node.id);
            onNodeClick(node);
          },
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [graphData, hoveredLinkId, incidentSet, metricData, metric, nodeMetricData, nodeMetricMode, onLinkClick, onNodeClick, queuedLinkModes, selectedLinkId, selectedNodeId]);

  return (
    <div className="simulation-map-viewer relative w-full h-full rounded-xl overflow-hidden">
      <div ref={containerRef} className="w-full h-full" />
      <div className="pointer-events-none select-none absolute left-4 top-32 z-[500] rounded-xl border border-ink/10 bg-slate-950/84 px-4 py-3 text-[14px] text-slate-200 shadow-2xl backdrop-blur">
        <div className="font-semibold text-slate-100">{t("지도 컨텍스트", "Map Context")}</div>
        <div className="mt-2 space-y-2 text-[13px] text-slate-300">
          <div className="flex items-center justify-between gap-4">
            <span className="text-slate-400">{t("시나리오", "Scenario")}</span>
            <span className="font-medium text-slate-100">{scenarioId ?? t("없음", "N/A")}</span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <span className="text-slate-400">{t("지표 모드", "Metric Mode")}</span>
            <span className="font-medium text-slate-100">{metricLegend.title}</span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <span className="text-slate-400">{t("노드 비교", "Node Compare")}</span>
            <span className="font-medium text-slate-100">
              {nodeMetricMode === "delta" ? t("비교", "Compare") : t("끔", "Off")}
            </span>
          </div>
          <LegendRow color="#22c55e" label={t("대기 교통량 입력", "Queued vehicle input")} />
          <LegendRow color="#f97316" label={t("복합 변경", "Mixed changes")} />
          {nodeMetricMode === "delta" && (
            <div className="flex items-center justify-between gap-4">
              <span className="text-slate-400">{t("기준값", "Baseline")}</span>
              <span className="font-medium text-slate-100">{nodeMetricBaseline ?? t("평균", "Average")}</span>
            </div>
          )}
          <div className="flex items-center justify-between gap-4">
            <span className="text-slate-400">{t("대기 변경", "Queued Changes")}</span>
            <span className="font-medium text-slate-100">{incidents.length}</span>
          </div>
        </div>
      </div>

      <div className="pointer-events-none select-none absolute right-4 top-4 z-[500] max-w-[260px] rounded-xl border border-ink/10 bg-slate-950/84 px-4 py-3 text-[14px] text-slate-200 shadow-2xl backdrop-blur">
        <div className="font-semibold text-slate-100">{t("선택 정보", "Selection")}</div>
        <div className="mt-2 space-y-2 text-[13px]">
          <div className="rounded-lg border border-ink/10 bg-slate-900/80 px-3 py-2">
            <div className="text-[12px] uppercase tracking-wide text-slate-400">{t("노드", "Node")}</div>
            <div className="mt-1 text-slate-100">
              {selectedNode
                ? `${selectedNode.name} (${selectedNode.connectedLinks.length} ${t("개 링크", "links")})`
                : t("노드를 클릭하세요", "Click a node")}
            </div>
          </div>
          <div className="rounded-lg border border-ink/10 bg-slate-900/80 px-3 py-2">
            <div className="text-[12px] uppercase tracking-wide text-slate-400">{t("링크", "Link")}</div>
            <div className="mt-1 text-slate-100">
              {selectedLink
                ? `Link ${selectedLink.id} | ${selectedLink.lanes} ${t("차로", "lane(s)")}`
                : t("링크를 클릭하세요", "Click a link")}
            </div>
          </div>
        </div>
      </div>

      <div className="pointer-events-none select-none absolute left-4 bottom-4 z-[500] rounded-xl border border-ink/10 bg-slate-950/84 px-4 py-3 text-[14px] text-slate-200 shadow-2xl backdrop-blur">
        <div className="font-semibold text-slate-100">{t("네트워크 상태", "Network Status")}</div>
        <div className="mt-2 space-y-1.5">
          {metricLegend.rows.map((item) => (
            <LegendRow key={item.label} color={item.color} label={item.label} />
          ))}
          <LegendRow color={INCIDENT_COLOR} label={t("대기 사고/이벤트", "Queued incident")} />
          {nodeMetricMode === "delta" && (
            <>
              <div className="pt-1 text-[12px] uppercase tracking-wide text-slate-400">
                {t("노드 차이 (기준 - 베이스라인)", "Node delta (focused - baseline)")}
              </div>
              <LegendRow color="#dc2626" label={t("증가 (+)", "Increase (+)")} />
              <LegendRow color="#3b82f6" label={t("감소 (-)", "Decrease (-)")} />
              <LegendRow color="#94a3b8" label={t("기준과 유사", "Near baseline")} />
            </>
          )}
        </div>
        {!hasMetricData && metric !== "none" && (
          <div className="mt-2 text-[12px] text-slate-400">
            {t("시뮬레이션 결과 로드 후 지표 값이 표시됩니다.", "Metric values appear after simulation results are loaded.")}
          </div>
        )}
        {!hasNodeMetricData && nodeMetricMode === "delta" && (
          <div className="mt-2 text-[12px] text-slate-400">
            {t("기준 시나리오 결과 로드 후 노드 변화량이 표시됩니다.", "Node delta appears after runtime results are loaded for the focused scenario.")}
          </div>
        )}
      </div>

      <div className="pointer-events-none select-none absolute right-4 bottom-4 z-[500] rounded-xl border border-ink/10 bg-slate-950/84 px-3 py-2 text-[13px] text-slate-300 shadow-2xl backdrop-blur">
        {nodeMetricMode === "delta" && nodeMetricFocusedScenario
          ? t(`노드 변화 오버레이: ${nodeMetricFocusedScenario}`, `Node delta overlay: ${nodeMetricFocusedScenario}`, `ノード変化オーバーレイ: ${nodeMetricFocusedScenario}`)
          : t("지도에서 노드나 링크를 직접 클릭하면 더 빠르게 큐를 구성할 수 있습니다.", "Click node or link directly on map to queue changes faster.")}
      </div>
    </div>
  );
}

function renderGraphLayers(
  L: any,
  map: LeafletMap,
  graph: GraphData,
  options: {
    linksLayerRef: React.MutableRefObject<LayerGroup | null>;
    nodesLayerRef: React.MutableRefObject<LayerGroup | null>;
    incidentSet: Set<string>;
    metricData: Record<string, number>;
    metric: "none" | "delay" | "speed";
    queuedLinkModes: Record<string, QueuedLinkMode>;
    nodeMetricData: Record<string, number>;
    nodeMetricMode: "none" | "delta";
    selectedNodeId: string | null;
    selectedLinkId: string | null;
    hoveredLinkId: string | null;
    setHoveredLinkId: React.Dispatch<React.SetStateAction<string | null>>;
    onLinkClick: (link: LinkFeature) => void;
    onNodeClick: (node: NodeFeature) => void;
  },
) {
  options.linksLayerRef.current?.remove();
  options.nodesLayerRef.current?.remove();

  const linkLayer = L.layerGroup();
  const nodeLayer = L.layerGroup();
  const nodeMetricPeakAbs =
    options.nodeMetricMode === "delta"
      ? Math.max(
          ...Object.values(options.nodeMetricData).map((value) => Math.abs(Number(value ?? 0))),
          0,
        )
      : 0;

  for (const link of graph.links) {
    const queuedMode = options.queuedLinkModes[link.id];
    const selected = link.id === options.selectedLinkId;
    const hovered = link.id === options.hoveredLinkId;
    const emphasized = Boolean(
      selected ||
      hovered ||
      queuedMode ||
      options.incidentSet.has(link.id) ||
      (options.metric !== "none" && options.metricData[link.id] != null),
    );
    const baseColor = graphLinkColor(
      link.id,
      options.metricData,
      options.metric,
      options.incidentSet,
      options.queuedLinkModes,
    );
    const strokeColor = selected ? LINK_SELECTED : baseColor;
    const casing = L.polyline(link.coordinates, {
      color: emphasized ? (selected ? "#0f172a" : "#334155") : "#ffffff",
      weight: selected ? 9 : hovered ? 8 : emphasized ? 7 : 5.5,
      opacity: selected ? 0.75 : emphasized ? 0.42 : 0.8,
      lineCap: "round",
      lineJoin: "round",
      interactive: false,
    }) as Polyline;
    const line = L.polyline(link.coordinates, {
      color: strokeColor,
      weight: selected ? 5.8 : hovered ? 5 : emphasized ? 4.4 : 3.1,
      opacity: selected ? 1 : emphasized ? 0.95 : 0.78,
      lineCap: "round",
      lineJoin: "round",
      interactive: false,
    }) as Polyline;
    const hitArea = L.polyline(link.coordinates, {
      color: "#000000",
      weight: 18,
      opacity: 0.001,
      lineCap: "round",
      lineJoin: "round",
      interactive: true,
    }) as Polyline;

    const metricValue = options.metricData[link.id];
    const metricText =
      options.metric === "delay"
        ? metricValue != null
          ? `${metricValue.toFixed(1)} s delay`
          : "No delay data"
        : options.metric === "speed"
          ? metricValue != null
            ? `${metricValue.toFixed(1)} km/h`
            : "No speed data"
          : "No runtime metric";
    const queueText = queuedMode ? `<br/>Queued: ${queuedMode}` : "";

    hitArea.on("mouseover", () => options.setHoveredLinkId(link.id));
    hitArea.on("mouseout", () => {
      options.setHoveredLinkId((current) => (current === link.id ? null : current));
    });
    hitArea.on("click", (event: LeafletMouseEvent) => {
      L.DomEvent.stopPropagation(event.originalEvent);
      options.onLinkClick(link);
    });
    hitArea.bindTooltip(
      `<div><strong>Link ${link.id}</strong><br/>${link.lanes} lane(s) | ${link.length.toFixed(1)} m<br/>${metricText}${queueText}</div>`,
      {
        direction: "top",
        sticky: true,
        className: "map-link-tooltip",
      },
    );

    linkLayer.addLayer(casing);
    linkLayer.addLayer(line);
    linkLayer.addLayer(hitArea);
  }

  for (const node of graph.nodes) {
    const selected = node.id === options.selectedNodeId;
    const nodeMetricValue = options.nodeMetricData[node.id];
    const nodeVisual = resolveNodeVisual({
      selected,
      mode: options.nodeMetricMode,
      delta: nodeMetricValue,
      peakAbs: nodeMetricPeakAbs,
    });
    const metricSuffix =
      options.nodeMetricMode === "delta" && nodeMetricValue != null
        ? `<br/>Delta ${formatSignedNodeValue(Number(nodeMetricValue))} veh`
        : "";
    const marker = L.circleMarker([node.lat, node.lon], {
      radius: nodeVisual.radius,
      fillColor: nodeVisual.fillColor,
      color: nodeVisual.color,
      weight: nodeVisual.weight,
      fillOpacity: 1,
      opacity: 1,
      interactive: true,
    }) as CircleMarker;
    marker.on("mouseover", () => {
      if (!selected) {
        marker.setStyle({
          radius: nodeVisual.radius + 1,
          color: "#f9e4c2",
          weight: Math.max(2.5, nodeVisual.weight),
        });
      }
    });
    marker.on("mouseout", () => {
      if (!selected) {
        marker.setStyle({
          radius: nodeVisual.radius,
          fillColor: nodeVisual.fillColor,
          color: nodeVisual.color,
          weight: nodeVisual.weight,
        });
      }
    });
    marker.on("click", (event: LeafletMouseEvent) => {
      L.DomEvent.stopPropagation(event.originalEvent);
      options.onNodeClick(node);
    });
    marker.bindTooltip(`<div><strong>${node.name}</strong><br/>${node.connectedLinks.length} connected link(s)${metricSuffix}</div>`, {
      direction: "top",
      offset: [0, -6],
      className: "map-node-tooltip",
    });
    nodeLayer.addLayer(marker);
  }

  linkLayer.addTo(map);
  nodeLayer.addTo(map);
  options.linksLayerRef.current = linkLayer;
  options.nodesLayerRef.current = nodeLayer;
}

function buildDisplayGraph(geojson: GeoJsonFeatureCollection): GraphData {
  const rawLinks = geojson.features
    .filter((feature) => feature.properties.type === "link" && feature.geometry.type === "LineString")
    .map((feature) => ({
      id: feature.properties.id,
      name: feature.properties.name ?? "",
      lanes: feature.properties.lanes ?? 1,
      length: feature.properties.length ?? 0,
        points: feature.geometry.coordinates as number[][],
    }));

  const rawNodes = geojson.features
    .filter((feature) => feature.properties.type === "node" && feature.geometry.type === "Point")
    .map((feature) => {
      const [lon, lat] = feature.geometry.coordinates as number[];
      const nodeId = String(feature.properties.id ?? feature.id ?? "").trim();
      const nodeName = String(feature.properties.name ?? "").trim();
      return {
        id: nodeId,
        label: nodeName || `Node ${nodeId || "?"}`,
        lat: Number(lat),
        lon: Number(lon),
      };
    })
    .filter((node) => node.id.length > 0);

  const endpointSeeds = rawLinks.flatMap((link) => [
    { linkId: link.id, coord: link.points[0] },
    { linkId: link.id, coord: link.points[link.points.length - 1] },
  ]);
  const fallbackClusters = clusterEndpoints(endpointSeeds);
  const nodeAnchors =
    rawNodes.length > 0
      ? rawNodes
      : fallbackClusters.map((cluster) => ({
          id: cluster.id,
          label: cluster.label,
          lat: cluster.lat,
          lon: cluster.lon,
        }));
  const displayLinks: DisplayLink[] = rawLinks
    .map((link) => {
      const source = findNearestCluster(nodeAnchors, link.points[0]);
      const target = findNearestCluster(nodeAnchors, link.points[link.points.length - 1]);
      if (!source || !target) {
        return null;
      }

      const originalCoordinates = link.points.map(([lon, lat]) => [lat, lon] as [number, number]);
      const coordinates = originalCoordinates;

      return {
        id: link.id,
        name: link.name,
        lanes: link.lanes,
        length: link.length,
        coordinates,
        originalCoordinates,
        sourceNodeId: source.id,
        targetNodeId: target.id,
      };
    })
    .filter((link): link is DisplayLink => link != null);

  const displayNodes: NodeFeature[] = nodeAnchors.map((anchor) => {
    const connectedLinks = displayLinks
      .filter((link) => link.sourceNodeId === anchor.id || link.targetNodeId === anchor.id)
      .map((link) => {
        const otherNodeId = link.sourceNodeId === anchor.id ? link.targetNodeId : link.sourceNodeId;
        const otherNode = nodeAnchors.find((item) => item.id === otherNodeId);
        const direction = otherNode
          ? classifyDirection(anchor.lon, anchor.lat, otherNode.lon, otherNode.lat)
          : "east";
        const role: "incoming" | "outgoing" = link.targetNodeId === anchor.id ? "incoming" : "outgoing";
        return {
          id: link.id,
          name: link.name,
          lanes: link.lanes,
          length: link.length,
          direction,
          role,
        };
      })
      .sort((left, right) => directionSort(left.direction) - directionSort(right.direction));

    return {
      id: anchor.id,
      name: anchor.label,
      lat: anchor.lat,
      lon: anchor.lon,
      connectedLinks,
    };
  });

  const bounds = [
    ...displayLinks.flatMap((link) => link.coordinates),
    ...displayNodes.map((node) => [node.lat, node.lon] as [number, number]),
  ];

  return {
    links: displayLinks,
    nodes: displayNodes,
    bounds,
  };
}

function classifyDirection(
  originLon: number,
  originLat: number,
  targetLon: number,
  targetLat: number,
): NodeDirection {
  const dx = targetLon - originLon;
  const dy = targetLat - originLat;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? "east" : "west";
  }
  return dy >= 0 ? "north" : "south";
}

function directionSort(direction: NodeDirection) {
  switch (direction) {
    case "north":
      return 0;
    case "east":
      return 1;
    case "south":
      return 2;
    case "west":
      return 3;
  }
}

function clusterEndpoints(seeds: { linkId: string; coord: number[] }[]) {
  const clusters: {
    id: string;
    label: string;
    lat: number;
    lon: number;
    members: [number, number][];
    linkIds: string[];
  }[] = [];

  for (const seed of seeds) {
    const [lon, lat] = seed.coord;
    const existing = clusters.find((cluster) => distanceMeters([cluster.lon, cluster.lat], [lon, lat]) <= ENDPOINT_CLUSTER_DISTANCE_METERS);
    if (existing) {
      existing.members.push([lon, lat]);
      existing.linkIds.push(seed.linkId);
      const { lon: avgLon, lat: avgLat } = averageCoordinate(existing.members);
      existing.lon = avgLon;
      existing.lat = avgLat;
    } else {
      clusters.push({
        id: `N${clusters.length + 1}`,
        label: `Node ${clusters.length + 1}`,
        lon,
        lat,
        members: [[lon, lat]],
        linkIds: [seed.linkId],
      });
    }
  }

  return clusters;
}

function averageCoordinate(points: [number, number][]) {
  const sums = points.reduce(
    (acc, [lon, lat]) => ({ lon: acc.lon + lon, lat: acc.lat + lat }),
    { lon: 0, lat: 0 },
  );
  return {
    lon: sums.lon / points.length,
    lat: sums.lat / points.length,
  };
}

function findNearestCluster(
  clusters: { id: string; lat: number; lon: number }[],
  coord: number[],
) {
  const [lon, lat] = coord;
  let best: { id: string; lat: number; lon: number } | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const cluster of clusters) {
    const distance = distanceMeters([cluster.lon, cluster.lat], [lon, lat]);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = cluster;
    }
  }
  return best;
}

function distanceMeters(a: [number, number], b: [number, number]) {
  const [lon1, lat1] = a;
  const [lon2, lat2] = b;
  const rad = Math.PI / 180;
  const x = (lon2 - lon1) * rad * Math.cos(((lat1 + lat2) / 2) * rad);
  const y = (lat2 - lat1) * rad;
  return Math.sqrt(x * x + y * y) * 6371000;
}

function resolveNodeVisual({
  selected,
  mode,
  delta,
  peakAbs,
}: {
  selected: boolean;
  mode: "none" | "delta";
  delta: number | undefined;
  peakAbs: number;
}) {
  if (selected) {
    return {
      radius: 9,
      fillColor: NODE_SELECTED,
      color: "#fff1d7",
      weight: 3,
    };
  }

  if (mode !== "delta" || delta == null) {
    return {
      radius: 7,
      fillColor: NODE_FILL,
      color: NODE_STROKE,
      weight: 2,
    };
  }

  const numericDelta = Number(delta);
  const ratio = peakAbs > 0 ? Math.min(Math.abs(numericDelta) / peakAbs, 1) : 0;
  const radius = 7 + ratio * 4;

  if (Math.abs(numericDelta) < 0.0001) {
    return {
      radius,
      fillColor: "#94a3b8",
      color: "#cbd5e1",
      weight: 2.1,
    };
  }

  if (numericDelta > 0) {
    return {
      radius,
      fillColor: ratio >= 0.66 ? "#dc2626" : ratio >= 0.33 ? "#f97316" : "#fb7185",
      color: ratio >= 0.66 ? "#fecaca" : "#fed7aa",
      weight: 2.25,
    };
  }

  return {
    radius,
    fillColor: ratio >= 0.66 ? "#1d4ed8" : ratio >= 0.33 ? "#3b82f6" : "#38bdf8",
    color: ratio >= 0.66 ? "#bfdbfe" : "#bae6fd",
    weight: 2.25,
  };
}

function formatSignedNodeValue(value: number) {
  const rounded = Math.round(value);
  if (rounded > 0) return `+${rounded.toLocaleString()}`;
  if (rounded < 0) return rounded.toLocaleString();
  return "0";
}

function graphLinkColor(
  linkId: string,
  metricData: Record<string, number>,
  metric: "none" | "delay" | "speed",
  incidentSet: Set<string>,
  queuedLinkModes: Record<string, QueuedLinkMode>,
) {
  const queuedMode = queuedLinkModes[linkId];
  if (incidentSet.has(linkId) && queuedMode === "volume") {
    return "#f97316";
  }

  if (incidentSet.has(linkId) || queuedMode === "speed" || queuedMode === "mixed") {
    return INCIDENT_COLOR;
  }

  if (queuedMode === "volume") {
    return "#22c55e";
  }

  const value = metricData[linkId];
  if (value == null || metric === "none") {
    return DEFAULT_LINK_COLOR;
  }

  if (metric === "speed") {
    if (value >= 50) return "#3dd77a";
    if (value >= 35) return "#f5bf42";
    return "#f87171";
  }

  if (value <= 20) return "#3dd77a";
  if (value <= 60) return "#f5bf42";
  return "#f87171";
}

function legendForMetric(metric: "none" | "delay" | "speed") {
  if (metric === "speed") {
    return {
      title: "Speed",
      rows: [
        { color: "#3dd77a", label: "Fast (>= 50 km/h)" },
        { color: "#f5bf42", label: "Moderate (35-49 km/h)" },
        { color: "#f87171", label: "Slow (< 35 km/h)" },
      ],
    };
  }

  if (metric === "delay") {
    return {
      title: "Delay",
      rows: [
        { color: "#3dd77a", label: "Low (<= 20 s)" },
        { color: "#f5bf42", label: "Medium (21-60 s)" },
        { color: "#f87171", label: "High (> 60 s)" },
      ],
    };
  }

  return {
    title: "Status",
    rows: [{ color: DEFAULT_LINK_COLOR, label: "Base VISSIM links" }],
  };
}

function LegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block h-2.5 w-3 rounded-sm" style={{ backgroundColor: color }} />
      <span>{label}</span>
    </div>
  );
}

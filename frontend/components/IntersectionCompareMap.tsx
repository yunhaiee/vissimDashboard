"use client";

import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { CircleMarker, LeafletMouseEvent, Map as LeafletMap } from "leaflet";

import { apiFetch } from "@/lib/api";

type NodeFeature = { id: string; name: string; lat: number; lon: number };
type LinkFeature = { id: string; coords: [number, number][] };

interface Props {
  selectedNodeIds: string[];
  onToggleNode: (nodeId: string, nodeName: string) => void;
  datasetRevision?: number | string;
}

const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const GEO_PARAMS = "scale_x=1.178200&scale_y=1.178200&offset_x=14427.6419&offset_y=-4221.3566&rotation_deg=-0.45759";

export default function IntersectionCompareMap({ selectedNodeIds, onToggleNode, datasetRevision = 0 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const linksLayerRef = useRef<{ remove: () => void; addTo: (map: LeafletMap) => void } | null>(null);
  const nodesLayerRef = useRef<{ remove: () => void; addTo: (map: LeafletMap) => void } | null>(null);

  const [ready, setReady] = useState(false);
  const [nodes, setNodes] = useState<NodeFeature[]>([]);
  const [links, setLinks] = useState<LinkFeature[]>([]);
  const [networkError, setNetworkError] = useState<string | null>(null);

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

      L.tileLayer(TILE_URL, {
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

      const selectedSet = new Set(selectedNodeIds);
      const layer = L.layerGroup();
      localLayer = layer;

      for (const node of nodes) {
        if (cancelled || mapRef.current !== map) {
          layer.remove();
          return;
        }
        const selected = selectedSet.has(node.id);
        const marker = L.circleMarker([node.lat, node.lon], {
          radius: selected ? 11.5 : 9,
          fillColor: selected ? "#2f7bc8" : "#8fa3b8",
          color: selected ? "#1e3e62" : "#53667a",
          weight: selected ? 2.8 : 2,
          fillOpacity: selected ? 0.95 : 0.82,
          interactive: true,
        }) as CircleMarker;

        marker.on("mouseover", () => {
          if (!selected) marker.setStyle({ radius: 10.5, fillOpacity: 0.92 });
        });
        marker.on("mouseout", () => {
          if (!selected) marker.setStyle({ radius: 9, fillOpacity: 0.82 });
        });
        marker.on("click", (e: LeafletMouseEvent) => {
          L.DomEvent.stopPropagation(e.originalEvent);
          onToggleNode(node.id, node.name);
        });

        marker.bindTooltip(`<div style="font-weight:600">${escapeHtml(node.name)}</div>`, {
          direction: "top",
          offset: [0, -8],
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
  }, [nodes, onToggleNode, selectedNodeIds]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-xl">
      <div ref={containerRef} className="h-full w-full" />
      {networkError && (
        <div className="pointer-events-none absolute left-3 top-3 z-[650] rounded-lg border border-rose-200 bg-rose-50/95 px-3 py-2 text-xs text-rose-700 shadow-sm">
          Failed to load map data: {networkError}
        </div>
      )}
    </div>
  );
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

"use client";

// Roadmap v2 §5.B — the 0.4 mockup's lasso, stage (a), on a real basemap.
//
// WHAT THIS IS AND IS NOT, because the distinction is the product's:
//   * it IS a raw WGS84 drawing surface. Every coordinate it produces is a
//     latitude/longitude the user clicked on a tile whose own projection Leaflet
//     owns, and it is sent to Overpass unchanged. There is no fit, no Helmert, no
//     transform of any kind between the click and the query;
//   * it makes NO georeference claim about generated output. That refusal lives in
//     `services/genesis/project.py` ("NOT A GEOREFERENCE") and is about placing an
//     emitted .inpx on the earth, which is a different act from asking OSM for a
//     region. Four components in this app already draw CARTO tiles for the same
//     reason (`DashboardMap`, `BaseValidationMap`, `IntersectionCompareMap`,
//     `MapViewer`).
//
// Leaflet is loaded with `await import("leaflet")` INSIDE the mount effect, the
// shape the four components above use: leaflet touches `window` at module scope,
// so a static import would break the server render even inside a client
// component. The page also mounts this through `next/dynamic` with `ssr: false`
// (documented in `01-app/02-guides/lazy-loading.md` for Next 16) — belt and
// braces, and there is nothing to prerender for a map anyway.
//
// SEMANTICS, from the mockup's own decision record: single click adds a corner,
// DOUBLE click closes the ring. Leaflet fires two `click`s before a `dblclick`,
// so a click is held for `DOUBLE_CLICK_GRACE_MS` and cancelled if a dblclick
// follows. The grace window ALONE loses that race when the OS double-click
// interval is configured longer than the window (Windows defaults to 500 ms) —
// the first click's timer fires and commits a stray corner before the dblclick
// arrives (found in adversarial review 2026-08-13). So the dblclick handler
// carries a second net: a corner committed within `STRAY_COMMIT_WINDOW_MS` and
// within `STRAY_COMMIT_RADIUS_PX` of the double-click position is the first
// click of THIS double-click, and is discarded before the ring closes.

import { useCallback, useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { Map as LeafletMap, LayerGroup, LeafletMouseEvent } from "leaflet";

/** The same CARTO dark basemap every other map in this app draws. */
const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";

/** How long a single click is held before it becomes a corner. Kept short so
 * drawing does not feel laggy — it does NOT by itself guarantee a double click
 * leaves no stray corner (the OS double-click interval can exceed it); the
 * position/time net below is what closes that gap. */
const DOUBLE_CLICK_GRACE_MS = 220;

/** A corner committed this recently AND this close (in screen pixels) to the
 * double-click position is treated as the first click of that double-click and
 * discarded before the ring closes. The window is generous (well past any sane
 * OS double-click setting); the radius keeps a deliberate earlier corner near
 * the close point safe. */
const STRAY_COMMIT_WINDOW_MS = 900;
const STRAY_COMMIT_RADIUS_PX = 12;

export type LatLon = [number, number];

export interface RegionLassoMapProps {
  /** The ring so far, open (the first corner is not repeated). */
  vertices: LatLon[];
  /** Whether the ring has been closed by a double click. */
  closed: boolean;
  /** The ring's bounding box. Since the trim became polygon-native this is NO
   * LONGER what the study area is cut to — the cyan ring is — and the rectangle
   * is drawn only because it is still the frame the residual metre band and the
   * size estimate are measured against. Hence its demotion below from a solid
   * amber dash to a faint hairline. It comes from the SERVER's measurement
   * (`RegionMeasurement.region`) rather than being derived here, so the shape on
   * screen is the same rectangle the pipeline uses and not a second computation
   * of it. */
  bbox: { south: number; west: number; north: number; east: number } | null;
  /** `null` while the limit has not been read from `GET /region/limits`. When a
   * number, further clicks are ignored at it — the cap is the server's. */
  maxVertices: number | null;
  onAddVertex: (lat: number, lon: number) => void;
  onClose: () => void;
  /** Remove the most recently added corner — called only when the dblclick
   * handler identifies it as the stray first click of the closing double-click
   * (see the header comment's second net). */
  onDiscardLastVertex: () => void;
  /** Where to look when there is nothing drawn yet. */
  centre: LatLon;
  /** Both strings come from the page's three-argument `t()` — this component owns
   * no user-facing text, so ko/en/ja stay in one plumbing rather than two. */
  ariaLabel: string;
  fitLabel: string;
}

export default function RegionLassoMap({
  vertices,
  closed,
  bbox,
  maxVertices,
  onAddVertex,
  onClose,
  onDiscardLastVertex,
  centre,
  ariaLabel,
  fitLabel,
}: RegionLassoMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const drawingRef = useRef<LayerGroup | null>(null);
  const [ready, setReady] = useState(false);

  // The handlers are attached to the map ONCE (re-attaching them on every state
  // change would leak listeners), so they read the current props through a ref
  // rather than through a closure over a stale render.
  const handlers = useRef({ onAddVertex, onClose, onDiscardLastVertex, vertices, maxVertices, closed });
  handlers.current = { onAddVertex, onClose, onDiscardLastVertex, vertices, maxVertices, closed };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let pendingClick: ReturnType<typeof setTimeout> | null = null;
    let lastCommit: { at: number; x: number; y: number } | null = null;

    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !containerRef.current) return;

      const container = containerRef.current;
      (container as { _leaflet_id?: number | null })._leaflet_id = null;
      container.innerHTML = "";

      const map = L.map(container, {
        zoomControl: true,
        preferCanvas: true,
        // OFF, deliberately: a double click is how the ring is closed, and a map
        // that also zoomed on it would move the ground under the shape the user
        // just finished.
        doubleClickZoom: false,
      });
      mapRef.current = map;
      map.setView(centre, 15);
      map.getContainer().style.cursor = "crosshair";

      L.tileLayer(TILE_URL, {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org">OSM</a> &copy; <a href="https://carto.com">CARTO</a>',
        maxZoom: 20,
      }).addTo(map);

      map.on("click", (event: LeafletMouseEvent) => {
        if (pendingClick) clearTimeout(pendingClick);
        pendingClick = setTimeout(() => {
          pendingClick = null;
          const current = handlers.current;
          if (current.closed) return;
          if (current.maxVertices !== null && current.vertices.length >= current.maxVertices) {
            return;
          }
          // Remember what was just committed and WHERE (screen pixels): the
          // dblclick handler uses this to identify — and discard — a corner
          // that was really the first click of a slow double-click.
          lastCommit = {
            at: Date.now(),
            x: event.containerPoint.x,
            y: event.containerPoint.y,
          };
          current.onAddVertex(event.latlng.lat, event.latlng.lng);
        }, DOUBLE_CLICK_GRACE_MS);
      });
      map.on("dblclick", (event: LeafletMouseEvent) => {
        if (pendingClick) {
          clearTimeout(pendingClick);
          pendingClick = null;
        }
        const current = handlers.current;
        // The second net (see header): a corner committed just now, at the
        // double-click's own screen position, was this double-click's first
        // click — the grace timer lost the race. Discard it before closing.
        const stray =
          lastCommit !== null &&
          Date.now() - lastCommit.at <= STRAY_COMMIT_WINDOW_MS &&
          Math.hypot(lastCommit.x - event.containerPoint.x, lastCommit.y - event.containerPoint.y) <=
            STRAY_COMMIT_RADIUS_PX;
        if (stray) {
          lastCommit = null;
          current.onDiscardLastVertex();
        }
        const effectiveLength = current.vertices.length - (stray ? 1 : 0);
        if (effectiveLength >= 3) current.onClose();
      });

      if (!cancelled) setReady(true);
    })();

    return () => {
      cancelled = true;
      if (pendingClick) clearTimeout(pendingClick);
      drawingRef.current?.remove();
      drawingRef.current = null;
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
    // The centre is an initial view only; re-centring on every parent render
    // would fight the user's own panning.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Redraw the ring, its corners and the faint bounding box whenever any of them
  // changes. One layer group, replaced wholesale: a lasso is at most 64 corners,
  // so diffing would be more code than it saves.
  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;

    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || mapRef.current !== map) return;
      drawingRef.current?.remove();
      drawingRef.current = null;

      const layer = L.layerGroup();
      if (bbox) {
        // DEMOTED, and the demotion is the point. This rectangle used to be the
        // shape the study area was cut to, so it was drawn as loudly as the ring;
        // the trim now follows the ring, and the box is only the frame the
        // residual metre band and the size estimate use. A hairline at 45%
        // opacity keeps that fact visible without competing with the shape the
        // user actually drew — deleting it would hide the residual instead.
        L.rectangle(
          [
            [bbox.south, bbox.west],
            [bbox.north, bbox.east],
          ],
          {
            color: "#f59e0b",
            weight: 1,
            opacity: 0.45,
            dashArray: "3 6",
            fill: false,
            interactive: false,
          },
        ).addTo(layer);
      }
      if (vertices.length >= 2) {
        if (closed) {
          // L.polygon closes the ring itself — no repeated first corner.
          L.polygon(vertices, {
            color: "#38bdf8",
            weight: 2,
            fillColor: "#38bdf8",
            fillOpacity: 0.16,
            interactive: false,
          }).addTo(layer);
        } else {
          L.polyline(vertices, {
            color: "#38bdf8",
            weight: 2,
            dashArray: "5 4",
            interactive: false,
          }).addTo(layer);
        }
      }
      vertices.forEach(([lat, lon], index) => {
        L.circleMarker([lat, lon], {
          radius: 4,
          color: "#0ea5e9",
          weight: 2,
          fillColor: index === 0 ? "#fef08a" : "#e0f2fe",
          fillOpacity: 1,
          interactive: false,
        }).addTo(layer);
      });
      if (cancelled || mapRef.current !== map) {
        layer.remove();
        return;
      }
      layer.addTo(map);
      drawingRef.current = layer;
    })();

    return () => {
      cancelled = true;
    };
  }, [ready, vertices, closed, bbox]);

  const fit = useCallback(async () => {
    const map = mapRef.current;
    if (!map || vertices.length === 0) return;
    const L = (await import("leaflet")).default;
    map.fitBounds(L.latLngBounds(vertices), { padding: [40, 40] });
  }, [vertices]);

  return (
    <div className="relative">
      <div
        ref={containerRef}
        role="application"
        aria-label={ariaLabel}
        className="h-[340px] w-full overflow-hidden rounded-lg border border-slate-300 bg-slate-800"
      />
      {vertices.length > 0 && (
        <button
          type="button"
          onClick={() => void fit()}
          className="absolute bottom-2 right-2 z-[400] rounded-md border border-slate-400 bg-surface/90 px-2 py-1 text-[11px] font-semibold text-slate-700"
        >
          {fitLabel}
        </button>
      )}
    </div>
  );
}

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
//
// ---------------------------------------------------------------------------
// UNIT #48 (2026-08-19) — THE CORNERS BECOME DRAGGABLE.
//
// The user drew a region, saw a corner in the wrong place, and had no way to
// move it except by reading its index off the map and typing degrees into the
// table. Worse for the case this unit exists for: unit #40/#48 PRE-FILL this
// map with a derived proposal, and "drag whatever you disagree with" is the
// entire premise of handing a human a shape instead of fetching it.
//
// FOUR decisions, each with the reason it was made rather than the other way:
//
//   1. ONE SOURCE OF TRUTH, UNCHANGED. A drag ends in `onMoveVertex(index, lat,
//      lon)`, which the page wires to `updateVertex` — the SAME callback the
//      editable table's `onCommit` already uses, and the only place a
//      coordinate mutates. Table typing and map dragging are therefore the same
//      write, not two writes that have to be kept in step.
//
//   2. DRAGGING IS ALLOWED ON A CLOSED RING, and that does NOT contradict the
//      "closed ring refuses clicks" rule three lines up. That rule is about
//      ADDING: a closed ring has all the corners it is going to get from
//      clicking, and a click landing inside it would silently grow the shape.
//      MOVING an existing corner changes no count and adds nothing — it is the
//      table's own edit, performed with a mouse. The pre-filled proposal
//      arrives closed, so a rule that stopped drags there would disable the one
//      case this was built for.
//
//   3. THE MARKERS ARE `L.marker` + `L.divIcon`, NOT `L.circleMarker`. Leaflet
//      only implements dragging on `Marker`, and `divIcon` keeps the visual
//      identical (a bordered dot, the first corner wider with an ivory centre —
//      the size-and-fill distinction the three-colour decision left it) with NO
//      image asset, so nothing has to resolve `marker-icon.png` through the
//      bundler. The colours are the ring's own sky, unchanged.
//
//   4. A MARKER SWALLOWS `click` AND FORWARDS `dblclick`. A click on a corner
//      must not also drop a second corner on top of it; but the ring is closed
//      by double-clicking NEAR THE FIRST CORNER, and with interactive markers
//      that double-click can land ON the marker and never reach the map. So the
//      marker forwards it to the same `onClose` the map handler calls. The
//      stray-commit net is untouched and untroubled by this: a first click that
//      landed on a marker never armed the map's pending-click timer at all.
//
// Adding a corner by clicking an edge MIDPOINT is deliberately NOT here — see
// the note beside the drag handlers below for what it would have cost.

import { useCallback, useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { Map as LeafletMap, LayerGroup, LeafletMouseEvent, TileLayer } from "leaflet";

import { useTheme } from "@/components/ThemeProvider";
import { basemapTileUrl } from "@/lib/basemap";

/** The same CARTO dark basemap every other map in this app draws. */

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

/** The drag handle's own size in CSS pixels — the first corner wider, exactly
 * the size-and-fill distinction the three-colour decision left the ring's
 * corners with when they could no longer be yellow. Diameters (a circleMarker's
 * `radius` was 6 and 4), so the handle a pointer has to hit is the dot it can
 * see. Wide enough to grab, small enough not to hide the road under it. */
const HANDLE_SIZE_PX = (isFirst: boolean) => (isFirst ? 14 : 11);

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
  /** UNIT #48 — a corner was dragged to a new place. Wired by the page to the
   * SAME `updateVertex` the editable table's `onCommit` calls, which is what
   * makes the table and the map one binding rather than two. Fired ONCE, on
   * `dragend`: during the drag Leaflet moves the marker itself and this
   * component redraws the ring outline in place (see the handler), so the
   * feedback is live without a React round trip per mouse-move. */
  onMoveVertex: (index: number, lat: number, lon: number) => void;
  /** Where to look when there is nothing drawn yet. */
  centre: LatLon;
  /** All three strings come from the page's three-argument `t()` — this
   * component owns no user-facing text, so ko/en/ja stay in one plumbing rather
   * than two. `handleLabel` is the noun a drag handle's hover title is built
   * from; this file appends the corner's number to it and nothing else. */
  ariaLabel: string;
  fitLabel: string;
  handleLabel: string;
}

export default function RegionLassoMap({
  vertices,
  closed,
  bbox,
  maxVertices,
  onAddVertex,
  onClose,
  onDiscardLastVertex,
  onMoveVertex,
  centre,
  ariaLabel,
  fitLabel,
  handleLabel,
}: RegionLassoMapProps) {
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
  const drawingRef = useRef<LayerGroup | null>(null);
  // TRUE from `dragstart` to `dragend` on a corner handle. The redraw effect
  // below refuses to rebuild the layer group while this is set, and the reason
  // is not tidiness: `layer.remove()` reaches Leaflet's `Marker.onRemove` ->
  // `MarkerDrag.removeHooks` -> `Draggable.disable`, which "counts as first
  // ending the drag" and FIRES `dragend` — so a rebuild that lands mid-gesture
  // does not merely repaint, it COMMITS the corner to wherever the pointer
  // happened to be at that instant and then removes the handle from under it.
  //
  // Keeping the drag's own writes off React state (the outline is moved in
  // place, below) is necessary and was not sufficient: `bbox` is a fresh object
  // out of every server preview, and a preview is scheduled by the previous
  // commit, so its response reliably arrives during the NEXT drag. Deferring is
  // safe because `dragend` always commits through `onMoveVertex`, which hands
  // back a new `vertices` array and re-runs this effect immediately after.
  const draggingRef = useRef(false);
  const [ready, setReady] = useState(false);

  // The handlers are attached to the map ONCE (re-attaching them on every state
  // change would leak listeners), so they read the current props through a ref
  // rather than through a closure over a stale render.
  const handlers = useRef({
    onAddVertex,
    onClose,
    onDiscardLastVertex,
    onMoveVertex,
    vertices,
    maxVertices,
    closed,
  });
  handlers.current = {
    onAddVertex,
    onClose,
    onDiscardLastVertex,
    onMoveVertex,
    vertices,
    maxVertices,
    closed,
  };

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

      tileLayerRef.current = L.tileLayer(tileUrlRef.current, {
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
    // A rebuild during a drag would end that drag and commit a corner the user
    // never dropped -- see `draggingRef`. The commit at `dragend` re-runs this
    // effect with the same dependencies a moment later, so nothing is lost.
    if (draggingRef.current) return;
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;

    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || mapRef.current !== map || draggingRef.current) return;
      drawingRef.current?.remove();
      drawingRef.current = null;

      const layer = L.layerGroup();
      // UNIT #48 — the ring outline is kept in a local so a DRAG can move it in
      // place. Rewriting `vertices` on every mouse-move would re-run this whole
      // effect, which replaces the layer group wholesale and would destroy the
      // very marker being dragged mid-gesture. So the drag updates this one
      // layer's latlngs directly (no React state), and `dragend` commits once —
      // after which the ordinary redraw below rebuilds everything consistently.
      let outline: { setLatLngs: (points: LatLon[]) => void } | null = null;
      if (bbox) {
        // DEMOTED, and the demotion is the point. This rectangle used to be the
        // shape the study area was cut to, so it was drawn as loudly as the ring;
        // the trim now follows the ring, and the box is only the frame the
        // residual metre band and the size estimate use. A hairline at 45%
        // opacity keeps that fact visible without competing with the shape the
        // user actually drew — deleting it would hide the residual instead.
        //
        // MONO since the three-colour decision (2026-08-13; the rule is stated
        // in full above `statusClasses` in app/genesis/region/page.tsx). The
        // stroke was amber, and amber is no longer one of this product's
        // accents — rose means refuse, emerald means pass, sky means the thing
        // you selected or drew. The residual frame is none of those, so it is
        // neutral: `--text-muted`, the same grey the chrome around it uses. The
        // demotion is unchanged — hairline, dashed, 45%.
        L.rectangle(
          [
            [bbox.south, bbox.west],
            [bbox.north, bbox.east],
          ],
          {
            color: "#898781",
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
          outline = L.polygon(vertices, {
            color: "#38bdf8",
            weight: 2,
            fillColor: "#38bdf8",
            fillOpacity: 0.16,
            interactive: false,
          }).addTo(layer);
        } else {
          outline = L.polyline(vertices, {
            color: "#38bdf8",
            weight: 2,
            dashArray: "5 4",
            interactive: false,
          }).addTo(layer);
        }
      }
      // The FIRST corner is the one a user aims the closing double-click near,
      // so it has to be findable. It used to be findable by being yellow, which
      // the three-colour decision (2026-08-13) removed: the accents are rose,
      // emerald and sky, and a ring corner is none of those three states — it is
      // part of the shape being drawn, so it is sky like the rest of the ring.
      // It is told apart by SIZE and FILL instead: a wider marker with an ivory
      // centre against the pale-sky centres of the others. Distinct by weight
      // and shape, which is the same substitution every dropped amber state on
      // these screens took.
      //
      // UNIT #48 — these are `L.marker` + `L.divIcon` rather than
      // `L.circleMarker` for ONE reason: Leaflet implements dragging on Marker
      // only. The look is the same dot at the same two sizes in the same sky,
      // built from a div so nothing has to resolve an image asset through the
      // bundler. `iconAnchor` is the icon's own centre, so the coordinate a
      // drag reports is the point under the dot's middle and not its corner.
      //
      // WHAT IS DELIBERATELY NOT HERE: a "+" handle at each edge's midpoint for
      // INSERTING a corner. It is the natural next reach, and it was left out
      // rather than half-built — it doubles the marker count against a 64-corner
      // cap, it needs its own answer for what "insert" means on an OPEN ring
      // being drawn (there is no edge back to the first corner yet), and it puts
      // a second click-to-add path beside the map click the stray-commit net
      // above exists to protect. Moving corners was the ask; inserting them is
      // named as absent instead of arriving untested.
      const dragCursor = "cursor: grab;";
      vertices.forEach(([lat, lon], index) => {
        const size = HANDLE_SIZE_PX(index === 0);
        const marker = L.marker([lat, lon], {
          keyboard: false,
          draggable: true,
          // Leaflet's own title tooltip, in the page's language: this component
          // owns no user-facing text of its own (see `ariaLabel`'s note).
          title: `${handleLabel} ${index + 1}`,
          icon: L.divIcon({
            className: "",
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2],
            html:
              `<div style="width:${size}px;height:${size}px;box-sizing:border-box;` +
              `border-radius:9999px;border:2px solid #0ea5e9;` +
              `background:${index === 0 ? "#fafaf9" : "#e0f2fe"};${dragCursor}"></div>`,
          }),
        }).addTo(layer);
        // LIVE, without React: the outline follows the handle during the drag
        // by having its own latlngs rewritten. `vertices` is untouched until
        // the drop, which is what keeps this effect from tearing down the
        // marker the pointer is holding.
        // Leaflet fires `dragstart` only once the pointer has passed its own
        // click tolerance, and a `dragstart` is always followed by a `dragend`
        // — so this flag cannot be left raised by a tap that never moved.
        marker.on("dragstart", () => {
          draggingRef.current = true;
        });
        marker.on("drag", () => {
          if (!outline) return;
          // The marker's OWN position, not the pointer's: Leaflet has already
          // applied the drag to it, and reading it back is what keeps the
          // outline and the handle from disagreeing by a pixel.
          const { lat: dragLat, lng: dragLon } = marker.getLatLng();
          const moved = handlers.current.vertices.map(
            (vertex, i) => (i === index ? [dragLat, dragLon] : vertex) as LatLon,
          );
          outline.setLatLngs(moved);
        });
        marker.on("dragend", () => {
          // Lowered BEFORE the commit: `onMoveVertex` hands back a new
          // `vertices` array, and the redraw it triggers is the one that has to
          // run. Any redraw deferred while the drag was in flight rides along
          // with it, since this effect reads every dependency afresh.
          draggingRef.current = false;
          const { lat: nextLat, lng: nextLon } = marker.getLatLng();
          handlers.current.onMoveVertex(index, nextLat, nextLon);
        });
        // A click ON a corner must not ALSO drop a second corner on top of it,
        // so it stops here. A DOUBLE click on one is how a user closes the ring
        // (they aim at the first corner), and with an interactive marker in the
        // way it would otherwise never reach the map's own dblclick handler —
        // so it is forwarded to the same `onClose`, under the same >= 3 rule.
        marker.on("click", (event: { originalEvent?: Event }) => {
          event.originalEvent?.stopPropagation();
        });
        marker.on("dblclick", (event: { originalEvent?: Event }) => {
          event.originalEvent?.stopPropagation();
          const current = handlers.current;
          if (!current.closed && current.vertices.length >= 3) current.onClose();
        });
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
    // `handleLabel` is in here because a language switch changes nothing else
    // on this map: without it the drag handles would keep the hover title they
    // were built with, in a language the rest of the screen has left.
  }, [ready, vertices, closed, bbox, handleLabel]);

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
          // 12px floor, U-1 polish 2026-08-13: this button sits ON the tiles,
          // which is the worst background this app has for small type.
          className="absolute bottom-2 right-2 z-[400] rounded-full border border-slate-400 bg-surface/90 px-2.5 py-1 text-[14px] font-semibold text-slate-700"
        >
          {fitLabel}
        </button>
      )}
    </div>
  );
}

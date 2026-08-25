"use client";

// The LOCAL-COORDINATES SVG canvas — this was the whole of `GateFindingsMap`
// until U-1-map, and it is now the honest FALLBACK behind
// `GenesisTileMap`'s real basemap.
//
// WHEN IT IS STILL SHOWN, and why it is kept rather than deleted: the basemap
// needs degrees, and degrees come from
// `GET /api/genesis/jobs/{id}/artifact/geojson`, which inverts the draft's
// metres on the plane the draft RECORDED. A generation whose census carries no
// recorded projection origin therefore cannot be placed on the earth at all —
// the server refuses to guess one (`services/genesis/geojson.py`), because a
// map drawn about an invented origin is right in shape and wrong in place,
// which is the silent kind of wrong. For those generations this canvas still
// shows the shape and its findings, and `GenesisTileMap` says in plain words
// that it is doing so and why.
//
// Nothing here recomputes a projection: every draw call reads coordinates
// straight out of the artifact, following the SAME isotropic-fit + y-flip
// convention `ApprovalFocusMap.tsx` uses (+y is north in the artifact,
// screen-down is +y in SVG).

import { memo, useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import {
  CONNECTOR_IDENTITY_DASH,
  NORMAL_CONNECTOR,
  NORMAL_LINK,
  REFUSE_COLOR,
  SELECTED_COLOR,
  WARN_COLOR,
  WARN_DASH,
  type HighlightSets,
  type Translate,
} from "@/components/genesis/findings";
import { genesisElementLaneOffsetsM, genesisElementWidthMeters } from "@/lib/genesisArtifacts";
import type {
  GenesisDraftConnector,
  GenesisDraftLink,
  GenesisDraftNetwork,
  GenesisDraftNode,
  GenesisFindingHighlight,
} from "@/lib/genesisArtifacts";
import {
  LANE_DASH_M,
  LANE_DASH_STROKE_M,
  LANE_DASH_STROKE_MIN_PX,
  LANE_GAP_M,
  MARKING_COLOR,
  MIN_ROAD_PX,
  dividerOffsetCoord,
  offsetPolyline,
} from "@/lib/roadWidth";

const VIEW_W = 640;
const VIEW_H = 440;
const VIEW_MARGIN = 16;
const FIT_PAD_RATIO = 0.06;

const MIN_SCALE = 0.5;
const MAX_SCALE = 24;
const WHEEL_ZOOM_STEP = 1.18;

/** Below this many VIEW-space units of movement, a background pointerdown ->
 * pointerup is a CLICK (clears the selection); above it, a DRAG (pan, leaves
 * the selection alone). In the SVG's own 640x440 space, not raw client
 * pixels, so it means the same thing at any zoom level. */
const BACKGROUND_CLICK_TOLERANCE = 4;

/** UNIT #58 -- the node-pick layer's default: NO nodes, so every call site that
 * predates it (`GenesisTileMap`'s fallback) renders exactly what it always did.
 * A module-level constant rather than an inline `[]`, so the default identity is
 * stable and the memoized layer below is not re-rendered by a fresh array on
 * every parent tick. */
const NO_NODES: readonly GenesisDraftNode[] = Object.freeze([]);
/** The node dot's radius, in VIEW units at fit scale. Big enough to be a real
 * click target on a 640x440 canvas, small enough not to swallow its junction. */
const NODE_R = 5.5;

type Bbox = { xMin: number; yMin: number; xMax: number; yMax: number };

export function networkBbox(network: GenesisDraftNetwork): Bbox | null {
  let xMin = Infinity;
  let yMin = Infinity;
  let xMax = -Infinity;
  let yMax = -Infinity;
  let any = false;
  for (const link of network.links) {
    for (const [x, y] of link.points) {
      any = true;
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
      if (y < yMin) yMin = y;
      if (y > yMax) yMax = y;
    }
  }
  if (!any) return null;
  return { xMin, yMin, xMax, yMax };
}

export function fitProjection(bbox: Bbox) {
  const spanX = bbox.xMax - bbox.xMin || 1;
  const spanY = bbox.yMax - bbox.yMin || 1;
  const padX = spanX * FIT_PAD_RATIO;
  const padY = spanY * FIT_PAD_RATIO;
  const x0 = bbox.xMin - padX;
  const y0 = bbox.yMin - padY;
  const totalSpanX = spanX + 2 * padX;
  const totalSpanY = spanY + 2 * padY;
  const availW = VIEW_W - 2 * VIEW_MARGIN;
  const availH = VIEW_H - 2 * VIEW_MARGIN;
  const scale = Math.min(availW / totalSpanX, availH / totalSpanY);
  const offsetX = VIEW_MARGIN + (availW - totalSpanX * scale) / 2;
  const offsetY = VIEW_MARGIN + (availH - totalSpanY * scale) / 2;
  // Y-FLIP: the artifact's +y is north; SVG's +y is screen-down. Without this
  // every generation would render upside down relative to how a consultant
  // reads north-up.
  const projX = (x: number) => offsetX + (x - x0) * scale;
  const projY = (y: number) => offsetY + (bbox.yMax + padY - y) * scale;
  // `scale` (view-units per metre) travels alongside projX/projY -- U-19
  // round 2's road-width ribbons need the SAME metres-to-view-units factor
  // the points themselves were projected with, so a 3-lane link's ribbon and
  // its own polyline agree on what "this many metres" means on screen.
  return { projX, projY, scale };
}

function pointsAttr(points: [number, number][], projX: (x: number) => number, projY: (y: number) => number): string {
  return points.map(([x, y]) => `${projX(x).toFixed(1)},${projY(y).toFixed(1)}`).join(" ");
}

/** Same formatting as `pointsAttr` above, for points that are ALREADY in
 * projected view-space (a lane divider's offset polyline, computed by
 * `offsetPolyline` from already-projected centreline points) rather than raw
 * network metres needing `projX`/`projY`. */
function rawPointsAttr(points: [number, number][]): string {
  return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
}

export default function DraftLocalMap({
  network,
  linksById,
  connectorsById,
  highlights,
  selectedHighlight,
  showNormal,
  showRefuse,
  showWarn,
  onSelectLink,
  onSelectConnector,
  onClearSelection,
  nodes = NO_NODES,
  selectedNodeId = null,
  onSelectNode,
  t,
}: {
  network: GenesisDraftNetwork;
  linksById: Map<number, GenesisDraftLink>;
  connectorsById: Map<number, GenesisDraftConnector>;
  highlights: HighlightSets;
  selectedHighlight: GenesisFindingHighlight | null;
  showNormal: boolean;
  showRefuse: boolean;
  showWarn: boolean;
  onSelectLink: (linkId: number) => void;
  onSelectConnector: (connectorId: number) => void;
  /** UNIT #58 -- the DRAFT'S OWN nodes, drawn as pickable dots. Supplied only
   * by the 현시도 binding map; every other call site leaves it empty and gets
   * the layer it always had. These are the census's own `network.nodes`
   * entries, narrowed to the ones the caller offers as candidates -- this
   * component invents none and ranks none. */
  nodes?: readonly GenesisDraftNode[];
  /** WHICH node is bound, owned by the caller. This component keeps no
   * selection state of its own -- the same discipline the link/connector
   * selection above already follows, so one selection can have two views
   * (this map and the candidate list beside it) without either forking it. */
  selectedNodeId?: number | null;
  /** A click is SELECTION, never confirmation: the 확인 button in the binding
   * section stays the only act that records an attestation. */
  onSelectNode?: (nodeId: number) => void;
  /** Clicking empty canvas (not a link/connector) clears the selection --
   * the SAME semantics `GenesisTileMap.tsx`'s leaflet renderer gives its own
   * basemap click. Distinguished from a pan by movement distance in
   * `handleBackgroundPointerUp` below, since the background rect is also the
   * drag surface. */
  onClearSelection: () => void;
  t: Translate;
}) {
  const [view, setView] = useState({ scale: 1, tx: 0, ty: 0 });
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Toggled by `setSvgRef` below on every mount/unmount of the `<svg>` node --
  // a plain ref's `.current` change is invisible to `useEffect`'s dependency
  // array, so the native wheel-listener effect needs this to know when to
  // re-attach (see that effect for why a JSX `onWheel` cannot be used at all).
  const [svgMounted, setSvgMounted] = useState(false);
  const setSvgRef = useCallback((node: SVGSVGElement | null) => {
    svgRef.current = node;
    setSvgMounted(node !== null);
  }, []);
  const dragState = useRef<{ active: boolean; pointerId: number; startClientX: number; startClientY: number; startTx: number; startTy: number }>({
    active: false,
    pointerId: -1,
    startClientX: 0,
    startClientY: 0,
    startTx: 0,
    startTy: 0,
  });

  const bbox = networkBbox(network);
  const projection = bbox ? fitProjection(bbox) : null;

  const clampScale = (value: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));

  const zoomAt = useCallback((clientX: number, clientY: number, factor: number) => {
    const svg = svgRef.current;
    if (!svg) return;
    const point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const anchor = point.matrixTransform(ctm.inverse());
    setView((current) => {
      const nextScale = clampScale(current.scale * factor);
      const appliedFactor = nextScale / current.scale;
      return {
        scale: nextScale,
        tx: anchor.x - (anchor.x - current.tx) * appliedFactor,
        ty: anchor.y - (anchor.y - current.ty) * appliedFactor,
      };
    });
  }, []);

  // React 19 registers a JSX `onWheel` handler as a PASSIVE listener, so
  // `event.preventDefault()` there is a silent no-op and the page scrolls
  // underneath the map while the user tries to zoom it (verified against this
  // repo's react-dom). The only fix is a real, non-passive DOM listener,
  // attached by hand -- `setSvgRef` (used as the `<svg>` ref below) triggers
  // this effect on every mount/unmount of that element.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handleWheelNative = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY < 0 ? WHEEL_ZOOM_STEP : 1 / WHEEL_ZOOM_STEP;
      zoomAt(event.clientX, event.clientY, factor);
    };
    svg.addEventListener("wheel", handleWheelNative, { passive: false });
    return () => svg.removeEventListener("wheel", handleWheelNative);
  }, [svgMounted, zoomAt]);

  const handleBackgroundPointerDown = (event: ReactPointerEvent<SVGRectElement>) => {
    (event.target as Element).setPointerCapture(event.pointerId);
    dragState.current = {
      active: true,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startTx: view.tx,
      startTy: view.ty,
    };
  };

  const handleBackgroundPointerMove = (event: ReactPointerEvent<SVGRectElement>) => {
    const drag = dragState.current;
    if (!drag.active || drag.pointerId !== event.pointerId) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const scaleX = VIEW_W / rect.width;
    const scaleY = VIEW_H / rect.height;
    const dx = (event.clientX - drag.startClientX) * scaleX;
    const dy = (event.clientY - drag.startClientY) * scaleY;
    setView((current) => ({ ...current, tx: drag.startTx + dx, ty: drag.startTy + dy }));
  };

  const handleBackgroundPointerUp = (event: ReactPointerEvent<SVGRectElement>) => {
    const drag = dragState.current;
    if (drag.pointerId !== event.pointerId) return;
    drag.active = false;
    // A CLICK on empty canvas clears the selection; a DRAG (pan) must not --
    // the background rect is both surfaces, told apart by how far the
    // pointer actually moved between down and up. In VIEW units (the SVG's
    // own 640x440 space, matching `zoomAt`'s already-established pattern of
    // reading real pixels off `getBoundingClientRect`), not raw client
    // pixels, so the tolerance means the same thing at any zoom level.
    const svg = svgRef.current;
    const rect = svg?.getBoundingClientRect();
    const scaleX = rect ? VIEW_W / rect.width : 1;
    const scaleY = rect ? VIEW_H / rect.height : 1;
    const movedX = Math.abs(event.clientX - drag.startClientX) * scaleX;
    const movedY = Math.abs(event.clientY - drag.startClientY) * scaleY;
    if (movedX <= BACKGROUND_CLICK_TOLERANCE && movedY <= BACKGROUND_CLICK_TOLERANCE) {
      onClearSelection();
    }
  };

  if (!projection) return null;

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <button
          type="button"
          onClick={() => setView({ scale: 1, tx: 0, ty: 0 })}
          className="ui-button-ghost rounded-full px-2.5 py-1 text-[14px]"
        >
          {t("보기 초기화", "Reset view", "表示をリセット")}
        </button>
      </div>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-surface" style={{ height: 440 }}>
        <svg ref={setSvgRef} viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="h-full w-full touch-none">
          <defs>
            <filter id="genesis-finding-glow" x="-150%" y="-150%" width="400%" height="400%">
              <feGaussianBlur stdDeviation="2.2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          {/* #60a (2026-08-25, live-test feedback): this rect used to carry a
            * LITERAL light-mode fill (`#f8fafc`, slate-50) left over from
            * before this app's single dark theme locked in -- it painted an
            * opaque white box straight over the container's own `bg-surface`
            * one line up, on every generation's node-pick map. `fill-surface`
            * is the SAME token that container uses (globals.css's
            * `--color-surface`, Tailwind v4 auto-generating the `fill-*`
            * utility for any `--color-*` theme entry), so this rect can never
            * drift from the surface colour again -- one source, not two. */}
          <rect
            x={0}
            y={0}
            width={VIEW_W}
            height={VIEW_H}
            className="fill-surface"
            onPointerDown={handleBackgroundPointerDown}
            onPointerMove={handleBackgroundPointerMove}
            onPointerUp={handleBackgroundPointerUp}
            onPointerCancel={handleBackgroundPointerUp}
          />
          {/* `view` (pan/zoom) is a transform on this `<g>` ONLY -- neither
            * `NetworkLayer` nor `HighlightLayer` below receives `view` as a
            * prop, so a drag/wheel tick (which sets `view` on every
            * pointermove) moves this one attribute without React re-rendering,
            * diffing or re-keying the ~5,000 polylines inside them. See
            * `NetworkLayer`'s own comment for the other half of that fix
            * (stroke width). `LaneDividersLayer` is the ONE exception: it
            * takes `view.scale` on purpose (see its own comment for why a
            * lane divider, unlike a ribbon's stroke width, cannot be frozen
            * by `vectorEffect="non-scaling-stroke"` alone), so it sits
            * BETWEEN the other two -- on top of `NetworkLayer`'s ribbons (a
            * divider drawn underneath its own road would be invisible) but
            * underneath `HighlightLayer`'s warn/refuse/selected treatments
            * (so a lane-marking line never dulls one of those), exactly the
            * z-order `GenesisTileMap.tsx`'s own normal/warn/refuse/selected
            * pass order gives its own dividers -- there by insertion order
            * into one leaflet layer group, here by sibling order under one
            * `<g>`. */}
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
            <NetworkLayer
              network={network}
              highlights={highlights}
              showNormal={showNormal}
              projection={projection}
              onSelectLink={onSelectLink}
              onSelectConnector={onSelectConnector}
            />
            <LaneDividersLayer
              network={network}
              highlights={highlights}
              showNormal={showNormal}
              projection={projection}
              viewScale={view.scale}
            />
            <HighlightLayer
              linksById={linksById}
              connectorsById={connectorsById}
              highlights={highlights}
              selectedHighlight={selectedHighlight}
              showRefuse={showRefuse}
              showWarn={showWarn}
              projection={projection}
              onSelectLink={onSelectLink}
              onSelectConnector={onSelectConnector}
            />
            {/* UNIT #58 -- the node pick layer, LAST so a dot is never buried
              * under a ribbon it sits on. Empty for every caller that supplies
              * no nodes, which is every caller but the 현시도 binding map. */}
            <NodePickLayer
              nodes={nodes}
              selectedNodeId={selectedNodeId}
              projection={projection}
              onSelectNode={onSelectNode}
            />
          </g>
        </svg>
      </div>
      {/* U-1-quick DELETED the "드래그로 이동, 휠로 확대/축소" line that used to
        * sit here. It was an OPERATION hint, not a disclosure: pan-and-zoom on
        * a map is the one interaction every user of this product already knows,
        * the 보기 초기화 button above undoes any mistake, and unlike the
        * local-coordinates disclaimer there is no claim in it that could be
        * lost. Nothing about the data moved into a tooltip here -- this text is
        * gone, on purpose. */}
    </div>
  );
}

/** UNIT #58 -- the draft's own nodes, as pickable dots.
 *
 * WHAT IT DRAWS AND WHAT IT REFUSES TO. Every dot is one entry of the census's
 * `network.nodes` -- the SERVER's own geometry, at the server's own
 * coordinates, projected by the SAME `projection` the roads were. This layer
 * ranks nothing, invents no node, and places none: a junction the draft does
 * not carry has no dot, and the candidate list beside the map says so in words.
 *
 * A click SELECTS. It does not confirm: the attestation is written by the one
 * 확인 button in the 네트워크 결박 section, and the caller owns the selection
 * state that both this map and that section's list read (docs/phase2/
 * DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도 카드 단순화 … (#58)").
 *
 * Memoized against `view` like its siblings: the parent's `<g transform>`
 * carries pan/zoom, so a drag tick does not re-render these dots. */
const NodePickLayer = memo(function NodePickLayer({
  nodes,
  selectedNodeId,
  projection,
  onSelectNode,
}: {
  nodes: readonly GenesisDraftNode[];
  selectedNodeId: number | null;
  projection: { projX: (x: number) => number; projY: (y: number) => number; scale: number };
  onSelectNode?: (nodeId: number) => void;
}) {
  if (nodes.length === 0) return null;
  return (
    <>
      {nodes.map((node) => {
        const selected = node.id === selectedNodeId;
        const cx = projection.projX(node.x);
        const cy = projection.projY(node.y);
        return (
          <g key={`node-${node.id}`}>
            <title>{`${node.id} — ${node.name}`}</title>
            <circle
              cx={cx}
              cy={cy}
              r={NODE_R}
              fill={selected ? SELECTED_COLOR : "#ffffff"}
              stroke={selected ? SELECTED_COLOR : "#475569"}
              strokeWidth={selected ? 3 : 2}
              vectorEffect="non-scaling-stroke"
              className={onSelectNode ? "cursor-pointer" : undefined}
              onClick={onSelectNode ? () => onSelectNode(node.id) : undefined}
            />
            <text
              x={cx}
              y={cy - NODE_R - 3}
              textAnchor="middle"
              fontSize={9}
              fontWeight={600}
              fill="#475569"
              pointerEvents="none"
            >
              {node.id}
            </text>
          </g>
        );
      })}
    </>
  );
});

/** Every NORMAL-tier link/connector ribbon (unit #29: NOT its lane dividers
 * any more -- see `LaneDividersLayer` below), memoized against everything
 * EXCEPT `view` (pan/zoom) -- the parent puts `view` on the surrounding
 * `<g transform>` instead, so a drag/wheel tick that only changes `view`
 * leaves this component's props untouched and `memo` skips the re-render
 * entirely. Without this, each of the ~5,000 links+connectors in a real
 * generation was rebuilt on every `pointermove` of a drag, because the old
 * inline JSX read `view.scale` directly for `strokeWidth`. `vectorEffect="non-scaling-stroke"`
 * replaces that division: it asks the SVG renderer itself to keep a stroke's
 * ON-SCREEN width constant regardless of the ancestor `<g>`'s scale, which is
 * exactly what `width / view.scale` was computing by hand -- so the visual
 * result (constant apparent stroke width while zooming) is unchanged, only
 * how it is computed.
 *
 * Lane dividers and the warn/refuse/selected treatments live in their own
 * sibling components now (`LaneDividersLayer`, `HighlightLayer`) -- see the
 * parent `<g>`'s own comment for why the three are split and what order they
 * render in. */
const NetworkLayer = memo(function NetworkLayer({
  network,
  highlights,
  showNormal,
  projection,
  onSelectLink,
  onSelectConnector,
}: {
  network: GenesisDraftNetwork;
  highlights: HighlightSets;
  showNormal: boolean;
  projection: { projX: (x: number) => number; projY: (y: number) => number; scale: number };
  onSelectLink: (linkId: number) => void;
  onSelectConnector: (connectorId: number) => void;
}) {
  return (
    <>
      {showNormal &&
        network.connectors.map((connector) => {
          if (highlights.byConnector.has(connector.id)) return null;
          if (connector.points.length < 2) return null;
          // U-19 round 2: width-scaled ribbon, not a flat 0.6 -- the SAME
          // formula genesisElementWidthMeters shares with the dashboard/
          // network tab (lib/roadWidth.ts's linkWidthMeters), converted to
          // this SVG's view-units by the SAME scale projX/projY project
          // points with, floored so a 1-lane connector at a wide fit-out
          // stays a real click target.
          //
          // HONEST LIMIT (adversarial verification 2026-08-13; the
          // coherence below added by unit #29's zoom fix): `roadW` is
          // computed at the network-FIT scale once, and the
          // `vectorEffect="non-scaling-stroke"` below then holds the
          // ON-SCREEN width constant while the user zooms -- unlike
          // GenesisTileMap and NetworkViewer, whose ribbons track zoom.
          // That is this fallback's deliberate perf design (`NetworkLayer`
          // is memoized against `view`, so it skips re-rendering ~5,000
          // polylines on every view tick), so the ribbons here state
          // RELATIVE road width, not zoom-true ground width.
          // `LaneDividersLayer`'s own divider offsets are frozen to that
          // SAME on-screen size, for the SAME reason: a divider sits
          // `offM * projection.scale` on-screen pixels from the centreline
          // no matter the zoom, which is exactly this ribbon's own frozen
          // on-screen width. The two are held constant by different
          // mechanisms -- this ribbon's WIDTH by `vectorEffect="non-scaling-stroke"`
          // alone, a divider's lateral OFFSET (baked into vertex positions,
          // which non-scaling-stroke does not touch) by an explicit
          // division in `dividerOffsetCoord` (lib/roadWidth.ts) -- but they
          // land on the same frozen-at-fit-scale screen size, which is what
          // keeps a divider from drifting outside its own ribbon as the
          // user zooms.
          const roadW = Math.max(genesisElementWidthMeters(connector) * projection.scale, MIN_ROAD_PX);
          const points = pointsAttr(connector.points, projection.projX, projection.projY);
          return (
            <g key={`c-${connector.id}`}>
              <polyline
                points={points}
                fill="none"
                stroke={NORMAL_CONNECTOR}
                strokeWidth={roadW}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                opacity={0.8}
                className="cursor-pointer"
                onClick={() => onSelectConnector(connector.id)}
              />
              {/* Connector identity cue -- mirrors NetworkViewer.tsx's Layer 4
                * ("Connectors use the SAME width rule and floor as links --
                * they are pavement, not annotation... a thin, muted centre
                * dashline so an engineer can still tell which pavement inside
                * a junction is a connector"). NOT a narrower ribbon -- the
                * pavement above is full width, same as a link's; only this
                * thin overlay marks it as a connector. Non-interactive: the
                * click target stays the wide ribbon underneath. */}
              <polyline
                points={points}
                fill="none"
                stroke={NORMAL_LINK}
                strokeWidth={0.6}
                strokeDasharray={CONNECTOR_IDENTITY_DASH}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                opacity={0.55}
                pointerEvents="none"
              />
            </g>
          );
        })}
      {showNormal &&
        network.links.map((link) => {
          if (highlights.refuseLinks.has(link.id) || highlights.warnLinks.has(link.id)) return null;
          if (link.points.length < 2) return null;
          // Same width formula as the connector ribbon above -- one function,
          // both element kinds.
          const roadW = Math.max(genesisElementWidthMeters(link) * projection.scale, MIN_ROAD_PX);
          return (
            <polyline
              key={`l-${link.id}`}
              points={pointsAttr(link.points, projection.projX, projection.projY)}
              fill="none"
              stroke={NORMAL_LINK}
              strokeWidth={roadW}
              vectorEffect="non-scaling-stroke"
              strokeLinecap="round"
              opacity={0.85}
              className="cursor-pointer"
              onClick={() => onSelectLink(link.id)}
            />
          );
        })}
    </>
  );
});

/** A NORMAL-tier link's interior lane dividers (unit #29 -- "port lane
 * markings to the genesis draft maps"; zoom fix, adversarial finding #1),
 * split out as its own sibling of `NetworkLayer` rather than folded into it,
 * for one reason: it is the ONE thing under this file's `<g>` that legitimately
 * needs `view.scale` as a prop, and giving it to `NetworkLayer` instead would
 * undo that component's own memoization (a `viewScale` prop that changes on
 * every wheel tick would force `NetworkLayer` to rebuild all ~5,000
 * connector+link polylines on every tick, the exact cost its memo exists to
 * avoid). This layer's own memoized prop set -- `network`, `projection`,
 * `viewScale` -- is deliberately the small one: everything a divider's own
 * geometry depends on, and nothing else `NetworkLayer` also needs
 * (`highlights`/`showNormal` ride along too, only because the SAME
 * normal-tier gate that picks which links get a ribbon also picks which get
 * a divider -- see the skip check below).
 *
 * WHY a divider needs `view.scale` when the ribbon it sits inside does not:
 * `vectorEffect="non-scaling-stroke"` freezes a stroke's ON-SCREEN WIDTH
 * regardless of the ancestor `<g>`'s zoom transform, but it does nothing for
 * a VERTEX'S POSITION -- and a divider's lateral offset from the centreline
 * is baked into vertex positions (`offsetPolyline` moves points sideways),
 * not into a stroke-width property. Those vertices sit inside the SAME
 * `<g transform="scale(view.scale)">` as everything else, so an offset
 * computed once as `offM * projection.scale` (correct at fit scale, where
 * view.scale === 1) stretches right along with the group's own scale as the
 * user zooms in, while the ribbon's frozen width stays put -- so the
 * outermost divider of an L-lane road walks outside its own ribbon once
 * zoom exceeds roughly L / (L - 2) (a 6-lane road, ~1.5x, about 3
 * `WHEEL_ZOOM_STEP` ticks). `dividerOffsetCoord` (lib/roadWidth.ts) is the
 * fix: divide the metre-scaled offset by `view.scale` before it becomes a
 * vertex position, so the group's own `scale(view.scale)` multiplies it
 * right back out to the constant `offM * projection.scale` on screen, at
 * ANY zoom -- reaching, by arithmetic, the same frozen on-screen size
 * `vectorEffect="non-scaling-stroke"` reaches for a stroke's width alone.
 *
 * Rendered BETWEEN `NetworkLayer` and `HighlightLayer` in the parent's `<g>`
 * -- a divider must draw on top of its OWN link's ribbon (drawn by
 * `NetworkLayer`, earlier in the `<g>`) to be visible at all, and underneath
 * any warn/refuse/selected treatment (drawn by `HighlightLayer`, later) so
 * those are never dulled by a lane-marking line on top of them. The
 * refuse/warn skip below already keeps a divider from ever sharing a link
 * with a refuse/warn treatment; sibling order is what protects `selected`,
 * which a normal-tier link can carry at the same time as its own divider.
 * This mirrors `GenesisTileMap.tsx`'s own draw order exactly: its "normal"
 * pass adds a link's divider immediately after that link's own ribbon, and
 * its later "warn"/"refuse"/"selected" passes end up on top because
 * leaflet's own insertion order is the z-order there too. */
const LaneDividersLayer = memo(function LaneDividersLayer({
  network,
  highlights,
  showNormal,
  projection,
  viewScale,
}: {
  network: GenesisDraftNetwork;
  highlights: HighlightSets;
  showNormal: boolean;
  projection: { projX: (x: number) => number; projY: (y: number) => number; scale: number };
  viewScale: number;
}) {
  if (!showNormal) return null;
  return (
    <>
      {network.links.map((link) => {
        if (highlights.refuseLinks.has(link.id) || highlights.warnLinks.has(link.id)) return null;
        if (link.points.length < 2) return null;
        // Lane dividers (unit #29) -- interior lane BOUNDARIES only, never
        // edge lines, mirroring NetworkViewer.tsx's own Layer 3 ("the
        // casing already reads as the road edge, and dropping [edge lines]
        // also avoids the largest, spike-prone +/-width/2 offsets on
        // curved arcs"). Connectors do NOT get this treatment, matching
        // NetworkViewer: a connector's own identity is already the thin
        // CONNECTOR_IDENTITY_DASH centreline `NetworkLayer` draws, and
        // adding a second dash rhythm on top would read as a third,
        // undocumented state.
        //
        // READABILITY GATE, not a new clamp: `roadW` is a value FROZEN at
        // this file's own fit scale (see `NetworkLayer`'s top comment --
        // `vectorEffect="non-scaling-stroke"` holds it constant across the
        // user's own pan/zoom). A divider is drawn only when `roadW` is
        // still the link's real metre-true width and not the MIN_ROAD_PX
        // floor -- below that floor the ribbon is already wider on screen
        // than its own geometry supports, so an interior line computed
        // from that same geometry has nothing honest left to sit inside
        // (see `NetworkLayer`'s "HONEST LIMIT" comment for why the
        // ribbon's width is relative, not zoom-true, in the first place --
        // a divider drawn from the same frozen-at-fit-scale geometry
        // inherits exactly that limit, not a new one).
        const roadW = Math.max(genesisElementWidthMeters(link) * projection.scale, MIN_ROAD_PX);
        const dividerOffsetsM = roadW > MIN_ROAD_PX ? genesisElementLaneOffsetsM(link) : [];
        if (dividerOffsetsM.length === 0) return null;
        const projectedPoints: [number, number][] = link.points.map(
          ([x, y]) => [projection.projX(x), projection.projY(y)] as [number, number],
        );
        return (
          <g key={`ld-${link.id}`}>
            {dividerOffsetsM.map((offM, index) => (
              <polyline
                key={`ld-${link.id}-${index}`}
                points={rawPointsAttr(offsetPolyline(projectedPoints, dividerOffsetCoord(offM, projection.scale, viewScale)))}
                fill="none"
                stroke={MARKING_COLOR}
                strokeWidth={Math.max(LANE_DASH_STROKE_M * projection.scale, LANE_DASH_STROKE_MIN_PX)}
                strokeDasharray={`${LANE_DASH_M * projection.scale} ${LANE_GAP_M * projection.scale}`}
                strokeOpacity={0.55}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                pointerEvents="none"
              />
            ))}
          </g>
        );
      })}
    </>
  );
});

/** Warn/refuse/selected treatments, the strongest claims on the map and
 * always the LAST thing drawn under the parent's `<g>` (see that `<g>`'s own
 * comment for the full three-layer order) -- a sibling of `NetworkLayer` and
 * `LaneDividersLayer`, memoized against everything except `view` for the
 * SAME reason `NetworkLayer` is. Unlike `LaneDividersLayer`, nothing drawn
 * here bakes a lateral offset into a vertex position -- every polyline below
 * redraws a link/connector's own raw points -- so `vectorEffect="non-scaling-stroke"`
 * alone is enough to hold these strokes to a constant on-screen width across
 * zoom, exactly like `NetworkLayer`'s own ribbons; there is nothing here
 * that needs `view.scale`. */
const HighlightLayer = memo(function HighlightLayer({
  linksById,
  connectorsById,
  highlights,
  selectedHighlight,
  showRefuse,
  showWarn,
  projection,
  onSelectLink,
  onSelectConnector,
}: {
  linksById: Map<number, GenesisDraftLink>;
  connectorsById: Map<number, GenesisDraftConnector>;
  highlights: HighlightSets;
  selectedHighlight: GenesisFindingHighlight | null;
  showRefuse: boolean;
  showWarn: boolean;
  projection: { projX: (x: number) => number; projY: (y: number) => number; scale: number };
  onSelectLink: (linkId: number) => void;
  onSelectConnector: (connectorId: number) => void;
}) {
  return (
    <>
      {showWarn &&
        [...highlights.warnLinks].map((linkId) => {
          const link = linksById.get(linkId);
          if (!link || link.points.length < 2) return null;
          return (
            <polyline
              key={`wl-${linkId}`}
              points={pointsAttr(link.points, projection.projX, projection.projY)}
              fill="none"
              stroke={WARN_COLOR}
              strokeWidth={2.4}
              strokeDasharray={WARN_DASH}
              vectorEffect="non-scaling-stroke"
              strokeLinecap="round"
              className="cursor-pointer"
              onClick={() => onSelectLink(linkId)}
            />
          );
        })}
      {showWarn &&
        [...highlights.warnConnectors].map((connectorId) => {
          const connector = connectorsById.get(connectorId);
          if (!connector || connector.points.length < 2) return null;
          return (
            <polyline
              key={`wc-${connectorId}`}
              points={pointsAttr(connector.points, projection.projX, projection.projY)}
              fill="none"
              stroke={WARN_COLOR}
              strokeWidth={2.4}
              strokeDasharray={WARN_DASH}
              vectorEffect="non-scaling-stroke"
              strokeLinecap="round"
              className="cursor-pointer"
              onClick={() => onSelectConnector(connectorId)}
            />
          );
        })}
      {showRefuse &&
        [...highlights.refuseLinks].map((linkId) => {
          const link = linksById.get(linkId);
          if (!link || link.points.length < 2) return null;
          return (
            <polyline
              key={`rl-${linkId}`}
              points={pointsAttr(link.points, projection.projX, projection.projY)}
              fill="none"
              stroke={REFUSE_COLOR}
              strokeWidth={2.8}
              vectorEffect="non-scaling-stroke"
              strokeLinecap="round"
              filter="url(#genesis-finding-glow)"
              className="cursor-pointer"
              onClick={() => onSelectLink(linkId)}
            />
          );
        })}
      {showRefuse &&
        [...highlights.refuseConnectors].map((connectorId) => {
          const connector = connectorsById.get(connectorId);
          if (!connector || connector.points.length < 2) return null;
          return (
            <polyline
              key={`rc-${connectorId}`}
              points={pointsAttr(connector.points, projection.projX, projection.projY)}
              fill="none"
              stroke={REFUSE_COLOR}
              strokeWidth={2.8}
              vectorEffect="non-scaling-stroke"
              strokeLinecap="round"
              filter="url(#genesis-finding-glow)"
              className="cursor-pointer"
              onClick={() => onSelectConnector(connectorId)}
            />
          );
        })}
      {selectedHighlight && (
        <>
          {selectedHighlight.linkIds.map((linkId) => {
            const link = linksById.get(linkId);
            if (!link || link.points.length < 2) return null;
            return (
              <polyline
                key={`sel-l-${linkId}`}
                points={pointsAttr(link.points, projection.projX, projection.projY)}
                fill="none"
                stroke={SELECTED_COLOR}
                strokeWidth={4}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                opacity={0.9}
                pointerEvents="none"
              />
            );
          })}
          {selectedHighlight.connectorIds.map((connectorId) => {
            const connector = connectorsById.get(connectorId);
            if (!connector || connector.points.length < 2) return null;
            return (
              <polyline
                key={`sel-c-${connectorId}`}
                points={pointsAttr(connector.points, projection.projX, projection.projY)}
                fill="none"
                stroke={SELECTED_COLOR}
                strokeWidth={4}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                opacity={0.9}
                pointerEvents="none"
              />
            );
          })}
        </>
      )}
    </>
  );
});

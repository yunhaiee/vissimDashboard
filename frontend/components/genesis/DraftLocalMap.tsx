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
  NORMAL_CONNECTOR,
  NORMAL_LINK,
  REFUSE_COLOR,
  SELECTED_COLOR,
  WARN_COLOR,
  WARN_DASH,
  type HighlightSets,
  type Translate,
} from "@/components/genesis/findings";
import type {
  GenesisDraftConnector,
  GenesisDraftLink,
  GenesisDraftNetwork,
  GenesisFindingHighlight,
} from "@/lib/genesisArtifacts";

const VIEW_W = 640;
const VIEW_H = 440;
const VIEW_MARGIN = 16;
const FIT_PAD_RATIO = 0.06;

const MIN_SCALE = 0.5;
const MAX_SCALE = 24;
const WHEEL_ZOOM_STEP = 1.18;

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
  return { projX, projY };
}

function pointsAttr(points: [number, number][], projX: (x: number) => number, projY: (y: number) => number): string {
  return points.map(([x, y]) => `${projX(x).toFixed(1)},${projY(y).toFixed(1)}`).join(" ");
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
    if (dragState.current.pointerId === event.pointerId) dragState.current.active = false;
  };

  if (!projection) return null;

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <button
          type="button"
          onClick={() => setView({ scale: 1, tx: 0, ty: 0 })}
          className="ui-button-ghost rounded-full px-2.5 py-1 text-[12px]"
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
          <rect
            x={0}
            y={0}
            width={VIEW_W}
            height={VIEW_H}
            fill="#f8fafc"
            onPointerDown={handleBackgroundPointerDown}
            onPointerMove={handleBackgroundPointerMove}
            onPointerUp={handleBackgroundPointerUp}
            onPointerCancel={handleBackgroundPointerUp}
          />
          {/* `view` (pan/zoom) is a transform on this `<g>` ONLY --
            * `NetworkLayer` below is memoized and does not receive `view` as a
            * prop, so a drag/wheel tick (which sets `view` on every
            * pointermove) moves this one attribute without React re-rendering,
            * diffing or re-keying the ~5,000 polylines inside it. See
            * `NetworkLayer`'s own comment for the other half of this fix
            * (stroke width). */}
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
            <NetworkLayer
              network={network}
              linksById={linksById}
              connectorsById={connectorsById}
              highlights={highlights}
              selectedHighlight={selectedHighlight}
              showNormal={showNormal}
              showRefuse={showRefuse}
              showWarn={showWarn}
              projection={projection}
              onSelectLink={onSelectLink}
              onSelectConnector={onSelectConnector}
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

/** Every link/connector polyline, memoized against everything EXCEPT `view`
 * (pan/zoom) -- the parent puts `view` on the surrounding `<g transform>`
 * instead, so a drag/wheel tick that only changes `view` leaves this
 * component's props untouched and `memo` skips the re-render entirely.
 * Without this, each of the ~5,000 links+connectors in a real generation was
 * rebuilt on every `pointermove` of a drag, because the old inline JSX read
 * `view.scale` directly for `strokeWidth`. `vectorEffect="non-scaling-stroke"`
 * replaces that division: it asks the SVG renderer itself to keep a stroke's
 * ON-SCREEN width constant regardless of the ancestor `<g>`'s scale, which is
 * exactly what `width / view.scale` was computing by hand -- so the visual
 * result (constant apparent stroke width while zooming, including the
 * selected-finding glow) is unchanged, only how it is computed. */
const NetworkLayer = memo(function NetworkLayer({
  network,
  linksById,
  connectorsById,
  highlights,
  selectedHighlight,
  showNormal,
  showRefuse,
  showWarn,
  projection,
  onSelectLink,
  onSelectConnector,
}: {
  network: GenesisDraftNetwork;
  linksById: Map<number, GenesisDraftLink>;
  connectorsById: Map<number, GenesisDraftConnector>;
  highlights: HighlightSets;
  selectedHighlight: GenesisFindingHighlight | null;
  showNormal: boolean;
  showRefuse: boolean;
  showWarn: boolean;
  projection: { projX: (x: number) => number; projY: (y: number) => number };
  onSelectLink: (linkId: number) => void;
  onSelectConnector: (connectorId: number) => void;
}) {
  return (
    <>
      {showNormal &&
        network.connectors.map((connector) => {
          if (highlights.byConnector.has(connector.id)) return null;
          if (connector.points.length < 2) return null;
          return (
            <polyline
              key={`c-${connector.id}`}
              points={pointsAttr(connector.points, projection.projX, projection.projY)}
              fill="none"
              stroke={NORMAL_CONNECTOR}
              strokeWidth={0.6}
              vectorEffect="non-scaling-stroke"
              opacity={0.8}
            />
          );
        })}
      {showNormal &&
        network.links.map((link) => {
          if (highlights.refuseLinks.has(link.id) || highlights.warnLinks.has(link.id)) return null;
          if (link.points.length < 2) return null;
          return (
            <polyline
              key={`l-${link.id}`}
              points={pointsAttr(link.points, projection.projX, projection.projY)}
              fill="none"
              stroke={NORMAL_LINK}
              strokeWidth={1.1}
              vectorEffect="non-scaling-stroke"
              strokeLinecap="round"
              opacity={0.85}
            />
          );
        })}
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

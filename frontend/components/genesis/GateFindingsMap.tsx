"use client";

// Roadmap v2 §5.B B3 (완결), half 1/2 -- "거부된 생성물이 '왜'를 화면에서 설명":
// a map of the draft network with the gate's refuse/warn findings and the
// generator's own excluded zones drawn on top of it, so a refused generation's
// reason is a PLACE, not only a sentence in the checklist above it.
//
// Lazily fetches ONE extra artifact (`genesis_json`, via
// lib/genesisArtifacts.ts's `fetchGenesisDraftNetwork`) the first time this
// panel is opened for a job -- opened automatically when the verdict is
// `refused` (the roadmap line this unit closes), collapsed otherwise so a
// PUBLISHED generation's page never pays for a network fetch nobody asked to
// see. Every draw call below reads coordinates straight out of that artifact;
// nothing here recomputes a projection or a limit the way `region/page.tsx`'s
// own comments forbid for the same reason.
//
// WHAT THIS PANEL DOES NOT DO: geometry-diff two networks (that is
// benchmark.py's domain -- see the comparison panel beside this one), and it
// does not invent a location for a finding it cannot place. A finding whose
// `subject` matches nothing in the artifact (see
// `resolveFindingHighlight`'s own doc comment for exactly which finding codes
// that can happen to) is still LISTED, in its own "위치를 찾지 못한 항목"
// section -- never silently dropped, and the map itself never renders as a
// silent blank: no artifact, an empty network and a load failure each get
// their own named message.

import { memo, useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import type { GenesisGateCheck, GenesisGateFinding, GenesisGateReport } from "@/lib/api";
import {
  fetchGenesisDraftNetwork,
  resolveFindingHighlight,
  type GenesisDraftConnector,
  type GenesisDraftLink,
  type GenesisDraftNetwork,
  type GenesisFindingHighlight,
  type GenesisIsolatedZone,
} from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

// Fixed viewBox; pan/zoom is a `<g>` transform on top of it, following the
// SAME isotropic-fit + y-flip convention ApprovalFocusMap.tsx already uses
// elsewhere in this app (+y is north in the artifact, screen-down is +y in
// SVG) -- this file states the same reasoning, not a second invented one.
const VIEW_W = 640;
const VIEW_H = 440;
const VIEW_MARGIN = 16;
const FIT_PAD_RATIO = 0.06;

const MIN_SCALE = 0.5;
const MAX_SCALE = 24;
const WHEEL_ZOOM_STEP = 1.18;

const NORMAL_LINK = "#c7cfdb";
const NORMAL_CONNECTOR = "#dbe1ea";
const REFUSE_COLOR = "#e11d48"; // rose-600, matches this app's existing "fail" convention
const WARN_COLOR = "#d97706"; // amber-600, matches this app's existing "not_checkable"/warn convention
const SELECTED_COLOR = "#0ea5e9"; // sky-500, reserved for the clicked element

type Bbox = { xMin: number; yMin: number; xMax: number; yMax: number };

function networkBbox(network: GenesisDraftNetwork): Bbox | null {
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

function fitProjection(bbox: Bbox) {
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

type FindingRef = {
  checkId: string;
  checkTitle: string;
  tier: GenesisGateCheck["tier"];
  finding: GenesisGateFinding;
  via: "finding" | "warning";
};

// A clicked link/connector can carry MORE THAN ONE finding -- V4's lane
// checks alone can put three separate findings on one connector. `refs`
// therefore carries every finding for the clicked element, never just the
// first; the detail panel lists all of them rather than silently dropping the
// rest (see `DetailPanel`'s "finding" branch).
type SelectedItem =
  | { kind: "finding"; refs: FindingRef[] }
  | { kind: "excluded_zone"; zone: GenesisIsolatedZone }
  | null;

interface HighlightSets {
  refuseLinks: Set<number>;
  warnLinks: Set<number>;
  refuseConnectors: Set<number>;
  warnConnectors: Set<number>;
  byLink: Map<number, FindingRef[]>;
  byConnector: Map<number, FindingRef[]>;
  unresolved: FindingRef[];
}

function collectFindingRefs(report: GenesisGateReport): FindingRef[] {
  const refs: FindingRef[] = [];
  for (const check of report.checks) {
    for (const finding of check.findings) {
      refs.push({ checkId: check.id, checkTitle: check.title, tier: check.tier, finding, via: "finding" });
    }
    for (const warning of check.warnings) {
      refs.push({ checkId: check.id, checkTitle: check.title, tier: check.tier, finding: warning, via: "warning" });
    }
  }
  return refs;
}

export default function GateFindingsMap({
  jobId,
  report,
  hasArtifact,
  autoOpen,
}: {
  jobId: string;
  report: GenesisGateReport | null;
  /** Whether this job's record carries a network artifact at all
   * (`selected.artifact != null` on the caller side) -- a job that errored
   * before a network was ever drafted has no `genesis_json` to fetch, and the
   * panel must say that rather than attempt a fetch that can only 404. */
  hasArtifact: boolean;
  /** Open by default (verdict === "refused" on the caller side) -- otherwise
   * the panel starts collapsed so a healthy generation's page never pays for
   * a network fetch nobody asked to see. */
  autoOpen: boolean;
}) {
  const { t } = useLanguage();

  const [open, setOpen] = useState(autoOpen);
  const [network, setNetwork] = useState<GenesisDraftNetwork | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedItem>(null);
  const [showNormal, setShowNormal] = useState(true);
  const [showRefuse, setShowRefuse] = useState(true);
  const [showWarn, setShowWarn] = useState(true);

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

  // Reset everything the moment the job changes -- a stale network under a
  // new job's findings would silently mislabel every highlight.
  useEffect(() => {
    setOpen(autoOpen);
    setNetwork(null);
    setLoadError(null);
    setSelected(null);
    setView({ scale: 1, tx: 0, ty: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  useEffect(() => {
    if (!open || network || loading || !hasArtifact) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchGenesisDraftNetwork(jobId)
      .then((payload) => {
        if (!cancelled) setNetwork(payload);
      })
      .catch((caught) => {
        if (!cancelled) setLoadError(caught instanceof Error ? caught.message : String(caught));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, network, loading, hasArtifact, jobId]);

  const bbox = useMemo(() => (network ? networkBbox(network) : null), [network]);
  const projection = useMemo(() => (bbox ? fitProjection(bbox) : null), [bbox]);

  const linksById = useMemo(() => {
    const map = new Map<number, GenesisDraftLink>();
    for (const link of network?.links ?? []) map.set(link.id, link);
    return map;
  }, [network]);
  const connectorsById = useMemo(() => {
    const map = new Map<number, GenesisDraftConnector>();
    for (const connector of network?.connectors ?? []) map.set(connector.id, connector);
    return map;
  }, [network]);

  const findingRefs = useMemo(() => (report ? collectFindingRefs(report) : []), [report]);

  // Highlight sets, one pass over every finding: which links/connectors carry
  // a refuse-tier finding, which carry a warn-tier one, and which findings
  // resolved to nothing at all. A link touched by both a refuse and a warn
  // finding is drawn refuse -- the stronger claim wins the pixel. `byLink`/
  // `byConnector` collect EVERY finding touching one element (not just the
  // first) -- a click surfaces the whole list, see `DetailPanel`.
  const highlights = useMemo<HighlightSets>(() => {
    const refuseLinks = new Set<number>();
    const warnLinks = new Set<number>();
    const refuseConnectors = new Set<number>();
    const warnConnectors = new Set<number>();
    const byLink = new Map<number, FindingRef[]>();
    const byConnector = new Map<number, FindingRef[]>();
    const unresolved: FindingRef[] = [];
    if (network) {
      for (const ref of findingRefs) {
        const highlight = resolveFindingHighlight(ref.finding, network);
        if (!highlight.resolved) {
          unresolved.push(ref);
          continue;
        }
        const isRefuse = ref.tier === "refuse";
        for (const linkId of highlight.linkIds) {
          (isRefuse ? refuseLinks : warnLinks).add(linkId);
          if (!byLink.has(linkId)) byLink.set(linkId, []);
          byLink.get(linkId)!.push(ref);
        }
        for (const connectorId of highlight.connectorIds) {
          (isRefuse ? refuseConnectors : warnConnectors).add(connectorId);
          if (!byConnector.has(connectorId)) byConnector.set(connectorId, []);
          byConnector.get(connectorId)!.push(ref);
        }
      }
    }
    return { refuseLinks, warnLinks, refuseConnectors, warnConnectors, byLink, byConnector, unresolved };
  }, [findingRefs, network]);

  const excludedZones = network?.metadata.connectivity?.excluded_zones ?? [];

  // Clicking a link/connector selects EVERY finding touching it (see
  // `highlights.byLink`/`byConnector` above) -- stable across a drag/zoom
  // frame because `highlights` only changes when `findingRefs`/`network` do,
  // which is what lets `NetworkLayer` below stay memoized through a pan.
  const handleSelectLink = useCallback(
    (linkId: number) => {
      const refs = highlights.byLink.get(linkId) ?? [];
      if (refs.length > 0) setSelected({ kind: "finding", refs });
    },
    [highlights],
  );
  const handleSelectConnector = useCallback(
    (connectorId: number) => {
      const refs = highlights.byConnector.get(connectorId) ?? [];
      if (refs.length > 0) setSelected({ kind: "finding", refs });
    },
    [highlights],
  );

  // The currently-selected finding(s), redrawn once more on top in their own
  // colour -- an outline the size of a whole isolated zone can otherwise make
  // it unclear WHICH click the detail panel is showing. Unions every selected
  // finding's own highlight (not just the first) since two findings on the
  // same clicked element are not guaranteed to resolve to the same zone.
  const selectedHighlight = useMemo<GenesisFindingHighlight | null>(() => {
    if (!network || selected?.kind !== "finding") return null;
    const linkIds = new Set<number>();
    const connectorIds = new Set<number>();
    for (const ref of selected.refs) {
      const highlight = resolveFindingHighlight(ref.finding, network);
      for (const linkId of highlight.linkIds) linkIds.add(linkId);
      for (const connectorId of highlight.connectorIds) connectorIds.add(connectorId);
    }
    return { linkIds: [...linkIds], connectorIds: [...connectorIds], resolved: true };
  }, [network, selected]);

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
  // attached by hand -- `setSvgNode` (used as the `<svg>` ref below) triggers
  // this effect on every mount/unmount of that element, since the element
  // only exists while `open && !loading && !loadError && network...`.
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

  const resetView = () => setView({ scale: 1, tx: 0, ty: 0 });

  if (!hasArtifact) {
    return (
      <MapShell title={t("지도에서 보기", "View on the map", "地図で見る")} t={t}>
        <EmptyState
          text={t(
            "이 생성은 네트워크를 만들지 못했습니다 — 지도로 보여줄 지오메트리가 없습니다.",
            "This generation never produced a network -- there is no geometry to show.",
            "この生成はネットワークを作成できませんでした — 地図で表示できる形状がありません。",
          )}
        />
      </MapShell>
    );
  }

  return (
    <MapShell
      title={t("지도에서 보기", "View on the map", "地図で見る")}
      subtitle={t(
        "링크는 초안 네트워크(genesis_json)의 좌표 그대로입니다 — 배경지도가 아니라 이 생성의 로컬 좌표계입니다.",
        "Links are drawn from the draft network's own coordinates (genesis_json) -- there is no basemap; this is the generation's own local coordinate system.",
        "リンクは下書きネットワーク(genesis_json)自体の座標です — 背景地図ではなく、この生成のローカル座標系です。",
      )}
      t={t}
      open={open}
      onToggle={() => setOpen((current) => !current)}
    >
      {open && (
        <>
          {loading && (
            <div className="flex h-[260px] items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 text-xs text-slate-500">
              {t("네트워크를 불러오는 중…", "Loading the network…", "ネットワークを読み込み中…")}
            </div>
          )}
          {!loading && loadError && (
            <EmptyState
              text={`${t("네트워크를 불러오지 못했습니다", "Could not load the network", "ネットワークを読み込めませんでした")}: ${loadError}`}
            />
          )}
          {!loading && !loadError && network && network.links.length === 0 && (
            <EmptyState
              text={t(
                "이 초안에는 링크가 하나도 없습니다.",
                "This draft has no links at all.",
                "この下書きにはリンクが一つもありません。",
              )}
            />
          )}
          {!loading && !loadError && network && network.links.length > 0 && projection && (
            <div className="flex flex-col gap-2 lg:flex-row">
              <div className="flex-1">
                <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-600">
                  <LegendToggle color={NORMAL_LINK} label={t("일반", "Normal", "通常")} checked={showNormal} onChange={setShowNormal} />
                  <LegendToggle
                    color={REFUSE_COLOR}
                    label={t("거부 등급 발견", "Refuse-tier findings", "拒否ティアの検出")}
                    checked={showRefuse}
                    onChange={setShowRefuse}
                  />
                  <LegendToggle
                    color={WARN_COLOR}
                    label={t("경고 등급 발견", "Warn-tier findings", "警告ティアの検出")}
                    checked={showWarn}
                    onChange={setShowWarn}
                  />
                  <button
                    type="button"
                    onClick={resetView}
                    className="ml-auto rounded border border-slate-300 bg-white px-2 py-0.5 font-semibold text-slate-600"
                  >
                    {t("보기 초기화", "Reset view", "表示をリセット")}
                  </button>
                </div>
                <div className="overflow-hidden rounded-lg border border-slate-200 bg-white" style={{ height: 440 }}>
                  <svg
                    ref={setSvgRef}
                    viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                    className="h-full w-full touch-none"
                  >
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
                      * `NetworkLayer` below is memoized and does not receive
                      * `view` as a prop, so a drag/wheel tick (which sets
                      * `view` on every pointermove) moves this one attribute
                      * without React re-rendering, diffing or re-keying the
                      * ~5,000 polylines inside it. See `NetworkLayer`'s own
                      * comment for the other half of this fix (stroke width). */}
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
                        onSelectLink={handleSelectLink}
                        onSelectConnector={handleSelectConnector}
                      />
                    </g>
                  </svg>
                </div>
                <p className="mt-1 text-[10px] text-slate-400">
                  {t("드래그로 이동, 휠로 확대/축소", "Drag to pan, wheel to zoom", "ドラッグで移動、ホイールで拡大縮小")}
                </p>
              </div>

              <DetailPanel
                selected={selected}
                onClear={() => setSelected(null)}
                unresolvedCount={highlights.unresolved.length}
                unresolved={highlights.unresolved}
                onSelectUnresolved={(ref) => setSelected({ kind: "finding", refs: [ref] })}
                excludedZones={excludedZones}
                onSelectExcludedZone={(zone) => setSelected({ kind: "excluded_zone", zone })}
                t={t}
              />
            </div>
          )}
        </>
      )}
    </MapShell>
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

function LegendToggle({
  color,
  label,
  checked,
  onChange,
}: {
  color: string;
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-1.5">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} aria-hidden="true" />
      {label}
    </label>
  );
}

function MapShell({
  title,
  subtitle,
  t,
  open,
  onToggle,
  children,
}: {
  title: string;
  subtitle?: string;
  t: Translate;
  open?: boolean;
  onToggle?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3.5 py-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-slate-900">{title}</p>
          {subtitle && <p className="mt-0.5 text-[11px] text-slate-500">{subtitle}</p>}
        </div>
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            className="shrink-0 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700"
          >
            {open ? t("접기", "Collapse", "折りたたむ") : t("펼치기", "Expand", "展開")}
          </button>
        )}
      </div>
      <div className={open === false ? "hidden" : "mt-2"}>{children}</div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-xs text-slate-500">
      {text}
    </p>
  );
}

function DetailPanel({
  selected,
  onClear,
  unresolved,
  unresolvedCount,
  onSelectUnresolved,
  excludedZones,
  onSelectExcludedZone,
  t,
}: {
  selected: SelectedItem;
  onClear: () => void;
  unresolved: FindingRef[];
  unresolvedCount: number;
  onSelectUnresolved: (ref: FindingRef) => void;
  excludedZones: GenesisIsolatedZone[];
  onSelectExcludedZone: (zone: GenesisIsolatedZone) => void;
  t: Translate;
}) {
  return (
    <div className="flex w-full flex-col gap-2 lg:w-[280px] lg:shrink-0">
      <div className="min-h-[140px] rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-[11px] text-slate-700">
        {!selected && (
          <p className="text-slate-400">
            {t(
              "표시된 링크를 클릭하면 해당 발견의 상세 정보가 여기 나타납니다.",
              "Click a highlighted link to see that finding's detail here.",
              "強調表示されたリンクをクリックすると、この発見の詳細がここに表示されます。",
            )}
          </p>
        )}
        {selected?.kind === "finding" && (
          <>
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase text-slate-500">
                {selected.refs.length > 1
                  ? t(
                      `이 요소의 발견 ${selected.refs.length}건`,
                      `${selected.refs.length} findings on this element`,
                      `この要素の発見 ${selected.refs.length} 件`,
                    )
                  : t("이 요소의 발견", "Finding on this element", "この要素の発見")}
              </span>
              <button type="button" onClick={onClear} className="text-slate-400">
                {t("닫기", "Close", "閉じる")}
              </button>
            </div>
            {/* EVERY finding on the clicked element, not just the first --
              * one connector can carry three separate V4 findings at once,
              * and collapsing them to one used to make the other two
              * invisible without any sign they existed. */}
            <ul className="mt-1.5 space-y-2">
              {selected.refs.map((ref, index) => (
                <li
                  key={`${ref.checkId}-${ref.finding.code}-${index}`}
                  className={index > 0 ? "border-t border-slate-200 pt-1.5" : undefined}
                >
                  <span
                    className={`rounded-full border px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                      ref.tier === "refuse" ? "border-rose-300 bg-rose-50 text-rose-900" : "border-amber-300 bg-amber-50 text-amber-900"
                    }`}
                  >
                    {ref.checkId} · {ref.tier === "refuse" ? t("거부 등급", "refuse", "拒否") : t("경고 등급", "warn", "警告")}
                  </span>
                  <p className="mt-1 font-semibold text-slate-800">{ref.checkTitle}</p>
                  <p className="mt-0.5 font-mono text-[10.5px] text-slate-500">{ref.finding.code}</p>
                  <p className="mt-0.5 text-slate-500">{ref.finding.subject}</p>
                  <p className="mt-1 leading-relaxed">{ref.finding.message}</p>
                </li>
              ))}
            </ul>
          </>
        )}
        {selected?.kind === "excluded_zone" && (
          <>
            <div className="flex items-center justify-between">
              <span className="rounded-full border border-slate-300 bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-slate-700">
                {t("제외된 구역", "Excluded zone", "除外されたゾーン")}
              </span>
              <button type="button" onClick={onClear} className="text-slate-400">
                {t("닫기", "Close", "閉じる")}
              </button>
            </div>
            <p className="mt-1.5 font-semibold text-slate-800">{selected.zone.representative}</p>
            <p className="mt-1 font-mono text-[10.5px] text-slate-500">{selected.zone.reason}</p>
            <p className="mt-1 leading-relaxed">{selected.zone.detail}</p>
            <p className="mt-1.5">
              {t("링크", "Links", "リンク")} {selected.zone.links} · {t("OSM way", "OSM ways", "OSM way")} {selected.zone.ways}
            </p>
            <p className="mt-1">{selected.zone.street_names.join(", ")}</p>
            <p className="mt-1.5 rounded border border-slate-300 bg-white px-1.5 py-1 text-[10px] text-slate-500">
              {t(
                "이 구역은 최종 좌표가 확정되기 전에 제외되어, 지도에 그릴 좌표가 없습니다.",
                "This zone was excluded before the final coordinates were fixed -- it has no coordinates left to draw.",
                "このゾーンは最終座標が確定する前に除外されたため、地図に描く座標がありません。",
              )}
            </p>
          </>
        )}
      </div>

      {excludedZones.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white px-3 py-2.5">
          <p className="text-[11px] font-bold text-slate-800">
            {t("제외된 구역", "Excluded zones", "除外されたゾーン")} ({excludedZones.length})
          </p>
          <p className="mt-0.5 text-[10px] text-slate-500">
            {t(
              "지도에 좌표가 없어 목록으로만 보여줍니다 — 이름을 누르면 사유가 나타납니다.",
              "No coordinates survive for these -- listed by name only; click one for the reason.",
              "座標が残っていないため一覧のみで表示します — 名前を押すと理由が表示されます。",
            )}
          </p>
          <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto">
            {excludedZones.map((zone, index) => (
              <li key={`${zone.representative}-${index}`}>
                <button
                  type="button"
                  onClick={() => onSelectExcludedZone(zone)}
                  className="w-full truncate rounded border border-slate-200 bg-slate-50 px-2 py-1 text-left text-[10.5px] text-slate-700"
                  title={zone.street_names.join(", ")}
                >
                  {zone.representative} · {zone.links}
                  {t("개 링크", " links", "リンク")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {unresolvedCount > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11px] text-amber-900">
          <p className="font-semibold">
            {t(
              `지도에 표시하지 못한 발견 ${unresolvedCount}건`,
              `${unresolvedCount} finding(s) could not be placed on the map`,
              `地図に表示できなかった発見 ${unresolvedCount} 件`,
            )}
          </p>
          <p className="mt-0.5 text-[10px] opacity-80">
            {t(
              "게이트 결과가 이 항목들의 위치 참조를 전달하지 않습니다 — 위 판정 목록에서 문구는 그대로 확인할 수 있습니다.",
              "The gate result does not carry a location reference for these -- their wording is still in the check list above.",
              "ゲート結果にはこれらの位置参照が含まれていません — 上の判定リストで文言は確認できます。",
            )}
          </p>
          <ul className="mt-1.5 max-h-32 space-y-1 overflow-y-auto">
            {unresolved.map((ref, index) => (
              <li key={`${ref.checkId}-${ref.finding.code}-${index}`}>
                <button
                  type="button"
                  onClick={() => onSelectUnresolved(ref)}
                  className="w-full truncate rounded border border-amber-200 bg-white px-2 py-1 text-left text-[10.5px]"
                >
                  {ref.checkId} · {ref.finding.subject}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

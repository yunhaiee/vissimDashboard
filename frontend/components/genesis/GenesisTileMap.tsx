"use client";

// U-1-map (docs/ux-backlog.md, the user's detailed elaboration item #1) —
// "GateFindingsMap ↔ dashboard-map parity: bare local-coords SVG is illegible,
// no basemap."
//
// This replaces `GateFindingsMap` as the primary. The gate's refuse/warn
// findings and the generator's excluded zones are drawn on the SAME CARTO dark
// basemap `DashboardMap`, `RegionLassoMap`, `BaseValidationMap`,
// `IntersectionCompareMap` and `MapViewer` already use, over the WGS84 the
// SERVER computed from the draft's own recorded projection origin
// (`GET /api/genesis/jobs/{id}/artifact/geojson`). So a refused generation's
// reason is a PLACE a consultant recognises, not a shape floating in a grey box.
//
// WHY TWO FETCHES. The GeoJSON says where the roads are; the census
// (`genesis_json`) carries what a map must still show and geometry cannot
// imply — the excluded zones (named, with no coordinates left to draw) and the
// retained isolated zones one finding expands into — plus the id/name namespace
// `resolveFindingHighlight` matches a finding's subject against. They are
// fetched together in ONE effect and the panel is drawn only when both are in.
//
// WHAT THIS PANEL DOES NOT DO: geometry-diff two networks (benchmark.py's
// domain — see the comparison section beside it), and it does not invent a
// location for a finding it cannot place, nor an origin for a draft that
// recorded none. A finding whose `subject` matches nothing in the artifact is
// still LISTED in its own "위치를 찾지 못한 항목" section; a draft with no
// recorded origin falls back to `DraftLocalMap`'s local-coordinate canvas with
// the reason named on screen. The map never renders as a silent blank: no
// artifact, an empty network and a load failure each get their own message.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { LayerGroup, Map as LeafletMap } from "leaflet";

import { useLanguage } from "@/components/LanguageProvider";
import DraftLocalMap from "@/components/genesis/DraftLocalMap";
import {
  CONNECTOR_IDENTITY_DASH,
  EmptyState,
  FindingsDetailPanel,
  LegendToggle,
  NORMAL_CONNECTOR,
  NORMAL_LINK,
  REFUSE_COLOR,
  SELECTED_COLOR,
  WARN_COLOR,
  WARN_DASH,
  buildHighlights,
  collectFindingRefs,
  selectedHighlightOf,
  type SelectedItem,
} from "@/components/genesis/findings";
import { InfoTip } from "@/components/genesis/ui";
import type { GenesisGateReport } from "@/lib/api";
import {
  fetchGenesisDraftGeoJson,
  fetchGenesisDraftNetwork,
  genesisElementLaneOffsetsM,
  genesisElementWidthMeters,
  type GenesisDraftConnector,
  type GenesisDraftLink,
  type GenesisDraftNetwork,
  type GenesisGeoJson,
} from "@/lib/genesisArtifacts";
import {
  LANE_DASH_M,
  LANE_DASH_STROKE_M,
  LANE_DASH_STROKE_MIN_PX,
  LANE_GAP_M,
  MARKING_COLOR,
  MIN_ROAD_PX,
  offsetPolyline,
} from "@/lib/roadWidth";

/** The same CARTO dark basemap every other map in this app draws. */
const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";

// U-19 round 2 -- metres -> on-screen pixels at a given zoom/latitude, so a
// leaflet stroke weight can be "this many real metres wide" instead of a flat
// number. Standard Web Mercator ground-resolution formula (the same one
// Bing/Google/Leaflet's own EPSG:3857 CRS is built on): the world is
// `256 * 2^zoom` px wide at that zoom, and `2 * PI * EARTH_RADIUS_M` metres
// around at the equator, narrowing by `cos(lat)` away from it.
// `EARTH_RADIUS_M` is leaflet's own constant (`L.CRS.Earth.R`), not a
// separately-sourced number.
const EARTH_RADIUS_M = 6378137;
function metersPerPixel(latDeg: number, zoom: number): number {
  return (2 * Math.PI * EARTH_RADIUS_M * Math.cos((latDeg * Math.PI) / 180)) / (256 * Math.pow(2, zoom));
}

/** The map's own height inside the drawer. The user's own number: at ≥360px a
 * junction and its approaches are both legible at a usable zoom; below that the
 * basemap is a strip. Kept above that floor with room to spare. */
const MAP_HEIGHT = 420;

/** Unit #41 -- a selection asked for from OUTSIDE the map (a finding row
 * clicked in the verdict rail beside it, or a candidate link picked inside an
 * attestation card). Carried as a numbered REQUEST rather than as a controlled
 * value on purpose: the map still OWNS its selection state -- every click on
 * the basemap writes it directly, with no round trip through a parent -- and a
 * request only ever pushes into it. The `seq` is what makes clicking the SAME
 * row twice re-focus instead of no-op. */
export type GenesisMapSelectionRequest = { seq: number; item: SelectedItem };

export default function GenesisTileMap({
  jobId,
  report,
  hasArtifact,
  selectionRequest = null,
  onPlaceableChange,
}: {
  jobId: string;
  report: GenesisGateReport | null;
  /** Whether this job's record carries a network artifact at all -- a job that
   * errored before a network was ever drafted has nothing to fetch, and the
   * panel must SAY that rather than attempt a fetch that can only 404. */
  hasArtifact: boolean;
  selectionRequest?: GenesisMapSelectionRequest | null;
  /** The set of `findingKeyOf` values whose subject actually resolved to
   * geometry, published upward so the verdict rail can offer a map jump ONLY
   * on rows that have somewhere to jump to. The census this is derived from is
   * fetched here and nowhere else, which is why it travels out rather than the
   * rail deriving its own answer. */
  onPlaceableChange?: (placeable: ReadonlySet<string>) => void;
}) {
  const { t } = useLanguage();

  const [network, setNetwork] = useState<GenesisDraftNetwork | null>(null);
  const [geo, setGeo] = useState<GenesisGeoJson | null>(null);
  /** Why the basemap is not available for THIS generation, in the server's own
   * words. Not an error: the census loaded and the shape is still drawn — this
   * is the sentence that says the map underneath it is missing and why. */
  const [geoRefusal, setGeoRefusal] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedItem>(null);
  const [showNormal, setShowNormal] = useState(true);
  const [showRefuse, setShowRefuse] = useState(true);
  const [showWarn, setShowWarn] = useState(true);

  // Reset everything the moment the job changes -- a stale network under a new
  // job's findings would silently mislabel every highlight.
  useEffect(() => {
    setNetwork(null);
    setGeo(null);
    setGeoRefusal(null);
    setLoadError(null);
    setSelected(null);
  }, [jobId]);

  useEffect(() => {
    // `loadError` MUST be in this guard: without it, a failed fetch re-fires
    // the effect, which clears its own error and retries forever -- the user
    // sees an eternal "loading" instead of the named failure. Found live
    // 2026-08-12 (logged-out user, auth-required artifact fetch). The error
    // state is terminal for this job; switching jobs resets it above.
    //
    // `loading` must NOT be a dependency of this effect, because the effect is
    // what sets it: with `loading` in deps, `setLoading(true)` re-fires the
    // effect, and React runs the *previous* run's cleanup first, cancelling the
    // fetch that same call just started. Measured against react-dom 19.2.4: a
    // near-instant response (~<2 ms, cache/loopback) could lose the race
    // outright -- every write discarded, `loading` stuck `true` forever;
    // anything slower survived but paid for a wasted duplicate fetch whose
    // first result was thrown away. Removing the dependency is race-free at
    // every delay tested, including 0-1 ms. Found live 2026-08-12; re-verified
    // empirically the same day.
    if (network || loadError !== null || !hasArtifact) return;
    let cancelled = false;
    setLoading(true);
    // The census decides whether this panel can render at all, so its failure
    // is THE failure. The GeoJSON's failure is a DEGRADATION, not an error:
    // caught here and turned into a named reason, so a draft the server refuses
    // to place still shows its shape and its findings.
    Promise.all([
      fetchGenesisDraftNetwork(jobId),
      fetchGenesisDraftGeoJson(jobId).catch((caught) => {
        if (!cancelled) {
          setGeoRefusal(caught instanceof Error ? caught.message : String(caught));
        }
        return null;
      }),
    ])
      .then(([census, geojson]) => {
        if (cancelled) return;
        setNetwork(census);
        setGeo(geojson);
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
  }, [network, loadError, hasArtifact, jobId]);

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
  const highlights = useMemo(() => buildHighlights(findingRefs, network), [findingRefs, network]);
  const selectedHighlight = useMemo(
    () => selectedHighlightOf(selected, network),
    [selected, network],
  );

  // Unit #41 -- publish "which findings can be placed" the moment the census
  // that answers it is in. Before this the rail had no way to know, so it
  // offered no map jump at all and a reader who clicked a finding got nothing
  // -- the bug this unit was opened for.
  useEffect(() => {
    onPlaceableChange?.(highlights.placeable);
  }, [highlights, onPlaceableChange]);

  const excludedZones = network?.metadata.connectivity?.excluded_zones ?? [];

  // U-19: clicking a link/connector selects THAT ELEMENT, whether or not it
  // carries a finding -- every link/connector on the map is clickable now,
  // not only ones a finding already highlighted. `highlights.byLink`/
  // `byConnector` is EVERY finding whose subject resolved to this element via
  // `resolveFindingHighlight` (through `buildHighlights`); reading it here
  // rather than re-matching a subject against this id/name is what keeps this
  // the same one resolver `selectedHighlightOf` uses in the other direction
  // (finding -> geometry). A link with no finding legitimately gets `refs: []`
  // -- the detail panel says so rather than refusing to select it.
  const handleSelectLink = useCallback(
    (linkId: number) => {
      const refs = highlights.byLink.get(linkId) ?? [];
      const link = linksById.get(linkId);
      setSelected({ kind: "element", elementKind: "link", id: linkId, name: link?.name ?? "", refs });
    },
    [highlights, linksById],
  );
  const handleSelectConnector = useCallback(
    (connectorId: number) => {
      const refs = highlights.byConnector.get(connectorId) ?? [];
      const connector = connectorsById.get(connectorId);
      setSelected({ kind: "element", elementKind: "connector", id: connectorId, name: connector?.name ?? "", refs });
    },
    [highlights, connectorsById],
  );
  // ONE clear function, shared by the detail panel's 닫기 button and both
  // renderers' "clicked empty map" handler -- not three copies of
  // `setSelected(null)` that could drift.
  const handleClearSelection = useCallback(() => setSelected(null), []);

  // Unit #41 -- an outside request pushes into THIS component's own state.
  // Keyed on the request's `seq` alone: re-running on the item's identity
  // would re-fire on every parent render and stamp over a click the reader
  // just made on the map itself.
  //
  // An ELEMENT request is re-resolved through the map's OWN handlers rather
  // than taken at face value: the caller (a fix card's "지도에서 대상 링크
  // 보기") knows an id and nothing else, and the name and the finding list a
  // click on that same link would show live in the census, which is fetched
  // here. Routing it through the same handler is what stops an externally
  // selected link from rendering as a nameless element with no findings.
  const requestSeq = selectionRequest?.seq ?? 0;
  // Both the pending request AND the two resolvers are read through refs, so
  // the effect's ONLY dependency is the sequence number: with the handlers in
  // the dependency array, the census arriving (which rebuilds them) would
  // re-apply the last request and stamp over whatever the reader had clicked
  // since.
  const pendingRef = useRef<{ item: SelectedItem; selectLink: (id: number) => void; selectConnector: (id: number) => void }>({
    item: null,
    selectLink: handleSelectLink,
    selectConnector: handleSelectConnector,
  });
  pendingRef.current = {
    item: selectionRequest?.item ?? null,
    selectLink: handleSelectLink,
    selectConnector: handleSelectConnector,
  };
  useEffect(() => {
    if (requestSeq <= 0) return;
    const { item, selectLink, selectConnector } = pendingRef.current;
    if (item?.kind === "element") {
      if (item.elementKind === "link") selectLink(item.id);
      else selectConnector(item.id);
      return;
    }
    setSelected(item);
  }, [requestSeq]);

  const header = (
    <div className="flex items-center justify-between gap-3">
      {/* The word 지도 left the screen (user, live pass 2026-08-14) -- the map
        * says it is a map. It stays for screen readers, and the ⓘ stays
        * visible because WHICH coordinate system this is (WGS84 vs local) is
        * state a reader of the numbers needs. */}
      <h3 className="flex items-center text-[15px] font-semibold text-slate-900">
        <span className="sr-only">{t("지도", "Map", "地図")}</span>
        <InfoTip
          text={
            geo
              ? t(
                  "이 생성의 좌표는 서버가 초안이 기록한 투영 원점으로 되돌려 계산한 WGS84입니다 — 초안이 OpenStreetMap 좌표에서 만들어졌기 때문에 가능한 정확한 역변환이며, 별도의 측량 정합이 아닙니다.",
                  "These coordinates are WGS84, computed on the server by inverting the projection origin the draft itself recorded -- an exact inverse, available because the draft was built FROM OpenStreetMap degrees, and not a survey fit.",
                  "この生成の座標は、下書きが記録した投影原点をサーバーが逆変換して求めた WGS84 です — 下書きが OpenStreetMap の座標から作られているため可能な厳密な逆変換であり、測量による整合ではありません。",
                )
              : t(
                  "링크는 초안 네트워크(genesis_json)의 좌표 그대로입니다 — 배경지도가 아니라 이 생성의 로컬 좌표계입니다.",
                  "Links are drawn from the draft network's own coordinates (genesis_json) -- there is no basemap; this is the generation's own local coordinate system.",
                  "リンクは下書きネットワーク(genesis_json)自体の座標です — 背景地図ではなく、この生成のローカル座標系です。",
                )
          }
        />
      </h3>
    </div>
  );

  if (!hasArtifact) {
    return (
      <div>
        {header}
        <div className="mt-3">
          <EmptyState
            text={t(
              "이 생성은 네트워크를 만들지 못했습니다 — 지도로 보여줄 지오메트리가 없습니다.",
              "This generation never produced a network -- there is no geometry to show.",
              "この生成はネットワークを作成できませんでした — 地図で表示できる形状がありません。",
            )}
          />
        </div>
      </div>
    );
  }

  return (
    <div>
      {header}
      <div className="mt-3">
        {loading && (
          <div className="flex h-[260px] items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 text-[15px] text-slate-500">
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
        {!loading && !loadError && network && network.links.length > 0 && (
          <div className="flex flex-col gap-3">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[14px] text-slate-600">
                <LegendToggle
                  color={NORMAL_LINK}
                  label={t("일반", "Normal", "通常")}
                  checked={showNormal}
                  onChange={setShowNormal}
                />
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
                  dashed
                  onChange={setShowWarn}
                />
              </div>

              {geo ? (
                <FindingsTileLayer
                  geo={geo}
                  linksById={linksById}
                  connectorsById={connectorsById}
                  highlights={highlights}
                  selectedHighlight={selectedHighlight}
                  selectedPoint={selected?.kind === "finding" ? selected.point ?? null : null}
                  showNormal={showNormal}
                  showRefuse={showRefuse}
                  showWarn={showWarn}
                  onSelectLink={handleSelectLink}
                  onSelectConnector={handleSelectConnector}
                  onClearSelection={handleClearSelection}
                />
              ) : (
                <>
                  {/* THE HONEST FALLBACK. Not a spinner and not a blank: the
                    * shape is still drawn, in the generation's own metres, and
                    * the sentence above it says the basemap is missing and
                    * quotes the server's reason. Substituting an origin here
                    * would put the whole network in the wrong place with no
                    * sign anything was wrong. */}
                  <p className="mb-2 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-[15px] text-slate-700">
                    {t(
                      "배경지도 없이 이 생성의 로컬 좌표로만 그립니다 — 서버가 이 초안을 지도 위에 올리지 못했습니다.",
                      "Drawn in this generation's own local coordinates, with no basemap -- the server could not place this draft on the earth.",
                      "背景地図なしに、この生成のローカル座標のみで描画しています — サーバーがこの下書きを地図上に配置できませんでした。",
                    )}
                    {geoRefusal && (
                      <span className="mt-1 block font-mono text-[14px] text-slate-500">{geoRefusal}</span>
                    )}
                  </p>
                  <DraftLocalMap
                    network={network}
                    linksById={linksById}
                    connectorsById={connectorsById}
                    highlights={highlights}
                    selectedHighlight={selectedHighlight}
                    showNormal={showNormal}
                    showRefuse={showRefuse}
                    showWarn={showWarn}
                    onSelectLink={handleSelectLink}
                    onSelectConnector={handleSelectConnector}
                    onClearSelection={handleClearSelection}
                    t={t}
                  />
                </>
              )}
            </div>

            <FindingsDetailPanel
              selected={selected}
              onClear={handleClearSelection}
              unresolvedCount={highlights.unresolved.length}
              unresolved={highlights.unresolved}
              onSelectUnresolved={(ref) => setSelected({ kind: "finding", refs: [ref] })}
              excludedZones={excludedZones}
              onSelectExcludedZone={(zone) => setSelected({ kind: "excluded_zone", zone })}
              t={t}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The leaflet half: CARTO tiles, and one polyline per feature in the gate's own
 * colours.
 *
 * Leaflet is loaded with `await import("leaflet")` INSIDE the mount effect, the
 * shape every other map in this app uses: leaflet touches `window` at module
 * scope, so a static import breaks the server render even inside a client
 * component.
 *
 * DRAW ORDER IS THE CLAIM ORDER — normal, then warn, then refuse, then the
 * selection — so a link carrying both a refuse and a warn finding shows the
 * stronger one, and the clicked element is never hidden under a neighbour.
 */
function FindingsTileLayer({
  geo,
  linksById,
  connectorsById,
  highlights,
  selectedHighlight,
  selectedPoint,
  showNormal,
  showRefuse,
  showWarn,
  onSelectLink,
  onSelectConnector,
  onClearSelection,
}: {
  geo: GenesisGeoJson;
  /** U-19 round 2: the census network's own lane/width fields, already
   * loaded client-side for round 1's name lookups -- reused here rather than
   * re-fetched or re-derived, since a geojson feature's properties carry only
   * `id`/`kind`/`name` (services/genesis/geojson.py's contract) and were never
   * meant to duplicate what the census already carries. */
  linksById: Map<number, GenesisDraftLink>;
  connectorsById: Map<number, GenesisDraftConnector>;
  highlights: ReturnType<typeof buildHighlights>;
  selectedHighlight: { linkIds: number[]; connectorIds: number[] } | null;
  /** Unit #41 -- ONE measured point to ring and centre on (a V3 testimony's own
   * `source_testimony.coordinates`). Null for every finding whose payload
   * carried no coordinate: no marker at all is the honest answer, and a pin
   * dropped at a link end this file guessed would be a location the product
   * invented. */
  selectedPoint: { lat: number; lon: number; label: string } | null;
  showNormal: boolean;
  showRefuse: boolean;
  showWarn: boolean;
  onSelectLink: (linkId: number) => void;
  onSelectConnector: (connectorId: number) => void;
  /** Clicking the empty map (not any feature) clears the selection -- the
   * click listener is bound to the map itself in the mount effect below, and
   * every feature's own click handler stops propagation so selecting a
   * feature does not immediately clear itself. */
  onClearSelection: () => void;
}) {
  const { t } = useLanguage();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const layerRef = useRef<LayerGroup | null>(null);
  const [ready, setReady] = useState(false);
  // Bumped on every "zoomend" (mount effect below) purely to RE-TRIGGER the
  // draw effect -- the actual zoom/centre are always read fresh off the map
  // instance inside that effect, never off this counter's value, so there is
  // no risk of it going stale between the event and the redraw.
  const [zoomTick, setZoomTick] = useState(0);

  // The click handlers are read through a ref so the drawing effect below does
  // not have to re-run (and re-add every polyline) when a parent render hands
  // it new function identities.
  const handlers = useRef({ onSelectLink, onSelectConnector, onClearSelection });
  handlers.current = { onSelectLink, onSelectConnector, onClearSelection };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;

    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !containerRef.current) return;

      const container = containerRef.current;
      (container as { _leaflet_id?: number | null })._leaflet_id = null;
      container.innerHTML = "";

      const map = L.map(container, { zoomControl: true, preferCanvas: true });
      mapRef.current = map;
      map.setView([geo.origin.lat, geo.origin.lon], 15);

      L.tileLayer(TILE_URL, {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org">OSM</a> &copy; <a href="https://carto.com">CARTO</a>',
        maxZoom: 20,
      }).addTo(map);

      // Clicking the empty map (basemap, not a feature) clears the selection.
      // A feature's own click handler (below) calls `L.DomEvent.stopPropagation`
      // first, so this only fires for a click that hit no polyline at all.
      map.on("click", () => handlers.current.onClearSelection());

      // U-19 round 2: road width is metres-converted-to-pixels AT THE CURRENT
      // ZOOM, so every zoom level needs its own redraw -- without this a
      // road's weight would freeze at whatever pixel count it was drawn with
      // on the very first render and stop tracking the zoom the user is
      // actually looking at.
      map.on("zoomend", () => setZoomTick((current) => current + 1));

      if (!cancelled) setReady(true);
    })();

    return () => {
      cancelled = true;
      layerRef.current?.remove();
      layerRef.current = null;
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
    // The origin is an initial view only; re-centring on every parent render
    // would fight the user's own panning.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;

    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || mapRef.current !== map) return;
      layerRef.current?.remove();
      layerRef.current = null;

      const layer = L.layerGroup();
      const selectedLinks = new Set(selectedHighlight?.linkIds ?? []);
      const selectedConnectors = new Set(selectedHighlight?.connectorIds ?? []);
      // Recomputed every redraw (this effect depends on `zoomTick`, bumped by
      // the mount effect's "zoomend" listener) off the map's OWN current zoom
      // and centre latitude -- never off a value captured once at mount.
      const pxPerMeter = 1 / metersPerPixel(map.getCenter().lat, map.getZoom());

      // Four passes, weakest claim first, so leaflet's own insertion order is
      // the z-order and the strongest claim ends up on top.
      const passes: Array<"normal" | "warn" | "refuse" | "selected"> = [
        "normal",
        "warn",
        "refuse",
        "selected",
      ];
      for (const pass of passes) {
        for (const feature of geo.features) {
          const id = feature.properties.id;
          const isConnector = feature.properties.kind === "connector";
          const refuse = isConnector
            ? highlights.refuseConnectors.has(id)
            : highlights.refuseLinks.has(id);
          const warn = isConnector ? highlights.warnConnectors.has(id) : highlights.warnLinks.has(id);
          const isSelected = isConnector ? selectedConnectors.has(id) : selectedLinks.has(id);

          if (pass === "normal" && (!showNormal || refuse || warn)) continue;
          if (pass === "warn" && (!showWarn || !warn || refuse)) continue;
          if (pass === "refuse" && (!showRefuse || !refuse)) continue;
          if (pass === "selected" && !isSelected) continue;

          const color =
            pass === "selected"
              ? SELECTED_COLOR
              : pass === "refuse"
                ? REFUSE_COLOR
                : pass === "warn"
                  ? WARN_COLOR
                  : isConnector
                    ? NORMAL_CONNECTOR
                    : NORMAL_LINK;
          // U-19 round 2: the "normal" tier draws a WIDTH-SCALED ribbon (real
          // lane count x lane width, converted to on-screen px at the current
          // zoom, floored so a 1-lane road stays a real click target) instead
          // of a flat 1px/2px line -- visual parity with the dashboard/
          // network tab's road ribbons. refuse/warn/selected keep their
          // EXISTING fixed weights unchanged; only the base ribbon changed.
          // HONESTY: if the census carries no matching element for this
          // geojson feature (should not happen -- both come from the same
          // generation -- but the census fetch and the geojson fetch ARE two
          // independent requests, see this file's header comment), width is
          // 0 and Math.max floors it to MIN_ROAD_PX rather than inventing a
          // plausible-looking width.
          const element = isConnector ? connectorsById.get(id) : linksById.get(id);
          const widthMeters = element ? genesisElementWidthMeters(element) : 0;
          const weight =
            pass === "selected"
              ? 6
              : pass === "refuse"
                ? 5
                : pass === "warn"
                  ? 4
                  : Math.max(widthMeters * pxPerMeter, MIN_ROAD_PX);

          // GeoJSON is [lon, lat]; leaflet is [lat, lon]. Flipped explicitly at
          // the one boundary where the two meet.
          const latlngs = feature.geometry.coordinates.map(([lon, lat]) => [lat, lon] as [number, number]);
          const line = L.polyline(
            latlngs,
            {
              color,
              weight,
              // The warn tier is mono now (user decision, 2026-08-13): the dash
              // is what tells it apart from an ordinary link, so it is not
              // decoration and must stay in step with `DraftLocalMap`'s.
              dashArray: pass === "warn" ? WARN_DASH : undefined,
              opacity: pass === "normal" ? 0.7 : 0.95,
              lineCap: "round",
              lineJoin: "round",
              // U-19: EVERY feature is clickable now, not only a refuse/warn
              // one -- "clicking a link/connector selects it" means every
              // link/connector. `interactive: true` is also what makes
              // leaflet's canvas renderer switch the cursor to a pointer on
              // hover (`leaflet-interactive`, Canvas.js's `_handleMouseHover`
              // toggles that class on the container), so no separate CSS
              // hover rule is needed for the pointer cursor.
              interactive: true,
            },
          );
          line.on("click", (event) => {
            // Leaflet's Path layers bubble their click to the map by default
            // (`bubblingMouseEvents: true`) -- without stopping it here, every
            // selecting click would ALSO hit the map's own click-to-clear
            // handler registered in the mount effect above, clearing the
            // selection the same click just set.
            L.DomEvent.stopPropagation(event);
            const current = handlers.current;
            if (isConnector) current.onSelectConnector(id);
            else current.onSelectLink(id);
          });
          line.addTo(layer);

          // Connector identity cue (U-19 round 2) -- mirrors NetworkViewer.
          // tsx's Layer 4: a thin dashed centreline over a NORMAL-tier
          // connector's own full-width pavement (drawn above, same width as a
          // link -- "Connectors use the SAME width rule and floor as links",
          // that file's own comment), not a narrower ribbon. Only the normal
          // tier: a warn/refuse/selected connector already carries its own
          // dash/weight vocabulary, and layering a second dash rhythm on top
          // would read as a third, undocumented state. Non-interactive so the
          // click target stays the wide ribbon underneath.
          if (pass === "normal" && isConnector) {
            L.polyline(latlngs, {
              color: NORMAL_LINK,
              weight: 1,
              dashArray: CONNECTOR_IDENTITY_DASH,
              opacity: 0.55,
              lineCap: "round",
              lineJoin: "round",
              interactive: false,
            }).addTo(layer);
          }

          // Lane dividers (unit #29, "port lane markings to the genesis
          // draft maps") -- interior lane BOUNDARIES only, mirroring
          // NetworkViewer.tsx's own Layer 3 (edge lines are intentionally
          // omitted; the ribbon's own edge already reads as the road edge).
          // LINKS only, matching NetworkViewer -- a connector already
          // carries its own CONNECTOR_IDENTITY_DASH cue just above, and a
          // second dash rhythm on top of that would read as a third,
          // undocumented state.
          //
          // READABILITY GATE, not a new clamp: drawn only when `weight`
          // (this ribbon's own on-screen width, computed above) is still
          // the link's real metre-true width and not the MIN_ROAD_PX floor
          // -- below that floor the ribbon is already wider on screen than
          // its own geometry supports, so an interior line computed from
          // that same geometry has nothing honest to sit inside. Reuses
          // MIN_ROAD_PX itself rather than a second, independently-chosen
          // number: the two floors describe the SAME regime, "this
          // ribbon's pixels no longer represent real metres."
          if (pass === "normal" && !isConnector && element && weight > MIN_ROAD_PX) {
            const laneOffsetsM = genesisElementLaneOffsetsM(element);
            if (laneOffsetsM.length > 0) {
              // map.project/unproject work in the SAME Web Mercator pixel
              // space `metersPerPixel` above approximates, at the map's
              // CURRENT zoom -- so offsetPolyline's px-space offsetting (the
              // ONE offset helper this codebase has, imported from
              // lib/roadWidth.ts rather than reimplemented) and the
              // divider's re-projection back to lat/lng both track live
              // zoom exactly like `weight` itself does (recomputed on every
              // "zoomend" redraw, see this effect's own header comment) --
              // not frozen at a fit scale the way DraftLocalMap's SVG
              // fallback has to be.
              const zoom = map.getZoom();
              const projected: [number, number][] = latlngs.map((ll) => {
                const p = map.project(ll, zoom);
                return [p.x, p.y];
              });
              for (const offM of laneOffsetsM) {
                const dividerLatLngs = offsetPolyline(projected, offM * pxPerMeter).map(([x, y]) =>
                  map.unproject([x, y], zoom),
                );
                L.polyline(dividerLatLngs, {
                  color: MARKING_COLOR,
                  weight: Math.max(LANE_DASH_STROKE_M * pxPerMeter, LANE_DASH_STROKE_MIN_PX),
                  dashArray: `${LANE_DASH_M * pxPerMeter} ${LANE_GAP_M * pxPerMeter}`,
                  opacity: 0.55,
                  lineCap: "round",
                  lineJoin: "round",
                  interactive: false,
                }).addTo(layer);
              }
            }
          }
        }
      }

      // Unit #41 -- THE DEPARTURE POINT, drawn last so it sits over its own
      // road. Two rings rather than a leaflet default marker: the default icon
      // is a PNG this app does not ship, and a circle in the selection hue is
      // the same vocabulary the selected polyline already speaks. Drawn ONLY
      // from a coordinate the payload measured (see the prop's own comment) --
      // there is no fallback pin.
      if (selectedPoint) {
        L.circleMarker([selectedPoint.lat, selectedPoint.lon], {
          radius: 11,
          color: SELECTED_COLOR,
          weight: 2,
          fill: false,
          opacity: 0.9,
          interactive: false,
        }).addTo(layer);
        L.circleMarker([selectedPoint.lat, selectedPoint.lon], {
          radius: 3.5,
          color: SELECTED_COLOR,
          fillColor: SELECTED_COLOR,
          fillOpacity: 1,
          weight: 0,
          interactive: false,
        }).addTo(layer);
      }

      if (cancelled || mapRef.current !== map) {
        layer.remove();
        return;
      }
      layer.addTo(map);
      layerRef.current = layer;
    })();

    return () => {
      cancelled = true;
    };
  }, [ready, geo, linksById, connectorsById, highlights, selectedHighlight, selectedPoint, showNormal, showRefuse, showWarn, zoomTick]);

  // Unit #41 -- BRING THE SELECTION INTO VIEW. A finding clicked in the rail
  // is useless if its road is off screen at the current pan, and this is the
  // half of "클릭 → 지도 하이라이트" a colour change alone cannot deliver.
  // Only ever a `setView`/`fitBounds` on a NEW selection -- never on a redraw,
  // so the reader's own panning survives a zoom or a legend toggle.
  const focusKey = selectedPoint
    ? `p:${selectedPoint.lat},${selectedPoint.lon}`
    : `l:${(selectedHighlight?.linkIds ?? []).join(",")}|c:${(selectedHighlight?.connectorIds ?? []).join(",")}`;
  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || mapRef.current !== map) return;
      if (selectedPoint) {
        map.setView([selectedPoint.lat, selectedPoint.lon], Math.max(map.getZoom(), 17));
        return;
      }
      const linkIds = new Set(selectedHighlight?.linkIds ?? []);
      const connectorIds = new Set(selectedHighlight?.connectorIds ?? []);
      if (linkIds.size === 0 && connectorIds.size === 0) return;
      const points: [number, number][] = [];
      for (const feature of geo.features) {
        const chosen =
          feature.properties.kind === "connector"
            ? connectorIds.has(feature.properties.id)
            : linkIds.has(feature.properties.id);
        if (!chosen) continue;
        for (const [lon, lat] of feature.geometry.coordinates) points.push([lat, lon]);
      }
      if (points.length === 0) return;
      map.fitBounds(L.latLngBounds(points), { padding: [60, 60], maxZoom: 18 });
    })();
    return () => {
      cancelled = true;
    };
    // `focusKey` is the whole dependency on purpose: it changes exactly when
    // the SELECTED geometry does, and re-running on `selectedHighlight`'s
    // object identity would re-centre the map on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, focusKey]);

  const fit = useCallback(async () => {
    const map = mapRef.current;
    if (!map || !geo.bbox) return;
    const L = (await import("leaflet")).default;
    const [west, south, east, north] = geo.bbox;
    map.fitBounds(
      L.latLngBounds(
        [south, west],
        [north, east],
      ),
      { padding: [30, 30] },
    );
  }, [geo]);

  // Fit once the map and the geometry are both in -- opening on a 15-zoom
  // guess would leave a large network mostly off screen.
  useEffect(() => {
    if (ready) void fit();
  }, [ready, fit]);

  return (
    <div className="relative">
      <div
        ref={containerRef}
        role="application"
        aria-label={t("생성된 네트워크와 게이트 소견 지도", "Generated network and gate findings map", "生成ネットワークとゲート所見の地図")}
        className="w-full overflow-hidden rounded-lg border border-slate-300 bg-slate-800"
        style={{ height: MAP_HEIGHT }}
      />
      {geo.bbox && (
        <button
          type="button"
          onClick={() => void fit()}
          className="ui-button-ghost absolute bottom-2 right-2 z-[400] rounded-full bg-surface px-2.5 py-1 text-[14px]"
        >
          {t("전체 보기", "Fit network", "全体表示")}
        </button>
      )}
    </div>
  );
}

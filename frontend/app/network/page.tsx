"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import NetworkSearchBox, { type NetworkSearchSelection } from "@/components/NetworkSearchBox";
import TrajectoryLayer, { createProjectionStore, PLAYBACK_SPEEDS, useTrajectoryPlayback } from "@/components/TrajectoryLayer";
import VissimDatasetBanner from "@/components/VissimDatasetBanner";
import {
  fetchActiveDataset,
  fetchNetwork,
  fetchSimulationJobs,
  type ActiveDataset,
  type NetworkData,
  type SimulationJob,
} from "@/lib/api";
import { elementTypeLabel, linkKindLabel, type NetworkElementRef } from "@/lib/networkElements";
import {
  buildFocusFlash,
  focusSignature,
  nextFocusNonce,
  parseNetworkFocus,
  useFocusPulse,
  type FocusBbox,
  type FocusElementRef,
} from "@/lib/networkFocus";

// NetworkViewer draws with d3 straight against the DOM, so it must stay
// client-only (same reason DashboardMap/Leaflet is dynamically imported).
const NetworkViewer = dynamic(() => import("@/components/NetworkViewer"), { ssr: false });

const MAX_SELECTED_NODES = 6;

// Transport glyphs with U+FE0E (text presentation selector) so platforms that
// would otherwise substitute a colour emoji keep them as plain glyphs.
const PLAY_GLYPH = "▶︎";
const PAUSE_GLYPH = "⏸︎";

// m:ss for the scrubber readout. Sim clocks can start at a non-zero t0, so the
// caller passes an already-relative offset; absolute sim seconds are shown
// separately next to it.
function formatClock(seconds: number): string {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.round(seconds)) : 0;
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// Label for one entry in the playback-source picker -- job name (falls back
// to its id) plus a short local-time stamp so multiple runs are distinguishable.
function formatJobLabel(job: SimulationJob): string {
  const name = job.name || job.id;
  if (!job.completed_at) return name;
  const date = new Date(job.completed_at);
  if (Number.isNaN(date.getTime())) return name;
  return `${name} (${date.toLocaleString()})`;
}

// `useSearchParams` makes the client tree up to the nearest Suspense boundary
// client-rendered (Next 16 docs, app/api-reference/functions/use-search-params
// "Prerendering"), so the boundary lives here rather than being inherited from
// the app shell. Everything below already renders client-side anyway (the
// viewer is a `ssr: false` dynamic import), so the fallback is never seen for
// long enough to need chrome of its own.
export default function NetworkPage() {
  return (
    <Suspense fallback={null}>
      <NetworkPageContent />
    </Suspense>
  );
}

function NetworkPageContent() {
  const { t } = useLanguage();
  const [network, setNetwork] = useState<NetworkData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [datasetRevision, setDatasetRevision] = useState("default");
  const [reloadNonce, setReloadNonce] = useState(0);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedLinkInfo, setSelectedLinkInfo] = useState<{ id: string; name: string; isConnector: boolean } | null>(null);
  // P6: the one static element (signal head, detector, …) clicked in the
  // diagram, if any. Single-select — clicking another replaces it, clicking the
  // same one again clears it — same toggle semantics as the link selection.
  const [selectedElementInfo, setSelectedElementInfo] = useState<NetworkElementRef | null>(null);
  // P2: the floating assistant's current agent session id, mirrored here via
  // the "vissim:agent-session" event so this page's refresh button can show
  // that session's in-progress edited working copy on demand -- see
  // docs/phase2/DECISIONS.md's P2 entry.
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null);

  // P3/P4: vehicle-trajectory playback. `projection` is the only channel
  // between NetworkViewer's live zoom transform and the canvas overlay — it is
  // deliberately not React state, because zoom emits once per animation frame
  // and re-rendering this page that often would be wasteful. The playback hook
  // owns the clock and fetches lazily (nothing is requested until the user
  // presses 재생 준비).
  const projection = useMemo(() => createProjectionStore(), []);
  const playback = useTrajectoryPlayback();
  const playbackEngine = playback.engine;

  // Real-run replay: completed agent-run jobs that recorded a vehicle
  // trajectory (routers/simulations.py's _run_agent_simulation_job,
  // trajectory_recorded=true) and were run against the currently active
  // dataset -- selecting one replays that run's own recorded .fzp instead of
  // the default file-next-to-dataset/demo source. `null` = default source.
  const [replayableJobs, setReplayableJobs] = useState<SimulationJob[]>([]);
  const [trajectorySourceJobId, setTrajectorySourceJobId] = useState<string | null>(null);

  // --- Approval-card focus handoff (2026-07-28) --------------------------
  // The approval card's 네트워크에서 보기 button navigates here with the
  // pending write's focus in the URL (lib/networkFocus.ts). Reading it from
  // the URL rather than from an in-memory channel is what makes the landing
  // reload-safe and shareable; the ids are re-resolved against the network
  // geometry this page already fetches, so nothing extra is fetched.
  const searchParams = useSearchParams();
  const urlFocus = useMemo(() => parseNetworkFocus(searchParams), [searchParams]);
  // Carries the URL's per-click nonce (lib/networkFocus.ts's
  // FOCUS_NONCE_PARAM), which is what makes "same target, asked again" a
  // DIFFERENT key here — everything below is keyed off this, so re-asking
  // revives a dismissed highlight, restarts the pulse and re-flies the zoom
  // with no extra state of its own.
  const focusKey = focusSignature(urlFocus);
  // Which focus the user has already dismissed (by clicking something else in
  // the diagram, or the banner's own dismiss). Stored as the focus signature
  // so arriving with a DIFFERENT focus — or the same one re-requested —
  // revives the highlight.
  const [dismissedFocusKey, setDismissedFocusKey] = useState("");
  const activeFocus = urlFocus && dismissedFocusKey !== focusKey ? urlFocus : null;

  const focusKeyRef = useRef(focusKey);
  useEffect(() => {
    focusKeyRef.current = focusKey;
  }, [focusKey]);
  // Stable identity: the diagram click handlers below are NetworkViewer draw
  // dependencies, so they must not change when the focus does.
  const dismissFocus = useCallback(() => {
    setDismissedFocusKey(focusKeyRef.current || "none");
  }, []);

  // Pulse phase, keyed to the focus it belongs to — extracted to
  // lib/networkFocus.ts's useFocusPulse (2026-07-28) so the URL-sourced
  // approval focus below and the network-search selection further down share
  // the exact same "pulse during render, settle after FOCUS_PULSE_MS,
  // skip entirely under prefers-reduced-motion" state machine instead of two
  // independently-drifting copies of it.
  const focusPulsing = useFocusPulse(focusKey);
  const urlFocusFlash = useMemo(
    () => (activeFocus ? buildFocusFlash(activeFocus.elements, focusPulsing) : null),
    [activeFocus, focusPulsing],
  );

  const focusBanner = useMemo(() => {
    if (!activeFocus) return null;
    const conflict = activeFocus.elements.find((element) => element.type === "conflict_area");
    const links = activeFocus.elements.filter((element) => element.type === "link");
    if (conflict) {
      const pair = links
        .slice(0, 2)
        .map((link) => t(`링크 ${link.id}`, `link ${link.id}`, `リンク${link.id}`))
        .join(" ↔ ");
      const head = t(`충돌구간 ${conflict.id}`, `Conflict area ${conflict.id}`, `錯綜区間${conflict.id}`);
      return pair ? `${head} · ${pair}` : head;
    }
    if (links.length === 1) return t(`링크 ${links[0].id}`, `Link ${links[0].id}`, `リンク${links[0].id}`);
    const count = activeFocus.elements.length;
    return t(`대상 요소 ${count}개`, `${count} target element${count === 1 ? "" : "s"}`, `対象要素${count}個`);
  }, [activeFocus, t]);

  // --- Network search selection (2026-07-28) ------------------------------
  // A SEPARATE focus "slot" from the URL-sourced approval focus above — a
  // search selection is a same-page interactive action, not a navigation, so
  // it carries its own nonce/pulse rather than round-tripping through the URL
  // codec. Takes precedence over the URL focus whenever present (selecting a
  // search result is unambiguously "look here NOW"); selecting a result also
  // retires the URL focus via dismissFocus so the two banners never stack.
  const [searchSelection, setSearchSelection] = useState<{
    key: string;
    bbox: FocusBbox;
    elements: FocusElementRef[];
    label: string;
  } | null>(null);
  const searchPulsing = useFocusPulse(searchSelection?.key ?? "");
  const searchFocusFlash = useMemo(
    () => (searchSelection ? buildFocusFlash(searchSelection.elements, searchPulsing) : null),
    [searchSelection, searchPulsing],
  );

  const handleSearchSelect = useCallback(
    (selection: NetworkSearchSelection) => {
      dismissFocus();
      setSearchSelection({
        key: nextFocusNonce(),
        bbox: selection.bbox,
        elements: selection.elements,
        label: selection.label,
      });
    },
    [dismissFocus],
  );
  const clearSearchSelection = useCallback(() => setSearchSelection(null), []);

  // Combined values NetworkViewer actually receives: search wins over the URL
  // focus whenever a search selection is active.
  const focusFlash = searchSelection ? searchFocusFlash : urlFocusFlash;
  const zoomToBbox = searchSelection
    ? { ...searchSelection.bbox, nonce: searchSelection.key }
    : activeFocus
      ? { ...activeFocus.bbox, nonce: activeFocus.nonce }
      : null;

  useEffect(() => {
    let cancelled = false;
    // Fetches the active dataset independently rather than reading the
    // `activeDataset` state above: that state is only ever populated by
    // VissimDatasetBanner's `onDatasetChange` callback, which -- verified
    // in VissimDatasetBanner.tsx -- fires ONLY on an explicit upload/reset
    // action, never for the banner's own initial mount fetch. On a normal
    // page load (no upload this session) `activeDataset` would silently
    // stay null forever, disabling the revision filter below and showing
    // every recorded run regardless of which network it actually belongs
    // to -- the exact "wrong data displayed" risk this filter exists to
    // prevent.
    Promise.all([fetchSimulationJobs(), fetchActiveDataset().catch(() => null)])
      .then(([jobs, dataset]) => {
        if (cancelled) return;
        setReplayableJobs(
          jobs.filter(
            (job) =>
              job.status === "done" &&
              job.trajectory_recorded === true &&
              (!dataset || job.dataset_revision === dataset.revision),
          ),
        );
      })
      .catch(() => {
        if (!cancelled) setReplayableJobs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetRevision, reloadNonce]);

  // Trajectories belong to the active dataset, so swapping datasets drops the
  // loaded frames back to the load affordance rather than animating vehicles
  // over a network they were never resolved against.
  useEffect(() => {
    playbackEngine.reset();
    setTrajectorySourceJobId(null);
  }, [playbackEngine, datasetRevision]);

  const handleTrajectorySourceChange = useCallback(
    (jobId: string | null) => {
      setTrajectorySourceJobId(jobId);
      playbackEngine.reset();
      // Selecting a source loads it immediately (rather than requiring a
      // separate "load" click) -- the picker only appears once at least one
      // real run is available, so this only ever fires on a deliberate choice.
      playbackEngine.load(jobId ?? undefined);
    },
    [playbackEngine],
  );

  // Load the raw-coordinate network on mount, and again whenever the active
  // dataset changes or a retry is requested.
  useEffect(() => {
    let cancelled = false;
    setNetwork(null);
    setError(null);
    setLoading(true);
    fetchNetwork(agentSessionId ?? undefined)
      .then((data) => {
        if (!cancelled) setNetwork(data);
      })
      .catch(() => {
        if (!cancelled) setError(t("네트워크 도식을 불러오지 못했습니다.", "Failed to load the network diagram."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetRevision, reloadNonce, agentSessionId, t]);

  // P2: mirror the floating assistant's current agent session id -- see
  // FloatingAssistant.tsx's own "vissim:agent-session" dispatch effect and
  // app/dashboard/page.tsx's matching listener.
  useEffect(() => {
    function handleAgentSession(event: Event) {
      const detail = (event as CustomEvent<{ sessionId: string | null }>).detail;
      setAgentSessionId(detail?.sessionId ?? null);
    }

    window.addEventListener("vissim:agent-session", handleAgentSession);
    return () => {
      window.removeEventListener("vissim:agent-session", handleAgentSession);
    };
  }, []);

  const handleDatasetChange = useCallback((dataset?: ActiveDataset) => {
    setActiveDataset(dataset ?? null);
    setSelectedNodeIds([]);
    setSelectedLinkInfo(null);
    setSelectedElementInfo(null);
    // A search selection's bbox is in the OLD network's coordinate space --
    // keeping it across a dataset swap would fly the diagram to a location
    // that means nothing in the new one (same reason the selections above are
    // cleared, one step worse).
    setSearchSelection(null);
    setDatasetRevision(dataset?.revision ?? String(Date.now()));
  }, []);

  // Mirror the dashboard's handleNodeClick semantics: clicking toggles the node
  // in the comparison set, capped at six with the oldest dropped first.
  const handleNodeClick = useCallback((nodeId: string) => {
    // Selecting anything retires the approval focus AND any search
    // selection — "look here" has served its purpose the moment the user
    // starts driving the diagram themselves.
    dismissFocus();
    setSearchSelection(null);
    setSelectedNodeIds((prev) => {
      if (prev.includes(nodeId)) {
        return prev.filter((id) => id !== nodeId);
      }
      const next = [...prev, nodeId];
      return next.length <= MAX_SELECTED_NODES ? next : next.slice(next.length - MAX_SELECTED_NODES);
    });
  }, [dismissFocus]);

  // P6: link/connector and element clicks are published as STRUCTURED context
  // (selectedLinks/selectedElement, which FloatingAssistant forwards as
  // selected_link_ids/selected_element), not just as a human-readable note —
  // that upgrade is recorded in docs/phase2/DECISIONS.md. They stay separate
  // from selected_node_ids because node ids key result/metric lookups while
  // links and elements key the parsed network.
  const handleLinkClick = useCallback((linkId: string, linkName: string, isConnector: boolean) => {
    dismissFocus();
    setSearchSelection(null);
    setSelectedLinkInfo((prev) => (prev && prev.id === linkId ? null : { id: linkId, name: linkName, isConnector }));
  }, [dismissFocus]);

  const handleElementClick = useCallback((ref: NetworkElementRef) => {
    dismissFocus();
    setSearchSelection(null);
    setSelectedElementInfo((prev) => (prev && prev.type === ref.type && prev.id === ref.id ? null : ref));
  }, [dismissFocus]);

  // Mirrors the dashboard/base-model pages' assistant page-context dispatch:
  // publish on mount/change and again whenever the floating assistant asks
  // (vissim:assistant-context-request), matching FloatingAssistant's listener
  // in components/FloatingAssistant.tsx. Neither of those pages dispatches a
  // `clear: true` event on unmount — FloatingAssistant already treats stale
  // context as inert once the pathname no longer starts with its route (see
  // the `pathname.startsWith(pageContext.route)` guard before the assistant
  // reads it), so this page matches that existing convention rather than
  // adding a new one.
  useEffect(() => {
    const publishAssistantContext = () => {
      window.dispatchEvent(
        new CustomEvent("vissim:page-context", {
          detail: {
            route: "/network",
            label: t("네트워크 프리뷰 컨텍스트", "Network preview context"),
            selectedNodeIds,
            selectedNodeNames: selectedNodeIds.map((nodeId) => {
              const node = network?.nodes.find((candidate) => candidate.id === nodeId);
              return node?.name || `Node ${nodeId}`;
            }),
            dataset: activeDataset
              ? {
                  filename: activeDataset.filename,
                  source: activeDataset.source,
                  revision: activeDataset.revision,
                }
              : null,
            // P6: structured selection, forwarded by FloatingAssistant as
            // selected_link_ids/selected_element so the agent resolves the real
            // link/element instead of guessing what "this road" meant. Always
            // 0-or-1 entries today (single-select); the array shape leaves room
            // for a future multi-select without another context-shape change.
            selectedLinks: selectedLinkInfo ? [selectedLinkInfo] : [],
            selectedElement: selectedElementInfo,
            notes: [
              selectedNodeIds.length > 0
                ? t(
                    `선택된 교차로 ${selectedNodeIds.length}개`,
                    `${selectedNodeIds.length} intersection${selectedNodeIds.length > 1 ? "s" : ""} selected`,
                  )
                : t("선택된 교차로 없음", "No intersections selected"),
              // The human-readable mirror of selectedLinks/selectedElement above,
              // kept so an answer can quote what is highlighted on screen.
              ...(selectedLinkInfo
                ? [
                    [
                      t("도식에서", "In the diagram,"),
                      linkKindLabel(selectedLinkInfo.isConnector, t),
                      selectedLinkInfo.name,
                      t("선택됨", "selected"),
                    ].join(" "),
                  ]
                : []),
              ...(selectedElementInfo
                ? [
                    [
                      t("도식에서", "In the diagram,"),
                      elementTypeLabel(selectedElementInfo.type, t),
                      selectedElementInfo.name || selectedElementInfo.id,
                      t("선택됨", "selected"),
                    ].join(" "),
                  ]
                : []),
            ],
            updatedAt: Date.now(),
          },
        }),
      );
    };

    publishAssistantContext();
    window.addEventListener("vissim:assistant-context-request", publishAssistantContext);
    return () => {
      window.removeEventListener("vissim:assistant-context-request", publishAssistantContext);
    };
  }, [activeDataset, network, selectedElementInfo, selectedLinkInfo, selectedNodeIds, t]);

  return (
    <div className="mx-auto w-full max-w-[96rem] space-y-6 pb-16">
      <section className="px-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[22px] font-semibold tracking-tight text-white">
                {t("네트워크 프리뷰", "Network Preview")}
              </h1>
              {network?.source?.view === "working" && (
                <span className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-600">
                  {t("편집본 표시 중", "Showing edited copy")}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-600">
              {t(
                "활성 VISSIM 데이터셋(.inpx)의 원좌표를 그대로 렌더링합니다.",
                "Renders the active VISSIM dataset (.inpx) straight from its raw coordinates.",
              )}
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => setReloadNonce((nonce) => nonce + 1)}
              className="flex h-9 shrink-0 items-center rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600 transition hover:text-slate-900"
            >
              {t("새로고침", "Refresh")}
            </button>
            <NetworkSearchBox onSelect={handleSearchSelect} className="w-full sm:w-80" />
          </div>
          <div className="w-full lg:max-w-[320px]">
            <VissimDatasetBanner onDatasetChange={handleDatasetChange} fullWidth className="h-full" />
          </div>
        </div>
      </section>

      {/* Banner colour is NetworkViewer.tsx's FOCUS_FLASH literal. Tailwind
          arbitrary values are compiled from the literal class string, so it
          cannot be imported — and importing anything from NetworkViewer here
          would defeat its `ssr: false` dynamic import above. Keep the two in
          step by hand; both carry the same "#00c2ff" marker. */}
      {(searchSelection || activeFocus) && (
        <div
          data-testid="network-focus-banner"
          className="mx-1 flex flex-wrap items-center gap-2 rounded-lg border border-[#00c2ff]/50 bg-[#00c2ff]/10 px-3 py-2 text-xs text-slate-600"
        >
          <span aria-hidden="true" className="inline-block h-2 w-2 shrink-0 rounded-full bg-[#00c2ff]" />
          <span className="font-semibold text-slate-700">
            {searchSelection
              ? t("검색 결과 강조", "Search result highlighted", "検索結果をハイライト")
              : t("승인 대상 강조", "Approval target highlighted", "承認対象をハイライト")}
          </span>
          {searchSelection ? (
            <span className="text-slate-500">{searchSelection.label}</span>
          ) : (
            focusBanner && <span className="text-slate-500">{focusBanner}</span>
          )}
          <button
            type="button"
            onClick={searchSelection ? clearSearchSelection : dismissFocus}
            className="ml-auto shrink-0 rounded-md border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:text-slate-900"
          >
            {t("강조 해제", "Clear highlight", "ハイライト解除")}
          </button>
        </div>
      )}

      <div className="relative h-[70vh] min-h-[420px] overflow-hidden rounded-xl border border-slate-300/70 bg-ink shadow-[0_18px_34px_rgba(15,23,42,0.16)]">
        {error ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
            <p className="text-sm text-slate-400">{error}</p>
            <button
              type="button"
              onClick={() => setReloadNonce((nonce) => nonce + 1)}
              className="rounded-lg border border-slate-300/70 bg-surface/90 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-surface"
            >
              {t("다시 시도", "Retry")}
            </button>
          </div>
        ) : loading || !network ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-slate-400">{t("네트워크 도식을 불러오는 중…", "Loading network diagram…")}</p>
          </div>
        ) : (
          <>
            {/* zoomToBbox carries the focus nonce as well as the four numbers:
                NetworkViewer keys its fit-to-region effect off a scalar built
                from them, and on a re-trigger the numbers are by definition
                unchanged — the nonce is the only thing that says "fly there
                again". `focusFlash`/`zoomToBbox` are the COMBINED values
                (search selection takes precedence over the URL-sourced
                approval focus) computed above. */}
            <NetworkViewer
              network={network}
              selectedNodeIds={selectedNodeIds}
              selectedLinkId={selectedLinkInfo?.id ?? null}
              selectedElement={selectedElementInfo}
              onNodeClick={handleNodeClick}
              onLinkClick={handleLinkClick}
              onElementClick={handleElementClick}
              onProjectionChange={projection.set}
              focusFlash={focusFlash}
              zoomToBbox={zoomToBbox}
            />
            {/* Canvas overlay: same box as the viewer's SVG, pointer-events
                none so zoom/pan/click all still land on the SVG beneath it. */}
            {playback.frames && (
              <TrajectoryLayer engine={playbackEngine} frames={playback.frames} projection={projection} network={network} />
            )}
          </>
        )}
      </div>

      {/* Playback controls only exist once there is a network to draw over. */}
      {!error && !loading && network && (
        <section className="px-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
            {t("차량 궤적 재생", "Vehicle playback")}
          </p>

          {/* Playback-source picker: the default demo/file source plus any
              completed agent-run job that recorded a real trajectory for the
              currently active dataset. Only shown once a real run exists --
              nothing changes for the common case of no recorded runs yet. */}
          {replayableJobs.length > 0 && (
            <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
              {t("재생 소스", "Playback source", "再生ソース")}
              <select
                value={trajectorySourceJobId ?? ""}
                onChange={(event) => handleTrajectorySourceChange(event.target.value || null)}
                className="h-8 rounded-lg border border-slate-200 bg-surface px-2 text-xs text-slate-700"
              >
                <option value="">{t("데모 데이터", "Demo data", "デモデータ")}</option>
                {replayableJobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {formatJobLabel(job)}
                  </option>
                ))}
              </select>
            </label>
          )}

          {playback.status === "idle" ? (
            <button
              type="button"
              onClick={() => playbackEngine.load(trajectorySourceJobId ?? undefined)}
              className="mt-2 flex h-9 shrink-0 items-center rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600 transition hover:text-slate-900"
            >
              {t("재생 준비", "Load playback")}
            </button>
          ) : playback.status === "loading" ? (
            <p className="mt-2 text-xs text-slate-500">{t("궤적 데이터를 불러오는 중…", "Loading trajectory data…")}</p>
          ) : playback.status === "error" ? (
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <p className="text-xs text-slate-500">
                {t(
                  "궤적 데이터를 불러오지 못했습니다. 이 데이터셋에 .fzp 파일이 없을 수 있습니다.",
                  "Couldn't load trajectory data. This dataset may have no .fzp file.",
                )}
              </p>
              <button
                type="button"
                onClick={() => playbackEngine.load(trajectorySourceJobId ?? undefined)}
                className="flex h-9 shrink-0 items-center rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600 transition hover:text-slate-900"
              >
                {t("다시 시도", "Retry")}
              </button>
            </div>
          ) : (
            <div className="mt-3 flex flex-col gap-2.5">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={playbackEngine.togglePlay}
                  aria-pressed={playback.playing}
                  className="flex h-9 min-w-[6.5rem] shrink-0 items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600 transition hover:text-slate-900"
                >
                  <span aria-hidden="true">{playback.playing ? PAUSE_GLYPH : PLAY_GLYPH}</span>
                  {playback.playing ? t("일시정지", "Pause") : t("재생", "Play")}
                </button>
                <span className="text-xs tabular-nums text-slate-700">
                  {formatClock(playback.time - playback.t0)} / {formatClock(playback.t1 - playback.t0)}
                </span>
                <span className="text-[11px] tabular-nums text-slate-500">
                  {t("시뮬레이션", "sim")} {playback.time.toFixed(1)}
                  {t("초", "s")}
                </span>
                <div
                  role="group"
                  aria-label={t("재생 속도", "Playback speed")}
                  className="ml-auto flex h-9 shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 p-1 text-xs font-medium"
                >
                  {PLAYBACK_SPEEDS.map((option) => {
                    const active = playback.speed === option;
                    return (
                      <button
                        key={option}
                        type="button"
                        aria-pressed={active}
                        onClick={() => playbackEngine.setSpeed(option)}
                        className={`flex h-full items-center rounded-md px-3 transition-colors ${
                          active ? "bg-white/10 text-white" : "text-slate-500 hover:text-slate-800"
                        }`}
                      >
                        ×{option}
                      </button>
                    );
                  })}
                </div>
              </div>

              <input
                type="range"
                aria-label={t("재생 위치", "Playback position")}
                min={playback.t0}
                max={playback.t1}
                step={0.1}
                value={playback.time}
                onChange={(event) => playbackEngine.seek(Number(event.target.value))}
                className="w-full accent-[#4c8dff]"
              />

              <p className="text-[11px] text-slate-500">
                {t(
                  `차량 ${playback.vehicleCount}대`,
                  `${playback.vehicleCount} vehicle${playback.vehicleCount === 1 ? "" : "s"}`,
                  `車両 ${playback.vehicleCount}台`,
                )}
              </p>
            </div>
          )}
        </section>
      )}

      {selectedNodeIds.length > 0 && (
        <p className="px-1 text-xs text-slate-500">
          {t(`선택된 교차로 ${selectedNodeIds.length}개`, `${selectedNodeIds.length} intersection${selectedNodeIds.length > 1 ? "s" : ""} selected`)}
        </p>
      )}
    </div>
  );
}

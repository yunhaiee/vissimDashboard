"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import CurationBar from "@/components/dashboard/CurationBar";
import NetworkSearchBox, { type NetworkSearchSelection } from "@/components/NetworkSearchBox";
import NodeFlowPanel from "@/components/NodeFlowPanel";
// Type-only -- the runtime component below still only ever loads through the
// `dynamic(..., { ssr: false })` import so d3's DOM access never reaches SSR.
import { type ViewProjection } from "@/components/NetworkViewer";
import TrajectoryLayer, {
  createProjectionStore,
  PLAYBACK_SPEEDS,
  useTrajectoryPlayback,
  type Playback,
  type PlaybackEngine,
} from "@/components/TrajectoryLayer";
import VissimDatasetBanner from "@/components/VissimDatasetBanner";
import {
  clearStoredScenarioSelection,
  readStoredScenarioSelection,
  writeStoredScenarioSelection,
} from "@/lib/dashboardSelectionStorage";
import {
  fetchActiveDataset,
  fetchCompare,
  fetchCompareSummary,
  fetchNetwork,
  fetchRealCounts,
  fetchScenarios,
  fetchSimulationJobs,
  type ActiveDataset,
  type CompareSummaryResponse,
  type DashboardCurationSpec,
  type NetworkData,
  type NodeRealCounts,
  type ScenarioMeta,
  type ScenarioResults,
  type SimulationJob,
} from "@/lib/api";
// calculateNodeAverageDelay is no longer imported here: per-direction delay is
// measured now (unit 1.5 D5) instead of being derived from the node average.
// It is still exported from lib/los and used by components/NetworkViewer.tsx.
import { LOS_COLORS, LOS_GRADE_THRESHOLDS, objectBelongsToNode, resolveLosGrade, type LosGrade } from "@/lib/los";
import { elementTypeLabel, linkKindLabel, type NetworkElementRef, type NetworkLinkRef } from "@/lib/networkElements";
import { buildFocusFlash, nextFocusNonce, useFocusPulse, type FocusBbox, type FocusElementRef } from "@/lib/networkFocus";
import { searchHitDisplayLabel, type SearchHitLabelParts } from "@/lib/networkSearchLabel";
import {
  addAllCapped,
  elementKey,
  linkKey,
  MAX_SELECTED_ELEMENTS,
  MAX_SELECTED_LINKS,
  nodeKey,
  replaceWith,
  toggleItem,
} from "@/lib/networkSelection";
import { getScenarioDisplayColor } from "@/lib/scenarioPalette";
// THE GEH implementation for the frontend (unit 1.5 D2) -- both this page and
// app/base-model/page.tsx import it instead of each keeping a private copy.
import { geh as calculateGeh, gehPassRate, type GehPassRate } from "@/lib/geh";
import { errorTone, gehTone, toneBadgeClass, type ValidationTone } from "@/lib/validationThresholds";

const DashboardMap = dynamic(() => import("@/components/DashboardMap"), { ssr: false });
// NetworkViewer draws the raw-coordinate diagram with d3, which touches the
// DOM directly, so it (like DashboardMap/Leaflet) must stay client-only.
const NetworkViewer = dynamic(() => import("@/components/NetworkViewer"), { ssr: false });

const SECONDS_PER_HOUR = 3600;

const KPI_METRICS = [
  // isTotal metrics (whole-network sums) scale with vehicle count, so their
  // "Best" (typically the lowest) can simply mean fewer vehicles were
  // served, not a better outcome — KpiCard caveats those instead of a bare
  // "Best" tag.
  //
  // UX #24 (unit 1.5 D6): the two network TOTALS are reported in
  // VEHICLE-HOURS. Vissim emits them in seconds, which rendered as an
  // unreadable "51.5k" with no way to tell 51,500 s from 51,500 anything —
  // 14.3 veh·h is the number a traffic engineer actually reports.
  // `displayDivisor` is applied to the value AND its standard deviation right
  // before rendering; the raw seconds are never mutated.
  // Averages and rates keep their own units and a divisor of 1 — dividing a
  // per-vehicle average by 3600 would be meaningless.
  { key: "DELAYAVG", label: "Average Delay", unit: "s/veh", higherIsBetter: false, isTotal: false, displayDivisor: 1 },
  { key: "SPEEDAVG", label: "Average Speed", unit: "km/h", higherIsBetter: true, isTotal: false, displayDivisor: 1 },
  { key: "TRAVTMTOT", label: "Total Travel Time", unit: "veh·h", higherIsBetter: false, isTotal: true, displayDivisor: SECONDS_PER_HOUR },
  { key: "DELAYTOT", label: "Total Delay", unit: "veh·h", higherIsBetter: false, isTotal: true, displayDivisor: SECONDS_PER_HOUR },
  { key: "VEHARR", label: "Arrived Vehicles", unit: "veh", higherIsBetter: true, isTotal: false, displayDivisor: 1 },
  { key: "STOPSAVG", label: "Average Stops", unit: "stops/veh", higherIsBetter: false, isTotal: false, displayDivisor: 1 },
] as const;

const MAX_SELECTED_NODES = 6;

type NodeDirectionRow = NonNullable<ScenarioResults["node_directions"]>[number];
type DirectionKey = "north" | "east" | "south" | "west";
type IntersectionMetricBarValue = {
  nodeId: string;
  nodeName: string;
  value: number | null;
  scenarioName?: string | null;
};

type IntersectionPerformanceCard = {
  nodeId: string;
  nodeName: string;
  delayByDirection: Array<{ direction: DirectionKey; value: number | null; los: LosGrade | null }>;
  maxQueueLength: number | null;
  maxQueueScenarioName: string | null;
  worstApproachDelay: number | null;
  worstDelayScenarioName: string | null;
  volumeTotal: number | null;
  volumeDirections: Record<DirectionKey, number>;
};

type TravelTimeSeries = {
  scenario: ScenarioMeta;
  // Resolved once, from the scenario's position in the FULL scenario list
  // (see scenarioColorById below) -- NOT recomputed from this series array's
  // own position, which only reflects the currently-selected subset.
  color: string;
  points: Array<{ label: string; value: number | null }>;
};
type TravelTimeChart = {
  series: TravelTimeSeries[];
  labels: string[];
  truncated: boolean;
  useSectionLabels: boolean;
};
type AssistantSimulationCompleteDetail = {
  jobId?: string;
  scenarioResultId?: string | null;
};

const DIRECTION_LABELS: Record<DirectionKey, { ko: string; en: string; shortKo: string; shortEn: string }> = {
  north: { ko: "북", en: "North", shortKo: "북", shortEn: "N" },
  east: { ko: "동", en: "East", shortKo: "동", shortEn: "E" },
  south: { ko: "남", en: "South", shortKo: "남", shortEn: "S" },
  west: { ko: "서", en: "West", shortKo: "서", shortEn: "W" },
};

const DIRECTION_ORDER: DirectionKey[] = ["north", "east", "south", "west"];
// Direction hues are deliberately a DIFFERENT family from the vivid scenario
// palette (blue/coral/green/violet in scenarioPalette.ts): amber/teal/magenta/
// bronze, so a volume segment can never be mistaken for a scenario key on the
// same screen. They also stay clear of the LOS green→red scale.
const DIRECTION_COLORS: Record<DirectionKey, string> = {
  north: "#e0a73c",
  east: "#17a89e",
  south: "#d45ca0",
  west: "#b0763a",
};
// The two intersection metric-bar charts identify each row by its NAME label,
// so the bar itself only needs to encode magnitude — a single neutral steel
// keeps it from echoing either the scenario or the direction palettes.
const METRIC_BAR_COLOR = "#7f8ba0";

// Every chart on this page must color a scenario the SAME way the picker
// legend does, regardless of which other scenarios are currently selected
// alongside it. scenarioColorById (built once from the FULL scenario list,
// see DashboardPage) is the single source of truth; this just reads it with
// a defensive fallback for a scenario id it doesn't recognize (shouldn't
// happen in practice since every scenario shown anywhere on this page comes
// from that same full list).
function resolveScenarioColor(colorById: Map<string, string>, scenario: Pick<ScenarioMeta, "id">): string {
  return colorById.get(scenario.id) ?? getScenarioDisplayColor(scenario, -1);
}

// --- Unit #21 (2026-08-14, option A): vehicle-trajectory replay -----------
// Moved here from app/network/page.tsx -- replay is something you do to a RUN
// (this page's scenario-comparison unit), not to a network. Everything below
// is verbatim from that page's own playback panel except the run is no longer
// picked from a free-floating dropdown; see `replayJob` at its call site for
// how it is anchored instead.

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

// Label for the anchored run -- job name (falls back to its id) plus a short
// local-time stamp.
function formatJobLabel(job: SimulationJob): string {
  const name = job.name || job.id;
  if (!job.completed_at) return name;
  const date = new Date(job.completed_at);
  if (Number.isNaN(date.getTime())) return name;
  return `${name} (${date.toLocaleString()})`;
}

// A completed agent-run job's scenario id is deterministically `SIM_<job.id>`
// -- backend/services/simulation_runs.py's create_job_record sets every job
// record's `scenario_result_id` to exactly `runtime_scenario_id(job_id)` at
// creation time, and list_completed_runtime_scenarios() (which is what
// /api/scenarios ultimately serves for a simulation-produced scenario) uses
// that same field as the scenario's `id`. This is the ONLY place the two id
// spaces meet on the frontend: /api/scenarios strips job_id off before a
// runtime scenario ever reaches ScenarioMeta, so decoding the prefix back out
// is not a guess, it is reading the one field the backend actually sends.
const RUNTIME_SCENARIO_PREFIX = "SIM_";
function runtimeScenarioJobId(scenarioId: string): string | null {
  return scenarioId.startsWith(RUNTIME_SCENARIO_PREFIX) ? scenarioId.slice(RUNTIME_SCENARIO_PREFIX.length) : null;
}

export default function DashboardPage() {
  const { t, lang } = useLanguage();
  const [scenarios, setScenarios] = useState<ScenarioMeta[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, ScenarioResults>>({});
  const [summary, setSummary] = useState<CompareSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [datasetLoading, setDatasetLoading] = useState(true);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [nodeNamesById, setNodeNamesById] = useState<Record<string, string>>({});
  const [realCounts, setRealCounts] = useState<NodeRealCounts[]>([]);
  const [datasetRevision, setDatasetRevision] = useState("default");
  const [curationSpec, setCurationSpec] = useState<DashboardCurationSpec | null>(null);
  const [audience, setAudience] = useState<"plain" | "expert">("plain");
  // Local-only "reveal all six" override for the network-KPI metric filter
  // below (spec.kpi_metrics narrows which of the six KPI_METRICS render).
  // Deliberately NOT part of the curation spec: clicking it must not change
  // what question was asked, only what's visible for the current answer --
  // mirrors CurationBar's own "Show everything" vs. re-asking distinction.
  const [kpiMetricsExpanded, setKpiMetricsExpanded] = useState(false);
  useEffect(() => {
    setKpiMetricsExpanded(false);
  }, [curationSpec]);
  // Map section 지도/도식 toggle. Leaflet ("map") stays the default; "diagram"
  // mounts NetworkViewer over the raw .inpx coordinates. The network is fetched
  // lazily — only once the diagram has been requested at least once — and then
  // refetched whenever the active dataset changes.
  const [mapView, setMapView] = useState<"map" | "diagram">("map");
  const [hasRequestedDiagram, setHasRequestedDiagram] = useState(false);
  // Unit #21: vehicle-trajectory replay. `projection` is the only channel
  // between the diagram's live zoom transform and the canvas overlay -- it is
  // deliberately not React state (see components/TrajectoryLayer.tsx's module
  // doc for why: zoom emits once per animation frame, and re-rendering this
  // page that often would be wasteful). The playback hook owns the clock and
  // fetches lazily (nothing is requested until the user presses 재생 준비).
  const projection = useMemo(() => createProjectionStore(), []);
  const playback = useTrajectoryPlayback();
  const playbackEngine = playback.engine;
  // Completed agent-run jobs that recorded a vehicle trajectory (routers/
  // simulations.py's _run_agent_simulation_job, trajectory_recorded=true) for
  // the currently active dataset -- the pool replay is allowed to draw from.
  // Which one (if any) actually anchors replay is decided by `replayJob`
  // below, from the scenario comparison's OWN primary selection -- this list
  // is never presented as an independent picker (see docs on VehicleReplaySection).
  const [replayableJobs, setReplayableJobs] = useState<SimulationJob[]>([]);
  // Multi-select (v2 UX unit, 2026-07-29, lib/networkSelection.ts). Plain
  // click REPLACES the whole selection with just the clicked thing (clicking
  // the sole-selected item again clears it); shift+click adds/removes;
  // shift+drag draws a box that adds everything inside. Mirrors /network's
  // page exactly — the "전체 통일" design decision requires both pages to
  // behave identically.
  const [selectedLinks, setSelectedLinks] = useState<NetworkLinkRef[]>([]);
  // P6: the static elements (signal head, detector, …) clicked in the
  // diagram view — 0, 1, or several (lib/networkSelection.ts's
  // MAX_SELECTED_ELEMENTS, mirroring the backend's own cap).
  const [selectedElements, setSelectedElements] = useState<NetworkElementRef[]>([]);
  // Ephemeral "selection limit reached" notice (Section D) — cleared after a
  // few seconds so it reads as a toast without needing a dedicated toast
  // component (none exists on this page).
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const selectionNoticeTimeoutRef = useRef<number | null>(null);
  const showSelectionNotice = useCallback((message: string) => {
    setSelectionNotice(message);
    if (selectionNoticeTimeoutRef.current != null) window.clearTimeout(selectionNoticeTimeoutRef.current);
    selectionNoticeTimeoutRef.current = window.setTimeout(() => setSelectionNotice(null), 4000);
  }, []);
  useEffect(() => {
    return () => {
      if (selectionNoticeTimeoutRef.current != null) window.clearTimeout(selectionNoticeTimeoutRef.current);
    };
  }, []);
  // Mirrors for the box-select handler below, which needs to read the LATEST
  // selection synchronously to combine it with the box's haul via
  // addAllCapped — a functional setState updater's return value is not
  // observable synchronously after the setState call returns (React defers
  // running it), so three independent setSelectedX(prev => ...) calls cannot
  // be used to compute one combined omitted-count the way a single
  // read-then-write can. Same pattern `selectedRef` below already uses for
  // the scenario-selection reload, for the same underlying reason.
  const selectedNodeIdsRef = useRef<string[]>([]);
  const selectedLinksRef = useRef<NetworkLinkRef[]>([]);
  const selectedElementsRef = useRef<NetworkElementRef[]>([]);
  useEffect(() => {
    selectedNodeIdsRef.current = selectedNodeIds;
  }, [selectedNodeIds]);
  useEffect(() => {
    selectedLinksRef.current = selectedLinks;
  }, [selectedLinks]);
  useEffect(() => {
    selectedElementsRef.current = selectedElements;
  }, [selectedElements]);
  const [network, setNetwork] = useState<NetworkData | null>(null);
  const [networkLoading, setNetworkLoading] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [networkReloadNonce, setNetworkReloadNonce] = useState(0);
  // Network search selection (v2 UX unit, 2026-07-28): NetworkSearchBox is
  // only mounted while the diagram (not the Leaflet map) is showing -- see
  // its render site below -- so there is no URL-sourced focus to combine
  // with here, unlike /network's page. Same shared pulse/flash plumbing
  // (lib/networkFocus.ts) as that page, kept in its own state slot.
  const [diagramSearchSelection, setDiagramSearchSelection] = useState<{
    key: string;
    bbox: FocusBbox;
    elements: FocusElementRef[];
    hit: SearchHitLabelParts;
  } | null>(null);
  // P2: the floating assistant's current agent session id, mirrored here via
  // the "vissim:agent-session" event so the diagram viewer can show that
  // session's in-progress edited working copy on demand (refresh) -- see
  // docs/phase2/DECISIONS.md's P2 entry.
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null);
  const requestSerial = useRef(0);
  const loadSerial = useRef(0);
  // Auto-seed a default intersection once per dataset so the performance and
  // flow panels are not empty on first load; reset on dataset change.
  const autoSeededNodesRef = useRef(false);
  // Mirrors `selected` for reloadScenariosAndValidation below, which needs
  // to READ the latest selection from inside an async .then() callback
  // without going through setSelected's functional-updater form (that form
  // would have to compute `next` inside the updater, which then has to be
  // read back out to persist it to storage -- and the updater itself must
  // stay a pure function of its `current` argument: React may invoke it
  // more than once (StrictMode) or discard the render that queued it
  // (concurrent features), so it must not have side effects and its return
  // value isn't reliably observable synchronously after the setSelected
  // call returns either). Kept in sync by the effect right below.
  const selectedRef = useRef<string[]>([]);
  const hasUploadedDataset = activeDataset?.source === "uploaded";
  // Focus mode: with no curation spec every section is visible; with one,
  // only the LLM-chosen sections render (the header and curation bar always stay).
  const sectionVisible = useCallback(
    (sectionId: string) => curationSpec == null || curationSpec.charts.includes(sectionId),
    [curationSpec],
  );
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);
  const clearDashboardData = useCallback(() => {
    requestSerial.current += 1;
    loadSerial.current += 1;
    setScenarios([]);
    setSelected([]);
    setResults({});
    setSummary(null);
    setSelectedNodeIds([]);
    setNodeNamesById({});
    setRealCounts([]);
    autoSeededNodesRef.current = false;
    // An AI curation answer is only valid for the scenarios/data it was
    // computed against; a dataset change invalidates it immediately.
    setCurationSpec(null);
    // The persisted comparison-set selection (see lib/dashboardSelectionStorage.ts)
    // is scoped to the dataset it was made against; leaving it behind here
    // would resurrect it against whatever dataset loads next.
    clearStoredScenarioSelection();
  }, []);

  // `revision` is threaded in as an argument (rather than read from the
  // `datasetRevision` state var) because both call sites below set
  // `datasetRevision` and call this function back-to-back in the same tick
  // -- reading the state here could see the value from BEFORE that update
  // committed. See lib/dashboardSelectionStorage.ts for why the restore is
  // keyed by revision at all (id collision across datasets).
  const reloadScenariosAndValidation = useCallback((revision: string, preferredScenarioId?: string | null) => {
    const loadId = ++loadSerial.current;
    setLoading(true);
    setError(null);

    Promise.allSettled([fetchScenarios(), fetchRealCounts()]).then(([scenarioOut, countsOut]) => {
      if (loadSerial.current !== loadId) return;

      if (scenarioOut.status === "fulfilled") {
        const items = scenarioOut.value;
        setScenarios(items);
        // Read the latest selection from the ref (kept in sync by the
        // effect above `clearDashboardData`) rather than via setSelected's
        // functional-updater form: the updater callback would have to
        // compute `next` AND persist it, but it must stay a pure function
        // of its `current` argument (React may invoke it more than once in
        // StrictMode, or discard it entirely under concurrent rendering),
        // so the write has to happen out here regardless. Computing `next`
        // out here too keeps the read and the write on the same footing.
        const current = selectedRef.current;
        const validCurrent = current.filter((id) => items.some((item) => item.id === id));
        let next: string[];
        if (preferredScenarioId && items.some((item) => item.id === preferredScenarioId)) {
          next = [preferredScenarioId, ...validCurrent.filter((id) => id !== preferredScenarioId)].slice(0, 4);
        } else if (validCurrent.length > 0) {
          // A selection the user (or a preceding call) already made is
          // restored in full -- the 4-cap below only bounds the
          // AUTOMATIC first-visit pick, not a deliberate selection that
          // happens to be bigger than 4.
          next = validCurrent;
        } else {
          const restored = readStoredScenarioSelection(revision)?.filter((id) => items.some((item) => item.id === id));
          next = restored && restored.length > 0 ? restored : items.slice(0, 4).map((item) => item.id);
        }
        setSelected(next);
        writeStoredScenarioSelection(revision, next);
      } else {
        setScenarios([]);
        setSelected([]);
        setError(t("시나리오 목록을 불러오지 못했습니다.", "Failed to load scenario list."));
      }

      setRealCounts(countsOut.status === "fulfilled" ? countsOut.value : []);
      setLoading(false);
    });
  }, [t]);

  // The picker's onChange goes through this (instead of setSelected
  // directly) so a manual selection change is persisted the moment it
  // happens -- see lib/dashboardSelectionStorage.ts. datasetRevision is
  // read from state here (not threaded as an argument like the two
  // reloadScenariosAndValidation call sites) because by the time a user can
  // click the picker the dataset load has already settled; there's no
  // same-tick staleness risk to guard against.
  const handleSelectedChange = useCallback((next: string[]) => {
    setSelected(next);
    writeStoredScenarioSelection(datasetRevision, next);
  }, [datasetRevision]);

  const loadActiveDataset = useCallback(() => {
    let cancelled = false;
    // Deferred one microtask so this can be called directly from a useEffect
    // body without React flagging a synchronous setState-in-effect (same
    // pattern already used below for the compare-results fetch effect).
    Promise.resolve().then(() => {
      if (!cancelled) setDatasetLoading(true);
    });

    fetchActiveDataset()
      .then((dataset) => {
        if (cancelled) return;
        setActiveDataset(dataset);
        const revision = dataset.revision ?? "default";
        setDatasetRevision(revision);
        setDatasetLoading(false);
        setError(null);

        if (dataset.source === "uploaded") {
          reloadScenariosAndValidation(revision);
        } else {
          clearDashboardData();
          setLoading(false);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setActiveDataset(null);
        setDatasetLoading(false);
        clearDashboardData();
        setLoading(false);
        // Previously this dead-ended with no way to retry short of a full
        // page reload once the backend was briefly unreachable.
        setError(t("데이터셋 상태를 확인하지 못했습니다. 백엔드 서버가 실행 중인지 확인하세요.", "Failed to check dataset status. Make sure the backend server is running."));
      });

    return () => {
      cancelled = true;
    };
  }, [clearDashboardData, reloadScenariosAndValidation, t]);

  useEffect(() => loadActiveDataset(), [loadActiveDataset]);

  // P2: mirror the floating assistant's current agent session id -- set on a
  // fresh agent turn, cleared on a session reset/agent-mode toggle -- so the
  // diagram viewer's refresh button knows which session's working copy (if
  // any) to request. See FloatingAssistant.tsx's own "vissim:agent-session"
  // dispatch effect.
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

  // The AI curation answer (headline + insight cards + focus mode) is only
  // valid for the scenario set it was computed against; changing which
  // scenarios are compared must not leave stale numbers on screen.
  const selectedKey = selected.join(",");
  const isFirstSelectionRender = useRef(true);
  useEffect(() => {
    if (isFirstSelectionRender.current) {
      isFirstSelectionRender.current = false;
      return;
    }
    setCurationSpec(null);
  }, [selectedKey]);

  useEffect(() => {
    const reqId = ++requestSerial.current;

    if (!hasUploadedDataset || !selected.length) {
      Promise.resolve().then(() => {
        if (requestSerial.current !== reqId) return;
        setResults({});
        setSummary(null);
        setLoading(false);
      });
      return;
    }

    Promise.resolve().then(() => {
      if (requestSerial.current !== reqId) return;
      setLoading(true);
      setError(null);
    });

    Promise.allSettled([fetchCompare(selected), fetchCompareSummary(selected)]).then(
      ([compareOut, summaryOut]) => {
        if (requestSerial.current !== reqId) return;

        if (compareOut.status !== "fulfilled") {
          setResults({});
          setSummary(null);
          setError(t("비교 데이터를 불러오지 못했습니다.", "Failed to load comparison data."));
          setLoading(false);
          return;
        }

        setResults(compareOut.value);
        setSummary(summaryOut.status === "fulfilled" ? summaryOut.value : null);
        setLoading(false);
      },
    );
  }, [hasUploadedDataset, selected, t]);

  // Seed the busiest intersection into the selection once results arrive, so
  // the intersection-performance and flow panels render real content on first
  // load instead of an empty "click a node" prompt. Runs at most once per
  // dataset (autoSeededNodesRef), and never overrides a user's own selection.
  useEffect(() => {
    if (autoSeededNodesRef.current) return;
    if (selectedNodeIds.length > 0) return;
    const top = summary?.top_congested_nodes?.[0];
    if (!top?.node_id) return;
    autoSeededNodesRef.current = true;
    const nodeId = String(top.node_id);
    const nodeName = top.node_name ?? `Node ${nodeId}`;
    setNodeNamesById((prev) => ({ ...prev, [nodeId]: nodeName }));
    setSelectedNodeIds([nodeId]);
  }, [summary, selectedNodeIds.length]);

  useEffect(() => {
    function handleAssistantSimulationComplete(event: Event) {
      if (!hasUploadedDataset) return;
      const detail = (event as CustomEvent<AssistantSimulationCompleteDetail>).detail;
      // Same dataset, so datasetRevision is already settled by the time a
      // simulation can complete (unlike the loadActiveDataset/handleDatasetChange
      // call sites, which set it in the same tick they call this).
      reloadScenariosAndValidation(datasetRevision, detail?.scenarioResultId ?? null);
    }

    window.addEventListener("vissim:assistant-simulation-complete", handleAssistantSimulationComplete);
    return () => {
      window.removeEventListener("vissim:assistant-simulation-complete", handleAssistantSimulationComplete);
    };
  }, [hasUploadedDataset, reloadScenariosAndValidation, datasetRevision]);

  const activeScenarios = useMemo(
    () => scenarios.filter((scenario) => selected.includes(scenario.id)),
    [scenarios, selected],
  );

  // Unit #21: the run replay is anchored to. NOT its own independent
  // selection -- it is exactly `activeScenarios[0]`, the SAME "primary"
  // scenario NetworkDiagramView below already colors the diagram against
  // (`selectedScenario={activeScenarios[0]?.id}`), so a run switch in the
  // comparison picker is also a run switch for replay; the two can never
  // silently disagree because there is only the one selection.
  const primaryScenario = activeScenarios[0] ?? null;
  const replayableJobsById = useMemo(() => {
    const map = new Map<string, SimulationJob>();
    for (const job of replayableJobs) map.set(job.id, job);
    return map;
  }, [replayableJobs]);
  const replayJob = useMemo(() => {
    const jobId = primaryScenario ? runtimeScenarioJobId(primaryScenario.id) : null;
    return jobId ? replayableJobsById.get(jobId) ?? null : null;
  }, [primaryScenario, replayableJobsById]);
  // Identifies WHICH run replay is currently pointed at, so the reset effect
  // right below can tell "still the same run" from "switched" without
  // re-deriving the whole object. Three distinct values, matching
  // VehicleReplaySection's three render states.
  const replayAnchorKey = replayJob ? `job:${replayJob.id}` : replayableJobs.length === 0 ? "demo" : "none";
  // Adversarial case this guards: switching the scenario comparison picker
  // (or the dataset) mid-playback must never leave the OLD run's frames
  // animating under the NEW run's label. `playback.frames` is keyed by
  // nothing but "whatever was last loaded" -- the only way to stop it
  // silently surviving a switch is to drop it back to idle every time the
  // anchor's identity changes, forcing an explicit 재생 준비 click for
  // whatever is newly selected rather than ever assuming continuity.
  useEffect(() => {
    playbackEngine.reset();
  }, [playbackEngine, replayAnchorKey]);

  // id -> display color, keyed off each scenario's position in the FULL
  // `scenarios` list (the same order DashboardScenarioPicker renders) so a
  // scenario's color is stable no matter which subset ends up selected. Every
  // chart below must resolve colors through this map (via resolveScenarioColor)
  // instead of indexing into `activeScenarios`/`selected`, which only reflects
  // the current selection's order and length.
  const scenarioColorById = useMemo(() => {
    const map = new Map<string, string>();
    scenarios.forEach((scenario, index) => {
      map.set(scenario.id, getScenarioDisplayColor(scenario, index));
    });
    return map;
  }, [scenarios]);

  const hasData = useMemo(
    () => activeScenarios.some((scenario) => results[scenario.id]),
    [activeScenarios, results],
  );
  // Section ids that pass their OWN data gates right now, independent of any
  // curation answer — lets CurationBar report how many it hid and explain
  // when it picked a section whose data (e.g. field counts) isn't loaded.
  const applicableSectionIds = useMemo(() => {
    const ids = ["map-selection"];
    if (hasData) ids.push("network-kpi-comparison", "intersection-performance", "travel-time-comparison", "overall-results-analysis");
    if (hasData && realCounts.length > 0) ids.push("field-count-validation");
    return ids;
  }, [hasData, realCounts.length]);

  // Metric-level focus mode inside network-kpi-comparison: a spec.kpi_metrics
  // subset narrows the six KPI cards down to the ones the question was
  // actually about (e.g. "왜 평균속도가 낮아?" -> speed + its delay companion),
  // instead of curation only ever being able to filter whole sections.
  // null here means "no restriction" -- absent/empty spec, or an
  // unrecognized/full set all fall back to showing every metric, same as
  // before this field existed.
  const kpiMetricsFilter = useMemo(() => {
    const requested = curationSpec?.kpi_metrics;
    if (!requested || requested.length === 0) return null;
    const valid = KPI_METRICS.map((metric) => metric.key).filter((key) => requested.includes(key));
    if (valid.length === 0 || valid.length >= KPI_METRICS.length) return null;
    return valid;
  }, [curationSpec]);
  const visibleKpiMetrics = useMemo(() => {
    if (!kpiMetricsFilter || kpiMetricsExpanded) return KPI_METRICS;
    return KPI_METRICS.filter((metric) => kpiMetricsFilter.includes(metric.key));
  }, [kpiMetricsFilter, kpiMetricsExpanded]);

  const minRunCount = useMemo(() => {
    const counts = activeScenarios.map((scenario) => results[scenario.id]?.run_count ?? 1).filter((count) => count > 0);
    return counts.length ? Math.min(...counts) : 1;
  }, [activeScenarios, results]);

  const activeNodeId = selectedNodeIds[selectedNodeIds.length - 1] ?? null;
  const activeNodeName = activeNodeId ? nodeNamesById[activeNodeId] ?? `Node ${activeNodeId}` : "";

  const intersectionCards = useMemo(
    () => buildIntersectionPerformanceCards(selectedNodeIds, nodeNamesById, activeScenarios, results),
    [activeScenarios, nodeNamesById, results, selectedNodeIds],
  );

  const travelTimeSeries = useMemo(
    () => buildTravelTimeSeries(activeScenarios, results, scenarioColorById),
    [activeScenarios, results, scenarioColorById],
  );

  const overallNarrative = useMemo(
    () => buildOverallNarrative(summary, activeScenarios, hasData, lang),
    [activeScenarios, hasData, summary, lang],
  );

  // Multi-select (v2 UX unit, 2026-07-29): plain click REPLACES THE WHOLE
  // selection — every channel, not just this one — with just this node
  // (clicking the sole-selected node again clears it); shift+click toggles
  // it into/out of the existing selection, capped at MAX_SELECTED_NODES.
  // "전체 통일": mixing types (2 nodes + a link) is ONLY reachable via
  // shift+click — a plain click always collapses back to a single-type,
  // single-item selection, which is why the plain-click branch below also
  // clears the OTHER two channels (reading their CURRENT length from the
  // ref-mirrors declared above, synchronously, before deciding whether this
  // is a toggle-off). `additive` defaults to false so DashboardMap's
  // Leaflet click path (which only ever passes nodeId/nodeName — no shift
  // support there) keeps working unchanged.
  const handleNodeClick = useCallback((nodeId: string, nodeName: string, additive: boolean = false) => {
    setDiagramSearchSelection(null);
    setNodeNamesById((prev) => ({ ...prev, [nodeId]: nodeName }));
    if (additive) {
      setSelectedNodeIds((prev) => {
        const { next, capped } = toggleItem(prev, nodeId, nodeKey, MAX_SELECTED_NODES);
        if (capped) showSelectionNotice(t(`교차로는 최대 ${MAX_SELECTED_NODES}개까지 선택할 수 있습니다.`, `You can select up to ${MAX_SELECTED_NODES} intersections.`));
        return next;
      });
      return;
    }
    const otherChannelsEmpty = selectedLinksRef.current.length === 0 && selectedElementsRef.current.length === 0;
    setSelectedNodeIds((prev) => replaceWith(prev, nodeId, nodeKey, otherChannelsEmpty));
    setSelectedLinks([]);
    setSelectedElements([]);
  }, [showSelectionNotice, t]);

  const handleRemoveNode = useCallback((nodeId: string) => {
    setSelectedNodeIds((prev) => prev.filter((id) => id !== nodeId));
  }, []);

  // P6: link/connector and element clicks in the diagram view are published as
  // STRUCTURED context (selectedLinks/selectedElements, forwarded by
  // FloatingAssistant as selected_link_ids/selected_element(s)), not just as a
  // human-readable note — see docs/phase2/DECISIONS.md's P6 entry. They stay
  // separate from selected_node_ids: node ids key result/metric lookups, while
  // link and element ids key the parsed network. Same cross-channel
  // plain-click replace as handleNodeClick above, mirroring /network's page.
  const handleLinkClick = useCallback((linkId: string, linkName: string, isConnector: boolean, additive: boolean) => {
    setDiagramSearchSelection(null);
    const item: NetworkLinkRef = { id: linkId, name: linkName, isConnector };
    if (additive) {
      setSelectedLinks((prev) => {
        const { next, capped } = toggleItem(prev, item, linkKey, MAX_SELECTED_LINKS);
        if (capped) showSelectionNotice(t(`링크/연결로는 최대 ${MAX_SELECTED_LINKS}개까지 선택할 수 있습니다.`, `You can select up to ${MAX_SELECTED_LINKS} links/connectors.`));
        return next;
      });
      return;
    }
    const otherChannelsEmpty = selectedNodeIdsRef.current.length === 0 && selectedElementsRef.current.length === 0;
    setSelectedLinks((prev) => replaceWith(prev, item, linkKey, otherChannelsEmpty));
    setSelectedNodeIds([]);
    setSelectedElements([]);
  }, [showSelectionNotice, t]);

  const removeLinkSelection = useCallback((linkId: string) => {
    setSelectedLinks((prev) => prev.filter((link) => link.id !== linkId));
  }, []);

  const handleElementClick = useCallback((ref: NetworkElementRef, additive: boolean) => {
    setDiagramSearchSelection(null);
    if (additive) {
      setSelectedElements((prev) => {
        const { next, capped } = toggleItem(prev, ref, elementKey, MAX_SELECTED_ELEMENTS);
        if (capped) showSelectionNotice(t(`요소는 최대 ${MAX_SELECTED_ELEMENTS}개까지 선택할 수 있습니다.`, `You can select up to ${MAX_SELECTED_ELEMENTS} elements.`));
        return next;
      });
      return;
    }
    const otherChannelsEmpty = selectedNodeIdsRef.current.length === 0 && selectedLinksRef.current.length === 0;
    setSelectedElements((prev) => replaceWith(prev, ref, elementKey, otherChannelsEmpty));
    setSelectedNodeIds([]);
    setSelectedLinks([]);
  }, [showSelectionNotice, t]);

  const removeElementSelection = useCallback((ref: NetworkElementRef) => {
    setSelectedElements((prev) => prev.filter((el) => !(el.type === ref.type && el.id === ref.id)));
  }, []);

  // Shift+drag box-select (Section C) — the viewer reports everything the
  // box touched, RAW; this is where the caps actually apply (the SAME
  // toggleItem/addAllCapped helpers every other selection channel uses), so
  // a box that oversells any one category is truncated with a notice rather
  // than silently accepted or silently dropped. Mirrors /network page's
  // handleBoxSelect exactly.
  const handleBoxSelect = useCallback(
    (hits: { nodes: { id: string; name: string }[]; links: NetworkLinkRef[]; elements: NetworkElementRef[] }) => {
      setDiagramSearchSelection(null);
      const nodesResult = hits.nodes.length
        ? addAllCapped(selectedNodeIdsRef.current, hits.nodes.map((n) => n.id), nodeKey, MAX_SELECTED_NODES)
        : null;
      const linksResult = hits.links.length
        ? addAllCapped(selectedLinksRef.current, hits.links, linkKey, MAX_SELECTED_LINKS)
        : null;
      const elementsResult = hits.elements.length
        ? addAllCapped(selectedElementsRef.current, hits.elements, elementKey, MAX_SELECTED_ELEMENTS)
        : null;
      if (nodesResult) {
        setSelectedNodeIds(nodesResult.next);
        setNodeNamesById((prev) => {
          const next = { ...prev };
          for (const node of hits.nodes) next[node.id] = node.name;
          return next;
        });
      }
      if (linksResult) setSelectedLinks(linksResult.next);
      if (elementsResult) setSelectedElements(elementsResult.next);
      const totalOmitted = (nodesResult?.omitted ?? 0) + (linksResult?.omitted ?? 0) + (elementsResult?.omitted ?? 0);
      if (totalOmitted > 0) {
        showSelectionNotice(
          t(`선택 개수 제한으로 ${totalOmitted}개 항목이 추가되지 않았습니다.`, `${totalOmitted} item(s) were not added because of the selection limit.`),
        );
      }
    },
    [showSelectionNotice, t],
  );

  // Plain click on empty diagram background clears the whole selection.
  const clearAllSelection = useCallback(() => {
    setSelectedNodeIds([]);
    setSelectedLinks([]);
    setSelectedElements([]);
  }, []);

  // Network search selection (v2 UX unit, 2026-07-28): mirrors /network's
  // handleSearchSelect, minus the URL-focus precedence dance that page needs
  // and this one doesn't (see the state comment above). Fires on EVERY
  // selection, including the same hit picked twice in a row -- the fresh
  // nonce minted here is what makes a repeat selection re-flash.
  const diagramSearchPulsing = useFocusPulse(diagramSearchSelection?.key ?? "");
  const diagramFocusFlash = useMemo(
    () => (diagramSearchSelection ? buildFocusFlash(diagramSearchSelection.elements, diagramSearchPulsing) : null),
    [diagramSearchSelection, diagramSearchPulsing],
  );
  const diagramZoomToBbox = diagramSearchSelection
    ? { ...diagramSearchSelection.bbox, nonce: diagramSearchSelection.key }
    : null;
  const handleDiagramSearchSelect = useCallback((selection: NetworkSearchSelection) => {
    setDiagramSearchSelection({
      key: nextFocusNonce(),
      bbox: selection.bbox,
      elements: selection.elements,
      hit: selection.hit,
    });
  }, []);

  const handleMapViewChange = useCallback((view: "map" | "diagram") => {
    setMapView(view);
    // First switch to the diagram arms the fetch effect below; once armed it
    // stays armed so later dataset changes keep the diagram in sync even while
    // the map view is showing.
    if (view === "diagram") setHasRequestedDiagram(true);
  }, []);

  // Unit #21: replay overlays the 도식 canvas only (TrajectoryLayer draws
  // through the same ViewProjection the diagram's own zoom emits -- there is
  // no such transform on the Leaflet map). Pressing 재생 준비 while the map
  // view is showing switches to the diagram FIRST so the click always leads
  // somewhere visible instead of loading frames behind a view that cannot
  // draw them; it never stays a dead button the way a disabled control would.
  const handleLoadReplay = useCallback(() => {
    if (mapView !== "diagram") handleMapViewChange("diagram");
    playbackEngine.load(replayJob ? replayJob.id : undefined);
  }, [handleMapViewChange, mapView, playbackEngine, replayJob]);

  // Lazily load the raw-coordinate network for the diagram view, and refetch it
  // whenever the active dataset (datasetRevision) changes or a retry is
  // requested. Stays inert until the diagram has been opened at least once.
  useEffect(() => {
    if (!hasRequestedDiagram) return;
    let cancelled = false;
    setNetwork(null);
    setNetworkError(null);
    setNetworkLoading(true);
    fetchNetwork(agentSessionId ?? undefined)
      .then((data) => {
        if (!cancelled) setNetwork(data);
      })
      .catch(() => {
        if (!cancelled) setNetworkError(t("네트워크 도식을 불러오지 못했습니다.", "Failed to load the network diagram."));
      })
      .finally(() => {
        if (!cancelled) setNetworkLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetRevision, hasRequestedDiagram, networkReloadNonce, agentSessionId, t]);

  // Unit #21: which completed runs are even eligible to replay -- independent
  // of `hasRequestedDiagram` above (the diagram may not have been opened yet,
  // but the replay panel still needs to know whether ANY run is replayable to
  // choose between its three states). Filtered exactly like /network's former
  // picker was: done, trajectory recorded, and run against the SAME dataset
  // revision currently active -- a job from a stale/replaced dataset must
  // never be offered.
  useEffect(() => {
    let cancelled = false;
    fetchSimulationJobs()
      .then((jobs) => {
        if (cancelled) return;
        setReplayableJobs(
          jobs.filter(
            (job) =>
              job.status === "done" &&
              job.trajectory_recorded === true &&
              (!activeDataset || job.dataset_revision === activeDataset.revision),
          ),
        );
      })
      .catch(() => {
        if (!cancelled) setReplayableJobs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeDataset]);

  const handleDatasetChange = useCallback((dataset?: ActiveDataset) => {
    setActiveDataset(dataset ?? null);
    clearDashboardData();
    // Selections reference objects of the previous network — clear them so a
    // stale link/element can't be sent to the assistant against a new dataset
    // (matches /network's handleDatasetChange). The search selection is the
    // same hazard one step worse: its bbox is in the OLD network's coordinate
    // space, so keeping it would fly the diagram to a location that means
    // nothing in the new one.
    setSelectedLinks([]);
    setSelectedElements([]);
    setDiagramSearchSelection(null);
    const revision = dataset?.revision ?? String(Date.now());
    setDatasetRevision(revision);
    setDatasetLoading(false);
    setError(null);

    if (dataset?.source === "uploaded") {
      reloadScenariosAndValidation(revision);
    } else {
      setLoading(false);
    }
  }, [clearDashboardData, reloadScenariosAndValidation]);

  useEffect(() => {
    const publishAssistantContext = () => {
      window.dispatchEvent(
        new CustomEvent("vissim:page-context", {
          detail: {
            route: "/dashboard",
            label: "Dashboard context",
            selectedScenarioIds: selected,
            selectedScenarioNames: activeScenarios.map((scenario) => scenario.name),
            selectedNodeIds,
            selectedNodeNames: selectedNodeIds.map((nodeId) => nodeNamesById[nodeId] ?? `Node ${nodeId}`),
            dataset: activeDataset
              ? {
                  filename: activeDataset.filename,
                  source: activeDataset.source,
                  revision: activeDataset.revision,
                }
              : null,
            // Multi-select (v2 UX unit, 2026-07-29): the FULL current
            // selection, forwarded by FloatingAssistant as
            // selected_link_ids/selected_element(s) so the agent resolves the
            // real link(s)/element(s) instead of guessing what "this road"
            // meant. FloatingAssistant is where the singular/plural HTTP-field
            // split for elements happens (exactly one -> legacy
            // `selected_element`, two or more -> `selected_elements`) — this
            // page just publishes the list.
            selectedLinks,
            selectedElements,
            notes: [
              hasUploadedDataset ? "Uploaded VISSIM dataset is active." : "Dashboard analysis is waiting for an uploaded VISSIM dataset.",
              hasData ? "Scenario comparison results are loaded." : "Scenario comparison results are not loaded yet.",
              // Lets the floating chat explain (if asked) why some sections
              // are currently hidden — the two AI surfaces otherwise have no
              // awareness of each other's state.
              ...(curationSpec
                ? [
                    `The dashboard is currently focused by a user question (AI answer: "${curationSpec.headline || "n/a"}"). Only these sections are shown: ${curationSpec.charts.join(", ") || "none"}. The user can click "Show everything" in the curation bar to see all sections again.`,
                  ]
                : []),
              // The human-readable mirror of selectedLinks/selectedElements
              // above, kept so an answer can quote what is highlighted on
              // screen — one line per selected link, then one per element.
              ...selectedLinks.map((link) =>
                [t("도식에서", "In the diagram,"), linkKindLabel(link.isConnector, t), link.name, t("선택됨", "selected")].join(" "),
              ),
              ...selectedElements.map((element) =>
                [t("도식에서", "In the diagram,"), elementTypeLabel(element.type, t), element.name || element.id, t("선택됨", "selected")].join(
                  " ",
                ),
              ),
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
  }, [activeDataset, activeScenarios, curationSpec, hasData, hasUploadedDataset, nodeNamesById, selected, selectedElements, selectedLinks, selectedNodeIds, t]);

  return (
    <div className="mx-auto w-full max-w-[104rem] space-y-6 pb-16">
      <section className="px-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight text-white">
              {t("교통 운영 분석", "Traffic Operations Analysis")}
            </h1>
            <p className="text-sm text-slate-600">
              {t("시나리오와 교차로 성능을 지도, 통행시간, 네트워크 지표로 함께 확인합니다.", "Review scenario and intersection performance through the map, travel time, and network metrics.")}
            </p>
          </div>
          <div className="flex min-h-[34px] items-center">
              {(datasetLoading || loading) && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-600">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
                  {datasetLoading ? t("데이터셋 확인 중", "Checking dataset") : t("로딩 중", "Loading")}
                </span>
              )}
              {!datasetLoading && !hasUploadedDataset && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-600">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                  {t("파일 업로드 필요", "Upload required")}
                </span>
              )}
              {!datasetLoading && !loading && hasData && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-600">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  {activeScenarios.filter((scenario) => results[scenario.id]).length}/{activeScenarios.length} {t("시나리오 준비됨", "scenarios ready")}
                </span>
              )}
          </div>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
          {hasUploadedDataset ? (
            <DashboardScenarioPicker scenarios={scenarios} selected={selected} onChange={handleSelectedChange} />
          ) : (
            <DashboardDatasetGate />
          )}
          <VissimDatasetBanner onDatasetChange={handleDatasetChange} fullWidth className="h-full" />
        </div>
      </section>

      {error && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => loadActiveDataset()}
            className="shrink-0 rounded-lg border border-rose-300 bg-surface px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100"
          >
            {t("다시 시도", "Retry")}
          </button>
        </div>
      )}

      {hasUploadedDataset && (
        <CurationBar
          selectedScenarioIds={selected}
          selectedNodeIds={selectedNodeIds}
          disabled={!hasData}
          spec={curationSpec}
          onSpecChange={setCurationSpec}
          applicableSectionIds={applicableSectionIds}
          audience={audience}
          onAudienceChange={setAudience}
        />
      )}

      {hasUploadedDataset && !sectionVisible("map-selection") ? null : hasUploadedDataset ? (
        <section id="map-selection" className="scroll-mt-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <SectionHeader
              title={t("지도 기반 교차로 선택", "Map-Based Intersection Selection")}
              subtitle={t("지도 또는 도식 보기에서 비교할 교차로를 최대 6개까지 선택합니다.", "Select up to six intersections to compare, in map or diagram view.")}
            />
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {network?.source?.view === "working" && (
                <span className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-600">
                  {t("편집본 표시 중", "Showing edited copy")}
                </span>
              )}
              {/* Search only makes sense where the flash it drives is
                  visible -- the Leaflet map view has no NetworkViewer to
                  flash anything on. */}
              {mapView === "diagram" && <NetworkSearchBox onSelect={handleDiagramSearchSelect} className="w-56" />}
              <button
                type="button"
                onClick={() => {
                  // Also arms the (otherwise lazy) diagram fetch effect, so a
                  // refresh click works even before the diagram view has
                  // ever been opened.
                  setHasRequestedDiagram(true);
                  setNetworkReloadNonce((nonce) => nonce + 1);
                }}
                className="flex h-9 shrink-0 items-center rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600 transition hover:text-slate-900"
              >
                {t("새로고침", "Refresh")}
              </button>
              <MapViewToggle view={mapView} onChange={handleMapViewChange} />
            </div>
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_460px]">
            <div className="relative">
              {hasData && <SectionJumpIndex showValidation={realCounts.length > 0} sectionVisible={sectionVisible} />}

              <div className="relative h-[520px] overflow-hidden rounded-xl border border-slate-300/70 bg-ink shadow-[0_18px_34px_rgba(15,23,42,0.16)] xl:h-[60vh]">
                {mapView === "diagram" ? (
                  <>
                    <NetworkDiagramView
                      network={network}
                      loading={networkLoading}
                      error={networkError}
                      results={results}
                      selectedScenario={activeScenarios[0]?.id}
                      selectedNodeIds={selectedNodeIds}
                      selectedLinkIds={selectedLinks.map((link) => link.id)}
                      selectedElements={selectedElements}
                      onNodeClick={handleNodeClick}
                      onLinkClick={handleLinkClick}
                      onElementClick={handleElementClick}
                      onBoxSelect={handleBoxSelect}
                      onBackgroundClick={clearAllSelection}
                      onRetry={() => setNetworkReloadNonce((nonce) => nonce + 1)}
                      focusFlash={diagramFocusFlash}
                      zoomToBbox={diagramZoomToBbox}
                      onProjectionChange={projection.set}
                    />
                    {/* Unit #21: canvas overlay, same box as the diagram's SVG,
                        pointer-events none so zoom/pan/click all still land on
                        the SVG beneath it -- moved verbatim from /network's own
                        overlay. Mounted only in THIS branch (mapView ===
                        "diagram"), which is the documented answer to "what
                        happens on the map view": vehicles simply are not drawn
                        there (no ViewProjection exists for Leaflet), but the
                        playback clock keeps running underneath -- switching
                        back to 도식 resumes exactly where the clock is, it does
                        not restart. */}
                    {playback.frames && (
                      <TrajectoryLayer engine={playbackEngine} frames={playback.frames} projection={projection} network={network} />
                    )}
                    {/* Search-selection banner (v2 UX unit, 2026-07-28) --
                        mirrors /network's own "네트워크에서 보기" banner colour
                        (NetworkViewer.tsx's FOCUS_FLASH literal, #00c2ff), as a
                        pointer-events-auto overlay so it never steals space
                        from the grid layout below it. */}
                    {diagramSearchSelection && (
                      <div
                        data-testid="dashboard-diagram-search-banner"
                        className="pointer-events-none absolute left-3 right-3 top-3 z-[900] flex flex-wrap items-center gap-2 rounded-lg border border-[#00c2ff]/50 bg-[#00c2ff]/10 px-3 py-2 text-xs text-slate-600 backdrop-blur-sm"
                      >
                        <span aria-hidden="true" className="inline-block h-2 w-2 shrink-0 rounded-full bg-[#00c2ff]" />
                        <span className="font-semibold text-slate-700">
                          {t("검색 결과 강조", "Search result highlighted", "検索結果をハイライト")}
                        </span>
                        <span className="text-slate-500">{searchHitDisplayLabel(diagramSearchSelection.hit, t)}</span>
                        <button
                          type="button"
                          onClick={() => setDiagramSearchSelection(null)}
                          className="pointer-events-auto ml-auto shrink-0 rounded-md border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:text-slate-900"
                        >
                          {t("강조 해제", "Clear highlight", "ハイライト解除")}
                        </button>
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <DashboardMap
                      summary={summary}
                      selectedNodeId={activeNodeId}
                      selectedNodeIds={selectedNodeIds}
                      selectedScenarioIds={selected}
                      results={results}
                      datasetRevision={datasetRevision}
                      onNodeClick={handleNodeClick}
                    />
                    {loading && (
                      <div className="absolute inset-0 z-[1000] flex items-center justify-center bg-blue-50/75 backdrop-blur-sm">
                        <div className="flex items-center gap-2 rounded-full border border-slate-300/70 bg-surface/90 px-4 py-2 text-sm font-medium text-slate-700">
                          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                          {t("지도 데이터 로딩 중", "Loading map data")}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>

            <div className="flex h-[520px] min-h-0 flex-col overflow-hidden rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)] xl:h-[60vh]">
              <SelectedIntersectionPanel
                selectedNodeIds={selectedNodeIds}
                nodeNamesById={nodeNamesById}
                maxSelectedNodes={MAX_SELECTED_NODES}
                onRemove={handleRemoveNode}
              />

              <div className="mt-4 min-h-0 flex-1 overflow-hidden border-t border-slate-100 pt-4">
                {activeNodeId && hasData ? (
                  <NodeFlowPanel
                    nodeId={activeNodeId}
                    nodeName={activeNodeName}
                    scenarios={scenarios}
                    selected={selected}
                    results={results}
                  />
                ) : (
                  <EmptyNodePanel />
                )}
              </div>
            </div>
          </div>

          {/* Unit #21: replay's own panel, renders directly under the 도식 it
              overlays -- run context (which run it is anchored to) lives right
              here, next to the map/diagram it draws on top of, rather than as
              a floating card elsewhere on the page. */}
          <VehicleReplaySection
            replayJob={replayJob}
            hasReplayableRuns={replayableJobs.length > 0}
            primaryScenarioName={primaryScenario?.name ?? null}
            playback={playback}
            playbackEngine={playbackEngine}
            onLoad={handleLoadReplay}
          />

          {/* Link/element selection chips (v2 UX unit, 2026-07-29) — nodes
              already have their own chip panel above (SelectedIntersectionPanel);
              this is the link/element counterpart, only meaningful once the
              diagram (not the Leaflet map) is showing. Reuses the same
              rounded-pill idiom. */}
          {mapView === "diagram" && (selectedLinks.length > 0 || selectedElements.length > 0) && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-200 bg-surface/70 px-3 py-2">
              {selectedLinks.map((link) => (
                <button
                  key={`link-${link.id}`}
                  type="button"
                  onClick={() => removeLinkSelection(link.id)}
                  className="rounded-full border border-slate-300 bg-surface px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                >
                  {linkKindLabel(link.isConnector, t)} {link.name || link.id} ×
                </button>
              ))}
              {selectedElements.map((element) => (
                <button
                  key={`el-${element.type}-${element.id}`}
                  type="button"
                  onClick={() => removeElementSelection(element)}
                  className="rounded-full border border-slate-300 bg-surface px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                >
                  {elementTypeLabel(element.type, t)} {element.name || element.id} ×
                </button>
              ))}
              <button
                type="button"
                onClick={clearAllSelection}
                className="ml-auto shrink-0 rounded-md border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:text-slate-900"
              >
                {t("모두 지우기", "Clear all")}
              </button>
            </div>
          )}
          {selectionNotice && (
            <p className="mt-2 text-[11px] text-amber-600" role="status">
              {selectionNotice}
            </p>
          )}
        </section>
      ) : (
        <DashboardUploadRequiredPanel />
      )}

      {/*
        Network KPI is shown FIRST among the results sections: unlike
        intersection performance and travel time, it never needs an
        intersection to be clicked first, so it is the fastest path to a
        decision-relevant number after the map (previously it sat behind two
        sections that render an empty state until the user picks a node).
      */}
      {hasData && sectionVisible("network-kpi-comparison") && (
        <section id="network-kpi-comparison" className="scroll-mt-6">
          <SectionHeader
            title={t("네트워크 KPI 비교", "Network KPI Comparison")}
            subtitle={t("선택한 시나리오의 주요 네트워크 지표를 상대 막대로 비교합니다.", "Compare key network indicators across selected scenarios using relative bars.")}
          />
          {kpiMetricsFilter && !kpiMetricsExpanded && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
              <span>
                {t(
                  `6개 중 ${kpiMetricsFilter.length}개 지표 표시 중`,
                  `Showing ${kpiMetricsFilter.length} of 6 metrics`,
                  `6件中${kpiMetricsFilter.length}件の指標を表示中`,
                )}
              </span>
              <span aria-hidden>·</span>
              <button
                type="button"
                onClick={() => setKpiMetricsExpanded(true)}
                className="font-semibold text-slate-600 underline decoration-dotted underline-offset-2 hover:text-slate-900"
              >
                {t("전체 지표 보기", "Show all metrics", "全指標を表示")}
              </button>
            </div>
          )}
          {minRunCount === 1 && (
            <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-800">
              {t(
                "일부 시나리오가 단일 실행(1회) 결과입니다. 무작위 변동(시드 효과) 때문에 값 차이가 시나리오 효과가 아닐 수 있습니다.",
                "Some scenarios are based on a single run. Differences may reflect random seed variation rather than the scenario itself.",
              )}
            </p>
          )}
          <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {visibleKpiMetrics.map((metric) => (
              <KpiCard
                key={metric.key}
                label={translateKpiMetricLabel(metric.key, t)}
                unit={translateKpiMetricUnit(metric.unit, t)}
                higherIsBetter={metric.higherIsBetter}
                isTotal={metric.isTotal}
                showTechnicalDetail={audience === "expert"}
                values={activeScenarios.map((scenario) => {
                  // `toFiniteNumber` alone is NOT enough here: it coerces first
                  // and `Number(null) === 0` is finite. The backend emits an
                  // explicit JSON null for any metric no run reported
                  // (`_aggregate_db_paths` -> `_safe_mean`), and it omits
                  // `_STDEV` entirely for a single-run scenario. Coercing first
                  // turned both into a measured 0 -- "0.0 s/veh Average Delay",
                  // "±0.0" -- and it made KpiCard's suppression branch
                  // (`present.length === 0` -> render nothing) unreachable.
                  // Reject null/undefined BEFORE coercion; a genuine measured 0
                  // still renders. Same failure as unit 1.5's per-approach
                  // delay, same guard as `calculateDirectionDelays`.
                  const network = results[scenario.id]?.network;
                  const rawValue = network?.[metric.key];
                  const rawStdev = network?.[`${metric.key}_STDEV`];
                  return {
                    scenario,
                    // UX #24: seconds -> vehicle-hours for the total metrics.
                    // The stdev shares the divisor so "±" stays in the same unit
                    // as the number it qualifies.
                    value: scaleKpiMetricValue(
                      rawValue == null ? null : toFiniteNumber(rawValue),
                      metric.displayDivisor,
                    ),
                    stdev: scaleKpiMetricValue(
                      rawStdev == null ? null : toFiniteNumber(rawStdev),
                      metric.displayDivisor,
                    ),
                    color: resolveScenarioColor(scenarioColorById, scenario),
                  };
                })}
              />
            ))}
          </div>
        </section>
      )}

      {hasData && sectionVisible("intersection-performance") && (
        <section id="intersection-performance" className="scroll-mt-6">
          <SectionHeader
            title={t("교차로 성능평가", "Intersection Performance Evaluation")}
            subtitle={t(
              "선택 교차로의 방향별 지체와 교차로별 대기행렬, 최악 접근로 지체, 통행량을 비교합니다. 각 막대는 해당 교차로에서 시나리오 중 최악값이며, 막대 아래 작은 글씨가 그 시나리오입니다.",
              "Compare directional delay plus intersection-level queue length, worst approach delay, and volume. Each bar is the worst value across scenarios for that intersection — the small text under it names which scenario.",
            )}
          />
          <IntersectionPerformanceGrid cards={intersectionCards} />
        </section>
      )}

      {hasData && sectionVisible("travel-time-comparison") && (
        <section id="travel-time-comparison" className="scroll-mt-6">
          <SectionHeader
            title={t("통행시간 비교분석", "Travel Time Comparison")}
            subtitle={t("Vissim 통행시간 결과를 시나리오별 선형 추세로 비교합니다.", "Compare Vissim travel-time outputs as scenario-level trend lines.")}
          />
          <TravelTimeComparison chart={travelTimeSeries} />
        </section>
      )}

      {hasData && sectionVisible("overall-results-analysis") && (
        <section id="overall-results-analysis" className="scroll-mt-6">
          <SectionHeader
            title={t("종합 결과 분석", "Overall Results Analysis")}
            subtitle={t("결과 해석과 보고서 생성만 간단하게 제공합니다.", "A short interpretation of the results, plus report generation.")}
          />
          <OverallResultsSection
            summary={summary}
            activeScenarios={activeScenarios}
            intersectionCards={intersectionCards}
            selectedNodeIds={selectedNodeIds}
            nodeNamesById={nodeNamesById}
            results={results}
            realCounts={realCounts}
            narrative={overallNarrative}
          />
        </section>
      )}

      {realCounts.length > 0 && hasData && sectionVisible("field-count-validation") && (
        <section id="field-count-validation" className="scroll-mt-6">
          <SectionHeader
            title={t("현장 계수 검증", "Validation Against Field Counts")}
            subtitle={t(
              "선택한 시나리오의 노드별 오차율과 GEH 값을 확인합니다. GEH = √(2×(모델-관측)² / (모델+관측)), 5 미만이면 우수, 10 이상이면 재검토가 필요합니다.",
              "Review per-node percentage error and GEH values for selected scenarios. GEH = √(2×(model−observed)² / (model+observed)); under 5 is good, 10 or over needs review.",
            )}
          />
          <ValidationMatrix
            realCounts={realCounts}
            scenarios={activeScenarios}
            scenarioColorById={scenarioColorById}
            summary={summary}
            onNodeClick={handleNodeClick}
            selectedNodeId={activeNodeId}
          />
        </section>
      )}
    </div>
  );
}

function SectionJumpIndex({
  showValidation,
  sectionVisible,
}: {
  showValidation: boolean;
  sectionVisible: (sectionId: string) => boolean;
}) {
  const { t } = useLanguage();
  const items = [
    { id: "network-kpi-comparison", href: "#network-kpi-comparison", label: t("KPI", "KPI") },
    { id: "intersection-performance", href: "#intersection-performance", label: t("성능평가", "Performance") },
    { id: "travel-time-comparison", href: "#travel-time-comparison", label: t("통행시간", "Travel Time") },
    { id: "overall-results-analysis", href: "#overall-results-analysis", label: t("종합결과", "Overall") },
    ...(showValidation ? [{ id: "field-count-validation", href: "#field-count-validation", label: t("현장검증", "Validation") }] : []),
    // A section hidden by the current AI focus-mode answer has no anchor on
    // the page right now, so its jump link is filtered out instead of
    // scrolling to nothing when clicked.
  ].filter((item) => sectionVisible(item.id));

  return (
    <nav
      aria-label={t("분석 섹션 바로가기", "Analysis section shortcuts")}
      className="group absolute left-0 top-24 z-[950] h-11 w-10 -translate-x-3"
    >
      <button
        type="button"
        className="absolute left-0 top-0 flex h-11 w-10 items-center justify-center rounded-r-full border border-l-0 border-slate-300 bg-surface/95 text-blue-700 shadow-[0_8px_18px_rgba(15,23,42,0.18)] backdrop-blur transition hover:bg-slate-900 hover:text-ink focus:outline-none focus:ring-2 focus:ring-accent/45"
        aria-label={t("분석 목차 열기", "Open analysis sections")}
      >
        <span className="flex flex-col gap-1">
          <span className="h-0.5 w-4 rounded-full bg-current" />
          <span className="h-0.5 w-4 rounded-full bg-current" />
          <span className="h-0.5 w-4 rounded-full bg-current" />
        </span>
      </button>

      <div className="pointer-events-none absolute right-[42px] top-0 w-[188px] -translate-x-2 opacity-0 transition duration-200 ease-out group-hover:pointer-events-auto group-hover:translate-x-0 group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:translate-x-0 group-focus-within:opacity-100">
        <div className="rounded-xl border border-slate-300/80 bg-surface/96 p-2.5 shadow-[0_16px_34px_rgba(15,23,42,0.18)] backdrop-blur-md">
          <div className="mb-2 flex items-center justify-between border-b border-slate-200/80 pb-2">
            <div>
              <p className="text-[10px] font-semibold text-slate-500">{t("분석 목차", "Sections")}</p>
              <p className="mt-0.5 text-[11px] font-bold text-slate-900">{t("빠른 이동", "Quick Jump")}</p>
            </div>
            <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-bold text-accent">
              {items.length}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            {items.map((item, index) => (
              <a
                key={item.href}
                href={item.href}
                className="group/link grid h-8 grid-cols-[1.25rem_1fr] items-center gap-2 rounded-md px-1.5 text-[11px] font-semibold text-slate-700 transition hover:bg-blue-100 hover:text-blue-700 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-accent/45"
              >
                <span className="flex h-5 w-5 items-center justify-center rounded-md border border-slate-200 bg-surface text-[10px] font-bold text-slate-500 shadow-sm transition group-hover/link:border-accent group-hover/link:bg-accent group-hover/link:text-white">
                  {index + 1}
                </span>
                <span className="truncate">{item.label}</span>
              </a>
            ))}
          </div>
        </div>
      </div>
    </nav>
  );
}

// Triggers the real file picker owned by the (already-rendered elsewhere)
// VissimDatasetBanner instance. Not a prop callback: <DashboardDatasetGate />
// and <DashboardUploadRequiredPanel /> are called with no props by contract
// (see dashboard-contract.test.ts), so the empty-state gate reaches the
// actual upload control this way instead of only describing it in prose.
function openDatasetFilePicker() {
  document.querySelector<HTMLInputElement>('input[type="file"][accept=".inpx,.zip"]')?.click();
}

function DashboardDatasetGate() {
  const { t } = useLanguage();

  return (
    <div className="flex h-full items-center rounded-xl border border-dashed border-amber-300 bg-amber-50 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-amber-700">
            {t("대시보드 연결 대기", "Dashboard Waiting for Dataset")}
          </p>
          <p className="mt-1 text-sm text-amber-800">
            {t(
              "VISSIM 파일 또는 프로젝트 ZIP을 업로드하면 시나리오 선택과 분석 지도가 활성화됩니다.",
              "Upload a VISSIM file or project ZIP to enable scenario selection and the analysis map.",
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={openDatasetFilePicker}
            className="rounded-full border border-transparent bg-amber-500 px-3 py-1.5 text-xs font-bold text-ink transition hover:bg-amber-600"
          >
            {t("지금 업로드", "Upload now")}
          </button>
          <span className="rounded-full border border-amber-300 bg-surface px-3 py-1 text-xs font-bold text-amber-600">
            INPX / ZIP
          </span>
        </div>
      </div>
    </div>
  );
}

function DashboardUploadRequiredPanel() {
  const { t } = useLanguage();

  return (
    <section className="rounded-xl border border-dashed border-slate-300 bg-surface/78 px-6 py-12 text-center shadow-sm">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-amber-300 bg-amber-50 text-sm font-black text-amber-600">
        DATA
      </div>
      <h2 className="mt-4 text-[15px] font-semibold text-white">
        {t("업로드한 VISSIM 데이터셋이 필요합니다", "Uploaded VISSIM Dataset Required")}
      </h2>
      <p className="mx-auto mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
        {t(
          "기본 샘플 데이터 대신 사용자가 올린 네트워크와 시나리오 결과를 기준으로 대시보드를 연결합니다.",
          "The dashboard now connects to the network and scenario outputs from the dataset you upload instead of the default sample data.",
        )}
      </p>
      <button
        type="button"
        onClick={openDatasetFilePicker}
        className="ui-button-primary mt-4 inline-flex items-center px-4 py-2 text-sm"
      >
        {t("VISSIM 파일 업로드", "Upload a VISSIM file")}
      </button>
    </section>
  );
}

function SelectedIntersectionPanel({
  selectedNodeIds,
  nodeNamesById,
  maxSelectedNodes,
  onRemove,
}: {
  selectedNodeIds: string[];
  nodeNamesById: Record<string, string>;
  maxSelectedNodes: number;
  onRemove: (nodeId: string) => void;
}) {
  const { t } = useLanguage();

  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            {t("교차로 비교분석", "Intersection Comparison")}
          </p>
          <h2 className="mt-0.5 text-base font-bold text-slate-900">
            {t("선택된 교차로", "Selected Intersections")}
          </h2>
        </div>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-600">
          {selectedNodeIds.length}/{maxSelectedNodes}
        </span>
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
        {t(
          "지도에서 교차로를 클릭하면 비교 목록에 추가됩니다. 다시 클릭하면 제거됩니다.",
          "Click intersections on the map to add them to the comparison list. Click again to remove.",
        )}
      </p>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {selectedNodeIds.length === 0 && (
          <span className="rounded-full border border-dashed border-slate-300 bg-surface px-2.5 py-1 text-[11px] text-slate-500">
            {t("선택 없음", "None selected")}
          </span>
        )}

        {selectedNodeIds.map((nodeId, index) => (
          <button
            key={nodeId}
            type="button"
            onClick={() => onRemove(nodeId)}
            className="rounded-full border border-slate-300 bg-surface px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
          >
            {index + 1}. {nodeNamesById[nodeId] ?? `Node ${nodeId}`} x
          </button>
        ))}
      </div>
    </div>
  );
}

function IntersectionPerformanceGrid({ cards }: { cards: IntersectionPerformanceCard[] }) {
  const { t } = useLanguage();

  if (!cards.length) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-surface/85 px-6 py-10 text-center text-sm text-slate-500">
        {t("지도에서 교차로를 선택하면 성능평가 카드가 표시됩니다.", "Select intersections on the map to show performance cards.")}
      </div>
    );
  }

  const queueValues = cards.map((card) => ({
    nodeId: card.nodeId,
    nodeName: card.nodeName,
    value: card.maxQueueLength,
    scenarioName: card.maxQueueScenarioName,
  }));
  const worstDelayValues = cards.map((card) => ({
    nodeId: card.nodeId,
    nodeName: card.nodeName,
    value: card.worstApproachDelay,
    scenarioName: card.worstDelayScenarioName,
  }));

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <IntersectionMetricBars
          title={t("최대 대기행렬", "Max Q Length")}
          unit="m"
          values={queueValues}
          valueFormatter={(value) => `${formatNumber(value, value < 10 ? 1 : 0)}m`}
        />
        <IntersectionMetricBars
          title={t("최악 접근로 지체", "Worst Approach Delay")}
          unit="s"
          values={worstDelayValues}
          valueFormatter={(value) => `${formatNumber(value, value < 10 ? 1 : 0)}s`}
        />
      </div>

      <VolumeByIntersectionChart cards={cards} />

      <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">
        {cards.map((card, cardIndex) => (
          <article
            key={card.nodeId}
            className="rounded-xl border border-slate-300/70 bg-surface/95 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]"
          >
            <div className="mb-3 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                  {t("교차로", "Intersection")} {cardIndex + 1}
                </p>
                <h3 className="truncate text-base font-bold text-slate-900">{card.nodeName}</h3>
              </div>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                Node {card.nodeId}
              </span>
            </div>
            <DirectionDelayChart values={card.delayByDirection} />
          </article>
        ))}
      </div>
    </div>
  );
}

function IntersectionMetricBars({
  title,
  unit,
  values,
  valueFormatter,
}: {
  title: string;
  unit: string;
  values: IntersectionMetricBarValue[];
  valueFormatter: (value: number) => string;
}) {
  const { t } = useLanguage();
  const maxValue = Math.max(...values.map((item) => item.value ?? 0), 1);

  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/95 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold text-slate-900">{title}</h3>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">
          {unit}
        </span>
      </div>

      <div className="mt-4 space-y-3">
        {values.map((item) => {
          const width = item.value != null ? Math.max(7, (item.value / maxValue) * 100) : 0;

          return (
            <div key={item.nodeId} className="grid grid-cols-[7.2rem_1fr_4.8rem] items-center gap-3">
              <span className="truncate text-[11px] font-semibold text-slate-700">{item.nodeName}</span>
              <span className="h-3 overflow-hidden bg-white/[0.05]">
                {item.value != null && (
                  <span className="block h-full" style={{ width: `${width}%`, backgroundColor: METRIC_BAR_COLOR }} />
                )}
              </span>
              <span className="text-right text-[11px] font-bold tabular-nums text-slate-900">
                {item.value != null ? valueFormatter(item.value) : "-"}
              </span>
              {item.scenarioName && (
                <span className="col-start-2 -mt-2 truncate text-[10px] text-slate-500">{item.scenarioName}</span>
              )}
            </div>
          );
        })}
      </div>

      {!values.some((item) => item.value != null) && (
        <p className="mt-3 text-[11px] text-slate-500">
          {t("표시할 교차로 데이터가 없습니다.", "No intersection data is available.")}
        </p>
      )}
    </div>
  );
}

function DirectionDelayChart({
  values,
}: {
  values: Array<{ direction: DirectionKey; value: number | null; los: LosGrade | null }>;
}) {
  const { t } = useLanguage();
  const maxValue = Math.max(...values.map((item) => item.value ?? 0), 1);
  // Unit 1.5 D5: these are MEASURED values now (VEHS-weighted VEHDELAY per
  // approach), so the "추정" badge and disclaimer that used to sit here are
  // gone. When nothing was measured we say so instead of drawing a bar.
  const hasMeasured = values.some((item) => item.value != null);
  const delayUnit = t("초/대", "s/veh");

  return (
    <div className="min-h-[190px] rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-[12px] font-bold text-slate-800">{t("방향별 지체", "Delay by Direction")}</p>
        <span
          className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-600"
          title={t(
            "이동류별 측정 지체(VEHDELAY)를 접근방향별로 교통량 가중 평균한 실측값입니다.",
            "Measured: per-movement VEHDELAY, volume-weighted into each approach.",
          )}
        >
          {/* Composed from STATIC t() keys: pickByLanguage looks the Korean
              string up in JA_UI, so a template literal would never match a
              dictionary entry and would silently fall back to English. */}
          {`${t("실측", "Measured")} ${delayUnit} · LOS`}
        </span>
      </div>

      {hasMeasured ? (
        <>
          <div className="mt-4 grid h-32 grid-cols-4 items-end gap-2 border-b border-slate-200 pb-2">
            {values.map(({ direction, value, los }) => {
              const height = value != null ? Math.max(10, (value / maxValue) * 100) : 0;
              const color = los ? LOS_COLORS[los] : "#45443f";

              return (
                <div key={direction} className="flex h-full flex-col items-center justify-end gap-1">
                  <span className="text-[10px] font-bold tabular-nums text-slate-800">
                    {value != null ? `${formatNumber(value, value < 10 ? 1 : 0)}${delayUnit}` : "-"}
                  </span>
                  <div className="flex h-[86px] w-full max-w-[42px] items-end bg-white/[0.04] px-1 py-1">
                    {value != null && (
                      <span className="block w-full" style={{ height: `${height}%`, backgroundColor: color }} />
                    )}
                  </div>
                  <span className="text-[10px] font-semibold text-slate-600">
                    {t(DIRECTION_LABELS[direction].shortKo, DIRECTION_LABELS[direction].shortEn)}
                    {los ? ` / ${los}` : ""}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] text-slate-500">
            {LOS_GRADE_THRESHOLDS.map(({ grade, label }) => (
              <span key={grade} className="inline-flex items-center gap-1" title={t(`지체 ${label}`, `Delay ${label}`)}>
                <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: LOS_COLORS[grade] }} />
                {grade} <span className="text-slate-400">({label})</span>
              </span>
            ))}
          </div>
        </>
      ) : (
        <div className="mt-4 flex h-32 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-slate-300 px-2 text-center">
          <p className="text-[11px] font-bold text-slate-600">{t("데이터 없음", "No data")}</p>
          <p className="text-[9.5px] leading-snug text-slate-500">
            {t(
              "이 교차로에는 이동류별 측정 지체(VEHDELAY)가 없어 방향별 지체를 표시할 수 없습니다.",
              "No per-movement VEHDELAY was measured at this intersection, so per-direction delay cannot be shown.",
            )}
          </p>
        </div>
      )}
    </div>
  );
}

function VolumeByIntersectionChart({ cards }: { cards: IntersectionPerformanceCard[] }) {
  const { t } = useLanguage();
  const maxTotal = Math.max(...cards.map((item) => item.volumeTotal ?? 0), 1);

  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/95 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold text-slate-900">{t("통행량 구성", "Volume Composition")}</h3>
        {/* Sibling unit chip swept with UX #24 -- ko primary like the rest. */}
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">{t("대", "veh")}</span>
      </div>

      <div className="mt-4 space-y-3">
        {cards.map((card) => (
          <div key={card.nodeId} className="grid grid-cols-[7.2rem_1fr_4.8rem] items-center gap-3">
            <span className="truncate text-[11px] font-semibold text-slate-700">{card.nodeName}</span>
            <div className="h-4 overflow-hidden bg-white/[0.05]">
              {card.volumeTotal != null && card.volumeTotal > 0 && (
                <div className="flex h-full" style={{ width: `${Math.max(8, (card.volumeTotal / maxTotal) * 100)}%` }}>
                  {DIRECTION_ORDER.map((direction) => {
                    const value = card.volumeDirections[direction] ?? 0;
                    const width = card.volumeTotal && card.volumeTotal > 0 ? (value / card.volumeTotal) * 100 : 0;
                    return (
                      <span
                        key={direction}
                        className="h-full"
                        title={`${t(DIRECTION_LABELS[direction].ko, DIRECTION_LABELS[direction].en)}: ${Math.round(value).toLocaleString()}`}
                        style={{ width: `${width}%`, backgroundColor: DIRECTION_COLORS[direction] }}
                      />
                    );
                  })}
                </div>
              )}
            </div>
            <span className="text-right text-[11px] font-bold tabular-nums text-slate-900">
              {card.volumeTotal != null ? Math.round(card.volumeTotal).toLocaleString() : "-"}
            </span>
          </div>
        ))}
      </div>

      {!cards.some((item) => item.volumeTotal != null) && (
        <p className="mt-3 text-[11px] text-slate-500">
          {t("표시할 통행량 데이터가 없습니다.", "No volume data is available.")}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          {t("방향", "Direction")}
        </span>
        {DIRECTION_ORDER.map((direction) => (
          <span
            key={direction}
            className="inline-flex items-center gap-1.5 rounded-md bg-white/[0.05] px-2 py-1 text-[11px] font-medium text-slate-700"
          >
            <span className="h-3 w-3 rounded-sm" style={{ backgroundColor: DIRECTION_COLORS[direction] }} />
            {t(DIRECTION_LABELS[direction].ko, DIRECTION_LABELS[direction].en)}
          </span>
        ))}
      </div>
    </div>
  );
}

function TravelTimeComparison({ chart }: { chart: TravelTimeChart }) {
  const { t } = useLanguage();
  const { series, labels, truncated, useSectionLabels } = chart;
  const populated = series.filter((item) => item.points.some((point) => point.value != null));
  const allValues = populated.flatMap((item) => item.points.map((point) => point.value)).filter((value): value is number => value != null);

  if (!populated.length || !allValues.length || !labels.length) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-surface/85 px-6 py-10 text-center text-sm text-slate-500">
        <p>
          {t(
            "통행시간 측정 결과가 없어 그래프를 표시할 수 없습니다.",
            "No travel-time measurements are available for the chart.",
          )}
        </p>
        <p className="mt-1 text-xs text-slate-400">
          {t(
            "VISSIM 네트워크에 통행시간 측정 구간(Vehicle Travel Time Measurement)이 정의되어 있고 시뮬레이션이 완료됐는지 확인하세요.",
            "Check that the VISSIM network defines Vehicle Travel Time Measurement sections and that the simulation run has finished.",
          )}
        </p>
      </div>
    );
  }

  const minValue = Math.min(...allValues);
  const maxValue = Math.max(...allValues);
  const valueSpan = Math.max(maxValue - minValue, 1);
  const left = 48;
  const right = 24;
  const top = 28;
  const bottom = 58;
  const height = 260;
  // Give every label its own ~46px of horizontal room so tick text doesn't
  // collide, instead of squeezing an arbitrary point count into a fixed width.
  const width = Math.max(760, left + right + labels.length * 46);
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;

  const xForIndex = (index: number) => left + (index / Math.max(labels.length - 1, 1)) * plotWidth;
  const yForValue = (value: number) => top + ((maxValue - value) / valueSpan) * plotHeight;

  // Break the line at gaps (a scenario missing a label its peers have)
  // instead of interpolating across missing measurements.
  function buildSegments(points: TravelTimeSeries["points"]): Array<Array<[number, number]>> {
    const segments: Array<Array<[number, number]>> = [];
    let current: Array<[number, number]> = [];
    points.forEach((point, index) => {
      if (point.value == null) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      current.push([xForIndex(index), yForValue(point.value)]);
    });
    if (current.length) segments.push(current);
    return segments;
  }

  // Thin tick labels out when there isn't room to show every one legibly.
  const tickStride = labels.length > 14 ? Math.ceil(labels.length / 14) : 1;

  return (
    <div className="mt-4 rounded-xl border border-slate-300/70 bg-surface/92 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      {truncated && (
        <p className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-800">
          {t(
            `표시 구간이 많아 처음 ${TRAVEL_TIME_MAX_POINTS}개만 표시합니다.`,
            `Showing the first ${TRAVEL_TIME_MAX_POINTS} of more available sections/intervals.`,
          )}
        </p>
      )}
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${width} ${height}`} style={{ minWidth: `${width}px` }}>
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const y = top + ratio * plotHeight;
            const labelValue = maxValue - ratio * valueSpan;
            return (
              <g key={ratio}>
                <line x1={left} x2={width - right} y1={y} y2={y} stroke="#33332f" strokeDasharray="4 5" />
                <text x={left - 8} y={y + 4} textAnchor="end" fontSize="10" fill="#898781">
                  {formatNumber(labelValue, 0)}
                </text>
              </g>
            );
          })}

          <line x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} stroke="#45443f" />
          <line x1={left} x2={left} y1={top} y2={height - bottom} stroke="#45443f" />

          {labels.map((label, index) =>
            index % tickStride === 0 ? (
              <text
                key={label}
                x={xForIndex(index)}
                y={height - bottom + 16}
                textAnchor="end"
                fontSize="9.5"
                fill="#898781"
                transform={`rotate(-38 ${xForIndex(index)} ${height - bottom + 16})`}
              >
                {label.length > 12 ? `${label.slice(0, 11)}…` : label}
              </text>
            ) : null,
          )}

          {populated.map((item) => {
            const color = item.color;
            const segments = buildSegments(item.points);
            return (
              <g key={item.scenario.id}>
                {segments.map((segment, segmentIndex) =>
                  // A lone point has no line to draw, so mark it with a dot;
                  // multi-point segments render as clean lines with no per-point
                  // markers (Console-style — the previous double end-dots read
                  // as clutter).
                  segment.length === 1 ? (
                    <circle
                      key={segmentIndex}
                      cx={segment[0][0]}
                      cy={segment[0][1]}
                      r="2.5"
                      fill={color}
                    />
                  ) : (
                    <polyline
                      key={segmentIndex}
                      points={segment.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}
                      fill="none"
                      stroke={color}
                      strokeWidth="2"
                      strokeLinejoin="round"
                    />
                  ),
                )}
              </g>
            );
          })}

          <text x={left} y={20} fontSize="10" fill="#898781">
            {useSectionLabels
              ? t("측정 구간별 통행시간", "Travel time by measurement section")
              : t("시간 간격별 통행시간", "Travel time by time interval")}
          </text>
          <text x={left - 34} y={top - 8} fontSize="10" fill="#898781">
            sec
          </text>
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {populated.map((item) => (
          <span key={item.scenario.id} className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-2.5 py-1 text-[11px] font-semibold text-slate-600">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: item.color }} />
            {item.scenario.name}
          </span>
        ))}
      </div>
    </div>
  );
}

function OverallResultsSection({
  summary,
  activeScenarios,
  intersectionCards,
  selectedNodeIds,
  nodeNamesById,
  results,
  realCounts,
  narrative,
}: {
  summary: CompareSummaryResponse | null;
  activeScenarios: ScenarioMeta[];
  intersectionCards: IntersectionPerformanceCard[];
  selectedNodeIds: string[];
  nodeNamesById: Record<string, string>;
  results: Record<string, ScenarioResults>;
  realCounts: NodeRealCounts[];
  narrative: string;
}) {
  const { t } = useLanguage();
  const [reportMarkdown, setReportMarkdown] = useState("");
  const [reportGeneratedAt, setReportGeneratedAt] = useState<Date | null>(null);
  const canGenerateReport = activeScenarios.length > 0 && intersectionCards.length > 0;

  useEffect(() => {
    let cancelled = false;
    Promise.resolve().then(() => {
      if (cancelled) return;
      setReportMarkdown("");
      setReportGeneratedAt(null);
    });
    return () => {
      cancelled = true;
    };
  }, [activeScenarios, intersectionCards, results, summary, realCounts, narrative]);

  const handleGenerateReport = useCallback(() => {
    const generatedAt = new Date();
    const markdown = buildCalibrationReportMarkdown({
      scenarios: activeScenarios,
      intersectionCards,
      selectedNodeIds,
      nodeNamesById,
      results,
      summary,
      realCounts,
      generatedAt,
      comparisonNarrative: narrative,
    });

    setReportMarkdown(markdown);
    setReportGeneratedAt(generatedAt);
  }, [activeScenarios, intersectionCards, narrative, nodeNamesById, realCounts, results, selectedNodeIds, summary]);

  const handleDownloadReport = useCallback(() => {
    if (!reportMarkdown) return;

    const blob = new Blob([reportMarkdown], { type: "text/markdown;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const stamp = formatDateForFilename(reportGeneratedAt ?? new Date());
    anchor.href = href;
    anchor.download = `base-model-calibration-validation-${stamp}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(href);
  }, [reportGeneratedAt, reportMarkdown]);

  const handleDownloadWordReport = useCallback(() => {
    if (!reportMarkdown) return;

    const html = buildWordDocumentHtml(reportMarkdown);
    const blob = new Blob([html], { type: "application/msword;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const stamp = formatDateForFilename(reportGeneratedAt ?? new Date());
    anchor.href = href;
    anchor.download = `base-model-calibration-validation-${stamp}.doc`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(href);
  }, [reportGeneratedAt, reportMarkdown]);

  return (
    <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <section className="rounded-xl border border-slate-300/70 bg-surface/95 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm font-bold text-slate-900">{t("결과 해석", "Result Interpretation")}</p>
            <p className="mt-1 text-[11px] text-slate-500">
              {t("현재 선택값 기준으로 최종 판단 문장만 정리합니다.", "A concise decision statement from the current selection.")}
            </p>
          </div>
          <span className="rounded-full bg-blue-100 px-2.5 py-1 text-[10px] font-bold text-accent">
            {t("핵심 요약", "Summary")}
          </span>
        </div>

        <p className="mt-4 max-w-3xl text-[15px] leading-7 text-slate-700">{narrative}</p>

        {(() => {
          const ranking = summary?.scenario_ranking ?? summary?.scenarioRanking ?? [];
          if (!ranking.length) return null;
          // The narrative above names a "top" scenario by score — show the
          // actual scores and per-scenario reason instead of asking the
          // reader to trust an unverifiable one-line claim.
          return (
            <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50 text-left text-[10px] font-semibold uppercase text-slate-500">
                    <th className="px-3 py-2">{t("순위", "Rank")}</th>
                    <th className="px-3 py-2">{t("시나리오", "Scenario")}</th>
                    <th className="px-3 py-2 text-right">{t("점수", "Score")}</th>
                    <th className="px-3 py-2">{t("근거", "Reason")}</th>
                  </tr>
                </thead>
                <tbody>
                  {[...ranking]
                    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
                    .map((item) => (
                      <tr key={item.scenario_id} className="border-b border-slate-50 last:border-0">
                        <td className="px-3 py-2 font-bold tabular-nums text-slate-700">{item.rank ?? "-"}</td>
                        <td className="px-3 py-2 font-semibold text-slate-800">{item.scenario_name ?? item.scenario_id}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                          {item.score != null ? item.score.toFixed(1) : "-"}
                        </td>
                        <td className="px-3 py-2 text-slate-500">{item.reason ?? "-"}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          );
        })()}

        <div className="mt-5 flex flex-wrap gap-2 text-[11px] font-semibold text-slate-600">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
            {t("시나리오", "Scenarios")} {activeScenarios.length}
          </span>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
            {t("선택 교차로", "Intersections")} {intersectionCards.length}
          </span>
          {intersectionCards[0] && (
            <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
              {t("대표 교차로", "Primary")} {intersectionCards[0].nodeName}
            </span>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-slate-300/70 bg-surface/95 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-bold text-slate-900">{t("보고서 생성", "Report Generator")}</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              {t(
                "선택된 시나리오와 교차로 결과로 보고서 초안을 만듭니다.",
                "Create a draft report from the selected scenarios and intersections.",
              )}
            </p>
          </div>
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-bold text-slate-600">
            {intersectionCards.length}
          </span>
        </div>

        <div className="mt-4 grid gap-2">
          <button
            type="button"
            onClick={handleGenerateReport}
            disabled={!canGenerateReport}
            className="ui-button-primary px-3 py-2.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t("보고서 생성하기", "Generate Report")}
          </button>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={handleDownloadReport}
              disabled={!reportMarkdown}
              className="ui-button-ghost px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("텍스트 (.md)", "Text (.md)")}
            </button>
            <button
              type="button"
              onClick={handleDownloadWordReport}
              disabled={!reportMarkdown}
              title={t(
                "Word에서 열 때 '파일 형식이 확장자와 다릅니다'라는 확인 창이 뜰 수 있습니다 — 정상이며 그대로 여세요.",
                "Word may show a \"file format doesn't match extension\" prompt when opening this — that's expected, open it anyway.",
              )}
              className="ui-button-ghost px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("Word (.doc)", "Word (.doc)")}
            </button>
          </div>
        </div>

        <p className="mt-3 text-[10px] leading-relaxed text-slate-500">
          {reportGeneratedAt
            ? t(
                `${formatDisplayDateTime(reportGeneratedAt, "ko")} 생성됨. Word 파일을 열 때 형식 확인 창이 뜨면 그대로 열면 됩니다.`,
                `Generated ${formatDisplayDateTime(reportGeneratedAt, "en")}. If Word shows a format-mismatch prompt, open it anyway.`,
                `${formatDisplayDateTime(reportGeneratedAt, "ja")} 生成済み`,
              )
            : canGenerateReport
              ? t("먼저 생성한 뒤 원하는 형식으로 내려받으세요.", "Generate first, then download the preferred format.")
              : t("교차로를 1개 이상 선택하면 생성할 수 있습니다.", "Select at least one intersection to generate a report.")}
        </p>
        <Link
          href="/simulation"
          className="mt-3 inline-block text-[10px] font-semibold text-slate-400 underline decoration-dotted underline-offset-2 hover:text-slate-600"
        >
          {t("고급: 시뮬레이션 실행기 열기 →", "Advanced: open the Simulation Runner →")}
        </Link>
      </section>
    </div>
  );
}

function KpiCard({
  label,
  unit,
  higherIsBetter,
  isTotal,
  showTechnicalDetail,
  values,
}: {
  label: string;
  unit: string;
  higherIsBetter: boolean;
  isTotal: boolean;
  /** "전문가" mode: show ±stdev, noise caveats, and range/diff detail rows.
   * "쉬운 설명" keeps the same numbers but drops the statistical fine print. */
  showTechnicalDetail: boolean;
  /** Resolved via resolveScenarioColor(scenarioColorById, scenario) by the
   * caller -- always the scenario's full-list color, never derived from this
   * array's own position (that position only reflects the current selection). */
  values: { scenario: ScenarioMeta; value: number | null; stdev: number | null; color: string }[];
}) {
  const { t } = useLanguage();
  const present = values.filter((item) => item.value != null);
  if (!present.length) return null;

  const nums = present.map((item) => item.value as number);
  const minVal = Math.min(...nums);
  const maxVal = Math.max(...nums);
  const spread = maxVal - minVal;
  const bestVal = higherIsBetter ? maxVal : minVal;
  // "Best" is only meaningful if the gap to the runner-up exceeds run-to-run
  // noise; without that check a single-seed difference smaller than its own
  // standard deviation was labeled "Best" as if it were a real effect.
  const sortedNums = [...nums].sort((a, b) => (higherIsBetter ? b - a : a - b));
  const runnerUpGap = sortedNums.length > 1 ? Math.abs(sortedNums[0] - sortedNums[1]) : null;
  const bestEntryStdev = present.find((item) => item.value === bestVal)?.stdev ?? null;
  const bestIsWithinNoise =
    runnerUpGap != null && bestEntryStdev != null && runnerUpGap < bestEntryStdev;

  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      <div className="mb-4 flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-800">{label}</h3>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
          {unit}
        </span>
      </div>
      <div className="overflow-x-auto pb-1">
        <div className="flex min-w-max items-end gap-3">
        {values.map(({ scenario, value, stdev, color: scenarioColor }) => {
          if (value == null) return null;

          const pct = maxVal > 0 ? (value / maxVal) * 100 : 100;

          // A minimum total is NOT "best" — a lower network-wide total can
          // simply mean fewer vehicles were served — so the Best tag is
          // suppressed on total metrics (the caveat line below explains why).
          const isBest = value === bestVal && !isTotal;
          const displayVal =
            value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value.toFixed(value < 10 ? 2 : 1);

          return (
            <div key={scenario.id} className="flex w-[76px] flex-col items-center gap-1.5">
              <div className="flex h-9 flex-col items-center justify-end gap-0.5">
                {isBest && (
                  <span
                    className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-emerald-600"
                    title={bestIsWithinNoise ? t("2위와의 차이가 표준편차보다 작습니다.", "The gap to the runner-up is smaller than the standard deviation.") : undefined}
                  >
                    {t("최적", "Best", "最良")}{showTechnicalDetail && bestIsWithinNoise ? "*" : ""}
                  </span>
                )}
                <div className="text-[11px] font-semibold tabular-nums text-slate-900">
                  {displayVal}
                  {showTechnicalDetail && stdev != null && (
                    <span className="ml-0.5 text-slate-400">±{stdev >= 10 ? Math.round(stdev) : stdev.toFixed(1)}</span>
                  )}
                </div>
              </div>
              <div className="relative flex h-24 w-7 items-end overflow-hidden border border-slate-200 bg-white/[0.04]">
                <div
                  className="w-full transition-all duration-500"
                  style={{ height: `${Math.max(pct, 8)}%`, backgroundColor: scenarioColor }}
                />
              </div>
              <div className="flex w-full items-center justify-center gap-1.5 text-[10px]">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: scenarioColor }}
                />
                <span className="max-w-[56px] truncate text-slate-700">{scenario.name}</span>
              </div>
            </div>
          );
        })}
        </div>
      </div>
      {showTechnicalDetail && bestIsWithinNoise && (
        <p className="mt-2 text-[10px] text-amber-700">
          * {t("최상위 값과 2위의 차이가 실행 간 변동(표준편차)보다 작아 확정적 차이로 보기 어렵습니다.", "The gap between the top value and the runner-up is smaller than run-to-run variation, so it may not be a real difference.")}
        </p>
      )}
      {isTotal && (
        <p className="mt-1 text-[10px] text-slate-400">
          {t("네트워크 전체 합계이며 차량 수에 비례합니다 — 값이 작다고 항상 더 좋은 것은 아닙니다.", "A network-wide total that scales with vehicle count — a lower value isn't always a better outcome.")}
        </p>
      )}
      {spread > 0 && (
        <p className="mt-1 text-[10px] text-slate-500">
          {t("범위", "Range")} {formatNumber(minVal, 1)} ~ {formatNumber(maxVal, 1)} ({t("차이", "diff")} {formatNumber(spread, 1)})
        </p>
      )}
    </div>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div>
      <h2 className="text-[15px] font-semibold text-white">{title}</h2>
      <p className="mt-0.5 text-sm text-slate-600">{subtitle}</p>
    </div>
  );
}

// Segmented text control that swaps the map section between the Leaflet map and
// the raw-coordinate NetworkViewer diagram. Mirrors the CurationBar audience
// toggle's styling so it reads as the same family of control.
function MapViewToggle({
  view,
  onChange,
}: {
  view: "map" | "diagram";
  onChange: (view: "map" | "diagram") => void;
}) {
  const { t } = useLanguage();
  const options: Array<{ key: "map" | "diagram"; label: string }> = [
    { key: "map", label: t("지도", "Map", "地図") },
    { key: "diagram", label: t("도식", "Diagram", "ダイアグラム") },
  ];

  return (
    <div className="flex h-9 shrink-0 items-center gap-0.5 rounded-lg border border-slate-200 p-1 text-xs font-medium">
      {options.map((option) => {
        const active = view === option.key;
        return (
          <button
            key={option.key}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.key)}
            className={`flex h-full items-center rounded-md px-3 transition-colors ${
              active ? "bg-white/10 text-white" : "text-slate-500 hover:text-slate-800"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

// Diagram-view content inside the map wrapper: its own loading and fetch-failure
// states (muted text + a retry affordance matching the page's other controls),
// falling through to NetworkViewer once the network is loaded.
function NetworkDiagramView({
  network,
  loading,
  error,
  results,
  selectedScenario,
  selectedNodeIds,
  selectedLinkIds,
  selectedElements,
  onNodeClick,
  onLinkClick,
  onElementClick,
  onBoxSelect,
  onBackgroundClick,
  onRetry,
  focusFlash,
  zoomToBbox,
  onProjectionChange,
}: {
  network: NetworkData | null;
  loading: boolean;
  error: string | null;
  results?: Record<string, ScenarioResults>;
  selectedScenario?: string;
  selectedNodeIds: string[];
  selectedLinkIds: string[];
  selectedElements: NetworkElementRef[];
  onNodeClick: (nodeId: string, nodeName: string, additive: boolean) => void;
  onLinkClick: (linkId: string, linkName: string, isConnector: boolean, additive: boolean) => void;
  onElementClick: (ref: NetworkElementRef, additive: boolean) => void;
  onBoxSelect: (hits: { nodes: { id: string; name: string }[]; links: NetworkLinkRef[]; elements: NetworkElementRef[] }) => void;
  onBackgroundClick: () => void;
  onRetry: () => void;
  // Network search selection (v2 UX unit, 2026-07-28) -- same shape as
  // NetworkViewer's own `focusFlash`/`zoomToBbox` props (components/
  // NetworkViewer.tsx), forwarded straight through so the dashboard's 도식
  // view can flash/zoom a search hit exactly like /network does.
  focusFlash?: { linkIds: string[]; elementKeys: string[]; pulsing: boolean } | null;
  zoomToBbox?: { x_min: number; y_min: number; x_max: number; y_max: number; nonce?: number | string } | null;
  // Unit #21: forwarded straight through to NetworkViewer -- the ONLY channel
  // between its live zoom transform and the vehicle-replay canvas overlay
  // mounted alongside this component (see the call site). Optional so every
  // OTHER caller of NetworkViewer that doesn't need replay is unaffected.
  onProjectionChange?: (projection: ViewProjection | null) => void;
}) {
  const { t } = useLanguage();

  if (error) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
        <p className="text-sm text-slate-400">{error}</p>
        <button
          type="button"
          onClick={onRetry}
          className="rounded-lg border border-slate-300/70 bg-surface/90 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-surface"
        >
          {t("다시 시도", "Retry")}
        </button>
      </div>
    );
  }

  if (loading || !network) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <p className="text-sm text-slate-400">{t("네트워크 도식을 불러오는 중…", "Loading network diagram…")}</p>
      </div>
    );
  }

  return (
    <NetworkViewer
      network={network}
      results={results}
      selectedScenario={selectedScenario}
      selectedNodeIds={selectedNodeIds}
      selectedLinkIds={selectedLinkIds}
      selectedElements={selectedElements}
      onNodeClick={onNodeClick}
      onLinkClick={onLinkClick}
      onElementClick={onElementClick}
      onBoxSelect={onBoxSelect}
      onBackgroundClick={onBackgroundClick}
      focusFlash={focusFlash}
      zoomToBbox={zoomToBbox}
      onProjectionChange={onProjectionChange}
    />
  );
}

// Unit #21 (2026-08-14, option A): the replay panel, carried over from
// /network's "차량 궤적 재생" section with its picker removed. One Panel, in
// normal flow directly under the map/diagram box (not a floating card), same
// Console-style uppercase label and control chrome the source panel already
// used -- restyled minimally, not redesigned.
//
// Exactly three mutually exclusive states:
//   1. `replayJob` set -- anchored to that run; the source line names it.
//   2. `replayJob` null AND `hasReplayableRuns` false -- no run in the whole
//      project has a recorded trajectory yet, so demo is offered, but NAMED
//      as demo (never silently substituted for a real run).
//   3. `replayJob` null AND `hasReplayableRuns` true -- the run the
//      comparison picker has selected is not one that recorded a trajectory
//      (a project-scenario baseline, or a job whose recording failed/expired).
//      A plain sentence, and -- B5-2 stage 4's rule -- NEVER a button: a
//      refusal is information, not a disabled control standing in for one.
function VehicleReplaySection({
  replayJob,
  hasReplayableRuns,
  primaryScenarioName,
  playback,
  playbackEngine,
  onLoad,
}: {
  replayJob: SimulationJob | null;
  hasReplayableRuns: boolean;
  primaryScenarioName: string | null;
  playback: Playback;
  playbackEngine: PlaybackEngine;
  onLoad: () => void;
}) {
  const { t } = useLanguage();
  const canLoad = replayJob != null || !hasReplayableRuns;

  return (
    <section className="mt-4 rounded-xl border border-slate-200 bg-surface/70 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
        {t("차량 궤적 재생", "Vehicle playback")}
      </p>

      {replayJob ? (
        <p className="mt-2 text-xs text-slate-600">
          {t("재생 대상", "Replaying", "再生対象")}{" "}
          <span className="font-medium text-slate-700">{formatJobLabel(replayJob)}</span>
        </p>
      ) : hasReplayableRuns ? (
        <p className="mt-2 text-xs text-slate-500">
          {primaryScenarioName
            ? t(
                `선택된 실행(${primaryScenarioName})에는 저장된 차량 궤적이 없습니다.`,
                `The selected run (${primaryScenarioName}) has no recorded vehicle trajectory.`,
              )
            : t("재생할 실행이 선택되지 않았습니다.", "No run is selected for playback.")}
        </p>
      ) : (
        <>
          <p className="mt-2 text-xs text-slate-600">
            {t("재생 소스", "Playback source", "再生ソース")}{" "}
            <span className="font-medium text-slate-700">{t("데모", "Demo")}</span>
          </p>
          <p className="mt-1 text-[11px] text-slate-500">
            {t(
              "재생 가능한 시뮬레이션 실행이 아직 없어 데모 데이터로 재생합니다.",
              "No replayable simulation run exists yet, so playback uses demo data.",
            )}
          </p>
        </>
      )}

      {canLoad &&
        (playback.status === "idle" ? (
          <button
            type="button"
            onClick={onLoad}
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
              onClick={onLoad}
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
        ))}
    </section>
  );
}

function DashboardScenarioPicker({
  scenarios,
  selected,
  onChange,
}: {
  scenarios: ScenarioMeta[];
  selected: string[];
  onChange: (selected: string[]) => void;
}) {
  const { t } = useLanguage();

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-slate-800">{t("시나리오 비교 세트", "Scenario Comparison Set")}</p>
          <p className="mt-0.5 text-[11px] text-slate-500">
            {t(
              "KPI, 지도, 교차로 비교에 포함할 시나리오를 선택합니다. 선택 내용은 저장되어 페이지를 이동했다가 돌아와도 유지됩니다.",
              "Choose scenarios to include in KPI, map, and node comparisons. Your selection is saved and stays intact when you navigate away and back.",
            )}
          </p>
        </div>
        <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">
          {selected.length}/{scenarios.length} {t("선택", "selected")}
        </span>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(215px,1fr))] gap-2">
        {scenarios.map((scenario, index) => {
          const active = selected.includes(scenario.id);
          const scenarioColor = getScenarioDisplayColor(scenario, index);
          const order = selected.indexOf(scenario.id) + 1;
          return (
            <button
              key={scenario.id}
              onClick={() => toggle(scenario.id)}
              className={`group flex items-center justify-between gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors duration-150 ${
                active
                  ? "border-slate-300 bg-white/[0.06]"
                  : "border-slate-200 bg-transparent hover:border-slate-300 hover:bg-white/[0.03]"
              }`}
            >
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: active ? scenarioColor : "#45443f" }}
                />
                <div className="min-w-0">
                  <p className={`truncate text-sm font-medium ${active ? "text-slate-900" : "text-slate-500"}`}>
                    {scenario.name}
                  </p>
                  <p className="mt-0.5 text-[10px] text-slate-500">
                    {active ? t("비교 포함", "Included") : t("클릭하여 포함", "Click to include")}
                  </p>
                </div>
              </div>
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold ${
                  active ? "bg-white/10 text-white" : "bg-slate-100 text-slate-400"
                }`}
              >
                {active ? order : "+"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function translateKpiMetricLabel(
  key: (typeof KPI_METRICS)[number]["key"],
  t: (ko: string, en: string, ja?: string) => string,
) {
  switch (key) {
    case "DELAYAVG":
      return t("평균 지체", "Average Delay");
    case "SPEEDAVG":
      return t("평균 속도", "Average Speed");
    case "TRAVTMTOT":
      return t("총 주행시간", "Total Travel Time");
    case "DELAYTOT":
      return t("총 지체", "Total Delay");
    case "VEHARR":
      return t("도착 차량수", "Arrived Vehicles");
    case "STOPSAVG":
      return t("평균 정지횟수", "Average Stops");
  }
}

// UX #24 (unit 1.5 D6): unit chips were English-only literals. Korean is the
// primary language of this product, so they go through t() like every other
// user-facing string.
function translateKpiMetricUnit(
  unit: (typeof KPI_METRICS)[number]["unit"],
  t: (ko: string, en: string, ja?: string) => string,
) {
  switch (unit) {
    case "s/veh":
      return t("초/대", "s/veh");
    case "km/h":
      return t("km/h", "km/h");
    case "veh·h":
      return t("대·시", "veh·h");
    case "veh":
      return t("대", "veh");
    case "stops/veh":
      return t("회/대", "stops/veh");
  }
}

/** Applies a KPI metric's display divisor (UX #24: seconds -> vehicle-hours
 * for the network totals). Null in, null out — a missing metric must not
 * become 0. */
function scaleKpiMetricValue(value: number | null, divisor: number): number | null {
  if (value == null) return null;
  return divisor === 1 ? value : value / divisor;
}

// The scenario the validation scaling is pegged to. Kept as a named constant
// so the internal id never appears in user-facing copy — resolveBaseScenarioName
// turns it into the scenario's display name where the scenario is loaded.
const BASE_SCENARIO_ID = "S000001";

function resolveBaseScenarioName(scenarios: ScenarioMeta[], t: (ko: string, en: string, ja?: string) => string): string {
  return scenarios.find((scenario) => scenario.id === BASE_SCENARIO_ID)?.name ?? t("기준 시나리오", "the base scenario", "基準シナリオ");
}

interface ScaledVolumeResult {
  value: number | null;
  rawValue: number | null;
  /** Non-null when a base-scenario scaling factor was applied (excelBasam / dbBasam). */
  scaleFactor: number | null;
}

// The ONE place that turns a raw simulated node volume into the "modelled"
// number used for validation. Both the on-screen matrix (ValidationMatrix)
// and the exported report (buildReportValidationRows) call this so the two
// surfaces can never silently disagree about a GEH/percent-error figure.
function computeScaledSimVolume(
  nodeId: string,
  scenarioId: string,
  summary: CompareSummaryResponse | null,
  realCounts: NodeRealCounts[],
): ScaledVolumeResult {
  const node = summary?.top_congested_nodes?.find((item) => String(item.node_id) === String(nodeId));
  const raw = node?.scenario_totals?.[scenarioId] ?? null;
  if (raw == null) return { value: null, rawValue: null, scaleFactor: null };

  const dbBasam = node?.scenario_totals?.[BASE_SCENARIO_ID] ?? null;
  const excelBasam = realCounts.find((row) => String(row.node_id) === String(nodeId))?.total_sim_basam ?? null;

  if (dbBasam != null && dbBasam > 0 && excelBasam != null) {
    const factor = excelBasam / dbBasam;
    return { value: raw * factor, rawValue: raw, scaleFactor: factor };
  }

  return { value: raw, rawValue: raw, scaleFactor: null };
}

const TONE_SEVERITY: Record<ValidationTone, number> = { neutral: 0, success: 1, warning: 2, danger: 3 };

// Cell color must reflect whichever criterion is worse — a node can pass the
// %-error band while failing "GEH < 5", and coloring by %-error alone (the
// previous behavior) hid that failure in a green cell.
function combinedValidationTone(pct: number | null, geh: number | null): ValidationTone {
  const pctTone = errorTone(pct);
  const gehTone_ = gehTone(geh);
  return TONE_SEVERITY[gehTone_] >= TONE_SEVERITY[pctTone] ? gehTone_ : pctTone;
}

function ValidationMatrix({
  realCounts,
  scenarios,
  scenarioColorById,
  summary,
  onNodeClick,
  selectedNodeId,
}: {
  realCounts: NodeRealCounts[];
  scenarios: ScenarioMeta[];
  /** Full-list id->color map (see DashboardPage) -- `scenarios` here is only
   * the currently-selected subset, so its own map() position must never be
   * used to pick a color. */
  scenarioColorById: Map<string, string>;
  summary: CompareSummaryResponse | null;
  onNodeClick: (nodeId: string, nodeName: string) => void;
  selectedNodeId: string | null;
}) {
  const { t } = useLanguage();

  const hasBasam = scenarios.some((scenario) => scenario.id === BASE_SCENARIO_ID);
  const nodes = [...realCounts].sort((a, b) => parseInt(a.node_id) - parseInt(b.node_id));

  return (
    <div className="mt-4 space-y-2">
      {!hasBasam && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700">
          {t("기준(base) 시나리오가 선택되지 않아 스케일 기반 검증 정확도가 낮아질 수 있습니다.", "The base scenario is not selected, so scaling-based validation may be less accurate.")}
        </p>
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-300/70 bg-surface/92 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600">{t("교차로", "Node")}</th>
              <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600">{t("관측", "Observed")}</th>
              {scenarios.map((scenario) => (
                <th key={scenario.id} className="px-4 py-3 text-center text-xs font-semibold text-slate-600">
                  <span className="flex items-center justify-center gap-1.5">
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ backgroundColor: resolveScenarioColor(scenarioColorById, scenario) }}
                    />
                    {scenario.name}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => {
              const obs = node.total_real;
              const isSelected = selectedNodeId === node.node_id;

              return (
                <tr
                  key={node.node_id}
                  onClick={() => onNodeClick(node.node_id, node.node_name)}
                  className={`cursor-pointer border-b border-slate-100 transition-colors last:border-0 hover:bg-slate-100/70 ${
                    isSelected ? "bg-blue-50 hover:bg-blue-50" : ""
                  }`}
                >
                  <td className="px-4 py-3">
                    <span className="font-medium text-slate-800">{node.node_name}</span>
                  </td>

                  <td className="px-4 py-3 text-right font-semibold tabular-nums text-slate-700">
                    {obs != null ? obs.toLocaleString() : "-"}
                  </td>

                  {scenarios.map((scenario) => {
                    // Same computation the exported report uses (computeScaledSimVolume)
                    // so the on-screen cell and the report row never disagree.
                    const scaled = computeScaledSimVolume(node.node_id, scenario.id, summary, realCounts);
                    const sim = scaled.value;
                    const pct = calculatePercentError(sim, obs);
                    const geh = calculateGeh(sim, obs);
                    const tone = combinedValidationTone(pct, geh);

                    return (
                      <td key={scenario.id} className="px-3 py-2 text-center">
                        {sim != null ? (
                          <div className={`inline-block rounded-lg px-2.5 py-1.5 ${toneBadgeClass(tone)}`}>
                            <div className="text-xs font-bold tabular-nums">
                              {pct != null ? `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%` : "-"}
                            </div>
                            <div className="mt-0.5 text-[10px] opacity-80 tabular-nums">
                              {Math.round(sim).toLocaleString()} {t("대", "veh")}
                              {geh != null && <span className="ml-1">GEH {geh.toFixed(1)}</span>}
                            </div>
                            {scaled.scaleFactor != null && (
                              <div
                                className="mt-0.5 text-[9px] font-semibold opacity-70"
                                title={t(
                                  `실측 기준 시나리오 대비 배율(${scaled.scaleFactor.toFixed(3)}x)이 적용된 추정값입니다. 원본 시뮬레이션 값: ${scaled.rawValue != null ? Math.round(scaled.rawValue).toLocaleString() : "-"}대`,
                                  `Estimated: scaled ${scaled.scaleFactor.toFixed(3)}x against the base scenario. Raw simulated value: ${scaled.rawValue != null ? Math.round(scaled.rawValue).toLocaleString() : "-"} veh`,
                                )}
                              >
                                ×{scaled.scaleFactor.toFixed(2)} {t("배율 적용", "scaled")}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-slate-400">-</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-slate-100 bg-slate-50">
              <td colSpan={2 + scenarios.length} className="px-4 py-2">
                <div className="flex flex-wrap items-center gap-4 text-[10px] text-slate-500">
                  <span className="font-medium">{t("오차 구간:", "Error bands:")}</span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-sm bg-emerald-200" /> {t("5% 이내", "within 5%")}
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-sm bg-amber-200" /> {t("5% ~ 15%", "5% to 15%")}
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-sm bg-rose-200" /> {t("15% 초과", "over 15%")}
                  </span>
                  <span className="ml-2">{t("GEH < 5 우수, GEH < 10 허용", "GEH < 5 good, GEH < 10 acceptable")}</span>
                </div>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function buildIntersectionPerformanceCards(
  selectedNodeIds: string[],
  nodeNamesById: Record<string, string>,
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): IntersectionPerformanceCard[] {
  return selectedNodeIds.map((nodeId) => {
    const maxQueue = pickMaxIntersectionMetric(scenarios, results, nodeId, calculateNodeMaxQueue);
    const worstDelay = pickMaxIntersectionMetric(scenarios, results, nodeId, calculateWorstApproachDelay);
    const volume = calculateAverageDirectionalVolume(scenarios, results, nodeId);

    return {
      nodeId,
      nodeName: nodeNamesById[nodeId] ?? `Node ${nodeId}`,
      delayByDirection: calculateDirectionDelays(nodeId, scenarios, results),
      maxQueueLength: maxQueue.value,
      maxQueueScenarioName: maxQueue.scenarioName,
      worstApproachDelay: worstDelay.value,
      worstDelayScenarioName: worstDelay.scenarioName,
      volumeTotal: volume.total,
      volumeDirections: volume.directions,
    };
  });
}

function pickMaxIntersectionMetric(
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
  nodeId: string,
  getter: (result: ScenarioResults, nodeId: string) => number | null,
): { value: number | null; scenarioName: string | null } {
  let bestValue: number | null = null;
  let bestScenarioName: string | null = null;

  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;

    const value = getter(result, nodeId);
    if (value == null) continue;

    if (bestValue == null || value > bestValue) {
      bestValue = value;
      bestScenarioName = scenario.name;
    }
  }

  return { value: bestValue, scenarioName: bestScenarioName };
}

function findNodeDirectionRow(result: ScenarioResults, nodeId: string): NodeDirectionRow | null {
  return result.node_directions?.find((row) => String(row.node_id) === String(nodeId)) ?? null;
}

// MEASURED per-approach delay (unit 1.5 D5).
//
// This used to spread the node-average delay across the approaches with an
// invented `0.75 + (dirVol / maxVol) * 0.5` factor and label the result
// "추정". Vissim reports VEHDELAY per movement, and the backend already
// classifies every movement to a compass approach, so `incoming_delay_sec`
// now carries the real VEHS-weighted mean per direction.
//
// A direction with no measurement stays null all the way to the chart, which
// renders an explicit "데이터 없음" state. A refusal with a clear reason beats
// a bar that is wrong.
function calculateDirectionDelays(
  nodeId: string,
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): IntersectionPerformanceCard["delayByDirection"] {
  const buckets: Record<DirectionKey, { sum: number; count: number }> = {
    north: { sum: 0, count: 0 },
    east: { sum: 0, count: 0 },
    south: { sum: 0, count: 0 },
    west: { sum: 0, count: 0 },
  };

  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;

    const row = findNodeDirectionRow(result, nodeId);
    const measured = row?.incoming_delay_sec;
    if (!measured) continue;

    for (const direction of DIRECTION_ORDER) {
      // `toFiniteNumber` alone is NOT enough here: it is `Number(value)` +
      // `Number.isFinite`, and `Number(null) === 0`, which is finite. The
      // backend sends explicit JSON nulls for every unmeasured approach, so
      // coercing first would turn "not measured" into a measured 0 s/veh at
      // LOS A -- a fabricated number on a customer-facing screen, and the exact
      // failure unit 1.5 exists to remove. Reject null/undefined BEFORE
      // coercion; a genuine measured 0.0 still passes through as a bar.
      const raw = measured[direction];
      const value = raw == null ? null : toFiniteNumber(raw);
      if (value == null) continue;
      buckets[direction].sum += value;
      buckets[direction].count += 1;
    }
  }

  return DIRECTION_ORDER.map((direction) => {
    const bucket = buckets[direction];
    const value = bucket.count > 0 ? bucket.sum / bucket.count : null;
    return {
      direction,
      value,
      los: value != null ? resolveLosGrade(value) : null,
    };
  });
}

function calculateAverageDirectionalVolume(
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
  nodeId: string,
): { total: number | null; directions: Record<DirectionKey, number> } {
  const totals: Record<DirectionKey, number> = { north: 0, east: 0, south: 0, west: 0 };
  let count = 0;

  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;

    const row = findNodeDirectionRow(result, nodeId);
    if (!row) continue;

    const volume = calculateDirectionalVolume(row);
    for (const direction of DIRECTION_ORDER) {
      totals[direction] += volume.directions[direction];
    }
    count += 1;
  }

  if (count === 0) {
    return { total: null, directions: totals };
  }

  const directions = Object.fromEntries(
    DIRECTION_ORDER.map((direction) => [direction, totals[direction] / count]),
  ) as Record<DirectionKey, number>;

  return { total: sumDirectionMap(directions), directions };
}

function calculateDirectionalVolume(row: NodeDirectionRow): { total: number; directions: Record<DirectionKey, number> } {
  const incoming = normalizeDirectionTotals(row.incoming);
  const incomingTotal = sumDirectionMap(incoming);
  if (incomingTotal > 0) {
    return { total: incomingTotal, directions: incoming };
  }

  const outgoing = normalizeDirectionTotals(row.outgoing);
  return { total: sumDirectionMap(outgoing), directions: outgoing };
}

function normalizeDirectionTotals(values: Record<string, number> | undefined): Record<DirectionKey, number> {
  return {
    north: Math.max(0, toFiniteNumber(values?.north) ?? 0),
    east: Math.max(0, toFiniteNumber(values?.east) ?? 0),
    south: Math.max(0, toFiniteNumber(values?.south) ?? 0),
    west: Math.max(0, toFiniteNumber(values?.west) ?? 0),
  };
}

function calculateWorstApproachDelay(result: ScenarioResults, nodeId: string): number | null {
  const delays = result.movements
    .filter((row) => objectBelongsToNode(row.OBJECT_ID, nodeId))
    .map((row) => toFiniteNumber(row["VEHDELAY_MEAN"] ?? row["VEHDELAY"]))
    .filter((value): value is number => value != null);

  return delays.length ? Math.max(...delays) : null;
}

function calculateNodeMaxQueue(result: ScenarioResults, nodeId: string): number | null {
  // "Max Q Length" must only ever report QLENMAX (peak queue). Substituting
  // QLEN (average queue) under that title — as an earlier version did when
  // QLENMAX was absent — mislabels an average as a maximum, so it is NOT
  // done: when no peak-queue value exists for this node we return null and
  // the bar renders "-" rather than a wrong number under the wrong label.
  const maxValues = result.queues
    .filter((row) => objectBelongsToNode(row.OBJECT_ID, nodeId))
    .map((row) => toFiniteNumber(row["QLENMAX_MEAN"] ?? row["QLENMAX"]))
    .filter((value): value is number => value != null);
  return maxValues.length ? Math.max(...maxValues) : null;
}

function sumDirectionMap(values: Record<string, number> | undefined): number {
  return Object.values(values ?? {}).reduce((sum, value) => sum + Number(value ?? 0), 0);
}

const TRAVEL_TIME_MAX_POINTS = 18;

function buildTravelTimeSeries(
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
  scenarioColorById: Map<string, string>,
): TravelTimeChart {
  // The grouping mode (per-section vs per-interval) is decided ONCE from the
  // union of all scenarios' rows, not independently per scenario — otherwise
  // two scenarios could plot different kinds of measurements at the same x
  // position with no way to tell them apart.
  const allObjectIds = new Set<string>();
  for (const scenario of scenarios) {
    const result = results[scenario.id];
    if (!result) continue;
    const rows = result.traveltime_interval_class?.length ? result.traveltime_interval_class : result.traveltime ?? [];
    for (const row of rows) {
      const id = String(row["OBJECT_ID"] ?? "").trim();
      if (id) allObjectIds.add(id);
    }
  }
  const useSectionLabels = allObjectIds.size > 1;

  const labelFor = (row: Record<string, unknown>, index: number): string => {
    const sectionLabel = String(row["OBJECT_ID"] ?? "").trim();
    const intervalLabel = String(row["ARG_INTERVAL"] ?? "").trim();
    return useSectionLabels
      ? sectionLabel || intervalLabel || `${index + 1}`
      : intervalLabel || sectionLabel || `${index + 1}`;
  };

  const perScenario = scenarios.map((scenario) => {
    const result = results[scenario.id];
    const grouped = new Map<string, { sum: number; count: number }>();
    if (result) {
      const intervalRows = result.traveltime_interval_class ?? [];
      const rows = intervalRows.length > 0 ? intervalRows : result.traveltime;
      rows.forEach((row, index) => {
        // Plot the real per-interval (or per-section) travel-time averages
        // as-is — an earlier version reshaped these into a synthetic
        // monotonically-rising eased curve, which fabricated the shape.
        const value = toFiniteNumber(row["TRAVTM_MEAN"] ?? row["TRAVTM"]);
        if (value == null) return;
        const label = labelFor(row, index);
        const current = grouped.get(label) ?? { sum: 0, count: 0 };
        current.sum += value;
        current.count += 1;
        grouped.set(label, current);
      });
    }
    return { scenario, grouped };
  });

  // Shared x-domain: every label seen in ANY scenario, so a given x position
  // means the same section/interval for every plotted line.
  const allLabels = new Set<string>();
  for (const { grouped } of perScenario) {
    for (const label of grouped.keys()) allLabels.add(label);
  }
  const sortedLabels = Array.from(allLabels).sort(numericAwareCompare);
  const truncated = sortedLabels.length > TRAVEL_TIME_MAX_POINTS;
  const labels = sortedLabels.slice(0, TRAVEL_TIME_MAX_POINTS);

  const series = perScenario.map(({ scenario, grouped }) => ({
    scenario,
    color: resolveScenarioColor(scenarioColorById, scenario),
    points: labels.map((label) => {
      const aggregate = grouped.get(label);
      return { label, value: aggregate ? roundChartValue(aggregate.sum / aggregate.count) : null };
    }),
  }));

  return { series, labels, truncated, useSectionLabels };
}

function roundChartValue(value: number): number {
  return Math.round(value * 10) / 10;
}

function buildOverallNarrative(
  summary: CompareSummaryResponse | null,
  activeScenarios: ScenarioMeta[],
  hasData: boolean,
  lang: "ko" | "en" | "ja",
): string {
  const pick = (ko: string, en: string, ja: string) => (lang === "ko" ? ko : lang === "ja" ? ja : en);

  if (!hasData) {
    return pick(
      "시나리오 결과를 불러오면 종합 분석이 표시됩니다.",
      "Overall analysis appears after scenario results are loaded.",
      "シナリオ結果を読み込むと総合分析が表示されます。",
    );
  }

  const ranking = summary?.scenario_ranking ?? summary?.scenarioRanking ?? [];
  const topRank = ranking[0];
  const hotspot = summary?.top_congested_nodes?.[0] ?? null;
  const scenarioName =
    topRank?.scenario_name ??
    activeScenarios.find((scenario) => scenario.id === topRank?.scenario_id)?.name ??
    topRank?.scenario_id ??
    pick("선택 시나리오", "the selected scenarios", "選択シナリオ");

  const hotspotText = hotspot
    ? pick(
        `${hotspot.node_name}이(가) 선택 결과 중 대표 혼잡 교차로입니다.`,
        `${hotspot.node_name} is the representative hotspot among the selected results.`,
        `${hotspot.node_name} が選択結果の中で代表的な混雑交差点です。`,
      )
    : pick(
        "대표 혼잡 교차로는 아직 산정되지 않았습니다.",
        "A representative hotspot has not been calculated yet.",
        "代表的な混雑交差点はまだ算定されていません。",
      );

  if (topRank) {
    return pick(
      `${scenarioName}이(가) 현재 종합 점수 기준 최상위 시나리오입니다. ${hotspotText} 교차로 성능평가, 통행시간 비교, 네트워크 KPI를 함께 확인해 최종 대안을 판단하세요.`,
      `${scenarioName} is currently ranked highest by the overall score. ${hotspotText} Review intersection performance, travel-time comparison, and network KPI together before choosing the final alternative.`,
      `${scenarioName} が現在、総合スコアで最上位のシナリオです。${hotspotText} 交差点性能評価・旅行時間比較・ネットワークKPIを併せて確認し、最終案を判断してください。`,
    );
  }

  return pick(
    `선택된 ${activeScenarios.length}개 시나리오의 비교 결과가 준비되었습니다. ${hotspotText}`,
    `Comparison results are ready for ${activeScenarios.length} selected scenarios. ${hotspotText}`,
    `選択された ${activeScenarios.length} 件のシナリオの比較結果が準備できました。${hotspotText}`,
  );
}

function buildCalibrationReportMarkdown({
  scenarios,
  intersectionCards,
  selectedNodeIds,
  nodeNamesById,
  results,
  summary,
  realCounts,
  generatedAt,
  comparisonNarrative,
}: {
  scenarios: ScenarioMeta[];
  intersectionCards: IntersectionPerformanceCard[];
  selectedNodeIds: string[];
  nodeNamesById: Record<string, string>;
  results: Record<string, ScenarioResults>;
  summary: CompareSummaryResponse | null;
  realCounts: NodeRealCounts[];
  generatedAt: Date;
  comparisonNarrative: string;
}): string {
  const scenarioNames = scenarios.map((scenario) => scenario.name).join(", ") || "Not specified";
  const intersectionNames =
    intersectionCards.map((card) => card.nodeName).join(", ") ||
    selectedNodeIds.map((id) => nodeNamesById[id] ?? `Node ${id}`).join(", ") ||
    "Not specified";
  const validationRows = buildReportValidationRows({ scenarios, intersectionCards, realCounts, results, summary });
  const categoryOnePassRate = calculateGehPassRate(validationRows, 5);
  const categoryTwoPassRate = calculateGehPassRate(validationRows, 10);
  const observedNodes = new Set(validationRows.map((row) => row.nodeId));
  const summaryCards = summary?.summary_cards ?? summary?.summaryCards ?? [];
  const keyInsights = summary?.key_insights ?? summary?.keyInsights ?? [];
  const ranking = summary?.scenario_ranking ?? summary?.scenarioRanking ?? [];
  const hotspotRows = summary?.top_congested_nodes ?? [];

  const lines: string[] = [];
  lines.push("# Traffic Modelling Report: Base Model Calibration & Validation");
  lines.push("");
  lines.push("## Document Control");
  lines.push("");
  lines.push(markdownTable(
    ["Field", "Details"],
    [
      ["Owner", "To be assigned"],
      ["Custodian", "To be assigned"],
      ["Approved", "Not yet reviewed or approved"],
      ["Document Number", "VISSIM-BASE-CALVAL-DRAFT"],
      ["Issue Date", formatReportDate(generatedAt)],
      ["Project", "Vissim Platform Scenario and Intersection Comparison"],
      ["Prepared By", "Auto-generated draft from Prism dashboard data (not reviewed by an engineer)"],
    ],
  ));
  lines.push("");
  lines.push("## Amendments");
  lines.push("");
  lines.push(markdownTable(
    ["Revision Number", "Revision Date", "Description of Key Changes", "Section / Page No."],
    [["0", formatReportDate(generatedAt), "Initial draft generated from platform scenario comparison outputs.", "All"]],
  ));
  lines.push("");
  lines.push("## 1. Purpose");
  lines.push("");
  lines.push(
    "This report documents the base model calibration and validation evidence available from the current Vissim platform comparison results, with emphasis on selected intersection performance, traffic volumes, delay/LOS indicators and GEH-based volume checks where observed counts are available.",
  );
  lines.push("");
  lines.push("## 2. Introduction");
  lines.push("");
  lines.push("### 2.1 Project Details");
  lines.push("");
  lines.push(
    "A Vissim base model has been assessed through the platform comparison dashboard to evaluate current and alternative operational performance across selected intersections. This draft has been generated directly from the scenario result set visible in the platform.",
  );
  lines.push("");
  lines.push("### 2.2 Project Location and Modelling Area");
  lines.push("");
  lines.push(markdownTable(
    ["Item", "Description"],
    [
      ["Project affected area", "To be confirmed by project metadata"],
      ["Area", "To be confirmed"],
      ["Main corridor", "To be confirmed"],
      ["Selected model nodes", intersectionNames],
    ],
  ));
  lines.push("");
  lines.push("### 2.3 Predefined Modelling Scenarios for the Study");
  lines.push("");
  lines.push(markdownTable(
    ["Scenario ID", "Scenario Name", "Result Status"],
    scenarios.map((scenario) => [
      scenario.id,
      scenario.name,
      results[scenario.id] ? "Loaded in platform comparison" : "No result loaded",
    ]),
  ));
  lines.push("");
  lines.push("## 3. Data Collection");
  lines.push("");
  lines.push(
    "**Road Network:** The road network and lane structure were represented in Vissim and reviewed against available digital network geometry used by the platform. In a formal submission, this should be cross-checked against aerial imagery and current layout drawings.",
  );
  lines.push("");
  lines.push(
    `**Traffic Flows:** The current report uses scenario outputs for ${scenarioNames}. Observed traffic count records are ${observedNodes.size > 0 ? `available for ${observedNodes.size} selected node(s)` : "not available for the selected nodes"} through the validation data endpoint.`,
  );
  lines.push("");
  lines.push(
    "**Signal Data:** Signal phasing information should be confirmed against the source Vissim model and any available signal timing records before formal submission.",
  );
  lines.push("");
  lines.push("## 4. Model Calibration & Validation");
  lines.push("");
  lines.push(
    "The tables below summarise the evidence currently available from the platform outputs. Any final submission should be checked against the applicable traffic modelling guidelines and source data package.",
  );
  lines.push("");
  lines.push("### 4.1 Model Calibration");
  lines.push("");
  lines.push(markdownTable(
    ["Calibration Item", "Current Report Value"],
    [
      ["Software Version", "Vissim version to be confirmed; platform output imported from local scenario results"],
      ["Time Period Settings", "Warm-up, simulation and cool-down periods to be confirmed from model settings"],
      ["Random Seed", buildSeedSummary(scenarios, results)],
      ["Vehicle Types", "Vehicle class grouping to be confirmed from Vissim input definitions"],
      ["Driving Behaviour", "Urban, freeway, merging and diverging behaviours to be confirmed from Vissim parameters"],
    ],
  ));
  lines.push("");
  lines.push("### 4.2 Traffic Volume Calibration / GEH Criteria");
  lines.push("");
  lines.push(markdownTable(
    ["Category", "Acceptance Criteria", "Current Result"],
    [
      ["Category 1", "At least 95% of compared flows should have GEH < 5", formatPassRate(categoryOnePassRate)],
      ["Category 2", "At least 95% of compared flows should have GEH < 10", formatPassRate(categoryTwoPassRate)],
      ["Category 3", "All material outliers should be reviewed and explained", validationRows.some((row) => row.geh != null && row.geh >= 10) ? "Outliers require review" : "No GEH >= 10 outlier in available rows"],
    ],
  ));
  lines.push("");
  lines.push("#### Observed vs Modelled Traffic Volume");
  lines.push("");
  lines.push(
    validationRows.length > 0
      ? markdownTable(
          ["Node", "Scenario", "Observed Volume", "Modelled Volume", "% Error", "GEH", "Category 1"],
          validationRows.map((row) => [
            row.nodeName,
            row.scenarioName,
            formatReportNumber(row.observed),
            `${formatReportNumber(row.modelled)}${row.modelledIsScaled ? "*" : ""}`,
            formatReportPercent(row.percentError),
            row.geh == null ? "N/A" : row.geh.toFixed(2),
            row.geh != null && row.geh < 5 ? "Pass" : "Review",
          ]),
        )
      : "_Observed traffic count data was not available for the selected intersections. GEH calibration evidence should be added when survey counts are confirmed._",
  );
  if (validationRows.some((row) => row.modelledIsScaled)) {
    const baseName = scenarios.find((scenario) => scenario.id === BASE_SCENARIO_ID)?.name ?? "the base scenario";
    lines.push("");
    lines.push(
      `_* Modelled volume is scaled against ${baseName} using the ratio between the observed base-scenario count and the simulated base-scenario volume. This is an estimate, not a direct simulation output._`,
    );
  }
  lines.push("");
  lines.push("#### Selected Intersection Performance Summary");
  lines.push("");
  lines.push(markdownTable(
    ["Intersection", "Max Q Length", "Worst Approach Delay", "Average Volume"],
    intersectionCards.map((card) => [
      card.nodeName,
      `${formatReportNumber(card.maxQueueLength)} m`,
      `${formatReportNumber(card.worstApproachDelay)} sec`,
      `${formatReportNumber(card.volumeTotal)} veh`,
    ]),
  ));
  lines.push("");
  lines.push("#### Selected Intersection Direction Summary");
  lines.push("");
  lines.push(markdownTable(
    ["Intersection", "Direction", "Scenario", "Inbound Volume", "Approach Delay", "LOS"],
    buildDirectionReportRows(intersectionCards, scenarios, results),
  ));
  lines.push("");
  lines.push(
    "_Approach Delay is a measured VISSIM output, not an estimate: VISSIM reports VEHDELAY per movement, and each movement is classified to its compass approach, so the value shown is the volume-weighted (VEHS-weighted) mean of the measured per-movement delays entering that approach. LOS is the standard delay band applied to that measured value. Where a movement carried no VEHDELAY measurement, the approach is reported as \"-\" and no delay or LOS is stated for it -- an unmeasured approach is never reported as 0._",
  );
  lines.push("");

  if (summaryCards.length > 0 || keyInsights.length > 0 || hotspotRows.length > 0 || ranking.length > 0) {
    lines.push("#### Platform Summary Insights");
    lines.push("");
    if (summaryCards.length > 0) {
      lines.push(markdownTable(
        ["Metric", "Value", "Description"],
        summaryCards.map((card) => [
          card.label,
          `${card.value}${card.unit ? ` ${card.unit}` : ""}`,
          card.description ?? "",
        ]),
      ));
      lines.push("");
    }
    if (ranking.length > 0) {
      lines.push(markdownTable(
        ["Rank", "Scenario", "Score", "Reason"],
        ranking.map((item, index) => [
          String(item.rank ?? index + 1),
          item.scenario_name ?? item.scenario_id,
          item.score_label ?? (item.score == null ? "" : item.score.toFixed(1)),
          item.reason ?? "",
        ]),
      ));
      lines.push("");
    }
    if (keyInsights.length > 0) {
      lines.push(keyInsights.map((item) => `- **${item.title}:** ${item.message}`).join("\n"));
      lines.push("");
    }
    if (hotspotRows.length > 0) {
      lines.push(markdownTable(
        ["Node", "Average Total", "Minimum", "Maximum", "Spread"],
        hotspotRows.slice(0, 5).map((item) => [
          item.node_name,
          formatReportNumber(item.average_total),
          formatReportNumber(item.min_total),
          formatReportNumber(item.max_total),
          formatReportNumber(item.spread),
        ]),
      ));
      lines.push("");
    }
  }

  lines.push("### 4.3 Model Validation");
  lines.push("");
  lines.push("#### Travel Times");
  lines.push("");
  lines.push(markdownTable(
    ["Scenario", "Travel Time Sections", "Average Modelled Travel Time", "Validation Assessment"],
    scenarios.map((scenario) => {
      const travelTimes = extractTravelTimeValues(results[scenario.id]);
      return [
        scenario.name,
        String(travelTimes.length),
        travelTimes.length > 0 ? `${formatReportNumber(average(travelTimes))} sec` : "N/A",
        "Observed travel time comparison required for final Category 1 assessment",
      ];
    }),
  ));
  lines.push("");
  lines.push(
    "Travel time validation should compare observed travel time data against Vissim model outputs. Category 1 compliance is generally achieved when modelled travel times are within the applicable tolerance of observed travel times.",
  );
  lines.push("");
  lines.push("## 5. Conclusion");
  lines.push("");
  lines.push(
    `The currently selected scenarios (${scenarioNames}) have been reviewed for the selected intersections (${intersectionNames}). ${comparisonNarrative}`,
  );
  lines.push(
    categoryOnePassRate.total > 0 && categoryOnePassRate.rate >= 95
      ? "Based on the available observed count comparison, the model satisfies the Category 1 GEH criterion for the selected validation rows, subject to completion of the remaining documentation checks."
      : "The generated evidence should be treated as a draft calibration and validation summary. Additional observed count, travel time and model configuration evidence should be confirmed before the base model is formally accepted.",
  );
  lines.push("");
  lines.push("## 6. Appendices / Documents Provided");
  lines.push("");
  lines.push(markdownTable(
    ["Appendix", "Document / Dataset", "Status"],
    [
      ["A", "Traffic survey raw data", observedNodes.size > 0 ? "Partially represented through validation counts" : "Required"],
      ["B", "Signal timing data", "Required for final report"],
      ["C", "Observed travel time data", "Required for final report"],
      ["D", "Vissim scenario result files", "Represented in platform comparison output"],
      ["E", "Layout drawings", "Required for final report"],
    ],
  ));
  lines.push("");

  return lines.join("\n");
}

function buildReportValidationRows({
  scenarios,
  intersectionCards,
  realCounts,
  results,
  summary,
}: {
  scenarios: ScenarioMeta[];
  intersectionCards: IntersectionPerformanceCard[];
  realCounts: NodeRealCounts[];
  results: Record<string, ScenarioResults>;
  summary: CompareSummaryResponse | null;
}): Array<{
  nodeId: string;
  nodeName: string;
  scenarioName: string;
  observed: number | null;
  modelled: number | null;
  modelledIsScaled: boolean;
  percentError: number | null;
  geh: number | null;
}> {
  const realByNode = new Map(realCounts.map((item) => [String(item.node_id), item]));
  const rows: Array<{
    nodeId: string;
    nodeName: string;
    scenarioName: string;
    observed: number | null;
    modelled: number | null;
    modelledIsScaled: boolean;
    percentError: number | null;
    geh: number | null;
  }> = [];

  for (const card of intersectionCards) {
    const observed = realByNode.get(String(card.nodeId))?.total_real ?? null;
    if (observed == null) continue;

    for (const scenario of scenarios) {
      // Same computeScaledSimVolume the dashboard's ValidationMatrix cell
      // uses, so this report can never report a different GEH for the same
      // node/scenario than what the user saw on screen. Falls back to raw
      // per-node movement volume only when compare-summary data is absent.
      const scaled = computeScaledSimVolume(card.nodeId, scenario.id, summary, realCounts);
      const modelled = scaled.value ?? calculateScenarioNodeVolume(results[scenario.id], card.nodeId);

      rows.push({
        nodeId: card.nodeId,
        nodeName: card.nodeName,
        scenarioName: scenario.name,
        observed,
        modelled,
        modelledIsScaled: scaled.scaleFactor != null,
        percentError: calculatePercentError(modelled, observed),
        geh: calculateGeh(modelled, observed),
      });
    }
  }

  return rows;
}

function buildDirectionReportRows(
  cards: IntersectionPerformanceCard[],
  scenarios: ScenarioMeta[],
  results: Record<string, ScenarioResults>,
): string[][] {
  const rows: string[][] = [];

  for (const card of cards) {
    for (const scenario of scenarios) {
      const nodeRow = findNodeDirectionRow(results[scenario.id], card.nodeId);
      const volume = nodeRow ? calculateDirectionalVolume(nodeRow) : null;
      for (const direction of DIRECTION_ORDER) {
        const directionLabel = DIRECTION_LABELS[direction].en;
        // Both columns are real per-direction MEASUREMENTS now (unit 1.5 D5):
        // inbound volume from VEHS, approach delay from the VEHS-weighted mean
        // of the per-movement VEHDELAY the backend already classifies to a
        // compass approach. `incoming_delay_sec` is nullable ON PURPOSE -- a
        // JSON null means "not measured", so it must be tested BEFORE any
        // numeric coercion (`Number(null) === 0` is finite) and printed as "-".
        // A synthesized per-approach delay is exactly what this unit removed;
        // never fill the gap with the intersection average.
        const rawDelay = nodeRow?.incoming_delay_sec?.[direction];
        const delay = rawDelay == null ? null : toFiniteNumber(rawDelay);
        rows.push([
          card.nodeName,
          directionLabel,
          scenario.name,
          formatReportNumber(volume?.directions[direction]),
          delay == null ? "-" : `${delay.toFixed(1)} sec`,
          delay == null ? "-" : resolveLosGrade(delay),
        ]);
      }
    }
  }

  return rows.length > 0 ? rows : [["N/A", "N/A", "N/A", "N/A", "N/A", "N/A"]];
}

function calculateScenarioNodeVolume(result: ScenarioResults | undefined, nodeId: string): number | null {
  if (!result) return null;

  const row = findNodeDirectionRow(result, nodeId);
  if (row) {
    return calculateDirectionalVolume(row).total;
  }

  const movementVolume = result.movements
    .filter((item) => objectBelongsToNode(item.OBJECT_ID, nodeId))
    .map((item) => toFiniteNumber(item["VEHS_MEAN"] ?? item["VEHS"]))
    .filter((value): value is number => value != null)
    .reduce((sum, value) => sum + value, 0);

  return movementVolume > 0 ? movementVolume : null;
}

function buildSeedSummary(scenarios: ScenarioMeta[], results: Record<string, ScenarioResults>): string {
  const seeds = scenarios
    .map((scenario) => {
      const network = results[scenario.id]?.network ?? {};
      const seed = network.RANDOM_SEED ?? network.RandomSeed ?? network.random_seed;
      return seed == null ? null : `${scenario.name}: ${seed}`;
    })
    .filter((item): item is string => item != null);

  return seeds.length > 0 ? seeds.join("; ") : "To be confirmed from Vissim simulation parameters";
}

function extractTravelTimeValues(result: ScenarioResults | undefined): number[] {
  if (!result) return [];

  const rows = [...(result.traveltime ?? []), ...(result.traveltime_interval_class ?? [])];
  const values: number[] = [];
  for (const row of rows) {
    for (const [key, value] of Object.entries(row)) {
      if (key === "OBJECT_ID" || key.startsWith("ARG_")) continue;
      const lower = key.toLowerCase();
      if (!lower.includes("trav") && !lower.includes("time")) continue;
      const parsed = toFiniteNumber(value);
      if (parsed != null && parsed > 0) values.push(parsed);
    }
  }

  return values;
}

function markdownTable(headers: string[], rows: string[][]): string {
  const safeHeaders = headers.map(escapeMarkdownCell);
  const safeRows = rows.map((row) => row.map((cell) => escapeMarkdownCell(String(cell ?? ""))));
  return [
    `| ${safeHeaders.join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`,
    ...safeRows.map((row) => `| ${row.join(" | ")} |`),
  ].join("\n");
}

function buildWordDocumentHtml(markdown: string): string {
  const body = markdownToWordHtml(markdown);
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Traffic Modelling Report</title>
  <style>
    body { font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #fafaf9; }
    h1 { font-size: 20pt; margin: 0 0 18pt; }
    h2 { font-size: 15pt; margin: 18pt 0 8pt; border-bottom: 1px solid #45443f; padding-bottom: 4pt; }
    h3 { font-size: 12.5pt; margin: 14pt 0 6pt; }
    h4 { font-size: 11pt; margin: 12pt 0 6pt; }
    p { margin: 0 0 8pt; }
    ul { margin: 0 0 8pt 18pt; padding: 0; }
    li { margin: 0 0 4pt; }
    table { width: 100%; border-collapse: collapse; margin: 8pt 0 12pt; }
    th, td { border: 1px solid #6a6862; padding: 5pt 6pt; vertical-align: top; }
    th { background: #33332f; font-weight: 700; }
    em { color: #a5a39a; }
  </style>
</head>
<body>
${body}
</body>
</html>`;
}

function markdownToWordHtml(markdown: string): string {
  const lines = markdown.split(/\r?\n/);
  const html: string[] = [];
  let paragraph: string[] = [];
  let listOpen = false;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    html.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };

  const closeList = () => {
    if (!listOpen) return;
    html.push("</ul>");
    listOpen = false;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      closeList();
      continue;
    }

    if (trimmed.startsWith("|") && index + 1 < lines.length && /^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(lines[index + 1].trim())) {
      flushParagraph();
      closeList();
      const headers = parseMarkdownTableRow(trimmed);
      index += 1;
      const rows: string[][] = [];
      while (index + 1 < lines.length && lines[index + 1].trim().startsWith("|")) {
        index += 1;
        rows.push(parseMarkdownTableRow(lines[index].trim()));
      }
      html.push(renderHtmlTable(headers, rows));
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    if (trimmed.startsWith("- ")) {
      flushParagraph();
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${renderInlineMarkdown(trimmed.slice(2))}</li>`);
      continue;
    }

    paragraph.push(trimmed);
  }

  flushParagraph();
  closeList();
  return html.join("\n");
}

function parseMarkdownTableRow(row: string): string[] {
  return row
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split(/(?<!\\)\|/)
    .map((cell) => cell.replace(/\\\|/g, "|").trim());
}

function renderHtmlTable(headers: string[], rows: string[][]): string {
  const headerHtml = headers.map((header) => `<th>${renderInlineMarkdown(header)}</th>`).join("");
  const rowHtml = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`)
    .join("\n");
  return `<table>
<thead><tr>${headerHtml}</tr></thead>
<tbody>
${rowHtml}
</tbody>
</table>`;
}

function renderInlineMarkdown(value: string): string {
  return escapeHtml(value)
    .replace(/&lt;br&gt;/g, "<br>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^_(.+)_$/g, "<em>$1</em>");
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeMarkdownCell(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\r?\n/g, "<br>");
}

// GEH itself lives in lib/geh.ts (unit 1.5 D2) -- this page used to carry its
// own copy of the formula. calculateGehPassRate is a thin row-shaped adapter
// over the shared gehPassRate so the exclusion rule stays in one place.
function calculatePercentError(modelled: number | null, observed: number | null): number | null {
  if (modelled == null || observed == null || observed === 0) return null;
  return ((modelled - observed) / observed) * 100;
}

function calculateGehPassRate(rows: Array<{ geh: number | null }>, threshold: number): GehPassRate {
  return gehPassRate(rows.map((row) => row.geh), threshold);
}

function formatPassRate(result: GehPassRate): string {
  if (result.total === 0) return "No observed comparison rows available";
  // The excluded count is surfaced, not swallowed: `total` is already the
  // shrunken denominator, so saying only "3/4" would hide the dropped rows.
  const excluded = result.excluded > 0 ? ` — ${result.excluded} excluded (GEH undefined)` : "";
  return `${result.passed}/${result.total} (${result.rate.toFixed(1)}%)${excluded}`;
}

function formatReportNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "N/A";
  return Math.round(value).toLocaleString();
}

function formatReportPercent(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "N/A";
  return `${value.toFixed(1)}%`;
}

function formatReportDate(date: Date): string {
  return date.toLocaleDateString("en-AU", { year: "numeric", month: "short", day: "2-digit" });
}

function formatDisplayDateTime(date: Date, lang: "ko" | "en" | "ja" = "ko"): string {
  const locale = lang === "en" ? "en-US" : lang === "ja" ? "ja-JP" : "ko-KR";
  return date.toLocaleString(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateForFilename(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}${month}${day}-${hour}${minute}`;
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatNumber(value: number, digits = 1): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

// CAUTION: this coerces first, and `Number(null) === 0` / `Number("") === 0`
// are both finite, so `null` in => `0` out. Every caller here wants that
// (a missing count/volume row is genuinely 0 vehicles). A caller where "no
// measurement" must stay distinct from a measured zero -- per-approach delay
// in `calculateDirectionDelays`, the network KPI cards' value/stdev -- MUST
// test for null before calling.
function toFiniteNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function numericAwareCompare(a: string, b: string): number {
  const aNum = Number.parseFloat(a);
  const bNum = Number.parseFloat(b);
  if (Number.isFinite(aNum) && Number.isFinite(bNum) && aNum !== bNum) {
    return aNum - bNum;
  }
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function EmptyNodePanel() {
  const { t } = useLanguage();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-slate-100 text-2xl">
        N
      </div>
      <div>
        <p className="font-semibold text-slate-700">{t("지도에서 교차로를 선택하세요", "Select a node on the map")}</p>
        <p className="mt-1 text-sm text-slate-500">
          {t("교차로를 클릭하면 방향별 통행량과 시나리오 비교를 볼 수 있습니다.", "Click a node to review direction-level flow and scenario comparison.")}
        </p>
      </div>
    </div>
  );
}

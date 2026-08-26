const rawBase = process.env.NEXT_PUBLIC_API_BASE?.trim() || "http://127.0.0.1:8001";
export const DEFAULT_BASE = rawBase.replace(/\/+$/, "");
export const BASE = DEFAULT_BASE;
const API_BASE_STORAGE_KEY = "vissim-api-base";
const LOCAL_API_CANDIDATES = ["http://127.0.0.1:8001", "http://localhost:8001"];
const API_FETCH_TIMEOUT_MS = 5000;
type LocalNetworkRequestInit = RequestInit & { targetAddressSpace?: "local" };

export function normalizeApiBase(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

export function getApiBase(): string {
  if (typeof window === "undefined") return DEFAULT_BASE;
  const override = readStorageValue(API_BASE_STORAGE_KEY)?.trim();
  return normalizeApiBase(override || DEFAULT_BASE);
}

export function setApiBaseOverride(value: string | null): void {
  if (typeof window === "undefined") return;
  const normalized = value ? normalizeApiBase(value) : "";
  if (normalized) {
    writeStorageValue(API_BASE_STORAGE_KEY, normalized);
  } else {
    removeStorageValue(API_BASE_STORAGE_KEY);
  }
}

function readStorageValue(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorageValue(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable in embedded/private browser contexts.
  }
}

function removeStorageValue(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Storage can be unavailable in embedded/private browser contexts.
  }
}

export function listAutoDetectApiBases(currentBase = getApiBase()): string[] {
  const candidates = [currentBase, ...LOCAL_API_CANDIDATES, DEFAULT_BASE].map(normalizeApiBase);
  return Array.from(new Set(candidates));
}

function isLocalApiBase(value: string): boolean {
  try {
    const url = new URL(value);
    return ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  } catch {
    return false;
  }
}

function isLikelyFrontendApiBase(value: string): boolean {
  try {
    const url = new URL(value);
    if (typeof window !== "undefined" && url.origin === window.location.origin) {
      return true;
    }
    return ["3000", "3001", "3002"].includes(url.port);
  } catch {
    return false;
  }
}

function listMutationApiBases(currentBase = getApiBase()): string[] {
  const normalizedCurrent = normalizeApiBase(currentBase);
  const candidates = isLocalApiBase(normalizedCurrent)
    ? [normalizedCurrent, ...LOCAL_API_CANDIDATES]
    : [normalizedCurrent, DEFAULT_BASE];
  return Array.from(new Set(candidates.map(normalizeApiBase)));
}

function listDirectMutationApiBases(currentBase = getApiBase()): string[] {
  const normalizedCurrent = normalizeApiBase(currentBase);
  const candidates = isLikelyFrontendApiBase(normalizedCurrent)
    ? [...LOCAL_API_CANDIDATES, DEFAULT_BASE]
    : [normalizedCurrent, ...LOCAL_API_CANDIDATES, DEFAULT_BASE];
  return Array.from(new Set(candidates.map(normalizeApiBase).filter((base) => !isLikelyFrontendApiBase(base))));
}

function listReadApiBases(currentBase = getApiBase()): string[] {
  const normalizedCurrent = normalizeApiBase(currentBase);
  const candidates = isLocalApiBase(normalizedCurrent)
    ? [normalizedCurrent, ...LOCAL_API_CANDIDATES, DEFAULT_BASE]
    : [normalizedCurrent, DEFAULT_BASE];
  return Array.from(new Set(candidates.map(normalizeApiBase)));
}

// ---------------------------------------------------------------------------
// Project workspaces (unit 1.4)
// ---------------------------------------------------------------------------
//
// Every backend read and write is scoped to a workspace (UI label: 프로젝트) by
// an `X-Workspace-Id` request header -- see backend/services/workspaces.py.
// The selected workspace is module state, persisted in localStorage so a
// reload stays in the same project; unset means "the backend's default
// workspace", which is exactly how a pre-workspace client behaves.
//
// Injected in `sameOriginRequestInit` and `requestInitForBase` rather than at
// each call site: those two are the ONLY places every fetch path in this file
// builds its RequestInit, so a new endpoint binding cannot forget the header.
// Two paths cannot carry it and are documented at their call sites: the
// simulation progress WebSocket (the browser WebSocket API cannot set headers)
// and any plain <a href> download built from `apiUrl`.
const WORKSPACE_STORAGE_KEY = "vissim-workspace-id";
const WORKSPACE_HEADER = "X-Workspace-Id";

let workspaceIdOverride: string | null = null;

export function getWorkspaceId(): string | null {
  if (workspaceIdOverride) return workspaceIdOverride;
  const stored = readStorageValue(WORKSPACE_STORAGE_KEY);
  if (stored) {
    workspaceIdOverride = stored;
    return stored;
  }
  return null;
}

export function setWorkspaceId(value: string | null): void {
  workspaceIdOverride = value;
  if (value) {
    writeStorageValue(WORKSPACE_STORAGE_KEY, value);
  } else {
    removeStorageValue(WORKSPACE_STORAGE_KEY);
  }
}

function withWorkspaceHeader(init?: RequestInit): RequestInit {
  const workspaceId = getWorkspaceId();
  if (!workspaceId) return { ...(init ?? {}) };
  const headers = new Headers(init?.headers);
  headers.set(WORKSPACE_HEADER, workspaceId);
  return { ...(init ?? {}), headers };
}

// `credentials: "include"` for the same reason `noWorkspaceRequestInit` states
// it: this init builds the DIRECT http://127.0.0.1:8001 leg of the fallback
// ladder, which is cross-origin, and fetch's default ("same-origin") drops the
// session cookie there. Before unit P④ an anonymous direct leg merely lost
// personalisation; now `main.WorkspaceScopeMiddleware` fail-closes a caller it
// cannot identify, so a cookie-less leg would 403 every read of a non-default
// 프로젝트 -- a signed-in member watching their own project refuse them the
// moment the Next proxy hiccups. The backend sets `allow_credentials=True`.
function requestInitForBase(base: string, init?: RequestInit): LocalNetworkRequestInit {
  const nextInit = withWorkspaceHeader(init) as LocalNetworkRequestInit;
  nextInit.credentials = nextInit.credentials ?? "include";
  if (isLocalApiBase(base)) {
    nextInit.mode = nextInit.mode ?? "cors";
    nextInit.targetAddressSpace = "local";
  }
  return nextInit;
}

// --- The one request family that must NOT carry X-Workspace-Id (unit U9) ---
//
// `main.WorkspaceScopeMiddleware` runs on EVERY request, ahead of routing and
// therefore ahead of auth. An ABSENT header resolves to the default workspace
// and is fine; a header that is PRESENT but names an unknown workspace 404s
// there and never reaches `/api/auth/*` at all (measured in U7 --
// backend/routers/auth.py's module docstring states this explicitly). A
// browser holding a stale `vissim-workspace-id` in localStorage -- exactly the
// state a person who cannot log in is likely to be in -- would therefore get a
// workspace 404 instead of a login screen.
//
// So the auth calls get their OWN init builder rather than relying on the
// header happening to be harmless: the workspace header is deleted here even
// if a caller passed one, and `authFetch` below is the only fetch path that
// uses these two functions. That is what makes "the login request cannot
// receive X-Workspace-Id" a structural property (and what
// tests/dashboard-contract.test.ts pins), not a convention.
//
// `credentials: "include"` covers both legs of the fallback ladder: the
// same-origin proxy hop would send the cookie anyway (fetch defaults to
// "same-origin"), and the direct http://127.0.0.1:8001 hop needs it stated --
// the backend sets `allow_credentials=True` (backend/main.py) for exactly this.
function noWorkspaceRequestInit(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  headers.delete(WORKSPACE_HEADER);
  return { ...(init ?? {}), headers, credentials: "include" };
}

function noWorkspaceRequestInitForBase(base: string, init?: RequestInit): LocalNetworkRequestInit {
  const nextInit = noWorkspaceRequestInit(init) as LocalNetworkRequestInit;
  if (isLocalApiBase(base)) {
    nextInit.mode = nextInit.mode ?? "cors";
    nextInit.targetAddressSpace = "local";
  }
  return nextInit;
}

function normalizedApiPath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

function sameOriginRequestInit(init?: RequestInit): RequestInit {
  return withWorkspaceHeader(init);
}

export function apiUrl(path: string): string {
  const normalizedPath = normalizedApiPath(path);
  return `${getApiBase()}${normalizedPath}`;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const normalizedPath = normalizedApiPath(path);
  if (typeof window !== "undefined") {
    try {
      return await fetchWithTimeout(normalizedPath, sameOriginRequestInit(init));
    } catch (error) {
      if (!shouldRetryApiFetch(error)) {
        throw error;
      }
    }
  }

  const primaryBase = normalizeApiBase(getApiBase());
  let lastNetworkError: unknown = null;

  for (const base of listReadApiBases(primaryBase)) {
    try {
      const response = await fetchWithTimeout(apiUrlForBase(base, normalizedPath), requestInitForBase(base, init));
      if (typeof window !== "undefined" && base !== primaryBase && isLocalApiBase(base) && isLocalApiBase(primaryBase)) {
        setApiBaseOverride(base);
      }
      return response;
    } catch (error) {
      if (!shouldRetryApiFetch(error)) {
        throw error;
      }
      lastNetworkError = error;
    }
  }

  throw lastNetworkError instanceof Error ? lastNetworkError : new Error("Failed to reach the API.");
}

async function apiMutationFetch(path: string, init?: RequestInit): Promise<Response> {
  const normalizedPath = normalizedApiPath(path);
  let lastNetworkError: unknown = null;

  if (typeof window !== "undefined") {
    try {
      return await fetch(normalizedPath, sameOriginRequestInit(init));
    } catch (error) {
      if (!shouldRetryApiFetch(error)) {
        throw error;
      }
      lastNetworkError = error;
    }
  }

  const primaryBase = normalizeApiBase(getApiBase());

  for (const base of listMutationApiBases(primaryBase)) {
    try {
      const response = await fetch(apiUrlForBase(base, normalizedPath), requestInitForBase(base, init));
      if (typeof window !== "undefined" && base !== primaryBase && isLocalApiBase(base) && isLocalApiBase(primaryBase)) {
        setApiBaseOverride(base);
      }
      return response;
    } catch (error) {
      if (!shouldRetryApiFetch(error)) {
        throw error;
      }
      lastNetworkError = error;
    }
  }

  const triedTargets = [
    ...(typeof window !== "undefined" ? [normalizedPath] : []),
    ...listMutationApiBases(primaryBase).map((base) => apiUrlForBase(base, normalizedPath)),
  ];
  const detail = lastNetworkError instanceof Error ? lastNetworkError.message : String(lastNetworkError ?? "unknown error");
  throw new Error(
    `Failed to reach the API. Tried same-origin API proxy first, then local API: ${triedTargets.join(", ")}. Last network error: ${detail}`,
  );
}

async function apiDirectMutationFetch(path: string, init?: RequestInit, options?: { sameOrigin?: boolean }): Promise<Response> {
  const normalizedPath = normalizedApiPath(path);
  const primaryBase = normalizeApiBase(getApiBase());
  const directBases = listDirectMutationApiBases(primaryBase);
  let lastNetworkError: unknown = null;

  if (typeof window !== "undefined" && options?.sameOrigin !== false) {
    try {
      return await fetch(normalizedPath, sameOriginRequestInit(init));
    } catch (error) {
      if (!shouldRetryApiFetch(error)) {
        throw error;
      }
      lastNetworkError = error;
    }
  }

  for (const base of directBases) {
    try {
      const response = await fetch(apiUrlForBase(base, normalizedPath), requestInitForBase(base, init));
      if (typeof window !== "undefined" && base !== primaryBase && isLocalApiBase(base) && isLocalApiBase(primaryBase)) {
        setApiBaseOverride(base);
      }
      return response;
    } catch (error) {
      if (!shouldRetryApiFetch(error)) {
        throw error;
      }
      lastNetworkError = error;
    }
  }

  const triedTargets = [
    ...(typeof window !== "undefined" && options?.sameOrigin !== false ? [normalizedPath] : []),
    ...directBases.map((base) => apiUrlForBase(base, normalizedPath)),
  ];
  const detail = lastNetworkError instanceof Error ? lastNetworkError.message : String(lastNetworkError ?? "unknown error");
  throw new Error(
    `Failed to reach the API. Tried same-origin API proxy first, then local API: ${triedTargets.join(", ")}. Last network error: ${detail}`,
  );
}

function apiUrlForBase(base: string, path: string): string {
  const normalizedPath = normalizedApiPath(path);
  return `${normalizeApiBase(base)}${normalizedPath}`;
}

async function fetchWithTimeout(url: string, init: RequestInit): Promise<Response> {
  if (init.signal) {
    return fetch(url, init);
  }

  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => controller.abort(), API_FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    globalThis.clearTimeout(timeoutId);
  }
}

function shouldRetryApiFetch(error: unknown): boolean {
  return error instanceof TypeError || (error instanceof Error && error.name === "AbortError");
}

function toWebSocketBase(httpBase: string): string {
  if (httpBase.startsWith("https://")) {
    return `wss://${httpBase.slice("https://".length)}`;
  }
  if (httpBase.startsWith("http://")) {
    return `ws://${httpBase.slice("http://".length)}`;
  }
  return httpBase;
}

async function readApiError(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) return `${response.status} ${response.statusText}`.trim();
  try {
    const data = JSON.parse(text) as { detail?: unknown };
    if (typeof data.detail === "string" && data.detail.trim()) {
      return data.detail;
    }
  } catch {
    // Plain text errors are still useful.
  }
  return text;
}

export interface ScenarioMeta {
  id: string;
  name: string;
  color: string;
}

// Phase 3 Tier 1: static network elements resolved server-side (inpx_parser.py's
// point_at_pos/lane_offset_point) to absolute placement, so the frontend never
// has to reimplement polyline/lane math. Every list on NetworkElements is
// always present (possibly empty); elements referencing unknown links are
// dropped server-side rather than shipped with null coordinates.
export interface NetworkElementEndpoint {
  link: string;
  pos: number;
  x: number;
  y: number;
  angle: number;
}

export interface SignalHeadElement {
  id: string;
  name: string;
  link: string;
  lane: number;
  pos: number;
  x: number;
  y: number;
  angle: number;
  sg: string | null;
  sc: string | null;
}

// Every vehicle input on a link resolves to that link's START point, so N
// inputs on one link land on ONE pixel -- on the real client model always 3,
// one per vehicle class, all carrying the SAME `name`. The composition fields
// (sibling-disambiguation unit, 2026-07-28; backend/services/inpx_parser.py's
// `vehicle_input_composition_fields`) are therefore the only thing that tells
// the stacked glyphs apart. All four are optional here because `vehComp` is
// itself optional in the .inpx schema (the synthetic demo network has none)
// and because an older backend omits them entirely -- consumers must degrade
// to "no class shown", never to a guess. `composition_name` is `""` when the
// input feeds a DIFFERENT class in different time intervals, in which case
// `composition_varies_by_interval` is true and `composition_nos` lists every
// class it feeds: a truthful "not one class" rather than interval 1's answer
// presented as the whole story.
export interface VehicleInputElement {
  id: string;
  name: string;
  link: string;
  x: number;
  y: number;
  angle: number;
  composition_no?: string;
  composition_name?: string;
  composition_nos?: string[];
  composition_varies_by_interval?: boolean;
}

export interface ReducedSpeedAreaElement {
  id: string;
  name: string;
  link: string;
  lane: number;
  pos: number;
  x: number;
  y: number;
  angle: number;
  length: number | null;
  end_x?: number;
  end_y?: number;
}

export interface DesSpeedDecisionElement {
  id: string;
  name: string;
  link: string;
  lane: number;
  pos: number;
  x: number;
  y: number;
  angle: number;
}

export interface DetectorElement {
  id: string;
  name: string;
  link: string;
  lane: number;
  pos: number;
  x: number;
  y: number;
  angle: number;
  length: number | null;
  end_x?: number;
  end_y?: number;
}

export interface DataCollectionPointElement {
  id: string;
  name: string;
  link: string;
  lane: number;
  pos: number;
  x: number;
  y: number;
  angle: number;
}

export interface TravelTimeSectionGeoElement {
  id: string;
  name: string;
  start: NetworkElementEndpoint;
  end: NetworkElementEndpoint;
}

export interface QueueCounterElement {
  id: string;
  name: string;
  link: string;
  pos: number;
  x: number;
  y: number;
  angle: number;
}

export interface StopSignElement {
  id: string;
  name: string;
  link: string;
  lane: number;
  pos: number;
  x: number;
  y: number;
  angle: number;
}

export interface PtStopElement {
  id: string;
  name: string;
  link: string;
  lane: number;
  pos: number;
  x: number;
  y: number;
  angle: number;
  length: number | null;
  end_x?: number;
  end_y?: number;
}

export interface PedAreaElement {
  id: string;
  name: string;
  polygon: [number, number][];
}

export interface NetworkElements {
  signal_heads: SignalHeadElement[];
  vehicle_inputs: VehicleInputElement[];
  reduced_speed_areas: ReducedSpeedAreaElement[];
  des_speed_decisions: DesSpeedDecisionElement[];
  detectors: DetectorElement[];
  data_collection_points: DataCollectionPointElement[];
  traveltime_sections_geo: TravelTimeSectionGeoElement[];
  queue_counters: QueueCounterElement[];
  stop_signs: StopSignElement[];
  pt_stops: PtStopElement[];
  ped_areas: PedAreaElement[];
}

export interface NetworkData {
  links: {
    id: string;
    name: string;
    points: [number, number][];
    lanes: number;
    lane_widths?: number[];
    width_total?: number;
    length: number;
    is_connector?: boolean;
    from_link?: string | null;
    to_link?: string | null;
    from_pos?: number | null;
    to_pos?: number | null;
  }[];
  nodes: { id: string; name: string; x: number; y: number; polygon: [number, number][] }[];
  detectors: { id: string; name: string }[];
  traveltime_sections: { id: string; name: string }[];
  elements?: NetworkElements;
  // P2: which .inpx this response was parsed from -- "working" when a
  // session_id was passed and that agent session has an in-progress working
  // copy (runtime/agent_sessions/<session_id>/working.inpx), "active"
  // otherwise (the uploaded/default dataset, unchanged).
  source?: { view: "working" | "active"; session_id?: string };
}

export interface ActiveDataset {
  source: "default" | "uploaded" | string;
  kind?: "default" | "inpx" | "zip" | string;
  filename: string;
  path?: string;
  uploaded_at?: string | null;
  revision?: string;
  has_project_db?: boolean;
  has_scenarios?: boolean;
  has_modifications?: boolean;
  has_real_counts?: boolean;
  // Unit 1.4: which 프로젝트 (workspace) this dataset belongs to.
  workspace_id?: string;
}

// 모델 건강검진 (model health-check) — GET /api/health-check's report shape,
// mirrored 1:1 from backend/services/agent/health_check.py's HealthCheckRunner.
export interface HealthCheckLocalizedText {
  ko: string;
  en: string;
  ja: string;
}

// AI handoff (2026-07-28): backend/services/agent/health_check.py derives
// this per finding from the constraint's own applies_to + the CURRENT
// write-tool registry (never a hand-maintained id->kind map) --
// "fix" = a shipped write verb can address it directly, "inspect" = no
// direct verb but the agent can investigate/explain, "none" = a documented,
// always-refused edit (e.g. evaluation config) -- no button should ever be
// rendered for "none". `target` is deliberately bounded (see that module's
// _fix_target/_inspect_target) -- for an aggregate finding covering many
// objects it carries a true `count` plus a FEW example ids, never the full
// list.
export interface HealthCheckSuggestedAction {
  kind: "fix" | "inspect" | "none";
  tool: string | null;
  target: Record<string, unknown>;
}

export interface HealthCheckFinding {
  constraint_id: string;
  severity: "issue" | "warning" | "info";
  tier: "refuse" | "warn";
  title: HealthCheckLocalizedText;
  // null only on a per-constraint truncation-marker entry (see
  // truncated_additional_count) — every real finding always carries a message.
  message: HealthCheckLocalizedText | null;
  details: Record<string, unknown>;
  object_refs: string[];
  truncated_additional_count?: number;
  suggested_action: HealthCheckSuggestedAction;
}

export interface HealthCheckEditTimeOnlyEntry {
  constraint_id: string;
  title: HealthCheckLocalizedText;
  tier: "refuse" | "warn";
  phase: string;
}

export interface HealthCheckReport {
  generated_at: string;
  dataset: { name: string; revision: string | null; source: string | null };
  summary: {
    checked_n: number;
    edit_time_only_n: number;
    findings_by_severity: { issue: number; warning: number; info: number };
  };
  findings: HealthCheckFinding[];
  edit_time_only: HealthCheckEditTimeOnlyEntry[];
  stats: {
    links: number;
    nodes: number;
    vehicle_inputs: number;
    signal_controllers: number;
    conflict_areas: number;
    routing_decisions: number;
    routes: number;
    vehicle_compositions: number;
    des_speed_decisions: number;
  };
}

export interface ScenarioResults {
  run_count: number;
  network: Record<string, number>;
  movements: { OBJECT_ID: string; [k: string]: number | string }[];
  movement_interval_class?: {
    OBJECT_ID: string;
    ARG_INTERVAL?: number | string | null;
    ARG_VEHICLECLASS?: string | null;
    [k: string]: number | string | null | undefined;
  }[];
  traveltime: { OBJECT_ID: string; [k: string]: number | string }[];
  traveltime_interval_class?: {
    OBJECT_ID: string;
    ARG_INTERVAL?: number | string | null;
    ARG_VEHICLECLASS?: string | null;
    [k: string]: number | string | null | undefined;
  }[];
  datacollection: { OBJECT_ID: string; [k: string]: number | string }[];
  queues: { OBJECT_ID: string; [k: string]: number | string }[];
  node_directions?: {
    node_id: string;
    node_name: string;
    incoming: Record<string, number>;
    outgoing: Record<string, number>;
    /** MEASURED VEHS-weighted VEHDELAY per incoming approach, in seconds
     * (unit 1.5 D5). NULLABLE ON PURPOSE: null means "not measured" and must
     * never be rendered as 0 — see DirectionDelayChart's no-data state. */
    incoming_delay_sec?: Record<string, number | null>;
    incoming_lanes?: Record<string, number>;
    outgoing_lanes?: Record<string, number>;
    lane_flows?: {
      from_direction: string;
      to_direction: string;
      from_lane: number;
      to_lane: number;
      volume: number;
      estimated?: boolean;
    }[];
  }[];
}

export type SummaryTone = "neutral" | "success" | "warning" | "error";

export interface CompareSummaryStatusBand {
  tone?: SummaryTone | string;
  label?: string;
  title?: string;
  message?: string;
  detail?: string;
}

export interface CompareSummaryCard {
  id: string;
  label: string;
  value: number | string;
  unit?: string;
  description?: string;
  delta?: number;
  trend?: "up" | "down" | "flat" | string;
  tone?: SummaryTone | string;
}

export interface CompareSummaryRankingItem {
  scenario_id: string;
  scenario_name?: string;
  color?: string;
  rank?: number;
  score?: number;
  score_label?: string;
  reason?: string;
}

export interface CompareSummaryInsight {
  id: string;
  title: string;
  message: string;
  tone?: SummaryTone | string;
  scenario_id?: string;
  metric?: string;
  value?: number;
  unit?: string;
}

export interface CompareSummaryResponse {
  requested_ids?: string[];
  scenario_ids?: string[];
  invalid_ids?: string[];
  missing_result_ids?: string[];
  network_metrics?: Record<
    string,
    {
      network_key: string;
      values: Record<string, number | null>;
      average: number | null;
      min: number | null;
      max: number | null;
      spread: number | null;
    }
  >;
  metric_leaders?: Record<
    string,
    {
      scenario_id: string | null;
      value: number | null;
      higher_is_better: boolean;
    }
  >;
  health_scores?: Array<{
    scenario_id: string;
    score: number;
    label: string;
    components: Record<string, number | null>;
  }>;
  top_congested_nodes?: Array<{
    node_id: string;
    node_name: string;
    average_total: number | null;
    min_total: number | null;
    max_total: number | null;
    spread: number | null;
    scenario_totals: Record<string, number | null>;
    leader: {
      scenario_id: string | null;
      value: number | null;
    };
  }>;
  status_band?: CompareSummaryStatusBand;
  summary_cards?: CompareSummaryCard[];
  scenario_ranking?: CompareSummaryRankingItem[];
  key_insights?: CompareSummaryInsight[];
  // Camel-case compatibility in case backend serializer differs.
  statusBand?: CompareSummaryStatusBand;
  summaryCards?: CompareSummaryCard[];
  scenarioRanking?: CompareSummaryRankingItem[];
  keyInsights?: CompareSummaryInsight[];
}

export interface SimulationIncident {
  link_id: number;
  type: "lane_closure" | "full_closure" | "speed_limit";
  closed_lanes: number;
  total_lanes: number;
  speed_limit: number;
  duration: number;
}

export interface SimulationStatusMessage {
  progress?: number;
  status?: string;
  error?: string | null;
}

export interface SimulationJob {
  id: string;
  scenario_result_id?: string;
  name?: string;
  status: "queued" | "running" | "done" | "error" | string;
  progress: number;
  params: {
    name?: string;
    scenario_id?: string | null;
    sim_duration?: number;
    random_seed?: number;
    vehicle_inputs?: Record<string, number>;
    signal_cycles?: Record<string, number>;
    signal_offsets?: Record<string, number>;
    incidents?: SimulationIncident[];
  };
  error?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  dataset_filename?: string | null;
  dataset_revision?: string | null;
  // Present on agent-run jobs (routers/simulations.py's
  // _run_agent_simulation_job) once status === "done" -- true when at least
  // one seed's vehicle-record (.fzp) output was armed AND written; drives
  // the /network page's real-run playback source picker.
  trajectory_recorded?: boolean;
  // RUN-PROGRESS UI (2026-07-28): seed-boundary progress bookkeeping --
  // backend/services/simulation_runs.py's create_job_record seeds these
  // defaults for every job (both the interactive single-run path and the
  // agent multi-seed path), and backend/routers/simulations.py's job
  // runners advance them at seed starts/completions and phase transitions
  // ONLY -- never by sampling inside a running simulation (see that
  // module's own comments). total_seeds is always 1 for the interactive
  // path (SimParams.random_seed is a single int); eta_s is null until at
  // least one seed has actually completed -- never a guessed number.
  total_seeds?: number;
  completed_seeds?: number;
  /** 1-based index of the seed currently arming/running/being collected,
   * or null between seeds / once the job is terminal. */
  current_seed?: number | null;
  phase?: "queued" | "arming" | "running" | "collecting" | "done" | "error" | string;
  per_seed_durations?: number[];
  /** Estimated seconds remaining, or null before the first seed completes
   * (honesty over false precision -- see the field comment above). */
  eta_s?: number | null;
  last_update_at?: string | null;
}

export interface SimulationResultsResponse {
  job_id: string;
  status: string;
  results: ScenarioResults;
}

export interface SimulationCapabilities {
  can_run: boolean;
  can_prepare_simulation?: boolean;
  reason: string | null;
  environment: string;
  pywin32_available: boolean;
  pywin32_error?: string | null;
  source_inpx?: string | null;
  source_inpx_exists?: boolean;
  dataset_source?: string | null;
  dataset_kind?: string | null;
  dataset_filename?: string | null;
  requires_uploaded_dataset?: boolean;
  requires_local_windows_backend?: boolean;
}

export type AssistantIntent = "general" | "status" | "diagnostic" | "settings" | "capabilities";

export interface AssistantStatus {
  model: string;
  model_path: string;
  model_path_exists: boolean;
  qwen_python_exists?: boolean;
  endpoint_configured: boolean;
  in_process_enabled: boolean;
  mode: string;
  state?: "standby" | "loading" | "ready" | "generating" | "failed" | "offline" | "model-missing" | string;
  ready: boolean;
  worker_available?: boolean;
  worker_running?: boolean;
  worker_pid?: number | null;
  worker_error?: string | null;
  router?: {
    intents: AssistantIntent[];
    rag_sources: string[];
  };
  tools?: {
    vissim_com?: VissimComToolStatus;
  };
}

export interface VissimComToolStatus {
  available?: boolean;
  executed?: boolean;
  connected?: boolean;
  reason?: string | null;
  error?: string;
  mode?: string;
  prog_id?: string | null;
  source_inpx_exists?: boolean;
  pywin32_available?: boolean;
}

export interface AssistantQueryRequest {
  message: string;
  selected_scenario_ids?: string[];
  selected_node_ids?: string[];
  // P6 — what the user clicked in the rendered network diagram. The backend
  // re-resolves both against the parsed active .inpx (links get lanes/length/
  // connector endpoints; an unknown id comes back as {"id", "missing": true})
  // and sanitizes/caps them at its own request boundary, so these are hints,
  // never trusted payload. Both askAssistant and streamAssistant JSON.stringify
  // this whole object, so no per-field plumbing is needed below.
  selected_link_ids?: string[];
  selected_element?: {
    type: string;
    id: string;
    name?: string;
    link?: string | null;
  } | null;
  // Multi-select (v2 UX unit, 2026-07-29): 2+ selected elements travel here
  // instead of the singular field above — see
  // backend/routers/assistant.py's `_SelectionFieldsMixin`, which accepts
  // (and merges) both, and lib/networkSelection.ts, which is where the
  // client decides which of the two to populate.
  selected_elements?: {
    type: string;
    id: string;
    name?: string;
    link?: string | null;
  }[];
  allow_tool_execution?: boolean;
  response_language?: "ko" | "en" | "ja";
}

export interface AssistantQueryResponse {
  intent: AssistantIntent;
  answer: string;
  model: string;
  model_status: AssistantStatus;
  runtime: {
    mode: string;
    error?: string;
    error_id?: string;
    recoverable?: boolean;
    attempts?: number;
  };
  sources: AssistantSource[];
  answer_format?: Array<{
    heading: string;
    instruction: string;
  }>;
  tool_results: Record<string, unknown>;
  safety: {
    checked: boolean;
    allowed: boolean;
    reason: string;
  };
}

export type AssistantStreamEvent =
  | {
      type: "status";
      stream_id?: string;
      phase?: string;
    }
  | {
      type: "metadata";
      stream_id?: string;
      intent?: AssistantIntent;
      runtime_mode?: string;
      response?: AssistantQueryResponse;
    }
  | {
      type: "chunk";
      stream_id?: string;
      text?: string;
    }
  | {
      type: "done";
      stream_id?: string;
      response?: AssistantQueryResponse;
    };

export interface AssistantStreamHandlers {
  onStatus?: (event: Extract<AssistantStreamEvent, { type: "status" }>) => void;
  onMetadata?: (response: AssistantQueryResponse) => void;
  onChunk?: (text: string) => void;
}

// --- M1.4 read-only agent (POST /api/assistant/agent/query/stream) --------
// M2.3 extends this with the write-approval flow: a paused turn's
// approval_request event / done.response.approval, and POST
// /api/assistant/agent/approve (approveAgentAction below) to resolve it.

export interface AgentQueryRequest {
  message: string;
  response_language?: "ko" | "en" | "ja";
  // M3.3 backend capability, M4 frontend wiring: an earlier turn's
  // done.response.session_id in the SAME open conversation resumes that
  // session's work dir server-side (state reload, accumulated guardrail
  // warnings, clarification memory) -- see
  // backend/routers/assistant.py's AgentQueryRequest.session_id and
  // docs/phase2/DECISIONS.md's 2026-07-24 M3.3 entry. Omitted/undefined
  // mints a fresh session exactly as before. Must match
  // ^[0-9a-f]{8,64}$ (the backend's _SESSION_ID_RE) -- always satisfied
  // here since the only source is a prior done.response.session_id, never
  // free-typed by the client.
  session_id?: string;
  // P7: what the user has clicked in the rendered network / selected on the
  // dashboard, forwarded so the agent knows what "이 링크 / this intersection"
  // refers to. Same fields, same sanitization and the same re-resolution as
  // AssistantQueryRequest's (backend/services/network_selection.py), except
  // the agent path resolves them against THIS session's working copy when it
  // has one, so an edited link reads back post-edit. Omitted/empty is the
  // pre-P7 behavior exactly. Context only -- the selection never widens what
  // the agent may do; every edit still goes through the approval card.
  selected_scenario_ids?: string[];
  selected_node_ids?: string[];
  selected_link_ids?: string[];
  selected_element?: {
    type: string;
    id: string;
    name?: string;
    link?: string | null;
  } | null;
  // Multi-select (v2 UX unit, 2026-07-29): same split as
  // AssistantQueryRequest's field above.
  selected_elements?: {
    type: string;
    id: string;
    name?: string;
    link?: string | null;
  }[];
}

// Unit #43's diagnosis ladder for ONE node, unit #47's wire shape for it
// (backend/services/agent/geh_ladder.py's `_rung`, trimmed by
// orchestrator.py's `_diagnosis_step_payload`). `rung` is one of
// DEMAND_LEVEL / ROUTING_SPLIT / CAPACITY_LIMIT / SIGNAL_TIMING /
// UNCLASSIFIABLE; a node the model already reproduces has no diagnosis at all
// and never appears here. `why` and `next_action.text` are already localized
// server-side into all three languages -- the client picks, it never composes.
// `mrwa_step` is the MRWA §2.12.1 calibration step that AUTHORISES the next
// action, and is null exactly for SUPPLY_MISSING_EVIDENCE (there is no
// guideline step for "we could not classify this").
export interface AgentDiagnosisNode {
  node_id: string;
  node_name?: string | null;
  rung: string;
  why: { ko: string; en: string; ja: string };
  next_action: {
    code: string;
    mrwa_step: number | null;
    citation: string;
    text: { ko: string; en: string; ja: string };
  };
  missing_evidence: string[];
  observed_total?: number | null;
  modelled_total?: number | null;
  node_total_geh?: number | null;
  geh_threshold?: number | null;
  node_control?: string | null;
}

// The per-step block above, plus the report-level tally. `nodes` is capped
// server-side (orchestrator.py's _DIAGNOSIS_WIRE_CAP) while `total` stays the
// real count, so a big report says so instead of quietly shortening itself.
export interface AgentDiagnosis {
  nodes: AgentDiagnosisNode[];
  total: number;
  truncated: boolean;
  by_rung: Record<string, string[]>;
  geh_threshold?: number | null;
}

export interface AgentStep {
  summary: string;
  tool: string;
  status: string;
  // Present only for a successfully-dispatched run_simulation step
  // (backend/services/agent/orchestrator.py's step-emission choke point,
  // read off the tool's own returned data, never parsed from `summary`).
  // Drives AgentRunDashboardHint's replay-availability note.
  trajectory_recorded?: boolean;
  job_id?: string;
  // Unit #47: present only for a successful run_reality_check step, from the
  // same choke point and for the same reason (read off the tool's returned
  // data, never parsed from prose). Absent -- never present-and-empty -- when
  // the report had nothing to diagnose.
  diagnosis?: AgentDiagnosis;
}

// M2.3: one proposed write action inside an approval_request/done.approval
// payload. `params` mirrors the tool's own pydantic params model
// (backend/services/agent/tools_edit.py) -- e.g. set_vehicle_input_volume
// carries {input_no, volume}.
export interface AgentAction {
  tool: string;
  params: Record<string, unknown>;
  summary: string;
}

// M2.3: one preflight finding attached to a pending approval -- mirrors
// orchestrator.py's `warning_payload` (constraint_id + a message already
// localized server-side to the turn's response_language).
export interface AgentWarning {
  constraint_id: string;
  message: string;
}

// Approval-card mini-map (v2 UX unit, 2026-07-28): one element the pending
// write touches -- an id the client re-resolves against the network
// geometry it already fetches for the /network page (backend/services/
// agent/approval_focus.py's own docstring: this never ships raw
// coordinates itself, to keep the approval payload small and avoid a
// second, drifting copy of the same geometry data). `type` "link" resolves
// against NetworkData.links; every other type resolves against the
// matching NetworkElements list (e.g. "vehicle_input" ->
// elements.vehicle_inputs). "conflict_area" carries no geometry of its own
// (conflict areas have none) -- it exists only so the caption can name the
// conflict area itself alongside the two links drawn for it.
// The optional fields below are per-type extras the backend attaches only
// when it has something true to say (sibling-disambiguation unit,
// 2026-07-28): `approval_focus.add_element` DROPS an empty value rather than
// nulling it, so an element with nothing extra -- and every network without
// vehicle compositions -- still ships the historical `{type, id, label}`
// exactly. They are all vehicle-input fields today:
//   * `composition` / `composition_no` -- WHICH vehicle class this input
//     feeds. `label` alone cannot disambiguate a stack: all three of link
//     620's inputs are literally named "Jennalup ST_WB".
//   * `composition_varies_by_interval` -- present (true) only when the input
//     feeds different classes in different intervals, in which case the two
//     fields above are absent because there is no single honest answer.
//   * `siblings` -- ids of the OTHER vehicle inputs drawn at this exact
//     coordinate, capped at 8. Ids only, never geometry (this payload
//     deliberately ships no coordinates -- see AgentApprovalFocus below); the
//     client re-resolves them against the network geometry it already fetches.
export interface AgentApprovalFocusElement {
  type: "link" | "conflict_area" | "signal_head" | "vehicle_input" | "des_speed_decision";
  id: string;
  label: string;
  composition?: string;
  composition_no?: string;
  composition_varies_by_interval?: boolean;
  siblings?: string[];
}

// backend/services/agent/approval_focus.py's `resolve_focus` return shape.
// `bbox` is `null` exactly when nothing resolved (unsupported tool, no
// working copy yet, or a target genuinely absent from this network) -- the
// UI's contract is to render no map at all in that case, never an error
// (ApprovalFocusMap.tsx). `coordinate_note` is a short, English,
// NOT-user-facing diagnostic tag -- logs/debugging only, never shown.
export interface AgentApprovalFocus {
  elements: AgentApprovalFocusElement[];
  bbox: { x_min: number; y_min: number; x_max: number; y_max: number } | null;
  coordinate_note: string;
}

// Network component search (v2 UX unit, 2026-07-28) -- backend/services/
// network_search.py's response shape. Unlike AgentApprovalFocusElement's
// narrow union, a search hit's own `focus.elements` type is a plain string:
// search is exhaustive over every drawable family (19 of them, including
// families no write verb ever targets -- nodes, routes, PT stops, ...), so
// the union above would have to grow to match and would then describe two
// different things for two different reasons. `label` ships RAW PARTS only
// (never a localized noun or composed sentence -- the language regression
// fixed in bd63874 came from exactly that shortcut); the UI composes the
// human string itself (see lib/networkSearchLabel.ts).
export interface NetworkSearchFocusElement {
  type: string;
  id: string;
  label: string;
}

export interface NetworkSearchFocus {
  elements: NetworkSearchFocusElement[];
  bbox: { x_min: number; y_min: number; x_max: number; y_max: number } | null;
}

export interface NetworkSearchHitLabel {
  type: string;
  id: string;
  name: string;
  detail: Record<string, unknown>;
}

export interface NetworkSearchHit {
  type: string;
  id: string;
  name: string;
  label: NetworkSearchHitLabel;
  match: { rank: number; field: string };
  focus: NetworkSearchFocus;
}

export interface NetworkSearchResponse {
  query: string;
  total: number;
  returned: number;
  limit: number;
  truncated: boolean;
  results: NetworkSearchHit[];
  source: { view: string };
}

// M2.3: the full payload backend/routers/assistant.py emits both from the
// dedicated `approval_request` NDJSON event and, identically, under
// `done.response.approval` (a client that only inspects `done` still gets
// it -- see that router's own docstring). `assumptions` is always `[]`
// today -- no structured assumption signal exists yet from the planner --
// but is part of the wire shape so the UI can render it once it starts
// populating, at no future contract-breaking cost. `focus` (v2 UX unit) is
// additive -- always present (never raises server-side), possibly the
// null-bbox "no focus" shape.
// Unit #44's MRWA Table 5-6 car-following proposal SET, as the approval card
// receives it (unit #47). It exists because the approval machinery authorises
// each write's signature independently: the card pauses on ONE of the three
// (`ax`, `bx_add`, `bx_mult`), and one third of one table row is not a change
// anybody can judge on its own. Every sentence in here is the PROPOSAL's own,
// already written server-side in three languages -- `partial_approval_warning`
// is quoted verbatim from saturation_flow.PARTIAL_APPROVAL_WARNING, which is
// also the last sentence of each member's `rationale`. The UI renders these; it
// must never compose its own version of any of them.
//
// `grant.reason` is the RE-MINT verdict at card-build time (GRANTED /
// RADIUS_DRIFTED / RADIUS_NOT_FULLY_NAMED / UNKNOWN_SET / ...), never a grant:
// a grant exists only as a live object inside the server process and nothing
// about one is ever serialized (backend/services/agent/car_following_broker.py).
export interface AgentCarFollowingSet {
  status: "PROPOSED" | "REFUSED";
  set_id?: string;
  set_members?: string[];
  behavior_no?: string;
  behavior_scope?: string | null;
  expected_after_apply?: Record<string, number>;
  // What the behaviour holds RIGHT NOW for those same members, read off the
  // live shadow model when the card was built (refuter, #47). The set is
  // approved one member per card, so this is what lets the panel tell a member
  // that has already landed from one that has not -- a claim about the MODEL,
  // never about approval history, which nothing here records. Null when the
  // behaviour could not be read back.
  current_values?: Record<string, number | null> | null;
  applied_row?: { saturation_flow_pcu?: number; additive?: number; multiplicative?: number };
  // BRACKETED carries both neighbouring rows so rounding DOWN is auditable;
  // EXACT carries neither, because there was nothing to round between.
  lookup?: {
    status?: string;
    measured_pcu?: number | null;
    bracket_lower?: { saturation_flow_pcu?: number; additive?: number; multiplicative?: number } | null;
    bracket_upper?: { saturation_flow_pcu?: number; additive?: number; multiplicative?: number } | null;
    policy?: string;
    policy_basis?: string;
    citation?: string;
  };
  measurement?: {
    saturation_flow_pcu?: number | null;
    provenance?: string;
    method?: string | null;
    attribution?: string | null;
    measured_at?: string | null;
    samples?: number | null;
    vehicles_per_sample?: number | null;
  };
  partial_approval_warning?: { ko: string; en: string; ja: string };
  consent_radius?: { reference_count?: number; link_total?: number; complete?: boolean };
  consent_radius_phrase?: string;
  // Both paths the modeller can take, forecast by the builder: the write
  // issued on its own vs. dispatched through the approval machinery.
  preflight_forecast?: {
    paths?: Record<string, { expected_to_refuse?: boolean; constraint_id?: string; why?: string }>;
  };
  proposals?: {
    params?: Record<string, unknown> | null;
    rationale?: { ko: string; en: string; ja: string };
    reason_code?: string | null;
  }[];
  rationale?: { ko: string; en: string; ja: string };
  reason_code?: string | null;
  grant?: {
    reason: string;
    available: boolean;
    consented_radius?: Record<string, unknown> | null;
    current_radius?: Record<string, unknown> | null;
  };
}

// Unit #51-c: the SECOND grant species' card payload -- one finished, held-out
// parameter search, as the approval card reads it. Same shape of thing as
// AgentCarFollowingSet above and it exists for the same reason: the approval
// machinery authorises each write's signature independently, so a card that
// showed only the action it paused on would present ONE COORDINATE of a searched
// vector as if it were the change.
//
// What is different from the Table 5-6 set, and why the copy differs with it:
// these values were CHOSEN by a search inside a published band, not read out of
// a published table with a measured number. `evidence_rank` is the backend's own
// three-language sentence saying exactly that, and DECISIONS.md 2026-08-20 made
// showing it a closing requirement of the decision -- so the panel renders it
// UNFOLDED, never behind a <details>.
//
// `preflight_forecast.paths` carries BOTH paths because they genuinely differ
// since #51-b: the same write refused when issued directly, clean through the
// approval machinery under this search's own grant. A single verdict would now
// be misleading, which is why nothing here reads `verdict`.
//
// `grant.reason` is the RE-MINT verdict at card-build time, never a grant: a
// grant exists only as a live object in the server process
// (backend/services/agent/searched_behaviour_broker.py).
export interface AgentSearchedSearch {
  status: "PROPOSED" | "REFUSED";
  unit?: string;
  decision?: string;
  search_id?: string | null;
  behavior_no?: string | null;
  reason_code?: string | null;
  /** The parameters the winner actually MOVED -- one approval card each. */
  searched_parameters?: string[];
  expected_after_apply?: Record<string, number>;
  /** What the behaviour holds RIGHT NOW for those same parameters, read off the
   * live shadow model at card-build time. Null when it could not be read. */
  current_values?: Record<string, number | null> | null;
  /** Searched but left at baseline -- the screen could not show an effect. */
  unchanged_parameters?: string[];
  rationale?: { ko: string; en: string; ja: string };
  /** The decision's closing requirement. Rendered unfolded. */
  evidence_rank?: { ko: string; en: string; ja: string };
  preflight_forecast?: {
    paths?: Record<
      string,
      {
        expected_to_refuse?: boolean | null;
        constraint_id?: string;
        condition?: string;
        refuses_if_radius_drifts?: string;
        why?: { ko: string; en: string; ja: string };
      }
    >;
    /** The two resolutions #51 offered, each carrying the USER's answer on it
     * (`status`: DECIDED / REJECTED_BY_USER). The chips render these fields --
     * the frontend never composes the verdict itself. */
    resolutions?: {
      key: string;
      status?: string;
      decided?: boolean;
      decision?: string;
      landed_in?: string;
      ko: string;
      en: string;
      ja: string;
    }[];
  };
  consent_radius?: { reference_count?: number; link_total?: number; complete?: boolean } | null;
  consent_radius_phrase?: string;
  holdout?: {
    accepted?: boolean;
    code?: string | null;
    holdout_nodes?: string[];
    calibration_nodes?: string[];
    unseen_seed?: number | null;
    winner_objective?: number | null;
    baseline_objective?: number | null;
  };
  ledger?: { search_id?: string; rows?: number; digest?: string };
  candidates?: number | null;
  objective?: string;
  space?: { parameter: string; low: number; high: number; citation: string }[];
  citation?: string | null;
  assumptions?: string[];
  grant?: {
    reason: string;
    available: boolean;
    consented_radius?: Record<string, unknown> | null;
    current_radius?: Record<string, unknown> | null;
  };
}

export interface AgentApproval {
  approval_id: string;
  actions: AgentAction[];
  warnings: AgentWarning[];
  assumptions: string[];
  focus?: AgentApprovalFocus;
  /** Unit #47: present only when this pause is one member of a Table 5-6
   * car-following set. Additive, payload-level (a sibling of actions/warnings,
   * exactly like `focus`) so `actions` stays byte-unchanged. */
  car_following_set?: AgentCarFollowingSet;
  /** Unit #51-c: the same, for a pause that is one write of a searched vector.
   * The two are mutually exclusive in practice -- the species' signature spaces
   * are disjoint by parameter -- but nothing here depends on that. */
  searched_search?: AgentSearchedSearch;
}

export interface AgentQueryResponse {
  answer: string;
  kind: "answer" | "clarification" | "cancelled" | "approval_request";
  steps: AgentStep[];
  session_id: string;
  /** Present iff kind === "approval_request". */
  approval?: AgentApproval;
  /** P8 (edited-model download): true iff THIS turn persisted a write --
   * backend/routers/assistant.py's _stream_agent_turn computes it from the
   * session's ArtifactManager.version advancing across the turn (nothing
   * else on the wire distinguishes a successful write step from a
   * successful read step). Drives whether FloatingAssistant shows a
   * "download the edited model" affordance under this turn's answer.
   * Absent/false on any turn that only read, was declined, or errored. */
  wrote_model?: boolean;
}

// P8 (edited-model download): one row from GET /api/assistant/agent's
// sibling download-listing router (backend/routers/agent_downloads.py).
// `version: null` means the session has cloned but never saved a write --
// `filename` is then "working.inpx", the session's only .inpx on disk.
export interface AgentDownloadEntry {
  session_id: string;
  /** Pre-shortened for display (first 8 hex chars) -- `session_id` itself
   * still carries the full id, needed to build the download URL. */
  session_id_short: string;
  version: number | null;
  is_latest: boolean;
  /** The actual on-disk filename (e.g. "edit_003.inpx"), NOT the decorated
   * name a download streams under -- see downloadAgentModel's own comment. */
  filename: string;
  /** ISO 8601 UTC. */
  modified_at: string;
  has_siblings: boolean;
  /** The CURRENT active dataset's filename -- best-effort (see
   * agent_downloads.py's list_downloads docstring for the caveat: a session
   * that outlives a later dataset swap reports the NEW dataset's name). */
  dataset_filename: string;
}

export type AgentStreamEvent =
  | {
      type: "status";
      stream_id?: string;
      phase?: string;
    }
  | ({
      type: "step";
      stream_id?: string;
    } & Partial<AgentStep> & { index?: number })
  | ({
      type: "approval_request";
      stream_id?: string;
    } & Partial<AgentApproval>)
  | {
      type: "chunk";
      stream_id?: string;
      text?: string;
    }
  // Backend heartbeat (backend/routers/assistant.py's _stream_agent_turn,
  // docs/phase2/DECISIONS.md 2026-07-24 heartbeat entry): emitted every
  // ~15s of queue silence during a long-running tool call (e.g. a 300s
  // RunContinuous) so an idle-timeout intermediary (the Next.js dev proxy)
  // never sees a long enough silent gap to kill the connection. Carries no
  // payload beyond stream_id -- handleAgentStreamLine below is a pure no-op
  // for it, same as any other event type it doesn't recognize.
  | {
      type: "ping";
      stream_id?: string;
    }
  // RUN-PROGRESS UI (2026-07-28): emitted the moment a run_simulation
  // dispatch SUBMITS a job to the simulation job queue -- well before the
  // run itself finishes (backend/routers/assistant.py's _stream_agent_turn,
  // backend/services/agent/tools_run.py's set_run_started_callback). Lets
  // the frontend start polling GET /api/simulations/{job_id} (SimulationJob's
  // seed-boundary progress fields) independently of this stream, which stays
  // blocked on the same turn until the run reaches a terminal state.
  | {
      type: "run_started";
      stream_id?: string;
      job_id?: string;
    }
  | {
      type: "done";
      stream_id?: string;
      response?: AgentQueryResponse;
    };

export interface AgentStreamHandlers {
  onStatus?: (event: Extract<AgentStreamEvent, { type: "status" }>) => void;
  onStep?: (event: Extract<AgentStreamEvent, { type: "step" }>) => void;
  onApprovalRequest?: (event: Extract<AgentStreamEvent, { type: "approval_request" }>) => void;
  /** RUN-PROGRESS UI: fires as soon as a run_simulation dispatch submits a
   * job, long before the run (and this stream) finishes -- see
   * AgentStreamEvent's "run_started" variant. */
  onRunStarted?: (event: Extract<AgentStreamEvent, { type: "run_started" }>) => void;
  onChunk?: (text: string) => void;
}

// M2.3: request body for POST /api/assistant/agent/approve. No
// response_language field on purpose -- the approve-turn's language is
// sourced server-side from the original proposal's pending_approval.json,
// never re-supplied by the client (see AgentApproveRequest in
// backend/routers/assistant.py).
export interface AgentApproveRequest {
  session_id: string;
  approval_id: string;
  approved: boolean;
  // U-2-core (D4's decision record, 2026-08-13 -- "the production approval
  // UI sends no `reason` at all ... the binding audit constraint is the
  // missing UI field"): the reviewer's optional one-line reason, wired from
  // the approval card's own input straight into the field the backend has
  // accepted since Phase 1.3 (AgentApproveRequest.reason,
  // backend/routers/assistant.py, cap MAX_APPROVAL_REASON_CHARS=600) but
  // this client never populated. Capped client-side at the same 600 chars
  // (FloatingAssistant.tsx's APPROVAL_REASON_MAX_CHARS) so a paste can
  // never overflow into a 422. Omitted (not sent as an empty string) when
  // the reviewer leaves it blank -- matches every pre-this-unit caller,
  // which simply never set the field at all.
  reason?: string;
}

export interface AssistantSource {
    kind: string;
    title: string;
    path?: string;
    section?: string;
    chunk_id?: string;
    retrieval?: string;
    vector_backend?: string;
    score?: number;
    source_file?: string;
    topic?: string;
    page_range?: string | null;
    snippet?: string;
    citation_label?: string;
}

export async function fetchScenarios(): Promise<ScenarioMeta[]> {
  const r = await apiFetch("/api/scenarios", { cache: "no-store" });
  return r.json();
}

export async function fetchResults(id: string): Promise<ScenarioResults> {
  const r = await apiFetch(`/api/scenarios/${id}/results`);
  return r.json();
}

export async function fetchCompare(ids: string[]): Promise<Record<string, ScenarioResults>> {
  const r = await apiFetch(`/api/scenarios/compare?ids=${ids.join(",")}`);
  if (!r.ok) {
    throw new Error(await r.text());
  }

  const payload = (await r.json()) as Record<string, unknown>;
  const normalized: Record<string, ScenarioResults> = {};
  for (const id of ids) {
    const result = payload[id];
    if (result && typeof result === "object") {
      normalized[id] = result as ScenarioResults;
    }
  }
  return normalized;
}

export async function fetchCompareSummary(ids: string[]): Promise<CompareSummaryResponse> {
  const r = await apiFetch(`/api/scenarios/compare-summary?ids=${ids.join(",")}`);
  if (!r.ok) {
    throw new Error(await r.text());
  }
  return (await r.json()) as CompareSummaryResponse;
}

export async function fetchAssistantStatus(): Promise<AssistantStatus> {
  const r = await apiFetch("/api/assistant/status", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// P8 (edited-model download): backend/routers/agent_downloads.py.
// `sessionId` omitted lists the latest edited model per session, newest
// first, across every session that has actually saved a write; passing it
// scopes to that one session's full retained version history instead.
export async function fetchAgentDownloads(options?: { sessionId?: string; limit?: number }): Promise<AgentDownloadEntry[]> {
  const params = new URLSearchParams();
  if (options?.sessionId) params.set("session_id", options.sessionId);
  if (options?.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  const r = await apiFetch(`/api/agent/downloads${query ? `?${query}` : ""}`, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function agentDownloadPath(sessionId: string, version: number | null): string {
  return `/api/agent/downloads/${sessionId}/${version === null ? "latest" : version}`;
}

// ---------------------------------------------------------------------------
// Roadmap row 2.4 -- network diff view (backend/routers/network_diff.py)
// ---------------------------------------------------------------------------
//
// A snapshot token is `"0"` (the session's pristine source -- what a
// rollback-to-version-0 restores), a saved version number as a string, the
// literal `"working"` (working.inpx) or the literal `"active"` (the
// workspace's current dataset). ONE space rather than two parameter modes:
// the backend router's docstring records the measurement behind that choice
// (0 of 1572 session dirs on the dev box hold two `edit_NNN.inpx`, so a
// version-pair-only picker would be empty for every session that exists).
export type NetworkDiffToken = string;

export interface NetworkDiffSide {
  token: NetworkDiffToken;
  kind: "pristine" | "version" | "working" | "active";
  /** 0 for the pristine source, N for edit_NNN, null for working/active. */
  version: number | null;
  filename: string;
  /** "session_meta" | "active_dataset_fallback" | "session" | "workspace" --
   * `active_dataset_fallback` means this session has no sidecar, so its v0 and
   * the `active` token are the SAME file. */
  resolved_from: string;
  /** ISO 8601 UTC, same shape as AgentDownloadEntry.modified_at. */
  modified_at: string;
}

/** One structured observation about the comparison. `messages` is the
 * `{lang: text}` i18n hook -- MEASURED EMPTY: services/genesis/diff.py never
 * populates it (backend/tests/test_network_diff_endpoint.py pins that), so the
 * view localizes by `code` and keeps `message` as the verbatim evidence line.
 * Preferred automatically if diff.py ever starts filling it. */
export interface NetworkDiffFinding {
  severity: "refuse" | "warn" | "info";
  code: string;
  message: string;
  messages: Record<string, string>;
  subject: string | null;
  context: Record<string, unknown>;
}

export interface NetworkDiffLaneChange {
  link_id: string;
  base_lanes: number;
  target_lanes: number;
  delta: number;
}

export interface NetworkDiffRewire {
  connector_id: string;
  base_from_link: string | null;
  target_from_link: string | null;
  base_to_link: string | null;
  target_to_link: string | null;
  changed_endpoints: string[];
}

export interface NetworkDiffLinkSection {
  added: string[];
  removed: string[];
  lane_count_changed: NetworkDiffLaneChange[];
  unchanged_count: number;
  change_count: number;
}

export interface NetworkDiffConnectorSection extends NetworkDiffLinkSection {
  rewired: NetworkDiffRewire[];
}

export interface NetworkDiffMovementRef {
  connector_id: string;
  from_link: string | null;
  to_link: string | null;
  label: string;
}

export interface NetworkDiffNodeMovement {
  node_id: string;
  present_in_base: boolean;
  present_in_target: boolean;
  base_movement_count: number;
  target_movement_count: number;
  added: NetworkDiffMovementRef[];
  removed: NetworkDiffMovementRef[];
  rewired: NetworkDiffRewire[];
  changed: boolean;
}

export interface NetworkDiffSignalCoverage {
  node_id: string;
  base_approaches: string[];
  base_signalized: string[];
  target_approaches: string[];
  target_signalized: string[];
  newly_signalized: string[];
  no_longer_signalized: string[];
  base_coverage: number | null;
  target_coverage: number | null;
  changed: boolean;
}

/** The position scope, verbatim from services/genesis/diff.py's PositionScope.
 * `reported` is false and there is no backend code path that sets it true;
 * `geometry_differs_count` is a COUNT of links whose polyline differs, never a
 * distance. The view must render it as exactly that -- see the module comment
 * on diff.py for why a positional delta here would inherit a ~14 m floor. */
export interface NetworkDiffPosition {
  reported: boolean;
  max_residual_m: number;
  residual_lane_widths: number;
  scale_term: number;
  basis: string;
  note: string;
  geometry_differs_count: number;
}

export interface NetworkDiffSummary {
  label: string;
  source: string | null;
  link_count: number;
  plain_link_count: number;
  connector_count: number;
  node_count: number;
  movement_count: number;
  signal_head_count: number;
  signalized_link_count: number;
}

/** Every section is null when `refused` -- deliberately, so a refusal can
 * never be read as an empty diff (services/genesis/diff.py's central promise).
 * Check `refused`/`ok` before touching a section. */
export interface NetworkStructuralDiff {
  refused: boolean;
  ok: boolean;
  change_count: number;
  has_changes: boolean;
  base: NetworkDiffSummary | null;
  target: NetworkDiffSummary | null;
  links: NetworkDiffLinkSection | null;
  connectors: NetworkDiffConnectorSection | null;
  reclassified: Array<{ link_id: string; base_kind: string; target_kind: string }> | null;
  node_movements: NetworkDiffNodeMovement[] | null;
  signal_coverage: NetworkDiffSignalCoverage[] | null;
  position: NetworkDiffPosition;
  findings: NetworkDiffFinding[];
}

export interface NetworkDiffResponse {
  session_id: string;
  session_id_short: string;
  base: NetworkDiffSide;
  target: NetworkDiffSide;
  /** True when both tokens resolved to the same file. An empty diff then has a
   * stated reason, which is not the same statement as "nothing changed". */
  identical_sources: boolean;
  available: { versions: number[]; has_working: boolean; has_pristine: boolean };
  diff: NetworkStructuralDiff;
}

/** Statuses the diff page has a dedicated state for. 404 is BOTH "no such
 * session" and "another project's session" -- the backend refuses to
 * distinguish them (no existence oracle), and neither may this client. */
export class NetworkDiffError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "NetworkDiffError";
    this.status = status;
  }
}

export async function fetchNetworkDiff(
  sessionId: string,
  base: NetworkDiffToken,
  target: NetworkDiffToken,
): Promise<NetworkDiffResponse> {
  const params = new URLSearchParams({ session_id: sessionId, base, target });
  const r = await apiFetch(`/api/network/diff?${params.toString()}`, { cache: "no-store" });
  if (!r.ok) throw new NetworkDiffError(await readApiError(r), r.status);
  return r.json();
}

// The backend names the actual streamed file server-side (dataset stem +
// short session id + version, zip when sibling assets exist else bare
// .inpx -- see agent_downloads.py's download_model docstring); read it back
// off Content-Disposition rather than re-deriving it client-side, since the
// list endpoint's own `filename` field is deliberately the raw on-disk name
// (e.g. "edit_003.inpx"), not this decorated one.
export async function downloadAgentModel(
  sessionId: string,
  version: number | null,
): Promise<{ blob: Blob; filename: string }> {
  const r = await apiFetch(agentDownloadPath(sessionId, version), { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  const blob = await r.blob();
  const filename = filenameFromContentDisposition(r.headers.get("content-disposition")) ?? `${sessionId}.inpx`;
  return { blob, filename };
}

// ONE reader for this header, shared by `downloadAgentModel` and
// `fetchGenesisArtifact` -- a second copy beside the genesis one was written
// and deleted during U-1-quick, which is exactly the duplication CLAUDE.md
// names. Any path separator in the recovered name is dropped before it is
// returned: the name goes straight to an `<a download>`, and a server-supplied
// string is not this app's to trust with a directory component.
function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return basenameOnly(decodeURIComponent(utf8Match[1]));
    } catch {
      // Malformed percent-encoding -- fall through to the plain filename.
    }
  }
  const plainMatch = header.match(/filename="([^"]+)"/i);
  return plainMatch ? basenameOnly(plainMatch[1]) : null;
}

function basenameOnly(value: string): string | null {
  const name = value.split(/[\\/]/).pop()?.trim();
  return name ? name : null;
}

export async function askAssistant(payload: AssistantQueryRequest, signal?: AbortSignal): Promise<AssistantQueryResponse> {
  const init: RequestInit = {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  };
  const r = await apiDirectMutationFetch("/api/assistant/query", init);
  if (r.ok) return r.json();

  const firstError = await readApiError(r);
  if (r.status >= 500 && !signal?.aborted) {
    await sleepForRetry(700);
    const retry = await apiDirectMutationFetch("/api/assistant/query", init, { sameOrigin: false });
    if (retry.ok) return retry.json();
    throw new Error(await readApiError(retry));
  }

  throw new Error(firstError);
}

export interface DashboardCurationRequest {
  message: string;
  selected_scenario_ids: string[];
  selected_node_ids?: string[];
  response_language?: string;
  audience?: "plain" | "expert";
}

export interface DashboardInsightCard {
  title: string;
  body: string;
  tone: "info" | "success" | "warning";
  related_chart: string | null;
}

export interface DashboardCurationSpec {
  headline: string;
  charts: string[];
  insight_cards: DashboardInsightCard[];
  fallback: boolean;
  error?: string;
  model?: string;
  audience?: string;
  /** Optional per-metric drill-down into the network-kpi-comparison section
   * (backend/services/chart_catalog.py KPI_METRIC_LABELS is the vocabulary).
   * Absent, empty, or an unrecognized/full set all mean "no restriction" --
   * the dashboard shows all six metric cards, same as before this field
   * existed. A fallback spec (fallback: true) never carries this field. */
  kpi_metrics?: string[];
}

export async function curateDashboard(
  payload: DashboardCurationRequest,
  signal?: AbortSignal,
): Promise<DashboardCurationSpec> {
  const init: RequestInit = {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  };
  const r = await apiDirectMutationFetch("/api/assistant/curate", init);
  if (r.ok) return r.json();

  const firstError = await readApiError(r);
  if (r.status >= 500 && !signal?.aborted) {
    await sleepForRetry(700);
    const retry = await apiDirectMutationFetch("/api/assistant/curate", init, { sameOrigin: false });
    if (retry.ok) return retry.json();
    throw new Error(await readApiError(retry));
  }

  throw new Error(firstError);
}

export async function streamAssistant(
  payload: AssistantQueryRequest,
  handlers: AssistantStreamHandlers = {},
  signal?: AbortSignal,
): Promise<AssistantQueryResponse> {
  const init: RequestInit = {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  };
  const response = await apiDirectMutationFetch("/api/assistant/query/stream", init);
  if (response.ok) return readAssistantEventStream(response, handlers);

  const firstError = await readApiError(response);
  if (response.status >= 500 && !signal?.aborted) {
    await sleepForRetry(700);
    const retry = await apiDirectMutationFetch("/api/assistant/query/stream", init, { sameOrigin: false });
    if (retry.ok) return readAssistantEventStream(retry, handlers);
    throw new Error(await readApiError(retry));
  }

  throw new Error(firstError);
}

async function readAssistantEventStream(response: Response, handlers: AssistantStreamHandlers): Promise<AssistantQueryResponse> {
  if (!response.body) {
    return readAssistantEventLines(await response.text(), handlers);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: AssistantQueryResponse | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      finalResponse = handleAssistantStreamLine(line, handlers) ?? finalResponse;
    }
  }

  buffer += decoder.decode();
  finalResponse = handleAssistantStreamLine(buffer, handlers) ?? finalResponse;
  if (finalResponse) return finalResponse;
  throw new Error("Assistant stream ended before a final response was received.");
}

function readAssistantEventLines(text: string, handlers: AssistantStreamHandlers): AssistantQueryResponse {
  let finalResponse: AssistantQueryResponse | null = null;
  for (const line of text.split(/\r?\n/)) {
    finalResponse = handleAssistantStreamLine(line, handlers) ?? finalResponse;
  }
  if (finalResponse) return finalResponse;
  throw new Error("Assistant stream ended before a final response was received.");
}

function handleAssistantStreamLine(line: string, handlers: AssistantStreamHandlers): AssistantQueryResponse | null {
  const trimmed = line.trim();
  if (!trimmed) return null;

  const event = JSON.parse(trimmed) as AssistantStreamEvent;
  if (event.type === "status") {
    handlers.onStatus?.(event);
    return null;
  }
  if (event.type === "metadata") {
    if (event.response) handlers.onMetadata?.(event.response);
    return null;
  }
  if (event.type === "chunk") {
    if (event.text) handlers.onChunk?.(event.text);
    return null;
  }
  if (event.type === "done" && event.response) {
    return event.response;
  }
  return null;
}

export async function streamAgentQuery(
  payload: AgentQueryRequest,
  handlers: AgentStreamHandlers = {},
  signal?: AbortSignal,
): Promise<AgentQueryResponse> {
  const init: RequestInit = {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  };
  const response = await apiDirectMutationFetch("/api/assistant/agent/query/stream", init);
  if (response.ok) return readAgentEventStream(response, handlers);

  const firstError = await readApiError(response);
  if (response.status >= 500 && !signal?.aborted) {
    await sleepForRetry(700);
    const retry = await apiDirectMutationFetch("/api/assistant/agent/query/stream", init, { sameOrigin: false });
    if (retry.ok) return readAgentEventStream(retry, handlers);
    throw new Error(await readApiError(retry));
  }

  throw new Error(firstError);
}

// M2.3: thrown by approveAgentAction on a 404 (unknown/stale/consumed
// pending approval -- backend/routers/assistant.py's approve_agent_action
// 404s the same way for both cases, with no further detail). Carries the
// HTTP status explicitly rather than making the UI string-match the
// (English-only, backend-owned) error detail text, so a stale approval can
// get its own graceful, localized inline message instead of the generic
// assistant-error copy.
//
// Unit U8 added two more statuses this class now carries, for the same reason:
// 401 (nobody is signed in) and 403 (signed in, but not reviewer/admin). The
// card reacts to the SERVER's answer -- it never guesses a role client-side --
// so it needs the status, and for 403 it needs `message`, which is the
// backend's own role-naming detail and is already localized server-side.
export class AgentApprovalError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AgentApprovalError";
    this.status = status;
  }
}

/** Approve/decline responses the approval card has a dedicated state for.
 * Listed once so the first attempt and the 5xx retry below cannot drift. */
const APPROVAL_STATUSES_WITH_OWN_UI = [401, 403, 404];

// Live-bug fix (docs/phase2/DECISIONS.md, 2026-07-24 heartbeat entry):
// thrown by readAgentEventStream when the NDJSON body ends (or its reader
// throws) WITHOUT ever delivering a `done` event -- a dropped/killed
// connection, as opposed to a clean stream that completed normally or a
// user-initiated AbortError (readAgentEventStream re-throws aborts as-is so
// callers keep using their existing isAbortError check). Distinguished from
// a generic Error so FloatingAssistant.tsx can resolve the pending message
// into one clear, localized "connection lost, try again" state instead of
// either an eternal spinner or the generic assistant-error copy.
export class AgentStreamDisconnectedError extends Error {
  constructor(message = "Agent stream ended before a final response was received.", options?: { cause?: unknown }) {
    super(message, options);
    this.name = "AgentStreamDisconnectedError";
  }
}

function isAbortErrorLike(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && String((error as { name?: unknown }).name) === "AbortError";
}

// M2.3: resolves one pending write approval from an earlier streamAgentQuery
// turn's approval_request event. Reuses the exact same NDJSON reader/handler
// plumbing as streamAgentQuery (readAgentEventStream below, no parser
// duplication) -- approved:true streams the approved write's execution +
// postwrite verification, approved:false streams a short polite decline,
// both in the same NDJSON shape a fresh agent turn uses.
export async function approveAgentAction(
  payload: AgentApproveRequest,
  handlers: AgentStreamHandlers = {},
  signal?: AbortSignal,
): Promise<AgentQueryResponse> {
  const init: RequestInit = {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  };
  const response = await apiDirectMutationFetch("/api/assistant/agent/approve", init);
  if (response.ok) return readAgentEventStream(response, handlers);

  const firstError = await readApiError(response);
  // 404 = stale/consumed approval (M2.3); 401/403 = not signed in / wrong role
  // (U8). All three are answers the card renders differently, so all three
  // carry their status instead of collapsing into a generic Error.
  if (APPROVAL_STATUSES_WITH_OWN_UI.includes(response.status)) {
    throw new AgentApprovalError(firstError, response.status);
  }
  if (response.status >= 500 && !signal?.aborted) {
    await sleepForRetry(700);
    const retry = await apiDirectMutationFetch("/api/assistant/agent/approve", init, { sameOrigin: false });
    if (retry.ok) return readAgentEventStream(retry, handlers);
    if (APPROVAL_STATUSES_WITH_OWN_UI.includes(retry.status)) {
      throw new AgentApprovalError(await readApiError(retry), retry.status);
    }
    throw new Error(await readApiError(retry));
  }

  throw new Error(firstError);
}

async function readAgentEventStream(response: Response, handlers: AgentStreamHandlers): Promise<AgentQueryResponse> {
  if (!response.body) {
    return readAgentEventLines(await response.text(), handlers);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: AgentQueryResponse | null = null;

  // Live-bug fix: an intermediary (observed: the Next.js dev proxy) that
  // kills an idle connection mid-turn can surface here either as
  // reader.read() rejecting, or (more commonly observed) as the readable
  // stream simply ending (`done: true`) with no `done` NDJSON event ever
  // having been parsed -- the loop below falls through to the
  // AgentStreamDisconnectedError throw after it either way. A genuine
  // user-initiated abort (AbortController.signal, wired into the
  // `fetch(...)` call the caller made) is re-thrown as-is so existing
  // isAbortError callers keep working unchanged; anything else reading the
  // body is a dropped connection, not a "malformed stream" bug.
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        finalResponse = handleAgentStreamLine(line, handlers) ?? finalResponse;
      }
    }
  } catch (caught) {
    if (isAbortErrorLike(caught)) throw caught;
    throw new AgentStreamDisconnectedError(undefined, { cause: caught });
  }

  buffer += decoder.decode();
  finalResponse = handleAgentStreamLine(buffer, handlers) ?? finalResponse;
  if (finalResponse) return finalResponse;
  throw new AgentStreamDisconnectedError();
}

function readAgentEventLines(text: string, handlers: AgentStreamHandlers): AgentQueryResponse {
  let finalResponse: AgentQueryResponse | null = null;
  for (const line of text.split(/\r?\n/)) {
    finalResponse = handleAgentStreamLine(line, handlers) ?? finalResponse;
  }
  if (finalResponse) return finalResponse;
  throw new AgentStreamDisconnectedError();
}

function handleAgentStreamLine(line: string, handlers: AgentStreamHandlers): AgentQueryResponse | null {
  const trimmed = line.trim();
  if (!trimmed) return null;

  const event = JSON.parse(trimmed) as AgentStreamEvent;
  if (event.type === "status") {
    handlers.onStatus?.(event);
    return null;
  }
  if (event.type === "step") {
    handlers.onStep?.(event);
    return null;
  }
  if (event.type === "approval_request") {
    handlers.onApprovalRequest?.(event);
    return null;
  }
  if (event.type === "run_started") {
    handlers.onRunStarted?.(event);
    return null;
  }
  if (event.type === "chunk") {
    if (event.text) handlers.onChunk?.(event.text);
    return null;
  }
  if (event.type === "ping") {
    // Heartbeat only (see AgentStreamEvent's "ping" comment) -- no handler,
    // no finalResponse update. Kept as its own explicit branch (rather than
    // relying on the implicit fall-through every OTHER unknown/future event
    // type already gets below) so this no-op is test-visible and won't look
    // like an oversight to the next reader.
    return null;
  }
  if (event.type === "done" && event.response) {
    return event.response;
  }
  // Any other/unrecognized event type (forward-compat: a future backend
  // event this client doesn't know about yet) is ignored the same way.
  return null;
}

function sleepForRetry(ms: number): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms);
  });
}

export async function fetchNetwork(sessionId?: string): Promise<NetworkData> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const r = await apiFetch(`/api/network${query}`, { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

// Network component search (v2 UX unit, 2026-07-28): GET /api/network/search
// (backend/routers/search.py). `signal` lets NetworkSearchBox abort an
// in-flight request when the user types again -- an aborted fetch rejects
// with an AbortError, which the caller is expected to swallow (this function
// deliberately does not catch it, so cancellation stays indistinguishable
// from any other rejection at this layer -- the caller already knows which
// request it aborted and why).
export async function fetchNetworkSearch(
  query: string,
  options?: { limit?: number; signal?: AbortSignal },
): Promise<NetworkSearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (options?.limit) params.set("limit", String(options.limit));
  const r = await apiFetch(`/api/network/search?${params.toString()}`, {
    cache: "no-store",
    signal: options?.signal,
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function fetchActiveDataset(): Promise<ActiveDataset> {
  const r = await apiFetch("/api/datasets/active", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// --- Project workspaces (프로젝트) -----------------------------------------
// One workspace owns one uploaded model plus its validation workbook, staged
// spreadsheets, simulation runs and agent sessions. See getWorkspaceId above
// for how the selection reaches the backend.

export interface WorkspaceSummary {
  id: string;
  name: string;
  created_at: string | null;
  is_default: boolean;
}

export interface WorkspaceListResponse {
  workspaces: WorkspaceSummary[];
  current: string;
  default: string;
}

/** The stored X-Workspace-Id no longer resolves for THIS session: the project
 * was deleted, or the signed-in account is not a member. P④'s membership gate
 * deliberately never falls back to the default workspace server-side, so a
 * client that keeps sending the refused header 403s on every request on every
 * page — this error is the typed signal that lets the picker drop it. */
export class WorkspaceAccessError extends Error {
  constructor(public readonly code: string) {
    super(code);
    this.name = "WorkspaceAccessError";
  }
}

export async function fetchWorkspaces(): Promise<WorkspaceListResponse> {
  const r = await apiFetch("/api/workspaces", { cache: "no-store" });
  if (!r.ok) {
    if (r.status === 403 || r.status === 404) {
      const body = await r
        .clone()
        .json()
        .catch(() => null);
      const code = body?.detail?.code;
      if (code === "workspace_forbidden" || code === "workspace_not_found") {
        throw new WorkspaceAccessError(code);
      }
    }
    throw new Error(await readApiError(r));
  }
  return r.json();
}

export async function createWorkspace(name: string): Promise<WorkspaceSummary> {
  const r = await apiMutationFetch("/api/workspaces", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

// --- Auth (unit U7 backend, U9 UI) -----------------------------------------
// POST /api/auth/login, POST /api/auth/logout, GET /api/auth/whoami --
// backend/routers/auth.py. The session token lives in an HttpOnly cookie the
// browser manages; NOTHING here reads or stores it, and there is deliberately
// no token in JS to steal. `whoami()` is the only way this client learns who
// it is, and a 401 from it is the normal anonymous answer, not an error.

export type AuthRole = "modeler" | "reviewer" | "admin";

/** `services/auth._public_user` -- the only user shape the backend exposes
 * (no password hash, by construction). `role` is typed loosely on purpose: a
 * role added server-side must render as itself rather than crash a build. */
export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: AuthRole | string;
  created_at?: string;
  disabled?: boolean;
}

export interface AuthSessionInfo {
  user: AuthUser;
  session: { expires_at?: string };
}

/** Carries the HTTP status so callers can distinguish "wrong credentials"
 * (401 -- a uniform answer the backend gives for wrong password, unknown
 * username AND disabled account alike) from a server fault, without
 * string-matching a localized detail. A network failure never becomes one of
 * these: it stays a plain Error, which is what makes the login page able to
 * tell "the backend said no" apart from "the backend did not answer". */
export class AuthRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AuthRequestError";
    this.status = status;
  }
}

// Same fallback ladder as apiMutationFetch (same-origin proxy first, then the
// local API bases) but built on the workspace-free init above. Kept as its own
// function rather than adding a flag to apiMutationFetch so the no-header
// property is visible in the call graph instead of depending on an argument.
//
// Cookie caveat, stated rather than discovered later: a cookie minted on the
// same-origin hop belongs to the frontend origin, and one minted on the direct
// http://127.0.0.1:8001 hop belongs to that origin. The fallback only fires
// when the same-origin fetch throws (proxy down), so a session normally lives
// on exactly one of them; if the proxy comes back after a direct login, the
// next whoami() 401s and the UI simply shows the login link again.
async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  const normalizedPath = normalizedApiPath(path);
  let lastNetworkError: unknown = null;

  if (typeof window !== "undefined") {
    try {
      return await fetch(normalizedPath, noWorkspaceRequestInit(init));
    } catch (error) {
      if (!shouldRetryApiFetch(error)) throw error;
      lastNetworkError = error;
    }
  }

  for (const base of listMutationApiBases(normalizeApiBase(getApiBase()))) {
    try {
      return await fetch(apiUrlForBase(base, normalizedPath), noWorkspaceRequestInitForBase(base, init));
    } catch (error) {
      if (!shouldRetryApiFetch(error)) throw error;
      lastNetworkError = error;
    }
  }

  const detail = lastNetworkError instanceof Error ? lastNetworkError.message : String(lastNetworkError ?? "unknown error");
  throw new Error(`Failed to reach the API for ${normalizedPath}. Last network error: ${detail}`);
}

/** Exchange credentials for the session cookie. `language` only selects which
 * of ko/en/ja the backend's own uniform failure detail comes back in -- it can
 * never change WHETHER the login succeeds. */
export async function login(username: string, password: string, language?: string): Promise<AuthSessionInfo> {
  const r = await authFetch("/api/auth/login", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(language ? { username, password, language } : { username, password }),
  });
  if (!r.ok) throw new AuthRequestError(await readApiError(r), r.status);
  return r.json();
}

/** End the session. The backend always answers 200 (logging out must work for
 * an already-expired token), so a non-ok here is a real fault. */
export async function logout(): Promise<void> {
  const r = await authFetch("/api/auth/logout", { method: "POST", cache: "no-store" });
  if (!r.ok) throw new AuthRequestError(await readApiError(r), r.status);
}

/** Who this browser is, or `null` when nobody -- 401 is the ANSWER to that
 * question, not a failure, so it is not thrown. Anything else (5xx, an
 * unreachable API) still throws, so "we could not tell" never renders as
 * "signed out". */
export async function whoami(): Promise<AuthSessionInfo | null> {
  const r = await authFetch("/api/auth/whoami", { cache: "no-store" });
  if (r.status === 401) return null;
  if (!r.ok) throw new AuthRequestError(await readApiError(r), r.status);
  return r.json();
}

export async function uploadInpxDataset(file: File): Promise<ActiveDataset> {
  const filename = encodeURIComponent(file.name);
  const path = `/api/datasets/upload?filename=${filename}`;

  try {
    const r = await apiMutationFetch(path, {
      method: "POST",
      cache: "no-store",
      body: file,
    });

    if (!r.ok) {
      throw new Error(await r.text());
    }

    return r.json();
  } catch (error) {
    if (!shouldRetryApiFetch(error)) {
      throw error;
    }

    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(
      `Failed to upload file through the API proxy. Make sure the backend is running on http://127.0.0.1:8001 or http://localhost:8001, then retry. Last network error: ${detail}`,
    );
  }
}

export async function resetActiveDataset(): Promise<ActiveDataset> {
  const r = await apiMutationFetch("/api/datasets/reset", { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchHealthCheck(): Promise<HealthCheckReport> {
  const r = await apiFetch("/api/health-check", { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export function healthCheckDownloadUrl(): string {
  return apiUrl("/api/health-check?format=json");
}

// ---------------------------------------------------------------------------
// Unit #50 — /api/calibration, the calibration workspace's read side.
//
// Four GETs over services that already existed. Nothing here writes: the
// screen's approval button calls `approveAgentAction` below, the same endpoint
// the drawer has always used, so there is exactly one approval path in this
// app.
// ---------------------------------------------------------------------------

/** One of the six words the flow rail is allowed to say about a step.
 *
 * The list is `services/calibration_rail.STATUSES`, in its order, and it is a
 * WIRE type: nothing on the client may produce one of these, and a seventh word
 * here would be a status no backend rule can emit.
 *
 * `CONFIRMED_BY_PERSON` arrived with unit #53 and is deliberately NOT `DONE`.
 * `DONE` means a named ARTIFACT carries a positive verdict; this means a named
 * PERSON signed for the step. Nine of the thirteen steps have no artifact at
 * all, so before #53 they said "no evidence" forever — true, and unable to tell
 * "nobody did this" from "somebody did it and this tool cannot see it". The
 * server draws that distinction; this file only spells it. */
export type CalibrationStepStatus =
  | "DONE"
  | "CONFIRMED_BY_PERSON"
  | "IN_PROGRESS"
  | "NEXT"
  | "NOT_APPLICABLE"
  | "NO_EVIDENCE";

/** One named human's signature on a §2.12.1 step (#53), as the server keeps it.
 *
 * `attested_by` and `attested_at` come from the session and the server clock —
 * there is deliberately no way to send either (see
 * `postCalibrationStepAttestation`). `stale` is the SERVER's comparison of the
 * `.inpx` this claim was signed against with the one on disk now, never a
 * client-side date or revision check. */
export interface CalibrationStepAttestation {
  id: string;
  step_number: number;
  attested_by: string;
  attested_at: string;
  reason: string;
  inpx_sha256: string;
  /** `null` when the server could not read the active network at all — which
   * the server itself treats as stale, because unknown is not fresh. */
  current_inpx_sha256: string | null;
  /** The network moved (or could not be read) since this was signed: the step
   * falls back to its derived status and asks for a re-confirmation. */
  stale: boolean;
  revoked: boolean;
  revoked_by?: string | null;
  revoked_at?: string | null;
  revoked_reason?: string | null;
  /** Append-only: the signing event, and the revocation if there was one. */
  history: { event: string; at: string; by: string; reason: string }[];
}

/** One named human's mapping of a vehicle input to a node's demand (#54).
 *
 * `named_by` and `named_at` come from the session and the server clock — there
 * is deliberately no way to send either (see `postCalibrationDemandTarget`).
 * `stale` is the SERVER's comparison of the `.inpx` this claim was named
 * against with the one on disk now, never a client-side date check.
 *
 * WHAT THIS RECORD IS. Not a fact about the network: an ASSUMPTION with
 * somebody's name on it. The proposal it eventually produces still carries
 * `fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL`; naming makes that assumption
 * attributed, never proven. */
export interface CalibrationDemandTargetMapping {
  id: string;
  /** The record's append position in this workspace's registry. It is what "this
   * node's latest word" is decided by on the server — a withdrawal of the newest
   * record leaves NOTHING standing rather than resurrecting the one it replaced.
   * Nothing on the client compares it; it is here because a bug report quotes it. */
  seq: number;
  node_id: string;
  vehicle_input_no: string;
  link_no: string;
  named_by: string;
  named_at: string;
  reason: string;
  inpx_sha256: string;
  /** `null` when the server could not read the active network at all — which
   * the server itself treats as stale, because unknown is not fresh. */
  current_inpx_sha256: string | null;
  /** The network moved (or could not be read) since this was named: the
   * derivation ignores it and the node falls back to its honest refusal. */
  stale: boolean;
  revoked: boolean;
  revoked_by?: string | null;
  revoked_at?: string | null;
  revoked_reason?: string | null;
  history: { event: string; at: string; by: string; reason: string }[];
}

/** One vehicle input a modeller may name for a node — the SERVER's own list.
 *
 * `services/agent/reality_check_targets.py` computes it (the inputs whose link
 * is among the node's counted approach links) and the store checks a choice
 * against the same list, so the picker offers exactly what can be recorded. A
 * client-side candidate list would be a second opinion that shows a modeller
 * choices the server would then refuse. */
export interface CalibrationDemandCandidate {
  vehicle_input_no: string;
  link_no: string;
  /** `null` where the `.inpx` never stated one — printed as such, never as 0. */
  current_volume: number | null;
  name?: string | null;
}

/** Per node: why the derivation answered as it did, what could be named, and
 * what stands. Everything here is derived server-side. */
export interface CalibrationDemandTargetNode {
  approach_links: string[];
  /** `reality_check_targets.REASON_CODES` — including `HUMAN_NAMED_VEHICLE_INPUT`
   * once somebody has named one. */
  reason: string | null;
  chosen: string | null;
  candidates: CalibrationDemandCandidate[];
  /** Why a mapping this workspace holds was NOT used (stale, revoked, or no
   * longer a candidate). `null` when there is nothing to say. */
  human_mapping_ignored?: string | null;
  mapping: CalibrationDemandTargetMapping | null;
}

export interface CalibrationDemandTargets {
  available: boolean;
  reason: string | null;
  nodes: Record<string, CalibrationDemandTargetNode>;
  mappings: Record<string, CalibrationDemandTargetMapping>;
}

/** One driving behaviour a search could be run on: all three searched
 * parameters readable off it, so a baseline vector exists and a write could be
 * verified. The server lists them; the screen offers the CHOICE. Nothing picks
 * one of several on the modeller's behalf — see
 * `search_job.resolve_target_behaviour`. */
export interface ParameterSearchBehaviour {
  no: string;
  name: string;
  reference_count?: number | null;
  params: Record<string, number>;
}

/** One live/finished parameter-search job, exactly as the server describes it
 * (`services/agent/search_job.job_card`).
 *
 * Every word a reader sees comes from here. There is no client-side status
 * vocabulary for a search, no client-side elapsed arithmetic and no client-side
 * opinion about whether a finished search produced anything: `headline` is the
 * sentence, `status_text` is the chip, and which one they are was decided on the
 * job thread. */
export interface ParameterSearchJob {
  id: string;
  status: "QUEUED" | "RUNNING" | "COMPLETE" | "PARTIAL" | "FAILED";
  /** Still going? A server FIELD, not a client derivation from `status` — the
   * moment the client owns "QUEUED and RUNNING mean keep polling" it owns a
   * status vocabulary of its own, and a sixth status would read as finished. */
  live: boolean;
  status_text: { ko: string; en: string; ja: string };
  headline: { ko: string; en: string; ja: string };
  phase?: string | null;
  candidates_evaluated: number;
  candidates_failed: number;
  budget_s: number;
  elapsed_s: number;
  consumed_s: number;
  duration_s?: number | null;
  seed?: number | null;
  holdout_seed?: number | null;
  behavior_no?: string | null;
  behavior_name?: string | null;
  session_id?: string | null;
  /** Set only when a COMPLETE search was remembered by the broker — the
   * reference an approval card is later rebuilt from. */
  search_id?: string | null;
  remembered: boolean;
  terminal_reason?: string | null;
  acceptance_code?: string | null;
  proposal_outcome?: string | null;
  winner?: {
    params: Record<string, number>;
    objective?: number | null;
    pass_count?: number | null;
    graded?: number | null;
    baseline_objective?: number | null;
  } | null;
  started_at?: string | null;
  finished_at?: string | null;
  /** A NAMED non-feature: a partial search's state is written for audit and is
   * never read back, because a grant may only be minted from a search this
   * process ran and remembered. */
  resume_from_disk?: { supported: boolean; why: string; artifact: string };
}

/** Unit #51's step-⑫ second track: the range-bounded parameter search.
 *
 * `locked` is the normal state and carries the precondition gate's own NAMED
 * refusals — each says what is wrong and what would open it. Unlocked, `card`
 * carries the ask-first trigger: a question with its evidence attached, never a
 * search already running. The client renders both; it decides neither. */
export interface CalibrationRailTrack {
  key: string;
  unit: string;
  locked: boolean;
  label: { ko: string; en: string; ja: string };
  why: { ko: string; en: string; ja: string };
  /** Why the gate refused, one entry per unmet precondition. Empty when open. */
  refusals: {
    code: string;
    why: { ko: string; en: string; ja: string };
    opens_when: { ko: string; en: string; ja: string };
    /** #57: the gate's own navigation targets for this reason (blocking node
     * ids, the Table 5-6 off-row nodes, the signal-timing node list, the
     * graded-node count) — `services/calibration_rail.py` forwards
     * `parameter_search._refusal()`'s `detail` verbatim. The lock card's
     * per-reason action chips read this to decide WHERE to send a reader;
     * they never compute a target of their own. Optional because a refusal
     * code this file does not build a chip for is still rendered as prose
     * only, and an older cached payload may predate this field. */
    detail?: Record<string, unknown>;
  }[];
  /** Present only when `locked` is false. */
  card: {
    key: string;
    unit: string;
    ask_first: boolean;
    auto_start: boolean;
    question: { ko: string; en: string; ja: string };
    because: { ko: string; en: string; ja: string };
    plan: {
      budget_s: number;
      resumable: boolean;
      objective: string;
      space: { parameter: string; low: number; high: number; citation: string }[];
      calibration_nodes: string[];
      holdout_nodes: string[];
      algorithm: string;
      algorithm_citation: string;
    };
    caveats: string[];
    /** The runner seam's honest state, MEASURED on the backend's own machine
     * since 2026-08-21: `wired` is Windows-plus-pywin32, re-taken on every read.
     * True on the consultant's box (the start control appears), false on a Mac
     * (it does not, and `why_not_here` says why). Never a claim that a search
     * will succeed — the gate, the job's refusals and the holdout all still
     * stand between this flag and a proposal. */
    runner: {
      interface: string;
      implementation?: string | null;
      wired: boolean;
      requires: string[];
      why_not_here: string | null;
    };
    /** Mirrors the track's own `search_job` — same object, so a reader of
     * either sees the same state. */
    search_job?: ParameterSearchJob | null;
  } | null;
  /** The newest search THIS workspace started, or null. On the TRACK rather
   * than only on the card because a search that SUCCEEDS closes the gate behind
   * it (every node passes, so the next rail read refuses with
   * `GEH_ALREADY_PASSES` and `card` is null) — a job block that lived only on
   * the card would erase the completion at the moment it arrived. */
  search_job?: ParameterSearchJob | null;
  /** Which behaviours a search could target, and the one to preselect when
   * there is exactly one. `null` means "more than one, or none — ask". */
  behaviours?: ParameterSearchBehaviour[];
  default_behavior_no?: string | null;
  /** Unit #51-c: what becomes of a searched value on each of the two paths, and
   * the user's recorded answer on the collision (DECISIONS.md 2026-08-20).
   *
   * On the TRACK, not the card, because it is true of this door whether or not
   * it has opened — a locked track is what this screen shows most of the time.
   *
   * `conditional: true` and it means it: no behaviour has been chosen here, so
   * the approved path carries `expected_to_refuse: null` — a condition
   * (`RADIUS_NAMED_IN_FULL`), not a verdict. The per-behaviour verdict is
   * `AgentSearchedSearch.preflight_forecast`, on the card that approves a
   * winner. Rendering either of them as one "will refuse" line is what #51-b's
   * two-path split made misleading. */
  guardrail?: {
    conditional?: boolean;
    decision?: string;
    paths?: Record<
      string,
      {
        expected_to_refuse?: boolean | null;
        constraint_id?: string;
        condition?: string;
        refuses_if_radius_drifts?: string;
        why?: { ko: string; en: string; ja: string };
      }
    >;
    resolutions?: {
      key: string;
      status?: string;
      decided?: boolean;
      decision?: string;
      landed_in?: string;
      ko: string;
      en: string;
      ja: string;
    }[];
  };
}

export interface CalibrationRailStep {
  number: number;
  /** MRWA op §2.12.1's own English label, verbatim. */
  label: string;
  /** This product's translations of it — `label_source` says so on the wire. */
  label_ko: string;
  label_ja: string;
  label_source: string;
  citation: string;
  /** The section id on the workspace page this step's work lives in. */
  anchor: string;
  /** True for the seven steps this product measures nothing for. */
  manual_area: boolean;
  status: CalibrationStepStatus;
  /** Which artifact answered — "none" only ever accompanies NO_EVIDENCE. */
  source: string;
  basis: { ko: string; en: string; ja: string };
  detail: Record<string, unknown>;
  /** Unit #51's second track. Present on step 12 only. */
  tracks?: CalibrationRailTrack[];
  /** Whether a human may SIGN for this step (#53) — the server's answer, from
   * `calibration_rail.ATTESTABLE_STEPS`. A client-side copy of that list would
   * be a second opinion about which steps carry machine evidence. */
  attestable?: boolean;
  /** The signature that STANDS, or null. A stale one is here too, so the screen
   * can show the record it is asking about; a revoked one never is. */
  attestation?: CalibrationStepAttestation | null;
}

export interface CalibrationRail {
  steps: CalibrationRailStep[];
  counts: Record<CalibrationStepStatus, number>;
  citation: string;
  principles: { text: string; citation: string }[];
  statuses: CalibrationStepStatus[];
  /** Which evidence bundles the server actually found — lets the UI tell "the
   * rail read this and found nothing" from "the rail was given nothing". */
  sources: {
    gate: boolean;
    health: boolean;
    phase_diagrams: boolean;
    /** Whether the SIGNATURE STORE could be read — distinct from it holding
     * nothing, which is the normal state and reads as no evidence. */
    step_attestations: boolean;
    shadow_model: boolean;
    reality_check: boolean;
    scenario_id: string | null;
  };
}

/** One node of the run-vs-observed report. The `diagnosis` block is unit #43's
 * ladder, the SAME shape the drawer renders from the stream. */
export interface CalibrationRealityNode {
  node_id: string;
  node_name?: string | null;
  observed_total?: number | null;
  modelled_total?: number | null;
  node_total_geh?: number | null;
  outcome: {
    outcome: string;
    scope: string;
    reason_code?: string | null;
    confidence?: string | null;
    verb?: string | null;
    params?: Record<string, unknown> | null;
    evidence?: Record<string, unknown>;
    rationale?: Record<string, unknown>;
  };
  diagnosis?: {
    rung: string;
    why: { ko: string; en: string; ja: string };
    next_action: AgentDiagnosisNode["next_action"];
    missing_evidence: string[];
    evidence?: Record<string, unknown>;
  } | null;
}

export interface CalibrationRealityCheck {
  status: string;
  reason_code?: string | null;
  message: { ko: string; en: string; ja: string };
  geh_threshold?: number | null;
  period?: string | null;
  checked_at?: string;
  sources?: Record<string, string>;
  coverage?: {
    counts?: Record<string, number>;
    ungradable_movements?: unknown[];
    run_nodes_without_observed?: unknown[];
    observed_nodes_absent_from_run?: unknown[];
  };
  nodes?: CalibrationRealityNode[];
  proposals?: CalibrationRealityNode[];
  refusals?: CalibrationRealityNode[];
  diagnosis?: { by_rung?: Record<string, string[]> };
}

export interface CalibrationAppliedRow {
  behavior_no: string;
  behavior_name: string;
  reference_count?: number | null;
  car_following_model?: number | null;
  /** `saturation_flow.verify_applied_row`'s verdict, verbatim. */
  verdict: {
    status: "MATCHES_PUBLISHED_ROW" | "MATCHES_NO_PUBLISHED_ROW" | "PARAMETERS_UNREADABLE";
    applied: Record<string, number | null>;
    row: { saturation_flow_pcu: number; additive: number; multiplicative: number } | null;
    per_parameter_rows: Record<string, number[]>;
    standstill_matches_guideline: boolean | null;
    why: string;
    citation: string;
  };
}

export interface CalibrationAppliedRows {
  available: boolean;
  reason: string | null;
  behaviors: CalibrationAppliedRow[];
  summary: {
    readable: number;
    on_published_row: number;
    off_published_row: number;
    guideline_standstill: number;
    default_coincident: number;
    unreadable: number;
    default_coincident_row_pcu: number | null;
  };
  citation: string;
}

/** A pause waiting for a human, read WITHOUT spending the single-use token. */
export interface CalibrationPendingApproval {
  session_id: string;
  approval_id: string;
  created_at?: number | null;
  message?: string | null;
  language?: string | null;
  actions: AgentAction[];
  warnings: AgentWarning[];
  assumptions: string[];
  focus?: AgentApprovalFocus | null;
  car_following_set?: AgentCarFollowingSet | null;
  /** Unit #51-c. Null for every pause that is not a searched write, and null
   * for one whose search did not survive a backend restart -- the panel is
   * rebuilt from the in-process broker, never invented from the pending file. */
  searched_search?: AgentSearchedSearch | null;
}

export async function fetchCalibrationRail(params?: {
  scenarioId?: string | null;
  period?: string | null;
  observationSetId?: string | null;
  observationPeriod?: string | null;
}): Promise<CalibrationRail> {
  const query = new URLSearchParams();
  if (params?.scenarioId) query.set("scenario_id", params.scenarioId);
  if (params?.period) query.set("period", params.period);
  if (params?.observationSetId) query.set("observation_set_id", params.observationSetId);
  if (params?.observationPeriod) query.set("observation_period", params.observationPeriod);
  const suffix = query.toString();
  const r = await apiFetch(`/api/calibration/rail${suffix ? `?${suffix}` : ""}`, { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function fetchCalibrationRealityCheck(params: {
  scenarioId: string;
  period?: string | null;
  observationSetId?: string | null;
  observationPeriod?: string | null;
}): Promise<CalibrationRealityCheck> {
  const query = new URLSearchParams({ scenario_id: params.scenarioId });
  if (params.period) query.set("period", params.period);
  if (params.observationSetId) query.set("observation_set_id", params.observationSetId);
  if (params.observationPeriod) query.set("observation_period", params.observationPeriod);
  const r = await apiFetch(`/api/calibration/reality-check?${query.toString()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function fetchCalibrationAppliedRows(): Promise<CalibrationAppliedRows> {
  const r = await apiFetch("/api/calibration/applied-rows", { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

/** What `POST /api/calibration/parameter-search/start` answers.
 *
 * It answers 200 either way. `started` says which happened and `refusals`
 * carries the named codes when it did not — those refusals ARE the product, and
 * a UI should not have to translate a 4xx back into a sentence. Both families
 * arrive in one list: the job's own start refusals (no session, no runner, a
 * behaviour nobody picked) and the precondition gate's
 * (`LADDER_NOT_EXHAUSTED`, `TABLE_5_6_NOT_APPLIED`, …). */
export interface ParameterSearchStartResult {
  started: boolean;
  refusals: {
    code: string;
    why: { ko: string; en: string; ja: string };
    opens_when?: { ko: string; en: string; ja: string };
    detail?: unknown;
  }[];
  behaviours: ParameterSearchBehaviour[];
  budget: { budget_s: number; clamped: boolean; min_s: number; max_s: number; default_s: number };
  duration: { duration_s: number; clamped: boolean; min_s: number; max_s: number; default_s: number };
  job: ParameterSearchJob | null;
}

/** Start one range-bounded parameter search (#51's live runner).
 *
 * SELECTORS ONLY. There is deliberately no way to send a gate verdict: the
 * backend re-derives the whole precondition payload from evidence it read
 * itself, and its request model forbids extra fields, so an invented
 * `preconditions` is a 422 at the door rather than a field that quietly does
 * nothing.
 *
 * `sessionId` is the agent conversation the finished search is remembered
 * against — the browser learns it from the `vissim:agent-session` broadcast the
 * drawer already publishes. Omitting it is not a client-side error to guard
 * against: the server refuses by name (`NO_AGENT_SESSION`) with the sentence
 * that says what to do, and this client renders it like any other refusal. */
export async function startParameterSearch(params: {
  scenarioId: string;
  sessionId?: string | null;
  behaviorNo?: string | null;
  budgetS?: number | null;
  durationS?: number | null;
  period?: string | null;
  observationSetId?: string | null;
  observationPeriod?: string | null;
}): Promise<ParameterSearchStartResult> {
  const body: Record<string, unknown> = { scenario_id: params.scenarioId };
  if (params.sessionId) body.session_id = params.sessionId;
  if (params.behaviorNo) body.behavior_no = params.behaviorNo;
  if (typeof params.budgetS === "number") body.budget_s = params.budgetS;
  if (typeof params.durationS === "number") body.duration_s = params.durationS;
  if (params.period) body.period = params.period;
  if (params.observationSetId) body.observation_set_id = params.observationSetId;
  if (params.observationPeriod) body.observation_period = params.observationPeriod;
  const r = await apiMutationFetch("/api/calibration/parameter-search/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

/** Poll one running search. The job record is the ONLY source of its status. */
export async function fetchParameterSearchJob(jobId: string): Promise<ParameterSearchJob> {
  const r = await apiFetch(`/api/calibration/parameter-search/jobs/${encodeURIComponent(jobId)}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

/** Sign for ONE calibration step (#53).
 *
 * WHAT IS DELIBERATELY NOT ON THIS BODY: `attested_by` and `attested_at`. The
 * server takes the attester from the session and the timestamp from its own
 * clock, and its request model forbids extra fields — so sending either is a
 * 422 at the door rather than somebody else's name against a step. Same
 * discipline as `confirmPhaseDiagram`, which is the act this is modelled on.
 *
 * The response carries the RECORD and no status: what the step now says is the
 * rail's answer, re-derived on the next `fetchCalibrationRail`. */
export async function postCalibrationStepAttestation(params: {
  stepNumber: number;
  reason: string;
}): Promise<CalibrationStepAttestation> {
  const r = await apiMutationFetch("/api/calibration/step-attestations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step_number: params.stepNumber, reason: params.reason }),
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

/** Withdraw one signature, with a reason of its own (#53).
 *
 * Nothing is deleted: the revoker's name, time and reason are appended beside
 * the original signing event, so "who signed this, and who later said it was
 * wrong" survives. The step then falls back to whatever its evidence says. */
export async function revokeCalibrationStepAttestation(
  attestationId: string,
  params: { reason: string },
): Promise<CalibrationStepAttestation> {
  const r = await apiMutationFetch(
    `/api/calibration/step-attestations/${encodeURIComponent(attestationId)}/revoke`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: params.reason }),
    },
  );
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

/** Which vehicle inputs may be named for each node, and what already is (#54).
 *
 * A READ of the derivation `GET /reality-check` already runs, exposed on its
 * own so the picker can render the candidates a refusal was talking about. The
 * report says "several inputs are candidates and this product will not pick
 * between them"; this says WHICH several. */
export async function fetchCalibrationDemandTargets(params?: {
  period?: string | null;
}): Promise<CalibrationDemandTargets> {
  const query = new URLSearchParams();
  if (params?.period) query.set("period", params.period);
  const suffix = query.toString();
  const r = await apiFetch(
    `/api/calibration/demand-targets/candidates${suffix ? `?${suffix}` : ""}`,
    { cache: "no-store" },
  );
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

/** Name ONE vehicle input as the driver of ONE node's demand (#54).
 *
 * WHAT IS DELIBERATELY NOT ON THIS BODY: `named_by` and `named_at`. The server
 * takes the namer from the session and the timestamp from its own clock, and
 * its request model forbids extra fields — so sending either is a 422 at the
 * door rather than an assumption filed under somebody else's name.
 *
 * The LINK is sent even though the server could look it up from the input
 * number: the modeller is asserting a triple and the server checks all three of
 * it against the candidate list. The response carries the RECORD and no status:
 * what the node now says is re-derived on the next `fetchCalibrationRealityCheck`. */
export async function postCalibrationDemandTarget(params: {
  nodeId: string;
  vehicleInputNo: string;
  linkNo: string;
  reason: string;
  period?: string | null;
}): Promise<CalibrationDemandTargetMapping> {
  const body: Record<string, unknown> = {
    node_id: params.nodeId,
    vehicle_input_no: params.vehicleInputNo,
    link_no: params.linkNo,
    reason: params.reason,
  };
  if (params.period) body.period = params.period;
  const r = await apiMutationFetch("/api/calibration/demand-targets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

/** Withdraw one mapping, with a reason of its own (#54).
 *
 * Nothing is deleted: the revoker's name, time and reason are appended beside
 * the original naming event. The node then falls back to the honest refusal it
 * had before anybody named anything. */
export async function revokeCalibrationDemandTarget(
  mappingId: string,
  params: { reason: string },
): Promise<CalibrationDemandTargetMapping> {
  const r = await apiMutationFetch(
    `/api/calibration/demand-targets/${encodeURIComponent(mappingId)}/revoke`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: params.reason }),
    },
  );
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function fetchCalibrationPendingApprovals(): Promise<{
  pending: CalibrationPendingApproval[];
  workspace_id: string;
}> {
  const r = await apiFetch("/api/calibration/pending-approvals", { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function startSimulation(params: {
  name: string;
  scenario_id?: string;
  sim_duration: number;
  random_seed: number;
  vehicle_inputs: Record<number, number>;
  signal_cycles: Record<number, number>;
  signal_offsets?: Record<number, number>;
  incidents?: SimulationIncident[];
}): Promise<{ id: string; status: string }> {
  const r = await apiMutationFetch("/api/simulations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchSimulationJobs(): Promise<SimulationJob[]> {
  const r = await apiFetch("/api/simulations", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchSimulationJob(jobId: string): Promise<SimulationJob> {
  const r = await apiFetch(`/api/simulations/${jobId}`, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchSimulationCapabilities(): Promise<SimulationCapabilities> {
  const r = await apiFetch("/api/simulations/capabilities", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function fetchSimulationCapabilitiesForBase(
  base: string,
  timeoutMs = 1400,
): Promise<SimulationCapabilities> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(apiUrlForBase(base, "/api/simulations/capabilities"), requestInitForBase(base, {
      cache: "no-store",
      signal: controller.signal,
    }));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  } finally {
    window.clearTimeout(timeout);
  }
}

export async function detectRunnableSimulationBackend(
  currentBase = getApiBase(),
): Promise<{ base: string; capabilities: SimulationCapabilities } | null> {
  for (const base of listAutoDetectApiBases(currentBase)) {
    try {
      const capabilities = await fetchSimulationCapabilitiesForBase(base);
      if (capabilities.can_run || capabilities.can_prepare_simulation) {
        return { base, capabilities };
      }
    } catch {
      // Local probing is opportunistic; unreachable candidates are expected.
    }
  }
  return null;
}

// NOTE (unit 1.4): the browser WebSocket API cannot attach custom headers, so
// this progress socket never carries X-Workspace-Id and the backend resolves
// it to the default workspace. Live progress polling that DOES need workspace
// scoping goes through fetchSimulationJob (a normal request) instead.
export function createSimulationWS(jobId: string): WebSocket {
  const wsBase = toWebSocketBase(getApiBase()).replace(/\/+$/, "");
  return new WebSocket(`${wsBase}/api/simulations/ws/${jobId}`);
}

export async function fetchSimulationResults(jobId: string): Promise<SimulationResultsResponse> {
  const r = await apiFetch(`/api/simulations/${jobId}/results`);
  if (!r.ok) {
    throw new Error(await r.text());
  }
  return r.json();
}

export interface SignalPhase {
  start: number;    // seconds from cycle start
  duration: number; // effective green duration in seconds
  signal_groups?: string[];
  signal_group_ids?: string[];
  movements?: {
    from_direction: string;
    to_direction: string;
    turn: string;
  }[];
}

export interface SignalNodeInfo {
  node_id: string;
  node_name: string;
  sc_no?: string | null;
  sc_name?: string | null;
  has_signal: boolean;
  cycle_time: number | null;
  num_signal_groups: number | null;
  num_phases: number | null;
  phases: SignalPhase[];
}

export async function fetchSignals(): Promise<SignalNodeInfo[]> {
  const r = await apiFetch("/api/geojson/signals", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface RealCountDirection {
  direction: string;
  real: number;
  sim_basam: number;
}

export interface NodeRealCounts {
  node_id: string;
  node_name: string;
  total_real: number | null;
  total_sim_basam: number | null;
  directions: RealCountDirection[];
}

export async function fetchRealCounts(): Promise<NodeRealCounts[]> {
  const r = await apiFetch("/api/validation/real-counts");
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface BaseWorkbookCountRow {
  period: "AM" | "PM" | string;
  node_id: string;
  node_name: string;
  movement: string;
  observed_volume: number | null;
  model_volume: number | null;
}

export interface BaseWorkbookTravelTimeRow {
  period: "AM" | "PM" | string;
  route_id: string;
  route_name: string;
  sample_order: number | null;
  time_label: string;
  observed_travel_time_sec: number | null;
  model_travel_time_sec: number | null;
  // Route distance (DisTrav(All), metres) for the chart's distance axis.
  dist_m?: number | null;
  // Optional dedicated validation-chart columns (seconds). When present they
  // override the chart's observed / model / band values.
  tomtom_avg_sec?: number | null;
  model_output_sec?: number | null;
  tomtom_low_sec?: number | null;
  tomtom_high_sec?: number | null;
}

export interface BaseWorkbookResponse {
  exists: boolean;
  filename: string;
  path?: string;
  sheets?: string[];
  counts: BaseWorkbookCountRow[];
  travel_times: BaseWorkbookTravelTimeRow[];
  warnings: string[];
  // Time periods actually present in the uploaded Counts sheet (AM before PM).
  periods?: string[];
  // Additive: present (non-null) only on the response to a POST /base-workbook
  // whose upload did NOT conform to the standard template (backend/routers/
  // validation.py's upload_base_workbook -- stage-then-promote). When set,
  // the REST of this response is the STILL-ACTIVE workbook, unchanged; this
  // block carries the staged upload's own inspect payload (everything
  // WorkbookMappingModal needs) plus the template-parse warnings that caused
  // the rejection. A plain GET /base-workbook, or a promoted (conforming)
  // upload, always has this null/absent.
  pending_mapping?: (WorkbookInspectResponse & { warnings: string[] }) | null;
}

export async function fetchBaseWorkbook(): Promise<BaseWorkbookResponse> {
  const r = await apiFetch("/api/validation/base-workbook", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export type ReportBlock = { type: string; heading?: string; body?: string };
export type ReportMeta = { title?: string; subtitle?: string; author?: string; date?: string; project?: string; revision?: string; reviewer?: string; description?: string };
// Mirrors backend/routers/report.py's ReportStyle -- the company style
// profile (R4.2). Every field optional; see lib/reportStyleProfile.ts's
// profileToApiStyle() for the frontend-profile -> this-shape seam.
export type ReportStyle = {
  palette?: string[];
  font_family?: string;
  font_size_title?: number;
  font_size_label?: number;
  figure_width_in?: number;
  figure_height_in?: number;
};
export type ReportSpec = { meta: ReportMeta; blocks: ReportBlock[]; filename?: string; style?: ReportStyle };

export async function buildReport(spec: ReportSpec): Promise<Blob> {
  const r = await apiMutationFetch("/api/report/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(await r.text());
  return r.blob();
}

/** CSV of exactly the rows a report block renders (R4.1's "데이터 내보내기").
 * `blockType` matches ReportBlock["type"] (e.g. "geh-validation"). */
export async function fetchReportBlockDataCsv(blockType: string): Promise<Blob> {
  const r = await apiFetch(`/api/report/data/${encodeURIComponent(blockType)}`, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.blob();
}

// ---------------------------------------------------------------------------
// Track R — the report ENGINE (backend/routers/report.py: /spine, /figures,
// /graphics, /style-profiles, /compose, /compose/artifacts).
//
// A DIFFERENT surface from the legacy `/build` + `/data/{block}` pair above,
// which stays supported and stays wired: that one assembles the old workbook
// report from a caller-chosen block list. This half reads the MRWA section
// spine (R-1), R-2's table figures and R-3's graphic figures, R-4's stored
// style profiles, and posts R-5a's typesetter. Design:
// docs/product/report-engine-design-2026-08-24.md, "UI (/report 재편)".
// ---------------------------------------------------------------------------

/** A sentence the report backend wrote, in the three languages it ships. */
export interface ReportText {
  ko: string;
  en: string;
  ja: string;
}

/** `services.report.spine`'s availability verdict for one Table 5-15 output. */
export type ReportOutputAvailability = "AVAILABLE" | "NOT_AVAILABLE";

export interface ReportOutputExtractor {
  module: string;
  symbol: string;
  evidence: string[];
}

export interface ReportRequiredOutput {
  name: string;
  availability: ReportOutputAvailability;
  extractor: ReportOutputExtractor | null;
  /** The MEASUREMENT behind the verdict, in the backend's own words. Rendered
   * verbatim beside a NOT_AVAILABLE output; never paraphrased on this side. */
  reason: string | null;
}

export interface ReportRequiredOutputGroup {
  type: string;
  outputs: ReportRequiredOutput[];
  evaluation_method: string | null;
  time_period: string | null;
}

export interface ReportSpineSourceKey {
  key: string;
  owner: string;
  what: string;
}

export interface ReportSpineSection {
  id: string;
  title: ReportText;
  /** The guideline clause + page this section exists because of. */
  basis: string[];
  source_keys: ReportSpineSourceKey[];
  required_outputs: ReportRequiredOutputGroup[];
  regulation_derived: boolean;
}

export interface ReportSpinePayload {
  minimum: { sentence: string; citation: string };
  extract: string;
  source_keys: ReportSpineSourceKey[];
  table_5_15: ReportRequiredOutputGroup[];
  unavailable_outputs: { type: string; output: string }[];
  sections: ReportSpineSection[];
}

/** Every figure in R-2/R-3 carries these, present or absent alike. */
interface ReportFigureBase {
  figure_id: string;
  /** `null` when the figure binds to no regulation-derived section — the
   * typesetter files those under its own `unbound_figures` section. */
  spine_section_id: string | null;
  absent: boolean;
  /** Present exactly when `absent` — the named reason, in three languages. */
  reason?: ReportText;
}

export interface ReportFigureColumn {
  key: string;
  ko: string;
  en: string;
  ja: string;
}

/** The provenance stamp every non-absent figure carries (the design doc's
 * "이 그림은 어느 실행의 것인가"). Keys vary by generator, so it is read as a
 * bag of strings rather than a fixed record. */
export type ReportFigureSourceCaption = Record<string, unknown>;

/** R-2's table figures — `GET /api/report/figures`. */
export interface ReportTableFigure extends ReportFigureBase {
  title?: ReportText;
  columns?: ReportFigureColumn[];
  rows?: Record<string, unknown>[];
  source_caption?: ReportFigureSourceCaption;
  citation?: string | null;
}

/** R-3's graphic figures — `GET /api/report/graphics`. `svg` is a complete
 * `<svg>` document the SERVER rendered. */
export interface ReportGraphicFigure extends ReportFigureBase {
  title?: ReportText;
  svg?: string;
  legend?: { key: string; ko: string; en: string; ja: string; fill: string; stroke: string }[];
  meta?: Record<string, unknown>;
  source_caption?: ReportFigureSourceCaption;
  citation?: string | null;
}

/** R-4's stored style profiles — `GET /api/report/style-profiles`' index rows
 * (`services.report.style_profile.list_profiles`). */
export interface ReportStyleProfileSummary {
  id: string;
  name: string;
  source_file: string;
  source_sha256: string;
  created_at: string;
  revision_count: number;
  has_logo: boolean;
}

/** The figure inputs `GET /figures`, `GET /graphics` and `POST /compose` all
 * take, spelled the same on all three so the preview and the document are
 * never built from two different sets of parameters. */
export interface ReportFigureParams {
  scenario_id?: string | null;
  period?: string | null;
  observation_set_id?: string | null;
  observation_period?: string | null;
  session_id?: string | null;
  before_scenario_id?: string | null;
  after_scenario_id?: string | null;
}

/** `POST /api/report/compose`'s body — `routers.report.ComposeRequest`, field
 * for field. The backend sets `extra="forbid"`, so a misspelled key is a 422
 * at the door rather than a silently ignored selection. */
export interface ComposeReportRequest extends ReportFigureParams {
  /** `{sections: [...]}` to include exactly those; omit for every section. */
  selection?: { sections: string[] } | null;
  style_profile_id?: string | null;
  language?: string;
  title?: string | null;
  filename?: string | null;
}

/** `services.report.typeset.TypesetRefused` / `StyleProfileRefused` as it
 * arrives on the wire: FastAPI nests the envelope under `detail`. */
export interface ReportRefusal {
  ok: false;
  reason_code: string;
  message: ReportText;
}

/** A refusal that carries the server's own sentence, so a caller can render
 * that instead of a generic failure line. */
export class ReportRefusedError extends Error {
  readonly refusal: ReportRefusal;

  constructor(refusal: ReportRefusal) {
    super(refusal.reason_code);
    this.name = "ReportRefusedError";
    this.refusal = refusal;
  }
}

function reportFigureQuery(params: ReportFigureParams): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "string" && value) query.set(key, value);
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

async function reportError(response: Response): Promise<Error> {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    const detail = parsed?.detail;
    if (detail && typeof detail === "object" && "reason_code" in detail && "message" in detail) {
      return new ReportRefusedError(detail as unknown as ReportRefusal);
    }
    if (typeof detail === "string" && detail.trim()) return new Error(detail);
  } catch {
    // Not JSON -- fall through to the raw body, which is still useful.
  }
  return new Error(text || `${response.status} ${response.statusText}`.trim());
}

/** The MRWA section skeleton (R-1). Parameterless on purpose: it is a
 * transcription of the guideline plus this build's availability audit, so no
 * run or dataset enters into it. */
export async function fetchReportSpine(): Promise<ReportSpinePayload> {
  const r = await apiFetch("/api/report/spine", { cache: "no-store" });
  if (!r.ok) throw await reportError(r);
  return r.json();
}

/** R-2's four table figures, gathered in one call. A figure with no input is
 * returned ABSENT with its reason, never a 4xx for the whole set. */
export async function fetchReportFigures(params: ReportFigureParams = {}): Promise<ReportTableFigure[]> {
  const r = await apiFetch(`/api/report/figures${reportFigureQuery(params)}`, { cache: "no-store" });
  if (!r.ok) throw await reportError(r);
  const payload = (await r.json()) as { figures: ReportTableFigure[] };
  return payload.figures;
}

/** R-3's three graphic figures. Same "degrade one, not all" contract. */
export async function fetchReportGraphics(params: ReportFigureParams = {}): Promise<ReportGraphicFigure[]> {
  const r = await apiFetch(`/api/report/graphics${reportFigureQuery(params)}`, { cache: "no-store" });
  if (!r.ok) throw await reportError(r);
  const payload = (await r.json()) as { graphics: ReportGraphicFigure[] };
  return payload.graphics;
}

/** Every style profile stored in this workspace, newest first (R-4). */
export async function fetchReportStyleProfiles(): Promise<ReportStyleProfileSummary[]> {
  const r = await apiFetch("/api/report/style-profiles", { cache: "no-store" });
  if (!r.ok) throw await reportError(r);
  const payload = (await r.json()) as { profiles: ReportStyleProfileSummary[] };
  return payload.profiles;
}

/** One catalogue leaf `services.report.style_profile.extract_style_profile`
 * could not pin down, named with the server's own reason -- the C1-b grammar
 * ("EXTRACTION IS A PROPOSAL, NOT A MEASUREMENT") this module's docstring
 * states: no leaf is silently `None`, every gap gets an entry here instead. */
export interface ReportStyleProfileNotExtracted {
  field: string;
  reason: ReportText;
}

/** `GET /style-profiles/{id}` / `POST /style-profiles`'s FULL record --
 * `services.report.style_profile.get_profile`'s shape. `original_profile` is
 * the extracted catalogue itself (every field optional -- a real DOCX has
 * holes); `not_extracted` is that catalogue's own first-class gap list,
 * which is why it is read from here rather than invented on this side. */
export interface ReportStyleProfileRecord {
  id: string;
  name: string;
  source_file: string;
  source_sha256: string;
  created_at: string;
  original_profile: {
    not_extracted: ReportStyleProfileNotExtracted[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

/** R-4's extractor: one styled DOCX -> one stored style-profile PROPOSAL
 * (`POST /api/report/style-profiles`). Raw body + `?filename=`, the exact
 * upload SHAPE `uploadPhaseDiagramTemplate` (below) uses and for the
 * identical reason -- this backend carries no `python-multipart`, so
 * `multipart/form-data` was never an option for either route.
 *
 * AUTHENTICATED (`Depends(current_user)`; see routers/report.py's module
 * docstring, "Auth decision for POST /style-profiles"): the upload writes a
 * per-workspace registry entry -- id, name, source file, created_at -- in
 * this same request, so it takes the phase-diagrams-upload SHAPE (a durable,
 * provenance-bearing record) rather than the token-staging shape
 * `uploadObservationWorkbook` uses. `reportError` turns an unauthenticated
 * 401 into a plain `Error` (the backend's `current_user` dependency answers
 * with a bare localized string, not the `{reason_code, message}` refusal
 * envelope) -- the report screen does not rely on that message, though: it
 * asks `useAuth()` before ever showing the control, so this request is
 * reachable only once a session already exists.
 *
 * Returns the FULL stored record (`GET .../{id}`'s shape), `not_extracted`
 * included, so the caller renders the extraction's own named absences from
 * this one response rather than a second round trip. */
export async function uploadReportStyleProfile(file: File, name?: string): Promise<ReportStyleProfileRecord> {
  const params = new URLSearchParams({ filename: file.name });
  if (name) params.set("name", name);
  const r = await apiMutationFetch(`/api/report/style-profiles?${params.toString()}`, {
    method: "POST",
    cache: "no-store",
    body: file,
  });
  if (!r.ok) throw await reportError(r);
  return r.json();
}

/** `GET /api/agent/audit/sessions`'s per-session row (Track R unit R-5c) --
 * only sessions whose action ledger actually holds an applied/rolled-back/
 * failed parameter change, i.e. the exact rows
 * `services.report.figures.approval_history_table` would render for that
 * `session_id`. Never `AgentDownloadEntry`'s "saved a write" set -- see
 * `fetchReportAuditSessions`'s own doc comment for why that would be an
 * invented mapping. */
export interface ReportAuditSessionSummary {
  session_id: string;
  approval_count: number;
  last_approval_at: string | null;
}

/** `GET /api/agent/audit/sessions`'s full response: the capped, newest-first
 * page AND `total` -- the count BEFORE the cap. Refuter-measured 2026-08-25:
 * the backend caps at 20 (le=200 via `?limit=`) because an unbounded walk
 * cost 2.92s over 500 synthetic sessions, and it discloses `total`
 * specifically so a truncated page is never mistaken for the whole list --
 * the sibling `fetchAgentDownloads` listing truncates SILENTLY (no total),
 * which is the shape this route's own backend docstring names and refuses to
 * repeat. */
export interface ReportAuditSessionsPage {
  sessions: ReportAuditSessionSummary[];
  total: number;
}

/** Sessions in THIS workspace worth offering on the /report page's
 * approval-history session picker (Track R unit R-5c, delegated in
 * docs/phase2/DECISIONS.md's 2026-08-24 "HWP는 v1 제외 확정 · 승인 이력 세션
 * 픽커는 위임" entry -- R-5b had named the gap: the page carried no
 * `session_id` input at all, so `approval_history_table` rendered a
 * permanent named absence).
 *
 * Reads `backend/routers/agent_audit.py`'s own listing route, not
 * `fetchAgentDownloads` -- that one lists sessions that saved a write
 * (`edit_NNN.inpx`), a DIFFERENT set from "sessions whose ledger holds an
 * applied change" (a write can dispatch and get postwrite-rolled-back with no
 * surviving snapshot, and a snapshot can exist from a plan that was never
 * gated at all). Using the downloads set here would be exactly the invented
 * mapping DECISIONS.md's R-5c entry rules out by name, so this calls the
 * route that measures the ledger directly instead. */
export async function fetchReportAuditSessions(): Promise<ReportAuditSessionsPage> {
  const r = await apiFetch("/api/agent/audit/sessions", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return (await r.json()) as ReportAuditSessionsPage;
}

/** What `POST /compose` publishes BESIDE the document: the two headline counts
 * it puts on response headers. `null` means the header was not readable (a
 * cross-origin response exposes no custom headers), which is not the same as
 * zero and is rendered differently. */
export interface ComposedReport {
  blob: Blob;
  filename: string;
  sections: number | null;
  limitations: number | null;
}

function headerCount(response: Response, name: string): number | null {
  const raw = response.headers.get(name);
  if (raw === null) return null;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

/** R-5a's styled report, as one .docx.
 *
 * `body` is posted VERBATIM: this function adds no field and drops none, so
 * what the screen shows as the selection is what the typesetter is asked for.
 * A refusal arrives as `ReportRefusedError` carrying the backend's own
 * ko/en/ja sentence. */
export async function composeReport(body: ComposeReportRequest): Promise<ComposedReport> {
  const r = await apiMutationFetch("/api/report/compose", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!r.ok) throw await reportError(r);
  const blob = await r.blob();
  return {
    blob,
    filename: filenameFromContentDisposition(r.headers.get("content-disposition")) ?? "prism-report.docx",
    sections: headerCount(r, "X-Prism-Report-Sections"),
    limitations: headerCount(r, "X-Prism-Report-Limitations"),
  };
}

export interface ComposedReportArtifacts {
  blob: Blob;
  filename: string;
  artifacts: number | null;
}

/** The sibling bundle: every graphic figure's SVG ORIGINAL plus
 * `manifest.json`. Same body, same refusals. The manifest is parsed by
 * lib/reportManifest.ts, which explains why the zip is the only place the
 * typesetter's limitation SENTENCES are published over HTTP. */
export async function composeReportArtifacts(body: ComposeReportRequest): Promise<ComposedReportArtifacts> {
  const r = await apiMutationFetch("/api/report/compose/artifacts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!r.ok) throw await reportError(r);
  const blob = await r.blob();
  return {
    blob,
    filename: filenameFromContentDisposition(r.headers.get("content-disposition")) ?? "prism-report-artifacts.zip",
    artifacts: headerCount(r, "X-Prism-Report-Artifacts"),
  };
}

export async function resetBaseWorkbook(): Promise<BaseWorkbookResponse> {
  const r = await apiMutationFetch("/api/validation/base-workbook", {
    method: "DELETE",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function uploadBaseWorkbook(file: File): Promise<BaseWorkbookResponse> {
  const filename = encodeURIComponent(file.name);
  const path = `/api/validation/base-workbook?filename=${filename}`;
  let lastNetworkError: unknown = null;

  if (typeof window !== "undefined") {
    try {
      // sameOriginRequestInit (not a bare object literal) so this upload
      // carries the X-Workspace-Id header like every other request -- it is
      // the one same-origin path in this file that does not go through
      // apiFetch/apiMutationFetch/apiDirectMutationFetch.
      const r = await fetch(path, {
        ...sameOriginRequestInit(),
        method: "POST",
        cache: "no-store",
        body: file,
      });

      if (!r.ok) {
        throw new Error(await r.text());
      }

      return r.json();
    } catch (error) {
      if (!shouldRetryApiFetch(error)) {
        throw error;
      }
      lastNetworkError = error;
    }
  }

  for (const base of listMutationApiBases()) {
    try {
      const r = await fetch(apiUrlForBase(base, path), {
        ...requestInitForBase(base),
        method: "POST",
        mode: "cors",
        cache: "no-store",
        body: file,
      });

      if (!r.ok) {
        throw new Error(await r.text());
      }

      if (typeof window !== "undefined" && normalizeApiBase(base) !== getApiBase()) {
        setApiBaseOverride(base);
      }
      return r.json();
    } catch (error) {
      if (shouldRetryApiFetch(error)) {
        lastNetworkError = error;
        continue;
      }
      throw error;
    }
  }

  const triedBaseList = listMutationApiBases();
  const triedBases = triedBaseList.join(", ");
  const detail = lastNetworkError instanceof Error ? lastNetworkError.message : String(lastNetworkError ?? "unknown error");
  throw new Error(`Failed to upload validation workbook. Tried API: ${triedBases}. Last network error: ${detail}`);
}

// Flexible base-workbook mapping (backend/services/workbook_mapping.py):
// upload ANY spreadsheet, inspect its sheets, map columns to the fields the
// dashboard needs. `uploadBaseWorkbook` above stays the FIRST thing tried --
// this flow is only for files that come back from it with warnings (a
// non-template layout). See that module's docstring for the self-comparison
// trap `WorkbookMappingRefusedError` exists to make un-ignorable.

export interface WorkbookMappingFieldGuess {
  column: number;
  header: string;
  source: "alias" | "remembered";
  matched_alias: string | null;
}

export interface WorkbookInspectSheet {
  name: string;
  header_row: number;
  headers: string[];
  columns: string[];
  sample_rows: Array<Array<string | number | boolean | null>>;
  sample_truncated: boolean;
  max_row: number;
  max_col: number;
  columns_truncated?: boolean;
  proposed_counts_mapping: Record<string, WorkbookMappingFieldGuess>;
  proposed_travel_times_mapping: Record<string, WorkbookMappingFieldGuess>;
  // Present only when this one sheet failed to parse -- inspect never fails
  // the whole request over one malformed sheet (see inspect_workbook's
  // per-sheet try/except).
  error?: string;
}

export interface WorkbookInspectProposedBlock {
  sheet: string;
  header_row: number;
  fields: Record<string, WorkbookMappingFieldGuess>;
}

export interface WorkbookInspectResponse {
  upload_token: string;
  filename: string;
  sheets: WorkbookInspectSheet[];
  sheets_truncated: boolean;
  proposed_mapping: {
    counts: WorkbookInspectProposedBlock | null;
    travel_times: WorkbookInspectProposedBlock | null;
  };
}

export interface WorkbookMappingSpec {
  sheet: string;
  header_row: number;
  fields: Record<string, number>;
}

export interface WorkbookMappingIssue {
  code: string;
  ko: string;
  en: string;
  ja: string;
}

export interface WorkbookMappingPreviewBlock<TRow> {
  field_columns: Record<string, number>;
  total_rows: number;
  rows: TRow[];
  truncated: boolean;
  issues: WorkbookMappingIssue[];
}

export interface WorkbookMappingPreviewResponse {
  counts_preview: WorkbookMappingPreviewBlock<BaseWorkbookCountRow> | null;
  travel_times_preview: WorkbookMappingPreviewBlock<BaseWorkbookTravelTimeRow> | null;
}

export interface WorkbookMappingRequestBody {
  upload_token: string;
  counts?: WorkbookMappingSpec;
  travel_times?: WorkbookMappingSpec;
}

// Thrown by importBaseWorkbookMapping when the backend refuses the import
// (400 with `{detail: {issues: [...]}}`) -- distinct from a generic Error so
// the mapping UI can render each localized {ko, en, ja} reason inline
// instead of one flat error string.
export class WorkbookMappingRefusedError extends Error {
  issues: WorkbookMappingIssue[];

  constructor(issues: WorkbookMappingIssue[]) {
    super(issues.map((issue) => issue.en).join(" "));
    this.name = "WorkbookMappingRefusedError";
    this.issues = issues;
  }
}

export async function inspectBaseWorkbook(file: File): Promise<WorkbookInspectResponse> {
  const filename = encodeURIComponent(file.name);
  const path = `/api/validation/base-workbook/inspect?filename=${filename}`;
  const r = await apiMutationFetch(path, { method: "POST", cache: "no-store", body: file });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

// Best-effort cleanup for a staged-but-abandoned upload (the user cancelled
// the mapping modal without importing). Safe to call blind -- the backend
// treats an unknown/already-discarded/expired token as a no-op (204), and
// staging-dir TTL GC (workbook_mapping.py, 2h) covers it either way, so a
// failed call here is not worth surfacing as a user-facing error.
export async function discardBaseWorkbookMapping(token: string): Promise<void> {
  const r = await apiMutationFetch(`/api/validation/base-workbook/mapping/${encodeURIComponent(token)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(await readApiError(r));
}

export async function resolveBaseWorkbookMappingSheet(
  token: string,
  sheet: string,
  headerRow: number,
): Promise<WorkbookInspectSheet> {
  const params = new URLSearchParams({ sheet, header_row: String(headerRow) });
  const r = await apiFetch(`/api/validation/base-workbook/mapping/${encodeURIComponent(token)}/resolve?${params.toString()}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function previewBaseWorkbookMapping(payload: WorkbookMappingRequestBody): Promise<WorkbookMappingPreviewResponse> {
  const r = await apiMutationFetch("/api/validation/base-workbook/mapping/preview", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function importBaseWorkbookMapping(payload: WorkbookMappingRequestBody): Promise<BaseWorkbookResponse> {
  const r = await apiMutationFetch("/api/validation/base-workbook/mapping/import", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (r.ok) return r.json();

  if (r.status === 400) {
    const text = await r.text();
    try {
      const data = JSON.parse(text) as { detail?: { issues?: WorkbookMappingIssue[] } };
      if (data.detail && Array.isArray(data.detail.issues) && data.detail.issues.length > 0) {
        throw new WorkbookMappingRefusedError(data.detail.issues);
      }
      const plainDetail = (data as { detail?: unknown }).detail;
      throw new Error(typeof plainDetail === "string" ? plainDetail : text);
    } catch (parseError) {
      if (parseError instanceof WorkbookMappingRefusedError) throw parseError;
      throw new Error(text || `${r.status} ${r.statusText}`);
    }
  }

  throw new Error(await readApiError(r));
}

// Phase 3 P3: resolved vehicle-trajectory playback frames
// (backend/services/trajectory_reader.py + backend/routers/trajectories.py).
// Positions are resolved server-side (lane_offset_point/point_at_pos) against
// the active dataset's network geometry -- same "server resolves geometry,
// frontend never reimplements polyline math" pattern as NetworkElements.
export interface TrajectoryVehicleFrame {
  id: number;
  link: string;
  lane: number | null;
  pos: number;
  // A real Vissim export's default vehicle-record attribute selection has
  // no SPEED column at all (verified live -- see docs/phase2/DECISIONS.md
  // and backend/services/trajectory_reader.py's module docstring), so this
  // is genuinely nullable, not just optional -- a renderer must treat
  // `null` as "unknown speed", never as "stopped" (0).
  speed: number | null;
  // Present once resolved against known network geometry; absent when the
  // vehicle's link couldn't be resolved (dropped from the frame instead, so
  // in practice every vehicle present here already has these).
  x?: number;
  y?: number;
  angle?: number;
}

export interface TrajectoryFrame {
  t: number;
  vehicles: TrajectoryVehicleFrame[];
}

export interface TrajectoryMeta {
  t0: number | null;
  t1: number | null;
  step: number | null;
  vehicle_count: number;
  skipped_rows: number;
  source_file?: string;
  source_path?: string;
  skipped_unresolved_vehicles?: number;
  returned_frame_count?: number;
  stride?: number;
  t0_filter?: number;
  t1_filter?: number;
  // Present only when the request replayed a completed agent-run job
  // (see the `job_id` param on fetchTrajectories below).
  job_id?: string;
}

export interface TrajectoryFrames {
  meta: TrajectoryMeta;
  frames: TrajectoryFrame[];
}

export async function fetchTrajectories(params?: {
  t0?: number;
  t1?: number;
  stride?: number;
  /** Replay a completed agent-run job's own recorded trajectory instead of
   * the default file-next-to-dataset/demo source (backend/routers/
   * trajectories.py's `job_id` query param). */
  jobId?: string;
}): Promise<TrajectoryFrames> {
  const query = new URLSearchParams();
  if (params?.t0 !== undefined) query.set("t0", String(params.t0));
  if (params?.t1 !== undefined) query.set("t1", String(params.t1));
  if (params?.stride !== undefined) query.set("stride", String(params.stride));
  if (params?.jobId !== undefined) query.set("job_id", params.jobId);
  const qs = query.toString();
  // Trajectory payloads are big by nature (a real 600s/5-seed run's frames
  // JSON measured 44MB, ~6.3s to transfer) — the shared 5s API_FETCH_TIMEOUT_MS
  // aborted the download mid-flight on every fallback URL (net::ERR_ABORTED,
  // live-diagnosed 2026-07-27). Supplying our own long-lived signal makes
  // fetchWithTimeout skip the short global timeout entirely.
  const r = await apiFetch(`/api/trajectories${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(120_000),
  });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

// ---------------------------------------------------------------------------
// Roadmap v2 §5.B B1 / §5.A A4 -- Genesis: OSM in, gated network out
// ---------------------------------------------------------------------------
//
// The whole chain (backend/routers/genesis.py -> services/genesis_jobs.py) is
// two calls plus polling: stage the .osm, start a job, watch it. What comes
// back is deliberately NOT a boolean:
//
//   refused     a refuse-tier check PROVED a fault. Nothing was registered, and
//               the artifact + verdict stay downloadable so the fault is
//               actionable.
//   provisional published, but a refuse-tier check could not RUN. On a fresh
//               generation that is always V1 -- a generation job may not open
//               COM, and V1 is the only check that proves a real Vissim opens
//               the file. `gate.live_evidence` is how that stops being true.
//   published   every refuse-tier check genuinely passed.
//   error       the .osm never became a network at all. `refusal.reasons` says
//               why; distinct from `refused`, which means an artifact exists.
//
// The UI must never collapse those four into "ok / not ok" -- the tri-state IS
// the product (tk-requirements-2026-07-31.md §R1.5).

//   checked     NOT a verdict: a LIVE-CHECK job's own terminal state (unit
//               #33). That job opens its parent's artifact in a real Vissim and
//               hands the evidence to the PARENT's re-gate -- the verdict it
//               changed lives on the parent, never here. `load_ok: false` still
//               lands `checked`: the probe worked and the gate answered.

export type GenesisJobStatus =
  | "queued"
  | "running"
  | "refused"
  | "provisional"
  | "published"
  | "error"
  | "checked";

export type GenesisGateVerdict = "pass" | "refused" | "incomplete";

export type GenesisCheckState = "pass" | "fail" | "not_checkable";

export interface GenesisGateCounts {
  pass: number;
  fail: number;
  not_checkable: number;
}

export interface GenesisLiveEvidence {
  label: string;
  network: string;
  vissim_version: string | null;
  sha256: string | null;
  // Whether gate.py ADMITTED the evidence -- false when the probe's file
  // sha256 does not equal the gated file's. Evidence about another build is not
  // weak evidence, so this renders as "not admitted", never as "pending".
  admitted: boolean;
  gap_codes: string[];
  // What this evidence RESTS ON, and there are exactly two answers.
  //
  //   "operator_attested"  it arrived over HTTP, so the app cannot prove Vissim
  //                        ever ran -- a caller who knows the artifact can
  //                        hand-write `load_ok: true` about it. Recorded as an
  //                        operator's attestation, with a name, and the UI says
  //                        so.
  //   "app_observed"       a LIVE-CHECK JOB (unit #33) opened the file itself,
  //                        on this app's own job queue, and wrote the record
  //                        from what LoadNet returned. Nothing was claimed.
  //
  // The second is stronger and is still not "proven" -- the record is a file in
  // the workspace. A V1 that went green either way must never read like one the
  // app watched happen unless it is the one it did.
  basis: string;
  attested_by: string | null;
  attested_at: string | null;
}

/** What CAUSED this verdict to replace an earlier one, and where the earlier
 * one's bytes were kept (unit #33). `null` on a first gate: absence is the
 * honest answer for a generation nobody re-gated, never an empty archive. */
export interface GenesisRegateProvenance {
  source?: string;
  job_id?: string;
  at?: string;
  previous_gate_archived: string | null;
}

export interface GenesisJobGate {
  verdict: GenesisGateVerdict;
  counts: GenesisGateCounts;
  // Refuse-tier checks that are not PASS -- the reason the verdict is not one.
  blocking: string[];
  checked_at: string;
  network_sha256: string;
  live_evidence: GenesisLiveEvidence | null;
  live_evidence_gap: string | null;
  // True when a refuse-tier check is green because a human attested it rather
  // than because this process observed it. A live-check job's own evidence is
  // the case where this is legitimately false WITH a green V1 -- read
  // `live_evidence.basis` for which, never this flag alone.
  rests_on_attestation: boolean;
  regate?: GenesisRegateProvenance | null;
}

export interface GenesisPublication {
  published_at: string;
  dataset_filename: string;
  dataset_revision: string;
  artifact_sha256: string;
  replaced: { source: string; filename: string; revision: string };
}

export interface GenesisRevocation {
  revoked_at: string;
  removed_from_workspace: boolean;
  reason: string;
  detail?: string;
}

/** WHAT WAS ASKED FOR, as opposed to what came back — written onto the job
 * record by the runner (`genesis_jobs._run_genesis_job_locked`) out of the
 * frozen `source.osm`'s own fetch provenance, so the 생성 이력 list can say
 * which area a generation is about without downloading its artifact.
 *
 * THE THREE-WAY DISTINCTION IS THE POINT, and it is carried by `undefined` vs
 * `null` vs an object — see `requestedRegionLabel` in lib/genesisArtifacts.ts,
 * which is the only place allowed to word it:
 *   * FIELD ABSENT (`undefined`) — a record written before this field existed.
 *     Nothing is known about its region and nothing may be invented for it.
 *   * `null` — the runner DID read the frozen .osm and found no fetch
 *     provenance in it, which is what a hand-uploaded file looks like.
 *   * an object — the .osm came from `POST /api/genesis/region/fetches`, and
 *     `bbox` is the rectangle the build was actually judged against (for a
 *     lasso that is the ring's OWN bounding box — the same rectangle the trim
 *     and the boundary ladder use, see `services/osm_fetch.py`'s provenance
 *     comment). `polygon_vertices` is present ONLY for a lasso fetch and is a
 *     COUNT, not the ring: the vertices live in the artifact's metadata and a
 *     history row has no business redrawing them. */
export interface GenesisRequestedRegion {
  bbox: { south: number; west: number; north: number; east: number } | null;
  polygon_vertices: number | null;
}

/** Present ONLY on a fix job's record (roadmap B5-2, `genesis_jobs.create_genesis_fix_job`'s
 * own docstring) -- key-ABSENT on an ordinary generation, never `null`, the same
 * "presence is itself the fact" discipline `requested_region` already established on this
 * type. `job_id` here is the PARENT this fix job was cloned from -- not to be confused with
 * `GenesisJob.id`, which is the fix job's own id. `subject` (unit #30) is the RESOLVED
 * finding's own subject (e.g. the vehicle input number) -- which of two same-coded findings
 * this fix answered -- and is key-ABSENT on records written before the field existed, so it
 * is optional here, never null. */
export interface GenesisFixLineage {
  job_id: string;
  finding_code: string;
  subject?: string;
  verb: string;
  params: Record<string, unknown>;
  approved_by: string | null;
  approval_rationale: string | null;
  approved_at: string;
  parent_sha256: string;
}

/** Present ONLY on a LIVE-CHECK job's record (unit #33). Same `derived_from`
 * key as a fix job's lineage and told apart by `kind`, which a fix lineage does
 * not carry at all -- the same "presence is itself the fact" discipline the
 * rest of this type uses. `job_id` is the PARENT whose artifact was cloned and
 * whose verdict this run re-decided. */
export interface GenesisLiveCheckLineage {
  kind: "live_check";
  job_id: string;
  requested_by: string | null;
  requested_at: string;
  parent_sha256: string;
}

export type GenesisJobLineage = GenesisFixLineage | GenesisLiveCheckLineage;

/** A LIVE-CHECK job's own result, written by its runner (unit #33). Present
 * only on that job type, and `null` until the probe has answered.
 *
 * `admitted` is read back off the PARENT's gate summary, not asserted by the
 * runner: whether evidence counts is gate.py's answer to its sha256 identity
 * rule. `load_ok: false` with `admitted: true` is a real and important state --
 * a real Vissim refused the file, and the gate believed it. */
export interface GenesisLiveCheckResult {
  parent_job_id: string;
  label: string;
  state_file: string;
  load_ok: boolean | null;
  load_error: string | null;
  save_ok: boolean | null;
  vissim_version: string | null;
  admitted: boolean | null;
  gap_codes: string[];
  parent_status: GenesisJobStatus;
  parent_verdict: GenesisGateVerdict | null;
  previous_gate_archived: string | null;
}

export interface GenesisJob {
  id: string;
  workspace_id: string;
  name: string;
  status: GenesisJobStatus;
  phase: string;
  progress: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  submitted_by: string | null;
  options: { exclude_service: boolean };
  /** `null` on a job that has no .osm of its own -- a fix job and a live-check
   * job both re-use the PARENT's artifact bytes and never re-parse an extract.
   * Nullable rather than optional because the backend writes the key as `null`
   * for those two, and a reader that assumed an object here crashed on them. */
  source_osm: { filename: string; bytes: number; sha256: string } | null;
  /** OPTIONAL on purpose — a record written before U-1-quick carries no such
   * key, and `undefined` is what tells the UI to say "—" instead of "업로드".
   * See `GenesisRequestedRegion` for the full three-way rule. */
  requested_region?: GenesisRequestedRegion | null;
  network: { links: number; connectors: number; nodes: number } | null;
  artifact: { inpx: string; sha256: string; bytes: number } | null;
  gate: GenesisJobGate | null;
  publication: GenesisPublication | null;
  revocation?: GenesisRevocation | null;
  refusal: { code: string; reasons: string[] } | null;
  error: string | null;
  /** See `GenesisFixLineage` / `GenesisLiveCheckLineage` -- absent means "an
   * ordinary generation", not "unknown". Narrow with `"kind" in derived_from`. */
  derived_from?: GenesisJobLineage;
  /** See `GenesisLiveCheckResult` -- present only on a live-check job. */
  live_check?: GenesisLiveCheckResult | null;
}

export interface GenesisGateFinding {
  code: string;
  subject: string;
  // Rendered by gate.py's OWN ko/en/ja table -- this client never authors a
  // replacement sentence for a finding the backend already words.
  message: string;
}

export interface GenesisGateCheck {
  id: string;
  tier: "refuse" | "warn";
  state: GenesisCheckState;
  title: string;
  blocks: boolean;
  findings: GenesisGateFinding[];
  // How many findings the payload left out (gate.json on disk always holds all
  // of them). Never silent: a dropped finding nobody counts is the failure mode
  // this project's operating principles forbid.
  findings_omitted: number;
  warnings: GenesisGateFinding[];
  warnings_omitted: number;
  gaps: Array<{ code: string; message: string }>;
  evidence: Record<string, unknown>;
}

export interface GenesisGateReport {
  job_id: string;
  language: string;
  network: string;
  network_sha256: string;
  verdict: GenesisGateVerdict;
  counts: GenesisGateCounts;
  status: GenesisJobStatus;
  publication: GenesisPublication | null;
  revocation: GenesisRevocation | null;
  live_evidence: GenesisLiveEvidence | null;
  checks: GenesisGateCheck[];
  report_markdown: string;
}

export class GenesisError extends Error {
  status: number;
  // The backend's stable refusal code (`genesis_upload_not_osm`,
  // `genesis_bad_name`, ...) when it sent one, so the UI can translate the
  // REASON instead of showing an English sentence to a Korean consultant.
  code: string | null;
  reasons: string[];

  constructor(message: string, status: number, code: string | null = null, reasons: string[] = []) {
    super(message);
    this.name = "GenesisError";
    this.status = status;
    this.code = code;
    this.reasons = reasons;
  }
}

async function genesisError(response: Response): Promise<GenesisError> {
  const text = await response.text();
  try {
    const data = JSON.parse(text) as { detail?: unknown };
    const detail = data.detail;
    if (detail && typeof detail === "object") {
      const structured = detail as { code?: string; reasons?: string[] };
      return new GenesisError(
        (structured.reasons ?? []).join(" ") || String(structured.code ?? text),
        response.status,
        structured.code ?? null,
        structured.reasons ?? [],
      );
    }
    if (typeof detail === "string") return new GenesisError(detail, response.status);
  } catch {
    // Plain text errors are still useful.
  }
  return new GenesisError(text || `${response.status} ${response.statusText}`.trim(), response.status);
}

export async function uploadGenesisOsm(
  file: File,
): Promise<{ token: string; filename: string; bytes: number }> {
  const path = `/api/genesis/uploads?filename=${encodeURIComponent(file.name)}`;
  const r = await apiMutationFetch(path, { method: "POST", cache: "no-store", body: file });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

export async function startGenesisJob(payload: {
  upload_token: string;
  name?: string;
  exclude_service?: boolean;
  // The optional layers, staged through POST /side-inputs (or, since this
  // unit, through parseDemandCounts + buildDemandInput /
  // stagePhaseDiagramSignalsInput). Omitted = the geometry-only path this
  // field has always taken.
  demand_token?: string;
  signals_token?: string;
}): Promise<GenesisJob> {
  const r = await apiMutationFetch("/api/genesis/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

export async function fetchGenesisJobs(): Promise<GenesisJob[]> {
  const r = await apiFetch("/api/genesis/jobs", { cache: "no-store" });
  if (!r.ok) throw await genesisError(r);
  const payload = (await r.json()) as { jobs: GenesisJob[] };
  return payload.jobs;
}

export async function fetchGenesisJob(jobId: string): Promise<GenesisJob> {
  const r = await apiFetch(`/api/genesis/jobs/${jobId}`, { cache: "no-store" });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

export async function fetchGenesisGateReport(
  jobId: string,
  language: string,
): Promise<GenesisGateReport> {
  const r = await apiFetch(
    `/api/genesis/jobs/${jobId}/gate?language=${encodeURIComponent(language)}`,
    { cache: "no-store" },
  );
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

// Attach a real Vissim's LoadNet evidence (tools/genesis_open_live_check.py's
// state.json) and RE-GATE. The upgrade is never granted client-side: gate.py
// admits the evidence only when the sha256 of the file the probe loaded equals
// the gated file's, and evidence that the load FAILED downgrades the verdict
// and revokes the publication.
export async function attachGenesisLiveCheck(
  jobId: string,
  state: unknown,
  label?: string,
): Promise<GenesisJob> {
  const r = await apiMutationFetch(`/api/genesis/jobs/${jobId}/live-check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state, label: label ?? null }),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** Queue a job that opens THIS generation's artifact in a REAL Vissim and
 * re-gates it on what the load actually did (unit #33).
 *
 * The difference from `attachGenesisLiveCheck` above is who ran the probe. That
 * one records an operator's claim about a probe they ran at a console; this one
 * makes the app run it -- so the caller gets back a QUEUED JOB record (202), not
 * a re-gated parent, and polls it through `fetchGenesisJob` like any other job.
 * The parent's verdict changes on its own schedule, which is why the detail page
 * re-keys its gate fetch on `gate.checked_at`.
 *
 * Nothing here decides anything: the verdict is gate.py's, from the evidence the
 * job wrote. A parent with no gated artifact answers a NAMED refusal
 * (`genesis_live_check_parent_not_gated` / `genesis_live_check_artifact_missing`)
 * which the UI renders as a sentence -- never as a disabled button. */
export async function startGenesisLiveCheck(jobId: string): Promise<GenesisJob> {
  const r = await apiMutationFetch(`/api/genesis/jobs/${jobId}/live-check/run`, {
    method: "POST",
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

export type GenesisArtifactKind =
  | "inpx"
  | "genesis_json"
  | "compliance_md"
  | "gate_json"
  | "gate_report"
  | "source_osm";

/** One artifact's BYTES, fetched the way every other authenticated genesis read
 * is fetched.
 *
 * WHAT THIS REPLACED, and why an `<a href>` could not work here (U-1-quick,
 * docs/ux-backlog.md item #2 -- reported from live use as "all five artifact
 * buttons do nothing"). The row used to be five plain anchors pointing at
 * `genesisArtifactUrl()`, i.e. at `apiUrl(...)` -- an ABSOLUTE
 * `http://127.0.0.1:8001/...` URL. That single line broke the download three
 * separate ways, each of them enough on its own:
 *
 *   1. AUTH. `routers/genesis.py`'s `download_artifact` takes
 *      `Depends(current_user)` (deliberately -- it hands out the network file
 *      itself, and its docstring gives the laundering attack it exists to
 *      stop). The session cookie is set by `POST /api/auth/login`, which this
 *      app issues through the SAME-ORIGIN Next proxy (next.config.ts rewrites
 *      `/api/:path*`), so the cookie is host-only for the page's own origin --
 *      `localhost:3000`. A navigation to `127.0.0.1:8001` is a different host
 *      and carries no cookie at all, so the response is the 401 that
 *      `backend/tests/test_genesis_jobs.py` already pins for an unauthenticated
 *      caller of this exact route.
 *   2. WORKSPACE. Even signed in, an anchor cannot set a request header, so it
 *      cannot carry `X-Workspace-Id`. `main.WorkspaceScopeMiddleware` binds an
 *      absent header to the DEFAULT workspace, `genesis_jobs.artifact_path`
 *      then reads the default workspace's registry, and a job belonging to any
 *      other 프로젝트 comes back 404 -- "unknown id, malformed id, or a job
 *      belonging to another workspace -- indistinguishable on purpose"
 *      (`genesis_jobs.get_job`). This is the named 1.4 hole, recorded in
 *      docs/ux-backlog.md under "workspace scoping cannot reach two surfaces".
 *   3. SILENCE. Whatever the browser then did with that 401/404 -- render it,
 *      or drop it in the downloads shelf -- the page itself said nothing,
 *      because a bare href has no failure branch to say it with.
 *
 * Going through `apiFetch` fixes all three at once: it takes the same-origin
 * path first (so the cookie rides along), `sameOriginRequestInit` attaches
 * `X-Workspace-Id`, and the caller gets a `Response` it can name a failure
 * from. The bytes come back as a Blob and the CALLER saves them; this function
 * deliberately does not touch the DOM, so it stays testable and stays the one
 * place the artifact route is addressed.
 *
 * The filename comes from the response's own `Content-Disposition` through the
 * SAME reader `downloadAgentModel` uses (`filenameFromContentDisposition` --
 * FastAPI's `FileResponse(..., filename=path.name)` always sets the header),
 * and falls back to `<jobId>.<kind>` -- never to a name invented from the job's
 * display name, which is not what is on disk. */
export interface GenesisArtifactDownload {
  blob: Blob;
  filename: string;
}

export async function fetchGenesisArtifact(
  jobId: string,
  kind: GenesisArtifactKind,
): Promise<GenesisArtifactDownload> {
  const r = await apiFetch(`/api/genesis/jobs/${jobId}/artifact/${kind}`, { cache: "no-store" });
  if (!r.ok) throw await genesisError(r);
  return {
    blob: await r.blob(),
    filename: filenameFromContentDisposition(r.headers.get("content-disposition")) ?? `${jobId}.${kind}`,
  };
}

// ---------------------------------------------------------------------------
// Roadmap B5-2 stage 4 -- "거부는 진단, 에이전트는 치료" made clickable. The two
// routes `backend/routers/genesis.py` landed in stage 3 (commit 194a2f6):
//   GET  /jobs/{id}/fixes        -- the registry's disposition for EVERY
//                                    finding in the job's STORED gate verdict
//   POST /jobs/{id}/fixes/apply  -- one approved fix -> a sibling fix job
// -----------------------------------------------------------------------------
// `GenesisFixDisposition.status` is a THIRD vocabulary, not `fixes.py`'s tidy
// two-value AUTOMATABLE/NOT_AUTOMATABLE -- it is whatever
// `services.agent.fixes` derives live (`VERB_UNREGISTERED` when a mapped verb
// has since been renamed/retired), so this type keeps it as `string` rather
// than a closed union the backend could silently outgrow. **The one thing a
// caller must NOT do is treat `status === "AUTOMATABLE"` as "safe to render an
// apply button"** -- see `app/genesis/page.tsx`'s `fixDispositionIsActionable`
// for why (`_render_fix_disposition`'s own docstring, "three shapes, never a
// fourth": a structural refusal on an AUTOMATABLE-declared mapping — e.g. V6's
// MEASURED-entrance `INPUT_VOLUME_IS_OBSERVED_DATA` — reports
// `status: "AUTOMATABLE"` with `params: null`, and `params !== null` is the
// only field this backend actually uses to mean "there is something to
// apply").

/** One finding's disposition -- the registry's answer, never re-derived
 * client-side. `rationale` is ALREADY trilingual (`FixProposal.rationale()`),
 * so unlike the gate report this needs no `language=` query param and no
 * re-fetch when the app language toggles. */
/** Unit #36: the job's own source `.osm` declares the connection the network
 * lost, with the exact way/node ids it declares it with. Key-ABSENT on every
 * disposition that has no such measurement — presence IS the fact, so a
 * consumer must never read an absent field as "the source stayed silent". */
export interface GenesisFixSourceTestimony {
  source_artifact: string;
  way_id: number;
  node_id: number;
  connected_way_ids: number[];
  coordinates: { lat: number; lon: number } | null;
}

/** Unit #35: the ask-first QUESTION a V3 world-gap refusal carries. The source
 * shares the gap, so nothing here proposes anything — these are the link ends
 * a human who knows the site could attest the road continues to, each with the
 * MEASURED metres between the two ends. Key-ABSENT unless the model actually
 * holds a reachable end: an attestation with no possible target is an empty
 * question, and the backend does not ask it. */
export interface GenesisFixAttestationInvitation {
  from_link: string | null;
  node_id: number | null;
  candidates: Array<{
    link_id: string;
    name: string;
    way_id: number;
    lanes: number;
    starts_at_node_osm_id: number;
    distance_m: number;
    turn: string;
    /** The connector that WOULD be constructed for this candidate — the exact
     * payload an attesting approval submits. Derived server-side (positions
     * from the two link ends, lanes from the generator's own pairing rule), so
     * this client never composes connector params itself; the apply path
     * re-derives them and refuses anything that differs. */
    params: Record<string, unknown>;
  }>;
}

/** Unit #38: the THIRD disposition — the finding's treatment is not an edit but
 * a NEW generation re-derived from the SAME source through a corrected
 * converter. Present only when the backend MEASURED that the census cannot
 * correct this finding (the draft already shares the defect); key-ABSENT
 * otherwise, so an absent field never reads as "regeneration was considered and
 * rejected". `verb`/`params` are `null` beside it on purpose: this finding has
 * a treatment, and it is not the registered edit verb. */
export interface GenesisFixRegeneration {
  status: string;
  finding_code: string;
  derivation_reason_code: string;
  explanation: { ko: string; en: string; ja: string };
  /** What the converter now does differently, from the converter's own ledger
   * (`services.genesis.osm.CONVERTER_FIXES`) — the same ids the regenerated
   * job's `derived_from.converter_fixes` carries. */
  converter_fixes?: Array<{
    id: string;
    unit: string;
    since: string;
    summary: string;
    skip_reasons: string[];
    gate_codes: string[];
  }>;
}

/** Unit #39: WHOSE the shortage is — the runway corridor's upstream end
 * classified against the census's own boundary markers and the source's declared
 * continuation (`genesis_jobs.derive_v4_boundary_attribution`). MEASUREMENT,
 * never a tier change: the finding stays a refusal whichever way this lands.
 *
 * `status` is the backend's own word and the only safe thing to branch on:
 * `boundary_attributed` (the requested ring is the cause) or `interior` (the
 * corridor ends inside the model, so the shortage is the road's). The optional
 * fields follow the status — an interior attribution names a `terminus_node` and
 * no `way_id`, and reading one off the other is how a client invents a cause. */
export interface GenesisFixBoundaryAttribution {
  status: string;
  way_id?: number;
  boundary_node?: number;
  deficit_m?: number;
  boundary_marker?: string;
  terminus_node?: number;
  reason?: string;
}

/** Unit #40: the widened REGION that would close a boundary-attributed runway
 * shortage — the user's own requested lasso with a finger spliced into the one
 * ring edge the corridor crosses. Key-ABSENT unless the backend actually
 * derived one (an interior shortage has no region treatment, and a splice it
 * refused by name has none either), so an absent field never reads as "the
 * boundary was not the cause".
 *
 * UNIT #48-b (user decision, 2026-08-20): NOTHING IN THIS APP RENDERS THIS
 * SHAPE ANY MORE. Live use measured the geometry out — every widened ring cuts
 * new roads somewhere else, so the derived proposals were whack-a-mole (jobs
 * 7f263915d770 and e2f654400d21) — and the automatic proposal UI was replaced by
 * a manual 영역 편집 button. The backend derivation and this type are RETAINED
 * and un-exposed on purpose: the measurement is sound and the obvious next home
 * for it is a PRE-FLIGHT boundary check on the region screen, before an extract
 * is ever run. Do not delete either without that decision being made.
 *
 * THE KEY'S PRESENCE IS STILL READ, and precisely that — corrected by the
 * refuter, because saying "nothing renders this" of a key one branch tests was
 * how a stale imperative survived the removal. `app/genesis/jobs/[id]/page.tsx`
 * asks whether this key is THERE to decide whether a NOT_AUTOMATABLE card's
 * one-line answer names the boundary as the cause and points at 영역 편집. It is
 * the right question to ask, and not an accident of convenience: the key is
 * absent unless the census recorded a requested POLYGON
 * (`derive_v4_boundary_extension` refuses `requested_region_not_a_polygon`
 * otherwise), which is the same field that screen's 영역 편집 button needs before
 * it renders at all — so the answer line can only name a button that is there.
 * None of the MEASUREMENTS below are read: not `added_area_km2`, not the
 * corridor patch, not the polygon.
 *
 * NOT a regeneration (`GenesisFixRegeneration` above): that re-derives from the
 * SAME source bytes, and this shortage's road is not in those bytes — the
 * stored `.osm` was fetched with the very region being extended. The treatment
 * is a NEW generation from a WIDER extract, which is why this carries a shape
 * for a human to check on a map rather than a button that starts anything. */
export interface GenesisFixBoundaryExtension {
  /** `[lat, lon]` pairs, open ring — the region page's own vertex convention. */
  extended_polygon_wgs84: Array<[number, number]>;
  extended_area_km2: number;
  added_area_km2: number;
  corridor_patch: {
    crossing_edge_index: number;
    crossing_point_wgs84: [number, number];
    centreline_wgs84: Array<[number, number]>;
    length_m: number;
    /** How much of `length_m` the SOURCE itself declared, and how much was
     * drawn on the last measured bearing because the extract is clipped at the
     * same boundary. Reported separately, never summed into one claim. */
    measured_source_m: number;
    extrapolated_m: number;
    half_width_m: number;
    roadway_width_m: number;
    continuation_way_ids: number[];
    vertices_added: number;
  };
  provenance: {
    source_artifact: string;
    source_field: string;
    parent_job: string | null;
    finding: { code: string; subject: string };
    way_id: number;
    boundary_node: number;
    deficit_m: number;
    margin_m: number;
    basis: string;
    boundary_marker: string;
    treatment: string;
  };
}

/** Unit #48: ONE widened region covering SEVERAL severed corridors — the shape
 * `GenesisFixBoundaryExtension` above cannot carry.
 *
 * Two callers, one payload. A `v3_orphan_link` whose way the lasso cut at BOTH
 * ends needs two fingers, not one; and a job with several boundary findings
 * needs them all on ONE ring, because two proposals each derived from the same
 * original polygon overwrite each other (the 2026-08-19 demo: extending for the
 * runway shortage never healed the orphan, since their fingers open onto
 * different edges).
 *
 * Every finger names what it was SIZED BY (`basis_kind`), because half of them
 * are not deficits: a severed orphan's finger reaches the metres the trim
 * ledger says the boundary CUT. Key-ABSENT unless a shape was actually derived;
 * the refusal, when there is one, arrives under its own key.
 *
 * UNIT #48-b (user decision, 2026-08-20): un-exposed, retained. See
 * `GenesisFixBoundaryExtension` above for the measured reason and for what this
 * machinery is a candidate to become. */
export interface GenesisBoundaryExtensionPatches {
  /** `[lat, lon]` pairs, open ring — the region page's own vertex convention. */
  extended_polygon_wgs84: Array<[number, number]>;
  extended_area_km2: number;
  added_area_km2: number;
  corridor_patches: Array<{
    crossing_edge_index: number;
    crossing_point_wgs84: [number, number];
    centreline_wgs84: Array<[number, number]>;
    length_m: number;
    /** How much of `length_m` the SOURCE itself declared, and how much was
     * drawn on the last measured bearing. Reported separately, never summed. */
    measured_source_m: number;
    extrapolated_m: number;
    half_width_m: number;
    roadway_width_m: number;
    continuation_way_ids: number[];
    vertices_added: number;
    way_id: number;
    boundary_node: number;
    /** What this finger's length was measured FROM, and which measurement that
     * was: `lane_change_runway_deficit` or `boundary_cut_metres`. */
    basis_m: number;
    basis_kind: string;
    margin_m: number;
    /** Every finding this one finger answers for — more than one only when two
     * findings resolved to the identical corridor end (the named merge). */
    covers: Array<{ code: string; subject: string }>;
  }>;
  /** Per FINDING rather than per finger: what the shape does for each one. */
  covers: Array<{
    code: string;
    subject: string;
    patch_count: number;
    basis_kind: string;
    basis_m: number;
    length_m: number;
    measured_source_m: number;
    extrapolated_m: number;
  }>;
  provenance: {
    source_artifact: string;
    source_field: string;
    parent_job: string | null;
    patch_count: number;
    finding_count: number;
    merged_patches: number;
    basis: string;
    treatment: string;
  };
}

/** Unit #48: why no all-at-once region could be derived.
 *
 * UNIT #48-b (user decision, 2026-08-20): NOT RENDERED. This refusal used to
 * print as prose beside the verdict; the user struck it with the proposal it
 * explained ("이건 지워"), because a named reason for withholding an option
 * nobody is offered any more is noise, not honesty. The key still arrives on the
 * wire and is still typed — see `GenesisFixBoundaryExtension` for why the whole
 * derivation is retained. */
export interface GenesisBoundaryExtensionRefusal {
  reason_code: string;
  detail: string;
}

/** Unit #48: WHY a `v3_orphan_link` is alone — measured against the census's
 * own trim ledger and cross-checked against the source's coordinates. Absent
 * when the orphan's way was never cut at the ring, which is the case that must
 * NOT be offered a region: an isolation the source itself declares is the
 * world's answer, not the boundary's. Evidence only; the tier never moves. */
export interface GenesisFixOrphanBoundaryAttribution {
  status: string;
  link_id: string;
  way_id: number;
  severed_ends: Array<{
    end: string;
    metres_cut: number;
    nodes_cut: number;
    boundary_node: number;
    outermost_cut_node: number;
    /** The ways the SOURCE says meet at the node the trim cut off — why the
     * link is alone. Measured, never a promise that a re-extract reconnects it:
     * the new draft decides that and faces the gate on its own. */
    junction_way_ids: number[];
    junction_way_ids_in_census: number[];
  }>;
  metres_cut_total: number;
  reason: string;
}

/** Unit #42: the SECOND ask-first question on a V3 world-gap refusal, and the
 * one `GenesisFixAttestationInvitation` cannot reach — the road may really END
 * here. Some world gaps are real: a department store's parking ramp genuinely
 * stops at the public network, and attesting to the nearest reachable candidate
 * would put false geometry in the model.
 *
 * Everything here is MEASURED, and it is the SCOPE the confirming reader signs
 * for: exactly one `way_id` (never the roads it touches), the census links that
 * carry it, and `collateral_way_ids` — what the SOURCE's own adjacency says
 * would be cut loose with it. Key-ABSENT whenever the disposition is not on
 * offer; the reason then arrives as `dead_end_refusal` and as prose. */
export interface GenesisFixDeadEndConfirmation {
  way_id: number;
  node_id: number | null;
  link_ids: string[];
  links: Array<{ link_id: string; name: string; lanes: number; length_m: number }>;
  excluded_length_m: number;
  /** Empty for the ordinary leaf case. Non-empty means confirming this dead end
   * also removes roads BEHIND it, which is a fact the reader must see before
   * they sign — the confirm call echoes this list back and the server refuses a
   * set that does not match its own re-derivation. */
  collateral_way_ids: number[];
  collateral: Array<{ way_id: number; name: string }>;
  source_component_ways: number;
  surviving_component_ways: number;
}

/** Unit #42: why the dead-end disposition is NOT on offer, when the reason is
 * one a reader deserves named. The load-bearing value is
 * `source_testifies_the_connection` — the source itself declares the road
 * continues, and a human confirmation may not delete road the map states. This
 * renders a SENTENCE, never a control: a refusal with a button is not a
 * refusal. Key-ABSENT when the disposition IS on offer, and also for the
 * measured reasons the testimony/connector prose already covers. */
export interface GenesisFixDeadEndRefusal {
  reason_code: string | null;
  detail: string | null;
}

export interface GenesisFixDisposition {
  status: string;
  verb: string | null;
  params: Record<string, unknown> | null;
  requires: string[];
  confidence: string;
  assumptions: string[];
  rationale: { ko: string; en: string; ja: string };
  reason_code: string | null;
  source_testimony?: GenesisFixSourceTestimony;
  attestation?: GenesisFixAttestationInvitation;
  regenerate?: GenesisFixRegeneration;
  /** Unit #39, typed here by unit #48-b: the route has always sent this key
   * (`routers/genesis._render_fix_disposition`) and no client had a use for it
   * until the 영역 편집 hand-off needed to say WHICH way the ring clipped and at
   * WHICH node. Key-ABSENT on every finding the derivation did not reach. */
  boundary_attribution?: GenesisFixBoundaryAttribution;
  boundary_extension?: GenesisFixBoundaryExtension;
  /** Unit #48, `v3_orphan_link` only: why the lasso orphaned this link, and the
   * region that would undo it — one finger per SEVERED END. Its own keys rather
   * than #40's pair, because a two-ended orphan cannot fit in a payload with
   * one `corridor_patch`. */
  orphan_boundary_attribution?: GenesisFixOrphanBoundaryAttribution;
  boundary_extension_patches?: GenesisBoundaryExtensionPatches;
  dead_end_confirmation?: GenesisFixDeadEndConfirmation;
  dead_end_refusal?: GenesisFixDeadEndRefusal;
}

export interface GenesisFixFinding {
  /** One of `report.checks[].id` (e.g. "V6") -- the join key back to the
   * V1-V9 grid's selected cell. */
  check_id: string;
  origin: "finding" | "warning";
  code: string;
  subject: string;
  context: Record<string, unknown>;
  disposition: GenesisFixDisposition;
}

export interface GenesisJobFixes {
  job_id: string;
  status: GenesisJobStatus;
  verdict: GenesisGateVerdict;
  /** Deliberately UNCAPPED (routers/genesis.py's own docstring) -- every
   * finding AND warning in the stored verdict, never truncated. */
  findings: GenesisFixFinding[];
  /** The registry's own mirror check: a gate finding code `gate.py` can emit
   * that this catalogue has no entry for at all. Carried through for
   * completeness; this page does not currently render it (no selected job's
   * fix list has ever contained one, and inventing a display for an empty
   * array to date would be speculative chrome). */
  unmapped_finding_codes: string[];
  /** Unit #48: ONE lasso covering EVERY boundary-caused finding in this
   * verdict. Key-ABSENT when nothing earned one; when a shape could not be
   * derived the reason arrives as `boundary_extension_all_refusal` instead, and
   * the per-finding proposals on the cards are unaffected either way. */
  boundary_extension_all?: GenesisBoundaryExtensionPatches;
  boundary_extension_all_refusal?: GenesisBoundaryExtensionRefusal;
}

export async function fetchGenesisJobFixes(jobId: string): Promise<GenesisJobFixes> {
  const r = await apiFetch(`/api/genesis/jobs/${jobId}/fixes`, { cache: "no-store" });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** One approved gate-finding fix -> the sibling fix job record (202). The
 * body's shape mirrors `ApplyGenesisFixRequest` in `routers/genesis.py`
 * exactly -- `approved_by` has no field here for the same reason it has none
 * there: the route stamps the signed-in session's own username, so the
 * client is structurally unable to name a different approver. `subject` is
 * optional ON THE WIRE (a legacy caller may omit it; the backend then refuses
 * a duplicated code by name rather than resolving it silently -- unit #30)
 * but REQUIRED here: this client always knows the finding's own subject, so
 * it is structurally unable to send an ambiguous approval. */
export async function applyGenesisFix(
  jobId: string,
  body: {
    finding_code: string;
    subject: string;
    verb: string;
    params: Record<string, unknown>;
    approval_rationale?: string;
    /** Unit #35: for a V3 world-gap finding, the operator's own attestation
     * that the real road continues, and to WHICH link. `attested_by` and
     * `attested_at` are deliberately absent here — the route stamps both from
     * the signed-in session and the server clock, so this client is
     * structurally unable to name a different attester. No attestation, no
     * proposal: sending this body without one is refused server-side. */
    attestation?: { to_link: string; basis: string };
  },
): Promise<GenesisJob> {
  const r = await apiMutationFetch(`/api/genesis/jobs/${jobId}/fixes/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** Unit #38: re-derive this generation from its own source through the
 * corrected converter -> the new job's record (202). Takes NO input beyond the
 * requester's own sentence, deliberately: every parameter a caller could send
 * would be a way to change the input while calling the result a treatment. The
 * source bytes, the options and the pipeline are the parent's own, and
 * `requested_by` is stamped from the session — this client is structurally
 * unable to name a different requester. */
export async function regenerateGenesisJob(jobId: string, reason?: string): Promise<GenesisJob> {
  const r = await apiMutationFetch(`/api/genesis/jobs/${jobId}/regenerate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason?.trim() || null }),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** Unit #42: record that a human CONFIRMED the road really ends here, and
 * re-derive this generation without that way -> the sibling job's record (202).
 *
 * Deliberately carries NO way id. The way is re-derived server-side from the
 * finding's own census record, so this client is structurally unable to widen
 * an exclusion past the road the confirming reader actually looked at — the
 * same discipline that keeps `attested_by` off `applyGenesisFix`'s body.
 * `confirmed_by` and the timestamp are likewise stamped from the session and the
 * server clock.
 *
 * `acknowledgedCollateralWayIds` is the ONE echo: the ways the reader was SHOWN
 * would also be cut loose (usually empty). The server re-derives the set and
 * refuses a mismatch, so a stale screen cannot smuggle an unseen severance past
 * the person signing for it. */
export async function confirmGenesisDeadEnd(
  jobId: string,
  body: {
    finding_code: string;
    subject: string;
    basis: string;
    acknowledged_collateral_way_ids: number[];
  },
): Promise<GenesisJob> {
  const r = await apiMutationFetch(`/api/genesis/jobs/${jobId}/dead-end-confirmation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

// ---------------------------------------------------------------------------
// Roadmap v2 §5.B B2 -- Region: draw an area, get the .osm the pipeline eats
// ---------------------------------------------------------------------------
//
// This block ENDS where the block above begins. A region fetch's only output is
// an `upload_token` -- the same token `uploadGenesisOsm` returns -- and the
// caller then calls `startGenesisJob` with it. There is deliberately no
// "fetch and generate" call: generation, gating and publishing stay on the one
// road B1 proved, and a second entry point into the gate is what §5.A A4 exists
// to prevent.
//
// Two of the four are pure arithmetic (`fetchRegionLimits`, `previewRegion`) and
// make no network call on the server either. They exist so the UI can show the
// area against the cap BEFORE anything is downloaded -- the caps refuse, they
// never truncate, so a user has to be able to see the line before crossing it.

/** The measurement `GET /api/genesis/region/preview` answers with — every field
 * a measured quantity or a derived limit, none of them an opinion.
 *
 * `estimated_bytes` is explicitly an ESTIMATE (mean measured OSM density × the
 * area). The byte cap is enforced against the bytes that actually arrive, never
 * against this number, and the UI must say so — an estimate presented as a
 * guarantee is how a "it will fit" turns into a refusal nobody predicted. */
/** A lasso, as WGS84 `[lat, lon]` corners. The ring may be sent open or closed —
 * the backend stores it open, which is also the convention Overpass's own
 * `poly:` filter uses. */
export type RegionPolygonVertices = Array<[number, number]>;

/** Either shape a region may be. Exactly one, never both: a polygon's bounding
 * box is DERIVED from the polygon by the backend, and a second box computed here
 * could disagree with the one the fetch is actually judged against. */
export type RegionShape =
  | { south: number; west: number; north: number; east: number }
  | { polygon: RegionPolygonVertices };

export interface RegionMeasurement {
  /** For a polygon this is the DERIVED bounding box — the rectangle the trim and
   * the boundary ladder will be held to, which is not the shape that was drawn.
   * The page draws it as a dashed rectangle and says so. */
  region: { south: number; west: number; north: number; east: number };
  /** Present only when a polygon was measured. */
  polygon?: { vertices: RegionPolygonVertices };
  polygon_vertices?: number;
  /** The area `estimated_bytes` was taken over. For a polygon this is the
   * BOUNDING BOX's area, deliberately larger than `area_km2`: Overpass answers
   * with whole ways and their recursed nodes, so the response is not
   * proportional to the ring's own area. */
  estimate_area_km2?: number;
  centre_lat: number;
  centre_lon: number;
  width_m: number;
  height_m: number;
  half_extent_m: number;
  area_km2: number;
  /** The draft projection's own validity radius at this centre latitude —
   * `services/genesis/project.py`. Past it the generator refuses outright, so
   * this is the same line twice, not a second opinion. */
  validity_radius_m: number;
  max_area_km2: number;
  /** Which physics sets the radius: `meridian_convergence` above |φ| = 14.04°,
   * `tangent_plane_curvature` below it. */
  binding_term: string;
  planimetric_error_bound_m: number;
  within_region_cap: boolean;
  estimated_bytes: number;
  max_response_bytes: number;
  estimate_basis: string;
}

export interface RegionLimits {
  centre_lat: number;
  centre_lon: number;
  validity_radius_m: number;
  max_area_km2: number;
  binding_term: string;
  planimetric_budget_m: number;
  max_response_bytes: number;
  reference_bytes_per_km2: number;
  peak_bytes_per_km2: number;
  /** The corner cap on a lasso, SERVED rather than duplicated here — a limit the
   * browser carried its own copy of is a limit that can disagree with the one
   * that actually refuses. */
  max_polygon_vertices: number;
  /** Which Overpass instance the SERVER is configured to use. Read-only on
   * purpose: a caller-supplied endpoint would make the fetch route an SSRF
   * instrument pointed at whatever the backend can reach. */
  endpoint: string;
  query_timeout_s: number;
  socket_timeout_s: number;
}

/** `staged` is the only success state and it means exactly "B1 can take it from
 * here". `refused` is a NAMED answer (a cap, a rate limit, a region with no
 * roads); `error` is the unpredicted kind, kept separate so "we said no, and
 * why" never reads like "we fell over". */
export type RegionFetchStatus = "queued" | "running" | "staged" | "refused" | "error";

/** Where this extract came from, recorded with it rather than beside it — the
 * same record is written INTO the `.osm` as a leading XML comment, so it
 * survives into the generation job's own source document. */
export interface RegionFetchProvenance {
  source: string;
  endpoint: string;
  query: string;
  /** ALWAYS the rectangle, polygon or not: this is the key every downstream
   * reader asks "which rectangle is the study area?", and for a lasso the honest
   * answer is the lasso's bounding box, because that is what the trim and the
   * boundary ladder actually judge against. */
  requested_region: { south: number; west: number; north: number; east: number };
  /** The lasso itself, recorded beside the rectangle. Absent — not null — on a
   * rectangle fetch, whose provenance is byte-identical to what it was before
   * polygons existed. */
  requested_polygon?: { vertices: RegionPolygonVertices };
  requested_at: string;
  fetched_at: string;
  elapsed_ms: number;
  attempts: number;
  bytes: number;
  sha256: string;
  overpass_generator: string | null;
  osm_base: string | null;
  ways: number;
  nodes: number;
  licence: string;
  /** Measured, not asserted: whether the instance restated the ODbL note. An
   * absent note is reported as absent rather than replaced with one we wrote. */
  attribution_note_present: boolean;
  attribution_note: string | null;
}

export interface RegionFetch {
  id: string;
  workspace_id: string;
  name: string;
  status: RegionFetchStatus;
  phase: string;
  progress: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  submitted_by: string | null;
  /** The rectangle in BOTH cases — drawn for a box fetch, derived for a lasso —
   * so every reader that had one keeps working. `polygon` is what says which. */
  region: { south: number; west: number; north: number; east: number };
  polygon: { vertices: RegionPolygonVertices } | null;
  measurement: RegionMeasurement;
  endpoint: string;
  /** The hand-off. Non-null exactly when `status === "staged"`. */
  upload_token: string | null;
  filename: string | null;
  bytes: number | null;
  provenance: RegionFetchProvenance | null;
  refusal: { code: string; reasons: string[]; detail: Record<string, unknown> } | null;
  error: string | null;
}

/** A `GenesisError` that also carries the MEASUREMENT behind the refusal.
 *
 * "This region is too large" without the half-extent, the limit and which term
 * binds is not an actionable refusal — the user cannot tell whether to shave a
 * street off or split the job in two. */
export class RegionFetchError extends GenesisError {
  measurement: Record<string, unknown>;

  constructor(base: GenesisError, measurement: Record<string, unknown>) {
    super(base.message, base.status, base.code, base.reasons);
    this.name = "RegionFetchError";
    this.measurement = measurement;
  }
}

async function regionError(response: Response): Promise<GenesisError> {
  // Read the body ONCE: `genesisError` consumes it, so the measurement has to
  // be pulled from the same clone rather than from a second `response.text()`
  // that would come back empty.
  const clone = response.clone();
  const base = await genesisError(response);
  try {
    const data = (await clone.json()) as { detail?: { measurement?: Record<string, unknown> } };
    const measurement = data?.detail?.measurement;
    if (measurement && typeof measurement === "object") return new RegionFetchError(base, measurement);
  } catch {
    // No structured measurement -- the plain refusal is still the answer.
  }
  return base;
}

export async function fetchRegionLimits(lat: number, lon: number): Promise<RegionLimits> {
  const query = `lat=${encodeURIComponent(String(lat))}&lon=${encodeURIComponent(String(lon))}`;
  const r = await apiFetch(`/api/genesis/region/limits?${query}`, { cache: "no-store" });
  if (!r.ok) throw await regionError(r);
  return r.json();
}

/** Measure either shape on the SERVER. Still a GET — a polygon travels as
 * `lat,lon;lat,lon;…`, which is the format the backend's refusals name literally
 * — so the preview stays open, cacheable and free of side effects. */
export async function previewRegion(shape: RegionShape): Promise<RegionMeasurement> {
  const query =
    "polygon" in shape
      ? new URLSearchParams({
          polygon: shape.polygon.map(([lat, lon]) => `${lat},${lon}`).join(";"),
        })
      : new URLSearchParams({
          south: String(shape.south),
          west: String(shape.west),
          north: String(shape.north),
          east: String(shape.east),
        });
  const r = await apiFetch(`/api/genesis/region/preview?${query.toString()}`, { cache: "no-store" });
  if (!r.ok) throw await regionError(r);
  return r.json();
}

export async function startRegionFetch(payload: RegionShape & { name?: string }): Promise<RegionFetch> {
  const r = await apiMutationFetch("/api/genesis/region/fetches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await regionError(r);
  return r.json();
}

export async function fetchRegionFetches(): Promise<RegionFetch[]> {
  const r = await apiFetch("/api/genesis/region/fetches", { cache: "no-store" });
  if (!r.ok) throw await regionError(r);
  const payload = (await r.json()) as { fetches: RegionFetch[] };
  return payload.fetches;
}

export async function fetchRegionFetch(fetchId: string): Promise<RegionFetch> {
  const r = await apiFetch(`/api/genesis/region/fetches/${fetchId}`, { cache: "no-store" });
  if (!r.ok) throw await regionError(r);
  return r.json();
}

// ---------------------------------------------------------------------------
// Admin: accounts and project membership (unit P④ — backend/routers/admin.py)
// ---------------------------------------------------------------------------
//
// Two properties make this block different from every other family above, and
// both are structural rather than conventional:
//
// 1. **These calls go through `authFetch`, not `apiFetch`.** Administration is
//    GLOBAL, not scoped to a 프로젝트, and `authFetch` is the one path that
//    DELETES `X-Workspace-Id` (see `noWorkspaceRequestInit`). Without that, a
//    browser holding a stale or forbidden workspace id in localStorage would
//    get a workspace 404/403 from `main.WorkspaceScopeMiddleware` before the
//    admin route was ever reached — i.e. the page for fixing project access
//    would be unreachable to anyone whose project access is broken.
// 2. **Failures arrive as `AuthRequestError`, carrying the status.** The admin
//    page has to tell 401 (sign in) from 403 (signed in, not an admin) from a
//    server fault, and it must not do that by string-matching a localized
//    detail — same reasoning as the login page's.
//
// Nothing here caches a user list, a role, or anything else that would let the
// UI pre-judge an answer: the server stays the authority, exactly as
// components/AuthProvider.tsx states for the session itself.

/** `services/auth._public_user` plus the role vocabulary the server accepts,
 * so the role picker cannot drift from `services/auth.ROLES`. */
export interface AdminUserListResponse {
  users: AuthUser[];
  roles: string[];
}

// ---------------------------------------------------------------------------
// Roadmap C1-b -- 현시도 review: a machine's proposal, a human's confirmation
// ---------------------------------------------------------------------------
//
// The backend renders each 대전 sheet's 현시도 band and (offline) asks a vision
// model to read it. Nothing here generates a proposal: these calls READ what the
// offline tool produced and POST the one thing only a human can supply.
//
// Two shapes below are load-bearing rather than incidental.
// `confirmPhaseDiagram` sends no attester name -- the server takes it from the
// session, so a caller cannot put somebody else's name against a mapping. And
// `stagePhaseDiagramSignalsInput` returns a staged side-input TOKEN rather than
// a document: the `genesis.signals.v1` document is assembled server-side, so
// "an unconfirmed proposal is inert" stays a refusal this client cannot route
// around instead of a convention it is trusted to honour.

/** Where the sheet PRINTS one text run inside the band, as a fraction of the
 * rendered image. Measured off the PDF's text layer, not recovered from pixels. */
export type PhaseDiagramLabel = {
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

/** The provenance of one rendered band -- which file, which bytes, which
 * rectangle, at what scale, hashing to what. The attestation is keyed to
 * `image_sha256`, which is what makes a confirmation falsifiable. */
export type PhaseDiagramRender = {
  /** Which of the three input paths made this review (unit #52). Only
   * `pdf_band` HAS an image; the other two are null across every image field
   * and the screen names the absence instead of drawing a stand-in. */
  medium: "pdf_band" | "excel_upload" | "blank_entry";
  source_file: string;
  source_sha256: string;
  page_no: number | null;
  crop_pt: [number, number, number, number] | null;
  render_scale: number | null;
  image_sha256: string | null;
  image_width_px: number | null;
  image_height_px: number | null;
  renderer: string;
  renderer_version: string;
  rendered_at: string;
  labels: PhaseDiagramLabel[];
};

export type PhaseDiagramMovement = {
  approach_label: string;
  turn: "through" | "left" | "right" | "u_turn" | "pedestrian" | "unknown";
  note: string | null;
};

export type PhaseDiagramPhase = {
  phase_id: string;
  movements: PhaseDiagramMovement[];
  serves_pedestrian: boolean | null;
  /** Written by the model, in Korean, for anything it could NOT resolve. There
   * is deliberately no confidence score: a number invites a threshold, and a
   * threshold is how a low-confidence guess gets promoted to a reading. */
  ambiguity: string | null;
};

export type PhaseDiagramApproach = {
  label: string;
  /** Where the leg is DRAWN. Not a compass bearing -- the sketch states no north. */
  side: "top" | "right" | "bottom" | "left" | "unknown";
  note: string | null;
};

/** A READING. An artifact, never a fact -- no field here asserts correctness,
 * and no string in this UI may imply one does.
 *
 * Two sources. `vision` is a model reading a rendered band (`model` names it,
 * `image_sha256` binds it) -- still a proposal, and the screen labels it one.
 * `excel_upload` is a spreadsheet somebody filled in and sent: no model read
 * it, it has no image, and since unit #55 (docs/phase2/DECISIONS.md,
 * "2026-08-24 -- 사용자 결정: 현시도 화면은 엑셀 업로드로 통합 ... (#55)") it is no
 * longer a proposal awaiting a per-intersection click -- the upload itself IS
 * that person's own statement of the reading.
 *
 * `uploaded_by` therefore carries TWO things that must not be collapsed into
 * one: it is the authoring word for THIS reading (who read the sheet and typed
 * it in -- a claim this app makes about the record's origin, never about
 * whether it is correct), and it is still never the 확인자 of anything. The
 * word 확인 on this screen belongs exclusively to the BINDING -- the human act
 * of tying this reading's approaches to a generated draft's node and link ids
 * (MappingAttestation.attested_by), reached from each card's 네트워크 결박
 * section at signals-build time. `uploaded_by` must never render beside that
 * attested_by, and `attested_by` must never be filled from `uploaded_by`. */
export type PhaseDiagramProposal = {
  source: "vision" | "excel_upload";
  uploaded_by: string | null;
  outcome: "proposed" | "refused";
  refusal: { code: string; reason: string } | null;
  read_intersection_no: string | null;
  read_intersection_name: string | null;
  approaches: PhaseDiagramApproach[];
  phases: PhaseDiagramPhase[];
  notes: string[];
  model: string | null;
  image_sha256: string | null;
  source_file: string;
  proposed_at: string;
};

export type PhaseDiagramFinding = {
  code: string;
  /** `flag` = a human must look before confirming. `observation` = a measurement
   * offered as corroboration, which is NOT evidence of correctness. `ok` = the
   * comparison ran and agreed. */
  severity: "flag" | "observation" | "ok";
  message: string;
  message_en: string;
  context: Record<string, unknown>;
};

/** `compared` is the SERVER's answer to "did the machine hold anything up
 * against anything?" — `services/genesis/phase_diagram.cross_check_compared`.
 *
 * It is FALSE only for the media that have neither a sheet text layer nor a
 * rendered band (엑셀 업로드 / 직접 입력), where the whole section could say
 * nothing but "대조할 시트 텍스트도 도면 이미지도 없습니다" — noise the user
 * struck out (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도 카드
 * 단순화 — 도식+시간표, 짧은 확인 (#58)"). A vision review keeps its cross-check
 * exactly as it was, flags included: deleting a comparison that HAPPENED would
 * be hiding evidence. The screen renders this answer; it never computes one. */
export type PhaseDiagramCrossCheck = {
  checked_at: string;
  findings: PhaseDiagramFinding[];
  compared: boolean;
};

/** A LOST amendment history, disclosed rather than healed.
 *
 * `null` when the ledger is healthy. When a review's `amendments.json` did not
 * parse, the server quarantined its bytes and started a fresh ledger — and says
 * so here, in all three languages, composed server-side
 * (`phase_diagram.amendment_quarantine_payload`) because the sentence states
 * what a machine did to a file. The screen prints its own heading beside this
 * text and never rewrites it. The flag is STICKY: later amendments append to a
 * history that is still missing what was lost, so the disclosure outlives them. */
export type PhaseDiagramAmendmentQuarantine = {
  quarantined: true;
  quarantine_path: string | null;
  quarantined_at: string | null;
  message: { ko: string; en: string; ja: string };
};

/** One 현시's typed timing, exactly as `phase_template.ParsedPhaseTiming` wrote
 * it. `min_green_s`/`max_green_s` are `null` when the cells were empty, which is
 * a different statement from zero — an actuated controller prints them and a
 * fixed one does not, and the card renders a column only where the value is. */
export type PhaseDiagramPhaseTiming = {
  phase_id: string;
  green_s: number;
  clearance_s: number;
  min_green_s: number | null;
  max_green_s: number | null;
};

/** One (수준, 계획 ID)'s timing. `cycle_s` is `null` when the 주기 cell was left
 * empty (the converter derives it as Σ(green+소거) and says so in the program's
 * provenance); `offset_s` is carried and never applied. */
export type PhaseDiagramPlanTiming = {
  level_no: number;
  plan_id: number;
  cycle_s: number | null;
  offset_s: number | null;
  time_of_day: string | null;
  phases: PhaseDiagramPhaseTiming[];
};

/** Which single value one amendment changes. The same closed vocabulary the
 * server's `AmendableField` declares — a field outside it is not amendable at
 * all, and the request is refused rather than accepted-and-ignored. */
export type PhaseDiagramTimingField =
  | "declared_amber_s"
  | "cycle_s"
  | "offset_s"
  | "time_of_day"
  | "green_s"
  | "clearance_s"
  | "min_green_s"
  | "max_green_s";

/** A named human's claim that this approach-to-phase mapping is the site's.
 * `attested_by`/`attested_at` come from the session and the server clock. */
export type PhaseDiagramAttestation = {
  attested_by: string;
  attested_at: string;
  source_file: string;
  source_sha256: string;
  /** `null` exactly when the review HAS no image. The server compares this
   * against the render on disk either way, so a confirmation can neither drop
   * an image that exists nor claim one that does not. */
  image_sha256: string | null;
  proposal_sha256: string | null;
  proposal_outcome: "proposed" | "refused" | "none";
  node_id: number;
  approach_links: Record<string, string>;
  approach_phases: Record<string, string>;
  level_no: number;
  plan_id: number;
  declared_amber_s: number | null;
  amber_declaration_note: string | null;
  changes: { field: string; was: string | null; now: string | null }[];
  note: string | null;
};

export type PhaseDiagramSheet = {
  source_file: string;
  intersection_name: string | null;
  intersection_no: string | null;
  /** Whether a 대전 sheet's TEXT LAYER produced this. `false` for the #52 media,
   * which have no sheet to parse -- what they carry is what somebody typed. */
  parsed: boolean;
  refusal_codes: string[];
  phase_ids: string[];
  /** Empty for a review with no 대전 TOD table. The confirm form then ASKS for
   * 수준/계획 ID instead of offering a choice nobody measured. */
  plan_choices: { level_no: number; plan_id: number }[];
  /** The 황색시간 this review currently declares. TWO possible authors, and
   * `amber_declared_by` says which: a person who typed it into the card's 신호
   * 시간 table (unit #58 — their word outranks the workbook's, because they
   * amended their own record after the fact), or the workbook itself. `null`
   * on both counts means nobody has declared one — the 대전 sheet prints
   * 황색+전적색 as ONE number and never the amber alone, so no parse can fill it. */
  declared_amber_s: number | null;
  /** The 근거. On the amended path this sentence is composed SERVER-SIDE
   * (`amber_declaration_sentence`) and names who declared the number, when, and
   * what it replaced — a provenance claim the browser does not get to author. */
  amber_declaration_note: string | null;
  amber_declared_by: string | null;
  amber_declared_at: string | null;
  /** The 신호 시간 this review HOLDS, per (수준, 계획 ID) — the same numbers the
   * export writes and the signals build converts, never a second derivation.
   * Empty is a real state: the mapping is still confirmable and the signals
   * build refuses BY NAME until timing exists. */
  timing: PhaseDiagramPlanTiming[];
  /** `null` when this review's amendment ledger is intact. */
  amendments_quarantine: PhaseDiagramAmendmentQuarantine | null;
};

export type PhaseDiagramReview = {
  review_id: string;
  source_file: string;
  render: PhaseDiagramRender;
  /** `null` is a real state, never an absent key: a refused or missing proposal
   * is the SAME screen with a blank form (roadmap C1 option (b)). */
  proposal: PhaseDiagramProposal | null;
  proposal_sha256: string | null;
  cross_check: PhaseDiagramCrossCheck | null;
  attestation: PhaseDiagramAttestation | null;
  /** Non-empty when a confirmation exists but was made against a different sheet
   * or a different render -- 재확인 필요, never a silent reuse. */
  stale_reasons: string[];
  confirmed: boolean;
  sheet: PhaseDiagramSheet;
};

export type PhaseDiagramSummary = {
  review_id: string;
  source_file: string;
  medium: "pdf_band" | "excel_upload" | "blank_entry";
  /** What the review is ABOUT, when the store knows it: the sheet's parsed
   * 교차로명, the uploaded row's, or the name a human typed. `null` falls back
   * to the file name in the list. */
  intersection_name: string | null;
  /** The DRAFT node a confirmation bound, or null. Not a Vissim node id -- the
   * confirm form binds against a generation's own draft, which is a different
   * id space from /network's active dataset. */
  node_id: number | null;
  image_sha256: string | null;
  proposal_source: "vision" | "excel_upload" | null;
  proposal_outcome: "proposed" | "refused" | null;
  proposal_refusal_code: string | null;
  flags: string[];
  confirmed: boolean;
  attested_by: string | null;
  attested_at: string | null;
  stale_reasons: string[];
};

export type PhaseDiagramApproachCandidate = {
  link_id: number;
  name: string;
  lanes: number;
  heading_deg: number | null;
  from_compass: string | null;
};

export type PhaseDiagramNodeCandidate = {
  node_id: number;
  name: string;
  x: number;
  y: number;
  approaches: PhaseDiagramApproachCandidate[];
  match_score: number;
  /** Why this node ranked where it did, in Korean. A rank with no reason is an
   * unexplained verdict, and the binding is the human's to make. */
  match_reason: string;
};

export async function fetchPhaseDiagrams(): Promise<PhaseDiagramSummary[]> {
  const r = await apiFetch("/api/genesis/phase-diagrams", { cache: "no-store" });
  if (!r.ok) throw await genesisError(r);
  const payload = (await r.json()) as { reviews: PhaseDiagramSummary[] };
  return payload.reviews;
}

export async function fetchPhaseDiagram(reviewId: string): Promise<PhaseDiagramReview> {
  const r = await apiFetch(`/api/genesis/phase-diagrams/${reviewId}`, { cache: "no-store" });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** The rendered band. Authenticated like every route in this sub-tree -- it is
 * client-confidential sheet material -- and reached with a plain `<img src>`
 * because the session rides on a same-site cookie. */
export function phaseDiagramImageUrl(reviewId: string): string {
  return apiUrl(`/api/genesis/phase-diagrams/${reviewId}/image`);
}

export async function fetchPhaseDiagramCandidates(
  reviewId: string,
  jobId: string,
): Promise<{
  intersection_name: string | null;
  candidates: PhaseDiagramNodeCandidate[];
  /** unit #62 -- ADDITIVE. The draft's own node count, so a truncation (the
   * name signal discriminated and `limit` cut the list) can be told apart
   * from the honest "every node offered" case where the two numbers match. */
  total_nodes: number;
  returned_nodes: number;
}> {
  const r = await apiFetch(
    `/api/genesis/phase-diagrams/${reviewId}/network-candidates?job_id=${encodeURIComponent(jobId)}`,
    { cache: "no-store" },
  );
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

export type PhaseDiagramConfirmation = {
  node_id: number;
  approach_phases: Record<string, string>;
  approach_links: Record<string, string>;
  level_no: number;
  plan_id: number;
  /** The image the human looked at, or `null` for a review that has none. Sent
   * verbatim from `review.render.image_sha256` -- never synthesised. */
  image_sha256: string | null;
  source_sha256: string;
  proposal_sha256: string | null;
  proposal_outcome: "proposed" | "refused" | "none";
  declared_amber_s: number | null;
  amber_declaration_note: string | null;
  changes: { field: string; was: string | null; now: string | null }[];
  note: string | null;
};

/** Record ONE human's attestation. There is deliberately no `attested_by` field
 * on `PhaseDiagramConfirmation`: the server takes the attester from the session,
 * so a caller cannot put somebody else's name against a mapping. */
export async function confirmPhaseDiagram(
  reviewId: string,
  payload: PhaseDiagramConfirmation,
): Promise<PhaseDiagramReview> {
  const r = await apiMutationFetch(`/api/genesis/phase-diagrams/${reviewId}/confirmation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** Correct ONE stored 신호 시간 value — the uploader amending their own record.
 *
 * THE USER'S DECISION (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도
 * 카드 단순화 — 도식+시간표, 짧은 확인 (#58)"): "황색시간 및 시간 같은거가 수정
 * 가능하게". There is deliberately no `edited_by` and no `edited_at` on this
 * payload, for the same reason `PhaseDiagramConfirmation` carries no attester:
 * the stamp says who changed a number and when, and a browser that could write
 * it could write somebody else's name. The server takes both from the session
 * and its own clock, and keeps the previous value in the review's ledger.
 *
 * Returns the WHOLE review as the server now holds it, so the card re-renders
 * from stored data rather than from what the browser hoped it wrote. */
export async function amendPhaseDiagramTiming(
  reviewId: string,
  payload: {
    field: PhaseDiagramTimingField;
    level_no?: number | null;
    plan_id?: number | null;
    phase_id?: string | null;
    value: number | string | null;
  },
): Promise<PhaseDiagramReview> {
  const r = await apiMutationFetch(`/api/genesis/phase-diagrams/${reviewId}/timing`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

// ---------------------------------------------------------------------------
// Unit #52 -- 현시도 입력: a template out, a workbook in, a blank one made here
// ---------------------------------------------------------------------------
//
// Three doors, one destination. Every one of them produces a PROPOSAL (or an
// empty form) and none of them writes an attestation: the only road to a
// confirmed mapping is still `confirmPhaseDiagram` above, with the attester
// taken from the session. The three download URLs are plain hrefs for the same
// reason `phaseDiagramImageUrl` is -- the session rides on a same-site cookie,
// so an <a> works and a fetch+blob would only add a copy of the file in memory.

/** The blank standard template. The 안내 sheet states the doctrine INSIDE the
 * file, because the file travels without this app around it. */
export function phaseDiagramTemplateUrl(): string {
  return apiUrl("/api/genesis/phase-diagrams/template.xlsx");
}

/** One review's proposal as a filled draft, every sheet stamped 미확인. */
export function phaseDiagramExportUrl(reviewId: string): string {
  return apiUrl(`/api/genesis/phase-diagrams/${reviewId}/export.xlsx`);
}

/** Every review in this workspace as one draft workbook, same stamp. */
export function phaseDiagramExportsUrl(): string {
  return apiUrl("/api/genesis/phase-diagrams/exports.xlsx");
}

/** One row the upload created, or refused. A refusal names its Excel row
 * number, because "fix row 14" is an instruction and "the file was bad" is not. */
export type PhaseDiagramUploadRefusal = {
  code: string;
  reasons: string[];
  row: number | null;
  intersection: string | null;
};

export type PhaseDiagramUploadResult = {
  source_file: string;
  uploaded_by: string;
  created: {
    review_id: string;
    intersection_name: string;
    intersection_no: string | null;
    phase_ids: string[];
    declared_amber_s: number | null;
    amber_declaration_note: string | null;
    /** The (수준, 계획ID) pairs the 신호시간 sheet carried timing for. EMPTY is a
     * real state, and a consequential one: the mapping can still be confirmed,
     * but no signals input can be built until timing exists. */
    plans: { level_no: number; plan_id: number }[];
    replaced: boolean;
  }[];
  refusals: PhaseDiagramUploadRefusal[];
  skipped_example_rows: number[];
};

/** Upload one filled-in template. Raw body + `?filename=`, the shape
 * `uploadObservationWorkbook` uses and for the same reason (this backend
 * carries no `python-multipart`). The response reports what was created AND
 * every row that refused -- a bad row is never silently dropped. */
export async function uploadPhaseDiagramTemplate(file: File): Promise<PhaseDiagramUploadResult> {
  const path = `/api/genesis/phase-diagrams/upload?filename=${encodeURIComponent(file.name)}`;
  const r = await apiMutationFetch(path, { method: "POST", cache: "no-store", body: file });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** Create an empty review for one named intersection -- roadmap C1 option (b),
 * reachable from the screen at last. No proposal, no image: it opens straight
 * into the blank manual-entry form. `originNote` is provenance only (e.g. which
 * node on the network diagram the reader started from) and binds nothing. */
export async function createBlankPhaseDiagram(
  intersectionName: string,
  options?: { intersectionNo?: string | null; originNote?: string | null },
): Promise<PhaseDiagramReview> {
  const r = await apiMutationFetch("/api/genesis/phase-diagrams/blank", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      intersection_name: intersectionName,
      intersection_no: options?.intersectionNo ?? null,
      origin_note: options?.originNote ?? null,
    }),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** Confirmed reviews to a staged `signals` side input, ready for
 * `startGenesisJob`. The document is assembled on the server; an unconfirmed or
 * stale review is a 409 naming it, never a silent skip. */
export async function stagePhaseDiagramSignalsInput(
  reviewIds: string[],
  filename?: string,
): Promise<{ token: string; kind: string; summary: Record<string, unknown> }> {
  const r = await apiMutationFetch("/api/genesis/phase-diagrams/signals-input", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_ids: reviewIds, filename: filename ?? null }),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

// ---------------------------------------------------------------------------
// Demand side-input UI — a counts workbook, clicked all the way to a token
// ---------------------------------------------------------------------------
//
// Before this pair, `demand_token` on `startGenesisJob` could only be filled by
// hand-writing a `genesis.demand.v1` document and staging it with `curl`. Two
// calls close that gap and nothing else changes: `parseDemandCounts` stages ONE
// Daejeon counts workbook and returns what the adapter read (sites, legs,
// classes, refusals -- verbatim, never softened); `buildDemandInput` takes that
// token plus a browser-typed `approach_links` map and returns the SAME
// `{token, kind, filename, bytes, sha256, staged_at, summary}` shape
// `POST /side-inputs?kind=demand` does, because it IS that route under the
// hood -- the document is validated by the same `side_inputs.parse_side_input`
// server-side, never re-implemented here.

/** One structured finding from the counts adapter, verbatim -- the adapter's
 * own refusal semantics, never re-worded by this client. `severity` mirrors
 * `services.dataimport.base.IssueSeverity`. */
export interface DemandCountsIssue {
  severity: "refuse" | "warn" | "info";
  code: string;
  message: string;
  subject: string | null;
  context: Record<string, unknown>;
}

/** One site the workbook carries, with its legs VERBATIM (the survey's own
 * labels) -- these are the row keys a mapping table builds `approach_links`
 * from, and `generate_demand` compares against these exact strings. */
export interface DemandCountsSite {
  site_id: string;
  site_name: string | null;
  legs: string[];
}

export interface DemandCountsSummary {
  adapter: string;
  region: string;
  source_files: string[];
  counts: number;
  sites: DemandCountsSite[];
  vehicle_classes: string[];
  window: { start: string; end: string } | null;
  issues: DemandCountsIssue[];
  refusals: string[];
  ok: boolean;
}

export interface StagedDemandCounts {
  token: string;
  kind: "counts";
  filename: string;
  bytes: number;
  sha256: string;
  staged_at: string;
  summary: DemandCountsSummary;
}

/** `services.genesis.side_inputs.DemandSideInput.summary()`, verbatim -- the
 * SAME shape `POST /side-inputs?kind=demand` returns, because `buildDemandInput`
 * stages through that exact function. */
export interface DemandInputSummary {
  doc_kind: string;
  adapter: string;
  region: string;
  source_files: string[];
  counts: number;
  sites_in_counts: string[];
  sites_mapped: string[];
  approaches_mapped: number;
  vehicle_classes: string[];
  counts_refusals: string[];
  window: { start: string; end: string } | null;
}

export interface StagedDemandInput {
  token: string;
  kind: "demand";
  filename: string;
  bytes: number;
  sha256: string;
  staged_at: string;
  summary: DemandInputSummary;
}

/** Stage ONE Daejeon counts workbook (`.xlsx`). Runs
 * `DaejeonCountAdapter` server-side and stages the RESULT under its own
 * `counts` kind -- the token this returns is meaningful only to
 * `buildDemandInput`, never to `startGenesisJob` directly. */
export async function parseDemandCounts(file: File): Promise<StagedDemandCounts> {
  const path = `/api/genesis/demand-inputs/parse?filename=${encodeURIComponent(file.name)}`;
  const r = await apiMutationFetch(path, { method: "POST", cache: "no-store", body: file });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** Staged counts + a browser-typed `approach_links` join → a staged `demand`
 * side input. `approach_links` is site id → {leg id → draft link id}; an empty
 * site entry or a missing leg is never filled in here -- the caller (the
 * demand page) sends exactly what the reviewer typed, and an incomplete
 * mapping is refused by the SAME validator `POST /side-inputs` uses, with the
 * SAME reason `services.genesis.demand.generate_demand` would give at
 * generation time for an unknown site or leg.
 *
 * `built_against_job` is REQUIRED -- the generation whose gate map the
 * reviewer read link ids off. The server resolves that job's own draft,
 * fingerprints its link table, and stamps the fingerprint into the staged
 * document; `generate_demand` refuses at generation time if the target
 * draft's link numbering has since shifted (a bbox change, a re-extract --
 * draft link ids are a sequential counter reset on every build), instead of
 * silently attaching one approach's observed demand to a different link. */
export async function buildDemandInput(payload: {
  counts_token: string;
  approach_links: Record<string, Record<string, number>>;
  built_against_job: string;
  window_start?: string;
  window_end?: string;
  demand_settings?: Record<string, unknown>;
  fleet_settings?: Record<string, unknown>;
  filename?: string;
}): Promise<StagedDemandInput> {
  const r = await apiMutationFetch("/api/genesis/demand-inputs/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await genesisError(r);
  return r.json();
}

/** One entry of `GET /api/genesis/side-inputs` -- the picker data for the
 * job-start form's demand/signals dropdowns. `summary` is whichever shape its
 * `kind` produces (`DemandCountsSummary`, `DemandInputSummary`, or the signals
 * document's own summary); read narrowly by `kind` rather than typed as a
 * union, since a picker only needs `filename`/`staged_at` plus a couple of
 * summary fields it already knows the name of. */
export interface StagedSideInputEntry {
  token: string;
  kind: string;
  filename: string;
  bytes: number;
  sha256: string;
  staged_at: string;
  summary: Record<string, unknown>;
}

/** Every side input currently staged in this workspace, newest first --
 * AUTHENTICATED (see the backend router's own docstring: this lists client
 * data, not the record of a decision). */
export async function fetchStagedSideInputs(): Promise<StagedSideInputEntry[]> {
  const r = await apiFetch("/api/genesis/side-inputs", { cache: "no-store" });
  if (!r.ok) throw await genesisError(r);
  const payload = (await r.json()) as { side_inputs: StagedSideInputEntry[] };
  return payload.side_inputs;
}

// ---------------------------------------------------------------------------
// Observation store (unit #28) -- backend/routers/observations.py +
// backend/services/observations.py
// ---------------------------------------------------------------------------
//
// A field-count workbook, staged and gated into a stored SET that later feeds
// `run_reality_check`'s `observation_set_id` parameter as the OBSERVED side
// (unit U10) -- sets are leg|turn-keyed. Two-step upload for the same reason
// every other raw-body upload in this file is (this backend has no
// python-multipart dependency): `POST /upload` once per workbook (raw bytes,
// the filename in the query string), then `POST /ingest` with the returned
// tokens plus the modeller's declared periods.
//
// THE FILENAME IS KEPT VERBATIM ON PURPOSE, exactly like `uploadGenesisOsm`
// above -- the Daejeon adapter reads the site id and the declared collection
// window out of the file name (services/observations.py's module docstring),
// so this client must never rename or otherwise transform it. `file.name`
// goes straight into `encodeURIComponent` and nowhere else.
//
// A refusal (400 from `/upload`, 400/422 from `/ingest`) carries the reason
// AS DATA -- `ObservationRefusal.to_dict()` on the wire -- so it is typed as
// its own error class rather than a generic `Error`, the same shape
// `GenesisError` gives the genesis screens. Unlike `GenesisError` (which
// carries only an English `reasons[]` and needs `refusalHeadline` to
// translate the CODE client-side), the backend already sends the full ko/en/ja
// sentence in `.messages` here -- nothing here re-derives or rewords it. A 422
// specifically means the quality GATE refused and nothing was stored;
// `.detail.gate` then carries the findings verbatim (severity/code/message per
// issue), which the caller renders as a named refusal block, never a generic
// error toast.

/** `{ko, en, ja}` -- the shape every refusal message and the registry health
 * disclosure below carry, mirroring `HealthCheckLocalizedText` above. */
export interface ObservationLocalizedText {
  ko: string;
  en: string;
  ja: string;
}

export class ObservationRefusalError extends Error {
  status: number;
  /** The backend's stable reason code (`observation_gate_refused`,
   * `observation_upload_not_a_workbook`, `observation_periods_required`, ...). */
  code: string;
  messages: ObservationLocalizedText;
  /** Everything else `ObservationRefusal.to_dict()` sent beyond `reason_code`/
   * `message` -- for a gate refusal this holds `gate`/`source_files`/`adapter`;
   * for others it may carry `duplicates`, `known_adapters`, `sniff_scores`, or
   * nothing at all. Rendered verbatim, never re-derived client-side. */
  detail: Record<string, unknown>;

  constructor(status: number, code: string, messages: ObservationLocalizedText, detail: Record<string, unknown>) {
    super(messages.en || code);
    this.name = "ObservationRefusalError";
    this.status = status;
    this.code = code;
    this.messages = messages;
    this.detail = detail;
  }
}

async function observationError(response: Response): Promise<Error> {
  const text = await response.text();
  try {
    const data = JSON.parse(text) as { detail?: unknown };
    const detail = data.detail;
    if (detail && typeof detail === "object") {
      const structured = detail as { reason_code?: string; message?: ObservationLocalizedText; [key: string]: unknown };
      if (structured.reason_code && structured.message) {
        const { reason_code, message, ...rest } = structured;
        return new ObservationRefusalError(response.status, reason_code, message, rest);
      }
    }
    if (typeof detail === "string") return new Error(detail);
  } catch {
    // Plain text errors (a 409 registry conflict, a 404 stale token) are still
    // useful as-is.
  }
  return new Error(text || `${response.status} ${response.statusText}`.trim());
}

export interface StagedObservationUpload {
  token: string;
  filename: string;
  bytes: number;
}

/** Stage ONE observation-count workbook (`.xlsx`/`.xlsm`). `file.name` is
 * `encodeURIComponent`-ed and otherwise untouched -- see the module comment
 * above for why it must never be renamed. */
export async function uploadObservationWorkbook(file: File): Promise<StagedObservationUpload> {
  const path = `/api/observations/upload?filename=${encodeURIComponent(file.name)}`;
  const r = await apiMutationFetch(path, { method: "POST", cache: "no-store", body: file });
  if (!r.ok) throw await observationError(r);
  return r.json();
}

/** One declared window the ingest aggregates counts into. Naive local
 * timestamps only (no timezone suffix) -- a `datetime-local` input's own
 * value shape -- because the Daejeon export states no timezone at all
 * (`services.observations.ObservationPeriod`'s own docstring). */
export interface ObservationPeriodInput {
  name: string;
  start: string;
  end: string;
}

export interface ObservationGateIssue {
  severity: "refuse" | "warn" | "info";
  code: string;
  message: string;
  messages: Record<string, string>;
  subject: string | null;
  citation: string | null;
  context: Record<string, unknown>;
}

export interface ObservationGateSummary {
  counts: { refuse: number; warn: number; info: number };
  codes: string[];
  issues: ObservationGateIssue[];
  issues_truncated: number;
}

export interface ObservationAdapterInfo {
  name: string;
  version: string;
  region: string;
  source_system: string;
}

export interface ObservationCoverage {
  first_interval_start: string | null;
  last_interval_end: string | null;
  interval_minutes: number[];
  records_read: number;
  records_aggregated: number;
  records_excluded_as_totals: number;
  records_outside_every_period: number;
  detector_records_read: number;
  detector_records_aggregated: number;
  detector_records_outside_every_period: number;
}

export interface ObservationPeriodCoverage {
  name: string;
  start: string;
  end: string;
  site_count: number;
  sites_with_data: string[];
  sites_without_data: string[];
  total_volume: number;
  records: number;
  detector_total_volume: number;
  detector_records: number;
}

/** `GET /api/observations`' per-row shape -- a set WITHOUT its aggregates
 * (`services.observations.summarize`), which is why `ObservationSetRecord`
 * below is a wider, separate type rather than this one reused. */
export interface ObservationSetSummary {
  id: string;
  name: string;
  workspace_id: string;
  ingested_at: string;
  adapter: ObservationAdapterInfo;
  source_files: string[];
  site_count: number;
  site_ids: string[];
  coverage: ObservationCoverage;
  periods: ObservationPeriodCoverage[];
  gate: ObservationGateSummary;
}

export interface ObservationMovementAggregate {
  volume: number;
  records: number;
}

export interface ObservationSiteAggregate {
  site_id: string;
  site_name: string | null;
  total: number;
  records: number;
  interval_count: number;
  first_interval_start: string;
  last_interval_end: string;
  movements: Record<string, ObservationMovementAggregate>;
  detector_total: number | null;
  detector_records: number;
  detectors: Record<string, ObservationMovementAggregate>;
  count_basis: string[];
}

/** `GET /api/observations/{set_id}` -- the summary plus its aggregates,
 * period name -> site id -> `ObservationSiteAggregate`. */
export interface ObservationSetRecord extends ObservationSetSummary {
  aggregates: Record<string, Record<string, ObservationSiteAggregate>>;
}

/** `registry_health_status()`'s disclosure -- present only after a registry
 * quarantine event, `null` when healthy. */
export interface ObservationRegistryHealth {
  code: "registry_quarantined";
  at: string | null;
  quarantine_path: string | null;
  lost_record_count: number | null;
  quarantine_count: number;
  messages: ObservationLocalizedText;
}

export interface ObservationSetsResponse {
  sets: ObservationSetSummary[];
  registry_health: ObservationRegistryHealth | null;
}

export async function fetchObservationSets(): Promise<ObservationSetsResponse> {
  const r = await apiFetch("/api/observations", { cache: "no-store" });
  if (!r.ok) throw await observationError(r);
  return r.json();
}

export async function fetchObservationSet(setId: string): Promise<ObservationSetRecord> {
  const r = await apiFetch(`/api/observations/${encodeURIComponent(setId)}`, { cache: "no-store" });
  if (!r.ok) throw await observationError(r);
  return r.json();
}

/** `POST /api/observations/ingest` -- parse, gate and store one set from
 * already-staged tokens. A 422 means the quality gate REFUSED and nothing was
 * stored (`ObservationRefusalError.code === "observation_gate_refused"`,
 * `.detail.gate` carries the findings) -- the caller renders that as a named
 * refusal block, never a generic error. */
export async function ingestObservationSet(payload: {
  name: string;
  upload_tokens: string[];
  periods: ObservationPeriodInput[];
  adapter?: string;
  keep_uploads?: boolean;
}): Promise<ObservationSetRecord> {
  const r = await apiMutationFetch("/api/observations/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw await observationError(r);
  const data = (await r.json()) as { stored: true; set: ObservationSetRecord };
  return data.set;
}

export async function deleteObservationSet(setId: string): Promise<void> {
  const r = await apiMutationFetch(`/api/observations/${encodeURIComponent(setId)}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!r.ok) throw await observationError(r);
}

/** One row of `GET /api/admin/workspaces/{id}/members`
 * (`services/membership._public_member`). `role` is the USER's global role;
 * `membership_role` is their role INSIDE this project — deliberately separate
 * fields, because they answer different questions. */
export interface WorkspaceMember {
  user_id: number;
  username: string;
  display_name: string;
  role: string;
  disabled: boolean;
  membership_role: string;
  member_since: string;
}

/** `open_to_everyone` is true for the DEFAULT project, which is readable by
 * anonymous callers by design (services/membership.py's rule table). The UI
 * must say so rather than render an empty roster, which would tell the admin
 * the opposite of the truth.
 *
 * Grant and revoke answer with this SAME shape (plus `granted`/`revoked`), so
 * a mutation never leaves the screen re-deriving a roster from a partial
 * response. */
export interface WorkspaceMemberListResponse {
  workspace: WorkspaceSummary;
  members: WorkspaceMember[];
  open_to_everyone: boolean;
  granted?: unknown;
  revoked?: boolean;
}

async function adminRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(path, { cache: "no-store", ...(init ?? {}) });
  if (!r.ok) throw new AuthRequestError(await readApiError(r), r.status);
  return (await r.json()) as T;
}

function adminJsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export async function fetchAdminUsers(): Promise<AdminUserListResponse> {
  return adminRequest<AdminUserListResponse>("/api/admin/users");
}

/** The project list for the ADMIN screen — the same `GET /api/workspaces`,
 * fetched WITHOUT `X-Workspace-Id`.
 *
 * `fetchWorkspaces` above sends the selected project's id, which is right for
 * the picker and wrong here: the admin screen exists to repair project access,
 * and a stale or since-deleted id in localStorage would 404 the very request
 * that lists the projects the admin needs to fix. An absent header resolves to
 * the default project, which is open to everyone by design, and the response
 * is still filtered by who the caller is (an admin sees all).
 *
 * Typed through `AuthRequestError` (not `Error`) like the rest of this block,
 * so the page can route a 401 to the login screen. */
export async function fetchAdminWorkspaces(): Promise<WorkspaceListResponse> {
  return adminRequest<WorkspaceListResponse>("/api/workspaces");
}

/** The admin types the password. There is no generated secret and no invite
 * link anywhere in this product — see backend/services/auth.py's docstring. */
export async function createAdminUser(payload: {
  username: string;
  password: string;
  role: string;
  display_name?: string;
}): Promise<AuthUser> {
  return adminRequest<AuthUser>("/api/admin/users", adminJsonInit("POST", payload));
}

export async function setAdminUserPassword(userId: number, password: string): Promise<AuthUser> {
  return adminRequest<AuthUser>(`/api/admin/users/${userId}/password`, adminJsonInit("POST", { password }));
}

export async function setAdminUserDisabled(userId: number, disabled: boolean): Promise<AuthUser> {
  return adminRequest<AuthUser>(`/api/admin/users/${userId}/disabled`, adminJsonInit("POST", { disabled }));
}

export async function setAdminUserRole(userId: number, role: string): Promise<AuthUser> {
  return adminRequest<AuthUser>(`/api/admin/users/${userId}/role`, adminJsonInit("POST", { role }));
}

export async function fetchWorkspaceMembers(workspaceId: string): Promise<WorkspaceMemberListResponse> {
  return adminRequest<WorkspaceMemberListResponse>(`/api/admin/workspaces/${workspaceId}/members`);
}

export async function grantWorkspaceMember(
  workspaceId: string,
  userId: number,
): Promise<WorkspaceMemberListResponse> {
  return adminRequest<WorkspaceMemberListResponse>(
    `/api/admin/workspaces/${workspaceId}/members`,
    adminJsonInit("POST", { user_id: userId }),
  );
}

export async function revokeWorkspaceMember(
  workspaceId: string,
  userId: number,
): Promise<WorkspaceMemberListResponse> {
  return adminRequest<WorkspaceMemberListResponse>(
    `/api/admin/workspaces/${workspaceId}/members/${userId}`,
    { method: "DELETE" },
  );
}

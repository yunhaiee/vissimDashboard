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
  requires_uploaded_zip?: boolean;
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
export interface AgentApproval {
  approval_id: string;
  actions: AgentAction[];
  warnings: AgentWarning[];
  assumptions: string[];
  focus?: AgentApprovalFocus;
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

export async function fetchWorkspaces(): Promise<WorkspaceListResponse> {
  const r = await apiFetch("/api/workspaces", { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
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

export type GenesisJobStatus =
  | "queued"
  | "running"
  | "refused"
  | "provisional"
  | "published"
  | "error";

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
  // What this evidence RESTS ON. It arrived over HTTP, so the app cannot prove
  // Vissim ever ran -- a caller who knows the artifact can hand-write
  // `load_ok: true` about it. The backend therefore records it as an operator's
  // attestation, with a name, and the UI says so. A V1 that went green this way
  // must never read like one the app watched happen.
  basis: string;
  attested_by: string | null;
  attested_at: string | null;
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
  // than because this process observed it.
  rests_on_attestation: boolean;
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
 * `GenesisJob.id`, which is the fix job's own id. */
export interface GenesisFixLineage {
  job_id: string;
  finding_code: string;
  verb: string;
  params: Record<string, unknown>;
  approved_by: string | null;
  approval_rationale: string | null;
  approved_at: string;
  parent_sha256: string;
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
  source_osm: { filename: string; bytes: number; sha256: string };
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
  /** See `GenesisFixLineage` -- absent means "an ordinary generation", not "unknown". */
  derived_from?: GenesisFixLineage;
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
export interface GenesisFixDisposition {
  status: string;
  verb: string | null;
  params: Record<string, unknown> | null;
  requires: string[];
  confidence: string;
  assumptions: string[];
  rationale: { ko: string; en: string; ja: string };
  reason_code: string | null;
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
 * client is structurally unable to name a different approver. */
export async function applyGenesisFix(
  jobId: string,
  body: { finding_code: string; verb: string; params: Record<string, unknown>; approval_rationale?: string },
): Promise<GenesisJob> {
  const r = await apiMutationFetch(`/api/genesis/jobs/${jobId}/fixes/apply`, {
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
  source_file: string;
  source_sha256: string;
  page_no: number;
  crop_pt: [number, number, number, number];
  render_scale: number;
  image_sha256: string;
  image_width_px: number;
  image_height_px: number;
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

/** A machine's READING. An artifact, never a fact -- no field here asserts
 * correctness, and no string in this UI may imply one does. */
export type PhaseDiagramProposal = {
  outcome: "proposed" | "refused";
  refusal: { code: string; reason: string } | null;
  read_intersection_no: string | null;
  read_intersection_name: string | null;
  approaches: PhaseDiagramApproach[];
  phases: PhaseDiagramPhase[];
  notes: string[];
  model: string;
  image_sha256: string;
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

export type PhaseDiagramCrossCheck = { checked_at: string; findings: PhaseDiagramFinding[] };

/** A named human's claim that this approach-to-phase mapping is the site's.
 * `attested_by`/`attested_at` come from the session and the server clock. */
export type PhaseDiagramAttestation = {
  attested_by: string;
  attested_at: string;
  source_file: string;
  source_sha256: string;
  image_sha256: string;
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
  parsed: boolean;
  refusal_codes: string[];
  phase_ids: string[];
  plan_choices: { level_no: number; plan_id: number }[];
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
  image_sha256: string;
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
): Promise<{ intersection_name: string | null; candidates: PhaseDiagramNodeCandidate[] }> {
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
  image_sha256: string;
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

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

function requestInitForBase(base: string, init?: RequestInit): LocalNetworkRequestInit {
  const nextInit = { ...(init ?? {}) } as LocalNetworkRequestInit;
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
  return { ...(init ?? {}) };
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

export interface NetworkData {
  links: { id: string; name: string; points: [number, number][]; lanes: number; length: number }[];
  nodes: { id: string; name: string; x: number; y: number; polygon: [number, number][] }[];
  detectors: { id: string; name: string }[];
  traveltime_sections: { id: string; name: string }[];
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

function sleepForRetry(ms: number): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms);
  });
}

export async function fetchNetwork(): Promise<NetworkData> {
  const r = await apiFetch("/api/network", { cache: "no-store" });
  if (!r.ok) throw new Error(await readApiError(r));
  return r.json();
}

export async function fetchActiveDataset(): Promise<ActiveDataset> {
  const r = await apiFetch("/api/datasets/active", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
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
}

export async function fetchBaseWorkbook(): Promise<BaseWorkbookResponse> {
  const r = await apiFetch("/api/validation/base-workbook", { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export type ReportBlock = { type: string; heading?: string; body?: string };
export type ReportMeta = { title?: string; subtitle?: string; author?: string; date?: string; project?: string; revision?: string; reviewer?: string; description?: string };
export type ReportSpec = { meta: ReportMeta; blocks: ReportBlock[]; filename?: string };

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
      const r = await fetch(path, {
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

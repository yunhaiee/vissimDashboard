"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import { useLanguage } from "@/components/LanguageProvider";
import { failed, ok, resolve, type Loaded } from "@/lib/asyncResource";
import MapViewer, {
  type Incident as MapIncident,
  type LinkFeature,
  type NodeConnection,
  type NodeFeature,
  type QueuedLinkMode,
} from "@/components/MapViewer";
import {
  createSimulationWS,
  DEFAULT_BASE,
  fetchActiveDataset,
  fetchScenarios,
  fetchSignals,
  fetchSimulationCapabilities,
  fetchSimulationJob,
  fetchSimulationJobs,
  getApiBase,
  resetActiveDataset,
  setApiBaseOverride,
  startSimulation,
  uploadInpxDataset,
  type ActiveDataset,
  type ScenarioMeta,
  type SignalNodeInfo,
  type SimulationCapabilities,
  type SimulationIncident,
  type SimulationJob,
  type SimulationStatusMessage,
} from "@/lib/api";

type EditMode = "speed" | "volume" | "signal";
type RunState = "idle" | "running" | "done" | "error";

type VehicleInputRow = {
  id: string;
  linkId: string;
  volume: string;
};

type SignalTimingRow = {
  id: string;
  controllerId: string;
  nodeId: string;
  nodeName: string;
  cycle: string;
  offset: string;
};

type SpeedLimitRow = {
  id: string;
  linkId: string;
  speedLimit: string;
  duration: string;
};

const makeRowId = () => Math.random().toString(36).slice(2, 10);

// #72: an INPX-only dataset has a structurally empty scenario list (no
// project DB), which is a valid, common state -- not an error. Distinguishing
// it from "the fetch itself failed" needs a resource that can tell "resolved
// to nothing" apart from "never resolved", which a plain `useState([])`
// cannot (both start and end at `[]`). Same `Loaded<T>` idiom app/calibration/
// page.tsx already uses for its own scenario list (#65); see lib/asyncResource.ts.
const SCENARIOS_KEY = "scenarios";
const NO_SCENARIOS: ScenarioMeta[] = [];

export default function SimulationPage() {
  const { t } = useLanguage();
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [dataset, setDataset] = useState<ActiveDataset | null>(null);
  const [scenariosLoaded, setScenariosLoaded] = useState<Loaded<ScenarioMeta[]> | null>(null);
  const {
    data: scenariosData,
    error: scenariosError,
    loading: scenariosLoading,
  } = resolve(scenariosLoaded, SCENARIOS_KEY);
  const scenarios = scenariosData ?? NO_SCENARIOS;
  // The one fact the picker needs: the fetch RESOLVED, it did not FAIL, and
  // what came back was empty. Loading and error are both false here on
  // purpose -- neither is evidence of "zero scenarios in this dataset".
  const scenariosEmpty = !scenariosLoading && !scenariosError && scenarios.length === 0;
  const [signals, setSignals] = useState<SignalNodeInfo[]>([]);
  const [capabilities, setCapabilities] = useState<SimulationCapabilities | null>(null);
  const [jobs, setJobs] = useState<SimulationJob[]>([]);
  const [apiBase, setApiBase] = useState(DEFAULT_BASE);
  const [apiBaseDraft, setApiBaseDraft] = useState(DEFAULT_BASE);
  const [selectedScenarioId, setSelectedScenarioId] = useState("");
  const [runName, setRunName] = useState("Simulation run");
  const [duration, setDuration] = useState(3600);
  const [seed, setSeed] = useState(42);
  const [editMode, setEditMode] = useState<EditMode>("speed");
  const [selectedLink, setSelectedLink] = useState<LinkFeature | null>(null);
  const [selectedNode, setSelectedNode] = useState<NodeFeature | null>(null);
  const [vehicleInputs, setVehicleInputs] = useState<VehicleInputRow[]>([]);
  const [signalTimings, setSignalTimings] = useState<SignalTimingRow[]>([]);
  const [speedLimits, setSpeedLimits] = useState<SpeedLimitRow[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeProgress, setActiveProgress] = useState(0);
  const [activeStatus, setActiveStatus] = useState<RunState>("idle");
  const [loading, setLoading] = useState(true);
  const [uploadingZip, setUploadingZip] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const loadBaseData = useCallback(async () => {
    setLoading(true);
    const [datasetResult, scenarioResult, signalResult, jobResult, capabilityResult] = await Promise.allSettled([
      fetchActiveDataset(),
      fetchScenarios(),
      fetchSignals(),
      fetchSimulationJobs(),
      fetchSimulationCapabilities(),
    ]);

    if (datasetResult.status === "fulfilled") {
      setDataset(datasetResult.value);
    }
    if (scenarioResult.status === "fulfilled") {
      const items = scenarioResult.value;
      setScenariosLoaded(ok(SCENARIOS_KEY, items));
      setSelectedScenarioId((current) => (items.some((scenario) => scenario.id === current) ? current : items[0]?.id ?? ""));
    } else {
      setScenariosLoaded(failed(SCENARIOS_KEY, scenarioResult.reason));
    }
    if (signalResult.status === "fulfilled") {
      setSignals(signalResult.value);
    }
    // Previously a failed signal/job fetch silently left these empty, which
    // rendered indistinguishably from "no signals in this network" / "no
    // runs yet" — a real backend error must not look identical to that.
    const signalsFailed = signalResult.status === "rejected";
    if (jobResult.status === "fulfilled") {
      setJobs(jobResult.value);
    }
    const jobsFailed = jobResult.status === "rejected";
    if (capabilityResult.status === "fulfilled") {
      setCapabilities(capabilityResult.value);
    } else {
      setCapabilities({
        can_run: false,
        can_prepare_simulation: false,
        reason: t("시뮬레이션 실행 상태를 확인할 수 없습니다. 실행 서버가 켜져 있는지 확인하세요.", "Cannot check simulation runtime. Make sure the execution server is running."),
        environment: "unknown",
        pywin32_available: false,
      });
    }

    if (datasetResult.status === "rejected" || scenarioResult.status === "rejected") {
      setError(t("시뮬레이션 기본 데이터를 불러오지 못했습니다.", "Failed to load simulation setup data."));
    } else if (signalsFailed || jobsFailed) {
      setError(
        t(
          `${signalsFailed ? "신호 데이터" : ""}${signalsFailed && jobsFailed ? " / " : ""}${jobsFailed ? "실행 작업 목록" : ""}을(를) 불러오지 못했습니다. 목록이 비어 보이지만 실제로는 서버 오류일 수 있습니다.`,
          `Failed to load ${[signalsFailed && "signal data", jobsFailed && "the job list"].filter(Boolean).join(" / ")}. The list may look empty, but this is a server error, not "nothing here".`,
        ),
      );
    }
    setLoading(false);
  }, [t]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const currentBase = getApiBase();
      setApiBase(currentBase);
      setApiBaseDraft(currentBase);
      void loadBaseData();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadBaseData]);

  const signalByNodeId = useMemo(() => new Map(signals.map((signal) => [String(signal.node_id), signal])), [signals]);
  const selectedSignal = selectedNode ? signalByNodeId.get(selectedNode.id) ?? null : null;
  const selectedSpeedRow = selectedLink ? speedLimits.find((row) => row.linkId === selectedLink.id) ?? null : null;
  const selectedVehicleRow = selectedLink ? vehicleInputs.find((row) => row.linkId === selectedLink.id) ?? null : null;
  const selectedSignalRow = selectedNode
    ? signalTimings.find((row) => row.nodeId === selectedNode.id || row.controllerId === selectedSignal?.sc_no) ?? null
    : null;

  // Unit #68: an uploaded dataset opens the run door regardless of kind --
  // a bare .inpx upload is runnable exactly like a zip (the runner's own
  // default-run path never reads project.vissimpdb/Scenarios; see
  // routers/simulations.py's _simulation_capabilities comment). Mirrors
  // app/dashboard/page.tsx's own `hasUploadedDataset` gate.
  const hasUploadedDataset = dataset?.source === "uploaded";
  const requiresUploadedDataset = capabilities?.requires_uploaded_dataset ?? true;
  const datasetReady = !requiresUploadedDataset || hasUploadedDataset;
  const runtimeReady = capabilities?.can_run ?? false;
  const running = activeStatus === "running";
  const canPrepareSimulation = capabilities?.can_prepare_simulation ?? capabilities?.can_run ?? false;
  const canUploadZip = canPrepareSimulation || isLocalApiBase(apiBase);
  // #72: a scenario is an optional override, not a requirement -- the default
  // run (no scenario_id) never reads pdb/Scenarios at all (#68's verified
  // trace; see the comment on `startSimulation`'s call below). An INPX-only
  // dataset's scenario list is structurally empty (no project DB) and must
  // not permanently lock the run door.
  const canRunSimulation = datasetReady && runtimeReady && !running;

  const queuedCount = speedLimits.length + vehicleInputs.length + signalTimings.length;
  const latestJobs = useMemo(() => jobs.slice(0, 5), [jobs]);

  const mapIncidents = useMemo<MapIncident[]>(
    () =>
      speedLimits.flatMap((row) => {
        const linkId = row.linkId.trim();
        if (!linkId) return [];
        return [{
          linkId,
          type: "speed_limit",
          closedLanes: 0,
          totalLanes: 0,
          speedLimit: Number(row.speedLimit) || 0,
          duration: Number(row.duration) || 0,
        }];
      }),
    [speedLimits],
  );

  const queuedLinkModes = useMemo<Record<string, QueuedLinkMode>>(() => {
    const modes: Record<string, QueuedLinkMode> = {};
    for (const row of vehicleInputs) {
      const linkId = row.linkId.trim();
      if (!linkId) continue;
      modes[linkId] = modes[linkId] === "speed" ? "mixed" : "volume";
    }
    for (const row of speedLimits) {
      const linkId = row.linkId.trim();
      if (!linkId) continue;
      modes[linkId] = modes[linkId] === "volume" ? "mixed" : "speed";
    }
    return modes;
  }, [speedLimits, vehicleInputs]);

  const clearDraft = useCallback(() => {
    setSelectedLink(null);
    setSelectedNode(null);
    setVehicleInputs([]);
    setSignalTimings([]);
    setSpeedLimits([]);
    setActiveJobId(null);
    setActiveProgress(0);
    setActiveStatus("idle");
    setError(null);
  }, []);

  const handleUploadZip = useCallback(async (file: File | undefined) => {
    if (!file) return;
    if (!canUploadZip) {
      setUploadError(t("실행 서버 연결 후 ZIP 파일을 업로드할 수 있습니다.", "Connect to the execution server before uploading a ZIP package."));
      if (zipInputRef.current) zipInputRef.current.value = "";
      return;
    }
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setUploadError(t("VISSIM 프로젝트 파일이 포함된 .zip 파일만 업로드할 수 있습니다.", "Upload a .zip package containing the VISSIM project files."));
      if (zipInputRef.current) zipInputRef.current.value = "";
      return;
    }

    setUploadingZip(true);
    setUploadError(null);
    setError(null);
    try {
      const nextDataset = await uploadInpxDataset(file);
      setDataset(nextDataset);
      clearDraft();
      publishDatasetChange(nextDataset);
      await loadBaseData();
    } catch (uploadProblem) {
      setUploadError(uploadProblem instanceof Error ? uploadProblem.message : t("ZIP 업로드에 실패했습니다.", "Failed to upload ZIP package."));
    } finally {
      setUploadingZip(false);
      if (zipInputRef.current) zipInputRef.current.value = "";
    }
  }, [canUploadZip, clearDraft, loadBaseData, t]);

  const [confirmClearDataset, setConfirmClearDataset] = useState(false);

  const handleClearUploadedZip = useCallback(async () => {
    setUploadingZip(true);
    setUploadError(null);
    setError(null);
    try {
      const nextDataset = await resetActiveDataset();
      setDataset(nextDataset);
      clearDraft();
      publishDatasetChange(nextDataset);
      await loadBaseData();
    } catch (resetProblem) {
      setUploadError(resetProblem instanceof Error ? resetProblem.message : t("기본 데이터 복원에 실패했습니다.", "Failed to restore the default dataset."));
    } finally {
      setUploadingZip(false);
    }
  }, [clearDraft, loadBaseData, t]);

  const addSpeedLimitForLink = useCallback((link: LinkFeature) => {
    setSpeedLimits((rows) => upsertSpeedLimitRow(rows, link.id));
  }, []);

  const addVehicleInputForLink = useCallback((link: LinkFeature) => {
    setVehicleInputs((rows) => upsertVehicleInputRow(rows, link.id));
  }, []);

  const addSignalForNode = useCallback((node: NodeFeature) => {
    const signal = signalByNodeId.get(node.id);
    setSignalTimings((rows) => upsertSignalTimingRow(rows, node, signal));
  }, [signalByNodeId]);

  const handleMapLinkClick = useCallback((link: LinkFeature) => {
    setSelectedLink(link);
    if (editMode === "speed") addSpeedLimitForLink(link);
    if (editMode === "volume") addVehicleInputForLink(link);
  }, [addSpeedLimitForLink, addVehicleInputForLink, editMode]);

  const handleMapNodeClick = useCallback((node: NodeFeature) => {
    setSelectedNode(node);
    if (editMode === "signal") addSignalForNode(node);
  }, [addSignalForNode, editMode]);

  const handleConnectedLinkClick = useCallback((link: LinkFeature) => {
    setSelectedLink(link);
    if (editMode === "speed") addSpeedLimitForLink(link);
    if (editMode === "volume") addVehicleInputForLink(link);
  }, [addSpeedLimitForLink, addVehicleInputForLink, editMode]);

  const applyApiBase = useCallback(async (nextBase: string | null) => {
    setApiBaseOverride(nextBase);
    const currentBase = getApiBase();
    setApiBase(currentBase);
    setApiBaseDraft(currentBase);
    setError(null);
    setActiveJobId(null);
    setActiveProgress(0);
    setActiveStatus("idle");
    await loadBaseData();
  }, [loadBaseData]);

  const monitorSimulationJob = useCallback((jobId: string) => {
    let closed = false;
    let failedPolls = 0;
    let ws: WebSocket | null = null;

    const finish = async (status: RunState, message?: string | null) => {
      if (closed) return;
      closed = true;
      window.clearInterval(pollTimer);
      ws?.close();
      setActiveStatus(status);
      setActiveProgress(status === "done" ? 100 : (status === "error" ? activeProgress : activeProgress));
      if (message) setError(message);
      await loadBaseData();
      if (status === "done") publishSimulationRefresh();
    };

    const updateProgress = (progress: number | undefined) => {
      setActiveProgress((current) => Math.max(current, normalizeProgress(progress)));
    };

    const applyStatus = (status: string | undefined, progress?: number, message?: string | null) => {
      updateProgress(progress);
      if (status === "done") void finish("done");
      if (status === "error") void finish("error", message ?? t("시뮬레이션 실행 중 오류가 발생했습니다.", "Simulation failed."));
    };

    try {
      ws = createSimulationWS(jobId);
      ws.onmessage = (event) => {
        let data: SimulationStatusMessage;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        applyStatus(data.status, data.progress, data.error);
      };
      ws.onerror = () => {
        ws?.close();
      };
    } catch {
      ws = null;
    }

    const pollTimer = window.setInterval(async () => {
      if (closed) return;
      try {
        const job = await fetchSimulationJob(jobId);
        failedPolls = 0;
        applyStatus(job.status, job.progress, job.error);
      } catch (pollProblem) {
        failedPolls += 1;
        if (failedPolls >= 6) {
          void finish(
            "error",
            pollProblem instanceof Error
              ? pollProblem.message
              : t("실행 상태를 확인할 수 없습니다.", "Cannot read simulation status."),
          );
        }
      }
    }, 1500);
  }, [activeProgress, loadBaseData, t]);

  const handleRun = async () => {
    setError(null);
    if (!canRunSimulation) {
      setActiveStatus("idle");
      setActiveProgress(0);
      setError(buildRunDisabledMessage({ capabilities, datasetReady, t }));
      return;
    }

    setActiveStatus("running");
    setActiveProgress(1);

    try {
      const job = await startSimulation({
        name: runName.trim() || "Simulation run",
        // #72: `|| undefined` (never `""`) so JSON.stringify OMITS the key
        // entirely when nothing is selected -- SimParams.scenario_id is
        // `str | None = None` (routers/simulations.py:63) and the default
        // run reads it as "no scenario", never as an empty string.
        scenario_id: selectedScenarioId || undefined,
        sim_duration: duration,
        random_seed: seed,
        vehicle_inputs: rowsToNumberMap(vehicleInputs, "linkId", "volume"),
        signal_cycles: rowsToNumberMap(signalTimings, "controllerId", "cycle"),
        signal_offsets: rowsToNumberMap(signalTimings, "controllerId", "offset"),
        incidents: speedLimitsToSimulationIncidents(speedLimits),
      });

      setActiveJobId(job.id);
      monitorSimulationJob(job.id);
      await loadBaseData();
    } catch (runProblem) {
      setActiveStatus("error");
      setActiveProgress(0);
      setError(runProblem instanceof Error ? runProblem.message : t("시뮬레이션 실행 요청에 실패했습니다.", "Failed to start the simulation."));
      await loadBaseData();
    }
  };

  return (
    <div className="mx-auto w-full max-w-[96rem] space-y-6 pb-16">
      <input
        ref={zipInputRef}
        type="file"
        accept=".zip"
        className="hidden"
        onChange={(event) => {
          void handleUploadZip(event.target.files?.[0]);
        }}
      />

      <ConfirmDialog
        open={confirmClearDataset}
        title={t("기본값으로 복원할까요?", "Restore the default dataset?")}
        message={t(
          `현재 데이터셋("${dataset?.filename}")과 그에 연결된 모든 시뮬레이션 결과가 사라집니다. 이 작업은 되돌릴 수 없습니다.`,
          `The current dataset ("${dataset?.filename}") and every simulation result tied to it will be removed. This cannot be undone.`,
        )}
        confirmLabel={t("복원", "Restore")}
        cancelLabel={t("취소", "Cancel")}
        busy={uploadingZip}
        onConfirm={() => {
          void handleClearUploadedZip().then(() => setConfirmClearDataset(false));
        }}
        onCancel={() => setConfirmClearDataset(false)}
      />

      <section className="px-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight text-white">
              {t("시뮬레이션 실행", "Simulation Runner")}
            </h1>
            <p className="mt-1 text-[15px] text-slate-600">
              {t(
                "지도에서 링크와 교차로를 선택해 입력 조건을 만들고, VISSIM 실행 결과를 생성합니다.",
                "Select links and intersections on the map, prepare run inputs, and generate VISSIM results.",
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {/* #72: sibling of #67's calibration-page fix -- this label wraps
                to two lines under the same conditions; same whitespace-nowrap
                treatment. */}
            <StatusPill label={t("데이터", "Dataset")} value={dataset?.filename ?? "-"} tone={hasUploadedDataset ? "success" : "neutral"} />
            <StatusPill label={t("실행", "Runtime")} value={runtimeReady ? "Ready" : "Blocked"} tone={runtimeReady ? "success" : "warning"} />
            <StatusPill label={t("대기 조건", "Queued")} value={`${queuedCount}`} tone={queuedCount > 0 ? "info" : "neutral"} />
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_370px]">
        <DataAndRuntimePanel
          dataset={dataset}
          capabilities={capabilities}
          apiBase={apiBase}
          apiBaseDraft={apiBaseDraft}
          loading={loading}
          uploading={uploadingZip}
          uploadError={uploadError}
          canUploadZip={canUploadZip}
          hasUploadedDataset={hasUploadedDataset}
          onSelectZip={() => zipInputRef.current?.click()}
          onClearDataset={() => setConfirmClearDataset(true)}
          onApiBaseDraftChange={setApiBaseDraft}
          onApplyApiBase={() => void applyApiBase(apiBaseDraft)}
          onUseDefaultApiBase={() => void applyApiBase("http://127.0.0.1:8001")}
          onRefresh={() => void loadBaseData()}
          t={t}
        />
        <RunPanel
          scenarios={scenarios}
          emptyScenarioMessage={
            scenariosLoading
              ? t("시나리오 목록 불러오는 중...", "Loading scenario list...", "シナリオ一覧を読み込み中...")
              : scenariosError
                ? t("시나리오 목록을 불러오지 못했습니다", "Couldn't load the scenario list", "シナリオ一覧を読み込めませんでした")
                : scenariosEmpty
                  ? t(
                      "시나리오 없음 — 기본 네트워크로 실행합니다",
                      "No scenarios — runs against the default network",
                      "シナリオなし — 既定のネットワークで実行します",
                    )
                  : ""
          }
          selectedScenarioId={selectedScenarioId}
          onScenarioChange={setSelectedScenarioId}
          runName={runName}
          onRunNameChange={setRunName}
          duration={duration}
          onDurationChange={setDuration}
          seed={seed}
          onSeedChange={setSeed}
          running={running}
          canRun={canRunSimulation}
          activeJobId={activeJobId}
          activeStatus={activeStatus}
          activeProgress={activeProgress}
          disabledMessage={buildRunDisabledMessage({ capabilities, datasetReady, t })}
          onRun={() => void handleRun()}
          onClearDraft={clearDraft}
        />
      </section>

      {error && (
        // #74 fix (2): a failed run's error can now carry the .inpx's own
        // .err content (path + Error lines) appended after a blank line --
        // whitespace-pre-wrap so those line breaks and the .err path survive
        // instead of collapsing into one run-on line (default `white-space:
        // normal`), while long individual lines still wrap instead of
        // forcing horizontal scroll.
        <div className="whitespace-pre-wrap rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-[15px] font-medium text-rose-700">
          {error}
        </div>
      )}

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_390px]">
        <div className="relative h-[640px] overflow-hidden rounded-xl border border-slate-300/70 bg-ink">
          <MapViewer
            onLinkClick={handleMapLinkClick}
            onNodeClick={handleMapNodeClick}
            incidents={mapIncidents}
            metric="none"
            scenarioId={selectedScenarioId || undefined}
            selectedLinkId={selectedLink?.id ?? null}
            queuedLinkModes={queuedLinkModes}
          />
          <div className="pointer-events-auto absolute left-4 top-4 z-[900] rounded-xl border border-ink/15 bg-slate-950/88 p-3 text-[14px] text-ink shadow-2xl backdrop-blur">
            <div className="mb-2 font-semibold">{t("편집 모드", "Edit Mode")}</div>
            <div className="grid grid-cols-3 gap-1.5">
              <ModeButton active={editMode === "speed"} label={t("속도", "Speed")} onClick={() => setEditMode("speed")} />
              <ModeButton active={editMode === "volume"} label={t("교통량", "Volume")} onClick={() => setEditMode("volume")} />
              <ModeButton active={editMode === "signal"} label={t("신호", "Signal")} onClick={() => setEditMode("signal")} />
            </div>
            <p className="mt-2 max-w-[240px] text-[13px] leading-4 text-slate-200">
              {editMode === "signal"
                ? t("교차로를 클릭하면 신호 조정 조건이 추가됩니다.", "Click an intersection to add signal timing.")
                : t("링크를 클릭하면 선택한 조건이 실행 대기 목록에 추가됩니다.", "Click a link to add the selected input to the run queue.")}
            </p>
          </div>
        </div>

        <SelectionInspector
          mode={editMode}
          selectedLink={selectedLink}
          selectedNode={selectedNode}
          selectedSignal={selectedSignal}
          selectedSpeedRow={selectedSpeedRow}
          selectedVehicleRow={selectedVehicleRow}
          selectedSignalRow={selectedSignalRow}
          onPickConnectedLink={handleConnectedLinkClick}
          onAddSignal={() => selectedNode && addSignalForNode(selectedNode)}
          onUpdateSpeed={(field, value) => selectedSpeedRow && updateSpeedRow(selectedSpeedRow.id, field, value, setSpeedLimits)}
          onUpdateVehicle={(value) => selectedVehicleRow && updateVehicleRow(selectedVehicleRow.id, "volume", value, setVehicleInputs)}
          onUpdateSignal={(field, value) => selectedSignalRow && updateSignalRow(selectedSignalRow.id, field, value, setSignalTimings)}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <QueuePanel
          title={t("속도 제한 조건", "Speed Limit Inputs")}
          emptyText={t("속도 모드에서 지도 링크를 클릭하세요.", "Click a map link in Speed mode.")}
          count={speedLimits.length}
        >
          {speedLimits.map((row) => (
            <QueueRow key={row.id} label={`Link ${row.linkId}`} onRemove={() => setSpeedLimits((rows) => rows.filter((item) => item.id !== row.id))}>
              <NumberField label={t("속도", "Speed")} value={row.speedLimit} onChange={(value) => updateSpeedRow(row.id, "speedLimit", value, setSpeedLimits)} suffix="km/h" />
              <NumberField label={t("적용 시간", "Duration")} value={row.duration} onChange={(value) => updateSpeedRow(row.id, "duration", value, setSpeedLimits)} suffix="s" />
            </QueueRow>
          ))}
        </QueuePanel>

        <QueuePanel
          title={t("교통량 입력 조건", "Vehicle Input Changes")}
          emptyText={t("교통량 모드에서 지도 링크를 클릭하세요.", "Click a map link in Volume mode.")}
          count={vehicleInputs.length}
        >
          {vehicleInputs.map((row) => (
            <QueueRow key={row.id} label={`Link ${row.linkId}`} onRemove={() => setVehicleInputs((rows) => rows.filter((item) => item.id !== row.id))}>
              <NumberField label={t("교통량", "Volume")} value={row.volume} onChange={(value) => updateVehicleRow(row.id, "volume", value, setVehicleInputs)} suffix="veh/h" />
            </QueueRow>
          ))}
        </QueuePanel>

        <QueuePanel
          title={t("신호 제어 조건", "Signal Timing Changes")}
          emptyText={t("신호 모드에서 교차로를 클릭하세요.", "Click an intersection in Signal mode.")}
          count={signalTimings.length}
        >
          {signalTimings.map((row) => (
            <QueueRow key={row.id} label={`${row.nodeName || t("교차로", "Node")} ${row.nodeId}`} onRemove={() => setSignalTimings((rows) => rows.filter((item) => item.id !== row.id))}>
              <NumberField label={t("주기", "Cycle")} value={row.cycle} onChange={(value) => updateSignalRow(row.id, "cycle", value, setSignalTimings)} suffix="s" />
              <NumberField label={t("옵셋", "Offset")} value={row.offset} onChange={(value) => updateSignalRow(row.id, "offset", value, setSignalTimings)} suffix="s" />
            </QueueRow>
          ))}
        </QueuePanel>
      </section>

      <section className="rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-bold text-slate-900">{t("최근 실행 작업", "Recent Simulation Jobs")}</h2>
            <p className="text-[15px] text-slate-600">
              {t("완료된 작업은 대시보드 비교 시나리오로 등록됩니다.", "Completed jobs are registered as comparison scenarios for the dashboard.")}
            </p>
          </div>
          <button type="button" className="ui-button-ghost px-3 py-2 text-[14px]" onClick={() => void loadBaseData()}>
            {t("새로고침", "Refresh")}
          </button>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {latestJobs.length === 0 ? (
            <EmptyLine text={t("아직 실행 작업이 없습니다.", "No simulation jobs yet.")} />
          ) : (
            latestJobs.map((job) => <JobRow key={job.id} job={job} />)
          )}
        </div>
      </section>
    </div>
  );
}

function DataAndRuntimePanel({
  dataset,
  capabilities,
  apiBase,
  apiBaseDraft,
  loading,
  uploading,
  uploadError,
  canUploadZip,
  hasUploadedDataset,
  onSelectZip,
  onClearDataset,
  onApiBaseDraftChange,
  onApplyApiBase,
  onUseDefaultApiBase,
  onRefresh,
  t,
}: {
  dataset: ActiveDataset | null;
  capabilities: SimulationCapabilities | null;
  apiBase: string;
  apiBaseDraft: string;
  loading: boolean;
  uploading: boolean;
  uploadError: string | null;
  canUploadZip: boolean;
  hasUploadedDataset: boolean;
  onSelectZip: () => void;
  onClearDataset: () => void;
  onApiBaseDraftChange: (value: string) => void;
  onApplyApiBase: () => void;
  onUseDefaultApiBase: () => void;
  onRefresh: () => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const runtimeTone = capabilities?.can_run ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800";
  return (
    <div className="grid h-full gap-4 lg:grid-cols-2">
        <div className="flex h-full flex-col rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-[15px] font-bold text-slate-900">{t("데이터 패키지", "Dataset Package")}</h2>
            <span className={`rounded-full px-2 py-0.5 text-[13px] font-bold ${hasUploadedDataset ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
              {hasUploadedDataset ? t("업로드됨", "Uploaded") : t("기본", "Default")}
            </span>
          </div>
          <p className="mt-2 text-[15px] font-semibold text-slate-800">{dataset?.filename ?? "-"}</p>
          <p className="mt-1 text-[14px] text-slate-500">
            {dataset?.kind ? `${dataset.kind} / ${dataset.source}` : t("데이터 상태를 확인 중입니다.", "Checking dataset status.")}
          </p>
          <div className="mt-auto flex flex-wrap gap-2 pt-4">
            <button type="button" className="ui-button-primary px-3 py-2 text-[14px]" disabled={uploading || !canUploadZip} onClick={onSelectZip}>
              {uploading ? t("업로드 중", "Uploading") : t("ZIP 업로드", "Upload ZIP")}
            </button>
            <button
              type="button"
              className="ui-button-danger px-3 py-2 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
              disabled={uploading || !hasUploadedDataset}
              onClick={onClearDataset}
            >
              {t("기본값 복원", "Restore Default")}
            </button>
            <button type="button" className="ui-button-ghost px-3 py-2 text-[14px]" disabled={loading} onClick={onRefresh}>
              {t("상태 새로고침", "Refresh")}
            </button>
          </div>
          {uploadError && <p className="mt-2 text-[14px] font-medium text-rose-600">{uploadError}</p>}
        </div>

        <div className="flex h-full flex-col rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
          <h2 className="text-[15px] font-bold text-slate-900">{t("실행 환경", "Runtime")}</h2>
          <div className={`mt-2 rounded-xl border px-3 py-3 text-[14px] ${runtimeTone}`}>
            <p className="font-bold">
              {capabilities?.can_run ? t("실행 가능", "Ready to run") : t("실행 불가", "Run blocked")}
            </p>
            <p className="mt-1 leading-5">
              {capabilities?.can_run
                ? t("VISSIM 실행 환경이 준비되었습니다.", "VISSIM runtime is ready.")
                : t("실행 환경이 준비되지 않았습니다. VISSIM과 실행 서버 상태를 확인하세요.", "The runtime is not ready. Check VISSIM and the execution server.")}
            </p>
            <p className="mt-1 text-[13px]">
              {t("환경", "Environment")}: {capabilities?.environment ?? "-"} / pywin32 {capabilities?.pywin32_available ? "OK" : "-"}
            </p>
          </div>
          <div className="mt-auto grid gap-2 pt-4 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
            <input className="ui-input text-[14px]" value={apiBaseDraft} onChange={(event) => onApiBaseDraftChange(event.target.value)} aria-label="API base URL" />
            <button type="button" className="ui-button-ghost px-3 py-2 text-[14px]" onClick={onApplyApiBase}>
              {t("적용", "Apply")}
            </button>
            <button type="button" className="ui-button-ghost px-3 py-2 text-[14px]" onClick={onUseDefaultApiBase}>
              {t("기본 주소", "Default")}
            </button>
          </div>
          <p className="mt-1 text-[13px] text-slate-500">{t("연결 주소", "Endpoint")}: {apiBase}</p>
        </div>
    </div>
  );
}

function SelectionInspector({
  mode,
  selectedLink,
  selectedNode,
  selectedSignal,
  selectedSpeedRow,
  selectedVehicleRow,
  selectedSignalRow,
  onPickConnectedLink,
  onAddSignal,
  onUpdateSpeed,
  onUpdateVehicle,
  onUpdateSignal,
}: {
  mode: EditMode;
  selectedLink: LinkFeature | null;
  selectedNode: NodeFeature | null;
  selectedSignal: SignalNodeInfo | null;
  selectedSpeedRow: SpeedLimitRow | null;
  selectedVehicleRow: VehicleInputRow | null;
  selectedSignalRow: SignalTimingRow | null;
  onPickConnectedLink: (link: LinkFeature) => void;
  onAddSignal: () => void;
  onUpdateSpeed: (field: keyof Omit<SpeedLimitRow, "id" | "linkId">, value: string) => void;
  onUpdateVehicle: (value: string) => void;
  onUpdateSignal: (field: keyof Omit<SignalTimingRow, "id" | "controllerId" | "nodeId" | "nodeName">, value: string) => void;
}) {
  const { t } = useLanguage();
  return (
    <div className="flex h-full flex-col rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
      <div>
        <h2 className="text-base font-bold text-slate-900">{t("선택 항목", "Selection")}</h2>
        <p className="mt-1 text-[15px] text-slate-600">
          {t("지도에서 선택한 링크와 교차로의 조건을 바로 수정합니다.", "Edit the currently selected link or intersection inputs.")}
        </p>
      </div>

      <div className="mt-4 space-y-3">
        <InfoBox
          label={t("선택 링크", "Selected Link")}
          value={selectedLink ? `${selectedLink.name || "Link"} ${selectedLink.id} / ${selectedLink.length.toFixed(1)} m` : t("지도에서 링크를 클릭하세요.", "Click a link on the map.")}
        />
        <InfoBox
          label={t("선택 교차로", "Selected Intersection")}
          value={selectedNode ? `${selectedNode.name || t("교차로", "Node")} (${selectedNode.id})` : t("지도에서 교차로를 클릭하세요.", "Click an intersection on the map.")}
        />

        {selectedNode && (
          <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
            <p className="mb-2 text-[14px] font-semibold text-slate-700">{t("연결 링크", "Connected Links")}</p>
            <ConnectionList links={selectedNode.connectedLinks} onPick={onPickConnectedLink} selectedLinkId={selectedLink?.id ?? null} />
          </div>
        )}

        {mode === "speed" && selectedSpeedRow && (
          <div className="rounded-xl border border-rose-100 bg-rose-50/70 p-3">
            <p className="text-[14px] font-semibold text-rose-800">{t("속도 제한 조정", "Speed Limit Adjustment")}</p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <NumberField label={t("속도", "Speed")} value={selectedSpeedRow.speedLimit} onChange={(value) => onUpdateSpeed("speedLimit", value)} suffix="km/h" />
              <NumberField label={t("적용 시간", "Duration")} value={selectedSpeedRow.duration} onChange={(value) => onUpdateSpeed("duration", value)} suffix="s" />
            </div>
          </div>
        )}

        {mode === "volume" && selectedVehicleRow && (
          <div className="rounded-xl border border-emerald-100 bg-emerald-50/70 p-3">
            <p className="text-[14px] font-semibold text-emerald-800">{t("교통량 입력 조정", "Vehicle Input Adjustment")}</p>
            <div className="mt-3">
              <NumberField label={t("교통량", "Volume")} value={selectedVehicleRow.volume} onChange={onUpdateVehicle} suffix="veh/h" />
            </div>
          </div>
        )}

        {mode === "signal" && (
          <div className="rounded-xl border border-blue-100 bg-blue-50/70 p-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[14px] font-semibold text-blue-800">{t("신호 제어 조정", "Signal Control Adjustment")}</p>
                <p className="mt-1 text-[13px] leading-4 text-blue-700">
                  {selectedSignal?.has_signal
                    ? t(`현재 주기 ${selectedSignal.cycle_time ?? "-"}초`, `Existing cycle ${selectedSignal.cycle_time ?? "-"}s`, `現在の周期 ${selectedSignal.cycle_time ?? "-"}秒`)
                    : t("선택 교차로에 매칭된 신호 데이터가 없으면 노드 ID로 조건을 생성합니다.", "If no matched signal data exists, the node ID is used for the input.")}
                </p>
              </div>
              {selectedNode && !selectedSignalRow && (
                <button type="button" className="ui-button-ghost px-2.5 py-1 text-[14px]" onClick={onAddSignal}>
                  {t("추가", "Add")}
                </button>
              )}
            </div>
            {selectedSignalRow && (
              <div className="mt-3 grid grid-cols-2 gap-2">
                <NumberField label={t("주기", "Cycle")} value={selectedSignalRow.cycle} onChange={(value) => onUpdateSignal("cycle", value)} suffix="s" />
                <NumberField label={t("옵셋", "Offset")} value={selectedSignalRow.offset} onChange={(value) => onUpdateSignal("offset", value)} suffix="s" />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function RunPanel({
  scenarios,
  emptyScenarioMessage,
  selectedScenarioId,
  onScenarioChange,
  runName,
  onRunNameChange,
  duration,
  onDurationChange,
  seed,
  onSeedChange,
  running,
  canRun,
  activeJobId,
  activeStatus,
  activeProgress,
  disabledMessage,
  onRun,
  onClearDraft,
}: {
  scenarios: ScenarioMeta[];
  // #72: shown as the picker's own placeholder option when the list is
  // empty -- distinct text for "the fetch failed" vs "this dataset genuinely
  // has none", never a dead end (a scenario is optional; the run still works).
  emptyScenarioMessage: string;
  selectedScenarioId: string;
  onScenarioChange: (value: string) => void;
  runName: string;
  onRunNameChange: (value: string) => void;
  duration: number;
  onDurationChange: (value: number) => void;
  seed: number;
  onSeedChange: (value: number) => void;
  running: boolean;
  canRun: boolean;
  activeJobId: string | null;
  activeStatus: RunState;
  activeProgress: number;
  disabledMessage: string;
  onRun: () => void;
  onClearDraft: () => void;
}) {
  const { t } = useLanguage();
  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-bold text-slate-900">{t("실행 설정", "Run Setup")}</h2>
          <p className="mt-1 text-[15px] text-slate-600">{t("설정한 조건으로 실제 실행 요청을 보냅니다.", "Sends a real run request with the configured inputs.")}</p>
        </div>
        <button type="button" className="ui-button-ghost px-3 py-2 text-[14px]" onClick={onClearDraft}>
          {t("조건 초기화", "Clear Inputs")}
        </button>
      </div>

      <div className="mt-4 space-y-3">
        <label className="block">
          <span className="text-[14px] font-semibold text-slate-600">{t("결과 이름", "Result Name")}</span>
          <input className="ui-input mt-1" value={runName} onChange={(event) => onRunNameChange(event.target.value)} />
        </label>
        <label className="block">
          <span className="text-[14px] font-semibold text-slate-600">{t("기준 시나리오", "Base Scenario")}</span>
          <select className="ui-select mt-1" value={selectedScenarioId} onChange={(event) => onScenarioChange(event.target.value)}>
            {scenarios.length === 0 ? (
              <option value="">{emptyScenarioMessage}</option>
            ) : (
              scenarios.map((scenario) => (
                <option key={scenario.id} value={scenario.id}>
                  {scenario.name} ({scenario.id})
                </option>
              ))
            )}
          </select>
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="text-[14px] font-semibold text-slate-600">{t("실행 시간", "Duration")}</span>
            <input type="number" className="ui-input mt-1" min={60} max={86400} value={duration} onChange={(event) => onDurationChange(Number(event.target.value))} />
          </label>
          <label className="block">
            <span className="text-[14px] font-semibold text-slate-600">{t("랜덤 시드", "Seed")}</span>
            <input type="number" className="ui-input mt-1" min={0} value={seed} onChange={(event) => onSeedChange(Number(event.target.value))} />
          </label>
        </div>
      </div>

      {!canRun && (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-[14px] leading-5 text-amber-800">
          {disabledMessage}
        </div>
      )}

      <button type="button" onClick={onRun} disabled={running || !canRun} className="ui-button-primary mt-auto w-full px-4 py-3 text-[15px] disabled:cursor-not-allowed disabled:opacity-50">
        {running ? t("실행 중", "Running") : t("시뮬레이션 실행", "Run Simulation")}
      </button>

      {activeStatus !== "idle" && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/80 p-3">
          <div className="flex items-center justify-between text-[14px] font-semibold text-slate-600">
            <span>{activeJobId ? `Job ${activeJobId}` : t("준비 중", "Preparing")}</span>
            <span>{activeProgress}%</span>
          </div>
          <div className="mt-2 h-2 rounded-full bg-slate-200">
            <div className={`h-2 rounded-full transition-all duration-500 ${activeStatus === "error" ? "bg-rose-500" : "bg-blue-500"}`} style={{ width: `${activeProgress}%` }} />
          </div>
          {activeStatus === "done" && (
            <div className="mt-3 grid grid-cols-2 gap-2">
              <Link className="ui-button-ghost px-3 py-2 text-center text-[14px]" href="/dashboard">
                {t("대시보드", "Dashboard")}
              </Link>
              <Link className="ui-button-ghost px-3 py-2 text-center text-[14px]" href="/base-model">
                {t("검증 화면", "Validation")}
              </Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConnectionList({
  links,
  selectedLinkId,
  onPick,
}: {
  links: NodeConnection[];
  selectedLinkId: string | null;
  onPick: (link: LinkFeature) => void;
}) {
  const { t } = useLanguage();
  const grouped = groupConnections(links);
  return (
    <div className="grid grid-cols-2 gap-2">
      {(["north", "east", "south", "west"] as const).map((direction) => (
        <div key={direction} className="rounded-lg border border-slate-200 bg-surface p-2">
          <p className="mb-1 text-[12px] font-bold uppercase text-slate-500">{directionLabel(direction, t)}</p>
          <div className="space-y-1">
            {grouped[direction].length === 0 && <span className="text-[12px] text-slate-400">-</span>}
            {grouped[direction].map((link) => {
              const active = selectedLinkId === link.id;
              return (
                <button
                  key={`${direction}-${link.role}-${link.id}`}
                  type="button"
                  onClick={() => onPick(link)}
                  className={`flex w-full items-center justify-between gap-2 rounded-md border px-2 py-1 text-[12px] transition ${
                    active ? "border-blue-500 bg-blue-100 text-slate-900" : "border-slate-200 bg-slate-50 text-slate-600 hover:border-slate-300"
                  }`}
                >
                  <span>Link {link.id}</span>
                  <span>{link.role === "incoming" ? t("진입", "In") : t("진출", "Out")}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function QueuePanel({
  title,
  emptyText,
  count,
  children,
}: {
  title: string;
  emptyText: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-[15px] font-bold text-slate-900">{title}</h2>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[13px] font-bold text-slate-600">{count}</span>
      </div>
      <div className="space-y-2">
        {count === 0 ? <EmptyLine text={emptyText} /> : children}
      </div>
    </section>
  );
}

function QueueRow({ label, onRemove, children }: { label: string; onRemove: () => void; children: React.ReactNode }) {
  const { t } = useLanguage();
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[14px] font-bold text-slate-800">{label}</span>
        <button type="button" className="text-[13px] font-semibold text-rose-600 hover:text-rose-800" onClick={onRemove}>
          {t("삭제", "Remove")}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">{children}</div>
    </div>
  );
}

function NumberField({
  label,
  value,
  suffix,
  onChange,
}: {
  label: string;
  value: string;
  suffix?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-[13px] font-semibold text-slate-600">{label}</span>
      <div className="mt-1 flex items-center gap-2">
        <input type="number" className="ui-input" value={value} onChange={(event) => onChange(event.target.value)} />
        {suffix && <span className="min-w-max text-[13px] font-semibold text-slate-500">{suffix}</span>}
      </div>
    </label>
  );
}

function ModeButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-3 py-2 text-[14px] font-bold transition ${
        active ? "border-white/30 bg-surface text-slate-900" : "border-white/10 bg-surface/8 text-white hover:bg-surface/14"
      }`}
    >
      {label}
    </button>
  );
}

function InfoBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2">
      <p className="text-[12px] font-bold uppercase text-slate-500">{label}</p>
      <p className="mt-1 text-[14px] font-semibold text-slate-800">{value}</p>
    </div>
  );
}

function EmptyLine({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-3 text-[14px] text-slate-500">
      {text}
    </div>
  );
}

function StatusPill({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "success" | "warning" | "info" }) {
  const toneClass =
    tone === "success"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : tone === "info"
          ? "border-blue-200 bg-blue-50 text-blue-800"
          : "border-slate-200 bg-surface text-slate-600";
  return (
    <span className={`inline-flex max-w-[260px] items-center gap-2 rounded-full border px-3 py-1 text-[14px] shadow-sm ${toneClass}`}>
      <span className="whitespace-nowrap font-semibold">{label}</span>
      <span className="truncate">{value}</span>
    </span>
  );
}

function JobRow({ job }: { job: SimulationJob }) {
  const { t } = useLanguage();
  const progress = job.status === "done" ? 100 : normalizeProgress(job.progress);
  const statusStyle = job.status === "done"
    ? "bg-emerald-50 text-emerald-700"
    : job.status === "error"
      ? "bg-rose-50 text-rose-700"
      : job.status === "running"
        ? "bg-blue-50 text-blue-700"
        : "bg-slate-100 text-slate-600";

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[14px] font-bold text-slate-800">{job.name || job.params?.name || job.id}</p>
          <p className="mt-0.5 text-[13px] text-slate-500">{job.scenario_result_id ?? job.id}</p>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[12px] font-bold ${statusStyle}`}>{job.status}</span>
      </div>
      <div className="mt-2 h-1.5 rounded-full bg-slate-200">
        <div className="h-1.5 rounded-full bg-blue-500" style={{ width: `${progress}%` }} />
      </div>
      {job.status === "done" && (
        <p className="mt-2 text-[13px] text-emerald-700">
          {t(
            "완료 시점에 대시보드 비교 시나리오로 등록되었습니다. 이후 데이터셋을 교체/복원했다면 목록에서 사라졌을 수 있습니다.",
            "Registered as a comparison scenario at completion. If the dataset was replaced or reset since, it may no longer appear there.",
          )}
        </p>
      )}
      {job.error && (
        // Same #74 fix (2) as the top error banner above -- this per-job
        // history card renders the identical job.error field.
        <p className="mt-2 whitespace-pre-wrap text-[13px] text-rose-700">{job.error}</p>
      )}
    </div>
  );
}

function rowsToNumberMap<T extends Record<string, string>>(
  rows: T[],
  keyField: keyof T,
  valueField: keyof T,
): Record<number, number> {
  const output: Record<number, number> = {};
  for (const row of rows) {
    const rawKey = Number(row[keyField]);
    const rawValue = Number(row[valueField]);
    if (!Number.isFinite(rawKey) || !Number.isFinite(rawValue)) continue;
    if (rawKey <= 0) continue;
    output[Math.round(rawKey)] = rawValue;
  }
  return output;
}

function speedLimitsToSimulationIncidents(rows: SpeedLimitRow[]): SimulationIncident[] {
  return rows.flatMap((row) => {
    const linkId = Number(row.linkId);
    const speedLimit = Number(row.speedLimit);
    const duration = Number(row.duration);
    if (!Number.isFinite(linkId) || !Number.isFinite(speedLimit) || !Number.isFinite(duration)) return [];
    if (linkId <= 0 || speedLimit <= 0 || duration <= 0) return [];
    return [{
      link_id: Math.round(linkId),
      type: "speed_limit",
      closed_lanes: 0,
      total_lanes: 0,
      speed_limit: Math.round(speedLimit),
      duration: Math.round(duration),
    }];
  });
}

function updateSpeedRow(
  id: string,
  field: keyof Omit<SpeedLimitRow, "id" | "linkId">,
  value: string,
  setRows: Dispatch<SetStateAction<SpeedLimitRow[]>>,
) {
  setRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
}

function updateVehicleRow(
  id: string,
  field: keyof Omit<VehicleInputRow, "id" | "linkId">,
  value: string,
  setRows: Dispatch<SetStateAction<VehicleInputRow[]>>,
) {
  setRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
}

function updateSignalRow(
  id: string,
  field: keyof Omit<SignalTimingRow, "id" | "controllerId" | "nodeId" | "nodeName">,
  value: string,
  setRows: Dispatch<SetStateAction<SignalTimingRow[]>>,
) {
  setRows((rows) => rows.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
}

function upsertSpeedLimitRow(rows: SpeedLimitRow[], linkId: string): SpeedLimitRow[] {
  if (rows.some((row) => row.linkId === linkId)) return rows;
  return [...rows, { id: makeRowId(), linkId, speedLimit: "40", duration: "3600" }];
}

function upsertVehicleInputRow(rows: VehicleInputRow[], linkId: string): VehicleInputRow[] {
  if (rows.some((row) => row.linkId === linkId)) return rows;
  return [...rows, { id: makeRowId(), linkId, volume: "1000" }];
}

function upsertSignalTimingRow(
  rows: SignalTimingRow[],
  node: NodeFeature,
  signal: SignalNodeInfo | undefined,
): SignalTimingRow[] {
  const controllerId = String(signal?.sc_no || node.id);
  if (rows.some((row) => row.nodeId === node.id || row.controllerId === controllerId)) return rows;
  return [
    ...rows,
    {
      id: makeRowId(),
      controllerId,
      nodeId: node.id,
      nodeName: node.name,
      cycle: String(signal?.cycle_time || 120),
      offset: "0",
    },
  ];
}

function groupConnections(links: NodeConnection[]): Record<NodeConnection["direction"], NodeConnection[]> {
  return {
    north: links.filter((link) => link.direction === "north"),
    east: links.filter((link) => link.direction === "east"),
    south: links.filter((link) => link.direction === "south"),
    west: links.filter((link) => link.direction === "west"),
  };
}

function directionLabel(direction: NodeConnection["direction"], t: (ko: string, en: string, ja?: string) => string): string {
  if (direction === "north") return t("북", "North");
  if (direction === "east") return t("동", "East");
  if (direction === "south") return t("남", "South");
  return t("서", "West");
}

function normalizeProgress(value: number | undefined): number {
  if (value == null || !Number.isFinite(value)) return 0;
  const percent = value > 1 ? value : value * 100;
  return Math.max(0, Math.min(100, Math.floor(percent)));
}

function buildRunDisabledMessage({
  capabilities,
  datasetReady,
  t,
}: {
  capabilities: SimulationCapabilities | null;
  datasetReady: boolean;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  // Unit #68: an uploaded inpx opens this door exactly like an uploaded zip
  // (datasetReady already covers both -- see hasUploadedDataset above), so
  // reaching here means NOTHING has been uploaded yet at all. Never claims a
  // .vissimpdb-bearing ZIP specifically -- the default run never reads one.
  if (!datasetReady) {
    return t(
      "실행하려면 VISSIM 데이터셋(INPX 파일 또는 프로젝트 ZIP)을 먼저 업로드해야 합니다.",
      "Upload a VISSIM dataset (an INPX file or a project ZIP) before running.",
    );
  }
  // #72: an empty (or unreadable) scenario list no longer disables the run --
  // scenario_id is optional and the default run never reads pdb/Scenarios.
  // The picker itself (RunPanel's `emptyScenarioMessage`) names that state
  // honestly; this function only covers reasons the RUN itself is blocked.
  if (!capabilities?.can_run) {
    return t("실행 환경이 준비되지 않았습니다. VISSIM과 실행 서버 상태를 확인하세요.", "The runtime is not ready. Check VISSIM and the execution server.");
  }
  return "";
}

function publishSimulationRefresh() {
  const revision = String(Date.now());
  window.localStorage.setItem("vissim-simulation-revision", revision);
  window.dispatchEvent(new CustomEvent("vissim-simulation-change", { detail: { revision } }));
}

function publishDatasetChange(dataset: ActiveDataset) {
  window.localStorage.setItem("vissim-dataset-revision", dataset.revision ?? String(Date.now()));
  window.dispatchEvent(new CustomEvent("vissim-dataset-change", { detail: dataset }));
}

function isLocalApiBase(value: string): boolean {
  try {
    const url = new URL(value);
    return ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  } catch {
    return false;
  }
}

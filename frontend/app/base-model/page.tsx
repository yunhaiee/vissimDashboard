"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { useLanguage } from "@/components/LanguageProvider";
import {
  errorTone,
  gehTone,
  toneBadgeClass,
  toneBarClass,
  toneDotClass,
  toneTextClass,
} from "@/lib/validationThresholds";
import BaseValidationMap, { type BaseValidationMapNode } from "@/components/BaseValidationMap";
import VissimDatasetBanner from "@/components/VissimDatasetBanner";
import WorkbookMappingModal from "@/components/WorkbookMappingModal";
import {
  readStoredValidationCategory,
  writeStoredValidationCategory,
} from "@/lib/validationCategoryStorage";
import {
  DEFAULT_VALIDATION_CATEGORY,
  getActiveProfile,
  VALIDATION_CATEGORY_ORDER,
  type ValidationCategory,
  type ValidationProfile,
} from "@/lib/validationProfiles";
import {
  discardBaseWorkbookMapping,
  fetchActiveDataset,
  fetchBaseWorkbook,
  fetchResults,
  fetchScenarios,
  resetBaseWorkbook,
  uploadBaseWorkbook,
  type ActiveDataset,
  type BaseWorkbookCountRow,
  type BaseWorkbookResponse,
  type BaseWorkbookTravelTimeRow,
  type ScenarioMeta,
  type ScenarioResults,
  type WorkbookInspectResponse,
} from "@/lib/api";

type Period = "AM" | "PM";

type CountComparisonRow = {
  key: string;
  period: string;
  nodeId: string;
  nodeName: string;
  movement: string;
  observed: number | null;
  model: number | null;
  diff: number | null;
  pctError: number | null;
  geh: number | null;
  source: "excel" | "scenario" | "missing";
};

type TravelTimePoint = {
  key: string;
  period: string;
  routeId: string;
  routeName: string;
  sampleOrder: number | null;
  timeLabel: string;
  distKm: number | null;
  observed: number | null;
  model: number | null;
  upper: number | null;
  lower: number | null;
  inBand: boolean | null;
  source: "excel" | "scenario" | "missing";
};

type TravelTimeChartRow = {
  label: string;
  dist: number;
  observed: number | null;
  model: number | null;
  upper: number | null;
  lower: number | null;
};

type BandStat = {
  name: string;
  value: number;
  color: string;
};

type CountComparisonGroup = {
  key: string;
  nodeId: string;
  nodeName: string;
  rows: CountComparisonRow[];
  validCount: number;
  missingCount: number;
  averageAbsPctError: number | null;
  worstGeh: number | null;
};

type CountVisualSummary = {
  totalRows: number;
  validRows: number;
  missingRows: number;
  averageAbsPctError: number | null;
  signedBiasPct: number | null;
  gehUnder5Pct: number | null;
  gehUnder10Pct: number | null;
  worstGeh: number | null;
  trafficPass: boolean;
  topIssues: CountComparisonRow[];
};

type CountDirection = "north" | "east" | "south" | "west" | "total";

type CountDirectionGroup = {
  direction: CountDirection;
  rows: CountComparisonRow[];
  observedTotal: number | null;
  modelTotal: number | null;
  diff: number | null;
  pctError: number | null;
  geh: number | null;
  validCount: number;
  missingCount: number;
};

// Category 1/2/3 thresholds live in lib/validationProfiles.ts -- see
// getActiveProfile() below. Do not reintroduce a local constant here.

const SECONDS_PER_MINUTE = 60;
const BASE_WORKBOOK_TEMPLATE_PATH = "/api/validation/base-workbook/template";
const MAX_SELECTED_VALIDATION_NODES = 8;

const EMPTY_WORKBOOK: BaseWorkbookResponse = {
  exists: false,
  filename: "real_counts.xlsx",
  counts: [],
  travel_times: [],
  warnings: [],
};

const DIRECTION_MAP: Record<string, Exclude<CountDirection, "total">> = {
  N: "north",
  E: "east",
  S: "south",
  W: "west",
  NORTH: "north",
  EAST: "east",
  SOUTH: "south",
  WEST: "west",
};

const COUNT_DIRECTION_ORDER: Exclude<CountDirection, "total">[] = ["north", "east", "south", "west"];

const COUNT_DIRECTION_LABELS: Record<CountDirection, { ko: string; en: string; short: string; arrow: string }> = {
  north: { ko: "북", en: "North", short: "N", arrow: "↑" },
  east: { ko: "동", en: "East", short: "E", arrow: "→" },
  south: { ko: "남", en: "South", short: "S", arrow: "↓" },
  west: { ko: "서", en: "West", short: "W", arrow: "←" },
  total: { ko: "전체", en: "Total", short: "T", arrow: "·" },
};

export default function BaseModelPage() {
  const { t } = useLanguage();
  const [scenarios, setScenarios] = useState<ScenarioMeta[]>([]);
  const [period, setPeriod] = useState<Period>("AM");
  const [amScenarioId, setAmScenarioId] = useState("");
  const [pmScenarioId, setPmScenarioId] = useState("");
  const [workbook, setWorkbook] = useState<BaseWorkbookResponse>(EMPTY_WORKBOOK);
  const [scenarioResult, setScenarioResult] = useState<ScenarioResults | null>(null);
  const [loading, setLoading] = useState(true);
  const [resultLoading, setResultLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [resettingWorkbook, setResettingWorkbook] = useState(false);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [datasetRevision, setDatasetRevision] = useState("default");
  const [error, setError] = useState<string | null>(null);
  const [selectedValidationNodeIds, setSelectedValidationNodeIds] = useState<string[]>([]);
  const [activeValidationNodeId, setActiveValidationNodeId] = useState<string | null>(null);
  // Column-mapping flow (non-template workbook layouts): set from the
  // `pending_mapping` block a non-conforming upload's response carries (see
  // uploadBaseWorkbook's stage-then-promote docs above `handleWorkbookUpload`
  // below). That upload never touched the active workbook, so the mapping
  // modal is purely a way forward -- there is nothing to undo on cancel.
  const [mappingInspect, setMappingInspect] = useState<(WorkbookInspectResponse & { warnings: string[] }) | null>(null);
  // Review category (MRWA Cat 1/2/3) -- a property of the REVIEW, not of the
  // loaded dataset (see lib/validationCategoryStorage.ts). Starts at the
  // default (Category 1, today's behavior) and is swapped in from storage
  // by the effect below rather than a lazy useState initializer, so the
  // client's first render matches SSR output and only updates after mount
  // (same convention the dashboard page follows for its own stored
  // selection -- see readStoredScenarioSelection in app/dashboard/page.tsx).
  const [category, setCategory] = useState<ValidationCategory>(DEFAULT_VALIDATION_CATEGORY);
  const requestSerial = useRef(0);

  useEffect(() => {
    const stored = readStoredValidationCategory();
    if (stored) setCategory(stored);
  }, []);

  const handleCategoryChange = useCallback((next: ValidationCategory) => {
    setCategory(next);
    writeStoredValidationCategory(next);
  }, []);

  // Deliberately NOT reset by handleDatasetChange -- a dataset swap changes
  // which model is being validated, not which MRWA tier the review was
  // scoped into. See lib/validationCategoryStorage.ts for the same
  // reasoning applied to persistence.
  const activeProfile = useMemo(() => getActiveProfile(category), [category]);

  const selectedScenarioId = period === "AM" ? amScenarioId : pmScenarioId;
  const selectedScenario = scenarios.find((scenario) => scenario.id === selectedScenarioId) ?? null;

  useEffect(() => {
    let cancelled = false;

    Promise.allSettled([fetchActiveDataset(), fetchBaseWorkbook()]).then(async ([datasetOut, workbookOut]) => {
      if (cancelled) return;

      const uploadedDataset = datasetOut.status === "fulfilled" && datasetOut.value.source === "uploaded";
      setActiveDataset(datasetOut.status === "fulfilled" ? datasetOut.value : null);
      if (uploadedDataset) {
        try {
          const items = await fetchScenarios();
          if (cancelled) return;
          setScenarios(items);
          setAmScenarioId((current) => current || pickDefaultScenario(items, "AM"));
          setPmScenarioId((current) => current || pickDefaultScenario(items, "PM"));
        } catch {
          if (cancelled) return;
          setScenarios([]);
          setAmScenarioId("");
          setPmScenarioId("");
          setError(t("시나리오 목록을 불러오지 못했습니다.", "Failed to load scenario list."));
        }
      } else {
        setScenarios([]);
        setAmScenarioId("");
        setPmScenarioId("");
        setPeriod("AM");
      }

      if (workbookOut.status === "fulfilled") {
        setWorkbook(workbookOut.value);
      } else {
        setWorkbook(EMPTY_WORKBOOK);
      }

      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [t]);

  useEffect(() => {
    const reqId = ++requestSerial.current;
    let cancelled = false;

    Promise.resolve().then(() => {
      if (!selectedScenarioId) {
        if (!cancelled && requestSerial.current === reqId) {
          setScenarioResult(null);
          setResultLoading(false);
        }
        return;
      }

      setResultLoading(true);
      fetchResults(selectedScenarioId)
        .then((result) => {
          if (!cancelled && requestSerial.current === reqId) {
            setScenarioResult(result);
            setResultLoading(false);
          }
        })
        .catch(() => {
          if (!cancelled && requestSerial.current === reqId) {
            setScenarioResult(null);
            setResultLoading(false);
            setError(t("선택한 베이스 시나리오 결과를 불러오지 못했습니다.", "Failed to load selected base scenario results."));
          }
        });
    });

    return () => {
      cancelled = true;
    };
  }, [datasetRevision, selectedScenarioId, t]);

  const countRows = useMemo(
    () => workbook.counts.filter((row) => row.period === period),
    [period, workbook.counts],
  );

  // Only offer the time periods the uploaded workbook actually contains. Before
  // an upload (or for older payloads without `periods`) fall back to AM/PM.
  const availablePeriods = useMemo<Period[]>(() => {
    const fromWorkbook = (workbook.periods ?? []).filter((value): value is Period => value === "AM" || value === "PM");
    return fromWorkbook.length ? fromWorkbook : ["AM", "PM"];
  }, [workbook.periods]);

  // Keep the selected period valid when a newly uploaded workbook lacks it.
  useEffect(() => {
    if (!availablePeriods.includes(period)) {
      setPeriod(availablePeriods[0]);
    }
  }, [availablePeriods, period]);

  const countComparisons = useMemo(
    () => buildCountComparisons(countRows, scenarioResult),
    [countRows, scenarioResult],
  );

  // Show only the selected analysis period's routes (the "분석 시간대" toggle).
  const travelPoints = useMemo(
    () => buildTravelTimePoints(
      workbook.travel_times.filter((row) => row.period === period),
      scenarioResult,
      period,
      activeProfile.travelTolerancePct,
    ),
    [scenarioResult, workbook.travel_times, period, activeProfile],
  );

  const countStats = useMemo(() => summarizeCounts(countComparisons, activeProfile), [countComparisons, activeProfile]);
  const countVisualSummary = useMemo(() => summarizeCountVisuals(countComparisons, activeProfile), [countComparisons, activeProfile]);
  const countGroups = useMemo(() => groupCountComparisonsByNode(countComparisons), [countComparisons]);
  const validationMapNodes = useMemo(() => buildValidationMapNodes(countGroups), [countGroups]);
  const validationGroupsById = useMemo(
    () => new Map(countGroups.map((group) => [group.nodeId, group])),
    [countGroups],
  );
  const effectiveSelectedValidationNodeIds = useMemo(
    () => selectedValidationNodeIds.filter((nodeId) => validationGroupsById.has(nodeId)),
    [selectedValidationNodeIds, validationGroupsById],
  );
  const effectiveActiveValidationNodeId = useMemo(
    () =>
      activeValidationNodeId && validationGroupsById.has(activeValidationNodeId)
        ? activeValidationNodeId
        : countGroups[0]?.nodeId ?? null,
    [activeValidationNodeId, countGroups, validationGroupsById],
  );
  const selectedValidationNodeNames = useMemo(
    () =>
      effectiveSelectedValidationNodeIds
        .map((nodeId) => validationGroupsById.get(nodeId)?.nodeName)
        .filter((name): name is string => Boolean(name)),
    [effectiveSelectedValidationNodeIds, validationGroupsById],
  );
  const travelStats = useMemo(() => summarizeTravelTimes(travelPoints, activeProfile), [travelPoints, activeProfile]);
  const travelRoutes = useMemo(() => groupTravelPointsByRoute(travelPoints), [travelPoints]);
  const pieData = useMemo<BandStat[]>(
    () => [
      { name: "GEH < 5", value: countStats.under5, color: "#74ac80" },
      { name: "GEH 5-10", value: countStats.between5And10, color: "#b5b3ab" },
      { name: "GEH > 10", value: countStats.over10, color: "#dc8373" },
    ],
    [countStats],
  );

  const flowBandStats = useMemo(() => summarizeFlowBands(countComparisons), [countComparisons]);
  const rSquaredData = useMemo(() => computeRSquared(countComparisons), [countComparisons]);
  // Per-row tolerance widths (±100 veh/h / ±15% / ±400 veh/h) are
  // category-invariant per Table 5-13 -- only the required aggregate pass
  // rate shown alongside them varies by category. No pass/fail badge is
  // rendered for these bands yet (see base-model page report gap note);
  // this only threads the active category's TARGET number into the label
  // that already exists.
  const flowBandPies = useMemo<Array<{ key: "low" | "mid" | "high"; title: string; criterion: string; data: BandStat[] }>>(
    () => [
      {
        key: "low",
        title: t("저유량 (< 700 veh/h)", "Low flow (< 700 veh/h)"),
        criterion: t(
          `기준: 차이 ±${activeProfile.flowLowToleranceVph} veh/h (${activeProfile.nameKo} 목표 통과율 ≥${activeProfile.flowLowWithinPct}%)`,
          `Criterion: diff ±${activeProfile.flowLowToleranceVph} veh/h (${activeProfile.nameEn} target pass rate ≥${activeProfile.flowLowWithinPct}%)`,
        ),
        data: [
          { name: t("기준 충족", "Within"), value: flowBandStats.low.pass, color: "#74ac80" },
          { name: t("기준 초과", "Exceeds"), value: flowBandStats.low.fail, color: "#dc8373" },
        ],
      },
      {
        key: "mid",
        title: t("중유량 (700–2,700 veh/h)", "Mid flow (700–2,700 veh/h)"),
        criterion: t(
          `기준: 차이 ±${activeProfile.flowMidTolerancePct}% (${activeProfile.nameKo} 목표 통과율 ≥${activeProfile.flowMidWithinPct}%)`,
          `Criterion: diff ±${activeProfile.flowMidTolerancePct}% (${activeProfile.nameEn} target pass rate ≥${activeProfile.flowMidWithinPct}%)`,
        ),
        data: [
          { name: t("기준 충족", "Within"), value: flowBandStats.mid.pass, color: "#74ac80" },
          { name: t("기준 초과", "Exceeds"), value: flowBandStats.mid.fail, color: "#dc8373" },
        ],
      },
      {
        key: "high",
        title: t("고유량 (> 2,700 veh/h)", "High flow (> 2,700 veh/h)"),
        criterion: t(
          `기준: 차이 ±${activeProfile.flowHighToleranceVph} veh/h (${activeProfile.nameKo} 목표 통과율 ≥${activeProfile.flowHighWithinPct}%)`,
          `Criterion: diff ±${activeProfile.flowHighToleranceVph} veh/h (${activeProfile.nameEn} target pass rate ≥${activeProfile.flowHighWithinPct}%)`,
        ),
        data: [
          { name: t("기준 충족", "Within"), value: flowBandStats.high.pass, color: "#74ac80" },
          { name: t("기준 초과", "Exceeds"), value: flowBandStats.high.fail, color: "#dc8373" },
        ],
      },
    ],
    [flowBandStats, activeProfile, t],
  );

  const handleValidationNodeClick = useCallback((nodeId: string) => {
    setActiveValidationNodeId(nodeId);
    setSelectedValidationNodeIds((current) => {
      if (current.includes(nodeId)) return current;
      return [nodeId, ...current].slice(0, MAX_SELECTED_VALIDATION_NODES);
    });
  }, []);

  const handleValidationNodeRemove = useCallback((nodeId: string) => {
    const nextSelection = selectedValidationNodeIds.filter((item) => item !== nodeId);
    setSelectedValidationNodeIds(nextSelection);
    setActiveValidationNodeId((active) => (active === nodeId ? nextSelection[0] ?? null : active));
  }, [selectedValidationNodeIds]);

  const handleWorkbookUpload = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    setUploading(true);
    setError(null);
    try {
      // Stage-then-promote (backend/routers/validation.py upload_base_workbook):
      // a file shaped like real_counts.xlsx (sheets literally named Counts/
      // TravelTimes) is promoted to the active workbook and this response IS
      // that new active state -- imports exactly as it always has, zero extra
      // steps. A non-conforming file is NEVER promoted: the backend leaves
      // the active workbook untouched and this response describes THAT
      // unchanged state instead, with `pending_mapping` carrying the staged
      // upload's token + inspect data (already staged server-side -- no
      // second upload needed) so the column-mapping modal can offer a way
      // forward. Either way `nextWorkbook`'s top-level fields are always the
      // correct thing to render -- never the rejected upload's garbage parse.
      const nextWorkbook = await uploadBaseWorkbook(file);
      setWorkbook(nextWorkbook);
      if (nextWorkbook.pending_mapping) {
        setMappingInspect(nextWorkbook.pending_mapping);
      }
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : String(uploadError));
    } finally {
      setUploading(false);
    }
  }, []);

  const handleMappingClose = useCallback(() => {
    // The active workbook was never touched by a staged-but-unmapped upload
    // (stage-then-promote), so closing is safe by construction -- this is
    // just best-effort cleanup of the staged copy, not an undo.
    if (mappingInspect) {
      discardBaseWorkbookMapping(mappingInspect.upload_token).catch(() => {});
    }
    setMappingInspect(null);
  }, [mappingInspect]);

  const handleMappingImported = useCallback((nextWorkbook: BaseWorkbookResponse) => {
    setWorkbook(nextWorkbook);
    setMappingInspect(null);
  }, []);

  const handleWorkbookReset = useCallback(async () => {
    setResettingWorkbook(true);
    setError(null);
    try {
      const nextWorkbook = await resetBaseWorkbook();
      setWorkbook(nextWorkbook);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : String(resetError));
    } finally {
      setResettingWorkbook(false);
    }
  }, []);

  const handleDatasetChange = useCallback((dataset?: ActiveDataset) => {
    const shouldLoadScenarioSelection = dataset?.source === "uploaded";
    setActiveDataset(dataset ?? null);
    setDatasetRevision(dataset?.revision ?? String(Date.now()));
    setScenarioResult(null);
    setLoading(true);
    setError(null);

    Promise.allSettled([
      shouldLoadScenarioSelection ? fetchScenarios() : Promise.resolve<ScenarioMeta[]>([]),
      fetchBaseWorkbook(),
    ]).then(([scenarioOut, workbookOut]) => {
      if (!shouldLoadScenarioSelection) {
        setScenarios([]);
        setAmScenarioId("");
        setPmScenarioId("");
        setPeriod("AM");
      } else if (scenarioOut.status === "fulfilled") {
        const items = scenarioOut.value;
        setScenarios(items);
        setAmScenarioId((current) =>
          current && items.some((scenario) => scenario.id === current) ? current : pickDefaultScenario(items, "AM"),
        );
        setPmScenarioId((current) =>
          current && items.some((scenario) => scenario.id === current) ? current : pickDefaultScenario(items, "PM"),
        );
      } else {
        setScenarios([]);
        setAmScenarioId("");
        setPmScenarioId("");
        setError(t("시나리오 목록을 불러오지 못했습니다.", "Failed to load scenario list."));
      }

      if (workbookOut.status === "fulfilled") {
        setWorkbook(workbookOut.value);
      } else {
        setWorkbook(EMPTY_WORKBOOK);
      }

      setLoading(false);
    });
  }, [t]);

  useEffect(() => {
    const publishAssistantContext = () => {
      window.dispatchEvent(
        new CustomEvent("vissim:page-context", {
          detail: {
            route: "/base-model",
            label: "Base model validation context",
            selectedScenarioIds: selectedScenarioId ? [selectedScenarioId] : [],
            selectedScenarioNames: selectedScenario ? [selectedScenario.name] : [],
            selectedNodeIds: effectiveSelectedValidationNodeIds,
            selectedNodeNames: selectedValidationNodeNames,
            dataset: activeDataset
              ? {
                  filename: activeDataset.filename,
                  source: activeDataset.source,
                  revision: activeDataset.revision,
                }
              : null,
            notes: [
              `Period ${period}`,
              workbook.exists
                ? `${workbook.filename}: ${workbook.counts.length} counts, ${workbook.travel_times.length} travel rows`
                : "Validation workbook is not connected.",
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
  }, [activeDataset, effectiveSelectedValidationNodeIds, period, selectedScenario, selectedScenarioId, selectedValidationNodeNames, workbook]);

  return (
    <div className="mx-auto w-full max-w-[96rem] space-y-6 pb-16">
      {mappingInspect && (
        <WorkbookMappingModal inspectResult={mappingInspect} onClose={handleMappingClose} onImported={handleMappingImported} />
      )}
      <section className="px-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight text-white">
              {t("베이스모델 검증", "Base Model Validation")}
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              {t("AM/PM 기본 시나리오의 교통량, GEH, 통행시간 검증 결과를 확인합니다.", "Validate AM/PM base scenarios with traffic volume, GEH, and travel time checks.")}
            </p>
          </div>
          <div className="flex flex-wrap items-start justify-end gap-3">
            <div className="flex min-h-[32px] flex-wrap items-center gap-2">
            {loading || resultLoading ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-600">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
                {t("로딩 중", "Loading")}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-600">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                {selectedScenario?.name ?? t("시나리오 선택 필요", "Choose scenario")}
              </span>
            )}
            </div>
          </div>
        </div>

        <div className="mt-4 grid gap-3 lg:grid-cols-[1.6fr_1fr]">
          <div className="grid gap-3 sm:grid-cols-2">
            <VissimDatasetBanner onDatasetChange={handleDatasetChange} fullWidth className="h-full" />
            <div className="h-full rounded-xl border border-slate-200 p-5">
              <p className="text-sm font-semibold text-slate-600">{t("실측데이터(엑셀)", "Observed Data (Excel)")}</p>
              <p className="mt-1 truncate text-xs leading-relaxed text-slate-500">
                {workbook.exists
                  ? workbook.filename
                  : t("표준 real_counts.xlsx가 아직 연결되지 않았습니다.", "No standard real_counts.xlsx is connected yet.")}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <a
                  href={BASE_WORKBOOK_TEMPLATE_PATH}
                  className="ui-button-ghost inline-flex items-center px-3 py-2 text-xs"
                >
                  {t("템플릿 다운로드", "Download Template")}
                </a>
                <label className="ui-button-primary inline-flex cursor-pointer items-center px-3 py-2 text-xs">
                  {uploading ? t("업로드 중", "Uploading") : t("Excel 업로드", "Upload Excel")}
                  <input type="file" accept=".xlsx" className="hidden" onChange={handleWorkbookUpload} disabled={uploading || resettingWorkbook} />
                </label>
                <button
                  type="button"
                  className="ui-button-danger inline-flex items-center px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={handleWorkbookReset}
                  disabled={!workbook.exists || uploading || resettingWorkbook}
                >
                  {resettingWorkbook ? t("초기화 중", "Resetting") : t("초기화", "Reset")}
                </button>
              </div>
              {workbook.exists && (
                <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-2 border-t border-slate-200 pt-3.5 text-xs">
                  <div className="flex items-center justify-between">
                    <dt className="text-slate-500">{t("교통량 행", "Count rows")}</dt>
                    <dd className="font-medium text-slate-700">{workbook.counts.length}</dd>
                  </div>
                  <div className="flex items-center justify-between">
                    <dt className="text-slate-500">{t("통행시간 행", "Travel rows")}</dt>
                    <dd className="font-medium text-slate-700">{workbook.travel_times.length}</dd>
                  </div>
                  {workbook.sheets && workbook.sheets.length > 0 && (
                    <div className="col-span-2 flex items-center justify-between">
                      <dt className="text-slate-500">{t("시트", "Sheets")}</dt>
                      <dd className="truncate font-medium text-slate-700" title={workbook.sheets.join(", ")}>
                        {workbook.sheets.join(", ")}
                      </dd>
                    </div>
                  )}
                  <div className="col-span-2 flex items-center justify-between border-t border-slate-200/70 pt-1.5">
                    <dt className="text-slate-500">{t("상태", "Status")}</dt>
                    <dd>
                      <span className="inline-flex items-center gap-1 font-medium text-slate-700">
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${workbook.warnings.length > 0 ? "bg-amber-500" : "bg-emerald-500"}`}
                          aria-hidden
                        />
                        {workbook.warnings.length > 0
                          ? t(`경고 ${workbook.warnings.length}건`, `${workbook.warnings.length} warning${workbook.warnings.length > 1 ? "s" : ""}`, `警告 ${workbook.warnings.length}件`)
                          : t("정상", "Healthy")}
                      </span>
                    </dd>
                  </div>
                </dl>
              )}
            </div>
          </div>
          <div className="h-full rounded-xl border border-slate-200 p-5">
            <p className="text-sm font-semibold text-slate-600">{t("분석 설정", "Analysis Setup")}</p>
            <div className="mt-4 grid gap-3">
            <ControlField label={t("BaseAM Model", "BaseAM Model")}>
              <select className="ui-select" value={amScenarioId} onChange={(event) => setAmScenarioId(event.target.value)} disabled={scenarios.length === 0}>
                {scenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name} ({scenario.id})
                  </option>
                ))}
              </select>
            </ControlField>
            <ControlField label={t("BasePM Model", "BasePM Model")}>
              <select className="ui-select" value={pmScenarioId} onChange={(event) => setPmScenarioId(event.target.value)} disabled={scenarios.length === 0}>
                {scenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name} ({scenario.id})
                  </option>
                ))}
              </select>
            </ControlField>
            <ControlField label={t("분석 시간대", "Analysis Period")}>
              <select className="ui-select" value={period} onChange={(event) => setPeriod(event.target.value as Period)}>
                {availablePeriods.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </ControlField>
            </div>
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          {error}
        </div>
      )}

      {workbook.warnings.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-700">
          {workbook.warnings.join(" ")}
        </div>
      )}

      <section>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionHeader title={t("1. 교통량 검증", "1. Traffic Count Validation")} />
          <ValidationCategorySelector category={category} onChange={handleCategoryChange} t={t} />
        </div>
        <CountComparisonOverview
          groups={countGroups}
          mapNodes={validationMapNodes}
          summary={countVisualSummary}
          selectedNodeIds={effectiveSelectedValidationNodeIds}
          activeNodeId={effectiveActiveValidationNodeId}
          datasetRevision={datasetRevision}
          onNodeClick={handleValidationNodeClick}
          onNodeRemove={handleValidationNodeRemove}
          t={t}
        />
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <ValidationPieCard
            title={t("GEH 분포", "GEH Distribution")}
            criterion={t(
              `기준(${activeProfile.nameKo}): GEH < 5 비율 ≥ ${activeProfile.gehUnder5Pct}%, GEH < 10 비율 ≥ ${activeProfile.gehUnder10Pct}%`,
              `Target (${activeProfile.nameEn}): GEH < 5 share ≥ ${activeProfile.gehUnder5Pct}%, GEH < 10 share ≥ ${activeProfile.gehUnder10Pct}%`,
            )}
            data={pieData}
            footer={
              countStats.hasData
                ? `${t("전체", "Total")} ${countStats.validCount} · ${t("GEH<5", "GEH<5")} ${formatPct(countStats.under5Pct)}`
                : undefined
            }
            empty={!countStats.hasData}
            emptyText={t("GEH를 계산할 수 있는 데이터가 없습니다.", "No data available for GEH calculation.")}
            t={t}
          />
          {flowBandPies.map((band) => {
            const total = band.data.reduce((acc, x) => acc + x.value, 0);
            const passCount = band.data[0]?.value ?? 0;
            const passPct = total > 0 ? (passCount / total) * 100 : null;
            return (
              <ValidationPieCard
                key={band.key}
                title={band.title}
                criterion={band.criterion}
                data={band.data}
                footer={
                  total > 0
                    ? `${t("전체", "Total")} ${total} · ${t("충족", "Within")} ${passPct == null ? "-" : formatPct(passPct)}`
                    : undefined
                }
                empty={total === 0}
                emptyText={t("해당 유량대 데이터 없음", "No data in this flow band")}
                t={t}
              />
            );
          })}
        </div>
        <div className="mt-4 rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-bold text-slate-800">
                {t(`${period} 첨두 R² 차트`, `${period} Peak Hour R² Chart`, `${period} ピーク時 R² チャート`)}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-500">
                {t("교차로 방향별 실측값(Observed) vs 모델값(Modelled)", "Observed vs Modelled traffic by direction")}
              </p>
            </div>
            {rSquaredData.slope != null && rSquaredData.r2 != null && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-right font-mono text-[11px] text-slate-700">
                <p>y = {rSquaredData.slope.toFixed(4)}x</p>
                <p>R² = {rSquaredData.r2.toFixed(4)}</p>
                {/* Target only -- no pass/fail evaluated against this yet, see report gap note. */}
                <p className="text-slate-400">
                  {t(`목표(${activeProfile.nameKo}): R² > ${activeProfile.rSquaredMin}`, `Target (${activeProfile.nameEn}): R² > ${activeProfile.rSquaredMin}`)}
                </p>
              </div>
            )}
          </div>
          {rSquaredData.points.length === 0 ? (
            <p className="py-10 text-center text-xs text-slate-500">
              {t("R² 차트를 그릴 수 있는 데이터가 없습니다.", "No data available for R² chart.")}
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={420}>
              <ScatterChart margin={{ top: 16, right: 32, left: 16, bottom: 28 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#33332f" />
                <XAxis
                  type="number"
                  dataKey="x"
                  name={t("실측", "Observed")}
                  domain={[0, rSquaredData.axisMax]}
                  tick={{ fontSize: 11 }}
                  label={{ value: "Observed", position: "insideBottom", offset: -10, style: { fontSize: 11, fill: "#898781" } }}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  name={t("모델", "Modelled")}
                  domain={[0, rSquaredData.axisMax]}
                  tick={{ fontSize: 11 }}
                  label={{ value: "Modelled", angle: -90, position: "insideLeft", offset: 10, style: { fontSize: 11, fill: "#898781" } }}
                />
                <ZAxis range={[36, 36]} />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  formatter={(value) => formatNumber(value == null ? null : Number(value), 0)}
                  labelFormatter={() => ""}
                />
                {rSquaredData.slope != null && (
                  <ReferenceLine
                    segment={[
                      { x: 0, y: 0 },
                      { x: rSquaredData.axisMax, y: rSquaredData.axisMax * rSquaredData.slope },
                    ]}
                    stroke="#e4574d"
                    strokeDasharray="4 4"
                    strokeWidth={1.2}
                    ifOverflow="extendDomain"
                  />
                )}
                <Scatter data={rSquaredData.points} fill="#ee9b5c" stroke="#c96442" strokeWidth={1} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
        </div>
        <p className="mt-3 rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
          {t("VISSIM 통행시간 측정 구간이 관측 지체 구간과 일치하는지 확인하세요.", "Please check the travel time measurement sections defined in VISSIM are consistent with the observed delay.")}
        </p>
      </section>

      <section>
        <SectionHeader
          title={t("2. 통행시간 분석", "2. Travel Time Validation")}
          subtitle={t(`Excel TravelTimes 시트에 정의된 루트별 관측값, 예측값, ±${activeProfile.travelTolerancePct}% 허용 경계를 비교합니다. (${activeProfile.nameKo} 기준: 통과율 ≥${activeProfile.travelWithinPct}%)`, `Compare observed, predicted, and ±${activeProfile.travelTolerancePct}% tolerance lines for routes defined in the TravelTimes sheet. (${activeProfile.nameEn} target: pass rate ≥${activeProfile.travelWithinPct}%)`, `Excel TravelTimes シートで定義されたルート別の観測値・予測値・±${activeProfile.travelTolerancePct}% 許容境界を比較します。`)}
        />
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {travelRoutes.length === 0 && (
            <div className="xl:col-span-2">
              <EmptyPanel text={t("TravelTimes 데이터가 없습니다.", "No TravelTimes data is available.", "TravelTimes データがありません。")} />
            </div>
          )}
          {travelRoutes.map((route) => (
            <div key={route.key} className="rounded-xl border border-slate-300/70 bg-surface/92 p-5 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-bold text-slate-900">{route.name}</h3>
                  <p className="text-[11px] text-slate-500">{t("루트", "Route")} {route.routeId}</p>
                </div>
                <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${route.allInBand ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
                  {route.inBandCount}/{route.validCount} {t("허용", "in band")}
                </span>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={pointsToCumulativeChartRows(route.points)} margin={{ top: 8, right: 24, left: 0, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#33332f" />
                  <XAxis
                    dataKey="dist"
                    type="number"
                    domain={[0, "dataMax"]}
                    tick={{ fontSize: 11 }}
                    tickFormatter={(value) => `${formatNumber(Number(value), 2)}`}
                    label={{
                      value: t("시작점부터의 거리(km)", "Distance from start (km)"),
                      position: "insideBottom",
                      offset: -4,
                      style: { fontSize: 11, fill: "#898781" },
                    }}
                  />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    tickFormatter={(value) => formatNumber(Number(value), 1)}
                    label={{
                      value: t("누적 통행시간(분)", "Cumulative travel time (min)"),
                      angle: -90,
                      position: "insideLeft",
                      style: { fontSize: 11, fill: "#898781" },
                    }}
                  />
                  <Tooltip formatter={(value) => `${formatNumber(Number(value), 1)} min`} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Line type="monotone" dataKey="observed" name={t("실측", "Observed")} stroke="#6da7ec" strokeWidth={2.4} dot={{ r: 3 }} connectNulls />
                  <Line type="monotone" dataKey="model" name={t("예측", "Predicted")} stroke="#e4574d" strokeWidth={2.4} dot={{ r: 3 }} connectNulls />
                  <Line type="monotone" dataKey="upper" name={`+${activeProfile.travelTolerancePct}%`} stroke="#fafaf9" strokeDasharray="6 6" dot={false} connectNulls />
                  <Line type="monotone" dataKey="lower" name={`-${activeProfile.travelTolerancePct}%`} stroke="#fafaf9" strokeDasharray="6 6" dot={false} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ))}
        </div>
      </section>

      <section>
        <SectionHeader
          title={t("3. 결과 분석", "3. Result Analysis")}
          subtitle={t("교통량, GEH, 통행시간 검증 결과를 종합합니다.", "Summarize traffic volume, GEH, and travel time validation outcomes.")}
        />
        <div className={`mt-4 rounded-xl border px-5 py-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)] ${analysisPanelClass(countStats, travelStats)}`}>
          <p className="text-sm leading-relaxed">
            {buildAnalysisText({ countStats, travelStats, profile: activeProfile, t })}
          </p>
        </div>
      </section>
    </div>
  );
}

function ControlField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-slate-600">{label}</span>
      {children}
    </label>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div>
      <h2 className="text-[15px] font-semibold text-white">{title}</h2>
      {subtitle && <p className="mt-0.5 text-sm text-slate-600">{subtitle}</p>}
    </div>
  );
}

// Review-context control, not dataset state: switches which MRWA Category
// 1/2/3 threshold set (lib/validationProfiles.ts) every pass/fail badge and
// target label on this page reads from. Segmented-control idiom copied from
// the view-mode switcher in components/NodeDirectionTable.tsx to match the
// existing palette (globals.css inverts the slate ramp -- bg-slate-800 +
// text-ink is that file's "selected" pairing).
function ValidationCategorySelector({
  category,
  onChange,
  t,
}: {
  category: ValidationCategory;
  onChange: (next: ValidationCategory) => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const activeProfile = getActiveProfile(category);
  return (
    <div className="flex flex-col items-start gap-1 sm:items-end">
      <span className="text-[11px] font-semibold text-slate-500">
        {t("검증 기준 (MRWA)", "Validation Category (MRWA)")}
      </span>
      <div
        className="flex items-center rounded-lg border border-slate-200 bg-surface p-0.5 text-xs shadow-sm"
        title={activeProfile.citation}
      >
        {VALIDATION_CATEGORY_ORDER.map((key) => {
          const profile = getActiveProfile(key);
          return (
            <button
              key={key}
              type="button"
              onClick={() => onChange(key)}
              className={`rounded-md px-3 py-1 font-medium transition-colors ${
                category === key
                  ? "bg-slate-800 text-ink shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {t(profile.nameKo, profile.nameEn)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function GehFormulaExplanation({ t }: { t: (ko: string, en: string, ja?: string) => string }) {
  return (
    <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="flex justify-center rounded-lg bg-surface px-3 py-3 text-slate-900 shadow-[inset_0_0_0_1px_rgba(148,163,184,0.25)]">
        <svg
          className="h-[58px] w-[230px] max-w-full overflow-visible"
          viewBox="0 0 230 58"
          role="img"
          aria-label={t("GEH는 2 곱하기 모델 교통량 빼기 실측 교통량의 제곱을 모델 교통량 더하기 실측 교통량으로 나눈 값의 제곱근입니다.", "GEH is the square root of two times modeled volume minus counted volume squared, divided by modeled volume plus counted volume.")}
        >
          <text x="6" y="36" fill="currentColor" fontFamily="Georgia, 'Times New Roman', serif" fontSize="20" fontStyle="italic">
            GEH
          </text>
          <text x="58" y="36" fill="currentColor" fontFamily="Georgia, 'Times New Roman', serif" fontSize="20">
            =
          </text>
          <path d="M78 31 L84 42 L94 13 L218 13" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.1" />
          <line x1="110" x2="203" y1="32" y2="32" stroke="currentColor" strokeLinecap="round" strokeWidth="1.4" />
          <text x="123" y="26" fill="currentColor" fontFamily="Georgia, 'Times New Roman', serif" fontSize="15">
            2(M - C)
          </text>
          <text x="181" y="18" fill="currentColor" fontFamily="Georgia, 'Times New Roman', serif" fontSize="10">
            2
          </text>
          <text x="139" y="49" fill="currentColor" fontFamily="Georgia, 'Times New Roman', serif" fontSize="15">
            M + C
          </text>
        </svg>
      </div>
      <dl className="mt-3 space-y-1.5 text-[11px] leading-relaxed text-slate-600">
        <div className="flex gap-2">
          <dt className="min-w-8 font-bold text-slate-800">M</dt>
          <dd>{t("모델링으로 산출한 예측 교통량", "Modeled volume estimated by the simulation")}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="min-w-8 font-bold text-slate-800">C/O</dt>
          <dd>{t("현장에서 관측한 실제 교통량", "Counted or observed field volume")}</dd>
        </div>
      </dl>
    </div>
  );
}

function CriterionRow({
  label,
  value,
  passed,
  t,
}: {
  label: string;
  value: string;
  passed: boolean | null;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const badge =
    passed == null
      ? "bg-slate-100 text-slate-500"
      : passed
        ? "bg-emerald-100 text-emerald-700"
        : "bg-rose-100 text-rose-700";
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
      <div>
        <div className="font-semibold">{label}</div>
        <div className="text-xs text-slate-500">{value}</div>
      </div>
      <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${badge}`}>
        {passed == null ? "-" : passed ? t("통과", "PASS") : t("확인", "CHECK")}
      </span>
    </div>
  );
}

function ValidationPieCard({
  title,
  criterion,
  data,
  footer,
  empty,
  emptyText,
  t,
}: {
  title: string;
  criterion: string;
  data: BandStat[];
  footer?: string;
  empty: boolean;
  emptyText: string;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  return (
    <div className="rounded-xl border border-slate-300/70 bg-surface/92 p-4 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      <div className="mb-2">
        <p className="text-xs font-bold text-slate-800">{title}</p>
        <p className="mt-0.5 text-[11px] text-slate-500">{criterion}</p>
      </div>
      {empty ? (
        <p className="py-10 text-center text-xs text-slate-500">{emptyText}</p>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" innerRadius={48} outerRadius={76} paddingAngle={2}>
                {data.map((entry) => (
                  <Cell key={entry.name} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip formatter={(value) => `${value} ${t("지점", "points")}`} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          </ResponsiveContainer>
          {footer && <p className="mt-1 text-center text-[11px] text-slate-600">{footer}</p>}
        </>
      )}
    </div>
  );
}

function EmptyPanel({ text }: { text: string }) {
  return (
    <div className="flex min-h-[220px] items-center justify-center rounded-xl border border-slate-300/70 bg-surface/92 p-6 text-center text-sm text-slate-500 shadow-[0_10px_22px_rgba(15,23,42,0.08)]">
      {text}
    </div>
  );
}

function CountComparisonOverview({
  groups,
  mapNodes,
  summary,
  selectedNodeIds,
  activeNodeId,
  datasetRevision,
  onNodeClick,
  onNodeRemove,
  t,
}: {
  groups: CountComparisonGroup[];
  mapNodes: BaseValidationMapNode[];
  summary: CountVisualSummary;
  selectedNodeIds: string[];
  activeNodeId: string | null;
  datasetRevision: string;
  onNodeClick: (nodeId: string, nodeName: string) => void;
  onNodeRemove: (nodeId: string) => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  if (summary.totalRows === 0) {
    return (
      <div className="mt-4">
        <EmptyPanel text={t("선택한 시간대의 Counts 데이터가 없습니다.", "No Counts data is available for the selected period.")} />
      </div>
    );
  }

  return (
    <div className="mt-4 space-y-4">
      <div className="relative h-[540px] overflow-hidden rounded-xl border border-slate-300/70 bg-ink shadow-[0_18px_34px_rgba(15,23,42,0.16)] xl:h-[62vh]">
        <BaseValidationMap
          nodes={mapNodes}
          selectedNodeIds={selectedNodeIds}
          activeNodeId={activeNodeId}
          datasetRevision={datasetRevision}
          onNodeClick={onNodeClick}
        />
      </div>

    </div>
  );
}

function ValidationNodeInspector({
  groups,
  selectedNodeIds,
  activeNodeId,
  onNodeClick,
  onNodeRemove,
  t,
}: {
  groups: CountComparisonGroup[];
  selectedNodeIds: string[];
  activeNodeId: string | null;
  onNodeClick: (nodeId: string, nodeName: string) => void;
  onNodeRemove: (nodeId: string) => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const groupsById = useMemo(() => new Map(groups.map((group) => [group.nodeId, group])), [groups]);
  const selectedGroups = selectedNodeIds
    .map((nodeId) => groupsById.get(nodeId))
    .filter((group): group is CountComparisonGroup => Boolean(group));
  const activeGroup = (activeNodeId ? groupsById.get(activeNodeId) : null) ?? selectedGroups[0] ?? null;
  const topIssueGroups = groups.slice(0, 20);
  const atSelectionCap = selectedGroups.length >= MAX_SELECTED_VALIDATION_NODES;

  return (
    <aside className="flex h-[540px] min-h-0 flex-col overflow-hidden rounded-xl border border-slate-300/70 bg-surface/94 p-4 shadow-[0_10px_24px_rgba(15,23,42,0.08)] xl:h-[62vh]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
            {t("비교 선택", "Comparison Selection")}
          </p>
          <h3 className="mt-1 text-base font-bold text-slate-900 tabular-nums">
            <span className={atSelectionCap ? "text-amber-700" : "text-slate-900"}>{selectedGroups.length}</span>
            <span className="mx-1 text-slate-400">/</span>
            <span className="text-slate-500">{MAX_SELECTED_VALIDATION_NODES}</span>
            <span className="ml-1.5 text-xs font-semibold text-slate-500">{t("선택", "selected")}</span>
          </h3>
        </div>
        {topIssueGroups.length > 0 && (
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-bold text-slate-600">
            Top {topIssueGroups.length}
          </span>
        )}
      </div>

      <div className="mt-3 flex min-h-[34px] flex-wrap items-center gap-1.5">
        {selectedGroups.length === 0 ? (
          <span className="text-[11px] text-slate-400">
            {t("지도 노드 또는 아래 Top 목록에서 선택하세요.", "Pick a map node or an item below to compare.")}
          </span>
        ) : (
          selectedGroups.map((group) => (
            <span
              key={group.nodeId}
              className={`inline-flex max-w-full items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${
                group.nodeId === activeGroup?.nodeId ? "bg-slate-900 text-ink" : "bg-slate-100 text-slate-700"
              }`}
            >
              <button
                type="button"
                className="max-w-[210px] truncate"
                onClick={() => onNodeClick(group.nodeId, group.nodeName)}
                title={group.nodeName}
              >
                {group.nodeName}
              </button>
              <button
                type="button"
                className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-[11px] leading-none opacity-75 hover:bg-surface/20 hover:opacity-100"
                onClick={() => onNodeRemove(group.nodeId)}
                aria-label={`${group.nodeName} ${t("선택 해제", "remove")}`}
              >
                ✕
              </button>
            </span>
          ))
        )}
      </div>
      {atSelectionCap && (
        <p className="mt-1 text-[10px] font-semibold text-amber-700">
          {t(`최대 ${MAX_SELECTED_VALIDATION_NODES}개까지 선택 가능 — 새로 선택하려면 기존 항목을 제거하세요.`, `Up to ${MAX_SELECTED_VALIDATION_NODES} can be selected — remove one to add a new node.`, `最大 ${MAX_SELECTED_VALIDATION_NODES} 件まで選択可能 — 新しく選択するには既存の項目を削除してください。`)}
        </p>
      )}

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto pr-1">
        {activeGroup ? (
          <ValidationNodeDetail group={activeGroup} t={t} />
        ) : groups.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-sm text-slate-400">
            {t("검증 가능한 노드가 없습니다.", "No comparable validation nodes.")}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
            <p className="font-semibold text-slate-600">{t("노드를 선택해주세요", "Select a node")}</p>
            <p className="mt-1 text-xs text-slate-400">{t("지도에서 클릭하거나 아래 Top 목록에서 노드를 선택하면 상세 비교가 표시됩니다.", "Click a map node or pick from the Top list below to see details.")}</p>
          </div>
        )}

        {topIssueGroups.length > 0 && (
          <div className="mt-4 border-t border-slate-200 pt-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h4 className="text-sm font-bold text-slate-900">
                {t(`문제 노드 Top ${topIssueGroups.length}`, `Top ${topIssueGroups.length} Issue Nodes`, `問題ノード Top ${topIssueGroups.length}`)}
              </h4>
              <span className="text-[11px] font-semibold text-slate-400">{t("GEH · 평균오차", "GEH · Avg Err")}</span>
            </div>
            <div className="space-y-2">
              {topIssueGroups.map((group, index) => (
                <ValidationIssueListItem
                  key={group.nodeId}
                  group={group}
                  rank={index + 1}
                  active={group.nodeId === activeGroup?.nodeId}
                  selected={selectedNodeIds.includes(group.nodeId)}
                  onClick={onNodeClick}
                  t={t}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function ValidationNodeDetail({
  group,
  t,
}: {
  group: CountComparisonGroup;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const totals = buildNodeValidationTotals(group.rows);
  const directionGroups = buildCountDirectionGroups(group.rows);
  const directionByKey = Object.fromEntries(directionGroups.map((item) => [item.direction, item])) as Partial<Record<CountDirection, CountDirectionGroup>>;
  const topRows = group.rows.slice(0, 7);

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
            {t("노드", "Node")} {group.nodeId}
          </p>
          <h4 className="mt-1 truncate text-base font-bold text-slate-950" title={group.nodeName}>
            {group.nodeName}
          </h4>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-bold ${gehBadgeClass(group.worstGeh)}`}>
          GEH {formatNumber(group.worstGeh, 1)}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <ValidationMetricCard label={t("실측 합계", "Observed")} value={formatNumber(totals.observedTotal, 0)} />
        <ValidationMetricCard label={t("모델 합계", "Model")} value={formatNumber(totals.modelTotal, 0)} />
        <ValidationMetricCard label={t("차이", "Diff")} value={formatSignedNumber(totals.diff, 0)} tone={totals.diff == null ? "neutral" : totals.diff > 0 ? "danger" : totals.diff < 0 ? "info" : "success"} />
        <ValidationMetricCard label={t("오차율", "Error")} value={formatSignedPct(totals.pctError)} tone={errorToneToMetricTone(totals.pctError)} />
      </div>

      <div className="mt-3 space-y-2">
        {COUNT_DIRECTION_ORDER.map((direction) => (
          <ValidationDirectionStrip
            key={`${group.nodeId}-${direction}`}
            group={directionByKey[direction] ?? emptyCountDirectionGroup(direction)}
            t={t}
          />
        ))}
      </div>

      <div className="mt-3 rounded-xl border border-slate-200 bg-surface px-3 py-2">
        <div className="mb-1.5 flex items-center justify-between text-[11px] font-semibold text-slate-500">
          <span>{t("상세 이동류", "Movement Detail")}</span>
          <span>{topRows.length}/{group.rows.length}</span>
        </div>
        <div className="space-y-1">
          {topRows.map((row) => (
            <div key={row.key} className="grid grid-cols-[2.6rem_minmax(0,1fr)_0.6rem_minmax(0,1fr)_3.2rem] items-center gap-x-1.5 text-[11px]">
              <span className="font-bold text-slate-600">{formatMovementLabel(row.movement)}</span>
              <span className="text-right tabular-nums text-slate-700">{formatNumber(row.observed, 0)}</span>
              <span aria-hidden className="text-center text-[10px] text-slate-300">/</span>
              <span className="text-left tabular-nums text-slate-500">{formatNumber(row.model, 0)}</span>
              <span className={`text-right font-bold tabular-nums ${errorToneClass(row.pctError)}`}>
                {formatSignedPct(row.pctError)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ValidationMetricCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "success" | "warning" | "danger" | "info" | "neutral";
}) {
  const toneClass =
    tone === "success"
      ? "text-emerald-700"
      : tone === "warning"
        ? "text-amber-700"
        : tone === "danger"
          ? "text-rose-700"
          : tone === "info"
            ? "text-blue-700"
            : "text-slate-900";
  return (
    <div className="rounded-xl border border-slate-200 bg-surface px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">{label}</p>
      <p className={`mt-1 text-base font-bold tabular-nums ${toneClass}`}>{value}</p>
    </div>
  );
}

function ValidationDirectionStrip({
  group,
  t,
}: {
  group: CountDirectionGroup;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const label = COUNT_DIRECTION_LABELS[group.direction];
  const isEmpty = group.rows.length === 0 || (group.observedTotal == null && group.modelTotal == null);

  if (isEmpty) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed border-slate-200 bg-slate-50/60 px-3 py-1.5 text-[11px] text-slate-400">
        <span className="inline-flex h-5 w-5 items-center justify-center rounded-md bg-slate-200 text-[10px] font-bold text-slate-500">
          {label.short}
        </span>
        <span aria-hidden className="text-slate-400">{label.arrow}</span>
        <span className="font-semibold text-slate-500">{t(label.ko, label.en)}</span>
        <span className="ml-auto">{t("데이터 없음", "No data")}</span>
      </div>
    );
  }

  const tone = errorTone(group.pctError);
  const BIAS_AXIS_PCT = 50;
  const biasMagnitude = group.pctError == null ? 0 : Math.min(Math.abs(group.pctError), BIAS_AXIS_PCT);
  const biasWidthPct = (biasMagnitude / BIAS_AXIS_PCT) * 50;
  const biasOnRight = (group.pctError ?? 0) >= 0;

  return (
    <div className="rounded-xl border border-slate-200 bg-surface px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-slate-900 text-[10px] font-bold text-ink">
            {label.short}
          </span>
          <span aria-hidden className="text-xs font-bold text-slate-500">{label.arrow}</span>
          <span className="text-xs font-bold text-slate-800">{t(label.ko, label.en)}</span>
        </div>
        <div className="text-right">
          <span className={`text-[11px] font-bold tabular-nums ${toneTextClass(tone)}`}>
            {formatSignedPct(group.pctError)}
          </span>
          <span className="ml-2 text-[10px] font-semibold text-slate-400">GEH {formatNumber(group.geh, 1)}</span>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <div
          className="relative h-2 flex-1 rounded-full bg-slate-100"
          role="img"
          aria-label={`${t("모델 편향", "Model bias")} ${formatSignedPct(group.pctError)}`}
        >
          <span aria-hidden className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-slate-300" />
          <span
            aria-hidden
            className={`absolute inset-y-0 ${biasOnRight ? "left-1/2 rounded-r-full" : "right-1/2 rounded-l-full"} ${toneBarClass(tone)}`}
            style={{ width: `${biasWidthPct}%` }}
          />
        </div>
        <span className="shrink-0 text-[10px] font-semibold tabular-nums text-slate-500">
          {formatNumber(group.observedTotal, 0)}
          <span className="mx-1 text-slate-300">/</span>
          {formatNumber(group.modelTotal, 0)}
        </span>
      </div>
    </div>
  );
}

function ValidationIssueListItem({
  group,
  rank,
  active,
  selected,
  onClick,
  t,
}: {
  group: CountComparisonGroup;
  rank: number;
  active: boolean;
  selected: boolean;
  onClick: (nodeId: string, nodeName: string) => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  return (
    <button
      type="button"
      className={`grid w-full grid-cols-[2.1rem_minmax(0,1fr)_auto] items-center gap-2 rounded-xl border px-3 py-2 text-left transition ${
        active
          ? "border-slate-900 bg-slate-900 text-ink shadow-sm"
          : selected
            ? "border-slate-300 bg-slate-100 text-slate-800 hover:border-slate-400"
            : "border-slate-200 bg-surface text-slate-800 hover:border-slate-300 hover:bg-slate-50"
      }`}
      onClick={() => onClick(group.nodeId, group.nodeName)}
    >
      <span className={`text-xs font-bold tabular-nums ${active ? "text-white/70" : "text-slate-400"}`}>{rank}</span>
      <span className="min-w-0">
        <span className="block truncate text-xs font-bold">{group.nodeName}</span>
        <span className={`mt-0.5 block text-[10px] ${active ? "text-white/60" : "text-slate-500"}`}>
          {t("노드", "Node")} {group.nodeId} · {group.validCount}/{group.rows.length}
        </span>
      </span>
      <span className="flex flex-col items-end gap-0.5">
        <span className={`inline-flex min-w-[2.5rem] justify-center rounded-full px-1.5 py-0.5 text-[11px] font-bold tabular-nums ${toneBadgeClass(gehTone(group.worstGeh))}`}>
          {formatNumber(group.worstGeh, 1)}
        </span>
        <span className="inline-flex items-center gap-1 text-[10px] font-bold tabular-nums">
          <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${toneDotClass(errorTone(group.averageAbsPctError))}`} />
          <span className={active ? "text-white/85" : toneTextClass(errorTone(group.averageAbsPctError))}>
            {group.averageAbsPctError == null ? "-" : formatPct(group.averageAbsPctError)}
          </span>
        </span>
      </span>
    </button>
  );
}

function CountMatrixStat({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string;
  tone?: "success" | "warning" | "danger" | "neutral";
  hint?: string;
}) {
  const valueClass =
    tone === "success" ? "text-emerald-700" : tone === "warning" ? "text-amber-700" : tone === "danger" ? "text-rose-700" : "text-slate-900";
  const hintClass =
    tone === "success"
      ? "text-emerald-600"
      : tone === "warning"
        ? "text-amber-600"
        : tone === "danger"
          ? "text-rose-600"
          : "text-slate-500";

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">{label}</p>
      <p className={`mt-1 text-xl font-bold tabular-nums ${valueClass}`}>{value}</p>
      {hint && <p className={`mt-0.5 text-[10px] font-semibold ${hintClass}`}>{hint}</p>}
    </div>
  );
}

function CountNodeDirectionMatrixRow({
  group,
  t,
}: {
  group: CountComparisonGroup;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const directionGroups = buildCountDirectionGroups(group.rows);
  const directionsByKey = Object.fromEntries(directionGroups.map((item) => [item.direction, item])) as Partial<Record<CountDirection, CountDirectionGroup>>;
  const totalGroup = directionsByKey.total;

  return (
    <div className="grid grid-cols-[190px_repeat(4,minmax(230px,1fr))] border-b border-slate-200 last:border-b-0">
      <div className="sticky left-0 z-10 border-r border-slate-200 bg-surface px-4 py-4 shadow-[5px_0_12px_rgba(15,23,42,0.04)]">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">{t("노드", "Node")} {group.nodeId}</p>
        <h3 className="mt-1 text-sm font-bold leading-snug text-slate-900">{group.nodeName}</h3>
        <div className="mt-3 space-y-1.5">
          <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold ${gehBadgeClass(group.worstGeh)}`}>
            GEH {formatNumber(group.worstGeh, 1)}
          </span>
          <p className="text-[11px] font-semibold text-slate-600">
            {t("평균오차", "Avg Err")} {group.averageAbsPctError == null ? "-" : formatPct(group.averageAbsPctError)}
          </p>
          <p className="text-[11px] text-slate-500">
            {group.validCount}/{group.rows.length} {t("비교 가능", "comparable")}
          </p>
          {totalGroup && (
            <p className="text-[10px] text-slate-500">
              {t("전체", "Total")} {formatNumber(totalGroup.modelTotal, 0)} / {formatNumber(totalGroup.observedTotal, 0)}
            </p>
          )}
        </div>
      </div>

      {COUNT_DIRECTION_ORDER.map((direction) => (
        <CountDirectionMatrixCell
          key={`${group.key}-${direction}`}
          group={directionsByKey[direction] ?? emptyCountDirectionGroup(direction)}
          t={t}
        />
      ))}
    </div>
  );
}

function CountDirectionMatrixCell({
  group,
  t,
}: {
  group: CountDirectionGroup;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  if (group.rows.length === 0) {
    return (
      <div className="border-l border-slate-200 bg-surface px-3 py-4">
        <div className="flex h-full min-h-[150px] items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50 text-xs text-slate-400">
          {t("데이터 없음", "No data")}
        </div>
      </div>
    );
  }

  const maxValue = Math.max(group.observedTotal ?? 0, group.modelTotal ?? 0, 1);
  const observedPct = group.observedTotal == null ? 0 : (group.observedTotal / maxValue) * 100;
  const modelPct = group.modelTotal == null ? 0 : (group.modelTotal / maxValue) * 100;

  return (
    <div className="border-l border-slate-200 bg-surface px-3 py-4">
      <div className="grid grid-cols-[1fr_auto] items-start gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            {t("실측 / 모델", "Observed / Model")}
          </p>
          <p className="mt-1 text-lg font-bold tabular-nums text-slate-900">
            {formatNumber(group.observedTotal, 0)}
            <span className="mx-1.5 text-sm font-semibold text-slate-400">/</span>
            {formatNumber(group.modelTotal, 0)}
          </p>
        </div>
        <div className="text-right">
          <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold ${gehBadgeClass(group.geh)}`}>
            GEH {formatNumber(group.geh, 1)}
          </span>
          <p className={`mt-1 text-xs font-bold tabular-nums ${errorToneClass(group.pctError)}`}>
            {formatSignedPct(group.pctError)}
          </p>
        </div>
      </div>

      <div className="mt-3 space-y-1.5">
        <ComparisonBarLine label={t("실측", "Observed")} value={group.observedTotal} pct={observedPct} color="#6da7ec" />
        <ComparisonBarLine label={t("모델", "Model")} value={group.modelTotal} pct={modelPct} color="#dc8373" />
      </div>

      <div className="mt-3 border-t border-slate-200 pt-2">
        {group.rows.map((row) => (
          <div key={row.key} className="grid grid-cols-[2.25rem_1fr_auto] items-center gap-2 py-1 text-[11px]">
            <span className="font-bold text-slate-600">{formatMovementLabel(row.movement)}</span>
            <span className="tabular-nums text-slate-600">
              {formatNumber(row.observed, 0)} / {formatNumber(row.model, 0)}
            </span>
            <span className={`font-bold tabular-nums ${errorToneClass(row.pctError)}`}>
              {formatSignedPct(row.pctError)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ComparisonBarLine({
  label,
  value,
  pct,
  color,
}: {
  label: string;
  value: number | null;
  pct: number;
  color: string;
}) {
  return (
    <div className="grid grid-cols-[3.25rem_minmax(0,1fr)_4.75rem] items-center gap-2 text-[11px]">
      <span className="font-semibold text-slate-500">{label}</span>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.min(100, Math.max(0, pct))}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-right font-bold tabular-nums text-slate-800">{formatNumber(value, 0)}</span>
    </div>
  );
}

function pickDefaultScenario(scenarios: ScenarioMeta[], period: Period): string {
  const periodName = `base${period.toLowerCase()}`;
  const directMatch = scenarios.find((scenario) => scenario.name.toLowerCase().replace(/\s+/g, "").includes(periodName));
  if (directMatch) return directMatch.id;

  const fallbackId = period === "AM" ? "S000001" : "S000002";
  if (scenarios.some((scenario) => scenario.id === fallbackId)) return fallbackId;

  return scenarios[period === "AM" ? 0 : 1]?.id ?? scenarios[0]?.id ?? "";
}

function buildCountComparisons(rows: BaseWorkbookCountRow[], result: ScenarioResults | null): CountComparisonRow[] {
  const byNode = new Map<string, BaseWorkbookCountRow[]>();
  for (const row of rows) {
    byNode.set(row.node_id, [...(byNode.get(row.node_id) ?? []), row]);
  }

  const selectedRows: BaseWorkbookCountRow[] = [];
  for (const nodeRows of byNode.values()) {
    const movementRows = nodeRows.filter((row) => row.movement.toLowerCase() !== "total");
    selectedRows.push(...(movementRows.length > 0 ? movementRows : nodeRows.filter((row) => row.movement.toLowerCase() === "total")));
  }

  return selectedRows.map((row, index) => {
    const resolved = resolveModelVolume(row, result);
    const observed = toNumber(row.observed_volume);
    const diff = resolved.value != null && observed != null ? resolved.value - observed : null;
    const pctError = resolved.value != null && observed != null && observed !== 0 ? (diff as number / observed) * 100 : null;
    const gehValue = resolved.value != null && observed != null ? geh(resolved.value, observed) : null;

    return {
      key: `${row.period}-${row.node_id}-${row.movement}-${index}`,
      period: row.period,
      nodeId: row.node_id,
      nodeName: row.node_name,
      movement: row.movement,
      observed,
      model: resolved.value,
      diff,
      pctError,
      geh: gehValue,
      source: resolved.source,
    };
  });
}

function summarizeCountVisuals(rows: CountComparisonRow[], profile: ValidationProfile): CountVisualSummary {
  const validRows = rows.filter((row) => row.observed != null && row.model != null);
  const pctRows = validRows.filter((row) => row.pctError != null);
  const gehRows = validRows.filter((row) => row.geh != null);
  const averageAbsPctError =
    pctRows.length > 0
      ? pctRows.reduce((sum, row) => sum + Math.abs(row.pctError ?? 0), 0) / pctRows.length
      : null;
  const signedBiasPct =
    pctRows.length > 0
      ? pctRows.reduce((sum, row) => sum + (row.pctError ?? 0), 0) / pctRows.length
      : null;
  const worstGeh = gehRows.length > 0 ? Math.max(...gehRows.map((row) => row.geh ?? 0)) : null;
  const under5 = gehRows.filter((row) => (row.geh as number) < 5).length;
  const under10 = gehRows.filter((row) => (row.geh as number) < 10).length;
  const gehUnder5Pct = gehRows.length > 0 ? (under5 / gehRows.length) * 100 : null;
  const gehUnder10Pct = gehRows.length > 0 ? (under10 / gehRows.length) * 100 : null;
  const trafficPass =
    gehRows.length > 0 &&
    (gehUnder5Pct ?? 0) >= profile.gehUnder5Pct &&
    (gehUnder10Pct ?? 0) >= profile.gehUnder10Pct;
  const topIssues = [...validRows]
    .sort((a, b) => countIssueScore(b) - countIssueScore(a))
    .slice(0, 5);

  return {
    totalRows: rows.length,
    validRows: validRows.length,
    missingRows: rows.length - validRows.length,
    averageAbsPctError,
    signedBiasPct,
    gehUnder5Pct,
    gehUnder10Pct,
    worstGeh,
    trafficPass,
    topIssues,
  };
}

function groupCountComparisonsByNode(rows: CountComparisonRow[]): CountComparisonGroup[] {
  const groups = new Map<string, CountComparisonRow[]>();
  for (const row of rows) {
    groups.set(row.nodeId, [...(groups.get(row.nodeId) ?? []), row]);
  }

  return [...groups.entries()]
    .map(([nodeId, groupRows]) => {
      const validRows = groupRows.filter((row) => row.observed != null && row.model != null);
      const pctRows = validRows.filter((row) => row.pctError != null);
      const gehRows = validRows.filter((row) => row.geh != null);
      const first = groupRows[0];
      return {
        key: nodeId,
        nodeId,
        nodeName: first.nodeName,
        rows: [...groupRows].sort((a, b) => countIssueScore(b) - countIssueScore(a)),
        validCount: validRows.length,
        missingCount: groupRows.length - validRows.length,
        averageAbsPctError:
          pctRows.length > 0
            ? pctRows.reduce((sum, row) => sum + Math.abs(row.pctError ?? 0), 0) / pctRows.length
            : null,
        worstGeh: gehRows.length > 0 ? Math.max(...gehRows.map((row) => row.geh ?? 0)) : null,
      };
    })
    .sort((a, b) => (b.worstGeh ?? -1) - (a.worstGeh ?? -1));
}

function buildValidationMapNodes(groups: CountComparisonGroup[]): BaseValidationMapNode[] {
  return groups.map((group) => {
    const totals = buildNodeValidationTotals(group.rows);
    const issueScore = Math.max(
      group.worstGeh ?? 0,
      Math.abs(group.averageAbsPctError ?? totals.pctError ?? 0) / 5,
      group.missingCount > 0 ? 4 : 0,
    );

    return {
      nodeId: group.nodeId,
      nodeName: group.nodeName,
      observedTotal: totals.observedTotal,
      modelTotal: totals.modelTotal,
      diff: totals.diff,
      pctError: totals.pctError,
      geh: group.worstGeh ?? totals.geh,
      validCount: group.validCount,
      missingCount: group.missingCount,
      issueScore,
    };
  });
}

function buildNodeValidationTotals(rows: CountComparisonRow[]): CountDirectionGroup {
  return aggregateCountDirectionRows("total", rows);
}

function buildCountDirectionGroups(rows: CountComparisonRow[]): CountDirectionGroup[] {
  const grouped = new Map<CountDirection, CountComparisonRow[]>();
  for (const row of rows) {
    const direction = countDirectionFromMovement(row.movement);
    grouped.set(direction, [...(grouped.get(direction) ?? []), row]);
  }

  const groups = COUNT_DIRECTION_ORDER
    .map((direction) => aggregateCountDirectionRows(direction, grouped.get(direction) ?? []))
    .filter((group) => group.rows.length > 0);
  const totalRows = grouped.get("total") ?? [];
  if (totalRows.length > 0) {
    groups.push(aggregateCountDirectionRows("total", totalRows));
  }

  return groups;
}

function emptyCountDirectionGroup(direction: CountDirection): CountDirectionGroup {
  return aggregateCountDirectionRows(direction, []);
}

function aggregateCountDirectionRows(direction: CountDirection, rows: CountComparisonRow[]): CountDirectionGroup {
  const observedValues = rows
    .map((row) => row.observed)
    .filter((value): value is number => value != null);
  const modelValues = rows
    .map((row) => row.model)
    .filter((value): value is number => value != null);
  const observedTotal = observedValues.length > 0 ? observedValues.reduce((sum, value) => sum + value, 0) : null;
  const modelTotal = modelValues.length > 0 ? modelValues.reduce((sum, value) => sum + value, 0) : null;
  const diff = observedTotal != null && modelTotal != null ? modelTotal - observedTotal : null;
  const pctError = diff != null && observedTotal != null && observedTotal !== 0 ? (diff / observedTotal) * 100 : null;
  const gehValue = observedTotal != null && modelTotal != null ? geh(modelTotal, observedTotal) : null;
  const validCount = rows.filter((row) => row.observed != null && row.model != null).length;

  return {
    direction,
    rows: [...rows].sort((a, b) => countIssueScore(b) - countIssueScore(a)),
    observedTotal,
    modelTotal,
    diff,
    pctError,
    geh: gehValue,
    validCount,
    missingCount: rows.length - validCount,
  };
}

function countDirectionFromMovement(movement: string): CountDirection {
  if (movement.toLowerCase() === "total") return "total";
  return parseMovement(movement)?.from ?? "total";
}

function countIssueScore(row: CountComparisonRow): number {
  const gehScore = row.geh ?? 0;
  const errorScore = Math.abs(row.pctError ?? 0) / 5;
  return Math.max(gehScore, errorScore);
}

function resolveModelVolume(row: BaseWorkbookCountRow, result: ScenarioResults | null): { value: number | null; source: CountComparisonRow["source"] } {
  const excelModel = toNumber(row.model_volume);
  if (excelModel != null) return { value: excelModel, source: "excel" };
  if (!result) return { value: null, source: "missing" };

  const node = result.node_directions?.find((item) => item.node_id === row.node_id);
  if (!node) return { value: null, source: "missing" };

  if (row.movement.toLowerCase() === "total") {
    return {
      value: Object.values(node.incoming ?? {}).reduce((sum, value) => sum + Number(value ?? 0), 0),
      source: "scenario",
    };
  }

  const parsed = parseMovement(row.movement);
  if (!parsed) return { value: null, source: "missing" };

  const laneSum = (node.lane_flows ?? [])
    .filter((flow) => flow.from_direction === parsed.from && flow.to_direction === parsed.to)
    .reduce((sum, flow) => sum + Number(flow.volume ?? 0), 0);
  return { value: laneSum > 0 ? laneSum : null, source: laneSum > 0 ? "scenario" : "missing" };
}

function parseMovement(movement: string): { from: Exclude<CountDirection, "total">; to: Exclude<CountDirection, "total"> } | null {
  const [rawFrom, rawTo] = movement.toUpperCase().split("-").map((part) => part.trim());
  const from = DIRECTION_MAP[rawFrom];
  const to = DIRECTION_MAP[rawTo];
  return from && to ? { from, to } : null;
}

function formatMovementLabel(movement: string): string {
  const parsed = parseMovement(movement);
  if (!parsed) return movement;

  const from = COUNT_DIRECTION_LABELS[parsed.from].short;
  const to = COUNT_DIRECTION_LABELS[parsed.to].short;
  return `${from} -> ${to}`;
}

function buildTravelTimePoints(
  rows: BaseWorkbookTravelTimeRow[],
  result: ScenarioResults | null,
  activePeriod?: string,
  // Defaults to Category 1's tolerance (unchanged pre-existing behavior) so
  // any caller that forgets to pass the active profile's value still gets a
  // sane band instead of NaN.
  travelTolerancePct: number = 15,
): TravelTimePoint[] {
  const travelTolerance = travelTolerancePct / 100;
  return rows.map((row, index) => {
    // Prefer the dedicated validation-chart columns ("TomTom avg" / explicit
    // tolerance bands) when the analyst filled them in; otherwise derive
    // from the raw Observed column using the active category's tolerance
    // (lib/validationProfiles.ts -- category-invariant per Table 5-13, but
    // still threaded through so a future guideline revision needs only a
    // data change here).
    const observed = toNumber(row.tomtom_avg_sec) ?? toNumber(row.observed_travel_time_sec);
    const resolved = resolveTravelTimeModel(row, result, activePeriod);
    const upper = toNumber(row.tomtom_high_sec) ?? (observed != null ? observed * (1 + travelTolerance) : null);
    const lower = toNumber(row.tomtom_low_sec) ?? (observed != null ? observed * (1 - travelTolerance) : null);
    const inBand =
      observed != null && resolved.value != null && upper != null && lower != null
        ? resolved.value >= lower && resolved.value <= upper
        : null;

    return {
      key: `${row.period}-${row.route_id}-${row.sample_order ?? row.time_label}-${index}`,
      period: row.period,
      routeId: row.route_id,
      routeName: row.route_name,
      sampleOrder: row.sample_order,
      timeLabel: row.time_label,
      distKm: row.dist_m != null ? row.dist_m / 1000 : null,
      observed,
      model: resolved.value,
      upper,
      lower,
      inBand,
      source: resolved.source,
    };
  });
}

function resolveTravelTimeModel(row: BaseWorkbookTravelTimeRow, result: ScenarioResults | null, activePeriod?: string): { value: number | null; source: TravelTimePoint["source"] } {
  // Prefer the dedicated "model output" column, then the raw TravelTime column.
  const modelOutput = toNumber(row.model_output_sec);
  if (modelOutput != null) return { value: modelOutput, source: "excel" };
  const excelModel = toNumber(row.model_travel_time_sec);
  if (excelModel != null) return { value: excelModel, source: "excel" };
  // The scenario fallback only applies to the period currently loaded.
  if (!result || (activePeriod != null && row.period !== activePeriod)) return { value: null, source: "missing" };

  const routeId = String(row.route_id);
  const sampleKeys = [row.sample_order != null ? String(row.sample_order) : "", row.time_label].filter(Boolean);
  const intervalMatch = (result.traveltime_interval_class ?? []).find((item) => {
    if (String(item.OBJECT_ID) !== routeId) return false;
    if (sampleKeys.length === 0) return true;
    return sampleKeys.includes(String(item.ARG_INTERVAL ?? ""));
  });
  const intervalValue = toNumber(intervalMatch?.TRAVTM_MEAN ?? intervalMatch?.TRAVTM);
  if (intervalValue != null) return { value: intervalValue, source: "scenario" };

  const routeAverage = result.traveltime.find((item) => String(item.OBJECT_ID) === routeId);
  const averageValue = toNumber(routeAverage?.TRAVTM_MEAN ?? routeAverage?.TRAVTM);
  return { value: averageValue, source: averageValue != null ? "scenario" : "missing" };
}

function summarizeCounts(rows: CountComparisonRow[], profile: ValidationProfile) {
  const validRows = rows.filter((row) => row.geh != null);
  const under5 = validRows.filter((row) => (row.geh as number) < 5).length;
  const between5And10 = validRows.filter((row) => (row.geh as number) >= 5 && (row.geh as number) < 10).length;
  const over10 = validRows.filter((row) => (row.geh as number) >= 10).length;
  const under10 = under5 + between5And10;
  const validCount = validRows.length;
  const under5Pct = pct(under5, validCount);
  const under10Pct = pct(under10, validCount);

  return {
    hasData: validCount > 0,
    validCount,
    under5,
    between5And10,
    over10,
    under10,
    under5Pct,
    betweenPct: pct(between5And10, validCount),
    over10Pct: pct(over10, validCount),
    under10Pct,
    trafficPass: validCount > 0 && under5Pct >= profile.gehUnder5Pct && under10Pct >= profile.gehUnder10Pct,
  };
}

function computeRSquared(rows: CountComparisonRow[]): {
  points: Array<{ x: number; y: number }>;
  slope: number | null;
  r2: number | null;
  axisMax: number;
} {
  const points: Array<{ x: number; y: number }> = [];
  for (const row of rows) {
    if (row.observed == null || row.model == null) continue;
    points.push({ x: row.observed, y: row.model });
  }
  if (points.length === 0) {
    return { points, slope: null, r2: null, axisMax: 0 };
  }

  let sumXY = 0;
  let sumX2 = 0;
  let sumY = 0;
  for (const p of points) {
    sumXY += p.x * p.y;
    sumX2 += p.x * p.x;
    sumY += p.y;
  }
  const slope = sumX2 > 0 ? sumXY / sumX2 : null;
  const meanY = sumY / points.length;

  let ssRes = 0;
  let ssTot = 0;
  for (const p of points) {
    const yHat = slope == null ? 0 : slope * p.x;
    ssRes += (p.y - yHat) ** 2;
    ssTot += (p.y - meanY) ** 2;
  }
  const r2 = ssTot > 0 ? 1 - ssRes / ssTot : null;

  let max = 0;
  for (const p of points) {
    if (p.x > max) max = p.x;
    if (p.y > max) max = p.y;
  }
  const step = max <= 1000 ? 250 : max <= 2500 ? 500 : 1000;
  const axisMax = Math.max(step, Math.ceil(max / step) * step);

  return { points, slope, r2, axisMax };
}

function summarizeFlowBands(rows: CountComparisonRow[]) {
  const buckets = {
    low: { pass: 0, fail: 0, total: 0 },
    mid: { pass: 0, fail: 0, total: 0 },
    high: { pass: 0, fail: 0, total: 0 },
  };
  for (const row of rows) {
    if (row.observed == null || row.model == null) continue;
    const observed = row.observed;
    const diff = Math.abs(row.model - observed);
    let band: "low" | "mid" | "high";
    let pass: boolean;
    if (observed < 700) {
      band = "low";
      pass = diff <= 100;
    } else if (observed <= 2700) {
      band = "mid";
      pass = observed > 0 ? (diff / observed) * 100 <= 15 : diff <= 100;
    } else {
      band = "high";
      pass = diff <= 400;
    }
    buckets[band].total += 1;
    if (pass) buckets[band].pass += 1;
    else buckets[band].fail += 1;
  }
  return buckets;
}

function summarizeTravelTimes(points: TravelTimePoint[], profile: ValidationProfile) {
  const validPoints = points.filter((point) => point.inBand != null);
  const inBandCount = validPoints.filter((point) => point.inBand).length;
  const validCount = validPoints.length;
  const inBandPct = pct(inBandCount, validCount);

  return {
    hasData: validCount > 0,
    validCount,
    inBandCount,
    inBandPct,
    travelPass: validCount > 0 && inBandPct >= profile.travelWithinPct,
  };
}

const PERIOD_RANK: Record<string, number> = { AM: 0, PM: 1 };

function groupTravelPointsByRoute(points: TravelTimePoint[]) {
  // Group by PERIOD + route so each (route, peak) is its own chart (e.g. NB-AM,
  // NB-PM, SB-AM, SB-PM = 4 charts).
  const groups = new Map<string, TravelTimePoint[]>();
  for (const point of points) {
    const key = `${point.period}-${point.routeId}-${point.routeName}`;
    groups.set(key, [...(groups.get(key) ?? []), point]);
  }

  return [...groups.entries()]
    .map(([key, routePoints]) => {
      const sorted = [...routePoints].sort((a, b) => {
        const aOrder = a.sampleOrder ?? (Number.parseFloat(a.timeLabel) || 0);
        const bOrder = b.sampleOrder ?? (Number.parseFloat(b.timeLabel) || 0);
        return aOrder - bOrder;
      });
      const comparablePoints = sorted.filter((point) => point.inBand != null);
      const inBandCount = comparablePoints.filter((point) => point.inBand).length;
      const first = sorted[0];
      return {
        key,
        routeId: first.routeId,
        period: first.period,
        name: `${first.routeName} — ${first.period}`,
        points: sorted,
        validCount: comparablePoints.length,
        inBandCount,
        allInBand: comparablePoints.length > 0 && inBandCount === comparablePoints.length,
      };
    })
    .sort((a, b) => {
      const ar = PERIOD_RANK[a.period] ?? 9;
      const br = PERIOD_RANK[b.period] ?? 9;
      if (ar !== br) return ar - br;
      return Number(a.routeId) - Number(b.routeId);
    });
}

function pointsToCumulativeChartRows(points: TravelTimePoint[]): TravelTimeChartRow[] {
  const rows: TravelTimeChartRow[] = [{ label: "0", dist: 0, observed: 0, model: 0, upper: 0, lower: 0 }];
  let observedTotal = 0;
  let modelTotal = 0;
  let upperTotal = 0;
  let lowerTotal = 0;
  let distTotal = 0;
  let observedComplete = true;
  let modelComplete = true;
  let upperComplete = true;
  let lowerComplete = true;

  points.forEach((point, index) => {
    if (point.observed == null) observedComplete = false;
    else observedTotal += point.observed;

    if (point.model == null) modelComplete = false;
    else modelTotal += point.model;

    if (point.upper == null) upperComplete = false;
    else upperTotal += point.upper;

    if (point.lower == null) lowerComplete = false;
    else lowerTotal += point.lower;

    distTotal += point.distKm ?? 0;

    rows.push({
      label: point.timeLabel || String(point.sampleOrder ?? index + 1),
      dist: distTotal,
      observed: observedComplete ? secondsToMinutes(observedTotal) : null,
      model: modelComplete ? secondsToMinutes(modelTotal) : null,
      upper: upperComplete ? secondsToMinutes(upperTotal) : null,
      lower: lowerComplete ? secondsToMinutes(lowerTotal) : null,
    });
  });

  return rows;
}

function secondsToMinutes(value: number): number {
  return value / SECONDS_PER_MINUTE;
}

function buildAnalysisText({
  countStats,
  travelStats,
  profile,
  t,
}: {
  countStats: ReturnType<typeof summarizeCounts>;
  travelStats: ReturnType<typeof summarizeTravelTimes>;
  profile: ValidationProfile;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  if (!countStats.hasData || !travelStats.hasData) {
    return t(
      "검증 데이터 부족: 교통량 비교 또는 통행시간 검증에 필요한 Excel/모델 데이터가 부족하여 최종 적합성 판정을 생성할 수 없습니다.",
      "Insufficient validation data: traffic count or travel time inputs are missing, so a final suitability decision cannot be generated.",
    );
  }

  const trafficResult = countStats.trafficPass
    ? t("검증 기준을 만족한다", "satisfies the validation criteria")
    : t("검증 기준을 만족하지 못한다", "does not satisfy the validation criteria");
  const travelResult = travelStats.travelPass
    ? t("검증 기준을 만족한다", "satisfies the validation criteria")
    : t("검증 기준을 만족하지 못한다", "does not satisfy the validation criteria");
  const finalResult = countStats.trafficPass && travelStats.travelPass
    ? t("따라서 이 기본 모델은 검증 기준을 만족하여 시나리오 테스트에 적합하다.", "Therefore, this base model satisfies the validation criteria and is suitable for scenario testing.")
    : t("따라서 이 기본 모델은 시나리오 테스트 전 추가 보정이 필요하다.", "Therefore, this base model requires additional calibration before scenario testing.");

  return t(
    `[${profile.nameKo} 기준] 교통량 비교분석 결과 GEH값 5 미만인 지점이 ${formatPct(countStats.under5Pct)}, 5~10 사이인 지점이 ${formatPct(countStats.betweenPct)}로 ${trafficResult}. 통행시간 분석결과 통행시간 축 ${formatPct(travelStats.inBandPct)} 관측 허용범위 내로 ${travelResult}. ${finalResult}`,
    `[${profile.nameEn} criteria] Traffic volume comparison shows ${formatPct(countStats.under5Pct)} of points with GEH < 5 and ${formatPct(countStats.betweenPct)} between 5 and 10, which ${trafficResult}. Travel time validation shows ${formatPct(travelStats.inBandPct)} within the observed tolerance band, which ${travelResult}. ${finalResult}`,
    `交通量比較分析の結果、GEH 5未満の地点が ${formatPct(countStats.under5Pct)}、5〜10の地点が ${formatPct(countStats.betweenPct)} で、${trafficResult}。旅行時間分析の結果、旅行時間で ${formatPct(travelStats.inBandPct)} が観測許容範囲内であり、${travelResult}。${finalResult}`,
  );
}

function analysisPanelClass(
  countStats: ReturnType<typeof summarizeCounts>,
  travelStats: ReturnType<typeof summarizeTravelTimes>,
): string {
  if (!countStats.hasData || !travelStats.hasData) {
    return "border-amber-200 bg-amber-50 text-amber-800";
  }
  if (countStats.trafficPass && travelStats.travelPass) {
    return "border-emerald-200 bg-emerald-50 text-emerald-800";
  }
  return "border-rose-200 bg-rose-50 text-rose-800";
}

function geh(sim: number, obs: number): number | null {
  const denom = sim + obs;
  if (denom <= 0) return null;
  return Math.sqrt((2 * (sim - obs) ** 2) / denom);
}

function pct(value: number, total: number): number {
  return total > 0 ? (value / total) * 100 : 0;
}

function toNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value: number | null | undefined, digits: number): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatPct(value: number): string {
  return `${formatNumber(value, 1)}%`;
}

function formatSignedPct(value: number | null): string {
  if (value == null) return "-";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value, 1)}%`;
}

function formatSignedNumber(value: number | null, digits: number): string {
  if (value == null) return "-";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value, digits)}`;
}

function errorToneToMetricTone(value: number | null): "success" | "warning" | "danger" | "neutral" {
  return errorTone(value);
}

function errorToneClass(value: number | null): string {
  return toneTextClass(errorTone(value));
}

function gehTextClass(value: number | null): string {
  return toneTextClass(gehTone(value));
}

function gehBadgeClass(value: number | null): string {
  return toneBadgeClass(gehTone(value));
}

"use client";

// ---------------------------------------------------------------------------
// The calibration workspace (unit #50).
//
// THE PROBLEM IT EXISTS FOR, in the user's own words: calibration was
// scattered across four surfaces — the assistant drawer, the dashboard's GEH
// table, a DOCX and a terminal — and none of them let anyone FEEL the whole
// flow: what comes first, what is finished, what is left, in the order the
// work actually happens.
//
// So the screen's spine is MRWA op §2.12.1's own thirteen steps, and every
// other panel here is a station one of those steps points at. Clicking a step
// scrolls to its station; each station renders the evidence that decided that
// step's status. The rail and the stations read the SAME payloads, so they
// cannot tell a reader two different stories.
//
// NEW CALIBRATION LOGIC HERE: none. Every number on this page was produced by
// a service that already existed — `reality_check` (scoring + #43's rungs),
// `verify_applied_row` (#44's Table 5-6 check), the generation gate, the
// phase-diagram store — and reached this page through the read-only
// /api/calibration routes. The approve button posts to the endpoint the drawer
// has always used. This file arranges and labels; it decides nothing.
//
// WHAT IT ABSORBED. `/base-model` was its own nav destination until the user's
// 2026-08-19 decision that base-model validation IS the scoring step. The page
// moved here whole (components/calibration/BaseModelStation.tsx) rather than
// being partially reimplemented, and `/base-model` 308s to this route so
// bookmarks still land on the work.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import BaseValidationMap, { type BaseValidationMapNode } from "@/components/BaseValidationMap";
import { useLanguage } from "@/components/LanguageProvider";
import BaseModelStation from "@/components/calibration/BaseModelStation";
import BehaviourStation from "@/components/calibration/BehaviourStation";
import FlowRail, { ParameterSearchTrack } from "@/components/calibration/FlowRail";
import NodeScoreTable, { NodeScoreDetail } from "@/components/calibration/NodeScoreTable";
import ProposalRail from "@/components/calibration/ProposalRail";
import {
  failed,
  ok,
  resolve,
  type Loaded,
} from "@/lib/asyncResource";
import {
  fetchCalibrationAppliedRows,
  fetchCalibrationRail,
  fetchCalibrationRealityCheck,
  fetchScenarios,
  type CalibrationAppliedRows,
  type CalibrationRail,
  type CalibrationRailStep,
  type CalibrationRealityCheck,
  type CalibrationRealityNode,
  type ScenarioMeta,
} from "@/lib/api";

/** The station ids the rail's `anchor` field points at. Kept as a type so a
 * station the server can name but this page does not render is a compile
 * error, not a dead click. */
const STATIONS = [
  "station-network",
  "station-signals",
  "station-scoring",
  "station-behaviour",
  "station-proposals",
  "station-manual",
] as const;

type StationId = (typeof STATIONS)[number];

function isStation(value: string): value is StationId {
  return (STATIONS as readonly string[]).includes(value);
}

/** The Table 5-6 read has no parameters, so its request key is a constant --
 * it is still a key, because `resolve` answers "is what I hold the answer to
 * THIS request" and a mount-only read is just the request that never varies. */
const APPLIED_ROWS_KEY = "applied-rows";

/** The report's nodes in the shape `BaseValidationMap` already speaks, so the
 * table and the map cross-highlight through one contract rather than two. */
function toMapNodes(report: CalibrationRealityCheck | null): BaseValidationMapNode[] {
  if (!report?.nodes) return [];
  return report.nodes.map((node) => {
    const observed = typeof node.observed_total === "number" ? node.observed_total : null;
    const modelled = typeof node.modelled_total === "number" ? node.modelled_total : null;
    const diff = observed != null && modelled != null ? modelled - observed : null;
    return {
      nodeId: String(node.node_id),
      nodeName: String(node.node_name ?? node.node_id),
      observedTotal: observed,
      modelTotal: modelled,
      diff,
      pctError: observed != null && observed !== 0 && diff != null ? (diff / observed) * 100 : null,
      geh: typeof node.node_total_geh === "number" ? node.node_total_geh : null,
      // This report grades node TOTALS, so one node contributes exactly one
      // graded comparison. Putting a movement count here would borrow a number
      // from a different tally — the movement counts live in the report's own
      // coverage line under the table.
      validCount: node.node_total_geh == null ? 0 : 1,
      missingCount: node.node_total_geh == null ? 1 : 0,
      issueScore: typeof node.node_total_geh === "number" ? node.node_total_geh : 0,
    };
  });
}

export default function CalibrationWorkspacePage() {
  const { t, lang } = useLanguage();

  const [scenarios, setScenarios] = useState<ScenarioMeta[]>([]);
  const [scenarioId, setScenarioId] = useState<string>("");
  const [compareId, setCompareId] = useState<string>("");
  // Bumped when an approval lands, so the rail re-reads. A nonce rather than an
  // imperative reload call: the rail's request key is what decides whether what
  // we hold is current, so "please read again" has to be a change to that key.
  const [railNonce, setRailNonce] = useState(0);

  // Each remote resource is ONE piece of state carrying the request key it
  // describes (see `Loaded` in lib/asyncResource.ts). `loading`, the error and
  // the reset-when-the-selection-changes all fall out of comparing that key
  // with the current one DURING RENDER, so no effect sets anything before its
  // own call -- this app's one convention for talking to the server.
  const railKey = `${scenarioId}|${railNonce}`;
  const [railLoaded, setRailLoaded] = useState<Loaded<CalibrationRail> | null>(null);
  const { data: rail, error: railError, loading: railLoading } = resolve(railLoaded, railKey);

  const [reportLoaded, setReportLoaded] = useState<Loaded<CalibrationRealityCheck> | null>(null);
  const {
    data: report,
    error: reportError,
    loading: reportLoading,
  } = resolve(reportLoaded, scenarioId, { idle: !scenarioId });

  const [compareLoaded, setCompareLoaded] = useState<Loaded<CalibrationRealityCheck> | null>(null);
  const { data: compareReport, error: compareError } = resolve(compareLoaded, compareId, {
    idle: !compareId,
  });

  const [appliedLoaded, setAppliedLoaded] = useState<Loaded<CalibrationAppliedRows> | null>(null);
  const {
    data: appliedRows,
    error: appliedError,
    loading: appliedLoading,
  } = resolve(appliedLoaded, APPLIED_ROWS_KEY);

  // Unit #51's track, read off the rail the server already sent. Derived, never
  // fetched separately: two requests would let the behaviour station and the
  // rail show different lock states on the same screen.
  const parameterSearchTrack =
    rail?.steps.find((step) => step.number === 12)?.tracks?.[0] ?? null;

  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [activeStep, setActiveStep] = useState<number | null>(null);


  useEffect(() => {
    fetchScenarios()
      .then((list) => setScenarios(list))
      .catch(() => setScenarios([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCalibrationAppliedRows()
      .then((rows) => {
        if (!cancelled) setAppliedLoaded(ok(APPLIED_ROWS_KEY, rows));
      })
      .catch((caught) => {
        if (!cancelled) setAppliedLoaded(failed(APPLIED_ROWS_KEY, caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCalibrationRail({ scenarioId: scenarioId || null })
      .then((payload) => {
        if (!cancelled) setRailLoaded(ok(railKey, payload));
      })
      .catch((caught) => {
        if (!cancelled) setRailLoaded(failed(railKey, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [railKey, scenarioId]);

  useEffect(() => {
    if (!scenarioId) return;
    let cancelled = false;
    fetchCalibrationRealityCheck({ scenarioId })
      .then((payload) => {
        if (!cancelled) setReportLoaded(ok(scenarioId, payload));
      })
      .catch((caught) => {
        if (!cancelled) setReportLoaded(failed(scenarioId, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [scenarioId]);

  useEffect(() => {
    if (!compareId) return;
    let cancelled = false;
    fetchCalibrationRealityCheck({ scenarioId: compareId })
      .then((payload) => {
        if (!cancelled) setCompareLoaded(ok(compareId, payload));
      })
      .catch((caught) => {
        if (!cancelled) setCompareLoaded(failed(compareId, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [compareId]);

  const handleNodeClick = useCallback((nodeId: string) => {
    setActiveNodeId((current) => (current === nodeId ? null : nodeId));
    setSelectedNodeIds((current) => (current.includes(nodeId) ? current : [...current, nodeId]));
  }, []);

  // Looked up by ID, and scrolled with NO `behavior: "smooth"`. Both are
  // measurements, not preferences (2026-08-19, driving the real page):
  //
  // * The section's `id` is the same token the server puts in `step.anchor`,
  //   so looking it up is the anchor doing the one job an anchor has -- and it
  //   cannot go stale the way a ref map rebuilt on every render can.
  // * A SMOOTH scrollIntoView did not move this page at all in a browser that
  //   was not compositing. It did not degrade to an instant jump; the scroll
  //   simply never happened, and the click looked broken while the step chip
  //   correctly highlighted. So the scroll is unconditional here and any
  //   animation is left to CSS, where a browser that cannot animate still
  //   lands the reader on the section.
  //
  // A step whose anchor names no section falls back to the manual-check area
  // rather than doing nothing -- which is where an unrecognised step belongs.
  const handleSelectStep = useCallback((step: CalibrationRailStep) => {
    setActiveStep(step.number);
    const anchor: StationId = isStation(step.anchor) ? step.anchor : "station-manual";
    document.getElementById(anchor)?.scrollIntoView({ block: "start" });
  }, []);

  const mapNodes = useMemo(() => toMapNodes(report), [report]);
  const activeNode: CalibrationRealityNode | null = useMemo(
    () => report?.nodes?.find((node) => node.node_id === activeNodeId) ?? null,
    [report, activeNodeId],
  );

  // Before/after: the SAME node in two runs, GEH against GEH. Only nodes that
  // both reports actually graded appear — a node one run could not grade has
  // no delta, and printing a dash beside a number invites reading it as zero.
  const deltas = useMemo(() => {
    if (!report?.nodes || !compareReport?.nodes) return [];
    const before = new Map(compareReport.nodes.map((node) => [node.node_id, node]));
    return report.nodes
      .map((node) => {
        const other = before.get(node.node_id);
        if (!other) return null;
        if (typeof node.node_total_geh !== "number" || typeof other.node_total_geh !== "number") return null;
        return {
          nodeId: node.node_id,
          nodeName: String(node.node_name ?? node.node_id),
          before: other.node_total_geh,
          after: node.node_total_geh,
          delta: node.node_total_geh - other.node_total_geh,
        };
      })
      .filter((row): row is NonNullable<typeof row> => row !== null);
  }, [report, compareReport]);

  const manualSteps = rail?.steps.filter((step) => step.manual_area) ?? [];

  return (
    <div className="flex flex-col gap-4 pb-16">
      <header>
        <h1 className="text-[22px] font-bold text-slate-900">
          {t("보정 워크스페이스", "Calibration workspace", "補正ワークスペース")}
        </h1>
        <p className="mt-1 text-[14px] leading-6 text-slate-600">
          {t(
            "MRWA §2.12.1의 13단계 순서대로, 이 워크스페이스가 실제로 가진 증거만으로 어디까지 왔는지 보여줍니다.",
            "Where this project stands in MRWA §2.12.1's thirteen steps, derived only from evidence this workspace actually holds.",
            "MRWA §2.12.1の13段階の順に、このワークスペースが実際に持つ証拠だけで現在地を示します。",
          )}
        </p>
      </header>

      <FlowRail
        rail={rail}
        loading={railLoading}
        error={railError}
        activeStep={activeStep}
        onSelectStep={handleSelectStep}
      />

      {/* ---------------------------------------------------------------- */}
      {/* Scoring station — steps ⑩ and ⑪ read their evidence from here.   */}
      {/* ---------------------------------------------------------------- */}
      <section
        id="station-scoring"
        className="scroll-mt-4 rounded-xl border border-slate-200 bg-surface px-4 py-3.5"
      >
        <h2 className="text-[16px] font-bold text-slate-900">
          {t("채점 — 실행 대 실측", "Scoring — run versus observed", "採点 — 実行と実測")}
        </h2>
        <p className="mt-1 text-[13px] leading-5 text-slate-600">
          {t(
            "모델 값은 선택한 실행의 결과에서 가져옵니다. 워크북에 붙여넣은 모델 열은 다른 실행의 것일 수 있어 사용하지 않습니다.",
            "Modelled values come from the selected run's own results. The workbook's pasted model column is not used — it may describe a different run.",
            "モデル値は選択した実行の結果から取得します。ワークブックに貼り付けられたモデル列は別の実行のものである可能性があるため使用しません。",
          )}
        </p>

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[13px] text-slate-600">
            <span className="font-semibold">{t("채점할 실행", "Run to score", "採点する実行")}</span>
            <select
              value={scenarioId}
              onChange={(event) => {
                setScenarioId(event.target.value);
                setActiveNodeId(null);
                setSelectedNodeIds([]);
              }}
              className="h-9 min-w-[240px] rounded-md border border-slate-300 bg-surface px-2 text-[14px] text-slate-900"
            >
              <option value="">{t("선택하세요", "Choose a run", "選択してください")}</option>
              {scenarios.map((scenario) => (
                <option key={scenario.id} value={scenario.id}>
                  {scenario.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-[13px] text-slate-600">
            <span className="font-semibold">{t("비교할 이전 실행", "Compare with an earlier run", "比較する以前の実行")}</span>
            <select
              value={compareId}
              onChange={(event) => setCompareId(event.target.value)}
              className="h-9 min-w-[240px] rounded-md border border-slate-300 bg-surface px-2 text-[14px] text-slate-900"
            >
              <option value="">{t("비교 안 함", "No comparison", "比較しない")}</option>
              {scenarios
                .filter((scenario) => scenario.id !== scenarioId)
                .map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name}
                  </option>
                ))}
            </select>
          </label>
        </div>

        {reportError && (
          <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[13px] leading-5 text-rose-700">
            {reportError}
          </p>
        )}

        {!scenarioId ? (
          <p className="mt-3 text-[14px] leading-6 text-slate-600">
            {t(
              "실행을 선택하면 노드별 GEH와 진단이 나타납니다.",
              "Pick a run and the per-node GEH and diagnosis appear.",
              "実行を選択すると、ノード別GEHと診断が表示されます。",
            )}
          </p>
        ) : reportLoading ? (
          <p className="mt-3 text-[14px] text-slate-500">
            {t("채점하는 중...", "Scoring...", "採点中...")}
          </p>
        ) : report ? (
          <div className="mt-3 grid gap-3 lg:grid-cols-[1.1fr_1fr]">
            <div>
              <NodeScoreTable
                report={report}
                activeNodeId={activeNodeId}
                selectedNodeIds={selectedNodeIds}
                onNodeClick={handleNodeClick}
              />
              {activeNode && (
                <div className="mt-3">
                  <NodeScoreDetail node={activeNode} />
                </div>
              )}
            </div>
            <div>
              <div className="h-[420px] overflow-hidden rounded-lg border border-slate-200">
                <BaseValidationMap
                  nodes={mapNodes}
                  selectedNodeIds={selectedNodeIds}
                  activeNodeId={activeNodeId}
                  onNodeClick={(nodeId) => handleNodeClick(nodeId)}
                />
              </div>
              {/* Honest caption. The map places a node only when the network
                  geometry carries that node; the TABLE above is the complete
                  list either way, so a node missing from the map is a missing
                  placement, never a missing score. */}
              <p className="mt-1.5 text-[13px] leading-5 text-slate-500">
                {t(
                  "노드 위치는 데이터셋의 좌표 정보에서 옵니다. 좌표가 없는 노드는 지도에 나타나지 않으며, 위 표가 전체 목록입니다.",
                  "Node placement comes from the dataset's georeference. A node without coordinates does not appear on the map — the table above is the complete list.",
                  "ノード位置はデータセットの座標情報に基づきます。座標のないノードは地図に表示されません。上の表が完全な一覧です。",
                )}
              </p>
            </div>
          </div>
        ) : null}

        {/* Before/after, same dataset lineage. */}
        {compareId && (
          <div className="mt-4 border-t border-slate-200 pt-3">
            <h3 className="text-[14px] font-bold text-slate-800">
              {t("전후 비교 — 노드별 GEH 변화", "Before/after — per-node GEH change", "前後比較 — ノード別GEHの変化")}
            </h3>
            {compareError ? (
              <p className="mt-1.5 text-[13px] text-rose-700">{compareError}</p>
            ) : deltas.length === 0 ? (
              <p className="mt-1.5 text-[13px] leading-5 text-slate-600">
                {t(
                  "두 실행 모두에서 등급이 매겨진 노드가 없어 비교할 값이 없습니다.",
                  "No node was graded in both runs, so there is nothing to compare.",
                  "両方の実行で評価されたノードがないため、比較する値がありません。",
                )}
              </p>
            ) : (
              <table className="mt-1.5 w-full border-collapse text-[13px]">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-slate-500">
                    <th className="px-2 py-1.5 font-semibold">{t("노드", "Node", "ノード")}</th>
                    <th className="px-2 py-1.5 text-right font-semibold">{t("이전", "Before", "以前")}</th>
                    <th className="px-2 py-1.5 text-right font-semibold">{t("이후", "After", "以後")}</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {deltas.map((row) => (
                    <tr key={row.nodeId} className="border-b border-slate-100">
                      <td className="px-2 py-1.5">
                        <span className="font-mono text-slate-700">{row.nodeId}</span>
                        <span className="ml-1.5 text-slate-600">{row.nodeName}</span>
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono text-slate-600">{row.before.toFixed(1)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-slate-600">{row.after.toFixed(1)}</td>
                      <td
                        className={`px-2 py-1.5 text-right font-mono font-semibold ${
                          row.delta < 0 ? "text-emerald-700" : row.delta > 0 ? "text-rose-700" : "text-slate-500"
                        }`}
                      >
                        {row.delta > 0 ? "+" : ""}
                        {row.delta.toFixed(1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* The absorbed base-model screen: the workbook, the Category 1/2/3
            profile, the flow bands, the R² scatter, the travel-time analysis
            and the §5.6.3 criteria coverage. Same code as before this unit. */}
        <div className="mt-4 border-t border-slate-200 pt-4">
          <BaseModelStation />
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section
        id="station-proposals"
        className="scroll-mt-4 rounded-xl border border-slate-200 bg-surface px-4 py-3.5"
      >
        <h2 className="text-[16px] font-bold text-slate-900">
          {t("제안 — 승인 대기", "Proposals — awaiting approval", "提案 — 承認待ち")}
        </h2>
        <p className="mt-1 text-[13px] leading-5 text-slate-600">
          {t(
            "어시스턴트가 만든 제안을 여기서도 승인할 수 있습니다. 승인 경로는 드로어와 같은 하나뿐입니다.",
            "Proposals raised by the assistant can be approved here too. There is exactly one approval path, shared with the drawer.",
            "アシスタントが作成した提案はここでも承認できます。承認経路はドロワーと同じ一つだけです。",
          )}
        </p>
        <div className="mt-3">
          <ProposalRail onResolved={() => setRailNonce((nonce) => nonce + 1)} />
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section
        id="station-behaviour"
        className="scroll-mt-4 rounded-xl border border-slate-200 bg-surface px-4 py-3.5"
      >
        <h2 className="text-[16px] font-bold text-slate-900">
          {t(
            "포화교통류와 행태 — MRWA Table 5-6",
            "Saturation flow and behaviour — MRWA Table 5-6",
            "飽和交通流と挙動 — MRWA Table 5-6",
          )}
        </h2>
        <p className="mt-1 text-[13px] leading-5 text-slate-600">
          {t(
            "각 주행행태의 현재 ax·bx_add·bx_mult가 공표된 표의 한 행 위에 있는지 확인합니다 (단계 ⑦·⑫).",
            "Whether each driving behaviour's current ax/bx_add/bx_mult sits on one published row of the table (steps 7 and 12).",
            "各走行挙動の現在のax・bx_add・bx_multが公表された表の一行にあるかを確認します（段階⑦・⑫）。",
          )}
        </p>
        <div className="mt-3">
          <BehaviourStation rows={appliedRows} loading={appliedLoading} error={appliedError} />
        </div>

        {/* Unit #51's slot. It renders the RAIL's own step-⑫ track — the same
            component, from the same server payload — rather than a second copy
            of the locked/available sentence. A second copy is exactly how the
            two would eventually disagree about whether a search may start, in
            two places on one screen. When the rail has not loaded there is
            simply nothing here to say. */}
        {parameterSearchTrack && (
          <div className="mt-3">
            <ParameterSearchTrack track={parameterSearchTrack} lang={lang} t={t} />
          </div>
        )}
      </section>

      {/* ---------------------------------------------------------------- */}
      <section
        id="station-network"
        className="scroll-mt-4 rounded-xl border border-slate-200 bg-surface px-4 py-3.5"
      >
        <h2 className="text-[16px] font-bold text-slate-900">
          {t("네트워크 코딩 — 단계 ①", "Network coding — step 1", "ネットワークコーディング — 段階①")}
        </h2>
        <p className="mt-1 text-[13px] leading-5 text-slate-600">
          {rail?.sources.gate
            ? t(
                "이 데이터셋을 발행한 생성의 게이트 판정(V1–V9)이 이 단계의 근거입니다.",
                "The generation gate (V1–V9) that published this dataset is this step's evidence.",
                "このデータセットを発行した生成のゲート判定(V1–V9)がこの段階の根拠です。",
              )
            : t(
                "이 데이터셋에는 생성 게이트 판정이 없어 모델 검진이 근거가 됩니다.",
                "This dataset carries no generation-gate verdict, so the model inspection is the evidence.",
                "このデータセットには生成ゲート判定がないため、モデル検診が根拠になります。",
              )}
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <Link href="/health" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
            {t("모델 검진 열기", "Open model inspection", "モデル検診を開く")}
          </Link>
          <Link href="/genesis" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
            {t("네트워크 생성·게이트", "Network generation & gate", "ネットワーク生成・ゲート")}
          </Link>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section
        id="station-signals"
        className="scroll-mt-4 rounded-xl border border-slate-200 bg-surface px-4 py-3.5"
      >
        <h2 className="text-[16px] font-bold text-slate-900">
          {t("신호 교차로 — 단계 ⑥", "Signalised intersections — step 6", "信号交差点 — 段階⑥")}
        </h2>
        <p className="mt-1 text-[13px] leading-5 text-slate-600">
          {t(
            "현시도 확인서(사람이 서명한 매핑)가 이 단계의 근거입니다. 확인되지 않은 현시도는 신호가 검증됐다는 증거가 아닙니다.",
            "A signed 현시도 (phase-diagram) attestation is this step's evidence. An unattested diagram is not evidence that the signals were verified.",
            "現示図の確認書（人が署名したマッピング）がこの段階の根拠です。未確認の現示図は信号が検証された証拠ではありません。",
          )}
        </p>
        <div className="mt-2">
          <Link href="/genesis" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
            {t("현시도 검토 열기", "Open 현시도 review", "現示図レビューを開く")}
          </Link>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* The seven steps this product measures nothing for, named.        */}
      {/* ---------------------------------------------------------------- */}
      <section
        id="station-manual"
        className="scroll-mt-4 rounded-xl border border-slate-200 bg-surface px-4 py-3.5"
      >
        <h2 className="text-[16px] font-bold text-slate-900">
          {t("수동 확인 영역", "Manual-check areas", "手動確認領域")}
        </h2>
        <p className="mt-1 text-[13px] leading-5 text-slate-600">
          {t(
            "아래 단계들은 이 제품이 측정하지 않습니다. Vissim에서 직접 확인하고 이 도구 밖에 기록하십시오. 화면이 이 단계들을 완료로 표시하는 일은 없습니다.",
            "This product measures nothing for the steps below. Check them in Vissim and record them outside this tool — the screen will never show them as done.",
            "以下の段階について本製品は測定しません。Vissimで直接確認し、本ツールの外に記録してください。画面がこれらを完了と表示することはありません。",
          )}
        </p>
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {manualSteps.map((step) => (
            <li
              key={step.number}
              className="rounded-full border border-slate-300 bg-surface px-2 py-0.5 text-[13px] text-slate-600"
            >
              <span className="font-mono">{step.number}</span> {step.label_ko}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

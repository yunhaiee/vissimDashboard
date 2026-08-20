"use client";

// ---------------------------------------------------------------------------
// The calibration workspace (unit #50, restructured by #50-b).
//
// THE PROBLEM IT EXISTS FOR, in the user's own words: calibration was
// scattered across four surfaces — the assistant drawer, the dashboard's GEH
// table, a DOCX and a terminal — and none of them let anyone FEEL the whole
// flow: what comes first, what is finished, what is left, in the order the
// work actually happens.
//
// #50 put all of it on one screen. Driving that screen, the user found the
// second half of the same problem: "스테이션이 전부 세로로 쌓여 페이지 구조·
// 작업 구조를 파악할 수 없다" — every station stacked down one column, so the
// thirteen-step rail scrolled a reader past four other stations to reach one,
// and the page's structure (and therefore the WORK's structure) was invisible.
//
// SO THIS SCREEN IS NOW THREE PARTS, and each answers one question:
//
//   * a CONTEXT BAR that never moves — which dataset, which run, how the run
//     scored in one line, and the before/after toggle. Everything global lives
//     here and nowhere else, so no step can appear to own it.
//   * the RAIL, permanent on the left — thirteen steps folded into eight
//     station rows (lib/calibrationStations.ts), each with its steps' status
//     chips and a sky badge counting the proposals waiting there. Clicking
//     SWITCHES the station; it is not a scroll any more.
//   * ONE STATION at a time on the right — that step's evidence, what to do
//     next, and that step's own proposal cards inline.
//
// The 제안 collection panel is GONE (the user's decision): a proposal read away
// from the step it acts on is a decision made without its evidence. Every
// proposal now renders at the station its edit belongs to, and the placement is
// executed by tests, so nothing waiting for a human can become unreachable.
//
// NEW CALIBRATION LOGIC HERE: none, in either unit. Every number on this page
// was produced by a service that already existed — `reality_check` (scoring +
// #43's rungs), `verify_applied_row` (#44's Table 5-6 check), the generation
// gate, the phase-diagram store — and reached this page through the read-only
// /api/calibration routes. The approve button posts to the endpoint the drawer
// has always used. This file arranges and labels; it decides nothing.
//
// WHAT IT ABSORBED. `/base-model` was its own nav destination until the user's
// 2026-08-19 decision that base-model validation IS the scoring step. The page
// moved here whole (components/calibration/BaseModelStation.tsx) rather than
// being partially reimplemented, and `/base-model` 308s to this route so
// bookmarks still land on the work. #50-b gives it a tab inside the scoring
// station rather than a place below it: it is a screen-sized body of evidence,
// and appending it to the node table reproduced the very stack this unit is
// undoing.
//
// WHAT #50-c CHANGED: NUMBERS FIRST, PROSE FOLDED. The user drove #50-b's real
// screen and reported the next thing wrong with it — "거의 모든 fine text는
// 크게/직관적 대안/없애", and specifically, of the mono provenance lines
// (`saturation_flow.verify_applied_row · op §5.2.5.1, Table 5-6, p.187` and its
// kind): "이런것도 다 지워 — 화면을 포화시켜". Measured on that screen, every
// station panel opened with two to four sentences of RULE before the first
// number, and every step box closed with a mono line naming a source file and a
// paragraph number. So the surface of each tab is now numbers, verdicts, and
// ONE next-action line; every rule, citation and doctrine sentence lives in a
// `<details>` or an ⓘ. ZERO INFORMATION LOSS is the constraint that makes this
// a restructure rather than a cull: every sentence and every citation string
// that left the surface is reachable, verbatim, one click away — the audit
// trail is the product, and a folded citation is still a citation.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import BaseValidationMap, { type BaseValidationMapNode } from "@/components/BaseValidationMap";
import { InfoTip } from "@/components/genesis/ui";
import { useLanguage } from "@/components/LanguageProvider";
import BaseModelStation from "@/components/calibration/BaseModelStation";
import BehaviourStation from "@/components/calibration/BehaviourStation";
import FlowRail, {
  ParameterSearchTrack,
  circledStep,
  pick,
  stationLabel,
  statusChipClass,
  statusLabel,
  stepLabel,
} from "@/components/calibration/FlowRail";
import NodeScoreTable, { NodeScoreDetail, nodeGehPasses } from "@/components/calibration/NodeScoreTable";
import StationProposals, { usePendingProposals } from "@/components/calibration/StationProposals";
import {
  CALIBRATION_STATIONS,
  DEFAULT_STATION,
  groupSteps,
  stationForStep,
  type CalibrationStationId,
} from "@/lib/calibrationStations";
import {
  failed,
  ok,
  resolve,
  resolveSelection,
  type Loaded,
} from "@/lib/asyncResource";
import {
  fetchActiveDataset,
  fetchCalibrationAppliedRows,
  fetchCalibrationRail,
  fetchCalibrationRealityCheck,
  fetchScenarios,
  type ActiveDataset,
  type CalibrationAppliedRows,
  type CalibrationRail,
  type CalibrationRailStep,
  type CalibrationRealityCheck,
  type CalibrationRealityNode,
  type ScenarioMeta,
} from "@/lib/api";

/** The reads with no parameters still carry a request key — `resolve` answers
 * "is what I hold the answer to THIS request", and a mount-only read is just
 * the request that never varies. */
const APPLIED_ROWS_KEY = "applied-rows";
const DATASET_KEY = "active-dataset";

/** "The rail has no steps" — one array, so an absent rail is a stable value
 * rather than a new empty array on every render. */
const NO_STEPS: CalibrationRailStep[] = [];

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

/**
 * The nodes a station has business showing, decided by the SERVER.
 *
 * `diagnosis.next_action.mrwa_step` is geh_ladder.py's own answer to "which
 * §2.12.1 step authorises the fix for this node" — so a station can ask for its
 * own nodes without this file classifying a single one of them. A node whose
 * next action carries no step (SUPPLY_MISSING_EVIDENCE) belongs to no step and
 * appears at none; it is still in the scoring table, which is the complete list.
 */
function nodesForSteps(
  report: CalibrationRealityCheck | null,
  steps: CalibrationRailStep[],
): CalibrationRealityNode[] {
  if (!report?.nodes || steps.length === 0) return [];
  const wanted = new Set(steps.map((step) => step.number));
  return report.nodes.filter((node) => {
    const step = node.diagnosis?.next_action?.mrwa_step;
    return typeof step === "number" && wanted.has(step);
  });
}

export default function CalibrationWorkspacePage() {
  const { t, lang } = useLanguage();

  const [scenarios, setScenarios] = useState<ScenarioMeta[]>([]);
  const [scenarioId, setScenarioId] = useState<string>("");
  const [compareOn, setCompareOn] = useState(false);
  const [compareIntent, setCompareIntent] = useState<string>("");
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

  const [datasetLoaded, setDatasetLoaded] = useState<Loaded<ActiveDataset> | null>(null);
  const { data: dataset, error: datasetError } = resolve(datasetLoaded, DATASET_KEY);

  const [appliedLoaded, setAppliedLoaded] = useState<Loaded<CalibrationAppliedRows> | null>(null);
  const {
    data: appliedRows,
    error: appliedError,
    loading: appliedLoading,
  } = resolve(appliedLoaded, APPLIED_ROWS_KEY);

  // The comparison run is DERIVED from the toggle and the run list, never set
  // by an effect: turning the toggle on picks the first other run, switching
  // the scored run drops a comparison that no longer exists, and neither costs
  // a frame in which the heading and the table disagree.
  const otherScenarios = useMemo(
    () => scenarios.filter((scenario) => scenario.id !== scenarioId),
    [scenarios, scenarioId],
  );
  const compareId = compareOn
    ? (resolveSelection(compareIntent || null, otherScenarios, (scenario) => scenario.id) ?? "")
    : "";

  const [compareLoaded, setCompareLoaded] = useState<Loaded<CalibrationRealityCheck> | null>(null);
  const { data: compareReport, error: compareError } = resolve(compareLoaded, compareId, {
    idle: !compareId,
  });

  // Unit #51's track, read off the rail the server already sent. Derived, never
  // fetched separately: two requests would let the behaviour station and the
  // rail show different lock states on the same screen.
  const parameterSearchTrack =
    rail?.steps.find((step) => step.number === 12)?.tracks?.[0] ?? null;

  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [activeStep, setActiveStep] = useState<number | null>(null);
  const [stationIntent, setStationIntent] = useState<CalibrationStationId | null>(null);
  const [scoringView, setScoringView] = useState<"nodes" | "workbook">("nodes");

  // Which station is open is a SELECTION, resolved during render against the
  // list of stations that exist (lib/asyncResource.ts's `resolveSelection`) —
  // never an effect that corrects itself one painted frame later.
  const activeStation =
    resolveSelection(stationIntent, CALIBRATION_STATIONS, (station) => station) ?? DEFAULT_STATION;

  // The rail's steps are what resolve a proposal's station (a manual-check
  // step's card belongs in 수동 확인 영역 because the SERVER flagged that step
  // manual). The empty case is a module constant rather than a fresh `[]`, so
  // the derivations below re-run when the RAIL changes and not on every render.
  const railSteps = rail?.steps ?? NO_STEPS;
  const handleProposalResolved = useCallback(() => setRailNonce((nonce) => nonce + 1), []);
  const proposals = usePendingProposals({ steps: railSteps, onResolved: handleProposalResolved });

  useEffect(() => {
    fetchScenarios()
      .then((list) => setScenarios(list))
      .catch(() => setScenarios([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchActiveDataset()
      .then((active) => {
        if (!cancelled) setDatasetLoaded(ok(DATASET_KEY, active));
      })
      .catch((caught) => {
        if (!cancelled) setDatasetLoaded(failed(DATASET_KEY, caught));
      });
    return () => {
      cancelled = true;
    };
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

  const handleSelectStation = useCallback((station: CalibrationStationId) => {
    setStationIntent(station);
    setActiveStep(null);
  }, []);

  // A step clicked inside an expanded group opens its station AND says which
  // step the reader came in on, so the panel can mark it. The station is the
  // SHARED rule's answer, not a second one written here.
  const handleSelectStep = useCallback((step: CalibrationRailStep) => {
    setActiveStep(step.number);
    setStationIntent(stationForStep(step));
  }, []);

  // ONE grouping, read by the rail's rows and by the open station alike — two
  // groupings would let a row point at a station that does not show that step.
  // Derived plainly during render: it is a fold of at most thirteen rows, and
  // a manual memo over an array the compiler cannot prove immutable buys
  // nothing but a bail-out of the whole component's optimization.
  const grouped = groupSteps(railSteps);
  const stationSteps = grouped.byStation[activeStation];

  const mapNodes = useMemo(() => toMapNodes(report), [report]);
  const activeNode: CalibrationRealityNode | null = useMemo(
    () => report?.nodes?.find((node) => node.node_id === activeNodeId) ?? null,
    [report, activeNodeId],
  );

  // The one-line verdict in the context bar. It is the SAME comparison the
  // scoring table's rows make (`nodeGehPasses`, imported rather than repeated)
  // against the report's own threshold, and it states the ungradable count
  // instead of quietly dropping those nodes out of the denominator.
  const gradedNodes = (report?.nodes ?? []).filter(
    (node) => nodeGehPasses(node, report?.geh_threshold) !== null,
  );
  const passedNodes = gradedNodes.filter((node) => nodeGehPasses(node, report?.geh_threshold) === true);
  const ungradedCount = (report?.nodes?.length ?? 0) - gradedNodes.length;

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

  const stationProposals = proposals.grouped.byStation[activeStation];
  const otherProposalCount = proposals.grouped.total - stationProposals.length;

  // Node context for the open station: the report's own nodes whose next action
  // cites one of this station's steps. Empty is the normal case for ① and ⑨,
  // and an empty map is never drawn — see `NodeContext`.
  const stationNodes = nodesForSteps(report, stationSteps);

  // ⑫ has no per-node diagnosis of its own (geh_ladder cites steps 6, 7, 10 and
  // 11), so its node context is the search plan's OWN split — which is also the
  // server's, and only exists while the track is unlocked.
  const behaviourNodeIds = parameterSearchTrack?.card
    ? [
        ...parameterSearchTrack.card.plan.calibration_nodes,
        ...parameterSearchTrack.card.plan.holdout_nodes,
      ]
    : [];

  return (
    <div className="flex flex-col gap-3 pb-16">
      <header>
        <h1 className="text-[22px] font-bold text-slate-900">
          {t("보정 워크스페이스", "Calibration workspace", "補正ワークスペース")}
          {/* What this screen IS, verbatim, in the ⓘ — it is background a reader
              needs once, and it was competing with the context bar's numbers
              every time after that (#50-c). */}
          <InfoTip
            text={t(
              "MRWA §2.12.1의 13단계 순서대로, 이 워크스페이스가 실제로 가진 증거만으로 어디까지 왔는지 보여줍니다.",
              "Where this project stands in MRWA §2.12.1's thirteen steps, derived only from evidence this workspace actually holds.",
              "MRWA §2.12.1の13段階の順に、このワークスペースが実際に持つ証拠だけで現在地を示します。",
            )}
            label={t("이 화면에 대하여", "About this screen", "この画面について")}
          />
        </h1>
      </header>

      {/* ---------------------------------------------------------------- */}
      {/* CONTEXT BAR — everything that belongs to no single step.          */}
      {/* ---------------------------------------------------------------- */}
      <section className="flex flex-wrap items-end gap-x-4 gap-y-2 rounded-xl border border-slate-200 bg-surface px-4 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[14px] font-semibold text-slate-600">
            {t("데이터셋", "Dataset", "データセット")}
          </span>
          {/* The dataset's NAME stays on the surface and got bigger: it is the
              identity of everything below it, and #50-b's own pin exists so no
              step can appear to own it. Mono, because a filename is one. */}
          <span className="font-mono text-[14px] text-slate-700">
            {dataset
              ? dataset.filename
              : datasetError
                ? t("확인할 수 없음", "could not be read", "確認できません")
                : t("확인 중...", "checking...", "確認中...")}
          </span>
        </div>

        <label className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("채점할 실행", "Run to score", "採点する実行")}</span>
          <select
            value={scenarioId}
            onChange={(event) => {
              setScenarioId(event.target.value);
              setActiveNodeId(null);
              setSelectedNodeIds([]);
            }}
            className="h-9 min-w-[220px] rounded-md border border-slate-300 bg-surface px-2 text-[14px] text-slate-900"
          >
            <option value="">{t("선택하세요", "Choose a run", "選択してください")}</option>
            {scenarios.map((scenario) => (
              <option key={scenario.id} value={scenario.id}>
                {scenario.name}
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-col gap-0.5">
          <span className="text-[14px] font-semibold text-slate-600">
            {t("종합 판정", "Overall verdict", "総合判定")}
          </span>
          <span className="text-[15px] font-semibold leading-6 text-slate-800">
            {!scenarioId
              ? t("실행을 선택하세요.", "Pick a run.", "実行を選択してください。")
              : reportLoading
                ? t("채점하는 중...", "Scoring...", "採点中...")
                : !report
                  ? t("채점 결과 없음", "no scoring result", "採点結果なし")
                  : report.status !== "CHECKED"
                    ? pick(report.message, lang)
                    : gradedNodes.length === 0
                      ? t("등급이 매겨진 노드가 없습니다.", "No node could be graded.", "評価されたノードがありません。")
                      : t(
                          `${gradedNodes.length}노드 중 ${passedNodes.length} 합격` +
                            (ungradedCount > 0 ? ` · 등급 불가 ${ungradedCount}` : ""),
                          `${passedNodes.length} of ${gradedNodes.length} nodes pass` +
                            (ungradedCount > 0 ? ` · ${ungradedCount} ungradable` : ""),
                          `${gradedNodes.length}ノード中${passedNodes.length}件合格` +
                            (ungradedCount > 0 ? ` · 評価不可${ungradedCount}` : ""),
                        )}
            {report?.status === "CHECKED" && typeof report.geh_threshold === "number" && (
              <span className="ml-1.5 rounded-full border border-slate-300 bg-surface px-2 py-0.5 text-[14px] font-normal text-slate-600">
                GEH <span className="font-mono font-semibold text-slate-800">{report.geh_threshold.toFixed(1)}</span>
              </span>
            )}
          </span>
        </div>

        <div className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("전후 비교", "Before/after", "前後比較")}</span>
          <div className="flex items-center gap-2">
            {/* SELECTED chip: navy fill + sky TEXT. `text-sky-600`, never 700+ --
                globals.css's dark @theme remaps `bg-sky-50` to navy (#032042) but
                remaps the text ramp only up to sky-600, so a 700 here keeps
                Tailwind's stock light-mode #0369a1 and renders dark-blue-on-navy.
                Same trap the genesis pages already fixed; every selected chip on
                this screen (and FlowRail's NEXT chip and proposal badge) follows
                this one rule. The border stays sky-300 on purpose -- stock #7dd3fc
                reads as a light hairline on dark, which is the selected look. */}
            <button
              type="button"
              onClick={() => setCompareOn((on) => !on)}
              aria-pressed={compareOn}
              disabled={otherScenarios.length === 0}
              className={`inline-flex h-9 items-center rounded-md border px-3 text-[14px] transition ${
                compareOn
                  ? "border-sky-300 bg-sky-50 text-sky-600"
                  : "border-slate-300 bg-surface text-slate-700 hover:bg-slate-50"
              } ${otherScenarios.length === 0 ? "cursor-not-allowed opacity-60" : ""}`}
            >
              {compareOn ? t("비교 켜짐", "Comparing", "比較オン") : t("비교 꺼짐", "Off", "比較オフ")}
            </button>
            {compareOn && (
              <select
                value={compareId}
                onChange={(event) => setCompareIntent(event.target.value)}
                className="h-9 min-w-[200px] rounded-md border border-slate-300 bg-surface px-2 text-[14px] text-slate-900"
              >
                {otherScenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* RAIL (permanent) + ONE STATION.                                   */}
      {/* ---------------------------------------------------------------- */}
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(240px,300px)_minmax(0,1fr)]">
        <FlowRail
          rail={rail}
          loading={railLoading}
          error={railError}
          grouped={grouped}
          proposalCounts={proposals.grouped.counts}
          activeStation={activeStation}
          onSelectStation={handleSelectStation}
          onSelectStep={handleSelectStep}
        />

        <section className="min-w-0 rounded-xl border border-slate-200 bg-surface px-4 py-3.5">
          <header className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-[17px] font-bold text-slate-900">
              {stationLabel(activeStation, t)}
              {/* 채점·진단 is not one of §2.12.1's thirteen — the caption that
                  said so used to sit on the rail row, where an ⓘ cannot go
                  without nesting a button in a button. It says the same thing
                  here, beside the heading whose citation it qualifies. */}
              {activeStation === "scoring" && (
                <InfoTip
                  text={t(
                    "채점·진단은 MRWA 단계 아님 — §2.12.1의 13단계에 속하지 않습니다. 실행을 실측과 대조해 어느 단계로 가야 할지 정하는 자리입니다.",
                    "Scoring & diagnosis is not an MRWA step — it is not one of §2.12.1's thirteen. It is where the run is compared with observed counts to decide which step to go to.",
                    "採点・診断はMRWA段階ではありません — §2.12.1の13段階には含まれません。実行を実測と対照し、どの段階へ進むかを決める場所です。",
                  )}
                  label={t("이 스테이션에 대하여", "About this station", "このステーションについて")}
                />
              )}
            </h2>
          </header>

          {/* Every step this station carries: the server's status, the server's
              basis sentence, and — since #50-c, in a fold — the artifact that
              answered. This is the half of the rail that moved here; a state
              belongs beside the evidence that produced it, not on a navigation
              row. The BASIS stays on the surface because it is the verdict's
              justification, which is what "no DONE from silence" is made of;
              the `source · citation` line beneath it was provenance, and
              provenance is what the user's 2026-08-20 decision moved one click
              away. Both are still here, whole. */}
          {stationSteps.map((step) => (
            <div
              key={step.number}
              className={`mt-2.5 rounded-lg border px-3 py-2 ${
                activeStep === step.number ? "border-sky-300 bg-sky-50/60" : "border-slate-200 bg-surface"
              }`}
            >
              <p className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[14px] font-semibold text-slate-500">
                  {circledStep(step.number)}
                </span>
                <span className="text-[15px] font-semibold text-slate-800">{stepLabel(step, lang)}</span>
                <span
                  className={`rounded-full border px-1.5 py-0.5 text-[14px] font-semibold ${statusChipClass(step.status)}`}
                >
                  {statusLabel(step.status, t)}
                </span>
                {step.manual_area && (
                  <InfoTip
                    text={t(
                      "수동 확인 영역 — 이 단계에 대해 이 제품은 아무것도 측정하지 않습니다. Vissim에서 직접 확인하고 이 도구 밖에 기록하십시오.",
                      "Manual-check area — this product measures nothing for this step. Check it in Vissim and record it outside this tool.",
                      "手動確認領域 — この段階について本製品は何も測定しません。Vissimで直接確認し、本ツールの外に記録してください。",
                    )}
                    label={t("수동 확인 영역이란", "What a manual-check area is", "手動確認領域とは")}
                  />
                )}
              </p>
              <p className="mt-1 text-[14px] leading-6 text-slate-700">{pick(step.basis, lang)}</p>
              <details className="mt-1 text-[13px] leading-5 text-slate-500">
                <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
                  {t("자세히 (근거 출처)", "More (which artifact answered)", "詳細（根拠の出典）")}
                </summary>
                <p className="mt-1 border-l border-slate-300 pl-3 font-mono text-[13px] text-slate-500">
                  {step.source}
                  {step.citation ? ` · ${step.citation}` : ""}
                </p>
              </details>
            </div>
          ))}

          {/* ------------------------------------------------------------ */}
          {/* Station bodies. Each one is the evidence and the next action  */}
          {/* for the steps listed above it, and nothing else.              */}
          {/* ------------------------------------------------------------ */}

          {activeStation === "scoring" && (
            <div className="mt-3">
              {/* Two bodies of evidence, one station, and a tab rather than a
                  stack: the absorbed base-model screen is screen-sized, and
                  appending it under the node table is exactly the vertical
                  stack this unit exists to undo. */}
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    ["nodes", t("노드 채점", "Node scoring", "ノード採点")],
                    ["workbook", t("기준 모델 검증", "Base-model validation", "基準モデル検証")],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setScoringView(key)}
                    className={`inline-flex h-9 items-center rounded-md border px-3 text-[14px] transition ${
                      scoringView === key
                        ? "border-sky-300 bg-sky-50 text-sky-600"
                        : "border-slate-300 bg-surface text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {scoringView === "nodes" ? (
                <div className="mt-3">
                  {/* WHICH MODEL COLUMN THIS SCREEN REFUSES TO READ. A rule, not
                      a number, so #50-c folded it — verbatim, because it is the
                      one sentence that explains why a reader's own workbook
                      column does not appear anywhere on this tab. */}
                  <details className="text-[13px] leading-5 text-slate-600">
                    <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
                      {t("자세히 (모델 값의 출처)", "More (where the modelled values come from)", "詳細（モデル値の出典）")}
                    </summary>
                    <p className="mt-1 border-l border-slate-300 pl-3">
                      {t(
                        "모델 값은 선택한 실행의 결과에서 가져옵니다. 워크북에 붙여넣은 모델 열은 다른 실행의 것일 수 있어 사용하지 않습니다.",
                        "Modelled values come from the selected run's own results. The workbook's pasted model column is not used — it may describe a different run.",
                        "モデル値は選択した実行の結果から取得します。ワークブックに貼り付けられたモデル列は別の実行のものである可能性があるため使用しません。",
                      )}
                    </p>
                  </details>

                  {reportError && (
                    <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[14px] leading-6 text-rose-700">
                      {reportError}
                    </p>
                  )}

                  {!scenarioId ? (
                    <p className="mt-3 text-[14px] leading-6 text-slate-600">
                      {t(
                        "위에서 실행을 선택하면 노드별 GEH와 진단이 나타납니다.",
                        "Pick a run above and the per-node GEH and diagnosis appear.",
                        "上で実行を選択すると、ノード別GEHと診断が表示されます。",
                      )}
                    </p>
                  ) : reportLoading ? (
                    <p className="mt-3 text-[14px] text-slate-500">
                      {t("채점하는 중...", "Scoring...", "採点中...")}
                    </p>
                  ) : report ? (
                    <div className="mt-3 grid gap-3 xl:grid-cols-[1.1fr_1fr]">
                      <div className="min-w-0">
                        <NodeScoreTable
                          report={report}
                          activeNodeId={activeNodeId}
                          selectedNodeIds={selectedNodeIds}
                          onNodeClick={handleNodeClick}
                        />
                        {activeNode && (
                          <div className="mt-3">
                            <NodeScoreDetail node={activeNode} threshold={report.geh_threshold} />
                          </div>
                        )}
                      </div>
                      <div className="min-w-0">
                        {/* The map is not decoration here: the scoring station
                            is where a reader asks WHERE the failing nodes are,
                            so it is the one station that always draws one. */}
                        <div className="h-[420px] overflow-hidden rounded-lg border border-slate-200">
                          <BaseValidationMap
                            nodes={mapNodes}
                            selectedNodeIds={selectedNodeIds}
                            activeNodeId={activeNodeId}
                            onNodeClick={(nodeId) => handleNodeClick(nodeId)}
                          />
                        </div>
                        {/* Honest caption, now an ⓘ on the map's own label. The
                            map places a node only when the network geometry
                            carries that node; the GRID is the complete list
                            either way, so a node missing from the map is a
                            missing placement, never a missing score. That claim
                            has to stay reachable, which is exactly what the ⓘ
                            guarantees — hover, focus, tap. */}
                        <p className="mt-1.5 text-[14px] text-slate-500">
                          {t("노드 위치", "Node placement", "ノード位置")}
                          <InfoTip
                            text={t(
                              "노드 위치는 데이터셋의 좌표 정보에서 옵니다. 좌표가 없는 노드는 지도에 나타나지 않으며, 왼쪽 타일이 전체 목록입니다.",
                              "Node placement comes from the dataset's georeference. A node without coordinates does not appear on the map — the tiles beside it are the complete list.",
                              "ノード位置はデータセットの座標情報に基づきます。座標のないノードは地図に表示されません。左のタイルが完全な一覧です。",
                            )}
                            label={t("지도에 대하여", "About this map", "この地図について")}
                          />
                        </p>
                      </div>
                    </div>
                  ) : null}

                  {/* Before/after, same dataset lineage. Toggled in the context
                      bar; shown here, where the GEH it compares is. */}
                  {compareId && (
                    <div className="mt-4 border-t border-slate-200 pt-3">
                      <h3 className="text-[15px] font-bold text-slate-800">
                        {t("전후 비교 — 노드별 GEH 변화", "Before/after — per-node GEH change", "前後比較 — ノード別GEHの変化")}
                      </h3>
                      {compareError ? (
                        <p className="mt-1.5 text-[14px] text-rose-700">{compareError}</p>
                      ) : deltas.length === 0 ? (
                        <p className="mt-1.5 text-[14px] leading-6 text-slate-600">
                          {t(
                            "두 실행 모두에서 등급이 매겨진 노드가 없어 비교할 값이 없습니다.",
                            "No node was graded in both runs, so there is nothing to compare.",
                            "両方の実行で評価されたノードがないため、比較する値がありません。",
                          )}
                        </p>
                      ) : (
                        <table className="mt-1.5 w-full border-collapse text-[14px]">
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
                </div>
              ) : (
                /* The absorbed base-model screen: the workbook, the Category
                   1/2/3 profile, the flow bands, the R² scatter, the travel-time
                   analysis and the §5.6.3 criteria coverage. Same code as before
                   this unit. */
                <div className="mt-3">
                  <BaseModelStation />
                </div>
              )}
            </div>
          )}

          {activeStation === "network" && (
            <div className="mt-3 flex flex-wrap gap-2">
              <Link href="/health" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
                {t("모델 검진 열기", "Open model inspection", "モデル検診を開く")}
              </Link>
              <Link href="/genesis" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
                {t("네트워크 생성·게이트", "Network generation & gate", "ネットワーク生成・ゲート")}
              </Link>
            </div>
          )}

          {activeStation === "signals" && (
            <div className="mt-3 flex flex-col gap-2">
              <StationDoctrine
                t={t}
                text={t(
                  "⑥의 근거는 사람이 서명한 현시도 확인서입니다 — 확인되지 않은 현시도는 신호가 검증됐다는 증거가 아닙니다. ⑦의 근거는 Table 5-6 적용 판정이며, 표 전체는 ⑫ 행태 스테이션에 있습니다.",
                  "Step 6's evidence is a signed 현시도 (phase-diagram) attestation — an unattested diagram is not evidence that the signals were verified. Step 7's evidence is the Table 5-6 application verdict; the full table lives in the ⑫ behaviour station.",
                  "⑥の根拠は人が署名した現示図の確認書です — 未確認の現示図は信号が検証された証拠ではありません。⑦の根拠はTable 5-6の適用判定で、表全体は⑫挙動ステーションにあります。",
                )}
              />
              <div className="flex flex-wrap gap-2">
                <Link href="/genesis" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
                  {t("현시도 검토 열기", "Open 현시도 review", "現示図レビューを開く")}
                </Link>
                <button
                  type="button"
                  onClick={() => handleSelectStation("behaviour")}
                  className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]"
                >
                  {t("Table 5-6 표 보기 (⑫)", "See the Table 5-6 table (⑫)", "Table 5-6の表を見る (⑫)")}
                </button>
              </div>
            </div>
          )}

          {activeStation === "behaviour" && (
            <div className="mt-3">
              <StationDoctrine
                t={t}
                text={t(
                  "각 주행행태의 현재 ax·bx_add·bx_mult가 공표된 표의 한 행 위에 있는지 확인합니다 (단계 ⑦·⑫).",
                  "Whether each driving behaviour's current ax/bx_add/bx_mult sits on one published row of the table (steps 7 and 12).",
                  "各走行挙動の現在のax・bx_add・bx_multが公表された表の一行にあるかを確認します（段階⑦・⑫）。",
                )}
              />
              <div className="mt-2">
                <BehaviourStation rows={appliedRows} loading={appliedLoading} error={appliedError} />
              </div>

              {/* Unit #51's slot. It renders the RAIL's own step-⑫ track — the
                  same component, from the same server payload — rather than a
                  second copy of the locked/available sentence. A second copy is
                  exactly how the two would eventually disagree about whether a
                  search may start, in two places on one screen. When the rail
                  has not loaded there is simply nothing here to say. */}
              {parameterSearchTrack && (
                <div className="mt-3">
                  <ParameterSearchTrack track={parameterSearchTrack} lang={lang} t={t} />
                </div>
              )}
            </div>
          )}

          {/* ⑨, ⑩ and ⑪ each rest on ONE artifact, and the folded sentence says
              which — the RULE, never a second opinion about the verdict, which
              is the step's own basis line above. #50-c folded them all: a rule
              is read once and then re-read only when it is challenged, while
              the basis line above it changes with every run. */}
          {activeStation === "transit" && (
            <StationDoctrine
              t={t}
              text={t(
                "이 단계에 대해 이 제품이 가진 사실은 네트워크의 대중교통 노선 수 하나뿐입니다. 노선이 0개일 때만 '해당 없음'이 되고, 노선이 있으면 보정 여부를 알 방법이 없어 '증거 없음'으로 남습니다.",
                "The only fact this product holds for this step is the network's public-transport line count. Zero lines earns \"not applicable\"; with lines present there is no way to tell a calibrated network from an untouched one, so it stays \"no evidence\".",
                "この段階について本製品が持つ事実は、ネットワークの公共交通路線数だけです。路線が0件のときのみ「該当なし」となり、路線がある場合は較正の有無を判断できないため「証拠なし」のままです。",
              )}
            />
          )}

          {activeStation === "demand" && (
            <StationDoctrine
              t={t}
              text={t(
                "근거는 실행–실측 대조가 낸 수요 제안과 입구 교통량의 실측 확인(D4)입니다. D4가 말하는 것은 '미실측'이 아니라 '확인되지 않음'입니다.",
                "The evidence is the demand proposals the run-versus-observed check raised, plus the D4 entrance-measurement verdicts. D4 says \"unverified\", never \"unmeasured\".",
                "根拠は実行と実測の対照が出した需要提案と、入口交通量の実測確認(D4)です。D4が示すのは「未実測」ではなく「未確認」です。",
              )}
            />
          )}

          {activeStation === "assignment" && (
            <StationDoctrine
              t={t}
              text={t(
                "근거는 실행–실측 대조가 낸 배분 제안과 ROUTING_SPLIT 진단입니다. 제안도 진단도 없다는 것은 배분이 확인됐다는 증거가 아닙니다.",
                "The evidence is the route-split proposals the run-versus-observed check raised and the ROUTING_SPLIT diagnoses. Neither a proposal nor a diagnosis is not evidence that route choice was confirmed.",
                "根拠は実行と実測の対照が出した配分提案とROUTING_SPLIT診断です。提案も診断もないことは、配分が確認された証拠ではありません。",
              )}
            />
          )}

          {/* THE ONE THAT STAYS A HEADLINE. 수동 확인 영역's rule is not
              background a reader may skip: it is the promise that this screen
              will never mark these six steps done. So it keeps a one-line
              verdict on the surface, at the type floor, with the full sentence
              in the fold beneath it. */}
          {activeStation === "manual" && (
            <div className="mt-3">
              <p className="text-[15px] font-semibold leading-6 text-slate-800">
                {t(
                  "이 제품은 위 단계들을 측정하지 않습니다 — 완료로 표시하지 않습니다.",
                  "This product measures nothing for the steps above — it never shows them as done.",
                  "本製品は上記の段階を測定しません — 完了と表示することはありません。",
                )}
              </p>
              <StationDoctrine
                t={t}
                text={t(
                  "위 단계들은 이 제품이 측정하지 않습니다. Vissim에서 직접 확인하고 이 도구 밖에 기록하십시오. 화면이 이 단계들을 완료로 표시하는 일은 없습니다.",
                  "This product measures nothing for the steps above. Check them in Vissim and record them outside this tool — the screen will never show them as done.",
                  "上記の段階について本製品は測定しません。Vissimで直接確認し、本ツールの外に記録してください。画面がこれらを完了と表示することはありません。",
                )}
              />
            </div>
          )}

          {/* Node context, for the stations that HAVE one. Measured, not
              assumed: a station draws a map only when the server named nodes
              for its steps (`next_action.mrwa_step`, or ⑫'s own search split).
              An empty map on ① or ⑨ would be furniture pretending to be
              evidence. */}
          <NodeContext
            nodes={stationNodes}
            extraNodeIds={activeStation === "behaviour" ? behaviourNodeIds : []}
            report={report}
            mapNodes={mapNodes}
            activeNodeId={activeNodeId}
            activeNode={activeNode}
            onNodeClick={handleNodeClick}
            t={t}
          />

          {/* This station's own proposals — inline, where their evidence is. */}
          <div className="mt-4 border-t border-slate-200 pt-3">
            <h3 className="text-[15px] font-bold text-slate-800">
              {t("이 단계의 제안 — 승인 대기", "This step's proposals — awaiting approval", "この段階の提案 — 承認待ち")}
              {/* One approval path, shared with the drawer — a property of the
                  machinery rather than of any one card, so it moved into the ⓘ
                  on the heading it qualifies (#50-c). */}
              <InfoTip
                text={t(
                  "승인 경로는 드로어와 같은 하나뿐입니다.",
                  "There is exactly one approval path, shared with the drawer.",
                  "承認経路はドロワーと同じ一つだけです。",
                )}
                label={t("승인 경로에 대하여", "About the approval path", "承認経路について")}
              />
            </h3>
            <div className="mt-2">
              <StationProposals
                placed={stationProposals}
                otherCount={otherProposalCount}
                loading={proposals.loading}
                error={proposals.error}
                decide={proposals.decide}
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

/**
 * The nodes this station's steps are about, with a map and the shared detail.
 *
 * Renders NOTHING when the server named no node for these steps — which is the
 * normal, correct state for ① and ⑨. That is the whole rule the user asked for
 * ("지도는 의미 있는 스테이션에서만"): a map appears where there are nodes to
 * place, and nowhere else.
 */
function NodeContext({
  nodes,
  extraNodeIds,
  report,
  mapNodes,
  activeNodeId,
  activeNode,
  onNodeClick,
  t,
}: {
  nodes: CalibrationRealityNode[];
  extraNodeIds: string[];
  report: CalibrationRealityCheck | null;
  mapNodes: BaseValidationMapNode[];
  activeNodeId: string | null;
  activeNode: CalibrationRealityNode | null;
  onNodeClick: (nodeId: string) => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const ids = [
    ...nodes.map((node) => node.node_id),
    ...extraNodeIds.filter((id) => !nodes.some((node) => node.node_id === id)),
  ];
  if (ids.length === 0) return null;

  const named = new Map((report?.nodes ?? []).map((node) => [node.node_id, node]));

  return (
    <div className="mt-3 border-t border-slate-200 pt-3">
      <h3 className="text-[15px] font-bold text-slate-800">
        {t("이 단계가 가리키는 노드", "The nodes this step points at", "この段階が指すノード")}
        {/* The caption under the map said two things: which nodes are
            highlighted, and that an unplaced node is a missing placement rather
            than a missing node. Both are RULES about the map, so #50-c put them
            in the ⓘ on the heading — reachable by hover, focus and tap. */}
        <InfoTip
          text={t(
            "강조된 노드는 이 단계의 다음 행동이 가리키는 노드입니다. 좌표가 없는 노드는 지도에 나타나지 않습니다.",
            "The highlighted nodes are the ones this step's next action points at. A node without coordinates does not appear on the map.",
            "強調されたノードは、この段階の次の行動が指すノードです。座標のないノードは地図に表示されません。",
          )}
          label={t("이 지도에 대하여", "About this map", "この地図について")}
        />
      </h3>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {ids.map((id) => {
          const node = named.get(id);
          return (
            <li key={id}>
              <button
                type="button"
                onClick={() => onNodeClick(id)}
                className={`rounded-full border px-2 py-0.5 text-[14px] transition ${
                  activeNodeId === id
                    ? "border-sky-300 bg-sky-50 text-sky-600"
                    : "border-slate-300 bg-surface text-slate-700 hover:bg-slate-50"
                }`}
              >
                <span className="font-mono">{id}</span>
                {node?.node_name ? <span className="ml-1.5">{node.node_name}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="mt-2 h-[280px] overflow-hidden rounded-lg border border-slate-200">
        <BaseValidationMap
          nodes={mapNodes}
          selectedNodeIds={ids}
          activeNodeId={activeNodeId}
          onNodeClick={(nodeId) => onNodeClick(nodeId)}
        />
      </div>

      {/* The SAME detail pane the scoring grid opens — one component, so a
          node reads identically wherever a reader reaches it, AND the same bar:
          this station holds the very report the scoring grid scored, so it
          threads that report's own `geh_threshold` rather than leaving the pane
          verdictless beside a grid that has one. `?.` because the report may
          not have loaded yet — and with no bar `nodeGehPasses` returns null, so
          the GEH prints as a number with no verdict colour, which is the honest
          rendering of "this pane has not been told what the bar is". */}
      {activeNode && ids.includes(activeNode.node_id) && (
        <div className="mt-2">
          <NodeScoreDetail node={activeNode} threshold={report?.geh_threshold} />
        </div>
      )}
    </div>
  );
}

/**
 * ONE folded rule, in the app's existing disclosure vocabulary (#50-c).
 *
 * The stations each opened with two to four sentences of RULE — what step ⑨
 * rests on, what D4 means, why an unattested 현시도 is not evidence — sitting at
 * full weight above the numbers they govern. The user's 2026-08-20 decision was
 * that this is what saturates the screen. NOTHING IS DELETED: the sentence goes
 * in here verbatim, in all three languages, one click from where it was.
 *
 * A `<details>` rather than an `InfoTip` on purpose: these are paragraphs a
 * reader settles down with, and a hover panel is for a phrase.
 */
function StationDoctrine({ text, t }: { text: string; t: (ko: string, en: string, ja?: string) => string }) {
  return (
    <details className="mt-1 text-[13px] leading-5 text-slate-600">
      <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
        {t("자세히 (이 단계의 근거 규칙)", "More (the rule this step rests on)", "詳細（この段階の根拠規則）")}
      </summary>
      <p className="mt-1 border-l border-slate-300 pl-3">{text}</p>
    </details>
  );
}

"use client";

// ---------------------------------------------------------------------------
// The scoring table (unit #50): one row per node of a run-vs-observed check.
//
// Source: `GET /api/calibration/reality-check`, which is `reality_check`'s own
// report — the same object the agent gets. Pass/fail is the report's own
// `geh_threshold`, never a number this file chose, and the per-node rung chip
// is unit #43's `diagnosis.rung` as the backend classified it.
//
// Rows cross-highlight with the map beside them: the same `selectedNodeIds` /
// `activeNodeId` / `onNodeClick` contract `BaseValidationMap` already speaks,
// so clicking a row moves the map and clicking a marker moves the table.
//
// WHAT THE DETAIL PANE SHOWS, AND WHAT IT REFUSES TO SHOW. It shows the
// evidence the report itself carries: the totals and their GEH, the D4
// entrance verdict, the run's delay/queue coverage per approach, the next
// action with its MRWA step, and the raw codes behind a fold (#41's doctrine —
// raw material is folded, never in the reader's face). It does NOT compute a
// per-movement GEH from the workbook's own model column: `reality_check`
// explicitly ignores that column (a pasted export that may describe a
// different run), and a movement table built from it would contradict the node
// row printed directly above it.
// ---------------------------------------------------------------------------

import { useLanguage } from "@/components/LanguageProvider";
import type { CalibrationRealityCheck, CalibrationRealityNode } from "@/lib/api";

type Translate = (ko: string, en: string, ja?: string) => string;

// The five rungs geh_ladder.py can return. Same words the drawer uses (see
// AGENT_RUNG_LABELS in components/agent/ApprovalCards.tsx) so one node reads
// identically on both surfaces.
const RUNG_LABELS: Record<string, [string, string, string]> = {
  DEMAND_LEVEL: ["수요 수준", "Demand level", "需要水準"],
  ROUTING_SPLIT: ["회전 배분", "Turn distribution", "回転配分"],
  CAPACITY_LIMIT: ["용량 한계", "Capacity limit", "容量限界"],
  SIGNAL_TIMING: ["신호 시간", "Signal timing", "信号時間"],
  UNCLASSIFIABLE: ["분류 불가", "Unclassifiable", "分類不可"],
};

function pick(text: { ko: string; en: string; ja: string } | undefined | null, lang: string): string {
  if (!text) return "";
  if (lang === "ko") return text.ko;
  if (lang === "ja") return text.ja;
  return text.en;
}

function rungLabel(rung: string, t: Translate): string {
  const entry = RUNG_LABELS[rung];
  return entry ? t(entry[0], entry[1], entry[2]) : rung;
}

function fmt(value: number | null | undefined, digits = 1): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "-";
}

function nodeGehPasses(node: CalibrationRealityNode, threshold: number | null | undefined): boolean | null {
  if (typeof node.node_total_geh !== "number" || typeof threshold !== "number") return null;
  return node.node_total_geh < threshold;
}

export default function NodeScoreTable({
  report,
  activeNodeId,
  selectedNodeIds,
  onNodeClick,
}: {
  report: CalibrationRealityCheck;
  activeNodeId: string | null;
  selectedNodeIds: string[];
  onNodeClick: (nodeId: string, nodeName: string) => void;
}) {
  const { t, lang } = useLanguage();
  const nodes = report.nodes ?? [];
  const threshold = report.geh_threshold ?? null;

  if (report.status !== "CHECKED") {
    // A refusal is an ANSWER here — reality_check wrote the sentence in three
    // languages and carries a machine code. Printing our own would be a second
    // opinion about a fact we did not establish.
    return (
      <div className="rounded-lg border border-slate-300 bg-slate-50 px-3 py-2.5 text-[14px] leading-6 text-slate-700">
        <p>{pick(report.message, lang)}</p>
        {report.reason_code && (
          <p className="mt-1 font-mono text-[13px] text-slate-500">{report.reason_code}</p>
        )}
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="rounded-lg border border-slate-300 bg-slate-50 px-3 py-2.5 text-[14px] leading-6 text-slate-700">
        {t(
          "이 실행에서 실측과 대조된 노드가 없습니다.",
          "This run had no node to compare against observed counts.",
          "この実行では実測と対照されたノードがありません。",
        )}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-slate-200 text-left text-slate-500">
            <th className="px-2 py-1.5 font-semibold">{t("노드", "Node", "ノード")}</th>
            <th className="px-2 py-1.5 text-right font-semibold">{t("실측", "Observed", "実測")}</th>
            <th className="px-2 py-1.5 text-right font-semibold">{t("모델", "Modelled", "モデル")}</th>
            <th className="px-2 py-1.5 text-right font-semibold">GEH</th>
            <th className="px-2 py-1.5 font-semibold">{t("진단", "Diagnosis", "診断")}</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => {
            const passes = nodeGehPasses(node, threshold);
            const active = activeNodeId === node.node_id;
            const selected = selectedNodeIds.includes(node.node_id);
            return (
              <tr
                key={node.node_id}
                onClick={() => onNodeClick(node.node_id, String(node.node_name ?? node.node_id))}
                className={`cursor-pointer border-b border-slate-100 ${
                  active ? "bg-sky-50" : selected ? "bg-sky-50/40" : "hover:bg-slate-50"
                }`}
              >
                <td className="px-2 py-1.5">
                  <span className="font-mono text-slate-700">{node.node_id}</span>
                  {node.node_name && <span className="ml-1.5 text-slate-600">{node.node_name}</span>}
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-slate-600">{fmt(node.observed_total, 0)}</td>
                <td className="px-2 py-1.5 text-right font-mono text-slate-600">{fmt(node.modelled_total, 0)}</td>
                <td className="px-2 py-1.5 text-right">
                  {/* Pass emerald, fail rose — the report's own threshold
                      decides which, and an undefined GEH is neither. */}
                  <span
                    className={`rounded-full border px-1.5 py-0.5 font-mono font-semibold ${
                      passes === null
                        ? "border-slate-300 bg-surface text-slate-500"
                        : passes
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : "border-rose-200 bg-rose-50 text-rose-700"
                    }`}
                  >
                    {fmt(node.node_total_geh)}
                  </span>
                </td>
                <td className="px-2 py-1.5">
                  {node.diagnosis ? (
                    <span
                      className={`rounded-full border px-1.5 py-0.5 text-[13px] font-bold uppercase tracking-[0.04em] ${
                        node.diagnosis.rung === "UNCLASSIFIABLE"
                          ? "border-slate-400 bg-slate-100 text-slate-800"
                          : "border-rose-200 bg-rose-50 text-rose-700"
                      }`}
                    >
                      {rungLabel(node.diagnosis.rung, t)}
                    </span>
                  ) : (
                    <span className="text-slate-500">
                      {t("진단 없음 (재현)", "no diagnosis (reproduced)", "診断なし（再現）")}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <p className="mt-1.5 font-mono text-[13px] text-slate-500">
        {t("GEH 기준", "GEH threshold", "GEH閾値")} {fmt(threshold)} ·{" "}
        {t("비교 노드", "nodes compared", "比較ノード")} {report.coverage?.counts?.compared ?? nodes.length} ·{" "}
        {t("등급 불가 이동류", "ungradable movements", "評価不可の移動流")}{" "}
        {report.coverage?.counts?.movements_ungradable ?? 0}
      </p>
    </div>
  );
}

/** The detail pane for ONE node — the evidence behind its row. */
export function NodeScoreDetail({ node }: { node: CalibrationRealityNode }) {
  const { t, lang } = useLanguage();
  const evidence = (node.outcome?.evidence ?? {}) as Record<string, unknown>;
  const target = (evidence.target ?? {}) as Record<string, unknown>;
  const entrance = (target.entrance_measurement ?? null) as Record<string, unknown> | null;
  const diagnosisEvidence = (node.diagnosis?.evidence ?? {}) as Record<string, unknown>;
  const congestion = (diagnosisEvidence.congestion ?? {}) as Record<string, unknown>;
  const approaches = Array.isArray(congestion.approaches) ? (congestion.approaches as unknown[]) : [];
  const nextAction = node.diagnosis?.next_action;

  return (
    <div className="rounded-lg border border-slate-200 bg-surface px-3 py-2.5">
      <p className="text-[14px] font-bold text-slate-900">
        {t("노드", "Node", "ノード")} {node.node_id}
        {node.node_name ? ` · ${node.node_name}` : ""}
      </p>

      {/* The one sentence a modeller acts on, in the app's proposal accent. */}
      {nextAction && (
        <p className="mt-1.5 rounded-md border border-sky-200 bg-sky-50 px-2 py-1.5 text-[13px] leading-5 text-slate-800">
          {pick(nextAction.text, lang)}
          <span className="mt-0.5 block font-mono text-[13px] text-slate-500">
            {nextAction.mrwa_step != null
              ? `${t("단계", "step", "段階")} ${nextAction.mrwa_step} · ${nextAction.citation}`
              : nextAction.citation}
          </span>
        </p>
      )}

      {node.diagnosis && (
        <p className="mt-1.5 text-[13px] leading-5 text-slate-700">{pick(node.diagnosis.why, lang)}</p>
      )}

      {/* D4: "unverified" is not "unmeasured", and the card must not blur the
          two — the verdict's own status word is printed, never a paraphrase. */}
      {entrance && (
        <p className="mt-1.5 text-[13px] leading-5 text-slate-700">
          <span className="font-semibold">{t("입구 실측 확인", "Entrance measurement", "流入部の実測確認")}: </span>
          <span className="font-mono">{String(entrance.status ?? "-")}</span>
          {entrance.basis ? <span className="block text-slate-600">{String(entrance.basis)}</span> : null}
        </p>
      )}

      {/* Delay/queue coverage — the evidence the capacity rungs rest on. The
          COVERAGE is stated alongside the worst value, because "no delay row"
          is not "no delay" (a starved approach is graded not at all). */}
      {Object.keys(congestion).length > 0 && (
        <p className="mt-1.5 text-[13px] leading-5 text-slate-700">
          <span className="font-semibold">{t("지체 증거", "Delay evidence", "遅延の根拠")}: </span>
          <span className="font-mono">{String(congestion.availability ?? "-")}</span>
          {typeof congestion.worst_delay_s === "number" && (
            <span className="font-mono">
              {" · "}
              {t("최악", "worst", "最悪")} {congestion.worst_delay_s}s
              {congestion.worst_los ? ` (LOS ${String(congestion.worst_los)})` : ""}
            </span>
          )}
          {approaches.length > 0 && (
            <span className="font-mono">
              {" · "}
              {t("접근로", "approaches", "流入路")} {approaches.length}
            </span>
          )}
        </p>
      )}

      {/* #41 doctrine: raw material behind a fold, never in the reader's face. */}
      <details className="mt-2 text-[13px] leading-5 text-slate-600">
        <summary className="cursor-pointer list-none text-[13px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (원자료·코드)", "More (raw evidence, codes)", "詳細（原資料・コード）")}
        </summary>
        <div className="mt-1.5 flex flex-col gap-1.5 border-l border-slate-300 pl-3">
          <p className="font-mono text-[13px] text-slate-500">
            {node.outcome?.outcome} · {node.outcome?.scope}
            {node.outcome?.reason_code ? ` · ${node.outcome.reason_code}` : ""}
            {node.diagnosis ? ` · ${node.diagnosis.rung}` : ""}
          </p>
          {node.diagnosis && node.diagnosis.missing_evidence.length > 0 && (
            <p className="font-mono text-[13px] text-slate-500">
              {t("빠진 근거", "missing evidence", "不足している根拠")}: {node.diagnosis.missing_evidence.join(", ")}
            </p>
          )}
          {typeof evidence.arithmetic === "string" && (
            <p className="font-mono text-[13px] text-slate-500">{evidence.arithmetic}</p>
          )}
          <pre className="max-h-64 overflow-auto rounded-md bg-slate-50 p-2 font-mono text-[13px] text-slate-600">
            {JSON.stringify(node.outcome?.evidence ?? {}, null, 2)}
          </pre>
        </div>
      </details>
    </div>
  );
}

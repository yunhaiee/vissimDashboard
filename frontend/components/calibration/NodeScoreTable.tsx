"use client";

// ---------------------------------------------------------------------------
// The scoring surface (unit #50, restructured by #50-c): one TILE per node of a
// run-vs-observed check.
//
// Source: `GET /api/calibration/reality-check`, which is `reality_check`'s own
// report — the same object the agent gets. Pass/fail is the report's own
// `geh_threshold`, never a number this file chose, and the per-node rung chip
// is unit #43's `diagnosis.rung` as the backend classified it.
//
// WHY A GRID AND NOT A TABLE (the user, 2026-08-20, on the real screen). The
// list was five 13px columns down one narrow column beside the map: to answer
// "which nodes fail", a reader had to read every row's GEH and compare it to a
// threshold printed in a mono line under the table. The question a scoring
// screen exists for is answered by SHAPE — one tile per node, the node-total
// GEH as the biggest thing on it, and the tile's own colour carrying the
// report's verdict. The five columns are not deleted: they moved into the
// node's detail, which a click on its tile opens.
//
// Tiles cross-highlight with the map beside them through the SAME
// `selectedNodeIds` / `activeNodeId` / `onNodeClick` contract the rows spoke
// (`BaseValidationMap` speaks it too), so clicking a tile moves the map and
// clicking a marker moves the grid. Verdict is the FILL; selection is a sky
// RING, because a tile cannot carry two meanings in one channel.
//
// WHAT THE DETAIL PANE SHOWS, AND WHAT IT REFUSES TO SHOW. It shows the
// evidence the report itself carries: the totals and their GEH, the D4
// entrance verdict, the run's delay/queue coverage per approach, the next
// action with its MRWA step, and the raw codes behind a fold (#41's doctrine —
// raw material is folded, never in the reader's face). It does NOT compute a
// per-movement GEH from the workbook's own model column: `reality_check`
// explicitly ignores that column (a pasted export that may describe a
// different run), and a movement table built from it would contradict the node
// totals printed directly above it.
// ---------------------------------------------------------------------------

import { useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  postCalibrationDemandTarget,
  revokeCalibrationDemandTarget,
  type CalibrationDemandTargetNode,
  type CalibrationRealityCheck,
  type CalibrationRealityNode,
} from "@/lib/api";

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

/** Pass, fail, or neither — the REPORT's own threshold decides, and an
 * undefined GEH is neither. Exported because the workspace's context bar states
 * the same verdict in one line ("6노드 중 4 합격"): a second copy of this
 * comparison is how a header and the table under it would come to disagree
 * about which nodes passed. */
export function nodeGehPasses(node: CalibrationRealityNode, threshold: number | null | undefined): boolean | null {
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
    //
    // #50-c: the machine code moved into the fold. A bare `reason_code` echoed
    // under the sentence told a modeller nothing the sentence had not already
    // said, and it is one of the mono lines the user ordered off the surface
    // (2026-08-20). It is still here, verbatim, one click away — it is the
    // string a bug report quotes.
    return (
      <div className="rounded-lg border border-slate-300 bg-slate-50 px-3 py-2.5 text-[14px] leading-6 text-slate-700">
        <p>{pick(report.message, lang)}</p>
        {report.reason_code && (
          <details className="mt-1 text-[13px] leading-5 text-slate-500">
            <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
              {t("자세히 (판정 코드)", "More (the refusal code)", "詳細（判定コード）")}
            </summary>
            <p className="mt-1 border-l border-slate-300 pl-3 font-mono text-[13px] text-slate-500">
              {report.reason_code}
            </p>
          </details>
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
    <div>
      {/* THE GRID. One tile, one node, one number a reader can see from across
          a desk. The tile's FILL is the report's verdict (emerald pass, rose
          fail, neutral ungradable) and the sky RING is selection — two
          channels, because a tile that painted selection in its fill would
          have to lie about one of the two. */}
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {nodes.map((node) => {
          const passes = nodeGehPasses(node, threshold);
          const active = activeNodeId === node.node_id;
          const selected = selectedNodeIds.includes(node.node_id);
          // #50-e (2026-08-23), corrected after the refuter's measurement on
          // the live app: at the tile grid's real width (xl:grid-cols-3
          // inside the report/map split inside the rail split -- ~97px at
          // 1280x900) every rung label WRAPS -- ko/ja run two lines, en runs
          // up to three ("Turn distribution →"). Computed once so the
          // clamped span below and its title/aria fallback can never
          // disagree with each other.
          const rungFooterText =
            passes === false && node.diagnosis
              ? `${rungLabel(node.diagnosis.rung, t)} →`
              : passes === null
                ? t("등급 불가", "ungradable", "評価不可")
                : "";
          return (
            // #60a (2026-08-25, live-test feedback -- "node 5 is shorter"):
            // MEASURED (static reproduction of this exact markup at the
            // grid's real column width, mirroring #50-e's own
            // measure-before-fix discipline): the `<ul>`'s default CSS Grid
            // `align-items: stretch` already equalises every `<li>` in a row
            // to its tallest neighbour -- but the visible bordered `<button>`
            // inside was never told to FILL that stretched height, so it
            // stayed shrink-wrapped to its own content. #50-e's min-h-[40px]
            // footer (below) already equalises the FOOTER between a passing
            // and a failing/ungradable tile (measured: both 117px in the
            // repro); the remaining gap was purely a taller sibling (a
            // wrapped node name, or a wrapped rung label past what the
            // footer's own 2-line cap absorbs) stretching the invisible
            // `<li>` while a shorter tile's visible button never grew to
            // match -- exactly "node 5 is shorter" while its row-mate carries
            // a diagnosis line. `h-full` on the button is the fix: with it,
            // three tiles built to differ on purpose (no footer content, a
            // one-line diagnosis, a wrapped node name) all measured the SAME
            // 161px button height in the repro; without it they measured
            // 117/117/161.
            <li key={node.node_id}>
              <button
                type="button"
                onClick={() => onNodeClick(node.node_id, String(node.node_name ?? node.node_id))}
                aria-pressed={active}
                className={`w-full h-full rounded-lg border px-3 py-2.5 text-left transition ${
                  passes === null
                    ? "border-slate-300 bg-surface hover:bg-slate-50"
                    : passes
                      ? "border-emerald-200 bg-emerald-50"
                      : "border-rose-200 bg-rose-50"
                } ${active ? "ring-2 ring-sky-400" : selected ? "ring-1 ring-sky-400" : ""}`}
              >
                <span className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                  <span className="font-mono text-[14px] font-semibold text-slate-700">{node.node_id}</span>
                  {node.node_name && <span className="text-[14px] text-slate-600">{node.node_name}</span>}
                </span>
                <span className="mt-1 flex items-end justify-between gap-2">
                  {/* The node-total GEH, at the size the question deserves. */}
                  <span
                    className={`font-mono text-[24px] font-bold leading-none ${
                      passes === null ? "text-slate-500" : passes ? "text-emerald-700" : "text-rose-700"
                    }`}
                  >
                    {fmt(node.node_total_geh)}
                  </span>
                  <span className="text-[14px] font-semibold text-slate-500">GEH</span>
                </span>
                {/* A FAILING tile carries its own next rung — #43's own
                    classification, so the tile answers "and then what" without
                    being opened. A passing tile says nothing extra: there is
                    nothing to climb.

                    #50-e (2026-08-23), CORRECTED same day: min-h-[20px] alone
                    floored a pass tile up to a one-line fail tile, but it did
                    not CAP a fail tile that wrapped past one line -- and at
                    this grid's real column width every rung label wraps (ko
                    "회전 배분 →" 2 lines, ja 2 lines, en up to 3 for "Turn
                    distribution →"), so a wrapped tile grew past its
                    unwrapped neighbours anyway. The wrapper now reserves TWO
                    lines (min-h-[40px]) on every tile, pass included, AND
                    caps at two with line-clamp-2, so height is identical
                    regardless of verdict or wrap. A clamped label is still an
                    ACTION a reader must be able to read in full -- so the
                    untruncated text rides along as `title` (mouse hover) and
                    `aria-label` (assistive tech), never silently cut. */}
                <span
                  className="mt-1.5 block min-h-[40px] line-clamp-2 text-[14px] leading-5"
                  title={rungFooterText || undefined}
                  aria-label={rungFooterText || undefined}
                >
                  {passes === false && node.diagnosis && (
                    <span className="font-semibold text-slate-700">
                      {rungLabel(node.diagnosis.rung, t)} →
                    </span>
                  )}
                  {passes === null && (
                    <span className="text-slate-500">{t("등급 불가", "ungradable", "評価不可")}</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {/* The report's own coverage, as NUMBERS rather than as a mono sentence
          under a table (#50-c). The ungradable count is stated beside the
          compared count on purpose: a node this run could not grade is not a
          node that passed. */}
      <ul className="mt-2 flex flex-wrap gap-1.5">
        {[
          [t("GEH 기준", "GEH threshold", "GEH閾値"), fmt(threshold)],
          [t("비교 노드", "nodes compared", "比較ノード"), String(report.coverage?.counts?.compared ?? nodes.length)],
          [
            t("등급 불가 이동류", "ungradable movements", "評価不可の移動流"),
            String(report.coverage?.counts?.movements_ungradable ?? 0),
          ],
        ].map(([label, value]) => (
          <li
            key={label}
            className="rounded-full border border-slate-300 bg-surface px-2 py-0.5 text-[14px] text-slate-600"
          >
            {label} <span className="font-mono font-semibold text-slate-800">{value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The detail pane for ONE node — the evidence behind its tile.
 *
 * #50-c moved the grid's five old COLUMNS in here (observed, modelled, GEH,
 * diagnosis): the tile answers "does this node pass and what rung is next", and
 * everything a reader needs only once they have picked a node belongs to the
 * node they picked. Both call sites thread the SAME report's `geh_threshold`,
 * because two panes rendering one node with two different verdicts is exactly
 * what one shared component exists to prevent. `threshold` is optional because
 * a report may not have loaded yet; without it the GEH is printed as a number
 * with no verdict, which is the honest rendering of "this pane was not told
 * what the bar is" — never a bar this file chose. */
// #50-e (2026-08-23): the same window-event handoff app/health/page.tsx and
// components/dashboard/CurationBar.tsx already use to reach the
// globally-mounted FloatingAssistant (components/AppShell.tsx). Not a shared
// import -- both of those files make the same choice for the same reason,
// recorded in their own comments: the event NAME is the contract between
// independent dispatchers, not a module either side needs to import.
const ASSISTANT_HANDOFF_EVENT = "vissim:assistant-handoff";

function requestAgentHandoff(prompt: string) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_HANDOFF_EVENT, { detail: { prompt } }));
}

/** The assistant prompt for the next-action CTA. `text` is next_action's own
 * server words (never a diagnosis this file composes -- see the file-top
 * comment on what this pane refuses to compute); the wrapper sentence and the
 * node id are the only things added, in all three languages, so the
 * assistant opens already knowing which node and which action without the
 * user retyping either. */
function buildNextActionPrompt(
  nodeId: string,
  text: { ko: string; en: string; ja: string },
  t: Translate,
): string {
  return t(
    `노드 ${nodeId}의 다음 조치: "${text.ko}" 지금 도와줘.`,
    `Node ${nodeId}'s next action: "${text.en}" Please help me do this now.`,
    `ノード${nodeId}の次の対応: 「${text.ja}」今すぐ手伝ってください。`,
  );
}

/** geh_ladder.py's own code for "no vehicle input was named for this node".
 *
 * A WIRE value, not a word this file chose: it is what
 * `diagnosis.missing_evidence` carries, and it is the only thing that decides
 * whether the picker below has any business appearing. A client-side rule
 * ("show it when the rung is UNCLASSIFIABLE", say) would offer the control on
 * nodes whose gap is something else entirely. */
const NO_DEMAND_TARGET = "NO_DEMAND_TARGET";

export function NodeScoreDetail({
  node,
  threshold,
  demandTarget,
  onDemandTargetChanged,
}: {
  node: CalibrationRealityNode;
  threshold?: number | null;
  /** The SERVER's candidate list and standing mapping for THIS node (#54), or
   * null when the page has not read them. Null renders no picker at all — an
   * invented candidate is the one thing this control must never show. */
  demandTarget?: CalibrationDemandTargetNode | null;
  onDemandTargetChanged?: () => void;
}) {
  const { t, lang } = useLanguage();
  const passes = nodeGehPasses(node, threshold);
  const evidence = (node.outcome?.evidence ?? {}) as Record<string, unknown>;
  const target = (evidence.target ?? {}) as Record<string, unknown>;
  const entrance = (target.entrance_measurement ?? null) as Record<string, unknown> | null;
  const diagnosisEvidence = (node.diagnosis?.evidence ?? {}) as Record<string, unknown>;
  const congestion = (diagnosisEvidence.congestion ?? {}) as Record<string, unknown>;
  const approaches = Array.isArray(congestion.approaches) ? (congestion.approaches as unknown[]) : [];
  const nextAction = node.diagnosis?.next_action;

  return (
    <div className="rounded-lg border border-slate-200 bg-surface px-3 py-2.5">
      <p className="text-[15px] font-bold text-slate-900">
        {t("노드", "Node", "ノード")} {node.node_id}
        {node.node_name ? ` · ${node.node_name}` : ""}
      </p>

      {/* THE OLD GRID'S COLUMNS, now here — observed, modelled, the graded GEH
          and the rung. Numbers at reading size, each under the word that names
          it, so the comparison a reader came for is one glance rather than five
          columns of 13px. */}
      <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          [t("실측", "Observed", "実測"), fmt(node.observed_total, 0), "text-slate-900"],
          [t("모델", "Modelled", "モデル"), fmt(node.modelled_total, 0), "text-slate-900"],
          [
            "GEH",
            fmt(node.node_total_geh),
            passes === null ? "text-slate-500" : passes ? "text-emerald-700" : "text-rose-700",
          ],
        ].map(([label, value, tone]) => (
          <div key={label} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
            <dt className="text-[14px] text-slate-500">{label}</dt>
            <dd className={`mt-0.5 font-mono text-[20px] font-bold leading-none ${tone}`}>{value}</dd>
          </div>
        ))}
        <div className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
          <dt className="text-[14px] text-slate-500">{t("진단", "Diagnosis", "診断")}</dt>
          <dd className="mt-1">
            {node.diagnosis ? (
              <span
                className={`inline-block rounded-full border px-1.5 py-0.5 text-[14px] font-bold ${
                  node.diagnosis.rung === "UNCLASSIFIABLE"
                    ? "border-slate-400 bg-slate-100 text-slate-800"
                    : "border-rose-200 bg-rose-50 text-rose-700"
                }`}
              >
                {rungLabel(node.diagnosis.rung, t)}
              </span>
            ) : (
              <span className="text-[14px] text-slate-500">
                {t("진단 없음 (재현)", "no diagnosis (reproduced)", "診断なし（再現）")}
              </span>
            )}
          </dd>
        </div>
      </dl>

      {/* The one sentence a modeller acts on, in the app's proposal accent,
          now with a CTA rather than just advice. Its MRWA step and citation
          still live in the fold below (#50-c, 2026-08-20): the action is what
          a reader acts on, the paragraph number is what an auditor checks.

          #50-e (2026-08-23): this box used to end at the sentence. Reusing
          the SAME window-event handoff app/health/page.tsx and
          CurationBar.tsx already use to reach the globally-mounted
          FloatingAssistant (components/AppShell.tsx, mounted once for every
          route including this one) -- no new mechanism, no invented route.
          The prompt is built from the SERVER's own next_action.text (this
          file never composes a diagnosis of its own -- see the header
          comment on what this pane refuses to compute) plus the node id, so
          the assistant opens already knowing which node and which action. */}
      {nextAction && (
        <div className="mt-2 rounded-md border border-sky-200 bg-sky-50 px-2 py-1.5">
          <p className="text-[14px] leading-6 text-slate-800">{pick(nextAction.text, lang)}</p>
          <button
            type="button"
            onClick={() => requestAgentHandoff(buildNextActionPrompt(node.node_id, nextAction.text, t))}
            className="mt-1 inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-[14px] font-semibold text-accent-deep transition hover:border-accent/60 hover:bg-accent/15"
          >
            {t("AI에게 물어보기", "Ask the AI", "AIに聞く")}
            <span aria-hidden>→</span>
          </button>
        </div>
      )}

      {/* #54: the CHOICE the refusal above was asking for. It sits BELOW the
          next-action box on purpose and does not replace its CTA: the two are
          different acts on the same gap — assign the input directly, or hand
          the node to the assistant — and a reader who wants the second must not
          have to find it somewhere else once the first appears. */}
      <DemandTargetPicker
        node={node}
        demandTarget={demandTarget ?? null}
        onChanged={onDemandTargetChanged}
      />

      {/* D4: "unverified" is not "unmeasured", and the card must not blur the
          two — the verdict's own status word is printed, never a paraphrase. */}
      {entrance && (
        <p className="mt-1.5 text-[14px] leading-6 text-slate-700">
          <span className="font-semibold">{t("입구 실측 확인", "Entrance measurement", "流入部の実測確認")}: </span>
          <span className="font-mono">{String(entrance.status ?? "-")}</span>
          {entrance.basis ? <span className="block text-slate-600">{String(entrance.basis)}</span> : null}
        </p>
      )}

      {/* #41 doctrine: raw material behind a fold, never in the reader's face.

          #50-e (2026-08-23 — user authority): the diagnosis.why paragraph and
          the delay-evidence line used to sit HERE on the surface, directly
          under the next-action box above -- restating in prose the verdict
          the box already acts on, plus a line of developer-facing coverage
          data a modeller does not need to read in order to act. Neither
          sentence is deleted: both are now the first two things behind this
          same fold, one click from the numbers they explain -- the #50-c
          "nothing is deleted, one click away" doctrine applied a second
          time, to a second surface. */}
      <details className="mt-2 text-[13px] leading-5 text-slate-600">
        <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (근거·출처·코드)", "More (evidence, citation, codes)", "詳細（根拠・出典・コード）")}
        </summary>
        <div className="mt-1.5 flex flex-col gap-1.5 border-l border-slate-300 pl-3">
          {node.diagnosis && (
            <p className="text-[14px] leading-6 text-slate-700">{pick(node.diagnosis.why, lang)}</p>
          )}
          {Object.keys(congestion).length > 0 && (
            <p className="text-[14px] leading-6 text-slate-700">
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
          {nextAction && (
            <p className="font-mono text-[13px] text-slate-500">
              {nextAction.mrwa_step != null
                ? `${t("단계", "step", "段階")} ${nextAction.mrwa_step} · ${nextAction.citation}`
                : nextAction.citation}
            </p>
          )}
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

/**
 * Unit #54 — 차량 인풋 지정: the choice a refusal was asking a person to make.
 *
 * THE PROBLEM. `reality_check_targets` names a node's demand target only where
 * exactly one vehicle input feeds a link the node was counted entering from. On
 * the real client model node 8 has SIX candidates over two approach links, so
 * the derivation refuses, the ladder cannot establish whether that entrance's
 * volume is measured data, and the node reads 분류 불가 forever — while the
 * calibration harness, which hardcodes one human's graph analysis for that same
 * node, reaches 용량 한계. The knowledge existed; the screen had nowhere to put
 * it.
 *
 * WHAT THIS CONTROL IS NOT. A guess, and not a fix either. It records an
 * ASSUMPTION with the signed-in user's name on it: the proposal it eventually
 * unlocks still carries `fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL`. Naming makes
 * that assumption attributed, never proven — which is why the line beside the
 * submit button says so in one sentence, and why the doctrine behind it is at
 * /docs rather than here.
 *
 * EVERY NUMBER ON IT IS THE SERVER'S. The candidate list is
 * `reality_check_targets`' own per-node output, the store checks a choice
 * against that same list, and this file computes nothing about it — a
 * client-assembled candidate would show a modeller an option the server would
 * then refuse. Nor does submitting decide anything: `onChanged` re-reads the
 * report and the rail, and the node's new classification is whatever the server
 * says on that read.
 */
function DemandTargetPicker({
  node,
  demandTarget,
  onChanged,
}: {
  node: CalibrationRealityNode;
  demandTarget: CalibrationDemandTargetNode | null;
  onChanged?: () => void;
}) {
  const { t } = useLanguage();
  // One form, two acts — the same shape #53's signature row uses, for the same
  // reason: both demand a reason, and the button that submits says which act it
  // is rather than a generic 확인.
  const [mode, setMode] = useState<"name" | "revoke" | null>(null);
  const [choice, setChoice] = useState<string>("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const candidates = demandTarget?.candidates ?? [];
  const mapping = demandTarget?.mapping ?? null;
  const missingTarget = (node.diagnosis?.missing_evidence ?? []).includes(NO_DEMAND_TARGET);

  // NOTHING TO CHOOSE FROM, NOTHING TO SHOW. A node whose rows do not resolve to
  // a link has no candidates at all (`ENTRANCE_NO_LINK_KEYED_ROWS` — the absence
  // `fixes.entrance_measurement` names), and a picker there would offer a
  // control that cannot be used. The next-action box above keeps the node's
  // real answer.
  //
  // A node whose gap has already been ANSWERED still shows this card, because
  // the standing mapping and its 철회 live here: a control that vanished the
  // moment it worked would leave a named assumption with no way to withdraw it.
  if (candidates.length === 0 || (!missingTarget && !mapping)) return null;

  const close = () => {
    setMode(null);
    setChoice("");
    setReason("");
    setError(null);
  };

  // Opening either act CLEARS the box. A reason typed for a naming must not be
  // able to arrive on a revocation (or the other way round) — they are two
  // different statements and only one of them is being made.
  const openForm = (next: "name" | "revoke") => {
    setMode((current) => (current === next ? null : next));
    setChoice("");
    setReason("");
    setError(null);
  };

  const submit = async () => {
    if (!mode || !reason.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "revoke") {
        if (!mapping) return;
        await revokeCalibrationDemandTarget(mapping.id, { reason });
      } else {
        const picked = candidates.find(
          (candidate) => `${candidate.vehicle_input_no}|${candidate.link_no}` === choice,
        );
        if (!picked) return;
        await postCalibrationDemandTarget({
          nodeId: node.node_id,
          vehicleInputNo: picked.vehicle_input_no,
          linkNo: picked.link_no,
          reason,
        });
      }
      close();
      onChanged?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = reason.trim().length > 0 && (mode === "revoke" || choice.length > 0);

  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-surface px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[14px] font-semibold text-slate-700">
          {t("수요 대상 차량 인풋", "The node's demand vehicle input", "需要対象の車両インプット")}
        </span>
        {mapping?.stale && (
          <span className="rounded-full border border-slate-300 bg-surface px-1.5 py-0.5 text-[14px] font-semibold text-slate-600">
            {t("재확인 필요", "Re-confirm", "再確認が必要")}
          </span>
        )}
        <span className="grow" />
        <button
          type="button"
          onClick={() => openForm("name")}
          className="ui-button-ghost inline-flex h-9 shrink-0 items-center px-3 text-[14px]"
        >
          {mapping
            ? t("다시 지정", "Name another", "指定し直す")
            : t("차량 인풋 지정", "Name the vehicle input", "車両インプットを指定")}
        </button>
        {mapping && (
          <button
            type="button"
            onClick={() => openForm("revoke")}
            className="ui-button-ghost inline-flex h-9 shrink-0 items-center px-3 text-[14px]"
          >
            {t("지정 철회", "Revoke", "指定を撤回")}
          </button>
        )}
      </div>

      {/* THE MAPPING THAT STANDS — who, when, and their own words for why. On
          the surface for the same reason the signature row's reason is: this
          record's entire worth is that somebody accountable stated one. */}
      {mapping && (
        <p className="mt-1 text-[14px] leading-6 text-slate-700">
          <span className="font-mono font-semibold">
            {t("인풋", "input", "インプット")} {mapping.vehicle_input_no} ·{" "}
            {t("링크", "link", "リンク")} {mapping.link_no}
          </span>
          <span className="block">
            <b>{mapping.named_by}</b> · {mapping.named_at}
          </span>
          <span className="block text-slate-600">{mapping.reason}</span>
        </p>
      )}

      {mode === "name" && (
        <div className="mt-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
          <p className="text-[14px] font-semibold text-slate-700">
            {t(
              "이 노드로 들어오는 차량 인풋 후보",
              "The vehicle inputs feeding this node's counted approaches",
              "このノードに流入する車両インプット候補",
            )}
          </p>
          {/* THE CANDIDATES, verbatim from the server. Input number, the link it
              feeds, and its CURRENT volume — a number a modeller recognises
              from Vissim, not a label this file made up. A volume the .inpx
              never stated prints as "미기재", never as 0: a zero here would read
              as a measurement. */}
          <ul className="mt-1 flex flex-col gap-1">
            {candidates.map((candidate) => {
              const key = `${candidate.vehicle_input_no}|${candidate.link_no}`;
              return (
                <li key={key}>
                  <label className="flex items-center gap-2 text-[14px] leading-6 text-slate-800">
                    <input
                      type="radio"
                      name={`demand-target-${node.node_id}`}
                      value={key}
                      checked={choice === key}
                      onChange={() => setChoice(key)}
                    />
                    <span className="font-mono font-semibold">
                      {t("인풋", "input", "インプット")} {candidate.vehicle_input_no}
                    </span>
                    <span className="font-mono text-slate-600">
                      {t("링크", "link", "リンク")} {candidate.link_no}
                    </span>
                    <span className="font-mono text-slate-600">
                      {typeof candidate.current_volume === "number"
                        ? `${fmt(candidate.current_volume, 0)} veh/h`
                        : t("교통량 미기재", "no volume stated", "交通量の記載なし")}
                    </span>
                    {candidate.name && <span className="text-slate-600">{candidate.name}</span>}
                  </label>
                </li>
              );
            })}
          </ul>
          <label
            className="mt-1.5 block text-[14px] font-semibold text-slate-700"
            htmlFor={`demand-reason-${node.node_id}`}
          >
            {t("지정 근거", "What you are asserting", "指定の根拠")}
          </label>
          <textarea
            id={`demand-reason-${node.node_id}`}
            value={reason}
            rows={3}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 bg-surface px-2 py-1.5 text-[14px] leading-6 text-slate-900"
          />
          {/* THE ACT, in one line, beside the control that performs it. Not the
              doctrine — why an attributed assumption is not a proven one is at
              /docs — but what THIS button records, which a reader about to put
              their name on something is entitled to know. */}
          <p className="mt-1 text-[14px] leading-6 text-slate-600">
            {t(
              "지정하면 '이 인풋이 이 노드 총량을 좌우한다'는 가정이 로그인한 사용자의 이름·시각과 함께 기록됩니다. 네트워크가 바뀌면 다시 묻습니다.",
              "Naming one records the assumption that this input drives the node's total, under the signed-in user's name and the server's clock. If the network changes, this asks again.",
              "指定すると「このインプットがノード総量を左右する」という仮定が、ログイン中の利用者名と時刻とともに記録されます。ネットワークが変わると再び確認します。",
            )}
          </p>
          <PickerActions
            busy={busy}
            canSubmit={canSubmit}
            error={error}
            onCancel={close}
            onSubmit={submit}
            submitLabel={t(
              "이 인풋을 지정합니다",
              "I name this input",
              "このインプットを指定します",
            )}
            hint={
              choice.length === 0
                ? t("인풋을 고르세요.", "Pick an input.", "インプットを選んでください。")
                : reason.trim().length === 0
                  ? t("이유를 적어야 합니다.", "A reason is required.", "理由の記入が必要です。")
                  : null
            }
          />
        </div>
      )}

      {mode === "revoke" && mapping && (
        <div className="mt-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
          <label
            className="block text-[14px] font-semibold text-slate-700"
            htmlFor={`demand-revoke-${node.node_id}`}
          >
            {t("철회 사유", "Why it no longer holds", "撤回の理由")}
          </label>
          <textarea
            id={`demand-revoke-${node.node_id}`}
            value={reason}
            rows={3}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 bg-surface px-2 py-1.5 text-[14px] leading-6 text-slate-900"
          />
          <p className="mt-1 text-[14px] leading-6 text-slate-600">
            {t(
              "철회해도 기록은 지워지지 않습니다 — 누가 언제 왜 철회했는지가 원래 지정 옆에 남고, 이 노드는 지정 전 판정으로 돌아갑니다.",
              "Revoking deletes nothing — who withdrew it, when, and why is kept beside the original naming, and this node falls back to the verdict it had before.",
              "撤回しても記録は消えません — 誰がいつなぜ撤回したかが元の指定の横に残り、このノードは指定前の判定に戻ります。",
            )}
          </p>
          <PickerActions
            busy={busy}
            canSubmit={canSubmit}
            error={error}
            onCancel={close}
            onSubmit={submit}
            submitLabel={t("이 지정을 철회합니다", "I withdraw this mapping", "この指定を撤回します")}
            hint={
              reason.trim().length === 0
                ? t("이유를 적어야 합니다.", "A reason is required.", "理由の記入が必要です。")
                : null
            }
          />
        </div>
      )}
    </div>
  );
}

/** The submit/cancel row both acts share.
 *
 * Shared rather than written twice because the DISABLED rule is the load-bearing
 * half: the server refuses an empty reason (and an unchosen input) by name, and
 * the button says so before the round trip rather than instead of it. Two copies
 * of that rule is how one of them would eventually stop matching the server. */
function PickerActions({
  busy,
  canSubmit,
  error,
  hint,
  onCancel,
  onSubmit,
  submitLabel,
}: {
  busy: boolean;
  canSubmit: boolean;
  error: string | null;
  hint: string | null;
  onCancel: () => void;
  onSubmit: () => void;
  submitLabel: string;
}) {
  const { t } = useLanguage();
  return (
    <>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {/* Navy fill, remapped sky ink, and a `border-sky-200` hairline --
            sky-200 and NOT 300, because globals.css's dark @theme remaps the
            sky border ramp only to 200 and a 300 here keeps Tailwind's stock
            #7dd3fc. Same rule the rail's NEXT chip and the search control
            follow, and the contract suite sweeps this file for it. */}
        <button
          type="button"
          onClick={onSubmit}
          disabled={busy || !canSubmit}
          className="inline-flex h-9 items-center rounded-md border border-sky-200 bg-sky-50 px-3 text-[14px] font-semibold text-sky-600 transition hover:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]"
        >
          {t("취소", "Cancel", "キャンセル")}
        </button>
        {hint && <span className="text-[14px] leading-6 text-slate-600">{hint}</span>}
      </div>
      {error && (
        <p className="mt-1.5 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[14px] leading-6 text-rose-700">
          {error}
        </p>
      )}
    </>
  );
}

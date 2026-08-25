"use client";

// ---------------------------------------------------------------------------
// Unit #57 — the locked #51 track's four NAMED reasons each get ONE action.
//
// User report, 2026-08-24: "#51 잠김 카드의 명명된 사유 4종이 산문뿐" — the gate
// already NAMES what is wrong (TABLE_5_6_NOT_APPLIED, SIGNAL_STEP_UNVERIFIED,
// LADDER_NOT_EXHAUSTED, HOLDOUT_NOT_AVAILABLE, plus ENTRANCE_NOT_MEASURED and
// the degenerate no-report codes) but a reader had to go find the door
// itself. These four have a door THIS screen can open directly; any other
// code renders no chip here at all — a reason this file does not recognise
// gets prose only, never a guessed action.
//
// WHY THIS IS ITS OWN FILE. `FlowRail.tsx`'s `ParameterSearchTrack` is pinned
// by the contract suite as containing NO `<button>` at all, in either branch
// (unit #51, "the start control is its own component so a button in the
// locked branch... cannot hide here") — exactly the same reason
// `ParameterSearchStart.tsx` exists as its own module rather than living
// inline. Three of these four actions are not plain links (a station switch,
// an assistant handoff, a node selection all run client code on click), so
// they need a real `<button>`, and the only place one may live is outside
// `ParameterSearchTrack`'s own pinned, buttonless body.
//
// EVERY ACTION REUSES AN EXISTING MECHANISM — #50-e's own rule ("no new
// mechanism, no invented route") applied a second time:
//
//   * SIGNAL_STEP_UNVERIFIED -> the SAME station-selection the rail's own
//     rows and the behaviour station's own "Table 5-6 표 보기 (⑫)" shortcut
//     already call (`onSelectStation`, app/calibration/page.tsx's
//     `handleSelectStation`). Falls back to a plain `Link` to
//     `/genesis/phases` when no station-selection callback is wired in —
//     this component is exported and could in principle be rendered
//     somewhere that has not threaded one through, and a station switch
//     nobody can honour is worse than no chip.
//   * TABLE_5_6_NOT_APPLIED -> the SAME window-event handoff NodeScoreTable,
//     app/health/page.tsx, CurationBar.tsx and app/calibration/page.tsx's ⑪
//     CTA already dispatch to reach the globally-mounted FloatingAssistant.
//     The roadmap row itself names this choice over station navigation
//     ("Table 5-6 처방 핸드오프(기존 이벤트 재사용)"): a reader looking at
//     THIS card is already inside the ⑫/behaviour station where the
//     Table 5-6 evidence lives (app/calibration/page.tsx renders
//     `ParameterSearchTrack` directly under `BehaviourStation`) — there is
//     nowhere closer on this screen to send them, so the action is asking
//     the assistant to act, not moving the reader again.
//   * LADDER_NOT_EXHAUSTED -> the SAME `onNodeClick`-shaped contract
//     NodeScoreTable and BaseValidationMap already speak, aimed at the FIRST
//     node `detail.blocking_nodes` names — never a node this file picked.
//   * HOLDOUT_NOT_AVAILABLE -> a plain `Link` to `/observations`, where the
//     실측 관측 work already lives.
//
// EVERY SENTENCE STAYS THE SERVER'S. A chip adds one short label (`t()`'d,
// this file's own words, same as every other control on this screen) and,
// where a prompt has to be built for the handoff, splices the server's OWN
// node ids and citation into a template — it never states a new fact about
// the model. A reason absent from `track.refusals` renders no chip (the
// caller only invokes this component per refusal that IS present), and a
// reason present but missing the one piece of `detail` its chip needs (an
// empty `blocking_nodes`, say, or no `nodes` list at all) renders null here:
// a button that cannot do what it says is worse than no button, the same
// rule `ParameterSearchStart.tsx`'s own header comment states about the
// search's start control.
// ---------------------------------------------------------------------------

import Link from "next/link";

import type { CalibrationRailTrack } from "@/lib/api";
import type { CalibrationStationId } from "@/lib/calibrationStations";

type Translate = (ko: string, en: string, ja?: string) => string;
type Refusal = CalibrationRailTrack["refusals"][number];

const TABLE_5_6_NOT_APPLIED = "TABLE_5_6_NOT_APPLIED";
const SIGNAL_STEP_UNVERIFIED = "SIGNAL_STEP_UNVERIFIED";
const LADDER_NOT_EXHAUSTED = "LADDER_NOT_EXHAUSTED";
const HOLDOUT_NOT_AVAILABLE = "HOLDOUT_NOT_AVAILABLE";

// The SAME window-event handoff app/health/page.tsx, CurationBar.tsx,
// NodeScoreTable.tsx and app/calibration/page.tsx's ⑪ CTA already use to
// reach the globally-mounted FloatingAssistant (components/AppShell.tsx).
// Declared independently rather than imported -- every one of those files
// makes the same choice for the same reason, recorded in their own comments:
// the event NAME is the contract between independent dispatchers, and unit
// #50-e's cross-file test pins every dispatcher against FloatingAssistant's
// own listener string, so a drift here fails loudly rather than silently.
const ASSISTANT_HANDOFF_EVENT = "vissim:assistant-handoff";

function requestAgentHandoff(prompt: string) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_HANDOFF_EVENT, { detail: { prompt } }));
}

/** The subset of `detail` this file ever reads back out as a string list --
 * never trusting the wire's shape blindly, and never inventing an entry the
 * server did not send. */
function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** The TABLE_5_6_NOT_APPLIED handoff prompt: the gate's OWN pending-node list
 * and citation, spliced into a request the assistant can act on. Never a
 * diagnosis this file composes -- `nodeIds` and `citation` are
 * `refusal.detail`'s own fields, read verbatim off the wire. */
function buildTable56PrescriptionPrompt(nodeIds: string[], citation: string, t: Translate): string {
  const nodes = nodeIds.join(", ");
  const cite = citation ? ` (${citation})` : "";
  return t(
    `Table 5-6 처방이 아직 적용되지 않은 노드: ${nodes}. 정지선 포화교통류율을 측정하고 Table 5-6 세트(ax/bx_add/bx_mult)${cite}를 적용해줘.`,
    `These nodes still need the Table 5-6 prescription applied: ${nodes}. Please measure the stop-line saturation flow and apply the Table 5-6 set (ax/bx_add/bx_mult)${cite}.`,
    `Table 5-6の処方がまだ適用されていないノード: ${nodes}。停止線飽和交通流率を測定しTable 5-6セット(ax/bx_add/bx_mult)${cite}を適用してください。`,
  );
}

const CHIP_CLASS =
  "mt-1 inline-flex h-8 w-fit items-center gap-1 rounded-full border border-sky-200 bg-sky-50 px-2.5 text-[14px] font-semibold text-sky-600 transition hover:border-sky-400";

/** One refusal's action chip, or nothing.
 *
 * Switches on the gate's own `code` -- never a guess parsed out of `why`'s
 * prose, which a rewording or a translation could drift out from under a
 * string match. A code this component does not recognise renders null: the
 * reason still prints in the caller's own `<li>`, just with no button. */
export default function ParameterSearchRefusalAction({
  refusal,
  t,
  onSelectStation,
  onSelectNode,
}: {
  refusal: Refusal;
  t: Translate;
  /** Switches the workspace to the named station -- `app/calibration/page.tsx`'s
   * `handleSelectStation`, the SAME function the rail's own rows and the
   * behaviour station's "Table 5-6 표 보기 (⑫)" shortcut already call. */
  onSelectStation?: (station: CalibrationStationId) => void;
  /** Selects a node and opens its diagnosis card -- the SAME `onNodeClick`
   * contract NodeScoreTable and BaseValidationMap already speak. */
  onSelectNode?: (nodeId: string) => void;
}) {
  const detail = refusal.detail ?? {};

  if (refusal.code === SIGNAL_STEP_UNVERIFIED) {
    if (onSelectStation) {
      return (
        <button type="button" onClick={() => onSelectStation("signals")} className={CHIP_CLASS}>
          {t("⑥ 신호 단계로 이동", "Go to step ⑥ (signals)", "⑥信号段階へ移動")}
          <span aria-hidden="true">→</span>
        </button>
      );
    }
    return (
      <Link href="/genesis/phases" className={CHIP_CLASS}>
        {t("현시도 검토 열기", "Open the 현시도 review", "現示図レビューを開く")}
        <span aria-hidden="true">→</span>
      </Link>
    );
  }

  if (refusal.code === TABLE_5_6_NOT_APPLIED) {
    const nodes = stringList(detail.nodes);
    if (nodes.length === 0) return null;
    const citation = typeof detail.citation === "string" ? detail.citation : "";
    return (
      <button
        type="button"
        onClick={() => requestAgentHandoff(buildTable56PrescriptionPrompt(nodes, citation, t))}
        aria-label={t(
          `Table 5-6 처방 요청 (노드 ${nodes.length}개)`,
          `Ask for the Table 5-6 prescription (${nodes.length} node(s))`,
          `Table 5-6処方を依頼 (ノード${nodes.length}件)`,
        )}
        className={CHIP_CLASS}
      >
        {t("AI에게 Table 5-6 처방 요청", "Ask the AI for the Table 5-6 prescription", "AIにTable 5-6処方を依頼")}
        <span aria-hidden="true">→</span>
      </button>
    );
  }

  if (refusal.code === LADDER_NOT_EXHAUSTED) {
    const blockingNodes = stringList(detail.blocking_nodes);
    const firstBlockingNode = blockingNodes[0];
    if (!firstBlockingNode || !onSelectNode) return null;
    return (
      <button type="button" onClick={() => onSelectNode(firstBlockingNode)} className={CHIP_CLASS}>
        {t(
          `노드 ${firstBlockingNode} 진단 보기`,
          `See node ${firstBlockingNode}'s diagnosis`,
          `ノード${firstBlockingNode}の診断を見る`,
        )}
        <span aria-hidden="true">→</span>
      </button>
    );
  }

  if (refusal.code === HOLDOUT_NOT_AVAILABLE) {
    return (
      <Link href="/observations" className={CHIP_CLASS}>
        {t("실측 관측 페이지로", "Go to observations", "実測観測ページへ")}
        <span aria-hidden="true">→</span>
      </Link>
    );
  }

  return null;
}

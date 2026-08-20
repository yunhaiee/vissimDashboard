"use client";

// ---------------------------------------------------------------------------
// Unit #47's approval + diagnosis cards, extracted (unit #50).
//
// Every component here was written for the assistant drawer and lived inside
// FloatingAssistant.tsx until the calibration workspace needed the SAME cards
// on a full page. It was lifted VERBATIM rather than reimplemented: a second
// copy of an approval card is a second place for the sentence a human consents
// to to drift, and that sentence is the product.
//
// So this module is a MOVE, not a rewrite. The JSX, the class names, the
// comments explaining why each colour and each fold is what it is, and the
// backend-quoting rules ("the only prose this component writes is labels") are
// unchanged; only the export keywords are new. dashboard-contract.test.ts pins
// the render against this file so the move cannot quietly become a redesign.
//
// What still lives in FloatingAssistant.tsx: the drawer itself, the streaming,
// and `runAgentApproval` -- the handler that POSTs to /api/assistant/agent/
// approve. This module renders and calls back; it owns no fetch of its own
// except the vehicle-input baseline lookup that arrived with the card.
// ---------------------------------------------------------------------------

import { useEffect, useState } from "react";
import Link from "next/link";

import ApprovalFocusMap from "@/components/ApprovalFocusMap";
import { useLanguage } from "@/components/LanguageProvider";
import { ok, resolve, type Loaded } from "@/lib/asyncResource";
import {
  fetchNetworkSearch,
  type AgentAction,
  type AgentApprovalFocus,
  type AgentCarFollowingSet,
  type AgentDiagnosis,
  type AgentDiagnosisNode,
  type AgentSearchedSearch,
  type AgentStep,
  type AgentWarning,
} from "@/lib/api";

/** Local alias, this repo's convention for the translate function rather than
 * a shared export (AppShell.tsx, FloatingAssistant.tsx and app/health/page.tsx
 * each declare their own). */
type Translate = (ko: string, en: string, ja?: string) => string;


// M2.3: lifecycle of a write-approval card attached to an agent-mode
// message. "pending" -> user has not acted yet (Run/Cancel both enabled);
// "approving"/"declining" -> the /agent/approve call is streaming (buttons
// disabled, busy label); "approved"/"declined" -> resolved, single-use,
// buttons replaced by a small label; "error" -> the resolve call failed
// (stale/consumed approval, or a network error) and is also terminal, since
// a consumed approval_id would just 404 again on retry.
export type AgentApprovalResolution = "pending" | "approving" | "declining" | "approved" | "declined" | "error";

export type AgentApprovalState = {
  approvalId: string;
  sessionId: string;
  actions: AgentAction[];
  warnings: AgentWarning[];
  assumptions: string[];
  resolution: AgentApprovalResolution;
  errorMessage?: string;
  /** U9: set only when the resolve call came back 401 -- the card then offers
   * a way to sign in instead of leaving a dead error. Absent for every other
   * failure, including 403: a signed-in user with the wrong role has nothing
   * to gain from a login link, and offering one would imply otherwise. */
  errorLoginHref?: string;
  /** Approval-card mini-map (v2 UX unit): the map-focus hint from the SAME
   * wire event actions/warnings/assumptions come from -- see
   * AgentApprovalFocus's own doc comment in lib/api.ts. Undefined only for
   * an approval reconciled from a pre-this-unit event shape; ApprovalFocusMap
   * already treats a missing/null bbox as "render nothing", so no separate
   * guard is needed here. */
  focus?: AgentApprovalFocus;
  /** Unit #47: the MRWA Table 5-6 car-following SET this pause is one member
   * of, from the same wire event -- see AgentCarFollowingSet in lib/api.ts.
   * Undefined for every other approval, which is every approval this app had
   * before this unit; CarFollowingSetPanel renders nothing then. */
  carFollowingSet?: AgentCarFollowingSet;
  /** Unit #51-c: the finished, held-out parameter SEARCH this pause is one
   * write of -- see AgentSearchedSearch in lib/api.ts. The other grant species,
   * carried the same way and rendered by SearchedParameterPanel. */
  searchedSearch?: AgentSearchedSearch;
};

// M2.3: builds/reconciles agentApproval state from either wire source --
// the early approval_request stream event (no session_id: the backend
// mints it before that event, see AgentStreamEvent's "approval_request"
// variant) or the terminal done.response.approval (session_id always
// known). `resolution` always starts "pending" here; only
// runAgentApproval below ever advances it.
export function approvalStateFromEvent(
  event: {
    approval_id?: string;
    actions?: AgentAction[];
    warnings?: AgentWarning[];
    assumptions?: string[];
    focus?: AgentApprovalFocus;
    car_following_set?: AgentCarFollowingSet;
    searched_search?: AgentSearchedSearch;
  },
  sessionId: string,
): AgentApprovalState {
  return {
    approvalId: event.approval_id ?? "",
    sessionId,
    actions: event.actions ?? [],
    warnings: event.warnings ?? [],
    assumptions: event.assumptions ?? [],
    resolution: "pending",
    focus: event.focus,
    carFollowingSet: event.car_following_set,
    searchedSearch: event.searched_search,
  };
}

// ---------------------------------------------------------------------------
// Unit #47 (a): unit #43's diagnosis ladder, on a surface a user can see.
//
// The ladder shipped 2026-08-18 and reached nobody: the reality-check report
// never survived onto the HTTP wire (orchestrator.py's step payload was
// {summary, tool, status}), so a node's rung, the reason it landed there and
// the MRWA step authorising its next action existed only in the trace, the
// audit ledger and whatever the answer prose happened to mention. #47 grafts
// `diagnosis` onto the run_reality_check step; this renders it.
//
// ONE LINE PER NODE, PLUS A FOLD. The line is the verdict (rung + node + GEH);
// everything that justifies it goes behind a native <details>, the same
// disclosure convention app/genesis/jobs/[id]/page.tsx settled on for #41's
// doctrine text -- it needs no state, survives a re-render with its answer
// intact, and is not a <button>, which matters because a diagnosis is
// information, not a control.
// ---------------------------------------------------------------------------

// The five rungs geh_ladder.py can return, in the descent order it tries them,
// as human words. Codes are the backend's identity for these and are shown too
// (folded, mono) -- the label is what a modeller reads, the code is what they
// quote in a bug report.
export const AGENT_RUNG_LABELS: Record<string, [string, string, string]> = {
  DEMAND_LEVEL: ["수요 수준", "Demand level", "需要水準"],
  ROUTING_SPLIT: ["회전 배분", "Turn distribution", "回転配分"],
  CAPACITY_LIMIT: ["용량 한계", "Capacity limit", "容量限界"],
  SIGNAL_TIMING: ["신호 시간", "Signal timing", "信号時間"],
  UNCLASSIFIABLE: ["분류 불가", "Unclassifiable", "分類不可"],
};

export function rungLabel(rung: string, t: Translate): string {
  const entry = AGENT_RUNG_LABELS[rung];
  return entry ? t(entry[0], entry[1], entry[2]) : rung;
}

/** The tri-lingual blocks the backend already wrote. The client picks; it
 * never composes one of these sentences itself. */
export function pickText(
  text: { ko: string; en: string; ja: string } | undefined,
  lang: string,
): string {
  if (!text) return "";
  if (lang === "ko") return text.ko;
  if (lang === "ja") return text.ja;
  return text.en;
}

export function AgentDiagnosisLadder({ steps }: { steps?: AgentStep[] }) {
  const { t, lang } = useLanguage();
  // The LAST reality check of the turn: a turn may run more than one (before
  // and after an edit), and the later one is the current picture.
  const diagnosis: AgentDiagnosis | undefined = (steps ?? [])
    .filter((step) => step.tool === "run_reality_check" && step.diagnosis)
    .map((step) => step.diagnosis!)
    .pop();
  if (!diagnosis || diagnosis.nodes.length === 0) return null;

  return (
    <div className="mb-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2">
      <p className="text-[13px] font-bold uppercase tracking-[0.08em] text-slate-500">
        {t("노드별 진단", "Per-node diagnosis", "ノード別診断")}
        <span className="ml-1.5 font-mono text-[13px] font-normal normal-case tracking-normal text-slate-400">
          {diagnosis.truncated
            ? t(
                `${diagnosis.nodes.length} / ${diagnosis.total}`,
                `${diagnosis.nodes.length} of ${diagnosis.total}`,
                `${diagnosis.nodes.length} / ${diagnosis.total}`,
              )
            : `${diagnosis.total}`}
        </span>
      </p>
      <div className="mt-1.5 space-y-1.5">
        {diagnosis.nodes.map((node) => (
          <AgentDiagnosisRow key={node.node_id} node={node} lang={lang} t={t} />
        ))}
      </div>
    </div>
  );
}

export function AgentDiagnosisRow({
  node,
  lang,
  t,
}: {
  node: AgentDiagnosisNode;
  lang: string;
  t: Translate;
}) {
  // THREE-COLOUR RULE (U-1, dashboard-contract.test.ts's "three accent
  // colours" test): a node carrying a diagnosis is a node whose modelled
  // volume MISSED its counted one, which is this app's rose "fail" state.
  // UNCLASSIFIABLE is the one rung that is not a verdict about the network --
  // it says the evidence did not reach one -- so it renders on the neutral ink
  // ramp with a heavier hairline, the app-wide WARN treatment, never a fourth
  // hue.
  const unclassified = node.rung === "UNCLASSIFIABLE";
  const chipClass = unclassified
    ? "border-slate-400 bg-slate-100 text-slate-800"
    : "border-rose-200 bg-rose-50 text-rose-700";
  // The next action is the way FORWARD -- sky, this app's "selection /
  // proposal" accent.
  const nextAction = pickText(node.next_action?.text, lang);
  // Unit #44's hand-off, made to follow naturally: a capacity-limited node is
  // exactly the one MRWA §5.2.5.1 answers, and the treatment needs a measured
  // stop-line saturation flow before it can propose anything. The ladder's own
  // next_action text already asks for the measurement (step ⑦); this adds only
  // what happens once it arrives, which is a fact about THIS app's tooling and
  // therefore the app's own sentence to write.
  const capacityHandoff =
    node.rung === "CAPACITY_LIMIT"
      ? t(
          "측정값을 주면 MRWA Table 5-6에서 차량추종 3개 값(ax·bx_add·bx_mult)을 한 세트로 제안합니다.",
          "Supply that measurement and the three Table 5-6 car-following values (ax, bx_add, bx_mult) are proposed as one set.",
          "測定値をいただければ、MRWA Table 5-6 から追従の3値（ax・bx_add・bx_mult）を一組として提案します。",
        )
      : null;

  return (
    <div className="rounded-md border border-slate-200 bg-surface px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={`rounded-full border px-1.5 py-0.5 text-[13px] font-bold uppercase tracking-[0.04em] ${chipClass}`}
        >
          {rungLabel(node.rung, t)}
        </span>
        <span className="font-mono text-[13px] text-slate-600">
          {t("노드", "Node", "ノード")} {node.node_id}
        </span>
        {typeof node.node_total_geh === "number" && (
          <span className="font-mono text-[13px] text-slate-500">
            GEH {node.node_total_geh.toFixed(1)}
            {typeof node.geh_threshold === "number" ? ` / ${node.geh_threshold}` : ""}
          </span>
        )}
      </div>
      {nextAction && (
        <p className="mt-1 rounded-md border border-sky-200 bg-sky-50 px-2 py-1 text-[13px] leading-5 text-slate-800">
          {nextAction}
          {capacityHandoff && <span className="block mt-0.5 text-slate-600">{capacityHandoff}</span>}
        </p>
      )}
      <details className="mt-1 text-[13px] leading-5 text-slate-600">
        <summary className="cursor-pointer list-none text-[13px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (근거·코드)", "More (reasoning, codes)", "詳細（根拠・コード）")}
        </summary>
        <div className="mt-1.5 flex flex-col gap-1.5 border-l border-slate-300 pl-3">
          <p>{pickText(node.why, lang)}</p>
          {(typeof node.observed_total === "number" || typeof node.modelled_total === "number") && (
            <p className="font-mono text-[13px] text-slate-500">
              {t("실측", "observed", "実測")} {node.observed_total ?? "-"} ·{" "}
              {t("모델", "modelled", "モデル")} {node.modelled_total ?? "-"}
            </p>
          )}
          {node.next_action?.citation && (
            <p className="font-mono text-[13px] text-slate-500">{node.next_action.citation}</p>
          )}
          {node.missing_evidence.length > 0 && (
            <p className="font-mono text-[13px] text-slate-500">
              {t("빠진 근거", "missing evidence", "不足している根拠")}: {node.missing_evidence.join(", ")}
            </p>
          )}
          <p className="font-mono text-[13px] text-slate-400">
            {node.rung}
            {node.next_action?.code ? ` · ${node.next_action.code}` : ""}
          </p>
        </div>
      </details>
    </div>
  );
}

// M4 roadmap bullet ("per-action preflight status chips (pass/warn/refuse)"):
// 통과 (pass, emerald) when this action's approval carries zero preflight
// warnings, 주의 N (warn) with the fired count otherwise. THREE-COLOR RULE
// (U-2 Console pass, 2026-08-14): warn is mono, not amber -- the app settled
// this app-wide (see dashboard-contract.test.ts's "U-1: three accent
// colours" test, 2026-08-13): rose/emerald/sky are the only three accents,
// and a warn tier renders on the neutral ink ramp with a heavier hairline
// instead of a fourth hue, exactly like observations.tsx's GateVerdictChip
// WARN chip. No "refuse" tone exists here BY DESIGN, not omission: verified against
// backend/services/agent/orchestrator.py -- a preflight report with any
// BLOCKING finding returns a "refusal" turn (kind: "answer", a structured
// refusal envelope) BEFORE `pending_approval` is ever constructed (see the
// `if preflight_report.blocking:` branch, which short-circuits ahead of the
// `pending_approval = {...}` assignment a few lines below it). A refused
// action can therefore never reach an approval_request event or this card
// at all -- only WARN-tier findings (or none) ever ride along here.
//
// `warningCount` is `approval.warnings.length`, not a per-action slice:
// backend/services/agent/orchestrator.py's `pending_approval` is always
// built from a SINGLE proposed action (`action_entry`/`pending_approval`
// there are singular objects, never a list) and `warnings` is that one
// action's own preflight findings -- so every action row getting the same
// count is exact for the only shape the backend emits today, not an
// approximation. If a future milestone teaches the backend to propose
// multiple actions in one approval, this chip would need a real per-action
// warning mapping from the wire contract; tracked here so that gap is not
// silently reintroduced.
export function PreflightStatusChip({ warningCount }: { warningCount: number }) {
  const { t } = useLanguage();
  if (warningCount <= 0) {
    return (
      <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-0.5 text-[13px] font-semibold text-emerald-700 ring-1 ring-emerald-200">
        {t("통과", "Pass", "通過")}
      </span>
    );
  }
  return (
    <span className="shrink-0 rounded-full border border-slate-400 bg-slate-100 px-2 py-0.5 text-[13px] font-semibold text-slate-800">
      {t(`주의 ${warningCount}`, `Warn ${warningCount}`, `注意 ${warningCount}`)}
    </span>
  );
}

// --- Vehicle-input approval target (sibling-disambiguation unit, 2026-07-28) ---
//
// The defect, verbatim: "i prompted 107, but it is trying to fix input for
// 307, not 107 (i dont even know if 107 exists.)" The write targeted 107 and
// was correct; the card said only "차량 입력 107 → 6000 veh/h", which is
// unverifiable when the network map draws 107, 207 and 307 on ONE pixel under
// ONE shared name. This block makes the card answer, without the user opening
// the map at all: which object (id + name + VEHICLE CLASS, the only field that
// separates the three), what it is today, and what else is hiding under it.
//
// `volume` is NOT on the approval wire payload, and the value the write will
// replace lives in the agent session's working copy -- which no endpoint
// exposes numerically. The one place a vehicle input's volume is readable from
// the client is GET /api/network/search (backend/routers/search.py), and that
// endpoint reads the ACTIVE dataset, explicitly never a session working copy.
// So the "before" value here is the BASE model's, which is exact right up
// until this conversation persists its first write and then silently is not.
// Hence `baselineTrusted` below: the value is shown only while base == working
// by construction, and its absence is explained rather than left blank. A card
// that showed "127.3 → 5000" for an input this session had already moved to
// 3000 would be a wrong number on an approval screen, which is the one thing
// this product must never do.
type VehicleInputBaseline = { volume: number | null; link: string | null };

// Module-level, promise-valued, same pattern (and same reasoning) as
// ApprovalFocusMap's network cache: repeated renders of the same card -- and a
// re-proposal of the same input later in the conversation -- never refetch.
// Failed lookups are evicted so one transient error doesn't wedge every later
// card into "no current value".
const vehicleInputBaselineCache = new Map<string, Promise<VehicleInputBaseline | null>>();

function fetchVehicleInputBaseline(inputNo: string): Promise<VehicleInputBaseline | null> {
  const cached = vehicleInputBaselineCache.get(inputNo);
  if (cached) return cached;
  const promise = fetchNetworkSearch(inputNo, { limit: 8 }).then((response) => {
    // Id-exact, family-exact: a query like "107" can also match a link name,
    // and only the vehicle input with THIS id is the object being written.
    const hit = response.results.find((result) => result.type === "vehicle_input" && String(result.id) === inputNo);
    if (!hit) return null;
    const detail = (hit.label?.detail ?? {}) as Record<string, unknown>;
    const volume = typeof detail.volume === "number" && Number.isFinite(detail.volume) ? detail.volume : null;
    return { volume, link: detail.link == null ? null : String(detail.link) };
  });
  vehicleInputBaselineCache.set(inputNo, promise);
  promise.catch(() => {
    vehicleInputBaselineCache.delete(inputNo);
  });
  return promise;
}

function useVehicleInputBaseline(inputNo: string | null): VehicleInputBaseline | null {
  // Keyed by the vehicle input this answer describes (lib/asyncResource.ts).
  // The clearing that used to happen in the effect now happens during the SAME
  // render that changes `inputNo`, which matters more here than anywhere else
  // in the app: for one frame the old shape showed the PREVIOUS input's current
  // volume beside the new input's id, and a wrong number on an approval card is
  // the one thing this product must never render.
  const [loaded, setLoaded] = useState<Loaded<VehicleInputBaseline> | null>(null);
  const { data: baseline } = resolve(loaded, inputNo ?? "", { idle: !inputNo });
  useEffect(() => {
    if (!inputNo) return;
    let cancelled = false;
    fetchVehicleInputBaseline(inputNo)
      .then((value) => {
        if (!cancelled) setLoaded(ok(inputNo, value));
      })
      .catch(() => {
        // No current value is a fine outcome -- the arrow line still renders
        // the requested volume. Never an error blob on an approval card.
      });
    return () => {
      cancelled = true;
    };
  }, [inputNo]);
  return baseline;
}

/** Trailing zeros are noise on a volume ("127.30 → 6000.00"); more than two
 * decimals is float exhaust, not data. */
function formatVolume(value: number): string {
  return String(Math.round(value * 100) / 100);
}

function VehicleInputActionDetail({
  action,
  focus,
  modelEdited,
}: {
  action: AgentAction;
  focus: AgentApprovalFocus | undefined;
  modelEdited: boolean;
}) {
  const { t } = useLanguage();
  const params = action.params ?? {};
  const inputNo = params.input_no == null ? "" : String(params.input_no);
  const rawInterval = Number(params.interval_no ?? 1);
  const intervalNo = Number.isFinite(rawInterval) ? rawInterval : 1;
  // The searchable volume is interval 1's (network_search's
  // `_detail_vehicle_input` reads the shadow model's scalar `volume`), so a
  // write aimed at any other interval has no honest "before" to show -- and
  // the interval itself becomes load-bearing on the card, since input 107 has
  // six of them and every one of them renders identically today.
  const firstInterval = intervalNo === 1;

  // Captured ONCE, when this card mounts -- i.e. the moment the approval
  // arrived, before the user could have approved anything. Reading the live
  // prop instead would make the value vanish from the card the instant the
  // user pressed 실행 (that turn sets wroteModel), which reads as the card
  // retracting the number it just justified itself with.
  const [baselineTrusted] = useState(() => !modelEdited);
  const wantsBaseline = baselineTrusted && firstInterval && inputNo !== "";
  const baseline = useVehicleInputBaseline(wantsBaseline ? inputNo : null);

  const element = focus?.elements.find((el) => el.type === "vehicle_input" && el.id === inputNo);
  const hostLinkId = focus?.elements.find((el) => el.type === "link")?.id ?? null;
  // Cross-check before believing the number: the search hit comes from the
  // active dataset while the focus comes from this session's working copy. If
  // they disagree about which link the input sits on, they are not describing
  // the same object (a dataset swapped mid-conversation, say) -- show nothing.
  const baselineUsable =
    baseline != null && (baseline.link == null || hostLinkId == null || baseline.link === hostLinkId);
  const currentVolume = baselineUsable ? baseline?.volume ?? null : null;

  // "차종 조성" is the vocabulary the backend's own warnings on this same card
  // use for a vehicle composition, so the chip and the mono warning strips
  // below it name the same object the same way. The varying case is already a full
  // sentence and takes no prefix -- prefixing it would read "차종 조성 차종
  // 조성 구간별 상이", and the en prefix would stutter against every real
  // composition name on this model ("Composition Class1" is fine, "Class
  // Class1" is not).
  const compositionChip = element?.composition
    ? t(`차종 조성 ${element.composition}`, `Composition ${element.composition}`, `車種構成 ${element.composition}`)
    : element?.composition_varies_by_interval
      ? t("차종 조성 구간별 상이", "Class mix varies by interval", "車種構成が時間帯別に異なる")
      : "";
  const stackIds = [inputNo, ...(element?.siblings ?? [])]
    .filter((id) => id !== "")
    .sort((a, b) => (Number(a) - Number(b) || a.localeCompare(b)));

  const hiddenBaselineNote = !wantsBaseline
    ? !firstInterval
      ? t(
          `현재값 미표시 · 시간 구간 ${intervalNo} 값은 조회할 수 없습니다`,
          `Current value hidden · interval ${intervalNo} value is not readable`,
          `現在値は非表示 · 期間${intervalNo}の値は取得できません`,
        )
      : t(
          "현재값 미표시 · 이 대화에서 모델이 이미 편집되었습니다",
          "Current value hidden · this conversation has already edited the model",
          "現在値は非表示 · この会話で既にモデルを編集しています",
        )
    : null;

  return (
    <div className="mt-1">
      <p className="text-[13px] leading-5 text-slate-600">
        <span className="font-semibold text-slate-800">
          {t(`차량 입력 ${inputNo}`, `Vehicle input ${inputNo}`, `車両入力 ${inputNo}`)}
        </span>
        {element?.label ? <span> · {element.label}</span> : null}
        {!firstInterval ? (
          <span> · {t(`시간 구간 ${intervalNo}`, `interval ${intervalNo}`, `期間 ${intervalNo}`)}</span>
        ) : null}
      </p>

      {compositionChip && (
        // Three-color rule: this names a vehicle-class attribute, not a
        // fail/pass/selection state -- mono, not blue.
        <span className="mt-1 inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[13px] font-semibold text-slate-600">
          {compositionChip}
        </span>
      )}

      <p className="mt-1 text-[13px] font-semibold text-slate-800">
        {currentVolume != null && <span className="font-normal text-slate-500">{formatVolume(currentVolume)} </span>}
        <span className="font-normal text-slate-500">→ </span>
        {`${params.volume} veh/h`}
      </p>

      {hiddenBaselineNote && <p className="mt-0.5 text-[13px] leading-4 text-slate-500">{hiddenBaselineNote}</p>}

      {stackIds.length > 1 && (
        <p className="mt-1 text-[13px] leading-4 text-slate-500">
          {t(
            `같은 지점에 차량 입력 ${stackIds.length}개(${stackIds.join(", ")}) · 이번 변경은 ${inputNo}에만 적용됩니다`,
            `${stackIds.length} vehicle inputs share this point (${stackIds.join(", ")}) · this change touches ${inputNo} only`,
            `同じ地点に車両入力${stackIds.length}件(${stackIds.join(", ")}) · 今回の変更は${inputNo}のみ`,
          )}
        </p>
      )}
    </div>
  );
}

/** One action row's "what exactly is being changed" line. Split out so the
 * vehicle-input case can own hooks (a baseline fetch) without every other tool
 * paying for them, and so the plain-string `agentActionDetail` below stays
 * exactly what it was for the other eleven verbs. */
export function AgentActionDetail({
  action,
  focus,
  modelEdited,
}: {
  action: AgentAction;
  focus: AgentApprovalFocus | undefined;
  modelEdited: boolean;
}) {
  const { t } = useLanguage();
  if (action.tool === "set_vehicle_input_volume") {
    return <VehicleInputActionDetail action={action} focus={focus} modelEdited={modelEdited} />;
  }
  // Mono for ids/codes: a tool with no case in agentActionDetail's switch
  // (below) falls back to a raw "key=value, key2=value2" dump instead of a
  // sentence -- that fallback is a CODE, same reasoning as the action-row
  // title's own fallback a few lines up in AgentApprovalCard, and
  // AGENT_ACTION_LABELS' remaining entries are exactly agentActionDetail's
  // known-sentence cases (set_vehicle_input_volume is handled above and
  // never reaches here), so the same membership check identifies it.
  return (
    <p className={`mt-0.5 text-[13px] text-slate-600 ${AGENT_ACTION_LABELS[action.tool] ? "" : "font-mono"}`}>
      {agentActionDetail(action, t)}
    </p>
  );
}

// U-2-core (D4's decision record, 2026-08-13): "the approval card does not
// display the PROPOSAL's own rationale ... to the person deciding". This is
// that rationale -- `action.summary`, the planner's own <=80-char step
// narration (planner.py's `PlannerDecision.summary`, a REQUIRED field on
// every tool-call schema — see planner.py's `_with_summary_param`). It was
// already on the wire (routers/assistant.py's `action_entry["summary"]`,
// unchanged by this unit) and already used to build the short answer text
// that appears ABOVE this card (`_approval_proposal_answer`) — it was simply
// never rendered ON the card itself, where the reviewer is actually looking
// when they decide. Rendered read-only and verbatim: never re-worded,
// re-summarized, or truncated further here.
//
// Named, not silently absent, per this unit's own instructions: unlike
// genesis's FixProposal (services/agent/fixes.py), which stores a tri-lingual
// `rationale_ko/en/ja`, a `confidence` label, and a populated `assumptions`
// tuple, THIS chat-driven write path's `PlannerDecision` carries only
// `summary` — no separate confidence signal exists for it at all, and
// `AgentApproval.assumptions` is wired on the wire shape but is hardcoded to
// `[]` server-side today (routers/assistant.py: "No structured 'assumption'
// signal exists yet from the planner"). So this card renders exactly what
// this proposal type produces (a rationale line here, and the assumptions
// block below whenever it stops being empty) and fabricates neither a
// confidence chip nor demand-estimate framing that no data here supports —
// both are genesis-only concepts (see FixFindingRow in app/genesis/page.tsx
// for where they legitimately apply).
export function AgentActionRationale({ summary }: { summary: string }) {
  const { t } = useLanguage();
  if (!summary.trim()) return null;
  return (
    <div className="mt-1.5 border-t border-slate-200 pt-1.5">
      <p className="text-[13px] font-bold uppercase tracking-[0.08em] text-slate-500">
        {t("제안 이유", "Why proposed", "提案理由")}
      </p>
      <p className="mt-0.5 text-[13px] leading-5 text-slate-700">{summary}</p>
    </div>
  );
}

// U-2-core: cap for the reviewer's optional approval/decline reason. MUST
// match backend/routers/assistant.py's `MAX_APPROVAL_REASON_CHARS` -- the
// pydantic field this posts into (`AgentApproveRequest.reason`) 422s past
// it server-side, and dashboard-contract.test.ts pins this literal against
// that backend constant so the two numbers cannot silently drift apart.
// Named once and reused by the input's `maxLength`, the defensive `.slice()`
// on every keystroke, and the live counter, rather than a bare `600`
// repeated three times (genesis's own reviewer-reason field at
// app/genesis/page.tsx does repeat the bare literal -- not duplicated here
// on purpose, since a mismatch between the two would be silent).
export const APPROVAL_REASON_MAX_CHARS = 600;

export function AgentApprovalCard({
  approval,
  modelEdited,
  onRun,
  onCancelApproval,
}: {
  approval: AgentApprovalState;
  /** True when THIS conversation has already persisted a write, i.e. the
   * session's working copy has diverged from the active dataset -- which is
   * what decides whether a base-model "current value" is still the truth. See
   * VehicleInputActionDetail's own comment. */
  modelEdited: boolean;
  /** U-2-core: both now take the reviewer's optional typed reason (trimmed,
   * `undefined` when blank) so it rides the SAME approve/decline POST this
   * card already sends -- see the reason input below and
   * AgentApproveRequest.reason in lib/api.ts. */
  onRun: (reason?: string) => void;
  onCancelApproval: (reason?: string) => void;
}) {
  const { t } = useLanguage();
  const busy = approval.resolution === "approving" || approval.resolution === "declining";
  const actionable = approval.resolution === "pending" || busy;
  const runDisabled = busy || !approval.sessionId;
  // U-2-core: local to this card, not part of AgentApprovalState -- it is
  // never read anywhere else, and the card unmounts once resolved (replaced
  // by the plain "승인됨"/"취소됨" label below), so there is nothing to
  // persist it into. `.slice()` on every keystroke is a second, input-
  // independent enforcement of the cap alongside the <input>'s own
  // `maxLength` -- belt-and-suspenders against the exact failure named in
  // this unit's own review brief: a reason that silently exceeds the cap and
  // 422s. Both must agree with the backend's own cap (routers/assistant.py's
  // MAX_APPROVAL_REASON_CHARS) -- see APPROVAL_REASON_MAX_CHARS below.
  const [reason, setReason] = useState("");
  const reasonNearCap = reason.length > APPROVAL_REASON_MAX_CHARS - 60;

  return (
    <div className="mt-2 rounded-xl border border-slate-200 bg-surface px-3 py-2.5">
      <p className="text-[14px] font-bold text-slate-800">
        {t("실행 전 확인이 필요합니다", "Approval needed before executing", "実行前に確認が必要です")}
      </p>

      <div className="mt-2 space-y-1.5">
        {approval.actions.map((action, index) => (
          <div key={`${action.tool}-${index}`} className="rounded-lg bg-slate-50 px-2.5 py-2">
            <div className="flex items-start justify-between gap-2">
              {/* Mono for ids/codes (U-2 Console pass): a tool this table has
                  no human label for falls back to printing its raw wire
                  identifier (agentActionLabel's own fallback below) -- that
                  is a CODE, not a sentence, so it renders in the app's
                  monospace convention like every other id/code, instead of
                  silently borrowing the translated label's prose weight. */}
              <p
                className={`text-[13px] font-semibold text-slate-700 ${AGENT_ACTION_LABELS[action.tool] ? "" : "font-mono"}`}
              >
                {agentActionLabel(action.tool, t)}
              </p>
              <PreflightStatusChip warningCount={approval.warnings.length} />
            </div>
            <AgentActionDetail action={action} focus={approval.focus} modelEdited={modelEdited} />
            <AgentActionRationale summary={action.summary} />
          </div>
        ))}
      </div>

      {approval.carFollowingSet && (
        <CarFollowingSetPanel set={approval.carFollowingSet} action={approval.actions[0]} />
      )}

      {approval.searchedSearch && (
        <SearchedParameterPanel search={approval.searchedSearch} action={approval.actions[0]} />
      )}

      {approval.focus?.bbox && (
        <ApprovalFocusMap focus={approval.focus} sessionId={approval.sessionId} action={approval.actions[0]} />
      )}

      {approval.warnings.length > 0 && (
        // Three-color rule (U-2 Console pass): a preflight WARN finding is
        // mono with a heavier hairline, same as PreflightStatusChip's own
        // "주의" chip a few lines up and the app-wide WARN treatment
        // (dashboard-contract.test.ts's "U-1: three accent colours" test) --
        // never a fourth, amber hue.
        <div className="mt-2 space-y-1">
          {approval.warnings.map((warning, index) => (
            <p
              key={`${warning.constraint_id || warning.message}-${index}`}
              className="rounded-md border border-slate-400 bg-slate-100 px-2 py-1.5 text-[13px] leading-5 text-slate-800"
            >
              {warning.message}
            </p>
          ))}
        </div>
      )}

      {approval.assumptions.length > 0 && (
        <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
          <p className="text-[13px] font-bold uppercase tracking-[0.08em] text-slate-500">{t("가정", "Assumptions", "前提")}</p>
          <ul className="mt-1 space-y-0.5">
            {approval.assumptions.map((assumption, index) => (
              <li key={index} className="text-[13px] leading-5 text-slate-600">
                {assumption}
              </li>
            ))}
          </ul>
        </div>
      )}

      {actionable ? (
        <div className="mt-2.5 flex flex-col gap-2">
          {/* U-2-core (D4's decision record, 2026-08-13): the ONE named gap
              this unit exists to close -- "the production approval UI sends
              no reason at all". Optional, wired verbatim into the SAME
              approve/decline POST's existing `reason` field for both Run
              and Cancel (backend/routers/assistant.py records it into the
              ledger regardless of outcome), so a real UI decision can carry
              the audit DOCX's 근거 cell instead of leaving it "-". */}
          <label className="flex flex-col gap-1 text-[13px] text-slate-600">
            <span className="flex items-center justify-between gap-2">
              <span>{t("사유 (선택)", "Reason (optional)", "理由（任意）")}</span>
              {/* Three-color rule (U-2 Console pass): "nearing the cap" is
                  not a fail/pass/selection state, so it is told apart by
                  WEIGHT (bold ink), never a fourth hue -- see PreflightStatusChip's
                  own comment for the app-wide rule this follows. */}
              <span className={`font-mono text-[13px] ${reasonNearCap ? "font-semibold text-slate-800" : "text-slate-400"}`}>
                {reason.length}/{APPROVAL_REASON_MAX_CHARS}
              </span>
            </span>
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value.slice(0, APPROVAL_REASON_MAX_CHARS))}
              maxLength={APPROVAL_REASON_MAX_CHARS}
              disabled={busy}
              placeholder={t(
                "승인·반려 사유를 남기면 감사 문서에 기록됩니다",
                "A reason left here is recorded in the audit document",
                "承認・却下の理由を残すと監査文書に記録されます",
              )}
              className="rounded-md border border-slate-300 bg-surface px-2 py-1 text-[14px] text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
            />
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => onRun(reason.trim() || undefined)}
              disabled={runDisabled}
              className="ui-button-primary inline-flex h-9 items-center px-4 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {approval.resolution === "approving" ? t("실행 중...", "Running...", "実行中...") : t("실행", "Run", "実行")}
            </button>
            <button
              type="button"
              onClick={() => onCancelApproval(reason.trim() || undefined)}
              disabled={runDisabled}
              className="ui-button-ghost inline-flex h-9 items-center px-4 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {approval.resolution === "declining" ? t("취소 중...", "Cancelling...", "キャンセル中...") : t("취소", "Cancel", "キャンセル")}
            </button>
          </div>
        </div>
      ) : approval.resolution === "error" ? (
        <div className="mt-2.5 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[13px] leading-5 text-rose-700">
          <p>{approval.errorMessage}</p>
          {approval.errorLoginHref && (
            <Link
              href={approval.errorLoginHref}
              className="mt-1.5 inline-flex items-center font-semibold text-rose-700 underline underline-offset-2"
            >
              {t("로그인하러 가기", "Go to sign-in", "ログインへ進む")}
            </Link>
          )}
        </div>
      ) : (
        <p className="mt-2.5 text-[13px] font-semibold text-slate-500">
          {approval.resolution === "approved" ? t("승인됨", "Approved", "承認済み") : t("취소됨", "Cancelled", "キャンセル済み")}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Unit #47 (b): the Table 5-6 SET, on the card that approves one third of it.
//
// The approval machinery authorises each write's canonical signature
// INDEPENDENTLY -- one action per pause, one signature each -- so a card that
// showed only the action it paused on would present one third of one published
// table row as though it were a whole change. Unit #44 recorded that gap and
// could not close it (preventing a partial approval means changing approval
// machinery shared with every other unit, which is a human decision); what it
// did instead was make the set DATA. This panel is the reader for it.
//
// Every sentence here comes from the backend. `partial_approval_warning` is
// saturation_flow.PARTIAL_APPROVAL_WARNING verbatim -- the same three sentences
// that already end each member's rationale -- and the forecast/radius strings
// are the builder's own. The only prose this component writes is labels.
// ---------------------------------------------------------------------------

const CAR_FOLLOWING_MEMBER_LABELS: Record<string, [string, string, string]> = {
  ax: ["정지 간격", "standstill distance", "停止時車間"],
  bx_add: ["안전거리 가산부", "additive safety distance", "安全距離の加算部"],
  bx_mult: ["안전거리 승산부", "multiplic. safety distance", "安全距離の乗算部"],
};

export function CarFollowingSetPanel({
  set,
  action,
}: {
  set: AgentCarFollowingSet;
  action?: AgentAction;
}) {
  const { t, lang } = useLanguage();
  if (set.status !== "PROPOSED") return null;

  const members = set.set_members ?? [];
  const expected = set.expected_after_apply ?? {};
  // Which of the three this card is actually asking about. The other two are
  // still pending -- that is the whole reason this panel exists.
  const thisParameter = typeof action?.params?.parameter === "string" ? action.params.parameter : "";
  // ... and of those two, which are ALREADY in the model. The set is approved
  // one member at a time, so on the second and third card part of it has landed
  // (refuter, #47: the label used to read "not yet approved" for all of them,
  // which stops being true the moment the first one is applied). Answered from
  // `current_values` -- what the live behaviour holds, read server-side at
  // card-build time -- so this states the MODEL's state, which is the thing the
  // client can actually know, and never an approval history it cannot.
  const current = set.current_values ?? {};
  const landed = (member: string) =>
    typeof current[member] === "number" && current[member] === expected[member];
  const approvedPath = set.preflight_forecast?.paths?.approved_set;
  const directPath = set.preflight_forecast?.paths?.direct_edit;
  const measurement = set.measurement ?? {};
  const lookup = set.lookup ?? {};
  const bracketed = Boolean(lookup.bracket_lower && lookup.bracket_upper);

  return (
    <div className="mt-2 rounded-lg border border-sky-200 bg-sky-50/50 px-2.5 py-2">
      <p className="text-[13px] font-bold uppercase tracking-[0.08em] text-slate-500">
        {t("MRWA Table 5-6 세트", "MRWA Table 5-6 set", "MRWA Table 5-6 の組")}
        <span className="ml-1.5 font-mono text-[13px] font-normal normal-case tracking-normal text-slate-500">
          {t(
            `행태 ${set.behavior_no ?? "-"} · ${members.length}건 중 1건 승인`,
            `behaviour ${set.behavior_no ?? "-"} · approving 1 of ${members.length}`,
            `挙動 ${set.behavior_no ?? "-"} · ${members.length} 件中 1 件を承認`,
          )}
        </span>
      </p>

      {/* The three members, with THIS card's one marked. A member is either
          the write being approved now (sky, the app's selection accent) or one
          of the two that are not. */}
      <div className="mt-1.5 space-y-0.5">
        {members.map((member) => {
          const isThis = member === thisParameter;
          const label = CAR_FOLLOWING_MEMBER_LABELS[member];
          return (
            <div
              key={member}
              className={`flex items-baseline justify-between gap-2 rounded-md px-2 py-1 text-[13px] leading-5 ${
                isThis ? "border border-sky-300 bg-surface text-slate-900" : "text-slate-600"
              }`}
            >
              <span>
                <span className="font-mono">{member}</span>
                {label && <span className="ml-1.5">{t(label[0], label[1], label[2])}</span>}
              </span>
              <span className="flex items-baseline gap-1.5">
                <span className="font-mono font-semibold">{expected[member] ?? "-"}</span>
                <span className="text-[13px] text-slate-500">
                  {isThis
                    ? t("이 승인", "this approval", "この承認")
                    : landed(member)
                      ? t("현재 값 일치", "already at this value", "現在値と一致")
                      : t("미적용", "not applied yet", "未適用")}
                </span>
              </span>
            </div>
          );
        })}
      </div>

      {/* The partial-approval warning, quoted. Neutral ink + heavier hairline
          is this app's WARN treatment (never a fourth hue) -- the same
          treatment the preflight warnings a few lines below wear. */}
      {set.partial_approval_warning && (
        <p className="mt-1.5 rounded-md border border-slate-400 bg-slate-100 px-2 py-1.5 text-[13px] leading-5 text-slate-800">
          {pickText(set.partial_approval_warning, lang)}
        </p>
      )}

      {/* The forecast -- what the guardrail will say on each path -- and the
          radius sentence, which is the thing the human is actually consenting
          to (DECISIONS.md 2026-08-19). */}
      {approvedPath && (
        <p
          className={`mt-1.5 rounded-md border px-2 py-1 text-[13px] leading-5 ${
            approvedPath.expected_to_refuse
              ? "border-rose-200 bg-rose-50 text-rose-700"
              : "border-emerald-200 bg-emerald-50 text-slate-800"
          }`}
        >
          <span className="font-semibold">
            {approvedPath.expected_to_refuse
              ? t("승인해도 거부됩니다", "Refused even once approved", "承認しても拒否されます")
              : t("승인 시 통과 예상", "Expected to pass once approved", "承認時は通過見込み")}
          </span>
          {set.consent_radius_phrase && (
            <span className="ml-1.5 font-mono text-[13px] text-slate-600">
              {set.consent_radius_phrase}
            </span>
          )}
        </p>
      )}

      <details className="mt-1.5 text-[13px] leading-5 text-slate-600">
        <summary className="cursor-pointer list-none text-[13px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (측정 출처·표의 행·예보)", "More (measurement, table row, forecast)", "詳細（測定の出所・表の行・予報）")}
        </summary>
        <div className="mt-1.5 flex flex-col gap-1.5 border-l border-slate-300 pl-3">
          <p>
            <span className="font-semibold">{t("측정 출처", "Measurement source", "測定の出所")}: </span>
            <span className="font-mono">{measurement.saturation_flow_pcu ?? "-"} pcu</span>
            {" · "}
            {measurement.attribution ?? "-"} · {measurement.method ?? "-"}
            {measurement.measured_at ? ` · ${measurement.measured_at}` : ""}
          </p>
          {/* BOTH bracketing rows, so rounding DOWN is auditable rather than
              a number that appeared from somewhere. */}
          {bracketed ? (
            <p>
              <span className="font-semibold">{t("표의 행", "Table row", "表の行")}: </span>
              <span className="font-mono">
                {lookup.bracket_lower?.saturation_flow_pcu} ↔ {lookup.bracket_upper?.saturation_flow_pcu} pcu
              </span>
              {" — "}
              {t("아래 행으로 내림", "rounded DOWN to the lower row", "下の行に切り下げ")}{" "}
              <span className="font-mono">
                {set.applied_row?.saturation_flow_pcu} pcu (
                {set.applied_row?.additive} / {set.applied_row?.multiplicative})
              </span>
            </p>
          ) : (
            <p>
              <span className="font-semibold">{t("표의 행", "Table row", "表の行")}: </span>
              <span className="font-mono">
                {set.applied_row?.saturation_flow_pcu} pcu ({set.applied_row?.additive} /{" "}
                {set.applied_row?.multiplicative})
              </span>
            </p>
          )}
          {directPath?.why && (
            <p>
              <span className="font-semibold">
                {t("승인 밖 경로", "Outside this approval", "この承認の外")}:{" "}
              </span>
              {directPath.why}
            </p>
          )}
          {approvedPath?.why && <p>{approvedPath.why}</p>}
          <p className="font-mono text-[13px] text-slate-400">
            {lookup.citation ?? ""}
            {set.grant?.reason ? ` · ${set.grant.reason}` : ""}
          </p>
        </div>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// REMOVED BY #50-d (the user, 2026-08-20): `GuardrailPathLines`,
// `GuardrailResolutionChips`, and the two path-key constants they read with.
//
// THE AUTHORITY, in the user's own words: "developer side information... get rid
// of it" — recorded in the roadmap row for #50-d as "2경로 예보·결정 칩·mono 코드
// 바 전부 문서/지식 파일로 … #51-c 표시 핀 사용자 권한 제거(기록)". What those
// two components put on screen was constraint ids, condition codes and status
// words (`SHARED_BEHAVIOR_REFUSAL`, `RADIUS_NAMED_IN_FULL`, `REJECTED_BY_USER`)
// beside phrases this client composed for them — the machinery describing itself
// to its own authors, in front of a reviewer deciding one write.
//
// WHAT DID NOT CHANGE, and this is the load-bearing half: the BACKEND. The
// two-path forecast is still computed and still on the wire
// (backend/services/agent/parameter_search.py's PATH_DIRECT_EDIT /
// PATH_APPROVED_SET), the shared-behaviour guardrail still refuses a direct
// write BY NAME, and the approved path still re-mints a single-use grant
// (searched_behaviour_broker.py, gates.py). Removing a rendering removes no
// enforcement — and the contract suite pins the backend constants so a rename
// there still cannot pass silently.
//
// WHERE THE EXPLANATION WENT: D16 of docs/product/calibration-doctrine.md,
// rendered at /docs by lib/help-content.tsx's `calibrationHelpEntries`.
//
// WHAT STAYED ON THE CARD: the evidence-rank sentence (below), unfolded. It is
// the one thing here a reviewer's DECISION depends on — a searched value is
// evidence one rank below a Table 5-6 value — and DECISIONS.md 2026-08-20 made
// showing it a closing requirement. It is a verdict, not developer information.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Unit #51-c: the finished SEARCH, on the card that approves one write of it.
//
// CarFollowingSetPanel's twin, and the differences between them are the whole
// reason this is a second panel rather than a parameter on the first:
//
//   * a Table 5-6 value is READ from a published table with a measured number;
//     a searched value is CHOSEN inside a published band. `evidence_rank` is the
//     backend's own sentence saying so, and DECISIONS.md 2026-08-20 made showing
//     it a closing requirement of the decision -- so it renders UNFOLDED, above
//     the fold, never inside the <details>. A reader who never opens the fold
//     must still have read it.
//   * what stands behind the number is a LEDGER and a HOLDOUT, not a table row,
//     so the fold names the ledger digest, the held-out nodes and the unseen
//     seed the winner was re-scored at.
//
// Every sentence is the backend's. The only prose this component writes is
// labels -- the same rule CarFollowingSetPanel states above.
// ---------------------------------------------------------------------------
export function SearchedParameterPanel({
  search,
  action,
}: {
  search: AgentSearchedSearch;
  action?: AgentAction;
}) {
  const { t, lang } = useLanguage();
  if (search.status !== "PROPOSED") return null;

  const parameters = search.searched_parameters ?? [];
  const expected = search.expected_after_apply ?? {};
  const current = search.current_values ?? {};
  const thisParameter = typeof action?.params?.parameter === "string" ? action.params.parameter : "";
  const landed = (name: string) =>
    typeof current[name] === "number" && current[name] === expected[name];
  const holdout = search.holdout ?? {};
  const ledger = search.ledger ?? {};

  return (
    <div className="mt-2 rounded-lg border border-sky-200 bg-sky-50/50 px-2.5 py-2">
      <p className="text-[13px] font-bold uppercase tracking-[0.08em] text-slate-500">
        {t("범위-구속 탐색 결과", "Range-bounded search result", "範囲拘束探索の結果")}
        <span className="ml-1.5 font-mono text-[13px] font-normal normal-case tracking-normal text-slate-500">
          {t(
            `행태 ${search.behavior_no ?? "-"} · ${parameters.length}건 중 1건 승인`,
            `behaviour ${search.behavior_no ?? "-"} · approving 1 of ${parameters.length}`,
            `挙動 ${search.behavior_no ?? "-"} · ${parameters.length} 件中 1 件を承認`,
          )}
        </span>
      </p>

      <div className="mt-1.5 space-y-0.5">
        {parameters.map((name) => {
          const isThis = name === thisParameter;
          return (
            <div
              key={name}
              className={`flex items-baseline justify-between gap-2 rounded-md px-2 py-1 text-[13px] leading-5 ${
                isThis ? "border border-sky-300 bg-surface text-slate-900" : "text-slate-600"
              }`}
            >
              <span className="font-mono">{name}</span>
              <span className="flex items-baseline gap-1.5">
                <span className="font-mono font-semibold">{expected[name] ?? "-"}</span>
                <span className="text-[13px] text-slate-500">
                  {isThis
                    ? t("이 승인", "this approval", "この承認")
                    : landed(name)
                      ? t("현재 값 일치", "already at this value", "現在値と一致")
                      : t("미적용", "not applied yet", "未適用")}
                </span>
              </span>
            </div>
          );
        })}
      </div>

      {/* THE CLOSING REQUIREMENT of DECISIONS.md 2026-08-20. Never folded, never
          shortened: this value's evidence ranks one below Table 5-6's, and the
          card that approves it says so where the reviewer is looking. */}
      {search.evidence_rank && (
        <p className="mt-1.5 rounded-md border border-slate-400 bg-slate-100 px-2 py-1.5 text-[13px] leading-5 text-slate-800">
          {pickText(search.evidence_rank, lang)}
        </p>
      )}

      {/* The radius the human is actually consenting to (DECISIONS.md
          2026-08-19). The two-path forecast and the resolution chips that used
          to bracket this line were removed by #50-d — see the note above — and
          the radius was not: it is the SCOPE of the consent this button gives,
          which is decision material, not developer information. */}
      {search.consent_radius_phrase && (
        <p className="mt-1 font-mono text-[13px] text-slate-600">{search.consent_radius_phrase}</p>
      )}

      <details className="mt-1.5 text-[13px] leading-5 text-slate-600">
        <summary className="cursor-pointer list-none text-[13px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (원장·홀드아웃·예보)", "More (ledger, holdout, forecast)", "詳細（台帳・ホールドアウト・予報）")}
        </summary>
        <div className="mt-1.5 flex flex-col gap-1.5 border-l border-slate-300 pl-3">
          <p>
            <span className="font-semibold">{t("탐색 원장", "Search ledger", "探索台帳")}: </span>
            <span className="font-mono">
              {ledger.search_id || "-"} · {ledger.rows ?? "-"} rows · {ledger.digest || "-"}
            </span>
            {typeof search.candidates === "number" && (
              <span className="font-mono"> · {search.candidates} candidates</span>
            )}
          </p>
          <p>
            <span className="font-semibold">{t("홀드아웃", "Holdout", "ホールドアウト")}: </span>
            <span className="font-mono">
              {(holdout.holdout_nodes ?? []).join(", ") || "-"} · seed {holdout.unseen_seed ?? "-"} ·{" "}
              {holdout.baseline_objective ?? "-"} → {holdout.winner_objective ?? "-"}
            </span>
            {holdout.code && <span className="font-mono"> · {holdout.code}</span>}
          </p>
          {search.unchanged_parameters && search.unchanged_parameters.length > 0 && (
            <p>
              <span className="font-semibold">
                {t("기준값 유지", "Left at baseline", "基準値のまま")}:{" "}
              </span>
              <span className="font-mono">{search.unchanged_parameters.join(", ")}</span>
            </p>
          )}
          {/* The per-path forecast sentences went with the lines they explained
              (#50-d). The LEDGER, the HOLDOUT and the citation above and below
              are what stands behind the NUMBER this card approves, and they
              stay. */}
          {search.rationale && <p>{pickText(search.rationale, lang)}</p>}
          <p className="font-mono text-[13px] text-slate-400">
            {search.citation ?? ""}
            {search.grant?.reason ? ` · ${search.grant.reason}` : ""}
          </p>
        </div>
      </details>
    </div>
  );
}

// M2.3 tool -> human label map for the approval card (docs/phase2/02-ROADMAP.md
// M4's "plan-approval card" bullet). Falls back to the raw tool name for
// anything not in this table (e.g. a future write verb landing ahead of a
// frontend update).
export const AGENT_ACTION_LABELS: Record<string, [string, string, string]> = {
  set_vehicle_input_volume: ["차량 입력 교통량 변경", "Change vehicle input volume", "車両入力交通量の変更"],
  set_signal_cycle: ["신호 주기 변경", "Change signal cycle", "信号周期の変更"],
  set_signal_offset: ["신호 옵셋 변경", "Change signal offset", "信号オフセットの変更"],
  set_sim_duration: ["시뮬레이션 시간 변경", "Change simulation duration", "シミュレーション時間の変更"],
  set_random_seed: ["랜덤 시드 변경", "Change random seed", "ランダムシードの変更"],
  run_simulation: ["시뮬레이션 실행", "Run simulation", "シミュレーション実行"],
  // Unit #47: the write unit #44's Table 5-6 set is made of. Reachable through
  // an approval card since this unit, so it needs a human label like the rest.
  set_driving_behavior_parameter: [
    "주행 행태 파라미터 변경",
    "Change driving behaviour parameter",
    "走行挙動パラメータの変更",
  ],
};

export function agentActionLabel(tool: string, t: Translate): string {
  const entry = AGENT_ACTION_LABELS[tool];
  return entry ? t(entry[0], entry[1], entry[2]) : tool;
}

// Defect 3 fix (docs/phase2/DECISIONS.md, 2026-07-31): the generic
// key=value fallback below used bare `String(value)`, which on a non-scalar
// param (e.g. apply_edit_plan's own `steps`, an array of {tool, params}
// objects) stringifies as "[object Object],[object Object]" -- valid JS,
// meaningless on an approval card. Generic fix, not an apply_edit_plan
// special case, so any future write tool with an object/array param never
// regresses the same way: an object/array renders as compact JSON, bounded
// so one pathological param value can never blow out the card's layout.
// Scalar values (string/number/boolean/null/undefined) are untouched --
// `String()` on any of those already reads correctly and this must stay
// byte-identical for the tools that rely on it today.
const PARAM_VALUE_MAX_CHARS = 80;

function formatParamValue(value: unknown): string {
  if (value !== null && typeof value === "object") {
    const json = JSON.stringify(value);
    return json.length > PARAM_VALUE_MAX_CHARS ? `${json.slice(0, PARAM_VALUE_MAX_CHARS - 1)}…` : json;
  }
  return String(value);
}

// Readable "field -> new value" line per action, param names matching
// backend/services/agent/tools_edit.py's pydantic params models exactly
// (sc_no/cycle, sc_no/offset, seconds, seed). Falls back to a plain key=value
// dump for an unrecognized tool.
//
// set_vehicle_input_volume deliberately has NO case here any more: a bare
// "차량 입력 107 → 6000 veh/h" is exactly the line the user could not verify
// (2026-07-28 sibling-disambiguation unit). It is rendered by
// VehicleInputActionDetail above instead, which needs several elements and a
// fetch and so cannot be one string.
export function agentActionDetail(action: AgentAction, t: Translate): string {
  const params = action.params ?? {};
  switch (action.tool) {
    case "set_signal_cycle":
      return t(
        `신호제어기 ${params.sc_no} 주기 → ${params.cycle}초`,
        `Signal controller ${params.sc_no} cycle → ${params.cycle}s`,
        `信号制御機 ${params.sc_no} 周期 → ${params.cycle}秒`,
      );
    case "set_signal_offset":
      return t(
        `신호제어기 ${params.sc_no} 옵셋 → ${params.offset}초`,
        `Signal controller ${params.sc_no} offset → ${params.offset}s`,
        `信号制御機 ${params.sc_no} オフセット → ${params.offset}秒`,
      );
    case "set_sim_duration":
      return t(
        `시뮬레이션 시간 → ${params.seconds}초`,
        `Simulation duration → ${params.seconds}s`,
        `シミュレーション時間 → ${params.seconds}秒`,
      );
    case "set_random_seed":
      return t(`랜덤 시드 → ${params.seed}`, `Random seed → ${params.seed}`, `ランダムシード → ${params.seed}`);
    case "run_simulation": {
      const seeds = params.random_seeds as number[] | null | undefined;
      const seedsLabel =
        seeds && seeds.length > 0
          ? t(`시드 ${seeds.join(", ")}`, `seeds ${seeds.join(", ")}`, `シード ${seeds.join(", ")}`)
          : t("기본 시드", "default seed", "デフォルトシード");
      return t(
        `${params.duration_s}초 실행 · ${seedsLabel}`,
        `Run for ${params.duration_s}s · ${seedsLabel}`,
        `${params.duration_s}秒実行 · ${seedsLabel}`,
      );
    }
    default:
      return Object.entries(params)
        .map(([key, value]) => `${key}=${formatParamValue(value)}`)
        .join(", ");
  }
}

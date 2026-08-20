"use client";

// ---------------------------------------------------------------------------
// Pending proposals, AT THE STEP THEY BELONG TO (unit #50, restructured by
// #50-b — this file was ProposalRail.tsx).
//
// WHAT CHANGED AND WHY. #50 gave the workspace a "제안" section: every agent
// pause still waiting for a human, collected in one panel. Driving the real
// screen, the user's decision was to DELETE that panel — a proposal read
// separately from the step it acts on is a decision made without its evidence,
// and the collection was one more thing stacked down the page. So the cards now
// render inside their own station (lib/calibrationStations.ts maps tool →
// §2.12.1 step → station) and the rail carries a sky badge per station, which
// is what makes them findable without a panel that gathers them.
//
// NOTHING WAITING IS EVER HIDDEN. Two invariants carry that promise:
//   * `groupProposals` places EVERY proposal — one whose tool this product
//     cannot map to a guideline step lands at the default station with its tool
//     named, never dropped (the contract suite executes this);
//   * the empty state of a station says how many proposals are waiting
//     ELSEWHERE, so "없습니다" here can never be read as "없습니다" everywhere.
//
// TWO THINGS THIS DOES NOT DO, both unchanged from #50 and both deliberate:
//
// 1. **It does not implement an approval card.** It renders `AgentApprovalCard`
//    from components/agent/ApprovalCards.tsx — the card #47 wrote, moved there
//    by #50 precisely so there is one of it. A second card would be a second
//    place for the sentence a human consents to to drift.
//
// 2. **It does not implement an approval machine.** The decision goes to
//    `approveAgentAction` → POST /api/assistant/agent/approve, the same
//    single-use token, the same `require_approver` identity check, the same
//    re-minted car-following grant. `GET /api/calibration/pending-approvals`
//    only makes the pause VISIBLE; it never spends it.
//
// The 401/403/404 branching mirrors the drawer's for the same reasons: 404 is
// a stale or already-consumed approval, 401 offers a way to sign in, and 403 —
// signed in, wrong role — surfaces the server's own sentence and offers no
// login link, because one would imply the role could be fixed by re-entering.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";

import { AgentApprovalCard, type AgentApprovalState } from "@/components/agent/ApprovalCards";
import { useLanguage } from "@/components/LanguageProvider";
import {
  AgentApprovalError,
  approveAgentAction,
  fetchCalibrationPendingApprovals,
  type CalibrationPendingApproval,
} from "@/lib/api";
import { failed, ok, resolve, type Loaded } from "@/lib/asyncResource";
import { loginHref } from "@/lib/authRedirect";
import {
  groupProposals,
  stationResolverFor,
  type GroupedProposals,
  type PlacedProposal,
  type StepLike,
} from "@/lib/calibrationStations";

type Translate = (ko: string, en: string, ja?: string) => string;

function toApprovalState(pending: CalibrationPendingApproval): AgentApprovalState {
  return {
    approvalId: pending.approval_id,
    sessionId: pending.session_id,
    actions: pending.actions ?? [],
    warnings: pending.warnings ?? [],
    assumptions: pending.assumptions ?? [],
    resolution: "pending",
    focus: pending.focus ?? undefined,
    carFollowingSet: pending.car_following_set ?? undefined,
    searchedSearch: pending.searched_search ?? undefined,
  };
}

/** The listing takes no parameters, so its request key never varies. */
const PENDING_KEY = "pending-approvals";

export type ProposalDecider = (approval: AgentApprovalState, approved: boolean, reason?: string) => void;

/**
 * The pending listing, read ONCE for the whole workspace.
 *
 * One read, not one per station: the rail's badges and the station's cards are
 * the same fact seen twice, and two requests would let a badge say 2 while the
 * station it points at shows 1.
 */
export function usePendingProposals({
  steps,
  onResolved,
}: {
  /** The rail's own steps, so a manual-check step's proposal lands in
   * 수동 확인 영역 because the SERVER flagged that step manual. */
  steps: readonly StepLike[];
  onResolved?: () => void;
}): {
  grouped: GroupedProposals<AgentApprovalState>;
  loading: boolean;
  error: string | null;
  decide: ProposalDecider;
} {
  const { t } = useLanguage();
  const pathname = usePathname();

  const [loaded, setLoaded] = useState<Loaded<CalibrationPendingApproval[]> | null>(null);
  const { data: pending, error, loading } = resolve(loaded, PENDING_KEY);
  // What THIS page did to an approval, by id. Kept apart from the server's
  // listing rather than merged into it, because the two answer different
  // questions: the listing says what is still waiting, this says what the
  // reviewer in front of us decided. Merging them used to be a setState inside
  // the fetch; keeping them apart makes the merge a derivation instead.
  const [decisions, setDecisions] = useState<Record<string, AgentApprovalState>>({});

  const approvals = useMemo(() => {
    const rows = pending ?? [];
    const fresh = rows.map((item) => decisions[item.approval_id] ?? toApprovalState(item));
    // A card this page resolved is gone from the listing the moment the approve
    // lands (the pending file is single-use). Dropping it here would erase the
    // outcome the reviewer just produced, so it stays until they navigate away.
    const departed = Object.values(decisions).filter(
      (decision) => !rows.some((row) => row.approval_id === decision.approvalId),
    );
    return [...fresh, ...departed];
  }, [pending, decisions]);

  const grouped = useMemo(
    () => groupProposals(approvals, stationResolverFor(steps)),
    [approvals, steps],
  );

  useEffect(() => {
    let cancelled = false;
    fetchCalibrationPendingApprovals()
      .then((body) => {
        if (!cancelled) setLoaded(ok(PENDING_KEY, body.pending));
      })
      .catch((caught) => {
        if (!cancelled) setLoaded(failed(PENDING_KEY, caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const decide = useCallback<ProposalDecider>(
    (approval, approved, reason) => {
      if (approval.resolution !== "pending") return;
      setDecisions((previous) => ({
        ...previous,
        [approval.approvalId]: { ...approval, resolution: approved ? "approving" : "declining" },
      }));
      approveAgentAction({
        session_id: approval.sessionId,
        approval_id: approval.approvalId,
        approved,
        reason,
      })
        .then(() => {
          setDecisions((previous) => ({
            ...previous,
            [approval.approvalId]: { ...approval, resolution: approved ? "approved" : "declined" },
          }));
          onResolved?.();
        })
        .catch((caught) => {
          const status = caught instanceof AgentApprovalError ? caught.status : null;
          const message =
            status === 404
              ? t(
                  "이 승인은 이미 처리되었거나 만료되었습니다.",
                  "This approval was already handled, or has expired.",
                  "この承認は既に処理済みか、期限切れです。",
                )
              : caught instanceof Error
                ? caught.message
                : String(caught);
          setDecisions((previous) => ({
            ...previous,
            [approval.approvalId]: {
              ...approval,
              resolution: "error",
              errorMessage: message,
              errorLoginHref: status === 401 ? loginHref(pathname) : undefined,
            },
          }));
        });
    },
    [onResolved, pathname, t],
  );

  return { grouped, loading, error, decide };
}

/** Why this card is at THIS station — printed only when it is not obvious.
 *
 * A card placed by its own tool at its own step needs no caption. The cases
 * that DO are the ones where a reader could otherwise be misled: an edit this
 * product cannot map to a guideline step, or one whose step's station could not
 * be resolved (neither may look like a step-⑩ demand edit merely because it is
 * sitting at the default station), and a batch that also moves another step.
 *
 * #50-d: A MARKER, NOT A PARAGRAPH. Each of the three cases used to explain
 * itself in a full sentence ("…so it is shown here (scoring & diagnosis) — the
 * step is not invented"), stacked above the approval card a reviewer is there to
 * decide on. What the reviewer needs from this strip is WHICH of the three
 * exceptions applies; WHY the product refuses to invent a step is doctrine, and
 * all three sentences are verbatim at /docs (D11 in
 * docs/product/calibration-doctrine.md). The tool ids stay one click below,
 * where they were, because they are what a reader checks when the placement
 * itself looks wrong. */
function PlacementNote({
  placed,
  t,
}: {
  placed: PlacedProposal<AgentApprovalState>;
  t: Translate;
}) {
  const unmapped = placed.step === null;
  if (!placed.fallback && placed.alsoSteps.length === 0 && placed.unmappedTools.length === 0) return null;
  return (
    <div className="rounded-t-lg border border-b-0 border-slate-300 bg-slate-50 px-2.5 py-1.5 text-[14px] leading-6 text-slate-700">
      <span className="font-semibold">
        {unmapped
          ? t("단계 미매핑 — 채점·진단에 배치", "No mapped step — placed at scoring & diagnosis", "段階未対応 — 採点・診断に配置")
          : placed.fallback
            ? t(
                `단계 ${placed.step} · 스테이션 미해결 — 채점·진단에 배치`,
                `Step ${placed.step} · station unresolved — placed at scoring & diagnosis`,
                `段階${placed.step} · ステーション未解決 — 採点・診断に配置`,
              )
            : t("다른 단계의 조치도 포함", "Also carries other steps' actions", "他の段階の操作も含む")}
      </span>
      {/* WHICH tool, and which other steps — the machine names behind the
          marker above. #50-c folded them (the user, 2026-08-20): the marker is
          what a reviewer decides on, the tool ids are what they check when
          the placement itself looks wrong. Nothing is dropped. */}
      <details className="mt-0.5 text-[13px] leading-5 text-slate-500">
        <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (편집 도구·단계)", "More (the edit tools, and the steps)", "詳細（編集ツール・段階）")}
        </summary>
        <p className="mt-1 border-l border-slate-300 pl-3 font-mono text-[13px] text-slate-500">
          {[
            placed.tool,
            ...placed.alsoSteps.map((step) => `${t("단계", "step", "段階")} ${step}`),
            ...placed.unmappedTools,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </details>
    </div>
  );
}

/**
 * The cards waiting at ONE station.
 *
 * `otherCount` is what stops the empty state from lying by omission: a station
 * with nothing pending says so AND says how many are waiting at other steps.
 */
export default function StationProposals({
  placed,
  otherCount,
  loading,
  error,
  decide,
}: {
  placed: PlacedProposal<AgentApprovalState>[];
  otherCount: number;
  loading: boolean;
  error: string | null;
  decide: ProposalDecider;
}) {
  const { t } = useLanguage();

  if (error) {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-[14px] leading-6 text-rose-700">
        {t("대기 중인 제안을 불러오지 못했습니다:", "Pending proposals could not be loaded:", "保留中の提案を読み込めませんでした:")}{" "}
        {error}
      </div>
    );
  }

  if (placed.length === 0) {
    // ONE SHORT LINE, AND NOTHING ELSE (#50-d ⑤). #50-c put the explanation in a
    // fold under this line; the user's 2026-08-20 decision removed the fold —
    // "승인 대기 제안 없음" is already the whole fact, and a disclosure under a
    // six-word absence is furniture. Both sentences it held are verbatim at
    // /docs (D12 in docs/product/calibration-doctrine.md).
    //
    // The `otherCount` half is NOT prose and never folds — and now cannot,
    // because there is no fold: "없습니다" here would read as "없습니다"
    // everywhere without it, which is the one thing this empty state exists to
    // prevent.
    return (
      <div className="text-[14px] leading-6 text-slate-600">
        <p>
          {loading
            ? t("대기 중인 제안을 확인하는 중...", "Checking for pending proposals...", "保留中の提案を確認中...")
            : otherCount > 0
              ? t(
                  `이 단계에는 승인 대기 제안 없음 · 다른 단계 ${otherCount}건`,
                  `Nothing awaits approval at this step · ${otherCount} at other steps`,
                  `この段階には承認待ちなし · 他の段階に${otherCount}件`,
                )
              : t("승인 대기 제안 없음", "Nothing awaits approval", "承認待ちの提案なし")}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {placed.map((entry) => (
        <div key={entry.proposal.approvalId}>
          <PlacementNote placed={entry} t={t} />
          <AgentApprovalCard
            approval={entry.proposal}
            // This page is not a conversation, so it has no turn that could have
            // edited the model. `false` is the honest value: the card then shows
            // the base model's current value, which on this screen IS the truth.
            modelEdited={false}
            onRun={(reason) => decide(entry.proposal, true, reason)}
            onCancelApproval={(reason) => decide(entry.proposal, false, reason)}
          />
        </div>
      ))}
    </div>
  );
}

"use client";

// ---------------------------------------------------------------------------
// The proposal rail (unit #50): every agent pause in this workspace that is
// still waiting for a human, on the calibration screen instead of only inside
// whichever tab opened the stream.
//
// TWO THINGS THIS DOES NOT DO, both deliberate:
//
// 1. **It does not implement an approval card.** It renders `AgentApprovalCard`
//    from components/agent/ApprovalCards.tsx — the card #47 wrote, moved there
//    by this unit precisely so there is one of it. A second card would be a
//    second place for the sentence a human consents to to drift.
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
  };
}

/** The listing takes no parameters, so its request key never varies. */
const PENDING_KEY = "pending-approvals";

export default function ProposalRail({ onResolved }: { onResolved?: () => void }) {
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

  const decide = useCallback(
    async (approval: AgentApprovalState, approved: boolean, reason?: string) => {
      if (approval.resolution !== "pending") return;
      setDecisions((previous) => ({
        ...previous,
        [approval.approvalId]: { ...approval, resolution: approved ? "approving" : "declining" },
      }));
      try {
        await approveAgentAction({
          session_id: approval.sessionId,
          approval_id: approval.approvalId,
          approved,
          reason,
        });
        setDecisions((previous) => ({
          ...previous,
          [approval.approvalId]: { ...approval, resolution: approved ? "approved" : "declined" },
        }));
        onResolved?.();
      } catch (caught) {
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
      }
    },
    [onResolved, pathname, t],
  );

  if (error) {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-[14px] leading-6 text-rose-700">
        {t("대기 중인 제안을 불러오지 못했습니다:", "Pending proposals could not be loaded:", "保留中の提案を読み込めませんでした:")}{" "}
        {error}
      </div>
    );
  }

  if (approvals.length === 0) {
    return (
      <p className="text-[14px] leading-6 text-slate-600">
        {loading
          ? t("대기 중인 제안을 확인하는 중...", "Checking for pending proposals...", "保留中の提案を確認中...")
          : t(
              "승인을 기다리는 제안이 없습니다. 제안은 어시스턴트가 만들며, 만들어지면 여기에도 나타납니다.",
              "No proposal is waiting for approval. Proposals are produced by the assistant, and appear here when they are.",
              "承認待ちの提案はありません。提案はアシスタントが作成し、作成されるとここにも表示されます。",
            )}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {approvals.map((approval) => (
        <AgentApprovalCard
          key={approval.approvalId}
          approval={approval}
          // This page is not a conversation, so it has no turn that could have
          // edited the model. `false` is the honest value: the card then shows
          // the base model's current value, which on this screen IS the truth.
          modelEdited={false}
          onRun={(reason) => void decide(approval, true, reason)}
          onCancelApproval={(reason) => void decide(approval, false, reason)}
        />
      ))}
    </div>
  );
}

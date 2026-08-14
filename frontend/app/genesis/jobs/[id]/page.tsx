"use client";

// Unit #23 -- the genesis DRAWER becomes its own route. Roadmap v2 §5.B B1 +
// the mockup at docs/product/mockups/genesis-detail-layout-mockup-2026-08-14.html,
// option D (user decision, 2026-08-14): "지도가 크고, 판정과 지도가 동시에 보임" --
// the drawer's real workflow (read a finding, look at where it is on the map,
// approve a fix) needs the map wide, and a 46%-of-content-width column could
// never give it that. So every job now has its own URL, and this page IS the
// drawer -- transplanted, not redesigned: every doctrine paragraph, every
// gate/fix mechanic and every fetch-caching discipline app/genesis/page.tsx's
// drawer used to carry lives here verbatim, just under a new floor plan (top
// bar, then map LEFT ~65% / verdict rail RIGHT ~35%).
//
// -------------------------------------------------------------------------
// DEEP-LINK COLD LOAD, and the one behaviour a route gives the old drawer that
// a `selectedId` in page state never could
// -------------------------------------------------------------------------
// Every fetch below keys off `jobId`, read from the URL via `useParams` --
// never off anything the hub might have had in memory, because a reader can
// land here directly (a shared link, a bookmark, B4 demo notes) with no hub
// visit in this tab's history at all. An id the backend does not recognise
// (typo'd, deleted, or another project's job -- the backend answers all three
// the same way the artifact route already does, see `downloadFailureText` in
// the old drawer) renders ONE named empty state with a way back, never a
// crash and never a silently blank page.
//
// THE STALE-FLASH TRAP, and how this file avoids it. `router.push` to a
// SIBLING job's URL (the fix-apply flow below) re-renders this SAME page
// component with a new `jobId` param -- React does not remount it, so a
// `useState` holding "the job" would go on showing the OLD job's verdict
// under the NEW job's URL until the next poll tick overwrites it. The old
// drawer already had the fix for exactly this shape of bug (switching which
// row was selected without a remount): store the fetched record WITH the key
// it was fetched for, and read it back only when that key still matches.
// `selected` below keeps that exact discipline, now keyed on the URL's
// `jobId` instead of a `selectedId` state -- the moment the id changes, the
// stale record stops qualifying and the page falls back to its loading state
// on the very next render, not on the next network round trip.
//
// Every gate/fixes caching key, the polling cadence, the fix-disposition
// trap, the three-colour rule and the tri-state doctrine are UNCHANGED from
// the file this page's content was moved out of -- see that file's own
// header comment (app/genesis/page.tsx) for the parts of this doctrine that
// stayed on the hub.

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import GenesisComparisonPanel from "@/components/genesis/GenesisComparisonPanel";
import GenesisTileMap from "@/components/genesis/GenesisTileMap";
import { InfoTip, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  applyGenesisFix,
  fetchGenesisArtifact,
  fetchGenesisGateReport,
  fetchGenesisJob,
  fetchGenesisJobFixes,
  fetchGenesisJobs,
  type GenesisArtifactKind,
  type GenesisFixDisposition,
  type GenesisFixFinding,
  type GenesisGateCheck,
  type GenesisGateReport,
  type GenesisJob,
  type GenesisJobFixes,
} from "@/lib/api";
import {
  POLL_INTERVAL_MS,
  TERMINAL_RECHECK_MS,
  formatTimestamp,
  isTerminal,
  networkScaleLabel,
  statusClasses,
  statusLabel,
} from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** The generation form's own unknown-code sentence -- kept here too (not only
 * on the hub) because `selected.refusal` (a job that failed before it ever
 * reached a gate report) renders through the same `refusalHeadline` fallback
 * on THIS page. Duplicated on purpose rather than imported across pages: it
 * is three lines of pure text with no state, and the alternative (importing
 * a helper from a sibling page.tsx) is not a pattern this app uses anywhere
 * else. */
function generationRefusalFallback(t: Translate): string {
  return t("생성이 거부되었습니다", "Generation was refused", "生成が拒否されました");
}

/** PASS / FAIL / NOT CHECKABLE -- three words, three treatments, never two.
 * `not_checkable` keeps its OWN NOUN, which is the part R1.5 actually requires:
 * a check that could not run may never be counted as a pass, so it may never
 * borrow the pass word. Its treatment is mono-with-a-doubled-border rather than
 * a third hue (see `checkStateTagClasses` below). */
function checkStateLabel(state: GenesisGateCheck["state"], t: Translate): string {
  switch (state) {
    case "pass":
      return t("통과", "PASS", "合格");
    case "fail":
      return t("실패", "FAIL", "不合格");
    case "not_checkable":
      return t("확인 불가", "NOT CHECKABLE", "確認不可");
    default:
      return state;
  }
}

/** THE STATE TAG CHIP -- the ONLY thing on this screen that carries a check's
 * colour (user decision from live review, 2026-08-13; see the original
 * comment history in app/genesis/page.tsx's own git log for the full
 * rationale, unchanged by this unit's move). */
function checkStateTagClasses(state: GenesisGateCheck["state"]): string {
  if (state === "pass") return "border-emerald-300 bg-emerald-50 text-emerald-800";
  if (state === "fail") return "border-rose-300 bg-rose-50 text-rose-800";
  return "border-2 border-slate-400 bg-slate-100 text-slate-600";
}

/** The card behind a check -- IDENTICAL for every state; selection is the
 * ONLY thing that changes it. */
function checkCellClasses(isSelected: boolean): string {
  return isSelected
    ? "border-slate-300 bg-slate-100 text-slate-900 ring-2 ring-slate-500"
    : "border-slate-200 bg-slate-50 text-slate-800";
}

function verdictLabel(verdict: GenesisGateReport["verdict"], t: Translate): string {
  switch (verdict) {
    case "pass":
      return t("통과 — 출하 가능", "PASS — shippable", "合格 — 出荷可");
    case "refused":
      return t("거부 — 결함이 입증되었습니다", "REFUSED — a fault was proven", "拒否 — 欠陥が証明されました");
    case "incomplete":
      return t(
        "미완 — 실행되지 못한 거부 등급 검사가 있습니다 (통과 아님)",
        "INCOMPLETE — a refuse-tier check could not run (not a pass)",
        "未完 — 実行できなかった拒否ティア検査があります（合格ではありません）",
      );
    default:
      return verdict;
  }
}

/** THE CONSOLE STAT -- a small muted label above a large bold value. */
function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-[86px]">
      <p className="text-[14px] text-slate-500">{label}</p>
      <p className={`mt-0.5 text-[22px] font-semibold leading-none ${tone ?? "text-slate-900"}`}>
        {value}
      </p>
    </div>
  );
}

/** Everything a check records beyond its own state. */
function checkHasBody(check: GenesisGateCheck): boolean {
  return (
    check.gaps.length > 0 ||
    check.findings.length > 0 ||
    check.warnings.length > 0 ||
    check.findings_omitted > 0
  );
}

// ---------------------------------------------------------------------------
// B5-2 stage 4 -- the fix strip. Transplanted verbatim from app/genesis/
// page.tsx; see that file's own git history for the full doctrine comments
// this unit did not need to change.
// ---------------------------------------------------------------------------

function fixDispositionIsActionable(disposition: GenesisFixDisposition): boolean {
  return disposition.status === "AUTOMATABLE" && disposition.params !== null;
}

function fixConfidenceLabel(confidence: string, t: Translate): string {
  switch (confidence) {
    case "ARITHMETIC_FROM_OBSERVED_COUNTS":
      return t("산술 계산값 (관측 카운트 기반)", "Arithmetic, from observed counts", "算術計算値（観測カウント基準）");
    case "CONSTRAINT_COMPUTED_CEILING":
      return t("제약이 계산한 상한값", "A constraint's own computed ceiling", "制約が計算した上限値");
    case "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE":
      return t("수요 추정값 (미검증 진입부)", "A demand ESTIMATE (unverified entrance)", "需要推定値（未検証の流入部）");
    case "NONE":
      return t("근거 값 없음", "No confidence value", "根拠値なし");
    default:
      return confidence;
  }
}

function fixIsDemandEstimateFraming(disposition: GenesisFixDisposition): boolean {
  return (
    disposition.confidence === "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE" ||
    disposition.assumptions.includes("ENTRANCE_NOT_VERIFIED_MEASURED") ||
    (disposition.verb === "set_vehicle_input_volume" && disposition.reason_code === "EVIDENCE_INSUFFICIENT")
  );
}

function fixAssumptionLabel(token: string, t: Translate): string {
  switch (token) {
    case "INPUT_DRIVES_NODE_TOTAL":
      return t(
        "이 입력이 교차로 총량을 좌우한다고 가정합니다",
        "Assumes this input drives the node's total",
        "このインプットが交差点合計を左右すると仮定します",
      );
    case "SIBLING_ROUTES_UNCHANGED":
      return t(
        "다른 경로들의 상대 비율은 그대로 둔다고 가정합니다",
        "Assumes the other routes keep their current relative flow",
        "他の経路の相対比率はそのまま維持されると仮定します",
      );
    case "DECISION_MOVEMENTS_COMPLETE":
      return t(
        "제시된 이동류 목록이 이 결정의 전체 범위라고 가정합니다",
        "Assumes the listed movements are this decision's complete scope",
        "提示された移動流のリストがこの決定の全範囲であると仮定します",
      );
    case "INTERVAL_MATCHES_COUNT_PERIOD":
      return t(
        "선택한 구간이 실측 기간과 일치한다고 가정합니다",
        "Assumes the chosen interval matches the count's own period",
        "選択した区間が実測期間と一致すると仮定します",
      );
    case "ENTRANCE_NOT_VERIFIED_MEASURED":
      return t(
        "이 진입부가 실측 지점인지 확인되지 않았다는 전제입니다 (수요 추정)",
        "Assumes this entrance could not be verified as measured (a named demand estimate)",
        "この流入部が実測地点かは確認されていないという前提です（需要推定）",
      );
    default:
      return token;
  }
}

function fixRequiredFieldSpec(field: string, t: Translate): { label: string; numeric: boolean } {
  switch (field) {
    case "volume":
      return { label: t("교통량 (대/시)", "Volume (veh/h)", "交通量（台/時）"), numeric: true };
    case "lane_count":
      return { label: t("차로 수", "Lane count", "車線数"), numeric: true };
    case "pos_m":
      return { label: t("링크상 위치 (m)", "Position on link (m)", "リンク上の位置（m）"), numeric: true };
    default:
      return { label: field, numeric: false };
  }
}

function applyFixErrorText(caught: unknown, t: Translate): { headline: string; reasons: string[] } {
  if (caught instanceof GenesisError) {
    return {
      headline: refusalHeadline(
        caught.code,
        t,
        t("수정을 적용하지 못했습니다", "The fix could not be applied", "修正を適用できませんでした"),
      ),
      reasons: caught.reasons,
    };
  }
  return { headline: caught instanceof Error ? caught.message : String(caught), reasons: [] };
}

/**
 * ONE finding's disposition -- EITHER an informational mono line (never a
 * button: a refusal is information, not a disabled control) OR an approval
 * card. Never a third shape.
 */
function FixFindingRow({
  jobId,
  finding,
  t,
  lang,
  onApplied,
}: {
  jobId: string;
  finding: GenesisFixFinding;
  t: Translate;
  lang: string;
  onApplied: (job: GenesisJob) => void;
}) {
  const disposition = finding.disposition;
  const actionable = fixDispositionIsActionable(disposition);
  const rationale =
    lang === "ko" ? disposition.rationale.ko : lang === "ja" ? disposition.rationale.ja : disposition.rationale.en;

  const [approvalRationale, setApprovalRationale] = useState("");
  const [requiredValues, setRequiredValues] = useState<Record<string, string>>({});
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<GenesisError | Error | null>(null);

  const missingRequired = disposition.requires.some((field) => !requiredValues[field]?.trim());

  const submit = useCallback(async () => {
    if (!disposition.verb || disposition.params === null) return;
    setApplying(true);
    setApplyError(null);
    try {
      const params: Record<string, unknown> = { ...disposition.params };
      for (const field of disposition.requires) {
        const spec = fixRequiredFieldSpec(field, t);
        const raw = requiredValues[field] ?? "";
        params[field] = spec.numeric ? Number(raw) : raw;
      }
      const job = await applyGenesisFix(jobId, {
        finding_code: finding.code,
        // The finding's own identity is (code, subject) -- unit #30. Sending
        // the subject is what makes the SECOND of two same-coded findings its
        // own apply target instead of a collision with the first.
        subject: finding.subject,
        verb: disposition.verb,
        params,
        approval_rationale: approvalRationale.trim() || undefined,
      });
      onApplied(job);
    } catch (caught) {
      setApplyError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setApplying(false);
    }
  }, [disposition, requiredValues, approvalRationale, jobId, finding.code, finding.subject, onApplied, t]);

  const error = applyError ? applyFixErrorText(applyError, t) : null;

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3 text-[15px] text-slate-800">
      <div className="flex flex-wrap items-baseline gap-2">
        {finding.origin === "warning" && <span className="text-[14px] text-slate-500">warn /</span>}
        <span className="font-mono text-[14px] font-semibold text-slate-700">{finding.code}</span>
        <span className="font-mono text-[14px] text-slate-500">{finding.subject}</span>
      </div>

      {!actionable ? (
        <div className="mt-2">
          {/* NOT_AUTOMATABLE (or a structural refusal wearing an AUTOMATABLE
            * status -- see fixDispositionIsActionable) -- ONE mono line, the
            * rationale verbatim, and NEVER a button. */}
          <p className="font-mono text-[15px] leading-relaxed text-slate-700">{rationale}</p>
          {disposition.reason_code && (
            <p className="mt-1 font-mono text-[14px] text-slate-500">{disposition.reason_code}</p>
          )}
        </div>
      ) : (
        <div className="mt-2 flex flex-col gap-2">
          <p className="font-mono text-[14px] text-slate-600">
            {t("동사", "Verb", "動詞")}: {disposition.verb}
          </p>
          <p className="leading-relaxed text-slate-700">{rationale}</p>

          <span
            className={`w-fit rounded-full border px-1.5 py-0.5 text-[14px] font-semibold ${
              fixIsDemandEstimateFraming(disposition)
                ? "border-2 border-slate-400 bg-slate-100 text-slate-900 underline decoration-dotted decoration-slate-400 underline-offset-4"
                : "border-slate-300 bg-white/[0.06] text-slate-600"
            }`}
          >
            {fixConfidenceLabel(disposition.confidence, t)}
          </span>

          {fixIsDemandEstimateFraming(disposition) && (
            <p className="text-[14px] leading-relaxed text-slate-600">
              {t(
                "※ 수요 추정 — 이 값은 검증된 실측이 아니라 명시된 추정치입니다. 실제 진입 교통량을 아신다면 이 제안 대신 그 값을 직접 입력해 주십시오.",
                "NB DEMAND ESTIMATE — this value is a named estimate, not a verified measurement. If you know the real entrance count, enter it directly instead of approving this.",
                "※ 需要推定 — この値は検証済みの実測ではなく、明示された推定値です。実際の流入交通量をご存知であれば、この提案の代わりに直接入力してください。",
              )}
            </p>
          )}

          {disposition.assumptions.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-[14px] leading-relaxed text-slate-600">
              {disposition.assumptions.map((token) => (
                <li key={token}>{fixAssumptionLabel(token, t)}</li>
              ))}
            </ul>
          )}

          {/* The inherited-auto-publish disclosure -- one sentence, not a
            * modal, on every approval card regardless of confidence. */}
          <p className="text-[14px] leading-relaxed text-slate-500">
            {t(
              "승인하면 실제 Vissim 편집이 실행되고 네트워크가 다시 게이트를 거칩니다 — 통과하면 그 결과가 자동으로 이 프로젝트의 활성 데이터셋으로 등록됩니다.",
              "Approving runs a real Vissim edit and re-gates the network — a pass automatically registers the result as this project's active dataset.",
              "承認すると実際のVissim編集が実行され、ネットワークは再度ゲートを通過します — 合格すればその結果は自動的にこのプロジェクトのアクティブなデータセットとして登録されます。",
            )}
          </p>

          {disposition.requires.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {disposition.requires.map((field) => {
                const spec = fixRequiredFieldSpec(field, t);
                return (
                  <label key={field} className="flex items-center gap-2 text-[14px] text-slate-700">
                    <span className="min-w-[120px]">{spec.label}</span>
                    <input
                      type={spec.numeric ? "number" : "text"}
                      value={requiredValues[field] ?? ""}
                      onChange={(event) =>
                        setRequiredValues((current) => ({ ...current, [field]: event.target.value }))
                      }
                      className="w-32 rounded-md border border-slate-300 bg-white/[0.06] px-2 py-1 text-[14px] text-slate-900"
                    />
                  </label>
                );
              })}
            </div>
          )}

          <label className="flex flex-col gap-1 text-[14px] text-slate-600">
            <span>{t("사유 (선택)", "Rationale (optional)", "理由（任意）")}</span>
            <input
              type="text"
              value={approvalRationale}
              onChange={(event) => setApprovalRationale(event.target.value)}
              maxLength={600}
              className="rounded-md border border-slate-300 bg-white/[0.06] px-2 py-1 text-[15px] text-slate-900"
            />
          </label>

          <div>
            <button
              type="button"
              disabled={applying || missingRequired}
              onClick={() => void submit()}
              className="ui-button-primary w-fit rounded-md px-3 py-1.5 text-[15px] disabled:cursor-not-allowed"
            >
              {applying ? t("적용하는 중…", "Applying…", "適用中…") : t("적용 승인", "Approve fix", "適用を承認")}
            </button>
          </div>

          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
              <p className="font-semibold">{error.headline}</p>
              {error.reasons.length > 0 && (
                <ul className="mt-1 space-y-1">
                  {error.reasons.map((reason, index) => (
                    <li key={index} className="font-mono text-[14px] leading-relaxed">
                      {reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * V1–V9 AS A GRID -- EVERY CELL STARTS CLOSED. Transplanted verbatim from
 * app/genesis/page.tsx (2026-08-13 doctrine, unchanged by this unit).
 */
function GateCheckGrid({
  checks,
  t,
  lang,
  jobId,
  fixes,
  fixesError,
  onFixApplied,
}: {
  checks: GenesisGateCheck[];
  t: Translate;
  lang: string;
  jobId: string;
  fixes: GenesisJobFixes | null;
  fixesError: GenesisError | Error | null;
  onFixApplied: (job: GenesisJob) => void;
}) {
  // NO initial selection, by the user's explicit instruction. `null` is the
  // mount state and stays until a click.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = selectedId ? checks.find((check) => check.id === selectedId) ?? null : null;

  return (
    <div>
      <div className="grid grid-cols-3 gap-3.5">
        {checks.map((check) => {
          const isSelected = check.id === selectedId;
          return (
            <button
              key={check.id}
              type="button"
              onClick={() => setSelectedId((current) => (current === check.id ? null : check.id))}
              aria-pressed={isSelected}
              title={check.title}
              className={`flex flex-col gap-1.5 rounded-lg border px-3 py-2.5 text-left transition-[background-color,box-shadow,color] duration-100 ${checkCellClasses(isSelected)}`}
            >
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[15px] font-bold">{check.id}</span>
                <span
                  className={`rounded-full border px-1.5 py-0.5 text-[14px] font-bold uppercase ${checkStateTagClasses(check.state)}`}
                >
                  {checkStateLabel(check.state, t)}
                </span>
              </span>
              {/* CORE NAME ONLY before a click (user, live pass 2026-08-14):
                * the gate titles are "이름 — 설명 한 문장" and nine clamped
                * sentences made the grid a wall of text. The cell shows the
                * name; the full sentence stays on hover (title=) and in the
                * detail below once the cell is opened. Derived by splitting
                * the payload's own string — no client-side name table. */}
              <span
                className={`line-clamp-1 text-[14px] leading-tight ${isSelected ? "font-semibold text-slate-900" : "font-medium"}`}
              >
                {check.title.split(" — ")[0]}
              </span>
            </button>
          );
        })}
      </div>

      <div aria-live="polite">
        {selected && (
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-[15px] text-slate-800">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-[15px] font-bold">{selected.id}</span>
              <span
                className={`rounded-full border px-1.5 py-0.5 text-[14px] font-bold uppercase ${checkStateTagClasses(selected.state)}`}
              >
                {checkStateLabel(selected.state, t)}
              </span>
              <span className="text-[14px] text-slate-600">
                {selected.tier === "refuse"
                  ? t("거부 등급", "refuse tier", "拒否ティア")
                  : t("경고 등급", "warn tier", "警告ティア")}
              </span>
            </div>
            <p className="mt-1.5 font-semibold">{selected.title}</p>

            <div className="mt-2">
              {selected.gaps.length > 0 && (
                <ul className="space-y-1">
                  {selected.gaps.map((gap) => (
                    <li key={gap.code} className="text-[15px] leading-relaxed">
                      {t("실행 불가", "could not run", "実行不可")}: {gap.message}
                    </li>
                  ))}
                </ul>
              )}

              {selected.findings.length > 0 && (
                <CollapsibleMessages
                  key={`f-${selected.id}`}
                  items={selected.findings.map((finding) => finding.message)}
                  t={t}
                />
              )}
              {selected.findings_omitted > 0 && (
                <p className="mt-1.5 text-[14px] text-slate-600">
                  {t(
                    `이 화면에 표시되지 않은 항목 ${selected.findings_omitted}건 — gate.json 전체 내려받기에 모두 들어 있습니다.`,
                    `${selected.findings_omitted} more not shown here — all of them are in the gate.json download.`,
                    `この画面に表示されていない項目 ${selected.findings_omitted} 件 — gate.json のダウンロードにすべて含まれています。`,
                  )}
                </p>
              )}

              {selected.warnings.length > 0 && (
                <CollapsibleMessages
                  key={`w-${selected.id}`}
                  items={selected.warnings.map((warning) => `warn / ${warning.message}`)}
                  t={t}
                />
              )}

              {!checkHasBody(selected) && (
                <p className="text-[15px] leading-relaxed text-slate-600">
                  {t(
                    "이 검사에 기록된 항목이 없습니다.",
                    "Nothing was recorded for this check.",
                    "この検査に記録された項目はありません。",
                  )}
                </p>
              )}

              {checkHasBody(selected) && (
                <div className="mt-4 border-t border-slate-200 pt-3">
                  <p className="text-[15px] font-semibold text-slate-800">
                    {t("수정 제안", "Fix proposals", "修正提案")}
                    <InfoTip
                      text={t(
                        "이 검사가 낸 각 발견에 대해, 등록된 수정 카탈로그가 자동으로 고칠 수 있는지와 그 근거를 답합니다. 자동 수정이 없다는 답은 정보이지 버튼이 아닙니다 — 거부는 진단, 에이전트는 치료.",
                        "For each finding this check raised, the registered fix catalogue answers whether it can be automatically repaired and on what evidence. \"No automated fix\" is information, never a button — a refusal is a diagnosis, and the agent is the treatment.",
                        "この検査が出した各発見について、登録済みの修正カタログが自動修正可能かとその根拠を答えます。「自動修正なし」は情報であり、ボタンではありません — 拒否は診断、エージェントは治療。",
                      )}
                    />
                  </p>

                  {!fixes && !fixesError && (
                    <p className="mt-2 text-[15px] text-slate-500">
                      {t("수정 제안을 불러오는 중…", "Loading fix proposals…", "修正提案を読み込み中…")}
                    </p>
                  )}

                  {fixesError && (
                    <p className="mt-2 text-[15px] leading-relaxed text-slate-600">
                      {fixesError instanceof GenesisError && fixesError.status === 401
                        ? t(
                            "수정 제안을 보려면 로그인이 필요합니다 — 로그인 후 다시 시도하세요.",
                            "Viewing fix proposals requires a signed-in user — sign in and try again.",
                            "修正提案の閲覧にはログインが必要です — ログイン後に再試行してください。",
                          )
                        : t(
                            "수정 제안을 불러오지 못했습니다.",
                            "Could not load fix proposals.",
                            "修正提案を読み込めませんでした。",
                          )}
                    </p>
                  )}

                  {fixes && (
                    <div className="mt-2 flex flex-col gap-3">
                      {fixes.findings
                        .filter((finding) => finding.check_id === selected.id)
                        .map((finding) => (
                          <FixFindingRow
                            key={`${finding.code}::${finding.subject}`}
                            jobId={jobId}
                            finding={finding}
                            t={t}
                            lang={lang}
                            onApplied={onFixApplied}
                          />
                        ))}
                      {fixes.findings.filter((finding) => finding.check_id === selected.id).length === 0 && (
                        <p className="text-[15px] text-slate-500">
                          {t(
                            "이 검사에는 개별 수정 제안이 없습니다.",
                            "No individual fix proposal for this check.",
                            "この検査には個別の修正提案がありません。",
                          )}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** The five downloadable artifacts. */
const ARTIFACT_KINDS: Array<{ kind: GenesisArtifactKind; label: (t: Translate) => string }> = [
  { kind: "inpx", label: (t) => t("네트워크 .inpx", "Network .inpx", "ネットワーク .inpx") },
  { kind: "compliance_md", label: (t) => t("§5 준수 리포트", "§5 compliance report", "§5 準拠レポート") },
  { kind: "gate_report", label: (t) => t("게이트 리포트", "Gate report", "ゲートレポート") },
  { kind: "gate_json", label: () => "gate.json" },
  { kind: "genesis_json", label: (t) => t("생성기 명부", "Generator census", "生成器の名簿") },
];

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function downloadFailureText(caught: unknown, t: Translate): string {
  if (caught instanceof GenesisError) {
    if (caught.status === 401) {
      return t(
        "내려받기에는 로그인이 필요합니다 — 로그인 후 다시 시도하세요.",
        "Downloading an artifact requires a signed-in user — sign in and try again.",
        "ダウンロードにはログインが必要です — ログイン後に再試行してください。",
      );
    }
    if (caught.status === 403) {
      return t(
        "이 프로젝트의 파일을 받을 권한이 없습니다.",
        "You are not allowed to read this project's files.",
        "このプロジェクトのファイルを取得する権限がありません。",
      );
    }
    if (caught.status === 404) {
      return t(
        "이 생성에는 그 파일이 없습니다 — 또는 지금 선택된 프로젝트의 생성이 아닙니다.",
        "This generation has no such file — or it does not belong to the project selected right now.",
        "この生成にはそのファイルがありません — もしくは現在選択中のプロジェクトの生成ではありません。",
      );
    }
    return caught.message;
  }
  return caught instanceof Error ? caught.message : String(caught);
}

/** Long finding lists fold (user, live pass 2026-08-14): V7-class checks carry
 * dozens of same-shaped rows — the first few state the pattern, the rest are
 * one click away. This fold is PRESENTATION ONLY: the server-side cap's own
 * named-omission line ("표시되지 않은 N건 — gate.json") is evidence and renders
 * separately, untouched. Callers key this by the selected check's id so the
 * fold resets when the reader moves to another check. */
const FINDINGS_COLLAPSED_COUNT = 6;

function CollapsibleMessages({ items, t }: { items: string[]; t: Translate }) {
  const [expanded, setExpanded] = useState(false);
  const overflow = items.length - FINDINGS_COLLAPSED_COUNT;
  const visible = expanded || overflow <= 0 ? items : items.slice(0, FINDINGS_COLLAPSED_COUNT);
  return (
    <>
      <ul className="mt-2 space-y-1">
        {visible.map((message, index) => (
          <li key={index} className="text-[15px] leading-relaxed">
            {message}
          </li>
        ))}
      </ul>
      {overflow > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          className="ui-button-ghost mt-1.5 rounded-full px-2.5 py-0.5 text-[14px] font-medium"
        >
          {expanded
            ? t("접기", "Collapse", "折りたたむ")
            : t(`나머지 ${overflow}건 펼치기`, `Show ${overflow} more`, `残り${overflow}件を表示`)}
        </button>
      )}
    </>
  );
}

/** THE ARTIFACT ROW. Unchanged mechanics from app/genesis/page.tsx, only the
 * bar it sits in moved -- top bar, right side, rather than a titled section
 * under the map. Each entry wears a download glyph (user, live pass
 * 2026-08-14: the bare pills read as tags, not as things that download). */
function ArtifactRow({ jobId, t }: { jobId: string; t: Translate }) {
  const [pending, setPending] = useState<GenesisArtifactKind | null>(null);
  const [failure, setFailure] = useState<{ kind: GenesisArtifactKind; message: string } | null>(null);

  const download = useCallback(
    async (kind: GenesisArtifactKind) => {
      setPending(kind);
      setFailure(null);
      try {
        const artifact = await fetchGenesisArtifact(jobId, kind);
        saveBlob(artifact.blob, artifact.filename);
      } catch (caught) {
        setFailure({ kind, message: downloadFailureText(caught, t) });
      } finally {
        setPending(null);
      }
    },
    [jobId, t],
  );

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {ARTIFACT_KINDS.map((artifact) => (
          <button
            key={artifact.kind}
            type="button"
            disabled={pending !== null}
            onClick={() => void download(artifact.kind)}
            className="ui-button-ghost inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[14px] font-medium disabled:cursor-not-allowed disabled:opacity-60"
          >
            <svg
              viewBox="0 0 24 24"
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M12 4v11" />
              <path d="m7.5 11 4.5 4.5L16.5 11" />
              <path d="M5 20h14" />
            </svg>
            {pending === artifact.kind
              ? t("내려받는 중…", "Downloading…", "ダウンロード中…")
              : artifact.label(t)}
          </button>
        ))}
      </div>
      {failure && (
        <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
          <p className="font-semibold">
            {t("내려받지 못했습니다", "The download did not happen", "ダウンロードできませんでした")} (
            {failure.kind})
          </p>
          <p className="mt-1 leading-relaxed">{failure.message}</p>
        </div>
      )}
    </div>
  );
}

/** One titled block, full width -- used for 생성 비교 below the map/rail split.
 * "your call, named" (this unit's own brief): the comparison table does not
 * fit a 35%-wide rail without recreating the exact cramped column this unit
 * exists to fix, so it stays full width, under the split, like the other
 * things this page cannot fit two-up. */
function DrawerSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-slate-200 pt-6">
      <h3 className="text-[15px] font-semibold text-slate-900">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GenesisJobDetailPage() {
  const { lang, t } = useLanguage();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const jobId = typeof params?.id === "string" ? params.id : "";

  // Sibling list, for the comparison panel's own "compare against" picker
  // only -- best-effort, the same discipline the hub's side-input listing
  // uses (a failed fetch here must not block the rest of this page).
  const [jobs, setJobs] = useState<GenesisJob[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchGenesisJobs()
      .then((entries) => {
        if (!cancelled) setJobs(entries);
      })
      .catch(() => {
        if (!cancelled) setJobs([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // THE STALE-FLASH GUARD -- see this file's header comment. `selected` is
  // read back only when the cached entry's own id still matches the URL's
  // `jobId`, exactly the discipline the old drawer used for the same reason.
  const [selectedEntry, setSelectedEntry] = useState<{ id: string; record: GenesisJob } | null>(null);
  const [notFoundId, setNotFoundId] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const selected = selectedEntry && selectedEntry.id === jobId ? selectedEntry.record : null;
  const notFound = notFoundId === jobId;

  const [reportEntry, setReportEntry] = useState<
    { jobId: string; language: string; payload: GenesisGateReport } | null
  >(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [fixesEntry, setFixesEntry] = useState<
    { jobId: string; checkedAt: string; payload: GenesisJobFixes } | null
  >(null);
  const [fixesError, setFixesError] = useState<GenesisError | Error | null>(null);

  const report =
    reportEntry && selected && reportEntry.jobId === selected.id && reportEntry.language === lang
      ? reportEntry.payload
      : null;
  const fixes =
    fixesEntry && selected && fixesEntry.jobId === selected.id && fixesEntry.checkedAt === (selected.gate?.checked_at ?? "")
      ? fixesEntry.payload
      : null;

  // Poll the job named by the URL. A tick that never once resolved for this
  // id names it not-found and stops (see header comment); once a job HAS
  // shown here, a later poll failure keeps the last-known record on screen
  // and retries, rather than flashing the page to an empty state over one
  // dropped request.
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let haveJob = false;

    const tick = async () => {
      try {
        const record = await fetchGenesisJob(jobId);
        if (cancelled) return;
        haveJob = true;
        setSelectedEntry({ id: jobId, record });
        setNotFoundId(null);
        setPollError(null);
        timer = setTimeout(
          () => void tick(),
          isTerminal(record.status) ? TERMINAL_RECHECK_MS : POLL_INTERVAL_MS,
        );
      } catch (caught) {
        if (cancelled) return;
        if (!haveJob) {
          setNotFoundId(jobId);
          return;
        }
        setPollError(caught instanceof Error ? caught.message : String(caught));
        timer = setTimeout(() => void tick(), TERMINAL_RECHECK_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  // The gate report -- read from the STORED verdict endpoint, one fetch per
  // (job, verdict, language). `checked_at` is in the key because a re-gate
  // (live-check evidence) changes the verdict without changing the job id.
  const gatedJobId = selected?.gate ? selected.id : null;
  const gateCheckedAt = selected?.gate?.checked_at ?? null;
  useEffect(() => {
    if (!gatedJobId) return;
    let cancelled = false;
    fetchGenesisGateReport(gatedJobId, lang)
      .then((payload) => {
        if (!cancelled) {
          setReportEntry({ jobId: gatedJobId, language: lang, payload });
          setReportError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setReportError(caught instanceof Error ? caught.message : String(caught));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gatedJobId, gateCheckedAt, lang]);

  // The fix strip's own data -- one fetch per (job, verdict), WITHOUT
  // `language` (the backend's rationale already comes back trilingual).
  // Deliberately a separate effect: `GET .../fixes` is not open to an
  // anonymous caller the way `GET .../gate` is, so the two fail independently.
  useEffect(() => {
    if (!gatedJobId) return;
    let cancelled = false;
    setFixesError(null);
    fetchGenesisJobFixes(gatedJobId)
      .then((payload) => {
        if (!cancelled) {
          setFixesEntry({ jobId: gatedJobId, checkedAt: gateCheckedAt ?? "", payload });
          setFixesError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setFixesError(caught instanceof Error ? caught : new Error(String(caught)));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gatedJobId, gateCheckedAt]);

  // On a 202 from POST .../fixes/apply: the sibling job now HAS its own URL,
  // so approving a fix navigates there instead of re-pointing an in-page
  // selection at it -- the lineage line the sibling's own top bar renders is
  // what makes that navigation legible as "the treated version of this one".
  const handleFixApplied = useCallback(
    (appliedJob: GenesisJob) => {
      router.push(`/genesis/jobs/${appliedJob.id}`);
    },
    [router],
  );

  // UNIT #20's help drawer is RETIRED (2026-08-14). 판정 게이트/잠정 등록/지도
  // have now moved a third time and stopped moving: they are verbatim in
  // lib/help-content.tsx (`jobHelpEntries`) and render at /docs, opened by the
  // book icon in the app topbar. Their journey (inline ⓘ on the hub → the hub's
  // drawer → this route's drawer → /docs) is exactly why they ended up in one
  // file: doctrine that follows a control around gets rewritten every time it
  // moves, and these sentences are the product's own claims.

  const backLink = (
    <Link
      href="/genesis"
      className="ui-button-ghost inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-[15px]"
    >
      ← {t("생성 이력", "Generations", "生成履歴")}
    </Link>
  );

  // UNKNOWN/FOREIGN ID -- named empty state, never a crash.
  if (notFound) {
    return (
      <div className="mx-auto max-w-[1500px] px-4 py-8">
        {backLink}
        <div className="mt-6 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
          <p className="text-[15px] text-slate-600">
            {t(
              "이 생성을 찾을 수 없습니다 — 삭제되었거나 다른 프로젝트의 생성일 수 있습니다.",
              "This generation could not be found — it may have been deleted, or it belongs to a different project.",
              "この生成が見つかりません — 削除されたか、別のプロジェクトの生成である可能性があります。",
            )}
          </p>
        </div>
      </div>
    );
  }

  // COLD-LOAD LOADING STATE -- the first tick has not resolved either way yet.
  if (!selected) {
    return (
      <div className="mx-auto max-w-[1500px] px-4 py-8">
        {backLink}
        <p className="mt-6 text-[15px] text-slate-500">{t("불러오는 중…", "Loading…", "読み込み中…")}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-8">
      {backLink}

      {/* TOP BAR -- name + verdict chip + 판정 stat counts + mono sha line +
        * lineage (fix jobs only) + artifacts, compacted to the right. Nothing
        * here re-spells the title (#13's de-duplication rule stands). */}
      <div className="mt-4 flex flex-col gap-4 border-b border-slate-200 pb-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-[20px] font-semibold text-white">{selected.name}</h1>
            <span
              className={`shrink-0 whitespace-nowrap rounded-full border px-2 py-0.5 text-[14px] font-semibold ${statusClasses(selected.status)}`}
            >
              {statusLabel(selected.status, t)}
            </span>
          </div>
          {/* The date·scale meta and the sha line left the screen (user, live
            * pass 2026-08-14) -- both stay in gate.json and the compliance
            * report; the history table still carries date and scale. */}
          {selected.source_osm.filename !== `${selected.name}.osm` && (
            <p className="mt-1 truncate font-mono text-[14px] text-slate-500">
              {selected.source_osm.filename}
            </p>
          )}
          {selected.derived_from && (
            <p className="mt-1 truncate font-mono text-[14px] text-slate-500">
              {t("파생", "Derived from", "派生")} — {selected.derived_from.job_id} ·{" "}
              {selected.derived_from.finding_code} · {selected.derived_from.approved_by ?? "—"}
            </p>
          )}
          {report && (
            <div className="mt-3 flex flex-wrap gap-x-8 gap-y-3">
              <Stat label={t("통과", "pass", "合格")} value={report.counts.pass} tone="text-emerald-800" />
              <Stat
                label={t("실패", "fail", "不合格")}
                value={report.counts.fail}
                tone={report.counts.fail > 0 ? "text-rose-800" : undefined}
              />
              <Stat
                label={t("확인 불가", "not checkable", "確認不可")}
                value={report.counts.not_checkable}
                tone={
                  report.counts.not_checkable > 0
                    ? "text-slate-900 underline decoration-dotted decoration-slate-400 underline-offset-4"
                    : undefined
                }
              />
            </div>
          )}
          {pollError && (
            <p className="mt-2 text-[14px] text-slate-500">
              {t(
                "최신 상태를 다시 불러오지 못했습니다 — 마지막으로 확인된 상태를 보여줍니다.",
                "Could not refresh this job's status — showing the last state that loaded.",
                "最新の状態を再取得できませんでした — 最後に確認できた状態を表示しています。",
              )}
            </p>
          )}
        </div>

        {selected.artifact && (
          <div className="shrink-0 lg:text-right">
            <p className="text-[14px] text-slate-500">{t("아티팩트", "Artifacts", "アーティファクト")}</p>
            <div className="mt-1 flex flex-wrap gap-2 lg:justify-end">
              <ArtifactRow jobId={selected.id} t={t} />
            </div>
          </div>
        )}
      </div>

      {/* 판정 -- the merged verdict headline, progress/provisional/refused/
        * revocation/refusal paragraphs, and the live-check attestation. The
        * V1–V9 GRID itself moves into the right rail below, beside the split. */}
      <section className="mt-6">
        <h2 className="flex items-center text-[15px] font-semibold text-slate-900">
          {t("판정", "Verdict", "判定")}
        </h2>

        <div className="mt-3">
          <p className="text-[15px] font-semibold text-slate-900">
            {report ? verdictLabel(report.verdict, t) : statusLabel(selected.status, t)}
          </p>

          {!isTerminal(selected.status) && (
            <p className="mt-2 text-[15px] text-slate-600">
              {t("진행", "Phase", "進行")}: {selected.phase} ({Math.round(selected.progress * 100)}%)
            </p>
          )}

          {selected.status === "provisional" && (
            <p className="mt-2 text-[15px] leading-relaxed text-slate-600">
              {t(
                "등록되었지만 잠정입니다 — V1은 라이브 검사 증거를 기다립니다.",
                "Registered, but provisional — V1 is waiting for live-check evidence.",
                "登録されましたが暫定です — V1 はライブ検査の証拠を待っています。",
              )}
            </p>
          )}
          {/* The refused-consequence paragraph left the screen (user, live
            * pass 2026-08-14) -- the verdict headline and chip already say
            * refused; the not-registered consequence reads in the 판정 게이트
            * help entry below. */}
          {selected.revocation && (
            <p className="mt-2 text-[15px] leading-relaxed text-slate-600">
              {selected.revocation.removed_from_workspace
                ? t(
                    "라이브 증거가 들어온 뒤 게이트가 이 네트워크를 거부하여, 프로젝트에서 등록을 해제했습니다.",
                    "After live evidence arrived the gate refused this network, so its registration was removed from the project.",
                    "ライブ証拠が届いた後にゲートがこのネットワークを拒否したため、プロジェクトから登録を解除しました。",
                  )
                : t(
                    "게이트가 이 네트워크를 거부했습니다. 프로젝트의 현재 데이터셋은 이미 다른 파일이므로 아무것도 삭제하지 않았습니다.",
                    "The gate refused this network. The project's active dataset is no longer this artifact, so nothing was deleted.",
                    "ゲートがこのネットワークを拒否しました。プロジェクトの現在のデータセットは既に別のファイルのため、何も削除していません。",
                  )}
            </p>
          )}
          {selected.refusal && (
            <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
              <p className="font-semibold">
                {refusalHeadline(selected.refusal.code, t, generationRefusalFallback(t))}
              </p>
              <ul className="mt-1 space-y-1">
                {selected.refusal.reasons.map((reason, index) => (
                  <li key={index} className="font-mono text-[14px] leading-relaxed">
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {selected.gate?.live_evidence && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3 text-[15px] text-slate-700">
            {/* "Attestation", never "proof". The state.json arrives over
              * HTTP, so this app cannot establish that Vissim ran — it can
              * only record who says it did. */}
            <p className="font-semibold text-slate-800">
              {t(
                "라이브 검사 증언 (운영자 진술)",
                "Live-check attestation (operator's statement)",
                "ライブ検査の証言（運用者の申告）",
              )}
            </p>
            <p className="mt-1">
              {selected.gate.live_evidence.label} · {selected.gate.live_evidence.network} ·{" "}
              {selected.gate.live_evidence.vissim_version ??
                t("버전 미기록", "version not recorded", "バージョン未記録")}
            </p>
            <p className="mt-1">
              {selected.gate.live_evidence.admitted
                ? t("채택됨 (동일 파일 확인)", "Admitted (same file proven)", "採用（同一ファイル確認）")
                : t(
                    "채택되지 않음 — 이 증거는 다른 파일에 대한 것입니다",
                    "Not admitted — this evidence is about a different file",
                    "採用されず — この証拠は別のファイルに関するものです",
                  )}
              {selected.gate.live_evidence.gap_codes.length > 0 &&
                ` (${selected.gate.live_evidence.gap_codes.join(", ")})`}
            </p>
            {selected.gate.live_evidence.attested_by && (
              <p className="mt-1">
                {t("증언자", "Attested by", "申告者")}:{" "}
                <span className="font-semibold">{selected.gate.live_evidence.attested_by}</span>
                {selected.gate.live_evidence.attested_at &&
                  ` · ${formatTimestamp(selected.gate.live_evidence.attested_at, lang)}`}
              </p>
            )}
            {selected.gate.rests_on_attestation && (
              <p className="mt-2 rounded border-2 border-slate-400 bg-slate-100 px-2 py-1 text-slate-900">
                {t(
                  "이 판정의 V1은 운영자 진술에 근거합니다 — 이 앱이 Vissim 로딩을 직접 관측한 것이 아닙니다.",
                  "V1 in this verdict rests on an operator's statement — this app did not observe the Vissim load itself.",
                  "この判定のV1は運用者の申告に基づきます — このアプリがVissimの読み込みを直接観測したわけではありません。",
                )}
              </p>
            )}
          </div>
        )}

        {reportError && (
          <div className="mt-4 rounded-lg border-2 border-slate-400 bg-slate-100 px-3 py-2 text-[15px] text-slate-900">
            {t("게이트 결과를 불러오지 못했습니다", "Failed to load the gate result", "ゲート結果を読み込めませんでした")}
            : {reportError}
          </div>
        )}
      </section>

      {/* THE 65/35 SPLIT (mockup option D, user decision 2026-08-14): map
        * LEFT wide, V1–V9 grid + selected finding + fix card RIGHT narrow --
        * inverted from the old drawer's map-under-the-grid stack, so a click
        * on a finding and the map location it names stay on screen together. */}
      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-[65fr_35fr] xl:items-start mb-8">
        <div className="min-w-0">
          {report ? (
            <GenesisTileMap jobId={selected.id} report={report} hasArtifact={Boolean(selected.artifact)} />
          ) : (
            <div className="rounded-xl border border-slate-200 bg-white/[0.03] px-6 py-10 text-center">
              <p className="text-[15px] text-slate-500">
                {t(
                  "아직 판정이 없어 지도를 표시할 수 없습니다.",
                  "No verdict yet, so the map cannot be shown.",
                  "まだ判定がないため、地図を表示できません。",
                )}
              </p>
            </div>
          )}
        </div>

        <div className="min-w-0">
          {report ? (
            <GateCheckGrid
              key={`${selected.id}-${gateCheckedAt ?? ""}`}
              checks={report.checks}
              t={t}
              lang={lang}
              jobId={selected.id}
              fixes={fixes}
              fixesError={fixesError}
              onFixApplied={handleFixApplied}
            />
          ) : (
            <p className="text-[15px] text-slate-500">
              {t("아직 판정이 없습니다.", "No verdict yet.", "まだ判定がありません。")}
            </p>
          )}
        </div>
      </div>

      {/* 생성 비교 -- full width under the split; see DrawerSection's own
        * comment for why it does not try to fit the 35% rail. */}
      <DrawerSection title={t("생성 비교", "Compare generations", "生成の比較")}>
        <GenesisComparisonPanel
          jobs={jobs}
          currentJob={selected}
          currentReport={report}
          currentReportError={reportError}
        />
      </DrawerSection>
    </div>
  );
}

"use client";

// ---------------------------------------------------------------------------
// The 13-step calibration flow rail (unit #50) — the workspace's skeleton.
//
// MRWA op §2.12.1 names thirteen steps in a fixed order and this app's
// calibration work was, until now, scattered across four surfaces with no way
// to see WHERE in that order a project stood. This rail is that view, and it
// is a READER: every status arrives from `GET /api/calibration/rail`, derived
// server-side in `services/calibration_rail.py` from artifacts other units
// produced. This component picks a colour and a sentence; it decides nothing.
//
// THE ONE RULE IT RENDERS. Seven of the thirteen steps have no evidence source
// in this product, and they say "증거 없음 — 수동 확인 영역" forever. A rail
// showing thirteen ticks on an uncalibrated network would be a claim nobody
// measured, which is the failure this whole codebase is arranged against.
// So NO_EVIDENCE is styled as a real, readable state — not as a greyed-out
// "coming soon" — and it is the only state a step can be in without a named
// artifact behind it.
//
// THREE-COLOUR RULE (dashboard-contract.test.ts's "U-1: three accent colours"):
// rose = fail, emerald = pass, sky = selection/proposal, and everything else
// rides the neutral ink ramp. A step is never amber. So: DONE is emerald,
// NEXT is sky (it is the proposal — "do this one"), and IN_PROGRESS,
// NOT_APPLICABLE and NO_EVIDENCE are told apart by WEIGHT and hairline on the
// neutral ramp, never by a fourth hue.
// ---------------------------------------------------------------------------

import { useLanguage } from "@/components/LanguageProvider";
import type {
  CalibrationRail,
  CalibrationRailStep,
  CalibrationRailTrack,
  CalibrationStepStatus,
} from "@/lib/api";

type Translate = (ko: string, en: string, ja?: string) => string;

const STATUS_LABELS: Record<CalibrationStepStatus, [string, string, string]> = {
  DONE: ["완료", "Done", "完了"],
  IN_PROGRESS: ["진행", "In progress", "進行中"],
  NEXT: ["다음 할 일", "Next", "次の作業"],
  NOT_APPLICABLE: ["해당 없음", "Not applicable", "該当なし"],
  NO_EVIDENCE: ["증거 없음", "No evidence", "証拠なし"],
};

// Neutral for three of the five, on purpose — see the header's colour note.
const STATUS_CHIP: Record<CalibrationStepStatus, string> = {
  DONE: "border-emerald-200 bg-emerald-50 text-emerald-700",
  IN_PROGRESS: "border-slate-400 bg-slate-100 text-slate-800",
  NEXT: "border-sky-300 bg-sky-50 text-sky-700",
  NOT_APPLICABLE: "border-slate-200 bg-slate-50 text-slate-500",
  NO_EVIDENCE: "border-slate-300 bg-surface text-slate-600",
};

function statusLabel(status: CalibrationStepStatus, t: Translate): string {
  const entry = STATUS_LABELS[status];
  return entry ? t(entry[0], entry[1], entry[2]) : status;
}

function stepLabel(step: CalibrationRailStep, lang: string): string {
  if (lang === "ko") return step.label_ko;
  if (lang === "ja") return step.label_ja;
  return step.label;
}

function pick(text: { ko: string; en: string; ja: string } | undefined, lang: string): string {
  if (!text) return "";
  if (lang === "ko") return text.ko;
  if (lang === "ja") return text.ja;
  return text.en;
}

// ---------------------------------------------------------------------------
// Unit #51's step-⑫ track. Two states, and the LOCKED one is the important one.
//
// A lock that says only "locked" teaches nothing. The server sends the
// precondition gate's own named refusals, each with what would open it, so this
// renders the LIST — a modeller reads which door to open instead of learning
// that a door exists. It is the same ladder grammar the #43 diagnosis uses.
//
// Unlocked, this shows the ask-first card and NO start button. That is not an
// omission: the search needs live Vissim COM, which belongs to the job queue on
// the user's own machine, and `card.runner.wired` is false in this build. A
// button that could not do what it says would be worse than no button, so the
// card states the runner's real state instead of implying one.
//
// Three-colour rule (see the header): the open track is sky, because it is a
// proposal — exactly what NEXT is. The locked one stays on the neutral ramp.
// ---------------------------------------------------------------------------
export function ParameterSearchTrack({
  track,
  lang,
  t,
}: {
  track: CalibrationRailTrack;
  lang: string;
  t: Translate;
}) {
  const card = track.card;
  const refusals = track.refusals ?? [];
  if (track.locked || !card) {
    return (
      <span className="mt-1 block rounded-md border border-dashed border-slate-300 bg-slate-50 px-2 py-1 text-[13px] leading-5 text-slate-500">
        <span className="font-semibold">
          {pick(track.label, lang)}
          <span className="ml-1 font-mono font-normal">{track.unit}</span>
          <span className="ml-1.5 font-normal">{t("잠김", "locked", "ロック中")}</span>
        </span>
        <span className="mt-0.5 block">{pick(track.why, lang)}</span>
        <span className="mt-1 block border-l border-slate-300 pl-2">
          {refusals.map((refusal) => (
            <span key={refusal.code} className="mt-0.5 block">
              <span className="font-mono text-[13px] text-slate-400">{refusal.code}</span>{" "}
              {pick(refusal.why, lang)}{" "}
              <span className="text-slate-600">{pick(refusal.opens_when, lang)}</span>
            </span>
          ))}
        </span>
      </span>
    );
  }
  return (
    <span className="mt-1 block rounded-md border border-sky-300 bg-sky-50/60 px-2 py-1 text-[13px] leading-5 text-slate-700">
      <span className="font-semibold text-slate-800">
        {pick(track.label, lang)}
        <span className="ml-1 font-mono font-normal text-slate-500">{track.unit}</span>
      </span>
      <span className="mt-0.5 block font-semibold">{pick(card.question, lang)}</span>
      <span className="mt-0.5 block">{pick(card.because, lang)}</span>
      <span className="mt-1 block font-mono text-[13px] text-slate-500">
        {card.plan.space.map((band) => `${band.parameter} ${band.low}–${band.high}`).join(" · ")}
        {card.plan.space[0] ? ` · ${card.plan.space[0].citation}` : ""}
      </span>
      <span className="mt-0.5 block font-mono text-[13px] text-slate-500">
        {t("보정", "calibrate", "補正")} {card.plan.calibration_nodes.join(",")} ·{" "}
        {t("홀드아웃", "holdout", "ホールドアウト")} {card.plan.holdout_nodes.join(",")} ·{" "}
        {Math.round(card.plan.budget_s / 60)}
        {t("분", "min", "分")}
        {card.plan.resumable ? t(" (이어하기 가능)", " (resumable)", " (再開可)") : ""}
      </span>
      {/* The honest half. Never hidden behind a disclosure. */}
      <span className="mt-1 block border-l border-sky-300 pl-2 text-slate-600">
        {card.runner.wired
          ? t(
              "실행기가 연결되어 있습니다.",
              "A runner is wired.",
              "実行機が接続されています。",
            )
          : t(
              "이 빌드에는 실행기가 연결되어 있지 않습니다 — 탐색 실행은 Vissim COM이 필요해 잡 큐(사용자 실행 환경)의 몫입니다. 이 카드는 시작 여부를 묻기만 합니다.",
              "No runner is wired in this build — running a search needs Vissim COM and belongs to the job queue on your own machine. This card only asks whether to start one.",
              "このビルドには実行機が接続されていません — 探索の実行にはVissim COMが必要で、ジョブキュー(ユーザーの実行環境)の担当です。このカードは開始の可否を尋ねるだけです。",
            )}
      </span>
    </span>
  );
}

export default function FlowRail({
  rail,
  loading,
  error,
  activeStep,
  onSelectStep,
}: {
  rail: CalibrationRail | null;
  loading: boolean;
  error: string | null;
  activeStep: number | null;
  /** Clicking a step scrolls to the work area that step's evidence comes from
   * — the rail is a control as well as a board. The anchor is the server's
   * (`step.anchor`), so a step can never point at a section that moved. */
  onSelectStep: (step: CalibrationRailStep) => void;
}) {
  const { t, lang } = useLanguage();

  if (error) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[14px] leading-6 text-rose-700">
        {t("보정 흐름을 불러오지 못했습니다:", "The calibration flow could not be loaded:", "補正フローを読み込めませんでした:")}{" "}
        {error}
      </div>
    );
  }

  if (!rail) {
    return (
      <div className="rounded-xl border border-slate-200 bg-surface px-4 py-3 text-[14px] text-slate-500">
        {loading
          ? t("보정 흐름을 읽는 중...", "Reading the calibration flow...", "補正フローを読み込み中...")
          : t("보정 흐름 정보가 없습니다.", "No calibration flow information.", "補正フロー情報がありません。")}
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-surface px-4 py-3.5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[16px] font-bold text-slate-900">
          {t("보정 13단계", "The 13 calibration steps", "補正13段階")}
          <span className="ml-2 font-mono text-[13px] font-normal text-slate-500">{rail.citation}</span>
        </h2>
        <p className="font-mono text-[13px] text-slate-500">
          {(["DONE", "IN_PROGRESS", "NEXT", "NOT_APPLICABLE", "NO_EVIDENCE"] as CalibrationStepStatus[])
            .filter((status) => (rail.counts[status] ?? 0) > 0)
            .map((status) => `${statusLabel(status, t)} ${rail.counts[status]}`)
            .join(" · ")}
        </p>
      </header>

      {/* The honest headline. A reader must not have to count chips to learn
          that this product measures nothing for seven of the thirteen steps. */}
      <p className="mt-1.5 text-[13px] leading-5 text-slate-600">
        {t(
          "각 단계의 상태는 이 워크스페이스에 실제로 있는 증거에서만 유도합니다. 증거가 없는 단계는 완료로 표시하지 않고 '증거 없음 — 수동 확인 영역'으로 둡니다.",
          "Each step's status is derived only from evidence this workspace actually holds. A step with no evidence is never shown as done — it stays a manual-check area.",
          "各段階の状態は、このワークスペースに実在する証拠からのみ導出します。証拠のない段階は完了とせず「証拠なし — 手動確認領域」とします。",
        )}
      </p>

      <ol className="mt-3 grid gap-1.5 lg:grid-cols-2">
        {rail.steps.map((step) => {
          const active = activeStep === step.number;
          return (
            <li key={step.number}>
              <button
                type="button"
                onClick={() => onSelectStep(step)}
                className={`flex w-full items-start gap-2.5 rounded-lg border px-2.5 py-2 text-left transition ${
                  active ? "border-sky-300 bg-sky-50/60" : "border-slate-200 bg-surface hover:bg-slate-50"
                }`}
              >
                <span className="mt-0.5 w-6 shrink-0 text-center font-mono text-[13px] font-semibold text-slate-500">
                  {step.number}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[14px] font-semibold text-slate-800">{stepLabel(step, lang)}</span>
                    <span
                      className={`rounded-full border px-1.5 py-0.5 text-[13px] font-semibold ${STATUS_CHIP[step.status]}`}
                    >
                      {statusLabel(step.status, t)}
                    </span>
                    {step.manual_area && (
                      <span className="text-[13px] text-slate-500">
                        {t("수동 확인 영역", "manual-check area", "手動確認領域")}
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-[13px] leading-5 text-slate-600">{pick(step.basis, lang)}</span>
                  {step.tracks?.map((track) => (
                    <ParameterSearchTrack key={track.key} track={track} lang={lang} t={t} />
                  ))}
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      {/* §2.12.1's own ordering principles. They bind anyone who cites the step
          order, so the rail that cites it shows them rather than leaving a
          reader to assume the order is the whole rule. */}
      <details className="mt-3 text-[13px] leading-5 text-slate-600">
        <summary className="cursor-pointer list-none text-[13px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (지침의 순서 원칙)", "More (the guideline's ordering principles)", "詳細（指針の順序原則）")}
        </summary>
        <ul className="mt-1.5 flex flex-col gap-1 border-l border-slate-300 pl-3">
          {rail.principles.map((principle) => (
            <li key={principle.citation + principle.text}>
              {principle.text}
              <span className="ml-1.5 font-mono text-[13px] text-slate-400">{principle.citation}</span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

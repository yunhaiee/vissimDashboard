"use client";

// ---------------------------------------------------------------------------
// The calibration flow rail — the workspace's permanent left NAVIGATION
// (unit #50, restructured by #50-b).
//
// MRWA op §2.12.1 names thirteen steps in a fixed order and this app's
// calibration work was, until #50, scattered across four surfaces with no way
// to see WHERE in that order a project stood. This rail is that view, and it
// is a READER: every status arrives from `GET /api/calibration/rail`, derived
// server-side in `services/calibration_rail.py` from artifacts other units
// produced. This component picks a colour and a sentence; it decides nothing.
//
// WHAT #50-b CHANGED, and why. The user drove #50's real screen and could not
// read it: "스테이션이 전부 세로로 쌓여 페이지 구조·작업 구조를 파악할 수 없다".
// Thirteen equal rows over five stacked sections meant the rail was a list to
// scroll past, not a place to stand. So:
//
//   * thirteen rows fold into EIGHT station groups (lib/calibrationStations.ts
//     owns the mapping and is tested directly), with 수동 확인 영역's six steps
//     behind one expandable row;
//   * a click SWITCHES the station shown on the right — it is not a scroll
//     jump any more, so nothing below the fold can be mistaken for gone;
//   * each row carries its steps' status chips and a sky badge counting the
//     proposals waiting at that station, so "where is there work" is answered
//     without opening anything.
//
// The per-step BASIS sentence moved to the station panel with the rest of that
// step's evidence (app/calibration/page.tsx). A rail row states where a step is
// and what state it is in; the sentence that justifies the state belongs beside
// the evidence that produced it.
//
// THE ONE RULE IT RENDERS. Six of the thirteen steps have no evidence source in
// this product, and they say "증거 없음 — 수동 확인 영역" forever. A rail
// showing thirteen ticks on an uncalibrated network would be a claim nobody
// measured, which is the failure this whole codebase is arranged against.
// So NO_EVIDENCE is styled as a real, readable state — not as a greyed-out
// "coming soon" — and it is the only state a step can be in without a named
// artifact behind it. Folding those six into one row does NOT hide them: the
// row states the count in the same status vocabulary, and opens.
//
// THREE-COLOUR RULE (dashboard-contract.test.ts's "U-1: three accent colours"):
// rose = fail, emerald = pass, sky = selection/proposal, and everything else
// rides the neutral ink ramp. A step is never amber. So: DONE is emerald,
// NEXT is sky (it is the proposal — "do this one"), and IN_PROGRESS,
// NOT_APPLICABLE and NO_EVIDENCE are told apart by WEIGHT and hairline on the
// neutral ramp, never by a fourth hue. The proposal badge is sky for the same
// reason the NEXT chip is.
//
// WHAT #50-c CHANGED, and why (the user, 2026-08-20, driving the real screen):
// "거의 모든 fine text는 크게/직관적 대안/없애" and, on the mono provenance
// lines specifically, "이런것도 다 지워 — 화면을 포화시켜". So this rail is now
// NUMBERS FIRST, PROSE FOLDED:
//
//   * the status tally was a mono SENTENCE ("완료 1 · 진행 2 · …") and is now
//     COUNT CHIPS in the same three-colour vocabulary the step chips use — the
//     tally is a set of numbers, and a number reads as a number;
//   * the honest headline (a step with no evidence is never shown as done) is
//     doctrine, so it moved into the header's ⓘ, verbatim and keyboard-reachable
//     — the rule the app already follows for a doctrine sentence beside the
//     control it explains (components/genesis/ui.tsx's InfoTip);
//   * §2.12.1's citation and the refusal CODES moved inside the folds that
//     already carry the ordering principles. Nothing is deleted: a fold keeps
//     the citation string verbatim, because it is audit evidence.
//
// The type floor is 14px on the surface; 13px survives only INSIDE a fold.
// ---------------------------------------------------------------------------

import { useState } from "react";

import { InfoTip } from "@/components/genesis/ui";

// Unit #51-c: the guardrail's two-path line and the decision chips are the SAME
// components the approval card uses. Imported rather than re-written for the
// reason StationProposals states about the card itself — a second copy of a
// sentence a human acts on is a second place for it to drift, and this one is
// the user's own recorded decision.
import { GuardrailPathLines, GuardrailResolutionChips } from "@/components/agent/ApprovalCards";
import { useLanguage } from "@/components/LanguageProvider";
import type {
  CalibrationRail,
  CalibrationRailStep,
  CalibrationRailTrack,
  CalibrationStepStatus,
} from "@/lib/api";
import {
  CALIBRATION_STATIONS,
  STATION_LABELS,
  stationRowsShown,
  type CalibrationStationId,
  type GroupedSteps,
} from "@/lib/calibrationStations";

type Translate = (ko: string, en: string, ja?: string) => string;

const STATUS_LABELS: Record<CalibrationStepStatus, [string, string, string]> = {
  DONE: ["완료", "Done", "完了"],
  IN_PROGRESS: ["진행", "In progress", "進行中"],
  NEXT: ["다음 할 일", "Next", "次の作業"],
  NOT_APPLICABLE: ["해당 없음", "Not applicable", "該当なし"],
  NO_EVIDENCE: ["증거 없음", "No evidence", "証拠なし"],
};

/** The order every status tally is printed in — the server's own. */
const STATUS_ORDER: CalibrationStepStatus[] = [
  "DONE",
  "IN_PROGRESS",
  "NEXT",
  "NOT_APPLICABLE",
  "NO_EVIDENCE",
];

// Neutral for three of the five, on purpose — see the header's colour note.
const STATUS_CHIP: Record<CalibrationStepStatus, string> = {
  DONE: "border-emerald-200 bg-emerald-50 text-emerald-700",
  IN_PROGRESS: "border-slate-400 bg-slate-100 text-slate-800",
  // `text-sky-600`, not 700: globals.css's dark `@theme` remaps sky-50 to a NAVY
  // (#032042) but only remaps sky-400/500/600 on the text side, so a `text-sky-700`
  // here keeps Tailwind's stock light-mode #0369a1 and lands dark-blue-on-navy —
  // the blue-on-blue trap the genesis pages already had to fix. 600 is remapped
  // (#6da7ec) and is the app's own "navy fill, sky text" badge idiom.
  NEXT: "border-sky-300 bg-sky-50 text-sky-600",
  NOT_APPLICABLE: "border-slate-200 bg-slate-50 text-slate-500",
  NO_EVIDENCE: "border-slate-300 bg-surface text-slate-600",
};

/** ①-⑬, the numbering this project's own documents write §2.12.1's steps with
 * (geh_ladder.py's `_CIRCLED`). Pure formatting of `step.number` — a step this
 * table cannot render falls back to the plain digits rather than to nothing. */
const CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬";

export function circledStep(number: number): string {
  return CIRCLED[number - 1] ?? String(number);
}

export function statusLabel(status: CalibrationStepStatus, t: Translate): string {
  const entry = STATUS_LABELS[status];
  return entry ? t(entry[0], entry[1], entry[2]) : status;
}

export function statusChipClass(status: CalibrationStepStatus): string {
  return STATUS_CHIP[status] ?? STATUS_CHIP.NO_EVIDENCE;
}

export function stepLabel(step: CalibrationRailStep, lang: string): string {
  if (lang === "ko") return step.label_ko;
  if (lang === "ja") return step.label_ja;
  return step.label;
}

export function stationLabel(station: CalibrationStationId, t: Translate): string {
  const entry = STATION_LABELS[station];
  return entry ? t(entry[0], entry[1], entry[2]) : station;
}

/** The server's own localized text, picked — never composed here. */
export function pick(text: { ko: string; en: string; ja: string } | undefined | null, lang: string): string {
  if (!text) return "";
  if (lang === "ko") return text.ko;
  if (lang === "ja") return text.ja;
  return text.en;
}

/** How many steps of a group are in each status, in the server's order.
 *
 * Counting is NOT deciding: every word here is one the server already said
 * about one of these steps. The collapsed 수동 확인 영역 row prints this so it
 * states its six steps' real state instead of a "6 steps" that hides it. */
function statusTally(steps: CalibrationRailStep[]): { status: CalibrationStepStatus; count: number }[] {
  return STATUS_ORDER.map((status) => ({
    status,
    count: steps.filter((step) => step.status === status).length,
  })).filter((entry) => entry.count > 0);
}

/** The sky dot-and-number a rail row wears when proposals wait at its station.
 *
 * Sky because a proposal IS the "do this one" the NEXT chip is (three-colour
 * rule). Zero renders nothing at all — a "0" badge is visual noise that reads
 * as a state rather than as an absence. */
function ProposalBadge({ count, t }: { count: number; t: Translate }) {
  if (count <= 0) return null;
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-sky-300 bg-sky-50 px-1.5 py-0.5 text-[14px] font-semibold text-sky-600"
      title={t("승인 대기 중인 제안", "Proposals awaiting approval", "承認待ちの提案")}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-sky-500" aria-hidden="true" />
      {count}
    </span>
  );
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
// #50-b moved where it is RENDERED (the ⑫ station panel, beside the Table 5-6
// evidence it acts on) and changed nothing about what it says. It stays
// exported from the rail's module because the rail is where the step-⑫ payload
// is described; one component, one payload, no second copy of the lock.
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
      <div className="mt-1 rounded-md border border-dashed border-slate-300 bg-slate-50 px-2.5 py-2 text-[14px] leading-6 text-slate-600">
        <p className="font-semibold text-slate-700">
          {pick(track.label, lang)}
          <span className="ml-1 font-mono text-[14px] font-normal text-slate-500">{track.unit}</span>
          <span className="ml-1.5 font-normal">{t("잠김", "locked", "ロック中")}</span>
        </p>
        <p className="mt-0.5">{pick(track.why, lang)}</p>
        {/* Every named refusal, in the reader's language. #50-c moved the CODE
            beside it into the fold below: a reader acts on "what is wrong and
            what would open it", and the machine code saturated the line without
            adding a single actionable word. It is still on the screen, one
            click away, because it is what an auditor greps for. */}
        <ul className="mt-1 flex flex-col gap-1 border-l border-slate-300 pl-2">
          {refusals.map((refusal) => (
            <li key={refusal.code}>
              {pick(refusal.why, lang)} <span className="text-slate-500">{pick(refusal.opens_when, lang)}</span>
            </li>
          ))}
        </ul>
        {refusals.length > 0 && (
          <details className="mt-1 text-[13px] leading-5 text-slate-500">
            <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
              {t("자세히 (거부 코드)", "More (the refusal codes)", "詳細（拒否コード）")}
            </summary>
            <p className="mt-1 border-l border-slate-300 pl-2 font-mono text-[13px] text-slate-500">
              {refusals.map((refusal) => refusal.code).join(" · ")}
            </p>
          </details>
        )}
        <TrackGuardrail guardrail={track.guardrail} lang={lang} t={t} />
      </div>
    );
  }
  return (
    <div className="mt-1 rounded-md border border-sky-300 bg-sky-50/60 px-2.5 py-2 text-[14px] leading-6 text-slate-700">
      <p className="font-semibold text-slate-800">
        {pick(track.label, lang)}
        <span className="ml-1 font-mono text-[14px] font-normal text-slate-500">{track.unit}</span>
      </p>
      <p className="mt-0.5 font-semibold">{pick(card.question, lang)}</p>
      <p className="mt-0.5">{pick(card.because, lang)}</p>
      {/* NUMBERS on the surface: the searched bands, the split, the budget. */}
      <p className="mt-1 text-[14px] text-slate-700">
        {card.plan.space.map((band) => `${band.parameter} ${band.low}–${band.high}`).join(" · ")}
      </p>
      <p className="mt-0.5 text-[14px] text-slate-700">
        {t("보정", "calibrate", "補正")} {card.plan.calibration_nodes.join(",")} ·{" "}
        {t("홀드아웃", "holdout", "ホールドアウト")} {card.plan.holdout_nodes.join(",")} ·{" "}
        {Math.round(card.plan.budget_s / 60)}
        {t("분", "min", "分")}
        {card.plan.resumable ? t(" (이어하기 가능)", " (resumable)", " (再開可)") : ""}
      </p>
      {/* The honest half. Never hidden behind a disclosure. */}
      <p className="mt-1 border-l border-sky-300 pl-2 text-slate-600">
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
      </p>
      {/* The band's PAGE. The range is a number and stays above; the citation is
          provenance, and the user's 2026-08-20 decision put provenance one click
          away. It travels with the band it authorises, verbatim. */}
      <details className="mt-1 text-[13px] leading-5 text-slate-600">
        <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
          {t("자세히 (탐색 범위의 출처)", "More (where the bands are published)", "詳細（探索範囲の出典）")}
        </summary>
        <p className="mt-1 border-l border-slate-300 pl-2 font-mono text-[13px] text-slate-500">
          {card.plan.space[0] ? card.plan.space[0].citation : ""}
        </p>
      </details>
      <TrackGuardrail guardrail={track.guardrail} lang={lang} t={t} />
    </div>
  );
}

/** Unit #51-c: what happens to a value this search would produce, and the user's
 * answer on the collision — on the track in BOTH states.
 *
 * Rendered locked as well as open on purpose: the locked state is what this
 * screen shows most of the time, and "the collision that used to block this door
 * has been decided, this way, on this date" is a fact about the product, not
 * about one search run.
 *
 * TWO LINES, NEVER ONE. Until 2026-08-20 a searched write had a single forecast
 * and printing it was true; #51-b implemented decision (a) and from that moment
 * one line would be a lie in one direction or the other. The approved arm here
 * carries a CONDITION rather than a verdict — no behaviour has been chosen at
 * this point, so the server states the rule and forecasts nothing, and
 * `GuardrailPathLines` renders exactly that (the card, where a behaviour exists,
 * gets the verdict). Every sentence and every status word is the server's. */
function TrackGuardrail({
  guardrail,
  lang,
  t,
}: {
  guardrail: CalibrationRailTrack["guardrail"];
  lang: string;
  t: Translate;
}) {
  if (!guardrail) return null;
  return (
    <div className="mt-1.5 border-t border-slate-200 pt-1.5">
      <p className="text-[14px] font-bold uppercase tracking-[0.08em] text-slate-500">
        {t("탐색 산출물의 가드레일", "The guardrail on a searched value", "探索産出物のガードレール")}
      </p>
      <GuardrailPathLines paths={guardrail.paths} t={t} />
      <GuardrailResolutionChips resolutions={guardrail.resolutions} lang={lang} t={t} />
      {/* #50-c: the DECISION's machine code moved off the surface (the user,
          2026-08-20) — the decision itself is the chips above, in the server's
          own sentence, and the code beside the heading was a second, unreadable
          copy of it. Folded, never dropped: it is the id an auditor cites. */}
      {guardrail.decision && (
        <details className="mt-1 text-[13px] leading-5 text-slate-500">
          <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
            {t("자세히 (결정 코드)", "More (the decision code)", "詳細（決定コード）")}
          </summary>
          <p className="mt-1 border-l border-slate-300 pl-2 font-mono text-[13px] text-slate-500">
            {guardrail.decision}
          </p>
        </details>
      )}
    </div>
  );
}

/** What a row says when it is drawn for its PROPOSALS and its steps are unread.
 *
 * A row with no step line and no note would read as "nothing to do here", which
 * is the opposite of why it is on screen: it is there because cards are waiting
 * at it and the rail has not been told what its steps are. It states which of
 * the two it is, and on what basis the cards below were placed. */
function StepsUnreadNote({ t }: { t: Translate }) {
  return (
    <span className="mt-0.5 block text-[14px] leading-6 text-slate-600">
      {t(
        "이 단계의 상태는 아직 읽지 못했습니다. 아래 제안은 편집 도구가 다루는 대상으로 배치했습니다.",
        "This station's step status has not been read yet. The proposals here were placed by what their edit tools act on.",
        "この段階の状態はまだ読み込めていません。ここの提案は編集ツールが扱う対象に基づいて配置しています。",
      )}
    </span>
  );
}

/** One step's line inside a rail row: its number, the server's label, its
 * status. No sentence — the basis sits with the evidence, in the station. */
function StepLine({ step, lang, t }: { step: CalibrationRailStep; lang: string; t: Translate }) {
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
      <span className="font-mono text-[14px] font-semibold text-slate-500">{circledStep(step.number)}</span>
      <span className="text-[14px] leading-6 text-slate-700">{stepLabel(step, lang)}</span>
      <span
        className={`rounded-full border px-1.5 py-0.5 text-[14px] font-semibold ${statusChipClass(step.status)}`}
      >
        {statusLabel(step.status, t)}
      </span>
    </span>
  );
}

export default function FlowRail({
  rail,
  loading,
  error,
  grouped,
  proposalCounts,
  activeStation,
  onSelectStation,
  onSelectStep,
}: {
  rail: CalibrationRail | null;
  loading: boolean;
  error: string | null;
  /** The rail's own steps, split across the stations by
   * lib/calibrationStations.ts. Passed in rather than derived here so the rail
   * and the station panel cannot group the same steps two different ways. */
  grouped: GroupedSteps<CalibrationRailStep>;
  proposalCounts: Record<CalibrationStationId, number>;
  activeStation: CalibrationStationId;
  /** Clicking a row SWITCHES the station shown on the right. Not a scroll:
   * #50 scrolled, and a smooth scroll that silently did nothing on a
   * non-compositing browser was one of the reasons this screen was unreadable. */
  onSelectStation: (station: CalibrationStationId) => void;
  /** A single step inside an expanded group — same switch, and it tells the
   * station which step the reader came in on. */
  onSelectStep: (step: CalibrationRailStep) => void;
}) {
  const { t, lang } = useLanguage();
  const [manualOpen, setManualOpen] = useState(false);

  // The shared rule, read once per render. A station holding a pending proposal
  // is ALWAYS a row here, even before the rail has said what its steps are —
  // otherwise the card at it has no badge, no row and no way to be opened.
  const shownStations = new Set(stationRowsShown(grouped, proposalCounts));

  const rowClass = (station: CalibrationStationId) =>
    `w-full rounded-lg border px-2.5 py-2 text-left transition ${
      activeStation === station
        ? "border-sky-300 bg-sky-50/60"
        : "border-slate-200 bg-surface hover:bg-slate-50"
    }`;

  return (
    <nav
      aria-label={t("보정 단계 내비게이션", "Calibration step navigation", "補正段階ナビゲーション")}
      className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-surface px-3 py-3 lg:sticky lg:top-4 lg:self-start"
    >
      <header>
        <h2 className="text-[15px] font-bold text-slate-900">
          {t("보정 13단계", "The 13 calibration steps", "補正13段階")}
          {/* The honest headline is DOCTRINE, so it goes where this app already
              puts doctrine beside the control it governs: a reachable ⓘ (hover,
              focus, tap; Escape closes). It used to sit at full weight under the
              heading, competing with the thirteen rows it describes. Verbatim —
              a tooltip is a promotion for a sentence worth keeping. */}
          <InfoTip
            text={t(
              "각 단계의 상태는 이 워크스페이스에 실제로 있는 증거에서만 유도합니다. 증거가 없는 단계는 완료로 표시하지 않고 '증거 없음 — 수동 확인 영역'으로 둡니다.",
              "Each step's status is derived only from evidence this workspace actually holds. A step with no evidence is never shown as done — it stays a manual-check area.",
              "各段階の状態は、このワークスペースに実在する証拠からのみ導出します。証拠のない段階は完了とせず「証拠なし — 手動確認領域」とします。",
            )}
            label={t("상태 유도 규칙 보기", "Show how a status is derived", "状態の導出規則を表示")}
          />
        </h2>
        {/* THE TALLY IS NUMBERS, so it reads as numbers. It was one mono
            sentence ("완료 1 · 진행 2 · 다음 할 일 1 · 증거 없음 9") that a
            reader had to parse; the counts now wear the SAME chip vocabulary as
            the step rows, so the rail's state is legible without reading. Same
            three-colour rule, same server words, same order. */}
        {rail && (
          <ul className="mt-1 flex flex-wrap gap-1">
            {STATUS_ORDER.filter((status) => (rail.counts[status] ?? 0) > 0).map((status) => (
              <li
                key={status}
                className={`rounded-full border px-1.5 py-0.5 text-[14px] font-semibold ${statusChipClass(status)}`}
              >
                {statusLabel(status, t)} {rail.counts[status]}
              </li>
            ))}
          </ul>
        )}
      </header>

      {/* A failed rail must not strand the reader: the scoring station is where
          the run is scored and it needs nothing from this payload, so the rows
          below degrade to a stated failure while navigation keeps working. */}
      {error && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[14px] leading-6 text-rose-700">
          {t("보정 흐름을 불러오지 못했습니다:", "The calibration flow could not be loaded:", "補正フローを読み込めませんでした:")}{" "}
          {error}
        </p>
      )}

      <ol className="flex flex-col gap-1.5">
        {CALIBRATION_STATIONS.map((station) => {
          const steps = grouped.byStation[station] ?? [];
          const isManual = station === "manual";
          const isScoring = station === "scoring";
          // WHICH ROWS EXIST is lib/calibrationStations.ts's rule, not this
          // component's — it is the same rule that placed the cards, and the
          // two disagreeing is exactly how a proposal became unreachable
          // (`stationRowsShown` carries the measurement). A row is drawn for
          // its steps, for being 채점·진단, or for the proposals waiting at it.
          if (!shownStations.has(station)) return null;
          // Drawn for its badge alone: the rail has no steps for it yet.
          const stepsUnread = steps.length === 0 && !isScoring;

          if (isManual) {
            return (
              <li key={station}>
                <div className="flex items-stretch gap-1">
                  <button type="button" onClick={() => onSelectStation(station)} className={`${rowClass(station)} flex-1`}>
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[14px] font-semibold text-slate-800">{stationLabel(station, t)}</span>
                      <ProposalBadge count={proposalCounts[station] ?? 0} t={t} />
                    </span>
                    <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[14px] text-slate-500">
                        {steps.map((step) => circledStep(step.number)).join("")}
                      </span>
                      {statusTally(steps).map((entry) => (
                        <span
                          key={entry.status}
                          className={`rounded-full border px-1.5 py-0.5 text-[14px] font-semibold ${statusChipClass(entry.status)}`}
                        >
                          {statusLabel(entry.status, t)} {entry.count}
                        </span>
                      ))}
                    </span>
                    {stepsUnread && <StepsUnreadNote t={t} />}
                  </button>
                  <button
                    type="button"
                    onClick={() => setManualOpen((open) => !open)}
                    aria-expanded={manualOpen}
                    className="ui-button-ghost shrink-0 rounded-lg px-2 text-[14px] text-slate-600"
                  >
                    {manualOpen ? t("접기", "Collapse", "折りたたむ") : t("펼치기", "Expand", "展開")}
                  </button>
                </div>
                {manualOpen && (
                  <ul className="mt-1 flex flex-col gap-1 border-l border-slate-300 pl-2">
                    {steps.map((step) => (
                      <li key={step.number}>
                        <button
                          type="button"
                          onClick={() => onSelectStep(step)}
                          className="w-full rounded-md px-1.5 py-1 text-left hover:bg-slate-50"
                        >
                          <StepLine step={step} lang={lang} t={t} />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          }

          return (
            <li key={station}>
              <button type="button" onClick={() => onSelectStation(station)} className={rowClass(station)}>
                {/* #50-c: the "MRWA 단계 아님" caption left this row for a
                    tooltip on the station's own header (app/calibration/page.tsx)
                    — a row cannot hold an ⓘ without nesting a button inside a
                    button, and the claim belongs beside the station's citation
                    line, which is the thing it qualifies. */}
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[14px] font-semibold text-slate-800">{stationLabel(station, t)}</span>
                  <ProposalBadge count={proposalCounts[station] ?? 0} t={t} />
                </span>
                {steps.map((step) => (
                  <StepLine key={step.number} step={step} lang={lang} t={t} />
                ))}
                {stepsUnread && <StepsUnreadNote t={t} />}
                {isScoring && steps.length === 0 && (
                  <span className="mt-0.5 block text-[14px] leading-6 text-slate-600">
                    {t(
                      "실행을 실측과 대조하고 노드별로 진단합니다.",
                      "Score the run against observed counts and diagnose it node by node.",
                      "実行を実測と対照し、ノードごとに診断します。",
                    )}
                  </span>
                )}
              </button>
            </li>
          );
        })}

        {/* A step this build has no station for is NAMED here rather than
            filed under 수동 확인 영역 — that would claim this product measures
            nothing for it, which is a different (and possibly false) statement.
            Normally empty; it exists so the next step that leaves the manual
            list cannot vanish the way step ⑨ nearly did. */}
        {grouped.unplaced.length > 0 && (
          <li>
            <span className="block rounded-lg border border-dashed border-slate-300 bg-slate-50 px-2.5 py-2">
              <span className="text-[14px] font-semibold text-slate-700">
                {t("단계 미배치", "Steps with no station yet", "配置未定の段階")}
              </span>
              {grouped.unplaced.map((step) => (
                <StepLine key={step.number} step={step} lang={lang} t={t} />
              ))}
            </span>
          </li>
        )}

        {!rail && (
          <li className="px-1 text-[14px] text-slate-500">
            {loading
              ? t("보정 흐름을 읽는 중...", "Reading the calibration flow...", "補正フローを読み込み中...")
              : t("보정 흐름 정보가 없습니다.", "No calibration flow information.", "補正フロー情報がありません。")}
          </li>
        )}
      </ol>

      {/* §2.12.1's own ordering principles, and — since #50-c — the rail's own
          citation with them. The citation used to be a mono line under the
          heading, one of the provenance lines the user ordered off the surface
          on 2026-08-20. It is not deleted: it sits at the top of the fold that
          already carried this guideline's other words, which is exactly where a
          reader who wants the source goes looking. */}
      {rail && (
        <details className="text-[13px] leading-5 text-slate-600">
          <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
            {t(
              "자세히 (출처·지침의 순서 원칙)",
              "More (the source, and the guideline's ordering principles)",
              "詳細（出典・指針の順序原則）",
            )}
          </summary>
          <div className="mt-1.5 flex flex-col gap-1 border-l border-slate-300 pl-3">
            <p className="font-mono text-[13px] text-slate-500">{rail?.citation}</p>
            <ul className="flex flex-col gap-1">
              {rail.principles.map((principle) => (
                <li key={principle.citation + principle.text}>
                  {principle.text}
                  <span className="ml-1.5 font-mono text-[13px] text-slate-400">{principle.citation}</span>
                </li>
              ))}
            </ul>
          </div>
        </details>
      )}
    </nav>
  );
}

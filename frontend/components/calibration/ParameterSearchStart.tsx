"use client";

// ---------------------------------------------------------------------------
// Unit #51's START CONTROL — the ⑫ track's one action.
//
// WHY THIS IS ITS OWN FILE. From #51 until 2026-08-20 the trigger card
// deliberately carried NO button: `card.runner.wired` was false in every build,
// and a button that cannot do what it says is worse than no button. The live
// runner (backend/services/agent/search_runner.py + search_job.py) changed the
// flag from a constant into a MEASUREMENT of the backend's machine, so the
// control exists exactly where the measurement is true and nowhere else.
//
// Putting it in its own module is what makes that checkable rather than
// promised: FlowRail's own `ParameterSearchTrack` now contains no `<button>` at
// all, so a start control smuggled into the LOCKED branch or the
// runner-not-wired branch is not a subtle regression — it is a button appearing
// in a file the contract suite pins as buttonless.
//
// WHAT IT DECIDES: nothing about a search.
//
//   * whether a search MAY start   -> the server's precondition gate, re-derived
//                                     on the POST from evidence it read itself
//   * what the job is DOING        -> `job.headline` / `job.status_text`, server
//                                     sentences in the reader's language
//   * whether to keep polling      -> `job.live`, a server FIELD
//   * which behaviour to search    -> the modeller, from the server's list; with
//                                     exactly one the server preselects it, with
//                                     several the server refuses to choose
//
// What this file owns is the button's own words (it is a client control, so
// `t()` is right for its label) and the fact that the label states the COST
// before the click: a search occupies the machine's only Vissim for its whole
// budget. Ask-first was the user's decision ② and it survives here — the card
// asks the question, this control is the answer, and nothing starts on its own.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useState } from "react";

import {
  fetchParameterSearchJob,
  startParameterSearch,
  type CalibrationRailTrack,
  type ParameterSearchJob,
  type ParameterSearchStartResult,
} from "@/lib/api";
import { failed, ok, resolve, type Loaded } from "@/lib/asyncResource";
import { pick } from "@/lib/serverText";

type Translate = (ko: string, en: string, ja?: string) => string;

/** How often a running search is re-read. Deliberately slow: a search runs for
 * minutes to hours and its progress advances at candidate boundaries, so a
 * faster poll would only add requests to a backend that is busy driving Vissim. */
const POLL_MS = 5000;

export default function ParameterSearchStart({
  track,
  scenarioId,
  sessionId,
  lang,
  t,
  onFinished,
}: {
  track: CalibrationRailTrack;
  /** The run the rail's own gate was derived from. Sent with the start request
   * so the search scores against the SAME evidence the card was built on. */
  scenarioId: string;
  /** The agent conversation the finished search is remembered against, from the
   * drawer's `vissim:agent-session` broadcast. `null` is not guarded against
   * here: the server refuses by name (NO_AGENT_SESSION) with the sentence that
   * says what to do, and that refusal renders like any other. */
  sessionId: string | null;
  lang: string;
  t: Translate;
  /** Called when a job reaches a terminal state, so the page can re-read the
   * rail: a finished search changes the gate underneath this very card. */
  onFinished?: () => void;
}) {
  const card = track.card;
  const behaviours = track.behaviours ?? [];
  const [behaviourIntent, setBehaviourIntent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<ParameterSearchStartResult | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  // The server preselects only when there is nothing to choose (exactly one
  // searchable behaviour). With several it sends `null`, and an unanswered
  // select is what the POST then refuses by name — the choice is the modeller's.
  const chosenBehaviour =
    behaviourIntent ?? track.default_behavior_no ?? (behaviours.length === 1 ? behaviours[0].no : "");

  // The job to show: the one this control just started, else the one the rail
  // already carries. Both are the SERVER's record of the same registry.
  const serverJob = outcome?.job ?? track.search_job ?? null;
  const jobId = serverJob?.id ?? "";

  const [polled, setPolled] = useState<Loaded<ParameterSearchJob> | null>(null);
  const { data: polledJob } = resolve(polled, jobId, { idle: !jobId, keepPrevious: true });
  const job = polledJob ?? serverJob;

  const handleFinished = useCallback(() => onFinished?.(), [onFinished]);

  // Poll while the SERVER says the job is live. `job.live` is a field, not a
  // comparison this file makes against a status vocabulary of its own.
  useEffect(() => {
    if (!jobId || !serverJob?.live) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      fetchParameterSearchJob(jobId)
        .then((payload) => {
          if (cancelled) return;
          setPolled(ok(jobId, payload));
          if (payload.live) {
            timer = setTimeout(tick, POLL_MS);
          } else {
            handleFinished();
          }
        })
        .catch((caught) => {
          if (!cancelled) setPolled(failed(jobId, caught));
        });
    };
    timer = setTimeout(tick, POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, serverJob?.live, handleFinished]);

  function handleStart() {
    if (busy) return;
    setBusy(true);
    setStartError(null);
    startParameterSearch({
      scenarioId,
      sessionId,
      behaviorNo: chosenBehaviour || null,
    })
      .then((result) => {
        setOutcome(result);
        setBusy(false);
        if (result.started) handleFinished();
      })
      .catch((caught) => {
        setStartError(caught instanceof Error ? caught.message : String(caught));
        setBusy(false);
      });
  }

  const budgetMinutes = card ? Math.round(card.plan.budget_s / 60) : 0;
  const running = Boolean(job?.live);

  return (
    // `border-sky-200`, never 300: globals.css's dark `@theme` remaps sky
    // 50/200/400/500/600 and NOTHING else, so a `border-sky-300` falls through to
    // Tailwind's stock #7dd3fc — a bright cyan hairline nothing else in this app
    // draws. Same trap, same fix, as the genesis pages (contract suite: "sky-300
    // is outside the dark remap").
    <div className="mt-1 border-l border-sky-200 pl-2">
      <p className="text-slate-600">
        {t(
          "실행기가 이 백엔드에 연결되어 있습니다 — 시작하면 Vissim이 후보마다 한 번씩 실행됩니다.",
          "A runner is wired on this backend — starting one runs Vissim once per candidate.",
          "実行機がこのバックエンドに接続されています — 開始すると候補ごとにVissimが1回実行されます。",
        )}
      </p>

      {/* The CHOICE the server refuses to make. Rendered only when there is one
          to make: with a single searchable behaviour the server preselects it,
          and a select with one option is a control that asks nothing. */}
      {behaviours.length > 1 && (
        <label className="mt-1 flex flex-wrap items-center gap-1.5 text-[14px] text-slate-700">
          <span>{t("보정할 주행행태", "Driving behaviour to calibrate", "補正する走行挙動")}</span>
          <select
            className="ui-select h-8 text-[14px]"
            value={chosenBehaviour}
            onChange={(event) => setBehaviourIntent(event.target.value)}
          >
            <option value="">{t("선택하세요", "Choose one", "選択してください")}</option>
            {behaviours.map((behaviour) => (
              <option key={behaviour.no} value={behaviour.no}>
                {behaviour.no} {behaviour.name}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleStart}
          disabled={busy || running}
          className="ui-button-primary inline-flex h-9 items-center px-3 text-[14px]"
        >
          {running
            ? t("탐색 실행 중…", "Search running…", "探索実行中…")
            : t(
                `탐색 시작 (예산 ${budgetMinutes}분, Vissim 실행)`,
                `Start the search (${budgetMinutes} min budget, runs Vissim)`,
                `探索開始 (予算${budgetMinutes}分、Vissimを実行)`,
              )}
        </button>
        {busy && (
          <span className="text-[14px] text-slate-500">
            {t("보내는 중…", "Submitting…", "送信中…")}
          </span>
        )}
      </div>

      {/* THE JOB, in the server's words. `headline` is one sentence and which
          sentence it is was decided on the job thread — running, budget spent,
          finished-and-remembered, finished-with-no-winner, failed. */}
      {job && (
        <div className="mt-1.5 rounded-md border border-slate-200 bg-surface px-2 py-1.5 text-[14px] leading-6 text-slate-700">
          <p className="font-semibold text-slate-800">{pick(job.status_text, lang)}</p>
          <p className="mt-0.5">{pick(job.headline, lang)}</p>
          <p className="mt-0.5 text-slate-600">
            {t("후보", "candidates", "候補")} {job.candidates_evaluated}
            {job.candidates_failed > 0
              ? ` (${t("실패", "failed", "失敗")} ${job.candidates_failed})`
              : ""}{" "}
            · {Math.round(job.elapsed_s / 60)}/{Math.round(job.budget_s / 60)}
            {t("분", "min", "分")}
            {job.behavior_no ? ` · ${t("주행행태", "behaviour", "走行挙動")} ${job.behavior_no}` : ""}
          </p>
          {job.winner && (
            <p className="mt-0.5 text-slate-700">
              {Object.entries(job.winner.params)
                .map(([parameter, value]) => `${parameter} ${value}`)
                .join(" · ")}
            </p>
          )}
        </div>
      )}

      {/* The server's named refusals for a start that did not happen. Same
          ladder grammar as the locked track's: what is wrong, and what opens it. */}
      {outcome && !outcome.started && outcome.refusals.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1 border-l border-slate-300 pl-2 text-[14px] leading-6 text-slate-700">
          {outcome.refusals.map((refusal) => (
            <li key={refusal.code}>
              {pick(refusal.why, lang)}{" "}
              <span className="text-slate-500">{pick(refusal.opens_when, lang)}</span>
            </li>
          ))}
        </ul>
      )}

      {startError && <p className="mt-1.5 text-[14px] text-rose-700">{startError}</p>}
    </div>
  );
}

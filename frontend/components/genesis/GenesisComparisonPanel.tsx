"use client";

// Roadmap v2 §5.B B3 (완결), half 2/2 -- prior-generation comparison.
//
// Picks an earlier job from the SAME list `GET /jobs` already returns (no new
// endpoint), fetches that job's own stored gate report the same way the main
// genesis page fetches the selected job's (`fetchGenesisGateReport` --
// lib/api.ts), and diffs the two: per-check (V1-V9) state changes, which
// findings were added/removed/unchanged, and the job records' own network
// summary. THE SCOPE IS NAMED ON SCREEN because it is a real limit, not a
// courtesy: this never compares network GEOMETRY -- that is
// `services/genesis/diff.py` / `benchmark.py`'s job, and re-deriving any part
// of it here, from data this page never fetches (no from/to job endpoints, no
// per-link geometry hashing), would be a second, drifting copy of a
// comparison the backend already does correctly.
//
// The one extra number worth having that neither the job record nor the gate
// report carries on its own -- how many zones were excluded -- comes from
// each side's OWN `genesis_json` artifact (lib/genesisArtifacts.ts's
// `fetchGenesisDraftNetwork`), fetched lazily and only for the two jobs being
// compared. A job with no artifact (errored before a network existed) renders
// as "—"; a job that DOES have one but whose fetch failed anyway renders as
// its own "불러오지 못함" marker (`ExcludedZoneResult`, below) -- the two used
// to collapse into the same "—", which read as "no excluded zones" when the
// honest answer was "could not check".

import { useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { InfoTip } from "@/components/genesis/ui";
import { fetchGenesisGateReport, type GenesisGateReport, type GenesisJob } from "@/lib/api";
import {
  buildNetworkStatDeltas,
  compareGenesisChecks,
  fetchGenesisDraftNetwork,
  formatTimestamp,
  statusLabel,
  type GenesisCheckDelta,
} from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

function stateLabel(state: GenesisCheckDelta["baseState"], t: Translate): string {
  switch (state) {
    case "pass":
      return t("통과", "PASS", "合格");
    case "fail":
      return t("실패", "FAIL", "不合格");
    case "not_checkable":
      return t("확인 불가", "N/C", "確認不可");
    default:
      return t("없음", "absent", "なし");
  }
}

function stateColor(state: GenesisCheckDelta["baseState"]): string {
  if (state === "pass") return "text-emerald-700";
  if (state === "fail") return "text-rose-700";
  if (state === "not_checkable") return "text-slate-900 underline decoration-dotted underline-offset-2";
  return "text-slate-400";
}

function statLabel(key: string, t: Translate): string {
  switch (key) {
    case "links":
      return t("링크", "Links", "リンク");
    case "connectors":
      return t("커넥터", "Connectors", "コネクタ");
    case "nodes":
      return t("노드", "Nodes", "ノード");
    case "excluded_zones":
      return t("제외된 구역", "Excluded zones", "除外されたゾーン");
    default:
      return key;
  }
}

// A missing count and a FAILED fetch used to render as the same "—" -- a
// consultant reading "no excluded zones" had no way to tell that apart from
// "the count could not be read at all". `ok`/`no_artifact`/`error` are three
// different, honestly-named claims: only `no_artifact` legitimately means "—".
type ExcludedZoneResult =
  | { status: "ok"; count: number }
  | { status: "no_artifact" }
  | { status: "error"; message: string };

async function loadExcludedZoneCount(job: GenesisJob): Promise<ExcludedZoneResult> {
  if (!job.artifact) return { status: "no_artifact" };
  try {
    const network = await fetchGenesisDraftNetwork(job.id);
    return { status: "ok", count: network.metadata.connectivity?.excluded_zones?.length ?? 0 };
  } catch (caught) {
    return { status: "error", message: caught instanceof Error ? caught.message : String(caught) };
  }
}

function excludedZoneCountOrNull(result: ExcludedZoneResult | null): number | null {
  return result?.status === "ok" ? result.count : null;
}

/** Same naming pattern as `GenesisTileMap`'s `loadError` -- "—" for a
 * genuine absence, a distinct marker for a fetch that failed outright. */
function excludedZoneDisplay(result: ExcludedZoneResult | null, t: Translate): string {
  if (!result || result.status === "no_artifact") return "—";
  if (result.status === "ok") return String(result.count);
  return t("불러오지 못함", "failed to load", "読み込み失敗");
}

export default function GenesisComparisonPanel({
  jobs,
  currentJob,
  currentReport,
  currentReportError = null,
}: {
  jobs: GenesisJob[];
  currentJob: GenesisJob;
  currentReport: GenesisGateReport | null;
  currentReportError?: string | null;
}) {
  const { lang, t } = useLanguage();
  const candidates = useMemo(() => jobs.filter((job) => job.id !== currentJob.id), [jobs, currentJob.id]);

  const [priorId, setPriorId] = useState<string>("");
  const [priorReport, setPriorReport] = useState<GenesisGateReport | null>(null);
  const [priorJob, setPriorJob] = useState<GenesisJob | null>(null);
  const [statDeltas, setStatDeltas] = useState<ReturnType<typeof buildNetworkStatDeltas> | null>(null);
  const [excludedZoneResults, setExcludedZoneResults] = useState<{
    prior: ExcludedZoneResult;
    current: ExcludedZoneResult;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!priorId) {
      setPriorReport(null);
      setPriorJob(null);
      setStatDeltas(null);
      setExcludedZoneResults(null);
      setError(null);
      return;
    }
    const job = candidates.find((candidate) => candidate.id === priorId) ?? null;
    setPriorJob(job);
    if (!job) return;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setPriorReport(null);
    setStatDeltas(null);
    setExcludedZoneResults(null);

    Promise.all([
      fetchGenesisGateReport(priorId, lang),
      loadExcludedZoneCount(job),
      loadExcludedZoneCount(currentJob),
    ])
      .then(([report, priorExcluded, currentExcluded]) => {
        if (cancelled) return;
        setPriorReport(report);
        setExcludedZoneResults({ prior: priorExcluded, current: currentExcluded });
        setStatDeltas(
          buildNetworkStatDeltas(
            job.network,
            currentJob.network,
            excludedZoneCountOrNull(priorExcluded),
            excludedZoneCountOrNull(currentExcluded),
          ),
        );
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught.message
              : t("이 생성의 게이트 결과를 불러오지 못했습니다.", "Could not load that generation's gate result.", "この生成のゲート結果を読み込めませんでした。"),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [priorId, lang, currentJob.id]);

  const checkTitles = useMemo(() => {
    const titles: Record<string, string> = {};
    for (const check of currentReport?.checks ?? []) titles[check.id] = check.title;
    for (const check of priorReport?.checks ?? []) if (!titles[check.id]) titles[check.id] = check.title;
    return titles;
  }, [currentReport, priorReport]);

  const deltas = useMemo(() => {
    if (!priorReport || !currentReport) return [];
    return compareGenesisChecks(priorReport, currentReport);
  }, [priorReport, currentReport]);

  return (
    /* ONE TITLE, ONE CONTROL, RESULTS — docs/ux-backlog.md, SECOND ELABORATION
     * item 9. This used to be a card (border + fill) drawn INSIDE the page's
     * own card, carrying a heading ("이전 생성과 비교") that meant the same
     * thing as the section heading directly above it, above a picker labelled
     * a third time. Three boxes and three titles for one control.
     *
     * The card and the heading are both gone: the caller supplies the section
     * title, this component supplies the picker and the table. The SCOPE
     * sentence — the limit on what this comparison claims — is NOT gone; it
     * moved onto the picker's own label, which is the one place it is on
     * screen exactly when a comparison is being made. This panel remains its
     * single source (the U-1 contract test pins that), and it stays verbatim. */
    <div>
      {!currentJob.gate ? (
        // Named, not blank: a queued/running job (or one that errored before a
        // network existed) never gets a gate report AT ALL -- `page.tsx` mounts
        // this panel on `selected` alone, so without this branch the spinner
        // above simply ends and nothing renders where a comparison would go.
        <p className="mt-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-center text-[13px] text-slate-500">
          {t(
            "이 생성에는 아직 게이트 결과가 없어 비교할 수 없습니다.",
            "This generation has no gate result yet, so it cannot be compared.",
            "この生成にはまだゲート結果がないため比較できません。",
          )}
        </p>
      ) : currentReportError ? (
        // The job HAS a gate but fetching its stored report FAILED -- without
        // this branch the failure collapses into the loading message below and
        // reads as pending forever. Same three-state honesty as
        // `ExcludedZoneResult`, applied to the current report itself.
        <p className="mt-2 rounded-lg border-2 border-slate-400 bg-slate-100 px-3 py-2 text-[13px] text-slate-900">
          {t(
            "현재 생성의 게이트 결과를 불러오지 못해 비교할 수 없습니다",
            "Could not load this generation's gate result, so it cannot be compared",
            "現在の生成のゲート結果を読み込めなかったため比較できません",
          )}
          : {currentReportError}
        </p>
      ) : !currentReport ? (
        // The job DOES have a gate -- `page.tsx` is still fetching its stored
        // report (`fetchGenesisGateReport`). Transient, but named rather than
        // silent so it never reads as "this feature is broken".
        <p className="mt-2 text-[13px] text-slate-500">
          {t(
            "현재 생성의 게이트 결과를 불러오는 중…",
            "Loading the current generation's gate result…",
            "現在の生成のゲート結果を読み込み中…",
          )}
        </p>
      ) : candidates.length === 0 ? (
        <p className="mt-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-center text-[13px] text-slate-500">
          {t("비교할 이전 생성이 없습니다.", "There is no earlier generation to compare against.", "比較できる以前の生成がありません。")}
        </p>
      ) : (
        <div>
          <label className="flex flex-col gap-1 text-[13px] text-slate-600">
            <span>
              {t("비교할 이전 생성", "Generation to compare against", "比較する以前の生成")}
              <InfoTip
                text={t(
                  "게이트 판정과 개수만 비교합니다 — 네트워크 기하 자체의 차이는 비교하지 않습니다.",
                  "Compares gate results and counts only -- it does not diff the networks' geometry itself.",
                  "ゲート判定と件数のみを比較します — ネットワークの形状自体の差分は比較しません。",
                )}
              />
            </span>
            <select
              value={priorId}
              onChange={(event) => setPriorId(event.target.value)}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs text-slate-800"
            >
              <option value="">{t("선택하세요", "Choose one", "選択してください")}</option>
              {candidates.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.name} · {statusLabel(job.status, t)} · {formatTimestamp(job.created_at, lang)}
                </option>
              ))}
            </select>
          </label>

          {loading && (
            <p className="mt-2 text-[13px] text-slate-500">{t("비교하는 중…", "Comparing…", "比較中…")}</p>
          )}
          {error && (
            <p className="mt-2 rounded-lg border-2 border-slate-400 bg-slate-100 px-3 py-2 text-[13px] text-slate-900">
              {error}
            </p>
          )}

          {!loading && !error && priorJob && priorReport && currentReport && (
            <>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse text-[13px]">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-slate-500">
                      <th className="py-1 pr-2 font-semibold">{t("검사", "Check", "検査")}</th>
                      <th className="py-1 pr-2 font-semibold">{priorJob.name}</th>
                      <th className="py-1 pr-2 font-semibold" />
                      <th className="py-1 pr-2 font-semibold">{currentJob.name}</th>
                      <th className="py-1 pr-2 font-semibold" title={t("거부/경고 등급 발견 (check.findings)", "refuse/warn-tier findings (check.findings)", "拒否/警告ティアの発見 (check.findings)")}>
                        {t("발견 추가", "Findings +", "発見 +")}
                      </th>
                      <th className="py-1 pr-2 font-semibold">{t("발견 제거", "Findings −", "発見 −")}</th>
                      <th className="py-1 pr-2 font-semibold">{t("발견 변동 없음", "Findings =", "発見 =")}</th>
                      <th className="py-1 pr-2 font-semibold" title={t("경고 전용 항목 (check.warnings) -- 위 발견 수에는 포함되지 않습니다", "warn-only items (check.warnings) -- not included in the findings columns above", "警告専用項目 (check.warnings) -- 上の発見数には含まれません")}>
                        {t("경고 추가", "Warn +", "警告 +")}
                      </th>
                      <th className="py-1 pr-2 font-semibold">{t("경고 제거", "Warn −", "警告 −")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deltas.map((delta) => (
                      <tr key={delta.id} className={`border-b border-slate-100 ${delta.stateChanged ? "bg-slate-100" : ""}`}>
                        <td className="py-1.5 pr-2">
                          <span className="font-mono font-bold">{delta.id}</span>
                          <span className="ml-1 text-slate-500">{checkTitles[delta.id] ?? ""}</span>
                        </td>
                        <td className={`py-1.5 pr-2 font-semibold ${stateColor(delta.baseState)}`}>
                          {stateLabel(delta.baseState, t)}
                        </td>
                        <td className="py-1.5 pr-2 text-slate-400">{delta.stateChanged ? "→" : "="}</td>
                        <td className={`py-1.5 pr-2 font-semibold ${stateColor(delta.targetState)}`}>
                          {stateLabel(delta.targetState, t)}
                        </td>
                        <td className="py-1.5 pr-2 text-rose-700">{delta.added.length > 0 ? `+${delta.added.length}` : "0"}</td>
                        <td className="py-1.5 pr-2 text-emerald-700">
                          {delta.removed.length > 0 ? `-${delta.removed.length}` : "0"}
                        </td>
                        <td className="py-1.5 pr-2 text-slate-500">{delta.unchangedCount}</td>
                        <td className="py-1.5 pr-2 text-slate-700">
                          {delta.addedWarnings.length > 0 ? `+${delta.addedWarnings.length}` : "0"}
                        </td>
                        <td className="py-1.5 pr-2 text-slate-700">
                          {delta.removedWarnings.length > 0 ? `-${delta.removedWarnings.length}` : "0"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {deltas.some((d) => d.baseFindingsOmitted > 0 || d.targetFindingsOmitted > 0 || d.baseWarningsOmitted > 0 || d.targetWarningsOmitted > 0) && (
                <p className="mt-1.5 text-[12px] text-slate-900">
                  {t(
                    "한쪽 이상의 게이트 결과가 표시 항목을 잘랐습니다 — 위 추가/제거 수는 하한값입니다.",
                    "At least one side's gate result truncated the findings it shipped -- the added/removed counts above are a lower bound.",
                    "少なくとも一方のゲート結果が表示項目を切り詰めています — 上の追加/削除件数は下限値です。",
                  )}
                </p>
              )}
              {deltas.some((d) => d.duplicateKeysPresent) && (
                <p className="mt-1 text-[12px] text-slate-500">
                  {t(
                    "일부 검사에서 코드+대상이 같은 항목이 여러 번 나타났습니다 — 위 추가/제거 수는 그 중복 횟수까지 반영한 값입니다.",
                    "At least one check repeated the same code+subject more than once -- the added/removed counts above already account for those repeats.",
                    "一部の検査でコード+対象が同じ項目が複数回現れました — 上の追加/削除件数はその重複回数を反映済みです。",
                  )}
                </p>
              )}

              {statDeltas && (
                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {statDeltas.map((stat) => (
                    <div key={stat.label} className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-center">
                      <p className="text-[12px] text-slate-500">{statLabel(stat.label, t)}</p>
                      <p className="mt-0.5 text-xs font-bold text-slate-800">
                        {stat.label === "excluded_zones"
                          ? excludedZoneDisplay(excludedZoneResults?.prior ?? null, t)
                          : (stat.base ?? "—")}
                        {" → "}
                        {stat.label === "excluded_zones"
                          ? excludedZoneDisplay(excludedZoneResults?.current ?? null, t)
                          : (stat.target ?? "—")}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

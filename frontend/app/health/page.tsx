"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  fetchHealthCheck,
  healthCheckDownloadUrl,
  type HealthCheckEditTimeOnlyEntry,
  type HealthCheckFinding,
  type HealthCheckReport,
} from "@/lib/api";
import { toneBadgeClass, toneTextClass, type ValidationTone } from "@/lib/validationThresholds";

const SEVERITY_TONE: Record<HealthCheckFinding["severity"], ValidationTone> = {
  issue: "danger",
  warning: "warning",
  info: "neutral",
};

const SEVERITY_ORDER: HealthCheckFinding["severity"][] = ["issue", "warning", "info"];

function severityLabel(severity: HealthCheckFinding["severity"], t: (ko: string, en: string, ja?: string) => string): string {
  if (severity === "issue") return t("이슈", "Issues", "問題");
  if (severity === "warning") return t("경고", "Warnings", "警告");
  return t("정보", "Info", "情報");
}

function formatTimestamp(iso: string, lang: "ko" | "en" | "ja"): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const locale = lang === "ko" ? "ko-KR" : lang === "ja" ? "ja-JP" : "en-US";
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

const STAT_LABELS: Record<keyof HealthCheckReport["stats"], { ko: string; en: string; ja: string }> = {
  links: { ko: "링크", en: "Links", ja: "リンク" },
  nodes: { ko: "노드", en: "Nodes", ja: "ノード" },
  vehicle_inputs: { ko: "차량 입력", en: "Vehicle inputs", ja: "車両インプット" },
  signal_controllers: { ko: "신호제어기", en: "Signal controllers", ja: "信号制御機" },
  conflict_areas: { ko: "충돌구간", en: "Conflict areas", ja: "錯綜区間" },
  routing_decisions: { ko: "경로 결정", en: "Routing decisions", ja: "経路決定" },
  routes: { ko: "경로", en: "Routes", ja: "経路" },
  vehicle_compositions: { ko: "차량 조성", en: "Vehicle compositions", ja: "車両構成" },
  des_speed_decisions: { ko: "희망속도결정", en: "Desired speed decisions", ja: "希望速度決定" },
};

export default function HealthCheckPage() {
  const { lang, t } = useLanguage();
  const [report, setReport] = useState<HealthCheckReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setExpanded({});
    try {
      const result = await fetchHealthCheck();
      setReport(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleExpanded = (index: number) => {
    setExpanded((prev) => ({ ...prev, [index]: !prev[index] }));
  };

  const findingsBySeverity = new Map<HealthCheckFinding["severity"], HealthCheckFinding[]>();
  if (report) {
    for (const finding of report.findings) {
      const bucket = findingsBySeverity.get(finding.severity) ?? [];
      bucket.push(finding);
      findingsBySeverity.set(finding.severity, bucket);
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          {t("모델 검진", "Model Health Check", "モデル検診")}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          {t(
            "Vissim/COM 없이 현재 활성 데이터셋(.inpx)을 파싱해, 가드레일 제약 카탈로그로 네트워크 정합성을 한 번에 점검합니다.",
            "Parses the currently active dataset (.inpx) with no Vissim/COM and runs the guardrail constraint catalog against it in one click.",
            "Vissim/COMなしで現在アクティブなデータセット(.inpx)を解析し、ガードレイル制約カタログでネットワークの整合性を一括点検します。",
          )}
        </p>
      </header>

      <div className="mb-6 flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
        <button
          type="button"
          onClick={run}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-ink transition hover:bg-slate-950 disabled:cursor-wait disabled:opacity-60"
        >
          {loading
            ? t("검진 중…", "Running…", "検診中…")
            : report
              ? t("다시 검진", "Run again", "再検診")
              : t("검진 실행", "Run health check", "検診を実行")}
        </button>
        {report && (
          <a
            href={healthCheckDownloadUrl()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-surface px-3.5 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-100"
          >
            {t("JSON 다운로드", "Download JSON", "JSONダウンロード")}
          </a>
        )}
        {report && (
          <span className="text-xs text-slate-500">
            {t("생성 시각", "Generated", "生成時刻")}: {formatTimestamp(report.generated_at, lang)}
            {" · "}
            {report.dataset.name}
            {report.dataset.revision ? ` (${report.dataset.revision.slice(0, 12)})` : ""}
          </span>
        )}
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {t("검진을 실행하지 못했습니다", "Failed to run the health check", "検診を実行できませんでした")}: {error}
        </div>
      )}

      {!report && !loading && !error && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
          {t(
            "위 버튼을 눌러 현재 활성 데이터셋의 건강검진을 실행하세요.",
            "Click the button above to run a health check on the currently active dataset.",
            "上のボタンを押して、現在アクティブなデータセットの健康診断を実行してください。",
          )}
        </div>
      )}

      {report && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {SEVERITY_ORDER.map((severity) => {
              const count = report.summary.findings_by_severity[severity] ?? 0;
              const tone = SEVERITY_TONE[severity];
              return (
                <div
                  key={severity}
                  className="rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]"
                >
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {severityLabel(severity, t)}
                  </p>
                  <p className={`mt-1 text-2xl font-bold tabular-nums ${toneTextClass(tone)}`}>{count}</p>
                </div>
              );
            })}
          </div>

          <p className="mb-4 text-xs text-slate-500">
            {t(
              `데이터셋 검사 가능 제약 ${report.summary.checked_n}개 확인 완료 · 편집/실행 시에만 검사되는 제약 ${report.summary.edit_time_only_n}개는 아래에 별도로 표시됩니다.`,
              `${report.summary.checked_n} dataset-scope constraint(s) checked · ${report.summary.edit_time_only_n} constraint(s) that can only be checked at edit/run time are listed separately below.`,
              `データセット検査可能な制約 ${report.summary.checked_n}件を確認済み · 編集/実行時のみ検査される制約 ${report.summary.edit_time_only_n}件は下に別途表示されます。`,
            )}
          </p>

          {report.findings.length === 0 ? (
            <div className="mb-6 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-6 text-center text-sm text-emerald-700">
              {t("발견된 이슈가 없습니다.", "No issues found.", "検出された問題はありません。")}
            </div>
          ) : (
            <div className="mb-6 space-y-6">
              {SEVERITY_ORDER.filter((s) => (findingsBySeverity.get(s) ?? []).length > 0).map((severity) => (
                <section key={severity}>
                  <h2 className="mb-2 text-sm font-bold text-slate-900">
                    {severityLabel(severity, t)} ({(findingsBySeverity.get(severity) ?? []).length})
                  </h2>
                  <ul className="space-y-2">
                    {(findingsBySeverity.get(severity) ?? []).map((finding, index) => {
                      const key = `${severity}-${index}`;
                      const globalIndex = report.findings.indexOf(finding);
                      const isOpen = !!expanded[globalIndex];
                      if (finding.message === null) {
                        return (
                          <li
                            key={key}
                            className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3.5 py-2.5 text-xs text-slate-500"
                          >
                            {t(
                              `${finding.title.ko} — 추가로 ${finding.truncated_additional_count}건 더 있습니다 (JSON 다운로드에서 전체 확인 가능).`,
                              `${finding.title.en} — ${finding.truncated_additional_count} more not shown (see the JSON download for the full list).`,
                              `${finding.title.ja} — さらに${finding.truncated_additional_count}件あります(全件はJSONダウンロードで確認できます)。`,
                            )}
                          </li>
                        );
                      }
                      return (
                        <li
                          key={key}
                          className="rounded-lg border border-slate-200 bg-surface/90 px-3.5 py-3"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span
                                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${toneBadgeClass(SEVERITY_TONE[finding.severity])}`}
                                >
                                  {finding.constraint_id}
                                </span>
                                <h3 className="text-sm font-bold text-slate-900">{finding.title[lang]}</h3>
                              </div>
                              <p className="mt-1 text-sm text-slate-700">{finding.message[lang]}</p>
                              {finding.object_refs.length > 0 && (
                                <p className="mt-1 font-mono text-[10px] text-slate-400">
                                  {finding.object_refs.join(", ")}
                                </p>
                              )}
                            </div>
                            {Object.keys(finding.details).length > 0 && (
                              <button
                                type="button"
                                onClick={() => toggleExpanded(globalIndex)}
                                className="flex-none rounded-lg border border-slate-300 bg-surface px-2.5 py-1 text-[11px] font-semibold text-slate-600 transition hover:bg-slate-100"
                              >
                                {isOpen ? t("접기", "Hide details", "閉じる") : t("상세", "Details", "詳細")}
                              </button>
                            )}
                          </div>
                          {isOpen && (
                            <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-950/90 px-3 py-2 text-[11px] text-slate-200">
                              {JSON.stringify(finding.details, null, 2)}
                            </pre>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          )}

          <details className="mb-6 rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3">
            <summary className="cursor-pointer text-sm font-bold text-slate-700">
              {t("편집 시 검사", "Checked at edit time", "編集時に検査")} ({report.edit_time_only.length})
            </summary>
            <p className="mt-2 text-xs text-slate-500">
              {t(
                "아래 제약들은 실제 편집(파라미터)이나 실행 결과가 있어야 평가할 수 있어, 이 정적 검진에는 포함되지 않습니다. AI 에이전트가 편집을 제안하거나 시뮬레이션을 실행할 때 자동으로 검사됩니다.",
                "These constraints need a proposed edit's parameters or real run results to evaluate, so this static check cannot cover them. They are checked automatically whenever the AI agent proposes an edit or runs a simulation.",
                "以下の制約は実際の編集パラメータや実行結果がないと評価できないため、この静的検診には含まれません。AIエージェントが編集を提案したりシミュレーションを実行したりする際に自動的に検査されます。",
              )}
            </p>
            <ul className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-xs text-slate-600 sm:grid-cols-2">
              {report.edit_time_only.map((entry: HealthCheckEditTimeOnlyEntry) => (
                <li key={entry.constraint_id} className="flex items-center gap-1.5">
                  <span
                    className={`h-1.5 w-1.5 flex-none rounded-full ${entry.tier === "refuse" ? "bg-rose-400" : "bg-amber-400"}`}
                    aria-hidden
                  />
                  <span className="truncate" title={entry.constraint_id}>
                    {entry.title[lang]}
                  </span>
                </li>
              ))}
            </ul>
          </details>

          <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3">
            <h2 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">
              {t("데이터셋 통계", "Dataset stats", "データセット統計")}
            </h2>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 md:grid-cols-4">
              {(Object.keys(STAT_LABELS) as Array<keyof HealthCheckReport["stats"]>).map((key) => (
                <div key={key}>
                  <p className="text-[11px] text-slate-500">{STAT_LABELS[key][lang]}</p>
                  <p className="text-sm font-bold tabular-nums text-slate-900">{report.stats[key]}</p>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

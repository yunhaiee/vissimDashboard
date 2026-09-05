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

// Matches CurationBar.tsx's own local alias for useLanguage()'s `t` (see that
// file's comment on why this stays a local type rather than a shared export).
type Translate = (ko: string, en: string, ja?: string) => string;

// Same custom-window-event seam CurationBar.tsx already uses to hand a
// request off to the real agent (FloatingAssistant, globally mounted in
// AppShell -- see components/AppShell.tsx, so it is already available on
// THIS page with no extra wiring). Not imported from CurationBar.tsx because
// that module doesn't export it either -- FloatingAssistant's own listener
// (components/FloatingAssistant.tsx) uses the same literal string directly;
// the event NAME is the shared contract between independent dispatchers,
// not a shared import.
const ASSISTANT_HANDOFF_EVENT = "vissim:assistant-handoff";

function requestAgentHandoff(prompt: string) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_HANDOFF_EVENT, { detail: { prompt } }));
}

function targetStr(target: Record<string, unknown>, key: string): string {
  const value = target[key];
  return value === null || value === undefined ? "" : String(value);
}

function targetExamples(target: Record<string, unknown>): string {
  const examples = target.examples;
  return Array.isArray(examples) ? examples.map((e) => String(e)).join(", ") : "";
}

function targetCount(target: Record<string, unknown>): string {
  const count = target.count;
  return typeof count === "number" ? String(count) : "";
}

// Per-constraint, localized, GROUNDED handoff prompts -- built from
// suggested_action.target (backend/services/agent/health_check.py's bounded
// _fix_target/_inspect_target), not just the finding's own display message,
// so the agent gets concrete ids/numbers to act on without a round trip.
// Falls back to the finding's own message text for any constraint id this
// page doesn't have a dedicated template for yet (future catalog growth),
// so a new dataset-checkable constraint never produces a dead/blank prompt.
function buildHandoffPrompt(finding: HealthCheckFinding, t: Translate): string {
  const { constraint_id: id } = finding;
  const target = finding.suggested_action?.target ?? {};

  switch (id) {
    case "volume_out_of_range": {
      const inputNo = targetStr(target, "input_no");
      const volume = targetStr(target, "volume");
      const suggestedFix = (target.suggested_fix as Record<string, unknown> | undefined) ?? {};
      const min = targetStr(suggestedFix, "min");
      const max = targetStr(suggestedFix, "max");
      return t(
        `차량 입력 ${inputNo}의 유입량 ${volume} veh/h가 허용 범위(${min}~${max} veh/h)를 벗어납니다. 현재 유입량 설정을 확인하고 범위 안에 들어오도록 수정해줘.`,
        `Vehicle input ${inputNo}'s volume ${volume} veh/h is outside the allowed range (${min}-${max} veh/h). Check the current input setting and fix it to fit the range.`,
        `車両インプット${inputNo}の交通量 ${volume} veh/h が許容範囲(${min}〜${max} veh/h)を超えています。現在のインプット設定を確認し、範囲内に収まるよう修正してください。`,
      );
    }
    case "latent_demand_overflow": {
      // FIX 4 (2026-07-28): the check became sibling-aware (a vehicle input's
      // link commonly carries several OTHER inputs, one per vehicle class --
      // see constraints.py's _check_latent_demand_overflow), so "this input's
      // OWN volume exceeds capacity" is frequently false; what actually
      // exceeds capacity is the LINK total (this input + every sibling, at
      // the interval being evaluated). sibling_volume_total/
      // link_total_volume/interval_no are real Finding.details keys now
      // carried into the target (health_check.py's _FIX_TARGET_FIELDS) --
      // state the TRUE breakdown instead of the false direct inequality.
      const inputNo = targetStr(target, "input_no");
      const linkId = targetStr(target, "link_id");
      const volume = targetStr(target, "volume");
      const capacity = targetStr(target, "capacity");
      const intervalNo = targetStr(target, "interval_no");
      const siblingTotal = targetStr(target, "sibling_volume_total");
      const linkTotal = targetStr(target, "link_total_volume");
      return t(
        `차량 입력 ${inputNo}(링크 ${linkId}, 구간 ${intervalNo})의 유입량 ${volume} veh/h에 같은 링크의 다른 차량 입력 합계 ${siblingTotal} veh/h를 더한 링크 총 수요 ${linkTotal} veh/h가 대략적 용량 ${capacity} veh/h를 초과합니다. 현재 유입량 설정을 확인하고 조정안을 제안해줘.`,
        `Vehicle input ${inputNo} (link ${linkId}, interval ${intervalNo})'s volume ${volume} veh/h plus ${siblingTotal} veh/h from other vehicle inputs on the same link gives a link total of ${linkTotal} veh/h, which exceeds the rough capacity ${capacity} veh/h. Check the current input setting and propose an adjustment.`,
        `車両インプット${inputNo}(リンク${linkId}、区間${intervalNo})の交通量 ${volume} veh/h に同じリンクの他の車両インプット合計 ${siblingTotal} veh/h を加えたリンク総需要 ${linkTotal} veh/h が、おおよその容量 ${capacity} veh/h を超えています。現在のインプット設定を確認し、調整案を提案してください。`,
      );
    }
    case "cycle_below_intergreen_sum": {
      const scNo = targetStr(target, "sc_no");
      const cycle = targetStr(target, "cycle");
      const minSum = targetStr(target, "min_sum");
      return t(
        `신호제어기 ${scNo}의 주기 ${cycle}초가 최소 녹색시간+인터그린 시간 합계 ${minSum}초보다 짧습니다. 현재 신호 프로그램을 확인하고 주기를 ${minSum}초 이상으로 수정해줘.`,
        `Signal controller ${scNo}'s cycle ${cycle}s is shorter than the minimum green + intergreen time sum of ${minSum}s. Check the current signal program and fix the cycle to at least ${minSum}s.`,
        `信号制御機${scNo}のサイクル ${cycle}秒 が最小青時間+インターグリーン時間の合計 ${minSum}秒 より短くなっています。現在の信号プログラムを確認し、サイクルを${minSum}秒以上に修正してください。`,
      );
    }
    case "ring_barrier_splits_sum_mismatch": {
      const scNo = targetStr(target, "sc_no");
      const cycle = targetStr(target, "cycle");
      const splitSum = targetStr(target, "split_sum");
      return t(
        `신호제어기 ${scNo}의 링-바리어 스플릿 합계는 ${splitSum}초인데 주기는 ${cycle}초입니다. 현재 신호 프로그램을 확인하고 주기와 스플릿 합이 일치하도록 수정해줘.`,
        `Signal controller ${scNo}'s ring-barrier splits sum to ${splitSum}s, but the cycle is ${cycle}s. Check the current signal program and fix it so the cycle matches the split sum.`,
        `信号制御機${scNo}のリングバリアスプリットの合計は${splitSum}秒ですが、サイクルは${cycle}秒です。現在の信号プログラムを確認し、サイクルとスプリット合計が一致するよう修正してください。`,
      );
    }
    case "green_split_outside_cycle": {
      const scNo = targetStr(target, "sc_no");
      const programNo = targetStr(target, "program_no");
      const sgNo = targetStr(target, "sg_no");
      const start = targetStr(target, "green_start_s");
      const end = targetStr(target, "green_end_s");
      const cycle = targetStr(target, "cycle_s");
      return t(
        `신호제어기 ${scNo}의 프로그램 ${programNo}, 신호그룹 ${sgNo}의 녹색시간 [${start}초→${end}초]이 주기 ${cycle}초를 벗어납니다. 현재 신호 프로그램을 확인하고 주기 안에 들어오도록 수정해줘.`,
        `Signal controller ${scNo} program ${programNo}, signal group ${sgNo}'s green window [${start}s→${end}s] falls outside the ${cycle}s cycle. Check the current signal program and fix it to fit within the cycle.`,
        `信号制御機${scNo}のプログラム${programNo}、信号グループ${sgNo}の青時間 [${start}秒→${end}秒] がサイクル${cycle}秒の範囲外です。現在の信号プログラムを確認し、サイクル内に収まるよう修正してください。`,
      );
    }
    case "green_split_below_min_green": {
      const scNo = targetStr(target, "sc_no");
      const programNo = targetStr(target, "program_no");
      const sgNo = targetStr(target, "sg_no");
      const duration = targetStr(target, "green_duration_s");
      const minGreen = targetStr(target, "min_green_s");
      return t(
        `신호제어기 ${scNo}의 프로그램 ${programNo}, 신호그룹 ${sgNo}의 녹색시간 ${duration}초가 최소 녹색시간 ${minGreen}초보다 짧습니다. 현재 신호 프로그램을 확인하고 최소 녹색시간 이상으로 수정해줘.`,
        `Signal controller ${scNo} program ${programNo}, signal group ${sgNo}'s green duration ${duration}s is below the minimum green ${minGreen}s. Check the current signal program and fix it to meet the minimum green.`,
        `信号制御機${scNo}のプログラム${programNo}、信号グループ${sgNo}の青時間 ${duration}秒 が最小青時間 ${minGreen}秒 を下回っています。現在の信号プログラムを確認し、最小青時間以上に修正してください。`,
      );
    }
    case "conflicting_greens_in_signal_plan": {
      const scNo = targetStr(target, "sc_no");
      const phaseA = targetStr(target, "phase_a_start");
      const phaseB = targetStr(target, "phase_b_start");
      return t(
        `신호제어기 ${scNo}의 현재 페이즈 플랜에서 시작 ${phaseA}초 페이즈와 시작 ${phaseB}초 페이즈가 겹쳐 직각 접근로에 동시에 녹색을 주고 있습니다. 인터그린 매트릭스를 확인하고 위험한지 알려줘.`,
        `Signal controller ${scNo}'s current phase plan overlaps the phase starting at ${phaseA}s with the one starting at ${phaseB}s, giving simultaneous green to perpendicular approaches. Check against the intergreen matrix and tell me if this is dangerous.`,
        `信号制御機${scNo}の現在のフェーズプランで、開始${phaseA}秒のフェーズと開始${phaseB}秒のフェーズが重なり、直交する進入路に同時に青を与えています。インターグリーンマトリックスを確認し、危険かどうか教えてください。`,
      );
    }
    case "units_ambiguous_on_write":
      return t(
        "이 네트워크의 단위(속도 등) 설정이 불완전하여 쓰기 시 단위가 모호합니다. 현재 단위 설정을 확인하고 어떤 위험이 있는지 알려줘.",
        "This network's unit settings (e.g. speed) are incomplete, making units ambiguous on write. Check the current unit settings and tell me what risk this poses.",
        "このネットワークの単位(速度など)設定が不完全なため、書き込み時に単位が曖昧です。現在の単位設定を確認し、どのようなリスクがあるか教えてください。",
      );
    case "stale_route_files_after_edit":
      return t(
        "동적배정(dynamic assignment) 경로 파일이 최근 편집 이후 오래된 상태일 수 있습니다. 현재 동적배정 설정을 확인하고 재계산이 필요한지 알려줘.",
        "The dynamic-assignment route files may be stale after a recent edit. Check the current dynamic-assignment settings and tell me whether they need to be recomputed.",
        "動的配分(dynamic assignment)の経路ファイルが最近の編集後に古くなっている可能性があります。現在の動的配分設定を確認し、再計算が必要か教えてください。",
      );
    case "conflict_area_data_unavailable": {
      const count = targetCount(target);
      const examples = targetExamples(target);
      return t(
        `충돌구간 ${count}개가 Passive 상태입니다 (예: ${examples}). 주요 교차로부터 확인하고 우선권 지정이 필요한지 알려줘.`,
        `${count} conflict area(s) are Passive (e.g. ${examples}). Check starting from the major intersections and tell me whether right-of-way needs to be assigned.`,
        `${count}件の錯綜区間がPassive状態です(例: ${examples})。主要な交差点から確認し、優先権の指定が必要か教えてください。`,
      );
    }
    case "route_integrity_unverifiable": {
      const count = targetCount(target);
      const examples = targetExamples(target);
      return t(
        `경로 ${count}개가 단절된 상태입니다 (예: ${examples}). 어느 경로인지 확인하고 복구가 필요한지 알려줘.`,
        `${count} route(s) are disconnected (e.g. ${examples}). Check which routes these are and tell me whether they need to be repaired.`,
        `経路${count}件が切断されています(例: ${examples})。どの経路か確認し、復旧が必要か教えてください。`,
      );
    }
    case "vehicle_input_cont_volume_mismatch": {
      const count = targetCount(target);
      const examples = targetExamples(target);
      return t(
        `차량 입력.구간 ${count}개가 연속(Cont) 표시로 인해 저장된 볼륨 값과 실제 적용되는 값이 다릅니다 (예: ${examples}). 어느 값이 실제로 적용되는지 확인하고 알려줘.`,
        `${count} vehicle-input.interval entries are marked continuous (Cont), so the stored volume differs from what actually applies (e.g. ${examples}). Check which value actually applies and tell me.`,
        `車両インプット.区間 ${count}件が連続(Cont)指定のため、保存されているボリューム値と実際に適用される値が異なります(例: ${examples})。実際に適用される値を確認して教えてください。`,
      );
    }
    case "des_speed_distribution_orphan_reference": {
      const count = targetCount(target);
      const examples = targetExamples(target);
      return t(
        `희망속도결정 항목 ${count}개가 존재하지 않는 희망속도분포를 참조하고 있습니다 (예: ${examples}). 어떤 분포를 대신 사용해야 할지 확인하고 알려줘.`,
        `${count} desired-speed decision entries reference a desired-speed distribution that doesn't exist (e.g. ${examples}). Check which distribution should be used instead and tell me.`,
        `希望速度決定エントリ${count}件が存在しない希望速度分布を参照しています(例: ${examples})。代わりにどの分布を使うべきか確認して教えてください。`,
      );
    }
    default:
      // Future-proofing: a dataset-checkable constraint this page has no
      // dedicated template for yet still gets a usable (if more generic)
      // grounded prompt built from its own display message, rather than a
      // button that produces an empty/broken handoff.
      return t(
        `"${finding.title.ko}": ${finding.message?.ko ?? ""} 확인하고 도와줘.`,
        `"${finding.title.en}": ${finding.message?.en ?? ""} Please look into this and help.`,
        `"${finding.title.ja}": ${finding.message?.ja ?? ""} 確認して手伝ってください。`,
      );
  }
}

function handoffButtonLabel(kind: HealthCheckFinding["suggested_action"]["kind"], t: Translate): string {
  return kind === "fix" ? t("AI로 수정하기", "Fix with AI", "AIで修正する") : t("AI에게 물어보기", "Ask the AI", "AIに聞く");
}

// Report-level handoff (optional deliverable): a single bounded summary --
// counts + up to 3 example titles, never the full findings list -- for a
// "review everything, help me prioritize" turn instead of one finding at a
// time.
function buildReportSummaryPrompt(report: HealthCheckReport, lang: "ko" | "en" | "ja", t: Translate): string {
  const { issue, warning, info } = report.summary.findings_by_severity;
  const actionable = report.findings.filter((f) => f.message !== null && (f.suggested_action?.kind ?? "none") !== "none");
  const exampleTitles = actionable.slice(0, 3).map((f) => f.title[lang]).join(", ");
  return t(
    `모델 검진 결과 이슈 ${issue}건, 경고 ${warning}건, 정보 ${info}건이 발견되었습니다 (예: ${exampleTitles}). 우선순위를 정해서 어디부터 확인해야 할지 알려줘.`,
    `The model health check found ${issue} issue(s), ${warning} warning(s), and ${info} info item(s) (e.g. ${exampleTitles}). Help me prioritize where to start.`,
    `モデル検診の結果、問題${issue}件、警告${warning}件、情報${info}件が見つかりました(例: ${exampleTitles})。優先順位をつけて、どこから確認すべきか教えてください。`,
  );
}

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
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">
          {t("모델 검진", "Model Health Check", "モデル検診")}
        </h1>
        <p className="mt-1 text-[15px] text-slate-600">
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
          className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-[15px] font-semibold text-ink transition hover:bg-slate-950 disabled:cursor-wait disabled:opacity-60"
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
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-surface px-3.5 py-2 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-100"
          >
            {t("JSON 다운로드", "Download JSON", "JSONダウンロード")}
          </a>
        )}
        {report && report.findings.some((f) => f.message !== null && (f.suggested_action?.kind ?? "none") !== "none") && (
          <button
            type="button"
            onClick={() => requestAgentHandoff(buildReportSummaryPrompt(report, lang, t))}
            className="inline-flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-3.5 py-2 text-[14px] font-semibold text-accent-deep transition hover:border-accent/60 hover:bg-accent/15"
          >
            {t("AI와 함께 검토하기", "Review with AI", "AIと一緒に確認する")}
          </button>
        )}
        {report && (
          <span className="text-[14px] text-slate-500">
            {t("생성 시각", "Generated", "生成時刻")}: {formatTimestamp(report.generated_at, lang)}
            {" · "}
            {report.dataset.name}
            {report.dataset.revision ? ` (${report.dataset.revision.slice(0, 12)})` : ""}
          </span>
        )}
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[15px] text-rose-700">
          {t("검진을 실행하지 못했습니다", "Failed to run the health check", "検診を実行できませんでした")}: {error}
        </div>
      )}

      {!report && !loading && !error && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center text-[15px] text-slate-500">
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
                  <p className="text-[14px] font-semibold uppercase tracking-wide text-slate-500">
                    {severityLabel(severity, t)}
                  </p>
                  <p className={`mt-1 text-2xl font-bold tabular-nums ${toneTextClass(tone)}`}>{count}</p>
                </div>
              );
            })}
          </div>

          <p className="mb-4 text-[14px] text-slate-500">
            {t(
              `데이터셋 검사 가능 제약 ${report.summary.checked_n}개 확인 완료 · 편집/실행 시에만 검사되는 제약 ${report.summary.edit_time_only_n}개는 아래에 별도로 표시됩니다.`,
              `${report.summary.checked_n} dataset-scope constraint(s) checked · ${report.summary.edit_time_only_n} constraint(s) that can only be checked at edit/run time are listed separately below.`,
              `データセット検査可能な制約 ${report.summary.checked_n}件を確認済み · 編集/実行時のみ検査される制約 ${report.summary.edit_time_only_n}件は下に別途表示されます。`,
            )}
          </p>

          {report.findings.length === 0 ? (
            <div className="mb-6 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-6 text-center text-[15px] text-emerald-700">
              {t("발견된 이슈가 없습니다.", "No issues found.", "検出された問題はありません。")}
            </div>
          ) : (
            <div className="mb-6 space-y-6">
              {SEVERITY_ORDER.filter((s) => (findingsBySeverity.get(s) ?? []).length > 0).map((severity) => (
                <section key={severity}>
                  <h2 className="mb-2 text-[15px] font-bold text-slate-900">
                    {severityLabel(severity, t)} ({(findingsBySeverity.get(severity) ?? []).length})
                  </h2>
                  <ul className="space-y-2">
                    {(findingsBySeverity.get(severity) ?? []).map((finding, index) => {
                      const key = `${severity}-${index}`;
                      const globalIndex = report.findings.indexOf(finding);
                      const isOpen = !!expanded[globalIndex];
                      // Defensive fallback, not just a type-level assumption: an
                      // older backend process (no live hot-reload for FastAPI --
                      // see .claude/launch.json) can still be serving a report
                      // shaped before suggested_action existed. Treat that as
                      // "no button" rather than crashing the whole findings list.
                      const action = finding.suggested_action ?? { kind: "none" as const, tool: null, target: {} };
                      if (finding.message === null) {
                        return (
                          <li
                            key={key}
                            className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3.5 py-2.5 text-[14px] text-slate-500"
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
                                  className={`rounded-full px-2 py-0.5 text-[12px] font-semibold ${toneBadgeClass(SEVERITY_TONE[finding.severity])}`}
                                >
                                  {finding.constraint_id}
                                </span>
                                <h3 className="text-[15px] font-bold text-slate-900">{finding.title[lang]}</h3>
                              </div>
                              <p className="mt-1 text-[15px] text-slate-700">{finding.message[lang]}</p>
                              {finding.object_refs.length > 0 && (
                                <p className="mt-1 font-mono text-[12px] text-slate-400">
                                  {finding.object_refs.join(", ")}
                                </p>
                              )}
                            </div>
                            <div className="flex flex-none items-center gap-1.5">
                              {/* AI handoff (2026-07-28): only rendered for kind="fix"/"inspect" --
                                  kind="none" (e.g. evaluation_config_disarmed, a documented
                                  always-refuse with no write tool at all) shows no button, since
                                  the message above already explains it and routing a user into a
                                  guaranteed refusal would be worse than nothing. Mirrors
                                  CurationBar.tsx's own handoff button styling so the two surfaces
                                  read as one family. */}
                              {action.kind !== "none" && (
                                <button
                                  type="button"
                                  onClick={() => requestAgentHandoff(buildHandoffPrompt(finding, t))}
                                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[13px] font-semibold transition ${
                                    action.kind === "fix"
                                      ? "border-accent/40 bg-accent/10 text-accent-deep hover:border-accent/60 hover:bg-accent/15"
                                      : "border-slate-200 bg-surface text-slate-500 hover:border-slate-300 hover:text-slate-800"
                                  }`}
                                >
                                  {handoffButtonLabel(action.kind, t)}
                                  <span aria-hidden>→</span>
                                </button>
                              )}
                              {Object.keys(finding.details).length > 0 && (
                                <button
                                  type="button"
                                  onClick={() => toggleExpanded(globalIndex)}
                                  className="rounded-lg border border-slate-300 bg-surface px-2.5 py-1 text-[13px] font-semibold text-slate-600 transition hover:bg-slate-100"
                                >
                                  {isOpen ? t("접기", "Hide details", "閉じる") : t("상세", "Details", "詳細")}
                                </button>
                              )}
                            </div>
                          </div>
                          {isOpen && (
                            /* globals.css PALETTE LOCK inverts the slate ramp for this
                               dark theme (slate-950 IS white, slate-200 is 10%-opacity
                               white), so the conventional bg-slate-950/text-slate-200
                               code block renders white-on-white. Wells use slate-50/100;
                               body text rides the slate-600..800 ramp. */
                            <pre className="mt-2 overflow-x-auto rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[13px] text-slate-700">
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
            <summary className="cursor-pointer text-[15px] font-bold text-slate-700">
              {t("편집 시 검사", "Checked at edit time", "編集時に検査")} ({report.edit_time_only.length})
            </summary>
            <p className="mt-2 text-[14px] text-slate-500">
              {t(
                "아래 제약들은 실제 편집(파라미터)이나 실행 결과가 있어야 평가할 수 있어, 이 정적 검진에는 포함되지 않습니다. AI 에이전트가 편집을 제안하거나 시뮬레이션을 실행할 때 자동으로 검사됩니다.",
                "These constraints need a proposed edit's parameters or real run results to evaluate, so this static check cannot cover them. They are checked automatically whenever the AI agent proposes an edit or runs a simulation.",
                "以下の制約は実際の編集パラメータや実行結果がないと評価できないため、この静的検診には含まれません。AIエージェントが編集を提案したりシミュレーションを実行したりする際に自動的に検査されます。",
              )}
            </p>
            <ul className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-[14px] text-slate-600 sm:grid-cols-2">
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
            <h2 className="mb-2 text-[14px] font-bold uppercase tracking-wide text-slate-500">
              {t("데이터셋 통계", "Dataset stats", "データセット統計")}
            </h2>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 md:grid-cols-4">
              {(Object.keys(STAT_LABELS) as Array<keyof HealthCheckReport["stats"]>).map((key) => (
                <div key={key}>
                  <p className="text-[13px] text-slate-500">{STAT_LABELS[key][lang]}</p>
                  <p className="text-[15px] font-bold tabular-nums text-slate-900">{report.stats[key]}</p>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

"use client";

import { useCallback, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { curateDashboard, type DashboardCurationSpec } from "@/lib/api";

const TONE_STYLES: Record<string, string> = {
  success: "border-[#cfe3d4] bg-[#f2f8f3] text-[#2f5a3d]",
  warning: "border-[#ecd9bb] bg-[#fdf6ea] text-[#8a5a1f]",
  info: "border-slate-200 bg-slate-50 text-slate-700",
};

// Mirrors backend/services/chart_catalog.py ids -> short display labels, so
// an insight card's related_chart can render as a jump link to the section
// that is its actual evidence, instead of leaving the reader to search for it.
const CHART_LABELS: Record<string, { ko: string; en: string }> = {
  "map-selection": { ko: "지도", en: "Map" },
  "intersection-performance": { ko: "교차로 성능평가", en: "Intersection performance" },
  "travel-time-comparison": { ko: "통행시간 비교", en: "Travel time" },
  "network-kpi-comparison": { ko: "네트워크 KPI", en: "Network KPI" },
  "overall-results-analysis": { ko: "종합 결과 분석", en: "Overall results" },
  "field-count-validation": { ko: "현장 계수 검증", en: "Field count validation" },
};

interface CurationBarProps {
  selectedScenarioIds: string[];
  selectedNodeIds: string[];
  disabled: boolean;
  spec: DashboardCurationSpec | null;
  onSpecChange: (spec: DashboardCurationSpec | null) => void;
  /** Section ids that would render right now if no question were asked
   * (i.e. pass their own data gates) — used to report how many were hidden
   * by the current answer, and to explain when the model picked a section
   * whose data isn't actually available. */
  applicableSectionIds: string[];
}

export default function CurationBar({
  selectedScenarioIds,
  selectedNodeIds,
  disabled,
  spec,
  onSpecChange,
  applicableSectionIds,
}: CurationBarProps) {
  const { t, lang } = useLanguage();
  const [message, setMessage] = useState("");
  const [audience, setAudience] = useState<"plain" | "expert">("plain");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(async () => {
    const question = message.trim();
    if (!question || loading || disabled) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const result = await curateDashboard(
        {
          message: question,
          selected_scenario_ids: selectedScenarioIds,
          selected_node_ids: selectedNodeIds,
          response_language: lang,
          audience,
        },
        controller.signal,
      );
      if (result.fallback) {
        // A fallback spec's chart list is a generic default, not "what was
        // on screen a moment ago" — applying it as focus mode would silently
        // hide sections (e.g. field-count-validation) the user could already
        // see. Show the AI could not answer and leave everything visible
        // instead of half-trusting a spec we know is not a real answer.
        onSpecChange(null);
        setError(
          t(
            "AI가 질문을 해석하지 못해 전체 화면을 그대로 표시합니다. 다르게 질문해 보세요.",
            "The AI could not interpret the question, so everything is still shown. Try rephrasing.",
          ),
        );
      } else {
        onSpecChange(result);
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (abortRef.current === controller) {
        setLoading(false);
      }
    }
  }, [message, loading, disabled, selectedScenarioIds, selectedNodeIds, lang, audience, onSpecChange, t]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setLoading(false);
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setLoading(false);
    setError(null);
    onSpecChange(null);
  }, [onSpecChange]);

  const hiddenSectionIds = spec ? applicableSectionIds.filter((id) => !spec.charts.includes(id)) : [];
  const chartLabel = (id: string) => CHART_LABELS[id]?.[lang === "ja" ? "en" : lang] ?? id;
  // The model chose a section whose data isn't actually available right now
  // (currently only possible for field-count-validation, gated on an
  // uploaded observed-counts workbook in addition to hasData).
  const unavailableChosenIds = spec ? spec.charts.filter((id) => !applicableSectionIds.includes(id)) : [];

  return (
    <section className="rounded-2xl border border-slate-300/70 bg-white/90 px-5 py-4 shadow-[0_10px_24px_rgba(15,23,42,0.08)] backdrop-blur">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-[260px] flex-1 items-center gap-2 rounded-xl border border-slate-300/70 bg-white px-3 py-2">
          <span aria-hidden className="text-base">✨</span>
          <input
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) submit();
            }}
            placeholder={t(
              "궁금한 점을 물어보세요 — 관련 지표만 보여드립니다 (예: 신호 최적화가 효과 있었나요?)",
              "Ask a question — only the relevant charts will be shown (e.g. Did signal optimization help?)",
              "質問してください — 関連する指標のみ表示します",
            )}
            disabled={disabled || loading}
            className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 disabled:opacity-60"
            aria-label={t("대시보드 질문 입력", "Dashboard question input")}
          />
        </div>
        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded-lg border border-slate-300/70 text-xs font-medium">
            <button
              type="button"
              onClick={() => setAudience("plain")}
              className={`px-3 py-2 transition ${audience === "plain" ? "bg-[#31517a] text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}
            >
              {t("쉬운 설명", "Plain", "やさしい説明")}
            </button>
            <button
              type="button"
              onClick={() => setAudience("expert")}
              className={`px-3 py-2 transition ${audience === "expert" ? "bg-[#31517a] text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}
            >
              {t("전문가", "Expert", "専門家")}
            </button>
          </div>
          {loading ? (
            <button
              type="button"
              onClick={cancel}
              className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-700 transition hover:bg-rose-100"
            >
              {t("취소", "Cancel", "キャンセル")}
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={disabled || !message.trim()}
              className="rounded-lg bg-[#31517a] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[#27415f] disabled:opacity-50"
            >
              {t("질문하기", "Ask", "質問する")}
            </button>
          )}
          {spec && !loading && (
            <button
              type="button"
              onClick={reset}
              className="rounded-lg border border-slate-300/70 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            >
              {t("전체 보기", "Show everything", "すべて表示")}
            </button>
          )}
        </div>
      </div>

      {loading && (
        <p className="mt-2 text-[11px] text-slate-500">
          {t("보통 10~20초 걸립니다…", "This usually takes 10-20 seconds…", "通常10〜20秒かかります…")}
        </p>
      )}

      {error && (
        <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>
      )}

      {spec && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {hiddenSectionIds.length > 0 && (
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-600">
                {t(
                  `${applicableSectionIds.length}개 중 ${applicableSectionIds.length - hiddenSectionIds.length}개 섹션만 표시 중`,
                  `Showing ${applicableSectionIds.length - hiddenSectionIds.length} of ${applicableSectionIds.length} sections`,
                  `${applicableSectionIds.length}件中${applicableSectionIds.length - hiddenSectionIds.length}件のセクションのみ表示中`,
                )}
              </span>
            )}
          </div>

          {spec.headline && (
            <p className="text-sm font-semibold leading-relaxed text-slate-900">{spec.headline}</p>
          )}

          {unavailableChosenIds.length > 0 && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {t(
                `"${unavailableChosenIds.map(chartLabel).join(", ")}"이(가) 관련 있지만, 필요한 데이터가 없어 표시할 수 없습니다.`,
                `"${unavailableChosenIds.map(chartLabel).join(", ")}" is relevant, but the data it needs isn't available to show.`,
              )}
            </p>
          )}

          {spec.insight_cards.length > 0 && (
            <div className="grid gap-3 md:grid-cols-2">
              {spec.insight_cards.map((card, index) => (
                <div
                  key={`${card.title}-${index}`}
                  className={`rounded-xl border px-4 py-3 ${TONE_STYLES[card.tone] ?? TONE_STYLES.info}`}
                >
                  <p className="text-sm font-semibold">{card.title}</p>
                  <p className="mt-1 text-sm leading-relaxed">{card.body}</p>
                  {card.related_chart && applicableSectionIds.includes(card.related_chart) && (
                    <a
                      href={`#${card.related_chart}`}
                      className="mt-2 inline-block text-xs font-semibold underline decoration-dotted underline-offset-2 opacity-80 hover:opacity-100"
                    >
                      {t(`${chartLabel(card.related_chart)} 보기 →`, `View ${chartLabel(card.related_chart)} →`)}
                    </a>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

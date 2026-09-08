"use client";

import { useCallback, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { curateDashboard, type DashboardCurationSpec, type DashboardInsightCard } from "@/lib/api";

// Matches FloatingAssistant.tsx's local alias for useLanguage()'s `t` --
// kept as a local type (not exported from LanguageProvider) for the same
// reason that file defines its own copy: this is the shape callers need,
// not a shared contract worth centralizing for two call sites.
type Translate = (ko: string, en: string, ja?: string) => string;

// Handoff to the full agent (FloatingAssistant, globally mounted in
// AppShell — see components/AppShell.tsx) via the same custom-window-event
// seam FloatingAssistant already uses for page context (its
// "vissim:page-context" listener in components/FloatingAssistant.tsx).
// CurationBar never imports or renders the assistant directly; it only
// announces the request and lets the listener open the panel and prefill
// the input. Deliverable 3 of the CurationBar/FloatingAssistant identity
// split (docs/phase2/DECISIONS.md UX-reorg entry): this bar stays a
// view-control surface, never a chat, but still offers a one-click bridge
// into the real conversational/action surface.
const ASSISTANT_HANDOFF_EVENT = "vissim:assistant-handoff";

// Live-demo fix (2026-07-27): this used to hardcode a Korean prompt
// regardless of the active UI language, so an en/ja user clicking the
// handoff button got a Korean composer prefill. Routed through the same
// t()/useLanguage() plumbing every other user-facing string in this file
// already uses -- `t` is CurationBar's own hook value, passed in rather than
// read from a module-level `useLanguage()` call (this function lives outside
// the component so it stays a plain, testable helper).
function buildHandoffPrompt(card: Pick<DashboardInsightCard, "title">, t: Translate): string {
  return t(
    `"${card.title}"에 대해 자세히 설명해줘. 필요하면 조치도 도와줘.`,
    `Tell me more about "${card.title}" and help me act on it if needed.`,
    `"${card.title}"について詳しく教えてください。必要であれば対応もお手伝いします。`,
  );
}

function requestAgentHandoff(card: Pick<DashboardInsightCard, "title">, t: Translate) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_HANDOFF_EVENT, { detail: { prompt: buildHandoffPrompt(card, t) } }));
}

const TONE_STYLES: Record<string, string> = {
  success: "border-emerald-200 bg-emerald-50 text-emerald-600",
  warning: "border-amber-300 bg-amber-50 text-amber-700",
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
  /** Lifted to the page so the SAME toggle also controls how much technical
   * detail (stdev, scaling factors, GEH) the charts below show — previously
   * this only changed the wording of AI-generated text, leaving every chart
   * exactly as jargon-heavy in "Plain" mode as in "Expert" mode. */
  audience: "plain" | "expert";
  onAudienceChange: (audience: "plain" | "expert") => void;
}

export default function CurationBar({
  selectedScenarioIds,
  selectedNodeIds,
  disabled,
  spec,
  onSpecChange,
  applicableSectionIds,
  audience,
  onAudienceChange,
}: CurationBarProps) {
  const { t, lang } = useLanguage();
  const [message, setMessage] = useState("");
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
    <section className="rounded-xl border border-slate-200 bg-surface px-5 py-4">
      {/* Identity header: a "dashboard curation" / view-control label, not a
          chat title — CurationBar filters what's already on screen, it does
          not converse. Same eyebrow-label styling FloatingAssistant already
          uses for its own route-context summary, so the two surfaces read as
          one design family while staying visually distinct in role. */}
      <p className="mb-2 text-[13px] font-bold uppercase tracking-[0.12em] text-slate-400">
        {t("대시보드 큐레이션", "Dashboard curation", "ダッシュボードキュレーション")}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex h-10 min-w-[260px] flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 focus-within:border-slate-300">
          {/* Monochrome sparkle, not a chat/speech-bubble glyph — kept as a
              subtle "this ranking is AI-curated" cue rather than removed.
              It reads as "AI-powered filtering", not "conversation", so it
              stays even though the bar's identity is now explicitly
              non-chat; a speech-bubble icon would have to go. */}
          <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-slate-400" fill="currentColor">
            <path d="M12 2l1.9 6.3a3 3 0 0 0 1.8 1.8L22 12l-6.3 1.9a3 3 0 0 0-1.8 1.8L12 22l-1.9-6.3a3 3 0 0 0-1.8-1.8L2 12l6.3-1.9a3 3 0 0 0 1.8-1.8z" />
          </svg>
          <input
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) submit();
            }}
            placeholder={t(
              "무엇을 보고 싶으세요? — 관련 차트만 보여드립니다 (예: 신호 최적화가 효과 있었나요?)",
              "What would you like to see? — only the relevant charts will be shown (e.g. Did signal optimization help?)",
              "何を見たいですか？ — 関連するチャートのみ表示します",
            )}
            disabled={disabled || loading}
            className="w-full bg-transparent text-[15px] text-slate-800 outline-none placeholder:text-slate-400 disabled:opacity-60"
            aria-label={t("대시보드 큐레이션 입력", "Dashboard curation input")}
          />
        </div>
        <div className="flex items-center gap-2">
          <div className="flex h-10 items-center gap-0.5 rounded-lg border border-slate-200 p-1 text-[14px] font-medium">
            <button
              type="button"
              onClick={() => onAudienceChange("plain")}
              className={`flex h-full items-center rounded-md px-3 transition-colors ${audience === "plain" ? "bg-slate-200 text-slate-900" : "text-slate-500 hover:text-slate-800"}`}
            >
              {t("쉬운 설명", "Plain", "やさしい説明")}
            </button>
            <button
              type="button"
              onClick={() => onAudienceChange("expert")}
              className={`flex h-full items-center rounded-md px-3 transition-colors ${audience === "expert" ? "bg-slate-200 text-slate-900" : "text-slate-500 hover:text-slate-800"}`}
            >
              {t("전문가", "Expert", "専門家")}
            </button>
          </div>
          {loading ? (
            <button
              type="button"
              onClick={cancel}
              className="ui-button-ghost inline-flex h-10 items-center px-4 text-[15px]"
            >
              {t("취소", "Cancel", "キャンセル")}
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={disabled || !message.trim()}
              className="ui-button-primary inline-flex h-10 items-center px-4 text-[15px] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("보기", "View", "表示")}
            </button>
          )}
          {spec && !loading && (
            <button
              type="button"
              onClick={reset}
              className="ui-button-ghost inline-flex h-10 items-center px-4 text-[15px]"
            >
              {t("전체 보기", "Show everything", "すべて表示")}
            </button>
          )}
        </div>
      </div>

      {loading && (
        <p className="mt-2 text-[13px] text-slate-500">
          {t("보통 10~20초 걸립니다…", "This usually takes 10-20 seconds…", "通常10〜20秒かかります…")}
        </p>
      )}

      {error && (
        <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[14px] text-rose-700">{error}</p>
      )}

      {spec && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {hiddenSectionIds.length > 0 && (
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[13px] font-semibold text-slate-600">
                {t(
                  `${applicableSectionIds.length}개 중 ${applicableSectionIds.length - hiddenSectionIds.length}개 섹션만 표시 중`,
                  `Showing ${applicableSectionIds.length - hiddenSectionIds.length} of ${applicableSectionIds.length} sections`,
                  `${applicableSectionIds.length}件中${applicableSectionIds.length - hiddenSectionIds.length}件のセクションのみ表示中`,
                )}
              </span>
            )}
          </div>

          {spec.headline && (
            <p className="text-[15px] font-semibold leading-relaxed text-slate-900">{spec.headline}</p>
          )}

          {unavailableChosenIds.length > 0 && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[14px] text-amber-800">
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
                  <p className="text-[15px] font-semibold">{card.title}</p>
                  <p className="mt-1 text-[15px] leading-relaxed">{card.body}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    {card.related_chart && applicableSectionIds.includes(card.related_chart) && (
                      <a
                        href={`#${card.related_chart}`}
                        className="inline-block text-[14px] font-semibold underline decoration-dotted underline-offset-2 opacity-80 hover:opacity-100"
                      >
                        {t(`${chartLabel(card.related_chart)} 보기 →`, `View ${chartLabel(card.related_chart)} →`)}
                      </a>
                    )}
                    {/* Handoff affordance (deliverable 3): always shown, small
                        + ghost-styled so it never competes with the card's own
                        content — opens/focuses FloatingAssistant and prefills
                        a prompt (in the active UI language, ko/en/ja) derived
                        from this card's title via the vissim:assistant-handoff
                        window event above. */}
                    <button
                      type="button"
                      onClick={() => requestAgentHandoff(card, t)}
                      className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-surface px-2 py-1 text-[12px] font-semibold text-slate-500 transition hover:border-slate-300 hover:text-slate-800"
                    >
                      {t("에이전트에게 요청", "Ask the agent", "エージェントに依頼")}
                      <span aria-hidden>→</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

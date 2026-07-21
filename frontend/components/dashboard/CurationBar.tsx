"use client";

import { useCallback, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { curateDashboard, type DashboardCurationSpec } from "@/lib/api";

const TONE_STYLES: Record<string, string> = {
  success: "border-[#cfe3d4] bg-[#f2f8f3] text-[#2f5a3d]",
  warning: "border-[#ecd9bb] bg-[#fdf6ea] text-[#8a5a1f]",
  info: "border-slate-200 bg-slate-50 text-slate-700",
};

interface CurationBarProps {
  selectedScenarioIds: string[];
  selectedNodeIds: string[];
  disabled: boolean;
  spec: DashboardCurationSpec | null;
  onSpecChange: (spec: DashboardCurationSpec | null) => void;
}

export default function CurationBar({
  selectedScenarioIds,
  selectedNodeIds,
  disabled,
  spec,
  onSpecChange,
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
      onSpecChange(result);
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (abortRef.current === controller) {
        setLoading(false);
      }
    }
  }, [message, loading, disabled, selectedScenarioIds, selectedNodeIds, lang, audience, onSpecChange]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setLoading(false);
    setError(null);
    onSpecChange(null);
  }, [onSpecChange]);

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
          <button
            type="button"
            onClick={submit}
            disabled={disabled || loading || !message.trim()}
            className="rounded-lg bg-[#31517a] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[#27415f] disabled:opacity-50"
          >
            {loading ? t("분석 중…", "Analyzing…", "分析中…") : t("질문하기", "Ask", "質問する")}
          </button>
          {spec && (
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

      {error && (
        <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>
      )}

      {spec && (
        <div className="mt-4 space-y-3">
          {spec.headline && (
            <p className="text-sm font-semibold leading-relaxed text-slate-900">{spec.headline}</p>
          )}
          {spec.fallback && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {t(
                "AI 응답을 해석하지 못해 기본 화면을 표시합니다.",
                "The AI response could not be parsed; showing the default view.",
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
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

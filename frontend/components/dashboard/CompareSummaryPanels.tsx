"use client";

import {
  type CompareSummaryCard,
  type CompareSummaryInsight,
  type CompareSummaryRankingItem,
  type ScenarioMeta,
} from "@/lib/api";
import { getScenarioDisplayColor } from "@/lib/scenarioPalette";

const TONE_STYLES: Record<string, { border: string }> = {
  success: { border: "border-emerald-200" },
  good: { border: "border-emerald-200" },
  warning: { border: "border-amber-200" },
  error: { border: "border-rose-200" },
  critical: { border: "border-rose-200" },
  neutral: { border: "border-slate-200" },
};

function resolveTone(tone?: string): { border: string } {
  return TONE_STYLES[tone?.toLowerCase() ?? "neutral"] ?? TONE_STYLES.neutral;
}

function formatValue(value: number | string): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1);
  }
  return value;
}

function getScenarioMeta(scenarios: ScenarioMeta[], scenarioId: string) {
  return scenarios.find((scenario) => scenario.id === scenarioId);
}

export function SummaryCardsPanel({ cards }: { cards: CompareSummaryCard[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => {
        const toneStyle = resolveTone(card.tone);

        return (
          <article
            key={card.id}
            className={`rounded-xl border bg-surface p-4 shadow-sm ${toneStyle.border}`}
          >
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">{card.label}</p>
            <p className="mt-3 text-2xl font-semibold text-slate-900">
              {formatValue(card.value)}
              {card.unit ? <span className="ml-1 text-sm font-medium text-slate-500">{card.unit}</span> : null}
            </p>
            {card.description ? <p className="mt-1 text-xs text-slate-500">{card.description}</p> : null}
          </article>
        );
      })}
    </div>
  );
}

export function ScenarioRankingPanel({
  rankings,
  scenarios,
}: {
  rankings: CompareSummaryRankingItem[];
  scenarios: ScenarioMeta[];
}) {
  const ordered = [...rankings].sort((a, b) => {
    if (a.rank != null && b.rank != null) return a.rank - b.rank;
    if (a.rank != null) return -1;
    if (b.rank != null) return 1;
    return (b.score ?? Number.NEGATIVE_INFINITY) - (a.score ?? Number.NEGATIVE_INFINITY);
  });

  return (
    <div className="space-y-3">
      {ordered.map((item, index) => {
        const meta = getScenarioMeta(scenarios, item.scenario_id);
        const label = item.scenario_name ?? meta?.name ?? item.scenario_id;
        const color = item.color ?? (meta ? getScenarioDisplayColor(meta, index) : "#4a83c2");
        const scoreLabel =
          item.score_label ?? (item.score != null ? `${item.score.toFixed(1)} pts` : "No score");
        const rank = item.rank ?? index + 1;

        return (
          <article key={`${item.scenario_id}-${rank}`} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-slate-900 px-2 text-xs font-semibold text-ink">
                  {rank}
                </span>
                <span className="h-3 w-3 rounded-full border border-white/30" style={{ backgroundColor: color }} />
                <p className="text-sm font-semibold text-slate-900">{label}</p>
              </div>
              <p className="text-xs font-semibold text-slate-600">{scoreLabel}</p>
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function KeyInsightsPanel({
  insights,
  scenarios,
}: {
  insights: CompareSummaryInsight[];
  scenarios: ScenarioMeta[];
}) {
  return (
    <div className="space-y-3">
      {insights.map((insight) => {
        const toneStyle = resolveTone(insight.tone);
        const scenario = insight.scenario_id ? getScenarioMeta(scenarios, insight.scenario_id) : null;

        return (
          <article key={insight.id} className={`rounded-xl border bg-surface p-4 shadow-sm ${toneStyle.border}`}>
            <div>
              <p className="text-sm font-semibold text-slate-900">{insight.title}</p>
              <p className="mt-1 text-xs leading-5 text-slate-600">{insight.message}</p>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {scenario ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] text-slate-600">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: getScenarioDisplayColor(scenario) }} />
                  {scenario.name}
                </span>
              ) : null}
              {insight.metric ? (
                <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] text-slate-600">
                  {insight.metric}
                </span>
              ) : null}
              {insight.value != null ? (
                <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] text-slate-600">
                  {insight.value.toFixed(1)} {insight.unit ?? ""}
                </span>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

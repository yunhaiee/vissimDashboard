"use client";

import Link from "next/link";

import { useLanguage } from "@/components/LanguageProvider";
import {
  calibrationHelpEntries,
  genesisHelpEntries,
  jobHelpEntries,
  observationsHelpEntries,
  phasesHelpEntries,
  regionHelpEntries,
  type HelpEntry,
} from "@/lib/help-content";

/**
 * /docs -- the in-app documentation, reached from the book icon in the topbar.
 *
 * WHAT REPLACED WHAT. Four genesis screens each carried their own round `?`
 * beside their h1 (unit #20's `PageHelp`), opening a right-side drawer of the
 * same doctrine sections this page now renders. The drawers are retired and the
 * text is not: a reference a reader has to already be standing on the right
 * screen to open is a reference most readers never find, and four of them meant
 * the background of the product had four front doors and no table of contents.
 * The sentences come from lib/help-content.tsx unchanged -- this file decides
 * order and typography, never wording.
 *
 * A READING SURFACE, NOT A DASHBOARD. No cards, no panels, no boxes inside
 * boxes: headings, paragraphs, one measure narrow enough to read, and section
 * `id`s so a screen can deep-link straight at its own section later. The body
 * text is the UI font rather than the mono the drawer used -- mono was the right
 * voice for a 13px sentence quoted beside a control, and the wrong one for a
 * page of prose.
 *
 * STATIC. This page fetches nothing and states no measurement, exactly as the
 * drawers did not. `regionHelpEntries` is called with no corner cap, so it
 * renders the same we-have-not-been-told 64 the region screen falls back to --
 * the authoritative number is the server's, and it is the server's refusal that
 * enforces it.
 */
export default function DocsPage() {
  const { t } = useLanguage();

  const sections: Array<{ id: string; title: string; href: string; entries: HelpEntry[] }> = [
    {
      id: "genesis",
      title: t("새 생성", "New generation", "新規生成"),
      href: "/genesis",
      entries: genesisHelpEntries(t),
    },
    {
      id: "region",
      title: t("영역 추출", "Region extraction", "領域からの抽出"),
      href: "/genesis/region",
      entries: regionHelpEntries(t),
    },
    {
      id: "phases",
      title: t("현시도 확인", "Phase-diagram review", "現示図の確認"),
      href: "/genesis/phases",
      entries: phasesHelpEntries(t),
    },
    {
      id: "job",
      title: t("생성 상세·판정", "Generation detail & verdict", "生成の詳細・判定"),
      href: "/genesis",
      entries: jobHelpEntries(t),
    },
    {
      id: "observations",
      title: t("실측 관측", "Observed counts", "実測観測"),
      href: "/observations",
      entries: observationsHelpEntries(t),
    },
    // #50-d (user-confirmed 2026-08-20). The calibration workspace's doctrine
    // came here whole -- seven ⓘ tooltips, six per-station folded rules, the
    // empty-state explanation and the ⑫ guardrail machinery. It sits LAST
    // because it is the longest section and the one a reader arrives at with a
    // question already formed, not the one they browse first.
    {
      id: "calibration",
      title: t("보정 워크스페이스", "Calibration workspace", "補正ワークスペース"),
      href: "/calibration",
      entries: calibrationHelpEntries(t),
    },
  ];

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-10">
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          {t("문서", "Documentation", "ドキュメント")}
        </h1>
        <p className="mt-2 text-[15px] leading-relaxed text-slate-500">
          {t(
            "이 앱이 왜 이렇게 동작하는지 — 화면마다 한 절씩, 각 화면의 배경 설명을 모았습니다.",
            "Why this app behaves the way it does — the background for each screen, one section per screen.",
            "このアプリがなぜこのように動くのか — 画面ごとに一節、各画面の背景説明をまとめました。",
          )}
        </p>
      </header>

      {sections.map((section) => (
        <section key={section.id} id={section.id} className="mb-12">
          <h2 className="border-b border-slate-200 pb-2 text-[17px] font-semibold text-slate-900">
            {section.title}
          </h2>
          {/* The screen this section is about, so the documentation is a way IN
            * rather than a dead end. Muted, on its own line: it annotates the
            * heading, it is not a call to action. */}
          <p className="mt-2 text-[14px] text-slate-500">
            <Link href={section.href} className="underline">
              {t("화면으로 이동", "Go to the screen", "画面へ移動")}
            </Link>
          </p>
          <div className="mt-6 flex flex-col gap-7">
            {section.entries.map((entry) => (
              <article key={entry.title}>
                <h3 className="text-[15px] font-semibold text-slate-800">{entry.title}</h3>
                <p className="mt-2 text-[15px] leading-[1.85] text-slate-600">{entry.body}</p>
              </article>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

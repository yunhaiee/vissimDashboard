"use client";

import Link from "next/link";

import { useLanguage } from "@/components/LanguageProvider";

// Previously a stale link (e.g. the archived /network route) landed on an
// unstyled default Next.js 404 in English regardless of the active
// language toggle — this renders inside the same AppShell/LanguageProvider
// as every other page instead.
export default function NotFound() {
  const { t } = useLanguage();

  return (
    <div className="mx-auto max-w-lg px-4 py-24 text-center">
      <p className="text-[15px] font-semibold uppercase tracking-widest text-slate-400">404</p>
      <h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-900">
        {t("페이지를 찾을 수 없습니다", "Page not found", "ページが見つかりません")}
      </h1>
      <p className="mt-3 text-[15px] leading-relaxed text-slate-600">
        {t(
          "요청한 페이지가 없거나 이동되었습니다. 대시보드로 돌아가세요.",
          "The page you requested doesn't exist or has moved. Head back to the dashboard.",
          "リクエストされたページは存在しないか、移動しました。ダッシュボードに戻ってください。",
        )}
      </p>
      <Link
        href="/dashboard"
        className="ui-button-primary mt-6 inline-flex items-center px-4 py-2 text-[15px]"
      >
        {t("대시보드로 이동", "Go to dashboard", "ダッシュボードへ")}
      </Link>
    </div>
  );
}

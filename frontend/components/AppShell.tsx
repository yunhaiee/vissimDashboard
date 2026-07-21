"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import FloatingAssistant from "@/components/FloatingAssistant";
import { useLanguage } from "@/components/LanguageProvider";
import type { Language } from "@/lib/i18n";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { lang, setLang, t } = useLanguage();

  const links: Array<{ href: string; label: string }> = [
    { href: "/base-model", label: t("베이스모델 검증", "Base Model Validation") },
    { href: "/dashboard", label: t("대시보드", "Dashboard") },
    { href: "/downloads", label: t("데이터 다운로드", "Data Downloads") },
    { href: "/report", label: t("보고서 작성", "Report Builder", "レポート作成") },
  ];

  return (
    <>
      <nav className="app-topbar">
        <div
          className="app-lang-switch app-lang-in-topbar"
          role="group"
          aria-label={t("Language switch", "Language switch")}
        >
          <LangButton lang="ko" current={lang} onSelect={setLang} label="Korean" />
          <LangButton lang="en" current={lang} onSelect={setLang} label="English" />
          <LangButton lang="ja" current={lang} onSelect={setLang} label="Japanese" />
        </div>

        <div className="app-topbar-inner">
          <span className="app-brand">
            <span className="app-brand-mark" />
            VISSIM Platform
          </span>

          {links.map((item) => {
            const active = pathname === item.href;
            return (
              <Link key={item.href} href={item.href} className={`app-link ${active ? "app-link-active" : ""}`}>
                {item.label}
              </Link>
            );
          })}
        </div>
      </nav>

      <main className="app-content">{children}</main>
      <FloatingAssistant />
    </>
  );
}

function LangButton({
  lang,
  current,
  onSelect,
  label,
}: {
  lang: Language;
  current: Language;
  onSelect: (lang: Language) => void;
  label: string;
}) {
  const active = current === lang;
  return (
    <button
      type="button"
      onClick={() => onSelect(lang)}
      className={`app-lang-btn ${active ? "app-lang-btn-active" : ""}`}
    >
      {label}
    </button>
  );
}

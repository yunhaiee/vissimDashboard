"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import FloatingAssistant from "@/components/FloatingAssistant";
import { useLanguage } from "@/components/LanguageProvider";
import type { Language } from "@/lib/i18n";

function NavIcon({ name }: { name: "dashboard" | "network" | "validation" | "downloads" | "report" }) {
  const paths: Record<string, React.ReactNode> = {
    dashboard: (
      <>
        <path d="M3 10.5 12 3l9 7.5" />
        <path d="M5.5 9.5V21h4.75v-5.25h3.5V21h4.75V9.5" />
      </>
    ),
    network: (
      <>
        <circle cx="6" cy="6" r="2.4" />
        <circle cx="18" cy="8" r="2.4" />
        <circle cx="9" cy="18" r="2.4" />
        <path d="M8.1 6.8 15.8 7.9" />
        <path d="M7.2 8.1 8.3 15.7" />
        <path d="M16.4 9.9 10.8 16.3" />
      </>
    ),
    validation: (
      <>
        <path d="M12 3 5 5.8v5.4c0 4.4 2.9 7.4 7 8.8 4.1-1.4 7-4.4 7-8.8V5.8Z" />
        <path d="m9 11.6 2.1 2.1L15.2 9.6" />
      </>
    ),
    downloads: (
      <>
        <path d="M12 3v11.5" />
        <path d="m7.25 10.25 4.75 4.75 4.75-4.75" />
        <path d="M5 20.5h14" />
      </>
    ),
    report: (
      <>
        <path d="M6.5 2.75H14l4.5 4.5v14H6.5Z" />
        <path d="M14 2.75v4.5h4.5" />
        <path d="M9.5 12.5h5.5" />
        <path d="M9.5 16h5.5" />
      </>
    ),
  };
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {paths[name]}
    </svg>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { lang, setLang, t } = useLanguage();

  // Dashboard listed first: it's the landing page (root redirects here) and
  // the page most users need first; the more specialist calibration page
  // follows it instead of leading.
  const links: Array<{ href: string; label: string; icon: "dashboard" | "network" | "validation" | "downloads" | "report" }> = [
    { href: "/dashboard", label: t("대시보드", "Dashboard", "ダッシュボード"), icon: "dashboard" },
    { href: "/base-model", label: t("베이스모델 검증", "Base Model Validation", "ベースモデル検証"), icon: "validation" },
    { href: "/network", label: t("네트워크", "Network", "ネットワーク"), icon: "network" },
    { href: "/downloads", label: t("데이터 다운로드", "Data Downloads", "データダウンロード"), icon: "downloads" },
    { href: "/report", label: t("보고서 작성", "Report Builder", "レポート作成"), icon: "report" },
  ];

  return (
    <div className="app-frame">
      <aside className="app-sidebar">
        <Link href="/dashboard" className="app-side-brand">
          <span className="app-brand-mark" />
          VISSIM Platform
        </Link>

        <nav className="app-side-nav" aria-label={t("주 메뉴", "Main navigation")}>
          {links.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`app-side-link ${active ? "app-side-link-active" : ""}`}
              >
                <NavIcon name={item.icon} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="app-side-footer">
          <div className="app-lang-switch" role="group" aria-label={t("Language switch", "Language switch")}>
            <LangButton lang="ko" current={lang} onSelect={setLang} label="Korean" />
            <LangButton lang="en" current={lang} onSelect={setLang} label="English" />
            <LangButton lang="ja" current={lang} onSelect={setLang} label="Japanese" />
          </div>
        </div>
      </aside>

      <main className="app-content">
        <div className="app-content-inner">{children}</div>
      </main>
      <FloatingAssistant />
    </div>
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

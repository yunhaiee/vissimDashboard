"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import FloatingAssistant from "@/components/FloatingAssistant";
import { roleLabel, useAuth } from "@/components/AuthProvider";
import { useLanguage } from "@/components/LanguageProvider";
import { loginHref } from "@/lib/authRedirect";
import type { Language } from "@/lib/i18n";

function NavIcon({ name }: { name: "dashboard" | "network" | "network-diff" | "validation" | "downloads" | "report" | "health" | "admin" }) {
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
    // Two stacked panes with a change marker between them -- the side-by-side
    // reading the diff view is.
    "network-diff": (
      <>
        <rect x="3" y="4.5" width="7.5" height="15" rx="1.5" />
        <rect x="13.5" y="4.5" width="7.5" height="15" rx="1.5" />
        <path d="M5.5 9h2.5" />
        <path d="M16 9h2.5" />
        <path d="M16 13.5h2.5" />
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
    health: (
      <>
        <path d="M12 21s-7.5-4.6-9.5-9.2C1.2 8.4 3 5.5 6.2 5.2c2-.2 3.6 1 4.8 2.6C12.2 6.2 13.8 5 15.8 5.2c3.2.3 5 3.2 3.7 6.6C17.5 16.4 12 21 12 21Z" />
        <path d="M6.5 12h2.2l1.3-2.6 2 4.8 1.3-2.2h4.2" />
      </>
    ),
    // Two people and a key: accounts, and what they are allowed to open.
    admin: (
      <>
        <circle cx="9" cy="7.5" r="2.8" />
        <path d="M3.5 19.5c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
        <circle cx="18" cy="10" r="2" />
        <path d="M18 12v5.5" />
        <path d="M18 15.5h2" />
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
  const { user } = useAuth();

  // Dashboard listed first: it's the landing page (root redirects here) and
  // the page most users need first; the more specialist calibration page
  // follows it instead of leading.
  const links: Array<{ href: string; label: string; icon: "dashboard" | "network" | "network-diff" | "validation" | "downloads" | "report" | "health" | "admin" }> = [
    { href: "/dashboard", label: t("대시보드", "Dashboard", "ダッシュボード"), icon: "dashboard" },
    { href: "/base-model", label: t("베이스모델 검증", "Base Model Validation", "ベースモデル検証"), icon: "validation" },
    { href: "/network", label: t("네트워크", "Network", "ネットワーク"), icon: "network" },
    // Roadmap 2.4: sits directly under /network -- it reads the same object,
    // one snapshot pair at a time, and a reviewer reaches for it right after
    // looking at the network itself.
    { href: "/network-diff", label: t("네트워크 변경 비교", "Network Diff", "ネットワーク差分比較"), icon: "network-diff" },
    { href: "/health", label: t("모델 검진", "Model Health Check", "モデル検診"), icon: "health" },
    { href: "/downloads", label: t("데이터 다운로드", "Data Downloads", "データダウンロード"), icon: "downloads" },
    { href: "/report", label: t("보고서 작성", "Report Builder", "レポート作成"), icon: "report" },
  ];

  // Unit P④. COSMETIC ONLY: this hides a link a non-admin has no use for, it
  // does NOT gate anything. Every /api/admin route is guarded server-side by
  // one router-level require_role("admin"), and app/admin/page.tsx renders
  // whatever the server answers -- so a stale cached role here can at worst
  // show or hide a menu item, never grant or deny a capability. (Contrast the
  // approval card, where a client-side role guess would hide a BUTTON and is
  // therefore forbidden outright.)
  if (user?.role === "admin") {
    links.push({
      href: "/admin",
      label: t("사용자 관리", "Users & Access", "ユーザー管理"),
      icon: "admin",
    });
  }

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
          <AccountAffordance />
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

// Unit U9. Renders NOTHING while the first whoami() is in flight: showing a
// 로그인 link to somebody who is already signed in, for the fraction of a
// second before the answer lands, is a lie the user may act on. Anonymous and
// signed-in are the only two states that get pixels.
//
// The 로그인 link carries the current path as `next` (validated by
// lib/authRedirect.ts on both ends) so a sign-in returns the user to the page
// they were reading, not to the dashboard.
function AccountAffordance() {
  const { t } = useLanguage();
  const { user, status, logout } = useAuth();
  const pathname = usePathname();

  if (status === "loading") return null;

  if (!user) {
    return (
      <Link href={loginHref(pathname)} className="app-side-account-action">
        {t("로그인", "Sign in", "ログイン")}
      </Link>
    );
  }

  return (
    <div className="app-side-account">
      {/* display_name is what an operator typed for this person; username is
          the login id and the fallback when no display name was set. */}
      <span className="app-side-account-name" title={user.username}>
        {user.display_name || user.username}
      </span>
      <span className="app-side-account-role">{roleLabel(user.role, t)}</span>
      <button type="button" onClick={() => void logout()} className="app-side-account-action">
        {t("로그아웃", "Sign out", "ログアウト")}
      </button>
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

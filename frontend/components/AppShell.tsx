"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import FloatingAssistant from "@/components/FloatingAssistant";
import ProjectSelector from "@/components/ProjectSelector";
import { roleLabel, useAuth } from "@/components/AuthProvider";
import { useLanguage } from "@/components/LanguageProvider";
import { loginHref } from "@/lib/authRedirect";
import type { Language } from "@/lib/i18n";

type NavIconName =
  | "dashboard"
  | "network"
  | "genesis"
  | "network-diff"
  | "validation"
  | "downloads"
  | "report"
  | "health"
  | "admin";

type Translate = (ko: string, en: string, ja?: string) => string;

function NavIcon({ name }: { name: NavIconName }) {
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
    // A map pin turning into a road grid: an extract on one side, a built
    // network on the other -- what genesis does in one glyph.
    genesis: (
      <>
        <path d="M8.5 3.5c2.2 0 4 1.8 4 4 0 2.9-4 7-4 7s-4-4.1-4-7c0-2.2 1.8-4 4-4Z" />
        <circle cx="8.5" cy="7.5" r="1.3" />
        <path d="M14 8.5h6" />
        <path d="M14 12.5h6" />
        <path d="M17 8.5v12" />
        <path d="M11 20.5h9" />
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

/**
 * The consultant pipeline, as four stages, in the order the work happens.
 *
 * Unit #18 (variant B of docs/product/mockups/flow-stepper-mockup-2026-08-13.html,
 * approved 2026-08-13): the pipeline IS the navigation. The sidebar groups every
 * page under the stage it serves, and the stepper says which stage the page you
 * are looking at belongs to.
 */
type PipelineStage = "network" | "data" | "sim" | "report";

const PIPELINE_STAGES = ["network", "data", "sim", "report"] as const satisfies readonly PipelineStage[];

/**
 * Route -> stage. A CONSTANT, and deliberately the only input the stepper has.
 *
 * The mockup drew each step carrying a status ("published 1", "수요 ✓ · 신호
 * 대기", an emerald done-dot). Those are not shipped here and the omission is
 * the point: this app has no cheap, honest way to answer "is the 데이터 stage
 * finished for this project" -- it would take a per-workspace roll-up of demand
 * inputs, confirmed 현시도, real counts and gate verdicts, and a stepper that
 * guessed at it would be exactly the fabricated-progress lie the validation
 * doctrine exists to prevent. So the highlight is a fact about the URL and
 * nothing else, and every other step renders mono rather than "not done yet".
 *
 * Routes that are not a pipeline stage (/admin, /login) are absent on purpose;
 * `stageForPath` returns null and the stepper renders nothing at all, which is
 * honest for a screen that is not part of the pipeline.
 */
const ROUTE_STAGE: Readonly<Record<string, PipelineStage>> = {
  "/genesis": "network",
  // A sub-screen of 생성, reached from the hub rather than the sidebar -- it
  // belongs to the same stage even though it is not its own menu entry.
  "/genesis/region": "network",
  "/network": "network",
  "/network-diff": "network",
  "/genesis/demand": "data",
  "/genesis/phases": "data",
  "/dashboard": "sim",
  "/base-model": "sim",
  "/health": "sim",
  // Hidden from the sidebar since the assistant went floating, but the
  // dashboard itself cross-links it as "고급: 시뮬레이션 실행기" -- a screen the
  // app presents as an extension of ③ belongs to ③, menu entry or not.
  "/simulation": "sim",
  "/report": "report",
  "/downloads": "report",
};

function stageForPath(pathname: string): PipelineStage | null {
  return ROUTE_STAGE[pathname] ?? null;
}

function stageLabel(stage: PipelineStage, t: Translate): string {
  switch (stage) {
    case "network":
      return t("네트워크", "Network", "ネットワーク");
    case "data":
      return t("데이터", "Data", "データ");
    case "sim":
      return t("시뮬·보정", "Simulate & Calibrate", "シミュ・補正");
    case "report":
      return t("보고서", "Report", "レポート");
  }
}

type NavLink = { href: string; label: string; icon: NavIconName };
type NavGroup = {
  /** Doubles as the React key and the `aria-labelledby` id stem, so no hook is
   * needed inside the render loop. */
  id: string;
  /** null for the group that is not a pipeline stage (see 관리 below). */
  stage: PipelineStage | null;
  ordinal: string | null;
  title: string;
  links: NavLink[];
};

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { lang, setLang, t } = useLanguage();
  const { user } = useAuth();

  // Labels are shorter than the flat menu's were because the group header now
  // carries the noun: under "① 네트워크", "생성" says everything "네트워크 생성"
  // said. Routes are untouched -- this unit regroups and renames, it moves no
  // page.
  const groups: NavGroup[] = [
    {
      id: "network",
      stage: "network",
      ordinal: "①",
      title: t("네트워크", "Network", "ネットワーク"),
      links: [
        { href: "/genesis", label: t("생성", "Build", "生成"), icon: "genesis" },
        { href: "/network", label: t("네트워크 뷰", "Network View", "ネットワークビュー"), icon: "network" },
        // The mockup folded this into the view above as a "+비교" mode. That is
        // a change to the network PAGE, not to the menu, so the route keeps its
        // own entry here and the merge stays open (docs/ux-backlog.md item 8).
        { href: "/network-diff", label: t("변경 비교", "Diff", "変更比較"), icon: "network-diff" },
      ],
    },
    {
      id: "data",
      stage: "data",
      ordinal: "②",
      title: t("데이터", "Data", "データ"),
      // Promoted out of /genesis, which is the half of variant B that changes
      // what a consultant can find: both screens produce inputs the generation
      // form merely SELECTS, and while they were reachable only from the
      // genesis hub they read as sub-steps of building a network rather than as
      // the project's data.
      links: [
        { href: "/genesis/demand", label: t("수요", "Demand", "需要"), icon: "downloads" },
        { href: "/genesis/phases", label: t("신호 (현시도)", "Signals (현시도)", "信号 (現示図)"), icon: "validation" },
      ],
    },
    {
      id: "sim",
      stage: "sim",
      ordinal: "③",
      title: t("시뮬·보정", "Simulate & Calibrate", "シミュ・補正"),
      links: [
        { href: "/dashboard", label: t("대시보드", "Dashboard", "ダッシュボード"), icon: "dashboard" },
        // Not placed by the mockup, which drew only 대시보드 and 모델 검진 here.
        // Filed under ③ because validating the base model against real counts
        // is what "보정" names -- it reads the same runs the dashboard does.
        { href: "/base-model", label: t("베이스모델 검증", "Base Model Validation", "ベースモデル検証"), icon: "validation" },
        { href: "/health", label: t("모델 검진", "Model Health Check", "モデル検診"), icon: "health" },
      ],
    },
    {
      id: "report",
      stage: "report",
      ordinal: "④",
      title: t("보고서", "Report", "レポート"),
      links: [
        { href: "/report", label: t("작성", "Builder", "作成"), icon: "report" },
        // The mockup absorbed 데이터 다운로드 into "작성 · 아티팩트". Absorbing it
        // into the report PAGE would delete a working screen, so it keeps its
        // entry inside the group the mockup put it in.
        { href: "/downloads", label: t("아티팩트", "Artifacts", "アーティファクト"), icon: "downloads" },
      ],
    },
  ];

  // Unit P④. COSMETIC ONLY: this hides a link a non-admin has no use for, it
  // does NOT gate anything. Every /api/admin route is guarded server-side by
  // one router-level require_role("admin"), and app/admin/page.tsx renders
  // whatever the server answers -- so a stale cached role here can at worst
  // show or hide a menu item, never grant or deny a capability. (Contrast the
  // approval card, where a client-side role guess would hide a BUTTON and is
  // therefore forbidden outright.)
  //
  // Unnumbered, and last: 사용자 관리 is not a pipeline stage, and giving it an
  // ordinal would claim it is one. For everybody except an admin the sidebar is
  // exactly the five blocks the mockup drew.
  if (user?.role === "admin") {
    groups.push({
      id: "admin",
      stage: null,
      ordinal: null,
      title: t("관리", "Administration", "管理"),
      links: [{ href: "/admin", label: t("사용자 관리", "Users & Access", "ユーザー管理"), icon: "admin" }],
    });
  }

  return (
    <div className="app-frame">
      <aside className="app-sidebar">
        <Link href="/dashboard" className="app-side-brand">
          <span className="app-brand-mark" />
          VISSIM Platform
        </Link>

        {/* Not on the sign-in screen: the adversarial pass found the picker
          * rendering ABOVE the login form (the anonymous workspace listing
          * legitimately returns the default project), and a screen about
          * acquiring an identity is the one place project scoping is noise. */}
        {pathname !== "/login" && <ProjectSelector />}

        <nav className="app-side-nav" aria-label={t("주 메뉴", "Main navigation")}>
          {groups.map((group, index) => (
            <div
              key={group.id}
              role="group"
              aria-labelledby={`app-side-group-${group.id}`}
              className={`flex flex-col gap-[2px] max-[880px]:flex-row max-[880px]:items-center max-[880px]:gap-1 ${
                index === 0 ? "" : "mt-3.5 max-[880px]:mt-0"
              }`}
            >
              <p
                id={`app-side-group-${group.id}`}
                className="px-1.5 pb-1 text-[11px] font-bold tracking-[0.04em] text-slate-400 max-[880px]:whitespace-nowrap max-[880px]:pb-0"
              >
                {group.ordinal ? `${group.ordinal} ${group.title}` : group.title}
              </p>
              {group.links.map((item) => {
                const active = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={`app-side-link ${active ? "app-side-link-active" : ""}`}
                  >
                    <NavIcon name={item.icon} />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          ))}
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
        <div className="app-content-inner">
          <PipelineStepper current={stageForPath(pathname)} />
          {children}
        </div>
      </main>
      <FloatingAssistant />
    </div>
  );
}

/**
 * The persistent stage indicator. Stateless by construction: it takes the stage
 * the route names and renders four labels, so there is nothing here that can
 * drift out of step with the project's real condition.
 *
 * Three-color rule (docs/ux-backlog.md, MOCKUP DECISIONS 2026-08-13): sky is
 * selection, so the current stage takes the same `border-sky-200 bg-sky-50
 * text-sky-600` the genesis screens use for their selected/info state -- the
 * foreground from the remapped LIGHT end of the family, which is the contrast
 * rule that navy fill needs. Every other stage is mono. No emerald: a green
 * step would read as "passed", and passing is a verdict this component has not
 * measured.
 */
function PipelineStepper({ current }: { current: PipelineStage | null }) {
  const { t } = useLanguage();

  if (!current) return null;

  return (
    <nav aria-label={t("파이프라인 단계", "Pipeline stage", "パイプライン段階")} className="mb-7 border-b border-slate-200 pb-3">
      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
        {PIPELINE_STAGES.map((stage, index) => {
          const active = stage === current;
          return (
            <li key={stage} className="flex items-center gap-1.5">
              {index > 0 && (
                <span aria-hidden className="text-slate-400">
                  ›
                </span>
              )}
              <span
                aria-current={active ? "step" : undefined}
                className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[12px] ${
                  active
                    ? "border-sky-200 bg-sky-50 font-semibold text-sky-600"
                    : "border-slate-200 text-slate-500"
                }`}
              >
                <span
                  aria-hidden
                  className={`h-[6px] w-[6px] rounded-full ${active ? "bg-sky-600" : "bg-slate-400"}`}
                />
                {stageLabel(stage, t)}
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
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
      <div className="app-side-account-ident">
        {/* display_name is what an operator typed for this person; username is
            the login id and the fallback when no display name was set. */}
        <span className="app-side-account-name" title={user.username}>
          {user.display_name || user.username}
        </span>
        <span className="app-side-account-role">{roleLabel(user.role, t)}</span>
      </div>
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

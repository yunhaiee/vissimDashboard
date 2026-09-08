"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { WorkspacePickerInline } from "@/components/ProjectSelector";
import {
  createWorkspace,
  fetchActiveDataset,
  fetchGenesisJobs,
  fetchSimulationJobs,
  fetchStagedSideInputs,
  fetchWorkspaces,
  GenesisError,
  setWorkspaceId,
  WorkspaceAccessError,
  type ActiveDataset,
  type GenesisJob,
  type GenesisJobStatus,
  type SimulationJob,
  type StagedSideInputEntry,
  type WorkspaceListResponse,
} from "@/lib/api";
import { formatTimestamp, statusLabel } from "@/lib/genesisArtifacts";

/**
 * 개요 (project overview) — unit #22 (2026-08-14 directive).
 *
 * The new front door: `app/page.tsx` redirects here instead of straight to
 * /dashboard, because the dashboard is stage ③ of the pipeline, not a place
 * that orients anybody to the PROJECT as a whole. This page composes four
 * facts the app already measures cheaply -- one per pipeline stage -- and
 * links each straight to the screen that owns it. It invents no number of its
 * own: every fetch below is an EXISTING helper from lib/api.ts, read exactly
 * as its own return shape already documents it.
 *
 * FRONTEND-ONLY, by the unit's own hard constraint: no backend route was
 * added for this page. Where no cheap existing helper answers a question
 * (④ 보고서's own count), the card links instead of inventing one.
 *
 * ONE LOADING STATE. All five fetches below race together (`Promise.
 * allSettled`) and the page renders nothing but a single muted line until
 * every one of them has settled -- never a grid where card A's real content
 * pops in while card C is still spinning. See the adversarial-review note
 * on `OverviewPage` below for why each card's ERROR state is also kept
 * distinct from its EMPTY state, rather than a catch-and-treat-as-zero.
 */

type Translate = (ko: string, en: string, ja?: string) => string;

// ---------------------------------------------------------------------------
// Small presentational pieces -- Console vocabulary, reused/mirrored from
// app/genesis/page.tsx rather than re-invented.
// ---------------------------------------------------------------------------

/** Mirrors app/genesis/page.tsx's own `statusClasses` (page-local there, not
 * exported): same `GenesisJobStatus` union, same three-colour chip mapping,
 * so a generation's status chip reads identically on 개요 and on the genesis
 * hub itself. A second small copy rather than a new cross-page dependency for
 * four lines of tailwind classes. */
function genesisStatusClasses(status: GenesisJobStatus): string {
  switch (status) {
    case "published":
      return "border-emerald-300 bg-emerald-50 text-emerald-800";
    case "provisional":
      return "border-slate-400 bg-slate-100 text-slate-900";
    case "refused":
    case "error":
      return "border-rose-300 bg-rose-50 text-rose-800";
    default:
      return "border-sky-200 bg-sky-50 text-sky-600";
  }
}

/** THE CONSOLE STAT -- muted label over a bold value. Mirrors app/genesis/
 * page.tsx's own page-local `Stat` (not exported there either) so the same
 * visual grammar reads identically here without a new shared-component
 * contract for four lines of markup. */
function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-[14px] text-slate-500">{label}</p>
      <p className="mt-0.5 truncate text-[15px] font-semibold text-slate-900">{value}</p>
    </div>
  );
}

/** One deep-link pill, the same `ui-button-ghost` pill chrome app/genesis/
 * page.tsx's own `CROSS_CHIP` cross-navigation links use -- reusing the
 * existing chip vocabulary rather than a new link style for this page. */
function StageLink({ href, label }: { href: string; label: string }) {
  return (
    <Link href={href} className="ui-button-ghost inline-flex rounded-full px-3 py-1 text-[14px] font-medium">
      {label}
    </Link>
  );
}

/**
 * THE TINTED-BOX RULE (app/genesis/region/page.tsx's own doctrine, restated
 * here because this page draws its own tinted notes): a tinted box states its
 * tint with its BACKGROUND and BORDER only. Its TEXT stays on the neutral ink
 * ramp -- never `text-rose-*`/`text-sky-*` -- so a box never goes blue-on-blue
 * or red-on-red.
 *
 * Three shapes, never a fourth conflated with another:
 *   "empty" -- a dashed border, the same "nothing here yet" box every genesis
 *     screen already uses for a real zero.
 *   "error" -- a solid rose border. NEVER used for a zero; only for a fetch
 *     that did not answer at all, so a network fault can never read as "0".
 *   "info"  -- a solid sky border (three-colour rule: sky = info/selection,
 *     never a fourth hue), for a fact this page could not observe for a
 *     reason that is not an error and not a zero -- today that is exactly
 *     ONE case, ② 데이터's 401 (see its own comment below).
 */
function StateNote({ tone, children }: { tone: "empty" | "error" | "info"; children: React.ReactNode }) {
  const classes =
    tone === "error"
      ? "border-rose-200 bg-rose-50 text-slate-800"
      : tone === "info"
        ? "border-sky-200 bg-sky-50 text-slate-800"
        : "border-dashed border-slate-300 bg-slate-50 text-slate-500";
  return <p className={`rounded-lg border px-3.5 py-3 text-[15px] leading-relaxed ${classes}`}>{children}</p>;
}

/** The card chrome every genesis screen's `Panel` (components/genesis/ui.tsx,
 * `density="console"`) already draws -- same border/shadow/radius/padding --
 * minus `Panel`'s own `mb-8`. `Panel` was built for STACKED sections in a
 * single column and always carries its own bottom margin for that; these four
 * cards sit side by side in a CSS grid instead, where the grid's own `gap`
 * already spaces them and a per-card margin would just uneven the row. A
 * second small copy of the chrome, not a `Panel` prop this page would be the
 * only caller of. */
function StageCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex h-full flex-col rounded-xl border border-slate-200 bg-slate-50 px-6 py-5 shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
      <h2 className="text-[15px] font-semibold text-slate-900">{title}</h2>
      <div className="mt-3 flex flex-1 flex-col gap-3">{children}</div>
    </section>
  );
}

function datasetSourceLabel(source: string | undefined, t: Translate): string {
  if (source === "uploaded") return t("업로드됨", "Uploaded", "アップロード済み");
  if (source === "default") return t("기본", "Default", "デフォルト");
  return source ?? "—";
}

// ---------------------------------------------------------------------------
// The no-project branch (unit #22, item 2) -- reuses ProjectSelector's own
// createWorkspace request, never a second copy of it.
// ---------------------------------------------------------------------------

function CreateProjectOnly() {
  const { t } = useLanguage();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createWorkspace(trimmed);
      // A freshly created project can never be "the default workspace" (the
      // backend always creates it as a named, non-default one), so this is
      // always a real id -- the one branch of ProjectSelector's own
      // `setWorkspaceId(isDefault ? null : workspaceId)` that can ever be true
      // here.
      setWorkspaceId(created.id);
      // A SWITCH IS A FULL RELOAD -- components/ProjectSelector.tsx's own
      // `handleSwitchWorkspace` doctrine (#18, 2026-08-13): every
      // workspace-scoped page in this app loads its data on mount with no
      // cross-page subscription, so tearing the page down is the one
      // mechanism that cannot leave a page stale once the project identity
      // underneath it changes. This page is no exception to that rule.
      window.location.reload();
    } catch (createError) {
      setError(
        createError instanceof Error
          ? createError.message
          : t("프로젝트를 만들지 못했습니다.", "Could not create the project.", "プロジェクトを作成できませんでした。"),
      );
      setBusy(false);
    }
  }, [name, t]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-slate-50 px-6 py-6 text-center shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
        <h1 className="text-[17px] font-semibold text-slate-900">
          {t("새 프로젝트 만들기", "Create a new project", "新規プロジェクトを作成")}
        </h1>
        <p className="mt-1.5 text-[15px] leading-relaxed text-slate-500">
          {t(
            "이 계정으로 볼 수 있는 프로젝트가 아직 없습니다. 이름을 정해 새 프로젝트를 만드세요.",
            "There is no project this account can see yet. Name one to create it.",
            "このアカウントで表示できるプロジェクトはまだありません。名前を付けて作成してください。",
          )}
        </p>
        <div className="mt-4 flex flex-col gap-2 text-left">
          <label htmlFor="overview-new-workspace-name" className="sr-only">
            {t("프로젝트 이름", "Project name", "プロジェクト名")}
          </label>
          <input
            id="overview-new-workspace-name"
            type="text"
            value={name}
            maxLength={120}
            placeholder={t("프로젝트 이름", "Project name", "プロジェクト名")}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void handleCreate();
            }}
            disabled={busy}
            className="ui-input px-2.5 py-2 text-[15px] disabled:cursor-not-allowed disabled:opacity-60"
          />
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={busy || !name.trim()}
            className="ui-button-primary px-3 py-2 text-[15px] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? t("만드는 중…", "Creating…", "作成中…") : t("만들기", "Create", "作成")}
          </button>
        </div>
        {error && <p className="mt-2 break-words text-[14px] leading-relaxed text-rose-600">{error}</p>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The normal overview
// ---------------------------------------------------------------------------

export default function OverviewPage() {
  const { t, lang } = useLanguage();

  const [loading, setLoading] = useState(true);

  const [workspaces, setWorkspaces] = useState<WorkspaceListResponse | null>(null);
  // "access" = a stored project id the server refuses (WorkspaceAccessError --
  // ProjectSelector.tsx's own self-heal is what actually fixes this, by
  // dropping the header and reloading; this page just has to not render a
  // stale or fabricated project meanwhile, so it takes the same no-project
  // branch as a genuinely empty listing). "error" = anything else (backend
  // unreachable, etc) -- kept SEPARATE from "access" because unlike a refused
  // project, this page has no evidence there is no project, so it must not
  // claim there is none.
  const [workspacesFailure, setWorkspacesFailure] = useState<"access" | "error" | null>(null);

  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [datasetError, setDatasetError] = useState(false);

  const [genesisJobs, setGenesisJobs] = useState<GenesisJob[]>([]);
  const [genesisJobsError, setGenesisJobsError] = useState(false);

  const [sideInputs, setSideInputs] = useState<StagedSideInputEntry[]>([]);
  // GET /api/genesis/side-inputs is the one read among this page's five that
  // requires a signed-in caller (backend/routers/genesis.py's own
  // `list_side_inputs` docstring) -- every other fetch on this page is
  // anonymous-OK for the default workspace. An anonymous visitor therefore
  // gets a NAMED "sign in to see this" note here, never a "0 수요 · 0 신호"
  // that would misreport a fact this request was never allowed to observe.
  const [sideInputsAuthRequired, setSideInputsAuthRequired] = useState(false);
  const [sideInputsError, setSideInputsError] = useState(false);

  const [simulationJobs, setSimulationJobs] = useState<SimulationJob[]>([]);
  const [simulationJobsError, setSimulationJobsError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    Promise.allSettled([
      fetchWorkspaces(),
      fetchActiveDataset(),
      fetchGenesisJobs(),
      fetchStagedSideInputs(),
      fetchSimulationJobs(),
    ]).then(([workspacesResult, datasetResult, jobsResult, sideInputsResult, simResult]) => {
      if (cancelled) return;

      if (workspacesResult.status === "fulfilled") {
        setWorkspaces(workspacesResult.value);
      } else {
        setWorkspacesFailure(workspacesResult.reason instanceof WorkspaceAccessError ? "access" : "error");
      }

      if (datasetResult.status === "fulfilled") {
        setActiveDataset(datasetResult.value);
      } else {
        setDatasetError(true);
      }

      if (jobsResult.status === "fulfilled") {
        setGenesisJobs(jobsResult.value);
      } else {
        setGenesisJobsError(true);
      }

      if (sideInputsResult.status === "fulfilled") {
        setSideInputs(sideInputsResult.value);
      } else if (sideInputsResult.reason instanceof GenesisError && sideInputsResult.reason.status === 401) {
        setSideInputsAuthRequired(true);
      } else {
        setSideInputsError(true);
      }

      if (simResult.status === "fulfilled") {
        setSimulationJobs(simResult.value);
      } else {
        setSimulationJobsError(true);
      }

      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  // ---- ONE loading state for the whole page (see file doc comment) --------
  if (loading) {
    return (
      <div className="mx-auto max-w-[1500px] px-4 py-8">
        <p className="text-[15px] text-slate-500">{t("불러오는 중…", "Loading…", "読み込み中…")}</p>
      </div>
    );
  }

  // Anonymous default-workspace sessions are NOT this state: services/
  // membership.visible_workspaces (backend/routers/workspaces.py's own
  // docstring) always returns the default workspace to an anonymous caller,
  // so `workspaces.workspaces` is never empty for one and `workspacesFailure`
  // is never "access" either (there is no stored, refusable project id to
  // refuse). Only a signed-in caller with zero memberships, or a stale
  // refused id, can ever reach this branch -- the default project always
  // gets the normal overview below.
  const noProject =
    workspacesFailure === "access" || (workspaces !== null && workspaces.workspaces.length === 0);

  // ---- The no-project branch (item 2): ONLY the create-project card -------
  if (noProject) {
    return <CreateProjectOnly />;
  }

  // fetchWorkspaces() itself failed for a reason that is NOT "no project" --
  // this page has no project name to show and must not invent one.
  if (!workspaces) {
    return (
      <div className="mx-auto max-w-[1500px] px-4 py-8">
        <StateNote tone="error">
          {t(
            "프로젝트 정보를 불러오지 못했습니다.",
            "Could not load project information.",
            "プロジェクト情報を読み込めませんでした。",
          )}
        </StateNote>
      </div>
    );
  }

  const demandCount = sideInputs.filter((entry) => entry.kind === "demand").length;
  const signalsCount = sideInputs.filter((entry) => entry.kind === "signals").length;

  const completedSimJobs = simulationJobs.filter((job) => job.status === "done");
  const latestCompletedSimJob = completedSimJobs[0] ?? null;

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-8">
      <header className="mb-8">
        {/* Unit #24 (2026-08-14 directive): the title is 개요 itself now --
          * this page's own name -- and the project it used to show moves to
          * the subtitle position as a SELECTABLE control: the same workspace
          * list and switch-semantics as the sidebar picker, one shared
          * implementation (components/ProjectSelector.tsx's own
          * `useWorkspacePicker`), never a second copy of the fetch/switch
          * logic written here. */}
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">
          {t("개요", "Overview", "概要")}
        </h1>
        <div className="mt-1.5">
          <WorkspacePickerInline id="overview-workspace-picker" />
        </div>
      </header>

      {/* Unit #24 (2026-08-14 directive): 2x2 at desktop, one column on
        * narrow screens -- was a 4-across row (sm:grid-cols-2 xl:grid-cols-4)
        * that only ever reached 2x2 below `xl`. Pipeline order is preserved
        * by DOM order alone (① ② / ③ ④ reading left-right, top-bottom is
        * exactly how four items lay out in a 2-column grid, unchanged). */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        {/* ------------------------------------------------------------- */}
        {/* ① 네트워크                                                     */}
        {/* ------------------------------------------------------------- */}
        <StageCard title={`① ${t("네트워크", "Network", "ネットワーク")}`}>
          {genesisJobsError ? (
            <StateNote tone="error">
              {t(
                "생성 이력을 불러오지 못했습니다.",
                "Could not load the generation history.",
                "生成履歴を読み込めませんでした。",
              )}
            </StateNote>
          ) : genesisJobs.length === 0 ? (
            <StateNote tone="empty">
              {t("아직 생성된 네트워크 없음", "No network generated yet", "まだ生成されたネットワークはありません")}
            </StateNote>
          ) : (
            <>
              <Stat
                label={t("활성 데이터셋", "Active dataset", "アクティブなデータセット")}
                value={
                  datasetError
                    ? t("불러오지 못함", "Could not load", "読み込めませんでした")
                    : `${activeDataset?.filename ?? "—"} (${datasetSourceLabel(activeDataset?.source, t)})`
                }
              />
              <div className="min-w-0">
                <p className="text-[14px] text-slate-500">{t("최근 생성", "Latest generation", "最新の生成")}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="truncate text-[15px] font-medium text-slate-800">{genesisJobs[0].name}</span>
                  <span
                    className={`shrink-0 whitespace-nowrap rounded-full border px-2 py-0.5 text-[13px] font-semibold ${genesisStatusClasses(genesisJobs[0].status)}`}
                  >
                    {statusLabel(genesisJobs[0].status, t)}
                  </span>
                </div>
              </div>
            </>
          )}
          <div className="mt-auto flex flex-wrap gap-2 pt-1">
            <StageLink
              href="/genesis"
              label={genesisJobs.length === 0 ? t("생성하기 →", "Build →", "生成する →") : t("생성 이력 보기 →", "View history →", "生成履歴を見る →")}
            />
          </div>
        </StageCard>

        {/* ------------------------------------------------------------- */}
        {/* ② 데이터                                                       */}
        {/* ------------------------------------------------------------- */}
        <StageCard title={`② ${t("데이터", "Data", "データ")}`}>
          {sideInputsAuthRequired ? (
            <StateNote tone="info">
              {t(
                "로그인하면 등록된 데이터를 볼 수 있습니다.",
                "Sign in to see this project's staged data.",
                "ログインするとステージング済みデータを確認できます。",
              )}
            </StateNote>
          ) : sideInputsError ? (
            <StateNote tone="error">
              {t("목록을 불러오지 못했습니다.", "Could not load the list.", "リストを読み込めませんでした。")}
            </StateNote>
          ) : demandCount === 0 && signalsCount === 0 ? (
            <StateNote tone="empty">
              {t("아직 등록된 데이터 없음", "No data staged yet", "まだ登録されたデータはありません")}
            </StateNote>
          ) : (
            <Stat
              label={t("스테이징된 입력", "Staged inputs", "ステージング済み入力")}
              value={`${t("수요", "Demand", "需要")} ${demandCount} · ${t("신호", "Signals", "信号")} ${signalsCount}`}
            />
          )}
          <div className="mt-auto flex flex-wrap gap-2 pt-1">
            <StageLink href="/genesis/demand" label={t("수요 →", "Demand →", "需要 →")} />
            <StageLink href="/genesis/phases" label={t("현시도 →", "Signals →", "現示図 →")} />
          </div>
        </StageCard>

        {/* ------------------------------------------------------------- */}
        {/* ③ 시뮬·보정                                                    */}
        {/* ------------------------------------------------------------- */}
        <StageCard title={`③ ${t("시뮬·보정", "Simulate & Calibrate", "シミュ・補正")}`}>
          {simulationJobsError ? (
            <StateNote tone="error">
              {t(
                "실행 이력을 불러오지 못했습니다.",
                "Could not load the run history.",
                "実行履歴を読み込めませんでした。",
              )}
            </StateNote>
          ) : completedSimJobs.length === 0 ? (
            <StateNote tone="empty">
              {t("아직 완료된 시뮬레이션 없음", "No completed simulation runs yet", "まだ完了したシミュレーションはありません")}
            </StateNote>
          ) : (
            <>
              <Stat
                label={t("완료된 실행", "Completed runs", "完了した実行")}
                value={t(`${completedSimJobs.length}건`, `${completedSimJobs.length}`, `${completedSimJobs.length}件`)}
              />
              <div className="min-w-0">
                <p className="text-[14px] text-slate-500">{t("최근 실행", "Most recent run", "最新の実行")}</p>
                <p className="mt-1 truncate text-[15px] font-medium text-slate-800">
                  {latestCompletedSimJob?.name || latestCompletedSimJob?.id}
                </p>
                <p className="mt-0.5 font-mono text-[14px] text-slate-500">
                  {formatTimestamp(latestCompletedSimJob?.completed_at ?? latestCompletedSimJob?.created_at ?? null, lang)}
                </p>
              </div>
            </>
          )}
          <div className="mt-auto flex flex-wrap gap-2 pt-1">
            <StageLink href="/dashboard" label={t("대시보드로 이동 →", "Go to dashboard →", "ダッシュボードへ →")} />
          </div>
        </StageCard>

        {/* ------------------------------------------------------------- */}
        {/* ④ 보고서 -- link-only: no existing helper answers a cheap count  */}
        {/* for "how many reports" without scanning artifacts, and this    */}
        {/* unit's hard constraint forbids adding one. See file doc        */}
        {/* comment. */}
        {/* ------------------------------------------------------------- */}
        <StageCard title={`④ ${t("보고서", "Report", "レポート")}`}>
          <StateNote tone="empty">
            {t(
              "이 카드는 수치를 집계하지 않습니다 — 작성 화면과 다운로드 화면으로 바로 이동하세요.",
              "This card tallies no number -- jump straight to the builder or the downloads.",
              "このカードは数値を集計しません — 作成画面とダウンロード画面へ直接移動してください。",
            )}
          </StateNote>
          <div className="mt-auto flex flex-wrap gap-2 pt-1">
            <StageLink href="/report" label={t("보고서 작성 →", "Write report →", "レポート作成 →")} />
            <StageLink href="/downloads" label={t("다운로드 →", "Downloads →", "ダウンロード →")} />
          </div>
        </StageCard>
      </div>
    </div>
  );
}

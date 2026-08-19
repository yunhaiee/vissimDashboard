"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { roleLabel, useAuth } from "@/components/AuthProvider";
import { useLanguage } from "@/components/LanguageProvider";
import {
  AuthRequestError,
  createAdminUser,
  fetchAdminUsers,
  fetchAdminWorkspaces,
  fetchWorkspaceMembers,
  grantWorkspaceMember,
  revokeWorkspaceMember,
  setAdminUserDisabled,
  setAdminUserPassword,
  setAdminUserRole,
  type AuthUser,
  type WorkspaceMemberListResponse,
  type WorkspaceSummary,
} from "@/lib/api";
import { failedWith, ok, resolve, resolveSelection, type Loaded } from "@/lib/asyncResource";
import { loginHref } from "@/lib/authRedirect";

/**
 * 관리자 (admin) — accounts and project access, unit P④.
 *
 * Until this page the only way to create a Prism account was an operator
 * running `backend/tools/manage_users.py` in a terminal on the backend
 * machine, which for a product whose users are traffic consultants is a reason
 * to never add a second user — and therefore a reason the 프로젝트 isolation
 * boundary was never actually closed.
 *
 * Three deliberate choices, all of which a reviewer should be able to check
 * from this file alone:
 *
 * 1. **The server is the authority, not this page.** Nothing here decides
 *    whether the caller is an admin; it asks, and renders whatever the answer
 *    is. `status` from `useAuth()` only prevents a flash of the wrong screen
 *    while the first `whoami()` is in flight — exactly the role the app shell
 *    already gives it. A page that gated on the cached role would show an
 *    empty admin console to a demoted user instead of the truth.
 * 2. **401 and 403 are different facts and get different screens.** 401 means
 *    "nobody is signed in", so it routes straight to /login with `next` set
 *    back here. 403 means "signed in, but not an administrator": auto-routing
 *    that to /login would land on a page that says "이미 로그인되어 있습니다"
 *    and offers a Continue button back to this same refusal — a loop, not a
 *    destination. So a 403 states the refusal and offers a sign-in link the
 *    user can take with a different account.
 * 3. **No password is ever generated here.** An admin creating an account
 *    types one, exactly as the CLI has always required (see
 *    `backend/services/auth.py`'s approved policy). A secret this product
 *    invented would be a secret this product had to transmit.
 *
 * ko primary throughout, en/ja via the three-argument `t()` so every string
 * carries its own Japanese rather than depending on `lib/i18n-ja.ts` having an
 * entry for it.
 */

type Access = "loading" | "ok" | "forbidden" | "error";

/** Everything the page renders, read in ONE round trip so it can be ONE
 * `Loaded` value (lib/asyncResource.ts). A list of accounts rendered beside
 * "목록을 불러오지 못했습니다" would be a screen contradicting itself, and on this
 * screen the lists are the evidence for every decision the operator makes. */
type AdminLists = { users: AuthUser[]; roles: string[]; workspaces: WorkspaceSummary[] };

/** This page asks ONE question and it takes no parameters -- "who exists, and
 * what projects exist" -- so its request key is a constant rather than a
 * selection or a refresh nonce. A re-read therefore keeps the current lists on
 * screen rather than flashing an empty one, which is what this page has always
 * done after a mutation. */
const LISTS_KEY = "admin-lists";

export default function AdminPage() {
  const { t } = useLanguage();
  const router = useRouter();
  const { user, status } = useAuth();

  const [access, setAccess] = useState<Access>("loading");
  const [listsLoaded, setListsLoaded] = useState<Loaded<AdminLists> | null>(null);
  const { data: lists, error: loadError } = resolve(listsLoaded, LISTS_KEY);
  const users = lists?.users ?? [];
  const roles = lists?.roles ?? [];
  const workspaces = lists?.workspaces ?? [];
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  /**
   * The ONE place a failed admin call turns into a screen state.
   *
   * Returning `true` for "handled as an access problem" keeps every call site
   * a single `if` instead of repeating the 401/403 branch — and makes it
   * impossible for one of them to quietly render a broken shell instead.
   */
  const handleAccessFailure = useCallback(
    (caught: unknown): boolean => {
      if (caught instanceof AuthRequestError && caught.status === 401) {
        router.replace(loginHref("/admin"));
        return true;
      }
      if (caught instanceof AuthRequestError && caught.status === 403) {
        setAccess("forbidden");
        return true;
      }
      return false;
    },
    [router],
  );

  const describe = useCallback(
    (caught: unknown): string =>
      caught instanceof Error && caught.message.trim()
        ? caught.message
        : t("요청을 처리하지 못했습니다.", "The request could not be completed.", "リクエストを処理できませんでした。"),
    [t],
  );

  /** The one wire call. It RETURNS the lists instead of storing them, which is
   * what lets two callers share it: the mount effect stores the answer from the
   * fetch's own callback (an effect may not set state in its body -- see
   * lib/asyncResource.ts), while `load` below awaits it inline. */
  const readLists = useCallback(async (): Promise<AdminLists> => {
    const [userPayload, workspacePayload] = await Promise.all([
      fetchAdminUsers(),
      fetchAdminWorkspaces(),
    ]);
    return {
      users: userPayload.users,
      roles: userPayload.roles,
      workspaces: workspacePayload.workspaces,
    };
  }, []);

  /** Where an answer -- or a refusal -- becomes screen state. One place each,
   * so the effect's callback and `load`'s await cannot drift apart. */
  const applyLists = useCallback((next: AdminLists) => {
    setListsLoaded(ok(LISTS_KEY, next));
    setAccess("ok");
  }, []);

  const applyLoadFailure = useCallback(
    (caught: unknown) => {
      if (handleAccessFailure(caught)) return;
      // `failedWith`, not `failed`: the sentence rendered is `describe`'s, which
      // supplies a translated one when the server sent no message at all.
      setListsLoaded(failedWith(LISTS_KEY, describe(caught)));
      setAccess("error");
    },
    [describe, handleAccessFailure],
  );

  /** A re-read that CALLERS CAN AWAIT -- the 다시 시도 button, and `run` below,
   * which must not report a success before the list it is describing has been
   * refreshed. That await is why this is a plain async function and not the
   * bump-a-nonce reload the polling screens use. */
  const load = useCallback(async () => {
    try {
      applyLists(await readLists());
    } catch (caught) {
      applyLoadFailure(caught);
    }
  }, [applyLists, applyLoadFailure, readLists]);

  // Waits for the first whoami() to settle. An anonymous visitor goes to the
  // login screen without the backend round trip that would answer 401 anyway;
  // everyone else asks the server, because only the server knows.
  useEffect(() => {
    if (status === "loading") return;
    if (status === "anonymous") {
      router.replace(loginHref("/admin"));
      return;
    }
    let cancelled = false;
    readLists()
      .then((next) => {
        if (!cancelled) applyLists(next);
      })
      .catch((caught) => {
        if (!cancelled) applyLoadFailure(caught);
      });
    return () => {
      cancelled = true;
    };
  }, [applyLists, applyLoadFailure, readLists, router, status]);

  /** Every mutation runs through here: one place that clears the previous
   * message, one place that maps a 401/403 onto the access screens, and one
   * place that refreshes. A mutation that reported success while the list
   * still showed the old state would be the worst outcome on this page.
   *
   * RETURNS whether the mutation actually succeeded. It used to return void
   * while swallowing the failure, so callers ran their post-success cleanup
   * unconditionally -- clearing the operator's typed password on a refused
   * create, which asks them to retype a secret because the server said no. */
  const run = useCallback(
    async (action: () => Promise<unknown>, success: string): Promise<boolean> => {
      setActionError(null);
      setNotice(null);
      try {
        await action();
        await load();
        setNotice(success);
        return true;
      } catch (caught) {
        if (handleAccessFailure(caught)) return false;
        setActionError(describe(caught));
        return false;
      }
    },
    [describe, handleAccessFailure, load],
  );

  if (status === "loading" || access === "loading") {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10">
        <p className="text-[15px] text-slate-500">{t("불러오는 중…", "Loading…", "読み込み中…")}</p>
      </div>
    );
  }

  if (access === "forbidden") {
    return (
      <div className="mx-auto w-full max-w-md py-10">
        <div className="ui-surface px-6 py-6">
          <h1 className="text-lg font-bold text-slate-900">
            {t("관리자 전용 화면입니다", "Administrators only", "管理者専用の画面です")}
          </h1>
          <p className="mt-2 text-[15px] text-slate-500">
            {t(
              "이 화면은 관리자 권한이 있는 계정만 사용할 수 있습니다. 권한이 필요하면 관리자에게 요청하세요.",
              "This screen is available to administrator accounts only. Ask an administrator if you need access.",
              "この画面は管理者権限を持つアカウントのみ使用できます。権限が必要な場合は管理者に依頼してください。",
            )}
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link href={loginHref("/admin")} className="ui-button-primary inline-flex h-10 items-center px-4 text-[15px]">
              {t("다른 계정으로 로그인", "Sign in as someone else", "別のアカウントでログイン")}
            </Link>
            <Link href="/dashboard" className="ui-button-ghost inline-flex h-10 items-center px-4 text-[15px]">
              {t("대시보드로", "Back to the dashboard", "ダッシュボードへ")}
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">
          {t("사용자 및 프로젝트 권한", "Users and project access", "ユーザーとプロジェクト権限")}
        </h1>
        <p className="mt-1 text-[15px] text-slate-600">
          {t(
            "계정을 만들고 권한을 바꾸며, 어떤 사용자가 어떤 프로젝트를 볼 수 있는지 정합니다.",
            "Create accounts, change roles, and decide which users can see which projects.",
            "アカウントを作成し権限を変更し、どのユーザーがどのプロジェクトを見られるかを決めます。",
          )}
        </p>
      </header>

      {access === "error" && (
        <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[15px] text-rose-700">
          <p>{t("목록을 불러오지 못했습니다", "Could not load the lists", "リストを読み込めませんでした")}: {loadError}</p>
          <button type="button" onClick={() => void load()} className="mt-2 text-[14px] font-semibold underline">
            {t("다시 시도", "Retry", "再試行")}
          </button>
        </div>
      )}

      {notice && (
        <p role="status" className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[14px] text-emerald-700">
          {notice}
        </p>
      )}
      {actionError && (
        <p role="alert" className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[14px] leading-5 text-rose-700">
          {actionError}
        </p>
      )}

      {/* Nothing interactive below a failed load. With `users`/`roles`/
        * `workspaces` all empty, the create form would offer a role <select>
        * with zero options while silently holding the default role in state,
        * and the membership list would assert "참여한 사용자가 없습니다" about a
        * project it never fetched. An empty state that was never measured is a
        * lie -- the same reason the backend refuses rather than defaults. */}
      {access !== "error" && (
        <>
          <UserSection users={users} roles={roles} currentUserId={user?.id ?? null} run={run} />
          <MembershipSection users={users} workspaces={workspaces} run={run} onFailure={handleAccessFailure} describe={describe} />
        </>
      )}
    </div>
  );
}

/** Resolves `true` only when the mutation succeeded; callers gate their
 * post-success cleanup (clearing inputs, closing a form) on it. */
type Run = (action: () => Promise<unknown>, success: string) => Promise<boolean>;

// ---------------------------------------------------------------------------
// Accounts
// ---------------------------------------------------------------------------

function UserSection({
  users,
  roles,
  currentUserId,
  run,
}: {
  users: AuthUser[];
  roles: string[];
  currentUserId: number | null;
  run: Run;
}) {
  const { t } = useLanguage();
  const [resettingId, setResettingId] = useState<number | null>(null);

  return (
    <section className="mb-6 rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
      <h2 className="text-[15px] font-bold text-slate-900">{t("계정", "Accounts", "アカウント")}</h2>
      <p className="mt-0.5 text-[14px] text-slate-500">
        {t(
          "비밀번호는 관리자가 직접 정합니다. 자동 생성이나 가입 절차는 없습니다.",
          "An administrator sets every password. There is no generated secret and no sign-up.",
          "パスワードは管理者が直接設定します。自動生成やサインアップはありません。",
        )}
      </p>

      <ul className="mt-4 space-y-2">
        {users.map((account) => {
          const isSelf = currentUserId != null && account.id === currentUserId;
          return (
            <li key={account.id} className="rounded-lg border border-slate-200 bg-slate-50/60 px-3.5 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[15px] font-semibold text-slate-900">
                      {account.display_name || account.username}
                    </span>
                    <span className="text-[14px] text-slate-500">{account.username}</span>
                    {isSelf && (
                      <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[12px] font-semibold text-slate-700">
                        {t("나", "You", "自分")}
                      </span>
                    )}
                    {account.disabled ? (
                      <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[12px] font-semibold text-rose-700">
                        {t("비활성", "Disabled", "無効")}
                      </span>
                    ) : (
                      <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[12px] font-semibold text-emerald-700">
                        {t("사용 중", "Active", "有効")}
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-[14px] text-slate-500">{roleLabel(account.role, t)}</p>
                </div>

                <div className="flex flex-none flex-wrap items-center gap-2">
                  <label className="sr-only" htmlFor={`admin-role-${account.id}`}>
                    {t("권한", "Role", "権限")}
                  </label>
                  <select
                    id={`admin-role-${account.id}`}
                    value={account.role}
                    onChange={(event) =>
                      void run(
                        () => setAdminUserRole(account.id, event.target.value),
                        t("권한을 변경했습니다.", "The role was changed.", "権限を変更しました。"),
                      )
                    }
                    className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-[14px] text-slate-700"
                  >
                    {roles.map((role) => (
                      <option key={role} value={role}>
                        {roleLabel(role, t)}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() =>
                      void run(
                        () => setAdminUserDisabled(account.id, !account.disabled),
                        account.disabled
                          ? t("계정을 다시 활성화했습니다.", "The account was re-enabled.", "アカウントを再度有効化しました。")
                          : t("계정을 비활성화했습니다.", "The account was disabled.", "アカウントを無効化しました。"),
                      )
                    }
                    className="ui-button-ghost inline-flex items-center px-3 py-2 text-[14px]"
                  >
                    {account.disabled ? t("활성화", "Enable", "有効化") : t("비활성화", "Disable", "無効化")}
                  </button>
                  <button
                    type="button"
                    onClick={() => setResettingId(resettingId === account.id ? null : account.id)}
                    className="ui-button-ghost inline-flex items-center px-3 py-2 text-[14px]"
                  >
                    {t("비밀번호 재설정", "Reset password", "パスワード再設定")}
                  </button>
                </div>
              </div>

              {resettingId === account.id && (
                <ResetPasswordForm
                  userId={account.id}
                  run={run}
                  onDone={() => setResettingId(null)}
                />
              )}
            </li>
          );
        })}
      </ul>

      <CreateUserForm roles={roles} run={run} />
    </section>
  );
}

function ResetPasswordForm({ userId, run, onDone }: { userId: number; run: Run; onDone: () => void }) {
  const { t } = useLanguage();
  const [password, setPassword] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!password) return;
    // Keep the typed password on refusal: the operator can fix the reason and
    // resubmit instead of retyping a secret the server merely rejected.
    const ok = await run(
      () => setAdminUserPassword(userId, password),
      t(
        "비밀번호를 변경했습니다. 이 사용자의 기존 로그인 세션은 모두 만료되었습니다.",
        "The password was changed. Every existing session of that user was revoked.",
        "パスワードを変更しました。このユーザーの既存セッションはすべて無効化されました。",
      ),
    );
    if (!ok) return;
    setPassword("");
    onDone();
  }

  return (
    <form onSubmit={handleSubmit} className="mt-3 flex flex-wrap items-end gap-2 border-t border-slate-200 pt-3">
      <div className="min-w-0 flex-1">
        <label htmlFor={`admin-password-${userId}`} className="block text-[14px] font-semibold text-slate-600">
          {t("새 비밀번호", "New password", "新しいパスワード")}
        </label>
        <input
          id={`admin-password-${userId}`}
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="ui-input mt-1.5"
        />
        <p className="mt-1 text-[13px] text-slate-400">
          {t(
            "변경하면 이 사용자의 기존 로그인 세션이 모두 만료됩니다.",
            "Changing it revokes every existing session of that user.",
            "変更するとこのユーザーの既存セッションはすべて無効化されます。",
          )}
        </p>
      </div>
      <button
        type="submit"
        disabled={!password}
        className="ui-button-primary inline-flex h-10 items-center px-4 text-[15px] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {t("변경", "Change", "変更")}
      </button>
    </form>
  );
}

function CreateUserForm({ roles, run }: { roles: string[]; run: Run }) {
  const { t } = useLanguage();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(roles[0] ?? "modeler");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!username.trim() || !password) return;
    // Keep everything typed on refusal ("아이디 중복", a too-weak password):
    // the operator corrects one field and resubmits rather than retyping the
    // whole form, secret included.
    const ok = await run(
      () =>
        createAdminUser({
          username: username.trim(),
          password,
          role,
          display_name: displayName.trim() || undefined,
        }),
      t("계정을 만들었습니다.", "The account was created.", "アカウントを作成しました。"),
    );
    if (!ok) return;
    setUsername("");
    setDisplayName("");
    setPassword("");
  }

  return (
    <form onSubmit={handleSubmit} className="mt-5 border-t border-slate-200 pt-4">
      <h3 className="text-[14px] font-bold text-slate-900">{t("새 계정 만들기", "Create an account", "新しいアカウントを作成")}</h3>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="admin-new-username" className="block text-[14px] font-semibold text-slate-600">
            {t("아이디", "Username", "ユーザーID")}
          </label>
          <input
            id="admin-new-username"
            type="text"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            className="ui-input mt-1.5"
          />
        </div>
        <div>
          <label htmlFor="admin-new-display-name" className="block text-[14px] font-semibold text-slate-600">
            {t("표시 이름", "Display name", "表示名")}
          </label>
          <input
            id="admin-new-display-name"
            type="text"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            className="ui-input mt-1.5"
          />
        </div>
        <div>
          <label htmlFor="admin-new-password" className="block text-[14px] font-semibold text-slate-600">
            {t("비밀번호", "Password", "パスワード")}
          </label>
          <input
            id="admin-new-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="ui-input mt-1.5"
          />
        </div>
        <div>
          <label htmlFor="admin-new-role" className="block text-[14px] font-semibold text-slate-600">
            {t("권한", "Role", "権限")}
          </label>
          <select
            id="admin-new-role"
            value={role}
            onChange={(event) => setRole(event.target.value)}
            className="ui-input mt-1.5"
          >
            {roles.map((value) => (
              <option key={value} value={value}>
                {roleLabel(value, t)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <button
        type="submit"
        disabled={!username.trim() || !password}
        className="ui-button-primary mt-3 inline-flex h-10 items-center px-4 text-[15px] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {t("계정 만들기", "Create account", "アカウントを作成")}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Project access
// ---------------------------------------------------------------------------

function MembershipSection({
  users,
  workspaces,
  run,
  onFailure,
  describe,
}: {
  users: AuthUser[];
  workspaces: WorkspaceSummary[];
  run: Run;
  onFailure: (caught: unknown) => boolean;
  describe: (caught: unknown) => string;
}) {
  const { t } = useLanguage();

  // The picker holds the operator's INTENT; the project actually in play is
  // derived from it during render (lib/asyncResource.ts's `resolveSelection`).
  //
  // Default to the first NON-default project when there is one: the default
  // project has no roster to manage (it is open to everyone), so opening on it
  // would show a screen with nothing to do. `resolveSelection`'s own fallback
  // is "the first item", so that preference is expressed by handing it a list
  // ordered that way -- a stable sort, so the server's order survives within
  // each group and the <select> below still renders `workspaces` untouched.
  //
  // As an effect this corrected itself one render late, in both directions: the
  // frame in which the workspace list arrived painted a picker on no project
  // with an empty roster beneath it, and a project that LEFT the list (revoked,
  // deleted) stayed named in the picker, because the effect only ran while
  // `selectedId` was still empty.
  const [selectedIntent, setSelectedIntent] = useState<string | null>(null);
  const rosterFirst = useMemo(
    () => [...workspaces].sort((a, b) => Number(a.is_default) - Number(b.is_default)),
    [workspaces],
  );
  const selectedId = resolveSelection(selectedIntent, rosterFirst, (entry) => entry.id) ?? "";

  // The roster, tagged with the project it describes. THAT is what makes
  // switching the picker faster than the roster loads safe: a response for
  // project A is stored under A's key and simply does not answer B, so project
  // A's members can never paint under project B's name -- on a screen whose
  // entire job is telling the operator who can see which client's data, showing
  // the wrong roster confidently is the one unacceptable outcome. (This was a
  // `latestRequestRef` compared by hand at three points, including a "superseded
  // request must not clear the spinner" guard in a `finally`; comparing the key
  // during render replaces all three, and additionally cannot leave a spinner
  // running after the reader picks a project whose answer is already in hand.)
  const [membersLoaded, setMembersLoaded] = useState<Loaded<WorkspaceMemberListResponse> | null>(null);
  const {
    data: membersData,
    error: membersError,
    loading,
  } = resolve(membersLoaded, selectedId, { idle: !selectedId });
  const members = useMemo(() => membersData?.members ?? [], [membersData]);
  const openToEveryone = membersData?.open_to_everyone ?? false;
  const [addUserId, setAddUserId] = useState<string>("");

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    fetchWorkspaceMembers(selectedId)
      .then((payload) => {
        if (!cancelled) setMembersLoaded(ok(selectedId, payload));
      })
      .catch((caught) => {
        if (cancelled) return;
        // A 401 navigates away and a 403 replaces the whole page with the
        // refusal screen, so neither leaves a roster for an error to sit in.
        if (onFailure(caught)) return;
        setMembersLoaded(failedWith(selectedId, describe(caught)));
      });
    return () => {
      cancelled = true;
    };
  }, [describe, onFailure, selectedId]);

  const memberIds = useMemo(() => new Set(members.map((entry) => entry.user_id)), [members]);
  const addable = users.filter((account) => !memberIds.has(account.id));

  return (
    <section className="rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
      <h2 className="text-[15px] font-bold text-slate-900">{t("프로젝트 접근 권한", "Project access", "プロジェクトアクセス権限")}</h2>
      <p className="mt-0.5 text-[14px] text-slate-500">
        {t(
          "프로젝트에 참여한 사용자만 그 프로젝트의 모델과 결과를 볼 수 있습니다. 관리자는 모든 프로젝트를 볼 수 있습니다.",
          "Only members of a project can see its model and results. Administrators can see every project.",
          "プロジェクトに参加したユーザーだけがそのプロジェクトのモデルと結果を見られます。管理者はすべてのプロジェクトを見られます。",
        )}
      </p>

      <div className="mt-4">
        <label htmlFor="admin-workspace-picker" className="block text-[14px] font-semibold text-slate-600">
          {t("프로젝트", "Project", "プロジェクト")}
        </label>
        <select
          id="admin-workspace-picker"
          value={selectedId}
          onChange={(event) => setSelectedIntent(event.target.value)}
          className="ui-input mt-1.5"
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>
              {workspace.name}
            </option>
          ))}
        </select>
      </div>

      {membersError && (
        <p role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[14px] text-rose-700">
          {membersError}
        </p>
      )}

      {openToEveryone && (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[14px] leading-5 text-amber-800">
          {t(
            "기본 프로젝트는 로그인하지 않은 사용자도 볼 수 있습니다. 데모·연습용이며, 실제 고객 자료는 새 프로젝트에 올리세요.",
            "The default project is visible to signed-out users. It is the demo/practice project — put real client data in a new project.",
            "デフォルトプロジェクトはログインしていないユーザーにも表示されます。デモ・練習用ですので、実際の顧客資料は新しいプロジェクトに置いてください。",
          )}
        </p>
      )}

      {loading ? (
        <p className="mt-3 text-[14px] text-slate-500">{t("불러오는 중…", "Loading…", "読み込み中…")}</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {members.length === 0 && (
            <li className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-center text-[14px] text-slate-500">
              {t(
                "참여한 사용자가 없습니다. 지금은 관리자만 이 프로젝트를 볼 수 있습니다.",
                "No members yet. Right now only administrators can see this project.",
                "参加ユーザーがいません。現在は管理者のみがこのプロジェクトを見られます。",
              )}
            </li>
          )}
          {members.map((member) => (
            <li
              key={member.user_id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3.5 py-2.5"
            >
              <div className="min-w-0">
                <span className="text-[15px] font-semibold text-slate-900">{member.display_name || member.username}</span>
                <span className="ml-2 text-[14px] text-slate-500">
                  {member.username} · {roleLabel(member.role, t)}
                </span>
              </div>
              <button
                type="button"
                onClick={() =>
                  void run(async () => {
                    // The mutation's response IS the roster, so it lands in the
                    // same keyed resource the fetch writes to -- a second copy
                    // of the members list is a second copy that can be wrong.
                    const payload = await revokeWorkspaceMember(selectedId, member.user_id);
                    setMembersLoaded(ok(selectedId, payload));
                  }, t("접근 권한을 해제했습니다.", "Access was removed.", "アクセス権限を解除しました。"))
                }
                className="ui-button-ghost inline-flex items-center px-3 py-1.5 text-[14px]"
              >
                {t("권한 해제", "Remove", "権限を解除")}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-slate-200 pt-3">
        <div className="min-w-0 flex-1">
          <label htmlFor="admin-add-member" className="block text-[14px] font-semibold text-slate-600">
            {t("사용자 추가", "Add a user", "ユーザーを追加")}
          </label>
          <select
            id="admin-add-member"
            value={addUserId}
            onChange={(event) => setAddUserId(event.target.value)}
            className="ui-input mt-1.5"
          >
            <option value="">{t("사용자 선택", "Choose a user", "ユーザーを選択")}</option>
            {addable.map((account) => (
              <option key={account.id} value={String(account.id)}>
                {(account.display_name || account.username) + ` (${account.username})`}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          disabled={!addUserId || !selectedId}
          onClick={() =>
            void run(async () => {
              const payload = await grantWorkspaceMember(selectedId, Number(addUserId));
              setMembersLoaded(ok(selectedId, payload));
              setAddUserId("");
            }, t("접근 권한을 부여했습니다.", "Access was granted.", "アクセス権限を付与しました。"))
          }
          className="ui-button-primary inline-flex h-10 items-center px-4 text-[15px] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("추가", "Add", "追加")}
        </button>
      </div>
    </section>
  );
}

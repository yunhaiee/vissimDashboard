"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { roleLabel, useAuth } from "@/components/AuthProvider";
import { useLanguage } from "@/components/LanguageProvider";
import { AuthRequestError } from "@/lib/api";
import { safeNextPath } from "@/lib/authRedirect";

/**
 * The login screen (unit U9).
 *
 * Two deliberate non-features:
 *
 * 1. NO account creation, and no "forgot password". Accounts exist only when
 *    an operator ran `backend/tools/manage_users.py` -- that is the approved
 *    policy (backend/routers/auth.py's module docstring), so the page says so
 *    instead of offering a link that would 404.
 * 2. NO client-side diagnosis of WHY a login failed. The backend answers wrong
 *    password, unknown username and disabled account with one identical 401 on
 *    purpose -- that is an anti-enumeration measure on a consultancy's client
 *    list -- so this page shows one message for all three. A network failure
 *    IS distinguished, because that is a different fact about the world and
 *    the user's fix for it is different.
 *
 * `useSearchParams` client-renders the tree up to the nearest Suspense
 * boundary (Next 16, app/api-reference/functions/use-search-params
 * "Prerendering"), so the boundary lives here -- same pattern as
 * app/network/page.tsx.
 */
export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  );
}

/** What went wrong, in the only three flavours that change what the user
 * should DO: fix the credentials, fix the connection, or wait/report. */
type LoginFailure = { kind: "credentials" | "network" | "server"; message: string };

function LoginPageContent() {
  const { lang, t } = useLanguage();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, status, login, logout } = useAuth();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<LoginFailure | null>(null);

  // Validated on the way IN, never trusted as typed: `next` is attacker-
  // controllable text in a URL (see lib/authRedirect.ts).
  const nextPath = safeNextPath(searchParams.get("next"));

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || !username.trim() || !password) return;

    setSubmitting(true);
    setFailure(null);
    try {
      await login(username.trim(), password, lang);
      setPassword("");
      router.replace(nextPath);
    } catch (caught) {
      setPassword("");
      setFailure(describeLoginFailure(caught, t));
    } finally {
      setSubmitting(false);
    }
  }

  if (status === "authenticated" && user) {
    return (
      <div className="mx-auto w-full max-w-md py-10">
        <div className="ui-surface px-6 py-6">
          <h1 className="text-lg font-bold text-slate-900">{t("이미 로그인되어 있습니다", "Already signed in")}</h1>
          <p className="mt-2 text-sm text-slate-500">
            {user.display_name || user.username} · {roleLabel(user.role, t)}
          </p>
          <div className="mt-5 flex gap-2">
            <Link href={nextPath} className="ui-button-primary inline-flex h-10 items-center px-4 text-sm">
              {t("계속하기", "Continue")}
            </Link>
            <button
              type="button"
              onClick={() => void logout()}
              className="ui-button-ghost inline-flex h-10 items-center px-4 text-sm"
            >
              {t("로그아웃", "Sign out")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-md py-10">
      <div className="ui-surface px-6 py-6">
        <h1 className="text-lg font-bold text-slate-900">{t("로그인", "Sign in")}</h1>
        <p className="mt-1.5 text-sm text-slate-500">
          {t("승인·반려 등 기록이 남는 작업에는 로그인이 필요합니다.", "Actions that leave a record, such as approving or declining, require a sign-in.")}
        </p>

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <label htmlFor="login-username" className="block text-xs font-semibold text-slate-600">
              {t("아이디", "Username")}
            </label>
            <input
              id="login-username"
              name="username"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={submitting}
              className="ui-input mt-1.5"
            />
          </div>

          <div>
            <label htmlFor="login-password" className="block text-xs font-semibold text-slate-600">
              {t("비밀번호", "Password")}
            </label>
            <input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={submitting}
              className="ui-input mt-1.5"
            />
          </div>

          {failure && (
            <p
              role="alert"
              className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700"
            >
              {failure.message}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting || !username.trim() || !password}
            className="ui-button-primary inline-flex h-10 w-full items-center justify-center px-4 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? t("로그인 중...", "Signing in...") : t("로그인", "Sign in")}
          </button>
        </form>

        <p className="mt-4 text-[11px] leading-5 text-slate-400">
          {t("계정은 관리자가 발급합니다. 가입 절차는 없습니다.", "Accounts are issued by an administrator. There is no sign-up.")}
        </p>
      </div>
    </div>
  );
}

/**
 * One message per kind of failure -- and exactly one for every rejected
 * credential.
 *
 * The 401 branch prefers the backend's own detail because that string is the
 * uniform one it deliberately chose, already localized server-side; the local
 * string is only a fallback for a 401 that carried no readable body (e.g. one
 * synthesized by a proxy). Neither path ever tells the user WHICH field was
 * wrong, because the server does not know how to say that either.
 */
function describeLoginFailure(caught: unknown, t: (ko: string, en: string) => string): LoginFailure {
  if (caught instanceof AuthRequestError) {
    if (caught.status === 401) {
      return {
        kind: "credentials",
        message: caught.message.trim() || t("아이디 또는 비밀번호가 올바르지 않습니다.", "Incorrect username or password."),
      };
    }
    return {
      kind: "server",
      message: `${t("로그인 처리 중 서버 오류가 발생했습니다.", "The server failed while processing the sign-in.")} (${caught.status})`,
    };
  }
  return {
    kind: "network",
    message: t(
      "서버에 연결하지 못했습니다. 백엔드가 실행 중인지 확인한 뒤 다시 시도해 주세요.",
      "Could not reach the server. Check that the backend is running, then try again.",
    ),
  };
}

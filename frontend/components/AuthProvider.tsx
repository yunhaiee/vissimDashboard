"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { login as loginRequest, logout as logoutRequest, whoami, type AuthSessionInfo, type AuthUser } from "@/lib/api";

/**
 * Session state for the whole app (unit U9), mirroring LanguageProvider's
 * shape: one context, one `use*` hook that throws outside its provider.
 *
 * There is NO token here and no localStorage. The session cookie is HttpOnly
 * (backend/routers/auth.py) precisely so scripted JS cannot read it, and this
 * provider deliberately does not invent a shadow copy of it: `user` is nothing
 * but the last echo of `GET /api/auth/whoami`. The server stays the authority
 * on who you are and on what you may do -- nothing in the UI is gated on the
 * role cached here (see the approval card, which reacts to 401/403 answers
 * rather than pre-judging them).
 */

/** "loading" only until the first whoami() settles. Kept distinct from
 * "anonymous" so the shell can avoid flashing a 로그인 link at a user who is,
 * in fact, signed in. */
export type AuthStatus = "loading" | "authenticated" | "anonymous";

type AuthContextValue = {
  user: AuthUser | null;
  status: AuthStatus;
  loading: boolean;
  /** Resolves to the signed-in user; throws AuthRequestError (401 for bad
   * credentials) or a plain Error (API unreachable) -- the login page needs to
   * tell those two apart. */
  login: (username: string, password: string, language?: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
  /** Re-read whoami(), e.g. after a 401 elsewhere in the app. */
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  // The single place the whoami answer becomes state. `null` covers both "the
  // server says nobody" (a 401) and "we could not ask": we cannot prove an
  // identity either way, so we hold none -- but neither is surfaced as an auth
  // ERROR. Every page already renders its own "backend unreachable" state, and
  // a second banner saying "signed out" would be a wrong explanation for a
  // network fault.
  const applySession = useCallback((session: AuthSessionInfo | null) => {
    setUser(session?.user ?? null);
    setStatus(session ? "authenticated" : "anonymous");
  }, []);

  // The mount read runs in the promise continuation rather than the effect
  // body, so an unmount mid-flight cannot land state on a dead tree.
  useEffect(() => {
    let cancelled = false;
    whoami()
      .then((session) => {
        if (!cancelled) applySession(session);
      })
      .catch(() => {
        if (!cancelled) applySession(null);
      });
    return () => {
      cancelled = true;
    };
  }, [applySession]);

  const readSession = useCallback(async () => {
    try {
      applySession(await whoami());
    } catch {
      applySession(null);
    }
  }, [applySession]);

  const login = useCallback(
    async (username: string, password: string, language?: string) => {
      // The login response carries the same user shape whoami() returns, so
      // there is no need to round-trip a second request to learn who we are.
      const session = await loginRequest(username, password, language);
      applySession(session);
      return session.user;
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      // The cookie is cleared by the response; drop the local echo either way.
      // A logout that failed to reach the server must still not leave a name
      // on screen that this browser can no longer act as.
      applySession(null);
    }
  }, [applySession]);

  const value = useMemo(
    () => ({ user, status, loading: status === "loading", login, logout, refresh: readSession }),
    [user, status, login, logout, readSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}

/** ko primary role labels, en/ja via the same t() the rest of the shell uses.
 * Exported so the shell and any future admin screen name a role identically. */
export const ROLE_LABELS: Record<string, [string, string, string]> = {
  modeler: ["모델러", "Modeler", "モデラー"],
  reviewer: ["리뷰어", "Reviewer", "レビュアー"],
  admin: ["관리자", "Admin", "管理者"],
};

export function roleLabel(role: string, t: (ko: string, en: string, ja?: string) => string): string {
  const entry = ROLE_LABELS[role];
  return entry ? t(entry[0], entry[1], entry[2]) : role;
}

"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  createWorkspace,
  fetchWorkspaces,
  getWorkspaceId,
  setWorkspaceId,
  WorkspaceAccessError,
  type WorkspaceSummary,
} from "@/lib/api";
import { isDatasetBusy, subscribeToDatasetBusy } from "@/lib/datasetEvents";

/**
 * 프로젝트 (workspace) picker — every request this app makes is scoped to the
 * selected one (lib/api.ts injects X-Workspace-Id), so switching projects
 * re-resolves the model, its validation workbook, its runs and its agent
 * sessions in one move.
 *
 * Unit #18 (variant B) MOVED this out of VissimDatasetBanner, where it had been
 * a block above the 모델 선택 controls on three pages. The move is the point of
 * the approved mockup: a picker that changes what every screen means belongs
 * above the navigation that reaches those screens, not inside one card on three
 * of them. The state and the request sequence below are the banner's, unchanged
 * — what changed is where the pixels are and, necessarily, how the refetch
 * reaches the pages (see the switch handler).
 *
 * `selectedWorkspaceId` is null until the list loads, at which point it takes
 * the backend's own answer for "which workspace did this request resolve to"
 * rather than guessing from localStorage.
 */
/** sessionStorage key for the one-shot workspace_forbidden recovery below —
 * present only between "we dropped a refused header" and the next successful
 * listing, so recovery can fire again for a FUTURE refusal but never loop. */
const WORKSPACE_RECOVERY_FLAG = "vissim-workspace-recovery";

export default function ProjectSelector() {
  const { t } = useLanguage();
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // An upload owned by a VissimDatasetBanner somewhere on the page. While this
  // picker lived inside that banner the guard was a local `uploading` boolean;
  // across components it has to travel (lib/datasetEvents.ts explains why the
  // guard is worth keeping at all).
  const [datasetBusy, setDatasetBusy] = useState(false);

  useEffect(() => {
    setDatasetBusy(isDatasetBusy());
    return subscribeToDatasetBusy(setDatasetBusy);
  }, []);

  useEffect(() => {
    let mounted = true;
    fetchWorkspaces()
      .then((payload) => {
        if (!mounted) return;
        window.sessionStorage.removeItem(WORKSPACE_RECOVERY_FLAG);
        setWorkspaces(payload.workspaces);
        setSelectedWorkspaceId(payload.current);
        // A stale id in localStorage (e.g. a workspace created on another
        // machine) 404s every request; the listing call is the one request
        // that tells us what the backend actually resolved, so re-sync to it.
        if (getWorkspaceId() && getWorkspaceId() !== payload.current) {
          setWorkspaceId(payload.current === payload.default ? null : payload.current);
        }
      })
      .catch((error) => {
        if (!mounted) return;
        // SELF-HEAL (user hit this live, 2026-08-14): a stored project id the
        // server refuses -- deleted project, or an account that is not a
        // member -- makes EVERY request on EVERY page 403 workspace_forbidden,
        // and the picker (also 403ing) used to just hide, leaving no way out.
        // Drop the refused header and reload into the default project; the
        // membership decision itself stays the server's. One-shot per tab via
        // sessionStorage so a server that refuses even the header-less call
        // cannot put the page in a reload loop.
        if (
          error instanceof WorkspaceAccessError &&
          getWorkspaceId() &&
          window.sessionStorage.getItem(WORKSPACE_RECOVERY_FLAG) === null
        ) {
          window.sessionStorage.setItem(WORKSPACE_RECOVERY_FLAG, error.code);
          setWorkspaceId(null);
          window.location.reload();
          return;
        }
        // Signed-out visitors (the login screen renders inside this shell too)
        // and an unreachable backend both land here. A missing workspace list
        // hides the picker rather than showing an error in the navigation —
        // the pages themselves already report a backend they cannot reach.
        setWorkspaces([]);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const handleSwitchWorkspace = useCallback(
    (workspaceId: string) => {
      if (!workspaceId || workspaceId === selectedWorkspaceId) return;
      const isDefault = workspaces.find((entry) => entry.id === workspaceId)?.is_default ?? false;
      // Storing null for the default workspace keeps a fresh browser behaving
      // exactly like a pre-workspace client (no header at all).
      setWorkspaceId(isDefault ? null : workspaceId);
      // A switch is a FULL RELOAD, not a broadcast. The adversarial pass on
      // this unit proved why the broadcast was not enough: while the picker
      // lived inside VissimDatasetBanner it existed on exactly the three pages
      // that refetch on the dataset-change event, so switching from any other
      // screen was structurally impossible. From the sidebar it is possible
      // everywhere -- and seven pipeline pages load workspace-scoped data on
      // mount with no listener, so a broadcast would leave the PREVIOUS
      // project's jobs/tokens/runs on screen while every new request already
      // carries the new project's header. Per-page subscriptions would fix
      // today's seven and silently re-open the bug in the next page that
      // forgets one; tearing the page down cannot go stale, for any page,
      // ever. (It also removes the interleaved-switch race outright -- there
      // is no async gap between storing the header and discarding the DOM.)
      window.location.reload();
    },
    [selectedWorkspaceId, workspaces],
  );

  const handleCreateWorkspace = useCallback(async () => {
    const name = newWorkspaceName.trim();
    if (!name) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createWorkspace(name);
      setWorkspaces((previous) => [...previous, created]);
      setNewWorkspaceName("");
      setCreating(false);
      handleSwitchWorkspace(created.id);
    } catch (createError) {
      setError(
        createError instanceof Error
          ? createError.message
          : t("프로젝트를 만들지 못했습니다.", "Could not create the project.", "プロジェクトを作成できませんでした。"),
      );
    } finally {
      setBusy(false);
    }
  }, [handleSwitchWorkspace, newWorkspaceName, t]);

  if (workspaces.length === 0) return null;

  const locked = busy || datasetBusy;

  return (
    /* Under the 880px rail (globals.css turns the sidebar into a centered
     * horizontal row) this card must be a FIXED-WIDTH, non-growing flex child:
     * the adversarial pass measured the unconstrained version wrapping its
     * Korean labels one syllable per line and inflating the whole rail to
     * ~490px tall. */
    <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 max-[880px]:mb-0 max-[880px]:w-[210px] max-[880px]:shrink-0">
      <label
        htmlFor="vissim-workspace-picker"
        className="whitespace-nowrap text-[11px] font-semibold text-slate-400"
      >
        {t("프로젝트", "Project", "プロジェクト")}
      </label>
      <select
        id="vissim-workspace-picker"
        value={selectedWorkspaceId ?? ""}
        disabled={locked}
        onChange={(event) => handleSwitchWorkspace(event.target.value)}
        className="ui-select mt-1 px-2 py-1.5 text-[13px] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.name}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => setCreating((open) => !open)}
        disabled={locked}
        aria-expanded={creating}
        className="ui-button-ghost mt-1.5 w-full whitespace-nowrap px-2 py-1 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {t("새 프로젝트", "New Project", "新規プロジェクト")}
      </button>
      {creating && (
        <div className="mt-1.5 flex flex-col gap-1.5">
          <input
            type="text"
            value={newWorkspaceName}
            maxLength={120}
            placeholder={t("프로젝트 이름", "Project name", "プロジェクト名")}
            onChange={(event) => setNewWorkspaceName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void handleCreateWorkspace();
            }}
            className="ui-input px-2 py-1.5 text-[12px]"
          />
          <button
            type="button"
            onClick={() => void handleCreateWorkspace()}
            disabled={busy || !newWorkspaceName.trim()}
            className="ui-button-primary px-2 py-1 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t("만들기", "Create", "作成")}
          </button>
        </div>
      )}
      {error && <p className="mt-1.5 break-words text-[11px] leading-relaxed text-rose-600">{error}</p>}
    </div>
  );
}

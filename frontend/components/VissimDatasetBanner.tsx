"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import { useLanguage } from "@/components/LanguageProvider";
import {
  createWorkspace,
  fetchActiveDataset,
  fetchWorkspaces,
  getWorkspaceId,
  resetActiveDataset,
  setWorkspaceId,
  uploadInpxDataset,
  type ActiveDataset,
  type WorkspaceSummary,
} from "@/lib/api";

interface Props {
  onDatasetChange?: (dataset: ActiveDataset) => void;
  className?: string;
  fullWidth?: boolean;
}

function publishDatasetChange(dataset: ActiveDataset) {
  window.localStorage.setItem("vissim-dataset-revision", dataset.revision ?? String(Date.now()));
  window.dispatchEvent(new CustomEvent("vissim-dataset-change", { detail: dataset }));
}

function DatasetStatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 font-medium text-slate-700">
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-slate-300"}`} aria-hidden />
      {label}
    </span>
  );
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function VissimDatasetBanner({ onDatasetChange, className = "", fullWidth = false }: Props) {
  const { t } = useLanguage();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [checking, setChecking] = useState(true);
  const [checkFailed, setCheckFailed] = useState(false);
  // fetch() has no upload-progress event, so a byte-accurate bar would need
  // rewriting the upload path onto XMLHttpRequest. This elapsed-time readout
  // is the honest middle ground: it proves the upload is still alive on a
  // large ZIP instead of a static "처리 중" label that never changes.
  const [uploadElapsedSec, setUploadElapsedSec] = useState(0);
  // 프로젝트 (workspace) picker — every request this app makes is scoped to the
  // selected one (lib/api.ts injects X-Workspace-Id), so switching projects
  // re-resolves the model, its validation workbook, its runs and its agent
  // sessions in one move. `selectedWorkspaceId` is null until the list loads,
  // at which point it takes the backend's own answer for "which workspace did
  // this request resolve to" rather than guessing from localStorage.
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  // `undefined` = no dataset has resolved yet; `null` = a dataset resolved but
  // the backend reported no workspace (a pre-1.4 backend).
  const lastBroadcastWorkspaceRef = useRef<string | null | undefined>(undefined);
  const hasCustomBackground = /\bbg-/.test(className);
  const hasCustomShadow = /\bshadow/.test(className);

  const loadActiveDataset = useCallback(() => {
    let mounted = true;
    setChecking(true);
    setCheckFailed(false);
    fetchActiveDataset()
      .then((dataset) => {
        if (!mounted) return;
        setActiveDataset(dataset);
        setChecking(false);
      })
      .catch(() => {
        if (!mounted) return;
        // Previously this silently left the dataset unset, so the label
        // below read "checking current dataset" forever with no way to
        // retell the user the backend is unreachable or try again.
        setActiveDataset(null);
        setChecking(false);
        setCheckFailed(true);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => loadActiveDataset(), [loadActiveDataset]);

  const loadWorkspaces = useCallback(() => {
    let mounted = true;
    fetchWorkspaces()
      .then((payload) => {
        if (!mounted) return;
        setWorkspaces(payload.workspaces);
        setSelectedWorkspaceId(payload.current);
        // A stale id in localStorage (e.g. a workspace created on another
        // machine) 404s every request; the listing call is the one request
        // that tells us what the backend actually resolved, so re-sync to it.
        if (getWorkspaceId() && getWorkspaceId() !== payload.current) {
          setWorkspaceId(payload.current === payload.default ? null : payload.current);
        }
      })
      .catch(() => {
        // The banner already surfaces backend-unreachable through the dataset
        // check; a missing workspace list just hides the picker.
        if (mounted) setWorkspaces([]);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => loadWorkspaces(), [loadWorkspaces]);

  const handleSwitchWorkspace = useCallback(
    (workspaceId: string) => {
      if (!workspaceId || workspaceId === selectedWorkspaceId) return;
      const isDefault = workspaces.find((entry) => entry.id === workspaceId)?.is_default ?? false;
      // Storing null for the default workspace keeps a fresh browser behaving
      // exactly like a pre-workspace client (no header at all).
      setWorkspaceId(isDefault ? null : workspaceId);
      setSelectedWorkspaceId(workspaceId);
      setError(null);
      loadActiveDataset();
    },
    [loadActiveDataset, selectedWorkspaceId, workspaces],
  );

  // Switching projects changes what every page's data means, so the existing
  // dataset-change broadcast is reused to make them refetch -- exactly the
  // path an upload already takes, so no page needs its own workspace logic.
  //
  // Keyed on the workspace the RELOADED dataset reports, not on the picker's
  // own state: `handleSwitchWorkspace` sets the selection and kicks off a
  // refetch, and broadcasting before that refetch lands would republish the
  // PREVIOUS project's dataset. The first resolved dataset is recorded
  // without broadcasting -- initial mount is not a change, and announcing it
  // would make every page double-fetch on load.
  useEffect(() => {
    if (!activeDataset) return;
    const workspaceId = activeDataset.workspace_id ?? null;
    if (lastBroadcastWorkspaceRef.current === undefined) {
      lastBroadcastWorkspaceRef.current = workspaceId;
      return;
    }
    if (lastBroadcastWorkspaceRef.current === workspaceId) return;
    lastBroadcastWorkspaceRef.current = workspaceId;
    publishDatasetChange(activeDataset);
    onDatasetChange?.(activeDataset);
  }, [activeDataset, onDatasetChange]);

  const handleCreateWorkspace = useCallback(async () => {
    const name = newWorkspaceName.trim();
    if (!name) return;
    setWorkspaceBusy(true);
    setError(null);
    try {
      const created = await createWorkspace(name);
      setWorkspaces((previous) => [...previous, created]);
      setNewWorkspaceName("");
      setCreatingWorkspace(false);
      handleSwitchWorkspace(created.id);
    } catch (createError) {
      setError(
        createError instanceof Error
          ? createError.message
          : t("프로젝트를 만들지 못했습니다.", "Could not create the project.", "プロジェクトを作成できませんでした。"),
      );
    } finally {
      setWorkspaceBusy(false);
    }
  }, [handleSwitchWorkspace, newWorkspaceName, t]);

  const handleSelectFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const runUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      setError(null);
      setUploadElapsedSec(0);
      const startedAt = Date.now();
      const ticker = window.setInterval(() => {
        setUploadElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
      }, 1000);
      try {
        const dataset = await uploadInpxDataset(file);
        setActiveDataset(dataset);
        publishDatasetChange(dataset);
        onDatasetChange?.(dataset);
      } catch (uploadError) {
        setError(uploadError instanceof Error ? uploadError.message : t("VISSIM 파일 업로드에 실패했습니다.", "Failed to upload VISSIM file."));
      } finally {
        window.clearInterval(ticker);
        setUploading(false);
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
      }
    },
    [onDatasetChange, t],
  );

  // Uploading replaces the active dataset (and hides every prior simulation
  // result computed against it), so stage the file and require confirmation
  // naming the concrete consequence instead of firing on file selection.
  const handleFileSelected = useCallback(
    (file: File | undefined) => {
      if (!file) return;
      if (activeDataset?.source === "uploaded") {
        setPendingFile(file);
        return;
      }
      void runUpload(file);
    },
    [activeDataset?.source, runUpload],
  );

  const handleReset = useCallback(async () => {
    setUploading(true);
    setError(null);
    try {
      const dataset = await resetActiveDataset();
      setActiveDataset(dataset);
      publishDatasetChange(dataset);
      onDatasetChange?.(dataset);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : t("기본 데이터셋 복원에 실패했습니다.", "Failed to restore the default dataset."));
    } finally {
      setUploading(false);
      setConfirmReset(false);
    }
  }, [onDatasetChange, t]);

  return (
    <div className={`${fullWidth ? "min-w-0" : "min-w-[260px]"} rounded-xl border border-slate-200  p-5  ${className}`}>
      <input
        ref={fileInputRef}
        type="file"
        accept=".inpx,.zip"
        className="hidden"
        onChange={(event) => {
          handleFileSelected(event.target.files?.[0]);
        }}
      />
      {workspaces.length > 0 && (
        <div className="mb-4 border-b border-slate-200 pb-3.5">
          <label
            htmlFor="vissim-workspace-picker"
            className="text-sm font-semibold text-slate-600"
          >
            {t("프로젝트", "Project", "プロジェクト")}
          </label>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <select
              id="vissim-workspace-picker"
              value={selectedWorkspaceId ?? ""}
              disabled={uploading || workspaceBusy}
              onChange={(event) => handleSwitchWorkspace(event.target.value)}
              className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => setCreatingWorkspace((open) => !open)}
              disabled={uploading || workspaceBusy}
              className="ui-button-ghost inline-flex items-center px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("새 프로젝트", "New Project", "新規プロジェクト")}
            </button>
          </div>
          {creatingWorkspace && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={newWorkspaceName}
                maxLength={120}
                placeholder={t("프로젝트 이름", "Project name", "プロジェクト名")}
                onChange={(event) => setNewWorkspaceName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void handleCreateWorkspace();
                }}
                className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-700"
              />
              <button
                type="button"
                onClick={() => void handleCreateWorkspace()}
                disabled={workspaceBusy || !newWorkspaceName.trim()}
                className="ui-button-primary inline-flex items-center px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
              >
                {t("만들기", "Create", "作成")}
              </button>
            </div>
          )}
          <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
            {t(
              "프로젝트마다 모델·검증 데이터·시뮬레이션 결과가 분리됩니다.",
              "Each project keeps its own model, validation data and simulation results.",
              "プロジェクトごとにモデル・検証データ・シミュレーション結果が分離されます。",
            )}
          </p>
        </div>
      )}
      <p className="text-sm font-semibold text-slate-600">{t("모델 선택", "Model")}</p>
      <p className="mt-1 truncate text-xs leading-relaxed text-slate-500">
        {activeDataset
          ? activeDataset.source === "uploaded"
            ? t(`업로드됨: ${activeDataset.filename}`, `Uploaded: ${activeDataset.filename}`, `アップロード済み: ${activeDataset.filename}`)
            : t(`기본 데이터: ${activeDataset.filename}`, `Default data: ${activeDataset.filename}`, `デフォルトデータ: ${activeDataset.filename}`)
          : checkFailed
            ? t("서버에 연결할 수 없습니다", "Could not reach the server")
            : t("현재 데이터 확인 중", "Checking current dataset")}
      </p>
      {checkFailed && (
        <button
          type="button"
          onClick={() => loadActiveDataset()}
          className="mt-1 text-[11px] font-semibold text-blue-600 underline decoration-dotted underline-offset-2 hover:text-blue-700"
        >
          {t("다시 시도", "Retry")}
        </button>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleSelectFile}
          disabled={uploading || checking}
          className="ui-button-primary inline-flex items-center px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading
            ? t(`업로드 중… ${uploadElapsedSec}초`, `Uploading… ${uploadElapsedSec}s`)
            : t("파일/ZIP 업로드", "Load File / ZIP")}
        </button>
        <button
          type="button"
          onClick={() => setConfirmReset(true)}
          disabled={uploading || activeDataset?.source !== "uploaded"}
          className="ui-button-ghost inline-flex items-center px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("기본값 복원", "Restore Default")}
        </button>
      </div>

      <ConfirmDialog
        open={pendingFile != null}
        title={t("데이터셋을 교체할까요?", "Replace the dataset?")}
        message={t(
          `"${pendingFile?.name}" 파일로 교체하면 현재 데이터셋("${activeDataset?.filename}")과 그에 연결된 모든 시뮬레이션 결과가 대시보드에서 사라집니다. 이 작업은 되돌릴 수 없습니다.`,
          `Replacing with "${pendingFile?.name}" will remove the current dataset ("${activeDataset?.filename}") and every simulation result tied to it from the dashboard. This cannot be undone.`,
        )}
        confirmLabel={t("교체", "Replace")}
        cancelLabel={t("취소", "Cancel")}
        busy={uploading}
        onConfirm={() => {
          if (pendingFile) void runUpload(pendingFile);
          setPendingFile(null);
        }}
        onCancel={() => {
          setPendingFile(null);
          if (fileInputRef.current) fileInputRef.current.value = "";
        }}
      />

      <ConfirmDialog
        open={confirmReset}
        title={t("기본값으로 복원할까요?", "Restore the default dataset?")}
        message={t(
          `현재 데이터셋("${activeDataset?.filename}")과 그에 연결된 모든 시뮬레이션 결과가 대시보드에서 사라집니다. 이 작업은 되돌릴 수 없습니다.`,
          `The current dataset ("${activeDataset?.filename}") and every simulation result tied to it will be removed from the dashboard. This cannot be undone.`,
        )}
        confirmLabel={t("복원", "Restore")}
        cancelLabel={t("취소", "Cancel")}
        busy={uploading}
        onConfirm={() => {
          void handleReset();
        }}
        onCancel={() => setConfirmReset(false)}
      />
      {activeDataset && (
        <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-2 border-t border-slate-200 pt-3.5 text-xs">
          <div className="flex items-center justify-between">
            <dt className="text-slate-500">{t("형식", "Format")}</dt>
            <dd className="font-medium uppercase tracking-wide text-slate-700">
              {activeDataset.kind ?? (activeDataset.source === "uploaded" ? "ZIP" : "—")}
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-slate-500">{t("시나리오", "Scenarios")}</dt>
            <dd>
              <DatasetStatusDot ok={!!activeDataset.has_scenarios} label={activeDataset.has_scenarios ? t("포함", "Included") : t("없음", "None")} />
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-slate-500">{t("수정사항", "Modifications")}</dt>
            <dd>
              <DatasetStatusDot ok={!!activeDataset.has_modifications} label={activeDataset.has_modifications ? t("포함", "Included") : t("없음", "None")} />
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-slate-500">{t("실측 데이터", "Real counts")}</dt>
            <dd>
              <DatasetStatusDot ok={!!activeDataset.has_real_counts} label={activeDataset.has_real_counts ? t("포함", "Included") : t("없음", "None")} />
            </dd>
          </div>
          {activeDataset.uploaded_at && (
            <div className="col-span-2 flex items-center justify-between border-t border-slate-200/70 pt-1.5">
              <dt className="text-slate-500">{t("업로드 시각", "Uploaded")}</dt>
              <dd className="font-medium text-slate-700">{formatTimestamp(activeDataset.uploaded_at)}</dd>
            </div>
          )}
        </dl>
      )}
      {error && <p className="mt-2 max-w-[280px] break-words text-[11px] leading-relaxed text-rose-600">{error}</p>}
    </div>
  );
}

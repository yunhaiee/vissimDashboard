"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
import { useLanguage } from "@/components/LanguageProvider";
import {
  fetchActiveDataset,
  resetActiveDataset,
  uploadInpxDataset,
  type ActiveDataset,
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
  const hasCustomBackground = /\bbg-/.test(className);
  const hasCustomShadow = /\bshadow/.test(className);

  useEffect(() => {
    let mounted = true;
    fetchActiveDataset()
      .then((dataset) => {
        if (mounted) setActiveDataset(dataset);
      })
      .catch(() => {
        if (mounted) setActiveDataset(null);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const handleSelectFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const runUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      setError(null);
      try {
        const dataset = await uploadInpxDataset(file);
        setActiveDataset(dataset);
        publishDatasetChange(dataset);
        onDatasetChange?.(dataset);
      } catch (uploadError) {
        setError(uploadError instanceof Error ? uploadError.message : t("VISSIM 파일 업로드에 실패했습니다.", "Failed to upload VISSIM file."));
      } finally {
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
    <div className={`${fullWidth ? "min-w-0" : "min-w-[260px]"} rounded-xl border border-slate-200 ${hasCustomBackground ? "" : "bg-white/95"} p-3 ${hasCustomShadow ? "" : "shadow-sm"} ${className}`}>
      <input
        ref={fileInputRef}
        type="file"
        accept=".inpx,.zip"
        className="hidden"
        onChange={(event) => {
          handleFileSelected(event.target.files?.[0]);
        }}
      />
      <p className="text-xs font-semibold text-slate-800">{t("모델 선택", "Model")}</p>
      <p className="mt-1 truncate text-[11px] leading-relaxed text-slate-500">
        {activeDataset
          ? activeDataset.source === "uploaded"
            ? t(`업로드됨: ${activeDataset.filename}`, `Uploaded: ${activeDataset.filename}`, `アップロード済み: ${activeDataset.filename}`)
            : t(`기본 데이터: ${activeDataset.filename}`, `Default data: ${activeDataset.filename}`, `デフォルトデータ: ${activeDataset.filename}`)
          : t("현재 데이터 확인 중", "Checking current dataset")}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleSelectFile}
          disabled={uploading}
          className="ui-button-primary inline-flex items-center px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? t("처리 중", "Loading") : t("파일/ZIP 업로드", "Load File / ZIP")}
        </button>
        <button
          type="button"
          onClick={() => setConfirmReset(true)}
          disabled={uploading || activeDataset?.source !== "uploaded"}
          className="ui-button-danger inline-flex items-center px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
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
        <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-slate-200/70 pt-3 text-[11px]">
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

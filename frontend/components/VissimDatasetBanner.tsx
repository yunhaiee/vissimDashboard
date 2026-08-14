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
import { publishDatasetChange, setDatasetBusy, subscribeToDatasetChange } from "@/lib/datasetEvents";

interface Props {
  onDatasetChange?: (dataset: ActiveDataset) => void;
  className?: string;
  fullWidth?: boolean;
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

  // The 프로젝트 picker used to sit in this component, so a project switch could
  // update this banner and call `onDatasetChange` by simply being in the same
  // closure. Unit #18 moved it to the sidebar (components/ProjectSelector.tsx),
  // which leaves the window broadcast as the only channel between them -- so
  // this is now the ONE path by which an announced dataset reaches both this
  // banner's own display and every page's `onDatasetChange`, whether the change
  // came from the sidebar picker or from the upload/reset buttons below.
  //
  // Routing the local upload/reset through the same broadcast rather than
  // calling `onDatasetChange` directly is what keeps a switch and an upload
  // indistinguishable to the pages, and is why nothing here needs to filter out
  // its own event: there is one publisher path and one subscriber path.
  useEffect(
    () =>
      subscribeToDatasetChange((dataset) => {
        setActiveDataset(dataset);
        onDatasetChange?.(dataset);
      }),
    [onDatasetChange],
  );

  const handleSelectFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const runUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      setDatasetBusy(true);
      setError(null);
      setUploadElapsedSec(0);
      const startedAt = Date.now();
      const ticker = window.setInterval(() => {
        setUploadElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
      }, 1000);
      try {
        const dataset = await uploadInpxDataset(file);
        publishDatasetChange(dataset);
      } catch (uploadError) {
        setError(uploadError instanceof Error ? uploadError.message : t("VISSIM 파일 업로드에 실패했습니다.", "Failed to upload VISSIM file."));
      } finally {
        window.clearInterval(ticker);
        setUploading(false);
        setDatasetBusy(false);
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
      }
    },
    [t],
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
    setDatasetBusy(true);
    setError(null);
    try {
      const dataset = await resetActiveDataset();
      publishDatasetChange(dataset);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : t("기본 데이터셋 복원에 실패했습니다.", "Failed to restore the default dataset."));
    } finally {
      setUploading(false);
      setDatasetBusy(false);
      setConfirmReset(false);
    }
  }, [t]);

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
      <p className="text-[15px] font-semibold text-slate-600">{t("모델 선택", "Model")}</p>
      <p className="mt-1 truncate text-[14px] leading-relaxed text-slate-500">
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
          className="mt-1 text-[13px] font-semibold text-blue-600 underline decoration-dotted underline-offset-2 hover:text-blue-700"
        >
          {t("다시 시도", "Retry")}
        </button>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleSelectFile}
          disabled={uploading || checking}
          className="ui-button-primary inline-flex items-center px-3 py-2 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading
            ? t(`업로드 중… ${uploadElapsedSec}초`, `Uploading… ${uploadElapsedSec}s`)
            : t("파일/ZIP 업로드", "Load File / ZIP")}
        </button>
        <button
          type="button"
          onClick={() => setConfirmReset(true)}
          disabled={uploading || activeDataset?.source !== "uploaded"}
          className="ui-button-ghost inline-flex items-center px-3 py-2 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
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
      {error && <p className="mt-2 max-w-[280px] break-words text-[13px] leading-relaxed text-rose-600">{error}</p>}
    </div>
  );
}

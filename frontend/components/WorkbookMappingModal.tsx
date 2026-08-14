"use client";

import { useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  importBaseWorkbookMapping,
  previewBaseWorkbookMapping,
  resolveBaseWorkbookMappingSheet,
  WorkbookMappingRefusedError,
  type BaseWorkbookResponse,
  type WorkbookInspectResponse,
  type WorkbookInspectSheet,
  type WorkbookMappingIssue,
  type WorkbookMappingPreviewBlock,
  type WorkbookMappingSpec,
} from "@/lib/api";

type FieldDef = { key: string; ko: string; en: string; ja: string; required: boolean };

const COUNT_FIELDS: FieldDef[] = [
  { key: "period", ko: "시간대 (Period)", en: "Period", ja: "時間帯 (Period)", required: true },
  { key: "node_id", ko: "교차로 ID (Node ID)", en: "Node ID", ja: "ノードID", required: false },
  { key: "node_name", ko: "교차로명 (Node Name)", en: "Node Name", ja: "ノード名", required: false },
  { key: "movement", ko: "이동류 (Movement)", en: "Movement", ja: "方向別交通量 (Movement)", required: true },
  { key: "observed_volume", ko: "실측 교통량 (Observed)", en: "Observed Volume", ja: "実測交通量 (Observed)", required: true },
  { key: "model_volume", ko: "모델 교통량 (Model)", en: "Model Volume", ja: "モデル交通量 (Model)", required: false },
];

const TRAVEL_FIELDS: FieldDef[] = [
  { key: "period", ko: "시간대 (Period)", en: "Period", ja: "時間帯 (Period)", required: true },
  { key: "route_id", ko: "루트 ID (Route ID)", en: "Route ID", ja: "ルートID", required: false },
  { key: "route_name", ko: "루트명 (Route Name)", en: "Route Name", ja: "ルート名", required: false },
  { key: "observed_travel_time_sec", ko: "실측 통행시간(초) (Observed, sec)", en: "Observed Travel Time (sec)", ja: "実測旅行時間(秒)", required: true },
  { key: "model_travel_time_sec", ko: "모델 통행시간(초) (Model, sec)", en: "Model Travel Time (sec)", ja: "モデル旅行時間(秒)", required: false },
  { key: "sample_order", ko: "순번 (Sample Order)", en: "Sample Order", ja: "順番", required: false },
  { key: "time_label", ko: "구간 라벨 (Time Label)", en: "Time Label", ja: "区間ラベル", required: false },
];

interface WorkbookMappingModalProps {
  // Additionally carries the template-parse `warnings` that explain WHY this
  // upload didn't map cleanly, when opened from a rejected upload (see
  // base-model/page.tsx's handleWorkbookUpload / upload_base_workbook's
  // stage-then-promote response). Optional so this stays a plain
  // WorkbookInspectResponse-shaped prop for any other future caller.
  inspectResult: WorkbookInspectResponse & { warnings?: string[] };
  onClose: () => void;
  onImported: (workbook: BaseWorkbookResponse) => void;
}

export default function WorkbookMappingModal({ inspectResult, onClose, onImported }: WorkbookMappingModalProps) {
  const { t, lang } = useLanguage();
  const token = inspectResult.upload_token;

  const bestCounts = inspectResult.proposed_mapping.counts;
  const bestTravel = inspectResult.proposed_mapping.travel_times;

  const [countsSheetName, setCountsSheetName] = useState(bestCounts?.sheet ?? inspectResult.sheets[0]?.name ?? "");
  const [countsSnapshot, setCountsSnapshot] = useState<WorkbookInspectSheet | null>(
    inspectResult.sheets.find((s) => s.name === countsSheetName) ?? inspectResult.sheets[0] ?? null,
  );
  const [countsHeaderRow, setCountsHeaderRow] = useState(bestCounts?.header_row ?? countsSnapshot?.header_row ?? 1);
  const [countsFields, setCountsFields] = useState<Record<string, number | undefined>>(
    () => fieldsFromGuess(bestCounts?.fields ?? countsSnapshot?.proposed_counts_mapping ?? {}),
  );

  const [includeTravelTimes, setIncludeTravelTimes] = useState(Boolean(bestTravel));
  const [travelSheetName, setTravelSheetName] = useState(bestTravel?.sheet ?? inspectResult.sheets[0]?.name ?? "");
  const [travelSnapshot, setTravelSnapshot] = useState<WorkbookInspectSheet | null>(
    inspectResult.sheets.find((s) => s.name === travelSheetName) ?? inspectResult.sheets[0] ?? null,
  );
  const [travelHeaderRow, setTravelHeaderRow] = useState(bestTravel?.header_row ?? travelSnapshot?.header_row ?? 1);
  const [travelFields, setTravelFields] = useState<Record<string, number | undefined>>(
    () => fieldsFromGuess(bestTravel?.fields ?? travelSnapshot?.proposed_travel_times_mapping ?? {}),
  );

  const [resolvingCounts, setResolvingCounts] = useState(false);
  const [resolvingTravel, setResolvingTravel] = useState(false);
  const [preview, setPreview] = useState<{ counts: WorkbookMappingPreviewBlock<Record<string, unknown>> | null; travel: WorkbookMappingPreviewBlock<Record<string, unknown>> | null } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [refusalIssues, setRefusalIssues] = useState<WorkbookMappingIssue[] | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!countsSheetName) return;
    let cancelled = false;
    setResolvingCounts(true);
    resolveBaseWorkbookMappingSheet(token, countsSheetName, countsHeaderRow)
      .then((snapshot) => {
        if (cancelled) return;
        setCountsSnapshot(snapshot);
      })
      .catch((error) => {
        if (!cancelled) setActionError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setResolvingCounts(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, countsSheetName, countsHeaderRow]);

  useEffect(() => {
    if (!includeTravelTimes || !travelSheetName) return;
    let cancelled = false;
    setResolvingTravel(true);
    resolveBaseWorkbookMappingSheet(token, travelSheetName, travelHeaderRow)
      .then((snapshot) => {
        if (cancelled) return;
        setTravelSnapshot(snapshot);
      })
      .catch((error) => {
        if (!cancelled) setActionError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setResolvingTravel(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, includeTravelTimes, travelSheetName, travelHeaderRow]);

  function handleCountsSheetChange(name: string) {
    setCountsSheetName(name);
    const fallback = inspectResult.sheets.find((s) => s.name === name);
    if (fallback) setCountsHeaderRow(fallback.header_row);
    setCountsFields({});
    setPreview(null);
    setRefusalIssues(null);
  }

  function handleTravelSheetChange(name: string) {
    setTravelSheetName(name);
    const fallback = inspectResult.sheets.find((s) => s.name === name);
    if (fallback) setTravelHeaderRow(fallback.header_row);
    setTravelFields({});
    setPreview(null);
    setRefusalIssues(null);
  }

  // Re-seed the field mapping from this sheet/header row's own auto-guess
  // once the snapshot for it has loaded (covers the case where the user
  // switches sheet -- we don't have that sheet's guess until resolve() returns).
  useEffect(() => {
    if (countsSnapshot && countsSnapshot.name === countsSheetName && countsSnapshot.header_row === countsHeaderRow && Object.keys(countsFields).length === 0) {
      setCountsFields(fieldsFromGuess(countsSnapshot.proposed_counts_mapping));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countsSnapshot]);

  useEffect(() => {
    if (
      includeTravelTimes &&
      travelSnapshot &&
      travelSnapshot.name === travelSheetName &&
      travelSnapshot.header_row === travelHeaderRow &&
      Object.keys(travelFields).length === 0
    ) {
      setTravelFields(fieldsFromGuess(travelSnapshot.proposed_travel_times_mapping));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [travelSnapshot, includeTravelTimes]);

  const countsSpec = useMemo<WorkbookMappingSpec | undefined>(() => {
    if (!countsSheetName) return undefined;
    return { sheet: countsSheetName, header_row: countsHeaderRow, fields: compactFields(countsFields) };
  }, [countsSheetName, countsHeaderRow, countsFields]);

  const travelSpec = useMemo<WorkbookMappingSpec | undefined>(() => {
    if (!includeTravelTimes || !travelSheetName) return undefined;
    return { sheet: travelSheetName, header_row: travelHeaderRow, fields: compactFields(travelFields) };
  }, [includeTravelTimes, travelSheetName, travelHeaderRow, travelFields]);

  async function handlePreview() {
    setPreviewing(true);
    setActionError(null);
    setRefusalIssues(null);
    try {
      const result = await previewBaseWorkbookMapping({ upload_token: token, counts: countsSpec, travel_times: travelSpec });
      setPreview({
        counts: result.counts_preview as unknown as WorkbookMappingPreviewBlock<Record<string, unknown>> | null,
        travel: result.travel_times_preview as unknown as WorkbookMappingPreviewBlock<Record<string, unknown>> | null,
      });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setPreviewing(false);
    }
  }

  async function handleImport() {
    setImporting(true);
    setActionError(null);
    setRefusalIssues(null);
    try {
      const workbook = await importBaseWorkbookMapping({ upload_token: token, counts: countsSpec, travel_times: travelSpec });
      onImported(workbook);
    } catch (error) {
      if (error instanceof WorkbookMappingRefusedError) {
        setRefusalIssues(error.issues);
      } else {
        setActionError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setImporting(false);
    }
  }

  const busy = resolvingCounts || resolvingTravel || previewing || importing;

  return (
    <div
      className="fixed inset-0 z-[3000] flex items-center justify-center bg-black/60 px-4 py-8 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={t("열 매핑", "Column Mapping")}
    >
      <div className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-slate-300/80 bg-surface shadow-[0_24px_60px_rgba(15,23,42,0.3)]">
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="text-[15px] font-bold text-slate-900">
              {t("열 매핑 필요", "Column Mapping Needed")}
            </h2>
            <p className="mt-1 text-[14px] leading-relaxed text-slate-600">
              {t(
                `"${inspectResult.filename}"이(가) 표준 템플릿과 다른 구조입니다. 각 항목이 어느 열에 해당하는지 확인해주세요.`,
                `"${inspectResult.filename}" doesn't match the standard template layout. Please confirm which column each field maps to.`,
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={importing}
            className="rounded-lg px-2 py-1 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={t("닫기", "Close")}
          >
            ✕
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {inspectResult.warnings && inspectResult.warnings.length > 0 && (
            <div className="mb-4 space-y-1 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
              <p className="text-[14px] font-bold uppercase tracking-wide text-amber-700">
                {t(
                  "표준 템플릿으로 가져오지 못한 이유 (기존 데이터는 그대로 유지됩니다)",
                  "Why the standard template import didn't work (your existing data is untouched)",
                )}
              </p>
              {inspectResult.warnings.map((warning, idx) => (
                <p key={idx} className="text-[14px] leading-relaxed text-amber-800">
                  {warning}
                </p>
              ))}
            </div>
          )}
          <MappingSection
            title={t("1. 교통량 (Counts)", "1. Traffic Counts")}
            sheetNames={inspectResult.sheets.map((s) => s.name)}
            sheetName={countsSheetName}
            onSheetChange={handleCountsSheetChange}
            headerRow={countsHeaderRow}
            onHeaderRowChange={(value) => {
              setCountsHeaderRow(value);
              setCountsFields({});
              setPreview(null);
              setRefusalIssues(null);
            }}
            snapshot={countsSnapshot}
            resolving={resolvingCounts}
            fields={COUNT_FIELDS}
            values={countsFields}
            onFieldChange={(key, value) => {
              setCountsFields((current) => ({ ...current, [key]: value }));
              setPreview(null);
              setRefusalIssues(null);
            }}
            t={t}
          />

          <div className="mt-6 border-t border-slate-200 pt-4">
            <label className="flex items-center gap-2 text-[14px] font-semibold text-slate-700">
              <input
                type="checkbox"
                checked={includeTravelTimes}
                onChange={(event) => {
                  setIncludeTravelTimes(event.target.checked);
                  setPreview(null);
                  setRefusalIssues(null);
                }}
              />
              {t("2. 통행시간(Travel Times)도 함께 매핑", "2. Also map Travel Times")}
            </label>
            {includeTravelTimes && (
              <div className="mt-3">
                <MappingSection
                  title=""
                  sheetNames={inspectResult.sheets.map((s) => s.name)}
                  sheetName={travelSheetName}
                  onSheetChange={handleTravelSheetChange}
                  headerRow={travelHeaderRow}
                  onHeaderRowChange={(value) => {
                    setTravelHeaderRow(value);
                    setTravelFields({});
                    setPreview(null);
                    setRefusalIssues(null);
                  }}
                  snapshot={travelSnapshot}
                  resolving={resolvingTravel}
                  fields={TRAVEL_FIELDS}
                  values={travelFields}
                  onFieldChange={(key, value) => {
                    setTravelFields((current) => ({ ...current, [key]: value }));
                    setPreview(null);
                    setRefusalIssues(null);
                  }}
                  t={t}
                />
              </div>
            )}
          </div>

          {preview && (
            <div className="mt-6 border-t border-slate-200 pt-4">
              <h3 className="text-[14px] font-bold uppercase tracking-wide text-slate-500">
                {t("미리보기 (Preview)", "Preview")}
              </h3>
              {preview.counts && (
                <PreviewTable
                  title={t("교통량 미리보기", "Counts preview")}
                  block={preview.counts}
                  columns={["period", "node_id", "node_name", "movement", "observed_volume", "model_volume"]}
                  t={t}
                />
              )}
              {preview.travel && (
                <PreviewTable
                  title={t("통행시간 미리보기", "Travel times preview")}
                  block={preview.travel}
                  columns={["period", "route_id", "route_name", "observed_travel_time_sec", "model_travel_time_sec"]}
                  t={t}
                />
              )}
            </div>
          )}

          {refusalIssues && refusalIssues.length > 0 && (
            <div className="mt-6 space-y-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3">
              <p className="text-[14px] font-bold uppercase tracking-wide text-rose-700">
                {t("가져오기가 거부되었습니다", "Import was refused")}
              </p>
              {refusalIssues.map((issue) => (
                <p key={issue.code} className="text-[15px] leading-relaxed text-rose-800">
                  {issue[lang] ?? issue.en}
                </p>
              ))}
            </div>
          )}

          {actionError && (
            <div className="mt-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[15px] text-rose-700">
              {actionError}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            disabled={importing}
            className="rounded-lg border border-slate-300 bg-surface px-3 py-2 text-[14px] font-semibold text-slate-700 transition hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t("취소", "Cancel")}
          </button>
          <button
            type="button"
            onClick={handlePreview}
            disabled={busy || !countsSpec}
            className="ui-button-ghost inline-flex items-center px-3 py-2 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {previewing ? t("미리보기 중", "Previewing") : t("미리보기", "Preview")}
          </button>
          <button
            type="button"
            onClick={handleImport}
            disabled={busy || !countsSpec}
            className="ui-button-primary inline-flex items-center px-3 py-2 text-[14px] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {importing ? t("가져오는 중", "Importing") : t("가져오기", "Import")}
          </button>
        </div>
      </div>
    </div>
  );
}

function MappingSection({
  title,
  sheetNames,
  sheetName,
  onSheetChange,
  headerRow,
  onHeaderRowChange,
  snapshot,
  resolving,
  fields,
  values,
  onFieldChange,
  t,
}: {
  title: string;
  sheetNames: string[];
  sheetName: string;
  onSheetChange: (name: string) => void;
  headerRow: number;
  onHeaderRowChange: (value: number) => void;
  snapshot: WorkbookInspectSheet | null;
  resolving: boolean;
  fields: FieldDef[];
  values: Record<string, number | undefined>;
  onFieldChange: (key: string, value: number | undefined) => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const columns = snapshot?.columns ?? [];
  const headers = snapshot?.headers ?? [];
  const guesses = fields === COUNT_FIELDS ? snapshot?.proposed_counts_mapping : snapshot?.proposed_travel_times_mapping;

  return (
    <div>
      {title && <h3 className="text-[14px] font-bold uppercase tracking-wide text-slate-500">{title}</h3>}
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-[14px] font-semibold text-slate-600">{t("시트", "Sheet")}</span>
          <select className="ui-select" value={sheetName} onChange={(event) => onSheetChange(event.target.value)}>
            {sheetNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[14px] font-semibold text-slate-600">{t("헤더 행 (Header Row)", "Header Row")}</span>
          <input
            type="number"
            min={1}
            max={snapshot?.max_row ?? undefined}
            className="ui-select"
            value={headerRow}
            onChange={(event) => onHeaderRowChange(Math.max(1, Number(event.target.value) || 1))}
          />
        </label>
      </div>

      {resolving ? (
        <p className="mt-3 text-[14px] text-slate-500">{t("불러오는 중...", "Loading...")}</p>
      ) : (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {fields.map((field) => {
            const guess = guesses?.[field.key];
            const selected = values[field.key];
            const isGuessed = guess != null && selected === guess.column;
            return (
              <label key={field.key} className="block">
                <span className="mb-1 flex items-center gap-1.5 text-[14px] font-semibold text-slate-600">
                  {t(field.ko, field.en, field.ja)}
                  {field.required && <span className="text-rose-500">*</span>}
                  {isGuessed && (
                    <span className="rounded-full bg-blue-50 px-1.5 py-0.5 text-[12px] font-bold text-blue-600">
                      {guess?.source === "remembered" ? t("기억됨", "remembered") : t("자동 추정", "guessed")}
                    </span>
                  )}
                </span>
                <select
                  className="ui-select"
                  value={selected ?? ""}
                  onChange={(event) => {
                    const raw = event.target.value;
                    onFieldChange(field.key, raw === "" ? undefined : Number(raw));
                  }}
                >
                  <option value="">{t("(매핑 안 함)", "(not mapped)")}</option>
                  {columns.map((letter, idx) => (
                    <option key={letter} value={idx}>
                      {letter} — {headers[idx] || t("(빈 열)", "(empty)")}
                    </option>
                  ))}
                </select>
              </label>
            );
          })}
        </div>
      )}

      {snapshot && snapshot.sample_rows.length > 0 && (
        <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-slate-50">
          <table className="w-full min-w-max text-left text-[13px]">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500">
                {columns.map((letter, idx) => (
                  <th key={letter} className="whitespace-nowrap px-2 py-1.5 font-semibold">
                    {letter}
                    {headers[idx] ? <span className="ml-1 font-normal text-slate-400">({headers[idx]})</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {snapshot.sample_rows.slice(0, 4).map((row, rowIdx) => (
                <tr key={rowIdx} className="border-b border-slate-200/70 text-slate-700 last:border-0">
                  {row.map((cell, cellIdx) => (
                    <td key={cellIdx} className="whitespace-nowrap px-2 py-1">
                      {cell == null || cell === "" ? "-" : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function PreviewTable({
  title,
  block,
  columns,
  t,
}: {
  title: string;
  block: WorkbookMappingPreviewBlock<Record<string, unknown>>;
  columns: string[];
  t: (ko: string, en: string, ja?: string) => string;
}) {
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between">
        <p className="text-[14px] font-semibold text-slate-700">{title}</p>
        <p className="text-[13px] text-slate-500">
          {block.total_rows} {t("행", "rows")}
          {block.truncated ? ` (${t("일부만 표시", "showing a subset")})` : ""}
        </p>
      </div>
      {block.issues.length === 0 ? (
        <div className="mt-1 overflow-x-auto rounded-lg border border-slate-200 bg-slate-50">
          <table className="w-full min-w-max text-left text-[13px]">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500">
                {columns.map((col) => (
                  <th key={col} className="whitespace-nowrap px-2 py-1.5 font-semibold">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, idx) => (
                <tr key={idx} className="border-b border-slate-200/70 text-slate-700 last:border-0">
                  {columns.map((col) => (
                    <td key={col} className="whitespace-nowrap px-2 py-1">
                      {formatPreviewCell(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="mt-1 space-y-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          {block.issues.map((issue) => (
            <p key={issue.code} className="text-[14px] leading-relaxed text-amber-800">
              {t(issue.ko, issue.en, issue.ja)}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function formatPreviewCell(value: unknown): string {
  if (value == null || value === "") return "-";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function fieldsFromGuess(guesses: Record<string, { column: number }>): Record<string, number | undefined> {
  const result: Record<string, number | undefined> = {};
  for (const [key, guess] of Object.entries(guesses)) {
    result[key] = guess.column;
  }
  return result;
}

function compactFields(values: Record<string, number | undefined>): Record<string, number> {
  const result: Record<string, number> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value != null) result[key] = value;
  }
  return result;
}

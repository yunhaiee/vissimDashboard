"use client";

// Unit #28 -- the observation STORE gets a route. backend/routers/observations.py
// was a complete API (upload, ingest through the same data-quality gate every
// other intake path uses, list, detail, delete) with zero frontend consumers
// before this page. What it is FOR: a stored set feeds `run_reality_check`'s
// `observation_set_id` parameter as the OBSERVED side (unit U10) -- sets are
// leg|turn-keyed, the same movement key the reality check joins on.
//
// Master-detail, ONE page, no dynamic route: the sets list is the Console-list
// table (docs/ux-backlog.md's LIST REFERENCE -- muted headers, monospace ids,
// no row borders, borderless hover rows), and clicking a row loads and shows
// that set's full detail (periods, gate findings, per-site aggregates) below it
// -- a local `selectedId`, never a navigation. Reading `frontend/app/genesis/
// demand/page.tsx` (the closest sibling: workbook upload + staged-token flow)
// and `components/genesis/ui.tsx` (Panel/ModalDialog) is what this page's
// upload flow and chrome are built from.
//
// THREE-COLOR RULE, held exactly: rose = the live refusal block (a 422 the
// quality gate raised) and the registry-quarantine disclosure; emerald = a
// successfully stored set / a passed gate; sky = the selected row. Nothing else
// takes a fourth hue -- a WARN-tier gate finding goes mono (heavier border), the
// same choice `app/genesis/demand/page.tsx`'s `issueClasses` already made.
//
// NO INLINE DOCTRINE PROSE on this page (the app purged repeated fine print,
// unit #20's retirement note in lib/help-content.tsx): one-line purpose under
// the h1, and the longer explanation of what a set is for lives at /docs
// (`observationsHelpEntries` below, added to app/docs/page.tsx).

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { ModalDialog, Panel } from "@/components/genesis/ui";
import {
  ObservationRefusalError,
  deleteObservationSet,
  fetchObservationSet,
  fetchObservationSets,
  ingestObservationSet,
  uploadObservationWorkbook,
  type ObservationGateIssue,
  type ObservationGateSummary,
  type ObservationRegistryHealth,
  type ObservationSetRecord,
  type ObservationSetSummary,
  type ObservationSiteAggregate,
  type StagedObservationUpload,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** One period row the reviewer is drafting -- text fields all the way down
 * (including the two `datetime-local` values) so an incomplete row can be
 * typed without fighting a numeric/date input's own validation first; the
 * server is the one authority on whether a window is well-formed. */
type PeriodDraft = { name: string; start: string; end: string };

function emptyPeriod(): PeriodDraft {
  return { name: "", start: "", end: "" };
}

// Same severity palette `app/genesis/demand/page.tsx`'s `issueClasses`/
// `issueWord` use, restated here rather than imported: this store's
// `ObservationGateIssue` and the demand screen's `DemandCountsIssue` are two
// different wire types that happen to share a `severity` field shape, and
// nothing today needs (or should invite) a client-side type union of two
// backend concerns just to share ten lines of class names.
function issueClasses(severity: ObservationGateIssue["severity"]): string {
  if (severity === "refuse") return "border-rose-300 bg-rose-50 text-slate-800";
  if (severity === "warn") return "border-slate-400 bg-slate-100 text-slate-800";
  return "border-slate-200 bg-slate-50 text-slate-600";
}

function issueWord(severity: ObservationGateIssue["severity"], t: Translate): string {
  if (severity === "refuse") return t("거부", "REFUSE", "拒否");
  if (severity === "warn") return t("경고", "WARN", "警告");
  return t("정보", "INFO", "情報");
}

function GateIssuesList({ gate, t, lang }: { gate: ObservationGateSummary; t: Translate; lang: string }) {
  if (gate.issues.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1.5">
      {gate.issues.map((issue, index) => (
        <li
          key={`${issue.code}-${index}`}
          className={`rounded-md border px-3 py-2 text-[14px] leading-relaxed ${issueClasses(issue.severity)}`}
        >
          <span className="mr-1 font-bold">{issueWord(issue.severity, t)}</span>
          <span className="font-mono">{issue.code}</span> — {issue.messages[lang] || issue.message}
          {issue.citation && (
            <p className="mt-1 text-[13px] text-slate-500">
              {t("근거", "Basis", "根拠")}: {issue.citation}
            </p>
          )}
        </li>
      ))}
      {gate.issues_truncated > 0 && (
        <li className="text-[14px] text-slate-500">
          {t(
            `그 외 ${gate.issues_truncated}건은 표시 한도로 생략되었습니다`,
            `${gate.issues_truncated} more, omitted at the display cap`,
            `他${gate.issues_truncated}件は表示上限のため省略されました`,
          )}
        </li>
      )}
    </ul>
  );
}

/** A stored set's gate never carries a REFUSE (`ingest_observation_set` raises
 * before storing anything when one fires) -- so the verdict chip a listing row
 * can honestly show is only ever "passed clean" (emerald) or "passed with
 * warnings" (mono), never rose. Rose is reserved for the LIVE refusal block a
 * failed ingest attempt renders, and for the registry-quarantine disclosure. */
function GateVerdictChip({ gate, t }: { gate: ObservationGateSummary; t: Translate }) {
  if (gate.counts.warn > 0) {
    return (
      <span className="inline-block whitespace-nowrap rounded-full border border-slate-400 bg-slate-100 px-2 py-0.5 text-[14px] font-semibold text-slate-800">
        {t(`경고 ${gate.counts.warn}`, `WARN ${gate.counts.warn}`, `警告 ${gate.counts.warn}`)}
      </span>
    );
  }
  return (
    <span className="inline-block whitespace-nowrap rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[14px] font-semibold text-slate-800">
      {t("통과", "PASS", "合格")}
    </span>
  );
}

function SiteAggregateDetails({ site, t }: { site: ObservationSiteAggregate; t: Translate }) {
  const movementEntries = Object.entries(site.movements);
  const detectorEntries = Object.entries(site.detectors);
  return (
    <details className="border-t border-slate-100 py-2">
      <summary className="cursor-pointer text-[15px] text-slate-800">
        <span className="font-semibold">{site.site_name ?? site.site_id}</span>
        {site.site_name && site.site_name !== site.site_id && (
          <span className="ml-1 font-mono text-[14px] text-slate-500">({site.site_id})</span>
        )}
        <span className="ml-3 font-mono text-[14px] text-slate-500">
          {t("총량", "total", "総量")} {site.total.toLocaleString()}
          {site.detector_total !== null ? ` + ${site.detector_total.toLocaleString()}` : ""} ·{" "}
          {t("구간", "intervals", "区間")} {site.interval_count} · {site.count_basis.join(", ") || "—"}
        </span>
      </summary>
      <div className="mt-2 pl-1">
        {movementEntries.length === 0 && detectorEntries.length === 0 && (
          <p className="text-[14px] text-slate-500">
            {t("세부 이동류 데이터가 없습니다", "No per-movement detail", "移動流の詳細データはありません")}
          </p>
        )}
        {movementEntries.length > 0 && (
          <table className="w-full text-[14px]">
            <thead>
              <tr className="text-left text-slate-500">
                <th scope="col" className="py-1 font-normal">
                  {t("접근로|회전", "leg|turn", "流入路|旋回")}
                </th>
                <th scope="col" className="py-1 text-right font-normal">
                  {t("볼륨", "volume", "ボリューム")}
                </th>
              </tr>
            </thead>
            <tbody>
              {movementEntries.map(([key, value]) => (
                <tr key={key} className="border-t border-slate-100">
                  <td className="py-1 font-mono">{key}</td>
                  <td className="py-1 text-right font-mono">{value.volume.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {detectorEntries.length > 0 && (
          <table className="mt-2 w-full text-[14px]">
            <thead>
              <tr className="text-left text-slate-500">
                <th scope="col" className="py-1 font-normal">
                  {t("검지기", "detector", "検知器")}
                </th>
                <th scope="col" className="py-1 text-right font-normal">
                  {t("볼륨", "volume", "ボリューム")}
                </th>
              </tr>
            </thead>
            <tbody>
              {detectorEntries.map(([key, value]) => (
                <tr key={key} className="border-t border-slate-100">
                  <td className="py-1 font-mono">{key}</td>
                  <td className="py-1 text-right font-mono">{value.volume.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </details>
  );
}

export default function ObservationsPage() {
  const { t, lang } = useLanguage();

  // -- Upload + ingest form state -----------------------------------------
  const [filesToUpload, setFilesToUpload] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<Error | null>(null);
  const [staged, setStaged] = useState<StagedObservationUpload[]>([]);

  const [name, setName] = useState("");
  const [adapter, setAdapter] = useState("");
  const [periods, setPeriods] = useState<PeriodDraft[]>([emptyPeriod()]);

  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<Error | null>(null);
  const [justStored, setJustStored] = useState<ObservationSetRecord | null>(null);

  // -- Sets list + registry health -----------------------------------------
  const [sets, setSets] = useState<ObservationSetSummary[]>([]);
  const [setsError, setSetsError] = useState<string | null>(null);
  const [registryHealth, setRegistryHealth] = useState<ObservationRegistryHealth | null>(null);

  const loadSets = useCallback(async () => {
    try {
      const response = await fetchObservationSets();
      setSets(response.sets);
      setRegistryHealth(response.registry_health);
      setSetsError(null);
    } catch (caught) {
      setSetsError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void loadSets();
  }, [loadSets]);

  // -- Master-detail selection (no dynamic route -- a local id + a fetch) --
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedRecord, setSelectedRecord] = useState<ObservationSetRecord | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [selectedError, setSelectedError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId) {
      setSelectedRecord(null);
      setSelectedError(null);
      return;
    }
    let cancelled = false;
    setSelectedLoading(true);
    setSelectedError(null);
    void (async () => {
      try {
        const record = await fetchObservationSet(selectedId);
        if (!cancelled) setSelectedRecord(record);
      } catch (caught) {
        if (!cancelled) setSelectedError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (!cancelled) setSelectedLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const selectRow = useCallback((id: string) => {
    // Click the already-open row to collapse it -- one row's detail open at a
    // time, matching the MASTER-DETAIL reference (a row click TOGGLES that
    // row's own detail, it does not stack a second panel).
    setSelectedId((current) => (current === id ? null : id));
  }, []);

  // -- Delete, confirmed through the shared ModalDialog primitive ----------
  const [deleteTarget, setDeleteTarget] = useState<ObservationSetSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteObservationSet(deleteTarget.id);
      setDeleteTarget(null);
      if (selectedId === deleteTarget.id) setSelectedId(null);
      void loadSets();
    } catch (caught) {
      setDeleteError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, selectedId, loadSets]);

  // -- Upload: stage every selected file, one raw-body POST per workbook ---
  const uploadFiles = useCallback(async () => {
    if (filesToUpload.length === 0) return;
    setUploading(true);
    setUploadError(null);
    const remaining = [...filesToUpload];
    try {
      while (remaining.length > 0) {
        const file = remaining[0];
        const result = await uploadObservationWorkbook(file);
        setStaged((current) => [...current, result]);
        remaining.shift();
      }
      setFilesToUpload([]);
    } catch (caught) {
      // Whatever succeeded before the failure stays staged; `remaining` keeps
      // only the file that failed plus whatever was never tried, so a retry
      // does not re-upload what is already staged.
      setFilesToUpload(remaining);
      setUploadError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setUploading(false);
    }
  }, [filesToUpload]);

  const removeStaged = useCallback((token: string) => {
    // Client-side only -- there is no discard-one-upload endpoint on this
    // store (only a successful ingest discards its own tokens server-side).
    // An abandoned staged file ages out on the backend's own staging TTL.
    setStaged((current) => current.filter((item) => item.token !== token));
  }, []);

  const updatePeriod = useCallback((index: number, field: keyof PeriodDraft, value: string) => {
    setPeriods((current) => current.map((period, i) => (i === index ? { ...period, [field]: value } : period)));
  }, []);
  const addPeriod = useCallback(() => setPeriods((current) => [...current, emptyPeriod()]), []);
  const removePeriod = useCallback((index: number) => {
    setPeriods((current) => (current.length > 1 ? current.filter((_, i) => i !== index) : current));
  }, []);

  const periodsComplete = periods.every((period) => period.name.trim() && period.start && period.end);
  // Mirrors the backend's OWN check (services/observations.py's
  // ObservationPeriod._window_is_naive_and_positive: "end <= start" refuses).
  // Without this, a reversed pair reaches POST /ingest and comes back as a
  // raw pydantic validation error -- an ARRAY under `detail`, not the
  // {reason_code, message} shape ObservationRefusalError parses -- so the
  // page would fall through to the generic-Error branch and render the
  // technical JSON blob verbatim instead of a named, ko/en/ja reason.
  // String comparison is safe here: a `datetime-local` input's value is
  // always `YYYY-MM-DDTHH:mm`, which sorts lexicographically exactly the
  // way it sorts chronologically.
  const periodsOrdered = periods.every((period) => !period.start || !period.end || period.end > period.start);
  const canIngest = staged.length > 0 && name.trim().length > 0 && periods.length > 0 && periodsComplete && periodsOrdered;
  const ingestBlockedReason = (): string | null => {
    if (staged.length === 0) return t("업로드된 워크북이 없습니다", "No workbook has been uploaded yet", "アップロード済みのワークブックがありません");
    if (!name.trim()) return t("이름을 입력하세요", "Name the set first", "名前を入力してください");
    if (!periodsComplete) return t("모든 기간에 이름·시작·끝을 입력하세요", "Every period needs a name, start and end", "すべての期間に名前・開始・終了を入力してください");
    if (!periodsOrdered) return t("각 기간의 끝은 시작보다 뒤여야 합니다", "Each period's end must be after its start", "各期間の終了は開始より後である必要があります");
    return null;
  };

  const ingest = useCallback(async () => {
    if (!canIngest) return;
    setIngesting(true);
    setIngestError(null);
    setJustStored(null);
    try {
      const record = await ingestObservationSet({
        name: name.trim(),
        upload_tokens: staged.map((item) => item.token),
        periods: periods.map((period) => ({ name: period.name.trim(), start: period.start, end: period.end })),
        adapter: adapter.trim() || undefined,
      });
      setJustStored(record);
      setStaged([]);
      setName("");
      setAdapter("");
      setPeriods([emptyPeriod()]);
      void loadSets();
    } catch (caught) {
      setIngestError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setIngesting(false);
    }
  }, [canIngest, name, staged, periods, adapter, loadSets]);

  const blockedReason = ingestBlockedReason();

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-8">
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">
          {t("실측 관측", "Observed Counts", "実測観測")}
        </h1>
        <p className="mt-1.5 text-[15px] leading-relaxed text-slate-600">
          {t(
            "현장에서 측정한 교통량을 올려 두면, 이후 모형 대조의 실측 쪽이 됩니다.",
            "Upload counts measured in the field; they become the observed side of a later model comparison.",
            "現場で計測した交通量をアップロードすると、以降のモデル対照における実測側になります。",
          )}
        </p>
      </header>

      {registryHealth && (
        <div className="mb-6 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] leading-relaxed text-slate-800">
          {registryHealth.messages[lang]}
        </div>
      )}

      <Panel
        density="console"
        title={t("워크북 업로드 · 적재", "Upload & store a workbook", "ワークブックのアップロード・取り込み")}
      >
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".xlsx,.xlsm"
            multiple
            onChange={(event) => setFilesToUpload(Array.from(event.target.files ?? []))}
            className="block text-[15px] text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white/[0.06] file:px-3 file:py-1.5 file:text-[14px] file:font-semibold file:text-slate-700"
          />
          <button
            type="button"
            disabled={filesToUpload.length === 0 || uploading}
            onClick={() => void uploadFiles()}
            className="ui-button-primary shrink-0 px-3.5 py-1.5 text-[15px] disabled:cursor-not-allowed"
          >
            {uploading ? t("올리는 중…", "Uploading…", "アップロード中…") : t("업로드", "Upload", "アップロード")}
          </button>
        </div>

        {uploadError && (
          <div className="mt-4 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-slate-800">
            <p className="font-semibold">
              {uploadError instanceof ObservationRefusalError
                ? uploadError.messages[lang]
                : t("업로드하지 못했습니다", "Upload failed", "アップロードできませんでした")}
            </p>
            {!(uploadError instanceof ObservationRefusalError) && (
              <p className="mt-1 whitespace-pre-wrap font-mono text-[14px] leading-relaxed">{uploadError.message}</p>
            )}
          </div>
        )}

        {staged.length > 0 && (
          <ul className="mt-4 flex flex-wrap gap-2">
            {staged.map((item) => (
              <li
                key={item.token}
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 bg-white/[0.06] px-3 py-1 text-[14px] text-slate-700"
              >
                <span className="font-mono">{item.filename}</span>
                <button
                  type="button"
                  onClick={() => removeStaged(item.token)}
                  aria-label={t("제거", "Remove", "削除")}
                  className="text-slate-500"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-6 flex flex-col gap-4 border-t border-slate-200 pt-5">
          <div className="flex flex-wrap gap-4">
            <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-[15px] text-slate-600">
              {t("세트 이름", "Set name", "セット名")}
              <input
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("예: 대전 유성구 4-5월", "e.g. 대전 유성구 4-5월", "例: 大田儒城区 4〜5月")}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[15px] text-slate-800"
              />
            </label>
            <label className="flex min-w-[220px] flex-col gap-1 text-[15px] text-slate-600">
              {t("어댑터 강제 지정 (선택)", "Force an adapter (optional)", "アダプタを強制指定（任意）")}
              <input
                type="text"
                value={adapter}
                onChange={(event) => setAdapter(event.target.value)}
                placeholder={t("비워두면 자동 인식", "empty = auto-detect", "空欄=自動判定")}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[15px] text-slate-800"
              />
            </label>
          </div>

          <div>
            <p className="text-[15px] font-semibold text-slate-700">
              {t("시간대 (최소 1개)", "Period(s) — at least one", "時間帯（最低1件）")}
            </p>
            <div className="mt-2 flex flex-col gap-2">
              {periods.map((period, index) => (
                <div key={index} className="flex flex-wrap items-end gap-2">
                  <label className="flex flex-col gap-1 text-[14px] text-slate-600">
                    {t("이름", "name", "名前")}
                    <input
                      type="text"
                      value={period.name}
                      onChange={(event) => updatePeriod(index, "name", event.target.value)}
                      placeholder={t("예: AM 첨두", "e.g. AM peak", "例: AMピーク")}
                      className="w-40 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px] text-slate-800"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-[14px] text-slate-600">
                    {t("시작", "start", "開始")}
                    <input
                      type="datetime-local"
                      value={period.start}
                      onChange={(event) => updatePeriod(index, "start", event.target.value)}
                      className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px] text-slate-800"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-[14px] text-slate-600">
                    {t("끝", "end", "終了")}
                    <input
                      type="datetime-local"
                      value={period.end}
                      onChange={(event) => updatePeriod(index, "end", event.target.value)}
                      className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px] text-slate-800"
                    />
                  </label>
                  {periods.length > 1 && (
                    <button
                      type="button"
                      onClick={() => removePeriod(index)}
                      className="ui-button-ghost rounded-md px-2.5 py-1.5 text-[14px]"
                    >
                      {t("제거", "Remove", "削除")}
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={addPeriod}
                className="ui-button-ghost self-start rounded-md px-3 py-1.5 text-[14px]"
              >
                {t("+ 기간 추가", "+ Add period", "+ 期間を追加")}
              </button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={!canIngest || ingesting}
              onClick={() => void ingest()}
              className="ui-button-primary shrink-0 px-3.5 py-1.5 text-[15px] disabled:cursor-not-allowed"
            >
              {ingesting ? t("적재하는 중…", "Storing…", "取り込み中…") : t("적재", "Store", "取り込み")}
            </button>
            {blockedReason && (
              <span className="text-[14px] font-semibold text-slate-800">{blockedReason}</span>
            )}
          </div>

          {ingestError && (
            <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] leading-relaxed text-slate-800">
              <p className="font-semibold">
                {ingestError instanceof ObservationRefusalError
                  ? ingestError.messages[lang]
                  : t("적재하지 못했습니다", "Could not store the set", "取り込めませんでした")}
              </p>
              {ingestError instanceof ObservationRefusalError && (() => {
                const gate = ingestError.detail.gate as ObservationGateSummary | undefined;
                return gate ? <GateIssuesList gate={gate} t={t} lang={lang} /> : null;
              })()}
              {!(ingestError instanceof ObservationRefusalError) && (
                <p className="mt-1 whitespace-pre-wrap font-mono text-[14px] leading-relaxed">{ingestError.message}</p>
              )}
            </div>
          )}

          {justStored && (
            <div className="rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-[15px] leading-relaxed text-slate-800">
              <p className="font-semibold">
                {t("관측 세트가 적재되었습니다", "Observation set stored", "観測セットを取り込みました")}
              </p>
              <p className="mt-1.5">
                {justStored.name} · {t("지점", "sites", "地点")} {justStored.site_count} ·{" "}
                {t("기간", "periods", "期間")} {justStored.periods.length}
              </p>
            </div>
          )}
        </div>
      </Panel>

      <section className="mt-8">
        <h2 className="mb-3 text-[15px] font-semibold text-slate-900">
          {t("관측 세트", "Observation sets", "観測セット")}
        </h2>
        {setsError && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
            {t("목록을 불러오지 못했습니다", "Failed to load the list", "一覧を読み込めませんでした")}: {setsError}
          </div>
        )}
        {sets.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-[15px] text-slate-500">
            {t("아직 적재된 관측 세트가 없습니다.", "No observation set has been stored yet.", "まだ取り込まれた観測セットはありません。")}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="text-[14px] font-normal text-slate-500">
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("이름", "Name", "名前")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("어댑터", "Adapter", "アダプター")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("기간", "Periods", "期間")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("지점", "Sites", "地点")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("게이트", "Gate", "ゲート")}
                  </th>
                  <th scope="col" className="py-2 text-right font-normal">
                    {t("생성일", "Created", "作成日")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {sets.map((set) => {
                  const selected = set.id === selectedId;
                  return (
                    <tr
                      key={set.id}
                      onClick={() => selectRow(set.id)}
                      tabIndex={0}
                      aria-current={selected ? "true" : undefined}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          selectRow(set.id);
                        }
                      }}
                      className={`cursor-pointer align-top text-[15px] ${selected ? "bg-sky-50" : "hover:bg-slate-50"}`}
                    >
                      <td className="max-w-[200px] py-2.5 pr-3">
                        <span className="block truncate font-medium text-slate-800">{set.name}</span>
                      </td>
                      <td className="max-w-[160px] py-2.5 pr-3 font-mono text-[14px] text-slate-500">
                        <span className="block truncate" title={set.adapter.name}>
                          {set.adapter.name}
                        </span>
                      </td>
                      <td className="max-w-[180px] py-2.5 pr-3 font-mono text-[14px] text-slate-500">
                        <span className="block truncate" title={set.periods.map((period) => period.name).join(", ")}>
                          {set.periods.map((period) => period.name).join(", ")}
                        </span>
                      </td>
                      <td className="py-2.5 pr-3 font-mono text-[14px] text-slate-500">{set.site_count}</td>
                      <td className="py-2.5 pr-3">
                        <GateVerdictChip gate={set.gate} t={t} />
                      </td>
                      <td className="whitespace-nowrap py-2.5 text-right font-mono text-[14px] text-slate-500">
                        {formatTimestamp(set.ingested_at, lang)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selectedId && (
        <section className="mt-8 border-t border-slate-200 pt-6">
          {selectedLoading && <p className="text-[15px] text-slate-500">{t("불러오는 중…", "Loading…", "読み込み中…")}</p>}
          {selectedError && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
              {t("상세를 불러오지 못했습니다", "Failed to load detail", "詳細を読み込めませんでした")}: {selectedError}
            </div>
          )}
          {selectedRecord && (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-[17px] font-semibold text-slate-900">{selectedRecord.name}</h3>
                    <span className="inline-block rounded-full border border-slate-300 bg-white/[0.06] px-2 py-0.5 font-mono text-[13px] text-slate-600">
                      {selectedRecord.adapter.name}
                    </span>
                  </div>
                  <p className="mt-1 text-[14px] text-slate-500">
                    {formatTimestamp(selectedRecord.ingested_at, lang)} · {t("파일", "files", "ファイル")}{" "}
                    {selectedRecord.source_files.length} · {t("지점", "sites", "地点")} {selectedRecord.site_count}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setDeleteTarget(selectedRecord)}
                  className="ui-button-danger shrink-0 rounded-md px-3 py-1.5 text-[14px] font-semibold"
                >
                  {t("삭제", "Delete", "削除")}
                </button>
              </div>

              <div className="mt-6">
                <h4 className="text-[15px] font-semibold text-slate-800">
                  {t("기간별 커버리지", "Period coverage", "期間別カバレッジ")}
                </h4>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full border-collapse text-left text-[14px]">
                    <thead>
                      <tr className="text-slate-500">
                        <th scope="col" className="py-1.5 pr-3 font-normal">{t("기간", "period", "期間")}</th>
                        <th scope="col" className="py-1.5 pr-3 font-normal">{t("시작", "start", "開始")}</th>
                        <th scope="col" className="py-1.5 pr-3 font-normal">{t("끝", "end", "終了")}</th>
                        <th scope="col" className="py-1.5 pr-3 text-right font-normal">{t("지점", "sites", "地点")}</th>
                        <th scope="col" className="py-1.5 pr-3 text-right font-normal">{t("기록", "records", "レコード")}</th>
                        <th scope="col" className="py-1.5 text-right font-normal">{t("총량", "volume", "総量")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedRecord.periods.map((period) => (
                        <tr key={period.name} className="border-t border-slate-100">
                          <td className="py-1.5 pr-3 font-medium text-slate-800">{period.name}</td>
                          <td className="py-1.5 pr-3 font-mono text-slate-600">{period.start}</td>
                          <td className="py-1.5 pr-3 font-mono text-slate-600">{period.end}</td>
                          <td className="py-1.5 pr-3 text-right font-mono text-slate-600">{period.site_count}</td>
                          <td className="py-1.5 pr-3 text-right font-mono text-slate-600">{period.records}</td>
                          <td className="py-1.5 text-right font-mono text-slate-600">
                            {period.total_volume.toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {selectedRecord.periods.some((period) => period.sites_without_data.length > 0) && (
                  <p className="mt-2 text-[14px] leading-relaxed text-slate-500">
                    {t(
                      "일부 기간에는 데이터가 없는 지점이 있습니다 — 위 표의 지점 수는 데이터가 있는 지점만 셉니다.",
                      "Some periods have sites with no data in that window — the site count above counts only sites WITH data.",
                      "一部の期間にはデータのない地点があります — 上の表の地点数はデータのある地点のみを数えます。",
                    )}
                  </p>
                )}
              </div>

              <div className="mt-6">
                <h4 className="text-[15px] font-semibold text-slate-800">
                  {t("게이트 결과", "Gate result", "ゲート結果")}
                </h4>
                <p className="mt-1.5 font-mono text-[14px] text-slate-500">
                  {t("경고", "warn", "警告")} {selectedRecord.gate.counts.warn} · {t("정보", "info", "情報")}{" "}
                  {selectedRecord.gate.counts.info}
                </p>
                <GateIssuesList gate={selectedRecord.gate} t={t} lang={lang} />
              </div>

              <div className="mt-6">
                <h4 className="text-[15px] font-semibold text-slate-800">
                  {t("지점별 집계", "Per-site aggregates", "地点別集計")}
                </h4>
                <div className="mt-2 flex flex-col gap-3">
                  {Object.entries(selectedRecord.aggregates).map(([periodName, sites], index) => (
                    <Panel
                      key={periodName}
                      density="compact"
                      title={periodName}
                      collapsible
                      defaultOpen={index === 0}
                    >
                      {Object.keys(sites).length === 0 ? (
                        <p className="text-[14px] text-slate-500">
                          {t("이 기간에 데이터가 있는 지점이 없습니다", "No site has data in this period", "この期間にデータのある地点はありません")}
                        </p>
                      ) : (
                        <div>
                          {Object.values(sites).map((site) => (
                            <SiteAggregateDetails key={site.site_id} site={site} t={t} />
                          ))}
                        </div>
                      )}
                    </Panel>
                  ))}
                </div>
              </div>
            </>
          )}
        </section>
      )}

      <ModalDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title={t("관측 세트 삭제", "Delete observation set", "観測セットを削除")}
      >
        <p className="text-[15px] leading-relaxed text-slate-700">
          {t(
            `'${deleteTarget?.name ?? ""}' 세트를 삭제할까요? 이 동작은 되돌릴 수 없습니다.`,
            `Delete the set '${deleteTarget?.name ?? ""}'? This cannot be undone.`,
            `セット「${deleteTarget?.name ?? ""}」を削除しますか？この操作は元に戻せません。`,
          )}
        </p>
        {deleteError && (
          <p className="mt-3 rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-[14px] text-slate-800">
            {deleteError}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setDeleteTarget(null)}
            className="ui-button-ghost rounded-md px-3.5 py-1.5 text-[15px]"
          >
            {t("취소", "Cancel", "キャンセル")}
          </button>
          <button
            type="button"
            disabled={deleting}
            onClick={() => void confirmDelete()}
            className="ui-button-danger rounded-md px-3.5 py-1.5 text-[15px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          >
            {deleting ? t("삭제 중…", "Deleting…", "削除中…") : t("삭제", "Delete", "削除")}
          </button>
        </div>
      </ModalDialog>
    </div>
  );
}

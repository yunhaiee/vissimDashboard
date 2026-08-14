"use client";

// Roadmap v2 §5.B row B1 (genesis becomes a product feature) + §5.A row A4
// (the gate is its exit door) + the minimal half of row B3 (the gate's
// verdict becomes visible to a human).
//
// Three backend surfaces, each read from the endpoint that OWNS its data
// rather than re-derived here:
//   * POST /api/genesis/uploads  -- stage the .osm (raw body + X-Filename, the
//     house two-step because this backend has no python-multipart);
//   * POST /api/genesis/jobs     -- start one generation, then poll
//     GET /api/genesis/jobs/{id} exactly as the browser would poll a run;
//   * GET  /api/genesis/jobs/{id}/gate -- the STORED verdict, check by check.
//
// -------------------------------------------------------------------------
// THIS PAGE IS THE HUB, NOT THE WHOLE PRODUCT (unit #23)
// -------------------------------------------------------------------------
// Until 2026-08-14 this file was BOTH the hub and the detail drawer: a
// list/detail split where clicking a history row opened everything about
// that generation -- verdict, map, artifacts, comparison -- in a ~46%-wide
// right column. Testing that live (the mockup at docs/product/mockups/
// genesis-detail-layout-mockup-2026-08-14.html, option D) surfaced the
// problem this unit's user decision fixes: the map is the whole reason a
// reader opens a generation ("read a finding, look at where it is"), and a
// 46%-wide column could never give it room. So every generation now has its
// own route -- app/genesis/jobs/[id]/page.tsx -- with the map wide on the
// left and the verdict/fix rail narrow on the right. Every doctrine
// paragraph, gate mechanic and fetch-caching discipline that page used to
// carry here moved there VERBATIM; see that file's own header comment for
// the deep-link/stale-flash concerns unique to being a route.
//
// What is left here is the hub: the history TABLE (muted column headers,
// monospace ids and coordinates, borderless rows, the verdict as a small
// right-aligned tag chip, pagination once the list grows) as the page's
// whole body, ONE primary action (새 생성) top-right of the page header, and
// a CENTERED MODAL (ModalDialog, components/genesis/ui.tsx) holding the
// generation form -- the same form this page has always had, moved from a
// standing panel into a dialog a reader opens on purpose. A row click is a
// real navigation (`router.push`), not a local selection.
//
// -------------------------------------------------------------------------
// WHAT THIS PAGE MUST NOT DO, and the reason it is written this way
// -------------------------------------------------------------------------
// The R1.5 gate is a TRI-state per check (pass / fail / not-checkable) and a
// tri-state verdict, and "a check that could not run is never a pass" is the
// design rule the whole gate hangs on (docs/phase2/DECISIONS.md, 2026-08-11).
// This page no longer renders the gate grid at all (that moved to the detail
// route), but the tri-state discipline still governs its one remaining piece
// of gate-adjacent chrome -- the history row's verdict chip -- through the
// SAME shared `statusLabel`/`statusClasses` the detail page also uses
// (lib/genesisArtifacts.ts), so the two can never describe a job's status in
// different words.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { ModalDialog, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  fetchGenesisJobs,
  fetchStagedSideInputs,
  startGenesisJob,
  uploadGenesisOsm,
  type GenesisJob,
  type StagedSideInputEntry,
} from "@/lib/api";
import {
  POLL_INTERVAL_MS,
  formatTimestamp,
  isTerminal,
  networkScaleLabel,
  requestedRegionLabel,
  statusClasses,
  statusLabel,
} from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** Rows per page in the history table. The LIST REFERENCE (docs/ux-backlog.md)
 * asks for pagination arrows "when the list grows"; ten is the point at which
 * the table stops fitting the page without scrolling. */
const HISTORY_PAGE_SIZE = 10;

/** The generation form's own unknown-code sentence, handed to the shared
 * `refusalHeadline` as its fallback: on THIS screen the refused request is a
 * generation, so the generic "요청이 거부되었습니다" would be a step vaguer than
 * the page can afford. Every known code lives in components/genesis/ui.tsx.
 * (Duplicated, not imported, on app/genesis/jobs/[id]/page.tsx too -- see
 * that file's own comment on this function for why.) */
function generationRefusalFallback(t: Translate): string {
  return t("생성이 거부되었습니다", "Generation was refused", "生成が拒否されました");
}

/** One dropdown line for a staged side input -- filename plus whatever its
 * OWN summary already states (never re-derived here, so the picker cannot say
 * something the staging response did not). */
function sideInputLabel(entry: StagedSideInputEntry): string {
  const summary = entry.summary ?? {};
  if (entry.kind === "demand") {
    const sites = Array.isArray(summary.sites_mapped) ? summary.sites_mapped.length : 0;
    const approaches = typeof summary.approaches_mapped === "number" ? summary.approaches_mapped : 0;
    return `${entry.filename} · ${sites}지점/${approaches}접근로`;
  }
  if (entry.kind === "signals") {
    const assignments = Array.isArray(summary.assignments) ? summary.assignments.length : 0;
    return `${entry.filename} · ${assignments}개 교차로`;
  }
  return entry.filename;
}

/** The cross-navigation chip. Pill-shaped, from the app's own
 * `.ui-button-ghost` primitive (U-1 second elaboration: "pill-style filter
 * chips from the existing .ui-button-ghost vocabulary") rather than a fourth
 * hand-rolled border/background triple. */
const CROSS_CHIP = "ui-button-ghost inline-flex items-center gap-1 whitespace-nowrap rounded-full px-3 py-1 text-[14px] font-medium";

function PlusGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden className="h-3 w-3 shrink-0">
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GenesisPage() {
  const { lang, t } = useLanguage();
  const router = useRouter();

  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [excludeService, setExcludeService] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<GenesisError | Error | null>(null);
  // unit #23 -- the form lives inside a centered ModalDialog now instead of a
  // standing panel; `modalOpen` is owned HERE (not by the dialog) so a
  // background submit that resolves after the reader already closed the
  // dialog can still close it again (idempotent) and refresh the list
  // without racing a remount of the fields it is reading.
  const [modalOpen, setModalOpen] = useState(false);

  // The optional layers' pickers -- staged through /genesis/demand or
  // /genesis/phases (or the raw POST /side-inputs), listed here by KIND only:
  // nothing on this page assembles a document, it only names a token
  // startGenesisJob already accepts. Both default to "" (none), the
  // geometry-only path this form has always taken.
  const [sideInputs, setSideInputs] = useState<StagedSideInputEntry[]>([]);
  const [demandToken, setDemandToken] = useState("");
  const [signalsToken, setSignalsToken] = useState("");

  const [jobs, setJobs] = useState<GenesisJob[]>([]);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const entries = await fetchGenesisJobs();
      setJobs(entries);
      setJobsError(null);
    } catch (caught) {
      setJobsError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  // Keep the list live while anything on it is still running -- a job
  // started from the modal below appears here with a "생성 중" chip that
  // never updates on its own otherwise (the per-job LIVE poll now lives on
  // the detail route, which this page does not open). Self-terminating: once
  // a refreshed `jobs` contains no non-terminal entry, the effect's own
  // dependency on `jobs` stops re-arming the timer.
  useEffect(() => {
    if (!jobs.some((job) => !isTerminal(job.status))) return;
    const timer = setTimeout(() => void loadJobs(), POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [jobs, loadJobs]);

  const loadSideInputs = useCallback(async () => {
    try {
      setSideInputs(await fetchStagedSideInputs());
    } catch {
      // Best-effort: a picker with nothing in it still lets the geometry-only
      // path work, so a failed listing must not block the form.
      setSideInputs([]);
    }
  }, []);

  useEffect(() => {
    void loadSideInputs();
  }, [loadSideInputs]);

  const submit = useCallback(async () => {
    if (!file) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const staged = await uploadGenesisOsm(file);
      await startGenesisJob({
        upload_token: staged.token,
        name: name.trim() || undefined,
        exclude_service: excludeService,
        demand_token: demandToken || undefined,
        signals_token: signalsToken || undefined,
      });
      // unit #23: close the dialog and refresh the list -- the new row
      // appears with its own running status (the list-poll effect above
      // keeps it live); the reader clicks it when THEY want its detail page,
      // rather than being navigated there automatically.
      setModalOpen(false);
      setPage(0);
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setDemandToken("");
      setSignalsToken("");
      void loadJobs();
      // The chosen tokens are discarded server-side once the job owns its
      // frozen copies (keep_side_inputs defaults to false), so the pickers'
      // list has to be re-read -- otherwise a used token would keep showing as
      // available until the next full page load.
      void loadSideInputs();
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setSubmitting(false);
    }
  }, [file, name, excludeService, demandToken, signalsToken, loadJobs, loadSideInputs]);

  // The three cross-navigations, each label paired with the sentence that says
  // WHY that screen exists. Declared once and read twice (the chip's `title`
  // and the disclosure list below it) so the hover text and the disclosed text
  // can never say two different things.
  const regionHint = {
    label: t("영역에서 추출하기", "Extract a region", "領域から抽出"),
    note: t(
      ".osm 파일이 없으신가요? 지도 영역을 직접 지정해 OpenStreetMap에서 받아올 수 있습니다 — 받은 추출은 여기 업로드한 파일과 똑같은 경로를 탑니다.",
      "No .osm file? Name a map region and it is fetched from OpenStreetMap for you — the extract then takes exactly the same road as a file uploaded here.",
      ".osm ファイルがありませんか？地図の領域を指定して OpenStreetMap から取得できます — 取得した抽出はここでアップロードしたファイルとまったく同じ経路を通ります。",
    ),
  };
  const phasesHint = {
    label: t("현시도 확인하기", "Review the 현시도", "現示図を確認"),
    note: t(
      "대전 신호 시트를 쓰시나요? 현시도는 그림이라 어떤 파싱도 접근로–현시 배정을 산출하지 못합니다. 검토 화면에서 사람이 확인한 배정만 신호 입력이 됩니다.",
      "Using the 대전 signal sheets? The 현시도 is a drawing, so no parse yields the approach→phase mapping. Only a mapping a human confirmed on the review screen becomes signal input.",
      "大田の信号シートをお使いですか？現示図は図面のため、解析では流入路と現示の対応を得られません。確認画面で人が確認した対応のみが信号入力になります。",
    ),
  };
  const demandHint = {
    label: t("수요 입력 만들기", "Build a demand input", "需要入力を作成"),
    note: t(
      "수요(교통량) 카운트가 있으신가요? 대전 스마트교차로 워크북을 올리고 접근로를 링크에 연결하면 아래 목록에서 고를 수 있는 수요 입력이 만들어집니다.",
      "Have traffic counts? Upload a Daejeon smart-intersection workbook and bind each approach to a link, and a demand input the dropdown below can pick up is built.",
      "交通量カウントをお持ちですか？大田スマート交差点のワークブックをアップロードし、流入路をリンクに対応付けると、下の一覧で選べる需要入力が作成されます。",
    ),
  };

  const pageCount = Math.max(1, Math.ceil(jobs.length / HISTORY_PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const visibleJobs = useMemo(
    () => jobs.slice(safePage * HISTORY_PAGE_SIZE, safePage * HISTORY_PAGE_SIZE + HISTORY_PAGE_SIZE),
    [jobs, safePage],
  );

  // UNIT #20's help drawer is RETIRED (2026-08-14). This screen's three
  // doctrine sections -- 생성 과정, 수요·신호 입력, 제외 도로 -- are verbatim in
  // lib/help-content.tsx (`genesisHelpEntries`) and render at /docs, which the
  // book icon in the app topbar opens from every route. The `?` beside the h1
  // is gone with them: a reference only reachable from the screen it describes
  // is one most readers never open.

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-8">
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center text-[22px] font-semibold tracking-tight text-white">
            {t("네트워크 생성 (Genesis)", "Network Genesis", "ネットワーク生成 (Genesis)")}
          </h1>
          <p className="mt-1 text-[15px] text-slate-500">
            {t(
              "OpenStreetMap 추출(.osm)에서 Vissim 네트워크 초안을 만들고, R1.5 검증 게이트를 통과한 것만 프로젝트에 등록합니다.",
              "Build a draft Vissim network from an OpenStreetMap extract (.osm); only what the R1.5 gate lets through is registered in the project.",
              "OpenStreetMap抽出(.osm)からVissimネットワークの下書きを作成し、R1.5検証ゲートを通過したものだけをプロジェクトに登録します。",
            )}
          </p>
        </div>
        {/* THE ONE PRIMARY ACTION (unit #23) -- top-right of the page, not
          * buried inside the list panel's own action slot (which stays
          * reserved for pagination, the Console list pattern's existing
          * rule: one control, never a toolbar). Opens the centered modal
          * below; it does not itself start anything. */}
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="ui-button-primary shrink-0 px-3.5 py-1.5 text-[15px]"
        >
          {t("새 생성", "New generation", "新規生成")}
        </button>
      </header>

      {/* ------------------------------------------------------------- */}
      {/* Generation history -- the Console list, and now the page body   */}
      {/* ------------------------------------------------------------- */}
      {/* The list IS the page (user, live pass 2026-08-14): a card titled
        * 생성 이력 under an h1 that already names this screen was the
        * repeated-title disease again, and its subtitle narrated what a row
        * click visibly does. The card chrome is gone — the table sits directly
        * under the page header; pagination keeps its top-right seat. */}
      <section className="mt-8">
        {jobs.length > HISTORY_PAGE_SIZE && (
          <div className="mb-2 flex items-center justify-end gap-1.5 text-[14px] text-slate-500">
            <span className="font-mono">
              {safePage * HISTORY_PAGE_SIZE + 1}–
              {Math.min(jobs.length, (safePage + 1) * HISTORY_PAGE_SIZE)} / {jobs.length}
            </span>
            <button
              type="button"
              disabled={safePage === 0}
              onClick={() => setPage((current) => Math.max(0, current - 1))}
              aria-label={t("이전 쪽", "Previous page", "前のページ")}
              className="ui-button-ghost rounded-md px-2 py-0.5 disabled:opacity-40"
            >
              ‹
            </button>
            <button
              type="button"
              disabled={safePage >= pageCount - 1}
              onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
              aria-label={t("다음 쪽", "Next page", "次のページ")}
              className="ui-button-ghost rounded-md px-2 py-0.5 disabled:opacity-40"
            >
              ›
            </button>
          </div>
        )}
        {jobsError && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
            {t("목록을 불러오지 못했습니다", "Failed to load the list", "一覧を読み込めませんでした")}: {jobsError}
          </div>
        )}
        {jobs.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-[15px] text-slate-500">
            {t(
              "아직 생성한 네트워크가 없습니다.",
              "No network has been generated yet.",
              "まだ生成したネットワークはありません。",
            )}
          </p>
        ) : (
          /* THE TABLE (docs/ux-backlog.md, the LIST REFERENCE): muted
            * column headers, monospace ids and coordinates, NO row borders
            * — the row height carries the separation — and the verdict as a
            * small right-aligned tag chip. */
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="text-[14px] font-normal text-slate-500">
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("이름", "Name", "名前")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("생성일", "Created", "作成日")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("영역", "Region", "領域")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-normal">
                    {t("규모", "Scale", "規模")}
                  </th>
                  <th scope="col" className="py-2 text-right font-normal">
                    {t("판정", "Verdict", "判定")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleJobs.map((job) => (
                  <tr
                    key={job.id}
                    onClick={() => router.push(`/genesis/jobs/${job.id}`)}
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        router.push(`/genesis/jobs/${job.id}`);
                      }
                    }}
                    className="cursor-pointer align-top text-[15px] hover:bg-slate-50"
                  >
                    <td className="max-w-[150px] py-2.5 pr-3">
                      <span className="block truncate font-medium text-slate-800">{job.name}</span>
                      {/* A derived filename that only re-spells the name above it is
                        * repetition, not information (user, live review 2026-08-13):
                        * it prints only when it actually differs — i.e. an upload
                        * whose file was not named after the job. */}
                      {job.source_osm.filename !== `${job.name}.osm` && (
                        <span className="block truncate font-mono text-[14px] text-slate-500">
                          {job.source_osm.filename}
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap py-2.5 pr-3 font-mono text-[14px] text-slate-500">
                      {formatTimestamp(job.created_at, lang)}
                    </td>
                    <td className="max-w-[160px] py-2.5 pr-3 font-mono text-[14px] text-slate-500">
                      <span className="block truncate" title={requestedRegionLabel(job.requested_region, t)}>
                        {requestedRegionLabel(job.requested_region, t)}
                      </span>
                    </td>
                    <td className="max-w-[150px] py-2.5 pr-3 font-mono text-[14px] text-slate-500">
                      <span className="block truncate" title={networkScaleLabel(job.network, t)}>
                        {networkScaleLabel(job.network, t)}
                      </span>
                    </td>
                    <td className="py-2.5 text-right">
                      <span
                        className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-[14px] font-semibold ${statusClasses(job.status)}`}
                      >
                        {statusLabel(job.status, t)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ------------------------------------------------------------- */}
      {/* 새 생성 -- the generation form, transplanted whole into a       */}
      {/* centered modal (unit #23). Field state stays on THIS page,     */}
      {/* not inside the dialog: closing the dialog only hides the tree, */}
      {/* it does not discard a draft, and a submit that resolves after  */}
      {/* the reader already closed it can still finish cleanly (the     */}
      {/* dialog's own `open` flag is read fresh on every render, so     */}
      {/* `setModalOpen(false)` after an already-closed dialog is a      */}
      {/* no-op, never a double-close).                                  */}
      {/* ------------------------------------------------------------- */}
      <ModalDialog
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={t("새 생성", "New generation", "新規生成")}
      >
        <div className="flex flex-col gap-4">
          {/* Step 1 is a BINARY — a local file or a drawn region — so its
            * two halves sit on one row (backlog item 6, finished in #18).
            * The extract link used to live in a chip row at the bottom of
            * the form, three visual steps away from the input it is the
            * alternative to. */}
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".osm,.xml"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="block min-w-[200px] flex-1 text-[15px] text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white/[0.06] file:px-3 file:py-1.5 file:text-[15px] file:font-semibold file:text-slate-700"
            />
            <span className="flex items-center gap-2 whitespace-nowrap">
              <span className="text-[14px] text-slate-500">{t("또는", "or", "または")}</span>
              <span title={regionHint.note}>
                <Link href="/genesis/region" className={CROSS_CHIP}>
                  <PlusGlyph />
                  {regionHint.label}
                </Link>
              </span>
            </span>
          </div>

          <label className="flex flex-col gap-1 text-[15px] text-slate-600">
            {t("네트워크 이름 (선택)", "Network name (optional)", "ネットワーク名（任意）")}
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={file ? file.name.replace(/\.(osm|xml)$/i, "") : "murdoch"}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[15px] text-slate-800"
            />
          </label>

          <label className="flex items-start gap-2 text-[15px] text-slate-600">
            <input
              type="checkbox"
              checked={excludeService}
              onChange={(event) => setExcludeService(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              {t(
                "highway=service 도로 제외 (주차장 통로·진입로·뒷골목)",
                "Exclude highway=service ways (car-park aisles, driveways, back lanes)",
                "highway=service の道路を除外（駐車場通路・進入路・裏路地）",
              )}
              {/* Unit #20: the disclosure that excluding is not deleting is
                * doctrine, so it lives in the page's own help drawer (`?`
                * beside the h1) under 제외 도로 -- same sentence, verbatim. */}
            </span>
          </label>

          {/* The optional layers' pickers. Both default to the empty option
            * (none), which is the geometry-only path this form has always
            * taken -- picking one only NAMES a token already staged and
            * validated; nothing here builds or edits a document. */}
          <div className="flex flex-wrap gap-3">
            <label className="flex min-w-[250px] flex-1 flex-col gap-1 text-[15px] text-slate-600">
              <span className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                {t("수요 입력 (선택)", "Demand input (optional)", "需要入力（任意）")}
                <span title={demandHint.note}>
                  <Link href="/genesis/demand" className={CROSS_CHIP}>
                    <PlusGlyph />
                    {t("만들기", "Build", "作成")}
                  </Link>
                </span>
              </span>
              <select
                value={demandToken}
                onChange={(event) => setDemandToken(event.target.value)}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[15px] text-slate-800"
              >
                <option value="">{t("없음", "none", "なし")}</option>
                {sideInputs
                  .filter((entry) => entry.kind === "demand")
                  .map((entry) => (
                    <option key={entry.token} value={entry.token}>
                      {sideInputLabel(entry)}
                    </option>
                  ))}
              </select>
            </label>
            <label className="flex min-w-[250px] flex-1 flex-col gap-1 text-[15px] text-slate-600">
              <span className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                {t("신호 입력 (선택)", "Signals input (optional)", "信号入力（任意）")}
                <span title={phasesHint.note}>
                  <Link href="/genesis/phases" className={CROSS_CHIP}>
                    <PlusGlyph />
                    {t("현시도 확인", "Confirm 현시도", "現示図確認")}
                  </Link>
                </span>
              </span>
              <select
                value={signalsToken}
                onChange={(event) => setSignalsToken(event.target.value)}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[15px] text-slate-800"
              >
                <option value="">{t("없음", "none", "なし")}</option>
                {sideInputs
                  .filter((entry) => entry.kind === "signals")
                  .map((entry) => (
                    <option key={entry.token} value={entry.token}>
                      {sideInputLabel(entry)}
                    </option>
                  ))}
              </select>
            </label>
          </div>

          {submitError && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
              <p className="font-semibold">
                {submitError instanceof GenesisError
                  ? refusalHeadline(submitError.code, t, generationRefusalFallback(t))
                  : t("생성을 시작하지 못했습니다", "Could not start the generation", "生成を開始できませんでした")}
              </p>
              <p className="mt-1 whitespace-pre-wrap font-mono text-[14px] leading-relaxed">
                {submitError.message}
              </p>
            </div>
          )}

          <div className="flex items-center justify-between gap-3 border-t border-slate-200 pt-4">
            <details>
              <summary className="cursor-pointer text-[14px] text-slate-500">
                {t(
                  "추출·현시도·수요 입력이 무엇인가요?",
                  "What are extract, 현시도 and demand input?",
                  "抽出・現示図・需要入力とは？",
                )}
              </summary>
              <ul className="mt-2 space-y-1.5">
                {[regionHint, phasesHint, demandHint].map((entry) => (
                  <li key={entry.label} className="text-[14px] leading-relaxed text-slate-600">
                    <span className="font-semibold text-slate-700">{entry.label}</span> — {entry.note}
                  </li>
                ))}
              </ul>
            </details>
            <button
              type="button"
              disabled={!file || submitting}
              onClick={() => void submit()}
              className="ui-button-primary shrink-0 px-3.5 py-1.5 text-[15px] disabled:cursor-not-allowed"
            >
              {submitting
                ? t("시작하는 중…", "Starting…", "開始中…")
                : t("생성 시작", "Start generation", "生成を開始")}
            </button>
          </div>
        </div>
      </ModalDialog>
    </div>
  );
}

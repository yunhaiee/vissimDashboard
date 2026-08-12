"use client";

// Roadmap v2 §5.B row B1 (genesis becomes a product feature) + §5.A row A4
// (the gate is its exit door) + the minimal half of row B3 (the gate's verdict
// becomes visible to a human).
//
// Until this page the whole chain ran from backend/tools/*.py and a consultant
// had no button. Three backend surfaces, each read from the endpoint that OWNS
// its data rather than re-derived here:
//   * POST /api/genesis/uploads  -- stage the .osm (raw body + X-Filename, the
//     house two-step because this backend has no python-multipart);
//   * POST /api/genesis/jobs     -- start one generation, then poll
//     GET /api/genesis/jobs/{id} exactly as the browser would poll a run;
//   * GET  /api/genesis/jobs/{id}/gate -- the STORED verdict, check by check.
//
// WHAT THIS PAGE MUST NOT DO, and the reason it is written this way:
// the R1.5 gate is a TRI-state per check (pass / fail / not-checkable) and a
// tri-state verdict, and "a check that could not run is never a pass" is the
// design rule the whole gate hangs on (docs/phase2/DECISIONS.md, 2026-08-11).
// So a not-checkable check is rendered in its own colour with its own word and
// its gap sentence, never as a grey "pending" or folded into a green tick, and a
// `provisional` publication says so on the card. Collapsing the states into
// ok/not-ok here would manufacture exactly the confidence the gate exists to
// refuse.
//
// Every gate sentence on this page comes from the backend: check titles,
// findings and gaps are rendered by services/genesis/gate.py's own ko/en/ja
// table and shipped in the payload. This file supplies the CHROME's ko/en/ja
// only, through the three-argument t() form (explicit ja) so no string here
// depends on an entry landing in the generated lib/i18n-ja.ts dictionary.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import GateFindingsMap from "@/components/genesis/GateFindingsMap";
import GenesisComparisonPanel from "@/components/genesis/GenesisComparisonPanel";
import { Panel, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  fetchGenesisGateReport,
  fetchGenesisJob,
  fetchGenesisJobs,
  fetchStagedSideInputs,
  genesisArtifactUrl,
  startGenesisJob,
  uploadGenesisOsm,
  type GenesisGateCheck,
  type GenesisGateReport,
  type GenesisJob,
  type GenesisJobStatus,
  type StagedSideInputEntry,
} from "@/lib/api";
import { formatTimestamp, statusLabel } from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** How often a running job is re-read. Generation is seconds-to-a-minute of
 * pure Python (no COM, no Vissim), so a one-second poll is both cheap and
 * fast enough to feel live. */
const POLL_INTERVAL_MS = 1000;
//: A terminal job is still re-gatable (attach_live_check), so it keeps being
//: observed -- just slowly. 15 s is chosen against what it is watching for: a
//: human on another machine running the live probe and posting its state.json,
//: an action measured in minutes. Fast enough that nobody stares at a verdict
//: that has already been overturned, slow enough to be invisible in the logs.
const TERMINAL_RECHECK_MS = 15000;

const TERMINAL: GenesisJobStatus[] = ["refused", "provisional", "published", "error"];

function isTerminal(status: GenesisJobStatus): boolean {
  return TERMINAL.includes(status);
}

// `statusLabel` (the four terminal states, each said in its own words --
// `provisional` deliberately does NOT read as success) and `formatTimestamp`
// now live in lib/genesisArtifacts.ts -- `GenesisComparisonPanel`'s job picker
// needs the exact same wording for the exact same fields, and a second,
// locally-drifting copy is how a picker option ends up showing a raw
// `job.status`/ISO timestamp next to this page's already-translated ones.

function statusClasses(status: GenesisJobStatus): string {
  switch (status) {
    case "published":
      return "border-emerald-300 bg-emerald-50 text-emerald-900";
    case "provisional":
      return "border-amber-300 bg-amber-50 text-amber-900";
    case "refused":
    case "error":
      return "border-rose-300 bg-rose-50 text-rose-900";
    default:
      return "border-sky-300 bg-sky-50 text-sky-900";
  }
}

/** PASS / FAIL / NOT CHECKABLE — three words, three colours, never two.
 * `not_checkable` gets amber and its own noun because R1.5 forbids counting it
 * as a pass, and a UI that greyed it out would be doing exactly that. */
function checkStateLabel(state: GenesisGateCheck["state"], t: Translate): string {
  switch (state) {
    case "pass":
      return t("통과", "PASS", "合格");
    case "fail":
      return t("실패", "FAIL", "不合格");
    case "not_checkable":
      return t("확인 불가", "NOT CHECKABLE", "確認不可");
    default:
      return state;
  }
}

function checkStateClasses(state: GenesisGateCheck["state"]): string {
  if (state === "pass") return "border-emerald-300 bg-emerald-50 text-emerald-900";
  if (state === "fail") return "border-rose-300 bg-rose-50 text-rose-900";
  return "border-amber-300 bg-amber-50 text-amber-900";
}

function verdictLabel(verdict: GenesisGateReport["verdict"], t: Translate): string {
  switch (verdict) {
    case "pass":
      return t("통과 — 출하 가능", "PASS — shippable", "合格 — 出荷可");
    case "refused":
      return t("거부 — 결함이 입증되었습니다", "REFUSED — a fault was proven", "拒否 — 欠陥が証明されました");
    case "incomplete":
      return t(
        "미완 — 실행되지 못한 거부 등급 검사가 있습니다 (통과 아님)",
        "INCOMPLETE — a refuse-tier check could not run (not a pass)",
        "未完 — 実行できなかった拒否ティア検査があります（合格ではありません）",
      );
    default:
      return verdict;
  }
}

/** The generation form's own unknown-code sentence, handed to the shared
 * `refusalHeadline` as its fallback: on THIS screen the refused request is a
 * generation, so the generic "요청이 거부되었습니다" would be a step vaguer than
 * the page can afford. Every known code (including the four
 * `genesis_upload_*`/`osm_unreadable`/`draft_refused` ones this page raises)
 * lives in components/genesis/ui.tsx now -- see that file for why four copies
 * became one. */
function generationRefusalFallback(t: Translate): string {
  return t("생성이 거부되었습니다", "Generation was refused", "生成が拒否されました");
}

/** One dropdown line for a staged side input -- filename plus whatever its
 * OWN summary already states (never re-derived here, so the picker cannot say
 * something the staging response did not). `demand`'s summary carries
 * `sites_mapped`/`approaches_mapped`; `signals`'s carries `assignments`. Falls
 * back to just the filename for a shape this picker does not recognise, which
 * covers a future kind without this file needing to know about it first. */
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

/**
 * COLLAPSED BY DEFAULT, OPENED BY SEVERITY. UX pass U-1: all nine R1.5 checks
 * used to render fully expanded -- id, title, tier, every gap, every finding,
 * every warning -- so a PUBLISHED generation (nine passes, nothing to read)
 * produced the same wall of text as a refused one, and the two or three rows
 * that actually decided the verdict had no more visual weight than the six that
 * did not.
 *
 * Nothing is dropped: the header line (id + state chip + tier + title) is
 * ALWAYS on screen for every check, and the body is one click away for the
 * checks that collapse. What decides the default is the state, not the reader:
 *
 *   * `fail` opens -- a refuse-tier fault is why this screen exists;
 *   * `not_checkable` opens -- R1.5's own rule is that a check which could not
 *     RUN is never a pass, and a closed row would let it read like one;
 *   * anything carrying gaps, findings, warnings or an omitted-findings count
 *     opens -- a warn-tier finding on a passing check is still a finding.
 *
 * Only a clean pass with nothing recorded starts closed, and it still opens on
 * click (saying so, rather than expanding into a blank).
 *
 * This is `GateFindingsMap`'s `autoOpen` precedent applied per row: the same
 * "collapse by default, auto-open on the state that matters" shape, not a
 * second disclosure idea.
 */
/** The cross-navigation chip. One constant so the three read as one row and a
 * fourth entry cannot drift into its own shape. */
const CROSS_CHIP =
  "rounded-full border border-slate-300 bg-white/[0.06] px-2.5 py-1 font-semibold text-slate-700";

function checkAutoOpens(check: GenesisGateCheck): boolean {
  if (check.state !== "pass") return true;
  return (
    check.gaps.length > 0 ||
    check.findings.length > 0 ||
    check.warnings.length > 0 ||
    check.findings_omitted > 0
  );
}

function GateCheckRow({ check, t }: { check: GenesisGateCheck; t: Translate }) {
  const [open, setOpen] = useState(() => checkAutoOpens(check));
  const hasBody =
    check.gaps.length > 0 ||
    check.findings.length > 0 ||
    check.warnings.length > 0 ||
    check.findings_omitted > 0;

  return (
    <li className={`rounded-lg border px-3.5 py-2.5 text-xs ${checkStateClasses(check.state)}`}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-baseline gap-2 text-left"
      >
        <span className="font-mono text-[11px] font-bold">{check.id}</span>
        <span className="rounded-full border border-current/30 px-1.5 py-0.5 text-[10px] font-bold uppercase">
          {checkStateLabel(check.state, t)}
        </span>
        <span className="text-[10px] opacity-70">
          {check.tier === "refuse"
            ? t("거부 등급", "refuse tier", "拒否ティア")
            : t("경고 등급", "warn tier", "警告ティア")}
        </span>
        <span className="min-w-0 flex-1 font-semibold">{check.title}</span>
        <span aria-hidden className="shrink-0 text-[10px] opacity-60">
          {open ? "▾" : "▸"}
        </span>
      </button>

      {open && (
        <div className="mt-1.5">
          {check.gaps.length > 0 && (
            <ul className="space-y-0.5">
              {check.gaps.map((gap) => (
                <li key={gap.code} className="text-[11px] leading-relaxed">
                  {t("실행 불가", "could not run", "実行不可")}: {gap.message}
                </li>
              ))}
            </ul>
          )}

          {check.findings.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {check.findings.map((finding, index) => (
                <li key={`${finding.code}-${index}`} className="text-[11px] leading-relaxed">
                  {finding.message}
                </li>
              ))}
            </ul>
          )}
          {check.findings_omitted > 0 && (
            <p className="mt-1 text-[11px] opacity-80">
              {t(
                `이 화면에 표시되지 않은 항목 ${check.findings_omitted}건 — gate.json 전체 내려받기에 모두 들어 있습니다.`,
                `${check.findings_omitted} more not shown here — all of them are in the gate.json download.`,
                `この画面に表示されていない項目 ${check.findings_omitted} 件 — gate.json のダウンロードにすべて含まれています。`,
              )}
            </p>
          )}

          {check.warnings.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {check.warnings.map((warning, index) => (
                <li key={`w-${warning.code}-${index}`} className="text-[11px] leading-relaxed">
                  warn / {warning.message}
                </li>
              ))}
            </ul>
          )}

          {!hasBody && (
            <p className="text-[11px] leading-relaxed opacity-70">
              {t(
                "이 검사에 기록된 항목이 없습니다.",
                "Nothing was recorded for this check.",
                "この検査に記録された項目はありません。",
              )}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GenesisPage() {
  const { lang, t } = useLanguage();

  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [excludeService, setExcludeService] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<GenesisError | Error | null>(null);

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
  const [selectedId, setSelectedId] = useState<string>("");
  // Both fetched payloads are stored WITH the key they were fetched for, and
  // read back only when that key still matches. Clearing them from inside the
  // effect body instead would mean a synchronous setState on every selection
  // change -- a cascading render, and (worse) a frame in which the previous
  // job's verdict is on screen under the new job's heading.
  const [selectedEntry, setSelectedEntry] = useState<{ id: string; record: GenesisJob } | null>(null);
  const [reportEntry, setReportEntry] = useState<
    { jobId: string; language: string; payload: GenesisGateReport } | null
  >(null);
  const [reportError, setReportError] = useState<string | null>(null);

  const selected = selectedEntry && selectedEntry.id === selectedId ? selectedEntry.record : null;
  const report =
    reportEntry && selected && reportEntry.jobId === selected.id && reportEntry.language === lang
      ? reportEntry.payload
      : null;

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const entries = await fetchGenesisJobs();
      setJobs(entries);
      setJobsError(null);
      setSelectedId((current) => current || entries[0]?.id || "");
    } catch (caught) {
      setJobsError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

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

  // Poll the SELECTED job while it is still running. The job record is the only
  // source of truth for progress; nothing here guesses a percentage the backend
  // did not state.
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const record = await fetchGenesisJob(selectedId);
        if (cancelled) return;
        setSelectedEntry({ id: selectedId, record });
        if (!isTerminal(record.status)) {
          timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
        } else {
          void loadJobs();
          // Terminal is NOT final. `attach_live_check` re-gates an already
          // terminal job: a provisional network can become published, or —
          // if the evidence records load_ok: false — refused, with its
          // publication revoked. Whoever attaches that evidence is usually not
          // the person watching this page (the probe runs on the COM box), so
          // stopping the poll here left an operator reading a verdict that had
          // already been overturned. Keep observing, slowly: the gate-report
          // effect below is keyed on `checked_at`, which only changes when the
          // record is re-fetched, so this is what makes that key mean anything.
          timer = setTimeout(() => void tick(), TERMINAL_RECHECK_MS);
        }
      } catch (caught) {
        if (!cancelled) setJobsError(caught instanceof Error ? caught.message : String(caught));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedId, loadJobs]);

  // The gate report, once the selected job has one. Read from the STORED
  // verdict endpoint, so what is on screen is the verdict that actually decided
  // whether the artifact was published -- never a fresh one that could differ.
  // `selected` is replaced on every poll tick, so the effect is keyed on the
  // job id plus the moment the verdict was decided plus the language: one gate
  // fetch per (job, verdict, language) rather than one per second. `checked_at`
  // is in the key rather than omitted because a re-gate (live-check evidence)
  // changes the verdict WITHOUT changing the job id.
  const gatedJobId = selected?.gate ? selected.id : null;
  const gateCheckedAt = selected?.gate?.checked_at ?? null;
  useEffect(() => {
    if (!gatedJobId) return;
    let cancelled = false;
    fetchGenesisGateReport(gatedJobId, lang)
      .then((payload) => {
        if (!cancelled) {
          setReportEntry({ jobId: gatedJobId, language: lang, payload });
          setReportError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setReportError(caught instanceof Error ? caught.message : String(caught));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gatedJobId, gateCheckedAt, lang]);

  const submit = useCallback(async () => {
    if (!file) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const staged = await uploadGenesisOsm(file);
      const started = await startGenesisJob({
        upload_token: staged.token,
        name: name.trim() || undefined,
        exclude_service: excludeService,
        demand_token: demandToken || undefined,
        signals_token: signalsToken || undefined,
      });
      setSelectedId(started.id);
      setSelectedEntry({ id: started.id, record: started });
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
    label: t("영역에서 추출하기 →", "Extract a region →", "領域から抽出 →"),
    note: t(
      ".osm 파일이 없으신가요? 지도 영역을 직접 지정해 OpenStreetMap에서 받아올 수 있습니다 — 받은 추출은 여기 업로드한 파일과 똑같은 경로를 탑니다.",
      "No .osm file? Name a map region and it is fetched from OpenStreetMap for you — the extract then takes exactly the same road as a file uploaded here.",
      ".osm ファイルがありませんか？地図の領域を指定して OpenStreetMap から取得できます — 取得した抽出はここでアップロードしたファイルとまったく同じ経路を通ります。",
    ),
  };
  const phasesHint = {
    label: t("현시도 확인하기 →", "Review the 현시도 →", "現示図を確認 →"),
    note: t(
      "대전 신호 시트를 쓰시나요? 현시도는 그림이라 어떤 파싱도 접근로–현시 배정을 산출하지 못합니다. 검토 화면에서 사람이 확인한 배정만 신호 입력이 됩니다.",
      "Using the 대전 signal sheets? The 현시도 is a drawing, so no parse yields the approach→phase mapping. Only a mapping a human confirmed on the review screen becomes signal input.",
      "大田の信号シートをお使いですか？現示図は図面のため、解析では流入路と現示の対応を得られません。確認画面で人が確認した対応のみが信号入力になります。",
    ),
  };
  const demandHint = {
    label: t("수요 입력 만들기 →", "Build a demand input →", "需要入力を作成 →"),
    note: t(
      "수요(교통량) 카운트가 있으신가요? 대전 스마트교차로 워크북을 올리고 접근로를 링크에 연결하면 아래 목록에서 고를 수 있는 수요 입력이 만들어집니다.",
      "Have traffic counts? Upload a Daejeon smart-intersection workbook and bind each approach to a link, and a demand input the dropdown below can pick up is built.",
      "交通量カウントをお持ちですか？大田スマート交差点のワークブックをアップロードし、流入路をリンクに対応付けると、下の一覧で選べる需要入力が作成されます。",
    ),
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          {t("네트워크 생성 (Genesis)", "Network Genesis", "ネットワーク生成 (Genesis)")}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          {t(
            "OpenStreetMap 추출(.osm)에서 Vissim 네트워크 초안을 만들고, R1.5 검증 게이트를 통과한 것만 프로젝트에 등록합니다.",
            "Build a draft Vissim network from an OpenStreetMap extract (.osm); only what the R1.5 gate lets through is registered in the project.",
            "OpenStreetMap抽出(.osm)からVissimネットワークの下書きを作成し、R1.5検証ゲートを通過したものだけをプロジェクトに登録します。",
          )}
        </p>
      </header>

      {/* ----------------------------------------------------------------- */}
      {/* Start a generation                                                 */}
      {/* ----------------------------------------------------------------- */}
      <Panel
        title={t("새 생성", "New generation", "新規生成")}
        subtitle={t(
          "로컬 .osm 파일을 선택하세요. 네트워크는 잡 큐에서 만들어지며, Vissim COM은 사용하지 않습니다.",
          "Pick a local .osm file. The network is built on the job queue and no Vissim COM is used.",
          "ローカルの .osm ファイルを選んでください。ネットワークはジョブキューで作成され、Vissim COM は使いません。",
        )}
      >
        <div className="flex flex-col gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".osm,.xml"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="block w-full text-xs text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white/[0.06] file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-slate-700"
          />

          <label className="flex flex-col gap-1 text-xs text-slate-600">
            {t("네트워크 이름 (선택)", "Network name (optional)", "ネットワーク名（任意）")}
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={file ? file.name.replace(/\.(osm|xml)$/i, "") : "murdoch"}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs text-slate-800"
            />
          </label>

          <label className="flex items-start gap-2 text-xs text-slate-600">
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
              <span className="mt-0.5 block text-[11px] text-slate-400">
                {t(
                  "제외된 도로는 버려지지 않고 이름이 남습니다 — 준수 리포트에 사유와 함께 기록됩니다.",
                  "Excluded ways are never dropped silently — each is named with its reason in the compliance report.",
                  "除外された道路は黙って捨てられず、準拠レポートに理由とともに記録されます。",
                )}
              </span>
            </span>
          </label>

          {/* The optional layers' pickers. Both default to the empty option
            * (none), which is the geometry-only path this form has always
            * taken -- picking one only NAMES a token already staged and
            * validated; nothing here builds or edits a document. */}
          <div className="flex flex-wrap gap-3">
            <label className="flex flex-1 flex-col gap-1 text-xs text-slate-600">
              {t("수요 입력 (선택)", "Demand input (optional)", "需要入力（任意）")}
              <select
                value={demandToken}
                onChange={(event) => setDemandToken(event.target.value)}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs text-slate-800"
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
            <label className="flex flex-1 flex-col gap-1 text-xs text-slate-600">
              {t("신호 입력 (선택)", "Signals input (optional)", "信号入力（任意）")}
              <select
                value={signalsToken}
                onChange={(event) => setSignalsToken(event.target.value)}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs text-slate-800"
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

          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={!file || submitting}
              onClick={() => void submit()}
              /* `.ui-button-primary` (app/globals.css's sanctioned button
               * primitive, `@layer components`) instead of a fourth hand-rolled
               * chip: it already carries the inverted palette's correct
               * foreground and its own `:disabled` opacity, which is what the
               * old `bg-slate-900 + text-ink + disabled:bg-slate-300` triple
               * was reimplementing by hand. */
              className="ui-button-primary px-3.5 py-1.5 text-xs disabled:cursor-not-allowed"
            >
              {submitting
                ? t("시작하는 중…", "Starting…", "開始中…")
                : t("생성 시작", "Start generation", "生成を開始")}
            </button>
            <span className="text-[11px] text-slate-400">
              {t(
                "생성에는 로그인이 필요합니다.",
                "Starting a generation requires a signed-in user.",
                "生成の開始にはログインが必要です。",
              )}
            </span>
          </div>

          {submitError && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
              <p className="font-semibold">
                {submitError instanceof GenesisError
                  ? refusalHeadline(submitError.code, t, generationRefusalFallback(t))
                  : t("생성을 시작하지 못했습니다", "Could not start the generation", "生成を開始できませんでした")}
              </p>
              <p className="mt-1 whitespace-pre-wrap font-mono text-[11px] leading-relaxed">
                {submitError.message}
              </p>
            </div>
          )}
        </div>

        {/* UX pass U-1: these three used to be full-paragraph notice boxes
          * stacked ABOVE the file input -- three secondary navigations
          * outranking the primary control, and the first thing a first-time
          * user read on this page. They are now a chip row BELOW the flow
          * they support, with every sentence kept (one click away, and on
          * hover) rather than rewritten or dropped:
          *   * /genesis/region  -- roadmap v2 §5.B B2. The region page ENDS at
          *     the staging area and hands its extract to this same flow, so
          *     the two entry points differ only in where the .osm came from;
          *     generation, gating and publishing are one road.
          *   * /genesis/phases  -- roadmap C1-b. The signals layer needs one
          *     fact no parse can produce (which approach runs in which 현시),
          *     because the 대전 sheet prints it as a drawing; a named human
          *     confirms it, and only then does it become a side input.
          *   * /genesis/demand  -- a counts workbook + a browser-typed
          *     site→leg→link map become a token the dropdown above can pick
          *     up: the same "build once, attach here" shape.
          * Each `note` is used TWICE (the chip's title and the disclosure
          * body) from one constant, so the two can never drift apart. */}
        <div className="mt-4 border-t border-slate-200 pt-3">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[11px]">
            <span className="text-slate-500">
              {t("입력이 더 필요하신가요?", "Need another input?", "他の入力が必要ですか？")}
            </span>
            {/* `title` sits on the WRAPPER, never on the anchor. Measured in
              * the running app: a `title` on a link with its own visible text
              * becomes that link's accessible NAME, so screen-reader and
              * voice-control users would hear/have to say the whole paragraph
              * instead of the label they can see (WCAG 2.5.3, label in name).
              * On the span it is a plain hover tooltip and the link keeps its
              * own label. */}
            <span title={regionHint.note}>
              <Link href="/genesis/region" className={CROSS_CHIP}>
                {regionHint.label}
              </Link>
            </span>
            <span title={phasesHint.note}>
              <Link href="/genesis/phases" className={CROSS_CHIP}>
                {phasesHint.label}
              </Link>
            </span>
            <span title={demandHint.note}>
              <Link href="/genesis/demand" className={CROSS_CHIP}>
                {demandHint.label}
              </Link>
            </span>
          </div>
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] text-slate-500">
              {t("각각 무엇인가요?", "What is each of these?", "それぞれ何ですか？")}
            </summary>
            <ul className="mt-1.5 space-y-1">
              {[regionHint, phasesHint, demandHint].map((entry) => (
                <li key={entry.label} className="text-[11px] leading-relaxed text-slate-600">
                  <span className="font-semibold text-slate-700">{entry.label}</span> — {entry.note}
                </li>
              ))}
            </ul>
          </details>
        </div>
      </Panel>

      {/* ----------------------------------------------------------------- */}
      {/* Job list                                                           */}
      {/* ----------------------------------------------------------------- */}
      <Panel
        title={t("생성 이력", "Generations", "生成履歴")}
        subtitle={t(
          "이 프로젝트의 생성만 보입니다.",
          "Only this project's generations are listed.",
          "このプロジェクトの生成のみ表示されます。",
        )}
      >
        {jobsError && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {t("목록을 불러오지 못했습니다", "Failed to load the list", "一覧を読み込めませんでした")}: {jobsError}
          </div>
        )}
        {jobs.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-xs text-slate-500">
            {t(
              "아직 생성한 네트워크가 없습니다.",
              "No network has been generated yet.",
              "まだ生成したネットワークはありません。",
            )}
          </p>
        ) : (
          <ul className="space-y-1.5">
            {jobs.map((job) => (
              <li key={job.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(job.id)}
                  className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-xs ${
                    job.id === selectedId ? "border-slate-400 bg-slate-50" : "border-slate-200 bg-transparent"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold text-slate-800">{job.name}</span>
                    <span className="block truncate text-[11px] text-slate-500">
                      {job.source_osm.filename} · {formatTimestamp(job.created_at, lang)}
                    </span>
                  </span>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClasses(job.status)}`}
                  >
                    {statusLabel(job.status, t)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* ----------------------------------------------------------------- */}
      {/* The selected generation and its gate verdict                       */}
      {/* ----------------------------------------------------------------- */}
      {selected && (
        <Panel
          title={t("게이트 판정", "Gate verdict", "ゲート判定")}
          subtitle={t(
            "R1.5 검증 게이트 V1–V9. 실행되지 못한 검사는 절대 통과가 아닙니다.",
            "The R1.5 validation gate, V1–V9. A check that could not run is never a pass.",
            "R1.5検証ゲート V1–V9。実行できなかった検査は決して合格ではありません。",
          )}
        >
          <div
            className={`mb-3 rounded-lg border px-3.5 py-3 text-xs ${statusClasses(selected.status)}`}
          >
            <p className="text-sm font-bold">{statusLabel(selected.status, t)}</p>
            {selected.network && (
              <p className="mt-1">
                {t("링크", "Links", "リンク")} {selected.network.links} ·{" "}
                {t("커넥터", "Connectors", "コネクタ")} {selected.network.connectors} ·{" "}
                {t("노드", "Nodes", "ノード")} {selected.network.nodes}
              </p>
            )}
            {!isTerminal(selected.status) && (
              <p className="mt-1">
                {t("진행", "Phase", "進行")}: {selected.phase} ({Math.round(selected.progress * 100)}%)
              </p>
            )}
            {selected.status === "provisional" && (
              <p className="mt-1.5 leading-relaxed">
                {t(
                  "프로젝트에 등록되었습니다. 다만 실행되지 못한 거부 등급 검사가 있어 잠정 상태입니다 — 생성 잡은 Vissim COM을 열 수 없으므로, 실제 Vissim이 이 파일을 여는 것을 증명하는 V1은 라이브 검사 증거를 붙여야 확정됩니다.",
                  "Registered in the project, but provisional: a refuse-tier check could not run. A generation job may not open Vissim COM, so V1 — the only check that proves a real Vissim opens this file — stays open until live-check evidence is attached.",
                  "プロジェクトに登録されましたが、実行できなかった拒否ティア検査があるため暫定です。生成ジョブは Vissim COM を開けないため、実際の Vissim がこのファイルを開くことを証明する V1 はライブ検査の証拠を添付するまで確定しません。",
                )}
              </p>
            )}
            {selected.status === "refused" && (
              <p className="mt-1.5 leading-relaxed">
                {t(
                  "거부 등급 검사가 결함을 입증했으므로 이 네트워크는 프로젝트에 등록되지 않았습니다. 파일과 판정은 아래에서 그대로 받아볼 수 있습니다.",
                  "A refuse-tier check proved a fault, so this network was NOT registered in the project. The file and the verdict are both downloadable below.",
                  "拒否ティアの検査が欠陥を証明したため、このネットワークはプロジェクトに登録されていません。ファイルと判定は下からそのまま取得できます。",
                )}
              </p>
            )}
            {selected.revocation && (
              <p className="mt-1.5 leading-relaxed">
                {selected.revocation.removed_from_workspace
                  ? t(
                      "라이브 증거가 들어온 뒤 게이트가 이 네트워크를 거부하여, 프로젝트에서 등록을 해제했습니다.",
                      "After live evidence arrived the gate refused this network, so its registration was removed from the project.",
                      "ライブ証拠が届いた後にゲートがこのネットワークを拒否したため、プロジェクトから登録を解除しました。",
                    )
                  : t(
                      "게이트가 이 네트워크를 거부했습니다. 프로젝트의 현재 데이터셋은 이미 다른 파일이므로 아무것도 삭제하지 않았습니다.",
                      "The gate refused this network. The project's active dataset is no longer this artifact, so nothing was deleted.",
                      "ゲートがこのネットワークを拒否しました。プロジェクトの現在のデータセットは既に別のファイルのため、何も削除していません。",
                    )}
              </p>
            )}
            {selected.refusal && (
              <div className="mt-1.5">
                <p className="font-semibold">
                  {refusalHeadline(selected.refusal.code, t, generationRefusalFallback(t))}
                </p>
                <ul className="mt-1 space-y-0.5">
                  {selected.refusal.reasons.map((reason, index) => (
                    <li key={index} className="font-mono text-[11px] leading-relaxed">
                      {reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {selected.gate?.live_evidence && (
            <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-[11px] text-slate-700">
              {/* "Attestation", never "proof". The state.json arrives over
                * HTTP, so this app cannot establish that Vissim ran — it can
                * only record who says it did. Naming that here is the whole
                * point: an accountable human claim is genuinely useful, and it
                * is not the same thing as a machine-verified load. */}
              <p className="font-semibold text-slate-800">
                {t(
                  "라이브 검사 증언 (운영자 진술)",
                  "Live-check attestation (operator's statement)",
                  "ライブ検査の証言（運用者の申告）",
                )}
              </p>
              <p className="mt-0.5">
                {selected.gate.live_evidence.label} · {selected.gate.live_evidence.network} ·{" "}
                {selected.gate.live_evidence.vissim_version ??
                  t("버전 미기록", "version not recorded", "バージョン未記録")}
              </p>
              <p className="mt-0.5">
                {selected.gate.live_evidence.admitted
                  ? t("채택됨 (동일 파일 확인)", "Admitted (same file proven)", "採用（同一ファイル確認）")
                  : t(
                      "채택되지 않음 — 이 증거는 다른 파일에 대한 것입니다",
                      "Not admitted — this evidence is about a different file",
                      "採用されず — この証拠は別のファイルに関するものです",
                    )}
                {selected.gate.live_evidence.gap_codes.length > 0 &&
                  ` (${selected.gate.live_evidence.gap_codes.join(", ")})`}
              </p>
              {selected.gate.live_evidence.attested_by && (
                <p className="mt-0.5">
                  {t("증언자", "Attested by", "申告者")}:{" "}
                  <span className="font-semibold">
                    {selected.gate.live_evidence.attested_by}
                  </span>
                  {selected.gate.live_evidence.attested_at &&
                    ` · ${formatTimestamp(selected.gate.live_evidence.attested_at, lang)}`}
                </p>
              )}
              {selected.gate.rests_on_attestation && (
                <p className="mt-1.5 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-900">
                  {t(
                    "이 판정의 V1은 운영자 진술에 근거합니다 — 이 앱이 Vissim 로딩을 직접 관측한 것이 아닙니다.",
                    "V1 in this verdict rests on an operator's statement — this app did not observe the Vissim load itself.",
                    "この判定のV1は運用者の申告に基づきます — このアプリがVissimの読み込みを直接観測したわけではありません。",
                  )}
                </p>
              )}
            </div>
          )}

          {reportError && (
            <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {t("게이트 결과를 불러오지 못했습니다", "Failed to load the gate result", "ゲート結果を読み込めませんでした")}
              : {reportError}
            </div>
          )}

          {report && (
            <>
              <div className="mb-3 rounded-lg border border-slate-200 bg-surface/90 px-3.5 py-3">
                <p className="text-xs font-bold text-slate-900">{verdictLabel(report.verdict, t)}</p>
                <p className="mt-1 text-[11px] text-slate-600">
                  {t("통과", "pass", "合格")} {report.counts.pass} · {t("실패", "fail", "不合格")}{" "}
                  {report.counts.fail} · {t("확인 불가", "not checkable", "確認不可")}{" "}
                  {report.counts.not_checkable}
                </p>
                <p className="mt-1 font-mono text-[11px] text-slate-400">
                  {report.network} · sha256 {report.network_sha256.slice(0, 16)}
                </p>
              </div>

              <ul className="space-y-2">
                {report.checks.map((check) => (
                  // Keyed on the VERDICT, not only the check id: the auto-open
                  // default is computed once per mount, and a re-gate (which
                  // changes `checked_at` while the job id stays put) is exactly
                  // when a row's default has to be recomputed rather than
                  // inherited from the verdict it just replaced.
                  <GateCheckRow key={`${selected.id}-${gateCheckedAt ?? ""}-${check.id}`} check={check} t={t} />
                ))}
              </ul>

              {/* Roadmap v2 §5.B B3 (완결) -- WHERE the gate's findings are,
                * spatially. Nested inside `report &&` because a highlight is
                * only meaningful once there is a stored verdict to highlight;
                * `GateFindingsMap` itself decides whether to auto-open (a
                * REFUSED verdict does), and fetches the network artifact only
                * once opened. */}
              <div className="mt-4">
                <GateFindingsMap
                  jobId={selected.id}
                  report={report}
                  hasArtifact={Boolean(selected.artifact)}
                  autoOpen={report.verdict === "refused"}
                />
              </div>
            </>
          )}

          {selected.artifact && (
            <div className="mt-4 flex flex-wrap gap-2 text-[11px]">
              <a
                href={genesisArtifactUrl(selected.id, "inpx")}
                className="rounded-md border border-slate-300 bg-white/[0.06] px-2.5 py-1 font-semibold text-slate-700"
              >
                {t("네트워크 .inpx", "Network .inpx", "ネットワーク .inpx")}
              </a>
              <a
                href={genesisArtifactUrl(selected.id, "compliance_md")}
                className="rounded-md border border-slate-300 bg-white/[0.06] px-2.5 py-1 font-semibold text-slate-700"
              >
                {t("§5 준수 리포트", "§5 compliance report", "§5 準拠レポート")}
              </a>
              <a
                href={genesisArtifactUrl(selected.id, "gate_report")}
                className="rounded-md border border-slate-300 bg-white/[0.06] px-2.5 py-1 font-semibold text-slate-700"
              >
                {t("게이트 리포트", "Gate report", "ゲートレポート")}
              </a>
              <a
                href={genesisArtifactUrl(selected.id, "gate_json")}
                className="rounded-md border border-slate-300 bg-white/[0.06] px-2.5 py-1 font-semibold text-slate-700"
              >
                gate.json
              </a>
              <a
                href={genesisArtifactUrl(selected.id, "genesis_json")}
                className="rounded-md border border-slate-300 bg-white/[0.06] px-2.5 py-1 font-semibold text-slate-700"
              >
                {t("생성기 명부", "Generator census", "生成器の名簿")}
              </a>
            </div>
          )}
        </Panel>
      )}

      {/* Roadmap v2 §5.B B3 (완결) -- prior-generation comparison. Its own
        * Panel (not nested inside the gate verdict one above) because it
        * reads a SECOND job's stored gate result, on the user's request --
        * the gate verdict panel above is about the one job selected in the
        * history list, and this one is deliberately about two.
        *
        * It carries NO `subtitle` by design. The page used to restate the
        * comparison's SCOPE -- gate results and counts only, never the
        * networks' geometry -- one line above `GenesisComparisonPanel`'s own
        * scope line, which says that in slightly different words: two sources
        * for one sentence, and the kind of duplication that drifts. The panel
        * owns that line (it is pinned there), so the page stops repeating it. */}
      {selected && (
        <Panel title={t("생성 비교", "Compare generations", "生成の比較")}>
          <GenesisComparisonPanel
            jobs={jobs}
            currentJob={selected}
            currentReport={report}
            currentReportError={reportError}
          />
        </Panel>
      )}
    </div>
  );
}

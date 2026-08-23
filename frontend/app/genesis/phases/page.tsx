"use client";

// Roadmap C1-b — 현시도 제안+확인. The confirm screen.
//
// C1 was blocked on the client transcribing each 현시도 into a table, because the
// sheet prints it as a PICTURE and no parse can produce it. This page unblocks it
// without violating 추측 금지: a vision model PROPOSES a reading offline, and a
// named human confirms or corrects it here. The confirmation — not the proposal —
// is what becomes data.
//
// WHAT THIS PAGE MUST NOT DO, and why it is written this way:
//
//   1. **No string here may imply the machine verified anything.** The proposal
//      panel is labelled as a reading, every phase shows the model's own
//      ambiguity note, and the cross-check's `observation` findings are rendered
//      in their own muted style with their own word — they corroborate a
//      measurement, they do not endorse a mapping. The only sentence on this page
//      that means "this is right" is the attestation line, and it carries a name.
//   2. **A refused or missing proposal is the SAME screen with a blank form.**
//      That is roadmap C1 option (b), delivered as a byproduct rather than as a
//      separate page — so an intersection the model could not read is one banner
//      different from one it could, not one workflow different.
//   3. **The form never builds a `genesis.signals.v1` document.** It POSTs the
//      confirmation and asks the server to stage the side input. A second
//      document builder in TypeScript could drift from the validator, and worse,
//      "an unconfirmed proposal is inert" would become a client-side convention
//      instead of a server-side refusal.
//   4. **Stale is not unconfirmed.** A confirmation made against a different
//      render is shown as 재확인 필요 WITH the attester's name still on screen —
//      collapsing it into "nobody confirmed" would hide that somebody did.
//
// Chrome strings use the three-argument t() form (explicit ja) so nothing here
// depends on an entry landing in the generated lib/i18n-ja.ts dictionary.
//
// U-1 POLISH (2026-08-13): the Console density pass and THE TINTED-BOX RULE
// stated in full above `statusClasses` in app/genesis/region/page.tsx — a tinted
// box carries its tint in the BACKGROUND and the BORDER, its text in the neutral
// ink ramp. This screen had the bug three times over (`text-emerald-900` on
// `bg-emerald-50` in the 확인됨 chip, in an AGREES finding and on the staged
// token; `text-sky-900` on `bg-sky-50` on the bound-node chip): globals.css's
// dark remap does not cover those shades, so they kept Tailwind's stock LIGHT
// values and rendered dark-on-dark. Type floor is 12px — the state chips and the
// finding rows used to be 10px and 11px, which on the screen that carries this
// product's only human attestation is the wrong place to save space.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { Panel, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  confirmPhaseDiagram,
  createBlankPhaseDiagram,
  fetchGenesisJobs,
  fetchPhaseDiagram,
  fetchPhaseDiagramCandidates,
  fetchPhaseDiagrams,
  phaseDiagramExportUrl,
  phaseDiagramExportsUrl,
  phaseDiagramImageUrl,
  phaseDiagramTemplateUrl,
  stagePhaseDiagramSignalsInput,
  uploadPhaseDiagramTemplate,
  type GenesisJob,
  type PhaseDiagramFinding,
  type PhaseDiagramNodeCandidate,
  type PhaseDiagramReview,
  type PhaseDiagramSummary,
  type PhaseDiagramUploadResult,
} from "@/lib/api";

type Translate = (ko: string, en: string, ja: string) => string;

/** One row of the mapping form: a draft approach link, the sheet label bound to
 * it, and the phase it runs in. `linkId` is the key `approach_phases` needs —
 * `generate_node_signals` keys on the integer link id and a label would match
 * nothing. */
type ApproachRow = {
  linkId: number;
  linkName: string;
  compass: string | null;
  label: string;
  phaseId: string;
};

/** 확인됨 / 재확인 필요 / 미확인 — three states, three words, never two.
 * `stale` deliberately does NOT read as unconfirmed: somebody did confirm, and
 * the reason it no longer applies is shown beside it. */
function reviewStateLabel(row: { confirmed: boolean; stale_reasons: string[] }, t: Translate): string {
  if (row.confirmed) return t("확인됨", "CONFIRMED", "確認済");
  if (row.stale_reasons.length > 0) return t("재확인 필요", "RE-CONFIRM", "再確認要");
  return t("미확인", "NOT CONFIRMED", "未確認");
}

/**
 * Tinted-box rule + three-colour discipline, both stated in full above
 * `statusClasses` in app/genesis/region/page.tsx: the fill and the border carry
 * the state, the ink ramp carries the word, and there are exactly three accent
 * hues — rose = refuse/fail, emerald = pass, sky = selection/info.
 *
 * 재확인 필요 and 확인 요망 are neither pass nor proven fault, so both go MONO:
 * a heavier `border-slate-400` on a neutral fill, with a `ring-1` where the
 * state is a full-width banner. THIS IS THE STATE THE WHOLE SCREEN EXISTS FOR,
 * so it is worth saying why dropping its hue is safe. 재확인 필요 must not read
 * as 확인됨 — and it does not: it is visibly NOT the green chip, its own word is
 * printed on it, and the stale reasons render verbatim underneath. What went is
 * a colour that globals.css had already remapped to the neutral ramp anyway;
 * the claim, the word and the reasons are untouched.
 */
function reviewStateClasses(row: { confirmed: boolean; stale_reasons: string[] }): string {
  if (row.confirmed) return "border-emerald-300 bg-emerald-50 text-slate-800";
  if (row.stale_reasons.length > 0) return "border-slate-400 bg-slate-100 text-slate-800";
  return "border-slate-300 bg-slate-50 text-slate-700";
}

/** What a review was made from, in one word (unit #52). The list has three kinds
 * of row now and the reader has to be able to tell them apart before clicking:
 * an 엑셀 row has no drawing to look at, and a 직접 입력 row has nothing in it
 * at all yet. */
function mediumLabel(medium: PhaseDiagramSummary["medium"], t: Translate): string | null {
  if (medium === "excel_upload") return t("엑셀", "EXCEL", "エクセル");
  if (medium === "blank_entry") return t("직접 입력", "MANUAL", "直接入力");
  return null;
}

/** The named absence where the drawing would be. NEVER a stand-in image: an
 * attestation is bound to the sha of the picture the human looked at, and a
 * placeholder that looked like a diagram would make that binding a lie. */
function noImageSentence(medium: PhaseDiagramReview["render"]["medium"], t: Translate): string {
  if (medium === "excel_upload") {
    return t(
      "이미지 없음 — 엑셀 업로드로 만든 검토입니다. 이 교차로의 현시도 그림은 이 앱에 없습니다.",
      "No image — this review came from an uploaded spreadsheet. This app holds no drawing for it.",
      "画像なし — アップロードされたエクセルから作成された検討です。図面はこのアプリにありません。",
    );
  }
  return t(
    "이미지 없음 — 화면에서 직접 만든 빈 양식입니다. 아래 표에 직접 입력하십시오.",
    "No image — a blank form created on this screen. Fill in the table below by hand.",
    "画像なし — 画面で作成した空の様式です。下の表に直接入力してください。",
  );
}

function findingClasses(severity: PhaseDiagramFinding["severity"]): string {
  if (severity === "flag") return "border-slate-400 bg-slate-100 text-slate-800";
  if (severity === "ok") return "border-emerald-300 bg-emerald-50 text-slate-800";
  return "border-slate-200 bg-slate-50 text-slate-600";
}

function findingWord(severity: PhaseDiagramFinding["severity"], t: Translate): string {
  if (severity === "flag") return t("확인 요망", "FLAG", "要確認");
  if (severity === "ok") return t("일치", "AGREES", "一致");
  return t("관측", "OBSERVATION", "観測");
}

// `refusalHeadline` (ko/en/ja for the backend's stable refusal codes, this
// page's four `phase_diagram_*` ones included) moved to
// components/genesis/ui.tsx, where the four genesis screens' four partial copies
// became one table over one globally-unique code space. Its rule is unchanged
// and restated there: the `reasons` array is still rendered verbatim underneath
// the headline, because it carries the file name and the shas, and rewriting a
// backend refusal client-side is how a UI starts lying.

// `useSearchParams` client-renders the tree up to the nearest Suspense boundary
// (Next 16, app/api-reference/functions/use-search-params "Prerendering"), so
// the boundary lives here rather than being inherited. Everything below this
// page's "use client" already renders client-side, so the fallback is never on
// screen long enough to need chrome of its own -- the same shape
// app/network/page.tsx uses for the approval-focus handoff.
export default function PhaseDiagramReviewPage() {
  return (
    <Suspense fallback={null}>
      <PhaseDiagramReviewPageContent />
    </Suspense>
  );
}

function PhaseDiagramReviewPageContent() {
  const { t } = useLanguage();
  const [rows, setRows] = useState<PhaseDiagramSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [review, setReview] = useState<PhaseDiagramReview | null>(null);
  const [error, setError] = useState<GenesisError | null>(null);
  const [busy, setBusy] = useState(false);

  const [jobs, setJobs] = useState<GenesisJob[]>([]);
  const [jobId, setJobId] = useState<string>("");
  const [candidates, setCandidates] = useState<PhaseDiagramNodeCandidate[]>([]);
  const [nodeId, setNodeId] = useState<number | null>(null);
  const [approachRows, setApproachRows] = useState<ApproachRow[]>([]);
  const [planChoice, setPlanChoice] = useState<string>("");
  const [amber, setAmber] = useState<string>("");
  const [amberNote, setAmberNote] = useState<string>("");
  const [note, setNote] = useState<string>("");
  const [stagedToken, setStagedToken] = useState<string | null>(null);

  // -- unit #52: the three ways IN ---------------------------------------
  // `uploadResult` is kept on screen after the upload rather than folded into a
  // toast: it carries the refused rows, and a refusal a reader has to catch
  // before it disappears is one nobody acts on.
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadResult, setUploadResult] = useState<PhaseDiagramUploadResult | null>(null);
  const [blankName, setBlankName] = useState<string>("");
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  const [listLoaded, setListLoaded] = useState(false);

  const refreshList = useCallback(async () => {
    try {
      const listed = await fetchPhaseDiagrams();
      setRows(listed);
      setError(null);
      return listed;
    } catch (caught) {
      setError(caught instanceof GenesisError ? caught : new GenesisError(String(caught), 0));
      return [];
    } finally {
      setListLoaded(true);
    }
  }, []);

  // -- unit #52 ④: arriving from the network diagram ----------------------
  // /network deep-links here as ?node=<id>&name=<node name>. The NAME is what
  // is used: /network draws the ACTIVE VISSIM DATASET, whose node ids come from
  // the .inpx, while the confirm form below binds a node from a GENERATION'S
  // DRAFT (fetchPhaseDiagramCandidates over a job's own DraftNetwork). Those are
  // two different id spaces, so matching on the number would be a coincidence
  // dressed as a fact. The id rides along as a breadcrumb — it is recorded as
  // provenance on a created review and binds nothing.
  const searchParams = useSearchParams();
  const nodeParam = searchParams.get("node");
  const nodeNameParam = searchParams.get("name");
  const deepLinkHandled = useRef(false);

  useEffect(() => {
    if (!listLoaded || deepLinkHandled.current) return;
    if (!nodeParam && !nodeNameParam) return;
    deepLinkHandled.current = true;
    // Deferred for the react-hooks `set-state-in-effect` rule, the same pattern
    // the initial load below uses.
    void Promise.resolve().then(() => {
      const wanted = (nodeNameParam ?? "").trim();
      const existing = wanted
        ? rows.find((row) => (row.intersection_name ?? "").trim() === wanted)
        : undefined;
      if (existing) {
        setSelectedId(existing.review_id);
        return;
      }
      setBlankName(wanted || (nodeParam ? `노드 ${nodeParam}` : ""));
    });
  }, [listLoaded, rows, nodeParam, nodeNameParam]);

  useEffect(() => {
    // Deferred for the react-hooks `set-state-in-effect` rule, the same pattern
    // the dashboard page uses.
    void Promise.resolve().then(async () => {
      const listed = await refreshList();
      if (listed.length > 0) setSelectedId((current) => current ?? listed[0].review_id);
      try {
        setJobs(await fetchGenesisJobs());
      } catch {
        setJobs([]);
      }
    });
  }, [refreshList]);

  const loadReview = useCallback(async (reviewId: string) => {
    try {
      const loaded = await fetchPhaseDiagram(reviewId);
      setReview(loaded);
      setError(null);
      const attested = loaded.attestation;
      setNodeId(attested ? attested.node_id : null);
      setPlanChoice(attested ? `${attested.level_no}:${attested.plan_id}` : "");
      // The amber comes from the attestation when one exists (what a human
      // already declared), and otherwise from what the UPLOAD said, with its
      // 근거 beside it. A prefill, not a default: the sentence under the field
      // still says the number is the reviewer's declaration, and an empty
      // upload leaves an empty box.
      const uploadedAmber = loaded.sheet.declared_amber_s;
      setAmber(
        attested?.declared_amber_s != null
          ? String(attested.declared_amber_s)
          : uploadedAmber != null
            ? String(uploadedAmber)
            : "",
      );
      setAmberNote(attested?.amber_declaration_note ?? loaded.sheet.amber_declaration_note ?? "");
      setNote(attested?.note ?? "");
      setApproachRows([]);
      setCandidates([]);
    } catch (caught) {
      setReview(null);
      setError(caught instanceof GenesisError ? caught : new GenesisError(String(caught), 0));
    }
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    void Promise.resolve().then(() => loadReview(selectedId));
  }, [selectedId, loadReview]);

  /** The model's reading, indexed label → the first phase it appears in. Used
   * only to prefill the form and to compute `changes`; it is never sent as the
   * mapping on its own. */
  const proposedPhaseByLabel = useMemo(() => {
    const index = new Map<string, string>();
    for (const phase of review?.proposal?.phases ?? []) {
      for (const movement of phase.movements) {
        if (!index.has(movement.approach_label)) index.set(movement.approach_label, phase.phase_id);
      }
    }
    return index;
  }, [review]);

  const loadCandidates = useCallback(async () => {
    if (!selectedId || !jobId) return;
    setBusy(true);
    try {
      const payload = await fetchPhaseDiagramCandidates(selectedId, jobId);
      setCandidates(payload.candidates);
      setError(null);
    } catch (caught) {
      setError(caught instanceof GenesisError ? caught : new GenesisError(String(caught), 0));
    } finally {
      setBusy(false);
    }
  }, [selectedId, jobId]);

  /** Binding a node fills the form with that node's OWN approach links. The
   * phase column is prefilled from the proposal where a label matches, and left
   * empty otherwise — an empty row is a visible gap, not a default. */
  const bindNode = useCallback(
    (candidate: PhaseDiagramNodeCandidate) => {
      setNodeId(candidate.node_id);
      const labels = review?.proposal?.approaches ?? [];
      setApproachRows(
        candidate.approaches.map((approach, index) => {
          const label = labels[index]?.label ?? "";
          return {
            linkId: approach.link_id,
            linkName: approach.name,
            compass: approach.from_compass,
            label,
            phaseId: (label && proposedPhaseByLabel.get(label)) || "",
          };
        }),
      );
    },
    [review, proposedPhaseByLabel],
  );

  const setRow = useCallback((linkId: number, patch: Partial<ApproachRow>) => {
    setApproachRows((current) =>
      current.map((row) => (row.linkId === linkId ? { ...row, ...patch } : row)),
    );
  }, []);

  const submit = useCallback(async () => {
    if (!review || nodeId == null) return;
    const [levelRaw, planRaw] = planChoice.split(":");
    const approachPhases: Record<string, string> = {};
    const approachLinks: Record<string, string> = {};
    const changes: { field: string; was: string | null; now: string | null }[] = [];
    for (const row of approachRows) {
      if (!row.phaseId) continue;
      approachPhases[String(row.linkId)] = row.phaseId;
      if (row.label) approachLinks[String(row.linkId)] = row.label;
      const proposed = row.label ? proposedPhaseByLabel.get(row.label) ?? null : null;
      if (proposed !== row.phaseId) {
        changes.push({ field: `link ${row.linkId} (${row.label || "?"})`, was: proposed, now: row.phaseId });
      }
    }
    setBusy(true);
    try {
      const updated = await confirmPhaseDiagram(review.review_id, {
        node_id: nodeId,
        approach_phases: approachPhases,
        approach_links: approachLinks,
        level_no: Number(levelRaw),
        plan_id: Number(planRaw),
        image_sha256: review.render.image_sha256,
        source_sha256: review.render.source_sha256,
        proposal_sha256: review.proposal_sha256,
        proposal_outcome: review.proposal ? review.proposal.outcome : "none",
        declared_amber_s: amber ? Number(amber) : null,
        amber_declaration_note: amberNote || null,
        changes,
        note: note || null,
      });
      setReview(updated);
      setError(null);
      await refreshList();
    } catch (caught) {
      setError(caught instanceof GenesisError ? caught : new GenesisError(String(caught), 0));
    } finally {
      setBusy(false);
    }
  }, [review, nodeId, planChoice, approachRows, proposedPhaseByLabel, amber, amberNote, note, refreshList]);

  /** Upload one filled-in template. What comes back is a PROPOSAL per 교차로명
   * in the file — never a confirmation — plus every row the parser refused,
   * which stays on screen until the next upload. */
  const submitUpload = useCallback(async () => {
    if (!uploadFile) return;
    setBusy(true);
    try {
      const result = await uploadPhaseDiagramTemplate(uploadFile);
      setUploadResult(result);
      setUploadFile(null);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
      setError(null);
      await refreshList();
      if (result.created.length > 0) setSelectedId(result.created[0].review_id);
    } catch (caught) {
      setError(caught instanceof GenesisError ? caught : new GenesisError(String(caught), 0));
    } finally {
      setBusy(false);
    }
  }, [uploadFile, refreshList]);

  /** Create an empty review for a named intersection and open it. The screen's
   * existing blank-form branch takes it from there — one workflow, not two. */
  const createBlank = useCallback(
    async (name: string, originNote?: string) => {
      const trimmed = name.trim();
      if (!trimmed) return;
      setBusy(true);
      try {
        const created = await createBlankPhaseDiagram(trimmed, { originNote: originNote ?? null });
        setBlankName("");
        setError(null);
        await refreshList();
        setSelectedId(created.review_id);
      } catch (caught) {
        setError(caught instanceof GenesisError ? caught : new GenesisError(String(caught), 0));
      } finally {
        setBusy(false);
      }
    },
    [refreshList],
  );

  const stageSignals = useCallback(async () => {
    const confirmedIds = rows.filter((row) => row.confirmed).map((row) => row.review_id);
    if (confirmedIds.length === 0) return;
    setBusy(true);
    try {
      const staged = await stagePhaseDiagramSignalsInput(confirmedIds);
      setStagedToken(staged.token);
      setError(null);
    } catch (caught) {
      setStagedToken(null);
      setError(caught instanceof GenesisError ? caught : new GenesisError(String(caught), 0));
    } finally {
      setBusy(false);
    }
  }, [rows]);

  const confirmedCount = rows.filter((row) => row.confirmed).length;
  const flags = review?.cross_check?.findings.filter((f) => f.severity === "flag") ?? [];
  const others = review?.cross_check?.findings.filter((f) => f.severity !== "flag") ?? [];

  // 수준 / 계획 ID. A 대전 sheet offers its own (수준, ID) pairs and the select
  // below is filled from them; a review with no TOD table (unit #52's uploaded
  // and blank media, and any sheet whose table did not parse) offers none, and
  // the form ASKS for the two numbers instead of defaulting them. Both paths
  // write the same "level:plan" string, and both are checked here — the old
  // `planChoice !== ""` would have accepted the ":" a pair of empty boxes makes.
  const [planLevelRaw, planIdRaw] = planChoice.split(":");
  const planChosen =
    planLevelRaw !== undefined &&
    planLevelRaw.trim() !== "" &&
    planIdRaw !== undefined &&
    planIdRaw.trim() !== "" &&
    Number.isFinite(Number(planLevelRaw)) &&
    Number.isFinite(Number(planIdRaw));
  const canSubmit =
    nodeId != null &&
    planChosen &&
    approachRows.some((row) => row.phaseId) &&
    !busy;
  const hasImage = review?.render.medium === "pdf_band" && review.render.image_sha256 != null;
  const uploadedProposal = review?.proposal?.source === "excel_upload";

  // UNIT #20's help drawer is RETIRED (2026-08-14). 확인 절차 and signals 입력
  // are verbatim in lib/help-content.tsx (`phasesHelpEntries`) and render at
  // /docs, opened by the book icon in the app topbar. The always-visible intro
  // paragraph below the h1 STAYS: it is this page's foundational claim, not a
  // tooltip, and the drawer only ever echoed it for discoverability.

  return (
    <main className="mx-auto max-w-[1500px] px-4 py-8">
      {/* One page-title size across the three genesis sub-screens (22px), body
        * 13, secondary 12 muted. The back link left `text-sky-700` for the same
        * reason the tinted boxes left their family inks: sky-700 is outside the
        * dark remap and rendered as stock #0369a1 on a near-black page. */}
      <header className="mb-6">
        <h1 className="flex items-center text-[22px] font-semibold tracking-tight text-slate-900">
          {t("현시도 확인", "Phase-diagram review", "現示図の確認")}
        </h1>
        <p className="mt-1.5 max-w-4xl text-[15px] leading-relaxed text-slate-600">
          {t(
            "시트의 현시도는 그림이므로 어떤 파싱도 접근로–현시 배정을 산출할 수 없습니다. 아래 제안은 모델이 도면을 읽은 결과일 뿐이며, 이 앱이 검증한 것이 아닙니다. 확인 버튼을 누른 사람의 진술만이 실제로 사용됩니다.",
            "The 현시도 is a drawing, so no parse can produce the approach→phase mapping. The proposal below is a model READING the picture — this app has not verified it. Only the statement of the person who confirms is used.",
            "現示図は図面であり、解析では流入路と現示の対応を得られません。以下の提案はモデルの判読であり、本アプリが検証したものではありません。確認した人の申告のみが使用されます。",
          )}
        </p>
        <Link href="/genesis" className="mt-2 inline-block text-[14px] font-semibold text-slate-500 underline">
          {t("← 네트워크 생성으로", "← Back to generation", "← ネットワーク生成へ")}
        </Link>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-slate-800">
          <p className="text-[15px] font-semibold">{refusalHeadline(error.code, t)}</p>
          {error.reasons.length > 0 ? (
            <ul className="mt-1.5 list-disc pl-5 text-[15px] leading-relaxed">
              {error.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 text-[15px] leading-relaxed">{error.message}</p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div>
          <Panel
            density="console"
            title={t("교차로", "Intersections", "交差点")}
            subtitle={t(
              `${rows.length}건 중 ${confirmedCount}건 확인됨`,
              `${confirmedCount} of ${rows.length} confirmed`,
              `${rows.length}件中${confirmedCount}件確認済`,
            )}
          >
            {rows.length === 0 ? (
              <p className="text-[15px] leading-relaxed text-slate-500">
                {t(
                  "렌더링된 현시도가 없습니다.",
                  "No rendered 현시도 yet.",
                  "レンダリング済みの現示図がありません。",
                )}
              </p>
            ) : (
              <ul className="space-y-1.5">
                {rows.map((row) => (
                  <li key={row.review_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(row.review_id)}
                      className={`w-full rounded-lg border px-3 py-2.5 text-left text-[15px] ${
                        row.review_id === selectedId
                          ? "border-sky-400 bg-sky-50"
                          : "border-slate-200 bg-transparent hover:bg-slate-50"
                      }`}
                    >
                      <span className="block truncate font-semibold text-slate-900">
                        {row.intersection_name || row.source_file}
                      </span>
                      {mediumLabel(row.medium, t) && (
                        <span className="mt-1.5 mr-1 inline-block rounded border border-slate-300 bg-slate-50 px-2 py-0.5 text-[14px] text-slate-600">
                          {mediumLabel(row.medium, t)}
                        </span>
                      )}
                      {/* The three state chips were 10px. This is the list that
                        * says which intersections still need a human, so it is
                        * the last place on the screen to shrink type. */}
                      <span
                        className={`mt-1.5 inline-block rounded border px-2 py-0.5 text-[14px] font-semibold ${reviewStateClasses(row)}`}
                      >
                        {reviewStateLabel(row, t)}
                      </span>
                      {row.proposal_outcome === "refused" && (
                        <span className="ml-1 inline-block rounded border border-slate-300 bg-slate-50 px-2 py-0.5 text-[14px] text-slate-600">
                          {t("제안 거부", "proposal refused", "提案拒否")}
                        </span>
                      )}
                      {row.flags.length > 0 && (
                        <span className="ml-1 inline-block rounded border border-slate-400 bg-slate-100 px-2 py-0.5 text-[14px] font-semibold text-slate-800">
                          {t(`대조 ${row.flags.length}건`, `${row.flags.length} flags`, `照合${row.flags.length}件`)}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {/* Unit #52 — the three ways to get a 현시도 INTO this screen, in the
            * order a reader meets them: take the form away, bring it back, or
            * skip the paper entirely. Each one lands on the same review list
            * above and the same confirm form on the right; none of them
            * confirms anything. The doctrine sentence lives at /docs (unit
            * #20's rule) and inside the downloaded file itself — this panel
            * carries actions, not an education. */}
          <Panel density="console" title={t("현시도 입력", "Add a 현시도", "現示図の入力")}>
            <a
              href={phaseDiagramTemplateUrl()}
              className="ui-button-ghost block rounded-lg px-3 py-2 text-center text-[15px] font-semibold"
            >
              {t("템플릿 다운로드", "Download the template", "テンプレートをダウンロード")}
            </a>

            <div className="mt-3">
              <input
                ref={uploadInputRef}
                type="file"
                accept=".xlsx"
                onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                className="block w-full text-[14px] text-slate-600"
                aria-label={t("현시도 엑셀 선택", "Choose a 현시도 workbook", "現示図エクセルを選択")}
              />
              <button
                type="button"
                onClick={submitUpload}
                disabled={!uploadFile || busy}
                className="mt-2 w-full rounded-lg bg-slate-900 px-3 py-2 text-[15px] font-semibold text-ink disabled:opacity-40"
              >
                {t("엑셀 올리기 (제안으로)", "Upload as a proposal", "エクセルを提案として送る")}
              </button>
            </div>

            {uploadResult && (
              <div className="mt-3 space-y-1.5 text-[14px] leading-relaxed">
                <p className="text-slate-700">
                  {uploadResult.created.length === 0
                    ? t(
                        "등록된 교차로가 없습니다. 아래 사유를 확인하십시오.",
                        "No intersection was registered. See the reasons below.",
                        "登録された交差点はありません。以下の理由をご確認ください。",
                      )
                    : t(
                        `${uploadResult.created.length}개 교차로가 제안으로 등록되었습니다. 확인 전에는 사용되지 않습니다.`,
                        `${uploadResult.created.length} intersection(s) registered as proposals. Nothing is used before 확인.`,
                        `${uploadResult.created.length}件の交差点が提案として登録されました。確認前は使用されません。`,
                      )}
                </p>
                {/* Whether the upload can actually reach a generation, per
                  * intersection. An upload with no 신호시간 rows is confirmable
                  * and useful, but it cannot become a signals input — saying so
                  * here is the difference between a reader finding out now and
                  * finding out at the staging button. */}
                {uploadResult.created
                  .filter((row) => row.plans.length === 0)
                  .map((row) => (
                    <p key={`no-timing-${row.review_id}`} className="text-slate-600">
                      {t(
                        `${row.intersection_name}: 신호시간 없음 — 확인은 되지만 신호 입력은 만들 수 없습니다.`,
                        `${row.intersection_name}: no signal timing — confirmable, but no signals input can be built.`,
                        `${row.intersection_name}: 信号時間なし — 確認はできますが signals 入力は作成できません。`,
                      )}
                    </p>
                  ))}
                {uploadResult.skipped_example_rows.length > 0 && (
                  <p className="text-slate-500">
                    {t(
                      `예시 줄 ${uploadResult.skipped_example_rows.length}개는 읽지 않았습니다.`,
                      `${uploadResult.skipped_example_rows.length} example row(s) were not read.`,
                      `例示行${uploadResult.skipped_example_rows.length}件は読みませんでした。`,
                    )}
                  </p>
                )}
                {/* Refused rows are RENDERED, never counted away: a row nobody
                  * sees is a row nobody fixes, and the intersection it belongs
                  * to did not come in at all. */}
                {uploadResult.refusals.map((refusal, index) => (
                  <div
                    key={`${refusal.code}-${index}`}
                    className="rounded-md border border-slate-400 bg-slate-100 px-2.5 py-1.5 text-slate-800"
                  >
                    <span className="mr-1.5 font-semibold">
                      {refusal.row != null
                        ? t(`${refusal.row}행 거부`, `row ${refusal.row} refused`, `${refusal.row}行 拒否`)
                        : t("거부", "refused", "拒否")}
                    </span>
                    {refusal.reasons.join(" ")}
                  </div>
                ))}
              </div>
            )}

            <div className="mt-4 border-t border-slate-200 pt-3">
              {(nodeParam || nodeNameParam) && (
                <p className="mb-2 text-[14px] leading-relaxed text-slate-600">
                  {t(
                    `네트워크 도식에서 노드 ${nodeParam ?? "—"}을(를) 선택해 왔습니다. 이 이름은 참고용이며, 어느 노드에 배정되는지는 아래 확인 양식에서 정해집니다.`,
                    `Arrived from node ${nodeParam ?? "—"} on the network diagram. The name is a starting point; which node the mapping binds to is decided on the confirm form.`,
                    `ネットワーク図のノード${nodeParam ?? "—"}から来ました。名称は参考であり、どのノードに対応するかは確認様式で決まります。`,
                  )}
                </p>
              )}
              <label className="block text-[15px] text-slate-700">
                <span className="block font-semibold">{t("교차로명", "Intersection name", "交差点名")}</span>
                <input
                  value={blankName}
                  onChange={(event) => setBlankName(event.target.value)}
                  className="mt-1.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                  placeholder={t("예: 둔산네거리", "e.g. 둔산네거리", "例: 둔산네거리")}
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  void createBlank(blankName, nodeParam ? `네트워크 도식 노드 ${nodeParam}` : undefined)
                }
                disabled={blankName.trim() === "" || busy}
                className="mt-2 w-full rounded-lg bg-slate-900 px-3 py-2 text-[15px] font-semibold text-ink disabled:opacity-40"
              >
                {t("새 현시도 입력", "New blank 현시도", "新しい現示図を入力")}
              </button>
            </div>

            {rows.length > 0 && (
              <a
                href={phaseDiagramExportsUrl()}
                className="mt-4 block text-[14px] font-semibold text-slate-500 underline"
              >
                {t("전체 내보내기 (미확인 초안)", "Export all (unconfirmed draft)", "全体を書き出す(未確認の草案)")}
              </a>
            )}
          </Panel>

          {/* No subtitle (user text-purge 2026-08-14): the button itself counts
            * the confirmed intersections it stages from. */}
          <Panel
            density="console"
            title={t("signals 입력 만들기", "Stage the signals input", "signals入力の作成")}
          >
            <button
              type="button"
              onClick={stageSignals}
              disabled={confirmedCount === 0 || busy}
              className="w-full rounded-lg bg-slate-900 px-3 py-2 text-[15px] font-semibold text-ink disabled:opacity-40"
            >
              {t(
                `확인된 ${confirmedCount}건으로 만들기`,
                `Stage from ${confirmedCount} confirmed`,
                `確認済${confirmedCount}件で作成`,
              )}
            </button>
            {stagedToken && (
              <p className="mt-2.5 break-all rounded-md border border-emerald-300 bg-emerald-50 px-2.5 py-1.5 text-[14px] leading-relaxed text-slate-800">
                {t("side-input 토큰", "side-input token", "side-inputトークン")}: {stagedToken}
              </p>
            )}
          </Panel>
        </div>

        {/* UX pass U-1: the right column used to render SIX panels open at once
          * (image, stale banner, proposal, cross-check, confirm form,
          * attestation record) in one scroll, with nothing selected producing a
          * panel whose only content was "pick one on the left". It is now
          * state-gated and severity-gated, in that order:
          *   * nothing selected -> one empty-state line, no panel chrome;
          *   * selected -> the image and the model's reading are OPEN (they are
          *     what a reviewer reads before deciding), and the confirm form is
          *     OPEN because confirming IS the purpose of this page;
          *   * the machine cross-check opens only when it FLAGGED something --
          *     the same "collapse by default, auto-open on the state that
          *     matters" rule the genesis findings map already sets, keyed on
          *     the review so switching intersections recomputes it;
          *   * the attestation record starts collapsed: it is a record of a
          *     decision already made, not an input to the next one.
          * The stale banner stays ALWAYS visible when present -- a confirmation
          * that no longer applies is exactly the thing that must not be one
          * click away. */}
        <div>
          {!review ? (
            <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-[15px] text-slate-500">
              {t("왼쪽에서 교차로를 선택하십시오.", "Pick an intersection on the left.", "左で交差点を選択してください。")}
            </p>
          ) : (
            <>
              <Panel
                density="console"
                title={`${review.sheet.intersection_name ?? review.source_file} ${
                  review.sheet.intersection_no ? `(교차로번호 ${review.sheet.intersection_no})` : ""
                }`}
                subtitle={
                  hasImage
                    ? t(
                        `이미지 ${review.render.image_sha256?.slice(0, 12)}… · ${review.render.renderer} · ${review.render.rendered_at}`,
                        `image ${review.render.image_sha256?.slice(0, 12)}… · ${review.render.renderer} · ${review.render.rendered_at}`,
                        `画像 ${review.render.image_sha256?.slice(0, 12)}… · ${review.render.renderer} · ${review.render.rendered_at}`,
                      )
                    : t(
                        `${review.source_file} · ${review.render.rendered_at}`,
                        `${review.source_file} · ${review.render.rendered_at}`,
                        `${review.source_file} · ${review.render.rendered_at}`,
                      )
                }
              >
                {hasImage ? (
                  <div className="overflow-x-auto rounded border border-slate-200 bg-white p-2">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={phaseDiagramImageUrl(review.review_id)}
                      alt={t("현시도 렌더 이미지", "Rendered 현시도 band", "現示図のレンダー画像")}
                      className="max-w-none"
                      style={{ width: review.render.image_width_px ?? undefined }}
                    />
                  </div>
                ) : (
                  /* The named absence. Unit #52 puts two media on this screen
                    * that have no drawing at all, and a placeholder that LOOKED
                    * like a diagram would make the attestation's image binding
                    * — "the picture this person actually looked at" — a lie. */
                  <p className="rounded border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-[15px] leading-relaxed text-slate-600">
                    {noImageSentence(review.render.medium, t)}
                  </p>
                )}
                {review.proposal && (
                  <a
                    href={phaseDiagramExportUrl(review.review_id)}
                    className="mt-3 inline-block text-[14px] font-semibold text-slate-500 underline"
                  >
                    {t("엑셀로 내보내기 (미확인)", "Export to Excel (unconfirmed)", "エクセルに書き出す(未確認)")}
                  </a>
                )}
              </Panel>

              {review.stale_reasons.length > 0 && (
                /* MONO with the heavy ring: this banner is the one thing on the
                  * screen that must not be one click away, and it is neither a
                  * pass nor a proven fault. Weight and shape carry it. */
                <div className="mb-5 rounded-lg border border-slate-400 bg-slate-100 px-4 py-3.5 text-slate-800 ring-1 ring-slate-400">
                  <p className="text-[15px] font-semibold">
                    {t("재확인 필요", "Re-confirmation needed", "再確認が必要です")}
                  </p>
                  <ul className="mt-1.5 list-disc pl-5 text-[15px] leading-relaxed">
                    {review.stale_reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}

              <Panel
                density="console"
                /* Two sources, two honest titles, ONE status. A spreadsheet was
                  * not read by a model, so calling it 모델의 판독 would name the
                  * wrong author — but it is a 제안 for exactly the same reason
                  * the model's reading is, and both wait for the same 확인. */
                title={
                  uploadedProposal
                    ? t("업로드된 시트 (제안)", "The uploaded sheet (a proposal)", "アップロードされた表(提案)")
                    : t("모델의 판독 (제안)", "The model's reading (a proposal)", "モデルの判読(提案)")
                }
                subtitle={
                  review.proposal
                    ? uploadedProposal
                      ? t(
                          `올린 사람: ${review.proposal.uploaded_by ?? "—"} · ${review.proposal.proposed_at} · 이 앱이 검증한 것이 아닙니다`,
                          `uploaded by ${review.proposal.uploaded_by ?? "—"} · ${review.proposal.proposed_at} · not verified by this app`,
                          `アップロード: ${review.proposal.uploaded_by ?? "—"} · ${review.proposal.proposed_at} · 本アプリの検証ではありません`,
                        )
                      : t(
                          `${review.proposal.model} · ${review.proposal.proposed_at} · 이 앱이 검증한 것이 아닙니다`,
                          `${review.proposal.model} · ${review.proposal.proposed_at} · not verified by this app`,
                          `${review.proposal.model} · ${review.proposal.proposed_at} · 本アプリの検証ではありません`,
                        )
                    : undefined
                }
              >
                {!review.proposal || review.proposal.outcome === "refused" ? (
                  <div className="rounded-md border border-slate-300 bg-slate-50 px-3.5 py-2.5 text-[15px] leading-relaxed text-slate-700">
                    <p className="font-semibold">
                      {t(
                        "판독 결과가 없습니다 — 아래 양식에 직접 입력하십시오.",
                        "No reading — fill in the form below by hand.",
                        "判読結果がありません — 下の様式に直接入力してください。",
                      )}
                    </p>
                    {review.proposal?.refusal && (
                      <p className="mt-1.5">
                        <span className="font-mono text-[14px]">{review.proposal.refusal.code}</span> —{" "}
                        {review.proposal.refusal.reason}
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="space-y-3">
                    {uploadedProposal && (
                      /* PROVENANCE, not attestation. It says who sent the file
                        * in — which is a different claim from "this mapping is
                        * right", and the only place that claim can be made is
                        * the 확인 button below. */
                      <p className="rounded-md border border-slate-300 bg-slate-50 px-3.5 py-2 text-[14px] leading-relaxed text-slate-700">
                        {t(
                          `${review.proposal.uploaded_by ?? "—"}님이 올린 엑셀의 내용입니다. 올린 것은 확인이 아닙니다 — 아래에서 확인해야 사용됩니다.`,
                          `This is the content of the spreadsheet ${review.proposal.uploaded_by ?? "—"} uploaded. Uploading is not confirming — it is used only after 확인 below.`,
                          `${review.proposal.uploaded_by ?? "—"}さんがアップロードしたエクセルの内容です。アップロードは確認ではありません。`,
                        )}
                      </p>
                    )}
                    <p className="text-[14px] leading-relaxed text-slate-500">
                      {t("도면에 인쇄된 접근로", "Approach legs printed on the drawing", "図面に印刷された流入路")}:{" "}
                      {review.proposal.approaches.map((a) => `${a.label} (${a.side})`).join(", ") || "—"}
                    </p>
                    {review.proposal.phases.map((phase) => (
                      <div key={phase.phase_id} className="rounded-md border border-slate-200 px-3.5 py-2.5">
                        <p className="text-[15px] font-semibold text-slate-900">{phase.phase_id}</p>
                        <ul className="mt-1 text-[15px] leading-relaxed text-slate-700">
                          {phase.movements.map((movement, index) => (
                            <li key={`${phase.phase_id}-${index}`}>
                              {movement.approach_label} — {movement.turn}
                              {movement.note ? ` (${movement.note})` : ""}
                            </li>
                          ))}
                        </ul>
                        {phase.ambiguity && (
                          <p className="mt-1.5 rounded border border-slate-400 bg-slate-100 px-2.5 py-1.5 text-[14px] leading-relaxed text-slate-800">
                            {t("모델이 확정하지 못한 부분", "What the model could not resolve", "モデルが確定できなかった点")}:{" "}
                            {phase.ambiguity}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Panel>

              <Panel
                density="console"
                // Keyed on the review so the severity default is recomputed
                // when a different intersection is selected, not inherited from
                // whichever one was open before.
                key={`cross-check-${review.review_id}`}
                collapsible
                defaultOpen={flags.length > 0}
                title={t("시트 텍스트와의 기계 대조", "Machine cross-check against the sheet text", "シート本文との機械照合")}
                subtitle={t(
                  "시트가 스스로 밝히는 사실(현시 수, 인쇄된 접근로 이름, 교차로번호)만 비교합니다. 배정의 정오는 여기서 판단하지 않습니다.",
                  "Compares only what the sheet itself states (phase count, printed approach names, 교차로번호). Whether the assignment is CORRECT is not judged here.",
                  "シート自体が明示する事実のみを比較します。배정の正誤はここでは判断しません。",
                )}
              >
                {flags.length === 0 && others.length === 0 ? (
                  <p className="text-[15px] text-slate-500">
                    {t("대조 결과가 없습니다.", "No cross-check on file.", "照合結果がありません。")}
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {[...flags, ...others].map((finding, index) => (
                      <li
                        key={`${finding.code}-${index}`}
                        className={`rounded-md border px-3 py-2 text-[14px] leading-relaxed ${findingClasses(finding.severity)}`}
                      >
                        <span className="mr-1.5 font-semibold">{findingWord(finding.severity, t)}</span>
                        {finding.message}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel
                density="console"
                title={t("확인 — 접근로와 현시 배정", "Confirm — approach to phase", "確認 — 流入路と現示の対応")}
                subtitle={t(
                  "이 화면에서 확인한 내용만 신호 입력이 됩니다. 제안은 확인 전에는 사용되지 않습니다.",
                  "Only what you confirm here becomes signal input. A proposal is never used unconfirmed.",
                  "この画面で確認した内容のみが信号入力になります。提案は確認前には使用されません。",
                )}
              >
                <div className="mb-4 flex flex-wrap items-end gap-3">
                  <label className="text-[15px] text-slate-700">
                    <span className="block font-semibold">{t("생성 작업", "Generation", "生成ジョブ")}</span>
                    <select
                      value={jobId}
                      onChange={(event) => setJobId(event.target.value)}
                      className="mt-1.5 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                    >
                      <option value="">{t("선택", "select", "選択")}</option>
                      {jobs.map((job) => (
                        <option key={job.id} value={job.id}>
                          {job.name} · {job.status}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    onClick={loadCandidates}
                    disabled={!jobId || busy}
                    className="ui-button-ghost rounded-full px-3.5 py-1.5 text-[14px] font-semibold disabled:opacity-40"
                  >
                    {t("노드 후보 불러오기", "Load node candidates", "ノード候補を読み込む")}
                  </button>
                  {nodeId != null && (
                    <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-[14px] font-semibold text-slate-800">
                      {t("바인딩된 노드", "bound node", "対応ノード")}: {nodeId}
                    </span>
                  )}
                </div>

                {candidates.length > 0 && (
                  <ul className="mb-4 space-y-1.5">
                    {candidates.map((candidate) => (
                      <li key={candidate.node_id}>
                        <button
                          type="button"
                          onClick={() => bindNode(candidate)}
                          className={`w-full rounded-md border px-3 py-2 text-left text-[14px] ${
                            candidate.node_id === nodeId
                              ? "border-sky-400 bg-sky-50"
                              : "border-slate-200 bg-transparent hover:bg-slate-50"
                          }`}
                        >
                          <span className="font-semibold text-slate-900">
                            {t("노드", "node", "ノード")} {candidate.node_id} — {candidate.name}
                          </span>
                          <span className="ml-2 text-slate-600">{candidate.match_reason}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                {approachRows.length === 0 ? (
                  <p className="text-[15px] text-slate-500">
                    {t(
                      "노드를 선택하면 그 노드의 진입 링크가 표로 나타납니다.",
                      "Pick a node and its approach links appear as rows.",
                      "ノードを選ぶとその流入リンクが表になります。",
                    )}
                  </p>
                ) : (
                  <table className="w-full text-[14px]">
                    <thead>
                      <tr className="text-left text-slate-500">
                        <th className="py-1">{t("링크", "link", "リンク")}</th>
                        <th className="py-1">{t("방향", "from", "方向")}</th>
                        <th className="py-1">{t("시트 접근로", "sheet approach", "シート流入路")}</th>
                        <th className="py-1">{t("현시", "phase", "現示")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {approachRows.map((row) => (
                        <tr key={row.linkId} className="border-t border-slate-100">
                          <td className="py-1 font-mono">{row.linkId}</td>
                          <td className="py-1 text-slate-600">{row.compass ?? "—"}</td>
                          <td className="py-1">
                            <input
                              value={row.label}
                              onChange={(event) => setRow(row.linkId, { label: event.target.value })}
                              list={`labels-${review.review_id}`}
                              className="w-full rounded border border-slate-300 px-1.5 py-1"
                              placeholder={t("시트에 인쇄된 이름", "as printed", "印刷名")}
                            />
                          </td>
                          <td className="py-1">
                            {review.sheet.phase_ids.length > 0 ? (
                              <select
                                value={row.phaseId}
                                onChange={(event) => setRow(row.linkId, { phaseId: event.target.value })}
                                className="rounded border border-slate-300 px-1.5 py-1"
                              >
                                <option value="">{t("미배정", "unassigned", "未割当")}</option>
                                {review.sheet.phase_ids.map((phaseId) => (
                                  <option key={phaseId} value={phaseId}>
                                    {phaseId}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              /* A blank review knows no phase list yet — nothing
                                * has been parsed or uploaded for it — so the
                                * number is typed. An empty cell stays a visible
                                * gap: `submit` skips rows with no phase. */
                              <input
                                value={row.phaseId}
                                onChange={(event) => setRow(row.linkId, { phaseId: event.target.value })}
                                className="w-20 rounded border border-slate-300 px-1.5 py-1"
                                placeholder={t("예: Φ1", "e.g. Φ1", "例: Φ1")}
                              />
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <datalist id={`labels-${review.review_id}`}>
                  {(review.proposal?.approaches ?? []).map((approach) => (
                    <option key={approach.label} value={approach.label} />
                  ))}
                </datalist>

                <div className="mt-5 flex flex-wrap items-end gap-3">
                  {review.sheet.plan_choices.length > 0 ? (
                    <label className="text-[15px] text-slate-700">
                      <span className="block font-semibold">{t("수준 / 계획 ID", "level / plan", "水準 / 計画ID")}</span>
                      <select
                        value={planChoice}
                        onChange={(event) => setPlanChoice(event.target.value)}
                        className="mt-1.5 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                      >
                        <option value="">{t("선택", "select", "選択")}</option>
                        {review.sheet.plan_choices.map((choice) => (
                          <option key={`${choice.level_no}:${choice.plan_id}`} value={`${choice.level_no}:${choice.plan_id}`}>
                            {t("수준", "level", "水準")} {choice.level_no} / ID {choice.plan_id}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    /* No TOD table to choose from (an uploaded or blank review,
                      * or a sheet whose table did not parse), so the two numbers
                      * are ASKED for rather than defaulted. An empty pair does
                      * not enable 확인 — see `planChosen`. */
                    <div className="flex items-end gap-2 text-[15px] text-slate-700">
                      <label>
                        <span className="block font-semibold">{t("수준", "level", "水準")}</span>
                        <input
                          value={planLevelRaw ?? ""}
                          onChange={(event) => setPlanChoice(`${event.target.value}:${planIdRaw ?? ""}`)}
                          inputMode="numeric"
                          className="mt-1.5 w-20 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                        />
                      </label>
                      <label>
                        <span className="block font-semibold">{t("계획 ID", "plan id", "計画ID")}</span>
                        <input
                          value={planIdRaw ?? ""}
                          onChange={(event) => setPlanChoice(`${planLevelRaw ?? ""}:${event.target.value}`)}
                          inputMode="numeric"
                          className="mt-1.5 w-20 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                        />
                      </label>
                    </div>
                  )}
                  <label className="text-[15px] text-slate-700">
                    <span className="block font-semibold">{t("선언 황색시간(초)", "declared amber (s)", "申告黄色時間(秒)")}</span>
                    <input
                      value={amber}
                      onChange={(event) => setAmber(event.target.value)}
                      inputMode="decimal"
                      className="mt-1.5 w-28 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                      placeholder="3.0"
                    />
                  </label>
                  <label className="flex-1 text-[15px] text-slate-700">
                    <span className="block font-semibold">{t("황색시간 근거", "amber declaration note", "黄色時間の根拠")}</span>
                    <input
                      value={amberNote}
                      onChange={(event) => setAmberNote(event.target.value)}
                      className="mt-1.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                    />
                  </label>
                </div>
                <p className="mt-2 text-[14px] leading-relaxed text-slate-500">
                  {t(
                    "시트는 황색+전적색을 하나의 값으로만 인쇄합니다. 황색시간은 확인자가 선언하는 값이며, 이 앱은 기본값을 넣지 않습니다.",
                    "The sheet prints 황색+전적색 as one number. The amber is the reviewer's declaration — this app substitutes no default.",
                    "シートは黄色+全赤を一つの値でのみ印刷します。黄色時間は確認者の申告であり、既定値は入れません。",
                  )}
                </p>

                <label className="mt-5 block text-[15px] text-slate-700">
                  <span className="block font-semibold">{t("확인 메모", "confirmation note", "確認メモ")}</span>
                  <textarea
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    rows={2}
                    className="mt-1.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
                  />
                </label>

                <button
                  type="button"
                  onClick={submit}
                  disabled={!canSubmit}
                  className="mt-5 rounded-lg bg-sky-700 px-4 py-2 text-[15px] font-semibold text-white disabled:opacity-40"
                >
                  {t("이 배정을 확인합니다", "I confirm this mapping", "この対応を確認します")}
                </button>
                <p className="mt-2 text-[14px] leading-relaxed text-slate-500">
                  {t(
                    "확인하면 로그인한 사용자의 이름과 시각, 그리고 이 이미지의 해시가 함께 기록됩니다.",
                    "Confirming records the signed-in user's name, the time, and this image's hash.",
                    "確認すると、ログイン中の利用者名・時刻・この画像のハッシュが記録されます。",
                  )}
                </p>
              </Panel>

              {review.attestation && (
                <Panel
                  density="console"
                  // A record of a decision already made, not an input to the
                  // next one -- collapsed by default, and re-keyed per review so
                  // it does not carry one intersection's open state onto
                  // another's record.
                  key={`attestation-${review.review_id}`}
                  collapsible
                  defaultOpen={false}
                  title={t("확인 기록", "The attestation", "確認記録")}
                >
                  <p className="text-[15px] text-slate-800">
                    {t("확인자", "attested by", "確認者")}: <b>{review.attestation.attested_by}</b> ·{" "}
                    {review.attestation.attested_at}
                  </p>
                  <p className="mt-1.5 text-[14px] leading-relaxed text-slate-600">
                    {/* A confirmation made against no image says so. Printing a
                      * dash where the sha goes would read as "unknown"; this
                      * review HAS no drawing and the record states it. */}
                    {t("이미지 해시", "image sha", "画像ハッシュ")}:{" "}
                    {review.attestation.image_sha256
                      ? `${review.attestation.image_sha256.slice(0, 16)}…`
                      : t("이미지 없음", "no image", "画像なし")}{" "}
                    · {t("제안", "proposal", "提案")}: {review.attestation.proposal_outcome}
                  </p>
                  {review.attestation.changes.length > 0 && (
                    <ul className="mt-2.5 list-disc pl-5 text-[14px] leading-relaxed text-slate-700">
                      {review.attestation.changes.map((change, index) => (
                        <li key={`${change.field}-${index}`}>
                          {change.field}: {change.was ?? "—"} → {change.now ?? "—"}
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              )}
            </>
          )}
        </div>
      </div>
    </main>
  );
}

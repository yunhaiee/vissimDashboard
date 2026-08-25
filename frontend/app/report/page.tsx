"use client";

// ---------------------------------------------------------------------------
// /report — the report ENGINE's screen (Track R unit R-5b).
//
// The layout is the one decided in docs/product/report-engine-design-2026-08-24.md,
// section "UI (/report 재편)": a spine tree on the left (per-section
// include/exclude toggle + per-section data-source state, "이 절의 증거 없음"
// grey with its reason), a per-section preview on the right, a top bar
// carrying the run selection and the style-profile selection (default: the
// Prism default style), and one 생성 that produces the .docx plus the
// SVG-originals zip.
//
// WHAT THIS SCREEN MAY SAY, AND WHAT IT MAY NOT
// ---------------------------------------------
// Every absence and every limitation on this page is the BACKEND's sentence,
// picked through lib/serverText.ts and never composed here: a figure that
// could not be built carries its own `reason` ({ko,en,ja}) from GET /figures
// and GET /graphics, a Table 5-15 output this build cannot extract carries
// R-1's measured `reason`, and the typesetter's limitations come out of the
// compose manifest verbatim. What this screen owns is STRUCTURE and LABELS --
// the word "포함", the column headings, the fact that a section has no figure
// bound to it at all.
//
// THREE STATES, NOT TWO. A section can be included with evidence, included
// with a named absence, or EXCLUDED BY THE USER. The third is not an evidence
// gap and must never read as one: `excluded_by_selection` is the typesetter's
// own name for it (services/report/typeset.py), and the two are worded
// differently here on purpose.
//
// THE SVG IS THE SERVER'S. R-3 renders each graphic figure server-side and
// ships the SVG as a string; this page injects that string and nothing else.
// The one `dangerouslySetInnerHTML` below is handed `svg`, which is bound in
// the line above it as `const svg = figure.svg` and is narrowed by a
// `typeof svg === "string"` guard -- no concatenation, no template, no second
// source -- because the alternative (re-drawing the network here) is a second
// renderer that could disagree with the one the document's zip actually
// carries.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import LegacyReportBuilder from "@/components/report/LegacyReportBuilder";
import {
  composeReport,
  composeReportArtifacts,
  fetchReportFigures,
  fetchReportGraphics,
  fetchReportSpine,
  fetchReportStyleProfiles,
  fetchScenarios,
  ReportRefusedError,
  type ComposeReportRequest,
  type ReportFigureParams,
  type ReportFigureSourceCaption,
  type ReportGraphicFigure,
  type ReportRefusal,
  type ReportSpinePayload,
  type ReportSpineSection,
  type ReportStyleProfileSummary,
  type ReportTableFigure,
  type ScenarioMeta,
} from "@/lib/api";
import { failed, ok, resolve, type Loaded } from "@/lib/asyncResource";
import {
  readReportManifest,
  SECTION_STATE_EXCLUDED,
  type ManifestRead,
  type ManifestUnreadableReason,
} from "@/lib/reportManifest";
import { pick } from "@/lib/serverText";

/** `services.report.typeset.UNBOUND_SECTION_ID` — the section the typesetter
 * files figures under when they bind to no regulation-derived section. It is a
 * real, selectable id, so it gets a real row in the tree. */
const UNBOUND_SECTION_ID = "unbound_figures";

/** One figure of either kind, tagged so the preview branches on the KIND
 * rather than on the SHAPE of the payload — the two are not interchangeable
 * (rows vs. an SVG string), which is why the backend serves them on separate
 * routes under separate keys. */
type PreviewFigure =
  | { kind: "table"; figure: ReportTableFigure }
  | { kind: "graphic"; figure: ReportGraphicFigure };

type Translate = (ko: string, en: string, ja?: string) => string;

/**
 * The compose request, built from EXACTLY what the screen shows.
 *
 * Module-level and pure so that "the selection sent is the selection
 * displayed" is a property of one readable function rather than of a handler's
 * closure. Three rules live here:
 *
 *  * `selection.sections` is the checked list, verbatim — no id is added
 *    because it "should" be there and none is dropped for being empty of
 *    evidence. An evidence gap is typeset as a named absence, never filtered
 *    out on the way to the server.
 *  * `style_profile_id` is set ONLY when the user picked a stored profile.
 *    Omitting the key is how the backend is told "the Prism default"; sending
 *    an empty string would be an unknown-id refusal.
 *  * the figure params are the same object the preview was fetched with, so
 *    the document cannot be built from different inputs than the ones the
 *    preview above it was drawn from.
 */
function composeRequestBody(args: {
  includedSectionIds: string[];
  styleProfileId: string;
  language: string;
  figureParams: ReportFigureParams;
}): ComposeReportRequest {
  const body: ComposeReportRequest = {
    selection: { sections: args.includedSectionIds },
    language: args.language,
    ...args.figureParams,
  };
  if (args.styleProfileId) body.style_profile_id = args.styleProfileId;
  return body;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** The Table 5-15 outputs a section owes that R-1's audit marks NOT_AVAILABLE,
 * each with the backend's own measured reason. */
function notAvailableOutputs(section: ReportSpineSection): { name: string; reason: string }[] {
  const out: { name: string; reason: string }[] = [];
  for (const group of section.required_outputs) {
    for (const output of group.outputs) {
      if (output.availability === "NOT_AVAILABLE") {
        out.push({ name: `${group.type} · ${output.name}`, reason: output.reason ?? "" });
      }
    }
  }
  return out;
}

type ComposeOutcome = {
  /** Present only when the .docx half ran — the sibling zip action alone
   * produces a manifest without one. */
  docx: { filename: string; sections: number | null; limitations: number | null } | null;
  bundle: { filename: string; artifacts: number | null } | null;
  manifest: ManifestRead | null;
};

export default function ReportPage() {
  const { lang, t } = useLanguage();

  const [scenariosLoaded, setScenariosLoaded] = useState<Loaded<ScenarioMeta[]> | null>(null);
  const [spineLoaded, setSpineLoaded] = useState<Loaded<ReportSpinePayload> | null>(null);
  const [profilesLoaded, setProfilesLoaded] = useState<Loaded<ReportStyleProfileSummary[]> | null>(null);
  const [figuresLoaded, setFiguresLoaded] = useState<Loaded<ReportTableFigure[]> | null>(null);
  const [graphicsLoaded, setGraphicsLoaded] = useState<Loaded<ReportGraphicFigure[]> | null>(null);

  const [scenarioId, setScenarioId] = useState("");
  const [beforeScenarioId, setBeforeScenarioId] = useState("");
  const [afterScenarioId, setAfterScenarioId] = useState("");
  const [styleProfileId, setStyleProfileId] = useState("");
  const [reportLanguage, setReportLanguage] = useState("");
  const [excludedSectionIds, setExcludedSectionIds] = useState<string[]>([]);

  const [busy, setBusy] = useState<"docx" | "artifacts" | null>(null);
  const [refusal, setRefusal] = useState<ReportRefusal | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ComposeOutcome | null>(null);

  const figureParams: ReportFigureParams = useMemo(
    () => ({
      scenario_id: scenarioId || null,
      before_scenario_id: beforeScenarioId || null,
      after_scenario_id: afterScenarioId || null,
    }),
    [scenarioId, beforeScenarioId, afterScenarioId],
  );
  const figureKey = useMemo(() => JSON.stringify(figureParams), [figureParams]);

  useEffect(() => {
    let cancelled = false;
    fetchScenarios()
      .then((data) => {
        if (!cancelled) setScenariosLoaded(ok("scenarios", data));
      })
      .catch((caught) => {
        if (!cancelled) setScenariosLoaded(failed("scenarios", caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchReportSpine()
      .then((data) => {
        if (!cancelled) setSpineLoaded(ok("spine", data));
      })
      .catch((caught) => {
        if (!cancelled) setSpineLoaded(failed("spine", caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchReportStyleProfiles()
      .then((data) => {
        if (!cancelled) setProfilesLoaded(ok("profiles", data));
      })
      .catch((caught) => {
        if (!cancelled) setProfilesLoaded(failed("profiles", caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchReportFigures(figureParams)
      .then((data) => {
        if (!cancelled) setFiguresLoaded(ok(figureKey, data));
      })
      .catch((caught) => {
        if (!cancelled) setFiguresLoaded(failed(figureKey, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [figureParams, figureKey]);

  useEffect(() => {
    let cancelled = false;
    fetchReportGraphics(figureParams)
      .then((data) => {
        if (!cancelled) setGraphicsLoaded(ok(figureKey, data));
      })
      .catch((caught) => {
        if (!cancelled) setGraphicsLoaded(failed(figureKey, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [figureParams, figureKey]);

  const scenarios = resolve(scenariosLoaded, "scenarios");
  const spine = resolve(spineLoaded, "spine");
  const profiles = resolve(profilesLoaded, "profiles");
  const tables = resolve(figuresLoaded, figureKey);
  const graphics = resolve(graphicsLoaded, figureKey);

  const allFigures: PreviewFigure[] = useMemo(() => {
    const tableItems: PreviewFigure[] = (tables.data ?? []).map((figure) => ({ kind: "table", figure }));
    const graphicItems: PreviewFigure[] = (graphics.data ?? []).map((figure) => ({ kind: "graphic", figure }));
    return [...tableItems, ...graphicItems];
  }, [tables.data, graphics.data]);

  const sections: ReportSpineSection[] = useMemo(() => spine.data?.sections ?? [], [spine.data]);
  const unboundFigures = useMemo(
    () => allFigures.filter((item) => item.figure.spine_section_id === null),
    [allFigures],
  );
  const treeSectionIds = useMemo(() => {
    const ids = sections.map((section) => section.id);
    return unboundFigures.length > 0 ? [...ids, UNBOUND_SECTION_ID] : ids;
  }, [sections, unboundFigures.length]);
  const includedSectionIds = useMemo(
    () => treeSectionIds.filter((id) => !excludedSectionIds.includes(id)),
    [treeSectionIds, excludedSectionIds],
  );

  const figuresFor = useCallback(
    (sectionId: string): PreviewFigure[] =>
      sectionId === UNBOUND_SECTION_ID
        ? unboundFigures
        : allFigures.filter((item) => item.figure.spine_section_id === sectionId),
    [allFigures, unboundFigures],
  );

  const toggleSection = (sectionId: string) =>
    setExcludedSectionIds((previous) =>
      previous.includes(sectionId) ? previous.filter((id) => id !== sectionId) : [...previous, sectionId],
    );

  const effectiveLanguage = reportLanguage || lang;

  const requestBody = useMemo(
    () => composeRequestBody({ includedSectionIds, styleProfileId, language: effectiveLanguage, figureParams }),
    [includedSectionIds, styleProfileId, effectiveLanguage, figureParams],
  );

  const runCompose = useCallback(
    async (mode: "docx" | "artifacts") => {
      setBusy(mode);
      setRefusal(null);
      setFailure(null);
      setOutcome(null);
      try {
        let docx: ComposeOutcome["docx"] = null;
        if (mode === "docx") {
          const composed = await composeReport(requestBody);
          downloadBlob(composed.blob, composed.filename);
          docx = { filename: composed.filename, sections: composed.sections, limitations: composed.limitations };
        }
        const bundle = await composeReportArtifacts(requestBody);
        downloadBlob(bundle.blob, bundle.filename);
        const manifest = await readReportManifest(bundle.blob);
        setOutcome({ docx, bundle: { filename: bundle.filename, artifacts: bundle.artifacts }, manifest });
      } catch (caught) {
        if (caught instanceof ReportRefusedError) {
          setRefusal(caught.refusal);
        } else {
          setFailure(caught instanceof Error ? caught.message : String(caught));
        }
      } finally {
        setBusy(null);
      }
    },
    [requestBody],
  );

  const runOptions = (scenarios.data ?? []).map((scenario) => (
    <option key={scenario.id} value={scenario.id}>
      {scenario.name}
    </option>
  ));

  return (
    <div className="mx-auto max-w-[1400px] space-y-5 px-4 py-6">
      <div>
        <h1 className="text-[22px] font-semibold text-white">{t("보고서", "Report", "レポート")}</h1>
        <p className="mt-1 text-[15px] text-slate-600">
          {t(
            "MRWA가 요구하는 절 구조에 이 제품의 도판을 채워 Word 보고서를 조판합니다.",
            "Typesets a Word report by filling the MRWA-required section structure with this product's figures.",
            "MRWAが要求する節構造にこの製品の図版を入れてWordレポートを組版します。",
          )}
        </p>
      </div>

      {/* ---- top bar: the inputs the preview AND the document are built from -- */}
      <section className="ui-surface flex flex-wrap items-end gap-4 rounded-xl border border-slate-200 px-4 py-3">
        <label className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("채점할 실행", "Run to score", "採点する実行")}</span>
          <select
            value={scenarioId}
            onChange={(event) => setScenarioId(event.target.value)}
            className="ui-select h-9 min-w-[220px] rounded-md border border-slate-300 px-2 text-[14px]"
          >
            <option value="">{t("선택 안 함", "Not selected", "選択しない")}</option>
            {runOptions}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("전후 비교 — 전", "Before/after — before", "前後比較 — 前")}</span>
          <select
            value={beforeScenarioId}
            onChange={(event) => setBeforeScenarioId(event.target.value)}
            className="ui-select h-9 min-w-[180px] rounded-md border border-slate-300 px-2 text-[14px]"
          >
            <option value="">{t("선택 안 함", "Not selected", "選択しない")}</option>
            {runOptions}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("전후 비교 — 후", "Before/after — after", "前後比較 — 後")}</span>
          <select
            value={afterScenarioId}
            onChange={(event) => setAfterScenarioId(event.target.value)}
            className="ui-select h-9 min-w-[180px] rounded-md border border-slate-300 px-2 text-[14px]"
          >
            <option value="">{t("선택 안 함", "Not selected", "選択しない")}</option>
            {runOptions}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("스타일 프로파일", "Style profile", "スタイルプロファイル")}</span>
          <select
            value={styleProfileId}
            onChange={(event) => setStyleProfileId(event.target.value)}
            className="ui-select h-9 min-w-[200px] rounded-md border border-slate-300 px-2 text-[14px]"
          >
            <option value="">{t("Prism 기본 스타일", "Prism default style", "Prism 既定スタイル")}</option>
            {(profiles.data ?? []).map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("보고서 언어", "Report language", "レポート言語")}</span>
          <select
            value={reportLanguage}
            onChange={(event) => setReportLanguage(event.target.value)}
            className="ui-select h-9 min-w-[160px] rounded-md border border-slate-300 px-2 text-[14px]"
          >
            <option value="">{t("화면 언어를 따름", "Follow the screen", "画面の言語に従う")}</option>
            <option value="ko">한국어</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>
        </label>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => runCompose("docx")}
            disabled={busy !== null || includedSectionIds.length === 0}
            className="ui-button-primary px-4 py-2 text-[15px] disabled:cursor-wait disabled:opacity-50"
          >
            {busy === "docx"
              ? t("생성 중…", "Generating…", "生成中…")
              : t("생성 (DOCX + 도판 zip)", "Generate (DOCX + figure zip)", "生成 (DOCX + 図版zip)")}
          </button>
          <button
            type="button"
            onClick={() => runCompose("artifacts")}
            disabled={busy !== null || includedSectionIds.length === 0}
            className="ui-button-ghost px-3 py-2 text-[14px] disabled:cursor-wait disabled:opacity-50"
          >
            {busy === "artifacts"
              ? t("내보내는 중…", "Exporting…", "書き出し中…")
              : t("도판 원본 zip만", "Figure originals zip only", "図版原本zipのみ")}
          </button>
        </div>
      </section>

      {profiles.error !== null ? (
        <p className="rounded-lg border border-slate-300 px-3 py-2 text-[14px] text-slate-500">
          {t(
            "저장된 스타일 프로파일 목록을 읽지 못했습니다 — Prism 기본 스타일로만 생성할 수 있습니다.",
            "The stored style profiles could not be read — only the Prism default style is available.",
            "保存済みスタイルプロファイル一覧を読めませんでした — Prism既定スタイルでのみ生成できます。",
          )}
        </p>
      ) : null}

      {/* ---- a refusal is an ANSWER: the backend's own sentence ------------- */}
      {refusal !== null ? (
        <section className="rounded-xl border border-rose-300 px-4 py-3">
          <h2 className="text-[15px] font-semibold text-rose-600">
            {t("보고서를 만들지 않았습니다", "No report was produced", "レポートは作成されませんでした")}
          </h2>
          <p className="mt-1 text-[15px] text-slate-700">{pick(refusal.message, lang)}</p>
          <p className="mt-1 text-[13px] text-slate-500">{refusal.reason_code}</p>
        </section>
      ) : null}

      {failure !== null ? (
        <section className="rounded-xl border border-rose-300 px-4 py-3">
          <h2 className="text-[15px] font-semibold text-rose-600">
            {t("생성 요청이 실패했습니다", "The compose request failed", "生成リクエストが失敗しました")}
          </h2>
          <p className="mt-1 text-[14px] text-slate-700">{failure}</p>
        </section>
      ) : null}

      {outcome !== null ? <ComposeReceipt outcome={outcome} lang={lang} t={t} /> : null}

      {/* ---- the split: spine tree left, per-section preview right ---------- */}
      <div className="grid gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
        <section className="rounded-xl border border-slate-200 p-4">
          <h2 className="text-[15px] font-semibold text-white">{t("보고서 스파인", "Report spine", "レポートスパイン")}</h2>
          {spine.loading ? (
            <p className="mt-2 text-[14px] text-slate-500">
              {t("스파인을 불러오는 중…", "Loading the spine…", "スパインを読み込み中…")}
            </p>
          ) : spine.error !== null ? (
            <p className="mt-2 text-[14px] text-slate-500">
              {t(
                "스파인을 읽지 못했습니다 — 절 목록 없이는 보고서를 만들 수 없습니다.",
                "The spine could not be read — no report can be built without the section list.",
                "スパインを読めませんでした — 節一覧なしではレポートを作成できません。",
              )}
            </p>
          ) : spine.data === null ? (
            <p className="mt-2 text-[14px] text-slate-500">
              {t("스파인이 비어 있습니다.", "The spine is empty.", "スパインが空です。")}
            </p>
          ) : (
            <>
              <p className="mt-1 text-[12px] text-slate-500">{spine.data.minimum.sentence}</p>
              <ol className="mt-3 space-y-2">
                {sections.map((section) => (
                  <li key={section.id} className="rounded-lg border border-slate-200 px-3 py-2">
                    <SectionToggle
                      title={pick(section.title, lang)}
                      included={!excludedSectionIds.includes(section.id)}
                      onToggle={() => toggleSection(section.id)}
                      t={t}
                    />
                    <EvidenceStateList
                      figures={figuresFor(section.id)}
                      owners={section.source_keys.map((key) => key.owner)}
                      notAvailable={notAvailableOutputs(section)}
                      lang={lang}
                      t={t}
                    />
                    {section.basis.map((line) => (
                      <p key={line} className="mt-1 text-[12px] text-slate-500">
                        {line}
                      </p>
                    ))}
                  </li>
                ))}
                {unboundFigures.length > 0 ? (
                  <li className="rounded-lg border border-slate-200 px-3 py-2">
                    <SectionToggle
                      title={t("절에 묶이지 않은 도판", "Figures bound to no section", "節に紐づかない図版")}
                      included={!excludedSectionIds.includes(UNBOUND_SECTION_ID)}
                      onToggle={() => toggleSection(UNBOUND_SECTION_ID)}
                      t={t}
                    />
                    <EvidenceStateList figures={unboundFigures} owners={[]} notAvailable={[]} lang={lang} t={t} />
                  </li>
                ) : null}
              </ol>
            </>
          )}
        </section>

        <section className="space-y-4">
          {tables.error !== null || graphics.error !== null ? (
            <p className="rounded-lg border border-slate-300 px-3 py-2 text-[14px] text-slate-500">
              {t(
                "도판을 읽지 못했습니다 — 아래 미리보기는 읽어낸 것만 보여 줍니다.",
                "The figures could not be read — the preview below shows only what was read.",
                "図版を読めませんでした — 以下のプレビューは読み取れた分のみを表示します。",
              )}
            </p>
          ) : null}
          {sections.map((section) => (
            <SectionPreview
              key={section.id}
              title={pick(section.title, lang)}
              basis={section.basis}
              figures={figuresFor(section.id)}
              notAvailable={notAvailableOutputs(section)}
              included={!excludedSectionIds.includes(section.id)}
              lang={lang}
              t={t}
            />
          ))}
          {unboundFigures.length > 0 ? (
            <SectionPreview
              title={t("절에 묶이지 않은 도판", "Figures bound to no section", "節に紐づかない図版")}
              basis={[]}
              figures={unboundFigures}
              notAvailable={[]}
              included={!excludedSectionIds.includes(UNBOUND_SECTION_ID)}
              lang={lang}
              t={t}
            />
          ) : null}
        </section>
      </div>

      {/* ---- the OLD workbook path, demoted but intact ---------------------- */}
      <details className="rounded-xl border border-slate-200 px-4 py-3">
        <summary className="cursor-pointer text-[15px] font-semibold text-slate-700">
          {t("기존 내보내기 (구 워크북 경로)", "Legacy export (old workbook path)", "従来のエクスポート (旧ワークブック経路)")}
        </summary>
        <p className="mt-2 text-[13px] text-slate-500">
          {t(
            "블록을 골라 만드는 예전 Word 보고서와 블록별 CSV 내보내기입니다. 위 조판기와는 다른 데이터 경로(검증 워크북)를 읽으므로 그대로 남겨 둡니다.",
            "The older block-composed Word report and its per-block CSV export. It reads a different data path (the validation workbook) from the typesetter above, so it stays.",
            "ブロックを選んで作る従来のWordレポートとブロック別CSV出力です。上の組版機とは別のデータ経路(検証ワークブック)を読むため残しています。",
          )}
        </p>
        <div className="mt-3">
          <LegacyReportBuilder />
        </div>
      </details>
    </div>
  );
}

function SectionToggle(props: { title: string; included: boolean; onToggle: () => void; t: Translate }) {
  const { title, included, onToggle, t } = props;
  return (
    <>
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={included} onChange={onToggle} className="mt-1 h-4 w-4 flex-none" aria-label={title} />
        <span className="text-[15px] font-medium text-slate-800">{title}</span>
      </label>
      {included ? null : (
        <p className="mt-1 text-[13px] text-slate-500">
          {t(
            "선택에서 제외됨 — 증거가 없어서가 아니라 사용자가 뺐습니다.",
            "Excluded by selection — the user left it out; it is not an evidence gap.",
            "選択から除外 — 証拠がないからではなく、利用者が外しました。",
          )}
        </p>
      )}
    </>
  );
}

/** The per-section data-source state the design doc asks the tree to carry. A
 * present figure is named; an absent one is grey and carries the SERVER's own
 * reason; a section with no figure at all says so and names the module that
 * would own those numbers. */
function EvidenceStateList(props: {
  figures: PreviewFigure[];
  owners: string[];
  notAvailable: { name: string; reason: string }[];
  lang: string;
  t: Translate;
}) {
  const { figures, owners, notAvailable, lang, t } = props;
  return (
    <>
      {figures.length === 0 ? (
        <p className="mt-1 text-[13px] text-slate-500">
          {t("이 절의 증거 없음", "No evidence for this section", "この節の証拠なし")}
          {owners.length > 0 ? ` — ${owners.join(", ")}` : ""}
        </p>
      ) : (
        <ul className="mt-1 space-y-1">
          {figures.map((item) => (
            <li key={item.figure.figure_id} className="text-[13px]">
              {item.figure.absent ? (
                <span className="text-slate-500">
                  {item.figure.figure_id} — {pick(item.figure.reason, lang)}
                </span>
              ) : (
                <span className="text-emerald-600">
                  {item.figure.figure_id} — {t("증거 있음", "evidence present", "証拠あり")}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {notAvailable.map((output) => (
        <p key={output.name} className="mt-1 text-[13px] text-slate-500">
          {t("추출 불가", "not extractable", "抽出不可")} — {output.name}: {output.reason}
        </p>
      ))}
    </>
  );
}

function SectionPreview(props: {
  title: string;
  basis: string[];
  figures: PreviewFigure[];
  notAvailable: { name: string; reason: string }[];
  included: boolean;
  lang: string;
  t: Translate;
}) {
  const { title, basis, figures, notAvailable, included, lang, t } = props;

  if (!included) {
    return (
      <section className="rounded-xl border border-slate-200 px-4 py-3">
        <h3 className="text-[15px] font-medium text-slate-500">{title}</h3>
        <p className="mt-1 text-[13px] text-slate-500">
          {t(
            "선택에서 제외됨 — 이 절은 문서에 실리지 않습니다. 증거가 없다는 뜻이 아닙니다.",
            "Excluded by selection — this section is not typeset into the document. It does not mean there is no evidence.",
            "選択から除外 — この節は文書に載りません。証拠がないという意味ではありません。",
          )}
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-slate-200 px-4 py-3">
      <h3 className="text-[15px] font-semibold text-white">{title}</h3>
      {basis.map((line) => (
        <p key={line} className="mt-1 text-[12px] text-slate-500">
          {line}
        </p>
      ))}

      {figures.length === 0 ? (
        <p className="mt-2 text-[14px] text-slate-500">
          {t("이 절의 증거 없음", "No evidence for this section", "この節の証拠なし")}
        </p>
      ) : (
        figures.map((item) =>
          item.kind === "table" ? (
            <TableFigureBlock key={item.figure.figure_id} figure={item.figure} lang={lang} t={t} />
          ) : (
            <GraphicFigureBlock key={item.figure.figure_id} figure={item.figure} lang={lang} t={t} />
          ),
        )
      )}

      {notAvailable.map((output) => (
        <p key={output.name} className="mt-2 text-[13px] text-slate-500">
          {t("추출 불가", "not extractable", "抽出不可")} — {output.name}: {output.reason}
        </p>
      ))}
    </section>
  );
}

function TableFigureBlock(props: { figure: ReportTableFigure; lang: string; t: Translate }) {
  const { figure, lang, t } = props;

  if (figure.absent) {
    return (
      <div className="mt-3 rounded-lg border border-slate-300 px-3 py-2">
        <p className="text-[14px] font-medium text-slate-500">{figure.figure_id}</p>
        <p className="mt-1 text-[14px] text-slate-500">{pick(figure.reason, lang)}</p>
      </div>
    );
  }

  const columns = figure.columns ?? [];
  const rows = figure.rows ?? [];
  return (
    <div className="mt-3">
      <p className="text-[14px] font-semibold text-slate-700">{pick(figure.title, lang) || figure.figure_id}</p>
      <div className="mt-1 overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key} className="border border-slate-200 px-2 py-1 text-left font-semibold text-slate-600">
                  {pick(column, lang)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column.key} className="border border-slate-200 px-2 py-1 text-slate-700">
                    {cellText(row[column.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <SourceCaptionLine caption={figure.source_caption} citation={figure.citation} t={t} />
    </div>
  );
}

function GraphicFigureBlock(props: { figure: ReportGraphicFigure; lang: string; t: Translate }) {
  const { figure, lang, t } = props;

  if (figure.absent) {
    return (
      <div className="mt-3 rounded-lg border border-slate-300 px-3 py-2">
        <p className="text-[14px] font-medium text-slate-500">{figure.figure_id}</p>
        <p className="mt-1 text-[14px] text-slate-500">{pick(figure.reason, lang)}</p>
      </div>
    );
  }

  const svg = figure.svg;
  return (
    <div className="mt-3">
      <p className="text-[14px] font-semibold text-slate-700">{pick(figure.title, lang) || figure.figure_id}</p>
      {typeof svg === "string" ? (
        // THE ONLY INJECTION ON THIS PAGE, and its source is the API payload's
        // own `svg` field -- R-3 rendered it server-side and the same string is
        // what ships in the artifact zip. Nothing is concatenated into it here.
        <div className="mt-1 overflow-x-auto" dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <p className="mt-1 text-[14px] text-slate-500">
          {t("이 도판에는 SVG가 실려 있지 않습니다.", "This figure carries no SVG.", "この図版にはSVGが含まれていません。")}
        </p>
      )}
      {(figure.legend ?? []).length > 0 ? (
        <p className="mt-1 text-[13px] text-slate-500">
          {(figure.legend ?? []).map((entry) => pick(entry, lang)).join(" · ")}
        </p>
      ) : null}
      <SourceCaptionLine caption={figure.source_caption} citation={figure.citation} t={t} />
    </div>
  );
}

/** The design doc's rule, on screen: a figure that cannot answer "which run is
 * this?" is not emitted. Caption keys differ per generator, so every non-null
 * key the server sent is printed rather than a chosen few. */
function SourceCaptionLine(props: {
  caption: ReportFigureSourceCaption | undefined;
  citation: string | null | undefined;
  t: Translate;
}) {
  const { caption, citation, t } = props;
  const entries = Object.entries(caption ?? {}).filter(([, value]) => value !== null && value !== undefined);
  if (entries.length === 0 && !citation) {
    return <p className="mt-1 text-[12px] text-slate-500">{t("출처 정보 없음", "No source caption", "出典情報なし")}</p>;
  }
  return (
    <p className="mt-1 text-[12px] text-slate-500">
      {entries.map(([key, value]) => `${key}: ${cellText(value)}`).join(" · ")}
      {citation ? ` · ${citation}` : ""}
    </p>
  );
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function manifestUnreadableText(reason: ManifestUnreadableReason, t: Translate): string {
  if (reason === "no_decompression_stream") {
    return t(
      "이 브라우저는 zip 압축을 풀 수 없어 매니페스트를 읽지 못했습니다. zip 파일 자체는 정상적으로 내려받았습니다.",
      "This browser cannot inflate the bundle, so the manifest was not read. The zip itself downloaded fine.",
      "このブラウザはzipを展開できないためマニフェストを読めませんでした。zipファイル自体は正常にダウンロードされています。",
    );
  }
  if (reason === "entry_missing") {
    return t(
      "내려받은 zip에 manifest.json이 없습니다.",
      "The downloaded zip carries no manifest.json.",
      "ダウンロードしたzipにmanifest.jsonがありません。",
    );
  }
  if (reason === "unsupported_compression") {
    return t(
      "manifest.json이 이 화면이 풀 수 없는 방식으로 압축되어 있습니다.",
      "manifest.json is stored with a compression method this screen cannot read.",
      "manifest.jsonはこの画面が展開できない圧縮方式で格納されています。",
    );
  }
  if (reason === "not_json") {
    return t(
      "manifest.json을 JSON으로 해석하지 못했습니다.",
      "manifest.json could not be parsed as JSON.",
      "manifest.jsonをJSONとして解釈できませんでした。",
    );
  }
  return t(
    "내려받은 파일을 zip으로 읽지 못했습니다.",
    "The downloaded file could not be read as a zip.",
    "ダウンロードしたファイルをzipとして読めませんでした。",
  );
}

/**
 * What the produced document does NOT do, in the typesetter's own words.
 *
 * The .docx response carries only two COUNTS (its `X-Prism-Report-*` headers);
 * the SENTENCES live in `manifest.json` inside the artifact bundle, which is
 * why the bundle is read here rather than the counts being dressed up into
 * prose. `svg_not_embedded_in_docx` and every style fallback print exactly as
 * the backend wrote them.
 */
function ComposeReceipt(props: { outcome: ComposeOutcome; lang: string; t: Translate }) {
  const { outcome, lang, t } = props;
  const manifest = outcome.manifest !== null && outcome.manifest.ok ? outcome.manifest.manifest : null;
  const docx = outcome.docx;
  const bundle = outcome.bundle;

  return (
    <section className="rounded-xl border border-emerald-300 px-4 py-3">
      <h2 className="text-[15px] font-semibold text-emerald-600">{t("생성 완료", "Composed", "生成完了")}</h2>

      {docx !== null ? (
        <p className="mt-1 text-[14px] text-slate-700">
          {docx.filename} ·{" "}
          {docx.sections !== null
            ? t(`절 ${docx.sections}개`, `${docx.sections} section(s)`, `節 ${docx.sections}個`)
            : t("절 수를 읽지 못했습니다", "the section count was not readable", "節数を読めませんでした")}{" "}
          ·{" "}
          {docx.limitations !== null
            ? t(`명명된 한계 ${docx.limitations}건`, `${docx.limitations} named limitation(s)`, `名前付き限界 ${docx.limitations}件`)
            : t("한계 수를 읽지 못했습니다", "the limitation count was not readable", "限界数を読めませんでした")}
        </p>
      ) : (
        <p className="mt-1 text-[14px] text-slate-700">
          {t(
            "문서는 만들지 않았습니다 — 도판 원본 zip만 내보냈습니다.",
            "No document was produced — only the figure-originals zip was exported.",
            "文書は作成していません — 図版原本zipのみ書き出しました。",
          )}
        </p>
      )}

      {bundle !== null ? (
        <p className="mt-1 text-[14px] text-slate-700">
          {bundle.filename}
          {bundle.artifacts !== null
            ? ` · ${t(`도판 원본 ${bundle.artifacts}개`, `${bundle.artifacts} figure original(s)`, `図版原本 ${bundle.artifacts}個`)}`
            : ""}
        </p>
      ) : null}

      {outcome.manifest !== null && !outcome.manifest.ok ? (
        <p className="mt-2 text-[14px] text-slate-500">{manifestUnreadableText(outcome.manifest.reason, t)}</p>
      ) : null}

      {manifest !== null ? (
        <>
          <p className="mt-2 text-[14px] text-slate-700">
            {t("스타일", "Style", "スタイル")}: {manifest.style.profile_name ?? manifest.style.source} ·{" "}
            {t(
              `폴백 ${manifest.style.fallbacks.length}건`,
              `${manifest.style.fallbacks.length} fallback(s)`,
              `フォールバック ${manifest.style.fallbacks.length}件`,
            )}
          </p>
          {manifest.style.fallbacks.map((fallback) => (
            <p key={fallback.field} className="mt-1 text-[13px] text-slate-500">
              {fallback.field}: {pick(fallback.reason, lang)}
              {fallback.profile_reason ? ` — ${fallback.profile_reason}` : ""}
            </p>
          ))}

          <h3 className="mt-3 text-[14px] font-semibold text-slate-700">
            {t("이 문서가 하지 못한 것", "What this document does not do", "この文書ができなかったこと")}
          </h3>
          {manifest.limitations.map((limitation) => (
            <p key={limitation.code} className="mt-1 text-[13px] text-slate-600">
              {pick(limitation.reason, lang)}
            </p>
          ))}

          <h3 className="mt-3 text-[14px] font-semibold text-slate-700">{t("절 상태", "Section states", "節の状態")}</h3>
          <ul className="mt-1 space-y-0.5">
            {manifest.sections.map((section) => (
              <li key={section.id} className="text-[13px] text-slate-600">
                {section.heading_number ? `${section.heading_number} ` : ""}
                {section.title} —{" "}
                {section.state === SECTION_STATE_EXCLUDED
                  ? t("선택에서 제외됨", "excluded by selection", "選択から除外")
                  : t(
                      `실림 · 명명된 부재 ${section.named_absences.length}건`,
                      `included · ${section.named_absences.length} named absence(s)`,
                      `収録 · 名前付き不在 ${section.named_absences.length}件`,
                    )}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

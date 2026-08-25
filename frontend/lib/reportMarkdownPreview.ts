// ---------------------------------------------------------------------------
// The /report screen's TEXT-ONLY projection (unit #60b, docs/phase2/
// DECISIONS.md "2026-08-25 -- 사용자 테스트 1차 피드백", #60b block).
//
// WHY THIS FILE EXISTS. The user's own words on the pushed build: too much
// fine print (per-section MRWA citation quotes, figure caption/citation
// lines) sitting on the default screen -- "if they are the paragraphs, dont
// show them. make a separate preview file and project texts as md format.
// text preview only, no style preview". app/report/page.tsx answers the
// first half by moving those paragraphs behind a 자세히 fold; this module
// answers the second half: a Markdown PROJECTION of the same report, built
// so the paragraphs a reader wants to actually READ (not just know exist)
// have one deliberate place to go.
//
// NO NEW BACKEND SURFACE. Every field this module reads comes from the THREE
// payloads the report screen already fetched with the ONE `figureParams`
// object (`GET /spine`, `GET /figures`, `GET /graphics` -- R-1/R-2/R-3). This
// file makes no request of its own; the caller hands over data it already
// has, exactly like `lib/reportManifest.ts` reads a zip the caller already
// downloaded.
//
// VERBATIM, STRUCTURALLY. Every sentence the backend wrote -- a figure's
// absence reason, a NOT_AVAILABLE output's reason, a section's `basis`
// quote, a source-caption value -- is spliced into the Markdown by
// reference, never `.slice()`d, `.substring()`d or otherwise shortened. The
// only string transform in this file is `mdCell`'s pipe-escape, and it
// touches table CELL VALUES only (so `|` cannot masquerade as a column
// separator) -- never a `reason`/`basis`/`citation` sentence, which always
// renders as a plain line where a literal `|` is harmless. A contract pin
// bans truncation across this whole file so that boundary cannot drift.
//
// NO INVENTED CONTENT. This module composes exactly the rows, reasons and
// captions the caller passed in -- no section, output or figure not present
// in `sections`/`tableFigures`/`graphicFigures` is added, and a table's body
// carries exactly the caller's `rows`, one Markdown row per input row.
// Markdown SYNTAX (headings, table pipes, bullet dashes) and this screen's
// own short structural labels (a heading, "not extractable") are the only
// things this file writes that the server did not -- exactly the same
// "structure and labels are this screen's own" boundary the JSX page's own
// header comment states.
// ---------------------------------------------------------------------------

// Relative imports below carry an explicit `.ts` extension (permitted by
// tsconfig's `allowImportingTsExtensions`, same as tests/dashboard-contract.
// test.ts's own imports) because this module is loaded TWO ways: bundled by
// Next.js from app/report/page.tsx (extensionless or not, the bundler
// resolves either), and loaded DIRECTLY by `node --test
// --experimental-strip-types` (tests/dashboard-contract.test.ts imports this
// file for its own behavioural test) -- Node's native ESM loader does no
// extension-guessing, so an extensionless relative import resolves in the
// bundler and 404s under the test runner. Explicit extensions satisfy both.
import type {
  ReportFigureSourceCaption,
  ReportGraphicFigure,
  ReportSpineSection,
  ReportTableFigure,
} from "./api.ts";
import { pick } from "./serverText.ts";

export type MarkdownPreviewTranslate = (ko: string, en: string, ja?: string) => string;

export type MarkdownPreviewFigure =
  | { kind: "table"; figure: ReportTableFigure }
  | { kind: "graphic"; figure: ReportGraphicFigure };

export interface ReportMarkdownPreviewInput {
  lang: string;
  t: MarkdownPreviewTranslate;
  /** Spine order IS document order -- the same array the tree/preview render
   * from, walked once, in the order the server returned it. */
  sections: ReportSpineSection[];
  /** R-2's table figures, exactly as `GET /figures` returned them. */
  tableFigures: ReportTableFigure[];
  /** R-3's graphic figures, exactly as `GET /graphics` returned them. */
  graphicFigures: ReportGraphicFigure[];
  /** The tree's own "절에 묶이지 않은 도판" label, resolved by the CALLER's
   * `t()` -- kept as a plain string rather than re-declared here so this
   * file does not carry a second copy of a triple the page already pins. */
  unboundTitle: string;
  /** Sections the user unchecked on screen -- the SAME list `composeRequestBody`
   * reads, so the preview never claims a section is included when the
   * document that would be composed right now would leave it out. */
  excludedSectionIds: string[];
}

/** A table cell's text form. Byte-identical to `app/report/page.tsx`'s own
 * `cellText`, plus one addition: a literal `|` is escaped so it cannot be
 * misread as a Markdown table delimiter. The escape is reversible (the
 * character is still there, just escaped) and applies ONLY to cell/caption
 * VALUES -- never to a `reason`/`basis`/`citation` sentence, which this file
 * always renders as a plain line, not a table cell. */
function mdCell(value: unknown): string {
  const text =
    value === null || value === undefined
      ? "—"
      : typeof value === "boolean"
        ? value
          ? "true"
          : "false"
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
  return text.replace(/\|/g, "\\|");
}

/** The design doc's source-caption line, as text: every non-null key the
 * server sent, joined, plus the citation -- the exact fields
 * `SourceCaptionLine` renders in JSX, spliced through unmodified. */
function sourceCaptionLine(
  caption: ReportFigureSourceCaption | undefined,
  citation: string | null | undefined,
  t: MarkdownPreviewTranslate,
): string {
  const entries = Object.entries(caption ?? {}).filter(([, value]) => value !== null && value !== undefined);
  if (entries.length === 0 && !citation) {
    return t("출처 정보 없음", "No source caption", "出典情報なし");
  }
  const parts = entries.map(([key, value]) => `${key}: ${mdCell(value)}`);
  if (citation) parts.push(citation);
  return parts.join(" · ");
}

/**
 * The whole report, as one Markdown string -- sections in spine order, each
 * with its heading, its `basis` quote(s), its figures (a table rendered as a
 * Markdown table, a graphic named with its legend/caption since no image can
 * live in plain text, an absent figure with the server's own reason), and a
 * final "한계" section collecting every NOT_AVAILABLE output and absent
 * figure this build already knew about -- nothing recomputed, nothing new
 * fetched.
 */
export function buildMarkdownPreview(input: ReportMarkdownPreviewInput): string {
  const { lang, t, sections, tableFigures, graphicFigures, unboundTitle, excludedSectionIds } = input;

  const figuresForSection = (sectionId: string | null): MarkdownPreviewFigure[] => [
    ...tableFigures
      .filter((figure) => figure.spine_section_id === sectionId)
      .map((figure): MarkdownPreviewFigure => ({ kind: "table", figure })),
    ...graphicFigures
      .filter((figure) => figure.spine_section_id === sectionId)
      .map((figure): MarkdownPreviewFigure => ({ kind: "graphic", figure })),
  ];

  const lines: string[] = [];
  const push = (line: string) => lines.push(line);
  const limitations: string[] = [];

  const renderFigure = (item: MarkdownPreviewFigure) => {
    if (item.figure.absent) {
      const reason = pick(item.figure.reason, lang);
      push(`**${item.figure.figure_id}** — ${reason}`);
      push("");
      limitations.push(`${item.figure.figure_id}: ${reason}`);
      return;
    }

    push(`**${pick(item.figure.title, lang) || item.figure.figure_id}**`);
    push("");

    if (item.kind === "table") {
      const columns = item.figure.columns ?? [];
      const rows = item.figure.rows ?? [];
      if (columns.length > 0) {
        push(`| ${columns.map((column) => pick(column, lang)).join(" | ")} |`);
        push(`| ${columns.map(() => "---").join(" | ")} |`);
        for (const row of rows) {
          push(`| ${columns.map((column) => mdCell(row[column.key])).join(" | ")} |`);
        }
        push("");
      }
    } else {
      push(
        t(
          "그래픽 원본(SVG)은 이 텍스트 미리보기에 담기지 않습니다 — 내려받은 문서 또는 도판 zip에서 확인하세요.",
          "The graphic original (SVG) is not carried in this text preview — see the downloaded document or the figure zip.",
          "グラフィック原本（SVG）はこのテキストプレビューには含まれません — ダウンロードした文書または図版zipで確認してください。",
        ),
      );
      push("");
      for (const entry of item.figure.legend ?? []) {
        push(`- ${pick(entry, lang)}`);
      }
      if ((item.figure.legend ?? []).length > 0) push("");
    }

    push(sourceCaptionLine(item.figure.source_caption, item.figure.citation, t));
    push("");
  };

  push(`# ${t("보고서 텍스트 미리보기", "Report text preview", "レポートのテキストプレビュー")}`);
  push("");
  push(
    t(
      "이 미리보기는 이 화면이 이미 읽어 온 데이터로만 만든 텍스트 사본입니다 — 서식이나 도판 이미지는 담지 않습니다.",
      "This preview is a text copy built only from data this screen already fetched — it carries no styling and no figure images.",
      "このプレビューはこの画面が既に取得したデータのみで作ったテキスト写しです — 書式や図版画像は含みません。",
    ),
  );
  push("");

  for (const section of sections) {
    push(`## ${pick(section.title, lang)}`);
    push("");

    if (excludedSectionIds.includes(section.id)) {
      push(
        t(
          "선택에서 제외됨 — 이 절은 문서에 실리지 않습니다. 증거가 없다는 뜻이 아닙니다.",
          "Excluded by selection — this section is not typeset into the document. It does not mean there is no evidence.",
          "選択から除外 — この節は文書に載りません。証拠がないという意味ではありません。",
        ),
      );
      push("");
      continue;
    }

    for (const line of section.basis) {
      push(`> ${line}`);
    }
    if (section.basis.length > 0) push("");

    const figures = figuresForSection(section.id);
    if (figures.length === 0) {
      push(t("이 절의 증거 없음", "No evidence for this section", "この節の証拠なし"));
      push("");
    } else {
      for (const item of figures) renderFigure(item);
    }

    // Mirrors app/report/page.tsx's own `notAvailableOutputs` filter. Kept
    // here rather than imported: lib/ does not depend on app/, and the
    // filter is one line over data this function already has in scope.
    for (const group of section.required_outputs) {
      for (const output of group.outputs) {
        if (output.availability === "NOT_AVAILABLE") {
          const name = `${group.type} · ${output.name}`;
          const reason = output.reason ?? "";
          push(`${t("추출 불가", "not extractable", "抽出不可")} — ${name}: ${reason}`);
          limitations.push(`${name}: ${reason}`);
        }
      }
    }
    push("");
  }

  const unbound = figuresForSection(null);
  if (unbound.length > 0) {
    push(`## ${unboundTitle}`);
    push("");
    for (const item of unbound) renderFigure(item);
  }

  push(`## ${t("한계 목록", "Limitations", "制限一覧")}`);
  push("");
  if (limitations.length === 0) {
    push(t("명명된 한계 없음", "No named limitation", "名前付き制限なし"));
  } else {
    for (const line of limitations) push(`- ${line}`);
  }
  push("");

  return lines.join("\n");
}

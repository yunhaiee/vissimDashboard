// ---------------------------------------------------------------------------
// The /report screen's RENDERED Markdown structure view (unit #64). Consumes
// `lib/reportMarkdownBlocks.ts`'s pure parse and turns it into real
// `<h1>`/`<table>`/`<ul>`/`<blockquote>` elements -- headings as headings,
// tables as tables, lists as lists, paragraphs as paragraphs -- instead of
// the raw Markdown TEXT the pane showed before this unit.
//
// STRUCTURE ONLY, NEVER STYLE. The standing decision from unit #60b --
// "text preview only, no style preview" -- still holds: every class name
// below is this app's own neutral typography (the same `text-[13px]
// text-slate-700` etc. idiom `TableFigureBlock`/`SectionPreview` already use
// on this page), never a font, colour or size drawn from a style profile.
// This component reads no style profile and fetches nothing; its one input
// is the Markdown string the caller already built.
// ---------------------------------------------------------------------------

import { parseMarkdownBlocks, type InlineSegment, type MarkdownBlock } from "@/lib/reportMarkdownBlocks";

function InlineText(props: { segments: InlineSegment[] }) {
  return (
    <>
      {props.segments.map((segment, index) =>
        segment.bold ? (
          <strong key={index} className="font-semibold text-slate-800">
            {segment.text}
          </strong>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </>
  );
}

function BlockNode(props: { block: MarkdownBlock; index: number }) {
  const { block, index } = props;

  switch (block.type) {
    case "heading":
      return block.level === 1 ? (
        <h1 key={index} className="mt-4 text-[17px] font-semibold text-slate-800 first:mt-0">
          <InlineText segments={block.segments} />
        </h1>
      ) : (
        <h2 key={index} className="mt-3 text-[15px] font-semibold text-slate-800 first:mt-0">
          <InlineText segments={block.segments} />
        </h2>
      );

    case "paragraph":
      return (
        <p key={index} className="mt-1.5 text-[13px] text-slate-700">
          <InlineText segments={block.segments} />
        </p>
      );

    case "blockquote":
      return (
        <blockquote key={index} className="mt-1.5 border-l-2 border-slate-300 pl-3 text-[13px] italic text-slate-600">
          {block.lines.map((segments, lineIndex) => (
            <p key={lineIndex} className={lineIndex > 0 ? "mt-1" : undefined}>
              <InlineText segments={segments} />
            </p>
          ))}
        </blockquote>
      );

    case "list":
      return (
        <ul key={index} className="mt-1.5 list-disc space-y-0.5 pl-5 text-[13px] text-slate-700">
          {block.items.map((segments, itemIndex) => (
            <li key={itemIndex}>
              <InlineText segments={segments} />
            </li>
          ))}
        </ul>
      );

    case "table":
      return (
        <div key={index} className="mt-1.5 overflow-x-auto">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                {block.columns.map((column, columnIndex) => (
                  <th
                    key={columnIndex}
                    className="border border-slate-200 px-2 py-1 text-left font-semibold text-slate-600"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="border border-slate-200 px-2 py-1 text-slate-700">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

/** Renders `markdown`'s STRUCTURE -- see `lib/reportMarkdownBlocks.ts` for
 * exactly which constructs are recognised and the honest-fallback rule for
 * everything else. Pure presentational: parses on every render (the
 * `markdown` string is already memoised by the caller, so this stays cheap)
 * and holds no state of its own. */
export default function MarkdownStructurePreview(props: { markdown: string }) {
  const blocks = parseMarkdownBlocks(props.markdown);
  return (
    <div>
      {blocks.map((block, index) => (
        <BlockNode key={index} block={block} index={index} />
      ))}
    </div>
  );
}

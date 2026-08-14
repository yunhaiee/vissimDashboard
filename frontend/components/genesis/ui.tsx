"use client";

// UX pass U-1 (docs/ux-backlog.md) -- the two pieces every genesis screen had
// its OWN copy of, extracted for the same reason `statusLabel`/`formatTimestamp`
// moved into lib/genesisArtifacts.ts: four copies of one thing is four things
// that drift, and drift here is visible to a consultant as "these four screens
// were built by four people".
//
// WHAT WAS MEASURED BEFORE EXTRACTING (2026-08-13):
//   * `Panel` existed FOUR times -- app/genesis/page.tsx:193 and
//     app/genesis/region/page.tsx:208 at `mb-5 px-5 py-4`, app/genesis/
//     demand/page.tsx:47 and app/genesis/phases/page.tsx:69 at
//     `mb-4 px-4 py-3`. Identical markup, two different paddings, and nothing
//     naming which was intended. The `density` prop below keeps BOTH rather
//     than silently restyling two pages: "regular" is the 5/4 variant,
//     "compact" the 4/3 one, and each page keeps the spacing it shipped with.
//   * `refusalHeadline` existed FOUR times, each switching on a DIFFERENT
//     slice of the backend's stable refusal codes with the same default. The
//     codes are globally unique (`genesis_upload_*`, `region_*`, `overpass_*`,
//     `phase_diagram_*`, `genesis_counts_*`, `genesis_demand_*`), so one table
//     serves every caller and a new page gets every existing code for free.
//
// WHAT THIS FILE MUST NOT BECOME: a place where a backend refusal is rewritten.
// Every caller still renders the `reasons` array verbatim underneath the
// headline -- the headline names the CODE in the user's language, the reasons
// carry the file name and the numbers, and inventing a friendlier sentence in
// place of either is how a UI starts lying about why something did not happen.
//
// U-1-quick (2026-08-13) added a THIRD shared piece, `InfoTip` -- see its own
// doc comment for the rule it encodes. Same reason as the two above: five
// screens were about to grow five tooltips.
//
// COLLAPSE IS ALLOWED, DELETION IS NOT. The optional `collapsible` prop below
// exists so a screen can put a section one click away; it uses the same
// 펼치기/접기 wording (and the same button chrome) the genesis screens already
// use, so the app grows no second disclosure vocabulary. A caller that
// needs a section's open state to follow the DATA (open it when this
// intersection's cross-check flagged something) passes `defaultOpen` and a
// `key` that changes with the data -- severity decides the default, the reader
// decides the rest.

import { useEffect, useId, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";

type Translate = (ko: string, en: string, ja: string) => string;

/** The paddings the four copies actually shipped with, named rather than
 * averaged away. `regular` = the original genesis + region variant; `compact` =
 * demand + phases.
 *
 * `console` is U-1's second elaboration (docs/ux-backlog.md item 13): the
 * reference design's ONE-LAYER card — 24px of padding and 32px of rhythm
 * between sections, instead of a 20/16 card that then nests more boxes inside
 * itself. It is a third named value rather than a redefinition of `regular`
 * for the same reason the first two were kept apart: silently restyling a
 * screen nobody reviewed is how a "shared component" becomes a surprise.
 *
 * All FOUR genesis screens take `console` as of 2026-08-13 (unit #13) — every
 * Panel on every one of them was measured as a top-level sibling first, so the
 * larger padding could not land on a card nested inside another card, which is
 * the exact disease item 9 reported. `regular`/`compact` are kept for the
 * screens outside this family that still use them. */
const DENSITY = {
  regular: "mb-5 px-5 py-4",
  compact: "mb-4 px-4 py-3",
  console: "mb-8 px-6 py-5",
} as const;

export function Panel({
  title,
  subtitle,
  action,
  children,
  density = "regular",
  collapsible = false,
  defaultOpen = true,
}: {
  title: string;
  subtitle?: string;
  /** ONE control, top-right of the section header — the Console list pattern
   * (docs/ux-backlog.md, the LIST REFERENCE paragraph: "ONE primary action
   * top-right of the section"). Deliberately singular: a slot that took an
   * array is a slot that grows a toolbar. */
  action?: React.ReactNode;
  children: React.ReactNode;
  density?: keyof typeof DENSITY;
  /** Adds the 펼치기/접기 toggle. The section is still RENDERED when closed is
   * false -- collapsing hides, it never drops. */
  collapsible?: boolean;
  /** Only read on mount. A caller whose default follows the data (severity)
   * remounts this panel with a `key` tied to that data. */
  defaultOpen?: boolean;
}) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(defaultOpen);
  const shown = collapsible ? open : true;

  return (
    <section
      className={`${DENSITY[density]} rounded-xl border border-slate-200 bg-white/[0.03] shadow-[0_8px_18px_rgba(15,23,42,0.06)]`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {/* One notch up from the body text around it (was text-sm, the same
            * size as the paragraphs underneath): with a single 22px h1 on the
            * page and everything else at 10-14px, a panel heading that matched
            * its own body left these screens with effectively two type sizes
            * and no middle. */}
          <h2 className="text-[15px] font-semibold text-slate-900">{title}</h2>
          {subtitle && <p className="mt-1 text-[12px] text-slate-500">{subtitle}</p>}
        </div>
        {action}
        {collapsible && (
          <button
            type="button"
            onClick={() => setOpen((current) => !current)}
            aria-expanded={open}
            className="shrink-0 rounded-md border border-slate-300 bg-white/[0.06] px-2.5 py-1 text-[12px] font-semibold text-slate-700"
          >
            {open ? t("접기", "Collapse", "折りたたむ") : t("펼치기", "Expand", "展開")}
          </button>
        )}
      </div>
      <div className={shown ? "mt-3" : "hidden"}>{children}</div>
    </section>
  );
}

/**
 * ⓘ — the third disclosure primitive these screens needed, added by U-1-quick
 * (docs/ux-backlog.md, the user's detailed elaboration, item #3).
 *
 * `Panel`'s 펼치기/접기 collapses a SECTION and `<details>` collapses a LIST;
 * neither fits a single doctrine SENTENCE that has to stay reachable while it
 * stops competing with the control it explains. The measured problem: five
 * always-visible paragraphs (the map's local-coordinates note, the comparison's
 * scope line, the provisional-status explanation, the excluded-roads note, and
 * a drag/zoom operation hint) sat at full weight beside the things they
 * annotate.
 *
 * THE RULE THIS COMPONENT ENCODES, and the one thing it must not become: a
 * doctrine sentence goes IN here verbatim and is reachable by hover, by click
 * and by keyboard focus; an OPERATION hint (how to use a mouse) does not come
 * here at all, it gets deleted. A tooltip is a promotion for a sentence worth
 * keeping, never a hiding place for one that is not.
 *
 * Reachability, concretely — a tooltip only a mouse can open would delete the
 * sentence for everyone else:
 *   * hover opens it (pointer);
 *   * focus opens it, so TAB reaches it, and the trigger is a real `<button>`
 *     rather than a `title` attribute (which no keyboard can summon);
 *   * click TOGGLES a pinned state, which is the only thing that works on a
 *     touch screen, and Escape closes it;
 *   * the panel carries `role="tooltip"` and the trigger points at it with
 *     `aria-describedby` while it is open, so a screen reader announces the
 *     sentence as the button's description instead of losing it entirely.
 *
 * The trigger draws its own circled "i" rather than printing U+24D8 ⓘ: that
 * glyph is missing from several of the fonts in this app's ko/ja stack and
 * would render as a tofu box on exactly the machines the product is being sold
 * into.
 *
 * It draws the letter as SVG GEOMETRY, not as a text node, and that is a fix
 * rather than a flourish. Reported from live review 2026-08-13: the "i" sat
 * visibly below the circle's centre. The cause is that flex centring centres a
 * text node's LINE BOX, and a line box is symmetric about the baseline —
 * whereas the ink of a lowercase "i" is all above it, with no descender to
 * balance the ascent. So the glyph lands low by roughly half the descender
 * depth, by an amount that changes with whichever font in the ko/ja stack
 * actually resolves. Nudging it with a margin would be tuning a constant
 * against one machine's font. Two shapes in a square viewBox are centred by
 * arithmetic this file controls, in every font, at every size.
 */
export function InfoTip({ text, label }: { text: string; label?: string }) {
  const { t } = useLanguage();
  const tooltipId = useId();
  // Two independent opens, deliberately: `hovering` follows the pointer AND
  // the focus ring (either one shows it, losing either one hides it), while
  // `pinned` is the click/touch latch that survives the pointer leaving. A
  // single boolean would make a tap open-and-immediately-close on a touch
  // device, which is how a tooltip becomes unreachable on a phone.
  const [hovering, setHovering] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hovering || pinned;

  return (
    <span className="relative ml-1 inline-flex align-middle">
      <button
        type="button"
        aria-label={label ?? t("설명 보기", "Show the explanation", "説明を表示")}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        onClick={() => setPinned((current) => !current)}
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
        onFocus={() => setHovering(true)}
        onBlur={() => setHovering(false)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setPinned(false);
            setHovering(false);
          }
        }}
        className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border border-slate-300 bg-white/[0.06] text-slate-500"
      >
        {/* The dot and the stem are placed about the viewBox's own centre
          * (y = 8): dot 2.2→5.4, gap, stem 6.8→13.2, so the ink spans 2.2→13.2
          * and is centred on 7.7 — half a unit high, which reads as centred
          * once the circular border is taken into account. No font metrics
          * involved, so it cannot drift with the ko/ja fallback stack. */}
        <svg viewBox="0 0 16 16" className="h-[11px] w-[11px]" fill="currentColor" aria-hidden="true">
          <circle cx="8" cy="3.8" r="1.6" />
          <rect x="6.9" y="6.8" width="2.2" height="6.4" rx="1.1" />
        </svg>
      </button>
      {open && (
        <span
          id={tooltipId}
          role="tooltip"
          className="absolute left-0 top-6 z-30 w-72 rounded-md border border-slate-300 bg-surface px-3 py-2.5 text-left text-[13px] font-normal leading-relaxed text-slate-600 shadow-[0_16px_34px_rgba(15,23,42,0.28)]"
        >
          {text}
        </span>
      )}
    </span>
  );
}

/**
 * PAGE HELP (unit #20) -- the ROUND ? beside a page's own h1, opening a
 * right-side drawer of static doctrine sections.
 *
 * WHY THIS IS A SEPARATE PRIMITIVE FROM `InfoTip` RATHER THAN A BIGGER ONE.
 * `InfoTip` answers ONE question a control raises, reachable exactly where
 * that control sits, and its content is validation-state or refusal-adjacent
 * -- "what does this word mean", "why is this disabled right now". Unit #20's
 * own instruction draws the line: doctrine, background and "why this was
 * designed this way" belongs one level UP, in a single reading surface a
 * reader opens on purpose rather than trips over beside every control. Five
 * always-open paragraphs became five `InfoTip`s in U-1-quick; this is the next
 * turn of that same screw -- three of THOSE tooltips (this file's own
 * `제외 도로`, `판정 게이트`, `잠정 등록` sentences on the genesis hub) move
 * again, out of a per-control tooltip and into this per-PAGE drawer, because
 * they are not "what just happened" but "why the product is built this way".
 * A control whose disabled reason or validation state a reader needs BEFORE
 * they can act stays an inline `InfoTip` -- moving IT into a drawer would turn
 * a one-glance answer into a two-click one.
 *
 * STATIC ONLY. This component fetches nothing and accepts no async content --
 * every caller hands it a plain `{ title, body }[]` computed from strings
 * already in hand (usually the same `t()` calls that used to sit inline). A
 * page that wants to show something LIVE (a job's own verdict, a measurement)
 * has that already, in its own panels; the drawer is a reference, not a view.
 *
 * REACHABILITY takes the same three inputs `InfoTip` insists on, because nothing
 * about being a drawer instead of a tooltip changes who needs to reach it:
 *   * a real `<button>` trigger (never a `title` attribute, which no keyboard
 *     can summon and no screen reader treats as interactive);
 *   * Escape closes it, from anywhere the drawer has focus;
 *   * a click on the dimmed backdrop closes it too -- the touch/mouse escape
 *     hatch `InfoTip`'s `pinned` toggle exists for, here as a backdrop instead
 *     of a second tap on the trigger, because a drawer (unlike a tooltip) covers
 *     enough of the screen that "tap it again" is not where a reader's hand is.
 * `role="dialog"` + `aria-modal="true"` + a label pointing at the drawer's own
 * heading, so a screen reader announces it as a modal region with a name,
 * exactly the way the close button's `aria-label` names the action that undoes
 * opening it.
 */
export function PageHelp({
  entries,
  title,
}: {
  /** Plain titled sections, in the order they should read. No nesting, no
   * cards-in-cards -- `PageHelp` is a reading surface, the same rule
   * `DrawerSection` states for the generation drawer on app/genesis/page.tsx. */
  entries: Array<{ title: string; body: string }>;
  /** Defaults to 도움말/Help/ヘルプ -- every caller so far wants exactly that
   * word, so a page only overrides it if its drawer is about something
   * narrower than "this whole screen". */
  title?: string;
}) {
  const { t } = useLanguage();
  const headingId = useId();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Escape closes from anywhere inside the drawer; focus returns to the
  // trigger so the reader lands back where they asked the question, the same
  // hand-back `InfoTip`'s own Escape handler performs locally.
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const heading = title ?? t("도움말", "Help", "ヘルプ");

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={t("도움말 열기", "Open help", "ヘルプを開く")}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="ml-2 inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full border border-slate-300 bg-white/[0.06] align-middle text-[12px] font-semibold text-slate-500"
      >
        ?
      </button>
      {open && (
        // The backdrop IS the click-outside handler: it covers everything but
        // the panel, so a pointerdown anywhere except the panel closes it. The
        // panel itself stops the click from reaching the backdrop underneath it.
        <div
          className="fixed inset-0 z-50 flex justify-end bg-slate-950/50"
          onClick={() => setOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={headingId}
            onClick={(event) => event.stopPropagation()}
            className="flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-slate-200 bg-surface px-6 py-6 shadow-[0_16px_48px_rgba(15,23,42,0.35)]"
          >
            <div className="flex items-start justify-between gap-3">
              <h2 id={headingId} className="text-[17px] font-semibold text-slate-900">
                {heading}
              </h2>
              <button
                ref={closeRef}
                type="button"
                onClick={() => setOpen(false)}
                aria-label={t("도움말 닫기", "Close help", "ヘルプを閉じる")}
                className="ui-button-ghost shrink-0 rounded-md px-2 py-0.5 text-[13px]"
              >
                ✕
              </button>
            </div>
            <div className="mt-5 flex flex-col gap-6">
              {entries.map((entry) => (
                <section key={entry.title}>
                  <h3 className="text-[13px] font-semibold text-slate-800">{entry.title}</h3>
                  {/* Mono body -- Console typography, the same voice every
                    * doctrine/reasons block on these screens already speaks in
                    * (refusal reasons, provenance, the fix strip's rationale). */}
                  <p className="mt-1.5 font-mono text-[13px] leading-relaxed text-slate-600">
                    {entry.body}
                  </p>
                </section>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/**
 * MODAL DIALOG (unit #23) -- a CENTERED dialog over a dimmed backdrop, for a
 * task the reader chose to start (새 생성) rather than a reference they opened
 * to check something mid-task. That is the one real difference from
 * `PageHelp` above, and it is why this is a second primitive instead of a
 * `side`/`center` prop on that one: `PageHelp` is inert (static content, no
 * form, safe to leave open while reading elsewhere), while a caller of THIS
 * component owns a form with real state -- closing it does not discard that
 * state (the caller's own `open` flag only hides the tree; see
 * app/genesis/page.tsx's own comment on why the form fields live in the page,
 * not in the modal, for exactly this reason), but the chrome around it needed
 * to say "this is the one thing in front of you right now" rather than "this
 * is a drawer of background reading" -- hence centred-over-a-backdrop instead
 * of PageHelp's right-side slide-in.
 *
 * REACHABILITY is the SAME three guarantees `PageHelp` already insists on,
 * because nothing about being centred instead of a drawer changes who needs
 * to reach it: a real `<button>` close trigger (focused on open, so the very
 * next Tab lands somewhere inside the dialog rather than back on the page
 * behind it), Escape closes from anywhere the dialog has focus, and a
 * pointerdown on the dimmed backdrop closes it too (the backdrop IS the
 * click-outside handler, exactly as `PageHelp`'s is -- the panel stops its own
 * click from reaching the backdrop underneath it). `role="dialog"` +
 * `aria-modal="true"` + `aria-labelledby` pointing at the dialog's own heading
 * complete the same announcement `PageHelp` makes.
 */
export function ModalDialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  const { t } = useLanguage();
  const headingId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 px-4 py-8"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-full w-full max-w-xl flex-col overflow-y-auto rounded-xl border border-slate-200 bg-surface px-6 py-6 shadow-[0_16px_48px_rgba(15,23,42,0.35)]"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id={headingId} className="text-[17px] font-semibold text-slate-900">
            {title}
          </h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label={t("닫기", "Close", "閉じる")}
            className="ui-button-ghost shrink-0 rounded-md px-2 py-0.5 text-[13px]"
          >
            ✕
          </button>
        </div>
        <div className="mt-5">{children}</div>
      </div>
    </div>
  );
}

/**
 * ko/en/ja for the backend's stable refusal codes -- every code any genesis
 * screen can receive, in one table.
 *
 * The `reasons` array is still rendered verbatim by every caller: it carries
 * the endpoint, the file name, the measured metres and the byte counts, and
 * rewriting a backend refusal client-side is how a UI starts lying about why
 * something did not happen. This function translates the CODE and nothing else.
 *
 * `fallback` is for a caller whose unknown-code sentence is more specific than
 * the generic one (the generation form says "생성이 거부되었습니다", because on
 * that screen the request IS a generation). Everything else takes the default.
 */
export function refusalHeadline(code: string | null, t: Translate, fallback?: string): string {
  switch (code) {
    // ---- upload / generation (app/genesis/page.tsx) ----
    case "genesis_upload_not_osm":
      return t(
        "OpenStreetMap 추출 파일(.osm)이 아닙니다",
        "Not an OpenStreetMap extract (.osm)",
        "OpenStreetMap抽出ファイル(.osm)ではありません",
      );
    case "genesis_upload_empty":
      return t("파일이 비어 있습니다", "The file is empty", "ファイルが空です");
    case "genesis_upload_no_filename":
      return t("파일 이름이 없습니다", "The upload carries no file name", "ファイル名がありません");
    case "genesis_bad_name":
      return t(
        "네트워크 이름으로 쓸 수 없는 문자가 있습니다",
        "That network name cannot be used",
        "ネットワーク名に使えない文字があります",
      );
    case "osm_unreadable":
      return t(
        "이 추출 파일에서는 네트워크를 읽을 수 없습니다",
        "No network could be read from this extract",
        "この抽出ファイルからネットワークを読み取れません",
      );
    case "draft_refused":
      return t(
        "생성기가 초안을 거부했습니다",
        "The generator refused to build a draft",
        "生成器が下書きを拒否しました",
      );

    // ---- region extraction (app/genesis/region/page.tsx) ----
    case "region_too_large":
      return t(
        "이 영역은 생성기의 투영 한계를 넘습니다",
        "This region is past the generator's projection limit",
        "この領域は生成器の投影限界を超えています",
      );
    case "region_response_too_large":
      return t(
        "응답이 이 앱의 용량 한계를 넘었습니다 — 잘라내지 않고 중단했습니다",
        "The response passed this app's size limit — it was stopped, never truncated",
        "応答がこのアプリの容量上限を超えました — 切り詰めず中断しました",
      );
    case "region_bbox_invalid":
      return t("영역 좌표가 올바르지 않습니다", "The region coordinates are not valid", "領域の座標が正しくありません");
    case "region_polygon_invalid":
      return t(
        "이 폴리곤으로는 영역을 정할 수 없습니다",
        "This polygon cannot define a region",
        "このポリゴンでは領域を決められません",
      );
    case "region_shape_ambiguous":
      return t(
        "영역 모양은 사각형이나 폴리곤 중 하나여야 합니다",
        "A region must be exactly one shape: a rectangle or a polygon",
        "領域の形は矩形かポリゴンのどちらか一つでなければなりません",
      );
    case "region_has_no_roads":
      return t(
        "이 영역에는 도로가 하나도 없습니다",
        "There is no road in this region at all",
        "この領域には道路が一つもありません",
      );
    case "overpass_rate_limited":
      return t(
        "Overpass가 요청 빈도를 제한했습니다 (429) — 재시도하지 않았습니다",
        "Overpass rate-limited us (429) — nothing was retried",
        "Overpass がリクエスト頻度を制限しました (429) — 再試行していません",
      );
    case "overpass_unavailable":
      return t("Overpass 인스턴스가 응답하지 않았습니다", "The Overpass instance did not answer", "Overpass インスタンスが応答しませんでした");
    case "overpass_timeout":
    case "overpass_unreachable":
      return t("Overpass에 닿지 못했습니다", "Overpass could not be reached", "Overpass に到達できませんでした");
    case "overpass_query_failed":
      return t(
        "Overpass가 200을 주었지만 문서 안에서 실패를 보고했습니다",
        "Overpass answered 200 but reported a failure inside the document",
        "Overpass は 200 を返しましたが、文書内で失敗を報告しました",
      );
    case "overpass_bad_response":
      return t(
        "OpenStreetMap 문서가 아닌 응답이 왔습니다",
        "What came back is not an OpenStreetMap document",
        "OpenStreetMap 文書ではない応答が返りました",
      );
    case "overpass_endpoint_misconfigured":
      return t("Overpass 주소 설정이 잘못되었습니다", "The Overpass endpoint is misconfigured", "Overpass のアドレス設定が誤っています");
    case "region_bad_name":
      return t("추출 이름으로 쓸 수 없는 문자가 있습니다", "That extract name cannot be used", "抽出名に使えない文字があります");
    case "interrupted_by_restart":
      return t(
        "서버가 재시작되어 이 요청은 끝나지 못했습니다",
        "The server restarted before this request finished",
        "サーバーが再起動したため、この要求は完了しませんでした",
      );

    // ---- 현시도 review (app/genesis/phases/page.tsx) ----
    case "phase_diagram_attestation_stale":
      return t(
        "확인 이후 도면 또는 시트가 바뀌었습니다 — 재확인이 필요합니다",
        "The drawing or the sheet changed after this confirmation — re-confirm",
        "確認後に図面またはシートが変更されました — 再確認が必要です",
      );
    case "phase_diagram_not_confirmed":
      return t(
        "아직 확인되지 않은 교차로가 있습니다",
        "An intersection has not been confirmed yet",
        "未確認の交差点があります",
      );
    case "phase_diagram_unknown_review":
      return t("해당 검토가 없습니다", "No such review", "該当のレビューがありません");
    case "phase_diagram_duplicate_node":
      return t(
        "같은 노드에 두 개의 확인이 있습니다",
        "Two confirmations name the same node",
        "同じノードに確認が二つあります",
      );

    // ---- demand side input (app/genesis/demand/page.tsx) ----
    case "genesis_counts_not_workbook":
      return t(
        "Excel(.xlsx) 카운트 워크북이 아닙니다",
        "Not an Excel (.xlsx) counts workbook",
        "Excel(.xlsx)のカウントワークブックではありません",
      );
    case "genesis_counts_empty":
      return t("파일이 비어 있습니다", "The file is empty", "ファイルが空です");
    case "genesis_counts_unreadable":
      return t(
        "이 워크북을 읽을 수 없습니다",
        "This workbook could not be read",
        "このワークブックを読み取れません",
      );
    case "genesis_demand_map_names_unknown_site":
      return t(
        "매핑이 카운트에 없는 지점을 지정했습니다",
        "The mapping names a site the counts do not contain",
        "対応表がカウントに存在しない地点を指定しています",
      );
    case "genesis_demand_map_names_unknown_approach":
      return t(
        "매핑이 카운트에 없는 접근로를 지정했습니다",
        "The mapping names an approach the counts do not contain",
        "対応表がカウントに存在しない流入路を指定しています",
      );

    // ---- fix approval (app/genesis/page.tsx, roadmap B5-2 stage 4) ----
    // Two layers, deliberately redundant (routers/genesis.py's own
    // apply_genesis_fix docstring) -- both can be the code on a refused
    // POST .../fixes/apply, and both need a headline here.
    case "genesis_fix_unknown_finding":
      return t(
        "이 발견 코드에는 등록된 수정이 없습니다",
        "No registered fix exists for this finding code",
        "この発見コードには登録された修正がありません",
      );
    case "genesis_fix_verb_mismatch":
      return t(
        "이 발견이 제안하는 동사와 다릅니다",
        "That is not the verb this finding's own mapping names",
        "この発見が提案する動詞と異なります",
      );
    case "genesis_fix_parent_not_refused":
      return t(
        "이 생성은 거부 상태가 아닙니다 — 수정은 거부된 생성에만 적용됩니다",
        "This generation is not refused -- a fix only applies to a refused generation",
        "この生成は拒否状態ではありません — 修正は拒否された生成にのみ適用されます",
      );
    case "genesis_fix_finding_not_in_parent_verdict":
      return t(
        "이 발견 코드는 이 생성의 판정에 없습니다",
        "This finding code does not appear in this generation's own verdict",
        "この発見コードはこの生成の判定に存在しません",
      );
    case "genesis_fix_params_target_mismatch":
      return t(
        "제출된 값이 이 발견이 지목한 대상과 다릅니다",
        "The submitted values address a different object than this finding names",
        "送信された値はこの発見が指し示す対象と異なります",
      );
    case "genesis_fix_measurement_gate_not_satisfied":
      return t(
        "실제 근거를 다시 확인한 결과, 이 승인은 더 이상 유효하지 않습니다",
        "Re-checked against real evidence, this approval no longer holds",
        "実際の根拠を再確認した結果、この承認はもはや有効ではありません",
      );
    case "genesis_fix_verb_no_longer_registered":
      return t(
        "이 제안은 그 사이 낡아졌습니다 — 동사 또는 값이 더 이상 등록되어 있지 않습니다",
        "This proposal has gone stale since it was made -- the verb or its params no longer validate",
        "この提案はその後古くなりました — 動詞または値がもはや登録されていません",
      );
    case "genesis_fix_no_gate_verdict":
      return t(
        "이 생성은 아직 게이트를 거치지 않았습니다",
        "This generation has not been gated yet",
        "この生成はまだゲートを通過していません",
      );

    default:
      return fallback ?? t("요청이 거부되었습니다", "The request was refused", "要求が拒否されました");
  }
}

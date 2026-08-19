"use client";

// Roadmap v2 §5.B row B2 — the region picker becomes real.
//
// Row 0.4 shipped `docs/product/mockups/region-selection-mockup.html`: a lasso,
// per-road toggles and block exclusion over a fake Daejeon grid. It was a design
// artifact wired to nothing. This page is the part of it that is REAL — a region
// in, an `.osm` staged, and the same upload token B1's generation flow already
// takes.
//
// WHAT SHIPPED AND WHAT DID NOT, stated rather than implied:
//   * shipped — a bounding box AND the mockup's polygon lasso, each measured
//     against the cap BEFORE anything is downloaded, fetched off the request
//     thread, and handed to `startGenesisJob` as the SAME token an uploaded file
//     produces;
//   * shipped, and since the polygon-native trim the lasso is judged as the shape
//     it is: Overpass is asked for the ring (`poly:`), the way-tail trim CUTS at
//     the ring, and a terminal it synthesizes there is the model boundary. What
//     remains rectangular is the residual metre band and the size estimate, both
//     measured on the ring's bounding box — drawn on the map as a faint hairline,
//     said in words beside it, and written into the artifact's compliance report.
//     This block used to describe the opposite as a NAMED gap, and that gap was
//     real until it was closed; the sentence is replaced rather than softened,
//     because a disclosure kept past its own fix misleads exactly as much as a
//     missing one;
//   * deferred, deliberately — the per-road include/exclude toggles and the
//     block-exclusion "shared wall" rule. Both act on the geometry of a network
//     that does not exist until after an extract has been fetched and drafted, so
//     they sit on top of this step rather than replacing it. (The lasso did not
//     need that geometry, which is why it is no longer on this list.)
//
// THE ONE RULE THIS PAGE MUST NOT BREAK: it never starts a generation of its
// own. A fetch ends at the staging door and the button below calls
// `startGenesisJob` — B1's route, the gate its only exit. There is no
// "fetch and publish" path here because a second road into the gate is what
// §5.A A4 exists to prevent.
//
// Chrome strings are ko primary through the three-argument t() form (explicit
// ja), like the genesis page beside it. Every NUMBER on screen comes from the
// backend's own measurement — this file computes no limit of its own, because a
// cap the browser recomputed could disagree with the cap the server enforces.
//
// ---------------------------------------------------------------------------
// U-1 POLISH (2026-08-13) — six changes, all from the user's own live testing
// of this screen, recorded in docs/ux-backlog.md under "REGION-PAGE ADDENDA":
//   1. the tinted-box contrast rule (stated in full above `statusClasses`);
//   2. `RegionGauge` renders in RECTANGLE MODE ONLY — see its doc comment;
//   3. a Console-style density pass: one layer per card, the measurement as
//      stat blocks (small muted label, large value) instead of four stacked
//      sentences, and NOTHING below 12px anywhere on the screen;
//   4. POLYGON IS THE DEFAULT MODE, rectangle the secondary toggle. With zero
//      corners `parsed` is null, so no preview request is fired and no
//      measurement exists to render — DEFAULT_BBOX seeds the rectangle FIELDS
//      and the map's opening view, never a measurement;
//   5. the 추출 이력 list is gone (see the note where it used to be);
//   6. the "이 화면에 일부러 넣지 않은 것" disclosure is gone (same note).
// The FLOW is untouched: same states, same requests, same hand-off.
// ---------------------------------------------------------------------------
//
// UNIT #20 (2026-08-14) — four live-tested changes on top of U-1, all from the
// user's own instructions after driving this exact screen:
//   1. THE MODE TOGGLE DIES. This page was "rectangle or lasso, pick one"; it
//      is now lasso-only. `RegionMode`, the rectangle input fields, `parseBox`
//      and `RegionGauge` (which existed ONLY for rectangle mode, per its own
//      doc comment) are gone with it. The backend keeps accepting a rectangle
//      shape unchanged — other callers and the contract's own API-binding test
//      still exercise it — this file simply never sends one.
//   2. THE LIMIT BECOMES THE BUTTON'S JOB. The measurement's stat grid, its
//      한계 안/한계 초과 chip and its "자세한 근거" detail all come off the
//      screen. The fetch behind them is untouched (the button still needs it);
//      what changed is that the button now CARRIES the limit itself —
//      disabled when over, and a title on its wrapping `<span>` (the
//      established pattern for a disabled button's hover text) states which
//      numbers put it there, read straight from the same measurement payload.
//   3. The always-visible 경계 처리 ⓘ is hidden; its sentences (verbatim,
//      still pinned) move into the page help drawer below.
//   4. THE VERTEX LIST BECOMES A TABLE. The read-only dotted coordinate
//      enumeration is replaced by `VertexRow` — index, an editable lat input,
//      an editable lon input, a delete button. `vertices` (this page's own
//      state) stays the single source of truth: a row's own text is a DRAFT
//      that only ever reaches `setVertices` after passing a ±90°/±180° range
//      check, so an invalid keystroke marks itself (mono ring, a message row)
//      and never reaches the map or a submitted payload. See `VertexRow`'s own
//      doc comment for the exact binding, because this is the one place an
//      adversarial edit — reentrancy, NaN, editing mid-close — could corrupt
//      the ring silently.

import Link from "next/link";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { useLanguage } from "@/components/LanguageProvider";
import { Panel, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  RegionFetchError,
  fetchRegionFetch,
  fetchRegionFetches,
  fetchRegionLimits,
  getWorkspaceId,
  previewRegion,
  startGenesisJob,
  startRegionFetch,
  type RegionFetch,
  type RegionFetchStatus,
  type RegionMeasurement,
  type RegionShape,
} from "@/lib/api";
import {
  BASIS_BOUNDARY_CUT,
  takeRegionPrefill,
  type RegionPrefill,
} from "@/lib/region-prefill";

/** Leaflet reads `window` at module scope, so the drawing surface is mounted
 * client-only (`01-app/02-guides/lazy-loading.md`), the same way
 * `app/dashboard/page.tsx` mounts `DashboardMap`. There is nothing to prerender
 * for a map, and the component itself also defers `import("leaflet")` into its
 * mount effect. */
const RegionLassoMap = dynamic(() => import("@/components/genesis/RegionLassoMap"), {
  ssr: false,
});

type Translate = (ko: string, en: string, ja: string) => string;

/** A running fetch is re-read at this interval. An Overpass call is seconds,
 * occasionally a minute when the instance is busy — the backend's socket budget
 * is a minute of query plus the transfer — so a one-second poll is both cheap
 * and fast enough to feel live. Same number the genesis page uses. */
const POLL_INTERVAL_MS = 1000;

/** How long the box sits still before the server is asked to measure it. Long
 * enough that typing a latitude does not fire six requests, short enough that
 * the area readout feels attached to the field. */
const PREVIEW_DEBOUNCE_MS = 350;

const TERMINAL: RegionFetchStatus[] = ["staged", "refused", "error"];

function isTerminal(status: RegionFetchStatus): boolean {
  return TERMINAL.includes(status);
}

/** A default that is somewhere real rather than (0, 0): Daejeon 둔산, this
 * product's first market. Small enough to be inside every cap. Unit #20: with
 * the rectangle mode gone this is now used for exactly one thing — the map's
 * opening centre (`DEFAULT_CENTRE`, below) and the one-off `GET
 * /region/limits` lookup that reads the corner cap before anything is drawn. */
const DEFAULT_BBOX = {
  south: "36.3444",
  west: "127.3765",
  north: "36.3564",
  east: "127.3925",
};

/** Where the map looks before anything is drawn: the same 둔산 default the
 * rectangle fields used to carry, computed once from `DEFAULT_BBOX`. */
const DEFAULT_CENTRE: [number, number] = [
  (Number(DEFAULT_BBOX.south) + Number(DEFAULT_BBOX.north)) / 2,
  (Number(DEFAULT_BBOX.west) + Number(DEFAULT_BBOX.east)) / 2,
];

function statusLabel(status: RegionFetchStatus, t: Translate): string {
  switch (status) {
    case "queued":
      return t("대기 중", "Queued", "待機中");
    case "running":
      return t("내려받는 중", "Downloading", "ダウンロード中");
    case "staged":
      return t("추출 완료 — 생성 준비됨", "EXTRACTED — ready to generate", "抽出完了 — 生成の準備ができました");
    case "refused":
      return t("거부됨 — 아무것도 받지 않았습니다", "REFUSED — nothing was downloaded", "拒否 — 何も取得していません");
    case "error":
      return t("실패", "Failed", "失敗");
    default:
      return status;
  }
}

// ---------------------------------------------------------------------------
// THE TINTED-BOX RULE (U-1 polish, 2026-08-13 — reported by the user from live
// use of this screen: the sky info box below the lasso map was blue-on-blue,
// the same disease as the gate's green-on-green pass cells)
//
// A tinted box states its tint with its BACKGROUND and its BORDER. Its TEXT
// comes from the neutral ink ramp — `text-slate-800` for the box's own voice,
// `text-slate-600/700` for a secondary line inside it — and NEVER from the tint
// family.
//
// WHY THIS IS A RULE AND NOT A JUDGEMENT CALL: globals.css's `@theme` block
// remaps only the Tailwind shades this app was actually using when the dark
// remap was written — sky 50/200/400/500/600, emerald 50–800, blue 50–800,
// rose 50–900, amber 50–900, slate 50–950. A `text-*` shade OUTSIDE that list
// keeps Tailwind's stock LIGHT-mode value and then lands on a DARK fill of its
// own family: `text-sky-900` is #0c4a6e on a `bg-sky-50` remapped to #032042,
// and `text-emerald-900` is #064e3b on a `bg-emerald-50` remapped to #1e2b22.
// Both are dark-on-dark. Allow-listing shade by shade would mean re-reading
// globals.css every time a box is added, so the rule is family-wide and has one
// sentence: the tint is a FILL, never an ink.
//
// `border-sky-300` is the same fall-through in a border — stock #7dd3fc, a
// bright cyan hairline nothing else in this app draws — so it becomes
// `border-sky-200`, a shade the remap does cover.
//
// ---------------------------------------------------------------------------
// AND THE THREE-COLOUR DISCIPLINE (user decision, 2026-08-13) — which settles
// the amber question this repo has carried open since the palette lock.
//
// The genesis screens have exactly three accent hues:
//     rose    = fail / refuse            (a proven fault)
//     emerald = pass / success
//     sky     = selection / info / link
// There is no fourth. AMBER IS GONE from these screens, and everything that
// used it — 확인 불가 / not_checkable, warn-tier findings, 재확인 필요,
// rests-on-attestation, provisional chips, every amber notice box — renders
// MONO instead: the neutral ramp with ink-strength text, a heavier
// `border-slate-400` hairline, and a `ring-1` on the full-width notices.
// Distinct by WEIGHT and SHAPE, never by a fourth hue.
//
// WHY THIS DOES NOT WEAKEN THE DOCTRINE, which is the only question that
// mattered: 확인 불가 must never read as "fine". A mono chip with a heavy ring
// sitting beside a green pass chip is visibly NOT the green state, and the
// WORD is printed on it either way (확인 불가 / 재확인 필요 / 거부됨). What is
// dropped is a hue; no claim, no state and no sentence goes with it. The
// palette lock's own note recorded the opposite risk — globals.css remaps the
// amber family to NEUTRAL anyway (`--color-amber-*` are white-alphas and the
// ivory ramp), so on the dark theme "amber" was already grey while pretending
// to be a colour. Naming the grey is more honest than inheriting it.
//
// WHAT THE SWEEP COSTS, named rather than glossed: `refused` used to be amber
// and `error` rose, which drew this page's own doctrine — a refusal is a
// decision with a reason, never a crash — as two different colours. Under three
// hues both are rose, because rose IS refuse. The distinction did not go with
// the colour: `statusLabel` still says 거부됨 — 아무것도 받지 않았습니다 for one
// and 실패 for the other, and only the refused branch renders the backend's
// `refusal.reasons` verbatim underneath. A reader loses a glance, not a fact.
//
// Both rules are swept across all three of this unit's screens (region, demand,
// phases) and pinned by the contract test "U-1: three accent colours, tint in
// the fill, ink in the text".
// ---------------------------------------------------------------------------

function statusClasses(status: RegionFetchStatus): string {
  switch (status) {
    case "staged":
      return "border-emerald-300 bg-emerald-50 text-slate-800";
    case "refused":
      return "border-rose-300 bg-rose-50 text-slate-800";
    case "error":
      return "border-rose-300 bg-rose-50 text-slate-800";
    default:
      return "border-sky-200 bg-sky-50 text-slate-800";
  }
}

// `refusalHeadline` (ko/en/ja for the backend's stable refusal codes, this
// page's `region_*`/`overpass_*` half included) now lives in
// components/genesis/ui.tsx: four genesis screens each carried their own copy
// switching on a different slice of one globally-unique code space. The rule it
// was written under has not changed and is restated there -- the `reasons` array
// is still rendered verbatim underneath every headline, because it carries the
// endpoint, the measured metres and the byte counts, and rewriting a backend
// refusal client-side is how a UI starts lying about why something did not
// happen.

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimestamp(iso: string | null, lang: "ko" | "en" | "ja"): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const locale = lang === "ko" ? "ko-KR" : lang === "ja" ? "ja-JP" : "en-US";
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

// ---------------------------------------------------------------------------
// The editable vertex table (unit #20)
// ---------------------------------------------------------------------------

/** ±90° / ±180° -- the only two facts a lat/lon pair has to satisfy to be a
 * point Leaflet, Overpass and the server's own polygon parser can all agree
 * on. Checked HERE, before a value ever reaches `setVertices`: a NaN handed to
 * Leaflet throws inside the tile layer, and an out-of-range degree handed to
 * the server is a round trip this screen can refuse for free instead. */
function isValidLatitude(value: number): boolean {
  return Number.isFinite(value) && value >= -90 && value <= 90;
}
function isValidLongitude(value: number): boolean {
  return Number.isFinite(value) && value >= -180 && value <= 180;
}

/**
 * One row of the editable vertex table -- index, a lat input, a lon input, a
 * delete button.
 *
 * THE BINDING, stated exactly because it is the part an adversarial read of
 * this file has to be able to check: the row's OWN state (`latText`/`lonText`)
 * is a DRAFT. Every keystroke updates the draft (so an in-progress "-" or
 * "36." stays on screen instead of snapping back on the next render), and
 * ALSO calls `onCommit` -- but only when BOTH fields parse to a finite number
 * inside range. An invalid draft therefore never becomes a `setVertices`
 * call: the page's `vertices` array, and through it the map and the eventual
 * `startRegionFetch` payload, only ever see a value this function has
 * already range-checked.
 *
 * THE RESYNC, and the bug it was written to close: this component is keyed by
 * INDEX (see the caller), so deleting a row out of the middle of the table
 * does not remount every row after it -- React matches the same key to the
 * same component instance and hands it new `lat`/`lon` props, shifted down by
 * one. Without the effect below, the row now sitting at that index would go
 * on showing the TEXT the row that used to occupy it had typed, silently
 * disagreeing with what `vertices` (and the map) now hold at that position --
 * a table that lies about its own state is worse than no table. The effect
 * resyncs the draft whenever the vertex THIS row is bound to actually changes
 * -- a delete elsewhere, or this row's own valid commit landing back through
 * props -- and it does NOT fire while a keystroke is merely invalid, because
 * an invalid draft never reaches `onCommit` and so `lat`/`lon` never change;
 * that is exactly the case that must keep the half-typed text on screen.
 */
function VertexRow({
  index,
  lat,
  lon,
  onCommit,
  onDelete,
  t,
}: {
  index: number;
  lat: number;
  lon: number;
  onCommit: (index: number, lat: number, lon: number) => void;
  onDelete: (index: number) => void;
  t: Translate;
}) {
  const [latText, setLatText] = useState(() => String(lat));
  const [lonText, setLonText] = useState(() => String(lon));

  // ADVERSARIAL FIX, found in this unit's own self-review: `key={index}` (see
  // the caller) means deleting a row from the MIDDLE of the table does not
  // remount every row after it -- React matches the same key to the same
  // component instance and just hands it new `lat`/`lon` props, so without
  // this effect the row now sitting at that index would keep showing the
  // TEXT the row that used to live there had typed, while `vertices` itself
  // already held the correct, shifted value. This resyncs the draft whenever
  // the vertex THIS row is bound to actually changes underneath it -- a
  // delete elsewhere, or this row's own valid commit landing back through
  // props. It does NOT fire while a keystroke is merely invalid (an invalid
  // draft never reaches `onCommit`, so `lat`/`lon` never change), which is
  // exactly the case that must keep the half-typed text on screen.
  useEffect(() => {
    setLatText(String(lat));
    setLonText(String(lon));
  }, [lat, lon]);

  const latOk = latText.trim() !== "" && isValidLatitude(Number(latText));
  const lonOk = lonText.trim() !== "" && isValidLongitude(Number(lonText));

  const commit = (nextLatText: string, nextLonText: string) => {
    const nextLat = Number(nextLatText);
    const nextLon = Number(nextLonText);
    const nextLatOk = nextLatText.trim() !== "" && isValidLatitude(nextLat);
    const nextLonOk = nextLonText.trim() !== "" && isValidLongitude(nextLon);
    // Never feed NaN, or a value outside the range Leaflet/Overpass/the
    // server can all agree is a coordinate, to the map or the payload -- an
    // invalid row simply stops here and marks itself below.
    if (nextLatOk && nextLonOk) onCommit(index, nextLat, nextLon);
  };

  const cellClasses = (ok: boolean) =>
    `w-full rounded border px-1.5 py-1 font-mono text-[14px] text-slate-800 ${
      ok ? "border-slate-300" : "border-slate-400 ring-1 ring-slate-400"
    }`;

  return (
    <>
      <tr className="border-t border-slate-100">
        <td className="py-1 pl-2 pr-2 font-mono text-[14px] text-slate-500">{index + 1}</td>
        <td className="py-1 pr-2">
          <input
            type="text"
            inputMode="decimal"
            value={latText}
            onChange={(event) => {
              const next = event.target.value;
              setLatText(next);
              commit(next, lonText);
            }}
            aria-invalid={!latOk}
            aria-label={t(`점 ${index + 1} 위도`, `Point ${index + 1} latitude`, `点 ${index + 1} 緯度`)}
            className={cellClasses(latOk)}
          />
        </td>
        <td className="py-1 pr-2">
          <input
            type="text"
            inputMode="decimal"
            value={lonText}
            onChange={(event) => {
              const next = event.target.value;
              setLonText(next);
              commit(latText, next);
            }}
            aria-invalid={!lonOk}
            aria-label={t(`점 ${index + 1} 경도`, `Point ${index + 1} longitude`, `点 ${index + 1} 経度`)}
            className={cellClasses(lonOk)}
          />
        </td>
        <td className="py-1 pr-2 text-right">
          <button
            type="button"
            onClick={() => onDelete(index)}
            className="ui-button-ghost rounded px-2 py-0.5 text-[14px] font-semibold"
          >
            {t("삭제", "Delete", "削除")}
          </button>
        </td>
      </tr>
      {(!latOk || !lonOk) && (
        <tr>
          <td colSpan={4} className="pb-1.5 pl-6 pr-2 text-[14px] leading-snug text-slate-600">
            {t(
              "위도는 -90~90, 경도는 -180~180 사이의 숫자여야 합니다 — 고치기 전까지 이 점은 지도에 반영되지 않습니다.",
              "Latitude must be between -90 and 90, longitude between -180 and 180 — this point will not update on the map until it is fixed.",
              "緯度は-90〜90、経度は-180〜180の数値である必要があります — 修正するまでこの点は地図に反映されません。",
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GenesisRegionPage() {
  const { lang, t } = useLanguage();
  const router = useRouter();

  // UNIT #20: the mode toggle is gone -- this page is lasso-only, so `vertices`
  // is now the ONLY shape state it carries (no `box`/`RegionMode` beside it).
  //
  // THE INITIAL STATE, traced rather than assumed, because opening straight on
  // an empty ring is only safe if the empty ring is genuinely empty:
  //   * `vertices` starts [], so `parsed` is null (a ring needs three corners);
  //   * null `parsed` makes the measure effect RETURN BEFORE the debounce timer
  //     is ever set, so `previewRegion` is not called on load — the screen opens
  //     with no request in flight;
  //   * `measurement` therefore stays null, so no measurement renders at all;
  //   * `readyToFetch` needs `measurement?.within_region_cap`, so the extract
  //     button is disabled until a real ring has been measured by the server.
  // The page holds no URL state (no useSearchParams, no router.replace), so a
  // deep link or a browser Back lands on this same initial state rather than on
  // a half-restored one.
  const [vertices, setVertices] = useState<Array<[number, number]>>([]);
  const [ringClosed, setRingClosed] = useState(false);
  // UNIT #40: the ONE way this page can open on a shape it did not draw -- a
  // V4 refusal whose runway shortage the backend attributed to this very
  // boundary, handed over with the region that would close it. Read ONCE from
  // sessionStorage in the mount effect below (see lib/region-prefill.ts for
  // why not a URL parameter), which keeps every promise the initial-state note
  // above makes: the store is empty on a refresh, so a reload of this page
  // lands on the same empty ring a first visit does.
  const [prefill, setPrefill] = useState<RegionPrefill | null>(null);
  const [name, setName] = useState("");
  const [measurement, setMeasurement] = useState<RegionMeasurement | null>(null);
  const [measureError, setMeasureError] = useState<string | null>(null);
  // The corner cap, READ from `GET /region/limits` rather than written here. Same
  // rule as every other number on this page: a limit the browser carried its own
  // copy of is a limit that can disagree with the one that actually refuses.
  const [maxVertices, setMaxVertices] = useState<number | null>(null);

  // The staged-extract listing. Its LIST UI was removed on 2026-08-13 (the note
  // at the bottom of this file carries the user's reason), and the plumbing was
  // deliberately left standing: `loadFetches` still calls the endpoint, still
  // selects the newest fetch, and this state still holds what came back, so the
  // master-detail rebuild the backlog schedules under U-1-flow has it without a
  // second round trip through the backend. `fetches` is therefore written and
  // not yet read — that is the removal, not an oversight.
  const [fetches, setFetches] = useState<RegionFetch[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  // Stored WITH the id it was fetched for and read back only while that id still
  // matches, so a selection change can never show the previous fetch's
  // provenance under the new fetch's heading. Same shape the genesis page uses.
  const [selectedEntry, setSelectedEntry] = useState<{ id: string; record: RegionFetch } | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<GenesisError | Error | null>(null);
  const [handingOff, setHandingOff] = useState(false);
  const [handOffError, setHandOffError] = useState<GenesisError | Error | null>(null);

  const selected = selectedEntry && selectedEntry.id === selectedId ? selectedEntry.record : null;
  // ONE shape: the polygon the lasso drew. This is the SAME object the preview
  // measures and the fetch is started with, so the two can never describe
  // different regions.
  const parsed = useMemo<RegionShape | null>(() => {
    return vertices.length >= 3 ? { polygon: vertices } : null;
  }, [vertices]);
  const overVertexCap = maxVertices !== null && vertices.length > maxVertices;
  const readyToFetch = Boolean(
    parsed && measurement?.within_region_cap && !overVertexCap && !submitting,
  );

  // UNIT #20 -- "the limit becomes the button's job". Non-null EXACTLY when the
  // button is disabled for the limit specifically (a real measurement exists
  // and says over-cap) -- never for "fewer than three corners yet" or
  // "already submitting", so the wrapping <span> below only carries a `title`
  // when there is a limit-shaped reason to explain. Every number in it is read
  // straight off the server's own measurement, never recomputed here.
  const overLimitReason = useMemo(() => {
    if (!measurement || measurement.within_region_cap) return null;
    return t(
      `반경 ${measurement.half_extent_m.toFixed(0)} m가 한계 ${measurement.validity_radius_m.toFixed(0)} m를 초과합니다 (넓이 ${measurement.area_km2.toFixed(2)} km² / 정사각형 기준 한계 ${measurement.max_area_km2.toFixed(1)} km²).`,
      `Half-extent ${measurement.half_extent_m.toFixed(0)} m exceeds the ${measurement.validity_radius_m.toFixed(0)} m limit (area ${measurement.area_km2.toFixed(2)} km², vs a ${measurement.max_area_km2.toFixed(1)} km² square-equivalent limit).`,
      `半径 ${measurement.half_extent_m.toFixed(0)} m が上限 ${measurement.validity_radius_m.toFixed(0)} m を超えています（面積 ${measurement.area_km2.toFixed(2)} km² / 正方形換算の上限 ${measurement.max_area_km2.toFixed(1)} km²）。`,
    );
  }, [measurement, t]);

  const addVertex = useCallback((lat: number, lon: number) => {
    setVertices((current) => [...current, [lat, lon]]);
    setRingClosed(false);
  }, []);

  // The table's two-way half of the binding: a row calls this ONLY after its
  // own range check passes (see `VertexRow`'s doc comment), so this function
  // itself does no further validation -- it is the single place `vertices`
  // actually changes for an edit, which is what keeps the map, the table and
  // the eventual payload all reading the same array.
  const updateVertex = useCallback((index: number, lat: number, lon: number) => {
    setVertices((current) => current.map((vertex, i) => (i === index ? [lat, lon] : vertex)));
  }, []);

  const deleteVertex = useCallback((index: number) => {
    setVertices((current) => current.filter((_, i) => i !== index));
  }, []);

  const resetRing = useCallback(() => {
    setVertices([]);
    setRingClosed(false);
  }, []);

  // ADVERSARIAL GUARD (unit #20): a delete (or, in principle, any future path)
  // that drops the ring below three corners while `ringClosed` is still true
  // would corrupt the map two ways at once -- `RegionLassoMap` draws a filled
  // `L.polygon` for two points instead of the open polyline it should, AND its
  // own click handler refuses every further click while `closed` is true, so
  // the ring could never be re-opened by drawing. This effect is the single
  // place that keeps "closed" meaning "a real ring", regardless of which path
  // shrank `vertices` under three.
  useEffect(() => {
    if (vertices.length < 3 && ringClosed) setRingClosed(false);
  }, [vertices.length, ringClosed]);

  // UNIT #40 -- the pre-fill, consumed exactly once. `takeRegionPrefill` reads
  // AND removes, and the effect runs on mount only, so the proposed shape can
  // never re-appear over a ring the user has since edited. The vertices land in
  // this page's OWN state, which means everything downstream of them is
  // unchanged: the map draws them, the table edits them, the server measures
  // them, and the 추출 button below is still the only thing that fetches
  // anything. A stored shape that does not validate is dropped silently -- the
  // page then simply opens empty, which is its documented initial state. So is
  // a shape stamped with ANOTHER 프로젝트: `getWorkspaceId()` is the project
  // every request from this page is scoped to, and pre-filling this project's
  // extract with a polygon derived in a different one is exactly the confusion
  // the stamp exists to prevent (see lib/region-prefill.ts).
  useEffect(() => {
    const handed = takeRegionPrefill(getWorkspaceId());
    if (!handed) return;
    setPrefill(handed);
    setVertices(handed.polygon);
    setRingClosed(true);
  }, []);

  const loadFetches = useCallback(async () => {
    try {
      const entries = await fetchRegionFetches();
      setFetches(entries);
      setListError(null);
      setSelectedId((current) => current || entries[0]?.id || "");
    } catch (caught) {
      setListError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void loadFetches();
  }, [loadFetches]);

  // The corner cap comes from the server's own limits call. A failure here is not
  // an error the user has to see: the cap is enforced by the fetch either way, so
  // the UI simply stops disabling clicks for it and the refusal arrives with the
  // server's own wording.
  useEffect(() => {
    let cancelled = false;
    fetchRegionLimits(DEFAULT_CENTRE[0], DEFAULT_CENTRE[1])
      .then((limits) => {
        // CHECKED rather than trusted, and the check earned itself: measured
        // against a backend older than this field, the raw value landed on screen
        // as "점 0 / undefined". A limit the UI cannot state is a limit the UI
        // must not mention -- `null` keeps the counter honest and lets the
        // server's own refusal do the talking.
        const cap = Number(limits.max_polygon_vertices);
        if (!cancelled) setMaxVertices(Number.isFinite(cap) && cap > 0 ? cap : null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Measure the polygon on the SERVER, debounced. The browser deliberately
  // computes no limit of its own: the cap is the draft projection's validity
  // radius and it is latitude-dependent, so a second implementation here could
  // disagree with the one that actually refuses.
  useEffect(() => {
    if (!parsed) {
      setMeasurement(null);
      setMeasureError(
        t(
          "지도를 눌러 점을 3개 이상 찍고, 두 번 눌러 영역을 닫습니다.",
          "Click at least three points on the map, then double-click to close the shape.",
          "地図をクリックして3点以上を打ち、ダブルクリックで領域を閉じます。",
        ),
      );
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      previewRegion(parsed)
        .then((payload) => {
          if (cancelled) return;
          setMeasurement(payload);
          setMeasureError(null);
        })
        .catch((caught) => {
          if (cancelled) return;
          setMeasurement(null);
          setMeasureError(caught instanceof Error ? caught.message : String(caught));
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [parsed, t]);

  // Poll the selected fetch while it is still running.
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const record = await fetchRegionFetch(selectedId);
        if (cancelled) return;
        setSelectedEntry({ id: selectedId, record });
        if (!isTerminal(record.status)) {
          timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
        } else {
          void loadFetches();
        }
      } catch (caught) {
        if (!cancelled) setListError(caught instanceof Error ? caught.message : String(caught));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedId, loadFetches]);

  const submit = useCallback(async () => {
    if (!parsed) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      // The SAME `parsed` the preview measured, spread whole -- the drawn ring,
      // never a re-derivation of it.
      const started = await startRegionFetch({ ...parsed, name: name.trim() || undefined });
      setSelectedId(started.id);
      setSelectedEntry({ id: started.id, record: started });
      void loadFetches();
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setSubmitting(false);
    }
  }, [parsed, name, loadFetches]);

  // THE hand-off. The staged extract goes to `POST /api/genesis/jobs` with the
  // token the fetch produced -- byte for byte the road an uploaded .osm takes,
  // gate included. Nothing on this page publishes anything.
  const handOff = useCallback(async () => {
    if (!selected?.upload_token) return;
    setHandingOff(true);
    setHandOffError(null);
    try {
      await startGenesisJob({
        upload_token: selected.upload_token,
        name: selected.name,
      });
      // No job id in the URL: the genesis page selects the newest generation on
      // load, and the one just started IS the newest. Threading an id through a
      // query parameter would buy nothing and would need a Suspense boundary
      // for `useSearchParams` on a page that has no other reason for one.
      router.push("/genesis");
    } catch (caught) {
      setHandOffError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setHandingOff(false);
    }
  }, [selected, router]);

  // UNIT #20's help drawer is RETIRED (2026-08-14). All five sections --
  // 경계 처리 (the boundary doctrine), 넓이 한계, 라쏘 조작, 데이터 출처, 추출
  // 이후 -- are verbatim in lib/help-content.tsx (`regionHelpEntries`) and
  // render at /docs, opened by the book icon in the app topbar. The corner cap
  // travels as that builder's one argument: this screen knows the server's
  // `limits.max_polygon_vertices`, /docs does not and falls back to the same
  // 64 this screen used before its limits arrived.

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="flex items-center text-[22px] font-semibold tracking-tight text-white">
          {t("영역에서 네트워크 추출", "Extract a region", "領域からネットワークを抽出")}
        </h1>
        <p className="mt-1 text-[15px] text-slate-600">
          {t(
            "지도 영역을 지정하면 OpenStreetMap에서 그 범위의 도로망(.osm)을 받아 옵니다. 받은 추출은 업로드한 파일과 똑같은 경로로 생성·검증 게이트를 거칩니다.",
            "Name a region and the road network for it is fetched from OpenStreetMap as an .osm. The extract then takes exactly the same road through generation and the validation gate as an uploaded file.",
            "地図の領域を指定すると、その範囲の道路網(.osm)を OpenStreetMap から取得します。取得した抽出は、アップロードしたファイルとまったく同じ経路で生成と検証ゲートを通ります。",
          )}
        </p>
        <p className="mt-2 text-[14px] text-slate-500">
          <Link href="/genesis" className="underline">
            {t("← 파일 업로드로 생성하기 / 생성 이력 보기", "← Generate from a file / see past generations", "← ファイルから生成 / 生成履歴を見る")}
          </Link>
        </p>
      </header>

      {/* The listing / polling failure, at page level. It used to render inside
        * the 추출 이력 panel; that panel is gone (see the note at the bottom of
        * this file) but the failure it reported is not — `loadFetches` and the
        * per-fetch poll both still set it, and an error with nowhere to render
        * is an error the screen swallows. */}
      {listError && (
        <div className="mb-6 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-slate-800">
          {t("추출 목록을 불러오지 못했습니다", "Failed to load the extractions", "抽出一覧を読み込めませんでした")}:{" "}
          <span className="font-mono text-[14px] text-slate-700">{listError}</span>
        </div>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Pick a region                                                      */}
      {/* ----------------------------------------------------------------- */}
      {/* No subtitle (user text-purge, 2026-08-14): the limit behaviour speaks
        * when the extract button is enabled or not, and the refusal path names
        * itself when it fires. */}
      <Panel density="console" title={t("① 영역 지정", "1. Name the region", "① 領域の指定")}>
        {prefill && (
          /* UNIT #40 -- why this page opened on a shape nobody drew here. One
            * line, sky (info) tint with ink from the neutral ramp per the
            * tinted-box rule above, and every number is the backend's own
            * measurement quoted verbatim. It does NOT claim the shape is
            * right: the map below and the table beside it are how a reader
            * disagrees with it. */
          <div className="mb-4 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-[15px] leading-relaxed text-slate-800">
            {prefill.covers && prefill.covers.length > 1 ? (
              /* UNIT #48 -- a proposal that covers SEVERAL findings names every
                * one of them. The scalar fields below describe only the FIRST
                * covered finding (see lib/region-prefill.ts), so printing the
                * single-finding sentence here would tell a reader their extract
                * treats one shortage when it treats three. The extract button on
                * this very screen is what makes that difference matter. */
              <>
                <p>
                  {t(
                    `작업 ${prefill.parent_job}의 경계 소견 ${prefill.covers.length}건을 한 번에 덮으려고 넓힌 영역입니다 — 넓이 +${prefill.added_area_km2} km². 지도에서 확인·수정하신 뒤 아래 추출 버튼을 누르세요.`,
                    `This region was widened to cover all ${prefill.covers.length} of job ${prefill.parent_job}'s boundary findings at once — +${prefill.added_area_km2} km². Check it on the map, edit any corner, then press the extract button below.`,
                    `ジョブ ${prefill.parent_job} の境界に起因する検出 ${prefill.covers.length} 件をまとめて覆うために広げた領域です — 面積 +${prefill.added_area_km2} km²。地図で確認・修正のうえ、下の抽出ボタンを押してください。`,
                  )}
                </p>
                <ul className="mt-1.5 flex flex-col gap-0.5">
                  {prefill.covers.map((cover) => (
                    <li
                      key={`${cover.finding_code}::${cover.subject}`}
                      className="font-mono text-[14px] leading-relaxed text-slate-600"
                    >
                      {t(
                        `${cover.finding_code} / ${cover.subject} — ${cover.length_m} m${cover.extrapolated_m > 0 ? ` (원본 선언 ${cover.measured_source_m} m + 외삽 ${cover.extrapolated_m} m)` : ""}`,
                        `${cover.finding_code} / ${cover.subject} — ${cover.length_m} m${cover.extrapolated_m > 0 ? ` (${cover.measured_source_m} m source-declared + ${cover.extrapolated_m} m extrapolated)` : ""}`,
                        `${cover.finding_code} / ${cover.subject} — ${cover.length_m} m${cover.extrapolated_m > 0 ? `（ソース宣言 ${cover.measured_source_m} m + 外挿 ${cover.extrapolated_m} m）` : ""}`,
                      )}
                    </li>
                  ))}
                </ul>
                {prefill.covers.some((cover) => cover.extrapolated_m > 0) && (
                  /* THE SPLIT'S MEANING, not only its numbers. The per-finding
                    * lines above already print how many metres of each finger
                    * the source declared; this sentence is what tells a reader
                    * WHAT an extrapolated tail is -- a wider extract REQUEST,
                    * never a claim that the road is there. Without it the
                    * numbers read as two kinds of measurement. */
                  <p className="mt-1.5 text-[14px] text-slate-600">
                    {t(
                      "위에서 '외삽'으로 표시된 길이는 원본이 도로라고 선언한 구간이 아니라 마지막으로 측정된 방위로 그은 것입니다 — 도로를 지어낸 것이 아니라 그만큼 더 넓게 추출을 요청하는 것이므로, 각 손가락의 끝부분을 지도에서 꼭 확인해 주세요.",
                      "The metres marked 'extrapolated' above are not road the source declared: they are drawn on the last MEASURED bearing. They widen the EXTRACT REQUEST by that much and claim nothing about the road being there — so check the far end of each finger on the map.",
                      "上で「外挿」と表示された長さはソースが道路と宣言した区間ではなく、最後に測定された方位へ引いたものです — 道路を作り出したのではなく、その分だけ広く抽出を要求するものですので、各指の先端を地図で必ずご確認ください。",
                    )}
                  </p>
                )}
              </>
            ) : prefill.covers?.[0]?.basis_kind === BASIS_BOUNDARY_CUT ? (
              /* UNIT #48 -- ONE finding, but its metres are NOT a shortage.
                * An orphan's hand-off is always a single covered finding, so it
                * lands in this branch, and `deficit_m` here is `basis_m`: the
                * metres the lasso CUT off a severed way. The payload's own basis
                * note refuses to call that a deficit ("half of them are not
                * one"), so this banner does not either — and it says what the
                * widening DOES (reach far enough to contain the road that was
                * cut) rather than that it closes anything. Whether the re-extract
                * leaves the link connected is the new draft's answer, and it
                * faces the gate on its own. */
              <p>
                {t(
                  `작업 ${prefill.parent_job}의 ${prefill.finding_code}에서 경계가 잘라낸 ${prefill.deficit_m} m를 다시 담을 만큼 넓힌 영역입니다 — 링크 ${prefill.subject}, 넓이 +${prefill.added_area_km2} km². 지도에서 확인·수정하신 뒤 아래 추출 버튼을 누르세요.`,
                  `This region was widened far enough to take back the ${prefill.deficit_m} m the boundary cut off in job ${prefill.parent_job}'s ${prefill.finding_code} — link ${prefill.subject}, +${prefill.added_area_km2} km². Check it on the map, edit any corner, then press the extract button below.`,
                  `ジョブ ${prefill.parent_job} の ${prefill.finding_code} で境界が切り落とした ${prefill.deficit_m} m を再び含められるだけ広げた領域です — リンク ${prefill.subject}、面積 +${prefill.added_area_km2} km²。地図で確認・修正のうえ、下の抽出ボタンを押してください。`,
                )}
              </p>
            ) : (
              <p>
                {t(
                  `작업 ${prefill.parent_job}의 ${prefill.finding_code} 부족분 ${prefill.deficit_m} m를 메우려고 넓힌 영역입니다 — 링크 ${prefill.subject}, 넓이 +${prefill.added_area_km2} km². 지도에서 확인·수정하신 뒤 아래 추출 버튼을 누르세요.`,
                  `This region was widened to close job ${prefill.parent_job}'s ${prefill.finding_code} shortage of ${prefill.deficit_m} m — link ${prefill.subject}, +${prefill.added_area_km2} km². Check it on the map, edit any corner, then press the extract button below.`,
                  `ジョブ ${prefill.parent_job} の ${prefill.finding_code} 不足分 ${prefill.deficit_m} m を埋めるために広げた領域です — リンク ${prefill.subject}、面積 +${prefill.added_area_km2} km²。地図で確認・修正のうえ、下の抽出ボタンを押してください。`,
                )}
              </p>
            )}
            {prefill.extrapolated_m > 0 && (!prefill.covers || prefill.covers.length <= 1) && (
              /* THE SPLIT, on the screen that owns the extract button. The
                * finger is long enough to close the deficit, but the source could
                * only DECLARE part of it -- this job's own .osm was fetched
                * with the very boundary being widened, so it is clipped at the
                * same edge. Stating it only in the payload, or only on the job
                * page the reader has already left, would let the shape under
                * this banner read as measured road all the way out. Widening
                * the lasso widens the EXTRACT REQUEST; it claims nothing about
                * the road being there, and that is precisely why a human is
                * looking at a map instead of a fetch that already ran. */
              <p className="mt-1.5 text-[14px] text-slate-600">
                {t(
                  `이 중 원본이 도로라고 선언한 길이는 ${prefill.measured_source_m} m뿐이고, 나머지 ${prefill.extrapolated_m} m는 마지막으로 측정된 방위로 그은 것입니다 — 도로를 지어낸 것이 아니라 그만큼 더 넓게 추출을 요청하는 것이므로, 그 끝부분을 지도에서 꼭 확인해 주세요.`,
                  `Only ${prefill.measured_source_m} m of that finger is road the source itself declared; the remaining ${prefill.extrapolated_m} m is drawn on the last MEASURED bearing. It widens the EXTRACT REQUEST by that much and claims nothing about the road being there — so check its far end on the map.`,
                  `そのうちソースが道路と宣言しているのは ${prefill.measured_source_m} m のみで、残り ${prefill.extrapolated_m} m は最後に測定された方位へ引いたものです — 道路を作り出したのではなく、その分だけ広く抽出を要求するものですので、その先端を地図で必ずご確認ください。`,
                )}
              </p>
            )}
          </div>
        )}
        <div className="mb-4 flex flex-col gap-3">
          <RegionLassoMap
            vertices={vertices}
            closed={ringClosed}
            // The dashed rectangle is the SERVER's derived bounding box, taken
            // from the measurement — not computed here.
            bbox={measurement?.region ?? null}
            maxVertices={maxVertices}
            onAddVertex={addVertex}
            onClose={() => setRingClosed(true)}
            onDiscardLastVertex={() => setVertices((current) => current.slice(0, -1))}
            // UNIT #48 -- the map's drag handles land in the SAME callback the
            // table's typed edit does. That is the whole binding: `vertices`
            // stays this page's single source of truth, so dragging a corner
            // updates the row beneath it and typing a degree moves the handle
            // above it, without either side holding a copy.
            onMoveVertex={updateVertex}
            centre={DEFAULT_CENTRE}
            ariaLabel={t(
              "지도에서 영역 그리기 — 꼭짓점을 끌어 옮길 수 있습니다",
              "Draw the region on the map — corners can be dragged",
              "地図で領域を描く — 頂点はドラッグで移動できます",
            )}
            fitLabel={t("그린 영역으로 이동", "Zoom to shape", "描いた領域へ移動")}
            handleLabel={t("점", "Point", "点")}
          />
          {/* 경계 처리 ⓘ hidden (unit #20) -- its sentences moved into the page
            * help drawer above, verbatim. This row is now just the two controls
            * that act on the ring itself. */}
          <div className="flex flex-wrap items-center gap-3 text-[14px] text-slate-500">
            <button
              type="button"
              onClick={resetRing}
              disabled={vertices.length === 0}
              className="ui-button-ghost rounded-full px-3 py-1 text-[14px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("초기화", "Reset", "初期化")}
            </button>
            {maxVertices !== null && vertices.length >= maxVertices && (
              /* MONO: the cap is reached, which is a limit rather than a
                * fault — ink strength and weight carry it, no hue. */
              <span className="font-semibold text-slate-800">
                {t(
                  `점은 최대 ${maxVertices}개입니다 — 더 찍을 수 없습니다. 모양을 단순하게 그려 주세요(자동으로 줄이지 않습니다).`,
                  `At most ${maxVertices} corners — no more can be added. Draw the shape with fewer (it is never simplified for you).`,
                  `点は最大 ${maxVertices} 個です — これ以上は追加できません。形を単純に描いてください（自動では減らしません）。`,
                )}
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          {/* THE EDITABLE VERTEX TABLE (unit #20), replacing the old read-only
            * dotted coordinate enumeration. See `VertexRow`'s own doc comment
            * for the binding: this page's `vertices` state is the single
            * source of truth, a row's inputs are controlled from it, and an
            * invalid edit never reaches `setVertices` (so it never reaches the
            * map or a submitted payload either). Height-capped and scrollable
            * on purpose -- 64 rows must never turn this into a giant form. */}
          <div className="min-w-0 flex-1">
            <p className="mb-1.5 text-[14px] font-medium text-slate-500">
              {t(`점 ${vertices.length}개`, `${vertices.length} point(s)`, `点 ${vertices.length} 個`)}
            </p>
            <div className="max-h-56 overflow-y-auto rounded-md border border-slate-200">
              <table className="w-full text-left">
                <thead className="sticky top-0 bg-slate-50 text-[14px] font-medium text-slate-500">
                  <tr>
                    <th className="py-1.5 pl-2 pr-2 font-medium">#</th>
                    <th className="py-1.5 pr-2 font-medium">{t("위도", "lat", "緯度")}</th>
                    <th className="py-1.5 pr-2 font-medium">{t("경도", "lon", "経度")}</th>
                    <th className="py-1.5 pr-2" />
                  </tr>
                </thead>
                <tbody>
                  {vertices.map(([lat, lon], index) => (
                    <VertexRow
                      key={index}
                      index={index}
                      lat={lat}
                      lon={lon}
                      onCommit={updateVertex}
                      onDelete={deleteVertex}
                      t={t}
                    />
                  ))}
                  {vertices.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-2 py-3 text-center text-[14px] text-slate-500">
                        {t("지도를 눌러 점을 찍으세요.", "Click the map to add a point.", "地図をクリックして点を打ってください。")}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="min-w-0 flex-1">
            <label className="flex flex-col gap-1 text-[14px] text-slate-600">
              {t("추출 이름 (선택)", "Extract name (optional)", "抽出名（任意）")}
              <input
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("중심 좌표에서 자동", "derived from the centre", "中心座標から自動")}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px] text-slate-800"
              />
            </label>
          </div>
        </div>

        {measureError && (
          /* Two different things used to share one amber box. `!parsed` is not
            * an error — it is the instruction for a shape that is not finished
            * yet, and on load it is the FIRST thing on screen, so an amber
            * alert there would open the page on a warning nobody caused. A
            * measurement the server refused stays amber. */
          <p
            className={`mt-5 rounded-lg border px-4 py-3 text-[15px] leading-relaxed ${
              parsed
                ? "border-slate-400 bg-slate-100 text-slate-800 ring-1 ring-slate-400"
                : "border-slate-200 bg-slate-50 text-slate-600"
            }`}
          >
            {measureError}
          </p>
        )}

        {/* UNIT #20 -- "the limit becomes the button's job". The stat grid, the
          * 한계 안/한계 초과 chip and the "자세한 근거" detail that used to render
          * here are gone; the fetch behind them is untouched (`measurement`
          * still drives `readyToFetch` below and `overLimitReason`'s hover
          * text), only the permanent on-screen display of it is removed. */}

        <div className="mt-6 flex flex-wrap items-center gap-3">
          {/* The reason lives on the WRAPPING span, not the button itself --
            * a `title` on a disabled control does not fire in some browsers,
            * the same reason this app already wraps its cross-navigation
            * chips (`app/genesis/page.tsx`'s `regionHint`/`demandHint`/
            * `phasesHint`) in a titled span rather than titling the link. */}
          <span title={overLimitReason ?? undefined}>
            <button
              type="button"
              disabled={!readyToFetch}
              onClick={() => void submit()}
              className="rounded-md bg-slate-900 px-4 py-2 text-[15px] font-semibold text-ink disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {submitting
                ? t("요청하는 중…", "Requesting…", "要求中…")
                : t("이 영역 추출하기", "Extract this region", "この領域を抽出")}
            </button>
          </span>
        </div>

        {submitError && (
          <div className="mt-5 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-slate-800">
            <p className="font-semibold">
              {submitError instanceof GenesisError
                ? refusalHeadline(submitError.code, t)
                : t("추출을 시작하지 못했습니다", "Could not start the extraction", "抽出を開始できませんでした")}
            </p>
            <p className="mt-1.5 whitespace-pre-wrap font-mono text-[14px] leading-relaxed text-slate-700">
              {submitError.message}
            </p>
            {submitError instanceof RegionFetchError && (
              <p className="mt-1.5 font-mono text-[14px] text-slate-600">
                {JSON.stringify(submitError.measurement)}
              </p>
            )}
          </div>
        )}
      </Panel>

      {/* ----------------------------------------------------------------- */}
      {/* The selected extract                                               */}
      {/* ----------------------------------------------------------------- */}
      {selected && (
        <Panel
          density="console"
          title={t("② 추출 결과", "2. The extract", "② 抽出結果")}
          subtitle={t(
            "받은 추출은 업로드된 .osm과 같은 스테이징에 놓입니다 — 생성은 기존 경로 그대로입니다.",
            "The extract lands in the same staging area an uploaded .osm does — generation is the existing route, unchanged.",
            "取得した抽出はアップロードされた .osm と同じステージングに置かれます — 生成は既存の経路のままです。",
          )}
        >
          <div className={`mb-5 rounded-lg border px-4 py-3.5 text-[15px] leading-relaxed ${statusClasses(selected.status)}`}>
            <p className="text-[15px] font-semibold">{statusLabel(selected.status, t)}</p>
            <p className="mt-1.5 font-mono text-[14px]">
              {selected.name} · {selected.region.south}, {selected.region.west} → {selected.region.north},{" "}
              {selected.region.east}
            </p>
            {selected.polygon && (
              /* A lasso extract says so, and says what the rectangle printed
                * above it actually is — the record's `region` is that bounding
                * box in both shapes' case, so without this line a lasso and a
                * rectangle fetch would be indistinguishable in the history. */
              <p className="mt-1">
                {t(
                  `폴리곤 ${selected.polygon.vertices.length}점으로 질의했고 잘라내기도 그 폴리곤 기준입니다. 위 사각형은 폴리곤의 바운딩 박스입니다.`,
                  `Queried as a ${selected.polygon.vertices.length}-corner polygon, and trimmed at that polygon too; the rectangle above is its bounding box.`,
                  `ポリゴン ${selected.polygon.vertices.length} 点で問い合わせ、切り詰めもそのポリゴン基準です。上の矩形はポリゴンのバウンディングボックスです。`,
                )}
              </p>
            )}
            {!isTerminal(selected.status) && (
              <p className="mt-1">
                {t("진행", "Phase", "進行")}: {selected.phase} ({Math.round(selected.progress * 100)}%)
              </p>
            )}
            {selected.refusal && (
              <div className="mt-1.5">
                <p className="font-semibold">{refusalHeadline(selected.refusal.code, t)}</p>
                <ul className="mt-1 space-y-0.5">
                  {selected.refusal.reasons.map((reason, index) => (
                    <li key={index} className="font-mono text-[14px] leading-relaxed">
                      {reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {selected.provenance && (
            /* Where this file came from, recorded WITH it. The same record is
             * written into the .osm itself as a leading XML comment, so it
             * survives into the generation's source document and into the
             * artifact download -- a network whose origin cannot be
             * reconstructed is not auditable. */
            <div className="mb-5 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-[14px] leading-relaxed text-slate-700">
              <p className="text-[15px] font-semibold text-slate-800">
                {t("출처 기록 (provenance)", "Provenance", "出所の記録 (provenance)")}
              </p>
              <p className="mt-1.5">
                {t("인스턴스", "Instance", "インスタンス")}: <span className="font-mono">{selected.provenance.endpoint}</span>
              </p>
              <p className="mt-0.5">
                {t("받은 시각", "Fetched", "取得時刻")}: {formatTimestamp(selected.provenance.fetched_at, lang)} ·{" "}
                {selected.provenance.elapsed_ms} ms · {t("시도", "attempts", "試行")} {selected.provenance.attempts}
              </p>
              <p className="mt-0.5">
                {formatBytes(selected.provenance.bytes)} · {t("도로", "ways", "道路")} {selected.provenance.ways} ·{" "}
                {t("노드", "nodes", "ノード")} {selected.provenance.nodes}
              </p>
              <p className="mt-0.5 break-all font-mono">sha256 {selected.provenance.sha256.slice(0, 32)}</p>
              {selected.provenance.osm_base && (
                <p className="mt-0.5">
                  {t("지도 기준 시각", "Map base", "地図の基準時刻")}: {selected.provenance.osm_base}
                </p>
              )}
              <p className="mt-1">
                {t("라이선스", "Licence", "ライセンス")}: {selected.provenance.licence}
                {!selected.provenance.attribution_note_present && (
                  <span className="ml-1 opacity-80">
                    {t(
                      "(이 인스턴스는 ODbL 고지를 응답에 포함하지 않았습니다 — 없는 고지를 지어내지 않고 그대로 기록합니다.)",
                      "(this instance did not restate the ODbL note in its response — recorded as absent rather than invented)",
                      "（このインスタンスは応答に ODbL の告知を含めませんでした — 無いものを作らずそのまま記録します。）",
                    )}
                  </span>
                )}
              </p>
              <details className="mt-2">
                <summary className="cursor-pointer font-semibold">{t("보낸 질의", "The query sent", "送信した問い合わせ")}</summary>
                <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded bg-white/[0.04] px-2.5 py-2 font-mono text-[14px]">
                  {selected.provenance.query}
                </pre>
              </details>
            </div>
          )}

          {selected.status === "staged" && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  disabled={handingOff}
                  onClick={() => void handOff()}
                  className="rounded-md bg-slate-900 px-4 py-2 text-[15px] font-semibold text-ink disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  {handingOff
                    ? t("시작하는 중…", "Starting…", "開始中…")
                    : t("이 추출로 생성 시작 →", "Generate from this extract →", "この抽出で生成を開始 →")}
                </button>
                <span className="text-[14px] text-slate-500">
                  {t(
                    "생성 → 검증 게이트 → 등록. 게이트를 통과하지 못한 네트워크는 프로젝트에 등록되지 않습니다.",
                    "Generate → validation gate → register. A network the gate does not clear is never registered in the project.",
                    "生成 → 検証ゲート → 登録。ゲートを通らなかったネットワークはプロジェクトに登録されません。",
                  )}
                </span>
              </div>
              {handOffError && (
                <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-slate-800">
                  <p className="font-semibold">
                    {handOffError instanceof GenesisError
                      ? refusalHeadline(handOffError.code, t)
                      : t("생성을 시작하지 못했습니다", "Could not start the generation", "生成を開始できませんでした")}
                  </p>
                  <p className="mt-1.5 whitespace-pre-wrap font-mono text-[14px] text-slate-700">{handOffError.message}</p>
                </div>
              )}
            </div>
          )}
        </Panel>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* TWO SECTIONS USED TO SIT HERE, and both were REMOVED on 2026-08-13  */}
      {/* by the user's own decision after live testing (docs/ux-backlog.md,  */}
      {/* U-1 "REGION-PAGE ADDENDA"). Named here rather than deleted quietly, */}
      {/* because the second removal in particular is a doctrine call:        */}
      {/*                                                                     */}
      {/* 1. 추출 이력 — a list of previously staged extracts. Every           */}
      {/*    generation consumes a FRESH extract (the B4 runbook's own rule),  */}
      {/*    so a past staged extract had no action attached to it: clicking   */}
      {/*    a row only re-showed a provenance record for bytes the next       */}
      {/*    generation would not use. The history a user actually wants is    */}
      {/*    GENERATION history, and that lives on the hub (/genesis), where   */}
      {/*    each row carries its verdict, its region and its scale. The       */}
      {/*    LIST went; the listing plumbing did not — `loadFetches` still     */}
      {/*    runs, still selects the newest fetch, and the endpoint is         */}
      {/*    untouched, so the master-detail rebuild in U-1-flow has it.       */}
      {/*    `listError` moved to the page-level notice under the header, so   */}
      {/*    a failed listing is still named rather than swallowed.            */}
      {/*                                                                     */}
      {/* 2. "이 화면에 일부러 넣지 않은 것" — the deferred-features           */}
      {/*    disclosure (per-road include/exclude, block exclusion). THE       */}
      {/*    DOCTRINE NOTE, because this looks like a named-absence deletion   */}
      {/*    and is not one: the named-absence rule governs DATA this product  */}
      {/*    silently drops while processing — a road excluded from a draft,   */}
      {/*    a count a workbook did not carry, a zone the gate could not see.  */}
      {/*    It does not govern UNBUILT ROADMAP FEATURES. A user cannot act    */}
      {/*    on "this button does not exist yet", nothing on screen is wrong   */}
      {/*    without it, and the deferral record has a home already:           */}
      {/*    docs/ux-backlog.md and docs/product/master-roadmap-v2, which      */}
      {/*    carry it with its reason and its sequencing. The contract test    */}
      {/*    now pins this section ABSENT.                                     */}
      {/* ----------------------------------------------------------------- */}
    </div>
  );
}

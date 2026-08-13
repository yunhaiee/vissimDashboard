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

import Link from "next/link";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { useLanguage } from "@/components/LanguageProvider";
import { InfoTip, Panel, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  RegionFetchError,
  fetchRegionFetch,
  fetchRegionFetches,
  fetchRegionLimits,
  previewRegion,
  startGenesisJob,
  startRegionFetch,
  type RegionFetch,
  type RegionFetchStatus,
  type RegionMeasurement,
  type RegionShape,
} from "@/lib/api";

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
 * product's first market. Small enough to be inside every cap. */
const DEFAULT_BBOX = {
  south: "36.3444",
  west: "127.3765",
  north: "36.3564",
  east: "127.3925",
};

type BoxFields = typeof DEFAULT_BBOX;
type BoxKey = keyof BoxFields;

/** Rectangle or lasso. Exactly one is ever sent, and which one is what the mode
 * toggle selects — never both, because a polygon's bounding box is DERIVED by the
 * backend and a second one computed here could disagree with the rectangle the
 * fetch is actually judged against. */
type RegionMode = "rect" | "polygon";

/** Where the map looks before anything is drawn: the same 둔산 default the
 * rectangle fields carry, so the two modes open on the same ground. */
const DEFAULT_CENTRE: [number, number] = [
  (Number(DEFAULT_BBOX.south) + Number(DEFAULT_BBOX.north)) / 2,
  (Number(DEFAULT_BBOX.west) + Number(DEFAULT_BBOX.east)) / 2,
];

function parseBox(fields: BoxFields): { south: number; west: number; north: number; east: number } | null {
  const values = (Object.keys(fields) as BoxKey[]).map((key) => Number(fields[key]));
  if (values.some((value) => !Number.isFinite(value))) return null;
  const [south, west, north, east] = values;
  if (north <= south || east <= west) return null;
  return { south, west, north, east };
}

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

function bindingTermLabel(term: string, t: Translate): string {
  if (term === "meridian_convergence") {
    return t("자오선 수렴", "meridian convergence", "子午線収束");
  }
  if (term === "tangent_plane_curvature") {
    return t("접평면 곡률", "tangent-plane curvature", "接平面曲率");
  }
  return term;
}

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

/**
 * The requested region's EXTENT drawn inside the cap square, both to the same
 * scale.
 *
 * Not a map, and not because this app cannot draw one — `RegionLassoMap` beside
 * it draws CARTO tiles, as four other components in this app already do. It is
 * not a map because a map answers a different question. This answers the one
 * comparison that decides whether a fetch can happen at all: the region's
 * half-extent against the draft projection's validity square at this latitude.
 * Over the line, the box turns amber and the cap square is what it was measured
 * against.
 *
 * (What the product refuses is a GEOREFERENCE for generated output —
 * `services/genesis/project.py`: "NOT A GEOREFERENCE" — which is about placing an
 * emitted .inpx on the earth. Drawing a raw WGS84 region on a basemap is a
 * different act and makes no such claim.)
 *
 * Shape-agnostic on purpose: `width_m`/`height_m` are the extent of whatever was
 * measured — a rectangle's corners or a lasso's vertices — so a polygon feeds
 * this gauge unchanged rather than needing a second one.
 *
 * RECTANGLE MODE ONLY since U-1 polish (2026-08-13), and the component is kept
 * whole rather than deleted because rectangle mode still has nothing else.
 * WHAT THE USER MEASURED, in their own live use: in polygon mode this small
 * dashed square sits beside a real basemap carrying a real drawn ring, and it
 * reads as a stray rectangle somebody drew — a second, contradictory shape on a
 * screen whose whole subject is which shape is being judged. It also says
 * nothing the polygon screen does not already say: the map draws the ring AND
 * the server's derived bounding box, and the 한계 안 / 한계 초과 stat block
 * states the same half-extent-against-the-cap comparison in numbers. In
 * rectangle mode there is no map at all, so the gauge is the only picture of
 * that comparison and it stays.
 */
function RegionGauge({ measurement, t }: { measurement: RegionMeasurement; t: Translate }) {
  const capSide = 2 * measurement.validity_radius_m;
  const scale = 200 / Math.max(capSide, measurement.width_m, measurement.height_m);
  const boxW = Math.max(measurement.width_m * scale, 2);
  const boxH = Math.max(measurement.height_m * scale, 2);
  const capW = capSide * scale;
  const ok = measurement.within_region_cap;
  return (
    <svg viewBox="0 0 240 240" role="img" className="h-40 w-40 shrink-0" aria-label={t("영역과 한계 비교", "Region against the cap", "領域と上限の比較")}>
      <rect
        x={120 - capW / 2}
        y={120 - capW / 2}
        width={capW}
        height={capW}
        fill="none"
        stroke="currentColor"
        strokeDasharray="5 4"
        className="text-slate-400"
      />
      <rect
        x={120 - boxW / 2}
        y={120 - boxH / 2}
        width={boxW}
        height={boxH}
        className={ok ? "fill-sky-200/60 stroke-sky-600" : "fill-rose-200/60 stroke-rose-500"}
        strokeWidth={1.5}
      />
    </svg>
  );
}

/**
 * The Console stat: a small muted LABEL above a large bold VALUE, with an
 * optional quiet line under it.
 *
 * U-1 polish (docs/ux-backlog.md, item 13 — "the Console stat pattern … for the
 * verdict counts, region measurement and history scale numbers"). What it
 * replaces here is four stacked sentences inside a tinted card inside a panel:
 * every number the region measurement produces was body text at the same weight
 * as the prose explaining it, so the figures that decide whether a fetch can
 * happen at all had no more presence than the footnote about them.
 */
function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-[19px] font-semibold leading-tight tracking-tight text-slate-900">
        {value}
      </p>
      {hint && <p className="mt-1 text-xs leading-relaxed text-slate-500">{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GenesisRegionPage() {
  const { lang, t } = useLanguage();
  const router = useRouter();

  // POLYGON IS THE DEFAULT (U-1 polish, 2026-08-13 — the user's call after live
  // testing: the lasso over a basemap is what a study area actually looks like,
  // and four decimal-degree fields are the fallback for someone who already has
  // coordinates).
  //
  // THE INITIAL STATE, traced rather than assumed, because "default to polygon"
  // is only safe if the empty ring is genuinely empty:
  //   * `vertices` starts [], so `parsed` is null (a ring needs three corners);
  //   * null `parsed` makes the measure effect RETURN BEFORE the debounce timer
  //     is ever set, so `previewRegion` is not called on load — the screen opens
  //     with no request in flight;
  //   * `measurement` therefore stays null, so no measurement renders at all.
  //     DEFAULT_BBOX seeds the rectangle INPUT FIELDS and (through
  //     DEFAULT_CENTRE) the map's opening view and the limits lookup; it has
  //     never been a measurement, and in polygon mode nothing reads it. A stale
  //     "한계 안: 반경 …" seeded from 둔산 is structurally impossible here;
  //   * `readyToFetch` needs `measurement?.within_region_cap`, so the extract
  //     button is disabled until a real ring has been measured by the server.
  // The page holds no URL state (no useSearchParams, no router.replace), so a
  // deep link or a browser Back lands on this same initial state rather than on
  // a half-restored one.
  const [mode, setMode] = useState<RegionMode>("polygon");
  const [box, setBox] = useState<BoxFields>(DEFAULT_BBOX);
  const [vertices, setVertices] = useState<Array<[number, number]>>([]);
  const [ringClosed, setRingClosed] = useState(false);
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
  // ONE shape, chosen by the mode — the payload the preview measures and the
  // payload the fetch is started with are the same object, so the two can never
  // describe different regions.
  const parsed = useMemo<RegionShape | null>(() => {
    if (mode === "polygon") {
      return vertices.length >= 3 ? { polygon: vertices } : null;
    }
    return parseBox(box);
  }, [mode, box, vertices]);
  const overVertexCap = mode === "polygon" && maxVertices !== null && vertices.length > maxVertices;
  const readyToFetch = Boolean(
    parsed && measurement?.within_region_cap && !overVertexCap && !submitting,
  );

  const setField = useCallback((key: BoxKey, value: string) => {
    setBox((current) => ({ ...current, [key]: value }));
  }, []);

  const addVertex = useCallback((lat: number, lon: number) => {
    setVertices((current) => [...current, [lat, lon]]);
    setRingClosed(false);
  }, []);

  const resetRing = useCallback(() => {
    setVertices([]);
    setRingClosed(false);
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

  // Measure the box on the SERVER, debounced. The browser deliberately computes
  // no limit of its own: the cap is the draft projection's validity radius and
  // it is latitude-dependent, so a second implementation here could disagree
  // with the one that actually refuses.
  useEffect(() => {
    if (!parsed) {
      setMeasurement(null);
      setMeasureError(
        mode === "polygon"
          ? t(
              "지도를 눌러 점을 3개 이상 찍고, 두 번 눌러 영역을 닫습니다.",
              "Click at least three points on the map, then double-click to close the shape.",
              "地図をクリックして3点以上を打ち、ダブルクリックで領域を閉じます。",
            )
          : t(
              "북위가 남위보다, 동경이 서경보다 커야 합니다.",
              "North must be greater than south, and east greater than west.",
              "北緯は南緯より、東経は西経より大きくなければなりません。",
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
  }, [parsed, mode, t]);

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
      // The SAME `parsed` the preview measured, spread whole — a rectangle's four
      // fields or a polygon's ring, never a re-derivation of either.
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

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          {t("영역에서 네트워크 추출", "Extract a region", "領域からネットワークを抽出")}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          {t(
            "지도 영역을 지정하면 OpenStreetMap에서 그 범위의 도로망(.osm)을 받아 옵니다. 받은 추출은 업로드한 파일과 똑같은 경로로 생성·검증 게이트를 거칩니다.",
            "Name a region and the road network for it is fetched from OpenStreetMap as an .osm. The extract then takes exactly the same road through generation and the validation gate as an uploaded file.",
            "地図の領域を指定すると、その範囲の道路網(.osm)を OpenStreetMap から取得します。取得した抽出は、アップロードしたファイルとまったく同じ経路で生成と検証ゲートを通ります。",
          )}
        </p>
        <p className="mt-2 text-xs text-slate-500">
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
        <div className="mb-6 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[13px] text-slate-800">
          {t("추출 목록을 불러오지 못했습니다", "Failed to load the extractions", "抽出一覧を読み込めませんでした")}:{" "}
          <span className="font-mono text-xs text-slate-700">{listError}</span>
        </div>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Pick a region                                                      */}
      {/* ----------------------------------------------------------------- */}
      {/* No subtitle (user text-purge, 2026-08-14): the limit behaviour speaks
        * when the measurement row shows the number against the cap, and the
        * refusal path names itself when it fires. */}
      <Panel density="console" title={t("① 영역 지정", "1. Name the region", "① 領域の指定")}>
        {/* ONE shape at a time. Both modes send exactly one region, because a
          * polygon's bounding box is derived by the server and a second box from
          * here could disagree with the rectangle the fetch is judged against.
          *
          * POLYGON FIRST in the list as well as in the state: the order a
          * toggle is read in is half of what "default" means, and a default
          * that sits second is a default a user has to be told about. Pills on
          * `.ui-button-ghost` — this app's existing quiet-control vocabulary —
          * so the shape toggle stops competing with the extract button. */}
        <div className="mb-4 flex items-center gap-2" role="group" aria-label={t("영역 모양", "Region shape", "領域の形")}>
          {(
            [
              ["polygon", t("폴리곤 (라쏘)", "Polygon (lasso)", "ポリゴン（投げ縄）")],
              ["rect", t("사각형 좌표", "Rectangle by coordinates", "矩形（座標）")],
            ] as Array<[RegionMode, string]>
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              onClick={() => setMode(value)}
              className={
                mode === value
                  ? "rounded-full border border-slate-400 bg-slate-100 px-3.5 py-1.5 text-xs font-semibold text-slate-900"
                  : "ui-button-ghost rounded-full px-3.5 py-1.5 text-xs font-semibold"
              }
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "polygon" && (
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
              centre={DEFAULT_CENTRE}
              ariaLabel={t(
                "지도에서 영역 그리기",
                "Draw the region on the map",
                "地図で領域を描く",
              )}
              fitLabel={t("그린 영역으로 이동", "Zoom to shape", "描いた領域へ移動")}
            />
            {/* WHAT IS CUT — the boundary doctrine, verbatim, one click away
              * (user text-purge 2026-08-14: the always-visible sky box left the
              * screen; the sentences are unchanged and still pinned, because
              * the disclosure's home is now this ⓘ plus the generated
              * compliance report). */}
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1 text-[12px] text-slate-500">
                {t("경계 처리", "Boundary rule", "境界の扱い")}
                <InfoTip
                  text={t(
                    "그린 폴리곤이 그대로 기준입니다. Overpass 질의도, 도로 꼬리 잘라내기도, 모형 경계 끝점 판정도 모두 이 폴리곤으로 합니다 — 폴리곤 밖으로 뻗은 도로는 폴리곤 변에서 자르고 그 자리에 노드를 만듭니다. 옅은 점선 사각형은 폴리곤의 바운딩 박스로, 거리 기반 경계 띠와 용량 추정에만 쓰입니다. 이 내용은 생성된 준수 보고서에도 그대로 적힙니다.",
                    "The polygon you drew is the boundary. Overpass is queried with it, the way-tail trim cuts at it, and a road end is judged against it — a road running past the polygon is cut AT its edge and a node is placed there. The faint dashed rectangle is the polygon's bounding box, used only for the distance-based boundary band and the size estimate. The generated compliance report states this too.",
                    "描いたポリゴンがそのまま基準です。Overpass の問い合わせも、道路の尾部の切り詰めも、モデル境界の端点判定も、すべてこのポリゴンで行います — ポリゴンの外へ伸びる道路はその辺で切り詰め、そこにノードを作ります。薄い破線の矩形はポリゴンのバウンディングボックスで、距離ベースの境界帯と規模の見積りにのみ使います。この内容は生成される準拠レポートにも記載されます。",
                  )}
                />
              </span>
              <button
                type="button"
                onClick={resetRing}
                disabled={vertices.length === 0}
                className="ui-button-ghost rounded-full px-3 py-1 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-40"
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
        )}

        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          <div className="grid min-w-0 flex-1 grid-cols-2 gap-2">
            {mode === "rect" &&
              (
                [
                  ["north", t("북위 (north)", "North", "北緯 (north)")],
                  ["south", t("남위 (south)", "South", "南緯 (south)")],
                  ["west", t("서경 (west)", "West", "西経 (west)")],
                  ["east", t("동경 (east)", "East", "東経 (east)")],
                ] as Array<[BoxKey, string]>
              ).map(([key, label]) => (
                <label key={key} className="flex flex-col gap-1 text-xs text-slate-600">
                  {label}
                  <input
                    type="text"
                    inputMode="decimal"
                    value={box[key]}
                    onChange={(event) => setField(key, event.target.value)}
                    className="rounded-md border border-slate-300 px-2.5 py-1.5 font-mono text-xs text-slate-800"
                  />
                </label>
              ))}
            {mode === "polygon" && measurement?.polygon && (
              /* The ring in numbers, because a shape a user cannot read back is a
               * shape they cannot check. These are the SERVER's echo of what it
               * measured, not the local click list. */
              <p className="col-span-2 max-h-28 overflow-y-auto rounded-md border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs leading-relaxed text-slate-600">
                {measurement.polygon.vertices
                  .map(([lat, lon]) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`)
                  .join("  ·  ")}
              </p>
            )}
            <label className="col-span-2 flex flex-col gap-1 text-xs text-slate-600">
              {t("추출 이름 (선택)", "Extract name (optional)", "抽出名（任意）")}
              <input
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("중심 좌표에서 자동", "derived from the centre", "中心座標から自動")}
                className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs text-slate-800"
              />
            </label>
          </div>

          {/* RECTANGLE MODE ONLY — the gauge's own doc comment carries the
            * reason. In polygon mode the basemap above draws the ring AND the
            * server's derived bounding box, so a second, smaller, abstract
            * rectangle down here read (the user's words, live) as a stray
            * drawn shape. */}
          {mode === "rect" && measurement && (
            <div className="flex items-start gap-3 text-slate-500">
              <RegionGauge measurement={measurement} t={t} />
            </div>
          )}
        </div>

        {measureError && (
          /* Two different things used to share one amber box. `!parsed` is not
            * an error — it is the instruction for a shape that is not finished
            * yet, and on load in polygon mode it is the FIRST thing on screen,
            * so an amber alert there would open the page on a warning nobody
            * caused. A measurement the server refused stays amber. */
          <p
            className={`mt-5 rounded-lg border px-4 py-3 text-[13px] leading-relaxed ${
              parsed
                ? "border-slate-400 bg-slate-100 text-slate-800 ring-1 ring-slate-400"
                : "border-slate-200 bg-slate-50 text-slate-600"
            }`}
          >
            {measureError}
          </p>
        )}

        {measurement && (
          <div className="mt-6">
            {/* The HEADLINE is the comparison that actually decides, and that
              * is the half-extent against the validity radius -- never the
              * area. A long thin region can sit well under the area figure and
              * still be refused (measured: 1.4 x 8.4 km is 12 km² against a
              * 17 km² square, and its half-extent is 4 195 m against a 2 079 m
              * radius). Leading with the area would tell that user they were
              * inside the limit right up until the refusal.
              *
              * U-1 polish: the verdict is a chip and the four figures are
              * Console stat blocks (muted label, large value) laid directly on
              * the panel. What went away is the CARD — a bordered tinted box
              * inside a bordered panel, holding four sentences at one size, in
              * which the two numbers that decide the outcome looked exactly
              * like the footnote about them. Nothing was dropped: every figure
              * and every caveat below is the same string it was. */}
            <span
              className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${
                measurement.within_region_cap
                  ? "border-emerald-300 bg-emerald-50 text-slate-800"
                  : "border-rose-300 bg-rose-50 text-slate-800"
              }`}
            >
              {measurement.within_region_cap
                ? t("한계 안", "Within the limit", "上限内")
                : t("한계 초과 — 이 영역은 추출할 수 없습니다", "Past the limit — this region cannot be extracted", "上限超過 — この領域は抽出できません")}
            </span>

            <div className="mt-5 flex flex-wrap gap-x-10 gap-y-6">
              <Stat
                label={t("반경 / 한계", "half-extent / limit", "半径 / 上限")}
                value={`${measurement.half_extent_m.toFixed(0)} / ${measurement.validity_radius_m.toFixed(0)} m`}
              />
              <Stat
                label={t("가로 × 세로", "width × height", "幅 × 高さ")}
                value={`${(measurement.width_m / 1000).toFixed(2)} × ${(measurement.height_m / 1000).toFixed(2)} km`}
              />
              <Stat
                label={t("넓이", "area", "面積")}
                value={`${measurement.area_km2.toFixed(2)} km²`}
                hint={t(
                  `정사각형이라면 최대 ${measurement.max_area_km2.toFixed(1)} km² — 길쭉한 영역은 넓이가 작아도 반경에서 걸립니다`,
                  `at most ${measurement.max_area_km2.toFixed(1)} km² if square — a long thin region trips the half-extent even at a small area`,
                  `正方形なら最大 ${measurement.max_area_km2.toFixed(1)} km² — 細長い領域は面積が小さくても半径で引っかかります`,
                )}
              />
              {/* The binding TERM stays visible: it names WHY the cap binds
                * where it does, and it is the one number here a
                * differently-shaped region can move. What moved into the
                * disclosure below is the REASONING behind figures already on
                * screen -- UX pass U-1, after a valid box was measured to fire
                * five stacked paragraphs at once. Collapsed, never dropped: the
                * estimate still says it is an estimate, and the bounding-box
                * caveat still says which area the bytes were guessed from. */}
              <Stat
                label={t("구속 항", "Binding term", "拘束項")}
                value={bindingTermLabel(measurement.binding_term, t)}
                hint={`${t("투영 오차", "projection error", "投影誤差")} ${measurement.planimetric_error_bound_m.toFixed(2)} m`}
              />
            </div>

            <details className="mt-5 text-[13px] text-slate-500">
              <summary className="cursor-pointer text-xs font-semibold">
                {t("자세한 근거", "The reasoning in full", "詳しい根拠")}
              </summary>
              <p className="mt-2 leading-relaxed">
                {t(
                  "한계는 생성기의 투영이 1 m 오차 예산에 도달하는 반경입니다 — 이 선을 넘으면 생성기가 어차피 projection_out_of_range로 거부하므로, 내려받기 전에 거부합니다.",
                  "The limit is the half-extent at which the generator's projection reaches its 1 m error budget. Past that line the generator refuses outright (projection_out_of_range), so it is refused before the download rather than after.",
                  "上限は、生成器の投影が 1 m の誤差予算に達する半径です。この線を越えると生成器がいずれにせよ projection_out_of_range で拒否するため、取得前に拒否します。",
                )}
              </p>
              {measurement.polygon && measurement.estimate_area_km2 !== undefined && (
                /* Which area is which, said in numbers. The area above is the
                  * polygon's own (what a user means by "my study area"); the byte
                  * estimate below is taken over the BOUNDING BOX, because Overpass
                  * answers with whole ways and their nodes and the response is not
                  * proportional to the ring's area in any way we can defend. */
                <p className="mt-2 leading-relaxed">
                  {t("폴리곤 넓이", "Polygon area", "ポリゴンの面積")} {measurement.area_km2.toFixed(2)} km² ·{" "}
                  {t("바운딩 박스 넓이", "bounding-box area", "バウンディングボックスの面積")}{" "}
                  {measurement.estimate_area_km2.toFixed(2)} km² ·{" "}
                  {t("점", "corners", "点")} {measurement.polygon_vertices ?? measurement.polygon.vertices.length} —{" "}
                  {t(
                    "아래 예상 크기는 폴리곤이 아니라 바운딩 박스 넓이로 계산합니다(Overpass는 way를 통째로 돌려주므로 폴리곤 넓이에 비례하지 않습니다).",
                    "the size estimate below is taken over the bounding-box area, not the polygon's: Overpass returns WHOLE ways, so the response is not proportional to the ring's area.",
                    "以下の推定サイズはポリゴンではなくバウンディングボックスの面積で計算します（Overpass は way を丸ごと返すため、ポリゴンの面積に比例しません）。",
                  )}
                </p>
              )}
              <p className="mt-2 leading-relaxed">
                {t("예상 크기(상한 기준)", "Estimated size (upper-ish)", "推定サイズ（上限寄り）")} ≲ {formatBytes(measurement.estimated_bytes)} /{" "}
                {t("응답 한계", "response limit", "応答上限")} {formatBytes(measurement.max_response_bytes)} —{" "}
                {t(
                  "예상치일 뿐이며, 한계는 실제로 도착한 바이트에 대해 적용됩니다.",
                  "an estimate only; the cap is enforced against the bytes that actually arrive.",
                  "あくまで推定であり、上限は実際に到着したバイトに対して適用されます。",
                )}
              </p>
            </details>
          </div>
        )}

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={!readyToFetch}
            onClick={() => void submit()}
            className="rounded-md bg-slate-900 px-4 py-2 text-[13px] font-semibold text-ink disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {submitting
              ? t("요청하는 중…", "Requesting…", "要求中…")
              : t("이 영역 추출하기", "Extract this region", "この領域を抽出")}
          </button>
        </div>

        {submitError && (
          <div className="mt-5 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[13px] text-slate-800">
            <p className="font-semibold">
              {submitError instanceof GenesisError
                ? refusalHeadline(submitError.code, t)
                : t("추출을 시작하지 못했습니다", "Could not start the extraction", "抽出を開始できませんでした")}
            </p>
            <p className="mt-1.5 whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-700">
              {submitError.message}
            </p>
            {submitError instanceof RegionFetchError && (
              <p className="mt-1.5 font-mono text-xs text-slate-600">
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
          <div className={`mb-5 rounded-lg border px-4 py-3.5 text-[13px] leading-relaxed ${statusClasses(selected.status)}`}>
            <p className="text-[15px] font-semibold">{statusLabel(selected.status, t)}</p>
            <p className="mt-1.5 font-mono text-xs">
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
                    <li key={index} className="font-mono text-xs leading-relaxed">
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
            <div className="mb-5 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-700">
              <p className="text-[13px] font-semibold text-slate-800">
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
                <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded bg-white/[0.04] px-2.5 py-2 font-mono text-xs">
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
                  className="rounded-md bg-slate-900 px-4 py-2 text-[13px] font-semibold text-ink disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  {handingOff
                    ? t("시작하는 중…", "Starting…", "開始中…")
                    : t("이 추출로 생성 시작 →", "Generate from this extract →", "この抽出で生成を開始 →")}
                </button>
                <span className="text-xs text-slate-500">
                  {t(
                    "생성 → 검증 게이트 → 등록. 게이트를 통과하지 못한 네트워크는 프로젝트에 등록되지 않습니다.",
                    "Generate → validation gate → register. A network the gate does not clear is never registered in the project.",
                    "生成 → 検証ゲート → 登録。ゲートを通らなかったネットワークはプロジェクトに登録されません。",
                  )}
                </span>
              </div>
              {handOffError && (
                <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[13px] text-slate-800">
                  <p className="font-semibold">
                    {handOffError instanceof GenesisError
                      ? refusalHeadline(handOffError.code, t)
                      : t("생성을 시작하지 못했습니다", "Could not start the generation", "生成を開始できませんでした")}
                  </p>
                  <p className="mt-1.5 whitespace-pre-wrap font-mono text-xs text-slate-700">{handOffError.message}</p>
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

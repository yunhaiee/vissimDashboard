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
//   * shipped with a NAMED gap — the lasso is what Overpass is asked for
//     (`poly:`), but boundary judgment downstream is the polygon's BOUNDING BOX:
//     the tail trim and the boundary ladder are axis-aligned rectangle arithmetic
//     (five kernels verified in `85b4bb2`), and generalising them to an arbitrary
//     ring is its own unit. So the box is drawn on the map as a dashed rectangle,
//     said in words beside it, and written into the artifact's compliance report.
//     A gap on screen is a gap; a gap left for the user to discover is a lie;
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

import Link from "next/link";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { useLanguage } from "@/components/LanguageProvider";
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

function statusClasses(status: RegionFetchStatus): string {
  switch (status) {
    case "staged":
      return "border-emerald-300 bg-emerald-50 text-emerald-900";
    case "refused":
      return "border-amber-300 bg-amber-50 text-amber-900";
    case "error":
      return "border-rose-300 bg-rose-50 text-rose-900";
    default:
      return "border-sky-300 bg-sky-50 text-sky-900";
  }
}

/** ko/en/ja for the backend's stable refusal codes. The `reasons` array is still
 * shown verbatim underneath — it carries the endpoint, the measured metres and
 * the byte counts, and rewriting a backend refusal client-side is how a UI
 * starts lying about why something did not happen. */
function refusalHeadline(code: string | null, t: Translate): string {
  switch (code) {
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
    default:
      return t("요청이 거부되었습니다", "The request was refused", "要求が拒否されました");
  }
}

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

function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="mb-5 rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
      <h2 className="text-sm font-bold text-slate-900">{title}</h2>
      {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
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
        className={ok ? "fill-sky-200/60 stroke-sky-600" : "fill-amber-200/60 stroke-amber-600"}
        strokeWidth={1.5}
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GenesisRegionPage() {
  const { lang, t } = useLanguage();
  const router = useRouter();

  const [mode, setMode] = useState<RegionMode>("rect");
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
        if (!cancelled) setMaxVertices(limits.max_polygon_vertices);
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
        <p className="mt-1 text-xs text-slate-500">
          <Link href="/genesis" className="underline">
            {t("← 파일 업로드로 생성하기 / 생성 이력 보기", "← Generate from a file / see past generations", "← ファイルから生成 / 生成履歴を見る")}
          </Link>
        </p>
      </header>

      {/* ----------------------------------------------------------------- */}
      {/* Pick a region                                                      */}
      {/* ----------------------------------------------------------------- */}
      <Panel
        title={t("① 영역 지정", "1. Name the region", "① 領域の指定")}
        subtitle={t(
          "WGS84 십진도. 넓이는 내려받기 전에 한계와 비교해 보여 줍니다 — 한계를 넘으면 자르지 않고 거부합니다.",
          "WGS84 decimal degrees. The area is shown against the limit BEFORE anything is downloaded — past the limit it is refused, never truncated.",
          "WGS84 十進度。範囲は取得前に上限と比較して表示します — 上限を超えた場合は切り詰めず拒否します。",
        )}
      >
        {/* ONE shape at a time. Both modes send exactly one region, because a
          * polygon's bounding box is derived by the server and a second box from
          * here could disagree with the rectangle the fetch is judged against. */}
        <div className="mb-3 flex items-center gap-1.5" role="group" aria-label={t("영역 모양", "Region shape", "領域の形")}>
          {(
            [
              ["rect", t("사각형", "Rectangle", "矩形")],
              ["polygon", t("폴리곤 (라쏘)", "Polygon (lasso)", "ポリゴン（投げ縄）")],
            ] as Array<[RegionMode, string]>
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              onClick={() => setMode(value)}
              className={`rounded-md border px-3 py-1.5 text-xs font-semibold ${
                mode === value
                  ? "border-slate-500 bg-slate-100 text-slate-900"
                  : "border-slate-300 bg-transparent text-slate-500"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "polygon" && (
          <div className="mb-3 flex flex-col gap-2">
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
            {/* THE GAP, on screen. The query is the polygon; the boundary
              * judgment is the dashed rectangle. Said here, said again in the
              * generated compliance report, and never implied. */}
            <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-900">
              {t(
                "점선 사각형이 경계 판정 기준입니다. Overpass에는 그린 폴리곤 그대로 질의하지만, 도로 꼬리 잘라내기와 모형 경계 판정은 폴리곤의 바운딩 박스(점선)로 합니다 — 따라서 점선 안이면서 폴리곤 밖에 있는 도로 끝은 경계가 아니라 내부 막다른 길로 분류됩니다. 이 사실은 생성된 준수 보고서에도 그대로 적힙니다.",
                "The dashed rectangle is what boundary judgment uses. Overpass is queried with the polygon exactly as drawn, but the way-tail trim and the model-boundary decision use the polygon's BOUNDING BOX (dashed) — so a road end inside the dashes but outside the polygon is classified as an interior dead end, not as the model boundary. The generated compliance report states this too.",
                "破線の矩形が境界判定の基準です。Overpass には描いたポリゴンのまま問い合わせますが、道路の尾部の切り詰めとモデル境界の判定はポリゴンのバウンディングボックス（破線）で行います — したがって破線の内側でポリゴンの外にある道路端は、モデル境界ではなく内部の行き止まりとして分類されます。この事実は生成される準拠レポートにも記載されます。",
              )}
            </p>
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
              <span>
                {t("점", "Corners", "点")} {vertices.length}
                {maxVertices !== null && ` / ${maxVertices}`}
                {ringClosed
                  ? ` · ${t("닫힘", "closed", "閉じました")}`
                  : ` · ${t("두 번 눌러 닫기", "double-click to close", "ダブルクリックで閉じる")}`}
              </span>
              <button
                type="button"
                onClick={resetRing}
                disabled={vertices.length === 0}
                className="rounded-md border border-slate-300 px-2.5 py-1 font-semibold text-slate-600 disabled:cursor-not-allowed disabled:text-slate-300"
              >
                {t("초기화", "Reset", "初期化")}
              </button>
              {maxVertices !== null && vertices.length >= maxVertices && (
                <span className="text-amber-700">
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
              <p className="col-span-2 max-h-24 overflow-y-auto rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 font-mono text-[10.5px] leading-relaxed text-slate-600">
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

          {measurement && (
            <div className="flex items-start gap-3 text-slate-500">
              <RegionGauge measurement={measurement} t={t} />
            </div>
          )}
        </div>

        {measureError && (
          <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {measureError}
          </p>
        )}

        {measurement && (
          <div
            className={`mt-3 rounded-lg border px-3.5 py-3 text-xs ${
              measurement.within_region_cap
                ? "border-slate-200 bg-surface/90 text-slate-700"
                : "border-amber-300 bg-amber-50 text-amber-900"
            }`}
          >
            {/* The HEADLINE is the comparison that actually decides, and that
              * is the half-extent against the validity radius -- never the
              * area. A long thin region can sit well under the area figure and
              * still be refused (measured: 1.4 x 8.4 km is 12 km² against a
              * 17 km² square, and its half-extent is 4 195 m against a 2 079 m
              * radius). Leading with the area would tell that user they were
              * inside the limit right up until the refusal. */}
            <p className="font-semibold">
              {measurement.within_region_cap
                ? t("한계 안", "Within the limit", "上限内")
                : t("한계 초과 — 이 영역은 추출할 수 없습니다", "Past the limit — this region cannot be extracted", "上限超過 — この領域は抽出できません")}
              {": "}
              {t("반경", "half-extent", "半径")} {measurement.half_extent_m.toFixed(0)} m /{" "}
              {measurement.validity_radius_m.toFixed(0)} m
            </p>
            <p className="mt-1">
              {t("가로", "Width", "幅")} {(measurement.width_m / 1000).toFixed(2)} km ·{" "}
              {t("세로", "Height", "高さ")} {(measurement.height_m / 1000).toFixed(2)} km ·{" "}
              {t("넓이", "area", "面積")} {measurement.area_km2.toFixed(2)} km²{" "}
              <span className="opacity-70">
                (
                {t(
                  `정사각형이라면 최대 ${measurement.max_area_km2.toFixed(1)} km² — 길쭉한 영역은 넓이가 작아도 반경에서 걸립니다`,
                  `at most ${measurement.max_area_km2.toFixed(1)} km² if square — a long thin region trips the half-extent even at a small area`,
                  `正方形なら最大 ${measurement.max_area_km2.toFixed(1)} km² — 細長い領域は面積が小さくても半径で引っかかります`,
                )}
                )
              </span>
            </p>
            <p className="mt-1 leading-relaxed">
              {t(
                "한계는 생성기의 투영이 1 m 오차 예산에 도달하는 반경입니다 — 이 선을 넘으면 생성기가 어차피 projection_out_of_range로 거부하므로, 내려받기 전에 거부합니다.",
                "The limit is the half-extent at which the generator's projection reaches its 1 m error budget. Past that line the generator refuses outright (projection_out_of_range), so it is refused before the download rather than after.",
                "上限は、生成器の投影が 1 m の誤差予算に達する半径です。この線を越えると生成器がいずれにせよ projection_out_of_range で拒否するため、取得前に拒否します。",
              )}{" "}
              {t("구속 항", "Binding term", "拘束項")}: {bindingTermLabel(measurement.binding_term, t)} ·{" "}
              {t("투영 오차", "projection error", "投影誤差")} {measurement.planimetric_error_bound_m.toFixed(2)} m
            </p>
            {measurement.polygon && measurement.estimate_area_km2 !== undefined && (
              /* Which area is which, said in numbers. The area above is the
                * polygon's own (what a user means by "my study area"); the byte
                * estimate below is taken over the BOUNDING BOX, because Overpass
                * answers with whole ways and their nodes and the response is not
                * proportional to the ring's area in any way we can defend. */
              <p className="mt-1">
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
            <p className="mt-1 opacity-80">
              {t("예상 크기(상한 기준)", "Estimated size (upper-ish)", "推定サイズ（上限寄り）")} ≲ {formatBytes(measurement.estimated_bytes)} /{" "}
              {t("응답 한계", "response limit", "応答上限")} {formatBytes(measurement.max_response_bytes)} —{" "}
              {t(
                "예상치일 뿐이며, 한계는 실제로 도착한 바이트에 대해 적용됩니다.",
                "an estimate only; the cap is enforced against the bytes that actually arrive.",
                "あくまで推定であり、上限は実際に到着したバイトに対して適用されます。",
              )}
            </p>
          </div>
        )}

        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            disabled={!readyToFetch}
            onClick={() => void submit()}
            className="rounded-md bg-slate-900 px-3.5 py-1.5 text-xs font-semibold text-ink disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {submitting
              ? t("요청하는 중…", "Requesting…", "要求中…")
              : t("이 영역 추출하기", "Extract this region", "この領域を抽出")}
          </button>
          <span className="text-[11px] text-slate-400">
            {t(
              "추출에는 로그인이 필요합니다. 데이터는 OpenStreetMap(ODbL)에서 받아 옵니다.",
              "Extracting requires a signed-in user. The data comes from OpenStreetMap (ODbL).",
              "抽出にはログインが必要です。データは OpenStreetMap (ODbL) から取得します。",
            )}
          </span>
        </div>

        {submitError && (
          <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
            <p className="font-semibold">
              {submitError instanceof GenesisError
                ? refusalHeadline(submitError.code, t)
                : t("추출을 시작하지 못했습니다", "Could not start the extraction", "抽出を開始できませんでした")}
            </p>
            <p className="mt-1 whitespace-pre-wrap font-mono text-[11px] leading-relaxed">{submitError.message}</p>
            {submitError instanceof RegionFetchError && (
              <p className="mt-1 font-mono text-[11px] opacity-80">
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
          title={t("② 추출 결과", "2. The extract", "② 抽出結果")}
          subtitle={t(
            "받은 추출은 업로드된 .osm과 같은 스테이징에 놓입니다 — 생성은 기존 경로 그대로입니다.",
            "The extract lands in the same staging area an uploaded .osm does — generation is the existing route, unchanged.",
            "取得した抽出はアップロードされた .osm と同じステージングに置かれます — 生成は既存の経路のままです。",
          )}
        >
          <div className={`mb-3 rounded-lg border px-3.5 py-3 text-xs ${statusClasses(selected.status)}`}>
            <p className="text-sm font-bold">{statusLabel(selected.status, t)}</p>
            <p className="mt-1 font-mono text-[11px]">
              {selected.name} · {selected.region.south}, {selected.region.west} → {selected.region.north},{" "}
              {selected.region.east}
            </p>
            {selected.polygon && (
              /* A lasso extract says so, and says which rectangle it was judged
                * against — the record's `region` is that rectangle in both cases,
                * so without this line the two would be indistinguishable. */
              <p className="mt-1">
                {t(
                  `폴리곤 ${selected.polygon.vertices.length}점으로 질의했고, 위 사각형(폴리곤의 바운딩 박스)이 경계 판정 기준입니다.`,
                  `Queried as a ${selected.polygon.vertices.length}-corner polygon; the rectangle above is its bounding box, and that is what boundary judgment used.`,
                  `ポリゴン ${selected.polygon.vertices.length} 点で問い合わせ、上の矩形（ポリゴンのバウンディングボックス）が境界判定の基準です。`,
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
                    <li key={index} className="font-mono text-[11px] leading-relaxed">
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
            <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-[11px] text-slate-700">
              <p className="font-semibold text-slate-800">
                {t("출처 기록 (provenance)", "Provenance", "出所の記録 (provenance)")}
              </p>
              <p className="mt-0.5">
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
              <details className="mt-1.5">
                <summary className="cursor-pointer">{t("보낸 질의", "The query sent", "送信した問い合わせ")}</summary>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-white/[0.04] px-2 py-1.5 font-mono text-[10.5px]">
                  {selected.provenance.query}
                </pre>
              </details>
            </div>
          )}

          {selected.status === "staged" && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  disabled={handingOff}
                  onClick={() => void handOff()}
                  className="rounded-md bg-slate-900 px-3.5 py-1.5 text-xs font-semibold text-ink disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  {handingOff
                    ? t("시작하는 중…", "Starting…", "開始中…")
                    : t("이 추출로 생성 시작 →", "Generate from this extract →", "この抽出で生成を開始 →")}
                </button>
                <span className="text-[11px] text-slate-400">
                  {t(
                    "생성 → 검증 게이트 → 등록. 게이트를 통과하지 못한 네트워크는 프로젝트에 등록되지 않습니다.",
                    "Generate → validation gate → register. A network the gate does not clear is never registered in the project.",
                    "生成 → 検証ゲート → 登録。ゲートを通らなかったネットワークはプロジェクトに登録されません。",
                  )}
                </span>
              </div>
              {handOffError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                  <p className="font-semibold">
                    {handOffError instanceof GenesisError
                      ? refusalHeadline(handOffError.code, t)
                      : t("생성을 시작하지 못했습니다", "Could not start the generation", "生成を開始できませんでした")}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{handOffError.message}</p>
                </div>
              )}
            </div>
          )}
        </Panel>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* History                                                            */}
      {/* ----------------------------------------------------------------- */}
      <Panel
        title={t("추출 이력", "Extractions", "抽出履歴")}
        subtitle={t(
          "이 프로젝트의 추출만 보입니다.",
          "Only this project's extractions are listed.",
          "このプロジェクトの抽出のみ表示されます。",
        )}
      >
        {listError && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {t("목록을 불러오지 못했습니다", "Failed to load the list", "一覧を読み込めませんでした")}: {listError}
          </div>
        )}
        {fetches.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-xs text-slate-500">
            {t("아직 추출한 영역이 없습니다.", "No region has been extracted yet.", "まだ抽出した領域はありません。")}
          </p>
        ) : (
          <ul className="space-y-1.5">
            {fetches.map((entry) => (
              <li key={entry.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(entry.id)}
                  className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-xs ${
                    entry.id === selectedId ? "border-slate-400 bg-slate-50" : "border-slate-200 bg-transparent"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold text-slate-800">{entry.name}</span>
                    <span className="block truncate text-[11px] text-slate-500">
                      {entry.measurement.area_km2.toFixed(2)} km² · {formatTimestamp(entry.created_at, lang)}
                    </span>
                  </span>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClasses(entry.status)}`}
                  >
                    {statusLabel(entry.status, t)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* What of the 0.4 mockup is deliberately NOT here, and what no longer is.
        * Stated on the page rather than only in a commit message: a consultant who
        * read the mockup would otherwise look for a feature and conclude the page
        * is broken. The lasso left this list when it shipped; the two that remain
        * have the same reason they always had, which is why the reason is repeated
        * rather than replaced. */}
      <p className="rounded-xl border border-dashed border-slate-300 px-5 py-4 text-[11px] leading-relaxed text-slate-500">
        {t(
          "폴리곤 라쏘는 이제 지원합니다(경계 판정은 바운딩 박스 — 위 설명 참고). 목업의 도로 개별 선택·블록 제외(공유 벽 규칙)는 의도적으로 다음 단계로 미뤘습니다 — 둘 다 이미 뽑아낸 도로망의 기하가 있어야 동작하므로, 추출 위에 얹는 기능이지 추출을 대신하는 기능이 아닙니다. 라쏘는 그 기하가 필요하지 않아 먼저 나왔습니다.",
          "The polygon lasso is now supported (boundary judgment uses its bounding box — see above). The mockup's per-road selection and block exclusion (the shared-wall rule) are deliberately deferred: both act on the geometry of a network that does not exist until after an extract has been fetched and drafted, so they sit on top of this step rather than replacing it. The lasso needed none of that geometry, which is why it shipped first.",
          "ポリゴン投げ縄は対応しました（境界判定はバウンディングボックス — 上記参照）。モックアップの道路ごとの選択・ブロック除外（共有壁ルール）は意図的に次段階へ送りました — いずれも抽出後の道路網の幾何が必要で、抽出の上に載る機能であり、抽出の代わりではありません。投げ縄はその幾何を必要としないため先に出しました。",
        )}
      </p>
    </div>
  );
}

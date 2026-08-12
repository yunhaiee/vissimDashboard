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
// COLLAPSE IS ALLOWED, DELETION IS NOT. The optional `collapsible` prop below
// exists so a screen can put a section one click away; it uses the same
// 펼치기/접기 wording (and the same button chrome) `GateFindingsMap`'s MapShell
// already uses, so the app grows no second disclosure vocabulary. A caller that
// needs a section's open state to follow the DATA (open it when this
// intersection's cross-check flagged something) passes `defaultOpen` and a
// `key` that changes with the data -- severity decides the default, the reader
// decides the rest.

import { useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";

type Translate = (ko: string, en: string, ja: string) => string;

/** The two paddings the four copies actually shipped with, named rather than
 * averaged away. `regular` = genesis + region; `compact` = demand + phases. */
const DENSITY = {
  regular: "mb-5 px-5 py-4",
  compact: "mb-4 px-4 py-3",
} as const;

export function Panel({
  title,
  subtitle,
  children,
  density = "regular",
  collapsible = false,
  defaultOpen = true,
}: {
  title: string;
  subtitle?: string;
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
          <h2 className="text-[15px] font-bold text-slate-900">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
        </div>
        {collapsible && (
          <button
            type="button"
            onClick={() => setOpen((current) => !current)}
            aria-expanded={open}
            className="shrink-0 rounded-md border border-slate-300 bg-white/[0.06] px-2.5 py-1 text-[11px] font-semibold text-slate-700"
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

    default:
      return fallback ?? t("요청이 거부되었습니다", "The request was refused", "要求が拒否されました");
  }
}

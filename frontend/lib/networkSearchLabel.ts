// Display-label composition for network component search (v2 UX unit,
// 2026-07-28). backend/services/network_search.py ships RAW PARTS only
// (`{type, id, name, detail}` -- see its own docstring on why: the language
// regression fixed in bd63874 came from a backend module baking in a
// localized noun). This module is the ONE place that turns those parts into
// the ko-primary / en / ja string a human reads, so NetworkSearchBox's
// dropdown rows and the two host pages' flash banners never diverge on how a
// hit is described.
//
// DUPLICATE DISPLAY NAMES ARE REAL (task brief): on the live Murdoch model,
// link 515 and connector 10969 are BOTH named "South St EB". A label built
// from `name` alone would make them indistinguishable, so every label here is
// "<localized type> <id> · <name>" (falling back to just "<type> <id>" when
// `name` is empty, e.g. every route -- Vissim routes have no `name` field at
// all) -- self-disambiguating by construction, per the task brief's own
// example ("링크 515 · South St EB" vs "연결로 10969 · South St EB").
//
// Deliberately NOT reusing lib/networkElements.ts's ELEMENT_TYPE_LABELS: that
// table's keys are NetworkElements list keys (the glyph classes NetworkViewer
// draws -- "signal_heads", plural), while search's family types are
// singular and cover several families NetworkViewer never draws at all
// (node, signal_controller, conflict_area, routing_decision, route,
// vehicle_composition, des_speed_distribution). A single table keyed by one
// or the other would need silent renaming either way; two small tables for
// two different address spaces is the honest shape. `link` is the one type
// shared with that module's vocabulary and IS reused, via `linkKindLabel`,
// because the connector-vs-link distinction is exactly what disambiguates
// the South St EB case.
import type { NetworkSearchHit } from "./api";
import { linkKindLabel } from "./networkElements";

export type Translator = (ko: string, en: string, ja?: string) => string;

/** The parts of a hit these functions actually read -- callers that only
 * have a hit's raw parts (e.g. a stored selection carrying data instead of a
 * baked string, so a display can re-localize on a later render) don't need
 * to keep the full `NetworkSearchHit` (its `match`/`focus` fields) around
 * just to satisfy this module's signatures. `NetworkSearchHit` itself
 * structurally satisfies this type, so existing full-hit callers are
 * unaffected. */
export type SearchHitLabelParts = Pick<NetworkSearchHit, "type" | "id" | "name" | "label">;

const SEARCH_TYPE_LABELS: Record<string, { ko: string; en: string; ja: string }> = {
  node: { ko: "노드", en: "Node", ja: "ノード" },
  signal_controller: { ko: "신호제어기", en: "Signal controller", ja: "信号制御機" },
  signal_head: { ko: "신호등", en: "Signal head", ja: "信号灯" },
  vehicle_input: { ko: "차량 입력", en: "Vehicle input", ja: "車両入力" },
  conflict_area: { ko: "상충 영역", en: "Conflict area", ja: "競合エリア" },
  routing_decision: { ko: "경로 결정", en: "Routing decision", ja: "経路決定" },
  route: { ko: "경로", en: "Route", ja: "ルート" },
  des_speed_decision: { ko: "희망속도 결정", en: "Desired speed decision", ja: "希望速度決定" },
  reduced_speed_area: { ko: "감속 구역", en: "Reduced speed area", ja: "減速エリア" },
  detector: { ko: "검지기", en: "Detector", ja: "検知器" },
  data_collection_point: { ko: "데이터 수집 지점", en: "Data collection point", ja: "データ収集地点" },
  queue_counter: { ko: "대기행렬 카운터", en: "Queue counter", ja: "待ち行列カウンタ" },
  pt_stop: { ko: "정류장", en: "PT stop", ja: "停留所" },
  stop_sign: { ko: "정지 표지", en: "Stop sign", ja: "一時停止標識" },
  traveltime_section: { ko: "통행시간 구간", en: "Travel time section", ja: "旅行時間区間" },
  ped_area: { ko: "보행자 구역", en: "Pedestrian area", ja: "歩行者エリア" },
  vehicle_composition: { ko: "차량 구성", en: "Vehicle composition", ja: "車両構成" },
  des_speed_distribution: { ko: "희망속도 분포", en: "Desired speed distribution", ja: "速度分布" },
};

/** Localized noun for one search hit's family -- "링크"/"연결로" for links
 * (via the connector flag search's own `label.detail.connector` carries),
 * the class noun for everything else. Unknown/future family types fall back
 * to the raw type token rather than throwing. */
export function searchHitTypeLabel(hit: SearchHitLabelParts, t: Translator): string {
  if (hit.type === "link") {
    return linkKindLabel(Boolean(hit.label.detail.connector), t);
  }
  const row = SEARCH_TYPE_LABELS[hit.type];
  return row ? t(row.ko, row.en, row.ja) : hit.type;
}

/** The one human string every consumer renders for a hit: "<type> <id> ·
 * <name>", or "<type> <id>" when the object has no name (routes). */
export function searchHitDisplayLabel(hit: SearchHitLabelParts, t: Translator): string {
  const typeLabel = searchHitTypeLabel(hit, t);
  const name = hit.name?.trim();
  return name ? `${typeLabel} ${hit.id} · ${name}` : `${typeLabel} ${hit.id}`;
}

import type { NetworkElements } from "./api";

/** One clicked static network element, as published to the floating assistant.
 * `type` is a NetworkElements key (the same key the viewer's layers panel uses);
 * `link` is the owning link id where the class has one (ped_areas do not). */
export type NetworkElementRef = {
  type: string;
  id: string;
  name: string;
  link?: string | null;
};

/** One clicked link or connector, as published to the floating assistant. */
export type NetworkLinkRef = {
  id: string;
  name: string;
  isConnector: boolean;
};

// Element class catalogue — panel order + i18n labels (ko primary, en via t();
// every ko string here already has a JA_UI entry). Lives in lib/ rather than in
// NetworkViewer so the pages and the floating assistant can label a clicked
// element without importing the d3/ssr:false viewer module.
export const ELEMENT_TYPE_LABELS: { key: keyof NetworkElements; ko: string; en: string }[] = [
  { key: "signal_heads", ko: "신호등", en: "Signal head" },
  { key: "vehicle_inputs", ko: "차량 입력", en: "Vehicle input" },
  { key: "reduced_speed_areas", ko: "감속 구역", en: "Reduced speed area" },
  { key: "des_speed_decisions", ko: "희망속도 결정", en: "Desired speed decision" },
  { key: "detectors", ko: "검지기", en: "Detector" },
  { key: "data_collection_points", ko: "데이터 수집 지점", en: "Data collection point" },
  { key: "traveltime_sections_geo", ko: "통행시간 구간", en: "Travel time section" },
  { key: "queue_counters", ko: "대기행렬 카운터", en: "Queue counter" },
  { key: "stop_signs", ko: "정지 표지", en: "Stop sign" },
  { key: "pt_stops", ko: "정류장", en: "PT stop" },
  { key: "ped_areas", ko: "보행자 구역", en: "Pedestrian area" },
];

/** Localized label for an element class key; unknown keys fall back to the raw
 * key rather than throwing (the backend could add a class before the UI does). */
export function elementTypeLabel(type: string, t: (ko: string, en: string, ja?: string) => string): string {
  const row = ELEMENT_TYPE_LABELS.find((label) => label.key === type);
  return row ? t(row.ko, row.en) : type;
}

/** Localized label for a link vs. a connector — the same two ko strings the
 * viewer's tooltips and the page-context notes already use. */
export function linkKindLabel(isConnector: boolean, t: (ko: string, en: string, ja?: string) => string): string {
  return isConnector ? t("연결로", "connector") : t("링크", "link");
}

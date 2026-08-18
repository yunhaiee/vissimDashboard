"use client";

// U-1-map (docs/ux-backlog.md, the user's detailed elaboration item #1) — the
// half of the findings map that is NOT a renderer.
//
// WHY THIS FILE EXISTS. The gate's findings now have TWO renderers: a real
// basemap (`GenesisTileMap`, leaflet + CARTO over the server-computed WGS84
// GeoJSON) and the original local-coordinates SVG (`DraftLocalMap`), which
// survives as the honest fallback for a generation whose draft carries no
// recorded projection origin. Everything below is what both of them agree on —
// which finding touches which link, which colour a tier is drawn in, and what a
// click SAYS — because two copies of that is two answers to "why is this link
// red", and this product's whole claim is that there is one.
//
// The id-resolution itself is NOT here: it lives in lib/genesisArtifacts.ts's
// `resolveFindingHighlight`, whose doc comment carries the measured inventory of
// which gate finding codes actually carry a link/connector id. This file only
// collects and groups what that function returns.

import type { GenesisGateCheck, GenesisGateFinding, GenesisGateReport } from "@/lib/api";
import {
  describeGenesisSubject,
  resolveFindingHighlight,
  type GenesisDraftNetwork,
  type GenesisFindingHighlight,
  type GenesisIsolatedZone,
} from "@/lib/genesisArtifacts";

export type Translate = (ko: string, en: string, ja: string) => string;

// THE DRAWING VOCABULARY, named once so a refuse-tier finding cannot be rose in
// one renderer and red in the other.
//
// THREE COLOURS ONLY (user decision, 2026-08-13): rose = refuse, sky =
// selection, and neutral for everything else. The warn tier used to be amber
// (#d97706) and is now near-white INK — it is told apart from an ordinary link
// by WEIGHT and by a DASH pattern, not by a fourth hue. That is a stronger
// signal than the amber was on this theme, not a weaker one: globals.css
// remaps the whole amber family to neutral anyway, so the old warn stroke was
// already rendering grey at ordinary weight.
export const NORMAL_LINK = "#c7cfdb";
export const NORMAL_CONNECTOR = "#dbe1ea";
export const REFUSE_COLOR = "#e11d48"; // rose-600, this app's existing "fail" convention
export const WARN_COLOR = "#fafaf9";
export const SELECTED_COLOR = "#0ea5e9"; // sky-500, reserved for the clicked element

/** The warn tier's dash, in SVG/leaflet's shared `stroke-dasharray` form. What
 * carries "warn" once the hue is gone, so both renderers must use it. */
export const WARN_DASH = "7 5";

/** U-19 round 2 -- a connector's OWN identity cue, mirroring
 * `NetworkViewer.tsx`'s Layer 4 (a thin dashed centreline drawn over a
 * connector's full-width pavement; see that file's own comment: "Connectors
 * use the SAME width rule and floor as links -- they are pavement, not
 * annotation"). Deliberately a different rhythm from `WARN_DASH` (short dash,
 * long gap, vs. WARN_DASH's near-solid "7 5") so a normal-tier connector's
 * identity dash can never read as the warn tier at a glance -- two different
 * claims ("this is a connector" vs. "this failed a warn-tier check") must
 * never share one dash pattern. */
export const CONNECTOR_IDENTITY_DASH = "2 6";

export type FindingRef = {
  checkId: string;
  checkTitle: string;
  tier: GenesisGateCheck["tier"];
  finding: GenesisGateFinding;
  via: "finding" | "warning";
};

// A clicked link/connector can carry MORE THAN ONE finding -- V4's lane checks
// alone can put three separate findings on one connector. `refs` therefore
// carries every finding for the clicked element, never just the first; the
// detail panel lists all of them rather than silently dropping the rest.
//
// U-19 ("clicking a link/connector selects it and shows what the gate said
// about it") added `"element"`: a DIRECTLY clicked link/connector, which is
// now every feature on the map, not only ones a finding already highlighted.
// `refs` here is legitimately often empty (most links carry no finding at
// all) -- it is populated by `buildHighlights`'s `byLink`/`byConnector`, the
// SAME per-element grouping the map's colouring already uses, which is
// itself built from `resolveFindingHighlight`. So "which findings touch this
// element" is answered by the one resolver, reached two ways: a finding
// resolving forward to its geometry (`selectedHighlightOf` below, the
// `"finding"` arm) and an element resolving backward to its findings
// (`highlights.byLink`/`byConnector`, read by both renderers' click handlers
// in `GenesisTileMap.tsx`) -- never a second, independent id/name match.
export type SelectedItem =
  | { kind: "element"; elementKind: "link" | "connector"; id: number; name: string; refs: FindingRef[] }
  | {
      kind: "finding";
      refs: FindingRef[];
      /** Unit #41 -- a MEASURED point to mark and centre on, carried only when
       * the backend supplied one (a V3 testimony's own
       * `source_testimony.coordinates`, in WGS84). Optional and never derived:
       * a finding with no coordinate in the payload gets no marker rather than
       * a plausible-looking pin at a link end this client picked. The
       * local-coordinates fallback renderer ignores it on purpose — it has no
       * projection to place a WGS84 point with. */
      point?: { lat: number; lon: number; label: string };
    }
  | { kind: "excluded_zone"; zone: GenesisIsolatedZone }
  | null;

export interface HighlightSets {
  refuseLinks: Set<number>;
  warnLinks: Set<number>;
  refuseConnectors: Set<number>;
  warnConnectors: Set<number>;
  byLink: Map<number, FindingRef[]>;
  byConnector: Map<number, FindingRef[]>;
  unresolved: FindingRef[];
  /** Unit #41 -- `findingKeyOf` for every finding whose subject DID resolve to
   * geometry. The verdict rail reads it to decide which finding rows may be
   * offered as "click to see it on the map": a row that cannot highlight
   * anything must not look like it can, which is the whole of the bug this
   * unit was opened for. Built in the same pass as everything else here, off
   * the same single `resolveFindingHighlight` call. */
  placeable: Set<string>;
}

/** The identity a finding is looked up by ACROSS the two payloads that
 * describe it -- the gate report (`check.findings`) and the fix catalogue
 * (`GET .../fixes`). `code + subject` is the same pair the backend itself
 * addresses a finding by (unit #30's apply target), so nothing here invents a
 * key: two findings the backend treats as one are one here too. */
export function findingKeyOf(finding: { code: string; subject: string }): string {
  return `${finding.code}::${finding.subject}`;
}

export function collectFindingRefs(report: GenesisGateReport): FindingRef[] {
  const refs: FindingRef[] = [];
  for (const check of report.checks) {
    for (const finding of check.findings) {
      refs.push({ checkId: check.id, checkTitle: check.title, tier: check.tier, finding, via: "finding" });
    }
    for (const warning of check.warnings) {
      refs.push({ checkId: check.id, checkTitle: check.title, tier: check.tier, finding: warning, via: "warning" });
    }
  }
  return refs;
}

/** Highlight sets, one pass over every finding: which links/connectors carry a
 * refuse-tier finding, which carry a warn-tier one, and which findings resolved
 * to nothing at all. A link touched by both a refuse and a warn finding is
 * drawn refuse -- the stronger claim wins the pixel. `byLink`/`byConnector`
 * collect EVERY finding touching one element (not just the first) -- a click
 * surfaces the whole list, see `FindingsDetailPanel`. */
export function buildHighlights(
  findingRefs: FindingRef[],
  network: GenesisDraftNetwork | null,
): HighlightSets {
  const refuseLinks = new Set<number>();
  const warnLinks = new Set<number>();
  const refuseConnectors = new Set<number>();
  const warnConnectors = new Set<number>();
  const byLink = new Map<number, FindingRef[]>();
  const byConnector = new Map<number, FindingRef[]>();
  const unresolved: FindingRef[] = [];
  const placeable = new Set<string>();
  if (network) {
    for (const ref of findingRefs) {
      const highlight = resolveFindingHighlight(ref.finding, network);
      if (!highlight.resolved) {
        unresolved.push(ref);
        continue;
      }
      placeable.add(findingKeyOf(ref.finding));
      const isRefuse = ref.tier === "refuse";
      for (const linkId of highlight.linkIds) {
        (isRefuse ? refuseLinks : warnLinks).add(linkId);
        if (!byLink.has(linkId)) byLink.set(linkId, []);
        byLink.get(linkId)!.push(ref);
      }
      for (const connectorId of highlight.connectorIds) {
        (isRefuse ? refuseConnectors : warnConnectors).add(connectorId);
        if (!byConnector.has(connectorId)) byConnector.set(connectorId, []);
        byConnector.get(connectorId)!.push(ref);
      }
    }
  }
  return {
    refuseLinks,
    warnLinks,
    refuseConnectors,
    warnConnectors,
    byLink,
    byConnector,
    unresolved,
    placeable,
  };
}

/** The currently-selected finding(s), as one highlight to redraw on top in the
 * selected colour. Unions every selected finding's own highlight (not just the
 * first) since two findings on the same clicked element are not guaranteed to
 * resolve to the same zone.
 *
 * A direct element click (`kind: "element"`, U-19) draws EXACTLY the clicked
 * link or connector -- it does not expand to a retained isolated zone the way
 * a finding's resolved highlight does (see `resolveFindingHighlight`'s doc
 * comment on `findRetainedZoneFor`): the reader selected one specific piece of
 * geometry, not "where does this finding point", so there is no id/name
 * matching to do here at all -- the id is already known. */
export function selectedHighlightOf(
  selected: SelectedItem,
  network: GenesisDraftNetwork | null,
): GenesisFindingHighlight | null {
  if (!network || !selected) return null;
  if (selected.kind === "element") {
    return {
      linkIds: selected.elementKind === "link" ? [selected.id] : [],
      connectorIds: selected.elementKind === "connector" ? [selected.id] : [],
      resolved: true,
    };
  }
  if (selected.kind !== "finding") return null;
  const linkIds = new Set<number>();
  const connectorIds = new Set<number>();
  for (const ref of selected.refs) {
    const highlight = resolveFindingHighlight(ref.finding, network);
    for (const linkId of highlight.linkIds) linkIds.add(linkId);
    for (const connectorId of highlight.connectorIds) connectorIds.add(connectorId);
  }
  return { linkIds: [...linkIds], connectorIds: [...connectorIds], resolved: true };
}

/** Unit #41 -- ONE way to print a subject, road name first and the machine
 * numbers small behind it, shared by the map's detail panel and the verdict
 * rail's finding rows so the same element cannot read two ways on one screen.
 * The split itself is `describeGenesisSubject`'s (lib/genesisArtifacts.ts);
 * this only decides the type treatment. */
export function SubjectLabel({
  subject,
  id,
  className,
}: {
  subject: string;
  /** An id the caller knows from somewhere OTHER than the subject string
   * (a fix finding's own `context.link_id`, for one). Printed only when the
   * subject did not already carry one -- never as a second, conflicting id. */
  id?: number | null;
  className?: string;
}) {
  const parts = describeGenesisSubject(subject);
  const shownId = parts.id ?? id ?? null;
  const tail = [parts.trailing, shownId === null ? "" : `#${shownId}`].filter(Boolean).join(" · ");
  return (
    <span className={className}>
      <span className="font-semibold text-slate-900">{parts.lead}</span>
      {tail && <span className="ml-1.5 font-mono text-[14px] font-normal text-slate-500">{tail}</span>}
    </span>
  );
}

/** ONE finding, as the map's detail panel prints it. Extracted in unit #41
 * because the two arms below ("element" and "finding") carried two identical
 * copies of it, and a road-name-first rewrite of one copy is how the same
 * finding starts reading two ways depending on which way the reader reached
 * it. Order is the unit's own: what it IS (subject), what it SAYS (the gate's
 * verbatim sentence), then the machine identity, small and last. */
function FindingRefLine({ entry, t }: { entry: FindingRef; t: Translate }) {
  return (
    <>
      <span
        className={`rounded-full border px-1.5 py-0.5 text-[14px] font-bold uppercase ${
          entry.tier === "refuse"
            ? "border-rose-300 bg-rose-50 text-rose-800"
            : "border-2 border-slate-400 bg-slate-100 text-slate-900"
        }`}
      >
        {entry.checkId} · {entry.tier === "refuse" ? t("거부 등급", "refuse", "拒否") : t("경고 등급", "warn", "警告")}
      </span>
      <SubjectLabel subject={entry.finding.subject} className="mt-1 block text-[15px]" />
      <p className="mt-1 leading-relaxed">{entry.finding.message}</p>
      <p className="mt-1 font-mono text-[14px] text-slate-500">
        {entry.checkTitle.split(" — ")[0]} · {entry.finding.code}
      </p>
    </>
  );
}

export function EmptyState({ text }: { text: string }) {
  return (
    <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-[15px] text-slate-500">
      {text}
    </p>
  );
}

export function LegendToggle({
  color,
  label,
  checked,
  dashed = false,
  onChange,
}: {
  color: string;
  label: string;
  checked: boolean;
  /** Draw the swatch as a DASHED bar rather than a dot. The warn tier is mono
   * now, so a round swatch in near-white would be indistinguishable from the
   * normal-link swatch — the legend has to show the same distinction the map
   * draws, or it stops being a legend. */
  dashed?: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-1.5">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {dashed ? (
        <svg width="16" height="10" viewBox="0 0 16 10" aria-hidden="true">
          <line
            x1="0"
            y1="5"
            x2="16"
            y2="5"
            stroke={color}
            strokeWidth="3"
            strokeDasharray={WARN_DASH}
          />
        </svg>
      ) : (
        <span
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: color }}
          aria-hidden="true"
        />
      )}
      {label}
    </label>
  );
}

/**
 * WHAT A CLICK SAYS — one panel, shared by both renderers.
 *
 * Three sections, and the last two are DOCTRINE rather than decoration:
 *   * the clicked element's findings, ALL of them;
 *   * 제외된 구역 — the zones `build_draft` removed, listed BY NAME with no
 *     map position, because they were excluded before the final coordinates
 *     were fixed and no honest pin exists for them (lib/genesisArtifacts.ts's
 *     header comment carries the mechanism);
 *   * the findings whose subject resolved to no geometry at all, also listed by
 *     name. Neither list may be dropped when the renderer changes: a map that
 *     silently omits what it cannot draw is the exact failure this product
 *     refuses.
 *
 * Type sizes follow the Console pass (U-1 second elaboration item 13): body
 * 13px, secondary 12px muted, nothing smaller.
 */
export function FindingsDetailPanel({
  selected,
  onClear,
  unresolved,
  unresolvedCount,
  onSelectUnresolved,
  excludedZones,
  onSelectExcludedZone,
  t,
}: {
  selected: SelectedItem;
  onClear: () => void;
  unresolved: FindingRef[];
  unresolvedCount: number;
  onSelectUnresolved: (ref: FindingRef) => void;
  excludedZones: GenesisIsolatedZone[];
  onSelectExcludedZone: (zone: GenesisIsolatedZone) => void;
  t: Translate;
}) {
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="min-h-[140px] rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-[15px] text-slate-700">
        {!selected && (
          <p className="text-[14px] text-slate-500">
            {t(
              "링크나 커넥터를 클릭하면 여기에 상세 정보가 나타납니다.",
              "Click a link or connector to see its detail here.",
              "リンクやコネクタをクリックすると、ここに詳細が表示されます。",
            )}
          </p>
        )}
        {/* U-19: the element itself, clicked directly on the map -- every
          * link/connector is reachable this way now, so `refs` is often
          * empty and the panel says so plainly rather than looking broken.
          * The sky badge is this app's selection/info accent (THREE-COLOR
          * rule), matching the sky stroke the map itself draws on the
          * clicked feature. */}
        {selected?.kind === "element" && (
          <>
            <div className="flex items-center justify-between">
              <span className="rounded-full border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-[14px] font-bold uppercase text-slate-800">
                {selected.elementKind === "link" ? t("링크", "Link", "リンク") : t("커넥터", "Connector", "コネクタ")}
              </span>
              <button type="button" onClick={onClear} className="text-[14px] text-slate-500">
                {t("닫기", "Close", "閉じる")}
              </button>
            </div>
            {/* Unit #41 -- road name first, the id small behind it. The name
              * is the census's own; `selected.id` is what the machine calls
              * it, and a nameless element falls back to exactly the bare id
              * this line used to lead with. */}
            <SubjectLabel
              subject={selected.name}
              id={selected.id}
              className="mt-1.5 block text-[15px]"
            />
            {selected.refs.length === 0 ? (
              <p className="mt-1.5 text-[14px] text-slate-500">
                {t("확인된 소견 없음", "No findings on this element", "確認された所見なし")}
              </p>
            ) : (
              <ul className="mt-1.5 space-y-2">
                {selected.refs.map((ref, index) => (
                  <li
                    key={`${ref.checkId}-${ref.finding.code}-${index}`}
                    className={index > 0 ? "border-t border-slate-200 pt-1.5" : undefined}
                  >
                    <FindingRefLine entry={ref} t={t} />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {selected?.kind === "finding" && (
          <>
            <div className="flex items-center justify-between">
              <span className="text-[14px] font-bold uppercase text-slate-500">
                {selected.refs.length > 1
                  ? t(
                      `이 요소의 발견 ${selected.refs.length}건`,
                      `${selected.refs.length} findings on this element`,
                      `この要素の発見 ${selected.refs.length} 件`,
                    )
                  : t("이 요소의 발견", "Finding on this element", "この要素の発見")}
              </span>
              <button type="button" onClick={onClear} className="text-[14px] text-slate-500">
                {t("닫기", "Close", "閉じる")}
              </button>
            </div>
            {/* EVERY finding on the clicked element, not just the first --
              * one connector can carry three separate V4 findings at once,
              * and collapsing them to one used to make the other two
              * invisible without any sign they existed. */}
            <ul className="mt-1.5 space-y-2">
              {selected.refs.map((ref, index) => (
                <li
                  key={`${ref.checkId}-${ref.finding.code}-${index}`}
                  className={index > 0 ? "border-t border-slate-200 pt-1.5" : undefined}
                >
                  <FindingRefLine entry={ref} t={t} />
                </li>
              ))}
            </ul>
            {/* Unit #41 -- the MEASURED departure point, when the payload
              * carried one. Named as what it is (the source's own node
              * coordinate) so nobody reads the sky ring on the map as a
              * position this client computed. */}
            {selected.point && (
              <p className="mt-1.5 font-mono text-[14px] text-slate-500">
                {selected.point.label} · {selected.point.lat.toFixed(6)}, {selected.point.lon.toFixed(6)}
              </p>
            )}
          </>
        )}
        {selected?.kind === "excluded_zone" && (
          <>
            <div className="flex items-center justify-between">
              <span className="rounded-full border border-slate-300 bg-slate-100 px-1.5 py-0.5 text-[14px] font-bold uppercase text-slate-700">
                {t("제외된 구역", "Excluded zone", "除外されたゾーン")}
              </span>
              <button type="button" onClick={onClear} className="text-[14px] text-slate-500">
                {t("닫기", "Close", "閉じる")}
              </button>
            </div>
            <p className="mt-1.5 font-semibold text-slate-800">{selected.zone.representative}</p>
            <p className="mt-1 font-mono text-[14px] text-slate-500">{selected.zone.reason}</p>
            <p className="mt-1 leading-relaxed">{selected.zone.detail}</p>
            <p className="mt-1.5">
              {t("링크", "Links", "リンク")} {selected.zone.links} · {t("OSM way", "OSM ways", "OSM way")} {selected.zone.ways}
            </p>
            <p className="mt-1">{selected.zone.street_names.join(", ")}</p>
            <p className="mt-1.5 rounded border border-slate-300 bg-white/[0.04] px-1.5 py-1 text-[14px] text-slate-600">
              {t(
                "이 구역은 최종 좌표가 확정되기 전에 제외되어, 지도에 그릴 좌표가 없습니다.",
                "This zone was excluded before the final coordinates were fixed -- it has no coordinates left to draw.",
                "このゾーンは最終座標が確定する前に除外されたため、地図に描く座標がありません。",
              )}
            </p>
          </>
        )}
      </div>

      {excludedZones.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-surface/90 px-3 py-2.5">
          <p className="text-[15px] font-bold text-slate-800">
            {t("제외된 구역", "Excluded zones", "除外されたゾーン")} ({excludedZones.length})
          </p>
          <p className="mt-0.5 text-[14px] text-slate-500">
            {t(
              "지도에 좌표가 없어 목록으로만 보여줍니다 — 이름을 누르면 사유가 나타납니다.",
              "No coordinates survive for these -- listed by name only; click one for the reason.",
              "座標が残っていないため一覧のみで表示します — 名前を押すと理由が表示されます。",
            )}
          </p>
          <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto">
            {excludedZones.map((zone, index) => (
              <li key={`${zone.representative}-${index}`}>
                <button
                  type="button"
                  onClick={() => onSelectExcludedZone(zone)}
                  className="w-full truncate rounded border border-slate-200 bg-slate-50 px-2 py-1 text-left text-[14px] text-slate-700"
                  title={zone.street_names.join(", ")}
                >
                  <SubjectLabel subject={zone.representative} className="text-[14px]" /> · {zone.links}
                  {t("개 링크", " links", "リンク")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {unresolvedCount > 0 && (
        <div className="rounded-lg border-2 border-slate-400 bg-slate-100 px-3 py-2.5 text-[15px] text-slate-900">
          <p className="font-semibold">
            {t(
              `지도에 표시하지 못한 발견 ${unresolvedCount}건`,
              `${unresolvedCount} finding(s) could not be placed on the map`,
              `地図に表示できなかった発見 ${unresolvedCount} 件`,
            )}
          </p>
          <p className="mt-0.5 text-[14px]">
            {t(
              "게이트 결과가 이 항목들의 위치 참조를 전달하지 않습니다 — 위 판정 목록에서 문구는 그대로 확인할 수 있습니다.",
              "The gate result does not carry a location reference for these -- their wording is still in the check list above.",
              "ゲート結果にはこれらの位置参照が含まれていません — 上の判定リストで文言は確認できます。",
            )}
          </p>
          <ul className="mt-1.5 max-h-32 space-y-1 overflow-y-auto">
            {unresolved.map((ref, index) => (
              <li key={`${ref.checkId}-${ref.finding.code}-${index}`}>
                <button
                  type="button"
                  onClick={() => onSelectUnresolved(ref)}
                  className="w-full truncate rounded border border-slate-400 bg-white/[0.04] px-2 py-1 text-left text-[14px]"
                >
                  {ref.checkId} · <SubjectLabel subject={ref.finding.subject} className="text-[14px]" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

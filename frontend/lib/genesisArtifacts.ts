// Roadmap v2 §5.B B3 (완결) -- the gate's verdict gets a map, and a generation
// gets a prior generation to be measured against.
//
// Everything below reads ONE more artifact the genesis page did not read yet:
// `GET /api/genesis/jobs/{id}/artifact/genesis_json` -- the draft network as
// data (`services/genesis/draft.py`'s `DraftNetwork.model_dump(mode="json")`).
// The shapes here are NOT re-derived from the pydantic source by inspection;
// they were checked against a REAL file, produced offline from the cached
// `client-data/murdoch.osm` with the same two backend tools a consultant runs
// by hand (`backend/tools/genesis_osm_draft.py` then
// `backend/tools/genesis_validation_gate.py --json`), never against a live
// job (this unit may not touch `backend/`, and a live run would also write
// into a real workspace's runtime/ tree, which is not this unit's to do
// either). The fields below are exactly what that run wrote to disk.
//
// THE ONE THING THIS FILE CANNOT DO HONESTLY: draw an EXCLUDED zone's
// geometry. `build_draft` (services/genesis/draft.py, `MAX_ISOLATION_ROUNDS`
// loop) removes an excluded pocket's ways and then REBUILDS the whole network
// from scratch -- a smaller extent moves the projection origin, which moves
// every coordinate -- so the ids in `metadata.connectivity.excluded_zones[].
// link_ids` name links from a draft that was thrown away, not links in the
// file this page can draw. `isolated_zones_retained` is the other list the
// same round produces and it is NOT the same claim: a retained zone is one
// this generator's own connectors left isolated, so R1.5 V3 refuses on it
// rather than letting it be silently dropped, and it stays IN the returned
// draft -- its `link_ids` genuinely are ids in `links` below. See
// `resolveFindingHighlight`'s doc comment for how that distinction is used.
//
// THE OTHER HONEST GAP: a gate finding's `subject` (GenesisGateFinding, see
// lib/api.ts) is a plain string, not a structured reference --
// `services/genesis/gate.py`'s `gate_payload` ships `{code, subject, message}`
// per finding and drops the `context` dict that actually carries a bare
// link/connector id. The full subject-shape inventory, measured by reading
// every `GateFinding(...)` call site in gate.py (not sampled from one report):
//   V1  a filename (`Path(oracle.network_path).name`) or a joined filename
//       list -- never a link/connector id at all.
//   V2  a reference-path string (`"<parent>/<tag>[<id>]/@<attr>"`) for every
//       code except `v2_connector_endpoint_dangling`, whose subject is a
//       free-text sentence from `genesis.diff._dangling_references` -- neither
//       shape is an id this file can look up.
//   V3  `_label()` (id, or `"<id> (<name>)"` when the link is named) for every
//       code except `v3_silent_departure_point`, which writes the link's own
//       NAME first and only falls back to its bare id when the link has none
//       (`str(entry.get("link") or entry.get("link_id") or "?")`, gate.py:1820
//       -- `entry["link"]` is literally `link.name`, draft.py:2386).
//   V4  three codes (`v4_more_mapped_lanes_than_link_has`,
//       `v4_lane_reference_malformed`, `v4_lane_index_outside_link`) write a
//       bare CONNECTOR id with no name at all; `v4_lane_drop_without_runway`
//       uses `_label()` on a link.
//   V5  three HEAD-id codes (`v5_head_signal_group_malformed`,
//       `v5_head_without_controller`, `v5_head_without_signal_group`) write a
//       bare signal-head number -- a THIRD id space, dense from 1 same as
//       links and connectors, and never resolvable against either;
//       `v5_signal_group_without_head` writes a `"{controller} {group}"` pair
//       string; `v5_approach_reaches_no_head_in_node` uses `_label()`.
//   V6  eight codes -- four composition-number codes (`v6_composition_empty`,
//       `v6_composition_relflow_sum_zero`, `v6_composition_relflow_negative`,
//       and warn-tier `v6_composition_unused`, added 2026-08-12) and four
//       vehicle-input-number codes (`v6_input_without_volume`,
//       `v6_input_volume_is_zero`, `v6_input_composition_missing`,
//       `v6_input_without_routing_coverage`) -- write a bare id from a FOURTH
//       and FIFTH id space, also dense from 1, also never resolvable against a
//       link or connector; `v6_link_demand_over_capacity` uses `_label()`.
//   V7  `v7_link_without_geometry` writes a bare id straight from the shared
//       link-or-connector namespace (`item.id`, where `item` ranges over both)
//       -- the one bare-numeric code that is safe by construction; every other
//       V7 code uses `_label()` on that same shared namespace.
//   V8  emits no findings at all (gaps only).
//   V9  `_label()` throughout.
//
// So a bare all-digit subject is NOT proof of a link/connector id -- V5's head
// numbers and V6's composition/input numbers are just as dense and start from
// 1, so a bare-numeric fallback applied to every code WILL collide with a real
// link or connector id and highlight the wrong element. `resolveFindingHighlight`
// therefore takes the finding's `code` too, and only trusts a bare-numeric
// subject for the codes enumerated above as actually carrying one (see
// `BARE_ID_RESOLVABLE_CODES`) -- every other bare-numeric subject reports
// unresolved rather than guessing. The `"<id> (<name>)"` shape needs no such
// gate: nothing outside `_label()` writes that exact pattern, and `_label()`
// only ever wraps a link or connector id.

import {
  apiFetch,
  GenesisError,
  type GenesisGateCheck,
  type GenesisGateFinding,
  type GenesisGateReport,
  type GenesisJobStatus,
  type GenesisRequestedRegion,
} from "@/lib/api";
import { laneBoundaryOffsets, linkWidthMeters } from "@/lib/roadWidth";

// ---------------------------------------------------------------------------
// The draft network -- `*.genesis.json`, DraftNetwork.model_dump(mode="json")
// ---------------------------------------------------------------------------

/** One directional carriageway. Coordinates are the draft projection's local
 * tangent-plane METRES (+x east, +y north -- `services/genesis/project.py`),
 * relative to an origin private to this one generation. Plain 2D, no
 * georeference -- the same "not a map" disclaimer the region page states for
 * its own gauge applies here (`RegionGauge`'s own comment). +y is NORTH, so an
 * on-screen north-up render needs a y-FLIP -- see `fitNetwork` below. */
export interface GenesisDraftLink {
  id: number;
  name: string;
  osm_way_id: number;
  segment_index: number;
  forward: boolean;
  lanes: number;
  lane_width_m: number;
  points: [number, number][];
  highway: string;
  from_node_osm_id: number;
  to_node_osm_id: number;
  side_offset_m: number;
  setback_clamped?: boolean;
}

/** One movement through one intersection. */
export interface GenesisDraftConnector {
  id: number;
  name: string;
  node_osm_id: number;
  node_id: number;
  from_link: number;
  to_link: number;
  from_pos_m: number;
  to_pos_m: number;
  lanes: number;
  lane_width_m: number;
  points: [number, number][];
  turn: string;
  heading_change_deg: number;
  lane_change: string;
}

export interface GenesisDraftNode {
  id: number;
  name: string;
  osm_id: number;
  x: number;
  y: number;
  polygon: [number, number][];
}

/** `services/genesis/connectivity.py`'s `IsolatedZone.as_dict()`, verbatim. */
export interface GenesisIsolatedZone {
  reason: string;
  detail: string;
  links: number;
  link_ids: number[];
  ways: number;
  way_ids: number[];
  representative: string;
  street_names: string[];
}

export interface GenesisConnectivityMetadata {
  /** NO GEOMETRY -- see this file's header comment. Named-only. */
  excluded_zones?: GenesisIsolatedZone[];
  excluded_zones_by_reason?: Record<string, number>;
  excluded_zone_links?: number;
  excluded_zones_unresolved?: string;
  /** HAS geometry -- every id in every zone's `link_ids` is a real id in this
   * same payload's `links`. */
  isolated_zones_retained?: GenesisIsolatedZone[];
  [key: string]: unknown;
}

export interface GenesisDraftNetwork {
  name: string;
  source_osm: string;
  traffic_regulation?: string;
  refused: boolean;
  refusals: string[];
  metadata: { connectivity?: GenesisConnectivityMetadata; [key: string]: unknown };
  links: GenesisDraftLink[];
  connectors: GenesisDraftConnector[];
  nodes: GenesisDraftNode[];
  skipped_ways?: Array<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// Road width -- U-19 round 2 (visual parity with the dashboard/network tab's
// width-scaled ribbons, per the user's direct request).
// ---------------------------------------------------------------------------
//
// FIRST CHECK, before touching anything else: does the census this file
// already fetches carry lane/width data? Yes -- both `GenesisDraftLink` and
// `GenesisDraftConnector` above carry `lanes` and `lane_width_m` on every
// element (verified against a REAL generation's artifact,
// `runtime/workspaces/bfc69251bd04/genesis/jobs/f57cf27e33f5/
// region_n36_3504_e127_3845.genesis.json` -- 1831 links + 3828 connectors,
// zero missing or zero/negative `lanes` or `lane_width_m` in that file). So
// no geojson contract change, no backend edit: the width this function needs
// was already loaded client-side for round 1's name lookups.
//
// THE ADAPTER, not a second formula. Neither genesis type carries
// `width_total` or `lane_widths` (`lib/roadWidth.ts`'s two richer input
// shapes) -- the generator emits one measured `lane_width_m` per element
// instead of a per-lane breakdown. So this builds a `lane_widths` array of
// that one value repeated `lanes` times and hands it to the SAME
// `linkWidthMeters` the dashboard/network tab uses: the summing and the
// lanes-only fallback both stay inside that one function. Nothing here
// multiplies lanes by width itself.
export function genesisElementWidthMeters(element: { lanes: number; lane_width_m: number }): number {
  const laneCount = Math.max(Math.round(element.lanes ?? 1), 1);
  return linkWidthMeters({ lane_widths: new Array(laneCount).fill(element.lane_width_m), lanes: element.lanes });
}

// ---------------------------------------------------------------------------
// Lane dividers -- unit #29 ("port lane markings to the genesis draft maps").
// ---------------------------------------------------------------------------
//
// THE SAME ADAPTER SHAPE as genesisElementWidthMeters above, and for the same
// reason: neither genesis type carries a `lane_widths` breakdown, only one
// measured `lane_width_m`, so this builds the `lane_widths` array
// `lib/roadWidth.ts`'s `laneBoundaryOffsets` expects and hands it off --
// nothing here recomputes an offset itself. A link with N lanes gets N-1
// interior boundary offsets (metres, signed from the centreline); a
// single-lane link or connector gets none. Named `...LaneOffsetsM` (not
// `...LaneBoundaryOffsets`) to read at a glance beside `...WidthMeters` as
// the SAME unit (metres), which both genesis map renderers then convert to
// their own view/pixel units the same way they already convert width.
export function genesisElementLaneOffsetsM(element: { lanes: number; lane_width_m: number }): number[] {
  const laneCount = Math.max(Math.round(element.lanes ?? 1), 1);
  return laneBoundaryOffsets({ lane_widths: new Array(laneCount).fill(element.lane_width_m), lanes: element.lanes });
}

/** `GET /jobs/{id}/artifact/genesis_json` -- authenticated, same as every
 * other artifact download (`routers/genesis.py`'s `download_artifact`: an
 * anonymous caller must not be able to read a network's geometry, refused or
 * not). `apiFetch` carries the session cookie and the workspace header for a
 * same-origin request the way every other genesis read in this app does
 * (`fetchGenesisJob` etc. in lib/api.ts) -- no new fetch pattern introduced
 * here. */
export async function fetchGenesisDraftNetwork(jobId: string): Promise<GenesisDraftNetwork> {
  const r = await apiFetch(`/api/genesis/jobs/${jobId}/artifact/genesis_json`, { cache: "no-store" });
  if (!r.ok) {
    const text = await r.text();
    let message = text || `${r.status} ${r.statusText}`.trim();
    try {
      const data = JSON.parse(text) as { detail?: unknown };
      if (typeof data.detail === "string" && data.detail.trim()) message = data.detail;
    } catch {
      // Plain text errors are still useful.
    }
    throw new GenesisError(message, r.status);
  }
  return r.json();
}

// ---------------------------------------------------------------------------
// The same draft in DEGREES -- U-1-map (docs/ux-backlog.md, item #1)
// ---------------------------------------------------------------------------
//
// The metres above are this generation's own local tangent plane and cannot be
// drawn over a basemap. `GET /api/genesis/jobs/{id}/artifact/geojson` inverts
// them SERVER-SIDE, on the plane the draft itself recorded
// (`services/genesis/geojson.py`), which is legitimate for exactly the reason a
// consultant's .inpx cannot be treated the same way: a genesis draft was BUILT
// from WGS84, so the inverse is algebra, not a fit.
//
// It is a SECOND fetch beside `fetchGenesisDraftNetwork`, deliberately, and the
// map makes both. Each answers something the other does not: the GeoJSON
// carries where the roads are on the earth; the census carries the doctrine
// facts a map must still show and cannot derive from geometry — the excluded
// zones (named, with no coordinates left to draw) and the retained isolated
// zones a finding expands into. Folding the census into the GeoJSON would make
// one artifact the source of two claims, which is how they start to disagree.

/** One link or connector as WGS84. `coordinates` is GeoJSON order --
 * `[lon, lat]`, NOT leaflet's `[lat, lon]`; every consumer flips it explicitly
 * rather than relying on remembering which library it is talking to. */
export interface GenesisGeoFeature {
  type: "Feature";
  id: string;
  geometry: { type: "LineString"; coordinates: [number, number][] };
  properties: { id: number; kind: "link" | "connector"; name: string };
}

export interface GenesisGeoJson {
  type: "FeatureCollection";
  features: GenesisGeoFeature[];
  /** The plane the server inverted with, carried so a reader can reproduce it
   * and so the map can centre on it before any feature is drawn. */
  origin: { lat: number; lon: number };
  /** `[west, south, east, north]`, standard GeoJSON. Absent for a draft with
   * no drawable geometry at all. */
  bbox?: [number, number, number, number];
}

/** `GET /jobs/{id}/artifact/geojson` -- authenticated and workspace-scoped like
 * every other genesis artifact read, through `apiFetch` for the same reason
 * (`fetchGenesisDraftNetwork`'s comment): a bare `fetch()` carries neither the
 * session cookie nor `X-Workspace-Id` and would silently read the default
 * project. A 409 here is the server REFUSING to place a draft that recorded no
 * projection origin -- the caller renders the fallback and says so, and must
 * never substitute an origin of its own. */
export async function fetchGenesisDraftGeoJson(jobId: string): Promise<GenesisGeoJson> {
  const r = await apiFetch(`/api/genesis/jobs/${jobId}/artifact/geojson`, { cache: "no-store" });
  if (!r.ok) {
    const text = await r.text();
    let message = text || `${r.status} ${r.statusText}`.trim();
    try {
      const data = JSON.parse(text) as { detail?: unknown };
      if (typeof data.detail === "string" && data.detail.trim()) message = data.detail;
      const detail = data.detail as { reasons?: unknown } | undefined;
      if (detail && Array.isArray(detail.reasons) && detail.reasons.length > 0) {
        message = String(detail.reasons[0]);
      }
    } catch {
      // Plain text errors are still useful.
    }
    throw new GenesisError(message, r.status);
  }
  return r.json();
}

// ---------------------------------------------------------------------------
// Finding -> geometry resolution (best-effort; see header comment)
// ---------------------------------------------------------------------------

/** `"<id> (<name>)"` -- `gate.py`'s own `_label()` format. Nothing outside
 * `_label()` writes this exact shape, and `_label()` only ever wraps a link or
 * connector id, so this pattern needs no code-level gate. */
const LABELED_SUBJECT_RE = /^(\d+)\s*\(/;
/** A bare id, nothing else. NOT proof of a link/connector id by itself -- see
 * `BARE_ID_RESOLVABLE_CODES` below and this file's header comment: V5's
 * signal-head numbers and V6's composition/input numbers are bare digits from
 * the SAME dense range and would collide silently if trusted for every code. */
const BARE_ID_RE = /^\d+$/;

/** Gate finding codes whose subject, reading gate.py's own `GateFinding(...)`
 * call sites end to end, is PROVEN to be a link or connector id whenever it is
 * a bare digit string -- either because the code never wraps it in a name at
 * all (the three V4 connector codes, and V7's `v7_link_without_geometry`,
 * which shares one namespace across links and connectors by construction), or
 * because it goes through `_label()`, which degrades to a bare id only when
 * the link/connector has no name and otherwise reads `"<id> (<name>)"` (caught
 * by `LABELED_SUBJECT_RE` above regardless of this allow-list). Every OTHER
 * code that can still produce a bare digit string -- V5's three head-id codes,
 * V6's four composition/input-id codes -- points into a namespace this file
 * cannot look up, and MUST resolve to `resolved: false` rather than risk
 * matching an unrelated link or connector by coincidence of id range. */
const BARE_ID_RESOLVABLE_CODES = new Set<string>([
  // Bare by construction -- never carry a name.
  "v4_more_mapped_lanes_than_link_has",
  "v4_lane_reference_malformed",
  "v4_lane_index_outside_link",
  "v7_link_without_geometry",
  // `_label()`-based -- bare only when the link/connector has no name.
  "v3_orphan_link",
  "v3_isolated_zone",
  "v3_unreachable_zone",
  "v3_trapped_zone",
  "v3_broken_zone",
  "v3_input_cannot_reach_an_exit",
  "v4_lane_drop_without_runway",
  "v5_approach_reaches_no_head_in_node",
  "v6_link_demand_over_capacity",
  "v7_zero_length_link",
  "v7_link_below_generator_floor",
  "v7_links_share_one_polyline",
  "v9_stop_line_without_queue_counter",
]);

export interface GenesisFindingHighlight {
  /** Link ids to draw as this finding's location. When the resolved link is a
   * member of a RETAINED isolated zone (metadata.connectivity.
   * isolated_zones_retained), every link of that zone is included -- one
   * link is where the finding's subject points; the whole zone is what "an
   * isolated pocket" actually looks like on a map. */
  linkIds: number[];
  connectorIds: number[];
  /** False when `subject` matched neither a link nor a connector by id or by
   * name -- an honest "could not place this on the map", never a silently
   * empty highlight. */
  resolved: boolean;
}

function findRetainedZoneFor(linkId: number, network: GenesisDraftNetwork): GenesisIsolatedZone | null {
  const zones = network.metadata.connectivity?.isolated_zones_retained ?? [];
  for (const zone of zones) {
    if (zone.link_ids.includes(linkId)) return zone;
  }
  return null;
}

/** Resolve one gate finding's `subject` string to network geometry. Tries, in
 * order: the leading numeric id in `"<id> (<name>)"`; a bare numeric subject
 * -- ONLY when `finding.code` is in `BARE_ID_RESOLVABLE_CODES`, since a bare
 * digit string alone is not proof of a link/connector id (see that constant's
 * doc comment); an exact name match. Links are tried before connectors at
 * every step (the two id spaces do not collide in a real generation --
 * verified: connector ids start at 10001 in the sample generation, link ids
 * run 1..1828 -- but links are tried first regardless, since most finding
 * codes are about links). Never throws; an unresolved subject comes back
 * `resolved: false`. */
export function resolveFindingHighlight(
  finding: Pick<GenesisGateFinding, "subject" | "code">,
  network: GenesisDraftNetwork,
): GenesisFindingHighlight {
  const subject = (finding.subject ?? "").trim();
  if (!subject) return { linkIds: [], connectorIds: [], resolved: false };

  const labeled = subject.match(LABELED_SUBJECT_RE);
  const bareAllowed = BARE_ID_RE.test(subject) && BARE_ID_RESOLVABLE_CODES.has(finding.code);
  const idCandidate = labeled ? labeled[1] : bareAllowed ? subject : null;
  if (idCandidate !== null) {
    const numericId = Number(idCandidate);
    const link = network.links.find((candidate) => candidate.id === numericId);
    if (link) {
      const zone = findRetainedZoneFor(link.id, network);
      return { linkIds: zone ? zone.link_ids : [link.id], connectorIds: [], resolved: true };
    }
    const connector = network.connectors.find((candidate) => candidate.id === numericId);
    if (connector) return { linkIds: [], connectorIds: [connector.id], resolved: true };
  }

  const byName = network.links.find((candidate) => candidate.name === subject);
  if (byName) {
    const zone = findRetainedZoneFor(byName.id, network);
    return { linkIds: zone ? zone.link_ids : [byName.id], connectorIds: [], resolved: true };
  }
  const connectorByName = network.connectors.find((candidate) => candidate.name === subject);
  if (connectorByName) return { linkIds: [], connectorIds: [connectorByName.id], resolved: true };

  return { linkIds: [], connectorIds: [], resolved: false };
}

// ---------------------------------------------------------------------------
// Prior-generation comparison -- gate results + summary stats ONLY.
// ---------------------------------------------------------------------------
//
// Deliberately not a geometry diff. `services/genesis/diff.py` (benchmark.py's
// domain, per this unit's own brief) already owns comparing two networks'
// actual topology; re-implementing any part of that client-side, over data
// this page was never given (no from/to endpoints, no signal coverage), would
// be a second, drifting copy of a comparison the backend already does
// properly. What CAN be compared honestly from what this page already fetches
// (GenesisJob + GenesisGateReport, both per job) is the gate's own verdict
// and finding set, plus the job record's own network summary -- so that is
// the whole of it, and the UI names that scope rather than implying more.

export type GenesisCheckDelta = {
  id: string;
  baseTier: GenesisGateCheck["tier"] | null;
  targetTier: GenesisGateCheck["tier"] | null;
  baseState: GenesisGateCheck["state"] | null;
  targetState: GenesisGateCheck["state"] | null;
  stateChanged: boolean;
  /** Refuse/warn-tier findings (`check.findings`) present in target but not in
   * base, matched by `code + subject` (the only stable identity a finding
   * carries across two reports) as a MULTISET -- base=[K], target=[K,K] shows
   * one added, not zero, because gate.py can legitimately emit the same
   * code+subject twice for one check (V2's per-side connector findings, for
   * one). A plain Set-based diff would show +0/-0 for that case and silently
   * undercount. */
  added: GenesisGateFinding[];
  removed: GenesisGateFinding[];
  unchangedCount: number;
  /** The SAME multiset diff, over `check.warnings` (warn-tier findings a
   * refuse-tier check still surfaces) -- a separate column, never folded into
   * `added`/`removed` above, because a warn-tier change is not the same claim
   * as a refuse-tier one and the UI must be able to show them apart. */
  addedWarnings: GenesisGateFinding[];
  removedWarnings: GenesisGateFinding[];
  unchangedWarningsCount: number;
  /** Either side's report may have capped the findings/warnings it shipped
   * (`findings_omitted`/`warnings_omitted` on the check) -- when any of the
   * four below is nonzero the corresponding added/removed lists are a lower
   * bound, and the UI must say so rather than imply a complete diff. */
  baseFindingsOmitted: number;
  targetFindingsOmitted: number;
  baseWarningsOmitted: number;
  targetWarningsOmitted: number;
  /** True when either side's findings OR warnings for this check repeated the
   * same `code + subject` more than once. The added/removed counts above stay
   * exact even so (they are computed as a multiset), but the UI discloses this
   * so a reader does not assume `code + subject` is always a unique key. */
  duplicateKeysPresent: boolean;
};

function findingKey(finding: GenesisGateFinding): string {
  return `${finding.code} ${finding.subject}`;
}

/** Multiset diff: counts each `code + subject` occurrence on both sides and
 * reports the POSITIVE difference as added/removed, rather than a Set-based
 * membership test that treats a second identical occurrence as a no-op. See
 * `GenesisCheckDelta.added`'s doc comment for why this matters -- gate.py does
 * emit duplicate `code + subject` pairs within one check's finding list. */
function diffFindingLists(
  base: GenesisGateFinding[],
  target: GenesisGateFinding[],
): { added: GenesisGateFinding[]; removed: GenesisGateFinding[]; unchangedCount: number; duplicateKeyPresent: boolean } {
  const baseCounts = new Map<string, number>();
  const baseSample = new Map<string, GenesisGateFinding>();
  for (const finding of base) {
    const key = findingKey(finding);
    baseCounts.set(key, (baseCounts.get(key) ?? 0) + 1);
    if (!baseSample.has(key)) baseSample.set(key, finding);
  }
  const targetCounts = new Map<string, number>();
  const targetSample = new Map<string, GenesisGateFinding>();
  for (const finding of target) {
    const key = findingKey(finding);
    targetCounts.set(key, (targetCounts.get(key) ?? 0) + 1);
    if (!targetSample.has(key)) targetSample.set(key, finding);
  }

  const added: GenesisGateFinding[] = [];
  const removed: GenesisGateFinding[] = [];
  let unchangedCount = 0;
  let duplicateKeyPresent = false;
  for (const key of new Set([...baseCounts.keys(), ...targetCounts.keys()])) {
    const baseCount = baseCounts.get(key) ?? 0;
    const targetCount = targetCounts.get(key) ?? 0;
    if (baseCount > 1 || targetCount > 1) duplicateKeyPresent = true;
    unchangedCount += Math.min(baseCount, targetCount);
    if (targetCount > baseCount) {
      const sample = targetSample.get(key)!;
      for (let i = 0; i < targetCount - baseCount; i += 1) added.push(sample);
    } else if (baseCount > targetCount) {
      const sample = baseSample.get(key)!;
      for (let i = 0; i < baseCount - targetCount; i += 1) removed.push(sample);
    }
  }
  return { added, removed, unchangedCount, duplicateKeyPresent };
}

/** Per-check (V1-V9) delta between two stored gate reports. A check absent
 * from one side (should not happen -- the gate always runs all nine -- but a
 * report from a differently-versioned gate is not impossible) carries `null`
 * for that side's tier/state rather than being silently skipped. */
export function compareGenesisChecks(
  base: GenesisGateReport,
  target: GenesisGateReport,
): GenesisCheckDelta[] {
  const baseById = new Map(base.checks.map((check) => [check.id, check]));
  const targetById = new Map(target.checks.map((check) => [check.id, check]));
  const ids = Array.from(new Set([...baseById.keys(), ...targetById.keys()])).sort();
  return ids.map((id) => {
    const b = baseById.get(id) ?? null;
    const t = targetById.get(id) ?? null;
    const findingsDiff = diffFindingLists(b?.findings ?? [], t?.findings ?? []);
    const warningsDiff = diffFindingLists(b?.warnings ?? [], t?.warnings ?? []);
    return {
      id,
      baseTier: b?.tier ?? null,
      targetTier: t?.tier ?? null,
      baseState: b?.state ?? null,
      targetState: t?.state ?? null,
      stateChanged: (b?.state ?? null) !== (t?.state ?? null),
      added: findingsDiff.added,
      removed: findingsDiff.removed,
      unchangedCount: findingsDiff.unchangedCount,
      addedWarnings: warningsDiff.added,
      removedWarnings: warningsDiff.removed,
      unchangedWarningsCount: warningsDiff.unchangedCount,
      baseFindingsOmitted: b?.findings_omitted ?? 0,
      targetFindingsOmitted: t?.findings_omitted ?? 0,
      baseWarningsOmitted: b?.warnings_omitted ?? 0,
      targetWarningsOmitted: t?.warnings_omitted ?? 0,
      duplicateKeysPresent: findingsDiff.duplicateKeyPresent || warningsDiff.duplicateKeyPresent,
    };
  });
}

/** Summary-stat deltas the comparison panel shows beside the check table.
 * `excludedZones`/`retainedZones` are only present when the caller has fetched
 * BOTH sides' `genesis_json` (they are not on the job record or the gate
 * report -- only in the draft artifact's own metadata), so they are optional
 * on purpose rather than a field the UI can assume is always there. */
export interface GenesisNetworkStat {
  label: string;
  base: number | null;
  target: number | null;
}

export function buildNetworkStatDeltas(
  base: { links: number; connectors: number; nodes: number } | null,
  target: { links: number; connectors: number; nodes: number } | null,
  baseExcluded: number | null,
  targetExcluded: number | null,
): GenesisNetworkStat[] {
  return [
    { label: "links", base: base?.links ?? null, target: target?.links ?? null },
    { label: "connectors", base: base?.connectors ?? null, target: target?.connectors ?? null },
    { label: "nodes", base: base?.nodes ?? null, target: target?.nodes ?? null },
    { label: "excluded_zones", base: baseExcluded, target: targetExcluded },
  ];
}

// ---------------------------------------------------------------------------
// Shared job-record wording -- ONE ko/en/ja source, used by both the main
// genesis page (the job history list) and `GenesisComparisonPanel` (the
// "compare against" picker). Living here rather than duplicated in each
// caller is what stops the picker from ever showing a raw `job.status` or a
// raw ISO `job.created_at` next to the history list's already-translated
// versions of the exact same fields.
// ---------------------------------------------------------------------------

/** The four terminal states, each said in its own words -- `provisional`
 * deliberately does NOT read as success: it means the network was registered
 * AND a refuse-tier check could not run, which is a different thing from a
 * network the gate has cleared. */
export function statusLabel(status: GenesisJobStatus, t: (ko: string, en: string, ja: string) => string): string {
  switch (status) {
    case "queued":
      return t("대기 중", "Queued", "待機中");
    case "running":
      return t("생성 중", "Generating", "生成中");
    case "refused":
      // Shortened to the verdict word alone (user, live pass 2026-08-14): the
      // not-registered consequence reads in the detail page's help drawer, and
      // a chip that wraps its own clause was the table's original layout bug.
      return t("거부됨", "REFUSED", "拒否");
    case "provisional":
      return t("잠정 등록 — 미확인 검사 있음", "PROVISIONAL — a check could not run", "暫定登録 — 未確認の検査あり");
    case "published":
      return t("등록 완료 — 게이트 통과", "PUBLISHED — gate passed", "登録完了 — ゲート通過");
    case "error":
      return t("네트워크를 만들지 못함", "No network could be built", "ネットワークを生成できません");
    case "checked":
      // Unit #33. Deliberately not a verdict word: this job produced V1's
      // evidence and the verdict it changed sits on the PARENT. A label like
      // "통과" here would claim a judgement this job never made.
      return t("실 Vissim 확인 완료", "LIVE CHECK DONE", "実Vissim確認 完了");
    default:
      return status;
  }
}

/** At most six decimals, trailing zeros dropped — `115.836` stays `115.836`
 * and a float that arrived as `-32.08120000000001` prints as `-32.0812`. Six
 * decimals is ~0.1 m at these latitudes, finer than any region a human drew or
 * typed, so this rounds away float noise without rounding away a coordinate. */
function formatCoordinate(value: number): string {
  return String(Number(value.toFixed(6)));
}

/** The 생성 이력 row's answer to "which area is this generation about?".
 *
 * THREE ANSWERS, NEVER TWO — the whole reason `GenesisJob.requested_region` is
 * `undefined | null | object` rather than a nullable field (see its own doc
 * comment in lib/api.ts):
 *   * `undefined` → "—". The record predates the field; this app knows nothing
 *     about its region and must not go looking for one to display.
 *   * `null` → "업로드". The runner DID read the generation's own frozen .osm
 *     and found no fetch provenance, which is exactly what a file somebody
 *     uploaded looks like. This is a measurement, not a guess.
 *   * an object → the rectangle the build was judged against, and — for a
 *     lasso — the ring's VERTEX COUNT beside it, because the row states that a
 *     polygon was drawn and that the box is what the generator used, which is
 *     the one thing about a polygon fetch a reader most needs to know.
 */
export function requestedRegionLabel(
  region: GenesisRequestedRegion | null | undefined,
  t: (ko: string, en: string, ja: string) => string,
): string {
  if (region === undefined) return "—";
  if (region === null) return t("업로드", "Uploaded file", "アップロード");

  const box = region.bbox;
  const boxText = box
    ? `${formatCoordinate(box.south)}, ${formatCoordinate(box.west)} ~ ${formatCoordinate(box.north)}, ${formatCoordinate(box.east)}`
    : null;
  if (region.polygon_vertices && region.polygon_vertices > 0) {
    const drawn = t(
      `폴리곤 ${region.polygon_vertices}점`,
      `Polygon, ${region.polygon_vertices} vertices`,
      `ポリゴン ${region.polygon_vertices} 点`,
    );
    return boxText ? `${drawn} + bbox ${boxText}` : drawn;
  }
  // An object with neither shape cannot be produced by the runner, but a
  // hand-edited record must not render as a blank cell.
  return boxText ?? "—";
}

/** `links · connectors · nodes`, or "—" for a generation that never got far
 * enough to have any. Never zero-filled: a job that failed before a draft
 * existed has NO counts, and "0 links" is a different claim from "not built". */
export function networkScaleLabel(
  network: { links: number; connectors: number; nodes: number } | null,
  t: (ko: string, en: string, ja: string) => string,
): string {
  if (!network) return "—";
  return `${t("링크", "links", "リンク")} ${network.links} · ${t("커넥터", "connectors", "コネクタ")} ${network.connectors} · ${t("노드", "nodes", "ノード")} ${network.nodes}`;
}

export function formatTimestamp(iso: string | null, lang: "ko" | "en" | "ja"): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const locale = lang === "ko" ? "ko-KR" : lang === "ja" ? "ja-JP" : "en-US";
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

// ---------------------------------------------------------------------------
// Unit #23 -- moved here from app/genesis/page.tsx when the drawer became its
// own route (app/genesis/jobs/[id]/page.tsx). Both the hub's history table
// AND the detail page's own header now need to colour a job's status and to
// know whether a job is still running -- one copy each, joining `statusLabel`
// above for the same reason it already lives here: two independently-drifting
// copies is how a hub row and a detail header end up disagreeing about
// whether a job is finished.
// ---------------------------------------------------------------------------

/** How often a running job is re-read. Generation is seconds-to-a-minute of
 * pure Python (no COM, no Vissim), so a one-second poll is both cheap and
 * fast enough to feel live. */
export const POLL_INTERVAL_MS = 1000;
//: A terminal job is still re-gatable (attach_live_check), so it keeps being
//: observed -- just slowly. 15 s is chosen against what it is watching for: a
//: human on another machine running the live probe and posting its state.json,
//: an action measured in minutes. Fast enough that nobody stares at a verdict
//: that has already been overturned, slow enough to be invisible in the logs.
export const TERMINAL_RECHECK_MS = 15000;

export const TERMINAL: GenesisJobStatus[] = [
  "refused",
  "provisional",
  "published",
  "error",
  // Unit #33: a live-check job's own end state. Terminal here for the same
  // reason it is terminal in the backend's TERMINAL_STATUSES -- nothing will
  // advance it -- so a page polling one stops hammering the API at 1 Hz.
  "checked",
];

export function isTerminal(status: GenesisJobStatus): boolean {
  return TERMINAL.includes(status);
}

/**
 * THREE COLOURS, AND THE FOURTH STATE IS MONO. (User decision, 2026-08-13.)
 *
 * This screen may use exactly three accents and no others:
 *   rose    — fail / refused
 *   emerald — pass / published
 *   sky     — selection, info, links
 *
 * Everything that used to be AMBER — `not_checkable`, `provisional`, warn-tier
 * findings, a verdict resting on an attestation — is now MONO: ink-strength
 * text inside a heavier (2px) neutral border, or a filled neutral chip. It is
 * told apart by WEIGHT and SHAPE, never by a fourth hue.
 *
 * WHY THAT IS SAFE, and it is the only question that matters here. R1.5's rule
 * is that a check which could not RUN must never read as fine. What made the
 * old amber unsafe was that globals.css remaps the whole amber family to
 * NEUTRAL for this theme — so the state that most needed to stand out was
 * already rendering grey, at the SAME weight as ordinary chrome. Mono with a
 * doubled border, beside a green pass chip, is louder than that was, and the
 * word (확인 불가 / 잠정) is printed either way. A hue was dropped; no claim was.
 *
 * TINTED BOX = TINT IN THE BACKGROUND AND THE BORDER, INK IN THE TEXT — the
 * other half of the rule, and the contrast bug it fixes (U-1 second
 * elaboration item 11, plus the region page's blue-on-blue box reported the
 * same day). globals.css remaps the Tailwind ramps for dark, but only the
 * shades this app actually used: `bg-emerald-50` became a DARK green (#1e2b22)
 * while the emerald 900 foreground was never remapped and stayed Tailwind's
 * default #064e3b — dark green on dark green. Same trap for sky's 700/800/900
 * over `bg-sky-50` (#032042). So a tinted box takes its foreground from the
 * remapped LIGHT end of its own family: emerald-800 (#b0d5b6), rose-800
 * (#ecac9c), sky-600 (#6da7ec — literally the Console badge pairing the
 * palette lock names for this navy). Borders come from shades the remap covers
 * (`*-200`/`*-300`), never from `*-400+` of a tinted family.
 */
export function statusClasses(status: GenesisJobStatus): string {
  switch (status) {
    case "published":
      return "border-emerald-300 bg-emerald-50 text-emerald-800";
    case "provisional":
    // `checked` takes the MONO treatment too, and for the same reason
    // `provisional` does: it is not a pass. A live-check job's own end state
    // says "the evidence was produced", never "the network is good" -- the
    // green, if any is earned, belongs to the parent's chip.
    case "checked":
      return "border-slate-400 bg-slate-100 text-slate-900";
    case "refused":
    case "error":
      return "border-rose-300 bg-rose-50 text-rose-800";
    default:
      return "border-sky-200 bg-sky-50 text-sky-600";
  }
}

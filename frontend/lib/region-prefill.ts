// Unit #40, re-aimed by unit #48-b -- the ONE hand-off between a boundary-caused
// refusal on the job screen and the region page.
//
// WHAT TRAVELS HERE CHANGED ON 2026-08-20 (user decision). Until then this
// module carried a DERIVED shape: the user's own lasso with a finger spliced
// into every ring edge a severed corridor crossed, sized by the backend to cover
// a measured runway deficit (#40) or the metres the ring cut off a way (#48).
// Live use measured that geometry out. Every widened boundary cuts NEW roads
// somewhere else, so the composite "widen for all of them and generate again"
// button was pressed over and over and the boundary findings MOVED rather than
// closed (jobs 7f263915d770 and e2f654400d21 under
// `runtime/workspaces/bfc69251bd04/genesis/jobs/`). The user chose manual region
// editing instead, and the automatic proposal UI was removed with it.
//
// So this hand-off now carries the job's OWN REQUESTED POLYGON, unchanged, plus
// the MEASURED evidence of what the boundary did -- which finding, which way,
// which boundary node, how many metres. Never a ring this app decided on the
// user's behalf. The backend's derivation machinery is retained and simply
// un-exposed (`services/genesis_jobs.derive_v4_boundary_extension`,
// `derive_boundary_extension_for_findings`), so nothing here has to pretend the
// measurement is gone -- only that the shape is the human's to draw.
//
// WHY sessionStorage AND NOT A URL PARAMETER, since that is the choice this
// module exists to make:
//   * the region page deliberately holds NO URL state ("no useSearchParams, no
//     router.replace", its own comment) so that a deep link or a browser Back
//     lands on its documented initial state rather than a half-restored one. A
//     query parameter would take that property away from it, and would need a
//     Suspense boundary for `useSearchParams` on a page that has no other
//     reason for one -- exactly the cost that page already declined to pay for
//     a job id;
//   * a polygon is up to 64 lat/lon pairs. In a URL it is a wall of digits a
//     user can edit into a shape nobody drew, and a bookmark of it would
//     re-open a stale region against a job that may have been re-gated since;
//   * `sessionStorage` is per-tab and this key is ONE-SHOT (`takeRegionPrefill`
//     reads and removes in the same call), so the hand-off survives exactly one
//     navigation and a refresh afterwards lands on the empty page the region
//     screen promises. `components/ProjectSelector.tsx` already uses
//     sessionStorage for a one-shot flag, so this is the app's existing shape
//     for "carry one fact across exactly one navigation".
//
// Everything read back out is RE-VALIDATED below. The writer is this app, but
// the store is a string a user can edit in devtools, and a NaN handed to Leaflet
// throws inside the tile layer -- the region page's own `VertexRow` states that
// rule for typed coordinates and it holds here for stored ones.
//
// AND THE PAYLOAD IS STAMPED WITH THE 프로젝트 IT WAS TAKEN FROM. One-shot is
// not the same as same-project: `sessionStorage` is per-TAB and survives the
// `window.location.reload()` that `components/ProjectSelector.tsx` switches
// projects with, so a write that never got consumed (the navigation lost a race
// with a project switch, or with the picker's own workspace_forbidden reload)
// would still be sitting there when the region page next mounts -- under a
// DIFFERENT project, whose extract button would then fetch project A's polygon
// into project B under a banner naming a job that does not exist there. The
// workspace id travels with the payload and `takeRegionPrefill` refuses (and
// still removes) a mismatch: a job id alone cannot be checked, because ids are
// per-workspace and nothing on this page can tell whose it is.

/** The sessionStorage key. Defined ONCE, imported by both sides of the
 * hand-off, so the writer and the reader cannot drift apart. */
export const REGION_PREFILL_STORAGE_KEY = "prism-region-prefill";

/** UNIT #48-b -- WHAT this hand-off is, named on the payload rather than
 * assumed. There is exactly one kind today, and it is still written and checked
 * for a reason: the build before 2026-08-20 wrote a DERIVED (already widened)
 * ring under this very key, and a tab that still holds an unconsumed one must
 * not have it read back as "the region this job was extracted with". */
export const PREFILL_KIND_MANUAL_REGION_EDIT = "manual_region_edit";

/** The two values `basis_kind` takes, mirrored from the backend's own
 * `BASIS_RUNWAY_DEFICIT` / `BASIS_BOUNDARY_CUT`. An unrecognized value is
 * treated as "not a deficit" wherever the wording turns on it: the neutral
 * sentence is true of both, and a wrong confident one is not. */
export const BASIS_RUNWAY_DEFICIT = "lane_change_runway_deficit";
export const BASIS_BOUNDARY_CUT = "boundary_cut_metres";

/** ONE boundary node the backend's own attribution names, and what happened
 * there. This is the "roughly where do I drag" hint, and it is measurement
 * only: a node id the census carries, the end of the way it sits on, and the
 * metres that came off there. */
export interface RegionPrefillBoundaryPoint {
  /** Which END of the way was cut, in the backend's own word (`severed_ends[].
   * end`), or `null` for a runway shortage -- that finding has a boundary node
   * but no severed end, and inventing one would be a claim nobody measured. */
  end: string | null;
  boundary_node: number;
  /** Metres the requested ring CUT OFF at this node, or `null` when nothing was
   * cut. A runway shortage's number is a SHORTAGE (see `basis_m` /
   * `basis_kind`); printing it here would rename it. */
  metres_cut: number | null;
}

/** UNIT #48 (shape), UNIT #48-b (contents) -- one entry per boundary-attributed
 * FINDING the reader might be here for.
 *
 * It no longer describes a finger this app derived, because no finger is
 * derived any more. It describes what the BACKEND measured about the boundary:
 * the finding's own identity, the way the ring cut or clipped, the node(s) it
 * did that at, and the metres. The region page prints these as hints beside the
 * job's own ring so a human knows which edge to drag. */
export interface RegionPrefillCoverage {
  finding_code: string;
  subject: string;
  /** WHAT `basis_m` IS, quoted from the backend's own `basis_kind`:
   * `lane_change_runway_deficit` (a runway SHORTAGE) or `boundary_cut_metres`
   * (the metres the ring CUT off a severed way). The two are separated on
   * purpose -- the payload's own basis note refuses to "call every finger's own
   * number a deficit, because half of them are not one" -- and the banner on the
   * screen that owns the extract button is the last place that distinction may
   * be dropped. */
  basis_kind: string;
  basis_m: number;
  /** The OSM way the attribution is about, or `null` when the measurement named
   * none (the V4 interior case carries the way on its provenance, not on the
   * attribution, and a null here is honest where a 0 would not be). */
  way_id: number | null;
  /** Every boundary node this finding's attribution named. Empty is a real
   * answer and is rendered as such. */
  boundary_points: RegionPrefillBoundaryPoint[];
}

/** What the region page needs to open on this job's own region and to say why
 * the reader is looking at it. Every field is either the job's own recorded
 * request or the backend's own measurement; nothing here is composed prose. */
export interface RegionPrefill {
  /** The job's REQUESTED ring, `[lat, lon]` pairs, open (last vertex is not the
   * first) -- the same convention `RegionPolygon` and the region page's own
   * `vertices` state use. Carried UNCHANGED: this is the shape the extract that
   * produced the parent job was made with, not a shape derived from it. */
  polygon: Array<[number, number]>;
  /** The 프로젝트 the payload was taken in — `getWorkspaceId()`'s own value,
   * `null` for the default project. Checked on read; see the header note. */
  workspace_id: string | null;
  /** The generation whose region this is. */
  parent_job: string;
  /** `PREFILL_KIND_MANUAL_REGION_EDIT`; see that constant. */
  kind: string;
  /** The boundary-attributed findings of that generation, as HINTS. EMPTY is a
   * real and ordinary state -- a reader may open the region editor on a job with
   * no boundary story at all -- so an empty list is carried and rendered as
   * "nothing to point at", never as a reason to drop the hand-off. */
  covers: RegionPrefillCoverage[];
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Same ±90 / ±180 range check the region page's editable vertex table applies
 * to a typed coordinate, applied here to a stored one for the same reason. */
function isVertex(value: unknown): value is [number, number] {
  if (!Array.isArray(value) || value.length !== 2) return false;
  const [lat, lon] = value;
  return (
    isFiniteNumber(lat) &&
    isFiniteNumber(lon) &&
    lat >= -90 &&
    lat <= 90 &&
    lon >= -180 &&
    lon <= 180
  );
}

function parseBoundaryPoint(value: unknown): RegionPrefillBoundaryPoint | null {
  if (typeof value !== "object" || value === null) return null;
  const row = value as Record<string, unknown>;
  if (row.end !== null && typeof row.end !== "string") return null;
  if (!isFiniteNumber(row.boundary_node)) return null;
  if (row.metres_cut !== null && !isFiniteNumber(row.metres_cut)) return null;
  return {
    end: (row.end as string | null) ?? null,
    boundary_node: row.boundary_node,
    metres_cut: (row.metres_cut as number | null) ?? null,
  };
}

/** Write the hand-off for exactly one navigation. Returns false when the store
 * is unavailable (SSR, a browser with storage disabled) so the caller can stay
 * put and say so rather than navigating to a page that will be empty. */
export function stashRegionPrefill(prefill: RegionPrefill): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.sessionStorage.setItem(REGION_PREFILL_STORAGE_KEY, JSON.stringify(prefill));
    return true;
  } catch {
    return false;
  }
}

/** Read the hand-off ONCE and remove it, or null. The removal is the point: a
 * refresh of the region page afterwards lands on the empty initial state that
 * page documents, and a shape nobody asked for a second time never reappears.
 *
 * `currentWorkspaceId` is the 프로젝트 the READER is in (`getWorkspaceId()`,
 * `null` for the default one). A payload stamped with any other project is
 * dropped — removed like every other read, and returned as `null` so the page
 * opens on its documented empty state instead of on a region belonging to a
 * project the reader cannot see. Passed in rather than imported so this module
 * stays dependency-free and directly testable. */
export function takeRegionPrefill(currentWorkspaceId: string | null): RegionPrefill | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(REGION_PREFILL_STORAGE_KEY);
    window.sessionStorage.removeItem(REGION_PREFILL_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const candidate = parsed as Record<string, unknown>;
  // UNIT #48-b: the kind is checked FIRST and exactly, so an unconsumed payload
  // written by the previous build -- a ring this app had already widened -- is
  // dropped rather than presented as the region the job was extracted with.
  if (candidate.kind !== PREFILL_KIND_MANUAL_REGION_EDIT) return null;
  const polygon = candidate.polygon;
  if (!Array.isArray(polygon) || polygon.length < 3 || !polygon.every(isVertex)) return null;
  const workspaceId = candidate.workspace_id;
  if (workspaceId !== null && typeof workspaceId !== "string") return null;
  if ((workspaceId ?? null) !== (currentWorkspaceId ?? null)) return null;
  if (typeof candidate.parent_job !== "string" || !candidate.parent_job) return null;
  // `covers` is REQUIRED and an invalid one is fatal rather than dropped. The
  // alternative -- returning the region without its hints -- would put a banner
  // on screen naming none of the findings the reader came here for, over a
  // button that fetches a whole region. Empty is not invalid: a job with no
  // boundary story has nothing to point at, and that is an answer.
  if (!Array.isArray(candidate.covers)) return null;
  const covers: RegionPrefillCoverage[] = [];
  for (const entry of candidate.covers) {
    if (typeof entry !== "object" || entry === null) return null;
    const row = entry as Record<string, unknown>;
    if (typeof row.finding_code !== "string" || typeof row.subject !== "string") return null;
    if (typeof row.basis_kind !== "string") return null;
    if (!isFiniteNumber(row.basis_m)) return null;
    if (row.way_id !== null && !isFiniteNumber(row.way_id)) return null;
    if (!Array.isArray(row.boundary_points)) return null;
    const points: RegionPrefillBoundaryPoint[] = [];
    for (const point of row.boundary_points) {
      const parsedPoint = parseBoundaryPoint(point);
      if (!parsedPoint) return null;
      points.push(parsedPoint);
    }
    covers.push({
      finding_code: row.finding_code,
      subject: row.subject,
      basis_kind: row.basis_kind,
      basis_m: row.basis_m,
      way_id: (row.way_id as number | null) ?? null,
      boundary_points: points,
    });
  }
  return {
    polygon: polygon.map(([lat, lon]) => [lat, lon] as [number, number]),
    workspace_id: workspaceId ?? null,
    parent_job: candidate.parent_job,
    kind: PREFILL_KIND_MANUAL_REGION_EDIT,
    covers,
  };
}

// Unit #40 -- the ONE hand-off between a V4 refusal and the region page.
//
// A boundary-attributed runway shortage (unit #39) now carries a derived
// TREATMENT: the user's own requested lasso with a finger spliced into the edge
// the corridor crosses, long enough to cover the measured deficit plus a stated
// margin (`genesis_jobs.derive_v4_boundary_extension`). The treatment is NOT a
// new screen and NOT an automatic re-fetch: the proposed shape is carried to
// `/genesis/region` -- unit #20's lasso + editable coordinate table -- where the
// human sees it over the basemap, drags whatever they disagree with, and presses
// that page's OWN extract button. That button is the ask-first gate, and this
// module never crosses it.
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
//     re-propose a stale region against a job that may have been re-gated since;
//   * `sessionStorage` is per-tab and this key is ONE-SHOT (`takeRegionPrefill`
//     reads and removes in the same call), so the proposal survives exactly one
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
// AND THE PROPOSAL IS STAMPED WITH THE 프로젝트 IT WAS DERIVED IN. One-shot is
// not the same as same-project: `sessionStorage` is per-TAB and survives the
// `window.location.reload()` that `components/ProjectSelector.tsx` switches
// projects with, so a write that never got consumed (the navigation lost a race
// with a project switch, or with the picker's own workspace_forbidden reload)
// would still be sitting there when the region page next mounts -- under a
// DIFFERENT project, whose extract button would then fetch project A's polygon
// into project B under a banner naming a job that does not exist there. The
// workspace id travels with the proposal and `takeRegionPrefill` refuses (and
// still removes) a mismatch: a job id alone cannot be checked, because ids are
// per-workspace and nothing on this page can tell whose it is.

/** The sessionStorage key. Defined ONCE, imported by both sides of the
 * hand-off, so the writer and the reader cannot drift apart. */
export const REGION_PREFILL_STORAGE_KEY = "prism-region-prefill";

/** What the region page needs to draw the proposal AND to say what it is
 * treating. Every field comes from the backend's own derivation
 * (`disposition.boundary_extension`) or from the finding it treats; nothing
 * here is composed prose. */
export interface RegionPrefill {
  /** The proposed ring, `[lat, lon]` pairs, open (last vertex is not the
   * first) -- the same convention `RegionPolygon` and the region page's own
   * `vertices` state use. */
  polygon: Array<[number, number]>;
  /** The 프로젝트 the proposal was derived in — `getWorkspaceId()`'s own value,
   * `null` for the default project. Checked on read; see the header note. */
  workspace_id: string | null;
  /** The refused generation this region is treating. */
  parent_job: string;
  /** The finding's own identity, quoted so the banner can name it. */
  finding_code: string;
  subject: string;
  /** The measured shortage the finger closes, and what the widened shape adds. */
  deficit_m: number;
  added_area_km2: number;
  /** How much of the finger the SOURCE declared, and how much was drawn on the
   * last measured bearing because the extract is clipped at this very boundary.
   * Carried so the banner on the screen that owns the extract button can state
   * the split -- the reader who presses that button is the one who most needs
   * to know that part of the shape is an extrapolation, not a measurement. */
  measured_source_m: number;
  extrapolated_m: number;
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

/** Write the proposal for exactly one navigation. Returns false when the store
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

/** Read the proposal ONCE and remove it, or null. The removal is the point: a
 * refresh of the region page afterwards lands on the empty initial state that
 * page documents, and a shape nobody asked for a second time never reappears.
 *
 * `currentWorkspaceId` is the 프로젝트 the READER is in (`getWorkspaceId()`,
 * `null` for the default one). A proposal stamped with any other project is
 * dropped — removed like every other read, and returned as `null` so the page
 * opens on its documented empty state instead of on a shape derived somewhere
 * the reader cannot see. Passed in rather than imported so this module stays
 * dependency-free and directly testable. */
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
  const polygon = candidate.polygon;
  if (!Array.isArray(polygon) || polygon.length < 3 || !polygon.every(isVertex)) return null;
  const workspaceId = candidate.workspace_id;
  if (workspaceId !== null && typeof workspaceId !== "string") return null;
  if ((workspaceId ?? null) !== (currentWorkspaceId ?? null)) return null;
  if (typeof candidate.parent_job !== "string" || !candidate.parent_job) return null;
  if (typeof candidate.finding_code !== "string") return null;
  if (typeof candidate.subject !== "string") return null;
  if (!isFiniteNumber(candidate.deficit_m) || !isFiniteNumber(candidate.added_area_km2)) {
    return null;
  }
  if (
    !isFiniteNumber(candidate.measured_source_m) ||
    !isFiniteNumber(candidate.extrapolated_m)
  ) {
    return null;
  }
  return {
    polygon: polygon.map(([lat, lon]) => [lat, lon] as [number, number]),
    workspace_id: workspaceId ?? null,
    parent_job: candidate.parent_job,
    finding_code: candidate.finding_code,
    subject: candidate.subject,
    deficit_m: candidate.deficit_m,
    added_area_km2: candidate.added_area_km2,
    measured_source_m: candidate.measured_source_m,
    extrapolated_m: candidate.extrapolated_m,
  };
}

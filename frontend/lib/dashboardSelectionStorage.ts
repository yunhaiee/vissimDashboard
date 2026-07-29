// Persists the dashboard's scenario comparison selection across route
// changes. Every route under app/ is a separate page, so navigating from
// /dashboard to e.g. /network and back unmounts DashboardPage and drops its
// `selected` React state -- see app/dashboard/page.tsx. This module is the
// read/write/clear side of restoring that selection on return.
//
// Keyed by dataset revision, not just validated against the current
// scenario list: scenario ids are sequential per dataset (S000001, S000002,
// ...), so a DIFFERENT dataset can plausibly reuse the same ids. Filtering
// stored ids against the freshly-fetched scenario list alone is not enough
// protection -- it would silently restore a selection that means something
// else for the new dataset. Only an exact revision match restores; a
// mismatch (or no stored value at all) is treated the same as "nothing
// stored" by the caller.
//
// Mirrors the house localStorage pattern in lib/api.ts (readStorageValue/
// writeStorageValue/removeStorageValue): typeof window guard plus
// try/catch, since storage can throw in embedded/private-browsing contexts.

const STORAGE_KEY = "vissim-dashboard-selected-scenarios";

type StoredSelection = {
  revision: string;
  ids: string[];
};

function isStoredSelection(value: unknown): value is StoredSelection {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.revision === "string" && Array.isArray(candidate.ids);
}

/**
 * Returns the persisted scenario ids for `revision`, or null if nothing is
 * stored, storage is unavailable, or the stored value belongs to a
 * different dataset revision. Never throws.
 */
export function readStoredScenarioSelection(revision: string): string[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isStoredSelection(parsed) || parsed.revision !== revision) return null;
    return parsed.ids.filter((id): id is string => typeof id === "string");
  } catch {
    return null;
  }
}

/** Persists `ids` as the current selection for `revision`. Never throws. */
export function writeStoredScenarioSelection(revision: string, ids: string[]): void {
  if (typeof window === "undefined") return;
  try {
    const value: StoredSelection = { revision, ids };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Storage can be unavailable in embedded/private-browsing contexts.
  }
}

/** Clears any persisted selection. Never throws. */
export function clearStoredScenarioSelection(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage can be unavailable in embedded/private-browsing contexts.
  }
}

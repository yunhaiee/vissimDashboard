// Persists the analyst's chosen MRWA review category (Category 1/2/3) for
// the base-model validation page across reloads.
//
// Unlike the dashboard's scenario-comparison selection
// (lib/dashboardSelectionStorage.ts), this is NOT keyed to a dataset
// revision. The review category is a property of the REVIEW (which MRWA
// tier the client's project has been scoped into -- see MRWA OMG §2's
// "category is agreed before modelling starts" doctrine, referenced in
// docs/product/tk-requirements-2026-07-31.md), not of which .inpx happens
// to be loaded at the moment. Swapping the active dataset must not silently
// reset which criteria the analyst is reviewing against.
//
// Guarded the same way as the rest of the house storage helpers (typeof
// window + try/catch) since storage can throw in embedded/private-browsing
// contexts -- mirrors lib/dashboardSelectionStorage.ts and the
// readStorageValue/writeStorageValue helpers in lib/api.ts.

import { isValidationCategory, type ValidationCategory } from "./validationProfiles";

const STORAGE_KEY = "vissim-validation-category";

/**
 * Returns the persisted validation category, or null if nothing is stored,
 * storage is unavailable, or the stored value is not a recognized category.
 * Never throws.
 */
export function readStoredValidationCategory(): ValidationCategory | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return isValidationCategory(raw) ? raw : null;
  } catch {
    return null;
  }
}

/** Persists `category` as the current validation category. Never throws. */
export function writeStoredValidationCategory(category: ValidationCategory): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, category);
  } catch {
    // Storage can be unavailable in embedded/private-browsing contexts.
  }
}

// Company "report style" profile (R4.2): chart color palette, font, and
// figure export size for the Report Builder page (app/report/page.tsx).
//
// DEFAULT = no profile stored. Every field is optional, and "not set" means
// "use the report's pre-existing hardcoded appearance" all the way down into
// the backend (backend/services/report_exports.py's render_travel_chart
// mirrors this same all-optional contract) -- so a user who never opens the
// style panel gets byte-for-byte today's report, unchanged. This is the
// regression-safety contract R4.2 asks for; see profileToApiStyle() below,
// which is the one seam that turns "no profile" into "omit `style` from the
// request entirely."
//
// Persisted per the house localStorage pattern -- guarded reads/writes, see
// lib/dashboardSelectionStorage.ts and lib/api.ts's readStorageValue/
// writeStorageValue/removeStorageValue (storage can throw in embedded/
// private-browsing contexts, so every access is try/catch'd and never
// throws out of this module).

import { getDefaultReportPalette } from "@/lib/scenarioPalette";

export type ReportStyleProfile = {
  name?: string;
  /** Hex colors, positional-by-role: [0] = model/simulation series, [1] =
   * observed series. Extra entries are reserved for future multi-series
   * report charts (kept as an array, not two named fields, for that reason). */
  palette?: string[];
  fontFamily?: string;
  fontSizeTitle?: number;
  fontSizeLabel?: number;
  /** Figure size as embedded in the exported .docx, in inches. */
  figureWidthIn?: number;
  figureHeightIn?: number;
};

export type ApiReportStyle = {
  palette?: string[];
  font_family?: string;
  font_size_title?: number;
  font_size_label?: number;
  figure_width_in?: number;
  figure_height_in?: number;
};

const STORAGE_KEY = "vissim-report-style-profile";

function readStorage(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable in embedded/private-browsing contexts.
  }
}

function removeStorage(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Storage can be unavailable in embedded/private-browsing contexts.
  }
}

function isReportStyleProfile(value: unknown): value is ReportStyleProfile {
  if (!value || typeof value !== "object") return false;
  const c = value as Record<string, unknown>;
  if (c.name !== undefined && typeof c.name !== "string") return false;
  if (c.palette !== undefined && (!Array.isArray(c.palette) || !c.palette.every((x) => typeof x === "string"))) {
    return false;
  }
  if (c.fontFamily !== undefined && typeof c.fontFamily !== "string") return false;
  for (const key of ["fontSizeTitle", "fontSizeLabel", "figureWidthIn", "figureHeightIn"] as const) {
    if (c[key] !== undefined && typeof c[key] !== "number") return false;
  }
  return true;
}

/** Returns the persisted profile, or null if nothing is stored / storage is
 * unavailable / the stored value is malformed. Never throws. */
export function readStoredReportStyleProfile(): ReportStyleProfile | null {
  const raw = readStorage(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return isReportStyleProfile(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Persists `profile`, or clears storage when `profile` is null/empty. Never throws. */
export function writeStoredReportStyleProfile(profile: ReportStyleProfile | null): void {
  if (!profile || isReportStyleProfileEmpty(profile)) {
    removeStorage(STORAGE_KEY);
    return;
  }
  try {
    writeStorage(STORAGE_KEY, JSON.stringify(profile));
  } catch {
    // JSON.stringify only throws on circular structures, which this
    // plain-field type can't produce -- guarded anyway per house pattern.
  }
}

export function clearStoredReportStyleProfile(): void {
  removeStorage(STORAGE_KEY);
}

/** A suggested starting palette (NOT auto-applied) -- offered as a
 * "기본값으로 채우기" starting point when a user opens the style panel with
 * an empty profile. Reuses scenarioPalette.ts's own color source. */
export function suggestedDefaultPalette(count = 6): string[] {
  return getDefaultReportPalette(count);
}

export function isReportStyleProfileEmpty(profile: ReportStyleProfile | null | undefined): boolean {
  if (!profile) return true;
  return (
    (!profile.palette || profile.palette.length === 0) &&
    !profile.fontFamily &&
    !profile.fontSizeTitle &&
    !profile.fontSizeLabel &&
    !profile.figureWidthIn &&
    !profile.figureHeightIn
  );
}

/**
 * The one seam between the frontend profile and the backend request body.
 * Returns undefined for an empty/absent profile -- so the `style` key is
 * omitted from the POST body entirely, which is what keeps the default
 * report byte-for-byte unchanged (see build_report()'s `spec.get("style")`
 * in backend/services/report_builder.py).
 */
export function profileToApiStyle(profile: ReportStyleProfile | null | undefined): ApiReportStyle | undefined {
  if (isReportStyleProfileEmpty(profile)) return undefined;
  const p = profile as ReportStyleProfile;
  return {
    palette: p.palette && p.palette.length > 0 ? p.palette : undefined,
    font_family: p.fontFamily || undefined,
    font_size_title: p.fontSizeTitle || undefined,
    font_size_label: p.fontSizeLabel || undefined,
    figure_width_in: p.figureWidthIn || undefined,
    figure_height_in: p.figureHeightIn || undefined,
  };
}

export function exportReportStyleProfileToJson(profile: ReportStyleProfile): string {
  return JSON.stringify(profile, null, 2);
}

/** Parses a JSON string into a ReportStyleProfile, throwing a
 * human-readable Error on invalid JSON or an unrecognized shape (surfaced
 * directly in the report page's error banner). */
export function parseReportStyleProfileFromJson(text: string): ReportStyleProfile {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("JSON 형식이 올바르지 않습니다.");
  }
  if (!isReportStyleProfile(parsed)) {
    throw new Error("스타일 프로필 형식이 올바르지 않습니다.");
  }
  return parsed;
}

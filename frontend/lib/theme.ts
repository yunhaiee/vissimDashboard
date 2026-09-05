// The reader's colour theme -- the browser-local twin of lib/i18n.ts's language.
//
// Two values and no "system": the PALETTE LOCK in app/globals.css names two
// hand-tuned palettes (dark, 2026-07-23; light, 2026-09-04), and an OS-driven
// third state would be one nobody signed off. Dark is the default and is the
// ABSENCE of the html attribute: the server renders no attribute, so a fresh
// browser paints exactly the HTML it was sent, and public/theme-init.js stamps
// the attribute before first paint only when the reader chose light.
export type Theme = "dark" | "light";

export const THEME_STORAGE_KEY = "vissim-theme";
export const THEME_ATTRIBUTE = "data-theme";

export function isTheme(value: string | null | undefined): value is Theme {
  return value === "dark" || value === "light";
}

/** What public/theme-init.js stamped on <html> before first paint. */
export function readDomTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute(THEME_ATTRIBUTE) === "light" ? "light" : "dark";
}

export function applyThemeAttribute(theme: Theme): void {
  if (typeof document === "undefined") return;
  if (theme === "light") {
    document.documentElement.setAttribute(THEME_ATTRIBUTE, "light");
  } else {
    document.documentElement.removeAttribute(THEME_ATTRIBUTE);
  }
}

export function writeStoredTheme(theme: Theme): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Browsers can disable storage in embedded/private contexts.
  }
}

import { JA_UI } from "./i18n-ja";

export type Language = "ko" | "en" | "ja";

export const LANGUAGE_STORAGE_KEY = "vissim-language";

export function isLanguage(value: string | null | undefined): value is Language {
  return value === "ko" || value === "en" || value === "ja";
}

// Japanese UI strings come from a generated ko->ja dictionary (JA_UI); strings not
// in it (e.g. dynamic template labels) fall back to the English UI string. The
// assistant still receives response_language="ja" and replies in Japanese either way.
export function pickByLanguage(lang: Language, ko: string, en: string): string {
  if (lang === "ko") return ko;
  if (lang === "ja") return JA_UI[ko] ?? en;
  return en;
}

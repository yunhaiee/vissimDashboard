"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { isLanguage, LANGUAGE_STORAGE_KEY, pickByLanguage, type Language } from "@/lib/i18n";

type LanguageContextValue = {
  lang: Language;
  setLang: (lang: Language) => void;
  t: (ko: string, en: string, ja?: string) => string;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Language>("ko");

  useEffect(() => {
    const stored = readStoredLanguage();
    if (isLanguage(stored)) {
      setLangState(stored);
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    writeStoredLanguage(lang);
  }, [lang]);

  const setLang = useCallback((nextLang: Language) => {
    setLangState(nextLang);
  }, []);

  const t = useCallback(
    (ko: string, en: string, ja?: string) => (lang === "ja" && ja !== undefined ? ja : pickByLanguage(lang, ko, en)),
    [lang],
  );

  const value = useMemo(
    () => ({ lang, setLang, t }),
    [lang, setLang, t],
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

function readStoredLanguage(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredLanguage(lang: Language): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
  } catch {
    // Browsers can disable storage in embedded/private contexts.
  }
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error("useLanguage must be used inside LanguageProvider");
  }
  return context;
}

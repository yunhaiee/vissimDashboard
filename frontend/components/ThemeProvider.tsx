"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { applyThemeAttribute, readDomTheme, writeStoredTheme, type Theme } from "@/lib/theme";

type ThemeContextValue = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("dark");

  // THE HYDRATION EXCEPTION (lib/asyncResource.ts), the same shape as
  // LanguageProvider -- with one difference that matters for a COLOUR: the
  // reader's choice has ALREADY been applied to <html> before first paint by
  // public/theme-init.js, so the page never flashes dark on the way to light.
  // This effect only folds that stamp back into React state so the toggle
  // draws the right glyph. It reads the attribute rather than localStorage a
  // second time: one source of truth, the script.
  useEffect(() => {
    const stamped = readDomTheme();
    if (stamped !== "dark") {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- data-theme is stamped pre-paint from localStorage; must run after hydration
      setThemeState(stamped);
    }
  }, []);

  const setTheme = useCallback((next: Theme) => {
    applyThemeAttribute(next);
    writeStoredTheme(next);
    setThemeState(next);
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used inside ThemeProvider");
  }
  return context;
}

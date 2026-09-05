import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import AppShell from "@/components/AppShell";
import { AuthProvider } from "@/components/AuthProvider";
import { LanguageProvider } from "@/components/LanguageProvider";
import { ThemeProvider } from "@/components/ThemeProvider";

export const metadata: Metadata = {
  title: "VISSIM Platform",
  description: "VISSIM simulation scenarios, analysis, and interactive controls.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className="app-shell antialiased">
        {/* public/theme-init.js stamps data-theme on <html> BEFORE first paint
            (beforeInteractive lands it in <head>, ahead of hydration), so a
            reader who chose the light theme never sees a dark frame first.
            Dark is the absence of the attribute; suppressHydrationWarning on
            <html> covers the one attribute the client may add. A file in
            public/ rather than an inline script: the contract suite keeps the
            raw-markup census closed. */}
        <Script src="/theme-init.js" strategy="beforeInteractive" />
        {/* AuthProvider sits INSIDE LanguageProvider: the shell's account
            affordance needs both, and a role label has to follow the language
            toggle like every other string. ThemeProvider is outermost: it
            renders no text and the other two need nothing from it. */}
        <ThemeProvider>
          <LanguageProvider>
            <AuthProvider>
              <AppShell>{children}</AppShell>
            </AuthProvider>
          </LanguageProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";
import AppShell from "@/components/AppShell";
import { AuthProvider } from "@/components/AuthProvider";
import { LanguageProvider } from "@/components/LanguageProvider";

export const metadata: Metadata = {
  title: "VISSIM Platform",
  description: "VISSIM simulation scenarios, analysis, and interactive controls.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" suppressHydrationWarning>
      {/* suppressHydrationWarning: browser extensions (Grammarly measured live
          2026-08-27) inject data-gr-* attributes into <body> before hydration,
          which otherwise trips a false-positive mismatch warning on every page. */}
      <body className="app-shell antialiased" suppressHydrationWarning>
        {/* AuthProvider sits INSIDE LanguageProvider: the shell's account
            affordance needs both, and a role label has to follow the language
            toggle like every other string. */}
        <LanguageProvider>
          <AuthProvider>
            <AppShell>{children}</AppShell>
          </AuthProvider>
        </LanguageProvider>
      </body>
    </html>
  );
}

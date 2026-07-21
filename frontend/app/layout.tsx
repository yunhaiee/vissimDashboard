import type { Metadata } from "next";
import "./globals.css";
import AppShell from "@/components/AppShell";
import { LanguageProvider } from "@/components/LanguageProvider";

export const metadata: Metadata = {
  title: "VISSIM Platform",
  description: "VISSIM simulation scenarios, analysis, and interactive controls.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className="app-shell antialiased">
        <LanguageProvider>
          <AppShell>{children}</AppShell>
        </LanguageProvider>
      </body>
    </html>
  );
}

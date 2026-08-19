import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

// `react-hooks/set-state-in-effect` used to be switched OFF here for three
// files — an exemption inherited from the original 2026-07-21 source import.
// It is gone, and deliberately: it was a SECOND convention living beside the
// rule, and the two disagreed about what a correct effect looks like. The app
// now has one answer, in lib/asyncResource.ts: remote state is a keyed answer
// resolved during render, derived state is derived during render, and the only
// effect still allowed to set state synchronously is one that must read a
// value AFTER hydration — which says so on its own line, with a reason.
//
// The three formerly-exempt files were fixed rather than re-exempted (measured:
// app/page.tsx no longer had a single violation, so the exemption was already
// stale for it). If this rule fires again, the fix is in that module, not here.
export default defineConfig([
  ...nextCoreWebVitals,
  ...nextTypeScript,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

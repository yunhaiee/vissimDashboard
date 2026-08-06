/**
 * Where the login page is allowed to send you afterwards (unit U9).
 *
 * The login screen takes a `next` query parameter so a user bounced off a
 * protected action lands back where they were. That parameter is attacker-
 * controllable text in a URL, so it is the classic open-redirect hole: a
 * link like `/login?next=https://evil.example/pay` on a page that trusts it
 * turns Prism's own domain into the bait for a phishing hop.
 *
 * The rule enforced here is deliberately narrow: a destination must resolve
 * to a SAME-ORIGIN path. Everything else -- absolute URLs, protocol-relative
 * `//host`, backslash variants the browser normalises to `//host`,
 * `javascript:` and friends -- falls back to DEFAULT_NEXT_PATH rather than
 * being "sanitised" into something adjacent to what was asked for.
 *
 * Kept in `lib/` (not inside `app/login/page.tsx`) for two reasons: the
 * approval card and the app shell both build login links too, so the rule has
 * exactly one implementation; and a pure module can be unit-tested directly by
 * `tests/dashboard-contract.test.ts` instead of only being regex-pinned.
 */

/** Where a login lands when `next` is absent or refused. Root redirects here. */
export const DEFAULT_NEXT_PATH = "/dashboard";

/** Parsing base. Never navigated to -- only used so relative and absolute
 * candidates can be compared against one known origin. `.invalid` is reserved
 * by RFC 2606 and can never resolve. */
const PARSE_BASE = "http://prism.invalid";

/** Control characters and the space. Tab/newline/NUL are stripped by URL
 * parsers and by browsers at different points, which is exactly how a scheme
 * gets smuggled past a naive prefix check -- so a candidate carrying any of
 * them is refused outright rather than normalised. */
const CONTROL_OR_SPACE = /[\u0000-\u0020\u007f]/;

/**
 * The same-origin path `raw` asks for, or DEFAULT_NEXT_PATH.
 *
 * Returns the RE-SERIALISED path (`pathname + search + hash`) rather than the
 * caller's own string, so nothing that was not understood here can reach
 * `router.replace`.
 */
export function safeNextPath(raw: string | null | undefined): string {
  if (typeof raw !== "string") return DEFAULT_NEXT_PATH;
  const candidate = raw.trim();
  // A path is the only accepted shape. `//host` and `/\host` are both
  // protocol-relative in practice (browsers fold `\` to `/`), so they are
  // rejected on sight rather than left to the origin check below.
  if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.startsWith("/\\")) {
    return DEFAULT_NEXT_PATH;
  }
  if (CONTROL_OR_SPACE.test(candidate)) return DEFAULT_NEXT_PATH;

  let url: URL;
  try {
    url = new URL(candidate, PARSE_BASE);
  } catch {
    return DEFAULT_NEXT_PATH;
  }
  if (url.origin !== PARSE_BASE) return DEFAULT_NEXT_PATH;
  // Bouncing back to the login screen after a successful login is a loop, not
  // a destination.
  if (url.pathname === "/login" || url.pathname.startsWith("/login/")) return DEFAULT_NEXT_PATH;
  return `${url.pathname}${url.search}${url.hash}`;
}

/**
 * The login link for a user currently on `currentPath`.
 *
 * Built here so every caller (the login-required state in the approval card,
 * the shell's anonymous affordance) encodes `next` the same way and runs the
 * same validation on the way in -- a link built from an unvalidated path would
 * just move the open-redirect check to whoever reads it back.
 */
export function loginHref(currentPath: string | null | undefined): string {
  const next = safeNextPath(currentPath);
  if (next === DEFAULT_NEXT_PATH) return "/login";
  return `/login?next=${encodeURIComponent(next)}`;
}

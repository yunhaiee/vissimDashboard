/**
 * The remote-demo door key — decision logic only, no framework.
 *
 * Split out of `frontend/middleware.ts` so it can be tested directly:
 * `next/server` is resolvable by the Next bundler but not by plain
 * `node --test`, and a security check that can only be exercised by starting
 * a dev server is a security check nobody exercises. The middleware is the
 * thin adapter that turns these verdicts into `NextResponse`s.
 *
 * WHAT THIS IS NOT: Prism's authentication. `services/auth.py` + `/login` own
 * named users, roles and the approval record; the master roadmap's §F row
 * ("앱 전체를 로그인 뒤로 — 의도적 미결정 사항") leaves gating the whole app
 * behind that layer an open decision, and a demo is not the place to settle
 * it. This is one shared key for one temporary public URL, and it is inert
 * unless an operator sets both env vars.
 */

/** The `WWW-Authenticate` challenge sent with every refusal. */
export const DEMO_AUTH_REALM = 'Basic realm="Prism demo", charset="UTF-8"';

/**
 * Constant-time string comparison.
 *
 * The Edge runtime has no `crypto.timingSafeEqual`, and `===` on a secret
 * leaks its matching prefix through timing. Length is compared openly — it
 * leaks through the response anyway — and then every character is folded in,
 * so the loop count never depends on where the first difference falls.
 */
export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

/**
 * True when the operator has configured BOTH halves of the key.
 *
 * Half-configured means OFF, not locked: a typo in one env var must not turn
 * a laptop dev server into an unopenable door.
 */
export function isDemoGateEnabled(user: string, password: string): boolean {
  return Boolean(user) && Boolean(password);
}

/** Decode a `Basic` credential pair, or null if the header is unusable. */
export function parseBasicCredentials(header: string | null): { user: string; password: string } | null {
  if (!header) return null;
  // RFC 7235: the scheme token is case-insensitive.
  if (!header.toLowerCase().startsWith("basic ")) return null;

  let decoded: string;
  try {
    decoded = atob(header.slice("basic ".length).trim());
  } catch {
    return null;
  }

  // Split on the FIRST colon only. RFC 7617 forbids a colon in the username
  // but permits one in the password, so `split(":")` would corrupt any
  // password that contains one.
  const separator = decoded.indexOf(":");
  if (separator < 0) return null;

  return { user: decoded.slice(0, separator), password: decoded.slice(separator + 1) };
}

/** True when `header` carries exactly the configured username and password. */
export function isAuthorized(header: string | null, expectedUser: string, expectedPassword: string): boolean {
  const credentials = parseBasicCredentials(header);
  if (!credentials) return false;

  // Both comparisons always run. `&&` would short-circuit on a wrong username
  // and turn the password check into a timing oracle.
  const userOk = safeEqual(credentials.user, expectedUser);
  const passwordOk = safeEqual(credentials.password, expectedPassword);
  return userOk && passwordOk;
}

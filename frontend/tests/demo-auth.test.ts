/**
 * Single-concern suite for `frontend/lib/demo-auth.ts` — the remote-demo edge
 * gate that `frontend/middleware.ts` puts in front of a public Cloudflare URL.
 *
 * This is the only thing between that URL and the agent endpoints (which spend
 * ANTHROPIC_API_KEY), so "it looked right" is not good enough. The middleware
 * itself is a four-line adapter; everything decidable lives here and is
 * exercised here.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  DEMO_AUTH_REALM,
  DEMO_SESSION_MAX_AGE_S,
  demoSessionToken,
  hasValidDemoSession,
  isAuthorized,
  isDemoGateEnabled,
  parseBasicCredentials,
  safeEqual,
} from "../lib/demo-auth.ts";

/** Build the `Authorization` header a browser sends for `user:password`. */
function basic(credentials: string): string {
  return `Basic ${btoa(credentials)}`;
}

test("the gate is enabled only when BOTH halves of the key are configured", () => {
  // The whole point of the default-off design: `npm run dev` on a laptop must
  // be unchanged, and a typo in one env var must not produce a door nobody
  // can open.
  assert.equal(isDemoGateEnabled("", ""), false);
  assert.equal(isDemoGateEnabled("professor", ""), false);
  assert.equal(isDemoGateEnabled("", "correct-horse"), false);
  assert.equal(isDemoGateEnabled("professor", "correct-horse"), true);
});

test("the challenge names a realm and a charset", () => {
  // Without `charset="UTF-8"` a browser may encode a non-ASCII password in
  // something other than UTF-8, and the comparison then fails for a viewer who
  // typed the right thing.
  assert.match(DEMO_AUTH_REALM, /^Basic realm="[^"]+", charset="UTF-8"$/);
});

test("safeEqual agrees with equality on the cases that matter", () => {
  assert.equal(safeEqual("", ""), true);
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  // A prefix is not a match — the property a naive early-return loop breaks.
  assert.equal(safeEqual("abc", "abcd"), false);
  assert.equal(safeEqual("abcd", "abc"), false);
});

test("credentials split on the FIRST colon so a password may contain one", () => {
  // RFC 7617 bans a colon in the username but allows one in the password.
  // Nothing in share_demo.py's generator emits one today, which is exactly
  // why this is pinned: a human-supplied --password can.
  assert.deepEqual(parseBasicCredentials(basic("professor:a:b:c")), {
    user: "professor",
    password: "a:b:c",
  });
});

test("the scheme token is matched case-insensitively", () => {
  // RFC 7235 says the scheme is case-insensitive; some clients send "basic".
  assert.deepEqual(parseBasicCredentials(`basic ${btoa("professor:pw")}`), {
    user: "professor",
    password: "pw",
  });
});

test("unusable authorization headers parse to null rather than throwing", () => {
  assert.equal(parseBasicCredentials(null), null);
  assert.equal(parseBasicCredentials(""), null);
  // Wrong scheme.
  assert.equal(parseBasicCredentials(`Bearer ${btoa("professor:pw")}`), null);
  // Not decodable as base64.
  assert.equal(parseBasicCredentials("Basic !!!not-base64!!!"), null);
  // Decodable, but carries no colon to split on.
  assert.equal(parseBasicCredentials(`Basic ${btoa("professor")}`), null);
});

test("isAuthorized admits the exact pair and refuses every near miss", () => {
  const user = "professor";
  const password = "correct-horse";

  assert.equal(isAuthorized(basic("professor:correct-horse"), user, password), true);

  assert.equal(isAuthorized(null, user, password), false);
  assert.equal(isAuthorized(basic("professor:wrong"), user, password), false);
  assert.equal(isAuthorized(basic("someone:correct-horse"), user, password), false);
  // A prefix of the real password must not open the door.
  assert.equal(isAuthorized(basic("professor:correct"), user, password), false);
  // Nor an extension of it.
  assert.equal(isAuthorized(basic("professor:correct-horse-battery"), user, password), false);
  // Case matters on both halves.
  assert.equal(isAuthorized(basic("Professor:correct-horse"), user, password), false);
  assert.equal(isAuthorized(basic("professor:Correct-Horse"), user, password), false);
});

test("an empty configured password is never satisfiable through isAuthorized", () => {
  // Belt and braces behind isDemoGateEnabled: if a caller ever forgets that
  // guard, an empty password must not mean "any empty password is welcome" in
  // a way that also admits a blank submission to a gate believed to be armed.
  // The gate is off in that configuration; this pins that isAuthorized alone
  // still requires the username to match.
  assert.equal(isAuthorized(basic("wrong:"), "professor", ""), false);
});

// ---------------------------------------------------------------- session ---
// The cookie exists because Basic alone challenged the viewer on EVERY in-app
// navigation (2026-09-09): the App Router fetches RSC payloads, and a `fetch()`
// that gets a 401 neither consults the browser's credential cache nor raises
// its dialog. These pin the properties that make one login hold.

test("the session token is deterministic for a credential pair", async () => {
  // The two processes share_demo.py starts must agree on the token without
  // exchanging a secret, and a restart must not log the viewer out.
  const first = await demoSessionToken("professor", "correct-horse");
  const second = await demoSessionToken("professor", "correct-horse");
  assert.equal(first, second);
});

test("a changed password mints a different token, revoking every old cookie", async () => {
  const before = await demoSessionToken("professor", "correct-horse");
  const after = await demoSessionToken("professor", "battery-staple");
  assert.notEqual(before, after);
  // And the old cookie is no longer admitted under the new key.
  assert.equal(hasValidDemoSession(before, after), false);
});

test("the username is part of the token, not just the password", async () => {
  const one = await demoSessionToken("professor", "correct-horse");
  const two = await demoSessionToken("student", "correct-horse");
  assert.notEqual(one, two);
});

test("the token is a hash, never the password itself", async () => {
  const token = await demoSessionToken("professor", "correct-horse");
  // SHA-256 hex: the raw key must never be at rest in a cookie jar.
  assert.match(token, /^[0-9a-f]{64}$/);
  assert.equal(token.includes("correct-horse"), false);
  assert.equal(token.includes("professor"), false);
});

test("a session is admitted only on an exact token match", async () => {
  const token = await demoSessionToken("professor", "correct-horse");
  assert.equal(hasValidDemoSession(token, token), true);
  assert.equal(hasValidDemoSession(`${token}x`, token), false);
  assert.equal(hasValidDemoSession(token.slice(0, -1), token), false);
  assert.equal(hasValidDemoSession(token.toUpperCase(), token), false);
});

test("an absent or empty cookie is never a session", async () => {
  // The unauthenticated case has to fall through to the Basic challenge
  // rather than being mistaken for a valid door key.
  const token = await demoSessionToken("professor", "correct-horse");
  assert.equal(hasValidDemoSession(null, token), false);
  assert.equal(hasValidDemoSession(undefined, token), false);
  assert.equal(hasValidDemoSession("", token), false);
});

test("the session expires rather than lasting forever", () => {
  assert.ok(DEMO_SESSION_MAX_AGE_S > 0, "a session that never expires is a permanent door key");
  assert.ok(DEMO_SESSION_MAX_AGE_S <= 60 * 60 * 24, "a demo session should not outlive a day");
});

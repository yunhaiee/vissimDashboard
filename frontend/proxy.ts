import { NextResponse, type NextRequest } from "next/server";

import {
  DEMO_AUTH_REALM,
  DEMO_SESSION_COOKIE,
  DEMO_SESSION_MAX_AGE_S,
  demoSessionToken,
  hasValidDemoSession,
  isAuthorized,
  isDemoGateEnabled,
} from "@/lib/demo-auth";

// Remote demo gate (2026-09-08). Prism's own auth (`services/auth.py`,
// `routers/auth.py`, `/login`) is the PRODUCT's identity layer, and the master
// roadmap's §F row — "앱 전체를 로그인 뒤로 — 현재 2개 엔드포인트만 게이트됨.
// 의도적 미결정 사항" — leaves gating the whole app behind it an OPEN decision.
// Settling that here, as a side effect of wanting to show the app to somebody,
// would be the wrong way to settle it, and it would not even do the job: that
// question is about named users and approval records, whereas a tunnelled demo
// needs one shared door key that leaves no name in the audit document.
//
// So this is an EDGE gate, not an auth change. Proxy runs BEFORE the `/api/*`
// rewrites in next.config.ts, so it covers the proxied FastAPI surface too —
// including the agent endpoints, which spend ANTHROPIC_API_KEY and must never
// sit open on a public URL.
//
// Inert unless BOTH env vars are set, so `npm run dev` on a laptop behaves
// exactly as it did before. `scripts/share_demo.py` sets them; nothing else
// does. The decision logic and its reasoning live in `lib/demo-auth.ts`, which
// is testable without a dev server; this file is the adapter.
const AUTH_USER = process.env.PRISM_DEMO_AUTH_USER ?? "";
const AUTH_PASSWORD = process.env.PRISM_DEMO_AUTH_PASSWORD ?? "";

//: The pre-merge route. `/intersections/:path*` matched the bare path too, so
//: the prefix test has to accept `/intersections` with no trailing segment.
const LEGACY_INTERSECTIONS_PATH = "/intersections";

function isLegacyIntersectionsPath(pathname: string): boolean {
  return pathname === LEGACY_INTERSECTIONS_PATH || pathname.startsWith(`${LEGACY_INTERSECTIONS_PATH}/`);
}

export async function proxy(request: NextRequest) {
  // Set only on the request that actually spends the password, so the
  // Set-Cookie rides the FIRST authorised response and every later one is a
  // plain pass-through.
  let mintSession = false;

  // The gate goes FIRST. A redirect served before authentication would tell an
  // anonymous caller which routes exist.
  if (isDemoGateEnabled(AUTH_USER, AUTH_PASSWORD)) {
    const token = await demoSessionToken(AUTH_USER, AUTH_PASSWORD);

    // Cookie before header: the App Router's RSC fetches carry the cookie and
    // NOT the Authorization header, and they are the majority of requests once
    // a viewer starts clicking around.
    if (!hasValidDemoSession(request.cookies.get(DEMO_SESSION_COOKIE)?.value, token)) {
      if (isAuthorized(request.headers.get("authorization"), AUTH_USER, AUTH_PASSWORD)) {
        mintSession = true;
      } else {
        return new NextResponse("Authentication required.", {
          status: 401,
          headers: {
            "WWW-Authenticate": DEMO_AUTH_REALM,
            // A shared cache replaying a stored 401 would lock out a viewer who
            // has already typed the password.
            "Cache-Control": "no-store",
          },
        });
      }
    }
  }

  const response = isLegacyIntersectionsPath(request.nextUrl.pathname)
    ? NextResponse.redirect(new URL("/dashboard", request.url))
    : NextResponse.next();

  if (mintSession) {
    response.cookies.set({
      name: DEMO_SESSION_COOKIE,
      value: await demoSessionToken(AUTH_USER, AUTH_PASSWORD),
      httpOnly: true,
      sameSite: "lax",
      // The tunnel is https; a laptop `npm run dev` is http and would silently
      // drop a Secure cookie, so this follows the origin rather than assuming.
      secure: request.nextUrl.protocol === "https:",
      path: "/",
      maxAge: DEMO_SESSION_MAX_AGE_S,
    });
  }

  return response;
}

export const config = {
  // Widened from "/intersections/:path*" so the demo gate can cover the whole
  // origin, static assets included: an unauthenticated fetch of the tunnel URL
  // should yield nothing at all. Browsers replay Basic credentials on
  // same-origin subresources once the prompt is satisfied, so one login covers
  // the whole page. The legacy redirect is now a pathname test inside `proxy`
  // rather than a matcher, and is pinned as such by the contract suite.
  //
  // The former WebSocket gap should now close on the session cookie: the
  // browser WebSocket API still sends no Authorization header, but its
  // handshake is a same-origin HTTP request and does carry cookies, so
  // `/api/simulations/ws/<job>` is admitted once a viewer has been through the
  // door. NOT exercised live — it needs a real run, which needs Vissim — so
  // `fetchSimulationJob` polling remains the documented fallback until a
  // tunnelled demo from the Windows box actually proves it.
  matcher: ["/:path*"],
};

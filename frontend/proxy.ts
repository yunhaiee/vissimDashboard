import { NextResponse, type NextRequest } from "next/server";

import { DEMO_AUTH_REALM, isAuthorized, isDemoGateEnabled } from "@/lib/demo-auth";

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

export function proxy(request: NextRequest) {
  // The gate goes FIRST. A redirect served before authentication would tell an
  // anonymous caller which routes exist.
  if (isDemoGateEnabled(AUTH_USER, AUTH_PASSWORD) && !isAuthorized(request.headers.get("authorization"), AUTH_USER, AUTH_PASSWORD)) {
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

  if (isLegacyIntersectionsPath(request.nextUrl.pathname)) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Widened from "/intersections/:path*" so the demo gate can cover the whole
  // origin, static assets included: an unauthenticated fetch of the tunnel URL
  // should yield nothing at all. Browsers replay Basic credentials on
  // same-origin subresources once the prompt is satisfied, so one login covers
  // the whole page. The legacy redirect is now a pathname test inside `proxy`
  // rather than a matcher, and is pinned as such by the contract suite.
  //
  // Known gap, documented rather than papered over: the browser WebSocket API
  // sends no Authorization header, so `/api/simulations/ws/<job>` gets a 401
  // through the tunnel and live run-progress falls back to polling via
  // `fetchSimulationJob`. Moot for a laptop demo (no Vissim, no COM, so no
  // live runs) — but it WOULD bite a tunnelled demo from the Windows box.
  matcher: ["/:path*"],
};

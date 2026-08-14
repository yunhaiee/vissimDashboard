"use client";

// This route stopped being its own sidebar entry (unit #27, backlog item 8,
// 2026-08-14): the mockup's own "+비교" annotation folded the diff view into
// /network as a mode instead of a page (see components/NetworkDiffView.tsx,
// which now carries every fact this file used to). What is left here is a
// thin client redirect so an old bookmark or a shared /network-diff link
// keeps landing on the diff view instead of 404ing.
//
// Nothing on the OLD version of this page ever read a query parameter of its
// own -- sessionId/baseToken/targetToken were plain in-memory React state,
// never URL-driven -- so there is nothing to carry forward into the new URL.
// A plain redirect to /network's diff mode is the whole of the fix; if that
// ever changes (the diff mode grows its own URL-carried picker state), this
// redirect is where a hand-typed old-style param would need to be translated.
//
// AppShell.tsx's ROUTE_STAGE still maps this path to the 네트워크 stage (see
// the comment there) so the pipeline stepper reads correctly for the instant
// this redirect is in flight, rather than rendering no stepper at all.

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function NetworkDiffRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/network?mode=diff");
  }, [router]);
  return null;
}

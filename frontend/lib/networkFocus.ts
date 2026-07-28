// Approval-card -> /network focus handoff (zoom/flash defect, 2026-07-28).
//
// The approval card's mini-map answers "which intersection is this?" in a
// thumbnail; this module carries the SAME focus onto the full network tab so
// the user can actually inspect it. The handoff travels entirely in URL query
// params -- deliberately NOT through a React context, a custom event or a new
// API type:
//
//   * self-contained + reload-safe: the /network URL a user lands on (or
//     bookmarks, or reopens after a refresh) still carries the focus, whereas
//     an in-memory handoff dies on F5. FloatingAssistant already survives
//     route changes, but the approval it came from may not still be pending.
//   * no new wire contract: the backend focus payload (lib/api.ts's
//     AgentApprovalFocus, unchanged) is the SOURCE of these params, not a
//     second copy of it -- everything below is derived from that payload at
//     click time.
//
// Bounded on purpose: element type/id pairs, capped, plus four bbox numbers.
// Never raw polylines (same rule approval_focus.py's own docstring states for
// the wire payload) -- the /network page re-resolves every id against the
// network geometry it already fetches.

import { useEffect, useState } from "react";

export type FocusBbox = { x_min: number; y_min: number; x_max: number; y_max: number };

/** One focus element as it travels in the URL: the backend focus payload's
 * `{type, id}` minus the label (the /network page localizes its own caption
 * from `type`, so shipping a baked label would go stale on a language switch). */
export type FocusElementRef = { type: string; id: string };

export type NetworkFocus = {
  bbox: FocusBbox;
  elements: FocusElementRef[];
  /** Per-click re-trigger token, "" when the URL carries none — see
   * FOCUS_NONCE_PARAM below. Part of focusSignature(), so a second click on the
   * SAME target reads as a new focus rather than as the one already dismissed. */
  nonce: string;
};

export const FOCUS_BBOX_PARAM = "fbox";
export const FOCUS_ELEMENTS_PARAM = "fel";

// Re-trigger token (2026-07-28 defect: "the button only works once").
//
// `fbox`+`fel` describe WHICH place to look at, and are deliberately stable —
// the same target always produces the same pair, which is what makes the URL
// bookmarkable and reload-safe. But that stability is exactly what broke the
// second click, in two places at once:
//
//   1. Router: clicking 네트워크에서 보기 while already parked on that exact
//      /network?fbox=…&fel=… URL is a navigation to the current URL, so the
//      app router has nothing to change and useSearchParams never re-emits.
//   2. Page state: /network remembers the focus the user dismissed BY ITS
//      SIGNATURE, so even a forced re-render of an identical focus would
//      resolve to "already dismissed, stay quiet".
//
// One extra param fixes both, because it changes the URL *and* the signature:
// `fseq` says nothing about the target, only "this is a fresh ask". It is
// generated at CLICK time (never at render time — a render-time nonce would be
// a hydration mismatch, and would still be stale by the second click).
export const FOCUS_NONCE_PARAM = "fseq";

// Hand-editable input: anything that is not a short opaque token is dropped
// rather than trusted, so a mangled URL degrades to "no nonce" (= the pre-fix
// behaviour, still perfectly usable) instead of poisoning the React keys and
// DOM-attribute-adjacent strings this value flows into.
const NONCE_PATTERN = /^[A-Za-z0-9_-]{1,32}$/;

// Monotonic within a page session; the timestamp prefix keeps it unique across
// remounts and reloads (a per-component counter would restart at 0 and hand
// out an already-used value, silently reviving the very defect this fixes).
let nonceCounter = 0;

/** A fresh re-trigger token. Call once per click — never memoize it. */
export function nextFocusNonce(): string {
  nonceCounter += 1;
  return `${Date.now().toString(36)}${nonceCounter.toString(36)}`;
}

/** The same focus href, re-triggerable: identical target, guaranteed-new URL.
 * Idempotent in the param (re-applying replaces rather than appends). */
export function withFocusNonce(href: string, nonce: string = nextFocusNonce()): string {
  const queryAt = href.indexOf("?");
  const path = queryAt < 0 ? href : href.slice(0, queryAt);
  const params = new URLSearchParams(queryAt < 0 ? "" : href.slice(queryAt + 1));
  params.set(FOCUS_NONCE_PARAM, nonce);
  return `${path}?${params.toString()}`;
}

// A whole signal controller's focus can carry 79 elements (live-measured:
// Beijing SC1 = 52 signal heads + their links). Past a couple of dozen the
// URL stops being reasonable and the extra ids buy nothing -- the bbox
// already frames the right place, and the remaining elements are all inside
// it. Same "cap the enumerated thing, never drop the load-bearing thing"
// discipline as approval_focus.py's own _MAX_FANOUT_ELEMENTS.
export const MAX_FOCUS_URL_ELEMENTS = 24;

/** Focus element `type` (singular, as the backend emits it) -> the
 * NetworkElements list key NetworkViewer stamps into its `data-elkey`
 * attributes (`"signal_heads:42"`). Types absent here have no element glyph
 * anywhere in NetworkViewer: "link" is drawn by the road passes (matched by
 * `data-linkid` instead of this map) and everything else absent -- node,
 * signal_controller, conflict_area, routing_decision, route,
 * vehicle_composition, des_speed_distribution -- has no geometry of its own
 * at all (approval_focus.py's / network_search.py's own docstrings agree on
 * this).
 *
 * The first four entries are every type the approval-focus write tools ever
 * emit. Network component search (2026-07-28, lib/networkSearchLabel.ts +
 * components/NetworkSearchBox.tsx) can surface every OTHER drawable element
 * family too, so its point-glyph classes are appended here rather than kept
 * in a second, drifting copy of this same table -- one focus-type -> elkey
 * map for every consumer of `focusElementKey`/`buildFocusFlash`. */
const ELEMENT_KEY_BY_FOCUS_TYPE: Record<string, string> = {
  signal_head: "signal_heads",
  vehicle_input: "vehicle_inputs",
  des_speed_decision: "des_speed_decisions",
  reduced_speed_area: "reduced_speed_areas",
  detector: "detectors",
  data_collection_point: "data_collection_points",
  queue_counter: "queue_counters",
  pt_stop: "pt_stops",
  stop_sign: "stop_signs",
  traveltime_section: "traveltime_sections_geo",
  ped_area: "ped_areas",
};

/** The `data-elkey` value NetworkViewer stamps for this focus element, or
 * null when the type is drawn some other way (links) or not at all. */
export function focusElementKey(ref: FocusElementRef): string | null {
  const listKey = ELEMENT_KEY_BY_FOCUS_TYPE[ref.type];
  return listKey ? `${listKey}:${ref.id}` : null;
}

function round(value: number): string {
  return Number.isFinite(value) ? String(Math.round(value * 10) / 10) : "0";
}

/** `/network?fbox=…&fel=…` for one approval focus. Returns null when the
 * focus has no bbox — the caller renders no button at all in that case, the
 * same "no bbox -> no map" contract ApprovalFocusMap already follows. */
export function buildNetworkFocusHref(
  bbox: FocusBbox | null | undefined,
  elements: readonly FocusElementRef[] | undefined,
): string | null {
  if (!bbox) return null;
  const params = new URLSearchParams();
  params.set(FOCUS_BBOX_PARAM, [bbox.x_min, bbox.y_min, bbox.x_max, bbox.y_max].map(round).join(","));
  const refs = (elements ?? [])
    .filter((el) => el.type && el.id && !el.type.includes(":") && !el.id.includes(","))
    .slice(0, MAX_FOCUS_URL_ELEMENTS);
  if (refs.length) {
    params.set(FOCUS_ELEMENTS_PARAM, refs.map((el) => `${el.type}:${el.id}`).join(","));
  }
  return `/network?${params.toString()}`;
}

/** Inverse of buildNetworkFocusHref. Returns null for any missing/malformed
 * input rather than throwing — a hand-edited or truncated URL must degrade to
 * "no focus", never break the network page. */
export function parseNetworkFocus(params: URLSearchParams | null | undefined): NetworkFocus | null {
  if (!params) return null;
  const raw = params.get(FOCUS_BBOX_PARAM);
  if (!raw) return null;
  const parts = raw.split(",").map((value) => Number.parseFloat(value));
  if (parts.length !== 4 || parts.some((value) => !Number.isFinite(value))) return null;
  const [x_min, y_min, x_max, y_max] = parts;
  if (x_max <= x_min || y_max <= y_min) return null;
  const elements: FocusElementRef[] = [];
  for (const token of (params.get(FOCUS_ELEMENTS_PARAM) ?? "").split(",")) {
    const separator = token.indexOf(":");
    if (separator <= 0) continue;
    const type = token.slice(0, separator).trim();
    const id = token.slice(separator + 1).trim();
    if (!type || !id) continue;
    elements.push({ type, id });
    if (elements.length >= MAX_FOCUS_URL_ELEMENTS) break;
  }
  const rawNonce = (params.get(FOCUS_NONCE_PARAM) ?? "").trim();
  const nonce = NONCE_PATTERN.test(rawNonce) ? rawNonce : "";
  return { bbox: { x_min, y_min, x_max, y_max }, elements, nonce };
}

/** Stable identity for one focus, so an effect can re-run when the URL's
 * focus actually changes — INCLUDING "same target asked for a second time",
 * which is what the leading nonce encodes (see FOCUS_NONCE_PARAM) — without
 * depending on object identity. */
export function focusSignature(focus: NetworkFocus | null): string {
  if (!focus) return "";
  const { x_min, y_min, x_max, y_max } = focus.bbox;
  return `${focus.nonce}#${round(x_min)},${round(y_min)},${round(x_max)},${round(y_max)}|${focus.elements
    .map((el) => `${el.type}:${el.id}`)
    .join(",")}`;
}

// --- Shared focusFlash/zoomToBbox plumbing (network search, 2026-07-28) ---
//
// Originally lived inline in app/network/page.tsx, written once for the
// approval-card URL handoff. Network component search (NetworkSearchBox.tsx)
// needs the EXACT same "pulse then settle" lifecycle and the exact same
// element-refs -> {linkIds, elementKeys} translation, on /network (where it
// must coexist with the URL handoff) and on /dashboard (which has never had
// either). Extracted here rather than copy-pasted three times, so the pulse
// timing and the flash-shape logic can never drift between call sites.

// ~4 cycles of NetworkViewer's 0.85s focus-flash keyframe — long enough to
// catch the eye after a zoom flight lands, short enough to never read as a
// permanent blink (irritating, and an accessibility problem). Skipped
// entirely under prefers-reduced-motion by useFocusPulse below.
export const FOCUS_PULSE_MS = 3400;

/** Pulse-then-settle state machine for one focus "slot" (the URL-sourced
 * approval focus and a search selection are two independent slots that can
 * be active on the same page — each calls this once with its own key).
 * Arriving with a NEW, non-empty `key` starts a short attention pulse
 * (skipped under prefers-reduced-motion), which SETTLES into the static
 * highlight after `pulseMs` — never an endless blink. Starting the pulse
 * happens DURING RENDER (React's documented "adjust state when a prop
 * changes" pattern), not in an effect — an effect would render one frame of
 * the settled highlight first, i.e. the pulse would visibly stutter on
 * arrival. `key === ""` means "no focus in this slot"; the hook simply never
 * pulses (and returns false) until a real key arrives. */
export function useFocusPulse(key: string, pulseMs: number = FOCUS_PULSE_MS): boolean {
  const [pulse, setPulse] = useState({ key: "", active: false });
  if (key && pulse.key !== key) {
    const reduceMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setPulse({ key, active: !reduceMotion });
  }
  const pulsing = pulse.active && pulse.key === key;
  useEffect(() => {
    if (!pulsing) return;
    const timer = window.setTimeout(() => setPulse((prev) => ({ ...prev, active: false })), pulseMs);
    return () => window.clearTimeout(timer);
  }, [pulsing, pulse.key, pulseMs]);
  return pulsing;
}

/** `elements` -> NetworkViewer's `focusFlash` prop shape: link ids resolve
 * against `data-linkid` (the road fills), everything else resolves against
 * `data-elkey` via `focusElementKey` (or drops out silently if it names a
 * family NetworkViewer never draws — a conflict area's own "self" ref, a
 * route, a node, ...). Returns null when nothing in `elements` maps to a
 * flashable glyph at all — the caller still zooms via `zoomToBbox`, it just
 * has nothing to recolour (mirrors ApprovalFocusMap.tsx's own "no bbox -> no
 * map" contract, one level down: "no flashable element -> no flash prop"). */
export function buildFocusFlash(
  elements: readonly FocusElementRef[],
  pulsing: boolean,
): { linkIds: string[]; elementKeys: string[]; pulsing: boolean } | null {
  const linkIds: string[] = [];
  const elementKeys: string[] = [];
  for (const element of elements) {
    if (element.type === "link") {
      linkIds.push(element.id);
      continue;
    }
    const key = focusElementKey(element);
    if (key) elementKeys.push(key);
  }
  if (!linkIds.length && !elementKeys.length) return null;
  return { linkIds, elementKeys, pulsing };
}

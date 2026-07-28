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

export type FocusBbox = { x_min: number; y_min: number; x_max: number; y_max: number };

/** One focus element as it travels in the URL: the backend focus payload's
 * `{type, id}` minus the label (the /network page localizes its own caption
 * from `type`, so shipping a baked label would go stale on a language switch). */
export type FocusElementRef = { type: string; id: string };

export type NetworkFocus = { bbox: FocusBbox; elements: FocusElementRef[] };

export const FOCUS_BBOX_PARAM = "fbox";
export const FOCUS_ELEMENTS_PARAM = "fel";

// A whole signal controller's focus can carry 79 elements (live-measured:
// Beijing SC1 = 52 signal heads + their links). Past a couple of dozen the
// URL stops being reasonable and the extra ids buy nothing -- the bbox
// already frames the right place, and the remaining elements are all inside
// it. Same "cap the enumerated thing, never drop the load-bearing thing"
// discipline as approval_focus.py's own _MAX_FANOUT_ELEMENTS.
export const MAX_FOCUS_URL_ELEMENTS = 24;

/** Focus element `type` (singular, as the backend emits it) -> the
 * NetworkElements list key NetworkViewer stamps into its `data-elkey`
 * attributes (`"signal_heads:42"`). Types absent here have no element glyph:
 * "link" is drawn by the road passes (matched by `data-linkid` instead) and
 * "conflict_area" has no geometry at all. */
const ELEMENT_KEY_BY_FOCUS_TYPE: Record<string, string> = {
  signal_head: "signal_heads",
  vehicle_input: "vehicle_inputs",
  des_speed_decision: "des_speed_decisions",
  reduced_speed_area: "reduced_speed_areas",
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
  return { bbox: { x_min, y_min, x_max, y_max }, elements };
}

/** Stable identity for one focus, so an effect can re-run when the URL's
 * focus actually changes (including "same focus clicked twice" — the caller
 * appends its own nonce for that) without depending on object identity. */
export function focusSignature(focus: NetworkFocus | null): string {
  if (!focus) return "";
  const { x_min, y_min, x_max, y_max } = focus.bbox;
  return `${round(x_min)},${round(y_min)},${round(x_max)},${round(y_max)}|${focus.elements
    .map((el) => `${el.type}:${el.id}`)
    .join(",")}`;
}

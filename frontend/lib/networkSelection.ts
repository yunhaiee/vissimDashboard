/**
 * Shared multi-select reducer helpers for the network diagram (NetworkViewer)
 * and its two host pages (/network, /dashboard) — v2 UX queue, final unit
 * (multi-select in the network diagram, 2026-07-29).
 *
 * "전체 통일" design decision: a plain (unmodified) click on ANY selectable
 * thing — node, link/connector, element glyph — REPLACES the whole selection
 * with just that thing (clicking the sole-selected item again deselects it,
 * keeping the pre-existing toggle-off feel); shift+click adds/removes it;
 * shift+drag draws a box that ADDS everything inside, up to the same caps.
 * One generic (kind, id) identity + cap-aware replace/toggle/box-add
 * implementation, shared by every selection channel (nodes/links/elements)
 * and both host pages, so they can never drift into subtly different
 * accumulate/replace/cap semantics — that drift is exactly what this unit
 * exists to remove (before this, nodes accumulated on plain click while
 * links/elements toggle-replaced; this file is the one place all three now
 * agree).
 *
 * Caps mirror the backend's own (backend/services/network_selection.py) —
 * NOT the source of truth (the backend re-sanitizes independently at the
 * request boundary, and that is what actually governs what reaches a
 * prompt) but kept in lockstep so truncation is something the user SEES
 * happen here, rather than something they only discover after the backend
 * silently re-capped a larger client selection.
 */

// Mirrors backend/services/network_selection.py's MAX_SELECTED_LINKS.
export const MAX_SELECTED_LINKS = 6;
// Mirrors backend/services/network_selection.py's MAX_SELECTED_ELEMENTS.
export const MAX_SELECTED_ELEMENTS = 8;

export interface ToggleResult<T> {
  next: T[];
  /** true when `item` was NOT added because the list was already at cap
   * (existing items are unchanged) — the caller surfaces this as a notice;
   * this module never drops something silently. */
  capped: boolean;
}

export interface AddAllResult<T> {
  next: T[];
  /** Count of candidates that were neither already present nor added,
   * because the list was already at cap by the time they were reached.
   * Duplicates already in the list are NOT counted — they were never a new
   * addition, so the cap never "drops" them. */
  omitted: number;
}

/** The plain-click rule: replace the WHOLE selection — every channel, not
 * just this one — with just `item`. `list` is the CLICKED item's own
 * channel; the caller is responsible for also clearing the other two
 * channels to `[]` (nodes/links/elements are different types, so one
 * generic function cannot reach across all three itself). `otherChannelsEmpty`
 * tells this function whether the other two channels were ALREADY empty
 * before this click — needed for the toggle-off case below, since "the sole
 * selected thing" means alone across the WHOLE selection, not just alone
 * within its own channel: clicking a link while 2 nodes are also selected
 * must replace everything with just that link, never toggle it off, even
 * though the link list itself has length 1. Defaults to `true` so a caller
 * that only has one channel to begin with (or does not care about the
 * cross-channel nuance) gets the simpler single-channel toggle-off
 * behaviour for free. */
export function replaceWith<T>(list: T[], item: T, keyOf: (item: T) => string, otherChannelsEmpty: boolean = true): T[] {
  if (otherChannelsEmpty && list.length === 1 && keyOf(list[0]) === keyOf(item)) return [];
  return [item];
}

/** The shift+click rule: toggle `item` (identified by `keyOf`) into/out of
 * `list`, capped at `cap`. Removing an already-selected item always
 * succeeds (a selection can only ever shrink below the cap); adding past
 * the cap is a no-op that reports `capped: true` instead of truncating
 * silently. */
export function toggleItem<T>(list: T[], item: T, keyOf: (item: T) => string, cap: number): ToggleResult<T> {
  const key = keyOf(item);
  const idx = list.findIndex((existing) => keyOf(existing) === key);
  if (idx >= 0) {
    return { next: [...list.slice(0, idx), ...list.slice(idx + 1)], capped: false };
  }
  if (list.length >= cap) {
    return { next: list, capped: true };
  }
  return { next: [...list, item], capped: false };
}

/** The shift+drag-box rule: add every item in `items` not already present in
 * `list`, capped at `cap`, in ONE pass — a box haul gets a single combined
 * omitted-count rather than one notice per item it touched. */
export function addAllCapped<T>(list: T[], items: T[], keyOf: (item: T) => string, cap: number): AddAllResult<T> {
  const next = list.slice();
  const seen = new Set(next.map(keyOf));
  let omitted = 0;
  for (const item of items) {
    const key = keyOf(item);
    if (seen.has(key)) continue;
    if (next.length >= cap) {
      omitted += 1;
      continue;
    }
    next.push(item);
    seen.add(key);
  }
  return { next, omitted };
}

/** Identity functions for the three selection channels — `toggleItem`/
 * `addAllCapped`/`replaceWith` are generic over these so a node id, a link
 * ref and an element ref all go through the exact same cap/toggle logic. */
export const nodeKey = (id: string): string => id;
export const linkKey = (link: { id: string }): string => link.id;
export const elementKey = (el: { type: string; id: string }): string => `${el.type}:${el.id}`;

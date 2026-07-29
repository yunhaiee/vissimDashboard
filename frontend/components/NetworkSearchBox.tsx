"use client";

// Network component search (v2 UX unit, 2026-07-28) -- "I know its name or
// number, where IS it?" (task brief). Backed by GET /api/network/search
// (backend/services/network_search.py), which returns a ranked, bounded list
// of hits, each already carrying a `focus` payload in the exact
// `{elements, bbox}` shape the approval-card focus machinery flashes (see
// ApprovalFocusMap.tsx's "네트워크에서 보기" button and lib/networkFocus.ts).
// This component owns ONLY the query/debounce/keyboard/dropdown -- it never
// touches NetworkViewer or the diagram itself. The host page (app/network or
// app/dashboard) turns a selection into `focusFlash`/`zoomToBbox` props via
// lib/networkFocus.ts's shared `useFocusPulse`/`buildFocusFlash`, exactly
// like the approval-card handoff already does.
//
// RE-SELECTING THE SAME RESULT MUST RE-FLASH (task brief; commit 29b5afb
// fixed the identical defect for the approval button). `onSelect` fires on
// EVERY selection, including the same hit selected twice in a row -- this
// component never dedupes/suppresses a repeat click. The fresh nonce that
// makes a repeat selection re-flash is the HOST's job (mint one per
// `onSelect` call, same "mint at the moment of the click" rule
// ApprovalFocusMap.tsx's own handleViewInNetwork follows), not this
// component's -- it has no opinion on how the host represents "the same
// target, asked again".

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { fetchNetworkSearch, type NetworkSearchHit } from "@/lib/api";
import { searchHitTypeLabel, type SearchHitLabelParts } from "@/lib/networkSearchLabel";

// Long enough that a number typed at speed ("2140") is one request rather than
// four, short enough that the dropdown still feels attached to the keyboard.
const DEBOUNCE_MS = 250;
const RESULT_LIMIT = 20;

export interface NetworkSearchSelection {
  bbox: { x_min: number; y_min: number; x_max: number; y_max: number };
  elements: { type: string; id: string }[];
  /** Raw parts (NOT a pre-composed string) needed to reconstruct the same
   * display label searchHitDisplayLabel() renders for this hit's dropdown
   * row. A host page's banner calls searchHitDisplayLabel(selection.hit, t)
   * itself, at render time, with its OWN current `t` -- so the banner
   * re-localizes on a language switch instead of freezing whatever `t` was
   * live when the user clicked. Baking the composed string into this type
   * once regressed exactly that (language switch didn't touch the banner);
   * carrying data instead of a string is what keeps the bug from coming
   * back. */
  hit: SearchHitLabelParts;
}

type Status = "idle" | "loading" | "ok" | "error";

export default function NetworkSearchBox({
  onSelect,
  className,
}: {
  onSelect: (selection: NetworkSearchSelection) => void;
  className?: string;
}) {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<NetworkSearchHit[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const debounceRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const listboxId = useId();

  // Debounced, abort-on-restart search. A fast typist must never see an
  // out-of-order response painted over a newer one -- each keystroke aborts
  // whatever request the PREVIOUS keystroke had in flight before starting
  // its own (both the pending debounce timer and any already-sent fetch).
  useEffect(() => {
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    abortRef.current?.abort();

    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setStatus("idle");
      setTotal(0);
      setTruncated(false);
      setActiveIndex(-1);
      return;
    }

    setStatus("loading");
    debounceRef.current = window.setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      fetchNetworkSearch(trimmed, { limit: RESULT_LIMIT, signal: controller.signal })
        .then((payload) => {
          if (controller.signal.aborted) return;
          setResults(payload.results);
          setTotal(payload.total);
          setTruncated(payload.truncated);
          setStatus("ok");
          setOpen(true);
          setActiveIndex(payload.results.length > 0 ? 0 : -1);
        })
        .catch(() => {
          // Includes AbortError from the cancellation above -- an aborted
          // request is not a user-visible error, it is superseded by
          // whichever keystroke triggered the abort (which will report its
          // own status once it resolves).
          if (controller.signal.aborted) return;
          setResults([]);
          setStatus("error");
          setOpen(true);
        });
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    };
  }, [query]);

  // Unmount safety net: an in-flight request must not resolve into a
  // component that no longer exists.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Click outside the control closes the dropdown (standard combobox
  // behaviour); Escape does the same via handleKeyDown below.
  useEffect(() => {
    function handlePointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, []);

  const selectHit = useCallback(
    (hit: NetworkSearchHit) => {
      // Not focusable -- no resolvable geometry anywhere in the model (e.g.
      // a speed distribution, or a link with no <geometry> at all). Visually
      // disabled below; selecting it is a deliberate no-op, not a silent one.
      if (!hit.focus.bbox) return;
      onSelect({
        bbox: hit.focus.bbox,
        elements: hit.focus.elements.map((element) => ({ type: element.type, id: element.id })),
        hit: { type: hit.type, id: hit.id, name: hit.name, label: hit.label },
      });
    },
    [onSelect],
  );

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!results.length) return;
      if (!open) setOpen(true);
      setActiveIndex((index) => Math.min(index < 0 ? 0 : index + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!results.length) return;
      if (!open) setOpen(true);
      setActiveIndex((index) => Math.max(index < 0 ? 0 : index - 1, 0));
    } else if (event.key === "Enter") {
      if (!open || activeIndex < 0 || activeIndex >= results.length) return;
      event.preventDefault();
      selectHit(results[activeIndex]);
    } else if (event.key === "Escape") {
      if (open) {
        event.preventDefault();
        setOpen(false);
      }
    }
  };

  const handleFocus = () => {
    // Reopens the SAME already-fetched results on refocus (no refetch) --
    // what lets a user re-select the same top hit a second time (re-flash)
    // without retyping the query.
    if (results.length > 0) setOpen(true);
  };

  // Clearing is a QUERY-only action: it empties the box and closes the
  // dropdown, and deliberately does NOT retract a highlight already applied to
  // the diagram (that is the host page's banner "강조 해제" button). Someone
  // who clears the box to type a second query must not have the thing they
  // just found yanked out from under them mid-look.
  const handleClear = () => {
    setQuery("");
    setOpen(false);
    inputRef.current?.focus();
  };

  // Keyboard navigation must not walk the active row out of the scrollport of
  // a 20-row dropdown that only shows ~6 at a time.
  useEffect(() => {
    if (!open || activeIndex < 0) return;
    listRef.current?.children[activeIndex]?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  const showDropdown = open && query.trim().length > 0;
  const statusMessage =
    status === "loading"
      ? t("검색 중…", "Searching…", "検索中…")
      : status === "error"
        ? t("검색을 완료하지 못했습니다.", "Couldn't complete the search.", "検索を完了できませんでした。")
        : status === "ok" && results.length === 0
          ? t("일치하는 항목이 없습니다.", "No matching results.", "一致する項目がありません。")
          : null;

  return (
    <div ref={containerRef} className={`relative ${className ?? ""}`}>
      <div className="relative">
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          fill="none"
          className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
        >
          <circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M13 13L17.5 17.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={activeIndex >= 0 && showDropdown ? `${listboxId}-opt-${activeIndex}` : undefined}
          aria-label={t("네트워크 구성요소 검색", "Search network components", "ネットワーク構成要素を検索")}
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={handleFocus}
          placeholder={t(
            "링크, 노드, 신호제어기 번호나 이름으로 검색",
            "Search by link, node, or controller — number or name",
            "リンク、ノード、信号制御機の番号や名前で検索",
          )}
          className={`ui-input h-9 pl-8 text-sm ${query ? "pr-8" : "pr-3"}`}
        />
        {query && (
          <button
            type="button"
            onClick={handleClear}
            aria-label={t("검색어 지우기", "Clear search", "検索語を消去")}
            className="absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
          >
            <span aria-hidden="true" className="text-sm leading-none">
              ×
            </span>
          </button>
        )}
      </div>

      {showDropdown && (
        <div
          className="absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-y-auto rounded-lg border border-slate-200 bg-surface shadow-[0_18px_34px_rgba(15,23,42,0.16)]"
          data-testid="network-search-dropdown"
        >
          {statusMessage ? (
            <p className="px-3 py-2.5 text-xs text-slate-500" role="status">
              {statusMessage}
            </p>
          ) : (
            <ul role="listbox" id={listboxId} ref={listRef} className="py-1">
              {results.map((hit, index) => {
                const focusable = hit.focus.bbox !== null;
                const active = index === activeIndex;
                const name = hit.name?.trim();
                return (
                  <li key={`${hit.type}:${hit.id}`} role="presentation">
                    {/* Type badge + id + name as three separate spans rather
                        than one composed sentence: on this network a link and
                        its auto-generated connector can share a display name
                        ("South St EB" is link 515 AND connector 10969), so the
                        badge and the id are what actually disambiguate a row.
                        searchHitDisplayLabel() still composes the same three
                        parts into the one string the host page's banner
                        quotes -- one vocabulary, two densities. */}
                    <button
                      type="button"
                      id={`${listboxId}-opt-${index}`}
                      role="option"
                      aria-selected={active}
                      aria-disabled={!focusable}
                      disabled={!focusable}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => selectHit(hit)}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${
                        !focusable
                          ? "cursor-not-allowed opacity-45"
                          : active
                            ? "bg-slate-100 text-slate-900"
                            : "text-slate-700 hover:bg-slate-50"
                      }`}
                    >
                      <span className="shrink-0 rounded border border-slate-200 bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                        {searchHitTypeLabel(hit, t)}
                      </span>
                      <span className="shrink-0 text-[13px] font-semibold tabular-nums">{hit.id}</span>
                      {name && <span className="min-w-0 flex-1 truncate text-[13px]">{name}</span>}
                      {!focusable && (
                        <span className="ml-auto shrink-0 text-[11px] text-slate-500">
                          {t("위치 정보 없음", "No location data", "位置情報なし")}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {status === "ok" && truncated && (
            <p className="border-t border-slate-200 px-3 py-1.5 text-[11px] text-slate-500">
              {t(
                `총 ${total}개 중 ${results.length}개 표시 · 검색어를 더 구체적으로 입력하세요`,
                `Showing ${results.length} of ${total} · refine your search to narrow it down`,
                `${total}件中${results.length}件を表示 · 検索語を絞り込んでください`,
              )}
            </p>
          )}
        </div>
      )}

      <span className="sr-only" role="status" aria-live="polite">
        {status === "ok" ? t(`검색 결과 ${total}개`, `${total} search results`, `検索結果${total}件`) : statusMessage ?? ""}
      </span>
    </div>
  );
}

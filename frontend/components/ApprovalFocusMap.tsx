"use client";

// Approval-card mini-map (v2 UX unit, 2026-07-28) -- closes the live-demo
// finding that an approval card described its target in TEXT ONLY ("conflict
// area '3' is between link 3 and link 12"), so the user could not tell WHICH
// intersection #3 was and approved essentially blind. This renders a small,
// static orientation thumbnail highlighting exactly the element(s) the
// pending write touches, using the SAME network geometry the /network page
// already fetches (fetchNetwork below -- no second, drifting geometry
// fetcher). Deliberately NOT NetworkViewer: no zoom/pan chrome, no layers
// panel, fixed small height -- it only has to answer "which intersection is
// this", not stand in for the full network preview. Reuses NetworkViewer's
// own projection conventions (y-flip, uniform/isotropic fit) so it reads
// consistently with the rest of the app.

import { useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  fetchNetwork,
  type AgentAction,
  type AgentApprovalFocus,
  type AgentApprovalFocusElement,
  type NetworkData,
} from "@/lib/api";

const MAP_HEIGHT_PX = 152;
const VIEW_W = 320;
const VIEW_H = 152;
const VIEW_MARGIN = 10;

// Matches NetworkViewer.tsx's own ACCENT constant exactly (frontend/
// components/NetworkViewer.tsx) -- this app has no dark-mode theme (verified:
// no `prefers-color-scheme`/`data-theme` rule anywhere in app/globals.css),
// so a fixed literal, not a CSS var, is the same choice that file already
// made.
const ACCENT = "#4c8dff";
const MUTED_LINK = "#c7cfdb";
const HIGHLIGHT_LINK = ACCENT;

// "Generous padding... show surrounding context, not just the element" (task
// brief): the WORLD bbox is expanded by this multiple of its own span on
// each side before fitting, so the fitted view spans roughly
// (1 + 2*PAD_RATIO)x the target's own extent -- enough surrounding network
// to orient, not so much the target shrinks to a speck.
const PAD_RATIO = 1.1;

// Module-level cache: repeated approvals in the SAME browser session (any
// session_id, including the "no session yet" `""` key) never refetch network
// geometry more than once per key -- the payload can be large on a real
// network (Beijing-sized: hundreds of links). Failed fetches are evicted so
// a transient error doesn't wedge every later approval card into permanent
// failure. Never cleared on unmount by design -- this is a page-session
// cache, same lifetime class as NetworkViewer's own zoom-transform ref
// pattern, not per-component state.
const networkCache = new Map<string, Promise<NetworkData>>();

function cachedFetchNetwork(sessionId: string | undefined): Promise<NetworkData> {
  const key = sessionId ?? "";
  const cached = networkCache.get(key);
  if (cached) return cached;
  const promise = fetchNetwork(sessionId);
  networkCache.set(key, promise);
  promise.catch(() => {
    networkCache.delete(key);
  });
  return promise;
}

type Bbox = { x_min: number; y_min: number; x_max: number; y_max: number };

function expandBbox(bbox: Bbox): Bbox {
  const spanX = Math.max(bbox.x_max - bbox.x_min, 1);
  const spanY = Math.max(bbox.y_max - bbox.y_min, 1);
  const padX = spanX * PAD_RATIO;
  const padY = spanY * PAD_RATIO;
  return {
    x_min: bbox.x_min - padX,
    x_max: bbox.x_max + padX,
    y_min: bbox.y_min - padY,
    y_max: bbox.y_max + padY,
  };
}

// Uniform (isotropic) fit within the fixed viewBox, mirroring
// NetworkViewer.tsx's own redraw effect exactly: one scale for both axes so
// the network keeps its true proportions, Y flipped (world-up ->
// screen-up). `|| 1` guards a degenerate span the same way that file's own
// `spanX = xMax - xMin || 1` does.
function fit(bbox: Bbox) {
  const spanX = bbox.x_max - bbox.x_min || 1;
  const spanY = bbox.y_max - bbox.y_min || 1;
  const availW = VIEW_W - 2 * VIEW_MARGIN;
  const availH = VIEW_H - 2 * VIEW_MARGIN;
  const s = Math.min(availW / spanX, availH / spanY);
  const offsetX = VIEW_MARGIN + (availW - spanX * s) / 2;
  const offsetY = VIEW_MARGIN + (availH - spanY * s) / 2;
  const projX = (x: number) => offsetX + (x - bbox.x_min) * s;
  const projY = (y: number) => offsetY + (bbox.y_max - y) * s;
  return { projX, projY };
}

function pointsAttr(points: [number, number][], projX: (x: number) => number, projY: (y: number) => number): string {
  return points.map(([x, y]) => `${projX(x).toFixed(1)},${projY(y).toFixed(1)}`).join(" ");
}

// type -> which NetworkElements list resolves it (everything except "link",
// which resolves against NetworkData.links directly, and "conflict_area",
// which carries no geometry of its own -- see AgentApprovalFocusElement's
// own doc comment in lib/api.ts).
const ELEMENT_LIST_KEY: Partial<Record<AgentApprovalFocusElement["type"], keyof NonNullable<NetworkData["elements"]>>> = {
  signal_head: "signal_heads",
  vehicle_input: "vehicle_inputs",
  des_speed_decision: "des_speed_decisions",
};

function primaryFocusLabel(action: AgentAction | undefined, t: (ko: string, en: string, ja?: string) => string): string {
  const p = (action?.params ?? {}) as Record<string, unknown>;
  switch (action?.tool) {
    case "set_vehicle_input_volume":
      return t(`차량 입력 ${p.input_no}`, `Vehicle input ${p.input_no}`, `車両インプット${p.input_no}`);
    case "set_signal_cycle":
    case "set_signal_offset":
      return t(`신호제어기 ${p.sc_no}`, `Signal controller ${p.sc_no}`, `信号制御機${p.sc_no}`);
    case "set_conflict_area_status":
      return t(`충돌구간 ${p.conflict_area_no}`, `Conflict area ${p.conflict_area_no}`, `錯綜区間${p.conflict_area_no}`);
    case "set_reduced_speed_area":
      return t(`링크 ${p.link_id}`, `Link ${p.link_id}`, `リンク${p.link_id}`);
    case "set_route_relative_flow":
      return t(
        `차량 경로 결정 ${p.decision_no}의 경로 ${p.route_no}`,
        `Route ${p.route_no} of routing decision ${p.decision_no}`,
        `経路決定${p.decision_no}のルート${p.route_no}`,
      );
    case "set_vehicle_composition_share":
      return t(
        `차량 조성 ${p.composition_no} (차종 ${p.veh_type_no})`,
        `Vehicle composition ${p.composition_no} (vehicle type ${p.veh_type_no})`,
        `車両構成${p.composition_no}(車種${p.veh_type_no})`,
      );
    case "set_des_speed_decision": {
      const base = t(
        `희망속도결정 ${p.decision_no}`,
        `Desired speed decision ${p.decision_no}`,
        `希望速度決定${p.decision_no}`,
      );
      if (!p.restore_decision_no) return base;
      return `${base}${t(` · 복원 ${p.restore_decision_no}`, ` · restore ${p.restore_decision_no}`, ` · 復元${p.restore_decision_no}`)}`;
    }
    case "set_signal_green_split":
      return t(
        `신호제어기 ${p.sc_no} 프로그램 ${p.program_no} 신호군 ${p.sg_no}`,
        `Controller ${p.sc_no} program ${p.program_no} signal group ${p.sg_no}`,
        `制御機${p.sc_no}プログラム${p.program_no}信号群${p.sg_no}`,
      );
    default:
      return action?.summary ?? "";
  }
}

function buildCaption(
  focus: AgentApprovalFocus,
  action: AgentAction | undefined,
  t: (ko: string, en: string, ja?: string) => string,
): string {
  const primary = primaryFocusLabel(action, t);
  if (action?.tool !== "set_conflict_area_status") return primary;
  const linkIds = focus.elements.filter((e) => e.type === "link").slice(0, 2);
  if (linkIds.length < 2) return primary;
  // Link ID, not name -- live-verified against the real active dataset
  // (SouthStreet_Base.inpx, conflict area 2140): a link and its auto-
  // generated connector can share the exact same display name ("South St
  // EB" on both link 515 and connector 10969), which would make a
  // name-based "A ↔ B" clause read as a no-op ("South St EB ↔ South St
  // EB"). Ids are always distinct and match the task brief's own example
  // format ("링크 3 ↔ 링크 12").
  const [a, b] = linkIds;
  return `${primary} · ${t(`링크 ${a.id}`, `link ${a.id}`, `リンク${a.id}`)} ↔ ${t(`링크 ${b.id}`, `link ${b.id}`, `リンク${b.id}`)}`;
}

export default function ApprovalFocusMap({
  focus,
  sessionId,
  action,
}: {
  focus: AgentApprovalFocus | null | undefined;
  sessionId: string;
  action: AgentAction | undefined;
}) {
  const { t } = useLanguage();
  const [network, setNetwork] = useState<NetworkData | null>(null);
  const [failed, setFailed] = useState(false);
  const bbox = focus?.bbox ?? null;

  useEffect(() => {
    if (!bbox) return;
    let cancelled = false;
    setFailed(false);
    cachedFetchNetwork(sessionId || undefined)
      .then((data) => {
        if (!cancelled) setNetwork(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bbox ? `${bbox.x_min},${bbox.y_min},${bbox.x_max},${bbox.y_max}` : null, sessionId]);

  const highlightSets = useMemo(() => {
    const linkIds = new Set<string>();
    const pointsByListKey = new Map<string, Set<string>>();
    for (const el of focus?.elements ?? []) {
      if (el.type === "link") {
        linkIds.add(el.id);
        continue;
      }
      const listKey = ELEMENT_LIST_KEY[el.type];
      if (!listKey) continue;
      if (!pointsByListKey.has(listKey)) pointsByListKey.set(listKey, new Set());
      pointsByListKey.get(listKey)!.add(el.id);
    }
    return { linkIds, pointsByListKey };
  }, [focus]);

  // Task contract: no bbox -> no map, no layout reservation, and never an
  // error blob on failure -- the text approval must always work on its own.
  if (!focus || !bbox || failed) return null;

  const caption = buildCaption(focus, action, t);
  const expanded = expandBbox(bbox);
  const { projX, projY } = fit(expanded);

  return (
    <div className="mt-2">
      <div
        className="relative w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-50"
        style={{ height: MAP_HEIGHT_PX }}
      >
        {network ? (
          <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} preserveAspectRatio="xMidYMid meet" className="h-full w-full">
            <defs>
              <filter id="approval-focus-glow" x="-150%" y="-150%" width="400%" height="400%">
                <feGaussianBlur stdDeviation="3.2" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            {network.links.map((link) => {
              const highlighted = highlightSets.linkIds.has(link.id);
              if (!link.points || link.points.length < 2) return null;
              return (
                <polyline
                  key={link.id}
                  points={pointsAttr(link.points, projX, projY)}
                  fill="none"
                  stroke={highlighted ? HIGHLIGHT_LINK : MUTED_LINK}
                  strokeWidth={highlighted ? 3 : 1}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  filter={highlighted ? "url(#approval-focus-glow)" : undefined}
                  opacity={highlighted ? 1 : 0.7}
                />
              );
            })}
            {Array.from(highlightSets.pointsByListKey.entries()).flatMap(([listKey, ids]) => {
              const list = (network.elements?.[listKey as keyof NonNullable<NetworkData["elements"]>] ?? []) as {
                id: string;
                x: number;
                y: number;
              }[];
              return list
                .filter((item) => ids.has(item.id))
                .map((item) => (
                  <circle
                    key={`${listKey}-${item.id}`}
                    cx={projX(item.x)}
                    cy={projY(item.y)}
                    r={4.5}
                    fill={ACCENT}
                    stroke="#ffffff"
                    strokeWidth={1.2}
                    filter="url(#approval-focus-glow)"
                  />
                ));
            })}
          </svg>
        ) : (
          <div className="h-full w-full animate-pulse bg-slate-100" aria-hidden="true" />
        )}
      </div>
      <p className="mt-1 text-[10px] font-medium text-slate-500">{caption}</p>
    </div>
  );
}

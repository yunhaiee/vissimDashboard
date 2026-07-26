"use client";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { fetchTrajectories, type TrajectoryFrame, type TrajectoryVehicleFrame } from "@/lib/api";
import type { ViewProjection } from "@/components/NetworkViewer";

// Phase 3 P4 — vehicle playback overlay for the network viewer.
//
// Three pieces live here:
//   1. createProjectionStore()   — the tiny pub/sub NetworkViewer pushes its
//      ViewProjection into. Deliberately NOT React state: zoom/pan emits once
//      per animation frame, and routing that through setState would re-render
//      the whole page on every frame of a drag.
//   2. createPlaybackEngine() + useTrajectoryPlayback() — fetch and transport
//      (rAF clock, speed, seek, loop). The engine is a plain external store, so
//      the sim clock can advance ~60x/sec while React only sees a new snapshot
//      every PUBLISH_INTERVAL_MS; the hook is a thin useSyncExternalStore
//      binding over it.
//   3. <TrajectoryLayer/>       — the canvas. Registers a renderer with the
//      engine and draws whatever sim time it is handed; it holds no clock.
//
// Vehicle coordinates arrive as RAW network metres with a math-convention angle
// (0 = +x, CCW positive). The viewer flips Y, which negates the angle on
// screen — the same rule NetworkViewer's dirsFromAngle() documents.

// --- projection store -------------------------------------------------------

export interface ProjectionStore {
  get: () => ViewProjection | null;
  set: (projection: ViewProjection | null) => void;
  subscribe: (listener: () => void) => () => void;
}

export function createProjectionStore(): ProjectionStore {
  let current: ViewProjection | null = null;
  const listeners = new Set<() => void>();
  return {
    get: () => current,
    set: (projection) => {
      current = projection;
      for (const listener of listeners) listener();
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

// --- playback engine --------------------------------------------------------

export type PlaybackStatus = "idle" | "loading" | "ready" | "error";

// What React is allowed to see. Replaced wholesale whenever it changes, so
// useSyncExternalStore's referential-stability contract holds.
export interface PlaybackSnapshot {
  status: PlaybackStatus;
  frames: TrajectoryFrame[] | null;
  playing: boolean;
  speed: number;
  /** Throttled mirror of the internal clock, in sim seconds. */
  time: number;
  t0: number;
  t1: number;
  /** Vehicles in the frame bracketing `time`. */
  vehicleCount: number;
}

// Stable-identity imperative surface. Created once per hook instance so the
// canvas layer's registration effect only re-runs when the FRAMES change, and
// so the page can reset playback from an effect without dependency churn.
export interface PlaybackEngine {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => PlaybackSnapshot;
  registerRenderer: (render: ((time: number) => void) | null) => void;
  /** Draw exactly one frame at the current sim time (zoom/resize while paused). */
  requestFrame: () => void;
  load: () => void;
  togglePlay: () => void;
  seek: (time: number) => void;
  setSpeed: (speed: number) => void;
  /** Back to the un-loaded state — used when the active dataset changes. */
  reset: () => void;
  /** Pause + drop any queued frame. Called on unmount; safe to re-use after. */
  stop: () => void;
}

export type Playback = PlaybackSnapshot & { engine: PlaybackEngine };

// Clock -> React state mirror rate. 10 Hz keeps the scrubber and the time
// readout smooth to the eye while costing ~10 renders/sec instead of ~60.
const PUBLISH_INTERVAL_MS = 100;
// Longest wall-clock gap credited to one tick. Without this, returning to a
// backgrounded tab would fast-forward the sim by the whole idle period.
const MAX_TICK_SECONDS = 0.25;

export const PLAYBACK_SPEEDS = [1, 2, 4] as const;

function deriveBounds(frames: TrajectoryFrame[], metaT0: number | null, metaT1: number | null): { t0: number; t1: number } {
  if (!frames.length) return { t0: 0, t1: 0 };
  const first = frames[0].t;
  const last = frames[frames.length - 1].t;
  const t0 = Number.isFinite(metaT0 as number) ? (metaT0 as number) : first;
  const t1 = Number.isFinite(metaT1 as number) ? (metaT1 as number) : last;
  return t1 > t0 ? { t0, t1 } : { t0: first, t1: Math.max(last, first) };
}

// Index of the last frame at or before `time` (binary search — frames are
// server-sorted by t and there can be hundreds of them).
function bracketIndex(frames: TrajectoryFrame[], time: number): number {
  if (time <= frames[0].t) return 0;
  const lastIdx = frames.length - 1;
  if (time >= frames[lastIdx].t) return lastIdx;
  let lo = 0;
  let hi = lastIdx;
  let ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t <= time) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

const IDLE_SNAPSHOT: PlaybackSnapshot = {
  status: "idle",
  frames: null,
  playing: false,
  speed: 1,
  time: 0,
  t0: 0,
  t1: 0,
  vehicleCount: 0,
};

// Plain (non-React) external store. Everything the rAF loop touches is a
// closure variable rather than React state, so the clock can advance every
// animation frame while React is only notified on a real snapshot change.
export function createPlaybackEngine(): PlaybackEngine {
  const listeners = new Set<() => void>();
  let snapshot: PlaybackSnapshot = IDLE_SNAPSHOT;

  let frames: TrajectoryFrame[] | null = null;
  let time = 0;
  let t0 = 0;
  let t1 = 0;
  let playing = false;
  let speed = 1;
  let renderer: ((time: number) => void) | null = null;
  let raf = 0;
  let lastWall = 0;
  let lastPublish = 0;
  let loading = false;

  const emit = () => {
    for (const listener of listeners) listener();
  };
  const commit = (patch: Partial<PlaybackSnapshot>) => {
    snapshot = { ...snapshot, ...patch };
    emit();
  };
  // Mirror the live clock (and the vehicle count at that instant) into the
  // snapshot. Called at most every PUBLISH_INTERVAL_MS while playing.
  const publish = () => {
    commit({ time, vehicleCount: frames && frames.length ? frames[bracketIndex(frames, time)].vehicles.length : 0 });
  };
  const cancelPending = () => {
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
  };

  function frameLoop() {
    raf = 0;
    if (!playing) {
      renderer?.(time);
      return;
    }
    const now = performance.now();
    const dt = Math.min(Math.max((now - lastWall) / 1000, 0), MAX_TICK_SECONDS);
    lastWall = now;

    const span = t1 - t0;
    let next = time + dt * speed;
    if (span > 0) {
      while (next > t1) next -= span; // loop back to the start
    } else {
      next = t0;
    }
    time = next;

    renderer?.(time);
    if (now - lastPublish >= PUBLISH_INTERVAL_MS) {
      lastPublish = now;
      publish();
    }
    raf = requestAnimationFrame(frameLoop);
  }

  const requestFrame = () => {
    if (raf) return; // a loop tick or one-off frame is already queued
    raf = requestAnimationFrame(() => {
      raf = 0;
      renderer?.(time);
    });
  };

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    registerRenderer(render) {
      renderer = render;
    },
    requestFrame,
    // Lazily fetched: the full frame set is hundreds of KB, so nothing is
    // requested until the user asks for playback (or retries after a failure).
    load() {
      if (loading) return;
      loading = true;
      commit({ status: "loading" });
      fetchTrajectories()
        .then((res) => {
          const list = res.frames ?? [];
          if (!list.length) {
            commit({ status: "error" });
            return;
          }
          const bounds = deriveBounds(list, res.meta?.t0 ?? null, res.meta?.t1 ?? null);
          frames = list;
          t0 = bounds.t0;
          t1 = bounds.t1;
          time = bounds.t0;
          commit({ status: "ready", frames: list, t0, t1, time });
          publish();
        })
        .catch(() => {
          commit({ status: "error" });
        })
        .finally(() => {
          loading = false;
        });
    },
    togglePlay() {
      playing = !playing;
      cancelPending();
      if (playing) {
        // Pressing play while parked at the very end restarts from the top.
        if (time >= t1) time = t0;
        lastWall = performance.now();
        lastPublish = 0;
        raf = requestAnimationFrame(frameLoop);
      }
      commit({ playing });
      publish();
    },
    seek(target) {
      time = Math.min(Math.max(target, t0), t1);
      lastWall = performance.now();
      publish();
      if (!playing) requestFrame();
    },
    setSpeed(next) {
      speed = next;
      commit({ speed: next });
    },
    reset() {
      const alreadyIdle = snapshot.status === "idle" && snapshot.frames === null && !snapshot.playing && snapshot.time === 0;
      playing = false;
      cancelPending();
      frames = null;
      t0 = 0;
      t1 = 0;
      time = 0;
      loading = false;
      if (alreadyIdle) return;
      snapshot = { ...IDLE_SNAPSHOT, speed }; // the speed choice survives a dataset swap
      emit();
    },
    stop() {
      cancelPending();
      if (playing) {
        playing = false;
        commit({ playing: false });
      }
    },
  };
}

// Thin useSyncExternalStore binding. The engine outlives individual renders
// (created once via a lazy useState initializer), so its identity is stable and
// safe to put in effect dependency arrays.
export function useTrajectoryPlayback(): Playback {
  const [engine] = useState(createPlaybackEngine);
  const snapshot = useSyncExternalStore(engine.subscribe, engine.getSnapshot, engine.getSnapshot);
  useEffect(() => engine.stop, [engine]);
  return { ...snapshot, engine };
}

// --- canvas rendering -------------------------------------------------------

// Metre-true car footprint, drawn as a capsule at the viewer's live scale. The
// floor keeps vehicles visible when the whole network is fitted to the viewport
// (aspect ratio is preserved because both dimensions share one scale).
const VEHICLE_LENGTH_M = 4.5;
const VEHICLE_WIDTH_M = 1.8;
const MIN_VEHICLE_LENGTH_PX = 3;
// Palette: light slate body on the #3a4150 asphalt, outlined in the existing
// road-casing slate. Stopped vehicles take a muted warm tint (a sibling of the
// reduced-speed amber) so queues read at a glance without a new loud hue.
const VEHICLE_FILL = "#cbd5e1";
const VEHICLE_FILL_STOPPED = "#d6b183";
const VEHICLE_STROKE = "#232a36";
const OUTLINE_MIN_LENGTH_PX = 6; // below this the outline just muddies the body
const CULL_PAD_PX = 24;

// Per-frame id lookup, memoized on the frame object itself. Only frames the
// playhead actually visits get a map built, and the entries disappear with the
// frames when a new dataset is loaded.
const frameVehicleMaps = new WeakMap<TrajectoryFrame, Map<number, TrajectoryVehicleFrame>>();
function vehicleMap(frame: TrajectoryFrame): Map<number, TrajectoryVehicleFrame> {
  let map = frameVehicleMaps.get(frame);
  if (!map) {
    map = new Map();
    for (const vehicle of frame.vehicles) map.set(vehicle.id, vehicle);
    frameVehicleMaps.set(frame, map);
  }
  return map;
}

// Shortest-arc interpolation between two headings in degrees (so 350° -> 10°
// sweeps 20° forward rather than 340° backwards).
function lerpAngleDeg(from: number, to: number, u: number): number {
  const delta = ((to - from + 540) % 360) - 180;
  return from + delta * u;
}

function capsulePath(ctx: CanvasRenderingContext2D, length: number, width: number) {
  const r = width / 2;
  const halfL = length / 2;
  ctx.beginPath();
  ctx.moveTo(-halfL + r, -r);
  ctx.lineTo(halfL - r, -r);
  ctx.arc(halfL - r, 0, r, -Math.PI / 2, Math.PI / 2);
  ctx.lineTo(-halfL + r, r);
  ctx.arc(-halfL + r, 0, r, Math.PI / 2, -Math.PI / 2);
  ctx.closePath();
}

function drawVehicles(
  ctx: CanvasRenderingContext2D,
  cssW: number,
  cssH: number,
  projection: ViewProjection,
  frames: TrajectoryFrame[],
  time: number,
) {
  const index = bracketIndex(frames, time);
  const current = frames[index];
  const nextFrame = index + 1 < frames.length ? frames[index + 1] : null;
  const span = nextFrame ? nextFrame.t - current.t : 0;
  const u = nextFrame && span > 1e-6 ? Math.min(Math.max((time - current.t) / span, 0), 1) : 0;
  const nextById = nextFrame && u > 0 ? vehicleMap(nextFrame) : null;

  const { k, s, tx, ty, offsetX, offsetY, xMin, yMax } = projection;
  const pxPerMeter = s * k;
  const drawScale = Math.max(pxPerMeter, MIN_VEHICLE_LENGTH_PX / VEHICLE_LENGTH_M);
  const lengthPx = VEHICLE_LENGTH_M * drawScale;
  const widthPx = VEHICLE_WIDTH_M * drawScale;
  const outlined = lengthPx >= OUTLINE_MIN_LENGTH_PX;

  ctx.lineWidth = Math.min(Math.max(lengthPx * 0.09, 0.5), 1.4);
  ctx.strokeStyle = VEHICLE_STROKE;

  for (const vehicle of current.vehicles) {
    if (vehicle.x == null || vehicle.y == null) continue;
    let x = vehicle.x;
    let y = vehicle.y;
    let angle = vehicle.angle ?? 0;

    // Linear interpolation toward the bracketing next frame. A vehicle missing
    // from that frame has left the network mid-interval, so it simply holds its
    // last known pose until the playhead crosses the boundary and drops it.
    const ahead = nextById?.get(vehicle.id);
    if (ahead && ahead.x != null && ahead.y != null) {
      x += (ahead.x - x) * u;
      y += (ahead.y - y) * u;
      angle = lerpAngleDeg(angle, ahead.angle ?? angle, u);
    }

    const sx = tx + k * (offsetX + (x - xMin) * s);
    const sy = ty + k * (offsetY + (yMax - y) * s);
    if (sx < -CULL_PAD_PX || sy < -CULL_PAD_PX || sx > cssW + CULL_PAD_PX || sy > cssH + CULL_PAD_PX) continue;

    ctx.save();
    ctx.translate(sx, sy);
    // Negated because the viewer's Y-flip reflects the plane (see the module
    // header and NetworkViewer's dirsFromAngle).
    ctx.rotate((-angle * Math.PI) / 180);
    capsulePath(ctx, lengthPx, widthPx);
    ctx.fillStyle = (vehicle.speed ?? 0) <= 0 ? VEHICLE_FILL_STOPPED : VEHICLE_FILL;
    ctx.fill();
    if (outlined) ctx.stroke();
    ctx.restore();
  }
}

export default function TrajectoryLayer({
  engine,
  frames,
  projection,
}: {
  engine: PlaybackEngine;
  frames: TrajectoryFrame[] | null;
  projection: ProjectionStore;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // CSS size + devicePixelRatio are cached and refreshed only on resize, so
    // the per-frame draw never forces a layout read.
    let cssW = canvas.clientWidth || 0;
    let cssH = canvas.clientHeight || 0;
    let dpr = window.devicePixelRatio || 1;
    const applySize = () => {
      dpr = window.devicePixelRatio || 1;
      const bw = Math.max(Math.round(cssW * dpr), 1);
      const bh = Math.max(Math.round(cssH * dpr), 1);
      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }
    };
    applySize();

    const draw = (time: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);
      const current = projection.get();
      if (!current || !frames || !frames.length) return;
      drawVehicles(ctx, cssW, cssH, current, frames, time);
    };

    engine.registerRenderer(draw);
    engine.requestFrame();

    // Zoom/pan: NetworkViewer already throttles its emits to one per animation
    // frame, and requestFrame() coalesces, so this is at most one redraw/frame.
    const unsubscribe = projection.subscribe(() => engine.requestFrame());

    let resizeRaf = 0;
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver((entries) => {
        const rect = entries[0]?.contentRect;
        if (rect) {
          cssW = rect.width;
          cssH = rect.height;
        }
        if (resizeRaf) return;
        resizeRaf = requestAnimationFrame(() => {
          resizeRaf = 0;
          applySize();
          engine.requestFrame();
        });
      });
      observer.observe(canvas);
    }

    return () => {
      unsubscribe();
      observer?.disconnect();
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      engine.registerRenderer(null);
    };
  }, [engine, frames, projection]);

  return <canvas ref={canvasRef} aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full" />;
}

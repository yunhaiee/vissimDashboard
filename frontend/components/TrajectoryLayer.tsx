"use client";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { fetchTrajectories, type NetworkData, type TrajectoryFrame, type TrajectoryVehicleFrame } from "@/lib/api";
import { DEFAULT_LANE_M, MIN_ROAD_PX, linkWidthMeters } from "@/lib/roadWidth";
import { type ViewProjection } from "@/components/NetworkViewer";

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
//
// P5 (playback fidelity) added a fourth piece between (2) and (3):
//   buildLinkGeometryIndex() + pointAtPos() + interpolateOnLink(), which
//   interpolate along the link's real centreline instead of chording between
//   frames, and the lane-relative sizing in drawVehicles(). Both need the
//   `network` prop; without it the layer degrades to exactly its P4 behaviour.

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
      // Isolate listener failures: this set() is called from inside
      // NetworkViewer's zoom handler and draw effect, so a throwing listener
      // (seen live: a hot-reload-stale PlaybackEngine missing a new method)
      // would otherwise abort the notify loop AND unwind the caller — freezing
      // the vehicle canvas at a stale transform while the SVG keeps moving.
      for (const listener of listeners) {
        try {
          listener();
        } catch (err) {
          console.error("projection listener failed", err);
        }
      }
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
  /** Draw immediately using the current sim time, with NO rAF indirection —
   * for a caller that is already running inside a throttled animation-frame
   * callback (the projection/zoom-transform update below) and would
   * otherwise pay ANOTHER frame of latency by going through requestFrame's
   * own queuing. See its call site for why that extra hop matters. */
  renderNow: () => void;
  /** `jobId` omitted -> default file-next-to-dataset/demo source; given ->
   * replay that completed agent-run job's own recorded trajectory. */
  load: (jobId?: string) => void;
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
    renderNow() {
      renderer?.(time);
    },
    // Lazily fetched: the full frame set is hundreds of KB, so nothing is
    // requested until the user asks for playback (or retries after a failure).
    // `jobId`, when given, replays that completed agent-run job's own
    // recorded trajectory (backend/routers/trajectories.py's `job_id` query
    // param) instead of the default file-next-to-dataset/demo source.
    load(jobId?: string) {
      if (loading) return;
      loading = true;
      commit({ status: "loading" });
      fetchTrajectories(jobId ? { jobId } : undefined)
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

// --- link geometry (P5 path-accurate interpolation) -------------------------

// The server resolves every frame's x/y as
//   point_at_pos(link.points, pos)  +  lane offset along that segment's normal
// (backend/services/trajectory_reader.py -> inpx_parser.lane_offset_point).
// Frames are 0.5 s apart, so lerping the resulting x/y directly draws a straight
// CHORD between two points on a curve — on a roundabout or a junction turn that
// chord leaves the pavement. The fix is to interpolate the Vissim-native `pos`
// scalar along the real polyline instead, and carry the server's lane offset
// across as a separate 2-vector (see interpolateOnLink below).
//
// LinkGeometry is the precompute that makes that cheap: the link's NON-degenerate
// segments in raw metre space, their cumulative arc-length starts, and their
// headings — plus the two numbers the sizing pass needs. Zero-length segments are
// dropped exactly like backend point_at_pos() skips them, so identical inputs
// resolve to identical centreline points on both sides.
export interface LinkGeometry {
  /** Segment start/end coordinates, raw network metres. */
  ax: Float64Array;
  ay: Float64Array;
  bx: Float64Array;
  by: Float64Array;
  /** Arc length at each segment START; length = segments + 1, last entry = total. */
  cum: Float64Array;
  /** Per-segment heading in degrees, math convention (0 = +x, CCW positive). */
  ang: Float64Array;
  /** Total polyline length in metres (the clamp bound, like the backend's). */
  length: number;
  /** Total roadway width — same three-shape tolerance the road render uses. */
  widthTotalM: number;
  lanes: number;
}

export type LinkGeometryIndex = Map<string, LinkGeometry>;

const GEOM_EPS = 1e-9;

// Build the per-link index once per network. Links that can't support the walk
// (fewer than two vertices, non-finite coordinates, every segment degenerate)
// are simply absent from the map — callers fall back to the plain x/y lerp for
// those rather than guessing.
export function buildLinkGeometryIndex(network: NetworkData | null | undefined): LinkGeometryIndex | null {
  if (!network || !network.links?.length) return null;
  const index: LinkGeometryIndex = new Map();
  for (const link of network.links) {
    const pts = link.points;
    if (!pts || pts.length < 2) continue;
    const ax: number[] = [];
    const ay: number[] = [];
    const bx: number[] = [];
    const by: number[] = [];
    const cum: number[] = [];
    const ang: number[] = [];
    let acc = 0;
    for (let i = 1; i < pts.length; i++) {
      const x0 = pts[i - 1][0];
      const y0 = pts[i - 1][1];
      const x1 = pts[i][0];
      const y1 = pts[i][1];
      if (!Number.isFinite(x0) || !Number.isFinite(y0) || !Number.isFinite(x1) || !Number.isFinite(y1)) continue;
      const dx = x1 - x0;
      const dy = y1 - y0;
      const segLen = Math.hypot(dx, dy);
      if (!(segLen > GEOM_EPS)) continue; // coincident vertices — backend skips these too
      ax.push(x0);
      ay.push(y0);
      bx.push(x1);
      by.push(y1);
      cum.push(acc);
      ang.push((Math.atan2(dy, dx) * 180) / Math.PI);
      acc += segLen;
    }
    if (!ax.length) continue; // fully degenerate polyline
    cum.push(acc);
    index.set(String(link.id), {
      ax: Float64Array.from(ax),
      ay: Float64Array.from(ay),
      bx: Float64Array.from(bx),
      by: Float64Array.from(by),
      cum: Float64Array.from(cum),
      ang: Float64Array.from(ang),
      length: acc,
      widthTotalM: linkWidthMeters(link),
      lanes: Math.max(Math.round(link.lanes ?? 1), 1),
    });
  }
  return index.size ? index : null;
}

interface Placement {
  x: number;
  y: number;
  angle: number;
}

// Reusable output cell — pointAtPos() runs up to three times per vehicle per
// animation frame, and allocating a fresh object each time would churn the GC at
// 60 Hz. Callers copy the scalars out immediately.
const placement: Placement = { x: 0, y: 0, angle: 0 };

// Client mirror of backend inpx_parser.point_at_pos(): absolute (x, y, heading)
// at arc-length `pos` along the centreline. `pos` is clamped into [0, length]
// (same reason the backend clamps: float noise / stale positions), and the angle
// is the heading of the segment CONTAINING pos — the backend picks the first
// segment whose END reaches pos, and the binary search below picks the same one,
// so a position landing exactly on a vertex resolves identically on both sides.
// O(log n) per call; writes into `out` and returns false only for a bad pos.
function pointAtPos(geom: LinkGeometry, pos: number, out: Placement): boolean {
  if (!Number.isFinite(pos)) return false;
  const m = geom.ang.length;
  const p = Math.min(Math.max(pos, 0), geom.length);
  let lo = 0;
  let hi = m - 1;
  let j = m - 1; // fallthrough = last segment, mirroring the backend's tail case
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (geom.cum[mid + 1] >= p) {
      j = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  const segLen = geom.cum[j + 1] - geom.cum[j];
  const tt = segLen > GEOM_EPS ? Math.min(Math.max((p - geom.cum[j]) / segLen, 0), 1) : 0;
  out.x = geom.ax[j] + (geom.bx[j] - geom.ax[j]) * tt;
  out.y = geom.ay[j] + (geom.by[j] - geom.ay[j]) * tt;
  out.angle = geom.ang[j];
  return true;
}

// Path-accurate pose for a vehicle whose bracketing frames sit on the SAME link.
//
//   1. recover each frame's lane offset as the 2-vector from the centreline
//      point at that frame's `pos` to the server-resolved x/y (this is exactly
//      lane_offset_point's normal * s_center, so nothing about the server's lane
//      placement is re-derived or assumed here);
//   2. interpolate the `pos` SCALAR and walk the real polyline to it — this is
//      what stops the car cutting the corner on a curve;
//   3. lerp the offset 2-vector and re-scale it to the lerped offset LENGTH,
//      then add it to the interpolated centreline point.
//
// Step 3's re-scale is the lane-flip guard. A straight lerp of the two offset
// vectors is a chord between them, so when the segment normal rotates inside one
// 0.5 s bracket (a sharp vertex, a roundabout) the offset shortens by up to
// (1 - cos(theta/2)) and the car drifts toward the centreline mid-interval — ~1 m
// at a 90 deg vertex. Renormalising to the lerped length keeps the lateral
// distance exact while still landing on the server's own x/y at u = 0 and u = 1
// (the scale factor is 1 at both ends), so frames stay continuous across bracket
// boundaries. The alternative — re-applying the offset along the interpolated
// segment's normal — is rejected: that normal is discontinuous at every vertex
// and would TELEPORT the car sideways there.
//
// Returns false (caller falls back to the straight x/y lerp) when the link is
// unknown/degenerate, either pos is non-finite, or `pos` moves backwards — the
// last case means a wrap-around onto the same link (or noise), never real travel.
function interpolateOnLink(
  geom: LinkGeometry,
  posA: number,
  xA: number,
  yA: number,
  posB: number,
  xB: number,
  yB: number,
  u: number,
  out: Placement,
): boolean {
  if (!Number.isFinite(posA) || !Number.isFinite(posB) || posB < posA) return false;
  if (!pointAtPos(geom, posA, out)) return false;
  const offAx = xA - out.x;
  const offAy = yA - out.y;
  if (!pointAtPos(geom, posB, out)) return false;
  const offBx = xB - out.x;
  const offBy = yB - out.y;
  if (!pointAtPos(geom, posA + (posB - posA) * u, out)) return false;

  let ox = offAx + (offBx - offAx) * u;
  let oy = offAy + (offBy - offAy) * u;
  const lenA = Math.hypot(offAx, offAy);
  const lenB = Math.hypot(offBx, offBy);
  const want = lenA + (lenB - lenA) * u;
  const have = Math.hypot(ox, oy);
  // `have` only collapses when the two offsets are near-opposite (a ~180 deg
  // normal flip inside one bracket); leave the chord alone rather than divide by
  // ~zero and fling the car across the road.
  if (have > GEOM_EPS) {
    const f = want / have;
    ox *= f;
    oy *= f;
  }
  out.x += ox;
  out.y += oy;
  return true;
}

// --- canvas rendering -------------------------------------------------------

// Vehicle footprint. Metre-true (4.5 m x 1.8 m) is the FLOOR of the visual size,
// not the whole story: at a wide base fit the roads themselves are drawn at
// max(width * s, MIN_ROAD_PX), so a metre-true car ends up a sub-pixel speck
// inside a floored-open road. Vehicles therefore also scale off the same
// on-screen lane width the road render implies (see laneOnScreenPx), which is
// what makes them read like Vissim's 2D view at every zoom.
const VEHICLE_LENGTH_M = 4.5;
// 1.8 / 4.5 — applying it to the final length keeps the true 4.5x1.8 footprint in
// the metre-true regime and scales it proportionally in the lane-driven one.
const VEHICLE_ASPECT = 0.4;
// A car occupies this many lane-widths of LENGTH (so VEHICLE_ASPECT * this = 0.72
// lane widths across — a car sitting properly inside its lane, not filling it).
const VEHICLE_LANE_LENGTHS = 1.8;
const MIN_VEHICLE_LENGTH_PX = 7;
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
  links: LinkGeometryIndex | null,
) {
  const index = bracketIndex(frames, time);
  const current = frames[index];
  const nextFrame = index + 1 < frames.length ? frames[index + 1] : null;
  const span = nextFrame ? nextFrame.t - current.t : 0;
  const u = nextFrame && span > 1e-6 ? Math.min(Math.max((time - current.t) / span, 0), 1) : 0;
  const nextById = nextFrame && u > 0 ? vehicleMap(nextFrame) : null;

  const { k, s, tx, ty, offsetX, offsetY, xMin, yMax } = projection;
  const metreLengthPx = VEHICLE_LENGTH_M * s * k;

  // The on-screen width of ONE lane of a link, reproducing NetworkViewer's road
  // rule exactly: it draws each surface at max(widthTotal * s, MIN_ROAD_PX) in
  // the un-zoomed group, which the d3 transform then scales by k. Dividing by the
  // lane count gives the lane the vehicle is actually sitting in. An unknown link
  // falls back to a single default-width lane — the same number a 3.5 m one-lane
  // link would produce, floor included, so sizing never jumps at the boundary.
  const laneOnScreenPx = (widthTotalM: number, lanes: number) => (Math.max(widthTotalM * s, MIN_ROAD_PX) * k) / lanes;
  const fallbackLanePx = laneOnScreenPx(DEFAULT_LANE_M, 1);

  ctx.strokeStyle = VEHICLE_STROKE;
  let appliedLineWidth = -1;

  for (const vehicle of current.vehicles) {
    if (vehicle.x == null || vehicle.y == null) continue;
    const geom = links?.get(String(vehicle.link)) ?? null;

    let x = vehicle.x;
    let y = vehicle.y;
    let angle = vehicle.angle ?? 0;

    // Interpolation toward the bracketing next frame. A vehicle missing from that
    // frame has left the network mid-interval, so it simply holds its last known
    // pose until the playhead crosses the boundary and drops it.
    const ahead = nextById?.get(vehicle.id);
    if (ahead && ahead.x != null && ahead.y != null) {
      // Same link -> walk the real centreline (see interpolateOnLink). Different
      // links -> the straight x/y lerp: those transitions last a single 0.5 s
      // bracket and happen inside junction pavement, where a chord stays on the
      // road. Heading then comes from the polyline segment at the interpolated
      // pos rather than a frame-to-frame lerp, which is what removes the
      // corner-cut headings on the roundabout.
      const sameLink = geom != null && String(ahead.link) === String(vehicle.link);
      const walked =
        sameLink &&
        interpolateOnLink(geom, vehicle.pos, vehicle.x, vehicle.y, ahead.pos, ahead.x, ahead.y, u, placement);
      if (walked) {
        x = placement.x;
        y = placement.y;
        angle = placement.angle;
      } else {
        x += (ahead.x - x) * u;
        y += (ahead.y - y) * u;
        angle = lerpAngleDeg(angle, ahead.angle ?? angle, u);
      }
    }

    const lanePx = geom ? laneOnScreenPx(geom.widthTotalM, geom.lanes) : fallbackLanePx;
    const lengthPx = Math.max(metreLengthPx, lanePx * VEHICLE_LANE_LENGTHS, MIN_VEHICLE_LENGTH_PX);
    const widthPx = lengthPx * VEHICLE_ASPECT;

    const sx = tx + k * (offsetX + (x - xMin) * s);
    const sy = ty + k * (offsetY + (yMax - y) * s);
    const pad = CULL_PAD_PX + lengthPx;
    if (sx < -pad || sy < -pad || sx > cssW + pad || sy > cssH + pad) continue;

    // Vehicles on one link share a size, so this only really re-assigns at link
    // boundaries — cheaper than setting canvas state per car.
    const lineWidth = Math.min(Math.max(lengthPx * 0.09, 0.5), 1.4);
    if (lineWidth !== appliedLineWidth) {
      ctx.lineWidth = lineWidth;
      appliedLineWidth = lineWidth;
    }

    ctx.save();
    ctx.translate(sx, sy);
    // Negated because the viewer's Y-flip reflects the plane (see the module
    // header and NetworkViewer's dirsFromAngle).
    ctx.rotate((-angle * Math.PI) / 180);
    capsulePath(ctx, lengthPx, widthPx);
    // A real Vissim export's default attribute selection carries no SPEED
    // column at all (verified live -- see trajectory_reader.py's module
    // docstring), so `speed` is genuinely nullable: unknown must render as
    // the normal (moving) color, never fall back to 0 and misrender a
    // possibly-moving vehicle as stopped.
    ctx.fillStyle = vehicle.speed != null && vehicle.speed <= 0 ? VEHICLE_FILL_STOPPED : VEHICLE_FILL;
    ctx.fill();
    // Retained guard: MIN_VEHICLE_LENGTH_PX now sits above this threshold, so it
    // passes in practice — it stays as the rule for "too small to outline" rather
    // than being silently dropped along with the old 3 px floor.
    if (lengthPx >= OUTLINE_MIN_LENGTH_PX) ctx.stroke();
    ctx.restore();
  }
}

export default function TrajectoryLayer({
  engine,
  frames,
  projection,
  network,
}: {
  engine: PlaybackEngine;
  frames: TrajectoryFrame[] | null;
  projection: ProjectionStore;
  // P5 (optional, purely additive): the same network the viewer drew. Supplies
  // the centreline polylines that make interpolation path-accurate and the
  // per-link widths that size vehicles against their lane. With it absent the
  // layer behaves exactly as before — straight x/y lerp, default lane width.
  network?: NetworkData | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Rebuilt only when the network object identity changes (the page replaces it
  // wholesale on load / dataset swap), never per frame.
  const linkGeometry = useMemo(() => buildLinkGeometryIndex(network), [network]);

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
      drawVehicles(ctx, cssW, cssH, current, frames, time, linkGeometry);
    };

    engine.registerRenderer(draw);
    engine.requestFrame();

    // Zoom/pan: NetworkViewer already throttles its own emits to one per
    // animation frame (see its emitProjection/projectionRafRef), so this
    // listener fires already inside that throttle. Drawing via requestFrame()
    // here would queue a SECOND, separate rAF on top of that first one — while
    // paused (raf idle, nothing else re-arming it) that is a full extra frame
    // of latency between the SVG's already-updated transform and the canvas
    // catching up, which is exactly the vehicles-trailing-off-the-road smear
    // reported during a fast drag-pan/wheel-zoom. renderNow() draws
    // synchronously with the projection update instead, so the canvas is
    // never more than the one frame emitProjection already costs behind the
    // road it is drawn over. (While playing this is a harmless no-op-ish
    // extra redraw — frameLoop's own per-frame tick already covers it.)
    // The engine object lives in useState, so across a dev hot-reload an OLD
    // engine instance (predating renderNow) can outlive this NEW subscriber
    // code — seen live 2026-07-27 as a canvas frozen off the roads. Falling
    // back to requestFrame keeps a stale session correct (one frame behind)
    // instead of dead; fresh sessions always take renderNow.
    const unsubscribe = projection.subscribe(() =>
      typeof engine.renderNow === "function" ? engine.renderNow() : engine.requestFrame(),
    );

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
  }, [engine, frames, projection, linkGeometry]);

  return <canvas ref={canvasRef} aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full" />;
}

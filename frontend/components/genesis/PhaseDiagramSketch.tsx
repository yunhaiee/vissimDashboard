"use client";

// Unit #55 -- the Φ strip. The user's decision (docs/phase2/DECISIONS.md:6826):
// "아예 엑셀 올리는걸로 통합하자 ... the page renders the diagram."
//
// This component draws ONE stored reading -- the parsed rows, nothing else --
// as a row of per-phase sketches. It fetches nothing, decides nothing, and
// asserts nothing about whether the reading is correct.
//
// THE THREE RULES THIS FILE EXISTS FOR:
//
//  1. **PLACEMENT IS LAYOUT, NOT GEOGRAPHY.** The v1 template has no 방위
//     column, so an approach's position in this drawing comes from its INDEX in
//     the sheet's own approach order and from nothing else. Drawing a leg at the
//     top of the box and letting a reader infer "north" would be this app
//     inventing a bearing nobody wrote down, which is the exact failure the
//     Daejeon batch was corrected for (bend-glyph origin misreads). The caption
//     below is therefore mandatory and is rendered with the strip, not tucked
//     into a tooltip.
//
//  2. **`unknown` IS NEVER PROMOTED TO AN ARROW.** An arrow states a direction.
//     A movement the sheet left blank -- or a note the parser could not resolve
//     to one of the four vehicular turns -- has no direction, and drawing one
//     would manufacture a reading nobody made. It renders as a TEXT CHIP that
//     says 미상 and carries the row's own 비고 verbatim. The rule is enforced by
//     the type system as well as by `movementGlyph`: `TURN_ROTATION_DEG` is a
//     Record over `VehicleTurn` only, so there is no heading for `unknown` (or
//     for `pedestrian`) to be drawn with.
//
//  3. **A PEDESTRIAN PHASE IS NOT A VEHICLE ARROW.** 보행 gets crossing bars --
//     a mark with no head and no direction -- for the same reason: a 보행 row
//     licenses no vehicular movement, and an arrow beside four other arrows
//     reads as one.
//
// Chrome strings take the three-argument t() form, the convention the phases
// screen states in its own header comment, so nothing here depends on an entry
// landing in the generated ja dictionary.

import type { PhaseDiagramMovement } from "@/lib/api";

type Translate = (ko: string, en: string, ja: string) => string;

export type SketchTurn = PhaseDiagramMovement["turn"];

/** The four turns that HAVE a direction. `pedestrian` and `unknown` are
 * deliberately absent -- see rule 2 above. */
type VehicleTurn = "through" | "left" | "right" | "u_turn";

/** Which mark a movement gets. THE discriminating path, and it carries the
 * narrowed turn out with it: `unknown` can only ever reach the chip arm and
 * `pedestrian` only ever the crossing arm, so the ONLY value that ever reaches
 * an arrow is one of the four `VehicleTurn`s. No cast, no default arm -- adding
 * a sixth turn to the vocabulary fails to compile here rather than silently
 * acquiring a heading. */
type Glyph = { kind: "arrow"; turn: VehicleTurn } | { kind: "crossing" } | { kind: "chip" };

function movementGlyph(turn: SketchTurn): Glyph {
  if (turn === "unknown") return { kind: "chip" };
  if (turn === "pedestrian") return { kind: "crossing" };
  return { kind: "arrow", turn };
}

/** Heading change, in degrees, applied to the inbound direction. Right-hand
 * traffic (Korea): a left turn leaves to the driver's left. */
const TURN_ROTATION_DEG: Record<VehicleTurn, number> = {
  through: 0,
  left: -90,
  right: 90,
  u_turn: 180,
};

/** Which lane the movement waits in, as a multiple of `LANE`. Left turns hug
 * the centreline, right turns the kerb -- so two movements off the same leg
 * never land on the same stroke. */
const TURN_LANE: Record<VehicleTurn, number> = {
  u_turn: 0.6,
  left: 1.1,
  through: 2.1,
  right: 3.1,
};

const VIEW = 144;
const C = VIEW / 2;
/** Where a leg meets the edge of the sketch. */
const LEG_R = 54;
/** Where the slot's number sits, just outside the leg. */
const MARKER_R = 64;
const LANE = 6;
const EXIT_LANE = 1.5;
const ROAD_WIDTH = 26;

type Pt = { x: number; y: number };

const CENTRE: Pt = { x: C, y: C };

const rad = (deg: number) => (deg * Math.PI) / 180;

function polar(angleDeg: number, r: number): Pt {
  return { x: C + r * Math.cos(rad(angleDeg)), y: C + r * Math.sin(rad(angleDeg)) };
}

/** Rotation in SVG's y-down frame: a POSITIVE angle turns clockwise on screen,
 * which is the driver's right. */
function rotate(v: Pt, deg: number): Pt {
  const c = Math.cos(rad(deg));
  const s = Math.sin(rad(deg));
  return { x: v.x * c - v.y * s, y: v.x * s + v.y * c };
}

function shift(base: Pt, direction: Pt, distance: number): Pt {
  return { x: base.x + direction.x * distance, y: base.y + direction.y * distance };
}

function cross(a: Pt, b: Pt): number {
  return a.x * b.y - a.y * b.x;
}

/** Where the inbound line and the outbound line would meet. Used as the Bézier
 * control point so a turn bends at the conflict point instead of at the middle
 * of the box. Parallel lines fall back to the midpoint. */
function knee(from: Pt, alongIn: Pt, to: Pt, alongOut: Pt): Pt {
  const denominator = cross(alongIn, alongOut);
  if (Math.abs(denominator) < 1e-6) return { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
  const delta = { x: to.x - from.x, y: to.y - from.y };
  const s = cross(delta, alongOut) / denominator;
  return shift(from, alongIn, s);
}

const round = (n: number) => Math.round(n * 100) / 100;

/** The slot an approach is DRAWN in: index 0 at the bottom, then evenly spaced
 * around the box. Four legs land bottom / right / top / left. This is a screen
 * arrangement -- the sheet states no bearing and neither does this function. */
function slotAngleDeg(index: number, count: number): number {
  return 90 - (index * 360) / Math.max(count, 1);
}

export function turnWord(turn: SketchTurn, t: Translate): string {
  if (turn === "through") return t("직진", "through", "直進");
  if (turn === "left") return t("좌회전", "left", "左折");
  if (turn === "right") return t("우회전", "right", "右折");
  if (turn === "u_turn") return t("유턴", "U-turn", "Uターン");
  if (turn === "pedestrian") return t("보행", "pedestrian", "歩行");
  return t("미상", "unknown", "不明");
}

export type SketchMovement = {
  approach_label: string;
  turn: SketchTurn;
  note: string | null;
};

export type SketchPhase = {
  phase_id: string;
  movements: readonly SketchMovement[];
  ambiguity: string | null;
};

/**
 * The slot order: the sheet's own `approaches` first, in its own order, then
 * any label a movement names that the approach list does not carry. A movement
 * is never dropped for want of a slot -- an approach nobody listed is still an
 * approach somebody wrote a row for.
 */
function slotOrder(
  approaches: readonly { label: string }[],
  phases: readonly SketchPhase[],
): string[] {
  const slots: string[] = [];
  for (const approach of approaches) {
    if (approach.label && !slots.includes(approach.label)) slots.push(approach.label);
  }
  for (const phase of phases) {
    for (const movement of phase.movements) {
      if (movement.approach_label && !slots.includes(movement.approach_label)) {
        slots.push(movement.approach_label);
      }
    }
  }
  return slots;
}

function ArrowMark({ from, d, turn, tint }: { from: Pt; d: Pt; turn: VehicleTurn; tint: string }) {
  const exitDirection = rotate(d, TURN_ROTATION_DEG[turn]);
  const entry = shift(from, rotate(d, 90), LANE * TURN_LANE[turn]);
  const exitBase = shift(CENTRE, exitDirection, LEG_R);
  const exit = shift(exitBase, rotate(exitDirection, 90), LANE * EXIT_LANE);

  // The head sits at the exit; the stroke stops just short of it so the two do
  // not double up on the same 3 units of ink.
  const tip = exit;
  const back = shift(tip, exitDirection, -9);
  const wing = rotate(exitDirection, 90);
  const headPoints = [
    `${round(tip.x)},${round(tip.y)}`,
    `${round(back.x + wing.x * 4.2)},${round(back.y + wing.y * 4.2)}`,
    `${round(back.x - wing.x * 4.2)},${round(back.y - wing.y * 4.2)}`,
  ].join(" ");

  const end = shift(tip, exitDirection, -7);
  const path =
    turn === "through"
      ? `M ${round(entry.x)} ${round(entry.y)} L ${round(end.x)} ${round(end.y)}`
      : turn === "u_turn"
        ? `M ${round(entry.x)} ${round(entry.y)} Q ${round(C + d.x * 24)} ${round(C + d.y * 24)} ${round(end.x)} ${round(end.y)}`
        : (() => {
            const control = knee(entry, d, end, { x: -exitDirection.x, y: -exitDirection.y });
            return `M ${round(entry.x)} ${round(entry.y)} Q ${round(control.x)} ${round(control.y)} ${round(end.x)} ${round(end.y)}`;
          })();

  return (
    <g className={tint}>
      <path d={path} fill="none" stroke="currentColor" strokeWidth={3.2} strokeLinecap="round" />
      <polygon points={headPoints} fill="currentColor" />
    </g>
  );
}

/** 보행: bars laid along the direction of vehicle travel, spread across the
 * leg. No head, no tip, no direction -- it cannot be misread as an arrow. */
function CrossingMark({ from, d, tint }: { from: Pt; d: Pt; tint: string }) {
  const across = rotate(d, 90);
  const centre = shift(from, d, 16);
  const bars = [-1.5, -0.5, 0.5, 1.5].map((step) => {
    const base = shift(centre, across, step * 5.4);
    const a = shift(base, d, -7);
    const b = shift(base, d, 7);
    return { key: `bar-${step}`, a, b };
  });
  return (
    <g className={tint}>
      {bars.map((bar) => (
        <line
          key={bar.key}
          x1={round(bar.a.x)}
          y1={round(bar.a.y)}
          x2={round(bar.b.x)}
          y2={round(bar.b.y)}
          stroke="currentColor"
          strokeWidth={2.6}
          strokeLinecap="butt"
        />
      ))}
    </g>
  );
}

function PhaseCell({
  phase,
  slots,
  t,
}: {
  phase: SketchPhase;
  slots: string[];
  t: Translate;
}) {
  const count = Math.max(slots.length, 1);
  const legs = slots.map((label, index) => {
    const angle = slotAngleDeg(index, count);
    const mouth = polar(angle, LEG_R);
    const inbound = { x: -Math.cos(rad(angle)), y: -Math.sin(rad(angle)) };
    return { label, index, angle, mouth, inbound, marker: polar(angle, MARKER_R) };
  });
  const legByLabel = new Map(legs.map((leg) => [leg.label, leg]));

  const marks = phase.movements.map((movement) => ({ movement, glyph: movementGlyph(movement.turn) }));
  const drawn = marks.filter((mark) => mark.glyph.kind !== "chip");
  const chips = marks.filter((mark) => mark.glyph.kind === "chip").map((mark) => mark.movement);

  return (
    <li className="w-[188px] shrink-0">
      <p className="text-[15px] font-semibold text-slate-900">{phase.phase_id}</p>
      <svg
        viewBox={`0 0 ${VIEW} ${VIEW}`}
        className="mt-1 h-[172px] w-[172px]"
        role="img"
        aria-label={`${phase.phase_id} ${t("현시 도식", "phase sketch", "現示の図")}`}
      >
        <title>{`${phase.phase_id} — ${t("현시 도식", "phase sketch", "現示の図")}`}</title>
        <g className="text-slate-200">
          {legs.map((leg) => (
            <line
              key={`road-${leg.index}`}
              x1={C}
              y1={C}
              x2={round(leg.mouth.x)}
              y2={round(leg.mouth.y)}
              stroke="currentColor"
              strokeWidth={ROAD_WIDTH}
              strokeLinecap="butt"
            />
          ))}
        </g>
        {drawn.map(({ movement, glyph }, index) => {
          const leg = legByLabel.get(movement.approach_label);
          if (!leg) return null;
          return (
            <g key={`m-${index}`}>
              <title>{`${movement.approach_label} — ${turnWord(movement.turn, t)}`}</title>
              {glyph.kind === "crossing" ? (
                <CrossingMark from={leg.mouth} d={leg.inbound} tint="text-slate-500" />
              ) : glyph.kind === "arrow" ? (
                <ArrowMark from={leg.mouth} d={leg.inbound} turn={glyph.turn} tint="text-slate-800" />
              ) : null}
            </g>
          );
        })}
        <g className="text-slate-600">
          {legs.map((leg) => (
            <text
              key={`n-${leg.index}`}
              x={round(leg.marker.x)}
              y={round(leg.marker.y)}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={11}
              fontWeight={600}
              fill="currentColor"
            >
              {leg.index + 1}
            </text>
          ))}
        </g>
      </svg>
      {drawn.length === 0 && chips.length === 0 && (
        <p className="mt-1 text-[14px] leading-relaxed text-slate-500">
          {t("이 현시에 기록된 이동류가 없습니다.", "No movement recorded in this phase.", "この現示に記録された移動流がありません。")}
        </p>
      )}
      {chips.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {chips.map((movement, index) => (
            <li
              key={`chip-${index}`}
              className="rounded-md border border-slate-400 bg-slate-100 px-2 py-1 text-[14px] leading-relaxed text-slate-800"
            >
              <span className="mr-1 font-semibold">{turnWord(movement.turn, t)}</span>
              {movement.approach_label}
              {movement.note ? ` — ${movement.note}` : ""}
            </li>
          ))}
        </ul>
      )}
      {phase.ambiguity && (
        <p className="mt-1.5 rounded border border-slate-400 bg-slate-100 px-2 py-1 text-[14px] leading-relaxed text-slate-800">
          {t("모델이 확정하지 못한 부분", "What the model could not resolve", "モデルが確定できなかった点")}:{" "}
          {phase.ambiguity}
        </p>
      )}
    </li>
  );
}

/**
 * The whole strip: a caption that refuses to imply a bearing, the slot legend
 * that binds each drawn leg to its VERBATIM sheet label, one cell per phase in
 * the parsed order, and the intersection-level notes underneath.
 */
export function PhaseDiagramSketch({
  phases,
  approaches,
  notes,
  t,
}: {
  phases: readonly SketchPhase[];
  approaches: readonly { label: string }[];
  notes: readonly string[];
  t: Translate;
}) {
  const slots = slotOrder(approaches, phases);

  return (
    <div>
      {/* MANDATORY, and it is not decoration: the template (v1) has no 방위
        * column, so every leg's position on this drawing came from a list
        * order. Saying so is the difference between a diagram and a claim. */}
      <p className="text-[14px] leading-relaxed text-slate-600">
        {t(
          "배치는 화면 배열이며 실제 방위가 아닙니다 — 템플릿에 방위 열이 없어, 접근로는 시트에 적힌 순서대로 놓입니다.",
          "The placement is a screen layout, not a real bearing — the template has no direction column, so legs are laid out in the order the sheet lists them.",
          "配置は画面上の並びであり実際の方位ではありません — テンプレートに方位の列がないため、流入路はシートに書かれた順に配置されます。",
        )}
      </p>

      {slots.length === 0 ? (
        <p className="mt-2 text-[15px] leading-relaxed text-slate-500">
          {t(
            "접근로가 기록되어 있지 않습니다.",
            "No approach leg is recorded.",
            "流入路が記録されていません。",
          )}
        </p>
      ) : (
        <ol className="mt-2 flex flex-wrap gap-1.5">
          {slots.map((label, index) => (
            <li
              key={label}
              className="rounded border border-slate-300 bg-slate-50 px-2 py-0.5 text-[14px] text-slate-700"
            >
              <span className="mr-1 font-semibold text-slate-900">{index + 1}</span>
              {label}
            </li>
          ))}
        </ol>
      )}

      {phases.length === 0 ? (
        <p className="mt-3 text-[15px] leading-relaxed text-slate-500">
          {t(
            "현시가 기록되어 있지 않습니다 — 그릴 도식이 없습니다.",
            "No phase is recorded — there is nothing to draw.",
            "現示が記録されていません — 描く図がありません。",
          )}
        </p>
      ) : (
        <ol className="mt-3 flex gap-4 overflow-x-auto pb-1">
          {phases.map((phase) => (
            <PhaseCell key={phase.phase_id} phase={phase} slots={slots} t={t} />
          ))}
        </ol>
      )}

      {notes.length > 0 && (
        <div className="mt-3 rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
          <p className="text-[14px] font-semibold text-slate-700">
            {t("교차로 메모", "Intersection notes", "交差点メモ")}
          </p>
          <ul className="mt-1 list-disc pl-5 text-[14px] leading-relaxed text-slate-700">
            {notes.map((note, index) => (
              <li key={`note-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

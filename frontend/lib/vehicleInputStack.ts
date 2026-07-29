// Same-point vehicle-input stacks (sibling-disambiguation unit, 2026-07-28).
//
// The defect this exists for, in the user's own words: "i prompted 107, but it
// is trying to fix input for 307, not 107 (i dont even know if 107 exists.)"
// The write WAS correct. The UI made it unverifiable, because the real client
// model (Murdoch Drive) puts THREE vehicle inputs -- one per vehicle class --
// on every demand link, all sharing the same name, and
// `inpx_parser._parse_vehicle_inputs` resolves every one of them to its link's
// START point (`point_at_pos(points, 0)`). Three separately-editable objects,
// one pixel, one name. Whichever glyph happened to be painted last owned the
// tooltip.
//
// Two things follow, and both are shared here rather than reimplemented per
// surface (the network diagram, the approval card's thumbnail, and the
// /network focus banner all need the SAME answer to "who else is under this
// pixel, and in what order?" -- three copies would drift and the fan-out on
// the map would stop matching the fan-out on the thumbnail):
//
//   * the stack grouping itself, and
//   * the composition label, which is the ONLY datum that tells the siblings
//     apart (`name` cannot -- all three of link 620's inputs are literally
//     "Jennalup ST_WB").
//
// Ordering is by NUMERIC id ascending, deliberately, not by payload order: it
// is the one ordering every surface can compute independently and still agree
// on, so the leftmost glyph on the diagram is the leftmost dot on the
// thumbnail. (On the real model it also happens to match document order --
// 107, 207, 307 -- so the fan-out reads as "one per class, in class order".)

import type { VehicleInputElement } from "./api";

/** Coordinate rounding used to decide "same point", matching
 * `backend/services/agent/approval_focus.py`'s `_SAME_POINT_DECIMALS`. Real
 * siblings are bit-identical (they all resolve to the same link start), so
 * this tolerance exists only so a float round-trip can never split a genuine
 * stack -- never to merge two inputs a user could actually tell apart. */
export const SAME_POINT_DECIMALS = 3;

export type VehicleInputStackSlot = {
  /** 0-based position of this input within its stack, in the shared order. */
  index: number;
  /** How many vehicle inputs sit on this exact point (1 = not stacked). */
  size: number;
  /** Every member of the stack, including this input, in the shared order. */
  members: VehicleInputElement[];
};

function pointKey(x: unknown, y: unknown): string | null {
  if (typeof x !== "number" || typeof y !== "number") return null;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const factor = 10 ** SAME_POINT_DECIMALS;
  return `${Math.round(x * factor)},${Math.round(y * factor)}`;
}

/** Numeric-first id comparison ("107" < "1000"), falling back to a plain
 * string compare for non-numeric ids so the order is always total. */
function compareIds(a: VehicleInputElement, b: VehicleInputElement): number {
  const na = Number(a.id);
  const nb = Number(b.id);
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return String(a.id).localeCompare(String(b.id));
}

/** `id -> where that input sits in its same-point stack`. Inputs with no
 * usable coordinate are returned as lone stacks (size 1) rather than dropped:
 * a caller asking about them must still get an answer, and "1 of 1" is the
 * truthful one. */
export function buildVehicleInputStacks(
  inputs: readonly VehicleInputElement[] | undefined | null,
): Map<string, VehicleInputStackSlot> {
  const byPoint = new Map<string, VehicleInputElement[]>();
  const lone: VehicleInputElement[] = [];
  for (const item of inputs ?? []) {
    const key = pointKey(item.x, item.y);
    if (key === null) {
      lone.push(item);
      continue;
    }
    const bucket = byPoint.get(key);
    if (bucket) bucket.push(item);
    else byPoint.set(key, [item]);
  }
  const slots = new Map<string, VehicleInputStackSlot>();
  for (const members of byPoint.values()) {
    members.sort(compareIds);
    members.forEach((member, index) => {
      slots.set(String(member.id), { index, size: members.length, members });
    });
  }
  for (const item of lone) {
    slots.set(String(item.id), { index: 0, size: 1, members: [item] });
  }
  return slots;
}

/** The human label for which vehicle class an input feeds, or null when the
 * model does not say.
 *
 * `vehComp` lives on each `<timeIntervalVehVolume>` child rather than on
 * `<vehicleInput>` itself, so one input may legally feed a different class per
 * interval; the backend represents that case rather than collapsing it
 * (`composition_name` is `""` and `composition_varies_by_interval` is true --
 * see `inpx_parser.vehicle_input_composition_fields`). Mirrored here: a
 * varying input is labelled as varying, never given a confident wrong class.
 * Networks with no compositions at all (the synthetic demo file) return null
 * and every caller simply omits the field. */
export function vehicleInputCompositionLabel(
  item: Pick<VehicleInputElement, "composition_name" | "composition_varies_by_interval"> | null | undefined,
  t: (ko: string, en: string, ja?: string) => string,
): string | null {
  if (!item) return null;
  if (item.composition_name) return item.composition_name;
  if (item.composition_varies_by_interval) {
    return t("차종 조성 구간별 상이", "class mix varies by interval", "車種構成が時間帯別に異なる");
  }
  return null;
}

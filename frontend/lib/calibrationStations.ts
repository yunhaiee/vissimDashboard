// ---------------------------------------------------------------------------
// WHERE A THING IS SHOWN on the calibration workspace (unit #50-b).
//
// THE PROBLEM IT EXISTS FOR, in the user's own words after driving #50's real
// screen: "스테이션이 전부 세로로 쌓여 페이지 구조·작업 구조를 파악할 수 없다".
// The workspace stacked every station down one column, so the thirteen-step
// rail scrolled a reader past four other stations to reach one. #50-b turns the
// rail into a permanent left NAVIGATION and shows exactly one station at a
// time — which needs one honest answer to two placement questions:
//
//   1. which station does step N's evidence live in, and
//   2. which station does a pending proposal's card belong to.
//
// WHAT THIS MODULE IS NOT. It never decides a STATUS. Every status word on that
// screen still comes from `GET /api/calibration/rail`
// (services/calibration_rail.py), and every number from the report. This is a
// ROUTING table for display: it decides which panel a card is drawn in, and
// nothing else. That is why it may be read by tests directly — it is
// alias-free and dependency-free on purpose, exactly like lib/findingMessage.ts
// (the contract suite's `node --test --experimental-strip-types` runner cannot
// resolve the `@/` alias, so a helper that imports one can only ever be
// regex-pinned, never RUN — and "no proposal is orphaned" is a claim that has
// to be executed to mean anything).
//
// THE TWO RULES THAT KEEP IT HONEST:
//
//   * A step whose station this module does not name comes back `null`, NOT
//     "manual". The screen then shows it in its own "단계 미배치" row rather
//     than filing it under 수동 확인 영역, which would be this product claiming
//     it measures nothing for a step that may well have evidence. That case is
//     not hypothetical: step 9 (public transport) left the manual list in #50's
//     own follow-up, and a table keyed "everything else is manual" would have
//     silently mislabelled it.
//   * A proposal whose tool this module cannot place is NEVER dropped. It goes
//     to the default station with its tool NAMED and `step: null`, so the
//     badge arithmetic still adds up and a reader can see that this product
//     does not know which guideline step the edit belongs to. Hiding it would
//     be the one unforgivable failure for a screen whose whole job is "nothing
//     waiting for a human is invisible".
// ---------------------------------------------------------------------------

/** The panels the workspace can show, in the order the rail lists them.
 *
 * `scoring` is deliberately FIRST and is not an MRWA step: it is where the run
 * is scored and diagnosed, which is where the work starts and returns. */
export const CALIBRATION_STATIONS = [
  "scoring",
  "network",
  "signals",
  "transit",
  "demand",
  "assignment",
  "behaviour",
  "manual",
] as const;

export type CalibrationStationId = (typeof CALIBRATION_STATIONS)[number];

/** The station a reader starts on, and the fallback for a proposal this module
 * cannot place. Always reachable, so nothing filed here can be missed. */
export const DEFAULT_STATION: CalibrationStationId = "scoring";

/**
 * Which station renders §2.12.1 step N's evidence.
 *
 * FINER THAN THE SERVER'S `step.anchor`, on purpose, and never in conflict with
 * it. The rail payload carries six anchors because #50's page had six sections;
 * this screen splits `station-scoring` into 채점 / ⑩ 수요 / ⑪ 배분 and
 * `station-behaviour` into ⑦ (capacity, beside its signal step) / ⑫ (behaviour
 * parameters), because the user's complaint was precisely that those steps had
 * no place of their own to stand in. Both mappings send a step to a panel that
 * renders that step's evidence; this one just has more panels.
 *
 * Steps 2, 3, 4, 5, 8 and 13 are absent BY OMISSION rather than mapped to
 * "manual": their manual-check status is the server's `manual_area` flag to
 * assert, not this table's (see `stationForStep`).
 */
export const STATION_BY_STEP: Readonly<Record<number, CalibrationStationId>> = {
  1: "network",
  6: "signals",
  7: "signals",
  9: "transit",
  10: "demand",
  11: "assignment",
  12: "behaviour",
};

/** The step shape this module needs — the rail payload's, structurally. */
export type StepLike = {
  number: number;
  manual_area?: boolean;
};

/**
 * The station step N belongs to, or `null` when this build has no place for it.
 *
 * `null` is a real answer and the caller must render it (the workspace gives it
 * a "단계 미배치" row). Returning "manual" instead would turn "we have not
 * placed this step" into "this product measures nothing for this step" — two
 * different claims, one of which is the screen's central honesty promise.
 */
export function stationForStep(step: StepLike): CalibrationStationId | null {
  const named = STATION_BY_STEP[step.number];
  if (named) return named;
  if (step.manual_area) return "manual";
  return null;
}

/**
 * What the rail row and the station header call each station.
 *
 * `[ko, en, ja]` in the same shape `t()` takes, and the table lives HERE rather
 * than in either component because the rail row and the station header must be
 * the same words — a reader clicks "수요 보정" and has to land on something
 * called "수요 보정". The step LABELS beside them stay the server's
 * (`step.label_ko` / `label` / `label_ja`, marked `label_source` on the wire);
 * these name the panels this screen groups them into, which is this product's
 * own arrangement and says so.
 */
export const STATION_LABELS: Readonly<Record<CalibrationStationId, [string, string, string]>> = {
  scoring: ["채점·진단", "Scoring & diagnosis", "採点・診断"],
  network: ["네트워크 코딩", "Network coding", "ネットワークコーディング"],
  signals: ["신호·용량", "Signals & capacity", "信号・容量"],
  transit: ["대중교통", "Public transport", "公共交通"],
  demand: ["수요 보정", "Demand calibration", "需要補正"],
  assignment: ["경로 선택(배분)", "Route choice", "経路選択(配分)"],
  behaviour: ["행태 파라미터", "Behaviour parameters", "挙動パラメータ"],
  manual: ["수동 확인 영역", "Manual-check areas", "手動確認領域"],
};

export type GroupedSteps<T> = {
  /** Every station, always present — an empty list is an answer. */
  byStation: Record<CalibrationStationId, T[]>;
  /** Steps this build has no station for. Rendered in their own row, never
   * folded into 수동 확인 영역 — see `stationForStep`. */
  unplaced: T[];
};

/**
 * The stations the rail draws a row for.
 *
 * A row appears when it carries steps, when it is the DEFAULT station (which
 * has no step of its own and must always be reachable), or when PROPOSALS were
 * placed at it.
 *
 * THAT LAST CLAUSE IS THE POINT (added by #50-b's refuter, 2026-08-20). A card
 * is placed by `groupProposals` from the static step→station table, which
 * answers even when the rail carries no steps at all — the rail request failed,
 * or the pending listing simply arrived first. The rail, meanwhile, used to hide
 * any row whose step list was empty. The two rules together made a proposal
 * UNREACHABLE: its badge was not drawn, its station could not be clicked (the
 * open station is only ever set from a rail row), and the station panel it sat
 * in could never be opened — while the panel the reader WAS looking at said
 * "N are waiting at other steps — see the blue badges on the left rail" about
 * badges that did not exist. Measured before the fix: a failed rail orphaned 4
 * of 5 pending proposals, and a rail payload missing two steps orphaned 2 of 5.
 *
 * So the rule lives here, beside the placement it has to agree with, and is
 * executed by the contract suite rather than asserted in a comment.
 */
export function stationRowsShown<T>(
  grouped: GroupedSteps<T>,
  proposalCounts: Partial<Record<CalibrationStationId, number>>,
): CalibrationStationId[] {
  return CALIBRATION_STATIONS.filter(
    (station) =>
      station === DEFAULT_STATION ||
      (grouped.byStation[station]?.length ?? 0) > 0 ||
      (proposalCounts[station] ?? 0) > 0,
  );
}

/** Split the rail's steps across the stations, losing none. */
export function groupSteps<T extends StepLike>(steps: readonly T[]): GroupedSteps<T> {
  const byStation = Object.fromEntries(
    CALIBRATION_STATIONS.map((station) => [station, [] as T[]]),
  ) as Record<CalibrationStationId, T[]>;
  const unplaced: T[] = [];

  for (const step of steps) {
    const station = stationForStep(step);
    if (station) byStation[station].push(step);
    else unplaced.push(step);
  }

  return { byStation, unplaced };
}

/**
 * Which §2.12.1 step an edit tool's SUBJECT belongs to.
 *
 * Every entry is an object→subject match, never an interpretation: a signal
 * cycle is step ⑥'s subject, a vehicle input is step ⑩'s. Tools whose step is
 * a judgement call are deliberately ABSENT (they land in the unmapped bucket
 * and say so) rather than assigned a step this product would then be citing
 * MRWA for:
 *
 *   * `set_sim_duration`, `set_random_seed` — run controls. Reading them into
 *     ⑬ "software-specific items" is plausible and unproven; this module does
 *     not cite a guideline step it cannot show.
 *   * `add_queue_counter`, `add_data_collection_point`,
 *     `add_data_collection_measurement`, `add_travel_time_section` —
 *     measurement instrumentation. They produce evidence FOR calibration; they
 *     are not one of its steps.
 *   * `set_time_distribution_*`, `set_vehicle_type_reference` — dwell-time
 *     distributions and vehicle accel/decel/weight curves. Neither is ②'s
 *     "vehicle speeds", though both are near it.
 *   * `rollback_last_edit` — undoes an edit belonging to whatever step the edit
 *     did; it has no step of its own.
 *
 * `apply_edit_plan` is absent for a different reason: it is a BATCH, and its
 * real tools are read out of its params (see `actionTools`).
 */
export const MRWA_STEP_BY_TOOL: Readonly<Record<string, number>> = {
  // ① 기본 네트워크 코딩 — geometry and topology
  add_link: 1,
  remove_link: 1,
  add_connector: 1,
  remove_connector: 1,
  set_link_geometry: 1,
  set_link_lane_count: 1,
  set_link_point_radius: 1,
  set_link_closure: 1,
  set_lane_closure: 1,
  set_turn_ban: 1,
  add_parking_lot: 1,
  remove_parking_lot: 1,
  set_parking_lot_capacity: 1,
  // ② 차량 속도
  set_des_speed_decision: 2,
  set_des_speed_distribution_points: 2,
  set_reduced_speed_area: 2,
  // ③ 비신호 교차로 — priority, stop control, conflict areas
  add_stop_sign: 3,
  remove_stop_sign: 3,
  add_priority_rule: 3,
  remove_priority_rule: 3,
  set_priority_rule_gap_headway: 3,
  set_conflict_area_status: 3,
  // ⑥ 신호 교차로
  set_signal_cycle: 6,
  set_signal_offset: 6,
  set_signal_green_split: 6,
  add_signal_controller: 6,
  add_signal_head: 6,
  remove_signal_head: 6,
  add_detector: 6,
  remove_detector: 6,
  // ⑨ 대중교통
  add_pt_stop: 9,
  remove_pt_stop: 9,
  add_pt_line: 9,
  remove_pt_line: 9,
  set_pt_line_stop_dwell: 9,
  // ⑩ 수요 보정
  set_vehicle_input_volume: 10,
  add_vehicle_input: 10,
  set_vehicle_composition_share: 10,
  add_pedestrian_input: 10,
  remove_pedestrian_input: 10,
  set_pedestrian_input_volume: 10,
  // ⑪ 경로 선택(배분)
  set_route_relative_flow: 11,
  // ⑫ 행태 파라미터 (and ⑦'s Table 5-6 set, which is written with these)
  set_driving_behavior_parameter: 12,
  duplicate_driving_behavior: 12,
  set_link_behavior_type_driving_behavior: 12,
};

/** The tool a batch plan hides its real work behind. */
const BATCH_TOOL = "apply_edit_plan";

export type ActionLike = {
  tool: string;
  params?: Record<string, unknown> | null;
};

/**
 * Every tool an action actually runs, batch steps included.
 *
 * `apply_edit_plan` carries `{steps: [{tool, params}, ...]}` (tools_edit.py's
 * own shape), so placing it by its own name would file the most common
 * multi-edit proposal there is under "no step" forever.
 */
export function actionTools(action: ActionLike): string[] {
  if (action.tool !== BATCH_TOOL) return [action.tool];
  const steps = (action.params ?? {})["steps"];
  if (!Array.isArray(steps)) return [action.tool];
  const nested = steps
    .map((step) => (step && typeof step === "object" ? (step as { tool?: unknown }).tool : null))
    .filter((tool): tool is string => typeof tool === "string" && tool.length > 0);
  return nested.length > 0 ? nested : [action.tool];
}

export type ProposalLike = {
  actions?: ActionLike[] | null;
};

/** Where one pending proposal is shown, and what decided that. */
export type PlacedProposal<T> = {
  proposal: T;
  station: CalibrationStationId;
  /** The §2.12.1 step that placed it — `null` when no tool could be placed. */
  step: number | null;
  /** The tool that placed it, for a caption a reader can check. */
  tool: string | null;
  /** Other steps this same card would also touch, if any. A card is shown
   * ONCE (badge arithmetic), so the station it lands on says what else it
   * moves rather than the screen drawing it twice. */
  alsoSteps: number[];
  /** Tools this module has no §2.12.1 step for. Named, never swallowed. */
  unmappedTools: string[];
  /** True when the card is at the DEFAULT station because its own station
   * could not be resolved — either the tools map to no step, or the step's
   * station is unknown (the rail has not loaded). The screen must caption a
   * fallback placement: a card sitting silently under 채점·진단 would read as
   * a scoring proposal, which is a claim about what it edits. */
  fallback: boolean;
};

/**
 * Which station shows step N's proposals.
 *
 * Built from the RAIL's own steps, so a manual-check step's proposal lands in
 * 수동 확인 영역 because the server flagged that step manual — not because this
 * module assumed "everything I have not named is manual", which is the
 * assumption that would have mislabelled step ⑨. Falls back to the static
 * table for a step the rail did not carry, and to `null` (→ a captioned
 * fallback placement) when even that has no answer.
 */
export type StationResolver = (step: number) => CalibrationStationId | null;

export function stationResolverFor(steps: readonly StepLike[]): StationResolver {
  const table = new Map<number, CalibrationStationId>();
  for (const step of steps) {
    const station = stationForStep(step);
    if (station) table.set(step.number, station);
  }
  return (number) => table.get(number) ?? STATION_BY_STEP[number] ?? null;
}

/** The resolver used when no rail has been read yet: the static table only. */
export const STATIC_STATION_RESOLVER: StationResolver = (step) => STATION_BY_STEP[step] ?? null;

/**
 * Place one proposal.
 *
 * The FIRST placeable tool decides, because a card is one decision a human
 * makes once: showing it at two stations would double the badge count and
 * invite two people to approve the same token. Everything the first tool did
 * not account for is reported alongside (`alsoSteps`, `unmappedTools`,
 * `fallback`) so the caption can say so.
 */
export function placeProposal<T extends ProposalLike>(
  proposal: T,
  resolveStation: StationResolver = STATIC_STATION_RESOLVER,
): PlacedProposal<T> {
  const tools = (proposal.actions ?? []).flatMap((action) => actionTools(action));
  const placed: { tool: string; step: number }[] = [];
  const unmappedTools: string[] = [];
  for (const tool of tools) {
    const step = MRWA_STEP_BY_TOOL[tool];
    if (typeof step === "number") placed.push({ tool, step });
    else if (!unmappedTools.includes(tool)) unmappedTools.push(tool);
  }
  const first = placed[0] ?? null;
  const alsoSteps = placed
    .slice(1)
    .map((entry) => entry.step)
    .filter((step, index, all) => step !== first?.step && all.indexOf(step) === index);
  const station = first ? resolveStation(first.step) : null;
  return {
    proposal,
    station: station ?? DEFAULT_STATION,
    step: first?.step ?? null,
    tool: first?.tool ?? null,
    alsoSteps,
    unmappedTools,
    fallback: station === null,
  };
}

export type GroupedProposals<T> = {
  /** Every station, always present — an empty list is an answer. */
  byStation: Record<CalibrationStationId, PlacedProposal<T>[]>;
  /** What the rail's sky badges print. Sums to `total`, by construction. */
  counts: Record<CalibrationStationId, number>;
  total: number;
};

/**
 * Split the pending proposals across the stations, losing none.
 *
 * The invariant the contract suite executes: every input appears in exactly one
 * station's list, so `sum(counts) === total === proposals.length`. A screen
 * whose badges do not add up to what the server is waiting on is a screen that
 * can hide an approval, which is the failure this workspace exists against.
 */
export function groupProposals<T extends ProposalLike>(
  proposals: readonly T[],
  resolveStation: StationResolver = STATIC_STATION_RESOLVER,
): GroupedProposals<T> {
  const byStation = Object.fromEntries(
    CALIBRATION_STATIONS.map((station) => [station, [] as PlacedProposal<T>[]]),
  ) as Record<CalibrationStationId, PlacedProposal<T>[]>;

  for (const proposal of proposals) {
    const placement = placeProposal(proposal, resolveStation);
    byStation[placement.station].push(placement);
  }

  const counts = Object.fromEntries(
    CALIBRATION_STATIONS.map((station) => [station, byStation[station].length]),
  ) as Record<CalibrationStationId, number>;

  return { byStation, counts, total: proposals.length };
}

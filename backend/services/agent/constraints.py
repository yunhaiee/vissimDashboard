"""Constraint catalog: the validation layer that is the product thesis.

Vissim almost never refuses a bad edit — it runs and reports confident wrong
numbers. This module manufactures the error signals Vissim never emits. Every
:class:`Finding` carries its constraint id so agent traces are
machine-scoreable (see docs/phase2/01-ARCHITECTURE.md §0 and
docs/phase2/02-ROADMAP.md M0.3).

Checks consume an *agent shadow model* dict (``{"links", "vehicle_inputs",
"signals", "nodes"}`` — see docs/phase2/02-ROADMAP.md M0.3 for the exact
shape) plus the attempted ``action`` (``{"tool": str, "params": dict}``) and
an ``extra`` dict carrying whatever postwrite/postrun evidence a check needs
(e.g. ``extra["written_value"]``/``extra["readback_value"]``,
``extra["run_result"]``).

An interface gap between the roadmap's aspirational text and what
``signal_parser.py`` exposed at M0.3 time is resolved here per the roadmap's
gap protocol; see ``docs/phase2/DECISIONS.md`` for the entry (constraint #5
below). Constraint #4's twin gap (``controller_type``) was CLOSED in v2 unit
① (2026-07-24): ``signal_parser.py`` now extracts the real
``<signalController type="...">`` attribute (verified against all 25 real
PTV Vissim 2026 example networks), so #4 reads a genuinely populated field,
not a forward-declared one. Constraint #18's twin gap (``conflict_areas``)
was CLOSED in v2 unit ④ (2026-07-24): ``tools_network.build_shadow_model``
now parses real conflict-area data straight from the .inpx (verified live
against Vissim 2026), so ``conflict_area_data_unavailable`` fires for real
too. Constraint #13's twin gap (``interval_count``/per-interval ``volumes``)
was CLOSED in v2 unit ⑤ (2026-07-24): ``tools_network._parse_vehicle_inputs``
now populates both for real, and ``set_vehicle_input_volume`` gained an
optional ``interval_no`` param — see #13's own docstring and the new #44
(``vehicle_input_interval_out_of_range``) it introduced. Constraint #31's
twin gap (``routes``) was CLOSED in v2 unit ⑥ (2026-07-24):
``tools_network._flatten_routes`` now populates ``shadow_model["routes"]``
for real, straight from ``<vehicleRoutingDecisionStatic>``/
``<vehicleRouteStatic>`` — see #31's own comment and the new
``set_route_relative_flow`` write tool + its dedicated
``route_not_found_in_decision``/``route_relative_flow_interval_out_of_range``/
``route_relative_flow_split_confirm`` checks. Constraint #14's SAME
``routes`` field also went from absent to populated in v2 unit ⑥, but #14
itself stays a documented gap (its ``manually_scaled_this_turn`` datum is
turn-scoped orchestrator state, out of this unit's scope) — see #14's own
comment.

This module is intentionally stdlib-only, like trace.py, and does not import
state.py or trace.py — it only consumes the shadow-model dict shape they
produce/agree on.

M3.1 CATALOG-GAP convention (docs/phase2/02-ROADMAP.md M3.1): a check that
depends on a datum the shadow model / ``extra["run_result"]`` cannot supply
today reads that datum defensively (``dict.get(...)``) from a documented,
not-yet-populated field name and silently returns ``None`` when it is
absent — it is fully implemented and tested (both branches, via an injected
fixture), it just never fires in production until the relevant parser/job
runner grows that field. This is the same pattern constraint #5
(``min_green_intergreen_sum``) established, and #4 (``controller_type``)
followed before its own gap closed. Every such check's docstring is tagged
``CATALOG-GAP`` with the field name it is waiting on.

===============================================================================
Catalog coverage table (docs/reference/vissim-constraints.html, compiled
2026-07-21) — every entry mapped to the constraint id(s) that encode it and
the test that proves it fires. Two catalog entries (§7 "Vissim cannot run as
a Windows service" and "The naive loop is the slow one") are deliberately
NOT encoded here — see "Excluded from this module" below.
===============================================================================

§1 Silent failure
------------------
- Conflict areas default to inactive -> ``conflict_area_data_unavailable``
  (test_conflict_area_data_unavailable_fires_when_passive_present)
- Conflicting greens are allowed, and score well ->
  ``conflicting_greens_in_signal_plan``
  (test_conflicting_greens_in_signal_plan_fires_on_overlapping_cross_axis_phases)
- Vehicles that can't change lanes are deleted -> ``vehicles_deleted_in_run``
  [M0.3] (test_vehicles_deleted_in_run_fires_when_positive)
- Demand that never enters the network -> ``latent_demand_overflow`` [M0.3,
  preflight rough-capacity warning]
  (test_latent_demand_overflow_fires_when_volume_exceeds_capacity) +
  ``latent_demand_residue_after_run`` [new, postrun]
  (test_latent_demand_residue_after_run_fires_when_positive)
- A gridlocked movement can report the best possible grade ->
  ``gridlock_reports_los_a`` (test_gridlock_reports_los_a_fires_for_zero_throughput_los_a)
- Broken routes stay broken even after you fix the road ->
  ``route_integrity_unverifiable``
  (test_route_integrity_unverifiable_fires_when_route_disconnected)
- Deleting a route silently bans a turn -> ``route_integrity_unverifiable``
  [merged with the entry above — same root cause: no "routes" in the
  shadow-model contract]
- Stale route files reused without complaint -> ``stale_route_files_after_edit``
  (test_stale_route_files_after_edit_fires_when_enabled_and_stale)
- Writing a parameter the active model ignores ->
  ``driving_behavior_parameter_model_mismatch``
  (test_driving_behavior_parameter_model_mismatch_fires_on_mismatch)

§2 Not editable how you'd expect
---------------------------------
- There is no lane-count property -> ``lane_count_edit_unsupported``
  (test_lane_count_edit_unsupported_fires_on_lane_count_param)
- There is no speed limit -> ``speed_limit_edit_ambiguous``
  (test_speed_limit_edit_ambiguous_fires_on_speed_limit_param)
- A speed decision is permanent unless you undo it -> ``speed_decision_unpaired``
  (test_speed_decision_unpaired_fires_without_restore_id)
- The main calibration parameter isn't a number -> ``cc1_scalar_write_rejected``
  (test_cc1_scalar_write_rejected_fires_on_bare_number)
- Signal timings may be outside the API entirely ->
  ``signal_controller_type_unsupported`` [M0.3]
  (test_signal_controller_type_unsupported_fires_when_type_missing)

§3 Fragile references
-----------------------
- Conflict area numbers are not stable identity ->
  ``conflict_area_data_unavailable`` [merged with §1's conflict-area entry]
- Positions are measured from the link's start ->
  ``link_geometry_edit_reference_integrity``
  (test_link_geometry_edit_reference_integrity_fires_when_link_flagged)
- Splitting a link renumbers it and inserts a gap ->
  ``link_geometry_edit_reference_integrity`` [merged with the entry above —
  both are consequences of a link-geometry edit]
- Port numbers bind detectors to external controllers ->
  ``object_reference_integrity_on_delete``
  (test_object_reference_integrity_on_delete_always_refuses)
- Deletes cascade, and the documentation is vague about how far ->
  ``object_reference_integrity_on_delete`` [merged with the entry above —
  both are "no reference graph to prove safety" refusals]

§4 Domain semantics
---------------------
- Volume is always per hour, whatever the interval ->
  ``vehicle_input_multi_interval_write_gap``
  (test_vehicle_input_multi_interval_write_gap_fires_when_datum_present)
- "More trucks" redistributes rather than adds ->
  ``vehicle_composition_share_redistribution_gap``
  (test_vehicle_composition_share_redistribution_gap_always_warns_for_that_tool)
- Scaling demand automatically scales routes — don't do it twice ->
  ``demand_scale_route_double_count_gap``
  (test_demand_scale_route_double_count_gap_fires_when_routes_flagged)
- Cycle time is a corridor property, not an intersection one ->
  ``signal_cycle_change_may_break_corridor_sync``
  (test_signal_cycle_change_may_break_corridor_sync_fires_when_sibling_shares_old_cycle)
- Splits must sum to the cycle, and include the clearance ->
  ``cycle_below_intergreen_sum`` [M0.3, floor check]
  (test_cycle_below_intergreen_sum_fires_when_present_and_violated) +
  ``ring_barrier_splits_sum_mismatch`` [new, exact-sum check]
  (test_ring_barrier_splits_sum_mismatch_fires_when_sums_differ)
- Restrictions apply per vehicle class, not per type ->
  ``restriction_missing_vehicle_class_scope``
  (test_restriction_missing_vehicle_class_scope_fires_without_classes)
- Units and percentages come back unmarked -> ``units_ambiguous_on_write``
  (test_units_ambiguous_on_write_fires_when_settings_incomplete)

§5 Results
-----------
- Evaluations are not retroactive -> ``evaluation_not_configured_before_run``
  (test_evaluation_not_configured_before_run_fires_when_run_result_says_false)
- Adding an evaluation deletes the results you already have ->
  ``evaluation_change_deletes_results``
  (test_evaluation_change_deletes_results_always_refuses)
- Warm-up is not a setting — it's a field on every evaluation ->
  ``sim_duration_too_short_for_evaluation`` [new, proactive preflight
  companion] (test_sim_duration_too_short_for_evaluation_fires_below_floor) +
  ``evaluation_warmup_start_at_zero`` [new, ideal postrun check]
  (test_evaluation_warmup_start_at_zero_fires_when_zero)
- "Queue length" is not a portable number ->
  ``queue_length_definition_not_portable``
  (test_queue_length_definition_not_portable_fires_when_definition_missing)
- Delay is only counted from a fixed distance upstream ->
  ``delay_measurement_truncated_by_queue``
  (test_delay_measurement_truncated_by_queue_fires_when_queue_exceeds_segment)
- The averaging interval changes the answer ->
  ``evaluation_interval_multiple_of_cycle``
  (test_evaluation_interval_multiple_of_cycle_fires_on_exact_multiple)
- One run is not an answer -> ``single_seed_run_not_statistically_valid``
  (test_single_seed_run_not_statistically_valid_fires_when_no_seeds_given)

§6 Calibration
----------------
- Driving behaviours are global objects -> ``driving_behavior_shared_object_edit``
  (test_driving_behavior_shared_object_edit_fires_without_duplication_flag)
- Capacity is many-to-one in the parameters ->
  ``parameter_diff_not_calibration_meaningful``
  (test_parameter_diff_not_calibration_meaningful_fires_when_moved_off_reference)
- Some parameters are off-limits by agency policy ->
  ``agency_restricted_parameter_write``
  (test_agency_restricted_parameter_write_fires_for_restricted_parameter)
- A version upgrade alone can change results -> ``vissim_version_unpinned``
  (test_vissim_version_unpinned_fires_when_different)

§7 Operations
---------------
- Dialogs will hang a worker forever -> ``unbounded_com_dialog_risk``
  (test_unbounded_com_dialog_risk_fires_when_quick_mode_not_confirmed)
- Vissim cannot run as a Windows service -> EXCLUDED, see below.
- The naive loop is the slow one -> EXCLUDED, see below.
- The error file is overwritten between runs ->
  ``error_log_overwritten_between_runs``
  (test_error_log_overwritten_between_runs_fires_when_not_archived)
- Simulation resolution changes results, not just runtime ->
  ``simulation_resolution_unpinned_across_comparison``
  (test_simulation_resolution_unpinned_across_comparison_fires_below_min)

Excluded from this module
---------------------------
"Vissim cannot run as a Windows service" and "The naive loop is the slow
one" (§7) are deployment/ops invariants, not per-action semantic checks —
neither varies with an action, a shadow model, or a run result, so encoding
either as a ``Constraint`` would mean an always-null check with no
plausible field name to wait on (the "never fabricate a datum" rule this
module otherwise follows). Both are already addressed elsewhere: the
session/licence/single-writer discipline in
docs/phase2/00-EXECUTION-GUIDE.md §3.1/§1 rule 6 and ``license_guard.py``
for the first; ``results_reader.py``'s batch reads and ``RunContinuous``
(never per-step loops, same rule 6) for the second. Not a coverage gap —
a deliberate scope boundary, recorded here and in this task's drafted
DECISIONS.md entry.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Callable

Phase = str  # "preflight" | "postwrite" | "postrun"
Tier = str  # "refuse" | "warn"


@dataclass
class Finding:
    """One constraint violation surfaced by a check.

    ``messages`` is rendered at check time (ko/en/ja, with the offending
    values interpolated). ``suggested_fix`` is machine-usable (e.g. a list of
    difflib candidate ids for #1, a capacity number for #3). ``details``
    holds arbitrary supporting evidence (readback vs written, capacity
    numbers) for logging/debugging.
    """

    constraint_id: str
    tier: Tier
    phase: Phase
    messages: dict[str, str]
    suggested_fix: Any = None
    details: dict[str, Any] = field(default_factory=dict)


CheckFn = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], "Finding | None"]


@dataclass
class Constraint:
    """One registered constraint: identity, applicability, and its check.

    ``applies_to`` lists the ``action["tool"]`` values this constraint runs
    for; ``"*"`` matches every tool (used by the postwrite/postrun checks
    that are tool-agnostic). ``message_ko``/``message_en``/``message_ja`` are
    the ``str.format``-style templates the constraint's check function
    renders against its own hit context via :func:`_render`.
    """

    id: str
    applies_to: list[str]
    phase: Phase
    tier: Tier
    check: CheckFn
    message_ko: str
    message_en: str
    message_ja: str
    suggested_fix: Any = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Constraint] = {}


def register(constraint: Constraint) -> Constraint:
    """Add a constraint to the module-level registry, keyed by its id."""
    _REGISTRY[constraint.id] = constraint
    return constraint


def get_registry() -> tuple[Constraint, ...]:
    """Return all registered constraints, in registration order."""
    return tuple(_REGISTRY.values())


def _render(constraint_id: str, **kwargs: Any) -> dict[str, str]:
    """Render a constraint's ko/en/ja templates against a hit context."""
    constraint = _REGISTRY[constraint_id]
    return {
        "ko": constraint.message_ko.format(**kwargs),
        "en": constraint.message_en.format(**kwargs),
        "ja": constraint.message_ja.format(**kwargs),
    }


def _applies(constraint: Constraint, tool: str) -> bool:
    return "*" in constraint.applies_to or tool in constraint.applies_to


def run_checks(
    phase: str,
    action: dict[str, Any],
    shadow_model: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> list[Finding]:
    """Run every registered constraint of ``phase`` applicable to ``action``.

    Returns the non-``None`` :class:`Finding` results, in registration order
    (deterministic — constraints are evaluated in the order they were
    :func:`register`-ed).
    """
    extra = extra if extra is not None else {}
    tool = action.get("tool", "")

    findings: list[Finding] = []
    for constraint in _REGISTRY.values():
        if constraint.phase != phase:
            continue
        if not _applies(constraint, tool):
            continue
        finding = constraint.check(action, shadow_model, extra)
        if finding is not None:
            findings.append(finding)
    return findings


# ---------------------------------------------------------------------------
# Shared lookup helpers
# ---------------------------------------------------------------------------

# {tool: {object_class, id_field, name_field, param}} for the M2 verb set
# constraint #1 (object_not_found) resolves against. "set_reduced_speed_area"
# (v2 unit (2)) added here per DECISIONS.md's lockstep convention -- it is an
# XML-injection write, not a COM setter, but #1's job (does the target id
# exist in the shadow model, difflib-suggest near misses if not) applies
# identically: a link_id has to resolve to a real "links" entry before
# services.simulation_network_builder.inject_reduced_speed_area ever touches
# the working copy.
_TOOL_TARGET_SPEC: dict[str, dict[str, str]] = {
    "set_vehicle_input_volume": {
        "object_class": "vehicle_inputs",
        "id_field": "no",
        "name_field": "name",
        "param": "input_no",
    },
    "set_signal_cycle": {
        "object_class": "signals",
        "id_field": "sc_no",
        "name_field": "sc_name",
        "param": "sc_no",
    },
    "set_signal_offset": {
        "object_class": "signals",
        "id_field": "sc_no",
        "name_field": "sc_name",
        "param": "sc_no",
    },
    "set_reduced_speed_area": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_id",
    },
    # v2 unit (4): set_conflict_area_status added to the object_not_found /
    # _TOOL_TARGET_SPEC lockstep table (conflict_areas object class). Conflict
    # areas have no name field of their own -- name_field points at the
    # tools_network.py-synthesized display "name" ("Link A vs Link B"), which
    # _check_object_not_found already handles gracefully via its `if name:`
    # guard when the field happens to be absent from a caller-built fixture.
    "set_conflict_area_status": {
        "object_class": "conflict_areas",
        "id_field": "no",
        "name_field": "name",
        "param": "conflict_area_no",
    },
    # v2 unit ⑥: set_route_relative_flow added to the object_not_found /
    # _TOOL_TARGET_SPEC lockstep table -- this only covers the DECISION-level
    # lookup (routing_decisions object class, keyed by decision no); the
    # SECOND-level lookup (does route_no exist WITHIN that decision) has no
    # single flat id to check against a single object list the way every
    # other tool here does, so it is a dedicated check
    # (route_not_found_in_decision, below) rather than an extension of this
    # generic single-id machinery.
    "set_route_relative_flow": {
        "object_class": "routing_decisions",
        "id_field": "no",
        "name_field": "name",
        "param": "decision_no",
    },
}

_OBJECT_CLASS_LABELS: dict[str, dict[str, str]] = {
    "vehicle_inputs": {"ko": "차량 입력", "en": "vehicle input", "ja": "車両インプット"},
    "signals": {"ko": "신호제어기", "en": "signal controller", "ja": "信号制御機"},
    "links": {"ko": "링크", "en": "link", "ja": "リンク"},
    "conflict_areas": {"ko": "충돌구간", "en": "conflict area", "ja": "錯綜区間"},
    "routing_decisions": {"ko": "차량 경로 결정", "en": "vehicle routing decision", "ja": "車両経路決定"},
}


def _find_by_field(objects: list[dict[str, Any]], field_name: str, value: str) -> dict[str, Any] | None:
    for obj in objects:
        if str(obj.get(field_name, "")) == value:
            return obj
    return None


def _coerce_number(raw: Any, cast: Callable[[Any], Any] = float) -> Any | None:
    """Cast ``raw`` via ``cast`` (``float`` or ``int``), or ``None`` on failure."""
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return None


# Live-bug fix (2026-07-27): a hit list whose size tracks NETWORK size (one
# entry per conflict-area link pair, per disconnected route, ...) rendered in
# full into a message used to flood the answer -- 144 conflict-area pairs on
# one real network -- and, via M3.3's accumulated-warnings carry-forward,
# that same wall of text then rode EVERY subsequent answer in the session,
# not just the one that first surfaced it. Cap the ENUMERATED, human-facing
# text at ``limit`` items with an explicit, localized remainder count; the
# caller is still responsible for putting the FULL, uncapped list in the
# Finding's ``details`` (machine-readable, for traces/eval -- nothing here is
# the only place the complete data lives).
def _capped_join(
    items: list[str], unit_ko: str, unit_en: str, unit_ja: str, limit: int = 8
) -> tuple[str, str, str]:
    """Join ``items`` for display, capping the enumerated prefix at ``limit``
    and folding any remainder into a "+N more" suffix per language. Returns
    ``(ko, en, ja)`` strings, identical (no suffix) when ``len(items) <=
    limit``. ``unit_*`` names what's being counted in the remainder note
    (e.g. "쌍"/"pairs"/"件" for conflict-area link pairs) so it reads as a
    sentence, not a bare number.
    """
    if len(items) <= limit:
        joined = ", ".join(items)
        return joined, joined, joined
    shown = ", ".join(items[:limit])
    remainder = len(items) - limit
    return (
        f"{shown} 외 {remainder}{unit_ko}",
        f"{shown}, and {remainder} more {unit_en}",
        f"{shown} 他{remainder}{unit_ja}",
    )


# ---------------------------------------------------------------------------
# #1 object_not_found (refuse, preflight)
# ---------------------------------------------------------------------------


def _check_object_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    tool = action.get("tool", "")
    spec = _TOOL_TARGET_SPEC.get(tool)
    if spec is None:
        return None

    params = action.get("params", {})
    target_id = str(params.get(spec["param"], ""))
    objects = shadow_model.get(spec["object_class"], [])

    if _find_by_field(objects, spec["id_field"], target_id) is not None:
        return None

    # Fuzzy-match target_id against both ids and names, but always resolve
    # back to the owning object's id: suggested_fix is machine-usable (a
    # caller retries with suggested_fix[0] as input_no/sc_no), so a name
    # string leaking through would break that retry.
    owner_id_by_string: dict[str, str] = {}
    match_pool: list[str] = []
    for obj in objects:
        obj_id = str(obj.get(spec["id_field"], ""))
        if obj_id and obj_id not in owner_id_by_string:
            match_pool.append(obj_id)
            owner_id_by_string[obj_id] = obj_id
        name = obj.get(spec["name_field"])
        if name:
            name_str = str(name)
            if name_str not in owner_id_by_string:
                match_pool.append(name_str)
                owner_id_by_string[name_str] = obj_id

    candidates: list[str] = []
    seen_ids: set[str] = set()
    for match in difflib.get_close_matches(target_id, match_pool, n=5):
        owner_id = owner_id_by_string[match]
        if owner_id in seen_ids:
            continue
        seen_ids.add(owner_id)
        candidates.append(owner_id)

    labels = _OBJECT_CLASS_LABELS[spec["object_class"]]
    return Finding(
        constraint_id="object_not_found",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "object_not_found",
            target_id=target_id,
            object_class_ko=labels["ko"],
            object_class_en=labels["en"],
            object_class_ja=labels["ja"],
        ),
        suggested_fix=candidates,
        details={"tool": tool, "target_id": target_id, "object_class": spec["object_class"]},
    )


register(
    Constraint(
        id="object_not_found",
        applies_to=[
            "set_vehicle_input_volume",
            "set_signal_cycle",
            "set_signal_offset",
            "set_reduced_speed_area",
            "set_conflict_area_status",
            "set_route_relative_flow",
        ],
        phase="preflight",
        tier="refuse",
        check=_check_object_not_found,
        message_ko="대상 ID '{target_id}'을(를) {object_class_ko} 목록에서 찾을 수 없습니다. "
        "오타이거나 잘못된 ID일 수 있으니 아래 후보를 확인해 주세요.",
        message_en="Target id '{target_id}' was not found among {object_class_en} objects. "
        "It may be a typo — check the suggested candidates.",
        message_ja="対象ID '{target_id}' は {object_class_ja} の一覧に見つかりませんでした。"
        "タイプミスの可能性があるため、候補をご確認ください。",
    )
)


# ---------------------------------------------------------------------------
# #2 volume_out_of_range (refuse, preflight)
# ---------------------------------------------------------------------------


def _check_volume_out_of_range(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_input_volume":
        return None

    params = action.get("params", {})
    raw_volume = params.get("volume")
    if raw_volume is None:
        return None
    volume = _coerce_number(raw_volume)
    if volume is None:
        return None

    if 0 <= volume <= 20000:
        return None

    return Finding(
        constraint_id="volume_out_of_range",
        tier="refuse",
        phase="preflight",
        messages=_render("volume_out_of_range", volume=raw_volume),
        suggested_fix={"min": 0, "max": 20000},
        details={"volume": volume},
    )


register(
    Constraint(
        id="volume_out_of_range",
        applies_to=["set_vehicle_input_volume"],
        phase="preflight",
        tier="refuse",
        check=_check_volume_out_of_range,
        message_ko="요청한 차량 입력 볼륨 {volume} veh/h는 허용 범위(0–20000 veh/h)를 벗어났습니다.",
        message_en="Requested vehicle input volume {volume} veh/h is outside the allowed "
        "range (0–20000 veh/h).",
        message_ja="指定された車両インプット交通量 {volume} veh/h は許容範囲(0–20000 veh/h)を"
        "超えています。",
    )
)


# ---------------------------------------------------------------------------
# #3 latent_demand_overflow (warn, preflight)
# ---------------------------------------------------------------------------

_ROUGH_LANE_CAPACITY_VEH_PER_H = 1900


def _check_latent_demand_overflow(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_input_volume":
        return None

    params = action.get("params", {})
    input_no = str(params.get("input_no", ""))
    raw_volume = params.get("volume")
    if raw_volume is None:
        return None
    volume = _coerce_number(raw_volume)
    if volume is None:
        return None

    vehicle_input = _find_by_field(shadow_model.get("vehicle_inputs", []), "no", input_no)
    if vehicle_input is None:
        return None  # missing target is object_not_found's job (#1)

    link_id = str(vehicle_input.get("link", ""))
    link = _find_by_field(shadow_model.get("links", []), "id", link_id)
    if link is None:
        return None  # incomplete network data; not this constraint's job

    lanes = link.get("lanes")
    if not isinstance(lanes, (int, float)) or lanes <= 0:
        return None

    capacity = lanes * _ROUGH_LANE_CAPACITY_VEH_PER_H
    if volume <= capacity:
        return None

    return Finding(
        constraint_id="latent_demand_overflow",
        tier="warn",
        phase="preflight",
        messages=_render(
            "latent_demand_overflow",
            volume=raw_volume,
            capacity=capacity,
            link_id=link_id,
            lanes=lanes,
        ),
        suggested_fix={"max_recommended_volume": capacity},
        details={"volume": volume, "capacity": capacity, "link_id": link_id, "lanes": lanes},
    )


register(
    Constraint(
        id="latent_demand_overflow",
        applies_to=["set_vehicle_input_volume"],
        phase="preflight",
        tier="warn",
        check=_check_latent_demand_overflow,
        message_ko="요청한 볼륨 {volume} veh/h가 링크 '{link_id}'(차로 {lanes}개)의 대략적 용량 "
        "{capacity} veh/h를 초과합니다. 초과 수요는 화면에 보이지 않는 잠재 수요로 대기하게 됩니다.",
        message_en="Requested volume {volume} veh/h exceeds the rough capacity {capacity} "
        "veh/h of link '{link_id}' ({lanes} lanes). Excess demand will queue invisibly as "
        "latent demand.",
        message_ja="指定した交通量 {volume} veh/h はリンク '{link_id}'(車線数 {lanes})のおおよその"
        "容量 {capacity} veh/h を超えています。超過分は目に見えない潜在需要として滞留します。",
    )
)


# ---------------------------------------------------------------------------
# #4 signal_controller_type_unsupported (refuse, preflight)
#
# Gap CLOSED in v2 unit ① (2026-07-24; was docs/phase2/DECISIONS.md
# 2026-07-22 entry, superseded): signal_parser.parse_signal_controllers now
# extracts the real <signalController type="..."> attribute onto
# SignalInfo.controller_type, verified against all 25 real PTV Vissim 2026
# example networks. Empirically only two type strings are COM-writable for
# cycle/offset (M2.1's CycTm finding, live-verified on the Beijing (FIXEDTIME)
# and Air Taxi Vertiport (FIXEDTIMESIMPLE) examples): "FIXEDTIME" and
# "FIXEDTIMESIMPLE". Every other real type observed (VAP, RINGBARRIERCONTROLLER,
# RAILWAYCROSSING, PEDESTRIANCROSSING, TWOSTAGECONTROLLER) keeps its program
# logic in a form Vissim's COM API cannot safely rewrite via CycTm/Offset —
# TOOL-CATALOG.md's "Impossible by design" refusal for external
# RBC/VAP/Econolite logic. Missing or "unknown" (signal_parser's own fallback
# when the attribute is genuinely blank) is still treated as unsupported —
# the conservative refuse-on-unproven-type stance is unchanged, just now
# backed by a real field instead of a forward-declared one. Cycle edits stay
# additionally guarded by guardrails.py's "parser_blind" postwrite handling
# for program-driven fixed-time controllers (the CycTm finding) — #4 only
# gates the controller FAMILY, not per-controller program-driven-ness.
# ---------------------------------------------------------------------------

_SUPPORTED_CONTROLLER_TYPES = frozenset({"FIXEDTIME", "FIXEDTIMESIMPLE"})


def _check_signal_controller_type_unsupported(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    tool = action.get("tool")
    if tool not in ("set_signal_cycle", "set_signal_offset"):
        return None

    params = action.get("params", {})
    sc_no = str(params.get("sc_no", ""))
    signal = _find_by_field(shadow_model.get("signals", []), "sc_no", sc_no)
    if signal is None:
        return None  # missing target is object_not_found's job (#1)

    controller_type = signal.get("controller_type")
    if controller_type in _SUPPORTED_CONTROLLER_TYPES:
        return None

    displayed_type = controller_type if controller_type else "unknown"
    return Finding(
        constraint_id="signal_controller_type_unsupported",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "signal_controller_type_unsupported", sc_no=sc_no, controller_type=displayed_type
        ),
        suggested_fix=None,
        details={"sc_no": sc_no, "controller_type": displayed_type},
    )


register(
    Constraint(
        id="signal_controller_type_unsupported",
        applies_to=["set_signal_cycle", "set_signal_offset"],
        phase="preflight",
        tier="refuse",
        check=_check_signal_controller_type_unsupported,
        message_ko="신호제어기 '{sc_no}'의 컨트롤러 타입({controller_type})은 지원되지 않습니다. "
        "고정시간(fixed-time) 제어기만 COM으로 주기/옵셋을 안전하게 수정할 수 있습니다.",
        message_en="Signal controller '{sc_no}' has controller type '{controller_type}', which "
        "is not supported. Only fixed-time controllers can have cycle/offset edited safely "
        "via COM.",
        message_ja="信号制御機 '{sc_no}' のコントローラタイプ('{controller_type}')はサポートされて"
        "いません。COM経由でサイクル/オフセットを安全に編集できるのは固定時間式(fixed-time)制御機"
        "のみです。",
    )
)


# ---------------------------------------------------------------------------
# #5 cycle_below_intergreen_sum (refuse, preflight)
#
# Gap decision (docs/phase2/DECISIONS.md): min_green_intergreen_sum is not yet
# parsed by signal_parser.py. We read it from the shadow-model signal entry
# when present; when absent, this check is silently skipped (the write itself
# remains read-back-verifiable — only this semantic screen lacks its datum
# until M3.1).
# ---------------------------------------------------------------------------


def _check_cycle_below_intergreen_sum(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_signal_cycle":
        return None

    params = action.get("params", {})
    sc_no = str(params.get("sc_no", ""))
    raw_cycle = params.get("cycle")
    if raw_cycle is None:
        return None

    signal = _find_by_field(shadow_model.get("signals", []), "sc_no", sc_no)
    if signal is None:
        return None  # missing target is object_not_found's job (#1)

    raw_min_sum = signal.get("min_green_intergreen_sum")
    if raw_min_sum is None:
        return None  # datum unavailable until M3.1 — see DECISIONS.md

    cycle = _coerce_number(raw_cycle)
    min_sum = _coerce_number(raw_min_sum)
    if cycle is None or min_sum is None:
        return None

    if cycle >= min_sum:
        return None

    return Finding(
        constraint_id="cycle_below_intergreen_sum",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "cycle_below_intergreen_sum", sc_no=sc_no, cycle=raw_cycle, min_sum=raw_min_sum
        ),
        suggested_fix={"min_cycle": min_sum},
        details={"sc_no": sc_no, "cycle": cycle, "min_sum": min_sum},
    )


register(
    Constraint(
        id="cycle_below_intergreen_sum",
        applies_to=["set_signal_cycle"],
        phase="preflight",
        tier="refuse",
        check=_check_cycle_below_intergreen_sum,
        message_ko="제안된 주기 {cycle}초가 신호제어기 '{sc_no}'의 최소 녹색시간+인터그린 시간 합계 "
        "{min_sum}초보다 짧습니다.",
        message_en="Proposed cycle {cycle}s is shorter than the minimum green + intergreen "
        "time sum of {min_sum}s for signal controller '{sc_no}'.",
        message_ja="提案されたサイクル {cycle}秒 は信号制御機 '{sc_no}' の最小青時間+インター"
        "グリーン時間の合計 {min_sum}秒 より短くなっています。",
    )
)


# ---------------------------------------------------------------------------
# #6 write_verification_failed (refuse -> abort, postwrite)
# ---------------------------------------------------------------------------

_FLOAT_TOLERANCE = 1e-9


def _values_differ(written: Any, readback: Any) -> bool:
    if isinstance(written, (int, float)) and isinstance(readback, (int, float)):
        return abs(float(written) - float(readback)) > _FLOAT_TOLERANCE
    return written != readback


def _check_write_verification_failed(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if "written_value" not in extra or "readback_value" not in extra:
        return None

    written = extra["written_value"]
    readback = extra["readback_value"]
    if not _values_differ(written, readback):
        return None

    return Finding(
        constraint_id="write_verification_failed",
        tier="refuse",
        phase="postwrite",
        messages=_render("write_verification_failed", written=written, readback=readback),
        suggested_fix=None,
        details={"written_value": written, "readback_value": readback},
    )


register(
    Constraint(
        id="write_verification_failed",
        applies_to=["*"],
        phase="postwrite",
        tier="refuse",
        check=_check_write_verification_failed,
        message_ko="쓰기 검증 실패: 기록한 값 {written}이(가) 재확인한 값 {readback}과 일치하지 "
        "않습니다.",
        message_en="Write verification failed: written value {written} does not match the "
        "read-back value {readback}.",
        message_ja="書き込み検証に失敗しました: 書き込んだ値 {written} が読み戻した値 {readback} "
        "と一致しません。",
    )
)


# ---------------------------------------------------------------------------
# #7 results_missing_kpi_tables (warn, postrun)
# ---------------------------------------------------------------------------


def _check_results_missing_kpi_tables(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    tables = run_result.get("tables", [])
    normalized = {str(t).upper() for t in tables}
    if normalized != {"SIMULATIONRUN"}:
        return None

    return Finding(
        constraint_id="results_missing_kpi_tables",
        tier="warn",
        phase="postrun",
        messages=_render("results_missing_kpi_tables", tables=", ".join(sorted(tables))),
        suggested_fix=None,
        details={"tables": list(tables)},
    )


register(
    Constraint(
        id="results_missing_kpi_tables",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_results_missing_kpi_tables,
        message_ko="결과 데이터베이스에 {tables} 테이블만 존재합니다. 평가 구간이 적용되기에는 "
        "시뮬레이션 실행 시간이 너무 짧았을 수 있습니다.",
        message_en="The result database contains only the {tables} table. The run may have "
        "been too short for evaluation intervals to produce KPI tables.",
        message_ja="結果データベースには {tables} テーブルしか存在しません。評価区間(evaluation "
        "interval)が適用されるにはシミュレーション実行時間が短すぎた可能性があります。",
    )
)


# ---------------------------------------------------------------------------
# #8 vehicles_deleted_in_run (warn, postrun)
# ---------------------------------------------------------------------------


def _check_vehicles_deleted_in_run(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    raw_deleted = run_result.get("deleted_vehicles")
    if raw_deleted is None:
        return None
    deleted = _coerce_number(raw_deleted, cast=int)
    if deleted is None:
        return None
    if deleted <= 0:
        return None

    return Finding(
        constraint_id="vehicles_deleted_in_run",
        tier="warn",
        phase="postrun",
        messages=_render("vehicles_deleted_in_run", deleted_vehicles=deleted),
        suggested_fix=None,
        details={"deleted_vehicles": deleted},
    )


register(
    Constraint(
        id="vehicles_deleted_in_run",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_vehicles_deleted_in_run,
        message_ko="이번 실행에서 차량 {deleted_vehicles}대가 삭제(디퓨전 소멸)되었습니다. 수요가 "
        "눈에 띄지 않게 사라져 KPI가 실제 부하보다 낮게 나타날 수 있습니다.",
        message_en="{deleted_vehicles} vehicle(s) were deleted during this run (DiffusTm "
        "disappearances). Demand silently vanished; KPIs understate load.",
        message_ja="今回の実行で車両 {deleted_vehicles}台 が削除されました(DiffusTm消失)。需要が"
        "気づかれずに消えたため、KPIは実際の負荷より低く表示される可能性があります。",
    )
)


# =============================================================================
# M3.1 — the rest of the catalog (docs/reference/vissim-constraints.html)
#
# Batch 1/4: constraints that fire directly on the M2 verb set
# (set_vehicle_input_volume, set_signal_cycle, set_signal_offset,
# set_sim_duration, set_random_seed, run_simulation), per docs/phase2/
# 02-ROADMAP.md M3.1's priority order (a).
# =============================================================================


# ---------------------------------------------------------------------------
# #9 single_seed_run_not_statistically_valid (warn, preflight)
#
# Catalog §5 "One run is not an answer": microsimulation is stochastic; DOT
# minimums cited range 5 (Maryland) - 11 (Washington State). Uses only
# action.params — run_simulation's own RunSimulationParams.random_seeds — no
# shadow-model or extra dependency, so this is a fully real, always-live
# check today.
# ---------------------------------------------------------------------------

_MIN_RECOMMENDED_SEED_COUNT = 5


def _check_single_seed_run_not_statistically_valid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "run_simulation":
        return None

    params = action.get("params", {})
    raw_seeds = params.get("random_seeds")
    seed_count = len(raw_seeds) if isinstance(raw_seeds, list) and raw_seeds else 1

    if seed_count >= _MIN_RECOMMENDED_SEED_COUNT:
        return None

    return Finding(
        constraint_id="single_seed_run_not_statistically_valid",
        tier="warn",
        phase="preflight",
        messages=_render(
            "single_seed_run_not_statistically_valid",
            seed_count=seed_count,
            min_seeds=_MIN_RECOMMENDED_SEED_COUNT,
        ),
        suggested_fix={"recommended_min_seeds": _MIN_RECOMMENDED_SEED_COUNT},
        details={"seed_count": seed_count},
    )


register(
    Constraint(
        id="single_seed_run_not_statistically_valid",
        applies_to=["run_simulation"],
        phase="preflight",
        tier="warn",
        check=_check_single_seed_run_not_statistically_valid,
        message_ko="이번 실행은 시드 {seed_count}개로만 진행됩니다. 마이크로시뮬레이션은 확률적이므로 "
        "단일(또는 소수) 실행 결과는 잠정치입니다. 기관 최소 기준(5~11개)에 맞춰 시드를 "
        "{min_seeds}개 이상 사용하는 것을 권장합니다.",
        message_en="This run uses only {seed_count} random seed(s). Microsimulation is "
        "stochastic, so a single (or small) run's result is provisional. DOT minimums range "
        "5-11 seeds; consider at least {min_seeds}.",
        message_ja="今回の実行はシード{seed_count}個のみで行われます。マイクロシミュレーションは"
        "確率的であるため、単一(または少数)の実行結果は暫定値です。行政機関の最低基準"
        "(5~11個)に合わせ、シードを{min_seeds}個以上使用することを推奨します。",
    )
)


# ---------------------------------------------------------------------------
# #10 sim_duration_too_short_for_evaluation (warn, preflight)
#
# Catalog §5 "Warm-up is not a setting — it's a field on every evaluation":
# simulations start empty, so early results are meaningless, and DOT warm-up
# minimums range 10-30 minutes. This is the proactive preflight companion to
# #22 evaluation_warmup_start_at_zero below (the ideal postrun check against
# the real per-evaluation start time, which the shadow model cannot supply
# today — see that constraint's CATALOG-GAP note). This one uses only
# set_sim_duration's own params.seconds, so it is fully real today.
# ---------------------------------------------------------------------------

_MIN_RECOMMENDED_SIM_DURATION_S = 600  # 10 minutes — the low end of cited DOT warm-up minimums


def _check_sim_duration_too_short_for_evaluation(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_sim_duration":
        return None

    params = action.get("params", {})
    raw_seconds = params.get("seconds")
    if raw_seconds is None:
        return None
    seconds = _coerce_number(raw_seconds, cast=int)
    if seconds is None:
        return None

    if seconds >= _MIN_RECOMMENDED_SIM_DURATION_S:
        return None

    return Finding(
        constraint_id="sim_duration_too_short_for_evaluation",
        tier="warn",
        phase="preflight",
        messages=_render(
            "sim_duration_too_short_for_evaluation",
            seconds=raw_seconds,
            min_seconds=_MIN_RECOMMENDED_SIM_DURATION_S,
        ),
        suggested_fix={"recommended_min_seconds": _MIN_RECOMMENDED_SIM_DURATION_S},
        details={"seconds": seconds},
    )


register(
    Constraint(
        id="sim_duration_too_short_for_evaluation",
        applies_to=["set_sim_duration"],
        phase="preflight",
        tier="warn",
        check=_check_sim_duration_too_short_for_evaluation,
        message_ko="시뮬레이션 시간을 {seconds}초로 설정하려 합니다. Vissim은 빈 네트워크에서 "
        "시작하므로 초반 구간은 워밍업으로 제외되어야 하며, 권장 최소 시간은 {min_seconds}초"
        "(10분)입니다. 이보다 짧으면 평가 구간이 제대로 확보되지 않을 수 있습니다.",
        message_en="Setting simulation duration to {seconds}s. Vissim starts from an empty "
        "network, so early results need to be excluded as warm-up; the recommended floor is "
        "{min_seconds}s (10 min). Shorter than that risks leaving no real evaluation window.",
        message_ja="シミュレーション時間を{seconds}秒に設定しようとしています。Vissimは空の"
        "ネットワークから開始するため、序盤はウォームアップとして除外する必要があり、推奨最小"
        "時間は{min_seconds}秒(10分)です。これより短いと評価区間が確保できない可能性があります。",
    )
)


# ---------------------------------------------------------------------------
# #11 signal_cycle_change_may_break_corridor_sync (warn, preflight)
#
# Catalog §4 "Cycle time is a corridor property, not an intersection one":
# coordinated signals only stay in step while they share a cycle length.
# Uses only shadow_model["signals"] (already-available cycle_time per
# controller) — a real, always-live check: if any OTHER controller currently
# shares the target's cycle_time (a proxy for "this corridor is
# coordinated"), changing the target away from that value risks breaking the
# green wave.
# ---------------------------------------------------------------------------


def _check_signal_cycle_change_may_break_corridor_sync(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_signal_cycle":
        return None

    params = action.get("params", {})
    sc_no = str(params.get("sc_no", ""))
    raw_new_cycle = params.get("cycle")
    if raw_new_cycle is None:
        return None
    new_cycle = _coerce_number(raw_new_cycle)
    if new_cycle is None:
        return None

    signals = shadow_model.get("signals", [])
    target = _find_by_field(signals, "sc_no", sc_no)
    if target is None:
        return None  # missing target is object_not_found's job (#1)

    old_cycle = _coerce_number(target.get("cycle_time"))
    if old_cycle is None:
        return None

    if abs(new_cycle - old_cycle) <= _FLOAT_TOLERANCE:
        return None  # not actually changing the cycle

    siblings = [
        str(sig.get("sc_no", ""))
        for sig in signals
        if str(sig.get("sc_no", "")) != sc_no
        and (candidate := _coerce_number(sig.get("cycle_time"))) is not None
        and abs(candidate - old_cycle) <= _FLOAT_TOLERANCE
    ]
    if not siblings:
        return None

    # Capped for the human-facing message only (same fix as
    # conflict_area_data_unavailable / route_integrity_unverifiable) — a
    # network-wide common background cycle can leave many controllers
    # sharing one cycle time. suggested_fix/details below keep the full,
    # uncapped list; only the rendered sentence is capped.
    siblings_ko, siblings_en, siblings_ja = _capped_join(siblings, "개", "controllers", "件")
    return Finding(
        constraint_id="signal_cycle_change_may_break_corridor_sync",
        tier="warn",
        phase="preflight",
        messages=_render(
            "signal_cycle_change_may_break_corridor_sync",
            sc_no=sc_no,
            old_cycle=target.get("cycle_time"),
            new_cycle=raw_new_cycle,
            siblings_ko=siblings_ko,
            siblings_en=siblings_en,
            siblings_ja=siblings_ja,
        ),
        suggested_fix={"coordinated_controllers": siblings},
        details={"sc_no": sc_no, "old_cycle": old_cycle, "new_cycle": new_cycle, "siblings": siblings},
    )


register(
    Constraint(
        id="signal_cycle_change_may_break_corridor_sync",
        applies_to=["set_signal_cycle"],
        phase="preflight",
        tier="warn",
        check=_check_signal_cycle_change_may_break_corridor_sync,
        message_ko="신호제어기 '{sc_no}'의 주기를 {old_cycle}초에서 {new_cycle}초로 변경하려 합니다. "
        "동일한 {old_cycle}초 주기를 공유하는 다른 제어기({siblings_ko})가 있어, 이는 교차로 단위가 "
        "아니라 코리도 단위의 변경일 수 있습니다 — 이 제어기만 바꾸면 옵셋 연동(green wave)이 "
        "몇 주기 안에 깨질 수 있습니다.",
        message_en="Changing signal controller '{sc_no}' cycle from {old_cycle}s to {new_cycle}s. "
        "Other controller(s) ({siblings_en}) currently share the same {old_cycle}s cycle, "
        "suggesting this is a corridor-level change, not an intersection-level one — changing "
        "only this controller can break the coordinated green wave within a few cycles.",
        message_ja="信号制御機 '{sc_no}' のサイクルを{old_cycle}秒から{new_cycle}秒に変更しようと"
        "しています。同じ{old_cycle}秒サイクルを共有する他の制御機({siblings_ja})が存在するため、"
        "これは交差点単位ではなくコリドー単位の変更である可能性があります — この制御機のみを"
        "変更すると、連携(グリーンウェーブ)が数サイクル以内に崩れる可能性があります。",
    )
)


# ---------------------------------------------------------------------------
# #12 cc1_scalar_write_rejected (refuse, preflight)
#
# Catalog §2 "The main calibration parameter isn't a number": CC1 is a
# reference to a Time Distribution object, not a scalar; SetAttValue with a
# bare number fails. Defensive, tool-agnostic: no current M2 write tool has a
# "cc1" param, so this never fires in production today — it exists to refuse
# a hallucinated or future naive tool call before it ever reaches COM.
# ---------------------------------------------------------------------------


def _check_cc1_scalar_write_rejected(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    params = action.get("params", {})
    for key, value in params.items():
        if str(key).lower() == "cc1" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return Finding(
                constraint_id="cc1_scalar_write_rejected",
                tier="refuse",
                phase="preflight",
                messages=_render("cc1_scalar_write_rejected", value=value),
                suggested_fix={
                    "hint": "point cc1 at a (new, duplicated) Time Distribution object instead"
                },
                details={"cc1_value": value},
            )
    return None


register(
    Constraint(
        id="cc1_scalar_write_rejected",
        applies_to=["*"],
        phase="preflight",
        tier="refuse",
        check=_check_cc1_scalar_write_rejected,
        message_ko="CC1에 숫자 {value}을(를) 직접 기록할 수 없습니다. CC1은 스칼라가 아니라 "
        "Time Distribution 객체에 대한 참조입니다. 다른 분포를 가리키거나, 그 분포의 평균을 "
        "수정해야 합니다 — 분포는 공유 객체이므로 새 분포를 복제해 사용하세요.",
        message_en="Cannot write the number {value} directly to CC1. CC1 is a reference to a "
        "Time Distribution object, not a scalar. Point it at a different distribution or edit "
        "that distribution's mean — distributions are shared, so duplicate before mutating.",
        message_ja="CC1に数値{value}を直接書き込むことはできません。CC1はスカラーではなく、"
        "Time Distributionオブジェクトへの参照です。別の分布を指すか、その分布の平均値を編集して"
        "ください — 分布は共有オブジェクトのため、変更前に複製してください。",
    )
)


# ---------------------------------------------------------------------------
# #13 vehicle_input_multi_interval_write_gap (warn, preflight)
#
# Catalog §4 "Volume is always per hour, whatever the interval": a vehicle
# input with a multi-interval demand profile has one volume value PER
# interval.
#
# GAP CLOSED in v2 unit ⑤ (2026-07-24): tools_network._parse_vehicle_inputs
# now populates "interval_count" (and the full per-interval "volumes" list)
# for real, straight from every <timeIntervalVehVolume> child — verified live
# against Vissim 2026 (backend/tools/vehicle_input_interval_probe.py, Border
# Crossing Breitenau.DE/Roundabout Schenectady.US/Toll Plaza Camden.US). AND
# set_vehicle_input_volume gained an optional interval_no param, so this
# check now reads the way its own catalog citation always meant: it fires
# ONLY when the write (implicitly or explicitly) targets interval 1 while
# OTHER intervals exist that this write leaves untouched. A write that
# explicitly targets interval_no > 1 is a deliberate, informed choice about
# WHICH interval to change — it does not trigger this warning (interval-
# bounds correctness for that case is vehicle_input_interval_out_of_range's
# job instead, below).
# ---------------------------------------------------------------------------


def _check_vehicle_input_multi_interval_write_gap(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_input_volume":
        return None

    params = action.get("params", {})
    raw_interval_no = params.get("interval_no", 1)
    interval_no = _coerce_number(raw_interval_no, cast=int)
    if interval_no is None or interval_no != 1:
        return None  # explicitly targeting a specific non-first interval — not this gap

    input_no = str(params.get("input_no", ""))
    vehicle_input = _find_by_field(shadow_model.get("vehicle_inputs", []), "no", input_no)
    if vehicle_input is None:
        return None  # missing target is object_not_found's job (#1)

    raw_interval_count = vehicle_input.get("interval_count")
    if raw_interval_count is None:
        return None  # caller-built fixture omitting the field entirely — defensive
    interval_count = _coerce_number(raw_interval_count, cast=int)
    if interval_count is None or interval_count <= 1:
        return None

    return Finding(
        constraint_id="vehicle_input_multi_interval_write_gap",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_input_multi_interval_write_gap", input_no=input_no, interval_count=interval_count
        ),
        suggested_fix=None,
        details={"input_no": input_no, "interval_count": interval_count},
    )


register(
    Constraint(
        id="vehicle_input_multi_interval_write_gap",
        applies_to=["set_vehicle_input_volume"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_input_multi_interval_write_gap,
        message_ko="차량 입력 '{input_no}'은(는) {interval_count}개의 시간 구간을 가지고 있습니다. "
        "이번 쓰기는 첫 번째 구간만 변경하며 나머지 {interval_count}개 구간은 그대로 남습니다. "
        "전체 프로파일을 바꾸려면 각 구간을 모두 갱신해야 합니다.",
        message_en="Vehicle input '{input_no}' has {interval_count} time intervals. This write "
        "only changes the first interval — the remaining intervals are left unchanged. Update "
        "every interval if the whole demand profile should change.",
        message_ja="車両インプット '{input_no}' には{interval_count}個の時間区間があります。"
        "今回の書き込みは最初の区間のみを変更し、残りの区間はそのまま残ります。需要プロファイル"
        "全体を変更する場合は、すべての区間を更新してください。",
    )
)


# ---------------------------------------------------------------------------
# #14 demand_scale_route_double_count_gap (warn, preflight)
#
# Catalog §4 "Scaling demand automatically scales routes — don't do it
# twice": route volumes are relative shares of what arrives, so raising input
# volume already carries through to every downstream route.
#
# HALF of this CATALOG-GAP closed in v2 unit ⑥ (2026-07-24):
# tools_network._flatten_routes now populates shadow_model["routes"] for
# real (a flat, id-keyed list — see that function's docstring), so the
# `if not routes: return None` early-exit below no longer short-circuits on
# a real network. The OTHER half stays open: `manually_scaled_this_turn` is
# turn-scoped ephemeral state ("was this route touched by
# set_route_relative_flow earlier in THIS SAME agent turn") that has no
# representation in the .inpx a shadow-model reparse reads — only
# services.agent.orchestrator's turn loop could thread that fact into a
# later preflight call within the same turn, and orchestrator.py is out of
# this unit's file scope (see the task brief's SCOPE list). Every real
# route's `manually_scaled_this_turn` is hard-coded False by
# `_flatten_routes`, so `manually_scaled` below is always `[]` and this
# check still never fires in production — same visible behavior as before,
# now for the documented reason instead of the old absent-key one. See the
# drafted DECISIONS.md entry.
# ---------------------------------------------------------------------------


def _check_demand_scale_route_double_count_gap(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_input_volume":
        return None

    routes = shadow_model.get("routes")  # CATALOG-GAP: not part of the shadow-model contract
    if not routes:
        return None

    params = action.get("params", {})
    input_no = str(params.get("input_no", ""))
    manually_scaled = [
        str(route.get("id", "")) for route in routes if route.get("manually_scaled_this_turn")
    ]
    if not manually_scaled:
        return None

    return Finding(
        constraint_id="demand_scale_route_double_count_gap",
        tier="warn",
        phase="preflight",
        messages=_render(
            "demand_scale_route_double_count_gap",
            input_no=input_no,
            routes=", ".join(manually_scaled),
        ),
        suggested_fix=None,
        details={"input_no": input_no, "manually_scaled_routes": manually_scaled},
    )


register(
    Constraint(
        id="demand_scale_route_double_count_gap",
        applies_to=["set_vehicle_input_volume"],
        phase="preflight",
        tier="warn",
        check=_check_demand_scale_route_double_count_gap,
        message_ko="차량 입력 '{input_no}'의 볼륨 변경은 하위 경로({routes})의 상대 배분에 이미 "
        "자동으로 반영됩니다. 해당 경로들이 이번 턴에 수동으로도 조정된 것으로 표시되어 있어 "
        "이중 반영(double-scaling) 위험이 있습니다 — 입력만 조정하고 경로 비율은 그대로 두세요.",
        message_en="Changing vehicle input '{input_no}''s volume already carries through "
        "automatically to downstream route(s) ({routes})'s relative shares. Those routes are "
        "also flagged as manually adjusted this turn — risk of double-scaling. Scale the input "
        "only; leave relative route flows alone.",
        message_ja="車両インプット '{input_no}' のボリューム変更は、下流の経路({routes})の相対"
        "配分に既に自動的に反映されています。これらの経路は今回のターンで手動でも調整された"
        "とマークされており、二重反映(ダブルスケーリング)のリスクがあります — インプットのみを"
        "調整し、経路の相対比率はそのままにしてください。",
    )
)


# ---------------------------------------------------------------------------
# #15 evaluation_not_configured_before_run (warn, postrun)
#
# Catalog §5 "Evaluations are not retroactive": Vissim records only into
# measurements armed before the run starts.
#
# Originally specified (M3.1) as a PREFLIGHT check against
# shadow_model["evaluations"] -- a CATALOG-GAP field that could never be
# populated in practice: the M2/M2.4 job runner submits each seed on its OWN
# fresh clone in its own job work dir (routers.simulations.
# _run_agent_simulation_job), never the agent turn's ArtifactManager working
# copy a preflight shadow-model reparse would see, so there was no way for
# this check to ever see "evaluations" before dispatch.
#
# Superseded by the fix that makes the underlying gap real (docs/phase2/
# DECISIONS.md, 2026-07-24 "run job configures evaluation itself" entry):
# the job runner now calls VissimController.configure_network_performance_
# evaluation() itself, once per seed, right after load_network and before
# RunContinuous -- and tools_run.py's run_simulation now reports whether
# that configuration actually landed as extra["run_result"]["evaluation_
# configured"] (postrun-only information; it cannot exist before the tool
# dispatches). Moved this check from preflight to postrun and pointed it at
# that field so it can actually fire in production instead of only in a
# synthetic fixture. Tier/id/applies_to are unchanged; this is a field-source
# + phase correction, not a new constraint.
#
# In practice, per the job runner's own hard-fail policy ("failure to
# configure evaluation aborts the whole job, never a silent KPI-less run" —
# routers/simulations.py), a "done" job record's evaluation_configured is
# always True today, so this warning is currently unreachable via the
# production job path; it stays a live, tested detector (both branches, via
# an injected extra["run_result"]) for any FUTURE run tool/job path that
# reports evaluation_configured: False instead of hard-failing outright, and
# as a defensive backstop if a job runner regression stops setting the field
# at all (tools_run.py's own defensive ``.get(..., False)`` default) — see
# docs/phase2/DECISIONS.md for the entry.
# ---------------------------------------------------------------------------


def _check_evaluation_not_configured_before_run(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "run_simulation":
        return None

    run_result = extra.get("run_result")
    if run_result is None:
        return None
    if "evaluation_configured" not in run_result:
        return None  # unmapped/older run tool -- no claim either way
    if run_result.get("evaluation_configured"):
        return None

    return Finding(
        constraint_id="evaluation_not_configured_before_run",
        tier="warn",
        phase="postrun",
        messages=_render("evaluation_not_configured_before_run"),
        suggested_fix=None,
        details={},
    )


register(
    Constraint(
        id="evaluation_not_configured_before_run",
        applies_to=["run_simulation"],
        phase="postrun",
        tier="warn",
        check=_check_evaluation_not_configured_before_run,
        message_ko="이 네트워크에는 활성화된 평가(evaluation)가 없습니다. Vissim은 실행 전에 켜둔 "
        "측정값만 기록하므로, 평가를 먼저 설정하지 않으면 이번 실행 이후 다시 실행하지 않고는 "
        "답할 수 없는 질문들이 생깁니다.",
        message_en="This network has no armed evaluations. Vissim only records measurements "
        "switched on before the run starts — without configuring one first, some questions "
        "about this run will be unanswerable without a re-run.",
        message_ja="このネットワークには有効化された評価(evaluation)がありません。Vissimは実行前に"
        "オンにした測定値のみを記録するため、事前に評価を設定しないと、今回の実行後に再実行"
        "なしでは答えられない質問が生じます。",
    )
)


# ---------------------------------------------------------------------------
# #16 lane_count_edit_unsupported (refuse, preflight)
#
# Catalog §2 "There is no lane-count property": lane count is fixed at link
# construction (AddLink's lane-width array); there is no per-lane
# add/remove attribute. Defensive, tool-agnostic: no current M2 write tool
# carries a lane-count-shaped param, so this guards against a hallucinated
# or future naive tool call before it ever reaches COM.
# ---------------------------------------------------------------------------

_LANE_COUNT_PARAM_NAMES = {"lane_count", "num_lanes", "lanes"}


def _check_lane_count_edit_unsupported(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    params = action.get("params", {})
    for key in params:
        if str(key).lower() in _LANE_COUNT_PARAM_NAMES:
            return Finding(
                constraint_id="lane_count_edit_unsupported",
                tier="refuse",
                phase="preflight",
                messages=_render("lane_count_edit_unsupported", param=key),
                suggested_fix={
                    "hint": "check whether the extra lane already exists but is class-restricted "
                    "(opening it is a safe attribute change); otherwise this requires "
                    "delete-and-rebuild of the link"
                },
                details={"param": key},
            )
    return None


register(
    Constraint(
        id="lane_count_edit_unsupported",
        applies_to=["*"],
        phase="preflight",
        tier="refuse",
        check=_check_lane_count_edit_unsupported,
        message_ko="'{param}' 파라미터로 차로 수를 직접 편집하는 기능은 지원되지 않습니다. Vissim은 "
        "링크 생성 시점에 차로 폭 배열로 차로 수를 고정하며, 차로를 추가/삭제하는 COM 속성이 "
        "없습니다. 이미 존재하지만 제한된 차로를 여는 것은 안전하지만, 그 외에는 링크를 "
        "삭제 후 재생성해야 합니다.",
        message_en="Directly editing lane count via '{param}' is not supported. Vissim fixes "
        "lane count at link construction (the lane-width array) — there is no COM "
        "add/remove-lane attribute. Opening an already-existing but restricted lane is safe; "
        "anything else requires delete-and-rebuild of the link.",
        message_ja="'{param}' パラメータによる車線数の直接編集はサポートされていません。Vissimは"
        "リンク作成時に車線幅の配列で車線数を固定しており、車線を追加/削除するCOM属性は"
        "ありません。既存だが制限されている車線を開放するのは安全ですが、それ以外はリンクを"
        "削除して再作成する必要があります。",
    )
)


# ---------------------------------------------------------------------------
# #17 speed_limit_edit_ambiguous (refuse, preflight)
#
# Catalog §2 "There is no speed limit": links have no speed attribute; speed
# comes from a desired speed distribution, a desired speed decision, or a
# reduced speed area — three different objects with different scope, and
# picking the wrong one gives plausible but wrong numbers. Defensive,
# tool-agnostic: no current M2 write tool carries a speed-limit-shaped
# param, so this guards a hallucinated/naive tool call.
# ---------------------------------------------------------------------------

_SPEED_LIMIT_PARAM_NAMES = {"speed_limit", "link_speed"}


def _check_speed_limit_edit_ambiguous(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    params = action.get("params", {})
    for key in params:
        if str(key).lower() in _SPEED_LIMIT_PARAM_NAMES:
            return Finding(
                constraint_id="speed_limit_edit_ambiguous",
                tier="refuse",
                phase="preflight",
                messages=_render("speed_limit_edit_ambiguous", param=key),
                suggested_fix={
                    "hint": "resolve with the user which of composition / desired speed decision "
                    "/ reduced speed area is meant before writing anything"
                },
                details={"param": key},
            )
    return None


register(
    Constraint(
        id="speed_limit_edit_ambiguous",
        applies_to=["*"],
        phase="preflight",
        tier="refuse",
        check=_check_speed_limit_edit_ambiguous,
        message_ko="'{param}' 파라미터로 속도를 직접 편집할 수 없습니다. Vissim 링크에는 속도 "
        "속성이 없습니다 — 속도는 (1)조성의 희망속도분포, (2)희망속도결정, (3)감속구간 중 "
        "하나에서 나오며 셋은 범위가 다릅니다. 어느 것을 의미하는지 사용자와 확인하기 전에는 "
        "쓰기를 거부합니다.",
        message_en="Cannot edit speed directly via '{param}'. Vissim links have no speed "
        "attribute — speed comes from (1) the composition's desired speed distribution, "
        "(2) a desired speed decision, or (3) a reduced speed area, and the three have "
        "different scope. Refusing until it's resolved with the user which one is meant.",
        message_ja="'{param}' パラメータで速度を直接編集することはできません。Vissimのリンクには"
        "速度属性がありません — 速度は(1)構成の希望速度分布、(2)希望速度決定、(3)減速区間の"
        "いずれかから決まり、3つは適用範囲が異なります。どれを意味するかユーザーと確認するまで"
        "書き込みを拒否します。",
    )
)


# =============================================================================
# Batch 2/4: conflict-area / intergreen semantics, per docs/phase2/
# 02-ROADMAP.md M3.1's priority order (b).
# =============================================================================


# ---------------------------------------------------------------------------
# #18 conflict_area_data_unavailable (warn, postwrite)
#
# Catalog §1 "Conflict areas default to inactive" + §3 "Conflict area
# numbers are not stable identity": conflict areas are geometry-derived, have
# no COM add/remove, default to Passive right-of-way, and are destroyed
# silently by any geometry edit.
#
# GAP CLOSED in v2 unit ④ (2026-07-24): tools_network.build_shadow_model now
# populates shadow_model["conflict_areas"] for real (parsed straight from the
# .inpx -- verified live against Vissim 2026 via
# backend/tools/conflict_area_probe.py: the XML DOES serialize conflict
# areas, and the COM AttValue ids match the XML attribute names exactly). The
# defensive ``shadow_model.get("conflict_areas")`` read below is UNCHANGED
# (still correct and still needed -- the demo network's .inpx carries zero
# conflictArea elements, so this check must keep degrading silently to an
# empty list there, and any shadow model built by an older/synthetic fixture
# without the key at all must keep degrading too), but this check now fires
# for real, live, on any real network with Passive conflict areas -- it is no
# longer a forward-declared no-op.
# ---------------------------------------------------------------------------


def _check_conflict_area_data_unavailable(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    # .get(...) stays defensive on purpose: real networks populate this for
    # real now (v2 unit ④), but the demo network legitimately has zero
    # conflict areas (empty list -- falsy, same silent-skip path), and older
    # synthetic fixtures may omit the key entirely.
    conflict_areas = shadow_model.get("conflict_areas")
    if not conflict_areas:
        return None

    passive = [
        ca
        for ca in conflict_areas
        if str(ca.get("status", "")).strip().lower() == "passive"
    ]
    if not passive:
        return None

    link_pairs = [
        "-".join(str(x) for x in ca.get("link_pair", ("?", "?"))) for ca in passive
    ]
    # Capped for the human-facing message (see _capped_join); `details`
    # below keeps the full, uncapped list so traces/eval lose nothing.
    pairs_ko, pairs_en, pairs_ja = _capped_join(link_pairs, "쌍", "pairs", "件")
    return Finding(
        constraint_id="conflict_area_data_unavailable",
        tier="warn",
        phase="postwrite",
        messages=_render(
            "conflict_area_data_unavailable",
            count=len(passive),
            pairs_ko=pairs_ko,
            pairs_en=pairs_en,
            pairs_ja=pairs_ja,
        ),
        suggested_fix=None,
        details={"passive_count": len(passive), "link_pairs": link_pairs},
    )


register(
    Constraint(
        id="conflict_area_data_unavailable",
        applies_to=["*"],
        phase="postwrite",
        tier="warn",
        check=_check_conflict_area_data_unavailable,
        message_ko="충돌구간(conflict area) {count}개가 Passive(우선권 없음) 상태입니다: {pairs_ko}. "
        "Vissim은 이를 COM으로 추가/삭제할 수 없고, 지오메트리 편집 시 설정이 조용히 사라집니다 — "
        "번호가 아니라 링크 쌍으로 스냅샷하고, Passive 개수가 늘지 않는지 회귀 지표로 추적하세요.",
        message_en="{count} conflict area(s) are Passive (no right-of-way control): {pairs_en}. "
        "Vissim has no COM add/remove for these, and their configuration is silently lost on "
        "any geometry edit — snapshot by link pair (never by number), and track the count of "
        "Passive conflict areas as a regression metric.",
        message_ja="{count}件の錯綜区間(conflict area)がPassive(優先権制御なし)状態です: {pairs_ja}。"
        "VissimはこれらをCOM経由で追加/削除できず、ジオメトリ編集時に設定が静かに失われます — "
        "番号ではなくリンクペアでスナップショットし、Passiveの件数が増えないことを回帰指標として"
        "追跡してください。",
    )
)


# ---------------------------------------------------------------------------
# #new conflict_area_status_change_confirm (warn, preflight) — v2 unit ④
#
# Not a §-numbered catalog entry; a defensive addition specific to
# set_conflict_area_status motivated directly by SS3.2's identity-instability
# landmine: "conflict area NUMBERS ARE NOT STABLE IDENTITY". A number alone
# in a tool call is not enough for a human approver to know which junction is
# about to change — this ALWAYS fires (whenever the target resolves; a
# missing target is object_not_found's job, #1) so the link pair and the
# CURRENT status are unconditionally echoed into the approval card's warning
# list (orchestrator.py's pending_approval["warnings"] / the rendered
# approval-request answer), regardless of whether the planner's own free-text
# summary happens to mention them. Deliberately warn-tier, not refuse: this
# is a confirmation aid, not a problem with the write itself.
# ---------------------------------------------------------------------------


def _check_conflict_area_status_change_confirm(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_conflict_area_status":
        return None

    params = action.get("params", {})
    no = str(params.get("conflict_area_no", ""))
    new_status = params.get("status", "")

    conflict_area = _find_by_field(shadow_model.get("conflict_areas", []), "no", no)
    if conflict_area is None:
        return None  # missing target is object_not_found's job (#1)

    link_a = conflict_area.get("link_a", "?")
    link_b = conflict_area.get("link_b", "?")
    old_status = conflict_area.get("status", "?")

    return Finding(
        constraint_id="conflict_area_status_change_confirm",
        tier="warn",
        phase="preflight",
        messages=_render(
            "conflict_area_status_change_confirm",
            no=no,
            link_a=link_a,
            link_b=link_b,
            old_status=old_status,
            new_status=new_status,
        ),
        suggested_fix=None,
        details={
            "no": no,
            "link_a": link_a,
            "link_b": link_b,
            "old_status": old_status,
            "new_status": new_status,
        },
    )


register(
    Constraint(
        id="conflict_area_status_change_confirm",
        applies_to=["set_conflict_area_status"],
        phase="preflight",
        tier="warn",
        check=_check_conflict_area_status_change_confirm,
        message_ko="충돌구간 '{no}'은(는) 링크 {link_a}와(과) 링크 {link_b} 사이에 있으며, 현재 상태는 "
        "{old_status}입니다. {new_status}(으)로 변경을 제안합니다 — 충돌구간 번호는 지오메트리 편집 "
        "시 바뀔 수 있으므로, 승인 전에 의도한 교차로가 맞는지 확인해 주세요.",
        message_en="Conflict area '{no}' sits between link {link_a} and link {link_b}, currently "
        "status {old_status}. Proposing to change it to {new_status} — conflict area numbers are "
        "not stable identity across geometry edits, so confirm this is the intended junction "
        "before approving.",
        message_ja="錯綜区間 '{no}' はリンク{link_a}とリンク{link_b}の間にあり、現在の状態は"
        "{old_status}です。{new_status}への変更を提案します — 錯綜区間の番号はジオメトリ編集で"
        "変わる可能性があるため、承認前に意図した交差点であることをご確認ください。",
    )
)


# ---------------------------------------------------------------------------
# #19 conflicting_greens_in_signal_plan (warn, preflight)
#
# Catalog §1 "Conflicting greens are allowed, and score well" — THE single
# most dangerous entry in the catalog: intergreen matrices are checked and
# highlighted but never enforced by Vissim, and a plan that gives two
# conflicting movements green simultaneously simulates fine (vehicles pass
# through each other) and the reported delay IMPROVES. This is a REAL, fully
# live check today: it reads the target signal's own "phases" (already in
# the shadow-model contract via signal_parser.py — start/duration/movements)
# and flags any two phases with an OVERLAPPING green window whose movements
# span perpendicular approach axes (north/south vs east/west) — a hard,
# geometry-certain conflict, not a permissive-movement judgement call.
# Deliberately conservative: only "straight"/"left" movements are compared
# (permitted concurrent right turns are common practice and would just be
# noise), and same-axis overlaps (e.g. opposing N/S through) are never
# flagged.
# ---------------------------------------------------------------------------

_DIRECTION_AXIS: dict[str, str] = {
    "north": "NS",
    "south": "NS",
    "east": "EW",
    "west": "EW",
}
_CONFLICT_RELEVANT_TURNS = {"straight", "left"}


def _phase_axes(phase: dict[str, Any]) -> set[str]:
    axes: set[str] = set()
    for movement in phase.get("movements", []) or []:
        if str(movement.get("turn", "")).lower() not in _CONFLICT_RELEVANT_TURNS:
            continue
        axis = _DIRECTION_AXIS.get(str(movement.get("from_direction", "")).lower())
        if axis:
            axes.add(axis)
    return axes


def _windows_overlap(a_start: float, a_dur: float, b_start: float, b_dur: float) -> bool:
    a_end = a_start + a_dur
    b_end = b_start + b_dur
    return a_start < b_end and b_start < a_end


def _check_conflicting_greens_in_signal_plan(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") not in ("set_signal_cycle", "set_signal_offset"):
        return None

    params = action.get("params", {})
    sc_no = str(params.get("sc_no", ""))
    signal = _find_by_field(shadow_model.get("signals", []), "sc_no", sc_no)
    if signal is None:
        return None  # missing target is object_not_found's job (#1)

    phases = signal.get("phases") or []
    if len(phases) < 2:
        return None

    for i in range(len(phases)):
        phase_a = phases[i]
        start_a = _coerce_number(phase_a.get("start"))
        dur_a = _coerce_number(phase_a.get("duration"))
        if start_a is None or dur_a is None:
            continue
        axes_a = _phase_axes(phase_a)
        if not axes_a:
            continue
        for j in range(i + 1, len(phases)):
            phase_b = phases[j]
            start_b = _coerce_number(phase_b.get("start"))
            dur_b = _coerce_number(phase_b.get("duration"))
            if start_b is None or dur_b is None:
                continue
            if not _windows_overlap(start_a, dur_a, start_b, dur_b):
                continue
            axes_b = _phase_axes(phase_b)
            if axes_a & axes_b:
                continue  # same axis only (e.g. opposing through) — not flagged
            if not axes_b:
                continue
            return Finding(
                constraint_id="conflicting_greens_in_signal_plan",
                tier="warn",
                phase="preflight",
                messages=_render(
                    "conflicting_greens_in_signal_plan",
                    sc_no=sc_no,
                    start_a=phase_a.get("start"),
                    start_b=phase_b.get("start"),
                ),
                suggested_fix=None,
                details={
                    "sc_no": sc_no,
                    "phase_a_start": start_a,
                    "phase_b_start": start_b,
                    "axes": sorted(axes_a | axes_b),
                },
            )
    return None


register(
    Constraint(
        id="conflicting_greens_in_signal_plan",
        applies_to=["set_signal_cycle", "set_signal_offset"],
        phase="preflight",
        tier="warn",
        check=_check_conflicting_greens_in_signal_plan,
        message_ko="신호제어기 '{sc_no}'의 현재 페이즈 플랜에서 시작 {start_a}초 페이즈와 시작 "
        "{start_b}초 페이즈가 겹치며, 서로 직각인 접근로(교차하는 진행 경로)에 동시에 녹색을 "
        "주고 있습니다. Vissim은 인터그린 매트릭스를 강제하지 않으므로 이 상태로도 시뮬레이션이 "
        "실행되고 지연시간이 오히려 개선된 것처럼 보일 수 있습니다 — 편집 전에 인터그린 매트릭스로 "
        "검증하세요.",
        message_en="Signal controller '{sc_no}''s current phase plan has an overlap between the "
        "phase starting at {start_a}s and the one starting at {start_b}s, giving simultaneous "
        "green to perpendicular (crossing) approaches. Vissim never enforces the intergreen "
        "matrix, so this will simulate fine and reported delay can look BETTER — validate "
        "against the intergreen matrix before editing further.",
        message_ja="信号制御機 '{sc_no}' の現在のフェーズプランでは、開始{start_a}秒のフェーズと"
        "開始{start_b}秒のフェーズが重なっており、互いに直交する(交差する)進行方向に同時に青を"
        "与えています。Vissimはインターグリーンマトリックスを強制しないため、このままシミュレー"
        "ションが実行され、遅延時間がむしろ改善したように見える可能性があります — 編集前に"
        "インターグリーンマトリックスで検証してください。",
    )
)


# ---------------------------------------------------------------------------
# #20 ring_barrier_splits_sum_mismatch (refuse, preflight)
#
# Catalog §4 "Splits must sum to the cycle, and include the clearance":
# ring-barrier splits must add up to the cycle length; lengthening one
# without shortening another is arithmetically invalid. CATALOG-GAP:
# signal_parser.py exposes cycle_time/phases but never a per-controller
# ring-barrier split list, so this reads the shadow-model signal entry's
# optional "ring_barrier_splits" field (same forward-ready pattern #5's
# min_green_intergreen_sum still follows; #4's controller_type gap closed in
# v2 unit ①); ring_barrier_splits itself remains always absent today.
# ---------------------------------------------------------------------------

_SPLIT_SUM_TOLERANCE_S = 0.5


def _check_ring_barrier_splits_sum_mismatch(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_signal_cycle":
        return None

    params = action.get("params", {})
    sc_no = str(params.get("sc_no", ""))
    raw_new_cycle = params.get("cycle")
    if raw_new_cycle is None:
        return None
    new_cycle = _coerce_number(raw_new_cycle)
    if new_cycle is None:
        return None

    signal = _find_by_field(shadow_model.get("signals", []), "sc_no", sc_no)
    if signal is None:
        return None  # missing target is object_not_found's job (#1)

    raw_splits = signal.get("ring_barrier_splits")  # CATALOG-GAP: never populated today
    if not raw_splits:
        return None

    splits: list[float] = []
    for raw in raw_splits:
        value = _coerce_number(raw)
        if value is None:
            return None  # malformed data — don't guess
        splits.append(value)

    split_sum = sum(splits)
    if abs(split_sum - new_cycle) <= _SPLIT_SUM_TOLERANCE_S:
        return None

    return Finding(
        constraint_id="ring_barrier_splits_sum_mismatch",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "ring_barrier_splits_sum_mismatch", sc_no=sc_no, cycle=raw_new_cycle, split_sum=split_sum
        ),
        suggested_fix={"split_sum": split_sum},
        details={"sc_no": sc_no, "cycle": new_cycle, "split_sum": split_sum, "splits": splits},
    )


register(
    Constraint(
        id="ring_barrier_splits_sum_mismatch",
        applies_to=["set_signal_cycle"],
        phase="preflight",
        tier="refuse",
        check=_check_ring_barrier_splits_sum_mismatch,
        message_ko="신호제어기 '{sc_no}'의 링-바리어 스플릿 합계는 {split_sum}초인데, 제안된 주기는 "
        "{cycle}초입니다. 각 링의 스플릿(녹색+황색+전적색 포함) 합은 주기와 반드시 같아야 하며, "
        "하나만 늘리고 다른 것을 줄이지 않으면 산술적으로 성립하지 않습니다.",
        message_en="Signal controller '{sc_no}''s ring-barrier splits sum to {split_sum}s, but "
        "the proposed cycle is {cycle}s. Each ring's splits (green + yellow + all-red clearance) "
        "must sum exactly to the cycle — lengthening one without shortening another is "
        "arithmetically invalid.",
        message_ja="信号制御機 '{sc_no}' のリングバリアスプリットの合計は{split_sum}秒ですが、提案"
        "されたサイクルは{cycle}秒です。各リングのスプリット(青+黄+全赤クリアランスを含む)の合計は"
        "サイクルと必ず一致する必要があり、一方だけを延ばして他方を短縮しないと算術的に成立しません。",
    )
)


# =============================================================================
# Batch 3/4: post-run lie detectors, per docs/phase2/02-ROADMAP.md M3.1's
# priority order (c) — latent demand residue, gridlock-LOS-A /
# zero-throughput-good-LOS, plus the rest of catalog §5/§6/§7's postrun
# items. ``guardrails.GuardrailEngine.postrun`` always calls
# ``run_checks("postrun", action, {}, {"run_result": run_result})`` — the
# shadow_model argument is ALWAYS ``{}`` for this phase, so every check below
# reads exclusively from ``extra["run_result"]``, never shadow_model.
# =============================================================================


# ---------------------------------------------------------------------------
# #21 latent_demand_residue_after_run (warn, postrun)
#
# Catalog §1 "Demand that never enters the network": excess demand queues
# OUTSIDE the network as latent demand rather than erroring — the postrun
# companion to #3 latent_demand_overflow's preflight rough-capacity warning.
# CATALOG-GAP: tools_run.py's run_result is ``{"tables", "deleted_vehicles"}``
# only today (no network-wide latent-demand measure); reads
# run_result["latent_demand_vehicles"] defensively.
# ---------------------------------------------------------------------------


def _check_latent_demand_residue_after_run(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    raw_latent = run_result.get("latent_demand_vehicles")  # CATALOG-GAP: not in run_result today
    if raw_latent is None:
        return None
    latent = _coerce_number(raw_latent)
    if latent is None or latent <= 0:
        return None

    return Finding(
        constraint_id="latent_demand_residue_after_run",
        tier="warn",
        phase="postrun",
        messages=_render("latent_demand_residue_after_run", latent_demand_vehicles=raw_latent),
        suggested_fix=None,
        details={"latent_demand_vehicles": latent},
    )


register(
    Constraint(
        id="latent_demand_residue_after_run",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_latent_demand_residue_after_run,
        message_ko="이번 실행 후 차량 {latent_demand_vehicles}대 분량의 잠재 수요(latent demand)가 "
        "네트워크 밖에서 대기 중입니다. 요청한 시나리오가 실제로는 완전히 발생하지 않은 것이므로, "
        "이 비교는 무효일 수 있습니다.",
        message_en="After this run, {latent_demand_vehicles} vehicle(s) worth of latent demand "
        "is still queued OUTSIDE the network. The requested scenario did not fully occur — this "
        "comparison may be invalid.",
        message_ja="今回の実行後、車両{latent_demand_vehicles}台分の潜在需要(latent demand)が"
        "ネットワークの外で待機しています。要求されたシナリオが実際には完全に発生しなかったため、"
        "この比較は無効な可能性があります。",
    )
)


# ---------------------------------------------------------------------------
# #22 gridlock_reports_los_a (warn, postrun)
#
# Catalog §1 "A gridlocked movement can report the best possible grade" —
# the "zero-throughput-good-LOS" lie detector: LOS grade A covers BOTH "no
# delay" and "no volume, as no vehicle is moving, due to a traffic jam."
# CATALOG-GAP: run_result carries no per-movement LOS/volume breakdown today
# (only {"tables", "deleted_vehicles"}); reads run_result["los_by_movement"]
# defensively ([{"movement", "los", "volume"}, ...]).
# ---------------------------------------------------------------------------


def _check_gridlock_reports_los_a(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    los_by_movement = run_result.get("los_by_movement")  # CATALOG-GAP: not in run_result today
    if not los_by_movement:
        return None

    offenders = [
        str(m.get("movement", "?"))
        for m in los_by_movement
        if str(m.get("los", "")).strip().upper() == "A"
        and (_coerce_number(m.get("volume")) or 0) <= 0
    ]
    if not offenders:
        return None

    return Finding(
        constraint_id="gridlock_reports_los_a",
        tier="warn",
        phase="postrun",
        messages=_render("gridlock_reports_los_a", movements=", ".join(offenders)),
        suggested_fix=None,
        details={"movements": offenders},
    )


register(
    Constraint(
        id="gridlock_reports_los_a",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_gridlock_reports_los_a,
        message_ko="움직임 {movements}이(가) 통행량 0에도 불구하고 최고 등급인 LOS A로 보고되었습니다. "
        "Vissim의 LOS A는 '지연 없음'과 '교통 정체로 차량이 전혀 움직이지 못함'을 구분하지 않습니다 — "
        "통행량 없이 LOS 등급만 보고하지 마세요.",
        message_en="Movement(s) {movements} reported the best grade, LOS A, despite zero "
        "throughput. Vissim's LOS A covers both 'no delay' and 'no volume, because gridlock "
        "means nothing is moving' — never report a LOS letter without the throughput next to it.",
        message_ja="移動 {movements} は通行量0であるにもかかわらず、最高評価のLOS Aとして報告され"
        "ました。VissimのLOS Aは「遅延なし」と「渋滞により車両が全く動けない」を区別しません — "
        "通行量なしでLOS等級のみを報告しないでください。",
    )
)


# ---------------------------------------------------------------------------
# #23 evaluation_warmup_start_at_zero (warn, postrun)
#
# Catalog §5 "Warm-up is not a setting — it's a field on every evaluation":
# the ideal postrun check (vs #10 sim_duration_too_short_for_evaluation's
# proactive preflight companion) — reads the ACTUAL per-evaluation start
# time an armed run used. CATALOG-GAP: run_result carries no per-evaluation
# warm-up data today; reads run_result["evaluation_warmup_starts"]
# defensively (``{eval_name: start_seconds}``).
# ---------------------------------------------------------------------------


def _check_evaluation_warmup_start_at_zero(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    starts = run_result.get("evaluation_warmup_starts")  # CATALOG-GAP: not in run_result today
    if not starts:
        return None

    zero_start = [
        name for name, start in starts.items() if (_coerce_number(start) or 0) <= 0
    ]
    if not zero_start:
        return None

    return Finding(
        constraint_id="evaluation_warmup_start_at_zero",
        tier="warn",
        phase="postrun",
        messages=_render("evaluation_warmup_start_at_zero", evaluations=", ".join(sorted(zero_start))),
        suggested_fix=None,
        details={"zero_start_evaluations": zero_start},
    )


register(
    Constraint(
        id="evaluation_warmup_start_at_zero",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_evaluation_warmup_start_at_zero,
        message_ko="평가 {evaluations}의 시작 시각이 0초(워밍업 미적용)로 설정되어 있습니다. "
        "Vissim은 빈 네트워크에서 시작하므로 초반 결과는 의미가 없습니다 — 개별 평가마다 시작 "
        "시각을 확인하세요.",
        message_en="Evaluation(s) {evaluations} have a start time of 0s (no warm-up excluded). "
        "Vissim starts from an empty network, so early results are meaningless — check the "
        "start time on every evaluation individually.",
        message_ja="評価 {evaluations} の開始時刻が0秒(ウォームアップ未適用)に設定されています。"
        "Vissimは空のネットワークから開始するため、序盤の結果には意味がありません — 個々の評価の"
        "開始時刻を確認してください。",
    )
)


# ---------------------------------------------------------------------------
# #24 queue_length_definition_not_portable (warn, postrun)
#
# Catalog §5 "'Queue length' is not a portable number": what counts as
# queued depends on configurable speed thresholds and counter placement.
# CATALOG-GAP: run_result carries no queue-definition provenance today;
# fires only when queue KPI numbers ARE present (run_result["queue_results"])
# but their defining parameters are not (run_result["queue_definition"]) —
# exactly the risk the catalog names ("attach the definition to every queue
# answer").
# ---------------------------------------------------------------------------


def _check_queue_length_definition_not_portable(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    queue_results = run_result.get("queue_results")  # CATALOG-GAP: not in run_result today
    if not queue_results:
        return None
    if run_result.get("queue_definition"):
        return None

    return Finding(
        constraint_id="queue_length_definition_not_portable",
        tier="warn",
        phase="postrun",
        messages=_render("queue_length_definition_not_portable"),
        suggested_fix=None,
        details={"queue_locations": sorted(queue_results.keys()) if hasattr(queue_results, "keys") else None},
    )


register(
    Constraint(
        id="queue_length_definition_not_portable",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_queue_length_definition_not_portable,
        message_ko="대기행렬(queue) 값이 보고되었지만 그 정의(속도 임계값, 최대 간격, 카운터 위치)가 "
        "함께 제공되지 않았습니다. 정의가 다른 모델끼리는 대기행렬 값을 비교할 수 없습니다.",
        message_en="Queue length values were reported without their defining parameters (speed "
        "threshold, max gap, counter location) attached. Queue numbers from differently "
        "configured models are not comparable.",
        message_ja="待ち行列(queue)の値が報告されましたが、その定義(速度閾値、最大ギャップ、"
        "カウンター位置)が添付されていません。設定が異なるモデル間で待ち行列の値を比較することは"
        "できません。",
    )
)


# ---------------------------------------------------------------------------
# #25 delay_measurement_truncated_by_queue (warn, postrun)
#
# Catalog §5 "Delay is only counted from a fixed distance upstream" (default
# 100m): when a queue stretches further back than the delay-segment start,
# the excess delay simply isn't counted. CATALOG-GAP: run_result carries no
# per-node delay-segment/queue-length pairing today; reads
# run_result["delay_segments"] defensively
# ([{"node", "segment_length_m", "observed_queue_m"}, ...]).
# ---------------------------------------------------------------------------


def _check_delay_measurement_truncated_by_queue(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    segments = run_result.get("delay_segments")  # CATALOG-GAP: not in run_result today
    if not segments:
        return None

    offenders = [
        str(seg.get("node", "?"))
        for seg in segments
        if (_coerce_number(seg.get("observed_queue_m")) or 0)
        > (_coerce_number(seg.get("segment_length_m")) or 0)
    ]
    if not offenders:
        return None

    return Finding(
        constraint_id="delay_measurement_truncated_by_queue",
        tier="warn",
        phase="postrun",
        messages=_render("delay_measurement_truncated_by_queue", nodes=", ".join(offenders)),
        suggested_fix=None,
        details={"nodes": offenders},
    )


register(
    Constraint(
        id="delay_measurement_truncated_by_queue",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_delay_measurement_truncated_by_queue,
        message_ko="노드 {nodes}에서 관측된 대기열 길이가 지연 측정 구간(기본 100m)을 초과합니다. "
        "구간 밖에서 발생한 초과 지연은 집계되지 않으므로, 가장 혼잡한 교차로일수록 지연시간이 "
        "과소평가됩니다.",
        message_en="At node(s) {nodes}, the observed queue length exceeds the delay-measurement "
        "segment (default 100m). Delay beyond that segment simply isn't counted, so the most "
        "congested intersections are the ones whose delay is most under-reported.",
        message_ja="ノード {nodes} で観測された待ち行列の長さが遅延計測区間(デフォルト100m)を"
        "超えています。区間外で発生した超過分の遅延は集計されないため、最も混雑した交差点ほど"
        "遅延時間が過小評価されます。",
    )
)


# ---------------------------------------------------------------------------
# #26 evaluation_interval_multiple_of_cycle (warn, postrun)
#
# Catalog §5 "The averaging interval changes the answer" (Washington State
# subtlety): an averaging interval that's an exact multiple of the signal
# cycle length samples the same point in the cycle every time. CATALOG-GAP:
# run_result carries neither today; reads run_result["evaluation_interval_s"]
# and run_result["signal_cycle_s"] defensively.
# ---------------------------------------------------------------------------


def _check_evaluation_interval_multiple_of_cycle(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    raw_interval = run_result.get("evaluation_interval_s")  # CATALOG-GAP: not in run_result today
    raw_cycle = run_result.get("signal_cycle_s")  # CATALOG-GAP: not in run_result today
    if raw_interval is None or raw_cycle is None:
        return None
    interval = _coerce_number(raw_interval)
    cycle = _coerce_number(raw_cycle)
    if interval is None or cycle is None or cycle <= 0:
        return None

    if interval % cycle > _FLOAT_TOLERANCE:
        return None

    return Finding(
        constraint_id="evaluation_interval_multiple_of_cycle",
        tier="warn",
        phase="postrun",
        messages=_render(
            "evaluation_interval_multiple_of_cycle", interval=raw_interval, cycle=raw_cycle
        ),
        suggested_fix=None,
        details={"interval_s": interval, "cycle_s": cycle},
    )


register(
    Constraint(
        id="evaluation_interval_multiple_of_cycle",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_evaluation_interval_multiple_of_cycle,
        message_ko="평가 구간({interval}초)이 신호 주기({cycle}초)의 정수배입니다. 이 경우 매 구간 "
        "주기 내 동일한 지점만 표본으로 수집하게 되어 결과가 편향될 수 있습니다.",
        message_en="The evaluation interval ({interval}s) is an exact multiple of the signal "
        "cycle ({cycle}s). This risks sampling the same point in the cycle every time, biasing "
        "the result.",
        message_ja="評価区間({interval}秒)が信号サイクル({cycle}秒)の整数倍になっています。この"
        "場合、毎回サイクル内の同じ地点だけをサンプリングすることになり、結果に偏りが生じる可能性が"
        "あります。",
    )
)


# ---------------------------------------------------------------------------
# #27 vissim_version_unpinned (warn, postrun)
#
# Catalog §6 "A version upgrade alone can change results": lane-change,
# merging and conflict-area handling changed in the 2024 release; a
# calibration can break without anyone editing anything. CATALOG-GAP:
# tools_run.py's run_result does not surface SIMULATIONRUN's VISSIMVER
# column today; reads run_result["vissim_version"] defensively against the
# pinned deployment version (docs/phase2/00-EXECUTION-GUIDE.md: "Vissim 2026
# is installed... ProgID VISSIM.Vissim.2026").
# ---------------------------------------------------------------------------

_EXPECTED_VISSIM_VERSION = "2026"


def _check_vissim_version_unpinned(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    raw_version = run_result.get("vissim_version")  # CATALOG-GAP: not in run_result today
    if not raw_version:
        return None
    if str(raw_version) == _EXPECTED_VISSIM_VERSION:
        return None

    return Finding(
        constraint_id="vissim_version_unpinned",
        tier="warn",
        phase="postrun",
        messages=_render(
            "vissim_version_unpinned", actual=raw_version, expected=_EXPECTED_VISSIM_VERSION
        ),
        suggested_fix={"expected_version": _EXPECTED_VISSIM_VERSION},
        details={"actual_version": str(raw_version)},
    )


register(
    Constraint(
        id="vissim_version_unpinned",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_vissim_version_unpinned,
        message_ko="이번 실행은 Vissim {actual} 버전으로 생성되었지만, 배포 환경에 고정된 버전은 "
        "{expected}입니다. 버전 업그레이드만으로도 차선변경/합류/충돌구간 처리 방식이 바뀌어 결과가 "
        "달라질 수 있으므로 재보정이 필요할 수 있습니다.",
        message_en="This run was produced with Vissim {actual}, but the pinned deployment "
        "version is {expected}. A version upgrade alone can change lane-change/merging/"
        "conflict-area handling and results — recalibration may be needed.",
        message_ja="今回の実行はVissim {actual} で生成されましたが、デプロイ環境に固定されている"
        "バージョンは{expected}です。バージョンアップだけで車線変更/合流/錯綜区間の処理方法が変わり"
        "結果が変化する可能性があるため、再キャリブレーションが必要な場合があります。",
    )
)


# ---------------------------------------------------------------------------
# #28 error_log_overwritten_between_runs (warn, postrun)
#
# Catalog §7 "The error file is overwritten between runs": with a single run
# configured, each new simulation overwrites the previous .err file — the
# record of removed vehicles and unmet demand from the last run disappears.
# CATALOG-GAP: the job runner does not archive/report .err status in
# run_result today; reads run_result["error_log_archived"] defensively.
# ---------------------------------------------------------------------------


def _check_error_log_overwritten_between_runs(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    if "error_log_archived" not in run_result:  # CATALOG-GAP: not in run_result today
        return None
    if run_result.get("error_log_archived"):
        return None

    return Finding(
        constraint_id="error_log_overwritten_between_runs",
        tier="warn",
        phase="postrun",
        messages=_render("error_log_overwritten_between_runs"),
        suggested_fix=None,
        details={},
    )


register(
    Constraint(
        id="error_log_overwritten_between_runs",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_error_log_overwritten_between_runs,
        message_ko="이번 실행 전에 이전 .err 로그가 보존(아카이브)되지 않았습니다. 단일 실행 구성에서 "
        "새 시뮬레이션은 이전 오류 로그(제거된 차량, 미충족 수요 기록)를 덮어쓰므로 이전 실행의 "
        "기록은 복구할 수 없습니다.",
        message_en="The previous .err log was not archived before this run started. With a "
        "single-run configuration, a new simulation overwrites the previous error log (removed "
        "vehicles, unmet demand) — the prior run's record cannot be recovered.",
        message_ja="今回の実行前に、以前の.errログがアーカイブ(保存)されていませんでした。単一実行"
        "構成では、新しいシミュレーションが以前のエラーログ(削除された車両、未充足需要の記録)を"
        "上書きするため、前回実行の記録は復元できません。",
    )
)


# ---------------------------------------------------------------------------
# #29 simulation_resolution_unpinned_across_comparison (warn, postrun)
#
# Catalog §7 "Simulation resolution changes results, not just runtime":
# PTV states different resolutions produce different results; values below 5
# steps/second are explicitly unsuitable for final results. CATALOG-GAP:
# RunSimulationParams has no resolution field and run_result doesn't surface
# it today; reads run_result["simulation_resolution"] defensively.
# ---------------------------------------------------------------------------

_MIN_RELIABLE_RESOLUTION_STEPS_PER_S = 5


def _check_simulation_resolution_unpinned_across_comparison(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    raw_resolution = run_result.get("simulation_resolution")  # CATALOG-GAP: not in run_result today
    if raw_resolution is None:
        return None
    resolution = _coerce_number(raw_resolution)
    if resolution is None:
        return None
    if resolution >= _MIN_RELIABLE_RESOLUTION_STEPS_PER_S:
        return None

    return Finding(
        constraint_id="simulation_resolution_unpinned_across_comparison",
        tier="warn",
        phase="postrun",
        messages=_render(
            "simulation_resolution_unpinned_across_comparison",
            resolution=raw_resolution,
            min_resolution=_MIN_RELIABLE_RESOLUTION_STEPS_PER_S,
        ),
        suggested_fix={"min_resolution_steps_per_s": _MIN_RELIABLE_RESOLUTION_STEPS_PER_S},
        details={"resolution": resolution},
    )


register(
    Constraint(
        id="simulation_resolution_unpinned_across_comparison",
        applies_to=["*"],
        phase="postrun",
        tier="warn",
        check=_check_simulation_resolution_unpinned_across_comparison,
        message_ko="이번 실행의 시뮬레이션 해상도는 초당 {resolution} 스텝입니다. PTV는 해상도가 "
        "다르면 결과도 달라진다고 명시하며, 초당 {min_resolution} 스텝 미만은 최종 결과로 "
        "적합하지 않습니다. 비교할 시나리오 전체에서 해상도를 고정하세요.",
        message_en="This run's simulation resolution is {resolution} steps/second. PTV states "
        "different resolutions produce different results, and below {min_resolution} steps/sec "
        "is explicitly unsuitable for final results. Lock resolution across any scenarios "
        "intended for comparison.",
        message_ja="今回の実行のシミュレーション解像度は毎秒{resolution}ステップです。PTVは解像度が"
        "異なれば結果も異なると明記しており、毎秒{min_resolution}ステップ未満は最終結果として"
        "不適切です。比較対象となるシナリオ全体で解像度を固定してください。",
    )
)


# =============================================================================
# Batch 4/4: the remainder of the catalog, per docs/phase2/02-ROADMAP.md
# M3.1's priority order (d). Several of these guard tool verbs that do not
# exist in the M2 registry yet (delete_object, add_evaluation, ...) --
# applies_to names the FUTURE tool explicitly rather than "*", so the check
# is inert in production today (no such action is ever dispatched) while
# still being directly, unconditionally testable by constructing that action
# by hand, per this module's "never fabricate a datum" discipline: these
# refuse/warn unconditionally because the underlying capability (a reference
# graph, a calibration baseline, ...) doesn't exist yet -- not because of a
# missing optional field on existing data.
# =============================================================================


# ---------------------------------------------------------------------------
# #30 speed_decision_unpaired (warn, preflight)
#
# Catalog §2 "A speed decision is permanent unless you undo it": PTV is
# explicit that a temporary speed change needs a SECOND desired speed
# decision immediately after to restore the original speed. No M2 tool
# creates desired speed decisions today; applies_to names the future tool
# explicitly.
#
# v2 unit (2) note: this is exactly why ``set_reduced_speed_area``
# (services/agent/tools_edit.py, XML-injection write) is the SAFE verb for a
# local/bounded speed reduction and does NOT need a twin of this check --
# unlike a desired-speed decision, a reducedSpeedArea is spatially bounded by
# its own pos/length regardless of how long it stays active, so there is no
# unpaired-decision failure mode to warn about. See that tool's docstring.
# ---------------------------------------------------------------------------


def _check_speed_decision_unpaired(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_desired_speed_decision":
        return None

    params = action.get("params", {})
    if params.get("paired_restore_decision_id"):
        return None

    return Finding(
        constraint_id="speed_decision_unpaired",
        tier="warn",
        phase="preflight",
        messages=_render("speed_decision_unpaired"),
        suggested_fix={"hint": "add a second desired speed decision immediately after this one to restore the original speed"},
        details={},
    )


register(
    Constraint(
        id="speed_decision_unpaired",
        applies_to=["add_desired_speed_decision"],
        phase="preflight",
        tier="warn",
        check=_check_speed_decision_unpaired,
        message_ko="이 희망속도결정(desired speed decision)에는 짝을 이루는 복원 결정이 없습니다. "
        "PTV는 일시적 속도 변화를 모델링하려면 원래 속도로 되돌리는 두 번째 결정을 바로 뒤에 "
        "추가해야 한다고 명시합니다 — 없으면 네트워크 나머지 구간 전체가 감속된 채로 남습니다.",
        message_en="This desired speed decision has no paired restore decision. PTV is explicit "
        "that a temporary speed change needs a SECOND decision immediately after to restore the "
        "original speed — without it, the rest of the network stays slowed for the whole trip.",
        message_ja="この希望速度決定(desired speed decision)には対になる復元決定がありません。"
        "PTVは、一時的な速度変化をモデル化するには元の速度に戻す2つ目の決定を直後に追加する必要が"
        "あると明記しています — これがないと、ネットワークの残り区間全体が減速したままになります。",
    )
)


# ---------------------------------------------------------------------------
# #31 route_integrity_unverifiable (warn, postwrite)
#
# Merges catalog §1 "Broken routes stay broken even after you fix the road"
# + "Deleting a route silently bans a turn": both stem from the same root
# cause — routes are stored as fixed link sequences and a banned movement is
# expressed by ABSENCE of a route, not an attribute.
#
# CATALOG-GAP CLOSED in v2 unit ⑥ (2026-07-24):
# tools_network._flatten_routes now populates shadow_model["routes"] for
# real — a flat, id-keyed list where `disconnected` is a GENUINE, computed
# fact (verified live, backend/tools/routing_decision_probe.py): every
# <vehicleRouteStatic> carries a <linkSeq><intObjectRef key="..."/></linkSeq>
# child naming the link/connector ids the route traverses, and every key
# already matched a real services.inpx_parser link id on Beijing — so a
# route is `disconnected` iff any of its own link_seq keys is NOT among the
# network's CURRENT link ids (computed fresh against whichever shadow model
# — before or after — this check is handed, so a link-deleting edit, when
# one ships, would flip this to True for real). No M2/v2 write tool edits
# link/route STRUCTURE today (v2 unit ⑥ only edits relFlow, a plain
# attribute that never changes link_seq), so this stays silent (no
# disconnected routes) on every real network in practice — but it is a live,
# fully real check now, not a permanently-dormant CATALOG-GAP one; it will
# fire the moment a future structural edit tool (V2b's add/remove
# links/connectors) actually breaks a route.
# ---------------------------------------------------------------------------


def _check_route_integrity_unverifiable(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    routes = shadow_model.get("routes")  # CATALOG-GAP: not in the shadow-model contract
    if not routes:
        return None

    disconnected = [str(r.get("id", "?")) for r in routes if r.get("disconnected")]
    if not disconnected:
        return None

    # Capped for the human-facing message (see _capped_join, same fix as
    # conflict_area_data_unavailable) -- disconnected-route count scales with
    # network size just like conflict-area pairs does; `details` below keeps
    # the full, uncapped list so traces/eval lose nothing.
    routes_ko, routes_en, routes_ja = _capped_join(disconnected, "개", "routes", "件")
    return Finding(
        constraint_id="route_integrity_unverifiable",
        tier="warn",
        phase="postwrite",
        messages=_render(
            "route_integrity_unverifiable",
            routes_ko=routes_ko,
            routes_en=routes_en,
            routes_ja=routes_ja,
        ),
        suggested_fix=None,
        details={"disconnected_routes": disconnected},
    )


register(
    Constraint(
        id="route_integrity_unverifiable",
        applies_to=["*"],
        phase="postwrite",
        tier="warn",
        check=_check_route_integrity_unverifiable,
        message_ko="경로 {routes_ko}이(가) 이번 편집 이후 단절되었습니다. Vissim은 링크를 나눈 뒤 새 "
        "링크를 삽입해도 기존 경로를 자동 복구하지 않으며, 마지막 남은 경로를 지우면 그 이동은 "
        "암묵적으로 금지됩니다 — 복구 여부와 이동 금지 여부를 사용자에게 확인받으세요.",
        message_en="Route(s) {routes_en} are disconnected after this edit. Vissim does not "
        "auto-restore a route after splitting/deleting links it ran across (even inserting new "
        "links back doesn't reconnect it), and removing the last route covering a movement "
        "implicitly bans that turn — confirm with the user whether to repair or accept the ban.",
        message_ja="経路 {routes_ja} が今回の編集後に切断されました。Vissimはリンクを分割/削除した後、"
        "新しいリンクを挿入しても既存の経路を自動復元しません。また、ある動線をカバーする最後の"
        "経路を削除すると、その動線は暗黙的に禁止されます — 修復するか禁止を受け入れるかユーザーに"
        "確認してください。",
    )
)


# ---------------------------------------------------------------------------
# #32 stale_route_files_after_edit (warn, postwrite)
#
# Catalog §1 "Stale route files reused without complaint": dynamic-assignment
# models store converged paths in external files; after a network edit those
# files are stale, and Vissim only checks them if "check edges" is enabled.
# CATALOG-GAP: dynamic-assignment status is not part of the shadow-model
# contract; reads shadow_model["dynamic_assignment"] defensively.
#
# NOT touched by v2 unit ⑥: this check concerns DYNAMIC assignment's
# external path/cost files, a completely different Vissim subsystem from the
# STATIC routing decisions (vehicleRoutingDecisionStatic) that unit shipped —
# same catalog section, unrelated root cause and unrelated shadow-model
# field. Stays open/documented, not accidentally "closed" by proximity.
# ---------------------------------------------------------------------------


def _check_stale_route_files_after_edit(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    da = shadow_model.get("dynamic_assignment")  # CATALOG-GAP: not in the shadow-model contract
    if not da:
        return None
    if not da.get("enabled"):
        return None
    if not da.get("paths_stale"):
        return None

    return Finding(
        constraint_id="stale_route_files_after_edit",
        tier="warn",
        phase="postwrite",
        messages=_render("stale_route_files_after_edit"),
        suggested_fix=None,
        details={},
    )


register(
    Constraint(
        id="stale_route_files_after_edit",
        applies_to=["*"],
        phase="postwrite",
        tier="warn",
        check=_check_stale_route_files_after_edit,
        message_ko="이 네트워크는 동적 배정(dynamic assignment)을 사용하며, 이번 편집으로 저장된 "
        "경로/비용 파일이 최신 네트워크와 어긋났습니다. 'check edges' 옵션이 꺼져 있으면 Vissim은 "
        "이를 조용히 무시하고 낡은 경로를 그대로 재사용합니다 — 경로/비용 파일을 재생성하거나 "
        "삭제하세요.",
        message_en="This network uses dynamic assignment, and this edit has left the saved "
        "path/cost files out of sync with the network. If the 'check edges' option is off, "
        "Vissim silently reuses the stale paths without complaint — regenerate or delete the "
        "path/cost files.",
        message_ja="このネットワークは動的配分(dynamic assignment)を使用しており、今回の編集に"
        "より保存された経路/コストファイルが最新のネットワークとずれています。'check edges' "
        "オプションがオフの場合、Vissimはこれを黙って無視し、古い経路をそのまま再利用します — "
        "経路/コストファイルを再生成するか削除してください。",
    )
)


# ---------------------------------------------------------------------------
# #33 driving_behavior_parameter_model_mismatch (refuse, preflight)
#
# Catalog §1 "Writing a parameter the active model ignores": every driving
# behaviour stores parameters for BOTH Wiedemann 74 and 99 regardless of
# which is active; writing a model-99 parameter on a model-74 behaviour
# changes nothing, and nothing reports the no-op. No M2 tool edits driving
# behaviours today; applies_to names the future tool explicitly.
# CATALOG-GAP: reads shadow_model["driving_behaviors"] defensively.
# ---------------------------------------------------------------------------

_MODEL_99_ONLY_PARAMS = {f"cc{n}" for n in range(0, 10)}  # Wiedemann 99 (freeway)
_MODEL_74_ONLY_PARAMS = {"axhze", "bxadd", "bxmult"}  # Wiedemann 74 (urban)


def _check_driving_behavior_parameter_model_mismatch(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None

    params = action.get("params", {})
    behavior_id = str(params.get("behavior_id", ""))
    parameter = str(params.get("parameter", "")).lower()

    behaviors = shadow_model.get("driving_behaviors")  # CATALOG-GAP: not in shadow-model contract
    if not behaviors:
        return None
    behavior = _find_by_field(behaviors, "id", behavior_id)
    if behavior is None:
        return None

    model = behavior.get("car_following_model")
    mismatched = (model == 74 and parameter in _MODEL_99_ONLY_PARAMS) or (
        model == 99 and parameter in _MODEL_74_ONLY_PARAMS
    )
    if not mismatched:
        return None

    return Finding(
        constraint_id="driving_behavior_parameter_model_mismatch",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "driving_behavior_parameter_model_mismatch",
            parameter=parameter,
            behavior_id=behavior_id,
            model=model,
        ),
        suggested_fix=None,
        details={"behavior_id": behavior_id, "parameter": parameter, "car_following_model": model},
    )


register(
    Constraint(
        id="driving_behavior_parameter_model_mismatch",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="refuse",
        check=_check_driving_behavior_parameter_model_mismatch,
        message_ko="'{parameter}'는 Wiedemann 모델 {model}에서 사용되지 않는 파라미터입니다. "
        "행동유형 '{behavior_id}'는 활성 모델이 아닌 쪽의 파라미터 집합을 무시하므로, 이 값을 써도 "
        "아무 변화가 없고 그 사실도 보고되지 않습니다.",
        message_en="'{parameter}' is not used by Wiedemann model {model}. Behavior '{behavior_id}' "
        "ignores the parameter set for the inactive model, so writing this value changes nothing "
        "— and nothing reports the no-op.",
        message_ja="'{parameter}' はWiedemannモデル{model}では使用されないパラメータです。行動"
        "タイプ '{behavior_id}' は非アクティブなモデル側のパラメータセットを無視するため、この値を"
        "書き込んでも何も変わらず、その事実も報告されません。",
    )
)


# ---------------------------------------------------------------------------
# #34 link_geometry_edit_reference_integrity (warn, postwrite)
#
# Merges catalog §3 "Positions are measured from the link's start" +
# "Splitting a link renumbers it and inserts a gap": both are consequences of
# a link geometry edit invalidating everything anchored to that link
# (signal heads, detectors, priority rules; routes referencing the old link
# number). No M2 tool edits link geometry today. CATALOG-GAP: reads an
# optional per-link "geometry_edited_this_turn" flag, defensively.
# ---------------------------------------------------------------------------


def _check_link_geometry_edit_reference_integrity(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    links = shadow_model.get("links", [])
    edited = [
        str(link.get("id", "?")) for link in links if link.get("geometry_edited_this_turn")
    ]
    if not edited:
        return None

    return Finding(
        constraint_id="link_geometry_edit_reference_integrity",
        tier="warn",
        phase="postwrite",
        messages=_render("link_geometry_edit_reference_integrity", links=", ".join(edited)),
        suggested_fix=None,
        details={"edited_links": edited},
    )


register(
    Constraint(
        id="link_geometry_edit_reference_integrity",
        applies_to=["*"],
        phase="postwrite",
        tier="warn",
        check=_check_link_geometry_edit_reference_integrity,
        message_ko="링크 {links}의 지오메트리가 이번 턴에 변경되었습니다. 신호기/검지기/우선권 "
        "규칙 등 이 링크 기준으로 위치가 정해지는 모든 객체와, 이 링크를 지나가는 모든 경로가 "
        "재검증 대상입니다 — 분할이었다면 새 번호와 삽입된 간격도 함께 확인하세요.",
        message_en="Link(s) {links} had their geometry changed this turn. Every object "
        "positioned relative to this link (signal heads, detectors, priority rules) and every "
        "route running across it needs re-validation — if this was a split, also check the new "
        "link number and the inserted gap.",
        message_ja="リンク {links} のジオメトリが今回のターンで変更されました。このリンクを基準に"
        "位置が決まるすべてのオブジェクト(信号機、検知器、優先権ルール)と、このリンクを通過する"
        "すべての経路が再検証の対象です — 分割だった場合は、新しい番号と挿入された間隙も確認して"
        "ください。",
    )
)


# ---------------------------------------------------------------------------
# #35 object_reference_integrity_on_delete (refuse, preflight)
#
# Merges catalog §3 "Port numbers bind detectors to external controllers" +
# "Deletes cascade, and the documentation is vague about how far": PTV says
# only that objects positioned on a deleted object "may also be deleted"
# without enumerating which, and actuated controllers address detectors by
# port number (not object number) — renumbering silently rewires which
# detector calls which movement. No M2 tool deletes objects today; unlike the
# other forward-ready checks in this batch, this one refuses UNCONDITIONALLY
# whenever that (not-yet-existing) tool is called — there is no reference
# graph in the shadow model to prove a delete's blast radius is safe, so the
# absence of proof is treated as proof of absence (same doctrine as #4
# signal_controller_type_unsupported's refuse-on-missing stance).
# ---------------------------------------------------------------------------


def _check_object_reference_integrity_on_delete(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "delete_object":
        return None

    params = action.get("params", {})
    return Finding(
        constraint_id="object_reference_integrity_on_delete",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "object_reference_integrity_on_delete",
            object_type=params.get("object_type", "?"),
            object_id=params.get("object_id", "?"),
        ),
        suggested_fix=None,
        details={"object_type": params.get("object_type"), "object_id": params.get("object_id")},
    )


register(
    Constraint(
        id="object_reference_integrity_on_delete",
        applies_to=["delete_object"],
        phase="preflight",
        tier="refuse",
        check=_check_object_reference_integrity_on_delete,
        message_ko="{object_type} '{object_id}' 삭제를 거부합니다. 이 시스템은 아직 참조 그래프를 "
        "가지고 있지 않아 삭제의 파급 범위(연쇄 삭제, 포트 번호 재배선 등)를 증명할 방법이 없습니다 "
        "— 검증할 수 없는 삭제는 수행하지 않습니다.",
        message_en="Refusing to delete {object_type} '{object_id}'. This system has no reference "
        "graph yet, so there is no way to prove the delete's blast radius (cascading deletes, "
        "detector port renumbering, ...) is safe — an unverifiable delete does not ship.",
        message_ja="{object_type} '{object_id}' の削除を拒否します。このシステムはまだ参照グラフを"
        "持っておらず、削除の波及範囲(連鎖削除、検知器ポート番号の再割り当てなど)が安全であることを"
        "証明する方法がありません — 検証できない削除は実行しません。",
    )
)


# ---------------------------------------------------------------------------
# #36 vehicle_composition_share_redistribution_gap (warn, preflight)
#
# Catalog §4 "'More trucks' redistributes rather than adds": vehicle mixes
# are relative shares normalized to 100%; raising the truck share LOWERS the
# car share, total stays the same. No M2 tool edits vehicle composition
# shares today; unconditional warn whenever that (not-yet-existing) tool is
# called — the ambiguity this catalog entry names is inherent to the verb
# itself, not dependent on any missing datum.
# ---------------------------------------------------------------------------


def _check_vehicle_composition_share_redistribution_gap(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_composition_share":
        return None

    params = action.get("params", {})
    return Finding(
        constraint_id="vehicle_composition_share_redistribution_gap",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_composition_share_redistribution_gap",
            composition_id=params.get("composition_id", "?"),
            class_id=params.get("class_id", "?"),
        ),
        suggested_fix={"hint": "ask whether the user means redistribution or an additive total-volume increase"},
        details={"composition_id": params.get("composition_id"), "class_id": params.get("class_id")},
    )


register(
    Constraint(
        id="vehicle_composition_share_redistribution_gap",
        applies_to=["set_vehicle_composition_share"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_composition_share_redistribution_gap,
        message_ko="조성 '{composition_id}'의 클래스 '{class_id}' 비율 변경은 다른 클래스 비율을 "
        "자동으로 낮춥니다(합계 100% 고정). 사용자가 '트럭을 늘려달라'고 했을 때 재분배를 의미하는지, "
        "총량을 늘리며 나머지는 그대로 두는 것을 의미하는지 확인 없이는 둘 다 그럴듯하지만 결과가 "
        "크게 다릅니다.",
        message_en="Changing class '{class_id}''s share in composition '{composition_id}' "
        "automatically lowers other classes' shares (they sum to 100%). Whether the user means "
        "redistribution or 'more trucks AND the same number of cars' (total volume rises too) "
        "is ambiguous without asking — both are legitimate and produce very different results.",
        message_ja="構成 '{composition_id}' のクラス '{class_id}' の比率変更は、他のクラスの比率を"
        "自動的に下げます(合計100%固定)。ユーザーが「トラックを増やして」と言った場合、再分配を"
        "意味するのか、総量を増やしつつ他はそのままにすることを意味するのかは確認なしには曖昧で"
        "あり、どちらも妥当ですが結果は大きく異なります。",
    )
)


# ---------------------------------------------------------------------------
# #37 restriction_missing_vehicle_class_scope (warn, preflight)
#
# Catalog §4 "Restrictions apply per vehicle class, not per type": speed
# decisions, lane closures and route validity are all defined PER CLASS, and
# any class not listed keeps its original behaviour — a partial
# specification silently exempts everyone else. No M2 tool authors
# restrictions today; unconditional-on-missing-scope warn whenever any of
# these (not-yet-existing) tools is called without an explicit class list.
# ---------------------------------------------------------------------------

_CLASS_SCOPED_RESTRICTION_TOOLS = ["set_lane_closure", "add_speed_decision", "set_route_restriction"]


def _check_restriction_missing_vehicle_class_scope(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") not in _CLASS_SCOPED_RESTRICTION_TOOLS:
        return None

    params = action.get("params", {})
    if params.get("vehicle_classes"):
        return None

    return Finding(
        constraint_id="restriction_missing_vehicle_class_scope",
        tier="warn",
        phase="preflight",
        messages=_render("restriction_missing_vehicle_class_scope", tool=action.get("tool", "")),
        suggested_fix={"hint": "enumerate the vehicle classes this restriction should apply to"},
        details={"tool": action.get("tool")},
    )


register(
    Constraint(
        id="restriction_missing_vehicle_class_scope",
        applies_to=_CLASS_SCOPED_RESTRICTION_TOOLS,
        phase="preflight",
        tier="warn",
        check=_check_restriction_missing_vehicle_class_scope,
        message_ko="'{tool}' 호출에 적용 대상 차량 클래스가 명시되지 않았습니다. Vissim의 제한은 "
        "클래스 단위로 적용되며, 목록에 없는 클래스는 원래 동작을 그대로 유지합니다 — 명시하지 "
        "않으면 의도와 달리 다른 클래스들이 조용히 제외됩니다.",
        message_en="'{tool}' was called without an explicit vehicle-class scope. Vissim "
        "restrictions apply per class, and any class not listed keeps its original behaviour — "
        "an unspecified scope silently exempts every other class from the restriction.",
        message_ja="'{tool}' の呼び出しに適用対象の車両クラスが明示されていません。Vissimの制限は"
        "クラス単位で適用され、リストにないクラスは元の動作を維持します — 明示しないと、意図に"
        "反して他のクラスが静かに除外されます。",
    )
)


# ---------------------------------------------------------------------------
# #38 units_ambiguous_on_write (warn, preflight)
#
# Catalog §4 "Units and percentages come back unmarked": whether speeds are
# km/h or mph is a network-level setting; percentages arrive as 0-1 rather
# than 0-100 with nothing marking which. CATALOG-GAP: no network-level unit
# settings exist in the {links, nodes, vehicle_inputs, signals} shadow-model
# contract; reads shadow_model["unit_settings"] defensively — only fires when
# the settings OBJECT exists but is incomplete (never on total absence, which
# would fire on every single preflight check in the whole test suite).
# ---------------------------------------------------------------------------


def _check_units_ambiguous_on_write(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    unit_settings = shadow_model.get("unit_settings")  # CATALOG-GAP: not in shadow-model contract
    if unit_settings is None:
        return None
    if unit_settings.get("speed_unit"):
        return None

    return Finding(
        constraint_id="units_ambiguous_on_write",
        tier="warn",
        phase="preflight",
        messages=_render("units_ambiguous_on_write"),
        suggested_fix=None,
        details={},
    )


register(
    Constraint(
        id="units_ambiguous_on_write",
        applies_to=["*"],
        phase="preflight",
        tier="warn",
        check=_check_units_ambiguous_on_write,
        message_ko="이 네트워크의 단위 설정(속도 km/h 대 mph 등)이 불완전하게 확인되었습니다. "
        "값에는 단위가 표시되지 않으므로, 단위를 확정하지 않고 값을 쓰거나 비교하면 조용히 "
        "잘못된 결과를 만들 수 있습니다.",
        message_en="This network's unit settings (e.g. speed in km/h vs mph) were only "
        "partially resolved. Values carry no unit marker of their own — writing or comparing "
        "without confirming units first can silently produce nonsense.",
        message_ja="このネットワークの単位設定(速度のkm/h対mphなど)が不完全にしか確認できません"
        "でした。値自体には単位が表示されないため、単位を確定せずに書き込みや比較を行うと、静かに"
        "誤った結果を生み出す可能性があります。",
    )
)


# ---------------------------------------------------------------------------
# #39 evaluation_change_deletes_results (refuse, preflight)
#
# Catalog §5 "Adding an evaluation deletes the results you already have":
# Vissim discards previous simulation results when the evaluation
# configuration changes. No M2 tool edits evaluation configuration today;
# unconditional refuse whenever that (not-yet-existing) tool is called — per
# the catalog's own "Do": "never let an agent add an evaluation without
# warning what it will erase," and this system has no results-inventory
# mechanism yet to make that warning meaningful, so it refuses outright.
# ---------------------------------------------------------------------------


def _check_evaluation_change_deletes_results(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") not in ("add_evaluation", "edit_evaluation_config"):
        return None

    return Finding(
        constraint_id="evaluation_change_deletes_results",
        tier="refuse",
        phase="preflight",
        messages=_render("evaluation_change_deletes_results"),
        suggested_fix={"hint": "confirm with the user which existing results this would erase before proceeding"},
        details={"tool": action.get("tool")},
    )


register(
    Constraint(
        id="evaluation_change_deletes_results",
        applies_to=["add_evaluation", "edit_evaluation_config"],
        phase="preflight",
        tier="refuse",
        check=_check_evaluation_change_deletes_results,
        message_ko="평가 구성 변경을 거부합니다. Vissim은 평가 설정이 바뀌면 기존 시뮬레이션 결과를 "
        "폐기하며, 이는 수 시간짜리 배치 실행의 결과일 수 있습니다. 무엇이 사라지는지 사용자에게 "
        "미리 경고할 방법이 아직 없어 이 편집은 진행하지 않습니다.",
        message_en="Refusing to change evaluation configuration. Vissim discards existing "
        "simulation results when evaluation settings change — potentially a multi-hour batch of "
        "runs. This system has no way yet to warn the user what would be erased, so it does not "
        "proceed.",
        message_ja="評価設定の変更を拒否します。Vissimは評価設定が変更されると既存のシミュレーション"
        "結果を破棄します — 数時間規模のバッチ実行の結果である可能性もあります。何が失われるかを"
        "事前にユーザーへ警告する方法がまだないため、この編集は実行しません。",
    )
)


# ---------------------------------------------------------------------------
# #40 driving_behavior_shared_object_edit (refuse, preflight)
#
# Catalog §6 "Driving behaviours are global objects": a behaviour parameter
# set isn't attached to a link — editing one to fix a single ramp changes
# EVERY link using it. The catalog's own remedy: "never edit a shared
# behaviour in place; duplicate it, edit the copy, and retarget only the
# intended links." Real, deterministic check on action.params alone (no
# shadow-model dependency) — no M2 tool edits driving behaviours today, but
# unlike #33/#36 this fires unless the caller explicitly marks that it
# duplicated first.
# ---------------------------------------------------------------------------


def _check_driving_behavior_shared_object_edit(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None

    params = action.get("params", {})
    if params.get("duplicated_before_edit") is True:
        return None

    return Finding(
        constraint_id="driving_behavior_shared_object_edit",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "driving_behavior_shared_object_edit", behavior_id=params.get("behavior_id", "?")
        ),
        suggested_fix={"hint": "duplicate the behaviour, edit the copy, and retarget only the intended links"},
        details={"behavior_id": params.get("behavior_id")},
    )


register(
    Constraint(
        id="driving_behavior_shared_object_edit",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="refuse",
        check=_check_driving_behavior_shared_object_edit,
        message_ko="행동유형 '{behavior_id}'은(는) 여러 링크가 공유하는 전역 객체입니다. 복제 없이 "
        "제자리에서 편집하면 이 행동유형을 쓰는 모든 링크가 함께 바뀝니다 — 먼저 복제한 뒤 그 "
        "사본만 편집하고 의도한 링크에만 재적용해야 합니다.",
        message_en="Behavior '{behavior_id}' is a global object shared by multiple links. "
        "Editing it in place changes every link using it — duplicate it first, edit the copy, "
        "and retarget only the intended links.",
        message_ja="行動タイプ '{behavior_id}' は複数のリンクが共有するグローバルオブジェクトです。"
        "複製せずにその場で編集すると、この行動タイプを使用するすべてのリンクが変更されます — "
        "先に複製し、そのコピーのみを編集して、意図したリンクにのみ再適用してください。",
    )
)


# ---------------------------------------------------------------------------
# #41 parameter_diff_not_calibration_meaningful (warn, postwrite)
#
# Catalog §6 "Capacity is many-to-one in the parameters": multiple distinct
# parameter combinations can produce identical capacity, so "restore this
# parameter to default" doesn't restore the model — it moves it to a
# different point where the engineer's compensating adjustments no longer
# make sense. CATALOG-GAP: no calibration-reference baseline exists in the
# shadow-model contract; reads the (not-yet-populated) per-behaviour
# "calibration_reference_value" field defensively.
# ---------------------------------------------------------------------------


def _check_parameter_diff_not_calibration_meaningful(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None
    if "written_value" not in extra:
        return None

    params = action.get("params", {})
    behavior_id = str(params.get("behavior_id", ""))
    parameter = str(params.get("parameter", ""))

    behaviors = shadow_model.get("driving_behaviors")  # CATALOG-GAP: not in shadow-model contract
    if not behaviors:
        return None
    behavior = _find_by_field(behaviors, "id", behavior_id)
    if behavior is None:
        return None

    param_info = (behavior.get("parameters") or {}).get(parameter)
    if not param_info:
        return None
    raw_reference = param_info.get("calibration_reference_value")
    if raw_reference is None:
        return None

    reference = _coerce_number(raw_reference)
    written = _coerce_number(extra.get("written_value"))
    if reference is None or written is None:
        return None
    if not _values_differ(written, reference):
        return None

    return Finding(
        constraint_id="parameter_diff_not_calibration_meaningful",
        tier="warn",
        phase="postwrite",
        messages=_render(
            "parameter_diff_not_calibration_meaningful",
            parameter=parameter,
            behavior_id=behavior_id,
            reference=raw_reference,
        ),
        suggested_fix={"calibration_reference_value": reference},
        details={"behavior_id": behavior_id, "parameter": parameter, "reference": reference, "written": written},
    )


register(
    Constraint(
        id="parameter_diff_not_calibration_meaningful",
        applies_to=["set_driving_behavior_parameter"],
        phase="postwrite",
        tier="warn",
        check=_check_parameter_diff_not_calibration_meaningful,
        message_ko="'{parameter}'가 행동유형 '{behavior_id}'의 보정 기준값({reference})에서 "
        "벗어났습니다. 용량은 여러 파라미터 조합이 동일한 값을 낼 수 있어(many-to-one), 이 값만 "
        "바꾸면 엔지니어의 보완 조정이 더 이상 맞지 않는 다른 지점으로 모델이 이동합니다 — 보정 "
        "통계를 다시 계산하고 승인 없이는 확정하지 마세요.",
        message_en="'{parameter}' now differs from behavior '{behavior_id}''s calibration "
        "reference value ({reference}). Capacity is many-to-one in these parameters, so this "
        "single change moves the model to a different point where the engineer's compensating "
        "adjustments may no longer make sense — recompute the calibration statistic before "
        "persisting without sign-off.",
        message_ja="'{parameter}' が行動タイプ '{behavior_id}' のキャリブレーション基準値"
        "({reference})から外れました。容量はこれらのパラメータにおいて多対一の関係にあるため、この"
        "一つの変更だけでモデルは技術者による補正調整がもはや意味を持たない別の点へ移動する可能性が"
        "あります — 承認なしに確定する前にキャリブレーション統計を再計算してください。",
    )
)


# ---------------------------------------------------------------------------
# #42 agency_restricted_parameter_write (refuse, preflight)
#
# Catalog §6 "Some parameters are off-limits by agency policy": Washington
# State and Maryland both publish tables of adjustable parameters, and both
# say of the deeper car-following parameters simply "use default" — WSDOT
# requires written justification/approval for anything outside suggested
# ranges. Real, deterministic check: a hardcoded agency-restricted parameter
# name set, no shadow-model dependency.
# ---------------------------------------------------------------------------

_AGENCY_RESTRICTED_PARAMETERS = {f"cc{n}" for n in range(0, 10)}


def _check_agency_restricted_parameter_write(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None

    params = action.get("params", {})
    parameter = str(params.get("parameter", "")).lower()
    if parameter not in _AGENCY_RESTRICTED_PARAMETERS:
        return None

    return Finding(
        constraint_id="agency_restricted_parameter_write",
        tier="refuse",
        phase="preflight",
        messages=_render("agency_restricted_parameter_write", parameter=parameter),
        suggested_fix={"hint": "use default; agency written justification/approval is required to deviate"},
        details={"parameter": parameter},
    )


register(
    Constraint(
        id="agency_restricted_parameter_write",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="refuse",
        check=_check_agency_restricted_parameter_write,
        message_ko="'{parameter}'는 기관 정책상 조정이 제한된 파라미터입니다. WSDOT/메릴랜드 DOT는 "
        "이 수준의 차량추종 파라미터에 대해 '기본값 사용'을 요구하며, 벗어나려면 서면 근거와 승인이 "
        "필요합니다 — AI는 현장 검증된 예외를 스스로 인증할 수 없습니다.",
        message_en="'{parameter}' is a parameter agency policy restricts adjustment of. WSDOT "
        "and Maryland DOT both say of this tier of car-following parameter simply 'use default' "
        "— deviating requires written justification and approval. An AI cannot self-certify a "
        "field-validated exception.",
        message_ja="'{parameter}' は行政機関のポリシーにより調整が制限されているパラメータです。"
        "WSDOTおよびメリーランド州DOTはいずれも、このレベルの車両追従パラメータについて「デフォルト"
        "を使用」することを求めており、逸脱するには書面による正当性の説明と承認が必要です — AIは"
        "現場で検証された例外を自ら認証することはできません。",
    )
)


# ---------------------------------------------------------------------------
# #43 unbounded_com_dialog_risk (warn, preflight)
#
# Catalog §7 "Dialogs will hang a worker forever": an empty file path opens a
# file browser and blocks indefinitely; there is no global "suppress all
# dialogs" switch, and quick mode is the documented mitigation. CATALOG-GAP:
# no per-run COM-environment flag exists in the shadow-model contract; reads
# shadow_model["com_environment"] defensively.
# ---------------------------------------------------------------------------


def _check_unbounded_com_dialog_risk(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "run_simulation":
        return None

    com_env = shadow_model.get("com_environment")  # CATALOG-GAP: not in shadow-model contract
    if com_env is None:
        return None
    if com_env.get("quick_mode_enabled"):
        return None

    return Finding(
        constraint_id="unbounded_com_dialog_risk",
        tier="warn",
        phase="preflight",
        messages=_render("unbounded_com_dialog_risk"),
        suggested_fix=None,
        details={},
    )


register(
    Constraint(
        id="unbounded_com_dialog_risk",
        applies_to=["run_simulation"],
        phase="preflight",
        tier="warn",
        check=_check_unbounded_com_dialog_risk,
        message_ko="이 실행 환경에서 quick mode가 켜져 있는지 확인되지 않았습니다. Vissim에는 모든 "
        "대화상자를 억제하는 전역 스위치가 없어, 형식 변환 확인이나 메시지 창이 뜨면 워커가 무기한 "
        "멈출 수 있습니다 — quick mode와 워치독을 확인하세요.",
        message_en="It could not be confirmed that quick mode is enabled for this run "
        "environment. Vissim has no global 'suppress all dialogs' switch — a format-conversion "
        "prompt or message window can hang a worker indefinitely — confirm quick mode and a "
        "wall-clock watchdog are in place.",
        message_ja="この実行環境でquick modeが有効になっているか確認できませんでした。Vissimには"
        "すべてのダイアログを抑制するグローバルスイッチがなく、形式変換の確認やメッセージウィンドウ"
        "が表示されるとワーカーが無期限に停止する可能性があります — quick modeとウォッチドッグの"
        "設定を確認してください。",
    )
)


# ---------------------------------------------------------------------------
# #44 vehicle_input_interval_out_of_range (refuse, preflight) — v2 unit ⑤
#
# Not a §-numbered catalog entry; a defensive addition specific to
# set_vehicle_input_volume's new interval_no param. interval_no == 1 is
# ALWAYS valid — every vehicle input has an implicit interval 1 even when the
# .inpx carries no explicit <timeIntervalVehVolume> at all (the demo
# network's shape; the M2.1 write path has always targeted Volume(1)
# successfully there). interval_no > 1 is only checkable against a KNOWN
# interval_count (tools_network._parse_vehicle_inputs, v2 unit ⑤) — without
# this check, an out-of-range request would reach live COM and raise a raw
# "Object N not found" com_error (verified live,
# backend/tools/vehicle_input_interval_probe.py) instead of a clear,
# structured refusal with the available interval count as suggested_fix.
# When interval_count is unknown (0 — the demo network's shape, or a
# caller-built fixture that omits the field), this check stays silent:
# refusing on absent data would be a false positive, not caution (same
# posture as #5's min_green_intergreen_sum gap) — the write is still safe
# either way, since a genuinely-nonexistent interval fails CLOSED over COM
# (a normal RuntimeError, never a silent wrong number).
# ---------------------------------------------------------------------------


def _check_vehicle_input_interval_out_of_range(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_input_volume":
        return None

    params = action.get("params", {})
    raw_interval_no = params.get("interval_no", 1)
    interval_no = _coerce_number(raw_interval_no, cast=int)
    if interval_no is None or interval_no <= 1:
        return None  # interval 1 is always valid; malformed values are invalid_params's job

    input_no = str(params.get("input_no", ""))
    vehicle_input = _find_by_field(shadow_model.get("vehicle_inputs", []), "no", input_no)
    if vehicle_input is None:
        return None  # missing target is object_not_found's job (#1)

    raw_interval_count = vehicle_input.get("interval_count")
    interval_count = _coerce_number(raw_interval_count, cast=int)
    if not interval_count:  # None or 0 — datum unavailable, don't guess
        return None

    if interval_no <= interval_count:
        return None

    return Finding(
        constraint_id="vehicle_input_interval_out_of_range",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "vehicle_input_interval_out_of_range",
            input_no=input_no,
            interval_no=interval_no,
            interval_count=interval_count,
        ),
        suggested_fix={"available_intervals": interval_count},
        details={"input_no": input_no, "interval_no": interval_no, "interval_count": interval_count},
    )


register(
    Constraint(
        id="vehicle_input_interval_out_of_range",
        applies_to=["set_vehicle_input_volume"],
        phase="preflight",
        tier="refuse",
        check=_check_vehicle_input_interval_out_of_range,
        message_ko="차량 입력 '{input_no}'에는 시간 구간이 {interval_count}개뿐입니다. 요청한 구간 "
        "{interval_no}은(는) 존재하지 않습니다.",
        message_en="Vehicle input '{input_no}' only has {interval_count} time interval(s). The "
        "requested interval {interval_no} does not exist.",
        message_ja="車両インプット '{input_no}' には時間区間が{interval_count}個しかありません。"
        "指定された区間{interval_no}は存在しません。",
    )
)


# =============================================================================
# v2 unit ⑥ (2026-07-24) — static vehicle routing decisions
# =============================================================================


# ---------------------------------------------------------------------------
# #46 route_not_found_in_decision (refuse, preflight)
#
# Not a §-numbered catalog entry; the SECOND half of set_route_relative_flow's
# object-resolution (the FIRST half — does decision_no exist at all — is
# object_not_found (#1), which _TOOL_TARGET_SPEC already covers). A routing
# decision's routes have no flat, cross-decision id space of their own (route
# "No" is only unique WITHIN its own decision — Beijing's own data has
# decision 1 route 2 and decision 5 route 2 as two entirely different
# objects), so this cannot reuse #1's single-flat-list machinery; it looks
# the target decision up first (deferring to #1 when even THAT fails) and
# then checks route_no against that ONE decision's own routes list.
# ---------------------------------------------------------------------------


def _check_route_not_found_in_decision(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_route_relative_flow":
        return None

    params = action.get("params", {})
    decision_no = str(params.get("decision_no", ""))
    route_no = str(params.get("route_no", ""))

    decision = _find_by_field(shadow_model.get("routing_decisions", []), "no", decision_no)
    if decision is None:
        return None  # missing decision is object_not_found's job (#1)

    routes = decision.get("routes", [])
    if _find_by_field(routes, "route_no", route_no) is not None:
        return None

    candidates = [str(r.get("route_no", "")) for r in routes]
    return Finding(
        constraint_id="route_not_found_in_decision",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "route_not_found_in_decision",
            decision_no=decision_no,
            route_no=route_no,
            candidates=", ".join(candidates) if candidates else "-",
        ),
        suggested_fix=candidates,
        details={"decision_no": decision_no, "route_no": route_no, "candidates": candidates},
    )


register(
    Constraint(
        id="route_not_found_in_decision",
        applies_to=["set_route_relative_flow"],
        phase="preflight",
        tier="refuse",
        check=_check_route_not_found_in_decision,
        message_ko="경로 결정 '{decision_no}'에 경로 번호 '{route_no}'이(가) 없습니다. 이 결정의 사용 "
        "가능한 경로 번호: {candidates}.",
        message_en="Routing decision '{decision_no}' has no route numbered '{route_no}'. This "
        "decision's available route numbers: {candidates}.",
        message_ja="経路決定 '{decision_no}' に経路番号 '{route_no}' はありません。この決定で利用"
        "可能な経路番号: {candidates}。",
    )
)


# ---------------------------------------------------------------------------
# #47 route_relative_flow_interval_out_of_range (refuse, preflight)
#
# Not a §-numbered catalog entry; the SAME defensive pattern as #44
# (vehicle_input_interval_out_of_range) applied to set_route_relative_flow's
# own interval_no param. interval_no == 1 is always potentially valid (every
# real route observed carries at least one relFlow interval; malformed
# values are invalid_params's job at the pydantic layer). interval_no > 1 is
# only checkable against a KNOWN interval_count
# (tools_network._parse_routing_decisions) -- without this check, an
# out-of-range request would reach live COM and raise a raw "Object N not
# found" com_error (verified live, backend/tools/routing_decision_probe.py's
# Luxembourg follow-up probe) instead of a clear, structured refusal with the
# available interval count as suggested_fix.
# ---------------------------------------------------------------------------


def _check_route_relative_flow_interval_out_of_range(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_route_relative_flow":
        return None

    params = action.get("params", {})
    raw_interval_no = params.get("interval_no", 1)
    interval_no = _coerce_number(raw_interval_no, cast=int)
    if interval_no is None or interval_no <= 1:
        return None  # interval 1 is always valid; malformed values are invalid_params's job

    decision_no = str(params.get("decision_no", ""))
    route_no = str(params.get("route_no", ""))
    decision = _find_by_field(shadow_model.get("routing_decisions", []), "no", decision_no)
    if decision is None:
        return None  # missing target is object_not_found's job (#1)
    route = _find_by_field(decision.get("routes", []), "route_no", route_no)
    if route is None:
        return None  # missing target is route_not_found_in_decision's job (#46)

    raw_interval_count = route.get("interval_count")
    interval_count = _coerce_number(raw_interval_count, cast=int)
    if not interval_count:  # None or 0 — datum unavailable, don't guess
        return None

    if interval_no <= interval_count:
        return None

    return Finding(
        constraint_id="route_relative_flow_interval_out_of_range",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "route_relative_flow_interval_out_of_range",
            decision_no=decision_no,
            route_no=route_no,
            interval_no=interval_no,
            interval_count=interval_count,
        ),
        suggested_fix={"available_intervals": interval_count},
        details={
            "decision_no": decision_no,
            "route_no": route_no,
            "interval_no": interval_no,
            "interval_count": interval_count,
        },
    )


register(
    Constraint(
        id="route_relative_flow_interval_out_of_range",
        applies_to=["set_route_relative_flow"],
        phase="preflight",
        tier="refuse",
        check=_check_route_relative_flow_interval_out_of_range,
        message_ko="경로 결정 '{decision_no}'의 경로 '{route_no}'에는 시간 구간이 {interval_count}개"
        "뿐입니다. 요청한 구간 {interval_no}은(는) 존재하지 않습니다.",
        message_en="Decision '{decision_no}' route '{route_no}' only has {interval_count} time "
        "interval(s). The requested interval {interval_no} does not exist.",
        message_ja="経路決定 '{decision_no}' の経路 '{route_no}' には時間区間が{interval_count}個"
        "しかありません。指定された区間{interval_no}は存在しません。",
    )
)


# ---------------------------------------------------------------------------
# #48 route_relative_flow_split_confirm (warn, preflight)
#
# Not a §-numbered catalog entry; a defensive addition specific to
# set_route_relative_flow, motivated directly by the v2 unit ⑥ probe's
# central empirical finding (see vissim_com.set_route_relative_flow's
# docstring): relFlow is a RAW weight, not a percentage -- Vissim computes
# each route's ACTUAL simulated traffic share at RUN time as this value
# divided by the SUM of every route's relFlow in the SAME decision, and
# writing one route's relFlow leaves every sibling route's own stored value
# completely untouched (verified live: no renormalization on write). A bare
# "relFlow set to 745" on an approval card would misleadingly read like a
# direct traffic-share number. This ALWAYS fires (whenever the target
# resolves; a missing decision/route is #1's/#46's job) so the decision's
# current route count, the estimated resulting share (computed from the
# OTHER routes' CURRENT relFlow plus this proposed new value), and an
# explicit single-route caveat are unconditionally echoed into the approval
# card's warning list -- regardless of whether the planner's own free-text
# summary happens to explain the normalization. Deliberately warn-tier, not
# refuse: this is a confirmation aid, not a problem with the write itself
# (mirrors conflict_area_status_change_confirm's precedent, v2 unit ④).
# ---------------------------------------------------------------------------


def _rel_flow_at_interval(route: dict[str, Any], interval_no: int) -> float | None:
    """The route's own relFlow value at ``interval_no`` (1-based), reading
    the per-interval ``rel_flows`` list when present and falling back to the
    top-level ``rel_flow`` field for interval 1 (mirrors
    guardrails._resolve_saved_value's same interval-list-then-top-level-
    fallback shape)."""
    rel_flows = route.get("rel_flows") or []
    entry = next(
        (rf for rf in rel_flows if _coerce_number(rf.get("interval_no"), cast=int) == interval_no),
        None,
    )
    if entry is not None:
        return _coerce_number(entry.get("rel_flow"))
    if interval_no == 1:
        return _coerce_number(route.get("rel_flow"))
    return None


def _check_route_relative_flow_split_confirm(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_route_relative_flow":
        return None

    params = action.get("params", {})
    decision_no = str(params.get("decision_no", ""))
    route_no = str(params.get("route_no", ""))
    raw_interval_no = params.get("interval_no", 1)
    interval_no = _coerce_number(raw_interval_no, cast=int)
    if interval_no is None:
        interval_no = 1
    raw_new_rel_flow = params.get("rel_flow")
    if raw_new_rel_flow is None:
        return None
    new_rel_flow = _coerce_number(raw_new_rel_flow)
    if new_rel_flow is None:
        return None

    decision = _find_by_field(shadow_model.get("routing_decisions", []), "no", decision_no)
    if decision is None:
        return None  # missing target is object_not_found's job (#1)
    routes = decision.get("routes", [])
    target = _find_by_field(routes, "route_no", route_no)
    if target is None:
        return None  # missing target is route_not_found_in_decision's job (#46)

    old_rel_flow = _rel_flow_at_interval(target, interval_no)
    route_count = len(routes)

    sibling_sum = 0.0
    sibling_known = True
    for route in routes:
        if str(route.get("route_no", "")) == route_no:
            continue
        sibling_value = _rel_flow_at_interval(route, interval_no)
        if sibling_value is None:
            sibling_known = False
            break
        sibling_sum += sibling_value

    if route_count <= 1:
        share_pct: float | None = 100.0
        share_note_ko = "이 결정에는 경로가 이 경로 하나뿐이므로, relFlow 값과 무관하게 실제 배분은 " \
            "항상 100%입니다."
        share_note_en = "This decision has only this one route, so the actual traffic share is " \
            "always 100% regardless of the relFlow value."
        share_note_ja = "この決定にはこの経路しかないため、relFlowの値に関わらず実際の配分は常に" \
            "100%です。"
    elif sibling_known and (sibling_sum + new_rel_flow) > 0:
        share_pct = round((new_rel_flow / (sibling_sum + new_rel_flow)) * 100, 1)
        share_note_ko = f"이 결정의 다른 경로들이 현재 relFlow를 유지한다고 가정하면, 이 경로의 " \
            f"예상 실제 배분은 약 {share_pct}%입니다."
        share_note_en = f"Assuming the decision's other routes keep their current relFlow, this " \
            f"route's estimated actual share is about {share_pct}%."
        share_note_ja = f"この決定の他の経路が現在のrelFlowを維持すると仮定すると、この経路の推定" \
            f"実配分は約{share_pct}%です。"
    else:
        share_pct = None
        share_note_ko = "다른 경로의 relFlow 값을 알 수 없어 예상 배분을 계산할 수 없습니다."
        share_note_en = "The resulting share could not be estimated (a sibling route's relFlow " \
            "is unknown)."
        share_note_ja = "他の経路のrelFlow値が不明なため、予想配分を計算できません。"

    return Finding(
        constraint_id="route_relative_flow_split_confirm",
        tier="warn",
        phase="preflight",
        messages=_render(
            "route_relative_flow_split_confirm",
            decision_no=decision_no,
            route_no=route_no,
            interval_no=interval_no,
            old_rel_flow=old_rel_flow if old_rel_flow is not None else "?",
            new_rel_flow=raw_new_rel_flow,
            route_count=route_count,
            share_note_ko=share_note_ko,
            share_note_en=share_note_en,
            share_note_ja=share_note_ja,
        ),
        suggested_fix=None,
        details={
            "decision_no": decision_no,
            "route_no": route_no,
            "interval_no": interval_no,
            "old_rel_flow": old_rel_flow,
            "new_rel_flow": new_rel_flow,
            "route_count": route_count,
            "estimated_share_pct": share_pct,
        },
    )


register(
    Constraint(
        id="route_relative_flow_split_confirm",
        applies_to=["set_route_relative_flow"],
        phase="preflight",
        tier="warn",
        check=_check_route_relative_flow_split_confirm,
        message_ko="경로 결정 '{decision_no}'의 경로 '{route_no}' (구간 {interval_no}) relFlow를 "
        "{old_rel_flow} → {new_rel_flow}(으)로 변경하려 합니다. relFlow는 백분율이 아니라 원시 "
        "가중치이며, Vissim은 같은 결정 안의 모든 경로 relFlow 합계에 대한 비율로 실제 교통 배분을 "
        "시뮬레이션 시점에 계산합니다 — 이 값을 바꿔도 다른 경로 자체의 relFlow는 그대로 남습니다 "
        "(이 결정의 경로 수: {route_count}개). {share_note_ko}",
        message_en="Proposing to change decision '{decision_no}' route '{route_no}' (interval "
        "{interval_no}) relFlow from {old_rel_flow} to {new_rel_flow}. relFlow is a RAW weight, "
        "not a percentage — Vissim computes the actual traffic split at simulation time as each "
        "route's share of the SUM of every route's relFlow in the same decision, and writing "
        "this value leaves every sibling route's own relFlow untouched (this decision has "
        "{route_count} route(s)). {share_note_en}",
        message_ja="経路決定 '{decision_no}' の経路 '{route_no}'（区間{interval_no}）のrelFlowを "
        "{old_rel_flow} から {new_rel_flow} に変更しようとしています。relFlowは百分率ではなく生の" \
        "重みであり、Vissimは同じ決定内の全経路のrelFlow合計に対する比率として実際の交通配分を"
        "シミュレーション時に計算します — この値を変更しても他の経路自身のrelFlowはそのまま残り"
        "ます（この決定の経路数: {route_count}）。{share_note_ja}",
    )
)

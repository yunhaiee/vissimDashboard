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
- "More trucks" redistributes rather than adds (a raw weight, not an
  auto-renormalized share -- see v2 unit ⑦'s corrected finding below) ->
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
import math
from dataclasses import dataclass, field
from typing import Any, Callable

Phase = str  # "preflight" | "postwrite" | "postrun" | "dataset"
# "dataset" (health-check unit, 2026-07-28): a constraint that only ever
# fires from services.agent.health_check's whole-network scan
# (constraints.get_dataset_checks()), never from guardrails.py's real
# preflight/postwrite/postrun edit-time calls -- run_checks() is only ever
# invoked with phase in {"preflight", "postwrite", "postrun"} (see
# guardrails.py's three GuardrailEngine methods), so a "dataset"-phase
# constraint is unreachable there by construction, not by convention alone.
Tier = str  # "refuse" | "warn"


@dataclass
class Finding:
    """One constraint violation surfaced by a check.

    ``messages`` is rendered at check time (ko/en/ja, with the offending
    values interpolated). ``suggested_fix`` is machine-usable (e.g. a list of
    difflib candidate dicts -- ``{id, name, ...}``, never bare ids, since
    2026-07-31 -- for #1, a capacity number for #3). ``details`` holds
    arbitrary supporting evidence (readback vs written, capacity numbers)
    for logging/debugging.
    """

    constraint_id: str
    tier: Tier
    phase: Phase
    messages: dict[str, str]
    suggested_fix: Any = None
    details: dict[str, Any] = field(default_factory=dict)


CheckFn = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], "Finding | list[Finding] | None"]


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


# ---------------------------------------------------------------------------
# Dataset-check hook (health-check unit, 2026-07-28)
#
# services.agent.health_check's HealthCheckRunner needs to run a SUBSET of
# this catalog against a whole parsed network with no proposed action at all
# (a one-click "모델 건강검진" report, not an agent turn) -- most constraints
# above are action-gated (they read action["params"] to know WHICH object a
# proposed edit targets, or extra["run_result"]/extra["written_value"] that
# only exist after a real COM write/run) and have no meaning against a cold
# parse. Rather than a fragile try-every-constraint-and-see-what-doesn't-
# crash approach, each dataset-scannable constraint opts IN explicitly here,
# by id, via :func:`register_dataset_check` -- called from health_check.py
# (the module that actually owns "how do you turn this one-action check into
# a whole-network scan"), never from this file, so this module's own edit-time
# behavior (``run_checks``, called by guardrails.py's three phase methods)
# is completely unchanged by any of it: nothing in ``_DATASET_CHECKS`` is
# ever consulted by ``run_checks``.
# ---------------------------------------------------------------------------

DatasetCheckFn = Callable[[dict[str, Any]], list["Finding"]]

_DATASET_CHECKS: list[tuple[tuple[str, ...], DatasetCheckFn]] = []


def register_dataset_check(constraint_ids: str | list[str], fn: DatasetCheckFn) -> None:
    """Opt one or more already-registered constraints into health-check
    dataset-wide scanning. ``fn`` takes ONLY the shadow model (a health check
    has no proposed action/extra) and returns every Finding it can find
    across the whole network -- zero, one, or many. Grouping multiple ids
    under one ``fn`` call (rather than one registration per id) lets a single
    scan that naturally produces several constraint ids at once (e.g. one
    pass over every signal controller yielding both ``cycle_below_
    intergreen_sum`` and ``conflicting_greens_in_signal_plan`` findings) be
    registered -- and therefore invoked -- exactly once, not once per id."""
    ids = (constraint_ids,) if isinstance(constraint_ids, str) else tuple(constraint_ids)
    for constraint_id in ids:
        if constraint_id not in _REGISTRY:
            raise KeyError(f"register_dataset_check: unknown constraint id {constraint_id!r}")
    _DATASET_CHECKS.append((ids, fn))


def get_dataset_checks() -> list[tuple[tuple[str, ...], DatasetCheckFn]]:
    """Every registered dataset-check scan function, each paired with the
    constraint id(s) it covers -- call each ``fn`` exactly once (never once
    per id) to avoid double-counting its findings."""
    return list(_DATASET_CHECKS)


def get_dataset_checkable_ids() -> frozenset[str]:
    """Every constraint id currently reachable through the health-check
    dataset-scan path (across every registered scan's id group)."""
    return frozenset(constraint_id for ids, _ in _DATASET_CHECKS for constraint_id in ids)


def get_edit_time_only_constraints() -> tuple[Constraint, ...]:
    """Every registered constraint NOT reachable through the health-check
    dataset scan -- action-gated checks (need a proposed edit's params) and
    run-gated checks (need real COM write/run evidence) alike. The health
    check report lists these by id/title so its coverage claim stays honest:
    "checked at edit time", never silently omitted."""
    checkable = get_dataset_checkable_ids()
    return tuple(c for c in _REGISTRY.values() if c.id not in checkable)


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

    QUEUE 3 unit 8 (``apply_edit_plan``): a check's ``CheckFn`` may return a
    ``list[Finding]`` instead of a single ``Finding | None`` — every check
    that predates this stays exactly ``Finding | None`` (unaffected; a bare
    ``Finding`` is still appended as one item, ``None`` still skipped). The
    ONLY constraint that returns a list today is ``apply_edit_plan_validation``
    (``services.agent.batch.validate_plan``): a single batch action can
    legitimately surface MANY distinct findings in one preflight pass (one
    per offending step, one per cross-step collision, one per combined-effect
    interaction, plus an always-present plan-overview finding) — the
    single-``Finding``-per-check shape every other constraint uses has no way
    to express "this one call found N independent problems," and minting N
    separate constraint ids/registrations for what is really one cohesive
    batch-validation pass would fragment ownership of a single, shared
    computation (``validate_plan`` runs the patched-shadow preflight loop
    exactly once). ``GuardrailEngine``/``GuardrailReport`` never needed to
    change: both already operate on a flat ``list[Finding]`` and read each
    finding's OWN ``.tier``, never the registering ``Constraint.tier``.
    """
    extra = extra if extra is not None else {}
    tool = action.get("tool", "")

    findings: list[Finding] = []
    for constraint in _REGISTRY.values():
        if constraint.phase != phase:
            continue
        if not _applies(constraint, tool):
            continue
        result = constraint.check(action, shadow_model, extra)
        if result is None:
            continue
        if isinstance(result, list):
            findings.extend(result)
        else:
            findings.append(result)
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
    # v2 unit ⑦: set_vehicle_composition_share added to the object_not_found
    # / _TOOL_TARGET_SPEC lockstep table -- this only covers the
    # COMPOSITION-level lookup (vehicle_compositions object class, keyed by
    # composition no); the SECOND-level lookup (does veh_type_no exist
    # WITHIN that composition) has no single flat id to check against a
    # single object list the way every other tool here does, so it is a
    # dedicated check (vehicle_composition_entry_not_found, below) --
    # mirrors set_route_relative_flow's own split exactly.
    "set_vehicle_composition_share": {
        "object_class": "vehicle_compositions",
        "id_field": "no",
        "name_field": "name",
        "param": "composition_no",
    },
    # v2a unit ⑧: set_des_speed_decision added to the object_not_found /
    # _TOOL_TARGET_SPEC lockstep table -- this only covers the PRIMARY
    # decision-level lookup (des_speed_decisions object class, keyed by
    # decision no, param "decision_no"); the SECOND-level lookup (does
    # veh_class_no have an assignment WITHIN that decision) AND the entirely
    # separate RESTORE-decision lookup (restore_decision_no, when the write
    # is paired -- this table only ever names ONE param per tool) are both
    # dedicated checks (des_speed_decision_entry_not_found, below) --
    # mirrors set_route_relative_flow's/set_vehicle_composition_share's own
    # split, extended for the second target a paired write can carry.
    "set_des_speed_decision": {
        "object_class": "des_speed_decisions",
        "id_field": "no",
        "name_field": "name",
        "param": "decision_no",
    },
    # v2a unit ⑨: set_signal_green_split added to the object_not_found /
    # _TOOL_TARGET_SPEC lockstep table -- this only covers the
    # CONTROLLER-level lookup (signals object class, keyed by sc_no); the
    # deeper lookups (does program_no exist on that controller, does sg_no
    # exist in that program, is the split shape editable at all) have no
    # single flat id the generic machinery can check, so they are a
    # dedicated check (signal_program_split_not_found, below) -- the same
    # split routes/composition entries/des-speed entries established,
    # extended to a THREE-part identity.
    "set_signal_green_split": {
        "object_class": "signals",
        "id_field": "sc_no",
        "name_field": "sc_name",
        "param": "sc_no",
    },
    # QUEUE 3 unit 2: set_priority_rule_gap_headway added to the
    # object_not_found / _TOOL_TARGET_SPEC lockstep table -- this only
    # covers the RULE-level lookup (priority_rules object class, keyed by
    # priority rule no, param "priority_rule_no"); the deeper lookup (does
    # marker_index exist on that rule) has no single flat id to check, so it
    # is a dedicated check (priority_rule_marker_not_found, below) -- the
    # same split routes/composition entries/des-speed entries/signal-program
    # splits established.
    "set_priority_rule_gap_headway": {
        "object_class": "priority_rules",
        "id_field": "no",
        "name_field": "name",
        "param": "priority_rule_no",
    },
    # QUEUE 3 (v2a) unit 3: set_des_speed_distribution_points added to the
    # object_not_found / _TOOL_TARGET_SPEC lockstep table -- the SAME
    # des_speed_distributions object class des_speed_distribution_not_found
    # already validates FROM the set_des_speed_decision side (does a
    # REFERENCED distribution number exist); this entry covers the OPPOSITE
    # direction (does the distribution being edited DIRECTLY exist), keyed
    # by "no" exactly like every other single-id write target here.
    "set_des_speed_distribution_points": {
        "object_class": "des_speed_distributions",
        "id_field": "no",
        "name_field": "name",
        "param": "distribution_no",
    },
    # QUEUE (v2a) unit: set_vehicle_type_reference added to the
    # object_not_found / _TOOL_TARGET_SPEC lockstep table -- this only
    # covers the VEHICLE-TYPE-level lookup (vehicle_types object class,
    # keyed by vehicle_type_no); whether the proposed target_no exists in
    # the CORRECT target collection for the action's own "field" is a
    # dedicated check (vehicle_type_reference_target_not_found, below) --
    # mirrors des_speed_distribution_not_found's own split from
    # set_des_speed_decision's primary-target lookup.
    "set_vehicle_type_reference": {
        "object_class": "vehicle_types",
        "id_field": "no",
        "name_field": "name",
        "param": "vehicle_type_no",
    },
    # QUEUE (Q3, v2a) unit 5: set_time_distribution_normal AND
    # set_time_distribution_points BOTH added to the object_not_found /
    # _TOOL_TARGET_SPEC lockstep table -- unlike every prior pair of sibling
    # write tools in this table, both target the SAME single-token identity
    # (time_distributions object class, keyed by "no") since a time
    # distribution has one flat "no" regardless of which half (mean/stdDev
    # vs points) a given call edits; the TYPE-appropriateness of which verb
    # is being called against which distribution is a SEPARATE dedicated
    # check (time_distribution_type_mismatch, below) -- mirrors the
    # des_speed_distribution_not_found/set_des_speed_decision split's own
    # "existence is generic, appropriateness is dedicated" precedent.
    "set_time_distribution_normal": {
        "object_class": "time_distributions",
        "id_field": "no",
        "name_field": "name",
        "param": "time_distribution_no",
    },
    "set_time_distribution_points": {
        "object_class": "time_distributions",
        "id_field": "no",
        "name_field": "name",
        "param": "time_distribution_no",
    },
    # QUEUE 3 unit 6: duplicate_driving_behavior/set_driving_behavior_parameter
    # added to the object_not_found / _TOOL_TARGET_SPEC lockstep table -- both
    # target the SAME single-token identity (driving_behaviors object class,
    # keyed by "no"): duplicate's source_no must already exist, and
    # set_driving_behavior_parameter's behavior_no must already exist
    # (whether it is SAFE to edit in place -- i.e. has zero referencing
    # consumers -- is the separate, dedicated
    # driving_behavior_shared_object_edit check, below).
    "duplicate_driving_behavior": {
        "object_class": "driving_behaviors",
        "id_field": "no",
        "name_field": "name",
        "param": "source_no",
    },
    "set_driving_behavior_parameter": {
        "object_class": "driving_behaviors",
        "id_field": "no",
        "name_field": "name",
        "param": "behavior_no",
    },
    # QUEUE 3 unit 6: set_link_behavior_type_driving_behavior added to the
    # object_not_found / _TOOL_TARGET_SPEC lockstep table -- this only covers
    # the LINK-BEHAVIOR-TYPE-level lookup (link_behavior_types object class,
    # keyed by "no", param "link_behavior_type_no"); the deeper lookups (does
    # veh_class_no have an EXISTING override to reassign; does the PROPOSED
    # driving_behavior_no target exist) have no single flat id the generic
    # machinery can check, so they are dedicated checks
    # (link_behavior_type_veh_class_override_not_found,
    # link_behavior_type_driving_behavior_target_not_found, below) -- the
    # same split routes/composition entries/des-speed entries/signal-program
    # splits/priority-rule markers established.
    "set_link_behavior_type_driving_behavior": {
        "object_class": "link_behavior_types",
        "id_field": "no",
        "name_field": "name",
        "param": "link_behavior_type_no",
    },
    # v2b unit (1): set_lane_closure added to the object_not_found /
    # _TOOL_TARGET_SPEC lockstep table -- this only covers the LINK-level
    # lookup (links object class, keyed by "id", param "link_id"); whether
    # lane_index actually exists on that link has no single flat id the
    # generic machinery can check (a lane has no identity of its own -- see
    # tools_network._parse_link_lanes's docstring), so that deeper check is
    # NOT a dedicated constraints.py check today (documented gap, closest
    # verifiable subset per the roadmap's gap protocol) -- it is enforced by
    # services.vissim_com.VissimController._lane_by_index, which fails
    # CLOSED with the link's real available lane indices (verified live,
    # backend/tools/probe_lane_closure.py). A future unit can add a
    # dedicated lane_not_found_on_link preflight check mirroring
    # route_not_found_in_decision's own split from this table.
    "set_lane_closure": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_id",
    },
}

_OBJECT_CLASS_LABELS: dict[str, dict[str, str]] = {
    "vehicle_inputs": {"ko": "차량 입력", "en": "vehicle input", "ja": "車両インプット"},
    "signals": {"ko": "신호제어기", "en": "signal controller", "ja": "信号制御機"},
    "links": {"ko": "링크", "en": "link", "ja": "リンク"},
    "conflict_areas": {"ko": "충돌구간", "en": "conflict area", "ja": "錯綜区間"},
    "routing_decisions": {"ko": "차량 경로 결정", "en": "vehicle routing decision", "ja": "車両経路決定"},
    "vehicle_compositions": {"ko": "차량 조성", "en": "vehicle composition", "ja": "車両構成"},
    "des_speed_decisions": {"ko": "희망속도결정", "en": "desired speed decision", "ja": "希望速度決定"},
    "priority_rules": {"ko": "우선순위 규칙", "en": "priority rule", "ja": "優先ルール"},
    "des_speed_distributions": {"ko": "희망속도분포", "en": "desired-speed distribution", "ja": "希望速度分布"},
    "vehicle_types": {"ko": "차량 유형", "en": "vehicle type", "ja": "車両タイプ"},
    "time_distributions": {"ko": "시간분포", "en": "time distribution", "ja": "時間分布"},
    "driving_behaviors": {"ko": "행동유형", "en": "driving behavior", "ja": "行動タイプ"},
    "link_behavior_types": {"ko": "링크 행동유형", "en": "link behavior type", "ja": "リンク行動タイプ"},
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

#: Bounded envelope for the fuzzy-match fallback below (standing rule --
#: suggestion lists reach LLM prompts, see the observation-budget incident).
#: The real Murdoch model shares one name across groups of 3 vehicle inputs
#: (one per vehicle class -- e.g. 107/207/307 are all "Jennalup ST_WB"), so a
#: single matched name can legitimately expand to 3 owners. 10 comfortably
#: fits that common single-group case with room for a couple more matches
#: before truncating; a rarer multi-group match may get cut off mid-group,
#: which is an acceptable trade-off for keeping the list bounded.
#:
#: v2 queue unit 2 (2026-07-31) re-measured this cap after enriching every
#: candidate from a bare id string to a disambiguator dict (this unit's own
#: fix, below) -- mirrors tools_network.py's identical re-measurement of its
#: own ``_CANDIDATE_CAP`` verbatim. MEASURED (json.dumps, compact
#: separators, 3-digit ids): 10 bare id strings is 61 bytes; 10 {id,name}
#: pairs (every object class except vehicle inputs) is 331 bytes; 10 FULL
#: vehicle-input dicts (id+name+link+composition_no+composition_name, using
#: the REAL link-620 values -- "Jennalup ST_WB"/"Class2-5(SLK
#: 1.47_7-8AM&3-4PM)") is ~1140 bytes. Against the 12000-char most-recent-
#: observation budget (planner.py) that is under 10% even in the worst case.
#: The cap stays 10 for the same reason tools_network.py's does: the
#: absolute numbers are small relative to the budget that matters most (the
#: refusal the model is reacting to right now), so shrinking it would trade
#: away real disambiguation for headroom the budget does not need. Older
#: history entries render under the tighter 300-char snippet budget where a
#: full 10-entry enriched list WILL trigger the existing item-boundary/
#: compact-summary truncation tiers -- by design, not a new failure mode.
_CANDIDATE_CAP = 10


def _candidate_disambiguators(object_class: str, obj: dict[str, Any]) -> dict[str, Any]:
    """Extra, type-specific fields carried on an ``object_not_found``
    candidate purely so the caller can tell same-named objects apart.

    Deliberate mirror of ``tools_network.py``'s identically-named helper
    (this module is intentionally stdlib-only and does not import
    tools_network.py — see the module docstring), restricted to the one
    object class known to share names across siblings on the real client
    model: vehicle inputs (one name per approach, one input per vehicle
    class sharing it -- e.g. 107/207/307 are ALL "Jennalup ST_WB"). Only
    non-empty fields are attached, so every other object class's candidate
    shape stays exactly ``{id, name}`` -- nothing is fabricated for object
    classes that carry no such field."""
    if object_class != "vehicle_inputs":
        return {}
    extra: dict[str, Any] = {}
    link = str(obj.get("link", "") or "")
    if link:
        extra["link"] = link
    composition_no = str(obj.get("composition_no", "") or "")
    if composition_no:
        extra["composition_no"] = composition_no
    composition_name = str(obj.get("composition_name", "") or "")
    if composition_name:
        extra["composition_name"] = composition_name
    if obj.get("composition_varies_by_interval"):
        extra["composition_nos"] = list(obj.get("composition_nos") or [])
    return extra


def _format_candidate_label(candidate: dict[str, Any]) -> str:
    """Render one enriched candidate dict as ``id(name)`` for the human-
    facing message text -- e.g. ``"107(Jennalup ST_WB)"``. A nameless
    candidate (or one whose name is literally its id) renders as just the
    id, matching #1's pre-enrichment id-only display for object classes
    that carry no meaningful name."""
    cid = str(candidate.get("id", ""))
    name = str(candidate.get("name", "") or "")
    if not name or name == cid:
        return cid
    return f"{cid}({name})"


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
    # back to the owning object's id(s): suggested_fix is machine-usable (a
    # caller retries with suggested_fix[0]["id"] as input_no/sc_no -- see the
    # 2026-07-31 enrichment note below), so a name string leaking through
    # would break that retry.
    #
    # A name is not always unique -- see _CANDIDATE_CAP's comment above. id
    # strings stay 1:1 with themselves (ids are unique); only name strings
    # fan out to a list of every owner that shares them, built in
    # shadow-model order for a stable, reproducible result. If some other
    # object's id string happens to collide with a shared name (pathological,
    # not expected on real data), the id claim wins and is not merged into --
    # ids stay 1:1.
    owner_ids_by_string: dict[str, list[str]] = {}
    id_claimed: set[str] = set()
    match_pool: list[str] = []
    entry_by_id: dict[str, dict[str, Any]] = {}
    for obj in objects:
        obj_id = str(obj.get(spec["id_field"], ""))
        if obj_id and obj_id not in entry_by_id:
            entry_by_id[obj_id] = obj
        if obj_id and obj_id not in owner_ids_by_string:
            match_pool.append(obj_id)
            owner_ids_by_string[obj_id] = [obj_id]
            id_claimed.add(obj_id)
        name = obj.get(spec["name_field"])
        if name:
            name_str = str(name)
            if name_str not in owner_ids_by_string:
                match_pool.append(name_str)
                owner_ids_by_string[name_str] = [obj_id]
            elif name_str not in id_claimed and obj_id not in owner_ids_by_string[name_str]:
                owner_ids_by_string[name_str].append(obj_id)

    # 2026-07-31 (live-demo defect, v2 queue unit 2): candidates used to be
    # bare id strings -- the exact same anonymous-candidate trap resolve_
    # object's miss path had (see tools_network.py's _difflib_candidates),
    # one level down. A refusal whose suggested_fix carries six ids from two
    # same-named vehicle-input trios gave the model no way to choose; it
    # re-issued the identical write, and downstream callers (the LLM
    # prompt, health-check handoffs) had the same blind spot. Every
    # candidate is now {id, name} plus whatever _candidate_disambiguators
    # adds for that object class, matching resolve_object's own candidate
    # shape as closely as this module's stdlib-only boundary allows.
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for match in difflib.get_close_matches(target_id, match_pool, n=5):
        for owner_id in owner_ids_by_string[match]:
            if owner_id in seen_ids:
                continue
            seen_ids.add(owner_id)
            owner_obj = entry_by_id.get(owner_id, {})
            owner_name = str(owner_obj.get(spec["name_field"], "") or "")
            candidates.append(
                {
                    "id": owner_id,
                    "name": owner_name,
                    **_candidate_disambiguators(spec["object_class"], owner_obj),
                }
            )
            if len(candidates) >= _CANDIDATE_CAP:
                break
        if len(candidates) >= _CANDIDATE_CAP:
            break

    if candidates:
        candidate_labels = [_format_candidate_label(c) for c in candidates]
        candidates_ko, candidates_en, candidates_ja = _capped_join(
            candidate_labels, "개", "candidates", "件"
        )
    else:
        candidates_ko = candidates_en = candidates_ja = "-"

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
            candidates_ko=candidates_ko,
            candidates_en=candidates_en,
            candidates_ja=candidates_ja,
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
            "set_vehicle_composition_share",
            "set_des_speed_decision",
            "set_signal_green_split",
            "set_priority_rule_gap_headway",
            # QUEUE 3 (v2a) unit 3 -- easy to forget (unit ② caught this).
            "set_des_speed_distribution_points",
            # QUEUE (v2a) unit -- vehicle type properties.
            "set_vehicle_type_reference",
            # QUEUE (Q3, v2a) unit 5 -- dwell-time distributions, both verbs
            # (easy to forget -- unit ② caught this).
            "set_time_distribution_normal",
            "set_time_distribution_points",
            # QUEUE 3 unit 6 -- driving-behavior duplicate-then-edit, all
            # three verbs (easy to forget -- unit ② caught this).
            "duplicate_driving_behavior",
            "set_driving_behavior_parameter",
            "set_link_behavior_type_driving_behavior",
        ],
        phase="preflight",
        tier="refuse",
        check=_check_object_not_found,
        message_ko="대상 ID '{target_id}'을(를) {object_class_ko} 목록에서 찾을 수 없습니다. "
        "오타이거나 잘못된 ID일 수 있습니다. 후보: {candidates_ko}.",
        message_en="Target id '{target_id}' was not found among {object_class_en} objects. "
        "It may be a typo. Candidates: {candidates_en}.",
        message_ja="対象ID '{target_id}' は {object_class_ja} の一覧に見つかりませんでした。"
        "タイプミスの可能性があります。候補: {candidates_ja}。",
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
#
# SIBLING-BLINDNESS FIXED (sibling-disambiguation unit, 2026-07-28). The
# original check compared ONE input's proposed volume against the WHOLE
# link's capacity and never looked at the other vehicle inputs feeding the
# same link -- which on the real client model is never fewer than two others:
# Murdoch Drive has 51 vehicle inputs in 17 groups of 3 (one per vehicle
# class), so EVERY possible target of this check was under-counted, 100% of
# the time.
#
# Measured silent failure it let through (runtime/uploads/current/
# current.inpx, link 620 = 1 lane, capacity 1900): inputs 107/207/307 carry
# 127.3 / 19.0 / 0.95 veh/h. Proposing 1881 on 107 passed with ZERO findings
# while the link's real post-write demand would be 1900.95 -- over capacity.
# The whole silent window was any proposed V in (1880.05, 1900.0]. And even
# when the old check DID fire it reported the wrong numbers: at the user's
# real 6000 request it said "6000 exceeds 1900" when the link would actually
# carry 6019.95, and its suggested_fix (max_recommended_volume = 1900) was
# ITSELF an overflow -- 1900 + 19.95 = 1919.95.
#
# The fix is the sibling-sum pattern this file already uses four times over
# (route_relative_flow_split_confirm, vehicle_composition_share_
# redistribution_gap, signal_cycle_change_may_break_corridor_sync,
# signal_green_split_change_confirm): enumerate every OTHER object sharing
# the host, sum, and report the breakdown so the number is auditable rather
# than asserted. The target itself is excluded by ``no`` so its CURRENT
# volume is never double-counted alongside its PROPOSED one.
#
# INTERVAL-BLINDNESS FIXED (2026-07-28, second pass). The sibling-sum fix
# above still read every sibling's TOP-LEVEL ``volume`` field, which
# ``tools_network._parse_vehicle_inputs`` DEFINES to be interval 1's value
# ALWAYS -- so a write that names ``interval_no`` > 1 got compared against
# interval 1's sibling demand, not the interval actually being written.
# Measured live on link 620 (siblings 207+307's real per-interval totals are
# ``{1: 19.95, 2: 21.0, 3: 21.0, 4: 21.0, 5: 21.0, 6: 18.9}``): a preflight
# check at ``interval_no`` 1, 2, or 6 reported ``sibling_volume_total=19.95``
# and ``link_total_volume=6019.95`` EVERY time -- a "link total" that exists
# at no point in time (the proposed interval's value plus interval 1's
# sibling values). Fixed by reading each sibling's volume via
# :func:`_volume_at_interval` (interval-aware) instead of its top-level
# ``volume`` field; see that function's own docstring for the exact
# fallback contract that keeps every pre-existing (interval-naive) fixture
# in this test file passing byte-for-byte.
# ---------------------------------------------------------------------------

_ROUGH_LANE_CAPACITY_VEH_PER_H = 1900


def _sibling_vehicle_inputs(
    shadow_model: dict[str, Any], link_id: str, input_no: str
) -> list[dict[str, Any]]:
    """Every vehicle input on ``link_id`` OTHER than ``input_no``, in shadow-
    model order. The one place "sibling vehicle input" is defined, shared by
    #3 and by ``vehicle_input_siblings_share_link`` below so the two can never
    disagree about who the siblings are."""
    if not link_id:
        return []
    return [
        vi
        for vi in shadow_model.get("vehicle_inputs", [])
        if str(vi.get("link", "")) == link_id and str(vi.get("no", "")) != input_no
    ]


def _interval_no_default_1(params: dict[str, Any]) -> int:
    """``params["interval_no"]``, defaulting to 1 -- the SAME default
    ``set_vehicle_input_volume`` itself uses (``tools_edit.py``'s
    ``SetVehicleInputVolumeParams.interval_no: int = Field(default=1, ...)``,
    verified against that field, not assumed) -- so a write that omits
    ``interval_no`` and a check evaluating that same write always agree on
    which interval is in play. Mirrors the identical ``params.get(
    "interval_no", 1)`` + int-coerce-with-fallback pattern this module
    already uses for #44/route_relative_flow_interval_out_of_range/
    set_route_relative_flow's own interval_no handling."""
    raw_interval_no = params.get("interval_no", 1)
    interval_no = _coerce_number(raw_interval_no, cast=int)
    return interval_no if interval_no is not None else 1


def _volume_at_interval(vehicle_input: dict[str, Any], interval_no: int) -> float | None:
    """The vehicle input's OWN volume AT ``interval_no`` (1-based) -- the
    interval-blindness fix both #3 and ``vehicle_input_siblings_share_link``
    needed (2026-07-28): both used to read the top-level ``volume`` field,
    which ``tools_network._parse_vehicle_inputs`` DEFINES to be interval 1's
    value ALWAYS, regardless which interval a write actually targets.

    Reads ``volumes[]`` (the shadow model's real per-interval truth) first.
    Falls back to the top-level ``volume`` field ONLY for ``interval_no ==
    1`` when the input carries NO per-interval data at all (``volumes``
    empty/absent -- the synthetic demo network, and every fixture in this
    test suite written before per-interval volumes existed): that field is
    DEFINED to mirror interval 1 exactly in that shape, never a guess, so
    every existing interval-naive fixture keeps working byte-for-byte.

    Any other miss -- ``interval_no > 1`` with no matching entry, i.e. this
    input has FEWER intervals than the one being asked about -- returns
    ``None``: honestly unknown, not silently 0. The caller's existing
    ``sibling_volume_unknown_count``/``sibling_unknown`` convention (see
    :func:`_sibling_volume_total`) carries that forward into the message and
    ``details`` rather than understating demand."""
    volumes = vehicle_input.get("volumes") or []
    for entry in volumes:
        if _coerce_number(entry.get("interval_no"), cast=int) == interval_no:
            return _coerce_number(entry.get("volume"))
    if interval_no == 1 and not volumes:
        return _coerce_number(vehicle_input.get("volume"))
    return None


def _sibling_volume_total(siblings: list[dict[str, Any]], interval_no: int = 1) -> tuple[float, int]:
    """``(summed volume AT interval_no, number of siblings whose volume at
    that interval was unknown)``.

    INTERVAL-AWARE (2026-07-28 fix -- was interval-blind; see #3's own
    comment block above for the measured false-total windows this produced).
    Reads each sibling's volume via :func:`_volume_at_interval`, not its
    top-level ``volume`` field (interval 1's value BY DEFINITION), so a
    write to interval 6 is compared against interval 6's sibling demand, not
    interval 1's. ``interval_no`` defaults to 1 -- both callers pass their
    own resolved value; the default only matters for a caller (or test) that
    doesn't care which interval, matching the pre-fix behavior exactly.

    A sibling with no known volume at ``interval_no`` (missing data, or the
    sibling genuinely has FEWER intervals than the one being asked about)
    contributes 0 to the sum -- so the total is a LOWER BOUND, never an
    invention -- and the count is returned so the message can say so out
    loud instead of quietly presenting a partial sum as complete."""
    total = 0.0
    unknown = 0
    for sibling in siblings:
        value = _volume_at_interval(sibling, interval_no)
        if value is None:
            unknown += 1
            continue
        total += value
    return round(total, 4), unknown


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
    interval_no = _interval_no_default_1(params)

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
    siblings = _sibling_vehicle_inputs(shadow_model, link_id, input_no)
    sibling_total, sibling_unknown = _sibling_volume_total(siblings, interval_no)
    link_total = round(volume + sibling_total, 4)
    if link_total <= capacity:
        return None

    # FIX 3 (2026-07-28): when the siblings ALONE already meet or exceed
    # capacity, no value of THIS input -- not even 0 -- can bring the link
    # total back under capacity. The old `max(capacity - sibling_total, 0.0)`
    # clamp presented 0.0 as a working ceiling in that state; re-running the
    # check at exactly that recommendation still fired (0 + sibling_total >
    # capacity), because it IS still an overflow. Never emit a recommendation
    # that fails its own check: state the impossibility instead of a number
    # that doesn't work.
    unfixable_by_this_input_alone = sibling_total > capacity
    max_recommended_volume = None if unfixable_by_this_input_alone else round(max(capacity - sibling_total, 0.0), 4)

    if not siblings:
        breakdown_ko = f"이 링크의 차량 입력은 '{input_no}' 하나뿐이므로 구간 {interval_no}의 링크 총 " \
            f"수요는 요청값 그대로 {link_total} veh/h입니다."
        breakdown_en = f"Input '{input_no}' is the only vehicle input on this link, so interval " \
            f"{interval_no}'s link total is the requested value itself, {link_total} veh/h."
        breakdown_ja = f"このリンクの車両インプットは '{input_no}' のみのため、区間{interval_no}の" \
            f"リンク総需要は要求値そのままの {link_total} veh/h です。"
    else:
        breakdown_ko = f"같은 링크의 다른 차량 입력 {len(siblings)}개의 구간 {interval_no} 합계 " \
            f"{sibling_total} veh/h를 더하면 링크 총 수요는 {volume} + {sibling_total} = " \
            f"{link_total} veh/h입니다."
        breakdown_en = f"Adding the {len(siblings)} other vehicle input(s) on the same link at " \
            f"interval {interval_no} ({sibling_total} veh/h combined), the link total becomes " \
            f"{volume} + {sibling_total} = {link_total} veh/h."
        breakdown_ja = f"同じリンクの他の車両インプット {len(siblings)} 件の区間{interval_no}の合計 " \
            f"{sibling_total} veh/h を加えると、リンク総需要は {volume} + {sibling_total} = " \
            f"{link_total} veh/h です。"

    if sibling_unknown:
        breakdown_ko += f" (다른 차량 입력 {sibling_unknown}개는 구간 {interval_no}의 볼륨을 알 수 " \
            "없어(해당 구간이 없거나 값이 없음) 0으로 계산했으므로 실제 총 수요는 이보다 클 수 " \
            "있습니다.)"
        breakdown_en += f" ({sibling_unknown} sibling input(s) have no known volume at interval " \
            f"{interval_no} (missing that interval, or a missing value) and were counted as 0, so " \
            "the real link total may be higher.)"
        breakdown_ja += f" (他の車両インプット {sibling_unknown} 件は区間{interval_no}のボリュームが" \
            "不明(その区間がない、または値がない)のため0として計算しており、実際の総需要はこれより" \
            "大きい可能性があります。)"

    if unfixable_by_this_input_alone:
        excess_from_siblings = round(sibling_total - capacity, 4)
        fix_note_ko = f"다른 차량 입력들만으로도 이미 구간 {interval_no}의 용량 {capacity} veh/h를 " \
            f"{excess_from_siblings} veh/h 초과했으므로, '{input_no}'을(를) 아무리 줄여도(0까지 " \
            "포함) 이 초과는 해결되지 않습니다. 다른 차량 입력의 볼륨을 줄이거나 링크의 차로 수" \
            "(용량)를 늘려야 합니다."
        fix_note_en = f"The other vehicle input(s) ALONE already exceed interval {interval_no}'s " \
            f"capacity {capacity} veh/h by {excess_from_siblings} veh/h, so reducing '{input_no}' " \
            "-- even to 0 -- cannot fix this. Either the other vehicle inputs' volumes must be " \
            "reduced, or the link's capacity (lane count) must be increased."
        fix_note_ja = f"他の車両インプットだけで既に区間{interval_no}の容量 {capacity} veh/h を " \
            f"{excess_from_siblings} veh/h 超過しているため、'{input_no}' をどれだけ(0まで)減らして" \
            "もこの超過は解消されません。他の車両インプットのボリュームを減らすか、リンクの車線数" \
            "(容量)を増やす必要があります。"
        suggested_fix: dict[str, Any] | None = {
            "max_recommended_volume": None,
            "fixable_by_reducing_this_input": False,
            "sibling_volume_total": sibling_total,
            "capacity": capacity,
            "excess_from_siblings_alone": excess_from_siblings,
        }
    else:
        fix_note_ko = f"다른 차량 입력을 그대로 두려면 이 입력의 구간 {interval_no} 권장 상한은 " \
            f"{max_recommended_volume} veh/h입니다."
        fix_note_en = "To leave the other vehicle inputs untouched, the recommended ceiling for " \
            f"THIS input at interval {interval_no} is {max_recommended_volume} veh/h."
        fix_note_ja = f"他の車両インプットをそのままにする場合、このインプットの区間{interval_no}の" \
            f"推奨上限は {max_recommended_volume} veh/h です。"
        suggested_fix = {"max_recommended_volume": max_recommended_volume}

    return Finding(
        constraint_id="latent_demand_overflow",
        tier="warn",
        phase="preflight",
        messages=_render(
            "latent_demand_overflow",
            input_no=input_no,
            volume=raw_volume,
            capacity=capacity,
            link_id=link_id,
            lanes=lanes,
            interval_no=interval_no,
            link_total=link_total,
            breakdown_ko=breakdown_ko,
            breakdown_en=breakdown_en,
            breakdown_ja=breakdown_ja,
            fix_note_ko=fix_note_ko,
            fix_note_en=fix_note_en,
            fix_note_ja=fix_note_ja,
        ),
        suggested_fix=suggested_fix,
        details={
            "volume": volume,
            "capacity": capacity,
            "link_id": link_id,
            "lanes": lanes,
            "interval_no": interval_no,
            "sibling_input_nos": [str(s.get("no", "")) for s in siblings],
            "sibling_volume_total": sibling_total,
            "sibling_volume_unknown_count": sibling_unknown,
            "link_total_volume": link_total,
            "max_recommended_volume": max_recommended_volume,
            "fixable_by_reducing_this_input": not unfixable_by_this_input_alone,
        },
    )


register(
    Constraint(
        id="latent_demand_overflow",
        applies_to=["set_vehicle_input_volume"],
        phase="preflight",
        tier="warn",
        check=_check_latent_demand_overflow,
        message_ko="차량 입력 '{input_no}'에 요청한 볼륨 {volume} veh/h (구간 {interval_no}). "
        "{breakdown_ko} 이는 링크 '{link_id}'(차로 {lanes}개)의 대략적 용량 {capacity} veh/h를 "
        "초과합니다. 초과 수요는 화면에 보이지 않는 잠재 수요로 대기하게 됩니다. {fix_note_ko}",
        message_en="Requested volume {volume} veh/h for vehicle input '{input_no}' (interval "
        "{interval_no}). {breakdown_en} That exceeds the rough capacity {capacity} veh/h of link "
        "'{link_id}' ({lanes} lanes). Excess demand will queue invisibly as latent demand. "
        "{fix_note_en}",
        message_ja="車両インプット '{input_no}' に指定した交通量 {volume} veh/h(区間{interval_no})。"
        "{breakdown_ja}これはリンク '{link_id}'(車線数 {lanes})のおおよその容量 {capacity} veh/h を"
        "超えています。超過分は目に見えない潜在需要として滞留します。{fix_note_ja}",
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
    # v2a unit ⑨: set_signal_green_split joins the gate -- the SAME two-type
    # editable population (FIXEDTIME via the COM-writable IntSupplyData
    # program blob, FIXEDTIMESIMPLE via plain EndRed/EndGreen attributes,
    # both live-verified by backend/tools/probe_signal_programs.py); every
    # external-controller type keeps refusing here by design.
    if tool not in ("set_signal_cycle", "set_signal_offset", "set_signal_green_split"):
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
        applies_to=["set_signal_cycle", "set_signal_offset", "set_signal_green_split"],
        phase="preflight",
        tier="refuse",
        check=_check_signal_controller_type_unsupported,
        message_ko="신호제어기 '{sc_no}'의 컨트롤러 타입({controller_type})은 지원되지 않습니다. "
        "고정시간(fixed-time) 제어기만 주기/옵셋/녹색시간을 안전하게 수정할 수 있습니다.",
        message_en="Signal controller '{sc_no}' has controller type '{controller_type}', which "
        "is not supported. Only fixed-time controllers can have cycle/offset/green splits "
        "edited safely.",
        message_ja="信号制御機 '{sc_no}' のコントローラタイプ('{controller_type}')はサポートされて"
        "いません。サイクル/オフセット/青時間を安全に編集できるのは固定時間式(fixed-time)制御機"
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
# decision immediately after to restore the original speed.
#
# WOKEN FOR REAL by v2a unit ⑧ (2026-07-27): originally registered against a
# placeholder tool name (``add_desired_speed_decision``) and param
# (``paired_restore_decision_id``) before any real desired-speed-decision
# write tool existed. The real tool is ``set_des_speed_decision`` (EDITS an
# existing decision's assignment -- desired-speed decisions have no COM
# Add/Remove, so "add" was never accurate either) with a real param,
# ``restore_decision_no``. Retargeted in place (same constraint id, same
# warn-tier "keep it simple" shape -- no shadow-model existence check added:
# this fires purely off whether the write NAMES a restore target, exactly as
# before, deliberately not gated on whether that target actually resolves,
# since a separate refuse-tier check (des_speed_decision_entry_not_found)
# already owns that and a co-occurring warn-tier finding on an otherwise-
# refused action is harmless) rather than adding a second, near-duplicate
# constraint -- this IS the catalog's own #30, not a new item.
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
    if action.get("tool") != "set_des_speed_decision":
        return None

    params = action.get("params", {})
    if params.get("restore_decision_no"):
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
        applies_to=["set_des_speed_decision"],
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
# changes nothing, and nothing reports the no-op.
#
# GAP CLOSED in QUEUE 3 unit 6 (2026-07-31): shadow_model["driving_behaviors"]
# is now populated for real (tools_network._parse_driving_behaviors, keyed by
# "no" not "id" -- see that field's own docstring) and set_driving_behavior_
# parameter is a real, shipped tool. Two fixes made live-data-necessary:
# (1) _MODEL_74_ONLY_PARAMS was WRONG -- the pre-existing placeholder names
# ("axhze"/"bxadd"/"bxmult") never matched any real COM/XML attribute;
# verified live (backend/tools/probe_driving_behaviors.py) the real Wiedemann
# 74 scalar family is "ax"/"bx_add"/"bx_mult" (vissim_com.VissimController.
# DRIVING_BEHAVIOR_PARAM_FIELDS' own canonical names). (2) the mismatch
# condition compared against the OPPOSITE explicit model only (74-vs-99
# symmetric pair); real data has a THIRD car_following_model value this
# codebase never anticipated -- "NOINTERACTION" (Footpath/pedestrian
# behaviors, car_following_model None per the shadow-model parser's own
# int-mapping contract, live-verified 1 of 15 on the real Murdoch production
# model) -- on which NEITHER a W74 NOR a W99 parameter is ever active, so the
# condition is now "not the REQUIRED model" (covers None too), not "the
# opposite explicit model".
# ---------------------------------------------------------------------------

_MODEL_99_ONLY_PARAMS = {f"cc{n}" for n in range(0, 10)}  # Wiedemann 99 (freeway)
_MODEL_74_ONLY_PARAMS = {"ax", "bx_add", "bx_mult"}  # Wiedemann 74 (urban)


def _check_driving_behavior_parameter_model_mismatch(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None

    params = action.get("params", {})
    behavior_id = str(params.get("behavior_no", params.get("behavior_id", "")))
    parameter = str(params.get("parameter", "")).lower()

    behaviors = shadow_model.get("driving_behaviors")
    if not behaviors:
        return None
    behavior = _find_by_field(behaviors, "no", behavior_id)
    if behavior is None:
        return None

    model = behavior.get("car_following_model")
    mismatched = (parameter in _MODEL_99_ONLY_PARAMS and model != 99) or (
        parameter in _MODEL_74_ONLY_PARAMS and model != 74
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
            model=behavior.get("car_follow_mod_type", model),
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
# Catalog §4 "'More trucks' redistributes rather than adds". ORIGINAL
# premise (pre-v2-unit-⑦, before set_vehicle_composition_share existed or
# any COM verification had been done): "vehicle mixes are relative shares
# normalized to 100%; raising the truck share LOWERS the car share" — i.e.
# that Vissim RENORMALIZES sibling entries on write. CORRECTED live in v2
# unit ⑦ (2026-07-27, backend/tools/probe_vehicle_compositions.py): this was
# a plausible-looking guess that turned out WRONG, the same landmine class as
# CycTm/NodeRes in docs/phase2/DECISIONS.md. Verified fact: RelFlow is a RAW
# weight — writing one entry's RelFlow leaves every sibling entry's own
# stored RelFlow completely UNCHANGED (confirmed live + after a SaveNetAs +
# fresh COM reload); the actual composition share is only ever a DERIVED
# number, computed at simulation time (and for display) as this entry's
# RelFlow divided by the SUM of every entry's RelFlow in the SAME
# composition — exactly mirroring set_route_relative_flow's own
# route_relative_flow_split_confirm (#48, v2 unit ⑥). This check now mirrors
# that one's shape: it ALWAYS fires (whenever the target resolves; a missing
# composition/vehType is #1's/vehicle_composition_entry_not_found's job) so
# the composition's current entry count, the estimated resulting share
# (computed from the OTHER entries' CURRENT relFlow plus this proposed new
# value), and an explicit single-entry caveat are unconditionally echoed
# into the approval card's warning list — regardless of whether the
# planner's own free-text summary happens to explain the normalization.
# Deliberately warn-tier, not refuse: a confirmation aid, not a problem with
# the write itself.
# ---------------------------------------------------------------------------


def _rel_flow_for_veh_type(entries: list[dict[str, Any]], veh_type_no: str) -> float | None:
    entry = _find_by_field(entries, "veh_type", veh_type_no)
    if entry is None:
        return None
    return _coerce_number(entry.get("rel_flow"))


def _check_vehicle_composition_share_redistribution_gap(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_composition_share":
        return None

    params = action.get("params", {})
    composition_no = str(params.get("composition_no", ""))
    veh_type_no = str(params.get("veh_type_no", ""))
    raw_new_rel_flow = params.get("rel_flow")
    if raw_new_rel_flow is None:
        return None
    new_rel_flow = _coerce_number(raw_new_rel_flow)
    if new_rel_flow is None:
        return None

    composition = _find_by_field(shadow_model.get("vehicle_compositions", []), "no", composition_no)
    if composition is None:
        return None  # missing target is object_not_found's job (#1)
    entries = composition.get("entries", [])
    target = _find_by_field(entries, "veh_type", veh_type_no)
    if target is None:
        return None  # missing target is vehicle_composition_entry_not_found's job

    old_rel_flow = _coerce_number(target.get("rel_flow"))
    entry_count = len(entries)

    sibling_sum = 0.0
    sibling_known = True
    for entry in entries:
        if str(entry.get("veh_type", "")) == veh_type_no:
            continue
        sibling_value = _coerce_number(entry.get("rel_flow"))
        if sibling_value is None:
            sibling_known = False
            break
        sibling_sum += sibling_value

    if entry_count <= 1:
        share_pct: float | None = 100.0
        share_note_ko = "이 조성에는 이 차종 하나뿐이므로, relFlow 값과 무관하게 실제 비율은 항상 " \
            "100%입니다."
        share_note_en = "This composition has only this one vehicle type, so the actual share is " \
            "always 100% regardless of the relFlow value."
        share_note_ja = "この構成にはこの車種しかないため、relFlowの値に関わらず実際の比率は常に" \
            "100%です。"
    elif sibling_known and (sibling_sum + new_rel_flow) > 0:
        share_pct = round((new_rel_flow / (sibling_sum + new_rel_flow)) * 100, 1)
        share_note_ko = f"이 조성의 다른 차종들이 현재 relFlow를 유지한다고 가정하면, 이 차종의 " \
            f"예상 실제 비율은 약 {share_pct}%입니다."
        share_note_en = f"Assuming the composition's other vehicle types keep their current " \
            f"relFlow, this vehicle type's estimated actual share is about {share_pct}%."
        share_note_ja = f"この構成の他の車種が現在のrelFlowを維持すると仮定すると、この車種の推定" \
            f"実際比率は約{share_pct}%です。"
    else:
        share_pct = None
        share_note_ko = "다른 차종의 relFlow 값을 알 수 없어 예상 비율을 계산할 수 없습니다."
        share_note_en = "The resulting share could not be estimated (another vehicle type's " \
            "relFlow is unknown)."
        share_note_ja = "他の車種のrelFlow値が不明なため、予想比率を計算できません。"

    return Finding(
        constraint_id="vehicle_composition_share_redistribution_gap",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_composition_share_redistribution_gap",
            composition_no=composition_no,
            veh_type_no=veh_type_no,
            old_rel_flow=old_rel_flow if old_rel_flow is not None else "?",
            new_rel_flow=raw_new_rel_flow,
            entry_count=entry_count,
            share_note_ko=share_note_ko,
            share_note_en=share_note_en,
            share_note_ja=share_note_ja,
        ),
        suggested_fix=None,
        details={
            "composition_no": composition_no,
            "veh_type_no": veh_type_no,
            "old_rel_flow": old_rel_flow,
            "new_rel_flow": new_rel_flow,
            "entry_count": entry_count,
            "estimated_share_pct": share_pct,
        },
    )


register(
    Constraint(
        id="vehicle_composition_share_redistribution_gap",
        applies_to=["set_vehicle_composition_share"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_composition_share_redistribution_gap,
        message_ko="차량 조성 '{composition_no}'의 차종 '{veh_type_no}' relFlow를 {old_rel_flow} → "
        "{new_rel_flow}(으)로 변경하려 합니다. relFlow는 백분율이 아니라 원시 가중치이며, Vissim은 "
        "같은 조성 안의 모든 차종 relFlow 합계에 대한 비율로 실제 구성 비율을 시뮬레이션 시점에 "
        "계산합니다 — 이 값을 바꿔도 다른 차종 자체의 relFlow는 그대로 남습니다 (이 조성의 차종 수: "
        "{entry_count}개). {share_note_ko}",
        message_en="Proposing to change vehicle composition '{composition_no}' vehicle type "
        "'{veh_type_no}' relFlow from {old_rel_flow} to {new_rel_flow}. relFlow is a RAW weight, "
        "not a percentage — Vissim computes the actual composition split at simulation time as "
        "each vehicle type's share of the SUM of every vehicle type's relFlow in the same "
        "composition, and writing this value leaves every OTHER vehicle type's own relFlow "
        "untouched (this composition has {entry_count} vehicle type(s)). {share_note_en}",
        message_ja="車両構成 '{composition_no}' の車種 '{veh_type_no}' のrelFlowを {old_rel_flow} "
        "から {new_rel_flow} に変更しようとしています。relFlowは百分率ではなく生の重みであり、"
        "Vissimは同じ構成内の全車種のrelFlow合計に対する比率として実際の構成比率をシミュレーション"
        "時に計算します — この値を変更しても他の車種自身のrelFlowはそのまま残ります(この構成の"
        "車種数: {entry_count})。{share_note_ja}",
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
# intended links."
#
# REWRITTEN in QUEUE 3 unit 6 (2026-07-31): the ORIGINAL implementation
# trusted a caller-supplied ``duplicated_before_edit`` boolean — a
# self-reported flag any planner call could simply always set ``True``,
# giving zero real protection. Now that shadow_model["driving_behaviors"] is
# populated for real (tools_network._parse_driving_behaviors), this is a REAL
# reference-count check against the actual reference graph (which
# link-behavior-types — by default assignment or per-vehicle-class override
# — point at this behavior number): the target REFUSES unless its
# reference_count is exactly 0. Per the user-approved safety design ("The
# copy, having zero references, is then freely editable in place" — a
# freshly duplicated behavior always starts at 0), this is a strictly
# stronger, un-gameable version of the same guardrail — there is no flag to
# set, no way to talk the check into skipping. Every consumer (bounded, per
# the observation-budget standing rule) is named in the refusal message, per
# this unit's own task brief. Degrades silently only when the shadow model
# has no driving_behaviors data at all or the target itself does not exist
# (the latter is #1 object_not_found's job).
# ---------------------------------------------------------------------------


def _driving_behavior_consumer_label(consumer: dict[str, Any]) -> str:
    label = str(consumer.get("link_behavior_type_name") or consumer.get("link_behavior_type_no", "?"))
    veh_class_no = consumer.get("veh_class_no")
    if veh_class_no:
        label = f"{label} (veh class {veh_class_no})"
    return label


def _check_driving_behavior_shared_object_edit(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None

    params = action.get("params", {})
    behavior_no = str(params.get("behavior_no", ""))

    behaviors = shadow_model.get("driving_behaviors")
    if not behaviors:
        return None  # no reference data to check against yet
    behavior = _find_by_field(behaviors, "no", behavior_no)
    if behavior is None:
        return None  # missing target is object_not_found's job (#1)

    reference_count = behavior.get("reference_count", 0) or 0
    if reference_count == 0:
        return None  # a fresh, unshared (e.g. just-duplicated) copy is safe to edit in place

    consumers = (behavior.get("referenced_by") or {}).get("consumers", [])
    labels = [_driving_behavior_consumer_label(c) for c in consumers]
    consumers_ko, consumers_en, consumers_ja = _capped_join(labels, "곳", "consumer(s)", "件")

    return Finding(
        constraint_id="driving_behavior_shared_object_edit",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "driving_behavior_shared_object_edit",
            behavior_id=behavior_no,
            reference_count=reference_count,
            consumers_ko=consumers_ko,
            consumers_en=consumers_en,
            consumers_ja=consumers_ja,
        ),
        suggested_fix={
            "hint": "duplicate_driving_behavior first, edit the copy, and retarget only the intended link-behavior-type",
            "reference_count": reference_count,
            "consumers": labels,
        },
        details={"behavior_id": behavior_no, "reference_count": reference_count, "consumers": labels},
    )


register(
    Constraint(
        id="driving_behavior_shared_object_edit",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="refuse",
        check=_check_driving_behavior_shared_object_edit,
        message_ko="행동유형 '{behavior_id}'은(는) {reference_count}개 링크 행동유형이 참조하는 "
        "전역 객체입니다 ({consumers_ko}). 제자리에서 편집하면 이 행동유형을 쓰는 모든 곳이 함께 "
        "바뀝니다 — duplicate_driving_behavior로 먼저 복제한 뒤 그 사본(참조 0개)만 편집하고, "
        "의도한 링크 행동유형에만 재적용하세요.",
        message_en="Behavior '{behavior_id}' is a global object referenced by {reference_count} "
        "link-behavior-type consumer(s) ({consumers_en}). Editing it in place changes every one "
        "of them at once — duplicate_driving_behavior it first, edit the copy (zero "
        "references), and retarget only the intended link-behavior-type.",
        message_ja="行動タイプ '{behavior_id}' は{reference_count}件のリンク行動タイプが参照する"
        "グローバルオブジェクトです({consumers_ja})。その場で編集すると、これを使用するすべての"
        "箇所が同時に変更されます — 先にduplicate_driving_behaviorで複製し、そのコピー(参照0件)"
        "のみを編集して、意図したリンク行動タイプにのみ再適用してください。",
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
    behavior_id = str(params.get("behavior_no", ""))
    parameter = str(params.get("parameter", ""))

    behaviors = shadow_model.get("driving_behaviors")
    if not behaviors:
        return None
    behavior = _find_by_field(behaviors, "no", behavior_id)
    if behavior is None:
        return None

    # CATALOG-GAP: "parameters"/"calibration_reference_value" remain
    # unpopulated by tools_network._parse_driving_behaviors -- no
    # calibration-reference baseline exists anywhere in this codebase yet
    # (a future calibration-tracking unit's job); this check stays a
    # documented no-op in production until that lands, exactly as before
    # this unit closed driving_behaviors' OWN gap.
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


# ---------------------------------------------------------------------------
# vehicle_composition_entry_not_found (refuse, preflight) — v2 unit ⑦
#
# Not a §-numbered catalog entry; the SECOND half of set_vehicle_composition_
# share's object-resolution (the FIRST half — does composition_no exist at
# all — is object_not_found (#1), which _TOOL_TARGET_SPEC already covers),
# mirroring route_not_found_in_decision's (#46) exact split for the same
# reason: a composition's relative-flow entry has no flat, cross-composition
# id space of their own (vehType "100" can appear in many different
# compositions), so this cannot reuse #1's single-flat-list machinery; it
# looks the target composition up first (deferring to #1 when even THAT
# fails) and then checks veh_type_no against that ONE composition's own
# entries list.
# ---------------------------------------------------------------------------


def _check_vehicle_composition_entry_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_composition_share":
        return None

    params = action.get("params", {})
    composition_no = str(params.get("composition_no", ""))
    veh_type_no = str(params.get("veh_type_no", ""))

    composition = _find_by_field(shadow_model.get("vehicle_compositions", []), "no", composition_no)
    if composition is None:
        return None  # missing composition is object_not_found's job (#1)

    entries = composition.get("entries", [])
    if _find_by_field(entries, "veh_type", veh_type_no) is not None:
        return None

    candidates = [str(e.get("veh_type", "")) for e in entries]
    return Finding(
        constraint_id="vehicle_composition_entry_not_found",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "vehicle_composition_entry_not_found",
            composition_no=composition_no,
            veh_type_no=veh_type_no,
            candidates=", ".join(candidates) if candidates else "-",
        ),
        suggested_fix=candidates,
        details={"composition_no": composition_no, "veh_type_no": veh_type_no, "candidates": candidates},
    )


register(
    Constraint(
        id="vehicle_composition_entry_not_found",
        applies_to=["set_vehicle_composition_share"],
        phase="preflight",
        tier="refuse",
        check=_check_vehicle_composition_entry_not_found,
        message_ko="차량 조성 '{composition_no}'에 차종 번호 '{veh_type_no}'이(가) 없습니다. 이 "
        "조성의 사용 가능한 차종 번호: {candidates}.",
        message_en="Vehicle composition '{composition_no}' has no vehicle type numbered "
        "'{veh_type_no}'. This composition's available vehicle type numbers: {candidates}.",
        message_ja="車両構成 '{composition_no}' に車種番号 '{veh_type_no}' はありません。この構成で"
        "利用可能な車種番号: {candidates}。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_composition_weight_invalid (refuse, preflight) — v2 unit ⑦
#
# Not a §-numbered catalog entry; a defensive range check mirroring #2
# (volume_out_of_range)'s shape. RelFlow is a weight, not a percentage (see
# vehicle_composition_share_redistribution_gap's own docstring) -- a
# negative or zero weight has no physical meaning (Vissim's own COM write
# was not probed for this edge case since the risk of a malformed value
# reaching a live network unexamined is exactly what preflight exists to
# prevent), and a non-finite value (NaN/±inf) can only ever be a client-side
# bug, never a real request.
# ---------------------------------------------------------------------------


def _check_vehicle_composition_weight_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_composition_share":
        return None

    params = action.get("params", {})
    raw_rel_flow = params.get("rel_flow")
    if raw_rel_flow is None:
        return None
    rel_flow = _coerce_number(raw_rel_flow)
    if rel_flow is None:
        return None

    if math.isfinite(rel_flow) and rel_flow > 0:
        return None

    return Finding(
        constraint_id="vehicle_composition_weight_invalid",
        tier="refuse",
        phase="preflight",
        messages=_render("vehicle_composition_weight_invalid", rel_flow=raw_rel_flow),
        suggested_fix={"min": "greater than 0", "must_be": "finite"},
        details={"rel_flow": raw_rel_flow},
    )


register(
    Constraint(
        id="vehicle_composition_weight_invalid",
        applies_to=["set_vehicle_composition_share"],
        phase="preflight",
        tier="refuse",
        check=_check_vehicle_composition_weight_invalid,
        message_ko="요청한 relFlow 값 {rel_flow}은(는) 유효하지 않습니다. relFlow는 0보다 크고 "
        "유한한 값이어야 합니다.",
        message_en="Requested relFlow value {rel_flow} is invalid. relFlow must be greater than "
        "0 and a finite number.",
        message_ja="指定されたrelFlow値 {rel_flow} は無効です。relFlowは0より大きい有限の値である"
        "必要があります。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_composition_referenced_by_vehicle_inputs (warn, preflight) — v2
# unit ⑦
#
# Not a §-numbered catalog entry; a defensive addition specific to
# set_vehicle_composition_share. A composition is a shared object exactly
# like a driving behavior (#40, driving_behavior_shared_object_edit) or a
# signal cycle time shared across a corridor (#-, signal_cycle_change_may_
# break_corridor_sync): editing it changes the vehicle mix for EVERY vehicle
# input that references it (tools_network._parse_vehicle_compositions'
# referencing_vehicle_input_nos, scanning every interval's vehComp -- v2
# unit ⑦ probe finding), not just whatever the user had in mind when asking.
# Fires only when the datum is present and non-empty (a composition with no
# known references is not evidence of anything -- silent, not a false
# "0 vehicle inputs" claim).
# ---------------------------------------------------------------------------


def _check_vehicle_composition_referenced_by_vehicle_inputs(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_composition_share":
        return None

    params = action.get("params", {})
    composition_no = str(params.get("composition_no", ""))
    composition = _find_by_field(shadow_model.get("vehicle_compositions", []), "no", composition_no)
    if composition is None:
        return None  # missing target is object_not_found's job (#1)

    referencing = [str(x) for x in composition.get("referencing_vehicle_input_nos", []) or []]
    if not referencing:
        return None

    refs_ko, refs_en, refs_ja = _capped_join(referencing, "개", "vehicle inputs", "件")
    return Finding(
        constraint_id="vehicle_composition_referenced_by_vehicle_inputs",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_composition_referenced_by_vehicle_inputs",
            composition_no=composition_no,
            count=len(referencing),
            refs_ko=refs_ko,
            refs_en=refs_en,
            refs_ja=refs_ja,
        ),
        suggested_fix=None,
        details={"composition_no": composition_no, "referencing_vehicle_input_nos": referencing},
    )


register(
    Constraint(
        id="vehicle_composition_referenced_by_vehicle_inputs",
        applies_to=["set_vehicle_composition_share"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_composition_referenced_by_vehicle_inputs,
        message_ko="차량 조성 '{composition_no}'은(는) 차량 입력 {count}개가 사용 중입니다: "
        "{refs_ko}. 이 조성을 변경하면 이 모든 차량 입력의 차량 구성이 함께 바뀝니다.",
        message_en="Vehicle composition '{composition_no}' is used by {count} vehicle input(s): "
        "{refs_en}. Changing this composition changes the vehicle mix for ALL of them at once.",
        message_ja="車両構成 '{composition_no}' は {count} 件の車両インプットで使用されています: "
        "{refs_ja}。この構成を変更すると、これらすべての車両インプットの車両構成が同時に変わります。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_composition_share_drastic_change (warn, preflight) — v2 unit ⑦
#
# Not a §-numbered catalog entry; a defensive sanity nudge distinct from the
# always-fires split-confirm above. Fires only when the proposed write moves
# a vehicle type's estimated share from a clear MINORITY (<50%) to a near-
# TOTAL DOMINANCE (>=90%) of the composition -- a shape very unlikely to be
# the intended request ("a few more trucks") versus a magnitude/units
# mistake (e.g. typing a percentage-like number where a raw weight was
# expected). Uses the SAME "assume siblings keep their current relFlow"
# share estimate as vehicle_composition_share_redistribution_gap so the two
# findings never disagree with each other.
# ---------------------------------------------------------------------------

_DRASTIC_CHANGE_MINORITY_CEILING_PCT = 50.0
_DRASTIC_CHANGE_DOMINANCE_FLOOR_PCT = 90.0


def _check_vehicle_composition_share_drastic_change(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_composition_share":
        return None

    params = action.get("params", {})
    composition_no = str(params.get("composition_no", ""))
    veh_type_no = str(params.get("veh_type_no", ""))
    raw_new_rel_flow = params.get("rel_flow")
    if raw_new_rel_flow is None:
        return None
    new_rel_flow = _coerce_number(raw_new_rel_flow)
    if new_rel_flow is None:
        return None

    composition = _find_by_field(shadow_model.get("vehicle_compositions", []), "no", composition_no)
    if composition is None:
        return None  # missing target is object_not_found's job (#1)
    entries = composition.get("entries", [])
    target = _find_by_field(entries, "veh_type", veh_type_no)
    if target is None:
        return None  # missing target is vehicle_composition_entry_not_found's job

    old_rel_flow = _coerce_number(target.get("rel_flow"))
    if old_rel_flow is None:
        return None

    sibling_sum = 0.0
    for entry in entries:
        if str(entry.get("veh_type", "")) == veh_type_no:
            continue
        sibling_value = _coerce_number(entry.get("rel_flow"))
        if sibling_value is None:
            return None  # unknown sibling -- cannot estimate either share, don't guess
        sibling_sum += sibling_value

    if sibling_sum + old_rel_flow <= 0 or sibling_sum + new_rel_flow <= 0:
        return None

    before_pct = round((old_rel_flow / (sibling_sum + old_rel_flow)) * 100, 1)
    after_pct = round((new_rel_flow / (sibling_sum + new_rel_flow)) * 100, 1)

    if not (before_pct < _DRASTIC_CHANGE_MINORITY_CEILING_PCT and after_pct >= _DRASTIC_CHANGE_DOMINANCE_FLOOR_PCT):
        return None

    return Finding(
        constraint_id="vehicle_composition_share_drastic_change",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_composition_share_drastic_change",
            composition_no=composition_no,
            veh_type_no=veh_type_no,
            before_pct=before_pct,
            after_pct=after_pct,
        ),
        suggested_fix=None,
        details={
            "composition_no": composition_no,
            "veh_type_no": veh_type_no,
            "before_share_pct": before_pct,
            "after_share_pct": after_pct,
        },
    )


register(
    Constraint(
        id="vehicle_composition_share_drastic_change",
        applies_to=["set_vehicle_composition_share"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_composition_share_drastic_change,
        message_ko="이 변경으로 차종 '{veh_type_no}'의 예상 비율이 소수({before_pct}%)에서 "
        "지배적 비율({after_pct}%)로 급격히 바뀝니다. 의도한 변경이 맞는지 확인해 주세요 — 단위나 "
        "자릿수 실수(예: 백분율 값을 원시 가중치 자리에 입력)가 아닌지 점검하는 것이 좋습니다.",
        message_en="This change moves vehicle type '{veh_type_no}''s estimated share from a "
        "minority ({before_pct}%) to a dominant ({after_pct}%) share of the composition. Please "
        "confirm this is the intended change — this shape often indicates a units/magnitude "
        "mistake (e.g. entering a percentage-like number where a raw weight was expected).",
        message_ja="この変更により、車種 '{veh_type_no}' の推定比率が少数派({before_pct}%)から"
        "支配的な比率({after_pct}%)へ急激に変化します。意図した変更かご確認ください — 単位や桁の"
        "誤り(例: 生の重みの代わりに百分率のような値を入力)でないか確認することをお勧めします。",
    )
)


# ---------------------------------------------------------------------------
# des_speed_decision_entry_not_found (refuse, preflight) — v2a unit ⑧
#
# Not a §-numbered catalog entry; mirrors vehicle_composition_entry_not_found/
# route_not_found_in_decision's exact split (the FIRST half — does the
# PRIMARY decision_no exist at all — is object_not_found (#1), which
# _TOOL_TARGET_SPEC already covers for that ONE param). set_des_speed_decision
# is the first write tool whose action can name TWO decisions (the primary
# and, when paired, the restore) -- _TOOL_TARGET_SPEC only ever names one
# param per tool, so restore_decision_no has NO generic object_not_found
# coverage at all; this check is the ONLY place a bogus restore_decision_no
# (or a real decision missing the requested veh_class_no assignment, on
# EITHER side) gets caught with a structured refusal before COM ever sees it.
# ---------------------------------------------------------------------------


def _des_speed_decision_role_finding(
    role: str, decision_no: str, veh_class_no: str, decision: dict[str, Any] | None
) -> Finding:
    if decision is None:
        candidates: list[str] = []
        reason_ko = "이 결정 자체가 네트워크에 존재하지 않습니다"
        reason_en = "this decision does not exist in the network at all"
        reason_ja = "この決定自体がネットワークに存在しません"
    else:
        candidates = [str(a.get("veh_class_no", "")) for a in decision.get("veh_class_assignments", [])]
        available_text = ", ".join(candidates) if candidates else "-"
        reason_ko = f"이 결정에는 이 차종에 대한 배정이 없습니다 (배정된 차종: {available_text})"
        reason_en = f"this decision has no assignment for this vehicle class (assigned: {available_text})"
        reason_ja = f"この決定にはこの車種の割り当てがありません(割り当て済み車種: {available_text})"

    role_ko = "복원(restore)" if role == "restore" else "주(primary)"
    role_en = "restore" if role == "restore" else "primary"
    role_ja = "復元(restore)" if role == "restore" else "主(primary)"

    return Finding(
        constraint_id="des_speed_decision_entry_not_found",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "des_speed_decision_entry_not_found",
            role_ko=role_ko,
            role_en=role_en,
            role_ja=role_ja,
            decision_no=decision_no,
            veh_class_no=veh_class_no,
            reason_ko=reason_ko,
            reason_en=reason_en,
            reason_ja=reason_ja,
        ),
        suggested_fix={"candidates": candidates},
        details={
            "role": role,
            "decision_no": decision_no,
            "veh_class_no": veh_class_no,
            "available_veh_class_nos": candidates,
        },
    )


def _check_des_speed_decision_entry_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_decision":
        return None

    params = action.get("params", {})
    veh_class_no = str(params.get("veh_class_no", ""))
    roles: list[tuple[str, str]] = [("primary", str(params.get("decision_no", "")))]
    restore_decision_no = params.get("restore_decision_no")
    if restore_decision_no:
        roles.append(("restore", str(restore_decision_no)))

    for role, decision_no in roles:
        decision = _find_by_field(shadow_model.get("des_speed_decisions", []), "no", decision_no)
        if decision is None:
            if role == "primary":
                return None  # missing PRIMARY decision is object_not_found's job (#1)
            return _des_speed_decision_role_finding(role, decision_no, veh_class_no, None)

        assignment = _find_by_field(decision.get("veh_class_assignments", []), "veh_class_no", veh_class_no)
        if assignment is None:
            return _des_speed_decision_role_finding(role, decision_no, veh_class_no, decision)

    return None


register(
    Constraint(
        id="des_speed_decision_entry_not_found",
        applies_to=["set_des_speed_decision"],
        phase="preflight",
        tier="refuse",
        check=_check_des_speed_decision_entry_not_found,
        message_ko="{role_ko} 희망속도결정 '{decision_no}'의 차종 '{veh_class_no}' 배정을 찾을 수 "
        "없습니다: {reason_ko}.",
        message_en="Could not find the {role_en} desired speed decision '{decision_no}''s "
        "assignment for vehicle class '{veh_class_no}': {reason_en}.",
        message_ja="{role_ja}希望速度決定 '{decision_no}' の車種 '{veh_class_no}' の割り当てが"
        "見つかりません: {reason_ja}。",
    )
)


# ---------------------------------------------------------------------------
# des_speed_distribution_not_found (refuse, preflight) — v2a unit ⑧
#
# Not a §-numbered catalog entry. COM itself already fails CLOSED on a bogus
# SpeedDistribution number (verified live, backend/tools/
# probe_des_speed_decisions.py: SetAttValue raises "Object 999999 of type
# Speed Distribution does not exist") -- this check exists so the refusal
# comes WITH a reason and a candidate list BEFORE ever touching COM, per the
# guardrail doctrine, rather than a raw COM error string reaching the user.
# Checks BOTH des_speed_distr_no (the primary write) and, when the write is
# paired, restore_distr_no in ONE pass -- either one being bogus refuses.
# ---------------------------------------------------------------------------


def _check_des_speed_distribution_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_decision":
        return None

    params = action.get("params", {})
    distributions = shadow_model.get("des_speed_distributions", [])
    known_nos = [str(d.get("no", "")) for d in distributions]
    known_no_set = set(known_nos)

    fields_to_check: list[tuple[str, Any]] = [("des_speed_distr_no", params.get("des_speed_distr_no"))]
    restore_distr_no = params.get("restore_distr_no")
    if restore_distr_no:
        fields_to_check.append(("restore_distr_no", restore_distr_no))

    for field_name, raw_no in fields_to_check:
        distr_no = str(raw_no) if raw_no is not None else ""
        if not distr_no or distr_no in known_no_set:
            continue

        candidates = difflib.get_close_matches(distr_no, known_nos, n=5) or sorted(known_no_set)[:20]
        return Finding(
            constraint_id="des_speed_distribution_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "des_speed_distribution_not_found",
                field=field_name,
                distr_no=distr_no,
                candidates=", ".join(candidates) if candidates else "-",
            ),
            suggested_fix={"candidates": candidates},
            details={"field": field_name, "distr_no": distr_no, "available": sorted(known_no_set)},
        )
    return None


register(
    Constraint(
        id="des_speed_distribution_not_found",
        applies_to=["set_des_speed_decision"],
        phase="preflight",
        tier="refuse",
        check=_check_des_speed_distribution_not_found,
        message_ko="속도분포 번호 '{distr_no}'({field})가 이 네트워크에 존재하지 않습니다. 사용 "
        "가능한 속도분포 번호: {candidates}.",
        message_en="Speed distribution '{distr_no}' ({field}) does not exist in this network. "
        "Available speed distribution numbers: {candidates}.",
        message_ja="速度分布番号 '{distr_no}'({field})はこのネットワークに存在しません。利用"
        "可能な速度分布番号: {candidates}。",
    )
)


# ---------------------------------------------------------------------------
# v2a unit ⑨ -- fixed-time signal-program green splits. Four checks, all
# probe-informed (backend/tools/probe_signal_programs.py, live Vissim 2026):
#
#   signal_program_split_not_found   refuse  (program/SG triple must resolve)
#   green_split_outside_cycle        refuse  (Vissim ACCEPTS beyond-cycle
#                                             silently -- live-proven; our
#                                             refusal is the only screen)
#   green_split_below_min_green      warn    (fires only when a real
#                                             min_green datum exists)
#   signal_green_split_change_confirm warn   (always fires on a resolvable
#                                             target: echoes old->new window,
#                                             inactive-program trap, overlap
#                                             list + the "Vissim will NOT
#                                             stop conflicting greens"
#                                             education -- live-proven: two
#                                             SGs were given fully
#                                             overlapping greens and Vissim
#                                             accepted every write)
# ---------------------------------------------------------------------------


def _find_signal_program_split(
    shadow_model: dict[str, Any], sc_no: str, program_no: str, sg_no: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """(controller, program, split) from shadow_model["signal_programs"],
    each None from its own level downward when not found."""
    controller = _find_by_field(shadow_model.get("signal_programs", []), "sc_no", sc_no)
    if controller is None:
        return None, None, None
    program = _find_by_field(controller.get("programs", []), "prog_no", program_no)
    if program is None:
        return controller, None, None
    split = _find_by_field(program.get("sg_splits", []), "sg_no", sg_no)
    return controller, program, split


def _green_window_segments(start_s: float, end_s: float, cycle_s: float) -> list[tuple[float, float]]:
    """A green window as non-wrapping [start, end) segments within one cycle.
    A wrapping window (start > end -- real and valid, e.g. Beijing program 1
    SG 1) splits into its two segments; a degenerate window (start == end)
    has no extent."""
    if start_s == end_s:
        return []
    if start_s < end_s:
        return [(start_s, end_s)]
    return [(start_s, cycle_s), (0.0, end_s)]


def _green_windows_overlap(
    a_start: float, a_end: float, b_start: float, b_end: float, cycle_s: float
) -> bool:
    for a0, a1 in _green_window_segments(a_start, a_end, cycle_s):
        for b0, b1 in _green_window_segments(b_start, b_end, cycle_s):
            if a0 < b1 and b0 < a1:
                return True
    return False


def _check_signal_program_split_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_signal_green_split":
        return None

    params = action.get("params", {})
    sc_no = str(params.get("sc_no", ""))
    program_no = str(params.get("program_no", ""))
    sg_no = str(params.get("sg_no", ""))

    if _find_by_field(shadow_model.get("signals", []), "sc_no", sc_no) is None:
        return None  # missing controller is object_not_found's job (#1)

    controller, program, split = _find_signal_program_split(shadow_model, sc_no, program_no, sg_no)

    if controller is None or not controller.get("programs"):
        # The controller exists but no program data could be parsed for it
        # (undecodable/absent blob on a FIXEDTIME controller; unsupported
        # types are #4's job) -- an edit with no visible timing data has no
        # verification story, so refuse rather than guess.
        return Finding(
            constraint_id="signal_program_split_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "signal_program_split_not_found",
                sc_no=sc_no,
                program_no=program_no,
                sg_no=sg_no,
                reason_ko="이 제어기의 신호 프로그램 데이터를 읽을 수 없습니다",
                reason_en="no signal-program data could be read for this controller",
                reason_ja="この制御機の信号プログラムデータを読み取れません",
            ),
            suggested_fix=None,
            details={"sc_no": sc_no, "program_no": program_no, "sg_no": sg_no, "reason": "no_program_data"},
        )

    if program is None:
        available = [str(p.get("prog_no", "")) for p in controller.get("programs", [])]
        active = str(controller.get("active_program_no") or "")
        available_text = ", ".join(available) if available else "-"
        return Finding(
            constraint_id="signal_program_split_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "signal_program_split_not_found",
                sc_no=sc_no,
                program_no=program_no,
                sg_no=sg_no,
                reason_ko=f"프로그램 '{program_no}'이(가) 없습니다 (보유 프로그램: {available_text}, "
                f"활성 프로그램: {active or '-'})",
                reason_en=f"program '{program_no}' does not exist (available: {available_text}, "
                f"active: {active or '-'})",
                reason_ja=f"プログラム '{program_no}' は存在しません(保有プログラム: {available_text}、"
                f"アクティブ: {active or '-'})",
            ),
            suggested_fix={"candidates": available, "active_program_no": active or None},
            details={
                "sc_no": sc_no,
                "program_no": program_no,
                "sg_no": sg_no,
                "reason": "program_not_found",
                "available_program_nos": available,
                "active_program_no": active or None,
            },
        )

    if split is None and program.get("program_kind") == "stage_based":
        # Murdoch Drive finding (2026-07-28): a STAGE-based program has no
        # per-signal-group green window to edit at all -- saying "signal
        # group not found" would send the planner hunting for a different
        # sg_no that also does not exist. Name the real reason instead.
        return Finding(
            constraint_id="signal_program_split_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "signal_program_split_not_found",
                sc_no=sc_no,
                program_no=program_no,
                sg_no=sg_no,
                reason_ko="이 프로그램은 현시(stage) 기반이라 신호그룹별 녹색시간 구간이 "
                "존재하지 않습니다 - 현시 순서와 전환 시각으로만 정의되므로 여기서 "
                "편집할 수 없습니다 (Vissim GUI의 신호 프로그램 편집기를 사용하세요)",
                reason_en="this program is STAGE-based and has no per-signal-group green "
                "window at all -- it is defined only by its stage sequence and transition "
                "instants, so it cannot be edited here (use the Vissim GUI's signal program "
                "editor instead)",
                reason_ja="このプログラムはステージ(現示)ベースであり、信号グループごとの"
                "青時間帯が存在しません - ステージ順序と切替時刻のみで定義されるため、"
                "ここでは編集できません(Vissim GUIの信号プログラムエディタをご利用ください)",
            ),
            suggested_fix=None,
            details={
                "sc_no": sc_no,
                "program_no": program_no,
                "sg_no": sg_no,
                "reason": "stage_based_program",
                "sg_splits_unavailable_reason": program.get("sg_splits_unavailable_reason"),
            },
        )

    if split is None:
        candidates = [str(s.get("sg_no", "")) for s in program.get("sg_splits", [])]
        candidates_text = ", ".join(candidates) if candidates else "-"
        return Finding(
            constraint_id="signal_program_split_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "signal_program_split_not_found",
                sc_no=sc_no,
                program_no=program_no,
                sg_no=sg_no,
                reason_ko=f"이 프로그램에 신호그룹 '{sg_no}'이(가) 없습니다 (보유 신호그룹: {candidates_text})",
                reason_en=f"this program has no signal group '{sg_no}' (available: {candidates_text})",
                reason_ja=f"このプログラムに信号グループ '{sg_no}' はありません(保有グループ: {candidates_text})",
            ),
            suggested_fix={"candidates": candidates},
            details={
                "sc_no": sc_no,
                "program_no": program_no,
                "sg_no": sg_no,
                "reason": "sg_not_found",
                "available_sg_nos": candidates,
            },
        )

    if not split.get("simple") or split.get("green_start_s") is None:
        return Finding(
            constraint_id="signal_program_split_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "signal_program_split_not_found",
                sc_no=sc_no,
                program_no=program_no,
                sg_no=sg_no,
                reason_ko="이 신호그룹의 프로그램 명령 구조가 단순 단일 녹색창 형태가 아니어서 "
                "안전하게 편집할 수 없습니다 (Vissim GUI에서 직접 편집하세요)",
                reason_en="this signal group's program command shape is not a simple single "
                "green window and cannot be edited safely (edit it in the Vissim GUI instead)",
                reason_ja="この信号グループのプログラム命令構造は単一の青時間帯ではないため、"
                "安全に編集できません(Vissim GUIで直接編集してください)",
            ),
            suggested_fix=None,
            details={"sc_no": sc_no, "program_no": program_no, "sg_no": sg_no, "reason": "non_simple_shape"},
        )

    return None


register(
    Constraint(
        id="signal_program_split_not_found",
        applies_to=["set_signal_green_split"],
        phase="preflight",
        tier="refuse",
        check=_check_signal_program_split_not_found,
        message_ko="신호제어기 '{sc_no}' 프로그램 '{program_no}' 신호그룹 '{sg_no}'의 녹색시간을 "
        "편집할 수 없습니다: {reason_ko}.",
        message_en="Cannot edit signal controller '{sc_no}' program '{program_no}' signal group "
        "'{sg_no}''s green split: {reason_en}.",
        message_ja="信号制御機 '{sc_no}' プログラム '{program_no}' 信号グループ '{sg_no}' の"
        "青時間を編集できません: {reason_ja}。",
    )
)


def _check_green_split_outside_cycle(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_signal_green_split":
        return None

    params = action.get("params", {})
    raw_start = params.get("green_start_s")
    raw_end = params.get("green_end_s")
    start = _coerce_number(raw_start)
    end = _coerce_number(raw_end)
    if start is None or end is None:
        return None  # missing/mistyped params are the registry's invalid_params job

    sc_no = str(params.get("sc_no", ""))
    program_no = str(params.get("program_no", ""))
    sg_no = str(params.get("sg_no", ""))
    _, program, _ = _find_signal_program_split(shadow_model, sc_no, program_no, sg_no)
    cycle = _coerce_number(program.get("cycle_s")) if program is not None else None

    problem_ko = problem_en = problem_ja = None
    if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end < 0:
        problem_ko = "녹색 시작/종료 시각은 0 이상의 유한한 값이어야 합니다"
        problem_en = "green start/end must be finite values >= 0"
        problem_ja = "青の開始/終了時刻は0以上の有限の値である必要があります"
    elif start == end:
        problem_ko = "녹색 시작과 종료가 같아 녹색시간이 0이 됩니다"
        problem_en = "green start equals green end -- a zero-length green window"
        problem_ja = "青の開始と終了が同じで、青時間が0になります"
    elif cycle is not None and cycle > 0 and (start > cycle or end > cycle):
        problem_ko = f"주기({cycle}초)를 벗어납니다 — Vissim은 이런 값을 거부하지 않고 조용히 받아들이므로(실측 확인) 여기서 차단합니다"
        problem_en = (
            f"it falls outside the cycle time ({cycle}s) — Vissim silently ACCEPTS such values "
            "(live-verified), so this screen is the only protection"
        )
        problem_ja = f"サイクル({cycle}秒)の範囲外です — Vissimはこのような値を拒否せず黙って受け入れるため(実測確認済み)、ここでブロックします"

    if problem_ko is None:
        return None

    return Finding(
        constraint_id="green_split_outside_cycle",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "green_split_outside_cycle",
            sc_no=sc_no,
            program_no=program_no,
            sg_no=sg_no,
            green_start_s=raw_start,
            green_end_s=raw_end,
            problem_ko=problem_ko,
            problem_en=problem_en,
            problem_ja=problem_ja,
        ),
        suggested_fix={"cycle_s": cycle} if cycle is not None else None,
        details={
            "sc_no": sc_no,
            "program_no": program_no,
            "sg_no": sg_no,
            "green_start_s": start,
            "green_end_s": end,
            "cycle_s": cycle,
        },
    )


register(
    Constraint(
        id="green_split_outside_cycle",
        applies_to=["set_signal_green_split"],
        phase="preflight",
        tier="refuse",
        check=_check_green_split_outside_cycle,
        message_ko="요청한 녹색창 [{green_start_s}초 → {green_end_s}초]은(는) 유효하지 않습니다: "
        "{problem_ko}. (신호제어기 '{sc_no}' 프로그램 '{program_no}' 신호그룹 '{sg_no}')",
        message_en="Requested green window [{green_start_s}s → {green_end_s}s] is invalid: "
        "{problem_en}. (signal controller '{sc_no}' program '{program_no}' signal group '{sg_no}')",
        message_ja="指定された青時間帯 [{green_start_s}秒 → {green_end_s}秒] は無効です: "
        "{problem_ja}。(信号制御機 '{sc_no}' プログラム '{program_no}' 信号グループ '{sg_no}')",
    )
)


def _check_green_split_below_min_green(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_signal_green_split":
        return None

    params = action.get("params", {})
    start = _coerce_number(params.get("green_start_s"))
    end = _coerce_number(params.get("green_end_s"))
    if start is None or end is None or not (math.isfinite(start) and math.isfinite(end)):
        return None

    sc_no = str(params.get("sc_no", ""))
    program_no = str(params.get("program_no", ""))
    sg_no = str(params.get("sg_no", ""))
    _, program, split = _find_signal_program_split(shadow_model, sc_no, program_no, sg_no)
    if split is None:
        return None  # missing target is signal_program_split_not_found's job

    min_green = _coerce_number(split.get("min_green_s"))
    if min_green is None or min_green <= 0:
        return None  # datum absent (blob programs) or not constraining — no claim

    cycle = _coerce_number(program.get("cycle_s")) if program is not None else None
    if start < end:
        duration = end - start
    elif cycle is not None and cycle > 0:
        duration = (cycle - start) + end  # wrapping window
    else:
        return None  # wrap without a known cycle — duration unknowable, no claim

    if duration >= min_green:
        return None

    return Finding(
        constraint_id="green_split_below_min_green",
        tier="warn",
        phase="preflight",
        messages=_render(
            "green_split_below_min_green",
            sc_no=sc_no,
            program_no=program_no,
            sg_no=sg_no,
            duration=round(duration, 3),
            min_green=min_green,
        ),
        suggested_fix={"min_green_s": min_green},
        details={
            "sc_no": sc_no,
            "program_no": program_no,
            "sg_no": sg_no,
            "green_duration_s": duration,
            "min_green_s": min_green,
        },
    )


register(
    Constraint(
        id="green_split_below_min_green",
        applies_to=["set_signal_green_split"],
        phase="preflight",
        tier="warn",
        check=_check_green_split_below_min_green,
        message_ko="요청한 녹색시간 {duration}초가 신호그룹 '{sg_no}'의 최소 녹색시간 "
        "{min_green}초보다 짧습니다 (신호제어기 '{sc_no}' 프로그램 '{program_no}').",
        message_en="Requested green duration of {duration}s is below signal group '{sg_no}''s "
        "minimum green of {min_green}s (signal controller '{sc_no}' program '{program_no}').",
        message_ja="指定された青時間 {duration}秒 は信号グループ '{sg_no}' の最小青時間 "
        "{min_green}秒 を下回っています(信号制御機 '{sc_no}' プログラム '{program_no}')。",
    )
)


def _check_signal_green_split_change_confirm(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_signal_green_split":
        return None

    params = action.get("params", {})
    start = _coerce_number(params.get("green_start_s"))
    end = _coerce_number(params.get("green_end_s"))
    if start is None or end is None or not (math.isfinite(start) and math.isfinite(end)):
        return None

    sc_no = str(params.get("sc_no", ""))
    program_no = str(params.get("program_no", ""))
    sg_no = str(params.get("sg_no", ""))
    controller, program, split = _find_signal_program_split(shadow_model, sc_no, program_no, sg_no)
    if controller is None or program is None or split is None:
        return None  # unresolvable target is signal_program_split_not_found's job

    old_start = split.get("green_start_s")
    old_end = split.get("green_end_s")
    sg_name = str(split.get("sg_name", "") or f"SG{sg_no}")
    head_count = split.get("head_count", 0)

    # Temporal overlap against every OTHER signal group's current window in
    # the SAME program. Temporal overlap is NOT automatically a conflict
    # (parallel movements legitimately share green) -- but whether two
    # overlapping groups genuinely conflict is GEOMETRY, and Beijing's blob
    # carries an EMPTY <intergreenmatrices> (no conflict relationships in
    # the data at all), so the honest claim is: here is every overlap, and
    # Vissim will NOT stop you (live-proven: fully-overlapping greens on two
    # signal groups were accepted without any complaint).
    cycle = _coerce_number(program.get("cycle_s"))
    overlapping: list[str] = []
    if cycle is not None and cycle > 0:
        for other in program.get("sg_splits", []):
            if str(other.get("sg_no", "")) == sg_no:
                continue
            other_start = _coerce_number(other.get("green_start_s"))
            other_end = _coerce_number(other.get("green_end_s"))
            if other_start is None or other_end is None:
                continue
            if _green_windows_overlap(start, end, other_start, other_end, cycle):
                overlapping.append(str(other.get("sg_no", "")))

    overlap_ko, overlap_en, overlap_ja = _capped_join(overlapping, "개", "signal groups", "件")
    if overlapping:
        overlap_note_ko = (
            f" 새 녹색창은 신호그룹 {overlap_ko}의 현재 녹색창과 시간상 겹칩니다 — 겹침이 곧 "
            "상충은 아니지만(평행 이동류는 정상적으로 동시 녹색), 상충 여부는 기하구조의 문제이며 "
            "이 데이터에는 없습니다."
        )
        overlap_note_en = (
            f" The new window temporally overlaps signal group(s) {overlap_en}'s current green "
            "-- overlap is not automatically a conflict (parallel movements legitimately share "
            "green), but which overlaps genuinely conflict is a geometry question this data "
            "does not answer."
        )
        overlap_note_ja = (
            f" 新しい青時間帯は信号グループ {overlap_ja} の現在の青時間帯と時間的に重なります — "
            "重なりが即ち錯綜ではありませんが(並行動線は正常に同時青)、実際に錯綜するかは幾何構造の"
            "問題であり、このデータには含まれていません。"
        )
    else:
        overlap_note_ko = " 다른 신호그룹의 현재 녹색창과 시간상 겹치지 않습니다."
        overlap_note_en = " No temporal overlap with any other signal group's current green window."
        overlap_note_ja = " 他の信号グループの現在の青時間帯との時間的な重なりはありません。"

    active_no = str(controller.get("active_program_no") or "")
    inactive_note_ko = inactive_note_en = inactive_note_ja = ""
    if active_no and str(program_no) != active_no:
        inactive_note_ko = (
            f" ⚠ 이 프로그램은 활성 프로그램이 아닙니다(활성: {active_no}) — 활성 프로그램을 바꾸지 "
            "않는 한 이 수정은 시뮬레이션 동작에 반영되지 않습니다."
        )
        inactive_note_en = (
            f" ⚠ This is NOT the active program (active: {active_no}) — this edit will not "
            "affect simulated behavior unless the active program is changed."
        )
        inactive_note_ja = (
            f" ⚠ これはアクティブなプログラムではありません(アクティブ: {active_no}) — アクティブな"
            "プログラムを変更しない限り、この編集はシミュレーション挙動に反映されません。"
        )

    return Finding(
        constraint_id="signal_green_split_change_confirm",
        tier="warn",
        phase="preflight",
        messages=_render(
            "signal_green_split_change_confirm",
            sc_no=sc_no,
            program_no=program_no,
            sg_no=sg_no,
            sg_name=sg_name,
            head_count=head_count,
            old_start=old_start,
            old_end=old_end,
            new_start=params.get("green_start_s"),
            new_end=params.get("green_end_s"),
            overlap_note_ko=overlap_note_ko,
            overlap_note_en=overlap_note_en,
            overlap_note_ja=overlap_note_ja,
            inactive_note_ko=inactive_note_ko,
            inactive_note_en=inactive_note_en,
            inactive_note_ja=inactive_note_ja,
        ),
        suggested_fix=None,
        details={
            "sc_no": sc_no,
            "program_no": program_no,
            "sg_no": sg_no,
            "sg_name": sg_name,
            "head_count": head_count,
            "old_green_window": {"green_start_s": old_start, "green_end_s": old_end},
            "new_green_window": {"green_start_s": start, "green_end_s": end},
            "overlapping_sg_nos": overlapping,
            "active_program_no": active_no or None,
            "targets_active_program": not active_no or str(program_no) == active_no,
        },
    )


register(
    Constraint(
        id="signal_green_split_change_confirm",
        applies_to=["set_signal_green_split"],
        phase="preflight",
        tier="warn",
        check=_check_signal_green_split_change_confirm,
        message_ko="신호제어기 '{sc_no}' 프로그램 '{program_no}' 신호그룹 '{sg_no}'({sg_name}, "
        "신호등 {head_count}개)의 녹색창을 [{old_start}초 → {old_end}초]에서 [{new_start}초 → "
        "{new_end}초]로 변경합니다.{inactive_note_ko}{overlap_note_ko} 주의: Vissim은 상충하는 "
        "이동류에 동시 녹색을 주어도 이를 막지 않습니다(실측 확인) — 결과는 정상처럼 보이지만 "
        "차량이 서로 통과하는 무의미한 시뮬레이션이 될 수 있습니다.",
        message_en="Changing signal controller '{sc_no}' program '{program_no}' signal group "
        "'{sg_no}' ({sg_name}, {head_count} signal head(s)) green window from [{old_start}s → "
        "{old_end}s] to [{new_start}s → {new_end}s].{inactive_note_en}{overlap_note_en} "
        "Caution: Vissim will NOT stop you from giving conflicting movements simultaneous "
        "green (live-verified) — the run would look normal while vehicles drive through each "
        "other, producing meaningless results.",
        message_ja="信号制御機 '{sc_no}' プログラム '{program_no}' 信号グループ '{sg_no}'"
        "({sg_name}、信号灯 {head_count}基)の青時間帯を [{old_start}秒 → {old_end}秒] から "
        "[{new_start}秒 → {new_end}秒] に変更します。{inactive_note_ja}{overlap_note_ja} "
        "注意: Vissimは錯綜する動線への同時青を防ぎません(実測確認済み) — 結果は正常に見えても"
        "車両が互いをすり抜ける無意味なシミュレーションになる恐れがあります。",
    )
)


# =============================================================================
# Health-check unit (2026-07-28) — three NEW constraints, phase="dataset".
#
# These are not §-numbered catalog entries and not extensions of an existing
# M2/v2 write tool's own preflight/postwrite/postrun gate: each names a
# whole-network integrity fact that only makes sense as a scan over EVERY
# object of its class, with no proposed action at all -- the health-check
# report's own reason for existing. Registered with phase="dataset" (see the
# Phase alias's own comment above) so they are, by construction, never
# reachable from guardrails.py's real edit-time run_checks() calls -- adding
# them changes nothing about what any existing tool call refuses/warns on.
# Each is wired into services.agent.health_check's whole-network scan via
# constraints.register_dataset_check (that module, not this one, owns the
# "how do you turn this into a scan" glue -- see that function's docstring).
# =============================================================================


# ---------------------------------------------------------------------------
# evaluation_config_disarmed (warn, dataset)
#
# Catalog §5 "Evaluations are not retroactive": the existing #15
# (evaluation_not_configured_before_run) can only ever answer this POSTRUN,
# from a live job's run_result["evaluation_configured"] (see that
# constraint's own docstring -- it has no shadow-model path). This is the
# STATIC counterpart a parse-only health check CAN answer with no COM and no
# run at all: tools_network._parse_evaluation_config reads the .inpx's own
# top-level <evaluation netPerfEvalAct="..." .../> element directly. Fires
# when the element is missing entirely (nothing was ever configured) OR
# present but netPerfEvalAct is not true (network performance evaluation —
# the measure most other KPI answers depend on — is switched off).
# ---------------------------------------------------------------------------


def _check_evaluation_config_disarmed(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    config = shadow_model.get("evaluation_config")
    if config is None:
        return None  # older/synthetic shadow model omitting the key entirely

    if not config.get("present"):
        return Finding(
            constraint_id="evaluation_config_disarmed",
            tier="warn",
            phase="dataset",
            messages=_render(
                "evaluation_config_disarmed",
                reason_ko="이 네트워크에는 평가(evaluation) 설정 자체가 없습니다",
                reason_en="this network has no evaluation configuration at all",
                reason_ja="このネットワークには評価(evaluation)設定自体がありません",
            ),
            suggested_fix=None,
            details={"present": False},
        )

    if config.get("net_perf_eval_act") is False:
        return Finding(
            constraint_id="evaluation_config_disarmed",
            tier="warn",
            phase="dataset",
            messages=_render(
                "evaluation_config_disarmed",
                reason_ko="네트워크 성능 평가(Network Performance Evaluation)가 꺼져 있습니다",
                reason_en="network performance evaluation is switched off",
                reason_ja="ネットワークパフォーマンス評価(Network Performance Evaluation)がオフです",
            ),
            suggested_fix=None,
            details={"present": True, "net_perf_eval_act": False},
        )

    return None


register(
    Constraint(
        id="evaluation_config_disarmed",
        applies_to=["*"],
        phase="dataset",
        tier="warn",
        check=_check_evaluation_config_disarmed,
        message_ko="{reason_ko} — Vissim은 실행 전에 켜둔 측정값만 기록하므로, 지금 평가를 켜지 "
        "않으면 다음 실행 후 다시 실행하지 않고는 답할 수 없는 질문들이 생깁니다.",
        message_en="{reason_en} — Vissim only records measurements switched on before the run "
        "starts, so without arming evaluation now, some questions about the next run will be "
        "unanswerable without a re-run.",
        message_ja="{reason_ja} — Vissimは実行前にオンにした測定値のみを記録するため、今評価を"
        "有効化しないと、次回実行後に再実行なしでは答えられない質問が生じます。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_input_cont_volume_mismatch (warn, dataset)
#
# Catalog §4 "Volume is always per hour, whatever the interval", the same
# root fact #13 (vehicle_input_multi_interval_write_gap) encodes for a
# PROPOSED write -- but this is a static DATA-integrity trap the .inpx can
# already carry with no write at all: a <timeIntervalVehVolume cont="true">
# interval means Vissim IGNORES that interval's own "volume" attribute and
# continues the PREVIOUS interval's volume instead (verified live,
# backend/tools/vehicle_input_interval_probe.py's own Cont finding). When the
# .inpx's stored "volume" on a cont=true interval DIFFERS from the previous
# interval's own effective volume, that stored number is misleading dead
# data -- whoever last edited this profile (by hand, by import, or by a
# future tool) may reasonably believe it takes effect; it never will.
# ---------------------------------------------------------------------------

_CONT_VOLUME_TOLERANCE = 1e-6


def _cont_mismatched_intervals(volumes: list[dict[str, Any]]) -> list[str]:
    """Every ``interval_no`` (1-based, as ``str``) in ``volumes`` (one
    vehicle input's own ``{interval_no, volume, continuous}`` list, in
    document order) whose ``cont=true`` interval carries a stored volume
    that differs from the volume actually in effect (the nearest EARLIER
    non-continuous interval's own volume, carried forward — Cont's real
    semantics)."""
    mismatched: list[str] = []
    if not volumes:
        return mismatched
    effective_volume = _coerce_number(volumes[0].get("volume"))
    for interval in volumes[1:]:
        own_volume = _coerce_number(interval.get("volume"))
        if interval.get("continuous"):
            if (
                own_volume is not None
                and effective_volume is not None
                and _values_differ(own_volume, effective_volume)
            ):
                mismatched.append(str(interval.get("interval_no")))
            # effective_volume carries forward unchanged (Cont == "keep previous")
        else:
            effective_volume = own_volume
    return mismatched


def _check_vehicle_input_cont_volume_mismatch(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    offenders: list[str] = []
    for vi in shadow_model.get("vehicle_inputs", []):
        input_no = str(vi.get("no", ""))
        for interval_no in _cont_mismatched_intervals(vi.get("volumes") or []):
            offenders.append(f"{input_no}.{interval_no}")

    if not offenders:
        return None

    entries_ko, entries_en, entries_ja = _capped_join(offenders, "개", "intervals", "件")
    return Finding(
        constraint_id="vehicle_input_cont_volume_mismatch",
        tier="warn",
        phase="dataset",
        messages=_render(
            "vehicle_input_cont_volume_mismatch",
            entries_ko=entries_ko,
            entries_en=entries_en,
            entries_ja=entries_ja,
        ),
        suggested_fix=None,
        details={"mismatched_input_intervals": offenders},
    )


register(
    Constraint(
        id="vehicle_input_cont_volume_mismatch",
        applies_to=["*"],
        phase="dataset",
        tier="warn",
        check=_check_vehicle_input_cont_volume_mismatch,
        message_ko="차량 입력.구간 {entries_ko}은(는) 연속(Cont) 표시가 되어 있어 Vissim이 이전 "
        "구간의 볼륨을 그대로 사용하며, 저장된 자체 볼륨 값은 무시합니다 — 저장된 숫자가 실제로 "
        "적용되는 값과 다릅니다.",
        message_en="Vehicle-input.interval {entries_en} are marked continuous (Cont) — Vissim "
        "carries the previous interval's volume forward and ignores this interval's own stored "
        "volume value, which differs from what actually applies.",
        message_ja="車両インプット.区間 {entries_ja} は連続(Cont)指定されているため、Vissimは前の"
        "区間のボリュームをそのまま使用し、この区間自体に保存されたボリューム値は無視されます — "
        "保存されている数値は実際に適用される値と異なります。",
    )
)


# ---------------------------------------------------------------------------
# des_speed_distribution_orphan_reference (warn, dataset)
#
# Catalog §3's "fragile references" spirit, applied to desired-speed
# decisions: each vehicle-class assignment names a des_speed_distr_no, and
# tools_network._parse_des_speed_decisions already resolves that number to a
# distribution NAME by cross-referencing shadow_model's own
# <speedDistributions> list, defaulting to "" when the number doesn't
# resolve. Reusing that already-computed resolution (rather than
# re-deriving a second id-set lookup) is the entire check: an assignment
# whose des_speed_distr_no is non-empty but whose resolved name is empty is a
# dangling reference -- COM itself fails closed on this (constraint #4's twin
# gap note; des_speed_decision's own docstring: "a bogus DISTRIBUTION number
# ... 'Object 999999 of type Speed Distribution does not exist'"), so this is
# a genuine, already-broken configuration a health check should surface
# BEFORE any agent turn ever tries to write through it.
# ---------------------------------------------------------------------------


def _check_des_speed_distribution_orphan_reference(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    entries = shadow_model.get("des_speed_decision_entries")
    if not entries:
        return None

    offenders = [
        f"{e.get('decision_no', '?')}.{e.get('veh_class_no', '?')}"
        for e in entries
        if str(e.get("des_speed_distr_no", "")).strip() and not str(e.get("des_speed_distr_name", "")).strip()
    ]
    if not offenders:
        return None

    entries_ko, entries_en, entries_ja = _capped_join(offenders, "개", "entries", "件")
    return Finding(
        constraint_id="des_speed_distribution_orphan_reference",
        tier="warn",
        phase="dataset",
        messages=_render(
            "des_speed_distribution_orphan_reference",
            entries_ko=entries_ko,
            entries_en=entries_en,
            entries_ja=entries_ja,
        ),
        suggested_fix=None,
        details={"offending_entries": offenders},
    )


register(
    Constraint(
        id="des_speed_distribution_orphan_reference",
        applies_to=["*"],
        phase="dataset",
        tier="warn",
        check=_check_des_speed_distribution_orphan_reference,
        message_ko="희망속도결정 항목 {entries_ko}(decision.vehClass)이(가) 존재하지 않는 희망속도"
        "분포 번호를 참조하고 있습니다. Vissim COM은 이런 참조에 대한 쓰기를 거부하지만("
        "\"Object N of type Speed Distribution does not exist\"), 데이터 자체는 이미 끊어진 "
        "상태입니다.",
        message_en="Desired-speed decision entr(ies) {entries_en} (decision.vehClass) reference a "
        "desired-speed distribution number that does not exist. Vissim COM refuses a write "
        "through a dangling reference like this (\"Object N of type Speed Distribution does not "
        "exist\"), but the data itself is already broken.",
        message_ja="希望速度決定エントリ {entries_ja}(decision.vehClass)が存在しない希望速度分布"
        "番号を参照しています。Vissim COMはこのような参照への書き込みを拒否しますが"
        "(\"Object N of type Speed Distribution does not exist\")、データ自体は既に壊れています。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_input_siblings_share_link (warn, preflight) — sibling-
# disambiguation unit, 2026-07-28
#
# Not a §-numbered catalog entry; a defensive addition specific to
# set_vehicle_input_volume, in the same family as #3 latent_demand_overflow
# (capacity) but about a DIFFERENT failure: not "too much traffic" but "the
# wrong MIX of traffic", and, upstream of that, "the user cannot tell which
# of three identical-looking objects is being written".
#
# The real client model (Murdoch Drive, runtime/uploads/current/current.inpx)
# puts THREE vehicle inputs on every one of its 17 demand links — 51 inputs,
# 17 groups of 3, each group sharing the same name, the same link AND the
# same exact (x, y) coordinate, differing only by vehicle composition
# (hundreds digit == vehComp: 1xx→"Class1", 2xx→"Class2-5", 3xx→"Class6-9").
# Because they resolve to one pixel, the network map's tooltip named 307
# while the agent was correctly writing 107, and the user had no way to
# verify the right object was touched (the write WAS right; the product gave
# them nothing to check it against).
#
# The semantic trap this constraint states out loud: a vehicle input feeds
# exactly ONE composition's stream, so setting one input's volume does NOT
# scale the approach's demand — it re-mixes it. Worked example, the exact
# request the user made: link 620 carries 107 = 127.3 (Class1), 207 = 19.0
# (Class2-5), 307 = 0.95 (Class6-9). Setting 107 to 6000 leaves 207 and 307
# untouched, so the non-Class1 share of the approach collapses from
# 19.95/147.25 = 13.5% to 19.95/6019.95 = 0.3% — the heavy vehicles do not
# scale with the light ones, and nothing in Vissim says so.
#
# Silent when the target is the only input on its link (nothing to confuse it
# with, and the mix cannot shift), so single-input networks — the synthetic
# demo file included — never see this warning.
# ---------------------------------------------------------------------------


def _vehicle_input_composition_label(vehicle_input: dict[str, Any]) -> tuple[str, str, str]:
    """``(ko, en, ja)`` naming the composition a vehicle input feeds.

    Three honest cases, never a fabricated class name: a single known
    composition (its name, or its bare ``vehComp`` number when the network
    names no compositions), a composition that DIFFERS across the input's
    time intervals (the schema permits it — see ``services.inpx_parser.
    vehicle_input_composition_fields``; 0 of the 51 real inputs do it), and
    no composition datum at all (a network whose ``<timeIntervalVehVolume>``
    children carry no ``vehComp``, e.g. the synthetic demo file)."""
    if vehicle_input.get("composition_varies_by_interval"):
        nos = ", ".join(str(n) for n in (vehicle_input.get("composition_nos") or []))
        return (
            f"차종 조성이 구간마다 다름: vehComp {nos}",
            f"composition varies by interval: vehComp {nos}",
            f"車両構成が区間ごとに異なる: vehComp {nos}",
        )
    name = str(vehicle_input.get("composition_name", "") or "").strip()
    if name:
        return (f"차종 조성 '{name}'", f"composition '{name}'", f"車両構成 '{name}'")
    comp_no = str(vehicle_input.get("composition_no", "") or "").strip()
    if comp_no:
        return (f"차종 조성 vehComp {comp_no}", f"composition vehComp {comp_no}", f"車両構成 vehComp {comp_no}")
    return ("차종 조성 미상", "composition unknown", "車両構成不明")


def _sibling_vehicle_input_labels(siblings: list[dict[str, Any]], interval_no: int = 1) -> list[str]:
    """``["207(Class2-5, 19.0 veh/h)", ...]`` — one display token per sibling,
    deliberately built ONCE and reused verbatim in all three languages
    (``_capped_join``'s established contract): every token is a number, an
    object name Vissim itself authored, or the language-neutral Vissim
    attribute spelling ``vehComp``, so nothing in it needs translating.

    ``volume`` is read AT ``interval_no`` (2026-07-28 interval-blindness fix
    — see #3's own comment block for the measured issue this and
    :func:`_sibling_volume_total` share), not the sibling's top-level
    ``volume`` field, so a sibling's displayed number always matches the
    interval the finding's own numbers are computed for."""
    labels: list[str] = []
    for sibling in siblings:
        sibling_no = str(sibling.get("no", "") or "?")
        volume = _volume_at_interval(sibling, interval_no)
        # "?" for an unknown volume, not a localized phrase -- this token is
        # reused verbatim across ko/en/ja (route_relative_flow_split_confirm
        # uses the same "?" convention for an unknown old value).
        volume_text = f"{volume} veh/h" if volume is not None else "? veh/h"
        name = str(sibling.get("composition_name", "") or "").strip()
        if sibling.get("composition_varies_by_interval"):
            comp_text = "vehComp " + "/".join(str(n) for n in (sibling.get("composition_nos") or []))
        elif name:
            comp_text = name
        else:
            comp_no = str(sibling.get("composition_no", "") or "").strip()
            comp_text = f"vehComp {comp_no}" if comp_no else ""
        labels.append(f"{sibling_no}({comp_text}, {volume_text})" if comp_text else f"{sibling_no}({volume_text})")
    return labels


def _check_vehicle_input_siblings_share_link(
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
    interval_no = _interval_no_default_1(params)

    vehicle_input = _find_by_field(shadow_model.get("vehicle_inputs", []), "no", input_no)
    if vehicle_input is None:
        return None  # missing target is object_not_found's job (#1)

    link_id = str(vehicle_input.get("link", ""))
    siblings = _sibling_vehicle_inputs(shadow_model, link_id, input_no)
    if not siblings:
        return None  # nothing to confuse it with, and no mix to shift

    sibling_total, sibling_unknown = _sibling_volume_total(siblings, interval_no)
    labels = _sibling_vehicle_input_labels(siblings, interval_no)
    siblings_ko, siblings_en, siblings_ja = _capped_join(labels, "개", "vehicle inputs", "件")
    target_comp_ko, target_comp_en, target_comp_ja = _vehicle_input_composition_label(vehicle_input)

    # 2026-07-28 interval-blindness fix: the TARGET's own "old" volume must
    # also be read AT interval_no -- its top-level `volume` field is
    # interval 1's value BY DEFINITION, same trap as the siblings' totals.
    old_volume = _volume_at_interval(vehicle_input, interval_no)
    old_total = round(old_volume + sibling_total, 4) if old_volume is not None else None
    new_total = round(volume + sibling_total, 4)
    old_other_pct: float | None = None
    new_other_pct: float | None = None
    if not sibling_unknown and old_total is not None and old_total > 0 and new_total > 0:
        old_other_pct = round(sibling_total / old_total * 100, 1)
        new_other_pct = round(sibling_total / new_total * 100, 1)

    if old_other_pct is not None and new_other_pct is not None:
        mix_ko = (
            f"다른 차종이 이 진입로의 구간 {interval_no} 전체 수요에서 차지하는 비중은 "
            f"{old_other_pct}%에서 {new_other_pct}%로 바뀝니다(구간 {interval_no} 링크 총 수요 "
            f"{old_total} → {new_total} veh/h)."
        )
        mix_en = (
            f"At interval {interval_no}, the other vehicle classes' share of this approach's total "
            f"demand moves from {old_other_pct}% to {new_other_pct}% (link total {old_total} → "
            f"{new_total} veh/h)."
        )
        mix_ja = (
            f"区間{interval_no}で他の車種がこの流入部の総需要に占める割合は {old_other_pct}% から "
            f"{new_other_pct}% に変わります(リンク総需要 {old_total} → {new_total} veh/h)。"
        )
    else:
        mix_ko = f"구간 {interval_no}에서 일부 차량 입력의 현재 볼륨을 알 수 없어 구성비 변화는 " \
            "계산하지 못했습니다."
        mix_en = f"The resulting mix shift at interval {interval_no} could not be computed (a " \
            "vehicle input's current volume at that interval is unknown)."
        mix_ja = f"区間{interval_no}で一部の車両インプットの現在のボリュームが不明なため、構成比の" \
            "変化は計算できませんでした。"

    return Finding(
        constraint_id="vehicle_input_siblings_share_link",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_input_siblings_share_link",
            input_no=input_no,
            link_id=link_id,
            sibling_count=len(siblings),
            volume=raw_volume,
            interval_no=interval_no,
            target_comp_ko=target_comp_ko,
            target_comp_en=target_comp_en,
            target_comp_ja=target_comp_ja,
            siblings_ko=siblings_ko,
            siblings_en=siblings_en,
            siblings_ja=siblings_ja,
            mix_ko=mix_ko,
            mix_en=mix_en,
            mix_ja=mix_ja,
        ),
        suggested_fix=None,
        details={
            "input_no": input_no,
            "link_id": link_id,
            "interval_no": interval_no,
            "sibling_input_nos": [str(s.get("no", "")) for s in siblings],
            "sibling_volume_total": sibling_total,
            "sibling_volume_unknown_count": sibling_unknown,
            "old_link_total_volume": old_total,
            "new_link_total_volume": new_total,
            "other_classes_share_pct_before": old_other_pct,
            "other_classes_share_pct_after": new_other_pct,
        },
    )


register(
    Constraint(
        id="vehicle_input_siblings_share_link",
        applies_to=["set_vehicle_input_volume"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_input_siblings_share_link,
        message_ko="차량 입력 '{input_no}'({target_comp_ko})은(는) 링크 '{link_id}'의 같은 진입 "
        "지점을 다른 차량 입력 {sibling_count}개와 공유합니다: {siblings_ko}. 차량 입력 하나는 한 "
        "차종 조성의 교통류만 담당하므로, '{input_no}'의 구간 {interval_no} 볼륨을 {volume} veh/h로 "
        "바꿔도 나머지 차량 입력의 볼륨은 그대로 남습니다 — 진입로 전체 수요가 그만큼 늘어나는 동시에 "
        "차종 구성비 자체가 달라집니다. {mix_ko} 차종 구성비를 유지하려면 나머지 차량 입력도 같은 "
        "비율로 조정해야 합니다.",
        message_en="Vehicle input '{input_no}' ({target_comp_en}) shares the same entry point on "
        "link '{link_id}' with {sibling_count} other vehicle input(s): {siblings_en}. One vehicle "
        "input feeds exactly ONE composition's stream, so setting '{input_no}''s interval "
        "{interval_no} volume to {volume} veh/h leaves the other inputs' volumes untouched — the "
        "approach's total demand rises AND its vehicle-class mix changes. {mix_en} To preserve the "
        "class mix, the other vehicle inputs must be scaled by the same factor.",
        message_ja="車両インプット '{input_no}'({target_comp_ja})は、リンク '{link_id}' の同じ流入"
        "地点を他の車両インプット {sibling_count} 件と共有しています: {siblings_ja}。1つの車両"
        "インプットは1つの車両構成の交通流のみを担うため、'{input_no}' の区間{interval_no}のボリューム"
        "を {volume} veh/h に変更しても他のインプットのボリュームはそのまま残ります — 流入部の総需要"
        "が増えると同時に、車種構成比自体が変わります。{mix_ja}車種構成比を維持するには、他の車両"
        "インプットも同じ比率で調整する必要があります。",
    )
)


# ---------------------------------------------------------------------------
# priority_rule_marker_not_found (refuse, preflight) — QUEUE 3 unit 2
#
# Not a §-numbered catalog entry; mirrors route_not_found_in_decision/
# vehicle_composition_entry_not_found/des_speed_decision_entry_not_found's
# exact split (the RULE-level lookup is object_not_found's job (#1), already
# covered via _TOOL_TARGET_SPEC). A conflict marker has NO identity
# attribute of its own at all (verified live, backend/tools/
# probe_priority_rules.py: AttValue('No') on a marker raises) and its own
# COM collection has no ItemByKey (verified live via hasattr) — WORSE than
# routes/composition-entries/des-speed-entries, which at least had a real
# attribute (route_no/veh_type/veh_class) to match on; a marker's identity
# here is a SYNTHESIZED 1-based ordinal (tools_network._parse_priority_rules'
# own docstring), so this check is the ONLY place a bogus marker_index gets
# a structured refusal (with the rule's real marker count) before COM ever
# sees it — vissim_com.VissimController._priority_rule_marker_by_index
# raises its own VissimObjectNotFoundError as a backstop, mirrored here.
# ---------------------------------------------------------------------------


def _check_priority_rule_marker_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_priority_rule_gap_headway":
        return None

    params = action.get("params", {})
    rule_no = str(params.get("priority_rule_no", ""))
    raw_marker_index = params.get("marker_index")

    rule = _find_by_field(shadow_model.get("priority_rules", []), "no", rule_no)
    if rule is None:
        return None  # missing rule is object_not_found's job (#1)

    markers = rule.get("conflict_markers", [])
    marker_indexes = [str(m.get("marker_index", "")) for m in markers]
    if str(raw_marker_index) in marker_indexes:
        return None

    count = len(markers)
    return Finding(
        constraint_id="priority_rule_marker_not_found",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "priority_rule_marker_not_found",
            rule_no=rule_no,
            marker_index=raw_marker_index,
            count=count,
        ),
        suggested_fix={"candidates": marker_indexes},
        details={"priority_rule_no": rule_no, "marker_index": raw_marker_index, "marker_count": count},
    )


register(
    Constraint(
        id="priority_rule_marker_not_found",
        applies_to=["set_priority_rule_gap_headway"],
        phase="preflight",
        tier="refuse",
        check=_check_priority_rule_marker_not_found,
        message_ko="우선순위 규칙 '{rule_no}'에 충돌 마커 인덱스 {marker_index}가 없습니다. 이 "
        "규칙에는 충돌 마커가 {count}개 있으며, 1부터 {count}까지의 번호로 지정해야 합니다.",
        message_en="Priority rule '{rule_no}' has no conflict marker at index {marker_index}. This "
        "rule has {count} conflict marker(s), indexed 1..{count}.",
        message_ja="優先ルール '{rule_no}' にはインデックス {marker_index} の競合マーカーが"
        "ありません。このルールには競合マーカーが {count} 件あり、1〜{count} の番号で指定する必要が"
        "あります。",
    )
)


# ---------------------------------------------------------------------------
# priority_rule_gap_headway_invalid (refuse, preflight) — QUEUE 3 unit 2
#
# Not a §-numbered catalog entry. The task brief's own draft wording said
# "hard refusal for non-positive values" — verified WRONG against real data
# before implementing (backend/tools/probe_priority_rules.py's live scan):
# 0 is a common, LEGITIMATE value for both attributes on real networks (13
# of the real Murdoch Drive model's own 20 conflict markers carry
# minGapTime="0"; dozens more do on Roundabout Buehl.DE) — meaning "no gap
# required"/"no clearance required" at that specific conflict point.
# Refusing 0 would make this tool unable to even reproduce a network's own
# existing legitimate data. Vissim's own live-verified floor is exactly 0,
# not >0 (SetAttValue(-1) on EITHER attribute RAISES: "... is lower than
# minimum value ... (Min: 0 ...)"), so this check refuses NEGATIVE (and
# non-numeric — pydantic's own float typing already catches most of this
# before dispatch, but a caller bypassing the params model, or NaN which
# float() accepts, would not be) values, matching Vissim's own semantics
# rather than the brief's un-probed guess. See docs/phase2/DECISIONS.md for
# the drafted entry recording this as a probe-driven deviation.
# ---------------------------------------------------------------------------


def _check_priority_rule_gap_headway_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_priority_rule_gap_headway":
        return None

    params = action.get("params", {})
    bad_fields: dict[str, Any] = {}
    for field_name in ("gap_time_s", "headway_m"):
        raw = params.get(field_name)
        if raw is None:
            continue
        value = _coerce_number(raw)
        if value is None or not math.isfinite(value) or value < 0:
            bad_fields[field_name] = raw

    if not bad_fields:
        return None

    fields_text = ", ".join(f"{k}={v}" for k, v in bad_fields.items())
    return Finding(
        constraint_id="priority_rule_gap_headway_invalid",
        tier="refuse",
        phase="preflight",
        messages=_render("priority_rule_gap_headway_invalid", fields_text=fields_text),
        suggested_fix={"min": "0 or greater", "must_be": "finite"},
        details={"invalid_fields": bad_fields},
    )


register(
    Constraint(
        id="priority_rule_gap_headway_invalid",
        applies_to=["set_priority_rule_gap_headway"],
        phase="preflight",
        tier="refuse",
        check=_check_priority_rule_gap_headway_invalid,
        message_ko="요청한 값이 유효하지 않습니다: {fields_text}. gap_time_s와 headway_m은 모두 "
        "0 이상의 유한한 값이어야 합니다 (Vissim 자체가 음수를 거부합니다).",
        message_en="The requested value(s) are invalid: {fields_text}. Both gap_time_s and "
        "headway_m must be finite numbers >= 0 (Vissim itself rejects negative values).",
        message_ja="指定された値が無効です: {fields_text}。gap_time_s と headway_m は両方とも0以上"
        "の有限の値である必要があります(Vissim自体が負の値を拒否します)。",
    )
)


# ---------------------------------------------------------------------------
# priority_rule_gap_headway_implausible (warn, preflight) — QUEUE 3 unit 2
#
# Not a §-numbered catalog entry. The task brief's draft bounds ("critical
# gaps beyond roughly 2-10s, headways beyond roughly 1-8s") assumed headway
# is a TIME — disproven live (vissim_com.set_priority_rule_gap_headway's own
# module comment: headway_m/MinClear/MinHdwy is a spatial clearance in
# METRES, observed 0-30 across two real networks, not seconds). Bounds
# below are re-derived from what was actually measured, not the brief's
# un-probed guess:
#   - gap_time_s: warn only ABOVE 10s. The brief's own upper bound is kept
#     (real observed data tops out at 8s on Roundabout Buehl.DE, so 10s
#     already sits above every genuine value seen); the brief's LOWER bound
#     (2s) is dropped outright — 0s is the SINGLE MOST COMMON real value
#     (see priority_rule_gap_headway_invalid's own comment), so warning on
#     it would be constant noise on real data, not a signal.
#   - headway_m: warn only ABOVE 50m. No real value observed anywhere near
#     that (max seen: 30m on Roundabout Buehl.DE); 50m is roughly 1.7x the
#     observed maximum, a deliberately generous margin so ordinary real
#     values never trip it while a value an order of magnitude off (e.g. a
#     typo, or a unit mix-up with a much larger quantity) still does. Same
#     "0 is common and legitimate, never warn on the low end" reasoning as
#     gap_time_s.
# Fires per-field independently (a call can trip zero, one, or both).
# ---------------------------------------------------------------------------

_GAP_TIME_WARN_ABOVE_S = 10.0
_HEADWAY_WARN_ABOVE_M = 50.0


def _check_priority_rule_gap_headway_implausible(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_priority_rule_gap_headway":
        return None

    params = action.get("params", {})
    flags: list[str] = []

    gap_time_s = _coerce_number(params.get("gap_time_s"))
    if gap_time_s is not None and math.isfinite(gap_time_s) and gap_time_s > _GAP_TIME_WARN_ABOVE_S:
        flags.append(f"gap_time_s={gap_time_s}s (> {_GAP_TIME_WARN_ABOVE_S}s)")

    headway_m = _coerce_number(params.get("headway_m"))
    if headway_m is not None and math.isfinite(headway_m) and headway_m > _HEADWAY_WARN_ABOVE_M:
        flags.append(f"headway_m={headway_m}m (> {_HEADWAY_WARN_ABOVE_M}m)")

    if not flags:
        return None

    flags_text = "; ".join(flags)
    return Finding(
        constraint_id="priority_rule_gap_headway_implausible",
        tier="warn",
        phase="preflight",
        messages=_render("priority_rule_gap_headway_implausible", flags_text=flags_text),
        suggested_fix=None,
        details={
            "gap_time_s": gap_time_s,
            "headway_m": headway_m,
            "gap_time_warn_above_s": _GAP_TIME_WARN_ABOVE_S,
            "headway_warn_above_m": _HEADWAY_WARN_ABOVE_M,
        },
    )


register(
    Constraint(
        id="priority_rule_gap_headway_implausible",
        applies_to=["set_priority_rule_gap_headway"],
        phase="preflight",
        tier="warn",
        check=_check_priority_rule_gap_headway_implausible,
        message_ko="요청한 값이 실제 교통공학적으로 이례적으로 큽니다: {flags_text}. 의도한 값이 "
        "맞는지 확인해 주세요 — 단위 실수(예: 분 단위를 초 단위 자리에 입력)가 아닌지 점검하는 것이 "
        "좋습니다.",
        message_en="The requested value is unusually large for a real traffic-engineering "
        "parameter: {flags_text}. Please confirm this is intended — this shape often indicates a "
        "units mistake (e.g. entering minutes where seconds/metres were expected).",
        message_ja="指定された値は実際の交通工学パラメータとして異例に大きいです: {flags_text}。"
        "意図した値かご確認ください — 単位の誤り(例: 秒/メートルの代わりに分を入力)でないか確認する"
        "ことをお勧めします。",
    )
)


# ---------------------------------------------------------------------------
# Desired-speed DISTRIBUTION curve edits (set_des_speed_distribution_points)
# -- QUEUE 3 (v2a) unit 3, the "legitimate CC1 route" showcase.
#
# Every check below is informed by backend/tools/probe_des_speed_curves.py's
# own SILENT-FAILURE MAP (live-verified against Vissim 2026, "Roundabout
# Billings.US"), via the SAME atomic ``ReplaceAll`` write mechanism
# ``set_des_speed_distribution_points`` itself uses:
#
#   invariant violation                          Vissim's live behavior
#   -------------------------------------------   -----------------------
#   fewer than 2 points                            REJECTED (COM raises)
#   any speed < 0                                  REJECTED (COM raises)
#   speed genuinely DECREASES between points       SILENTLY ACCEPTED
#   any fraction outside [0, 1]                    REJECTED (COM raises)
#   first point's fraction != 0                    REJECTED (COM raises)
#   last point's fraction != 1                     REJECTED (COM raises)
#   fraction decreases in the MIDDLE (0/1 ends ok) SILENTLY ACCEPTED
#   absurdly large speed (e.g. 99999)              SILENTLY ACCEPTED
#   exact duplicate point (same speed AND frac)    SILENTLY ACCEPTED
#   x1 == x2 with DIFFERENT fractions (step fn)    ACCEPTED (legitimate --
#                                                   matches real PTV-
#                                                   authored data, see below)
#
# Every SILENTLY-ACCEPTED row gets its own refuse-tier check here so Vissim's
# gap becomes a structured refusal instead of a silently-corrupted curve;
# every REJECTED row is ALSO re-checked proactively (COM already protects
# it, but a raw COM error string reaching the user violates the guardrail
# doctrine's "refusal with a clear reason" standard every other tool in this
# module follows).
#
# **Speeds must be strictly increasing" (the task brief's own GUESS) is
# WRONG, disproven by real data, not just live probing:** a network-wide
# static scan of all 559 real ``<speedDistribution>`` elements across 25
# Vissim 2026 example networks found 19 real, PTV-authored distributions
# (all in "Rollercoaster.inpx") with a REPEATED speed value across two
# consecutive points (e.g. ``[(x=10, fx=0), (x=10, fx=1)]``) -- a legitimate
# near-instantaneous "step" desired speed. The checks below therefore refuse
# only a genuine DECREASE (``x[i+1] < x[i]``), never equality.
# ---------------------------------------------------------------------------

#: Highest real ``x`` (speed, km/h) observed across every real
#: ``<speedDistribution>`` in all 25 Vissim 2026 example networks: 205 km/h
#: (a Karlsruhe 3D autobahn-speed distribution). 250 gives real legitimate
#: high-speed data headroom while still catching the "wildly implausible"
#: case (the probe's own absurd_speed test entered 99999) -- reasoned, not
#: arbitrary: no real road vehicle's desired-speed distribution in any
#: catalogued network comes remotely close to this bound.
_SPEED_DISTRIBUTION_IMPLAUSIBLE_ABOVE_KMH = 250.0

#: [0, 1] float tolerance for the fraction endpoint checks (first == 0.0,
#: last == 1.0) -- mirrors guardrails.py's own shared 1e-9 float tolerance
#: (docs/phase2/DECISIONS.md, "float tolerance") so a value that is
#: genuinely 0.0/1.0 but arrives with harmless float noise (e.g. from a
#: prior round-trip) is never falsely refused.
_FRACTION_ENDPOINT_TOLERANCE = 1e-9


def _speed_distribution_points_from_params(
    params: dict[str, Any],
) -> list[tuple[float | None, float | None]] | None:
    """[(speed_kmh, cum_fraction), ...] from a proposed
    ``set_des_speed_distribution_points`` action's own ``points`` param, each
    coerced to ``float`` or ``None`` on failure -- ``None`` (the whole
    return) when ``points`` itself is missing or not a list, so every check
    below degrades silently rather than crashing on a malformed action (this
    runs on the LLM's OWN proposed params, before pydantic's write-tool
    model has ever validated them -- see guardrails.py's preflight, called
    against the raw decision dict)."""
    raw_points = params.get("points")
    if not isinstance(raw_points, list):
        return None
    result: list[tuple[float | None, float | None]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, dict):
            return None
        result.append(
            (_coerce_number(raw_point.get("speed_kmh")), _coerce_number(raw_point.get("cum_fraction")))
        )
    return result


def _check_des_speed_distribution_points_count_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_distribution_points":
        return None
    points = _speed_distribution_points_from_params(action.get("params", {}))
    if points is None or len(points) >= 2:
        return None

    return Finding(
        constraint_id="des_speed_distribution_points_count_invalid",
        tier="refuse",
        phase="preflight",
        messages=_render("des_speed_distribution_points_count_invalid", count=len(points)),
        suggested_fix=None,
        details={"point_count": len(points)},
    )


register(
    Constraint(
        id="des_speed_distribution_points_count_invalid",
        applies_to=["set_des_speed_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_des_speed_distribution_points_count_invalid,
        message_ko="속도분포 곡선은 최소 2개의 점이 필요합니다 (요청: {count}개). Vissim COM 자체가 "
        "\"Container SpeedDistributionDataPoints must contain at least 2 item(s)\"로 이 요청을 "
        "거부합니다.",
        message_en="A speed-distribution curve needs at least 2 points (requested: {count}). "
        "Vissim COM itself refuses this (\"Container SpeedDistributionDataPoints must contain at "
        "least 2 item(s)\").",
        message_ja="速度分布曲線には最低2点が必要です(要求: {count}点)。Vissim COM自体がこの要求を"
        "拒否します(\"Container SpeedDistributionDataPoints must contain at least 2 item(s)\")。",
    )
)


def _check_des_speed_distribution_points_speed_negative(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_distribution_points":
        return None
    points = _speed_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    offenders = [speed for speed, _ in points if speed is not None and speed < 0]
    if not offenders:
        return None

    offenders_text = ", ".join(str(v) for v in offenders)
    return Finding(
        constraint_id="des_speed_distribution_points_speed_negative",
        tier="refuse",
        phase="preflight",
        messages=_render("des_speed_distribution_points_speed_negative", offenders=offenders_text),
        suggested_fix=None,
        details={"negative_speeds_kmh": offenders},
    )


register(
    Constraint(
        id="des_speed_distribution_points_speed_negative",
        applies_to=["set_des_speed_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_des_speed_distribution_points_speed_negative,
        message_ko="음수 속도값은 허용되지 않습니다: {offenders} km/h. Vissim의 속도 하한은 0입니다.",
        message_en="Negative speed values are not allowed: {offenders} km/h. Vissim's own floor "
        "for speed is 0.",
        message_ja="負の速度値は許可されません: {offenders} km/h。Vissimの速度の下限は0です。",
    )
)


def _check_des_speed_distribution_points_speed_not_monotonic(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_distribution_points":
        return None
    points = _speed_distribution_points_from_params(action.get("params", {}))
    if not points or len(points) < 2:
        return None

    speeds = [speed for speed, _ in points]
    if any(speed is None for speed in speeds):
        return None

    for i in range(1, len(speeds)):
        if speeds[i] < speeds[i - 1]:
            return Finding(
                constraint_id="des_speed_distribution_points_speed_not_monotonic",
                tier="refuse",
                phase="preflight",
                messages=_render(
                    "des_speed_distribution_points_speed_not_monotonic",
                    index=i,
                    prev_speed=speeds[i - 1],
                    speed=speeds[i],
                ),
                suggested_fix=None,
                details={"speeds_kmh": speeds, "decrease_at_index": i},
            )
    return None


register(
    Constraint(
        id="des_speed_distribution_points_speed_not_monotonic",
        applies_to=["set_des_speed_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_des_speed_distribution_points_speed_not_monotonic,
        message_ko="속도값은 점을 따라 감소할 수 없습니다: 점 {index}의 속도 {speed} km/h가 이전 점의 "
        "{prev_speed} km/h보다 낮습니다. Vissim 자체는 이 오류를 조용히 허용하므로(silently "
        "accepted), 이 코드가 직접 거부합니다 (같은 속도가 연속되는 것은 허용됩니다 -- 실제 데이터에 "
        "존재하는 계단형 곡선).",
        message_en="Speed must not decrease across points: point {index}'s speed of {speed} km/h "
        "is lower than the previous point's {prev_speed} km/h. Vissim itself silently accepts this "
        "-- this refusal is this codebase's own screen (an EQUAL speed across consecutive points "
        "is allowed -- a legitimate step-function curve seen in real data).",
        message_ja="速度は点を追うごとに減少できません: 点{index}の速度 {speed} km/h が直前の点の "
        "{prev_speed} km/h より低くなっています。Vissim自体はこれを黙って受け入れるため、この拒否は"
        "本コードベース独自のチェックです(連続する点で同じ速度になることは許可されます — 実データに"
        "存在する階段状の曲線)。",
    )
)


def _check_des_speed_distribution_points_fraction_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_distribution_points":
        return None
    points = _speed_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    fractions = [fraction for _, fraction in points]
    if any(fraction is None for fraction in fractions):
        return None

    reason_ko = reason_en = reason_ja = None
    out_of_range = [f for f in fractions if f < 0.0 or f > 1.0]
    if out_of_range:
        text = ", ".join(str(v) for v in out_of_range)
        reason_ko = f"누적확률(fraction)이 [0, 1] 범위를 벗어났습니다: {text}."
        reason_en = f"Cumulative fraction outside [0, 1]: {text}."
        reason_ja = f"累積確率(fraction)が[0, 1]の範囲外です: {text}。"
    elif abs(fractions[0] - 0.0) > _FRACTION_ENDPOINT_TOLERANCE:
        reason_ko = f"첫 점의 fraction은 정확히 0.0이어야 합니다 (요청: {fractions[0]})."
        reason_en = f"The first point's fraction must be exactly 0.0 (requested: {fractions[0]})."
        reason_ja = f"最初の点のfractionは正確に0.0である必要があります(要求: {fractions[0]})。"
    elif abs(fractions[-1] - 1.0) > _FRACTION_ENDPOINT_TOLERANCE:
        reason_ko = f"마지막 점의 fraction은 정확히 1.0이어야 합니다 (요청: {fractions[-1]})."
        reason_en = f"The last point's fraction must be exactly 1.0 (requested: {fractions[-1]})."
        reason_ja = f"最後の点のfractionは正確に1.0である必要があります(要求: {fractions[-1]})。"
    else:
        for i in range(1, len(fractions)):
            if fractions[i] < fractions[i - 1]:
                reason_ko = (
                    f"fraction은 점을 따라 감소할 수 없습니다: 점 {i}의 {fractions[i]}가 이전 점의 "
                    f"{fractions[i - 1]}보다 낮습니다 (Vissim은 처음/끝 점만 검사하고 중간 값의 "
                    "역전은 조용히 허용합니다)."
                )
                reason_en = (
                    f"Fraction must not decrease across points: point {i}'s {fractions[i]} is "
                    f"lower than the previous point's {fractions[i - 1]} (Vissim only validates "
                    "the first/last points and silently accepts a reversal in the middle)."
                )
                reason_ja = (
                    f"fractionは点を追うごとに減少できません: 点{i}の{fractions[i]}が直前の点の "
                    f"{fractions[i - 1]}より低くなっています(Vissimは最初/最後の点のみ検証し、中間の"
                    "逆転は黙って受け入れます)。"
                )
                break

    if reason_ko is None:
        return None

    return Finding(
        constraint_id="des_speed_distribution_points_fraction_invalid",
        tier="refuse",
        phase="preflight",
        messages={"ko": reason_ko, "en": reason_en, "ja": reason_ja},
        suggested_fix=None,
        details={"cum_fractions": fractions},
    )


register(
    Constraint(
        id="des_speed_distribution_points_fraction_invalid",
        applies_to=["set_des_speed_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_des_speed_distribution_points_fraction_invalid,
        message_ko="{ko}",
        message_en="{en}",
        message_ja="{ja}",
    )
)


def _check_des_speed_distribution_points_speed_implausible(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_distribution_points":
        return None
    points = _speed_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    offenders = [
        speed
        for speed, _ in points
        if speed is not None and speed > _SPEED_DISTRIBUTION_IMPLAUSIBLE_ABOVE_KMH
    ]
    if not offenders:
        return None

    offenders_text = ", ".join(str(v) for v in offenders)
    return Finding(
        constraint_id="des_speed_distribution_points_speed_implausible",
        tier="warn",
        phase="preflight",
        messages=_render(
            "des_speed_distribution_points_speed_implausible",
            offenders=offenders_text,
            bound=_SPEED_DISTRIBUTION_IMPLAUSIBLE_ABOVE_KMH,
        ),
        suggested_fix=None,
        details={
            "implausible_speeds_kmh": offenders,
            "warn_above_kmh": _SPEED_DISTRIBUTION_IMPLAUSIBLE_ABOVE_KMH,
        },
    )


register(
    Constraint(
        id="des_speed_distribution_points_speed_implausible",
        applies_to=["set_des_speed_distribution_points"],
        phase="preflight",
        tier="warn",
        check=_check_des_speed_distribution_points_speed_implausible,
        message_ko="비현실적으로 큰 속도값입니다: {offenders} km/h ({bound} km/h 초과). 실제 25개 "
        "Vissim 예제 네트워크 전체에서 관측된 가장 높은 속도는 205 km/h였습니다 — 의도한 값이 맞는지, "
        "단위 실수(예: mph 값을 km/h로 그대로 입력)가 아닌지 확인해 주세요.",
        message_en="Unrealistically high speed value(s): {offenders} km/h (above {bound} km/h). "
        "The highest speed observed across all 25 real Vissim example networks was 205 km/h -- "
        "please confirm this is intended and not a units mistake (e.g. an mph value entered as "
        "km/h).",
        message_ja="非現実的に大きい速度値です: {offenders} km/h ({bound} km/h 超過)。実際の25個の"
        "Vissim例題ネットワーク全体で観測された最高速度は205 km/hでした — 意図した値かどうか、単位の"
        "誤り(例: mph値をそのままkm/hとして入力)でないかご確認ください。",
    )
)


def _check_des_speed_distribution_points_duplicate(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_distribution_points":
        return None
    points = _speed_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    seen: set[tuple[float, float]] = set()
    duplicates: list[tuple[float, float]] = []
    for speed, fraction in points:
        if speed is None or fraction is None:
            continue
        key = (speed, fraction)
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    if not duplicates:
        return None

    duplicates_text = ", ".join(f"({speed}, {fraction})" for speed, fraction in duplicates)
    return Finding(
        constraint_id="des_speed_distribution_points_duplicate",
        tier="warn",
        phase="preflight",
        messages=_render("des_speed_distribution_points_duplicate", duplicates=duplicates_text),
        suggested_fix=None,
        details={"duplicate_points": [{"speed_kmh": s, "cum_fraction": f} for s, f in duplicates]},
    )


register(
    Constraint(
        id="des_speed_distribution_points_duplicate",
        applies_to=["set_des_speed_distribution_points"],
        phase="preflight",
        tier="warn",
        check=_check_des_speed_distribution_points_duplicate,
        message_ko="완전히 동일한 점이 중복됩니다 (속도, fraction): {duplicates}. Vissim은 이를 "
        "허용하지만 곡선 모양에 아무 의미도 더하지 않습니다 -- 실수가 아닌지 확인해 주세요.",
        message_en="Exact duplicate point(s) (speed, fraction): {duplicates}. Vissim allows this, "
        "but a duplicate adds nothing to the curve's shape -- please confirm this is not a mistake.",
        message_ja="完全に同一の点が重複しています(速度, fraction): {duplicates}。Vissimはこれを"
        "許可しますが、曲線の形には何も追加しません -- 誤りでないかご確認ください。",
    )
)


# ---------------------------------------------------------------------------
# des_speed_distribution_points_shared_target (warn, preflight) -- the "⑧
# lesson": SpeedDistributions is ONE SHARED collection (unit ⑧'s own
# DECISIONS.md title), used by desired-speed decisions, vehicle
# compositions, reduced-speed areas, and more (see tools_network.
# _parse_speed_distributions' own docstring for the reference-counting
# machinery this reuses). Editing a distribution's curve changes it for
# EVERY referencing object simultaneously -- always warn when there is at
# least one reference, silent when the distribution is unreferenced (a
# scratch/unused distribution some caller is free to reshape without
# affecting anything else).
# ---------------------------------------------------------------------------


def _check_des_speed_distribution_points_shared_target(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_des_speed_distribution_points":
        return None

    params = action.get("params", {})
    distribution_no = str(params.get("distribution_no", ""))
    distribution = _find_by_field(shadow_model.get("des_speed_distributions", []), "no", distribution_no)
    if distribution is None:
        return None  # missing target is object_not_found's job (#1)

    referenced_by = distribution.get("referenced_by") or {}
    reference_count = distribution.get("reference_count", 0)
    if not reference_count:
        return None

    decisions = referenced_by.get("des_speed_decisions", {})
    compositions = referenced_by.get("vehicle_compositions", {})
    reduced_speed_areas = referenced_by.get("reduced_speed_areas", {})
    other = referenced_by.get("other", {})

    parts_ko: list[str] = []
    parts_en: list[str] = []
    parts_ja: list[str] = []
    for label, ref, unit_ko, unit_en, unit_ja in (
        (decisions, decisions, "개 희망속도결정", "desired-speed decision(s)", "件の希望速度決定"),
        (compositions, compositions, "개 차량조성 항목", "vehicle-composition entr(ies)", "件の車両構成項目"),
        (
            reduced_speed_areas,
            reduced_speed_areas,
            "개 저속구간",
            "reduced-speed area(s)",
            "件の低速区間",
        ),
    ):
        count = ref.get("count", 0)
        if not count:
            continue
        ids_text = ", ".join(ref.get("ids", []))
        suffix_ko = "" if not ref.get("truncated") else f" (총 {count}개 중 일부만 표시)"
        suffix_en = "" if not ref.get("truncated") else f" (showing some of {count})"
        suffix_ja = "" if not ref.get("truncated") else f" (合計{count}件中の一部のみ表示)"
        parts_ko.append(f"{count}{unit_ko}[{ids_text}]{suffix_ko}")
        parts_en.append(f"{count} {unit_en} [{ids_text}]{suffix_en}")
        parts_ja.append(f"{count}{unit_ja}[{ids_text}]{suffix_ja}")

    other_count = other.get("count", 0)
    if other_count:
        parts_ko.append(f"기타 네트워크 요소 {other_count}건 (개별 ID 없음)")
        parts_en.append(f"{other_count} other network element(s) (no individual ids)")
        parts_ja.append(f"その他のネットワーク要素{other_count}件(個別ID無し)")

    refs_ko = "; ".join(parts_ko)
    refs_en = "; ".join(parts_en)
    refs_ja = "; ".join(parts_ja)

    return Finding(
        constraint_id="des_speed_distribution_points_shared_target",
        tier="warn",
        phase="preflight",
        messages=_render(
            "des_speed_distribution_points_shared_target",
            distribution_no=distribution_no,
            reference_count=reference_count,
            refs_ko=refs_ko,
            refs_en=refs_en,
            refs_ja=refs_ja,
        ),
        suggested_fix=None,
        details={"distribution_no": distribution_no, "referenced_by": referenced_by, "reference_count": reference_count},
    )


register(
    Constraint(
        id="des_speed_distribution_points_shared_target",
        applies_to=["set_des_speed_distribution_points"],
        phase="preflight",
        tier="warn",
        check=_check_des_speed_distribution_points_shared_target,
        message_ko="속도분포 '{distribution_no}'는 공유 대상입니다 -- 총 {reference_count}건이 "
        "참조 중: {refs_ko}. 이 곡선을 수정하면 이들 전부에 동시에 영향을 줍니다.",
        message_en="Speed distribution '{distribution_no}' is a SHARED target -- {reference_count} "
        "reference(s) total: {refs_en}. Editing this curve affects every one of them at once.",
        message_ja="速度分布 '{distribution_no}' は共有対象です -- 合計{reference_count}件が参照中: "
        "{refs_ja}。この曲線を編集すると、これらすべてに同時に影響します。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_type_reference_target_not_found (refuse, preflight) -- QUEUE (v2a)
# unit
#
# Not a §-numbered catalog entry. COM itself already fails CLOSED on a bogus
# reference target (verified live, backend/tools/probe_vehicle_types.py:
# SetAttValue raises "Object 999999 of type Weight Distribution does not
# exist") -- this check exists so the refusal comes WITH a reason and a
# candidate list BEFORE ever touching COM, per the guardrail doctrine,
# mirroring des_speed_distribution_not_found's own precedent exactly. Which
# shadow-model list validates target_no depends on the action's own "field"
# param -- a vehicle type has EIGHT different reference kinds, each pointing
# at a DIFFERENT target collection (unlike des_speed_decision's single
# des_speed_distributions target).
# ---------------------------------------------------------------------------

#: {field: shadow_model key for that field's target collection} -- mirrors
#: tools_network._parse_vehicle_types' own reference_lookups table and
#: vissim_com.VissimController.VEHICLE_TYPE_REFERENCE_FIELDS' keys exactly.
_VEHICLE_TYPE_REFERENCE_TARGET_SPEC: dict[str, str] = {
    "model2d3d_distr": "model2d3d_distributions",
    "weight_distr": "weight_distributions",
    "power_distr": "power_distributions",
    "des_accel_func": "des_accel_functions",
    "max_accel_func": "max_accel_functions",
    "des_decel_func": "des_decel_functions",
    "max_decel_func": "max_decel_functions",
    "des_curve_speed_func": "curve_speed_functions",
}


def _check_vehicle_type_reference_target_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_type_reference":
        return None

    params = action.get("params", {})
    field = str(params.get("field", ""))
    shadow_key = _VEHICLE_TYPE_REFERENCE_TARGET_SPEC.get(field)
    if shadow_key is None:
        # An unknown field value never reaches here -- the params model's
        # own Literal enum already rejects it as invalid_params before
        # dispatch. Kept defensive (a fixture/future caller bypassing the
        # pydantic layer degrades silently rather than KeyErrors).
        return None

    targets = shadow_model.get(shadow_key, [])
    known_nos = [str(t.get("no", "")) for t in targets]
    known_no_set = set(known_nos)

    target_no = str(params.get("target_no", "")) if params.get("target_no") is not None else ""
    if not target_no or target_no in known_no_set:
        return None

    candidates = difflib.get_close_matches(target_no, known_nos, n=5) or sorted(known_no_set)[:20]
    return Finding(
        constraint_id="vehicle_type_reference_target_not_found",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "vehicle_type_reference_target_not_found",
            field=field,
            target_no=target_no,
            candidates=", ".join(candidates) if candidates else "-",
        ),
        suggested_fix={"candidates": candidates},
        details={"field": field, "target_no": target_no, "available": sorted(known_no_set)},
    )


register(
    Constraint(
        id="vehicle_type_reference_target_not_found",
        applies_to=["set_vehicle_type_reference"],
        phase="preflight",
        tier="refuse",
        check=_check_vehicle_type_reference_target_not_found,
        message_ko="대상 번호 '{target_no}'({field})가 이 네트워크에 존재하지 않습니다. 사용 가능한 "
        "번호: {candidates}.",
        message_en="Target '{target_no}' ({field}) does not exist in this network. "
        "Available numbers: {candidates}.",
        message_ja="対象番号 '{target_no}'({field})はこのネットワークに存在しません。利用可能な"
        "番号: {candidates}。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_type_shared_by_compositions (warn, preflight) -- QUEUE (v2a) unit
#
# The "⑧/③ lesson", the task brief's own explicit ask: a vehicle type is
# referenced by composition relFlows (tools_network._vehicle_type_
# composition_refs) -- editing ANY of its references (which distribution/
# function it uses) changes behavior for every vehicle of this type
# generated by every one of those compositions' demand streams at once.
# Always fires when there is at least one referencing composition, silent
# otherwise (an unreferenced/scratch vehicle type is free to reassign
# without warning) -- same policy as des_speed_distribution_points_shared_
# target and vehicle_composition_referenced_by_vehicle_inputs above.
# ---------------------------------------------------------------------------


def _check_vehicle_type_shared_by_compositions(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_vehicle_type_reference":
        return None

    params = action.get("params", {})
    vehicle_type_no = str(params.get("vehicle_type_no", ""))
    vehicle_type = _find_by_field(shadow_model.get("vehicle_types", []), "no", vehicle_type_no)
    if vehicle_type is None:
        return None  # missing target is object_not_found's job (#1)

    refs = vehicle_type.get("referencing_composition_nos") or {}
    count = refs.get("count", 0)
    if not count:
        return None

    ids = refs.get("ids", [])
    ids_ko, ids_en, ids_ja = _capped_join(ids, "개 차량조성", "vehicle composition(s)", "件の車両構成")
    truncated_note_ko = "" if not refs.get("truncated") else f" (총 {count}개 중 일부만 표시)"
    truncated_note_en = "" if not refs.get("truncated") else f" (showing some of {count})"
    truncated_note_ja = "" if not refs.get("truncated") else f" (合計{count}件中の一部のみ表示)"

    return Finding(
        constraint_id="vehicle_type_shared_by_compositions",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_type_shared_by_compositions",
            vehicle_type_no=vehicle_type_no,
            count=count,
            ids_ko=ids_ko + truncated_note_ko,
            ids_en=ids_en + truncated_note_en,
            ids_ja=ids_ja + truncated_note_ja,
        ),
        suggested_fix=None,
        details={"vehicle_type_no": vehicle_type_no, "referencing_compositions": refs},
    )


register(
    Constraint(
        id="vehicle_type_shared_by_compositions",
        applies_to=["set_vehicle_type_reference"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_type_shared_by_compositions,
        message_ko="차량 유형 '{vehicle_type_no}'는 {count}개의 차량 조성에서 참조됩니다: {ids_ko}. "
        "이 참조를 변경하면 해당 조성을 사용하는 모든 수요에 동시에 영향을 줍니다.",
        message_en="Vehicle type '{vehicle_type_no}' is referenced by {count} vehicle composition(s): "
        "{ids_en}. Changing this reference affects every demand stream using those compositions at once.",
        message_ja="車両タイプ '{vehicle_type_no}' は{count}件の車両構成から参照されています: {ids_ja}。"
        "この参照を変更すると、それらの構成を使用するすべての需要に同時に影響します。",
    )
)


# ---------------------------------------------------------------------------
# Time-distribution edits (set_time_distribution_normal /
# set_time_distribution_points) -- QUEUE (Q3, v2a) unit 5, dwell-time
# distributions.
#
# Not §-numbered catalog entries. Live-verified (backend/tools/
# probe_dwell_time_distributions.py, "BRT Priority Texas.US"): Net.
# TimeDistributions holds BOTH representations on every object (Mean/StdDev
# scalars, an EMPIRICAL-only DurDistrDataPts curve) but only ONE actually
# drives simulated sampling, selected by the object's own "type" attribute
# -- Vissim's own COM layer already refuses a Mean/StdDev write on an
# EMPIRICAL target ("Attribute Mean cannot be changed"), but the REVERSE
# direction (a points ReplaceAll against a NORMAL target's empty
# DurDistrDataPts) was not tested live against a real object (destructive,
# low-value probe step) -- time_distribution_type_mismatch refuses BOTH
# directions proactively rather than trusting the untested one to also fail
# closed, per the guardrail doctrine.
# ---------------------------------------------------------------------------


def _check_time_distribution_type_mismatch(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    tool = action.get("tool")
    if tool not in ("set_time_distribution_normal", "set_time_distribution_points"):
        return None

    params = action.get("params", {})
    distribution_no = str(params.get("time_distribution_no", ""))
    distribution = _find_by_field(shadow_model.get("time_distributions", []), "no", distribution_no)
    if distribution is None:
        return None  # missing target is object_not_found's job (#1)

    actual_type = str(distribution.get("type", "") or "").upper()
    expected_type = "NORMAL" if tool == "set_time_distribution_normal" else "EMPIRICAL"
    if actual_type == expected_type:
        return None

    other_tool = "set_time_distribution_points" if tool == "set_time_distribution_normal" else "set_time_distribution_normal"
    return Finding(
        constraint_id="time_distribution_type_mismatch",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "time_distribution_type_mismatch",
            distribution_no=distribution_no,
            actual_type=actual_type or "unknown",
            expected_type=expected_type,
            other_tool=other_tool,
        ),
        suggested_fix={"use_tool": other_tool},
        details={"distribution_no": distribution_no, "actual_type": actual_type, "tool": tool},
    )


register(
    Constraint(
        id="time_distribution_type_mismatch",
        applies_to=["set_time_distribution_normal", "set_time_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_time_distribution_type_mismatch,
        message_ko="시간분포 '{distribution_no}'의 실제 유형은 {actual_type}이며, 이 도구는 "
        "{expected_type} 분포에만 사용할 수 있습니다. {actual_type} 분포의 실제 표본 추출 값은 이 "
        "필드를 편집해도 바뀌지 않습니다 — 대신 {other_tool}을(를) 사용해 주세요.",
        message_en="Time distribution '{distribution_no}' is actually {actual_type}-type, but this "
        "tool only applies to {expected_type} distributions. Editing this field would not change "
        "what a {actual_type} distribution actually samples — use {other_tool} instead.",
        message_ja="時間分布 '{distribution_no}' の実際のタイプは{actual_type}であり、このツールは"
        "{expected_type}分布にのみ使用できます。{actual_type}分布の実際のサンプリング値はこの"
        "フィールドを編集しても変わりません — 代わりに{other_tool}を使用してください。",
    )
)


def _check_time_distribution_mean_or_stddev_negative(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_normal":
        return None

    params = action.get("params", {})
    bad_fields: dict[str, Any] = {}
    for field_name in ("mean_s", "std_dev_s"):
        raw = params.get(field_name)
        if raw is None:
            continue
        value = _coerce_number(raw)
        if value is None or not math.isfinite(value) or value < 0:
            bad_fields[field_name] = raw

    if not bad_fields:
        return None

    fields_text = ", ".join(f"{k}={v}" for k, v in bad_fields.items())
    return Finding(
        constraint_id="time_distribution_mean_or_stddev_negative",
        tier="refuse",
        phase="preflight",
        messages=_render("time_distribution_mean_or_stddev_negative", fields_text=fields_text),
        suggested_fix={"min": "0 or greater", "must_be": "finite"},
        details={"invalid_fields": bad_fields},
    )


register(
    Constraint(
        id="time_distribution_mean_or_stddev_negative",
        applies_to=["set_time_distribution_normal"],
        phase="preflight",
        tier="refuse",
        check=_check_time_distribution_mean_or_stddev_negative,
        message_ko="요청한 값이 유효하지 않습니다: {fields_text}. mean_s와 std_dev_s는 모두 0 이상의 "
        "유한한 값이어야 합니다 (Vissim 자체가 음수를 거부합니다 — \"is lower than minimum value of "
        "attribute Mean/Standard deviation (Min: 0 s)\"). std_dev_s=0은 실제 데이터에서 흔한 "
        "정상값입니다.",
        message_en="The requested value(s) are invalid: {fields_text}. Both mean_s and std_dev_s "
        "must be finite numbers >= 0 (Vissim itself rejects negative values — \"is lower than "
        "minimum value of attribute Mean/Standard deviation (Min: 0 s)\"). std_dev_s=0 is a common, "
        "legitimate value in real data.",
        message_ja="指定された値が無効です: {fields_text}。mean_s と std_dev_s は両方とも0以上の"
        "有限の値である必要があります(Vissim自体が負の値を拒否します — \"is lower than minimum "
        "value of attribute Mean/Standard deviation (Min: 0 s)\")。std_dev_s=0は実データでよく見ら"
        "れる正常な値です。",
    )
)


# Bound reasoning: a network-wide static scan across all 25 real Vissim 2026
# example networks found the largest REAL, non-sentinel mean value at 2700s
# (Karlsruhe 3D's own "2700 s (Parking Duration)" distribution) -- 3600s (1
# hour) gives that real value headroom while still catching two genuine
# sentinel-scale outliers also found in the same scan (Air Taxi Vertiport's
# "Permanent" = 9999s, Cableways' "Forever" = 99999s) as an intentional WARN,
# not a refusal -- these are legitimate PTV-authored data, but a human asked
# to confirm an hour-plus dwell/wait time is exactly the "legal but
# suspicious" case this tier exists for.
_TIME_DISTRIBUTION_MEAN_IMPLAUSIBLE_ABOVE_S = 3600.0


def _check_time_distribution_mean_implausible(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_normal":
        return None

    mean_s = _coerce_number(action.get("params", {}).get("mean_s"))
    if mean_s is None or not math.isfinite(mean_s) or mean_s <= _TIME_DISTRIBUTION_MEAN_IMPLAUSIBLE_ABOVE_S:
        return None

    return Finding(
        constraint_id="time_distribution_mean_implausible",
        tier="warn",
        phase="preflight",
        messages=_render(
            "time_distribution_mean_implausible",
            mean_s=mean_s,
            bound=_TIME_DISTRIBUTION_MEAN_IMPLAUSIBLE_ABOVE_S,
        ),
        suggested_fix=None,
        details={"mean_s": mean_s, "warn_above_s": _TIME_DISTRIBUTION_MEAN_IMPLAUSIBLE_ABOVE_S},
    )


register(
    Constraint(
        id="time_distribution_mean_implausible",
        applies_to=["set_time_distribution_normal"],
        phase="preflight",
        tier="warn",
        check=_check_time_distribution_mean_implausible,
        message_ko="비현실적으로 큰 평균값입니다: {mean_s}초 ({bound}초 초과). 실제 25개 Vissim 예제 "
        "네트워크 전체에서 관측된 가장 큰 (특수 목적이 아닌) 평균값은 2700초였습니다 — 의도한 값이 "
        "맞는지, 단위 실수(예: 분 단위 값을 초 단위 자리에 입력)가 아닌지 확인해 주세요.",
        message_en="Unrealistically large mean value: {mean_s}s (above {bound}s). The largest "
        "ordinary-purpose mean observed across all 25 real Vissim example networks was 2700s -- "
        "please confirm this is intended and not a units mistake (e.g. minutes entered where "
        "seconds were expected).",
        message_ja="非現実的に大きい平均値です: {mean_s}秒 ({bound}秒超過)。実際の25個のVissim例題"
        "ネットワーク全体で観測された(特殊目的でない)最大の平均値は2700秒でした — 意図した値かどうか、"
        "単位の誤り(例: 分単位の値を秒単位として入力)でないかご確認ください。",
    )
)


# ---------------------------------------------------------------------------
# Time-distribution DISTRIBUTION curve edits (set_time_distribution_points)
# -- mirrors QUEUE 3 (v2a) unit 3's own des_speed_distribution_points_*
# family exactly (same ReplaceAll mechanism, independently re-verified live
# for duration rather than assumed to generalize from speed -- see
# vissim_com.VissimController's own time-distribution module comment).
# ---------------------------------------------------------------------------


def _time_distribution_points_from_params(
    params: dict[str, Any],
) -> list[tuple[float | None, float | None]] | None:
    """[(duration_s, cum_fraction), ...] from a proposed
    ``set_time_distribution_points`` action's own ``points`` param, mirroring
    :func:`_speed_distribution_points_from_params` exactly (``None`` -- the
    whole return -- when ``points`` itself is missing/malformed, so every
    check below degrades silently)."""
    raw_points = params.get("points")
    if not isinstance(raw_points, list):
        return None
    result: list[tuple[float | None, float | None]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, dict):
            return None
        result.append(
            (_coerce_number(raw_point.get("duration_s")), _coerce_number(raw_point.get("cum_fraction")))
        )
    return result


def _check_time_distribution_points_count_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_points":
        return None
    points = _time_distribution_points_from_params(action.get("params", {}))
    if points is None or len(points) >= 2:
        return None

    return Finding(
        constraint_id="time_distribution_points_count_invalid",
        tier="refuse",
        phase="preflight",
        messages=_render("time_distribution_points_count_invalid", count=len(points)),
        suggested_fix=None,
        details={"point_count": len(points)},
    )


register(
    Constraint(
        id="time_distribution_points_count_invalid",
        applies_to=["set_time_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_time_distribution_points_count_invalid,
        message_ko="시간분포 곡선은 최소 2개의 점이 필요합니다 (요청: {count}개). Vissim COM 자체가 "
        "\"Container DurationDistributionDataPoints must contain at least 2 item(s)\"로 이 요청을 "
        "거부합니다.",
        message_en="A time-distribution curve needs at least 2 points (requested: {count}). "
        "Vissim COM itself refuses this (\"Container DurationDistributionDataPoints must contain "
        "at least 2 item(s)\").",
        message_ja="時間分布曲線には最低2点が必要です(要求: {count}点)。Vissim COM自体がこの要求を"
        "拒否します(\"Container DurationDistributionDataPoints must contain at least 2 item(s)\")。",
    )
)


def _check_time_distribution_points_duration_negative(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_points":
        return None
    points = _time_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    offenders = [duration for duration, _ in points if duration is not None and duration < 0]
    if not offenders:
        return None

    offenders_text = ", ".join(str(v) for v in offenders)
    return Finding(
        constraint_id="time_distribution_points_duration_negative",
        tier="refuse",
        phase="preflight",
        messages=_render("time_distribution_points_duration_negative", offenders=offenders_text),
        suggested_fix=None,
        details={"negative_durations_s": offenders},
    )


register(
    Constraint(
        id="time_distribution_points_duration_negative",
        applies_to=["set_time_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_time_distribution_points_duration_negative,
        message_ko="음수 시간값은 허용되지 않습니다: {offenders}초. Vissim의 시간 하한은 0입니다.",
        message_en="Negative duration values are not allowed: {offenders}s. Vissim's own floor "
        "for duration is 0.",
        message_ja="負の時間値は許可されません: {offenders}秒。Vissimの時間の下限は0です。",
    )
)


def _check_time_distribution_points_duration_not_monotonic(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_points":
        return None
    points = _time_distribution_points_from_params(action.get("params", {}))
    if not points or len(points) < 2:
        return None

    durations = [duration for duration, _ in points]
    if any(duration is None for duration in durations):
        return None

    for i in range(1, len(durations)):
        if durations[i] < durations[i - 1]:
            return Finding(
                constraint_id="time_distribution_points_duration_not_monotonic",
                tier="refuse",
                phase="preflight",
                messages=_render(
                    "time_distribution_points_duration_not_monotonic",
                    index=i,
                    prev_duration=durations[i - 1],
                    duration=durations[i],
                ),
                suggested_fix=None,
                details={"durations_s": durations, "decrease_at_index": i},
            )
    return None


register(
    Constraint(
        id="time_distribution_points_duration_not_monotonic",
        applies_to=["set_time_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_time_distribution_points_duration_not_monotonic,
        message_ko="시간값은 점을 따라 감소할 수 없습니다: 점 {index}의 시간 {duration}초가 이전 점의 "
        "{prev_duration}초보다 낮습니다. Vissim 자체는 이 오류를 조용히 허용하므로(silently "
        "accepted, 실측 확인됨), 이 코드가 직접 거부합니다 (같은 시간이 연속되는 것은 허용됩니다 -- "
        "실제 데이터에 존재하는 계단형 곡선).",
        message_en="Duration must not decrease across points: point {index}'s duration of "
        "{duration}s is lower than the previous point's {prev_duration}s. Vissim itself silently "
        "accepts this (live-verified) -- this refusal is this codebase's own screen (an EQUAL "
        "duration across consecutive points is allowed -- a legitimate step-function curve seen in "
        "real data).",
        message_ja="時間値は点を追うごとに減少できません: 点{index}の時間 {duration}秒 が直前の点の "
        "{prev_duration}秒 より低くなっています。Vissim自体はこれを黙って受け入れるため(実測確認済み)、"
        "この拒否は本コードベース独自のチェックです(連続する点で同じ時間になることは許可されます — "
        "実データに存在する階段状の曲線)。",
    )
)


_TIME_DISTRIBUTION_FRACTION_ENDPOINT_TOLERANCE = 1e-9


def _check_time_distribution_points_fraction_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_points":
        return None
    points = _time_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    fractions = [fraction for _, fraction in points]
    if any(fraction is None for fraction in fractions):
        return None

    reason_ko = reason_en = reason_ja = None
    out_of_range = [f for f in fractions if f < 0.0 or f > 1.0]
    if out_of_range:
        text = ", ".join(str(v) for v in out_of_range)
        reason_ko = f"누적확률(fraction)이 [0, 1] 범위를 벗어났습니다: {text}."
        reason_en = f"Cumulative fraction outside [0, 1]: {text}."
        reason_ja = f"累積確率(fraction)が[0, 1]の範囲外です: {text}。"
    elif abs(fractions[0] - 0.0) > _TIME_DISTRIBUTION_FRACTION_ENDPOINT_TOLERANCE:
        reason_ko = f"첫 점의 fraction은 정확히 0.0이어야 합니다 (요청: {fractions[0]})."
        reason_en = f"The first point's fraction must be exactly 0.0 (requested: {fractions[0]})."
        reason_ja = f"最初の点のfractionは正確に0.0である必要があります(要求: {fractions[0]})。"
    elif abs(fractions[-1] - 1.0) > _TIME_DISTRIBUTION_FRACTION_ENDPOINT_TOLERANCE:
        reason_ko = f"마지막 점의 fraction은 정확히 1.0이어야 합니다 (요청: {fractions[-1]})."
        reason_en = f"The last point's fraction must be exactly 1.0 (requested: {fractions[-1]})."
        reason_ja = f"最後の点のfractionは正確に1.0である必要があります(要求: {fractions[-1]})。"
    else:
        for i in range(1, len(fractions)):
            if fractions[i] < fractions[i - 1]:
                reason_ko = (
                    f"fraction은 점을 따라 감소할 수 없습니다: 점 {i}의 {fractions[i]}가 이전 점의 "
                    f"{fractions[i - 1]}보다 낮습니다 (Vissim은 처음/끝 점만 검사하고 중간 값의 "
                    "역전은 조용히 허용합니다)."
                )
                reason_en = (
                    f"Fraction must not decrease across points: point {i}'s {fractions[i]} is "
                    f"lower than the previous point's {fractions[i - 1]} (Vissim only validates "
                    "the first/last points and silently accepts a reversal in the middle)."
                )
                reason_ja = (
                    f"fractionは点を追うごとに減少できません: 点{i}の{fractions[i]}が直前の点の "
                    f"{fractions[i - 1]}より低くなっています(Vissimは最初/最後の点のみ検証し、中間の"
                    "逆転は黙って受け入れます)。"
                )
                break

    if reason_ko is None:
        return None

    return Finding(
        constraint_id="time_distribution_points_fraction_invalid",
        tier="refuse",
        phase="preflight",
        messages={"ko": reason_ko, "en": reason_en, "ja": reason_ja},
        suggested_fix=None,
        details={"cum_fractions": fractions},
    )


register(
    Constraint(
        id="time_distribution_points_fraction_invalid",
        applies_to=["set_time_distribution_points"],
        phase="preflight",
        tier="refuse",
        check=_check_time_distribution_points_fraction_invalid,
        message_ko="{ko}",
        message_en="{en}",
        message_ja="{ja}",
    )
)


# Bound reasoning: a network-wide static scan across all 25 real Vissim 2026
# example networks found the largest REAL point (x/duration) value inside
# any EMPIRICAL timeDistribution's own durDistrDataPts at 1800s (Karlsruhe
# 3D's distribution no=1050) -- 3600s (1 hour), matching this unit's own
# mean-implausible bound for consistency, gives that real value 2x headroom.
_TIME_DISTRIBUTION_POINTS_DURATION_IMPLAUSIBLE_ABOVE_S = 3600.0


def _check_time_distribution_points_duration_implausible(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_points":
        return None
    points = _time_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    offenders = [
        duration
        for duration, _ in points
        if duration is not None and duration > _TIME_DISTRIBUTION_POINTS_DURATION_IMPLAUSIBLE_ABOVE_S
    ]
    if not offenders:
        return None

    offenders_text = ", ".join(str(v) for v in offenders)
    return Finding(
        constraint_id="time_distribution_points_duration_implausible",
        tier="warn",
        phase="preflight",
        messages=_render(
            "time_distribution_points_duration_implausible",
            offenders=offenders_text,
            bound=_TIME_DISTRIBUTION_POINTS_DURATION_IMPLAUSIBLE_ABOVE_S,
        ),
        suggested_fix=None,
        details={
            "implausible_durations_s": offenders,
            "warn_above_s": _TIME_DISTRIBUTION_POINTS_DURATION_IMPLAUSIBLE_ABOVE_S,
        },
    )


register(
    Constraint(
        id="time_distribution_points_duration_implausible",
        applies_to=["set_time_distribution_points"],
        phase="preflight",
        tier="warn",
        check=_check_time_distribution_points_duration_implausible,
        message_ko="비현실적으로 큰 시간값입니다: {offenders}초 ({bound}초 초과). 실제 25개 Vissim "
        "예제 네트워크 전체에서 관측된 가장 큰 곡선 점 값은 1800초였습니다 — 의도한 값이 맞는지 "
        "확인해 주세요.",
        message_en="Unrealistically large duration value(s): {offenders}s (above {bound}s). The "
        "largest curve-point value observed across all 25 real Vissim example networks was 1800s "
        "-- please confirm this is intended.",
        message_ja="非現実的に大きい時間値です: {offenders}秒 ({bound}秒超過)。実際の25個のVissim"
        "例題ネットワーク全体で観測された曲線点の最大値は1800秒でした — 意図した値かどうかご確認くだ"
        "さい。",
    )
)


def _check_time_distribution_points_duplicate(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_time_distribution_points":
        return None
    points = _time_distribution_points_from_params(action.get("params", {}))
    if not points:
        return None

    seen: set[tuple[float, float]] = set()
    duplicates: list[tuple[float, float]] = []
    for duration, fraction in points:
        if duration is None or fraction is None:
            continue
        key = (duration, fraction)
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    if not duplicates:
        return None

    duplicates_text = ", ".join(f"({duration}, {fraction})" for duration, fraction in duplicates)
    return Finding(
        constraint_id="time_distribution_points_duplicate",
        tier="warn",
        phase="preflight",
        messages=_render("time_distribution_points_duplicate", duplicates=duplicates_text),
        suggested_fix=None,
        details={"duplicate_points": [{"duration_s": d, "cum_fraction": f} for d, f in duplicates]},
    )


register(
    Constraint(
        id="time_distribution_points_duplicate",
        applies_to=["set_time_distribution_points"],
        phase="preflight",
        tier="warn",
        check=_check_time_distribution_points_duplicate,
        message_ko="완전히 동일한 점이 중복됩니다 (시간, fraction): {duplicates}. Vissim은 이를 "
        "허용하지만 곡선 모양에 아무 의미도 더하지 않습니다 -- 실수가 아닌지 확인해 주세요.",
        message_en="Exact duplicate point(s) (duration, fraction): {duplicates}. Vissim allows "
        "this, but a duplicate adds nothing to the curve's shape -- please confirm this is not a "
        "mistake.",
        message_ja="完全に同一の点が重複しています(時間, fraction): {duplicates}。Vissimはこれを"
        "許可しますが、曲線の形には何も追加しません -- 誤りでないかご確認ください。",
    )
)


# ---------------------------------------------------------------------------
# time_distribution_shared_target (warn, preflight) -- the "⑧/③ lesson"
# generalized a third time: TimeDistributions is ONE SHARED collection,
# referenced by PT-stop dwell assignments, per-vehicle-class dwell
# overrides, driving-behavior CC1/zipper-overtake time, and further sites
# with no shadow-model object of their own (see tools_network.
# _parse_time_distributions' own docstring for the reference-counting
# machinery this reuses). Applies to BOTH write tools -- editing either
# representation (mean/stdDev OR points) of a shared distribution affects
# every referencing object simultaneously. Always warns when there is at
# least one reference, silent when the distribution is unreferenced.
# ---------------------------------------------------------------------------


def _check_time_distribution_shared_target(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    tool = action.get("tool")
    if tool not in ("set_time_distribution_normal", "set_time_distribution_points"):
        return None

    params = action.get("params", {})
    distribution_no = str(params.get("time_distribution_no", ""))
    distribution = _find_by_field(shadow_model.get("time_distributions", []), "no", distribution_no)
    if distribution is None:
        return None  # missing target is object_not_found's job (#1)

    referenced_by = distribution.get("referenced_by") or {}
    reference_count = distribution.get("reference_count", 0)
    if not reference_count:
        return None

    pt_stops = referenced_by.get("pt_stops", {})
    veh_class_overrides = referenced_by.get("pt_vehicle_class_overrides", {})
    driving_behaviors = referenced_by.get("driving_behaviors", {})
    other = referenced_by.get("other", {})

    parts_ko: list[str] = []
    parts_en: list[str] = []
    parts_ja: list[str] = []
    for ref, unit_ko, unit_en, unit_ja in (
        (pt_stops, "개 PT 정류장 대기시간 배정", "PT-stop dwell assignment(s)", "件のPT停留所停車時間割当"),
        (veh_class_overrides, "개 차종별 대기시간 재정의", "per-vehicle-class dwell override(s)", "件の車種別停車時間上書き"),
        (driving_behaviors, "개 주행행태(CC1/지퍼합류시간)", "driving-behavior CC1/zipper-overtake assignment(s)", "件の走行挙動(CC1/ジッパー合流時間)"),
    ):
        count = ref.get("count", 0)
        if not count:
            continue
        ids_text = ", ".join(ref.get("ids", []))
        suffix_ko = "" if not ref.get("truncated") else f" (총 {count}개 중 일부만 표시)"
        suffix_en = "" if not ref.get("truncated") else f" (showing some of {count})"
        suffix_ja = "" if not ref.get("truncated") else f" (合計{count}件中の一部のみ表示)"
        parts_ko.append(f"{count}{unit_ko}[{ids_text}]{suffix_ko}")
        parts_en.append(f"{count} {unit_en} [{ids_text}]{suffix_en}")
        parts_ja.append(f"{count}{unit_ja}[{ids_text}]{suffix_ja}")

    other_count = other.get("count", 0)
    if other_count:
        parts_ko.append(f"기타 네트워크 요소 {other_count}건 (개별 ID 없음)")
        parts_en.append(f"{other_count} other network element(s) (no individual ids)")
        parts_ja.append(f"その他のネットワーク要素{other_count}件(個別ID無し)")

    refs_ko = "; ".join(parts_ko)
    refs_en = "; ".join(parts_en)
    refs_ja = "; ".join(parts_ja)

    return Finding(
        constraint_id="time_distribution_shared_target",
        tier="warn",
        phase="preflight",
        messages=_render(
            "time_distribution_shared_target",
            distribution_no=distribution_no,
            reference_count=reference_count,
            refs_ko=refs_ko,
            refs_en=refs_en,
            refs_ja=refs_ja,
        ),
        suggested_fix=None,
        details={"distribution_no": distribution_no, "referenced_by": referenced_by, "reference_count": reference_count},
    )


register(
    Constraint(
        id="time_distribution_shared_target",
        applies_to=["set_time_distribution_normal", "set_time_distribution_points"],
        phase="preflight",
        tier="warn",
        check=_check_time_distribution_shared_target,
        message_ko="시간분포 '{distribution_no}'는 공유 대상입니다 -- 총 {reference_count}건이 "
        "참조 중: {refs_ko}. 이 값을 수정하면 이들 전부에 동시에 영향을 줍니다.",
        message_en="Time distribution '{distribution_no}' is a SHARED target -- {reference_count} "
        "reference(s) total: {refs_en}. Editing this value affects every one of them at once.",
        message_ja="時間分布 '{distribution_no}' は共有対象です -- 合計{reference_count}件が参照中: "
        "{refs_ja}。この値を編集すると、これらすべてに同時に影響します。",
    )
)


# ---------------------------------------------------------------------------
# #85 driving_behavior_parameter_range_invalid (refuse, preflight)
#
# QUEUE 3 unit 6. Proactively refuses the whitelisted driving-behavior
# scalars' known floor/ceiling violations -- live-verified
# (backend/tools/probe_driving_behaviors.py + follow-up probe scripts)
# Vissim's own SetAttValue rejects: ax < 0 ("Average standstill distance ...
# Min: 0"), lat_dist_driv_def < 0 / lat_dist_stand_def < 0 ("Lateral minimum
# distance ... Min: 0"), and saf_dist_fact_ln_chg outside [0, 1] ("Safety
# distance reduction factor ... Min: 0"/"... Max: 1", a genuine [0, 1]
# factor). Mirrors every prior unit's "refuse what Vissim refuses anyway,
# with a nicer bilingual message" precedent (set_priority_rule_gap_headway,
# set_des_speed_distribution_points, set_time_distribution_normal/points).
# bx_add/bx_mult/coop_ln_chg_speed_diff/min_speed_for_lat are DELIBERATELY
# excluded here -- live-verified Vissim itself ACCEPTS a negative value for
# all four; #86 (below) covers implausible MAGNITUDE across the whole
# whitelist instead.
# ---------------------------------------------------------------------------

#: {parameter: (min, max)} -- inclusive bounds Vissim's own COM layer
#: enforces (``None`` = no known bound on that side). Only the 4 fields with
#: a live-verified floor/ceiling appear here; the other 4 whitelisted fields
#: are absent by design (see this constraint's own module comment).
_DRIVING_BEHAVIOR_PARAM_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "ax": (0.0, None),
    "lat_dist_driv_def": (0.0, None),
    "lat_dist_stand_def": (0.0, None),
    "saf_dist_fact_ln_chg": (0.0, 1.0),
}


def _check_driving_behavior_parameter_range_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None

    params = action.get("params", {})
    parameter = str(params.get("parameter", ""))
    bounds = _DRIVING_BEHAVIOR_PARAM_BOUNDS.get(parameter)
    if bounds is None:
        return None
    value = _coerce_number(params.get("value"))
    if value is None:
        return None

    lo, hi = bounds
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return Finding(
            constraint_id="driving_behavior_parameter_range_invalid",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "driving_behavior_parameter_range_invalid",
                parameter=parameter,
                value=value,
                lo=lo if lo is not None else "-∞",
                hi=hi if hi is not None else "+∞",
            ),
            suggested_fix={"min": lo, "max": hi},
            details={"parameter": parameter, "value": value, "min": lo, "max": hi},
        )
    return None


register(
    Constraint(
        id="driving_behavior_parameter_range_invalid",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="refuse",
        check=_check_driving_behavior_parameter_range_invalid,
        message_ko="'{parameter}'={value}는 허용 범위[{lo}, {hi}]를 벗어납니다. Vissim이 자체적으로 "
        "거부하는 값이며 (COM 오류로 이어지기 전에) 여기서 거부합니다.",
        message_en="'{parameter}'={value} is outside the allowed range [{lo}, {hi}]. Vissim's own "
        "COM layer rejects this value -- refused here before it ever reaches a raw COM error.",
        message_ja="'{parameter}'={value} は許容範囲[{lo}, {hi}]外です。Vissim自体がこの値を拒否する"
        "ため、生のCOMエラーに至る前にここで拒否します。",
    )
)


# ---------------------------------------------------------------------------
# #86 driving_behavior_parameter_value_implausible (warn, preflight)
#
# QUEUE 3 unit 6. Vissim SILENTLY ACCEPTS an absurdly large magnitude on
# every whitelisted scalar (live-verified: 99999 accepted on saf_dist_fact_
# ln_chg was actually REJECTED by the [0,1] ceiling -- #85's job -- but
# bx_add/bx_mult/coop_ln_chg_speed_diff/min_speed_for_lat/ax/lat_dist_driv_
# def/lat_dist_stand_def all accept unbounded-upward values with no
# complaint) -- the same "silently accepts, warn on implausible magnitude"
# pattern _check_des_speed_distribution_points_speed_implausible/
# _check_time_distribution_mean_implausible already established for their
# own sibling collections. Bounds are generous engineering-judgment ceilings
# (never expected to fire on real calibrated data), not physical limits.
# ---------------------------------------------------------------------------

#: {parameter: implausible-above threshold} -- generous, not physical
#: (real Wiedemann-74/lane-change values observed across every network
#: probed this unit stay under 15; these are 10-60x that).
_DRIVING_BEHAVIOR_PARAM_IMPLAUSIBLE_ABOVE: dict[str, float] = {
    "ax": 100.0,  # meters
    "bx_add": 100.0,
    "bx_mult": 100.0,
    "coop_ln_chg_speed_diff": 300.0,  # km/h
    "min_speed_for_lat": 300.0,  # km/h
    "lat_dist_driv_def": 100.0,  # meters
    "lat_dist_stand_def": 100.0,  # meters
}


def _check_driving_behavior_parameter_value_implausible(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_driving_behavior_parameter":
        return None

    params = action.get("params", {})
    parameter = str(params.get("parameter", ""))
    threshold = _DRIVING_BEHAVIOR_PARAM_IMPLAUSIBLE_ABOVE.get(parameter)
    if threshold is None:
        return None
    value = _coerce_number(params.get("value"))
    if value is None or abs(value) <= threshold:
        return None

    return Finding(
        constraint_id="driving_behavior_parameter_value_implausible",
        tier="warn",
        phase="preflight",
        messages=_render(
            "driving_behavior_parameter_value_implausible",
            parameter=parameter,
            value=value,
            threshold=threshold,
        ),
        suggested_fix=None,
        details={"parameter": parameter, "value": value, "threshold": threshold},
    )


register(
    Constraint(
        id="driving_behavior_parameter_value_implausible",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="warn",
        check=_check_driving_behavior_parameter_value_implausible,
        message_ko="'{parameter}'={value}는 매우 비현실적인 크기입니다 (기준: |값| > {threshold}). "
        "Vissim은 이 값을 조용히 받아들이므로, 오타나 단위 착오가 아닌지 다시 확인하세요.",
        message_en="'{parameter}'={value} is an implausibly large magnitude (threshold: |value| > "
        "{threshold}). Vissim silently accepts this -- double-check it is not a typo or a unit "
        "mistake.",
        message_ja="'{parameter}'={value} は非現実的に大きな値です(基準: |値| > {threshold})。"
        "Vissimはこの値を黙って受け入れるため、誤入力や単位の誤りでないか再確認してください。",
    )
)


# ---------------------------------------------------------------------------
# #87 link_behavior_type_veh_class_override_not_found (refuse, preflight)
#
# QUEUE 3 unit 6. set_link_behavior_type_driving_behavior's SECOND-level
# lookup (does veh_class_no have an EXISTING per-vehicle-class override to
# reassign) -- the generic object_not_found/_TOOL_TARGET_SPEC machinery only
# covers the FIRST level (does link_behavior_type_no itself exist). Creating
# a BRAND-NEW override is a live-verified, reproducible pywin32/COM
# landmine (AddVehClassDrivingBehavior raises "The Python instance can not
# be converted to a COM object" -- see vissim_com.VissimController's own
# module comment) this codebase deliberately does not attempt -- a caller
# naming a vehicle class with no existing override gets this clean,
# structured refusal instead of a confusing failure deep in COM.
# ---------------------------------------------------------------------------


def _check_link_behavior_type_veh_class_override_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_link_behavior_type_driving_behavior":
        return None

    params = action.get("params", {})
    veh_class_no = params.get("veh_class_no")
    if veh_class_no is None:
        return None  # targeting the DEFAULT assignment -- no override lookup needed

    veh_class_no = str(veh_class_no)
    link_behavior_type_no = str(params.get("link_behavior_type_no", ""))
    lbt = _find_by_field(shadow_model.get("link_behavior_types", []), "no", link_behavior_type_no)
    if lbt is None:
        return None  # missing link-behavior-type is object_not_found's job (#1)

    overrides = lbt.get("overrides", [])
    if _find_by_field(overrides, "veh_class_no", veh_class_no) is not None:
        return None

    candidates = [str(o.get("veh_class_no", "")) for o in overrides]
    return Finding(
        constraint_id="link_behavior_type_veh_class_override_not_found",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "link_behavior_type_veh_class_override_not_found",
            link_behavior_type_no=link_behavior_type_no,
            veh_class_no=veh_class_no,
            candidates=", ".join(candidates) if candidates else "-",
        ),
        suggested_fix=candidates,
        details={
            "link_behavior_type_no": link_behavior_type_no,
            "veh_class_no": veh_class_no,
            "candidates": candidates,
        },
    )


register(
    Constraint(
        id="link_behavior_type_veh_class_override_not_found",
        applies_to=["set_link_behavior_type_driving_behavior"],
        phase="preflight",
        tier="refuse",
        check=_check_link_behavior_type_veh_class_override_not_found,
        message_ko="링크 행동유형 '{link_behavior_type_no}'에는 차량클래스 '{veh_class_no}'에 대한 "
        "기존 재정의가 없습니다. 이 에이전트는 새 차량클래스별 재정의를 생성할 수 없습니다(알려진 "
        "COM 제약) -- 기존 재정의만 재할당할 수 있습니다. 이 유형의 기존 재정의 차량클래스: "
        "{candidates}.",
        message_en="Link-behavior-type '{link_behavior_type_no}' has no EXISTING override for "
        "vehicle class '{veh_class_no}'. This agent cannot create a brand-new per-vehicle-class "
        "override (a known COM limitation) -- only an existing one can be reassigned. This "
        "type's existing override vehicle classes: {candidates}.",
        message_ja="リンク行動タイプ '{link_behavior_type_no}' には車両クラス '{veh_class_no}' の"
        "既存の上書きがありません。このエージェントは新しい車種別上書きを作成できません(既知の"
        "COM制約) -- 既存の上書きのみ再割り当て可能です。このタイプの既存上書き車両クラス: "
        "{candidates}。",
    )
)


# ---------------------------------------------------------------------------
# #88 link_behavior_type_driving_behavior_target_not_found (refuse, preflight)
#
# QUEUE 3 unit 6. Does the PROPOSED driving_behavior_no target actually
# exist? Mirrors des_speed_distribution_not_found's own split from
# set_des_speed_decision's primary-target lookup: object_not_found/
# _TOOL_TARGET_SPEC validates the object being EDITED
# (link_behavior_type_no); this validates the object being POINTED AT.
# ---------------------------------------------------------------------------


def _check_link_behavior_type_driving_behavior_target_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_link_behavior_type_driving_behavior":
        return None

    params = action.get("params", {})
    driving_behavior_no = str(params.get("driving_behavior_no", ""))
    behaviors = shadow_model.get("driving_behaviors", [])
    if _find_by_field(behaviors, "no", driving_behavior_no) is not None:
        return None

    candidates = difflib.get_close_matches(
        driving_behavior_no, [str(b.get("no", "")) for b in behaviors], n=5
    )
    return Finding(
        constraint_id="link_behavior_type_driving_behavior_target_not_found",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "link_behavior_type_driving_behavior_target_not_found",
            driving_behavior_no=driving_behavior_no,
            candidates=", ".join(candidates) if candidates else "-",
        ),
        suggested_fix=candidates,
        details={"driving_behavior_no": driving_behavior_no, "candidates": candidates},
    )


register(
    Constraint(
        id="link_behavior_type_driving_behavior_target_not_found",
        applies_to=["set_link_behavior_type_driving_behavior"],
        phase="preflight",
        tier="refuse",
        check=_check_link_behavior_type_driving_behavior_target_not_found,
        message_ko="주행행태 '{driving_behavior_no}'을(를) 찾을 수 없습니다. 후보: {candidates}.",
        message_en="Driving behavior '{driving_behavior_no}' was not found. Candidates: "
        "{candidates}.",
        message_ja="走行挙動 '{driving_behavior_no}' が見つかりません。候補: {candidates}。",
    )
)


# ---------------------------------------------------------------------------
# #89 rollback_invalidates_run_results (warn, postwrite) — QUEUE 3 unit 7
#
# Not a §-numbered catalog entry -- a defensive addition specific to the new
# rollback_last_edit tool. run_since_edit (extra["run_since_edit"], sourced
# from the tool's own verification payload -- see guardrails.py's
# GuardrailEngine.postwrite and services.agent.rollback.RollbackPlan's own
# docstring for how it is determined from the session's persisted history)
# is True when a run_simulation call succeeded AFTER the edit rollback_last_
# edit just undid. That run's KPIs were computed against a model that no
# longer exists once the rollback lands -- Vissim will not say so (it has no
# concept of "these numbers came from a network state you've since
# reverted"), so this is exactly the class of silent lie the validation
# layer exists to catch (docs/phase2/00-EXECUTION-GUIDE.md SS4). warn, not
# refuse -- the rollback itself is correct and intended; the point is to
# make sure nobody keeps trusting a now-stale run result, not to block
# undoing a bad edit.
# ---------------------------------------------------------------------------


def _check_rollback_invalidates_run_results(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "rollback_last_edit":
        return None
    if not extra.get("run_since_edit"):
        return None

    return Finding(
        constraint_id="rollback_invalidates_run_results",
        tier="warn",
        phase="postwrite",
        messages=_render("rollback_invalidates_run_results"),
        suggested_fix=None,
        details={"run_since_edit": True},
    )


register(
    Constraint(
        id="rollback_invalidates_run_results",
        applies_to=["rollback_last_edit"],
        phase="postwrite",
        tier="warn",
        check=_check_rollback_invalidates_run_results,
        message_ko="이 되돌리기 이전에 시뮬레이션을 실행한 적이 있습니다. 그 결과(KPI)는 방금 "
        "되돌린 편집이 반영된 모델을 기준으로 계산된 것이므로, 지금 모델 상태와 더 이상 일치하지 "
        "않습니다 -- 그 결과를 신뢰하지 말고, 필요하다면 다시 실행하세요.",
        message_en="A simulation was run after the edit that was just undone. Those results "
        "(KPIs) were computed against the model as it existed WITH that edit applied, so they "
        "no longer correspond to the model now -- do not trust that run's numbers; re-run if "
        "you need current results.",
        message_ja="この巻き戻しより前にシミュレーションを実行したことがあります。その結果"
        "（KPI）は、たった今取り消した編集が反映されたモデルを基準に計算されたものであり、"
        "現在のモデル状態とはもはや一致しません -- その結果は信頼せず、必要であれば再度実行して"
        "ください。",
    )
)


# ---------------------------------------------------------------------------
# #90 apply_edit_plan_validation (refuse+warn, preflight) — QUEUE 3 unit 8
#
# Not a §-numbered catalog entry -- a defensive addition specific to the new
# apply_edit_plan (batch/compound edits) tool. All of the real logic lives in
# services.agent.batch.validate_plan (plan structure, per-step preflight,
# same-target collisions, patched-shadow combined-effect detection, the
# always-present plan-overview finding) -- this check is a thin adapter,
# lazily importing that module (services.agent.batch imports THIS module at
# its own top level, so a top-level import here would be circular; the lazy
# import is the standard way this codebase breaks that class of cycle, e.g.
# claude_provider.py's lazy `import anthropic`).
#
# Returns a LIST of Finding (see run_checks' own updated docstring for why
# that is now a supported CheckFn shape) -- a single apply_edit_plan action
# can legitimately surface many independent findings in one pass (one per
# offending step, one per collision, one per combined-effect interaction,
# plus the overview), which no single-Finding-per-check shape could express.
# ---------------------------------------------------------------------------


def _check_apply_edit_plan_validation(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> list[Finding]:
    if action.get("tool") != "apply_edit_plan":
        return []
    from services.agent import batch  # lazy: breaks the constraints<->batch cycle

    return batch.validate_plan(action, shadow_model)


register(
    Constraint(
        id="apply_edit_plan_validation",
        applies_to=["apply_edit_plan"],
        phase="preflight",
        tier="refuse",
        check=_check_apply_edit_plan_validation,
        message_ko="배치 편집 계획 검증 (services.agent.batch.validate_plan 위임).",
        message_en="Batch edit plan validation (delegates to services.agent.batch.validate_plan).",
        message_ja="バッチ編集計画の検証(services.agent.batch.validate_planに委任)。",
    )
)

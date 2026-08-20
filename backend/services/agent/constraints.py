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
produce/agree on. THREE exceptions, each a pure-data/pure-function leaf with no
state, no side effects and no COM/session dependency, so none of them
reintroduces the coupling this rule exists to avoid:

- ``services.agent.mrwa_reference`` (Phase 0.3, the MRWA constraint/defaults
  pack, docs/product/master-roadmap-2026-07.md item 0.3) — so the MRWA table
  numbers a handful of checks below cite live in exactly one place, not
  copy-pasted into this file a second time.
- ``services.agent.car_following_grant`` (unit #44-b, 2026-08-19) — the
  structural key to ``driving_behavior_shared_object_edit``'s one narrow
  exception. It is pure dataclass + pure functions and imports only
  ``gates.signature`` (whose own runtime dependency is ``json``), so the
  stdlib-only property survives. See its module docstring for why the
  exception could not be a flag on the action.
- ``services.agent.searched_behaviour_grant`` (unit #51-b, 2026-08-20) — the
  structural key to that guardrail's SECOND narrow exception, for #51's searched
  parameters. Same shape and same leaf property: pure dataclass + pure
  functions over ``gates.signature``, ``mrwa_reference`` and
  ``car_following_grant``'s radius fingerprint, nothing else. A separate SPECIES
  rather than a widening of the first, so the two exceptions' signature spaces
  cannot mix (DECISIONS.md 2026-08-20).

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

from services.agent import car_following_grant, mrwa_reference, searched_behaviour_grant

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
    # Q4 unit (2): measurement-infrastructure placement -- queue counters,
    # data-collection points/measurements, travel-time sections. Each ADD
    # tool's PRIMARY target is the link it places against; each REMOVE
    # tool's target is the object's own "no" in the new shadow-model list
    # the parser added this unit (tools_network._parse_queue_counters/
    # _parse_data_collection_points/_parse_data_collection_measurements/
    # _parse_travel_time_sections). add_travel_time_section carries a
    # SECOND link target (to_link) with no single-param home in this
    # table -- mirrors set_route_relative_flow's/set_des_speed_decision's
    # own "primary here, deeper check elsewhere" split, except the deeper
    # check here is NOT a new constraints.py entry (this unit's own
    # "minimal constraints.py footprint" instruction) -- it is
    # vissim_com.VissimController._link_object_by_no's own fail-closed
    # COM raise, surfacing as tool_execution_error rather than a
    # preflight refusal. Same deliberate scope cut for
    # add_data_collection_measurement's point_nos (a LIST, no single-param
    # home at all) -- validated by vissim_com.py's own per-point existence
    # check before creation. Follow-up: promote both to dedicated preflight
    # checks (richer, difflib-suggested refusals) in a later unit.
    "add_queue_counter": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    "remove_queue_counter": {
        "object_class": "queue_counters",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    "add_data_collection_point": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    "remove_data_collection_point": {
        "object_class": "data_collection_points",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    "remove_data_collection_measurement": {
        "object_class": "data_collection_measurements",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    "add_travel_time_section": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "from_link",
    },
    "remove_travel_time_section": {
        "object_class": "travel_time_sections",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    # Q4 unit (3): full link closure + turn bans (movement removal). All
    # three verbs target the "links" object class (a connector IS a
    # "links" entry, distinguished only by its own is_connector field --
    # tools_network.py's shadow model already carries it, no parser change
    # needed this unit). Whether lane_index exists / whether connector_no
    # is genuinely a connector has no single flat id the generic
    # object_not_found machinery can check (mirrors set_lane_closure's own
    # documented gap, one entry above) -- enforced inline in
    # tools_edit.py's set_turn_ban (fails closed with a clear ValueError)
    # rather than a second dedicated constraint, per this unit's own
    # "constraints.py minimal" instruction.
    "set_link_closure": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_id",
    },
    "set_turn_ban": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "connector_no",
    },
    "remove_connector": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "connector_no",
    },
    # Q4 unit (4): signal-head + detector placement. Both ADD tools' PRIMARY
    # target is the link they place against (mirrors add_queue_counter/
    # add_data_collection_point's own precedent exactly) -- add_signal_head's
    # sc_no/sg_no and add_detector's optional sc_no get NO dedicated
    # preflight Finding here (same deliberate, documented scope cut as
    # add_travel_time_section's to_link/add_data_collection_measurement's
    # point_nos: a bogus one fails closed as a tool_execution_error via
    # vissim_com.py's own structured errors instead). Each REMOVE tool's
    # target is the object's own "no" in the new shadow-model list this unit
    # added (tools_network._parse_signal_heads/_parse_detectors).
    "add_signal_head": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    "remove_signal_head": {
        "object_class": "signal_heads",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    "add_detector": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    "remove_detector": {
        "object_class": "detectors",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    # Q4 unit (5): add/remove links & connectors. add_link has NO entry here
    # -- it creates a brand-new object, nothing pre-existing to check (same
    # "creates, does not target" shape every prior add_* tool with no
    # existing-object dependency would have, e.g. duplicate_driving_
    # behavior's own source_no IS checked because IT references an existing
    # object, unlike a from-scratch create). remove_link's target is the
    # link's own "no" in the "links" shadow-model list (unchanged shape,
    # inpx_parser.parse_network's own pre-existing field). add_connector's
    # PRIMARY target is from_link (mirrors add_travel_time_section's own
    # "primary here, to_link's existence fails closed via COM instead" split
    # exactly) -- to_link/from_lanes/to_lanes get no dedicated preflight
    # Finding here, same deliberate, documented scope cut.
    "remove_link": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "no",
    },
    "add_connector": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "from_link",
    },
    # Q4 unit (6): stop-sign + priority-rule/conflict-area PLACEMENT
    # (creation). add_stop_sign's PRIMARY target is the link it places
    # against (mirrors add_signal_head/add_queue_counter's own precedent
    # exactly) -- sc_no/sg_no get no dedicated preflight Finding here, same
    # deliberate, documented scope cut as add_detector's optional sc_no.
    # remove_stop_sign's target is the object's own "no" in the new
    # "stop_signs" shadow-model list this unit added
    # (tools_network._parse_stop_signs).
    "add_stop_sign": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    "remove_stop_sign": {
        "object_class": "stop_signs",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    # add_priority_rule's PRIMARY target is stop_link_no (mirrors add_
    # travel_time_section's own "primary here, deeper targets fail closed
    # via COM instead" split exactly) -- each marker's own link_no gets no
    # dedicated preflight Finding here, same deliberate, documented scope
    # cut as add_data_collection_measurement's point_nos. remove_priority_
    # rule's target is the EXISTING "priority_rules" object class QUEUE 3
    # unit 2 already wired here for set_priority_rule_gap_headway, just a
    # different param name ("no", matching every other remove_* tool's own
    # convention, rather than "priority_rule_no").
    "add_priority_rule": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "stop_link_no",
    },
    "remove_priority_rule": {
        "object_class": "priority_rules",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    # Q4 unit (7): PT stop + PT line placement (genesis). add_pt_stop's
    # PRIMARY target is the link it places against (mirrors add_stop_sign/
    # add_signal_head/add_queue_counter's own precedent exactly).
    # remove_pt_stop's target is the object's own "no" in the new "pt_stops"
    # shadow-model list this unit added (tools_network._parse_pt_stops) --
    # WHETHER it is safe to remove (zero real referrers) is the separate,
    # dedicated reference-integrity check inside tools_edit.remove_pt_stop
    # itself (services.inpx_structure.find_id_references), not a
    # constraints.py Finding -- mirrors remove_data_collection_point's own
    # precedent (Q4 unit (2)).
    "add_pt_stop": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    "remove_pt_stop": {
        "object_class": "pt_stops",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    # add_pt_line's PRIMARY target is entry_link_no (mirrors add_travel_
    # time_section's/add_connector's own "primary here, deeper targets fail
    # closed via COM instead" split exactly) -- dest_link_no/veh_type_no/
    # pt_stop_no get NO dedicated preflight Finding here, same deliberate,
    # documented scope cut as add_travel_time_section's to_link. remove_pt_
    # line's target is the new "pt_lines" shadow-model list this unit added
    # (tools_network._parse_pt_lines) -- no reference-integrity check needed
    # (a PT line is a leaf object, see tools_edit.remove_pt_line's own
    # docstring).
    "add_pt_line": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "entry_link_no",
    },
    "remove_pt_line": {
        "object_class": "pt_lines",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    # set_pt_line_stop_dwell has NO entry here -- its target is the TWO-part
    # (line_no, stop_no) pair, which this generic single-id machinery cannot
    # express (mirrors set_route_relative_flow's/set_des_speed_decision's own
    # split exactly) -- see the dedicated pt_line_stop_not_found check below,
    # which ALSO validates time_distribution_no in the same pass.
    #
    # Q5 unit (2): add_signal_controller's target is the DONOR it duplicates
    # -- the "signals" object class set_signal_cycle/set_signal_offset
    # already use, just a different param name ("source_sc_no"), exactly
    # mirroring duplicate_driving_behavior's own "source_no points at the
    # driving_behaviors class" entry. Whether the donor is of a duplicable
    # TYPE is a separate dedicated check
    # (signal_controller_duplicate_source_type_unsupported, below) -- the
    # same "existence is generic, appropriateness is dedicated" split
    # time_distribution_type_mismatch and des_speed_distribution_not_found
    # already established.
    "add_signal_controller": {
        "object_class": "signals",
        "id_field": "sc_no",
        "name_field": "sc_name",
        "param": "source_sc_no",
    },
    # Q5 unit (3): add_vehicle_input's target is the LINK it places demand
    # on -- the same "links" object class add_queue_counter/add_stop_sign/
    # add_signal_head already use, just a different param name. Whether that
    # link is a CONNECTOR is deliberately NOT this entry's business (it is a
    # real link either way, so #1 must stay silent): that is the dedicated
    # vehicle_input_on_connector warn below, the same "existence is generic,
    # appropriateness is dedicated" split add_signal_controller's own donor
    # type check already established.
    "add_vehicle_input": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    # Q6 unit (1): link geometry edits (lanes / length / curvature). All
    # three target an EXISTING link by its own "links" id -- the plainest
    # entries in this whole table, and (unlike every add_* entry above, whose
    # link param names where a NEW object is placed) the link IS the thing
    # being edited. Deeper checks have no single flat id the generic
    # machinery can express and live elsewhere by the established split:
    # whether shrinking would destroy lane-anchored objects is
    # tools_edit.set_link_lane_count's own reference-integrity refusal (the
    # remove_link/remove_pt_stop precedent, via services.inpx_structure);
    # whether point_index exists and is INTERIOR fails closed inside
    # tools_edit/vissim_com with the link's real point count named (the same
    # deliberate cut set_lane_closure's own lane_index documents above); and
    # the two genuinely semantic failures the probe measured DO get dedicated
    # checks below (link_geometry_truncates_anchored_objects,
    # link_lane_count_shifts_centerline).
    "set_link_lane_count": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_id",
    },
    "set_link_geometry": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_id",
    },
    "set_link_point_radius": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_id",
    },
    # Q6 unit (2): parking lots. add_parking_lot's PRIMARY target is the LINK
    # it places against (the same "links" object class add_pt_stop/
    # add_signal_head/add_queue_counter/add_vehicle_input already use, just a
    # different param name); whether lane_index exists on that link has no
    # single flat id the generic machinery can check and fails closed inside
    # vissim_com._lane_by_index with the link's real lane indices named --
    # the same deliberate, documented cut set_lane_closure's own lane_index
    # carries. set_parking_lot_capacity's and remove_parking_lot's targets
    # are the lot's own "no" in the new "parking_lots" shadow-model list this
    # unit added (tools_network._parse_parking_lots).
    #
    # Whether the requested capacity is realisable at all is deliberately NOT
    # this entry's business (the lot/link exist either way, so #1 must stay
    # silent): those are the dedicated parking_lot_capacity_unrealistic and
    # parking_lot_does_not_fit_link checks below -- the same "existence is
    # generic, appropriateness is dedicated" split add_signal_controller's
    # donor-type check and add_vehicle_input's connector warn established.
    # And WHETHER a lot is safe to remove (zero referring parking routes) is
    # the reference-integrity refusal inside tools_edit.remove_parking_lot
    # itself (services.inpx_structure.find_id_references), not a Finding --
    # mirroring remove_pt_stop's and remove_data_collection_point's own
    # precedent exactly.
    "add_parking_lot": {
        "object_class": "links",
        "id_field": "id",
        "name_field": "name",
        "param": "link_no",
    },
    "set_parking_lot_capacity": {
        "object_class": "parking_lots",
        "id_field": "no",
        "name_field": "name",
        "param": "parking_lot_no",
    },
    "remove_parking_lot": {
        "object_class": "parking_lots",
        "id_field": "no",
        "name_field": "name",
        "param": "no",
    },
    # Q6 unit (3): pedestrians. add_pedestrian_input's PRIMARY target is the
    # pedestrian AREA it originates on (the new "pedestrian_areas" shadow-model
    # list); the two other verbs target the input's own "no". Wired at BIRTH,
    # in BOTH places -- this table AND object_not_found's applies_to below --
    # because Q5 unit (3) found TEN already-shipped verbs that had an entry
    # here but were never named there, so check #1 silently never ran for any
    # of them.
    #
    # Whether the target area can legally ORIGINATE pedestrians (i.e. carries
    # a routing decision) is deliberately NOT this entry's business: the area
    # exists either way, so #1 must stay silent, and the dedicated
    # pedestrian_input_origin_area_has_no_route check below answers it. Same
    # "existence is generic, appropriateness is dedicated" split as
    # add_signal_controller's donor-type check and add_vehicle_input's
    # connector warn.
    "add_pedestrian_input": {
        "object_class": "pedestrian_areas",
        "id_field": "no",
        "name_field": "name",
        "param": "area_no",
    },
    "set_pedestrian_input_volume": {
        "object_class": "pedestrian_inputs",
        "id_field": "no",
        "name_field": "name",
        "param": "pedestrian_input_no",
    },
    "remove_pedestrian_input": {
        "object_class": "pedestrian_inputs",
        "id_field": "no",
        "name_field": "name",
        "param": "pedestrian_input_no",
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
    # Q4 unit (2): measurement-infrastructure placement.
    "queue_counters": {"ko": "대기행렬 카운터", "en": "queue counter", "ja": "キューカウンター"},
    "data_collection_points": {"ko": "데이터 수집 지점", "en": "data collection point", "ja": "データ収集ポイント"},
    "data_collection_measurements": {
        "ko": "데이터 수집 측정", "en": "data collection measurement", "ja": "データ収集測定",
    },
    "travel_time_sections": {"ko": "통행시간 구간", "en": "travel time section", "ja": "旅行時間区間"},
    # Q4 unit (4): signal-head + detector placement.
    "signal_heads": {"ko": "신호 헤드", "en": "signal head", "ja": "信号ヘッド"},
    "detectors": {"ko": "검지기", "en": "detector", "ja": "検知器"},
    # Q4 unit (6): stop-sign + priority-rule/conflict-area placement.
    "stop_signs": {"ko": "정지 표지판", "en": "stop sign", "ja": "一時停止標識"},
    # Q4 unit (7): PT stop + PT line placement.
    "pt_stops": {"ko": "대중교통 정류장", "en": "PT stop", "ja": "公共交通停留所"},
    "pt_lines": {"ko": "대중교통 노선", "en": "PT line", "ja": "公共交通路線"},
    # Q6 unit (2): parking lots.
    "parking_lots": {"ko": "주차장", "en": "parking lot", "ja": "駐車場"},
    # Q6 unit (3): pedestrians (Viswalk).
    "pedestrian_inputs": {"ko": "보행자 입력", "en": "pedestrian input", "ja": "歩行者インプット"},
    "pedestrian_areas": {"ko": "보행 구역", "en": "pedestrian area", "ja": "歩行者エリア"},
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
            # Q4 unit (2): measurement-infrastructure placement -- every
            # add/remove verb whose PRIMARY target has a single-param home
            # in _TOOL_TARGET_SPEC (add_data_collection_measurement is
            # deliberately absent -- see that table's own comment).
            "add_queue_counter",
            "remove_queue_counter",
            "add_data_collection_point",
            "remove_data_collection_point",
            "remove_data_collection_measurement",
            "add_travel_time_section",
            "remove_travel_time_section",
            # Q4 unit (4): signal-head + detector placement.
            "add_signal_head",
            "remove_signal_head",
            "add_detector",
            "remove_detector",
            # Q4 unit (6): stop-sign + priority-rule/conflict-area placement
            # (easy to forget -- unit (2)'s own review caught this once
            # already).
            "add_stop_sign",
            "remove_stop_sign",
            "add_priority_rule",
            "remove_priority_rule",
            # Q5 unit (2): add_signal_controller's DONOR must already exist
            # (easy to forget -- unit (2)'s own review caught this class of
            # miss twice before).
            "add_signal_controller",
            # Q5 unit (3): add_vehicle_input's target LINK must already exist.
            "add_vehicle_input",
            # GAP CLOSURE (2026-08-03): the ten verbs below each shipped with
            # a _TOOL_TARGET_SPEC entry but were never named HERE, so #1 never
            # ran for them at all -- a spec entry alone does NOTHING. Q5 unit
            # (3) found and reported this rather than absorbing it as a
            # drive-by; this is the unit that closes it. Several of those
            # verbs' own table comments above already claim they were "added
            # to the object_not_found / _TOOL_TARGET_SPEC lockstep table", so
            # the intent was always here -- only half the wiring had landed,
            # and a bogus link/connector/stop id fell through to a raw COM
            # error or a bare ValueError surfaced as tool_execution_error
            # instead of #1's structured refusal naming the real available ids
            # and difflib-suggesting near misses.
            #
            # Each was re-checked against its OWN tools_edit.py implementation
            # before being added here -- all ten target exactly ONE flat id in
            # exactly the object class their spec entry names, and all ten
            # currently fail on a bogus id with a message that neither lists
            # the real ids nor suggests near misses:
            #   add_connector     from_link     -> _lane_by_index COM raise
            #   add_pt_line       entry_link_no -> _link_object_by_no COM raise
            #   add_pt_stop       link_no       -> _lane_by_index COM raise
            #   remove_connector  connector_no  -> inpx_structure._find_connector ValueError
            #   remove_link       no            -> inline "link ... not found" ValueError
            #   remove_pt_line    no            -> _item_by_key COM raise
            #   remove_pt_stop    no            -> _item_by_key COM raise
            #   set_lane_closure  link_id       -> _lane_by_index COM raise
            #   set_link_closure  link_id       -> inline "link ... not found" ValueError
            #   set_turn_ban      connector_no  -> inline "connector ... not found" ValueError
            #
            # The three verbs that ALSO carry an inline APPROPRIATENESS check
            # keep it, unchanged and un-duplicated: set_turn_ban's "is this
            # really a connector", remove_link's "this is a CONNECTOR, use
            # remove_connector", and remove_connector's own "exists but is not
            # a connector" each fire on an id that DOES exist in "links" --
            # exactly the case where #1 stays silent. That is the same
            # "existence is generic, appropriateness is dedicated" split
            # add_signal_controller's donor-type check and add_vehicle_input's
            # vehicle_input_on_connector warn already established, not a
            # conflict with it.
            "add_connector",
            "add_pt_line",
            "add_pt_stop",
            "remove_connector",
            "remove_link",
            "remove_pt_line",
            "remove_pt_stop",
            "set_lane_closure",
            "set_link_closure",
            "set_turn_ban",
            # Q6 unit (1): link geometry edits. Wired AT BIRTH here, in the
            # same change that adds their _TOOL_TARGET_SPEC entries -- the
            # whole point of the gap closure above.
            "set_link_lane_count",
            "set_link_geometry",
            "set_link_point_radius",
            # Q6 unit (2): parking lots. Wired AT BIRTH here, in the same
            # change that adds their _TOOL_TARGET_SPEC entries -- the whole
            # point of the gap closure above.
            "add_parking_lot",
            "set_parking_lot_capacity",
            "remove_parking_lot",
            # Q6 unit (3): pedestrians (Viswalk). Wired AT BIRTH here, in the
            # same change that adds their _TOOL_TARGET_SPEC entries -- the
            # whole point of the gap closure above.
            "add_pedestrian_input",
            "set_pedestrian_input_volume",
            "remove_pedestrian_input",
            # NO deliberate omissions remain: set(_TOOL_TARGET_SPEC) - set(this
            # list) is now EMPTY, pinned by
            # test_object_not_found_applies_to_is_in_lockstep_with_the_spec_table.
            # add_data_collection_measurement -- the absence Q4 unit (2)'s
            # comment above points at -- was never half-wired: its point_nos is
            # a LIST with no single-param home, so it has no spec entry EITHER
            # and is absent from both halves by design. Any FUTURE deliberate
            # omission must be recorded here AND in that test's own allowlist.
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
#
# Q5 unit (3): "add_vehicle_input" added -- pure TABLE WIRING against this
# pre-existing check (same 0..20000 veh/h envelope, same refuse tier, only
# the param name differs), not a new constraint. Mirrors the precedent
# signal_head_detector_pos_out_of_range established when Q4 unit (6) wired
# add_stop_sign/add_priority_rule onto it: a creation verb's own value
# bounds are the same claim as the setter's, so they share one constraint id
# and one message rather than growing the registry.
# ---------------------------------------------------------------------------

#: {tool: the param carrying a veh/h volume}. Kept local to this one check.
_VOLUME_FIELDS: dict[str, str] = {
    "set_vehicle_input_volume": "volume",
    "add_vehicle_input": "volume_vph",
}


def _check_volume_out_of_range(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    volume_param = _VOLUME_FIELDS.get(action.get("tool", ""))
    if volume_param is None:
        return None

    params = action.get("params", {})
    raw_volume = params.get(volume_param)
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
        applies_to=list(_VOLUME_FIELDS.keys()),
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
# add/remove attribute on an EXISTING link. Defensive, tool-agnostic at the
# time this constraint was written (M0.3): no M2 write tool yet carried a
# lane-count-shaped param, so this guarded against a hallucinated or future
# naive tool call before it ever reached COM.
#
# **Q4 unit (5), LIVE-CAUGHT regression fix:** add_link's own ``lane_count``
# param is EXACTLY the sanctioned mechanism this constraint's own message
# describes ("Vissim fixes lane count at link construction — the lane-width
# array") -- it is not a naive "edit an existing link's lane count" attempt
# at all, it's a brand-new link's INITIAL lane count, the one place Vissim
# genuinely does let a caller set it. Without this exemption, EVERY
# add_link/add_connector call through the real orchestrator (which runs
# preflight; raw ``toolkit.dispatch`` calls, used by every unit test file in
# this codebase including this unit's own, do NOT) was unconditionally
# refused — reproduced live (backend/tools/add_links_connectors_live_check.py
# Phase B: a real run_agent_turn add_link call refused with THIS exact
# constraint id, 0.1s runtime, before this fix). ``_LANE_COUNT_PARAM_EXEMPT_
# TOOLS`` keeps the guard's original "no current M2 write tool" reasoning
# intact for every OTHER tool -- a hallucinated lane_count-shaped param on
# any pre-existing setter is still refused exactly as before.
# ---------------------------------------------------------------------------

# **Q6 unit (1): this constraint's own premise is now MEASURED FALSE for the
# COM layer, and the exemption list is what keeps it useful anyway.** Its text
# says "there is no COM add/remove-lane attribute" -- ``ILaneContainer.
# AddLane(Key, Width)``/``RemoveLane(Lane)`` genuinely exist and genuinely
# work on an EXISTING link (live-proven both directions,
# backend/tools/probe_link_geometry.py Parts A/B). What IS still true, and is
# the only part a naive caller could stumble into, is that a link's own
# ``NumLanes`` attribute is READ-ONLY ("put_AttValue failed - the attribute
# NUMLANES is not editable", measured) -- so a tool that merely carries a
# lane-count-shaped param and expects to set it is still refused correctly.
# ``set_link_lane_count`` is exempted here for exactly the reason
# ``add_link``/``add_connector`` are (Q4 unit (5)'s live-caught regression:
# without the exemption EVERY call of a verb carrying such a param is refused
# forever, silently, before it reaches COM) -- and this unit checked that trap
# deliberately rather than rediscovering it live, because that unit recorded
# it. The messages are left describing the delete-and-rebuild alternative for
# tools that genuinely have no other route; the honest route now has a name,
# and the ko/en/ja text points at it.
_LANE_COUNT_PARAM_NAMES = {"lane_count", "num_lanes", "lanes"}
_LANE_COUNT_PARAM_EXEMPT_TOOLS = frozenset({"add_link", "add_connector", "set_link_lane_count"})


def _check_lane_count_edit_unsupported(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") in _LANE_COUNT_PARAM_EXEMPT_TOOLS:
        return None
    params = action.get("params", {})
    for key in params:
        if str(key).lower() in _LANE_COUNT_PARAM_NAMES:
            return Finding(
                constraint_id="lane_count_edit_unsupported",
                tier="refuse",
                phase="preflight",
                messages=_render("lane_count_edit_unsupported", param=key),
                suggested_fix={
                    "hint": "use set_link_lane_count to change an existing link's lane count "
                    "(COM AddLane/RemoveLane), or set_lane_closure when the extra lane already "
                    "exists but is class-restricted"
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
        message_ko="이 도구의 '{param}' 파라미터로는 차로 수를 편집할 수 없습니다. 링크의 NumLanes "
        "속성 자체는 읽기 전용입니다(실측 확인). 기존 링크의 차로 수를 바꾸려면 "
        "set_link_lane_count를 사용하세요. 이미 존재하지만 제한된 차로를 여는 것은 "
        "set_lane_closure로 안전하게 할 수 있습니다.",
        message_en="Lane count cannot be edited through this tool's '{param}' parameter: a "
        "link's own NumLanes attribute is read-only (measured). Use set_link_lane_count to "
        "change an existing link's lane count, or set_lane_closure to reopen an "
        "already-existing but class-restricted lane.",
        message_ja="このツールの '{param}' パラメータで車線数を編集することはできません。リンクの"
        "NumLanes属性自体が読み取り専用です(実測確認)。既存リンクの車線数を変更するには"
        "set_link_lane_countを、既存だが制限されている車線を開放するにはset_lane_closureを"
        "使用してください。",
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
# CATALOG-GAP: unit U1 widened tools_run.py's run_result considerably, but
# NOT here — there is still no network-wide latent-demand measure in any
# result .db table (same unobtainability as deleted_vehicles, see
# tools_run.py's docstring); reads run_result["latent_demand_vehicles"]
# defensively.
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
# SUPPLIED since unit U1: tools_run.py builds run_result["los_by_movement"]
# ([{"movement", "los", "volume", "delay_s"}, ...]) from
# MOVEMENT_EvaluationTimeIntervalClass — VEHS is the volume, VEHDELAY banded
# through results_evidence.los_band is the grade. Still read defensively: the
# key is deliberately OMITTED when that table is absent from the result .db
# (see tools_run._collect_run_evidence's tri-state policy).
# ---------------------------------------------------------------------------


def _check_gridlock_reports_los_a(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    los_by_movement = run_result.get("los_by_movement")  # omitted when the table is absent
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
# SUPPLIED since unit U1: run_result["queue_results"] comes from
# MOVEMENT_EvaluationTimeInterval and run_result["queue_definition"] from the
# run .inpx's <evaluation queueBeginSpeed/queueEndSpeed/queueMaxClear/
# queueMaxLength/queueConsiderAdjacentLanes>. Fires only when queue KPI
# numbers ARE present but their defining parameters are not — exactly the
# risk the catalog names ("attach the definition to every queue answer").
# tools_run.py ships the two together or ships neither, so this fires on a
# genuinely unreadable definition and not on every run.
# ---------------------------------------------------------------------------


def _check_queue_length_definition_not_portable(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    queue_results = run_result.get("queue_results")  # omitted when the table is absent
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
# the excess delay simply isn't counted. SUPPLIED since unit U1:
# run_result["delay_segments"] ([{"node", "segment_length_m",
# "observed_queue_m"}, ...]) pairs the .inpx's <evaluation
# nodesDelaySegmentStart> against each node's worst QLENMAX from
# MOVEMENT_EvaluationTimeInterval. Still read defensively: the key is
# omitted when either side is unavailable.
# ---------------------------------------------------------------------------


def _check_delay_measurement_truncated_by_queue(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    segments = run_result.get("delay_segments")  # omitted when either side is unavailable
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
# cycle length samples the same point in the cycle every time.
# HALF-SUPPLIED since unit U1: run_result["evaluation_interval_s"] is now
# real (the interval the run was actually armed with). CATALOG-GAP remains on
# the other half — run_result["signal_cycle_s"] has no producer, so this
# check still cannot fire. Both are read defensively.
# ---------------------------------------------------------------------------


def _check_evaluation_interval_multiple_of_cycle(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    run_result = extra.get("run_result")
    if run_result is None:
        return None

    raw_interval = run_result.get("evaluation_interval_s")  # supplied since unit U1
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
# number). CATALOG-GAP: reads an optional per-link "geometry_edited_this_turn"
# flag, defensively.
#
# **Q6 unit (1): the tools this M0.3 check was written FOR finally exist.**
# Its original comment said "No M2 tool edits link geometry today", and the
# flag it watches is set by nothing at all -- no parser produces it, so this
# constraint has been inert since M0.3. ``set_link_geometry`` and
# ``set_link_lane_count`` are exactly the edits it describes (one moves the
# polyline, the other makes Vissim re-centre it -- measured), so the check now
# ALSO fires off the action's own link_id for those two tools, and the
# pre-existing flag path is left untouched for any future caller that can set
# it. ``set_link_point_radius`` is deliberately NOT included: it is the one
# geometry-adjacent write measured NOT to move the shape at all (x/y and
# Length2D unchanged), so nothing anchored to the link needs re-validating.
# This is a WARN about re-validating what is anchored; the REFUSAL that stops
# an edit which would silently relocate those same objects is the separate
# link_geometry_truncates_anchored_objects, below.
# ---------------------------------------------------------------------------

#: The write tools whose sanctioned action genuinely changes a link's drawn
#: shape (see the block comment above for why the radius verb is absent).
_GEOMETRY_EDITING_TOOLS = frozenset({"set_link_geometry", "set_link_lane_count"})


def _check_link_geometry_edit_reference_integrity(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    links = shadow_model.get("links", [])
    edited = [
        str(link.get("id", "?")) for link in links if link.get("geometry_edited_this_turn")
    ]
    if action.get("tool") in _GEOMETRY_EDITING_TOOLS:
        link_id = str(action.get("params", {}).get("link_id", "")).strip()
        if link_id and link_id not in edited:
            edited.append(link_id)
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
#
# ONE NARROW EXCEPTION, unit #44-b (2026-08-19). DECISIONS.md 2026-08-18
# recorded the collision: MRWA §5.2.5.1 calibrates the urban driving behaviour
# NETWORK-WIDE from one measured stop-line saturation flow, and every W74
# behaviour in the real consultant model is referenced at least once, so this
# guardrail refused exactly the edit the guideline prescribes. DECISIONS.md
# 2026-08-19 is the user's call on it, quoted:
#
#   "ask-first 승인 카드가 적용 범위를 문장으로 명시하고 ('행태 N을 참조하는
#   링크 M곳에 함께 적용됩니다') 사람이 읽고 승인하는 구조이므로, 이는 가드레일이
#   막으려던 '모르고 저지르는 광역 편집'이 아니라 '알고 승인하는 광역 편집'이다.
#   가드레일의 원래 목적(의도치 않은 공유 객체 편집 방지)은 예외 밖 전 경로에서
#   그대로 유지된다."
#
# The exception is keyed on STRUCTURE, never on a self-reported flag -- the
# same lesson this check's own rewrite above records. It stands down ONLY when
# ``services.agent.car_following_grant.evaluate`` returns GRANTED, which
# requires all of: a grant minted from a complete, measurement-backed Table 5-6
# proposal set; that grant naming THIS write by its canonical signature (so the
# exception reaches ax/bx_add/bx_mult at the table's own values and nothing
# else -- the other five whitelisted parameters and every W99 cc* name are
# uncovered by construction); the grant arriving through ``extra``, a channel
# written only by orchestrator.py from the write gate, never by any planner
# payload; a consent state from that gate; a blast radius that could be named in
# FULL, because a radius whose consumer list is truncated or absent is not a
# sentence a human can have read (refuter, #44-b — an un-named consumer's links
# can change by any amount without the drift check seeing it); and that radius,
# re-derived from the live shadow model, still equalling the one consent was
# given for. Anything short of that refuses here exactly as before, with THIS
# check's own message and no new name. The two constraints registered
# after this one are the exception's other half: one NAMES the consented radius
# (warn), one refuses BY NAME when the radius has drifted.
# ---------------------------------------------------------------------------


def _driving_behavior_consumer_label(consumer: dict[str, Any]) -> str:
    label = str(consumer.get("link_behavior_type_name") or consumer.get("link_behavior_type_no", "?"))
    veh_class_no = consumer.get("veh_class_no")
    if veh_class_no:
        label = f"{label} (veh class {veh_class_no})"
    return label


def _driving_behavior_target(
    action: dict[str, Any], shadow_model: dict[str, Any]
) -> dict[str, Any] | None:
    """The shadow model's OWN entry for a ``set_driving_behavior_parameter``
    target, or ``None`` when this action/model pair has nothing to say.

    Shared by the shared-object refusal and #44-b's two grant constraints so
    all three resolve the same target the same way — the drift check's
    right-hand side must come from the live model, never from the action or
    the grant.
    """
    if action.get("tool") != "set_driving_behavior_parameter":
        return None
    behaviors = shadow_model.get("driving_behaviors")
    if not behaviors:
        return None  # no reference data to check against yet
    # missing target is object_not_found's job (#1)
    return _find_by_field(behaviors, "no", str(action.get("params", {}).get("behavior_no", "")))


def _check_driving_behavior_shared_object_edit(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    behavior = _driving_behavior_target(action, shadow_model)
    if behavior is None:
        return None
    behavior_no = str(action.get("params", {}).get("behavior_no", ""))

    reference_count = behavior.get("reference_count", 0) or 0
    if reference_count == 0:
        return None  # a fresh, unshared (e.g. just-duplicated) copy is safe to edit in place

    # #44-b's narrow exception (see the section comment above). GRANTED is the
    # ONLY verdict that stands this refusal down; RADIUS_DRIFTED deliberately
    # does not, so a drifted blast radius is refused twice -- once generically
    # here, once by name below -- rather than resting on one new code path.
    if car_following_grant.evaluate(action, behavior, extra)["verdict"] == car_following_grant.GRANTED:
        return None

    # #51-b's narrow exception (2026-08-20), the SECOND one and the last.
    # Additive by species, never by widening: this is a different grant type
    # carrying a different provenance kind, so the call above and the call here
    # can never both answer GRANTED for one action -- each one's
    # ``provenance_from`` rejects the other's payload on BOTH discriminators.
    # Everything the first exception requires, this one requires too (an
    # approval-bound grant that names this write by canonical signature, a
    # consent state, a radius that is nameable in full and has not drifted), plus
    # one more: the arriving VALUE is re-checked against the guideline's own
    # published band, because a searched number's whole licence is that band.
    # Anything short of GRANTED refuses here exactly as before, with THIS check's
    # own message and no new name.
    if (
        searched_behaviour_grant.evaluate(action, behavior, extra)["verdict"]
        == searched_behaviour_grant.GRANTED
    ):
        return None

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
# #44-b car_following_shared_edit_consented (warn, preflight)
# #44-b car_following_consent_radius_drifted  (refuse, preflight)
#
# The other half of the exception documented in #40's section comment above.
# The decision (DECISIONS.md 2026-08-19) rests its entire safety argument on
# one thing: a human read a SENTENCE naming what the edit touches, and clicked.
# So the exception is not allowed to be silent, and it is not allowed to
# outlive the sentence:
#
# - ``car_following_shared_edit_consented`` is that sentence, emitted as a
#   warn-tier finding every time the exception fires. It rides both moments the
#   engine sees the write -- while the approval card is being BUILT (so the
#   radius is on the card the human reads) and again at dispatch -- and names
#   the behaviour, the consumers, the link total, the measured saturation flow
#   and the row. A guardrail that steps aside without saying so would be a
#   worse product than one that refuses.
# - ``car_following_consent_radius_drifted`` refuses when the reference graph
#   re-derived from the LIVE shadow model no longer equals the one named when
#   consent was given: consent was to a named radius, not to whatever the
#   behaviour happens to reach now. It carries both numbers so the refusal says
#   "동의는 N개였는데 지금은 M개" rather than merely "something changed".
#
# Both are inert outside the exception: with no grant in ``extra`` (every
# direct tool call, every ``fixes.preflight_findings`` call, every other unit)
# ``evaluate`` returns NO_GRANT and neither check produces anything.
# ---------------------------------------------------------------------------


def _radius_phrases(radius: dict[str, Any] | None) -> tuple[str, str, str]:
    """One blast radius as a ko/en/ja clause: consumers, links, and whether the
    link count is the WHOLE of it.

    ``car_following_grant.consent_radius``'s ``link_total`` sums only the consumers
    that were actually named, so on an INCOMPLETE radius it is a lower bound and has
    to read like one — printing it as a flat total is how a human ends up approving
    "링크 145개" for an edit that lands on 208. ``evaluate`` no longer GRANTS an
    incomplete radius at all, so this shape reaches a reader only through the
    drifted-radius refusal, which quotes both sides.
    """
    payload = radius or {}
    consumers = payload.get("reference_count", 0)
    links = payload.get("link_total", 0)
    if payload.get("complete"):
        return (
            f"참조 {consumers}곳 · 링크 {links}개",
            f"{consumers} consumer(s) · {links} link(s)",
            f"参照 {consumers}件 · リンク {links}本",
        )
    return (
        f"참조 {consumers}곳 · 명단에 오른 참조처만 합산해 링크 {links}개 이상 (전체 명단 확인 불가)",
        f"{consumers} consumer(s) · at least {links} link(s) (named consumers only — the "
        "full list could not be read)",
        f"参照 {consumers}件 · 名簿に載った参照先のみで リンク {links}本以上 (全体の名簿は確認不可)",
    )


def _car_following_grant_context(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(behavior, verdict)`` for a driving-behaviour write, or ``None`` when
    the action/model pair is not one this pair of checks can speak about."""
    behavior = _driving_behavior_target(action, shadow_model)
    if behavior is None:
        return None
    return behavior, car_following_grant.evaluate(action, behavior, extra)


def _check_car_following_shared_edit_consented(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    context = _car_following_grant_context(action, shadow_model, extra)
    if context is None:
        return None
    behavior, verdict = context
    if verdict["verdict"] != car_following_grant.GRANTED:
        return None
    if (behavior.get("reference_count", 0) or 0) == 0:
        # Nothing was ever refused here, so there is no exception to announce:
        # an unshared behaviour is plain-vanilla editable and always was.
        return None

    grant = verdict["grant"]
    radius = verdict["consented_radius"]
    radius_ko, radius_en, radius_ja = _radius_phrases(radius)
    labels = [
        _driving_behavior_consumer_label(c)
        for c in (behavior.get("referenced_by") or {}).get("consumers", [])
    ]
    consumers_ko, consumers_en, consumers_ja = _capped_join(labels, "곳", "consumer(s)", "件")
    measurement = grant.measurement or {}
    attribution = str(measurement.get("attribution") or "-")
    method = str(measurement.get("method") or "-")

    return Finding(
        constraint_id="car_following_shared_edit_consented",
        tier="warn",
        phase="preflight",
        messages=_render(
            "car_following_shared_edit_consented",
            behavior_id=str(action.get("params", {}).get("behavior_no", "")),
            parameter=str(action.get("params", {}).get("parameter", "")),
            value=action.get("params", {}).get("value"),
            radius_ko=radius_ko,
            radius_en=radius_en,
            radius_ja=radius_ja,
            consumers_ko=consumers_ko,
            consumers_en=consumers_en,
            consumers_ja=consumers_ja,
            saturation_flow_pcu=f"{grant.saturation_flow_pcu:g}",
            attribution=attribution,
            method=method,
        ),
        suggested_fix={
            "hint": "no fix required — this is the approved network-wide calibration; "
            "duplicate_driving_behavior instead if the intent is local after all",
            "set_id": grant.set_id,
        },
        details={
            "behavior_id": grant.behavior_no,
            "set_id": grant.set_id,
            "consent": verdict["consent"],
            "consented_radius": radius,
            "consumers": labels,
            "saturation_flow_pcu": grant.saturation_flow_pcu,
            "measurement": dict(measurement),
            "decision": "DECISIONS.md 2026-08-19",
        },
    )


register(
    Constraint(
        id="car_following_shared_edit_consented",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="warn",
        check=_check_car_following_shared_edit_consented,
        message_ko="행동유형 '{behavior_id}'의 {parameter}를 {value}로 바꾸는 이 편집은 공유 객체 "
        "편집입니다: {radius_ko}에 함께 적용됩니다 ({consumers_ko}). 거부하지 않는 이유는 이것이 "
        "MRWA §5.2.5.1의 의도 자체이기 때문입니다 — 측정된 정지선 포화교통류 "
        "{saturation_flow_pcu} pcu({attribution}, {method})로 Table 5-6의 한 행을 읽어 도심 "
        "주행 행태를 네트워크 전역에 보정하는 것. 즉 '모르고 저지르는 광역 편집'이 아니라 "
        "'알고 승인하는 광역 편집'입니다(DECISIONS.md 2026-08-19). 이 승인된 세트 밖에서는 "
        "공유 행태의 제자리 편집이 종전대로 거부됩니다.",
        message_en="This edit — {parameter} = {value} on behavior '{behavior_id}' — IS a "
        "shared-object edit: it applies to {radius_en} together ({consumers_en}). It is not "
        "refused because that is precisely MRWA §5.2.5.1's intent — one measured stop-line "
        "saturation flow of {saturation_flow_pcu} pcu ({attribution}, {method}) reading one row "
        "of Table 5-6 to calibrate the urban driving behaviour across the network. This is the "
        "'network-wide edit made knowingly', not the 'network-wide edit made by accident' the "
        "guardrail exists to stop (DECISIONS.md 2026-08-19). Outside this approved set, in-place "
        "edits of a shared behaviour are refused exactly as before.",
        message_ja="行動タイプ '{behavior_id}' の {parameter} を {value} に変更するこの編集は共有"
        "オブジェクトの編集です: {radius_ja}に同時に適用されます({consumers_ja})。拒否しないのは、"
        "これがMRWA §5.2.5.1の意図そのものだからです — 測定された停止線飽和交通流 "
        "{saturation_flow_pcu} pcu({attribution}、{method})でTable 5-6の一行を読み、都市の走行"
        "挙動をネットワーク全体に補正する。つまり「知らずに行う広域編集」ではなく「承知のうえで"
        "承認された広域編集」です(DECISIONS.md 2026-08-19)。この承認された組の外では、共有挙動の"
        "その場編集は従来どおり拒否されます。",
    )
)


def _check_car_following_consent_radius_drifted(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    context = _car_following_grant_context(action, shadow_model, extra)
    if context is None:
        return None
    _, verdict = context
    if verdict["verdict"] != car_following_grant.RADIUS_DRIFTED:
        return None

    grant = verdict["grant"]
    consented_ko, consented_en, consented_ja = _radius_phrases(verdict["consented_radius"])
    current_ko, current_en, current_ja = _radius_phrases(verdict["current_radius"])

    return Finding(
        constraint_id="car_following_consent_radius_drifted",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "car_following_consent_radius_drifted",
            behavior_id=grant.behavior_no,
            set_id=grant.set_id,
            consented_ko=consented_ko,
            consented_en=consented_en,
            consented_ja=consented_ja,
            current_ko=current_ko,
            current_en=current_en,
            current_ja=current_ja,
        ),
        suggested_fix={
            "hint": "rebuild the Table 5-6 proposal against the current network so a human "
            "reads and approves the radius as it is now",
            "consented_radius": verdict["consented_radius"],
            "current_radius": verdict["current_radius"],
        },
        details={
            "behavior_id": grant.behavior_no,
            "set_id": grant.set_id,
            "consent": verdict["consent"],
            "consented_radius": verdict["consented_radius"],
            "current_radius": verdict["current_radius"],
            "decision": "DECISIONS.md 2026-08-19",
        },
    )


register(
    Constraint(
        id="car_following_consent_radius_drifted",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="refuse",
        check=_check_car_following_consent_radius_drifted,
        message_ko="동의한 적용 범위가 달라져 이 편집을 거부합니다. 승인 시점에 명명된 범위는 "
        "{consented_ko}였는데, 지금 모델에서 다시 계산한 범위는 {current_ko}입니다. Table 5-6 "
        "차량추종 예외가 성립하는 근거는 사람이 '어디에 함께 적용되는지'를 읽고 승인했다는 "
        "사실뿐이므로, 그 반경이 바뀌면 동의는 더 이상 이 편집을 덮지 않습니다 — 현재 네트워크로 "
        "제안을 다시 만들어 바뀐 범위를 다시 승인받으십시오 (행동유형 '{behavior_id}', 세트 "
        "{set_id}).",
        message_en="Refusing this edit: the consented blast radius has drifted. When approval was "
        "given the radius named was {consented_en}; re-derived from the model now it is "
        "{current_en}. The Table 5-6 car-following exception stands on exactly one thing — that a "
        "human read where the edit lands and approved THAT — so once the radius moves, the "
        "consent no longer covers this write. Rebuild the proposal against the current network "
        "and have the new radius approved (behavior '{behavior_id}', set {set_id}).",
        message_ja="同意された適用範囲が変化したため、この編集を拒否します。承認時に示された範囲は"
        "{consented_ja}でしたが、現在のモデルから再導出した範囲は{current_ja}です。Table 5-6 の"
        "追従モデル例外が成り立つ根拠は、人が「どこに同時に適用されるか」を読んで承認したという"
        "事実だけであるため、その半径が変われば同意はこの書き込みを覆いません — 現在のネット"
        "ワークで提案を作り直し、変わった範囲を改めて承認してください(行動タイプ "
        "'{behavior_id}'、セット {set_id})。",
    )
)


# ---------------------------------------------------------------------------
# #51-b searched_behaviour_shared_edit_consented (warn, preflight)
# #51-b searched_behaviour_consent_radius_drifted (refuse, preflight)
#
# The other half of the SECOND exception, whose stand-down sits in #40's check
# above. DECISIONS.md 2026-08-20 extended the 2026-08-19 decision to #51's
# searched parameters, in the same shape and with the same narrowness, so these
# two constraints are #44-b's two with three differences that matter:
#
# - the evidence is a SEARCH, not a table lookup, so the warning carries the
#   search's own provenance -- candidate count, ledger reference, the unseen
#   holdout seed and the out-of-sample numbers, and the published band the value
#   was found inside -- instead of a measured saturation flow and a table row;
# - the warning says OUT LOUD that this evidence ranks one step BELOW Table
#   5-6's, because it is a value chosen inside a band rather than a value
#   measured (``searched_behaviour_grant.EVIDENCE_RANK``, the decision's own
#   closing paragraph). A guardrail that steps aside and then overstates why is
#   a worse product than one that refuses;
# - a value outside its published band is refused BY NAME even with a grant
#   present (``VALUE_OUT_OF_PUBLISHED_RANGE`` / ``PARAMETER_NOT_SEARCHABLE``,
#   checked in ``searched_behaviour_grant.evaluate`` on every arrival). Those
#   two verdicts refuse through #40's own message rather than a fourth
#   constraint id: they are "this grant does not license this write", which is
#   exactly what #40 already says.
#
# Both are inert outside the exception: with no searched grant in ``extra``
# (every direct tool call, every ``fixes.preflight_findings`` call, every Table
# 5-6 approval) ``evaluate`` returns NO_GRANT and neither check produces
# anything.
# ---------------------------------------------------------------------------


def _searched_grant_context(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(behavior, verdict)`` for a driving-behaviour write, or ``None`` when
    the action/model pair is not one this pair of checks can speak about."""
    behavior = _driving_behavior_target(action, shadow_model)
    if behavior is None:
        return None
    return behavior, searched_behaviour_grant.evaluate(action, behavior, extra)


def _searched_number(value: Any) -> str:
    """One number as a compact string, or ``"-"``. ``None`` never renders as 0."""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "-"


def _searched_band_phrase(grant: Any, parameter: str) -> str:
    """``"0.25-0.45 (op §5.2.5.1, p.188)"`` for the parameter being written."""
    for band in grant.provenance.get("bands") or ():
        if isinstance(band, dict) and band.get("parameter") == parameter:
            return (
                f"{_searched_number(band.get('low'))}-{_searched_number(band.get('high'))} "
                f"({band.get('citation')})"
            )
    return "-"


def _check_searched_behaviour_shared_edit_consented(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    context = _searched_grant_context(action, shadow_model, extra)
    if context is None:
        return None
    behavior, verdict = context
    if verdict["verdict"] != searched_behaviour_grant.GRANTED:
        return None
    if (behavior.get("reference_count", 0) or 0) == 0:
        # Nothing was ever refused here, so there is no exception to announce:
        # an unshared behaviour is plain-vanilla editable and always was.
        return None

    grant = verdict["grant"]
    radius = verdict["consented_radius"]
    radius_ko, radius_en, radius_ja = _radius_phrases(radius)
    labels = [
        _driving_behavior_consumer_label(c)
        for c in (behavior.get("referenced_by") or {}).get("consumers", [])
    ]
    consumers_ko, consumers_en, consumers_ja = _capped_join(labels, "곳", "consumer(s)", "件")
    provenance = dict(grant.provenance or {})
    ledger = dict(provenance.get("ledger") or {})
    parameter = str(action.get("params", {}).get("parameter", ""))
    rank = dict(provenance.get("evidence_rank") or {})

    return Finding(
        constraint_id="searched_behaviour_shared_edit_consented",
        tier="warn",
        phase="preflight",
        messages=_render(
            "searched_behaviour_shared_edit_consented",
            behavior_id=str(action.get("params", {}).get("behavior_no", "")),
            parameter=parameter,
            value=action.get("params", {}).get("value"),
            band=_searched_band_phrase(grant, parameter),
            radius_ko=radius_ko,
            radius_en=radius_en,
            radius_ja=radius_ja,
            consumers_ko=consumers_ko,
            consumers_en=consumers_en,
            consumers_ja=consumers_ja,
            candidates=provenance.get("candidates", 0),
            ledger_ref=f"{ledger.get('search_id') or '-'} ({ledger.get('rows', 0)} rows, "
            f"digest {ledger.get('digest') or '-'})",
            unseen_seed=provenance.get("unseen_seed"),
            holdout_nodes=", ".join(provenance.get("holdout_nodes") or []),
            holdout_before=_searched_number(provenance.get("holdout_objective_baseline")),
            holdout_after=_searched_number(provenance.get("holdout_objective_winner")),
            rank_ko=rank.get("ko", ""),
            rank_en=rank.get("en", ""),
            rank_ja=rank.get("ja", ""),
        ),
        suggested_fix={
            "hint": "no fix required — this is the approved, held-out search result; "
            "duplicate_driving_behavior instead if the intent is local after all",
            "search_id": grant.search_id,
        },
        details={
            "behavior_id": grant.behavior_no,
            "search_id": grant.search_id,
            "consent": verdict["consent"],
            "consented_radius": radius,
            "consumers": labels,
            "provenance": provenance,
            "evidence_rank_below": "TABLE_5_6_LOOKUP",
            "decision": "DECISIONS.md 2026-08-20",
        },
    )


register(
    Constraint(
        id="searched_behaviour_shared_edit_consented",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="warn",
        check=_check_searched_behaviour_shared_edit_consented,
        message_ko="행동유형 '{behavior_id}'의 {parameter}를 {value}로 바꾸는 이 편집은 공유 객체 "
        "편집입니다: {radius_ko}에 함께 적용됩니다 ({consumers_ko}). 거부하지 않는 이유는 이 값이 "
        "공표 범위 {band} 안에서 후보 {candidates}개를 실행한 탐색의 승자이고, 보정에 쓰지 않은 "
        "노드({holdout_nodes})에서 탐색이 쓴 적 없는 시드 {unseen_seed}로 재실행해 평균 GEH "
        "{holdout_before} → {holdout_after}로 개선됨을 확인했기 때문입니다. 탐색 원장: "
        "{ledger_ref}. {rank_ko} 즉 '모르고 저지르는 광역 편집'이 아니라 '알고 승인하는 광역 "
        "편집'입니다(DECISIONS.md 2026-08-20). 이 승인된 탐색 밖에서는 공유 행태의 제자리 편집이 "
        "종전대로 거부됩니다.",
        message_en="This edit — {parameter} = {value} on behavior '{behavior_id}' — IS a "
        "shared-object edit: it applies to {radius_en} together ({consumers_en}). It is not "
        "refused because this value is the winner of a search that ran {candidates} candidate(s) "
        "inside the published band {band}, and was then re-run on nodes held out of the "
        "calibration ({holdout_nodes}) at seed {unseen_seed} — one the search never used — where "
        "mean GEH improved {holdout_before} → {holdout_after}. Search ledger: {ledger_ref}. "
        "{rank_en} This is the 'network-wide edit made knowingly', not the 'network-wide edit "
        "made by accident' the guardrail exists to stop (DECISIONS.md 2026-08-20). Outside this "
        "approved search, in-place edits of a shared behaviour are refused exactly as before.",
        message_ja="行動タイプ '{behavior_id}' の {parameter} を {value} に変更するこの編集は共有"
        "オブジェクトの編集です: {radius_ja}に同時に適用されます({consumers_ja})。拒否しないのは、"
        "この値が公表範囲 {band} の中で候補{candidates}件を実行した探索の勝者であり、補正に使って"
        "いないノード({holdout_nodes})で探索が使ったことのないシード{unseen_seed}により再実行し、"
        "平均GEHが{holdout_before} → {holdout_after}に改善したことを確認したからです。探索台帳: "
        "{ledger_ref}。{rank_ja} つまり「知らずに行う広域編集」ではなく「承知のうえで承認された"
        "広域編集」です(DECISIONS.md 2026-08-20)。この承認された探索の外では、共有挙動のその場"
        "編集は従来どおり拒否されます。",
    )
)


def _check_searched_behaviour_consent_radius_drifted(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    context = _searched_grant_context(action, shadow_model, extra)
    if context is None:
        return None
    _, verdict = context
    if verdict["verdict"] != searched_behaviour_grant.RADIUS_DRIFTED:
        return None

    grant = verdict["grant"]
    consented_ko, consented_en, consented_ja = _radius_phrases(verdict["consented_radius"])
    current_ko, current_en, current_ja = _radius_phrases(verdict["current_radius"])

    return Finding(
        constraint_id="searched_behaviour_consent_radius_drifted",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "searched_behaviour_consent_radius_drifted",
            behavior_id=grant.behavior_no,
            search_id=grant.search_id,
            consented_ko=consented_ko,
            consented_en=consented_en,
            consented_ja=consented_ja,
            current_ko=current_ko,
            current_en=current_en,
            current_ja=current_ja,
        ),
        suggested_fix={
            "hint": "re-run the search against the current network so a human reads and "
            "approves the radius as it is now",
            "consented_radius": verdict["consented_radius"],
            "current_radius": verdict["current_radius"],
        },
        details={
            "behavior_id": grant.behavior_no,
            "search_id": grant.search_id,
            "consent": verdict["consent"],
            "consented_radius": verdict["consented_radius"],
            "current_radius": verdict["current_radius"],
            "decision": "DECISIONS.md 2026-08-20",
        },
    )


register(
    Constraint(
        id="searched_behaviour_consent_radius_drifted",
        applies_to=["set_driving_behavior_parameter"],
        phase="preflight",
        tier="refuse",
        check=_check_searched_behaviour_consent_radius_drifted,
        message_ko="동의한 적용 범위가 달라져 이 편집을 거부합니다. 승인 시점에 명명된 범위는 "
        "{consented_ko}였는데, 지금 모델에서 다시 계산한 범위는 {current_ko}입니다. 탐색 결과에 "
        "대한 예외가 성립하는 근거는 사람이 '어디에 함께 적용되는지'를 읽고 승인했다는 사실뿐이므로, "
        "그 반경이 바뀌면 동의는 더 이상 이 편집을 덮지 않습니다 — 현재 네트워크에서 탐색을 다시 "
        "돌려 바뀐 범위를 다시 승인받으십시오 (행동유형 '{behavior_id}', 탐색 {search_id}).",
        message_en="Refusing this edit: the consented blast radius has drifted. When approval was "
        "given the radius named was {consented_en}; re-derived from the model now it is "
        "{current_en}. The searched-parameter exception stands on exactly one thing — that a human "
        "read where the edit lands and approved THAT — so once the radius moves, the consent no "
        "longer covers this write. Re-run the search against the current network and have the new "
        "radius approved (behavior '{behavior_id}', search {search_id}).",
        message_ja="同意された適用範囲が変化したため、この編集を拒否します。承認時に示された範囲は"
        "{consented_ja}でしたが、現在のモデルから再導出した範囲は{current_ja}です。探索結果に対する"
        "例外が成り立つ根拠は、人が「どこに同時に適用されるか」を読んで承認したという事実だけである"
        "ため、その半径が変われば同意はこの書き込みを覆いません — 現在のネットワークで探索をやり直し、"
        "変わった範囲を改めて承認してください(行動タイプ '{behavior_id}'、探索 {search_id})。",
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
# add_priority_rule_marker_invalid (refuse, preflight) -- Q4 unit (6)'s ONE
# new constraint (this unit's own "constraints.py minimal" instruction:
# _TOOL_TARGET_SPEC entries + table wiring above + at most one small
# constraint here; everything else lives in services/vissim_com.py and
# services/agent/tools_edit.py, per those modules' own comments).
#
# The CREATION-side counterpart of priority_rule_gap_headway_invalid just
# above -- same "0 is a legitimate, common real value; only negative/
# non-finite is refused" semantics QUEUE 3 unit 2 already established
# (11/20 real Murdoch conflict markers carry minGapTime="0" -- re-measured
# 2026-08-03 straight from the XML during the Phase 0.3 review; the "13/20"
# this comment previously carried was the count of priority RULES, 13, not
# of zero-valued markers), extended from
# a single (gap_time_s, headway_m) pair to add_priority_rule's own LIST of
# per-marker pairs -- the shape mismatch (list vs scalar) is why this is a
# distinct check rather than a widened applies_to on the existing one
# (_check_priority_rule_gap_headway_invalid reads flat "gap_time_s"/
# "headway_m" params; add_priority_rule's own live in "markers[i]").
# ---------------------------------------------------------------------------


def _check_add_priority_rule_marker_invalid(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> list[Finding]:
    if action.get("tool") != "add_priority_rule":
        return []

    params = action.get("params", {})
    markers = params.get("markers") or []
    findings: list[Finding] = []
    for idx, marker in enumerate(markers, start=1):
        if not isinstance(marker, dict):
            continue
        bad_fields: dict[str, Any] = {}
        for field_name in ("gap_time_s", "headway_m"):
            raw = marker.get(field_name)
            if raw is None:
                continue
            value = _coerce_number(raw)
            if value is None or not math.isfinite(value) or value < 0:
                bad_fields[field_name] = raw
        if not bad_fields:
            continue
        fields_text = ", ".join(f"{k}={v}" for k, v in bad_fields.items())
        findings.append(
            Finding(
                constraint_id="add_priority_rule_marker_invalid",
                tier="refuse",
                phase="preflight",
                messages=_render(
                    "add_priority_rule_marker_invalid", marker_index=idx, fields_text=fields_text
                ),
                suggested_fix={"min": "0 or greater", "must_be": "finite"},
                details={"marker_index": idx, "invalid_fields": bad_fields},
            )
        )
    return findings


register(
    Constraint(
        id="add_priority_rule_marker_invalid",
        applies_to=["add_priority_rule"],
        phase="preflight",
        tier="refuse",
        check=_check_add_priority_rule_marker_invalid,
        message_ko="markers[{marker_index}]의 값이 유효하지 않습니다: {fields_text}. gap_time_s와 "
        "headway_m은 모두 0 이상의 유한한 값이어야 합니다 (Vissim 자체가 음수를 거부합니다).",
        message_en="markers[{marker_index}] has invalid value(s): {fields_text}. Both gap_time_s "
        "and headway_m must be finite numbers >= 0 (Vissim itself rejects negative values).",
        message_ja="markers[{marker_index}] の値が無効です: {fields_text}。gap_time_s と headway_m "
        "は両方とも0以上の有限の値である必要があります(Vissim自体が負の値を拒否します)。",
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
# priority_rule_gap_time_outside_mrwa_table_5_9_range (warn, preflight) --
# Phase 0.3 (MRWA constraint/defaults pack, docs/product/master-roadmap-
# 2026-07.md item 0.3). Consults services.agent.mrwa_reference.
# TABLE_5_9_GAP_ACCEPTANCE: warns when a proposed gap_time_s falls outside
# the [min, max] CRITICAL-GAP envelope spanning the two movement types this
# pack's own task brief transcribed from MRWA op §5 Table 5-9 (left turn
# 5.0s, crossing 2-lane 4.0s) -> envelope [4.0, 5.0]s.
#
# Movement type (left turn / crossing / through / ...) is NOT inferable from
# today's shadow model: a priority rule's own "affect_driv_dir" and each
# conflict marker's "effect_driv_dir" are Vissim's fixed FORWARD/REVERSE
# lane-direction attribute (live-verified, tools_network._parse_priority_
# rules' own docstring / backend/tools/probe_priority_rules.py -- every real
# example carries "FORWARD"), not a turn-movement label, and no other
# shadow-model field on a priority rule carries one either. Per this unit's
# own task brief ("if not inferable, check against the overall published
# envelope... and say so in the message"), this check therefore ALWAYS uses
# the envelope, and the rendered message says so explicitly rather than
# implying a movement-specific match against the caller's actual geometry.
#
# Only gap_time_s is compared. Table 5-9 also publishes a "follow-up
# headway" (also seconds) per movement, but Vissim's own priority-rule
# "headway_m" field is a spatial CLEARANCE in METRES despite its name (see
# priority_rule_gap_headway_invalid's own module comment for the
# live-verified landmine, and mrwa_reference.TABLE_5_9_GAP_ACCEPTANCE's own
# per-entry context strings) -- comparing a metres value against a seconds
# table would be exactly the units mistake this catalog exists to prevent,
# so headway_m is never touched by this check.
#
# Deliberately WARN, never refuse: the guideline is non-prescriptive by its
# own terms ("비규범적, 대안 방법론 수용", op p.5, per docs/product/
# tk-requirements-2026-07-31.md's own summary) -- a value outside the
# published envelope may still be exactly right for the real site, so this
# only makes the deviation visible, never blocks it.
# ---------------------------------------------------------------------------

_TABLE_5_9_GAP_LOW_S, _TABLE_5_9_GAP_HIGH_S = mrwa_reference.table_5_9_critical_gap_envelope()


def _check_priority_rule_gap_time_outside_mrwa_table_5_9_range(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_priority_rule_gap_headway":
        return None

    params = action.get("params", {})
    gap_time_s = _coerce_number(params.get("gap_time_s"))
    if gap_time_s is None or not math.isfinite(gap_time_s):
        return None
    if _TABLE_5_9_GAP_LOW_S <= gap_time_s <= _TABLE_5_9_GAP_HIGH_S:
        return None

    return Finding(
        constraint_id="priority_rule_gap_time_outside_mrwa_table_5_9_range",
        tier="warn",
        phase="preflight",
        messages=_render(
            "priority_rule_gap_time_outside_mrwa_table_5_9_range",
            gap_time_s=gap_time_s,
            low=_TABLE_5_9_GAP_LOW_S,
            high=_TABLE_5_9_GAP_HIGH_S,
        ),
        suggested_fix={"published_range_s": [_TABLE_5_9_GAP_LOW_S, _TABLE_5_9_GAP_HIGH_S]},
        details={"gap_time_s": gap_time_s, "range_s": [_TABLE_5_9_GAP_LOW_S, _TABLE_5_9_GAP_HIGH_S]},
    )


register(
    Constraint(
        id="priority_rule_gap_time_outside_mrwa_table_5_9_range",
        applies_to=["set_priority_rule_gap_headway"],
        phase="preflight",
        tier="warn",
        check=_check_priority_rule_gap_time_outside_mrwa_table_5_9_range,
        message_ko="gap_time_s={gap_time_s}s는 MRWA op §5 Table 5-9의 임계 간격(critical gap) 범위"
        "({low}s–{high}s)를 벗어납니다. 이 모델은 이동류(좌회전/횡단 등)를 자동으로 판별할 수 없어 "
        "Table 5-9가 다루는 두 이동류(좌회전 5.0s, 2차로 횡단 4.0s)를 아우르는 범위와 비교했습니다 "
        "— 실제 이동류에 맞는 값인지 현장 평가로 확인해 주세요.",
        message_en="gap_time_s={gap_time_s}s falls outside MRWA op §5 Table 5-9's critical-gap "
        "range ({low}s-{high}s). This model cannot infer the movement type (left turn / "
        "crossing / ...) automatically, so the value was checked against the envelope "
        "spanning the two movement types Table 5-9 covers here (left turn 5.0s, crossing "
        "2-lane 4.0s) — confirm this matches an on-site assessment for the actual movement.",
        message_ja="gap_time_s={gap_time_s}秒はMRWA op §5 Table 5-9の臨界間隔(critical gap)範囲"
        "({low}秒–{high}秒)を外れています。このモデルでは移動流(左折/横断など)を自動判別できないため、"
        "Table 5-9が扱う2つの移動流(左折5.0秒、2車線横断4.0秒)を包含する範囲と比較しました — 実際の"
        "移動流に適した値か現地評価でご確認ください。",
    )
)


# ---------------------------------------------------------------------------
# priority_rule_gap_time_below_mrwa_roundabout_minimum (warn, preflight) --
# Phase 0.3. Consults mrwa_reference.ROUNDABOUT_CRITICAL_GAP_MINIMUM: the
# most permissive published MRWA critical-gap minimum this pack carries
# anywhere is 1.5s (multi-lane roundabout circulating, cited "op p.53" --
# docs/meeting-brief-tk-mrwa.md §2 proposal ①) -- a proposed gap_time_s
# below that floor is worth surfacing regardless of whether this particular
# priority rule actually sits at a roundabout, since no MRWA table this pack
# encodes publishes anything lower.
#
# Node/geometry typing (single- vs multi-lane roundabout, or "is this a
# roundabout at all") is not in today's shadow model -- tools_network.py's
# node parsing carries no such field (verified: grep for "roundabout" across
# services/agent turns up only this constraint's own reasoning and pre-
# existing example-network filenames, never a parsed attribute) -- so this
# deliberately does NOT try to pick between the 2.1s (single-lane) and 1.5s
# (multi-lane) minimums; it uses the more permissive 1.5s floor (fewest false
# positives) and names BOTH numbers in the rendered message so a human
# reviewer can apply whichever actually matches the real geometry. This is
# this unit's documented answer to the task brief's "roundabout minimums
# where the geometry is identifiable" -- geometry is NOT identifiable here,
# so the check runs unconditionally against the permissive floor instead of
# being skipped outright, and says so in its own message.
#
# Distinct from priority_rule_gap_time_outside_mrwa_table_5_9_range just
# above (a different citation, a different claim -- "below every published
# MRWA minimum this pack has" vs "outside the turning/crossing envelope") --
# co-firing on the same low value is expected and additive, not redundant,
# mirroring priority_rule_gap_headway_invalid + _implausible's own
# established co-existence on this same tool.
# ---------------------------------------------------------------------------

_ROUNDABOUT_GAP_FLOOR_S = min(e.value for e in mrwa_reference.ROUNDABOUT_CRITICAL_GAP_MINIMUM.entries)
_ROUNDABOUT_GAP_SINGLE_LANE_S = max(e.value for e in mrwa_reference.ROUNDABOUT_CRITICAL_GAP_MINIMUM.entries)


def _check_priority_rule_gap_time_below_mrwa_roundabout_minimum(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_priority_rule_gap_headway":
        return None

    params = action.get("params", {})
    gap_time_s = _coerce_number(params.get("gap_time_s"))
    if gap_time_s is None or not math.isfinite(gap_time_s):
        return None
    if gap_time_s >= _ROUNDABOUT_GAP_FLOOR_S:
        return None

    return Finding(
        constraint_id="priority_rule_gap_time_below_mrwa_roundabout_minimum",
        tier="warn",
        phase="preflight",
        messages=_render(
            "priority_rule_gap_time_below_mrwa_roundabout_minimum",
            gap_time_s=gap_time_s,
            floor=_ROUNDABOUT_GAP_FLOOR_S,
            single_lane=_ROUNDABOUT_GAP_SINGLE_LANE_S,
        ),
        suggested_fix={
            "roundabout_minimums_s": {
                "single_lane": _ROUNDABOUT_GAP_SINGLE_LANE_S,
                "multi_lane": _ROUNDABOUT_GAP_FLOOR_S,
            }
        },
        details={"gap_time_s": gap_time_s, "floor_s": _ROUNDABOUT_GAP_FLOOR_S},
    )


register(
    Constraint(
        id="priority_rule_gap_time_below_mrwa_roundabout_minimum",
        applies_to=["set_priority_rule_gap_headway"],
        phase="preflight",
        tier="warn",
        check=_check_priority_rule_gap_time_below_mrwa_roundabout_minimum,
        message_ko="gap_time_s={gap_time_s}s는 MRWA가 공표한 회전교차로 임계 간격(critical gap) "
        "최솟값보다 낮습니다(다차로 순환 {floor}s, 단일차로 순환 {single_lane}s — op p.53). 이 "
        "우선순위 규칙이 회전교차로인지 이 모델에서 자동 판별할 수 없어 더 관대한 {floor}s를 "
        "기준으로 확인했습니다 — 실제 기하구조에 맞는 값인지 확인해 주세요.",
        message_en="gap_time_s={gap_time_s}s is below MRWA's published roundabout critical-gap "
        "minimum (multi-lane circulating {floor}s, single-lane circulating {single_lane}s — "
        "op p.53). This model cannot automatically tell whether this priority rule is a "
        "roundabout, so it was checked against the more permissive {floor}s floor — confirm "
        "this matches the real geometry.",
        message_ja="gap_time_s={gap_time_s}秒はMRWAが公表するラウンドアバウトの臨界間隔(critical "
        "gap)最小値を下回っています(多車線環道{floor}秒、単一車線環道{single_lane}秒 — op p.53)。"
        "この優先ルールがラウンドアバウトかどうかこのモデルでは自動判別できないため、より緩やかな"
        "{floor}秒を基準に確認しました — 実際の形状に適した値かご確認ください。",
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


# ---------------------------------------------------------------------------
# measurement_infra_pos_out_of_range (refuse, preflight) -- Q4 unit (2)'s
# ONE new constraint (this unit's own "constraints.py minimal" instruction:
# _TOOL_TARGET_SPEC entries above + at most one small constraint here;
# everything else lives in services/vissim_com.py and
# services/agent/tools_edit.py, per those modules' own comments).
#
# Task brief: "refuse pos < 0 or > link length" for every queue-counter/
# data-collection-point/travel-time-section placement -- a pos measured
# from the wrong end (or against the wrong link) silently places the
# object off the physical road, and Vissim itself does not refuse an
# out-of-range Pos on any of these Add methods (untested live by this
# unit's own probe -- a defensive check per the guardrail doctrine's
# "Vissim almost never refuses a bad edit" thesis, not a claimed silent-
# failure reproduction). A queue counter's proximity to the nearest stop
# line (the task brief's optional WARN tier) is deliberately NOT checked
# here: signal-head positions are not in the agent's own shadow model
# (tools_network.build_shadow_model) at all -- only inpx_parser.py's
# separate, frontend-only geometry resolver parses them -- so the check is
# not cheaply determinable today; the task brief's own escape hatch
# ("if not cheap, skip and note") applies.
# ---------------------------------------------------------------------------

# {tool: [(link_param, pos_param), ...]} -- add_travel_time_section carries
# TWO independent (link, pos) pairs; every other add tool here carries
# exactly one. Kept local to this one check (not merged into the shared,
# single-param-only _TOOL_TARGET_SPEC table above).
_MEASUREMENT_INFRA_POS_FIELDS: dict[str, list[tuple[str, str]]] = {
    "add_queue_counter": [("link_no", "pos_m")],
    "add_data_collection_point": [("link_no", "pos_m")],
    "add_travel_time_section": [("from_link", "from_pos_m"), ("to_link", "to_pos_m")],
}


def _check_measurement_infra_pos_out_of_range(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> list[Finding]:
    tool = action.get("tool", "")
    fields = _MEASUREMENT_INFRA_POS_FIELDS.get(tool)
    if fields is None:
        return []

    params = action.get("params", {})
    links = shadow_model.get("links", [])
    findings: list[Finding] = []
    for link_param, pos_param in fields:
        link_id = str(params.get(link_param, ""))
        pos = _coerce_number(params.get(pos_param))
        if pos is None:
            continue  # malformed param -- pydantic/param validation's job, not this check's
        link = _find_by_field(links, "id", link_id)
        if link is None:
            continue  # unknown link -- object_not_found's job (#1)
        length = _coerce_number(link.get("length"))
        if length is None:
            continue  # CATALOG-GAP: link carries no resolvable length -- no claim possible
        if 0 <= pos <= length:
            continue
        findings.append(
            Finding(
                constraint_id="measurement_infra_pos_out_of_range",
                tier="refuse",
                phase="preflight",
                messages=_render(
                    "measurement_infra_pos_out_of_range",
                    pos=pos,
                    link_id=link_id,
                    length=length,
                ),
                details={
                    "tool": tool,
                    "field": pos_param,
                    "link_id": link_id,
                    "pos": pos,
                    "link_length": length,
                },
            )
        )
    return findings


register(
    Constraint(
        id="measurement_infra_pos_out_of_range",
        applies_to=list(_MEASUREMENT_INFRA_POS_FIELDS.keys()),
        phase="preflight",
        tier="refuse",
        check=_check_measurement_infra_pos_out_of_range,
        message_ko="위치 {pos}m가 링크 {link_id}의 길이(0~{length}m) 범위를 벗어났습니다.",
        message_en="Position {pos} m falls outside link {link_id}'s own length (0-{length} m).",
        message_ja="位置 {pos}m がリンク {link_id} の長さ(0〜{length}m)の範囲外です。",
    )
)


# ---------------------------------------------------------------------------
# signal_head_detector_pos_out_of_range (refuse, preflight) -- Q4 unit (4)'s
# ONE new constraint (this unit's own "constraints.py minimal" instruction:
# _TOOL_TARGET_SPEC entries above + at most one small constraint here;
# everything else lives in services/vissim_com.py and
# services/agent/tools_edit.py, per those modules' own comments).
#
# Mirrors measurement_infra_pos_out_of_range's own shape exactly (refuse
# pos < 0 or > link length -- Vissim itself does not refuse an out-of-range
# Pos on either Add method, untested live by this unit's own probe, same
# defensive "Vissim almost never refuses a bad edit" reasoning) -- kept as
# its OWN constraint id/table rather than folded into the measurement-infra
# one, so add_signal_head/add_detector's own applies_to list stays
# independent of a sibling unit's table. A detector's own length_m and a
# zone running past the link's own end (pos + length_m > link length) are
# DELIBERATELY NOT checked here -- same "constraints.py minimal" scope cut
# measurement_infra_pos_out_of_range's own docstring already applies to a
# queue counter's proximity to the nearest stop line: a real, documented gap
# for a future unit, not silently assumed safe. A caller-omitted pos_m that
# tools_edit.add_detector's own stop-line-proxy default computes is NOT
# re-validated here either -- this check only ever sees the caller's own
# literal params (matching every other preflight check in this file); that
# tool's own defaulting logic refuses a negative result itself.
# ---------------------------------------------------------------------------

# {tool: (link_param, pos_param)} -- every tool here carries exactly one
# (link, pos) pair (unlike add_travel_time_section's two).
#
# Q4 unit (6): "add_stop_sign"/"add_priority_rule" added -- pure table
# wiring against this pre-existing, already-generic check (same field-pair
# shape, same "Vissim itself does not refuse an out-of-range Pos, untested
# live by this unit's own probe" reasoning), not a new constraint. Each
# marker's own (link, pos) inside add_priority_rule's "markers" list is
# DELIBERATELY NOT checked here -- same documented scope cut as add_
# travel_time_section's own to_link/measurement_infra's queue-counter-
# proximity gap; a bogus marker link/pos fails closed via COM instead (see
# tools_edit.add_priority_rule's own docstring).
_SIGNAL_HEAD_DETECTOR_POS_FIELDS: dict[str, tuple[str, str]] = {
    "add_signal_head": ("link_no", "pos_m"),
    "add_detector": ("link_no", "pos_m"),
    "add_stop_sign": ("link_no", "pos_m"),
    "add_priority_rule": ("stop_link_no", "stop_pos_m"),
}


def _check_signal_head_detector_pos_out_of_range(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    tool = action.get("tool", "")
    fields = _SIGNAL_HEAD_DETECTOR_POS_FIELDS.get(tool)
    if fields is None:
        return None
    link_param, pos_param = fields

    params = action.get("params", {})
    pos = _coerce_number(params.get(pos_param))
    if pos is None:
        return None  # malformed/omitted param -- pydantic validation's or the tool's own job

    link_id = str(params.get(link_param, ""))
    link = _find_by_field(shadow_model.get("links", []), "id", link_id)
    if link is None:
        return None  # unknown link -- object_not_found's job (#1)
    length = _coerce_number(link.get("length"))
    if length is None:
        return None  # CATALOG-GAP: link carries no resolvable length -- no claim possible
    if 0 <= pos <= length:
        return None

    return Finding(
        constraint_id="signal_head_detector_pos_out_of_range",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "signal_head_detector_pos_out_of_range",
            pos=pos,
            link_id=link_id,
            length=length,
        ),
        details={"tool": tool, "field": pos_param, "link_id": link_id, "pos": pos, "link_length": length},
    )


register(
    Constraint(
        id="signal_head_detector_pos_out_of_range",
        applies_to=list(_SIGNAL_HEAD_DETECTOR_POS_FIELDS.keys()),
        phase="preflight",
        tier="refuse",
        check=_check_signal_head_detector_pos_out_of_range,
        message_ko="위치 {pos}m가 링크 {link_id}의 길이(0~{length}m) 범위를 벗어났습니다.",
        message_en="Position {pos} m falls outside link {link_id}'s own length (0-{length} m).",
        message_ja="位置 {pos}m がリンク {link_id} の長さ(0〜{length}m)の範囲外です。",
    )
)


# ---------------------------------------------------------------------------
# link_closure_route_consequence (warn, preflight) -- Q4 unit (3)'s ONE new
# constraint (this unit's own "constraints.py minimal" instruction:
# _TOOL_TARGET_SPEC entries above + at most one small constraint here;
# everything else lives in services/inpx_structure.py and
# services/agent/tools_edit.py, per those modules' own comments).
#
# Task brief: closing/banning a link/connector that a route still traverses
# is not something Vissim reroutes for you -- the shadow model already has
# "routes" (v2 unit ⑥, tools_network._flatten_routes) with each route's own
# link_seq, so this is a straight membership check, no CATALOG-GAP. Fires
# in PREFLIGHT (not postwrite, unlike route_integrity_unverifiable/#31 --
# closing a link never changes its own existence or any route's link_seq,
# so the "before" shadow model already has every answer this check will
# ever need; showing it BEFORE approval, on the SAME action a user is about
# to confirm, is strictly more useful than after the fact). Only fires on
# the CLOSING/BANNING direction (closed=True / banned=True) -- reopening/
# un-banning never worsens route reachability, so there is nothing to warn
# about there.
#
# Deliberately does NOT cover remove_connector: that verb's own route
# consequence is a REFUSAL (a route's link_seq left dangling is a genuine
# reference-integrity violation, not merely an operational caveat) --
# enforced by services.inpx_structure.remove_connector itself via the
# spine's find_id_references (see that function's own module comment,
# extended ID_REFERENCE_SITES["links"] with a "route link sequence" site
# this same unit), never by this constraint.
# ---------------------------------------------------------------------------

_LINK_CLOSURE_ROUTE_CONSEQUENCE_FIELDS: dict[str, tuple[str, str]] = {
    # tool -> (target_id_param, "close"-direction param name)
    "set_link_closure": ("link_id", "closed"),
    "set_turn_ban": ("connector_no", "banned"),
}


def _check_link_closure_route_consequence(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    tool = action.get("tool", "")
    fields = _LINK_CLOSURE_ROUTE_CONSEQUENCE_FIELDS.get(tool)
    if fields is None:
        return None
    id_param, direction_param = fields
    params = action.get("params", {})
    if not params.get(direction_param, True):
        return None  # reopening/un-banning -- nothing to warn about

    target_id = str(params.get(id_param, ""))
    if not target_id:
        return None

    routes = shadow_model.get("routes") or []
    affected = [
        str(r.get("id", "?")) for r in routes if target_id in (r.get("link_seq") or [])
    ]
    if not affected:
        return None

    routes_ko, routes_en, routes_ja = _capped_join(affected, "개", "routes", "件")
    return Finding(
        constraint_id="link_closure_route_consequence",
        tier="warn",
        phase="preflight",
        messages=_render(
            "link_closure_route_consequence",
            target_id=target_id,
            routes_ko=routes_ko,
            routes_en=routes_en,
            routes_ja=routes_ja,
        ),
        details={"target_id": target_id, "affected_routes": affected},
    )


register(
    Constraint(
        id="link_closure_route_consequence",
        applies_to=list(_LINK_CLOSURE_ROUTE_CONSEQUENCE_FIELDS.keys()),
        phase="preflight",
        tier="warn",
        check=_check_link_closure_route_consequence,
        message_ko="링크/커넥터 {target_id}를 지나는 경로 {routes_ko}이(가) 있습니다. Vissim은 폐쇄되거나 "
        "금지된 링크/회전을 지나는 경로를 자동으로 우회시키지 않으며, 그 경로에 배정된 수요는 잠재 "
        "수요로 소실되거나 폐쇄 지점 앞에서 정체될 수 있습니다 — 해당 경로들도 함께 조정할지 확인하세요.",
        message_en="Route(s) {routes_en} run through link/connector {target_id}. Vissim does not "
        "automatically reroute traffic assigned to a closed or banned link/turn -- demand on "
        "those routes may vanish as latent demand or queue behind the closure. Confirm whether "
        "those routes should be adjusted too.",
        message_ja="リンク/コネクタ {target_id} を通る経路 {routes_ja} があります。Vissimは閉鎖・禁止"
        "されたリンク/右左折を通る経路を自動的に迂回させないため、その経路に割り当てられた需要は"
        "潜在需要として消失するか、閉鎖箇所の手前で滞留する可能性があります — それらの経路も併せて"
        "調整するか確認してください。",
    )
)


# ---------------------------------------------------------------------------
# add_link_lane_count_high (warn, preflight) -- Q4 unit (5)'s ONE new
# constraint (this unit's own "constraints.py minimal" instruction:
# _TOOL_TARGET_SPEC entries above + at most one small constraint here;
# everything else lives in services/vissim_com.py and
# services/agent/tools_edit.py, per those modules' own comments).
#
# Task brief: "lane_count <1 or >10 warn" -- <1 is a hard pydantic refusal
# (AddLinkParams' own Field(ge=1), never reaches here); >10 is legal (Vissim
# itself places no upper bound on a link's own lane count -- untested live
# by this unit, no evidence either way, so this is a POLICY caution, not a
# reproduced silent failure) but unusual enough on a SINGLE link to flag
# rather than execute silently, matching the guardrail doctrine's
# "allowed-with-warning" tier (00-EXECUTION-GUIDE.md SS4): the write still
# lands, the caller just gets the specific number named.
# ---------------------------------------------------------------------------

_ADD_LINK_LANE_COUNT_WARN_THRESHOLD = 10


def _check_add_link_lane_count_high(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_link":
        return None
    params = action.get("params", {})
    lane_count = _coerce_number(params.get("lane_count"), cast=int)
    if lane_count is None or lane_count <= _ADD_LINK_LANE_COUNT_WARN_THRESHOLD:
        return None
    return Finding(
        constraint_id="add_link_lane_count_high",
        tier="warn",
        phase="preflight",
        messages=_render(
            "add_link_lane_count_high", lane_count=lane_count, threshold=_ADD_LINK_LANE_COUNT_WARN_THRESHOLD
        ),
        details={"lane_count": lane_count, "threshold": _ADD_LINK_LANE_COUNT_WARN_THRESHOLD},
    )


register(
    Constraint(
        id="add_link_lane_count_high",
        applies_to=["add_link"],
        phase="preflight",
        tier="warn",
        check=_check_add_link_lane_count_high,
        message_ko="차로 수 {lane_count}개는 링크 하나치고 이례적으로 많습니다(기준 {threshold}개 초과). "
        "의도한 값인지 확인하세요.",
        message_en="{lane_count} lanes is unusually high for a single link (threshold: "
        "{threshold}). Confirm this is intentional.",
        message_ja="車線数{lane_count}本は1つのリンクとしては異例に多いです(基準{threshold}本超)。"
        "意図した値か確認してください。",
    )
)


# ---------------------------------------------------------------------------
# pt_line_stop_not_found (refuse, preflight) -- Q4 unit (7)'s ONE new
# constraint (this unit's own "constraints.py minimal" instruction:
# _TOOL_TARGET_SPEC entries above + at most one small constraint here;
# everything else lives in services/vissim_com.py and
# services/agent/tools_edit.py, per those modules' own comments).
#
# set_pt_line_stop_dwell has NO _TOOL_TARGET_SPEC entry -- its target is the
# TWO-part (line_no, stop_no) pair, which that generic single-id machinery
# cannot express (mirrors set_route_relative_flow's/set_des_speed_decision's
# own split); so object_not_found (#1) never runs for this tool at all. This
# check covers the WHOLE preflight-existence requirement in one pass: does
# the (line_no, stop_no) pair genuinely name a real ptLineStop
# (shadow_model["pt_lines"], tools_network._parse_pt_lines), AND does
# time_distribution_no genuinely name a real time distribution
# (shadow_model["time_distributions"] -- Q3 unit 5's own machinery,
# explicitly reused per this unit's task brief, not re-derived) -- ANY of
# the three failing refuses with a clear reason and a candidate list,
# mirroring des_speed_decision_entry_not_found's/des_speed_distribution_not_
# found's own two-check split, combined into ONE constraint id here per this
# unit's own constraints.py budget.
# ---------------------------------------------------------------------------


def _check_pt_line_stop_not_found(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_pt_line_stop_dwell":
        return None

    params = action.get("params", {})
    line_no = str(params.get("line_no", ""))
    stop_no = str(params.get("stop_no", ""))

    pt_lines = shadow_model.get("pt_lines", [])
    line = _find_by_field(pt_lines, "no", line_no)
    if line is None:
        known_line_nos = [str(pl.get("no", "")) for pl in pt_lines]
        candidates = difflib.get_close_matches(line_no, known_line_nos, n=5) or sorted(known_line_nos)[:20]
        return Finding(
            constraint_id="pt_line_stop_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "pt_line_stop_not_found",
                reason_ko=f"PT 노선 '{line_no}'가 이 네트워크에 존재하지 않습니다",
                reason_en=f"PT line '{line_no}' does not exist in this network",
                reason_ja=f"PT路線 '{line_no}' はこのネットワークに存在しません",
                candidates=", ".join(candidates) if candidates else "-",
            ),
            suggested_fix={"candidates": candidates},
            details={"line_no": line_no, "stop_no": stop_no, "available_lines": known_line_nos},
        )

    stop_nos = [str(s.get("pt_stop_no", "")) for s in line.get("stops", [])]
    if stop_no not in stop_nos:
        available_text = ", ".join(stop_nos) if stop_nos else "-"
        return Finding(
            constraint_id="pt_line_stop_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "pt_line_stop_not_found",
                reason_ko=(
                    f"PT 노선 '{line_no}'에는 정류장 '{stop_no}'에 대한 배정이 없습니다 "
                    f"(배정된 정류장: {available_text})"
                ),
                reason_en=(
                    f"PT line '{line_no}' has no stop assignment for '{stop_no}' "
                    f"(assigned stops: {available_text})"
                ),
                reason_ja=(
                    f"PT路線 '{line_no}' には停留所 '{stop_no}' の割り当てがありません"
                    f"(割り当て済み停留所: {available_text})"
                ),
                candidates=available_text,
            ),
            suggested_fix={"candidates": stop_nos},
            details={"line_no": line_no, "stop_no": stop_no, "available_stops": stop_nos},
        )

    distributions = shadow_model.get("time_distributions", [])
    distr_no = str(params.get("time_distribution_no", ""))
    known_distr_nos = [str(d.get("no", "")) for d in distributions]
    if distr_no not in known_distr_nos:
        candidates = difflib.get_close_matches(distr_no, known_distr_nos, n=5) or sorted(known_distr_nos)[:20]
        return Finding(
            constraint_id="pt_line_stop_not_found",
            tier="refuse",
            phase="preflight",
            messages=_render(
                "pt_line_stop_not_found",
                reason_ko=f"시간분포 번호 '{distr_no}'가 이 네트워크에 존재하지 않습니다",
                reason_en=f"time distribution '{distr_no}' does not exist in this network",
                reason_ja=f"時間分布番号 '{distr_no}' はこのネットワークに存在しません",
                candidates=", ".join(candidates) if candidates else "-",
            ),
            suggested_fix={"candidates": candidates},
            details={
                "line_no": line_no,
                "stop_no": stop_no,
                "distr_no": distr_no,
                "available_distributions": known_distr_nos,
            },
        )
    return None


register(
    Constraint(
        id="pt_line_stop_not_found",
        applies_to=["set_pt_line_stop_dwell"],
        phase="preflight",
        tier="refuse",
        check=_check_pt_line_stop_not_found,
        message_ko="PT 노선/정류장/시간분포 대상을 확인할 수 없습니다: {reason_ko}. 후보: {candidates}.",
        message_en=(
            "Could not resolve the PT line/stop/time-distribution target: {reason_en}. "
            "Candidates: {candidates}."
        ),
        message_ja="PT路線/停留所/時間分布の対象を確認できません: {reason_ja}。候補: {candidates}。",
    )
)


# ---------------------------------------------------------------------------
# add_detector_length_deviates_from_mrwa_default (warn, preflight) --
# Phase 0.3 (MRWA constraint/defaults pack). add_detector's own
# ``length_m: float = _MRWA_DETECTOR_LENGTH_M`` (services/agent/
# tools_edit.py) already makes 4.5 m the silent default when a caller omits
# the field -- this check makes an EXPLICIT deviation from that default
# visible with its citation, per this unit's own task brief ("warn when
# placement deviates materially from 2.0 m/4.5 m -- the verb already cites
# these as defaults -- this makes deviation visible").
#
# Reads ``params.get("length_m")`` directly (never a pydantic-resolved
# default): ``action["params"]`` here is the orchestrator's pre-dispatch
# decision.params (services/agent/orchestrator.py, ``action = {"tool":
# decision.tool, "params": decision.params}``), built and preflight-checked
# BEFORE AddDetectorParams' own pydantic defaulting ever runs -- so a caller
# who genuinely omitted length_m reads back as absent here (``None``), not
# as the resolved 4.5, exactly mirroring priority_rule_gap_headway_invalid's
# own established "raw is None -> no claim" reading of this same action
# shape. Only an EXPLICIT, different length_m ever fires this.
#
# No tolerance band beyond ordinary float noise: 4.5 m is a precise Appendix
# A default (mrwa_reference.DETECTOR_LENGTH_M), not a fuzzy plausibility
# range like priority_rule_gap_headway_implausible's -- real detector
# lengths legitimately range 1-6 m in the wild (see _MRWA_DETECTOR_LENGTH_M's
# own comment, backend/tools/probe_signal_heads_detectors.py's static scan),
# so this check does not claim those values are WRONG, only that they
# deviate from the cited MRWA default and should be documented -- warn tier,
# never refuse.
# ---------------------------------------------------------------------------

_DETECTOR_LENGTH_DEFAULT_M = mrwa_reference.DETECTOR_LENGTH_M.value
_DETECTOR_FLOAT_TOLERANCE_M = 1e-6


def _check_add_detector_length_deviates_from_mrwa_default(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_detector":
        return None

    params = action.get("params", {})
    length_m = _coerce_number(params.get("length_m"))
    if length_m is None or not math.isfinite(length_m):
        return None
    if abs(length_m - _DETECTOR_LENGTH_DEFAULT_M) <= _DETECTOR_FLOAT_TOLERANCE_M:
        return None

    return Finding(
        constraint_id="add_detector_length_deviates_from_mrwa_default",
        tier="warn",
        phase="preflight",
        messages=_render(
            "add_detector_length_deviates_from_mrwa_default",
            length_m=length_m,
            default_m=_DETECTOR_LENGTH_DEFAULT_M,
        ),
        suggested_fix={"mrwa_default_length_m": _DETECTOR_LENGTH_DEFAULT_M},
        details={"length_m": length_m, "default_m": _DETECTOR_LENGTH_DEFAULT_M},
    )


register(
    Constraint(
        id="add_detector_length_deviates_from_mrwa_default",
        applies_to=["add_detector"],
        phase="preflight",
        tier="warn",
        check=_check_add_detector_length_deviates_from_mrwa_default,
        message_ko="검지기 길이 {length_m}m는 MRWA Appendix A 기본값 {default_m}m와 다릅니다(신호교차로 "
        "접근로 검지기 기준). 값 자체가 틀린 것은 아니지만(실제 검지기 길이는 1-6m 범위에서 흔히 다양함), "
        "MRWA 기준과의 편차이므로 근거를 기록해 주세요.",
        message_en="Detector length {length_m}m differs from the MRWA Appendix A default "
        "{default_m}m (for a signalized-intersection approach detector). The value is not "
        "necessarily wrong — real detector lengths commonly range 1-6m — but it is a deviation "
        "from the cited MRWA default, so please document the reason.",
        message_ja="検知器の長さ{length_m}mはMRWA附録A規定値{default_m}mと異なります(信号交差点"
        "アプローチの検知器基準)。値自体が誤りとは限りません(実際の検知器長は1-6mの範囲で様々です)が、"
        "MRWA規定値からの逸脱のため、理由を記録してください。",
    )
)


# ---------------------------------------------------------------------------
# add_detector_setback_deviates_from_mrwa_default (warn, preflight) --
# Phase 0.3. Companion to add_detector_length_deviates_from_mrwa_default
# just above, same task-brief line item, same MRWA Appendix A source
# (mrwa_reference.DETECTOR_SETBACK_M), but a DIFFERENT param (pos_m vs
# length_m) and a different derivation shape, hence a separate constraint id
# rather than one combined check (mirrors this catalog's general preference
# for per-field ids when the fields have genuinely different preconditions
# -- e.g. the several des_speed_distribution_points_* checks -- over forcing
# unrelated preconditions into one Finding).
#
# add_detector's own default-pos logic (services/agent/tools_edit.py,
# add_detector) ONLY computes a setback when pos_m is omitted entirely, by
# finding the stop-line-proxy signal head on the SAME (link_no, lane_index)
# and subtracting the MRWA 2.0 m default -- meaning an omitted pos_m can
# NEVER deviate (it IS the default, by construction) and is silently
# skipped here. This check only has something to say when the caller gives
# pos_m EXPLICITLY: it looks for that same stop-line-proxy signal head (the
# head furthest along the lane, i.e. largest pos -- mirroring add_detector's
# own "largest pos wins" tie-break exactly) on shadow_model["signal_heads"],
# computes the ACTUAL setback (head.pos - pos_m), and warns if that deviates
# from 2.0 m.
#
# When no signal head exists on that (link_no, lane_index) at all, there is
# no MRWA-default baseline to compare against (this is precisely
# add_detector's own "no stop-line proxy -- pos_m must be given explicitly"
# case, a normal, common situation for a mid-link detector with nothing to
# do with a stop line) -- this check stays silent rather than inventing a
# comparison, per this unit's own "if not cheaply derivable, skip and record
# why" doctrine (task brief item 3's same doctrine, applied here to item 4).
# ---------------------------------------------------------------------------

_DETECTOR_SETBACK_DEFAULT_M = mrwa_reference.DETECTOR_SETBACK_M.value


def _check_add_detector_setback_deviates_from_mrwa_default(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_detector":
        return None

    params = action.get("params", {})
    pos_m = _coerce_number(params.get("pos_m"))
    if pos_m is None or not math.isfinite(pos_m):
        return None  # omitted pos_m IS the MRWA default, by construction -- nothing to compare

    link_no = str(params.get("link_no", ""))
    lane_index = params.get("lane_index")
    candidate_heads = [
        head
        for head in shadow_model.get("signal_heads", [])
        if str(head.get("link", "")) == link_no and head.get("lane_index") == lane_index
    ]
    if not candidate_heads:
        return None  # no stop-line proxy available -- no baseline, skip (documented above)

    stop_line_head = max(candidate_heads, key=lambda head: head.get("pos") or 0.0)
    head_pos = _coerce_number(stop_line_head.get("pos"))
    if head_pos is None:
        return None

    actual_setback = head_pos - pos_m
    if abs(actual_setback - _DETECTOR_SETBACK_DEFAULT_M) <= _DETECTOR_FLOAT_TOLERANCE_M:
        return None

    return Finding(
        constraint_id="add_detector_setback_deviates_from_mrwa_default",
        tier="warn",
        phase="preflight",
        messages=_render(
            "add_detector_setback_deviates_from_mrwa_default",
            actual_setback=round(actual_setback, 3),
            default_m=_DETECTOR_SETBACK_DEFAULT_M,
            head_no=stop_line_head.get("no", "?"),
        ),
        suggested_fix={"mrwa_default_setback_m": _DETECTOR_SETBACK_DEFAULT_M},
        details={
            "actual_setback_m": actual_setback,
            "default_m": _DETECTOR_SETBACK_DEFAULT_M,
            "stop_line_head_no": stop_line_head.get("no", "?"),
        },
    )


register(
    Constraint(
        id="add_detector_setback_deviates_from_mrwa_default",
        applies_to=["add_detector"],
        phase="preflight",
        tier="warn",
        check=_check_add_detector_setback_deviates_from_mrwa_default,
        message_ko="검지기가 신호 헤드 '{head_no}'로부터 {actual_setback}m 후방에 배치되어, MRWA "
        "Appendix A 기본값 {default_m}m와 다릅니다. 값 자체가 틀린 것은 아니지만 MRWA 기준과의 "
        "편차이므로 근거를 기록해 주세요.",
        message_en="This detector sits {actual_setback}m back from signal head '{head_no}', "
        "differing from the MRWA Appendix A default {default_m}m. The value is not necessarily "
        "wrong, but it is a deviation from the cited MRWA default, so please document the reason.",
        message_ja="この検知器は信号ヘッド '{head_no}' から{actual_setback}m後方に配置されており、"
        "MRWA附録A規定値{default_m}mと異なります。値自体が誤りとは限りませんが、MRWA規定値からの"
        "逸脱のため、理由を記録してください。",
    )
)


# ---------------------------------------------------------------------------
# signal_controller_duplicate_source_type_unsupported (refuse, preflight) --
# Q5 unit (2). The FIRST of this unit's two constraints, and the one that
# keeps add_signal_controller inside the guardrail doctrine's
# "verifiability" axis (00-EXECUTION-GUIDE.md §4).
#
# add_signal_controller creates a new controller by DUPLICATING an existing
# one, so the copy is only as knowable as its donor. Constraint #4
# (signal_controller_type_unsupported, pre-existing) already refuses every
# cycle/offset/green-split EDIT on a controller outside
# _SUPPORTED_CONTROLLER_TYPES; this is the CREATE-side mirror of the same
# population, and it is deliberately a separate constraint rather than one
# more entry on #4's applies_to list, because the param it reads is
# different (``source_sc_no``, the DONOR) and the remedy it offers is
# different (pick another donor, not "give up on this edit").
#
# Measured grounds (backend/tools/probe_signal_controller_duplicate.py
# Part D, 2026-08-03, on Signalization RBC Boise.US -- 49 RBC controllers):
#   - all 49 carry their program in an EXTERNAL file
#     (``supplyFile1='#data#<no>.prbc'``) and NONE carries an in-file
#     ``intSupplyData`` program at all;
#   - build_shadow_model yields ZERO ``signal_program_splits`` rows for the
#     whole network, versus 44 for Beijing's single FIXEDTIME controller --
#     so nothing this codebase can read would ever verify what a copy
#     inherited;
#   - constraint #4 then refuses every timing edit on the copy, so it would
#     also be permanently uneditable by this agent.
# NOT claimed (the COM half is blocked on this machine -- Boise fails to
# load, an RBC licensing block already recorded in DECISIONS.md 2026-07-31,
# and this unit re-confirmed it is NOT the scenario-management flag): that
# Vissim itself refuses such a duplicate, or that the copy would literally
# share the donor's external file. Both are plausible; neither was observed;
# the refusal rests on neither.
# ---------------------------------------------------------------------------


def _fixedtime_donor_candidates(shadow_model: dict[str, Any]) -> list[str]:
    """Controller ids in this network whose type IS duplicable -- the
    "nearest allowed alternative" the guardrail doctrine requires a refusal
    to carry. Bounded by _CANDIDATE_CAP, like every other candidate list."""
    candidates: list[str] = []
    for signal in shadow_model.get("signals", []):
        if signal.get("controller_type") in _SUPPORTED_CONTROLLER_TYPES:
            name = str(signal.get("sc_name") or "").strip()
            sc_no = str(signal.get("sc_no", ""))
            candidates.append(f"{sc_no} ({name})" if name else sc_no)
        if len(candidates) >= _CANDIDATE_CAP:
            break
    return candidates


def _check_signal_controller_duplicate_source_type_unsupported(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_signal_controller":
        return None

    source_sc_no = str(action.get("params", {}).get("source_sc_no", ""))
    donor = _find_by_field(shadow_model.get("signals", []), "sc_no", source_sc_no)
    if donor is None:
        return None  # missing donor is object_not_found's job (#1)

    controller_type = donor.get("controller_type")
    if controller_type in _SUPPORTED_CONTROLLER_TYPES:
        return None

    displayed_type = controller_type if controller_type else "unknown"
    candidates = _fixedtime_donor_candidates(shadow_model)
    if candidates:
        candidates_ko, candidates_en, candidates_ja = _capped_join(
            candidates, "개", "candidates", "件"
        )
    else:
        candidates_ko = candidates_en = candidates_ja = "-"

    return Finding(
        constraint_id="signal_controller_duplicate_source_type_unsupported",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "signal_controller_duplicate_source_type_unsupported",
            sc_no=source_sc_no,
            controller_type=displayed_type,
            candidates_ko=candidates_ko,
            candidates_en=candidates_en,
            candidates_ja=candidates_ja,
        ),
        suggested_fix={"duplicable_donors": candidates},
        details={
            "source_sc_no": source_sc_no,
            "controller_type": displayed_type,
            "duplicable_donors": candidates,
        },
    )


register(
    Constraint(
        id="signal_controller_duplicate_source_type_unsupported",
        applies_to=["add_signal_controller"],
        phase="preflight",
        tier="refuse",
        check=_check_signal_controller_duplicate_source_type_unsupported,
        message_ko="복제 원본 신호제어기 '{sc_no}'의 타입({controller_type})은 복제할 수 없습니다. "
        "이 타입은 신호 프로그램이 .inpx 밖의 외부 파일에 있어, 복사본이 무엇을 물려받았는지 "
        "확인할 수도 없고 이후 수정할 수도 없습니다. 고정시간(fixed-time) 제어기를 원본으로 "
        "선택하세요. 후보: {candidates_ko}.",
        message_en="Source signal controller '{sc_no}' has controller type "
        "'{controller_type}', which cannot be duplicated: this type keeps its signal program in "
        "an external file outside the .inpx, so what the copy inherited could neither be "
        "verified nor edited afterwards. Duplicate a fixed-time controller instead. "
        "Candidates: {candidates_en}.",
        message_ja="複製元の信号制御機 '{sc_no}' のタイプ('{controller_type}')は複製できません。"
        "このタイプは信号プログラムを.inpx外部のファイルに保持するため、コピーが何を継承したかを"
        "検証することも、その後編集することもできません。固定時間式(fixed-time)制御機を元に"
        "選んでください。候補: {candidates_ja}。",
    )
)


# ---------------------------------------------------------------------------
# signal_controller_duplicate_inherits_donor_program (warn, preflight) --
# Q5 unit (2)'s second constraint, and the one this unit's brief specifically
# sanctioned ("if a duplicate-then-edit verb needs a warn naming what the
# copy inherits from the donor, that is the kind that earns its place").
#
# The silent lie this exists for: a caller asks for "a new signal controller
# for the north junction", gets one, wires heads to it, runs the model, and
# reads confident delay numbers produced by a timing plan that is actually a
# verbatim copy of a DIFFERENT intersection's. Nothing in Vissim complains;
# the copy is a perfectly valid controller. Live-measured on Beijing: one
# duplicate carries 2 programs x 22 signal groups = 44 green splits, every
# one identical to the donor's (backend/tools/probe_signal_controller_
# duplicate.py [A.3]/[A.9]).
#
# Warn, not refuse: duplicating IS the correct and only way to create a
# controller here, and inheriting a working program is exactly why it is
# correct -- the copy is a legitimate starting point, not an error. It just
# must never be mistaken for a blank one. Tier 2 of the doctrine
# ("allowed-with-warning: legal but suspicious ... execute AND attach the
# specific constraint warning") verbatim.
#
# Silent when the donor is missing (#1's job) or of an unduplicable type
# (the refuse check above owns that case -- firing both would bury the
# refusal in noise).
# ---------------------------------------------------------------------------


def _donor_program_shape(shadow_model: dict[str, Any], sc_no: str) -> tuple[int, int, int]:
    """(program count, signal-group count, total green-split rows) the copy
    will inherit, read from the FLAT signal_program_splits list -- the same
    rows guardrails.py's postwrite diff counts as "added" for this verb."""
    rows = [
        row
        for row in shadow_model.get("signal_program_splits", [])
        if str(row.get("sc_no", "")) == sc_no
    ]
    programs = {str(row.get("program_no", "")) for row in rows}
    signal_groups = {str(row.get("sg_no", "")) for row in rows}
    return len(programs), len(signal_groups), len(rows)


def _check_signal_controller_duplicate_inherits_donor_program(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_signal_controller":
        return None

    source_sc_no = str(action.get("params", {}).get("source_sc_no", ""))
    donor = _find_by_field(shadow_model.get("signals", []), "sc_no", source_sc_no)
    if donor is None:
        return None  # missing donor is object_not_found's job (#1)
    if donor.get("controller_type") not in _SUPPORTED_CONTROLLER_TYPES:
        return None  # the refuse check above owns this case

    program_count, sg_count, split_count = _donor_program_shape(shadow_model, source_sc_no)
    if split_count:
        shape_ko = f"프로그램 {program_count}개 x 신호그룹 {sg_count}개 = 녹색시간 {split_count}건"
        shape_en = (
            f"{program_count} program(s) x {sg_count} signal group(s) = "
            f"{split_count} green split(s)"
        )
        shape_ja = f"プログラム{program_count}個 x 信号グループ{sg_count}個 = 青時間{split_count}件"
    else:
        # A supported-type donor whose splits this model does not expose --
        # say so rather than rendering a confident "0 x 0".
        shape_ko = "이 모델에서 녹색시간 정보를 읽을 수 없는 프로그램"
        shape_en = "a program whose green splits this model does not expose"
        shape_ja = "このモデルでは青時間情報を読み取れないプログラム"

    return Finding(
        constraint_id="signal_controller_duplicate_inherits_donor_program",
        tier="warn",
        phase="preflight",
        messages=_render(
            "signal_controller_duplicate_inherits_donor_program",
            sc_no=source_sc_no,
            shape_ko=shape_ko,
            shape_en=shape_en,
            shape_ja=shape_ja,
        ),
        suggested_fix={
            "next_steps": [
                "set_signal_green_split on the NEW controller number",
                "set_signal_offset on the NEW controller number (coordination)",
                "add_signal_head(sc_no=<new number>, sg_no=...) to wire geometry to it",
            ],
        },
        details={
            "source_sc_no": source_sc_no,
            "inherited_program_count": program_count,
            "inherited_signal_group_count": sg_count,
            "inherited_green_split_count": split_count,
        },
    )


register(
    Constraint(
        id="signal_controller_duplicate_inherits_donor_program",
        applies_to=["add_signal_controller"],
        phase="preflight",
        tier="warn",
        check=_check_signal_controller_duplicate_inherits_donor_program,
        message_ko="새 신호제어기는 원본 '{sc_no}'의 신호 프로그램을 그대로 물려받습니다 "
        "({shape_ko}). 빈 제어기가 아니라 원본과 똑같은 신호시간으로 동작하므로, 새 번호에 대해 "
        "set_signal_green_split(필요시 set_signal_offset)으로 실제 의도한 시간계획으로 바꾸세요. "
        "또한 add_signal_head로 신호등을 연결하기 전까지는 아무것도 제어하지 않습니다.",
        message_en="The new signal controller inherits source '{sc_no}'s entire signal program "
        "({shape_en}). It is not a blank controller -- it will run the SOURCE's timings until you "
        "change them with set_signal_green_split (and set_signal_offset for coordination) on the "
        "new controller number, and it controls nothing at all until signal heads are wired to it "
        "with add_signal_head.",
        message_ja="新しい信号制御機は元の '{sc_no}' の信号プログラムをそのまま継承します"
        "({shape_ja})。空の制御機ではなく元と同じ信号時間で動作するため、新しい番号に対して"
        "set_signal_green_split(必要に応じてset_signal_offset)で意図した時間計画に変更して"
        "ください。またadd_signal_headで信号灯を接続するまでは何も制御しません。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_input_on_connector (warn, preflight) -- Q5 unit (3). The FIRST of
# this unit's two constraints.
#
# **This shipped as a WARN because the probe overturned the refusal it was
# expected to be.** The plan was "Vissim's own UI does not offer vehicle
# inputs on connectors, so COM must be refusing or silently breaking them --
# refuse". Both halves were wrong, and the measurement says so
# (backend/tools/probe_vehicle_input_create.py Parts B/D/E/F, 2026-08-03):
# COM ACCEPTS AddVehicleInput(key, <connector Link object>), the saved file
# reloads clean (load_ok/init_ok, empty .err), and the input genuinely
# GENERATES TRAFFIC -- the same 13 vehicles over the same window as the
# identical input on a plain link, against a NULL CONTROL (same network, all
# 12 real inputs zeroed, no new input) that produced exactly 0. Refusing it
# would have refused something Vissim actually permits and that actually
# works, which the guardrail doctrine forbids on taste alone
# (00-EXECUTION-GUIDE.md SS4).
#
# What IS real, and is what this warn says: demand placed on a connector
# enters MID-JUNCTION. Beijing's connector 10000 runs link 1 -> link 2 from
# pos 335.02 m, so an input on it skips the whole 335 m approach: at t=30 s
# the plain-link arm still had 8 of its 13 vehicles ON the approach while the
# connector arm had 12 of 13 already piled onto the NEXT connector. Nothing
# queues on the approach, nothing metering that approach applies, and any
# report of the approach's own flow or queue simply cannot see this demand --
# a confident wrong number with no error attached, i.e. tier 2 of the
# doctrine ("legal but suspicious ... execute AND attach the specific
# constraint warning") verbatim.
#
# The nearest allowed alternative is concrete and free: a connector's own
# from_link is already in the shadow model, so the message can name the
# approach the caller probably meant.
# ---------------------------------------------------------------------------


def _check_vehicle_input_on_connector(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_vehicle_input":
        return None

    link_id = str(action.get("params", {}).get("link_no", ""))
    link = _find_by_field(shadow_model.get("links", []), "id", link_id)
    if link is None:
        return None  # missing link is object_not_found's job (#1)
    if not link.get("is_connector"):
        return None

    upstream = str(link.get("from_link") or "")
    if upstream:
        alt_ko = f"대신 상류 접근링크 '{upstream}'에 배치하세요"
        alt_en = f"Place it on the upstream approach link '{upstream}' instead"
        alt_ja = f"代わりに上流の流入リンク '{upstream}' に配置してください"
    else:
        # from_link absent on this entry -- say so rather than inventing an
        # alternative the caller cannot act on.
        alt_ko = "이 커넥터의 상류 링크를 모델에서 읽을 수 없어 대안을 제시하지 못했습니다"
        alt_en = (
            "This connector's own upstream link could not be read from the model, so no "
            "specific alternative is named"
        )
        alt_ja = "このコネクタの上流リンクをモデルから読み取れないため、代替は提示できません"

    return Finding(
        constraint_id="vehicle_input_on_connector",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_input_on_connector",
            link_id=link_id,
            upstream=upstream or "-",
            alt_ko=alt_ko,
            alt_en=alt_en,
            alt_ja=alt_ja,
        ),
        suggested_fix={"upstream_link": upstream or None},
        details={
            "link_id": link_id,
            "is_connector": True,
            "upstream_link": upstream or None,
            "downstream_link": str(link.get("to_link") or "") or None,
            "from_pos": link.get("from_pos"),
        },
    )


register(
    Constraint(
        id="vehicle_input_on_connector",
        applies_to=["add_vehicle_input"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_input_on_connector,
        message_ko="'{link_id}'은(는) 일반 링크가 아니라 커넥터입니다. Vissim은 커넥터 위의 차량 "
        "입력을 허용하고 실제로 교통량도 생성하지만(실측 확인), 수요가 교차로 한가운데에서 "
        "발생하므로 상류 접근로 전체를 건너뜁니다. 그 결과 접근로에는 대기행렬이 생기지 않고, "
        "접근로에 적용되는 신호·우선순위 제어도 이 교통량에는 적용되지 않으며, 접근로 기준 "
        "교통량·대기행렬 보고서에도 이 수요가 나타나지 않습니다. {alt_ko}.",
        message_en="'{link_id}' is a connector, not a plain link. Vissim does accept a vehicle "
        "input on a connector and it does generate traffic (measured live), but the demand then "
        "enters mid-junction and bypasses the whole upstream approach: nothing queues on that "
        "approach, signal or priority control placed on that approach does not apply to this "
        "traffic, and any report of "
        "the approach's own flow or queue will not see this demand at all. {alt_en}.",
        message_ja="'{link_id}' は通常のリンクではなくコネクタです。Vissimはコネクタ上の車両"
        "インプットを受け付け、実際に交通も生成されます(実測確認済み)が、需要が交差点の途中から"
        "発生するため上流の流入部全体を迂回します。その結果、流入部には待ち行列ができず、流入部に"
        "適用される信号・優先制御もこの交通には効かず、流入部基準の交通量・待ち行列レポートにも"
        "この需要は現れません。{alt_ja}。",
    )
)


# ---------------------------------------------------------------------------
# vehicle_input_created_covers_first_interval_only (warn, preflight) -- Q5
# unit (3)'s second constraint.
#
# The silent lie this exists for, live-measured: add_vehicle_input writes
# Volume(1) and nothing else, but the NUMBER of time intervals a fresh input
# carries is the NETWORK's property, not the verb's. On Toll Plaza Camden.US
# (5 real inputs x 5 interval rows each) a freshly created input came back
# with 5 rows and volumes ['450', '0', '0', '0', '0']; on Air Taxi Vertiport,
# whose own time-interval set has 12 intervals starting at 0 ms / 1000 ms /
# 190000 ms / ..., the first-ever input carried 12 rows, so a requested
# 450 veh/h actually applies for ONE SECOND and is 0 for the rest of the run.
# Nothing in Vissim complains; the model runs and reports confident,
# nearly-empty numbers.
# (backend/tools/probe_vehicle_input_create.py Parts C/G, 2026-08-03.)
#
# **Stated CATALOG-GAP: this check can only fire when the network ALREADY has
# a vehicle input to read an interval count off.** The interval count lives
# in the .inpx's own <timeIntervalSet>, which build_shadow_model does not
# parse (adding that parse is a tools_network change, deliberately out of
# this unit's scope) -- so on a from-nothing network with zero inputs, which
# is exactly the genesis case, preflight is silent. That gap is covered, not
# ignored: tools_edit.add_vehicle_input reparses the persisted file after its
# own write and reports the REAL interval count and how many intervals were
# left at zero in its result message, which is always accurate because it is
# a readback rather than a prediction.
#
# Warn, not refuse: filling interval 1 only is the correct behaviour for a
# creation verb (there is no sane way to guess the other intervals' volumes),
# and set_vehicle_input_volume(interval_no=...) is the real follow-up. This
# is the creation-side sibling of #13 (vehicle_input_multi_interval_write_
# gap), which says the same thing about the SETTER; kept as its own
# constraint because #13 keys off an EXISTING input_no this verb does not
# have yet.
# ---------------------------------------------------------------------------


def _network_vehicle_input_interval_count(shadow_model: dict[str, Any]) -> int | None:
    """The interval count every vehicle input in this network agrees on, or
    ``None`` when the model has no input to read one off (the stated gap) or
    when its inputs disagree -- in which case no single claim is honest and
    this check stays silent rather than picking one."""
    counts: set[int] = set()
    for vehicle_input in shadow_model.get("vehicle_inputs", []):
        count = _coerce_number(vehicle_input.get("interval_count"), int)
        if count is not None:
            counts.add(count)
    if len(counts) != 1:
        return None
    return counts.pop()


def _check_vehicle_input_created_covers_first_interval_only(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "add_vehicle_input":
        return None

    interval_count = _network_vehicle_input_interval_count(shadow_model)
    if interval_count is None or interval_count <= 1:
        return None

    volume = action.get("params", {}).get("volume_vph")
    return Finding(
        constraint_id="vehicle_input_created_covers_first_interval_only",
        tier="warn",
        phase="preflight",
        messages=_render(
            "vehicle_input_created_covers_first_interval_only",
            interval_count=interval_count,
            unset_count=interval_count - 1,
            volume=volume,
        ),
        suggested_fix={
            "next_steps": [
                "set_vehicle_input_volume(input_no=<new number>, interval_no=i) for i in "
                f"2..{interval_count}",
            ],
        },
        details={
            "network_interval_count": interval_count,
            "intervals_left_at_zero": interval_count - 1,
            "volume": volume,
        },
    )


register(
    Constraint(
        id="vehicle_input_created_covers_first_interval_only",
        applies_to=["add_vehicle_input"],
        phase="preflight",
        tier="warn",
        check=_check_vehicle_input_created_covers_first_interval_only,
        message_ko="이 네트워크의 수요 모델은 시간 구간이 {interval_count}개입니다. 새 차량 입력에 "
        "지정한 {volume} veh/h는 구간 1에만 적용되고 나머지 {unset_count}개 구간은 0 veh/h로 "
        "남습니다. 즉 구간 1의 시간대가 끝나면 이 진입 교통량은 조용히 사라집니다. 새 번호에 대해 "
        "set_vehicle_input_volume(interval_no=...)으로 나머지 구간도 채우세요.",
        message_en="This network's demand model has {interval_count} time intervals. The "
        "{volume} veh/h you give the new vehicle input applies to interval 1 only -- the other "
        "{unset_count} stay at 0 veh/h, so this demand silently stops once interval 1's window "
        "ends. Fill the rest with set_vehicle_input_volume(interval_no=...) on the new number.",
        message_ja="このネットワークの需要モデルには時間区間が{interval_count}個あります。新しい"
        "車両インプットに指定した {volume} veh/h は区間1にのみ適用され、残りの{unset_count}区間は"
        "0 veh/hのままです。つまり区間1の時間帯が終わると、この流入交通量は静かに消えます。新しい"
        "番号に対してset_vehicle_input_volume(interval_no=...)で残りの区間も埋めてください。",
    )
)


# ---------------------------------------------------------------------------
# Q6 unit (1): link geometry edits (lanes / length / curvature). THREE new
# constraints, each from a measurement in backend/tools/probe_link_geometry.py
# rather than from taste -- one refuse and two warns.
#
# What is deliberately NOT here, and why: the reference-integrity refusal that
# stops a lane-count reduction from destroying lane-anchored objects lives in
# tools_edit.set_link_lane_count (services.inpx_structure's
# ReferenceIntegrityError, naming every referrer), following
# remove_link/remove_connector/remove_pt_stop's own precedent exactly -- a
# refusal whose evidence is "here are the 4 objects that would silently
# vanish" has no useful ko/en/ja TEMPLATE form, and duplicating the traversal
# here would give this codebase two competing answers to one question.
# ---------------------------------------------------------------------------

#: Every shadow-model list whose entries anchor themselves at a POSITION
#: along a link, as ``(object_class, link_field)``. Deliberately mirrors --
#: rather than imports -- ``tools_edit._POSITION_ANCHOR_SITES``, which asks
#: the same question of the raw XML: the two run at different layers
#: (preflight against the shadow model; the tool against the working copy it
#: is about to write), and this file has kept every such table independent
#: since ``_TOOL_TARGET_SPEC``/``_WRITE_TARGET_FIELD_SPEC`` were deliberately
#: not shared with guardrails.py.
_LINK_POSITION_ANCHOR_CLASSES: tuple[tuple[str, str], ...] = (
    ("signal_heads", "link"),
    ("detectors", "link"),
    ("data_collection_points", "link"),
    ("stop_signs", "link"),
    ("pt_stops", "link"),
    ("queue_counters", "link_id"),
)


def _link_position_anchors(shadow_model: dict[str, Any], link_id: str) -> list[dict[str, Any]]:
    """Every object anchored at a position along ``link_id``, from the shadow
    model: the lane/link-anchored measurement and control objects, plus every
    CONNECTOR endpoint attached to the link (a connector is itself a "links"
    entry carrying from_link/from_pos/to_link/to_pos -- inpx_parser's own
    pre-existing fields, so this needs no parser change)."""
    anchors: list[dict[str, Any]] = []
    for object_class, link_field in _LINK_POSITION_ANCHOR_CLASSES:
        for obj in shadow_model.get(object_class, []) or []:
            if str(obj.get(link_field, "")) != str(link_id):
                continue
            pos = _coerce_number(obj.get("pos"))
            if pos is None:
                continue
            anchors.append({"kind": object_class, "no": str(obj.get("no", "")), "pos": float(pos)})
    for link in shadow_model.get("links", []) or []:
        if not link.get("is_connector"):
            continue
        for side in ("from", "to"):
            if str(link.get(f"{side}_link", "")) != str(link_id):
                continue
            pos = _coerce_number(link.get(f"{side}_pos"))
            if pos is None:
                continue
            anchors.append(
                {"kind": f"connector_{side}", "no": str(link.get("id", "")), "pos": float(pos)}
            )
    return anchors


def _polyline_length_m(points: Any) -> float | None:
    """Total 2D length of a ``[[x, y], ...]``-shaped point list, or ``None``
    when the shape is not that -- a hallucinated param must make this check
    silent, never crash a preflight pass."""
    try:
        coords = [(float(p[0]), float(p[1])) for p in points]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if len(coords) < 2:
        return None
    return sum(
        math.hypot(coords[i][0] - coords[i - 1][0], coords[i][1] - coords[i - 1][1])
        for i in range(1, len(coords))
    )


def _check_link_geometry_truncates_anchored_objects(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_link_geometry":
        return None
    params = action.get("params", {})
    link_id = str(params.get("link_id", ""))
    new_length = _polyline_length_m(params.get("points_xy"))
    if not link_id or new_length is None:
        return None

    truncated = [
        anchor
        for anchor in _link_position_anchors(shadow_model, link_id)
        if anchor["pos"] > new_length + 1e-6
    ]
    if not truncated:
        return None

    worst = max(anchor["pos"] for anchor in truncated)
    labels = [
        f"{a['kind']} {a['no']} @ {a['pos']:.1f}m"
        for a in sorted(truncated, key=lambda a: -a["pos"])
    ]
    objects_ko, objects_en, objects_ja = _capped_join(labels, "개", "objects", "件")
    return Finding(
        constraint_id="link_geometry_truncates_anchored_objects",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "link_geometry_truncates_anchored_objects",
            link_id=link_id,
            new_length=f"{new_length:.1f}",
            min_length=f"{worst:.1f}",
            count=len(truncated),
            objects_ko=objects_ko,
            objects_en=objects_en,
            objects_ja=objects_ja,
        ),
        suggested_fix={
            "min_length_m": round(worst, 3),
            "requested_length_m": round(new_length, 3),
            "objects": truncated[:10],
            "next_steps": [
                f"keep link {link_id} at least {worst:.1f} m long",
                "or move/remove the listed objects first",
            ],
        },
        details={
            "link_id": link_id,
            "requested_length_m": round(new_length, 3),
            "min_length_m": round(worst, 3),
            "truncated_count": len(truncated),
        },
    )


register(
    Constraint(
        id="link_geometry_truncates_anchored_objects",
        applies_to=["set_link_geometry"],
        phase="preflight",
        tier="refuse",
        check=_check_link_geometry_truncates_anchored_objects,
        # The flagship silent failure of this unit, measured twice live: a
        # data-collection point at pos 132.485 m on a link shortened to
        # 82.803 m came back at EXACTLY the new end and persisted there, and
        # shortening Urban Intersection Beijing.CN link 5 from 452.06 m to
        # 226.03 m clamped all three connectors anchored at its far end AND
        # stretched their own geometry to reach -- an 11.71 m turn silently
        # became a 237.70 m one. load_check passes, conflict areas stay at
        # 224, nothing in Vissim says a word.
        message_ko="링크 {link_id}을(를) {new_length} m로 줄이면 그보다 뒤쪽에 배치된 객체 "
        "{count}개({objects_ko})가 조용히 링크 끝으로 끌려옵니다. Vissim은 이 편집을 거부하지 "
        "않고 각 객체의 위치를 새 끝점으로 바꿔버리며, 이 링크에 붙은 커넥터는 끝점까지 늘어나 "
        "짧은 회전 커넥터가 수백 m짜리가 됩니다(실측). 링크 길이를 최소 {min_length} m 이상으로 "
        "유지하거나, 해당 객체들을 먼저 옮기거나 삭제하세요.",
        message_en="Shortening link {link_id} to {new_length} m would silently drag {count} "
        "object(s) anchored beyond that point ({objects_en}) to the link's new end. Vissim does "
        "not refuse this -- it rewrites each object's position to the new end, and stretches "
        "connectors attached to the link so they still reach, turning a short turn connector "
        "into a hundreds-of-metres one (measured). Keep the link at least {min_length} m long, "
        "or move/remove those objects first.",
        message_ja="リンク {link_id} を {new_length} m に短縮すると、その先に配置されたオブジェクト"
        "{count}件({objects_ja})が静かにリンクの新しい終点へ移動させられます。Vissimはこの編集を"
        "拒否せず、各オブジェクトの位置を新しい終点に書き換え、リンクに接続されたコネクタは"
        "終点まで引き伸ばされて短い右左折コネクタが数百mになります(実測)。リンク長を最低"
        "{min_length} m以上に保つか、対象オブジェクトを先に移動・削除してください。",
    )
)


def _check_link_lane_count_shifts_centerline(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_link_lane_count":
        return None
    params = action.get("params", {})
    link_id = str(params.get("link_id", ""))
    requested = _coerce_number(params.get("lane_count"), cast=int)
    if not link_id or requested is None:
        return None

    link = _find_by_field(shadow_model.get("links", []) or [], "id", link_id)
    if link is None:
        return None
    current = _coerce_number(link.get("lanes"), cast=int)
    if current is None or current == requested:
        return None

    widths = [w for w in (link.get("lane_widths") or []) if _coerce_number(w) is not None]
    width = _coerce_number(params.get("lane_width_m"))
    if width is None:
        width = float(widths[-1]) if widths else 3.5
    shift = abs(requested - current) * float(width) / 2.0
    return Finding(
        constraint_id="link_lane_count_shifts_centerline",
        tier="warn",
        phase="preflight",
        messages=_render(
            "link_lane_count_shifts_centerline",
            link_id=link_id,
            current=current,
            requested=requested,
            shift=f"{shift:.2f}",
        ),
        details={
            "link_id": link_id,
            "current_lane_count": current,
            "requested_lane_count": requested,
            "expected_centerline_shift_m": round(shift, 3),
            "lane_width_m": round(float(width), 3),
        },
    )


register(
    Constraint(
        id="link_lane_count_shifts_centerline",
        applies_to=["set_link_lane_count"],
        phase="preflight",
        tier="warn",
        check=_check_link_lane_count_shifts_centerline,
        # Measured three times, on two networks, in both directions: Vissim
        # keeps a link's lanes centred on its polyline, so changing the lane
        # count re-centres the POLYLINE -- every point of Beijing link 4 moved
        # 1.75 m when a 3.5 m lane was added, every point of link 8 moved
        # 1.5 m when a 3.0 m lane was removed, and Camden link 1's whole
        # polyline translated 0.5334 m for a 1.0668 m lane. Length2D never
        # changes. A warn, not a refusal: this is Vissim's own correct
        # behaviour for a widened road and the caller may well want it -- but
        # a link that silently moves sideways can leave the kerb line it was
        # matched to, which nothing else in the pipeline would report.
        message_ko="링크 {link_id}의 차로 수를 {current}개에서 {requested}개로 바꾸면 Vissim이 "
        "링크 중심선을 다시 계산하여 링크 전체가 옆으로 약 {shift} m 이동합니다(실측: 이동량은 "
        "추가/삭제된 차로 폭의 절반). 링크 길이는 변하지 않습니다. 실측 도면에 맞춘 모델이라면 "
        "이동 후 위치가 여전히 맞는지 확인하세요.",
        message_en="Changing link {link_id} from {current} to {requested} lanes makes Vissim "
        "recompute the link's centreline: the whole link moves sideways by about {shift} m "
        "(measured -- exactly half the added/removed lane width). Its length does not change. If "
        "this model is matched to survey geometry, check the link still sits where it should.",
        message_ja="リンク {link_id} の車線数を{current}本から{requested}本に変更すると、Vissimが"
        "リンクの中心線を再計算し、リンク全体が横に約{shift} m移動します(実測: 移動量は追加/削除"
        "された車線幅の半分)。リンク長は変わりません。実測図面に合わせたモデルの場合は、移動後の"
        "位置が正しいか確認してください。",
    )
)


def _geometric_radius_m(shadow_model: dict[str, Any], link_id: str, point_index: Any) -> float | None:
    """The circumradius of the three consecutive polyline points centred on
    ``point_index`` -- i.e. the radius the DRAWN shape actually has, which is
    what Vissim itself computes when ``radiusSrc`` is CALCULATED. ``None``
    when it cannot be computed (link/point missing, or the three points are
    collinear, where the true radius is infinite rather than any number worth
    printing). Used only to put the caller's override next to reality in the
    warning text -- never to refuse."""
    if point_index is None:
        return None
    link = _find_by_field(shadow_model.get("links", []) or [], "id", str(link_id))
    if link is None:
        return None
    points = link.get("points") or []
    index = int(point_index)
    if not 2 <= index <= len(points) - 1:
        return None
    try:
        ax, ay = float(points[index - 2][0]), float(points[index - 2][1])
        bx, by = float(points[index - 1][0]), float(points[index - 1][1])
        cx, cy = float(points[index][0]), float(points[index][1])
    except (TypeError, ValueError, IndexError):
        return None
    area2 = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    if area2 < 1e-9:
        return None
    side_a = math.hypot(bx - ax, by - ay)
    side_b = math.hypot(cx - bx, cy - by)
    side_c = math.hypot(cx - ax, cy - ay)
    return (side_a * side_b * side_c) / (2.0 * area2)


def _check_link_point_radius_overrides_geometry(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    if action.get("tool") != "set_link_point_radius":
        return None
    params = action.get("params", {})
    radius = _coerce_number(params.get("radius_m"))
    if radius is None:
        # radius_m omitted == RESTORE Vissim's own computed radius. That is
        # the direction that REMOVES a divergence, so it warns about nothing.
        return None
    link_id = str(params.get("link_id", ""))
    point_index = _coerce_number(params.get("point_index"), cast=int)
    geometric = _geometric_radius_m(shadow_model, link_id, point_index)
    return Finding(
        constraint_id="link_point_radius_overrides_geometry",
        tier="warn",
        phase="preflight",
        messages=_render(
            "link_point_radius_overrides_geometry",
            link_id=link_id,
            point_index=point_index,
            radius=f"{float(radius):g}",
            geometric=("-" if geometric is None else f"{geometric:.1f}"),
        ),
        suggested_fix={
            "next_steps": [
                "omit radius_m to hand the radius back to Vissim's own polyline computation",
                f"or reshape the link with set_link_geometry(link_id='{link_id}', ...) so the "
                "drawn curve really has that radius",
            ]
        },
        details={
            "link_id": link_id,
            "point_index": point_index,
            "radius_m": float(radius),
            "geometric_radius_m": None if geometric is None else round(geometric, 3),
        },
    )


register(
    Constraint(
        id="link_point_radius_overrides_geometry",
        applies_to=["set_link_point_radius"],
        phase="preflight",
        tier="warn",
        check=_check_link_point_radius_overrides_geometry,
        # Measured: Vissim REFUSES a Rad write outright while radiusSrc is
        # CALCULATED ("Attribute Radius cannot be changed"), and accepts it
        # only after the source is switched to USERINPUT -- at which point the
        # radius the curve-speed model reads is whatever the caller said, and
        # the drawn shape no longer constrains it (x/y and Length2D are
        # unchanged by the write, also measured). 16 082 of the 60 420
        # polyline points across the 25 real 2026 example networks are already
        # USERINPUT, so this is a first-class Vissim feature, not an abuse --
        # hence a warn that names both numbers rather than a refusal.
        message_ko="링크 {link_id}의 점 {point_index}의 곡선 반경을 {radius} m로 직접 지정하면 "
        "Vissim이 폴리라인에서 계산하던 값(현재 형상 기준 약 {geometric} m) 대신 이 값을 "
        "사용합니다. 링크 형상 자체는 바뀌지 않으므로, 그려진 모양과 곡선 감속 모델이 보는 반경이 "
        "달라집니다. 형상을 실제로 바꾸려면 set_link_geometry를, 계산값으로 되돌리려면 radius_m을 "
        "생략하세요.",
        message_en="Overriding the curve radius at link {link_id} point {point_index} to "
        "{radius} m makes Vissim use that number instead of the one it computes from the "
        "polyline (about {geometric} m for the shape as drawn). The link's shape itself does not "
        "change, so the drawn curve and the radius the curve-speed model reads now disagree. Use "
        "set_link_geometry to change the real shape, or omit radius_m to restore the computed "
        "value.",
        message_ja="リンク {link_id} の点 {point_index} の曲線半径を {radius} m に直接指定すると、"
        "Vissimはポリラインから計算した値(現在の形状では約{geometric} m)ではなくこの値を使用します。"
        "リンク形状自体は変わらないため、描かれた曲線と曲線速度モデルが読む半径が食い違います。"
        "実際の形状を変えるにはset_link_geometryを、計算値に戻すにはradius_mを省略してください。",
    )
)


# ---------------------------------------------------------------------------
# Q6 unit (2): parking lots. THREE constraints, each from a measurement in
# backend/tools/probe_parking_lot.py, none from taste.
#
# parking_lot_capacity_unrealistic (refuse, preflight)
#
# Vissim's own Capacity attribute is READ-ONLY and DERIVED as
# floor(Length / LenPerSpc) (Part I, measured against an untouched control lot
# in the same session: SetAttValue("Capacity", 37) fails loudly with
# "Attribute Capacity cannot be changed", while Length 6->60 takes capacity
# 1->10 and LenPerSpc 6->4 then takes it 10->15). Two consequences make an
# up-front bounds screen necessary rather than decorative:
#
#   * Length = 0 is SILENTLY ACCEPTED -- capacity 0, LenPerSpc silently forced
#     to 1.0, persisted as length="0" lenPerSpc="1" capacity="0", and
#     load_check STILL returns load_ok/init_ok. A zero-space parking lot that
#     Vissim never complains about is precisely the confident-wrong-number
#     shape this layer exists to refuse; the pydantic ge=1 on both verbs stops
#     the literal zero, and this check stops the values that are non-zero but
#     still cannot be honestly realised.
#   * The ceiling is not a taste call either: the whole 629-lot corpus tops
#     out at capacity 10000 (the ZONECONNECTOR "no limit" sentinel on Motorway
#     Dyn Assign Mainz.DE), and 572 of the 573 REALPARKINGSPACE lots on
#     3D - Complex Intersection Karlsruhe.DE carry capacity 1.
#     _PARKING_LOT_CAPACITY_MAX is set at that observed sentinel: anything
#     above it has no precedent anywhere in real Vissim data AND would demand
#     a length no real link could carry.
# ---------------------------------------------------------------------------

#: Highest space count observed anywhere in the real Vissim 2026 example
#: corpus (Motorway Dyn Assign Mainz.DE's ZONECONNECTOR "no limit" sentinel);
#: see this section's own comment for why the bound is measured, not chosen.
_PARKING_LOT_CAPACITY_MAX = 10000

#: Tools whose ``capacity`` param these two screens apply to.
_PARKING_CAPACITY_TOOLS = ("add_parking_lot", "set_parking_lot_capacity")


def _check_parking_lot_capacity_unrealistic(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    del shadow_model, extra
    tool = action.get("tool", "")
    if tool not in _PARKING_CAPACITY_TOOLS:
        return None
    raw = action.get("params", {}).get("capacity")
    try:
        capacity = int(raw)
    except (TypeError, ValueError):
        return None  # a non-integer is the params model's job, not ours
    if 1 <= capacity <= _PARKING_LOT_CAPACITY_MAX:
        return None
    return Finding(
        constraint_id="parking_lot_capacity_unrealistic",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "parking_lot_capacity_unrealistic",
            capacity=capacity,
            maximum=_PARKING_LOT_CAPACITY_MAX,
        ),
        suggested_fix={"min_capacity": 1, "max_capacity": _PARKING_LOT_CAPACITY_MAX},
        details={"tool": tool, "capacity": capacity, "max_capacity": _PARKING_LOT_CAPACITY_MAX},
    )


register(
    Constraint(
        id="parking_lot_capacity_unrealistic",
        applies_to=list(_PARKING_CAPACITY_TOOLS),
        phase="preflight",
        tier="refuse",
        check=_check_parking_lot_capacity_unrealistic,
        message_ko="주차장 용량 {capacity}면은 이 모델이 허용하는 범위(1~{maximum}면)를 "
        "벗어납니다. Vissim의 Capacity 속성은 직접 쓸 수 없고 길이÷면당길이로 계산되기 때문에, "
        "요청한 면수는 주차장 길이로 환산되어 기록됩니다. 특히 길이가 0이 되는 값은 Vissim이 "
        "아무 경고 없이 받아들여 용량 0인 주차장을 만들고, 네트워크는 정상 로드되므로 잘못된 "
        "결과를 그대로 보고하게 됩니다. 1~{maximum} 사이의 면수를 지정하세요.",
        message_en="A parking capacity of {capacity} spaces is outside the range this model "
        "accepts (1-{maximum}). Vissim's Capacity attribute cannot be written directly -- it is "
        "derived as length / length-per-space -- so the requested count is realised as the lot's "
        "LENGTH. A value that lands on zero length is accepted silently by Vissim, producing a "
        "zero-space lot on a network that still loads and runs, which then reports confident "
        "wrong numbers. Ask for a count between 1 and {maximum}.",
        message_ja="駐車場容量 {capacity} 台はこのモデルが受け付ける範囲(1〜{maximum}台)を"
        "外れています。VissimのCapacity属性は直接書き込めず、長さ÷1台あたり長さから算出される"
        "ため、要求された台数は駐車場の長さとして記録されます。長さが0になる値はVissimが警告"
        "なしに受け入れ、容量0の駐車場ができてもネットワークは正常に読み込まれるため、誤った"
        "結果がそのまま報告されます。1〜{maximum} の範囲で指定してください。",
    )
)


# ---------------------------------------------------------------------------
# parking_lot_does_not_fit_link (refuse, preflight)
#
# Vissim's OWN check on this is loud and good -- writing a Length past the
# link's end raised "AttValue failed: Parking Lot 2 does not fit on link 1
# (335.122 m). Please reduce its length or move it." (Part I, writing 3x link
# 1's real 335.12213607578946 m Length2D). This constraint exists anyway, for
# two reasons the doctrine cares about:
#
#   1. It arrives as a PREFLIGHT refusal with ko/en/ja text and a numeric
#      suggested_fix (the largest capacity that fits), instead of a raw COM
#      exception surfacing as tool_execution_error -- the same "existence is
#      generic, appropriateness is dedicated" reasoning behind
#      signal_controller_duplicate_source_type_unsupported.
#   2. Our rule is deliberately CONSERVATIVE and says so: refuse when
#      ``pos + capacity x len_per_space`` exceeds the link's own length. The
#      probe did not establish whether Vissim measures from the lot's own pos
#      or merely compares the bare length, so this screen is at least as
#      strict as Vissim's. Over-refusal here is fail-safe; the alternative --
#      guessing the laxer rule and letting through a lot Vissim then clamps or
#      rejects mid-write -- is not.
#
# ``len_per_space_m`` omitted means Vissim's own default, which is 6.0 DISPLAY
# units (live-measured: 6.0 on a METERS network, 6.0 display-feet = 1.8288 m
# on Toll Plaza Camden.US). The shadow model carries no display unit, so this
# check uses the metric 6.0 -- the LARGER of the two possible real values, so
# the screen stays on the conservative side on a feet network too.
# ---------------------------------------------------------------------------

_PARKING_LOT_DEFAULT_LEN_PER_SPACE_M = 6.0


def _check_parking_lot_does_not_fit_link(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    del extra
    tool = action.get("tool", "")
    if tool not in _PARKING_CAPACITY_TOOLS:
        return None
    params = action.get("params", {})
    try:
        capacity = int(params.get("capacity"))
    except (TypeError, ValueError):
        return None
    if capacity < 1:
        return None  # parking_lot_capacity_unrealistic owns that case

    per_space = params.get("len_per_space_m")
    try:
        per_space_m = (
            float(per_space) if per_space is not None else _PARKING_LOT_DEFAULT_LEN_PER_SPACE_M
        )
    except (TypeError, ValueError):
        per_space_m = _PARKING_LOT_DEFAULT_LEN_PER_SPACE_M
    if per_space_m <= 0:
        return None

    if tool == "add_parking_lot":
        link_id = str(params.get("link_no", ""))
        try:
            pos_m = float(params.get("pos_m", 0.0))
        except (TypeError, ValueError):
            pos_m = 0.0
    else:
        lot = _find_by_field(
            shadow_model.get("parking_lots", []), "no", str(params.get("parking_lot_no", ""))
        )
        if lot is None:
            return None  # object_not_found's job (#1)
        link_id = str(lot.get("link") or "")
        try:
            pos_m = float(lot.get("pos") or 0.0)
        except (TypeError, ValueError):
            pos_m = 0.0

    link = _find_by_field(shadow_model.get("links", []), "id", link_id)
    if link is None:
        return None  # object_not_found's job (#1)
    raw_length = link.get("length")
    if raw_length is None:
        return None  # nothing to compare against; say nothing rather than guess
    link_length_m = float(raw_length)

    required_m = pos_m + capacity * per_space_m
    if required_m <= link_length_m:
        return None

    available_m = max(link_length_m - pos_m, 0.0)
    max_capacity = int(available_m // per_space_m)
    return Finding(
        constraint_id="parking_lot_does_not_fit_link",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "parking_lot_does_not_fit_link",
            capacity=capacity,
            link_id=link_id,
            required=f"{required_m:.1f}",
            link_length=f"{link_length_m:.1f}",
            pos=f"{pos_m:.1f}",
            per_space=f"{per_space_m:.1f}",
            max_capacity=max_capacity,
        ),
        suggested_fix={"max_capacity": max_capacity, "link_length_m": round(link_length_m, 3)},
        details={
            "tool": tool,
            "link_id": link_id,
            "capacity": capacity,
            "pos_m": pos_m,
            "len_per_space_m": per_space_m,
            "required_m": round(required_m, 3),
            "link_length_m": round(link_length_m, 3),
            "max_capacity": max_capacity,
        },
    )


register(
    Constraint(
        id="parking_lot_does_not_fit_link",
        applies_to=list(_PARKING_CAPACITY_TOOLS),
        phase="preflight",
        tier="refuse",
        check=_check_parking_lot_does_not_fit_link,
        message_ko="{capacity}면 주차장은 링크 {link_id}에 들어가지 않습니다. 시작 위치 {pos} m에서 "
        "면당 {per_space} m씩 필요해 총 {required} m가 필요하지만 링크 길이는 {link_length} m뿐입니다. "
        "이 위치에서 가능한 최대 면수는 {max_capacity}면입니다. 면수를 줄이거나, 면당 길이를 줄이거나, "
        "더 긴 링크에 배치하세요.",
        message_en="A {capacity}-space parking lot does not fit on link {link_id}. Starting at "
        "{pos} m and needing {per_space} m per space it would require {required} m, but the link "
        "is only {link_length} m long. The largest lot that fits at this position is "
        "{max_capacity} space(s). Reduce the capacity, reduce the metres per space, or place it "
        "on a longer link.",
        message_ja="{capacity}台の駐車場はリンク {link_id} に収まりません。開始位置 {pos} m から"
        "1台あたり {per_space} m 必要なため合計 {required} m を要しますが、リンク長は "
        "{link_length} m しかありません。この位置で収まる最大台数は {max_capacity} 台です。"
        "台数を減らすか、1台あたりの長さを縮めるか、より長いリンクに配置してください。",
    )
)


# ---------------------------------------------------------------------------
# parking_lot_inert_without_parking_routing_decision (warn, preflight)
#
# The "non-functional skeleton" question, answered honestly. A REALPARKINGSPACE
# lot is reachable ONLY through a parking routing decision (the
# vehicleRoutingDecisionParking -> vehicleRouteParking@parkLot chain); nothing
# else in the schema ever sends a vehicle to one. This toolkit ships no verb
# that creates either, so a lot added onto a network with no parking routing
# machinery changes no vehicle's behaviour at all -- while the network still
# loads, still runs, and still reports confident numbers.
#
# What the probe could and could not measure, stated rather than blurred
# (Part H): the lot has NO plain occupancy attribute (Occup/Occupancy/
# NumSpaces/ParkSpcs/ConcatLen each raise "Could not find attribute with ID"),
# only a simulation-run-indexed OccupRate() result; a 120 s RunContinuous on
# Beijing with the fresh lot completed without error. That is the NEGATIVE case
# only -- no positive control (a network WITH a parking decision, occupancy
# > 0) was run, and the vehicle count read after the run had already ENDED is
# not evidence of anything.
#
# The claim therefore rests on the CORPUS, which is unambiguous: the only
# network with REALPARKINGSPACE lots (3D - Complex Intersection Karlsruhe.DE)
# has exactly one parking route per lot -- 573 lots, 573 routes, ZERO
# unreferenced -- and the two ZONECONNECTOR networks have zero parking routes
# because they are driven by an OD matrix under dynamic assignment instead. A
# warn, not a refusal: the lot itself is real, correctly placed, and a
# perfectly reasonable first half of a two-step build.
# ---------------------------------------------------------------------------


def _check_parking_lot_inert_without_routing(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    del extra
    if action.get("tool") != "add_parking_lot":
        return None
    # The shadow model carries no parking-routing-decision class (deliberate --
    # see tools_network._parse_parking_lots' own section comment for why), so
    # this reads the closest state the model DOES expose: whether the network
    # has any parking lots at all. On a network that already has lots the
    # routing machinery is presumably already there and this warn would be
    # noise; on a from-nothing network it is exactly the case that needs
    # saying. Stated as a proxy, not as a direct observation.
    if shadow_model.get("parking_lots"):
        return None
    link_id = str(action.get("params", {}).get("link_no", ""))
    return Finding(
        constraint_id="parking_lot_inert_without_parking_routing_decision",
        tier="warn",
        phase="preflight",
        messages=_render("parking_lot_inert_without_parking_routing_decision", link_id=link_id),
        suggested_fix={"needs": "vehicleRoutingDecisionParking"},
        details={"link_id": link_id, "existing_parking_lots": 0},
    )


register(
    Constraint(
        id="parking_lot_inert_without_parking_routing_decision",
        applies_to=["add_parking_lot"],
        phase="preflight",
        tier="warn",
        check=_check_parking_lot_inert_without_routing,
        message_ko="이 네트워크에는 주차장이 하나도 없습니다. 주차장은 그 자체로 교통을 끌어들이지 "
        "않으며, 주차 경로결정(vehicleRoutingDecisionParking)이 이 주차장을 목적지로 지정해야만 "
        "차량이 들어옵니다. 이 도구모음에는 주차 경로결정을 만드는 기능이 없으므로, 링크 {link_id}에 "
        "추가되는 주차장은 지금 상태로는 시뮬레이션 결과를 전혀 바꾸지 않습니다. 네트워크는 정상 "
        "로드되고 실행되므로 오류처럼 보이지 않는다는 점에 주의하세요. Vissim GUI에서 주차 "
        "경로결정을 추가한 뒤 사용하세요.",
        message_en="This network has no parking lots at all yet. A parking lot does not attract "
        "traffic on its own -- vehicles only enter one when a parking routing decision "
        "(vehicleRoutingDecisionParking) names it as a destination, and this toolkit ships no verb "
        "that creates one. The lot added on link {link_id} will therefore not change simulation "
        "results as things stand, and nothing will look like an error: the network still loads and "
        "still runs. Add the parking routing decision in the Vissim GUI before relying on it.",
        message_ja="このネットワークにはまだ駐車場が1つもありません。駐車場それ自体は交通を"
        "引き寄せません。駐車経路決定(vehicleRoutingDecisionParking)が目的地として指定して"
        "はじめて車両が入ります。本ツール群には駐車経路決定を作成する機能がないため、リンク "
        "{link_id} に追加される駐車場は現状ではシミュレーション結果を変えません。ネットワークは"
        "正常に読み込まれ実行されるため、エラーには見えない点に注意してください。Vissim GUIで"
        "駐車経路決定を追加してから利用してください。",
    )
)


# ---------------------------------------------------------------------------
# Q6 unit (3): PEDESTRIANS (Viswalk) -- three constraints, all from measured
# behaviour. Evidence: backend/tools/probe_pedestrian.py (18 parts, null
# control, --audit re-derivation).
#
# THE LICENCE QUESTION was asked before any of this: this box CAN create and
# simulate Viswalk objects, so these are guardrails on real write verbs, not a
# licence-gap refusal. (No licence/module attribute exists on the Vissim COM
# object to interrogate -- the verdict is behavioural, measured at four
# levels. It was an ACADEMIC licence; a box whose Vissim licence lacks the
# Viswalk add-on is NOT covered and its failure mode is unmeasured.)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# pedestrian_input_origin_area_has_no_route (refuse, preflight)
#
# THE decisive finding of the unit. A pedestrian input whose origin area
# carries no pedestrian ROUTING DECISION makes the saved network fail Vissim's
# own initialisation: LoadNet succeeds, init dies with "The simulation could
# not be continued."
#
# Six probe arms isolate the cause to the AREA, one bit at a time:
#   A  existing area 1 (routed),   volume 0    -> init_ok True
#   K  existing area 1 (routed),   volume 750  -> init_ok True
#   N  a bare NEW area, no input at all        -> init_ok True
#   O  existing area 2 (UNROUTED), volume 750  -> init_ok False
#   L  a NEW area      (UNROUTED), volume 750  -> init_ok False
#   M  a NEW area      (UNROUTED), volume 0    -> init_ok False
# K vs L says the routing decision is what matters; L vs M says the demand
# VOLUME is irrelevant (zero fails identically); N says a bare created area is
# harmless on its own; O vs L says it is not about freshly-minted geometry
# either -- Beijing's area 2 SHIPPED with the network, is real connected
# crosswalk polygon, and fails exactly like a brand-new one.
#
# It must be a STATIC routing decision, and that scoping is MEASURED too.
# _routed_area_nos first shipped counting PARTIAL decisions as well (the
# permissive, i.e. dangerous, direction); probe parts Q/R refuted it as a
# controlled pair on ONE network -- Cableways, the only corpus network with
# partial decisions at all: partial-only area 142 -> init_ok False,
# STATIC-routed area 102 of the SAME network -> init_ok True.
#
# REFUSE rather than warn, deliberately: the alternative is handing the user a
# network they cannot run, and this toolkit ships no verb that creates a
# pedestrian routing decision, so there is no in-agent fix to offer -- only
# the honest nearest alternative, which is the list of areas that already
# qualify. The refusal ALSO lives inline in tools_edit.add_pedestrian_input
# (it must, so a direct dispatch that never reaches preflight is still
# guarded); this Finding is what makes it a structured, ko/en/ja, planner-
# readable refusal in the orchestrated path rather than a bare ValueError.
# ---------------------------------------------------------------------------


def _canonical_pedestrian_id(raw: Any) -> str:
    """One id spelling for the guard AND the action (commit 3316e76's
    pattern). ``tools_edit`` normalises through ``int()``; this mirrors it, so
    "0001", " 1", "+1" and "1 " all resolve to the same area as "1" rather
    than sliding past a refusal that compares raw strings -- the exact bypass
    Q6 unit (2)'s verification found in the parking-lot cascade guard.
    Non-integer input yields "" and every caller stays silent, leaving the
    complaint to check #1."""
    if raw is None:
        return ""
    try:
        return str(int(str(raw).strip()))
    except (TypeError, ValueError):
        return ""


def _check_pedestrian_input_origin_area_has_no_route(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    del extra
    if action.get("tool") != "add_pedestrian_input":
        return None
    # Canonicalise ONCE and use that spelling everywhere the routed-area set is
    # consulted (commit 3316e76's pattern / Q6 unit (2)'s defect 1): "0001",
    # " 1", "+1" and "1 " must all resolve to the same area as "1", or this
    # refusal is bypassable by whitespace.
    canonical_area_no = _canonical_pedestrian_id(action.get("params", {}).get("area_no"))
    if not canonical_area_no:
        return None

    areas = shadow_model.get("pedestrian_areas") or []
    # Check #1 (object_not_found) owns "does this area exist"; staying silent
    # on an unknown id keeps the two checks from double-reporting.
    if not any(str(area.get("no", "")) == canonical_area_no for area in areas):
        return None

    # has_routing_decision is computed by the PARSER
    # (tools_network._parse_pedestrian_areas), from the same _routed_area_nos
    # helper tools_edit.add_pedestrian_input's own inline guard uses -- one
    # derivation, so the guard and the verb cannot disagree about which areas
    # qualify.
    routed = {
        str(area.get("no", ""))
        for area in areas
        if area.get("has_routing_decision")
    }
    if canonical_area_no in routed:
        return None

    candidates = sorted(routed, key=lambda value: (len(value), value))
    candidate_text = ", ".join(candidates) if candidates else "-"
    return Finding(
        constraint_id="pedestrian_input_origin_area_has_no_route",
        tier="refuse",
        phase="preflight",
        messages=_render(
            "pedestrian_input_origin_area_has_no_route",
            area_no=canonical_area_no,
            candidates=candidate_text,
        ),
        suggested_fix={"area_no": candidates[:10]},
        details={
            "area_no": canonical_area_no,
            "areas_with_routing_decision": candidates,
        },
    )


register(
    Constraint(
        id="pedestrian_input_origin_area_has_no_route",
        applies_to=["add_pedestrian_input"],
        phase="preflight",
        tier="refuse",
        check=_check_pedestrian_input_origin_area_has_no_route,
        message_ko="보행 구역 {area_no}에는 정적(static) 보행자 경로결정이 없어 여기에 보행자 입력을 두면 "
        "네트워크가 초기화되지 않습니다(Vissim: 'The simulation could not be continued'). "
        "수요를 0으로 두어도 마찬가지이며, 파일 로드는 성공하고 실행 직전에 실패합니다. "
        "부분(partial) 경로결정만 있는 구역도 실측상 동일하게 실패합니다. "
        "이 도구모음은 보행자 경로결정을 만들 수 없습니다. 이미 경로결정이 있는 구역: "
        "{candidates}. 그중 하나를 쓰거나, Vissim GUI에서 경로결정을 먼저 추가하세요.",
        message_en="Pedestrian area {area_no} has no STATIC pedestrian routing decision, so a "
        "pedestrian input placed there stops the network initialising (Vissim: 'The simulation "
        "could not be continued'). Setting the demand to zero does not help -- the volume is "
        "irrelevant, and the file still LOADS, failing only at run start. A PARTIAL routing "
        "decision does not count either (measured). This toolkit cannot "
        "create pedestrian routing decisions. Areas that already have one: {candidates}. Use one "
        "of those, or add the routing decision in the Vissim GUI first.",
        message_ja="歩行者エリア {area_no} には静的(static)歩行者経路決定がないため、ここに歩行者インプットを"
        "置くとネットワークが初期化できなくなります(Vissim: 'The simulation could not be "
        "continued')。需要を0にしても同じで、ファイルの読み込みは成功し実行直前に失敗します。"
        "部分(partial)経路決定だけのエリアも実測上同じく失敗します。"
        "このツール群は歩行者経路決定を作成できません。すでに経路決定があるエリア: {candidates}。"
        "そのいずれかを使うか、Vissim GUIで先に経路決定を追加してください。",
    )
)


# ---------------------------------------------------------------------------
# pedestrian_input_created_covers_first_interval_only (warn, postwrite)
#
# The pedestrian twin of Q5 unit (3)'s
# vehicle_input_created_covers_first_interval_only, and measured the same way
# rather than assumed by analogy: ONE input created on the 14-interval Air
# Taxi Vertiport network persisted with ALL 14 intervals -- interval 1
# carrying the requested volume, intervals 2..14 all at zero (persisted delta
# {'pedestrian_inputs': 1, 'ped_volume_intervals': 14}). On 1-interval Beijing
# the same verb moves ped_volume_intervals by exactly 1, so the warn stays
# silent there.
#
# The interval STRUCTURE belongs to the network, not to the verb: Vissim
# refuses Volume(2) outright on a 1-interval network ("Object 2 not found"),
# so there is nothing this layer could have written instead. A warn, not a
# refusal -- a first-interval-only input is a legitimate partial build, it is
# just not what a planner who said "2000 ped/h" is likely to have pictured on
# a network whose demand runs in fourteen slices.
# ---------------------------------------------------------------------------


def _check_pedestrian_input_first_interval_only(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    del shadow_model
    if action.get("tool") != "add_pedestrian_input":
        return None
    artifacts = (extra or {}).get("result", {}).get("artifacts") or {}
    interval_count = int(artifacts.get("interval_count") or 0)
    unset = int(artifacts.get("intervals_left_at_zero") or 0)
    if interval_count <= 1 or unset <= 0:
        return None
    return Finding(
        constraint_id="pedestrian_input_created_covers_first_interval_only",
        tier="warn",
        phase="postwrite",
        messages=_render(
            "pedestrian_input_created_covers_first_interval_only",
            input_no=str(artifacts.get("new_pedestrian_input_no", "")),
            interval_count=interval_count,
            unset=unset,
        ),
        suggested_fix={"intervals_left_at_zero": unset},
        details={
            "pedestrian_input_no": str(artifacts.get("new_pedestrian_input_no", "")),
            "interval_count": interval_count,
            "intervals_left_at_zero": unset,
        },
    )


register(
    Constraint(
        id="pedestrian_input_created_covers_first_interval_only",
        applies_to=["add_pedestrian_input"],
        phase="postwrite",
        tier="warn",
        check=_check_pedestrian_input_first_interval_only,
        message_ko="이 네트워크의 보행 수요는 시간대 {interval_count}개로 나뉘어 있는데, 새로 만든 "
        "보행자 입력 {input_no}의 수요는 시간대 1에만 적용됩니다. 나머지 {unset}개 시간대는 "
        "0 명/시 그대로여서, 첫 시간대가 끝나면 이 지점의 보행 수요가 사라집니다. 시간대 구조는 "
        "네트워크가 정하는 것이라 이 도구가 대신 채울 수 없습니다.",
        message_en="This network's pedestrian demand is split into {interval_count} time "
        "intervals, but the new pedestrian input {input_no} only carries demand in interval 1. "
        "The other {unset} are still 0 ped/h, so pedestrian demand at this point disappears once "
        "the first interval ends. The interval structure belongs to the network, so this verb "
        "cannot fill the rest for you.",
        message_ja="このネットワークの歩行需要は {interval_count} 個の時間帯に分かれていますが、"
        "新規の歩行者インプット {input_no} の需要は時間帯1にのみ適用されます。残り {unset} 個は "
        "0 人/時のままで、最初の時間帯が終わるとこの地点の歩行需要は消えます。時間帯の構成は"
        "ネットワーク側が決めるため、この動詞では埋められません。",
    )
)


# ---------------------------------------------------------------------------
# pedestrian_area_edit_unsupported (refuse, preflight)
#
# The refuse-tier constraint that NAMES the gap, so a planner asking for an
# area edit gets a reason and an alternative instead of "unknown tool".
#
# Creating an area over COM works and is verifiable (AddArea(Key, WktPolygon),
# proven on three networks including two with zero areas). It is not shipped
# because the two things a planner would need next are unsafe or impossible:
#
# 1. REMOVAL SILENTLY CASCADE-DELETES ROUTES, WITH A GREEN ORACLE. Removing
#    Beijing's area 1 moved the persisted counts by {'areas': -1,
#    'pedestrian_inputs': -1, 'ped_volume_intervals': -1,
#    'ped_routing_decisions_static': -1, 'ped_routes_static': -4,
#    'ped_route_locations': -4} -- and load_ok/init_ok were BOTH True
#    afterwards. Vissim's own oracle is green on a network that just lost four
#    pedestrian routes. The blast radius also exceeds the direct referrer
#    count: area 1 had 4 direct referrers, but the routing decision that
#    ORIGINATES there takes its own 2 routes with it AND 2 further routes
#    elsewhere that used area 1 as a DESTINATION go too (attributed by id from
#    the persisted pair, not reasoned about -- routing decisions 3 and 4 each
#    keep only route '2' afterwards).
# 2. A CREATED AREA IS INERT. Pedestrians reach an area only through a
#    pedestrian routing decision, which this toolkit cannot create -- and an
#    input placed on such an area breaks initialisation outright (see
#    pedestrian_input_origin_area_has_no_route). So "add an area" alone hands
#    the planner an object it can only make useless or dangerous.
#
# This is the third face of a cascade this codebase has now measured three
# times: Q4 unit (3)'s conflict areas, Q6 unit (2)'s parking lots, and now
# pedestrian areas.
# ---------------------------------------------------------------------------

#: Tool names a planner might reach for that this unit deliberately does not
#: ship. Kept as data so the refusal names the right verb back to the caller.
#:
#: **DELIVERY PATH, and it is NOT preflight.** None of these names is a
#: REGISTERED tool, and ``orchestrator``'s loop only runs preflight for
#: registered gated tools -- so as a preflight constraint this would be dead
#: code, and a planner reaching for ``add_pedestrian_area`` would get the
#: generic ``unknown_tool`` envelope instead of the explanation. Measured
#: end-to-end, not assumed. The rendered message is therefore delivered by
#: :func:`explain_unsupported_tool`, which the orchestrator's
#: unrecognized-tool branch consults BEFORE falling through to
#: ``unknown_tool``. It stays a REGISTERED constraint (so its id, tiers and
#: ko/en/ja text live with every other constraint and are covered by the
#: registry-wide template tests) -- only its delivery differs.
_UNSUPPORTED_PEDESTRIAN_AREA_TOOLS = frozenset(
    {
        "add_pedestrian_area",
        "remove_pedestrian_area",
        "set_pedestrian_area_geometry",
        "add_pedestrian_routing_decision",
        "add_pedestrian_route",
    }
)


def _check_pedestrian_area_edit_unsupported(
    action: dict[str, Any], shadow_model: dict[str, Any], extra: dict[str, Any]
) -> Finding | None:
    del shadow_model, extra
    tool_name = str(action.get("tool") or "")
    if tool_name not in _UNSUPPORTED_PEDESTRIAN_AREA_TOOLS:
        return None
    return Finding(
        constraint_id="pedestrian_area_edit_unsupported",
        tier="refuse",
        phase="preflight",
        messages=_render("pedestrian_area_edit_unsupported", tool=tool_name),
        suggested_fix={
            "supported": [
                "add_pedestrian_input",
                "set_pedestrian_input_volume",
                "remove_pedestrian_input",
            ]
        },
        details={"tool": tool_name},
    )


register(
    Constraint(
        id="pedestrian_area_edit_unsupported",
        applies_to=sorted(_UNSUPPORTED_PEDESTRIAN_AREA_TOOLS),
        phase="preflight",
        tier="refuse",
        check=_check_pedestrian_area_edit_unsupported,
        message_ko="{tool}은(는) 지원하지 않습니다. 보행 구역 삭제는 Vissim에서 그 구역을 쓰는 "
        "보행자 경로결정과 경로를 조용히 함께 지워버리며, 그러고도 네트워크는 정상 로드·초기화되어 "
        "오류가 드러나지 않습니다(실측: 구역 1개 삭제로 경로결정 1개·경로 4개·경로지점 4개 소실). "
        "구역을 새로 만드는 것 자체는 가능하지만, 보행자 경로결정을 만들 수 없어 만들어도 아무 "
        "효과가 없습니다. 보행 구역과 경로는 Vissim GUI에서 편집하세요. 이 도구모음이 지원하는 "
        "보행 관련 작업은 보행자 입력의 생성·수요 변경·삭제입니다.",
        message_en="{tool} is not supported. Removing a pedestrian area SILENTLY deletes the "
        "pedestrian routing decisions and routes that use it, and the network still loads and "
        "still initialises afterwards, so nothing looks wrong (measured: removing one area took 1 "
        "routing decision, 4 routes and 4 route locations with it). Creating an area does work, "
        "but this toolkit cannot create the pedestrian routing decision that would make it "
        "useful, so a new area changes nothing. Edit pedestrian areas and routes in the Vissim "
        "GUI. What this toolkit does support for pedestrians is creating, re-sizing and removing "
        "pedestrian INPUTS.",
        message_ja="{tool} はサポートしていません。歩行者エリアを削除すると、そのエリアを使う"
        "歩行者経路決定と経路が黙って一緒に削除され、それでもネットワークは正常に読み込まれ"
        "初期化されるため異常が表に出ません(実測: エリア1個の削除で経路決定1個・経路4個・"
        "経路地点4個が消失)。エリアの新規作成自体は可能ですが、歩行者経路決定を作成できないため"
        "作っても効果がありません。歩行者エリアと経路は Vissim GUI で編集してください。"
        "このツール群が対応する歩行者関連の操作は、歩行者インプットの作成・需要変更・削除です。",
    )
)



def explain_unsupported_tool(tool_name: str, language: str = "ko") -> dict[str, Any] | None:
    """The rendered refusal for a tool name this toolkit DELIBERATELY does not
    ship, or ``None`` for any other unknown name.

    Exists because ``pedestrian_area_edit_unsupported`` cannot reach a planner
    through preflight: preflight only runs for REGISTERED gated tools, and
    these names are registered nowhere (that is the whole point of them). The
    orchestrator's unrecognized-tool branch calls this first, so a planner
    asking to edit a pedestrian area gets the measured reason and the nearest
    supported alternatives instead of a bare "Unknown tool".

    Returns ``{"constraint_id", "message", "suggested_fix"}``; ``message`` is
    in ``language`` (ko/en/ja, ko as the house default). A genuinely bogus
    name returns ``None`` so the caller still emits plain ``unknown_tool`` --
    this helper must never turn a typo into a confident explanation.
    """
    finding = _check_pedestrian_area_edit_unsupported({"tool": tool_name, "params": {}}, {}, {})
    if finding is None:
        return None
    messages = finding.messages
    return {
        "constraint_id": finding.constraint_id,
        "message": messages.get(language) or messages["ko"],
        "suggested_fix": finding.suggested_fix,
    }

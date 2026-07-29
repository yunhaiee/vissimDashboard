"""Guardrail engine: the M2.2 orchestration layer over constraints.py.

``constraints.py`` is the catalog (individual checks, ko/en/ja messages);
this module is the ENGINE that runs the right checks at the right phase and
turns the results into a single, orchestrator-friendly :class:`GuardrailReport`.
Three phases, matching docs/phase2/01-ARCHITECTURE.md §5 and
docs/phase2/02-ROADMAP.md M2.2:

- :meth:`GuardrailEngine.preflight` — before a write/run dispatch: range,
  existence, and semantic screens against the current shadow model.
- :meth:`GuardrailEngine.postwrite` — after a write dispatch returns: the
  written-vs-read-back compare (``write_verification_failed``) PLUS a
  structural "nothing else moved" diff between the before/after extended
  shadow models PLUS a target-persisted check (controller review addition):
  the written value must actually appear in the AFTER shadow model — a COM
  read-back only proves Vissim's in-memory state changed, not that the tool
  saved the network file, so a write tool that forgets ``save_net_as``
  would otherwise pass both original checks while the versioned artifact
  silently lacks the change. The target object's own edited field is
  allowed to change (diff exemption); any other difference is refusal-tier
  evidence. Per the M2.2 brief, all three reuse the
  ``write_verification_failed`` constraint id rather than minting new ones —
  the evidence rides in ``Finding.details``.
- :meth:`GuardrailEngine.postrun` — after a run completes: the post-run lie
  detectors (deleted vehicles, missing KPI tables, ...). Not wired into the
  orchestrator loop yet (that is M2.4's job); the method exists now so M2.4
  builds directly on a stable signature.

**Ablation flag.** ``GuardrailEngine(disabled=...)`` — ``None`` (the
default) reads the ``AGENT_GUARDRAILS_DISABLED`` env var (``"1"``/``"true"``,
case-insensitive → disabled). A disabled engine returns a zero-finding,
non-blocking :class:`GuardrailReport` (``disabled=True``) from every phase
without ever calling into ``constraints.py``. This is the TR-C paper's
no-validation ablation arm (docs/phase2/02-ROADMAP.md M5.3) — first-class
and tested, not debug scaffolding.

This module is intentionally stdlib-only, imports no COM anything, and
consumes/produces plain dicts and the dataclasses defined here and in
``constraints.py`` — pure logic, easy to unit test with synthetic fixtures.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.agent import constraints
from services.agent.constraints import Finding

_ENV_VAR_DISABLED = "AGENT_GUARDRAILS_DISABLED"
_TRUTHY_ENV_VALUES = frozenset({"1", "true"})

# The extended shadow model's object-list keys and their natural id field
# (docs/phase2/02-ROADMAP.md M0.3 / tools_network.py's build_shadow_model).
# Mirrors tools_network.py's own _OBJECT_TYPE_SPECS id-field choices rather
# than importing it — that dict is private and object_type-keyed (planner
# vocabulary), this one is shadow-model-key-keyed (diff vocabulary); see the
# M1.2 brief's precedent for deliberately mirroring instead of reaching into
# a sibling module's private helpers.
_SHADOW_OBJECT_ID_FIELDS: dict[str, str] = {
    "links": "id",
    "nodes": "id",
    "vehicle_inputs": "no",
    "signals": "sc_no",
    # v2 unit (4): required by BOTH postwrite checks below, not diff-only --
    # _target_not_persisted_finding looks this up directly (a KeyError
    # without it, verified), and _nothing_else_moved_diff also gains
    # conflict_areas coverage as an unavoidable consequence of the same
    # entry (same shared table both mechanisms read).
    "conflict_areas": "no",
    # v2 unit ⑥: the FLAT routes list tools_network._flatten_routes builds
    # (not "routing_decisions" — a route's own identity is the (decision,
    # route) pair, which has no natural home in a decision-keyed list; see
    # _target_id_for_spec below for how a two-param write action resolves
    # this SAME synthesized id).
    "routes": "id",
    # v2 unit ⑦: the FLAT vehicle_composition_entries list
    # tools_network._flatten_vehicle_composition_entries builds (not
    # "vehicle_compositions" — an entry's own identity is the (composition,
    # vehType) pair, mirroring "routes" above exactly, for the identical
    # reason).
    "vehicle_composition_entries": "id",
    # v2a unit ⑧: the FLAT des_speed_decision_entries list
    # tools_network._flatten_des_speed_decision_entries builds (not
    # "des_speed_decisions" -- an assignment's own identity is the
    # (decision, vehClass) pair, mirroring "routes"/"vehicle_composition_
    # entries" above exactly).
    "des_speed_decision_entries": "id",
    # v2a unit ⑨: the FLAT signal_program_splits list
    # tools_network._flatten_signal_program_splits builds (not the nested
    # "signal_programs" -- a green split's own identity is the (controller,
    # program, signal group) TRIPLE, the first three-part id in this table;
    # required by BOTH postwrite checks, same as every flat entry above --
    # _target_not_persisted_finding KeyErrors without it, and the
    # nothing-else-moved diff gains split-level coverage from the same
    # entry: an IntSupplyData blob write that accidentally moved a DIFFERENT
    # signal group's window now surfaces as diff evidence).
    "signal_program_splits": "id",
    # QUEUE 3 unit 2: the FLAT priority_rule_markers list
    # tools_network._flatten_priority_rule_markers builds (not the nested
    # "priority_rules" -- a conflict marker's own identity is the (rule,
    # marker_index) pair, mirroring "routes"/"vehicle_composition_entries"/
    # "des_speed_decision_entries" above exactly). Required by BOTH postwrite
    # checks, same as every flat entry above.
    "priority_rule_markers": "id",
    # QUEUE 3 (v2a) unit 3: des_speed_distributions itself (no flattening
    # needed -- unlike routes/composition-entries/des-speed-decision-
    # entries/signal-program-splits/priority-rule-markers, a distribution
    # already has a single-token identity, its own "no").
    "des_speed_distributions": "no",
    # QUEUE (v2a) unit: vehicle types have a genuine single-token identity,
    # their own "no" -- no flattening needed (unlike routes/composition-
    # entries/des-speed-entries/priority-rule-markers above), since a
    # vehicle type's reference fields are all flat attributes on the type
    # object itself, not a nested per-assignment child collection.
    "vehicle_types": "no",
}

# {tool: {object_class, id_param, value_field}} — which shadow-model object
# and field a write tool's own action is expected to change, so the
# nothing-else-moved diff can exempt exactly that one field. Only the M2
# write verbs that target one shadow-model object are listed; a tool absent
# here (e.g. a future sim-duration/global-setting write with no single
# object-list target) gets NO exemption -- the diff then treats ANY change
# anywhere as evidence, which is the conservative default per the guardrail
# doctrine (docs/phase2/00-EXECUTION-GUIDE.md §4). Kept independent from
# constraints.py's private _TOOL_TARGET_SPEC for the same reason as above.
_WRITE_TARGET_FIELD_SPEC: dict[str, dict[str, str]] = {
    # "none_means" fixes the confirmed blind spot (DECISIONS 2026-07-24, M5
    # batch): a parsed value of None is AMBIGUOUS — for "not_persisted"
    # fields the parser CAN see the attribute whenever the file carries it
    # (vehicle-input volume: live-verified in M2.1's smoke), so None after a
    # write means the write never reached the file → refuse. For
    # "parser_blind" fields the parser structurally cannot see the value on
    # some objects (signal cycle_time inside opaque program blobs; offset is
    # absent from the shadow signal shape entirely) → no claim can be made,
    # degrade silently.
    "set_vehicle_input_volume": {
        "object_class": "vehicle_inputs",
        "id_param": "input_no",
        "value_field": "volume",
        "none_means": "not_persisted",
        # v2 unit ⑤: interval-aware addressing. The TARGET-PERSISTED check
        # (_resolve_saved_value) redirects to the matching "volumes" list
        # entry for interval_no > 1, staying exactly "volume" (byte-
        # compatible) for interval_no absent/1. The DIFF EXEMPTION
        # (_write_target_fields) is intentionally NOT interval-conditional:
        # it always exempts {"volume", "volumes", "interval_count"} together
        # for this tool, any interval_no. Reason (real regression, caught by
        # this task's own test suite before shipping): the demo network's
        # vehicle inputs start with ZERO <timeIntervalVehVolume> elements, so
        # an interval_no==1 write there takes interval_count 0->1 and
        # volumes []->[...] in the SAME step as volume None-><written> --
        # exempting "volume" alone left interval_count/volumes as
        # false-positive "something else moved" evidence. The three fields
        # are derived/mirrored views of the same underlying XML structure
        # (interval_count == len(volumes), volume == volumes[0].volume), so
        # treating them as one exempt unit is the more correct reading, not
        # merely a workaround.
        "interval_param": "interval_no",
        "interval_list_field": "volumes",
        "interval_key_field": "interval_no",
        "interval_value_field": "volume",
        "interval_count_field": "interval_count",
    },
    "set_signal_cycle": {
        "object_class": "signals",
        "id_param": "sc_no",
        "value_field": "cycle_time",
        "none_means": "parser_blind",
    },
    "set_signal_offset": {
        "object_class": "signals",
        "id_param": "sc_no",
        "value_field": "offset",
        "none_means": "parser_blind",
    },
    # v2 unit (4): status is a plain XML attribute the parser can always see
    # whenever the conflictArea element itself exists (verified live --
    # backend/tools/conflict_area_probe.py -- no opaque-blob case like
    # signal cycle_time) -- so, like volume, a None after a write IS evidence
    # the write never reached the file.
    "set_conflict_area_status": {
        "object_class": "conflict_areas",
        "id_param": "conflict_area_no",
        "value_field": "status",
        "none_means": "not_persisted",
    },
    # v2 unit ⑥: routing-decision route relative flow. relFlow is a plain
    # XML attribute the parser can always see whenever the vehicleRouteStatic
    # element itself exists (verified live -- backend/tools/
    # routing_decision_probe.py -- no opaque-blob case like signal
    # cycle_time), so, like volume/status, a None after a write IS evidence
    # the write never reached the file. "id_param_parts" (not "id_param") --
    # see _target_id_for_spec -- because this tool's target has a TWO-part
    # identity (decision_no + route_no), unlike every other write tool here.
    # Interval-aware exactly like set_vehicle_input_volume (v2 unit ⑤): the
    # DIFF EXEMPTION always exempts {"rel_flow", "rel_flows",
    # "interval_count"} together for this tool, any interval_no, for the
    # same "derived/mirrored views of one XML structure" reasoning that
    # exemption already documents for vehicle inputs.
    "set_route_relative_flow": {
        "object_class": "routes",
        "id_param_parts": ("decision_no", "route_no"),
        "value_field": "rel_flow",
        "none_means": "not_persisted",
        "interval_param": "interval_no",
        "interval_list_field": "rel_flows",
        "interval_key_field": "interval_no",
        "interval_value_field": "rel_flow",
        "interval_count_field": "interval_count",
    },
    # v2 unit ⑦: vehicle-composition relative-flow (share weight). relFlow
    # is a plain XML attribute the parser can always see whenever the
    # vehicleCompositionRelativeFlow element itself exists (verified live --
    # backend/tools/probe_vehicle_compositions.py -- no opaque-blob case
    # like signal cycle_time), so, like status/rel_flow above, a None after
    # a write IS evidence the write never reached the file.
    # "id_param_parts" (not "id_param") -- same TWO-part identity reasoning
    # as set_route_relative_flow (composition_no + veh_type_no); see
    # tools_edit.py's module docstring. NO interval dimension at all
    # (verified live: RelFlow is scalar-only for vehicle compositions,
    # unlike route RelFlow) -- so no interval_param/interval_*_field here.
    "set_vehicle_composition_share": {
        "object_class": "vehicle_composition_entries",
        "id_param_parts": ("composition_no", "veh_type_no"),
        "value_field": "rel_flow",
        "none_means": "not_persisted",
    },
    # v2a unit ⑧: desired-speed decision per-vehicle-class distribution
    # assignment. desSpeedDistr is a plain XML attribute the parser can
    # always see whenever the vehClassDesSpeedDistribution element itself
    # exists (verified live -- backend/tools/probe_des_speed_decisions.py --
    # no opaque-blob case like signal cycle_time), so a None after a write IS
    # evidence the write never reached the file, same as status/rel_flow
    # above. "id_param_parts" -- same TWO-part identity reasoning as routes/
    # vehicle-composition entries (decision_no + veh_class_no). This entry
    # covers ONLY the PRIMARY (decision_no) target; the optional RESTORE
    # target (restore_decision_no) is a SECOND object entirely -- see
    # _PAIRED_EXTRA_TARGET_SPEC below, the generalization this tool needed
    # because _WRITE_TARGET_FIELD_SPEC itself only ever names ONE target per
    # tool.
    "set_des_speed_decision": {
        "object_class": "des_speed_decision_entries",
        "id_param_parts": ("decision_no", "veh_class_no"),
        "value_field": "des_speed_distr_no",
        "none_means": "not_persisted",
        # LIVE FINDING (backend/tools/live_check_des_speed_decision.py): the
        # flat entry's des_speed_distr_name is looked up from
        # des_speed_distr_no at PARSE time -- writing the id ALWAYS also
        # changes the name as an unavoidable side effect, so both must be
        # exempted together or a correct write false-positives as "something
        # else moved" (reproduced live on Roundabout Billings.US: '55 mph'
        # -> '10 mph Roundabout HGV'). See _write_target_fields's own
        # docstring for the general "coupled_fields" mechanism this uses.
        "coupled_fields": ("des_speed_distr_name",),
    },
    # v2a unit ⑨: fixed-time signal-program green split. The FIRST
    # THREE-part identity (controller, program, signal group -- the same SG
    # carries a different window in each program, so two parts would be
    # ambiguous); id_param_parts joins them into the SAME
    # "{sc_no}.{program_no}.{sg_no}" id tools_network.
    # _flatten_signal_program_splits synthesizes. value_field is the
    # green_window DICT ({green_start_s, green_end_s}) -- one field holding
    # both edges, compared whole by the recursive numeric-tolerant
    # _values_differ, so a write that lands only ONE edge in the saved file
    # still fails the target-persisted compare. none_means "not_persisted":
    # the parser can always see the window whenever the program data exists
    # at all (FIXEDTIME: decoded from the intSupplyData blob, verified live
    # -- backend/tools/probe_signal_programs.py proved Vissim's SaveNetAs
    # serializes the COM-written blob back out with the edit intact;
    # FIXEDTIMESIMPLE: plain endRed/endGreen attributes), and preflight's
    # signal_program_split_not_found refuses targets whose window is not
    # parseable BEFORE any write -- so a None-window entry after a write IS
    # evidence the write never reached the file.
    "set_signal_green_split": {
        "object_class": "signal_program_splits",
        "id_param_parts": ("sc_no", "program_no", "sg_no"),
        "value_field": "green_window",
        "none_means": "not_persisted",
    },
    # QUEUE 3 unit 2: priority-rule conflict-marker gap/headway. TWO-part
    # identity (priority_rule_no + marker_index -- a conflict marker has no
    # single-token identity in Vissim itself, matching the routes/
    # composition-entries/des-speed-entries precedent). value_field is the
    # gap_headway DICT ({gap_time_s, headway_m}) -- ONE field holding both
    # sub-values, mirroring set_signal_green_split's green_window shape
    # exactly (see tools_network._flatten_priority_rule_markers's own
    # docstring for why gap_time_s/headway_m are NOT also separate top-level
    # fields here). none_means "not_persisted": the parser can always see
    # both attributes whenever the conflictMarker element itself exists
    # (verified live -- backend/tools/probe_priority_rules.py -- no
    # opaque-blob case like signal cycle_time).
    "set_priority_rule_gap_headway": {
        "object_class": "priority_rule_markers",
        "id_param_parts": ("priority_rule_no", "marker_index"),
        "value_field": "gap_headway",
        "none_means": "not_persisted",
    },
    # QUEUE 3 (v2a) unit 3: desired-speed distribution curve, ATOMIC
    # full-curve replace. value_field is "points" -- a LIST of {speed_kmh,
    # cum_fraction} dicts, the FIRST list-shaped (not dict-shaped) top-level
    # value_field in this table; guardrails.py's own _values_differ already
    # recurses structurally through lists of dicts (length + element-wise
    # compare), so no new comparison machinery is needed -- the whole
    # replaced curve is compared as one atomic unit, matching "atomic
    # full-curve replace beats point surgery" exactly: a half-applied edit
    # (wrong length, or any one point off) fails the compare as a WHOLE,
    # never a false partial pass. none_means "not_persisted": the parser
    # can always see a distribution's points whenever the speedDistribution
    # element itself exists (plain XML attributes, no opaque-blob case like
    # signal cycle_time). "coupled_fields" -- "point_count"/
    # "points_truncated" are DERIVED from "points" at parse time
    # (tools_network._parse_speed_distributions), so a write that changes
    # the point COUNT always also changes those two fields as an
    # unavoidable side effect -- the SAME "exempt the whole derived group
    # together" lesson unit ⑧'s des_speed_distr_name coupling taught
    # (_write_target_fields's own docstring), applied here BEFORE ever
    # shipping rather than found live and patched after.
    "set_des_speed_distribution_points": {
        "object_class": "des_speed_distributions",
        "id_param": "distribution_no",
        "value_field": "points",
        "none_means": "not_persisted",
        "coupled_fields": ("point_count", "points_truncated"),
    },
    # QUEUE (v2a) unit: vehicle-type reference reassignment. Unlike EVERY
    # other spec entry above, this tool's target field is not a fixed name
    # -- it is chosen at CALL TIME by the action's own "field" param (one of
    # 8 reference kinds; see tools_edit.py's VEHICLE_TYPE_REFERENCE_FIELD_
    # NAMES). "value_field_param" names which params key holds that choice;
    # _spec_value_field resolves the actual shadow-model field as
    # f"{params[field]}_no" -- matching the des_speed_distr/des_speed_distr_no
    # naming convention set_des_speed_decision's own spec entry already
    # established (a reference field's flat shadow-model name is always the
    # field's own name + "_no"). The reference field is a plain XML attribute
    # the parser can always see whenever the vehicleType element itself
    # exists (verified live -- backend/tools/probe_vehicle_types.py -- no
    # opaque-blob case like signal cycle_time), so a None after a write IS
    # evidence the write never reached the file, same as every other
    # "not_persisted" entry above.
    "set_vehicle_type_reference": {
        "object_class": "vehicle_types",
        "id_param": "vehicle_type_no",
        "value_field_param": "field",
        "none_means": "not_persisted",
    },
}

# v2a unit ⑧: ``set_des_speed_decision`` is the first write tool whose action
# can touch TWO separate objects (the primary decision's assignment AND, when
# ``restore_decision_no`` is given, a SECOND decision's assignment for the
# same vehicle class) -- ``_WRITE_TARGET_FIELD_SPEC`` has no built-in
# multi-target mechanism (one exemption per tool, by design, per the M2.2
# brief this table has followed since M2.3). Rather than redesign that table
# for a single tool, this is a small, ADDITIVE lookup consulted only for
# tools present here: when ``trigger_param`` is truthy in the action's own
# params, a SECOND (object_id, field) exemption is added to the SAME
# object_class's diff exemptions (see ``_nothing_else_moved_diff`` and
# ``_target_not_persisted_finding`` below) -- without it, a legitimate paired
# write's own restore-side change would read as "something else moved" and
# refuse-and-rollback a correct edit, defeating the whole feature. Every
# OTHER tool's behavior is byte-unchanged: this dict is empty for them, so
# the diff/persisted-check machinery below falls back to exactly the single-
# target behavior it has always had.
_PAIRED_EXTRA_TARGET_SPEC: dict[str, dict[str, Any]] = {
    "set_des_speed_decision": {
        "object_class": "des_speed_decision_entries",
        "id_param_parts": ("restore_decision_no", "veh_class_no"),
        "value_field": "des_speed_distr_no",
        "none_means": "not_persisted",
        "trigger_param": "restore_decision_no",
        # Same live-verified coupling as the primary spec's own entry above
        # -- the restore target's des_speed_distr_name changes as the same
        # unavoidable parse-time side effect.
        "coupled_fields": ("des_speed_distr_name",),
    },
}

# QUEUE 3 (v2a) unit 3's own regression fix: adding "des_speed_distributions"
# to _SHADOW_OBJECT_ID_FIELDS (needed for THIS unit's own
# set_des_speed_distribution_points postwrite verification) newly exposed a
# PRE-EXISTING, sanctioned behavior of v2 unit ②'s set_reduced_speed_area --
# it legitimately injects a BRAND NEW <speedDistribution> as part of its own
# XML-injection write (services.simulation_network_builder.
# inject_reduced_speed_area), which the diff had never been able to see
# before (no shadow key tracked distributions at all pre-unit-3). Newly
# ADDED objects have no per-tool exemption mechanism today -- the
# _WRITE_TARGET_FIELD_SPEC/_PAIRED_EXTRA_TARGET_SPEC machinery above only
# ever exempts FIELD-level changes on an object present in BOTH before/after
# (an "added" id is unconditional evidence in _diff_object_list, by
# construction: there is no before-side object to attach an exempt field
# list to). Rather than design a brand-new "allowed to add" mechanism broad
# enough for arbitrary future tools, this is a small, ADDITIVE opt-in
# (mirroring the coupled_fields/trigger_param precedent already established
# twice in this file): a tool names the object_class(es) its OWN sanctioned
# action is known to add brand-new objects into, and every id ADDED to that
# class during THIS tool's write is treated as expected, never evidence.
# Every OTHER tool's behavior is byte-unchanged: this dict is empty for
# them, so "added" stays unconditional evidence exactly as before.
_ALLOWS_NEW_OBJECTS_IN: dict[str, frozenset[str]] = {
    "set_reduced_speed_area": frozenset({"des_speed_distributions"}),
}

# QUEUE 3 (v2a) unit 3's SECOND regression fix, found live-testing this same
# addition against a REAL parsed (not hand-built) shadow model:
# "des_speed_distributions"' own "referenced_by"/"reference_count" fields
# (tools_network._parse_speed_distributions) are PURELY DERIVED from OTHER
# object lists (des_speed_decisions, vehicle_compositions, a static XML scan)
# -- no tool ever writes them directly, and no tool's own
# _WRITE_TARGET_FIELD_SPEC entry could ever name them as its "intended
# target" (coupled_fields exempts a field only WITHIN the writing tool's own
# target object_class -- but here the field that shifts lives on a
# DIFFERENT object_class than the one the tool actually wrote to: e.g.
# set_des_speed_decision reassigning decision 6's vehClass=10 from
# distribution 50 to 25 legitimately changes BOTH distributions' OWN
# reference_count/referenced_by, even though the tool's target object_class
# is "des_speed_decision_entries", not "des_speed_distributions" at all).
# Reproduced live: a genuine, correct set_des_speed_decision write against a
# real two-decision two-distribution network false-positived as "something
# else moved" on BOTH distributions before this fix. Since these two fields
# can NEVER be a tool's own intended write target for ANY tool (there is no
# verb that sets them), the correct fix is a BLANKET, tool-agnostic
# exemption for this object_class -- not a per-tool coupled_fields entry,
# which would need to enumerate every current and future tool capable of
# moving a desSpeedDistr/vehClassDesSpeedDistr/... reference around.
_ALWAYS_EXEMPT_FIELDS: dict[str, frozenset[str]] = {
    "des_speed_distributions": frozenset({"referenced_by", "reference_count"}),
    # QUEUE (v2a) unit: "references" (per-field resolved id+name+stats) and
    # "referencing_composition_nos" on vehicle_types are BOTH purely derived
    # -- the former from the flat "{field}_no" fields plus each target
    # collection's own data, the latter from vehicle_composition_entries'
    # own veh_type membership. Neither has a write path of its own (no tool
    # sets them directly), matching the des_speed_distributions precedent
    # above exactly -- see tools_network._parse_vehicle_types' own
    # docstring.
    "vehicle_types": frozenset({"references", "referencing_composition_nos"}),
}


def _spec_value_field(spec: dict[str, Any], action: dict[str, Any]) -> str:
    """The top-level shadow-model field NAME this write's value lives in.

    Every spec names it as a fixed ``"value_field"`` string EXCEPT
    ``set_vehicle_type_reference`` (QUEUE (v2a) unit), whose target field is
    chosen at CALL TIME by the action's own ``"field"`` param (one of 8
    reference kinds) -- ``"value_field_param"`` names which params key holds
    that choice, and the shadow model's own flat field is that value + "_no"
    (matching the ``des_speed_distr``/``des_speed_distr_no`` naming
    convention ``set_des_speed_decision``'s spec entry already established).
    Every OTHER spec lacks ``"value_field_param"``, so this always falls
    through to the existing fixed-string behavior for them, unchanged.
    """
    value_field_param = spec.get("value_field_param")
    if value_field_param is None:
        return spec["value_field"]
    chosen = action.get("params", {}).get(value_field_param, "")
    return f"{chosen}_no"


def _target_id_for_spec(spec: dict[str, str], action: dict[str, Any]) -> str:
    """The write action's target object id in the shadow model, per its
    ``_WRITE_TARGET_FIELD_SPEC`` entry.

    Every tool but ``set_route_relative_flow`` supplies a single
    ``id_param`` naming ONE of ``action["params"]``'s own keys, read
    directly. ``set_route_relative_flow``'s target has a TWO-part identity
    (decision_no + route_no) with no single param to read, so its spec
    supplies ``id_param_parts`` instead -- joined with ``"."`` into the SAME
    synthesized ``"{decision_no}.{route_no}"`` string
    ``tools_network._flatten_routes`` already uses as the flat
    ``shadow_model["routes"]`` list's own ``id`` field, so the two sides
    (this lookup, and that list's own keys) always agree.
    """
    params = action.get("params", {})
    id_param = spec.get("id_param")
    if id_param is not None:
        return str(params.get(id_param, ""))
    id_param_parts = spec.get("id_param_parts")
    if id_param_parts:
        return ".".join(str(params.get(part, "")) for part in id_param_parts)
    return ""


def _env_says_disabled() -> bool:
    raw = os.environ.get(_ENV_VAR_DISABLED, "")
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


def _blocking(findings: list[Finding]) -> bool:
    return any(finding.tier == "refuse" for finding in findings)


@dataclass
class GuardrailReport:
    """One phase's outcome: every finding, whether it blocks, and whether
    the engine was disabled (ablation) when it ran.

    ``blocking`` is true iff at least one finding is refuse-tier — the
    orchestrator's single signal for "stop, do not proceed."
    """

    phase: str
    findings: list[Finding]
    blocking: bool
    disabled: bool

    def refusals(self) -> list[Finding]:
        """Every refuse-tier finding, in ``findings`` order."""
        return [finding for finding in self.findings if finding.tier == "refuse"]

    def first_refusal(self) -> Finding | None:
        """The first refuse-tier finding, in ``findings`` order, or ``None``.

        ``blocking`` is True iff this returns non-``None``. Callers building
        a refusal envelope (the orchestrator) use this finding's
        ``constraint_id`` as ``error_type`` and its ``suggested_fix``.
        """
        refusals = self.refusals()
        return refusals[0] if refusals else None

    def warnings(self) -> list[Finding]:
        """Every warn-tier finding, in ``findings`` order."""
        return [finding for finding in self.findings if finding.tier == "warn"]


def _empty_report(phase: str) -> GuardrailReport:
    return GuardrailReport(phase=phase, findings=[], blocking=False, disabled=True)


class GuardrailEngine:
    """Runs the right ``constraints.py`` checks for each phase and wraps the
    result as a :class:`GuardrailReport`.

    ``disabled``: ``None`` (default) reads :data:`AGENT_GUARDRAILS_DISABLED
    <_ENV_VAR_DISABLED>` fresh at construction time (not cached at import
    time, so tests can toggle the env var per-test); an explicit ``True``/
    ``False`` always wins over the env var.
    """

    def __init__(self, disabled: bool | None = None) -> None:
        self.disabled = _env_says_disabled() if disabled is None else bool(disabled)

    def preflight(
        self, action: dict[str, Any], shadow_model: dict[str, Any] | None
    ) -> GuardrailReport:
        """Preflight checks against the current shadow model, before dispatch."""
        if self.disabled:
            return _empty_report("preflight")
        findings = list(
            constraints.run_checks("preflight", action, shadow_model or {}, {})
        )
        return GuardrailReport(
            phase="preflight", findings=findings, blocking=_blocking(findings), disabled=False
        )

    def postwrite(
        self,
        action: dict[str, Any],
        before_model: dict[str, Any] | None,
        after_model: dict[str, Any] | None,
        com_result: dict[str, Any] | None,
    ) -> GuardrailReport:
        """Post-write verification: written-vs-read-back compare + a
        structural "nothing else moved" diff between ``before_model`` and
        ``after_model``.

        ``com_result`` is the write tool's ``verification`` payload (i.e.
        ``envelope["verification"]`` from its ``toolkit.dispatch`` result,
        NOT the whole 7-key envelope) — expected to carry ``written_value``/
        ``readback_value`` when the tool supports read-back verification. A
        tool that omits either key gets no ``write_verification_failed``
        protection from the value-compare half of this check (the diff half
        still runs regardless).
        """
        if self.disabled:
            return _empty_report("postwrite")

        payload = com_result if isinstance(com_result, dict) else {}
        extra: dict[str, Any] = {}
        if "written_value" in payload:
            extra["written_value"] = payload["written_value"]
        if "readback_value" in payload:
            extra["readback_value"] = payload["readback_value"]

        findings = list(
            constraints.run_checks("postwrite", action, after_model or {}, extra)
        )

        diff_evidence = _nothing_else_moved_diff(action, before_model or {}, after_model or {})
        if diff_evidence:
            findings.append(_diff_finding(diff_evidence))

        persist_finding = _target_not_persisted_finding(action, after_model or {}, payload)
        if persist_finding is not None:
            findings.append(persist_finding)

        return GuardrailReport(
            phase="postwrite", findings=findings, blocking=_blocking(findings), disabled=False
        )

    def postrun(self, action: dict[str, Any], run_result: dict[str, Any] | None) -> GuardrailReport:
        """Post-run lie detectors (deleted vehicles, missing KPI tables, ...).

        Not wired into the orchestrator loop by M2.2 (M2.4's job — the run
        gate + post-run detectors milestone); the method is complete and
        tested now so M2.4 can call it directly.
        """
        if self.disabled:
            return _empty_report("postrun")
        findings = list(
            constraints.run_checks("postrun", action, {}, {"run_result": run_result})
        )
        return GuardrailReport(
            phase="postrun", findings=findings, blocking=_blocking(findings), disabled=False
        )


# ---------------------------------------------------------------------------
# "Nothing else moved" structural diff
# ---------------------------------------------------------------------------


def _index_by_id(objects: list[dict[str, Any]], id_field: str) -> dict[str, dict[str, Any]]:
    return {str(obj.get(id_field, "")): obj for obj in objects}


def _diff_object_list(
    object_class: str,
    id_field: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    exempt_targets: list[tuple[str, frozenset[str]]] | None,
    *,
    suppress_added: bool = False,
    always_exempt_fields: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Structural diff of one shadow-model object list, id-keyed.

    ``exempt_targets`` is a list of ``(object_id, field_names)`` pairs —
    every object (by id) whose listed field NAMES are allowed to differ
    without becoming evidence. Almost every write tool contributes exactly
    ONE entry here (the write action's own intended target — usually one
    field, but v2 unit ⑤'s ``set_vehicle_input_volume`` exempts a small
    coupled set, see ``_write_target_fields``); v2a unit ⑧'s
    ``set_des_speed_decision`` is the first tool that can contribute TWO,
    when its optional paired restore write is present (see
    ``_PAIRED_EXTRA_TARGET_SPEC``) — TWO DIFFERENT objects in the SAME
    object_class, each with its own exempt field set, so a single
    ``(id, fields)`` tuple could no longer represent every tool's needs.
    Every other addition, removal, or field-level change becomes one
    evidence entry.

    ``suppress_added`` (QUEUE 3 (v2a) unit 3, see ``_ALLOWS_NEW_OBJECTS_IN``)
    — when true, a brand-new id present only in ``after`` is expected
    (the calling tool's own sanctioned action legitimately creates one),
    not evidence. Removals are NEVER suppressed by this flag — an
    unexpectedly vanished object stays evidence regardless.

    ``always_exempt_fields`` (QUEUE 3 (v2a) unit 3's SECOND regression fix,
    see ``_ALWAYS_EXEMPT_FIELDS``) — field names that never become evidence
    for ANY object in this object_class, regardless of WHICH tool wrote or
    which object is the tool's own named target. Unlike ``exempt_targets``
    (tool-specific, target-specific) this is for fields that are PURELY
    DERIVED from other object lists and have no write path of their own at
    all (e.g. a distribution's own reference count, which shifts whenever
    ANY OTHER tool reassigns something to point at a different
    distribution) — no per-tool spec could enumerate every tool capable of
    moving such a reference, so the field itself is exempt everywhere.
    """
    before_by_id = _index_by_id(before, id_field)
    after_by_id = _index_by_id(after, id_field)
    evidence: list[dict[str, Any]] = []

    for obj_id in sorted(set(before_by_id) - set(after_by_id)):
        evidence.append({"object_class": object_class, "object_id": obj_id, "change": "removed"})
    if not suppress_added:
        for obj_id in sorted(set(after_by_id) - set(before_by_id)):
            evidence.append({"object_class": object_class, "object_id": obj_id, "change": "added"})
    for obj_id in sorted(set(before_by_id) & set(after_by_id)):
        before_obj, after_obj = before_by_id[obj_id], after_by_id[obj_id]
        for field_name in sorted(set(before_obj) | set(after_obj)):
            if field_name in always_exempt_fields:
                continue
            if exempt_targets is not None and any(
                target_id == obj_id and field_name in fields for target_id, fields in exempt_targets
            ):
                continue
            before_value, after_value = before_obj.get(field_name), after_obj.get(field_name)
            if _values_differ(before_value, after_value):
                evidence.append(
                    {
                        "object_class": object_class,
                        "object_id": obj_id,
                        "change": "field_changed",
                        "field": field_name,
                        "before": before_value,
                        "after": after_value,
                    }
                )
    return evidence


def _coerce_int_or_default(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _interval_no_from_action(spec: dict[str, str], action: dict[str, Any]) -> int:
    """1 for every tool without an ``interval_param`` in its spec, or when
    the action's own params omit/malform it -- the byte-compatible default
    (v2 unit ⑤)."""
    interval_param = spec.get("interval_param")
    if interval_param is None:
        return 1
    raw_interval_no = action.get("params", {}).get(interval_param, 1)
    return _coerce_int_or_default(raw_interval_no, default=1)


def _write_target_fields(spec: dict[str, str], action: dict[str, Any]) -> frozenset[str]:
    """Which top-level shadow-model field NAME(s) the nothing-else-moved diff
    should exempt for this write. Every tool without an ``interval_param``
    exempts ``{spec["value_field"]} | spec.get("coupled_fields", ())``
    (unchanged since M1.2/M2.2 for tools with no coupled fields).
    ``set_vehicle_input_volume`` (v2 unit ⑤) exempts ``{"volume", "volumes",
    "interval_count"}`` together, UNCONDITIONALLY regardless of interval_no
    -- see the ``_WRITE_TARGET_FIELD_SPEC`` comment for why the narrower
    per-interval-no exemption this function used to compute was a real
    false-positive regression against the demo network's zero-interval
    vehicle inputs, and why exempting the trio together is the more correct
    reading (they are coupled/derived views of the same XML structure), not
    merely a workaround. ``set_des_speed_decision`` (v2a unit ⑧) is the
    NON-interval case of the identical lesson, found LIVE
    (``backend/tools/live_check_des_speed_decision.py``): the flat entry's
    ``des_speed_distr_name`` is looked up from ``des_speed_distr_no`` at
    PARSE time (``tools_network._parse_des_speed_decisions``), so writing
    the id field always also changes the name field as an unavoidable side
    effect -- ``coupled_fields`` generalizes the "exempt the whole derived
    group together" fix beyond the interval-specific shape v2 unit ⑤
    introduced it for. QUEUE (v2a) unit's ``set_vehicle_type_reference`` is
    the reason ``action`` is now actually read (via :func:`_spec_value_field`)
    rather than merely accepted for signature symmetry -- its own value
    field name depends on the action's own ``field`` param.
    """
    value_field = _spec_value_field(spec, action)
    if spec.get("interval_param") is None:
        return frozenset({value_field, *spec.get("coupled_fields", ())})
    fields = {value_field, spec["interval_list_field"]}
    count_field = spec.get("interval_count_field")
    if count_field:
        fields.add(count_field)
    return frozenset(fields)


def _resolve_saved_value(
    spec: dict[str, str], action: dict[str, Any], target: dict[str, Any]
) -> tuple[str, Any]:
    """(field_label, saved_value) for the target-persisted check.

    Same interval-dimension redirect as :func:`_write_target_field`, but
    this one also digs into the interval LIST to find the matching entry by
    ``interval_no`` (the diff-exemption helper only needs the top-level
    field NAME, not a specific list entry).
    """
    interval_no = _interval_no_from_action(spec, action)
    value_field = _spec_value_field(spec, action)
    if interval_no == 1:
        return value_field, target.get(value_field)

    intervals = target.get(spec["interval_list_field"]) or []
    key_field = spec["interval_key_field"]
    value_field = spec["interval_value_field"]
    field_label = f"{spec['interval_list_field']}[{interval_no}].{value_field}"
    entry = next(
        (iv for iv in intervals if _coerce_int_or_default(iv.get(key_field), -1) == interval_no),
        None,
    )
    if entry is None:
        # The interval itself is absent from the AFTER model entirely -- a
        # genuinely dropped write (or an index Vissim never had) reads the
        # exact same way as "not persisted": no value at all.
        return field_label, None
    return field_label, entry.get(value_field)


def _paired_extra_target_id(extra_spec: dict[str, Any], action: dict[str, Any]) -> str | None:
    """The extra (restore) target's id, or ``None`` when this action's own
    ``trigger_param`` is absent/falsy — i.e. the write was NOT paired this
    time, so no second exemption applies (see ``_PAIRED_EXTRA_TARGET_SPEC``).
    """
    trigger_value = action.get("params", {}).get(extra_spec["trigger_param"])
    if not trigger_value:
        return None
    return _target_id_for_spec(extra_spec, action)


def _nothing_else_moved_diff(
    action: dict[str, Any], before_model: dict[str, Any], after_model: dict[str, Any]
) -> list[dict[str, Any]]:
    tool = action.get("tool", "")
    spec = _WRITE_TARGET_FIELD_SPEC.get(tool)
    exemptions: dict[str, list[tuple[str, frozenset[str]]]] = {}
    if spec is not None:
        target_id = _target_id_for_spec(spec, action)
        exemptions.setdefault(spec["object_class"], []).append(
            (target_id, _write_target_fields(spec, action))
        )

    extra_spec = _PAIRED_EXTRA_TARGET_SPEC.get(tool)
    if extra_spec is not None:
        extra_target_id = _paired_extra_target_id(extra_spec, action)
        if extra_target_id is not None:
            exemptions.setdefault(extra_spec["object_class"], []).append(
                (extra_target_id, frozenset({extra_spec["value_field"], *extra_spec.get("coupled_fields", ())}))
            )

    allow_new = _ALLOWS_NEW_OBJECTS_IN.get(tool, frozenset())

    evidence: list[dict[str, Any]] = []
    for object_class, id_field in _SHADOW_OBJECT_ID_FIELDS.items():
        evidence.extend(
            _diff_object_list(
                object_class,
                id_field,
                before_model.get(object_class, []),
                after_model.get(object_class, []),
                exemptions.get(object_class),
                suppress_added=object_class in allow_new,
                always_exempt_fields=_ALWAYS_EXEMPT_FIELDS.get(object_class, frozenset()),
            )
        )
    return evidence


def _values_differ(before: Any, after: Any) -> bool:
    """Recursive inequality with numeric tolerance — the single comparison
    semantics for both the target-persisted check and the
    nothing-else-moved diff.

    Numeric pairs compare with the same 1e-9 tolerance as constraint #6's
    value-compare (bool excluded — an int subclass, but equality-comparing
    True to 1.0 numerically would be nonsense here). Lists/tuples and dicts
    recurse element-wise, and any STRUCTURAL difference (length, key set,
    type mix) is a real difference — only numeric noise is forgiven.

    Why recursion matters (live-verified, M2.3 — see DECISIONS.md
    2026-07-23): Vissim's own ``SaveNetAs`` re-serializes auto-computed
    connector geometry (``links[].points``, lists of float pairs) with
    ~5.7e-14 floating-point drift on EVERY save. An exact ``!=`` on those
    fields refused every real-world write as "something else moved" even
    though nothing did. 1e-9 is ~5 orders of magnitude above the observed
    noise and ~7 below any geometrically meaningful coordinate change
    (millimeters), so real edits still register as evidence.
    """
    if (
        isinstance(before, (int, float))
        and isinstance(after, (int, float))
        and not isinstance(before, bool)
        and not isinstance(after, bool)
    ):
        return abs(float(before) - float(after)) > 1e-9
    if isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
        if len(before) != len(after):
            return True
        return any(_values_differ(b, a) for b, a in zip(before, after))
    if isinstance(before, dict) and isinstance(after, dict):
        if set(before) != set(after):
            return True
        return any(_values_differ(before[key], after[key]) for key in before)
    return before != after


def _check_one_target_persisted(
    spec: dict[str, Any], action: dict[str, Any], after_model: dict[str, Any], written_value: Any
) -> Finding | None:
    """The shared single-target implementation both
    :func:`_target_not_persisted_finding` calls run — once for a tool's
    primary target, and (v2a unit ⑧) once more for its optional paired
    restore target when present. See that function's own docstring for the
    overall degrade-silently policy this shares.
    """
    target_id = _target_id_for_spec(spec, action)
    id_field = _SHADOW_OBJECT_ID_FIELDS[spec["object_class"]]
    target = next(
        (
            obj
            for obj in (after_model.get(spec["object_class"]) or [])
            if str(obj.get(id_field, "")) == target_id
        ),
        None,
    )
    if target is None:
        return None

    field, saved_value = _resolve_saved_value(spec, action, target)
    if saved_value is None:
        if spec.get("none_means") != "not_persisted":
            # Parser-blind field: no file-level claim can be made either
            # way (e.g. cycle_time on opaque program-driven controllers per
            # the M2.1 CycTm finding) — a refusal here would be a false
            # positive, not caution.
            return None
        # Parseable-but-unset field: the parser WOULD show the value had the
        # write reached the file, so None after a write IS the evidence the
        # write was silently dropped — fall through to the finding.
    elif not _values_differ(written_value, saved_value):
        return None

    return Finding(
        constraint_id="write_verification_failed",
        tier="refuse",
        phase="postwrite",
        messages={
            "ko": (
                f"쓰기 검증 실패: 저장된 네트워크 파일에 기록한 값이 반영되지 않았습니다 "
                f"({field}: 기록 {written_value} ≠ 저장 {saved_value})."
            ),
            "en": (
                f"Write verification failed: the saved network file does not reflect the "
                f"written value ({field}: wrote {written_value}, file has {saved_value})."
            ),
            "ja": (
                f"書き込み検証に失敗しました: 保存されたネットワークファイルに書き込んだ値が"
                f"反映されていません ({field}: 書き込み {written_value} ≠ 保存 {saved_value})。"
            ),
        },
        suggested_fix=None,
        details={
            "persistence_check": {
                "object_class": spec["object_class"],
                "object_id": target_id,
                "field": field,
                "written_value": written_value,
                "saved_value": saved_value,
            }
        },
    )


def _target_not_persisted_finding(
    action: dict[str, Any], after_model: dict[str, Any], com_result: dict[str, Any]
) -> Finding | None:
    """Controller review addition: verify the written value is actually
    present in the AFTER shadow model (i.e. in the saved network file the
    model was reparsed from).

    The read-back compare proves Vissim's IN-MEMORY state took the write;
    the nothing-else-moved diff proves nothing UNRELATED changed in the
    file. Neither proves the intended change itself reached the file — a
    write tool that succeeds over COM but never calls ``save_net_as`` before
    returning produces identical before/after models (diff: silent) and a
    matching read-back (compare: silent), leaving the versioned artifact a
    lie. This check closes that triangle.

    Degrades silently (returns ``None``) when it cannot make a claim either
    way: unmapped tool, no ``written_value`` in the verification payload,
    target object absent from the after model (``object_not_found``/the
    diff's "removed" evidence own those cases), or the parsed field is
    ``None`` — the parser cannot see that attribute for this object (e.g.
    signal ``cycle_time`` for opaque program-driven controllers, per the
    M2.1 ``CycTm`` finding in docs/phase2/DECISIONS.md), so a file-level
    persistence claim would be a guess, and a refusal here would be a false
    positive rather than caution.

    **v2a unit ⑧:** when the tool has a ``_PAIRED_EXTRA_TARGET_SPEC`` entry
    AND its ``trigger_param`` is present in this action's own params, the
    SAME check also runs against the restore target (keyed off
    ``com_result["restore_written_value"]``) after the primary target
    passes — genuine on-disk proof for BOTH halves of a paired write, not
    just an inference from the primary target's own check. (In practice a
    correctly-implemented paired write tool never reaches persist() at all
    unless both COM read-backs already matched, so this mostly re-confirms
    what the tool already verified — see tools_edit.py's
    ``set_des_speed_decision`` docstring — but it is real, independent proof
    against the SAVED FILE, not merely COM's in-memory state.)
    """
    spec = _WRITE_TARGET_FIELD_SPEC.get(action.get("tool", ""))
    if spec is None or "written_value" not in com_result:
        return None

    finding = _check_one_target_persisted(spec, action, after_model, com_result["written_value"])
    if finding is not None:
        return finding

    extra_spec = _PAIRED_EXTRA_TARGET_SPEC.get(action.get("tool", ""))
    if extra_spec is not None and "restore_written_value" in com_result:
        extra_target_id = _paired_extra_target_id(extra_spec, action)
        if extra_target_id is not None:
            finding = _check_one_target_persisted(
                extra_spec, action, after_model, com_result["restore_written_value"]
            )

    return finding


def _diff_finding(evidence: list[dict[str, Any]]) -> Finding:
    """A refuse-tier ``write_verification_failed`` finding carrying the
    structural diff as evidence — reuses the existing constraint id per the
    M2.2 brief ("prefer attaching diff evidence to report details over
    inventing new constraint ids") rather than a bespoke id like
    ``unexpected_side_effect``."""
    summary = "; ".join(
        f"{item['object_class']}#{item['object_id']}:{item.get('field', item['change'])}"
        for item in evidence
    )
    return Finding(
        constraint_id="write_verification_failed",
        tier="refuse",
        phase="postwrite",
        messages={
            "ko": f"쓰기 검증 실패: 의도한 대상 외에 다른 값이 변경되었습니다 ({summary}).",
            "en": (
                f"Write verification failed: changes were detected outside the intended "
                f"target ({summary})."
            ),
            "ja": f"書き込み検証に失敗しました: 意図した対象以外の値が変更されました ({summary})。",
        },
        suggested_fix=None,
        details={"nothing_else_moved_diff": evidence},
    )

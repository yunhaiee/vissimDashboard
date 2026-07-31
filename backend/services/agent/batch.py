"""QUEUE 3 unit 8: the batch-planning core for ``tools_edit.apply_edit_plan``.

Mirrors ``rollback.py``'s own split (QUEUE 3 unit 7): this module holds the
PURE planning/validation logic (no COM, no file I/O beyond one small byte-copy
helper reused for the mid-batch abort path); ``tools_edit.apply_edit_plan``
is the thin tool function that wires this module's output into the real
:class:`~services.agent.edit_session.VissimEditSession` + ``toolkit.dispatch``
call sequence, exactly like ``rollback_last_edit`` wires ``rollback.py``'s
planning output into a real file restore.

**The user-approved design** (docs/phase2 v2 queue, unit 8): all-or-nothing.
One approval card lists every step's diff; each step is preflighted
individually; steps apply serially with per-step readback; the first failed
readback (or failed step of any kind) aborts the batch and rolls back every
already-applied step. No partial-success end state, ever.

**Two validation classes, per the task brief:**

1. *Plan validation* (:func:`validate_plan`'s structural pass + the per-step
   preflight loop): every step's tool must be a registered write-tier tool
   (refuse ``run_simulation``, every read tool, ``rollback_last_edit``, and
   ``apply_edit_plan`` itself — see :data:`BATCH_STEP_BLOCKLIST`, no
   recursion); every step's params must validate against that tool's own
   registered params model; every step gets the FULL preflight constraint
   run (``constraints.run_checks("preflight", ...)``) against the CURRENT
   (unpatched) shadow model — this is a loop over the EXISTING engine,
   nothing new.

2. *Cross-step interaction checks* — the genuinely new class:

   - **Same-object-same-field collision**: two steps whose OWN resolvable
     write target (:func:`resolve_write_target`, built on top of
     ``guardrails.py``'s existing ``_WRITE_TARGET_FIELD_SPEC`` table — see
     that function's own docstring for why importing those private names
     directly is the correct, precedented choice here) name the SAME
     ``(object_class, target_id)`` pair AND share at least one field name —
     the later step would silently overwrite the earlier one's intent.
     Refused as almost-certainly-unintended, naming both step indices.
   - **Combined effect trips a constraint no single step trips**: the known
     worked case is two ``set_vehicle_input_volume`` steps on SAME-LINK
     SIBLINGS whose combined total exceeds capacity even though each step,
     checked alone against the CURRENT (pre-batch) shadow model, is under
     capacity — the sibling-aware ``latent_demand_overflow``/
     ``vehicle_input_siblings_share_link`` checks (constraints.py) only ever
     see ONE write's proposed value against the OTHER siblings' CURRENT
     (unwritten) values, so a plan whose EARLIER step already raised a
     sibling's effective volume is invisible to those checks unless the
     shadow model they run against reflects that earlier step's intent.
     **The mechanism**: for step *i* (*i* > 1), :func:`validate_plan` runs
     that step's OWN preflight a SECOND time, against a shadow model
     PATCHED (:func:`_apply_patch`) with every EARLIER step's own intended
     value — never against real Vissim, never through COM, purely an
     in-memory ``copy.deepcopy`` + targeted field mutation. Any finding that
     appears in that PATCHED pass but did not appear in step *i*'s own
     pass against the UNPATCHED model is, by construction, caused ONLY by
     the combination — invisible to reviewing each step in isolation, which
     is exactly the same "almost-certainly-unintended, name both steps"
     framing the same-target-collision case uses. **Design decision**: such
     a combined-effect finding is ALWAYS escalated to refuse-tier for the
     batch (via :func:`_combined_effect_finding`), REGARDLESS of the
     underlying single-edit constraint's own registered tier (e.g.
     ``latent_demand_overflow`` is warn-tier for a single edit). Justified
     because the whole point of this check is that NEITHER step's own
     individual review would have caught it; letting it merely ride as a
     warning the user might click past on a 10-step approval card would
     silently defeat the reason this check exists. A single-step edit's own
     preflight is unaffected — this escalation happens only inside
     ``validate_plan``'s own batch-specific findings.

Patching is necessarily a bounded, explicit per-tool mapping
(:data:`_PATCHERS` + the generic interval-aware path) — no existing
machinery in this codebase predicts a pre-write "intended value" from a
tool's own params (every existing check either reads the CURRENT shadow
model, or compares an AFTER-the-fact written/readback pair), so this is
genuinely new logic, scoped to the current write-tool catalog. A step whose
tool has no resolvable target (``set_reduced_speed_area`` — deliberately
absent from ``_WRITE_TARGET_FIELD_SPEC``, see tools_edit.py's own module
docstring) or no registered patcher degrades conservatively: its own effect
is invisible to LATER steps' patched-shadow preflight, and it can never be
named in a same-target collision either — documented gaps, not silent
wrong answers (nothing about that step's OWN preflight against the
unpatched model is affected).
"""

from __future__ import annotations

import copy
import dataclasses
import difflib
from typing import Any, Callable

from pydantic import ValidationError

from services.agent import constraints
from services.agent.constraints import Finding
# Reuse guardrails.py's private write-target tables directly rather than
# re-deriving the same "which params identify a write's target" knowledge.
# approval_focus.py already established this exact exception to the
# mirror-don't-reach-into-a-sibling-module's-private-table convention (see
# that module's own docstring): this module and guardrails.py must always
# agree on which params name a write's target, so importing is the choice
# that CANNOT drift, unlike re-deriving the same table a second time.
from services.agent.guardrails import (
    _SHADOW_OBJECT_ID_FIELDS,
    _WRITE_TARGET_FIELD_SPEC,
    _spec_value_field,
    _target_id_for_spec,
    _values_differ,
    _write_target_fields,
)
from services.agent.toolkit import ToolRegistry, _format_validation_errors, default_registry

#: A plan needs at least 2 steps (1 is just the tool itself — use it
#: directly) and at most 10 (approval-card readability / preflight cost;
#: stated explicitly in both the tool's own docstring and the refusal
#: message below).
MIN_STEPS = 2
MAX_STEPS = 10

#: Tools a batch step may never name. Every OTHER ineligible case (a read
#: tool, ``run_simulation``) is already caught structurally by the
#: ``spec.tier != "write"`` check in :func:`_validate_steps` — these two ARE
#: tier="write" themselves but are explicitly blocked: ``rollback_last_edit``
#: (its one-level-undo semantics target "the most recently applied edit" via
#: session history, not a batch-internal notion of a step — undo does not
#: compose inside a batch) and ``apply_edit_plan`` itself (no recursion).
BATCH_STEP_BLOCKLIST = frozenset({"rollback_last_edit", "apply_edit_plan"})

#: Re-exported so ``tools_edit.apply_edit_plan`` gets the SAME numeric-
#: tolerant comparison ``guardrails.py``'s postwrite checks use for its own
#: per-step readback verification, rather than a second, potentially
#: disagreeing implementation.
values_differ = _values_differ


class BatchAbortedError(RuntimeError):
    """Raised by ``tools_edit.apply_edit_plan`` when a step fails during the
    serial apply loop, AFTER every already-applied step has been rolled back
    to the pre-batch state.

    The message embeds the literal, greppable marker ``batch_step_apply_
    failed`` (mirroring ``NoEditSessionError``/``rollback.py``'s own
    convention) and is capped for readability by ``toolkit.dispatch``'s
    300-char exception-summary sanitizer -- the essential facts (step index,
    tool, marker, rollback confirmation) are front-loaded so they survive
    truncation. The FULL structured detail also rides in :attr:`details`,
    which ``toolkit.dispatch``'s exception handler surfaces verbatim as the
    error envelope's ``artifacts`` (a purely additive convention -- see that
    function's own comment) -- this is the "structured result naming which
    step failed" the task brief asks for, independent of the human-readable
    message's own length cap.
    """

    def __init__(
        self,
        *,
        step_index: int,
        total_steps: int,
        tool: str,
        reason: str,
        rolled_back: list[dict[str, Any]],
        never_reached: list[dict[str, Any]],
        restore_verified: bool,
    ) -> None:
        self.step_index = step_index
        self.total_steps = total_steps
        self.tool = tool
        self.reason = reason
        self.restore_verified = restore_verified
        rolled_back_text = ", ".join(str(s["index"]) for s in rolled_back) or "none"
        never_reached_text = ", ".join(str(s["index"]) for s in never_reached) or "none"
        message = (
            f"batch_step_apply_failed: step {step_index}/{total_steps} ({tool}) failed at apply "
            f"time: {reason} Step(s) [{rolled_back_text}] that had already been applied were "
            f"rolled back to the pre-batch state (restore verified byte-identical: "
            f"{restore_verified}). Step(s) [{never_reached_text}] were never attempted."
        )
        self.details: dict[str, Any] = {
            "failed_step_index": step_index,
            "failed_step_tool": tool,
            "total_steps": total_steps,
            "reason": reason,
            "restore_verified": restore_verified,
            "steps": (
                [dict(s, status="rolled_back") for s in rolled_back]
                + [{"index": step_index, "tool": tool, "status": "failed", "reason": reason}]
                + [dict(s, status="never_reached") for s in never_reached]
            ),
        }
        super().__init__(message)


# ---------------------------------------------------------------------------
# Write-target resolution (shared by collision detection, patching, and the
# plan-overview description -- ONE lookup, reused three ways).
# ---------------------------------------------------------------------------


def reverted_targets_from_diff(diff: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket a ``guardrails.diff_shadow_models`` structural diff into the
    ``reverted_targets`` shape ``guardrails.GuardrailEngine.postwrite``
    already reads generically to build its dynamic diff exemption -- the
    IDENTICAL transform ``tools_edit.rollback_last_edit`` applies inline to
    ITS OWN diff (kept separate/unshared there deliberately, to avoid
    touching that already-shipped, already-tested unit's code for this one).

    ``tools_edit.apply_edit_plan`` uses this for the FORWARD direction: diff
    the shadow model immediately before the batch's first step against
    immediately after its last step, and declare EVERYTHING that actually
    changed as the batch's own expected target -- this is deliberately more
    robust than trying to predict a step's full effect ahead of time
    (:func:`resolve_write_target`/:func:`_patch_for_step` only ever know
    about a step's OWN declared scalar/dict/list field; a tool like
    ``set_reduced_speed_area`` also creates a brand-new side-effect object
    -- a ``des_speed_distributions`` entry -- that no static per-tool table
    predicts, and that tool's own ``guardrails._ALLOWS_NEW_OBJECTS_IN`` entry
    is keyed to ITS OWN tool name, not ``apply_edit_plan``'s, so it would
    never apply to a batched call). A real diff sidesteps all of that: the
    exemption is exactly "whatever changed while this batch ran," verified
    mechanically rather than predicted.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in diff:
        key = (entry["object_class"], entry["object_id"])
        bucket = buckets.setdefault(
            key,
            {
                "object_class": entry["object_class"],
                "object_id": entry["object_id"],
                "fields": [],
                "expect_removed": False,
                "expect_added": False,
            },
        )
        if entry["change"] == "field_changed":
            bucket["fields"].append(entry["field"])
        elif entry["change"] == "removed":
            bucket["expect_removed"] = True
        elif entry["change"] == "added":
            bucket["expect_added"] = True
    for bucket in buckets.values():
        bucket["fields"] = sorted(set(bucket["fields"]))
    return list(buckets.values())


def resolve_write_target(action: dict[str, Any]) -> dict[str, Any] | None:
    """``{"object_class", "target_id", "fields"}`` this single write action's
    own RESOLVABLE target is, per ``guardrails._WRITE_TARGET_FIELD_SPEC`` --
    the SAME lookup ``guardrails.py``'s own diff-exemption bookkeeping uses
    internally, exposed here for this module's own three consumers.

    ``None`` for a tool with no single-target spec entry (e.g.
    ``set_reduced_speed_area``) -- every consumer below degrades to "no claim
    can be made" for that step, the same conservative default the spec table
    itself documents for an unmapped tool elsewhere in this codebase.
    """
    tool = action.get("tool", "")
    spec = _WRITE_TARGET_FIELD_SPEC.get(tool)
    if spec is None:
        return None
    return {
        "object_class": spec["object_class"],
        "target_id": _target_id_for_spec(spec, action),
        "fields": _write_target_fields(spec, action),
    }


# ---------------------------------------------------------------------------
# Patched-shadow preflight: predicting a step's own intended value.
#
# Genuinely new logic (see module docstring) -- a bounded, explicit per-tool
# mapping. Every patcher takes (params, current_target_object) and returns a
# dict of {shadow_model_field: new_value} to merge onto the target.
# ---------------------------------------------------------------------------


def _coerce_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _patch_interval_aware(spec: dict[str, Any], step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Generic patcher for any ``_WRITE_TARGET_FIELD_SPEC`` entry that
    declares an ``interval_param`` (``set_vehicle_input_volume`` -- the
    task's own named worked example -- and ``set_route_relative_flow``,
    which shares the identical interval-list shape). Mirrors
    ``guardrails._resolve_saved_value``'s own interval-list navigation, but
    WRITES a new entry (or appends one) instead of only reading."""
    value_field = _spec_value_field(spec, {"params": step_params})
    interval_no = _coerce_int(step_params.get(spec.get("interval_param")), 1)
    new_value = step_params.get(value_field)
    list_field = spec["interval_list_field"]
    key_field = spec["interval_key_field"]
    iv_value_field = spec["interval_value_field"]
    count_field = spec.get("interval_count_field")

    existing = target.get(list_field) or []
    if interval_no == 1 and not existing:
        # Byte-compatible with the non-interval-aware fixtures/networks (v2
        # unit ⑤'s own documented fallback): no volumes[] list at all yet,
        # interval 1 lives only in the top-level scalar field.
        return {value_field: new_value}

    new_list = [dict(iv) for iv in existing]
    found = False
    for iv in new_list:
        if _coerce_int(iv.get(key_field), -1) == interval_no:
            iv[iv_value_field] = new_value
            found = True
    if not found:
        new_list.append({key_field: interval_no, iv_value_field: new_value})
    patch: dict[str, Any] = {list_field: new_list}
    if count_field:
        patch[count_field] = len(new_list)
    if interval_no == 1:
        patch[value_field] = new_value
    return patch


def _patch_generic_scalar(field_name: str, param_name: str) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    def _patch(step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        del target
        return {field_name: step_params.get(param_name)}

    return _patch


def _patch_signal_cycle(step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    del target
    return {"cycle_time": step_params.get("cycle")}


def _patch_vehicle_type_reference(step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    del target
    field = step_params.get("field", "") or ""
    return {f"{field}_no": step_params.get("target_no")} if field else {}


def _patch_driving_behavior_parameter(step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    del target
    parameter = step_params.get("parameter", "") or ""
    return {parameter: step_params.get("value")} if parameter else {}


def _patch_dict_field(field_name: str, sub_fields: tuple[str, ...]) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """Patcher for a compound ``value_field`` (``green_window``,
    ``gap_headway``, ``mean_std_dev``) -- merges only the sub-field(s) this
    step actually supplies onto the target's CURRENT dict, matching how
    ``set_priority_rule_gap_headway``/``set_time_distribution_normal`` treat
    an omitted sub-field as "leave it unchanged" rather than clearing it."""

    def _patch(step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        current = dict(target.get(field_name) or {})
        for sub_field in sub_fields:
            if step_params.get(sub_field) is not None:
                current[sub_field] = step_params[sub_field]
        return {field_name: current}

    return _patch


def _patch_points_field(step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    del target
    points = step_params.get("points")
    return {"points": points} if points is not None else {}


def _patch_link_behavior_type_driving_behavior(step_params: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    del target
    return {"driving_behavior_no": step_params.get("driving_behavior_no")}


#: tool -> patcher, for every ``_WRITE_TARGET_FIELD_SPEC`` entry WITHOUT an
#: ``interval_param`` (those route through :func:`_patch_interval_aware`
#: generically instead -- see :func:`_patch_for_step`). A tool absent here
#: (and not interval-aware) contributes no patch: LATER steps' patched-shadow
#: preflight simply will not see this step's own effect, a documented,
#: conservative gap (that step's OWN preflight against the unpatched model is
#: unaffected either way).
_PATCHERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "set_signal_cycle": _patch_signal_cycle,
    "set_signal_offset": _patch_generic_scalar("offset", "offset"),
    "set_conflict_area_status": _patch_generic_scalar("status", "status"),
    "set_vehicle_composition_share": _patch_generic_scalar("rel_flow", "rel_flow"),
    "set_des_speed_decision": _patch_generic_scalar("des_speed_distr_no", "des_speed_distr_no"),
    "set_signal_green_split": _patch_dict_field("green_window", ("green_start_s", "green_end_s")),
    "set_priority_rule_gap_headway": _patch_dict_field("gap_headway", ("gap_time_s", "headway_m")),
    "set_des_speed_distribution_points": _patch_points_field,
    "set_vehicle_type_reference": _patch_vehicle_type_reference,
    "set_time_distribution_normal": _patch_dict_field("mean_std_dev", ("mean_s", "std_dev_s")),
    "set_time_distribution_points": _patch_points_field,
    "set_driving_behavior_parameter": _patch_driving_behavior_parameter,
    "set_link_behavior_type_driving_behavior": _patch_link_behavior_type_driving_behavior,
}


def _patch_for_step(action: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """The ``{field: new_value}`` patch this ONE step's own intended write
    represents, against ``target`` (the CURRENT shadow-model object it
    addresses) -- empty when the tool has no resolvable target or no
    registered patcher (documented gap, see module docstring)."""
    tool = action.get("tool", "")
    spec = _WRITE_TARGET_FIELD_SPEC.get(tool)
    if spec is not None and spec.get("interval_param") is not None:
        return _patch_interval_aware(spec, action.get("params", {}), target)
    patcher = _PATCHERS.get(tool)
    if patcher is None:
        return {}
    return patcher(action.get("params", {}), target)


def _apply_patch(shadow_model: dict[str, Any], action: dict[str, Any]) -> None:
    """Mutate ``shadow_model`` IN PLACE so it reflects ``action``'s own
    intended value -- the patched-shadow preflight mechanism a LATER step's
    combined-effect check runs against. No-op when the target can't be
    resolved or patched (see :func:`_patch_for_step`)."""
    target_info = resolve_write_target(action)
    if target_info is None or not target_info["target_id"]:
        return
    object_class = target_info["object_class"]
    id_field = _SHADOW_OBJECT_ID_FIELDS.get(object_class)
    if id_field is None:
        return
    for obj in shadow_model.get(object_class, []):
        if str(obj.get(id_field, "")) == target_info["target_id"]:
            obj.update(_patch_for_step(action, obj))
            return


# ---------------------------------------------------------------------------
# Structural validation: step count, tool eligibility, params shape.
# ---------------------------------------------------------------------------


def _step_count_finding(count: int) -> Finding:
    return Finding(
        constraint_id="apply_edit_plan_step_count_out_of_range",
        tier="refuse",
        phase="preflight",
        messages={
            "ko": f"편집 계획은 최소 {MIN_STEPS}단계, 최대 {MAX_STEPS}단계여야 합니다 (요청된 단계: "
            f"{count}개). 단계가 1개뿐이라면 해당 도구를 직접 호출하세요.",
            "en": f"An edit plan must have between {MIN_STEPS} and {MAX_STEPS} steps (requested: "
            f"{count}). A single-step plan should just call that tool directly instead.",
            "ja": f"編集計画は最小{MIN_STEPS}段階、最大{MAX_STEPS}段階である必要があります(要求された"
            f"段階数: {count})。段階が1つだけの場合はそのツールを直接呼び出してください。",
        },
        suggested_fix={"min_steps": MIN_STEPS, "max_steps": MAX_STEPS},
        details={"requested_step_count": count},
    )


def _malformed_step_finding(index: int, raw: Any) -> Finding:
    return Finding(
        constraint_id="apply_edit_plan_step_malformed",
        tier="refuse",
        phase="preflight",
        messages={
            "ko": f"{index}번째 단계의 형식이 올바르지 않습니다 -- {{\"tool\": str, \"params\": "
            "dict}} 형태여야 합니다.",
            "en": f"Step {index} is malformed -- every step must be shaped "
            '{"tool": str, "params": dict}.',
            "ja": f"{index}番目の段階の形式が正しくありません -- "
            '{"tool": str, "params": dict} 形式である必要があります。',
        },
        suggested_fix=None,
        details={"batch_step_index": index, "raw_step": raw if isinstance(raw, (dict, str)) else str(raw)},
    )


def _unknown_tool_finding(index: int, tool: str, registry: ToolRegistry) -> Finding:
    candidates = difflib.get_close_matches(tool, registry.names(), n=5)
    return Finding(
        constraint_id="apply_edit_plan_step_unknown_tool",
        tier="refuse",
        phase="preflight",
        messages={
            "ko": f"{index}번째 단계의 도구 '{tool}'을(를) 찾을 수 없습니다.",
            "en": f"Step {index}'s tool '{tool}' is not a registered tool.",
            "ja": f"{index}番目の段階のツール '{tool}' は登録されていません。",
        },
        suggested_fix=candidates or None,
        details={"batch_step_index": index, "tool": tool},
    )


def _ineligible_tool_finding(index: int, tool: str, tier: str) -> Finding:
    reason = "blocklisted (no recursion / no undo-inside-a-batch)" if tool in BATCH_STEP_BLOCKLIST else f"tier {tier!r}, not 'write'"
    return Finding(
        constraint_id="apply_edit_plan_step_tool_ineligible",
        tier="refuse",
        phase="preflight",
        messages={
            "ko": f"{index}번째 단계의 도구 '{tool}'은(는) 배치 단계로 사용할 수 없습니다 "
            f"({'재귀 호출 금지' if tool in BATCH_STEP_BLOCKLIST else '쓰기(write) 등급 도구만 허용'}). "
            "배치 단계는 등록된 쓰기 등급 도구여야 하며, run_simulation/조회 도구/rollback_last_edit/"
            "apply_edit_plan 자신은 허용되지 않습니다.",
            "en": f"Step {index}'s tool '{tool}' cannot be used as a batch step ({reason}). Every "
            "batch step must be a registered write-tier tool; run_simulation, every read tool, "
            "rollback_last_edit, and apply_edit_plan itself are all refused.",
            "ja": f"{index}番目の段階のツール '{tool}' はバッチ段階として使用できません ({reason})。"
            "バッチの各段階は登録済みの書き込み(write)等級ツールである必要があり、run_simulation、"
            "参照ツール、rollback_last_edit、apply_edit_plan自身は許可されません。",
        },
        suggested_fix=None,
        details={"batch_step_index": index, "tool": tool, "tier": tier},
    )


def _invalid_step_params_finding(index: int, tool: str, exc: ValidationError) -> Finding:
    detail = _format_validation_errors(exc)
    return Finding(
        constraint_id="apply_edit_plan_step_invalid_params",
        tier="refuse",
        phase="preflight",
        messages={
            "ko": f"{index}번째 단계({tool})의 파라미터가 올바르지 않습니다: {detail}",
            "en": f"Step {index} ({tool})'s params are invalid: {detail}",
            "ja": f"{index}番目の段階({tool})のパラメータが正しくありません: {detail}",
        },
        suggested_fix=None,
        details={"batch_step_index": index, "tool": tool, "validation_errors": detail},
    )


def _validate_steps(
    raw_steps: Any, registry: ToolRegistry
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """Structural + per-step-tool/params validation (task requirement #1's
    first half). Returns ``(validated_steps, findings)`` -- ``validated_steps``
    is ``[]`` (never partial) whenever ``findings`` contains ANY refuse-tier
    entry, since a plan with even one unresolved step is not safe to
    preflight/patch further (a bad step could be anything from a typo to a
    hostile tool name)."""
    findings: list[Finding] = []
    if not isinstance(raw_steps, list) or not (MIN_STEPS <= len(raw_steps) <= MAX_STEPS):
        findings.append(_step_count_finding(len(raw_steps) if isinstance(raw_steps, list) else 0))
        return [], findings

    steps: list[dict[str, Any]] = []
    ok = True
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("tool"), str) or not isinstance(
            raw.get("params"), dict
        ):
            findings.append(_malformed_step_finding(index, raw))
            ok = False
            continue
        tool = raw["tool"]
        spec = registry.get(tool)
        if spec is None:
            findings.append(_unknown_tool_finding(index, tool, registry))
            ok = False
            continue
        if tool in BATCH_STEP_BLOCKLIST or spec.tier != "write":
            findings.append(_ineligible_tool_finding(index, tool, spec.tier))
            ok = False
            continue
        try:
            validated = spec.params_model.model_validate(raw["params"])
        except ValidationError as exc:
            findings.append(_invalid_step_params_finding(index, tool, exc))
            ok = False
            continue
        steps.append({"tool": tool, "params": validated.model_dump()})

    if not ok:
        return [], findings
    return steps, findings


# ---------------------------------------------------------------------------
# Cross-step interaction: same-object-same-field collision.
# ---------------------------------------------------------------------------


def _collision_finding(
    first_index: int,
    first_tool: str,
    second_index: int,
    second_tool: str,
    object_class: str,
    target_id: str,
    shared_fields: frozenset[str],
) -> Finding:
    fields_text = ", ".join(sorted(shared_fields))
    return Finding(
        constraint_id="apply_edit_plan_target_collision",
        tier="refuse",
        phase="preflight",
        messages={
            "ko": f"{first_index}번째 단계({first_tool})와 {second_index}번째 단계({second_tool})가 "
            f"같은 대상 {object_class}#{target_id}의 같은 필드({fields_text})를 각각 다른 값으로 "
            f"수정합니다 -- 나중 단계가 앞 단계의 결과를 조용히 덮어쓰게 되어 의도치 않은 것으로 "
            "간주하고 계획 전체를 거부합니다.",
            "en": f"Step {first_index} ({first_tool}) and step {second_index} ({second_tool}) both "
            f"target the SAME object {object_class}#{target_id} and the SAME field(s) "
            f"({fields_text}) -- the later step would silently overwrite the earlier one's result. "
            "Refused as almost-certainly-unintended.",
            "ja": f"{first_index}番目の段階({first_tool})と{second_index}番目の段階({second_tool})"
            f"が同じ対象 {object_class}#{target_id} の同じフィールド({fields_text})をそれぞれ異なる"
            "値に変更しようとしています -- 後の段階が前の段階の結果を静かに上書きすることになるため、"
            "意図しないものとみなし計画全体を拒否します。",
        },
        suggested_fix=None,
        details={
            "first_step_index": first_index,
            "first_step_tool": first_tool,
            "second_step_index": second_index,
            "second_step_tool": second_tool,
            "object_class": object_class,
            "target_id": target_id,
            "shared_fields": sorted(shared_fields),
        },
    )


def _find_target_collisions(steps: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[tuple[str, str], list[tuple[int, str, frozenset[str]]]] = {}
    for index, step in enumerate(steps, start=1):
        target = resolve_write_target(step)
        if target is None or not target["target_id"]:
            continue
        key = (target["object_class"], target["target_id"])
        for prior_index, prior_tool, prior_fields in seen.get(key, []):
            shared = prior_fields & target["fields"]
            if shared:
                findings.append(
                    _collision_finding(
                        prior_index, prior_tool, index, step["tool"], target["object_class"],
                        target["target_id"], shared,
                    )
                )
        seen.setdefault(key, []).append((index, step["tool"], target["fields"]))
    return findings


# ---------------------------------------------------------------------------
# Cross-step interaction: combined effect trips a constraint no single step
# trips (the patched-shadow preflight mechanism -- see module docstring).
# ---------------------------------------------------------------------------


def _combined_effect_finding(
    original: Finding, index: int, tool: str, prior_step_indices: list[int]
) -> Finding:
    prior_text = ", ".join(str(i) for i in prior_step_indices)
    original_text = original.messages.get("en", "")
    return Finding(
        constraint_id="apply_edit_plan_combined_effect",
        tier="refuse",
        phase="preflight",
        messages={
            "ko": f"{index}번째 단계({tool})는 단독으로는 문제가 없지만, 앞선 단계({prior_text})의 "
            f"의도한 값을 함께 반영해 확인하면 '{original.constraint_id}' 제약을 위반합니다: "
            f"{original.messages.get('ko', original_text)} 이는 각 단계를 따로 검토해서는 발견할 수 "
            "없는 조합 효과이므로 의도치 않은 것으로 간주하고 계획 전체를 거부합니다.",
            "en": f"Step {index} ({tool}) is fine on its own, but combined with the intended "
            f"value(s) of earlier step(s) [{prior_text}], it trips the '{original.constraint_id}' "
            f"constraint: {original_text} This is a combined effect invisible to reviewing each "
            "step in isolation, so it is refused as almost-certainly-unintended.",
            "ja": f"{index}番目の段階({tool})は単独では問題ありませんが、前の段階({prior_text})の"
            f"意図した値と合わせて確認すると '{original.constraint_id}' 制約に違反します: "
            f"{original.messages.get('ja', original_text)} これは各段階を個別に確認しても発見できない"
            "組み合わせ効果であるため、意図しないものとみなし計画全体を拒否します。",
        },
        suggested_fix=original.suggested_fix,
        details={
            "batch_step_index": index,
            "batch_step_tool": tool,
            "combined_with_step_indices": prior_step_indices,
            "underlying_constraint_id": original.constraint_id,
            "underlying_tier": original.tier,
            "underlying_details": original.details,
        },
    )


def _attribute_finding_to_step(finding: Finding, index: int, tool: str) -> Finding:
    """Per-step preflight passthrough (task requirement #1's second half):
    keep the ORIGINAL constraint id/tier/messages (so the underlying
    semantics stay exactly what a standalone call to that tool would show —
    an evaluator grepping for e.g. ``object_not_found`` still finds it), but
    prefix each language's message with a step marker and record the step
    index/tool in ``details`` for programmatic attribution."""
    prefixed = {
        "ko": f"[{index}단계: {tool}] {finding.messages.get('ko', '')}",
        "en": f"[Step {index}: {tool}] {finding.messages.get('en', '')}",
        "ja": f"[{index}段階: {tool}] {finding.messages.get('ja', '')}",
    }
    return dataclasses.replace(
        finding,
        messages=prefixed,
        details={**finding.details, "batch_step_index": index, "batch_step_tool": tool},
    )


# ---------------------------------------------------------------------------
# Plan overview (always-present, when the plan is structurally valid):
# old -> new per step, ridden on the approval card's warnings list.
# ---------------------------------------------------------------------------


def _describe_step(step: dict[str, Any], original_model: dict[str, Any]) -> str:
    target = resolve_write_target(step)
    if target is None:
        return f"{step['tool']}({step['params']})"
    id_field = _SHADOW_OBJECT_ID_FIELDS.get(target["object_class"])
    obj: dict[str, Any] = {}
    if id_field:
        obj = next(
            (o for o in original_model.get(target["object_class"], []) if str(o.get(id_field, "")) == target["target_id"]),
            {},
        ) or {}
    patch = _patch_for_step(step, obj)
    if not patch:
        return f"{step['tool']} ({target['object_class']}#{target['target_id']})"
    changes = ", ".join(f"{field}: {obj.get(field)!r} -> {new_value!r}" for field, new_value in patch.items())
    return f"{step['tool']} ({target['object_class']}#{target['target_id']}): {changes}"


def _plan_overview_finding(steps: list[dict[str, Any]], original_model: dict[str, Any]) -> Finding:
    lines = [f"{index}. {_describe_step(step, original_model)}" for index, step in enumerate(steps, start=1)]
    overview = "\n".join(lines)
    return Finding(
        constraint_id="apply_edit_plan_summary",
        tier="warn",
        phase="preflight",
        messages={
            "ko": f"편집 계획 ({len(steps)}단계):\n{overview}",
            "en": f"Edit plan ({len(steps)} steps):\n{overview}",
            "ja": f"編集計画 ({len(steps)}段階):\n{overview}",
        },
        suggested_fix=None,
        details={"step_count": len(steps)},
    )


# ---------------------------------------------------------------------------
# Top-level entry point, called from constraints.py's registered
# `apply_edit_plan_validation` check.
# ---------------------------------------------------------------------------


def validate_plan(
    action: dict[str, Any],
    shadow_model: dict[str, Any],
    *,
    registry: ToolRegistry | None = None,
) -> list[Finding]:
    """Every finding ``apply_edit_plan``'s single preflight pass produces --
    refuse-tier structural/per-step/collision/combined-effect findings, PLUS
    an always-present warn-tier plan overview (when the plan is structurally
    valid enough to describe). See the module docstring for the full design.
    """
    target_registry = registry if registry is not None else default_registry
    raw_steps = action.get("params", {}).get("steps")
    steps, findings = _validate_steps(raw_steps, target_registry)
    if not steps:
        return findings  # structurally broken -- nothing else is safe to check

    original_model = copy.deepcopy(shadow_model)
    patched_model = copy.deepcopy(shadow_model)
    per_step_original: list[list[Finding]] = []

    for index, step in enumerate(steps, start=1):
        step_action = {"tool": step["tool"], "params": step["params"]}
        step_findings = constraints.run_checks("preflight", step_action, original_model, {})
        per_step_original.append(step_findings)
        findings.extend(_attribute_finding_to_step(f, index, step["tool"]) for f in step_findings)

    findings.extend(_find_target_collisions(steps))

    for index, step in enumerate(steps, start=1):
        step_action = {"tool": step["tool"], "params": step["params"]}
        if index > 1:
            patched_findings = constraints.run_checks("preflight", step_action, patched_model, {})
            original_ids = {f.constraint_id for f in per_step_original[index - 1]}
            new_findings = [f for f in patched_findings if f.constraint_id not in original_ids]
            if new_findings:
                prior_indices = list(range(1, index))
                findings.extend(
                    _combined_effect_finding(f, index, step["tool"], prior_indices) for f in new_findings
                )
        _apply_patch(patched_model, step_action)

    findings.append(_plan_overview_finding(steps, original_model))
    return findings


__all__ = [
    "BATCH_STEP_BLOCKLIST",
    "MAX_STEPS",
    "MIN_STEPS",
    "BatchAbortedError",
    "resolve_write_target",
    "reverted_targets_from_diff",
    "validate_plan",
    "values_differ",
]

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
    "set_vehicle_input_volume": {
        "object_class": "vehicle_inputs",
        "id_param": "input_no",
        "value_field": "volume",
    },
    "set_signal_cycle": {
        "object_class": "signals",
        "id_param": "sc_no",
        "value_field": "cycle_time",
    },
    "set_signal_offset": {
        "object_class": "signals",
        "id_param": "sc_no",
        "value_field": "offset",
    },
}


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
    exempt_field: tuple[str, str] | None,
) -> list[dict[str, Any]]:
    """Structural diff of one shadow-model object list, id-keyed.

    ``exempt_field`` is ``(object_id, field_name)`` — the one (object,
    field) pair allowed to differ without becoming evidence (the write
    action's own intended target). Every other addition, removal, or
    field-level change becomes one evidence entry.
    """
    before_by_id = _index_by_id(before, id_field)
    after_by_id = _index_by_id(after, id_field)
    evidence: list[dict[str, Any]] = []

    for obj_id in sorted(set(before_by_id) - set(after_by_id)):
        evidence.append({"object_class": object_class, "object_id": obj_id, "change": "removed"})
    for obj_id in sorted(set(after_by_id) - set(before_by_id)):
        evidence.append({"object_class": object_class, "object_id": obj_id, "change": "added"})
    for obj_id in sorted(set(before_by_id) & set(after_by_id)):
        before_obj, after_obj = before_by_id[obj_id], after_by_id[obj_id]
        for field_name in sorted(set(before_obj) | set(after_obj)):
            if exempt_field is not None and exempt_field == (obj_id, field_name):
                continue
            before_value, after_value = before_obj.get(field_name), after_obj.get(field_name)
            if before_value != after_value:
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


def _nothing_else_moved_diff(
    action: dict[str, Any], before_model: dict[str, Any], after_model: dict[str, Any]
) -> list[dict[str, Any]]:
    spec = _WRITE_TARGET_FIELD_SPEC.get(action.get("tool", ""))
    exemptions: dict[str, tuple[str, str]] = {}
    if spec is not None:
        target_id = str(action.get("params", {}).get(spec["id_param"], ""))
        exemptions[spec["object_class"]] = (target_id, spec["value_field"])

    evidence: list[dict[str, Any]] = []
    for object_class, id_field in _SHADOW_OBJECT_ID_FIELDS.items():
        evidence.extend(
            _diff_object_list(
                object_class,
                id_field,
                before_model.get(object_class, []),
                after_model.get(object_class, []),
                exemptions.get(object_class),
            )
        )
    return evidence


def _values_differ(written: Any, saved: Any) -> bool:
    """Same comparison semantics as constraint #6's value-compare: float
    tolerance for numeric pairs (bool excluded — it is an int subclass but
    equality-comparing True to 1.0 numerically would be nonsense here),
    plain inequality otherwise."""
    if (
        isinstance(written, (int, float))
        and isinstance(saved, (int, float))
        and not isinstance(written, bool)
        and not isinstance(saved, bool)
    ):
        return abs(float(written) - float(saved)) > 1e-9
    return written != saved


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
    """
    spec = _WRITE_TARGET_FIELD_SPEC.get(action.get("tool", ""))
    if spec is None or "written_value" not in com_result:
        return None

    target_id = str(action.get("params", {}).get(spec["id_param"], ""))
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

    saved_value = target.get(spec["value_field"])
    if saved_value is None:
        return None

    written_value = com_result["written_value"]
    if not _values_differ(written_value, saved_value):
        return None

    field = spec["value_field"]
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

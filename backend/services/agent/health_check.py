"""HealthCheckRunner — "모델 건강검진" (model health-check), a one-click
integrity report that runs the guardrail constraint catalog against a parsed
.inpx with NO Vissim/COM and no proposed action at all.

This productizes constraints.py + guardrails.py's validation layer (the
product thesis: "Vissim fails silently; the validation layer is the
product") as a standalone feature, independent of any agent turn, and doubles
as paper material (docs/phase2/04-EVAL-PLAN.md) — a reproducible, offline,
machine-readable snapshot of what a network's static configuration is already
carrying wrong before anyone edits or runs anything.

Design: opt-in, not try-everything
-----------------------------------
Most of constraints.py's ~58 constraints are action-gated (need a proposed
edit's ``action["params"]`` to know which object a write would target) or
run-gated (need ``extra["run_result"]``/``extra["written_value"]`` a real COM
write/run produces) — neither exists here. Rather than guessing which checks
"probably" tolerate an empty action (fragile, and silently wrong the moment a
check's shape changes), every constraint this module surfaces opts in
EXPLICITLY via ``constraints.register_dataset_check`` — see that function's
own docstring in constraints.py. This module owns the "how do you turn one
action-shaped check into a whole-network scan" glue; constraints.py owns the
check logic itself (tier, phase, tri-lingual messages) so nothing here
duplicates a rule constraints.py already encodes and tests.

Two ways to reuse a check without duplicating its logic:
  1. **Direct** (``_scan_direct``): a handful of constraints already read
     ONLY the shadow model (no ``action``/``extra`` at all) — calling
     ``constraints.run_checks(phase, {}, shadow_model, {})`` once and
     filtering to the ids this scan owns is the whole job.
  2. **Synthesized per-object** (``_scan_vehicle_inputs``,
     ``_scan_signals``, ``_scan_signal_program_splits``): the check was
     written to validate ONE proposed action's target; scanning the whole
     network means calling it once per real object, with a synthesized
     action built from that object's OWN CURRENT value (e.g. "as if a write
     had just set this vehicle input's volume to what it already is") — the
     exact same check function, tier, and message runs either way, so a
     value that's already invalid TODAY is caught, not just a proposed
     change to one.

Zero behavior change to the edit-time path: guardrails.py's three
GuardrailEngine methods only ever call ``run_checks("preflight"/"postwrite"/
"postrun", ...)`` — nothing in this module is on that call path, and the
health-check-only constraints (``evaluation_config_disarmed``,
``vehicle_input_cont_volume_mismatch``, ``des_speed_distribution_orphan_
reference``) are registered with ``phase="dataset"``, which none of those
three calls ever requests.

Coverage honesty: ``constraints.get_edit_time_only_constraints()`` is
DERIVED from the same registry this module scans — every constraint NOT
reachable through a dataset-check registration lands in the report's
``edit_time_only`` list automatically, so the report's coverage claim can
never silently drift from the catalog as constraints.py grows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.agent import constraints
from services.agent import tools_network

# Live-bug fix precedent (2026-07-27, constraints.py's own _capped_join
# docstring): a hit list whose size tracks NETWORK size flooded a message
# once already. This report is exactly the kind of artifact that might get
# piped straight into a paper table or a UI list with no cap of its own, so
# BOTH axes are bounded here independently of constraints.py's own per-
# message capping: how many Findings render per constraint id, and how long
# any single Finding's own `details` payload can run.
_MAX_FINDINGS_PER_CONSTRAINT = 20
_MAX_DETAILS_LIST_LEN = 20

_TIER_TO_SEVERITY = {"refuse": "issue", "warn": "warning"}

# Short, localized titles for every constraint this module can actually
# surface as a dataset-wide finding (health_check-facing UI copy — separate
# from constraints.py's own longer message_ko/en/ja templates, which stay
# the single source of truth for the FULL explanation).
_TITLES: dict[str, dict[str, str]] = {
    "conflict_area_data_unavailable": {
        "ko": "Passive 충돌구간", "en": "Passive conflict areas", "ja": "Passiveな錯綜区間",
    },
    "route_integrity_unverifiable": {
        "ko": "단절된 경로", "en": "Disconnected routes", "ja": "切断された経路",
    },
    "volume_out_of_range": {
        "ko": "차량 입력 볼륨 범위 초과", "en": "Vehicle input volume out of range",
        "ja": "車両インプット交通量が範囲外",
    },
    "latent_demand_overflow": {
        "ko": "잠재 수요 (용량 초과)", "en": "Latent demand (over capacity)", "ja": "潜在需要(容量超過)",
    },
    "cycle_below_intergreen_sum": {
        "ko": "주기가 최소 인터그린 합 미만", "en": "Cycle below minimum intergreen sum",
        "ja": "サイクルが最小インターグリーン合計未満",
    },
    "ring_barrier_splits_sum_mismatch": {
        "ko": "링-바리어 스플릿 합계 불일치", "en": "Ring-barrier splits sum mismatch",
        "ja": "リングバリアスプリット合計不一致",
    },
    "conflicting_greens_in_signal_plan": {
        "ko": "상충 이동류 동시 녹색", "en": "Conflicting greens in signal plan",
        "ja": "錯綜動線への同時青",
    },
    "green_split_outside_cycle": {
        "ko": "녹색시간이 주기를 벗어남", "en": "Green split outside cycle", "ja": "青時間がサイクル範囲外",
    },
    "green_split_below_min_green": {
        "ko": "녹색시간이 최소 녹색시간 미만", "en": "Green split below minimum green",
        "ja": "青時間が最小青時間未満",
    },
    "units_ambiguous_on_write": {
        "ko": "단위 설정 불완전", "en": "Ambiguous unit settings", "ja": "単位設定が不完全",
    },
    "stale_route_files_after_edit": {
        "ko": "오래된 동적배정 경로 파일", "en": "Stale dynamic-assignment route files",
        "ja": "古い動的配分経路ファイル",
    },
    "evaluation_config_disarmed": {
        "ko": "평가(evaluation) 비활성", "en": "Evaluation not armed", "ja": "評価(evaluation)が無効",
    },
    "vehicle_input_cont_volume_mismatch": {
        "ko": "연속(Cont) 구간 볼륨 불일치", "en": "Continuous-interval volume mismatch",
        "ja": "連続区間(Cont)のボリューム不一致",
    },
    "des_speed_distribution_orphan_reference": {
        "ko": "희망속도분포 참조 끊김", "en": "Orphaned desired-speed distribution reference",
        "ja": "希望速度分布の参照切れ",
    },
}

# Details keys that plausibly identify the offending object(s) — best-effort
# extraction into `object_refs` so the report/UI can link a finding back to
# a concrete network object without every scan having to build that list by
# hand. Order matters only for which key wins when several are present.
_OBJECT_REF_DETAIL_KEYS = (
    "sc_no", "input_no", "link_id", "no", "decision_no", "route_no",
    "composition_no", "veh_type_no",
)


# ---------------------------------------------------------------------------
# Scan functions — each registered onto constraints.py's dataset-check hook
# at import time (mirrors tools_network.py/tools_edit.py's own
# "importing this module registers its X onto the shared registry" pattern).
# ---------------------------------------------------------------------------


def _scan_direct(shadow_model: dict[str, Any], phase: str, ids: frozenset[str]) -> list[constraints.Finding]:
    """Run every ``phase``-registered constraint once, with an empty
    (no-op) action — correct for the handful of constraints that already
    read ONLY the shadow model. Filters to ``ids`` so a tool-agnostic
    (``applies_to=["*"]``) constraint this scan did NOT opt in can never
    leak into the result even if it happened to also tolerate an empty
    action."""
    action: dict[str, Any] = {"tool": "__health_check__", "params": {}}
    findings = constraints.run_checks(phase, action, shadow_model, {})
    return [f for f in findings if f.constraint_id in ids]


def _scan_postwrite_direct(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    return _scan_direct(
        shadow_model,
        "postwrite",
        frozenset({"conflict_area_data_unavailable", "route_integrity_unverifiable", "stale_route_files_after_edit"}),
    )


def _scan_preflight_direct(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    return _scan_direct(shadow_model, "preflight", frozenset({"units_ambiguous_on_write"}))


def _scan_dataset_phase(shadow_model: dict[str, Any], ids: frozenset[str]) -> list[constraints.Finding]:
    """The three brand-new phase="dataset" constraints — run_checks() itself
    never dispatches phase="dataset" from any real edit-time call, but
    nothing stops US from asking for it directly here, which is exactly the
    point of that phase existing."""
    action: dict[str, Any] = {"tool": "__health_check__", "params": {}}
    findings = constraints.run_checks("dataset", action, shadow_model, {})
    return [f for f in findings if f.constraint_id in ids]


def _scan_evaluation_config(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    return _scan_dataset_phase(shadow_model, frozenset({"evaluation_config_disarmed"}))


def _scan_vehicle_input_cont_chains(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    return _scan_dataset_phase(shadow_model, frozenset({"vehicle_input_cont_volume_mismatch"}))


def _scan_des_speed_orphans(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    return _scan_dataset_phase(shadow_model, frozenset({"des_speed_distribution_orphan_reference"}))


_VEHICLE_INPUT_SCAN_IDS = frozenset({"volume_out_of_range", "latent_demand_overflow"})


def _scan_vehicle_inputs(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    """Every vehicle input's OWN current volume, checked as if a write had
    just proposed that exact value — catches an input that is ALREADY out
    of the sanity range or ALREADY overflowing its link's rough capacity,
    not just a proposed change to one."""
    findings: list[constraints.Finding] = []
    for vi in shadow_model.get("vehicle_inputs", []):
        volume = vi.get("volume")
        if volume is None:
            continue
        action = {
            "tool": "set_vehicle_input_volume",
            "params": {"input_no": vi.get("no", ""), "volume": volume},
        }
        for f in constraints.run_checks("preflight", action, shadow_model, {}):
            if f.constraint_id in _VEHICLE_INPUT_SCAN_IDS:
                findings.append(f)
    return findings


_SIGNAL_SCAN_IDS = frozenset(
    {"cycle_below_intergreen_sum", "ring_barrier_splits_sum_mismatch", "conflicting_greens_in_signal_plan"}
)


def _scan_signals(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    """Every signal controller's OWN current cycle time, checked as if a
    write had just proposed that exact value (a no-op the underlying checks
    correctly treat as "not actually changing anything" for the
    corridor-sync check, which this scan deliberately does NOT opt into —
    that one is inherently about a PROPOSED change, not a static fact) plus
    the controller's own currently-configured phase plan
    (conflicting_greens_in_signal_plan is fully real/live, not a
    CATALOG-GAP)."""
    findings: list[constraints.Finding] = []
    for sig in shadow_model.get("signals", []):
        cycle = sig.get("cycle_time")
        if cycle is None:
            continue
        action = {
            "tool": "set_signal_cycle",
            "params": {"sc_no": sig.get("sc_no", ""), "cycle": cycle},
        }
        for f in constraints.run_checks("preflight", action, shadow_model, {}):
            if f.constraint_id in _SIGNAL_SCAN_IDS:
                findings.append(f)
    return findings


_GREEN_SPLIT_SCAN_IDS = frozenset({"green_split_outside_cycle", "green_split_below_min_green"})


def _scan_signal_program_splits(shadow_model: dict[str, Any]) -> list[constraints.Finding]:
    """Every (controller, program, signal group)'s OWN current green
    window, checked as if a write had just proposed that exact window — a
    unit ⑨ addition (v2a): catches a green window that is already outside
    its own cycle or already shorter than its own signal group's minimum
    green, not just a proposed edit to one."""
    findings: list[constraints.Finding] = []
    for split in shadow_model.get("signal_program_splits", []):
        window = split.get("green_window")
        if not window:
            continue
        action = {
            "tool": "set_signal_green_split",
            "params": {
                "sc_no": split.get("sc_no", ""),
                "program_no": split.get("program_no", ""),
                "sg_no": split.get("sg_no", ""),
                "green_start_s": window.get("green_start_s"),
                "green_end_s": window.get("green_end_s"),
            },
        }
        for f in constraints.run_checks("preflight", action, shadow_model, {}):
            if f.constraint_id in _GREEN_SPLIT_SCAN_IDS:
                findings.append(f)
    return findings


def _register_dataset_checks() -> None:
    """Idempotent: constraints.register_dataset_check re-appends on every
    call, so this must run exactly once per process — guarded by the
    ``_registered`` flag below, called at import time (mirrors tools_
    network.py/tools_edit.py's own "importing this module registers its
    tools" pattern), never re-invoked by HealthCheckRunner itself."""
    constraints.register_dataset_check(
        ["conflict_area_data_unavailable", "route_integrity_unverifiable", "stale_route_files_after_edit"],
        _scan_postwrite_direct,
    )
    constraints.register_dataset_check(["units_ambiguous_on_write"], _scan_preflight_direct)
    constraints.register_dataset_check(["evaluation_config_disarmed"], _scan_evaluation_config)
    constraints.register_dataset_check(
        ["vehicle_input_cont_volume_mismatch"], _scan_vehicle_input_cont_chains
    )
    constraints.register_dataset_check(
        ["des_speed_distribution_orphan_reference"], _scan_des_speed_orphans
    )
    constraints.register_dataset_check(["volume_out_of_range", "latent_demand_overflow"], _scan_vehicle_inputs)
    constraints.register_dataset_check(
        ["cycle_below_intergreen_sum", "ring_barrier_splits_sum_mismatch", "conflicting_greens_in_signal_plan"],
        _scan_signals,
    )
    constraints.register_dataset_check(
        ["green_split_outside_cycle", "green_split_below_min_green"], _scan_signal_program_splits
    )


_registered = False


def _ensure_registered() -> None:
    global _registered
    if not _registered:
        _register_dataset_checks()
        _registered = True


_ensure_registered()


# ---------------------------------------------------------------------------
# Report shaping
# ---------------------------------------------------------------------------


def _bound_details(details: dict[str, Any]) -> dict[str, Any]:
    """Cap any list-valued entry in ``details`` at
    ``_MAX_DETAILS_LIST_LEN`` — the machine-readable twin of constraints.py's
    own message-level ``_capped_join`` (2026-07-27 lesson): a hit list that
    scales with network size must never flood a payload, human-facing
    message OR machine-readable details alike."""
    bounded: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, list) and len(value) > _MAX_DETAILS_LIST_LEN:
            bounded[key] = value[:_MAX_DETAILS_LIST_LEN]
            bounded[f"{key}_truncated_from"] = len(value)
        else:
            bounded[key] = value
    return bounded


def _object_refs(details: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in _OBJECT_REF_DETAIL_KEYS:
        value = details.get(key)
        if value is None or value == "":
            continue
        refs.append(str(value))
    return refs[:_MAX_DETAILS_LIST_LEN]


def _finding_to_dict(finding: constraints.Finding, title: dict[str, str]) -> dict[str, Any]:
    return {
        "constraint_id": finding.constraint_id,
        "severity": _TIER_TO_SEVERITY.get(finding.tier, "warning"),
        "tier": finding.tier,
        "title": title,
        "message": finding.messages,
        "details": _bound_details(finding.details),
        "object_refs": _object_refs(finding.details),
    }


def _edit_time_only_title(constraint_id: str) -> dict[str, str]:
    fallback = constraint_id.replace("_", " ")
    return _TITLES.get(constraint_id, {"ko": fallback, "en": fallback, "ja": fallback})


def _stats(shadow_model: dict[str, Any]) -> dict[str, int]:
    return {
        "links": len(shadow_model.get("links", [])),
        "nodes": len(shadow_model.get("nodes", [])),
        "vehicle_inputs": len(shadow_model.get("vehicle_inputs", [])),
        "signal_controllers": len(shadow_model.get("signals", [])),
        "conflict_areas": len(shadow_model.get("conflict_areas", [])),
        "routing_decisions": len(shadow_model.get("routing_decisions", [])),
        "routes": len(shadow_model.get("routes", [])),
        "vehicle_compositions": len(shadow_model.get("vehicle_compositions", [])),
        "des_speed_decisions": len(shadow_model.get("des_speed_decisions", [])),
    }


class HealthCheckRunner:
    """Parses one .inpx and evaluates every dataset-scope-capable constraint
    against it — no COM, no proposed action, no live Vissim."""

    def run(self, inpx_path: Path, *, dataset_meta: dict[str, Any] | None = None) -> dict[str, Any]:
        _ensure_registered()
        shadow_model = tools_network.build_shadow_model(Path(inpx_path))

        raw_findings: list[constraints.Finding] = []
        for _ids, scan in constraints.get_dataset_checks():
            raw_findings.extend(scan(shadow_model))

        # Deterministic ordering: refuse before warn, then constraint id —
        # independent of scan-registration order, and stable across runs
        # since Python's sort is stable and every tie-break key is explicit.
        tier_rank = {"refuse": 0, "warn": 1}
        raw_findings.sort(key=lambda f: (tier_rank.get(f.tier, 2), f.constraint_id))

        by_constraint: dict[str, list[constraints.Finding]] = {}
        for f in raw_findings:
            by_constraint.setdefault(f.constraint_id, []).append(f)

        findings: list[dict[str, Any]] = []
        findings_by_severity = {"issue": 0, "warning": 0, "info": 0}
        for constraint_id, group in by_constraint.items():
            title = _TITLES.get(constraint_id, _edit_time_only_title(constraint_id))
            severity = _TIER_TO_SEVERITY.get(group[0].tier, "warning")
            findings_by_severity[severity] = findings_by_severity.get(severity, 0) + len(group)
            shown = group[:_MAX_FINDINGS_PER_CONSTRAINT]
            for f in shown:
                findings.append(_finding_to_dict(f, title))
            remaining = len(group) - len(shown)
            if remaining > 0:
                findings.append(
                    {
                        "constraint_id": constraint_id,
                        "severity": severity,
                        "tier": group[0].tier,
                        "title": title,
                        "message": None,
                        "details": {},
                        "object_refs": [],
                        "truncated_additional_count": remaining,
                    }
                )

        edit_time_only = [
            {"constraint_id": c.id, "title": _edit_time_only_title(c.id), "tier": c.tier, "phase": c.phase}
            for c in constraints.get_edit_time_only_constraints()
        ]

        checked_ids = sorted(constraints.get_dataset_checkable_ids())

        dataset_meta = dataset_meta or {}
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "name": dataset_meta.get("filename") or Path(inpx_path).name,
                "revision": dataset_meta.get("revision"),
                "source": dataset_meta.get("source"),
            },
            "summary": {
                "checked_n": len(checked_ids),
                "edit_time_only_n": len(edit_time_only),
                "findings_by_severity": findings_by_severity,
            },
            "findings": findings,
            "edit_time_only": edit_time_only,
            "stats": _stats(shadow_model),
        }

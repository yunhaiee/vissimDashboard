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

Two interface gaps between the roadmap's aspirational text and what
``signal_parser.py`` actually exposes today are resolved here per the
roadmap's gap protocol; see ``docs/phase2/DECISIONS.md`` for both entries
(constraints #4 and #5 below).

This module is intentionally stdlib-only, like trace.py, and does not import
state.py or trace.py — it only consumes the shadow-model dict shape they
produce/agree on.
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
# constraint #1 (object_not_found) resolves against.
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
}

_OBJECT_CLASS_LABELS: dict[str, dict[str, str]] = {
    "vehicle_inputs": {"ko": "차량 입력", "en": "vehicle input", "ja": "車両インプット"},
    "signals": {"ko": "신호제어기", "en": "signal controller", "ja": "信号制御機"},
}


def _find_by_field(objects: list[dict[str, Any]], field_name: str, value: str) -> dict[str, Any] | None:
    for obj in objects:
        if str(obj.get(field_name, "")) == value:
            return obj
    return None


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

    ids = [str(obj.get(spec["id_field"], "")) for obj in objects]
    names = [str(obj.get(spec["name_field"], "")) for obj in objects if obj.get(spec["name_field"])]
    candidates = difflib.get_close_matches(target_id, ids + names, n=5)

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
        applies_to=["set_vehicle_input_volume", "set_signal_cycle", "set_signal_offset"],
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
    try:
        volume = float(raw_volume)
    except (TypeError, ValueError):
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
    try:
        volume = float(raw_volume)
    except (TypeError, ValueError):
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
# Gap decision (docs/phase2/DECISIONS.md): signal_parser.parse_signal_controllers
# exposes no controller-type attribute today. We read controller_type from the
# shadow-model signal entry (an agent-model extension M1.2 will populate);
# missing or "unknown" is treated as unsupported (conservative: an
# unverifiable signal edit is a silent-failure risk).
# ---------------------------------------------------------------------------

_SUPPORTED_CONTROLLER_TYPE = "fixed_time"


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
    if controller_type == _SUPPORTED_CONTROLLER_TYPE:
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

    try:
        cycle = float(raw_cycle)
        min_sum = float(raw_min_sum)
    except (TypeError, ValueError):
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
        message_ko="제안된 주기 {cycle}초가 신호제어기 '{sc_no}'의 최소 녹색시간+중간녹색시간 합계 "
        "{min_sum}초보다 짧습니다.",
        message_en="Proposed cycle {cycle}s is shorter than signal controller '{sc_no}''s "
        "minimum green + intergreen time sum of {min_sum}s.",
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
    try:
        deleted = int(raw_deleted)
    except (TypeError, ValueError):
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

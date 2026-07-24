"""YAML -> :class:`~eval.types.TaskSpec`, and the ``network:`` alias table.

Tasks live at ``backend/eval/tasks/*.yaml``. The schema (see each field's
docstring in ``types.py``):

```yaml
id: T004_vehicle_input_happy
category: edit                # read | edit | run | refusal | clarification
user_message: "1번 입력 교통량을 600으로 바꿔줘"
network: demo_two_intersection
max_turns: 6                  # optional, default 6
guardrails_disabled: false    # optional, default null (use the suite's flag)
scripted_turns:
  - tool: set_vehicle_input_volume
    params: {input_no: "1", volume: 600}
    summary: "교통량을 600으로 변경 제안"
  - tool: set_vehicle_input_volume   # re-issued after auto-approval
    params: {input_no: "1", volume: 600}
    summary: "교통량을 600으로 변경합니다"
  - tool: respond
    params: {answer: "..."}
    summary: "완료를 보고합니다"
expected:
  post_state:
    - object: "vehicle_input:1"
      attr: volume
      value: 600
  must_warn: []
  must_refuse: false
  answer_contains: []
notes: "free text"
```

This module only PARSES; it never imports anything from ``services.agent`` —
keeping the loader importable (and independently testable) without pulling
in the agent runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from eval.types import ExpectedOutcome, PostStateCheck, ScriptedTurn, TaskSpec

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASKS_DIR = Path(__file__).resolve().parent / "tasks"

# The M5.1 seed suite runs entirely against the synthetic demo network
# (CLAUDE.md: "Non-COM code must work against synthetic demo data ... never
# make it require live Vissim"). Additional aliases (PTV example networks,
# a future Korean case-study .inpx) are additive — see docs/phase2/
# 04-EVAL-PLAN.md §2's "Networks" note; M5.3/M5.4 extend this table, not the
# loader's parsing logic.
NETWORK_ALIASES: dict[str, Path] = {
    "demo_two_intersection": _REPO_ROOT / "data" / "practice.inpx",
}


class TaskSpecError(ValueError):
    """A task YAML file failed to parse into a valid :class:`TaskSpec`."""


def resolve_network_path(alias: str) -> Path:
    """The .inpx path for a task's ``network:`` alias.

    A bare path (containing a path separator or ending in ``.inpx``) is
    accepted verbatim too, resolved relative to the repo root — so a future
    task can point at a PTV example network without a table entry.
    """
    if alias in NETWORK_ALIASES:
        path = NETWORK_ALIASES[alias]
    else:
        candidate = Path(alias)
        path = candidate if candidate.is_absolute() else _REPO_ROOT / candidate
    if not path.exists():
        raise TaskSpecError(
            f"network alias {alias!r} resolves to {path!s}, which does not exist. "
            f"Known aliases: {sorted(NETWORK_ALIASES)}"
        )
    return path


def _require(data: dict[str, Any], key: str, *, task_path: Path) -> Any:
    if key not in data:
        raise TaskSpecError(f"{task_path}: missing required key {key!r}")
    return data[key]


def _parse_post_state(raw: list[dict[str, Any]], *, task_path: Path) -> tuple[PostStateCheck, ...]:
    checks: list[PostStateCheck] = []
    for i, entry in enumerate(raw):
        if "value" not in entry:
            raise TaskSpecError(f"{task_path}: expected.post_state[{i}] missing 'value'")
        source = entry.get("source", "shadow_model")
        if source not in ("shadow_model", "verification"):
            raise TaskSpecError(
                f"{task_path}: expected.post_state[{i}].source must be 'shadow_model' or "
                f"'verification', got {source!r}"
            )
        if source == "shadow_model" and not entry.get("object"):
            raise TaskSpecError(
                f"{task_path}: expected.post_state[{i}] source=shadow_model requires 'object'"
            )
        if source == "verification" and not entry.get("tool"):
            raise TaskSpecError(
                f"{task_path}: expected.post_state[{i}] source=verification requires 'tool'"
            )
        checks.append(
            PostStateCheck(
                value=entry["value"],
                source=source,
                object=entry.get("object"),
                tool=entry.get("tool"),
                attr=entry.get("attr", "value"),
                tolerance=float(entry.get("tolerance", 1e-6)),
            )
        )
    return tuple(checks)


def _parse_expected(raw: dict[str, Any] | None, *, task_path: Path) -> ExpectedOutcome:
    raw = raw or {}
    return ExpectedOutcome(
        post_state=_parse_post_state(raw.get("post_state") or [], task_path=task_path),
        must_warn=tuple(raw.get("must_warn") or []),
        must_not_warn=tuple(raw.get("must_not_warn") or []),
        must_refuse=bool(raw.get("must_refuse", False)),
        refuse_constraint_id=raw.get("refuse_constraint_id"),
        clarification_expected=bool(raw.get("clarification_expected", False)),
        answer_contains=tuple(raw.get("answer_contains") or []),
    )


def _parse_scripted_turns(
    raw: list[dict[str, Any]], *, task_path: Path
) -> tuple[ScriptedTurn, ...]:
    turns: list[ScriptedTurn] = []
    for i, entry in enumerate(raw):
        if "tool" not in entry:
            raise TaskSpecError(f"{task_path}: scripted_turns[{i}] missing 'tool'")
        turns.append(
            ScriptedTurn(
                tool=entry["tool"],
                params=dict(entry.get("params") or {}),
                summary=entry.get("summary", "진행합니다"),
            )
        )
    return tuple(turns)


def parse_task_spec(data: dict[str, Any], *, task_path: Path) -> TaskSpec:
    """Build one :class:`TaskSpec` from an already-YAML-parsed dict."""
    task_id = _require(data, "id", task_path=task_path)
    category = _require(data, "category", task_path=task_path)
    if category not in ("read", "edit", "run", "refusal", "clarification"):
        raise TaskSpecError(f"{task_path}: unknown category {category!r}")
    user_message = _require(data, "user_message", task_path=task_path)
    network = data.get("network", "demo_two_intersection")

    return TaskSpec(
        id=task_id,
        category=category,
        user_message=user_message,
        network=network,
        expected=_parse_expected(data.get("expected"), task_path=task_path),
        scripted_turns=_parse_scripted_turns(data.get("scripted_turns") or [], task_path=task_path),
        max_turns=int(data.get("max_turns", 6)),
        guardrails_disabled=data.get("guardrails_disabled"),
        fake_run_result=data.get("fake_run_result"),
        notes=data.get("notes", ""),
        source_path=str(task_path),
    )


def load_task_file(path: Path | str) -> TaskSpec:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise TaskSpecError(f"{path}: top-level YAML must be a mapping")
    return parse_task_spec(data, task_path=path)


def load_tasks(tasks_dir: Path | str | None = None) -> list[TaskSpec]:
    """Load every ``*.yaml`` task spec in ``tasks_dir`` (default
    ``backend/eval/tasks/``), sorted by filename for a deterministic order.

    Raises :class:`TaskSpecError` (with the offending file named) on any
    malformed task, and raises a plain ``ValueError`` if two files declare
    the same ``id`` — a duplicate id would silently double-count in the
    results table.
    """
    directory = Path(tasks_dir) if tasks_dir is not None else _TASKS_DIR
    specs: list[TaskSpec] = []
    seen_ids: dict[str, Path] = {}
    for path in sorted(directory.glob("*.yaml")):
        spec = load_task_file(path)
        if spec.id in seen_ids:
            raise ValueError(
                f"Duplicate task id {spec.id!r}: {seen_ids[spec.id]} and {path}"
            )
        seen_ids[spec.id] = path
        specs.append(spec)
    return specs


__all__ = [
    "NETWORK_ALIASES",
    "TaskSpecError",
    "load_task_file",
    "load_tasks",
    "parse_task_spec",
    "resolve_network_path",
]

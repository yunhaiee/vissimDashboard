"""Tests for eval/tasks_loader.py: the real M5.1 seed suite parses cleanly,
and malformed specs are rejected with a clear error rather than silently
producing a bad TaskSpec."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eval.tasks_loader import (
    NETWORK_ALIASES,
    TaskSpecError,
    load_task_file,
    load_tasks,
    parse_task_spec,
    resolve_network_path,
)

EXPECTED_CATEGORIES = {"read", "edit", "run", "refusal", "clarification"}


def test_load_tasks_finds_the_seed_suite():
    specs = load_tasks()
    assert len(specs) >= 15
    ids = [spec.id for spec in specs]
    assert len(ids) == len(set(ids)), "duplicate task ids"
    assert {spec.category for spec in specs} <= EXPECTED_CATEGORIES


def test_seed_suite_covers_every_required_category():
    specs = load_tasks()
    by_category: dict[str, int] = {}
    for spec in specs:
        by_category[spec.category] = by_category.get(spec.category, 0) + 1
    # M5.1 brief: reads, M2 edit verbs (happy + out-of-range + nonexistent),
    # run with short-run-warning, lane-closure refusal, ambiguous-object
    # clarification.
    assert by_category.get("read", 0) >= 1
    assert by_category.get("edit", 0) >= 1
    assert by_category.get("run", 0) >= 1
    assert by_category.get("refusal", 0) >= 1
    assert by_category.get("clarification", 0) >= 1


def test_network_alias_resolves_to_the_synthetic_demo_network():
    path = resolve_network_path("demo_two_intersection")
    assert path.name == "practice.inpx"
    assert path.exists()


def test_unknown_network_alias_raises_with_known_aliases_listed():
    with pytest.raises(TaskSpecError) as exc_info:
        resolve_network_path("no_such_network_alias")
    assert "demo_two_intersection" in str(exc_info.value)


def test_missing_required_key_raises_task_spec_error(tmp_path: Path):
    data = {"category": "read", "user_message": "hi"}  # missing 'id'
    with pytest.raises(TaskSpecError, match="id"):
        parse_task_spec(data, task_path=tmp_path / "bad.yaml")


def test_unknown_category_raises(tmp_path: Path):
    data = {"id": "x", "category": "not_a_real_category", "user_message": "hi"}
    with pytest.raises(TaskSpecError, match="category"):
        parse_task_spec(data, task_path=tmp_path / "bad.yaml")


def test_post_state_shadow_model_requires_object(tmp_path: Path):
    data = {
        "id": "x",
        "category": "edit",
        "user_message": "hi",
        "expected": {"post_state": [{"attr": "volume", "value": 1}]},
    }
    with pytest.raises(TaskSpecError, match="object"):
        parse_task_spec(data, task_path=tmp_path / "bad.yaml")


def test_post_state_verification_requires_tool(tmp_path: Path):
    data = {
        "id": "x",
        "category": "edit",
        "user_message": "hi",
        "expected": {"post_state": [{"source": "verification", "attr": "readback_value", "value": 1}]},
    }
    with pytest.raises(TaskSpecError, match="tool"):
        parse_task_spec(data, task_path=tmp_path / "bad.yaml")


def test_duplicate_task_id_raises(tmp_path: Path):
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            yaml.safe_dump({"id": "DUPLICATE", "category": "read", "user_message": "hi"}),
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="Duplicate task id"):
        load_tasks(tmp_path)


def test_load_task_file_round_trips_a_full_spec(tmp_path: Path):
    payload = {
        "id": "T999_smoke",
        "category": "edit",
        "user_message": "1번 입력 교통량을 500으로 바꿔줘",
        "network": "demo_two_intersection",
        "max_turns": 4,
        "guardrails_disabled": False,
        "scripted_turns": [
            {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 500}, "summary": "s1"},
            {"tool": "respond", "params": {"answer": "done"}, "summary": "s2"},
        ],
        "expected": {
            "post_state": [{"object": "vehicle_input:1", "attr": "volume", "value": 500}],
            "must_warn": [],
            "must_refuse": False,
            "answer_contains": ["500"],
        },
        "notes": "smoke",
    }
    path = tmp_path / "T999_smoke.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    spec = load_task_file(path)
    assert spec.id == "T999_smoke"
    assert spec.max_turns == 4
    assert spec.guardrails_disabled is False
    assert len(spec.scripted_turns) == 2
    assert spec.scripted_turns[0].tool == "set_vehicle_input_volume"
    assert spec.expected.post_state[0].value == 500
    assert spec.expected.answer_contains == ("500",)


def test_network_aliases_table_has_at_least_the_demo_network():
    assert "demo_two_intersection" in NETWORK_ALIASES

"""Tests for planner.py's history-observation rendering fix (live-demo bug,
docs/phase2/DECISIONS.md): the OLD `_observation_snippet` applied one flat
~300-char RAW CHARACTER SLICE to every history entry regardless of recency,
which silently chopped a real Beijing-sized envelope mid-object (e.g. 224
conflict areas, ~27.6KB, cut to its first ~3 items with no signal anything
was missing) -- so the model reported the tool's own data as "truncated" and
the planner re-issued the identical call up to the iteration cap trying to
see more of something that was never actually incomplete tool-side.

This file drives ``run_agent_turn`` (the public entry point) with a canned
``FakeStructuredProvider`` that records every ``generate_structured`` call's
``messages`` -- the SAME technique test_agent_selection_context.py already
uses to assert on the rendered per-iteration state block -- against
deterministic, self-contained fixture tools (isolated ``ToolRegistry``, never
``default_registry``, never a real .inpx/COM) so these assertions are not at
the mercy of whatever the demo network happens to contain today. Real
Beijing-network sizes were independently measured against
backend/tools/agent_obs_size_probe.py (see the DECISIONS.md draft entry) --
12 vehicle inputs (with per-interval volumes[]) at ~1.9KB, 224 conflict areas
at ~27.6KB -- and the synthetic fixtures below are shaped/sized to match.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict

import services.agent.planner as planner_module
from services.agent.orchestrator import run_agent_turn
from services.agent.toolkit import ToolRegistry, tool


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured``
    seam, recording every call's ``messages`` -- see
    test_agent_selection_context.py's identical fixture for the full
    rationale."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        self.calls.append({"messages": messages, "tools": tools, "schema": schema, "system": system})
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str = "확인합니다", **params: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


def _state_block_of(call: dict[str, Any]) -> str:
    return call["messages"][0]["content"]


def _history_line_for(state_block: str, tool_name: str) -> str:
    """The single `- <tool_name>: <rendered observation>` line for
    ``tool_name`` out of a rendered state block (there is exactly one per
    tool in these fixtures)."""
    for line in state_block.splitlines():
        if line.startswith(f"- {tool_name}:"):
            return line[len(f"- {tool_name}: "):]
    raise AssertionError(f"no history line for {tool_name!r} in state block:\n{state_block}")


# ---------------------------------------------------------------------------
# Fixture tools: synthetic, Beijing-shaped, deterministic
# ---------------------------------------------------------------------------


def _make_vehicle_inputs_payload(count: int) -> dict[str, Any]:
    """The exact shape tools_network.py's REAL get_vehicle_inputs returns
    (v2 unit ⑤: per-interval volumes[]) -- ~155 chars/entry, matching the
    real Beijing measurement (1888 chars / 12 inputs)."""
    vehicle_inputs = [
        {
            "no": str(i),
            "name": f"Vehicle Input {i}",
            "link": str(100 + i),
            "volume": 1000.0 + i,
            "interval_count": 1,
            "volumes": [{"interval_no": 1, "from_s": 0.0, "volume": 1000.0 + i, "continuous": False}],
        }
        for i in range(1, count + 1)
    ]
    return {"count": len(vehicle_inputs), "vehicle_inputs": vehicle_inputs}


def _make_conflict_areas_payload(count: int) -> dict[str, Any]:
    """The exact shape tools_network.py's REAL get_conflict_areas returns
    (v2 unit ④) -- ~120 chars/entry, matching the real Beijing measurement
    (27592 chars / 224 areas)."""
    conflict_areas = [
        {
            "no": str(i),
            "link_a": str(i),
            "link_b": str(i + 1000),
            "link_pair": [str(i), str(i + 1000)],
            "status": "PASSIVE",
            "name": f"Link {i} vs Link {i + 1000}",
        }
        for i in range(1, count + 1)
    ]
    return {"count": len(conflict_areas), "conflict_areas": conflict_areas}


def _register_fixture_tools(registry: ToolRegistry, *, vehicle_input_count: int, conflict_area_count: int) -> None:
    class NoParams(BaseModel):
        model_config = ConfigDict(extra="forbid")

    @tool(name="get_vehicle_inputs", tier="read", provider="inspect", params=NoParams, registry=registry)
    def _get_vehicle_inputs(params: NoParams) -> dict[str, Any]:
        """Synthetic Beijing-shaped vehicle inputs (test fixture)."""
        del params
        return _make_vehicle_inputs_payload(vehicle_input_count)

    @tool(name="get_conflict_areas", tier="read", provider="inspect", params=NoParams, registry=registry)
    def _get_conflict_areas(params: NoParams) -> dict[str, Any]:
        """Synthetic Beijing-shaped conflict areas (test fixture)."""
        del params
        return _make_conflict_areas_payload(conflict_area_count)

    @tool(name="get_network_overview", tier="read", provider="inspect", params=NoParams, registry=registry)
    def _get_network_overview(params: NoParams) -> dict[str, Any]:
        """A small, always-fits-easily read tool (test fixture)."""
        del params
        return {"links": {"count": 1, "items": [{"id": "1", "name": "x"}], "truncated": False}}


# ---------------------------------------------------------------------------
# FIX 1(a)+(c): the MOST RECENT history entry renders under a generous
# budget -- a 12-input Beijing-shaped envelope must render COMPLETE.
# ---------------------------------------------------------------------------


def test_recent_beijing_shaped_12_vehicle_inputs_renders_complete_under_default_budget(tmp_path):
    registry = ToolRegistry()
    _register_fixture_tools(registry, vehicle_input_count=12, conflict_area_count=0)

    provider = FakeStructuredProvider(
        [
            _action("get_vehicle_inputs", summary="차량 입력을 확인합니다"),
            _action("respond", summary="확인했습니다", answer="12개의 입력을 확인했습니다."),
        ]
    )

    run_agent_turn(
        "차량 입력을 전부 보여줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-vi-12",
        language="ko",
        registry=registry,
    )

    # The SECOND planner call is where get_vehicle_inputs' result is the
    # most-recent (and, here, only) history entry.
    assert len(provider.calls) == 2
    state_block = _state_block_of(provider.calls[1])
    rendered = _history_line_for(state_block, "get_vehicle_inputs")

    full_envelope = json.dumps(
        {
            "status": "ok",
            "error_type": None,
            "message": "",
            "artifacts": None,
            "suggested_fix": None,
            "verification": None,
            "data": _make_vehicle_inputs_payload(12),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Byte-identical to the full, untruncated envelope -- FIX 1(c)'s explicit
    # acceptance criterion.
    assert rendered == full_envelope
    assert json.loads(rendered)["data"]["count"] == 12
    for i in range(1, 13):
        assert f"Vehicle Input {i}" in rendered
    assert "표시" not in rendered  # no truncation marker anywhere


# ---------------------------------------------------------------------------
# FIX 1(b): truncation, when it IS needed, cuts on item boundaries with an
# explicit N/M marker -- a 224-item list legitimately won't fit even the
# generous recent budget.
# ---------------------------------------------------------------------------


def test_recent_224_conflict_areas_truncates_at_item_boundary_with_correct_marker(tmp_path):
    registry = ToolRegistry()
    _register_fixture_tools(registry, vehicle_input_count=0, conflict_area_count=224)

    provider = FakeStructuredProvider(
        [
            _action("get_conflict_areas", summary="충돌 구간을 확인합니다"),
            _action("respond", summary="확인했습니다", answer="충돌 구간을 확인했습니다."),
        ]
    )

    run_agent_turn(
        "충돌 구간을 전부 보여줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-ca-224",
        language="ko",
        registry=registry,
    )

    state_block = _state_block_of(provider.calls[1])
    rendered = _history_line_for(state_block, "get_conflict_areas")

    full_len = len(
        json.dumps(
            {
                "status": "ok",
                "error_type": None,
                "message": "",
                "artifacts": None,
                "suggested_fix": None,
                "verification": None,
                "data": _make_conflict_areas_payload(224),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    assert full_len > planner_module._DEFAULT_RECENT_OBS_BUDGET  # sanity: truncation IS required

    # Still parseable JSON -- never a mid-object character chop.
    parsed = json.loads(rendered)
    assert parsed["data"]["count"] == 224  # the count itself survives untouched

    items = parsed["data"]["conflict_areas"]
    *object_items, marker = items
    assert all(isinstance(item, dict) for item in object_items)
    assert isinstance(marker, str)

    match = re.search(r"표시 (\d+)/(\d+)", marker)
    assert match is not None, f"no N/M marker found in {marker!r}"
    shown, total = int(match.group(1)), int(match.group(2))
    assert total == 224
    assert shown == len(object_items)
    assert 0 < shown < 224  # a real, partial truncation -- not everything, not nothing

    assert len(rendered) <= planner_module._recent_observation_budget()


# ---------------------------------------------------------------------------
# FIX 1(a): recency-weighting itself -- an OLDER entry renders compact while
# the newest stays generous, for the exact same tool/payload shape.
# ---------------------------------------------------------------------------


def test_older_history_entry_renders_compact_while_newest_entry_stays_generous(tmp_path):
    registry = ToolRegistry()
    _register_fixture_tools(registry, vehicle_input_count=0, conflict_area_count=224)

    provider = FakeStructuredProvider(
        [
            _action("get_conflict_areas", summary="충돌 구간을 확인합니다"),
            _action("get_network_overview", summary="개요도 확인합니다"),
            _action("respond", summary="확인했습니다", answer="확인을 마쳤습니다."),
        ]
    )

    run_agent_turn(
        "충돌 구간과 개요를 확인해줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-recency",
        language="ko",
        registry=registry,
    )

    # Third call: history now has TWO entries -- get_conflict_areas (now the
    # OLDER entry) and get_network_overview (the MOST RECENT one).
    assert len(provider.calls) == 3
    state_block = _state_block_of(provider.calls[2])
    older_rendered = _history_line_for(state_block, "get_conflict_areas")

    # Compact budget: far fewer items than the recent-budget rendering from
    # the earlier test would have shown at the SAME position.
    assert len(older_rendered) <= planner_module._OBSERVATION_SNIPPET_CHARS
    # D2 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): this used to be
    # a loose "if it happens to be parseable JSON" check, tolerating the
    # zero-items bug (the 300-char snippet budget legitimately rendered ZERO
    # kept conflict areas for this exact fixture shape before the fix). Now
    # that the hard floor is in place, the render is ALWAYS valid JSON with
    # at least one real item.
    parsed = json.loads(older_rendered)
    items = parsed["data"]["conflict_areas"]
    shown = sum(1 for item in items if isinstance(item, dict))
    assert 0 < shown < 10  # a real, non-empty item -- nowhere near the generous-budget count


# ---------------------------------------------------------------------------
# D2 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): the zero-items
# bug. The old binary search always folded the truncation marker's own cost
# into every trial size, including the smallest one -- against a real
# envelope this could push a single, otherwise-comfortably-fitting item over
# a tight budget, so "the largest count that fits" search correctly, but
# uselessly, settled on ZERO items shown (verified against the live
# incident's actual trace/state.json: a 224-item get_conflict_areas envelope
# rendered at the OLDER-entry 300-char snippet budget kept 0/224 items).
# ---------------------------------------------------------------------------


def test_render_observation_224_conflict_areas_at_snippet_budget_keeps_at_least_one_item():
    """Direct, white-box regression test on ``_render_observation`` itself
    (not the full ``run_agent_turn`` machinery) at the exact budget
    (``_OBSERVATION_SNIPPET_CHARS`` = 300) and payload shape that produced
    the live-demo zero-items bug: kept_items must never be 0, and the
    "N/M 표시" marker must accurately reflect what was actually kept."""
    observation = {
        "status": "ok",
        "error_type": None,
        "message": "",
        "artifacts": None,
        "suggested_fix": None,
        "verification": None,
        "data": _make_conflict_areas_payload(224),
    }
    budget = planner_module._OBSERVATION_SNIPPET_CHARS

    rendered = planner_module._render_observation(observation, budget, "ko")

    assert len(rendered) <= budget
    parsed = json.loads(rendered)  # still valid, parseable JSON
    items = parsed["data"]["conflict_areas"]
    *object_items, marker = items
    kept_items = len(object_items)

    assert kept_items >= 1, "the zero-items bug: not a single complete item was kept"
    assert all(isinstance(item, dict) for item in object_items)

    match = re.search(r"표시 (\d+)/(\d+)", marker)
    assert match is not None, f"no N/M marker found in {marker!r}"
    shown, total = int(match.group(1)), int(match.group(2))
    assert total == 224
    assert shown == kept_items  # marker must reflect what was ACTUALLY kept


def test_render_observation_hard_floor_preserves_overhead_fields_when_no_item_fits():
    """The write-envelope shape from the live incident: a small
    ``guardrail_warnings`` list sitting next to non-list overhead
    (message/verification/artifacts) that alone is already close to the
    snippet budget, so not even ONE full guardrail-warning item fits
    alongside it. The old code fell all the way through to the generic
    "observation too large" marker, discarding ``verification.readback_value``
    -- exactly the field the model needed to confirm a write actually landed.
    The fix must keep those unrelated overhead fields intact."""
    observation = {
        "status": "ok",
        "error_type": None,
        "message": "Conflict area 3 (link 3 vs link 12) status set to AHASRIGHTOFWAY.",
        "artifacts": {"link_a": "3", "link_b": "12"},
        "suggested_fix": None,
        "verification": {"written_value": "AHASRIGHTOFWAY", "readback_value": "AHASRIGHTOFWAY"},
        "data": None,
        "guardrail_warnings": [
            {
                "constraint_id": "conflict_area_status_change_confirm",
                "message": (
                    "충돌구간 '3'은(는) 링크 3와(과) 링크 12 사이에 있으며, 현재 상태는 "
                    "PASSIVE입니다. AHASRIGHTOFWAY(으)로 변경을 제안합니다 — 충돌구간 번호는 "
                    "지오메트리 편집 시 바뀔 수 있으므로, 승인 전에 의도한 교차로가 맞는지 "
                    "확인해 주세요."
                ),
            },
            {
                "constraint_id": "conflict_area_data_unavailable",
                "message": (
                    "충돌구간(conflict area) 144개가 Passive(우선권 없음) 상태입니다: 3-21, "
                    "3-28, 3-43, 3-10020, 3-10021, 3-10022, 3-10027, 3-10029 외 136쌍."
                ),
            },
        ],
    }
    budget = planner_module._OBSERVATION_SNIPPET_CHARS
    full_len = len(json.dumps(observation, ensure_ascii=False, separators=(",", ":")))
    assert full_len > budget  # sanity: truncation IS required

    rendered = planner_module._render_observation(observation, budget, "ko")

    assert len(rendered) <= budget
    parsed = json.loads(rendered)  # still valid, parseable JSON -- never a bare fallback string
    # The whole point: fields that have NOTHING to do with the oversized
    # guardrail_warnings list must survive, even though the list itself may
    # end up empty at this budget.
    assert parsed["verification"] == {
        "written_value": "AHASRIGHTOFWAY",
        "readback_value": "AHASRIGHTOFWAY",
    }
    assert parsed["message"] == observation["message"]
    assert parsed["artifacts"] == observation["artifacts"]


# ---------------------------------------------------------------------------
# _recent_observation_budget(): env override + safe fallback (same
# discipline as services.agent.structured.ladder_ceiling_seconds).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Defect 1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-31): Tier 5, the
# message-preserving fallback. rollback_last_edit executed successfully and
# its OWN `message` already said what happened, but the envelope's OTHER
# fields (nothing to do with the message) pushed the whole thing past budget
# anyway -- and every list-truncation tier existing before this fix is
# powerless against a bulky NON-list field (or two independently large lists
# at different paths), so rendering fell all the way through to the generic
# "too large to display" marker, discarding the message along with
# everything else. The model then genuinely could not tell what had
# happened and re-requested the write it had just successfully rolled back.
# ---------------------------------------------------------------------------


def test_render_observation_preserves_message_when_no_list_truncation_can_help():
    """A bulky NON-list `artifacts` field (not a curve/list -- nothing any
    prior tier's list-shrinking logic can touch) dwarfs a short, informative
    `message` shaped exactly like rollback_last_edit's own D3-fixed,
    bounded message. Proven at BOTH of planner.py's real budget tiers: the
    generous most-recent-entry budget and the tight older-entry compact
    snippet budget."""
    message = (
        "Rolled back to version 3 (undid version 4: set_des_speed_distribution_points "
        "(des_speed_distributions#5.points: [30 item(s)] → [10 item(s)])). Restore "
        "verified byte-for-byte identical."
    )
    observation = {
        "status": "ok",
        "error_type": None,
        "message": message,
        # A big DICT, not a list -- _find_largest_list_path has nothing here
        # to shrink; the only list anywhere (verification.reverted_targets)
        # is already empty, so emptying it again changes nothing.
        "artifacts": {f"undone_params_field_{i}": "x" * 60 for i in range(400)},
        "suggested_fix": None,
        "verification": {"written_value": "abc123", "readback_value": "abc123", "reverted_targets": []},
        "data": None,
    }
    full_len = len(json.dumps(observation, ensure_ascii=False, separators=(",", ":")))
    # Sanity: this observation is oversized at BOTH real budget tiers, so
    # Tier 5 is genuinely exercised at each, not trivially skipped because
    # the whole thing already fit.
    assert full_len > planner_module._DEFAULT_RECENT_OBS_BUDGET
    assert full_len > planner_module._OBSERVATION_SNIPPET_CHARS

    for budget in (planner_module._OBSERVATION_SNIPPET_CHARS, planner_module._DEFAULT_RECENT_OBS_BUDGET):
        rendered = planner_module._render_observation(observation, budget, "ko")
        assert len(rendered) <= budget
        parsed = json.loads(rendered)  # still valid, parseable JSON
        assert "artifacts" not in parsed  # the bulky, unrelated field is dropped
        assert parsed["status"] == "ok"
        # The message survives whole (it comfortably fits both real budgets
        # once artifacts/verification/suggested_fix/data are dropped).
        assert parsed["message"] == message


def test_render_observation_shrinks_the_message_itself_when_even_the_minimal_shape_does_not_fit():
    """A budget so tight that even ``{status, error_type, message}`` alone
    does not fit -- the message TEXT itself is shrunk (never silently
    dropped), ellipsis-marked, so SOME of the outcome story always survives
    rather than none of it."""
    message = "Rolled back to version 3 (undid version 4: set_signal_cycle (signal_controllers#7.cycle_time: 60.0 → 90.0)). Restore verified byte-for-byte identical."
    observation = {
        "status": "ok",
        "error_type": None,
        "message": message,
        "artifacts": {"x": "y" * 500},
        "suggested_fix": None,
        "verification": None,
        "data": None,
    }
    budget = 60  # smaller than the minimal {status,error_type,message} shape's own overhead + full message

    rendered = planner_module._render_observation(observation, budget, "ko")

    assert len(rendered) <= budget
    parsed = json.loads(rendered)
    assert parsed["message"]  # never empty -- SOME of the story survives
    assert message.startswith(parsed["message"][:-1])  # a genuine prefix, ellipsis-marked
    assert parsed["message"].endswith("…")


def test_recent_observation_budget_env_override_and_safe_fallback(monkeypatch):
    monkeypatch.delenv(planner_module._RECENT_OBS_BUDGET_ENV, raising=False)
    assert planner_module._recent_observation_budget() == planner_module._DEFAULT_RECENT_OBS_BUDGET

    monkeypatch.setenv(planner_module._RECENT_OBS_BUDGET_ENV, "500")
    assert planner_module._recent_observation_budget() == 500

    monkeypatch.setenv(planner_module._RECENT_OBS_BUDGET_ENV, "not-a-number")
    assert planner_module._recent_observation_budget() == planner_module._DEFAULT_RECENT_OBS_BUDGET

    monkeypatch.setenv(planner_module._RECENT_OBS_BUDGET_ENV, "-10")
    assert planner_module._recent_observation_budget() == planner_module._DEFAULT_RECENT_OBS_BUDGET

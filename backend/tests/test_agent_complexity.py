"""Tests for M3.2's complexity-tier classifier (services/agent/complexity.py).

Pure unit tests -- no provider, no orchestrator, no filesystem. See
test_agent_orchestrator_complexity_tiers.py for the wired-in
orchestrator/planner behavior these classifications drive.
"""
from __future__ import annotations

import pytest

from services.agent import complexity


# ---------------------------------------------------------------------------
# classify_tier -- one case per tier, per language, matching the roadmap's
# own tier examples (docs/phase2/02-ROADMAP.md M3.2/§3 of the system prompt).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "이 네트워크에 뭐가 있어?",
        "107번 입력 교통량 알려줘",
        "what is in this network?",
        "show input 107's volume",
        "",
        "   ",
    ],
)
def test_classify_tier_simple(message):
    assert complexity.classify_tier(message) == "simple"


@pytest.mark.parametrize(
    "message",
    [
        "시나리오를 비교해서 지연 차이를 설명해줘",
        "compare the scenarios and explain the delay difference",
        "AシナリオとBシナリオを比較して違いを説明して",
    ],
)
def test_classify_tier_complex(message):
    assert complexity.classify_tier(message) == "complex"


@pytest.mark.parametrize(
    "message",
    [
        "노드 1 지연을 최소화해줘",
        "교차로 혼잡을 개선해줘",
        "reduce delay at this intersection",
        "please optimize the signal timing",
        "交差点の遅延を最小化して",
    ],
)
def test_classify_tier_agentic(message):
    assert complexity.classify_tier(message) == "agentic"


def test_classify_tier_agentic_outranks_complex_when_both_match():
    # Optimization intent phrased as a comparison must still route to the
    # strictest tier (complexity.py's module docstring: "agentic is checked
    # before complex").
    message = "설정 A와 설정 B를 비교해서 지연을 더 줄이는 쪽으로 최적화해줘"
    assert complexity.classify_tier(message) == "agentic"


def test_classify_tier_none_defaults_to_simple():
    assert complexity.classify_tier(None) == "simple"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Structural invariants the rest of the M3.2 wiring (planner.py,
# orchestrator.py) relies on.
# ---------------------------------------------------------------------------


def test_tiers_tuple_has_exactly_the_three_documented_tiers():
    assert complexity.TIERS == ("simple", "complex", "agentic")


def test_tier_guidance_has_nonempty_text_for_every_tier():
    for tier in complexity.TIERS:
        assert tier in complexity.TIER_GUIDANCE
        assert complexity.TIER_GUIDANCE[tier].strip()


def test_mandatory_preview_tiers_excludes_simple():
    assert "simple" not in complexity.MANDATORY_PREVIEW_TIERS
    assert complexity.MANDATORY_PREVIEW_TIERS == frozenset({"complex", "agentic"})

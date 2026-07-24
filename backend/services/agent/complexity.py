"""Complexity-tier classification (M3.2): simple | complex | agentic.

docs/phase2/02-ROADMAP.md M3.2 wires the three-tier routing that
``prompts/v1_system.md`` §3 has described since M1.3 but could not yet act
on ("full tier routing arrives in a later stage"). This module is the
"later stage": a single, deterministic, stdlib-only classifier plus the
per-tier prompt-guidance text ``planner.py`` injects into the system prompt.

**Why deterministic keyword matching, not an LLM call.** An extra
provider round-trip just to classify would cost latency/tokens on every
turn and would make the three-tier routing itself untestable with a canned
provider (the scripted acceptance tests need the SAME tier every time for
the SAME input, with zero network/API dependency). ``classify_tier`` reads
only ``state.goal`` (the user's original message) and is called once per
turn (``orchestrator.run_agent_turn``), so its result is stable across every
iteration of that turn -- there is no per-message drift mid-conversation
the way there would be if it re-ran on the compacted state block instead.

**Ko/en/ja keyword tables.** Mirrors the roadmap's own tier examples
verbatim: complex = "compare the scenarios and explain the delay
difference"; agentic = "reduce delay at this intersection". Order matters:
agentic is checked before complex, since an optimization request phrased
as a comparison ("reduce vs compare configs") should still route to the
strictest tier.

This module is intentionally decoupled from ``constraints.py``/
``guardrails.py`` -- it classifies REQUESTS, not actions, and never touches
a shadow model or COM.
"""

from __future__ import annotations

from typing import Literal

Tier = Literal["simple", "complex", "agentic"]

#: The three-tier vocabulary, in ascending strictness order. Any other
#: string is not a valid tier -- callers that persist/compare tiers should
#: validate against this rather than hand-rolling the three literals.
TIERS: tuple[Tier, ...] = ("simple", "complex", "agentic")

#: Tiers where the planner may not end the turn with `respond` as its very
#: first decision (§3's "mandatory preview" -- see planner.py's
#: ``_mandatory_preview_decision``): at least one grounding/read action must
#: run first, so the conclusion is visibly backed by real tool evidence
#: before it is drawn. "simple" is deliberately excluded -- a single
#: verified call answering directly is exactly what that tier is for.
MANDATORY_PREVIEW_TIERS: frozenset[str] = frozenset({"complex", "agentic"})

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

# Open-ended optimization intent ("reduce delay", "지연을 줄여줘",
# "최적화해줘"). Checked FIRST -- an optimization request always outranks a
# comparison request for strictness, even if it also uses comparison
# language ("두 설정 중 지연을 더 줄이는 쪽으로 최적화해줘").
_AGENTIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ko": ("최적화", "최소화", "최대화", "줄여", "줄이", "개선해", "낮춰", "낮추", "향상시켜"),
    "en": (
        "optimi",  # optimize / optimise / optimization
        "minimi",  # minimize / minimise
        "maximi",  # maximize / maximise
        "reduce ",
        "reduce the",
        "improve the",
        "lower the",
        "best config",
        "best setting",
        "least delay",
    ),
    "ja": ("最適化", "最小化", "最大化", "減らし", "改善して", "下げて"),
}

# Multi-step plan across objects/scenarios ("compare the scenarios and
# explain the delay difference", "A랑 B 비교해서 설명해줘").
_COMPLEX_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ko": ("비교", "그리고", "차이를 설명", "차이가 뭐", "왜 그런지 설명"),
    "en": ("compare", "difference between", "explain why", " and then ", " versus ", " vs "),
    "ja": ("比較", "そして", "違いを説明", "なぜそうなる"),
}


def _matches_any(text: str, table: dict[str, tuple[str, ...]]) -> bool:
    return any(keyword in text for keywords in table.values() for keyword in keywords)


def classify_tier(message: str) -> Tier:
    """Classify ``message`` (the turn's original goal text) into a tier.

    Deterministic and side-effect-free: the same input always yields the
    same tier, so a canned/scripted test can assert an exact tier without
    any provider call. Falls back to ``"simple"`` for empty input and for
    anything matching neither keyword table -- the safe, least-restrictive
    default (docs/phase2/00-EXECUTION-GUIDE.md §4's conservative-default
    doctrine applies to gating strictness, not to under-classifying safety
    checks: a "simple" misclassification never skips a guardrail, since
    every write/run still always pauses for approval regardless of tier).
    """
    text = (message or "").strip().lower()
    if not text:
        return "simple"
    if _matches_any(text, _AGENTIC_KEYWORDS):
        return "agentic"
    if _matches_any(text, _COMPLEX_KEYWORDS):
        return "complex"
    return "simple"


# ---------------------------------------------------------------------------
# Per-tier system-prompt guidance (planner.py injects into {TIER_GUIDANCE})
# ---------------------------------------------------------------------------

TIER_GUIDANCE: dict[Tier, str] = {
    "simple": (
        "### Tier: simple\n"
        "This request needs at most one or two verified calls. When a "
        "read-tier action's parameters are already fully supplied by the "
        "request, act on it immediately -- do not narrate a separate "
        "planning step first. A write/run action still always pauses for "
        "the user's explicit approval before it executes (§2/§5); that is "
        "unchanged by this tier. If you filled in any unstated parameter "
        "yourself (a default, an assumption), say so explicitly in the "
        "final answer -- never let an assumption pass silently as a fact.\n"
    ),
    "complex": (
        "### Tier: complex\n"
        "This request needs a multi-step plan across objects or scenarios. "
        "Reason step by step: gather the reads you need across every object "
        "or scenario the request touches before answering. You may NOT end "
        "the turn with `respond` as your very first action this turn -- at "
        "least one grounding read must run first (this is enforced "
        "automatically: an attempt to respond immediately is diverted into "
        "a grounding read instead), so the user always sees what you "
        "actually checked before your conclusion arrives. A write/run "
        "action still always pauses for the user's explicit approval "
        "(§2/§5); this tier adds a mandatory preview of your reasoning on "
        "top of that pause, it does not change the approval flow itself.\n"
    ),
    "agentic": (
        "### Tier: agentic\n"
        "This is open-ended optimization (e.g. \"reduce delay\"). This is "
        "the strictest tier:\n"
        "- Same mandatory preview as complex: you may not `respond` as your "
        "very first action this turn.\n"
        "- Every proposed write/run still always pauses for the user's "
        "explicit approval (§2/§5).\n"
        "- After any `run_simulation` call, treat its post-run findings "
        "(`guardrail_warnings` in the tool result -- e.g. "
        "`vehicles_deleted_in_run`, `results_missing_kpi_tables`) as "
        "mandatory evidence, not optional context.\n"
        "- CRITICAL: you may NEVER present a configuration as the answer or "
        "the best result when its reported delay/KPI improvement coexists "
        "with a fired constraint warning on that SAME run. A reported "
        "improvement next to a fired warning is not evidence of a real "
        "improvement -- it is a known Vissim silent-failure pattern (e.g. "
        "demand vanished via DiffusTm deletions, so delay looks better "
        "because load was understated, not because the network actually "
        "improved). If every option you evaluated has this problem, say so "
        "plainly instead of picking the least-bad one as if it were safe. "
        "(As a safety net, any accumulated warning is automatically "
        "appended to your final answer even if you forget to mention it -- "
        "but you must still never claim the improvement is clean.)\n"
    ),
}


__all__ = [
    "MANDATORY_PREVIEW_TIERS",
    "TIERS",
    "TIER_GUIDANCE",
    "Tier",
    "classify_tier",
]

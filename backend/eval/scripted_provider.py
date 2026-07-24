"""Provider adapters: the seam ``runner.py`` plugs a planner backend through.

Two implementations of the ``generate_structured(messages, tools, schema,
*, system, max_tokens) -> (action, meta)`` duck type ``planner.Planner``
consumes:

- :class:`ScriptedProvider` — the CI-safe default. Returns a task's
  ``scripted_turns`` in order, exactly like every existing
  ``FakeStructuredProvider`` test fixture in ``backend/tests/test_agent_
  orchestrator*.py`` (same shape, deliberately — this is that pattern
  promoted to a reusable, non-test module). Never touches the network or
  costs money; this is what every pytest run and every default ``runner.py``
  invocation uses.
- :func:`build_real_provider` — the ``--real-provider`` seam for a live
  evaluation run. Mirrors ``assistant_router._default_provider``'s
  Claude/Qwen selection without importing that module's unrelated
  ``OLLAMA_SYSTEM_PROMPT`` constant. **Never called from pytest** (the task
  brief: "never live Vissim or paid LLM calls in pytest") — only from
  ``runner.py``'s CLI path when ``--real-provider`` is explicitly passed.
"""

from __future__ import annotations

import os
from typing import Any

from eval.types import ScriptedTurn


class ScriptedProviderExhaustedError(AssertionError):
    """A task's ``scripted_turns`` queue ran out before the orchestrator
    stopped asking the planner for a decision — the task spec under-scripted
    the scenario (e.g. forgot the "re-issued after approval" duplicate
    entry documented on :class:`~eval.types.ScriptedTurn`)."""


class ScriptedProvider:
    """Replays a task's canned ``(tool, params, summary)`` sequence as the
    planner's ``generate_structured`` seam.

    One instance is used across EVERY ``run_agent_turn`` call for a given
    task (``runner.py`` reuses the same instance across the propose/approve
    round-trip) — the queue is a single FIFO shared across calls, which is
    exactly what makes the "write/run actions appear twice" contract on
    :class:`~eval.types.ScriptedTurn` work: round 1 consumes the propose
    entry and returns (approval pause); round 2 is a FRESH ``run_agent_turn``
    call (fresh planner, fresh ``AgentState``) that consumes the next queue
    entry — the re-issue.
    """

    def __init__(self, turns: tuple[ScriptedTurn, ...] | list[ScriptedTurn], *, task_id: str = "") -> None:
        self._queue: list[ScriptedTurn] = list(turns)
        self._task_id = task_id
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
        self.calls.append(
            {"messages": messages, "tools": tools, "schema": schema, "system": system}
        )
        if not self._queue:
            raise ScriptedProviderExhaustedError(
                f"Task {self._task_id!r}: scripted_turns exhausted but the orchestrator asked "
                "the planner for another decision. Add another entry (a write/run action that "
                "needs approval must appear twice — see ScriptedTurn's docstring)."
            )
        turn = self._queue.pop(0)
        params = dict(turn.params)
        params["summary"] = turn.summary
        action = {"tool": turn.tool, "params": params}
        meta = {"mode": "scripted", "fallback_rung": 0}
        return action, meta

    @property
    def remaining(self) -> int:
        """Turns never consumed — a non-zero value at the end of a task
        usually means the task over-scripted a path the orchestrator short-
        circuited (e.g. scripted a respond after a refusal that itself
        already ended the turn)."""
        return len(self._queue)


def default_scripted_provider_factory(spec: Any) -> ScriptedProvider:
    """The default ``provider_factory`` ``runner.py`` uses: one fresh
    :class:`ScriptedProvider` per task, built from ``spec.scripted_turns``."""
    return ScriptedProvider(spec.scripted_turns, task_id=spec.id)


def build_real_provider() -> Any:
    """The live-evaluation provider: a real ``generate_structured`` backend.

    Mirrors ``services.assistant_router._default_provider``'s selection
    (``ASSISTANT_PROVIDER=claude|qwen``, else Claude iff ``ANTHROPIC_API_KEY``
    is set, else local Qwen) without importing that function directly — it
    binds an unrelated system prompt (``OLLAMA_SYSTEM_PROMPT``) meant for the
    legacy regex-based assistant, not for the structured planner seam this
    package drives.

    Lazy imports throughout (this module must stay importable, and imported
    by pytest collection, even when neither ``anthropic`` nor the local Qwen
    stack is configured) — same discipline as ``claude_provider.py``'s own
    lazy ``import anthropic``.

    **Never call this from a pytest test.** It is the ``--real-provider``
    seam for a live, paid (Claude) or locally-hosted (Qwen) evaluation run
    only; ``runner.py``'s default path never reaches this function.
    """
    choice = os.getenv("ASSISTANT_PROVIDER", "").strip().lower()
    if choice != "qwen" and (choice == "claude" or os.getenv("ANTHROPIC_API_KEY", "").strip()):
        from services.claude_provider import ClaudeProvider

        return ClaudeProvider()
    from services.assistant_router import LocalQwenProvider

    return LocalQwenProvider()


__all__ = [
    "ScriptedProvider",
    "ScriptedProviderExhaustedError",
    "build_real_provider",
    "default_scripted_provider_factory",
]

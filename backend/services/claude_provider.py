"""Anthropic Claude API provider for the assistant.

Implements the same duck-typed contract as ``LocalQwenProvider``
(``status()`` and ``generate(prompt, on_token, think)``), so the router,
templates, and tests treat both interchangeably. Selection happens in
``assistant_router._default_provider()``; set ``ASSISTANT_PROVIDER=qwen``
to force the local model even when an API key is present.
"""
from __future__ import annotations

import os
from typing import Any, Callable

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"


class ClaudeProvider:
    def __init__(self, system_prompt: str | None = None, model: str | None = None) -> None:
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_CLAUDE_MODEL)
        self.system_prompt = system_prompt
        self._client: Any = None
        self._client_error: str | None = None

    def _get_client(self) -> Any:
        if self._client is None and self._client_error is None:
            try:
                import anthropic

                # Explicit bounds instead of the SDK defaults (600s timeout,
                # 2 retries ~= a 30-minute worst case): an agent turn holds a
                # live NDJSON stream, a session work dir, and possibly a COM
                # edit session while each planner call runs — a wedged HTTP
                # request must fail fast and fall down the existing
                # fallback/error ladder rather than hold all of that hostage.
                # Observed live 2026-07-23: a stalled call after a COM
                # LoadNet failure froze the whole approve stream.
                self._client = anthropic.Anthropic(timeout=90.0, max_retries=1)
            except Exception as exc:  # missing package or key resolution failure
                self._client_error = str(exc)
        return self._client

    def _api_key_present(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

    def status(self) -> dict[str, Any]:
        ready = self._api_key_present() and self._client_error is None
        if not self._api_key_present():
            state = "missing-key"
        elif self._client_error:
            state = "failed"
        else:
            state = "ready"
        return {
            "model": f"{self.model} (Claude API)",
            "mode": "claude-api",
            "state": state,
            "ready": ready,
            "worker_error": self._client_error,
            # Fields the Qwen-oriented status consumers may look for.
            "model_path": None,
            "model_path_exists": False,
            "endpoint_configured": True,
            "worker_available": ready,
            "worker_running": ready,
        }

    def generate(
        self,
        prompt: str,
        on_token: Callable[[str], None] | None = None,
        think: bool = False,
    ) -> tuple[str | None, dict[str, Any]]:
        if not self._api_key_present():
            return None, {"mode": "fallback", "error": "ANTHROPIC_API_KEY is not set."}
        client = self._get_client()
        if client is None:
            return None, {"mode": "fallback", "error": self._client_error or "Anthropic client unavailable."}

        max_tokens = int(os.getenv("ANTHROPIC_MAX_TOKENS", "2048"))
        if think:
            max_tokens = max(max_tokens, 3072)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.system_prompt:
            kwargs["system"] = self.system_prompt

        try:
            if on_token is not None:
                chunks: list[str] = []
                with client.messages.stream(**kwargs) as stream:
                    for text in stream.text_stream:
                        chunks.append(text)
                        on_token(text)
                answer = "".join(chunks).strip()
            else:
                response = client.messages.create(**kwargs)
                answer = "".join(
                    block.text for block in response.content if getattr(block, "type", "") == "text"
                ).strip()
        except Exception as exc:
            return None, {"mode": "fallback", "error": str(exc)}

        if not answer:
            return None, {"mode": "fallback", "error": "Claude returned an empty answer."}
        return answer, {"mode": "claude-api", "model": self.model, "think": think}

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Native tool-use structured generation: one action dict, or None + why.

        Uses Anthropic's native tool-use (``tool_choice: {"type": "any"}`` forces
        a ``tool_use`` block) with prompt caching on the system and tools blocks,
        per docs/phase2/02-ROADMAP.md M1.1. A tool_use block's params are first
        run through ``salvage_and_validate`` (repairs occasional XML-tool-syntax
        contamination, then validates) before being accepted. On a missing/
        invalid tool_use block, retries once with an appended assistant+user
        turn stating the validation error (``fallback_rung`` 1); giving up after
        that returns ``(None, meta)`` with the error (``fallback_rung`` 2). No
        streaming. See :mod:`services.agent.structured` for the shared
        salvage/validation/meta-building both providers use.
        """
        from services.agent.structured import build_meta, salvage_and_validate

        if not self._api_key_present():
            return None, build_meta(
                mode="fallback", fallback_rung=2, error="ANTHROPIC_API_KEY is not set."
            )
        client = self._get_client()
        if client is None:
            return None, build_meta(
                mode="fallback",
                fallback_rung=2,
                error=self._client_error or "Anthropic client unavailable.",
            )

        tokens = max_tokens or int(os.getenv("ANTHROPIC_MAX_TOKENS", "2048"))
        request_tools = [dict(t) for t in tools]
        if request_tools:
            request_tools[-1] = {**request_tools[-1], "cache_control": {"type": "ephemeral"}}

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": tokens,
            "tools": request_tools,
            "tool_choice": {"type": "any"},
            "messages": list(messages),
        }
        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

        last_error = "Claude did not return a valid action."
        for rung in (0, 1):
            try:
                response = client.messages.create(**kwargs)
            except Exception as exc:
                return None, build_meta(
                    mode="claude-api", fallback_rung=2, model=self.model, error=str(exc)
                )

            tool_use_block = next(
                (block for block in response.content if getattr(block, "type", "") == "tool_use"),
                None,
            )
            if tool_use_block is not None:
                raw_action = {"tool": tool_use_block.name, "params": tool_use_block.input}
                action, reason = salvage_and_validate(raw_action, tools, schema)
                if reason is None:
                    return action, build_meta(mode="claude-api", fallback_rung=rung, model=self.model)
                last_error = reason
            else:
                last_error = "Claude response contained no tool_use block."

            if rung == 0:
                retry_notice = (
                    f"That response was invalid: {last_error}. "
                    "Reply again with exactly one valid tool call."
                )
                if tool_use_block is not None:
                    # A tool_use block WAS present (its params just failed
                    # validation) -- the Messages API requires the very
                    # next user turn to carry a matching tool_result for
                    # every tool_use block id in the preceding assistant
                    # turn, or the request 400s once that pairing is
                    # enforced (docs/phase2/DECISIONS.md, "Deferred
                    # findings from the M0+M1 whole-branch review", first
                    # bullet). is_error=True marks the attempt rejected;
                    # `content` carries the validation reason so the model
                    # can actually fix it on retry.
                    retry_user_message: dict[str, Any] = {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_block.id,
                                "is_error": True,
                                "content": retry_notice,
                            }
                        ],
                    }
                else:
                    # No tool_use block at all (e.g. a plain-text reply) --
                    # there is no tool_use id to pair a tool_result against,
                    # so a plain-text nudge is the only valid shape here.
                    retry_user_message = {"role": "user", "content": retry_notice}

                kwargs["messages"] = kwargs["messages"] + [
                    {"role": "assistant", "content": response.content},
                    retry_user_message,
                ]

        return None, build_meta(mode="claude-api", fallback_rung=2, model=self.model, error=last_error)

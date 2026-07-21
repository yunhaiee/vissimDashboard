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

                self._client = anthropic.Anthropic()
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

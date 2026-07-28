"""Query router and local Qwen adapter for dashboard prompting."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
import sys
import threading
import time
import uuid
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable

from config import REPO_ROOT
from services.datasets import get_active_dataset, get_active_inpx_path, get_active_real_counts_excel
from services.network_selection import (
    element_selection_fields,
    resolve_selected_elements,
    resolve_selected_links,
)
from services.rag_documents import get_rag_status, search_rag
from services.validation import geh as compute_geh, load_real_counts
from services.results_reader import get_compare_summary, get_selected_node_details, list_scenarios
from services.simulation_runs import list_job_records
from services.vissim_tool import get_vissim_com_capabilities, get_vissim_status

logger = logging.getLogger(__name__)

DEFAULT_QWEN_MODEL_DIR = r"C:\Users\kaistys\models\Qwen3.5-9B"
DEFAULT_QWEN_PYTHON = r"C:\Users\kaistys\anaconda3\envs\Qwen\python.exe"
DEFAULT_MODEL_LABEL = "Qwen3.5-9B 4bit"
OLLAMA_SYSTEM_PROMPT = (
    "You are a VISSIM Web Dashboard assistant. "
    "Answer in the language requested in the user prompt. "
    "Use only the supplied dashboard context; if data is missing or estimated, say so briefly. "
    "Keep answers concise and operational, and return only the final answer."
)
DOC_CONTEXT_LIMIT = 5
JSON_CONTEXT_LIMIT = 9000
INTERNAL_ANSWER_TERMS = (
    "recent_errors",
    "comparison_summary",
    "selected_intersection_details",
    "selected_scenario_ids",
    "selected_node_ids",
    "selected_links",
    "selected_element",
    "selected_elements",
    "simulation_jobs",
    "vissim_com",
    "rag_chunks",
    "tool_results",
    "base_model_validation",
    "runtime",
    "fallback",
)
PROMPT_SIMULATION_DEFAULT_DURATION = 300
PROMPT_SIMULATION_DEFAULT_SEED = 42

ANSWER_FORMATS = {
    "capabilities": [
        ("\ud560 \uc218 \uc788\ub294 \uc77c", "Explain the assistant's practical VISSIM dashboard capabilities."),
        ("\ud544\uc694\ud55c \uc785\ub825", "State what files or selections make those capabilities work."),
        ("\uc81c\ud55c", "Mention safety and data availability limits briefly."),
    ],
    "diagnostic": [
        ("\uc9c4\ub2e8 \uc694\uc57d", "Start with the direct finding in one or two sentences."),
        ("\uadfc\uac70", "List only concrete dashboard or document evidence; avoid internal field names."),
        ("\ub2e4\uc74c \uc870\uce58", "Give one to three practical checks or fixes."),
    ],
    "settings": [
        ("\uc694\uc57d", "Summarize the requested setting change."),
        ("\uac00\ub2a5 \uc5ec\ubd80", "Say whether it can be executed now and why."),
        ("\ub2e4\uc74c \uc870\uce58", "Suggest the next concrete action."),
    ],
    "status": [
        ("\uc0c1\ud0dc \uc694\uc57d", "Summarize current run, dataset, or job state."),
        ("\ud655\uc778\ud560 \uc810", "List the next checks only when useful."),
    ],
    "general": [
        ("\ub2f5\ubcc0", "Answer the user question briefly."),
        ("\uadfc\uac70", "State only the relevant retrieved VISSIM document or dashboard support."),
        ("\ud655\uc778 \ud544\uc694", "Say what is not confirmed or what needs more data."),
    ],
}

ANSWER_FORMAT_HEADINGS_EN = {
    "capabilities": ["Capabilities", "Required input", "Limits"],
    "diagnostic": ["Diagnostic summary", "Evidence", "Next steps"],
    "settings": ["Summary", "Feasibility", "Next steps"],
    "status": ["Status summary", "Checks"],
    "general": ["Answer", "Evidence", "Needs confirmation"],
}

ANSWER_FORMAT_HEADINGS_JA = {
    "capabilities": ["できること", "必要な入力", "制限"],
    "diagnostic": ["診断の要約", "根拠", "次のアクション"],
    "settings": ["要約", "可否", "次のアクション"],
    "status": ["状態の要約", "確認事項"],
    "general": ["回答", "根拠", "確認が必要"],
}


@dataclass(frozen=True)
class AssistantRequestContext:
    message: str
    selected_scenario_ids: list[str]
    selected_node_ids: list[str]
    # P6 -- what the user clicked in the rendered network diagram, already
    # sanitized at the request boundary (routers/assistant.py's
    # _SelectionFieldsMixin). Both default to "nothing selected" so every
    # pre-P6 caller -- and every existing test -- is unaffected.
    selected_link_ids: list[str] = field(default_factory=list)
    selected_element: dict[str, Any] | None = None
    # Multi-select -- the FULL element selection (shift+click / shift+drag),
    # already merged with `selected_element` at the request boundary, so a
    # single-click request arrives here as a one-entry list AND on the legacy
    # field above. Defaults keep every pre-multi-select caller unaffected;
    # `selected_elements_omitted` is > 0 only when the client selected more
    # objects than services.network_selection.MAX_SELECTED_ELEMENTS.
    selected_elements: list[dict[str, Any]] = field(default_factory=list)
    selected_elements_omitted: int = 0
    allow_tool_execution: bool = False
    response_language: str = "auto"


class LocalQwenProvider:
    """Generate with Qwen through HTTP, in-process, or a persistent stdio worker."""

    def __init__(self, model_dir: str | None = None) -> None:
        self.model_dir = Path(model_dir or os.getenv("QWEN_MODEL_DIR", DEFAULT_QWEN_MODEL_DIR))
        self.python_path = Path(os.getenv("QWEN_PYTHON", DEFAULT_QWEN_PYTHON))
        self._lock = threading.Lock()
        self._worker: subprocess.Popen[str] | None = None
        self._messages: Queue[dict[str, Any]] = Queue()
        self._worker_error: str | None = None
        self._worker_info: dict[str, Any] | None = None
        self._worker_state = "standby"

    def status(self) -> dict[str, Any]:
        ollama_model = os.getenv("QWEN_OLLAMA_MODEL", "").strip()
        endpoint = os.getenv("QWEN_LOCAL_ENDPOINT", "").strip()
        in_process = os.getenv("QWEN_ENABLE_IN_PROCESS", "").strip() == "1"
        worker_running = self._worker is not None and self._worker.poll() is None
        worker_available = self.model_dir.exists() and self.python_path.exists()
        worker_loading = worker_running and self._worker_info is None
        if ollama_model:
            mode = "ollama"
            ready = True
            state = "ready"
        elif endpoint:
            mode = "http-endpoint"
            ready = self.model_dir.exists()
            state = "ready" if ready else "model-missing"
        elif in_process:
            mode = "in-process"
            ready = self.model_dir.exists()
            state = "ready" if ready else "model-missing"
        else:
            mode = "stdio-worker" if worker_available else "fallback"
            ready = worker_running and self._worker_info is not None
            if not worker_available:
                state = "offline"
            elif worker_loading:
                state = "loading"
            elif worker_running:
                state = self._worker_state if self._worker_state == "generating" else "ready"
            elif self._worker_error:
                state = "failed"
            else:
                state = "standby"
        return {
            "model": f"{ollama_model} (Ollama)" if ollama_model else DEFAULT_MODEL_LABEL,
            "model_path": str(self.model_dir),
            "model_path_exists": self.model_dir.exists(),
            "qwen_python": str(self.python_path),
            "qwen_python_exists": self.python_path.exists(),
            "endpoint_configured": bool(endpoint),
            "in_process_enabled": in_process,
            "worker_available": worker_available,
            "worker_running": worker_running,
            "worker_pid": self._worker.pid if worker_running and self._worker is not None else None,
            "worker_error": None if ready else self._worker_error,
            "worker_info": self._worker_info if worker_running else None,
            "quantization": (self._worker_info or {}).get("quantization") if worker_running else None,
            "mode": mode,
            "state": state,
            "ready": ready,
        }

    def generate(self, prompt: str, on_token: Callable[[str], None] | None = None, think: bool = False) -> tuple[str | None, dict[str, Any]]:
        ollama_model = os.getenv("QWEN_OLLAMA_MODEL", "").strip()
        if ollama_model:
            try:
                return self._generate_with_ollama(prompt, on_token, think), {"mode": "ollama", "think": think}
            except Exception as exc:
                if think:  # reasoning ran out of budget / produced no answer -> answer fast without it
                    try:
                        return self._generate_with_ollama(prompt, on_token, False), {"mode": "ollama", "think": False, "note": "think-retry"}
                    except Exception as exc2:
                        return None, {"mode": "fallback", "error": str(exc2)}
                return None, {"mode": "fallback", "error": str(exc)}

        endpoint = os.getenv("QWEN_LOCAL_ENDPOINT", "").strip()
        if endpoint:
            try:
                return self._generate_with_endpoint(endpoint, prompt), {"mode": "http-endpoint"}
            except Exception as exc:
                return None, {"mode": "fallback", "error": str(exc)}

        if os.getenv("QWEN_ENABLE_IN_PROCESS", "").strip() == "1":
            try:
                return self._generate_in_process(prompt), {"mode": "in-process"}
            except Exception as exc:
                return None, {"mode": "fallback", "error": str(exc)}

        if self._worker_auto_start_enabled() and self.model_dir.exists() and self.python_path.exists():
            # Live-demo resilience mirror (see services/claude_provider.py's
            # generate_structured and docs/phase2/DECISIONS.md, dated entry):
            # this loop is Qwen's own bounded-but-lengthy retry ladder --
            # each attempt is bounded by QWEN_GENERATION_TIMEOUT_SEC (default
            # 240s) and a failed attempt before the last also pays a fresh
            # worker-restart wait (_restart_worker_after_failure ->
            # QWEN_WORKER_START_TIMEOUT_SEC, default 420s) -- so two attempts
            # could previously walk silently for the better part of 15
            # minutes with zero log output. ladder_ceiling_seconds() (shared
            # with the Claude path) bounds the total walk; every attempt
            # failure now logs at WARNING, a recovery after a prior failure
            # logs at INFO, and a ceiling trip logs one ERROR line before
            # returning the same (None, meta) shape callers already handle.
            from services.agent.structured import ladder_ceiling_seconds

            attempts = max(1, int(os.getenv("QWEN_GENERATION_ATTEMPTS", "2")))
            ceiling = ladder_ceiling_seconds()
            ladder_start = time.monotonic()
            last_error: Exception | None = None
            had_prior_failure = False
            for attempt in range(1, attempts + 1):
                elapsed_before = time.monotonic() - ladder_start
                if elapsed_before >= ceiling:
                    logger.error(
                        "LocalQwenProvider.generate: worker ladder ceiling exceeded "
                        "(elapsed=%.1fs >= ceiling=%.1fs) before attempt=%d/%d -- "
                        "aborting without a further attempt",
                        elapsed_before, ceiling, attempt, attempts,
                    )
                    return None, {
                        "mode": "fallback",
                        "error": (
                            f"provider ladder ceiling ({ceiling:.0f}s) exceeded after "
                            f"{elapsed_before:.1f}s; aborted before attempt {attempt}"
                        ),
                        "attempts": attempt - 1,
                    }

                attempt_start = time.monotonic()
                try:
                    text = self._generate_with_worker(prompt, on_token)
                except Exception as exc:
                    attempt_elapsed = time.monotonic() - attempt_start
                    logger.warning(
                        "LocalQwenProvider.generate: worker attempt failed "
                        "(attempt=%d/%d elapsed=%.1fs exception=%s): %s",
                        attempt, attempts, attempt_elapsed, type(exc).__name__, exc,
                    )
                    last_error = exc
                    had_prior_failure = True
                    self._worker_error = str(exc)
                    self._worker_state = "failed"
                    if attempt < attempts:
                        self._restart_worker_after_failure()
                    continue

                if had_prior_failure:
                    logger.info(
                        "LocalQwenProvider.generate: worker recovered on attempt=%d/%d "
                        "after a prior failure (total elapsed=%.1fs)",
                        attempt, attempts, time.monotonic() - ladder_start,
                    )
                return text, {"mode": "stdio-worker", "attempts": attempt}
            return None, {"mode": "fallback", "error": str(last_error or "Qwen generation failed."), "attempts": attempts}

        return None, {"mode": "fallback", "error": "Local Qwen worker is not available."}

    def _worker_auto_start_enabled(self) -> bool:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return False
        value = os.getenv("QWEN_WORKER_AUTO_START", "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def warmup(self) -> None:
        """Spawn the worker and load the model ahead of the first request.

        Meant to be called from a background thread at startup so the first chat
        is not a cold start. Blocks (in that thread) until the model finishes
        loading; failures are recorded on ``_worker_state``/``_worker_error`` and
        surfaced through ``status()``.
        """
        if os.getenv("QWEN_OLLAMA_MODEL", "").strip():
            try:
                self._generate_with_ollama("ping", None, think=False)  # load the GGUF into VRAM
            except Exception:
                pass
            return
        if os.getenv("QWEN_LOCAL_ENDPOINT", "").strip():
            return
        if os.getenv("QWEN_ENABLE_IN_PROCESS", "").strip() == "1":
            return
        if not (self._worker_auto_start_enabled() and self.model_dir.exists() and self.python_path.exists()):
            return
        try:
            with self._lock:
                self._ensure_worker()
        except Exception:
            pass

    def _generate_with_endpoint(self, endpoint: str, prompt: str) -> str:
        timeout = float(os.getenv("QWEN_HTTP_TIMEOUT_SEC", "120"))
        payload = json.dumps({"prompt": prompt, "message": prompt, "model_path": str(self.model_dir)}).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip()

        for key in ("answer", "response", "text", "generated_text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return raw.strip()

    def _generate_with_ollama(self, prompt: str, on_token: Callable[[str], None] | None = None, think: bool = False) -> str:
        """Generate via the local Ollama server (GGUF). Streams the answer tokens to
        ``on_token``; when ``think`` is true the model's reasoning is produced in a
        separate ``thinking`` field and is NOT streamed (only the final answer)."""
        model = os.getenv("QWEN_OLLAMA_MODEL", "qwen3:8b").strip()
        base = os.getenv("QWEN_OLLAMA_URL", "http://localhost:11434").rstrip("/")
        num_predict = int(os.getenv("QWEN_OLLAMA_NUM_PREDICT", "2560" if think else "512"))
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "think": bool(think),
            "options": {
                "temperature": float(os.getenv("QWEN_TEMPERATURE", "0.25")),
                "num_predict": num_predict,
            },
        }
        request = urllib.request.Request(
            base + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = float(os.getenv("QWEN_GENERATION_TIMEOUT_SEC", "240"))
        self._worker_state = "generating"
        pieces: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        raise RuntimeError(str(obj["error"]))
                    content = (obj.get("message") or {}).get("content")
                    if content:
                        pieces.append(content)
                        if on_token is not None:
                            try:
                                on_token(content)
                            except Exception:
                                pass
                    if obj.get("done"):
                        break
        finally:
            self._worker_state = "ready"
        text = "".join(pieces).strip()
        if not text:
            raise RuntimeError("Ollama returned an empty response.")
        return text

    def _generate_with_worker(self, prompt: str, on_token: Callable[[str], None] | None = None) -> str:
        with self._lock:
            self._ensure_worker()
            if self._worker is None or self._worker.stdin is None:
                raise RuntimeError("Qwen worker did not start.")

            request_id = uuid.uuid4().hex
            payload: dict[str, Any] = {"type": "generate", "id": request_id, "prompt": prompt}
            if on_token is not None:
                payload["stream"] = True
            self._worker_state = "generating"
            timeout = float(os.getenv("QWEN_GENERATION_TIMEOUT_SEC", "240"))
            try:
                self._worker.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._worker.stdin.flush()

                if on_token is None:
                    response = self._await_message(
                        timeout=timeout,
                        predicate=lambda item: item.get("type") == "response" and item.get("id") == request_id,
                    )
                else:
                    response = self._await_streaming_response(request_id, on_token, timeout)
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or "Qwen generation failed."))
                text = str(response.get("text") or "").strip()
                if not text:
                    raise RuntimeError("Qwen returned an empty response.")
                self._worker_state = "ready"
                return text
            finally:
                if self._worker_state == "generating":
                    self._worker_state = "ready"

    def _await_streaming_response(
        self, request_id: str, on_token: Callable[[str], None], timeout: float
    ) -> dict[str, Any]:
        """Forward incremental ``token`` events to ``on_token`` until the worker
        emits the final ``response`` message for this request."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._worker is not None and self._worker.poll() is not None:
                raise RuntimeError(self._worker_error or f"Qwen worker exited with code {self._worker.returncode}.")
            remaining = max(0.1, deadline - time.monotonic())
            try:
                item = self._messages.get(timeout=min(0.5, remaining))
            except Empty:
                continue
            if item.get("id") != request_id:
                continue
            if item.get("type") == "token":
                piece = item.get("text")
                if piece:
                    try:
                        on_token(str(piece))
                    except Exception:
                        pass
                continue
            if item.get("type") == "response":
                return item
        raise TimeoutError("Timed out waiting for the local Qwen worker.")

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.poll() is None:
            return

        self._messages = Queue()
        self._worker_error = None
        self._worker_info = None
        self._worker_state = "loading"
        worker_path = Path(__file__).with_name("qwen_worker.py")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        command = [
            str(self.python_path),
            "-u",
            str(worker_path),
            "--model-dir",
            str(self.model_dir),
            "--max-new-tokens",
            os.getenv("QWEN_MAX_NEW_TOKENS", "256"),
            "--temperature",
            os.getenv("QWEN_TEMPERATURE", "0.25"),
        ]
        if os.getenv("QWEN_LOAD_IN_4BIT", "1").strip() != "0":
            command.append("--load-in-4bit")

        self._worker = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        threading.Thread(target=self._read_worker_stdout, daemon=True).start()
        threading.Thread(target=self._read_worker_stderr, daemon=True).start()

        ready = self._await_message(
            timeout=float(os.getenv("QWEN_WORKER_START_TIMEOUT_SEC", "420")),
            predicate=lambda item: item.get("type") in {"ready", "error"},
        )
        if ready.get("type") == "error":
            self._worker_state = "failed"
            raise RuntimeError(str(ready.get("error") or "Qwen worker failed to load."))
        self._worker_info = ready
        self._worker_error = None
        self._worker_state = "ready"

    def _restart_worker_after_failure(self) -> None:
        worker = self._worker
        self._worker = None
        self._worker_info = None
        self._messages = Queue()
        self._worker_state = "standby"
        if worker is None:
            return
        try:
            if worker.poll() is None:
                worker.kill()
        except Exception:
            pass

    def _read_worker_stdout(self) -> None:
        worker = self._worker
        if worker is None or worker.stdout is None:
            return
        for line in worker.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _read_worker_stderr(self) -> None:
        worker = self._worker
        if worker is None or worker.stderr is None:
            return
        tail: list[str] = []
        for line in worker.stderr:
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            self._worker_error = "\n".join(tail[-8:])

    def _await_message(self, timeout: float, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._worker is not None and self._worker.poll() is not None:
                raise RuntimeError(self._worker_error or f"Qwen worker exited with code {self._worker.returncode}.")
            remaining = max(0.1, deadline - time.monotonic())
            try:
                item = self._messages.get(timeout=min(0.5, remaining))
            except Empty:
                continue
            if predicate(item):
                return item
        raise TimeoutError("Timed out waiting for the local Qwen worker.")

    def _generate_in_process(self, prompt: str) -> str:
        processor, model, torch = _load_qwen_in_process(str(self.model_dir))
        chat = [
            {"role": "system", "content": "You are a VISSIM dashboard assistant. Answer in the language requested by the prompt."},
            {"role": "user", "content": prompt},
        ]
        text = processor.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        inputs = None
        outputs = None
        generated = None
        try:
            inputs = processor(text=[text], return_tensors="pt").to(device)
            with torch.inference_mode():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=int(os.getenv("QWEN_MAX_NEW_TOKENS", "384")),
                    do_sample=True,
                    temperature=float(os.getenv("QWEN_TEMPERATURE", "0.25")),
                    eos_token_id=processor.tokenizer.eos_token_id,
                    pad_token_id=processor.tokenizer.eos_token_id,
                )
            generated = outputs[0][inputs["input_ids"].shape[-1] :]
            return processor.decode(generated, skip_special_tokens=True).strip()
        finally:
            del generated, outputs, inputs
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Strict-JSON structured generation for a model with no native tool-use.

        Deliberately thin (see docs/phase2/DECISIONS.md, dated entry for this
        method): all prompt-building, salvage, action validation, and
        fallback-rung bookkeeping live in ``services.agent.structured``,
        imported lazily here (matching ``claude_provider.py``'s lazy ``import
        anthropic`` pattern) so this module carries no module-load-time
        dependency on ``agent/``. Both the parsed-JSON path and the rescue path
        below route through ``salvage_and_validate`` (repairs occasional
        XML-tool-syntax contamination, then validates) rather than
        ``validate_action`` directly. Never routes the reply through
        ``clean_generated_answer`` (it strips JSON). ``max_tokens`` is accepted
        for signature parity with ``ClaudeProvider`` but is not forwarded — the
        underlying ``generate`` path sizing is controlled by the existing
        ``QWEN_MAX_NEW_TOKENS`` env var.
        """
        from services.agent.structured import (
            build_meta,
            build_strict_json_prompt,
            salvage_and_validate,
        )
        from services.dashboard_curator import extract_json_object

        prompt = build_strict_json_prompt(messages, tools, schema, system)
        reply, generate_meta = self.generate(prompt)
        mode = generate_meta.get("mode", "fallback")
        if not reply:
            return None, build_meta(
                mode=mode,
                fallback_rung=2,
                error=generate_meta.get("error") or "Qwen returned no reply.",
            )

        try:
            action = json.loads(reply)
        except (json.JSONDecodeError, TypeError, ValueError):
            action = None
        if isinstance(action, dict):
            cleaned, reason = salvage_and_validate(action, tools, schema)
            if reason is None:
                return cleaned, build_meta(mode=mode, fallback_rung=0)

        rescued = extract_json_object(reply)
        if isinstance(rescued, dict):
            cleaned, reason = salvage_and_validate(rescued, tools, schema)
            if reason is None:
                return cleaned, build_meta(mode=mode, fallback_rung=1)
            return None, build_meta(mode=mode, fallback_rung=2, error=reason)

        return None, build_meta(
            mode=mode, fallback_rung=2, error="Qwen reply was not valid JSON."
        )


@lru_cache(maxsize=1)
def _load_qwen_in_process(model_dir: str):
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

    processor = AutoProcessor.from_pretrained(model_dir)
    kwargs: dict[str, Any] = {
        "device_map": "auto",
        "dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        "low_cpu_mem_usage": True,
    }
    if os.getenv("QWEN_LOAD_IN_4BIT", "1").strip() != "0":
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForImageTextToText.from_pretrained(model_dir, **kwargs)
    return processor, model, torch


def _default_provider() -> Any:
    """Pick the assistant LLM backend.

    ``ASSISTANT_PROVIDER=claude|qwen`` forces a backend; otherwise Claude is
    used whenever an API key is configured, keeping local Qwen as the
    no-key/on-premise fallback.
    """
    choice = os.getenv("ASSISTANT_PROVIDER", "").strip().lower()
    if choice != "qwen" and (choice == "claude" or os.getenv("ANTHROPIC_API_KEY", "").strip()):
        from services.claude_provider import ClaudeProvider

        return ClaudeProvider(system_prompt=OLLAMA_SYSTEM_PROMPT)
    return LocalQwenProvider()


class VissimAssistantRouter:
    def __init__(
        self,
        provider: Any | None = None,
        com_status_reader: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.provider = provider or _default_provider()
        self.com_status_reader = com_status_reader or get_vissim_status

    def _model_label(self) -> str:
        try:
            return str(self.provider.status().get("model") or DEFAULT_MODEL_LABEL)
        except Exception:
            return DEFAULT_MODEL_LABEL

    def warmup(self) -> None:
        """Preload the local model so the first chat request isn't a cold start."""
        warmup = getattr(self.provider, "warmup", None)
        if callable(warmup):
            warmup()

    def status(self) -> dict[str, Any]:
        return {
            **self.provider.status(),
            "router": {
                "intents": ["general", "status", "diagnostic", "settings", "capabilities"],
                "rag_sources": ["docs", "dashboard summaries", "simulation registry"],
            },
            "rag": get_rag_status(),
            "tools": {
                "vissim_com": get_vissim_com_capabilities(),
            },
        }

    def answer(self, request: AssistantRequestContext, on_token: Callable[[str], None] | None = None) -> dict[str, Any]:
        message = request.message.strip()
        intent = classify_intent(message)
        response_language = normalize_response_language(request.response_language, request.message)
        if is_simple_greeting(message):
            return self._direct_answer(
                intent=intent,
                answer=build_greeting_answer(response_language),
                response_language=response_language,
                safety=build_safety_result(intent, message, request.allow_tool_execution),
            )
        if is_assistant_meta_question(message):
            return self._direct_answer(
                intent=intent,
                answer=build_assistant_meta_answer(
                    status=self.provider.status(),
                    english=response_language == "en",
                    japanese=response_language == "ja",
                    include_model=is_model_identity_question(message),
                ),
                response_language=response_language,
                safety=build_safety_result(intent, message, request.allow_tool_execution),
                tool_results={"assistant_model": compact_provider_status(self.provider.status())},
            )

        context = self._build_context(request, intent)
        if is_simple_greeting(message):
            return {
                "intent": intent,
                "answer": build_greeting_answer(context.get("response_language") or "ko"),
                "model": self._model_label(),
                "model_status": self.status(),
                "runtime": {"mode": "dashboard-context"},
                "sources": [],
                "tool_results": context["tool_results"],
                "safety": context["safety"],
                "answer_format": answer_format_payload(intent, context.get("response_language") or "ko"),
            }
        prompt = self._compose_prompt(message, intent, context)
        prompt_simulation = context["tool_results"].get("prompt_simulation")
        wants_reasoning = is_analytical_question(message) or is_validation_question(message)
        if intent == "capabilities" or prompt_simulation:
            generated, runtime = None, {"mode": "dashboard-context"}
        elif (intent == "status" or is_dashboard_diagnostic_question(message)) and not wants_reasoning:
            generated, runtime = None, {"mode": "dashboard-context"}
        else:
            think = is_analytical_question(message)  # hybrid: reasoning only for why/cause/how questions
            generated, runtime = self.provider.generate(prompt, on_token, think=think)
        cleaned_answer = clean_generated_answer(generated) if generated else ""
        answer = cleaned_answer or self._fallback_answer(intent, context)

        return {
            "intent": intent,
            "answer": answer,
            "model": self._model_label(),
            "model_status": self.status(),
            "runtime": runtime,
            "sources": context["sources"],
            "tool_results": context["tool_results"],
            "safety": context["safety"],
            "answer_format": answer_format_payload(intent, context.get("response_language") or "ko"),
        }

    def _direct_answer(
        self,
        intent: str,
        answer: str,
        response_language: str,
        safety: dict[str, Any],
        tool_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "intent": intent,
            "answer": answer,
            "model": self._model_label(),
            "model_status": self.status(),
            "runtime": {"mode": "dashboard-context"},
            "sources": [],
            "tool_results": tool_results or {},
            "safety": safety,
            "answer_format": answer_format_payload(intent, response_language),
        }

    def _build_context(self, request: AssistantRequestContext, intent: str) -> dict[str, Any]:
        active_dataset = get_active_dataset()
        safety = build_safety_result(intent, request.message, request.allow_tool_execution)
        response_language = normalize_response_language(request.response_language, request.message)
        if intent == "capabilities":
            return {
                "sources": [],
                "tool_results": {
                    "active_dataset": compact_dataset(active_dataset),
                    "assistant_capabilities": assistant_capabilities_payload(),
                    "vissim_com": {"capabilities": get_vissim_com_capabilities()},
                },
                "safety": safety,
                "rag_chunks": [],
                "response_language": response_language,
            }

        has_uploaded_dataset = active_dataset.get("source") == "uploaded"
        scenarios = list_scenarios() if has_uploaded_dataset else []
        selected_ids = request.selected_scenario_ids if has_uploaded_dataset else []
        selected_ids = selected_ids or [scenario["id"] for scenario in scenarios[:4]]
        selected_ids = [scenario_id for scenario_id in selected_ids if scenario_id]
        selected_node_ids = compact_selected_ids(request.selected_node_ids, 6) if has_uploaded_dataset else []
        summary = get_compare_summary(selected_ids) if selected_ids else {}
        selected_intersections = (
            get_selected_node_details(selected_ids, selected_node_ids)
            if selected_ids and selected_node_ids
            else {"selected_node_ids": selected_node_ids, "scenario_ids": selected_ids, "nodes": [], "missing_node_ids": selected_node_ids}
        )
        jobs = list_job_records()[:8]
        docs = [] if intent == "status" else search_rag(request.message, DOC_CONTEXT_LIMIT)

        tool_results: dict[str, Any] = {
            "active_dataset": compact_dataset(active_dataset),
            "selected_scenario_ids": selected_ids,
            "selected_node_ids": selected_node_ids,
            "scenario_count": len(scenarios),
        }
        # Inject base-model GEH validation early (before the bulky comparison and
        # intersection blocks) so it survives JSON context truncation when the
        # user asks about GEH / validation.
        if has_uploaded_dataset and intent in {"general", "diagnostic"} and (
            is_validation_question(request.message) or is_analytical_question(request.message)
        ):
            validation_summary = build_base_validation_summary(period=detect_count_period(request.message))
            if validation_summary:
                tool_results["base_model_validation"] = validation_summary
                red_ids = [n["node_id"] for n in validation_summary["nodes"] if n.get("grade") == "poor"]
                target_nodes = list(dict.fromkeys([*selected_node_ids, *red_ids]))
                routing = build_node_routing_summary(target_nodes)
                if routing:
                    tool_results["routing_decisions"] = routing
        tool_results["comparison_summary"] = compact_compare_summary(summary, response_language)
        if selected_node_ids:
            tool_results["selected_intersection_details"] = selected_intersections

        # P6: the diagram selection. Deliberately NOT gated on
        # has_uploaded_dataset the way the node/scenario blocks are -- links and
        # elements come from the active .inpx, which exists for the bundled demo
        # dataset too, so clicking a road on a fresh checkout still gives the
        # model something real to answer about. Each block degrades on its own:
        # an unparseable network yields "missing" entries, never a failed answer.
        if request.selected_link_ids:
            try:
                tool_results["selected_links"] = resolve_selected_links(request.selected_link_ids)
            except Exception:
                logger.exception("Assistant context: selected link details failed")
        # Multi-select: `selected_elements` is the merged full selection (a
        # pre-multi-select request's single element is its one entry), and
        # element_selection_fields keeps ONE object on the legacy
        # `selected_element` key while a group is named object-by-object under
        # `selected_elements` -- never collapsed to a count, never truncated to
        # the first. The real client model needs this: an approach carries
        # three vehicle inputs on one coordinate, one per vehicle class.
        selected_elements = request.selected_elements or (
            [request.selected_element] if request.selected_element else []
        )
        if selected_elements:
            try:
                tool_results.update(
                    element_selection_fields(
                        resolve_selected_elements(selected_elements),
                        request.selected_elements_omitted,
                        prefix="selected_",
                    )
                )
            except Exception:
                logger.exception("Assistant context: selected element details failed")

        if intent in {"status", "diagnostic"}:
            compacted_jobs = compact_jobs(jobs, response_language)
            tool_results["simulation_jobs"] = compacted_jobs
            tool_results["vissim_com"] = compact_vissim_com_status(self.com_status_reader())
        if intent == "diagnostic":
            tool_results["recent_errors"] = [job for job in tool_results.get("simulation_jobs", []) if job.get("status") == "error"]
            tool_results["dashboard_diagnostic"] = is_dashboard_diagnostic_question(request.message)
        if intent == "settings":
            prompt_simulation = build_prompt_simulation_context(request.message, request.allow_tool_execution)
            tool_results["setting_change"] = {
                "executed": bool(prompt_simulation.get("executed")),
                "reason": prompt_simulation.get("reason") or safety["reason"],
            }
            if prompt_simulation.get("recognized"):
                tool_results["prompt_simulation"] = prompt_simulation
            tool_results["vissim_com"] = {
                "capabilities": get_vissim_com_capabilities(),
                "executed": bool(prompt_simulation.get("executed")),
                "reason": prompt_simulation.get("reason") or "Settings execution is still gated by the assistant safety check.",
            }

        doc_sources = []
        seen_doc_paths: set[str] = set()
        for item in docs:
            if item["path"] in seen_doc_paths:
                continue
            seen_doc_paths.add(item["path"])
            doc_sources.append(source_from_rag_item(item))

        sources = [{"kind": "dashboard", "title": "Active dataset and scenario summary"}, *doc_sources]
        if tool_results.get("selected_intersection_details"):
            sources.append({"kind": "dashboard", "title": "Selected intersection dashboard details"})
        if (
            tool_results.get("selected_links")
            or tool_results.get("selected_element")
            or tool_results.get("selected_elements")
        ):
            sources.append({"kind": "dashboard", "title": "Selected network diagram objects"})
        if intent in {"status", "diagnostic"}:
            sources.append({"kind": "tool", "title": "Simulation job registry"})
            sources.append({"kind": "tool", "title": "Vissim COM Tool"})
        if intent == "settings":
            sources.append({"kind": "tool", "title": "Vissim COM Tool capabilities"})
            if tool_results.get("prompt_simulation"):
                sources.append({"kind": "tool", "title": "Prompt simulation runner"})

        return {
            "sources": sources,
            "tool_results": tool_results,
            "safety": safety,
            "rag_chunks": docs,
            "response_language": response_language,
        }

    def _compose_prompt(self, message: str, intent: str, context: dict[str, Any]) -> str:
        response_language = context.get("response_language")
        payload = {
            "intent": intent,
            "answer_format": answer_format_payload(intent, response_language),
            "tool_results": context["tool_results"],
            "safety": context["safety"],
            "rag_chunks": context["rag_chunks"],
        }
        if response_language == "en":
            payload = sanitize_context_for_english(payload)
        format_instructions = answer_format_prompt(intent, response_language)
        language_instruction = {
            "en": "Answer in English.\n",
            "ja": "Answer in Japanese (日本語で回答してください).\n",
        }.get(response_language, "Answer in Korean unless the user asks otherwise.\n")
        return (
            "You are answering inside the VISSIM Web Dashboard.\n"
            f"{language_instruction}"
            + (
                "Do not echo Korean source strings from the context; translate them into the answer language, use a generic label, or cite the stable ID instead.\n"
                if response_language != "ko"
                else ""
            )
            + "Return only the final answer. Do not write reasoning, analysis, a draft, or planning notes.\n"
            "Do not expose internal route names, fallback status, implementation details, JSON keys, Python names, or schema names.\n"
            "Never mention internal names such as recent_errors, comparison_summary, selected_scenario_ids, selected_intersection_details, rag_chunks, tool_results, runtime, or fallback.\n"
            "Do not use Markdown emphasis markers. Never wrap words with double asterisks or underscores.\n"
            "Do not use Markdown heading markers. If labels help readability, write them as short plain labels ending with a colon.\n"
            "Avoid report-like over-structuring. Prefer a concise operational answer with two or three short sections at most.\n"
            "Use the provided context only. If a value is missing, estimated, or from fallback dashboard data, say so briefly.\n"
            "When selected intersection details are present, prioritize the active or last selected intersection and explain volume, delay, queue, worst approach, and scenario deltas in operational language.\n"
            "If the retrieved documents do not support the answer, say that the documents do not confirm it.\n"
            "Distinguish dashboard data from VISSIM document knowledge when both are present.\n"
            "For capability or help questions, describe available assistant features only; do not analyze scenario metrics or cite documents unless the user asked for a specific data or document answer.\n"
            "For settings requests, report safety status before suggesting the next action.\n"
            "Validation metric facts (authoritative; never contradict these):\n"
            "- GEH is a count-validation statistic comparing a location's model (simulated) traffic volume with its observed real-world count: GEH = sqrt(2*(model-observed)^2/(model+observed)). Lower is better; below 5 is a good match, 5 to 10 is fair, 10 or above is poor.\n"
            "- GEH is computed per scenario from validation counts; a single loaded scenario is sufficient and it never requires selecting or comparing two or more scenarios. A single-scenario condition only limits scenario-to-scenario comparison, never GEH or base-model validation.\n"
            "- The 교통량 검증 map colours each node by its WORST single-movement GEH (map_geh), NOT the node-total GEH. A node can have a good node total yet appear red because one movement is far off, so a red node DOES exceed the threshold at the movement level — never deny that a red node has GEH 10 or above.\n"
            "- When per-node validation data is in the context, answer a node's GEH question with its grade and map_geh; if the node is red/poor, cite its worst movement (direction, observed vs model volume, and GEH) to explain why it is red, and you may add the node-total GEH for context. Do not claim GEH is unavailable because only one scenario is selected.\n"
            "VISSIM calibration guide — when the user asks WHERE the problem is or HOW to fix a high-GEH node, give actionable guidance, not just numbers:\n"
            "- GEH compares volumes, so a high-GEH movement is a DEMAND or ROUTING problem, not car-following. The main VISSIM levers are Static Vehicle Routing Decisions (relative flows = turn ratios), Vehicle Inputs (demand on feeding links), and connectors / lane connectors (whether a movement physically exists).\n"
            "- If the node TOTAL matches observed but one movement is off, demand is fine and the issue is the TURN DISTRIBUTION — fix that approach's Static Routing Decision relative flows (and its sibling movements) to match the observed turning-movement counts.\n"
            "- If a worst movement carries a diagnosis (pattern, likely_cause, suggested_fix), base your guidance on it: state where the problem most likely is (the specific movement and VISSIM object) and the concrete fix step, then add one short verification step. Prefer the most probable single cause over listing everything.\n"
            "- If routing-decision data is in the context, it holds the ACTUAL current Static Routing Decision relative flows (turn ratios) near the node, aggregated per approach (each turn with rel_flow and share_pct). Use it to give concrete numbers — name the approach and turn, state its current share %, and which way to adjust it — instead of saying the values are unavailable. Approaches are matched geometrically so choose the one whose direction matches the problem movement; if the list is empty, say no static routing decision was found there (the movement may have no route, matching a missing-movement diagnosis).\n"
            "Network performance metric facts (authoritative; never contradict these):\n"
            "- delay, speed, and stops are PER-VEHICLE AVERAGES (average delay seconds per vehicle, average speed, average stops per vehicle). total_travel_time is a TOTAL: the SUM of every vehicle's time spent in the whole network over the entire simulation period, in seconds — so values of hundreds of thousands or millions of seconds are NORMAL. It is NOT an average and NOT a single route's travel time.\n"
            "- Never call a TOTAL (total_travel_time, total delay, total volume) 'low' or 'high' on its own: a sum has no inherent good/bad level without a baseline. Assess the network's performance LEVEL using the per-vehicle averages (avg delay, avg speed, avg stops), or by comparing the total against another scenario. For total_travel_time lower is better, but say that only relative to a comparison — never as an absolute 'it is low'.\n"
            "- When you report total_travel_time, label it clearly as the total (총 통행시간 / total travel time = sum over all vehicles), give it in seconds (optionally also hours), and if total delay is in the context you may note what share of the total travel time is delay.\n"
            "- Individual route travel times (typically a few hundred seconds each) are the per-route averages used for travel-time validation against observed values; do NOT confuse them with the network total_travel_time.\n\n"
            f"{format_instructions}\n\n"
            f"User question:\n{message}\n\n"
            f"Dashboard context JSON:\n{limited_json(payload)}"
            + {
                "en": "\n\nImportant: write the entire final answer in English.",
                "ja": "\n\n重要: 最終的な回答は必ずすべて日本語で書いてください。文脈に含まれる韓国語や英語の語句もすべて日本語に翻訳すること。",
            }.get(response_language, "\n\n중요: 최종 답변은 반드시 전부 한국어로 작성하세요. 영어로 답하지 마세요.")
        )

    def _fallback_answer(self, intent: str, context: dict[str, Any]) -> str:
        tools = context["tool_results"]
        dataset = tools.get("active_dataset") or {}
        summary = tools.get("comparison_summary") or {}
        insights = summary.get("key_insights") or []
        jobs = tools.get("simulation_jobs") or []
        response_language = context.get("response_language")
        lang = "ja" if response_language == "ja" else ("en" if response_language != "ko" else "ko")
        english = lang == "en"
        if intent == "capabilities":
            if lang == "ja":
                return "\n".join(
                    [
                        "できること:",
                        "- アップロードされた VISSIM データセット、シナリオ比較、選択された交差点、検証指標、VISSIM ドキュメントの根拠、シミュレーション/COM の状態について説明できます。",
                        "必要な入力:",
                        "- 分析は、VISSIM の INPX/ZIP データセット、選択されたシナリオ、選択されたノード、または検証ワークブックがあるときに最も信頼できます。",
                        "制限:",
                        "- 明示的で安全な実行要求がない限り設定の変更やシミュレーションの実行は行わず、データのない項目は未確認として表示します。",
                    ]
                )
            if english:
                return "\n".join(
                    [
                        "Capabilities:",
                        "- I can explain uploaded VISSIM datasets, scenario comparisons, selected intersections, validation metrics, VISSIM document evidence, and simulation/COM status.",
                        "Required input:",
                        "- Analysis is most reliable when a VISSIM INPX/ZIP dataset, selected scenarios, selected nodes, or a validation workbook are available.",
                        "Limits:",
                        "- I do not change settings or run simulations without an explicit safe execution request, and I mark missing data as unconfirmed.",
                    ]
                )
            return "\n".join(
                [
                    "\ud560 \uc218 \uc788\ub294 \uc77c: VISSIM INPX/ZIP \uc5c5\ub85c\ub4dc \ub370\uc774\ud130\ub97c \uae30\ubc18\uc73c\ub85c \ub300\uc2dc\ubcf4\ub4dc \ud604\ud669, \uc2dc\ub098\ub9ac\uc624 \ube44\uad50, \uad50\ucc28\ub85c/\uc9c4\uc785\ubc29\ud5a5 \uc131\ub2a5, \uac80\uc99d \uc9c0\ud45c, VISSIM \ubb38\uc11c \uadfc\uac70\ub97c \ud568\uaed8 \uc124\uba85\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.",
                    "\ud544\uc694\ud55c \uc785\ub825: \uc2e4\uc81c \ubd84\uc11d\uc740 \uc5c5\ub85c\ub4dc\ub41c VISSIM \ud30c\uc77c, \uc120\ud0dd\ub41c \uc2dc\ub098\ub9ac\uc624, \uc120\ud0dd\ub41c \ub178\ub4dc, \ub610\ub294 \uae30\uc900 \uac80\uc99d \uc6cc\ud06c\ubd81\uc774 \uc788\uc744 \ub54c \uac00\uc7a5 \uc815\ud655\ud569\ub2c8\ub2e4.",
                    "\uc81c\ud55c: \uc124\uc815 \ubcc0\uacbd\uc774\ub098 \uc2e4\ud589\uc740 \uc548\uc804 \ud655\uc778 \uc5c6\uc774 \uc784\uc758\ub85c \uc218\ud589\ud558\uc9c0 \uc54a\uace0, \ub370\uc774\ud130\uac00 \uc5c6\ub294 \ud56d\ubaa9\uc740 \ud655\uc778 \ud544\uc694\ub85c \ud45c\uc2dc\ud569\ub2c8\ub2e4.",
                ]
            )
        scenario_ids_default = "なし" if lang == "ja" else ("none" if lang == "en" else "선택 없음")
        scenario_ids = ", ".join(tools.get("selected_scenario_ids") or []) or scenario_ids_default

        if intent == "status":
            if lang == "ja":
                return build_status_overview_answer_ja(tools)
            if english:
                return build_status_overview_answer_en(tools)
            return build_status_overview_answer(tools)
        if intent == "diagnostic" and tools.get("dashboard_diagnostic"):
            if lang == "ja":
                return build_dashboard_diagnostic_answer_ja(tools)
            if english:
                return build_dashboard_diagnostic_answer_en(tools)
            return build_dashboard_diagnostic_answer(tools)
        if intent == "settings" and tools.get("prompt_simulation"):
            if lang == "ja":
                return build_prompt_simulation_answer_ja(tools["prompt_simulation"])
            if english:
                return build_prompt_simulation_answer_en(tools["prompt_simulation"])
            return build_prompt_simulation_answer(tools["prompt_simulation"])
        if intent == "general" and tools.get("selected_intersection_details"):
            selected_answer = build_selected_intersection_answer(tools)
            if selected_answer:
                return selected_answer

        if lang == "ja":
            lines = [
                f"現在のデータセットは `{dataset.get('filename', 'unknown')}` で、選択中のシナリオは {scenario_ids} です。",
            ]

            if intent == "status":
                lines.append(f"最近のシミュレーションジョブを {len(jobs)} 件確認しました。")
                if jobs:
                    latest = jobs[0]
                    lines.append(f"最新のジョブは `{latest.get('name') or latest.get('id')}` で、状態は `{latest.get('status')}` です。")
            elif intent == "diagnostic":
                errors = tools.get("recent_errors") or []
                if errors:
                    lines.append(f"最近のエラージョブが {len(errors)} 件あります。最初のエラー: {errors[0].get('error') or '詳細なし'}")
                else:
                    lines.append("最近のジョブレジストリには明確なエラージョブは見当たりません。")
            elif intent == "settings":
                lines.append(f"安全性チェック: {context['safety'].get('reason')}")
            elif insights:
                lines.append("主な要約: " + " / ".join(str(item.get("label") or item.get("title") or item.get("id")) for item in insights[:3]))
            else:
                lines.append("現在の選択条件では、すぐに要約できる比較インサイトが十分にありません。")

            return "\n".join(lines)

        lines = [
            f"현재 데이터셋은 `{dataset.get('filename', 'unknown')}`이고, 선택 시나리오는 {scenario_ids}입니다.",
        ]

        if intent == "status":
            lines.append(f"최근 시뮬레이션 작업은 {len(jobs)}개 확인했습니다.")
            if jobs:
                latest = jobs[0]
                lines.append(f"가장 최근 작업은 `{latest.get('name') or latest.get('id')}`이며 상태는 `{latest.get('status')}`입니다.")
        elif intent == "diagnostic":
            errors = tools.get("recent_errors") or []
            if errors:
                lines.append(f"최근 오류 작업 {len(errors)}개가 있습니다. 첫 오류: {errors[0].get('error') or '상세 없음'}")
            else:
                lines.append("최근 작업 레지스트리에서는 명확한 오류 작업이 보이지 않습니다.")
        elif intent == "settings":
            lines.append(f"안전성 검사: {context['safety'].get('reason')}")
        elif insights:
            lines.append("핵심 요약: " + " / ".join(str(item.get("label") or item.get("title") or item.get("id")) for item in insights[:3]))
        else:
            lines.append("현재 선택 조건에서 바로 요약할 비교 인사이트가 충분하지 않습니다.")

        return "\n".join(lines)


_VALIDATION_QUESTION_TERMS = ("geh", "검증", "validation", "관측", "실측", "보정", "calibrat")


def is_validation_question(message: str) -> bool:
    low = (message or "").lower()
    return any(term in low for term in _VALIDATION_QUESTION_TERMS)


_ANALYTICAL_QUESTION_TERMS = (
    "왜", "원인", "이유", "차이", "분석", "설명", "어떻게", "어디", "고치", "고쳐", "해결",
    "개선", "맞추", "가이드", "why", "cause", "reason", "explain", "analyz", "how", "fix", "solve", "guide", "improve",
)


def is_analytical_question(message: str) -> bool:
    """A 'why / cause / difference / explain' question that wants reasoning, not a
    status template — even when it mentions words like '시뮬레이션'."""
    low = (message or "").lower()
    return any(term in low for term in _ANALYTICAL_QUESTION_TERMS)


def _diagnose_movement(observed: float | None, model: float | None, node_total_ok: bool) -> dict[str, str] | None:
    """Map a movement-level volume mismatch to the most likely VISSIM cause and a
    concrete fix. GEH is a volume metric, so causes are demand/routing (Vehicle
    Inputs, Static Routing Decision relative flows, connectors) — not car-following."""
    if observed is None or model is None:
        return None
    distribution_note = (
        "노드 총량은 실측과 맞으므로 수요가 아니라 회전 배분(turn distribution) 문제"
        if node_total_ok
        else "노드 총량도 어긋나므로 차량 입력(수요)도 함께 점검"
    )
    if observed > 0 and model == 0:
        return {
            "pattern": "missing_movement",
            "likely_cause": "이 회전 이동류가 모델에 사실상 없음 — 접근↔유출 커넥터 미연결이거나 Static Routing Decision 의 상대유량(RelFlow)이 0",
            "suggested_fix": "Network Editor 에서 해당 방향 커넥터/차로 연결 존재를 확인하고, 그 접근로 Static Vehicle Routing Decision 의 경로 목록에 해당 유출 링크를 추가해 RelFlow 를 부여. 차로 폐쇄·충돌영역(Conflict Area)·우선권 규칙으로 막혀 있는지도 확인",
        }
    if model >= observed * 1.8:
        return {
            "pattern": "over_assigned",
            "likely_cause": f"모델이 이 이동류에 교통량을 과대 배분(실측 회전율보다 많음). {distribution_note}",
            "suggested_fix": "해당 접근로 Static Vehicle Routing Decision 의 RelFlow 를 실측 회전교통량(TMC) 비율에 맞춰 낮추고, 같은 접근로 형제 이동류의 RelFlow 를 함께 조정해 합을 유지"
            + ("" if node_total_ok else ". 차량 입력 시간대 프로파일도 점검"),
        }
    if observed >= model * 1.8:
        return {
            "pattern": "under_assigned",
            "likely_cause": f"모델이 이 이동류에 교통량을 과소 배분. {distribution_note}",
            "suggested_fix": "해당 이동류의 RelFlow 를 상향하고 경로/커넥터가 존재하는지 확인"
            + ("" if node_total_ok else ". 차량 입력 수요·상류 링크 정체로 미도달하는지 점검"),
        }
    return {
        "pattern": "ratio_off",
        "likely_cause": "회전 배분 비율이 실측과 다소 차이",
        "suggested_fix": "Static Vehicle Routing Decision 의 RelFlow 를 실측 TMC 비율로 미세 조정",
    }


def detect_count_period(message: str) -> str | None:
    """Pick the observed-count period (AM/PM) a question is about, else None.

    None lets the validation loader auto-select (AM when present)."""
    low = (message or "").lower()
    if any(term in low for term in ("pm", "오후", "저녁", "퇴근", "evening")):
        return "PM"
    if any(term in low for term in ("am", "오전", "아침", "출근", "morning")):
        return "AM"
    return None


def build_base_validation_summary(limit: int = 12, period: str | None = None) -> dict[str, Any] | None:
    """Per-node GEH from the validation workbook (model vs observed counts).

    Mirrors the dashboard 교통량 검증 map: each node's grade/colour is driven by
    its WORST single-movement GEH (``map_geh``), not the node-total GEH, so a
    node whose totals match can still be red when one movement is far off.
    Scenario-independent (Counts sheet). Returns None when no observed counts.
    """
    try:
        real = load_real_counts(str(get_active_real_counts_excel()), period)
    except Exception:
        return None
    if not real:
        return None

    nodes: list[dict[str, Any]] = []
    red = 0
    for node_id in sorted(real, key=lambda value: int(value) if value.isdigit() else 999):
        data = real[node_id]
        observed = data.get("total_real")
        model = data.get("total_sim_basam")
        total_geh = compute_geh(model, observed) if (model is not None and observed is not None) else None

        worst: tuple[float, Any, Any, Any] | None = None  # (geh, direction, observed, model)
        for direction in data.get("directions") or []:
            value = compute_geh(direction.get("sim_basam"), direction.get("real"))
            if value is None:
                continue
            if worst is None or value > worst[0]:
                worst = (value, direction.get("direction"), direction.get("real"), direction.get("sim_basam"))

        map_geh = worst[0] if worst is not None else total_geh
        grade = None if map_geh is None else ("good" if map_geh < 5 else "fair" if map_geh < 10 else "poor")
        if grade == "poor":
            red += 1
        worst_movement = None
        if worst is not None:
            worst_movement = {
                "direction": worst[1],
                "observed": worst[2],
                "model": worst[3],
                "geh": round(worst[0], 2),
            }
            if grade in {"poor", "fair"}:
                node_total_ok = total_geh is not None and total_geh < 5
                worst_movement["diagnosis"] = _diagnose_movement(worst[2], worst[3], node_total_ok)
        nodes.append(
            {
                "node_id": node_id,
                "node_name": data.get("node_name") or f"Node {node_id}",
                "observed_volume": observed,
                "model_volume": model,
                "node_total_geh": round(total_geh, 2) if total_geh is not None else None,
                "map_geh": round(map_geh, 2) if map_geh is not None else None,
                "grade": grade,
                "worst_movement": worst_movement,
            }
        )

    return {
        "metric": "GEH",
        "definition": (
            "map_geh is each node's WORST single-movement GEH and drives the map colour; "
            "node_total_geh is the aggregate (movements can cancel out). A node can have a good "
            "total yet be red because one movement is far off. "
            "GEH = sqrt(2*(model-observed)^2/(model+observed)); <5 good, 5-10 fair, >=10 poor; "
            "computed per scenario, a single scenario is sufficient."
        ),
        "thresholds": {"good": "<5", "fair": "5-10", "poor": ">=10"},
        "period": (period or "AM").upper(),
        "node_count": len(nodes),
        "red_count": red,
        "nodes": nodes[:limit],
    }


@lru_cache(maxsize=2)
def _routing_by_node_cached(inpx_path: str, mtime: float) -> dict:
    from services.inpx_parser import routing_decisions_by_node

    return routing_decisions_by_node(ET.parse(inpx_path).getroot())


def build_node_routing_summary(node_ids, max_nodes: int = 2, max_approaches: int = 4) -> dict[str, Any] | None:
    """Actual Static Routing Decision relative flows (turn ratios) near the given
    nodes, read from the inpx — the concrete lever for fixing a turn-distribution
    GEH mismatch. Aggregates the per-vehicle-class decisions per approach. NOTE:
    approaches are matched to nodes geometrically, so a node's list can include a
    directly adjacent intersection; pick the approach whose direction matches the
    problem movement."""
    try:
        path = get_active_inpx_path()
        if not path.exists():
            return None
        by_node = _routing_by_node_cached(str(path), os.path.getmtime(path))
    except Exception:
        return None

    out: dict[str, Any] = {}
    for nid in list(dict.fromkeys(str(n) for n in (node_ids or [])))[:max_nodes]:
        rds = by_node.get(nid)
        if not rds:
            out[nid] = {
                "approaches": [],
                "note": "이 노드 인근에서 정적 경로결정(Static Routing Decision)을 찾지 못함 — 문제 이동류의 경로 자체가 모델에 없을 수 있음",
            }
            continue
        approaches: dict[str, dict[str, float]] = {}
        for rd in rds:
            name = rd.get("name") or rd.get("approach_link") or "?"
            turns = approaches.setdefault(name, {})
            for route in rd.get("routes", []):
                turn = route.get("name") or "?"
                turns[turn] = turns.get(turn, 0.0) + (route.get("rel_flow") or 0.0)
        rows = []
        for name, turns in approaches.items():
            total = sum(turns.values())
            if total <= 0:
                continue
            rows.append({
                "approach": name,
                "total_rel_flow": round(total, 1),
                "turns": [
                    {"turn": turn, "rel_flow": round(value, 1), "share_pct": round(value / total * 100, 1)}
                    for turn, value in sorted(turns.items(), key=lambda kv: -kv[1])
                ],
            })
        rows.sort(key=lambda row: -row["total_rel_flow"])
        out[nid] = {"approaches": rows[:max_approaches]}
    return out or None


def assistant_capabilities_payload() -> dict[str, Any]:
    return {
        "can_answer": [
            "uploaded_dataset_status",
            "scenario_comparison",
            "intersection_and_direction_performance",
            "base_model_validation",
            "vissim_document_grounded_questions",
            "simulation_and_com_status",
        ],
        "requires_for_data_analysis": [
            "uploaded_vissim_inpx_or_zip",
            "selected_scenario_ids",
            "selected_node_ids",
            "optional_validation_workbook",
        ],
        "limits": [
            "does_not_execute_settings_without_confirmation",
            "does_not_invent_missing_dataset_values",
            "shows_document_sources_only_for_document_or_diagnostic_questions",
        ],
    }


def build_selected_intersection_answer(tools: dict[str, Any]) -> str:
    details = tools.get("selected_intersection_details") or {}
    nodes = details.get("nodes") or []
    missing_node_ids = details.get("missing_node_ids") or []
    if not nodes:
        if missing_node_ids:
            missing_text = ", ".join(map(str, missing_node_ids))
            return "\n".join(
                [
                    "\uc120\ud0dd \uad50\ucc28\ub85c:",
                    f"- \uc120\ud0dd\ud55c \ub178\ub4dc {missing_text}\uc5d0 \ub300\ud55c \ubc29\ud5a5\ubcc4 \uacb0\uacfc\uac00 \ud604\uc7ac \ube44\uad50 \uc2dc\ub098\ub9ac\uc624\uc5d0\uc11c \ud655\uc778\ub418\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.",
                    "\ud655\uc778 \ud544\uc694:",
                    "- \ud574\ub2f9 \uad50\ucc28\ub85c\uac00 \ud604\uc7ac \uc120\ud0dd\ud55c \uc2dc\ub098\ub9ac\uc624 \uacb0\uacfc DB\uc5d0 \ud3ec\ud568\ub418\uc5c8\ub294\uc9c0 \ud655\uc778\ud558\uc138\uc694.",
                ]
            )
        return ""

    node = nodes[-1]
    scenarios = node.get("scenarios") or []
    comparison = node.get("comparison") or {}
    node_name = node.get("node_name") or f"Node {node.get('node_id')}"
    node_id = node.get("node_id")
    seconds_unit = "\ucd08"
    vehicles_unit = "\ub300"
    highest_delay = comparison.get("highest_delay")
    lowest_delay = comparison.get("lowest_delay")
    highest_queue = comparison.get("highest_queue")
    highest_volume = comparison.get("highest_volume")
    focus_scenario = _scenario_by_id(scenarios, (highest_delay or highest_queue or highest_volume or {}).get("scenario_id"))
    if focus_scenario is None:
        focus_scenario = next((scenario for scenario in scenarios if scenario.get("present")), None)

    lines = [
        "\uc120\ud0dd \uad50\ucc28\ub85c:",
        f"- {node_name} (Node {node_id})\uc744 \uae30\uc900\uc73c\ub85c {len(scenarios)}\uac1c \uc2dc\ub098\ub9ac\uc624\ub97c \ube44\uad50\ud588\uc2b5\ub2c8\ub2e4.",
    ]
    if len(nodes) > 1:
        other_count = len(nodes) - 1
        lines.append(f"- \uc5ec\ub7ec \uad50\ucc28\ub85c\uac00 \uc120\ud0dd\ub418\uc5b4 \uc788\uc5b4, \ub300\uc2dc\ubcf4\ub4dc\uc758 \ub9c8\uc9c0\ub9c9 \uc120\ud0dd \uad50\ucc28\ub85c\ub97c \uc6b0\uc120 \uc124\uba85\ud569\ub2c8\ub2e4. \ucd94\uac00 \uc120\ud0dd {other_count}\uac1c\ub3c4 \ucee8\ud14d\uc2a4\uc5d0 \uc788\uc2b5\ub2c8\ub2e4.")

    lines.append("\ud575\uc2ec \ubcd1\ubaa9:")
    if highest_delay:
        lines.append(
            f"- \ud3c9\uade0 \uc9c0\uccb4\uac00 \uac00\uc7a5 \ud070 \uc2dc\ub098\ub9ac\uc624\ub294 {highest_delay.get('scenario_name')}\uc774\uba70, "
            f"\ud3c9\uade0 \uc9c0\uccb4\ub294 {_format_metric(highest_delay.get('value'), seconds_unit)}\uc785\ub2c8\ub2e4."
        )
    if highest_queue:
        lines.append(
            f"- \ucd5c\ub300 \ub300\uae30\ud589\ub82c\uc740 {highest_queue.get('scenario_name')}\uc5d0\uc11c \uac00\uc7a5 \ud06c\uace0, "
            f"QLENMAX \uae30\uc900 {_format_metric(highest_queue.get('value'))}\uc785\ub2c8\ub2e4."
        )
    if highest_volume:
        lines.append(
            f"- \uad50\ud1b5\ub7c9\uc740 {highest_volume.get('scenario_name')}\uc5d0\uc11c \uac00\uc7a5 \ud06c\uace0, "
            f"\ucd1d {_format_metric(highest_volume.get('value'), vehicles_unit, digits=0)} \uc218\uc900\uc785\ub2c8\ub2e4."
        )
    if lowest_delay and highest_delay and lowest_delay.get("scenario_id") != highest_delay.get("scenario_id"):
        lines.append(
            f"- \uc9c0\uccb4 \uce21\uba74\uc5d0\uc11c\ub294 {lowest_delay.get('scenario_name')}\uc774 \uac00\uc7a5 \uc720\ub9ac\ud558\uba70 "
            f"\ud3c9\uade0 {_format_metric(lowest_delay.get('value'), seconds_unit)}\uc785\ub2c8\ub2e4."
        )

    if focus_scenario:
        approaches = focus_scenario.get("top_approaches") or []
        movements = focus_scenario.get("top_movements") or []
        direction_delays = focus_scenario.get("direction_delay_estimates") or {}
        lines.append("\uadfc\uac70:")
        if approaches:
            top_approach = approaches[0]
            lines.append(
                f"- {focus_scenario.get('scenario_name')}\uc5d0\uc11c {_direction_label(top_approach.get('direction'))} \uc811\uadfc \uc720\uc785\ub7c9\uc774 "
                f"{_format_metric(top_approach.get('volume'), vehicles_unit, digits=0)}\ub85c \uac00\uc7a5 \ud07d\ub2c8\ub2e4."
            )
        if movements:
            movement = movements[0]
            lines.append(
                f"- \uc8fc\uc694 \uc774\ub3d9\uc740 {_direction_label(movement.get('from_direction'))} {movement.get('from_lane')}\ucc28\ub85c -> "
                f"{_direction_label(movement.get('to_direction'))} {movement.get('to_lane')}\ucc28\ub85c, "
                f"{_format_metric(movement.get('volume'), vehicles_unit, digits=0)}\uc785\ub2c8\ub2e4."
            )
        if direction_delays:
            worst_direction, worst_value = _worst_direction_delay(direction_delays)
            if worst_direction and worst_value is not None:
                lines.append(
                    f"- \ubc29\ud5a5\ubcc4 \uc9c0\uccb4\ub294 \uad50\ud1b5\ub7c9 \uae30\ubc18 \ucd94\uc815\uce58\uc774\uba70, "
                    f"{_direction_label(worst_direction)} \uc811\uadfc\uc774 {_format_metric(worst_value, seconds_unit)}\ub85c \uac00\uc7a5 \ubd88\ub9ac\ud569\ub2c8\ub2e4."
                )

    lines.append("\ub2e4\uc74c \uc2e4\ud5d8:")
    lines.append("- \uc9c0\uccb4\uac00 \ud070 \uc2dc\ub098\ub9ac\uc624\ub294 \uc2e0\ud638 \ud604\uc2dc, \uc8fc\uae30, \uc635\uc14b, \uc720\uc785\ub7c9 \ubcc0\uacbd \uc804\ud6c4\ub97c \uac19\uc740 \uad50\ucc28\ub85c\uc5d0\uc11c \ub2e4\uc2dc \ube44\uad50\ud558\uba74 \uc6d0\uc778\uc774 \ube68\ub9ac \uc881\ud600\uc9d1\ub2c8\ub2e4.")
    return "\n".join(lines)


def compact_selected_ids(values: list[str], limit: int) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        selected.append(value)
        if len(selected) >= limit:
            break
    return selected


def _scenario_by_id(scenarios: list[dict[str, Any]], scenario_id: object) -> dict[str, Any] | None:
    if scenario_id is None:
        return None
    scenario_id_text = str(scenario_id)
    return next((scenario for scenario in scenarios if str(scenario.get("scenario_id")) == scenario_id_text), None)


def _worst_direction_delay(values: dict[str, Any]) -> tuple[str | None, float | None]:
    candidates = [(direction, coerce_float(value)) for direction, value in values.items()]
    candidates = [(direction, value) for direction, value in candidates if value is not None]
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[1])


def _format_metric(value: object, suffix: str = "", *, digits: int = 1) -> str:
    number = coerce_float(value)
    if number is None:
        return "\ud655\uc778 \uc548 \ub428"
    if digits <= 0:
        return f"{number:,.0f}{suffix}"
    return f"{number:,.{digits}f}{suffix}"


def _direction_label(value: object) -> str:
    labels = {
        "north": "\ubd81\uce21",
        "east": "\ub3d9\uce21",
        "south": "\ub0a8\uce21",
        "west": "\uc11c\uce21",
    }
    return labels.get(str(value), str(value or "\ud655\uc778 \uc548 \ub428"))


def coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_status_overview_answer(tools: dict[str, Any]) -> str:
    dataset = tools.get("active_dataset") or {}
    summary = tools.get("comparison_summary") or {}
    jobs = tools.get("simulation_jobs") or []
    vissim_com = tools.get("vissim_com") or {}

    filename = dataset.get("filename") or "\ud655\uc778 \uc548 \ub428"
    source = dataset.get("source") or "\ud655\uc778 \uc548 \ub428"
    source_label = "\uc5c5\ub85c\ub4dc\ub428" if source == "uploaded" else str(source)
    scenario_count = tools.get("scenario_count")
    selected_ids = summary.get("scenario_ids") or tools.get("selected_scenario_ids") or []
    status_band = summary.get("status_band") or {}

    connected = bool(vissim_com.get("connected"))
    com_label = "\uc5f0\uacb0\ub428" if connected else "\uc5f0\uacb0 \uc548 \ub428"
    com_reason = vissim_com.get("reason") or vissim_com.get("error")
    sim = vissim_com.get("simulation") or {}
    counts = vissim_com.get("counts") or {}
    source_inpx = vissim_com.get("source_inpx")
    vehicle_inputs = vissim_com.get("vehicle_inputs") or []
    signal_controllers = vissim_com.get("signal_controllers") or []

    completed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"completed", "done", "success"}]
    running_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"running", "queued", "pending"}]
    failed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"error", "failed", "fail"}]

    unknown = "\ud655\uc778 \uc548 \ub428"
    ready = "\uc900\ube44\ub428"
    scenario_count_text = str(scenario_count) if scenario_count is not None else unknown
    selected_text = f", \uc120\ud0dd/\ube44\uad50 {', '.join(map(str, selected_ids))}" if selected_ids else ""
    lines = [
        "\uc0c1\ud0dc \uc694\uc57d:",
        f"- \ub370\uc774\ud130\uc14b: {filename} ({source_label})",
        f"- \uc2dc\ub098\ub9ac\uc624: {scenario_count_text}\uac1c{selected_text}",
        f"- VISSIM COM: {com_label}" + (f" ({com_reason})" if com_reason and not connected else ""),
        f"- \uc2dc\ubbac\ub808\uc774\uc158 \uc791\uc5c5: \ucd1d {len(jobs)}\uac1c, \uc644\ub8cc {len(completed_jobs)}\uac1c, \uc9c4\ud589 {len(running_jobs)}\uac1c, \uc624\ub958 {len(failed_jobs)}\uac1c",
        "\ub370\uc774\ud130\uc14b:",
        f"- \ud504\ub85c\uc81d\ud2b8 DB: {yes_no(dataset.get('has_project_db'))}",
        f"- \uc2dc\ub098\ub9ac\uc624 \ud30c\uc77c: {yes_no(dataset.get('has_scenarios'))}",
        f"- \uc2e4\uce21 \uad50\ud1b5\ub7c9: {yes_no(dataset.get('has_real_counts'))}",
    ]

    if status_band:
        status_text = status_band.get("title") or status_band.get("label") or ready
        lines.append(f"- \ube44\uad50 \uc0c1\ud0dc: {status_text}")
    if source != "uploaded":
        lines.append("- \ube44\uad50 \uc0c1\ud0dc: \uc0ac\uc6a9\uc790\uac00 VISSIM INPX/ZIP \ud30c\uc77c\uc744 \uc5c5\ub85c\ub4dc\ud558\uae30 \uc804\uc774\ub77c \ub300\uc2dc\ubcf4\ub4dc \ubd84\uc11d\uc744 \uc7a0\uae08\ub2c8\ub2e4.")
    if connected and source_inpx:
        lines.append(f"- VISSIM \ub124\ud2b8\uc6cc\ud06c \ud30c\uc77c: {Path(str(source_inpx)).name}")
    if connected and sim:
        sim_sec = sim.get("sim_sec", unknown)
        sim_period = sim.get("sim_period", unknown)
        lines.append(f"- VISSIM \uc2dc\ubbac\ub808\uc774\uc158 \uc2dc\uac04: {sim_sec} / {sim_period}")
    if connected and counts:
        lines.append(f"- VISSIM \ub124\ud2b8\uc6cc\ud06c \uac1d\uccb4: \ub9c1\ud06c {counts.get('links', 0)}\uac1c, \uc2e0\ud638\uc81c\uc5b4\uae30 {counts.get('signal_controllers', 0)}\uac1c")
    if connected and vehicle_inputs:
        lines.append(f"- \ucc28\ub7c9 \uc785\ub825 \ubc88\ud638 \uc608\uc2dc: {object_number_summary(vehicle_inputs)}")
    if connected and signal_controllers:
        lines.append(f"- \uc2e0\ud638\uc81c\uc5b4\uae30 \ubc88\ud638 \uc608\uc2dc: {object_number_summary(signal_controllers)}")

    lines.extend(
        [
            "\ud655\uc778\ud560 \uc810:",
            (
                "- \ub300\uc2dc\ubcf4\ub4dc \ubd84\uc11d\uc740 \ud604\uc7ac \uc5c5\ub85c\ub4dc\ub41c \ub370\uc774\ud130\uc14b \uae30\uc900\uc73c\ub85c \uc218\ud589\ub429\ub2c8\ub2e4."
                if source == "uploaded"
                else "- \ud30c\uc77c\uc744 \uc5c5\ub85c\ub4dc\ud558\uae30 \uc804\uc774\ubbc0\ub85c \uae30\ubcf8 \uc0d8\ud50c \uc2dc\ub098\ub9ac\uc624\ub97c \ub300\uc2dc\ubcf4\ub4dc \ubd84\uc11d \uacb0\uacfc\ub85c \ubcf4\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4."
            ),
            (
                "- VISSIM COM\uc774 \uc5f0\uacb0\ub418\uc5b4 \uc2e4\ud589 \uc0c1\ud0dc\uc640 \ub124\ud2b8\uc6cc\ud06c \uac1d\uccb4\ub97c \uc870\ud68c\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4."
                if connected
                else "- VISSIM COM\uc774 \uc5f0\uacb0\ub418\uc9c0 \uc54a\uc740 \uc0c1\ud0dc\uc5d0\uc11c\ub294 \uc2e4\uc2dc\uac04 \uc2dc\ubbac\ub808\uc774\uc158 \uc81c\uc5b4\ub098 \uc2e4\ud589 \uc911 \ubaa8\ub2c8\ud130\ub9c1\uc740 \uc81c\ud55c\ub429\ub2c8\ub2e4."
            ),
            "- \uc2e4\ud589 \uc911\uc778 VISSIM \uc778\uc2a4\ud134\uc2a4\ub97c \uc5f0\uacb0\ud558\ub824\uba74 VISSIM\uc744 \uc2e4\ud589\ud55c \ub4a4 COM \uc0c1\ud0dc\ub97c \ub2e4\uc2dc \ud655\uc778\ud558\uba74 \ub429\ub2c8\ub2e4.",
        ]
    )
    return "\n".join(lines)


def build_status_overview_answer_en(tools: dict[str, Any]) -> str:
    dataset = tools.get("active_dataset") or {}
    summary = tools.get("comparison_summary") or {}
    jobs = tools.get("simulation_jobs") or []
    vissim_com = tools.get("vissim_com") or {}

    filename = dataset.get("filename") or "unknown"
    source = dataset.get("source") or "unknown"
    source_label = "uploaded" if source == "uploaded" else str(source)
    scenario_count = tools.get("scenario_count")
    selected_ids = summary.get("scenario_ids") or tools.get("selected_scenario_ids") or []
    status_band = summary.get("status_band") or {}

    connected = bool(vissim_com.get("connected"))
    com_label = "connected" if connected else "not connected"
    com_reason = english_text(vissim_com.get("reason") or vissim_com.get("error"), "unavailable")
    sim = vissim_com.get("simulation") or {}
    counts = vissim_com.get("counts") or {}
    source_inpx = vissim_com.get("source_inpx")
    vehicle_inputs = vissim_com.get("vehicle_inputs") or []
    signal_controllers = vissim_com.get("signal_controllers") or []

    completed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"completed", "done", "success"}]
    running_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"running", "queued", "pending"}]
    failed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"error", "failed", "fail"}]

    scenario_count_text = str(scenario_count) if scenario_count is not None else "unknown"
    selected_text = f", selected/compared {', '.join(map(str, selected_ids))}" if selected_ids else ""
    lines = [
        "Status summary:",
        f"- Dataset: {filename} ({source_label})",
        f"- Scenarios: {scenario_count_text}{selected_text}",
        f"- VISSIM COM: {com_label}" + (f" ({com_reason})" if com_reason and not connected else ""),
        f"- Simulation jobs: {len(jobs)} total, {len(completed_jobs)} completed, {len(running_jobs)} running, {len(failed_jobs)} failed",
        "Dataset:",
        f"- Project DB: {yes_no_en(dataset.get('has_project_db'))}",
        f"- Scenario files: {yes_no_en(dataset.get('has_scenarios'))}",
        f"- Real traffic counts: {yes_no_en(dataset.get('has_real_counts'))}",
    ]

    if status_band:
        status_text = english_text(status_band.get("title") or status_band.get("label"), "ready")
        lines.append(f"- Comparison status: {status_text}")
    if source != "uploaded":
        lines.append("- Comparison status: dashboard analysis is locked until a VISSIM INPX/ZIP file is uploaded.")
    if connected and source_inpx:
        lines.append(f"- VISSIM network file: {Path(str(source_inpx)).name}")
    if connected and sim:
        sim_sec = sim.get("sim_sec", "unknown")
        sim_period = sim.get("sim_period", "unknown")
        lines.append(f"- VISSIM simulation time: {sim_sec} / {sim_period}")
    if connected and counts:
        lines.append(f"- VISSIM network objects: {counts.get('links', 0)} links, {counts.get('signal_controllers', 0)} signal controllers")
    if connected and vehicle_inputs:
        lines.append(f"- Vehicle input examples: {object_number_summary_en(vehicle_inputs)}")
    if connected and signal_controllers:
        lines.append(f"- Signal controller examples: {object_number_summary_en(signal_controllers)}")

    lines.extend(
        [
            "Checks:",
            (
                "- Dashboard analysis is based on the currently uploaded dataset."
                if source == "uploaded"
                else "- No uploaded file is active, so sample scenarios should not be treated as dashboard analysis results."
            ),
            (
                "- VISSIM COM is connected, so runtime state and network objects can be queried."
                if connected
                else "- VISSIM COM is not connected, so live simulation control and runtime monitoring are limited."
            ),
            "- To connect a running VISSIM instance, start VISSIM and check the COM status again.",
        ]
    )
    return "\n".join(lines)


def build_status_overview_answer_ja(tools: dict[str, Any]) -> str:
    dataset = tools.get("active_dataset") or {}
    summary = tools.get("comparison_summary") or {}
    jobs = tools.get("simulation_jobs") or []
    vissim_com = tools.get("vissim_com") or {}

    filename = dataset.get("filename") or "unknown"
    source = dataset.get("source") or "unknown"
    source_label = "uploaded" if source == "uploaded" else str(source)
    scenario_count = tools.get("scenario_count")
    selected_ids = summary.get("scenario_ids") or tools.get("selected_scenario_ids") or []
    status_band = summary.get("status_band") or {}

    connected = bool(vissim_com.get("connected"))
    com_label = "接続済み" if connected else "未接続"
    com_reason = english_text(vissim_com.get("reason") or vissim_com.get("error"), "unavailable")
    sim = vissim_com.get("simulation") or {}
    counts = vissim_com.get("counts") or {}
    source_inpx = vissim_com.get("source_inpx")
    vehicle_inputs = vissim_com.get("vehicle_inputs") or []
    signal_controllers = vissim_com.get("signal_controllers") or []

    completed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"completed", "done", "success"}]
    running_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"running", "queued", "pending"}]
    failed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"error", "failed", "fail"}]

    scenario_count_text = str(scenario_count) if scenario_count is not None else "unknown"
    selected_text = f"、選択・比較中 {', '.join(map(str, selected_ids))}" if selected_ids else ""
    lines = [
        "状態の要約:",
        f"- データセット: {filename} ({source_label})",
        f"- シナリオ数: {scenario_count_text}{selected_text}",
        f"- VISSIM COM: {com_label}" + (f" ({com_reason})" if com_reason and not connected else ""),
        f"- シミュレーションジョブ: 合計 {len(jobs)} 件、完了 {len(completed_jobs)} 件、実行中 {len(running_jobs)} 件、失敗 {len(failed_jobs)} 件",
        "データセット:",
        f"- プロジェクト DB: {yes_no_ja(dataset.get('has_project_db'))}",
        f"- シナリオファイル: {yes_no_ja(dataset.get('has_scenarios'))}",
        f"- 実測交通量: {yes_no_ja(dataset.get('has_real_counts'))}",
    ]

    if status_band:
        status_text = english_text(status_band.get("title") or status_band.get("label"), "ready")
        lines.append(f"- 比較ステータス: {status_text}")
    if source != "uploaded":
        lines.append("- 比較ステータス: VISSIM の INPX/ZIP ファイルがアップロードされるまでダッシュボード分析はロックされています。")
    if connected and source_inpx:
        lines.append(f"- VISSIM ネットワークファイル: {Path(str(source_inpx)).name}")
    if connected and sim:
        sim_sec = sim.get("sim_sec", "unknown")
        sim_period = sim.get("sim_period", "unknown")
        lines.append(f"- VISSIM シミュレーション時間: {sim_sec} / {sim_period}")
    if connected and counts:
        lines.append(f"- VISSIM ネットワークオブジェクト: リンク {counts.get('links', 0)} 個、信号制御機 {counts.get('signal_controllers', 0)} 個")
    if connected and vehicle_inputs:
        lines.append(f"- Vehicle input の例: {object_number_summary_ja(vehicle_inputs)}")
    if connected and signal_controllers:
        lines.append(f"- 信号制御機の例: {object_number_summary_ja(signal_controllers)}")

    lines.extend(
        [
            "確認事項:",
            (
                "- ダッシュボード分析は現在アップロードされているデータセットに基づいています。"
                if source == "uploaded"
                else "- アップロード済みのファイルがないため、サンプルシナリオをダッシュボード分析結果として扱わないでください。"
            ),
            (
                "- VISSIM COM が接続されているため、実行状態とネットワークオブジェクトを照会できます。"
                if connected
                else "- VISSIM COM が未接続のため、リアルタイムのシミュレーション制御や実行中モニタリングは制限されます。"
            ),
            "- 実行中の VISSIM インスタンスに接続するには、VISSIM を起動してから COM の状態を再確認してください。",
        ]
    )
    return "\n".join(lines)


def build_dashboard_diagnostic_answer(tools: dict[str, Any]) -> str:
    jobs = tools.get("simulation_jobs") or []
    recent_errors = tools.get("recent_errors") or []
    summary = tools.get("comparison_summary") or {}
    vissim_com = tools.get("vissim_com") or {}

    completed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"completed", "done", "success"}]
    running_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"running", "queued", "pending"}]
    invalid_ids = summary.get("invalid_ids") or []
    missing_result_ids = summary.get("missing_result_ids") or []
    selected_ids = summary.get("scenario_ids") or tools.get("selected_scenario_ids") or []
    connected = bool(vissim_com.get("connected"))
    com_reason = vissim_com.get("reason") or vissim_com.get("error")
    selected_text = ", ".join(map(str, selected_ids)) if selected_ids else "\uc120\ud0dd \uc5c6\uc74c"
    com_status_text = "\uc5f0\uacb0\ub428" if connected else "\uc5f0\uacb0 \uc548 \ub428"
    missing_text = ", ".join(map(str, missing_result_ids)) if missing_result_ids else "\uc5c6\uc74c"
    invalid_text = ", ".join(map(str, invalid_ids)) if invalid_ids else "\uc5c6\uc74c"

    lines = [
        "\uc9c4\ub2e8 \uc694\uc57d:",
        f"- \ucd5c\uadfc \uc2dc\ubbac\ub808\uc774\uc158 \uc791\uc5c5 {len(jobs)}\uac1c \uc911 \uc624\ub958 \uc791\uc5c5\uc740 {len(recent_errors)}\uac1c\uc785\ub2c8\ub2e4.",
        f"- \ube44\uad50 \ub300\uc0c1 \uc2dc\ub098\ub9ac\uc624: {selected_text}",
        f"- VISSIM COM: {com_status_text}" + (f" ({com_reason})" if com_reason and not connected else ""),
        "\uadfc\uac70:",
        f"- \uc644\ub8cc\ub41c \uc791\uc5c5 {len(completed_jobs)}\uac1c, \uc9c4\ud589 \uc911\uc778 \uc791\uc5c5 {len(running_jobs)}\uac1c\uc785\ub2c8\ub2e4.",
        f"- \uacb0\uacfc\uac00 \ube60\uc9c4 \uc2dc\ub098\ub9ac\uc624: {missing_text}",
        f"- \uc720\ud6a8\ud558\uc9c0 \uc54a\uc740 \uc2dc\ub098\ub9ac\uc624 ID: {invalid_text}",
        "\ub2e4\uc74c \uc870\uce58:",
    ]

    if recent_errors:
        first_error = recent_errors[0]
        error_text = first_error.get("error") or "\uc138\ubd80 \uba54\uc2dc\uc9c0 \uc5c6\uc74c"
        lines.append(f"- \ucd5c\uc2e0 \uc624\ub958 \uc791\uc5c5 `{first_error.get('name') or first_error.get('id')}`\uc758 \uc624\ub958 \uba54\uc2dc\uc9c0\ub97c \uba3c\uc800 \ud655\uc778\ud558\uc138\uc694: {error_text}")
    else:
        lines.append("- \uc9c0\uae08 \uae30\ub85d\ub41c \uc2dc\ubbac\ub808\uc774\uc158 \uc624\ub958\ub294 \uc5c6\uc73c\ubbc0\ub85c, \ubd84\uc11d \uacb0\uacfc \uc218\uce58\uc758 \uc774\uc0c1\uce58\ub9cc \ucd94\uac00\ub85c \ud655\uc778\ud558\uba74 \ub429\ub2c8\ub2e4.")
    if not connected:
        lines.append("- \uc2e4\ud589 \uc911\uc778 VISSIM\uc744 \uc81c\uc5b4\ud558\uac70\ub098 \uc2e4\uc2dc\uac04 \uc0c1\ud0dc\ub97c \ubcf4\ub824\uba74 VISSIM\uc744 \uba3c\uc800 \uc2e4\ud589\ud55c \ub4a4 COM \uc5f0\uacb0\uc744 \ub2e4\uc2dc \ud655\uc778\ud558\uc138\uc694.")
    return "\n".join(lines)


def build_dashboard_diagnostic_answer_en(tools: dict[str, Any]) -> str:
    jobs = tools.get("simulation_jobs") or []
    recent_errors = tools.get("recent_errors") or []
    summary = tools.get("comparison_summary") or {}
    vissim_com = tools.get("vissim_com") or {}

    completed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"completed", "done", "success"}]
    running_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"running", "queued", "pending"}]
    invalid_ids = summary.get("invalid_ids") or []
    missing_result_ids = summary.get("missing_result_ids") or []
    selected_ids = summary.get("scenario_ids") or tools.get("selected_scenario_ids") or []
    connected = bool(vissim_com.get("connected"))
    com_reason = english_text(vissim_com.get("reason") or vissim_com.get("error"), "unavailable")
    selected_text = ", ".join(map(str, selected_ids)) if selected_ids else "none"
    com_status_text = "connected" if connected else "not connected"
    missing_text = ", ".join(map(str, missing_result_ids)) if missing_result_ids else "none"
    invalid_text = ", ".join(map(str, invalid_ids)) if invalid_ids else "none"

    lines = [
        "Diagnostic summary:",
        f"- Among {len(jobs)} recent simulation jobs, {len(recent_errors)} have recorded errors.",
        f"- Compared scenarios: {selected_text}",
        f"- VISSIM COM: {com_status_text}" + (f" ({com_reason})" if com_reason and not connected else ""),
        "Evidence:",
        f"- Completed jobs: {len(completed_jobs)}, running jobs: {len(running_jobs)}.",
        f"- Scenarios missing results: {missing_text}",
        f"- Invalid scenario IDs: {invalid_text}",
        "Next steps:",
    ]

    if recent_errors:
        first_error = recent_errors[0]
        error_text = english_text(first_error.get("error"), "no detailed message")
        lines.append(f"- Check the latest failed job `{english_job_label(first_error)}` first: {error_text}")
    else:
        lines.append("- No simulation errors are recorded right now; focus on metric outliers or missing scenario results next.")
    if not connected:
        lines.append("- To control VISSIM or inspect live runtime state, start VISSIM and check the COM connection again.")
    return "\n".join(lines)


def build_dashboard_diagnostic_answer_ja(tools: dict[str, Any]) -> str:
    jobs = tools.get("simulation_jobs") or []
    recent_errors = tools.get("recent_errors") or []
    summary = tools.get("comparison_summary") or {}
    vissim_com = tools.get("vissim_com") or {}

    completed_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"completed", "done", "success"}]
    running_jobs = [job for job in jobs if str(job.get("status") or "").lower() in {"running", "queued", "pending"}]
    invalid_ids = summary.get("invalid_ids") or []
    missing_result_ids = summary.get("missing_result_ids") or []
    selected_ids = summary.get("scenario_ids") or tools.get("selected_scenario_ids") or []
    connected = bool(vissim_com.get("connected"))
    com_reason = english_text(vissim_com.get("reason") or vissim_com.get("error"), "unavailable")
    selected_text = ", ".join(map(str, selected_ids)) if selected_ids else "なし"
    com_status_text = "接続済み" if connected else "未接続"
    missing_text = ", ".join(map(str, missing_result_ids)) if missing_result_ids else "なし"
    invalid_text = ", ".join(map(str, invalid_ids)) if invalid_ids else "なし"

    lines = [
        "診断の要約:",
        f"- 最近のシミュレーションジョブ {len(jobs)} 件のうち、{len(recent_errors)} 件にエラーが記録されています。",
        f"- 比較対象シナリオ: {selected_text}",
        f"- VISSIM COM: {com_status_text}" + (f" ({com_reason})" if com_reason and not connected else ""),
        "根拠:",
        f"- 完了したジョブ {len(completed_jobs)} 件、実行中のジョブ {len(running_jobs)} 件。",
        f"- 結果が欠落しているシナリオ: {missing_text}",
        f"- 無効なシナリオ ID: {invalid_text}",
        "次のアクション:",
    ]

    if recent_errors:
        first_error = recent_errors[0]
        error_text = english_text(first_error.get("error"), "no detailed message")
        lines.append(f"- まず最新の失敗ジョブ `{english_job_label(first_error)}` を確認してください: {error_text}")
    else:
        lines.append("- 現時点で記録されているシミュレーションエラーはありません。次は指標の外れ値やシナリオ結果の欠落を確認してください。")
    if not connected:
        lines.append("- VISSIM を制御したり実行中の状態を確認するには、VISSIM を起動してから COM 接続を再確認してください。")
    return "\n".join(lines)


def object_number_summary(rows: list[dict[str, Any]], limit: int = 8) -> str:
    numbers: list[str] = []
    for row in rows:
        value = row.get("No")
        if value is None:
            continue
        numbers.append(str(value))
    if not numbers:
        return "\ud655\uc778 \uc548 \ub428"
    suffix = f", ... (+{len(numbers) - limit})" if len(numbers) > limit else ""
    return ", ".join(numbers[:limit]) + suffix


def object_number_summary_en(rows: list[dict[str, Any]], limit: int = 8) -> str:
    numbers: list[str] = []
    for row in rows:
        value = row.get("No")
        if value is None:
            continue
        numbers.append(str(value))
    if not numbers:
        return "unknown"
    suffix = f", ... (+{len(numbers) - limit})" if len(numbers) > limit else ""
    return ", ".join(numbers[:limit]) + suffix


def object_number_summary_ja(rows: list[dict[str, Any]], limit: int = 8) -> str:
    numbers: list[str] = []
    for row in rows:
        value = row.get("No")
        if value is None:
            continue
        numbers.append(str(value))
    if not numbers:
        return "未確認"
    suffix = f", ... (+{len(numbers) - limit})" if len(numbers) > limit else ""
    return ", ".join(numbers[:limit]) + suffix


def build_prompt_simulation_context(message: str, allow_tool_execution: bool) -> dict[str, Any]:
    command = parse_prompt_simulation_command(message)
    if not command["recognized"]:
        return {"recognized": False}

    capabilities = get_prompt_simulation_capabilities()
    result: dict[str, Any] = {
        "recognized": True,
        "executed": False,
        "requires_confirmation": not allow_tool_execution,
        "params": command["params"],
        "changes": command["changes"],
        "warnings": command["warnings"],
        "capabilities": capabilities,
        "job": None,
        "reason": None,
    }

    if not allow_tool_execution:
        result["reason"] = "\uc2dc\ubbac\ub808\uc774\uc158 \uc2e4\ud589 \uad8c\ud55c\uc774 \uaebc\uc838 \uc788\uc5b4 \ud30c\ub77c\ubbf8\ud130\ub9cc \uc900\ube44\ud588\uc2b5\ub2c8\ub2e4."
        return result

    if not capabilities.get("can_run"):
        result["reason"] = capabilities.get("reason") or "\ud604\uc7ac \ud658\uacbd\uc5d0\uc11c\ub294 VISSIM \uc2dc\ubbac\ub808\uc774\uc158\uc744 \uc2e4\ud589\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4."
        return result

    try:
        job = start_prompt_simulation_job(command["params"])
    except Exception as exc:
        result["reason"] = str(exc)
        return result

    result["executed"] = True
    result["requires_confirmation"] = False
    result["job"] = job
    result["reason"] = None
    return result


def parse_prompt_simulation_command(message: str) -> dict[str, Any]:
    text = message.strip()
    lowered = text.lower()
    changes: list[str] = []
    warnings: list[str] = []

    duration = parse_prompt_duration_seconds(lowered, warnings)
    seed = parse_prompt_seed(lowered)
    vehicle_inputs = parse_prompt_numeric_map(
        lowered,
        [
            r"(?:vehicle\s*input|vehinput|input|\ucc28\ub7c9\s*\uc785\ub825|\uc720\uc785\ub7c9|\uad50\ud1b5\ub7c9)\s*(?:no\.?|#|\ubc88\ud638|\ubc88)?\s*(\d+)\D{0,18}(\d+(?:\.\d+)?)",
        ],
        value_type=float,
    )
    signal_cycles = parse_prompt_numeric_map(
        lowered,
        [
            r"(?:signal\s*controller|signal|sc|\uc2e0\ud638\uc81c\uc5b4\uae30|\uc2e0\ud638|\ucee8\ud2b8\ub864\ub7ec)\s*(?:no\.?|#|\ubc88\ud638|\ubc88)?\s*(\d+).{0,40}(?:cycle|\uc8fc\uae30)\D{0,12}(\d+)",
            r"(?:cycle|\uc8fc\uae30)\D{0,12}(\d+).{0,40}(?:signal\s*controller|signal|sc|\uc2e0\ud638\uc81c\uc5b4\uae30|\uc2e0\ud638|\ucee8\ud2b8\ub864\ub7ec)\D{0,8}(\d+)",
        ],
        value_type=int,
        reverse_patterns={1},
    )
    signal_offsets = parse_prompt_numeric_map(
        lowered,
        [
            r"(?:signal\s*controller|signal|sc|\uc2e0\ud638\uc81c\uc5b4\uae30|\uc2e0\ud638|\ucee8\ud2b8\ub864\ub7ec)\s*(?:no\.?|#|\ubc88\ud638|\ubc88)?\s*(\d+).{0,40}(?:offset|\uc635\uc14b|\uc624\ud504\uc14b)\D{0,12}(\d+)",
            r"(?:offset|\uc635\uc14b|\uc624\ud504\uc14b)\D{0,12}(\d+).{0,40}(?:signal\s*controller|signal|sc|\uc2e0\ud638\uc81c\uc5b4\uae30|\uc2e0\ud638|\ucee8\ud2b8\ub864\ub7ec)\D{0,8}(\d+)",
        ],
        value_type=int,
        reverse_patterns={1},
    )
    scenario_id = parse_prompt_scenario_id(text)

    if duration is not None:
        changes.append(f"\uc2e4\ud589 \uc2dc\uac04 {duration}\ucd08")
    if seed is not None:
        changes.append(f"\ub79c\ub364 \uc2dc\ub4dc {seed}")
    for key, value in vehicle_inputs.items():
        changes.append(f"\ucc28\ub7c9 \uc785\ub825 {key}\ubc88 {value:g}")
    for key, value in signal_cycles.items():
        changes.append(f"\uc2e0\ud638 {key}\ubc88 \uc8fc\uae30 {value}\ucd08")
    for key, value in signal_offsets.items():
        changes.append(f"\uc2e0\ud638 {key}\ubc88 \uc624\ud504\uc14b {value}\ucd08")
    if scenario_id:
        changes.append(f"\uae30\uc900 \uc2dc\ub098\ub9ac\uc624 {scenario_id}")

    has_run = has_prompt_simulation_run_term(lowered)
    recognized = has_run and (bool(changes) or is_prompt_simulation_request(lowered))
    params = {
        "name": prompt_simulation_name(text),
        "scenario_id": scenario_id,
        "sim_duration": duration or PROMPT_SIMULATION_DEFAULT_DURATION,
        "random_seed": seed if seed is not None else PROMPT_SIMULATION_DEFAULT_SEED,
        "vehicle_inputs": vehicle_inputs,
        "signal_cycles": signal_cycles,
        "signal_offsets": signal_offsets,
        "incidents": [],
    }
    if duration is None and has_run:
        changes.append(f"\uc2e4\ud589 \uc2dc\uac04 {PROMPT_SIMULATION_DEFAULT_DURATION}\ucd08(\uae30\ubcf8\uac12)")
    if seed is None and has_run:
        changes.append(f"\ub79c\ub364 \uc2dc\ub4dc {PROMPT_SIMULATION_DEFAULT_SEED}(\uae30\ubcf8\uac12)")

    return {
        "recognized": recognized,
        "params": params,
        "changes": changes,
        "warnings": warnings,
    }


def parse_prompt_duration_seconds(text: str, warnings: list[str]) -> int | None:
    patterns = [
        r"(?:duration|sim(?:ulation)?\s*(?:period|time|duration)|\uc2e4\ud589\s*\uc2dc\uac04|\uc2dc\ubbac\ub808\uc774\uc158\s*\uc2dc\uac04|\uae30\uac04|\ub3d9\uc548)\D{0,16}(\d+(?:\.\d+)?)\s*(\ucd08|sec|secs|second|seconds|s|\ubd84|min|mins|minute|minutes)?",
        r"(\d+(?:\.\d+)?)\s*(\ucd08|sec|secs|second|seconds|s|\ubd84|min|mins|minute|minutes)\s*(?:\ub3d9\uc548\s*)?(?:\uc2e4\ud589|\ub3cc\ub824|run|simulate)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = (match.group(2) or "\ucd08").lower()
        seconds = int(round(value * 60)) if unit in {"\ubd84", "min", "mins", "minute", "minutes"} else int(round(value))
        if seconds < 60:
            warnings.append(f"\uc694\uccad\ud55c \uc2e4\ud589 \uc2dc\uac04 {seconds}\ucd08\ub294 \ud5c8\uc6a9 \ubc94\uc704 \uc544\ub798\ub77c 60\ucd08\ub85c \ubcf4\uc815\ud588\uc2b5\ub2c8\ub2e4.")
            return 60
        if seconds > 86400:
            warnings.append(f"\uc694\uccad\ud55c \uc2e4\ud589 \uc2dc\uac04 {seconds}\ucd08\ub294 \ud5c8\uc6a9 \ubc94\uc704\ub97c \ub118\uc5b4 86400\ucd08\ub85c \ubcf4\uc815\ud588\uc2b5\ub2c8\ub2e4.")
            return 86400
        return seconds
    return None


def parse_prompt_seed(text: str) -> int | None:
    match = re.search(r"(?:random\s*seed|seed|\ub79c\ub364\s*\uc2dc\ub4dc|\uc2dc\ub4dc)\D{0,12}(\d+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return max(0, min(int(match.group(1)), 2_147_483_647))


def parse_prompt_scenario_id(text: str) -> str | None:
    match = re.search(r"\b(SIM_[A-Za-z0-9_-]+|S\d{3,})\b", text, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def parse_prompt_numeric_map(
    text: str,
    patterns: list[str],
    *,
    value_type: type,
    reverse_patterns: set[int] | None = None,
) -> dict[int, Any]:
    values: dict[int, Any] = {}
    reverse_patterns = reverse_patterns or set()
    for index, pattern in enumerate(patterns):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            first = match.group(1)
            second = match.group(2)
            key_text, value_text = (second, first) if index in reverse_patterns else (first, second)
            key = int(float(key_text))
            if value_type is int:
                value = int(round(float(value_text)))
                value = max(0, min(value, 86400))
            else:
                value = max(0.0, min(float(value_text), 100000.0))
            values[key] = value
    return values


def prompt_simulation_name(message: str) -> str:
    compact = re.sub(r"\s+", " ", message).strip()
    return f"prompt: {compact[:60]}" if compact else "prompt simulation"


def build_prompt_simulation_answer(result: dict[str, Any]) -> str:
    params = result.get("params") or {}
    changes = result.get("changes") or []
    warnings = result.get("warnings") or []
    job = result.get("job") or {}
    scenario_result_id = job.get("scenario_result_id") or "\uc644\ub8cc \ud6c4 \uc0dd\uc131"

    if result.get("executed"):
        lines = [
            "\uc2e4\ud589 \uc2dc\uc791:",
            f"- \uc791\uc5c5 ID: {job.get('id')}",
            f"- \uacb0\uacfc \uc2dc\ub098\ub9ac\uc624: {scenario_result_id}",
            "- \uc644\ub8cc\ub418\uba74 \ucc44\ud305 \uacb0\uacfc \uce74\ub4dc\uc640 \uc2dc\ubbac\ub808\uc774\uc158 \ub300\uc2dc\ubcf4\ub4dc\uc5d0\uc11c \uc18d\ub3c4, \uc9c0\uc5f0, \uc815\uc9c0, \ucd1d \uc5ec\ud589\uc2dc\uac04 \uc9c0\ud45c\ub97c \ud655\uc778\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.",
        ]
    elif result.get("requires_confirmation"):
        lines = [
            "\uc2e4\ud589 \uc900\ube44:",
            "- \uc694\uccad\ud55c \ud30c\ub77c\ubbf8\ud130\ub294 \uc77d\uc5c8\uc9c0\ub9cc, \ud604\uc7ac \ucc44\ud305 \uc694\uccad\uc740 \uc2e4\ud589 \uad8c\ud55c\uc774 \uaebc\uc838 \uc788\uc5b4 VISSIM\uc744 \uc870\uc791\ud558\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4.",
        ]
    else:
        reason_text = result.get("reason") or "\ud604\uc7ac \ud658\uacbd\uc5d0\uc11c\ub294 \uc2e4\ud589\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4."
        lines = [
            "\uc2e4\ud589 \ubcf4\ub958:",
            f"- {reason_text}",
        ]

    if changes:
        lines.append("\uc801\uc6a9 \ud30c\ub77c\ubbf8\ud130:")
        lines.extend(f"- {change}" for change in changes[:8])
    else:
        lines.append(f"\uc801\uc6a9 \ud30c\ub77c\ubbf8\ud130: \uc2e4\ud589 \uc2dc\uac04 {params.get('sim_duration')}\ucd08, \ub79c\ub364 \uc2dc\ub4dc {params.get('random_seed')}")
    if warnings:
        lines.append("\uc8fc\uc758:")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def build_prompt_simulation_answer_en(result: dict[str, Any]) -> str:
    params = result.get("params") or {}
    warnings = result.get("warnings") or []
    job = result.get("job") or {}
    scenario_result_id = job.get("scenario_result_id") or "created after completion"

    if result.get("executed"):
        lines = [
            "Execution started:",
            f"- Job ID: {job.get('id')}",
            f"- Result scenario: {scenario_result_id}",
            "- When it completes, the chat result card and simulation dashboard will show speed, delay, stops, and total travel-time metrics.",
        ]
    elif result.get("requires_confirmation"):
        lines = [
            "Ready to run:",
            "- I parsed the requested parameters, but this chat request did not include execution permission, so VISSIM was not modified.",
        ]
    else:
        lines = [
            "Execution blocked:",
            f"- {prompt_simulation_reason_en(result)}",
        ]

    param_lines = prompt_simulation_parameter_lines_en(params)
    if param_lines:
        lines.append("Applied parameters:")
        lines.extend(param_lines)
    safe_warnings = [str(warning) for warning in warnings if not re.search(r"[\uac00-\ud7a3]", str(warning))]
    if safe_warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in safe_warnings)
    return "\n".join(lines)


def build_prompt_simulation_answer_ja(result: dict[str, Any]) -> str:
    params = result.get("params") or {}
    warnings = result.get("warnings") or []
    job = result.get("job") or {}
    scenario_result_id = job.get("scenario_result_id") or "\u5b8c\u4e86\u5f8c\u306b\u751f\u6210"

    if result.get("executed"):
        lines = [
            "\u5b9f\u884c\u958b\u59cb:",
            f"- Job ID: {job.get('id')}",
            f"- \u7d50\u679c\u30b7\u30ca\u30ea\u30aa: {scenario_result_id}",
            "- \u5b8c\u4e86\u3059\u308b\u3068\u3001\u30c1\u30e3\u30c3\u30c8\u306e\u7d50\u679c\u30ab\u30fc\u30c9\u3068\u30b7\u30df\u30e5\u30ec\u30fc\u30b7\u30e7\u30f3\u30c0\u30c3\u30b7\u30e5\u30dc\u30fc\u30c9\u306b\u901f\u5ea6\u30fb\u9045\u5ef6\u30fb\u505c\u6b62\u30fb\u7dcf\u65c5\u884c\u6642\u9593\u306e\u6307\u6a19\u304c\u8868\u793a\u3055\u308c\u307e\u3059\u3002",
        ]
    elif result.get("requires_confirmation"):
        lines = [
            "\u5b9f\u884c\u6e96\u5099\u5b8c\u4e86:",
            "- \u8981\u6c42\u3055\u308c\u305f\u30d1\u30e9\u30e1\u30fc\u30bf\u306f\u8aad\u307f\u53d6\u308a\u307e\u3057\u305f\u304c\u3001\u3053\u306e\u30c1\u30e3\u30c3\u30c8\u8981\u6c42\u306b\u306f\u5b9f\u884c\u6a29\u9650\u304c\u542b\u307e\u308c\u3066\u3044\u306a\u3044\u305f\u3081\u3001VISSIM \u306f\u5909\u66f4\u3057\u3066\u3044\u307e\u305b\u3093\u3002",
        ]
    else:
        lines = [
            "\u5b9f\u884c\u4fdd\u7559:",
            f"- {prompt_simulation_reason_ja(result)}",
        ]

    param_lines = prompt_simulation_parameter_lines_ja(params)
    if param_lines:
        lines.append("\u9069\u7528\u30d1\u30e9\u30e1\u30fc\u30bf:")
        lines.extend(param_lines)
    safe_warnings = [str(warning) for warning in warnings if not re.search(r"[\uac00-\ud7a3]", str(warning))]
    if safe_warnings:
        lines.append("\u6ce8\u610f:")
        lines.extend(f"- {warning}" for warning in safe_warnings)
    return "\n".join(lines)


def prompt_simulation_parameter_lines_ja(params: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    vehicle_inputs = params.get("vehicle_inputs") or {}
    signal_cycles = params.get("signal_cycles") or {}
    signal_offsets = params.get("signal_offsets") or {}
    if params.get("sim_duration") is not None:
        lines.append(f"- \u30b7\u30df\u30e5\u30ec\u30fc\u30b7\u30e7\u30f3\u6642\u9593: {params.get('sim_duration')} \u79d2")
    if params.get("random_seed") is not None:
        lines.append(f"- Random seed: {params.get('random_seed')}")
    for number, value in list(vehicle_inputs.items())[:8]:
        lines.append(f"- Vehicle input {number}: {value}")
    for number, value in list(signal_cycles.items())[:8]:
        lines.append(f"- \u4fe1\u53f7\u5236\u5fa1\u6a5f {number} \u306e\u30b5\u30a4\u30af\u30eb: {value} \u79d2")
    for number, value in list(signal_offsets.items())[:8]:
        lines.append(f"- \u4fe1\u53f7\u5236\u5fa1\u6a5f {number} \u306e\u30aa\u30d5\u30bb\u30c3\u30c8: {value} \u79d2")
    return lines


def prompt_simulation_reason_ja(result: dict[str, Any]) -> str:
    reason = str(result.get("reason") or "").strip()
    if reason and not contains_hangul(reason):
        return reason
    if result.get("requires_confirmation"):
        return "\u3053\u306e\u30c1\u30e3\u30c3\u30c8\u8981\u6c42\u3067\u306f\u5b9f\u884c\u6a29\u9650\u304c\u7121\u52b9\u306b\u306a\u3063\u3066\u3044\u307e\u3059\u3002"
    return "\u73fe\u5728\u306e\u74b0\u5883\u3067\u306f VISSIM \u30b7\u30df\u30e5\u30ec\u30fc\u30b7\u30e7\u30f3\u3092\u5b9f\u884c\u3067\u304d\u307e\u305b\u3093\u3002"


def prompt_simulation_parameter_lines_en(params: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    vehicle_inputs = params.get("vehicle_inputs") or {}
    signal_cycles = params.get("signal_cycles") or {}
    signal_offsets = params.get("signal_offsets") or {}
    if params.get("sim_duration") is not None:
        lines.append(f"- Simulation duration: {params.get('sim_duration')} sec")
    if params.get("random_seed") is not None:
        lines.append(f"- Random seed: {params.get('random_seed')}")
    for number, value in list(vehicle_inputs.items())[:8]:
        lines.append(f"- Vehicle input {number}: {value}")
    for number, value in list(signal_cycles.items())[:8]:
        lines.append(f"- Signal controller {number} cycle: {value} sec")
    for number, value in list(signal_offsets.items())[:8]:
        lines.append(f"- Signal controller {number} offset: {value} sec")
    return lines


def prompt_simulation_reason_en(result: dict[str, Any]) -> str:
    reason = str(result.get("reason") or "").strip()
    if reason and not contains_hangul(reason):
        return reason
    if result.get("requires_confirmation"):
        return "Execution permission is disabled for this chat request."
    return "The current environment cannot run a VISSIM simulation."


def get_prompt_simulation_capabilities() -> dict[str, Any]:
    from routers.simulations import _simulation_capabilities

    return _simulation_capabilities()


def start_prompt_simulation_job(params: dict[str, Any]) -> dict[str, Any]:
    from routers.simulations import SimParams, start_simulation_job

    return start_simulation_job(SimParams(**params))


def yes_no(value: Any) -> str:
    if value is True:
        return "\uc788\uc74c"
    if value is False:
        return "\uc5c6\uc74c"
    return "\ud655\uc778 \uc548 \ub428"


def yes_no_en(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def yes_no_ja(value: Any) -> str:
    if value is True:
        return "あり"
    if value is False:
        return "なし"
    return "未確認"


def contains_hangul(value: Any) -> bool:
    return bool(re.search(r"[\uac00-\ud7a3]", str(value or "")))


def english_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if text and not contains_hangul(text):
        return text
    return fallback


def english_job_label(job: dict[str, Any]) -> str:
    job_id = str(job.get("id") or "").strip()
    name = str(job.get("name") or "").strip()
    if name and not contains_hangul(name):
        return name
    if job_id:
        return english_job_id_label(job_id)
    return "latest failed job"


def english_job_id_label(job_id: str) -> str:
    return job_id if job_id.lower().startswith("job") else f"job {job_id}"


def sanitize_context_for_english(value: Any, key: str = "") -> Any:
    if isinstance(value, list):
        return [sanitize_context_for_english(item, key) for item in value]
    if isinstance(value, dict):
        return {item_key: sanitize_context_for_english(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, str) and contains_hangul(value):
        if key in {"node_name", "name"}:
            return "Korean source label omitted in English mode"
        if key in {"message", "title", "label", "detail", "error", "reason"}:
            return "Korean source text omitted in English mode."
        return "Korean source text omitted in English mode."
    return value


def contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def normalize_response_language(value: str | None, message: str = "") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"ko", "kr", "korean"}:
        return "ko"
    if normalized in {"en", "eng", "english"}:
        return "en"
    if normalized in {"ja", "jp", "jpn", "japanese"}:
        return "ja"
    return infer_message_language(message)


def infer_message_language(message: str) -> str:
    hangul_count = len(re.findall(r"[\uac00-\ud7a3]", message))
    kana_count = len(re.findall(r"[\u3040-\u30ff]", message))  # hiragana + katakana = Japanese
    latin_count = len(re.findall(r"[A-Za-z]", message))
    if kana_count > 0:
        return "ja"
    if hangul_count > 0:
        if latin_count >= 8 and latin_count >= hangul_count * 2:
            return "en"
        return "ko"
    if latin_count > 0:
        return "en"
    return "ko"


def is_simple_greeting(message: str) -> bool:
    normalized = re.sub(r"[^\w\uac00-\ud7a3]+", " ", message, flags=re.UNICODE).strip().lower()
    return normalized in {
        "hello",
        "hi",
        "hey",
        "hi there",
        "hello there",
        "\uc548\ub155",
        "\uc548\ub155\ud558\uc138\uc694",
        "\ud558\uc774",
    }


def build_greeting_answer(language: str | bool) -> str:
    # Accepts a language code ("ko"/"en"/"ja"); legacy bool True == English.
    if language is True or language == "en":
        return "Hello. I can help you inspect the VISSIM dashboard, explain scenarios or intersections, diagnose simulation issues, and prepare safe parameter-run requests."
    if language == "ja":
        return "こんにちは。VISSIM ダッシュボードの状態確認、シナリオや交差点の分析、シミュレーションの問題診断、安全なパラメータ実行リクエストのお手伝いができます。"
    return "\uc548\ub155\ud558\uc138\uc694. VISSIM \ub300\uc2dc\ubcf4\ub4dc \uc0c1\ud0dc, \uc2dc\ub098\ub9ac\uc624/\uad50\ucc28\ub85c \ubd84\uc11d, \uc2dc\ubbac\ub808\uc774\uc158 \uc624\ub958 \uc9c4\ub2e8, \uc548\uc804\ud55c \ud30c\ub77c\ubbf8\ud130 \uc2e4\ud589 \uc694\uccad\uc744 \ub3c4\uc640\ub4dc\ub9b4 \uc218 \uc788\uc2b5\ub2c8\ub2e4."


def is_assistant_meta_question(message: str) -> bool:
    normalized = normalize_question_text(message)
    return is_assistant_identity_question(normalized) or is_model_identity_question(normalized)


def is_assistant_identity_question(message: str) -> bool:
    normalized = normalize_question_text(message)
    return contains_any(
        normalized,
        [
            "\ub108 \ub204\uad6c",
            "\ub10c \ub204\uad6c",
            "\ub2f9\uc2e0 \ub204\uad6c",
            "\ub108\ub294\ub204\uad6c",
            "\ub10c\ub204\uad6c",
            "\ub2f9\uc2e0\ub204\uad6c",
            "\ub204\uad6c\uc57c",
            "\uc815\uccb4",
            "\ub108\ub294 \ub204\uad6c",
            "who are you",
            "what are you",
            "your identity",
        ],
    )


def is_model_identity_question(message: str) -> bool:
    normalized = normalize_question_text(message)
    return contains_any(
        normalized,
        [
            "\ubb34\uc2a8 \ubaa8\ub378",
            "\uc5b4\ub5a4 \ubaa8\ub378",
            "\ubaa8\ub378\uc774\uc57c",
            "\ubaa8\ub378\uc774\ub0d0",
            "\ubaa8\ub378 \uc774\ub984",
            "what model",
            "which model",
            "model are you",
            "model name",
            "qwen",
        ],
    )


def normalize_question_text(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def build_assistant_meta_answer(
    status: dict[str, Any],
    english: bool,
    include_model: bool = False,
    japanese: bool = False,
) -> str:
    model = str(status.get("model") or DEFAULT_MODEL_LABEL)
    mode = str(status.get("mode") or "unknown")
    ready = bool(status.get("ready"))
    worker_running = bool(status.get("worker_running"))

    if japanese:
        if include_model:
            readiness = "準備完了" if ready else "未準備"
            worker = "実行中" if worker_running else "未実行"
            return (
                f"回答: 私は VISSIM Web ダッシュボードのアシスタントです。現在設定されているローカルモデルは {model} です。\n"
                f"状態: 実行モードは {mode}、モデル状態は {readiness}、worker は {worker} です。\n"
                "用途: 現在の VISSIM プラットフォームのコンテキストから、ダッシュボード・シナリオ・交差点・検証・ドキュメント・シミュレーションの安全に関する質問に回答します。"
            )
        return (
            "回答: 私はこの VISSIM Web ダッシュボードに組み込まれた AI アシスタントです。\n"
            f"状態: 回答の生成が必要なときは設定されたバックエンドモデル ({model}) を使用し、簡単な状態や安全に関する質問はダッシュボードのデータから直接回答します。\n"
            "用途: シナリオ、交差点、検証結果、VISSIM ドキュメント、シミュレーションエラー、安全なパラメータ実行要求について質問してください。"
        )

    if english:
        if include_model:
            readiness = "ready" if ready else "not ready"
            worker = "running" if worker_running else "not running"
            return (
                f"Answer: I am the VISSIM Web Dashboard assistant. The configured local model is {model}.\n"
                f"Status: Runtime mode is {mode}; model status is {readiness}; worker is {worker}.\n"
                "Use: I answer dashboard, scenario, intersection, validation, document, and simulation-safety questions from the current VISSIM platform context."
            )
        return (
            "Answer: I am the AI assistant built into this VISSIM Web Dashboard.\n"
            f"Status: I use the configured assistant backend ({model}) when generation is needed, and I answer simple status or safety questions directly from dashboard data.\n"
            "Use: Ask me about scenarios, intersections, validation results, VISSIM documents, simulation errors, or safe parameter-run requests."
        )

    if include_model:
        readiness = "\uc900\ube44\ub428" if ready else "\uc900\ube44 \uc548 \ub428"
        worker = "\uc2e4\ud589 \uc911" if worker_running else "\uc2e4\ud589 \uc548 \ub428"
        return (
            f"\ub2f5\ubcc0: \uc800\ub294 VISSIM \uc6f9 \ub300\uc2dc\ubcf4\ub4dc \uc5b4\uc2dc\uc2a4\ud134\ud2b8\uc785\ub2c8\ub2e4. \ud604\uc7ac \uc124\uc815\ub41c \ub85c\uceec \ubaa8\ub378\uc740 {model}\uc785\ub2c8\ub2e4.\n"
            f"\uc0c1\ud0dc: \uc2e4\ud589 \ubaa8\ub4dc\ub294 {mode}\uc774\uace0, \ubaa8\ub378 \uc0c1\ud0dc\ub294 {readiness}, worker\ub294 {worker}\uc785\ub2c8\ub2e4.\n"
            "\ud65c\uc6a9: \ud604\uc7ac VISSIM \ud50c\ub7ab\ud3fc \ucee8\ud14d\uc2a4\uc5d0\uc11c \ub300\uc2dc\ubcf4\ub4dc, \uc2dc\ub098\ub9ac\uc624, \uad50\ucc28\ub85c, \uac80\uc99d, \ubb38\uc11c, \uc2dc\ubbac\ub808\uc774\uc158 \uc548\uc804 \uc9c8\ubb38\uc5d0 \ub2f5\ud569\ub2c8\ub2e4."
        )
    return (
        "\ub2f5\ubcc0: \uc800\ub294 \uc774 VISSIM \uc6f9 \ub300\uc2dc\ubcf4\ub4dc\uc5d0 \ub0b4\uc7a5\ub41c AI \uc5b4\uc2dc\uc2a4\ud134\ud2b8\uc785\ub2c8\ub2e4.\n"
        f"\uc0c1\ud0dc: \ub2f5\ubcc0 \uc0dd\uc131\uc774 \ud544\uc694\ud560 \ub54c\ub294 \uc124\uc815\ub41c \ubc31\uc5d4\ub4dc \ubaa8\ub378({model})\uc744 \uc0ac\uc6a9\ud558\uace0, \uac04\ub2e8\ud55c \uc0c1\ud0dc/\uc548\uc804 \uc9c8\ubb38\uc740 \ub300\uc2dc\ubcf4\ub4dc \ub370\uc774\ud130\ub85c \uc989\uc2dc \ub2f5\ud569\ub2c8\ub2e4.\n"
        "\ud65c\uc6a9: \uc2dc\ub098\ub9ac\uc624, \uad50\ucc28\ub85c, \uac80\uc99d \uacb0\uacfc, VISSIM \ubb38\uc11c, \uc2dc\ubbac\ub808\uc774\uc158 \uc624\ub958, \uc548\uc804\ud55c \ud30c\ub77c\ubbf8\ud130 \uc2e4\ud589 \uc694\uccad\uc744 \ubb3c\uc5b4\ubcf4\uba74 \ub429\ub2c8\ub2e4."
    )


def compact_provider_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": status.get("model"),
        "model_path_exists": status.get("model_path_exists"),
        "qwen_python_exists": status.get("qwen_python_exists"),
        "mode": status.get("mode"),
        "state": status.get("state"),
        "ready": status.get("ready"),
        "worker_available": status.get("worker_available"),
        "worker_running": status.get("worker_running"),
        "worker_error": None if status.get("ready") else status.get("worker_error"),
    }


def build_assistant_exception_response(
    message: str,
    response_language: str | None,
    exc: Exception,
    error_id: str,
    model_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        intent = classify_intent(message)
    except Exception:
        intent = "general"
    try:
        normalized_language = normalize_response_language(response_language, message)
    except Exception:
        normalized_language = "ko"
    try:
        safety = build_safety_result(intent, message, allow_tool_execution=False)
    except Exception:
        safety = {
            "checked": True,
            "allowed": False,
            "requires_confirmation": False,
            "reason": "Assistant request failed before the safety check completed.",
        }

    status = model_status or minimal_assistant_status(error_id)
    return {
        "intent": intent,
        "answer": build_assistant_exception_answer(
            error_id,
            english=normalized_language == "en",
            japanese=normalized_language == "ja",
        ),
        "model": DEFAULT_MODEL_LABEL,
        "model_status": status,
        "runtime": {
            "mode": "error-fallback",
            "error_id": error_id,
            "error": type(exc).__name__,
            "recoverable": True,
        },
        "sources": [],
        "tool_results": {
            "assistant_error": {
                "error_id": error_id,
                "type": type(exc).__name__,
                "recoverable": True,
            }
        },
        "safety": safety,
        "answer_format": answer_format_payload(intent, normalized_language),
    }


def build_assistant_exception_answer(error_id: str, english: bool, japanese: bool = False) -> str:
    if japanese:
        return "\n".join(
            [
                "回答:",
                "AI モデルの応答経路で一時的な問題が発生したため、サーバーエラーを返す代わりに安全なダッシュボードフォールバックに切り替えました。",
                "根拠:",
                f"バックエンドのログ確認用にエラー ID `{error_id}` を記録しました。",
                "確認が必要:",
                "モデルの worker が準備できていれば、アシスタントの状態を更新してから再試行すると生成された回答を受け取れます。",
            ]
        )
    if english:
        return "\n".join(
            [
                "Answer:",
                "The AI model path had a temporary problem, so I switched to a safe dashboard fallback instead of returning a server error.",
                "Evidence:",
                f"Error ID `{error_id}` was recorded for backend log lookup.",
                "Needs confirmation:",
                "Refresh the assistant status and retry for a generated answer if the model worker is ready.",
            ]
        )
    return "\n".join(
        [
            "\ub2f5\ubcc0:",
            "AI \ubaa8\ub378 \uc751\ub2f5 \uacbd\ub85c\uc5d0\uc11c \uc77c\uc2dc\uc801\uc778 \ubb38\uc81c\uac00 \ubc1c\uc0dd\ud574 \uc11c\ubc84 \uc624\ub958 \ub300\uc2e0 \uc548\uc804\ud55c \ub300\uc2dc\ubcf4\ub4dc \ud3f4\ubc31\uc73c\ub85c \uc804\ud658\ud588\uc2b5\ub2c8\ub2e4.",
            "\uadfc\uac70:",
            f"\ubc31\uc5d4\ub4dc \ub85c\uadf8 \ud655\uc778\uc6a9 \uc624\ub958 ID `{error_id}`\uac00 \uae30\ub85d\ub418\uc5c8\uc2b5\ub2c8\ub2e4.",
            "\ud655\uc778 \ud544\uc694:",
            "\ubaa8\ub378 worker\uac00 \uc900\ube44\ub418\uc5c8\ub294\uc9c0 \uc0c1\ud0dc\ub97c \uc0c8\ub85c\uace0\uce68\ud55c \ub4a4 \ub2e4\uc2dc \uc694\uccad\ud558\uba74 \uc0dd\uc131 \ub2f5\ubcc0\uc744 \ubc1b\uc744 \uc218 \uc788\uc2b5\ub2c8\ub2e4.",
        ]
    )


def minimal_assistant_status(error_id: str) -> dict[str, Any]:
    return {
        "model": DEFAULT_MODEL_LABEL,
        "model_path": "",
        "model_path_exists": False,
        "qwen_python_exists": False,
        "endpoint_configured": False,
        "in_process_enabled": False,
        "worker_available": False,
        "worker_running": False,
        "worker_error": f"error_id={error_id}",
        "mode": "error-fallback",
        "state": "failed",
        "ready": False,
    }


def is_capabilities_question(text: str) -> bool:
    return contains_any(
        text,
        [
            "\ubb58 \ud560 \uc218",
            "\ubb50 \ud560 \uc218",
            "\ubb34\uc5c7\uc744 \ud560 \uc218",
            "\uc5b4\ub5a4 \uae30\ub2a5",
            "\uac00\ub2a5\ud55c \uae30\ub2a5",
            "\ub3c4\uc6c0\ub9d0",
            "\ubb34\uc5c7\uc744 \ub3c4\uc640",
            "\uc5b4\ub5bb\uac8c \ub3c4\uc640",
            "what can you do",
            "what do you do",
            "capabilities",
            "features",
            "help",
        ],
    )


def is_dashboard_diagnostic_question(text: str) -> bool:
    lowered = text.lower()
    has_diagnostic_term = contains_any(
        lowered,
        [
            "\uc624\ub958",
            "\uc5d0\ub7ec",
            "\uc774\uc0c1",
            "\uc9d5\ud6c4",
            "\uc9c4\ub2e8",
            "\ubb38\uc81c",
            "error",
            "anomaly",
            "diagnose",
            "diagnostic",
        ],
    )
    has_dashboard_scope = contains_any(
        lowered,
        [
            "\ucd5c\uadfc",
            "\uc2dc\ubbac\ub808\uc774\uc158",
            "\uc791\uc5c5",
            "\uc2e4\ud589",
            "vissim",
            "simulation",
            "job",
            "recent",
        ],
    )
    return has_diagnostic_term and has_dashboard_scope


def has_prompt_simulation_run_term(text: str) -> bool:
    return contains_any(
        text,
        [
            "\uc2e4\ud589",
            "\ub3cc\ub824",
            "\uc218\ud589",
            "\uc2dc\ubbac\ub808\uc774\uc158",
            "run",
            "execute",
            "simulate",
            "simulation",
        ],
    )


def is_prompt_simulation_request(text: str) -> bool:
    lowered = text.lower()
    has_run = has_prompt_simulation_run_term(lowered)
    has_parameter = contains_any(
        lowered,
        [
            "\uc8fc\uae30",
            "\uc635\uc14b",
            "\uc624\ud504\uc14b",
            "\uc2dc\ub4dc",
            "\ucc28\ub7c9",
            "\uc720\uc785\ub7c9",
            "\uad50\ud1b5\ub7c9",
            "\uae30\uac04",
            "\ub3d9\uc548",
            "cycle",
            "offset",
            "seed",
            "vehicle input",
            "volume",
            "duration",
            "sim period",
        ],
    )
    return has_run and (has_parameter or "vissim" in lowered)


def classify_intent(message: str) -> str:
    lowered = message.lower()
    if is_capabilities_question(lowered):
        return "capabilities"
    if is_prompt_simulation_request(lowered) and not is_analytical_question(lowered):
        return "settings"
    if contains_any(
        lowered,
        [
            "\uc624\ub958",
            "\uc5d0\ub7ec",
            "\uc2e4\ud328",
            "\uc9c4\ub2e8",
            "\ubb38\uc81c",
            "\uc774\uc0c1",
            "\uc9d5\ud6c4",
            "error",
            "fail",
            "failed",
            "diagnose",
            "no path",
            "not found",
            "cannot be found",
        ],
    ):
        return "diagnostic"
    if contains_any(
        lowered,
        [
            "\ubcc0\uacbd",
            "\uc124\uc815",
            "\uc801\uc6a9",
            "\uc218\uc815",
            "\ubc14\uafd4",
            "offset",
            "cycle",
            "signal",
            "vehicle input",
            "change",
            "set ",
        ],
    ) and not is_analytical_question(lowered):
        return "settings"
    # A "why / cause / difference / explain" question wants reasoning from the
    # model, not the instant status template \u2014 route it to general even when it
    # mentions words like "\uc2dc\ubbac\ub808\uc774\uc158" that would otherwise fall into status.
    if is_analytical_question(lowered):
        return "general"
    if contains_any(
        lowered,
        [
            "\uc0c1\ud0dc",
            "\uc9c4\ud589",
            "\uc2e4\ud589 \uc911",
            "\uc2dc\ubbac\ub808\uc774\uc158",
            "status",
            "running",
            "job",
            "com",
        ],
    ):
        return "status"
    return "general"


def answer_format_sections(intent: str, response_language: str | None = "ko") -> list[tuple[str, str]]:
    sections = ANSWER_FORMATS.get(intent, ANSWER_FORMATS["general"])
    if response_language == "ja":
        headings = ANSWER_FORMAT_HEADINGS_JA.get(intent, ANSWER_FORMAT_HEADINGS_JA["general"])
    elif response_language == "en":
        headings = ANSWER_FORMAT_HEADINGS_EN.get(intent, ANSWER_FORMAT_HEADINGS_EN["general"])
    else:
        return sections
    return [(headings[index] if index < len(headings) else heading, instruction) for index, (heading, instruction) in enumerate(sections)]


def answer_format_payload(intent: str, response_language: str | None = "ko") -> list[dict[str, str]]:
    sections = answer_format_sections(intent, response_language)
    return [{"heading": heading, "instruction": instruction} for heading, instruction in sections]


def answer_format_prompt(intent: str, response_language: str | None = "ko") -> str:
    sections = answer_format_sections(intent, response_language)
    lines = [
        "Preferred answer shape:",
        "Start with the conclusion. Use the labels below only if they make the answer easier to scan.",
        "Do not force every label if it would create filler. Keep the whole answer compact.",
        "Use short bullets only when multiple checks or steps are genuinely useful.",
        "Do not add a separate sources section because the UI shows sources below the answer.",
        "Do not use Markdown emphasis markers. Never wrap words with double asterisks or underscores.",
        "Write any labels as plain text with a colon, without Markdown heading markers.",
    ]
    for heading, instruction in sections:
        lines.append(f"{heading}: {instruction}")
    return "\n".join(lines)


def source_from_rag_item(item: dict[str, Any]) -> dict[str, Any]:
    path_text = str(item.get("path") or "")
    path = Path(path_text)
    page_range = page_range_for_path(path)
    topic = topic_for_path(path, str(item.get("title") or ""))
    source_file = source_file_for_path(path, str(item.get("title") or ""))
    retrieval = str(item.get("retrieval") or "keyword")
    score = item.get("score")
    text = str(item.get("text") or "")

    source: dict[str, Any] = {
        "kind": item.get("kind", "doc"),
        "title": item.get("title", path.stem),
        "path": path_text,
        "section": item.get("section", ""),
        "chunk_id": item.get("chunk_id", ""),
        "retrieval": retrieval,
        "source_file": source_file,
        "topic": topic,
        "page_range": page_range,
        "snippet": compact_snippet(text),
        "citation_label": citation_label(source_file, topic, page_range, str(item.get("title") or path.stem)),
    }
    if item.get("vector_backend"):
        source["vector_backend"] = item["vector_backend"]
    if isinstance(score, (int, float)):
        source["score"] = round(float(score), 4)
    return source


def page_range_for_path(path: Path) -> str | None:
    match = re.search(r"pages_(\d+)_(\d+)", path.name)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    return f"pages {start}-{end}" if start != end else f"page {start}"


def topic_for_path(path: Path, title: str) -> str:
    try:
        relative = path.relative_to(REPO_ROOT / "docs")
    except ValueError:
        return ""
    parts = relative.parts
    if len(parts) >= 4 and parts[0] == "pdf_knowledge":
        return friendly_label(parts[2])
    if len(parts) >= 2:
        return friendly_label(parts[0])
    if " - " in title:
        return title.split(" - ")[-1]
    return ""


def source_file_for_path(path: Path, title: str) -> str:
    try:
        relative = path.relative_to(REPO_ROOT / "docs")
    except ValueError:
        return path.stem
    parts = relative.parts
    if len(parts) >= 2 and parts[0] == "pdf_knowledge":
        return friendly_label(parts[1])
    return friendly_label(parts[0]) if parts else title


def citation_label(source_file: str, topic: str, page_range: str | None, title: str) -> str:
    if not page_range and source_file and source_file == topic:
        return f"{source_file} / {title}"
    parts = [part for part in [source_file, topic, page_range] if part]
    return " / ".join(parts) if parts else title


def friendly_label(value: str) -> str:
    replacements = {
        "vissim_manual": "Vissim Manual",
        "vissim_first_steps": "Vissim First Steps",
        "scatsim_guide_draft_tk": "SCATSIM Guide",
        "operational_modelling": "Operational Modelling",
        "mesoscopic_and_hybrid_modelling_guidelines": "Mesoscopic and Hybrid Guidelines",
        "signal_control": "Signal Control",
        "vehicle_inputs_demand": "Vehicle Inputs and Demand",
        "pedestrian_routing": "Pedestrian Routing",
        "links_connectors_routes": "Links, Connectors, and Routes",
        "evaluation_results": "Evaluation Results",
        "simulation_run": "Simulation Run",
        "com_automation": "COM Automation",
        "error_cases": "Error Cases",
        "com_examples": "COM Examples",
        "project_notes": "Project Notes",
        "pdf_knowledge": "PDF Knowledge",
    }
    return replacements.get(value, value.replace("_", " ").replace("-", " ").title())


def compact_snippet(text: str, limit: int = 260) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 1].rstrip() + "..."


def strip_markdown_emphasis(text: str) -> str:
    stripped = re.sub(r"\*\*([^*\n][^*]*?)\*\*", r"\1", text)
    stripped = re.sub(r"__([^_\n][^_]*?)__", r"\1", stripped)
    return stripped.replace("**", "")


def strip_markdown_answer_markers(text: str) -> str:
    stripped = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    return strip_markdown_emphasis(stripped)


def clean_generated_answer(text: str | None) -> str:
    if not text:
        return ""

    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    markers = ["최종 답변:", "최종답변:", "Final answer:", "Final:", "Answer:", "답변:"]
    lowered = cleaned.lower()
    marker_positions = [(lowered.rfind(marker.lower()), marker) for marker in markers]
    marker_positions = [(pos, marker) for pos, marker in marker_positions if pos >= 0]
    if marker_positions:
        pos, marker = max(marker_positions, key=lambda item: item[0])
        cleaned = cleaned[pos + len(marker) :].strip()
    else:
        draft_pos = lowered.rfind("draft:")
        if draft_pos >= 0:
            cleaned = cleaned[draft_pos + len("draft:") :].strip()

    lines = [line.strip().strip('"') for line in cleaned.splitlines() if line.strip()]
    skip_prefixes = (
        "the user",
        "i need",
        "from `",
        "from ",
        "safety check",
        "wait",
        "let's",
        "draft:",
        "so the",
    )
    answer_lines: list[str] = []
    skipped_internal_line = False
    for line in lines:
        lowered_line = line.lower()
        if lowered_line.startswith(skip_prefixes):
            continue
        if any(term in lowered_line for term in INTERNAL_ANSWER_TERMS):
            skipped_internal_line = True
            continue
        if line.startswith("(") and not re.search(r"[가-힣]", line):
            continue
        answer_lines.append(line)

    if not answer_lines:
        if skipped_internal_line:
            return ""
        return strip_markdown_answer_markers(cleaned.strip())

    answer = "\n".join(answer_lines).strip()
    answer = re.sub(r"\n{3,}", "\n\n", answer)
    return strip_markdown_answer_markers(answer)


def search_docs(query: str) -> list[dict[str, str]]:
    docs_dir = REPO_ROOT / "docs"
    if not docs_dir.exists():
        return []

    query_tokens = tokenize(query)
    scored: list[tuple[int, Path, str]] = []
    for path in docs_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for chunk in chunk_text(text):
            score = sum(1 for token in tokenize(chunk) if token in query_tokens)
            if score > 0:
                scored.append((score, path, chunk))

    if not scored:
        for path in list(docs_dir.glob("*.md"))[:DOC_CONTEXT_LIMIT]:
            try:
                scored.append((0, path, path.read_text(encoding="utf-8", errors="replace")[:1200]))
            except OSError:
                continue

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "title": path.stem.replace("-", " ").title(),
            "path": str(path),
            "text": chunk[:1600],
        }
        for _, path, chunk in scored[:DOC_CONTEXT_LIMIT]
    ]


def tokenize(text: str) -> set[str]:
    return {item.lower() for item in re.findall(r"[A-Za-z0-9_가-힣]+", text) if len(item) > 1}


def chunk_text(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) > 1400 and current:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks or [text[:1400]]


def build_safety_result(intent: str, message: str, allow_tool_execution: bool) -> dict[str, Any]:
    if intent != "settings":
        return {"checked": False, "allowed": False, "reason": "No setting change requested."}

    risky = contains_any(message.lower(), ["delete", "remove", "reset", "초기화", "삭제", "전부", "all"])
    if risky:
        return {"checked": True, "allowed": False, "reason": "파괴적이거나 범위가 큰 변경 요청이라 실행하지 않았습니다."}
    if is_prompt_simulation_request(message):
        if not allow_tool_execution:
            return {"checked": True, "allowed": False, "reason": "시뮬레이션 실행 권한이 꺼져 있어 VISSIM을 조작하지 않습니다."}
        return {"checked": True, "allowed": True, "reason": "허용된 시뮬레이션 파라미터만 적용합니다."}
    if not allow_tool_execution:
        return {"checked": True, "allowed": False, "reason": "채팅 화면에서는 우선 안전성 검사까지만 수행하고 실제 설정 변경은 실행하지 않습니다."}
    return {"checked": True, "allowed": False, "reason": "구체적인 VISSIM 설정 변경 도구 계약이 아직 없어 실행을 보류했습니다."}


def compact_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": dataset.get("source"),
        "kind": dataset.get("kind"),
        "filename": dataset.get("filename"),
        "revision": dataset.get("revision"),
        "has_project_db": dataset.get("has_project_db"),
        "has_scenarios": dataset.get("has_scenarios"),
        "has_real_counts": dataset.get("has_real_counts"),
    }


def compact_congested_node_en(node: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(node)
    node_id = str(compacted.get("node_id") or "").strip()
    compacted["node_name"] = english_text(compacted.get("node_name"), f"Node {node_id}" if node_id else "Selected node")
    return compacted


def compact_key_insight_en(insight: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(insight)
    title = english_text(compacted.get("title") or compacted.get("label"), "Dashboard insight")
    value = compacted.get("value")
    unit = compacted.get("unit")
    compacted["title"] = title
    if compacted.get("label") is not None:
        compacted["label"] = english_text(compacted.get("label"), title)
    compacted["message"] = english_text(compacted.get("message"), english_insight_message(title, value, unit))
    return compacted


def compact_status_band_en(status_band: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status_band, dict):
        return {}
    compacted = dict(status_band)
    compacted["label"] = english_text(compacted.get("label"), "Comparison ready")
    compacted["title"] = english_text(compacted.get("title"), "Scenario comparison is ready.")
    compacted["message"] = english_text(compacted.get("message"), "Dashboard metrics are available for comparison.")
    compacted["detail"] = english_text(compacted.get("detail"), "All selected scenarios are loaded.")
    return compacted


def english_insight_message(title: str, value: Any, unit: Any) -> str:
    if value is None:
        return f"{title} is available in the dashboard summary."
    suffix = f" {unit}" if unit else ""
    return f"{title}: {value}{suffix}."


def compact_compare_summary(summary: dict[str, Any], response_language: str | None = "ko") -> dict[str, Any]:
    compacted = {
        "scenario_ids": summary.get("scenario_ids", []),
        "invalid_ids": summary.get("invalid_ids", []),
        "missing_result_ids": summary.get("missing_result_ids", []),
        "metric_leaders": summary.get("metric_leaders", {}),
        "top_congested_nodes": summary.get("top_congested_nodes", [])[:3],
        "key_insights": summary.get("key_insights", [])[:5],
        "status_band": summary.get("status_band", {}),
    }
    if response_language == "en":
        compacted["top_congested_nodes"] = [compact_congested_node_en(node) for node in compacted["top_congested_nodes"]]
        compacted["key_insights"] = [compact_key_insight_en(insight) for insight in compacted["key_insights"]]
        compacted["status_band"] = compact_status_band_en(compacted["status_band"])
    return compacted


def compact_jobs(jobs: list[dict[str, Any]], response_language: str | None = "ko") -> list[dict[str, Any]]:
    compacted = []
    for job in jobs[:8]:
        name = job.get("name")
        error = job.get("error")
        if response_language == "en":
            name = english_text(name, english_job_id_label(str(job.get("id"))) if job.get("id") else "simulation job")
            error = english_text(error, "Korean error detail omitted in English mode.")
        compacted.append(
            {
                "id": job.get("id"),
                "name": name,
                "status": job.get("status"),
                "progress": job.get("progress"),
                "error": error,
                "created_at": job.get("created_at"),
                "completed_at": job.get("completed_at"),
            }
        )
    return compacted


def compact_vissim_com_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": status.get("available"),
        "executed": status.get("executed"),
        "connected": status.get("connected"),
        "reason": status.get("reason"),
        "error": status.get("error"),
        "prog_id": status.get("prog_id"),
        "launched": status.get("launched"),
        "loaded_active_network": status.get("loaded_active_network"),
        "source_inpx": status.get("source_inpx"),
        "source_inpx_exists": status.get("source_inpx_exists"),
        "network": status.get("network") or {},
        "simulation": status.get("simulation") or {},
        "counts": status.get("counts") or {},
        "signal_controllers": (status.get("signal_controllers") or [])[:6],
        "vehicle_inputs": (status.get("vehicle_inputs") or [])[:6],
    }


def limited_json(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(text) <= JSON_CONTEXT_LIMIT:
        return text
    return text[:JSON_CONTEXT_LIMIT] + "\n... truncated ..."

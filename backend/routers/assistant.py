from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from queue import Queue
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.assistant_router import AssistantRequestContext, VissimAssistantRouter, build_assistant_exception_response, minimal_assistant_status
from services.vissim_tool import get_vissim_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistant", tags=["assistant"])
assistant_router = VissimAssistantRouter()


def _kill_stale_qwen_workers() -> None:
    """Kill orphaned Qwen worker processes left by a previous backend instance.
    On Windows a child worker is NOT auto-killed when its parent (uvicorn) dies,
    so across restarts they pile up, each holding ~5-6 GB of VRAM, exhaust the
    GPU and make generation extremely slow (spilling to system RAM)."""
    try:
        import psutil
    except ImportError:
        return
    killed = 0
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if any("qwen_worker.py" in str(part) for part in cmdline):
                proc.kill()
                killed += 1
        except Exception:
            continue
    if killed:
        logger.info("Cleaned up %d stale Qwen worker(s) at startup.", killed)


def _preload_assistant_model() -> None:
    """Clean up orphaned workers, then warm up the local model at startup (in the
    background) so the first chat request is not a cold start. Orphan cleanup runs
    even when preload is disabled with QWEN_PRELOAD=0."""
    _kill_stale_qwen_workers()
    if os.getenv("QWEN_PRELOAD", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    threading.Thread(target=assistant_router.warmup, name="qwen-warmup", daemon=True).start()


_preload_assistant_model()


class AssistantQueryRequest(BaseModel):
    message: str = Field(min_length=1)
    selected_scenario_ids: list[str] = Field(default_factory=list)
    selected_node_ids: list[str] = Field(default_factory=list)
    allow_tool_execution: bool = False
    response_language: str = "auto"


@router.get("/status")
def get_assistant_status():
    return assistant_router.status()


@router.get("/tools/vissim-com/status")
def get_vissim_com_tool_status(launch: bool = False, load_active_network: bool = False):
    return get_vissim_status(launch_if_missing=launch, load_active_network=load_active_network)


@router.post("/query")
def query_assistant(payload: AssistantQueryRequest):
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    return run_assistant_query(message, payload)


@router.post("/query/stream")
def stream_assistant(payload: AssistantQueryRequest):
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    def generate_events():
        stream_id = uuid.uuid4().hex[:10]
        yield encode_stream_event({"type": "status", "stream_id": stream_id, "phase": "accepted"})
        yield encode_stream_event({"type": "status", "stream_id": stream_id, "phase": "preparing"})

        # Run the (blocking) query in a worker thread and forward model tokens to
        # the client as they are produced, so the answer streams in live instead
        # of appearing all at once after generation completes.
        token_queue: Queue = Queue()
        holder: dict[str, Any] = {}

        def on_token(piece: str) -> None:
            token_queue.put(("token", piece))

        def run() -> None:
            try:
                holder["response"] = run_assistant_query(
                    message, payload, stream_id=stream_id, on_token=on_token
                )
            finally:
                token_queue.put(("end", None))

        threading.Thread(target=run, name=f"assistant-stream-{stream_id}", daemon=True).start()

        streamed_any = False
        while True:
            kind, value = token_queue.get()
            if kind == "end":
                break
            if kind == "token" and value:
                streamed_any = True
                yield encode_stream_event({"type": "chunk", "stream_id": stream_id, "text": str(value)})

        response = holder.get("response")
        if response is None:
            response = run_assistant_query(message, payload, stream_id=stream_id)

        response_without_answer = {**response, "answer": ""}
        runtime_mode = str((response.get("runtime") or {}).get("mode") or "")
        yield encode_stream_event(
            {
                "type": "metadata",
                "stream_id": stream_id,
                "intent": response.get("intent"),
                "runtime_mode": runtime_mode,
                "response": response_without_answer,
            }
        )

        # Non-generated answers (greetings, capability/status replies, fallbacks)
        # stream no tokens; emit the final answer in chunks so the UI still shows it.
        if not streamed_any:
            for chunk in chunk_answer_for_stream(str(response.get("answer") or "")):
                yield encode_stream_event({"type": "chunk", "stream_id": stream_id, "text": chunk})

        yield encode_stream_event({"type": "done", "stream_id": stream_id, "response": response})

    return StreamingResponse(
        generate_events(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


def run_assistant_query(
    payload_message: str,
    payload: AssistantQueryRequest,
    stream_id: str | None = None,
    on_token: Callable[[str], None] | None = None,
):
    try:
        return assistant_router.answer(
            AssistantRequestContext(
                message=payload_message,
                selected_scenario_ids=payload.selected_scenario_ids,
                selected_node_ids=payload.selected_node_ids,
                allow_tool_execution=payload.allow_tool_execution,
                response_language=payload.response_language,
            ),
            on_token=on_token,
        )
    except Exception as exc:
        error_id = uuid.uuid4().hex[:10]
        logger.exception("Assistant query failed error_id=%s stream_id=%s", error_id, stream_id)
        try:
            model_status = assistant_router.status()
        except Exception:
            model_status = minimal_assistant_status(error_id)
        return build_assistant_exception_response(
            message=payload_message,
            response_language=payload.response_language,
            exc=exc,
            error_id=error_id,
            model_status=model_status,
        )


def encode_stream_event(event: dict[str, object]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"


def chunk_answer_for_stream(answer: str, chunk_size: int = 96):
    if not answer:
        return
    buffer = ""
    for char in answer:
        buffer += char
        if char in ".!?\n。！？" or len(buffer) >= chunk_size:
            yield buffer
            buffer = ""
    if buffer:
        yield buffer

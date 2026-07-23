from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import REPO_ROOT
from services.agent.edit_session import (
    VissimEditSession,
    clear_active_edit_session,
    set_active_edit_session,
)
from services.agent.gates import PreapprovedWriteGate, ProposeWriteGate, signature
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.planner import pick_language_text
from services.agent.state import ArtifactManager
import services.agent.tools_network as tools_network
from services.assistant_router import (
    AssistantRequestContext,
    VissimAssistantRouter,
    _default_provider,
    build_assistant_exception_answer,
    build_assistant_exception_response,
    minimal_assistant_status,
)
from services.dashboard_curator import curate_dashboard
from services.datasets import get_active_inpx_path
from services.results_reader import get_compare_summary, get_selected_node_details
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


class AgentQueryRequest(BaseModel):
    message: str = Field(min_length=1)
    response_language: str | None = "ko"


class AgentApproveRequest(BaseModel):
    """M2.3: resolves one pending write approval — see ``stream_agent_query``'s
    ``approval_request`` NDJSON event, which is where ``session_id``/
    ``approval_id`` come from. ``response_language`` is deliberately NOT a
    field here: the language the approve-turn answers in is sourced from
    ``pending_approval.json`` (the language the ORIGINAL proposal turn used),
    not re-supplied by the client, so a propose→approve pair can never
    answer in two different languages."""

    session_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    approved: bool


class CurateRequest(BaseModel):
    message: str = Field(min_length=1)
    selected_scenario_ids: list[str] = Field(default_factory=list)
    selected_node_ids: list[str] = Field(default_factory=list)
    response_language: str = "ko"
    audience: str = "plain"


@router.post("/curate")
def curate(payload: CurateRequest):
    """LLM dashboard curation: which charts answer the question + insight cards."""
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    context: dict[str, Any] = {}
    try:
        context["compare_summary"] = get_compare_summary(payload.selected_scenario_ids)
    except Exception:
        logger.exception("Curation context: compare summary failed")
        context["compare_summary"] = {}
    if payload.selected_node_ids:
        try:
            context["selected_nodes"] = get_selected_node_details(
                payload.selected_scenario_ids, payload.selected_node_ids
            )
        except Exception:
            logger.exception("Curation context: node details failed")

    result = curate_dashboard(
        provider=assistant_router.provider,
        message=message,
        context=context,
        response_language=payload.response_language,
        audience=payload.audience,
    )
    result["model"] = assistant_router.provider.status().get("model")
    return result


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
            # Never re-run the query here: with allow_tool_execution=True a
            # retry could start the same simulation job twice. Surface an
            # error response instead.
            error_id = uuid.uuid4().hex[:10]
            logger.error(
                "Assistant stream worker produced no response stream_id=%s error_id=%s",
                stream_id,
                error_id,
            )
            try:
                model_status = assistant_router.status()
            except Exception:
                model_status = minimal_assistant_status(error_id)
            response = build_assistant_exception_response(
                message=message,
                response_language=payload.response_language,
                exc=RuntimeError("Assistant worker thread produced no response."),
                error_id=error_id,
                model_status=model_status,
            )

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


def _agent_session_work_dir(session_id: str) -> Path:
    """Per-session agent scratch dir — mirrors ``simulation_runs.job_dir``'s
    REPO_ROOT-relative base resolution (``backend/services/simulation_runs.py``)."""
    return REPO_ROOT / "runtime" / "agent_sessions" / session_id


# ``/agent/query/stream`` always mints ``session_id`` itself (``uuid.uuid4().
# hex[:12]``), so it never needs validating. ``/agent/approve`` is the FIRST
# place a client-supplied ``session_id`` reaches ``_agent_session_work_dir``
# (and therefore the filesystem) — without a shape check, a crafted
# ``session_id`` like ``"../../../whatever"`` would let path construction
# escape ``runtime/agent_sessions/`` entirely. Real session ids are always
# 12 lowercase hex chars; this is intentionally a little looser (8-64) so a
# format change to the id generator doesn't immediately break this check.
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8,64}$")


def _pending_approval_path(work_dir: Path) -> Path:
    return work_dir / "pending_approval.json"


def _write_pending_approval(work_dir: Path, payload: dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    _pending_approval_path(work_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_pending_approval(work_dir: Path) -> dict[str, Any] | None:
    path = _pending_approval_path(work_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _delete_pending_approval(work_dir: Path) -> None:
    _pending_approval_path(work_dir).unlink(missing_ok=True)


def _build_edit_session(working_inpx: Path) -> VissimEditSession:
    """Construct the ``VissimEditSession`` for an approved write turn.

    A thin, separately-named seam purely so tests can
    ``monkeypatch.setattr(assistant_endpoint, "_build_edit_session", ...)``
    with a fake session — mirroring ``_default_provider``'s existing
    injection pattern in this same file — without needing real Vissim COM
    in pytest. Production code path: unconditionally the real class.
    """
    return VissimEditSession(working_inpx)


def _ensure_working_copy(artifact_manager: ArtifactManager) -> None:
    """``clone()`` for a brand-new work dir; otherwise re-sync from
    ``source_inpx`` and refresh the shadow model WITHOUT re-triggering
    ``ArtifactManager``'s clone-precondition guard.

    ``/agent/approve`` constructs a fresh ``ArtifactManager`` over the SAME
    work dir ``/agent/query/stream`` already ``clone()``-d (state.py and
    guardrails.py are both off-limits for this task, so there is no cross-
    request Python object to literally reuse — see the drafted DECISIONS.md
    entry in the M2.3 report). Calling ``clone()`` a second time there would
    hit ``_guard_against_clobbering_unsaved_work``'s ``version == 0``
    branch and raise, EVEN THOUGH the working copy is provably still
    pristine: the only way execution reaches here is via a
    ``pending_approval.json`` that exists ONLY when the proposing turn
    ended via ``ApprovalRequired`` — which orchestrator.py always raises
    BEFORE any dispatch, so no write has ever landed on this work dir yet.
    ``rollback_to(0)`` is public API that achieves the same
    outcome (working copy re-synced from ``source_inpx`` + a freshly
    reparsed shadow model) without touching that guard at all.
    """
    if artifact_manager.working_path.exists():
        # Restore the latest GOOD state, not blindly the pristine clone:
        # today every stream mints a fresh session id so version is always 0
        # here, but the moment M3.3 reloads sessions by id, a work dir can
        # arrive carrying user-approved, postwrite-verified edits
        # (version >= 1) — rolling those back to 0 would silently destroy
        # approved work. rollback_to(version) is byte-identical to a no-op
        # for a clean working copy and discards only unverified divergence
        # (e.g. a crash between persist() and save_version()).
        artifact_manager.rollback_to(artifact_manager.version)
    else:
        artifact_manager.clone()


_APPROVAL_DECLINED_TEXT = {
    "ko": "요청하신 작업을 취소했습니다. 네트워크는 변경되지 않았습니다.",
    "en": "The requested action was cancelled. The network was left unchanged.",
    "ja": "リクエストされた操作はキャンセルされました。ネットワークは変更されていません。",
}

# {actions} is filled by _format_actions_for_note — a compact, LLM-legible
# rendering of the approved tool call(s), so the re-run's planner has
# everything it needs to reissue the SAME action rather than guessing.
_APPROVAL_SYSTEM_NOTE = {
    "ko": (
        "\n\n[시스템 안내: 사용자가 다음 작업을 승인했습니다 — {actions}. "
        "정확히 이 작업을 다시 호출하여 실행하세요.]"
    ),
    "en": (
        "\n\n[System note: the user approved the following action(s) — {actions}. "
        "Call exactly this action again so it can execute.]"
    ),
    "ja": (
        "\n\n[システム通知: ユーザーは次の操作を承認しました — {actions}。"
        "正確にこの操作を再度呼び出して実行してください。]"
    ),
}


def _format_actions_for_note(actions: list[dict[str, Any]]) -> str:
    rendered = []
    for action in actions:
        params_text = ", ".join(f"{k}={v!r}" for k, v in (action.get("params") or {}).items())
        rendered.append(f"{action.get('tool')}({params_text})")
    return "; ".join(rendered)


# ---------------------------------------------------------------------------
# Shared streaming body: run_agent_turn on a worker thread, bridged to NDJSON
# ---------------------------------------------------------------------------


async def _stream_agent_turn(
    request: Request,
    *,
    message: str,
    language: str,
    session_id: str,
    work_dir: Path,
    artifact_manager: ArtifactManager,
    gate: Any,
    engine: GuardrailEngine,
    edit_session: "VissimEditSession | None" = None,
) -> AsyncIterator[str]:
    """NDJSON event body shared by ``/agent/query/stream`` (a fresh
    proposal, ``ProposeWriteGate``) and ``/agent/approve``'s approved path
    (a ``PreapprovedWriteGate`` re-run with a live ``VissimEditSession``
    active). Runs ``run_agent_turn`` on a dedicated worker thread (COM, when
    ``edit_session`` is given, must stay on one OS thread for its whole
    lifetime — see ``edit_session.py``'s threading contract) and bridges its
    ``on_step`` callback to NDJSON ``step`` events via a thread-safe
    ``Queue``, same structure M1.4 established for ``/agent/query/stream``.

    M2.3 additions over the M1.4 shape: an ``approval_request`` event (plus
    a ``pending_approval.json`` write) when the turn pauses for approval,
    and cooperative cancellation — ``should_cancel`` is polled from BOTH a
    disconnect check between step events and this generator's own
    ``finally`` (covers a client that vanishes without a clean disconnect
    message reaching ``request.is_disconnected()`` in time).
    """
    stream_id = uuid.uuid4().hex[:10]
    yield encode_stream_event({"type": "status", "stream_id": stream_id, "phase": "accepted"})

    step_queue: Queue = Queue()
    holder: dict[str, Any] = {}
    cancelled = threading.Event()

    def should_cancel() -> bool:
        return cancelled.is_set()

    def on_step(step: dict[str, Any]) -> None:
        step_queue.put(("step", dict(step)))

    def run() -> None:
        try:
            _ensure_working_copy(artifact_manager)
            # M1-inherited module-global session context (tools_network.py's
            # own documented caveat): points every read tool at THIS turn's
            # working copy, not the source dataset, so an edit made earlier
            # in this session is visible to subsequent reads within it.
            # Reset in `finally` so a later, unrelated request never
            # inherits it — hygiene on top of the pre-existing, tracked
            # concurrency limitation (docs/phase2/DECISIONS.md, "Future
            # multi-dataset work"), not a fix for it.
            tools_network.set_active_inpx(artifact_manager.working_path)
            if edit_session is not None:
                set_active_edit_session(edit_session)
            try:
                holder["result"] = run_agent_turn(
                    message,
                    provider=_default_provider(),
                    work_dir=work_dir,
                    session_id=session_id,
                    language=language,
                    on_step=on_step,
                    gate=gate,
                    engine=engine,
                    artifact_manager=artifact_manager,
                    should_cancel=should_cancel,
                )
            finally:
                if edit_session is not None:
                    clear_active_edit_session()
                    edit_session.close()
        except Exception as exc:  # noqa: BLE001 - surfaced as an apologetic `done`, never raised
            holder["error"] = exc
        finally:
            tools_network.set_active_inpx(None)
            step_queue.put(("end", None))

    threading.Thread(target=run, name=f"agent-stream-{stream_id}", daemon=True).start()

    # Accumulated so the exception branch below can still report whatever
    # steps actually streamed before the failure (same rationale as M1.4).
    streamed_steps: list[dict[str, Any]] = []

    try:
        while True:
            try:
                kind, value = step_queue.get_nowait()
            except Empty:
                try:
                    disconnected = await request.is_disconnected()
                except Exception:  # noqa: BLE001 - a disconnect-check failure must never break the stream
                    disconnected = False
                if disconnected:
                    cancelled.set()
                await asyncio.sleep(0.03)
                continue
            if kind == "end":
                break
            summary = value.get("summary", "")
            tool = value.get("tool", "")
            status = value.get("status", "")
            streamed_steps.append({"summary": summary, "tool": tool, "status": status})
            yield encode_stream_event(
                {
                    "type": "step",
                    "stream_id": stream_id,
                    "summary": summary,
                    "tool": tool,
                    "status": status,
                    "index": value.get("index", 0),
                }
            )
    finally:
        # A client that vanishes mid-stream (Starlette abandoning this
        # generator, e.g. via GeneratorExit) must still stop the turn
        # promptly rather than let it keep spending LLM/COM time after
        # nobody is listening — same flag the polling loop above sets.
        cancelled.set()

    result = holder.get("result")
    error = holder.get("error")
    if error is not None or result is None:
        error_id = uuid.uuid4().hex[:10]
        logger.error(
            "Agent stream failed stream_id=%s session_id=%s error_id=%s",
            stream_id,
            session_id,
            error_id,
            exc_info=error,
        )
        answer = build_assistant_exception_answer(
            error_id, english=language == "en", japanese=language == "ja"
        )
        for chunk in chunk_answer_for_stream(answer):
            yield encode_stream_event({"type": "chunk", "stream_id": stream_id, "text": chunk})
        yield encode_stream_event(
            {
                "type": "done",
                "stream_id": stream_id,
                "response": {
                    "answer": answer,
                    "kind": "answer",
                    "steps": streamed_steps,
                    "session_id": session_id,
                },
            }
        )
        return

    approval_payload: dict[str, Any] | None = None
    if result.kind == "approval_request" and result.pending_approval is not None:
        approval_id = uuid.uuid4().hex
        action_entry = {
            "tool": result.pending_approval["tool"],
            "params": result.pending_approval["params"],
            "summary": result.pending_approval.get("summary", ""),
        }
        warnings = result.pending_approval.get("warnings", [])
        # No structured "assumption" signal exists yet from the planner
        # (see the M2.3 report's drafted DECISIONS.md entry) — always empty
        # today, kept in the wire shape so the frontend can implement
        # against the field now and it starts populating later at no
        # contract-breaking cost.
        assumptions: list[str] = []
        pending_file_payload = {
            "approval_id": approval_id,
            "created_at": time.time(),
            # Beyond the roadmap brief's literal 4 named fields
            # (approval_id/created_at/signatures/actions + warnings) —
            # `message`/`language` are the original turn's own text and
            # response language, both required to faithfully replay this
            # exact request once approved (see approve_agent_action).
            "message": message,
            "language": language,
            "signatures": [signature(action_entry["tool"], action_entry["params"])],
            "actions": [action_entry],
            "warnings": warnings,
        }
        _write_pending_approval(work_dir, pending_file_payload)
        approval_payload = {
            "approval_id": approval_id,
            "actions": [action_entry],
            "warnings": warnings,
            "assumptions": assumptions,
        }
        yield encode_stream_event(
            {"type": "approval_request", "stream_id": stream_id, **approval_payload}
        )

    for chunk in chunk_answer_for_stream(result.answer):
        yield encode_stream_event({"type": "chunk", "stream_id": stream_id, "text": chunk})

    done_response: dict[str, Any] = {
        "answer": result.answer,
        "kind": result.kind,
        "steps": result.steps,
        "session_id": session_id,
    }
    if approval_payload is not None:
        done_response["approval"] = approval_payload
    yield encode_stream_event({"type": "done", "stream_id": stream_id, "response": done_response})


@router.post("/agent/query/stream")
def stream_agent_query(payload: AgentQueryRequest, request: Request):
    """M1.4 (read-only) + M2.3 (write + approval): stream an agent turn
    (docs/phase2/01-ARCHITECTURE.md §3).

    Thin by design — request parsing, provider/work-dir setup, and streaming
    glue only; all agent logic lives in ``services.agent.orchestrator``. NDJSON
    events: one ``status`` (accepted), one ``step`` per ``on_step`` callback,
    ``chunk`` events for the final answer, then one ``done`` — except a
    write-tier request now (M2.3) pauses first: a ``step`` with
    ``status: "pending_approval"``, an ``approval_request`` event, THEN the
    (short, proposal-line) chunks and ``done`` — ``done.response.kind`` is
    ``"approval_request"`` and carries the same payload again under
    ``.approval`` so a client that only inspects ``done`` still gets it. No
    event ever carries a filesystem path (``trace_path`` stays server-side).

    Builds a per-session ``ArtifactManager`` (source = the active dataset's
    .inpx, per ``services.datasets.get_active_inpx_path`` — the same
    resolution the rest of the app already uses; work dir = this session's
    scratch dir) and points ``tools_network``'s module-global active-inpx at
    its working copy for the duration of the turn, so every read tool this
    turn dispatches reflects THIS session's edits, not the shared source
    dataset (tools_network.py's own docstring already flags the module-
    global's single-session-at-a-time limitation — this wiring inherits
    that, it does not introduce it). The write-tier gate defaults to
    ``ProposeWriteGate``: the FIRST write of a plan always pauses for
    approval; there is currently no way to opt out of the approval flow
    from this endpoint (by design — M2.3's whole point).
    """
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    language = (payload.response_language or "ko").strip().lower() or "ko"

    session_id = uuid.uuid4().hex[:12]
    work_dir = _agent_session_work_dir(session_id)
    artifact_manager = ArtifactManager(source_inpx=get_active_inpx_path(), work_dir=work_dir)
    engine = GuardrailEngine()
    gate = ProposeWriteGate()

    async def generate_events() -> AsyncIterator[str]:
        async for event in _stream_agent_turn(
            request,
            message=message,
            language=language,
            session_id=session_id,
            work_dir=work_dir,
            artifact_manager=artifact_manager,
            gate=gate,
            engine=engine,
        ):
            yield event

    return StreamingResponse(
        generate_events(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/agent/approve")
def approve_agent_action(payload: AgentApproveRequest, request: Request):
    """M2.3: resolve one pending write approval from an earlier
    ``/agent/query/stream`` turn's ``approval_request`` event.

    ``approved=true`` re-runs the SAME original message (plus a short
    system-note suffix telling the planner which action the user approved —
    see ``_APPROVAL_SYSTEM_NOTE``) with a ``PreapprovedWriteGate`` scoped to
    exactly that action's signature, and a live ``VissimEditSession``
    activated for the turn — the write actually happens here, streamed with
    the same NDJSON shape as ``/agent/query/stream`` (a normal
    ``kind: "answer"`` ``done`` once the write is dispatched and postwrite-
    verified, or a postwrite-abort ``answer`` if verification failed and the
    change was rolled back).

    ``approved=false`` deletes the pending approval and streams a short,
    polite ``done`` — the network is never touched.

    Single-use either way: the pending approval file is deleted as soon as
    it is read, so replaying the same request twice 404s the second time.
    An unknown/stale ``(session_id, approval_id)`` pair, or a malformed
    ``session_id`` (see ``_SESSION_ID_RE`` — this is the first endpoint
    where a CLIENT-supplied session_id reaches filesystem path
    construction), both 404 with no further detail.
    """
    if not _SESSION_ID_RE.match(payload.session_id):
        raise HTTPException(status_code=404, detail="No matching pending approval.")

    work_dir = _agent_session_work_dir(payload.session_id)
    pending = _read_pending_approval(work_dir)
    if pending is None or pending.get("approval_id") != payload.approval_id:
        raise HTTPException(status_code=404, detail="No matching pending approval.")

    # Single-use: consumed the moment it is validated, regardless of outcome.
    _delete_pending_approval(work_dir)

    language = str(pending.get("language") or "ko")

    if not payload.approved:

        async def decline_events() -> AsyncIterator[str]:
            stream_id = uuid.uuid4().hex[:10]
            yield encode_stream_event({"type": "status", "stream_id": stream_id, "phase": "accepted"})
            answer = pick_language_text(_APPROVAL_DECLINED_TEXT, language)
            for chunk in chunk_answer_for_stream(answer):
                yield encode_stream_event({"type": "chunk", "stream_id": stream_id, "text": chunk})
            yield encode_stream_event(
                {
                    "type": "done",
                    "stream_id": stream_id,
                    "response": {
                        "answer": answer,
                        "kind": "answer",
                        "steps": [],
                        "session_id": payload.session_id,
                    },
                }
            )

        return StreamingResponse(
            decline_events(),
            media_type="application/x-ndjson; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    original_message = str(pending.get("message") or "")
    actions = pending.get("actions") or []
    note = pick_language_text(_APPROVAL_SYSTEM_NOTE, language).format(
        actions=_format_actions_for_note(actions)
    )
    full_message = original_message + note

    artifact_manager = ArtifactManager(source_inpx=get_active_inpx_path(), work_dir=work_dir)
    engine = GuardrailEngine()
    gate = PreapprovedWriteGate(set(pending.get("signatures") or []))
    edit_session = _build_edit_session(artifact_manager.working_path)

    async def approve_events() -> AsyncIterator[str]:
        async for event in _stream_agent_turn(
            request,
            message=full_message,
            language=language,
            session_id=payload.session_id,
            work_dir=work_dir,
            artifact_manager=artifact_manager,
            gate=gate,
            engine=engine,
            edit_session=edit_session,
        ):
            yield event

    return StreamingResponse(
        approve_events(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
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

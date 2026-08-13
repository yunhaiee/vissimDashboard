from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any, AsyncIterator, Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

# Unit U8 (roadmap 2.5): the approval gate is the ONE place in this router that
# demands an identity. ``_language`` is imported rather than re-implemented so
# a single route cannot answer its 401 (raised inside ``current_user``) in one
# language and its 403 in another.
from routers.auth import _language as _auth_language, current_user, require_role
from services import workspaces
from services.agent.edit_session import (
    VissimEditSession,
    clear_active_edit_session,
    set_active_edit_session,
)
from services.agent.gates import PreapprovedWriteGate, ProposeWriteGate, signature
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.planner import pick_language_text
from services.agent.sessions import (
    SESSIONS_ROOT,
    read_session_meta_dir,
    session_work_dir,
    session_workspace_matches,
    write_session_meta,
)
from services.agent.state import ArtifactManager
from services.agent.toolkit import default_registry
from services.agent.trace import TraceWriter
import services.agent.tools_network as tools_network
import services.agent.tools_run as tools_run
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
from services.network_selection import (
    build_selection_context,
    element_selection_fields,
    merge_element_selection,
    resolve_selected_elements,
    resolve_selected_links,
    sanitize_selected_element,
    sanitize_selected_elements,
    sanitize_selected_link_ids,
    sanitize_selected_node_ids,
    sanitize_selected_scenario_ids,
)
from services.results_reader import get_compare_summary, get_selected_node_details
from services.vissim_tool import get_vissim_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistant", tags=["assistant"])
assistant_router = VissimAssistantRouter()

#: Phase 1.3: hard cap on the approval rationale a client may send. A "one-line
#: reason" is what the roadmap row asks for and what fits a report table cell
#: (services/audit_trail.py elides past its own, smaller, render-time cap);
#: rejecting anything longer at the pydantic boundary keeps an unbounded blob
#: out of the append-only ledger in the first place.
#:
#: RAISED 300 -> 600 (unit D4, 2026-08-12). The cap's job is to keep a BLOB out
#: of the ledger, and 600 still does that. What 300 no longer accommodated was
#: the thing the reviewer is most likely to want to sign: the proposal's own
#: machine rationale. Measured on the demo loop, a demand proposal's rationale
#: runs 359 (ja) / 379 (ko) / 641 (en) chars, because D4 appends the sentence
#: that says the value is a NAMED demand estimate and asks the approver to
#: reject it if they hold the real count. At 300 every language had to be
#: abridged, and the first thing an abridgement drops is the caveat — i.e. the
#: cap was quietly turning "this is an estimate" into "this is a correction".
#: 600 lets ko and ja through verbatim; en still overflows and is abridged by
#: its caller (see tests/test_calibration_loop_e2e.py's module docstring for
#: the measurement, which is asserted there rather than described).
MAX_APPROVAL_REASON_CHARS = 600

#: Unit U8: who may resolve a pending write/run approval. Both roles are NAMED
#: — ``routers/auth.require_role`` grants ``admin`` nothing implicitly, exactly
#: so that "리뷰어가 로그인해 승인 이력을 자기 이름으로 남김" stays answerable
#: from the audit document. An admin is listed because a small consultancy's
#: admin IS often the senior reviewer, not because admin is a superuser.
APPROVAL_ROLES: tuple[str, ...] = ("reviewer", "admin")

#: The 403 body. It names the required roles (the generic
#: ``require_role`` message deliberately does not) because the person who hits
#: this is a modeler who needs to know WHO to ask, not that "something" was
#: forbidden. ko primary, en/ja through the same ``pick_language_text`` table
#: lookup every other deterministic string in this router uses.
_APPROVAL_ROLE_REQUIRED = {
    "ko": "승인·반려는 {roles} 권한을 가진 사용자만 할 수 있습니다.",
    "en": "Only a user with the {roles} role may approve or decline this action.",
    "ja": "承認・却下は {roles} 権限を持つユーザーのみが行えます。",
}

#: The same enforcement U7 shipped and tested — used as the gate itself, not
#: re-implemented. Built at import time so a typo'd role name raises there
#: (``require_role``'s own ValueError) rather than producing a route nobody can
#: ever call.
_require_approval_role = require_role(*APPROVAL_ROLES)


def require_approver(
    request: Request, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    """The authenticated ``reviewer``/``admin`` resolving this approval, or
    401/403.

    Thin wrapper around U7's ``require_role`` so the CHECK has exactly one
    implementation: the only thing added here is a 403 detail that names the
    roles, which a modeler hitting this endpoint needs in order to act.
    """
    try:
        return _require_approval_role(request, user)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        detail = pick_language_text(_APPROVAL_ROLE_REQUIRED, _auth_language(request)).format(
            roles=", ".join(APPROVAL_ROLES)
        )
        raise HTTPException(status_code=403, detail=detail) from exc


def _approval_actor(user: dict[str, Any]) -> dict[str, Any]:
    """The approver fields the ledger records — and ONLY those.

    Copied out of the session's user row rather than out of the request, so the
    name in the audit document is one this system authenticated. Deliberately
    excludes ``created_at``/``disabled``: an append-only ledger should carry the
    identity of the decision, not a snapshot of an account's current state.
    """
    return {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
    }


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


# P6 — the diagram selection every chat request may carry. Every field is
# CLIENT input that ends up inside a model prompt, so it is normalized at
# this boundary (whitelisted keys, str/None values, per-field length caps, link
# list capped at services.network_selection.MAX_SELECTED_LINKS) before any
# service sees it; the values themselves are then re-resolved against the
# parsed active .inpx rather than believed. Shared by every request model below
# via this mixin so they can never drift apart.
#
# MULTI-SELECT: the diagram now supports shift+click / shift+drag, so a request
# may carry SEVERAL elements (`selected_elements`) instead of the single
# `selected_element` P6 defined. Both are accepted; `_merge_element_selection`
# below is the ONE place the two are reconciled, so no model can implement the
# back-compat rule differently. This matters concretely: on the real client
# model an approach carries three vehicle inputs at one coordinate (one per
# vehicle class), and only the plural field can refer to that approach.
class _SelectionFieldsMixin(BaseModel):
    selected_link_ids: list[str] = Field(default_factory=list)
    selected_element: dict | None = None
    # Multi-select. Always the FULL selection after merging: a client that
    # sends only the singular field still finds its element here, so every
    # consumer can read this one list and ignore the legacy field.
    selected_elements: list[dict] = Field(default_factory=list)
    # How many further element references the cap dropped — NOT client input
    # (the model validator always overwrites whatever was sent). Surfaced so
    # the prompt can say the selection was truncated instead of quietly
    # answering about a subset of what the user selected.
    selected_elements_omitted: int = 0

    @field_validator("selected_link_ids", mode="before")
    @classmethod
    def _clean_link_ids(cls, value: Any) -> list[str]:
        return sanitize_selected_link_ids(value)

    @field_validator("selected_element", mode="before")
    @classmethod
    def _clean_element(cls, value: Any) -> dict | None:
        return sanitize_selected_element(value)

    @field_validator("selected_elements", mode="before")
    @classmethod
    def _clean_elements(cls, value: Any) -> list[dict]:
        # Idempotent second line of defence: the model validator below has
        # already merged+sanitized this field, and re-sanitizing a clean list
        # is a no-op, but a model constructed field-by-field still cannot get
        # an unsanitized element list past this boundary.
        return sanitize_selected_elements(value)

    @model_validator(mode="before")
    @classmethod
    def _merge_element_selection(cls, data: Any) -> Any:
        """Fold the singular element channel into the plural one — the single
        implementation of the back-compat rule (see
        ``services.network_selection.merge_element_selection``): plural list
        first in the client's own order, then ``selected_element`` appended
        unless the list already holds that ``(type, id)``.

        A pre-multi-select client therefore keeps working EXACTLY as before —
        it sends only ``selected_element``, which stays populated and reaches
        every existing consumer untouched, and additionally shows up as the
        one entry of ``selected_elements``.
        """
        if not isinstance(data, dict):
            return data
        elements, omitted = merge_element_selection(
            data.get("selected_elements"), data.get("selected_element")
        )
        return {
            **data,
            "selected_elements": elements,
            "selected_elements_omitted": omitted,
        }


class AssistantQueryRequest(_SelectionFieldsMixin):
    message: str = Field(min_length=1)
    selected_scenario_ids: list[str] = Field(default_factory=list)
    selected_node_ids: list[str] = Field(default_factory=list)
    allow_tool_execution: bool = False
    response_language: str = "auto"


class AgentQueryRequest(_SelectionFieldsMixin):
    """P7: the agent turn carries the SAME on-screen selection the plain
    assistant path has carried since P6 — plus node/scenario ids, which had
    never reached the agent at all (see docs/phase2/DECISIONS.md's P6 entry,
    "Known gap"). Every field defaults to empty, so a pre-P7 client's request
    body is unchanged and produces a byte-identical turn.

    ``selected_link_ids``/``selected_element``/``selected_elements`` are
    validated by the shared mixin (which is also the one place the
    singular/plural element back-compat rule lives). ``selected_node_ids``/``selected_scenario_ids`` get the same
    treatment here (stripped, de-duplicated, char-capped, count-capped at
    ``MAX_SELECTED_NODES``/``MAX_SELECTED_SCENARIOS``) rather than in the
    mixin, because ``AssistantQueryRequest``'s own copies of those two fields
    are consumed by a DIFFERENT downstream (``results_reader``'s node-detail
    lookups, which do their own compaction) and tightening them here would be
    an unrelated behavior change to the plain path.
    """

    message: str = Field(min_length=1)
    response_language: str | None = "ko"
    # M3.3: optional session reuse -- omitted/None (every pre-M3.3 client)
    # mints a fresh session exactly as before; a client that supplies the
    # session_id from an earlier turn's `done.response.session_id` resumes
    # that SAME work dir (trace appends -- append-mode by design -- and
    # services.agent.session_store reloads that session's AgentState), so a
    # follow-up message carries forward its history, accumulated guardrail
    # warnings, and (if the previous turn ended in a clarification question)
    # the original goal for the planner to reconcile with this reply. See
    # `_SESSION_ID_RE`'s validation below -- the SAME client-input-boundary
    # check `/agent/approve` already applies, now shared by both endpoints
    # via that one module-level pattern.
    session_id: str | None = None
    # P7: the other two selection channels (links/element come from the mixin).
    selected_scenario_ids: list[str] = Field(default_factory=list)
    selected_node_ids: list[str] = Field(default_factory=list)

    @field_validator("selected_node_ids", mode="before")
    @classmethod
    def _clean_node_ids(cls, value: Any) -> list[str]:
        return sanitize_selected_node_ids(value)

    @field_validator("selected_scenario_ids", mode="before")
    @classmethod
    def _clean_scenario_ids(cls, value: Any) -> list[str]:
        return sanitize_selected_scenario_ids(value)


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
    # Phase 1.3 (audit-trail export): the reviewer's one-line rationale for
    # this decision, recorded into the session's action ledger so the
    # "파라미터 변경 기록 + 근거" report section can print WHY a change was
    # allowed, not just that it was. Optional and additive — every client
    # that predates this field (and the frontend today) simply omits it, and
    # the ledger entry then carries no rationale, exactly as before.
    # Length-capped because it goes straight into a report table cell.
    reason: str | None = Field(default=None, max_length=MAX_APPROVAL_REASON_CHARS)


class CurateRequest(_SelectionFieldsMixin):
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
    # P6: the same diagram selection the chat path gets, resolved the same way.
    # Each block degrades independently — a bad/missing .inpx must never 500 a
    # curation request that would otherwise have answered fine.
    if payload.selected_link_ids:
        try:
            context["selected_links"] = resolve_selected_links(payload.selected_link_ids)
        except Exception:
            logger.exception("Curation context: selected link details failed")
    # Multi-select: `selected_elements` already holds the merged full selection
    # (the singular field folded in by _SelectionFieldsMixin), and
    # element_selection_fields keeps the one-object case on the legacy
    # `selected_element` key, so a pre-multi-select curation context is
    # unchanged while a group of three arrives as three named objects.
    if payload.selected_elements:
        try:
            context.update(
                element_selection_fields(
                    resolve_selected_elements(payload.selected_elements),
                    payload.selected_elements_omitted,
                    prefix="selected_",
                )
            )
        except Exception:
            logger.exception("Curation context: selected element details failed")

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

    # Unit 1.4: captured HERE, on the request-handling thread (which the
    # workspace middleware has scoped), not inside the response generator --
    # a StreamingResponse body is iterated later, on a different hop.
    request_workspace_id = workspaces.current_workspace_id()

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

        # ContextVars do not cross threading.Thread, so the captured request
        # workspace is re-bound inside the worker -- assistant_router.
        # _build_context resolves the active dataset and would otherwise raise
        # WorkspaceScopeError.
        def run() -> None:
            try:
                with workspaces.bind_workspace(request_workspace_id):
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
    """Per-session agent scratch dir.

    Unit 1.4: delegates to ``services.agent.sessions.session_work_dir`` rather
    than rebuilding ``runtime/agent_sessions/<id>`` from ``REPO_ROOT`` a second
    time — that duplicate was carrying an explicit "must stay in sync" caveat
    in sessions.py's own docstring, which is a bug waiting for whoever moves
    the root. ``SESSIONS_ROOT`` is imported into this module so tests can
    monkeypatch it here the same way they already do on
    ``routers.network``/``routers.agent_downloads``.
    """
    return session_work_dir(session_id, SESSIONS_ROOT)


def _session_source_inpx(session_id: str, work_dir: Path) -> Path:
    """The .inpx this session was (or will be) cloned from — frozen at session
    creation, never re-resolved from the live active dataset.

    Unit 1.4's isolation requirement: an ``ArtifactManager`` built from
    ``get_active_inpx_path()`` on EVERY request means that swapping the
    workspace's active dataset mid-session silently repoints the session's
    ``rollback_to(0)`` (and ``_ensure_working_copy``'s re-sync) at a DIFFERENT
    network — the user asks to undo one edit and gets somebody else's model
    written into their working copy. The first request for a session records
    ``{workspace_id, source_inpx, dataset_revision}`` in
    ``session_meta.json``; every later request for the same session reads it
    back.

    A session whose sidecar is missing/unreadable (pre-1.4 sessions, and every
    test that fabricates a work dir) falls back to live resolution — exactly
    the old behavior — and gets a sidecar written for next time.

    **The write is fatal.** It used to be logged and swallowed, which quietly
    created a LIVE session with no recorded owner. Since
    ``services.agent.sessions.session_workspace_matches`` now fails closed, an
    unowned session is not merely undocumented, it is unreachable — and worse,
    before that fix it was reachable by the WRONG project. Refusing the turn is
    the honest outcome: this project's doctrine is that a refused edit with a
    clear reason beats a completed edit with wrong ownership.
    """
    meta = read_session_meta_dir(work_dir)
    recorded = meta.get("source_inpx")
    if recorded:
        return Path(str(recorded))

    resolved = get_active_inpx_path()
    try:
        write_session_meta(
            work_dir,
            {
                "session_id": session_id,
                "workspace_id": workspaces.current_workspace_id(),
                "source_inpx": str(resolved),
                "dataset_revision": _active_dataset_revision(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring: an unowned session must not exist
        logger.error("Could not record session metadata for %s", session_id, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Could not start the agent session."
        ) from exc
    return resolved


def _session_belongs_to_workspace(session_id: str, workspace_id: str) -> bool:
    """Whether ``session_id`` may be read/resumed from ``workspace_id``.

    Thin delegation to ``services.agent.sessions.session_workspace_matches`` —
    THE ownership predicate, shared verbatim with ``routers/network.py``,
    ``routers/agent_downloads.py`` and ``services/audit_trail.py``. This used to
    be a second, independent copy of the same three lines, which is how it came
    to keep the fail-open default-workspace substitution after that was
    identified as a hole: one boundary rule needs exactly one implementation.

    The root is taken from ``_agent_session_work_dir``'s own answer rather than
    from ``SESSIONS_ROOT`` directly, because THAT function is this module's
    sessions seam: tests patch the function (``test_agent_audit_endpoint.py``)
    as well as the constant, and an ownership check that consulted a different
    root than the one the pending-approval file is read from would be checking
    a different session than it acts on.
    """
    return session_workspace_matches(
        session_id, workspace_id, _agent_session_work_dir(session_id).parent
    )


def _active_dataset_revision() -> str | None:
    """Best-effort dataset revision for the session sidecar. Never raises: it
    is provenance for the audit trail, not a correctness input."""
    try:
        from services.datasets import get_active_dataset

        return str(get_active_dataset().get("revision") or "") or None
    except Exception:  # noqa: BLE001 - provenance only
        return None


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


def _record_approval_decision(
    work_dir: Path,
    session_id: str,
    pending: dict[str, Any],
    *,
    approved: bool,
    reason: str | None,
    actor: dict[str, Any] | None = None,
) -> None:
    """Phase 1.3: append the human's approve/decline decision — their one-line
    rationale, when given, and (U8) WHO they are — to the session's action
    ledger.

    Written as a ``user_confirm`` record (the kind ``orchestrator.py`` already
    uses for ``stage="requested"`` when it pauses for approval), with
    ``stage="approved"``/``"declined"`` plus the tool/params the decision
    covers, so ``services/audit_trail.py`` can attach the rationale to the
    write that follows it. This is the ONLY new thing the approval path
    writes: it does not touch the pending file, the artifact manager, the
    gate, or the re-run itself.

    ``actor`` is ``_approval_actor``'s dict for the authenticated approver.
    It defaults to ``None`` and is written as ``None`` when absent, so this
    helper keeps its pre-U8 call signature working and a ledger line stays the
    same shape either way; ``audit_trail.normalize_actor`` renders anything
    that is not a complete U8 actor as "기록 없음(인증 이전)".

    Best-effort by design. A ledger append that fails (an unwritable work dir,
    a session id the trace layer rejects) is logged and swallowed — an audit
    nicety must never be able to block a user's approved edit from running.
    """
    try:
        actions = pending.get("actions") or []
        action = actions[0] if isinstance(actions, list) and actions else {}
        if not isinstance(action, dict):
            action = {}
        TraceWriter(work_dir, session_id).write(
            "user_confirm",
            {
                "stage": "approved" if approved else "declined",
                "approval_id": pending.get("approval_id"),
                "tool": action.get("tool"),
                "params": action.get("params") or {},
                "summary": action.get("summary") or "",
                "reason": reason or None,
                "actor": actor or None,
            },
        )
    except Exception:  # noqa: BLE001 - see the docstring: never block the edit
        logger.warning("Could not record the approval decision in the action ledger", exc_info=True)


# ---------------------------------------------------------------------------
# Bug 1 root-cause fix (docs/phase2/DECISIONS.md): per-session_id turn lock
# ---------------------------------------------------------------------------
#
# ``run_agent_turn`` is only crash-safe against a SEQUENCE of calls sharing
# a ``work_dir``/``session_id`` -- ``session_store.load_state`` and
# ``ArtifactManager.__init__``'s version scan both read whatever is on disk
# at the moment they run, and ``session_store.save_state``/
# ``ArtifactManager.save_version``/``rollback_to`` both write unconditionally
# ("version counter is single-instance by design", "no cross-process lock" --
# state.py's own docstring). Two ``run_agent_turn`` calls for the SAME
# session_id running on OVERLAPPING background threads (e.g. a client that
# fires its next message before the previous turn's NDJSON stream has fully
# resolved on the server -- entirely possible for a write/run turn, which can
# take many real seconds of Vissim COM time) each independently load the SAME
# stale ``state.json``/work-dir snapshot, mutate their OWN in-memory copy, and
# independently persist at the end -- whichever turn's ``save_state``/
# ``ArtifactManager`` write lands LAST silently clobbers the other's, and a
# turn that constructs its own ``ArtifactManager`` while a concurrent write is
# still in flight can see version 0 and roll ``working.inpx`` back to the
# pristine clone out from under it. This reproduced deterministically with
# controlled thread timing (see test_agent_approval_endpoint.py's
# ``test_concurrent_turn_after_approve_does_not_lose_state_or_working_copy``)
# and explains BOTH halves of the live-rehearsal symptom (an apparently
# "fresh" session history AND a working copy that shows the pre-edit value)
# from one mechanism, without requiring any of the id/path-mismatch
# candidates (which a serial repro shows are NOT broken) to be at fault.
#
# Fix: serialize every ``run_agent_turn`` call for one ``session_id`` behind
# a per-session lock covering the whole ensure-working-copy -> run_agent_turn
# critical section (including the ``tools_network``/``tools_run``/edit-session
# module-global context those calls set), so a same-session turn always
# starts from the PRIOR turn's fully-persisted state instead of racing it.
# This is in-process only (matches the file-based-storage, single-instance
# design everywhere else in this module) -- it does not, and does not need
# to, solve cross-process or multi-worker deployment, which this app does not
# run under today.

_SESSION_LOCKS_GUARD = threading.Lock()
_session_turn_locks: dict[str, threading.Lock] = {}

#: Generous: must comfortably exceed a real Vissim COM write/run turn so a
#: legitimately slow (not stuck) prior turn is never mistaken for a deadlock.
_SESSION_LOCK_TIMEOUT_S = 300.0

# ---------------------------------------------------------------------------
# Unit 1.4, disclosed interim measure: a PROCESS-WIDE agent-turn mutex
# ---------------------------------------------------------------------------
#
# The per-session lock above only serializes turns sharing a session_id. But
# an agent turn also installs FOUR process-wide module globals for its
# duration -- ``tools_network._active_inpx_override``,
# ``tools_run._active_run_source``, ``tools_run._on_run_started`` and
# ``edit_session._active_edit_session`` -- which are not session-scoped at all.
# Two turns from DIFFERENT sessions overlapping therefore clobber each other
# today: whichever set the globals last wins, and the other turn's read tools
# silently answer against the wrong working copy. Before workspaces that was a
# same-dataset annoyance; with two projects on one box it is a cross-client
# data leak, which this unit is specifically about closing.
#
# The correct fix is to plumb the active .inpx / run source / edit session
# explicitly through ``run_agent_turn`` instead of through module globals, and
# that is deliberately OUT of scope here (it touches the orchestrator and every
# tool module). The honest interim measure is this global mutex: agent turns
# serialize across ALL sessions, so the globals can never be observed by a
# turn that did not set them. It costs concurrency -- a second user's chat
# waits for the first user's Vissim write to finish -- and that cost is stated
# rather than hidden.
#
# Lock ORDER is always session-lock then global-lock (never the reverse), so
# two threads holding different session locks cannot deadlock waiting on each
# other. Each acquisition keeps the existing _SESSION_LOCK_TIMEOUT_S semantics
# and raises the same SessionBusyError, so the caller-visible behavior of "a
# turn that cannot start says so instead of hanging forever" is unchanged.
_GLOBAL_TURN_LOCK = threading.Lock()


class SessionBusyError(RuntimeError):
    """A second turn for the same ``session_id`` could not acquire the
    session lock within :data:`_SESSION_LOCK_TIMEOUT_S`. Surfaces through
    ``_stream_agent_turn``'s existing generic exception handling as a clear,
    apologetic refusal -- never a silent data race."""


def _get_session_turn_lock(session_id: str) -> threading.Lock:
    with _SESSION_LOCKS_GUARD:
        lock = _session_turn_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_turn_locks[session_id] = lock
        return lock


@contextmanager
def _session_turn_scope(session_id: str) -> Iterator[None]:
    """Serializes ``run_agent_turn`` calls sharing ``session_id`` (the original
    Bug 1 fix) AND, since unit 1.4, every agent turn process-wide (see the
    ``_GLOBAL_TURN_LOCK`` rationale above).

    Raises :class:`SessionBusyError` (never hangs forever) if either lock is
    not obtained within :data:`_SESSION_LOCK_TIMEOUT_S`.
    """
    lock = _get_session_turn_lock(session_id)
    if not lock.acquire(timeout=_SESSION_LOCK_TIMEOUT_S):
        raise SessionBusyError(
            f"Timed out waiting for an earlier turn on session {session_id!r} to finish."
        )
    try:
        if not _GLOBAL_TURN_LOCK.acquire(timeout=_SESSION_LOCK_TIMEOUT_S):
            raise SessionBusyError(
                "Timed out waiting for another agent turn to finish. Agent turns are "
                "serialized process-wide while the per-turn module globals are still "
                "shared state."
            )
        try:
            yield
        finally:
            _GLOBAL_TURN_LOCK.release()
    finally:
        lock.release()


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

    Bug 1 fix (docs/phase2/DECISIONS.md): the caller constructs
    ``artifact_manager`` synchronously in the request handler, BEFORE
    ``_session_turn_scope`` is acquired — if an earlier turn for the same
    session was still mid-write at that moment, the ``.version`` this
    instance captured at construction can already be stale by the time this
    function actually runs (which only happens once the earlier turn's lock
    is released, i.e. once it has FULLY persisted). ``refresh_version()``
    re-scans the work dir right here, under the lock, so ``rollback_to()``
    below always targets the true latest saved version instead of whatever
    was on disk a request-handling-thread's-worth of time ago.
    """
    artifact_manager.refresh_version()
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


def _resolve_agent_selection(
    payload: AgentQueryRequest, artifact_manager: ArtifactManager
) -> dict[str, Any] | None:
    """P7: the compact "what the user clicked" structure for this agent turn.

    **Resolution precedence: this session's working copy, else the active
    dataset.** Once a session has a ``working.inpx`` (its clone, plus any
    approved+verified edit), that file — not the pristine source dataset — is
    what the user is looking at (``routers/network.py``'s P2 session view
    renders exactly this file) and what every read tool in the turn will see
    (``tools_network.set_active_inpx(artifact_manager.working_path)`` in
    ``_stream_agent_turn``). Resolving the selection anywhere else would let
    the planner be told a link's pre-edit identity while its own tools report
    the post-edit one. A brand-new session has no working copy yet at this
    point (``_ensure_working_copy`` runs later, on the turn's worker thread,
    under the session lock), so it falls back to the active dataset — which
    is byte-identical to the clone that is about to be made from it.

    Returns ``None`` when nothing is selected. Never raises: a selection that
    cannot be resolved is dropped (the turn still runs, just without the
    reference) rather than 500ing a chat request.
    """
    try:
        working_path = artifact_manager.working_path
        inpx_path = working_path if working_path.is_file() else None
        return build_selection_context(
            node_ids=payload.selected_node_ids,
            link_ids=payload.selected_link_ids,
            # Multi-select: the merged list is authoritative (it already holds
            # the singular field when a pre-multi-select client sent one);
            # `element` is passed too because build_selection_context merges
            # them itself, which keeps this call correct even if a caller ever
            # builds a payload without going through the mixin.
            element=payload.selected_element,
            elements=payload.selected_elements,
            elements_omitted=payload.selected_elements_omitted,
            scenario_ids=payload.selected_scenario_ids,
            inpx_path=inpx_path,
        )
    except Exception:  # noqa: BLE001 - context is a nice-to-have, never a 500
        logger.exception("Agent selection context could not be resolved; continuing without it")
        return None


_APPROVAL_DECLINED_TEXT = {
    "ko": "요청하신 작업을 취소했습니다. 네트워크는 변경되지 않았습니다.",
    "en": "The requested action was cancelled. The network was left unchanged.",
    "ja": "リクエストされた操作はキャンセルされました。ネットワークは変更されていません。",
}

# ---------------------------------------------------------------------------
# NDJSON heartbeat: a long-running tool call (e.g. a 2+ minute RunContinuous
# simulation) produces no `on_step` narration until it finishes -- see
# run_agent_turn's own docstring, "on_step ... called once per narrated
# step" -- so the queue-drain loop below can otherwise sit silent for
# minutes. An idle NDJSON connection that long is exactly what an
# intermediary (observed: the Next.js dev proxy) will kill, at which point
# the backend's own request.is_disconnected() poll correctly cancels the
# turn -- but the browser's now-dead fetch/reader is never told anything
# happened (docs/phase2/DECISIONS.md, 2026-07-24 heartbeat entry). A `ping`
# event emitted whenever the queue has been silent for
# _stream_ping_interval_seconds() keeps the connection demonstrably alive so
# an idle-timeout intermediary never has a silent gap long enough to act on.
# Both /agent/query/stream and /agent/approve share this one generator, so
# one change covers both endpoints.
# ---------------------------------------------------------------------------

DEFAULT_STREAM_PING_INTERVAL_S = 15.0


def _stream_ping_interval_seconds() -> float:
    """Max seconds the NDJSON queue-drain loop may stay silent before it
    emits a heartbeat `ping`. Read fresh on every call (never cached, same
    pattern as ``services.agent.structured.ladder_ceiling_seconds``) so a
    test can shrink it via ``AGENT_STREAM_PING_INTERVAL_S`` without a
    process restart. Falls back to :data:`DEFAULT_STREAM_PING_INTERVAL_S` on
    anything unset, unparsable, or non-positive."""
    raw = os.getenv("AGENT_STREAM_PING_INTERVAL_S", "").strip()
    if not raw:
        return DEFAULT_STREAM_PING_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STREAM_PING_INTERVAL_S
    return value if value > 0 else DEFAULT_STREAM_PING_INTERVAL_S

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
    selection: dict[str, Any] | None = None,
    workspace_id: str | None = None,
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

    # RUN-PROGRESS UI (2026-07-28): run_simulation blocks inside
    # tools_run._run_job_and_wait's poll loop for the run's ENTIRE duration
    # (a multi-seed batch can be 10+ minutes) before toolkit.dispatch ever
    # returns and an on_step "step" event can fire — that gap is exactly the
    # "Preparing answer..." with zero indication bug this milestone fixes.
    # tools_run.set_run_started_callback fires as soon as the job is
    # SUBMITTED (before the poll loop starts), so the frontend learns the
    # job_id immediately and can poll GET /api/simulations/{job_id} for
    # seed-boundary progress on its own, independent of this NDJSON stream
    # (which stays blocked on the same turn either way). A dedicated
    # `run_started` event, not reusing the `ping` heartbeat: ping is a
    # content-free liveness signal on a fixed interval (any payload on it
    # would need the SAME poll-vs-push disambiguation this event exists to
    # avoid), and a job can start at any offset inside that interval — an
    # event tied to the actual moment of submission is simpler than piggy-
    # backing on an unrelated timer.
    def on_run_started(job_id: str) -> None:
        step_queue.put(("run_started", {"job_id": job_id}))

    # Unit 1.4: the turn body runs on a plain threading.Thread, which does NOT
    # inherit the request's ContextVars — so the workspace this request
    # resolved to is captured here and re-bound inside the thread. Without it,
    # every dataset accessor the turn reaches (tools_run.run_simulation ->
    # create_job_record, tools_edit's rollback source resolution, ...) would
    # raise WorkspaceScopeError. Falls back to the current scope so a caller
    # that did not pass one still works inside a request.
    bound_workspace_id = workspace_id or workspaces.current_workspace_id()

    def run() -> None:
        # The bind itself is inside the try/finally that publishes "end": if it
        # ever raised (a malformed id), skipping that publish would leave the
        # NDJSON drain loop below waiting forever instead of streaming an
        # apologetic `done`.
        try:
            with workspaces.bind_workspace(bound_workspace_id):
                _run_turn_body()
        except Exception as exc:  # noqa: BLE001 - surfaced as an apologetic `done`
            holder.setdefault("error", exc)
            step_queue.put(("end", None))

    def _run_turn_body() -> None:
        try:
            # Bug 1 root-cause fix (see _session_turn_scope's docstring):
            # serialize the ENTIRE ensure-working-copy -> run_agent_turn
            # critical section per session_id, so a same-session turn
            # started while an earlier one (e.g. an in-flight approve) is
            # still persisting can never race it and silently lose the
            # other's state.json history or working-copy write.
            with _session_turn_scope(session_id):
                _ensure_working_copy(artifact_manager)
                # P8 (edited-model download): the version number right after
                # ensure-working-copy, BEFORE run_agent_turn dispatches
                # anything -- the baseline a post-turn comparison needs to
                # tell "this turn itself wrote something" from "this session
                # already had edits from an earlier turn". artifact_manager
                # is the SAME instance run_agent_turn dispatches writes
                # through (orchestrator.py calls artifact_manager.
                # save_version() directly on it), so `.version` after the
                # turn already reflects any write THIS turn made -- no
                # refresh_version() rescan needed.
                holder["version_before"] = artifact_manager.version
                # M1-inherited module-global session context (tools_network.py's
                # own documented caveat): points every read tool at THIS turn's
                # working copy, not the source dataset, so an edit made earlier
                # in this session is visible to subsequent reads within it.
                # Reset in `finally` so a later, unrelated request never
                # inherits it — hygiene on top of the pre-existing, tracked
                # concurrency limitation (docs/phase2/DECISIONS.md, "Future
                # multi-dataset work"), not a fix for it.
                tools_network.set_active_inpx(artifact_manager.working_path)
                # M2.4: tools_run.py's run_simulation submits THIS turn's working
                # copy to the simulation job queue — a SEPARATE module-global
                # from tools_network's (mirrors edit_session.py's own active-
                # session pattern rather than reaching into tools_network's
                # private resolver; see tools_run.py's module docstring). Reset
                # in `finally` alongside tools_network's, same hygiene rationale.
                tools_run.set_active_run_source(artifact_manager.working_path)
                tools_run.set_run_started_callback(on_run_started)
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
                        # P7: context only — the planner learns WHICH objects
                        # the user clicked. It changes no gate, no tool
                        # permission and no preflight (see run_agent_turn's
                        # own `selection` docs); the same ProposeWriteGate /
                        # GuardrailEngine still authorize and validate every
                        # action, on explicitly named targets.
                        selection=selection,
                    )
                finally:
                    if edit_session is not None:
                        clear_active_edit_session()
                        edit_session.close()
                    tools_network.set_active_inpx(None)
                    tools_run.clear_active_run_source()
                    tools_run.clear_run_started_callback()
        except Exception as exc:  # noqa: BLE001 - surfaced as an apologetic `done`, never raised
            holder["error"] = exc
        finally:
            step_queue.put(("end", None))

    threading.Thread(target=run, name=f"agent-stream-{stream_id}", daemon=True).start()

    # Accumulated so the exception branch below can still report whatever
    # steps actually streamed before the failure (same rationale as M1.4).
    streamed_steps: list[dict[str, Any]] = []

    ping_interval = _stream_ping_interval_seconds()
    last_emit = time.monotonic()

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
                now = time.monotonic()
                if now - last_emit >= ping_interval:
                    last_emit = now
                    yield encode_stream_event({"type": "ping", "stream_id": stream_id})
                await asyncio.sleep(0.03)
                continue
            last_emit = time.monotonic()
            if kind == "end":
                break
            if kind == "run_started":
                yield encode_stream_event(
                    {"type": "run_started", "stream_id": stream_id, "job_id": value.get("job_id")}
                )
                continue
            summary = value.get("summary", "")
            tool = value.get("tool", "")
            status = value.get("status", "")
            streamed_step: dict[str, Any] = {"summary": summary, "tool": tool, "status": status}
            step_event: dict[str, Any] = {
                "type": "step",
                "stream_id": stream_id,
                "summary": summary,
                "tool": tool,
                "status": status,
                "index": value.get("index", 0),
            }
            # trajectory_recorded/job_id (run_simulation only, orchestrator's
            # own choke point) -- optional, forwarded as-is when present so
            # the frontend's replay hint doesn't need to wait for `done`.
            if "trajectory_recorded" in value:
                streamed_step["trajectory_recorded"] = value["trajectory_recorded"]
                step_event["trajectory_recorded"] = value["trajectory_recorded"]
            if "job_id" in value:
                streamed_step["job_id"] = value["job_id"]
                step_event["job_id"] = value["job_id"]
            streamed_steps.append(streamed_step)
            yield encode_stream_event(step_event)
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
                    # P8: a turn that errored never gets credit for a write,
                    # even if `version_before` was captured before the
                    # failure -- there is no way from here to tell "the
                    # write landed, then something else in this turn failed"
                    # apart from "the write itself failed", so the safe
                    # default is always False on this path.
                    "wrote_model": False,
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
        # Approval-card mini-map (v2 UX unit): the focus hint
        # orchestrator.py's ApprovalRequired branch already resolved
        # (services.agent.approval_focus.resolve_focus — never raises, so
        # this key is always present, possibly the "no focus" shape).
        # Attached at the PAYLOAD level (sibling of actions/warnings), not
        # inside action_entry — orchestrator.py only ever proposes ONE
        # action per pause (same "singular, not a list" limitation
        # `warnings` already documents above), and keeping action_entry
        # byte-unchanged means the existing exact-equality assertions on
        # `approval_event["actions"]` (test_agent_approval_endpoint.py,
        # test_agent_run_approval_endpoint.py) needed no changes at all.
        focus = result.pending_approval.get("focus")
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
            "focus": focus,
        }
        # P7: replayed verbatim by the approve turn, for the same reason
        # `message`/`language` are — the approved re-run must reconstruct the
        # SAME turn, and the approve request has no selection of its own
        # (AgentApproveRequest deliberately carries only the ids that identify
        # the pending approval). Already resolved + sanitized when this turn
        # started, and it authorizes nothing: the re-run is scoped to the
        # approved action's signature by PreapprovedWriteGate regardless.
        if selection:
            pending_file_payload["selection"] = selection
        _write_pending_approval(work_dir, pending_file_payload)
        approval_payload = {
            "approval_id": approval_id,
            "actions": [action_entry],
            "warnings": warnings,
            "assumptions": assumptions,
            "focus": focus,
        }
        yield encode_stream_event(
            {"type": "approval_request", "stream_id": stream_id, **approval_payload}
        )

    for chunk in chunk_answer_for_stream(result.answer):
        yield encode_stream_event({"type": "chunk", "stream_id": stream_id, "text": chunk})

    # P8 (edited-model download): did THIS turn persist a write? Nothing on
    # the wire marks this today -- a successful write step's `status` is
    # "ok", indistinguishable from a successful READ step's "ok" without
    # also knowing the tool's tier, which no step payload carries either
    # (and orchestrator.py's step_payload dict is asserted against by exact
    # equality in test_agent_orchestrator.py / test_agent_orchestrator_
    # approval.py, so adding a key there is not the low-risk move it looks
    # like). The precise, already-available signal instead: whether THIS
    # artifact_manager's version advanced past the value captured right
    # after _ensure_working_copy, in `run()` above -- version only ever
    # moves via save_version(), which orchestrator.py calls exactly once per
    # successfully dispatched + postwrite-verified write tool. A turn that
    # never reached that line (e.g. the very first `_ensure_working_copy`
    # itself raised) has no "version_before" to compare against, so this
    # degrades to False rather than raising.
    version_before = holder.get("version_before")
    wrote_model = version_before is not None and artifact_manager.version > version_before

    done_response: dict[str, Any] = {
        "answer": result.answer,
        "kind": result.kind,
        "steps": result.steps,
        "session_id": session_id,
        "wrote_model": wrote_model,
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

    M3.3: ``payload.session_id``, when supplied, resumes that session's work
    dir/trace/state instead of minting a fresh one — see
    ``AgentQueryRequest.session_id``'s own field comment and
    ``services.agent.orchestrator.run_agent_turn``'s reload contract. Every
    ``done`` event still carries ``response.session_id`` regardless of
    whether it was client-supplied or freshly minted, so a client can always
    resume the returned id on its next message.

    P7: the request may also carry what the user has CLICKED in the rendered
    network (``selected_node_ids``/``selected_link_ids``/``selected_element``
    or its multi-select successor ``selected_elements``/
    ``selected_scenario_ids``) — resolved by ``_resolve_agent_selection``
    against this session's working copy when it has one, else the active
    dataset, and handed to ``run_agent_turn`` as planner context. Context
    only: no gate, tool permission, or preflight behavior changes with it.
    """
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    language = (payload.response_language or "ko").strip().lower() or "ko"

    # M3.3: a client-supplied session_id resumes that session's work dir
    # (services.agent.session_store reloads its AgentState at turn start --
    # see orchestrator.run_agent_turn); no session_id (every pre-M3.3
    # client) mints a fresh one exactly as before. Same validation as
    # `/agent/approve`'s existing client-input boundary -- a malformed id is
    # a 400 here (unlike approve's 404: there is no existing pending-approval
    # resource being addressed, just a bad request) rather than silently
    # falling back to a fresh session, since silently ignoring a client's
    # explicit resume request would be a confusing, hard-to-debug surprise.
    if payload.session_id is not None:
        if not _SESSION_ID_RE.match(payload.session_id):
            raise HTTPException(status_code=400, detail="Invalid session_id.")
        session_id = payload.session_id
    else:
        session_id = uuid.uuid4().hex[:12]
    work_dir = _agent_session_work_dir(session_id)
    workspace_id = workspaces.current_workspace_id()
    # M3.3 resume + unit 1.4 isolation: a client-supplied session_id may name a
    # session that belongs to ANOTHER project. Refuse it the same way an
    # unknown pending approval is refused, rather than letting this workspace
    # keep editing that workspace's working copy.
    if payload.session_id is not None and not _session_belongs_to_workspace(session_id, workspace_id):
        raise HTTPException(status_code=404, detail="No matching agent session.")
    artifact_manager = ArtifactManager(
        source_inpx=_session_source_inpx(session_id, work_dir), work_dir=work_dir
    )
    engine = GuardrailEngine()
    gate = ProposeWriteGate()
    # P7: resolved HERE, in the request handler, rather than on the streaming
    # worker thread — it is pure file reads (parse + lru_cache), it must not
    # sit inside the per-session turn lock, and resolving before the turn
    # starts keeps the failure mode trivial (no selection, turn proceeds).
    selection = _resolve_agent_selection(payload, artifact_manager)

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
            selection=selection,
            workspace_id=workspace_id,
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
def approve_agent_action(
    payload: AgentApproveRequest,
    request: Request,
    approver: dict[str, Any] = Depends(require_approver),
):
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

    **U8 (roadmap 2.5): this is the only endpoint outside ``/api/auth`` that
    requires an identity.** Both legs — approve AND decline — require a signed-in
    ``reviewer`` or ``admin`` (``require_approver``); a modeler gets 403 and
    an anonymous caller 401, and in BOTH cases the pending approval survives
    untouched, because the dependency resolves before this body runs and the
    file is consumed inside it. The approver's identity is recorded in the
    ledger, which is where the audit DOCX's "승인자" column comes from.
    Until U9 ships the login UI the frontend's approval card will receive
    401 — sequenced deliberately: an approval history that cannot name its
    approver is the thing this unit exists to end.
    """
    if not _SESSION_ID_RE.match(payload.session_id):
        raise HTTPException(status_code=404, detail="No matching pending approval.")

    workspace_id = workspaces.current_workspace_id()
    # Unit 1.4: approving another project's pending write would execute a
    # Vissim edit against a network this request has no business touching.
    # Same opaque 404 as an unknown/stale approval — never a distinguishable
    # "exists but is not yours".
    if not _session_belongs_to_workspace(payload.session_id, workspace_id):
        raise HTTPException(status_code=404, detail="No matching pending approval.")

    work_dir = _agent_session_work_dir(payload.session_id)
    pending = _read_pending_approval(work_dir)
    if pending is None or pending.get("approval_id") != payload.approval_id:
        raise HTTPException(status_code=404, detail="No matching pending approval.")

    # Single-use: consumed the moment it is validated, regardless of outcome.
    _delete_pending_approval(work_dir)

    # Phase 1.3: the decision (and its one-line rationale, if the reviewer gave
    # one) lands in the ledger BEFORE the approved re-run appends its own
    # plan/action records, which is what lets the audit export attach the
    # rationale to the write that follows it.
    _record_approval_decision(
        work_dir,
        payload.session_id,
        pending,
        approved=payload.approved,
        reason=(payload.reason or "").strip() or None,
        actor=_approval_actor(approver),
    )
    logger.info(
        "agent approval %s: session %s %s by %s (%s)",
        payload.approval_id,
        payload.session_id,
        "approved" if payload.approved else "declined",
        approver["username"],
        approver["role"],
    )

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

    artifact_manager = ArtifactManager(
        source_inpx=_session_source_inpx(payload.session_id, work_dir), work_dir=work_dir
    )
    engine = GuardrailEngine()
    gate = PreapprovedWriteGate(set(pending.get("signatures") or []))
    # M2.4: a run-tier approval needs the artifact manager + active-inpx
    # context (set in _stream_agent_turn's run(), same as every turn) but
    # NOT a live VissimEditSession — the job queue owns COM for runs (see
    # docs/phase2/DECISIONS.md, "VissimEditSession runs COM on the agent
    # turn's worker thread, not the job queue"). Building one anyway would
    # needlessly spend a scarce edit-session concurrency slot (MAX_CONCURRENT
    # _EDIT_SESSIONS=2) launching a Vissim instance no run tool ever touches.
    # `actions` holds exactly one entry today (run_agent_turn returns on the
    # FIRST ApprovalRequired), so this is equivalent to checking that one
    # action's own tier, but stays correct if a future milestone ever
    # batches more than one approved action per turn.
    needs_edit_session = any(
        (spec := default_registry.get(action.get("tool", ""))) is not None and spec.tier == "write"
        for action in actions
    )
    edit_session = _build_edit_session(artifact_manager.working_path) if needs_edit_session else None

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
            # P7: the ORIGINAL turn's selection, replayed from
            # pending_approval.json (see where it is written) so the approved
            # re-run's planner sees exactly the context the proposal did.
            selection=pending.get("selection") if isinstance(pending.get("selection"), dict) else None,
            workspace_id=workspace_id,
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
                # P6: already sanitized by AssistantQueryRequest's validators;
                # the router resolves them against the parsed active network.
                selected_link_ids=payload.selected_link_ids,
                selected_element=payload.selected_element,
                # Multi-select: the full merged selection. The singular field
                # above is kept as-is so nothing that reads it changes.
                selected_elements=payload.selected_elements,
                selected_elements_omitted=payload.selected_elements_omitted,
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

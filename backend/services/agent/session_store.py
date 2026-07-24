"""Session state persistence for multi-turn agent conversations (M3.3).

``orchestrator.run_agent_turn`` is the only production caller: it attempts
:func:`load_state` at the top of every turn (a no-op — returns ``None`` —
for a brand-new session whose work dir has no ``state.json`` yet) and calls
:func:`save_state` at the end of EVERY turn, for every terminal kind
(``answer``/``clarification``/``approval_request``/``cancelled``), via
``orchestrator._finalize_turn``. See that module's docstring for the full
reload/persist contract; this module only owns the file I/O + validation.

**Reload contract.** :func:`load_state` never raises — every failure mode
(missing file, unreadable file, a JSON shape :meth:`AgentState.from_dict`
rejects) degrades to ``None`` with a logged warning, so a caller can always
uniformly fall back to a fresh :class:`~services.agent.state.AgentState`
without a separate existence check, and a corrupt/partial ``state.json``
(e.g. a previous process killed mid-write) can never turn a normal agent
turn into a 500. This mirrors ``state.py``'s own established bias toward
conservative degrade-not-raise behavior at parse boundaries, applied here to
a new failure surface: on-disk session state a previous process may have
failed to finish writing, or a file a human/another tool has hand-edited
into an unrecognized shape.

**Save contract.** :func:`save_state` writes via a temp-file-then-``replace``
so a concurrent reader (a crashed-and-restarted process resuming the same
session) never observes a half-written ``state.json`` — ``os.replace`` is
atomic on both POSIX and Windows. Unlike :func:`load_state`, this function
DOES raise on a real failure (bad ``session_id``, unwritable directory, a
non-JSON-serializable value that snuck into ``AgentState.context``) —
deciding that a persistence failure must never break an already-computed
answer is the CALLER's job (``orchestrator._finalize_turn`` catches broadly
around this call), not this module's; a storage module that silently
swallows its own write failures would hide a real bug from whoever needs to
know one is happening.

**Session-id sanitization.** :func:`services.agent.trace.validate_session_id`
— the SAME allowlist :class:`~services.agent.trace.TraceWriter` enforces
(docs/phase2/DECISIONS.md's M3.3 entry) — is re-checked in both functions
here, defense in depth for a caller that builds ``work_dir`` from an
unsanitized id without routing through ``TraceWriter`` or the HTTP-boundary
check in ``routers/assistant.py`` first. Callers here otherwise supply
``work_dir`` already resolved (mirroring ``TraceWriter(work_dir,
session_id)``'s own two-argument shape) rather than this module rebuilding
``runtime/agent_sessions/<session_id>`` itself — avoiding a second,
independent copy of that base-path constant drifting out of sync with
``routers/assistant.py``'s ``_agent_session_work_dir`` (the existing
precedent for this exact tradeoff is ``sessions.py``'s ``SESSIONS_ROOT``,
which documents the same "must stay in sync" caveat).

This module is intentionally stdlib-only, no COM, no FastAPI.
"""

from __future__ import annotations

import logging
from pathlib import Path

from services.agent.state import AgentState
from services.agent.trace import validate_session_id

logger = logging.getLogger(__name__)

#: Fixed filename inside a session work dir — no session_id embedded (the
#: work dir itself is already session-scoped), same pattern as
#: ``routers/assistant.py``'s ``pending_approval.json``.
STATE_FILENAME = "state.json"


def state_path(work_dir: Path | str) -> Path:
    """The path :func:`save_state`/:func:`load_state` read and write."""
    return Path(work_dir) / STATE_FILENAME


def save_state(work_dir: Path | str, session_id: str, state: AgentState) -> None:
    """Persist ``state`` to ``<work_dir>/state.json``, overwriting any prior
    contents via an atomic temp-file-then-replace.

    Raises:
        ValueError: ``session_id`` fails :func:`~services.agent.trace.
            validate_session_id`.
        OSError: the directory/file could not be created or written.
        TypeError: ``state`` contains a value :meth:`AgentState.to_json`
            (``json.dumps``) cannot serialize.

    See the module docstring for why this function raises rather than
    swallowing its own failures — the caller (``orchestrator._finalize_turn``)
    decides whether a persistence failure should be fatal.
    """
    validate_session_id(session_id)
    target_dir = Path(work_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = state_path(target_dir)
    tmp_path = final_path.with_name(final_path.name + ".tmp")
    tmp_path.write_text(state.to_json(), encoding="utf-8")
    tmp_path.replace(final_path)


def load_state(work_dir: Path | str, session_id: str) -> AgentState | None:
    """Reload a previously-saved :class:`~services.agent.state.AgentState`,
    or ``None`` when there is nothing usable to reload — see the module
    docstring's reload contract. Never raises.
    """
    validate_session_id(session_id)
    path = state_path(work_dir)
    if not path.exists():
        return None
    try:
        payload = path.read_text(encoding="utf-8")
        return AgentState.from_json(payload)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "session_store: could not reload %s (%s: %s); starting a fresh session state "
            "instead",
            path,
            type(exc).__name__,
            exc,
        )
        return None


__all__ = ["STATE_FILENAME", "load_state", "save_state", "state_path"]

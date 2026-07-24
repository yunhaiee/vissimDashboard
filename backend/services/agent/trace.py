"""Append-only JSONL trace log for agent sessions.

Every record written by :class:`TraceWriter` has the shape
``{ts, session_id, kind, payload}`` and is appended as a single line of JSON
to ``<work_dir>/trace_<session_id>.jsonl``. This file is the eval substrate
for later Phase 2 milestones (the M1 orchestrator writes to it, the M5 eval
harness reads it back), so the record shape and the ``kind`` catalog below
must stay stable.

This module is intentionally stdlib-only: every other module in
``backend/services/agent/`` will import it.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# The full catalog of trace record kinds. Unknown kinds are rejected rather
# than silently accepted, since a typo here would corrupt the eval substrate.
#
# "cancelled" was added in M2.2, one kind beyond the roadmap's original
# 10-kind list (docs/phase2/02-ROADMAP.md M0.1) — the cooperative
# cancellation seam threaded through the same orchestrator loop M2.2 already
# edits for the guardrail engine; see docs/phase2/DECISIONS.md.
ALLOWED_KINDS = frozenset(
    {
        "action_start",
        "action_end",
        "plan",
        "preflight",
        "postwrite",
        "postrun",
        "refusal",
        "warning",
        "user_confirm",
        "fallback_rung",
        "cancelled",
    }
)

# M3.3 (docs/phase2/DECISIONS.md, blocking finding carried from the M0+M1
# whole-branch review): every ``session_id`` reaching this module's
# ``work_dir / f"trace_{session_id}.jsonl"`` (or a sibling module's own
# ``work_dir / session_id``-derived path — see ``session_store.py``, which
# reuses this exact pattern/function) MUST be sanitized before it ever
# touches ``Path`` construction, so a crafted id (``"../../../whatever"``,
# an absolute path, a null byte) can never escape ``work_dir``.
#
# Deliberately BROADER than ``routers/assistant.py``'s own
# ``_SESSION_ID_RE`` (``^[0-9a-f]{8,64}$``, the lowercase-hex shape every
# production ``uuid.uuid4().hex[:12]`` id has): that regex is a second,
# STRICTER gate specifically at the HTTP client-input boundary, where there
# is no legitimate reason for a real caller to send anything but hex. This
# module's callers are not only that one HTTP boundary — every orchestrator
# test, eval-harness task (``eval-T001_...``), and live-check script
# (``run-gate-live-check``, ``live-check-phase-b-<suffix>``) constructs a
# ``TraceWriter`` with a short, human-readable, non-hex session id — a
# literal copy of the hex-only pattern here would reject every one of those
# call sites. The invariant that actually matters at THIS boundary is "no
# path-traversal metacharacter", not "looks like a UUID": excluding ``.``,
# ``/``, ``\\``, and whitespace from the allowed character class blocks
# traversal (``..`` cannot even partially match) while staying compatible
# with every existing session-id shape in this codebase. See
# docs/phase2/DECISIONS.md's M3.3 entry for the full gap-protocol writeup —
# this is a deliberate "closest verifiable subset" reading of the "same
# allowlist pattern as routers/assistant.py's _SESSION_ID_RE" directive, not
# a silent redesign.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_session_id(session_id: str) -> str:
    """Validate ``session_id`` against :data:`SESSION_ID_RE`. Returns
    ``session_id`` unchanged on success.

    Raises:
        ValueError: ``session_id`` doesn't match the allowlist — refuse
            rather than let a crafted id reach filesystem path construction.
            Never path-traverses: this function only ever raises or returns,
            it never constructs or touches a path itself.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError(
            f"Invalid session_id {session_id!r}: must match {SESSION_ID_RE.pattern!r}"
        )
    return session_id


class TraceWriter:
    """Appends one JSON record per line to a per-session trace file.

    One :class:`TraceWriter` corresponds to one agent session's trace file,
    located at ``<work_dir>/trace_<session_id>.jsonl``. The file is opened in
    append mode on every write, so records survive process restarts: a new
    ``TraceWriter`` built with the same ``work_dir``/``session_id`` continues
    appending to the same file instead of truncating it.
    """

    def __init__(self, work_dir: Path | str, session_id: str) -> None:
        # M3.3 blocking finding: sanitize BEFORE any Path construction below
        # — see SESSION_ID_RE's module-level docstring for why this is a
        # deliberately broader allowlist than routers/assistant.py's own
        # stricter HTTP-boundary check, not a copy of it.
        validate_session_id(session_id)
        self.work_dir = Path(work_dir)
        self.session_id = session_id
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.work_dir / f"trace_{session_id}.jsonl"

    def write(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a trace record and return it.

        Raises:
            ValueError: if ``kind`` is not in :data:`ALLOWED_KINDS`.
            TypeError: if ``payload`` is not a dict, or is not JSON-serializable.
        """
        if kind not in ALLOWED_KINDS:
            raise ValueError(
                f"Unknown trace kind {kind!r}; must be one of {sorted(ALLOWED_KINDS)}"
            )
        if not isinstance(payload, dict):
            raise TypeError(f"payload must be a dict, got {type(payload).__name__}")

        record: dict[str, Any] = {
            "ts": time.time(),
            "session_id": self.session_id,
            "kind": kind,
            "payload": payload,
        }
        line = json.dumps(record)

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

        return record


def read_trace(path: Path | str) -> list[dict[str, Any]]:
    """Read a trace file back into a list of records, in write order."""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records

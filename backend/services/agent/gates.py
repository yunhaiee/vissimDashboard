"""Approval gates for write-tier orchestrator decisions (M2.3).

Three :class:`WriteGate` (see ``orchestrator.py``'s ``Protocol``)
implementations live here; ``RefuseAllWriteGate`` stays defined in
``orchestrator.py`` itself (the M1-preserving default, untouched by M2.3):

- :class:`ProposeWriteGate` -- the new M2.3 default for a live user turn.
  ``authorize()`` never returns a verdict at all: it always raises
  :class:`ApprovalRequired`, which ``orchestrator.py``'s loop catches to
  pause the turn and hand the proposed action back to the caller (the
  ``routers/assistant.py`` endpoint) as an approval card, instead of either
  silently refusing OR silently writing.
- :class:`PreapprovedWriteGate` -- used for the ``/agent/approve`` re-run
  after the user says yes. Authorizes ONLY actions whose canonical
  :func:`signature` was in the set the user actually approved; anything
  else (a re-proposed different action, or a second write attempt in the
  same re-run) is refused through the ordinary ``tool_tier_forbidden``
  path, exactly like ``RefuseAllWriteGate`` would.

:func:`signature` is the one canonicalization both the gate and
``routers/assistant.py`` (when persisting/reading ``pending_approval.json``)
must agree on, so it lives here rather than being duplicated.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.agent.toolkit import ToolSpec


def signature(tool: str, params: dict[str, Any]) -> str:
    """Canonical signature for one proposed write action.

    ``tool + "\\n" + json.dumps(params, sort_keys=True, ensure_ascii=False)``
    -- stable regardless of the params dict's original key order (so a
    planner re-emitting logically-identical params after the approve-time
    replan still matches), and ``ensure_ascii=False`` so a Korean-language
    param value (e.g. a name-based lookup) round-trips as literal text
    rather than a ``\\uXXXX`` escape that would still compare equal but is
    painful to eyeball in ``pending_approval.json``/logs.

    NOTE: two calls that are semantically the same action but differ in
    VALUE TYPE (e.g. ``{"volume": 600}`` vs ``{"volume": 600.0}``) produce
    DIFFERENT signatures, since ``json.dumps`` distinguishes int/float. In
    practice this does not bite: every write tool's pydantic params model
    (``tools_edit.py``) coerces each field to one fixed type before the
    action dict this function ever sees is built, so the same tool call
    always serializes its params the same way.
    """
    return tool + "\n" + json.dumps(params, sort_keys=True, ensure_ascii=False)


class ApprovalRequired(Exception):
    """Raised by :meth:`ProposeWriteGate.authorize` to pause the current
    turn for the user's explicit approval, instead of returning a bool.

    Carries the exact ``action`` (``{"tool", "params"}``) and the tool's
    registered :class:`~services.agent.toolkit.ToolSpec` that triggered it,
    so a caller catching this (``orchestrator.py``) can build the approval
    card without re-deriving either from scratch.
    """

    def __init__(self, action: dict[str, Any], spec: "ToolSpec") -> None:
        super().__init__(f"Approval required for tool {action.get('tool')!r}")
        self.action = action
        self.spec = spec


class ProposeWriteGate:
    """The M2.3 default write-tier gate: every write pauses for approval.

    ``authorize()`` NEVER returns -- it always raises
    :class:`ApprovalRequired`. This is deliberate: unlike
    ``RefuseAllWriteGate`` (silently refuse) or a hypothetical "always
    allow" gate (silently write), this gate's entire job is to hand control
    back to a human before anything happens.
    """

    def authorize(self, action: dict[str, Any], spec: "ToolSpec") -> bool:
        raise ApprovalRequired(action, spec)


class PreapprovedWriteGate:
    """Authorizes exactly the write action(s) matching a pre-approved
    signature set.

    Constructed from the signatures persisted at proposal time (see
    :func:`signature` and ``routers/assistant.py``'s ``pending_approval.json``
    handling). Used for the ``/agent/approve`` re-run so ONLY the action(s)
    the user actually approved can dispatch -- a planner call that proposes
    anything else (including a second, different write later in the same
    re-run) is refused through the ordinary ``tool_tier_forbidden`` path,
    same as the default ``RefuseAllWriteGate``. Membership is checked, not
    consumed: authorizing does not remove a signature from the set, so a
    planner that (redundantly) proposes the SAME approved action twice in
    one re-run would be authorized both times -- an accepted edge case,
    since nothing in this milestone's scripted flows produces it (the
    planner normally moves on to ``respond`` after a clean write, per
    ``test_agent_orchestrator_write_gate.py``'s established pattern).
    """

    def __init__(self, signatures: set[str]) -> None:
        self.signatures = set(signatures)

    def authorize(self, action: dict[str, Any], spec: "ToolSpec") -> bool:
        del spec
        return signature(action.get("tool", ""), action.get("params", {})) in self.signatures


__all__ = [
    "ApprovalRequired",
    "PreapprovedWriteGate",
    "ProposeWriteGate",
    "signature",
]

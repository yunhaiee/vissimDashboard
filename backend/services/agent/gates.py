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
  :func:`signature` was in the set the user actually approved, and
  **consumes** that signature the moment it authorizes it -- an approved
  signature is good for exactly ONE dispatch. Anything else (a re-proposed
  different action, or a SECOND attempt at the same already-dispatched
  action later in the same re-run) is refused through the ordinary
  ``tool_tier_forbidden``-shaped path, exactly like ``RefuseAllWriteGate``
  would (``orchestrator.py`` additionally recognizes the
  already-consumed case via :meth:`PreapprovedWriteGate.refusal_reason`
  and swaps in a distinct, non-misleading message -- see there).

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
    signature set -- ONCE each.

    Constructed from the signatures persisted at proposal time (see
    :func:`signature` and ``routers/assistant.py``'s ``pending_approval.json``
    handling). Used for the ``/agent/approve`` re-run so ONLY the action(s)
    the user actually approved can dispatch -- a planner call that proposes
    anything else (including a different write later in the same re-run) is
    refused through the ordinary ``tool_tier_forbidden`` path, same as the
    default ``RefuseAllWriteGate``.

    **Consume-on-success (post-incident fix).** A live-demo incident showed
    the real planner re-issuing the SAME already-approved+dispatched action
    again later in the same turn (it saw a short-run KPI warning with no
    tables and, instead of reporting that to the user, tried re-running the
    simulation) -- and the previous membership-only check authorized every
    repeat, so one user click drove six real 300s Vissim runs before the
    iteration cap (K=6) forced the turn to stop. ``authorize()`` now removes
    a signature from :attr:`signatures` the moment it grants it: an approved
    signature is good for exactly ONE dispatch. A same-signature retry later
    in the same re-run gets ``False`` -- refused through the ordinary
    tier-refusal envelope, same shape as a never-approved action, so the
    planner is told (not silently allowed) and must respond to the user
    instead of looping. :meth:`refusal_reason` lets the caller
    (``orchestrator.py``) distinguish "already used this approval" from
    "never approved at all" for a clearer message, without changing the
    ``bool``-returning ``authorize()`` contract other gates/tests rely on.

    Multiple DISTINCT approved actions in one signature set remain
    independent: consuming one never affects another's membership.
    """

    def __init__(self, signatures: set[str]) -> None:
        self.signatures = set(signatures)
        # Every signature this instance has ever granted -- superset of the
        # (now-removed-from `signatures`) ones already dispatched this
        # re-run. Powers `refusal_reason` below; never consulted by
        # `authorize` itself.
        self._consumed: set[str] = set()

    def authorize(self, action: dict[str, Any], spec: "ToolSpec") -> bool:
        del spec
        sig = signature(action.get("tool", ""), action.get("params", {}))
        if sig not in self.signatures:
            return False
        self.signatures.discard(sig)
        self._consumed.add(sig)
        return True

    def refusal_reason(self, action: dict[str, Any], spec: "ToolSpec") -> str | None:
        """Best-effort refusal detail for a ``False`` verdict from
        :meth:`authorize` on this same ``action``: ``"approval_consumed"``
        when ``action``'s signature WAS approved and already granted once by
        THIS instance (a repeat within the same re-run), ``None`` when it
        was never approved at all (an ordinary tier refusal). Optional
        protocol extension -- ``orchestrator.py`` probes for this method via
        ``getattr`` and falls back to the generic refusal message when a
        gate (``RefuseAllWriteGate``, ``ProposeWriteGate``) doesn't define
        it.
        """
        del spec
        sig = signature(action.get("tool", ""), action.get("params", {}))
        return "approval_consumed" if sig in self._consumed else None


__all__ = [
    "ApprovalRequired",
    "PreapprovedWriteGate",
    "ProposeWriteGate",
    "signature",
]

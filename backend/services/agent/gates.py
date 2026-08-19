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

**#44-b addition: ``write_provenance``.** Both gates can carry an optional
``grant`` (``services.agent.car_following_grant.CarFollowingGrant``) and
answer ``write_provenance(action)`` with what the APPROVAL MACHINERY knows
about that action -- which grant covers it, and whether a human's click has
been consumed for it yet. It is an optional protocol extension in the same
shape as :meth:`PreapprovedWriteGate.refusal_reason`: ``orchestrator.py``
probes for it via ``getattr`` and gates that do not define it (the default
``RefuseAllWriteGate``, and any caller-supplied gate) simply produce no
provenance at all, which is the pre-#44-b behaviour everywhere.

Why the gate is the carrier: the constraint engine has to be able to tell
"this write is the one a human approved off a cited Table 5-6 proposal" from
"this write merely claims to be", and the planner controls ONLY
``{"tool", "params"}``. The gate is machinery -- constructed by the router,
never by the model -- so provenance riding on it is a channel no planner
payload can reach. See ``car_following_grant``'s own module docstring for
the full argument, including why a self-reported flag was rejected (this
repo has had to un-ship that exact design twice).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from services.agent.car_following_grant import CarFollowingGrant
    from services.agent.toolkit import ToolSpec


# ---------------------------------------------------------------------------
# Consent states (#44-b) -- what the GATE says about the human's click.
#
# Defined here rather than in ``car_following_grant`` because the gate is what
# reports them and ``car_following_grant`` imports this module (for
# :func:`signature`); putting them the other way round would be a cycle. The
# grant module re-exports both under the same names, so consumers can read them
# from whichever side they already depend on.
# ---------------------------------------------------------------------------

#: The turn is PAUSED for a human. Nothing dispatches in this state.
AWAITING_APPROVAL = "AWAITING_APPROVAL"

#: A human clicked and :meth:`PreapprovedWriteGate.authorize` matched and
#: CONSUMED this exact action's approved signature. The only state a write
#: actually dispatches in.
CONSUMED_APPROVAL = "CONSUMED_APPROVAL"


def _write_provenance(
    grant: "CarFollowingGrant | None", action: dict[str, Any], consent: str
) -> dict[str, Any] | None:
    """The gate-side half of the #44-b provenance protocol, shared by both gates.

    ``None`` -- the answer for every gate that carries no grant, and for every
    action a carried grant does not name -- is what keeps the exception a
    per-action match rather than a mode the engine gets into. ``kind`` is read
    off the grant itself so this module never has to know what kinds exist.
    """
    if grant is None or not grant.covers(action):
        return None
    return {"kind": grant.kind, "grant": grant, "consent": consent}


def _resolve_grant(gate: Any, action: dict[str, Any]) -> "CarFollowingGrant | None":
    """The gate's grant: eager if it has one, else its late binder's answer (#47).

    Both gates are constructed by ``routers/assistant.py`` before the thing the
    grant is derived from exists -- the proposing turn's set is minted mid-turn by
    the tool that proposes it, and the approving turn's shadow model is only
    parsed once the working copy has been re-synced on the turn thread. A gate
    carrying NEITHER a grant nor a provider (the default everywhere else, and
    every test and caller that predates #47) resolves to ``None`` here, which is
    the pre-#44-b behaviour.

    A provider that raises means NO grant, never a failed turn: the fallback is
    the ordinary shared-object refusal, which is a named message a human can read.
    """
    grant = getattr(gate, "grant", None)
    if grant is not None:
        return grant
    provider = getattr(gate, "grant_provider", None)
    if provider is None:
        return None
    try:
        return provider(action)
    except Exception:  # noqa: BLE001
        return None


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

    ``grant`` (#44-b, optional) is the Table 5-6 car-following grant the
    proposal this turn is acting on minted. It exists here for ONE reason:
    the approval card's own preflight runs before the card is shown, and a
    refuse-tier finding there means the human is never asked at all
    (``orchestrator.py``'s ``ApprovalRequired`` branch: ``if
    preflight_report.blocking`` returns a refusal instead of a card). That
    is precisely the dead end DECISIONS 2026-08-18 recorded and 2026-08-19
    decided to end, so the grant has to be visible at card-build time too.
    Nothing can be written through this gate while it is: ``authorize``
    never returns a verdict, so the consent state stays
    ``AWAITING_APPROVAL`` and the only effect is that the card gets built --
    with the constraint engine's warn-tier finding naming the radius on it,
    which is the sentence the decision's safety argument rests on.
    """

    #: #47: a LATE binder for the same grant, ``action -> CarFollowingGrant |
    #: None``. This gate is constructed by ``routers/assistant.py`` before the
    #: planner has run, so on the real product path the set the grant would come
    #: from does not exist yet -- it is built mid-turn, by the tool that proposes
    #: it. ``grant`` (eager) stays the primary field and wins whenever it is set;
    #: the provider is consulted only when it is ``None``, and a gate given
    #: neither behaves exactly as it did before #44-b. Machinery either way: like
    #: ``grant``, it is supplied by the router and unreachable from the planner,
    #: which only ever emits ``{"tool", "params"}``.
    def __init__(
        self,
        grant: "CarFollowingGrant | None" = None,
        grant_provider: "Callable[[dict[str, Any]], CarFollowingGrant | None] | None" = None,
    ) -> None:
        self.grant = grant
        self.grant_provider = grant_provider

    def authorize(self, action: dict[str, Any], spec: "ToolSpec") -> bool:
        raise ApprovalRequired(action, spec)

    def write_provenance(self, action: dict[str, Any]) -> dict[str, Any] | None:
        """See :meth:`PreapprovedWriteGate.write_provenance`. Always
        ``AWAITING_APPROVAL`` -- this gate cannot dispatch anything."""
        return _write_provenance(_resolve_grant(self, action), action, AWAITING_APPROVAL)


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

    def __init__(
        self,
        signatures: set[str],
        grant: "CarFollowingGrant | None" = None,
        grant_provider: "Callable[[dict[str, Any]], CarFollowingGrant | None] | None" = None,
    ) -> None:
        self.signatures = set(signatures)
        #: #44-b: the Table 5-6 grant these approved signatures came from, or
        #: ``None`` (every other unit's approvals, unchanged).
        self.grant = grant
        #: #47: the same grant, resolved late. ``/agent/approve`` builds this
        #: gate in the request handler, where the working copy has not been
        #: re-synced yet and ``artifact_manager.shadow_model`` is still ``None``
        #: — so the radius the grant is re-derived against would be read from
        #: nothing. Consulted only when ``grant`` is ``None``; a gate given
        #: neither is byte-identical to its pre-#47 self.
        self.grant_provider = grant_provider
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

    def write_provenance(self, action: dict[str, Any]) -> dict[str, Any] | None:
        """What the approval machinery knows about ``action`` (#44-b).

        ``None`` unless a carried grant NAMES this exact write AND this gate
        actually holds the human's approval for it -- either still pending
        (the signature is in :attr:`signatures`, so :data:`AWAITING_APPROVAL`)
        or already spent by this instance's own one-shot :meth:`authorize`
        (:data:`CONSUMED_APPROVAL`, the state a dispatch follows). A write this
        gate was never given a signature for gets ``None`` even when the grant
        covers it: a grant says which writes a proposal produced, an approval
        says a human agreed to them, and the exception needs both.

        The one-shot property needs no new enforcement here: a repeat of an
        already-dispatched write is refused by ``authorize`` before the
        orchestrator ever reaches preflight, so the constraint engine is never
        asked to excuse a reused approval.
        """
        sig = signature(action.get("tool", ""), action.get("params", {}))
        if sig in self._consumed:
            consent = CONSUMED_APPROVAL
        elif sig in self.signatures:
            consent = AWAITING_APPROVAL
        else:
            return None
        return _write_provenance(_resolve_grant(self, action), action, consent)


__all__ = [
    "AWAITING_APPROVAL",
    "ApprovalRequired",
    "CONSUMED_APPROVAL",
    "PreapprovedWriteGate",
    "ProposeWriteGate",
    "signature",
]

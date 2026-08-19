"""Unit #47 — the PRODUCT path's carrier for #44-b's Table 5-6 grant.

What was missing
----------------
#44 builds the three-write Table 5-6 set, #44-b mints a :class:`~services.agent.
car_following_grant.CarFollowingGrant` from it and the constraint engine already
honours that grant (``constraints._check_car_following_shared_edit_consented``).
But ``routers/assistant.py`` built both write gates with ``grant=None``, so on the
real product path — an assistant turn that proposes, an approval card, a click —
the grant never existed and the narrow exception was unreachable. Only
``backend/tools/calibration_loop_live_check.py`` (a harness that constructs the
gates by hand) could ever reach it.

This module is the missing carrier. It is deliberately NOT a persistence layer.

Why nothing is persisted
------------------------
The obvious wiring is to serialize the grant into ``pending_approval.json`` beside
the signatures it already holds, and rebuild it at ``/agent/approve``. That would
hand anyone who can write one file in the session work dir the ability to mint a
network-wide driving-behaviour edit that the guardrail then stands down for —
which is the whole of what #44-b exists to prevent. ``car_following_grant``
already refuses a serialized look-alike (``provenance_from`` requires an actual
:class:`CarFollowingGrant` INSTANCE, not a dict that quacks like one); persisting
one here and rehydrating it would route around that check from the other side.

So:

* **Grants live only in this process's memory.** Nothing about a grant, and
  nothing a grant can be rebuilt from, is ever written to disk by this module.
* **``pending_approval.json`` carries a REFERENCE, never a payload.** The router
  writes ``car_following_set_id``: an opaque string naming a set THIS PROCESS
  minted earlier in THIS session. A hand-edited file can name any string it
  likes; :func:`recall` answers ``None`` for every set the process did not build
  itself, the gate is then built with no grant at all, and
  ``driving_behavior_shared_object_edit`` refuses exactly as it did before #44-b.
  There is no field in that file — none — that widens what a grant covers.
* **The grant is RE-MINTED at approve time, never deserialized.**
  :func:`mint` calls ``saturation_flow.grant_from_proposals`` again, on the live
  ``FixProposal`` objects the proposing turn produced, so every structural check
  that function performs (complete set of three, all ``PROPOSAL`` outcome, one
  ``set_id``, one behaviour, exactly ax/bx_add/bx_mult, values agreeing with
  ``expected_after_apply``, an accepted measurement) runs again at the moment the
  human's click is being honoured.
* **The consent radius is re-derived from the LIVE shadow model at mint time.**
  ``car_following_grant.evaluate`` already re-derives it once more at preflight;
  doing it here as well means a set whose blast radius moved between proposal and
  approval never even gets a grant attached, and the refusal names which side
  moved. Belt and braces on purpose: this is the one number the decision's whole
  safety argument rests on (DECISIONS.md 2026-08-19).

A process restart between the proposal and the click therefore loses the set and
the approved write is refused by the ordinary shared-object guardrail. That is the
correct failure direction — the fallback is the pre-#44-b behaviour, which is a
named refusal a human can read, never a silent widening.

Session scoping
---------------
Sets are keyed by ``session_id`` so one session's proposal can never authorize
another session's write, and :func:`set_active_session` publishes the current
turn's id the same way ``tools_network.set_active_inpx`` publishes its working
copy — set by ``routers/assistant.py`` around the turn, cleared in its ``finally``.
Tools receive only their pydantic params, so a module-global is how a tool learns
which turn it is running in; this one inherits the same documented
single-session-at-a-time caveat ``tools_network`` records, and adds nothing to it
(the STORE is per-session regardless of what the global says — an unset global
simply means nothing is remembered).

Bounded by construction: at most :data:`MAX_SETS_PER_SESSION` sets per session and
:data:`MAX_SESSIONS` sessions, both evicted oldest-first, so a long-lived server
cannot accumulate proposal sets without limit.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from typing import Any, Mapping, Sequence

from services.agent import car_following_grant, fixes, saturation_flow
from services.agent.car_following_grant import CarFollowingGrant
from services.agent.gates import signature

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

#: Proposal sets kept per session. A turn proposes one set; a modeller who
#: re-measures and re-proposes within one session should not lose the earlier
#: ones while an approval card for them is still open, but there is no reason to
#: keep an unbounded history either.
MAX_SETS_PER_SESSION = 8

#: Sessions kept. Agent sessions are long-lived on disk but a grant is only ever
#: useful between a proposal and the click that follows it.
MAX_SESSIONS = 32

# ---------------------------------------------------------------------------
# Mint outcomes -- why a grant was or was not issued, by name
# ---------------------------------------------------------------------------

#: A grant was minted and its radius still matches the one consented to.
GRANTED = "GRANTED"

#: No session context, so nothing could be looked up.
NO_SESSION = "NO_SESSION"

#: This session never minted a set by that id. The answer for every forged or
#: stale ``car_following_set_id`` in ``pending_approval.json``, and for every set
#: lost to a process restart.
UNKNOWN_SET = "UNKNOWN_SET"

#: ``grant_from_proposals`` refused to rebuild the grant from the remembered
#: proposals. Should be unreachable (the same call succeeded at :func:`remember`
#: time, on the same objects), so it is reported rather than swallowed.
REMINT_FAILED = "REMINT_FAILED"

#: The behaviour's reference graph moved between proposal and approval. Consent
#: was to a NAMED radius, so a moved radius is not the thing that was consented to.
RADIUS_DRIFTED = "RADIUS_DRIFTED"

#: The radius could not be named in full on either side (a truncated or absent
#: consumer list). The exception's premise is a sentence a human read; where that
#: sentence cannot be written truthfully there is nothing to have read.
RADIUS_NOT_FULLY_NAMED = "RADIUS_NOT_FULLY_NAMED"

#: The target behaviour is not in the live shadow model at all.
BEHAVIOR_NOT_IN_MODEL = "BEHAVIOR_NOT_IN_MODEL"

MINT_REASONS = (
    GRANTED,
    NO_SESSION,
    UNKNOWN_SET,
    REMINT_FAILED,
    RADIUS_DRIFTED,
    RADIUS_NOT_FULLY_NAMED,
    BEHAVIOR_NOT_IN_MODEL,
)

# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()

#: ``session_id -> {set_id -> (FixProposal, ...)}``, both LRU-bounded. Live
#: objects, never their ``to_dict()`` forms: :func:`mint` re-runs the real
#: minting path over them, and a dict could not survive
#: ``grant_from_proposals``'s ``is_proposal``/``verb`` checks anyway.
#:
#: ISLAND, not a shared reference (refuter, #47). Everything the grant is
#: derived from is re-read off these objects at mint time, so anything else
#: holding one of them holds a lever on what the grant covers. Measured before
#: this was closed: ``remember(props)`` then ``props[0].params["value"] = 99.0``
#: made the re-minted grant cover ``ax=99.0`` and stop covering the ``ax=2.5``
#: the human was shown. No product path mutated them -- but ``recall`` hands the
#: same objects to ``_car_following_card_payload``, whose payload shares their
#: nested ``evidence`` dicts (``FixProposal.to_dict`` copies one level), and
#: that payload goes into a response body. So the store copies on the way in and
#: on the way out via :func:`_snapshot`, and no reference into it exists at all.
_SETS: "OrderedDict[str, OrderedDict[str, tuple[fixes.FixProposal, ...]]]" = OrderedDict()


def _snapshot(actions: Sequence[fixes.FixProposal]) -> tuple[fixes.FixProposal, ...]:
    """A private copy of one proposal set. See :data:`_SETS`.

    ``FixProposal`` is a plain dataclass of strings, dicts, lists and one
    optional nested ``FixProposal`` -- no handles, no registry, no I/O objects --
    so a deep copy is both cheap (three of them) and total.
    """
    return tuple(copy.deepcopy(list(actions)))

#: The turn currently running on this process, published by
#: ``routers/assistant.py`` exactly like ``tools_network``'s active inpx.
_active_session_id: str | None = None


def set_active_session(session_id: str | None) -> None:
    """Publish (or clear) the session id the current agent turn belongs to."""
    global _active_session_id
    _active_session_id = str(session_id) if session_id else None


def active_session() -> str | None:
    """The current turn's session id, or ``None`` outside a turn."""
    return _active_session_id


def _resolve_session(session_id: str | None) -> str | None:
    resolved = session_id if session_id else _active_session_id
    return str(resolved) if resolved else None


# ---------------------------------------------------------------------------
# Remember / recall
# ---------------------------------------------------------------------------


def remember(
    proposals: Sequence[fixes.FixProposal], *, session_id: str | None = None
) -> str | None:
    """Keep a complete Table 5-6 proposal set for this session; return its ``set_id``.

    Returns ``None`` — remembering nothing — whenever the set is not one
    ``grant_from_proposals`` accepts. The validation is performed by ACTUALLY
    MINTING a grant and discarding it, rather than by re-checking the same
    conditions here: a second implementation of "is this a real set" would be a
    second thing to keep in sync with the first, and this repository's own
    history is that the copy drifts. The grant built here is thrown away on
    purpose — the one that reaches a gate is always minted fresh in :func:`mint`,
    against the shadow model as it stands at that moment.
    """
    session = _resolve_session(session_id)
    if session is None:
        return None
    actions = tuple(proposals or ())
    try:
        grant = saturation_flow.grant_from_proposals(actions)
    except (ValueError, TypeError, AttributeError):
        return None

    with _LOCK:
        sets = _SETS.get(session)
        if sets is None:
            sets = OrderedDict()
            _SETS[session] = sets
        _SETS.move_to_end(session)
        sets[grant.set_id] = _snapshot(actions)
        sets.move_to_end(grant.set_id)
        while len(sets) > MAX_SETS_PER_SESSION:
            sets.popitem(last=False)
        while len(_SETS) > MAX_SESSIONS:
            _SETS.popitem(last=False)
    return grant.set_id


def recall(
    set_id: str | None, *, session_id: str | None = None
) -> tuple[fixes.FixProposal, ...] | None:
    """The remembered proposal set, or ``None``.

    ``None`` for every id this process did not mint in this session — which is
    what makes a hand-edited ``car_following_set_id`` worthless.

    A COPY, never the stored objects (refuter, #47): the caller's use for these
    is to build a response payload out of them, and ``FixProposal.to_dict``
    copies only one level, so handing out the originals would put the store's
    own ``evidence`` dicts — ``expected_after_apply``, ``consent_radius`` — on
    the wire as live references. See :data:`_SETS`.
    """
    session = _resolve_session(session_id)
    if session is None or not set_id:
        return None
    with _LOCK:
        sets = _SETS.get(session)
        if not sets:
            return None
        stored = sets.get(str(set_id))
    return None if stored is None else _snapshot(stored)


def set_id_covering(
    action: Mapping[str, Any] | None, *, session_id: str | None = None
) -> str | None:
    """Which remembered set names THIS exact write, if any.

    Matched on the canonical ``gates.signature`` of the action against the
    signatures the set's own proposals produce — the same comparison the grant
    itself performs, so "the router thinks this action belongs to the set" and
    "the grant covers this action" can never disagree.
    """
    session = _resolve_session(session_id)
    payload = action if isinstance(action, Mapping) else {}
    if session is None or payload.get("tool") != car_following_grant.GRANT_TOOL:
        return None
    params = payload.get("params")
    sig = signature(car_following_grant.GRANT_TOOL, dict(params) if isinstance(params, Mapping) else {})
    with _LOCK:
        sets = _SETS.get(session)
        if not sets:
            return None
        # Newest first: a re-measured set proposed later in the same session is
        # the one the open approval card belongs to.
        for set_id, actions in reversed(list(sets.items())):
            if any(
                signature(car_following_grant.GRANT_TOOL, dict(a.params or {})) == sig
                for a in actions
                if a.is_proposal and a.verb == car_following_grant.GRANT_TOOL
            ):
                return set_id
    return None


def forget(set_id: str | None, *, session_id: str | None = None) -> None:
    """Drop ONE remembered set. What a declined approval does to its grant."""
    session = _resolve_session(session_id)
    if session is None or not set_id:
        return
    with _LOCK:
        sets = _SETS.get(session)
        if sets is not None:
            sets.pop(str(set_id), None)


def forget_session(session_id: str | None) -> None:
    """Drop everything remembered for one session."""
    session = _resolve_session(session_id)
    if session is None:
        return
    with _LOCK:
        _SETS.pop(session, None)


def reset() -> None:
    """Drop every remembered set. For tests; production never needs it."""
    with _LOCK:
        _SETS.clear()
    set_active_session(None)


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------


def behavior_in_model(
    behavior_no: str | None, shadow_model: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """The live shadow model's own entry for one driving behaviour, or ``None``.

    Same resolution ``constraints._driving_behavior_target`` performs (match on
    the string form of ``no``), so the radius this module re-derives and the one
    the constraint engine re-derives are read off the same object.
    """
    model = shadow_model if isinstance(shadow_model, Mapping) else {}
    behaviors = model.get("driving_behaviors") or ()
    wanted = str(behavior_no or "")
    if not wanted:
        return None
    for entry in behaviors:
        if isinstance(entry, Mapping) and str(entry.get("no", "")) == wanted:
            return dict(entry)
    return None


def mint(
    set_id: str | None,
    shadow_model: Mapping[str, Any] | None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Re-mint one remembered set's grant against the CURRENT shadow model.

    Returns ``{"grant", "reason", "set_id", "consented_radius", "current_radius"}``.
    ``grant`` is ``None`` for every reason other than :data:`GRANTED`, and a
    ``None`` grant is not an error condition for the caller — it simply means the
    gate is built without one and the ordinary guardrail applies, which is the
    behaviour every path had before #44-b.

    Never raises. A caller building a write gate must not be able to fail because
    a proposal set went missing.
    """
    result: dict[str, Any] = {
        "grant": None,
        "reason": UNKNOWN_SET,
        "set_id": str(set_id) if set_id else None,
        "consented_radius": None,
        "current_radius": None,
    }
    session = _resolve_session(session_id)
    if session is None:
        return {**result, "reason": NO_SESSION}

    actions = recall(set_id, session_id=session)
    if not actions:
        return result

    try:
        grant = saturation_flow.grant_from_proposals(actions)
    except (ValueError, TypeError, AttributeError):
        return {**result, "reason": REMINT_FAILED}

    consented = dict(grant.consent_radius)
    result["consented_radius"] = consented

    behavior = behavior_in_model(grant.behavior_no, shadow_model)
    if behavior is None:
        return {**result, "reason": BEHAVIOR_NOT_IN_MODEL}

    current = car_following_grant.consent_radius(behavior)
    result["current_radius"] = current
    if current != consented:
        return {**result, "reason": RADIUS_DRIFTED}
    if not (consented.get("complete") and current.get("complete")):
        return {**result, "reason": RADIUS_NOT_FULLY_NAMED}

    return {**result, "grant": grant, "reason": GRANTED}


def grant_provider(
    shadow_model_fn: Any, *, session_id: str | None = None, set_id: str | None = None
) -> Any:
    """A gate-shaped late binder: ``action -> CarFollowingGrant | None``.

    Both write gates are constructed in a request handler, BEFORE the thing they
    need exists: the proposing turn's set is minted mid-turn by the tool that
    proposes it, and the approving turn's shadow model is only parsed once
    ``_ensure_working_copy`` has run on the turn thread. So the grant is resolved
    at the moment the gate is asked for provenance rather than at construction.

    ``shadow_model_fn`` is a zero-arg callable (normally
    ``lambda: artifact_manager.shadow_model``) so the radius is re-derived against
    the working copy as it stands at that moment, including any edit made earlier
    in the same turn.

    ``set_id`` PINS the provider to one remembered set — what ``/agent/approve``
    passes, reading the reference out of ``pending_approval.json``. Without it the
    provider asks which remembered set covers the action, which is what the
    proposing turn needs (it has no reference yet). Either way the answer is a
    grant re-minted here and now, and ``gates._write_provenance`` still requires
    it to ``cover()`` the action before any provenance is produced.
    """

    def _provide(action: Mapping[str, Any] | None) -> CarFollowingGrant | None:
        target = str(set_id) if set_id else set_id_covering(action, session_id=session_id)
        if not target:
            return None
        try:
            shadow = shadow_model_fn() if callable(shadow_model_fn) else shadow_model_fn
        except Exception:  # noqa: BLE001 - a model read failure means no grant, never a raise
            return None
        return mint(target, shadow, session_id=session_id).get("grant")

    return _provide


__all__ = [
    "BEHAVIOR_NOT_IN_MODEL",
    "GRANTED",
    "MAX_SESSIONS",
    "MAX_SETS_PER_SESSION",
    "MINT_REASONS",
    "NO_SESSION",
    "RADIUS_DRIFTED",
    "RADIUS_NOT_FULLY_NAMED",
    "REMINT_FAILED",
    "UNKNOWN_SET",
    "active_session",
    "behavior_in_model",
    "forget",
    "forget_session",
    "grant_provider",
    "mint",
    "recall",
    "remember",
    "reset",
    "set_active_session",
    "set_id_covering",
]

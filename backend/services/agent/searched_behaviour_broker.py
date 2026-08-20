"""Unit #51-b — the PRODUCT path's carrier for #51's searched-parameter grant.

The same carrier #47 built for Table 5-6, for the other grant species
-----------------------------------------------------------------------
``car_following_broker`` exists because a grant that only a hand-built harness
can construct is not reachable on the product path. The searched-parameter
exception has the same gap and the same answer, so this module is deliberately
#47's shape, function for function — ``remember`` / ``recall`` / ``mint`` /
``grant_provider`` / ``set_active_session``. What differs is what a record IS
(a finished search rather than a proposal set) and which minting path it goes
through (``parameter_search.grant_from_search``).

WHERE THE SEARCH STATE LIVES — measured, 2026-08-20
---------------------------------------------------
DECISIONS.md 2026-08-20 requires a joint with no forgery path, and names the
failure explicitly: 원장 파일을 손으로 고쳐 다른 값의 grant를 얻으면 실패입니다.
So the first question was where a search's state actually lives today. Measured
by reading every caller: ``services/calibration_rail.py`` calls
``evaluate_preconditions`` and ``trigger_card`` and nothing else, and NOTHING in
this repository runs a search, persists a ``SearchState``, or reads one back —
the live runner is explicitly a later unit (``parameter_search.
runner_requirement()['wired'] is False``). The state therefore lives nowhere at
all yet, which means the joint gets to be designed rather than retrofitted, and
it is designed the only way that closes the file vector:

* **A finished search lives only in this process's memory.** Nothing about a
  record, and nothing a grant can be rebuilt from, is ever written to disk by
  this module. The ledger is not an INPUT to minting on any path: ``mint`` reads
  :data:`_SEARCHES` and only :data:`_SEARCHES`, and :func:`remember` is the only
  thing that writes to it.
* **``pending_approval.json`` may carry a REFERENCE, never a payload.** An
  opaque ``search_id`` naming a search THIS PROCESS finished in THIS session. A
  hand-edited file can name any string it likes; :func:`recall` answers ``None``
  for every id the process did not remember itself, the gate is then built with
  no grant at all, and ``driving_behavior_shared_object_edit`` refuses exactly as
  it did before #51-b.
* **The grant is RE-MINTED at approve time, never deserialized.**
  :func:`mint` calls ``parameter_search.grant_from_search`` again on the
  remembered payloads, so every structural check that function performs — a
  COMPLETE status, a re-derived split, a holdout acceptance RE-DERIVED FROM THE
  LEDGER at an unseen seed, an in-band winner that was really run, writes that
  are exactly the parameters that moved — runs again at the moment the human's
  click is being honoured.
* **The consent radius is re-derived from the LIVE shadow model at mint time**,
  and ``searched_behaviour_grant.evaluate`` re-derives it once more at preflight.
  Belt and braces on purpose: this is the number both decisions' safety argument
  rests on.

What that leaves, stated rather than papered over: a hand-edited ledger FILE
cannot produce a grant because no file is ever read. If a later unit adds
stop-and-resume from disk (``parameter_search.resume`` accepts a state dict), a
resumed search must still be re-run and re-accepted IN THIS PROCESS before
:func:`remember` will keep it, and ``grant_from_search``'s ledger-side
re-derivations are then what stand between a tampered state and a grant. Those
re-derivations are real — they refuse a forged acceptance code, a chosen split,
a leaked holdout node, an out-of-band winner and a winner nothing ever ran — but
the property "no file is an input" is the one this unit ships, and a future
disk-resume unit must say which of the two it is relying on.

A process restart between the proposal and the click therefore loses the search
and the approved write is refused by the ordinary shared-object guardrail. That
is the correct failure direction, and it is the same one #47 recorded.

Session scoping and bounds are #47's, unchanged in kind: records are keyed by
``session_id`` so one session's search can never authorize another session's
write, and both dimensions are LRU-bounded so a long-lived server cannot
accumulate them.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from typing import Any, Mapping

from services.agent import parameter_search, searched_behaviour_grant
from services.agent.car_following_broker import behavior_in_model
from services.agent.gates import signature
from services.agent.searched_behaviour_grant import SearchedBehaviourGrant

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

#: Finished searches kept per session. A search costs an hour of wall clock by
#: default, so a session holding more than a couple of finished ones at once is
#: already unusual; the bound exists so the store cannot grow without limit, not
#: because four is a meaningful number.
MAX_SEARCHES_PER_SESSION = 4

#: Sessions kept. Same reasoning as #47's: a grant is only ever useful between a
#: proposal and the click that follows it.
MAX_SESSIONS = 32

# ---------------------------------------------------------------------------
# Mint outcomes -- why a grant was or was not issued, by name
# ---------------------------------------------------------------------------

#: A grant was minted and its radius still matches the one consented to.
GRANTED = "GRANTED"

#: No session context, so nothing could be looked up.
NO_SESSION = "NO_SESSION"

#: This session never remembered a search by that id. The answer for every
#: forged or stale ``search_id`` in ``pending_approval.json``, and for every
#: search lost to a process restart.
UNKNOWN_SEARCH = "UNKNOWN_SEARCH"

#: ``grant_from_search`` refused to rebuild the grant from the remembered
#: payloads. Should be unreachable (the same call succeeded at :func:`remember`
#: time, on copies of the same objects), so it is reported rather than swallowed.
REMINT_FAILED = "REMINT_FAILED"

#: The behaviour's reference graph moved between proposal and approval. Consent
#: was to a NAMED radius, so a moved radius is not the thing consented to.
RADIUS_DRIFTED = "RADIUS_DRIFTED"

#: The radius could not be named in full on either side. The exception's premise
#: is a sentence a human read; where that sentence cannot be written truthfully
#: there is nothing to have read.
RADIUS_NOT_FULLY_NAMED = "RADIUS_NOT_FULLY_NAMED"

#: The target behaviour is not in the live shadow model at all.
BEHAVIOR_NOT_IN_MODEL = "BEHAVIOR_NOT_IN_MODEL"

MINT_REASONS = (
    GRANTED,
    NO_SESSION,
    UNKNOWN_SEARCH,
    REMINT_FAILED,
    RADIUS_DRIFTED,
    RADIUS_NOT_FULLY_NAMED,
    BEHAVIOR_NOT_IN_MODEL,
)

# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()

#: ``session_id -> {search_id -> record}``, both LRU-bounded. A record is
#: ``{"result", "acceptance", "proposal", "preconditions"}`` — the four payloads
#: ``parameter_search.grant_from_search`` cross-checks against each other.
#:
#: ISLAND, not a shared reference (#47's refuter, whose exploit applies verbatim
#: here). Everything the grant covers is re-read off these payloads at mint time,
#: so anything else holding one of them holds a lever on what the grant covers —
#: and unlike #47's ``FixProposal`` objects these are plain dicts, which callers
#: pass around and mutate far more casually. So the store deep-copies on the way
#: IN and on the way OUT (:func:`_snapshot`), and no reference into it exists at
#: all.
_SEARCHES: "OrderedDict[str, OrderedDict[str, dict[str, Any]]]" = OrderedDict()


def _snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    """A private copy of one remembered record. See :data:`_SEARCHES`.

    The payloads are JSON-shaped throughout (``result`` is
    ``ParameterSearch.result()``, ``proposal`` is ``parameter_search.propose()``,
    and both are built to be serialisable), so a deep copy is total.
    """
    return copy.deepcopy(dict(record))


#: The turn currently running on this process, published by the router exactly
#: like ``car_following_broker``'s and ``tools_network``'s active inpx.
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
    *,
    result: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    proposal: Mapping[str, Any],
    preconditions: Mapping[str, Any],
    session_id: str | None = None,
) -> str | None:
    """Keep one finished, accepted search for this session; return its ``search_id``.

    Returns ``None`` — remembering nothing — whenever the payloads are not ones
    ``grant_from_search`` accepts. The validation is performed by ACTUALLY
    MINTING a grant and discarding it, rather than by re-checking the same
    conditions here: a second implementation of "is this a real finished search"
    would be a second thing to keep in sync with the first, and this repository's
    own history is that the copy drifts. The grant built here is thrown away on
    purpose — the one that reaches a gate is always minted fresh in :func:`mint`,
    against the shadow model as it stands at that moment.
    """
    session = _resolve_session(session_id)
    if session is None:
        return None
    record = {
        "result": dict(result or {}),
        "acceptance": dict(acceptance or {}),
        "proposal": dict(proposal or {}),
        "preconditions": dict(preconditions or {}),
    }
    try:
        grant = parameter_search.grant_from_search(**record)
    except (ValueError, TypeError, AttributeError, KeyError):
        return None

    with _LOCK:
        searches = _SEARCHES.get(session)
        if searches is None:
            searches = OrderedDict()
            _SEARCHES[session] = searches
        _SEARCHES.move_to_end(session)
        searches[grant.search_id] = _snapshot(record)
        searches.move_to_end(grant.search_id)
        while len(searches) > MAX_SEARCHES_PER_SESSION:
            searches.popitem(last=False)
        while len(_SEARCHES) > MAX_SESSIONS:
            _SEARCHES.popitem(last=False)
    return grant.search_id


def recall(
    search_id: str | None, *, session_id: str | None = None
) -> dict[str, Any] | None:
    """The remembered record, or ``None``.

    ``None`` for every id this process did not remember in this session — which
    is what makes a hand-edited ``search_id`` in ``pending_approval.json``
    worthless, and what makes a hand-edited ledger FILE worthless too: no file is
    ever read here, so there is nothing for one to be.

    A COPY, never the stored payloads. See :data:`_SEARCHES`.
    """
    session = _resolve_session(session_id)
    if session is None or not search_id:
        return None
    with _LOCK:
        searches = _SEARCHES.get(session)
        if not searches:
            return None
        stored = searches.get(str(search_id))
    return None if stored is None else _snapshot(stored)


def search_id_covering(
    action: Mapping[str, Any] | None, *, session_id: str | None = None
) -> str | None:
    """Which remembered search names THIS exact write, if any.

    Matched on the canonical ``gates.signature`` of the action against the
    signatures the remembered proposal's own actions produce — the same
    comparison the grant itself performs, so "the router thinks this action
    belongs to the search" and "the grant covers this action" can never disagree.
    """
    session = _resolve_session(session_id)
    payload = action if isinstance(action, Mapping) else {}
    if session is None or payload.get("tool") != searched_behaviour_grant.GRANT_TOOL:
        return None
    params = payload.get("params")
    sig = signature(
        searched_behaviour_grant.GRANT_TOOL,
        dict(params) if isinstance(params, Mapping) else {},
    )
    with _LOCK:
        searches = _SEARCHES.get(session)
        if not searches:
            return None
        # Newest first: a search re-run later in the same session is the one the
        # open approval card belongs to.
        for search_id, record in reversed(list(searches.items())):
            actions = (record.get("proposal") or {}).get("actions") or ()
            for item in actions:
                if not isinstance(item, Mapping):
                    continue
                if item.get("tool") != searched_behaviour_grant.GRANT_TOOL:
                    continue
                params = item.get("params")
                candidate = signature(
                    searched_behaviour_grant.GRANT_TOOL,
                    dict(params) if isinstance(params, Mapping) else {},
                )
                if candidate == sig:
                    return search_id
    return None


def forget(search_id: str | None, *, session_id: str | None = None) -> None:
    """Drop ONE remembered search. What a declined approval does to its grant."""
    session = _resolve_session(session_id)
    if session is None or not search_id:
        return
    with _LOCK:
        searches = _SEARCHES.get(session)
        if searches is not None:
            searches.pop(str(search_id), None)


def forget_session(session_id: str | None) -> None:
    """Drop everything remembered for one session."""
    session = _resolve_session(session_id)
    if session is None:
        return
    with _LOCK:
        _SEARCHES.pop(session, None)


def reset() -> None:
    """Drop every remembered search. For tests; production never needs it."""
    with _LOCK:
        _SEARCHES.clear()
    set_active_session(None)


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------


def mint(
    search_id: str | None,
    shadow_model: Mapping[str, Any] | None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Re-mint one remembered search's grant against the CURRENT shadow model.

    Returns ``{"grant", "reason", "search_id", "consented_radius", "current_radius"}``.
    ``grant`` is ``None`` for every reason other than :data:`GRANTED`, and a
    ``None`` grant is not an error condition for the caller — it simply means the
    gate is built without one and the ordinary guardrail applies, which is the
    behaviour every path had before #51-b.

    Never raises. A caller building a write gate must not be able to fail because
    a remembered search went missing.
    """
    result: dict[str, Any] = {
        "grant": None,
        "reason": UNKNOWN_SEARCH,
        "search_id": str(search_id) if search_id else None,
        "consented_radius": None,
        "current_radius": None,
    }
    session = _resolve_session(session_id)
    if session is None:
        return {**result, "reason": NO_SESSION}

    record = recall(search_id, session_id=session)
    if not record:
        return result

    try:
        grant = parameter_search.grant_from_search(**record)
    except (ValueError, TypeError, AttributeError, KeyError):
        return {**result, "reason": REMINT_FAILED}

    consented = dict(grant.consent_radius)
    result["consented_radius"] = consented

    behavior = behavior_in_model(grant.behavior_no, shadow_model)
    if behavior is None:
        return {**result, "reason": BEHAVIOR_NOT_IN_MODEL}

    current = searched_behaviour_grant.consent_radius(behavior)
    result["current_radius"] = current
    if current != consented:
        return {**result, "reason": RADIUS_DRIFTED}
    if not (consented.get("complete") and current.get("complete")):
        return {**result, "reason": RADIUS_NOT_FULLY_NAMED}

    return {**result, "grant": grant, "reason": GRANTED}


def grant_provider(
    shadow_model_fn: Any, *, session_id: str | None = None, search_id: str | None = None
) -> Any:
    """A gate-shaped late binder: ``action -> SearchedBehaviourGrant | None``.

    #47's argument applies unchanged: both write gates are constructed in a
    request handler, BEFORE the thing they need exists — the proposing turn's
    search finishes mid-turn, and the approving turn's shadow model is only
    parsed once the working copy has been re-synced on the turn thread. So the
    grant is resolved at the moment the gate is asked for provenance rather than
    at construction.

    ``search_id`` PINS the provider to one remembered search — what an
    ``/agent/approve`` handler would pass, reading the reference out of
    ``pending_approval.json``. Without it the provider asks which remembered
    search covers the action, which is what a proposing turn needs (it has no
    reference yet). Either way the answer is a grant re-minted here and now, and
    ``gates._write_provenance`` still requires it to ``cover()`` the action
    before any provenance is produced.
    """

    def _provide(action: Mapping[str, Any] | None) -> SearchedBehaviourGrant | None:
        target = (
            str(search_id) if search_id else search_id_covering(action, session_id=session_id)
        )
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
    "MAX_SEARCHES_PER_SESSION",
    "MAX_SESSIONS",
    "MINT_REASONS",
    "NO_SESSION",
    "RADIUS_DRIFTED",
    "RADIUS_NOT_FULLY_NAMED",
    "REMINT_FAILED",
    "UNKNOWN_SEARCH",
    "active_session",
    "behavior_in_model",
    "forget",
    "forget_session",
    "grant_provider",
    "mint",
    "recall",
    "remember",
    "reset",
    "search_id_covering",
    "set_active_session",
]

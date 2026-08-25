"""Which network objects a reality check may propose against — DERIVED, or refused.

THE GAP THIS CLOSES (measured 2026-08-21, chip ``task_93c5b5dd``)
----------------------------------------------------------------
``reality_check`` grades every node it can join and then, for each miss, asks
"which object would an edit touch?". That question is answered by ``targets``,
which is CALLER knowledge — and on the calibration screen there was no caller.
``routers/calibration.py`` passed ``None`` at all three of its call sites, so
every real miss came back ``NO_TARGET_OBJECT``: a true refusal, and a permanent
one, because nothing on the server was ever going to supply the answer. The
screen could tell a modeller that node 8 is 215 vehicles short and never that
vehicle input 113 is the object that carries them.

This module is the missing caller, and it is deliberately a small one.

WHAT IT WILL DERIVE, AND WHAT IT REFUSES TO
-------------------------------------------
**Routing** (``decision_no`` / ``route_no`` / ``decision_movements``) is an
identity, not a judgement: a static routing decision sits ON a link, an observed
movement OBJECT_ID names the link it ENTERS FROM, and those are the same link or
they are not. A decision governs its own scope, so the movements it governs are
this node's rows entering from that link — no share, no dominance, no ranking.

**Demand** (``vehicle_input_no`` / ``link_no`` / ``current_volume``) is derived
only where the same kind of identity holds AND it is unique:

* the node's own observed rows must be Vissim movement OBJECT_IDs, because the
  link is recoverable from nothing else. Leg-keyed observation-store rows
  (``"<approach>|<turn>"``) and aliased workbooks carry approach LABELS, and
  ``fixes.entrance_measurement`` already names that absence
  (:data:`fixes.ENTRANCE_NO_LINK_KEYED_ROWS`); this module does not get to
  guess what that function refuses to;
* and EXACTLY ONE vehicle input may survive the join. A node with two counted
  approaches has two candidates and there is no evidence here that says which
  one carries the shortfall; the real client model puts THREE inputs on one link
  (107/207/307 on link 620, one per vehicle class — ``tools_network.
  _parse_vehicle_inputs``), which is the same ambiguity with a different cause.
  Both cases keep the honest ``NO_TARGET_OBJECT`` state.

WHAT IT MUST NEVER DO (a decision, recorded)
--------------------------------------------
Pick between candidates by DOMINANCE — "input 107 carries 69% of the link, so it
is the one". That number was hand-derived by a human in the calibration harness
and it is a judgement about which flow explains a miss, not a fact the network
states. Automating it would put this system's guess where a modeller's decision
belongs, and the guess would be invisible on the approval card. Two candidates
means no target, and the modeller names one.

WHERE THE MODELLER NAMES ONE (unit #54, 2026-08-24)
----------------------------------------------------
"The modeller names one" was, until this unit, a sentence with nowhere to be
carried out: the agent could pass a target on a turn, and the SCREEN could not.
So the six-candidate node stayed 분류 불가 forever while the harness — which
hardcodes one human's graph analysis, ``{node 8 -> input 113, link 617}`` —
reached ``CAPACITY_LIMIT`` on the same node and the same run.

``human_mappings`` is that answer, threaded in from
``services.demand_target_map`` through the router. It WINS over the derivation
for its node — but ONLY where the derivation had a choice to make.

**Machine outranks testimony**, which is #53's rule and not a new one. On a node
the derivation already resolved from the network alone
(:data:`SINGLE_VEHICLE_INPUT` — exactly one input feeds its counted approaches),
a mapping decides nothing and is recorded as ignored with
:data:`HUMAN_MAPPING_DERIVATION_ALREADY_UNIQUE`. It cannot even DISAGREE there:
with one candidate, the store's own check guarantees it names that same input.
Reporting :data:`HUMAN_NAMED_VEHICLE_INPUT` on such a node would attribute a
derivation to a modeller and put somebody's name on a fact that did not need
one. A human mapping is for choosing among SEVERAL, and only there.

The three properties that keep it from being a back door are:

* it can only name a triple the derivation ITSELF vouched for. The store checks
  the (node, input, link) against this module's own candidate list at naming
  time, and :func:`derive_targets` checks it AGAIN here against the live one —
  two guards, because a network that moved under a mapping must not keep it
  alive, and because one guard being deleted must not silently restore a claim;
* a STALE or REVOKED mapping is ignored and the node falls back to the exact
  absence state it had before anybody named anything. Never to a half-state, and
  never silently: ``nodes[id]["vehicle_input"]["human_mapping_ignored"]`` says
  which;
* the target it produces is labelled. ``reason`` becomes
  :data:`HUMAN_NAMED_VEHICLE_INPUT`, the signer and the timestamp ride on the
  target into ``evidence["target"]["human_named"]``, and the assumption token
  ``fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL`` rides beside them. A signature does
  not make "this input drives this node's total" PROVEN — it makes it
  ATTRIBUTED, and the difference is the whole product.

Every OTHER node is derived exactly as it was: this is a per-node override, not
a mode.

A derived demand target does NOT make the resulting proposal safe: it is still
subject to unit D4 (a MEASURED entrance is refused outright) and any proposal it
does produce still carries :data:`fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL`, the
product's own written admission that "this input drives this node's total" is
assumed rather than proven. Deriving the target does not weaken that admission —
it is precisely what makes it visible on the card.

WHAT A DEMAND TARGET FROM HERE ACTUALLY BUYS (measured 2026-08-23)
------------------------------------------------------------------
A refusal, essentially always — and that is the doctrine working, not a
shortfall. The join that finds the input is an observed movement OBJECT_ID whose
FROM-link is the input's own link, which is the SAME join
``fixes.entrance_measurement`` uses to prove the counts were taken AT that
entrance. So an input this module names sits on a link the workbook counted, its
rows carry observed volumes, and D4 answers :data:`fixes.ENTRANCE_MEASURED` by
construction: the demand edit is refused by name and the turn split is proposed
instead. What the screen gains is not a demand edit — it is the difference
between "no vehicle input was named as the target" (which was nobody's fault and
nobody's fix) and "vehicle input 1 feeds link 101, and link 101 is where the 910
vehicles were counted".

The demand ESTIMATE path (:data:`fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED`)
is therefore reachable only from a caller that names an input whose link the
counts do NOT reach — the agent, acting on a human's instruction, which is
exactly who that labelled-estimate path was built for.

:func:`derive_targets` itself is pure: no I/O, no COM, no network read. The
caller supplies the observed side and the offline ``.inpx`` parse; it joins
them and says what it could not. :func:`derive_targets_for_active_dataset`
(added #56 (c)) is the one impure sibling in this file -- see its own
docstring for why it lives here rather than staying private to
``routers/calibration.py``.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from services import results_evidence
from services.agent import fixes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Why a node got the target it got — machine-readable, one code per outcome
# ---------------------------------------------------------------------------

#: No observed row on this node is a Vissim movement OBJECT_ID, so no row can be
#: resolved to a link and neither half can be derived. The store's leg-keyed
#: rows and an aliased workbook both land here.
NO_OBJECT_ID_ROWS = "NO_OBJECT_ID_ROWS"
#: Exactly one vehicle input feeds a link this node is counted entering from.
SINGLE_VEHICLE_INPUT = "SINGLE_VEHICLE_INPUT"
#: No vehicle input feeds any of this node's counted approach links.
NO_VEHICLE_INPUT_ON_APPROACH = "NO_VEHICLE_INPUT_ON_APPROACH"
#: Several do. See the module docstring: this is where a dominance pick would
#: have gone, and does not.
MULTIPLE_VEHICLE_INPUT_CANDIDATES = "MULTIPLE_VEHICLE_INPUT_CANDIDATES"
#: A named human chose one of this node's candidates (#54). Not a derivation:
#: an attributed assumption, and the only reason code here that carries a name.
HUMAN_NAMED_VEHICLE_INPUT = "HUMAN_NAMED_VEHICLE_INPUT"
#: Exactly one static routing decision sits on this approach link.
DECISION_IDENTIFIED = "DECISION_IDENTIFIED"
#: One of that decision's routes ends on the link this movement exits by.
ROUTE_IDENTIFIED = "ROUTE_IDENTIFIED"
#: No static routing decision sits on this approach link.
NO_DECISION_ON_APPROACH = "NO_DECISION_ON_APPROACH"
#: More than one does, and a decision governing this turn cannot be chosen.
MULTIPLE_DECISIONS_ON_APPROACH = "MULTIPLE_DECISIONS_ON_APPROACH"
#: The decision is unique but no single route of it ends on this movement's
#: exit link (none does, or several do).
ROUTE_NOT_IDENTIFIED = "ROUTE_NOT_IDENTIFIED"
#: The route was identified but a relFlow on that decision could not be read, so
#: the sibling total the proposal's arithmetic needs cannot be stated.
REL_FLOW_UNREADABLE = "REL_FLOW_UNREADABLE"

REASON_CODES = (
    NO_OBJECT_ID_ROWS,
    SINGLE_VEHICLE_INPUT,
    NO_VEHICLE_INPUT_ON_APPROACH,
    MULTIPLE_VEHICLE_INPUT_CANDIDATES,
    HUMAN_NAMED_VEHICLE_INPUT,
    DECISION_IDENTIFIED,
    ROUTE_IDENTIFIED,
    NO_DECISION_ON_APPROACH,
    MULTIPLE_DECISIONS_ON_APPROACH,
    ROUTE_NOT_IDENTIFIED,
    REL_FLOW_UNREADABLE,
)

# ---------------------------------------------------------------------------
# Why a human mapping was NOT used — a different question from the above
# ---------------------------------------------------------------------------
#
# These never appear as a ``reason``: the reason is what the node's derivation
# concluded, and an ignored mapping leaves that conclusion exactly as it was.
# They ride alongside it, so "I named an input and the screen still says 분류
# 불가" has an answer other than silence.

#: The network moved (or could not be read) since the mapping was named.
HUMAN_MAPPING_STALE = "HUMAN_MAPPING_STALE"
#: The mapping was withdrawn. It is history, not evidence.
HUMAN_MAPPING_REVOKED = "HUMAN_MAPPING_REVOKED"
#: The named input is no longer one of this node's candidates — the second of
#: the two guards (the store checked the same thing at naming time).
HUMAN_MAPPING_NOT_A_CANDIDATE = "HUMAN_MAPPING_NOT_A_CANDIDATE"
#: The derivation had already resolved this node from the network alone, so the
#: testimony had nothing to decide. See :func:`derive_targets` — this codebase's
#: #53 rule is that a machine verdict outranks a human's account of the same
#: fact, and a node with ONE candidate is a machine verdict.
HUMAN_MAPPING_DERIVATION_ALREADY_UNIQUE = "HUMAN_MAPPING_DERIVATION_ALREADY_UNIQUE"

IGNORED_MAPPING_CODES = (
    HUMAN_MAPPING_STALE,
    HUMAN_MAPPING_REVOKED,
    HUMAN_MAPPING_NOT_A_CANDIDATE,
    HUMAN_MAPPING_DERIVATION_ALREADY_UNIQUE,
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _object_id_rows(node_id: str, evidence: Mapping[str, Any]) -> list[dict[str, str]]:
    """``[{"movement_id", "from_link", "to_link"}]`` for this node's real rows.

    A row is kept only when its id is a whole movement OBJECT_ID **and** the
    node that id names is this node. The second half is not paranoia about
    nothing: the id carries its own node token, so a row filed under the wrong
    node would otherwise contribute an approach link that belongs to a different
    intersection — and that link's vehicle input would then look like this
    node's single candidate.
    """
    rows: list[dict[str, str]] = []
    for movement in evidence.get("movements") or []:
        token = str(movement.get("id") or movement.get("direction") or "")
        from_link = fixes.movement_from_link(token)
        to_link = fixes.movement_to_link(token)
        if from_link is None or to_link is None:
            continue
        if results_evidence.node_token(token) != node_id:
            continue
        rows.append(
            {
                "movement_id": token,
                "from_link": fixes.canonical_link_id(from_link),
                "to_link": fixes.canonical_link_id(to_link),
            }
        )
    return rows


def _vehicle_input_for(
    approach_links: list[str], shadow_model: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str, list[str], list[dict[str, Any]]]:
    """``(input, reason, candidate ids, candidate rows)``.

    The fourth member is unit #54's: the same candidates, as ROWS a picker can
    render and a store can check a human's choice against — input number, the
    link it feeds and its current volume. Emitted here rather than recomputed by
    the router or the store, because "which inputs feed this node's counted
    approaches" must have exactly one implementation; a second one is how a
    screen comes to offer a choice this module would then refuse.
    """
    candidates = [
        entry
        for entry in shadow_model.get("vehicle_inputs") or []
        if isinstance(entry, Mapping)
        and fixes.canonical_link_id(entry.get("link")) in approach_links
        and str(entry.get("no") or "")
    ]
    ids = [str(entry.get("no")) for entry in candidates]
    rows = [
        {
            "vehicle_input_no": str(entry.get("no")),
            "link_no": str(fixes.canonical_link_id(entry.get("link")) or ""),
            # ``None`` passed through rather than defaulted: an input whose
            # volume the .inpx never states has an unknown one, and the builder
            # refuses EVIDENCE_INSUFFICIENT by name for it.
            "current_volume": _number(entry.get("volume")),
            "name": str(entry.get("name") or "") or None,
        }
        for entry in candidates
    ]
    if not candidates:
        return None, NO_VEHICLE_INPUT_ON_APPROACH, ids, rows
    if len(candidates) > 1:
        return None, MULTIPLE_VEHICLE_INPUT_CANDIDATES, ids, rows
    return dict(candidates[0]), SINGLE_VEHICLE_INPUT, ids, rows


def _usable_human_mapping(
    mapping: Mapping[str, Any] | None, candidate_rows: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    """``(mapping, None)`` if this claim still stands, else ``(None, why not)``.

    THE SECOND OF TWO GUARDS. The router threads only fresh, unrevoked mappings
    (``demand_target_map.fresh_index_payload``); this re-checks both flags and
    re-checks the triple against the LIVE candidate list. Neither guard is
    trusted alone — the store's check happened at naming time and the network
    may have moved since, and a caller that forgot to filter must not be able to
    resurrect a withdrawn claim.
    """
    if not isinstance(mapping, Mapping):
        return None, None
    if mapping.get("revoked") is True:
        return None, HUMAN_MAPPING_REVOKED
    if mapping.get("stale"):
        return None, HUMAN_MAPPING_STALE
    input_no = str(mapping.get("vehicle_input_no") or "").strip()
    link_no = str(fixes.canonical_link_id(mapping.get("link_no")) or "")
    if not input_no or not link_no:
        return None, HUMAN_MAPPING_NOT_A_CANDIDATE
    for row in candidate_rows:
        if row["vehicle_input_no"] == input_no and row["link_no"] == link_no:
            return {**row, "mapping": dict(mapping)}, None
    return None, HUMAN_MAPPING_NOT_A_CANDIDATE


def _decision_on(link: str, shadow_model: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    decisions = [
        entry
        for entry in shadow_model.get("routing_decisions") or []
        if isinstance(entry, Mapping)
        and fixes.canonical_link_id(entry.get("link")) == link
        and str(entry.get("no") or "")
    ]
    if not decisions:
        return None, NO_DECISION_ON_APPROACH
    if len(decisions) > 1:
        return None, MULTIPLE_DECISIONS_ON_APPROACH
    return dict(decisions[0]), DECISION_IDENTIFIED


def _route_for(
    decision: Mapping[str, Any], to_link: str
) -> tuple[dict[str, Any] | None, float | None, float | None, str]:
    """``(route, current_rel_flow, sibling_total, reason)`` for one exit link.

    THE IDENTITY, and its reach, stated plainly: a route is matched when its
    ``destLink`` IS the link the movement exits by. A route that continues past
    this intersection ends somewhere else and simply does not match — this
    refuses rather than walking ``link_seq`` to see whether the route "passes
    through", because a link appearing somewhere along a path does not say the
    route serves this turn (several routes off one approach share their early
    links), and a rule whose answer depends on where a modeller happened to end
    a route is not an identity.

    Both relFlow values are required. relFlow is a RAW weight, so the value that
    produces an observed share ``p`` is ``sibling_total * p / (1 - p)``
    (``fixes._build_routing_proposal``, verified live: Vissim does not
    renormalize siblings on write) — without the siblings' sum there is no
    proposal to make, and a route whose own current weight cannot even be read is
    not one this module will name as a target for rewriting.
    """
    routes = [entry for entry in decision.get("routes") or [] if isinstance(entry, Mapping)]
    matches = [
        entry for entry in routes if fixes.canonical_link_id(entry.get("dest_link")) == to_link
    ]
    if len(matches) != 1:
        return None, None, None, ROUTE_NOT_IDENTIFIED
    route = matches[0]
    current = _number(route.get("rel_flow"))
    siblings = [entry for entry in routes if entry is not route]
    sibling_values = [_number(entry.get("rel_flow")) for entry in siblings]
    if current is None or any(value is None for value in sibling_values):
        return None, None, None, REL_FLOW_UNREADABLE
    sibling_total = float(sum(value or 0.0 for value in sibling_values))
    return dict(route), current, sibling_total, ROUTE_IDENTIFIED


def derive_targets(
    *,
    observed: Mapping[str, Mapping[str, Any]] | None,
    shadow_model: Mapping[str, Any] | None,
    human_mappings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """``{"targets": [RealityCheckNodeTarget], "nodes": {node_id: why}}``.

    ``observed`` is ``reality_check.observed_nodes(...)["nodes"]`` — the same
    ``{node_id: NodeRealCounts}`` the report itself grades, normalized here
    through ``fixes.node_evidence_from_real_counts`` so the movement rows this
    joins against are byte-for-byte the ones the report will join against.
    ``shadow_model`` is ``tools_network.build_shadow_model``'s offline parse.

    ``human_mappings`` is ``{node_id: demand_target_map.summarize(...)}`` — unit
    #54's per-node human override, described in this module's docstring. Absent
    (the default) this function behaves exactly as it did before that unit.

    Either side missing yields no targets and no opinion — the caller's
    behaviour is then exactly what it was before this module existed.

    ``nodes`` is the audit half: one entry per node saying which links it was
    counted entering from, which vehicle inputs feed those links, and, by
    :data:`REASON_CODES`, why each half of its target does or does not exist. It
    is also what the demand-target picker renders and what the store checks a
    human's choice against.
    """
    # Deferred, and for the reason every module in this package states at its
    # own deferred import of ``tools_metrics``: importing it pulls the whole
    # tool registry in and reorders tool registration.
    from services.agent.tools_metrics import RealityCheckNodeTarget

    result: dict[str, Any] = {"targets": [], "nodes": {}}
    if not observed or not isinstance(shadow_model, Mapping):
        return result

    for raw_node_id, node in sorted(observed.items(), key=lambda item: str(item[0])):
        if not isinstance(node, Mapping):
            continue
        node_id = str(raw_node_id)
        evidence = fixes.node_evidence_from_real_counts(node)
        rows = _object_id_rows(node_id, evidence)
        approach_links: list[str] = []
        for row in rows:
            if row["from_link"] not in approach_links:
                approach_links.append(row["from_link"])

        why: dict[str, Any] = {
            "approach_links": list(approach_links),
            "object_id_rows": len(rows),
            "vehicle_input": {
                "chosen": None,
                "candidates": [],
                "candidate_inputs": [],
                "reason": NO_OBJECT_ID_ROWS,
            },
            "routing": {},
        }
        result["nodes"][node_id] = why
        if not rows:
            continue

        # -- the DEMAND half ------------------------------------------------
        vehicle_input, reason, candidate_ids, candidate_rows = _vehicle_input_for(
            approach_links, shadow_model
        )
        why["vehicle_input"] = {
            "chosen": None if vehicle_input is None else str(vehicle_input.get("no")),
            "candidates": candidate_ids,
            "candidate_inputs": candidate_rows,
            "reason": reason,
        }

        # #54: a named human's choice WINS over the derivation for this node —
        # and only for this node, and only where the derivation had a choice to
        # make. The auto answer above stays computed and stays visible in
        # ``candidates``: the reader has to be able to see that six inputs were
        # candidates and that a person picked one of them, which is a different
        # claim from "the network says 113".
        named, ignored = _usable_human_mapping(
            (human_mappings or {}).get(node_id), candidate_rows
        )
        if named is not None and reason == SINGLE_VEHICLE_INPUT:
            # MACHINE OUTRANKS TESTIMONY (#53's rule, applied here). Exactly one
            # vehicle input feeds this node's counted approaches, so the network
            # has ALREADY answered and there was nothing for a person to choose
            # between. The mapping is not wrong — with one candidate the store's
            # own check guarantees it names that same input — it is simply not
            # what decided, and reporting HUMAN_NAMED here would attribute a
            # derivation to a modeller and put an unnecessary name on a fact.
            #
            # It is recorded rather than dropped: the audit block below keeps the
            # mapping's id and author beside the code saying why it did not
            # decide, so "I named an input and the screen credits the network"
            # has an answer.
            why["vehicle_input"]["mapping_id"] = named["mapping"].get("id")
            why["vehicle_input"]["named_by"] = str(named["mapping"].get("named_by") or "") or None
            named, ignored = None, HUMAN_MAPPING_DERIVATION_ALREADY_UNIQUE
        named_by: str | None = None
        named_at: str | None = None
        if ignored is not None:
            why["vehicle_input"]["human_mapping_ignored"] = ignored
        if named is not None:
            mapping = named["mapping"]
            named_by = str(mapping.get("named_by") or "") or None
            named_at = str(mapping.get("named_at") or "") or None
            vehicle_input = {
                "no": named["vehicle_input_no"],
                "link": named["link_no"],
                "volume": named["current_volume"],
            }
            why["vehicle_input"].update(
                {
                    "chosen": named["vehicle_input_no"],
                    "reason": HUMAN_NAMED_VEHICLE_INPUT,
                    "named_by": named_by,
                    "named_at": named_at,
                    "mapping_id": mapping.get("id"),
                    "mapping_reason": mapping.get("reason"),
                    # A signer does not make this proven. The token that says so
                    # travels with the target and lands on the approval card.
                    "assumption": fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL,
                }
            )

        if vehicle_input is not None:
            result["targets"].append(
                RealityCheckNodeTarget(
                    node_id=node_id,
                    vehicle_input_no=str(vehicle_input.get("no")),
                    # Interval 1's volume, as the shadow model reports it. ``None``
                    # is passed through rather than defaulted: the builder then
                    # refuses EVIDENCE_INSUFFICIENT by name, which is the truth
                    # about an input whose volume the .inpx never states.
                    vehicle_input_current_volume=_number(vehicle_input.get("volume")),
                    # The link is what unit D4 joins the observed rows against.
                    # Supplying it is what lets a MEASURED entrance be REFUSED
                    # instead of quietly relabelled a demand estimate.
                    vehicle_input_link_no=str(vehicle_input.get("link") or "") or None,
                    # #54's provenance. ``None`` on every derived target, which
                    # is what makes a non-None value mean exactly one thing:
                    # a person put their name on this.
                    vehicle_input_named_by=named_by,
                    vehicle_input_named_at=named_at,
                )
            )

        # -- the ROUTING half -----------------------------------------------
        for link in approach_links:
            on_link = [row for row in rows if row["from_link"] == link]
            scope = [row["movement_id"] for row in on_link]
            decision, decision_reason = _decision_on(link, shadow_model)
            link_why: dict[str, Any] = {
                "decision_no": None if decision is None else str(decision.get("no")),
                "reason": decision_reason,
                "decision_movements": list(scope),
                "routes": {},
            }
            why["routing"][link] = link_why
            if decision is None:
                continue
            for row in on_link:
                route, current, sibling_total, route_reason = _route_for(decision, row["to_link"])
                link_why["routes"][row["movement_id"]] = {
                    "route_no": None if route is None else str(route.get("route_no")),
                    "reason": route_reason,
                }
                if route is None:
                    continue
                result["targets"].append(
                    RealityCheckNodeTarget(
                        node_id=node_id,
                        movement=row["movement_id"],
                        decision_no=str(decision.get("no")),
                        route_no=str(route.get("route_no")),
                        current_rel_flow=current,
                        sibling_rel_flow_total=sibling_total,
                        # The decision's SCOPE — every row of this node entering
                        # from the link the decision sits on. It is the
                        # denominator the observed share is taken against, and
                        # ``fixes`` refuses rather than falling back to the node
                        # total without it.
                        decision_movements=list(scope),
                    )
                )

    return result


def derive_targets_for_active_dataset(
    *,
    shadow_model: Mapping[str, Any] | None,
    period: str | None = None,
    observation_set_id: str | None = None,
    human_mappings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """:func:`derive_targets`'s WHOLE answer over the ACTIVE dataset's own
    observed-counts workbook, or ``None``.

    THE I/O HALF, extracted 2026-08-25 (#56 (c)) from what was
    ``routers/calibration.py``'s private ``_derive_all``. That router function
    is now a one-line forward to this one -- its docstring's "ONE derivation,
    called from all three of this router's reality-check call sites AND from
    the demand-target routes" is still true, and this is why: an agent TOOL
    needs the identical derivation (services must not import ``routers.*``,
    so the router's private helper was never reachable from
    ``services/agent/tools_calibration.py``), and the alternative -- a second
    function that re-reads the workbook and re-derives -- is exactly the
    "two opinions about which object a miss belongs to" doctrine this module's
    own docstring already refuses. ``routers/calibration.py``'s tests keep
    pinning ``_derive_all`` by name; only its body changed.

    Everything below is exactly what ``_derive_all`` did: read the active
    dataset's observed-counts workbook, join it against ``shadow_model``
    through :func:`derive_targets`, and degrade any failure to ``None`` -- an
    unreadable workbook is missing evidence for the targets, not a 500.

    ``observation_set_id`` short-circuits for the reason ``_derive_all``
    documented it: a stored observation set needs a ``site_nodes`` map no
    caller of this function supplies, and ``observations.observed_side``
    refuses without one anyway, so such a request is NOT_CHECKABLE with no
    nodes to target either way -- deriving would cost a workbook read to
    answer a question nobody asked.
    """
    if shadow_model is None or observation_set_id:
        return None
    try:
        # Deferred: importing ``tools_metrics`` (for ``RealityCheckNodeTarget``,
        # via ``derive_targets`` below) pulls the whole tool registry in and
        # would reorder tool registration -- the same reason every other
        # module in this package defers that same import.
        from services import datasets, validation
        from services.agent import reality_check

        observed = reality_check.observed_nodes(
            validation.load_base_workbook(str(datasets.get_active_real_counts_excel())),
            period=period or None,
        )
        if observed["problem"] is not None:
            return None
        return derive_targets(
            observed=observed["nodes"],
            shadow_model=shadow_model,
            human_mappings=human_mappings,
        )
    except Exception as exc:  # noqa: BLE001 — no target is a state, not a fault
        logger.warning(
            "reality_check_targets: edit targets could not be derived (%s: %s)",
            type(exc).__name__,
            exc,
        )
        return None


__all__ = [
    "DECISION_IDENTIFIED",
    "HUMAN_MAPPING_DERIVATION_ALREADY_UNIQUE",
    "HUMAN_MAPPING_NOT_A_CANDIDATE",
    "HUMAN_MAPPING_REVOKED",
    "HUMAN_MAPPING_STALE",
    "HUMAN_NAMED_VEHICLE_INPUT",
    "IGNORED_MAPPING_CODES",
    "MULTIPLE_DECISIONS_ON_APPROACH",
    "MULTIPLE_VEHICLE_INPUT_CANDIDATES",
    "NO_DECISION_ON_APPROACH",
    "NO_OBJECT_ID_ROWS",
    "NO_VEHICLE_INPUT_ON_APPROACH",
    "REASON_CODES",
    "REL_FLOW_UNREADABLE",
    "ROUTE_IDENTIFIED",
    "ROUTE_NOT_IDENTIFIED",
    "SINGLE_VEHICLE_INPUT",
    "derive_targets",
    "derive_targets_for_active_dataset",
]

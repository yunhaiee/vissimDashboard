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

Pure: no I/O, no COM, no network read. The caller supplies the observed side and
the offline ``.inpx`` parse; this module joins them and says what it could not.
"""
from __future__ import annotations

from typing import Any, Mapping

from services import results_evidence
from services.agent import fixes

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
    DECISION_IDENTIFIED,
    ROUTE_IDENTIFIED,
    NO_DECISION_ON_APPROACH,
    MULTIPLE_DECISIONS_ON_APPROACH,
    ROUTE_NOT_IDENTIFIED,
    REL_FLOW_UNREADABLE,
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
) -> tuple[dict[str, Any] | None, str, list[str]]:
    """``(input, reason, candidate ids)`` — the ONE input on these links, or none."""
    candidates = [
        entry
        for entry in shadow_model.get("vehicle_inputs") or []
        if isinstance(entry, Mapping)
        and fixes.canonical_link_id(entry.get("link")) in approach_links
        and str(entry.get("no") or "")
    ]
    ids = [str(entry.get("no")) for entry in candidates]
    if not candidates:
        return None, NO_VEHICLE_INPUT_ON_APPROACH, ids
    if len(candidates) > 1:
        return None, MULTIPLE_VEHICLE_INPUT_CANDIDATES, ids
    return dict(candidates[0]), SINGLE_VEHICLE_INPUT, ids


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
) -> dict[str, Any]:
    """``{"targets": [RealityCheckNodeTarget], "nodes": {node_id: why}}``.

    ``observed`` is ``reality_check.observed_nodes(...)["nodes"]`` — the same
    ``{node_id: NodeRealCounts}`` the report itself grades, normalized here
    through ``fixes.node_evidence_from_real_counts`` so the movement rows this
    joins against are byte-for-byte the ones the report will join against.
    ``shadow_model`` is ``tools_network.build_shadow_model``'s offline parse.

    Either side missing yields no targets and no opinion — the caller's
    behaviour is then exactly what it was before this module existed.

    ``nodes`` is the audit half: one entry per node saying which links it was
    counted entering from and, by :data:`REASON_CODES`, why each half of its
    target does or does not exist. Nothing renders it today; it exists so the
    answer "no target" is inspectable rather than a silence.
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
            "vehicle_input": {"chosen": None, "candidates": [], "reason": NO_OBJECT_ID_ROWS},
            "routing": {},
        }
        result["nodes"][node_id] = why
        if not rows:
            continue

        # -- the DEMAND half ------------------------------------------------
        vehicle_input, reason, candidate_ids = _vehicle_input_for(approach_links, shadow_model)
        why["vehicle_input"] = {
            "chosen": None if vehicle_input is None else str(vehicle_input.get("no")),
            "candidates": candidate_ids,
            "reason": reason,
        }
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


__all__ = [
    "DECISION_IDENTIFIED",
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
]

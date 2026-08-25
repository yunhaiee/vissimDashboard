"""Unit #47 — the product's entry point to #44's Table 5-6 car-following set.

One read-tier tool: ``propose_car_following_from_saturation_flow``. It is the
"제안" step of the product path ``assistant turn -> proposal -> approval card ->
approval`` that #44/#44-b were built for and that nothing in the product could
reach: ``saturation_flow.propose_car_following_parameters`` had exactly two
callers before this module, ``backend/tools/calibration_loop_live_check.py`` and
the test suite.

The rung is NEVER taken from the planner
----------------------------------------
``propose_car_following_parameters`` takes ``rung=`` and refuses anything that is
not ``geh_ladder.CAPACITY_LIMIT`` — that check is the premise of the whole
treatment ("§5.2.5.1 answers an ESTABLISHED capacity limit"). If this tool
accepted a rung as a parameter, a planner could type ``CAPACITY_LIMIT`` and the
premise would be a self-reported flag: the exact shape this repository has had to
un-ship twice (``_check_driving_behavior_shared_object_edit``'s original
``duplicated_before_edit`` boolean, and ``saturation_flow``'s original trust in
``measurement["ok"]`` — both recorded in ``car_following_grant``'s docstring).

So the tool re-derives it. ``diagnosis`` is a nested copy of
``RunRealityCheckParams``, and this tool re-runs ``build_reality_check_report``
over the run's own result databases, then reads the named node's ``diagnosis``
out of THAT report. The rung a proposal cites is therefore computed from the run
at proposal time, by the server, from the same code path ``run_reality_check``
uses.

The one input to that derivation that is not the run's (refuter, #47)
---------------------------------------------------------------------
This module first claimed that NOTHING a caller passes could make a
non-capacity-limited node look capacity-limited. That was false, and the
measurement is short: ``RunRealityCheckParams.targets[].node_control`` is a
CALLER assertion, and ``geh_ladder.classify_node``'s last branch reads
``concentrated and control == CONTROL_SIGNALIZED`` — so on identical run
evidence (delay on 1 of 4 approaches, node 5, the same measurement)

* ``node_control="signalized"`` → ``SIGNAL_TIMING`` → refused, as it should be;
* ``node_control`` **omitted** → ``CAPACITY_LIMIT`` → proposal + a mintable
  grant, with the ladder's ``NODE_CONTROL_UNKNOWN`` caveat as the only trace.

The premise was therefore obtainable by SAYING NOTHING, which is the shape of
every guard this repo has had to un-ship. So the omission is now refused by
name (:data:`NODE_CONTROL_UNDECLARED`), and the refusal says which fact is
missing and where to read it. What that leaves is a caller who declares
``"unsignalized"`` about a node that is signalised — a false statement about
the NETWORK, the same class of input as ``vehicle_input_link_no`` and
``movement_object_id``, which this whole surface takes from the caller by
design ("supplied by the CALLER, never guessed") and which the modeller makes
in the open, in the ladder they are reading. It is not, and this module does
not claim it is, structurally prevented.

The other three inputs, and why they are inputs
-----------------------------------------------
* **The measurement** is genuinely the modeller's to supply — MRWA Table 5-6 takes
  a MEASURED stop-line saturation flow and this tool cannot go and measure one.
  It is not trusted either: it is built through
  ``saturation_flow.observed_measurement``, whose data-quality gates (method and
  attribution present, Appendix B §B.4 sample minimums) are then re-derived from
  the record's own fields by ``_authoritative_measurement``. Absent or unciteable,
  the tool returns the REFUSAL that asks for one — never a row read on a guess.
* **The behaviour number** names a target; everything ABOUT that behaviour —
  its reference count, its named consumers, the blast radius a human will read —
  is read from the live shadow model, never from the caller.
* **The scope** is context for the rationale only (``_preflight_forecast`` records
  it and never lets it lower a verdict).

What it hands to the rest of the machinery
------------------------------------------
On success the three ``FixProposal``s are handed to
``car_following_broker.remember``, which mints a grant to validate them and keeps
the live objects in process memory for the approval that may follow. The tool
returns the proposals (for the answer and the approval card), the set's identity,
the bracket rows, the preflight forecast and the consented radius — and never the
grant itself, which exists only inside the broker and only in this process.

Read-tier on purpose: it proposes and applies nothing. The three writes it
describes still go through the planner, the ``ProposeWriteGate`` pause, the
approval card and a human's click, exactly like every other write.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services import demand_target_map
from services.agent import (
    car_following_broker,
    car_following_grant,
    fixes,
    geh_ladder,
    reality_check_targets,
    saturation_flow,
)
from services.agent.tools_metrics import RunRealityCheckParams, build_reality_check_report
from services.agent.tools_network import _current_shadow_model
from services.agent.toolkit import tool

logger = logging.getLogger(__name__)

#: Refused: the node landed on CAPACITY_LIMIT only because nobody said whether
#: it is signal-controlled (refuter, #47).
#:
#: ``geh_ladder`` records this as a CAVEAT on a fired capacity rung rather than a
#: blocker, which is right for a DIAGNOSIS — the report still has something true
#: to say. It is not right for a TREATMENT: MRWA's calibration order puts step 6
#: (signal timing) before step 7 (stop-line saturation flow), so on a node whose
#: delay is concentrated the way a green-split problem concentrates it, §5.2.5.1's
#: remedy is being proposed out of order unless the node is known not to be
#: signalised. And the same field is what separates this rung from
#: ``SIGNAL_TIMING`` at all, so accepting the omission would make the premise
#: something a caller obtains by leaving a field out.
NODE_CONTROL_UNDECLARED = "NODE_CONTROL_UNDECLARED"


class SaturationFlowMeasurementParams(BaseModel):
    """A stop-line saturation flow somebody actually measured, with its provenance.

    Every field except the value itself exists so the number can be CITED. MRWA
    grades saturation flow by method (Appendix B §B.3's hierarchy), so a value
    with no method cannot be placed in that hierarchy at all — which is why
    ``method`` and ``attribution`` are not optional in effect: omit either and the
    builder returns ``MEASUREMENT_PROVENANCE_MISSING`` instead of a proposal.
    """

    model_config = ConfigDict(extra="forbid")

    saturation_flow_pcu: float = Field(
        description=(
            "The MEASURED straight-movement stop-line saturation flow, in pcu per hour of "
            "green. This is the table's input; it is never estimated for you."
        )
    )
    method: str | None = Field(
        default=None,
        description=(
            "How it was measured (e.g. 'on-site stop-line survey', 'SCATS detector "
            "extraction'). Required — without it the measurement cannot be placed in MRWA "
            "Appendix B §B.3's method hierarchy and is refused as un-citable."
        ),
    )
    attribution: str | None = Field(
        default=None,
        description="Who measured it (person, team or agency). Required, same reason as method.",
    )
    measured_at: str | None = Field(
        default=None, description="When it was measured, as the modeller states it."
    )
    samples: float | None = Field(
        default=None,
        description=(
            "Number of saturated-green samples per lane. MRWA Appendix B §B.4 Table B-3 wants "
            "at least 6; fewer is refused, not warned."
        ),
    )
    vehicles_per_sample: float | None = Field(
        default=None,
        description="Vehicles per sample. MRWA Appendix B §B.4 wants at least 5; fewer is refused.",
    )
    intersection: str | None = Field(default=None, description="The intersection it was taken at.")
    approach_link: str | None = Field(default=None, description="The approach link it was taken on.")
    note: str | None = Field(default=None, description="Anything else the modeller wants recorded.")


class ProposeCarFollowingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis: RunRealityCheckParams = Field(
        description=(
            "The SAME arguments you passed to run_reality_check. The node's rung is re-derived "
            "from the run here rather than taken from you: MRWA §5.2.5.1's car-following "
            "calibration answers an ESTABLISHED capacity limit, so the diagnosis is recomputed "
            "from the run's own result databases at proposal time. Include this node's "
            "node_control in targets (from get_signal_heads / get_signal_controllers) -- it is "
            "the one part of the diagnosis the run cannot supply, and when the delay is "
            "concentrated on a few approaches the proposal is REFUSED without it, because a "
            "signalised node's next step is step 6 (signal timing), not step 7."
        )
    )
    node_id: str = Field(
        description=(
            "Which node of that report this treatment is for. Its diagnosis must be "
            "CAPACITY_LIMIT; any other rung is refused by name, with the rung it actually has."
        )
    )
    behavior_no: str = Field(
        description=(
            "The urban (Wiedemann 74) driving behaviour to calibrate, from get_driving_behaviors. "
            "Everything about it -- reference count, named consumers, the blast radius the "
            "approval card states -- is read from the network, never from you."
        )
    )
    measurement: SaturationFlowMeasurementParams = Field(
        description="The measured stop-line saturation flow Table 5-6 is entered with."
    )
    behavior_scope: Literal["network_wide", "localized"] | None = Field(
        default=None,
        description=(
            "Context for the rationale: whether the modeller intends this as the network-wide "
            "urban calibration §5.2.5.1 describes (the default reading) or a local change. It "
            "never changes the proposed params and never lowers a guardrail verdict -- asking "
            "for localisation is not the same as having localised, which needs "
            "duplicate_driving_behavior first."
        ),
    )


def _node_entry(report: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    wanted = str(node_id or "")
    for node in report.get("nodes") or ():
        if isinstance(node, dict) and str(node.get("node_id", "")) == wanted:
            return node
    return None


def _behavior_target(behavior_no: str) -> dict[str, Any]:
    """The live shadow model's own entry for the named behaviour, in the shape
    ``saturation_flow`` fingerprints.

    Returns ``{}`` when the network has no such behaviour, which the builder then
    refuses with ``NO_DRIVING_BEHAVIOR_TARGET`` — the honest outcome rather than a
    fabricated target. ``referenced_by`` rides along because it is what becomes
    ``evidence["consent_radius"]``: the blast radius the approval card states and
    the left-hand side of the drift check at apply time. A target passed WITHOUT
    it would name an empty radius, fail the drift check against this same model
    and refuse — safe, but it would look like a bug in the exception rather than
    a missing field, so it is supplied at the one place the target is read.
    """
    shadow = _current_shadow_model()
    entry = car_following_broker.behavior_in_model(behavior_no, shadow)
    if entry is None:
        return {}
    return {
        "no": str(entry.get("no")),
        "name": entry.get("name"),
        "car_following_model": entry.get("car_following_model"),
        "reference_count": entry.get("reference_count", 0),
        "referenced_by": entry.get("referenced_by") or {},
    }


@tool(
    name="propose_car_following_from_saturation_flow",
    tier="read",
    provider="metrics",
    params=ProposeCarFollowingParams,
)
def propose_car_following_from_saturation_flow(params: ProposeCarFollowingParams) -> dict[str, Any]:
    """Turn a MEASURED stop-line saturation flow into the three MRWA Table 5-6 car-following values (ax, bx_add, bx_mult) for one driving behaviour, as ONE proposal set a human then approves -- or into an evidenced refusal. Read-only: it writes nothing and applies nothing. The node's diagnosis is RE-DERIVED here from the run (pass the same arguments you gave run_reality_check under 'diagnosis'), never taken from you: §5.2.5.1's calibration answers an established CAPACITY_LIMIT, and any other rung is refused by name. The one part of that derivation the run cannot supply is whether the node is signal-controlled, so put node_control on the node's entry in diagnosis.targets: where the delay is concentrated on a minority of approaches and node_control is absent, the proposal is refused (NODE_CONTROL_UNDECLARED) rather than reading a capacity limit off a fact nobody stated -- a signalised node's next step is MRWA step 6, which precedes step 7. The measurement's method and attribution are required and its sample counts are checked against MRWA Appendix B §B.4 -- short of a citable measurement the tool ASKS for one instead of reading a row on a guess. Between two published rows the value rounds DOWN, and both bracketing rows are reported so the choice is auditable. The three values are one row of one table: approving only some of them leaves the behaviour matching no published row, and the set says so in all three languages. Driving behaviours are shared objects, so the set also forecasts what the guardrail will say on each path -- refused if the write is issued on its own, clean through the approval machinery when the blast radius can be named in full -- and states that radius, which is the sentence the approval card asks a human to read."""
    report = build_reality_check_report(
        params.diagnosis.scenario_id,
        period=params.diagnosis.period,
        targets=params.diagnosis.targets,
        observation_set_id=params.diagnosis.observation_set_id,
        observation_period=params.diagnosis.observation_period,
        site_nodes=params.diagnosis.site_nodes,
    )
    if report.get("status") != "CHECKED":
        return {
            "message": (
                "The run could not be compared against the observed counts "
                f"({report.get('reason_code')}), so no node has a diagnosis and no car-following "
                "treatment can be justified. "
                + str((report.get("message") or {}).get("en") or "")
            ),
            "car_following_proposal": {
                "status": "NOT_DIAGNOSABLE",
                "reason_code": report.get("reason_code"),
                "node_id": params.node_id,
                "report_message": report.get("message"),
            },
        }

    node = _node_entry(report, params.node_id)
    if node is None:
        graded = [str(n.get("node_id")) for n in (report.get("nodes") or ()) if isinstance(n, dict)]
        return {
            "message": (
                f"Node {params.node_id} is not in this run's reality check, so it has no "
                f"diagnosis to act on. Nodes compared: {', '.join(graded) or 'none'}."
            ),
            "car_following_proposal": {
                "status": "NODE_NOT_IN_REPORT",
                "node_id": params.node_id,
                "compared_node_ids": graded,
            },
        }

    diagnosis = node.get("diagnosis")
    diagnosis_payload = diagnosis if isinstance(diagnosis, dict) else {}
    if geh_ladder.NODE_CONTROL_UNKNOWN in (diagnosis_payload.get("missing_evidence") or ()):
        # See NODE_CONTROL_UNDECLARED. Refused BEFORE the measurement is read:
        # the modeller should not be told their survey was the problem when the
        # missing thing is one field about the network.
        return {
            "message": (
                f"Node {params.node_id}'s delay is concentrated on a minority of its approaches "
                "and nothing in this check says whether the node is signalised, so the capacity "
                "diagnosis rests on an unstated fact. MRWA step 6 (signal timing) precedes step 7 "
                "(stop-line saturation flow): supply node_control for this node (from "
                "get_signal_heads / get_signal_controllers) under diagnosis.targets and re-run."
            ),
            "car_following_proposal": {
                "status": "REFUSED",
                "reason_code": NODE_CONTROL_UNDECLARED,
                "node_id": params.node_id,
                "node_name": node.get("node_name"),
                "rung": diagnosis_payload.get("rung"),
                "diagnosis": diagnosis_payload or None,
                "grant_available": False,
                "proposals": [],
                "rationale": {
                    "ko": f"노드 {params.node_id}의 지체가 일부 접근로에 몰려 있는데, 이 노드가 신호 "
                    "교차로인지가 이번 대조에 지정되지 않았습니다. 신호 교차로라면 MRWA 보정 순서상 "
                    "⑥ 신호 시간이 ⑦ 정지선 포화교통류보다 먼저이므로, 그 사실을 모른 채 §5.2.5.1의 "
                    "차량추종 보정을 제안하지 않습니다. get_signal_heads / get_signal_controllers로 "
                    "확인한 뒤 diagnosis.targets에 node_control을 지정해 다시 요청하십시오.",
                    "en": f"Node {params.node_id}'s delay sits on a minority of its approaches, and "
                    "nothing in this check states whether the node is signalised. If it is, MRWA's "
                    "calibration order puts step 6 (signal timing) BEFORE step 7 (stop-line "
                    "saturation flow), so §5.2.5.1's car-following treatment is not proposed "
                    "without that fact. Read it with get_signal_heads / get_signal_controllers and "
                    "supply node_control on this node's entry in diagnosis.targets.",
                    "ja": f"ノード {params.node_id} の遅れは一部の流入部に集中していますが、この"
                    "ノードが信号交差点かどうかが今回の対照で指定されていません。信号交差点であれば"
                    "MRWAの補正順序では⑥信号時間が⑦停止線飽和交通流より先であるため、その事実が"
                    "不明なまま §5.2.5.1 の追従補正は提案しません。get_signal_heads / "
                    "get_signal_controllers で確認のうえ、diagnosis.targets の該当ノードに "
                    "node_control を指定して再実行してください。",
                },
            },
        }

    measurement = saturation_flow.observed_measurement(
        saturation_flow_pcu=params.measurement.saturation_flow_pcu,
        method=params.measurement.method,
        attribution=params.measurement.attribution,
        measured_at=params.measurement.measured_at,
        samples=params.measurement.samples,
        vehicles_per_sample=params.measurement.vehicles_per_sample,
        intersection=params.measurement.intersection or node.get("node_name") or params.node_id,
        approach_link=params.measurement.approach_link,
        note=params.measurement.note,
    )
    behavior = _behavior_target(params.behavior_no)

    proposals = saturation_flow.propose_car_following_parameters(
        rung=diagnosis,
        measurement=measurement,
        behavior=behavior,
        behavior_scope=params.behavior_scope,
        shadow_model=_current_shadow_model(),
    )

    payload = build_proposal_payload(proposals, diagnosis=diagnosis, node=node)
    if payload["status"] == "PROPOSED":
        # Remembered ONLY on the complete-set path, and remembered as live
        # objects in process memory (see car_following_broker's docstring for why
        # nothing about a grant is ever persisted). A ``None`` here -- no session
        # context, or a set grant_from_proposals declines -- costs the exception,
        # not the proposal: the three writes still reach an approval card, and
        # the ordinary shared-object refusal applies to them, which is the
        # pre-#44-b behaviour.
        set_id = car_following_broker.remember(proposals)
        payload["grant_available"] = set_id is not None
        if set_id is None:
            logger.info(
                "car-following set for node %s behaviour %s was not brokered; the approval "
                "path will see no grant",
                params.node_id,
                params.behavior_no,
            )

    return {"message": payload["message_en"], "car_following_proposal": payload}


def build_proposal_payload(
    proposals: tuple[Any, ...],
    *,
    diagnosis: dict[str, Any] | None,
    node: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The tool envelope's ``car_following_proposal`` block.

    Split out so the router's approval-card payload and this tool's own return
    value are built from ONE shape. Everything here is derived from the proposals'
    own evidence — no sentence is composed in this module that the proposal did
    not already say, in all three languages, at the place that knows why.
    """
    actions = list(proposals or ())
    node_payload = node if isinstance(node, dict) else {}
    rung = (diagnosis or {}).get("rung") if isinstance(diagnosis, dict) else None
    base: dict[str, Any] = {
        "status": "REFUSED",
        "node_id": str(node_payload.get("node_id") or ""),
        "node_name": node_payload.get("node_name"),
        "rung": rung,
        "diagnosis": diagnosis if isinstance(diagnosis, dict) else None,
        "grant_available": False,
        "proposals": [action.to_dict() for action in actions],
    }

    if not actions:
        return {
            **base,
            "message_en": "No car-following proposal and no refusal was produced.",
        }

    if not all(action.is_proposal for action in actions):
        refusal = actions[0]
        return {
            **base,
            "reason_code": refusal.reason_code,
            "rationale": refusal.rationale(),
            "message_en": refusal.rationale_en,
        }

    evidence = dict(actions[0].evidence)
    lookup = dict(evidence.get("lookup") or {})
    measurement = dict(evidence.get("measurement") or {})
    radius = dict(evidence.get("consent_radius") or {})
    return {
        **base,
        "status": "PROPOSED",
        "set_id": evidence.get("set_id"),
        "set_members": list(evidence.get("set_members") or ()),
        "expected_after_apply": dict(evidence.get("expected_after_apply") or {}),
        "behavior_no": str((actions[0].params or {}).get("behavior_no") or ""),
        "behavior_scope": evidence.get("behavior_scope"),
        "applied_row": dict(evidence.get("applied_row") or {}),
        "lookup": {
            "status": lookup.get("status"),
            "measured_pcu": lookup.get("measured_pcu"),
            "bracket_lower": lookup.get("bracket_lower"),
            "bracket_upper": lookup.get("bracket_upper"),
            "policy": lookup.get("policy"),
            "policy_basis": lookup.get("policy_basis"),
            "citation": lookup.get("citation"),
        },
        "measurement": measurement,
        # Quoted, never re-written: the same three sentences that already end
        # every member's rationale (saturation_flow.PARTIAL_APPROVAL_WARNING).
        # The approval card pauses on ONE of the three writes, so it has to be
        # able to say this on its own.
        "partial_approval_warning": dict(saturation_flow.PARTIAL_APPROVAL_WARNING),
        "consent_radius": radius,
        "consent_radius_phrase": car_following_grant.radius_phrase(radius),
        "preflight_forecast": dict(evidence.get("preflight_forecast") or {}),
        "citation": dict(evidence.get("citation") or {}),
        "assumptions": list(actions[0].assumptions),
        "message_en": actions[0].rationale_en,
    }


#: Re-exported so a consumer of this module does not also have to import the
#: ladder to name the one rung this treatment answers.
CAPACITY_LIMIT = geh_ladder.CAPACITY_LIMIT


# ---------------------------------------------------------------------------
# get_demand_target_derivation — #56 (c)
# ---------------------------------------------------------------------------
#
# THE GAP THIS CLOSES (DECISIONS.md, 2026-08-24, "같은 날 추가 측정"): a node
# whose demand target the derivation could not resolve reads UNCLASSIFIABLE
# with a next action, and until now the assistant had NO TOOL that could read
# WHY -- reality_check_targets.derive_targets' own per-node audit (reason
# code, the candidate rows it vouches for, any standing #54 mapping) lived
# only in the router's HTTP response for the SCREEN's picker
# (GET /api/calibration/demand-targets/candidates). Asked to explain or fix a
# target-less node, the assistant's only lever was get_vehicle_inputs -- which
# lists every vehicle input in the NETWORK and cannot say which ones feed
# THIS node's counted approaches or why none/several were chosen -- so it
# re-issued the identical call, hit the read-tier duplicate-call guard
# (FIX 2, orchestrator.py), and burned the whole iteration cap on a call that
# could never have answered the question. This tool reads the SAME audit the
# picker reads, through the SAME derivation (see
# reality_check_targets.derive_targets_for_active_dataset's own docstring on
# why that extraction exists) -- never a second one.


class GetDemandTargetDerivationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str | None = Field(
        default=None,
        description=(
            "One node to inspect (its counts-workbook / Vissim node id). Omit to get every node "
            "the derivation looked at."
        ),
    )
    period: Literal["AM", "PM"] | None = Field(
        default=None,
        description=(
            "Which counts period's rows decide the node's counted approach links. Omitted = the "
            "workbook's default period, the same rule run_reality_check's own `period` follows."
        ),
    )


@tool(
    name="get_demand_target_derivation",
    tier="read",
    provider="metrics",
    params=GetDemandTargetDerivationParams,
)
def get_demand_target_derivation(params: GetDemandTargetDerivationParams) -> dict[str, Any]:
    """Read WHY a node's demand target (the vehicle input a reality-check demand edit would touch) does or does not exist -- the derivation's own audit, never re-derived here. Per node: the reason code (no vehicle input reaches the node's counted approaches at all -- naming one cannot help; several feed it and none is auto-picked; exactly one does and it is the target; or a person named one under #54), the candidate rows the derivation itself vouches for (vehicle input number, link, current volume -- the same rows the screen's picker offers), the standing #54 human mapping if any (including a STALE or IGNORED one and why it does not decide), and the assumption token a resolved target carries. Call this INSTEAD of get_vehicle_inputs when a node's diagnosis is unclassified and names a missing demand target: get_vehicle_inputs lists every vehicle input in the whole network and cannot say which feed THIS node's counted approaches or why none/several were chosen, so repeating it on the same refusal returns the same unusable list every time and burns the iteration cap for nothing. Read-only and proposes nothing -- naming an input for a node with several candidates is still a human act, through the screen's picker or the demand-targets route."""
    shadow = _current_shadow_model()
    standing: dict[str, dict[str, Any]] = {}
    try:
        standing = demand_target_map.index_payload()
    except Exception as exc:  # noqa: BLE001 — an unreadable store is a state, not a fault
        logger.info(
            "get_demand_target_derivation: demand-target mappings unreadable (%s: %s)",
            type(exc).__name__,
            exc,
        )
        standing = {}

    derived = reality_check_targets.derive_targets_for_active_dataset(
        shadow_model=shadow,
        period=params.period,
        observation_set_id=None,
        human_mappings=standing,
    )
    if derived is None:
        return {
            "message": "The observed-counts workbook could not be read, so no demand target "
            "could be derived for any node.",
            "demand_target_derivation": {"available": False, "reason": "NO_DERIVATION", "nodes": {}},
        }

    all_nodes = derived.get("nodes") or {}
    if params.node_id is not None and str(params.node_id) not in all_nodes:
        return {
            "message": f"Node {params.node_id} was not part of this derivation (it carries no "
            "counted rows this workbook's period joins to a Vissim movement id).",
            "demand_target_derivation": {
                "available": True,
                "reason": "NODE_NOT_FOUND",
                "node_id": str(params.node_id),
                "known_node_ids": sorted(all_nodes, key=lambda value: (len(value), value)),
                "nodes": {},
            },
        }

    nodes_out: dict[str, Any] = {}
    for node_id, why in all_nodes.items():
        if params.node_id is not None and str(node_id) != str(params.node_id):
            continue
        demand = (why or {}).get("vehicle_input") or {}
        # A target EXISTS (SINGLE_VEHICLE_INPUT or a human named one) the
        # instant `chosen` is set — both carry the same admission every
        # resolved target in this product carries (fixes._human_named_block's
        # own docstring): naming an input drives this node's total is
        # ASSUMED, never proven, whether the network or a person supplied it.
        chosen = demand.get("chosen")
        nodes_out[str(node_id)] = {
            "approach_links": list((why or {}).get("approach_links") or []),
            "reason": demand.get("reason"),
            "chosen": chosen,
            "candidates": list(demand.get("candidate_inputs") or []),
            "human_mapping_ignored": demand.get("human_mapping_ignored"),
            "mapping": standing.get(str(node_id)),
            "assumption": fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL if chosen else None,
        }

    return {
        "message": f"Demand-target derivation audit for {len(nodes_out)} node(s).",
        "demand_target_derivation": {"available": True, "reason": None, "nodes": nodes_out},
    }


__all__ = [
    "CAPACITY_LIMIT",
    "GetDemandTargetDerivationParams",
    "NODE_CONTROL_UNDECLARED",
    "ProposeCarFollowingParams",
    "SaturationFlowMeasurementParams",
    "build_proposal_payload",
    "get_demand_target_derivation",
    "propose_car_following_from_saturation_flow",
]

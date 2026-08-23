"""The fix mapping (Phase 2 unit U4) — from "what is wrong" to "which
parameter, which verb".

Roadmap row 2.1's chain is: anomaly catalogue (U3, ``anomalies.py``) → **fix
mapping (this module)** → approval loop (``guardrails``/``gates``) → reality
check (U5). This module is the practitioner-diagnosis step: given a detected
anomaly or a count-validation miss, it names the SPECIFIC parameter that
usually causes it and the SPECIFIC registry verb that adjusts it, with the
value already derived from the evidence — so the approval loop has something
concrete to approve and the audit trail has something concrete to record.

The doctrine
------------
**The mapping PROPOSES, the approval loop DISPOSES.** Nothing here applies an
edit, calls a tool, opens a database or touches COM. Every function returns a
:class:`FixProposal` describing an edit somebody else may or may not make.

Two rules follow from that, and they are what the tests pin hardest:

* **A proposal that cannot state its evidence must not be emitted.** "No
  defensible proposal" is a first-class outcome (:data:`REFUSAL` /
  :data:`NO_AUTOMATED_FIX`), never an exception and never a silent ``None``.
  Every refusal carries a machine ``reason_code`` and a ko/en/ja rationale.
* **A proposal must never overstate.** If the evidence supports "the split
  between turns is off", the mapping must NOT propose a demand change. This
  is the approved refusal shape recorded on roadmap row 2.1 (user decision
  2026-08-06): *node total reconciles within GEH<5 → routing-split question,
  NOT a demand error; the model is reproducing observed behaviour*. A
  routing-level proposal therefore carries the demand-level refusal it
  displaced, in :attr:`FixProposal.also_refused`, rather than quietly
  dropping it.
* **An observed entrance volume is DATA TO MATCH, not a knob** (unit D4,
  professor 2026-08-12 + the user's decided policy). Where the counts were
  taken AT the entrance a vehicle input feeds, rewriting that input's volume
  so the model's downstream numbers agree inverts the direction of
  calibration and destroys the measurement the run is graded against. The
  mapping refuses structurally (:data:`INPUT_VOLUME_IS_OBSERVED_DATA`) and
  keeps grading, so the knobs that ARE ours — the turn split, and beyond this
  module behaviour, speeds and signals — still get proposed. Where
  measured-ness cannot be VERIFIED, the demand proposal survives (the
  approval card is the "ask the user first" step) but is relabelled as what
  it is: a named demand ESTIMATE, never an unnamed guess. The verdict itself
  is :func:`entrance_measurement`, which states its own reach.

Three outcomes, one shape
-------------------------
:func:`map_node_counts` — the entry point row 2.1's loop calls with a node's
observed vs modelled totals and per-movement splits — answers exactly one of:

* **(a) demand-side proposal** (``set_vehicle_input_volume``) when the node
  TOTAL itself misses;
* **(b) routing-side proposal** (``set_route_relative_flow``) when the total
  reconciles but a movement split misses;
* **(c) REFUSAL** when the total reconciles AND no movement misses — the
  model already reproduces observed behaviour, and the rationale states the
  GEH value and the threshold it relied on.

All three come back as one :class:`FixProposal` with the same keys (``verb``
and ``params`` are ``None`` on a refusal), so U5 and row 3.7 render one shape,
never two — the same "always present so consumers get one shape" convention
``anomalies.py``'s scan report already established.

GEH is not respelled here: :func:`services.validation.geh` computes it and
:data:`services.validation.GEH_GOOD_LT` is the threshold, both imported. A
``None`` GEH (undefined — see that function) is evidence-insufficient, never
a pass.

The mapping is derived, never hand-asserted
-------------------------------------------
Same doctrine as ``anomalies.get_anomaly_coverage()`` and
``constraints.get_edit_time_only_constraints()``. A hardcoded ``{id: verb}``
dict with no liveness check is exactly the failure mode U3 eliminated for
coverage, and it is not reintroduced here:

* :func:`get_fix_coverage` resolves every mapped verb against the LIVE tool
  registry (``toolkit.default_registry``) at call time. A verb that is no
  longer registered — or is registered under a tier that is not ``"write"``,
  which can never be an edit proposal — degrades the entry to
  :data:`VERB_UNREGISTERED`, and :func:`propose_fix` refuses instead of
  emitting a proposal naming a verb that does not exist.
* Every emitted proposal's ``params`` are validated against the verb's REAL
  pydantic params model from that same registry before the proposal leaves
  this module (:func:`validate_params`). A params payload the live schema
  rejects becomes a refusal, not a proposal.
* Anomaly-keyed "no automated fix" entries declare the arming they are
  predicated on, and :func:`get_fix_coverage` compares it against
  ``anomalies.effective_arming()`` live. The day ``starved_link`` gains a
  detector, this catalogue's "nothing detects it yet, so there is nothing to
  fix from" claim is flagged (:data:`ANOMALY_ARMING_DRIFTED`), not left to rot.
* :func:`get_unmapped_anomalies` is the mirror image: every anomaly in U3's
  catalogue with no entry here at all. Nothing may fall between a fix entry
  and a no-fix entry.

Why this is a separate lookup and not ``Finding.suggested_fix``
--------------------------------------------------------------
``constraints.Finding.suggested_fix`` already exists and is ``None`` on the
postrun warns, so reusing it was the obvious candidate. It is deliberately
NOT reused as this unit's output channel, for four measured reasons:

1. It is written INSIDE each constraint's own check function; there is no
   post-hoc hook. Populating it would mean editing ``constraints.py``'s check
   logic for every mapped constraint.
2. Its shape is per-constraint and already load-bearing for shipped
   consumers: ``health_check._fix_target`` copies it into a handoff prompt
   only when it is a ``dict``, and ``frontend/app/health/page.tsx`` reads
   ``min``/``max`` straight off it. A ``FixProposal`` posted there would
   change what those render.
3. It is one value per ``Finding``. This mapping must also key on ANOMALY
   ids (``latent_demand_diffusion`` produces no finding at all) and must be
   able to express a REFUSAL — and ``suggested_fix=None`` already means
   "this check computed no suggestion", not "refused, with evidence".
4. Its inputs are strictly smaller: a check sees ``action``/``shadow_model``/
   ``extra``, never the observed field counts a fix value is derived from.

So the dependency runs ONE WAY: this module READS ``suggested_fix`` where a
constraint already computed a defensible number (``latent_demand_overflow``'s
``max_recommended_volume`` is exactly that, and
``test_agent_constraints.py`` already feeds it straight into
``set_vehicle_input_volume``), and never writes into that channel.

No COM, no I/O
--------------
Everything operates on in-memory payloads some earlier stage produced.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from services import results_evidence, validation
from services.agent import anomalies, constraints
from services.agent.toolkit import ToolRegistry, default_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome of one mapping call (a property of the evidence in hand)
# ---------------------------------------------------------------------------

#: A concrete edit is proposed: verb + params + evidence + rationale.
PROPOSAL = "PROPOSAL"
#: No edit is proposed, and the rationale says why on the evidence.
REFUSAL = "REFUSAL"
#: This finding has NO defensible automated fix at all — a registered
#: statement of the gap, not an absent key.
NO_AUTOMATED_FIX = "NO_AUTOMATED_FIX"

OUTCOMES = (PROPOSAL, REFUSAL, NO_AUTOMATED_FIX)


# ---------------------------------------------------------------------------
# Scope of a fix — load-bearing (row 2.1's approved refusal shape)
# ---------------------------------------------------------------------------

#: The demand itself is wrong: a vehicle input carries the wrong volume.
DEMAND_LEVEL = "DEMAND_LEVEL"
#: The demand is right and its DISTRIBUTION between turns is wrong: a static
#: routing decision's relative flows.
ROUTING_SPLIT_LEVEL = "ROUTING_SPLIT_LEVEL"
#: Neither the demand nor its distribution is wrong: the network cannot PASS a
#: correctly-specified demand, and the car-following parameters that set the
#: discharge headway are the lever. Added by unit #44; the proposals live in
#: ``services.agent.saturation_flow``, which builds them from a MEASURED
#: saturation flow via MRWA Table 5-6 rather than from anything computed here.
#: Registered in this module because ``SCOPES`` is the one canonical list every
#: consumer validates against (``_register``'s own scope check; the coverage
#: test's ``outcome.scope in fixes.SCOPES``) — a sibling module that invented
#: its own scope string would silently fail that check.
CAR_FOLLOWING_LEVEL = "CAR_FOLLOWING_LEVEL"
#: No edit scope at all (a refusal, or an entry with no automated fix).
NOT_APPLICABLE = "NOT_APPLICABLE"

SCOPES = (DEMAND_LEVEL, ROUTING_SPLIT_LEVEL, CAR_FOLLOWING_LEVEL, NOT_APPLICABLE)


# ---------------------------------------------------------------------------
# Registration status (a property of the catalogue) — two declarable, two
# derived. Mirrors anomalies.py's arming exactly.
# ---------------------------------------------------------------------------

#: A registered write verb adjusts the causing parameter.
AUTOMATABLE = "AUTOMATABLE"
#: There is no defensible automated fix, and the entry says why.
NOT_AUTOMATABLE = "NOT_AUTOMATABLE"
#: Derived, never declared: the mapped verb is no longer in the live tool
#: registry (renamed, removed, or re-tiered). The anti-stale-claim state — a
#: hardcoded dict cannot produce it.
VERB_UNREGISTERED = "VERB_UNREGISTERED"
#: Derived, never declared: a no-fix entry's claim is predicated on an
#: anomaly arming that U3's catalogue no longer reports.
ANOMALY_ARMING_DRIFTED = "ANOMALY_ARMING_DRIFTED"

STATUSES = (AUTOMATABLE, NOT_AUTOMATABLE, VERB_UNREGISTERED, ANOMALY_ARMING_DRIFTED)
_DECLARABLE_STATUSES = (AUTOMATABLE, NOT_AUTOMATABLE)


# ---------------------------------------------------------------------------
# Reason codes — every refusal / no-fix carries exactly one
# ---------------------------------------------------------------------------

#: THE Phase-2 exit-criterion refusal. Node total reconciles AND no movement
#: split misses: the model already reproduces observed behaviour.
MODEL_REPRODUCES_OBSERVED = "MODEL_REPRODUCES_OBSERVED"
#: The node total reconciles, so a DEMAND change is not what the evidence
#: supports. Carried on a routing proposal's ``also_refused``.
NODE_TOTAL_RECONCILES = "NODE_TOTAL_RECONCILES"
#: The evidence needed to derive a value is missing or undefined (a GEH that
#: is None, a modelled total of 0, an unknown current value).
EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
#: The caller supplied no object to edit, so no proposal can name a target.
NO_TARGET_OBJECT = "NO_TARGET_OBJECT"
#: The edit this verb performs provably cannot change the observed miss.
FIX_CANNOT_HELP = "FIX_CANNOT_HELP"
#: No registered write verb reaches the causing parameter at all.
NO_REGISTERED_VERB = "NO_REGISTERED_VERB"
#: Verbs exist, but the evidence cannot say WHICH cause is in play or what
#: their parameters would have to be.
EVIDENCE_CANNOT_DISCRIMINATE = "EVIDENCE_CANNOT_DISCRIMINATE"
#: Not observable at all from the evidence this system can read (U3's
#: NOT_MEASURABLE, attached from there).
NOT_MEASURABLE = "NOT_MEASURABLE"
#: Nothing detects this anomaly yet, so there is nothing to fix from (U3's
#: NOT_YET_ARMED, attached from there).
NO_DETECTOR_YET = "NO_DETECTOR_YET"
#: Derived: the mapped verb vanished from the live tool registry.
VERB_NOT_IN_REGISTRY = "VERB_NOT_IN_REGISTRY"
#: Derived: the params this mapping built were rejected by the verb's OWN
#: live pydantic schema. Never emitted as a proposal.
PARAMS_REJECTED_BY_SCHEMA = "PARAMS_REJECTED_BY_SCHEMA"
#: No entry is registered for this key at all.
NO_MAPPING_REGISTERED = "NO_MAPPING_REGISTERED"
#: Unit D4 ("보정 방향 역전"). The vehicle input this edit would rewrite feeds
#: an entrance the observed counts were TAKEN AT: an entrance-level observed
#: row resolves to that input's own link. An observed entrance volume is the
#: calibration TARGET, not a knob — rewriting it so the model's downstream
#: numbers agree inverts the direction of calibration and destroys the very
#: measurement the run is graded against. Structural refusal, not a threshold.
INPUT_VOLUME_IS_OBSERVED_DATA = "INPUT_VOLUME_IS_OBSERVED_DATA"


# ---------------------------------------------------------------------------
# How a proposed value was obtained
# ---------------------------------------------------------------------------

#: Closed-form arithmetic over the stated observed/modelled numbers — exact
#: under the stated ``assumptions``.
CONFIDENCE_ARITHMETIC = "ARITHMETIC_FROM_OBSERVED_COUNTS"
#: Taken verbatim from a constraint's own computed ceiling
#: (``Finding.suggested_fix``), not re-derived here.
CONFIDENCE_CONSTRAINT_CEILING = "CONSTRAINT_COMPUTED_CEILING"
#: Unit D4. The SAME closed-form arithmetic as
#: :data:`CONFIDENCE_ARITHMETIC`, deliberately one notch lower because of what
#: the arithmetic is being applied TO: an entrance whose volume this system
#: could not verify as measured (:data:`ENTRANCE_UNVERIFIED`). The number is
#: exact under its stated assumptions; what is unverified is the premise that
#: the value it replaces was ever free to be replaced. Naming that in the
#: confidence field — not only in the rationale — keeps a consumer that ranks
#: proposals by confidence from ranking an estimate alongside a measurement.
CONFIDENCE_DEMAND_ESTIMATE = "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE"
#: No value proposed (refusal / no automated fix).
CONFIDENCE_NONE = "NONE"


# ---------------------------------------------------------------------------
# Assumption tokens — machine-readable caveats a proposal is exact UNDER
# ---------------------------------------------------------------------------

#: The named vehicle input is the one whose volume drives this node's total.
ASSUME_INPUT_DRIVES_NODE_TOTAL = "INPUT_DRIVES_NODE_TOTAL"
#: The decision's OTHER routes keep their current relFlow (Vissim does not
#: renormalize siblings on write — verified live, see
#: ``vissim_com.set_route_relative_flow``).
ASSUME_SIBLING_ROUTES_UNCHANGED = "SIBLING_ROUTES_UNCHANGED"
#: The caller's ``decision_movements`` list is the COMPLETE set of movements
#: the named routing decision governs. It is the denominator the observed
#: share is taken against, so a short list overstates this route's share and a
#: long one understates it. Never inferred here: a routing decision's scope is
#: network knowledge (``get_routing_decisions``), not something the counts
#: workbook or the run says, and its absence is a refusal (see
#: :func:`_build_routing_proposal`) rather than a silent fallback to the node
#: total.
ASSUME_DECISION_MOVEMENTS_COMPLETE = "DECISION_MOVEMENTS_COMPLETE"
#: The caller's ``interval_no`` addresses the interval the observed counts
#: were measured in. Never guessed from a period name here.
ASSUME_INTERVAL_MATCHES_COUNT_PERIOD = "INTERVAL_MATCHES_COUNT_PERIOD"
#: Unit D4. This proposal rewrites a vehicle input whose volume could NOT be
#: verified as measured (:func:`entrance_measurement` returned
#: :data:`ENTRANCE_UNVERIFIED`), so the value it proposes is a NAMED demand
#: ESTIMATE rather than a correction of a knob. The token is the machine half
#: of the same statement the rationale makes in prose: approving adopts an
#: estimate; if the real entrance count is known, the proposal should be
#: rejected and that count supplied instead.
ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED = "ENTRANCE_NOT_VERIFIED_MEASURED"


# ---------------------------------------------------------------------------
# Key kinds
# ---------------------------------------------------------------------------

KEY_CONSTRAINT = "constraint"
KEY_ANOMALY = "anomaly"
#: A count-validation miss. No constraint detects it — it lives in the
#: observed-counts workbook, not in the run's guardrail findings — so it gets
#: its own stable key rather than being matched out of prose.
KEY_VALIDATION = "validation"

#: The two validation-miss keys, named once so callers never spell them.
NODE_TOTAL_VOLUME_MISS = "node_total_volume_miss"
MOVEMENT_SPLIT_MISS = "movement_split_miss"
#: The CHECK that produces those two (and the refusal when neither applies).
#: Not a fix entry — there is no single verb for "count validation" — but a
#: stable key so a refusal is addressable and :func:`propose_fix` can be
#: called with it directly.
COUNT_VALIDATION = "count_validation"

_COUNT_VALIDATION_KEYS = frozenset(
    {NODE_TOTAL_VOLUME_MISS, MOVEMENT_SPLIT_MISS, COUNT_VALIDATION}
)


# ---------------------------------------------------------------------------
# The proposal (and the refusal — deliberately ONE type)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixProposal:
    """One answer from the mapping: a proposal, a refusal, or a stated gap.

    Deliberately one type for all three outcomes. ``verb``/``params`` are
    ``None`` unless ``outcome == PROPOSAL``, so a consumer (U5's reality
    check, row 3.7's active diagnosis, the approval card) branches on one
    field and renders one shape. A refusal is not a degenerate proposal —
    it is the product, and it carries the same evidence and the same
    tri-lingual rationale a proposal does.

    ``evidence`` echoes the SOURCE FIELD NAMES the values came from (U1's
    ``run_result`` keys, the validation workbook's own column names) so the
    audit trail records what was relied on, not just the conclusion.
    """

    key: str
    key_kind: str
    outcome: str
    scope: str
    verb: str | None
    params: dict[str, Any] | None
    evidence: dict[str, Any]
    rationale_ko: str
    rationale_en: str
    rationale_ja: str
    confidence: str
    reason_code: str | None = None
    assumptions: tuple[str, ...] = ()
    #: The scope this mapping CONSIDERED and refused. Set on a routing-level
    #: proposal to carry the demand-level refusal it displaced, so "we did
    #: not propose a demand change, and here is why" survives into the
    #: approval card instead of being dropped.
    also_refused: "FixProposal | None" = None

    @property
    def is_proposal(self) -> bool:
        return self.outcome == PROPOSAL

    def rationale(self) -> dict[str, str]:
        return {"ko": self.rationale_ko, "en": self.rationale_en, "ja": self.rationale_ja}

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "key_kind": self.key_kind,
            "outcome": self.outcome,
            "scope": self.scope,
            "verb": self.verb,
            "params": dict(self.params) if self.params is not None else None,
            "evidence": dict(self.evidence),
            "rationale": self.rationale(),
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "assumptions": list(self.assumptions),
            "also_refused": self.also_refused.to_dict() if self.also_refused else None,
        }


# ---------------------------------------------------------------------------
# The catalogue entry
# ---------------------------------------------------------------------------

_BuilderFn = Callable[..., FixProposal]


@dataclass(frozen=True)
class FixMapping:
    """One catalogue key → the parameter and verb that adjust it.

    ``key`` is a ``constraints`` constraint id, an ``anomalies`` anomaly id,
    or one of this module's two validation-miss keys — never prose, so row
    2.1's loop can go finding → anomaly → proposal by id.

    ``expected_anomaly_arming`` is only for anomaly-keyed entries and is what
    makes a "no automated fix" claim falsifiable: it is compared against
    ``anomalies.effective_arming()`` at call time.
    """

    key: str
    key_kind: str
    status: str
    scope: str
    label_ko: str
    label_en: str
    label_ja: str
    reason_ko: str
    reason_en: str
    reason_ja: str
    verb: str | None = None
    builder: _BuilderFn | None = None
    #: The verb params this mapping fills in.
    param_fields: tuple[str, ...] = ()
    #: The evidence field names this mapping reads — U1 ``run_result`` keys
    #: and validation-workbook column names, spelled as they are at source.
    evidence_fields: tuple[str, ...] = ()
    reason_code: str | None = None
    expected_anomaly_arming: str | None = None
    #: Where a measured reason was established, when it was established
    #: elsewhere. Attached, not restated (U3's convention).
    reason_source: str | None = None
    reason_verbatim_fn: Callable[[], str] | None = None
    roadmap_rows: tuple[str, ...] = ("2.1", "3.7")

    def label(self) -> dict[str, str]:
        return {"ko": self.label_ko, "en": self.label_en, "ja": self.label_ja}

    def reason(self) -> dict[str, str]:
        return {"ko": self.reason_ko, "en": self.reason_en, "ja": self.reason_ja}

    def reason_verbatim(self) -> str | None:
        if self.reason_verbatim_fn is None:
            return None
        try:
            return self.reason_verbatim_fn()
        except Exception:  # pragma: no cover - an attached reason must not crash a scan
            logger.exception("fixes: attached reason for %r could not be read", self.key)
            return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, FixMapping] = {}


def register_fix(mapping: FixMapping) -> FixMapping:
    """Add a mapping to the module-level catalogue, keyed by its key.

    Validates only what is knowable WITHOUT the tool registry — a duplicate
    key, an undeclarable status, an ``AUTOMATABLE`` entry with no verb or no
    builder, a ``NOT_AUTOMATABLE`` entry that names a verb anyway or omits
    its reason code. Whether the named verb still resolves is exactly what
    :func:`get_fix_coverage` derives live, and raising here would turn a
    reportable stale claim into an import-time crash of the whole backend —
    the same call ``anomalies.register_anomaly`` makes, for the same reason.
    """
    if mapping.key in _REGISTRY:
        raise ValueError(f"register_fix: duplicate fix key {mapping.key!r}")
    if mapping.status not in _DECLARABLE_STATUSES:
        raise ValueError(
            f"register_fix: {mapping.key!r} declared status {mapping.status!r}; declarable "
            f"values are {list(_DECLARABLE_STATUSES)} ({VERB_UNREGISTERED} and "
            f"{ANOMALY_ARMING_DRIFTED} are derived, never declared)"
        )
    if mapping.scope not in SCOPES:
        raise ValueError(f"register_fix: {mapping.key!r} declared unknown scope {mapping.scope!r}")
    if mapping.status == AUTOMATABLE:
        if not mapping.verb:
            raise ValueError(f"register_fix: {mapping.key!r} is {AUTOMATABLE} but names no verb")
        if mapping.builder is None:
            raise ValueError(
                f"register_fix: {mapping.key!r} is {AUTOMATABLE} but has no builder — a verb "
                "name without a way to fill its params is not a fix"
            )
        if mapping.scope == NOT_APPLICABLE:
            raise ValueError(
                f"register_fix: {mapping.key!r} is {AUTOMATABLE} but declares scope "
                f"{NOT_APPLICABLE}"
            )
    else:
        if mapping.verb:
            raise ValueError(
                f"register_fix: {mapping.key!r} is {NOT_AUTOMATABLE} but names verb "
                f"{mapping.verb!r}"
            )
        if not mapping.reason_code:
            raise ValueError(
                f"register_fix: {mapping.key!r} is {NOT_AUTOMATABLE} and must carry a "
                "reason_code saying WHY there is no automated fix"
            )
    if not (mapping.reason_ko and mapping.reason_en and mapping.reason_ja):
        raise ValueError(f"register_fix: {mapping.key!r} needs a ko/en/ja reason")
    _REGISTRY[mapping.key] = mapping
    return mapping


def get_fix_registry() -> tuple[FixMapping, ...]:
    """Every registered mapping, in registration order."""
    return tuple(_REGISTRY.values())


def get_fix(key: str) -> FixMapping | None:
    return _REGISTRY.get(key)


# ---------------------------------------------------------------------------
# Derived liveness — computed from live registry state on every call
# ---------------------------------------------------------------------------


def _ensure_verbs_imported() -> None:
    """Import the module that REGISTERS the write verbs, lazily.

    ``toolkit.default_registry`` is populated by import side effect, so a
    process that never imported ``tools_edit`` would see an empty registry
    and this module would report every mapping as ``VERB_UNREGISTERED`` —
    a false stale-claim, which is worse than no claim. Lazy + guarded,
    mirroring ``anomalies.armed_evaluation_types()``'s own late import: a
    top-level import here would drag ``tools_edit``'s whole dependency tree
    (edit sessions, dataset paths) into anything that merely wants to read
    the catalogue.
    """
    try:
        from services.agent import tools_edit  # noqa: F401
    except Exception:  # pragma: no cover - import failure is not this unit's story
        logger.exception("fixes: could not import tools_edit to populate the tool registry")


def _registry_for(registry: ToolRegistry | None) -> ToolRegistry:
    if registry is not None:
        # A caller-supplied registry (a test's scratchpad copy) is used
        # EXACTLY as given -- importing verbs into it is impossible anyway
        # (registration targets default_registry), and pretending otherwise
        # would make a mutation test pass against the wrong object.
        return registry
    _ensure_verbs_imported()
    return default_registry


def resolve_verb(verb: str | None, *, registry: ToolRegistry | None = None) -> dict[str, Any]:
    """``{"resolved": bool, "why": str|None, "tier": str|None}`` for a verb
    name, against the LIVE tool registry.

    A name that is not registered, or is registered under a tier that is not
    ``"write"`` (a read or a run can never be an edit proposal), does not
    resolve. This is the whole reason the mapping is derived rather than
    tabulated: renaming a verb in ``tools_edit.py`` changes this return value
    and no hand-written table can.
    """
    if not verb:
        return {"resolved": False, "why": "NO_VERB", "tier": None}
    spec = _registry_for(registry).get(verb)
    if spec is None:
        return {"resolved": False, "why": "NOT_REGISTERED", "tier": None}
    if spec.tier != "write":
        return {"resolved": False, "why": "WRONG_TIER", "tier": spec.tier}
    return {"resolved": True, "why": None, "tier": spec.tier}


def effective_status(
    mapping: FixMapping, *, registry: ToolRegistry | None = None
) -> tuple[str, dict[str, Any]]:
    """``(status, detail)`` from LIVE registry + LIVE anomaly-catalogue state.

    An ``AUTOMATABLE`` entry whose verb no longer resolves degrades to
    :data:`VERB_UNREGISTERED`. A ``NOT_AUTOMATABLE`` entry whose declared
    ``expected_anomaly_arming`` no longer matches what
    ``anomalies.effective_arming()`` reports degrades to
    :data:`ANOMALY_ARMING_DRIFTED` — the day ``starved_link`` gains a
    detector, this catalogue's "nothing detects it yet" claim is flagged
    rather than left to rot.
    """
    detail: dict[str, Any] = {"verb": mapping.verb}

    if mapping.status == AUTOMATABLE:
        resolution = resolve_verb(mapping.verb, registry=registry)
        detail["verb_resolution"] = resolution
        if not resolution["resolved"]:
            return VERB_UNREGISTERED, detail
        return AUTOMATABLE, detail

    if mapping.expected_anomaly_arming is not None:
        anomaly = anomalies.get_anomaly(mapping.key)
        if anomaly is None:
            detail["anomaly_arming"] = None
            detail["expected_anomaly_arming"] = mapping.expected_anomaly_arming
            return ANOMALY_ARMING_DRIFTED, detail
        arming, _resolved, _unresolved = anomalies.effective_arming(anomaly)
        detail["anomaly_arming"] = arming
        detail["expected_anomaly_arming"] = mapping.expected_anomaly_arming
        if arming != mapping.expected_anomaly_arming:
            return ANOMALY_ARMING_DRIFTED, detail

    return NOT_AUTOMATABLE, detail


def get_fix_coverage(*, registry: ToolRegistry | None = None) -> dict[str, dict[str, Any]]:
    """``{key: coverage}`` derived from live registry + catalogue state.

    Every field below is a derivation, not a stored value, so the coverage
    claim cannot outlive the verbs and anomalies it claims.
    """
    coverage: dict[str, dict[str, Any]] = {}
    for mapping in _REGISTRY.values():
        status, detail = effective_status(mapping, registry=registry)
        coverage[mapping.key] = {
            "key": mapping.key,
            "key_kind": mapping.key_kind,
            "label": mapping.label(),
            "declared_status": mapping.status,
            "status": status,
            "scope": mapping.scope,
            "verb": mapping.verb,
            "param_fields": list(mapping.param_fields),
            "evidence_fields": list(mapping.evidence_fields),
            "reason": mapping.reason(),
            "reason_code": mapping.reason_code,
            "reason_source": mapping.reason_source,
            "reason_verbatim": mapping.reason_verbatim(),
            "roadmap_rows": list(mapping.roadmap_rows),
            "detail": detail,
        }
    return coverage


def get_unmapped_anomalies() -> tuple[dict[str, Any], ...]:
    """Every anomaly in U3's catalogue with NO entry here at all.

    The mirror of ``anomalies.get_uncatalogued_postrun_constraints()`` and
    this catalogue's honesty about its own edges: an anomaly must have either
    a fix entry or an explicit no-fix entry, and nothing may fall between the
    two. Derived from ``anomalies.get_anomaly_registry()`` live, so a new
    anomaly added to U3 without a decision here shows up rather than being
    silently unfixable.
    """
    unmapped: list[dict[str, Any]] = []
    for anomaly in anomalies.get_anomaly_registry():
        if anomaly.id in _REGISTRY:
            continue
        arming, _resolved, _unresolved = anomalies.effective_arming(anomaly)
        unmapped.append({"anomaly_id": anomaly.id, "arming": arming, "label": anomaly.label()})
    return tuple(unmapped)


def validate_params(
    verb: str, params: Mapping[str, Any], *, registry: ToolRegistry | None = None
) -> tuple[bool, str | None]:
    """Validate a params payload against the verb's REAL pydantic model.

    The live registry's ``ToolSpec.params_model`` — the exact model
    ``toolkit.dispatch`` would validate against — never a copy of the field
    list. Every model forbids extra keys (``toolkit._require_forbids_extra``
    enforces that at registration), so an invented param name fails here
    instead of at approval time.
    """
    spec = _registry_for(registry).get(verb)
    if spec is None:
        return False, f"verb {verb!r} is not in the live tool registry"
    try:
        spec.params_model.model_validate(dict(params))
    except Exception as exc:  # pydantic ValidationError, or anything it raises
        return False, " ".join(str(exc).split())[:300]
    return True, None


def preflight_findings(
    proposal: FixProposal, shadow_model: Mapping[str, Any] | None = None
) -> list[constraints.Finding]:
    """Run the REAL preflight constraint catalog over a proposal.

    Read-only use of ``constraints.run_checks`` — the same call
    ``GuardrailEngine.preflight`` makes — so a caller (and this unit's own
    tests) can assert a proposal would survive the approval loop's own gate
    rather than merely being schema-shaped. Returns ``[]`` for a refusal:
    there is no action to check.
    """
    if not proposal.is_proposal or proposal.verb is None:
        return []
    return constraints.run_checks(
        "preflight",
        {"tool": proposal.verb, "params": dict(proposal.params or {})},
        dict(shadow_model or {}),
        {},
    )


# ---------------------------------------------------------------------------
# Evidence helpers — the node payload shapes this unit consumes
# ---------------------------------------------------------------------------

#: The validation workbook's own field names (``services.validation``'s
#: ``NodeRealCounts``/``DirectionCount`` TypedDicts), spelled once.
_WORKBOOK_FIELDS = ("total_real", "total_sim_basam", "directions", "real", "sim_basam")


def node_evidence_from_real_counts(node: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one node-level real-counts row into evidence shape.

    ``{node_id, observed_total, modelled_total, movements: [{id, direction,
    observed, modelled}], sources}``. ``modelled`` is the observed side's own
    ``sim_basam``/``total_sim_basam`` column until
    :func:`merge_modelled_from_run_result` replaces it with the run's own
    numbers.

    ``node`` is one of TWO shapes, and this is the only place that tells them
    apart: ``services.validation.NodeRealCounts`` (the base workbook — no
    ``count_basis`` key) or ``services.observations.observed_side``'s node
    dict (a STORED observation set — always carries ``count_basis``, e.g.
    ``"detector_counts"`` for a SCATS/WA site with no turning movement at
    all). Hardcoding ``sources.observed = "validation_workbook"`` regardless
    of which one arrived here was a false origin string on every
    observation-store-sourced proposal: an approver (or an audit trail read
    months later) would be told a number came from the base workbook when it
    actually came from ``services.observations``, with no ``count_basis`` to
    say the number might be a site-level detector aggregate rather than a
    movement count. This is an approval-gate provenance field — it must name
    the real origin and carry ``count_basis`` through, not merely be
    schema-shaped.
    """
    movements: list[dict[str, Any]] = []
    for direction in node.get("directions") or []:
        name = str(direction.get("direction") or "")
        movements.append(
            {
                "id": name,
                "direction": name,
                "observed": _number(direction.get("real")),
                "modelled": _number(direction.get("sim_basam")),
            }
        )
    count_basis = node.get("count_basis")
    observed_source = (
        f"services.observations (count_basis={count_basis})"
        if count_basis
        else "validation_workbook"
    )
    return {
        "node_id": str(node.get("node_id") or ""),
        "node_name": node.get("node_name"),
        "observed_total": _number(node.get("total_real")),
        "modelled_total": _number(node.get("total_sim_basam")),
        "movements": movements,
        "sources": {
            "observed": observed_source,
            "modelled": "validation_workbook",
            "workbook_fields": list(_WORKBOOK_FIELDS),
            "run_result_keys": [],
            "count_basis": count_basis,
        },
    }


def modelled_from_run_result(
    run_result: Mapping[str, Any] | None, node_id: str
) -> dict[str, Any] | None:
    """Per-movement MODELLED volumes for one node out of U1's ``run_result``.

    Reads ``run_result["movement_volumes"]`` — ``{OBJECT_ID: {"vehs", "node"}}``
    — and keeps the entries whose ``node`` token matches, dropping the
    node-level aggregate row Vissim writes alongside them (its OBJECT_ID IS
    the bare node id, so it would be double-counted).

    Returns ``None`` — never an empty dict, and never zeros — when the key is
    absent or carries U1's ``ARMED_BUT_EMPTY`` marker. "Collection was armed
    and returned nothing" is not "the node carried no traffic"; reporting the
    second when the first is true is the failure U3 exists to prevent, and a
    fix proposal derived from a fabricated zero would be exactly that failure
    with an edit attached.
    """
    if not run_result:
        return None
    raw = run_result.get("movement_volumes")
    if raw is None:
        return None
    if isinstance(raw, results_evidence.EmptyEvidenceMapping):
        return None
    if not isinstance(raw, Mapping) or not raw:
        # Present-but-empty with the marker class lost across a JSON boundary
        # is still ARMED_BUT_EMPTY -- the durable descriptor is what says so.
        return None

    target = str(node_id)
    movements: dict[str, float] = {}
    total = 0.0
    for object_id, row in raw.items():
        if not isinstance(row, Mapping):
            continue
        token = str(row.get("node") or results_evidence.node_token(object_id))
        if token != target:
            continue
        if str(object_id) == target:
            continue  # the node-level aggregate row, not a movement
        vehs = _number(row.get("vehs"))
        if vehs is None:
            continue
        movements[str(object_id)] = vehs
        total += vehs
    if not movements:
        return None
    return {"total": total, "movements": movements}


def merge_modelled_from_run_result(
    node_evidence: Mapping[str, Any], run_result: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Replace a node evidence payload's MODELLED numbers with the run's own.

    The observed side always stays the workbook's. Returns the payload
    unchanged (a copy) when the run carries no usable movement evidence —
    an unusable run must not blank out numbers the workbook did have.
    """
    merged = {k: (list(v) if isinstance(v, list) else v) for k, v in node_evidence.items()}
    modelled = modelled_from_run_result(run_result, str(merged.get("node_id") or ""))
    if modelled is None:
        return merged
    by_id = modelled["movements"]
    movements = []
    for movement in merged.get("movements") or []:
        entry = dict(movement)
        key = str(entry.get("id") or "")
        if key in by_id:
            entry["modelled"] = by_id[key]
        movements.append(entry)
    # Movements the run knows and the workbook does not: kept, with no
    # observed side, so the modelled total is not quietly understated.
    known = {str(m.get("id") or "") for m in movements}
    for object_id, vehs in by_id.items():
        if object_id not in known:
            movements.append(
                {"id": object_id, "direction": None, "observed": None, "modelled": vehs}
            )
    merged["movements"] = movements
    merged["modelled_total"] = modelled["total"]
    sources = dict(merged.get("sources") or {})
    sources["modelled"] = "run_result"
    sources["run_result_keys"] = ["movement_volumes"]
    merged["sources"] = sources
    return merged


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: float) -> float:
    """Two decimals, once, so every proposal's number and its stated
    arithmetic agree exactly."""
    return round(value, 2)


def _fmt(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:g}"


# ---------------------------------------------------------------------------
# Is the entrance MEASURED? — unit D4 ("보정 방향 역전")
# ---------------------------------------------------------------------------

#: An entrance-level observed row resolves to the target vehicle input's OWN
#: link: what that input carries IS the measurement. Data to match, not a knob.
ENTRANCE_MEASURED = "measured"
#: The evidence in hand could not establish measured-ness in EITHER direction.
#: A category of its own, on purpose: "not verified as measured" is NOT
#: "verified as unmeasured, and therefore free to edit".
ENTRANCE_UNVERIFIED = "unverified"

ENTRANCE_STATUSES = (ENTRANCE_MEASURED, ENTRANCE_UNVERIFIED)

#: WHY a verdict landed where it did — machine-readable, so a consumer can
#: tell "we looked and found the measurement" from each distinct way of not
#: being able to look at all.
ENTRANCE_ROW_ON_INPUT_LINK = "ROW_ON_INPUT_LINK"
ENTRANCE_NO_LINK_ON_TARGET = "NO_LINK_ON_TARGET"
ENTRANCE_NO_MOVEMENT_ROWS = "NO_MOVEMENT_ROWS"
ENTRANCE_NO_LINK_KEYED_ROWS = "NO_LINK_KEYED_ROWS"
ENTRANCE_NO_ROW_ON_INPUT_LINK = "NO_ROW_ON_INPUT_LINK"
ENTRANCE_ROW_HAS_NO_OBSERVED = "ROW_HAS_NO_OBSERVED"

#: The ko/en/ja phrase each ``why`` contributes to a RATIONALE — deliberately
#: terse. The full sentence, with the ids and the numbers, lives in the
#: verdict's ``basis`` and travels in the evidence.
#:
#: Measured 2026-08-18 (unit #45 — this note said the cap was 300, which it has
#: not been since D4 raised it): the cap is
#: ``routers/assistant.MAX_APPROVAL_REASON_CHARS`` = **600**, and the rationale
#: this phrase rides in — the demand-ESTIMATE proposal below — runs 385 (ko) /
#: 365 (ja) / 647 (en) chars, so en ALREADY overflows and is abridged by its
#: caller. ``basis`` runs 86 (``ROW_HAS_NO_OBSERVED``) to 160
#: (``ROW_ON_INPUT_LINK``) chars over the six ``why`` values on the demo
#: fixture — it interpolates ids, so a longer network moves both ends up, and
#: this note said "98-160" when the floor was 86. Inlining it would therefore
#: push en further past a cap it is already past, and spend most of ko's and
#: ja's remaining headroom, for a detail the evidence block carries verbatim.
#:
#: THOSE THREE NUMBERS ARE NOT RE-DERIVED HERE, and this note is the place that
#: proves why they must not be: it first shipped them as 380/360/642 — off by
#: five in every language, in a note whose own headline word is "Measured".
#: They are the demo loop's, they are ASSERTED (against the live cap) at
#: ``tests/test_calibration_loop_e2e.py``'s stage 4, its module docstring is
#: where they are written down, and ``test_agent_fixes`` pins this comment to
#: that docstring so a fourth copy cannot drift away from it again. Change
#: them by re-running that measurement, never by hand.
_ENTRANCE_WHY_PHRASES: dict[str, dict[str, str]] = {
    ENTRANCE_NO_LINK_ON_TARGET: {
        "ko": "이 입력의 링크가 주어지지 않음",
        "en": "the input's link was not supplied",
        "ja": "このインプットのリンクが未指定",
    },
    ENTRANCE_NO_MOVEMENT_ROWS: {
        "ko": "이 노드에 실측 이동류 행이 없음",
        "en": "no observed movement rows on this node",
        "ja": "このノードに実測移動の行がない",
    },
    ENTRANCE_NO_LINK_KEYED_ROWS: {
        "ko": "실측 이동류가 링크를 알 수 없는 접근로 라벨로만 기록됨",
        "en": "the observed movements are approach labels, not link-bearing OBJECT_IDs",
        "ja": "実測移動がリンクを特定できない流入部ラベルのみ",
    },
    ENTRANCE_NO_ROW_ON_INPUT_LINK: {
        "ko": "이 링크에서 진입하는 실측 행이 없음",
        "en": "no observed row enters from this link",
        "ja": "このリンクから流入する実測行がない",
    },
    ENTRANCE_ROW_HAS_NO_OBSERVED: {
        "ko": "해당 행에 실측값이 없음",
        "en": "that row carries no observed count",
        "ja": "当該行に実測値がない",
    },
}

#: node, FROM link, from position, to link, to position — and positions can be
#: negative, so splitting on "-" is not safe. Two leading integer runs are
#: (``results_evidence.node_token`` takes the first for the same reason).
_OBJECT_ID_FROM_LINK_RE = re.compile(r"^(\d+)-(\d+)-")

#: The WHOLE id, anchored at both ends: node, from link, from position, to
#: link, to position. Anchored because this one is used to read the FOURTH
#: field, and a prefix match would happily read a link number out of a token
#: whose tail it never looked at. Positions may be integral or negative
#: (``-?\d+(?:\.\d+)?``), which is the same reason splitting on "-" is unsafe.
_OBJECT_ID_FULL_RE = re.compile(r"^(\d+)-(\d+)-(-?\d+(?:\.\d+)?)-(\d+)-(-?\d+(?:\.\d+)?)$")


def canonical_link_id(value: Any) -> str:
    """A link id in the ONE spelling both sides of the D4 join are compared in.

    The join is a string comparison between two numbers that reached this
    module by different roads: the vehicle input's link (COM →
    ``vissim_com.get_vehicle_input`` → the caller's target, or the shadow
    model's ``link`` field) and a movement OBJECT_ID's own from-link digits.
    Those roads disagree about spelling. COM hands numeric attributes back as
    floats often enough that ``vissim_com`` already casts the sibling ``No``
    field through ``int()`` for exactly this reason (see its comment: a
    float-typed ``No`` renders ``"13.0"`` and misses every id keyed ``"13"``);
    ``"00617"`` is the same failure with a different cause.

    Left uncanonicalized, ``"617.0" != "617"`` would make a MEASURED entrance
    read as UNVERIFIED — and UNVERIFIED is the branch that ALLOWS the demand
    edit, as a labelled estimate. So the failure is not a missing refusal, it
    is a silent licence to overwrite a measurement, which is precisely what
    unit D4 exists to prevent. Canonicalizing is therefore the safe direction.

    An integral number becomes its integer spelling; everything else is
    returned verbatim, so a genuinely non-numeric link id still compares as
    itself. A NON-integral number is deliberately NOT truncated: turning
    ``"617.5"`` into ``"617"`` would manufacture a match, and a false MEASURED
    verdict refuses an edit that was legitimate — the opposite error, but
    still an error made up by this function rather than found in the data.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if not math.isfinite(number) or number != int(number):
        return text
    return str(int(number))


def movement_from_link(movement_id: Any) -> str | None:
    """The FROM-link of a Vissim movement OBJECT_ID, or ``None``.

    ``"8-617-1229.801336-617-1286.072980"`` → ``"617"``: node 8, from link 617
    at 1229.80, to link 617 at 1286.07. ``None`` for anything that is not an
    OBJECT_ID — a workbook turn token (``"E-W"``), an observation-store leg
    label (``"북측방면|left"``), a bare node id — because guessing a link out
    of a label is exactly the confident-wrong join this module refuses to make.
    """
    match = _OBJECT_ID_FROM_LINK_RE.match(str(movement_id or "").strip())
    return match.group(2) if match else None


def movement_to_link(movement_id: Any) -> str | None:
    """The TO-link of a Vissim movement OBJECT_ID, or ``None``.

    ``"8-12075-168.594386-758-13.532567"`` → ``"758"``: the link that turn
    leaves node 8 on. The sibling of :func:`movement_from_link`, and it lives
    here for the same reason that one does — this module owns what an OBJECT_ID
    is made of, so a consumer that needs the exit link does not get to invent a
    second parse of the same five fields.

    ``None`` for anything that is not a WHOLE OBJECT_ID, including a token that
    merely starts like one: the exit link is the fourth field, so a prefix match
    would report a link out of a string this function never finished reading.
    """
    match = _OBJECT_ID_FULL_RE.match(str(movement_id or "").strip())
    return match.group(4) if match else None


def entrance_measurement(
    node: Mapping[str, Any] | None, target: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Is the target vehicle input's entrance volume OBSERVED DATA? (unit D4)

    The professor's doctrine, made machine-checkable: an observed entrance
    count is the calibration TARGET, not a parameter. So before any proposal
    rewrites a vehicle input's volume, this asks whether the evidence in hand
    shows that volume was itself measured.

    Verdict is :data:`ENTRANCE_MEASURED` or :data:`ENTRANCE_UNVERIFIED` —
    never "unmeasured". The two are not the same claim and this function is
    not permitted to make the second one.

    WHAT IT CAN SEE (v1, evidence-level)
    ------------------------------------
    Exactly one join, and it is an identity rather than a guess: an observed
    movement row whose id is a Vissim movement OBJECT_ID names the link the
    movement ENTERS FROM (:func:`movement_from_link`), and the caller's target
    names the link its vehicle input feeds (``link_no``, read from
    ``get_vehicle_inputs``). Same link + a non-empty observed count on that row
    ⇒ the counts were taken AT this entrance ⇒ :data:`ENTRANCE_MEASURED`. That
    is the shape the real demo workbook has: node 8's row
    ``8-617-1229.801336-617-1286.072980`` carries 649 observed vehicles
    entering from link 617, which is the link vehicle input 113 feeds.

    Both sides go through :func:`canonical_link_id` first — ``"617.0"`` and
    ``"00617"`` must not read as a different link from ``"617"``, because that
    mismatch does not merely lose a refusal, it silently licenses the demand
    edit as an "unverified" estimate. And the cited count is the SUM over every
    counted row on that link, not the first: an approach usually fans out into
    several movements, and naming one of them would understate what was
    measured at the entrance being defended.

    WHAT IT CANNOT SEE — named absences, not silent passes
    ------------------------------------------------------
    * **The observation store's leg-keyed rows** (``services.observations``
      aggregates turning counts under ``"<approach leg>|<turn>"``, its own
      ``MOVEMENT_ID_SEPARATOR``). There is no link↔leg edge anywhere in this
      system today, so a store-sourced node cannot be resolved to a link at
      all. Building that edge is the named follow-up; until it exists these
      nodes are :data:`ENTRANCE_UNVERIFIED` with
      :data:`ENTRANCE_NO_LINK_KEYED_ROWS`.
    * **Detector-basis nodes**, whose ``directions`` are ``[]`` by
      construction (a loop counts a site total, not a turn) — no rows at all,
      :data:`ENTRANCE_NO_MOVEMENT_ROWS`.
    * **An aliased workbook**: when the caller supplies ``movement_object_id``
      aliases, ``reality_check._aliased_run_view`` renames the run's
      OBJECT_IDs to the workbook's own turn tokens BEFORE the evidence reaches
      here, so the link is no longer recoverable from the row id. Same verdict
      and same named follow-up as the store path.
    * **A target with no ``link_no``**: nothing to match against
      (:data:`ENTRANCE_NO_LINK_ON_TARGET`). This is a reach limit worth
      stating plainly — a caller who omits the link gets the estimation-
      labelled proposal rather than the refusal, so the label, the assumption
      token and this verdict are what keep that from being silent.

    No I/O, no COM, no network read: it answers from the two payloads it is
    handed, like everything else in this module.
    """
    node = node or {}
    target = target or {}
    input_no = target.get("input_no")
    # ``link_no`` is this module's spelling; ``link`` is the shadow model's own
    # field name, accepted so a caller that passes a vehicle input straight
    # through is not silently downgraded to UNVERIFIED. ``or`` rather than a
    # default, so an explicit ``link_no: None`` (what tools_metrics sends when
    # the caller omitted it) still falls back rather than short-circuiting.
    raw_link = target.get("link_no") or target.get("link")
    supplied_link = None if raw_link in (None, "") else str(raw_link).strip()
    link = None if supplied_link is None else canonical_link_id(supplied_link)
    sources = dict(node.get("sources") or {})
    node_id = str(node.get("node_id") or "")

    verdict: dict[str, Any] = {
        "status": ENTRANCE_UNVERIFIED,
        "why": ENTRANCE_NO_LINK_ON_TARGET,
        "basis": "",
        "input_no": None if input_no in (None, "") else str(input_no),
        # The CANONICAL form is what the join used, so it is what the evidence
        # reports; what the caller actually handed over is kept beside it,
        # because a provenance block that silently rewrote its input would be
        # unauditable exactly where auditing matters.
        "link_no": link,
        "link_no_supplied": supplied_link,
        "observed_row": None,
        "observed_on_input_link": None,
        "rows_on_input_link": [],
        "observed_source": sources.get("observed"),
        "count_basis": sources.get("count_basis"),
        "predicate": (
            "fixes.entrance_measurement v1 — an observed movement OBJECT_ID whose FROM-link is "
            "the vehicle input's own link (unit D4)"
        ),
    }

    if link is None:
        verdict["basis"] = (
            f"the target for vehicle input {verdict['input_no'] or '?'} named no link, so no "
            "observed row could be matched to this entrance"
        )
        return verdict

    movements = list(node.get("movements") or [])
    if not movements:
        verdict["why"] = ENTRANCE_NO_MOVEMENT_ROWS
        verdict["basis"] = (
            f"node {node_id or '?'}'s observed evidence carries no movement rows "
            f"(count_basis={sources.get('count_basis')!r}), so it cannot say whether link {link} "
            "was counted"
        )
        return verdict

    from_links: list[str] = []
    on_link: list[Mapping[str, Any]] = []
    for movement in movements:
        token = str(movement.get("id") or movement.get("direction") or "")
        from_link = movement_from_link(token)
        if from_link is None:
            continue
        from_link = canonical_link_id(from_link)
        if from_link not in from_links:
            from_links.append(from_link)
        if from_link == link:
            on_link.append(movement)

    verdict["node_from_links"] = from_links
    if not from_links:
        sample = [str(m.get("id") or m.get("direction") or "") for m in movements[:3]]
        verdict["why"] = ENTRANCE_NO_LINK_KEYED_ROWS
        verdict["basis"] = (
            f"none of node {node_id or '?'}'s {len(movements)} observed movement rows is a Vissim "
            f"movement OBJECT_ID (e.g. {sample}), so no row can be resolved to a link"
        )
        return verdict

    verdict["rows_on_input_link"] = [
        str(m.get("id") or m.get("direction") or "") for m in on_link
    ]

    if not on_link:
        verdict["why"] = ENTRANCE_NO_ROW_ON_INPUT_LINK
        verdict["basis"] = (
            f"node {node_id or '?'}'s observed rows enter from links {from_links}; none of them is "
            f"link {link}, the link vehicle input {verdict['input_no'] or '?'} feeds"
        )
        return verdict

    # EVERY counted row on that link, not the first one. An approach normally
    # fans out into several movements (the demo node's link 12075 carries
    # 249 + 17, its link 759 carries 3 + 4), so citing one row would understate
    # what was measured at this entrance -- and understating the measurement is
    # how a refusal that is right turns into a refusal an approver disbelieves.
    counted = [m for m in on_link if _number(m.get("observed")) is not None]
    if not counted:
        verdict["why"] = ENTRANCE_ROW_HAS_NO_OBSERVED
        verdict["basis"] = (
            f"node {node_id or '?'} has {len(on_link)} row(s) entering from link {link}, but none "
            "of them carries an observed count"
        )
        return verdict

    observed_total = _round(sum(float(_number(m.get("observed")) or 0.0) for m in counted))
    first = counted[0]
    row_id = str(first.get("id") or first.get("direction") or "")
    verdict["status"] = ENTRANCE_MEASURED
    verdict["why"] = ENTRANCE_ROW_ON_INPUT_LINK
    verdict["observed_on_input_link"] = observed_total
    verdict["observed_rows"] = [
        {
            "movement_id": str(m.get("id") or m.get("direction") or ""),
            "observed": _number(m.get("observed")),
            "modelled": _number(m.get("modelled")),
        }
        for m in counted
    ]
    #: The first counted row, kept as the example a sentence can name. The
    #: NUMBER to quote is ``observed_on_input_link``, which is all of them.
    verdict["observed_row"] = {
        "movement_id": row_id,
        "from_link": link,
        "observed": _number(first.get("observed")),
        "modelled": _number(first.get("modelled")),
    }
    rows_note = (
        f"observed movement row {row_id!r}"
        if len(counted) == 1
        else f"{len(counted)} observed movement rows (e.g. {row_id!r})"
    )
    verdict["basis"] = (
        f"{rows_note} enter node {node_id or '?'} from link {link} — the link vehicle input "
        f"{verdict['input_no'] or '?'} feeds — carrying {_fmt(observed_total)} counted vehicles "
        "in total"
    )
    return verdict


def _entrance_phrase(verdict: Mapping[str, Any], language: str) -> str:
    """The short ko/en/ja phrase for a verdict's ``why``."""
    return _ENTRANCE_WHY_PHRASES.get(str(verdict.get("why") or ""), {}).get(
        language, str(verdict.get("basis") or "")
    )


# ---------------------------------------------------------------------------
# Refusal construction
# ---------------------------------------------------------------------------


def _refuse(
    *,
    key: str,
    key_kind: str,
    reason_code: str,
    evidence: dict[str, Any],
    ko: str,
    en: str,
    ja: str,
    outcome: str = REFUSAL,
    scope: str = NOT_APPLICABLE,
    also_refused: "FixProposal | None" = None,
) -> FixProposal:
    return FixProposal(
        key=key,
        key_kind=key_kind,
        outcome=outcome,
        scope=scope,
        verb=None,
        params=None,
        evidence=evidence,
        rationale_ko=ko,
        rationale_en=en,
        rationale_ja=ja,
        confidence=CONFIDENCE_NONE,
        reason_code=reason_code,
        also_refused=also_refused,
    )


def _emit(
    *,
    key: str,
    key_kind: str,
    scope: str,
    verb: str,
    params: dict[str, Any],
    evidence: dict[str, Any],
    ko: str,
    en: str,
    ja: str,
    confidence: str,
    assumptions: tuple[str, ...],
    also_refused: FixProposal | None = None,
    registry: ToolRegistry | None = None,
) -> FixProposal:
    """Build a PROPOSAL, but only after the LIVE schema accepts its params.

    A payload the verb's own pydantic model rejects is downgraded to a
    refusal here rather than shipped to an approval card that would fail at
    dispatch. This is the module's own application of "verify, not trust":
    the mapping does not get to claim its params are valid, the registry
    says so.
    """
    resolution = resolve_verb(verb, registry=registry)
    if not resolution["resolved"]:
        return _refuse(
            key=key,
            key_kind=key_kind,
            reason_code=VERB_NOT_IN_REGISTRY,
            evidence={**evidence, "verb": verb, "verb_resolution": resolution},
            ko=f"이 수정에 필요한 편집 동사 '{verb}'이(가) 현재 도구 레지스트리에 없습니다"
            f"({resolution['why']}). 존재하지 않는 동사를 제안하지 않고 거부합니다.",
            en=f"The edit verb '{verb}' this fix needs is not in the live tool registry "
            f"({resolution['why']}). Refused rather than proposing a verb that does not exist.",
            ja=f"この修正に必要な編集動詞 '{verb}' が現在のツールレジストリにありません"
            f"({resolution['why']})。存在しない動詞を提案せず拒否します。",
        )

    ok, error = validate_params(verb, params, registry=registry)
    if not ok:
        return _refuse(
            key=key,
            key_kind=key_kind,
            reason_code=PARAMS_REJECTED_BY_SCHEMA,
            evidence={**evidence, "verb": verb, "rejected_params": dict(params), "error": error},
            ko=f"'{verb}'의 실제 파라미터 스키마가 이 제안값을 거부했습니다: {error}. "
            "승인 카드로 넘기지 않고 거부합니다.",
            en=f"'{verb}''s own live params schema rejected this payload: {error}. Refused "
            "rather than sending it to an approval card.",
            ja=f"'{verb}' の実際のパラメータスキーマがこの提案値を拒否しました: {error}。"
            "承認カードに渡さず拒否します。",
        )

    return FixProposal(
        key=key,
        key_kind=key_kind,
        outcome=PROPOSAL,
        scope=scope,
        verb=verb,
        params=params,
        evidence=evidence,
        rationale_ko=ko,
        rationale_en=en,
        rationale_ja=ja,
        confidence=confidence,
        reason_code=None,
        assumptions=assumptions,
        also_refused=also_refused,
    )


# ---------------------------------------------------------------------------
# Builder (a): demand-level — set_vehicle_input_volume
# ---------------------------------------------------------------------------


def _build_demand_proposal(
    *,
    node: Mapping[str, Any],
    target: Mapping[str, Any] | None,
    node_total_geh: float,
    threshold: float,
    registry: ToolRegistry | None = None,
) -> FixProposal:
    """The node TOTAL misses — but WHICH demand, and is it ours to change?

    The value is the current volume scaled by observed/modelled — the same
    arithmetic a practitioner does by hand, stated in ``evidence["arithmetic"]``
    so the audit trail can re-check it.

    Unit D4 put a gate in front of that arithmetic (professor, 2026-08-12; the
    user's decided policy). An observed entrance volume is the calibration
    TARGET, not a knob:

    * :data:`ENTRANCE_MEASURED` — an observed row resolves to this input's own
      link — is a REFUSAL (:data:`INPUT_VOLUME_IS_OBSERVED_DATA`). No
      threshold, no override: rewriting the count to make downstream numbers
      agree inverts the direction of calibration.
    * :data:`ENTRANCE_UNVERIFIED` still proposes, because the approval card IS
      the "ask the user first" step — but the proposal is relabelled as what
      it is, a NAMED demand ESTIMATE: the assumption token
      :data:`ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED`, the lowered confidence
      :data:`CONFIDENCE_DEMAND_ESTIMATE`, the verdict in
      ``evidence["target"]["entrance_measurement"]`` and a rationale sentence
      that tells the approver to reject it and supply the real count if they
      have one. Never an unnamed guess.
    """
    key, kind = NODE_TOTAL_VOLUME_MISS, KEY_VALIDATION
    observed = _number(node.get("observed_total"))
    modelled = _number(node.get("modelled_total"))
    node_id = str(node.get("node_id") or "")
    base_evidence: dict[str, Any] = {
        "node_id": node_id,
        "observed_total": observed,
        "modelled_total": modelled,
        "node_total_geh": _round(node_total_geh),
        "geh_threshold": threshold,
        "sources": dict(node.get("sources") or {}),
    }

    if target is None:
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=NO_TARGET_OBJECT,
            evidence=base_evidence,
            ko=f"노드 {node_id}의 총 교통량이 실측과 어긋납니다(GEH {_round(node_total_geh)} ≥ "
            f"{threshold}). 다만 어떤 차량 입력(Vehicle Input)을 조정해야 하는지 지목되지 않아 "
            "구체적인 제안을 만들 수 없습니다 — 대상 없이 수요를 바꾸자는 제안은 하지 않습니다.",
            en=f"Node {node_id}'s total volume misses the observed count (GEH "
            f"{_round(node_total_geh)} >= {threshold}), but no vehicle input was named as the "
            "target, so no concrete proposal can be made — a demand change with no object to "
            "change is not proposed.",
            ja=f"ノード {node_id} の総交通量が実測と乖離しています(GEH {_round(node_total_geh)} ≥ "
            f"{threshold})。ただし調整すべき車両インプットが指定されていないため、具体的な提案は"
            "作成できません — 対象のない需要変更は提案しません。",
        )

    input_no = target.get("input_no")
    current_volume = _number(target.get("current_volume"))
    interval_no = target.get("interval_no", 1)
    entrance = entrance_measurement(node, target)
    base_evidence["target"] = {
        "input_no": input_no,
        "current_volume": current_volume,
        "interval_no": interval_no,
        # Unit D4: the verdict AND its basis ride in the evidence, so the
        # approval card and the audit DOCX (both of which render evidence
        # generically) carry "why this was, or was not, ours to edit".
        "entrance_measurement": entrance,
    }

    if input_no not in (None, "") and entrance["status"] == ENTRANCE_MEASURED:
        row = entrance["observed_row"] or {}
        # The number quoted is every counted row on that link, not just the
        # example row named beside it -- an entrance normally fans out into
        # several movements.
        counted_here = _fmt(_number(entrance.get("observed_on_input_link")))
        rows_here = len(entrance.get("observed_rows") or [])
        rows_ko = "" if rows_here <= 1 else f"(이 링크 진입 실측 행 {rows_here}건 합계) "
        rows_en = "" if rows_here <= 1 else f"(summed over {rows_here} counted rows on that link) "
        rows_ja = "" if rows_here <= 1 else f"(このリンクからの実測行 {rows_here} 件の合計) "
        # Unit #45. The tail of this refusal used to promise four levers —
        # "turn distribution, driving behaviour and desired speeds, signal
        # timing, and link geometry/lanes" — when exactly ONE of them had a
        # proposal builder. Prose making a stronger claim than the code is a
        # defect here, so the tail now separates FOUR states, measured against
        # the builders and the registry on 2026-08-18:
        #
        # * PROPOSED automatically — the turn distribution, and only it
        #   (``_build_routing_proposal`` → ``set_route_relative_flow``).
        # * PROPOSED ONCE SOMETHING IS SUPPLIED, and the sentence says what:
        #   ``saturation_flow.propose_car_following_parameters`` (unit #44)
        #   emits its three-value Table 5-6 set only when the node's rung is
        #   ``geh_ladder.CAPACITY_LIMIT`` AND a measured stop-line saturation
        #   flow was handed in; short of the measurement it REQUESTS one
        #   (``NO_SATURATION_FLOW_MEASUREMENT``) rather than proposing. Naming
        #   the three parameters rather than "driving behaviour" is deliberate:
        #   ax/bx_add/bx_mult are the whole of what is automatable, and W99's
        #   CC0-CC9 remain policy-forbidden.
        # * DIAGNOSED ONLY — signal timing. Unit #43's ladder reaches
        #   ``SIGNAL_TIMING`` and cites step 6 when the caller states the node
        #   is signalised, and there is no proposal builder behind it.
        # * MANUAL — desired speeds and link geometry/lanes. The COM verbs
        #   exist and are verified; nothing IN THIS LOOP proposes values for
        #   them. "In this loop" is load-bearing, not hedging: ``genesis_fixes``
        #   DOES emit ``set_link_lane_count`` (V4, widen a link to the lane
        #   count its connector already maps) and ``set_link_geometry`` (V7,
        #   reset a polyline to census-derived points). Those are the Genesis
        #   BUILD gate repairing a network that contradicts itself before any
        #   run exists — not a lever for closing a GEH gap, which is the only
        #   thing this sentence is about. Unqualified, "nothing proposes values
        #   for them" is simply false, and the sentence below says "in this
        #   loop" for exactly that reason.
        #
        # WHY THIS STAYS GENERAL RATHER THAN NAMING THIS NODE'S RUNG (measured,
        # #45): the rung cannot exist yet at this line. ``geh_ladder`` CONSUMES
        # ``map_node_counts``'s outcome -- this object -- and two of its five
        # rungs are defined by it, so the diagnosis is strictly downstream; it
        # also needs the run's queue/delay rows, which this module is never
        # handed (``map_node_counts`` takes ``node``/``targets``, no
        # ``run_result``). Wiring it would mean inverting an import direction
        # and widening a signature used at 30 call sites, which is a behaviour
        # change to a structural refusal this unit may not touch. The reader
        # loses nothing: ``reality_check`` puts the node's real rung in the
        # SAME entry as this refusal, under ``diagnosis``.
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=INPUT_VOLUME_IS_OBSERVED_DATA,
            evidence=base_evidence,
            ko=f"노드 {node_id}의 총 교통량이 실측과 어긋납니다(GEH {_round(node_total_geh)} ≥ "
            f"{threshold}). 그러나 이 제안이 바꾸려는 차량 입력 {input_no}은(는) 링크 "
            f"{entrance['link_no']}의 진입부이고, 그 진입부가 바로 실측이 이루어진 지점입니다: "
            f"실측 이동류 {row.get('movement_id')}이(가) 링크 {entrance['link_no']}에서 진입하며 "
            f"{rows_ko}{counted_here}대로 계측되었습니다. 실측된 진입 교통량은 "
            "보정의 '목표값'이지 조절 손잡이가 아닙니다 — 모델의 하류 수치를 맞추려고 실측값 자체를 "
            "고쳐 쓰면 보정의 방향이 뒤집히고, 이 실행을 채점하는 기준 자체가 사라집니다. 따라서 "
            "이 편집은 거부합니다. 같은 불일치를 좁히기 위해 이 루프가 자동으로 제안하는 것은 "
            "회전 배분(경로 결정의 relFlow) 하나입니다. 이 노드가 용량 한계로 진단되고 정지선 "
            "포화교통류를 측정해 주면 차량추종 3개 값(MRWA Table 5-6)까지 제안할 수 있으며, 측정이 없으면 "
            "제안 대신 측정을 요청합니다. 신호 시간은 신호 교차로라고 알려 주면 ⑥ 검토 지시까지이고, "
            "제안은 만들지 않습니다. 희망 속도와 링크 기하·차로(용량)는 이 루프에 제안 빌더가 없어 "
            "사람이 직접 편집합니다. "
            "그리고 모델에 차량이 부족한 이유가 다른 '실측되지 않은' 진입부라면, 수요 추정은 이 "
            "입력이 아니라 그쪽에서 이루어져야 합니다.",
            en=f"Node {node_id}'s total volume misses the observed count (GEH "
            f"{_round(node_total_geh)} >= {threshold}). But vehicle input {input_no}, the object "
            f"this edit would rewrite, feeds link {entrance['link_no']} — and that entrance is "
            f"where the counts were TAKEN: observed movement row {row.get('movement_id')} enters "
            f"from link {entrance['link_no']} carrying {rows_en}{counted_here} "
            "counted vehicles. An observed entrance volume is the calibration TARGET, not a knob: "
            "rewriting the measurement so the model's downstream numbers agree inverts the "
            "direction of calibration and destroys the very evidence this run is graded against. "
            "Refused. To close the same gap, this loop automatically proposes one thing: the turn "
            "distribution (a routing decision's relFlow). "
            "Given a CAPACITY diagnosis for this node and a measured "
            "stop-line saturation flow it can also propose three car-following values (MRWA Table "
            "5-6) — without that measurement it requests one instead of proposing. Signal timing "
            "reaches a step-6 review once you say the node is signalised, and stops there. Desired "
            "speeds and link geometry/lanes (capacity) have no proposal builder in this loop: "
            "hand-edited. And where the model is short of "
            "vehicles because some OTHER, unmeasured entrance is too low, that entrance is where "
            "demand may be estimated, not this one.",
            ja=f"ノード {node_id} の総交通量が実測と乖離しています(GEH {_round(node_total_geh)} ≥ "
            f"{threshold})。しかし、この編集が書き換えようとする車両インプット {input_no} はリンク "
            f"{entrance['link_no']} の流入部であり、その流入部こそ実測が行われた地点です: 実測移動 "
            f"{row.get('movement_id')} がリンク {entrance['link_no']} から流入し "
            f"{rows_ja}{counted_here} 台として計測されています。実測された流入交通量は"
            "キャリブレーションの「目標値」であってつまみではありません — モデルの下流の数値を"
            "合わせるために実測値そのものを書き換えれば、キャリブレーションの向きが反転し、この実行"
            "を採点する基準自体が失われます。よってこの編集は拒否します。同じ乖離を縮めるためにこの"
            "ループが自動提案するのは回転配分(経路決定のrelFlow)の一つだけです。このノードが容量"
            "限界と診断され、停止線の飽和交通流を測定して渡せば追従モデルの3値(MRWA Table 5-6)まで"
            "提案でき、測定がなければ提案ではなく測定を要求します。信号時間は信号交差点と指定すれば"
            "⑥の確認指示までで、提案は作りません。希望速度とリンク幾何・車線数(容量)はこのループに"
            "提案ビルダーがなく手動編集です。"
            "そして車両が不足している原因が別の「実測されて"
            "いない」流入部であるなら、需要の推定はこのインプットではなくそちらで行うべきです。",
        )

    if input_no in (None, "") or current_volume is None or not modelled or not observed:
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=EVIDENCE_INSUFFICIENT,
            evidence=base_evidence,
            ko=f"노드 {node_id}의 총 교통량 불일치는 확인되었지만(GEH {_round(node_total_geh)}), "
            "차량 입력 번호·현재 볼륨·실측/모델 총량 중 하나 이상이 없어 새 볼륨을 산술적으로 "
            "도출할 수 없습니다. 근거를 댈 수 없는 값은 제안하지 않습니다.",
            en=f"Node {node_id}'s total mismatch is established (GEH {_round(node_total_geh)}), "
            "but the input number, its current volume, or a usable observed/modelled total is "
            "missing, so no new volume can be derived arithmetically. A value whose evidence "
            "cannot be stated is not proposed.",
            ja=f"ノード {node_id} の総交通量の不一致は確認されました(GEH "
            f"{_round(node_total_geh)})が、車両インプット番号・現在のボリューム・実測/モデル総量"
            "のいずれかが欠けており、新しいボリュームを算術的に導出できません。根拠を示せない値は"
            "提案しません。",
        )

    new_volume = _round(current_volume * observed / modelled)
    arithmetic = (
        f"{_fmt(current_volume)} * {_fmt(observed)} / {_fmt(modelled)} = {_fmt(new_volume)}"
    )
    evidence = {
        **base_evidence,
        "proposed_volume": new_volume,
        "arithmetic": arithmetic,
        "run_result_keys": list((node.get("sources") or {}).get("run_result_keys") or []),
    }

    # Unit D4: the entrance survived the measured gate only as UNVERIFIED, so
    # what follows is a demand ESTIMATE and says so — in the rationale, in the
    # assumption token, and in the confidence. The estimation clause is
    # APPENDED to the unchanged proposal text rather than replacing it: the
    # arithmetic and its evidence are exactly what they were, and it is the
    # premise (that this entrance was ever ours to rewrite) that is unverified.
    estimate_ko = (
        f" ※ 수요 추정(demand estimation): 이 진입부가 실측 지점인지 확인하지 못했습니다"
        f"({_entrance_phrase(entrance, 'ko')}). 실제 진입 교통량을 아시면 이 제안을 거절하고 그 "
        "값을 넣어 주십시오 — 승인하면 위 비율로 산출한 명시적 추정치를 채택하는 것입니다."
    )
    estimate_en = (
        f" NB DEMAND ESTIMATE, not a measurement: this entrance could not be verified as a "
        f"measured one ({_entrance_phrase(entrance, 'en')}). If you know the real entrance count, "
        "REJECT this and supply it — approving adopts the ratio above as a named estimate."
    )
    estimate_ja = (
        f" ※ 需要推定(demand estimation): この流入部が実測地点かを確認できませんでした"
        f"({_entrance_phrase(entrance, 'ja')})。実際の流入交通量をご存知であれば、この提案を拒否して"
        "その値をご指定ください — 承認する場合、上記の比率による明示的な推定値を採用することに"
        "なります。"
    )

    return _emit(
        key=key,
        key_kind=kind,
        scope=DEMAND_LEVEL,
        verb="set_vehicle_input_volume",
        params={
            "input_no": str(input_no),
            "volume": new_volume,
            "interval_no": int(interval_no or 1),
        },
        evidence=evidence,
        ko=f"노드 {node_id}의 총 교통량 자체가 실측과 어긋납니다: 실측 {_fmt(observed)} vs 모델 "
        f"{_fmt(modelled)}, GEH {_round(node_total_geh)} ≥ 기준 {threshold}. 회전 배분이 아니라 "
        f"수요(Vehicle Input) 수준의 문제이므로, 차량 입력 {input_no}의 구간 {interval_no} 볼륨을 "
        f"{_fmt(current_volume)} → {new_volume} veh/h로 조정할 것을 제안합니다({arithmetic}). "
        "이 값은 해당 차량 입력이 이 노드 총량을 좌우한다는 전제 하에서 정확합니다." + estimate_ko,
        en=f"Node {node_id}'s TOTAL volume itself misses the observed count: observed "
        f"{_fmt(observed)} vs modelled {_fmt(modelled)}, GEH {_round(node_total_geh)} >= "
        f"threshold {threshold}. That is a demand-level problem (Vehicle Input), not a turn-"
        f"distribution one, so the proposal is to change vehicle input {input_no}'s interval "
        f"{interval_no} volume from {_fmt(current_volume)} to {new_volume} veh/h ({arithmetic}). "
        "The value is exact under the assumption that this input drives the node's total."
        + estimate_en,
        ja=f"ノード {node_id} の総交通量自体が実測と乖離しています: 実測 {_fmt(observed)} 対 モデル "
        f"{_fmt(modelled)}、GEH {_round(node_total_geh)} ≥ 閾値 {threshold}。これは回転配分ではなく"
        f"需要(車両インプット)レベルの問題のため、車両インプット {input_no} の区間 {interval_no} の"
        f"ボリュームを {_fmt(current_volume)} → {new_volume} veh/h に変更することを提案します"
        f"({arithmetic})。この値は当該インプットがノード総量を左右するという前提のもとで正確です。"
        + estimate_ja,
        confidence=CONFIDENCE_DEMAND_ESTIMATE,
        assumptions=(
            ASSUME_INPUT_DRIVES_NODE_TOTAL,
            ASSUME_INTERVAL_MATCHES_COUNT_PERIOD,
            ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED,
        ),
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Builder (b): routing-split level — set_route_relative_flow
# ---------------------------------------------------------------------------


def _demand_refusal_note(
    *, node_id: str, observed: float | None, modelled: float | None, geh: float, threshold: float
) -> FixProposal:
    """The approved refusal shape (roadmap row 2.1, user decision 2026-08-06),
    carried ON a routing proposal so "we did not propose a demand change" is
    recorded rather than merely implied."""
    return _refuse(
        key=NODE_TOTAL_VOLUME_MISS,
        key_kind=KEY_VALIDATION,
        reason_code=NODE_TOTAL_RECONCILES,
        evidence={
            "node_id": node_id,
            "observed_total": observed,
            "modelled_total": modelled,
            "node_total_geh": _round(geh),
            "geh_threshold": threshold,
        },
        ko=f"노드 {node_id}의 총량은 실측과 합치합니다(실측 {_fmt(observed)} vs 모델 "
        f"{_fmt(modelled)}, GEH {_round(geh)} < {threshold}). 따라서 이것은 수요 오류가 아니라 "
        "회전 배분 문제이며, 차량 입력 볼륨 변경은 제안하지 않습니다.",
        en=f"Node {node_id}'s total reconciles with the observed count (observed "
        f"{_fmt(observed)} vs modelled {_fmt(modelled)}, GEH {_round(geh)} < {threshold}). This "
        "is therefore a routing-split question, NOT a demand error, and no vehicle input volume "
        "change is proposed.",
        ja=f"ノード {node_id} の総量は実測と一致しています(実測 {_fmt(observed)} 対 モデル "
        f"{_fmt(modelled)}、GEH {_round(geh)} < {threshold})。したがってこれは需要の誤りではなく"
        "回転配分の問題であり、車両インプットのボリューム変更は提案しません。",
    )


def _total_clause(
    *, node_id: str, movement_id: str, geh: float, threshold: float, reconciles: bool
) -> dict[str, dict[str, str]]:
    """The leading clause a routing rationale opens with, in ko/en/ja.

    Two states, and the difference is load-bearing rather than cosmetic:

    * ``reconciles`` — the classic arm (b): the node total agrees with the
      count, so the split is the only thing wrong. Wording preserved verbatim
      from before unit D4, because it is what every existing consumer, test
      and audit document already carries.
    * not ``reconciles`` — D4's fall-through: the total misses too, and the
      demand that would fix it is an OBSERVED entrance count this loop refuses
      to rewrite. Saying "the total reconciles" here would be a plain false
      statement in the text an approver signs, so it says the true thing and
      points at the refusal riding on ``also_refused``.

    ``short`` opens a refusal, ``long`` opens the proposal, ``because`` is the
    mid-sentence connective before the relFlow value itself.
    """
    if reconciles:
        return {
            "short": {
                "ko": f"노드 {node_id}의 총량은 맞지만 이동류 {movement_id}의 배분이 어긋납니다",
                "en": f"Node {node_id}'s total is right but movement {movement_id}'s split misses",
                "ja": f"ノード {node_id} の総量は合っていますが、移動 {movement_id} の配分が乖離しています",
            },
            "long": {
                "ko": f"노드 {node_id}의 총량은 실측과 합치하지만(GEH {_round(geh)} < {threshold}) "
                f"이동류 {movement_id}의 배분이 어긋납니다",
                "en": f"Node {node_id}'s total reconciles with observation (GEH {_round(geh)} < "
                f"{threshold}) but movement {movement_id}'s split misses",
                "ja": f"ノード {node_id} の総量は実測と一致していますが(GEH {_round(geh)} < "
                f"{threshold})、移動 {movement_id} の配分が乖離しています",
            },
            "because": {
                "ko": "수요가 아니라 회전 배분 문제이므로",
                "en": "This is a routing-split problem, not a demand one, so",
                "ja": "需要ではなく回転配分の問題のため、",
            },
        }
    return {
        "short": {
            "ko": f"노드 {node_id}의 총량도 실측과 어긋나지만(GEH {_round(geh)} ≥ {threshold}) 그 "
            f"수요는 실측된 진입 교통량이라 편집 대상이 아니며(함께 실린 거부 참조), 이동류 "
            f"{movement_id}의 배분 또한 어긋납니다",
            "en": f"Node {node_id}'s total misses too (GEH {_round(geh)} >= {threshold}), but its "
            "demand is an OBSERVED entrance count and therefore not this loop's to edit (see the "
            f"refusal carried alongside); movement {movement_id}'s split also misses",
            "ja": f"ノード {node_id} の総量も実測と乖離していますが(GEH {_round(geh)} ≥ "
            f"{threshold})、その需要は実測された流入交通量であり編集対象ではありません(併記された"
            f"拒否を参照)。移動 {movement_id} の配分も乖離しています",
        },
        "long": {
            "ko": f"노드 {node_id}의 총량도 실측과 어긋나지만(GEH {_round(geh)} ≥ {threshold}) 그 "
            f"수요는 실측된 진입 교통량이므로 바꿀 수 없고(함께 실린 거부 참조), 이동류 "
            f"{movement_id}의 배분 또한 어긋납니다",
            "en": f"Node {node_id}'s total misses too (GEH {_round(geh)} >= {threshold}), but that "
            "demand is an OBSERVED entrance count and cannot be rewritten (see the refusal carried "
            f"alongside); movement {movement_id}'s split also misses",
            "ja": f"ノード {node_id} の総量も実測と乖離していますが(GEH {_round(geh)} ≥ "
            f"{threshold})、その需要は実測された流入交通量のため書き換えられません(併記された拒否を"
            f"参照)。移動 {movement_id} の配分も乖離しています",
        },
        "because": {
            "ko": "실측된 수요는 손댈 수 없고 남은 지렛대는 회전 배분이므로",
            "en": "The measured demand is not a knob, so the remaining lever is the turn "
            "distribution:",
            "ja": "実測された需要は調整対象ではなく、残るレバーは回転配分のため、",
        },
    }


def _decision_movement_tokens(target: Mapping[str, Any]) -> tuple[str, ...]:
    """The caller's ``decision_movements``, normalized to non-empty tokens.

    A bare string is read as ONE token rather than as its characters — a
    single-movement scope is a real (if degenerate) answer, and it lands on
    the "share is 100%, relFlow cannot express that" refusal below rather than
    on "you told us nothing".
    """
    raw = target.get("decision_movements")
    if isinstance(raw, str):
        raw = [raw]
    if raw is None or isinstance(raw, (bytes, Mapping)) or not isinstance(raw, Sequence):
        return ()
    tokens: list[str] = []
    for item in raw:
        token = str(item).strip()
        if token and token not in tokens:
            tokens.append(token)
    return tuple(tokens)


def _observed_over_decision(
    node: Mapping[str, Any], tokens: Sequence[str]
) -> tuple[float, dict[str, float], list[str]]:
    """``(total, {token: observed}, unresolved)`` for the movements a decision
    governs.

    A token is matched against each movement's ``id`` and its ``direction``
    (the workbook writes the same turn token into both, and a run-only
    movement carries only an OBJECT_ID). A token that matches nothing, or
    matches a movement with no observed count, is UNRESOLVED and named — its
    volume cannot be silently treated as zero, because a zero would inflate
    this route's share against a denominator that is missing part of the
    decision. Two tokens that resolve to the SAME movement are summed once.
    """
    movements = list(node.get("movements") or [])
    total = 0.0
    resolved: dict[str, float] = {}
    unresolved: list[str] = []
    counted: set[int] = set()
    for token in tokens:
        index = next(
            (
                position
                for position, entry in enumerate(movements)
                if str(entry.get("id") or "") == token
                or str(entry.get("direction") or "") == token
            ),
            None,
        )
        observed = None if index is None else _number(movements[index].get("observed"))
        if index is None or observed is None:
            unresolved.append(token)
            continue
        resolved[token] = observed
        if index not in counted:
            counted.add(index)
            total += observed
    return total, resolved, unresolved


def _build_routing_proposal(
    *,
    node: Mapping[str, Any],
    movement: Mapping[str, Any],
    movement_geh: float,
    node_total_geh: float,
    threshold: float,
    target: Mapping[str, Any] | None,
    demand_note: FixProposal | None = None,
    registry: ToolRegistry | None = None,
) -> FixProposal:
    """The total reconciles but one movement's SPLIT misses.

    relFlow is a RAW weight, not a percentage: Vissim derives each route's
    actual share at run time as its relFlow over the SUM of the decision's
    relFlows, and writing one route leaves siblings untouched (verified live
    — ``vissim_com.set_route_relative_flow``, and
    ``constraints.route_relative_flow_split_confirm`` says the same on the
    approval card). So the value that produces the OBSERVED share ``p`` with
    siblings unchanged is ``sibling_total * p / (1 - p)`` — not a scaling of
    the old value, which would land short of the target share.

    WHICH ``p``, and why the caller must say
    ---------------------------------------
    ``p`` is this movement's observed share **of the decision that governs
    it**, never of the node. A routing decision governs the turns off ONE
    approach as often as it governs the whole intersection, and the two
    denominators are not close: on the demo network decision 2 governs the E
    approach's three turns (471 + 100 + 107 = 678 veh), so the E-W movement's
    share is 471/678 = 69.5% — against the node total of 2542 it would read
    18.5%, and the closed form would return 43.21 instead of 432.32, an order
    of magnitude low. A relFlow that low does not "err on the safe side": it
    silently reassigns the approach's traffic to its siblings.

    Which movements a decision governs is NETWORK knowledge — the caller reads
    it from ``get_routing_decisions`` and passes it as the target's
    ``decision_movements`` — so this module does not guess it. Without that
    list the only denominator available is the node total, which is wrong
    whenever the decision is narrower than the node, and this builder REFUSES
    (:data:`EVIDENCE_INSUFFICIENT`) rather than emitting a number derived from
    it. A decision that genuinely does govern the whole node is expressed by
    passing every one of its movements, not by a flag; the emitted proposal
    then carries :data:`ASSUME_DECISION_MOVEMENTS_COMPLETE`, which is the
    assumption the list encodes.

    ``demand_note`` — unit D4's fall-through
    ---------------------------------------
    Normally this builder is reached only when the node total RECONCILES, and
    it says so: it builds :func:`_demand_refusal_note`
    (:data:`NODE_TOTAL_RECONCILES`) itself and carries it on ``also_refused``.
    D4 opened a SECOND way in — the total misses, but the demand edit was
    refused because that entrance is measured
    (:data:`INPUT_VOLUME_IS_OBSERVED_DATA`) — and there "the total reconciles"
    would be a false sentence in the one text an approver actually reads. So
    the caller passes the refusal it already made as ``demand_note``, and the
    rationale states what is really true: the total misses as well, and the
    turn distribution is the knob that remains.
    """
    key, kind = MOVEMENT_SPLIT_MISS, KEY_VALIDATION
    node_id = str(node.get("node_id") or "")
    observed_total = _number(node.get("observed_total"))
    modelled_total = _number(node.get("modelled_total"))
    observed = _number(movement.get("observed"))
    modelled = _number(movement.get("modelled"))
    movement_id = str(movement.get("id") or movement.get("direction") or "")

    total_reconciles = demand_note is None
    if demand_note is None:
        demand_note = _demand_refusal_note(
            node_id=node_id,
            observed=observed_total,
            modelled=modelled_total,
            geh=node_total_geh,
            threshold=threshold,
        )
    total_clause = _total_clause(
        node_id=node_id,
        movement_id=movement_id,
        geh=node_total_geh,
        threshold=threshold,
        reconciles=total_reconciles,
    )

    base_evidence: dict[str, Any] = {
        "node_id": node_id,
        "movement_id": movement_id,
        "direction": movement.get("direction"),
        "observed_movement": observed,
        "modelled_movement": modelled,
        "movement_geh": _round(movement_geh),
        "observed_total": observed_total,
        "modelled_total": modelled_total,
        "node_total_geh": _round(node_total_geh),
        "geh_threshold": threshold,
        "sources": dict(node.get("sources") or {}),
    }

    if target is None:
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=NO_TARGET_OBJECT,
            evidence=base_evidence,
            also_refused=demand_note,
            ko=f"{total_clause['short']['ko']}"
            f"(GEH {_round(movement_geh)} ≥ {threshold}). 다만 어느 경로 결정/경로를 조정할지 "
            "지목되지 않아 제안을 만들 수 없습니다.",
            en=f"{total_clause['short']['en']} "
            f"(GEH {_round(movement_geh)} >= {threshold}). No routing decision/route was named "
            "as the target, so no proposal can be made.",
            ja=f"{total_clause['short']['ja']}"
            f"(GEH {_round(movement_geh)} ≥ {threshold})。調整対象の経路決定/経路が指定されて"
            "いないため、提案を作成できません。",
        )

    decision_no = target.get("decision_no")
    route_no = target.get("route_no")
    interval_no = target.get("interval_no", 1)
    current_rel_flow = _number(target.get("current_rel_flow"))
    sibling_total = _number(target.get("sibling_rel_flow_total"))
    scope_tokens = _decision_movement_tokens(target)
    base_evidence["target"] = {
        "decision_no": decision_no,
        "route_no": route_no,
        "interval_no": interval_no,
        "current_rel_flow": current_rel_flow,
        "sibling_rel_flow_total": sibling_total,
        "decision_movements": list(scope_tokens) or None,
    }

    if decision_no in (None, "") or route_no in (None, "") or sibling_total is None:
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=EVIDENCE_INSUFFICIENT,
            evidence=base_evidence,
            also_refused=demand_note,
            ko=f"이동류 {movement_id}의 배분 불일치는 확인되었지만(GEH {_round(movement_geh)}), "
            "경로 결정 번호·경로 번호·형제 경로 relFlow 합계 중 하나 이상이 없어 "
            "새 relFlow를 산술적으로 도출할 수 없습니다.",
            en=f"Movement {movement_id}'s split mismatch is established (GEH "
            f"{_round(movement_geh)}), but the decision number, the route number, or the sibling "
            "relFlow total is missing, so no new relFlow can be derived.",
            ja=f"移動 {movement_id} の配分の不一致は確認されました(GEH {_round(movement_geh)})が、"
            "経路決定番号・経路番号・兄弟経路のrelFlow合計のいずれかが欠けており、"
            "新しいrelFlowを算術的に導出できません。",
        )

    # -- the DENOMINATOR the observed share is taken against ----------------
    # Without the decision's own movement list there is no defensible one.
    # The node total is the only other candidate and it is wrong whenever the
    # decision governs less than the whole node -- which is the normal case --
    # so this refuses instead of shipping a number that understates the route.
    if not scope_tokens:
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=EVIDENCE_INSUFFICIENT,
            evidence=base_evidence,
            also_refused=demand_note,
            ko=f"이동류 {movement_id}의 배분 불일치는 확인되었지만(GEH {_round(movement_geh)}), "
            f"경로 결정 {decision_no}이(가) 어떤 이동류들을 관장하는지 알 수 없습니다 — "
            "get_routing_decisions에서 얻은 decision_movements를 함께 주십시오. 그 목록이 없으면 "
            "실측 배분율을 노드 전체 총량에 대해서만 계산할 수 있고, 그러면 이 경로의 배분율이 "
            "과소평가되어 relFlow가 실제 필요한 값보다 훨씬 작게 산출됩니다.",
            en=f"Movement {movement_id}'s split mismatch is established (GEH "
            f"{_round(movement_geh)}), but what is missing is which movements decision "
            f"{decision_no} governs — supply decision_movements from get_routing_decisions; "
            "without it the observed share can only be taken against the whole node total, "
            "which would understate this route's share and yield a relFlow far below the one "
            "the counts actually support.",
            ja=f"移動 {movement_id} の配分の不一致は確認されました(GEH {_round(movement_geh)})が、"
            f"経路決定 {decision_no} がどの移動を管轄しているかが不明です — "
            "get_routing_decisions から得た decision_movements を渡してください。それがないと"
            "実測配分率はノード総量に対してしか計算できず、この経路の配分率を過小評価して"
            "実際に必要な値よりはるかに小さいrelFlowを導いてしまいます。",
        )

    decision_total, resolved, unresolved = _observed_over_decision(node, scope_tokens)
    # Rounded ONCE, here, so the evidence's denominator, the arithmetic string
    # and the proposed value can never disagree (see :func:`_round`).
    decision_total = _round(decision_total)
    in_scope = any(
        str(movement.get("id") or "") == token or str(movement.get("direction") or "") == token
        for token in scope_tokens
    )
    base_evidence["decision_movements"] = list(scope_tokens)
    base_evidence["observed_by_decision_movement"] = dict(resolved)
    base_evidence["observed_decision_total"] = decision_total
    base_evidence["movement_in_decision_scope"] = in_scope
    base_evidence["unresolved_decision_movements"] = list(unresolved)

    if unresolved or not in_scope or decision_total <= 0 or observed is None:
        problems: dict[str, list[str]] = {"ko": [], "en": [], "ja": []}
        if unresolved:
            named = ", ".join(unresolved)
            problems["ko"].append(f"실측에서 찾을 수 없는 이동류 토큰: {named}")
            problems["en"].append(f"movement tokens with no observed count on this node: {named}")
            problems["ja"].append(f"実測に見つからない移動トークン: {named}")
        if not in_scope or observed is None:
            problems["ko"].append(f"이동류 {movement_id}이(가) 그 목록에 없거나 실측값이 없습니다")
            problems["en"].append(
                f"movement {movement_id} is not in that list, or carries no observed count"
            )
            problems["ja"].append(f"移動 {movement_id} がその一覧にないか、実測値がありません")
        if decision_total <= 0:
            problems["ko"].append("그 이동류들의 실측 합계가 0입니다")
            problems["en"].append("those movements' observed volumes sum to 0")
            problems["ja"].append("それらの移動の実測合計が0です")
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=EVIDENCE_INSUFFICIENT,
            evidence=base_evidence,
            also_refused=demand_note,
            ko=f"경로 결정 {decision_no}의 관장 이동류 목록(decision_movements)이 이 노드의 실측과 "
            f"맞지 않아 실측 배분율의 분모를 세울 수 없습니다: {'; '.join(problems['ko'])}. "
            "분모가 불완전한 채로 계산한 relFlow는 배분율을 왜곡하므로 제안하지 않습니다.",
            en=f"Decision {decision_no}'s stated scope (decision_movements) does not line up with "
            f"this node's observed counts, so the observed share has no denominator: "
            f"{'; '.join(problems['en'])}. A relFlow derived from an incomplete denominator "
            "distorts the split, so none is proposed.",
            ja=f"経路決定 {decision_no} の管轄移動一覧(decision_movements)がこのノードの実測と"
            f"整合しないため、実測配分率の分母を立てられません: {'; '.join(problems['ja'])}。"
            "分母が不完全なまま算出したrelFlowは配分を歪めるため、提案しません。",
        )

    share = observed / decision_total
    base_evidence["observed_share"] = round(share, 6)
    base_evidence["observed_share_scope"] = "decision_movements"

    if not (0.0 < share < 1.0):
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=FIX_CANNOT_HELP,
            evidence=base_evidence,
            also_refused=demand_note,
            ko=f"경로 결정 {decision_no}이(가) 관장하는 이동류 실측 합계 {_fmt(decision_total)}대 "
            f"기준으로 이 이동류의 실측 배분율이 {round(share * 100, 1)}%입니다. relFlow 가중치로는 "
            "0%나 100%를 표현할 수 없고(0은 이동류 자체를 제거하는 별개의 결정, 100%는 형제 경로의 "
            "제거), 이는 relFlow 한 값의 조정으로 다룰 문제가 아닙니다.",
            en=f"Against decision {decision_no}'s own observed total ({_fmt(decision_total)} veh "
            f"across the movements it governs) this movement's observed share is "
            f"{round(share * 100, 1)}%. A relFlow weight cannot express 0% or 100% (0 removes the "
            "movement entirely — a different decision — and 100% means removing the sibling "
            "routes), so this is not a single-relFlow adjustment.",
            ja=f"経路決定 {decision_no} が管轄する移動の実測合計 {_fmt(decision_total)} 台に対し、"
            f"この移動の実測配分率は {round(share * 100, 1)}% です。relFlowの重みでは0%や100%を"
            "表現できず(0は移動そのものの削除という別の判断、100%は兄弟経路の削除)、relFlow1値の"
            "調整で扱う問題ではありません。",
        )

    if sibling_total <= 0:
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=FIX_CANNOT_HELP,
            evidence=base_evidence,
            also_refused=demand_note,
            ko=f"경로 결정 {decision_no}의 다른 경로 relFlow 합계가 {_fmt(sibling_total)}입니다 — "
            "이 경로가 사실상 유일하므로 relFlow를 어떤 값으로 바꿔도 실제 배분은 항상 100%이며, "
            "이 불일치는 relFlow로 고칠 수 없습니다.",
            en=f"Decision {decision_no}'s other routes carry a relFlow total of "
            f"{_fmt(sibling_total)} — this route is effectively the only one, so its actual share "
            "is always 100% whatever relFlow is written, and this mismatch cannot be fixed with "
            "relFlow.",
            ja=f"経路決定 {decision_no} の他経路のrelFlow合計は {_fmt(sibling_total)} です — この"
            "経路が実質唯一のため、relFlowをどの値にしても実配分は常に100%であり、この不一致は"
            "relFlowでは修正できません。",
        )

    new_rel_flow = _round(sibling_total * share / (1.0 - share))
    arithmetic = (
        f"{_fmt(sibling_total)} * ({_fmt(observed)}/{_fmt(decision_total)}) / "
        f"(1 - {_fmt(observed)}/{_fmt(decision_total)}) = {_fmt(new_rel_flow)}"
    )
    movement_list = ", ".join(scope_tokens)
    evidence = {
        **base_evidence,
        "proposed_rel_flow": new_rel_flow,
        "arithmetic": arithmetic,
        "resulting_share_pct": round(new_rel_flow / (sibling_total + new_rel_flow) * 100, 1),
        "run_result_keys": list((node.get("sources") or {}).get("run_result_keys") or []),
    }

    share_pct = round(share * 100, 1)
    return _emit(
        key=key,
        key_kind=kind,
        scope=ROUTING_SPLIT_LEVEL,
        verb="set_route_relative_flow",
        params={
            "decision_no": str(decision_no),
            "route_no": str(route_no),
            "rel_flow": new_rel_flow,
            "interval_no": int(interval_no or 1),
        },
        evidence=evidence,
        ko=f"{total_clause['long']['ko']}"
        f": 실측 {_fmt(observed)} vs 모델 {_fmt(modelled)}, "
        f"GEH {_round(movement_geh)} ≥ {threshold}. {total_clause['because']['ko']} 경로 결정 "
        f"{decision_no}의 경로 {route_no}(구간 {interval_no}) relFlow를 {_fmt(current_rel_flow)} → "
        f"{new_rel_flow}로 조정할 것을 제안합니다({arithmetic}). relFlow는 백분율이 아니라 원시 "
        f"가중치이며, 형제 경로가 현재 값을 유지하면 이 경로의 실제 배분은 {share_pct}%가 됩니다 — "
        f"이는 노드 전체가 아니라 경로 결정 {decision_no}이(가) 관장하는 이동류 "
        f"{movement_list}의 실측 합계 {_fmt(decision_total)}대 중 이 이동류의 {_fmt(observed)}대가 "
        f"차지하는 비율입니다.",
        en=f"{total_clause['long']['en']}: observed {_fmt(observed)} vs "
        f"modelled {_fmt(modelled)}, GEH {_round(movement_geh)} >= {threshold}. "
        f"{total_clause['because']['en']} the proposal is to change decision "
        f"{decision_no} route {route_no} (interval {interval_no}) relFlow from "
        f"{_fmt(current_rel_flow)} to {new_rel_flow} ({arithmetic}). relFlow is a RAW weight, not "
        f"a percentage: with the sibling routes left at their current values this route's actual "
        f"share becomes {share_pct}% — the share observed WITHIN decision {decision_no}, i.e. "
        f"{_fmt(observed)} of the {_fmt(decision_total)} vehicles counted across the movements it "
        f"governs ({movement_list}), not its share of the node total.",
        ja=f"{total_clause['long']['ja']}: 実測 {_fmt(observed)} 対 モデル "
        f"{_fmt(modelled)}、GEH {_round(movement_geh)} ≥ {threshold}。{total_clause['because']['ja']}"
        f"経路決定 {decision_no} の経路 {route_no}(区間 {interval_no})のrelFlowを "
        f"{_fmt(current_rel_flow)} → {new_rel_flow} に変更することを提案します({arithmetic})。"
        f"relFlowは百分率ではなく生の重みであり、兄弟経路が現在値を維持すれば、この経路の実配分は "
        f"{share_pct}% になります — これはノード全体ではなく、経路決定 {decision_no} が管轄する移動"
        f"({movement_list})の実測合計 {_fmt(decision_total)} 台に対する、この移動の "
        f"{_fmt(observed)} 台の比率です。",
        confidence=CONFIDENCE_ARITHMETIC,
        assumptions=(
            ASSUME_SIBLING_ROUTES_UNCHANGED,
            ASSUME_DECISION_MOVEMENTS_COMPLETE,
            ASSUME_INTERVAL_MATCHES_COUNT_PERIOD,
        ),
        also_refused=demand_note,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Builder: latent_demand_overflow — reads the constraint's OWN ceiling
# ---------------------------------------------------------------------------


def _build_latent_demand_overflow_proposal(
    *,
    finding: constraints.Finding | Mapping[str, Any] | None,
    action: Mapping[str, Any] | None,
    registry: ToolRegistry | None = None,
) -> FixProposal:
    """``latent_demand_overflow`` already computed the defensible number.

    This is the one-way read of the ``suggested_fix`` channel described in
    the module docstring: the constraint's own
    ``suggested_fix["max_recommended_volume"]`` IS the ceiling, computed
    against the link's capacity and its sibling inputs' volumes, and this
    mapping does not re-derive it. When the constraint says the overflow is
    ``fixable_by_reducing_this_input: False``, no volume for THIS input can
    help, and the mapping refuses instead of proposing one that would fail
    the very check it came from.

    Unit D4, and the honest statement of where its predicate reaches
    ----------------------------------------------------------------
    This is the mapping's OTHER ``set_vehicle_input_volume`` proposal, so it
    is subject to the same doctrine: an observed entrance volume is data to
    match, not a knob. :func:`entrance_measurement` is therefore called here
    too — but a ``latent_demand_overflow`` finding carries NO observed counts
    at all (its evidence is the link's capacity, the sibling inputs' volumes
    and the requested value; a preflight check has no counts workbook and no
    run behind it), so today the verdict is ALWAYS
    :data:`ENTRANCE_UNVERIFIED` / :data:`ENTRANCE_NO_MOVEMENT_ROWS`. That is
    stated rather than pretended: the call is real so this builder gains the
    refusal for free the day a caller does hand it observed evidence, and
    until then every proposal from here is labelled as the demand ESTIMATE it
    is (:data:`ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED`, and the rationale
    sentence that tells the approver to reject it and supply the real count).

    Note what this means for a MEASURED entrance that overflows its link: the
    right answer is not to shave the count down to capacity, it is more lanes
    or a capacity that reflects reality — and the rationale says so, so an
    approver holding real counts is told which side to fix. The confidence
    stays :data:`CONFIDENCE_CONSTRAINT_CEILING`: that field says how the
    NUMBER was obtained (the constraint computed it), and that has not
    changed; the estimation caveat is about the premise, and it rides in the
    assumption token, the rationale and the evidence.
    """
    key, kind = "latent_demand_overflow", KEY_CONSTRAINT
    suggested_fix, details = _finding_parts(finding)
    params = dict((action or {}).get("params") or {})
    input_no = params.get("input_no") or details.get("input_no")
    interval_no = params.get("interval_no", details.get("interval_no", 1))
    entrance = entrance_measurement(
        # No node evidence exists on this path -- see the docstring. The link
        # IS known (the constraint resolved it), so the verdict's own basis
        # names the missing half rather than the missing target.
        None,
        {"input_no": input_no, "link_no": details.get("link_id")},
    )
    evidence: dict[str, Any] = {
        "constraint_id": key,
        "input_no": input_no,
        "interval_no": interval_no,
        "entrance_measurement": entrance,
        "suggested_fix": dict(suggested_fix) if isinstance(suggested_fix, Mapping) else suggested_fix,
        "details": {
            k: details.get(k)
            for k in ("capacity", "link_id", "lanes", "sibling_volume_total", "link_total_volume")
            if k in details
        },
        "suggested_fix_source": "constraints.latent_demand_overflow (read, never written)",
    }

    if not isinstance(suggested_fix, Mapping):
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=EVIDENCE_INSUFFICIENT,
            evidence=evidence,
            ko="이 경고에 제약이 계산한 권장 상한(suggested_fix)이 실려 있지 않아 제안할 볼륨을 "
            "산출할 수 없습니다.",
            en="This warning carries no constraint-computed ceiling (suggested_fix), so no "
            "volume can be proposed.",
            ja="この警告に制約が算出した推奨上限(suggested_fix)が含まれていないため、提案する"
            "ボリュームを算出できません。",
        )

    ceiling = _number(suggested_fix.get("max_recommended_volume"))
    fixable = suggested_fix.get("fixable_by_reducing_this_input", True)

    if fixable is False or ceiling is None:
        excess = suggested_fix.get("excess_from_siblings_alone")
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=FIX_CANNOT_HELP,
            evidence=evidence,
            ko=f"같은 링크의 다른 차량 입력들만으로도 이미 용량을 "
            f"{_fmt(_number(excess))} veh/h 초과합니다 — 이 입력 '{input_no}'을(를) 0까지 줄여도 "
            "초과는 해소되지 않으므로, 이 동사로는 고칠 수 없습니다. 다른 차량 입력의 볼륨을 "
            "줄이거나 링크 차로 수를 늘려야 합니다.",
            en=f"The other vehicle inputs on this link ALONE already exceed capacity by "
            f"{_fmt(_number(excess))} veh/h — reducing input '{input_no}' even to 0 cannot clear "
            "it, so this verb cannot fix it. Either the other inputs' volumes must come down or "
            "the link needs more lanes.",
            ja=f"同じリンクの他の車両インプットだけで既に容量を {_fmt(_number(excess))} veh/h "
            f"超過しています — このインプット '{input_no}' を0まで減らしても解消しないため、この"
            "動詞では修正できません。他のインプットのボリュームを減らすか、リンクの車線数を"
            "増やす必要があります。",
        )

    if input_no in (None, ""):
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=NO_TARGET_OBJECT,
            evidence=evidence,
            ko="조정할 차량 입력 번호를 알 수 없어 제안을 만들 수 없습니다.",
            en="The vehicle input number to change is unknown, so no proposal can be made.",
            ja="変更対象の車両インプット番号が不明なため、提案を作成できません。",
        )

    # Unit D4's gate on the mapping's OTHER demand-level verb. Unreachable
    # today (see the docstring: this finding carries no observed counts), and
    # deliberately here anyway -- the day this builder is handed observed
    # evidence, a measured entrance must refuse here exactly as it does on the
    # node-total path, rather than needing somebody to remember to add it.
    if entrance["status"] == ENTRANCE_MEASURED:
        row = entrance["observed_row"] or {}
        return _refuse(
            key=key,
            key_kind=kind,
            reason_code=INPUT_VOLUME_IS_OBSERVED_DATA,
            evidence=evidence,
            ko=f"차량 입력 {input_no}의 볼륨이 링크 {entrance['link_no']}의 용량을 넘습니다. 그러나 "
            f"이 진입부는 실측이 이루어진 지점입니다(실측 이동류 {row.get('movement_id')}). 실측된 "
            "진입 교통량을 용량에 맞춰 깎는 것은 데이터를 모델에 맞추는 일이며, 보정의 방향이 "
            "뒤집힙니다 — 실측 수요가 용량을 넘는다면 고쳐야 할 쪽은 용량(차로 수·기하)이거나 "
            "같은 링크의 다른 입력이지, 실측값이 아닙니다.",
            en=f"Vehicle input {input_no}'s volume exceeds link {entrance['link_no']}'s capacity. "
            f"But this entrance is where counts were taken (observed movement row "
            f"{row.get('movement_id')}). Shaving an OBSERVED entrance volume down to capacity "
            "fits the data to the model and inverts the direction of calibration — if the "
            "measured demand exceeds capacity, what needs fixing is the capacity (lanes, "
            "geometry) or the other inputs on that link, not the measurement.",
            ja=f"車両インプット {input_no} のボリュームがリンク {entrance['link_no']} の容量を"
            f"超えています。しかしこの流入部は実測が行われた地点です(実測移動 "
            f"{row.get('movement_id')})。実測された流入交通量を容量に合わせて削ることはデータを"
            "モデルに合わせる行為であり、キャリブレーションの向きが反転します — 実測需要が容量を"
            "超えるなら、直すべきは容量(車線数・幾何)か同じリンクの他のインプットであって、実測値"
            "ではありません。",
        )

    return _emit(
        key=key,
        key_kind=kind,
        scope=DEMAND_LEVEL,
        verb="set_vehicle_input_volume",
        params={
            "input_no": str(input_no),
            "volume": ceiling,
            "interval_no": int(interval_no or 1),
        },
        evidence={**evidence, "proposed_volume": ceiling},
        ko=f"차량 입력 {input_no}의 구간 {interval_no} 볼륨이 링크 용량을 넘어 잠재 수요를 "
        f"발생시킵니다. 제약이 직접 계산한 권장 상한 {ceiling} veh/h를 그대로 제안합니다"
        "(이 값은 이 제약이 링크 용량과 형제 입력 볼륨으로 산출한 것이며, 여기서 다시 "
        "계산하지 않습니다)."
        f" 주의 — 이것은 수요 추정(demand estimation)입니다: 이 진입부의 볼륨이 실측값인지 확인할 "
        f"수 없었습니다({_entrance_phrase(entrance, 'ko')}). 이 값이 실측 교통량이라면 이 제안을 "
        "거절하십시오 — 실측 수요가 용량을 넘는 상황에서 고쳐야 할 쪽은 용량(차로 수·기하)이지 "
        "실측값이 아닙니다.",
        en=f"Vehicle input {input_no}'s interval {interval_no} volume exceeds the link's capacity "
        f"and creates latent demand. The proposal is the ceiling the constraint itself computed, "
        f"{ceiling} veh/h — derived there from the link capacity and the sibling inputs' volumes, "
        "and not re-derived here."
        f" NOTE — this is a DEMAND ESTIMATE: this entrance's volume could not be verified as "
        f"measured ({_entrance_phrase(entrance, 'en')}). If that volume IS an observed count, "
        "REJECT this proposal — when measured demand exceeds capacity, what needs fixing is the "
        "capacity (lanes, geometry), not the measurement.",
        ja=f"車両インプット {input_no} の区間 {interval_no} のボリュームがリンク容量を超え、潜在"
        f"需要を生じさせています。制約自身が算出した推奨上限 {ceiling} veh/h をそのまま提案します"
        "(この値は当該制約がリンク容量と兄弟インプットのボリュームから算出したもので、ここでは"
        "再計算しません)。"
        f" 注意 — これは需要推定(demand estimation)です: この流入部のボリュームが実測値であるかを"
        f"確認できませんでした({_entrance_phrase(entrance, 'ja')})。その値が実測交通量である場合は"
        "この提案を拒否してください — 実測需要が容量を超える場合に直すべきは容量(車線数・幾何)で"
        "あって実測値ではありません。",
        # The NUMBER is still the constraint's own computed ceiling, which is
        # what this field reports; the estimation caveat is about the premise
        # and rides in the assumption token, the rationale and the evidence.
        confidence=CONFIDENCE_CONSTRAINT_CEILING,
        assumptions=(
            ASSUME_INTERVAL_MATCHES_COUNT_PERIOD,
            ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED,
        ),
        registry=registry,
    )


def _finding_parts(
    finding: constraints.Finding | Mapping[str, Any] | None,
) -> tuple[Any, dict[str, Any]]:
    """``(suggested_fix, details)`` from either a real ``Finding`` or its
    dict form (``anomalies._finding_to_dict``'s output, which is what
    survives a JSON boundary into row 2.1's loop)."""
    if finding is None:
        return None, {}
    if isinstance(finding, constraints.Finding):
        return finding.suggested_fix, dict(finding.details or {})
    if isinstance(finding, Mapping):
        return finding.get("suggested_fix"), dict(finding.get("details") or {})
    return None, {}


# ---------------------------------------------------------------------------
# The three-outcome branch — row 2.1's entry point for count validation
# ---------------------------------------------------------------------------


def map_node_counts(
    node: Mapping[str, Any],
    *,
    targets: Mapping[str, Any] | None = None,
    geh_threshold: float = validation.GEH_GOOD_LT,
    registry: ToolRegistry | None = None,
) -> FixProposal:
    """One node's observed vs modelled evidence → proposal or refusal.

    ``node`` is :func:`node_evidence_from_real_counts`'s shape.
    ``targets`` names the objects an edit could touch::

        {"vehicle_input": {"input_no": "7", "current_volume": 800.0,
                           "interval_no": 1},
         "routes": {"<movement id or direction>": {
                        "decision_no": "3", "route_no": "2",
                        "current_rel_flow": 42.0,
                        "sibling_rel_flow_total": 58.0,
                        "decision_movements": ["m1", "m2"],
                        "interval_no": 1}}}

    Both come from the caller (the shadow model / routing summary knows them;
    this module does not read a network). Their absence is a refusal, never a
    guess.

    ``decision_movements`` is the routing target's SCOPE: every workbook
    movement token the named decision governs (``get_routing_decisions``
    answers it), and the observed share is taken against those movements'
    counted volumes. A decision that governs the whole node passes every one
    of the node's movements; a decision that governs one approach passes that
    approach's turns. Omitting it is a refusal — see
    :func:`_build_routing_proposal` for the measured reason.

    The branch, in order, and each arm is one of the three graded outcomes:

    (a) node-total GEH >= threshold → ``set_vehicle_input_volume`` —
        **unless** unit D4's gate refuses it because that entrance is where
        the counts were taken (:data:`INPUT_VOLUME_IS_OBSERVED_DATA`), in
        which case the refusal is HELD and grading continues into (b);
    (b) some movement GEH >= threshold → ``set_route_relative_flow``,
        carrying the demand-level refusal it displaced (either
        :data:`NODE_TOTAL_RECONCILES` or D4's);
    (c) no movement misses → the held D4 refusal if there is one, else
        REFUSAL :data:`MODEL_REPRODUCES_OBSERVED`, rationale naming the GEH
        values and the threshold.

    Before D4, arm (a) returned unconditionally on a node-total miss, so a
    node whose total missed never reached movement grading. That was harmless
    while the demand edit was always available; with a measured entrance it
    would have collapsed "the demand is not ours to change" into "nothing is",
    which is false — routing, behaviour, speeds and signals remain.
    """
    targets = targets or {}
    node_id = str(node.get("node_id") or "")
    observed_total = _number(node.get("observed_total"))
    modelled_total = _number(node.get("modelled_total"))
    total_geh = validation.geh(modelled_total, observed_total)

    if total_geh is None:
        return _refuse(
            key=COUNT_VALIDATION,
            key_kind=KEY_VALIDATION,
            reason_code=EVIDENCE_INSUFFICIENT,
            evidence={
                "node_id": node_id,
                "observed_total": observed_total,
                "modelled_total": modelled_total,
                "node_total_geh": None,
                "geh_threshold": geh_threshold,
                "sources": dict(node.get("sources") or {}),
            },
            ko=f"노드 {node_id}의 GEH가 정의되지 않습니다(실측 {_fmt(observed_total)}, 모델 "
            f"{_fmt(modelled_total)}). 정의되지 않은 지표는 '양호'가 아니라 판단 불가이며, "
            "근거 없이는 어떤 수정도 제안하지 않습니다.",
            en=f"Node {node_id}'s GEH is undefined (observed {_fmt(observed_total)}, modelled "
            f"{_fmt(modelled_total)}). An undefined statistic is not a pass — it is no evidence, "
            "and no fix is proposed without evidence.",
            ja=f"ノード {node_id} のGEHが定義されません(実測 {_fmt(observed_total)}、モデル "
            f"{_fmt(modelled_total)})。定義されない指標は「良好」ではなく判断不可であり、根拠"
            "なしにいかなる修正も提案しません。",
        )

    # -- (a) the node TOTAL itself misses ----------------------------------
    demand_refusal: FixProposal | None = None
    if total_geh >= geh_threshold:
        demand = _build_demand_proposal(
            node=node,
            target=targets.get("vehicle_input"),
            node_total_geh=total_geh,
            threshold=geh_threshold,
            registry=registry,
        )
        if demand.reason_code != INPUT_VOLUME_IS_OBSERVED_DATA:
            return demand
        # Unit D4: the demand edit was refused because that entrance IS the
        # observed data. Before D4 this branch returned unconditionally, so a
        # node-total miss never reached movement grading at all -- and on a
        # measured entrance that made "no demand edit" mean "no edit", which
        # is the wrong lesson: the split, the behaviour and the signals are
        # still ours to change. So the refusal is held and the grading below
        # runs; it rides on the routing proposal's ``also_refused`` (the
        # NODE_TOTAL_RECONCILES precedent) or stands alone if nothing else
        # can be proposed.
        demand_refusal = demand

    graded, ungradable = _grade_movements(node)
    worst = graded[0] if graded else None

    # -- (b) a movement SPLIT misses ---------------------------------------
    if worst is not None and worst["geh"] >= geh_threshold:
        movement = worst["movement"]
        route_targets = targets.get("routes") or {}
        target = route_targets.get(str(movement.get("id") or "")) or route_targets.get(
            str(movement.get("direction") or "")
        )
        return _build_routing_proposal(
            node=node,
            movement=movement,
            movement_geh=worst["geh"],
            node_total_geh=total_geh,
            threshold=geh_threshold,
            target=target,
            demand_note=demand_refusal,
            registry=registry,
        )

    if demand_refusal is not None:
        # The total misses, the demand is measured, and no movement split
        # misses either: the D4 refusal is the whole answer and stands alone.
        # Arm (c) below must NOT be reached -- its rationale says the total
        # reconciles, which here it does not.
        return demand_refusal

    # -- (c) the model already reproduces observed behaviour ---------------
    worst_text = "-" if worst is None else f"{_round(worst['geh'])}"
    ungradable_note_ko = (
        "" if not ungradable else f" (GEH를 계산할 수 없는 이동류 {len(ungradable)}건은 이 판단에서 "
        "제외했습니다.)"
    )
    ungradable_note_en = (
        "" if not ungradable else f" ({len(ungradable)} movement(s) whose GEH is undefined were "
        "excluded from this judgement.)"
    )
    ungradable_note_ja = (
        "" if not ungradable else f" (GEHを計算できない移動 {len(ungradable)} 件はこの判断から"
        "除外しました。)"
    )
    return _refuse(
        key=COUNT_VALIDATION,
        key_kind=KEY_VALIDATION,
        reason_code=MODEL_REPRODUCES_OBSERVED,
        evidence={
            "node_id": node_id,
            "observed_total": observed_total,
            "modelled_total": modelled_total,
            "node_total_geh": _round(total_geh),
            "worst_movement_geh": None if worst is None else _round(worst["geh"]),
            "worst_movement_id": None if worst is None else worst["movement"].get("id"),
            "geh_threshold": geh_threshold,
            "graded_movement_n": len(graded),
            "ungradable_movement_n": len(ungradable),
            "sources": dict(node.get("sources") or {}),
        },
        ko=f"노드 {node_id}의 총량 GEH는 {_round(total_geh)}로 기준 {geh_threshold} 미만이고, "
        f"이동류별 최대 GEH도 {worst_text}로 기준 {geh_threshold} 미만입니다 — 모델이 이미 실제 "
        f"거동을 재현하고 있습니다. 수요 오류도 회전 배분 오류도 아니므로 어떤 파라미터 변경도 "
        f"제안하지 않습니다.{ungradable_note_ko}",
        en=f"Node {node_id}'s total GEH is {_round(total_geh)}, below the {geh_threshold} "
        f"threshold, and its worst per-movement GEH is {worst_text}, also below {geh_threshold} — "
        "the model is already reproducing observed behaviour. This is neither a demand error nor "
        f"a routing-split error, so no parameter change is proposed.{ungradable_note_en}",
        ja=f"ノード {node_id} の総量GEHは {_round(total_geh)} で閾値 {geh_threshold} 未満、移動別"
        f"の最大GEHも {worst_text} で閾値 {geh_threshold} 未満です — モデルは既に実際の挙動を"
        f"再現しています。需要の誤りでも回転配分の誤りでもないため、いかなるパラメータ変更も"
        f"提案しません。{ungradable_note_ja}",
    )


def _grade_movements(
    node: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[Mapping[str, Any]]]:
    """``(graded, ungradable)``: movements with a defined GEH, worst first.

    Mirrors ``assistant_router.build_base_validation_summary``'s worst-
    movement selection (each node's colour is driven by its WORST single-
    movement GEH, not the node-total GEH). A movement whose GEH is undefined
    is NOT graded 'good' -- it is reported separately so the refusal's own
    rationale can disclose how many it could not judge.
    """
    graded: list[dict[str, Any]] = []
    ungradable: list[Mapping[str, Any]] = []
    for movement in node.get("movements") or []:
        value = validation.geh(_number(movement.get("modelled")), _number(movement.get("observed")))
        if value is None:
            ungradable.append(movement)
            continue
        graded.append({"geh": value, "movement": movement})
    graded.sort(key=lambda entry: entry["geh"], reverse=True)
    return graded, ungradable


# ---------------------------------------------------------------------------
# Registered builders — ONE signature, so ``propose_fix`` has exactly one
# call shape and a new entry cannot invent a private calling convention.
# Extra kwargs a given builder does not need are accepted and ignored.
# ---------------------------------------------------------------------------


def _builder_count_validation(
    *,
    node: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    geh_threshold: float = validation.GEH_GOOD_LT,
    registry: ToolRegistry | None = None,
    **_ignored: Any,
) -> FixProposal:
    """Both count-validation keys resolve through the SAME three-outcome
    branch. Asking for one of them is asking "map this node"; which of the
    three arms answers is the evidence's call, not the caller's — so the
    returned proposal's own ``key`` may differ from the key requested."""
    if node is None:
        return _refuse(
            key=COUNT_VALIDATION,
            key_kind=KEY_VALIDATION,
            reason_code=EVIDENCE_INSUFFICIENT,
            evidence={"key": COUNT_VALIDATION},
            ko="노드의 실측/모델 증거 없이는 어떤 수정도 제안할 수 없습니다.",
            en="No fix can be proposed without the node's observed/modelled evidence.",
            ja="ノードの実測/モデルのエビデンスなしにいかなる修正も提案できません。",
        )
    return map_node_counts(node, targets=targets, geh_threshold=geh_threshold, registry=registry)


def _builder_latent_demand_overflow(
    *,
    finding: constraints.Finding | Mapping[str, Any] | None = None,
    action: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
    **_ignored: Any,
) -> FixProposal:
    return _build_latent_demand_overflow_proposal(
        finding=finding, action=action, registry=registry
    )


# ---------------------------------------------------------------------------
# The generic entry point — key → proposal or refusal
# ---------------------------------------------------------------------------


def propose_fix(
    key: str,
    *,
    finding: constraints.Finding | Mapping[str, Any] | None = None,
    action: Mapping[str, Any] | None = None,
    node: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    geh_threshold: float = validation.GEH_GOOD_LT,
    registry: ToolRegistry | None = None,
) -> FixProposal:
    """The mapping's single addressable entry point: one key in, one
    :class:`FixProposal` out — proposal, refusal, or stated gap.

    ``key`` is a constraint id, an anomaly id, or one of this module's
    count-validation keys. An unregistered key does not raise and does not
    return ``None``: it comes back as :data:`NO_AUTOMATED_FIX` /
    :data:`NO_MAPPING_REGISTERED`, so a caller renders one shape and the gap
    is visible instead of being an exception somebody catches.
    """
    if key == COUNT_VALIDATION:
        # The CHECK, not a fix entry (there is no single verb for "count
        # validation"), so it has no registry row to look up -- it delegates
        # straight to the three-outcome branch, whose arms carry their own
        # keys.
        return _builder_count_validation(
            node=node, targets=targets, geh_threshold=geh_threshold, registry=registry
        )

    mapping = _REGISTRY.get(key)
    if mapping is None:
        return _refuse(
            key=key,
            key_kind=KEY_CONSTRAINT,
            outcome=NO_AUTOMATED_FIX,
            reason_code=NO_MAPPING_REGISTERED,
            evidence={"key": key},
            ko=f"'{key}'에 대해 등록된 수정 매핑이 없습니다 — 이 발견은 아직 자동 수정 대상이 "
            "아닙니다.",
            en=f"No fix mapping is registered for '{key}' — this finding is not yet something "
            "this system knows how to fix automatically.",
            ja=f"'{key}' に対して登録された修正マッピングがありません — この検出はまだ自動修正の"
            "対象ではありません。",
        )

    status, detail = effective_status(mapping, registry=registry)

    if status == VERB_UNREGISTERED:
        return _refuse(
            key=key,
            key_kind=mapping.key_kind,
            reason_code=VERB_NOT_IN_REGISTRY,
            evidence={"key": key, **detail},
            ko=f"'{key}'의 수정 동사 '{mapping.verb}'이(가) 현재 도구 레지스트리에 없습니다 — "
            "매핑이 실제 도구와 어긋났습니다. 존재하지 않는 동사를 제안하지 않습니다.",
            en=f"'{key}''s fix verb '{mapping.verb}' is not in the live tool registry — the "
            "mapping has drifted from the tools. A verb that does not exist is not proposed.",
            ja=f"'{key}' の修正動詞 '{mapping.verb}' が現在のツールレジストリにありません — "
            "マッピングが実際のツールと乖離しています。存在しない動詞は提案しません。",
        )

    if mapping.status == NOT_AUTOMATABLE:
        return _no_automated_fix(mapping, status=status, detail=detail)

    assert mapping.builder is not None  # register_fix guarantees this
    return mapping.builder(
        key=mapping.key,
        finding=finding,
        action=action,
        node=node,
        targets=targets,
        geh_threshold=geh_threshold,
        registry=registry,
    )


def propose_for_finding(
    finding: constraints.Finding | Mapping[str, Any],
    *,
    action: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> FixProposal:
    """``propose_fix`` keyed off a guardrail finding's own constraint id.

    The shape row 2.1's loop already has in hand from
    ``GuardrailEngine.preflight``/``.postrun`` (or from
    ``anomalies.index_postrun_findings``'s dict form).
    """
    if isinstance(finding, constraints.Finding):
        key = finding.constraint_id
    else:
        key = str(finding.get("constraint_id") or "")
    return propose_fix(key, finding=finding, action=action, registry=registry)


def _no_automated_fix(
    mapping: FixMapping, *, status: str, detail: Mapping[str, Any]
) -> FixProposal:
    verbatim = mapping.reason_verbatim()
    evidence: dict[str, Any] = {
        "key": mapping.key,
        "status": status,
        "reason_source": mapping.reason_source,
        "reason_verbatim": verbatim,
        **dict(detail),
    }
    suffix = f" [{verbatim}]" if verbatim else ""
    stale_ko = (
        ""
        if status != ANOMALY_ARMING_DRIFTED
        else f" 주의: 이 판단은 U3 카탈로그의 arming '{mapping.expected_anomaly_arming}'을 전제로 "
        f"하는데 현재 값은 '{detail.get('anomaly_arming')}'입니다 — 이 항목의 주장이 낡았습니다."
    )
    stale_en = (
        ""
        if status != ANOMALY_ARMING_DRIFTED
        else f" NOTE: this claim is predicated on U3's arming "
        f"'{mapping.expected_anomaly_arming}' but the catalogue now reports "
        f"'{detail.get('anomaly_arming')}' — this entry's claim is stale."
    )
    stale_ja = (
        ""
        if status != ANOMALY_ARMING_DRIFTED
        else f" 注意: この判断はU3カタログのarming '{mapping.expected_anomaly_arming}' を前提と"
        f"していますが、現在の値は '{detail.get('anomaly_arming')}' です — この項目の主張は"
        "古くなっています。"
    )
    return FixProposal(
        key=mapping.key,
        key_kind=mapping.key_kind,
        outcome=NO_AUTOMATED_FIX,
        scope=NOT_APPLICABLE,
        verb=None,
        params=None,
        evidence=evidence,
        rationale_ko=mapping.reason_ko + suffix + stale_ko,
        rationale_en=mapping.reason_en + suffix + stale_en,
        rationale_ja=mapping.reason_ja + suffix + stale_ja,
        confidence=CONFIDENCE_NONE,
        reason_code=mapping.reason_code,
    )


# ---------------------------------------------------------------------------
# The catalogue itself
# ---------------------------------------------------------------------------


def _register_catalogue() -> None:
    """Idempotent (guarded by ``_registered``): registering twice raises on
    the duplicate key. Called at import time, mirroring ``anomalies.py``'s
    ``_ensure_registered`` pattern."""

    # -- (a) node total misses -> demand ------------------------------------
    register_fix(
        FixMapping(
            key=NODE_TOTAL_VOLUME_MISS,
            key_kind=KEY_VALIDATION,
            status=AUTOMATABLE,
            scope=DEMAND_LEVEL,
            verb="set_vehicle_input_volume",
            builder=_builder_count_validation,
            param_fields=("input_no", "volume", "interval_no"),
            evidence_fields=("total_real", "total_sim_basam", "movement_volumes"),
            label_ko="노드 총 교통량 불일치",
            label_en="Node total volume miss",
            label_ja="ノード総交通量の不一致",
            reason_ko="노드 총량 자체가 실측과 어긋나면 회전 배분이 아니라 수요가 원인입니다 — "
            "해당 접근로의 Vehicle Input 볼륨(veh/h)이 조정 대상 파라미터입니다. 단(D4), 그 "
            "진입부에서 실측이 이루어졌다면 그 볼륨은 '맞춰야 할 데이터'이지 손잡이가 아니므로 "
            "편집을 거부하고, 실측 여부를 확인할 수 없는 진입부의 제안은 '수요 추정'으로 명시해 "
            "승인자에게 되묻습니다.",
            reason_en="When the node TOTAL itself misses, the cause is demand, not turn "
            "distribution — the approach's Vehicle Input volume (veh/h) is the parameter to "
            "adjust. With one structural exception (D4): where the counts were taken AT that "
            "entrance, its volume is data to match rather than a knob, so the edit is refused; "
            "and where measured-ness cannot be verified, the proposal is labelled a demand "
            "ESTIMATE and put back to the approver.",
            reason_ja="ノード総量自体が実測と乖離する場合、原因は回転配分ではなく需要です — "
            "当該流入部のVehicle Inputのボリューム(veh/h)が調整対象のパラメータです。ただし(D4)、"
            "その流入部で実測が行われている場合、そのボリュームは合わせるべきデータであってつまみ"
            "ではないため編集を拒否し、実測かどうかを確認できない場合は提案を「需要推定」と明示して"
            "承認者に問い返します。",
        )
    )

    # -- (b) split misses with a reconciling total -> routing ---------------
    register_fix(
        FixMapping(
            key=MOVEMENT_SPLIT_MISS,
            key_kind=KEY_VALIDATION,
            status=AUTOMATABLE,
            scope=ROUTING_SPLIT_LEVEL,
            verb="set_route_relative_flow",
            builder=_builder_count_validation,
            param_fields=("decision_no", "route_no", "rel_flow", "interval_no"),
            evidence_fields=("total_real", "total_sim_basam", "real", "sim_basam",
                             "movement_volumes"),
            label_ko="이동류 배분 불일치 (총량은 합치)",
            label_en="Movement split miss (total reconciles)",
            label_ja="移動配分の不一致(総量は一致)",
            reason_ko="노드 총량이 실측과 합치하는데 개별 이동류가 어긋나면 수요가 아니라 회전 "
            "배분이 원인입니다 — Static Vehicle Routing Decision의 경로 relFlow(원시 가중치)가 "
            "조정 대상 파라미터입니다.",
            reason_en="When the node total reconciles but an individual movement misses, the "
            "cause is the turn distribution, not demand — a Static Vehicle Routing Decision "
            "route's relFlow (a raw weight) is the parameter to adjust.",
            reason_ja="ノード総量が実測と一致するのに個別の移動が乖離する場合、原因は需要ではなく"
            "回転配分です — Static Vehicle Routing Decisionの経路relFlow(生の重み)が調整対象の"
            "パラメータです。",
        )
    )

    # -- latent_demand_overflow: the constraint computed the number ---------
    register_fix(
        FixMapping(
            key="latent_demand_overflow",
            key_kind=KEY_CONSTRAINT,
            status=AUTOMATABLE,
            scope=DEMAND_LEVEL,
            verb="set_vehicle_input_volume",
            builder=_builder_latent_demand_overflow,
            param_fields=("input_no", "volume", "interval_no"),
            evidence_fields=("suggested_fix.max_recommended_volume", "capacity",
                             "sibling_volume_total", "link_total_volume"),
            label_ko="잠재 수요 초과",
            label_en="Latent demand overflow",
            label_ja="潜在需要の超過",
            reason_ko="요청 볼륨이 링크 용량을 넘어 잠재 수요를 만듭니다. 이 제약은 권장 상한을 "
            "직접 계산해 suggested_fix에 실어 주므로, 이 매핑은 그 값을 읽어 쓸 뿐 다시 계산하지 "
            "않습니다.",
            reason_en="The requested volume exceeds the link's capacity and creates latent "
            "demand. This constraint computes the recommended ceiling itself and carries it in "
            "suggested_fix, so this mapping READS that value rather than re-deriving it.",
            reason_ja="要求ボリュームがリンク容量を超え、潜在需要を生みます。この制約は推奨上限を"
            "自ら算出しsuggested_fixに載せるため、このマッピングはその値を読むだけで再計算は"
            "しません。",
            reason_source="constraints.py, _check_latent_demand_overflow's suggested_fix",
        )
    )

    # -- ghost_queue: ARMED, but no registered verb reaches the lever -------
    register_fix(
        FixMapping(
            key="ghost_queue",
            key_kind=KEY_ANOMALY,
            status=NOT_AUTOMATABLE,
            scope=NOT_APPLICABLE,
            reason_code=NO_REGISTERED_VERB,
            expected_anomaly_arming=anomalies.ARMED,
            evidence_fields=("queue_results", "queue_definition", "delay_segments"),
            label_ko="유령 대기열",
            label_en="Ghost queue",
            label_ja="幽霊待ち行列",
            reason_ko="자동 수정 없음. 이 이상 징후를 만드는 두 지렛대는 네트워크 파라미터가 아니라 "
            "평가 설정입니다: 대기행렬 정의(속도 임계값/카운터 배치)와 지체 측정 구간 길이. 현재 "
            "출하된 write 동사 중 어느 것도 이 둘에 닿지 않습니다 — add_queue_counter/"
            "remove_queue_counter는 카운터를 놓을 뿐 정의를 바꾸지 않습니다. 대기열 수치가 틀린 게 "
            "아니라 '무엇을 센 수치인지'가 모델 간에 다른 것이므로, 값을 조정하는 수정은 오히려 "
            "잘못된 값을 만듭니다.",
            reason_en="No automated fix. The two levers behind this anomaly are evaluation "
            "SETTINGS, not network parameters: the queue definition (speed thresholds / counter "
            "placement) and the delay-measurement segment length. No shipped write verb reaches "
            "either — add_queue_counter/remove_queue_counter place a counter, they do not change "
            "the definition. The queue NUMBER is not wrong; what it counts differs between "
            "models, so 'adjusting' it would manufacture a wrong number.",
            reason_ja="自動修正なし。この異常の背後にある2つのレバーはネットワークパラメータでは"
            "なく評価設定です: 待ち行列の定義(速度閾値/カウンタ配置)と遅れ測定区間長。出荷済みの"
            "write動詞のいずれもこれらに到達しません — add_queue_counter/remove_queue_counterは"
            "カウンタを置くだけで定義は変えません。待ち行列の数値が誤っているのではなく、何を"
            "数えた数値かがモデル間で異なるため、値の調整はむしろ誤った値を作ります。",
        )
    )

    # -- blocked_connector: verbs exist, evidence cannot discriminate -------
    register_fix(
        FixMapping(
            key="blocked_connector",
            key_kind=KEY_ANOMALY,
            status=NOT_AUTOMATABLE,
            scope=NOT_APPLICABLE,
            reason_code=EVIDENCE_CANNOT_DISCRIMINATE,
            expected_anomaly_arming=anomalies.ARMED,
            evidence_fields=("los_by_movement", "movement_volumes"),
            label_ko="차단된 커넥터",
            label_en="Blocked connector",
            label_ja="遮断されたコネクタ",
            reason_ko="자동 수정 없음. 통행량 0 + LOS A라는 신호는 최소 세 가지 원인과 양립합니다: "
            "(1) 경로의 relFlow가 0, (2) 접근↔유출 커넥터 자체가 없음, (3) 상류 정체로 차량이 "
            "도달하지 못함. 결과 DB에는 커넥터 상태 열이 없어(U3 카탈로그가 이를 대리 지표라고 "
            "명시) 셋을 구분할 수 없고, add_connector가 필요로 하는 기하 파라미터(차로 대응, "
            "스플라인)는 실행 증거에서 도출되지 않습니다. 원인을 특정하지 못한 채 relFlow를 "
            "올리는 제안은 근거를 넘어선 주장입니다.",
            reason_en="No automated fix. The signature (zero throughput graded LOS A) is "
            "consistent with at least three causes: (1) the route's relFlow is 0, (2) the "
            "approach-to-exit connector does not exist, (3) upstream congestion means no vehicle "
            "arrives. The result database carries no connector-state column to tell them apart "
            "(U3's catalogue names this a proxy, not a direct observation), and the geometric "
            "parameters add_connector needs (lane correspondence, spline) are not derivable from "
            "run evidence. Raising a relFlow without identifying the cause would claim more than "
            "the evidence supports.",
            reason_ja="自動修正なし。通行量0かつLOS Aという兆候は少なくとも3つの原因と両立します: "
            "(1)経路のrelFlowが0、(2)流入↔流出コネクタ自体が存在しない、(3)上流の渋滞で車両が"
            "到達しない。結果DBにコネクタ状態の列はなく(U3カタログはこれを代理指標と明記)3者を"
            "区別できず、add_connectorが必要とする幾何パラメータ(車線対応、スプライン)は実行"
            "エビデンスから導出できません。原因を特定せずrelFlowを上げる提案は、根拠を超えた主張"
            "です。",
            reason_source="services/agent/anomalies.py, blocked_connector's own proxy caveat",
        )
    )

    # -- starved_link: nothing detects it yet, so nothing to fix from -------
    register_fix(
        FixMapping(
            key="starved_link",
            key_kind=KEY_ANOMALY,
            status=NOT_AUTOMATABLE,
            scope=NOT_APPLICABLE,
            reason_code=NO_DETECTOR_YET,
            expected_anomaly_arming=anomalies.NOT_YET_ARMED,
            evidence_fields=(),
            label_ko="기아 링크",
            label_en="Starved link",
            label_ja="飢餓リンク",
            reason_ko="자동 수정 없음 — 아직 이 이상 징후를 감지하는 제약이 없어(U3: "
            "NOT_YET_ARMED) 수정을 걸 발견 자체가 없습니다. 연구 공백이 아니라 예정된 배관 "
            "작업이며, 링크 평가가 무장되면 이 항목을 다시 판단해야 합니다.",
            reason_en="No automated fix — no constraint detects this anomaly yet (U3: "
            "NOT_YET_ARMED), so there is no finding to attach a fix to. A scheduled plumbing "
            "unit, not a research gap: this entry must be revisited once link evaluation is "
            "armed.",
            reason_ja="自動修正なし — この異常を検出する制約がまだ存在しないため(U3: "
            "NOT_YET_ARMED)、修正を紐づける検出自体がありません。研究上の空白ではなく予定された"
            "配管作業であり、リンク評価が武装されればこの項目は再判断が必要です。",
            reason_source="services/agent/anomalies.py, starved_link's measured arming",
        )
    )

    # -- latent_demand_diffusion: NOT_MEASURABLE, reason ATTACHED -----------
    register_fix(
        FixMapping(
            key="latent_demand_diffusion",
            key_kind=KEY_ANOMALY,
            status=NOT_AUTOMATABLE,
            scope=NOT_APPLICABLE,
            reason_code=NOT_MEASURABLE,
            expected_anomaly_arming=anomalies.NOT_MEASURABLE,
            evidence_fields=("latent_demand_vehicles", "deleted_vehicles"),
            label_ko="확산/잠재 수요 (>1%)",
            label_en="Diffusion / latent demand (>1%)",
            label_ja="拡散/潜在需要 (>1%)",
            reason_ko="자동 수정 없음 — 이 시스템이 읽을 수 있는 증거로는 관측 자체가 불가능합니다"
            "(U3: NOT_MEASURABLE). 측정할 수 없는 것은 수정 후 확인도 할 수 없으므로, 여기서 "
            "제안하는 어떤 편집도 검증 불가능한 편집이 됩니다. 검증된 사유는 아래에 그대로 "
            "첨부합니다(재서술하지 않음).",
            reason_en="No automated fix — this is not observable at all from the evidence this "
            "system can read (U3: NOT_MEASURABLE). What cannot be measured cannot be verified "
            "after an edit either, so any edit proposed here would be an unverifiable edit. The "
            "verified reason is attached verbatim below rather than restated.",
            reason_ja="自動修正なし — このシステムが読み取れるエビデンスでは観測自体が不可能です"
            "(U3: NOT_MEASURABLE)。測定できないものは修正後の確認もできないため、ここで提案する"
            "編集はすべて検証不能な編集になります。検証済みの理由は再記述せず、そのまま添付します。",
            reason_source="services/agent/tools_run.py module docstring, via "
            "anomalies.unobtainable_from_result_db_reason()",
            reason_verbatim_fn=anomalies.unobtainable_from_result_db_reason,
        )
    )


_registered = False


def _ensure_registered() -> None:
    global _registered
    if not _registered:
        _register_catalogue()
        _registered = True


_ensure_registered()


# ---------------------------------------------------------------------------
# Report helper — one call for row 2.1's loop and row 3.7's renderer
# ---------------------------------------------------------------------------


def build_fix_report(
    *,
    node: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    findings: Sequence[constraints.Finding | Mapping[str, Any]] = (),
    geh_threshold: float = validation.GEH_GOOD_LT,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Everything this unit can say about one run, in one payload.

    Designed for U5 (reality check) and row 3.7 (active diagnosis) to consume
    without rework: the count-validation verdict for a node (if one was
    given), one entry per finding, the live coverage table, and the catalogue's
    own edges (anomalies with no entry here at all).
    """
    node_outcome = (
        None
        if node is None
        else map_node_counts(
            node, targets=targets, geh_threshold=geh_threshold, registry=registry
        ).to_dict()
    )
    per_finding = [
        propose_for_finding(finding, registry=registry).to_dict() for finding in findings
    ]
    return {
        "mapped_at": datetime.now(timezone.utc).isoformat(),
        "roadmap_rows": ["2.1", "3.7"],
        "geh_threshold": geh_threshold,
        "node_outcome": node_outcome,
        "finding_outcomes": per_finding,
        "coverage": get_fix_coverage(registry=registry),
        "unmapped_anomalies": [dict(entry) for entry in get_unmapped_anomalies()],
    }


__all__ = [
    "ANOMALY_ARMING_DRIFTED",
    "ASSUME_DECISION_MOVEMENTS_COMPLETE",
    "ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED",
    "ASSUME_INPUT_DRIVES_NODE_TOTAL",
    "ASSUME_INTERVAL_MATCHES_COUNT_PERIOD",
    "ASSUME_SIBLING_ROUTES_UNCHANGED",
    "AUTOMATABLE",
    "CAR_FOLLOWING_LEVEL",
    "CONFIDENCE_ARITHMETIC",
    "CONFIDENCE_CONSTRAINT_CEILING",
    "CONFIDENCE_DEMAND_ESTIMATE",
    "CONFIDENCE_NONE",
    "COUNT_VALIDATION",
    "DEMAND_LEVEL",
    "ENTRANCE_MEASURED",
    "ENTRANCE_NO_LINK_KEYED_ROWS",
    "ENTRANCE_NO_LINK_ON_TARGET",
    "ENTRANCE_NO_MOVEMENT_ROWS",
    "ENTRANCE_NO_ROW_ON_INPUT_LINK",
    "ENTRANCE_ROW_HAS_NO_OBSERVED",
    "ENTRANCE_ROW_ON_INPUT_LINK",
    "ENTRANCE_STATUSES",
    "ENTRANCE_UNVERIFIED",
    "EVIDENCE_CANNOT_DISCRIMINATE",
    "EVIDENCE_INSUFFICIENT",
    "FIX_CANNOT_HELP",
    "FixMapping",
    "FixProposal",
    "INPUT_VOLUME_IS_OBSERVED_DATA",
    "KEY_ANOMALY",
    "KEY_CONSTRAINT",
    "KEY_VALIDATION",
    "MODEL_REPRODUCES_OBSERVED",
    "MOVEMENT_SPLIT_MISS",
    "NODE_TOTAL_RECONCILES",
    "NODE_TOTAL_VOLUME_MISS",
    "NOT_APPLICABLE",
    "NOT_AUTOMATABLE",
    "NOT_MEASURABLE",
    "NO_AUTOMATED_FIX",
    "NO_DETECTOR_YET",
    "NO_MAPPING_REGISTERED",
    "NO_REGISTERED_VERB",
    "NO_TARGET_OBJECT",
    "OUTCOMES",
    "PARAMS_REJECTED_BY_SCHEMA",
    "PROPOSAL",
    "REFUSAL",
    "ROUTING_SPLIT_LEVEL",
    "SCOPES",
    "STATUSES",
    "VERB_NOT_IN_REGISTRY",
    "VERB_UNREGISTERED",
    "build_fix_report",
    "canonical_link_id",
    "effective_status",
    "entrance_measurement",
    "get_fix",
    "get_fix_coverage",
    "get_fix_registry",
    "get_unmapped_anomalies",
    "map_node_counts",
    "merge_modelled_from_run_result",
    "modelled_from_run_result",
    "movement_from_link",
    "movement_to_link",
    "node_evidence_from_real_counts",
    "preflight_findings",
    "propose_fix",
    "propose_for_finding",
    "register_fix",
    "resolve_verb",
    "validate_params",
]

"""Unit #51 — the RANGE-BOUNDED parameter search, and everything it refuses first.

Where this sits, and why it is last
-----------------------------------
#43 (``geh_ladder``) diagnoses a GEH miss and names the authority's next step.
#44 (``saturation_flow``) turns a MEASURED stop-line saturation flow into a Table 5-6
row — a lookup, never a hunch. #50 (``calibration_rail``) shows the thirteen §2.12.1
steps and left step ⑫'s second track LOCKED with its preconditions named. This module
is that track.

It is the LAST resort on purpose, and the guideline says so twice in the same
paragraph this module's ranges come from:

* op §5.2.5.1, p.188 — "The factors should be determined based on driving behaviours
  observed on-site." (``mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE``)
* op §2.12.1, p.33 — "change one parameter at a time"
  (``mrwa_reference.CALIBRATION_PRINCIPLE_ONE_PARAMETER_CITATION``)

So a search never starts because a number looks off. It starts only when the ladder is
MEASURED exhausted (:func:`measure_exhaustion`), and only after a precondition gate that
refuses BY NAME and says what would open it (:func:`evaluate_preconditions`).

================================================================================
WHAT THIS UNIT MEASURED BEFORE IT CHOSE ANYTHING
================================================================================

1. The search space, from the write verbs that actually exist
------------------------------------------------------------
A parameter is a candidate only if BOTH hold: a write verb exists for it, and the
guideline publishes a RANGE for it. Measured 2026-08-19 against
``vissim_com.VissimController.DRIVING_BEHAVIOR_PARAM_FIELDS`` (the one whitelist
``set_driving_behavior_parameter`` honours) and against
``docs/product/reference/mrwa-section5-extract.md``:

======================================  =========  ==========  =====================
parameter                               write verb  published   verdict
                                                    range
======================================  =========  ==========  =====================
``ax`` / ``bx_add`` / ``bx_mult``       yes        Table 5-6   EXCLUDED — a MEASURED
                                                               path exists (#44). A
                                                               search would replace
                                                               a measurement with a
                                                               guess.
``saf_dist_fact_ln_chg``                yes        0.25-0.45   CANDIDATE
``lat_dist_stand_def``                  yes        0.1-0.4 m   CANDIDATE
``lat_dist_driv_def``                   yes        0.2-1.0 m   CANDIDATE
``coop_ln_chg_speed_diff``              yes        none        EXCLUDED — no band
``min_speed_for_lat``                   yes        none        EXCLUDED — no band
max decel, cooperative braking          **no**     -9 to -5    EXCLUDED — published
                                                               but not applicable
Table 5-9 critical gap                  yes        per-row     EXCLUDED — see below
                                        (priority   DEFAULTS,
                                        rules)      not bands
Table 5-10 front / rear gap             **no**     0.5-1.0 s   EXCLUDED — the only
                                                   2.0-4.0 s   conflict-area write
                                                               verb is
                                                               ``set_conflict_area_
                                                               status`` (an enum),
                                                               so these seconds
                                                               cannot be applied at
                                                               all
======================================  =========  ==========  =====================

The two §5 tables the task brief asked to MEASURE rather than assume both came back
negative, for different reasons, and both are named rather than quietly omitted
(:data:`EXCLUSIONS`):

* **Table 5-10 (conflict areas)** publishes real bands but this codebase has no verb
  that writes them. ``set_conflict_area_status`` sets ``Status`` — one of PASSIVE /
  AHASRIGHTOFWAY / BHASRIGHTOFWAY / UNDETERMINED — and nothing in ``vissim_com``
  touches a front or rear gap. Searching a parameter that cannot be applied would
  produce a recommendation with no way to act on it.
* **Table 5-9 (gap acceptance)** has a write verb (``set_priority_rule_gap_headway``'s
  ``gap_time_s``) but publishes a single DEFAULT per movement type, not a band — and
  ``constraints.py``'s own priority-rule check records that a rule's movement type
  cannot be inferred from the shadow model *today, always*, so even the per-row default
  cannot be attached to a specific rule. ``table_5_9_critical_gap_envelope()``'s (4, 5)
  spans DIFFERENT movement types; treating it as one rule's tolerance band would be
  this module inventing an authority claim.

Net: a THREE-dimensional space, every dimension carrying the authority's own page.

2. The holdout, from the data that actually exists
--------------------------------------------------
The user's decision pinned the PRINCIPLE ("never validate on the data you calibrated
on" — op §2.12, p.33) and left the METHOD to be measured. Measured:

* The observed side's grain is ``(period, node, movement)``: the base counts workbook's
  Counts sheet carries SimRun / TimeInt / Period / Movement / Direction / Vehs(All) /
  Observed rows plus a per-node Total row (``tools/make_demo_data.py``), and a stored
  observation set carries ``aggregates[period][site]`` joined to nodes by a
  caller-supplied map (``services.observations.observed_side``).
* The scored unit is the NODE: ``reality_check`` grades ``node_total_geh`` per node, and
  ``geh_ladder`` diagnoses per node.
* The live consultant scene is 6 nodes with a FLAT demand profile — one demand, one
  period actually runnable.

That rules out the split a modeller would reach for first. **A period holdout (calibrate
on AM, validate on PM) is not available here**: flat demand means there is no second
demand profile to run, and the demo fixture's own PM observed values are derived from
the same single modelled run as its AM ones (``make_demo_data._observed_counts_by_period``
says so in its own docstring — "the demo ships ONE run"). Calling that a holdout would be
validating against a transform of the calibration data.

**Decision: a NODE holdout, plus a mandatory unseen-seed re-run of the winner.** The node
split is the only split on this data that yields observed counts the optimizer genuinely
never saw. The unseen seed is not a second holdout — it defends a different failure (a
vector that only wins on the seed it was tuned with) — and both are required
(:func:`accept_winner`).

Its limits, stated rather than papered over:

* With 6 nodes the split is 4 / 2 (:data:`HOLDOUT_STRIDE`), so the holdout side is TWO
  nodes. Two nodes is a weak test and the rationale says so out loud.
* Fewer than :data:`MIN_NODES_FOR_HOLDOUT` graded nodes and there is no honest split at
  all: :data:`HOLDOUT_NOT_AVAILABLE` refuses rather than reusing a calibration node.
  The 2-node demo fixture lands here, correctly.
* The searched parameters live on a driving behaviour that is shared network-wide, so a
  held-out node is not causally isolated from the calibration nodes — the split
  withholds the DATA, which is what op §2.12 asks for, not the physics.

The structural half matters more than the prose: :class:`_Objective` is constructed with
the calibration node ids ONLY, the search loop can reach no other scorer, and
:func:`score_holdout` is called exactly once per accepted winner, after the loop has
ended. A test pins that the two id sets are disjoint and that no ledger entry from a
search stage carries a holdout node.

3. The optimizer, from the simulation budget
--------------------------------------------
Budget: the user's decision is a WALL-CLOCK ceiling, default 3600 s
(:data:`DEFAULT_BUDGET_S`), with stop-and-resume — not a run count. At the ~30 s per run
this scene's runs cost, that is roughly 120 evaluations. The choice was made against
that number:

**Deterministic compass (pattern) search, not a GA.** Recorded reasons, in the order
they decided it:

1. *The guideline*. op §2.12.1 p.33 says "change one parameter at a time". A compass
   search literally is that — every step moves exactly one coordinate, and every ledger
   row reads "moved ``lat_dist_driv_def`` by +0.2 m, mean GEH 6.31 → 5.84". A GA moves
   all coordinates at once by construction and can produce no such sentence.
2. *The budget*. A GA needs population x generations; 120 evaluations buys about 6
   generations at a population of 20, which is far too few for selection to do anything —
   at that scale a GA is random search with extra machinery.
3. *The dimension*. After screening this space is 2-3 parameters. Pattern search is at
   home there.
4. *Reproducibility*. The optimizer draws no random numbers at all. The only RNG in the
   unit is the screening LHS (seeded, recorded) and the simulation seed (pinned,
   recorded), so a ledger replays exactly.

Stage 1 is an LHS screen whose job is not to optimize but to produce the SENSITIVITY
evidence the rationale has to cite; its selection rule states itself
(:data:`SENSITIVITY_EFFECT_FLOOR`, :data:`SENSITIVITY_BINS`). Those two names are the
rule — the earlier rho-based pair is gone, and so is the drafting in which this
paragraph and ``__all__`` still named it.

Stdlib only — ``random``, ``math``, ``statistics``. No numpy, no scipy, no GA framework.
Spearman's rho is fifteen lines here and would otherwise be a dependency.

4. The runner is a SEAM, and this unit does not drive Vissim
------------------------------------------------------------
A candidate is ``(parameter vector, seed) -> GEH per node``. Everything between is behind
:class:`SearchRunner`. :class:`SyntheticRunner` is what the offline tests use. **No live
COM runner ships in this unit** — CLAUDE.md's job-queue rule means a real run belongs to
the job queue on the user's own machine, and a half-wired COM path here would be exactly
the false capability claim this repository keeps catching. :func:`runner_requirement`
states, in one place, what a real runner must provide, so the gap is a specification
rather than a silence.

5. The guardrail collision this unit does NOT resolve
-----------------------------------------------------
Every searched parameter is written with ``set_driving_behavior_parameter``, and every
W74 behaviour in a real consultant model is referenced at least once — so
``constraints._check_driving_behavior_shared_object_edit`` refuses the write. #44-b's
narrow exception does not help: a :class:`~services.agent.car_following_grant.
CarFollowingGrant` covers ``ax``/``bx_add``/``bx_mult`` at Table 5-6's own values and
nothing else, by signature, on purpose.

So a search proposal on a shared behaviour WILL be refused at preflight. This module does
not hide that, does not route around it, and does not pick the localization (duplicate
the behaviour) that would technically pass — duplicating a network-wide behaviour changes
what the model means, which is a modeller's decision, not a search's.
:func:`preflight_forecast` reports the refusal BEFORE the human is asked, by name, with
the two resolutions the pattern in ``docs/phase2/DECISIONS.md`` (2026-08-18 / 2026-08-19)
established: widen the exception, or localize — both human calls.

Pure and stdlib-only: no I/O, no COM, no state module, no clock of its own (the clock is
injected). The engine returns a serializable state dict; persisting it is the caller's.
"""

from __future__ import annotations

import math
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from services import validation
from services.agent import fixes, geh_ladder, mrwa_reference, saturation_flow

# ---------------------------------------------------------------------------
# 1. The search space
# ---------------------------------------------------------------------------

#: The tool every candidate would be applied with. One verb, so the guardrail
#: forecast has exactly one thing to reason about.
SEARCH_TOOL = "set_driving_behavior_parameter"

#: Parameters with a write verb AND a published MRWA range. Ordered, so a
#: screening plan built from the same seed is the same plan.
#:
#: NOT a hardcoded list of bounds — the bounds are read from
#: ``mrwa_reference.merging_behaviour_range`` at build time (see
#: :func:`search_space`), so this tuple names WHICH parameters are in play and
#: the reference pack remains the only place a number lives.
SEARCHABLE_PARAMETERS: tuple[str, ...] = (
    "saf_dist_fact_ln_chg",
    "lat_dist_driv_def",
    "lat_dist_stand_def",
)

#: Why each measured-and-rejected parameter is not in the space. Data, not
#: prose, so the rationale can print it and a test can assert the space and the
#: exclusions together account for everything considered.
EXCLUDED_MEASURED_PATH = "MEASURED_PATH_EXISTS"
EXCLUDED_NO_PUBLISHED_RANGE = "NO_PUBLISHED_RANGE"
EXCLUDED_NO_WRITE_VERB = "NO_WRITE_VERB"
EXCLUDED_NO_PER_INSTANCE_RANGE = "NO_PER_INSTANCE_RANGE"

EXCLUSION_CODES = (
    EXCLUDED_MEASURED_PATH,
    EXCLUDED_NO_PUBLISHED_RANGE,
    EXCLUDED_NO_WRITE_VERB,
    EXCLUDED_NO_PER_INSTANCE_RANGE,
)

#: ``(parameter, code, ko, en, ja)``. Every row here was MEASURED, not assumed —
#: see this module's docstring table for what was read to establish each one.
EXCLUSIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "ax / bx_add / bx_mult",
        EXCLUDED_MEASURED_PATH,
        "Table 5-6 측정 경로(#44)가 이미 있습니다. 측정할 수 있는 값을 탐색으로 대체하지 않습니다.",
        "A measured path already exists (Table 5-6, unit #44). A value that can be measured is "
        "never replaced by a search.",
        "Table 5-6の測定経路(#44)が既にあります。測定できる値を探索で置き換えません。",
    ),
    (
        "coop_ln_chg_speed_diff",
        EXCLUDED_NO_PUBLISHED_RANGE,
        "지침이 이 파라미터의 범위를 공표하지 않습니다. 범위 없이는 후보가 될 수 없습니다.",
        "The guideline publishes no range for this parameter. Without a published band it cannot "
        "be a candidate.",
        "指針がこのパラメータの範囲を公表していません。範囲がなければ候補になりません。",
    ),
    (
        "min_speed_for_lat",
        EXCLUDED_NO_PUBLISHED_RANGE,
        "지침이 이 파라미터의 범위를 공표하지 않습니다. 범위 없이는 후보가 될 수 없습니다.",
        "The guideline publishes no range for this parameter. Without a published band it cannot "
        "be a candidate.",
        "指針がこのパラメータの範囲を公表していません。範囲がなければ候補になりません。",
    ),
    (
        "maximum deceleration for cooperative braking (op §5.2.5.1, p.188)",
        EXCLUDED_NO_WRITE_VERB,
        "지침은 -9.00~-5.00 m/s² 범위를 공표하지만, 이 코드베이스에 이 값을 쓰는 verb가 없습니다. "
        "적용할 수 없는 것은 탐색하지 않습니다.",
        "The guideline publishes a -9.00 to -5.00 m/s2 band, but no write verb for it exists in "
        "this codebase. What cannot be applied is not searched.",
        "指針は-9.00〜-5.00 m/s²の範囲を公表していますが、この値を書き込むverbがありません。"
        "適用できないものは探索しません。",
    ),
    (
        "conflict-area front / rear gap (op §5 Table 5-10)",
        EXCLUDED_NO_WRITE_VERB,
        "Table 5-10은 앞/뒤 간격(초) 범위를 공표하지만, 충돌영역에 존재하는 쓰기 verb는 "
        "set_conflict_area_status(상태 enum)뿐이고 간격(초)을 쓰는 verb는 없습니다.",
        "Table 5-10 publishes front/rear gap bands in seconds, but the only conflict-area write "
        "verb is set_conflict_area_status (a status enum) — nothing writes those seconds.",
        "Table 5-10は前後ギャップ(秒)の範囲を公表していますが、競合領域に存在する書き込みverbは"
        "set_conflict_area_status(状態enum)のみで、秒を書くverbはありません。",
    ),
    (
        "priority-rule critical gap (op §5 Table 5-9)",
        EXCLUDED_NO_PER_INSTANCE_RANGE,
        "쓰기 verb(set_priority_rule_gap_headway)는 있지만 Table 5-9는 이동류별 단일 기본값을 "
        "공표할 뿐 범위가 아니며, 섀도 모델에서 우선규칙의 이동류를 판별할 수 없어 특정 규칙에 "
        "그 행을 붙일 수도 없습니다.",
        "A write verb exists (set_priority_rule_gap_headway), but Table 5-9 publishes one DEFAULT "
        "per movement type, not a band — and a priority rule's movement type cannot be inferred "
        "from the shadow model, so no row can be attached to a specific rule either.",
        "書き込みverb(set_priority_rule_gap_headway)はありますが、Table 5-9は移動流別の単一"
        "既定値を公表するのみで範囲ではなく、シャドーモデルから優先規則の移動流を判別できないため、"
        "特定の規則に行を対応させることもできません。",
    ),
)


@dataclass(frozen=True)
class Dimension:
    """One searchable parameter and the published band it may move inside.

    ``low``/``high`` are never literals in this module — they are read from
    ``mrwa_reference.merging_behaviour_range``, so the search's bounds and the
    authority's page cannot drift apart.
    """

    parameter: str
    low: float
    high: float
    citation: str
    guideline_wording: str

    @property
    def span(self) -> float:
        return self.high - self.low

    def clip(self, value: float) -> float:
        """Force a value inside the published band. The ONE place a candidate
        value is produced, so an out-of-range candidate cannot exist."""
        return min(self.high, max(self.low, float(value)))

    def contains(self, value: Any) -> bool:
        number = _number(value)
        return number is not None and self.low - 1e-9 <= number <= self.high + 1e-9


def search_space() -> tuple[Dimension, ...]:
    """The candidate dimensions, built from the reference pack.

    A parameter named in :data:`SEARCHABLE_PARAMETERS` whose range the reference
    pack does not carry is DROPPED, not defaulted — if the published band ever
    disappears from the pack, the parameter stops being searchable rather than
    silently acquiring bounds this module made up.
    """
    dimensions: list[Dimension] = []
    for parameter in SEARCHABLE_PARAMETERS:
        band = mrwa_reference.merging_behaviour_range(parameter)
        if band is None:
            continue
        low, high = band
        dimensions.append(
            Dimension(
                parameter=parameter,
                low=float(low),
                high=float(high),
                citation=mrwa_reference.MERGING_BEHAVIOUR_CITATION,
                guideline_wording=mrwa_reference.merging_behaviour_wording(parameter),
            )
        )
    return tuple(dimensions)


def in_published_range(vector: Mapping[str, Any] | None) -> bool:
    """Is every value of a vector inside its own published band?

    The invariant every candidate must satisfy. Checked at ledger time as well
    as at generation time — a vector that arrived from a resumed state file has
    not been through :meth:`Dimension.clip` in this process.
    """
    payload = vector if isinstance(vector, Mapping) else {}
    dimensions = {dim.parameter: dim for dim in search_space()}
    if not payload:
        return False
    for parameter, value in payload.items():
        dimension = dimensions.get(str(parameter))
        if dimension is None or not dimension.contains(value):
            return False
    return True


# ---------------------------------------------------------------------------
# 2. Ladder exhaustion — measured, never assumed
# ---------------------------------------------------------------------------

#: Missing-evidence codes a modeller can CLOSE by supplying something. A node
#: waiting on one of these is not exhausted — it is waiting.
CLOSABLE_MISSING_EVIDENCE = frozenset(
    {
        geh_ladder.GEH_UNDEFINED,
        geh_ladder.NO_DEMAND_TARGET,
        geh_ladder.ENTRANCE_NOT_VERIFIED_MEASURED,
        geh_ladder.NO_QUEUE_OR_DELAY_EVIDENCE,
        geh_ladder.DELAY_NOT_GRADABLE,
    }
)

#: Missing-evidence codes that mean the LADDER LOOKED AND THE ANSWER IS NOT
#: HERE. Nothing a modeller supplies changes them, so a node carrying only
#: these has run out of ladder.
LADDER_DEAD_END_EVIDENCE = frozenset(
    {geh_ladder.NO_THROUGHPUT_SHORTFALL, geh_ladder.NO_CONGESTION_AT_THIS_NODE}
)

#: Rungs that name a CHEAPER action than a parameter search. Any missing node
#: on one of these blocks the search entirely — the guideline's own order puts
#: demand (10), route choice (11) and signals (6) before behaviour (12).
CHEAPER_RUNGS = (geh_ladder.DEMAND_LEVEL, geh_ladder.ROUTING_SPLIT, geh_ladder.SIGNAL_TIMING)

#: Per-node exhaustion verdicts.
EXHAUSTED_PRESCRIPTION_APPLIED = "EXHAUSTED_PRESCRIPTION_APPLIED"
EXHAUSTED_LADDER_DEAD_END = "EXHAUSTED_LADDER_DEAD_END"
CHEAPER_RUNG_REMAINS = "CHEAPER_RUNG_REMAINS"
PRESCRIPTION_PENDING = "PRESCRIPTION_PENDING"
EVIDENCE_MISSING = "EVIDENCE_MISSING"
NODE_PASSES = "NODE_PASSES"

NODE_VERDICTS = (
    EXHAUSTED_PRESCRIPTION_APPLIED,
    EXHAUSTED_LADDER_DEAD_END,
    CHEAPER_RUNG_REMAINS,
    PRESCRIPTION_PENDING,
    EVIDENCE_MISSING,
    NODE_PASSES,
)

_EXHAUSTED_VERDICTS = (EXHAUSTED_PRESCRIPTION_APPLIED, EXHAUSTED_LADDER_DEAD_END)


def table_5_6_fully_applied(applied_rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Is #44's prescription actually in the network, on every readable behaviour?

    Reads ``saturation_flow.verify_applied_row`` verdicts — the SAME payload
    ``routers/calibration.py`` and ``calibration_rail`` read, never a second
    opinion about what "applied" means.

    ``applied`` is False for an unreadable network too: "we could not tell" is
    not "it is applied", and a search that started on the strength of an
    unreadable network would rest on nothing.
    """
    rows = [row for row in (applied_rows or ()) if isinstance(row, Mapping)]
    readable: list[str] = []
    on_row: list[str] = []
    for row in rows:
        verdict = row.get("verdict")
        verdict = verdict if isinstance(verdict, Mapping) else {}
        status = str(verdict.get("status") or "")
        if status == saturation_flow.ROW_UNREADABLE:
            continue
        behavior_no = str(row.get("behavior_no") or "")
        readable.append(behavior_no)
        if status == saturation_flow.ROW_MATCHED and verdict.get("standstill_matches_guideline"):
            on_row.append(behavior_no)
    return {
        "readable": len(readable),
        "on_published_row": len(on_row),
        "off_published_row": sorted(set(readable) - set(on_row)),
        "applied": bool(readable) and len(on_row) == len(readable),
    }


def measure_exhaustion(
    reality_check: Mapping[str, Any] | None,
    applied_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Is the §2.12.1 ladder MEASURED exhausted for this run?

    Per node, reads the ``diagnosis`` the same report already carries (#43) —
    never re-diagnosing, so this module and the ladder cannot disagree in front
    of a modeller.

    ``exhausted`` is True only when (a) at least one node is genuinely out of
    ladder and (b) NO node still has a cheaper rung, a pending Table 5-6
    prescription, or closable missing evidence. One node with a demand fix
    available blocks the whole search: the guideline's order is an order, and a
    behaviour search that jumped it would be tuning around an error someone can
    still correct.
    """
    report = reality_check if isinstance(reality_check, Mapping) else {}
    prescription = table_5_6_fully_applied(applied_rows)
    per_node: dict[str, dict[str, Any]] = {}

    for entry in report.get("nodes") or ():
        if not isinstance(entry, Mapping):
            continue
        node_id = str(entry.get("node_id") or "")
        if not node_id:
            continue
        diagnosis = entry.get("diagnosis")
        if not isinstance(diagnosis, Mapping):
            # No diagnosis at all means the ladder returned None: the model
            # already reproduces this node. Nothing to search for here.
            per_node[node_id] = {"verdict": NODE_PASSES, "rung": None, "missing_evidence": []}
            continue
        rung = str(diagnosis.get("rung") or "")
        missing = [str(code) for code in (diagnosis.get("missing_evidence") or ())]
        if rung in CHEAPER_RUNGS:
            verdict = CHEAPER_RUNG_REMAINS
        elif rung == geh_ladder.CAPACITY_LIMIT:
            verdict = (
                EXHAUSTED_PRESCRIPTION_APPLIED if prescription["applied"] else PRESCRIPTION_PENDING
            )
        elif rung == geh_ladder.UNCLASSIFIABLE:
            # A dead end must be POSITIVELY evidenced, never inferred from the
            # absence of a closable code. Read the wrong way round (2026-08-19,
            # refuter), this branch declared EVERY unrecognised evidence code a
            # dead end — so a future ``geh_ladder`` code, or a diagnosis
            # carrying no codes at all, silently opened the search. The ladder
            # says "the answer is not here" only with one of
            # :data:`LADDER_DEAD_END_EVIDENCE`; anything else is still waiting.
            blocking = [code for code in missing if code in CLOSABLE_MISSING_EVIDENCE]
            dead_end = [code for code in missing if code in LADDER_DEAD_END_EVIDENCE]
            verdict = (
                EXHAUSTED_LADDER_DEAD_END if dead_end and not blocking else EVIDENCE_MISSING
            )
        else:
            verdict = EVIDENCE_MISSING
        per_node[node_id] = {"verdict": verdict, "rung": rung or None, "missing_evidence": missing}

    def _ids(*verdicts: str) -> list[str]:
        return sorted(node for node, item in per_node.items() if item["verdict"] in verdicts)

    exhausted_nodes = _ids(*_EXHAUSTED_VERDICTS)
    blocking_nodes = _ids(CHEAPER_RUNG_REMAINS, PRESCRIPTION_PENDING, EVIDENCE_MISSING)
    return {
        "exhausted": bool(exhausted_nodes) and not blocking_nodes,
        "per_node": per_node,
        "exhausted_nodes": exhausted_nodes,
        "blocking_nodes": blocking_nodes,
        "cheaper_rung_nodes": _ids(CHEAPER_RUNG_REMAINS),
        "prescription_pending_nodes": _ids(PRESCRIPTION_PENDING),
        "evidence_missing_nodes": _ids(EVIDENCE_MISSING),
        "passing_nodes": _ids(NODE_PASSES),
        "table_5_6": prescription,
    }


# ---------------------------------------------------------------------------
# 3. The precondition gate — refuse by name, say what opens it
# ---------------------------------------------------------------------------

NO_REALITY_CHECK = "NO_REALITY_CHECK"
REALITY_CHECK_NOT_CHECKABLE = "REALITY_CHECK_NOT_CHECKABLE"
GEH_ALREADY_PASSES = "GEH_ALREADY_PASSES"
ENTRANCE_NOT_MEASURED = "ENTRANCE_NOT_MEASURED"
LADDER_NOT_EXHAUSTED = "LADDER_NOT_EXHAUSTED"
TABLE_5_6_NOT_APPLIED = "TABLE_5_6_NOT_APPLIED"
SIGNAL_STEP_UNVERIFIED = "SIGNAL_STEP_UNVERIFIED"
HOLDOUT_NOT_AVAILABLE = "HOLDOUT_NOT_AVAILABLE"
NO_SEARCHABLE_PARAMETER = "NO_SEARCHABLE_PARAMETER"

REFUSAL_CODES = (
    NO_REALITY_CHECK,
    REALITY_CHECK_NOT_CHECKABLE,
    GEH_ALREADY_PASSES,
    ENTRANCE_NOT_MEASURED,
    LADDER_NOT_EXHAUSTED,
    TABLE_5_6_NOT_APPLIED,
    SIGNAL_STEP_UNVERIFIED,
    HOLDOUT_NOT_AVAILABLE,
    NO_SEARCHABLE_PARAMETER,
)

#: The smallest node count that can be split without reusing a node on both
#: sides: two to calibrate against, two to validate against. One held-out node
#: would make the whole validation rest on a single intersection.
MIN_NODES_FOR_HOLDOUT = 4

#: Every Nth node (sorted) goes to the holdout. A STRIDE, not a head/tail slice:
#: node numbering in a real model tracks geography, so "the last two" would hand
#: the holdout one corner of the network. Deterministic, so the split is
#: reproducible from the node list alone with no RNG to record.
HOLDOUT_STRIDE = 3


def split_nodes(node_ids: Sequence[str]) -> dict[str, Any]:
    """Deterministic calibration / holdout split. See :data:`HOLDOUT_STRIDE`.

    ``available`` is False below :data:`MIN_NODES_FOR_HOLDOUT`, and the caller
    refuses — this function never returns a split that reuses a node, and never
    returns an empty holdout.
    """
    ordered = sorted({str(node) for node in (node_ids or ()) if str(node)}, key=_node_sort_key)
    if len(ordered) < MIN_NODES_FOR_HOLDOUT:
        return {
            "available": False,
            "calibration": ordered,
            "holdout": [],
            "stride": HOLDOUT_STRIDE,
            "reason": HOLDOUT_NOT_AVAILABLE,
        }
    holdout = [node for index, node in enumerate(ordered) if index % HOLDOUT_STRIDE == HOLDOUT_STRIDE - 1]
    calibration = [node for node in ordered if node not in set(holdout)]
    if len(holdout) < 2 or len(calibration) < 2:
        # A stride that lands badly (e.g. 4 nodes -> 1 held out) is fixed by
        # taking every other node instead of by shrinking the requirement.
        holdout = [node for index, node in enumerate(ordered) if index % 2 == 1]
        calibration = [node for node in ordered if node not in set(holdout)]
    return {
        "available": len(holdout) >= 2 and len(calibration) >= 2,
        "calibration": calibration,
        "holdout": holdout,
        "stride": HOLDOUT_STRIDE,
        "reason": None if len(holdout) >= 2 and len(calibration) >= 2 else HOLDOUT_NOT_AVAILABLE,
    }


def _verified_split(split: Mapping[str, Any]) -> dict[str, Any]:
    """Re-derive a precondition payload's split and refuse it if it disagrees.

    :func:`split_nodes` is a pure function of the node list, so the honest split
    is always recoverable from the two sides' union. A payload whose sides do
    not reproduce it was not built by the gate — it was chosen — and a chosen
    holdout is not a holdout. Refuses rather than silently re-splitting: a
    caller who handed in one thing and got another would not know.

    An EMPTY payload (both sides absent) is passed through as empty. That is the
    ``{"ok": True}`` forgery, and its answer is already the right one: a state
    with no nodes scores nothing.
    """
    calibration = [str(node) for node in (split.get("calibration") or ()) if str(node)]
    holdout = [str(node) for node in (split.get("holdout") or ()) if str(node)]
    if not calibration and not holdout:
        return {"calibration": [], "holdout": []}
    overlap = sorted(set(calibration) & set(holdout), key=_node_sort_key)
    honest = split_nodes(calibration + holdout)
    if overlap or not honest["available"] or (
        honest["calibration"] != sorted(calibration, key=_node_sort_key)
        or honest["holdout"] != sorted(holdout, key=_node_sort_key)
    ):
        raise ValueError(
            "parameter_search.start refuses: the supplied calibration/holdout split is not the "
            f"one split_nodes derives from these nodes (overlap={overlap!r}, "
            f"supplied calibration={sorted(calibration, key=_node_sort_key)!r} holdout="
            f"{sorted(holdout, key=_node_sort_key)!r}, derived calibration="
            f"{honest['calibration']!r} holdout={honest['holdout']!r}). A holdout a caller may "
            "choose is not a holdout."
        )
    return {"calibration": honest["calibration"], "holdout": honest["holdout"]}


def _refusal(code: str, *, ko: str, en: str, ja: str, opens_ko: str, opens_en: str, opens_ja: str,
             detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One named refusal in the ladder's own grammar: what is wrong, and what
    would open it. Never a bare False."""
    return {
        "code": code,
        "why": {"ko": ko, "en": en, "ja": ja},
        "opens_when": {"ko": opens_ko, "en": opens_en, "ja": opens_ja},
        "detail": dict(detail or {}),
    }


def _missing_nodes(report: Mapping[str, Any]) -> list[str]:
    """Nodes whose total GEH misses the report's OWN threshold.

    The threshold is the report's (``validation.GEH_GOOD_LT``, carried on the
    report as ``geh_threshold``), never a second one spelled here.
    """
    threshold = _number(report.get("geh_threshold"))
    threshold = validation.GEH_GOOD_LT if threshold is None else threshold
    missing: list[str] = []
    for entry in report.get("nodes") or ():
        if not isinstance(entry, Mapping):
            continue
        value = _number(entry.get("node_total_geh"))
        if value is not None and value >= threshold:
            node_id = str(entry.get("node_id") or "")
            if node_id:
                missing.append(node_id)
    return sorted(missing, key=_node_sort_key)


def _graded_nodes(report: Mapping[str, Any]) -> list[str]:
    """Nodes carrying a defined total GEH — the only ones a search can score
    against or hold out."""
    graded: list[str] = []
    for entry in report.get("nodes") or ():
        if not isinstance(entry, Mapping):
            continue
        if _number(entry.get("node_total_geh")) is None:
            continue
        node_id = str(entry.get("node_id") or "")
        if node_id:
            graded.append(node_id)
    return sorted(graded, key=_node_sort_key)


def _entrance_unverified_nodes(report: Mapping[str, Any]) -> list[str]:
    """Nodes whose D4 entrance verdict is not ``measured``.

    The premise of every claim past this point is that the demand is fixed
    observed data. A node whose entrance was never verified could be missing
    because the input is wrong, and tuning behaviour to absorb a wrong input is
    the compensating error this whole gate exists to prevent.
    """
    unverified: list[str] = []
    for entry in report.get("nodes") or ():
        if not isinstance(entry, Mapping):
            continue
        outcome = entry.get("outcome")
        outcome = outcome if isinstance(outcome, Mapping) else {}
        evidence = outcome.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        entrance = evidence.get("entrance")
        entrance = entrance if isinstance(entrance, Mapping) else {}
        status = str(entrance.get("status") or "")
        node_id = str(entry.get("node_id") or "")
        if not node_id or not status:
            continue
        if status != fixes.ENTRANCE_MEASURED:
            unverified.append(node_id)
    return sorted(set(unverified), key=_node_sort_key)


def _signal_step_done(rail: Mapping[str, Any] | None) -> bool | None:
    """Is §2.12.1 step 6 (signalised intersections) verified?

    ``None`` when no rail was supplied at all — the caller then cannot claim
    verification either way, and the gate refuses rather than assuming.
    """
    payload = rail if isinstance(rail, Mapping) else None
    if payload is None:
        return None
    for step in payload.get("steps") or ():
        if isinstance(step, Mapping) and step.get("number") == 6:
            return str(step.get("status") or "") == "DONE"
    return None


def evaluate_preconditions(
    *,
    reality_check: Mapping[str, Any] | None,
    applied_rows: Sequence[Mapping[str, Any]] | None = None,
    rail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """May a search start? Every "no" is a named refusal that says what opens it.

    Returns ``{"ok", "refusals", "exhaustion", "split", "space", "missing_nodes",
    "graded_nodes"}``. ``ok`` is True only when ``refusals`` is empty; there is
    no override flag, no ``force``, and no caller-supplied boolean anywhere in
    this function — this repository has twice shipped a guard a caller could
    talk its way past (``duplicated_before_edit``, ``measurement["ok"]``) and
    both had to be rewritten.

    Every refusal is collected, not short-circuited: a modeller who is three
    steps away should learn all three now.
    """
    report = reality_check if isinstance(reality_check, Mapping) else None
    refusals: list[dict[str, Any]] = []
    space = search_space()

    if not space:
        refusals.append(
            _refusal(
                NO_SEARCHABLE_PARAMETER,
                ko="공표 범위를 가진 탐색 가능 파라미터가 하나도 없습니다.",
                en="No searchable parameter carries a published range.",
                ja="公表範囲を持つ探索可能パラメータが一つもありません。",
                opens_ko="참조팩(mrwa_reference)에 공표 범위가 복구되면 열립니다.",
                opens_en="It opens when the reference pack carries a published range again.",
                opens_ja="参照パックに公表範囲が復元されると開きます。",
            )
        )

    if report is None:
        refusals.append(
            _refusal(
                NO_REALITY_CHECK,
                ko="대조된 실행이 없습니다. 탐색은 실측 대비 GEH를 목적함수로 쓰므로 점수가 없으면 시작할 수 없습니다.",
                en="No scored run. The search optimises GEH against observed counts, so with no "
                "score there is nothing to search on.",
                ja="対照された実行がありません。探索は実測比GEHを目的関数とするため、点数がなければ開始できません。",
                opens_ko="시뮬레이션을 실행하고 실측 워크북과 대조(reality check)하면 열립니다.",
                opens_en="It opens once a run has been scored against the observed counts.",
                opens_ja="シミュレーションを実行し実測と対照すると開きます。",
            )
        )
        return {
            "ok": False,
            "refusals": refusals,
            "exhaustion": measure_exhaustion(None, applied_rows),
            "split": split_nodes([]),
            "space": [_dimension_payload(dim) for dim in space],
            "missing_nodes": [],
            "graded_nodes": [],
        }

    status = str(report.get("status") or "")
    if status and status != "CHECKED":
        refusals.append(
            _refusal(
                REALITY_CHECK_NOT_CHECKABLE,
                ko=f"대조 결과가 채점 불가 상태입니다({report.get('reason_code') or status}).",
                en=f"The reality check is not checkable ({report.get('reason_code') or status}).",
                ja=f"対照結果が採点不可の状態です({report.get('reason_code') or status})。",
                opens_ko="대조 결과가 명명한 사유를 해소하면 열립니다.",
                opens_en="It opens once the reason the reality check named is resolved.",
                opens_ja="対照結果が名指しした事由を解消すると開きます。",
                detail={"reason_code": report.get("reason_code"), "status": status},
            )
        )

    graded = _graded_nodes(report)
    missing = _missing_nodes(report)
    exhaustion = measure_exhaustion(report, applied_rows)
    split = split_nodes(graded)

    # Gated on GRADED nodes, not on ``status == "CHECKED"``. Reading the status
    # here made the refusal disappear whenever the field was absent or unknown
    # (2026-08-19, refuter): a report with no ``status`` key and every node
    # passing produced NO refusal at all. "Every node we could grade already
    # passes" is true or false on the nodes, and the nodes are what to read.
    if graded and not missing:
        refusals.append(
            _refusal(
                GEH_ALREADY_PASSES,
                ko="채점된 모든 노드가 GEH 기준을 통과합니다. 탐색할 미스가 없습니다.",
                en="Every graded node already passes the GEH criterion. There is no miss to search on.",
                ja="採点された全ノードがGEH基準を通過しています。探索すべきミスがありません。",
                opens_ko="기준을 벗어나는 노드가 생기면 열립니다 — 지금은 열릴 필요가 없습니다.",
                opens_en="It opens if a node starts missing the criterion — today it need not open.",
                opens_ja="基準を外れるノードが生じれば開きます — 今は開く必要がありません。",
                detail={"graded_nodes": len(graded)},
            )
        )

    unverified = _entrance_unverified_nodes(report)
    if unverified:
        refusals.append(
            _refusal(
                ENTRANCE_NOT_MEASURED,
                ko=f"입구가 실측으로 확인되지 않은 노드가 있습니다: {', '.join(unverified)}. "
                "수요가 고정이라는 전제가 서지 않으면, 행태 파라미터 탐색은 잘못된 입력을 "
                "보상하는 튜닝이 됩니다.",
                en="Some nodes' entrances are not verified as measured: "
                f"{', '.join(unverified)}. Without the premise that demand is fixed observed data, "
                "a behaviour search becomes tuning that compensates for a wrong input.",
                ja=f"流入部が実測として確認されていないノードがあります: {', '.join(unverified)}。"
                "需要が固定という前提が立たなければ、挙動パラメータ探索は誤った入力を補償する調整になります。",
                opens_ko="해당 노드의 차량 입력 링크를 실측 카운트에 연결해 D4 판정이 measured가 되면 열립니다.",
                opens_en="It opens once those nodes' vehicle-input links join the observed counts and "
                "their D4 verdict reads measured.",
                opens_ja="該当ノードの車両インプットのリンクを実測カウントに接続し、D4判定がmeasuredになると開きます。",
                detail={"nodes": unverified},
            )
        )

    if exhaustion["prescription_pending_nodes"]:
        refusals.append(
            _refusal(
                TABLE_5_6_NOT_APPLIED,
                ko=f"용량 한계로 진단된 노드가 있으나 Table 5-6 처방이 아직 적용되지 않았습니다"
                f"(공표 행 밖 주행행태 {len(exhaustion['table_5_6']['off_published_row'])}개). "
                "측정으로 정할 수 있는 값을 탐색으로 대체하지 않습니다.",
                en="A node is diagnosed at the capacity limit but the Table 5-6 prescription is not "
                f"applied yet ({len(exhaustion['table_5_6']['off_published_row'])} behaviour(s) off a "
                "published row). A value that can be measured is not replaced by a search.",
                ja="容量限界と診断されたノードがありますが、Table 5-6の処方がまだ適用されていません"
                f"(公表行外の走行挙動{len(exhaustion['table_5_6']['off_published_row'])}件)。"
                "測定で決められる値を探索で置き換えません。",
                opens_ko="정지선 포화교통류율을 측정하고 Table 5-6 세트(ax/bx_add/bx_mult)를 적용하면 열립니다.",
                opens_en="It opens once the stop-line saturation flow is measured and the Table 5-6 set "
                "(ax/bx_add/bx_mult) is applied.",
                opens_ja="停止線飽和交通流率を測定しTable 5-6セット(ax/bx_add/bx_mult)を適用すると開きます。",
                detail={
                    "nodes": exhaustion["prescription_pending_nodes"],
                    "table_5_6": exhaustion["table_5_6"],
                    "citation": mrwa_reference.TABLE_5_6_CITATION,
                },
            )
        )

    signal_done = _signal_step_done(rail)
    if exhaustion["per_node"] and signal_done is not True:
        signalised = [
            node
            for node, item in exhaustion["per_node"].items()
            if item.get("rung") == geh_ladder.SIGNAL_TIMING
        ]
        refusals.append(
            _refusal(
                SIGNAL_STEP_UNVERIFIED,
                ko="신호 단계(§2.12.1 ⑥)가 검증 완료로 표시되지 않았습니다. 지침 순서상 신호(⑥)는 "
                "행태 파라미터(⑫)보다 앞섭니다.",
                en="The signal step (§2.12.1 step 6) is not marked verified. The guideline's order puts "
                "signals (6) before behaviour parameters (12).",
                ja="信号段階(§2.12.1 ⑥)が検証済みと表示されていません。指針の順序では信号(⑥)が"
                "挙動パラメータ(⑫)より先です。",
                opens_ko="보정 레일의 ⑥단계가 완료가 되면(현시도 검토 등 증거 제출) 열립니다.",
                opens_en="It opens when the calibration rail's step 6 reads Done.",
                opens_ja="補正レールの⑥段階が完了になると開きます。",
                detail={
                    "step_6_status": None if signal_done is None else "DONE",
                    "signal_timing_nodes": sorted(signalised, key=_node_sort_key),
                    "citation": mrwa_reference.CALIBRATION_STEPS_CITATION,
                },
            )
        )

    # UNCONDITIONAL. Gating this on ``missing`` left the exhaustion measurement
    # advisory (2026-08-19, refuter): with no node over the threshold nothing
    # consulted it at all, so a gate could open on a ladder that was never
    # measured exhausted. Exhaustion is the whole licence for this unit, so it
    # is a positive requirement — ``exhausted`` must be True, not merely
    # "not contradicted".
    if not exhaustion["exhausted"]:
        blocking = exhaustion["blocking_nodes"]
        refusals.append(
            _refusal(
                LADDER_NOT_EXHAUSTED,
                ko=f"사다리가 아직 소진되지 않았습니다. 더 앞선 조치가 남은 노드: "
                f"{', '.join(blocking) or '없음'}. 탐색은 사다리를 다 쓴 뒤의 최후 수단입니다.",
                en="The ladder is not exhausted yet. Nodes with an earlier action still available: "
                f"{', '.join(blocking) or 'none'}. The search is a last resort, after the ladder.",
                ja=f"はしごがまだ尽きていません。より前の措置が残るノード: "
                f"{', '.join(blocking) or 'なし'}。探索ははしごを使い切った後の最終手段です。",
                opens_ko="각 노드가 명명한 다음 조치(수요·경로·신호·Table 5-6·증거 보완)를 마치면 열립니다.",
                opens_en="It opens once each node's own named next action (demand, routing, signals, "
                "Table 5-6, or the missing evidence) has been carried out.",
                opens_ja="各ノードが名指しした次の措置(需要・経路・信号・Table 5-6・証拠補完)を終えると開きます。",
                detail={
                    "blocking_nodes": blocking,
                    "cheaper_rung_nodes": exhaustion["cheaper_rung_nodes"],
                    "evidence_missing_nodes": exhaustion["evidence_missing_nodes"],
                    "exhausted_nodes": exhaustion["exhausted_nodes"],
                },
            )
        )

    if not split["available"]:
        refusals.append(
            _refusal(
                HOLDOUT_NOT_AVAILABLE,
                ko=f"채점 가능한 노드가 {len(graded)}개뿐이라 홀드아웃을 나눌 수 없습니다"
                f"(최소 {MIN_NODES_FOR_HOLDOUT}개 필요). 보정에 쓴 데이터로 검증하지 않는다는 "
                "원칙(op §2.12, p.33)을 지킬 수 없으므로 거부합니다.",
                en=f"Only {len(graded)} node(s) can be graded, so no holdout can be split off "
                f"(at least {MIN_NODES_FOR_HOLDOUT} are needed). The principle that validation data "
                "must be independent of calibration data (op §2.12, p.33) could not be kept, so this "
                "refuses.",
                ja=f"採点可能なノードが{len(graded)}件のみで、ホールドアウトを分割できません"
                f"(最低{MIN_NODES_FOR_HOLDOUT}件必要)。補正に使ったデータで検証しないという原則"
                "(op §2.12, p.33)を守れないため拒否します。",
                opens_ko="실측과 조인되는 노드가 4개 이상이 되면 열립니다 — 관측 지점을 늘리거나 "
                "site→node 매핑을 보완하십시오.",
                opens_en="It opens once at least four nodes join the observed counts — add survey sites "
                "or complete the site-to-node map.",
                opens_ja="実測と結合するノードが4件以上になると開きます — 観測地点を増やすかsite→node"
                "マッピングを補完してください。",
                detail={
                    "graded_nodes": len(graded),
                    "minimum": MIN_NODES_FOR_HOLDOUT,
                    "citation": mrwa_reference.CALIBRATION_PRINCIPLE_INDEPENDENT_DATA_CITATION,
                },
            )
        )

    return {
        "ok": not refusals,
        "refusals": refusals,
        "exhaustion": exhaustion,
        "split": split,
        "space": [_dimension_payload(dim) for dim in space],
        "missing_nodes": missing,
        "graded_nodes": graded,
    }


def _dimension_payload(dimension: Dimension) -> dict[str, Any]:
    return {
        "parameter": dimension.parameter,
        "low": dimension.low,
        "high": dimension.high,
        "citation": dimension.citation,
        "guideline_wording": dimension.guideline_wording,
    }


# ---------------------------------------------------------------------------
# 4. The runner seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """What one candidate's simulation produced: GEH per node, or a named failure.

    ``geh_by_node`` may carry ``None`` for a node the run could not grade —
    ``validation.geh`` returns None rather than a number for an undefined
    statistic, and this seam preserves that distinction all the way into the
    ledger instead of substituting a passing zero.
    """

    ok: bool
    geh_by_node: Mapping[str, float | None]
    reason: str | None = None
    elapsed_s: float = 0.0


class SearchRunner(Protocol):
    """One candidate -> one scored run.

    A real implementation submits the parameter vector as a working-copy edit,
    runs the simulation through the JOB QUEUE (never a request handler, never a
    per-step loop — CLAUDE.md's COM rules), scores the finished run against the
    same observed counts the reality check used, and returns GEH per node.

    **No such implementation ships in this unit.** See :func:`runner_requirement`.
    """

    def run(
        self, vector: Mapping[str, float], seed: int, node_ids: Sequence[str]
    ) -> RunOutcome:  # pragma: no cover - protocol
        ...


def runner_requirement() -> dict[str, Any]:
    """What a real runner must provide, in one place, so the gap is a spec.

    Stated rather than half-built. A COM-driving runner written here could not
    be executed or verified in this unit (live Vissim is out of scope by the
    unit's own rules), and shipping an untested one would be a capability claim
    this repository has been burned by before.
    """
    return {
        "interface": "services.agent.parameter_search.SearchRunner",
        "wired": False,
        "requires": [
            "clone the active dataset into a job work dir (never edit the active .inpx in place)",
            "apply the candidate vector with set_driving_behavior_parameter, one field at a time",
            "run through the job queue with RunContinuous, at the pinned seed",
            "score the finished run with build_reality_check_report against the SAME observation "
            "set and period the search's preconditions were evaluated on",
            "return validation.geh per node — None, never 0.0, where the statistic is undefined",
        ],
        "why_not_here": (
            "Live Vissim COM is out of scope for this unit. A runner that cannot be executed here "
            "cannot be verified here, and an unverified COM path presented as working is the "
            "failure mode this product exists to prevent."
        ),
    }


@dataclass
class SyntheticRunner:
    """An offline runner over a KNOWN function — what the tests search against.

    Not a mock in the "records calls" sense: it computes a real, deterministic
    GEH surface from the parameter vector, so a test can assert the search
    actually found the optimum rather than merely that it ran.

    ``surface(vector, seed, node_id) -> float`` is the caller's. ``cost_s`` is
    the simulated wall clock each run consumes, so budget exhaustion and resume
    can be exercised without waiting.
    """

    surface: Callable[[Mapping[str, float], int, str], float | None]
    cost_s: float = 30.0
    calls: int = 0
    fail_on: Callable[[Mapping[str, float], int], str | None] | None = None

    def run(
        self, vector: Mapping[str, float], seed: int, node_ids: Sequence[str]
    ) -> RunOutcome:
        self.calls += 1
        if self.fail_on is not None:
            reason = self.fail_on(vector, seed)
            if reason:
                return RunOutcome(ok=False, geh_by_node={}, reason=reason, elapsed_s=self.cost_s)
        return RunOutcome(
            ok=True,
            geh_by_node={node: self.surface(vector, seed, node) for node in node_ids},
            elapsed_s=self.cost_s,
        )


# ---------------------------------------------------------------------------
# 5. The objective — constructed with the calibration nodes and nothing else
# ---------------------------------------------------------------------------

#: The optimizer minimises MEAN GEH, not the pass COUNT. The pass count is a
#: step function: every vector between two thresholds scores identically, which
#: gives a local search no gradient at all to follow. Mean GEH is the continuous
#: surrogate; the pass count is recorded on every ledger row beside it, so a
#: reader sees the number the guideline actually cares about even though the
#: search steers by the other.
OBJECTIVE = "MEAN_GEH_OVER_CALIBRATION_NODES"


@dataclass(frozen=True)
class Score:
    """One run reduced to a comparable number, plus what it was reduced FROM."""

    objective: float | None
    pass_count: int
    graded: int
    geh_by_node: Mapping[str, float | None]
    ok: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "pass_count": self.pass_count,
            "graded": self.graded,
            "geh_by_node": {node: value for node, value in self.geh_by_node.items()},
            "ok": self.ok,
            "reason": self.reason,
        }


class _Objective:
    """Scores a run over ONE fixed node set.

    The holdout containment is structural, not a convention: this object holds
    the calibration node ids, the search loop holds only this object, and the
    holdout scorer is a separate instance the loop is never given. There is no
    code path from the optimizer to a holdout GEH.
    """

    def __init__(self, node_ids: Sequence[str], threshold: float) -> None:
        self._node_ids = tuple(sorted({str(node) for node in node_ids}, key=_node_sort_key))
        self._threshold = float(threshold)

    @property
    def node_ids(self) -> tuple[str, ...]:
        return self._node_ids

    def score(self, outcome: RunOutcome) -> Score:
        if not outcome.ok:
            return Score(None, 0, 0, {}, False, outcome.reason or "RUN_FAILED")
        values = {node: _number(outcome.geh_by_node.get(node)) for node in self._node_ids}
        usable = [value for value in values.values() if value is not None and math.isfinite(value)]
        if not usable:
            return Score(None, 0, 0, values, False, "NO_GRADABLE_NODE")
        return Score(
            objective=statistics.fmean(usable),
            pass_count=sum(1 for value in usable if value < self._threshold),
            graded=len(usable),
            geh_by_node=values,
            ok=True,
        )


def score_holdout(
    outcome: RunOutcome, holdout_node_ids: Sequence[str], threshold: float
) -> Score:
    """Score a run over the HELD-OUT nodes. Called after the search ends, never
    inside it — the only entry point to a holdout number in this module."""
    return _Objective(holdout_node_ids, threshold).score(outcome)


# ---------------------------------------------------------------------------
# 6. Stage 1 — LHS screening and its self-stated selection rule
# ---------------------------------------------------------------------------

#: THE SELECTION CRITERION, in GEH units: how far the objective's mean moves
#: across a parameter's own published band (:func:`sensitivity`'s
#: ``main_effect``). A parameter is carried into stage 2 when moving it across
#: its band shifts mean GEH by at least this much.
#:
#: MEASURED, not assumed. The first cut of this module selected on |Spearman
#: rho| and selected NOTHING on a synthetic surface with a known optimum
#: (measured 2026-08-19: rho 0.12 / 0.06 / 0.04 on a quadratic bowl whose true
#: weights were 90 / 12 / 0). Rank correlation only detects MONOTONE effects,
#: and a calibration objective is U-shaped in its parameters almost by
#: definition — there is an optimum in the middle of the band, so the objective
#: falls and then rises and the ranks cancel. Selecting on rho would therefore
#: have declared every parameter insignificant on exactly the surfaces this
#: search exists for. So the criterion is a binned MAIN EFFECT (a coarse
#: one-way Sobol/elementary-effects estimate), which sees a U just fine, and rho
#: is reported alongside it as descriptive evidence of DIRECTION — never as the
#: gate. 0.25 GEH is this product's own engineering floor on the same scale as
#: the guideline's <5 criterion, stated in the rationale as ours.
SENSITIVITY_EFFECT_FLOOR = 0.25

#: Bins the sampled range is cut into for the main-effect estimate. Three is
#: the smallest number that can see a U (low / middle / high); more bins on a
#: budget-sized screen would put too few samples in each.
SENSITIVITY_BINS = 3

#: Reported, never the criterion — see :data:`SENSITIVITY_EFFECT_FLOOR`.
SENSITIVITY_RHO_REPORTED_ONLY = True

#: How many parameters stage 2 will carry, at most. Below this the space is
#: small enough that screening only produces evidence.
MAX_SELECTED_PARAMETERS = 4

#: Default LHS sample count: a multiple of :data:`SENSITIVITY_BINS` so the bins
#: come out equal, and about 12% of a default hour's ~120 runs, which leaves the
#: bulk of the budget for the optimizer.
DEFAULT_SCREENING_SAMPLES = 15


def latin_hypercube(
    space: Sequence[Dimension], samples: int, seed: int
) -> list[dict[str, float]]:
    """A seeded Latin hypercube over the published bands.

    Stratified per dimension (each band cut into ``samples`` equal strata, one
    draw inside each, then the strata permuted independently per dimension), so
    every parameter's whole range is covered even at a small sample count.

    Every value goes through :meth:`Dimension.clip`, so an out-of-range sample
    cannot be produced even if the arithmetic drifted.
    """
    rng = random.Random(seed)
    count = max(1, int(samples))
    columns: dict[str, list[float]] = {}
    for dimension in space:
        width = dimension.span / count
        draws = [dimension.low + (index + rng.random()) * width for index in range(count)]
        rng.shuffle(draws)
        columns[dimension.parameter] = [dimension.clip(value) for value in draws]
    return [
        {dimension.parameter: columns[dimension.parameter][index] for dimension in space}
        for index in range(count)
    ]


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rank correlation, stdlib only.

    ``None`` when it is undefined — fewer than three pairs, or either side
    constant (a zero-variance rank vector has no correlation, and returning 0.0
    there would read as "measured no effect" rather than "could not measure").
    """
    pairs = [
        (float(x), float(y))
        for x, y in zip(xs, ys)
        if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))
    ]
    if len(pairs) < 3:
        return None
    rank_x = _ranks([pair[0] for pair in pairs])
    rank_y = _ranks([pair[1] for pair in pairs])
    mean_x = statistics.fmean(rank_x)
    mean_y = statistics.fmean(rank_y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(rank_x, rank_y))
    denom_x = math.sqrt(sum((a - mean_x) ** 2 for a in rank_x))
    denom_y = math.sqrt(sum((b - mean_y) ** 2 for b in rank_y))
    if denom_x == 0.0 or denom_y == 0.0:
        return None
    return numerator / (denom_x * denom_y)


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks, ties shared — the standard Spearman treatment."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        shared = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[order[position]] = shared
        index = end + 1
    return ranks


def main_effect(
    xs: Sequence[float], ys: Sequence[float], bins: int = SENSITIVITY_BINS
) -> float | None:
    """How far the objective's MEAN moves across one parameter's sampled range.

    Samples are sorted by the parameter, cut into ``bins`` equal-count strata,
    and the answer is ``max(bin mean) - min(bin mean)`` — a coarse one-way main
    effect. Unlike a rank correlation it sees a U-shaped response, which is the
    shape a calibration objective actually has (see
    :data:`SENSITIVITY_EFFECT_FLOOR` for the measurement that forced this).

    ``None`` when there are fewer than ``bins`` samples per bin to average, or
    when the parameter did not vary — "could not measure" is not "measured no
    effect", and the caller must be able to tell them apart.
    """
    pairs = [
        (float(x), float(y))
        for x, y in zip(xs, ys)
        if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))
    ]
    count = max(2, int(bins))
    if len(pairs) < count * 2:
        return None
    if max(pair[0] for pair in pairs) == min(pair[0] for pair in pairs):
        return None
    pairs.sort(key=lambda pair: pair[0])
    width = len(pairs) / count
    means: list[float] = []
    for index in range(count):
        chunk = pairs[int(round(index * width)) : int(round((index + 1) * width))]
        if chunk:
            means.append(statistics.fmean(value for _, value in chunk))
    if len(means) < 2:
        return None
    return max(means) - min(means)


def sensitivity(
    vectors: Sequence[Mapping[str, float]],
    objectives: Sequence[float | None],
    space: Sequence[Dimension],
) -> dict[str, Any]:
    """Rank each parameter by how much it moved the objective, and say why.

    The selection rule states itself in the payload (``effect_floor`` /
    ``bins`` / ``rule``) so a reader of the rationale never has to open this file
    to learn what "significant" meant.

    ``spearman_rho`` rides along on every row as DESCRIPTIVE evidence — it says
    whether the effect is monotone and in which direction — but it decides
    nothing. See :data:`SENSITIVITY_EFFECT_FLOOR` for the measurement that
    demoted it.
    """
    usable = [
        (dict(vector), value)
        for vector, value in zip(vectors, objectives)
        if value is not None and math.isfinite(float(value))
    ]
    ys = [value for _, value in usable]
    per_parameter: list[dict[str, Any]] = []
    for dimension in space:
        xs = [vector.get(dimension.parameter) for vector, _ in usable]
        paired_x = [x for x in xs if x is not None]
        rho = spearman(paired_x, ys) if len(paired_x) == len(ys) else None
        effect = main_effect(paired_x, ys) if len(paired_x) == len(ys) else None
        significant = effect is not None and effect >= SENSITIVITY_EFFECT_FLOOR
        per_parameter.append(
            {
                "parameter": dimension.parameter,
                "main_effect": effect,
                "spearman_rho": rho,
                "monotone": rho is not None and abs(rho) >= 0.5,
                "significant": significant,
            }
        )
    ranked = sorted(
        per_parameter,
        key=lambda item: (0 if item["significant"] else 1, -(item["main_effect"] or 0.0)),
    )
    selected = [item["parameter"] for item in ranked if item["significant"]][:MAX_SELECTED_PARAMETERS]
    return {
        "per_parameter": per_parameter,
        "selected": selected,
        "samples": len(usable),
        "objective_spread": (max(ys) - min(ys)) if ys else 0.0,
        "effect_floor": SENSITIVITY_EFFECT_FLOOR,
        "bins": SENSITIVITY_BINS,
        "max_selected": MAX_SELECTED_PARAMETERS,
        "rule": (
            f"A parameter is carried into optimisation when its binned MAIN EFFECT — the spread of "
            f"mean GEH across {SENSITIVITY_BINS} equal-count strata of its own published band — is at "
            f"least {SENSITIVITY_EFFECT_FLOOR} GEH. Spearman's rho is reported but does NOT decide: "
            "it only detects monotone effects, and a calibration objective is U-shaped in its "
            "parameters (an optimum inside the band), where the ranks cancel and rho reads near zero "
            "for a parameter that dominates the surface."
        ),
    }


# ---------------------------------------------------------------------------
# 7. Budget, ledger, state
# ---------------------------------------------------------------------------

#: The user's decision: a WALL-CLOCK ceiling, not a run count. One hour.
DEFAULT_BUDGET_S = 3600.0

#: Compass-search step, as a fraction of each published band. Halved whenever a
#: full sweep finds no improvement; the search stops below the floor.
INITIAL_STEP_FRACTION = 0.25
MIN_STEP_FRACTION = 1.0 / 32.0

STAGE_BASELINE = "BASELINE"
STAGE_SCREENING = "SCREENING"
STAGE_OPTIMIZATION = "OPTIMIZATION"
STAGE_HOLDOUT = "HOLDOUT"
STAGES = (STAGE_BASELINE, STAGE_SCREENING, STAGE_OPTIMIZATION, STAGE_HOLDOUT)

STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL_BUDGET_EXHAUSTED = "PARTIAL_BUDGET_EXHAUSTED"
STATUS_PARTIAL_INTERRUPTED = "PARTIAL_INTERRUPTED"
STATUS_REFUSED = "REFUSED"
STATUSES = (
    STATUS_COMPLETE,
    STATUS_PARTIAL_BUDGET_EXHAUSTED,
    STATUS_PARTIAL_INTERRUPTED,
    STATUS_REFUSED,
)

#: Ledger state version. A resumed state carrying a different one is refused
#: rather than reinterpreted.
STATE_VERSION = 1


def ledger_schema() -> dict[str, str]:
    """The ledger row's fields and what each means. Returned as data so the
    schema is documented in one place and can be asserted by a test."""
    return {
        "index": "0-based position in the ledger, monotonic across resumes",
        "stage": f"one of {', '.join(STAGES)}",
        "params": "the candidate vector — parameter -> value, always inside the published band",
        "seed": "the simulation random seed this candidate was run at",
        "node_set": "CALIBRATION or HOLDOUT — which node ids this row was scored over",
        "objective": f"{OBJECTIVE}; None when the run failed or graded nothing",
        "pass_count": "nodes in this row's node set with GEH below the report's threshold",
        "graded": "nodes that produced a defined GEH",
        "geh_by_node": "the per-node GEH this row was reduced from — None preserved, never 0.0",
        "ok": "did the run produce a gradable result",
        "reason": "why not, when ok is False",
        "started_at_s": "seconds since the search began (injected clock, monotonic)",
        "elapsed_s": "wall clock this candidate consumed",
    }


@dataclass
class SearchState:
    """Everything needed to stop a search and continue it later.

    JSON-shaped via :meth:`to_dict` — this module persists nothing itself, so a
    caller may keep it beside a job record, in a work dir, or in memory.
    """

    search_id: str
    seed: int
    budget_s: float
    calibration_nodes: tuple[str, ...]
    holdout_nodes: tuple[str, ...]
    threshold: float
    screening_samples: int
    baseline_vector: dict[str, float]
    ledger: list[dict[str, Any]] = field(default_factory=list)
    consumed_s: float = 0.0
    stage: str = STAGE_BASELINE
    selected_parameters: list[str] = field(default_factory=list)
    sensitivity: dict[str, Any] = field(default_factory=dict)
    center: dict[str, float] = field(default_factory=dict)
    center_objective: float | None = None
    step_fraction: float = INITIAL_STEP_FRACTION
    cursor: int = 0
    evaluated: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "search_id": self.search_id,
            "seed": self.seed,
            "budget_s": self.budget_s,
            "calibration_nodes": list(self.calibration_nodes),
            "holdout_nodes": list(self.holdout_nodes),
            "threshold": self.threshold,
            "screening_samples": self.screening_samples,
            "baseline_vector": dict(self.baseline_vector),
            "ledger": [dict(row) for row in self.ledger],
            "consumed_s": self.consumed_s,
            "stage": self.stage,
            "selected_parameters": list(self.selected_parameters),
            "sensitivity": dict(self.sensitivity),
            "center": dict(self.center),
            "center_objective": self.center_objective,
            "step_fraction": self.step_fraction,
            "cursor": self.cursor,
            "evaluated": dict(self.evaluated),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SearchState":
        data = dict(payload or {})
        version = data.get("version")
        if version != STATE_VERSION:
            raise ValueError(
                f"parameter_search state version {version!r} cannot be resumed by this build "
                f"(expected {STATE_VERSION}). Refusing rather than reinterpreting a state file "
                "whose meaning may have changed."
            )
        return cls(
            search_id=str(data.get("search_id") or ""),
            seed=int(data.get("seed") or 0),
            budget_s=float(data.get("budget_s") or DEFAULT_BUDGET_S),
            calibration_nodes=tuple(str(node) for node in data.get("calibration_nodes") or ()),
            holdout_nodes=tuple(str(node) for node in data.get("holdout_nodes") or ()),
            threshold=float(data.get("threshold") or validation.GEH_GOOD_LT),
            screening_samples=int(data.get("screening_samples") or DEFAULT_SCREENING_SAMPLES),
            baseline_vector={str(k): float(v) for k, v in dict(data.get("baseline_vector") or {}).items()},
            ledger=[dict(row) for row in data.get("ledger") or ()],
            consumed_s=float(data.get("consumed_s") or 0.0),
            stage=str(data.get("stage") or STAGE_BASELINE),
            selected_parameters=[str(name) for name in data.get("selected_parameters") or ()],
            sensitivity=dict(data.get("sensitivity") or {}),
            center={str(k): float(v) for k, v in dict(data.get("center") or {}).items()},
            center_objective=_number(data.get("center_objective")),
            step_fraction=float(data.get("step_fraction") or INITIAL_STEP_FRACTION),
            cursor=int(data.get("cursor") or 0),
            evaluated={str(k): float(v) for k, v in dict(data.get("evaluated") or {}).items()},
        )


# ---------------------------------------------------------------------------
# 8. The engine
# ---------------------------------------------------------------------------


class ParameterSearch:
    """The two-stage, budget-bounded, ledgered search.

    Construct with a runner and a state; call :meth:`run` to consume budget.
    :meth:`run` returns as soon as the budget is spent or the search converges,
    and the state it leaves behind resumes exactly.

    The search NEVER sees a holdout node: :attr:`_objective` is built with the
    calibration ids only and is the sole scorer the loop can reach.
    """

    def __init__(
        self,
        *,
        state: SearchState,
        runner: SearchRunner,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.state = state
        self._runner = runner
        self._clock = clock or time.monotonic
        self._space = {dim.parameter: dim for dim in search_space()}
        self._objective = _Objective(state.calibration_nodes, state.threshold)
        self._started = self._clock()

    # -- budget ------------------------------------------------------------

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.state.budget_s - self.state.consumed_s)

    def _budget_left(self) -> bool:
        return self.remaining_s > 0.0

    # -- evaluation --------------------------------------------------------

    def _evaluate(self, vector: Mapping[str, float], stage: str) -> Score:
        """Run one candidate, ledger it, and charge the budget.

        REFUSES a vector outside the published band — it does not clip one.
        Clipping is the GENERATORS' job (:meth:`Dimension.clip`, used by the LHS
        and by every compass step, both of which naturally propose points past an
        edge). By the time a vector arrives here it has already been through one
        of them, so an out-of-range value means it came from somewhere else — a
        resumed state file, a hand-edited payload, a future caller — and silently
        pulling it back into range would accept corrupted input as if it were a
        candidate. Same discipline as :meth:`SearchState.from_dict`'s version
        check: refuse rather than reinterpret.

        An unknown parameter is refused for the same reason rather than dropped:
        a vector naming a field this space does not contain is not a vector this
        search built.
        """
        candidate = {str(name): value for name, value in dict(vector).items()}
        unknown = sorted(set(candidate) - set(self._space))
        if unknown:
            raise ValueError(
                f"refusing to run a candidate naming parameter(s) outside the search space: "
                f"{unknown!r} (space: {sorted(self._space)})"
            )
        if not in_published_range(candidate):
            raise ValueError(
                f"refusing to run a candidate outside the published MRWA range: {candidate!r} "
                f"(bands: {[(d.parameter, d.low, d.high) for d in self._space.values()]})"
            )
        started_at = self._clock() - self._started
        outcome = self._runner.run(candidate, self.state.seed, list(self._objective.node_ids))
        score = self._objective.score(outcome)
        self.state.consumed_s += float(outcome.elapsed_s or 0.0)
        self.state.ledger.append(
            {
                "index": len(self.state.ledger),
                "stage": stage,
                "params": dict(candidate),
                "seed": self.state.seed,
                "node_set": "CALIBRATION",
                "objective": score.objective,
                "pass_count": score.pass_count,
                "graded": score.graded,
                "geh_by_node": dict(score.geh_by_node),
                "ok": score.ok,
                "reason": score.reason,
                "started_at_s": started_at,
                "elapsed_s": float(outcome.elapsed_s or 0.0),
            }
        )
        if score.objective is not None:
            self.state.evaluated[_vector_key(candidate)] = score.objective
        return score

    # -- stages ------------------------------------------------------------

    def _screening_plan(self) -> list[dict[str, float]]:
        space = [self._space[name] for name in SEARCHABLE_PARAMETERS if name in self._space]
        return latin_hypercube(space, self.state.screening_samples, self.state.seed)

    def _rows(self, stage: str) -> list[dict[str, Any]]:
        return [row for row in self.state.ledger if row.get("stage") == stage]

    def _run_baseline(self) -> None:
        if self._rows(STAGE_BASELINE):
            self.state.stage = STAGE_SCREENING
            return
        self._evaluate(self.state.baseline_vector, STAGE_BASELINE)
        self.state.stage = STAGE_SCREENING

    def _run_screening(self) -> bool:
        """Returns False when the budget ran out mid-screen."""
        plan = self._screening_plan()
        done = len(self._rows(STAGE_SCREENING))
        for vector in plan[done:]:
            if not self._budget_left():
                return False
            self._evaluate(vector, STAGE_SCREENING)
        rows = self._rows(STAGE_SCREENING)
        self.state.sensitivity = sensitivity(
            [row["params"] for row in rows],
            [row["objective"] for row in rows],
            [self._space[name] for name in SEARCHABLE_PARAMETERS if name in self._space],
        )
        self.state.selected_parameters = list(self.state.sensitivity["selected"])
        # THE CENTRE IS NOT THE BEST SCREENING ROW.
        #
        # An LHS row carries a value for EVERY dimension, including the ones the
        # screen just measured as insignificant — and those values are wherever
        # the sampler happened to land. Taking the row wholesale would carry an
        # arbitrary sample forward as if it were a finding, and (measured
        # 2026-08-19 on the synthetic surface) the proposal then wrote
        # ``lat_dist_stand_def = 0.201`` for a parameter the screen had just
        # shown does nothing — a number with no evidence behind it, which is
        # exactly the hunch-tuning this unit exists to replace.
        #
        # So: selected dimensions start at the best screening row's values;
        # every other dimension is returned to the BASELINE and never moves
        # again. A parameter this search cannot show an effect for is a
        # parameter this search does not change.
        best = _best_row(rows)
        selected = set(self.state.selected_parameters)
        source = dict(best["params"]) if best is not None else dict(self.state.baseline_vector)
        self.state.center = {
            name: (source.get(name, value) if name in selected else value)
            for name, value in self.state.baseline_vector.items()
        }
        self.state.center_objective = None
        self.state.stage = STAGE_OPTIMIZATION
        self.state.cursor = 0
        self.state.step_fraction = INITIAL_STEP_FRACTION
        return True

    def _sweep(self) -> list[tuple[str, int]]:
        """The compass sweep: each selected parameter, each direction, in a fixed
        order. One coordinate per step — op §2.12.1 p.33's "change one parameter
        at a time", applied literally."""
        return [
            (parameter, direction)
            for parameter in sorted(self.state.selected_parameters)
            for direction in (1, -1)
        ]

    def _run_optimization(self) -> bool:
        if not self.state.selected_parameters:
            # Screening measured nothing significant. That IS the answer, and it
            # is worth more than a search that would move a parameter anyway.
            # :meth:`best` then admits only the baseline row, the holdout compare
            # is baseline-against-baseline, and the unit refuses instead of
            # proposing — which is the honest end of a search that found nothing.
            return True
        # The re-blended centre (selected dims from the screen, the rest returned
        # to baseline) is a point nothing has run yet, so its objective is
        # MEASURED rather than inherited from the row it was partly built from.
        # Done here, not at the end of screening, so a budget stop between the
        # two stages resumes correctly instead of leaving an unscored centre.
        if self.state.center_objective is None:
            if not self._budget_left():
                return False
            self.state.center_objective = self._evaluate(
                self.state.center, STAGE_OPTIMIZATION
            ).objective
        sweep = self._sweep()
        while self.state.step_fraction >= MIN_STEP_FRACTION:
            improved = False
            while self.state.cursor < len(sweep):
                if not self._budget_left():
                    return False
                parameter, direction = sweep[self.state.cursor]
                dimension = self._space[parameter]
                trial = dict(self.state.center)
                trial[parameter] = dimension.clip(
                    trial[parameter] + direction * self.state.step_fraction * dimension.span
                )
                self.state.cursor += 1
                if _vector_key(trial) == _vector_key(self.state.center):
                    continue  # clipped back onto the centre: no new information
                cached = self.state.evaluated.get(_vector_key(trial))
                score_value = cached if cached is not None else None
                if score_value is None:
                    score = self._evaluate(trial, STAGE_OPTIMIZATION)
                    score_value = score.objective
                if score_value is None:
                    continue
                if self.state.center_objective is None or score_value < self.state.center_objective:
                    self.state.center = trial
                    self.state.center_objective = score_value
                    improved = True
                    self.state.cursor = 0  # restart the sweep from the new centre
                    break
            if not improved and self.state.cursor >= len(sweep):
                self.state.step_fraction /= 2.0
                self.state.cursor = 0
        return True

    def run(self) -> dict[str, Any]:
        """Consume budget until the search finishes or the clock runs out.

        Returns the same payload :func:`result` builds. Safe to call again on
        the same state after a stop: every stage resumes from the ledger.
        """
        if not self._budget_left():
            return self.result(STATUS_PARTIAL_BUDGET_EXHAUSTED)
        if self.state.stage == STAGE_BASELINE:
            self._run_baseline()
        if self.state.stage == STAGE_SCREENING:
            if not self._run_screening():
                return self.result(STATUS_PARTIAL_BUDGET_EXHAUSTED)
        if self.state.stage == STAGE_OPTIMIZATION:
            if not self._run_optimization():
                return self.result(STATUS_PARTIAL_BUDGET_EXHAUSTED)
        return self.result(STATUS_COMPLETE)

    # -- results -----------------------------------------------------------

    def _admissible(self, row: Mapping[str, Any]) -> bool:
        """May this row be PROPOSED — not merely, was it measured?

        A row is admissible when every dimension the screen did NOT select sits
        at its baseline value. The screen's whole product is the statement "this
        parameter does not measurably move GEH inside its published band";
        shipping a row that moved one anyway would contradict the evidence in the
        same payload. LHS rows move every dimension at once, so after selection
        almost none of them are admissible — which is correct, and is why the
        optimizer re-blends its centre before it starts.

        Before selection has happened (still screening, or a partial result) the
        set is empty and every calibration row is admissible: nothing has been
        declared insignificant yet, so no claim is being contradicted.
        """
        if row.get("node_set") != "CALIBRATION":
            return False
        # THE BAND IS RE-CHECKED HERE, not merely at generation time.
        # ``_evaluate`` validates what it is about to run, which covers every
        # row THIS process produced — but a resumed state's ledger arrives
        # verbatim from :meth:`SearchState.from_dict`, which does not (and must
        # not silently clip). Without this line a hand-edited or corrupted state
        # file could put a row outside the published band at the front of
        # ``best()`` and have :func:`propose` emit it as a write (2026-08-19,
        # refuter: ``lat_dist_stand_def = 0.05`` against a published 0.1-0.4).
        if not in_published_range(row.get("params")):
            return False
        if not self.state.selected_parameters and self.state.stage == STAGE_SCREENING:
            return True
        selected = set(self.state.selected_parameters)
        params = row.get("params") or {}
        for name, baseline_value in self.state.baseline_vector.items():
            if name in selected:
                continue
            if _vector_key({name: _number(params.get(name)) or 0.0}) != _vector_key(
                {name: baseline_value}
            ):
                return False
        return True

    def best(self) -> dict[str, Any] | None:
        """The best PROPOSABLE row so far — a partial result is still a result."""
        return _best_row([row for row in self.state.ledger if self._admissible(row)])

    def best_measured(self) -> dict[str, Any] | None:
        """The best row the search ever ran, admissible or not. Reported for the
        ledger summary so a reader can see the search DID find a lower point than
        the one it is willing to propose, and why it will not propose it.

        "Admissible or not" still means "inside the published band": an
        out-of-band row was never a candidate this search could have run, so
        reporting one as measured would put a number this product never stood
        behind in front of a reader.
        """
        return _best_row(
            [
                row
                for row in self.state.ledger
                if row.get("node_set") == "CALIBRATION" and in_published_range(row.get("params"))
            ]
        )

    def baseline(self) -> dict[str, Any] | None:
        return _best_row(
            [row for row in self._rows(STAGE_BASELINE) if in_published_range(row.get("params"))]
        )

    def result(self, status: str) -> dict[str, Any]:
        best = self.best()
        baseline = self.baseline()
        return {
            "search_id": self.state.search_id,
            "status": status,
            "objective": OBJECTIVE,
            "seed": self.state.seed,
            "budget_s": self.state.budget_s,
            "consumed_s": self.state.consumed_s,
            "remaining_s": self.remaining_s,
            "candidates": len(self.state.ledger),
            "stage": self.state.stage,
            "sensitivity": dict(self.state.sensitivity),
            "selected_parameters": list(self.state.selected_parameters),
            "calibration_nodes": list(self.state.calibration_nodes),
            "holdout_nodes": list(self.state.holdout_nodes),
            "baseline": dict(baseline) if baseline else None,
            "best": dict(best) if best else None,
            "best_measured": dict(self.best_measured() or {}) or None,
            "space": [_dimension_payload(dim) for dim in self._space.values()],
            "partial": status != STATUS_COMPLETE,
            "state": self.state.to_dict(),
        }


def start(
    *,
    search_id: str,
    preconditions: Mapping[str, Any],
    baseline_vector: Mapping[str, float],
    seed: int,
    threshold: float | None = None,
    budget_s: float = DEFAULT_BUDGET_S,
    screening_samples: int = DEFAULT_SCREENING_SAMPLES,
) -> SearchState:
    """Build the initial state from a PASSING precondition payload.

    Raises when the gate refused. There is deliberately no way to start a search
    from a failed gate: the refusals are the product, and a caller that could
    skip them would make the whole precondition layer advisory.

    The SPLIT is re-derived here rather than trusted. ``ok`` said the gate
    passed; it did not say the ``split`` beside it is the one :func:`split_nodes`
    produces. Taking the caller's word for it (2026-08-19, refuter) let a
    hand-written payload hold out nodes of its own choosing — including the
    calibration nodes themselves, which made :func:`accept_winner` return
    HOLDOUT_PASSED on the very data the optimizer had been scoring. The holdout
    is only meaningful if nobody gets to pick it, so this recomputes it from the
    two lists' union and refuses any payload that disagrees.
    """
    payload = preconditions if isinstance(preconditions, Mapping) else {}
    if not payload.get("ok"):
        codes = [str(item.get("code")) for item in payload.get("refusals") or ()]
        raise ValueError(
            "parameter_search.start refuses: the precondition gate did not pass "
            f"({', '.join(codes) or 'no payload'}). Resolve the named refusals first."
        )
    split = _verified_split(payload.get("split") or {})
    space = search_space()
    baseline = {
        dim.parameter: dim.clip(_number(dict(baseline_vector).get(dim.parameter)) or dim.low)
        for dim in space
    }
    return SearchState(
        search_id=str(search_id),
        seed=int(seed),
        budget_s=float(budget_s),
        calibration_nodes=tuple(str(node) for node in split.get("calibration") or ()),
        holdout_nodes=tuple(str(node) for node in split.get("holdout") or ()),
        threshold=float(threshold if threshold is not None else validation.GEH_GOOD_LT),
        screening_samples=int(screening_samples),
        baseline_vector=baseline,
    )


def resume(
    state: Mapping[str, Any] | SearchState,
    runner: SearchRunner,
    *,
    clock: Callable[[], float] | None = None,
    additional_budget_s: float | None = None,
) -> ParameterSearch:
    """Continue a stopped search from its own state.

    ``additional_budget_s`` extends the ceiling — the user's decision is a
    default hour with stop-and-resume, and resuming with the ceiling already
    spent would otherwise return immediately.
    """
    restored = state if isinstance(state, SearchState) else SearchState.from_dict(state)
    if additional_budget_s:
        restored.budget_s += float(additional_budget_s)
    return ParameterSearch(state=restored, runner=runner, clock=clock)


# ---------------------------------------------------------------------------
# 9. Holdout acceptance
# ---------------------------------------------------------------------------

HOLDOUT_PASSED = "HOLDOUT_PASSED"
HOLDOUT_NOT_IMPROVED = "HOLDOUT_NOT_IMPROVED"
HOLDOUT_NOT_GRADABLE = "HOLDOUT_NOT_GRADABLE"
HOLDOUT_SEED_NOT_HELD = "HOLDOUT_SEED_NOT_HELD"
NO_CANDIDATE = "NO_CANDIDATE"

#: A vector this acceptance would have to RUN sits outside its published band.
#: Reachable only from a state whose ledger or baseline did not come from
#: :meth:`ParameterSearch._evaluate` — a resumed file, a hand-edited payload.
#: Named rather than clipped, for the reason :meth:`ParameterSearch._evaluate`
#: gives: refuse rather than reinterpret.
HOLDOUT_VECTOR_OUT_OF_RANGE = "HOLDOUT_VECTOR_OUT_OF_RANGE"

ACCEPTANCE_CODES = (
    HOLDOUT_PASSED,
    HOLDOUT_NOT_IMPROVED,
    HOLDOUT_NOT_GRADABLE,
    HOLDOUT_SEED_NOT_HELD,
    HOLDOUT_VECTOR_OUT_OF_RANGE,
    NO_CANDIDATE,
)


def accept_winner(
    search: ParameterSearch,
    runner: SearchRunner,
    *,
    unseen_seed: int,
) -> dict[str, Any]:
    """Validate the best candidate on data the optimizer never scored.

    Two independent tests, both required:

    1. **The node holdout.** The winner and the BASELINE are each run over the
       held-out nodes and compared. An absolute holdout GEH would say nothing —
       the held-out nodes might simply be easy — so acceptance is a COMPARISON:
       the winner must improve on the baseline's mean holdout GEH and must not
       pass fewer holdout nodes.
    2. **An unseen seed.** Both holdout runs use a seed the search never used, so
       a vector that only wins on its own seed is caught here rather than in
       front of a client. Refused by name if the caller hands back the search's
       own seed.

    Never raises; a failure is a named verdict, and a named verdict is the
    product.
    """
    best = search.best()
    baseline = search.baseline()
    holdout_nodes = list(search.state.holdout_nodes)
    threshold = search.state.threshold

    if best is None or not best.get("ok"):
        return {"accepted": False, "code": NO_CANDIDATE, "winner": None, "holdout": None}
    if int(unseen_seed) == int(search.state.seed):
        return {
            "accepted": False,
            "code": HOLDOUT_SEED_NOT_HELD,
            "winner": dict(best),
            "holdout": None,
            "detail": {"search_seed": search.state.seed, "offered_seed": int(unseen_seed)},
        }

    baseline_probe = dict(baseline["params"]) if baseline else dict(search.state.baseline_vector)
    if not in_published_range(best.get("params")) or not in_published_range(baseline_probe):
        # Nothing is RUN on an out-of-band vector. ``best()`` is already
        # filtered, so this is the baseline half — restored verbatim by
        # ``SearchState.from_dict`` and never clipped there on purpose.
        return {
            "accepted": False,
            "code": HOLDOUT_VECTOR_OUT_OF_RANGE,
            "winner": dict(best),
            "holdout": None,
            "detail": {
                "winner_in_range": in_published_range(best.get("params")),
                "baseline_in_range": in_published_range(baseline_probe),
                "bands": [_dimension_payload(dim) for dim in search_space()],
            },
        }

    winner_outcome = runner.run(dict(best["params"]), int(unseen_seed), holdout_nodes)
    winner_score = score_holdout(winner_outcome, holdout_nodes, threshold)
    baseline_params = baseline_probe
    baseline_outcome = runner.run(baseline_params, int(unseen_seed), holdout_nodes)
    baseline_score = score_holdout(baseline_outcome, holdout_nodes, threshold)

    for stage_score, params, outcome in (
        (winner_score, dict(best["params"]), winner_outcome),
        (baseline_score, baseline_params, baseline_outcome),
    ):
        search.state.ledger.append(
            {
                "index": len(search.state.ledger),
                "stage": STAGE_HOLDOUT,
                "params": dict(params),
                "seed": int(unseen_seed),
                "node_set": "HOLDOUT",
                "objective": stage_score.objective,
                "pass_count": stage_score.pass_count,
                "graded": stage_score.graded,
                "geh_by_node": dict(stage_score.geh_by_node),
                "ok": stage_score.ok,
                "reason": stage_score.reason,
                "started_at_s": None,
                "elapsed_s": float(outcome.elapsed_s or 0.0),
            }
        )

    payload = {
        "winner": dict(best),
        "unseen_seed": int(unseen_seed),
        "holdout_nodes": holdout_nodes,
        "winner_holdout": winner_score.to_dict(),
        "baseline_holdout": baseline_score.to_dict(),
        "principle": "validation data must be independent of the data used for calibration",
        "principle_citation": mrwa_reference.CALIBRATION_PRINCIPLE_INDEPENDENT_DATA_CITATION,
    }

    if winner_score.objective is None or baseline_score.objective is None:
        return {"accepted": False, "code": HOLDOUT_NOT_GRADABLE, "holdout": payload}
    improved = winner_score.objective < baseline_score.objective
    no_regression = winner_score.pass_count >= baseline_score.pass_count
    if not (improved and no_regression):
        return {"accepted": False, "code": HOLDOUT_NOT_IMPROVED, "holdout": payload}
    return {"accepted": True, "code": HOLDOUT_PASSED, "holdout": payload}


# ---------------------------------------------------------------------------
# 10. The guardrail forecast — named before the human is asked
# ---------------------------------------------------------------------------

SHARED_BEHAVIOR_REFUSAL = "driving_behavior_shared_object_edit"

GUARDRAIL_WILL_REFUSE = "GUARDRAIL_WILL_REFUSE"
GUARDRAIL_CLEAR = "GUARDRAIL_CLEAR"
GUARDRAIL_UNKNOWN = "GUARDRAIL_UNKNOWN"


def preflight_forecast(behavior: Mapping[str, Any] | None) -> dict[str, Any]:
    """Will the constraint engine refuse this proposal? Told BEFORE the ask.

    The collision, stated plainly: every parameter this unit searches is written
    with ``set_driving_behavior_parameter``, and
    ``constraints._check_driving_behavior_shared_object_edit`` refuses that write
    on any behaviour with ``reference_count > 0``. #44-b's exception does not
    reach here — a :class:`~services.agent.car_following_grant.CarFollowingGrant`
    covers ``ax``/``bx_add``/``bx_mult`` at Table 5-6's own values, by signature,
    and names nothing else on purpose.

    Two resolutions exist and BOTH are human calls, so neither is taken here:

    * widen the #44-b exception to cover this path (a decision of the same shape
      as ``docs/phase2/DECISIONS.md`` 2026-08-19), or
    * localize — duplicate the behaviour and point only some links at the copy.

    Localization is **not auto-selected**. Duplicating a network-wide behaviour
    changes what the model means; a search may not make that choice on a
    modeller's behalf.
    """
    payload = behavior if isinstance(behavior, Mapping) else None
    if payload is None:
        return {
            "verdict": GUARDRAIL_UNKNOWN,
            "constraint_id": SHARED_BEHAVIOR_REFUSAL,
            "reference_count": None,
            "why": {
                "ko": "대상 주행행태를 읽지 못해 가드레일 결과를 예보할 수 없습니다.",
                "en": "The target driving behaviour could not be read, so the guardrail outcome "
                "cannot be forecast.",
                "ja": "対象の走行挙動を読めないため、ガードレールの結果を予報できません。",
            },
            "resolutions": _guardrail_resolutions(),
        }
    reference_count = _int(payload.get("reference_count"))
    if reference_count <= 0:
        return {
            "verdict": GUARDRAIL_CLEAR,
            "constraint_id": SHARED_BEHAVIOR_REFUSAL,
            "reference_count": reference_count,
            "why": {
                "ko": "이 주행행태를 참조하는 링크 행태 유형이 없어 공유 객체 가드레일에 걸리지 않습니다.",
                "en": "No link behaviour type references this driving behaviour, so the shared-object "
                "guardrail does not apply.",
                "ja": "この走行挙動を参照するリンク挙動タイプがないため、共有オブジェクトのガードレールに"
                "かかりません。",
            },
            "resolutions": [],
        }
    return {
        "verdict": GUARDRAIL_WILL_REFUSE,
        "constraint_id": SHARED_BEHAVIOR_REFUSAL,
        "reference_count": reference_count,
        "why": {
            "ko": f"이 제안은 지금 상태로는 거부됩니다. 대상 주행행태는 {reference_count}곳에서 참조되는 "
            "공유 객체이고, set_driving_behavior_parameter의 공유 객체 가드레일이 이를 막습니다. "
            "#44-b의 예외는 Table 5-6 세트(ax/bx_add/bx_mult)에만 서명 단위로 한정되므로 이 경로에는 "
            "적용되지 않습니다.",
            "en": f"As things stand this proposal will be REFUSED. The target driving behaviour is a "
            f"shared object ({reference_count} reference(s)) and the shared-object guardrail on "
            "set_driving_behavior_parameter blocks the write. #44-b's exception is confined by "
            "signature to the Table 5-6 set (ax/bx_add/bx_mult) and does not reach this path.",
            "ja": f"この提案は現状では拒否されます。対象の走行挙動は{reference_count}箇所から参照される"
            "共有オブジェクトであり、set_driving_behavior_parameterの共有オブジェクトガードレールが"
            "これを阻みます。#44-bの例外はTable 5-6セット(ax/bx_add/bx_mult)に署名単位で限定される"
            "ため、この経路には適用されません。",
        },
        "resolutions": _guardrail_resolutions(),
    }


def _guardrail_resolutions() -> list[dict[str, Any]]:
    return [
        {
            "key": "widen_exception",
            "auto_selectable": False,
            "ko": "#44-b 예외를 이 경로까지 넓힌다 (2026-08-19 결정과 같은 형태의 사람 결정).",
            "en": "Widen #44-b's exception to cover this path — a human decision of the same shape as "
            "the 2026-08-19 one.",
            "ja": "#44-bの例外をこの経路まで広げる(2026-08-19の決定と同型の人間の判断)。",
        },
        {
            "key": "localize_duplicate",
            "auto_selectable": False,
            "ko": "주행행태를 복제해 일부 링크만 새 행태를 참조하게 한다. 자동으로 선택하지 않습니다 — "
            "네트워크 전역 행태를 복제하는 것은 모델의 의미를 바꾸는 일이라 모델러의 결정입니다.",
            "en": "Localize: duplicate the behaviour and repoint some links at the copy. NOT auto-selected "
            "— duplicating a network-wide behaviour changes what the model means, so it is the "
            "modeller's call.",
            "ja": "走行挙動を複製し一部リンクのみ新しい挙動を参照させる。自動選択しません — ネットワーク"
            "全体の挙動を複製することはモデルの意味を変えるため、モデラーの判断です。",
        },
    ]


# ---------------------------------------------------------------------------
# 11. Rationale and proposal
# ---------------------------------------------------------------------------


def rationale(
    result: Mapping[str, Any], acceptance: Mapping[str, Any]
) -> dict[str, str]:
    """The trilingual sentence a modeller reads before approving anything.

    Names, in order: the published range each parameter moved inside and its
    page, how many candidates were run, what the sensitivity screen measured,
    and the holdout comparison with its own numbers. Every one of those is read
    off the payloads — nothing here is asserted that the ledger does not carry.
    """
    payload = dict(result or {})
    accepted = dict(acceptance or {})
    holdout = dict(accepted.get("holdout") or {})
    best = dict(payload.get("best") or {})
    space = list(payload.get("space") or [])
    sens = dict(payload.get("sensitivity") or {})

    bands_en = ", ".join(
        f"{item['parameter']} {_fmt(item['low'])}-{_fmt(item['high'])}" for item in space
    )
    citation = space[0]["citation"] if space else mrwa_reference.MERGING_BEHAVIOUR_CITATION
    candidates = payload.get("candidates") or 0
    significant = [
        item["parameter"] for item in sens.get("per_parameter") or [] if item.get("significant")
    ]
    held_at_baseline = [
        item["parameter"]
        for item in sens.get("per_parameter") or []
        if not item.get("significant")
    ]
    effect_floor = sens.get("effect_floor", SENSITIVITY_EFFECT_FLOOR)
    bins = sens.get("bins", SENSITIVITY_BINS)
    winner_holdout = dict(holdout.get("winner_holdout") or {})
    baseline_holdout = dict(holdout.get("baseline_holdout") or {})
    values_en = ", ".join(f"{name}={_fmt(value)}" for name, value in sorted((best.get("params") or {}).items()))

    def _holdout_clause(language: str) -> str:
        if not holdout:
            return {
                "ko": "홀드아웃 검증이 아직 수행되지 않았습니다.",
                "en": "The holdout validation has not been run yet.",
                "ja": "ホールドアウト検証はまだ実施されていません。",
            }[language]
        won = _fmt(winner_holdout.get("objective"))
        base = _fmt(baseline_holdout.get("objective"))
        nodes = ", ".join(holdout.get("holdout_nodes") or [])
        seed = holdout.get("unseen_seed")
        if accepted.get("accepted"):
            return {
                "ko": f"보정에 쓰지 않은 노드({nodes})에서, 탐색이 쓴 적 없는 시드 {seed}로 재실행한 결과 "
                f"평균 GEH {base} → {won}로 개선되어 홀드아웃을 통과했습니다.",
                "en": f"On the held-out nodes ({nodes}), re-run at seed {seed} which the search never "
                f"used, mean GEH improved {base} → {won} — the holdout passed.",
                "ja": f"補正に使っていないノード({nodes})で、探索が使ったことのないシード{seed}で再実行した"
                f"結果、平均GEHが{base} → {won}に改善しホールドアウトを通過しました。",
            }[language]
        return {
            "ko": f"홀드아웃 미통과({accepted.get('code')}): 노드 {nodes}, 시드 {seed}에서 평균 GEH "
            f"{base} → {won}. 제안하지 않습니다.",
            "en": f"The holdout did NOT pass ({accepted.get('code')}): on nodes {nodes} at seed {seed}, "
            f"mean GEH {base} → {won}. Nothing is proposed.",
            "ja": f"ホールドアウト不通過({accepted.get('code')}): ノード{nodes}、シード{seed}で平均GEH "
            f"{base} → {won}。提案しません。",
        }[language]

    held_ko = (
        f" 유의하지 않은 {', '.join(held_at_baseline)}은(는) 기준값에 그대로 두었습니다."
        if held_at_baseline
        else ""
    )
    held_en = (
        f" The parameter(s) the screen could not show an effect for ({', '.join(held_at_baseline)}) "
        "were left at their baseline values, not moved."
        if held_at_baseline
        else ""
    )
    held_ja = (
        f" 有意でない{', '.join(held_at_baseline)}は基準値のままにしました。"
        if held_at_baseline
        else ""
    )
    return {
        "ko": (
            f"MRWA 공표 범위({bands_en}; {citation}) 안에서만 탐색했습니다. 후보 {candidates}개를 "
            f"실행했고, 민감도 스크리닝에서 유의한 파라미터는 {', '.join(significant) or '없음'}입니다"
            f"(기준: 공표 범위를 {bins}개 구간으로 나눈 주효과 ≥ {effect_floor} GEH).{held_ko} "
            f"최선 후보: {values_en or '없음'}. {_holdout_clause('ko')} "
            f"{mrwa_reference.MERGING_RANGE_SCOPE_NOTE} {mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE}"
        ),
        "en": (
            f"The search moved only inside MRWA's published ranges ({bands_en}; {citation}). "
            f"{candidates} candidate(s) were run; the sensitivity screen found "
            f"{', '.join(significant) or 'no'} significant parameter(s) "
            f"(criterion: a main effect of at least {effect_floor} GEH across {bins} strata of the "
            f"published band).{held_en} Best candidate: {values_en or 'none'}. "
            f"{_holdout_clause('en')} {mrwa_reference.MERGING_RANGE_SCOPE_NOTE} "
            f"{mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE}"
        ),
        "ja": (
            f"MRWAの公表範囲({bands_en}; {citation})の中でのみ探索しました。候補{candidates}件を実行し、"
            f"感度スクリーニングで有意なパラメータは{', '.join(significant) or 'なし'}です"
            f"(基準: 公表範囲を{bins}区間に分けた主効果 ≥ {effect_floor} GEH)。{held_ja} "
            f"最良候補: {values_en or 'なし'}。{_holdout_clause('ja')} "
            f"{mrwa_reference.MERGING_RANGE_SCOPE_NOTE} {mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE}"
        ),
    }


def propose(
    *,
    result: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    behavior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The ask-first payload: one write per searched parameter, or a refusal.

    A candidate that did not pass the holdout produces NO actions — the refusal
    is the whole answer, and it carries the same rationale a proposal would.

    The guardrail forecast rides along on every proposal, refusal or not: a human
    asked to approve a write that preflight will refuse deserves to be told
    before clicking, not after.
    """
    payload = dict(result or {})
    accepted = dict(acceptance or {})
    forecast = preflight_forecast(behavior)
    reasons = rationale(payload, accepted)
    behavior_no = str((behavior or {}).get("no") or "") if isinstance(behavior, Mapping) else ""

    if not accepted.get("accepted"):
        return {
            "outcome": "REFUSAL",
            "reason_code": accepted.get("code") or NO_CANDIDATE,
            "actions": [],
            "rationale": reasons,
            "preflight_forecast": forecast,
            "holdout": accepted.get("holdout"),
            "ledger_size": len(payload.get("state", {}).get("ledger", []) or []),
        }

    best = dict(payload.get("best") or {})
    baseline_vector = dict((payload.get("state") or {}).get("baseline_vector") or {})
    # ONLY the parameters that actually moved. A write that sets a field to the
    # value it already holds is a no-op the human would still have to read,
    # approve and audit — and on a shared behaviour it is a no-op that trips the
    # guardrail for nothing. Combined with the admissibility rule in
    # :meth:`ParameterSearch._admissible`, this means every action in a proposal
    # names a parameter the screen measured an effect for AND the optimizer moved.
    actions = []
    unchanged: list[str] = []
    for parameter, value in sorted((best.get("params") or {}).items()):
        baseline_value = _number(baseline_vector.get(parameter))
        if baseline_value is not None and _vector_key({parameter: value}) == _vector_key(
            {parameter: baseline_value}
        ):
            unchanged.append(parameter)
            continue
        actions.append(
            {
                "tool": SEARCH_TOOL,
                "params": {
                    "behavior_no": behavior_no,
                    "parameter": parameter,
                    "value": value,
                },
            }
        )
    if not actions:
        # The winner IS the baseline. Nothing to propose, and saying so is the
        # honest end of a search that ran and found no better point.
        return {
            "outcome": "REFUSAL",
            "reason_code": NO_CANDIDATE,
            "actions": [],
            "unchanged_parameters": unchanged,
            "rationale": reasons,
            "preflight_forecast": forecast,
            "holdout": accepted.get("holdout"),
            "ledger_size": len(payload.get("state", {}).get("ledger", []) or []),
        }
    return {
        "outcome": "PROPOSAL",
        "reason_code": None,
        "actions": actions,
        "unchanged_parameters": unchanged,
        "rationale": reasons,
        "preflight_forecast": forecast,
        "holdout": accepted.get("holdout"),
        "ledger_size": len(payload.get("state", {}).get("ledger", []) or []),
        "assumptions": [
            "SEARCH_IS_A_LAST_RESORT_NOT_AN_OBSERVATION",
            "PUBLISHED_RANGE_IS_MRWA_MERGING_AREA_SCOPE",
        ],
    }


# ---------------------------------------------------------------------------
# 12. The trigger card — ask-first, never auto-start
# ---------------------------------------------------------------------------


def trigger_card(preconditions: Mapping[str, Any]) -> dict[str, Any] | None:
    """"Shall I start a search?" — or ``None`` when the gate has not opened.

    ``None`` is the common case and the important one: no card, no nudge, no
    "you could also try". The card appears only on a MEASURED exhausted ladder,
    which is the user's decision ② (a proposal card on ladder exhaustion,
    ask-first, never an automatic start).

    Approving it enqueues a job; it never starts anything by itself, and this
    module cannot start one either — see :func:`runner_requirement`.
    """
    payload = preconditions if isinstance(preconditions, Mapping) else {}
    if not payload.get("ok"):
        return None
    exhaustion = dict(payload.get("exhaustion") or {})
    split = dict(payload.get("split") or {})
    space = list(payload.get("space") or [])
    exhausted = list(exhaustion.get("exhausted_nodes") or [])
    if not exhausted or not exhaustion.get("exhausted"):
        # The card's whole sentence is "the ladder is exhausted at node(s) X".
        # With X empty that sentence is an authority claim about nothing, and it
        # rendered verbatim — "The ladder is exhausted at node(s)  — ..." — off a
        # gate that had opened without measuring exhaustion (2026-08-19,
        # refuter). No named node, no card.
        return None
    bands = ", ".join(f"{item['parameter']} {_fmt(item['low'])}-{_fmt(item['high'])}" for item in space)
    return {
        "key": "parameter_search_trigger",
        "unit": "#51",
        "ask_first": True,
        "auto_start": False,
        "question": {
            "ko": "범위-구속 파라미터 탐색을 시작할까요?",
            "en": "Shall I start a range-bounded parameter search?",
            "ja": "範囲拘束パラメータ探索を開始しますか?",
        },
        "because": {
            "ko": f"노드 {', '.join(exhausted)}에서 사다리를 다 썼습니다 — 수요·경로·신호는 해당 없고 "
            f"Table 5-6 처방도 적용됐는데 GEH가 여전히 어긋납니다. 남은 것은 지침이 범위를 공표한 "
            f"행태 파라미터뿐입니다({bands}).",
            "en": f"The ladder is exhausted at node(s) {', '.join(exhausted)} — demand, routing and "
            "signals do not apply and the Table 5-6 prescription is already applied, yet GEH still "
            f"misses. What remains is the behaviour parameters the guideline publishes a range for "
            f"({bands}).",
            "ja": f"ノード{', '.join(exhausted)}ではしごを使い切りました — 需要・経路・信号は該当せず、"
            f"Table 5-6の処方も適用済みですが、GEHが依然として外れています。残るのは指針が範囲を公表する"
            f"挙動パラメータのみです({bands})。",
        },
        "plan": {
            "budget_s": DEFAULT_BUDGET_S,
            "resumable": True,
            "objective": OBJECTIVE,
            "space": space,
            "calibration_nodes": list(split.get("calibration") or []),
            "holdout_nodes": list(split.get("holdout") or []),
            "algorithm": "LHS screening, then deterministic compass search (one parameter per step)",
            "algorithm_citation": mrwa_reference.CALIBRATION_PRINCIPLE_ONE_PARAMETER_CITATION,
        },
        "caveats": [
            mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE,
            mrwa_reference.MERGING_RANGE_SCOPE_NOTE,
        ],
        "runner": runner_requirement(),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt(value: Any) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:g}"


def _node_sort_key(node_id: str) -> tuple[int, Any]:
    text = str(node_id)
    return (0, int(text)) if text.isdigit() else (1, text)


def _vector_key(vector: Mapping[str, float]) -> str:
    """A vector's identity for the evaluated-cache, rounded so floating-point
    noise cannot present the same point as two."""
    return "|".join(f"{name}={float(value):.6f}" for name, value in sorted(vector.items()))


def _best_row(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    usable = [row for row in rows if row.get("ok") and _number(row.get("objective")) is not None]
    if not usable:
        return None
    return dict(min(usable, key=lambda row: float(row["objective"])))


def _best_objective(rows: Sequence[Mapping[str, Any]]) -> float | None:
    best = _best_row(rows)
    return None if best is None else _number(best.get("objective"))


__all__ = [
    "ACCEPTANCE_CODES",
    "CHEAPER_RUNGS",
    "CLOSABLE_MISSING_EVIDENCE",
    "DEFAULT_BUDGET_S",
    "DEFAULT_SCREENING_SAMPLES",
    "Dimension",
    "ENTRANCE_NOT_MEASURED",
    "EXCLUSIONS",
    "EXCLUSION_CODES",
    "GEH_ALREADY_PASSES",
    "GUARDRAIL_CLEAR",
    "GUARDRAIL_UNKNOWN",
    "GUARDRAIL_WILL_REFUSE",
    "HOLDOUT_NOT_AVAILABLE",
    "HOLDOUT_NOT_GRADABLE",
    "HOLDOUT_NOT_IMPROVED",
    "HOLDOUT_PASSED",
    "HOLDOUT_SEED_NOT_HELD",
    "HOLDOUT_STRIDE",
    "LADDER_DEAD_END_EVIDENCE",
    "LADDER_NOT_EXHAUSTED",
    "MIN_NODES_FOR_HOLDOUT",
    "NODE_VERDICTS",
    "NO_CANDIDATE",
    "NO_REALITY_CHECK",
    "NO_SEARCHABLE_PARAMETER",
    "OBJECTIVE",
    "ParameterSearch",
    "REALITY_CHECK_NOT_CHECKABLE",
    "REFUSAL_CODES",
    "RunOutcome",
    "SEARCHABLE_PARAMETERS",
    "SEARCH_TOOL",
    "SENSITIVITY_BINS",
    "SENSITIVITY_EFFECT_FLOOR",
    "SHARED_BEHAVIOR_REFUSAL",
    "SIGNAL_STEP_UNVERIFIED",
    "STAGES",
    "STATE_VERSION",
    "STATUSES",
    "STATUS_COMPLETE",
    "STATUS_PARTIAL_BUDGET_EXHAUSTED",
    "Score",
    "SearchRunner",
    "SearchState",
    "SyntheticRunner",
    "TABLE_5_6_NOT_APPLIED",
    "accept_winner",
    "evaluate_preconditions",
    "in_published_range",
    "latin_hypercube",
    "ledger_schema",
    "measure_exhaustion",
    "preflight_forecast",
    "propose",
    "rationale",
    "resume",
    "runner_requirement",
    "score_holdout",
    "search_space",
    "sensitivity",
    "spearman",
    "split_nodes",
    "start",
    "table_5_6_fully_applied",
    "trigger_card",
]

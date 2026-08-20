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

5. The guardrail collision, and how the user resolved it (#51-b, 2026-08-20)
----------------------------------------------------------------------------
Every searched parameter is written with ``set_driving_behavior_parameter``, and every
W74 behaviour in a real consultant model is referenced at least once — so
``constraints._check_driving_behavior_shared_object_edit`` refuses the write. #44-b's
narrow exception does not help: a :class:`~services.agent.car_following_grant.
CarFollowingGrant` covers ``ax``/``bx_add``/``bx_mult`` at Table 5-6's own values and
nothing else, by signature, on purpose.

#51 shipped with that collision open and forecast, and named the two resolutions — widen
the exception, or localize — as human calls. The user made the call on **2026-08-20**
(``docs/phase2/DECISIONS.md``): **(a), widen it**, in the same shape and with the same
narrowness as 2026-08-19's. Localization was REJECTED, in the user's own words: 네트워크
전체를 채점해 찾은 값을 일부에만 적용하면 값의 의미가 바뀐다.

What that means for this module, and what it deliberately does not mean:

* :func:`grant_from_search` is the exception's structural key and the ONLY minting path.
  It builds a :class:`~services.agent.searched_behaviour_grant.SearchedBehaviourGrant`
  only from a COMPLETE search whose holdout acceptance is re-derived from its own ledger.
* The DIRECT path — a plain tool call, ``fixes.preflight_findings``, the health check —
  is refused exactly as before. Nothing here is reachable from a payload.
* :func:`preflight_forecast` now reports BOTH paths (``paths``), and its top-level
  ``verdict`` still describes the direct one. #44's refuter invariant is unchanged: a
  forecast may be pessimistic, never optimistic.
* This module still does not localize, and still never auto-selects anything.

Pure and stdlib-only: no I/O, no COM, no state module, no clock of its own (the clock is
injected). The engine returns a serializable state dict; persisting it is the caller's.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from services import validation
from services.agent import (
    fixes,
    geh_ladder,
    mrwa_reference,
    saturation_flow,
    searched_behaviour_grant,
)
from services.agent.gates import signature

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


#: What a live runner must do, in the order it must do it. The specification
#: half of :func:`runner_requirement`, hoisted to a constant so the same five
#: lines are reported whether or not this machine can run one — and so
#: ``search_runner``'s module docstring can be checked against a single source
#: rather than against a copy inside a function body.
RUNNER_REQUIRES = (
    "clone the active dataset into a job work dir (never edit the active .inpx in place)",
    "apply the candidate vector with set_driving_behavior_parameter, one field at a time",
    "run through the job queue with RunContinuous, at the pinned seed",
    "score the finished run with build_reality_check_report against the SAME observation "
    "set and period the search's preconditions were evaluated on",
    "return validation.geh per node — None, never 0.0, where the statistic is undefined",
)


class SearchRunner(Protocol):
    """One candidate -> one scored run.

    A real implementation submits the parameter vector as a working-copy edit,
    runs the simulation through the JOB QUEUE (never a request handler, never a
    per-step loop — CLAUDE.md's COM rules), scores the finished run against the
    same observed counts the reality check used, and returns GEH per node.

    ``services.agent.search_runner.VissimSearchRunner`` is that implementation
    (2026-08-21); ``SyntheticRunner`` below is the offline one the tests search
    against. See :func:`runner_requirement` for what a machine must provide
    before the live one can be used at all.
    """

    def run(
        self, vector: Mapping[str, float], seed: int, node_ids: Sequence[str]
    ) -> RunOutcome:  # pragma: no cover - protocol
        ...


def runner_requirement() -> dict[str, Any]:
    """What a real runner must provide, and whether THIS MACHINE can provide it.

    ``requires`` is unchanged from the day this was a pure specification, and it
    is kept in full whichever way ``wired`` comes out: it is the list
    ``services.agent.search_runner`` is written against, line for line, and a
    reader of an unwired card is owed the same statement of what running one
    would take.

    ``wired`` stopped being a constant on 2026-08-21, when that implementation
    landed. It is now a MEASUREMENT of the box the backend is running on —
    Windows plus pywin32, the same probe every COM entry point in this codebase
    already makes — and it is re-taken on every call rather than cached, so the
    answer a card shows is the answer at the moment it was asked. On the
    consultant's Windows machine it reads True; on the Mac laptop this repo is
    also developed on it reads False, honestly, with ``why_not_here`` naming the
    reason. It is NOT a claim that a search will succeed: the precondition gate,
    the job's own refusals and the holdout all still stand between this flag and
    a proposal.
    """
    try:
        from services.agent import search_runner
    except Exception as exc:  # noqa: BLE001 — an unimportable runner IS "not wired"
        return {
            "interface": "services.agent.parameter_search.SearchRunner",
            "implementation": None,
            "wired": False,
            "requires": list(RUNNER_REQUIRES),
            "why_not_here": (
                "The live runner module could not be imported in this backend "
                f"({type(exc).__name__}: {exc}), so no search can be started here."
            ),
        }

    available, reason = search_runner.runner_availability()
    return {
        "interface": search_runner.RUNNER_INTERFACE,
        "implementation": search_runner.RUNNER_IMPLEMENTATION,
        "wired": bool(available),
        "requires": list(RUNNER_REQUIRES),
        "why_not_here": None
        if available
        else (
            f"{reason} Running a search means driving Vissim over COM, so this backend cannot "
            "start one — the card still says what a search would do, and on a machine that can "
            "run it the same card carries the start control."
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
# 9-b. The grant — minted ONLY from a finished search (unit #51-b)
#
# DECISIONS.md 2026-08-20 extended #44-b's narrow exception to this path, in the
# same shape and with the same narrowness. This is the structural key: the only
# function anywhere that can build a
# :class:`~services.agent.searched_behaviour_grant.SearchedBehaviourGrant`, and
# it refuses everything short of a complete, self-consistent, held-out search.
#
# Where the anti-forgery property actually lives, stated once:
#
# * NOTHING here reads a file. The caller hands in live payloads, and
#   ``searched_behaviour_broker`` — the only thing that keeps them between a
#   proposal and a click — keeps them in PROCESS MEMORY only, exactly as #47
#   does for Table 5-6 sets. ``pending_approval.json`` carries an opaque
#   ``search_id`` and nothing else, and an id the process did not mint answers
#   ``None``.
# * The acceptance is RE-DERIVED FROM THE LEDGER, never read off the ``code``
#   field: the two HOLDOUT rows must be present, at a seed the search never
#   used, over the held-out nodes, and the winner must really beat the baseline
#   on them. So even a caller that hands in ``{"accepted": True, "code":
#   "HOLDOUT_PASSED"}`` gets a refusal unless the ledger says the same thing.
# * The split is RE-DERIVED (``_verified_split``) and no calibration row may
#   carry a held-out node — the #51 refuter's two walls, re-run at mint time
#   rather than trusted from run time.
# ---------------------------------------------------------------------------


def ledger_reference(result: Mapping[str, Any] | None) -> dict[str, Any]:
    """A citable reference to the ledger a rationale is talking about.

    ``{"search_id", "rows", "digest"}``. The digest is a fingerprint of the ledger the
    grant was minted from, so a reader handed a ledger can tell whether it is THAT one —
    it is a citation aid, deliberately NOT a verification key: nothing in this unit
    accepts a digest as evidence, because a hash a caller supplies is a claim, and this
    design does not accept claims (see this section's header for where the real property
    lives).
    """
    payload = dict(result or {})
    state = payload.get("state")
    state = dict(state) if isinstance(state, Mapping) else {}
    ledger = [dict(row) for row in state.get("ledger") or ()]
    canonical = json.dumps(ledger, sort_keys=True, ensure_ascii=False, default=str)
    return {
        "search_id": str(payload.get("search_id") or ""),
        "rows": len(ledger),
        "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
    }


def grant_from_search(
    *,
    result: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    proposal: Mapping[str, Any],
    preconditions: Mapping[str, Any],
) -> searched_behaviour_grant.SearchedBehaviourGrant:
    """Mint the one thing that can stand ``driving_behavior_shared_object_edit`` down
    for a searched parameter. THE ONLY MINTING PATH.

    Raises ``ValueError`` — never a partial or narrower grant — because a mis-assembled
    payload is a programming error at the call site, not a modelling refusal a user should
    read. The refusals users read are :func:`propose`'s and the constraint engine's.

    The checks, in the order they run and with what each one is for:

    1. ``status == COMPLETE`` — a partial search's best row is a snapshot of an
       interrupted process. "완료된 탐색에서만" (DECISIONS.md 2026-08-20).
    2. ``SearchState.from_dict`` — a state from another build is refused, not
       reinterpreted (its own version wall, re-run here).
    3. the precondition record is real: the gate passed, and ITS split re-derives to the
       search's own. A grant is bound to the gate that opened the search.
    4. the split re-derives (``_verified_split``) and both sides are non-empty; no
       CALIBRATION row scored a held-out node.
    5. the acceptance is re-derived from the ledger's own HOLDOUT rows at an unseen seed,
       with BOTH legs scored on exactly the held-out set — an improvement measured on
       different nodes than the baseline is not an improvement (refuter, #51-b).
    6. the winner is in the published bands, was really run on the calibration nodes to a
       GRADED objective, and is exactly the vector the acceptance validated; and the
       baseline the ledger ran is the one the search declares, because that vector is
       what decides which writes check 7 covers.
    7. the writes are exactly the parameters that MOVED — a partial application of a
       searched vector is a different vector, carrying none of the evidence the ledger
       holds — and every one of them was SELECTED by the sensitivity screen.

    **Call order (a contract, not a convenience).** ``accept_winner`` appends the two
    HOLDOUT rows to the LIVE state, so ``result`` must be snapshotted AFTER acceptance
    (``search.result(STATUS_COMPLETE)``, not the payload ``run()`` returned): check 5
    reads those rows, and a result taken before them carries a ledger that cannot
    support the grant. That is refused by name rather than waved through, because the
    ledger is the evidence — a grant whose ledger does not contain the holdout is a
    grant resting on the acceptance dict alone, which is the thing this function
    deliberately does not trust::

        search.run()
        acceptance = accept_winner(search, runner, unseen_seed=...)
        result = search.result(STATUS_COMPLETE)          # <- after, not before
        proposal = propose(result=result, acceptance=acceptance, behavior=behavior)
        grant = grant_from_search(result=result, acceptance=acceptance,
                                  proposal=proposal, preconditions=gate)
    """
    payload = dict(result or {})
    accepted = dict(acceptance or {})
    offer = dict(proposal or {})
    gate = dict(preconditions or {})

    # -- 1. a FINISHED search ------------------------------------------------
    status = str(payload.get("status") or "")
    if status != STATUS_COMPLETE:
        raise ValueError(
            f"grant_from_search: the search is {status or 'unstatused'!r}, not {STATUS_COMPLETE}. "
            "A grant is issued only from a COMPLETED search — a partial result's best row is a "
            "snapshot of an interrupted process, not a winner."
        )

    state = SearchState.from_dict(payload.get("state") or {})

    # -- 3. the precondition gate that opened this search --------------------
    if not gate.get("ok"):
        codes = [str(item.get("code")) for item in gate.get("refusals") or ()]
        raise ValueError(
            "grant_from_search: no passing precondition record accompanies this search "
            f"({', '.join(codes) or 'no payload'}). The gate's refusals are the product; a grant "
            "minted without the record of them would be a search that never had to be justified."
        )
    gate_split = _verified_split(gate.get("split") or {})

    # -- 4. the split, re-derived rather than trusted ------------------------
    split = _verified_split(
        {"calibration": list(state.calibration_nodes), "holdout": list(state.holdout_nodes)}
    )
    calibration_nodes = list(split.get("calibration") or ())
    holdout_nodes = list(split.get("holdout") or ())
    if not calibration_nodes or not holdout_nodes:
        raise ValueError(
            "grant_from_search: this search has no calibration/holdout split at all "
            f"(calibration={calibration_nodes!r}, holdout={holdout_nodes!r}). It scored nothing, "
            "so there is nothing for a grant to stand on."
        )
    if gate_split != split:
        raise ValueError(
            "grant_from_search: the precondition record's split "
            f"{gate_split!r} is not the search's own {split!r}. The record must be the one that "
            "opened THIS search."
        )
    held = set(holdout_nodes)
    for row in state.ledger:
        if row.get("node_set") != "CALIBRATION":
            continue
        leaked = sorted(set(row.get("geh_by_node") or {}) & held, key=_node_sort_key)
        if leaked:
            raise ValueError(
                f"grant_from_search: ledger row {row.get('index')} was scored as CALIBRATION but "
                f"carries held-out node(s) {leaked!r}. The holdout withholds the DATA; a ledger "
                "that scored it is not a holdout."
            )

    # -- 5. the acceptance, RE-DERIVED from the ledger -----------------------
    holdout = accepted.get("holdout")
    holdout = dict(holdout) if isinstance(holdout, Mapping) else {}
    if not accepted.get("accepted") or str(accepted.get("code") or "") != HOLDOUT_PASSED:
        raise ValueError(
            f"grant_from_search: the winner was not accepted (code={accepted.get('code')!r}). "
            "A search whose winner did not survive the holdout has nothing to propose, and a "
            "grant is never issued over a candidate the validation refused."
        )
    unseen_seed = _int(holdout.get("unseen_seed"))
    if unseen_seed == int(state.seed):
        raise ValueError(
            f"grant_from_search: the holdout ran at the search's own seed {unseen_seed!r}. A "
            "vector that only wins on the seed it was tuned with is exactly what the unseen seed "
            "exists to catch."
        )
    if [str(node) for node in holdout.get("holdout_nodes") or ()] != holdout_nodes:
        raise ValueError(
            "grant_from_search: the acceptance names holdout nodes "
            f"{holdout.get('holdout_nodes')!r}, the search's split says {holdout_nodes!r}."
        )

    winner = holdout.get("winner")
    winner = dict(winner) if isinstance(winner, Mapping) else {}
    winner_params = dict(winner.get("params") or {})
    if not winner_params or not in_published_range(winner_params):
        raise ValueError(
            f"grant_from_search: the accepted winner {winner_params!r} is not a vector inside the "
            "published MRWA bands "
            f"({[(d.parameter, d.low, d.high) for d in search_space()]}). The band is the whole "
            "licence for a searched value; a value outside it is refused, never clipped."
        )
    winner_key = _vector_key(winner_params)

    holdout_rows = [
        row
        for row in state.ledger
        if row.get("stage") == STAGE_HOLDOUT
        and row.get("node_set") == "HOLDOUT"
        and _int(row.get("seed")) == unseen_seed
    ]
    if len(holdout_rows) < 2:
        raise ValueError(
            f"grant_from_search: the ledger carries {len(holdout_rows)} HOLDOUT row(s) at seed "
            f"{unseen_seed}; the acceptance claims a completed comparison, which writes two (the "
            "winner and the baseline). The acceptance code is never taken on trust — it is "
            "re-derived from these rows."
        )
    # ``accept_winner`` appends the winner first, then the baseline. The LAST pair is
    # therefore the comparison this acceptance is reporting.
    winner_row, baseline_row = holdout_rows[-2], holdout_rows[-1]

    baseline_ledger = _best_row(
        [
            row
            for row in state.ledger
            if row.get("stage") == STAGE_BASELINE and in_published_range(row.get("params"))
        ]
    )
    expected_baseline = (
        dict(baseline_ledger["params"]) if baseline_ledger else dict(state.baseline_vector)
    )
    # The DECLARED baseline and the one the ledger actually RAN have to be one vector.
    # Check 7 measures what MOVED from ``state.baseline_vector`` while the holdout's
    # baseline leg is pinned to the ledger's own BASELINE row, and until this line
    # nothing compared the two -- so moving the declared vector alone re-labelled a
    # parameter the winner really moved as "unchanged", and a PARTIAL application of the
    # winner vector could then be minted with every other check still passing (refuter,
    # #51-b). Both sides have to move together now, and moving the ledger row breaks the
    # baseline-leg comparison immediately below.
    if baseline_ledger is not None and _vector_key(dict(state.baseline_vector)) != _vector_key(
        expected_baseline
    ):
        raise ValueError(
            f"grant_from_search: the search declares its baseline as "
            f"{dict(state.baseline_vector)!r}, but the ledger's BASELINE row ran "
            f"{expected_baseline!r}. What a winner MOVED is measured from the baseline, so a "
            "declared baseline the ledger never ran would decide which writes this grant covers "
            "with no evidence behind it."
        )
    if _vector_key(dict(winner_row.get("params") or {})) != winner_key:
        raise ValueError(
            "grant_from_search: the ledger's holdout run was over "
            f"{winner_row.get('params')!r}, but the acceptance reports {winner_params!r}. The "
            "vector that was validated is the only vector a grant may cover."
        )
    if _vector_key(dict(baseline_row.get("params") or {})) != _vector_key(expected_baseline):
        raise ValueError(
            "grant_from_search: the holdout's baseline leg ran "
            f"{baseline_row.get('params')!r}, not this search's baseline {expected_baseline!r}. "
            "An improvement measured against something else is not an improvement."
        )
    for row, side in ((winner_row, "winner"), (baseline_row, "baseline")):
        if not row.get("ok") or _number(row.get("objective")) is None:
            raise ValueError(
                f"grant_from_search: the {side} holdout row did not grade "
                f"(ok={row.get('ok')!r}, reason={row.get('reason')!r})."
            )
        # EXACTLY the held-out set, on BOTH legs -- not merely a subset of it.
        # ``_Objective.score`` keys every node it was asked for (an ungradable one carries
        # ``None``), so an honest holdout row always names the whole set; and the two legs
        # only mean anything COMPARED, which requires them to have been scored on the same
        # nodes. A subset check let a winner scored on one easy held-out node "beat" a
        # baseline scored on both, and let the two legs score disjoint nodes entirely
        # (refuter, #51-b) -- an improvement measured on different data is the very thing
        # the baseline-vector check above refuses one line earlier.
        scored = set(row.get("geh_by_node") or {})
        if scored != held:
            raise ValueError(
                f"grant_from_search: the {side} holdout row scored "
                f"{sorted(scored, key=_node_sort_key)!r}, which is not the held-out set "
                f"{holdout_nodes!r}. Both legs of the comparison are scored on the SAME held-out "
                "nodes; an improvement measured on a different set is not an improvement."
            )
    winner_objective = float(_number(winner_row.get("objective")))
    baseline_objective = float(_number(baseline_row.get("objective")))
    if not winner_objective < baseline_objective:
        raise ValueError(
            f"grant_from_search: the ledger says the winner did NOT improve out of sample "
            f"({baseline_objective:g} -> {winner_objective:g}), whatever the acceptance code "
            "claims. HOLDOUT_PASSED is re-derived here, never believed."
        )
    if _int(winner_row.get("pass_count")) < _int(baseline_row.get("pass_count")):
        raise ValueError(
            "grant_from_search: the winner passes fewer held-out nodes than the baseline "
            f"({winner_row.get('pass_count')!r} < {baseline_row.get('pass_count')!r}); a mean that "
            "improved while the pass count fell is not an accepted winner."
        )

    # -- 6. the winner was really RUN, on the calibration side ---------------
    # "Ran" means PRODUCED A NUMBER. ``ok`` alone was satisfiable by an appended row that
    # graded nothing at all -- an empty ``geh_by_node`` and no objective -- which is a
    # claim that the winner was evaluated, not evidence of it (refuter, #51-b). Every
    # honest ``ok`` row carries a finite objective, because that is what ``_Objective.
    # score`` returns one for.
    if not any(
        row.get("node_set") == "CALIBRATION"
        and row.get("ok")
        and _number(row.get("objective")) is not None
        and _vector_key(dict(row.get("params") or {})) == winner_key
        for row in state.ledger
    ):
        raise ValueError(
            f"grant_from_search: no calibration ledger row ever ran the winner {winner_params!r} "
            "to a graded objective. A vector the search never evaluated is not this search's "
            "answer, and a row that graded nothing did not evaluate it."
        )

    # -- 7. the writes are exactly the parameters that MOVED -----------------
    moved = {
        name: value
        for name, value in winner_params.items()
        if _vector_key({name: value})
        != _vector_key({name: _number(state.baseline_vector.get(name)) or 0.0})
    }
    selected = set(state.selected_parameters)
    unselected = sorted(set(moved) - selected)
    if unselected:
        raise ValueError(
            f"grant_from_search: the winner moved {unselected!r}, which the sensitivity screen "
            f"did not select ({sorted(selected)!r}). A parameter this search could not show an "
            "effect for is a parameter this search does not change."
        )

    if str(offer.get("outcome") or "") != "PROPOSAL":
        raise ValueError(
            f"grant_from_search: the proposal's outcome is {offer.get('outcome')!r}, not "
            "'PROPOSAL'. A refusal is not a proposal with the safety removed."
        )
    actions = [dict(item) for item in offer.get("actions") or () if isinstance(item, Mapping)]
    if not actions:
        raise ValueError("grant_from_search: the proposal carries no actions to cover.")

    values: dict[str, float] = {}
    behavior_nos: set[str] = set()
    for action in actions:
        if action.get("tool") != searched_behaviour_grant.GRANT_TOOL:
            raise ValueError(
                f"grant_from_search: member tool {action.get('tool')!r} is not "
                f"{searched_behaviour_grant.GRANT_TOOL!r} — this grant covers searched "
                "driving-behaviour writes and nothing else."
            )
        params = dict(action.get("params") or {})
        parameter = str(params.get("parameter") or "")
        behavior_nos.add(str(params.get("behavior_no") or ""))
        band = searched_behaviour_grant.band_verdict(action)
        if band is not None:
            raise ValueError(
                f"grant_from_search: write {parameter!r}={params.get('value')!r} is refused by the "
                f"published-band check ({band}). The exception reaches the searched parameters at "
                "in-band values, and nothing else."
            )
        if parameter not in moved:
            raise ValueError(
                f"grant_from_search: write {parameter!r} is not one the winner moved "
                f"({sorted(moved)!r}). A write that sets a parameter to the value it already holds "
                "would ride the exception for nothing."
            )
        if _vector_key({parameter: params.get("value")}) != _vector_key(
            {parameter: moved[parameter]}
        ):
            raise ValueError(
                f"grant_from_search: write {parameter!r} carries {params.get('value')!r} but the "
                f"accepted winner is {moved[parameter]!r}. The grant covers the winner's own "
                "values, byte for byte."
            )
        values[parameter] = float(moved[parameter])

    if set(values) != set(moved):
        raise ValueError(
            f"grant_from_search: the proposal writes {sorted(values)!r} but the winner moved "
            f"{sorted(moved)!r}. A PARTIAL application of a searched vector is a different vector "
            "— it carries none of the evidence this ledger holds, because nothing was ever scored "
            "at it."
        )
    if len(behavior_nos) != 1 or not next(iter(behavior_nos)):
        raise ValueError(
            f"grant_from_search: the writes target {sorted(behavior_nos)!r}; a grant covers one "
            "named behaviour."
        )
    behavior_no = next(iter(behavior_nos))

    radius = offer.get("consent_radius")
    if not isinstance(radius, Mapping) or "complete" not in radius:
        raise ValueError(
            "grant_from_search: the proposal carries no consent radius. The exception's whole "
            "safety argument is that a human read WHERE the edit lands; with no radius there is "
            "no sentence to have read."
        )

    return searched_behaviour_grant.build_grant(
        search_id=str(payload.get("search_id") or ""),
        behavior_no=behavior_no,
        values=values,
        provenance={
            "search_id": str(payload.get("search_id") or ""),
            "status": status,
            "objective": OBJECTIVE,
            "candidates": _int(payload.get("candidates") or len(state.ledger)),
            "ledger": ledger_reference(payload),
            "seed": int(state.seed),
            "unseen_seed": unseen_seed,
            "calibration_nodes": calibration_nodes,
            "holdout_nodes": holdout_nodes,
            "holdout_objective_baseline": baseline_objective,
            "holdout_objective_winner": winner_objective,
            "holdout_pass_baseline": _int(baseline_row.get("pass_count")),
            "holdout_pass_winner": _int(winner_row.get("pass_count")),
            "acceptance_code": HOLDOUT_PASSED,
            "selected_parameters": sorted(selected),
            "bands": [
                _dimension_payload(dim) for dim in search_space() if dim.parameter in values
            ],
            "citation": mrwa_reference.MERGING_BEHAVIOUR_CITATION,
            "scope_note": mrwa_reference.MERGING_RANGE_SCOPE_NOTE,
            "observation_note": mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE,
            "principle_citation": mrwa_reference.CALIBRATION_PRINCIPLE_INDEPENDENT_DATA_CITATION,
            "evidence_rank": dict(searched_behaviour_grant.EVIDENCE_RANK),
            "decision": DECISION_2026_08_20,
        },
        consent_radius_payload=dict(radius),
        signatures=[
            signature(searched_behaviour_grant.GRANT_TOOL, dict(action.get("params") or {}))
            for action in actions
        ],
    )


# ---------------------------------------------------------------------------
# 10. The guardrail forecast — named before the human is asked
# ---------------------------------------------------------------------------

SHARED_BEHAVIOR_REFUSAL = "driving_behavior_shared_object_edit"

#: The two constraints #51-b's exception replaces the refusal with (unit #51-b).
SEARCHED_CONSENTED_CONSTRAINT = "searched_behaviour_shared_edit_consented"
SEARCHED_RADIUS_DRIFTED_CONSTRAINT = "searched_behaviour_consent_radius_drifted"

#: The user decision this unit's exception implements, cited wherever it acts.
DECISION_2026_08_20 = "DECISIONS.md 2026-08-20"

GUARDRAIL_WILL_REFUSE = "GUARDRAIL_WILL_REFUSE"
GUARDRAIL_CLEAR = "GUARDRAIL_CLEAR"
GUARDRAIL_UNKNOWN = "GUARDRAIL_UNKNOWN"

#: The two ways this proposal's writes can reach the engine, named like
#: ``saturation_flow``'s so a reader of either forecast reads the same words.
PATH_DIRECT_EDIT = "direct_edit"
PATH_APPROVED_SET = "approved_set"


def preflight_forecast(behavior: Mapping[str, Any] | None) -> dict[str, Any]:
    """Will the constraint engine refuse this proposal? Told BEFORE the ask.

    The collision, stated plainly: every parameter this unit searches is written
    with ``set_driving_behavior_parameter``, and
    ``constraints._check_driving_behavior_shared_object_edit`` refuses that write
    on any behaviour with ``reference_count > 0``.

    **What changed on 2026-08-20 (#51-b), and what did not.** The user resolved the
    collision the way DECISIONS.md 2026-08-19 resolved #44's: option (a), a narrow
    exception. So the answer is no longer one verdict but TWO PATHS, and this forecast
    reports both because they genuinely differ:

    * :data:`PATH_DIRECT_EDIT` — a plain tool call, ``fixes.preflight_findings``, the
      health check, any planner write. **Refused exactly as before.** Nothing about the
      exception is reachable from a payload; it needs a grant that only
      :func:`grant_from_search` can build and only the write gate can carry.
    * :data:`PATH_APPROVED_SET` — these writes dispatched through the approval machinery
      under this search's own grant. Clean via the named exception
      (:data:`SEARCHED_CONSENTED_CONSTRAINT`) — but only while the blast radius can be
      named in FULL, and only while it is still the radius consent was given for
      (:data:`SEARCHED_RADIUS_DRIFTED_CONSTRAINT` refuses by name if it moves).

    The top-level ``verdict`` still describes the DIRECT path, unchanged in meaning since
    #51: every existing reader of that key asked "what happens if this write is issued on
    its own", and that answer did not change.

    #44's refuter invariant is unchanged and now has to hold on both axes: **a forecast
    may be pessimistic, never optimistic.** A path that says clean is a path where the
    engine, handed that path's own provenance, produces no refusal — which is why a
    behaviour whose consumer list is truncated or absent forecasts the approved path as
    REFUSED too, exactly as ``saturation_flow`` does for #44-b.
    """
    payload = behavior if isinstance(behavior, Mapping) else None
    if payload is None:
        why = {
            "ko": "대상 주행행태를 읽지 못해 가드레일 결과를 예보할 수 없습니다.",
            "en": "The target driving behaviour could not be read, so the guardrail outcome "
            "cannot be forecast.",
            "ja": "対象の走行挙動を読めないため、ガードレールの結果を予報できません。",
        }
        return {
            "verdict": GUARDRAIL_UNKNOWN,
            "constraint_id": SHARED_BEHAVIOR_REFUSAL,
            "reference_count": None,
            "radius_complete": None,
            "why": why,
            "paths": {
                PATH_DIRECT_EDIT: {
                    "expected_to_refuse": True,
                    "constraint_id": SHARED_BEHAVIOR_REFUSAL,
                    "why": why,
                },
                # UNKNOWN is forecast PESSIMISTICALLY on both paths: with no behaviour to
                # read, the radius cannot be named, and a radius that cannot be named is
                # a radius the exception is unavailable for.
                PATH_APPROVED_SET: {
                    "expected_to_refuse": True,
                    "constraint_id": SHARED_BEHAVIOR_REFUSAL,
                    "why": why,
                },
            },
            "resolutions": _guardrail_resolutions(),
        }
    reference_count = _int(payload.get("reference_count"))
    radius_complete = bool(searched_behaviour_grant.consent_radius(payload).get("complete"))
    if reference_count <= 0:
        why = {
            "ko": "이 주행행태를 참조하는 링크 행태 유형이 없어 공유 객체 가드레일에 걸리지 않습니다.",
            "en": "No link behaviour type references this driving behaviour, so the shared-object "
            "guardrail does not apply.",
            "ja": "この走行挙動を参照するリンク挙動タイプがないため、共有オブジェクトのガードレールに"
            "かかりません。",
        }
        return {
            "verdict": GUARDRAIL_CLEAR,
            "constraint_id": SHARED_BEHAVIOR_REFUSAL,
            "reference_count": reference_count,
            "radius_complete": radius_complete,
            "why": why,
            "paths": {
                PATH_DIRECT_EDIT: {
                    "expected_to_refuse": False,
                    "constraint_id": SHARED_BEHAVIOR_REFUSAL,
                    "why": why,
                },
                # NOT via the exception — there is nothing here for it to excuse, and
                # saying otherwise would credit a guardrail bypass for an edit that never
                # needed one.
                PATH_APPROVED_SET: {
                    "expected_to_refuse": False,
                    "constraint_id": SHARED_BEHAVIOR_REFUSAL,
                    "why": why,
                },
            },
            "resolutions": [],
        }

    direct_why = {
        "ko": f"직접 발행하면 이 쓰기는 거부됩니다. 대상 주행행태는 {reference_count}곳에서 참조되는 "
        "공유 객체이고, set_driving_behavior_parameter의 공유 객체 가드레일이 이를 막습니다. "
        f"{DECISION_2026_08_20}이 낸 예외는 승인 기계를 통과한 이 탐색의 grant에만 적용되며, "
        "그 밖의 모든 경로에서는 종전과 똑같이 거부됩니다.",
        "en": f"Issued on its own, this write is REFUSED. The target driving behaviour is a shared "
        f"object ({reference_count} reference(s)) and the shared-object guardrail on "
        f"set_driving_behavior_parameter blocks it. The exception {DECISION_2026_08_20} decided on "
        "applies ONLY to this search's own grant dispatched through the approval machinery; every "
        "other path refuses exactly as before.",
        "ja": f"単独で発行すればこの書き込みは拒否されます。対象の走行挙動は{reference_count}箇所から"
        "参照される共有オブジェクトであり、set_driving_behavior_parameterの共有オブジェクト"
        f"ガードレールが阻みます。{DECISION_2026_08_20}が出した例外は、承認機構を通したこの探索の"
        "grantにのみ適用され、それ以外の経路では従来どおり拒否されます。",
    }

    if not radius_complete:
        approved = {
            "expected_to_refuse": True,
            "constraint_id": SHARED_BEHAVIOR_REFUSAL,
            "why": {
                "ko": f"승인 경로로도 여전히 거부됩니다: 이 주행행태의 적용 반경을 전부 명명할 수 "
                f"없습니다(참조 {reference_count}곳 중 명단에 오른 것만 합산 가능). 예외의 근거는 "
                "사람이 '어디에 함께 적용되는지'를 읽었다는 사실뿐이므로, 그 문장을 사실대로 쓸 수 "
                "없는 곳에는 예외가 적용되지 않습니다.",
                "en": "STILL REFUSED, even through the approval machinery: this behaviour's blast "
                f"radius cannot be named in full ({reference_count} reference(s), of which only the "
                "named ones can be summed). The exception rests on a human having READ where the "
                "edit lands, so it does not apply where that sentence cannot be written truthfully.",
                "ja": f"承認経路でも依然として拒否されます: この走行挙動の適用半径を完全に命名でき"
                f"ません(参照{reference_count}件のうち名簿に載ったもののみ集計可能)。例外の根拠は人が"
                "「どこに同時に適用されるか」を読んだという事実だけであるため、その文を事実どおり"
                "書けない場合には適用されません。",
            },
        }
    else:
        approved = {
            "expected_to_refuse": False,
            "constraint_id": SEARCHED_CONSENTED_CONSTRAINT,
            "refuses_if_radius_drifts": SEARCHED_RADIUS_DRIFTED_CONSTRAINT,
            "why": {
                "ko": f"승인 경로에서는 통과합니다: 이 탐색 자체의 grant"
                "(parameter_search.grant_from_search)로 승인 기계를 통과해 발행되면 "
                f"driving_behavior_shared_object_edit가 물러서고 {SEARCHED_CONSENTED_CONSTRAINT}이 "
                f"대신 반경을 명명합니다 — 링크 행태 유형 {reference_count}곳이 함께, 알고 승인한 "
                f"채로 바뀝니다({DECISION_2026_08_20}). 이를 정직하게 유지하는 것은 전부 거부이지 "
                "경고가 아닙니다: grant는 이 쓰기들을 정규 서명으로 명명하므로 다른 것은 예외를 탈 수 "
                "없고, 값은 도착할 때마다 공표 범위와 대조되며, 승인과 적용 사이에 참조 그래프가 "
                f"움직이면 {SEARCHED_RADIUS_DRIFTED_CONSTRAINT}가 이름을 대며 거부하고, 반경을 전부 "
                "명명할 수 없으면 예외 자체가 없습니다.",
                "en": "Clean via the named exception: dispatched through the approval machinery under "
                "this search's own grant (parameter_search.grant_from_search), "
                f"driving_behavior_shared_object_edit stands down and {SEARCHED_CONSENTED_CONSTRAINT} "
                f"names the radius instead — {reference_count} link-behavior-type consumer(s) changed "
                f"together, knowingly ({DECISION_2026_08_20}). Four things keep it honest and all four "
                "refuse rather than warn: the grant names these writes by canonical signature, so "
                "nothing else rides the exception; every arriving value is re-checked against the "
                "published band; if the reference graph moves between approval and apply, "
                f"{SEARCHED_RADIUS_DRIFTED_CONSTRAINT} refuses by name; and the exception is "
                "unavailable at all unless every consumer is named.",
                "ja": "承認経路では通過します: この探索自身のgrant"
                "(parameter_search.grant_from_search)で承認機構を通して発行されると "
                f"driving_behavior_shared_object_edit が退き、代わりに{SEARCHED_CONSENTED_CONSTRAINT}"
                f"が半径を命名します — リンク挙動タイプ{reference_count}件が承知のうえで同時に変更され"
                f"ます({DECISION_2026_08_20})。これを正直に保つ四つはいずれも警告ではなく拒否です: "
                "grantが正規署名でこれらの書き込みを命名するため他は例外に乗れず、到着ごとに公表範囲と"
                f"照合され、承認と適用の間に参照グラフが動けば{SEARCHED_RADIUS_DRIFTED_CONSTRAINT}が"
                "名指しで拒否し、半径を完全に命名できなければ例外自体が存在しません。",
            },
        }

    return {
        "verdict": GUARDRAIL_WILL_REFUSE,
        "constraint_id": SHARED_BEHAVIOR_REFUSAL,
        "reference_count": reference_count,
        "radius_complete": radius_complete,
        "why": direct_why,
        "paths": {
            PATH_DIRECT_EDIT: {
                "expected_to_refuse": True,
                "constraint_id": SHARED_BEHAVIOR_REFUSAL,
                "why": direct_why,
            },
            PATH_APPROVED_SET: approved,
        },
        "resolutions": _guardrail_resolutions(),
    }


def guardrail_policy() -> dict[str, Any]:
    """The two paths a searched write can take, stated BEFORE any winner exists (#51-c).

    :func:`preflight_forecast` answers this for one behaviour and gives a VERDICT.
    This answers it for a search a modeller has not started yet — the ⑫ trigger card's
    surface, where no behaviour has been chosen and no radius has been read. So it
    states the RULE and the CONDITION and forecasts nothing: the direct path's refusal
    is unconditional and is asserted, while the approved path carries
    ``expected_to_refuse: None`` because its answer genuinely depends on a radius nobody
    has read. #44's invariant (a forecast may be pessimistic, never optimistic) is kept
    here the only way it can be: by not forecasting an outcome that has no evidence yet.

    The user's two answers ride along (:func:`_guardrail_resolutions`), which is the
    point of this on the rail: the decision of 2026-08-20 is a fact about the product,
    and the screen that offers the search is where it belongs.
    """
    return {
        "conditional": True,
        "decision": DECISION_2026_08_20,
        "paths": {
            PATH_DIRECT_EDIT: {
                "expected_to_refuse": True,
                "constraint_id": SHARED_BEHAVIOR_REFUSAL,
                "why": {
                    "ko": "직접 발행하면 거부됩니다 — 탐색 파라미터는 모두 공유 객체인 주행행태에 "
                    f"쓰이고, {SHARED_BEHAVIOR_REFUSAL}가 그 쓰기를 막습니다. 예외는 페이로드로 "
                    "닿을 수 없습니다.",
                    "en": "Issued on its own, the write is REFUSED — every searched parameter lands "
                    f"on a shared driving behaviour and {SHARED_BEHAVIOR_REFUSAL} blocks it. Nothing "
                    "about the exception is reachable from a payload.",
                    "ja": "単独で発行すれば拒否されます — 探索パラメータはすべて共有オブジェクトである"
                    f"走行挙動に書き込まれ、{SHARED_BEHAVIOR_REFUSAL}が阻みます。例外はペイロードから"
                    "は届きません。",
                },
            },
            PATH_APPROVED_SET: {
                # NOT a verdict. See this function's docstring: no behaviour has been
                # read, so there is nothing to be right or wrong about yet.
                "expected_to_refuse": None,
                "constraint_id": SEARCHED_CONSENTED_CONSTRAINT,
                "condition": "RADIUS_NAMED_IN_FULL",
                "refuses_if_radius_drifts": SEARCHED_RADIUS_DRIFTED_CONSTRAINT,
                "why": {
                    "ko": "승인 경로로는 통과합니다 — 단, 적용 반경을 전부 명명할 수 있을 때만. "
                    f"완료된 탐색의 grant로 승인 기계를 통과하면 {SEARCHED_CONSENTED_CONSTRAINT}이 "
                    "반경을 명명하며 대신 서고, 반경을 전부 명명할 수 없으면 예외 자체가 없습니다"
                    f"({DECISION_2026_08_20}).",
                    "en": "Through the approval machinery it passes — but only where the blast "
                    "radius can be named in full. Dispatched under a completed search's own grant, "
                    f"{SEARCHED_CONSENTED_CONSTRAINT} names the radius and stands in; where every "
                    f"consumer cannot be named there is no exception at all ({DECISION_2026_08_20}).",
                    "ja": "承認経路では通過します — ただし適用半径を完全に命名できる場合のみ。完了した"
                    f"探索のgrantで承認機構を通ると{SEARCHED_CONSENTED_CONSTRAINT}が半径を命名して"
                    f"代わりに立ち、全消費者を命名できない場合は例外自体がありません"
                    f"({DECISION_2026_08_20})。",
                },
            },
        },
        "resolutions": _guardrail_resolutions(),
    }


def _guardrail_resolutions() -> list[dict[str, Any]]:
    """The two resolutions #51's forecast offered, with the user's answer on them.

    Kept as the same two keys rather than deleted: the forecast's job is to say what the
    state of the collision IS, and "(a) was chosen and has landed" is a different fact
    from "(a) is available". ``auto_selectable`` stays ``False`` on both — nothing in
    this module selects either one; (a) was selected by a human on 2026-08-20 and
    implemented by unit #51-b, and (b) remains a modeller's call that a search may never
    make on their behalf.
    """
    return [
        {
            "key": "widen_exception",
            "auto_selectable": False,
            "status": "DECIDED",
            "decided": True,
            "decision": DECISION_2026_08_20,
            "landed_in": "#51-b",
            "ko": f"결정됨 (a): 탐색 산출물에도 좁은 예외를 낸다 — {DECISION_2026_08_20} 사용자 결정, "
            "유닛 #51-b에서 구현. 승인 기계를 통과한 이 탐색의 grant에만, 탐색 공간 3종에만, 승자 "
            "값 그대로만 적용됩니다.",
            "en": f"DECIDED (a): the narrow exception is extended to searched outputs — user decision "
            f"{DECISION_2026_08_20}, implemented by unit #51-b. It applies only to this search's own "
            "grant through the approval machinery, only to the three searched parameters, and only "
            "at the accepted winner's own values.",
            "ja": f"決定済み (a): 探索の産出物にも狭い例外を出す — {DECISION_2026_08_20}のユーザー決定、"
            "ユニット#51-bで実装。承認機構を通したこの探索のgrantにのみ、探索空間の3種にのみ、"
            "受理された勝者の値そのままにのみ適用されます。",
        },
        {
            "key": "localize_duplicate",
            "auto_selectable": False,
            "status": "REJECTED_BY_USER",
            "decided": True,
            "decision": DECISION_2026_08_20,
            "ko": "기각 (b): 주행행태를 복제해 일부 링크만 새 행태를 참조하게 하는 국소화. "
            f"{DECISION_2026_08_20}에서 기각되었습니다 — 네트워크 전체를 채점해 찾은 값을 일부에만 "
            "적용하면 값의 의미가 바뀌기 때문입니다. 어느 쪽이든 자동으로 선택하지 않습니다.",
            "en": "REJECTED (b): localize — duplicate the behaviour and repoint some links at the "
            f"copy. Rejected in {DECISION_2026_08_20}: a value found by scoring the whole network "
            "means something else once it is applied to only part of it. Never auto-selected either "
            "way.",
            "ja": "却下 (b): 走行挙動を複製し一部リンクのみ新しい挙動を参照させる局所化。"
            f"{DECISION_2026_08_20}で却下されました — ネットワーク全体を採点して見つけた値を一部に"
            "のみ適用すると値の意味が変わるためです。いずれにせよ自動選択しません。",
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
    the holdout comparison with its own numbers, the LEDGER this all came out of
    (:func:`ledger_reference`), and — last, because it is the sentence a reader
    must not miss — the fact that this evidence ranks BELOW Table 5-6's
    (``searched_behaviour_grant.EVIDENCE_RANK``, DECISIONS.md 2026-08-20). Every
    one of those is read off the payloads; nothing here is asserted that the
    ledger does not carry.

    The evidence-rank sentence is not decoration. #44's values are a published
    table read with a measured number; these are values this product CHOSE inside
    a band. A rationale that presented the two as equally sourced would be the
    overstatement this repository keeps catching in its own output, so the
    rationale says the difference out loud and then says what fills the gap.
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
    # The ledger this rationale is talking about, named so a reader can ask for THAT one
    # (unit #51-b, DECISIONS.md 2026-08-20: "근거문에 탐색 출처 탑재 — 후보 수·원장 참조·
    # 홀드아웃 통과·'MRWA 범위 X–Y 내 탐색' 인용").
    ledger = ledger_reference(payload)
    ledger_ko = (
        f" 탐색 원장: {ledger['search_id'] or '-'} · {ledger['rows']}행 · "
        f"다이제스트 {ledger['digest']}."
    )
    ledger_en = (
        f" Search ledger: {ledger['search_id'] or '-'} · {ledger['rows']} row(s) · "
        f"digest {ledger['digest']}."
    )
    ledger_ja = (
        f" 探索台帳: {ledger['search_id'] or '-'} · {ledger['rows']}行 · "
        f"ダイジェスト {ledger['digest']}。"
    )
    rank = searched_behaviour_grant.EVIDENCE_RANK
    return {
        "ko": (
            f"MRWA 공표 범위({bands_en}; {citation}) 안에서만 탐색했습니다. 후보 {candidates}개를 "
            f"실행했고, 민감도 스크리닝에서 유의한 파라미터는 {', '.join(significant) or '없음'}입니다"
            f"(기준: 공표 범위를 {bins}개 구간으로 나눈 주효과 ≥ {effect_floor} GEH).{held_ko} "
            f"최선 후보: {values_en or '없음'}. {_holdout_clause('ko')}{ledger_ko} {rank['ko']} "
            f"{mrwa_reference.MERGING_RANGE_SCOPE_NOTE} {mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE}"
        ),
        "en": (
            f"The search moved only inside MRWA's published ranges ({bands_en}; {citation}). "
            f"{candidates} candidate(s) were run; the sensitivity screen found "
            f"{', '.join(significant) or 'no'} significant parameter(s) "
            f"(criterion: a main effect of at least {effect_floor} GEH across {bins} strata of the "
            f"published band).{held_en} Best candidate: {values_en or 'none'}. "
            f"{_holdout_clause('en')}{ledger_en} {rank['en']} "
            f"{mrwa_reference.MERGING_RANGE_SCOPE_NOTE} "
            f"{mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE}"
        ),
        "ja": (
            f"MRWAの公表範囲({bands_en}; {citation})の中でのみ探索しました。候補{candidates}件を実行し、"
            f"感度スクリーニングで有意なパラメータは{', '.join(significant) or 'なし'}です"
            f"(基準: 公表範囲を{bins}区間に分けた主効果 ≥ {effect_floor} GEH)。{held_ja} "
            f"最良候補: {values_en or 'なし'}。{_holdout_clause('ja')}{ledger_ja} {rank['ja']} "
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

    **``consent_radius`` (unit #51-b).** The blast radius of the target behaviour, as
    it stands AT PROPOSAL TIME — the left-hand side of the drift check that
    ``searched_behaviour_grant.evaluate`` performs at apply time, and the number the
    approval card's sentence is built from. Captured here because this is the moment the
    human is shown what the edit touches; ``None`` when no behaviour could be read at
    all, which ``grant_from_search`` then refuses to mint over (an unreadable radius is
    not a sentence anyone can have read). It is data on the payload, never a claim the
    payload makes about itself: the grant is minted from it in-process, and the constraint
    engine re-derives the right-hand side from the live shadow model regardless.
    """
    payload = dict(result or {})
    accepted = dict(acceptance or {})
    forecast = preflight_forecast(behavior)
    reasons = rationale(payload, accepted)
    behavior_no = str((behavior or {}).get("no") or "") if isinstance(behavior, Mapping) else ""
    radius = (
        searched_behaviour_grant.consent_radius(behavior)
        if isinstance(behavior, Mapping)
        else None
    )

    if not accepted.get("accepted"):
        return {
            "outcome": "REFUSAL",
            "reason_code": accepted.get("code") or NO_CANDIDATE,
            "actions": [],
            "rationale": reasons,
            "preflight_forecast": forecast,
            "consent_radius": radius,
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
            "consent_radius": radius,
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
        "consent_radius": radius,
        "holdout": accepted.get("holdout"),
        "ledger_size": len(payload.get("state", {}).get("ledger", []) or []),
        "assumptions": [
            "SEARCH_IS_A_LAST_RESORT_NOT_AN_OBSERVATION",
            "PUBLISHED_RANGE_IS_MRWA_MERGING_AREA_SCOPE",
            "EVIDENCE_RANKS_BELOW_TABLE_5_6_LOOKUP",
        ],
    }


# ---------------------------------------------------------------------------
# 11-b. The approval card's payload (unit #51-c)
# ---------------------------------------------------------------------------


def card_payload(
    *,
    result: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    proposal: Mapping[str, Any],
    preconditions: Mapping[str, Any],
) -> dict[str, Any]:
    """One remembered search, as the approval card reads it (#51-c).

    #47's ``tools_calibration.build_proposal_payload`` for the other grant species,
    and the same argument for existing: the approval machinery authorises ONE write
    per pause, so a card that showed only the action it paused on would present one
    coordinate of a searched vector as though it were the change. The vector, the
    ledger it came out of, the holdout that accepted it and the radius it lands on
    are what a human is actually being asked about.

    Deliberately takes the SAME four payloads ``grant_from_search`` does, so a caller
    holding a broker record can splat it into either and the card can never describe a
    search the grant could not be minted from.

    Every sentence in it is the proposal's own (``rationale``, the forecast's ``why``
    on both paths, the resolutions' decision text) plus
    ``searched_behaviour_grant.EVIDENCE_RANK``, which DECISIONS.md 2026-08-20 made a
    closing requirement: this value's evidence ranks below Table 5-6's, and the card
    that approves it has to say so where a reader cannot miss it. Nothing here composes
    a new claim, and nothing here is a grant — a grant exists only as a live object in
    this process (``searched_behaviour_broker``).
    """
    payload = dict(result or {})
    accepted = dict(acceptance or {})
    offer = dict(proposal or {})
    gate = dict(preconditions or {})

    actions = [item for item in offer.get("actions") or () if isinstance(item, Mapping)]
    parameters: list[str] = []
    expected: dict[str, Any] = {}
    behavior_no = ""
    for action in actions:
        params = action.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        name = str(params.get("parameter") or "")
        if not name:
            continue
        behavior_no = behavior_no or str(params.get("behavior_no") or "")
        parameters.append(name)
        expected[name] = params.get("value")

    radius = offer.get("consent_radius")
    radius = dict(radius) if isinstance(radius, Mapping) else None
    holdout = accepted.get("holdout")
    holdout = dict(holdout) if isinstance(holdout, Mapping) else {}
    space = [dict(item) for item in payload.get("space") or () if isinstance(item, Mapping)]
    forecast = offer.get("preflight_forecast")
    forecast = dict(forecast) if isinstance(forecast, Mapping) else {}

    return {
        "status": "PROPOSED" if offer.get("outcome") == "PROPOSAL" else "REFUSED",
        "unit": "#51-b",
        "decision": DECISION_2026_08_20,
        "search_id": str(payload.get("search_id") or "") or None,
        "behavior_no": behavior_no or None,
        "reason_code": offer.get("reason_code"),
        "searched_parameters": parameters,
        "expected_after_apply": expected,
        "unchanged_parameters": list(offer.get("unchanged_parameters") or ()),
        "rationale": dict(offer.get("rationale") or {}),
        # The sentence the decision closes on. Carried as its own field rather than
        # left inside `rationale`'s prose so the card can render it unfolded.
        "evidence_rank": dict(searched_behaviour_grant.EVIDENCE_RANK),
        "preflight_forecast": forecast,
        "consent_radius": radius,
        "consent_radius_phrase": searched_behaviour_grant.radius_phrase(radius),
        "holdout": {
            "accepted": bool(accepted.get("accepted")),
            "code": accepted.get("code"),
            "holdout_nodes": [str(node) for node in holdout.get("holdout_nodes") or ()],
            "calibration_nodes": [
                str(node) for node in (gate.get("split") or {}).get("calibration") or ()
            ],
            "unseen_seed": holdout.get("unseen_seed"),
            "winner_objective": (holdout.get("winner_holdout") or {}).get("objective"),
            "baseline_objective": (holdout.get("baseline_holdout") or {}).get("objective"),
        },
        "ledger": ledger_reference(payload),
        "candidates": payload.get("candidates"),
        "objective": payload.get("objective") or OBJECTIVE,
        "space": space,
        "citation": (space[0].get("citation") if space else None)
        or mrwa_reference.MERGING_BEHAVIOUR_CITATION,
        "assumptions": list(offer.get("assumptions") or ()),
    }


# ---------------------------------------------------------------------------
# 12. The trigger card — ask-first, never auto-start
# ---------------------------------------------------------------------------


def trigger_card(
    preconditions: Mapping[str, Any], *, search_job: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """"Shall I start a search?" — or ``None`` when the gate has not opened.

    ``None`` is the common case and the important one: no card, no nudge, no
    "you could also try". The card appears only on a MEASURED exhausted ladder,
    which is the user's decision ② (a proposal card on ladder exhaustion,
    ask-first, never an automatic start).

    Answering it enqueues a job on ``POST /api/calibration/parameter-search/start``;
    NOTHING here starts one. ``auto_start`` is False and this function has no
    side effect of any kind — it reads a gate payload and returns a question.

    ``search_job`` is the state of the search this workspace has already started,
    if any, built by ``services.agent.search_job.job_card`` and passed in by the
    caller rather than read here: this module stays pure, and the job registry is
    per-process state that a pure gate evaluation must not reach into. It rides
    on the TRACK as well (``calibration_rail.parameter_search_track``), because a
    finished search must stay visible after its own success re-locks the gate —
    a search that fixes every node's GEH closes the door behind it with
    ``GEH_ALREADY_PASSES``, and the card that would have reported the winner is
    ``None`` by then.
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
        "search_job": dict(search_job) if isinstance(search_job, Mapping) else None,
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
    "DECISION_2026_08_20",
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
    "PATH_APPROVED_SET",
    "PATH_DIRECT_EDIT",
    "ParameterSearch",
    "REALITY_CHECK_NOT_CHECKABLE",
    "REFUSAL_CODES",
    "RUNNER_REQUIRES",
    "RunOutcome",
    "SEARCHABLE_PARAMETERS",
    "SEARCHED_CONSENTED_CONSTRAINT",
    "SEARCHED_RADIUS_DRIFTED_CONSTRAINT",
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
    "card_payload",
    "evaluate_preconditions",
    "grant_from_search",
    "guardrail_policy",
    "in_published_range",
    "latin_hypercube",
    "ledger_reference",
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

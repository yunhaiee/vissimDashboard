"""Unit #43 — the GEH-miss diagnosis LADDER.

Where this sits
---------------
``fixes.map_node_counts`` (unit U4) grades one node and answers with exactly
one of three outcomes; ``reality_check`` (unit U5) runs it over every joined
node. Between them they recognise **two** causes of a GEH miss: the demand
level (``set_vehicle_input_volume``) and the turn distribution
(``set_route_relative_flow``). Those are the only two edits ``fixes.py``
proposes at all — measured, 2026-08-18: two verbs out of the registry's sixty.

Unit D4 then made the honest case visible. When the entrance volume is
OBSERVED DATA, the demand edit is refused by name
(``fixes.INPUT_VOLUME_IS_OBSERVED_DATA``), and if the caller also named no
routing decision the whole node ends at "no target object" — a true sentence
that leaves the modeller with nothing to do. This module fills the paragraph
AFTER that refusal: it does not replace D4's structural refusal, it says what
the evidence points at next, and it cites the authority's own step number for
the action.

It proposes NOTHING. No verb, no params, no approval card — diagnosis only.
The treatment (measured saturation flow → MRWA Table 5-6 → car-following
parameters) is the next unit's, and :data:`CAPACITY_LIMIT` is deliberately the
rung that hands off to it.

The rungs
---------
======================  =====================================================
``DEMAND_LEVEL``        U4 arm (a) answered. MRWA step 10.
``ROUTING_SPLIT``       U4 arm (b) answered. MRWA step 11.
``CAPACITY_LIMIT``      Inputs are measured and fixed, the node passes fewer
                        vehicles than were counted, and the run's own delay
                        evidence says they are stuck here. MRWA step 7 →
                        measure stop-line saturation flow (§5.2.5.1).
``SIGNAL_TIMING``       The same, but the delay is concentrated on a minority
                        of approaches and the caller says this node is
                        signalised. MRWA step 6, which PRECEDES step 7.
``UNCLASSIFIABLE``      Not enough evidence for any of the above — and the
                        rung names WHICH evidence, so the gap is closable.
======================  =====================================================

Why delay, and not queue, is the firing test
--------------------------------------------
Both are available (see :func:`run_congestion_evidence`), and both are
reported. Only delay is *graded*, for a measured reason: this codebase already
carries HCM control-delay bands (``results_evidence.los_band``, mirrored from
``services/exporters.py``), so "LOS E or F" is a cited threshold rather than
one invented here. MRWA's Vissim chapter sets **no numeric tolerance for queue
length at all** — its queue criterion is qualitative (transcribed in
docs/product/handoff-2026-08-18.md §4.1(d)) — so a queue-length threshold
would have to be made up, and a made-up threshold in the one field that
decides whether a client is told "your network is over capacity" is exactly
the failure this repository refuses. Queue therefore rides along as
corroborating evidence, and a run that carries queue but no gradable delay
degrades to :data:`UNCLASSIFIABLE`/:data:`DELAY_NOT_GRADABLE` rather than
guessing.

Availability, measured before it was relied on
----------------------------------------------
Against this checkout's real result databases (read-only, ``immutable=1``):

* ``MOVEMENT_EvaluationTimeInterval`` → ``QLEN``/``QLENMAX``, and
  ``MOVEMENT_EvaluationTimeIntervalClass`` → ``VEHDELAY``, are BOTH present and
  populated on every run that has node results at all — 63/63 movements carry a
  queue, 57/63 carry a delay in ``runtime/simulations/17428c42/seed_00/`` (the
  6 nulls are zero-volume movements, which Vissim grades not at all and
  ``read_movement_class_rows`` deliberately keeps null);
* ``QUEUECOUNTER_EvaluationTimeInterval`` is ARMED_BUT_EMPTY or absent
  throughout — which is why ``results_evidence.read_queue_rows`` reads the
  MOVEMENT table and why this module does too;
* both reach ``reality_check`` already: ``tools_run._collect_run_evidence``
  puts them in the same ``run_result`` the modelled volumes come from, under
  ``queue_results`` and ``los_by_movement``, keyed by the same OBJECT_ID;
* they are NOT universal — a probe run with an unarmed evaluation carries
  neither (measured: ``runtime/evaluation-probe/.../1.db``, 0 rows in both
  tables), which is the case :data:`NO_QUEUE_OR_DELAY_EVIDENCE` exists for.

So the ladder reads real evidence, and reports the state of that evidence in
the report's coverage block whether it fired or not.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from services import results_evidence
from services.agent import fixes, mrwa_reference, reality_check_targets

# ---------------------------------------------------------------------------
# The rungs
# ---------------------------------------------------------------------------

#: U4 arm (a) answered: a demand-level proposal was produced.
DEMAND_LEVEL = "DEMAND_LEVEL"
#: U4 arm (b) answered: a routing-split proposal was produced.
ROUTING_SPLIT = "ROUTING_SPLIT"
#: The inputs are measured data, the node moves fewer vehicles than were
#: counted, and the run's delay evidence says they are queued here.
CAPACITY_LIMIT = "CAPACITY_LIMIT"
#: The same, plus: the delay sits on a minority of approaches and the caller
#: told us this node is signalised.
SIGNAL_TIMING = "SIGNAL_TIMING"
#: No rung above is supported by the evidence in hand. Carries
#: ``missing_evidence`` — a named list, never a silence.
UNCLASSIFIABLE = "UNCLASSIFIABLE"

RUNGS = (DEMAND_LEVEL, ROUTING_SPLIT, CAPACITY_LIMIT, SIGNAL_TIMING, UNCLASSIFIABLE)


# ---------------------------------------------------------------------------
# Missing-evidence codes — what an UNCLASSIFIABLE rung is waiting for
# ---------------------------------------------------------------------------

#: The node total's GEH is undefined, so there is no miss to classify yet.
GEH_UNDEFINED = "GEH_UNDEFINED"
#: No demand target was named, so ``fixes.entrance_measurement`` never ran and
#: nothing establishes that this node's inputs are measured data.
NO_DEMAND_TARGET = "NO_DEMAND_TARGET"
#: A target existed but the entrance came back ``ENTRANCE_UNVERIFIED``: the
#: premise every capacity claim rests on ("the input is fixed") is unproven.
ENTRANCE_NOT_VERIFIED_MEASURED = "ENTRANCE_NOT_VERIFIED_MEASURED"
#: The node moved AT LEAST as many vehicles as were counted. Whatever the miss
#: is, it is not the network failing to pass the demand.
NO_THROUGHPUT_SHORTFALL = "NO_THROUGHPUT_SHORTFALL"
#: The run carries neither queue nor delay rows for this node.
NO_QUEUE_OR_DELAY_EVIDENCE = "NO_QUEUE_OR_DELAY_EVIDENCE"
#: Queue rows exist but no movement carries a delay, and queue length has no
#: cited numeric threshold in the guideline this system defers to.
DELAY_NOT_GRADABLE = "DELAY_NOT_GRADABLE"
#: Delay is gradable and no movement reaches the LOS E/F band: the node is not
#: holding traffic back, so the shortfall came from somewhere upstream.
NO_CONGESTION_AT_THIS_NODE = "NO_CONGESTION_AT_THIS_NODE"
#: Not a blocker — a CAVEAT carried ON a fired capacity rung. The delay is
#: concentrated the way a green-split problem concentrates it, but nothing in
#: the payload says whether this node is signalised.
NODE_CONTROL_UNKNOWN = "NODE_CONTROL_UNKNOWN"
#: Also a CAVEAT, not a blocker. Some of this node's movements — or whole
#: approaches — carry no delay in this run, so the delay picture is partial.
#: Vissim grades a zero-volume movement not at all, which is exactly the state
#: a STARVED approach at an oversaturated intersection is in, so "no delay row"
#: must never be read as "no delay": see :func:`node_congestion`.
DELAY_COVERAGE_INCOMPLETE = "DELAY_COVERAGE_INCOMPLETE"

MISSING_EVIDENCE_CODES = (
    GEH_UNDEFINED,
    NO_DEMAND_TARGET,
    ENTRANCE_NOT_VERIFIED_MEASURED,
    NO_THROUGHPUT_SHORTFALL,
    NO_QUEUE_OR_DELAY_EVIDENCE,
    DELAY_NOT_GRADABLE,
    NO_CONGESTION_AT_THIS_NODE,
    NODE_CONTROL_UNKNOWN,
    DELAY_COVERAGE_INCOMPLETE,
)


# ---------------------------------------------------------------------------
# Node control — CALLER knowledge, exactly like ``targets``
# ---------------------------------------------------------------------------
#
# Whether a node is signalised is a property of the NETWORK, and a run result
# does not carry it: ``movement_volumes``/``queue_results``/``los_by_movement``
# are keyed by movement OBJECT_ID and say nothing about signal heads. The
# caller reads it (``get_signal_heads`` returns each head's link; the shadow
# model knows which links reach which node) and passes it, the same way it
# already passes which vehicle input feeds a node. Absent, the answer is
# "unknown" and the ladder says so — never "unsignalised".

CONTROL_SIGNALIZED = "signalized"
CONTROL_UNSIGNALIZED = "unsignalized"
CONTROL_UNKNOWN = "unknown"

NODE_CONTROLS = (CONTROL_SIGNALIZED, CONTROL_UNSIGNALIZED, CONTROL_UNKNOWN)


def normalize_control(value: Any) -> str:
    """Any caller spelling → one of :data:`NODE_CONTROLS`.

    Anything unrecognised becomes :data:`CONTROL_UNKNOWN` rather than raising:
    a mis-spelled hint must degrade the diagnosis, never break a read-only
    report a modeller is waiting on.
    """
    text = str(value or "").strip().lower()
    if text in (CONTROL_SIGNALIZED, "signalised", "signal", "signalized_intersection"):
        return CONTROL_SIGNALIZED
    if text in (CONTROL_UNSIGNALIZED, "unsignalised", "priority", "give_way", "stop"):
        return CONTROL_UNSIGNALIZED
    return CONTROL_UNKNOWN


# ---------------------------------------------------------------------------
# Congestion-evidence availability
# ---------------------------------------------------------------------------

#: Both queue rows and gradable delay rows reached this module.
QUEUE_AND_DELAY = "QUEUE_AND_DELAY"
#: Delay only (queue table absent or armed-but-empty). The rung can still fire:
#: delay is the graded half.
DELAY_ONLY = "DELAY_ONLY"
#: Queue only. The rung CANNOT fire — see the module docstring.
QUEUE_ONLY = "QUEUE_ONLY"
#: Neither.
NEITHER = "NEITHER"

AVAILABILITY_STATES = (QUEUE_AND_DELAY, DELAY_ONLY, QUEUE_ONLY, NEITHER)

#: The ``run_result`` keys this module reads, and the tables behind them — U1
#: owns every spelling, quoted here rather than re-derived. ``VOLUMES_KEY`` is
#: deliberately a second spelling of ``reality_check.MOVEMENT_VOLUMES_KEY``
#: rather than an import of it: ``reality_check`` imports THIS module, and one
#: shared constant is not worth an import cycle. A test pins the two equal.
VOLUMES_KEY = "movement_volumes"
QUEUE_KEY = "queue_results"
LOS_KEY = "los_by_movement"
_QUEUE_TABLE = results_evidence.MOVEMENT_INTERVAL_TABLE
_LOS_TABLE = results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE

#: The delay bands that count as "this movement is at or beyond capacity".
#: HCM signalised control-delay letters, produced by
#: ``results_evidence.los_band`` — not a number spelled again here.
CONGESTED_LOS_BANDS = ("E", "F")


def _mapping_at(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


def _rows_at(payload: Mapping[str, Any], key: str) -> Sequence[Any]:
    """A list-shaped payload key, or ``()``.

    U1's ARMED_BUT_EMPTY marker is an empty ``list`` subclass, so it passes
    through here as the empty sequence it is — the reason it was built that way
    (``results_evidence``'s own note) is that every consumer guards with
    falsiness, and this is one of them.
    """
    value = payload.get(key)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    return value


def _availability(has_queue: bool, has_delay: bool) -> str:
    if has_queue and has_delay:
        return QUEUE_AND_DELAY
    if has_delay:
        return DELAY_ONLY
    if has_queue:
        return QUEUE_ONLY
    return NEITHER


def run_congestion_evidence(run_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the RUN carries in the way of queue and delay, stated once.

    This is the availability measurement the ladder rests on, published in the
    report's coverage block so a reader can tell "the ladder did not fire" from
    "the ladder could not look". It is a description of the payload, never a
    judgement about the network: the per-table state strings are U1's own
    descriptor values (``PRESENT(n)`` / ``ARMED_BUT_EMPTY`` / ``TABLE_ABSENT``),
    quoted, not restated.
    """
    payload = run_result or {}
    descriptor = _mapping_at(payload, "evidence")
    queue_rows = _mapping_at(payload, QUEUE_KEY)
    los_rows = _rows_at(payload, LOS_KEY)

    graded_delays = sum(
        1
        for row in los_rows
        if isinstance(row, Mapping) and _number(row.get("delay_s")) is not None
    )
    queue_values = sum(
        1
        for row in queue_rows.values()
        if isinstance(row, Mapping)
        and (_number(row.get("qlenmax")) is not None or _number(row.get("qlen")) is not None)
    )
    return {
        "availability": _availability(bool(queue_values), bool(graded_delays)),
        "queue": {
            "key": QUEUE_KEY,
            "table": _QUEUE_TABLE,
            "table_state": descriptor.get(_QUEUE_TABLE, results_evidence.TABLE_ABSENT),
            "rows": len(queue_rows),
            "rows_with_a_length": queue_values,
        },
        "delay": {
            "key": LOS_KEY,
            "table": _LOS_TABLE,
            "table_state": descriptor.get(_LOS_TABLE, results_evidence.TABLE_ABSENT),
            "rows": len(los_rows),
            "rows_with_a_delay": graded_delays,
        },
        "graded_by": (
            "services.results_evidence.los_band (HCM SIGNALISED control-delay bands: E>55s, "
            f"F>80s), applied to every node regardless of node_control; LOS "
            f"{'/'.join(CONGESTED_LOS_BANDS)} is the congested test. On an unsignalised node the "
            "HCM's own bands are stricter, so this grading UNDER-reports congestion there rather "
            "than over-reporting it. Queue length is reported but NOT thresholded: MRWA's Vissim "
            "chapter sets no numeric queue tolerance."
        ),
    }


# ---------------------------------------------------------------------------
# One node's congestion picture
# ---------------------------------------------------------------------------


def node_congestion(run_result: Mapping[str, Any] | None, node_id: str) -> dict[str, Any]:
    """Queue and delay for ONE node's movements, grouped by approach.

    The node token comes from the same place U5's join does
    (``results_evidence.node_token`` / the row's own ``node``), and the approach
    is the movement OBJECT_ID's FROM-link — ``fixes.movement_from_link``, the
    function unit D4 already uses for exactly this parse. A key that is not an
    OBJECT_ID (a bare node id, a workbook turn token) has no approach and is
    excluded from the grouping rather than bucketed under a guess.

    Delay COVERAGE is part of the answer, not a detail: ``approaches_without_delay``
    and ``movements_without_delay`` name the parts of this node the run graded
    not at all, and ``concentrated`` is computed so that an ungraded approach
    can never make the delay look concentrated. Vissim grades a zero-volume
    movement not at all, and a starved approach at an oversaturated
    intersection is exactly that — so reading "no delay row" as "no delay"
    would turn a capacity finding into a green-split one in precisely the case
    the capacity rung exists for.
    """
    payload = run_result or {}
    volumes = _mapping_at(payload, VOLUMES_KEY)
    queue_rows = _mapping_at(payload, QUEUE_KEY)
    los_rows = _rows_at(payload, LOS_KEY)

    delay_by_object: dict[str, float] = {}
    for row in los_rows:
        if not isinstance(row, Mapping):
            continue
        value = _number(row.get("delay_s"))
        if value is not None:
            delay_by_object[str(row.get("movement"))] = value

    # Every OBJECT_ID this run can attribute to the node, from all three
    # payloads: a movement can carry a delay without carrying a volume (and
    # vice versa), and dropping either side would understate the evidence.
    object_ids: set[str] = set()
    for key in list(volumes) + list(queue_rows) + list(delay_by_object):
        text = str(key)
        row = volumes.get(text)
        token = (
            str(row["node"])
            if isinstance(row, Mapping) and row.get("node")
            else results_evidence.node_token(text)
        )
        if token == str(node_id):
            object_ids.add(text)

    movements: list[dict[str, Any]] = []
    for object_id in sorted(object_ids):
        approach = fixes.movement_from_link(object_id)
        if approach is None:
            # The node's own aggregate row (its OBJECT_ID IS the bare node id).
            # Evidence that the node exists, not a movement — the same split
            # ``reality_check.run_nodes`` makes.
            continue
        queue = queue_rows.get(object_id)
        queue = queue if isinstance(queue, Mapping) else {}
        delay = delay_by_object.get(object_id)
        movements.append(
            {
                "object_id": object_id,
                "approach_link": approach,
                "delay_s": delay,
                "los": results_evidence.los_band(delay),
                "qlen": _number(queue.get("qlen")),
                "qlenmax": _number(queue.get("qlenmax")),
            }
        )

    approaches: dict[str, dict[str, Any]] = {}
    for movement in movements:
        entry = approaches.setdefault(
            movement["approach_link"],
            {
                "approach_link": movement["approach_link"],
                "movements": 0,
                "worst_delay_s": None,
                "worst_los": None,
                "worst_qlenmax": None,
                "congested": False,
            },
        )
        entry["movements"] += 1
        worst_of = (
            ("worst_delay_s", movement["delay_s"]),
            ("worst_qlenmax", movement["qlenmax"]),
        )
        for field, value in worst_of:
            if value is not None and (entry[field] is None or value > entry[field]):
                entry[field] = value
        entry["worst_los"] = results_evidence.los_band(entry["worst_delay_s"])
        entry["congested"] = entry["worst_los"] in CONGESTED_LOS_BANDS

    graded = [m for m in movements if m["delay_s"] is not None]
    queued = [m for m in movements if m["qlenmax"] is not None or m["qlen"] is not None]
    congested = [m for m in movements if m["los"] in CONGESTED_LOS_BANDS]
    congested_approaches = sorted(link for link, entry in approaches.items() if entry["congested"])
    # An approach whose every movement came back without a delay is UNKNOWN,
    # not free. Vissim grades a zero-volume movement not at all (measured: 5 of
    # this checkout's 63 real movements, all zero-volume), and an approach that
    # passed zero vehicles at an oversaturated intersection is the one most
    # likely to be blocked — so counting it as uncongested would make the
    # concentration test fire hardest in exactly the gridlock case the capacity
    # rung exists for. Named here and subtracted from the test below.
    approaches_without_delay = sorted(
        link for link, entry in approaches.items() if entry["worst_delay_s"] is None
    )

    worst = max(graded, key=lambda m: m["delay_s"], default=None)
    worst_queue = max(
        (m["qlenmax"] for m in movements if m["qlenmax"] is not None), default=None
    )

    return {
        "node_id": str(node_id),
        "availability": _availability(bool(queued), bool(graded)),
        "movements": len(movements),
        "movements_with_delay": len(graded),
        "movements_without_delay": len(movements) - len(graded),
        "movements_with_queue": len(queued),
        "congested_movements": len(congested),
        "worst_delay_s": None if worst is None else round(worst["delay_s"], 2),
        "worst_los": None if worst is None else worst["los"],
        "worst_movement_id": None if worst is None else worst["object_id"],
        "worst_qlenmax_m": None if worst_queue is None else round(worst_queue, 2),
        "approaches": [approaches[link] for link in sorted(approaches)],
        "congested_approaches": congested_approaches,
        "approaches_without_delay": approaches_without_delay,
        # Concentration is only a statement when there is more than one
        # approach to concentrate ON, and only when SOME approach is free: an
        # intersection whose every approach is at LOS F is uniformly
        # oversaturated, which is a capacity finding, not a green-split one.
        #
        # The ungraded approaches ride on the CONGESTED side of the test, which
        # is the worst case for the claim being made: "concentrated" then holds
        # only if it would still hold were every unmeasured approach congested.
        # That keeps a partially-graded run from asserting a distribution it
        # never measured, without inventing a coverage threshold.
        "concentrated": (
            len(approaches) >= 2
            and len(congested_approaches) > 0
            and len(congested_approaches) + len(approaches_without_delay)
            <= len(approaches) / 2
        ),
    }


# ---------------------------------------------------------------------------
# Citations — assembled from the reference pack, never spelled here
# ---------------------------------------------------------------------------


#: ko/ja step labels for the §2.12.1 steps THIS module cites, and only those.
#: They live here rather than in ``mrwa_reference`` on purpose: that module is
#: a TRANSCRIPTION of an English document and adding translations to it would
#: make the transcription no longer verbatim. A step the ladder cites without a
#: label here falls back to the English one rather than to a blank — and a test
#: pins that every step the ladder can emit is covered.
_STEP_LABELS: dict[int, dict[str, str]] = {
    6: {"ko": "신호 교차로", "ja": "信号交差点"},
    7: {"ko": "정지선 포화교통류", "ja": "停止線飽和交通流"},
    10: {"ko": "수요 보정", "ja": "需要補正"},
    11: {"ko": "경로 선택", "ja": "経路選択"},
}

#: ①-⑬, the numbering the guideline's step list is written with in this
#: project's own documents. Index 0 is step 1.
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬"


def _step_cite(number: int) -> str:
    """The machine/English citation: step number, its label, and the page."""
    step, label, citation = mrwa_reference.calibration_step(number)
    return f"MRWA §2.12.1 step {step} ({label}), {citation}"


def _step_cite_ko(number: int) -> str:
    """The Korean rendering, in the form this project's documents use —
    ``MRWA §2.12.1 ⑦(정지선 포화교통류), 문서 p.34-42``. "문서 p.N" is the
    guideline's OWN printed page (the source PDF's page minus 18), which is the
    number a reader holding the document can turn to."""
    step, label, citation = mrwa_reference.calibration_step(number)
    local = _STEP_LABELS.get(step, {}).get("ko", label)
    return f"MRWA §2.12.1 {_CIRCLED[step - 1]}({local}), 문서 {_pages(citation)}"


def _step_cite_ja(number: int) -> str:
    step, label, citation = mrwa_reference.calibration_step(number)
    local = _STEP_LABELS.get(step, {}).get("ja", label)
    return f"MRWA §2.12.1 {_CIRCLED[step - 1]}({local})、文書 {_pages(citation)}"


def _pages(citation: str) -> str:
    """The ``p.N-M`` tail of a citation, or the citation itself if it has none."""
    _, separator, tail = citation.rpartition("p.")
    return f"p.{tail}" if separator else citation


def _saturation_flow_cite() -> str:
    return (
        f"{_step_cite(7)}; {mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION} "
        f"(→ {mrwa_reference.TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW.source})"
    )


def _car_following_cite_ko() -> str:
    return "MRWA §5.2.5.1, 문서 " + _pages(
        mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION
    )


def _car_following_cite_ja() -> str:
    return "MRWA §5.2.5.1、文書 " + _pages(
        mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION
    )


# ---------------------------------------------------------------------------
# Next actions
# ---------------------------------------------------------------------------

MEASURE_SATURATION_FLOW = "MEASURE_SATURATION_FLOW"
REVIEW_SIGNAL_TIMING = "REVIEW_SIGNAL_TIMING"
CALIBRATE_DEMAND = "CALIBRATE_DEMAND"
CONFIRM_ROUTE_CHOICE = "CONFIRM_ROUTE_CHOICE"
SUPPLY_MISSING_EVIDENCE = "SUPPLY_MISSING_EVIDENCE"


def _next_action(
    code: str, step: int | None, citation: str, ko: str, en: str, ja: str
) -> dict[str, Any]:
    return {
        "code": code,
        "mrwa_step": step,
        "citation": citation,
        "text": {"ko": ko, "en": en, "ja": ja},
    }


def _fmt(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{round(number, 2):g}"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rung(
    code: str,
    *,
    ko: str,
    en: str,
    ja: str,
    next_action: dict[str, Any],
    evidence: dict[str, Any],
    missing_evidence: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "rung": code,
        "why": {"ko": ko, "en": en, "ja": ja},
        "next_action": next_action,
        "missing_evidence": list(missing_evidence),
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------


def _d4_refusal(outcome: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The D4 "this entrance is the measurement" refusal, wherever it rides.

    ``map_node_counts`` returns it directly when nothing else could be
    proposed, and hangs it on a routing proposal's ``also_refused`` when the
    split lever was available. One helper so the ladder reads the SAME fact in
    both shapes rather than only recognising one of them.
    """
    if outcome.get("reason_code") == fixes.INPUT_VOLUME_IS_OBSERVED_DATA:
        return outcome
    also = outcome.get("also_refused")
    if isinstance(also, Mapping) and also.get("reason_code") == fixes.INPUT_VOLUME_IS_OBSERVED_DATA:
        return also
    return None


def entrance_verdict(outcome: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """``fixes.entrance_measurement``'s verdict out of whichever evidence block
    carries it — the demand arm always attaches it under ``target``.

    PUBLIC since 2026-08-23, and that is the point of it: the D4 gate leg in
    ``parameter_search._entrance_unverified_nodes`` had been reading
    ``evidence["entrance"]`` — a key no producer in this repository has ever
    written — so the leg returned ``[]`` on every real report and never fired.
    Rather than give that module a second (third) opinion about where the
    verdict lives, it calls THIS function. One reader, one answer.

    Both places a verdict really rides are handled: ``map_node_counts``'s demand
    arm hangs it under ``evidence["target"]``, and when the D4 refusal is
    DISPLACED by a routing proposal it travels on that proposal's
    ``also_refused`` — which is why the loop below looks in both. The OTHER real
    shape, ``evidence["entrance_measurement"]`` at the top level, belongs to
    ``fixes._map_latent_demand_overflow`` (a constraint finding, not a node
    outcome) and cannot reach a reality-check node entry; it is named here so
    the next reader does not have to re-derive that.
    """
    for candidate in (outcome, outcome.get("also_refused")):
        if not isinstance(candidate, Mapping):
            continue
        evidence = candidate.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        target = evidence.get("target")
        if isinstance(target, Mapping) and isinstance(target.get("entrance_measurement"), Mapping):
            return target["entrance_measurement"]
    return None


def _target_less_rung(
    *,
    node_id: str,
    total_geh: float | None,
    geh_threshold: float,
    base_evidence: Mapping[str, Any],
    demand_target_reason: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The UNCLASSIFIABLE rung for a node with NO demand-target verdict at
    all — #56 (b), branched on WHY.

    Before #56, every such node reached the SAME next action regardless of
    why no target existed: "name this node's vehicle input with
    get_vehicle_inputs". Measured wrong (DECISIONS.md, "같은 날 추가 측정 —
    CTA는 후보 0 노드에 불가능한 행동을 지시한다"): a node whose counted
    approaches carry ZERO vehicle inputs
    (:data:`reality_check_targets.NO_VEHICLE_INPUT_ON_APPROACH`) cannot be
    answered that way — there is no input to name, and the sentence sent a
    modeller (and the assistant relaying it) looking for an answer that does
    not exist, which is exactly the loop DECISIONS.md's #56 measurement traced
    to a burned iteration cap. A node with SEVERAL candidates
    (:data:`reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES`) needs a
    *different* act entirely: a human choice among them (the #54 picker, or
    #56 (c)'s audit tool to read the candidates first), never an automatic
    pick — this product's own doctrine (``reality_check_targets``' module
    docstring) refuses to choose by dominance.

    ``demand_target_reason`` absent, or carrying a reason this function does
    not recognise, keeps the PRE-#56 sentence VERBATIM — the honest fallback
    for "why is not known here", never a guess at which of the two applies.
    """
    reason = (demand_target_reason or {}).get("reason")
    candidate_count = (demand_target_reason or {}).get("candidate_count")
    # #59: what the modeller is asked to choose BETWEEN is link-groups, not raw
    # inputs — the demo's node 8 offers 2 choices over 6 inputs. Absent (an older
    # caller that threads only #56 (b)'s pair) it falls back to the input count,
    # which is what this sentence counted before groups existed.
    group_count = (demand_target_reason or {}).get("candidate_group_count")
    choice_count = group_count if isinstance(group_count, int) and group_count >= 1 else None

    if reason == reality_check_targets.NO_VEHICLE_INPUT_ON_APPROACH:
        return _rung(
            UNCLASSIFIABLE,
            ko=f"노드 {node_id}의 GEH {_fmt(total_geh)}가 기준 {geh_threshold}를 넘지만, 이 노드의 "
            "카운트된 접근로로 유입되는 차량 인풋이 하나도 없어 '입구 교통량이 실측인지'를 판정하지 "
            "못했습니다. 인풋을 지정해도 답이 나오지 않습니다 — 이 접근로 자체에 연결된 차량 인풋이 "
            "없기 때문입니다.",
            en=f"Node {node_id} misses (GEH {_fmt(total_geh)} >= {geh_threshold}), and NO vehicle "
            "input feeds this node's counted approach links at all, so whether its entrance is "
            "measured data could not be established. Naming an input will not answer this — there "
            "is no input on these approaches to name.",
            ja=f"ノード {node_id} のGEH {_fmt(total_geh)} は閾値 {geh_threshold} を超えますが、この"
            "ノードの計上済み流入リンクに車両インプットが一つも接続されておらず、流入交通量が実測かを"
            "判定できませんでした。インプットを指定しても答えは出ません — その接近路自体にインプット"
            "が接続されていないためです。",
            next_action=_supply_action(
                ko="이 노드의 접근로에는 연결된 차량 인풋이 없습니다. 관측 지점을 추가하거나, 이 "
                "노드의 site→node 매핑이 올바른지 점검하십시오 — 인풋을 지정하는 문제가 아닙니다.",
                en="No vehicle input is connected to this node's approaches. Add an observation "
                "point, or check whether this node's site→node mapping is correct — this is "
                "not a question of naming an input.",
                ja="このノードの接近路には接続された車両インプットがありません。観測地点を追加する"
                "か、このノードのsite→nodeマッピングが正しいか確認してください — インプットを"
                "指定する問題ではありません。",
            ),
            evidence=dict(base_evidence),
            missing_evidence=[NO_DEMAND_TARGET],
        )

    if reason == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES and isinstance(
        candidate_count, int
    ) and candidate_count >= 2:
        # #59: the count that decides the sentence is the number of CHOICES the
        # picker presents (link-groups). The input count stays in the sentence
        # rather than being replaced, because "six inputs over two approaches"
        # is what the reader sees in the model and dropping it would make the
        # screen's two look like a different network.
        offered = choice_count if choice_count is not None else candidate_count
        spread_ko = (
            "" if choice_count is None or choice_count == candidate_count
            else f"(차량 인풋 {candidate_count}개가 차종별로 묶여 있습니다) "
        )
        spread_en = (
            "" if choice_count is None or choice_count == candidate_count
            else f"({candidate_count} vehicle inputs, grouped by vehicle class) "
        )
        spread_ja = (
            "" if choice_count is None or choice_count == candidate_count
            else f"(車両インプット{candidate_count}件が車種別にまとめられています) "
        )
        return _rung(
            UNCLASSIFIABLE,
            ko=f"노드 {node_id}의 GEH {_fmt(total_geh)}가 기준 {geh_threshold}를 넘지만, 이 노드의 "
            f"카운트된 접근로가 {offered}개 {spread_ko}있어 이 제품이 자동으로 "
            "하나를 고르지 않았습니다. '입구 교통량이 실측인지' 판정은 그 선택 뒤로 미뤄집니다.",
            en=f"Node {node_id} misses (GEH {_fmt(total_geh)} >= {geh_threshold}), and "
            f"{offered} counted approaches {spread_en}feed this node — this "
            "product will not pick one automatically, so whether the entrance is measured data "
            "waits on that choice.",
            ja=f"ノード {node_id} のGEH {_fmt(total_geh)} は閾値 {geh_threshold} を超えますが、この"
            f"ノードの計上済み流入リンクが{offered}件{spread_ja}あり、本製品は自動で"
            "一つを選びません。流入交通量が実測かどうかの判定はその選択待ちです。",
            next_action=_supply_action(
                ko=f"후보 {offered}개 중 하나를 사람이 지정해야 합니다 — 화면의 '수요 대상 "
                "차량 인풋'에서 지정하거나, get_demand_target_derivation으로 후보를 확인한 뒤 근거를 "
                "들어 지정하십시오.",
                en=f"A person must name one of the {offered} candidates — use the screen's "
                "demand-target picker, or read the candidates with get_demand_target_derivation and "
                "name one with a stated reason.",
                ja=f"候補{offered}件のうち一つを人が指定する必要があります — 画面の「需要"
                "対象車両インプット」で指定するか、get_demand_target_derivationで候補を確認したうえで"
                "理由を添えて指定してください。",
            ),
            evidence=dict(base_evidence),
            missing_evidence=[NO_DEMAND_TARGET],
        )

    # Unknown, absent, or a reason neither branch above recognises: the caller
    # did not thread #56 (b)'s audit, or the derivation genuinely has no
    # opinion. Fall back to the exact sentence this rung had before #56 --
    # never a guess at which of the two above applies.
    return _rung(
        UNCLASSIFIABLE,
        ko=f"노드 {node_id}의 GEH {_fmt(total_geh)}가 기준 {geh_threshold}를 넘지만, 이 노드에 "
        "차량 인풋 대상이 지정되지 않아 '입구 교통량이 실측인지'를 판정하지 못했습니다. 그 "
        "판정 없이는 용량·신호 어느 쪽도 근거 없이 단정하게 됩니다.",
        en=f"Node {node_id} misses (GEH {_fmt(total_geh)} >= {geh_threshold}), but no "
        "vehicle-input target was named for it, so whether its entrance volume is measured "
        "data was never established — and every rung below rests on that.",
        ja=f"ノード {node_id} のGEH {_fmt(total_geh)} は閾値 {geh_threshold} を超えますが、"
        "車両インプットの対象が指定されておらず、流入交通量が実測かどうかを判定できません。"
        "その判定なしでは容量も信号も根拠なく断定することになります。",
        next_action=_supply_action(
            ko="get_vehicle_inputs로 이 노드에 유입되는 차량 인풋(번호·현재 교통량·링크)을 "
            "지정해 다시 대조하십시오. 링크가 있어야 실측 여부를 판정할 수 있습니다.",
            en="Name this node's vehicle input (number, current volume, and LINK) from "
            "get_vehicle_inputs and re-check — the link is what makes the entrance "
            "checkable.",
            ja="get_vehicle_inputsでこのノードに流入する車両インプット(番号・現在交通量・"
            "リンク)を指定して再度対照してください。リンクがあって初めて実測判定ができます。",
        ),
        evidence=dict(base_evidence),
        missing_evidence=[NO_DEMAND_TARGET],
    )


def classify_node(
    *,
    node: Mapping[str, Any],
    outcome: Mapping[str, Any],
    congestion: Mapping[str, Any],
    geh_threshold: float,
    node_control: str | None = None,
    demand_target_reason: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """One node's evidence → one rung, or ``None`` when there is no miss.

    ``node`` is the MERGED evidence ``reality_check`` already built (observed
    and modelled totals from the workbook and THIS run respectively).
    ``outcome`` is ``fixes.map_node_counts(...).to_dict()`` — read, never
    re-derived: the ladder must not be able to disagree with the proposal the
    same report ships. ``congestion`` is :func:`node_congestion`'s output.

    ``demand_target_reason`` is #56 (b)'s per-node audit —
    ``{"reason": <a reality_check_targets reason code>, "candidate_count":
    <int>}`` — CALLER knowledge exactly like ``node_control`` is: it is read
    off ``services.agent.reality_check_targets.derive_targets``' own account
    of why this node's demand target does or does not exist, never re-derived
    here (the ladder must not disagree with THAT either). Its only effect is
    on the target-less rung below: absent, that rung's sentence is exactly
    what it was before #56.

    Returns ``None`` for a node the model already reproduces. There is nothing
    to diagnose there, and inventing a rung for it would put a "next action" in
    front of a modeller whose next action is to move on.
    """
    control = normalize_control(node_control)
    node_id = str(node.get("node_id") or congestion.get("node_id") or "")
    observed_total = _number(node.get("observed_total"))
    modelled_total = _number(node.get("modelled_total"))
    outcome_evidence = _mapping_at(outcome, "evidence")
    total_geh = _number(outcome_evidence.get("node_total_geh"))

    base_evidence: dict[str, Any] = {
        "node_id": node_id,
        "observed_total": observed_total,
        "modelled_total": modelled_total,
        "node_total_geh": total_geh,
        "geh_threshold": geh_threshold,
        "outcome": outcome.get("outcome"),
        "outcome_scope": outcome.get("scope"),
        "outcome_reason_code": outcome.get("reason_code"),
        "node_control": control,
        "congestion": dict(congestion),
    }

    # -- nothing to diagnose -------------------------------------------------
    if outcome.get("reason_code") == fixes.MODEL_REPRODUCES_OBSERVED:
        return None

    # -- the miss itself is not established ----------------------------------
    if total_geh is None:
        return _rung(
            UNCLASSIFIABLE,
            ko=f"노드 {node_id}의 총량 GEH가 정의되지 않아 미스 자체가 성립하지 않습니다"
            f"(실측 {_fmt(observed_total)}, 모델 {_fmt(modelled_total)}). 분류하려면 이 노드의 "
            "실측 총량과 이번 실행의 모델 총량이 모두 필요합니다.",
            en=f"Node {node_id}'s total GEH is undefined (observed {_fmt(observed_total)}, "
            f"modelled {_fmt(modelled_total)}), so there is no established miss to classify. "
            "Both this node's observed total and THIS run's modelled total are needed.",
            ja=f"ノード {node_id} の総量GEHが定義されず、ミス自体が成立しません"
            f"(実測 {_fmt(observed_total)}、モデル {_fmt(modelled_total)})。分類には実測総量と"
            "今回の実行のモデル総量の両方が必要です。",
            next_action=_supply_action(
                ko="이 노드의 실측 교통량과 실행 결과(movement_volumes)를 모두 확보한 뒤 다시 대조하십시오.",
                en="Obtain both the observed counts and the run's movement_volumes for this node, "
                "then re-check.",
                ja="このノードの実測交通量と実行結果(movement_volumes)を揃えてから再度対照してください。",
            ),
            evidence=base_evidence,
            missing_evidence=[GEH_UNDEFINED],
        )

    # -- rungs 1 and 2: U4 already answered. Behaviour unchanged; NAMED. -----
    if outcome.get("outcome") == fixes.PROPOSAL and outcome.get("scope") == fixes.DEMAND_LEVEL:
        return _rung(
            DEMAND_LEVEL,
            ko=f"노드 총량 GEH {_fmt(total_geh)}(기준 {geh_threshold})가 어긋나고, 이 입구가 실측"
            "이라는 확인이 없어 수요 수준 조정을 제안했습니다.",
            en=f"The node total misses (GEH {_fmt(total_geh)}, threshold {geh_threshold}) and this "
            "entrance is not verified as measured, so a demand-level change was proposed.",
            ja=f"ノード総量GEH {_fmt(total_geh)}(閾値 {geh_threshold})が外れ、この流入部が実測"
            "である確認がないため、需要水準の調整を提案しました。",
            next_action=_next_action(
                CALIBRATE_DEMAND,
                10,
                _step_cite(10),
                ko=f"제안된 수요 값을 검토·승인하거나, 실제 입구 실측치가 있으면 그 값을 대신 넣으십시오 "
                f"({_step_cite_ko(10)}).",
                en="Review and approve the proposed demand value, or supply the real measured "
                f"entrance count instead ({_step_cite(10)}).",
                ja="提案された需要値を確認・承認するか、実測の流入交通量があればその値に置き換えて"
                f"ください({_step_cite_ja(10)})。",
            ),
            evidence=base_evidence,
        )

    if outcome.get("outcome") == fixes.PROPOSAL and (
        outcome.get("scope") == fixes.ROUTING_SPLIT_LEVEL
    ):
        return _rung(
            ROUTING_SPLIT,
            ko="이동류별 배분이 어긋나 회전 배분(경로 상대유량) 조정을 제안했습니다.",
            en="A movement's split misses, so a turn-distribution (route relative flow) change "
            "was proposed.",
            ja="移動別の配分が外れているため、回転配分(経路相対流量)の調整を提案しました。",
            next_action=_next_action(
                CONFIRM_ROUTE_CHOICE,
                11,
                _step_cite(11),
                ko=f"제안된 상대유량을 검토·승인하십시오 ({_step_cite_ko(11)}). 지침은 경로 "
                "선택을 교통량 보정보다 먼저 확정할 것을 요구합니다.",
                en=f"Review and approve the proposed relative flow ({_step_cite(11)}). The "
                "guideline confirms route choice BEFORE volume calibration.",
                ja=f"提案された相対流量を確認・承認してください({_step_cite_ja(11)})。指針は経路選択"
                "を交通量補正より先に確定するよう求めています。",
            ),
            evidence=base_evidence,
        )

    # -- the node TOTAL reconciles, so the miss is a SPLIT -------------------
    #
    # U4 reached its routing arm and could not build a proposal there (no
    # decision named, no decision scope, a schema rejection). The cause is
    # still known — the demand is right and its distribution is not — and the
    # capacity/signal rungs below are not merely unsupported here, they would
    # be FALSE: a node that passes the counted total is not failing to pass the
    # demand. So the descent stops, and the ask is the routing target.
    if total_geh < geh_threshold:
        return _rung(
            ROUTING_SPLIT,
            ko=f"노드 {node_id}의 총량은 맞고(GEH {_fmt(total_geh)} < 기준 {geh_threshold}) 이동류 "
            f"배분이 어긋납니다. 수정 대상 경로 결정이 지정되지 않아 제안은 만들지 못했습니다"
            f"({outcome.get('reason_code')}) — 원인 자체는 회전 배분입니다.",
            en=f"Node {node_id}'s total reconciles (GEH {_fmt(total_geh)} < {geh_threshold}) and a "
            "movement's split does not. No proposal could be built because the routing target was "
            f"missing ({outcome.get('reason_code')}) — but the cause is the turn distribution.",
            ja=f"ノード {node_id} の総量は合っており(GEH {_fmt(total_geh)} < 閾値 {geh_threshold})、"
            f"移動別の配分が外れています。修正対象の経路決定が指定されていないため提案は作れません"
            f"でした({outcome.get('reason_code')}) — 原因自体は回転配分です。",
            next_action=_next_action(
                CONFIRM_ROUTE_CHOICE,
                11,
                _step_cite(11),
                ko=f"get_routing_decisions로 이 회전을 지배하는 경로 결정(번호·경로·현재 상대유량·"
                f"형제 합계·decision_movements)을 지정해 다시 대조하십시오 ({_step_cite_ko(11)}).",
                en="Name the routing decision that governs this turn (decision, route, current "
                "relFlow, sibling total, and decision_movements) from get_routing_decisions and "
                f"re-check ({_step_cite(11)}).",
                ja="get_routing_decisionsでこの回転を支配する経路決定(番号・経路・現在の相対流量・"
                f"兄弟合計・decision_movements)を指定して再度対照してください({_step_cite_ja(11)})。",
            ),
            evidence=base_evidence,
        )

    # -- below the two: the TOTAL misses and no edit was proposed -------------
    d4 = _d4_refusal(outcome)
    entrance = entrance_verdict(outcome)
    base_evidence["entrance_measurement"] = dict(entrance) if entrance else None

    if d4 is None:
        # Without D4's verdict nothing establishes that this node's inflow is
        # data rather than a parameter, and every rung below is predicated on
        # exactly that. Two distinct gaps, two distinct asks.
        if entrance is None:
            return _target_less_rung(
                node_id=node_id,
                total_geh=total_geh,
                geh_threshold=geh_threshold,
                base_evidence=base_evidence,
                demand_target_reason=demand_target_reason,
            )
        return _rung(
            UNCLASSIFIABLE,
            ko=f"노드 {node_id}의 GEH {_fmt(total_geh)}가 기준 {geh_threshold}를 넘지만, 이 입구의 "
            f"교통량이 실측인지 확인되지 않았습니다({entrance.get('why')}). '실측으로 확인되지 않음'은 "
            "'실측이 아님'이 아니므로, 인풋이 고정이라는 전제가 필요한 용량·신호 판정으로 내려가지 "
            "않습니다.",
            en=f"Node {node_id} misses (GEH {_fmt(total_geh)} >= {geh_threshold}), but this "
            f"entrance was not verified as measured ({entrance.get('why')}). "
            "'Not verified as measured' is not 'not measured', so the ladder does not descend to "
            "rungs that require the input to be fixed.",
            ja=f"ノード {node_id} のGEH {_fmt(total_geh)} は閾値 {geh_threshold} を超えますが、この"
            f"流入部の交通量が実測か確認できていません({entrance.get('why')})。「実測と確認できない」"
            "は「実測でない」ではないため、インプットが固定という前提が要る容量・信号の判定には"
            "降りません。",
            next_action=_supply_action(
                ko="이 입구에서 실제로 조사된 진입 교통량(해당 링크의 실측 행)을 공급하거나, 실측이 "
                "아니라면 수요 추정 제안을 검토하십시오.",
                en="Supply the surveyed entry count for this link (an observed row that resolves to "
                "it), or — if it was never surveyed — review the demand-estimate proposal instead.",
                ja="このリンクで実際に調査された流入交通量(該当リンクに解決する実測行)を供給するか、"
                "実測でなければ需要推定の提案を検討してください。",
            ),
            evidence=base_evidence,
            missing_evidence=[ENTRANCE_NOT_VERIFIED_MEASURED],
        )

    # -- D4 established it: the inflow is measured data and is not ours -------
    shortfall = (
        observed_total is not None
        and modelled_total is not None
        and modelled_total < observed_total
    )
    base_evidence["throughput_shortfall"] = shortfall
    base_evidence["throughput_shortfall_veh"] = (
        None if not shortfall else round(observed_total - modelled_total, 2)
    )

    if not shortfall:
        return _rung(
            UNCLASSIFIABLE,
            ko=f"입구 교통량은 실측이고 고정입니다. 다만 이 노드의 모델 처리량 {_fmt(modelled_total)}대는 "
            f"실측 {_fmt(observed_total)}대에 미달하지 않습니다 — 네트워크가 수요를 통과시키지 못하는 "
            "상황이 아니므로 용량 부족으로 분류하지 않습니다.",
            en="The entrance volume is measured and fixed, but this node's modelled throughput "
            f"({_fmt(modelled_total)}) is not short of the observed count ({_fmt(observed_total)}) "
            "— the network is not failing to pass the demand, so this is not classified as a "
            "capacity limit.",
            ja="流入交通量は実測で固定ですが、このノードのモデル処理量 {0} 台は実測 {1} 台を下回って"
            "いません — ネットワークが需要を通せていない状況ではないため、容量不足には分類しません。".format(
                _fmt(modelled_total), _fmt(observed_total)
            ),
            next_action=_next_action(
                CONFIRM_ROUTE_CHOICE,
                11,
                _step_cite(11),
                ko=f"이 노드로 들어오는 경로 선택과 상류 입구 배분을 먼저 확인하십시오 "
                f"({_step_cite_ko(11)}) — 초과 통행량은 다른 경로에서 유입된 것입니다.",
                en="Check route choice and the upstream input split feeding this node first "
                f"({_step_cite(11)}) — the surplus arrived from somewhere else.",
                ja=f"このノードに流入する経路選択と上流の配分をまず確認してください({_step_cite_ja(11)})"
                " — 超過分は別の経路から入っています。",
            ),
            evidence=base_evidence,
            missing_evidence=[NO_THROUGHPUT_SHORTFALL],
        )

    availability = congestion.get("availability")
    if availability == NEITHER:
        return _rung(
            UNCLASSIFIABLE,
            # "for this node", not "in this run": the tables can be armed and
            # populated while THIS node's movements carry nothing (a node whose
            # only run row is its own aggregate row joins with zero movements).
            # The report's coverage block states the run-wide availability;
            # this sentence must not contradict it.
            ko=f"입구는 실측이고 처리량은 실측보다 {_fmt(base_evidence['throughput_shortfall_veh'])}대 "
            f"부족하지만, 이번 실행에 이 노드({node_id})의 대기행렬·지체 자료가 없어 '차량이 여기서 "
            "막혔는지'를 확인할 수 없습니다. 자료 없이 용량 문제라고 단정하지 않습니다.",
            en="The entrance is measured and the node moved "
            f"{_fmt(base_evidence['throughput_shortfall_veh'])} vehicles fewer than were counted, "
            f"but this run carries no queue or delay data FOR NODE {node_id}, so whether the "
            "vehicles are held HERE cannot be established. A capacity claim is not made without it.",
            ja="流入部は実測で、処理量は実測より {0} 台不足していますが、今回の実行にこのノード({1})の"
            "待ち行列・遅れのデータがなく、車両がここで滞留しているかを確認できません。データなしに"
            "容量問題とは断定しません。".format(
                _fmt(base_evidence["throughput_shortfall_veh"]), node_id
            ),
            next_action=_supply_action(
                ko="네트워크의 노드 평가(nodeResults, collectData=true)를 켜고 다시 실행한 뒤 대조"
                f"하십시오 — 이 판정에 필요한 표는 {_LOS_TABLE}(지체)와 {_QUEUE_TABLE}(대기행렬)입니다.",
                en="Arm the network's node evaluation (nodeResults collectData=true), re-run, and "
                f"re-check — this rung needs {_LOS_TABLE} (delay) and {_QUEUE_TABLE} (queue).",
                ja="ネットワークのノード評価(nodeResults collectData=true)を有効にして再実行し、"
                f"再度対照してください — この判定には {_LOS_TABLE}(遅れ)と {_QUEUE_TABLE}"
                "(待ち行列)が必要です。",
            ),
            evidence=base_evidence,
            missing_evidence=[NO_QUEUE_OR_DELAY_EVIDENCE],
        )

    if availability == QUEUE_ONLY:
        return _rung(
            UNCLASSIFIABLE,
            ko="대기행렬 자료는 있으나 이동류별 지체가 없습니다. 대기행렬 길이에는 MRWA Vissim 장이 "
            "수치 허용오차를 두지 않아(정성적 기준) 임의의 문턱값 없이 혼잡 여부를 등급화할 수 "
            "없으므로, 지어내지 않고 분류 불가로 보고합니다.",
            en="Queue data is present but no per-movement delay is. MRWA's Vissim chapter sets no "
            "numeric queue tolerance (its queue criterion is qualitative), so congestion cannot be "
            "graded from queue alone without inventing a threshold — reported unclassifiable "
            "rather than invented.",
            ja="待ち行列のデータはありますが、移動別の遅れがありません。MRWAのVissim章は待ち行列長に"
            "数値許容誤差を定めておらず(定性的基準)、閾値を作らずに混雑を等級化できないため、"
            "分類不可として報告します。",
            next_action=_supply_action(
                ko=f"{_LOS_TABLE}의 VEHDELAY가 수집되도록 노드 평가를 켠 뒤 재실행하십시오. "
                "지체가 있어야 HCM LOS 대역으로 혼잡을 판정할 수 있습니다.",
                en=f"Re-run with node evaluation armed so {_LOS_TABLE}'s VEHDELAY is collected; "
                "delay is what lets congestion be graded against the HCM LOS bands.",
                ja=f"ノード評価を有効にして {_LOS_TABLE} のVEHDELAYが収集されるよう再実行してください。"
                "遅れがあって初めてHCMのLOS帯で混雑を判定できます。",
            ),
            evidence=base_evidence,
            missing_evidence=[DELAY_NOT_GRADABLE],
        )

    if not congestion.get("congested_movements"):
        # Scoped to the movements this run GRADED. An ungraded movement is not
        # a quiet one -- Vissim grades a zero-volume movement not at all -- so
        # "nothing reached the congested band" is a statement about the graded
        # subset, and when that subset is partial the gap is named rather than
        # rolled into the finding.
        ungraded_movements = int(congestion.get("movements_without_delay") or 0)
        partial_ko = (
            "" if not ungraded_movements
            else f" 다만 이동류 {ungraded_movements}건은 이번 실행에 지체 자료가 없어 어느 쪽 근거도 "
            "되지 못합니다."
        )
        partial_en = (
            "" if not ungraded_movements
            else f" Note that {ungraded_movements} movement(s) carry no delay in this run and are "
            "evidence for neither side."
        )
        partial_ja = (
            "" if not ungraded_movements
            else f" ただし移動 {ungraded_movements} 件は今回の実行に遅れデータがなく、どちらの根拠にも"
            "なりません。"
        )
        return _rung(
            UNCLASSIFIABLE,
            ko=f"입구는 실측이고 처리량은 {_fmt(base_evidence['throughput_shortfall_veh'])}대 부족하지만, "
            f"이번 실행이 등급을 매긴 이동류 {congestion.get('movements_with_delay')}건의 최대 지체는 "
            f"{_fmt(congestion.get('worst_delay_s'))}초"
            f"(LOS {congestion.get('worst_los') or '-'})로 혼잡 대역(LOS "
            f"{'/'.join(CONGESTED_LOS_BANDS)})에 들지 않습니다 — 등급이 매겨진 범위에서는 이 노드가 "
            f"차량을 붙잡고 있다는 근거가 없으므로 여기의 용량 문제로 분류하지 않습니다.{partial_ko}",
            en="The entrance is measured and the node is "
            f"{_fmt(base_evidence['throughput_shortfall_veh'])} vehicles short, but across the "
            f"{congestion.get('movements_with_delay')} movement(s) this run graded the worst delay "
            f"is {_fmt(congestion.get('worst_delay_s'))} s "
            f"(LOS {congestion.get('worst_los') or '-'}), below the congested band (LOS "
            f"{'/'.join(CONGESTED_LOS_BANDS)}) — nothing in the graded evidence has this node "
            f"holding the traffic back, so the shortfall is not classified as capacity HERE."
            f"{partial_en}",
            ja="流入部は実測で処理量は {0} 台不足していますが、今回の実行が等級を付けた移動 {4} 件の"
            "最大遅れは {1} 秒(LOS {2})で混雑帯(LOS {3})に入りません — 等級が付いた範囲では、この"
            "ノードが車両を止めている根拠はないため、ここの容量問題には分類しません。{5}".format(
                _fmt(base_evidence["throughput_shortfall_veh"]),
                _fmt(congestion.get("worst_delay_s")),
                congestion.get("worst_los") or "-",
                "/".join(CONGESTED_LOS_BANDS),
                congestion.get("movements_with_delay"),
                partial_ja,
            ),
            next_action=_next_action(
                CONFIRM_ROUTE_CHOICE,
                11,
                _step_cite(11),
                ko=f"수요가 이 노드에 도달하지 못하고 있습니다. 상류 노드의 용량과 경로 선택을 먼저 "
                f"보십시오 ({_step_cite_ko(11)}).",
                en="The demand is not reaching this node. Look upstream — the upstream node's "
                f"capacity and the route choice that feeds this one ({_step_cite(11)}).",
                ja=f"需要がこのノードに到達していません。上流ノードの容量と経路選択を先に確認して"
                f"ください({_step_cite_ja(11)})。",
            ),
            evidence=base_evidence,
            missing_evidence=(
                [NO_CONGESTION_AT_THIS_NODE, DELAY_COVERAGE_INCOMPLETE]
                if ungraded_movements
                else [NO_CONGESTION_AT_THIS_NODE]
            ),
        )

    # -- the two firing rungs ------------------------------------------------
    concentrated = bool(congestion.get("concentrated"))
    approaches = congestion.get("approaches") or []
    congested_approaches = congestion.get("congested_approaches") or []
    queue_clause_ko = (
        "" if congestion.get("worst_qlenmax_m") is None
        else f", 최대 대기행렬 {_fmt(congestion.get('worst_qlenmax_m'))} m"
    )
    queue_clause_en = (
        "" if congestion.get("worst_qlenmax_m") is None
        else f", max queue {_fmt(congestion.get('worst_qlenmax_m'))} m"
    )
    queue_clause_ja = (
        "" if congestion.get("worst_qlenmax_m") is None
        else f"、最大待ち行列 {_fmt(congestion.get('worst_qlenmax_m'))} m"
    )

    # Coverage caveat for BOTH firing rungs. It is not a blocker: the capacity
    # finding rests on the movements that ARE graded, and the concentration
    # test above has already counted the ungraded approaches on the congested
    # side. But a reader must be told the delay picture is partial and how it
    # was treated, or "1 of 4 approaches" reads as a measurement it is not.
    blind_approaches = list(congestion.get("approaches_without_delay") or [])
    coverage_ko = (
        "" if not blind_approaches
        else f" (참고: 접근로 {len(blind_approaches)}곳(링크 {', '.join(blind_approaches)})에는 이번 "
        "실행의 지체 자료가 없습니다. 편중 판정에서는 이들을 '혼잡하지 않음'이 아니라 '알 수 없음'으로 "
        "세었습니다.)"
    )
    coverage_en = (
        "" if not blind_approaches
        else f" (Note: {len(blind_approaches)} approach(es) (link(s) "
        f"{', '.join(blind_approaches)}) carry no delay in this run; when judging concentration "
        "they were counted as UNKNOWN, not as free.)"
    )
    coverage_ja = (
        "" if not blind_approaches
        else f"(注: {len(blind_approaches)} か所の流入部(リンク {', '.join(blind_approaches)})に"
        "今回の実行の遅れデータがありません。集中の判定では、これらを「非混雑」ではなく「不明」として"
        "数えました。)"
    )
    coverage_code = [DELAY_COVERAGE_INCOMPLETE] if blind_approaches else []

    if concentrated and control == CONTROL_SIGNALIZED:
        return _rung(
            SIGNAL_TIMING,
            ko=f"입구 교통량은 실측이고 고정인데 노드 {node_id}는 실측보다 "
            f"{_fmt(base_evidence['throughput_shortfall_veh'])}대 적게 통과시키고 있습니다"
            f"(GEH {_fmt(total_geh)}). 그런데 지체가 접근로 전체가 아니라 "
            f"접근로 {len(approaches)}곳 중 {len(congested_approaches)}곳(링크 "
            f"{', '.join(congested_approaches) or '-'})에 몰려 있고, 이 노드는 신호 교차로로 "
            f"주어졌습니다(최대 지체 {_fmt(congestion.get('worst_delay_s'))}초, LOS "
            f"{congestion.get('worst_los')}{queue_clause_ko}). 교차로 전체의 용량보다 녹색 시간 "
            f"배분을 먼저 의심할 근거입니다.{coverage_ko}",
            en=f"The entrance volume is measured and fixed, yet node {node_id} passes "
            f"{_fmt(base_evidence['throughput_shortfall_veh'])} fewer vehicles than were counted "
            f"(GEH {_fmt(total_geh)}) — and the delay is not spread across the intersection: it "
            f"sits on {len(congested_approaches)} of {len(approaches)} approaches (link(s) "
            f"{', '.join(congested_approaches) or '-'}; worst delay "
            f"{_fmt(congestion.get('worst_delay_s'))} s, LOS {congestion.get('worst_los')}"
            f"{queue_clause_en}), and the caller states this node is signalised. That is grounds "
            f"to suspect the green-time split before the intersection's capacity itself."
            f"{coverage_en}",
            ja=f"流入交通量は実測で固定ですが、ノード {node_id} は実測より "
            f"{_fmt(base_evidence['throughput_shortfall_veh'])} 台少なく通過させています"
            f"(GEH {_fmt(total_geh)})。しかも遅れは交差点全体ではなく "
            f"{len(congested_approaches)}/{len(approaches)} の流入部(リンク "
            f"{', '.join(congested_approaches) or '-'})に集中しており(最大遅れ "
            f"{_fmt(congestion.get('worst_delay_s'))} 秒、LOS {congestion.get('worst_los')}"
            f"{queue_clause_ja})、このノードは信号交差点と指定されています。交差点全体の容量より"
            f"先に青時間配分を疑う根拠です。{coverage_ja}",
            next_action=_next_action(
                REVIEW_SIGNAL_TIMING,
                6,
                _step_cite(6),
                ko=f"편중된 접근로의 신호 시간(주기·현시 순서·녹색 배분·옵셋)을 검토하십시오 "
                f"({_step_cite_ko(6)}). 지침의 보정 순서상 ⑥ 신호 교차로는 ⑦ 정지선 "
                "포화교통류보다 앞섭니다.",
                en="Review the signal timing (cycle, phase order, green split, offset) on the "
                f"loaded approaches ({_step_cite(6)}). In the guideline's order, step 6 comes "
                "BEFORE step 7 (stop-line saturation flow).",
                ja=f"集中している流入部の信号時間(サイクル・現示順序・青配分・オフセット)を確認して"
                f"ください({_step_cite_ja(6)})。指針の補正順序では⑥信号交差点が⑦停止線飽和交通流"
                "より先です。",
            ),
            evidence=base_evidence,
            missing_evidence=coverage_code,
        )

    caveat = [NODE_CONTROL_UNKNOWN] if (concentrated and control == CONTROL_UNKNOWN) else []
    caveat_ko = (
        "" if not caveat
        else f" (참고: 지체가 접근로 {len(approaches)}곳 중 {len(congested_approaches)}곳에 몰려 있으나 이 "
        "노드가 신호 교차로인지는 이 실행 자료로 알 수 없습니다. 신호 교차로라면 지침 순서상 ⑥ 신호 "
        "시간이 ⑦보다 먼저입니다.)"
    )
    caveat_en = (
        "" if not caveat
        else f" (Note: the delay sits on {len(congested_approaches)} of {len(approaches)} "
        "approaches, but nothing in this run says whether the node is signalised. If it is, "
        "step 6 (signal timing) precedes step 7.)"
    )
    caveat_ja = (
        "" if not caveat
        else f"(注: 遅れは {len(congested_approaches)}/{len(approaches)} の流入部に集中していますが、"
        "この実行資料ではこのノードが信号交差点かどうか分かりません。信号交差点であれば⑥信号時間が"
        "⑦より先です。)"
    )

    return _rung(
        CAPACITY_LIMIT,
        ko=f"입력은 실측이고 고정인데 네트워크가 그 수요를 처리하지 못하고 있습니다 — 용량 문제입니다. "
        f"노드 {node_id}는 실측 {_fmt(observed_total)}대에 대해 모델 처리량이 {_fmt(modelled_total)}대"
        f"({_fmt(base_evidence['throughput_shortfall_veh'])}대 부족, GEH {_fmt(total_geh)} ≥ 기준 "
        f"{geh_threshold})이고, 이동류 {congestion.get('congested_movements')}건이 혼잡 대역"
        f"(최대 지체 {_fmt(congestion.get('worst_delay_s'))}초, LOS {congestion.get('worst_los')}"
        f"{queue_clause_ko})에 있습니다. 수요 오류도 회전 배분 오류도 아닙니다."
        f"{caveat_ko}{coverage_ko}",
        en="The input is measured and fixed, and the network is not passing that demand — this is "
        f"a capacity problem. Node {node_id} was counted at {_fmt(observed_total)} vehicles and "
        f"modelled at {_fmt(modelled_total)} ({_fmt(base_evidence['throughput_shortfall_veh'])} "
        f"short, GEH {_fmt(total_geh)} >= threshold {geh_threshold}), with "
        f"{congestion.get('congested_movements')} movement(s) in the congested band (worst delay "
        f"{_fmt(congestion.get('worst_delay_s'))} s, LOS {congestion.get('worst_los')}"
        f"{queue_clause_en}). It is neither a demand error nor a routing-split error."
        f"{caveat_en}{coverage_en}",
        ja="インプットは実測で固定なのに、ネットワークがその需要を処理できていません — 容量の問題です。"
        "ノード {0} は実測 {1} 台に対しモデル処理量 {2} 台({3} 台不足、GEH {4} ≥ 閾値 {5})で、"
        "移動 {6} 件が混雑帯(最大遅れ {7} 秒、LOS {8}{9})にあります。需要の誤りでも回転配分の誤りでも"
        "ありません。{10}{11}".format(
            node_id,
            _fmt(observed_total),
            _fmt(modelled_total),
            _fmt(base_evidence["throughput_shortfall_veh"]),
            _fmt(total_geh),
            geh_threshold,
            congestion.get("congested_movements"),
            _fmt(congestion.get("worst_delay_s")),
            congestion.get("worst_los"),
            queue_clause_ja,
            caveat_ja,
            coverage_ja,
        ),
        next_action=_next_action(
            MEASURE_SATURATION_FLOW,
            7,
            _saturation_flow_cite(),
            ko="핵심 교차로의 정지선 직진 포화교통류를 측정하십시오 "
            f"({_step_cite_ko(7)}). 인풋이 모두 실측인데 GEH가 높을 때 지침이 지정하는 "
            "조치는, 측정된 포화교통류에 근거해 차량추종 파라미터(정지 간격 2.5 m, 안전거리의 "
            f"가산·승산부)를 정하는 것입니다({_car_following_cite_ko()}, Table 5-6).",
            en="Measure the stop-line straight-movement saturation flow at the key intersection(s) "
            f"({_step_cite(7)}). When every input is measured and GEH is still high, the guideline's "
            "own remedy is to set the car-following parameters (2.5 m standstill distance, the "
            "additive and multiplicative parts of the safety distance) from that measured "
            f"saturation flow ({mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION}, "
            "Table 5-6).",
            ja="主要交差点の停止線における直進飽和交通流を測定してください"
            f"({_step_cite_ja(7)})。インプットがすべて実測でGEHが高い場合、指針が定める対応は、"
            "測定した飽和交通流に基づいて追従モデルのパラメータ(停止時車間2.5 m、安全距離の加算部・"
            f"乗算部)を決めることです({_car_following_cite_ja()}、Table 5-6)。",
        ),
        evidence=base_evidence,
        missing_evidence=caveat + coverage_code,
    )


def _supply_action(*, ko: str, en: str, ja: str) -> dict[str, Any]:
    """A next action that asks for EVIDENCE rather than naming an MRWA step.

    ``mrwa_step`` is ``None`` on purpose: citing a calibration step for "go and
    collect the data first" would attach the authority's name to an instruction
    the authority did not give.
    """
    return _next_action(
        SUPPLY_MISSING_EVIDENCE,
        None,
        "no MRWA step applies until the named evidence exists",
        ko=ko,
        en=en,
        ja=ja,
    )


__all__ = [
    "AVAILABILITY_STATES",
    "CALIBRATE_DEMAND",
    "CAPACITY_LIMIT",
    "CONFIRM_ROUTE_CHOICE",
    "CONGESTED_LOS_BANDS",
    "CONTROL_SIGNALIZED",
    "CONTROL_UNKNOWN",
    "CONTROL_UNSIGNALIZED",
    "DELAY_COVERAGE_INCOMPLETE",
    "DELAY_NOT_GRADABLE",
    "DELAY_ONLY",
    "DEMAND_LEVEL",
    "ENTRANCE_NOT_VERIFIED_MEASURED",
    "GEH_UNDEFINED",
    "MEASURE_SATURATION_FLOW",
    "MISSING_EVIDENCE_CODES",
    "NEITHER",
    "NODE_CONTROLS",
    "NODE_CONTROL_UNKNOWN",
    "NO_CONGESTION_AT_THIS_NODE",
    "NO_DEMAND_TARGET",
    "NO_QUEUE_OR_DELAY_EVIDENCE",
    "NO_THROUGHPUT_SHORTFALL",
    "QUEUE_AND_DELAY",
    "QUEUE_ONLY",
    "ROUTING_SPLIT",
    "RUNGS",
    "SIGNAL_TIMING",
    "SUPPLY_MISSING_EVIDENCE",
    "UNCLASSIFIABLE",
    "VOLUMES_KEY",
    "classify_node",
    "entrance_verdict",
    "node_congestion",
    "normalize_control",
    "run_congestion_evidence",
]

"""Unit #44 — measured saturation flow → MRWA Table 5-6 → car-following parameters.

What this answers
-----------------
"Every input is measured data and GEH is still high." Unit D4 refuses to touch
the entrance volume in that case (``fixes.INPUT_VOLUME_IS_OBSERVED_DATA``), and
unit #43's ladder then names the cause and the authority's own next step:
:data:`geh_ladder.CAPACITY_LIMIT` → MRWA §2.12.1 step ⑦, "measure the stop-line
saturation flow", pointing at §5.2.5.1 and Table 5-6. #43 proposes nothing; it
hands off here.

This module is the treatment. It takes a MEASURED straight-movement saturation
flow, looks it up in the guideline's own Table 5-6, and proposes the three
Wiedemann-74 car-following parameters §5.2.5.1 names — ``ax`` (2.5 m standstill
distance), ``bx_add`` and ``bx_mult`` (the additive and multiplicative parts of
the safety distance). Nothing here tunes by feel: the only free input is a
number somebody measured on site, and the only transform is a table lookup that
cites its row.

Why the measurement must come from the user (measured, not assumed)
-------------------------------------------------------------------
The obvious alternative is to DERIVE the saturation flow from the run we
already have — ``VEHS × 3600/interval ÷ (effective green / cycle)``. That path
is refused, and the refusal is measured rather than assumed. Two INDEPENDENT
blockers, both named in :data:`DERIVATION_BLOCKERS`, either of which is on its
own sufficient:

**(1) Green time.** The offline reader exists and is good —
``services.signal_parser.parse_signal_programs`` decodes the base64
``intSupplyData`` blob straight out of the ``.inpx`` with no COM at all, and
``tools_network.build_shadow_model`` already publishes the result under
``signal_programs``/``signal_program_splits``. But run against every model in
this checkout on 2026-08-18 it yields no usable green split anywhere:

===================================  ==========================================
The real consultant-derived model    6 controllers (TCS398_MurdochDr,
(``client-data/데모-교차로/           TCS972_Murdoch_Discovery, TCS973_Murdoch_
DEMO_캘리브레이션_보정전_             RobinW, TCS1111, TCS601, Murdoch-Farrington
GEH753.inpx``)                       Link), 22 programs. 20 of the 22 are
                                     ``program_kind="stage_based"`` and report
                                     ``sg_splits_unavailable_reason=
                                     "STAGE_BASED_PROGRAM"`` — the parser
                                     deliberately refuses to synthesise green
                                     windows from a stage sequence, and this
                                     module does not second-guess that. The
                                     only 2 programs that DO carry splits are
                                     both on TCS1111, and the parser's own
                                     data-quality flags disown them:
                                     ``SINGLE_SIGNAL_GROUP_PROGRAM`` on both,
                                     plus ``CYCLE_ABOVE_TYPICAL_MAX`` and
                                     ``NEAR_PERMANENT_GREEN`` on the AM one
                                     (375 s cycle, 357 s green — a holding
                                     controller, not an intersection).
Genesis-built networks (Murdoch      ZERO signal controllers. No heads, no
v2/v7, the Daejeon region job)       groups, no programs — nothing to read.
Synthetic demo (``data/practice      2 controllers, ``controller_type=
.inpx``, the no-live-Vissim path)    "unknown"``, 0 programs.
===================================  ==========================================

So: 0 usable green splits, on every model, real and synthetic alike.

**(2) PCU decomposition — the structural one.** Table 5-6 is indexed in **pcu**,
not vehicles. ``results_evidence.read_movement_class_rows`` restricts to
``ARG_VEHICLECLASS = 'ALL'`` and returns one summed ``vehs`` per movement (it
must — the real customer .dbs carry six class rows side by side and summing
them double-counts every vehicle), so the per-class counts a PCU conversion
needs never reach this module. And even if they did, the class codes in those
files are Vissim class numbers (1001/1002/1003/2002/2003), which do not map to
the Austroads classes :data:`mrwa_reference.SATURATION_FLOW_PCU_TABLE` is keyed
by without a per-model mapping nobody has written. This blocker is INDEPENDENT
of green time: it would still stand on a model whose every program parsed
cleanly, which is why it is reported even when green time is available.

Hence :func:`derive_measurement` always refuses today, naming which blockers
apply, and the ask-first observed input is the only path that reaches a
proposal. That is the same shape D4 uses for demand: a number nobody measured
is never silently manufactured, and the difference between "observed" and
"derived" is carried on the proposal rather than blurred.

The lookup checked against a consultant who did it by hand
----------------------------------------------------------
Measured 2026-08-18 against the real consultant-derived model's own driving
behaviours (``tools_network.build_shadow_model``, offline). It carries EIGHT
Wiedemann-74 behaviours named after Table 5-6 rows — "Urban (motorized) -
SatFlow (1,780)", "(1,950)", "(2,050)", "(2,100)" and a merging twin of each —
and every one of their parameter triples equals what :func:`lookup_table_5_6`
returns for that row, with ``ax = 2.5`` on all eight:

    1,780 → 2.50 / 3.50 · 1,950 → 2.25 / 3.25 · 2,050 → 2.00 / 3.00 ·
    2,100 → 1.75 / 2.75

So this module reproduces, from the published table, what a practising MRWA
consultant arrived at by hand — an independent check on the transcription that
no test of ours could give, and a statement of what is actually being
automated: the naming, the citation and the audit trail, not the judgement.

The same model also carries three behaviours named "flyt updated 2.4 3.0",
"2.2 2.8" and "2.3 3.0". Those pairs appear in NO row of Table 5-6. That is
precisely the state this unit exists to replace — plausible numbers, adjusted
by feel, with nothing in the model recording what measurement justified them.
A proposal from here can always name its row, its measured pcu and its source.

The lookup policy, stated once
------------------------------
Table 5-6 publishes SIX discrete saturation-flow points and no interpolation
formula. The guideline says nothing about a measurement that lands between two
of them, so whatever happens there is OUR policy and is named as such — see
:data:`BETWEEN_ROWS_POLICY` and its own docstring for why it rounds DOWN.

Scope of the change
-------------------
:data:`SCOPE_NETWORK_WIDE` is the default because it is what §5.2.5.1 asks for:
"default driving behaviours should generally be adopted, with the exception of
car following model parameters", determined from measured saturation flow "at
key intersection(s)" — one measurement calibrating the urban behaviour, applied
across the network. Narrowing that to selected links (duplicate + retarget) is a
modelling decision that changes what the numbers mean, so it is never chosen
automatically; a caller must ask for :data:`SCOPE_LOCALIZED` explicitly. See
:func:`propose_car_following_parameters` for the guardrail interaction this
creates, which is forecast on the proposal rather than left to surprise the
modeller at preflight.

This module proposes; it never applies. No COM, no I/O, no writes — the
approval loop disposes, exactly as ``fixes`` and ``genesis_fixes`` do.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.agent import car_following_grant, fixes, geh_ladder, mrwa_reference
from services.agent.gates import signature
from services.agent.toolkit import ToolRegistry

# ---------------------------------------------------------------------------
# Keys and scope
# ---------------------------------------------------------------------------

#: This module's ``FixProposal.key``. A validation-driven fix like U4's, but a
#: distinct key: the finding it answers is not "the node total misses" but "the
#: node total misses AND the input is measured AND the node is the bottleneck".
CAR_FOLLOWING_FROM_SATURATION_FLOW = "car_following_from_saturation_flow"

#: The MRWA-intended scope: the named urban driving behaviour, network-wide.
SCOPE_NETWORK_WIDE = "NETWORK_WIDE"
#: Duplicate the behaviour and retarget only chosen links. Never the default.
SCOPE_LOCALIZED = "LOCALIZED"

BEHAVIOR_SCOPES = (SCOPE_NETWORK_WIDE, SCOPE_LOCALIZED)

#: The two ways this set's writes can reach the constraint engine, and therefore the two
#: verdicts ``evidence["preflight_forecast"]["paths"]`` has to carry (unit #44-b).
#: :data:`PATH_DIRECT_EDIT` is the write on its own — still refused on a shared behaviour.
#: :data:`PATH_APPROVED_SET` is the write dispatched through the approval machinery under
#: this set's grant — the one narrow exception DECISIONS.md 2026-08-19 decided on.
PATH_DIRECT_EDIT = "direct_edit"
PATH_APPROVED_SET = "approved_set"

FORECAST_PATHS = (PATH_DIRECT_EDIT, PATH_APPROVED_SET)


def normalize_behavior_scope(value: Any) -> str:
    """Any caller spelling → one of :data:`BEHAVIOR_SCOPES`.

    Anything unrecognised becomes :data:`SCOPE_NETWORK_WIDE` — the MRWA
    default. Deliberately NOT the other way round: an unreadable scope hint
    must never silently narrow a network calibration to a handful of links,
    because that would change what the proposed numbers claim to represent
    while looking like it had obeyed the caller.
    """
    text = str(value or "").strip().upper()
    if text in (SCOPE_LOCALIZED, "LOCAL", "LINKS", "PER_LINK"):
        return SCOPE_LOCALIZED
    return SCOPE_NETWORK_WIDE


# ---------------------------------------------------------------------------
# Provenance of the measurement — the honesty axis, carried end to end
# ---------------------------------------------------------------------------

#: Somebody measured it at the stop line and said how. The only provenance that
#: reaches a proposal today.
OBSERVED = "OBSERVED"
#: Computed from run results and the signal program. Refused — see
#: :data:`DERIVATION_BLOCKERS` and the module docstring.
DERIVED = "DERIVED"

PROVENANCES = (OBSERVED, DERIVED)


# ---------------------------------------------------------------------------
# Reason codes — every refusal carries exactly one
# ---------------------------------------------------------------------------

#: No saturation-flow measurement was supplied at all. The table cannot be
#: entered, so no proposal exists to make.
NO_SATURATION_FLOW_MEASUREMENT = "NO_SATURATION_FLOW_MEASUREMENT"
#: A DERIVED measurement was requested. Always refused today; the evidence
#: names which of :data:`DERIVATION_BLOCKERS` apply and what was measured.
SATURATION_FLOW_NOT_DERIVABLE = "SATURATION_FLOW_NOT_DERIVABLE"
#: The measurement carries no method/attribution, so "measured" is a claim with
#: nothing behind it. MRWA's own hierarchy grades saturation flow BY method
#: (Appendix B §B.3), so an unattributed number cannot be placed in it.
MEASUREMENT_PROVENANCE_MISSING = "MEASUREMENT_PROVENANCE_MISSING"
#: The measurement was taken from fewer samples than Appendix B §B.4 Table B-3
#: requires. A REFUSE-tier data-quality gate, not a warning.
SAMPLE_SIZE_BELOW_MRWA_MINIMUM = "SAMPLE_SIZE_BELOW_MRWA_MINIMUM"
#: The number is outside this system's own plausibility screen for a saturation
#: flow (~800-2200 PCU/h) — a data-entry problem, not a table-range problem, and
#: named separately so the two do not get one message.
#:
#: PROVENANCE, corrected 2026-08-18 after an adversarial re-read of the source:
#: this band is NOT published by Main Roads. It is Prism's own drafted
#: threshold, and the reference pack says so in its own metadata
#: (``SATURATION_FLOW_PLAUSIBILITY_BANDS.source`` is "Appendix B extract's own
#: 'Constraint-engine candidates' section", with ``PAGE_CITE_PENDING``); the
#: extract's heading is literally "Constraint-engine candidates (drafted from
#: the rules above)" and lists "SF outside ~800-2200 PCU/h" under **Warn**, with
#: a tilde. No such band appears anywhere in ``client-data/operational-
#: modelling-v2.pdf``. Earlier text here promoted it to "Appendix B's own
#: published plausibility band" in all three user-facing languages — a citation
#: of authority for something the authority did not say, which is the one error
#: this module's own docstring says it must never make. The SCREEN is kept (a
#: conservative data-entry guard is right); only its attribution is now honest.
SATURATION_FLOW_IMPLAUSIBLE = "SATURATION_FLOW_IMPLAUSIBLE"
#: Plausible, but below Table 5-6's lowest published row (1630 pcu).
SATURATION_FLOW_BELOW_TABLE = "SATURATION_FLOW_BELOW_TABLE"
#: Plausible-band edge, but above Table 5-6's highest published row (2200 pcu).
SATURATION_FLOW_ABOVE_TABLE = "SATURATION_FLOW_ABOVE_TABLE"
#: The ladder's rung is not CAPACITY_LIMIT, so §5.2.5.1's remedy is not what
#: this evidence calls for. Proposing it anyway would be tuning by feel with a
#: citation stapled on.
RUNG_IS_NOT_CAPACITY_LIMIT = "RUNG_IS_NOT_CAPACITY_LIMIT"
#: No driving behaviour was named, so there is no object to edit. Same shape as
#: U4's ``NO_TARGET_OBJECT``: the caller reads the network, this module does not.
NO_DRIVING_BEHAVIOR_TARGET = "NO_DRIVING_BEHAVIOR_TARGET"

REASON_CODES = (
    NO_SATURATION_FLOW_MEASUREMENT,
    SATURATION_FLOW_NOT_DERIVABLE,
    MEASUREMENT_PROVENANCE_MISSING,
    SAMPLE_SIZE_BELOW_MRWA_MINIMUM,
    SATURATION_FLOW_IMPLAUSIBLE,
    SATURATION_FLOW_BELOW_TABLE,
    SATURATION_FLOW_ABOVE_TABLE,
    RUNG_IS_NOT_CAPACITY_LIMIT,
    NO_DRIVING_BEHAVIOR_TARGET,
)

#: Why a DERIVED saturation flow cannot be produced. Independent — either one
#: alone is sufficient to refuse, and both are reported so a future unit knows
#: it must close BOTH, not whichever is easier.
GREEN_TIME_UNAVAILABLE = "GREEN_TIME_UNAVAILABLE"
PCU_DECOMPOSITION_UNAVAILABLE = "PCU_DECOMPOSITION_UNAVAILABLE"

DERIVATION_BLOCKERS = (GREEN_TIME_UNAVAILABLE, PCU_DECOMPOSITION_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Confidence and assumptions
# ---------------------------------------------------------------------------

#: The value is not this system's arithmetic and not an estimate: it is the
#: guideline's own published row, selected by a number the modeller measured.
CONFIDENCE_TABLE_LOOKUP = "MRWA_TABLE_LOOKUP_FROM_MEASURED_SATURATION_FLOW"

#: §5.2.5.1 says "measured STRAIGHT MOVEMENT saturation flow". Nothing in a
#: submitted number proves the movement it was taken on was a through movement,
#: so the requirement rides as a named assumption rather than a silent one.
ASSUME_STRAIGHT_MOVEMENT = "SATURATION_FLOW_IS_A_STRAIGHT_MOVEMENT"
#: Table 5-6 lives in §5.2.5's URBAN driving-behaviour clause. That the named
#: behaviour is the urban (Wiedemann 74) set is the caller's claim; the
#: model-mismatch preflight check is what actually tests it.
ASSUME_BEHAVIOR_IS_URBAN = "TARGET_BEHAVIOR_IS_THE_URBAN_W74_SET"
#: A network-wide behaviour edit reaches every link using that behaviour, which
#: is what §5.2.5.1 intends and is still worth stating out loud.
ASSUME_CHANGE_IS_NETWORK_WIDE = "CHANGE_APPLIES_TO_EVERY_LINK_USING_THIS_BEHAVIOR"
#: The measurement was taken at ONE key intersection and is being applied to the
#: whole urban behaviour — the guideline's own method, and an extrapolation.
ASSUME_KEY_INTERSECTION_REPRESENTS_NETWORK = "KEY_INTERSECTION_REPRESENTS_THE_URBAN_NETWORK"

ASSUMPTIONS = (
    ASSUME_STRAIGHT_MOVEMENT,
    ASSUME_BEHAVIOR_IS_URBAN,
    ASSUME_CHANGE_IS_NETWORK_WIDE,
    ASSUME_KEY_INTERSECTION_REPRESENTS_NETWORK,
)


# ---------------------------------------------------------------------------
# Table 5-6 lookup
# ---------------------------------------------------------------------------

#: Lookup outcomes.
EXACT = "EXACT"
BRACKETED = "BRACKETED"
OUT_OF_RANGE_LOW = "OUT_OF_RANGE_LOW"
OUT_OF_RANGE_HIGH = "OUT_OF_RANGE_HIGH"
IMPLAUSIBLE = "IMPLAUSIBLE"

LOOKUP_STATUSES = (EXACT, BRACKETED, OUT_OF_RANGE_LOW, OUT_OF_RANGE_HIGH, IMPLAUSIBLE)

#: What this module does when a measurement falls BETWEEN two published rows.
#:
#: The guideline gives six points and no rule for the gaps, so this is our
#: choice and it is named, not hidden. It rounds DOWN — the highest published
#: row whose saturation flow is at or below the measurement — for a stated
#: reason: in Table 5-6 a LOWER saturation flow maps to a LARGER safety
#: distance, a larger safety distance produces a longer discharge headway, and
#: a longer headway makes the model pass FEWER vehicles. Rounding down
#: therefore cannot make the network look like it has capacity the measurement
#: does not evidence — and over-stating capacity is the error that matters
#: here, because this proposal exists precisely to explain a node that is
#: already failing to pass its measured demand.
#:
#: Interpolation was rejected on the same ground the rest of this codebase
#: rejects invented numbers: a linear blend of two published rows produces a
#: pair that appears in no MRWA table, which a reader cannot check against the
#: source. Both bracketing rows are reported on every BRACKETED result
#: (``bracket_lower``/``bracket_upper``) so the modeller can see what was
#: passed over and override with the upper row if their judgement says so.
BETWEEN_ROWS_POLICY = "ROUND_DOWN_TO_PUBLISHED_ROW"

BETWEEN_ROWS_POLICY_BASIS = (
    "MRWA Table 5-6 is a lookup of six published points, not an interpolation formula, and the "
    "guideline states no rule for a measurement between two rows. This system's named policy is "
    "to snap DOWN to the highest published row at or below the measurement, because a lower "
    "saturation-flow row carries a LARGER safety distance and therefore cannot overstate the "
    "capacity the measurement evidences. Both bracketing rows are reported so the choice is "
    "auditable and can be overridden."
)


def _plausibility_band() -> tuple[float, float]:
    """This system's plausibility screen for a saturation flow. NOT MRWA's.

    Read out of :data:`mrwa_reference.SATURATION_FLOW_PLAUSIBILITY_BANDS`
    rather than spelled again here, so this module cannot drift from the
    reference pack. Falls back to Table 5-6's own range if that entry is ever
    removed — a narrower, never a wider, claim.

    See :data:`SATURATION_FLOW_IMPLAUSIBLE` for why the name of this band is
    load-bearing: the reference pack records it as a DRAFTED constraint-engine
    candidate with no page cite, so nothing built on it may describe it as a
    Main Roads publication.
    """
    for entry in mrwa_reference.SATURATION_FLOW_PLAUSIBILITY_BANDS.entries:
        if entry.kind == "RANGE" and entry.unit == "PCU/h" and isinstance(entry.value, tuple):
            return (float(entry.value[0]), float(entry.value[1]))
    return mrwa_reference.table_5_6_saturation_flow_range()


def _row_dict(row: tuple[float, float, float]) -> dict[str, float]:
    saturation_flow, additive, multiplicative = row
    return {
        "saturation_flow_pcu": saturation_flow,
        "additive": additive,
        "multiplicative": multiplicative,
    }


def lookup_table_5_6(saturation_flow_pcu: Any) -> dict[str, Any]:
    """One measured saturation flow (pcu) → the Table 5-6 row to apply.

    Never raises and never invents a row. The result always carries ``status``
    (one of :data:`LOOKUP_STATUSES`), the ``policy`` that produced it, and the
    table's own citation; ``row`` is ``None`` on every non-selecting status so
    a caller cannot accidentally read a fabricated pair.

    Note the deliberate ordering of the two out-of-range tests against the
    plausibility screen: the screen's ceiling (2200) is numerically the same as
    Table 5-6's top row (2200 pcu), so anything above the table is also caught
    by the screen and is reported as :data:`SATURATION_FLOW_IMPLAUSIBLE` (the
    data-quality finding) rather than as a mere table-range miss. Below the
    table the two DIFFER (the screen's floor is 800, the table's is 1630), so a
    value in 800..1630 is a real, plausible measurement that the table simply
    does not cover — a genuinely different situation, and it gets its own
    status.

    That top-end coincidence is NOT corroboration and must not be read as such.
    An earlier version of this docstring called it "two independently sourced
    MRWA numbers agreeing"; they are neither independent nor two MRWA numbers.
    Table 5-6's 2200 is Main Roads' (p.187); the screen's 2200 is Prism's own
    drafted candidate (see :data:`SATURATION_FLOW_IMPLAUSIBLE`) and most likely
    took its ceiling FROM that very table. The ordering below is a defensible
    conservative choice on its own; it never rested on a second source.
    """
    value = _number(saturation_flow_pcu)
    rows = mrwa_reference.TABLE_5_6_ROWS
    low_pcu, high_pcu = mrwa_reference.table_5_6_saturation_flow_range()
    band_low, band_high = _plausibility_band()

    base: dict[str, Any] = {
        "measured_pcu": value,
        "status": None,
        "row": None,
        "bracket_lower": None,
        "bracket_upper": None,
        "policy": BETWEEN_ROWS_POLICY,
        "policy_basis": BETWEEN_ROWS_POLICY_BASIS,
        "citation": mrwa_reference.TABLE_5_6_CITATION,
        "table_range_pcu": [low_pcu, high_pcu],
        "plausibility_band_pcu": [band_low, band_high],
        "published_rows": [_row_dict(row) for row in rows],
    }

    if value is None or value <= 0 or value < band_low or value > band_high:
        return {**base, "status": IMPLAUSIBLE}
    if value < low_pcu:
        return {**base, "status": OUT_OF_RANGE_LOW, "bracket_upper": _row_dict(rows[0])}
    if value > high_pcu:
        # Unreachable while the table ceiling equals the screen's ceiling (both
        # 2200), which the IMPLAUSIBLE branch above already caught. Kept because
        # that coincidence is a property of the DATA, not of this function -- if
        # a future transcription extends either, this branch is what keeps the
        # answer correct instead of silently mis-labelled.
        return {**base, "status": OUT_OF_RANGE_HIGH, "bracket_lower": _row_dict(rows[-1])}

    for row in rows:
        if row[0] == value:
            return {**base, "status": EXACT, "row": _row_dict(row)}

    lower = max((row for row in rows if row[0] < value), key=lambda row: row[0])
    upper = min((row for row in rows if row[0] > value), key=lambda row: row[0])
    return {
        **base,
        "status": BRACKETED,
        "row": _row_dict(lower),  # BETWEEN_ROWS_POLICY: round DOWN
        "bracket_lower": _row_dict(lower),
        "bracket_upper": _row_dict(upper),
    }


# ---------------------------------------------------------------------------
# The inverse lookup — what a behaviour's CURRENT parameters sit on
# ---------------------------------------------------------------------------

#: :func:`verify_applied_row` verdicts.
ROW_MATCHED = "MATCHES_PUBLISHED_ROW"
ROW_UNMATCHED = "MATCHES_NO_PUBLISHED_ROW"
ROW_UNREADABLE = "PARAMETERS_UNREADABLE"

ROW_VERIFICATION_STATUSES = (ROW_MATCHED, ROW_UNMATCHED, ROW_UNREADABLE)


def verify_applied_row(
    behavior: Mapping[str, Any] | None, *, expected_row: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Does a behaviour's CURRENT ax/bx_add/bx_mult sit on a published row?

    The inverse of :func:`lookup_table_5_6`, and the answer to the one thing
    this unit could otherwise only warn about in prose.

    :func:`propose_car_following_parameters` emits THREE proposals because
    ``set_driving_behavior_parameter`` writes one field at a time, and every
    one of them says, in three languages, that approving only some leaves the
    behaviour matching no published row. That warning was the entire protection:
    measured 2026-08-18, ``PreapprovedWriteGate`` authorises the three
    signatures INDEPENDENTLY (approving only ``bx_add`` returns ``True`` for it
    and ``False`` for the other two — by design, "multiple DISTINCT approved
    actions in one signature set remain independent"), nothing on
    ``FixProposal`` couples them, and the constraint engine returns no finding
    on the resulting state. So a behaviour that started on the 1630 row and
    received only the ``bx_add`` half of the 1950 row ends at
    ``(2.5, 2.25, 3.75)`` — a pair printed in no MRWA table, indistinguishable
    from the "flyt updated 2.4 3.0" hand-tuning this unit exists to replace, and
    nothing anywhere could say so.

    This function is that missing sentence. It is deliberately a READER — pure,
    no writes, no COM — so it costs nothing and can run wherever the behaviour
    is already in hand: a re-run report, the approval loop after dispatch, or
    ``fixes.preflight_findings``' caller. Structural locking of the three writes
    was the alternative; it would mean changing ``gates``/``routers`` approval
    machinery that every other unit shares, to solve a problem a named detection
    path solves, so naming was chosen over locking.

    ``behavior`` is a shadow-model driving-behaviour dict
    (``tools_network._parse_driving_behaviors`` publishes ``ax``/``bx_add``/
    ``bx_mult`` on every one). ``expected_row`` is optionally the row the
    proposal intended, which lets the result say WHICH half landed.
    """
    payload = behavior if isinstance(behavior, Mapping) else {}
    applied = {name: _number(payload.get(name)) for name in ("ax", "bx_add", "bx_mult")}

    result: dict[str, Any] = {
        "status": ROW_UNREADABLE,
        "applied": applied,
        "row": None,
        "expected_row": dict(expected_row) if expected_row else None,
        "standstill_matches_guideline": None,
        "per_parameter_rows": {},
        "citation": mrwa_reference.TABLE_5_6_CITATION,
        "why": "",
    }

    if applied["bx_add"] is None or applied["bx_mult"] is None:
        result["why"] = (
            "The behaviour does not carry readable bx_add/bx_mult values, so whether it sits on a "
            "published Table 5-6 row cannot be determined. Not an accusation — a missing reading."
        )
        return result

    # Which published rows, if any, each half came from. A partial application's
    # signature is precisely these two disagreeing.
    for name, column in (("bx_add", 1), ("bx_mult", 2)):
        result["per_parameter_rows"][name] = [
            row[0] for row in mrwa_reference.TABLE_5_6_ROWS if _same(row[column], applied[name])
        ]

    if applied["ax"] is not None:
        result["standstill_matches_guideline"] = _same(
            float(mrwa_reference.STANDSTILL_DISTANCE_M.value), applied["ax"]
        )

    matched = [
        row
        for row in mrwa_reference.TABLE_5_6_ROWS
        if _same(row[1], applied["bx_add"]) and _same(row[2], applied["bx_mult"])
    ]
    if matched:
        result["status"] = ROW_MATCHED
        result["row"] = _row_dict(matched[0])
        expected_flow = (expected_row or {}).get("saturation_flow_pcu")
        if expected_row is not None and not _same(_number(expected_flow), matched[0][0]):
            result["why"] = (
                f"The behaviour sits on Table 5-6's {matched[0][0]:g} pcu row — a published row, "
                f"but NOT the {_fmt(expected_flow)} pcu row this proposal intended. The set was "
                "applied to a different row than the one the measurement selected."
            )
        else:
            result["why"] = (
                f"The behaviour's safety-distance pair matches Table 5-6's {matched[0][0]:g} pcu "
                f"row exactly ({mrwa_reference.TABLE_5_6_CITATION})."
            )
        return result

    result["status"] = ROW_UNMATCHED
    halves = result["per_parameter_rows"]
    detail = (
        f"bx_add={_fmt(applied['bx_add'])} belongs to row(s) "
        f"{halves['bx_add'] or 'none'}, bx_mult={_fmt(applied['bx_mult'])} to row(s) "
        f"{halves['bx_mult'] or 'none'}"
    )
    result["why"] = (
        "The behaviour's additive/multiplicative pair appears in NO row of MRWA Table 5-6 "
        f"({detail}). Two published halves from different rows is the signature of a "
        "PARTIALLY APPLIED car-following set — ax, bx_add and bx_mult are one row and must move "
        "together. Either apply the rest of the set or restore the row it came from; until then "
        "the behaviour's discharge headway is not traceable to any measured saturation flow, "
        "which is the state this calibration exists to end."
    )
    return result


# ---------------------------------------------------------------------------
# Green-time availability — measured, published, never assumed
# ---------------------------------------------------------------------------

#: Availability states for the offline green-time read.
GREEN_TIME_NONE = "NONE"
GREEN_TIME_PARTIAL = "PARTIAL"
GREEN_TIME_FULL = "FULL"

GREEN_TIME_STATES = (GREEN_TIME_NONE, GREEN_TIME_PARTIAL, GREEN_TIME_FULL)


def green_time_availability(shadow_model: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the STORED artifacts carry in the way of green splits.

    A description of ``shadow_model["signal_programs"]`` — which
    ``tools_network.build_shadow_model`` fills from
    ``signal_parser.parse_signal_programs``, offline, with no COM — never a
    judgement about the network. The parser's own machine-readable reason codes
    (``STAGE_BASED_PROGRAM`` and the ``PROGRAMS_*`` family) and data-quality
    flags are QUOTED and counted, not re-derived, so this function cannot
    disagree with the module that did the reading.

    Published on every refusal this module emits, so a reader can tell "the
    derivation was not attempted" from "the derivation was attempted and the
    artifacts do not support it" — and, on the day a model with real
    signal-group programs arrives, can see that the picture changed without
    anyone editing a docstring.
    """
    payload = shadow_model or {}
    controllers = payload.get("signal_programs")
    controllers = controllers if isinstance(controllers, Sequence) and not isinstance(
        controllers, (str, bytes)
    ) else []

    programs = 0
    programs_with_splits = 0
    split_rows = 0
    unavailable_reasons: dict[str, int] = {}
    data_quality: dict[str, int] = {}

    for controller in controllers:
        if not isinstance(controller, Mapping):
            continue
        entries = controller.get("programs")
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            continue
        for program in entries:
            if not isinstance(program, Mapping):
                continue
            programs += 1
            splits = program.get("sg_splits")
            splits = splits if isinstance(splits, Sequence) and not isinstance(
                splits, (str, bytes)
            ) else []
            if splits:
                programs_with_splits += 1
                split_rows += len(splits)
            reason = program.get("sg_splits_unavailable_reason")
            if reason:
                unavailable_reasons[str(reason)] = unavailable_reasons.get(str(reason), 0) + 1
            flags = program.get("data_quality")
            if isinstance(flags, Sequence) and not isinstance(flags, (str, bytes)):
                for flag in flags:
                    data_quality[str(flag)] = data_quality.get(str(flag), 0) + 1

    if not programs_with_splits:
        state = GREEN_TIME_NONE
    elif programs_with_splits < programs:
        state = GREEN_TIME_PARTIAL
    else:
        state = GREEN_TIME_FULL

    return {
        "state": state,
        "controllers": len(controllers),
        "programs": programs,
        "programs_with_sg_splits": programs_with_splits,
        "sg_split_rows": split_rows,
        "unavailable_reasons": unavailable_reasons,
        "data_quality_flags": data_quality,
        "read_by": (
            "services.signal_parser.parse_signal_programs, via "
            "services.agent.tools_network.build_shadow_model['signal_programs'] — offline .inpx "
            "XML (the base64 intSupplyData blob), no Vissim COM involved. Reason codes and "
            "data-quality flags above are that parser's own, quoted."
        ),
    }


# ---------------------------------------------------------------------------
# The measurement record
# ---------------------------------------------------------------------------


def observed_measurement(
    *,
    saturation_flow_pcu: Any,
    method: Any = None,
    measured_at: Any = None,
    attribution: Any = None,
    samples: Any = None,
    vehicles_per_sample: Any = None,
    intersection: Any = None,
    approach_link: Any = None,
    note: Any = None,
) -> dict[str, Any]:
    """Build the record for a saturation flow somebody MEASURED on site.

    Pure bookkeeping plus MRWA's own data-quality tests — no judgement about
    whether the number is right, only about whether it is attributable and
    whether it was taken from enough samples to be one. ``ok`` is ``False``
    with a ``reason_code`` when it is not; the caller does not have to
    re-implement the checks, and :func:`propose_car_following_parameters`
    refuses on exactly that field.

    The sample minimums are Appendix B §B.4 Table B-3's, read out of
    :data:`mrwa_reference.SATURATION_FLOW_SAMPLE_MINIMUMS` rather than spelled
    here. Both NUMBERS are the guideline's (min 5 vehicles/sample, min 6
    samples/lane). The TIER is only partly the pack's: the Appendix B extract's
    candidate list puts "sample <5 vehicles" under Refuse but does not list
    samples-per-lane at any tier, so refusing on that second minimum is THIS
    unit's own policy, not a quotation. It is still the right call — a
    saturation flow from three samples is not a weak measurement to be flagged,
    it is not a measurement, and the whole car-following change would rest on
    it — but it is ours to defend, not Main Roads'.
    """
    value = _number(saturation_flow_pcu)
    minimums = _sample_minimums()
    sample_count = _number(samples)
    per_sample = _number(vehicles_per_sample)

    record: dict[str, Any] = {
        "provenance": OBSERVED,
        "saturation_flow_pcu": value,
        "unit": "pcu/h of green",
        "method": _text(method) or None,
        "measured_at": _text(measured_at) or None,
        "attribution": _text(attribution) or None,
        "samples": sample_count,
        "vehicles_per_sample": per_sample,
        "intersection": _text(intersection) or None,
        "approach_link": _text(approach_link) or None,
        "note": _text(note) or None,
        "mrwa_sample_minimums": minimums,
        "ok": True,
        "reason_code": None,
        "why": None,
    }

    if value is None:
        return {**record, "ok": False, "reason_code": NO_SATURATION_FLOW_MEASUREMENT,
                "why": "no saturation-flow value supplied"}

    # Attribution is what separates a measurement from a number. MRWA grades
    # saturation flow BY method (Appendix B §B.3's hierarchy runs on-site ->
    # similar-site -> RR67 -> SCATS), so a value with no method cannot be
    # placed in that hierarchy and its standing is unknowable.
    if not record["method"] or not record["attribution"]:
        return {
            **record,
            "ok": False,
            "reason_code": MEASUREMENT_PROVENANCE_MISSING,
            "why": "method and attribution are both required for a measurement to be citable",
        }

    shortfalls: list[str] = []
    if sample_count is not None and minimums.get("samples_per_lane") is not None:
        if sample_count < minimums["samples_per_lane"]:
            shortfalls.append(
                f"samples {sample_count:g} < {minimums['samples_per_lane']:g} per lane"
            )
    if per_sample is not None and minimums.get("vehicles_per_sample") is not None:
        if per_sample < minimums["vehicles_per_sample"]:
            shortfalls.append(
                f"vehicles/sample {per_sample:g} < {minimums['vehicles_per_sample']:g}"
            )
    if shortfalls:
        return {
            **record,
            "ok": False,
            "reason_code": SAMPLE_SIZE_BELOW_MRWA_MINIMUM,
            "why": "; ".join(shortfalls),
        }

    return record


def derive_measurement(
    *,
    shadow_model: Mapping[str, Any] | None = None,
    run_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attempt a DERIVED saturation flow. Always refuses today, with reasons.

    Kept as a real function rather than an absent capability for the reason the
    rest of this repository keeps its gaps registered: "we did not build it" and
    "we measured what it would take and here is what is missing" are different
    claims, and only the second one closes. The blockers it names are
    re-measured from the payload on every call, so the day an artifact arrives
    that carries signal-group programs, this function's answer changes by
    itself and the remaining blocker
    (:data:`PCU_DECOMPOSITION_UNAVAILABLE`) is what is left standing.

    Never returns ``ok=True``. When that becomes possible it will be because a
    unit closed both blockers, and this docstring will be wrong in a way a test
    catches (:func:`derivation_is_still_blocked`).
    """
    availability = green_time_availability(shadow_model)
    blockers: list[str] = []
    if availability["state"] != GREEN_TIME_FULL:
        blockers.append(GREEN_TIME_UNAVAILABLE)
    # Independent of green time and always present: nothing in the run payload
    # decomposes VEHS into vehicle classes, and Table 5-6 is indexed in pcu.
    blockers.append(PCU_DECOMPOSITION_UNAVAILABLE)

    return {
        "provenance": DERIVED,
        "saturation_flow_pcu": None,
        "ok": False,
        "reason_code": SATURATION_FLOW_NOT_DERIVABLE,
        "blockers": blockers,
        "green_time": availability,
        "pcu": {
            "why": (
                "MRWA Table 5-6 is indexed in pcu. services.results_evidence.read_movement_class_"
                "rows restricts to ARG_VEHICLECLASS = 'ALL' and returns one summed vehicle count "
                "per movement, so no per-class counts reach this module; and the class codes real "
                "customer .dbs carry (1001/1002/1003/2002/2003) are Vissim class numbers with no "
                "written mapping to the Austroads classes mrwa_reference.SATURATION_FLOW_PCU_"
                "TABLE is keyed by. A vehicle count is therefore not convertible to pcu here."
            ),
            "pcu_factor_table": mrwa_reference.SATURATION_FLOW_PCU_TABLE.name,
        },
        "run_result_keys": sorted(str(key) for key in (run_result or {})),
        "why": (
            "A saturation flow was not derived from the run. Blockers: "
            + ", ".join(blockers)
        ),
    }


def derivation_is_still_blocked(result: Mapping[str, Any]) -> bool:
    """``True`` while :func:`derive_measurement` cannot produce a number.

    Exists so the claim in this module's docstring is TESTED rather than
    asserted in prose that rots.
    """
    return not result.get("ok") and bool(result.get("blockers"))


# ---------------------------------------------------------------------------
# Proposal / refusal construction — genesis_fixes' two-step shape
# ---------------------------------------------------------------------------


def _refuse(
    *,
    reason_code: str,
    evidence: dict[str, Any],
    ko: str,
    en: str,
    ja: str,
) -> fixes.FixProposal:
    return fixes.FixProposal(
        key=CAR_FOLLOWING_FROM_SATURATION_FLOW,
        key_kind=fixes.KEY_VALIDATION,
        outcome=fixes.REFUSAL,
        scope=fixes.NOT_APPLICABLE,
        verb=None,
        params=None,
        evidence=evidence,
        rationale_ko=ko,
        rationale_en=en,
        rationale_ja=ja,
        confidence=fixes.CONFIDENCE_NONE,
        reason_code=reason_code,
    )


def _emit(
    *,
    params: dict[str, Any],
    evidence: dict[str, Any],
    ko: str,
    en: str,
    ja: str,
    assumptions: tuple[str, ...],
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """Build one parameter-write PROPOSAL, but only if the LIVE registry takes it.

    Same two-step as ``genesis_fixes._emit``: resolve the verb against the real
    tool registry, then validate params against the verb's own pydantic model,
    so a payload this module thinks is fine but the live schema rejects is
    downgraded here rather than shipped to an approval card that would fail at
    dispatch. This matters more than usual for the W99 ban: if ``parameter``
    ever carried a ``cc*`` name, the constraint engine would refuse it at
    preflight — but this gate means it never even gets that far.
    """
    verb = "set_driving_behavior_parameter"
    resolution = fixes.resolve_verb(verb, registry=registry)
    if not resolution["resolved"]:
        return _refuse(
            reason_code=fixes.VERB_NOT_IN_REGISTRY,
            evidence={**evidence, "verb": verb, "verb_resolution": resolution},
            ko=f"차량추종 파라미터를 쓰는 편집 동사 '{verb}'이(가) 현재 도구 레지스트리에 없습니다"
            f"({resolution['why']}). 존재하지 않는 동사를 제안하지 않고 거부합니다.",
            en=f"The edit verb '{verb}' this change needs is not in the live tool registry "
            f"({resolution['why']}). Refused rather than proposing a verb that does not exist.",
            ja=f"追従パラメータを書く編集動詞 '{verb}' が現在のツールレジストリにありません"
            f"({resolution['why']})。存在しない動詞を提案せず拒否します。",
        )
    ok, error = fixes.validate_params(verb, params, registry=registry)
    if not ok:
        return _refuse(
            reason_code=fixes.PARAMS_REJECTED_BY_SCHEMA,
            evidence={**evidence, "verb": verb, "rejected_params": dict(params), "error": error},
            ko=f"'{verb}'의 실제 파라미터 스키마가 이 제안값을 거부했습니다: {error}. "
            "승인 카드로 넘기지 않고 거부합니다.",
            en=f"'{verb}''s own live params schema rejected this payload: {error}. Refused rather "
            "than sending it to an approval card.",
            ja=f"'{verb}' の実際のパラメータスキーマがこの提案値を拒否しました: {error}。"
            "承認カードに渡さず拒否します。",
        )
    return fixes.FixProposal(
        key=CAR_FOLLOWING_FROM_SATURATION_FLOW,
        key_kind=fixes.KEY_VALIDATION,
        outcome=fixes.PROPOSAL,
        scope=fixes.CAR_FOLLOWING_LEVEL,
        verb=verb,
        params=params,
        evidence=evidence,
        rationale_ko=ko,
        rationale_en=en,
        rationale_ja=ja,
        confidence=CONFIDENCE_TABLE_LOOKUP,
        reason_code=None,
        assumptions=assumptions,
    )


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------

#: The three §5.2.5.1 parameters, in the order the guideline lists them, with
#: where each value comes from. ``ax`` is FLAT (§5.2.5.1's body, 2.5 m); the
#: other two are the Table 5-6 row's two columns. Kept as data so the rationale
#: and the params can never name different sources for the same number.
_PARAMETER_SOURCES: tuple[tuple[str, str], ...] = (
    ("ax", "standstill"),
    ("bx_add", "additive"),
    ("bx_mult", "multiplicative"),
)

_PARAM_LABELS: dict[str, dict[str, str]] = {
    "ax": {"ko": "정지 간격", "en": "standstill distance", "ja": "停止時車間"},
    "bx_add": {"ko": "안전거리 가산부", "en": "additive part of safety distance",
               "ja": "安全距離の加算部"},
    "bx_mult": {"ko": "안전거리 승산부", "en": "multiplic. part of safety distance",
                "ja": "安全距離の乗算部"},
}


def propose_car_following_parameters(
    *,
    rung: Mapping[str, Any] | None,
    measurement: Mapping[str, Any] | None,
    behavior: Mapping[str, Any] | None = None,
    behavior_scope: Any = None,
    shadow_model: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> tuple[fixes.FixProposal, ...]:
    """CAPACITY_LIMIT + a measured saturation flow → car-following proposals.

    Returns EITHER exactly one ``REFUSAL`` or exactly three ``PROPOSAL``s —
    ``ax``, ``bx_add``, ``bx_mult``, in §5.2.5.1's own order. Three rather than
    one because ``set_driving_behavior_parameter`` writes a single field, so
    three is what the registry actually accepts and what an approval card can
    show line by line; they are nonetheless ONE change (one table row, one
    measurement), and every one of them carries the same shared evidence block
    saying so — a partially approved set leaves the behaviour off any published
    row, which the rationale states in all three languages.

    That statement used to be the ONLY thing holding the set together, and a
    warning is not a mechanism: the approval machinery authorises the three
    signatures independently by design, so a partial approval was both possible
    and, once applied, invisible. The three now also carry the set as DATA
    (``set_id``, ``set_members``, ``expected_after_apply``), and
    :func:`verify_applied_row` reads a behaviour back and names the mismatch if
    only part of the set landed. Detection, not prevention — preventing it means
    changing approval machinery shared with every other unit, which is a human
    decision, and this at least ends the silence.

    ``rung`` is :func:`geh_ladder.classify_node`'s output, read and never
    re-derived: this module must not be able to reach a different verdict than
    the diagnosis shipped in the same report. ``measurement`` is
    :func:`observed_measurement`'s record (or :func:`derive_measurement`'s
    refusal). ``behavior`` is the caller's named target —
    ``{"no": ..., "name": ..., "reference_count": ...}`` — read from the shadow
    model the same way ``targets`` is in U4; this module does no network I/O.

    Deliberately NOT done here: choosing localisation. When the target behaviour
    is shared (``reference_count > 0``), ``constraints.driving_behavior_shared_
    object_edit`` refuses an in-place edit and tells the caller to duplicate and
    retarget. That guardrail assumes a LOCAL intent, and §5.2.5.1's intent is
    the opposite — network-wide urban calibration — so this module neither
    silently duplicates (which would quietly narrow a network calibration to
    whatever links someone picked, changing what the numbers mean) nor hides the
    collision. It forecasts it: ``evidence["preflight_forecast"]`` says the
    refusal is expected and why, so the modeller meets it in the proposal
    instead of at dispatch. Closing that gap properly is a decision for a human,
    recorded for the next unit.
    """
    scope = normalize_behavior_scope(behavior_scope)
    rung_payload = rung if isinstance(rung, Mapping) else {}
    rung_code = str(rung_payload.get("rung") or "")
    measurement_payload = _authoritative_measurement(
        dict(measurement) if isinstance(measurement, Mapping) else {}
    )
    behavior_payload = dict(behavior) if isinstance(behavior, Mapping) else {}

    node_id = ""
    rung_evidence = rung_payload.get("evidence")
    if isinstance(rung_evidence, Mapping):
        node_id = str(rung_evidence.get("node_id") or "")

    shared: dict[str, Any] = {
        "node_id": node_id,
        "rung": rung_code or None,
        "measurement": measurement_payload,
        "behavior_scope": scope,
        "target_behavior": behavior_payload or None,
        # #44-b: the blast radius, NAMED, as data. The exception to
        # ``driving_behavior_shared_object_edit`` rests entirely on a human having
        # read what this edit touches, so the radius has to be on the proposal
        # itself — both to build that sentence and to be the left-hand side of
        # ``car_following_consent_radius_drifted``'s comparison at apply time. A
        # caller that hands in a ``behavior`` without ``referenced_by`` therefore
        # gets a radius of zero named consumers, which will NOT match a live shadow
        # model that has some: the grant then fails the drift check and the edit is
        # refused. That is the safe direction on purpose — an unnamed radius must
        # never be able to excuse a network-wide write. Drift alone did not achieve
        # that, though (refuter, #44-b): when the live model was ALSO reference-graph-
        # less the two empty radii compared equal and the edit was granted on a
        # "링크 0개" sentence, so ``consent_radius`` now also records whether the
        # picture is ``complete`` and ``evaluate`` refuses without it.
        "consent_radius": car_following_grant.consent_radius(behavior_payload),
        "citation": {
            "table": mrwa_reference.TABLE_5_6_CITATION,
            "clause": mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION,
            "step": geh_ladder._step_cite(7),
        },
    }

    # -- 1. the diagnosis must be the one §5.2.5.1 answers --------------------
    #
    # First, because it is the premise: without it the number is being applied
    # to a problem nobody established was a capacity problem, which is exactly
    # the "AI tunes by feel" failure this whole ladder exists to prevent.
    if rung_code != geh_ladder.CAPACITY_LIMIT:
        return (
            _refuse(
                reason_code=RUNG_IS_NOT_CAPACITY_LIMIT,
                evidence=shared,
                ko=f"이 노드의 진단은 '{rung_code or '없음'}'이며 용량 한계(CAPACITY_LIMIT)가 "
                "아닙니다. MRWA §5.2.5.1의 차량추종 보정은 '입력이 모두 실측인데 네트워크가 그 수요를 "
                "통과시키지 못한다'가 확인된 경우의 조치이므로, 그 진단 없이 포화교통류 기반 파라미터를 "
                "제안하지 않습니다.",
                en=f"This node's diagnosis is '{rung_code or 'none'}', not CAPACITY_LIMIT. "
                "MRWA §5.2.5.1's car-following calibration is the remedy for an established "
                "'inputs are all measured and the network still cannot pass the demand' finding, "
                "so no saturation-flow-based parameter change is proposed without it.",
                ja=f"このノードの診断は「{rung_code or 'なし'}」であり容量限界(CAPACITY_LIMIT)では"
                "ありません。MRWA §5.2.5.1の追従モデル補正は「インプットがすべて実測なのに需要を"
                "通せない」と確認された場合の対応であるため、その診断なしに飽和交通流に基づく"
                "パラメータ変更は提案しません。",
            ),
        )

    # -- 2. the measurement must exist and be citable -------------------------
    if not measurement_payload:
        return (
            _refuse(
                reason_code=NO_SATURATION_FLOW_MEASUREMENT,
                evidence={**shared, "green_time": green_time_availability(shadow_model)},
                ko=f"노드 {node_id}는 용량 한계로 진단되었으나, 정지선 포화교통류 측정값이 없어 "
                f"MRWA Table 5-6을 조회할 수 없습니다. 이 표는 '측정된 직진 포화교통류'를 입력으로 "
                "받으며, 측정 없이는 어떤 행마다 값도 근거가 없습니다 — 추정하지 않고 측정값을 "
                f"요청합니다 ({geh_ladder._step_cite_ko(7)}).",
                en=f"Node {node_id} is diagnosed as a capacity limit, but no stop-line saturation "
                "flow measurement was supplied, so MRWA Table 5-6 cannot be entered. The table "
                "takes a MEASURED straight-movement saturation flow as its input, and without one "
                "no row has any standing — the measurement is requested rather than estimated "
                f"({geh_ladder._step_cite(7)}).",
                ja=f"ノード {node_id} は容量限界と診断されましたが、停止線の飽和交通流の測定値が"
                "なく、MRWA Table 5-6を参照できません。この表は「測定された直進飽和交通流」を入力と"
                "するため、測定なしにはどの行にも根拠がありません — 推定せず測定値を要求します"
                f"({geh_ladder._step_cite_ja(7)})。",
            ),
        )

    if not measurement_payload.get("ok"):
        return (_refuse_measurement(shared, measurement_payload, shadow_model, node_id),)

    # -- 3. the table must actually cover the measurement ---------------------
    lookup = lookup_table_5_6(measurement_payload.get("saturation_flow_pcu"))
    shared["lookup"] = lookup
    if lookup["row"] is None:
        return (_refuse_lookup(shared, lookup, node_id),)

    # -- 4. a target object must be named -------------------------------------
    behavior_no = _text(behavior_payload.get("no") or behavior_payload.get("behavior_no"))
    if not behavior_no:
        return (
            _refuse(
                reason_code=NO_DRIVING_BEHAVIOR_TARGET,
                evidence=shared,
                ko=f"포화교통류 {_fmt(lookup['measured_pcu'])} pcu에 대한 Table 5-6 행은 확정되었으나"
                f"(가산 {_fmt(lookup['row']['additive'])} / 승산 "
                f"{_fmt(lookup['row']['multiplicative'])}), 수정할 주행 행태가 지정되지 않았습니다. "
                "get_driving_behaviors로 도심(Wiedemann 74) 행태의 번호를 지정해 다시 요청하십시오.",
                en="The Table 5-6 row for a measured "
                f"{_fmt(lookup['measured_pcu'])} pcu is settled (additive "
                f"{_fmt(lookup['row']['additive'])} / multiplicative "
                f"{_fmt(lookup['row']['multiplicative'])}), but no driving behaviour was named to "
                "change. Name the urban (Wiedemann 74) behaviour's number from "
                "get_driving_behaviors and re-request.",
                ja=f"飽和交通流 {_fmt(lookup['measured_pcu'])} pcu に対する Table 5-6 の行は確定"
                f"しました(加算 {_fmt(lookup['row']['additive'])} / 乗算 "
                f"{_fmt(lookup['row']['multiplicative'])})が、変更する走行挙動が指定されていません。"
                "get_driving_behaviors で都市(Wiedemann 74)挙動の番号を指定して再要求してください。",
            ),
        )

    # -- 5. build the three writes -------------------------------------------
    shared["preflight_forecast"] = _preflight_forecast(behavior_payload, scope)
    return _build_actions(
        shared=shared,
        lookup=lookup,
        measurement=measurement_payload,
        behavior_no=behavior_no,
        behavior=behavior_payload,
        scope=scope,
        node_id=node_id,
        registry=registry,
    )


def _authoritative_measurement(payload: dict[str, Any]) -> dict[str, Any]:
    """Re-derive a measurement's verdict from its FIELDS, never from its flag.

    ``propose_car_following_parameters`` takes a ``Mapping``, and until
    2026-08-18 it read ``payload["ok"]`` and believed it. That made every
    data-quality gate in :func:`observed_measurement` opt-in: a caller (a
    router, an LLM-assembled payload, a test fixture that drifted) that handed
    in ``{"saturation_flow_pcu": 1950, "ok": True}`` with no method, no
    attribution and no sample counts got three fully-cited PROPOSALs whose
    rationale read "Measurement source: on-site measurement (-) at -, -,
    submitted by -". A refusal you can turn off by asserting you passed it is
    not a refusal.

    This repository has already learned this exact lesson once, on the guardrail
    this module forecasts: ``constraints._check_driving_behavior_shared_object_
    edit``'s own comment records that its ORIGINAL version "trusted a
    caller-supplied ``duplicated_before_edit`` boolean — a self-reported flag
    any planner call could simply always set ``True``, giving zero real
    protection", and was rewritten to measure the real reference graph instead.
    Same fix, same reason: the checks are re-run here against the payload's own
    fields, so the only way to pass them is to actually satisfy them.

    Idempotent on a record :func:`observed_measurement` produced — the same
    fields re-checked give the same verdict — so a well-formed caller sees no
    change at all. A payload that is already ``ok=False`` keeps its own reason
    code (that includes :func:`derive_measurement`'s refusal, which must reach
    :func:`_refuse_measurement` with ``SATURATION_FLOW_NOT_DERIVABLE`` intact).
    """
    if not payload:
        return payload
    if not payload.get("ok"):
        return payload
    provenance = str(payload.get("provenance") or OBSERVED)
    if provenance != OBSERVED:
        # Only an OBSERVED measurement can reach a proposal today. A payload
        # claiming any other provenance AND ok=True is asserting a capability
        # that does not exist (see derive_measurement, which never returns ok).
        return {
            **payload,
            "ok": False,
            "reason_code": SATURATION_FLOW_NOT_DERIVABLE,
            "why": (
                f"provenance {provenance!r} claimed ok=True, but only an {OBSERVED} measurement "
                "can enter Table 5-6 today"
            ),
        }
    return observed_measurement(
        saturation_flow_pcu=payload.get("saturation_flow_pcu"),
        method=payload.get("method"),
        measured_at=payload.get("measured_at"),
        attribution=payload.get("attribution"),
        samples=payload.get("samples"),
        vehicles_per_sample=payload.get("vehicles_per_sample"),
        intersection=payload.get("intersection"),
        approach_link=payload.get("approach_link"),
        note=payload.get("note"),
    )


def _preflight_forecast(behavior: Mapping[str, Any], scope: str) -> dict[str, Any]:
    """Whether ``driving_behavior_shared_object_edit`` will refuse this, said up front.

    Not a substitute for the real check — ``fixes.preflight_findings`` runs the
    actual constraint engine and a caller should use it. This is the honest
    heads-up that a network-wide edit of a SHARED behaviour is exactly what
    that constraint refuses, together with the reason the collision exists at
    all: the guardrail assumes a local intent, §5.2.5.1 has a global one.

    TWO PATHS since unit #44-b (2026-08-19), because there are now two answers
    and one number cannot carry both:

    * :data:`PATH_DIRECT_EDIT` — the write issued on its own (a planner tool
      call, ``fixes.preflight_findings``, any caller with no approval context).
      Refused whenever the target is shared, exactly as before. This is what
      the top-level ``expected_to_refuse`` still reports, so every existing
      consumer of that key keeps its meaning.
    * :data:`PATH_APPROVED_SET` — the write dispatched through the approval
      machinery under the grant this proposal set mints
      (:func:`grant_from_proposals`). Clean via the named exception
      ``car_following_shared_edit_consented`` **only when the blast radius can
      be named in full** (``car_following_grant.consent_radius(...)["complete"]``
      — a truncated or absent consumer list means the sentence a human would
      read understates where the edit lands, and this path then forecasts the
      ordinary refusal instead). Even then it stays clean only while the radius
      is the one consent was given for: if the reference graph moves between
      approval and apply, ``car_following_consent_radius_drifted`` refuses by
      name.

    The refuter's invariant from #44 is unchanged and now has to hold on both
    axes: a forecast may be pessimistic, never optimistic. A path that says
    clean is a path where the engine, handed that path's own provenance,
    produces no refusal.
    """
    reference_count = _number(behavior.get("reference_count"))
    # The forecast is about the action this module ACTUALLY emits, which is an
    # in-place ``set_driving_behavior_parameter`` on ``behavior_no`` — the same
    # payload under either scope (see _build_actions: scope changes the
    # rationale, never the params). So the refusal depends ONLY on the target's
    # reference count.
    #
    # It used to depend on ``scope`` as well, and that was wrong in the
    # direction that matters: a LOCALIZED request against a shared behaviour
    # forecast "no collision expected — the duplicate-and-retarget path
    # applies", while the real engine refused, because nothing had duplicated
    # anything. Asking for localisation is not the same as having localised;
    # the duplicate + retarget is a separate step this module does not perform.
    # A forecast that is optimistic exactly where the guardrail bites is worse
    # than no forecast, so scope is now reported as CONTEXT and never lowers the
    # verdict. Pinned by test_the_forecast_never_says_clean_where_the_engine_
    # refuses.
    will_refuse = reference_count is not None and reference_count > 0
    if will_refuse:
        why = (
            "This behaviour is referenced by "
            f"{_fmt(reference_count)} link-behavior-type consumer(s), and "
            "constraints.driving_behavior_shared_object_edit refuses an in-place edit of any "
            "behaviour with a non-zero reference count. That guardrail's premise is a LOCAL "
            "intent ('editing one to fix a single ramp changes every link using it'); MRWA "
            "§5.2.5.1's intent here is the opposite — one measured saturation flow calibrating "
            "the urban behaviour across the network. Issued on its own, this write is still "
            "refused: the narrow exception decided on 2026-08-19 applies ONLY to this set "
            "dispatched through the approval machinery (see the approved_set path below)."
        )
        if scope == SCOPE_LOCALIZED:
            why += (
                " A LOCALIZED scope was requested, which does NOT avoid this refusal: the write "
                "proposed here still targets the shared behaviour in place. duplicate_driving_"
                "behavior must actually run first, and the copy (reference count 0) is what the "
                "parameter write then targets."
            )
    else:
        why = "No in-place-edit collision expected: " + (
            "the behaviour has no referencing consumers, so it is safe to edit in place."
            if reference_count == 0
            else "the target's reference count is unknown, so nothing is forecast — run "
            "fixes.preflight_findings against the real shadow model."
        )

    # Can the radius even be stated in full? The exception is only available where
    # every consumer is named (see ``car_following_grant.consent_radius``'s
    # ``complete``); where it is not, ``evaluate`` returns RADIUS_NOT_FULLY_NAMED and
    # the ordinary shared-object refusal never stands down. Forecasting that path as
    # "clean" would be optimistic exactly where the guardrail bites — the one
    # direction this forecast is not allowed to be wrong in.
    radius_complete = bool(car_following_grant.consent_radius(behavior).get("complete"))

    if will_refuse and not radius_complete:
        approved_path = {
            "expected_to_refuse": True,
            "constraint_id": "driving_behavior_shared_object_edit",
            "why": (
                "STILL REFUSED, even through the approval machinery: this behaviour's blast "
                "radius cannot be named in full. Its consumer list is truncated or absent, so "
                f"the link total that would be shown ({_fmt(reference_count)} consumer(s)) "
                "covers only the consumers that were named and an un-named one can change by "
                "any amount without the drift check seeing it. The exception decided on "
                "2026-08-19 rests on a human having READ where the edit lands, so it does not "
                "apply where that sentence cannot be written truthfully. duplicate_driving_"
                "behavior and retarget a named link set, or re-read the behaviour from a shadow "
                "model that names every consumer."
            ),
        }
    elif will_refuse:
        approved_path = {
            "expected_to_refuse": False,
            "constraint_id": "car_following_shared_edit_consented",
            "refuses_if_radius_drifts": "car_following_consent_radius_drifted",
            "why": (
                "Clean via the named exception: dispatched through the approval machinery under "
                "this set's own grant (saturation_flow.grant_from_proposals), "
                "constraints.driving_behavior_shared_object_edit stands down and "
                "car_following_shared_edit_consented names the radius instead — "
                f"{_fmt(reference_count)} link-behavior-type consumer(s) changed together, "
                "knowingly (DECISIONS.md 2026-08-19). Three things keep it honest and all three "
                "refuse rather than warn: the grant names these three writes by canonical "
                "signature, so nothing else rides the exception; if the reference graph moves "
                "between approval and apply, car_following_consent_radius_drifted refuses by "
                "name, because consent was to a named radius; and the exception is unavailable "
                "at all unless every consumer is named, because a partly-named radius is not a "
                "sentence a human can have read."
            ),
        }
    else:
        # Nothing was being refused, so the exception is not what makes this
        # clean — saying otherwise would credit a guardrail bypass for an edit
        # that never needed one.
        approved_path = {
            "expected_to_refuse": False,
            "constraint_id": "car_following_shared_edit_consented",
            "refuses_if_radius_drifts": "car_following_consent_radius_drifted",
            "why": (
                "Clean, and NOT via the exception — there is nothing here for it to excuse. "
                + why
            ),
        }

    return {
        "constraint_id": "driving_behavior_shared_object_edit",
        # The DIRECT path's verdict, unchanged in meaning since #44 — every
        # existing consumer of this key reads "what happens if this write is
        # issued on its own", and that answer did not change.
        "expected_to_refuse": will_refuse,
        "reference_count": reference_count,
        "behavior_scope": scope,
        "why": why,
        "paths": {
            PATH_DIRECT_EDIT: {
                "expected_to_refuse": will_refuse,
                "constraint_id": "driving_behavior_shared_object_edit",
                "why": why,
            },
            PATH_APPROVED_SET: approved_path,
        },
    }


def _build_actions(
    *,
    shared: dict[str, Any],
    lookup: Mapping[str, Any],
    measurement: Mapping[str, Any],
    behavior_no: str,
    behavior: Mapping[str, Any],
    scope: str,
    node_id: str,
    registry: ToolRegistry | None,
) -> tuple[fixes.FixProposal, ...]:
    row = lookup["row"]
    standstill = float(mrwa_reference.STANDSTILL_DISTANCE_M.value)
    values = {
        "standstill": standstill,
        "additive": float(row["additive"]),
        "multiplicative": float(row["multiplicative"]),
    }

    measured = _fmt(lookup["measured_pcu"])
    source_ko = _measurement_phrase(measurement, "ko")
    source_en = _measurement_phrase(measurement, "en")
    source_ja = _measurement_phrase(measurement, "ja")
    policy_ko, policy_en, policy_ja = _policy_clause(lookup)
    scope_ko, scope_en, scope_ja = _scope_clause(scope, behavior, behavior_no)

    assumptions = (
        ASSUME_STRAIGHT_MOVEMENT,
        ASSUME_BEHAVIOR_IS_URBAN,
        ASSUME_KEY_INTERSECTION_REPRESENTS_NETWORK,
    ) + ((ASSUME_CHANGE_IS_NETWORK_WIDE,) if scope == SCOPE_NETWORK_WIDE else ())

    actions: list[fixes.FixProposal] = []
    for parameter, source_key in _PARAMETER_SOURCES:
        value = values[source_key]
        label = _PARAM_LABELS[parameter]
        is_table_value = source_key != "standstill"
        basis_ko = (
            f"MRWA Table 5-6의 포화교통류 {_fmt(row['saturation_flow_pcu'])} pcu 행"
            if is_table_value
            else "MRWA §5.2.5.1 본문(포화교통류와 무관한 고정값)"
        )
        basis_en = (
            f"MRWA Table 5-6's {_fmt(row['saturation_flow_pcu'])} pcu row"
            if is_table_value
            else "MRWA §5.2.5.1's body (a flat value, independent of saturation flow)"
        )
        basis_ja = (
            f"MRWA Table 5-6 の飽和交通流 {_fmt(row['saturation_flow_pcu'])} pcu の行"
            if is_table_value
            else "MRWA §5.2.5.1 本文(飽和交通流に依存しない固定値)"
        )

        evidence = {
            **shared,
            "parameter": parameter,
            "proposed_value": value,
            "value_source": basis_en,
            "applied_row": dict(row),
            "coordinated_with": [name for name, _ in _PARAMETER_SOURCES if name != parameter],
            # The set, stated as data rather than only as a warning sentence.
            # ``set_id`` is identical across the three (one node, one behaviour,
            # one row), so a consumer can group them without parsing prose; and
            # ``expected_after_apply`` is the complete state the behaviour must
            # end in, which is exactly what verify_applied_row needs to detect a
            # partial application afterwards. Neither field enforces anything —
            # enforcement would mean changing the shared approval machinery —
            # but together they make the partial state DETECTABLE, which is what
            # the warning alone could not do.
            "set_id": (
                f"{CAR_FOLLOWING_FROM_SATURATION_FLOW}:{node_id}:{behavior_no}:"
                f"{row['saturation_flow_pcu']:g}"
            ),
            "set_members": [name for name, _ in _PARAMETER_SOURCES],
            "expected_after_apply": {
                "ax": values["standstill"],
                "bx_add": values["additive"],
                "bx_mult": values["multiplicative"],
            },
        }

        actions.append(
            _emit(
                params={
                    "behavior_no": behavior_no,
                    "parameter": parameter,
                    "value": value,
                },
                evidence=evidence,
                ko=f"노드 {node_id}는 입력이 모두 실측인데도 수요를 통과시키지 못하는 용량 한계로 "
                f"진단되었습니다. 측정된 정지선 직진 포화교통류 {measured} pcu에 대해 "
                f"{basis_ko}에 따라 주행 행태 {behavior_no}의 {label['ko']}({parameter})를 "
                f"{_fmt(value)}로 설정할 것을 제안합니다. 근거: {mrwa_reference.TABLE_5_6_CITATION} / "
                f"{mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION}. "
                f"측정 출처: {source_ko}.{policy_ko} {scope_ko} "
                "이 값은 ax·bx_add·bx_mult 세 개가 한 세트(표의 한 행)이므로, 일부만 승인하면 "
                "행태가 표의 어느 행과도 일치하지 않게 됩니다.",
                en=f"Node {node_id} is diagnosed as a capacity limit — every input is measured and "
                f"the network still cannot pass the demand. For a measured stop-line straight-"
                f"movement saturation flow of {measured} pcu, {basis_en} sets the "
                f"{label['en']} ({parameter}) of driving behaviour {behavior_no} to "
                f"{_fmt(value)}. Basis: {mrwa_reference.TABLE_5_6_CITATION} / "
                f"{mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION}. "
                f"Measurement source: {source_en}.{policy_en} {scope_en} "
                "ax, bx_add and bx_mult are one set (one row of the table): approving only some "
                "of them leaves the behaviour matching no published row.",
                ja=f"ノード {node_id} は、インプットがすべて実測でありながら需要を通せない容量限界と"
                f"診断されました。測定された停止線の直進飽和交通流 {measured} pcu に対し、"
                f"{basis_ja}に従って走行挙動 {behavior_no} の{label['ja']}({parameter})を "
                f"{_fmt(value)} に設定することを提案します。根拠: "
                f"{mrwa_reference.TABLE_5_6_CITATION} / "
                f"{mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION}。"
                f"測定の出所: {source_ja}。{policy_ja} {scope_ja} "
                "ax・bx_add・bx_mult は表の一行として一組であり、一部のみ承認すると挙動が"
                "どの公表行とも一致しなくなります。",
                assumptions=assumptions,
                registry=registry,
            )
        )
    return tuple(actions)


# ---------------------------------------------------------------------------
# The grant — unit #44-b's structural key
# ---------------------------------------------------------------------------


def grant_from_proposals(
    proposals: Sequence[fixes.FixProposal],
) -> car_following_grant.CarFollowingGrant:
    """Mint the one thing that can stand ``driving_behavior_shared_object_edit`` down.

    THE ONLY MINTING PATH. Everything the exception is worth rests on this function
    refusing to build a grant out of anything less than a complete, self-consistent set
    that :func:`propose_car_following_parameters` actually produced — which in turn means
    the CAPACITY_LIMIT rung, a provenance-checked OBSERVED measurement that survived
    :func:`observed_measurement`'s data-quality gates, a real Table 5-6 row, and a named
    target behaviour were all established first. There is deliberately no "build me a
    grant for this behaviour" entry point, because that is the shape of every guard this
    repo has had to un-ship (see ``car_following_grant``'s docstring for both).

    Raises ``ValueError`` — never returns a partial or empty grant — because a
    mis-assembled set is a programming error at the call site, not a modelling refusal
    the user should read. The refusals users see are ``FixProposal``s.

    What the returned grant then authorizes is EXACTLY these three writes: its
    ``signatures`` are their canonical ``gates.signature`` forms, so the behaviour number,
    the parameter name and the value are all pinned at once. Nothing else — not the other
    five whitelisted driving-behaviour parameters, not W99's CC0–CC9, not the same
    parameter at a different value — can ride it.
    """
    actions = list(proposals or ())
    if len(actions) != len(_PARAMETER_SOURCES):
        raise ValueError(
            f"grant_from_proposals: expected the complete set of {len(_PARAMETER_SOURCES)} "
            f"proposals ({', '.join(name for name, _ in _PARAMETER_SOURCES)}); got "
            f"{len(actions)}. A grant over a partial set would authorize a behaviour that "
            "matches no published Table 5-6 row."
        )

    for action in actions:
        if not action.is_proposal or action.verb != car_following_grant.GRANT_TOOL:
            raise ValueError(
                "grant_from_proposals: every member must be a PROPOSAL emitting "
                f"{car_following_grant.GRANT_TOOL!r}; got outcome={action.outcome!r} "
                f"verb={action.verb!r} (reason_code={action.reason_code!r}). A refusal is not a "
                "proposal with the safety removed."
            )
        if action.key != CAR_FOLLOWING_FROM_SATURATION_FLOW:
            raise ValueError(
                f"grant_from_proposals: member key {action.key!r} is not "
                f"{CAR_FOLLOWING_FROM_SATURATION_FLOW!r} — this grant covers the Table 5-6 "
                "car-following set and nothing else."
            )

    set_ids = {str(a.evidence.get("set_id") or "") for a in actions}
    if len(set_ids) != 1 or not set_ids.pop():
        raise ValueError(
            "grant_from_proposals: the three members must share exactly one set_id (one node, "
            "one behaviour, one table row); got "
            f"{sorted({str(a.evidence.get('set_id')) for a in actions})!r}."
        )
    set_id = str(actions[0].evidence["set_id"])

    behavior_nos = {str((a.params or {}).get("behavior_no", "")) for a in actions}
    if len(behavior_nos) != 1:
        raise ValueError(
            f"grant_from_proposals: members target different behaviours {sorted(behavior_nos)!r}."
        )
    behavior_no = behavior_nos.pop()

    values = {str((a.params or {}).get("parameter", "")): (a.params or {}).get("value") for a in actions}
    if set(values) != set(car_following_grant.GRANTED_PARAMETERS):
        raise ValueError(
            "grant_from_proposals: the set must be exactly "
            f"{sorted(car_following_grant.GRANTED_PARAMETERS)!r}; got {sorted(values)!r}. The "
            "exception reaches these three parameters and no others."
        )

    # The set's own declared end state, re-checked against the writes that claim to reach
    # it. ``expected_after_apply`` is what verify_applied_row reads back afterwards, so a
    # grant whose writes disagree with it would authorize a set that its own verifier
    # would then call a partial application.
    expected = actions[0].evidence.get("expected_after_apply")
    expected = dict(expected) if isinstance(expected, Mapping) else {}
    for parameter, value in values.items():
        if not _same(expected.get(parameter), value):
            raise ValueError(
                f"grant_from_proposals: member {parameter!r} writes {value!r} but the set's "
                f"expected_after_apply says {expected.get(parameter)!r}."
            )

    measurement = actions[0].evidence.get("measurement")
    measurement = dict(measurement) if isinstance(measurement, Mapping) else {}
    if not measurement.get("ok"):
        raise ValueError(
            "grant_from_proposals: the set carries no accepted measurement "
            f"(reason_code={measurement.get('reason_code')!r}). Table 5-6 is never entered on a "
            "guess, and a grant is never issued without the number that justified it."
        )

    row = actions[0].evidence.get("applied_row")
    row = dict(row) if isinstance(row, Mapping) else {}

    return car_following_grant.build_grant(
        set_id=set_id,
        behavior_no=behavior_no,
        values={k: float(v) for k, v in values.items()},
        saturation_flow_pcu=float(row.get("saturation_flow_pcu") or 0.0),
        measurement={
            "saturation_flow_pcu": measurement.get("saturation_flow_pcu"),
            "provenance": measurement.get("provenance"),
            "method": measurement.get("method"),
            "attribution": measurement.get("attribution"),
            "measured_at": measurement.get("measured_at"),
        },
        consent_radius_payload=dict(actions[0].evidence.get("consent_radius") or {}),
        signatures=[signature(car_following_grant.GRANT_TOOL, dict(a.params or {})) for a in actions],
    )


# ---------------------------------------------------------------------------
# Refusal bodies for the two data-shaped rejections
# ---------------------------------------------------------------------------


def _refuse_measurement(
    shared: dict[str, Any],
    measurement: Mapping[str, Any],
    shadow_model: Mapping[str, Any] | None,
    node_id: str,
) -> fixes.FixProposal:
    reason = str(measurement.get("reason_code") or NO_SATURATION_FLOW_MEASUREMENT)
    why = str(measurement.get("why") or "")
    evidence = {**shared, "green_time": green_time_availability(shadow_model)}

    if reason == SATURATION_FLOW_NOT_DERIVABLE:
        blockers = ", ".join(str(code) for code in (measurement.get("blockers") or []))
        return _refuse(
            reason_code=SATURATION_FLOW_NOT_DERIVABLE,
            evidence={**evidence, "derivation": dict(measurement)},
            ko=f"노드 {node_id}의 포화교통류를 실행 결과에서 유도하려 했으나 불가능합니다"
            f"({blockers}). 결과 DB의 VEHS는 차량 대수이고 Table 5-6은 pcu 기준이며, 차종 분해가 "
            "결과 payload에 도달하지 않습니다. 또한 이 체크아웃의 모든 모델에서 신호 프로그램이 "
            "단계(stage) 기반이라 신호그룹별 녹색 시간이 읽히지 않습니다. 유도값을 지어내지 않고, "
            "현장에서 측정한 포화교통류를 요청합니다.",
            en=f"Deriving node {node_id}'s saturation flow from the run is not possible "
            f"({blockers}). The results DB's VEHS is a VEHICLE count while Table 5-6 is indexed in "
            "pcu, and no per-class decomposition reaches the run payload; separately, every "
            "signal program in this checkout is stage-based, so no per-signal-group green time "
            "can be read. Rather than manufacture a derived figure, an on-site measurement is "
            "requested.",
            ja=f"ノード {node_id} の飽和交通流を実行結果から導出することはできません({blockers})。"
            "結果DBのVEHSは車両台数で、Table 5-6はpcu基準ですが、車種分解が結果ペイロードに"
            "届きません。さらに、このチェックアウトの全モデルで信号プログラムがステージ方式のため、"
            "信号グループ別の青時間が読めません。導出値を作らず、現地測定値を要求します。",
        )

    if reason == SAMPLE_SIZE_BELOW_MRWA_MINIMUM:
        return _refuse(
            reason_code=SAMPLE_SIZE_BELOW_MRWA_MINIMUM,
            evidence=evidence,
            ko=f"제출된 포화교통류는 MRWA Appendix B §B.4 Table B-3의 최소 표본 요건에 미달합니다"
            f"({why}). 이 기준은 권고가 아니라 거부 기준이며, 이 측정값 하나에 네트워크 전체의 "
            "차량추종 파라미터가 걸려 있으므로 미달 상태로는 진행하지 않습니다.",
            en="The submitted saturation flow does not meet MRWA Appendix B §B.4 Table B-3's "
            f"minimum sampling requirements ({why}). Those minimums are a refuse-tier gate, not "
            "advice, and the whole network's car-following parameters would rest on this one "
            "figure — so it is not accepted short.",
            ja="提出された飽和交通流は MRWA Appendix B §B.4 Table B-3 の最小標本要件を満たしません"
            f"({why})。この基準は助言ではなく拒否基準であり、ネットワーク全体の追従パラメータが"
            "この一つの値に依存するため、不足のまま進めません。",
        )

    if reason == MEASUREMENT_PROVENANCE_MISSING:
        return _refuse(
            reason_code=MEASUREMENT_PROVENANCE_MISSING,
            evidence=evidence,
            ko="포화교통류 값은 있으나 측정 방법과 귀속(누가·언제·어떻게 측정했는지)이 없습니다"
            f"({why}). MRWA Appendix B §B.3는 포화교통류를 '방법'으로 등급화하므로, 방법 없는 "
            "숫자는 그 위계 어디에도 놓을 수 없고 보고서에 인용할 수도 없습니다.",
            en="A saturation-flow value was supplied but its method and attribution (who measured "
            f"it, when, how) were not ({why}). MRWA Appendix B §B.3 grades saturation flow BY "
            "method, so an unattributed number cannot be placed anywhere in that hierarchy, nor "
            "cited in a report.",
            ja="飽和交通流の値はありますが、測定方法と帰属(誰が・いつ・どのように測定したか)が"
            f"ありません({why})。MRWA Appendix B §B.3 は飽和交通流を「方法」で等級化するため、"
            "方法のない数値はその階層のどこにも置けず、報告書にも引用できません。",
        )

    return _refuse(
        reason_code=NO_SATURATION_FLOW_MEASUREMENT,
        evidence=evidence,
        ko=f"포화교통류 측정값을 사용할 수 없습니다({why or reason}). 측정 없이 Table 5-6을 "
        "조회하지 않습니다.",
        en=f"The saturation-flow measurement is unusable ({why or reason}). Table 5-6 is not "
        "entered without a measurement.",
        ja=f"飽和交通流の測定値を使用できません({why or reason})。測定なしに Table 5-6 は"
        "参照しません。",
    )


def _refuse_lookup(
    shared: dict[str, Any], lookup: Mapping[str, Any], node_id: str
) -> fixes.FixProposal:
    status = str(lookup.get("status"))
    measured = _fmt(lookup.get("measured_pcu"))
    low, high = lookup["table_range_pcu"]
    band_low, band_high = lookup["plausibility_band_pcu"]

    if status == IMPLAUSIBLE:
        return _refuse(
            reason_code=SATURATION_FLOW_IMPLAUSIBLE,
            evidence=shared,
            ko=f"제출된 포화교통류 {measured} pcu는 이 시스템의 타당성 점검 범위 "
            f"{_fmt(band_low)}–{_fmt(band_high)} PCU/h를 벗어납니다. 표 범위 문제가 아니라 자료 "
            "입력 문제로 보이므로, 값을 확인한 뒤 다시 제출하십시오. (이 범위는 Main Roads가 "
            "공표한 수치가 아니라 Appendix B 정리본의 제약 후보 목록에서 이 시스템이 채택한 "
            "자체 기준입니다 — 근거 문서의 쪽 인용은 아직 확인되지 않았습니다.)",
            en=f"The submitted saturation flow of {measured} pcu falls outside this system's "
            f"plausibility screen of {_fmt(band_low)}-{_fmt(band_high)} PCU/h. That reads as a "
            "data-entry problem rather than a table-coverage one — check the figure and resubmit. "
            "(This band is NOT a published Main Roads figure: it is Prism's own threshold, taken "
            "from the Appendix B extract's drafted constraint-engine candidate list, where it is "
            "written '~800-2200' at warn tier and carries no page cite.)",
            ja=f"提出された飽和交通流 {measured} pcu は本システムの妥当性チェック範囲 "
            f"{_fmt(band_low)}–{_fmt(band_high)} PCU/h を外れています。表の範囲の問題ではなく"
            "データ入力の問題と考えられるため、値を確認して再提出してください。(この範囲は "
            "Main Roads の公表値ではなく、Appendix B 抜粋の制約候補リストから本システムが採用した"
            "独自基準です — 出典ページは未確認です。)",
        )

    if status == OUT_OF_RANGE_LOW:
        return _refuse(
            reason_code=SATURATION_FLOW_BELOW_TABLE,
            evidence=shared,
            ko=f"측정된 포화교통류 {measured} pcu는 타당 범위 안이지만 MRWA Table 5-6의 최저 행"
            f"({_fmt(low)} pcu)보다 낮습니다. 표를 아래로 외삽하면 공표되지 않은 안전거리 값을 "
            "지어내게 되므로 하지 않습니다. 이 정도로 낮은 포화교통류는 차량추종 파라미터보다 "
            f"먼저 기하구조·차로 폭·회전반경(RR67 입력)을 확인할 사안입니다 "
            f"({mrwa_reference.RR67_FORMULA['citation']}).",
            en=f"The measured {measured} pcu is plausible but below MRWA Table 5-6's lowest "
            f"published row ({_fmt(low)} pcu). Extrapolating the table downward would invent an "
            "unpublished safety-distance pair, which is not done. A saturation flow this low is a "
            "reason to check geometry, lane width and turn radius (the RR67 inputs) before "
            f"car-following parameters ({mrwa_reference.RR67_FORMULA['citation']}).",
            ja=f"測定された {measured} pcu は妥当範囲内ですが、MRWA Table 5-6 の最低行"
            f"({_fmt(low)} pcu)を下回ります。表を下方に外挿すると未公表の安全距離値を作ることに"
            "なるため行いません。これほど低い飽和交通流は、追従パラメータより先に幾何構造・車線幅・"
            f"回転半径(RR67の入力)を確認すべき事案です({mrwa_reference.RR67_FORMULA['citation']})。",
        )

    return _refuse(
        reason_code=SATURATION_FLOW_ABOVE_TABLE,
        evidence=shared,
        ko=f"측정된 포화교통류 {measured} pcu는 MRWA Table 5-6의 최고 행({_fmt(high)} pcu)을 "
        "넘습니다. 표를 위로 외삽하지 않습니다.",
        en=f"The measured {measured} pcu is above MRWA Table 5-6's highest published row "
        f"({_fmt(high)} pcu). The table is not extrapolated upward.",
        ja=f"測定された {measured} pcu は MRWA Table 5-6 の最高行({_fmt(high)} pcu)を超えます。"
        "表を上方に外挿することはしません。",
    )


# ---------------------------------------------------------------------------
# Rationale fragments
# ---------------------------------------------------------------------------


def _measurement_phrase(measurement: Mapping[str, Any], language: str) -> str:
    """How the number was obtained, in one clause — never omitted.

    §5.2.5.1's whole claim is that these parameters come FROM a measurement, so
    a rationale that cited the table without saying where the number came from
    would be quoting the authority for something the authority did not say.
    """
    method = _text(measurement.get("method")) or "-"
    attribution = _text(measurement.get("attribution")) or "-"
    where = _text(measurement.get("intersection")) or _text(measurement.get("approach_link")) or "-"
    when = _text(measurement.get("measured_at")) or "-"
    samples = measurement.get("samples")
    sample_clause = "" if samples is None else f", n={_fmt(samples)}"
    if language == "ko":
        return f"현장 측정({method}), 장소 {where}, 일시 {when}, 제출 {attribution}{sample_clause}"
    if language == "ja":
        return f"現地測定({method})、場所 {where}、日時 {when}、提出 {attribution}{sample_clause}"
    return f"on-site measurement ({method}) at {where}, {when}, submitted by {attribution}{sample_clause}"


def _policy_clause(lookup: Mapping[str, Any]) -> tuple[str, str, str]:
    """The between-rows note — present only when a choice was actually made."""
    if lookup.get("status") != BRACKETED:
        return ("", "", "")
    lower = lookup["bracket_lower"]
    upper = lookup["bracket_upper"]
    return (
        f" 측정값은 표의 두 행 사이({_fmt(lower['saturation_flow_pcu'])} / "
        f"{_fmt(upper['saturation_flow_pcu'])} pcu)에 있습니다. 지침은 행 사이 보간 규칙을 두지 "
        f"않으므로, 이 시스템의 명시적 정책에 따라 낮은 쪽 행({_fmt(lower['saturation_flow_pcu'])} "
        "pcu)을 택했습니다 — 낮은 행일수록 안전거리가 커서 측정이 뒷받침하지 않는 용량을 "
        "과대평가하지 않기 때문입니다. 두 행을 모두 근거에 실었으니 위쪽 행으로 바꿀 수 있습니다.",
        f" The measurement falls BETWEEN two published rows "
        f"({_fmt(lower['saturation_flow_pcu'])} / {_fmt(upper['saturation_flow_pcu'])} pcu). The "
        "guideline states no interpolation rule, so this system's named policy applied: snap to "
        f"the LOWER row ({_fmt(lower['saturation_flow_pcu'])} pcu), because a lower row carries a "
        "larger safety distance and therefore cannot overstate capacity the measurement does not "
        "evidence. Both rows are in the evidence, so the upper one can be chosen instead.",
        f" 測定値は表の2行の間({_fmt(lower['saturation_flow_pcu'])} / "
        f"{_fmt(upper['saturation_flow_pcu'])} pcu)にあります。指針は行間の補間規則を定めていない"
        f"ため、本システムの明示的方針により低い側の行({_fmt(lower['saturation_flow_pcu'])} pcu)を"
        "選びました — 低い行ほど安全距離が大きく、測定が裏付けない容量を過大評価しないためです。"
        "両方の行を根拠に記載しているので、上の行に変更できます。",
    )


def _scope_clause(scope: str, behavior: Mapping[str, Any], behavior_no: str) -> tuple[str, str, str]:
    reference_count = _number(behavior.get("reference_count"))
    count = _fmt(reference_count)
    if scope == SCOPE_LOCALIZED:
        return (
            f"적용 범위는 국소(localized)로 요청되었습니다 — 행태 {behavior_no}를 복제해 지정된 "
            "링크에만 적용하십시오. 다만 MRWA §5.2.5.1의 의도는 네트워크 전역 보정이므로, 국소 "
            "적용은 이 값이 대표하는 바를 바꾼다는 점을 승인 시 기록해 두십시오.",
            f"A LOCALIZED scope was requested — duplicate behaviour {behavior_no} and retarget "
            "only the named links. Note that MRWA §5.2.5.1's intent is a network-wide "
            "calibration, so localising it changes what these values represent; record that on "
            "approval.",
            f"適用範囲は局所(localized)として要求されました — 挙動 {behavior_no} を複製し、指定"
            "リンクにのみ適用してください。ただし MRWA §5.2.5.1 の意図はネットワーク全体の補正で"
            "あるため、局所適用はこれらの値が表すものを変えます。承認時に記録してください。",
        )
    return (
        f"적용 범위는 네트워크 전역입니다 — MRWA §5.2.5.1이 의도하는 도심 주행 행태 보정이며, "
        f"행태 {behavior_no}를 참조하는 모든 링크({count}곳)에 함께 적용됩니다.",
        f"The scope is NETWORK-WIDE — the urban driving-behaviour calibration MRWA §5.2.5.1 "
        f"intends — so it reaches every link that references behaviour {behavior_no} ({count} "
        "consumer(s)).",
        f"適用範囲はネットワーク全体です — MRWA §5.2.5.1 が意図する都市走行挙動の補正であり、"
        f"挙動 {behavior_no} を参照するすべてのリンク({count} 件)に適用されます。",
    )


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _sample_minimums() -> dict[str, float | None]:
    """Appendix B §B.4 Table B-3's minimums, read out of the reference pack."""
    out: dict[str, float | None] = {
        "vehicles_per_sample": None,
        "vehicles_in_queue": None,
        "samples_per_lane": None,
    }
    for entry in mrwa_reference.SATURATION_FLOW_SAMPLE_MINIMUMS.entries:
        if not isinstance(entry.value, (int, float)):
            continue
        if entry.unit == "vehicles/sample":
            out["vehicles_per_sample"] = float(entry.value)
        elif entry.unit == "vehicles in queue":
            out["vehicles_in_queue"] = float(entry.value)
        elif entry.unit == "samples/lane":
            out["samples_per_lane"] = float(entry.value)
    return out


def _number(value: Any) -> float | None:
    """A finite float, or ``None``.

    NaN and the infinities are rejected here rather than at each call site, and
    that is load-bearing rather than tidy: every comparison against NaN is
    ``False``, so a NaN saturation flow slipped through :func:`lookup_table_5_6`'s
    plausibility guard, its below-table guard AND its above-table guard, and
    reached the bracketing step with no row on either side of it. Caught by
    ``test_implausible_and_unparseable_values_never_select_a_row``. A value that
    cannot be compared cannot be looked up, so it is not a number here.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _same(left: Any, right: Any) -> bool:
    """Two parameter values are the same number, within float noise.

    Table 5-6's values are published to two decimals but arrive back from a
    model as whatever the .inpx stored, so an exact ``==`` would report a
    behaviour that IS on a published row as matching none — a false alarm in
    the one direction :func:`verify_applied_row` must not produce.
    """
    a, b = _number(left), _number(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= 1e-6


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _fmt(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{round(number, 3):g}"


__all__ = [
    "ASSUMPTIONS",
    "ASSUME_BEHAVIOR_IS_URBAN",
    "ASSUME_CHANGE_IS_NETWORK_WIDE",
    "ASSUME_KEY_INTERSECTION_REPRESENTS_NETWORK",
    "ASSUME_STRAIGHT_MOVEMENT",
    "BEHAVIOR_SCOPES",
    "BETWEEN_ROWS_POLICY",
    "BETWEEN_ROWS_POLICY_BASIS",
    "BRACKETED",
    "CAR_FOLLOWING_FROM_SATURATION_FLOW",
    "CONFIDENCE_TABLE_LOOKUP",
    "DERIVATION_BLOCKERS",
    "DERIVED",
    "EXACT",
    "FORECAST_PATHS",
    "GREEN_TIME_FULL",
    "GREEN_TIME_NONE",
    "GREEN_TIME_PARTIAL",
    "GREEN_TIME_STATES",
    "GREEN_TIME_UNAVAILABLE",
    "IMPLAUSIBLE",
    "LOOKUP_STATUSES",
    "MEASUREMENT_PROVENANCE_MISSING",
    "NO_DRIVING_BEHAVIOR_TARGET",
    "NO_SATURATION_FLOW_MEASUREMENT",
    "OBSERVED",
    "OUT_OF_RANGE_HIGH",
    "OUT_OF_RANGE_LOW",
    "PATH_APPROVED_SET",
    "PATH_DIRECT_EDIT",
    "PCU_DECOMPOSITION_UNAVAILABLE",
    "PROVENANCES",
    "REASON_CODES",
    "ROW_MATCHED",
    "ROW_UNMATCHED",
    "ROW_UNREADABLE",
    "ROW_VERIFICATION_STATUSES",
    "RUNG_IS_NOT_CAPACITY_LIMIT",
    "SAMPLE_SIZE_BELOW_MRWA_MINIMUM",
    "SATURATION_FLOW_ABOVE_TABLE",
    "SATURATION_FLOW_BELOW_TABLE",
    "SATURATION_FLOW_IMPLAUSIBLE",
    "SATURATION_FLOW_NOT_DERIVABLE",
    "SCOPE_LOCALIZED",
    "SCOPE_NETWORK_WIDE",
    "derivation_is_still_blocked",
    "derive_measurement",
    "grant_from_proposals",
    "green_time_availability",
    "lookup_table_5_6",
    "normalize_behavior_scope",
    "observed_measurement",
    "propose_car_following_parameters",
    "verify_applied_row",
]

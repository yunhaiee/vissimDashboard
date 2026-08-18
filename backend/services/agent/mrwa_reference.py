"""MRWA constraint/defaults pack — structured reference data, self-citing.

Phase 0.3 (docs/product/master-roadmap-2026-07.md item 0.3, track C). Main
Roads Western Australia's own published numbers, encoded as data so
``constraints.py`` (and any future genesis/data-quality consumer, per
docs/product/tk-requirements-2026-07-31.md EPIC 1 "MRWA 기본값 라이브러리")
can warn "citing the authority's page" instead of a bare number the reader
has to trust blind.

Source documents this module draws from (all four were the required
pre-reading for this unit):
  - MRWA *Operational Modelling Guidelines* v2.0 (2021-01) — cited as "op".
  - The same document's §5 "Vissim" section, pp.197-239 of the 289-page PDF
    (confirmed present per docs/meeting-brief-tk-mrwa.md's 2026-07-31
    correction) — cited as "op §5".
  - MRWA Appendix B "Saturation Flow Information" — local extract
    docs/product/reference/mrwa-appendix-b-extract.md, page cites "p.B-N".
  - MRWA Appendix A "Signal Data Information for Modelling" — local extract
    docs/product/reference/mrwa-appendix-a-extract.md, page cites "p.N".
  - docs/meeting-brief-tk-mrwa.md §2 proposal ① and
    docs/product/tk-requirements-2026-07-31.md's "§5 정독 결과" section, which
    between them are the ONLY source for the specific Table 5-6/5-8/5-9/5-10
    values this task brief itself supplied (this codebase's two local
    Appendix extracts do not cover §5 at all — §5 is in the main
    Operational Modelling Guidelines PDF, not the Appendix B/A PDFs).

CITATION DISCIPLINE — the 0.7 unit's precedent (see
docs/product/adapter-contract.md's own "Honest citation" note, reproduced in
spirit here): every :class:`ReferenceValue` below carries a ``citation``
string. Where a number reached this module without an exact page attached to
it, the citation says literally "page cite pending" rather than a guessed
page — the never-invent rule applies to this module's own authority claims
first. Concretely:
  - Table 5-9 (gap acceptance) and Table 5-10 (conflict-area gap) and
    Table 5-6 (safety-distance/saturation-flow) are all §5 tables; this
    module's local materials place §5 at pp.197-239 but do not carry the
    individual table's own page within that range, so all three cite
    "op §5, Table 5-N, page cite pending".
  - The roundabout critical-gap minimums DO carry a page
    (docs/meeting-brief-tk-mrwa.md §2 proposal ①: "회전교차로 임계 gap 최소
    2.1s/1.5s(op p.53)") — cited as "op p.53" accordingly, not pending.
  - The detector setback/length defaults were already cited once, by
    services.agent.tools_edit.py's own ``_MRWA_DETECTOR_SETBACK_M``/
    ``_MRWA_DETECTOR_LENGTH_M`` comment, as "Appendix A, p.78" — but that
    comment ALSO records that this codebase's own local Appendix A extract
    (docs/product/reference/mrwa-appendix-a-extract.md) runs only to ~p.49
    and does not itself carry p.78, so that page could not be independently
    re-verified from material available on this machine. This module
    inherits the same page cite with the same caveat, rather than either
    silently dropping it or inventing independent confirmation — see
    :data:`DETECTOR_SETBACK_M`/:data:`DETECTOR_LENGTH_M`'s own citation
    strings.
  - Table 5-8 (reduced-speed-area design speed by turn radius) is listed in
    this unit's own task brief as a table to encode, but no radius/speed
    breakpoints for it were supplied by any source available to this
    module (not the task brief, not either local Appendix extract, not the
    two meeting-notes documents). Rather than inventing plausible-looking
    numbers, :data:`TABLE_5_8_RSA_DESIGN_SPEED` ships as a deliberately
    EMPTY, ``complete=False`` table — a documented gap, not a silent one.
    See its own docstring and docs/phase2/DECISIONS.md's drafted entry for
    this unit.
  - Table 5-9/5-10/5-6 are each PARTIAL too: only the specific rows this
    unit's task brief itself transcribed are encoded (two movement types
    for 5-9, front/rear/merge for 5-10, two saturation-flow points for
    5-6). Each table's ``complete`` flag is ``False`` and its
    ``coverage_note`` says so — never padded with invented intermediate
    rows.

This module is intentionally stdlib-only (dataclasses + typing), like
constraints.py itself — pure data, no side effects, no dependency on
state.py/trace.py/any COM-facing module, so it can be imported and iterated
over in a test with zero fixture setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["MINIMUM", "MAXIMUM", "RANGE", "DEFAULT"]

#: The literal marker used everywhere in this module (and honoured by
#: test_agent_mrwa_reference.py's integrity test) when a number reached this
#: module without an exact source page — never replace this string with a
#: guessed page number.
PAGE_CITE_PENDING = "page cite pending"


@dataclass(frozen=True)
class ReferenceValue:
    """One MRWA-published number (or a bounded pair), always self-citing.

    ``value`` is a single number for MINIMUM/MAXIMUM/DEFAULT, or a
    ``(low, high)`` tuple for RANGE. ``unit`` is a short string ("s", "m",
    "km/h", "PCU/h", "%", "steps/s", ...). ``citation`` names the source
    document + page, or :data:`PAGE_CITE_PENDING`. ``context`` is a short
    qualifier (movement type, geometry, ...) distinguishing this entry from
    siblings in the same table. ``note`` carries any honesty caveat that
    belongs on this specific entry (e.g. an inherited-not-independently-
    verified page cite).
    """

    kind: Kind
    value: float | tuple[float, float]
    unit: str
    citation: str
    context: str = ""
    note: str = ""


@dataclass(frozen=True)
class ReferenceTable:
    """A named group of :class:`ReferenceValue` entries plus honesty
    metadata about how much of the real MRWA table this module actually
    encodes."""

    name: str
    source: str
    entries: tuple[ReferenceValue, ...]
    complete: bool
    coverage_note: str = ""


# =============================================================================
# 1. Saturation flow (Appendix B) — method hierarchy, RR67 formula inputs,
#    plausibility bands. All citations below are taken verbatim from
#    docs/product/reference/mrwa-appendix-b-extract.md, which already carries
#    real Appendix-B page numbers throughout.
# =============================================================================

#: Main Roads' own preference order for arriving at a saturation flow number
#: (Appendix B §B.3) — ordered, most-preferred first. Not numeric, so kept as
#: plain strings with their own citations rather than forced into
#: ReferenceValue.
SATURATION_FLOW_METHOD_HIERARCHY: tuple[tuple[str, str], ...] = (
    ("on-site measurement (critical lanes first)", "Appendix B §B.3"),
    ("similar-intersection measurement", "Appendix B §B.3"),
    ("RR67 + local factor (from measured lanes at the same site)", "Appendix B §B.3"),
    ("RR67 standalone", "Appendix B §B.3"),
    (
        "SCATS MF data (last resort; NOT recommended — only valid if fully "
        "saturated in the preceding 24h; indication only)",
        "Appendix B §B.3, p.B-9",
    ),
)

#: RR67 (Kimber et al. 1986) single-unopposed-lane formula, Appendix B p.B-8:
#:   SF = 2080 − 42·δG·G − 100·(3.25 − wl) − 140·δn, then divided by
#:   [1 + 1.5·f/r] when the lane is turning.
#: Field meanings, exactly as the extract states them:
#:   δG   uphill flag (1 if uphill grade, else 0)
#:   G    % grade (uphill only)
#:   wl   lane width, metres
#:   δn   nearside-lane flag
#:   f    turning proportion, 0-1
#:   r    turn radius, metres
#: Worked example in the source (wl=4.2, offside, f=1.0, r=22) -> 2036 PCU/h.
RR67_FORMULA = {
    "formula": "SF = (2080 - 42*deltaG*G - 100*(3.25 - wl) - 140*deltaN) / (1 + 1.5*f/r if turning else 1)",
    "citation": "Appendix B, p.B-8",
    "worked_example": {"wl": 4.2, "position": "offside", "f": 1.0, "r": 22, "sf_pcu_h": 2036},
}

#: PCU factors, Appendix B Table B-1 (no independent page cite inside the
#: extract for the table itself, distinct from the p.B-8 formula page).
SATURATION_FLOW_PCU_TABLE = ReferenceTable(
    name="Appendix B Table B-1 — PCU factors (Austroads)",
    source="Appendix B, Table B-1",
    entries=(
        ReferenceValue("DEFAULT", 1.0, "PCU", PAGE_CITE_PENDING, context="Austroads class 1 (light vehicle)"),
        ReferenceValue("DEFAULT", 2.0, "PCU", PAGE_CITE_PENDING, context="Austroads class 2-5"),
        ReferenceValue("DEFAULT", 3.0, "PCU", PAGE_CITE_PENDING, context="Austroads class 6-9"),
        ReferenceValue("DEFAULT", 4.0, "PCU", PAGE_CITE_PENDING, context="Austroads class 10-11"),
        ReferenceValue("DEFAULT", 5.0, "PCU", PAGE_CITE_PENDING, context="Austroads class 12"),
        ReferenceValue("DEFAULT", 0.2, "PCU", PAGE_CITE_PENDING, context="cycle"),
        ReferenceValue("DEFAULT", 0.4, "PCU", PAGE_CITE_PENDING, context="motorcycle"),
        ReferenceValue("DEFAULT", 2.0, "PCU", PAGE_CITE_PENDING, context="rigid bus"),
        ReferenceValue("DEFAULT", 3.0, "PCU", PAGE_CITE_PENDING, context="articulated bus"),
    ),
    complete=True,
    coverage_note="All 9 rows the extract carries are encoded; no further rows known.",
)

#: The Appendix-B extract's own "Constraint-engine candidates" section
#: (drafted by a prior unit as this task's explicit starting point) — kept
#: here as data with each band's own honesty status. On-site-measurement
#: rules (min 5 vehicles/sample, min 6 samples/lane) are REFUSE-tier data
#: quality gates, not plausibility bands, so they are recorded separately
#: below (see SATURATION_FLOW_SAMPLE_MINIMUMS).
SATURATION_FLOW_PLAUSIBILITY_BANDS = ReferenceTable(
    name="Saturation flow plausibility bands (Appendix B candidate list)",
    source="Appendix B extract's own 'Constraint-engine candidates' section",
    entries=(
        ReferenceValue("RANGE", (800.0, 2200.0), "PCU/h", PAGE_CITE_PENDING, context="measured or RR67 saturation flow"),
        ReferenceValue("MAXIMUM", 5.0, "%", "Appendix B §B.7", context="measured vs RR67 divergence before a local factor must be applied"),
        ReferenceValue("RANGE", (2.5, 5.5), "m", PAGE_CITE_PENDING, context="lane width (wl)"),
        ReferenceValue("MAXIMUM", 10.0, "%", PAGE_CITE_PENDING, context="uphill grade (G)"),
        ReferenceValue("RANGE", (12.0, 100.0), "m", PAGE_CITE_PENDING, context="turn radius (r)"),
        ReferenceValue("RANGE", (85.0, 110.0), "%", "Appendix B §B.9 (worked example: 93%)", context="local factor (measured / RR67)"),
    ),
    complete=True,
    coverage_note="All bands the extract's own candidate list names are encoded.",
)

#: On-site measurement REFUSE-tier minimums (Appendix B §B.4, Table B-3).
SATURATION_FLOW_SAMPLE_MINIMUMS = ReferenceTable(
    name="On-site saturation-flow measurement minimums",
    source="Appendix B §B.4, Table B-3",
    entries=(
        ReferenceValue("MINIMUM", 5.0, "vehicles/sample", "Appendix B §B.4, Table B-3"),
        ReferenceValue("MINIMUM", 9.0, "vehicles in queue", "Appendix B §B.4, Table B-3"),
        ReferenceValue("MINIMUM", 6.0, "samples/lane", "Appendix B §B.4, Table B-3", note="ideally 10"),
    ),
    complete=True,
)


# =============================================================================
# 2. Gap acceptance by movement type — op §5 Table 5-9.
#    PARTIAL: only the two movement types this task's own brief transcribed
#    are encoded. "critical gap" -> Vissim priority-rule gap_time_s (a real
#    TIME field). "follow-up headway" is ALSO a time in the MRWA table, but
#    Vissim's own priority-rule "headway_m" field is a spatial CLEARANCE in
#    METRES despite its name (see tools_network._parse_priority_rules'
#    docstring / vissim_com.set_priority_rule_gap_headway's own module
#    comment — a live-verified landmine, not this module's own claim) — so
#    follow-up headway is recorded here for completeness but is NOT
#    consumed by any constraints.py check: there is no matching Vissim field
#    to compare it against.
# =============================================================================

TABLE_5_9_GAP_ACCEPTANCE = ReferenceTable(
    name="op §5 Table 5-9 — gap acceptance by movement type",
    source="op §5, Table 5-9",
    entries=(
        ReferenceValue("DEFAULT", 5.0, "s", "op §5, Table 5-9, " + PAGE_CITE_PENDING, context="left turn — critical gap"),
        ReferenceValue("RANGE", (2.0, 3.0), "s", "op §5, Table 5-9, " + PAGE_CITE_PENDING, context="left turn — follow-up headway (NOT Vissim-comparable, see module docstring)"),
        ReferenceValue("DEFAULT", 4.0, "s", "op §5, Table 5-9, " + PAGE_CITE_PENDING, context="crossing, 2-lane — critical gap"),
        ReferenceValue("DEFAULT", 2.0, "s", "op §5, Table 5-9, " + PAGE_CITE_PENDING, context="crossing, 2-lane — follow-up headway (NOT Vissim-comparable, see module docstring)"),
    ),
    complete=False,
    coverage_note=(
        "Only 'left turn' and 'crossing, 2-lane' are encoded — the two movement "
        "types docs/meeting-brief-tk-mrwa.md / docs/product/tk-requirements-2026-07-31.md "
        "actually transcribed from the source PDF. The full Table 5-9 (through movements, "
        "right turn, multi-lane crossing, ...) was NOT reproduced in any material available "
        "to this module, so no further rows are invented."
    ),
)


def table_5_9_critical_gap_envelope() -> tuple[float, float]:
    """(min, max) critical-gap seconds across every movement type
    :data:`TABLE_5_9_GAP_ACCEPTANCE` actually encodes — the "overall
    published envelope" constraints.py falls back to when a priority rule's
    movement type cannot be inferred from the shadow model (today, always —
    see that check's own comment). Computed from data, not a second
    hardcoded literal, so the two never drift apart."""
    critical_gaps = [
        e.value
        for e in TABLE_5_9_GAP_ACCEPTANCE.entries
        if e.context.endswith("critical gap") and isinstance(e.value, (int, float))
    ]
    return (min(critical_gaps), max(critical_gaps))


# =============================================================================
# 3. Conflict-area front/rear gap — op §5 Table 5-10. PARTIAL (general
#    front/rear + merge only, per this unit's own task brief).
# =============================================================================

TABLE_5_10_CONFLICT_AREA_GAP = ReferenceTable(
    name="op §5 Table 5-10 — conflict-area front/rear gap",
    source="op §5, Table 5-10",
    entries=(
        ReferenceValue("RANGE", (0.5, 1.0), "s", "op §5, Table 5-10, " + PAGE_CITE_PENDING, context="front gap (general)"),
        ReferenceValue("RANGE", (2.0, 4.0), "s", "op §5, Table 5-10, " + PAGE_CITE_PENDING, context="rear gap (general)"),
        ReferenceValue("RANGE", (1.5, 3.0), "s", "op §5, Table 5-10, " + PAGE_CITE_PENDING, context="merge"),
    ),
    complete=False,
    coverage_note=(
        "Only the general front/rear bands and the merge band are encoded — the "
        "table's full per-geometry breakdown was not supplied to this module."
    ),
)


# =============================================================================
# 4. Roundabout critical-gap minimums. NOT a §5 table — cited in the main
#    Operational Modelling Guidelines body, with a real page number (unlike
#    #2/#3 above).
# =============================================================================

ROUNDABOUT_CRITICAL_GAP_MINIMUM = ReferenceTable(
    name="Roundabout critical-gap minimums",
    source="op, p.53 (docs/meeting-brief-tk-mrwa.md §2 proposal ①)",
    entries=(
        ReferenceValue("MINIMUM", 2.1, "s", "op p.53", context="single-lane circulating"),
        ReferenceValue("MINIMUM", 1.5, "s", "op p.53", context="multi-lane circulating"),
    ),
    complete=True,
)


# =============================================================================
# 5. Safety-distance <-> saturation-flow mapping — op §5 Table 5-6. PARTIAL
#    (two SF points only). The two-figure pairing's exact meaning (e.g.
#    which is "front" vs "rear", or min vs max) was not specified by any
#    source available to this module -- recorded honestly as an unlabelled
#    (low, high) pair rather than asserted.
# =============================================================================

TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW = ReferenceTable(
    name="op §5 Table 5-6 — safety distance vs saturation flow",
    source="op §5, Table 5-6",
    entries=(
        ReferenceValue(
            "RANGE", (2.75, 3.75), "s", "op §5, Table 5-6, " + PAGE_CITE_PENDING,
            context="saturation flow 1630 PCU/h",
            note="pairing's exact semantics (front/rear vs min/max) not specified in available material",
        ),
        ReferenceValue(
            "RANGE", (1.50, 2.50), "s", "op §5, Table 5-6, " + PAGE_CITE_PENDING,
            context="saturation flow 2200 PCU/h",
            note="pairing's exact semantics (front/rear vs min/max) not specified in available material",
        ),
    ),
    complete=False,
    coverage_note=(
        "Only the two saturation-flow points (1630, 2200 PCU/h) this unit's task brief "
        "named are encoded; the real table likely has intermediate rows not reproduced here. "
        "No consumer wires this table today (see module docstring) — Vissim's own W99 "
        "car-following parameters (which a 'safety distance' figure might otherwise map to) "
        "are institutionally excluded from every write verb in this codebase "
        "(agency_restricted_parameter_write, constraints.py), so no attempt is made here to "
        "assert which Vissim field, if any, this table is meant to bound."
    ),
)


# =============================================================================
# 6. Reduced-speed-area design speed by turn radius — op §5 Table 5-8.
#    DELIBERATELY EMPTY: no radius/speed breakpoints were supplied by any
#    source available to this module. See module docstring.
# =============================================================================

TABLE_5_8_RSA_DESIGN_SPEED = ReferenceTable(
    name="op §5 Table 5-8 — reduced-speed-area design speed by turn radius",
    source="op §5, Table 5-8",
    entries=(),
    complete=False,
    coverage_note=(
        "No rows encoded. No turn-radius/design-speed breakpoints for this table were "
        "supplied by this task's brief, docs/meeting-brief-tk-mrwa.md, "
        "docs/product/tk-requirements-2026-07-31.md, or either local Appendix extract — "
        "inventing plausible-looking numbers would violate this pack's own never-invent "
        "citation discipline, so this table ships as a documented gap instead. "
        "set_reduced_speed_area's own params (link_no, speed_kmh, from_m, length_m) also "
        "carry no turn-radius input, and the shadow model builds no reduced_speed_areas "
        "object list today (tools_network._reduced_speed_area_distribution_refs' own "
        "docstring), so even a complete Table 5-8 could not be geometry-matched without a "
        "further parser unit — recorded in docs/phase2/DECISIONS.md's drafted entry for this "
        "unit."
    ),
)


# =============================================================================
# 7. Detector placement defaults — Appendix A (via tools_edit.py's own prior
#    citation; see module docstring for the inherited-page caveat).
# =============================================================================

#: Kept as plain floats (not wrapped in ReferenceTable) because
#: services.agent.tools_edit.py already owns the canonical constants
#: (``_MRWA_DETECTOR_SETBACK_M`` / ``_MRWA_DETECTOR_LENGTH_M``) that
#: add_detector's own default-pos logic actually runs on; this module does
#: NOT import them (constraints.py stays a write-tool-independent leaf, per
#: its own "stdlib-only" design note) and instead re-states the same two
#: numbers with the same citation — a dedicated integrity test
#: (test_agent_mrwa_reference.py) asserts the two modules' literals stay
#: equal so they cannot silently drift apart.
DETECTOR_SETBACK_M = ReferenceValue(
    "DEFAULT", 2.0, "m",
    "Appendix A, p.78 (as already cited by services.agent.tools_edit.py's own "
    "_MRWA_DETECTOR_SETBACK_M comment; that comment's own caveat applies here too — "
    "this codebase's local Appendix A extract runs only to ~p.49 and does not itself "
    "carry p.78, so the page is inherited, not independently re-verified)",
    context="detector setback back from the stop line / signal head",
)
DETECTOR_LENGTH_M = ReferenceValue(
    "DEFAULT", 4.5, "m",
    "Appendix A, p.78 (same inherited-page caveat as DETECTOR_SETBACK_M — see its own citation string)",
    context="detector length at a signalized-intersection approach",
)


# =============================================================================
# 8. PT dwell defaults — op §5 point 3 (docs/product/tk-requirements-2026-07-31.md
#    "§5 정독 결과" §3): stop 10s, transfer 20s +/- 5s.
# =============================================================================

PT_DWELL_DEFAULTS = ReferenceTable(
    name="PT dwell defaults",
    source="op §5 (docs/product/tk-requirements-2026-07-31.md '§5 정독 결과' §3)",
    entries=(
        ReferenceValue("DEFAULT", 10.0, "s", "op §5, " + PAGE_CITE_PENDING, context="stop dwell"),
        ReferenceValue("DEFAULT", 20.0, "s", "op §5, " + PAGE_CITE_PENDING, context="transfer dwell", note="tolerance +/- 5s"),
    ),
    complete=True,
    coverage_note=(
        "Both values the source summary names are encoded. The real Murdoch model's own "
        "shared BusDwell distribution (10s +/- 2s across all 18 stops, none individually "
        "wired — see docs/meeting-brief-tk-mrwa.md §2 item ④) approximates the stop-dwell "
        "default but was never independently checked against it beyond that summary note."
    ),
)


# =============================================================================
# 9. Simulation discipline — op §5 point 2 (resolution, seeds, warm-up).
#    NOT wired into any new constraint this unit ships: the existing
#    simulation_resolution_unpinned_across_comparison check (constraints.py)
#    already enforces PTV's own GENERIC floor (5 steps/s); MRWA's own
#    STRICTER fixed-10-steps/s-across-all-scenarios rule is data-only here,
#    awaiting a consumer, per this unit's own "ship the reference data" rule
#    for anything no verb exposes today (deliberately not force-fit onto the
#    existing, differently-sourced check).
# =============================================================================

SIMULATION_DISCIPLINE = ReferenceTable(
    name="Simulation run discipline",
    source="op §5 (docs/product/tk-requirements-2026-07-31.md '§5 정독 결과' §2)",
    entries=(
        ReferenceValue("DEFAULT", 10.0, "steps/s", "op §5, " + PAGE_CITE_PENDING, context="simulation resolution, fixed across all scenarios in a comparison"),
        ReferenceValue("RANGE", (5.0, 10.0), "seeds", "op §5, " + PAGE_CITE_PENDING, context="run count, identical seed set across all scenarios"),
    ),
    complete=True,
    coverage_note=(
        "Warm-up (max(15 min, 2x longest travel time)) is a FORMULA, not a bare number, so "
        "it is recorded separately below rather than forced into ReferenceValue's "
        "single-number/range shape."
    ),
)

WARMUP_FORMULA = {
    "formula": "max(15 minutes, 2 * longest_travel_time)",
    "citation": "op §5, " + PAGE_CITE_PENDING,
    "note": "cool-down uses the same value as warm-up, per the same source summary.",
}


# =============================================================================
# 8. The calibration ORDER — op §2.12.1's thirteen steps.
#
# Not a number: an ordered procedure. It is here rather than in its consumer
# for the same reason every table above is — it is the authority's own text,
# and a diagnosis that tells a modeller "measure the stop-line saturation
# flow next" is making an authority claim that must carry the authority's own
# step number and page.
#
# Page basis, stated so the next reader does not have to re-derive it: the
# source PDF (client-data/operational-modelling-v2.pdf, 289 pages) is offset
# from the document's own printed page numbers by 18 (document page = PDF page
# - 18). Every "p.N" in this module is a DOCUMENT page, and §2.12.1 runs
# document pages 34-42 — transcribed in docs/product/handoff-2026-08-18.md
# §4.1(b), which is where this list comes from. Same discipline as the rest of
# the module: the steps are transcribed, never re-ordered or re-worded to suit
# a consumer.
#
# The one §5 pointer this ladder needs is beside it: §5.2.5.1 is the clause
# that says what to do when every input is measured and GEH is still high, and
# its page IS independently traceable in this checkout
# (docs/product/reference/mrwa-section5-extract.md's own "5.2.5.1 General
# (p.187-189)" heading and its Table 5-6 at p.187).
# =============================================================================

CALIBRATION_STEPS_CITATION = "op §2.12.1, p.34-42"

#: op §5.2.5.1 — "default driving behaviours should generally be adopted, with
#: the exception of car following model parameters"; the additive/multiplicative
#: safety-distance parts "should be determined based on measured straight
#: movement saturation flow at key intersection(s)" (→ Table 5-6, encoded above
#: as :data:`TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW`).
CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION = "op §5.2.5.1, p.187-188"

#: ``(step number, English label, citation)`` for op §2.12.1's calibration
#: sequence, in the guideline's own order. Steps 1-9 are the CAPACITY side and
#: come BEFORE demand (10) and route choice (11) — which is the fact a GEH
#: diagnosis needs: when the inputs are measured data, the next step the
#: authority names is 7, not another demand edit.
CALIBRATION_STEPS_2_12_1: tuple[tuple[int, str, str], ...] = (
    (1, "basic network coding", CALIBRATION_STEPS_CITATION),
    (2, "vehicle speeds", CALIBRATION_STEPS_CITATION),
    (3, "unsignalised intersections", CALIBRATION_STEPS_CITATION),
    (4, "blocking", CALIBRATION_STEPS_CITATION),
    (5, "turn lanes", CALIBRATION_STEPS_CITATION),
    (6, "signalised intersections", CALIBRATION_STEPS_CITATION),
    (7, "stop-line saturation flow", CALIBRATION_STEPS_CITATION),
    (8, "lane utilisation", CALIBRATION_STEPS_CITATION),
    (9, "public transport", CALIBRATION_STEPS_CITATION),
    (10, "demand calibration", CALIBRATION_STEPS_CITATION),
    (11, "route choice", CALIBRATION_STEPS_CITATION),
    (12, "behaviour parameters", CALIBRATION_STEPS_CITATION),
    (13, "software-specific items", CALIBRATION_STEPS_CITATION),
)

#: The ordering principles stated alongside the steps. Kept verbatim in
#: substance because a consumer that cites the step order is also bound by
#: them — in particular "one parameter at a time", which is why this codebase's
#: diagnosis ladder names ONE next action rather than a list of candidates.
#:
#: Each carries its OWN citation, not the step list's. Two of the three are not
#: on the step pages at all: the first is in §2.12.1's own preamble on document
#: p.33, and the second is in §2.12's preamble — a different section — also on
#: p.33. Both were previously cited as "op §2.12.1, p.34-42", which is where
#: the numbered steps are and where neither sentence appears. A page range that
#: does not contain the text it is attached to is the failure this module
#: exists to prevent, so the citations are per-principle and each was read off
#: the source (printed footer "Page 33 of 271" = PDF page 51, the -18 offset
#: this module documents above).
CALIBRATION_PRINCIPLE_ONE_PARAMETER_CITATION = "op §2.12.1, p.33"
CALIBRATION_PRINCIPLE_INDEPENDENT_DATA_CITATION = "op §2.12, p.33"
CALIBRATION_PRINCIPLE_ROUTE_BEFORE_VOLUME_CITATION = "op §2.12.1.11, p.41"

CALIBRATION_PRINCIPLES_2_12_1: tuple[tuple[str, str], ...] = (
    ("change one parameter at a time", CALIBRATION_PRINCIPLE_ONE_PARAMETER_CITATION),
    (
        "validation data must be independent of the data used for calibration",
        CALIBRATION_PRINCIPLE_INDEPENDENT_DATA_CITATION,
    ),
    (
        "route choice (§2.12.1.11) is confirmed BEFORE volume calibration",
        CALIBRATION_PRINCIPLE_ROUTE_BEFORE_VOLUME_CITATION,
    ),
)


def calibration_step(number: int) -> tuple[int, str, str]:
    """One §2.12.1 step by its number, or raise.

    Raises rather than returning a placeholder: a diagnosis that cited a step
    the guideline does not have would be an invented authority claim, and the
    failure mode for that is a crash in a test, never a plausible sentence in
    front of a client.
    """
    for entry in CALIBRATION_STEPS_2_12_1:
        if entry[0] == number:
            return entry
    raise KeyError(f"op §2.12.1 has no step {number} (it has 1-{len(CALIBRATION_STEPS_2_12_1)})")


# =============================================================================
# Integrity helpers — consumed by test_agent_mrwa_reference.py.
# =============================================================================

ALL_TABLES: tuple[ReferenceTable, ...] = (
    SATURATION_FLOW_PCU_TABLE,
    SATURATION_FLOW_PLAUSIBILITY_BANDS,
    SATURATION_FLOW_SAMPLE_MINIMUMS,
    TABLE_5_9_GAP_ACCEPTANCE,
    TABLE_5_10_CONFLICT_AREA_GAP,
    ROUNDABOUT_CRITICAL_GAP_MINIMUM,
    TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW,
    TABLE_5_8_RSA_DESIGN_SPEED,
    PT_DWELL_DEFAULTS,
    SIMULATION_DISCIPLINE,
)

#: Standalone ReferenceValues not grouped into one of the tables above.
ALL_STANDALONE_VALUES: tuple[ReferenceValue, ...] = (
    DETECTOR_SETBACK_M,
    DETECTOR_LENGTH_M,
)


def all_entries() -> list[tuple[str, ReferenceValue]]:
    """(table_name, entry) for every :class:`ReferenceValue` this module
    ships, table entries first then standalone values — the flat list
    test_agent_mrwa_reference.py's integrity test iterates over."""
    out: list[tuple[str, ReferenceValue]] = []
    for table in ALL_TABLES:
        for entry in table.entries:
            out.append((table.name, entry))
    for value in ALL_STANDALONE_VALUES:
        out.append(("(standalone)", value))
    return out

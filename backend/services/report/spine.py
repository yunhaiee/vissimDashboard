"""MRWA report content spine — the report's skeleton, declared as data.

Unit R-1 of Track R (docs/product/report-engine-design-2026-08-24.md, layer 1:
"스타일은 피부, 뼈대는 규정"). This module answers ONE question — *which
sections does Main Roads require a modelling report to have, and what does each
one owe* — and it answers it only from
``docs/product/reference/mrwa-reporting-requirements.md``, the orchestrator's
verbatim extract of §2.12.2 (p.43) and §5.8 / §5.8.1 + Table 5-15 (p.219-220).

WHAT THIS MODULE IS NOT
-----------------------
It fetches nothing. Every section names its data source as a **SOURCE KEY**
(:data:`SOURCE_KEYS`) — a declaration that some other module owns those
numbers — and no report data is read, computed, or cached here. That is what
keeps this module a pure, stdlib-only leaf that a test can import with zero
fixture setup, exactly like ``services/agent/mrwa_reference.py``, whose
citation discipline this module copies wholesale.

CITATION DISCIPLINE (inherited from ``services/agent/mrwa_reference.py`` and
from ``services/dataimport/linsig.py``'s ``CIT_*`` mechanism)
-------------------------------------------------------------------------
Every ``CIT_*`` constant below reads

    MRWA OMG v2.0 <clause> <page> (<EXTRACT>) — '<verbatim quote>'

and the quote is VERBATIM from :data:`EXTRACT`. ``tests/test_report_spine.py``
pins that BOTH WAYS, mechanically:

* module → file: every quote in every ``CIT_*`` is found in the extract, and
  the page the constant claims is the page the extract prints after that very
  quote (so a reworded quote AND a moved page number each fail);
* file → module: every double-quoted span the extract carries is quoted by
  some ``CIT_*`` here, and every Table 5-15 cell is transcribed — so a
  requirement cannot be silently dropped from the spine either.

Nothing in this module is typed into the test as an expected string; the test
reads this module's own constants and asserts them against the extract file.

THE SPINE IS A MINIMUM, NOT A CEILING
-------------------------------------
See :data:`SPINE_IS_A_MINIMUM` — the guideline's own "include, but are not
limited to" clause, quoted with its page. The regulation-derived core is
:data:`REGULATION_SECTIONS` and it is frozen; a caller that needs more sections
passes them to :func:`build_spine` / :func:`spine_payload` instead of editing
the core. A house section is an addition to a regulatory minimum, never an
amendment of it.

HONESTY MARKERS ON EVERY TABLE 5-15 OUTPUT
------------------------------------------
Each of the 20 outputs Table 5-15 lists (7 network-wide, 4 intersection, 4
route, 5 critical-section) carries an :class:`Availability` this
BUILD can defend today. ``AVAILABLE`` means a real extractor exists in this
checkout and is named (:class:`Extractor`: module, symbol, and the literal
tokens — result-table and column names — that must appear in that module's
source). ``NOT_AVAILABLE`` means no extractor exists, and it carries the reason.
Each was measured against the readers, not guessed; the per-output ``reason``
strings carry the measurement. The audit, in one place:

* Network-wide — six of seven AVAILABLE from
  ``services/results_reader.py::_read_db``'s
  ``VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass`` query.
  **Total latent traffic demand is NOT_AVAILABLE**, and that gap is already
  established in this codebase rather than newly claimed here: unit U1 recorded
  it in ``services/agent/tools_run.py``'s module docstring ("deleted_vehicles is
  always None today ... The same holds for ``latent_demand_vehicles``") and
  ``services/agent/anomalies.py`` registers ``latent_demand_diffusion`` as
  ``NOT_MEASURABLE`` on that basis.
* Intersection — all four AVAILABLE (``results_evidence.read_movement_class_rows``
  for volume and delay, ``read_queue_rows`` for queue length,
  ``results_evidence.los_band`` for the LOS grade).
* Route — three of four AVAILABLE from the travel-time measurement query.
  **Average delay time is NOT_AVAILABLE**: that query selects VEHS/TRAVTM/
  DISTTRAV only, and no Vissim delay-measurement table is read anywhere in this
  backend.
* Critical section — volume and speed AVAILABLE from the data-collection query.
  **Density, Heat map and LoS are NOT_AVAILABLE** — the first two are the
  extract's own named caveat, and this row's LoS follows Density down because
  the only LOS extractor in the build grades signalized control delay, which is
  not what "Link · Data collection" produces.

An output nothing reads is NOT_AVAILABLE with a stated reason. It is never
quietly dropped from the transcription: a requirement missing from the spine
reads to a client as a requirement that does not exist.

WHAT THE AUDIT PROVES, AND WHAT IT DOES NOT
-------------------------------------------
Stated exactly, because an audit that is trusted for more than it checks is
worse than no audit. ``tests/test_report_spine.py`` proves, per AVAILABLE
output:

* the cited reader module IMPORTS, and the cited symbol EXISTS on it;
* every evidence token APPEARS in that module's source (word-boundary match),
  so a reader that keeps its function name but stops selecting the column
  fails;
* within one Table 5-15 row, no two outputs cite the SAME
  ``(module, symbol, evidence)`` signature.

It does **not** prove that the column named is the right QUANTITY for that
output. A token search establishes that the reader touches that identifier,
not that the identifier holds what the guideline asked for. Two consequences,
named rather than papered over:

* **A wrong-but-unique column passes.** The token has to be real and has to be
  in the CITED reader — a name invented, or borrowed from a different module,
  fails. Anything else in that reader passes: pointing Route/"Average travel
  time" at ``STOPSAVG`` (a real ``results_reader`` column, belonging to the
  network performance measurement, meaning average stops per vehicle) leaves
  all tests green. Measured, not asserted — that mutation was run.
  Only the *careless-copy-paste* class is mechanically caught, and it is caught
  by the within-row distinctness rule above: a sibling's column pasted over its
  neighbour's in the SAME row makes two outputs identical, which fails.
* **Quantity-to-column correctness is a human-review property.** The
  per-output ``reason`` strings exist so a reviewer can check the mapping by
  reading, and the row/column pairs above were read off the reader SQL when
  this unit was written. No test can restate the meaning of a Vissim column
  without becoming a second, unverified transcription of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

#: The single source for every citation string below. Repo-relative, so a test
#: can resolve it and read the file the citations claim to quote.
EXTRACT = "docs/product/reference/mrwa-reporting-requirements.md"


# =============================================================================
# 1. Citations — verbatim quotes with their pages.
#
# Discovery in the test is BY NAME PREFIX (``CIT_``), the mechanism
# tests/test_dataimport_fixed_time.py uses for services/dataimport/*.py, so a
# tenth citation added later is covered the day it is written.
# =============================================================================

# --- §2.12.2 Model Validation (p.43) -----------------------------------------

CIT_2_12_2_PARAMETER_RECORD = (
    f"MRWA OMG v2.0 §2.12.2 p.43 ({EXTRACT}) — 'Adjustments made to parameters during the "
    "model development process should be documented in the modelling report to provide a "
    "record of the changes.'"
)
CIT_2_12_2_STATISTICAL_COMPARISON = (
    f"MRWA OMG v2.0 §2.12.2 p.43 ({EXTRACT}) — 'In addition to this record, Main Roads "
    "requires a statistical comparison of model performance with on-street observations.'"
)
CIT_2_12_2_SEPARATE_SECTIONS = (
    f"MRWA OMG v2.0 §2.12.2 p.43 ({EXTRACT}) — 'The modelling report should include separate "
    "sections for the reporting of calibration and validation and the modeller should state "
    "which of the observed datasets have been used for each process.'"
)
CIT_2_12_2_DATASET_COMPARISON = (
    f"MRWA OMG v2.0 §2.12.2 p.43 ({EXTRACT}) — 'A comparison of each of the observed datasets "
    "against modelled outputs should then be presented in either the calibration or validation "
    "sections of the report.'"
)
CIT_2_12_2_LIMITATIONS = (
    f"MRWA OMG v2.0 §2.12.2 p.43 ({EXTRACT}) — 'The decision to accept or reject a model lies "
    "with Main Roads and modellers should explain any limitations in the modelling that might "
    "prevent the model from achieving the guideline calibration and validation criteria.'"
)

# --- §5.8 Model Output (p.219) -----------------------------------------------

CIT_5_8_REPORT_ASSESSMENT = (
    f"MRWA OMG v2.0 §5.8 p.219 ({EXTRACT}) — 'Modellers are required to report model "
    "assessment results to Main Roads for review when the base model is validated and when "
    "proposed option models are completed.'"
)
CIT_5_8_FINAL_OUTCOMES = (
    f"MRWA OMG v2.0 §5.8 p.219 ({EXTRACT}) — 'Main Roads will require the model evaluation "
    "results to be reported as the final model outcomes in the modelling report. The model "
    "outputs include, but are not limited to those shown in Table 5-15. The results extracted "
    "from the model should be clearly presented in tabular or graphical forms.'"
)

#: The clause that makes this spine a floor rather than a ceiling. Split out
#: from :data:`CIT_5_8_FINAL_OUTCOMES` (which contains it) so
#: :data:`SPINE_IS_A_MINIMUM` can cite the exact words rather than the whole
#: paragraph, and so dropping the minimum sentence fails a test by name.
CIT_5_8_MINIMUM_CLAUSE = (
    f"MRWA OMG v2.0 §5.8 p.219 ({EXTRACT}) — 'include, but are not limited to'"
)

# --- §5.8.1.1 / §5.8.1.2 (p.220) ---------------------------------------------

CIT_5_8_1_1_NETWORK_WIDE = (
    f"MRWA OMG v2.0 §5.8.1.1 p.220 ({EXTRACT}) — 'Network-wide model performance outputs "
    "should be presented for the scoped evaluation time period with appropriate time "
    "intervals.'"
)
CIT_5_8_1_2_INTERSECTION = (
    f"MRWA OMG v2.0 §5.8.1.2 p.220 ({EXTRACT}) — 'The intersection performance outputs should "
    "include directional traffic volume, vehicle delay, level of service and queue length.'"
)


#: **The spine is a MINIMUM.** Stated at module level, quoting the guideline's
#: own clause with its page, because every consumer of this module (the /report
#: UI, the DOCX builder) is about to present the section list to a client and a
#: list presented as complete when the authority called it a floor is a
#: misrepresentation of the authority. Extension is by :func:`build_spine`'s
#: ``extra_sections``, never by editing :data:`REGULATION_SECTIONS`.
SPINE_IS_A_MINIMUM = (
    "This spine is the MINIMUM Main Roads requires, not the whole of a report: §5.8 says the "
    f"model outputs {CIT_5_8_MINIMUM_CLAUSE}. Sections and outputs beyond it are added through "
    "build_spine(extra_sections=...) and never by editing the regulation-derived core."
)


# =============================================================================
# 2. Source keys — WHAT FILLS a section, declared, never fetched.
# =============================================================================

@dataclass(frozen=True)
class SourceKey:
    """One product data source a spine section draws on.

    ``owner`` is a repo-relative path so a test can assert the named module is
    a file on this machine — a source key pointing at nothing is a section that
    can never be filled, and that should fail loudly rather than read as a plan.
    Nothing here is imported: this module stays a leaf.
    """

    key: str
    owner: str
    what: str


SOURCE_KEYS: tuple[SourceKey, ...] = (
    SourceKey(
        "approval_ledger",
        "backend/services/audit_trail.py",
        "who approved which parameter change, when, why, and from what value to what value "
        "(AuditLedger / ChangeEntry / Approver)",
    ),
    SourceKey(
        "reality_check",
        "backend/services/agent/reality_check.py",
        "the modelled-vs-observed GEH table and its per-node verdicts",
    ),
    SourceKey(
        "gate_report",
        "backend/services/genesis/gate.py",
        "the V1-V9 gate verdict, including every check that came back NOT_CHECKABLE",
    ),
    SourceKey(
        "observed_dataset_ids",
        "backend/services/observations.py",
        "the identifiers, periods and provenance of each ingested observation set",
    ),
    SourceKey(
        "holdout_split",
        "backend/services/agent/search_job.py",
        "which nodes (and which seed) were held out of calibration and used for validation "
        "instead — holdout_nodes / holdout_seed",
    ),
    SourceKey(
        "named_limitations",
        "backend/services/agent/anomalies.py",
        "the anomalies this build declares NOT_CHECKABLE or NOT_MEASURABLE, each with the "
        "measured reason it cannot be checked",
    ),
    SourceKey(
        "run_results",
        "backend/services/results_reader.py",
        "the simulation run's own result database — the numbers Table 5-15 asks for. The "
        "specific reader for each output is named on that output's Extractor.",
    ),
)

_SOURCE_KEYS_BY_KEY = {entry.key: entry for entry in SOURCE_KEYS}


def source_key(key: str) -> SourceKey:
    """One :class:`SourceKey` by name, or raise.

    Raises rather than returning a placeholder, for the same reason
    ``mrwa_reference.calibration_step`` does: a section claiming a data source
    this product does not have is an invented capability claim, and the failure
    mode for that is a red test, never a plausible sentence in a client's report.
    """
    try:
        return _SOURCE_KEYS_BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"unknown report source key {key!r} (known: {sorted(_SOURCE_KEYS_BY_KEY)})"
        ) from None


# =============================================================================
# 3. Table 5-15 — transcribed, with an availability marker on every output.
# =============================================================================

Availability = Literal["AVAILABLE", "NOT_AVAILABLE"]

AVAILABLE: Availability = "AVAILABLE"
NOT_AVAILABLE: Availability = "NOT_AVAILABLE"

#: The literal reason string for an output no code in this build reads. Used
#: verbatim (prefixed with the specific measurement) so a grep finds every one
#: of them, and so "we never looked" can never masquerade as "the data is
#: unavailable".
NO_EXTRACTOR = "no extractor in this build"


@dataclass(frozen=True)
class Extractor:
    """The code that actually gets one Table 5-15 output out of a run.

    ``module`` is a dotted import path and ``symbol`` an attribute on it, so a
    test can import the one and ``getattr`` the other — that is the proof the
    extractor exists rather than being remembered. ``evidence`` holds literal
    tokens (the Vissim result table and the column, or the constant that
    implements a derived grade) which must appear in that module's SOURCE, so a
    reader that stops selecting the column fails the audit even though the
    function still imports.

    ``evidence`` is checked for EXISTENCE, not for meaning — see the module
    docstring's "WHAT THE AUDIT PROVES, AND WHAT IT DOES NOT". Two outputs in
    one Table 5-15 row may not share a signature (that is the copy-paste guard),
    but a token that exists and is simply the wrong quantity will pass. Pick the
    column by reading the reader's SQL, not by copying a neighbour.
    """

    module: str
    symbol: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RequiredOutput:
    """One cell of Table 5-15's "Model output" column, plus this build's honest
    claim about it."""

    name: str
    availability: Availability
    extractor: Extractor | None
    reason: str


@dataclass(frozen=True)
class Table515Row:
    """One row of Table 5-15, transcribed. ``type``, ``evaluation_method`` and
    ``time_period`` are the extract's own cell text, unedited."""

    type: str
    outputs: tuple[RequiredOutput, ...]
    evaluation_method: str
    time_period: str


# --- the extractors this build really has ------------------------------------
#
# Every one below was measured against the reader named in it, not remembered:
#   services/results_reader.py:191-198  VEHICLENETWORKPERFORMANCEMEASUREMENT
#   services/results_reader.py:227-249  MOVEMENT / VEHICLETRAVELTIMEMEASUREMENT
#   services/results_reader.py:254-271  DATACOLLECTIONMEASUREMENT / queue SQL
#   services/results_evidence.py:327    read_queue_rows  (QLEN/QLENMAX)
#   services/results_evidence.py:377    read_movement_class_rows (VEHS/VEHDELAY)
#   services/results_evidence.py:453    los_band (HCM control-delay bands)

_NETWORK_TABLE = "VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass"
_TRAVEL_TIME_TABLE = "VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass"
_DATA_COLLECTION_TABLE = "DATACOLLECTIONMEASUREMENT_EvaluationTimeIntervalClass"


def _network(column: str) -> Extractor:
    return Extractor("services.results_reader", "_read_db", (_NETWORK_TABLE, column))


def _route(column: str) -> Extractor:
    return Extractor("services.results_reader", "_read_db", (_TRAVEL_TIME_TABLE, column))


def _data_collection(column: str) -> Extractor:
    return Extractor("services.results_reader", "_read_db", (_DATA_COLLECTION_TABLE, column))


def _available(name: str, extractor: Extractor, how: str) -> RequiredOutput:
    return RequiredOutput(name, AVAILABLE, extractor, how)


def _unavailable(name: str, why: str) -> RequiredOutput:
    return RequiredOutput(name, NOT_AVAILABLE, None, f"{NO_EXTRACTOR}: {why}")


#: The four rows of Table 5-15, in the extract's own order. THE single source of
#: truth — every section's required outputs are looked up here rather than
#: re-typed beside it, the same discipline ``mrwa_reference.TABLE_5_6_ROWS``
#: keeps.
TABLE_5_15_ROWS: tuple[Table515Row, ...] = (
    Table515Row(
        type="Network-wide",
        outputs=(
            _available(
                "Number of vehicle served",
                _network("VEHARR"),
                "VEHARR (vehicles arrived) from the network performance measurement",
            ),
            _available(
                "Total travel distance",
                _network("DISTTOT"),
                "DISTTOT from the network performance measurement",
            ),
            _available(
                "Total travel time",
                _network("TRAVTMTOT"),
                "TRAVTMTOT from the network performance measurement",
            ),
            _available(
                "Average speed",
                _network("SPEEDAVG"),
                "SPEEDAVG from the network performance measurement",
            ),
            _available(
                "Total delay",
                _network("DELAYTOT"),
                "DELAYTOT from the network performance measurement",
            ),
            _available(
                "Average delay per vehicle",
                _network("DELAYAVG"),
                "DELAYAVG from the network performance measurement",
            ),
            _unavailable(
                "Total latent traffic demand",
                "Vissim records DiffusTm-driven removals and latent demand only in the run's "
                ".err text log, never in any SQLite result table, so there is no result-.db "
                "column to read. Established by unit U1 in services/agent/tools_run.py's "
                "module docstring (paragraph 'deleted_vehicles is always None today'), on "
                "which services/agent/anomalies.py registers latent_demand_diffusion as "
                "NOT_MEASURABLE. Reporting it would need the .err log parsed, which nothing "
                "in this build does.",
            ),
        ),
        evaluation_method="Vehicle network performance",
        time_period="Total analysis period (peak)",
    ),
    Table515Row(
        type="Intersection",
        outputs=(
            _available(
                "Turning movement volume",
                Extractor(
                    "services.results_evidence",
                    "read_movement_class_rows",
                    ("MOVEMENT_INTERVAL_CLASS_TABLE", "VEHS"),
                ),
                "VEHS per movement OBJECT_ID, restricted to ARG_VEHICLECLASS='ALL' so the six "
                "vehicle classes a real customer .db carries are not double-counted",
            ),
            _available(
                "Approach queue length",
                Extractor(
                    "services.results_evidence",
                    "read_queue_rows",
                    ("MOVEMENT_INTERVAL_TABLE", "QLENMAX"),
                ),
                "QLEN (averaged) and QLENMAX (maximised) per movement; the approach roll-up is "
                "services/report_exports.py::los_summary_rows",
            ),
            _available(
                "Average delay for each approach and intersection",
                Extractor(
                    "services.results_evidence",
                    "read_movement_class_rows",
                    ("MOVEMENT_INTERVAL_CLASS_TABLE", "VEHDELAY"),
                ),
                "VEHDELAY per movement; the approach and whole-intersection aggregation is "
                "services/report_exports.py::los_summary_rows (weighted_overall)",
            ),
            _available(
                "LoS for each turning movement and intersection",
                Extractor("services.results_evidence", "los_band", ("_LOS_THRESHOLDS",)),
                "HCM signalized bands over the VEHDELAY above (A<=10, B<=20, C<=35, D<=55, "
                "E<=80, F>80 s/veh); None in stays None out, so an ungraded movement never "
                "acquires an 'A'",
            ),
        ),
        evaluation_method="Node · Queue counter",
        time_period="1 hour interval analysis period",
    ),
    Table515Row(
        type="Route",
        outputs=(
            _available(
                "Volume",
                _route("VEHS"),
                "VEHS per travel-time measurement OBJECT_ID",
            ),
            _available(
                "Average travel time",
                _route("TRAVTM"),
                "TRAVTM per travel-time measurement OBJECT_ID",
            ),
            _available(
                "Travel distance",
                _route("DISTTRAV"),
                "DISTTRAV per travel-time measurement OBJECT_ID",
            ),
            _unavailable(
                "Average delay time",
                "the travel-time measurement query in services/results_reader.py::_read_db "
                "selects VEHS, TRAVTM and DISTTRAV only, and no Vissim delay-measurement "
                "result table is read anywhere in this backend — this row's own evaluation "
                "method names 'Delay' as a second measurement type, and that half of it is "
                "not armed or read. Route delay is therefore not derivable from what is "
                "collected today.",
            ),
        ),
        evaluation_method="Vehicle travel time · Delay",
        time_period="1 hour interval analysis period",
    ),
    Table515Row(
        type="Critical section (for example, bottle neck)",
        outputs=(
            _available(
                "Traffic volume",
                _data_collection("VEHS"),
                "VEHS per data collection measurement OBJECT_ID",
            ),
            _unavailable(
                "Density",
                "no link-evaluation reader exists. LINKEVALSEGMENT_EvaluationTimeIntervalClass "
                "appears in services/results_evidence.py only inside EVALUATION_TABLES, which "
                "is a table-PRESENCE descriptor list — no SQL in this backend selects a density "
                "column from it. Named as a gap by the extract's own honesty caveat.",
            ),
            _unavailable(
                "LoS",
                "the only LOS extractor in this build, services/results_evidence.py::los_band, "
                "grades HCM SIGNALIZED CONTROL DELAY, which is what the Intersection row's "
                "'Node' evaluation produces. This row's method is 'Link · Data collection', "
                "whose LoS is density-based — and Density above is itself unavailable. Marking "
                "this AVAILABLE because a function named los_band exists would be claiming a "
                "grade computed from the wrong quantity.",
            ),
            _unavailable(
                "Heat map",
                "nothing in backend/services renders or exports one, and no reader produces the "
                "per-segment density field a heat map would colour. Named as a gap by the "
                "extract's own honesty caveat.",
            ),
            _available(
                "Vehicle speed",
                _data_collection("SPEEDAVGARITH"),
                "SPEEDAVGARITH and SPEEDAVGHARM per data collection measurement OBJECT_ID",
            ),
        ),
        evaluation_method="Link · Data collection",
        time_period="15 minute interval analysis period",
    ),
)

_TABLE_5_15_BY_TYPE = {row.type: row for row in TABLE_5_15_ROWS}


def table_5_15_row(row_type: str) -> Table515Row:
    """One Table 5-15 row by its ``Type`` cell, or raise.

    Raises for the same reason :func:`source_key` does — a section requiring a
    row the guideline does not publish is an invented requirement.
    """
    try:
        return _TABLE_5_15_BY_TYPE[row_type]
    except KeyError:
        raise KeyError(
            f"Table 5-15 has no row type {row_type!r} (it has: {sorted(_TABLE_5_15_BY_TYPE)})"
        ) from None


def unavailable_outputs() -> tuple[tuple[str, str], ...]:
    """``(row type, output name)`` for every output this build cannot extract.

    Computed from :data:`TABLE_5_15_ROWS` rather than listed a second time, so
    the audit and the data can never disagree — and so a consumer that wants to
    print "these five are not in this report, and here is why" has one call for
    it instead of its own scan.
    """
    return tuple(
        (row.type, output.name)
        for row in TABLE_5_15_ROWS
        for output in row.outputs
        if output.availability == NOT_AVAILABLE
    )


# =============================================================================
# 4. The spine itself — ordered sections, each derived from a quote above.
# =============================================================================

@dataclass(frozen=True)
class ReportSection:
    """One section of the report, and what makes it mandatory.

    ``basis`` holds ``CIT_*`` strings — the verbatim regulatory sentence plus
    its page — so a section can always answer "who says I have to be here".
    ``source_keys`` names :data:`SOURCE_KEYS` entries; ``required_output_types``
    names :data:`TABLE_5_15_ROWS` types. Neither is resolved at construction:
    the module stays declarative and :func:`spine_payload` does the joining.
    """

    id: str
    title_ko: str
    title_en: str
    title_ja: str
    basis: tuple[str, ...]
    source_keys: tuple[str, ...]
    required_output_types: tuple[str, ...] = ()


#: The regulation-derived core, in the source document's own order: §2.12.2's
#: five requirements as the extract lists them (p.43), then §5.8 (p.219), then
#: §5.8.1.1 and §5.8.1.2 (p.220).
#:
#: The order is the GUIDELINE's, not a house preference — which is why it is
#: stable and why a test pins it. Note what is deliberately ABSENT: 모델 목적·
#: 범위, 민감도/시나리오 and the appendices that
#: docs/product/report-engine-design-2026-08-24.md sketches for layer 1. The
#: extract does not require them, and this module transcribes requirements
#: rather than composing a table of contents; they belong in
#: ``extra_sections`` (see :data:`SPINE_IS_A_MINIMUM`), where they read as this
#: product's additions rather than as Main Roads'.
REGULATION_SECTIONS: tuple[ReportSection, ...] = (
    ReportSection(
        id="parameter_adjustment_record",
        title_ko="파라미터 조정 기록",
        title_en="Record of parameter adjustments",
        title_ja="パラメータ調整記録",
        basis=(CIT_2_12_2_PARAMETER_RECORD,),
        source_keys=("approval_ledger",),
    ),
    ReportSection(
        id="statistical_comparison",
        title_ko="모델 성능과 실측의 통계 비교",
        title_en="Statistical comparison of model performance with on-street observations",
        title_ja="モデル性能と実測の統計比較",
        basis=(CIT_2_12_2_STATISTICAL_COMPARISON,),
        source_keys=("reality_check", "observed_dataset_ids"),
    ),
    ReportSection(
        id="calibration",
        title_ko="보정 (Calibration)",
        title_en="Calibration",
        title_ja="キャリブレーション",
        basis=(CIT_2_12_2_SEPARATE_SECTIONS, CIT_2_12_2_DATASET_COMPARISON),
        source_keys=("observed_dataset_ids", "holdout_split", "reality_check"),
    ),
    ReportSection(
        id="validation",
        title_ko="검증 (Validation)",
        title_en="Validation",
        title_ja="検証",
        basis=(CIT_2_12_2_SEPARATE_SECTIONS, CIT_2_12_2_DATASET_COMPARISON),
        source_keys=("observed_dataset_ids", "holdout_split", "reality_check"),
    ),
    ReportSection(
        id="limitations",
        title_ko="한계 설명",
        title_en="Limitations in the modelling",
        title_ja="モデリング上の限界",
        basis=(CIT_2_12_2_LIMITATIONS,),
        source_keys=("gate_report", "named_limitations"),
    ),
    ReportSection(
        id="model_evaluation_results",
        title_ko="모델 평가 결과 (최종 성과)",
        title_en="Model evaluation results",
        title_ja="モデル評価結果",
        basis=(CIT_5_8_REPORT_ASSESSMENT, CIT_5_8_FINAL_OUTCOMES),
        source_keys=("run_results",),
        required_output_types=tuple(row.type for row in TABLE_5_15_ROWS),
    ),
    ReportSection(
        id="network_wide_performance",
        title_ko="네트워크 전체 성능 산출물",
        title_en="Network-wide performance outputs",
        title_ja="ネットワーク全体の性能アウトプット",
        basis=(CIT_5_8_1_1_NETWORK_WIDE,),
        source_keys=("run_results",),
        required_output_types=("Network-wide",),
    ),
    ReportSection(
        id="intersection_performance",
        title_ko="교차로 성능 산출물",
        title_en="Intersection performance outputs",
        title_ja="交差点の性能アウトプット",
        basis=(CIT_5_8_1_2_INTERSECTION,),
        source_keys=("run_results",),
        required_output_types=("Intersection",),
    ),
)


def build_spine(extra_sections: Sequence[ReportSection] = ()) -> tuple[ReportSection, ...]:
    """The regulation-derived core, optionally extended — core first, always.

    This is the ONLY supported way to add a section (see
    :data:`SPINE_IS_A_MINIMUM`). Extras are appended after the core rather than
    interleaved, so a reader can always tell where Main Roads' requirements stop
    and this product's additions begin.

    Raises on an id that collides with the core, or with another extra: two
    sections answering to one id would make the /report UI's include/exclude
    toggles ambiguous, and — worse — would let a house section shadow a
    regulatory one. Every extra is validated the same way a core section is
    (known source keys, known Table 5-15 types), because an extension that can
    name a data source this product lacks is exactly the invented-capability
    claim this module exists to prevent.
    """
    seen = {section.id for section in REGULATION_SECTIONS}
    validated: list[ReportSection] = []
    for section in extra_sections:
        if not isinstance(section, ReportSection):
            raise TypeError(f"extra_sections takes ReportSection, got {type(section).__name__}")
        if section.id in seen:
            raise ValueError(
                f"report section id {section.id!r} is already taken — an extra section may not "
                "shadow a regulation-derived one, or another extra"
            )
        for key in section.source_keys:
            source_key(key)
        for row_type in section.required_output_types:
            table_5_15_row(row_type)
        seen.add(section.id)
        validated.append(section)
    return REGULATION_SECTIONS + tuple(validated)


# =============================================================================
# 5. JSON-safe surface — what the /report UI and the DOCX builder consume.
# =============================================================================

def _extractor_payload(extractor: Extractor | None) -> dict[str, Any] | None:
    if extractor is None:
        return None
    return {
        "module": extractor.module,
        "symbol": extractor.symbol,
        "evidence": list(extractor.evidence),
    }


def _output_payload(output: RequiredOutput) -> dict[str, Any]:
    return {
        "name": output.name,
        "availability": output.availability,
        "extractor": _extractor_payload(output.extractor),
        "reason": output.reason,
    }


def _row_payload(row: Table515Row) -> dict[str, Any]:
    return {
        "type": row.type,
        "outputs": [_output_payload(output) for output in row.outputs],
        "evaluation_method": row.evaluation_method,
        "time_period": row.time_period,
    }


def _section_payload(section: ReportSection) -> dict[str, Any]:
    return {
        "id": section.id,
        "title": {
            "ko": section.title_ko,
            "en": section.title_en,
            "ja": section.title_ja,
        },
        "basis": list(section.basis),
        "source_keys": [
            {
                "key": entry.key,
                "owner": entry.owner,
                "what": entry.what,
            }
            for entry in (source_key(key) for key in section.source_keys)
        ],
        "required_outputs": [
            _row_payload(table_5_15_row(row_type)) for row_type in section.required_output_types
        ],
        "regulation_derived": section.id in {s.id for s in REGULATION_SECTIONS},
    }


def spine_payload(extra_sections: Sequence[ReportSection] = ()) -> dict[str, Any]:
    """The whole spine as plain JSON-safe data (dicts, lists, strings).

    Nothing is fetched: the source keys are resolved to their DECLARATIONS
    (key, owning module, what it holds), never to their contents. A consumer
    that wants the numbers goes to the owner named here.

    ``minimum`` is first in the payload on purpose — a consumer rendering this
    top-to-bottom shows "this is a floor" before it shows the list, which is
    the whole point of :data:`SPINE_IS_A_MINIMUM`.
    """
    sections = build_spine(extra_sections)
    return {
        "minimum": {
            "sentence": SPINE_IS_A_MINIMUM,
            "citation": CIT_5_8_MINIMUM_CLAUSE,
        },
        "extract": EXTRACT,
        "source_keys": [
            {"key": entry.key, "owner": entry.owner, "what": entry.what}
            for entry in SOURCE_KEYS
        ],
        "table_5_15": [_row_payload(row) for row in TABLE_5_15_ROWS],
        "unavailable_outputs": [
            {"type": row_type, "output": name} for row_type, name in unavailable_outputs()
        ],
        "sections": [_section_payload(section) for section in sections],
    }


__all__ = [
    "AVAILABLE",
    "Availability",
    "EXTRACT",
    "Extractor",
    "NOT_AVAILABLE",
    "NO_EXTRACTOR",
    "REGULATION_SECTIONS",
    "ReportSection",
    "RequiredOutput",
    "SOURCE_KEYS",
    "SPINE_IS_A_MINIMUM",
    "SourceKey",
    "TABLE_5_15_ROWS",
    "Table515Row",
    "build_spine",
    "source_key",
    "spine_payload",
    "table_5_15_row",
    "unavailable_outputs",
]

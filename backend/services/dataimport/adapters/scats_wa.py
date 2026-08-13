"""SCATS / Main Roads WA adapter — **stub** (roadmap 0.7, completed at 4.x).

Purpose, precisely: this is not a finished importer, it is the *proof that the
neutral contract holds*. MRWA Appendix A documents, field by field, what Main
Roads hands a modeller (Phase History, Event History, the four-tab Signal Data
spreadsheet, Detector Volume Data, LMA/LMB drawings). :data:`SCATS_FIELD_MAP`
maps every one of those documented fields onto a real field of
``schema.py``, and ``tests/test_dataimport_scats_wa.py`` asserts that every
declared neutral path resolves. If Appendix A's fields could not be pointed at
real schema fields, the schema would be wrong — and while writing this map,
three times it was (see the module-level notes in ``schema.py`` and
docs/product/adapter-contract.md §"What Appendix A changed").

What the stub really parses: a **CSV subset** in the shapes Appendix A
describes, one file per structure/tab. The real deliverables are .xlsx
workbooks, .csv event logs and .doc phase-sequence charts; reading .xlsx tabs
and the drawings is deferred with the rest of adapter #2 (roadmap Phase 4).
Every unparsed source in Appendix A still appears in the field map, flagged
``parsed_by_stub=False``, so the gap is documented rather than forgotten.

Two behaviours here matter more than the parsing:

* **It refuses rather than guesses.** No Site tab means no TCS number means no
  way to attribute a single record to a site — the adapter refuses instead of
  deriving a site id from a file name. No Timings tab means no yellow and no
  all-red — the adapter leaves them ``None`` and warns, rather than computing
  them from Appendix-A A.3's Austroads formula, which needs approach speed and
  grade that none of these files carry. No Phase History means no complete
  cycle count means ped demand frequency (A.2 p.11) is not computable — again
  a warning and an ``absent`` note, never a plausible number.
* **Everything it does compute says so.** The average cycle c̄ it derives from
  Phase History carries ``Provenance.derived_by`` naming A.2 as its authority.
  The full A.2 treatment (frequency-adjusted phase times, coordinated-site
  cycle recount per Fig A-5) is roadmap 1.6's module, not an adapter's, and the
  stub says so in an INFO issue rather than half-doing it.

Nothing here touches Vissim.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from ..base import (
    AdapterError,
    AdapterInfo,
    DataAdapter,
    ImportIssue,
    ImportResult,
    IssueSeverity,
    SniffResult,
)
from ..xlsx_guard import XlsxBombError, guard_xlsx_path
from ..schema import (
    ApproachRef,
    CanonicalVehicleClass,
    ClassificationScheme,
    ControlType,
    CoordinationData,
    CoordinationPoint,
    DetectorCountData,
    DetectorData,
    DriveSide,
    MovementRef,
    PedestrianPhaseData,
    PedestrianProtectionType,
    PhaseData,
    PositionHint,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalGroupData,
    SignalGroupKind,
    SignalGroupWindow,
    SignalProgramData,
    SiteMetadata,
    TimeWindow,
    TurningCountData,
    VehicleClassRef,
    VolumeUnit,
)

CIT_A1 = "MRWA Appendix A §A.1 (p.3–5) — source data Main Roads provides."
CIT_A2 = "MRWA Appendix A §A.2 — derivation algorithms (stretch phase, avg cycle, frequency)."
CIT_A2_P11 = "MRWA Appendix A §A.2 (p.11) — ped demand frequency = demands met ÷ complete cycles."
CIT_A3 = "MRWA Appendix A §A.3 — time settings (yellow, all-red, pedestrian, protection types)."
CIT_A5 = "MRWA Appendix A §A.5 — coordination, subsystems, link/offset plans."
CIT_A1_XLSX = (
    "MRWA Appendix A §A.1 — Site tab / Detector Volume Data, read from the REAL .xlsx "
    "workbook (LM00398/LM00972/LM00973.2026-02.xlsx, the WA drop measured 2026-08-10), not "
    "the hand-authored CSV subset the rest of this module was built against."
)


class FieldMapping(BaseModel):
    """One documented Appendix-A field and where it lands in the neutral model.

    ``neutral_path`` is a dotted path into ``schema.py`` that
    ``schema.neutral_field_path_exists`` can resolve — the map is therefore
    *checked*, not merely prose. ``parsed_by_stub`` separates "the contract
    covers this" from "this stub reads it today".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_structure: str
    source_field: str
    neutral_path: str
    parsed_by_stub: bool
    citation: str
    note: str = ""


SCATS_FIELD_MAP: tuple[FieldMapping, ...] = (
    # -- Signal Data spreadsheet, Site tab ---------------------------------
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab",
        source_field="TCS number",
        neutral_path="SignalControllerData.controller_id",
        parsed_by_stub=True,
        citation=CIT_A1,
        note="Also becomes SiteMetadata.site_id. Kept as a string — TCS ids are codes.",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab",
        source_field="Site name",
        neutral_path="SiteMetadata.site_name",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab",
        source_field="Region",
        neutral_path="SignalControllerData.region",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    # -- Signal Data spreadsheet, Timings tab ------------------------------
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Late start",
        neutral_path="SignalGroupData.late_start_s",
        parsed_by_stub=True,
        citation=CIT_A1,
        note="Per signal group, cross-checkable against the Phase Sequence Chart markup.",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Early cut-off",
        neutral_path="SignalGroupData.early_cut_off_s",
        parsed_by_stub=True,
        citation=CIT_A3,
        note="A.3: intergreen = early cut-off + yellow + all-red.",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Minimum green",
        neutral_path="PhaseData.min_green_s",
        parsed_by_stub=True,
        citation=CIT_A1,
        note="MRWA §5: min green is used as the controller states it, never adjusted.",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Maximum green",
        neutral_path="PhaseData.max_green_s",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Yellow",
        neutral_path="PhaseData.amber_s",
        parsed_by_stub=True,
        citation=CIT_A3,
        note="A.3 Table A-2 derives it from speed/grade; we only ever READ it here.",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="All-red",
        neutral_path="PhaseData.all_red_s",
        parsed_by_stub=True,
        citation=CIT_A3,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Pedestrian delay",
        neutral_path="PedestrianPhaseData.delay_s",
        parsed_by_stub=True,
        citation=CIT_A3,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Pedestrian walk",
        neutral_path="PedestrianPhaseData.walk_s",
        parsed_by_stub=True,
        citation=CIT_A3,
        note="WA standard is 6 s; we read the site's value, we do not assume the standard.",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Pedestrian clearance",
        neutral_path="PedestrianPhaseData.clearance_1_s",
        parsed_by_stub=True,
        citation=CIT_A3,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Timings tab",
        source_field="Protection type",
        neutral_path="PedestrianPhaseData.protection_type",
        parsed_by_stub=True,
        citation=CIT_A3,
        note="Six A.3 arrangements; unmapped tokens stay in protection_type_raw.",
    ),
    FieldMapping(
        source_structure="SCATS Special Times cell 11",
        source_field="Protection time",
        neutral_path="PedestrianPhaseData.protection_time_s",
        parsed_by_stub=False,
        citation=CIT_A3,
        note="A.3: existing sites hold protection time here; not in the CSV subset.",
    ),
    # -- Signal Data spreadsheet, Link & Offset Plans tab ------------------
    FieldMapping(
        source_structure="Signal Data spreadsheet / Link & Offset Plans tab",
        source_field="Subsystem",
        neutral_path="SignalControllerData.coordination.subsystem",
        parsed_by_stub=True,
        citation=CIT_A5,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Link & Offset Plans tab",
        source_field="Coordinated phase (a,b^c notation)",
        neutral_path="SignalProgramData.coordination.coordinated_phase_id",
        parsed_by_stub=True,
        citation=CIT_A5,
        note="'^' = start-of-phase coordination -> coordination.coordination_point.",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Link & Offset Plans tab",
        source_field="Offsets",
        neutral_path="SignalProgramData.coordination.offsets_s",
        parsed_by_stub=True,
        citation=CIT_A5,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Link & Offset Plans tab",
        source_field="Cycle bounds (min)",
        neutral_path="SignalControllerData.cycle_min_s",
        parsed_by_stub=True,
        citation=CIT_A5,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Link & Offset Plans tab",
        source_field="Cycle bounds (max)",
        neutral_path="SignalControllerData.cycle_max_s",
        parsed_by_stub=True,
        citation=CIT_A5,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Link & Offset Plans tab",
        source_field="Link plan number (1 night / 2 PM / 3 balanced / 4 AM)",
        neutral_path="SignalProgramData.coordination.plan_number",
        parsed_by_stub=True,
        citation=CIT_A5,
    ),
    # -- SCATS Phase History -----------------------------------------------
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Phase",
        neutral_path="SignalProgramData.phases.phase_id",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Duration (s)",
        neutral_path="SignalProgramData.phases.observed_total_duration_s",
        parsed_by_stub=True,
        citation=CIT_A2,
        note=(
            "A.2: phase duration is green PLUS its intergreen. It is NOT green_s; "
            "splitting it needs the Timings tab."
        ),
    ),
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Start / End",
        neutral_path="SignalProgramData.observation_window",
        parsed_by_stub=True,
        citation=CIT_A2,
    ),
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Occurrence count (derived)",
        neutral_path="SignalProgramData.phases.observation_count",
        parsed_by_stub=True,
        citation=CIT_A2,
    ),
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Stretch phase (derived, A.2)",
        neutral_path="SignalProgramData.phases.is_stretch_phase",
        parsed_by_stub=True,
        citation=CIT_A2,
        note="Marked only when one phase is unambiguously the most frequent.",
    ),
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Phase frequency f_i (derived, A.2)",
        neutral_path="SignalProgramData.phases.frequency",
        parsed_by_stub=True,
        citation=CIT_A2,
    ),
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Complete cycles n (derived, A.2)",
        neutral_path="SignalProgramData.observed_cycle_count",
        parsed_by_stub=True,
        citation=CIT_A2,
    ),
    FieldMapping(
        source_structure="SCATS Phase History",
        source_field="Average cycle c̄ (derived, A.2)",
        neutral_path="SignalProgramData.cycle_time_s",
        parsed_by_stub=True,
        citation=CIT_A2,
        note="Derived value; provenance.derived_by names A.2 as the authority.",
    ),
    # -- SCATS Event History ------------------------------------------------
    FieldMapping(
        source_structure="SCATS Event History",
        source_field="SGi=On / SGi=Off",
        neutral_path="SignalProgramData.signal_group_windows.green_duration_s",
        parsed_by_stub=True,
        citation=CIT_A1,
        note=(
            "Gives green LENGTH; cycle POSITION needs the stretch-phase timeline, so "
            "start_s/end_s stay None with an absent note."
        ),
    ),
    FieldMapping(
        source_structure="SCATS Event History",
        source_field="Signal group number",
        neutral_path="SignalGroupData.signal_group_id",
        parsed_by_stub=True,
        citation=CIT_A1,
        note="MRWA §5: SG numbering must match SCATS, so the number is never re-issued.",
    ),
    FieldMapping(
        source_structure="SCATS Event History",
        source_field="Walk j:Demand=Off Active=On (demand met)",
        neutral_path="PedestrianPhaseData.demand_frequency",
        parsed_by_stub=True,
        citation=CIT_A2_P11,
        note="Needs complete cycles from Phase History; not computable without it.",
    ),
    FieldMapping(
        source_structure="SCATS Event History",
        source_field="Walk j:Active=Off (clearance-1 start)",
        neutral_path="PedestrianPhaseData.walk_s",
        parsed_by_stub=True,
        citation=CIT_A3,
        note="Observed walk length = Active=On -> Active=Off.",
    ),
    # -- Detector volume data ----------------------------------------------
    FieldMapping(
        source_structure="Detector Volume Data",
        source_field="Detector volume per interval",
        neutral_path="DetectorCountData.volume",
        parsed_by_stub=True,
        citation=CIT_A1,
        note=(
            "Has NO turning movement — this is why DetectorCountData exists separately "
            "from TurningCountData."
        ),
    ),
    FieldMapping(
        source_structure="Detector Volume Data",
        source_field="Detector id",
        neutral_path="DetectorData.detector_id",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    # -- Real .xlsx workbook — Site tab (fields the CSV subset never carried) --
    #
    # Added 2026-08-10 against the 3 real WA SCATS workbooks: the hand-authored
    # CSV subset's Site tab never had a road code, an SLK or a lat/lon, so the
    # original field map had nowhere honest to point these at. Following the
    # same rule that gave DetectorCountData its own record type, the SCHEMA
    # grew fields (SiteMetadata.road_ref/road_name/stationing_km/position)
    # rather than the adapter inventing a place to put real source data.
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab (real .xlsx)",
        source_field="Road",
        neutral_path="SiteMetadata.road_ref",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab (real .xlsx)",
        source_field="Road Name",
        neutral_path="SiteMetadata.road_name",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab (real .xlsx)",
        source_field="SLK",
        neutral_path="SiteMetadata.stationing_km",
        parsed_by_stub=True,
        citation=CIT_A1,
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab (real .xlsx)",
        source_field="Latitude / Longitude",
        neutral_path="SiteMetadata.position",
        parsed_by_stub=True,
        citation=CIT_A1,
        note=(
            "x = longitude, y = latitude. The tab states plain decimal degrees, never a CRS "
            "name, so SiteMetadata.coordinate_reference is left absent rather than assuming "
            "WGS84."
        ),
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Site tab (real .xlsx)",
        source_field="Intersection",
        neutral_path="SiteMetadata.site_name",
        parsed_by_stub=True,
        citation=CIT_A1,
        note="Preferred over 'Road Name' as site_name: it names the full intersection.",
    ),
    # -- Real .xlsx workbook — Hourly / Quarter Hourly Volume tabs -------------
    FieldMapping(
        source_structure="Signal Data spreadsheet / Quarter Hourly Volume tab (real .xlsx)",
        source_field="Per-detector volume, 15-minute bins",
        neutral_path="DetectorCountData.volume",
        parsed_by_stub=True,
        citation=CIT_A1,
        note="The EMITTED granularity — MRWA's stated profiling interval (15 min).",
    ),
    FieldMapping(
        source_structure="Signal Data spreadsheet / Hourly Volume tab (real .xlsx)",
        source_field="Per-detector volume, 1-hour bins",
        neutral_path="DetectorCountData.volume",
        parsed_by_stub=True,
        citation=CIT_A1,
        note=(
            "Cross-checked against Quarter Hourly Volume (each hourly figure must equal the "
            "sum of its 4 quarter-hour bins) then DISCARDED rather than also emitted — "
            "emitting both interval lengths for the same detector would double-count the same "
            "vehicles and the quality gate would (correctly) refuse it as an overlap. Emitted "
            "directly, as 1-hour records, only when Quarter Hourly Volume is absent."
        ),
    ),
    # -- Sources we deliberately do not machine-read ------------------------
    FieldMapping(
        source_structure="Phase Sequence Chart (.doc)",
        source_field="Late-start and protection markup",
        neutral_path="SignalGroupData.late_start_s",
        parsed_by_stub=False,
        citation=CIT_A1,
        note="A human cross-check source; MRWA 1.6 note requires LMA/chart agreement.",
    ),
    FieldMapping(
        source_structure="Strategic Monitor graph",
        source_field="Active link plan in the modelled window",
        neutral_path="SignalProgramData.coordination.plan_number",
        parsed_by_stub=False,
        citation=CIT_A5,
        note="A.5: identifies which plan was running; a graph, not a data file.",
    ),
    FieldMapping(
        source_structure="LMA drawing",
        source_field="SG numbers / phases / detector layout",
        neutral_path="DetectorData.position",
        parsed_by_stub=False,
        citation=CIT_A1,
        note="Drawing. Supplies detector function and placement we otherwise leave None.",
    ),
    FieldMapping(
        source_structure="LMA drawing",
        source_field="Signal group phase membership",
        neutral_path="SignalGroupData.phase_ids",
        parsed_by_stub=False,
        citation=CIT_A1,
    ),
)


#: The six A.3 arrangements, keyed by normalised source token. Anything not in
#: here keeps its raw string and produces an INFO issue — an adapter must not
#: pick the nearest member.
PROTECTION_TYPE_MAP: dict[str, PedestrianProtectionType] = {
    "exclusive": PedestrianProtectionType.EXCLUSIVE_PHASE,
    "exclusive phase": PedestrianProtectionType.EXCLUSIVE_PHASE,
    "parallel time control": PedestrianProtectionType.PARALLEL_TIME_CONTROLLED,
    "parallel + time control": PedestrianProtectionType.PARALLEL_TIME_CONTROLLED,
    "parallel flashing yellow": PedestrianProtectionType.PARALLEL_FLASHING_YELLOW,
    "parallel + flashing yellow": PedestrianProtectionType.PARALLEL_FLASHING_YELLOW,
    "timed red arrow": PedestrianProtectionType.TIMED_RED_ARROW,
    "red arrow with flash": PedestrianProtectionType.RED_ARROW_WITH_FLASH,
    "fully controlled": PedestrianProtectionType.FULLY_CONTROLLED,
}

_SG_EVENT = re.compile(r"^SG\s*(?P<sg>\d+)\s*=\s*(?P<state>On|Off)\s*$", re.IGNORECASE)
_WALK_EVENT = re.compile(
    r"^Walk\s*(?P<walk>\d+)\s*:\s*(?P<body>.+?)\s*$", re.IGNORECASE
)

#: filename fragment -> role. Order matters: the first match wins, so the
#: specific patterns precede the generic ones.
_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("phase_history", "phase_history"),
    ("phasehistory", "phase_history"),
    ("event_history", "event_history"),
    ("eventhistory", "event_history"),
    ("link_offset", "link_offset"),
    ("linkoffset", "link_offset"),
    ("link-offset", "link_offset"),
    ("timing", "timings"),
    ("detector", "detector_volumes"),
    ("site", "site"),
)


def _classify(files: Sequence[Path]) -> dict[str, Path]:
    roles: dict[str, Path] = {}
    for path in files:
        name = path.name.lower()
        for fragment, role in _ROLE_PATTERNS:
            if fragment in name:
                roles.setdefault(role, path)
                break
    return roles


def _normalise_row(row: dict[Any, Any]) -> dict[str, str]:
    """Flatten one DictReader row to ``str -> str``.

    A row with more fields than the header lands its surplus under
    ``DictReader``'s ``restkey`` (``None``) as a *list*; a short row yields
    ``None`` values. Neither is a reason to crash a parse — ragged rows are an
    expected property of hand-maintained survey files, so they are normalised
    here and reported by the per-row checks upstream.
    """
    normalised: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        if isinstance(value, list):
            value = ", ".join(part for part in value if part)
        normalised[str(key).strip()] = str(value or "").strip()
    return normalised


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [_normalise_row(row) for row in csv.DictReader(handle)]
    except OSError as exc:  # unreadable input is not a data-quality finding
        raise AdapterError(f"cannot read {path.name}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise AdapterError(f"{path.name} is not decodable as UTF-8: {exc}") from exc


def _opt(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return value
    return None


def _opt_float(row: dict[str, str], *keys: str) -> float | None:
    raw = _opt(row, *keys)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# The REAL .xlsx workbook — Site + Hourly/Quarter Hourly Volume tabs.
#
# Measured 2026-08-10 against the 3 real WA SCATS detector-volume workbooks in
# ``client-data/Traffic signal data.zip`` (LM00398/LM00972/LM00973.2026-02.xlsx
# — never committed, never read by a test): each carries a ``Site`` tab of
# key/value rows (Traffic Signal, Road, Road Name, SLK, Latitude, Longitude,
# Intersection) and at least one of ``Hourly Volume``/``Quarter Hourly Volume``
# — 4 metadata rows, a 'Detector' group-label row, a header row naming each
# detector column by number, then one row per (day, hour|quarter-hour). Real
# files also carry ``Hourly Peak``, ``Quarter Hourly Peak``, ``Hourly Site
# Performance`` and — on some sites — ``Link and Offset Plans``, ``SCATS Phase
# History`` and ``Timings``, all in a REAL xlsx layout that is different from
# the CSV-subset layout ``_parse_timings``/``_parse_link_offset``/
# ``_parse_phase_history`` above were built against. Only Site + the two
# volume tabs are read here (see ``_parse_xlsx_workbook``'s docstring for why
# the rest is a named gap, not a silent misparse).
# ---------------------------------------------------------------------------

XLSX_SUFFIXES = (".xlsx", ".xlsm")

SITE_SHEET = "Site"
HOURLY_VOLUME_SHEET = "Hourly Volume"
QUARTER_HOURLY_VOLUME_SHEET = "Quarter Hourly Volume"

#: Real .xlsx tabs this unit has SEEN but deliberately does not parse — a
#: genuinely different layout, not a smaller version of the same one. Naming
#: them lets ``_parse_xlsx_workbook`` say exactly what it skipped instead of
#: the gap being invisible.
XLSX_UNPARSED_SHEETS_OF_INTEREST: tuple[str, ...] = (
    "Hourly Peak",
    "Quarter Hourly Peak",
    "Hourly Site Performance",
    "Link and Offset Plans",
    "SCATS Phase History",
    "Timings",
)

#: 0-indexed row layout of the volume tabs, verified against all 3 real files:
#: rows 0-3 = title/site/from/to metadata, row 4 = the 'Detector' group label,
#: row 5 = ``Day, Date, Hour|Qtr Hour, <detector numbers...>``, row 6+ = data.
#:
#: These are the EXPECTED/default position only — kept for readability and as
#: the anchor the search window below is sized around. The runtime behaviour
#: never trusts them directly: :func:`_find_volume_header_row` locates the
#: header by its CONTENT (a structural search), and the data start row is
#: derived from wherever that search actually found it. An ordinary export
#: change — one metadata row added or removed — shifts every fixed index
#: below it; trusting ``_VOLUME_HEADER_ROW`` as a position rather than a
#: default meant that shift silently fed the 'Detector' group-label row in as
#: if it were the header (fabricating one detector literally named
#: 'Detector', dropping every real detector's column with no issue at all —
#: reproduced with a synthetic one-extra-row fixture, see
#: tests/test_dataimport_scats_wa_xlsx.py).
_VOLUME_HEADER_ROW = 5
_VOLUME_DATA_START_ROW = 6
_VOLUME_DETECTOR_COL_START = 3

#: How many leading rows :func:`_find_volume_header_row` is willing to search
#: before giving up. Bounded so a workbook that is not this layout at all
#: (wrong sheet, corrupt export) fails fast with a named REFUSE instead of
#: scanning thousands of data rows for a false-positive header match; wide
#: enough to tolerate several inserted/removed metadata rows around the
#: nominal position (``_VOLUME_HEADER_ROW`` = 5).
_VOLUME_HEADER_SEARCH_WINDOW = 20

#: The header row's fixed-position tokens, case/space-insensitive. Column 2
#: differs between the two volume tabs ('Hour' vs 'Qtr Hour'), so both are
#: accepted regardless of which tab is being searched — it is the STRUCTURE,
#: not the interval label, that identifies the row.
_VOLUME_HEADER_COL0 = "day"
_VOLUME_HEADER_COL1 = "date"
_VOLUME_HEADER_COL2_TOKENS = frozenset({"hour", "qtr hour"})

#: A real SCATS detector column header is a bare number (the module docstring:
#: "a header row naming each detector column by number"). Anything else sat in
#: a detector-column position — the 'Detector' group-label text itself, a
#: stray word, a formula-error string — is a sign the header row was
#: misidentified or the column is not a detector at all, never a detector id
#: to trust.
_PLAUSIBLE_DETECTOR_ID_RE = re.compile(r"^\d{1,4}$")

_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _normalise_header_cell(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _looks_like_volume_header_row(row: Sequence[Any]) -> bool:
    """Does ``row`` structurally match ``Day, Date, Hour|Qtr Hour, ...``?

    Checked by CONTENT, not position — this is the whole fix: the same 3
    tokens are required regardless of which row index they turn up at.
    """
    if len(row) < 3:
        return False
    return (
        _normalise_header_cell(row[0]) == _VOLUME_HEADER_COL0
        and _normalise_header_cell(row[1]) == _VOLUME_HEADER_COL1
        and _normalise_header_cell(row[2]) in _VOLUME_HEADER_COL2_TOKENS
    )


def _find_volume_header_row(
    rows: Sequence[tuple[Any, ...]], *, search_window: int = _VOLUME_HEADER_SEARCH_WINDOW
) -> int | None:
    """The row index whose first 3 columns read ``Day, Date, Hour|Qtr Hour`` —
    found by searching a bounded window from the top, never assumed from
    ``_VOLUME_HEADER_ROW``. ``None`` when no row in the window matches, which
    the caller turns into a named REFUSE rather than guessing."""
    limit = min(len(rows), search_window)
    for index in range(limit):
        if _looks_like_volume_header_row(rows[index]):
            return index
    return None


def _open_scats_workbook(path: Path):
    """Open a workbook read-only, dimensions untouched (callers reset per-sheet).

    Same house pattern as ``adapters/daejeon.py``'s ``_open_worksheet``:
    unreadable bytes are an :class:`AdapterError` (a programming/input problem,
    never a data-quality finding), because there is no per-sheet issue to
    attach a "cannot even open this file" failure to.
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - openpyxl is a hard dependency
        raise AdapterError(f"openpyxl is required to read {path.name}: {exc}") from exc

    # Decompression-bomb guard BEFORE openpyxl touches the file -- same
    # reasoning as adapters/daejeon.py's `_open_worksheet`; see
    # services/dataimport/xlsx_guard.py's module docstring for the measured
    # numbers.
    try:
        guard_xlsx_path(path)
    except XlsxBombError as exc:
        raise AdapterError(str(exc)) from exc

    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except OSError as exc:
        raise AdapterError(f"cannot read {path.name}: {exc}") from exc
    except Exception as exc:  # a corrupt/non-xlsx byte stream is unreadable input
        raise AdapterError(f"{path.name} is not a readable .xlsx workbook: {exc}") from exc


def _sheet_rows(workbook: Any, name: str) -> list[tuple[Any, ...]] | None:
    """A sheet's rows as tuples, or ``None`` when the sheet is absent.

    ``reset_dimensions()`` is NOT optional here: every real SCATS workbook
    measured self-reports the wrong ``<dimension>`` and openpyxl's read-only
    mode trusts it, silently returning an empty/truncated sheet without this
    call (the same landmine ``adapters/daejeon.py`` documents for the Daejeon
    drop).
    """
    if name not in workbook.sheetnames:
        return None
    worksheet = workbook[name]
    worksheet.reset_dimensions()
    return [tuple(row) for row in worksheet.iter_rows(values_only=True)]


def _site_sheet_kv(rows: Sequence[tuple[Any, ...]]) -> dict[str, Any]:
    """The ``Site`` tab's (label, value) rows as a dict.

    Row 0 is a lone title (``'Traffic Signal Data'``) with no value column; it
    lands as a harmless ``{"Traffic Signal Data": None}`` entry that nothing
    looks up. Every other real row is exactly one (label, value) pair.
    """
    kv: dict[str, Any] = {}
    for row in rows:
        if not row:
            continue
        key = row[0]
        if key is None:
            continue
        key = str(key).strip()
        if not key:
            continue
        kv[key] = row[1] if len(row) > 1 else None
    return kv


def _kv_str(kv: Mapping[str, Any], key: str) -> str | None:
    value = kv.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _kv_float(kv: Mapping[str, Any], key: str) -> float | None:
    """Every value in the real Site tab is a STRING, including numeric-looking
    ones ('12.34', '-33.123456789012') — a fabricated example in the measured
    shape (openpyxl reads a numeric-looking cell back as ``str``, not
    ``float``), not a value copied from any client workbook."""
    value = kv.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _combine_date_and_clock(date_value: Any, clock_value: Any) -> datetime | None:
    """``Date`` (a datetime at midnight) + ``Hour``/``Qtr Hour`` ('HH:MM') ->
    one timestamp, or ``None`` when either half does not parse as expected.
    Never raises — an unusable row is the caller's problem to count and skip."""
    if not isinstance(date_value, datetime):
        return None
    if not isinstance(clock_value, str):
        return None
    match = _CLOCK_RE.match(clock_value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return date_value.replace(hour=hour, minute=minute, second=0, microsecond=0)


@dataclass
class _VolumeSheetResult:
    """One volume tab's parse: what a detector column-header names, per-detector
    per-interval values (for the hourly/quarter-hourly cross-check), the
    :class:`DetectorCountData` records this tab is willing to emit, how many
    cells were blank, and WHERE the header actually was (never assumed to be
    ``_VOLUME_HEADER_ROW`` — see :func:`_find_volume_header_row`)."""

    sheet: str
    detector_ids: list[str]
    values: dict[str, dict[datetime, float]]
    records: list[DetectorCountData]
    blank_cells: int
    rows_read: int
    header_row: int  # 0-indexed


def _parse_volume_sheet(
    path: Path,
    rows: Sequence[tuple[Any, ...]],
    sheet_name: str,
    interval: timedelta,
    site_id: str,
    controller_id: str,
    result: ImportResult,
) -> _VolumeSheetResult | None:
    """One ``Hourly Volume``/``Quarter Hourly Volume`` tab -> per-detector
    :class:`DetectorCountData`, or ``None`` (with an issue recorded) when the
    header row cannot be found, a detector column's header is not a plausible
    detector id, or the header names no detector columns at all.

    The header row is located STRUCTURALLY — by searching a bounded window
    for the ``Day, Date, Hour|Qtr Hour`` shape (:func:`_find_volume_header_row`)
    — never trusted at the nominal ``_VOLUME_HEADER_ROW`` offset. An ordinary
    export change (one metadata row inserted or removed) shifts that offset;
    trusting it silently read the 'Detector' group-label row as the header in
    exactly that case, fabricating a single detector literally named
    'Detector' while dropping every real detector's column with no issue at
    all (reproduced with a synthetic fixture — see
    ``tests/test_dataimport_scats_wa_xlsx.py``). A detector column whose
    header is not a plausible SCATS detector id (bare digits) is the same
    failure mode one row over, so it refuses the same way rather than being
    silently accepted as a detector id.

    A blank detector cell produces NO record — never a zero. Measured
    2026-08-10 on the real drop: LM00973 (9 detectors, the smallest of the
    three real sites) has genuinely blank cells in both volume tabs; the other
    two real sites have none. Both are handled the same way here: counted and
    reported (``scats_xlsx_volume_blank_cells``), never defaulted.
    """
    header_row_index = _find_volume_header_row(rows)
    if header_row_index is None:
        searched = min(len(rows), _VOLUME_HEADER_SEARCH_WINDOW)
        result.issues.append(
            ImportIssue(
                severity=IssueSeverity.REFUSE,
                code="scats_xlsx_volume_header_not_found",
                message=(
                    f"{path.name}: {sheet_name!r} has no row, in the first {searched} row(s), "
                    "whose first 3 columns read 'Day, Date, Hour' or 'Day, Date, Qtr Hour' — the "
                    "header this layout requires. Rather than trust the nominal position "
                    f"(row {_VOLUME_HEADER_ROW + 1}), which an inserted or removed metadata row "
                    "can silently shift, no volumes were read from this tab."
                ),
                subject=f"{path.name} / {sheet_name}",
                citation=CIT_A1_XLSX,
                context={"rows_present": len(rows), "rows_searched": searched},
            )
        )
        return None

    data_start_row = header_row_index + 1
    header = rows[header_row_index]
    detector_cols: list[tuple[int, str]] = []
    implausible: list[tuple[int, str]] = []
    for col_index in range(_VOLUME_DETECTOR_COL_START, len(header)):
        raw = header[col_index]
        if raw is None:
            continue
        token = str(raw).strip()
        if not token:
            continue
        if _PLAUSIBLE_DETECTOR_ID_RE.match(token):
            detector_cols.append((col_index, token))
        else:
            implausible.append((col_index, token))

    if implausible:
        result.issues.append(
            ImportIssue(
                severity=IssueSeverity.REFUSE,
                code="scats_xlsx_volume_implausible_detector_id",
                message=(
                    f"{path.name}: {sheet_name!r} row {header_row_index + 1} names "
                    f"{len(implausible)} detector column(s) whose header cell is not a plausible "
                    "SCATS detector id (bare digits): "
                    f"{', '.join(repr(token) for _, token in implausible[:10])}. A cell that does "
                    "not look like a detector number is not accepted as one — this is the same "
                    "shape a misidentified header row produces, so the whole tab is refused rather "
                    "than partly trusted."
                ),
                subject=f"{path.name} / {sheet_name}",
                citation=CIT_A1_XLSX,
                context={
                    "header_row": header_row_index + 1,
                    "implausible_columns": [
                        {"column": col_index, "value": token}
                        for col_index, token in implausible[:50]
                    ],
                },
            )
        )
        return None

    if not detector_cols:
        result.issues.append(
            ImportIssue(
                severity=IssueSeverity.WARN,
                code="scats_xlsx_no_detector_columns",
                message=(
                    f"{path.name}: {sheet_name!r} row {header_row_index + 1} names no detector "
                    "columns; no volumes read from this tab."
                ),
                subject=f"{path.name} / {sheet_name}",
                citation=CIT_A1_XLSX,
            )
        )
        return None

    values: dict[str, dict[datetime, float]] = {det_id: {} for _, det_id in detector_cols}
    records: list[DetectorCountData] = []
    blank_cells = 0
    unusable_rows = 0
    rows_read = 0

    for row_index in range(data_start_row, len(rows)):
        row = rows[row_index]
        date_value = row[1] if len(row) > 1 else None
        clock_value = row[2] if len(row) > 2 else None
        start = _combine_date_and_clock(date_value, clock_value)
        if start is None:
            unusable_rows += 1
            continue
        rows_read += 1
        end = start + interval

        for col_index, det_id in detector_cols:
            raw = row[col_index] if col_index < len(row) else None
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                blank_cells += 1
                continue
            try:
                volume = float(raw)
            except (TypeError, ValueError):
                blank_cells += 1
                continue
            values[det_id][start] = volume
            record = DetectorCountData(
                provenance=Provenance(
                    source_file=path.name,
                    locator=f"{sheet_name}!row {row_index + 1}",
                    native_id=det_id,
                ),
                site_id=site_id,
                detector_id=det_id,
                controller_id=controller_id,
                interval_start=start,
                interval_end=end,
                volume=volume,
                volume_unit=VolumeUnit.VEHICLES,
            )
            record.mark_absent(
                "vehicle_class",
                "SCATS loop volumes are undifferentiated; no class breakdown exists in this "
                "source.",
            )
            records.append(record)

    if unusable_rows:
        result.issues.append(
            ImportIssue(
                severity=IssueSeverity.WARN,
                code="scats_xlsx_volume_row_unusable",
                message=(
                    f"{path.name}: {sheet_name!r} has {unusable_rows} row(s) whose Date/Hour "
                    "column did not parse as expected; skipped rather than guessed."
                ),
                subject=f"{path.name} / {sheet_name}",
                citation=CIT_A1_XLSX,
                context={"rows_skipped": unusable_rows},
            )
        )
    if blank_cells:
        result.issues.append(
            ImportIssue(
                severity=IssueSeverity.INFO,
                code="scats_xlsx_volume_blank_cells",
                message=(
                    f"{path.name}: {sheet_name!r} has {blank_cells} blank/unreadable detector "
                    f"cell(s) across {len(detector_cols)} detector column(s) and {rows_read} "
                    "interval row(s). No record was written for those (site, detector, interval) "
                    "combinations — a blank cell is an unobserved interval, not zero flow."
                ),
                subject=f"{path.name} / {sheet_name}",
                citation=CIT_A1_XLSX,
                context={"blank_cells": blank_cells, "detector_columns": len(detector_cols)},
            )
        )

    return _VolumeSheetResult(
        sheet=sheet_name,
        detector_ids=[det_id for _, det_id in detector_cols],
        values=values,
        records=records,
        blank_cells=blank_cells,
        rows_read=rows_read,
        header_row=header_row_index,
    )


class DetectorMovement(BaseModel):
    """One (site, detector) -> movement, supplied ENTIRELY by the caller.

    See :func:`build_turning_counts_from_detector_map`'s docstring for why
    this adapter never derives one itself.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    approach: ApproachRef
    movement: MovementRef


def build_turning_counts_from_detector_map(
    detector_counts: Sequence[DetectorCountData],
    *,
    detector_movements: Mapping[tuple[str, str], DetectorMovement],
    mapping_source: str,
) -> tuple[list[TurningCountData], list[ImportIssue]]:
    """Lift detector-level counts to movement-level counts — ONLY with a
    caller-supplied map. This adapter never builds one itself.

    A SCATS detector number is local to its site (SCATS detector "7" at site
    LM00972 and detector "7" at LM00973 are unrelated loops); turning "site
    LM00972 detector 7" into "link 1234, movement N→E" needs the LMA/LMB site
    drawing MRWA Appendix-A documents (``SCATS_FIELD_MAP``'s "LMA drawing"
    entries, ``parsed_by_stub=False``). MEASURED 2026-08-10: this drop's
    ``client-data/`` does not contain one. Checked and ruled out —

    * ``Shpfile_of_Routes_for_TravelTime_Analysis.zip`` — travel-time ROUTE
      geometry (7-30/8-30 and 15-30/16-30 AM/PM runs), no detector or SCATS
      reference of any kind.
    * ``Calc_MurdochDr_BaseTurningMov_JH_edit_20260526.xlsx`` — a manual
      turning-movement survey (26 site sheets). Every cell was searched for
      "detector"/"SCATS"/"TCS"/"LM00…" — zero matches. It counts the same
      intersections by a different, uncorrelated method; it does not name
      which SCATS detector observed which turn.
    * ``murdoch.osm`` — zero occurrences of "detector" anywhere in the file.

    So the mapping is exactly what the task doctrine calls for: caller-supplied
    input with an explicit refusal when absent — the same pattern
    ``genesis.signals.generate_node_signals`` uses for ``approach_phases``.
    ``detector_movements`` has no default; pass ``{}`` to make the refusal
    total and explicit rather than accidental. Every (site, detector) pair NOT
    covered is named in a REFUSE issue rather than silently dropped or
    guessed from position/ordering.

    ``mapping_source`` must say where the map came from (a person, a drawing,
    an email) — it becomes every lifted record's ``Provenance.derived_by``, so
    nothing downstream can mistake a human declaration for something this
    adapter read out of the workbook.

    Returns ``(lifted_counts, issues)``. ``lifted_counts`` holds only the
    records that WERE mapped; the unmapped ones are named in ``issues``, never
    silently omitted.
    """
    if not mapping_source or not mapping_source.strip():
        raise AdapterError(
            "mapping_source must name where detector_movements came from (a person, a drawing, "
            "an email) — a caller-supplied mapping with no stated authority is exactly the "
            "invented provenance this project forbids"
        )

    counts: list[TurningCountData] = []
    issues: list[ImportIssue] = []
    unmapped: dict[tuple[str, str], int] = defaultdict(int)

    for record in detector_counts:
        key = (record.site_id, record.detector_id)
        mapped = detector_movements.get(key)
        if mapped is None:
            unmapped[key] += 1
            continue
        counts.append(
            TurningCountData(
                provenance=Provenance(
                    source_file=record.provenance.source_file,
                    native_id=record.provenance.native_id,
                    derived_by=(
                        f"detector→movement lift using a caller-supplied map ({mapping_source}); "
                        "no LMA/LMB drawing exists in this drop for the adapter to derive one from"
                    ),
                ),
                site_id=record.site_id,
                approach=mapped.approach,
                movement=mapped.movement,
                vehicle_class=VehicleClassRef(
                    scheme=ClassificationScheme.UNCLASSIFIED,
                    code="all",
                    canonical=CanonicalVehicleClass.ALL_VEHICLES,
                ),
                interval_start=record.interval_start,
                interval_end=record.interval_end,
                volume=record.volume,
                volume_unit=record.volume_unit,
            )
        )

    if unmapped:
        total_records = sum(unmapped.values())
        issues.append(
            ImportIssue(
                severity=IssueSeverity.REFUSE,
                code="scats_detector_movement_mapping_missing",
                message=(
                    f"{len(unmapped)} (site, detector) pair(s), {total_records} record(s), have no "
                    "entry in the supplied detector_movements map. Only the LMA/LMB site drawing "
                    "(or an equivalent human declaration) can say which movement a SCATS detector "
                    "counts — none was supplied for these, so they are excluded from the lifted "
                    "turning counts rather than guessed from detector number or ordering."
                ),
                citation=CIT_A1,
                context={
                    "unmapped_pairs": [
                        {"site_id": site_id, "detector_id": detector_id, "records": count}
                        for (site_id, detector_id), count in sorted(unmapped.items())
                    ][:50],
                    "unmapped_pair_count": len(unmapped),
                    "unmapped_record_count": total_records,
                },
            )
        )
    return counts, issues


class SCATSWAAdapter(DataAdapter):
    """Adapter #2 (WA / SCATS), stub tier. See the module docstring."""

    info = AdapterInfo(
        name="scats_wa",
        region="AU-WA",
        source_system="SCATS",
        version="0.2.0",
        description=(
            "Maps MRWA Appendix-A structures onto the neutral model. Reads real .xlsx SCATS "
            "detector-volume workbooks (Site + Hourly/Quarter Hourly Volume tabs) plus a "
            "hand-authored CSV subset for the signal-timing structures; the xlsx forms of "
            "Timings/Phase History/Link & Offset Plans and the LMA/LMB drawings remain a "
            "named gap (see the module and _parse_xlsx_workbook docstrings)."
        ),
    )

    # -- sniff -------------------------------------------------------------

    def sniff(self, files: Sequence[Path]) -> SniffResult:
        xlsx_result = self._sniff_xlsx(files)
        csv_result = self._sniff_csv(files)
        if xlsx_result is None or xlsx_result.confidence == 0.0:
            return csv_result
        if csv_result.confidence == 0.0:
            return xlsx_result
        # Both scored something (a mixed file set) — report the stronger one,
        # naming both so a caller is not left guessing which files won it.
        winner = xlsx_result if xlsx_result.confidence >= csv_result.confidence else csv_result
        return SniffResult(
            confidence=winner.confidence,
            reason=f"{winner.reason} (xlsx: {xlsx_result.reason}; csv: {csv_result.reason})",
            matched_files=sorted(set(xlsx_result.matched_files) | set(csv_result.matched_files)),
        )

    def _sniff_csv(self, files: Sequence[Path]) -> SniffResult:
        try:
            roles = _classify(files)
        except Exception as exc:  # a sniff never raises
            return SniffResult(confidence=0.0, reason=f"classification failed: {exc}")
        if not roles:
            return SniffResult(
                confidence=0.0,
                reason="no file name matches a documented MRWA Appendix-A structure",
            )

        confidence = min(1.0, 0.25 * len(roles))
        corroborated = False
        event_file = roles.get("event_history")
        if event_file is not None and event_file.exists():
            try:
                text = event_file.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
            except OSError:
                text = ""
            corroborated = bool(_SG_EVENT.search(text) or "Walk" in text)
        site_file = roles.get("site")
        if not corroborated and site_file is not None and site_file.exists():
            try:
                header = site_file.read_text(encoding="utf-8-sig", errors="ignore")[:512]
            except OSError:
                header = ""
            corroborated = "tcs" in header.lower()
        if corroborated:
            confidence = min(1.0, confidence + 0.25)

        return SniffResult(
            confidence=confidence,
            reason=(
                f"matched {len(roles)} Appendix-A structure(s): {', '.join(sorted(roles))}"
                + ("; content corroborated" if corroborated else "; names only, content unchecked")
            ),
            matched_files=[path.name for path in roles.values()],
        )

    def _sniff_xlsx(self, files: Sequence[Path]) -> SniffResult | None:
        """Cheap, read-only: open each ``.xlsx``/``.xlsm`` just far enough to
        read its sheet NAMES (no cell values), score by which of the real
        workbook's tabs are present. Returns ``None`` when no xlsx/xlsm file is
        in the set at all, so :meth:`sniff` can fall through to the CSV score
        without a spurious "0.0, no xlsx" reason cluttering the winner."""
        candidates = [path for path in files if path.suffix.lower() in XLSX_SUFFIXES]
        if not candidates:
            return None

        matched: list[str] = []
        partial: list[str] = []
        for path in candidates:
            try:
                workbook = _open_scats_workbook(path)
            except AdapterError:
                continue
            try:
                sheets = set(workbook.sheetnames)
            finally:
                workbook.close()
            has_volume = bool(sheets & {HOURLY_VOLUME_SHEET, QUARTER_HOURLY_VOLUME_SHEET})
            if SITE_SHEET in sheets and has_volume:
                matched.append(path.name)
            elif SITE_SHEET in sheets or has_volume:
                partial.append(path.name)

        if matched:
            return SniffResult(
                confidence=1.0,
                reason=(
                    f"{len(matched)} workbook(s) carry a {SITE_SHEET!r} tab plus "
                    f"{HOURLY_VOLUME_SHEET!r}/{QUARTER_HOURLY_VOLUME_SHEET!r}"
                ),
                matched_files=matched,
            )
        if partial:
            return SniffResult(
                confidence=0.4,
                reason=(
                    f"{len(partial)} workbook(s) carry a {SITE_SHEET!r} tab OR a volume tab, "
                    "not both — below the selection threshold on purpose"
                ),
                matched_files=partial,
            )
        return SniffResult(
            confidence=0.0,
            reason=f"no .xlsx/.xlsm workbook carries a {SITE_SHEET!r} tab or a volume tab",
        )

    # -- parse -------------------------------------------------------------

    def parse(self, files: Sequence[Path]) -> ImportResult:
        """Dispatches by suffix: real ``.xlsx``/``.xlsm`` workbooks go through
        :meth:`_parse_xlsx_workbook` (one call per workbook, independent —
        matches ``services.observations.ingest_observation_set``'s "one
        workbook at a time" streaming contract); everything else goes through
        the original CSV-subset role classifier. A mixed file set runs both;
        that combination has never been observed in practice (the real drop is
        xlsx-only, the fixture drop is csv-only) but nothing here forbids it.
        """
        result = self.empty_result()
        result.source_files = [path.name for path in files]

        xlsx_paths = [path for path in files if path.suffix.lower() in XLSX_SUFFIXES]
        csv_paths = [path for path in files if path not in xlsx_paths]

        for path in sorted(xlsx_paths, key=lambda item: item.name):
            self._parse_xlsx_workbook(path, result)

        if csv_paths:
            self._parse_csv_bundle(csv_paths, result)

        return result

    # -- xlsx: the real workbook --------------------------------------------

    def _parse_xlsx_workbook(self, path: Path, result: ImportResult) -> None:
        """One real ``.xlsx`` SCATS detector-volume workbook -> neutral records.

        Reads ``Site`` (site identity + the fields the CSV subset never had:
        road, road name, SLK, lat/lon) and ``Hourly Volume``/``Quarter Hourly
        Volume`` (per-detector, per-interval counts). Every other real tab this
        unit has seen (``XLSX_UNPARSED_SHEETS_OF_INTEREST``) is named in an
        INFO issue and left unread — it is a genuinely different xlsx layout
        (key/value Site-style rows for some, phase-lettered columns for
        Timings, wall-clock rows for Phase History) from the CSV-subset
        parsers above, and misreading a different layout with the wrong parser
        would be a wrong number, not a smaller one.

        Corrupt/unreadable bytes raise :class:`AdapterError` (an input
        problem); a missing ``Site`` tab or TCS number is a REFUSE issue
        (an expected data problem) and this workbook contributes nothing
        further — but does not stop any OTHER workbook in the same ``parse()``
        call.
        """
        workbook = _open_scats_workbook(path)
        try:
            parsed_site = self._parse_xlsx_site(path, workbook, result)
            if parsed_site is None:
                return
            site, controller = parsed_site
            result.sites.append(site)
            result.controllers.append(controller)

            self._parse_xlsx_detector_volumes(path, workbook, site, controller, result)

            present_unparsed = [
                name for name in XLSX_UNPARSED_SHEETS_OF_INTEREST if name in workbook.sheetnames
            ]
            if present_unparsed:
                result.issues.append(
                    ImportIssue(
                        severity=IssueSeverity.INFO,
                        code="scats_xlsx_sheet_present_not_parsed",
                        message=(
                            f"{path.name}: also carries {', '.join(present_unparsed)} — real xlsx "
                            "tabs in a layout different from the CSV-subset parsers above (see "
                            "_parse_xlsx_workbook's docstring). Left unread rather than misparsed."
                        ),
                        subject=path.name,
                        context={"sheets_not_parsed": present_unparsed},
                    )
                )

            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="scats_no_turning_counts_in_source",
                    message=(
                        "MRWA Appendix-A supplies detector volumes, not turning counts. "
                        "Movement-level demand must come from a separate turning-count survey; "
                        "none was expected here."
                    ),
                    subject=path.name,
                    citation=CIT_A1_XLSX,
                )
            )
        finally:
            workbook.close()

    def _parse_xlsx_site(
        self, path: Path, workbook: Any, result: ImportResult
    ) -> tuple[SiteMetadata, SignalControllerData] | None:
        """Returns ``None`` (having recorded a REFUSE issue) when the site
        cannot be identified. Never raises for a data problem."""
        rows = _sheet_rows(workbook, SITE_SHEET)
        if rows is None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="scats_site_identity_unavailable",
                    message=(
                        f"{path.name} carries no {SITE_SHEET!r} tab, so no TCS number is available "
                        "and no record can be attributed to a site. The adapter will not derive a "
                        "site id from the file name."
                    ),
                    subject=path.name,
                    citation=CIT_A1_XLSX,
                    context={"sheets_present": list(workbook.sheetnames)},
                )
            )
            return None

        kv = _site_sheet_kv(rows)
        tcs = _kv_str(kv, "Traffic Signal")
        if not tcs:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="scats_tcs_number_missing",
                    message=(
                        f"{path.name}: the {SITE_SHEET!r} tab states no 'Traffic Signal' value, so "
                        "no record can be attributed to a site. The adapter will not substitute "
                        "the file name."
                    ),
                    provenance=Provenance(
                        source_file=path.name, locator=f"{SITE_SHEET}!row 2"
                    ),
                    citation=CIT_A1_XLSX,
                )
            )
            return None

        provenance = Provenance(
            source_file=path.name, locator=f"{SITE_SHEET} tab", native_id=tcs
        )

        lat = _kv_float(kv, "Latitude")
        lon = _kv_float(kv, "Longitude")
        position: PositionHint | None = None
        if lat is not None or lon is not None:
            position = PositionHint(x=lon, y=lat, description=_kv_str(kv, "Intersection"))

        road_ref = _kv_str(kv, "Road")
        road_name = _kv_str(kv, "Road Name")
        stationing_km = _kv_float(kv, "SLK")
        site_name = _kv_str(kv, "Intersection") or road_name

        site = SiteMetadata(
            provenance=provenance,
            site_id=tcs,
            site_name=site_name,
            source_system="SCATS",
            jurisdiction="WA, Australia",
            # Regional constant declared BY THE ADAPTER (WA drives on the
            # left), not a value read from the file — see the CSV _parse_site
            # for the same note.
            drive_side=DriveSide.LEFT,
            road_ref=road_ref,
            road_name=road_name,
            stationing_km=stationing_km,
            position=position,
        )
        site.mark_absent(
            "coordinate_reference",
            f"{path.name}'s {SITE_SHEET!r} tab states Latitude/Longitude as plain decimal "
            "degrees but names no coordinate reference system; WGS84 is the common convention "
            "but is not asserted here without the workbook stating it.",
        )
        if position is None:
            site.mark_absent(
                "position", f"{path.name}'s {SITE_SHEET!r} tab states no Latitude/Longitude."
            )
        if not road_ref:
            site.mark_absent(
                "road_ref", f"{path.name}'s {SITE_SHEET!r} tab states no 'Road' value."
            )
        if not road_name:
            site.mark_absent(
                "road_name", f"{path.name}'s {SITE_SHEET!r} tab states no 'Road Name' value."
            )
        if stationing_km is None:
            site.mark_absent(
                "stationing_km", f"{path.name}'s {SITE_SHEET!r} tab states no 'SLK' value."
            )
        site.mark_absent(
            "collection_period",
            f"{path.name}'s {SITE_SHEET!r} tab states no survey period directly; the "
            "observation window is on the Hourly/Quarter Hourly Volume tabs' own Date column.",
        )

        controller = SignalControllerData(
            provenance=provenance,
            controller_id=tcs,
            name=site_name,
            site_id=tcs,
            control_type=ControlType.ADAPTIVE_SCATS,
            control_type_raw="SCATS",
        )
        controller.mark_absent(
            "region", f"{path.name}'s {SITE_SHEET!r} tab states no Region value."
        )
        controller.mark_absent(
            "cycle_time_s",
            "A SCATS site has no single nominal cycle; this unit reads Site + Hourly/Quarter "
            "Hourly Volume only (see the sheets-not-parsed INFO issue for what else this "
            "workbook carries).",
        )
        return site, controller

    def _parse_xlsx_detector_volumes(
        self,
        path: Path,
        workbook: Any,
        site: SiteMetadata,
        controller: SignalControllerData,
        result: ImportResult,
    ) -> None:
        """Quarter Hourly Volume (15-minute, MRWA's stated profiling interval)
        is the granularity EMITTED as :class:`DetectorCountData`. Hourly
        Volume is cross-checked against it — each stated hourly total must
        equal the sum of its four quarter-hour bins — and then discarded
        rather than also emitted: the same "the finer series is the record,
        the coarser one is a cross-check" rule ``adapters/daejeon.py`` applies
        to its 소계 rows, for the identical reason (emitting both would put
        two different interval lengths for the same detector into
        ``result.detector_counts``, and ``quality.check_detector_count_intervals``
        would — correctly — REFUSE it as an overlap: the same vehicles counted
        twice). When only ``Hourly Volume`` exists, ITS rows are emitted
        instead — never left unread just because the finer tab is missing.
        """
        quarter_rows = _sheet_rows(workbook, QUARTER_HOURLY_VOLUME_SHEET)
        hourly_rows = _sheet_rows(workbook, HOURLY_VOLUME_SHEET)

        quarter = (
            _parse_volume_sheet(
                path, quarter_rows, QUARTER_HOURLY_VOLUME_SHEET, timedelta(minutes=15),
                site.site_id, controller.controller_id, result,
            )
            if quarter_rows is not None
            else None
        )
        hourly = (
            _parse_volume_sheet(
                path, hourly_rows, HOURLY_VOLUME_SHEET, timedelta(hours=1),
                site.site_id, controller.controller_id, result,
            )
            if hourly_rows is not None
            else None
        )

        if quarter is None and hourly is None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="scats_xlsx_no_volume_tab",
                    message=(
                        f"{path.name}: neither {HOURLY_VOLUME_SHEET!r} nor "
                        f"{QUARTER_HOURLY_VOLUME_SHEET!r} is present (or usable), so no detector "
                        "volumes were read from this workbook."
                    ),
                    subject=path.name,
                    citation=CIT_A1_XLSX,
                    context={"sheets_present": list(workbook.sheetnames)},
                )
            )
            return

        emitted = quarter if quarter is not None else hourly
        assert emitted is not None  # narrows for the type checker

        seen_detectors: set[str] = set()
        for detector_id in emitted.detector_ids:
            if detector_id in seen_detectors:
                continue
            seen_detectors.add(detector_id)
            detector = DetectorData(
                provenance=Provenance(
                    source_file=path.name,
                    locator=f"{emitted.sheet}!row {emitted.header_row + 1}",
                    native_id=detector_id,
                ),
                detector_id=detector_id,
                controller_id=controller.controller_id,
            )
            detector.mark_absent(
                "function",
                "The Hourly/Quarter Hourly Volume tabs state volumes only; detector function "
                "and placement are on the LMA drawing, which this drop does not include.",
            )
            detector.mark_absent(
                "position", "See function — the LMA drawing is not in this drop."
            )
            controller.detectors.append(detector)

        result.detector_counts.extend(emitted.records)

        if quarter is not None and hourly is not None:
            self._cross_check_hourly_against_quarter(path, hourly, quarter, result)
        elif hourly is not None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="scats_xlsx_hourly_volume_used_no_quarter_hourly",
                    message=(
                        f"{path.name}: only {HOURLY_VOLUME_SHEET!r} is present/usable, so its "
                        "1-hour intervals were emitted directly; no finer-resolution cross-check "
                        "was possible."
                    ),
                    subject=path.name,
                    citation=CIT_A1_XLSX,
                )
            )
        elif quarter is not None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="scats_xlsx_hourly_volume_tab_absent",
                    message=(
                        f"{path.name}: {HOURLY_VOLUME_SHEET!r} is absent/unusable, so the "
                        f"{QUARTER_HOURLY_VOLUME_SHEET!r} 15-minute intervals could not be "
                        "cross-checked against an independent hourly aggregate."
                    ),
                    subject=path.name,
                    citation=CIT_A1_XLSX,
                )
            )

    def _cross_check_hourly_against_quarter(
        self,
        path: Path,
        hourly: _VolumeSheetResult,
        quarter: _VolumeSheetResult,
        result: ImportResult,
    ) -> None:
        """Judge every Hourly Volume cell against the 4 Quarter Hourly Volume
        bins it should equal, THEN discard the hourly figures — this is the
        point-of-discard check: since Hourly Volume never becomes a record,
        the quality gate can never see it, so a disagreement has to be caught
        here or not at all. Measured 2026-08-10: 0 mismatches across the 3
        real workbooks (7,532 hours cross-checked) — this path was exercised
        against real data before being written, not left theoretical.
        """
        mismatches: list[str] = []
        checked = 0
        for detector_id, hour_values in hourly.values.items():
            quarter_values = quarter.values.get(detector_id, {})
            for hour_start, stated in hour_values.items():
                bins = [
                    quarter_values.get(hour_start + timedelta(minutes=15 * i))
                    for i in range(4)
                ]
                if any(value is None for value in bins):
                    # A blank quarter-hour cell already produced its own INFO
                    # issue; cross-checking a partial hour would be comparing
                    # a stated total against a sum we know is incomplete.
                    continue
                checked += 1
                summed = sum(bins)  # type: ignore[arg-type]
                if abs(stated - summed) > 1e-6:
                    mismatches.append(
                        f"detector {detector_id} {hour_start.isoformat()}: Hourly Volume "
                        f"{stated:g} vs Quarter Hourly Volume sum {summed:g}"
                    )
        if mismatches:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="scats_xlsx_hourly_disagrees_with_quarter_hourly",
                    message=(
                        f"{path.name}: {len(mismatches)} hour(s) where Hourly Volume disagrees "
                        "with the sum of its four Quarter Hourly Volume bins. The finer tab is "
                        "what was emitted; the disagreement means at least one of the two tabs "
                        "is wrong and nothing downstream could tell which."
                    ),
                    subject=path.name,
                    citation=CIT_A1_XLSX,
                    context={
                        "examples": mismatches[:10],
                        "total_mismatches": len(mismatches),
                        "hours_checked": checked,
                    },
                )
            )
        elif checked:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="scats_xlsx_hourly_corroborates_quarter_hourly",
                    message=(
                        f"{path.name}: {checked} hour(s) cross-checked — Hourly Volume equals "
                        "the sum of its four Quarter Hourly Volume bins in every one."
                    ),
                    subject=path.name,
                )
            )

    # -- csv subset ----------------------------------------------------------

    def _parse_csv_bundle(self, files: Sequence[Path], result: ImportResult) -> None:
        """The original CSV-subset path (roadmap 0.7). Unchanged behaviour;
        moved into its own method so :meth:`parse` could add the xlsx branch
        without interleaving two unrelated file formats in one function body.
        """
        roles = _classify(files)

        site_file = roles.get("site")
        if site_file is None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="scats_site_identity_unavailable",
                    message=(
                        "No Site tab was supplied, so no TCS number is available and no record "
                        "can be attributed to a site. The adapter will not derive a site id from "
                        "a file name."
                    ),
                    citation=CIT_A1,
                    context={"supplied_roles": sorted(roles)},
                )
            )
            return

        parsed_site = self._parse_site(site_file, result)
        if parsed_site is None:
            # The refusal is already on the result. Returning (rather than
            # raising) is the contract: expected data problems are reported.
            return
        site, controller = parsed_site
        result.sites.append(site)
        result.controllers.append(controller)

        if "timings" in roles:
            self._parse_timings(roles["timings"], controller, result)
        if "link_offset" in roles:
            self._parse_link_offset(roles["link_offset"], controller, result)

        cycles: int | None = None
        if "phase_history" in roles:
            cycles = self._parse_phase_history(
                roles["phase_history"], controller, result, timings_present="timings" in roles
            )
        if "event_history" in roles:
            self._parse_event_history(roles["event_history"], controller, result, cycles)
        if "detector_volumes" in roles:
            self._parse_detector_volumes(roles["detector_volumes"], site.site_id, controller, result)

        result.issues.append(
            ImportIssue(
                severity=IssueSeverity.INFO,
                code="scats_no_turning_counts_in_source",
                message=(
                    "MRWA Appendix-A supplies detector volumes, not turning counts. Movement-level "
                    "demand must come from a separate turning-count survey; none was expected here."
                ),
                citation=CIT_A1,
            )
        )

    # -- per-structure parsers --------------------------------------------

    def _parse_site(
        self, path: Path, result: ImportResult
    ) -> tuple[SiteMetadata, SignalControllerData] | None:
        """Returns ``None`` (having recorded a REFUSE issue) when the site
        cannot be identified. Never raises for a data problem."""
        rows = _read_csv(path)
        row = rows[0] if rows else {}
        tcs = _opt(row, "TCS", "TCS number", "TCSNo", "Site")
        if not tcs:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="scats_tcs_number_missing",
                    message=(
                        f"{path.name} carries no TCS number, so no record can be attributed to a "
                        "site. The adapter will not substitute a file name or a row index."
                    ),
                    provenance=Provenance(source_file=path.name, locator="row 1"),
                    citation=CIT_A1,
                )
            )
            return None

        provenance = Provenance(source_file=path.name, locator="row 1", native_id=tcs)
        site = SiteMetadata(
            provenance=provenance,
            site_id=tcs,
            site_name=_opt(row, "Name", "Site name"),
            source_system="SCATS",
            jurisdiction="WA, Australia",
            region=_opt(row, "Region"),
            # Regional constant declared BY THE ADAPTER (WA drives on the left),
            # not a value guessed from the file. The distinction matters: a
            # documented, citable regional fact is knowledge; a per-record value
            # the source did not carry is an invention.
            drive_side=DriveSide.LEFT,
        )
        site.mark_absent(
            "coordinate_reference",
            "Appendix-A source data states no coordinate reference system.",
        )
        site.mark_absent(
            "collection_period",
            "Site tab states no survey period; the observation window comes from Phase History.",
        )

        controller = SignalControllerData(
            provenance=provenance,
            controller_id=tcs,
            name=_opt(row, "Name", "Site name"),
            site_id=tcs,
            region=_opt(row, "Region"),
            control_type=ControlType.ADAPTIVE_SCATS,
            control_type_raw="SCATS",
        )
        controller.mark_absent(
            "cycle_time_s",
            "A SCATS site has no single nominal cycle; bounds come from the Link & Offset "
            "Plans tab and the observed average from Phase History.",
        )
        return site, controller

    def _parse_timings(
        self, path: Path, controller: SignalControllerData, result: ImportResult
    ) -> None:
        rows = _read_csv(path)
        groups: dict[str, dict[str, Any]] = {}
        phase_values: dict[str, dict[str, set[float]]] = defaultdict(
            lambda: defaultdict(set)
        )
        phase_rows: dict[str, int] = {}
        ped_rows: list[tuple[int, dict[str, str]]] = []

        for index, row in enumerate(rows, start=2):  # row 1 is the header
            sg = _opt(row, "SG", "SignalGroup", "Signal group")
            phase = _opt(row, "Phase")
            if sg:
                entry = groups.setdefault(
                    sg,
                    {
                        "row": index,
                        "late_start_s": _opt_float(row, "LateStart", "Late start"),
                        "early_cut_off_s": _opt_float(row, "EarlyCutOff", "Early cut-off"),
                        "min_green_s": _opt_float(row, "MinGreen", "Minimum green"),
                        "phase_ids": [],
                    },
                )
                if phase and phase not in entry["phase_ids"]:
                    entry["phase_ids"].append(phase)
            if phase:
                phase_rows.setdefault(phase, index)
                for field, keys in (
                    ("amber_s", ("Yellow", "Amber")),
                    ("all_red_s", ("AllRed", "All-red", "All red")),
                    ("min_green_s", ("MinGreen", "Minimum green")),
                    ("max_green_s", ("MaxGreen", "Maximum green")),
                ):
                    value = _opt_float(row, *keys)
                    if value is not None:
                        phase_values[phase][field].add(value)
            if _opt(row, "Walk", "WalkId", "Ped"):
                ped_rows.append((index, row))

        for sg, entry in groups.items():
            controller.signal_groups.append(
                SignalGroupData(
                    provenance=Provenance(
                        source_file=path.name, locator=f"row {entry['row']}", native_id=sg
                    ),
                    signal_group_id=sg,
                    kind=SignalGroupKind.VEHICLE,
                    phase_ids=entry["phase_ids"] or None,
                    late_start_s=entry["late_start_s"],
                    early_cut_off_s=entry["early_cut_off_s"],
                    min_green_s=entry["min_green_s"],
                )
            )

        if phase_values:
            phases: list[PhaseData] = []
            for phase_id, fields in phase_values.items():
                kwargs: dict[str, float | None] = {}
                conflicts: list[str] = []
                for field, values in fields.items():
                    if len(values) == 1:
                        kwargs[field] = next(iter(values))
                    else:
                        kwargs[field] = None
                        conflicts.append(f"{field}={sorted(values)}")
                try:
                    phase = PhaseData(
                        provenance=Provenance(
                            source_file=path.name,
                            locator=f"row {phase_rows[phase_id]}",
                            native_id=phase_id,
                        ),
                        phase_id=phase_id,
                        **kwargs,
                    )
                except ValidationError as exc:
                    # A schema invariant the SOURCE violated (e.g. min green
                    # above max green) is a data problem, so it is reported,
                    # not raised past the caller.
                    result.issues.append(
                        ImportIssue(
                            severity=IssueSeverity.REFUSE,
                            code="timings_phase_invalid",
                            message=(
                                f"Phase {phase_id} in {path.name} violates a neutral-model "
                                f"invariant and was dropped: {exc.errors()[0]['msg']}"
                            ),
                            subject=f"controller {controller.controller_id} / phase {phase_id}",
                            provenance=Provenance(
                                source_file=path.name, locator=f"row {phase_rows[phase_id]}"
                            ),
                            citation=CIT_A1,
                        )
                    )
                    continue
                phase.mark_absent(
                    "green_s",
                    "The Timings tab states limits and clearances only; green time on a SCATS "
                    "site is adaptive and comes from Phase History.",
                )
                for conflict in conflicts:
                    field_name = conflict.split("=")[0]
                    phase.mark_absent(
                        field_name,
                        f"The Timings tab gives conflicting values ({conflict}) for phase "
                        f"{phase_id}; the adapter will not choose between them.",
                    )
                    result.issues.append(
                        ImportIssue(
                            severity=IssueSeverity.WARN,
                            code="timings_value_conflict",
                            message=(
                                f"Phase {phase_id}: the Timings tab states more than one value "
                                f"for {conflict}. Left unset rather than picking one."
                            ),
                            subject=f"controller {controller.controller_id} / phase {phase_id}",
                            citation=CIT_A1,
                        )
                    )
                phases.append(phase)

            program = SignalProgramData(
                provenance=Provenance(
                    source_file=path.name, locator="Timings tab", native_id="declared_timings"
                ),
                program_id="declared_timings",
                name="Signal Data spreadsheet — Timings tab",
                source_kind=ProgramSourceKind.DECLARED,
                phases=phases,
            )
            program.mark_absent(
                "cycle_time_s",
                "The Timings tab states no cycle; SCATS cycle length varies by plan and demand.",
            )
            controller.programs.append(program)

        for index, row in ped_rows:
            self._append_pedestrian_phase(path, index, row, controller, result)

    def _append_pedestrian_phase(
        self,
        path: Path,
        index: int,
        row: dict[str, str],
        controller: SignalControllerData,
        result: ImportResult,
    ) -> None:
        walk_id = _opt(row, "Walk", "WalkId", "Ped") or ""
        raw_protection = _opt(row, "Protection", "PedProtection")
        protection = (
            PROTECTION_TYPE_MAP.get(raw_protection.strip().lower()) if raw_protection else None
        )
        if raw_protection and protection is None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="pedestrian_protection_type_unmapped",
                    message=(
                        f"Walk {walk_id}: protection arrangement {raw_protection!r} matches none "
                        "of Appendix-A A.3's six types; kept verbatim, enum left unset."
                    ),
                    subject=f"controller {controller.controller_id} / walk {walk_id}",
                    citation=CIT_A3,
                )
            )
        ped = PedestrianPhaseData(
            provenance=Provenance(
                source_file=path.name, locator=f"row {index}", native_id=walk_id
            ),
            pedestrian_phase_id=walk_id,
            signal_group_id=_opt(row, "SG", "SignalGroup"),
            walk_s=_opt_float(row, "PedWalk", "Walk time", "WalkTime"),
            clearance_1_s=_opt_float(row, "PedClearance", "Clearance", "Clearance1"),
            clearance_2_s=_opt_float(row, "Clearance2"),
            delay_s=_opt_float(row, "PedDelay", "Delay"),
            protection_type=protection,
            protection_type_raw=raw_protection,
        )
        if ped.clearance_2_s is None:
            ped.mark_absent(
                "clearance_2_s",
                "A.3's WA rule derives clearance-2 as intergreen − 1 s; the intergreen is not "
                "stated per walk in this source, so it is not computed here.",
            )
        controller.pedestrian_phases.append(ped)

    def _parse_link_offset(
        self, path: Path, controller: SignalControllerData, result: ImportResult
    ) -> None:
        rows = _read_csv(path)
        if not rows:
            return
        subsystems = {value for row in rows if (value := _opt(row, "Subsystem"))}
        mins = [value for row in rows if (value := _opt_float(row, "CycleMin", "Cycle min"))]
        maxes = [value for row in rows if (value := _opt_float(row, "CycleMax", "Cycle max"))]

        cycle_min = min(mins) if mins else None
        cycle_max = max(maxes) if maxes else None
        if cycle_min is not None and cycle_max is not None and cycle_min > cycle_max:
            # Assignment does not re-run the model validator, so the
            # contradiction is caught here rather than slipping through.
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="cycle_bounds_contradictory",
                    message=(
                        f"Controller {controller.controller_id}: the Link & Offset Plans tab gives "
                        f"a minimum cycle of {cycle_min:g} s above its maximum of {cycle_max:g} s. "
                        "Neither bound was recorded."
                    ),
                    subject=f"controller {controller.controller_id}",
                    citation=CIT_A5,
                    context={"cycle_min_s": cycle_min, "cycle_max_s": cycle_max},
                )
            )
        else:
            controller.cycle_min_s = cycle_min
            controller.cycle_max_s = cycle_max

        if len(subsystems) == 1:
            controller.coordination = CoordinationData(subsystem=next(iter(subsystems)))
        elif len(subsystems) > 1:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="multiple_subsystems_declared",
                    message=(
                        f"Controller {controller.controller_id}: the Link & Offset Plans tab names "
                        f"{len(subsystems)} subsystems {sorted(subsystems)}; left unset rather "
                        "than choosing one."
                    ),
                    subject=f"controller {controller.controller_id}",
                    citation=CIT_A5,
                )
            )

        for index, row in enumerate(rows, start=2):
            plan_raw = _opt(row, "Plan", "PlanNo", "Link plan")
            if not plan_raw:
                continue
            coordinated = _opt(row, "CoordinatedPhase", "Coordinated phase")
            point: CoordinationPoint | None = None
            phase_id: str | None = None
            if coordinated:
                point = (
                    CoordinationPoint.START_OF_PHASE
                    if "^" in coordinated
                    else CoordinationPoint.END_OF_PHASE
                )
                phase_id = coordinated.split("^")[-1].strip() or None
            offsets_raw = _opt(row, "Offsets", "Offset")
            offsets: list[float] | None = None
            if offsets_raw:
                parsed = []
                for token in offsets_raw.replace(";", ",").split(","):
                    try:
                        parsed.append(float(token.strip()))
                    except ValueError:
                        continue
                offsets = parsed or None
            try:
                plan_number = int(plan_raw)
            except ValueError:
                plan_number = None

            program = SignalProgramData(
                provenance=Provenance(
                    source_file=path.name, locator=f"row {index}", native_id=plan_raw
                ),
                program_id=f"plan_{plan_raw}",
                name=f"Link plan {plan_raw}",
                source_kind=ProgramSourceKind.DECLARED,
                coordination=CoordinationData(
                    subsystem=_opt(row, "Subsystem"),
                    coordinated_phase_id=phase_id,
                    coordination_point=point,
                    offsets_s=offsets,
                    plan_number=plan_number,
                    raw=coordinated,
                ),
            )
            program.mark_absent(
                "phases",
                "A link plan states coordination and cycle bounds, not phase durations.",
            )
            program.mark_absent(
                "cycle_time_s",
                "A link plan states cycle BOUNDS; the running cycle varies within them.",
            )
            controller.programs.append(program)

    def _parse_phase_history(
        self,
        path: Path,
        controller: SignalControllerData,
        result: ImportResult,
        *,
        timings_present: bool,
    ) -> int | None:
        rows = _read_csv(path)
        counts: dict[str, int] = defaultdict(int)
        totals: dict[str, float] = defaultdict(float)
        first_row: dict[str, int] = {}
        starts: list[datetime] = []
        ends: list[datetime] = []

        for index, row in enumerate(rows, start=2):
            phase_id = _opt(row, "Phase")
            duration = _opt_float(row, "Duration", "Duration(s)", "Duration (s)")
            if not phase_id or duration is None:
                result.issues.append(
                    ImportIssue(
                        severity=IssueSeverity.WARN,
                        code="phase_history_row_unusable",
                        message=(
                            f"{path.name} row {index}: missing phase id or duration; row skipped."
                        ),
                        provenance=Provenance(source_file=path.name, locator=f"row {index}"),
                        citation=CIT_A1,
                    )
                )
                continue
            counts[phase_id] += 1
            totals[phase_id] += duration
            first_row.setdefault(phase_id, index)
            start = _parse_dt(_opt(row, "Start"))
            end = _parse_dt(_opt(row, "End"))
            if start:
                starts.append(start)
            if end:
                ends.append(end)

        if not counts:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="phase_history_empty",
                    message=f"{path.name} yielded no usable phase records.",
                    citation=CIT_A1,
                )
            )
            return None

        # A.2: the stretch phase runs every cycle and defines the cycle
        # boundary. We identify it as the unambiguously most frequent phase and
        # refuse to guess when two phases tie.
        peak = max(counts.values())
        leaders = [phase for phase, count in counts.items() if count == peak]
        stretch = leaders[0] if len(leaders) == 1 else None
        cycles = counts[stretch] if stretch else None
        if stretch is None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="stretch_phase_ambiguous",
                    message=(
                        f"Controller {controller.controller_id}: phases {sorted(leaders)} occur "
                        f"equally often ({peak}×), so A.2's stretch phase cannot be identified. "
                        "Cycle count, phase frequency and average cycle are left unset."
                    ),
                    subject=f"controller {controller.controller_id}",
                    citation=CIT_A2,
                    context={"tied_phases": sorted(leaders), "occurrences": peak},
                )
            )

        phases: list[PhaseData] = []
        for phase_id, count in counts.items():
            phase = PhaseData(
                provenance=Provenance(
                    source_file=path.name,
                    locator=f"rows aggregated from row {first_row[phase_id]}",
                    native_id=phase_id,
                    derived_by="MRWA Appendix-A A.2 — occurrence count and total activation time",
                ),
                phase_id=phase_id,
                is_stretch_phase=(phase_id == stretch) if stretch else None,
                frequency=(count / cycles) if cycles else None,
                observation_count=count,
                observed_total_duration_s=totals[phase_id],
            )
            phase.mark_absent(
                "green_s",
                "A.2: a Phase History duration is green PLUS its intergreen. The split is not "
                "in this source.",
            )
            if not timings_present:
                phase.mark_absent(
                    "amber_s",
                    "No Timings tab supplied. A.3's yellow formula needs approach speed and "
                    "grade, which none of these files carry, so it is not computed.",
                )
                phase.mark_absent(
                    "all_red_s",
                    "No Timings tab supplied. A.3's all-red formula needs clearance distance and "
                    "posted speed, which none of these files carry.",
                )
            if cycles is None:
                phase.mark_absent(
                    "frequency",
                    "A.2's f_i needs a complete-cycle count, which needs an unambiguous stretch "
                    "phase.",
                )
            phases.append(phase)

        window: TimeWindow | None = None
        if starts and ends and max(ends) > min(starts):
            window = TimeWindow(start=min(starts), end=max(ends))

        average_cycle: float | None = None
        if cycles:
            average_cycle = sum(totals.values()) / cycles

        program = SignalProgramData(
            provenance=Provenance(
                source_file=path.name,
                locator="all rows",
                native_id="observed_history",
                derived_by=(
                    "MRWA Appendix-A A.2 — avg cycle c̄ = total duration ÷ n_cycles"
                    if average_cycle is not None
                    else "MRWA Appendix-A A.2 — per-phase aggregation"
                ),
            ),
            program_id="observed_history",
            name="SCATS Phase History (observed)",
            source_kind=ProgramSourceKind.OBSERVED_HISTORY,
            cycle_time_s=average_cycle,
            phases=phases,
            observation_window=window,
            observed_cycle_count=cycles,
        )
        if window is None:
            program.mark_absent(
                "observation_window",
                "Phase History rows carry no usable Start/End timestamps.",
            )
        if average_cycle is None:
            program.mark_absent(
                "cycle_time_s", "No complete-cycle count, so A.2's c̄ is not computable."
            )
        controller.programs.append(program)

        if not timings_present:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="intergreen_split_unavailable",
                    message=(
                        f"Controller {controller.controller_id}: Phase History gives phase "
                        "durations (green + intergreen) but no Timings tab was supplied, so "
                        "yellow and all-red are unknown. They were NOT back-computed — A.3's "
                        "formulae need approach speed, grade and clearance distance that these "
                        "files do not contain."
                    ),
                    subject=f"controller {controller.controller_id}",
                    citation=CIT_A3,
                )
            )
        result.issues.append(
            ImportIssue(
                severity=IssueSeverity.INFO,
                code="fixed_time_conversion_deferred",
                message=(
                    "Observed history was imported as observations. The full A.2 conversion to a "
                    "fixed-time plan (frequency-adjusted phase times, coordinated-site cycle "
                    "recount per Fig A-5) is the SCATS→fixed-time module (roadmap 1.6), not the "
                    "adapter."
                ),
                subject=f"controller {controller.controller_id}",
                citation=CIT_A2,
            )
        )
        return cycles

    def _parse_event_history(
        self,
        path: Path,
        controller: SignalControllerData,
        result: ImportResult,
        cycles: int | None,
    ) -> None:
        rows = _read_csv(path)
        sg_open: dict[str, datetime] = {}
        sg_durations: dict[str, list[float]] = defaultdict(list)
        sg_first_row: dict[str, int] = {}
        walk_active_since: dict[str, datetime] = {}
        walk_durations: dict[str, list[float]] = defaultdict(list)
        walk_demands_met: dict[str, int] = defaultdict(int)
        walk_first_row: dict[str, int] = {}

        for index, row in enumerate(rows, start=2):
            stamp = _parse_dt(_opt(row, "Timestamp", "Time", "DateTime"))
            event = _opt(row, "Event")
            if stamp is None or not event:
                continue

            sg_match = _SG_EVENT.match(event)
            if sg_match:
                sg = sg_match.group("sg")
                sg_first_row.setdefault(sg, index)
                if sg_match.group("state").lower() == "on":
                    sg_open[sg] = stamp
                elif sg in sg_open:
                    sg_durations[sg].append((stamp - sg_open.pop(sg)).total_seconds())
                continue

            walk_match = _WALK_EVENT.match(event)
            if walk_match:
                walk = walk_match.group("walk")
                body = walk_match.group("body").lower().replace(" ", "")
                walk_first_row.setdefault(walk, index)
                if "demand=off" in body and "active=on" in body:
                    walk_demands_met[walk] += 1
                    walk_active_since[walk] = stamp
                elif "active=off" in body and walk in walk_active_since:
                    walk_durations[walk].append(
                        (stamp - walk_active_since.pop(walk)).total_seconds()
                    )

        program = controller.program_by_id("observed_history")
        if program is None and sg_durations:
            program = SignalProgramData(
                provenance=Provenance(
                    source_file=path.name,
                    locator="all rows",
                    native_id="observed_history",
                    derived_by="MRWA Appendix-A A.1 — SGi=On/Off pairing",
                ),
                program_id="observed_history",
                name="SCATS Event History (observed)",
                source_kind=ProgramSourceKind.OBSERVED_HISTORY,
            )
            program.mark_absent(
                "phases", "Event History carries signal-group events, not phase records."
            )
            program.mark_absent(
                "cycle_time_s", "No Phase History supplied, so A.2's average cycle is unknown."
            )
            controller.programs.append(program)

        known_groups = {group.signal_group_id for group in controller.signal_groups}
        for sg, durations in sg_durations.items():
            if sg not in known_groups:
                group = SignalGroupData(
                    provenance=Provenance(
                        source_file=path.name, locator=f"row {sg_first_row[sg]}", native_id=sg
                    ),
                    signal_group_id=sg,
                    kind=SignalGroupKind.VEHICLE,
                )
                group.mark_absent(
                    "phase_ids",
                    "Event History states signal-group switching only; phase membership is on "
                    "the LMA drawing, which is not machine-readable.",
                )
                controller.signal_groups.append(group)
                known_groups.add(sg)
            if program is not None:
                window = SignalGroupWindow(
                    provenance=Provenance(
                        source_file=path.name,
                        locator=f"rows aggregated from row {sg_first_row[sg]}",
                        native_id=sg,
                        derived_by="mean of observed SGi=On -> SGi=Off durations",
                    ),
                    signal_group_id=sg,
                    green_duration_s=mean(durations),
                    observation_count=len(durations),
                )
                window.mark_absent(
                    "start_s",
                    "Event History timestamps are wall-clock; placing the green inside the cycle "
                    "needs the stretch-phase timeline (A.2), which is not applied here.",
                )
                window.mark_absent("end_s", "See start_s.")
                program.signal_group_windows.append(window)

        existing_walks = {ped.pedestrian_phase_id for ped in controller.pedestrian_phases}
        for walk in sorted(set(walk_demands_met) | set(walk_durations)):
            frequency = (walk_demands_met[walk] / cycles) if cycles else None
            observed_walk = mean(walk_durations[walk]) if walk_durations.get(walk) else None
            existing = next(
                (ped for ped in controller.pedestrian_phases if ped.pedestrian_phase_id == walk),
                None,
            )
            if existing is not None:
                existing.demand_frequency = frequency
                if frequency is None:
                    existing.mark_absent(
                        "demand_frequency",
                        "A.2 p.11 needs a complete-cycle count from Phase History, which was not "
                        "supplied.",
                    )
                continue
            ped = PedestrianPhaseData(
                provenance=Provenance(
                    source_file=path.name,
                    locator=f"rows aggregated from row {walk_first_row.get(walk, 2)}",
                    native_id=walk,
                    derived_by="MRWA Appendix-A A.2 p.11 — demands met ÷ complete cycles",
                ),
                pedestrian_phase_id=walk,
                walk_s=observed_walk,
                demand_frequency=frequency,
            )
            ped.mark_absent(
                "signal_group_id",
                "Event History Walk records do not name the signal group; the mapping is on the "
                "LMA drawing.",
            )
            if frequency is None:
                ped.mark_absent(
                    "demand_frequency",
                    "A.2 p.11 needs a complete-cycle count from Phase History, which was not "
                    "supplied.",
                )
            controller.pedestrian_phases.append(ped)
            existing_walks.add(walk)

        if walk_demands_met and cycles is None:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="ped_demand_frequency_not_computable",
                    message=(
                        f"Controller {controller.controller_id}: {len(walk_demands_met)} walk "
                        "movement(s) have demand events, but A.2 p.11's frequency needs a "
                        "complete-cycle count that no supplied Phase History provides. Left unset."
                    ),
                    subject=f"controller {controller.controller_id}",
                    citation=CIT_A2_P11,
                )
            )

    def _parse_detector_volumes(
        self,
        path: Path,
        site_id: str,
        controller: SignalControllerData,
        result: ImportResult,
    ) -> None:
        rows = _read_csv(path)
        seen: set[str] = set()
        for index, row in enumerate(rows, start=2):
            detector_id = _opt(row, "Detector", "DetectorId", "Detector no")
            start = _parse_dt(_opt(row, "IntervalStart", "Start"))
            end = _parse_dt(_opt(row, "IntervalEnd", "End"))
            volume = _opt_float(row, "Volume", "Count")
            if not detector_id or start is None or end is None or volume is None or end <= start:
                result.issues.append(
                    ImportIssue(
                        severity=IssueSeverity.WARN,
                        code="detector_volume_row_unusable",
                        message=(
                            f"{path.name} row {index}: detector id, interval or volume missing or "
                            "invalid; row skipped."
                        ),
                        provenance=Provenance(source_file=path.name, locator=f"row {index}"),
                        citation=CIT_A1,
                    )
                )
                continue

            provenance = Provenance(
                source_file=path.name, locator=f"row {index}", native_id=detector_id
            )
            if detector_id not in seen:
                detector = DetectorData(
                    provenance=provenance,
                    detector_id=detector_id,
                    controller_id=controller.controller_id,
                )
                detector.mark_absent(
                    "function",
                    "Detector Volume Data states volumes only; detector function and placement "
                    "are on the LMA drawing.",
                )
                detector.mark_absent("position", "See function — the LMA drawing is not parsed.")
                controller.detectors.append(detector)
                seen.add(detector_id)

            record = DetectorCountData(
                provenance=provenance,
                site_id=site_id,
                detector_id=detector_id,
                controller_id=controller.controller_id,
                interval_start=start,
                interval_end=end,
                volume=volume,
                volume_unit=VolumeUnit.VEHICLES,
            )
            record.mark_absent(
                "vehicle_class",
                "SCATS loop volumes are undifferentiated; no class breakdown exists in this "
                "source.",
            )
            result.detector_counts.append(record)


__all__ = [
    "FieldMapping",
    "PROTECTION_TYPE_MAP",
    "SCATSWAAdapter",
    "SCATS_FIELD_MAP",
]

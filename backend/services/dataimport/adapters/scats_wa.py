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
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

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
from ..schema import (
    ControlType,
    CoordinationData,
    CoordinationPoint,
    DetectorCountData,
    DetectorData,
    DriveSide,
    PedestrianPhaseData,
    PedestrianProtectionType,
    PhaseData,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalGroupData,
    SignalGroupKind,
    SignalGroupWindow,
    SignalProgramData,
    SiteMetadata,
    TimeWindow,
    VolumeUnit,
)

CIT_A1 = "MRWA Appendix A §A.1 (p.3–5) — source data Main Roads provides."
CIT_A2 = "MRWA Appendix A §A.2 — derivation algorithms (stretch phase, avg cycle, frequency)."
CIT_A2_P11 = "MRWA Appendix A §A.2 (p.11) — ped demand frequency = demands met ÷ complete cycles."
CIT_A3 = "MRWA Appendix A §A.3 — time settings (yellow, all-red, pedestrian, protection types)."
CIT_A5 = "MRWA Appendix A §A.5 — coordination, subsystems, link/offset plans."


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


class SCATSWAAdapter(DataAdapter):
    """Adapter #2 (WA / SCATS), stub tier. See the module docstring."""

    info = AdapterInfo(
        name="scats_wa",
        region="AU-WA",
        source_system="SCATS",
        version="0.1.0-stub",
        description=(
            "Maps MRWA Appendix-A structures onto the neutral model. Parses a CSV subset "
            "only; .xlsx tabs and drawings deferred to roadmap Phase 4."
        ),
    )

    # -- sniff -------------------------------------------------------------

    def sniff(self, files: Sequence[Path]) -> SniffResult:
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

    # -- parse -------------------------------------------------------------

    def parse(self, files: Sequence[Path]) -> ImportResult:
        result = self.empty_result()
        roles = _classify(files)
        result.source_files = [path.name for path in files]

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
            return result

        parsed_site = self._parse_site(site_file, result)
        if parsed_site is None:
            # The refusal is already on the result. Returning it (rather than
            # raising) is the contract: expected data problems are reported.
            return result
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
        return result

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

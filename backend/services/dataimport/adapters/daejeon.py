"""대전 스마트교차로 turning-count adapter — adapter #1 (roadmap 1.2, Track D).

Reads the 유성구 count export the client delivered: one .xlsx workbook per
intersection, a single sheet, a **three-row hierarchical header**, and one row
per (approach × 15-minute bin) with hourly ``소계`` subtotal rows interleaved
in the same column layout.

The header, verbatim from the real drop::

    r1 | 유입부 | 시간대 | 좌회전 (7 cols) | 직 진 (7) | 우회전 (7) | 유 턴 (7)
    r2 |        |        | 화물 | | | 버스 | | 승용 | 합계 |  … repeated per movement
    r3 |        |        | 대형 | 중형 | 소형 | 대형 | 소형 |      |      |

so every movement is broken into six vehicle classes (화물 대형/중형/소형,
버스 대형/소형, 승용) plus the movement's own 합계.

**Scope: counts only.** The Daejeon signal side arrived as 23 PDF drawings, not
a machine-readable export; parsing those is not attempted here (a low-confidence
read of a drawing cannot carry the provenance this layer promises), and the
inventory records it as a question to the client rather than an engineering
task. This adapter therefore produces sites and turning counts, no controllers.

What this module has to get right, and why each one is a *contract* decision
rather than a parsing detail — the four numbered items are the ambiguities the
0.7 contract left open for adapter #1 (docs/product/adapter-contract.md §7):

1. **소계 rows are never emitted as records.** A ``소계`` row is the same
   vehicles as the four 15-minute rows above it, re-binned to an hour. Emitting
   both would put two intervals of different lengths in one series — the
   quality gate would (correctly) refuse it as a double count, and the correct
   fix is upstream: do not emit the aggregate. But the aggregate is still
   *evidence*, so it is checked against the rows it summarises before being
   discarded, and a disagreement is a REFUSE. The principle this module follows
   throughout: **what is emitted is judged by the gate; what is deliberately
   not emitted is judged here, at the point of discard.**
2. **합계 columns ARE emitted**, as an ``UNCLASSIFIED``/``ALL_VEHICLES`` record
   beside the six class records. It is a stated value and the contract says
   preserve stated values; because it *is* emitted, judging it belongs to the
   gate, and ``quality.check_class_total_consistency`` (added by this unit)
   does exactly that on the class axis. Consumers must exclude it from sums —
   ``quality.is_all_class_total`` is the one predicate for that, and
   ``dataimport.demand`` uses it.
3. **A blank cell produces no record.** In this export a movement block is
   either fully numeric or fully blank for a given approach (verified across
   the whole real drop: 0 partially blank blocks), so a blank block means the
   movement is not served/counted at that approach, and a blank *row* means the
   interval was not observed. Neither is zero demand, and neither is written
   down as one; the gate's interval-gap warning is what makes the hole visible.
4. **The bin length is read, never assumed.** The file name declares the
   aggregation period (``…_15분_집계 주기.xlsx``) and the row spacing observes
   it; the adapter cross-checks the two and refuses when they disagree, rather
   than doing what the contract explicitly forbids — "assuming 15-minute bins
   because most sources use them".

Two landmines from the real files, both handled here:

* **Ten of the eleven real workbooks declare ``<dimension ref="A1"/>``.**
  openpyxl's read-only mode trusts that declaration and yields a single cell,
  silently reading an empty sheet. ``worksheet.reset_dimensions()`` before
  iterating is mandatory; without it this adapter would import nothing from
  almost every real file and report success.
* **The 유입부 column is vertically merged in ten of the eleven workbooks**, so
  only the first row of each approach block carries the name and every other
  row reads ``None``. The approach is forward-filled from the last non-empty
  value; a sheet whose very first data row has no approach is refused rather
  than attributed to a guess.

Nothing here imports Vissim, and nothing here needs a licence: it is openpyxl
plus the neutral model.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..base import (
    AdapterError,
    AdapterInfo,
    DataAdapter,
    FieldMapping,
    ImportIssue,
    ImportResult,
    IssueSeverity,
    SniffResult,
)
from ..schema import (
    ApproachRef,
    CanonicalVehicleClass,
    ClassificationScheme,
    DriveSide,
    MovementRef,
    Provenance,
    SiteMetadata,
    TimeWindow,
    TurnType,
    TurningCountData,
    VehicleClassRef,
    VolumeUnit,
)

# ---------------------------------------------------------------------------
# Citations
#
# The Daejeon export ships with no specification document, so there is no page
# to cite for its layout: these strings name the *observed* structure and the
# repo document that recorded it, and say so. Borrowing an MRWA page number for
# a Korean file would be exactly the invented-authority the contract forbids.
# ---------------------------------------------------------------------------

CIT_DROP_INVENTORY = (
    "docs/product/data-intake-inventory-2026-08-03.md §1 — structural inventory of the "
    "대전 유성구 count drop (no vendor specification was supplied; layout is observed)."
)
CIT_CONTRACT_NEVER_INVENT = (
    "docs/product/adapter-contract.md §1 — an adapter must never invent a value."
)

# ---------------------------------------------------------------------------
# Regional constants — declared by the adapter, not read from a file.
#
# Contract §2.6: a documented, citable fact about the region an adapter serves
# is legitimate *when it is declared in one reviewable place*. Inventing is
# filling a per-record field the source did not carry. Keep this list short.
# ---------------------------------------------------------------------------

#: Korea drives on the right. Stated once, on SiteMetadata; TurnType stays
#: geometric so the neutral records mean the same thing in both hemispheres.
REGION_DRIVE_SIDE = DriveSide.RIGHT
REGION_JURISDICTION = "대전광역시"
SOURCE_SYSTEM = "스마트교차로"

#: NOT a regional constant: the time zone. Contract §7.8 is explicit that
#: interval timestamps are stored as given and ``SiteMetadata.timezone`` stays
#: ``None`` when the source does not state one. KST is *implied* by the
#: jurisdiction but the export never says it, so it is recorded as an absence
#: with a reason instead of filled in. (KST has no DST, so nothing downstream
#: is ambiguous today; a consumer that needs UTC must ask, not guess.)
TIMEZONE_ABSENT_REASON = (
    "the export states no time zone; timestamps are stored exactly as written "
    "(Asia/Seoul is implied by the jurisdiction but is not stated by the source)"
)

# ---------------------------------------------------------------------------
# Header vocabulary
# ---------------------------------------------------------------------------

APPROACH_HEADER = "유입부"
TIME_HEADER = "시간대"
SUBTOTAL_TOKEN = "소계"

#: Movement header (row 1) -> geometric turn. The real files write "직 진" and
#: "유 턴" with an internal space, so tokens are compared with whitespace
#: stripped while ``turn_raw`` keeps the source's own spelling.
MOVEMENT_TOKENS: dict[str, TurnType] = {
    "좌회전": TurnType.LEFT,
    "직진": TurnType.THROUGH,
    "우회전": TurnType.RIGHT,
    "유턴": TurnType.U_TURN,
}

#: (row-2 token, row-3 token) -> (class code, canonical bucket, label).
#:
#: ``canonical`` is filled only where the coarse bucket is defensible without a
#: correspondence table. 화물 대형/중형 are unambiguously heavy vehicles and
#: 버스 either size is a bus; **화물 소형 is deliberately left unmapped** — a
#: Korean 소형화물차 is typically a ~1 t van-derived truck that behaves like a
#: light vehicle in traffic terms, and calling it either LIGHT_VEHICLE or
#: HEAVY_VEHICLE would decide a question the source did not answer. The gate
#: reports it as an unmapped code (INFO) and the composition seeder carries the
#: verbatim code through, which is what "reduced class coverage rather than the
#: adapter guessing" means in practice. Resolving it is open question 4 to the
#: client (data-intake inventory §"Questions for TK").
CLASS_COLUMNS: dict[tuple[str, str], tuple[str, CanonicalVehicleClass | None, str]] = {
    ("화물", "대형"): ("화물_대형", CanonicalVehicleClass.HEAVY_VEHICLE, "화물 대형"),
    ("화물", "중형"): ("화물_중형", CanonicalVehicleClass.HEAVY_VEHICLE, "화물 중형"),
    ("화물", "소형"): ("화물_소형", None, "화물 소형"),
    ("버스", "대형"): ("버스_대형", CanonicalVehicleClass.BUS, "버스 대형"),
    ("버스", "소형"): ("버스_소형", CanonicalVehicleClass.BUS, "버스 소형"),
    ("승용", ""): ("승용", CanonicalVehicleClass.LIGHT_VEHICLE, "승용"),
}

#: The movement's own all-class figure. Emitted as a record (see the module
#: docstring, decision 2) with the scheme that says "this is not one class".
TOTAL_COLUMN = ("합계", "")
TOTAL_CLASS_CODE = "합계"

#: The scheme these six codes belong to. Contract §7.1 told adapter #1 exactly
#: what to do if Daejeon's classes did not match the twelve-class member placed
#: on spec: add a member, never reuse another scheme's codes.
CLASS_SCHEME = ClassificationScheme.KOREA_SMART_INTERSECTION_6

#: The unit. The export never states one; it is a per-class vehicle count from
#: a movement-aware roadside device, and PCU weighting would not produce the
#: integer per-class columns these files carry. Declared here, once, and
#: reported as an INFO on every import rather than left implicit — the field
#: has no default precisely because a PCU figure read as vehicles is the class
#: of error this layer exists to prevent.
VOLUME_UNIT = VolumeUnit.VEHICLES

#: ``…_<교차로명>_15분_집계 주기.xlsx`` — the aggregation period the export
#: declares about itself, and the token immediately before it is the only
#: intersection identifier anywhere in the file.
_PERIOD_TOKEN = re.compile(r"^(\d+)\s*분$")
#: ``[…] 2026-04-06 00_00_2026-05-10 23_45_…`` — the declared coverage window.
_WINDOW_IN_NAME = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2})_(\d{2})_(\d{4}-\d{2}-\d{2})\s+(\d{1,2})_(\d{2})"
)

#: How many example problems one issue carries before it stops listing them.
#: A corrupted workbook can produce thousands of identical findings; one issue
#: per workbook per code with a capped example list and a full count keeps a
#: refusal readable without hiding its size.
MAX_EXAMPLES = 20


# ---------------------------------------------------------------------------
# Field map — written first, as data, and resolved against schema.py by a test
# (contract §8 checklist item 3).
# ---------------------------------------------------------------------------

DAEJEON_FIELD_MAP: tuple[FieldMapping, ...] = (
    FieldMapping(
        source_structure="file name",
        source_field="교차로명 (token before the aggregation period)",
        neutral_path="SiteMetadata.site_name",
        citation=CIT_DROP_INVENTORY,
        note="Also the fallback SiteMetadata.site_id — see site_id_from_file_name.",
    ),
    FieldMapping(
        source_structure="file name",
        source_field="집계 주기 (e.g. 15분)",
        neutral_path="TurningCountData.interval_end",
        citation=CIT_DROP_INVENTORY,
        note="Declared bin length; cross-checked against observed row spacing.",
    ),
    FieldMapping(
        source_structure="file name",
        source_field="declared coverage window (start_end)",
        neutral_path="SiteMetadata.collection_period",
        citation=CIT_DROP_INVENTORY,
        note="collection_period is set from the OBSERVED data; the declared window is compared to it.",
    ),
    FieldMapping(
        source_structure="Sheet1 / column 유입부",
        source_field="approach label (…방면)",
        neutral_path="TurningCountData.approach.leg_id",
        citation=CIT_DROP_INVENTORY,
        note="Verbatim, also kept in approach.raw. Bearing is never inferred from the label.",
    ),
    FieldMapping(
        source_structure="Sheet1 / column 시간대",
        source_field="interval start timestamp",
        neutral_path="TurningCountData.interval_start",
        citation=CIT_DROP_INVENTORY,
    ),
    FieldMapping(
        source_structure="Sheet1 / header row 1",
        source_field="좌회전 / 직 진 / 우회전 / 유 턴",
        neutral_path="TurningCountData.movement.turn",
        citation=CIT_DROP_INVENTORY,
        note="Geometric TurnType; the source token is kept in movement.turn_raw.",
    ),
    FieldMapping(
        source_structure="Sheet1 / header rows 2–3",
        source_field="화물 대형/중형/소형 · 버스 대형/소형 · 승용",
        neutral_path="TurningCountData.vehicle_class.code",
        citation=CIT_DROP_INVENTORY,
        note="scheme=korea_smart_intersection_6; canonical filled only where defensible.",
    ),
    FieldMapping(
        source_structure="Sheet1 / header rows 2–3",
        source_field="합계 (per-movement all-class total)",
        neutral_path="TurningCountData.vehicle_class.canonical",
        citation=CIT_DROP_INVENTORY,
        note="Emitted as UNCLASSIFIED/ALL_VEHICLES; judged by quality.check_class_total_consistency.",
    ),
    FieldMapping(
        source_structure="Sheet1 / data cell",
        source_field="count value",
        neutral_path="TurningCountData.volume",
        citation=CIT_DROP_INVENTORY,
        note="Cells are text in the real files; blank means not observed, never zero.",
    ),
    FieldMapping(
        source_structure="adapter regional constant",
        source_field="drive side",
        neutral_path="SiteMetadata.drive_side",
        citation=CIT_CONTRACT_NEVER_INVENT,
        note="Contract §2.6 regional constant, declared once as REGION_DRIVE_SIDE.",
    ),
    FieldMapping(
        source_structure="Sheet1 / column 시간대 = 소계",
        source_field="hourly subtotal row",
        neutral_path="TurningCountData.volume",
        parsed=False,
        citation=CIT_DROP_INVENTORY,
        note=(
            "Deliberately NOT emitted (it re-bins the same vehicles); cross-checked against "
            "the rows it summarises, then discarded. See module docstring decision 1."
        ),
    ),
    FieldMapping(
        source_structure="not supplied",
        source_field="signal timings / phasing",
        neutral_path="SignalControllerData.controller_id",
        parsed=False,
        citation=CIT_DROP_INVENTORY,
        note="The Daejeon signal side is 23 PDF drawings; out of scope for this adapter.",
    ),
)


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """Cell -> stripped text. ``None`` and whitespace both become ``""``."""
    if value is None:
        return ""
    return str(value).strip()


def _squash(value: Any) -> str:
    """Header token with all internal whitespace removed ("직 진" -> "직진")."""
    return re.sub(r"\s+", "", _text(value))


def _parse_timestamp(value: Any) -> datetime | None:
    """Interval start, from either a real datetime cell or its text form.

    The real workbooks store every cell as text, including the timestamps, so
    both spellings occur across a drop and both must land on the same instant.
    Returns ``None`` for anything that is not a timestamp — the caller decides
    whether that is the ``소계`` token or a row to report and skip.
    """
    if isinstance(value, datetime):
        return value
    text = _text(value)
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_volume(value: Any) -> float | None:
    """Count cell -> number, or ``None`` for a blank. Non-numeric text is a
    parse problem the caller reports; it never becomes a zero."""
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _modal(values: Iterable[float]) -> float | None:
    counted: dict[float, int] = defaultdict(int)
    for value in values:
        counted[value] += 1
    if not counted:
        return None
    return max(sorted(counted), key=lambda key: counted[key])


class _ColumnPlan:
    """One data column: which movement it belongs to and which class it counts.

    Whether the column is the movement's 합계 is not stored separately —
    ``class_ref`` already says so, and ``quality.is_all_class_total`` is the one
    predicate anything downstream should ask.
    """

    __slots__ = ("index", "turn", "turn_raw", "class_ref")

    def __init__(
        self, index: int, turn: TurnType, turn_raw: str, class_ref: VehicleClassRef
    ) -> None:
        self.index = index
        self.turn = turn
        self.turn_raw = turn_raw
        self.class_ref = class_ref


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class DaejeonCountAdapter(DataAdapter):
    """대전 스마트교차로 turning-count workbooks -> neutral records.

    ``site_ids`` maps a file name to the site identifier the *caller* declares
    for it. Supplying it is the honest path: the export carries no site id
    anywhere in the sheet, so without a declaration the adapter falls back to
    the intersection name embedded in the file name and says so loudly (WARN
    ``daejeon_site_id_from_file_name``). See the class attribute
    :attr:`site_id_from_file_name` for why the fallback exists at all.
    """

    info = AdapterInfo(
        name="daejeon_smart_intersection",
        region="KR-Daejeon",
        source_system=SOURCE_SYSTEM,
        version="1.0.0",
        description=(
            "Turning counts from the 대전 스마트교차로 15-minute export: full turning "
            "movements including U-turns, six vehicle classes plus a stated total. "
            "Counts only — the Daejeon signal side is not machine-readable."
        ),
    )

    #: The contract's MUST-NOT list names "substituting a site id from a file
    #: name" as invention, and the SCATS adapter refuses on exactly that
    #: ground. The clause earns its keep there because a SCATS drop *does*
    #: carry a TCS number, so a file name would be overriding a stated
    #: identity. This export states no identity at all: the intersection name
    #: in the file name is the only identifier the source provides, and
    #: refusing it would refuse 100% of the real drop while discarding the one
    #: fact the exporter did record. So the fallback is allowed, but never
    #: silently: the id is provenanced to the file name, a WARN says the
    #: identity is unverified and must be cross-referenced before it is joined
    #: to a network (contract §7.5 — that join is an explicit, reviewable step,
    #: never a heuristic), and passing ``site_ids`` replaces it with a declared
    #: one. Set False to make the adapter refuse instead.
    site_id_from_file_name: bool = True

    def __init__(self, site_ids: Mapping[str, str] | None = None) -> None:
        self._site_ids = dict(site_ids or {})

    # -- sniff -------------------------------------------------------------

    def sniff(self, files: Sequence[Path]) -> SniffResult:
        """Cheap, read-only, never raises.

        Name evidence alone tops out below the selection threshold on purpose:
        "an .xlsx whose name mentions 집계 주기" is not enough to start mapping
        Korean class columns onto a neutral model. Reading the header row is
        what earns the confidence, and it is one worksheet header away.
        """
        candidates = [path for path in files if path.suffix.lower() in (".xlsx", ".xlsm")]
        if not candidates:
            return SniffResult(confidence=0.0, reason="no .xlsx workbook in the file set")

        named = [
            path
            for path in candidates
            if _declared_period_minutes(path.name) is not None or "집계" in path.name
        ]
        matched: list[Path] = []
        for path in candidates:
            try:
                header = _peek_header(path)
            except Exception:  # a sniff never raises; unreadable == not ours
                continue
            if header and _squash(header[0]) == APPROACH_HEADER and _squash(header[1]) == TIME_HEADER:
                matched.append(path)

        if matched:
            return SniffResult(
                confidence=1.0,
                reason=(
                    f"{len(matched)} workbook(s) carry the 유입부/시간대 header of the "
                    "대전 스마트교차로 count export"
                ),
                matched_files=[path.name for path in matched],
            )
        if named:
            return SniffResult(
                confidence=0.4,
                reason=(
                    f"{len(named)} file name(s) look like a 집계 주기 export but no sheet "
                    "header could be read — below the threshold on purpose"
                ),
                matched_files=[path.name for path in named],
            )
        return SniffResult(
            confidence=0.0, reason="no workbook carries the 유입부/시간대 count header"
        )

    # -- parse -------------------------------------------------------------

    def parse(self, files: Sequence[Path]) -> ImportResult:
        result = self.empty_result()
        result.source_files = [path.name for path in files]

        workbooks = [path for path in files if path.suffix.lower() in (".xlsx", ".xlsm")]
        if not workbooks:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="daejeon_no_workbook_supplied",
                    message=(
                        "No .xlsx workbook was supplied. The 대전 스마트교차로 count export is "
                        "one workbook per intersection; nothing can be read from the given files."
                    ),
                    context={"files": [path.name for path in files]},
                )
            )
            return result

        for path in sorted(workbooks, key=lambda item: item.name):
            self._parse_workbook(path, result)

        if result.counts:
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="daejeon_volume_unit_assumed_vehicles",
                    message=(
                        "The export states no volume unit. Counts are recorded as VEHICLES: "
                        "these are per-class integer counts from a movement-aware roadside "
                        "device, which a PCU-weighted figure would not be. Recorded here "
                        "because the unit is a declaration of this adapter, not a value read "
                        "from the file."
                    ),
                    citation=CIT_CONTRACT_NEVER_INVENT,
                )
            )
            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="daejeon_approach_label_orientation_unverified",
                    message=(
                        "Approach labels are direction names (…방면) kept verbatim. Whether the "
                        "label names the direction traffic arrives FROM or heads TOWARD is not "
                        "stated by the export, so no compass bearing was inferred; the label "
                        "must be matched to network legs explicitly before routing is seeded."
                    ),
                    citation=CIT_DROP_INVENTORY,
                )
            )
        return result

    # -- one workbook ------------------------------------------------------

    def _parse_workbook(self, path: Path, result: ImportResult) -> None:
        sheet_title, rows = _load_rows(path)
        if len(rows) <= 3:
            result.issues.append(
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_sheet_empty",
                    f"{path.name}: the first worksheet holds no data rows below the header.",
                    path,
                )
            )
            return

        header = rows[:3]
        if _squash(_cell(header[0], 0)) != APPROACH_HEADER or _squash(_cell(header[0], 1)) != TIME_HEADER:
            result.issues.append(
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_header_not_recognised",
                    (
                        f"{path.name}: header row 1 does not start with "
                        f"{APPROACH_HEADER}/{TIME_HEADER}, so the column layout cannot be "
                        "established and no column may be guessed."
                    ),
                    path,
                    context={
                        "row1_first_two": [_text(_cell(header[0], 0)), _text(_cell(header[0], 1))]
                    },
                )
            )
            return

        plan, plan_issues = self._plan_columns(header, path)
        result.issues.extend(plan_issues)
        if not plan:
            result.issues.append(
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_no_recognised_count_columns",
                    (
                        f"{path.name}: none of the header's movement/class columns could be "
                        "mapped, so there is nothing this adapter may read without guessing."
                    ),
                    path,
                )
            )
            return

        site_id, site_name, site_issues = self._resolve_site(path)
        result.issues.extend(site_issues)
        if site_id is None:
            return

        self._read_rows(path, sheet_title, rows[3:], plan, site_id, site_name, result)

    # -- header ------------------------------------------------------------

    def _plan_columns(
        self, header: Sequence[Sequence[Any]], path: Path
    ) -> tuple[list[_ColumnPlan], list[ImportIssue]]:
        """Turn the three header rows into a per-column plan.

        Both hierarchy levels are forward-filled across their merged spans
        (openpyxl reports a merged cell's value only on its top-left cell), and
        a column whose (movement, class) pair is not in the vocabulary is
        reported and skipped — never mapped to "the closest" class.
        """
        issues: list[ImportIssue] = []
        width = max(len(row) for row in header)
        plan: list[_ColumnPlan] = []
        unmapped_movements: list[str] = []
        unmapped_classes: list[str] = []

        movement_raw = ""
        turn: TurnType | None = None
        group = ""
        for index in range(2, width):
            top = _text(_cell(header[0], index))
            if top:
                movement_raw = top
                turn = MOVEMENT_TOKENS.get(_squash(top))
                # A new movement restates its own class hierarchy; clearing the
                # forward-filled level keeps one block's classes from leaking
                # into the next, and keeps the trailing empty columns every
                # real workbook carries from inheriting the last 합계.
                group = ""
                if turn is None:
                    unmapped_movements.append(top)
            middle = _text(_cell(header[1], index))
            if middle:
                group = middle
            leaf = _text(_cell(header[2], index))

            if not middle and not leaf:
                continue  # outside any stated class column (trailing padding)
            if turn is None or not group:
                continue
            key = (_squash(group), _squash(leaf))
            if key == TOTAL_COLUMN:
                plan.append(
                    _ColumnPlan(
                        index,
                        turn,
                        movement_raw,
                        VehicleClassRef(
                            scheme=ClassificationScheme.UNCLASSIFIED,
                            code=TOTAL_CLASS_CODE,
                            canonical=CanonicalVehicleClass.ALL_VEHICLES,
                            label=TOTAL_CLASS_CODE,
                        ),
                    )
                )
                continue
            mapped = CLASS_COLUMNS.get(key)
            if mapped is None:
                token = f"{group} {leaf}".strip()
                if token and token not in unmapped_classes:
                    unmapped_classes.append(token)
                continue
            code, canonical, label = mapped
            plan.append(
                _ColumnPlan(
                    index,
                    turn,
                    movement_raw,
                    VehicleClassRef(
                        scheme=CLASS_SCHEME, code=code, canonical=canonical, label=label
                    ),
                )
            )

        if unmapped_movements:
            issues.append(
                self._issue(
                    IssueSeverity.WARN,
                    "daejeon_unmapped_movement_column",
                    (
                        f"{path.name}: {len(unmapped_movements)} movement header(s) are not in "
                        f"the known vocabulary ({', '.join(unmapped_movements[:MAX_EXAMPLES])}); "
                        "their columns were skipped rather than mapped to the nearest turn."
                    ),
                    path,
                    context={"tokens": unmapped_movements[:MAX_EXAMPLES]},
                )
            )
        if unmapped_classes:
            issues.append(
                self._issue(
                    IssueSeverity.WARN,
                    "daejeon_unmapped_class_column",
                    (
                        f"{path.name}: {len(unmapped_classes)} vehicle-class header(s) are not in "
                        f"the known vocabulary ({', '.join(unmapped_classes[:MAX_EXAMPLES])}); "
                        "their columns were skipped rather than guessed into a class."
                    ),
                    path,
                    context={"tokens": unmapped_classes[:MAX_EXAMPLES]},
                )
            )
        return plan, issues

    # -- site identity -----------------------------------------------------

    def _resolve_site(self, path: Path) -> tuple[str | None, str | None, list[ImportIssue]]:
        declared = self._site_ids.get(path.name)
        name_token = _site_name_from_file_name(path.name)
        if declared:
            return declared, name_token, []
        if name_token and self.site_id_from_file_name:
            return (
                name_token,
                name_token,
                [
                    self._issue(
                        IssueSeverity.WARN,
                        "daejeon_site_id_from_file_name",
                        (
                            f"{path.name}: the workbook states no site identifier anywhere, so "
                            f"the site id was taken from the intersection name in the file name. "
                            "It is a label, not a controller id: cross-reference it against the "
                            "network and the signal set before joining, or pass an explicit "
                            "site_ids mapping."
                        ),
                        path,
                        citation=CIT_CONTRACT_NEVER_INVENT,
                        context={"site_id": name_token},
                    )
                ],
            )
        return (
            None,
            name_token,
            [
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_site_identity_unavailable",
                    (
                        f"{path.name}: the workbook states no site identifier and the file name "
                        "does not carry the '…_<교차로명>_<N>분_…' pattern the export uses, so no "
                        "record can be attributed to a site. Supply site_ids={file name: site id}."
                    ),
                    path,
                    citation=CIT_CONTRACT_NEVER_INVENT,
                )
            ],
        )

    # -- data rows ---------------------------------------------------------

    def _read_rows(
        self,
        path: Path,
        sheet_title: str,
        body: Sequence[Sequence[Any]],
        plan: Sequence[_ColumnPlan],
        site_id: str,
        site_name: str | None,
        result: ImportResult,
    ) -> None:
        approach: str | None = None
        parsed: list[tuple[int, str, datetime, Sequence[Any]]] = []
        subtotal_rows: list[tuple[int, str, Sequence[Any]]] = []
        unrecognised: list[str] = []
        no_approach_rows: list[int] = []
        blank_cells = 0

        for offset, row in enumerate(body):
            row_no = offset + 4  # 1-based sheet row (three header rows above)
            label = _text(_cell(row, 0))
            if label:
                approach = label
            when = _cell(row, 1)
            token = _text(when)
            if approach is None:
                if token:
                    no_approach_rows.append(row_no)
                continue
            timestamp = _parse_timestamp(when)
            if timestamp is not None:
                parsed.append((row_no, approach, timestamp, row))
            elif _squash(token) == SUBTOTAL_TOKEN:
                subtotal_rows.append((row_no, approach, row))
            elif token:
                unrecognised.append(f"row {row_no}: {token!r}")

        if no_approach_rows:
            result.issues.append(
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_approach_missing_before_first_label",
                    (
                        f"{path.name}: {len(no_approach_rows)} data row(s) appear before any "
                        "유입부 label, so the approach they belong to is unknown. The column is "
                        "vertically merged in this export; a sheet that starts mid-merge cannot "
                        "be attributed without guessing."
                    ),
                    path,
                    context={"rows": no_approach_rows[:MAX_EXAMPLES]},
                )
            )
            return
        if unrecognised:
            result.issues.append(
                self._issue(
                    IssueSeverity.WARN,
                    "daejeon_unrecognised_time_token",
                    (
                        f"{path.name}: {len(unrecognised)} row(s) carry a 시간대 value that is "
                        "neither a timestamp nor the 소계 subtotal token; they were skipped."
                    ),
                    path,
                    context={"examples": unrecognised[:MAX_EXAMPLES]},
                )
            )
        if not parsed:
            result.issues.append(
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_no_interval_rows",
                    f"{path.name}: no row carries a readable interval timestamp.",
                    path,
                )
            )
            return

        bin_minutes = self._resolve_bin_minutes(path, parsed, result)
        if bin_minutes is None:
            return
        span = timedelta(minutes=bin_minutes)

        duplicates: list[str] = []
        non_numeric: list[str] = []
        seen: set[tuple[str, datetime]] = set()
        emitted = 0
        movements_present: dict[tuple[str, str], bool] = {
            (approach_label, column.turn_raw): False
            for approach_label in {label for _row, label, _t, _c in parsed}
            for column in plan
        }

        for row_no, approach_label, timestamp, row in parsed:
            key = (approach_label, timestamp)
            if key in seen:
                duplicates.append(f"row {row_no}: {timestamp.isoformat()}")
                continue
            seen.add(key)
            provenance = Provenance(
                source_file=path.name,
                locator=f"{sheet_title}!row {row_no}",
            )
            approach_ref = ApproachRef(leg_id=approach_label, raw=approach_label)
            for column in plan:
                raw = _cell(row, column.index)
                volume = _parse_volume(raw)
                if volume is None:
                    if _text(raw):
                        # Non-numeric, non-blank text: report, never coerce.
                        non_numeric.append(
                            f"row {row_no} col {column.index + 1}: {_text(raw)!r}"
                        )
                    else:
                        blank_cells += 1
                    continue
                movements_present[(approach_label, column.turn_raw)] = True
                result.counts.append(
                    TurningCountData(
                        provenance=provenance,
                        site_id=site_id,
                        approach=approach_ref,
                        movement=MovementRef(turn=column.turn, turn_raw=column.turn_raw),
                        vehicle_class=column.class_ref,
                        interval_start=timestamp,
                        interval_end=timestamp + span,
                        volume=volume,
                        volume_unit=VOLUME_UNIT,
                    )
                )
                emitted += 1

        if non_numeric:
            result.issues.append(
                self._issue(
                    IssueSeverity.WARN,
                    "daejeon_non_numeric_count_cell",
                    (
                        f"{path.name}: {len(non_numeric)} count cell(s) hold text that is not a "
                        "number; they were skipped rather than read as zero."
                    ),
                    path,
                    context={"examples": non_numeric[:MAX_EXAMPLES], "total": len(non_numeric)},
                )
            )
        if duplicates:
            result.issues.append(
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_duplicate_interval_row",
                    (
                        f"{path.name}: {len(duplicates)} row(s) repeat an (approach, interval) "
                        "pair already read. The repeats were NOT emitted — every total derived "
                        "from a doubled interval would be wrong — but the sheet contradicts "
                        "itself and must be corrected at source."
                    ),
                    path,
                    context={"examples": duplicates[:MAX_EXAMPLES]},
                )
            )

        self._check_subtotals(path, body, plan, result)

        missing_movements = sorted(
            f"{approach_label} / {movement}"
            for (approach_label, movement), present in movements_present.items()
            if not present
        )
        starts = [timestamp for _row, _approach, timestamp, _cells in parsed]
        site = SiteMetadata(
            provenance=Provenance(source_file=path.name, locator=f"{sheet_title}!A1"),
            site_id=site_id,
            site_name=site_name,
            source_system=SOURCE_SYSTEM,
            jurisdiction=REGION_JURISDICTION,
            drive_side=REGION_DRIVE_SIDE,
            collection_period=TimeWindow(start=min(starts), end=max(starts) + span),
        )
        site.mark_absent("timezone", TIMEZONE_ABSENT_REASON)
        site.mark_absent(
            "coordinate_reference",
            "the count export carries no coordinates and states no CRS",
        )
        result.sites.append(site)

        result.issues.append(
            self._issue(
                IssueSeverity.INFO,
                "daejeon_workbook_parsed",
                (
                    f"{path.name}: {emitted} count record(s) from {len(parsed)} interval row(s) "
                    f"across {len({approach for _r, approach, _t, _c in parsed})} approach(es); "
                    f"{len(subtotal_rows)} 소계 row(s) cross-checked then discarded; "
                    f"{blank_cells} blank cell(s) skipped (blank is not zero)."
                ),
                path,
                context={
                    "records": emitted,
                    "interval_rows": len(parsed),
                    "subtotal_rows": len(subtotal_rows),
                    "blank_cells": blank_cells,
                    "interval_minutes": bin_minutes,
                },
            )
        )
        if missing_movements:
            result.issues.append(
                self._issue(
                    IssueSeverity.INFO,
                    "daejeon_movement_not_counted",
                    (
                        f"{path.name}: {len(missing_movements)} (approach, movement) block(s) are "
                        "blank throughout — the movement is not served or not counted there. No "
                        "zero-volume records were invented for them."
                    ),
                    path,
                    context={"blocks": missing_movements[:MAX_EXAMPLES]},
                )
            )
        self._check_declared_window(path, site, result)

    # -- bin length --------------------------------------------------------

    def _resolve_bin_minutes(
        self,
        path: Path,
        parsed: Sequence[tuple[int, str, datetime, Sequence[Any]]],
        result: ImportResult,
    ) -> float | None:
        """Read the interval length; never assume it.

        Two independent statements exist — the aggregation period the file name
        declares, and the spacing the rows actually show — and the honest
        answer is only available when they agree. The *modal* spacing is used
        because a real export has holes (an unobserved bin leaves a double-width
        gap), and a hole must not change what a bin means.
        """
        per_approach: dict[str, list[datetime]] = defaultdict(list)
        for _row, approach, timestamp, _cells in parsed:
            per_approach[approach].append(timestamp)
        deltas: list[float] = []
        for stamps in per_approach.values():
            ordered = sorted(stamps)
            deltas.extend(
                (later - earlier).total_seconds() / 60.0
                for earlier, later in zip(ordered, ordered[1:])
                if later > earlier
            )
        observed = _modal(deltas)
        declared = _declared_period_minutes(path.name)

        if declared is not None and observed is not None:
            if abs(declared - observed) > 1e-9:
                result.issues.append(
                    self._issue(
                        IssueSeverity.REFUSE,
                        "daejeon_interval_length_contradicts_declaration",
                        (
                            f"{path.name}: the file name declares a {declared:g}-minute "
                            f"aggregation period but the rows are spaced {observed:g} minutes "
                            "apart. One of the two is wrong and nothing downstream could tell "
                            "which, so no interval was written."
                        ),
                        path,
                        context={"declared_minutes": declared, "observed_minutes": observed},
                    )
                )
                return None
            return declared
        if declared is not None:
            return declared
        if observed is not None:
            result.issues.append(
                self._issue(
                    IssueSeverity.WARN,
                    "daejeon_interval_length_inferred",
                    (
                        f"{path.name}: the file name declares no aggregation period, so the "
                        f"interval length was taken from the most common row spacing "
                        f"({observed:g} minutes). It is an inference, not a stated value."
                    ),
                    path,
                    citation=CIT_CONTRACT_NEVER_INVENT,
                    context={"observed_minutes": observed},
                )
            )
            return observed
        result.issues.append(
            self._issue(
                IssueSeverity.REFUSE,
                "daejeon_interval_length_unknown",
                (
                    f"{path.name}: only one interval row is present and the file name declares "
                    "no aggregation period, so the interval's length is unknown. An interval "
                    "stored with an assumed length is a wrong number nobody would notice."
                ),
                path,
                citation=CIT_CONTRACT_NEVER_INVENT,
            )
        )
        return None

    # -- 소계 cross-check ---------------------------------------------------

    def _check_subtotals(
        self,
        path: Path,
        body: Sequence[Sequence[Any]],
        plan: Sequence[_ColumnPlan],
        result: ImportResult,
    ) -> None:
        """Judge every 소계 row against the interval rows it summarises.

        This is the point-of-discard check described in decision 1: the
        subtotal never becomes a record, so the gate can never see it, so the
        contradiction it can reveal has to be caught here or not at all. A
        disagreement is refuse case (a) — one of the two figures is wrong and
        nothing downstream can tell which.
        """
        mismatches: list[str] = []
        approach: str | None = None
        pending: dict[int, list[float]] = defaultdict(list)

        for offset, row in enumerate(body):
            row_no = offset + 4
            label = _text(_cell(row, 0))
            if label and label != approach:
                approach = label
                pending.clear()
            when = _cell(row, 1)
            if _parse_timestamp(when) is not None:
                for column in plan:
                    value = _parse_volume(_cell(row, column.index))
                    if value is not None:
                        pending[column.index].append(value)
                continue
            if _squash(_text(when)) != SUBTOTAL_TOKEN:
                continue
            for column in plan:
                stated = _parse_volume(_cell(row, column.index))
                if stated is None:
                    continue
                summed = sum(pending.get(column.index, []))
                if abs(stated - summed) > 1e-6:
                    mismatches.append(
                        f"row {row_no} col {column.index + 1}: 소계 {stated:g} vs "
                        f"{len(pending.get(column.index, []))} interval row(s) summing {summed:g}"
                    )
            pending.clear()

        if mismatches:
            result.issues.append(
                self._issue(
                    IssueSeverity.REFUSE,
                    "daejeon_subtotal_disagrees_with_intervals",
                    (
                        f"{path.name}: {len(mismatches)} 소계 subtotal(s) disagree with the "
                        "15-minute rows they summarise. The subtotal rows are not imported, but "
                        "the disagreement means at least one figure in this sheet is wrong and "
                        "nothing downstream could tell which."
                    ),
                    path,
                    citation=CIT_DROP_INVENTORY,
                    context={"examples": mismatches[:MAX_EXAMPLES], "total": len(mismatches)},
                )
            )

    # -- declared vs observed window ---------------------------------------

    def _check_declared_window(
        self, path: Path, site: SiteMetadata, result: ImportResult
    ) -> None:
        declared = _declared_window(path.name)
        if declared is None or site.collection_period is None:
            return
        declared_start, declared_last_start = declared
        observed = site.collection_period
        problems: list[str] = []
        if observed.start > declared_start:
            problems.append(
                f"declared start {declared_start.isoformat()} vs first observed "
                f"{observed.start.isoformat()}"
            )
        # The file name labels the last *bin*, so the declared coverage ends one
        # bin after the last declared start.
        if declared_last_start >= observed.end:
            problems.append(
                f"declared last interval {declared_last_start.isoformat()} is not covered; "
                f"observed data ends {observed.end.isoformat()}"
            )
        if problems:
            result.issues.append(
                self._issue(
                    IssueSeverity.WARN,
                    "daejeon_declared_window_not_covered",
                    (
                        f"{path.name}: the export's own file name declares a wider collection "
                        "window than the sheet delivers. The observed span is what was recorded; "
                        "the shortfall is unobserved time, not zero demand."
                    ),
                    path,
                    citation=CIT_DROP_INVENTORY,
                    context={"problems": problems},
                )
            )

    # -- issue helper ------------------------------------------------------

    def _issue(
        self,
        severity: IssueSeverity,
        code: str,
        message: str,
        path: Path,
        *,
        citation: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> ImportIssue:
        return ImportIssue(
            severity=severity,
            code=code,
            message=message,
            subject=path.name,
            citation=citation,
            context=context or {},
        )


# ---------------------------------------------------------------------------
# Workbook access
#
# Kept at module level, and deliberately thin: everything openpyxl-specific is
# here so the parsing logic above is testable against plain row sequences.
# ---------------------------------------------------------------------------

def _cell(row: Sequence[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _open_worksheet(path: Path):
    """Open the first worksheet of a workbook, read-only, dimensions repaired.

    ``reset_dimensions()`` is not optional: ten of the eleven real workbooks
    declare ``<dimension ref="A1"/>``, and openpyxl's read-only mode believes
    it — an adapter without this call reads one cell, finds no data, and
    reports a clean empty import.
    """
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - openpyxl is a hard dependency
        raise AdapterError(f"openpyxl is required to read {path.name}: {exc}") from exc

    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except OSError as exc:
        raise AdapterError(f"cannot read {path.name}: {exc}") from exc
    except Exception as exc:  # a corrupt/non-xlsx byte stream is unreadable input
        raise AdapterError(f"{path.name} is not a readable .xlsx workbook: {exc}") from exc
    return workbook


def _load_rows(path: Path) -> tuple[str, list[tuple[Any, ...]]]:
    """``(sheet title, rows)``. The title goes into every record's locator, so
    provenance names the sheet the file actually has rather than the one the
    real export happens to use."""
    workbook = _open_worksheet(path)
    try:
        worksheet = workbook[workbook.sheetnames[0]]
        worksheet.reset_dimensions()
        return worksheet.title, [tuple(row) for row in worksheet.iter_rows(values_only=True)]
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(f"{path.name} could not be read as a worksheet: {exc}") from exc
    finally:
        workbook.close()


def _peek_header(path: Path) -> tuple[Any, ...] | None:
    """First row of the first sheet, for :meth:`DaejeonCountAdapter.sniff`."""
    workbook = _open_worksheet(path)
    try:
        worksheet = workbook[workbook.sheetnames[0]]
        worksheet.reset_dimensions()
        for row in worksheet.iter_rows(values_only=True):
            padded = tuple(row) + (None,) * max(0, 2 - len(row))
            return padded
        return None
    finally:
        workbook.close()


def _file_name_tokens(file_name: str) -> list[str]:
    return Path(file_name).stem.split("_")


def _declared_period_minutes(file_name: str) -> float | None:
    """The aggregation period the file name declares (``…_15분_…``)."""
    for token in reversed(_file_name_tokens(file_name)):
        match = _PERIOD_TOKEN.match(token.strip())
        if match:
            return float(match.group(1))
    return None


def _site_name_from_file_name(file_name: str) -> str | None:
    """The intersection name: the token immediately before ``…분``.

    Deliberately a positional rule against the export's own naming pattern
    rather than "the longest Korean token" — a rule that can fail visibly is
    worth more here than a heuristic that quietly returns the wrong name.
    """
    tokens = _file_name_tokens(file_name)
    for index in range(len(tokens) - 1, 0, -1):
        if _PERIOD_TOKEN.match(tokens[index].strip()):
            candidate = tokens[index - 1].strip()
            return candidate or None
    return None


def _declared_window(file_name: str) -> tuple[datetime, datetime] | None:
    """``2026-04-06 00_00_2026-05-10 23_45`` -> (first start, last start)."""
    match = _WINDOW_IN_NAME.search(Path(file_name).stem)
    if not match:
        return None
    start_day, start_h, start_m, end_day, end_h, end_m = match.groups()
    try:
        start = datetime.strptime(f"{start_day} {start_h}:{start_m}", "%Y-%m-%d %H:%M")
        end = datetime.strptime(f"{end_day} {end_h}:{end_m}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return start, end


__all__ = [
    "CLASS_COLUMNS",
    "CLASS_SCHEME",
    "DAEJEON_FIELD_MAP",
    "DaejeonCountAdapter",
    "MOVEMENT_TOKENS",
    "REGION_DRIVE_SIDE",
    "SOURCE_SYSTEM",
    "TOTAL_CLASS_CODE",
]

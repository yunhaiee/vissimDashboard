"""현시도 입력 — the standard Excel template, its parser, and the draft export.

Unit #52. Three shapes, one file format, and ONE doctrine, which is the same
doctrine ``phase_diagram.py`` is built around and which nothing here is allowed
to soften:

* :func:`build_template` — the empty workbook a client fills in by hand.
* :func:`parse_template` — an uploaded workbook read back into **proposals**.
* :func:`build_export` — a review's existing proposal written back out as a
  filled-in copy of the same template, every sheet stamped :data:`UNCONFIRMED_MARK`.

**What a workbook can and cannot do here.** A workbook that arrives through
:func:`parse_template` becomes a ``PhaseProposal`` with ``source="excel_upload"``
and the uploader's name on it as PROVENANCE. It is not an attestation and it is
not a fact: the only road from any proposal to a signals document still runs
through ``POST /phase-diagrams/{id}/confirmation`` and the
``MappingAttestation`` the server stamps there. A spreadsheet somebody mailed in
is a claim about a drawing, exactly like a vision model's reading is — the app
records who supplied it and refuses to act on it until a human clicks 확인.

**A bad row is never silently dropped, and never partially imported.** Every
refusal below carries ``{code, reasons}`` plus the 1-based row number the reader
can find in Excel. And a row refusal takes its WHOLE intersection down
(:data:`REFUSED_INTERSECTION`) rather than importing the rows that happened to
parse: an intersection silently missing one leg is a junction with one approach
quietly unassigned, which is the failure this module's parent exists to prevent.

**The bomb guard is mandatory and runs first.** ``xlsx_guard.guard_xlsx_bytes``
is called before ``openpyxl`` is handed the bytes, for the reason that module's
docstring measures: ``load_workbook(read_only=True)`` fully materialises the
shared-strings table before it returns, so a 500 KB upload can cost half a
gigabyte of RAM. Every ``.xlsx`` reader in this app goes through that guard and
this one is not an exception.

No COM, no Vissim, no network. openpyxl only, through the same
``services.exporters`` primitives every other workbook in this app is built with.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Sequence

from ..dataimport.xlsx_guard import XlsxBombError, guard_xlsx_bytes
from .phase_diagram import (
    PhaseDiagramRefused,
    PhaseProposal,
    ProposedApproach,
    ProposedMovement,
    ProposedPhase,
    Turn,
)

# ---------------------------------------------------------------------------
# The format. Carried IN the file, for ``side_inputs.py``'s reason: a workbook
# that was renamed, or built by hand from a screenshot of the columns, is caught
# by its own contents rather than by a filename nobody can trust.
# ---------------------------------------------------------------------------

#: The version stamp. Written into the 안내 sheet as a visible line and looked
#: for TOLERANTLY on the way back in — anywhere in the first rows of any sheet,
#: normalised, so a client who reformats the guide page does not lose the stamp.
TEMPLATE_KIND = "prism.phase_template.v1"
TEMPLATE_VERSION_LABEL = "템플릿 버전"

#: A workbook stamped with some OTHER version of this template is refused by
#: name rather than parsed hopefully: the columns may have moved.
_VERSION_FAMILY = "prism.phase_template.v"

#: The one media type an .xlsx is served as, spelled here so the route and the
#: builder cannot drift to two different strings.
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

GUIDE_SHEET = "안내"
ROWS_SHEET = "현시입력"
#: Unit #52 second pass — the timing table. Optional as a whole (a workbook with
#: no 신호시간 rows is still a valid 현시도 input), but COMPLETE when present:
#: a half-filled timing set would build a Vissim program with a phase missing
#: its green, which is the silent-wrong-numbers failure this project exists to
#: prevent.
TIMING_SHEET = "신호시간"
GUIDE_HEADER = "현시도 입력 템플릿 — 채우기 전에 읽으십시오"

#: The one sentence every exported draft carries, on every sheet. It is not
#: decoration: an .xlsx leaves this app and gets mailed around, and the file has
#: to say what it is when nobody remembers where it came from.
UNCONFIRMED_MARK = "AI 제안 · 미확인 — 확인 클릭 전에는 사실이 아님"

#: The 상태 column an EXPORT carries and a blank template does not. Unknown
#: columns are ignored on the way back in, so a draft round-trips unchanged.
STATUS_COLUMN = "상태"

COL_INTERSECTION_NO = "교차로 ID(선택)"
COL_INTERSECTION_NAME = "교차로명"
COL_PHASE = "현시번호(Φn)"
COL_APPROACH = "접근로 라벨"
COL_MOVEMENTS = "접근로→현시 배정"
COL_AMBER = "황색(초)"
COL_AMBER_NOTE = "황색 근거"
COL_NOTE = "비고"

COLUMNS: tuple[str, ...] = (
    COL_INTERSECTION_NO,
    COL_INTERSECTION_NAME,
    COL_PHASE,
    COL_APPROACH,
    COL_MOVEMENTS,
    COL_AMBER,
    COL_AMBER_NOTE,
    COL_NOTE,
)

#: Columns without which a sheet is not this template. 교차로 ID, 황색 and 비고
#: are all genuinely optional inputs, so their absence is not a refusal.
REQUIRED_COLUMNS: tuple[str, ...] = (
    COL_INTERSECTION_NAME,
    COL_PHASE,
    COL_APPROACH,
)

# -- 신호시간: the timing table's columns ------------------------------------
#
# GRANULARITY, MEASURED rather than chosen (services/genesis/signals_daejeon.py
# ``to_fixed_time_plan``): that converter writes ONE ``declared_amber_s`` into
# every ``PhaseData.amber_s`` while ``intergreen_s`` comes per phase off the
# sheet's own YELLOW(RED) column, and ``all_red_i = intergreen_i - amber``. So
# the amber is per INTERSECTION (it stays in 현시입력, one cell, never repeated)
# and the clearance is per PHASE — and the per-phase column is 황색+전적색
# COMBINED, the same quantity the 대전 sheet prints, so an exported 대전 review
# round-trips its own numbers instead of a re-derivation of them.
#
# EVERY OTHER FIELD the 대전 path emits has a column here too, and that is a
# finding rather than a preference: an adversarial pass proved the first version
# rebuilt a program whose 연동 offset had silently become ``None`` — the sheet
# states 10 s, the round trip produced a plan that ran without it, and Vissim
# would have run that plan without a murmur. The audit that followed found the
# same shape in 최소/최대 녹색 (the SIGNAL TABLE states both) and in 시간대. A
# column each is the cheap half of that fix; the expensive half was noticing.
COL_LEVEL = "수준"
COL_PLAN_ID = "계획ID"
COL_GREEN = "녹색(초)"
COL_CLEARANCE = "소거(초) [황색+전적색]"
COL_MIN_GREEN = "최소녹색(초, 선택)"
COL_MAX_GREEN = "최대녹색(초, 선택)"
COL_CYCLE = "주기(초, 선택)"
COL_OFFSET = "연동(초, 선택)"
COL_TIME_OF_DAY = "시간대(선택)"

TIMING_COLUMNS: tuple[str, ...] = (
    COL_INTERSECTION_NAME,
    COL_LEVEL,
    COL_PLAN_ID,
    COL_PHASE,
    COL_GREEN,
    COL_CLEARANCE,
    COL_MIN_GREEN,
    COL_MAX_GREEN,
    COL_CYCLE,
    COL_OFFSET,
    COL_TIME_OF_DAY,
)

TIMING_REQUIRED_COLUMNS: tuple[str, ...] = (
    COL_INTERSECTION_NAME,
    COL_LEVEL,
    COL_PLAN_ID,
    COL_PHASE,
    COL_GREEN,
)

#: The example row's 교차로명 prefix. Rows starting with it are skipped and the
#: skip is REPORTED (``skipped_example_rows``) — the template ships one filled
#: row so a reader can see the shape, and a silent skip of anything is exactly
#: what this module does not do.
EXAMPLE_PREFIX = "예시)"

#: The movement vocabulary, in the words a Korean sheet prints. Maps onto
#: ``phase_diagram.Turn``'s closed set; anything else becomes ``unknown`` AND
#: keeps its verbatim text as the movement's note, so a word this table does not
#: know is visible to the reviewer instead of being rounded to "through".
_MOVEMENT_WORDS: dict[str, Turn] = {
    "직진": "through",
    "직": "through",
    "through": "through",
    "좌회전": "left",
    "좌": "left",
    "left": "left",
    "우회전": "right",
    "우": "right",
    "right": "right",
    "유턴": "u_turn",
    "u턴": "u_turn",
    "uturn": "u_turn",
    "u_turn": "u_turn",
    "보행": "pedestrian",
    "보행자": "pedestrian",
    "횡단": "pedestrian",
    "pedestrian": "pedestrian",
}

#: How a Turn is WRITTEN OUT, so an exported draft re-parses to the same value.
_TURN_WORDS: dict[str, str] = {
    "through": "직진",
    "left": "좌회전",
    "right": "우회전",
    "u_turn": "유턴",
    "pedestrian": "보행",
    "unknown": "미상",
}

_MOVEMENT_SPLIT = re.compile(r"[,/·;]+|\s{2,}")
_PHASE_DIGITS = re.compile(r"(\d{1,2})")

#: A phase number above this is not a phase this vocabulary describes; it is a
#: mis-typed cell (a year, a cycle length) and is refused rather than accepted.
MAX_PHASE_NO = 20


# ---------------------------------------------------------------------------
# Refusals — one shape, always naming the row a human can open in Excel
# ---------------------------------------------------------------------------

REFUSED_INTERSECTION = "phase_template_intersection_refused"


@dataclass(frozen=True)
class RowRefusal:
    """One named reason one row (or one intersection) produced nothing.

    ``row`` is the 1-based row number Excel itself shows, so "fix row 14" is an
    instruction the reader can act on without counting.
    """

    code: str
    reasons: list[str]
    row: int | None = None
    intersection: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reasons": list(self.reasons),
            "row": self.row,
            "intersection": self.intersection,
        }


@dataclass(frozen=True)
class ParsedPhaseTiming:
    """One phase-row of 신호시간. ``min_green_s``/``max_green_s`` are ``None``
    when the cells are empty, which is a different statement from zero: the
    SIGNAL TABLE prints them for an actuated controller and prints nothing for a
    fixed one, and this carries that distinction rather than flattening it."""

    green_s: float
    clearance_s: float
    min_green_s: float | None = None
    max_green_s: float | None = None


@dataclass
class ParsedPlanTiming:
    """One (수준, 계획ID)'s typed timing, in phase order.

    ``cycle_s`` is ``None`` when the 주기 cell was left empty — the converter
    then derives it as Σ(green + 소거) and SAYS so in the program's provenance.
    A stated cycle that contradicts that sum is refused there by name, never
    corrected.

    ``offset_s`` is the 연동 the plan runs. It is CARRIED but never applied to
    the phase start times, exactly as ``signals_daejeon`` refuses to apply it:
    the sheet never states the instant it counts from. Carrying it and warning
    is the honest shape; dropping it (the first version's bug) produced a plan
    that had quietly lost a corridor's progression.
    """

    level_no: int
    plan_id: int
    cycle_s: float | None = None
    offset_s: float | None = None
    time_of_day: str | None = None
    #: phase id -> its timing. Ordered by phase number on the way out.
    phases: dict[str, ParsedPhaseTiming] = field(default_factory=dict)

    def ordered(self) -> list[tuple[str, ParsedPhaseTiming]]:
        return sorted(self.phases.items(), key=lambda item: _phase_sort_key(item))

    def to_payload(self) -> dict[str, Any]:
        """The shape ``side_inputs.ExcelTimingPlanRef`` takes, minus the amber —
        which comes from the CONFIRMATION, not from the workbook."""
        return {
            "level_no": self.level_no,
            "plan_id": self.plan_id,
            "cycle_s": self.cycle_s,
            "offset_s": self.offset_s,
            "time_of_day": self.time_of_day,
            "phases": [
                {
                    "phase_id": phase_id,
                    "green_s": timing.green_s,
                    "clearance_s": timing.clearance_s,
                    "min_green_s": timing.min_green_s,
                    "max_green_s": timing.max_green_s,
                }
                for phase_id, timing in self.ordered()
            ],
        }


@dataclass
class ParsedIntersection:
    """One 교차로's rows, read into the pieces a proposal is made of."""

    name: str
    intersection_no: str | None = None
    approaches: list[str] = field(default_factory=list)
    #: phase id -> [(approach label, turn, note)]
    phases: dict[str, list[tuple[str, Turn, str | None]]] = field(default_factory=dict)
    declared_amber_s: float | None = None
    amber_note: str | None = None
    amber_row: int | None = None
    notes: list[str] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)
    #: (수준, 계획ID) -> its typed timing. Empty when the 신호시간 sheet carries
    #: no row for this intersection, which is a first-class case: the mapping is
    #: still confirmable, and the signals build refuses by name until timing
    #: exists.
    plans: dict[tuple[int, int], ParsedPlanTiming] = field(default_factory=dict)

    def timing_payload(self) -> list[dict[str, Any]]:
        return [
            self.plans[key].to_payload() for key in sorted(self.plans)
        ]

    def to_proposal(self, *, source_file: str, uploaded_by: str) -> PhaseProposal:
        """This intersection as a PROPOSAL. Never an attestation, never a fact.

        ``model`` is ``None`` and ``source`` is ``excel_upload``: no model read
        anything here, and the field that would name one must not be filled with
        a person. The person's name goes to ``uploaded_by``, which the review
        screen renders as PROVENANCE ("who supplied this sheet"), never beside
        the word 확인.
        """
        phases = [
            ProposedPhase(
                phase_id=phase_id,
                movements=[
                    ProposedMovement(approach_label=label, turn=turn, note=note)
                    for label, turn, note in movements
                ],
            )
            for phase_id, movements in sorted(self.phases.items(), key=_phase_sort_key)
        ]
        return PhaseProposal(
            outcome="proposed",
            source="excel_upload",
            uploaded_by=uploaded_by,
            model=None,
            image_sha256=None,
            read_intersection_no=self.intersection_no,
            read_intersection_name=self.name,
            approaches=[ProposedApproach(label=label) for label in self.approaches],
            phases=phases,
            notes=list(self.notes),
            source_file=source_file,
            proposed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


@dataclass
class ParsedTemplate:
    """Everything one uploaded workbook produced, including what it refused."""

    source_file: str
    intersections: list[ParsedIntersection] = field(default_factory=list)
    refusals: list[RowRefusal] = field(default_factory=list)
    skipped_example_rows: list[int] = field(default_factory=list)

    def refusal_dicts(self) -> list[dict[str, Any]]:
        return [refusal.to_dict() for refusal in self.refusals]


# ---------------------------------------------------------------------------
# Building — the blank template and the filled draft, one builder
# ---------------------------------------------------------------------------


def _guide_lines(*, marked: bool) -> list[str]:
    lines = [
        f"{TEMPLATE_VERSION_LABEL}: {TEMPLATE_KIND}",
        "",
        "이 파일은 무엇입니까",
        "  · 이 파일을 앱에 올리면 '제안'이 됩니다. 제안은 사실이 아닙니다.",
        "  · 앱 화면에서 사람이 [확인] 버튼을 누른 내용만 신호 입력으로 사용됩니다.",
        "  · 확인하면 누가 언제 확인했는지가 함께 기록됩니다. 이 파일에는 확인자 칸이 없습니다.",
        "",
        "채우는 방법",
        f"  · '{ROWS_SHEET}' 시트에 한 줄에 하나씩: 접근로 하나가 현시 하나에서 진행하면 한 줄입니다.",
        f"  · {COL_INTERSECTION_NAME}: 필수. 같은 이름의 줄들이 하나의 교차로로 묶입니다.",
        f"  · {COL_PHASE}: 필수. 'Φ1' 또는 '1' 또는 '1현시' 모두 인식합니다.",
        f"  · {COL_APPROACH}: 필수. 시트에 인쇄된 접근로 이름을 그대로 적으십시오.",
        f"  · {COL_MOVEMENTS}: 직진 / 좌회전 / 우회전 / 유턴 / 보행 (쉼표로 여러 개). 비우면 '미상'입니다.",
        f"  · {COL_AMBER}: 선택. 숫자만. 교차로당 하나의 값이며, 같은 교차로에서 값이 서로 다르면 거부됩니다.",
        f"  · {COL_AMBER_NOTE}: 황색시간을 그 값으로 선언하는 근거. 이 앱은 기본값을 넣지 않습니다.",
        f"  · {EXAMPLE_PREFIX}로 시작하는 줄은 예시이므로 읽지 않습니다. 지우고 쓰셔도 됩니다.",
        "",
        f"'{TIMING_SHEET}' 시트 — 신호 입력을 만들려면 필요합니다",
        "  · 이 시트를 비워 두어도 현시 배정은 확인할 수 있습니다. 다만 신호시간이 없으면",
        "    signals 입력(주기·녹색·황색)을 만들 수 없고, 그때는 화면이 그 사실을 그대로 알려 줍니다.",
        f"  · 한 줄에 (교차로명, {COL_LEVEL}, {COL_PLAN_ID}, {COL_PHASE})마다 {COL_GREEN}을 적습니다.",
        f"  · {COL_CLEARANCE}: 황색과 전적색을 합한 값이며 반드시 적어야 합니다. 대전 시트의",
        "    YELLOW(RED)와 같은 값이고, 전적색 = 소거 − 황색으로 계산합니다. 비어 있으면 거부합니다 —",
        "    소거시간을 0으로 가정하면 Vissim은 아무 말 없이 그대로 돌아갑니다.",
        f"  · {COL_CYCLE}: 비워 두면 (녹색+소거)의 합을 주기로 씁니다. 적으신 값이 합과 다르면 거부합니다.",
        f"  · {COL_OFFSET}: 연동값입니다. 그대로 기록하지만 현시 시작 시각에는 적용하지 않습니다 —",
        "    기준 시점(주 제어기 시계인지 연동 현시 시작인지)이 어디에도 적혀 있지 않기 때문입니다.",
        f"  · {COL_MIN_GREEN} / {COL_MAX_GREEN}: 감응 제어의 최소·최대 녹색입니다. 비워 두면",
        "    '밝히지 않음'이며 0초와는 다른 뜻입니다.",
        f"  · {COL_TIME_OF_DAY}: 이 계획이 언제 운영되는지, 시트가 적은 그대로.",
        f"  · {COL_CYCLE}·{COL_OFFSET}·{COL_TIME_OF_DAY}는 계획당 한 번만 적으면 됩니다.",
        "  · 한 교차로에 신호시간을 한 줄이라도 적으면, 그 교차로의 모든 현시에 대해 모든 계획의",
        "    녹색이 있어야 합니다. 절반만 적힌 계획은 한 현시가 조용히 빠진 신호가 되기 때문입니다.",
        "",
        "거부에 대하여",
        "  · 한 줄이라도 거부되면 그 교차로 전체를 가져오지 않습니다. 일부만 들어온 교차로는",
        "    한 접근로가 조용히 빠진 교차로이기 때문입니다. 거부된 줄 번호는 화면에 그대로 표시됩니다.",
    ]
    if marked:
        lines = [UNCONFIRMED_MARK, ""] + lines + ["", UNCONFIRMED_MARK]
    return lines


def _build_workbook(
    rows: Sequence[Sequence[Any]],
    *,
    marked: bool,
    example: bool,
    timing_rows: Sequence[Sequence[Any]] = (),
) -> bytes:
    from services.exporters import _finalize, _new_wb, _sheet

    wb = _new_wb()
    guide = _sheet(wb, GUIDE_SHEET, [GUIDE_HEADER])
    for line in _guide_lines(marked=marked):
        guide.append([line])

    # The status column's HEADER carries the mark as well as its rows, so a
    # sheet with no data rows (an export of a review that has no timing yet) is
    # still stamped. The parser matches columns by title and ignores the ones it
    # does not know, so this cannot disturb a round trip.
    status_header = f"{STATUS_COLUMN} · {UNCONFIRMED_MARK}"
    headers = list(COLUMNS) + ([status_header] if marked else [])
    sheet = _sheet(wb, ROWS_SHEET, headers)
    if example:
        sheet.append(
            [
                "1234",
                f"{EXAMPLE_PREFIX} 둔산네거리",
                "Φ1",
                "둔산로(북)",
                "직진, 우회전",
                3.0,
                "예시 — 확인자가 선언한 값",
                "예시 줄입니다. 지우고 쓰십시오.",
            ]
        )
    for row in rows:
        sheet.append(list(row) + ([UNCONFIRMED_MARK] if marked else []))

    timing_headers = list(TIMING_COLUMNS) + ([status_header] if marked else [])
    timing = _sheet(wb, TIMING_SHEET, timing_headers)
    if example:
        timing.append(
            [f"{EXAMPLE_PREFIX} 둔산네거리", 1, 1, "Φ1", 30.0, 4.0, 25.0, 60.0, 68.0, 0.0, "07:00~09:00"]
        )
        timing.append(
            [f"{EXAMPLE_PREFIX} 둔산네거리", 1, 1, "Φ2", 30.0, 4.0, "", "", "", "", ""]
        )
    for row in timing_rows:
        timing.append(list(row) + ([UNCONFIRMED_MARK] if marked else []))

    if marked:
        # A print header as well as the cells: the mark has to survive somebody
        # printing the sheet and passing the paper around.
        for worksheet in wb.worksheets:
            worksheet.oddHeader.center.text = UNCONFIRMED_MARK
    return _finalize(wb)


def build_template() -> bytes:
    """The blank standard template: 안내 + 현시입력 + one example row."""
    return _build_workbook((), marked=False, example=True)


@dataclass(frozen=True)
class ExportEntry:
    """One review, as the draft workbook needs it.

    ``plans`` is the timing this review actually HAS — a 대전 review's own TOD
    table (via :func:`daejeon_timing_payload`), an uploaded review's stored
    rows, or nothing at all for a blank one. It is never synthesised: a draft
    with invented greens would be a plan nobody typed, carried in a file that
    says 미확인 and nothing about where its numbers came from.
    """

    intersection_name: str
    intersection_no: str | None = None
    proposal: PhaseProposal | None = None
    plans: Sequence[dict[str, Any]] = ()
    notes: Sequence[str] = ()
    #: What the REVIEW already holds as an uploaded declaration, if anything. A
    #: 대전 review carries none by construction (the sheet prints 황색+전적색
    #: combined and never the amber alone), so this column exports empty there
    #: rather than borrowing a confirmer's number into a file stamped 미확인.
    declared_amber_s: float | None = None
    amber_declaration_note: str | None = None


def daejeon_timing_payload(sheet: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """An already-parsed 대전 sheet's timing, in this template's shape.

    Only what the sheet itself prints, and by the arithmetic
    ``signals_daejeon.to_fixed_time_plan`` already uses::

        clearance_i = YELLOW(RED)_i     (황색+전적색 combined, as printed)
        green_i     = 시간분할_i - clearance_i

    so an exported draft carries the sheet's OWN numbers and a re-upload rebuilds
    the same program rather than a second derivation of it.

    A (수준, 계획) whose phases are not all statable is left OUT and named in the
    returned notes — a half-exported plan would refuse on re-upload with the
    reader having no idea which sheet cell was missing.
    """
    payload: list[dict[str, Any]] = []
    notes: list[str] = []
    controller = getattr(sheet, "controller", None)
    pairs = sorted(
        {(entry.level_no, entry.plan_id) for entry in getattr(sheet, "tod_entries", [])}
    )
    for level_no, plan_id in pairs:
        row = sheet.plan(plan_id)
        cycle = sheet.level_cycle_s(level_no)
        if row is None:
            notes.append(f"수준 {level_no} / 계획 {plan_id}: 시트에 시간분할 행이 없습니다.")
            continue
        phases: list[dict[str, Any]] = []
        unstated: list[str] = []
        for index, split_s in enumerate(row.splits_ring_a, start=1):
            if split_s is None:
                continue  # the phase does not run in this plan
            timing = sheet.phase(index)
            clearance = timing.yellow_red_s if timing else None
            if clearance is None:
                unstated.append(f"Φ{index}")
                continue
            green = round(float(split_s) - float(clearance), 6)
            if green <= 0:
                unstated.append(f"Φ{index}")
                continue
            phases.append(
                {
                    "phase_id": f"Φ{index}",
                    "green_s": green,
                    "clearance_s": round(float(clearance), 6),
                    # The SIGNAL TABLE's own 최소/최대 녹색. Absent stays absent:
                    # a fixed-time controller prints neither, and writing 0 would
                    # state a minimum green of zero seconds.
                    "min_green_s": None if timing is None else timing.min_green_s,
                    "max_green_s": None if timing is None else timing.max_green_s,
                }
            )
        if unstated or not phases:
            notes.append(
                f"수준 {level_no} / 계획 {plan_id}: 시트가 {', '.join(unstated) or '이 계획'}의 "
                f"소거시간을 밝히지 않아 {TIMING_SHEET}에 내보내지 않았습니다."
            )
            continue
        program = (
            controller.program_by_id(f"L{level_no}-P{plan_id}")
            if controller is not None
            else None
        )
        payload.append(
            {
                "level_no": level_no,
                "plan_id": plan_id,
                "cycle_s": None if cycle is None else round(float(cycle), 6),
                # 연동. Carried verbatim and NEVER applied to the phase starts,
                # the same as on the sheet path — the sheet does not state the
                # instant it counts from. Dropping it (the first version) built
                # a plan that had quietly lost a corridor's progression.
                "offset_s": None if row.offset_s is None else round(float(row.offset_s), 6),
                "time_of_day": None if program is None else program.time_of_day,
                "phases": phases,
            }
        )
    return payload, notes


def build_export(entries: Sequence[ExportEntry]) -> bytes:
    """Reviews → a filled copy of the template, stamped UNCONFIRMED on every sheet.

    A review whose proposal is missing or refused still gets its rows — one empty
    row naming why — because a draft that silently omitted the intersections the
    model could not read would understate the work left to do.

    Every ``ambiguity`` sentence the model wrote lands in 비고 verbatim. That is
    the field carrying "what the machine could NOT resolve", and an export that
    dropped it would hand a reviewer a table that looks finished.

    Every timing row the review HAS lands in 신호시간, so a 대전 review exported,
    corrected and re-uploaded rebuilds the same signal program.
    """
    rows: list[list[Any]] = []
    timing_rows: list[list[Any]] = []
    for entry in entries:
        name = entry.intersection_name
        intersection_no = entry.intersection_no
        proposal = entry.proposal
        for note in entry.notes:
            rows.append([intersection_no or "", name, "", "", "", "", "", f"메모: {note}"])
        for plan in entry.plans:
            phases = list(plan.get("phases") or [])
            for index, phase in enumerate(phases):
                timing_rows.append(
                    [
                        name,
                        plan.get("level_no"),
                        plan.get("plan_id"),
                        phase.get("phase_id"),
                        phase.get("green_s"),
                        phase.get("clearance_s"),
                        _blank(phase.get("min_green_s")),
                        _blank(phase.get("max_green_s")),
                        # 주기 · 연동 · 시간대 belong to the PLAN, so they ride on
                        # that plan's first row and are never repeated -- a
                        # second copy is a second chance to disagree with itself.
                        _blank(plan.get("cycle_s")) if index == 0 else "",
                        _blank(plan.get("offset_s")) if index == 0 else "",
                        _blank(plan.get("time_of_day")) if index == 0 else "",
                    ]
                )
        # The amber rides on this intersection's FIRST row, exactly where the
        # parser reads it from and never repeated — one number per intersection
        # is what the converter uses, and a second copy is a second chance to
        # disagree with itself.
        pending: list[Any] = [entry.declared_amber_s, entry.amber_declaration_note or ""]

        def mapping_row(
            phase_id: str,
            approach: str,
            movements: str,
            note: str,
            *,
            _no: str | None = intersection_no,
            _name: str = name,
            _pending: list[Any] = pending,
        ) -> list[Any]:
            amber, amber_note = _pending[0], _pending[1]
            _pending[0], _pending[1] = None, ""
            return [
                _no or "",
                _name,
                phase_id,
                approach,
                movements,
                "" if amber is None else amber,
                amber_note,
                note,
            ]

        if proposal is None or proposal.outcome == "refused":
            reason = (
                "판독 없음 — 직접 입력하십시오"
                if proposal is None or proposal.refusal is None
                else f"판독 거부: {proposal.refusal.code} — {proposal.refusal.reason}"
            )
            rows.append(mapping_row("", "", "", reason))
            continue
        for phase in proposal.phases:
            ambiguity = phase.ambiguity
            if not phase.movements:
                rows.append(mapping_row(phase.phase_id, "", "", _note_cell(None, ambiguity)))
                continue
            for index, movement in enumerate(phase.movements):
                rows.append(
                    mapping_row(
                        phase.phase_id,
                        movement.approach_label,
                        _TURN_WORDS.get(movement.turn, movement.turn),
                        # The ambiguity sentence belongs to the PHASE, so it
                        # rides on that phase's first row and is never repeated.
                        _note_cell(movement.note, ambiguity if index == 0 else None),
                    )
                )
        for note in proposal.notes:
            rows.append(mapping_row("", "", "", f"메모: {note}"))
    return _build_workbook(rows, marked=True, example=False, timing_rows=timing_rows)


def _blank(value: Any) -> Any:
    """``None`` becomes an EMPTY CELL, never a zero.

    The distinction is the point: an empty 최소녹색 means the sheet did not state
    one, and a 0 would mean an actuated minimum green of zero seconds.
    """
    return "" if value is None else value


def _note_cell(note: str | None, ambiguity: str | None) -> str:
    parts = []
    if ambiguity:
        parts.append(f"모델이 확정하지 못한 부분: {ambiguity}")
    if note:
        parts.append(note)
    return " / ".join(parts)


# ---------------------------------------------------------------------------
# Parsing — the way back in
# ---------------------------------------------------------------------------


def _normalise(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_template(content: bytes, *, source_file: str) -> ParsedTemplate:
    """One uploaded workbook → proposals, and every reason a row produced none.

    Raises :class:`PhaseDiagramRefused` for anything that stops the FILE being
    read at all (a bomb, a missing version stamp, an unknown version, absent
    columns). Everything a reader could fix in one cell is a :class:`RowRefusal`
    in the returned value, never an exception — the whole point is that the
    response names all of them at once instead of one per retry.
    """
    name = str(source_file or "").strip() or "upload.xlsx"

    # MANDATORY, and first: see the module docstring and xlsx_guard's own.
    try:
        guard_xlsx_bytes(content, label=name)
    except XlsxBombError as exc:
        raise PhaseDiagramRefused(
            "phase_template_bomb_refused",
            [
                f"{name}: 이 파일은 압축을 풀면 이 앱이 다루는 어떤 실제 워크북보다 큽니다. 열지 않았습니다.",
                str(exc),
            ],
        ) from exc

    import openpyxl

    try:
        workbook = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any reader failure is an unreadable upload
        raise PhaseDiagramRefused(
            "phase_template_unreadable",
            [f"{name}: 엑셀 파일로 열 수 없습니다 ({type(exc).__name__}: {exc})."],
        ) from exc

    try:
        _check_version(workbook, name=name)
        sheet, header = _find_rows_sheet(workbook, name=name)
        parsed = _read_rows(sheet, header, source_file=name)
        _read_timing(workbook, parsed)
        return parsed
    finally:
        workbook.close()


def _check_version(workbook: Any, *, name: str) -> None:
    """The stamp, looked for tolerantly and refused by name when it is wrong."""
    found: list[str] = []
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows(min_row=1, max_row=40, max_col=6, values_only=True):
            for value in row:
                text = _cell(value)
                if _VERSION_FAMILY in text.casefold():
                    found.append(text)
    if any(TEMPLATE_KIND in text.casefold() for text in found):
        return
    if found:
        raise PhaseDiagramRefused(
            "phase_template_version_unknown",
            [
                f"{name}: 이 파일은 다른 판의 현시도 템플릿입니다 ({found[0]!r}). 이 앱이 읽는 판은 "
                f"{TEMPLATE_KIND}입니다 — 열의 위치가 다를 수 있으므로 추측해서 읽지 않습니다.",
                "화면의 [템플릿 다운로드]로 현재 판을 받아 다시 작성하십시오.",
            ],
        )
    raise PhaseDiagramRefused(
        "phase_template_version_missing",
        [
            f"{name}: 현시도 템플릿 표시({TEMPLATE_KIND})가 없습니다. 이 앱은 이름만 보고 파일의 "
            "형식을 믿지 않습니다.",
            "화면의 [템플릿 다운로드]로 받은 파일에 작성하십시오.",
        ],
    )


def _find_rows_sheet(workbook: Any, *, name: str) -> tuple[Any, dict[str, int]]:
    """The 현시입력 sheet and its header map, by CONTENT not by position."""
    wanted = {_normalise(column): column for column in COLUMNS}
    candidates = list(workbook.worksheets)
    named = [ws for ws in candidates if _normalise(ws.title) == _normalise(ROWS_SHEET)]
    for worksheet in named + candidates:
        for row_index, row in enumerate(
            worksheet.iter_rows(min_row=1, max_row=10, values_only=True), start=1
        ):
            header: dict[str, int] = {}
            for column_index, value in enumerate(row or ()):
                key = wanted.get(_normalise(value))
                if key is not None and key not in header:
                    header[key] = column_index
            if all(column in header for column in REQUIRED_COLUMNS):
                header["__row__"] = row_index
                return worksheet, header
    raise PhaseDiagramRefused(
        "phase_template_columns_missing",
        [
            f"{name}: '{ROWS_SHEET}' 표의 필수 열({', '.join(REQUIRED_COLUMNS)})을 찾지 못했습니다.",
            "열 제목을 바꾸지 마십시오 — 이 앱은 제목으로 열을 찾습니다.",
        ],
    )


def _read_rows(sheet: Any, header: dict[str, int], *, source_file: str) -> ParsedTemplate:
    parsed = ParsedTemplate(source_file=source_file)
    start = int(header.get("__row__", 1)) + 1
    by_name: dict[str, ParsedIntersection] = {}
    refused_names: set[str] = set()

    def value(row: Sequence[Any], column: str) -> str:
        index = header.get(column)
        if index is None or index >= len(row):
            return ""
        return _cell(row[index])

    for row_no, row in enumerate(
        sheet.iter_rows(min_row=start, values_only=True), start=start
    ):
        cells = list(row or ())
        if not any(_cell(cell) for cell in cells):
            continue

        name = value(cells, COL_INTERSECTION_NAME)
        if name.startswith(EXAMPLE_PREFIX):
            parsed.skipped_example_rows.append(row_no)
            continue
        if not name:
            parsed.refusals.append(
                RowRefusal(
                    code="phase_template_row_no_intersection",
                    reasons=[
                        f"{row_no}행: {COL_INTERSECTION_NAME}이(가) 비어 있습니다. 이 줄이 어느 "
                        "교차로의 것인지 이 앱은 추측하지 않습니다."
                    ],
                    row=row_no,
                )
            )
            continue

        entry = by_name.setdefault(name, ParsedIntersection(name=name))
        entry.rows.append(row_no)

        phase_id = _phase_id(value(cells, COL_PHASE))
        if phase_id is None:
            parsed.refusals.append(
                RowRefusal(
                    code="phase_template_row_bad_phase",
                    reasons=[
                        f"{row_no}행: 현시번호 {value(cells, COL_PHASE)!r}을(를) 읽을 수 없습니다. "
                        f"'Φ1' 또는 '1' 형식으로, 1–{MAX_PHASE_NO} 사이여야 합니다."
                    ],
                    row=row_no,
                    intersection=name,
                )
            )
            refused_names.add(name)
            continue

        approach = value(cells, COL_APPROACH)
        if not approach:
            parsed.refusals.append(
                RowRefusal(
                    code="phase_template_row_no_approach",
                    reasons=[
                        f"{row_no}행: {COL_APPROACH}이(가) 비어 있습니다. 어느 접근로가 "
                        f"{phase_id}에서 진행하는지가 이 표의 내용 자체입니다."
                    ],
                    row=row_no,
                    intersection=name,
                )
            )
            refused_names.add(name)
            continue

        amber_text = value(cells, COL_AMBER)
        if amber_text:
            amber = _amber(amber_text)
            if amber is None:
                parsed.refusals.append(
                    RowRefusal(
                        code="phase_template_row_amber_invalid",
                        reasons=[
                            f"{row_no}행: 황색시간 {amber_text!r}은(는) 숫자가 아닙니다. 이 값은 "
                            "확인자가 선언하는 초 단위 숫자입니다."
                        ],
                        row=row_no,
                        intersection=name,
                    )
                )
                refused_names.add(name)
                continue
            if entry.declared_amber_s is not None and entry.declared_amber_s != amber:
                parsed.refusals.append(
                    RowRefusal(
                        code="phase_template_row_amber_conflict",
                        reasons=[
                            f"{row_no}행: 같은 교차로({name})에 황색시간이 두 값으로 적혀 있습니다 "
                            f"({entry.declared_amber_s}초, {amber}초). 어느 쪽이 맞는지 이 앱은 "
                            "고르지 않습니다."
                        ],
                        row=row_no,
                        intersection=name,
                    )
                )
                refused_names.add(name)
                continue
            entry.declared_amber_s = amber
            entry.amber_row = row_no
            note = value(cells, COL_AMBER_NOTE)
            if note and not entry.amber_note:
                entry.amber_note = note

        intersection_no = value(cells, COL_INTERSECTION_NO)
        if intersection_no and not entry.intersection_no:
            entry.intersection_no = intersection_no

        note = value(cells, COL_NOTE)
        turns = _movements(value(cells, COL_MOVEMENTS))
        if approach not in entry.approaches:
            entry.approaches.append(approach)
        movements = entry.phases.setdefault(phase_id, [])
        for turn, verbatim in turns:
            movements.append((approach, turn, _movement_note(note, verbatim)))

    for name in sorted(refused_names):
        entry = by_name.pop(name, None)
        parsed.refusals.append(
            RowRefusal(
                code=REFUSED_INTERSECTION,
                reasons=[
                    f"{name}: 위에 표시된 줄이 거부되어 이 교차로 전체를 가져오지 않았습니다. "
                    "일부만 들어온 교차로는 한 접근로가 조용히 빠진 교차로입니다."
                ],
                intersection=name,
            )
        )

    for name, entry in list(by_name.items()):
        # Defensive, and unreachable on today's paths: a row that reaches
        # `entry.phases` has already cleared the phase-id and approach checks, so
        # an intersection with rows and no phases cannot exist. It stays because
        # the alternative shape of that bug is `to_proposal()` raising inside
        # pydantic (a 500 with no code), and a named refusal is the failure this
        # module is supposed to have.
        if not entry.phases:
            by_name.pop(name)
            parsed.refusals.append(
                RowRefusal(
                    code="phase_template_intersection_empty",
                    reasons=[f"{name}: 배정된 현시가 하나도 없어 제안을 만들지 않았습니다."],
                    intersection=name,
                )
            )

    parsed.intersections = [by_name[name] for name in sorted(by_name)]
    if not parsed.intersections and not parsed.refusals:
        if parsed.skipped_example_rows:
            # The untouched template. Naming this separately matters: "표가
            # 비었습니다" would send a reader looking for a broken file, when
            # what happened is that they uploaded the blank form.
            raise PhaseDiagramRefused(
                "phase_template_only_example_rows",
                [
                    f"{source_file}: 예시 줄만 있고 입력된 줄이 없습니다 "
                    f"({EXAMPLE_PREFIX}로 시작하는 줄은 읽지 않습니다).",
                    "'현시입력' 시트에 교차로명·현시번호·접근로 라벨을 채운 뒤 다시 올리십시오.",
                ],
            )
        raise PhaseDiagramRefused(
            "phase_template_no_rows",
            [f"{source_file}: '{ROWS_SHEET}' 표에 읽을 줄이 없습니다."],
        )
    return parsed


def _find_timing_sheet(workbook: Any) -> tuple[Any, dict[str, int]] | None:
    """The 신호시간 sheet and its header map, or ``None`` when the workbook has
    none — which is a first-class case, not a fault: a 현시도 mapping is
    confirmable without any timing at all."""
    wanted = {_normalise(column): column for column in TIMING_COLUMNS}
    named = [
        ws for ws in workbook.worksheets if _normalise(TIMING_SHEET) in _normalise(ws.title)
    ]
    for worksheet in named:
        for row_index, row in enumerate(
            worksheet.iter_rows(min_row=1, max_row=10, values_only=True), start=1
        ):
            header: dict[str, int] = {}
            for column_index, value in enumerate(row or ()):
                key = wanted.get(_normalise(value))
                if key is not None and key not in header:
                    header[key] = column_index
            if all(column in header for column in TIMING_REQUIRED_COLUMNS):
                header["__row__"] = row_index
                return worksheet, header
    return None


def _read_timing(workbook: Any, parsed: ParsedTemplate) -> None:
    """The 신호시간 sheet, read onto the intersections the mapping produced.

    **Optional as a whole, complete when present.** A workbook with no timing
    rows leaves every ``ParsedIntersection.plans`` empty and nothing is refused:
    the mapping is still confirmable and the signals build says, by name, what
    to add. But the moment ONE row names an intersection, that intersection's
    timing has to be a plan somebody could actually run — every phase in its
    mapping gets a green in every plan named, every green belongs to a phase the
    mapping has, and no (수준, 계획, 현시) is stated twice. Anything short of
    that refuses BY ROW, and takes the whole intersection with it for the same
    reason a bad mapping row does: a plan missing one phase is an intersection
    with one movement quietly dark.
    """
    found = _find_timing_sheet(workbook)
    if found is None:
        return
    sheet, header = found
    start = int(header.get("__row__", 1)) + 1
    by_name = {entry.name: entry for entry in parsed.intersections}
    refused: set[str] = set()
    # (name, level, plan, phase) -> the row that first stated it
    seen: dict[tuple[str, int, int, str], int] = {}
    stated_cycles: dict[tuple[str, int, int], tuple[float, int]] = {}
    stated_offsets: dict[tuple[str, int, int], tuple[float, int]] = {}
    stated_tod: dict[tuple[str, int, int], tuple[str, int]] = {}
    touched: set[str] = set()

    def value(row: Sequence[Any], column: str) -> str:
        index = header.get(column)
        if index is None or index >= len(row):
            return ""
        return _cell(row[index])

    def refuse(code: str, reason: str, *, row: int, name: str | None) -> None:
        parsed.refusals.append(
            RowRefusal(code=code, reasons=[reason], row=row, intersection=name)
        )
        if name:
            refused.add(name)

    for row_no, raw in enumerate(sheet.iter_rows(min_row=start, values_only=True), start=start):
        cells = list(raw or ())
        if not any(_cell(cell) for cell in cells):
            continue
        name = value(cells, COL_INTERSECTION_NAME)
        if name.startswith(EXAMPLE_PREFIX):
            parsed.skipped_example_rows.append(row_no)
            continue
        if not name:
            refuse(
                "phase_template_timing_no_intersection",
                f"{row_no}행({TIMING_SHEET}): 교차로명이 비어 있어 어느 교차로의 신호시간인지 "
                "알 수 없습니다.",
                row=row_no,
                name=None,
            )
            continue
        entry = by_name.get(name)
        if entry is None:
            refuse(
                "phase_template_timing_unknown_intersection",
                f"{row_no}행({TIMING_SHEET}): '{name}'은(는) 현시입력 시트에 없는 교차로입니다. "
                "배정 없이 시간만 있는 신호는 만들지 않습니다.",
                row=row_no,
                name=name,
            )
            continue
        touched.add(name)

        level = _int_cell(value(cells, COL_LEVEL))
        plan_id = _int_cell(value(cells, COL_PLAN_ID))
        if level is None or plan_id is None:
            refuse(
                "phase_template_timing_bad_plan_key",
                f"{row_no}행({TIMING_SHEET}): 수준/계획ID가 정수가 아닙니다 "
                f"({value(cells, COL_LEVEL)!r}, {value(cells, COL_PLAN_ID)!r}).",
                row=row_no,
                name=name,
            )
            continue
        phase_id = _phase_id(value(cells, COL_PHASE))
        if phase_id is None:
            refuse(
                "phase_template_timing_bad_phase",
                f"{row_no}행({TIMING_SHEET}): 현시번호 {value(cells, COL_PHASE)!r}을(를) 읽을 수 "
                "없습니다.",
                row=row_no,
                name=name,
            )
            continue
        if phase_id not in entry.phases:
            refuse(
                "phase_template_timing_green_without_phase",
                f"{row_no}행({TIMING_SHEET}): {phase_id}은(는) '{name}'의 현시입력에 없는 "
                "현시입니다. 배정되지 않은 현시에 녹색을 주면 아무 접근로도 진행하지 않는 "
                "현시가 만들어집니다.",
                row=row_no,
                name=name,
            )
            continue

        key = (name, level, plan_id, phase_id)
        if key in seen:
            refuse(
                "phase_template_timing_duplicate",
                f"{row_no}행({TIMING_SHEET}): 수준 {level} / 계획 {plan_id} / {phase_id}이(가) "
                f"{seen[key]}행에도 있습니다. 같은 현시에 두 값이 있으면 한쪽이 조용히 이깁니다.",
                row=row_no,
                name=name,
            )
            continue
        seen[key] = row_no

        green = _seconds(value(cells, COL_GREEN))
        if green is None or green <= 0:
            refuse(
                "phase_template_timing_green_invalid",
                f"{row_no}행({TIMING_SHEET}): 녹색 {value(cells, COL_GREEN)!r}은(는) 0보다 큰 "
                "숫자여야 합니다.",
                row=row_no,
                name=name,
            )
            continue
        clearance_text = value(cells, COL_CLEARANCE)
        clearance = _seconds(clearance_text)
        if clearance is None or clearance < 0:
            refuse(
                "phase_template_timing_clearance_invalid",
                f"{row_no}행({TIMING_SHEET}): 소거시간(황색+전적색)이 필요합니다 "
                f"({clearance_text!r}). 0으로 가정하지 않습니다 — 소거시간을 지어내면 Vissim은 "
                "아무 말 없이 그대로 돌립니다.",
                row=row_no,
                name=name,
            )
            continue

        band: list[float | None] = []
        for column, code in (
            (COL_MIN_GREEN, "phase_template_timing_min_green_invalid"),
            (COL_MAX_GREEN, "phase_template_timing_max_green_invalid"),
        ):
            text = value(cells, column)
            if not text:
                band.append(None)
                continue
            seconds = _seconds(text)
            if seconds is None or seconds < 0:
                refuse(
                    code,
                    f"{row_no}행({TIMING_SHEET}): {column} {text!r}은(는) 0 이상의 숫자여야 "
                    "합니다. 비워 두면 '시트가 밝히지 않음'이며, 0과는 다른 뜻입니다.",
                    row=row_no,
                    name=name,
                )
                band.append(None)
                break
            band.append(seconds)
        if len(band) < 2:
            continue
        min_green, max_green = band
        if min_green is not None and max_green is not None and min_green > max_green:
            refuse(
                "phase_template_timing_green_band_invalid",
                f"{row_no}행({TIMING_SHEET}): 최소녹색({min_green:g}초)이 "
                f"최대녹색({max_green:g}초)보다 깁니다.",
                row=row_no,
                name=name,
            )
            continue

        # Plan-level cells (주기 · 연동 · 시간대). Each belongs to the PLAN, so
        # it may be typed on any of that plan's rows and must not disagree with
        # itself across them — a second value is a second chance to be wrong,
        # and picking one would be this app choosing a signal timing.
        plan_cells = (
            (COL_CYCLE, stated_cycles, "phase_template_timing_cycle_invalid",
             "phase_template_timing_cycle_conflict", True),
            (COL_OFFSET, stated_offsets, "phase_template_timing_offset_invalid",
             "phase_template_timing_offset_conflict", False),
        )
        bad_plan_cell = False
        for column, store, invalid_code, conflict_code, strictly_positive in plan_cells:
            text = value(cells, column)
            if not text:
                continue
            seconds = _seconds(text)
            if seconds is None or (seconds <= 0 if strictly_positive else seconds < 0):
                refuse(
                    invalid_code,
                    f"{row_no}행({TIMING_SHEET}): {column} {text!r}은(는) "
                    f"{'0보다 큰' if strictly_positive else '0 이상의'} 숫자여야 합니다.",
                    row=row_no,
                    name=name,
                )
                bad_plan_cell = True
                break
            previous = store.get((name, level, plan_id))
            if previous is not None and abs(previous[0] - seconds) > 1e-6:
                refuse(
                    conflict_code,
                    f"{row_no}행({TIMING_SHEET}): 같은 계획(수준 {level} / 계획 {plan_id})에 "
                    f"{column}이(가) 두 값({previous[0]:g}, {seconds:g})으로 적혀 있습니다.",
                    row=row_no,
                    name=name,
                )
                bad_plan_cell = True
                break
            store[(name, level, plan_id)] = (seconds, row_no)
        if bad_plan_cell:
            continue

        time_of_day = value(cells, COL_TIME_OF_DAY)
        if time_of_day:
            previous_tod = stated_tod.get((name, level, plan_id))
            if previous_tod is not None and previous_tod[0] != time_of_day:
                refuse(
                    "phase_template_timing_time_of_day_conflict",
                    f"{row_no}행({TIMING_SHEET}): 같은 계획(수준 {level} / 계획 {plan_id})에 "
                    f"시간대가 두 값({previous_tod[0]!r}, {time_of_day!r})으로 적혀 있습니다.",
                    row=row_no,
                    name=name,
                )
                continue
            stated_tod[(name, level, plan_id)] = (time_of_day, row_no)

        plan = entry.plans.setdefault(
            (level, plan_id), ParsedPlanTiming(level_no=level, plan_id=plan_id)
        )
        plan.phases[phase_id] = ParsedPhaseTiming(
            green_s=green,
            clearance_s=clearance,
            min_green_s=min_green,
            max_green_s=max_green,
        )

    for stated, attribute in (
        (stated_cycles, "cycle_s"),
        (stated_offsets, "offset_s"),
        (stated_tod, "time_of_day"),
    ):
        for (name, level, plan_id), (stated_value, _row) in stated.items():
            entry = by_name.get(name)
            if entry is not None and (level, plan_id) in entry.plans:
                setattr(entry.plans[(level, plan_id)], attribute, stated_value)

    # Completeness: every phase of the mapping in every plan that was named.
    for name in sorted(touched):
        entry = by_name.get(name)
        if entry is None or name in refused:
            continue
        for (level, plan_id), plan in sorted(entry.plans.items()):
            missing = sorted(
                (phase_id for phase_id in entry.phases if phase_id not in plan.phases),
                key=lambda phase_id: _phase_sort_key((phase_id, None)),
            )
            if missing:
                parsed.refusals.append(
                    RowRefusal(
                        code="phase_template_timing_phase_without_green",
                        reasons=[
                            f"{name}: 수준 {level} / 계획 {plan_id}에 {', '.join(missing)}의 "
                            f"녹색이 없습니다. {TIMING_SHEET} 시트에 그 현시의 줄을 채우거나, "
                            "이 계획의 줄을 모두 지우십시오 — 절반만 적힌 계획은 한 현시가 "
                            "조용히 빠진 신호입니다."
                        ],
                        intersection=name,
                    )
                )
                refused.add(name)

    for name in sorted(refused):
        entry = by_name.pop(name, None)
        if entry is None:
            continue
        parsed.intersections = [item for item in parsed.intersections if item.name != name]
        parsed.refusals.append(
            RowRefusal(
                code=REFUSED_INTERSECTION,
                reasons=[
                    f"{name}: {TIMING_SHEET} 시트에서 거부된 줄이 있어 이 교차로 전체를 가져오지 "
                    "않았습니다."
                ],
                intersection=name,
            )
        )


def _int_cell(text: str) -> int | None:
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return int(value) if float(value).is_integer() else None


def _seconds(text: str) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _phase_id(text: str) -> str | None:
    match = _PHASE_DIGITS.search(unicodedata.normalize("NFKC", text or ""))
    if not match:
        return None
    number = int(match.group(1))
    if number < 1 or number > MAX_PHASE_NO:
        return None
    return f"Φ{number}"


def _amber(text: str) -> float | None:
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _movements(text: str) -> list[tuple[Turn, str | None]]:
    """The 배정 cell as (turn, the word we did not know). Never empty.

    A cell this table cannot read becomes ``unknown`` WITH its verbatim text
    attached, so the reviewer sees the word the sheet actually used instead of a
    turn this parser picked for them.
    """
    pieces = [piece.strip() for piece in _MOVEMENT_SPLIT.split(text or "") if piece.strip()]
    if not pieces:
        return [("unknown", None)]
    out: list[tuple[Turn, str | None]] = []
    for piece in pieces:
        turn = _MOVEMENT_WORDS.get(_normalise(piece))
        out.append((turn, None) if turn else ("unknown", piece))
    return out


def _movement_note(note: str, verbatim: str | None) -> str | None:
    parts = [part for part in (note or "", f"원문: {verbatim}" if verbatim else "") if part]
    return " / ".join(parts) or None


def _phase_sort_key(item: tuple[str, Any]) -> tuple[int, str]:
    match = _PHASE_DIGITS.search(item[0])
    return (int(match.group(1)) if match else 999, item[0])


def template_filename(prefix: str = "현시도-입력-템플릿") -> str:
    return f"{prefix}.xlsx"


__all__ = [
    "COLUMNS",
    "COL_AMBER",
    "COL_AMBER_NOTE",
    "COL_APPROACH",
    "COL_INTERSECTION_NAME",
    "COL_INTERSECTION_NO",
    "COL_MOVEMENTS",
    "COL_NOTE",
    "COL_PHASE",
    "EXAMPLE_PREFIX",
    "GUIDE_SHEET",
    "MAX_PHASE_NO",
    "REFUSED_INTERSECTION",
    "ROWS_SHEET",
    "STATUS_COLUMN",
    "COL_CLEARANCE",
    "COL_CYCLE",
    "COL_GREEN",
    "COL_LEVEL",
    "COL_PLAN_ID",
    "TEMPLATE_KIND",
    "TEMPLATE_VERSION_LABEL",
    "TIMING_COLUMNS",
    "TIMING_SHEET",
    "UNCONFIRMED_MARK",
    "XLSX_MEDIA_TYPE",
    "ExportEntry",
    "ParsedIntersection",
    "ParsedPlanTiming",
    "ParsedTemplate",
    "RowRefusal",
    "build_export",
    "daejeon_timing_payload",
    "build_template",
    "parse_template",
    "template_filename",
]

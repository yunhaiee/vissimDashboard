"""대전 ``교차로 운영 DATABASE`` signal-sheet parser — roadmap 3.1's Korean half.

What this module is, and what it is not
---------------------------------------
``services.genesis.signals`` is roadmap 3.1 Genesis (b) **SCATS** half: a 1.6
:class:`~services.dataimport.fixed_time.FixedTimePlan` plus a draft node becomes
Vissim signal controllers, groups, heads and detectors. Its module docstring
opens by recording that "the Daejeon half — parsing Daejeon signal PDFs — is
blocked on client data and is not here", and
``services.dataimport.adapters.daejeon`` (the 스마트교차로 **count** adapter)
records the same block from the other side: "The Daejeon signal side arrived as
23 PDF drawings, not a machine-readable export."

**That block was wrong, and this module is the measurement that retires it.**
The 23 PDFs are not drawings. Every one of them is a text-layer PDF: pypdf's
layout extractor recovers the whole ``교차로 운영 DATABASE`` form — header,
SIGNAL TABLE, PLAN TABLE, TOD PLAN 1/2/3 and the weekly plan — as an aligned
character grid, with no OCR anywhere. What IS a drawing is the small 현시도
sketch in the top-left corner (approach names, lane widths, phase arrows), and
that distinction turns out to be the whole story of what this module can and
cannot deliver: see "The gap this module does not close" below.

This module reads the sheet. It does not decide what the numbers mean where the
sheet is silent, and it does not build a signal controller on its own — its
output is the neutral :class:`~services.dataimport.schema.SignalControllerData`
that every other consumer in this codebase already reads, plus a bridge
(:func:`to_fixed_time_plan`) into the exact ``FixedTimePlan`` shape
``signals.generate_node_signals`` takes. Nothing here imports Vissim, nothing
here needs a licence, and nothing here needs the client PDFs: the parser's
entry point is :func:`parse_sheet_text`, which takes **text**, so the whole
module is testable against synthetic fixtures.


The document, as measured (23 files, 2026-08-10)
------------------------------------------------
One PDF per signalised intersection, one page each, ~1.3–1.7 kB of extractable
text. The form is the Korean police-standard 교차로 운영 DATABASE sheet, printed
by six different controller firmwares (``DK-3000S-R28``, ``DK-3700S``,
``SDS-9000``, ``SDS-9000A``, ``SDS-9000SD``, ``SDS-9000SD-R28``). The firmware
name changes the header line and nothing else — all 23 share one layout:

``header``
    ``교차로명`` (name), ``교차로번호`` (the controller id), ``교차로군``
    (the SCATS-style subarea, usually ``SA nn``), the firmware string, and a
    ``YYYY년 MM월 DD일 HH시 MM분 변경`` revision stamp.

``SIGNAL TABLE``
    Seven rows × six phase columns ``Φ1``–``Φ6``, each row printed **twice**:
    once for ``RING A`` and once for ``RING B``. The rows are ``OPTION``,
    ``MIN. GREEN``, ``MAX.Ⅱ``, ``YELLOW(RED)``, ``BEF. PED.``, ``WALK`` and
    ``WALK CLEAR``. Cells are legitimately blank — a ring that does not serve a
    phase, or a phase with no pedestrian movement — so values are assigned to
    phases **by column position**, never by order. A naive whitespace split
    misassigns every blank-bearing row, and eight of the 23 sheets have at
    least one blank in ``YELLOW(RED)`` alone.

``PLAN TABLE``
    Two independent lookup tables printed side by side, which is the one thing
    about this form that is easy to get wrong:

    * ``수준`` (level) 1–6 → ``주기(초)``, the **cycle time**.
    * ``ID`` 1–16 → ``연동`` (the coordination offset) plus ``시간분할(초)``,
      the **split vector** — one comma-separated list of phase times for
      ``RING A`` and a second for ``RING B``, printed on two adjacent rows with
      the ID label vertically centred between them (so the label lands on the
      first row in some sheets and the second in others). Sheets with actuated
      operation print further split vectors under ``감응제어``'s
      ``압버튼(×)`` / ``검지기(○)`` / ``검지기(×)`` sub-columns.

    They are independent because ``TOD PLAN`` selects one of each: a row reads
    ``구분, 시각, 수준, ID``, e.g. *at 07:00 run level 4 with plan 8*. Reading
    the ``수준`` column as "the level that owns the IDs printed beside it" —
    which the printed layout invites — produces wrong cycle times, and one of
    the 23 sheets proves it by pairing level 4 with plan 8 while plan 8 is
    printed beside level 3.

``TOD PLAN 1/2/3`` + ``주간신호계획`` + ``특수일신호계획``
    Three time-of-day tables, then weekday → (TOD plan, TIME plan) and
    special-date → (TOD plan, TIME plan) maps.

``MEMO``
    Free text, and not decorative: two sheets carry
    ``…현시 보행신호시간 Y값 포함`` — *for these phases the pedestrian time
    includes the yellow* — which changes what ``BEF. PED. + WALK + WALK CLEAR``
    adds up to. It is surfaced as a warning rather than acted on.

The internal consistency check that makes the parse verifiable:
**Σ(시간분할) must equal the 주기 of the level the TOD table pairs it with.**
It is arithmetic a reviewer can redo on paper, it is checked for every
(level, plan) pair any TOD row actually references, and a mismatch is a REFUSE,
not a rounding note — a split vector that does not close the cycle means either
the extraction dropped a column or the sheet is internally wrong, and both are
things a human must look at before anything reaches Vissim.


The gap this module does not close — stated up front
-----------------------------------------------------
Two facts a Vissim fixed-time program needs are **not in the text layer**, and
neither is guessed:

1. **Which approach runs in which phase.** That is the 현시도 sketch — a
   drawing. ``signals.generate_node_signals`` already treats this as required
   caller input (``approach_phases``) and refuses without it, for exactly the
   reason its docstring gives: nothing links a phase id to a compass bearing.
   This module changes nothing there; it reports the absence
   (``daejeon_phase_movement_diagram_not_text``) so the requirement is visible
   rather than discovered late.

2. **The yellow/all-red split.** The sheet has ONE clearance row, labelled
   ``YELLOW(RED)``, and no ``전적색`` row anywhere. Two readings were possible
   and the sheet alone does not choose between them — but the client has now
   **answered the question this module raised**: ``YELLOW(RED)`` is
   ``황색 + 전적색`` combined (the whole clearance), not the yellow alone. This
   matches the evidence the parser itself surfaced — across the real drop,
   ``MIN. GREEN`` equals ``시간분할 − YELLOW(RED)`` on 59 of 94 (sheet, phase)
   pairs, and ``MIN. GREEN`` is a *green*, so the subtracted quantity was
   always the whole clearance.

   That answer settles WHICH quantity ``YELLOW(RED)`` is. It does **not**
   state the amber by itself — the sheet still prints one number for
   ``황색 + 전적색`` combined, never the two split apart — so the amber is
   still a number nobody but a reviewer can supply, and MRWA §5.4.2.1's
   "input from controller information, not changed" still applies to it.
   :func:`to_fixed_time_plan` therefore still refuses by default with
   ``daejeon_intergreen_split_unstated``, and still produces a plan only when
   a reviewer declares the amber explicitly through
   :attr:`DaejeonSettings.declared_amber_s` — **no amber is ever hardcoded as
   a default here**, answered client question or not. That is the guardrail
   doctrine's third tier applied where it bites: refused unless a human
   records the decision, never defaulted.

   Once an amber is declared, ``all_red_i = YELLOW(RED)_i − amber``. **All-red
   of exactly zero is an ordinary, valid design** — amber runs straight to
   red with no all-red display, which is common at an intersection with no
   all-red interval at all — and is accepted, not refused. Only a *negative*
   all-red (the declared amber longer than the sheet's own ``YELLOW(RED)``)
   refuses, as ``daejeon_all_red_negative``: that shape means the declaration
   contradicts what the sheet states, not that the design is unusual.

   A commonly cited convention for 황색시간 at a typical Korean urban
   approach is **3.0 s** (the police-standard figure practitioners declare
   when nothing more specific is available). That number is **not measured
   from any document in this repository** — no citation here claims
   otherwise — and is offered purely as the basis a reviewer may cite when
   declaring :attr:`DaejeonSettings.declared_amber_s`; it is never assigned
   automatically.

3. **The coordination offset's reference point.** ``연동`` is read and carried,
   but the sheet never says what instant it counts from, so the bridge places
   phase 1's green at cycle time 0 and warns
   (``daejeon_coordination_offset_not_applied``) with the value attached. An
   offset applied against the wrong reference is a corridor-wide progression
   error that Vissim will run without comment.


Confidentiality
---------------
``client-data/`` is confidential. This module contains no client value: every
number in this docstring's worked examples is a shape, the tests run against
synthetic fixtures under ``backend/tests/fixtures/daejeon_signal/``, and the CLI
probe (``backend/tools/genesis_signals_daejeon_probe.py``) prints aggregate
counts with a redacted sample.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..dataimport.base import AdapterError, AdapterInfo, ImportIssue, IssueSeverity
from ..dataimport.fixed_time import FixedTimePlan, PhaseDerivation
from ..dataimport.schema import (
    PhaseData,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalProgramData,
)

# ---------------------------------------------------------------------------
# Identity + citations
#
# The sheet ships with no specification document, exactly like the count export
# ``adapters/daejeon.py`` reads, so these strings name the OBSERVED structure and
# say so. Borrowing an MRWA page number for a Korean controller form would be the
# invented authority the adapter contract forbids.
# ---------------------------------------------------------------------------

DAEJEON_SIGNAL_ADAPTER = AdapterInfo(
    name="daejeon_signal_db",
    region="KR-Daejeon",
    source_system="교차로 운영 DATABASE",
    version="1.0.0",
    description=(
        "Reads the 대전 signal-controller operation sheet (교차로 운영 DATABASE) out of the "
        "text layer of its PDF. Produces neutral SignalControllerData; refuses to synthesise "
        "the yellow/all-red split the sheet does not state."
    ),
)

CIT_SHEET_OBSERVED = (
    "대전 교차로 운영 DATABASE sheet — layout observed across the 23-file client drop "
    "(2026-08-10). No vendor specification was supplied; every field mapping is observational."
)
CIT_NEVER_INVENT = (
    "docs/product/adapter-contract.md §1 — an adapter must never invent a value."
)
CIT_S5421_TIMINGS_UNCHANGED = (
    "MRWA Operational Modelling Guidelines §5.4.2.1 — signal timings (yellow, all-red) are "
    "input from controller/SCATS information and are not changed by the modeller."
)

#: The jurisdiction, declared once (adapter contract §2.6: a documented regional
#: fact is legitimate when it lives in one reviewable place).
REGION_JURISDICTION = "대전광역시"

# ---------------------------------------------------------------------------
# The form's own vocabulary
# ---------------------------------------------------------------------------

#: SIGNAL TABLE row labels, in printed order. Each is printed once for RING A and
#: once (on the following line) for RING B.
ROW_OPTION = "OPTION"
ROW_MIN_GREEN = "MIN. GREEN"
ROW_MAX_II = "MAX.Ⅱ"
ROW_YELLOW_RED = "YELLOW(RED)"
ROW_BEFORE_PED = "BEF. PED."
ROW_WALK = "WALK"
ROW_WALK_CLEAR = "WALK CLEAR"

SIGNAL_TABLE_ROWS: tuple[str, ...] = (
    ROW_OPTION,
    ROW_MIN_GREEN,
    ROW_MAX_II,
    ROW_YELLOW_RED,
    ROW_BEFORE_PED,
    ROW_WALK,
    ROW_WALK_CLEAR,
)

#: Rows without which the sheet is not usable at all. ``WALK``/``WALK CLEAR``/
#: ``BEF. PED.`` describe pedestrian staging and a vehicle-only intersection may
#: legitimately print them empty, so they are not required.
REQUIRED_SIGNAL_TABLE_ROWS: tuple[str, ...] = (ROW_MIN_GREEN, ROW_YELLOW_RED)

#: Weekday tokens in the 주간신호계획 block, Sunday-first as the sheet prints them.
WEEKDAYS: tuple[str, ...] = ("일", "월", "화", "수", "목", "금", "토")

#: How far (in layout characters) a value may sit from its column's header anchor
#: and still belong to it. Measured across the 23 real sheets: the largest
#: observed drift between a ``Φn`` header and the value under it is 1 character,
#: against an inter-column pitch of ~12, so 2 is a two-sided safety margin that
#: cannot reach the neighbouring column.
COLUMN_TOLERANCE = 2

_INT = re.compile(r"^\d+$")
_TOKEN = re.compile(r"\S+")
#: A ``시간분할(초)`` cell. Fields may be EMPTY — one real sheet prints
#: ``13,52,25,,20,40`` — and an empty field is preserved as ``None`` rather than
#: dropped, because dropping it shifts every value after it onto the wrong phase.
_SPLIT_VECTOR = re.compile(r"^\d{0,3}(?:,\d{0,3}){1,}$")
_CLOCK = re.compile(r"^\d{1,2}:\d{2}$")
_PHI = re.compile(r"Φ(\d)")
_RING_A_HDR = re.compile(r"\bA([1-9])\b")
_RING_B_HDR = re.compile(r"\bB([1-9])\b")
_MODEL = re.compile(r"\b([A-Z]{2,3}-\d{3,4}[A-Z]*(?:-R\d+)?)\b")
_REVISED = re.compile(
    r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(\d{1,2})\s*시\s*(\d{1,2})\s*분"
)
_NAME = re.compile(r"교차로명\s*:\s*(\S+)")
_NUMBER = re.compile(r"교차로번호\s*:\s*(\S+)")
_GROUP = re.compile(r"교차로군\s*:\s*(.*?)(?:\s{2,}작성년월|\s*$)")
#: The MEMO note that changes what the pedestrian rows add up to.
_MEMO_Y_INCLUDED = re.compile(r"보행신호\s*시간\s*Y\s*값\s*포함")

# ---------------------------------------------------------------------------
# Issue codes
# ---------------------------------------------------------------------------

#: Codes that make a sheet unusable: the parse returns a
#: :class:`DaejeonSheet` with ``controller is None``. Kept as data so the
#: docstring's "refuses and produces nothing" claim is testable.
REFUSE_CODES: frozenset[str] = frozenset(
    {
        "daejeon_sheet_not_recognised",
        "daejeon_intersection_number_missing",
        "daejeon_phase_columns_missing",
        "daejeon_signal_table_row_missing",
        "daejeon_value_off_column",
        "daejeon_plan_table_missing",
        "daejeon_split_rows_unpaired",
        "daejeon_split_pair_without_plan_id",
        "daejeon_split_length_disagrees_between_rings",
        "daejeon_split_count_mismatch",
        "daejeon_duplicate_level",
        "daejeon_duplicate_plan_id",
        "daejeon_level_without_cycle",
        "daejeon_tod_references_unknown_level",
        "daejeon_tod_references_unknown_plan",
        "daejeon_split_sum_mismatch",
        "daejeon_no_tod_entries",
    }
)

#: Codes the BRIDGE (:func:`to_fixed_time_plan`) can refuse with. Separate from
#: :data:`REFUSE_CODES` because a sheet that parses perfectly still refuses here:
#: the gap is in the document, not in the parse.
BRIDGE_REFUSE_CODES: frozenset[str] = frozenset(
    {
        "daejeon_intergreen_split_unstated",
        "daejeon_program_not_found",
        "daejeon_phase_yellow_unknown",
        "daejeon_all_red_negative",
        "daejeon_green_not_positive",
        "daejeon_ring_splits_disagree",
    }
)


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    *,
    messages: dict[str, str] | None = None,
    subject: str | None = None,
    provenance: Provenance | None = None,
    citation: str | None = None,
    context: dict | None = None,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        messages=messages or {},
        subject=subject,
        provenance=provenance,
        citation=citation,
        context=context or {},
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class DaejeonSettings(BaseModel):
    """Every number and every declaration this module judges by, in one place —
    the same shape as ``signals.SignalSettings`` and
    ``fixed_time.ConversionSettings``.

    :attr:`declared_amber_s` is the only field that can turn a refusal into a
    result, and it exists in exactly that shape on purpose. The client has
    confirmed the sheet's ``YELLOW(RED)`` column IS 황색+전적색 combined (see the
    module docstring), but the column still prints that combined number only —
    never the amber alone — so the amber itself remains a number nobody but a
    reviewer can supply. A reviewer who has asked the client, or who is
    applying a written national standard — 3.0 s is the commonly cited
    Korean police-standard 황색시간 for a typical urban approach, offered here
    as a basis a reviewer may cite, NOT a value read from any document in this
    repository and NEVER assigned automatically — records the amber here, and
    the refusal becomes an allowed-with-warning result carrying the
    declaration in its provenance. It is never defaulted to a number: no
    ``declared_amber_s`` means no plan, exactly as before the client answered.

    With an amber declared, ``all_red_i = YELLOW(RED)_i − declared_amber_s``.
    Exactly zero is accepted (an ordinary design with no all-red display);
    only a NEGATIVE all-red — the declared amber longer than what the sheet
    states — refuses, as ``daejeon_all_red_negative``.
    """

    model_config = ConfigDict(extra="forbid")

    declared_amber_s: float | None = Field(
        default=None,
        gt=0,
        description=(
            "A REVIEWER'S DECLARATION, not a default: the yellow display in seconds, when "
            "someone has established what the sheet's YELLOW(RED) column contains and — "
            "separately — what portion of it is amber. None (the default) means nobody has "
            "declared an amber, and to_fixed_time_plan refuses regardless of what the "
            "YELLOW(RED) column is now known to mean. This is never defaulted to 3.0 s or "
            "any other number by this module, however common a convention it is."
        ),
    )
    amber_declaration_note: str | None = Field(
        default=None,
        description=(
            "Why declared_amber_s is what it is — the client answer, the standard, the "
            "meeting. Travels into Provenance.derived_by (on every phase and on the program) "
            "so the decision is auditable later, and so a DECLARED plan is distinguishable "
            "from one whose amber was read straight from a source."
        ),
    )
    cycle_sum_tolerance_s: float = Field(
        default=0.0,
        ge=0,
        description=(
            "How far Σ(시간분할) may sit from the level's 주기 before the sheet is refused. "
            "Zero by default: both are printed whole seconds and every real sheet closes "
            "exactly, so a mismatch is a finding, not noise."
        ),
    )
    require_ring_split_agreement: bool = Field(
        default=True,
        description=(
            "A dual-ring controller may run different split vectors on RING A and RING B. "
            "The neutral program has one phase list, so the bridge refuses rather than "
            "picking a ring. Set False only with a recorded reason."
        ),
    )


DEFAULT_SETTINGS = DaejeonSettings()


# ---------------------------------------------------------------------------
# Parsed shapes — what the sheet literally says
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseTiming:
    """The seven SIGNAL TABLE rows for one phase, merged across the two rings.

    ``*_rings_disagree`` records the case the neutral model cannot express: a
    dual-ring controller that runs different numbers on ``A`` and ``B`` for the
    same phase column. The merged value is then ``None`` — not one of the two.
    """

    phase_no: int
    option: float | None = None
    min_green_s: float | None = None
    max_green_s: float | None = None
    yellow_red_s: float | None = None
    before_ped_s: float | None = None
    walk_s: float | None = None
    walk_clear_s: float | None = None
    rings_disagree: tuple[str, ...] = ()

    @property
    def ped_sequence_s(self) -> float | None:
        """``BEF. PED. + WALK + WALK CLEAR`` when all three are stated.

        Corroboration only. Across the real drop this sum equals the green on
        some phases and the whole phase time on others — the ``MEMO``'s
        ``Y값 포함`` note is exactly the difference — so it is never a source of
        a timing, only a cross-check that can raise a warning.
        """
        parts = (self.before_ped_s, self.walk_s, self.walk_clear_s)
        if any(p is None for p in parts):
            return None
        return float(sum(p for p in parts if p is not None))


@dataclass(frozen=True)
class LevelRow:
    """``수준`` → ``주기(초)``. One of the PLAN TABLE's two independent lookups."""

    level_no: int
    cycle_s: float
    line_no: int


@dataclass(frozen=True)
class PlanRow:
    """``ID`` → ``연동`` + ``시간분할(초)``. The PLAN TABLE's other lookup.

    ``splits_ring_a``/``splits_ring_b`` are the two printed rows of the
    ``시간분할(초)`` column, positional: entry *i* is phase Φ*i*, and ``None``
    means the cell was printed empty — that phase does not run in this plan.
    ``actuated_splits`` holds any further vectors printed under ``감응제어``'s
    sub-columns, keyed by the sub-column's own label — read and carried, never
    merged into the base plan.
    """

    plan_id: int
    offset_s: float | None
    splits_ring_a: tuple[float | None, ...]
    splits_ring_b: tuple[float | None, ...]
    actuated_splits: tuple[tuple[str, tuple[float | None, ...]], ...] = ()
    line_no: int = 0

    @property
    def rings_agree(self) -> bool:
        return self.splits_ring_a == self.splits_ring_b

    @property
    def has_empty_field(self) -> bool:
        return any(v is None for v in self.splits_ring_a) or any(
            v is None for v in self.splits_ring_b
        )


@dataclass(frozen=True)
class TodEntry:
    """One ``TOD PLAN n`` row: at ``clock``, run ``level_no`` with ``plan_id``."""

    tod_plan_no: int
    index: int
    clock: str
    level_no: int
    plan_id: int
    line_no: int


@dataclass(frozen=True)
class DayAssignment:
    """One ``주간신호계획`` row: a weekday runs ``tod_plan_no``/``time_plan_no``."""

    weekday: str
    tod_plan_no: int
    time_plan_no: int


@dataclass(frozen=True)
class DaejeonSheet:
    """One parsed ``교차로 운영 DATABASE`` sheet.

    ``controller`` is ``None`` exactly when a REFUSE issue is present — the
    module produces no partial controller, the same contract ``FixedTimePlan``
    and ``NodeSignals`` keep. Everything read before the refusal is still on the
    dataclass, because a reviewer looking at a refusal wants to see how far the
    parse got.
    """

    source_file: str
    intersection_name: str | None
    intersection_no: str | None
    group_raw: str | None
    controller_model: str | None
    revised_at_raw: str | None
    phase_count: int
    phases: tuple[PhaseTiming, ...]
    levels: tuple[LevelRow, ...]
    plans: tuple[PlanRow, ...]
    tod_entries: tuple[TodEntry, ...]
    day_assignments: tuple[DayAssignment, ...]
    memo_notes: tuple[str, ...]
    controller: SignalControllerData | None
    issues: tuple[ImportIssue, ...]

    @property
    def refusals(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.REFUSE)

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.WARN)

    @property
    def infos(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.INFO)

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def fully_parsed(self) -> bool:
        """Parsed with nothing missing: a controller, at least one program, and
        no warnings about a field the sheet failed to yield."""
        return (
            self.ok
            and self.controller is not None
            and bool(self.controller.programs)
            and not any(i.code in PARTIAL_CODES for i in self.warnings)
        )

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [i.code for i in self.issues if severity is None or i.severity is severity]

    def level_cycle_s(self, level_no: int) -> float | None:
        for level in self.levels:
            if level.level_no == level_no:
                return level.cycle_s
        return None

    def plan(self, plan_id: int) -> PlanRow | None:
        for row in self.plans:
            if row.plan_id == plan_id:
                return row
        return None

    def phase(self, phase_no: int) -> PhaseTiming | None:
        for timing in self.phases:
            if timing.phase_no == phase_no:
                return timing
        return None


#: WARN codes that mean "parsed, but a field the sheet should carry did not come
#: out". ``fully_parsed`` is false when any of these fired: the sheet is usable
#: but a named piece is missing, which is the difference between the "fully" and
#: "partially" columns of the coverage table.
PARTIAL_CODES: frozenset[str] = frozenset(
    {
        "daejeon_ring_values_disagree",
        "daejeon_phase_yellow_missing",
        "daejeon_plan_offset_missing",
        "daejeon_actuated_split_variant_present",
        "daejeon_memo_alters_pedestrian_timing",
        "daejeon_group_not_in_sa_form",
    }
)


# ---------------------------------------------------------------------------
# Layout helpers — the character-grid mechanics
# ---------------------------------------------------------------------------


def _tokens(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group()) for m in _TOKEN.finditer(line)]


def _nearest_column(start: int, anchors: Mapping[int, int]) -> int | None:
    """Which column a token at character ``start`` belongs to.

    ``anchors`` maps column key → the character index of that column's header.
    Returns the key whose anchor is within :data:`COLUMN_TOLERANCE`, or ``None``
    when the token sits between columns — which is a finding, not something to
    round away.
    """
    best_key: int | None = None
    best_gap = COLUMN_TOLERANCE + 1
    for key, anchor in anchors.items():
        gap = abs(start - anchor)
        if gap < best_gap:
            best_gap, best_key = gap, key
    return best_key if best_gap <= COLUMN_TOLERANCE else None


def _segment_of(start: int, ordered_anchors: Sequence[tuple[str, int]]) -> str | None:
    """Which named segment a token at ``start`` falls in.

    Segment ``k`` runs from ``anchor_k - COLUMN_TOLERANCE`` up to
    ``anchor_{k+1} - COLUMN_TOLERANCE``. Used for the PLAN TABLE, whose columns
    are left-aligned labels with right-aligned numbers under them, so a value may
    overhang its header by a character or two but never reaches the next column.
    """
    chosen: str | None = None
    for name, anchor in ordered_anchors:
        if start >= anchor - COLUMN_TOLERANCE:
            chosen = name
        else:
            break
    return chosen


def _as_float(token: str) -> float | None:
    return float(token) if _INT.match(token) else None


def _split_vector(token: str) -> tuple[float | None, ...] | None:
    """A ``시간분할`` cell as a positional vector, empties preserved as ``None``.

    Position IS identity here: field *i* is phase Φ*i*. ``13,52,25,,20,40`` is
    six positions with the fourth unrun, and compacting it to five values would
    move 20 s of green from Φ5 to Φ4 — a wrong number Vissim would run happily.
    """
    if not _SPLIT_VECTOR.match(token):
        return None
    parts = token.split(",")
    if sum(1 for p in parts if p) < 2:
        return None
    return tuple(float(p) if p else None for p in parts)


def _vector_sum(vec: Sequence[float | None]) -> float:
    return float(sum(v for v in vec if v is not None))


# ---------------------------------------------------------------------------
# PDF text extraction — the ONLY place pypdf is touched
# ---------------------------------------------------------------------------


def read_sheet_text(pdf: bytes | str | Path) -> str:
    """The sheet's text layer, as an aligned character grid.

    ``extraction_mode="layout"`` is not a nicety here: the form is a table with
    legitimately blank cells, and only the layout extractor preserves the
    horizontal position that says which phase column a value belongs to. Plain
    reading-order extraction returns the same tokens in an order that has no
    stable relationship to the grid, which is how a parser ends up confidently
    attributing phase 4's yellow to phase 3.

    Raises :class:`AdapterError` for an input that cannot be read at all — a
    missing file, a corrupt PDF, or an environment without ``pypdf``. Everything
    a data reviewer could act on is an ``ImportIssue`` instead.
    """
    try:
        from pypdf import PdfReader  # imported lazily: text fixtures need no PDF stack
    except ImportError as exc:  # pragma: no cover - environment guard
        raise AdapterError(
            "pypdf is required to read a 대전 signal sheet PDF (backend/requirements.txt). "
            "parse_sheet_text() takes text and needs no PDF library."
        ) from exc

    import io

    try:
        source: object
        if isinstance(pdf, bytes):
            source = io.BytesIO(pdf)
        else:
            source = str(pdf)
        reader = PdfReader(source)  # type: ignore[arg-type]
        return "\n".join(
            (page.extract_text(extraction_mode="layout") or "") for page in reader.pages
        )
    except AdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - any pypdf failure is an unreadable input
        raise AdapterError(f"could not read the PDF text layer: {type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------


def parse_sheet_text(text: str, *, source_file: str) -> DaejeonSheet:
    """Parse one sheet's extracted text. Returns a :class:`DaejeonSheet` always.

    "Report, do not raise" — the adapter contract's second obligation. A sheet
    this module cannot use comes back with ``controller is None`` and REFUSE
    issues naming the file and the line, never as an exception and never as a
    half-filled controller.
    """
    lines = text.splitlines()
    issues: list[ImportIssue] = []
    subject = source_file

    def _bail(
        *,
        name: str | None = None,
        number: str | None = None,
        group: str | None = None,
        model: str | None = None,
        revised: str | None = None,
        phases: tuple[PhaseTiming, ...] = (),
        levels: tuple[LevelRow, ...] = (),
        plans: tuple[PlanRow, ...] = (),
        tod: tuple[TodEntry, ...] = (),
        days: tuple[DayAssignment, ...] = (),
        memo: tuple[str, ...] = (),
        phase_count: int = 0,
    ) -> DaejeonSheet:
        return DaejeonSheet(
            source_file=source_file,
            intersection_name=name,
            intersection_no=number,
            group_raw=group,
            controller_model=model,
            revised_at_raw=revised,
            phase_count=phase_count,
            phases=phases,
            levels=levels,
            plans=plans,
            tod_entries=tod,
            day_assignments=days,
            memo_notes=memo,
            controller=None,
            issues=tuple(issues),
        )

    # -- 1. is this the form we think it is? ------------------------------
    header_idx = next(
        (i for i, ln in enumerate(lines) if "D A T A B A S E" in ln and "운" in ln), None
    )
    signal_table_idx = next((i for i, ln in enumerate(lines) if "SIGNAL TABLE" in ln), None)
    if header_idx is None or signal_table_idx is None:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_sheet_not_recognised",
                f"{source_file}: no '교 차 로 운 영 D A T A B A S E' header and/or no "
                "'SIGNAL TABLE' section was found in the extracted text. This parser reads one "
                "specific Korean controller form and will not attempt a different document.",
                messages={
                    "ko": f"{source_file}: '교차로 운영 DATABASE' 머리글 또는 'SIGNAL TABLE' "
                    "구역을 찾지 못했습니다. 이 파서는 해당 양식만 읽습니다.",
                    "en": f"{source_file}: the 교차로 운영 DATABASE header and/or the SIGNAL "
                    "TABLE section is absent; this parser reads only that form.",
                    "ja": f"{source_file}: 교차로 운영 DATABASE ヘッダまたは SIGNAL TABLE が "
                    "見つかりません。このパーサはその様式のみを読みます。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
                context={"has_header": header_idx is not None,
                         "has_signal_table": signal_table_idx is not None},
            )
        )
        return _bail()

    header_line = lines[header_idx]
    model_match = _MODEL.search(header_line)
    model = model_match.group(1) if model_match else None
    revised_match = _REVISED.search(header_line)
    revised = (
        "-".join(revised_match.groups()[:3]) + " " + ":".join(revised_match.groups()[3:])
        if revised_match
        else None
    )

    # -- 2. identity ------------------------------------------------------
    ident_idx = next((i for i, ln in enumerate(lines) if "교차로명" in ln), None)
    name = number = group_raw = None
    if ident_idx is not None:
        ident = lines[ident_idx]
        if (m := _NAME.search(ident)) is not None:
            name = m.group(1)
        if (m := _NUMBER.search(ident)) is not None:
            number = m.group(1)
        if (m := _GROUP.search(ident)) is not None:
            group_raw = m.group(1).strip() or None
    if not number:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_intersection_number_missing",
                f"{source_file}: the sheet states no 교차로번호. Every neutral record is keyed "
                "by the controller id, and a controller with an id we made up cannot be "
                "reconciled against the client's own inventory later.",
                messages={
                    "ko": f"{source_file}: 교차로번호가 없습니다. 제어기 ID를 임의로 만들지 "
                    "않습니다 — 발주처 대장과 대조할 수 없게 됩니다.",
                    "en": f"{source_file}: no 교차로번호 is stated; a made-up controller id "
                    "cannot be reconciled against the client's inventory.",
                    "ja": f"{source_file}: 교차로번호 がありません。制御機 ID を作り出しません。",
                },
                subject=subject,
                citation=CIT_NEVER_INVENT,
            )
        )
        return _bail(name=name, group=group_raw, model=model, revised=revised)

    subject = f"{source_file} / 교차로 {number}"
    if group_raw and not group_raw.upper().startswith("SA"):
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "daejeon_group_not_in_sa_form",
                f"교차로군 reads {group_raw!r}, not the 'SA nn' form the other sheets use. It is "
                "carried verbatim; whether it names the same kind of subsystem is a question for "
                "the client, not something to normalise here.",
                messages={
                    "ko": f"교차로군이 다른 시트의 'SA nn' 형식이 아닌 {group_raw!r}입니다. "
                    "원문 그대로 보존하며 임의로 정규화하지 않습니다.",
                    "en": f"교차로군 reads {group_raw!r}, not the 'SA nn' form; kept verbatim "
                    "rather than normalised.",
                    "ja": f"교차로군 が他シートの 'SA nn' 形式ではなく {group_raw!r} です。"
                    "原文のまま保持します。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
                context={"group_raw": group_raw},
            )
        )

    # -- 3. phase columns -------------------------------------------------
    phi_idx = next((i for i, ln in enumerate(lines) if _PHI.search(ln)), None)
    if phi_idx is None:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_phase_columns_missing",
                f"{source_file}: no Φ1…Φn header row. Every SIGNAL TABLE value is attributed to "
                "a phase by its column position, so with no column header there is nothing to "
                "attribute values to and the alternative — attributing by order — silently "
                "misassigns every row that contains a blank cell.",
                messages={
                    "ko": f"{source_file}: Φ1…Φn 열 머리글이 없습니다. 값은 열 위치로 현시에 "
                    "귀속되므로, 순서로 배정하면 빈칸이 있는 모든 행이 잘못 배정됩니다.",
                    "en": f"{source_file}: no Φ1…Φn header row; values are attributed by column "
                    "position and attributing by order misassigns every row with a blank cell.",
                    "ja": f"{source_file}: Φ1…Φn の列見出しがありません。値は列位置で現示に "
                    "帰属するため、順序による割当は空白セルのある行をすべて誤配置します。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
            )
        )
        return _bail(name=name, number=number, group=group_raw, model=model, revised=revised)

    phase_columns: dict[int, int] = {
        int(m.group(1)): m.start() for m in _PHI.finditer(lines[phi_idx])
    }

    # The A1…An / B1…Bn header rows are the same grid printed twice. They are a
    # free cross-check on the Φ anchors: if the three disagree the extraction
    # drifted and every value below is suspect.
    for pattern, label in ((_RING_A_HDR, "A"), (_RING_B_HDR, "B")):
        row = next(
            (
                ln
                for ln in lines[phi_idx: signal_table_idx + 1]
                if len(pattern.findall(ln)) >= max(2, len(phase_columns) - 1)
            ),
            None,
        )
        if row is None:
            continue
        for m in pattern.finditer(row):
            col = phase_columns.get(int(m.group(1)))
            if col is not None and abs(m.start() - col) > COLUMN_TOLERANCE:
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "daejeon_value_off_column",
                        f"{source_file}: ring header {label}{m.group(1)} sits {m.start() - col} "
                        f"characters from the Φ{m.group(1)} column. The grid the values are read "
                        "off has drifted, so no value below can be trusted to its phase.",
                        messages={
                            "ko": f"{source_file}: 링 머리글 {label}{m.group(1)}이 Φ 열에서 "
                            "벗어났습니다. 격자가 어긋나 아래 값들의 현시 귀속을 신뢰할 수 "
                            "없습니다.",
                            "en": f"{source_file}: ring header {label}{m.group(1)} is off its Φ "
                            "column; the grid has drifted and no value below is trustworthy.",
                            "ja": f"{source_file}: リング見出し {label}{m.group(1)} が Φ 列から "
                            "ずれています。格子が崩れており以下の値は信頼できません。",
                        },
                        subject=subject,
                        citation=CIT_SHEET_OBSERVED,
                        context={"header": f"{label}{m.group(1)}", "found_at": m.start(),
                                 "expected_at": col},
                    )
                )
                return _bail(
                    name=name, number=number, group=group_raw, model=model, revised=revised
                )

    # -- 4. SIGNAL TABLE --------------------------------------------------
    cells, table_issues, missing_rows = _read_signal_table(
        lines, signal_table_idx, phase_columns, source_file=source_file, subject=subject
    )
    issues.extend(table_issues)
    if missing_rows or any(i.severity is IssueSeverity.REFUSE for i in table_issues):
        if missing_rows:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_signal_table_row_missing",
                    f"{source_file}: the SIGNAL TABLE has no {sorted(missing_rows)} row. "
                    "MIN. GREEN and YELLOW(RED) are the two rows every downstream timing check "
                    "reads; a sheet without them states no clearance at all.",
                    messages={
                        "ko": f"{source_file}: SIGNAL TABLE에 {sorted(missing_rows)} 행이 "
                        "없습니다. 소거시간을 전혀 명시하지 않은 시트입니다.",
                        "en": f"{source_file}: the SIGNAL TABLE lacks the {sorted(missing_rows)} "
                        "row(s); the sheet states no clearance at all.",
                        "ja": f"{source_file}: SIGNAL TABLE に {sorted(missing_rows)} 行が "
                        "ありません。クリアランス時間の記載がありません。",
                    },
                    subject=subject,
                    citation=CIT_SHEET_OBSERVED,
                    context={"missing_rows": sorted(missing_rows)},
                )
            )
        return _bail(name=name, number=number, group=group_raw, model=model, revised=revised)

    phases, phase_issues = _merge_rings(cells, subject=subject)
    issues.extend(phase_issues)
    phase_count = len(phases)

    # -- 5. PLAN TABLE ----------------------------------------------------
    plan_idx = next(
        (i for i, ln in enumerate(lines) if "수준" in ln and "시간분할" in ln), None
    )
    if plan_idx is None:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_plan_table_missing",
                f"{source_file}: no PLAN TABLE header ('수준 … 시간분할(초)'). Without it the "
                "sheet states no cycle and no split, which is everything a fixed-time program is.",
                messages={
                    "ko": f"{source_file}: PLAN TABLE 머리글('수준 … 시간분할(초)')이 없습니다. "
                    "주기와 시간분할이 없으면 고정주기 프로그램이 성립하지 않습니다.",
                    "en": f"{source_file}: no PLAN TABLE header; the sheet states no cycle and "
                    "no split, which is everything a fixed-time program is.",
                    "ja": f"{source_file}: PLAN TABLE の見出しがありません。周期と時間分割が "
                    "なければ固定時間プログラムは成立しません。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
            )
        )
        return _bail(
            name=name, number=number, group=group_raw, model=model, revised=revised,
            phases=phases, phase_count=phase_count,
        )

    levels, plans, tod_entries, plan_issues = _read_plan_table(
        lines, plan_idx, source_file=source_file, subject=subject
    )
    issues.extend(plan_issues)
    days = _read_day_assignments(lines)
    memo = _read_memo_notes(lines)
    if memo:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "daejeon_memo_alters_pedestrian_timing",
                f"{source_file}: the MEMO states the pedestrian time includes the Y (yellow) "
                f"value for some phases ({len(memo)} note(s)). BEF. PED. + WALK + WALK CLEAR "
                "therefore sums to the phase time on those phases and to the green on the "
                "others, so the pedestrian rows are not a usable cross-check here and are not "
                "used as one.",
                messages={
                    "ko": "MEMO에 일부 현시의 보행시간이 황색(Y)을 포함한다고 적혀 있습니다. "
                    "보행 행 합계를 녹색시간 교차검증으로 사용하지 않습니다.",
                    "en": "The MEMO states the pedestrian time includes the yellow for some "
                    "phases, so the pedestrian rows are not used as a green cross-check.",
                    "ja": "MEMO に一部現示の歩行時間が黄色(Y)を含むと記載されています。歩行行の "
                    "合計は緑時間の相互検証には使用しません。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
                context={"note_count": len(memo)},
            )
        )

    if any(i.severity is IssueSeverity.REFUSE for i in plan_issues):
        return _bail(
            name=name, number=number, group=group_raw, model=model, revised=revised,
            phases=phases, levels=levels, plans=plans, tod=tod_entries, days=days,
            memo=memo, phase_count=phase_count,
        )

    # -- 6. cross-checks, on the pairs the sheet actually uses -------------
    issues.extend(
        _cross_check(
            levels, plans, tod_entries, phase_count,
            source_file=source_file, subject=subject,
        )
    )
    if any(i.severity is IssueSeverity.REFUSE for i in issues):
        return _bail(
            name=name, number=number, group=group_raw, model=model, revised=revised,
            phases=phases, levels=levels, plans=plans, tod=tod_entries, days=days,
            memo=memo, phase_count=phase_count,
        )

    # -- 7. the neutral controller ---------------------------------------
    controller, build_issues = _build_controller(
        source_file=source_file,
        subject=subject,
        number=number,
        name=name,
        group_raw=group_raw,
        model=model,
        revised=revised,
        phases=phases,
        levels=levels,
        plans=plans,
        tod_entries=tod_entries,
    )
    issues.extend(build_issues)

    issues.append(
        _issue(
            IssueSeverity.INFO,
            "daejeon_phase_movement_diagram_not_text",
            "The 현시도 (which approach moves in which phase) is a drawing in the sheet's "
            "top-left corner and is NOT in the text layer. signals.generate_node_signals already "
            "requires that mapping as caller input (approach_phases) and refuses without it; "
            "this parser does not supply it and does not guess it.",
            messages={
                "ko": "현시도(어느 접근로가 어느 현시에 진행하는지)는 도면이며 텍스트 계층에 "
                "없습니다. 현시–접근로 배정은 호출자가 제공해야 하며 추측하지 않습니다.",
                "en": "The 현시도 (approach→phase mapping) is a drawing, not text; it stays "
                "caller-supplied input and is never guessed here.",
                "ja": "現示図(どの流入路がどの現示で進行するか)は図面でありテキスト層に "
                "ありません。現示と流入路の対応は呼び出し側の入力であり推測しません。",
            },
            subject=subject,
            citation=CIT_SHEET_OBSERVED,
        )
    )

    return DaejeonSheet(
        source_file=source_file,
        intersection_name=name,
        intersection_no=number,
        group_raw=group_raw,
        controller_model=model,
        revised_at_raw=revised,
        phase_count=phase_count,
        phases=phases,
        levels=levels,
        plans=plans,
        tod_entries=tod_entries,
        day_assignments=days,
        memo_notes=memo,
        controller=controller,
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------
# SIGNAL TABLE
# ---------------------------------------------------------------------------


def _read_signal_table(
    lines: Sequence[str],
    signal_table_idx: int,
    phase_columns: Mapping[int, int],
    *,
    source_file: str,
    subject: str,
) -> tuple[dict[tuple[str, str, int], float | None], list[ImportIssue], set[str]]:
    """Read the seven rows × two rings × N phase columns.

    The label line carries RING A and the line immediately after it carries
    RING B — that is how the form prints it, verified on all 23 real sheets and
    the reason ``WALK`` is searched for as a whole-line label rather than a
    substring (``WALK CLEAR`` contains it).
    """
    issues: list[ImportIssue] = []
    cells: dict[tuple[str, str, int], float | None] = {}
    missing: set[str] = set()
    first_col = min(phase_columns.values())
    last_col = max(phase_columns.values())

    for label in SIGNAL_TABLE_ROWS:
        idx = _find_row(lines, label, signal_table_idx)
        if idx is None:
            if label in REQUIRED_SIGNAL_TABLE_ROWS:
                missing.add(label)
            continue
        for ring, line in (("A", lines[idx]), ("B", lines[idx + 1] if idx + 1 < len(lines) else "")):
            for start, token in _tokens(line):
                if start < first_col - COLUMN_TOLERANCE:
                    continue  # the row label itself
                if start > last_col + COLUMN_TOLERANCE:
                    continue  # margin annotations printed right of Φ6
                value = _as_float(token)
                if value is None:
                    continue
                phase_no = _nearest_column(start, phase_columns)
                if phase_no is None:
                    issues.append(
                        _issue(
                            IssueSeverity.REFUSE,
                            "daejeon_value_off_column",
                            f"{source_file} line {idx + 1} ({label}, RING {ring}): the value "
                            f"{token!r} at character {start} is not within "
                            f"{COLUMN_TOLERANCE} characters of any Φ column "
                            f"{sorted(phase_columns.items())}. Assigning it to the nearest "
                            "column anyway is exactly the guess this parser refuses to make.",
                            messages={
                                "ko": f"{source_file} {idx + 1}행 ({label}, RING {ring}): 값이 "
                                "어떤 Φ 열에도 정렬되지 않습니다. 가장 가까운 열에 임의 배정하지 "
                                "않습니다.",
                                "en": f"{source_file} line {idx + 1} ({label}, RING {ring}): the "
                                "value aligns with no Φ column and is not assigned to the "
                                "nearest one.",
                                "ja": f"{source_file} {idx + 1}行 ({label}, RING {ring}): 値が "
                                "どの Φ 列にも整列していません。最も近い列に割り当てません。",
                            },
                            subject=subject,
                            citation=CIT_SHEET_OBSERVED,
                            context={"row": label, "ring": ring, "char": start, "line": idx + 1},
                        )
                    )
                    continue
                cells[(label, ring, phase_no)] = value

    return cells, issues, missing


def _find_row(lines: Sequence[str], label: str, from_idx: int) -> int | None:
    """Index of the RING A line for ``label``.

    ``WALK`` is a prefix of ``WALK CLEAR``, so the search is for the label as a
    standalone token run rather than a substring; and it starts at the SIGNAL
    TABLE marker so a stray occurrence in the drawing area cannot win.
    """
    pattern = re.compile(rf"(?<!\S){re.escape(label)}(?!\S)")
    for i in range(from_idx, len(lines)):
        if pattern.search(lines[i]):
            return i
    return None


def _merge_rings(
    cells: Mapping[tuple[str, str, int], float | None], *, subject: str
) -> tuple[tuple[PhaseTiming, ...], list[ImportIssue]]:
    """Collapse the two rings into one per-phase record, or refuse to.

    The neutral :class:`PhaseData` has no ring axis. Where the two rings state
    the same number, that number is the phase's. Where only one ring states it,
    that is the only statement the sheet makes and it is taken (with the phase
    named in an INFO). Where the two rings state DIFFERENT numbers the sheet is
    describing a dual-ring controller the neutral model cannot express: the
    merged value becomes ``None`` and the disagreement is recorded, because
    picking ring A would be inventing a controller that is not the one installed.
    """
    issues: list[ImportIssue] = []
    phase_nos = sorted({phase for (_, _, phase) in cells})
    timings: list[PhaseTiming] = []
    single_ring: list[str] = []

    field_for_row = {
        ROW_OPTION: "option",
        ROW_MIN_GREEN: "min_green_s",
        ROW_MAX_II: "max_green_s",
        ROW_YELLOW_RED: "yellow_red_s",
        ROW_BEFORE_PED: "before_ped_s",
        ROW_WALK: "walk_s",
        ROW_WALK_CLEAR: "walk_clear_s",
    }

    for phase_no in phase_nos:
        values: dict[str, float | None] = {}
        disagree: list[str] = []
        for row, attr in field_for_row.items():
            a = cells.get((row, "A", phase_no))
            b = cells.get((row, "B", phase_no))
            if a is None and b is None:
                values[attr] = None
            elif a is None or b is None:
                values[attr] = a if a is not None else b
                single_ring.append(f"Φ{phase_no}/{row}")
            elif a == b:
                values[attr] = a
            else:
                values[attr] = None
                disagree.append(row)
        timings.append(
            PhaseTiming(phase_no=phase_no, rings_disagree=tuple(disagree), **values)
        )
        if disagree:
            issues.append(
                _issue(
                    IssueSeverity.WARN,
                    "daejeon_ring_values_disagree",
                    f"Φ{phase_no}: RING A and RING B state different {disagree}. This is a "
                    "dual-ring controller running different timings per ring, and the neutral "
                    "program has one phase list — so the merged value is left unstated rather "
                    "than silently taken from ring A.",
                    messages={
                        "ko": f"Φ{phase_no}: RING A와 RING B의 {disagree} 값이 다릅니다. 링별로 "
                        "다른 제어이며, 중립 모델에는 링 축이 없어 임의로 A를 택하지 않고 "
                        "미기재로 둡니다.",
                        "en": f"Φ{phase_no}: rings A and B state different {disagree}; the "
                        "neutral model has no ring axis, so the value is left unstated rather "
                        "than taken from ring A.",
                        "ja": f"Φ{phase_no}: リング A と B で {disagree} が異なります。中立モデル "
                        "にリング軸がないため、A を選ばず未記載とします。",
                    },
                    subject=subject,
                    citation=CIT_SHEET_OBSERVED,
                    context={"phase": phase_no, "rows": disagree},
                )
            )
        if values.get("yellow_red_s") is None:
            issues.append(
                _issue(
                    IssueSeverity.WARN,
                    "daejeon_phase_yellow_missing",
                    f"Φ{phase_no}: no usable YELLOW(RED) value. Any program using this phase "
                    "cannot state a clearance, and the bridge to a Vissim signal program will "
                    "refuse rather than write a default amber.",
                    messages={
                        "ko": f"Φ{phase_no}: 사용 가능한 YELLOW(RED) 값이 없습니다. 기본 황색을 "
                        "지어내지 않고 거부합니다.",
                        "en": f"Φ{phase_no}: no usable YELLOW(RED); no default amber is invented.",
                        "ja": f"Φ{phase_no}: 使用可能な YELLOW(RED) がありません。既定の黄色は "
                        "作りません。",
                    },
                    subject=subject,
                    citation=CIT_S5421_TIMINGS_UNCHANGED,
                    context={"phase": phase_no},
                )
            )

    if single_ring:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "daejeon_value_from_single_ring",
                f"{len(single_ring)} cell(s) are stated by one ring only "
                f"({sorted(single_ring)[:6]}…). The stated value is the only statement the sheet "
                "makes about that phase and is used as-is; the blank is not read as a zero.",
                messages={
                    "ko": "일부 셀은 한쪽 링에만 값이 있습니다. 해당 값을 그대로 사용하며 "
                    "빈칸을 0으로 읽지 않습니다.",
                    "en": "Some cells are stated by one ring only; the stated value is used and "
                    "the blank is not read as a zero.",
                    "ja": "一部のセルは片方のリングにのみ値があります。空白は 0 とは読みません。",
                },
                subject=subject,
                context={"count": len(single_ring)},
            )
        )
    return tuple(timings), issues


# ---------------------------------------------------------------------------
# PLAN TABLE
# ---------------------------------------------------------------------------


def _plan_anchors(header: str, sub_header: str) -> tuple[list[tuple[str, int]], list[str]]:
    """Ordered ``(segment name, character anchor)`` for the PLAN TABLE grid.

    Built from the printed headers of the sheet in hand rather than from
    hard-coded columns: the six controller firmwares print the same grid at
    slightly different character offsets, and pinning absolute columns would
    make the parser work on one firmware and misread another.
    """
    anchors: list[tuple[str, int]] = []
    actuated: list[str] = []

    def add(name: str, at: int | None) -> None:
        if at is not None and at >= 0:
            anchors.append((name, at))

    add("level", header.find("수준"))
    add("cycle", header.find("주기"))
    add("plan_id", header.find("ID"))
    add("offset", header.find("연"))
    split_at = sub_header.find("RING A. B")
    if split_at < 0:
        split_at = sub_header.find("RING A")
    if split_at < 0:
        split_at = header.find("시간분할")
    add("split", split_at)
    for label in ("압버튼", "검지기(○)", "검지기(×)"):
        at = sub_header.find(label)
        if at >= 0:
            anchors.append((f"actuated:{label}", at))
            actuated.append(label)
    add("tod_index", header.find("구분"))

    clocks = [m.start() for m in re.finditer("시각", header)]
    # The first 수준/ID anchors belong to the left-hand plan table; the rest are
    # the TOD PLAN 1/2/3 sub-columns, in printed order.
    levels = [m.start() for m in re.finditer("수준", header)][1:]
    ids = [m.start() for m in re.finditer(r"\bID\b", header)][1:]
    for n, clock_at in enumerate(clocks, start=1):
        anchors.append((f"tod{n}_clock", clock_at))
        if n - 1 < len(levels):
            anchors.append((f"tod{n}_level", levels[n - 1]))
        if n - 1 < len(ids):
            anchors.append((f"tod{n}_plan", ids[n - 1]))
    add("reserve_start", header.find("시작"))
    add("reserve_end", header.find("종료"))
    add("reserve_days", header.find("요일/점멸"))

    anchors.sort(key=lambda pair: pair[1])
    return anchors, actuated


def _read_plan_table(
    lines: Sequence[str],
    plan_idx: int,
    *,
    source_file: str,
    subject: str,
) -> tuple[
    tuple[LevelRow, ...], tuple[PlanRow, ...], tuple[TodEntry, ...], list[ImportIssue]
]:
    issues: list[ImportIssue] = []
    header = lines[plan_idx]
    sub_header = lines[plan_idx + 1] if plan_idx + 1 < len(lines) else ""
    anchors, _actuated_labels = _plan_anchors(header, sub_header)

    level_rows: list[LevelRow] = []
    plan_id_rows: list[tuple[int, int, float | None]] = []  # (line, plan_id, offset)
    split_rows: list[
        tuple[int, tuple[float | None, ...], tuple[tuple[str, tuple[float | None, ...]], ...]]
    ] = []
    tod_entries: list[TodEntry] = []
    seen_levels: set[int] = set()
    seen_plan_ids: set[int] = set()
    empty_level_slots: list[int] = []

    for i in range(plan_idx + 2, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        by_segment: dict[str, list[tuple[int, str]]] = {}
        for start, token in _tokens(line):
            seg = _segment_of(start, anchors)
            if seg is None:
                continue
            by_segment.setdefault(seg, []).append((start, token))

        # -- 수준 → 주기 -------------------------------------------------
        level_token = _first_int(by_segment.get("level"))
        cycle_token = _first_int(by_segment.get("cycle"))
        if level_token is not None:
            if cycle_token is None:
                # The form prints all six 수준 slots whether or not the site uses
                # them, so an empty slot is normal and is NOT a finding on its
                # own. It only matters if a TOD row selects it, and
                # daejeon_tod_references_unknown_level is the check that says so.
                empty_level_slots.append(level_token)
            elif level_token in seen_levels:
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "daejeon_duplicate_level",
                        f"{source_file} line {i + 1}: 수준 {level_token} appears twice. A level "
                        "is a key into the cycle table; two rows means one of the two cycles is "
                        "wrong and nothing here can tell which.",
                        messages={
                            "ko": f"{source_file} {i + 1}행: 수준 {level_token}이 두 번 "
                            "나타납니다. 어느 주기가 맞는지 판단할 근거가 없습니다.",
                            "en": f"{source_file} line {i + 1}: 수준 {level_token} appears "
                            "twice and nothing here can tell which cycle is correct.",
                            "ja": f"{source_file} {i + 1}行: 수준 {level_token} が二度現れます。"
                            "どちらの周期が正しいか判断できません。",
                        },
                        subject=subject,
                        context={"level": level_token, "line": i + 1},
                    )
                )
            else:
                seen_levels.add(level_token)
                level_rows.append(
                    LevelRow(level_no=level_token, cycle_s=float(cycle_token), line_no=i + 1)
                )

        # -- ID → 연동 ---------------------------------------------------
        plan_token = _first_int(by_segment.get("plan_id"))
        if plan_token is not None:
            if plan_token in seen_plan_ids:
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "daejeon_duplicate_plan_id",
                        f"{source_file} line {i + 1}: plan ID {plan_token} appears twice.",
                        messages={
                            "ko": f"{source_file} {i + 1}행: 계획 ID {plan_token}이 중복입니다.",
                            "en": f"{source_file} line {i + 1}: plan ID {plan_token} is duplicated.",
                            "ja": f"{source_file} {i + 1}行: 計画 ID {plan_token} が重複します。",
                        },
                        subject=subject,
                        context={"plan_id": plan_token, "line": i + 1},
                    )
                )
            else:
                seen_plan_ids.add(plan_token)
                offset = _first_int(by_segment.get("offset"))
                plan_id_rows.append(
                    (i, plan_token, float(offset) if offset is not None else None)
                )

        # -- 시간분할 ----------------------------------------------------
        base = _first_split(by_segment.get("split"))
        if base is not None:
            variants: list[tuple[str, tuple[float, ...]]] = []
            for seg, items in by_segment.items():
                if not seg.startswith("actuated:"):
                    continue
                vec = _first_split(items)
                if vec is not None:
                    variants.append((seg.split(":", 1)[1], vec))
            split_rows.append((i, base, tuple(sorted(variants))))

        # -- TOD PLAN n --------------------------------------------------
        index = _first_int(by_segment.get("tod_index"))
        for n in (1, 2, 3):
            clock_items = by_segment.get(f"tod{n}_clock")
            clock = next(
                (tok for _, tok in clock_items or [] if _CLOCK.match(tok)), None
            )
            if clock is None:
                continue
            level_ref = _first_int(by_segment.get(f"tod{n}_level"))
            plan_ref = _first_int(by_segment.get(f"tod{n}_plan"))
            if level_ref is None or plan_ref is None:
                issues.append(
                    _issue(
                        IssueSeverity.WARN,
                        "daejeon_tod_row_incomplete",
                        f"{source_file} line {i + 1}: TOD PLAN {n} states {clock} but not both "
                        "수준 and ID; the row is dropped rather than half-read.",
                        messages={
                            "ko": f"{source_file} {i + 1}행: TOD PLAN {n}에 수준/ID가 모두 있지 "
                            "않아 해당 행을 사용하지 않습니다.",
                            "en": f"{source_file} line {i + 1}: TOD PLAN {n} lacks 수준 and/or "
                            "ID; the row is dropped rather than half-read.",
                            "ja": f"{source_file} {i + 1}行: TOD PLAN {n} に 수준/ID が揃わず、"
                            "行を破棄します。",
                        },
                        subject=subject,
                        context={"tod_plan": n, "clock": clock, "line": i + 1},
                    )
                )
                continue
            tod_entries.append(
                TodEntry(
                    tod_plan_no=n,
                    index=index if index is not None else 0,
                    clock=clock,
                    level_no=level_ref,
                    plan_id=plan_ref,
                    line_no=i + 1,
                )
            )

    plans, pairing_issues = _pair_splits(
        split_rows, plan_id_rows, source_file=source_file, subject=subject
    )
    issues.extend(pairing_issues)

    if empty_level_slots:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "daejeon_level_slot_empty",
                f"{source_file}: 수준 {sorted(empty_level_slots)} print no 주기. The form prints "
                "all six level slots whether or not the site uses them, so an empty slot is a "
                "spare, not a missing value. It becomes a refusal only if a TOD row selects it "
                "(daejeon_tod_references_unknown_level).",
                messages={
                    "ko": f"수준 {sorted(empty_level_slots)}에 주기가 없습니다. 양식은 6개 슬롯을 "
                    "항상 인쇄하므로 미사용 슬롯이며, TOD가 선택할 때만 거부 사유가 됩니다.",
                    "en": f"수준 {sorted(empty_level_slots)} print no 주기 — the form always "
                    "prints six slots, so these are spares; only a TOD row selecting one refuses.",
                    "ja": f"수준 {sorted(empty_level_slots)} に周期がありません。様式は常に6枠を "
                    "印字するため未使用枠であり、TOD が選択した場合のみ拒否となります。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
                context={"empty_levels": sorted(empty_level_slots)},
            )
        )

    if not tod_entries and not any(i.severity is IssueSeverity.REFUSE for i in issues):
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_no_tod_entries",
                f"{source_file}: no TOD PLAN row was read. The TOD table is the ONLY thing that "
                "pairs a 수준 (cycle) with an ID (split + offset) — the two tables are "
                "independent lookups — so with no TOD row there is no way to say which cycle "
                "goes with which split, and pairing them by print position is the exact error "
                "this parser exists to avoid.",
                messages={
                    "ko": "TOD PLAN 행을 읽지 못했습니다. 수준(주기)과 ID(시간분할·연동)를 "
                    "짝지어 주는 것은 TOD 표뿐이며, 인쇄 위치로 짝짓는 것은 명백한 오류입니다.",
                    "en": "No TOD PLAN row was read. The TOD table is the only thing pairing a "
                    "수준 (cycle) with an ID (split+offset); pairing by print position is wrong.",
                    "ja": "TOD PLAN 行を読めませんでした。수준(周期)と ID(時間分割・オフセット)を "
                    "対応付けるのは TOD 表のみで、印字位置での対応付けは誤りです。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
            )
        )

    for row in plans:
        if row.offset_s is None:
            issues.append(
                _issue(
                    IssueSeverity.WARN,
                    "daejeon_plan_offset_missing",
                    f"plan ID {row.plan_id} states no 연동. The plan is kept — a stand-alone "
                    "intersection needs no offset — but it cannot take part in a coordinated "
                    "corridor until the value is supplied.",
                    messages={
                        "ko": f"계획 ID {row.plan_id}에 연동값이 없습니다. 단독 교차로에는 "
                        "문제없으나 연동 축에는 사용할 수 없습니다.",
                        "en": f"Plan ID {row.plan_id} states no 연동; fine stand-alone, but it "
                        "cannot join a coordinated corridor.",
                        "ja": f"計画 ID {row.plan_id} にオフセットがありません。単独交差点では "
                        "問題ありませんが系統制御には使えません。",
                    },
                    subject=subject,
                    context={"plan_id": row.plan_id},
                )
            )
        if row.has_empty_field:
            issues.append(
                _issue(
                    IssueSeverity.WARN,
                    "daejeon_split_field_empty",
                    f"plan ID {row.plan_id}: the 시간분할 cell contains an empty field "
                    f"({_render_split(row.splits_ring_a)}). Read positionally — field i is Φi — "
                    "an empty field means that phase does not run in this plan, and the vector "
                    "is NOT compacted, because compacting it moves every later phase's time onto "
                    "the phase before it. If the empty field is a stray comma instead, the "
                    "field count will not match the SIGNAL TABLE's phase count and the sheet is "
                    "refused (daejeon_split_count_mismatch) rather than guessed at.",
                    messages={
                        "ko": f"계획 ID {row.plan_id}의 시간분할에 빈 칸이 있습니다. 위치가 곧 "
                        "현시이므로 압축하지 않습니다. 쉼표 오타라면 현시 수와 개수가 달라져 "
                        "시트가 거부됩니다.",
                        "en": f"Plan ID {row.plan_id}'s 시간분할 has an empty field; position is "
                        "identity so the vector is not compacted. A stray comma instead shows up "
                        "as a phase-count mismatch and refuses the sheet.",
                        "ja": f"計画 ID {row.plan_id} の時間分割に空欄があります。位置が現示を "
                        "示すため詰めません。カンマの誤入力なら現示数不一致で拒否されます。",
                    },
                    subject=subject,
                    citation=CIT_SHEET_OBSERVED,
                    context={"plan_id": row.plan_id, "fields": len(row.splits_ring_a)},
                )
            )
        if row.actuated_splits:
            issues.append(
                _issue(
                    IssueSeverity.WARN,
                    "daejeon_actuated_split_variant_present",
                    f"plan ID {row.plan_id} prints "
                    f"{len(row.actuated_splits)} further split vector(s) under 감응제어 "
                    f"({[label for label, _ in row.actuated_splits]}). They are read and carried "
                    "verbatim but NOT merged into the base plan: which one the controller runs "
                    "depends on detector state, which is a run-time fact the sheet cannot state "
                    "and a fixed-time program cannot represent.",
                    messages={
                        "ko": f"계획 ID {row.plan_id}에 감응제어 시간분할이 추가로 있습니다. "
                        "검지기 상태에 따라 달라지므로 고정주기 프로그램에 합치지 않습니다.",
                        "en": f"Plan ID {row.plan_id} prints extra 감응제어 split vectors; they "
                        "depend on detector state and are not merged into the fixed-time plan.",
                        "ja": f"計画 ID {row.plan_id} に感応制御の時間分割が追加されています。"
                        "検知器状態に依存するため固定時間計画には統合しません。",
                    },
                    subject=subject,
                    context={
                        "plan_id": row.plan_id,
                        "variants": [label for label, _ in row.actuated_splits],
                    },
                )
            )

    return tuple(level_rows), tuple(plans), tuple(tod_entries), issues


def _first_int(items: Sequence[tuple[int, str]] | None) -> int | None:
    for _, token in items or []:
        if _INT.match(token):
            return int(token)
    return None


def _first_split(items: Sequence[tuple[int, str]] | None) -> tuple[float | None, ...] | None:
    for _, token in items or []:
        vec = _split_vector(token)
        if vec is not None:
            return vec
    return None


def _render_split(vec: Sequence[float | None]) -> str:
    """A split vector as the sheet prints it — empties as empties."""
    return ",".join("" if v is None else f"{v:g}" for v in vec)


def _pair_splits(
    split_rows: Sequence[
        tuple[int, tuple[float | None, ...], tuple[tuple[str, tuple[float | None, ...]], ...]]
    ],
    plan_id_rows: Sequence[tuple[int, int, float | None]],
    *,
    source_file: str,
    subject: str,
) -> tuple[tuple[PlanRow, ...], list[ImportIssue]]:
    """Turn the printed split rows into plans.

    The form prints ``시간분할(초)`` as TWO rows per plan — RING A then RING B —
    with the ``ID``/``연동`` label vertically centred between them, so the label
    lands on the first row in some firmwares and on the second in others. The
    pairing rule that survives both: take the split rows in document order, chunk
    them two at a time, and the plan id is the one id-row printed on either line
    of the chunk. Verified against every sheet in the real drop, including the
    ones where blank lines separate the pairs and the ones where they do not.
    """
    issues: list[ImportIssue] = []
    if len(split_rows) % 2 != 0:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_split_rows_unpaired",
                f"{source_file}: {len(split_rows)} 시간분할 rows were read, which is odd. The "
                "column prints exactly two rows per plan (RING A, RING B), so an odd count means "
                "the extraction lost a row and every plan below the loss would be attributed to "
                "the wrong ID.",
                messages={
                    "ko": f"{source_file}: 시간분할 행 수({len(split_rows)})가 홀수입니다. "
                    "계획당 2행(RING A/B)이므로 행이 유실되었고, 이후 모든 계획이 잘못된 ID에 "
                    "귀속됩니다.",
                    "en": f"{source_file}: an odd number ({len(split_rows)}) of 시간분할 rows — "
                    "the column prints two per plan, so a row was lost and every plan below it "
                    "would bind to the wrong ID.",
                    "ja": f"{source_file}: 時間分割行が奇数({len(split_rows)})です。1計画あたり "
                    "2行のため行が欠落しており、以降の計画が誤った ID に結び付きます。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
                context={"split_row_count": len(split_rows)},
            )
        )
        return (), issues

    by_line = {line: (plan_id, offset) for line, plan_id, offset in plan_id_rows}
    plans: list[PlanRow] = []
    for k in range(0, len(split_rows), 2):
        # Ring B's 감응제어 vectors are the same cells printed twice; only ring
        # A's are carried, the same way the base split's ring B is compared
        # rather than duplicated.
        line_a, vec_a, act_a = split_rows[k]
        line_b, vec_b = split_rows[k + 1][0], split_rows[k + 1][1]
        owner = by_line.get(line_a) or by_line.get(line_b)
        if owner is None:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_split_pair_without_plan_id",
                    f"{source_file} lines {line_a + 1}–{line_b + 1}: a 시간분할 pair with no ID "
                    "printed on either row. A split vector nobody can name cannot be selected by "
                    "a TOD row, and guessing which ID it belongs to would silently give one "
                    "intersection another's timings.",
                    messages={
                        "ko": f"{source_file} {line_a + 1}–{line_b + 1}행: ID 없는 시간분할 "
                        "쌍입니다. 어느 ID의 것인지 추측하지 않습니다.",
                        "en": f"{source_file} lines {line_a + 1}–{line_b + 1}: a split pair with "
                        "no ID on either row; which ID it belongs to is not guessed.",
                        "ja": f"{source_file} {line_a + 1}–{line_b + 1}行: ID のない時間分割対 "
                        "です。どの ID のものかは推測しません。",
                    },
                    subject=subject,
                    citation=CIT_NEVER_INVENT,
                    context={"lines": [line_a + 1, line_b + 1]},
                )
            )
            continue
        plan_id, offset = owner
        if len(vec_a) != len(vec_b):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_split_length_disagrees_between_rings",
                    f"{source_file}: plan ID {plan_id} prints {len(vec_a)} split values for "
                    f"RING A and {len(vec_b)} for RING B. The two rings run the same phase "
                    "columns, so a length difference means a value was lost in extraction.",
                    messages={
                        "ko": f"계획 ID {plan_id}: RING A/B의 시간분할 개수가 다릅니다. 추출 중 "
                        "값이 유실되었습니다.",
                        "en": f"Plan ID {plan_id}: RING A and RING B print a different number of "
                        "split values; a value was lost in extraction.",
                        "ja": f"計画 ID {plan_id}: RING A と B の時間分割の個数が異なります。"
                        "抽出時に値が欠落しています。",
                    },
                    subject=subject,
                    context={"plan_id": plan_id, "ring_a": len(vec_a), "ring_b": len(vec_b)},
                )
            )
            continue
        plans.append(
            PlanRow(
                plan_id=plan_id,
                offset_s=offset,
                splits_ring_a=vec_a,
                splits_ring_b=vec_b,
                actuated_splits=act_a,
                line_no=line_a + 1,
            )
        )
    return tuple(plans), issues


def _read_day_assignments(lines: Sequence[str]) -> tuple[DayAssignment, ...]:
    """The 주간신호계획 block: weekday → (TOD plan, TIME plan).

    Read with a token pattern rather than column anchors because the block's own
    header (``요일 TOD PLAN TIME PLAN``) prints without separating spaces in
    several firmwares, which leaves nothing to anchor on.
    """
    pattern = re.compile(rf"(?<!\S)([{''.join(WEEKDAYS)}])\s+(\d+)\s+(\d+)(?!\S)")
    out: list[DayAssignment] = []
    seen: set[str] = set()
    for line in lines:
        for m in pattern.finditer(line):
            weekday = m.group(1)
            if weekday in seen:
                continue
            seen.add(weekday)
            out.append(
                DayAssignment(
                    weekday=weekday, tod_plan_no=int(m.group(2)), time_plan_no=int(m.group(3))
                )
            )
    return tuple(out)


def _read_memo_notes(lines: Sequence[str]) -> tuple[str, ...]:
    """MEMO lines that change what a timing means.

    Deliberately narrow: only the ``…보행신호시간 Y값 포함`` note is matched,
    because it is the one MEMO string in the real drop that alters arithmetic.
    Everything else in the MEMO is operational prose, and hoovering it up would
    pull client free text into an object that gets logged and reported.
    """
    return tuple(line.strip() for line in lines if _MEMO_Y_INCLUDED.search(line))


# ---------------------------------------------------------------------------
# Cross-checks
# ---------------------------------------------------------------------------


def _cross_check(
    levels: Sequence[LevelRow],
    plans: Sequence[PlanRow],
    tod_entries: Sequence[TodEntry],
    phase_count: int,
    *,
    source_file: str,
    subject: str,
    settings: DaejeonSettings | None = None,
) -> list[ImportIssue]:
    """The arithmetic that makes the parse verifiable rather than merely finished.

    Only the (level, plan) pairs some TOD row actually selects are checked. An
    ID printed in the table but never scheduled is a spare the controller does
    not run, and refusing a whole sheet because a spare does not close would be
    a refusal with no operational meaning.
    """
    settings = settings or DEFAULT_SETTINGS
    issues: list[ImportIssue] = []
    cycles = {row.level_no: row.cycle_s for row in levels}
    by_id = {row.plan_id: row for row in plans}
    # One finding per distinct problem, not one per TOD row that reaches it: a
    # split that does not close its cycle is one defect even when six scheduled
    # rows select it, and a refusal list that repeats itself hides the others.
    reported: set[tuple] = set()

    def _once(key: tuple) -> bool:
        if key in reported:
            return False
        reported.add(key)
        return True

    for entry in tod_entries:
        if entry.level_no not in cycles:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_tod_references_unknown_level",
                    f"{source_file} line {entry.line_no}: TOD PLAN {entry.tod_plan_no} at "
                    f"{entry.clock} selects 수준 {entry.level_no}, which the PLAN TABLE does not "
                    f"define (it defines {sorted(cycles)}). The controller runs a cycle this "
                    "sheet never states.",
                    messages={
                        "ko": f"{entry.clock} TOD 항목이 정의되지 않은 수준 {entry.level_no}을 "
                        "선택합니다. 시트에 없는 주기로 운영된다는 뜻입니다.",
                        "en": f"The TOD entry at {entry.clock} selects 수준 {entry.level_no}, "
                        "which the PLAN TABLE never defines.",
                        "ja": f"{entry.clock} の TOD 項目が未定義の 수준 {entry.level_no} を "
                        "選択しています。",
                    },
                    subject=subject,
                    context={"level": entry.level_no, "defined": sorted(cycles),
                             "line": entry.line_no},
                )
            )
            continue
        row = by_id.get(entry.plan_id)
        if row is None:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_tod_references_unknown_plan",
                    f"{source_file} line {entry.line_no}: TOD PLAN {entry.tod_plan_no} at "
                    f"{entry.clock} selects plan ID {entry.plan_id}, for which no 시간분할 was "
                    f"read (read: {sorted(by_id)}).",
                    messages={
                        "ko": f"{entry.clock} TOD 항목이 시간분할을 읽지 못한 계획 ID "
                        f"{entry.plan_id}을 선택합니다.",
                        "en": f"The TOD entry at {entry.clock} selects plan ID {entry.plan_id}, "
                        "for which no split vector was read.",
                        "ja": f"{entry.clock} の TOD 項目が時間分割を読めなかった計画 ID "
                        f"{entry.plan_id} を選択しています。",
                    },
                    subject=subject,
                    context={"plan_id": entry.plan_id, "read": sorted(by_id),
                             "line": entry.line_no},
                )
            )
            continue

        cycle = cycles[entry.level_no]
        # RING B is checked separately ONLY when it differs from RING A. The two
        # rows are identical on most sheets, and reporting one defect twice
        # crowds the other findings out of the refusal list.
        rings: list[tuple[str, tuple[float | None, ...]]] = [("A", row.splits_ring_a)]
        if row.splits_ring_b != row.splits_ring_a:
            rings.append(("B", row.splits_ring_b))
        for ring, vec in rings:
            total = _vector_sum(vec)
            if abs(total - cycle) > settings.cycle_sum_tolerance_s and _once(
                ("sum", entry.level_no, entry.plan_id, ring)
            ):
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "daejeon_split_sum_mismatch",
                        f"{source_file}: TOD PLAN {entry.tod_plan_no} at {entry.clock} pairs "
                        f"수준 {entry.level_no} (주기 {cycle:g} s) with plan ID {entry.plan_id}, "
                        f"whose RING {ring} 시간분할 sums to {total:g} s. A split vector that "
                        "does not close its cycle means either the extraction dropped a column "
                        "or the sheet is internally wrong — both need a human before anything "
                        "reaches Vissim, which would run the difference as a silent phase drift.",
                        messages={
                            "ko": f"{entry.clock}: 수준 {entry.level_no}의 주기 {cycle:g}초와 "
                            f"계획 {entry.plan_id} RING {ring} 시간분할 합 {total:g}초가 "
                            "다릅니다. 열 유실이거나 시트 자체 오류이며 사람이 확인해야 합니다.",
                            "en": f"At {entry.clock}: 수준 {entry.level_no} states a cycle of "
                            f"{cycle:g} s but plan {entry.plan_id}'s RING {ring} split sums to "
                            f"{total:g} s — extraction loss or a wrong sheet; a human must look.",
                            "ja": f"{entry.clock}: 수준 {entry.level_no} の周期 {cycle:g}秒 と "
                            f"計画 {entry.plan_id} RING {ring} の合計 {total:g}秒 が一致しません。",
                        },
                        subject=subject,
                        citation=CIT_SHEET_OBSERVED,
                        context={
                            "clock": entry.clock,
                            "level": entry.level_no,
                            "plan_id": entry.plan_id,
                            "ring": ring,
                            "cycle_s": cycle,
                            "split_sum_s": total,
                        },
                    )
                )
            if phase_count and len(vec) != phase_count and _once(
                ("count", entry.plan_id, ring)
            ):
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "daejeon_split_count_mismatch",
                        f"{source_file}: plan ID {entry.plan_id} RING {ring} states {len(vec)} "
                        f"phase times, but the SIGNAL TABLE populates {phase_count} phase "
                        "column(s). One of the two was read wrong, and the pairing of split "
                        "values to phases — which is what every green window depends on — is "
                        "therefore unknown.",
                        messages={
                            "ko": f"계획 {entry.plan_id} RING {ring}의 시간분할 개수({len(vec)})와 "
                            f"SIGNAL TABLE 현시 수({phase_count})가 다릅니다. 시간분할과 현시의 "
                            "대응을 알 수 없습니다.",
                            "en": f"Plan {entry.plan_id} RING {ring} states {len(vec)} phase "
                            f"times but the SIGNAL TABLE populates {phase_count} phases; the "
                            "split-to-phase pairing is unknown.",
                            "ja": f"計画 {entry.plan_id} RING {ring} の時間分割数({len(vec)})と "
                            f"SIGNAL TABLE の現示数({phase_count})が異なります。",
                        },
                        subject=subject,
                        context={"plan_id": entry.plan_id, "ring": ring,
                                 "split_count": len(vec), "phase_count": phase_count},
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# The neutral controller
# ---------------------------------------------------------------------------


def _build_controller(
    *,
    source_file: str,
    subject: str,
    number: str,
    name: str | None,
    group_raw: str | None,
    model: str | None,
    revised: str | None,
    phases: Sequence[PhaseTiming],
    levels: Sequence[LevelRow],
    plans: Sequence[PlanRow],
    tod_entries: Sequence[TodEntry],
) -> tuple[SignalControllerData, list[ImportIssue]]:
    """One :class:`SignalControllerData`, one program per scheduled (level, plan).

    Programs are keyed ``L{level}-P{plan}`` and carry the clock times that select
    them in ``time_of_day``, verbatim as the sheet words them. A (level, plan)
    pair printed but never scheduled is not emitted: it is a spare, and emitting
    it as a program would claim the controller runs it.
    """
    issues: list[ImportIssue] = []
    cycles = {row.level_no: row.cycle_s for row in levels}
    by_id = {row.plan_id: row for row in plans}

    note_parts = [f"controller firmware {model}" if model else None,
                  f"sheet revised {revised}" if revised else None]
    sheet_note = "; ".join(p for p in note_parts if p) or None

    scheduled: dict[tuple[int, int], list[str]] = {}
    for entry in tod_entries:
        scheduled.setdefault((entry.level_no, entry.plan_id), []).append(
            f"TOD{entry.tod_plan_no} {entry.clock}"
        )

    programs: list[SignalProgramData] = []
    for (level_no, plan_id), clocks in sorted(scheduled.items()):
        row = by_id[plan_id]
        cycle = cycles[level_no]
        phase_records: list[PhaseData] = []
        for index, split_s in enumerate(row.splits_ring_a, start=1):
            if split_s is None:
                continue  # printed empty: this phase does not run in this plan
            timing = next((t for t in phases if t.phase_no == index), None)
            prov = Provenance(
                source_file=source_file,
                locator=f"PLAN TABLE ID {plan_id} 시간분할 #{index}; SIGNAL TABLE Φ{index}",
                native_id=f"Φ{index}",
                note=sheet_note,
            )
            phase = PhaseData(
                provenance=prov,
                phase_id=f"Φ{index}",
                sequence_index=index - 1,
                green_s=None,
                amber_s=None,
                all_red_s=None,
                intergreen_s=timing.yellow_red_s if timing else None,
                min_green_s=timing.min_green_s if timing else None,
                max_green_s=timing.max_green_s if timing else None,
            )
            phase.mark_absent(
                "green_s",
                f"green = 시간분할({split_s:g} s) − clearance, and splitting it needs a "
                "declared amber the sheet's single YELLOW(RED) row (황색+전적색 combined, per "
                "the client's answer) does not itself provide; see to_fixed_time_plan / "
                "daejeon_intergreen_split_unstated.",
            )
            phase.mark_absent(
                "amber_s",
                "the sheet has one clearance row labelled YELLOW(RED) — 황색+전적색 combined, "
                "per the client's answer — and never prints the amber by itself.",
            )
            phase.mark_absent(
                "all_red_s",
                "the sheet has no 전적색 row of its own; the all-red is only derivable once a "
                "reviewer declares the amber (to_fixed_time_plan).",
            )
            if timing is None or timing.yellow_red_s is None:
                phase.mark_absent(
                    "intergreen_s",
                    "YELLOW(RED) is blank for this phase in both rings, or the rings disagree.",
                )
            if phase.min_green_s is None:
                phase.mark_absent(
                    "min_green_s", "MIN. GREEN is blank for this phase, or the rings disagree."
                )
            if phase.max_green_s is None:
                phase.mark_absent(
                    "max_green_s", "MAX.Ⅱ is blank for this phase, or the rings disagree."
                )
            phase_records.append(phase)

        program = SignalProgramData(
            provenance=Provenance(
                source_file=source_file,
                locator=f"PLAN TABLE 수준 {level_no} / ID {plan_id}",
                native_id=f"L{level_no}-P{plan_id}",
                note=sheet_note,
            ),
            program_id=f"L{level_no}-P{plan_id}",
            name=f"수준 {level_no} / ID {plan_id}",
            source_kind=ProgramSourceKind.DECLARED,
            cycle_time_s=cycle,
            offset_s=row.offset_s,
            phases=phase_records,
            time_of_day=", ".join(sorted(set(clocks))),
        )
        if row.offset_s is None:
            program.mark_absent("offset_s", "the 연동 cell is blank for this plan ID.")
        programs.append(program)

    cycle_values = sorted({row.cycle_s for row in levels})
    controller = SignalControllerData(
        provenance=Provenance(
            source_file=source_file,
            locator="교차로 운영 DATABASE header",
            native_id=number,
            note=sheet_note,
        ),
        controller_id=number,
        name=name,
        region=REGION_JURISDICTION,
        control_type=None,
        control_type_raw=None,
        cycle_time_s=cycle_values[0] if len(cycle_values) == 1 else None,
        cycle_min_s=cycle_values[0] if cycle_values else None,
        cycle_max_s=cycle_values[-1] if cycle_values else None,
        programs=programs,
        coordination=None,
    )
    controller.mark_absent(
        "control_type",
        "the sheet names a controller firmware and prints both a TOD plan table and 감응제어 "
        "columns, but never states 정주기/감응 as the operating mode; inferring one from the "
        "presence of detector columns would be a guess.",
    )
    controller.mark_absent(
        "signal_groups",
        "signal groups are the 현시도 drawing (which approach moves in which phase); it is not "
        "in the text layer and is not inferred.",
    )
    controller.mark_absent(
        "detectors",
        "detector positions are not on this form; the 감응제어 columns state only that detector "
        "state selects a split vector.",
    )
    if len(cycle_values) != 1:
        controller.mark_absent(
            "cycle_time_s",
            f"the sheet states {len(cycle_values)} different cycles across its 수준 levels; a "
            "single nominal cycle would be a choice the sheet does not make.",
        )
    if group_raw:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "daejeon_group_recorded",
                f"교차로군 {group_raw!r} is carried verbatim. Whether it corresponds to a SCATS "
                "subsystem for coordination purposes is a client question, not an inference.",
                subject=subject,
                context={"group_raw": group_raw},
            )
        )
    return controller, issues


# ---------------------------------------------------------------------------
# The bridge into services.genesis.signals
# ---------------------------------------------------------------------------


def to_fixed_time_plan(
    sheet: DaejeonSheet,
    *,
    level_no: int,
    plan_id: int,
    settings: DaejeonSettings | None = None,
) -> tuple[FixedTimePlan | None, list[ImportIssue]]:
    """One scheduled (level, plan) pair → the ``FixedTimePlan``
    ``signals.generate_node_signals`` takes.

    Returns ``(None, issues)`` whenever a REFUSE fires — the same "no partial
    product" contract the rest of the chain keeps.

    **This refuses by default, and that is the finding, not a bug.** Vissim needs
    the amber as its own display; the sheet prints one clearance number
    (``황색+전적색`` combined, per the client's answer — see the module
    docstring) and never separates the amber out of it. So unless a reviewer
    has declared the amber through :attr:`DaejeonSettings.declared_amber_s`,
    this returns ``daejeon_intergreen_split_unstated`` and no plan. With the
    declaration, the arithmetic is entirely re-derivable on paper::

        intergreen_i = YELLOW(RED)_i          (read from the SIGNAL TABLE)
        amber        = the declared value     (recorded in Provenance.derived_by)
        all_red_i    = intergreen_i - amber   (>= 0; exactly 0 is an ordinary design)
        green_i      = 시간분할_i - intergreen_i
        start_i      = Σ 시간분할_1..i-1       (phase 1 at cycle time 0)

    ``all_red_i`` of exactly zero is accepted and produces a program phase
    with no all-red display — amber running straight to red is an ordinary,
    valid signal design, not a gap. Only a NEGATIVE ``all_red_i`` — the
    declared amber longer than the sheet's own ``YELLOW(RED)`` — refuses, as
    ``daejeon_all_red_negative``: that shape means the declaration
    contradicts the sheet, not that the intersection is unusual.

    ``연동`` is deliberately NOT applied to ``start_i``: the sheet never states
    the instant the offset counts from, and a corridor offset applied against the
    wrong reference is a progression error Vissim runs without comment.
    """
    settings = settings or DEFAULT_SETTINGS
    issues: list[ImportIssue] = []
    subject = f"{sheet.source_file} / 교차로 {sheet.intersection_no} / L{level_no}-P{plan_id}"
    program_id = f"L{level_no}-P{plan_id}"

    program = (
        sheet.controller.program_by_id(program_id) if sheet.controller is not None else None
    )
    row = sheet.plan(plan_id)
    cycle = sheet.level_cycle_s(level_no)
    if program is None or row is None or cycle is None:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_program_not_found",
                f"{sheet.source_file}: no scheduled program {program_id}. Only (수준, ID) pairs "
                "some TOD row actually selects are emitted as programs — a pair the controller "
                "never runs is a spare, and building a Vissim program out of one would model a "
                "signal plan the intersection does not operate.",
                messages={
                    "ko": f"예약된 프로그램 {program_id}이 없습니다. TOD가 실제로 선택하는 "
                    "(수준, ID) 조합만 프로그램으로 만듭니다.",
                    "en": f"No scheduled program {program_id}; only (수준, ID) pairs a TOD row "
                    "selects become programs.",
                    "ja": f"予約されたプログラム {program_id} がありません。TOD が実際に選択 "
                    "する組み合わせのみをプログラム化します。",
                },
                subject=subject,
                context={"program_id": program_id},
            )
        )
        return None, issues

    if settings.require_ring_split_agreement and not row.rings_agree:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_ring_splits_disagree",
                f"plan ID {plan_id} runs different 시간분할 on RING A and RING B. A fixed-time "
                "program has one phase sequence; emitting ring A's would model half the "
                "controller and report the result as the whole intersection.",
                messages={
                    "ko": f"계획 {plan_id}의 RING A/B 시간분할이 다릅니다. 한쪽만 내보내면 "
                    "제어기의 절반만 모델링하고 전체인 것처럼 보고하게 됩니다.",
                    "en": f"Plan {plan_id} runs different splits on RING A and RING B; emitting "
                    "one ring would model half the controller and report it as the whole.",
                    "ja": f"計画 {plan_id} は RING A と B で時間分割が異なります。片方のみを "
                    "出力すると制御機の半分だけを全体として報告することになります。",
                },
                subject=subject,
                citation=CIT_NEVER_INVENT,
                context={"plan_id": plan_id},
            )
        )
        return None, issues

    # -- pass 1: everything that does NOT depend on the amber declaration ---
    #
    # Deliberately BEFORE the amber gate. A sheet whose splits are broken should
    # say so, not hide behind the declaration it also lacks — and the coverage
    # probe reads "the only refusal is daejeon_intergreen_split_unstated" as
    # "this sheet is ready except for the one gap the document itself has",
    # which is only a true reading if every other check has already run.
    @dataclass(frozen=True)
    class _Slot:
        index: int
        split_s: float
        intergreen_s: float
        green_s: float
        start_s: float
        min_green_s: float | None
        max_green_s: float | None

    slots: list[_Slot] = []
    dropped: list[int] = []
    start = 0.0
    for index, split_s in enumerate(row.splits_ring_a, start=1):
        if split_s is None:
            dropped.append(index)
            continue
        timing = sheet.phase(index)
        intergreen = timing.yellow_red_s if timing else None
        if intergreen is None:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_phase_yellow_unknown",
                    f"Φ{index}: no usable YELLOW(RED). Without it neither the green nor the "
                    "all-red can be stated, and a signal group with an invented clearance is "
                    "the silent-wrong-numbers failure this project exists to prevent.",
                    messages={
                        "ko": f"Φ{index}: YELLOW(RED)를 사용할 수 없어 녹색·전적색을 산출할 수 "
                        "없습니다. 소거시간을 지어내지 않습니다.",
                        "en": f"Φ{index}: no usable YELLOW(RED), so neither green nor all-red "
                        "can be stated; a clearance is never invented.",
                        "ja": f"Φ{index}: YELLOW(RED) が使えず緑・全赤を算出できません。"
                        "クリアランスを作り出しません。",
                    },
                    subject=subject,
                    citation=CIT_S5421_TIMINGS_UNCHANGED,
                    context={"phase": index},
                )
            )
            return None, issues
        green = round(float(split_s) - float(intergreen), 6)
        if green <= 0:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_green_not_positive",
                    f"Φ{index}: 시간분할 {split_s:g} s minus the clearance {intergreen:g} s "
                    f"leaves a green of {green:g} s. A phase that is all clearance is not a "
                    "phase; the sheet or the extraction is wrong.",
                    messages={
                        "ko": f"Φ{index}: 시간분할 {split_s:g}초에서 소거 {intergreen:g}초를 "
                        f"빼면 녹색이 {green:g}초입니다. 시트 또는 추출이 잘못되었습니다.",
                        "en": f"Φ{index}: split {split_s:g} s minus clearance {intergreen:g} s "
                        f"leaves a green of {green:g} s; the sheet or the extraction is wrong.",
                        "ja": f"Φ{index}: 時間分割 {split_s:g}秒 からクリアランス "
                        f"{intergreen:g}秒 を引くと緑が {green:g}秒 になります。",
                    },
                    subject=subject,
                    context={"phase": index, "split_s": split_s,
                             "intergreen_s": intergreen, "green_s": green},
                )
            )
            return None, issues
        slots.append(
            _Slot(
                index=index,
                split_s=float(split_s),
                intergreen_s=float(intergreen),
                green_s=green,
                start_s=start,
                min_green_s=timing.min_green_s if timing else None,
                max_green_s=timing.max_green_s if timing else None,
            )
        )
        start = round(start + float(split_s), 6)

    if not slots:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_green_not_positive",
                f"plan ID {plan_id} runs no phase at all — every 시간분할 field is empty.",
                messages={
                    "ko": f"계획 {plan_id}의 시간분할이 모두 비어 있어 진행하는 현시가 없습니다.",
                    "en": f"Plan {plan_id} runs no phase at all — every 시간분할 field is empty.",
                    "ja": f"計画 {plan_id} は時間分割がすべて空で、進行する現示がありません。",
                },
                subject=subject,
                context={"plan_id": plan_id},
            )
        )
        return None, issues

    # -- the amber gate ----------------------------------------------------
    if settings.declared_amber_s is None:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "daejeon_intergreen_split_unstated",
                f"{sheet.source_file}: the sheet has ONE clearance row, labelled YELLOW(RED), "
                "and no 전적색 row anywhere. The client has confirmed YELLOW(RED) is "
                "황색+전적색 combined, which settles WHICH quantity the column is — but the "
                "column still prints only the combined number, never the amber by itself. "
                "Vissim needs the amber as its own display and §5.4.2.1 requires yellow and "
                "all-red to be input from controller information unchanged, so the amber split "
                "is still not assumed. Declare it via DaejeonSettings.declared_amber_s (a "
                "reviewer's engineering declaration, e.g. the 3.0 s Korean police-standard "
                "convention, or a further client answer) — never a default this module picks. "
                "Everything else about this program checked out.",
                messages={
                    "ko": "시트에는 YELLOW(RED) 한 행만 있고 전적색 행이 없습니다. 발주처는 "
                    "YELLOW(RED)가 황색+전적색 결합값임을 확인했지만, 그 안에서 황색만 별도로 "
                    "적힌 곳은 없습니다. Vissim은 황색을 독립 표시로 요구하고 §5.4.2.1은 제어기 "
                    "값을 그대로 입력하도록 하므로, 황색 분할값을 임의로 가정하지 않습니다. "
                    "declared_amber_s로 명시하십시오(예: 경찰청 표준 3.0초 관행 또는 추가 발주처 "
                    "확인). 그 외 항목은 모두 통과했습니다.",
                    "en": "The sheet has one clearance row (YELLOW(RED)) and no 전적색 row. The "
                    "client has confirmed YELLOW(RED) is 황색+전적색 combined — that settles "
                    "WHICH quantity the column is, but the column still prints only the combined "
                    "number, never the amber alone. Vissim needs amber as its own display and "
                    "§5.4.2.1 forbids substituting a default for it. Declare the amber via "
                    "declared_amber_s (a reviewer's engineering declaration, e.g. the 3.0 s "
                    "Korean police-standard convention, or a further client answer). Every other "
                    "check on this program passed.",
                    "ja": "シートにはクリアランス行が YELLOW(RED) の1行のみで全赤の行が "
                    "ありません。発注者は YELLOW(RED) が黄色+全赤の合算値であることを確認しました "
                    "が、その内訳(黄色のみ)を記載した箇所はありません。既定値を代入せず、"
                    "declared_amber_s で明示してください(例:韓国警察庁標準の3.0秒慣行、または "
                    "追加の発注者回答)。他の検査はすべて通過しています。",
                },
                subject=subject,
                citation=CIT_S5421_TIMINGS_UNCHANGED,
                context={
                    "program_id": program_id,
                    "phase_count": len(slots),
                    "dropped_phases": dropped,
                    "blocked_only_by_amber": True,
                },
            )
        )
        return None, issues

    amber = float(settings.declared_amber_s)
    derived_by = (
        "대전 교차로 운영 DATABASE: intergreen = YELLOW(RED); amber = reviewer declaration"
        + (f" ({settings.amber_declaration_note})" if settings.amber_declaration_note else "")
    )

    # -- pass 2: apply the declaration and build ---------------------------
    rows: list[PhaseDerivation] = []
    phase_records: list[PhaseData] = []
    for index in dropped:
        # 1.6's ASSUMPTION A5 shape: a phase that never runs is carried as a
        # DROPPED row, not omitted. generate_node_signals filters dropped rows
        # out of the assignable set, so an approach assigned to this phase
        # refuses there instead of silently getting no green.
        rows.append(
            PhaseDerivation(
                phase_id=f"Φ{index}",
                occurrences=None,
                observed_total_s=0.0,
                scats_average_s=None,
                frequency=None,
                average_phase_s=0.0,
                rounded_phase_s=None,
                start_offset_s=None,
                intergreen_s=None,
                green_s=None,
                is_stretch_phase=False,
                dropped=True,
                drop_reason=(
                    "the 시간분할 cell is printed empty for this plan: the phase does not run "
                    "in it."
                ),
            )
        )
    for slot in slots:
        # all_red == 0 is ACCEPTED: amber running straight to red with no
        # all-red display is an ordinary, valid signal design, not a gap in
        # the data (see the module docstring's item 2 and the client's
        # confirmed YELLOW(RED) = 황색+전적색 reading). Only a NEGATIVE
        # all-red refuses — that shape means the declared amber is longer
        # than what the sheet itself states for this phase, which is a
        # genuine contradiction between the declaration and the document,
        # not an unusual-but-valid intersection.
        all_red = round(slot.intergreen_s - amber, 6)
        if all_red < 0:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "daejeon_all_red_negative",
                    f"Φ{slot.index}: the declared amber ({amber:g} s) is LONGER than the "
                    f"sheet's YELLOW(RED) ({slot.intergreen_s:g} s), which would need a "
                    f"negative all-red of {all_red:g} s. An all-red of exactly zero is an "
                    "ordinary design and is accepted; a negative one is not physically "
                    "meaningful, so either the declaration is wrong for this phase or "
                    "YELLOW(RED) does not cover the whole clearance here.",
                    messages={
                        "ko": f"Φ{slot.index}: 선언한 황색({amber:g}s)이 "
                        f"YELLOW(RED)({slot.intergreen_s:g}s)보다 길어 전적색이 "
                        f"{all_red:g}s(음수)가 됩니다. 전적색이 정확히 0인 것은 정상 설계로 "
                        "허용하지만, 음수는 물리적으로 불가능하므로 선언이 틀렸거나 이 현시의 "
                        "YELLOW(RED)가 전체 소거시간을 나타내지 않는 것입니다.",
                        "en": f"Φ{slot.index}: the declared amber is longer than YELLOW(RED), "
                        f"so the all-red would be {all_red:g} s (negative). All-red of exactly "
                        "zero is an ordinary design and is accepted; a negative one is not — "
                        "either the declaration is wrong for this phase or YELLOW(RED) does not "
                        "cover the whole clearance here.",
                        "ja": f"Φ{slot.index}: 宣言した黄色が YELLOW(RED) より長いため、全赤が "
                        f"{all_red:g}s(負の値)になります。全赤がちょうど0であることは正常な "
                        "設計として許容しますが、負の値は物理的にあり得ません。宣言が誤っている "
                        "か、当該現示の YELLOW(RED) が全クリアランスを表していません。",
                    },
                    subject=subject,
                    citation=CIT_S5421_TIMINGS_UNCHANGED,
                    context={"phase": slot.index, "amber_s": amber,
                             "intergreen_s": slot.intergreen_s, "all_red_s": all_red},
                )
            )
            return None, issues

        prov = Provenance(
            source_file=sheet.source_file,
            locator=f"PLAN TABLE ID {plan_id} 시간분할 #{slot.index}; SIGNAL TABLE Φ{slot.index}",
            native_id=f"Φ{slot.index}",
            derived_by=derived_by,
        )
        phase_records.append(
            PhaseData(
                provenance=prov,
                phase_id=f"Φ{slot.index}",
                sequence_index=slot.index - 1,
                green_s=slot.green_s,
                amber_s=amber,
                all_red_s=all_red,
                intergreen_s=slot.intergreen_s,
                min_green_s=slot.min_green_s,
                max_green_s=slot.max_green_s,
            )
        )
        rows.append(
            PhaseDerivation(
                phase_id=f"Φ{slot.index}",
                occurrences=None,
                # A DECLARED plan has no observation. 0.0 is the same "the source
                # did not state this" placeholder fixed_time.py itself writes
                # (`phase.observed_total_duration_s or 0.0`); occurrences=None
                # and scats_average_s=None are what say this is not an
                # observation, and source_kind=DECLARED says it on the program.
                observed_total_s=0.0,
                scats_average_s=None,
                frequency=None,
                average_phase_s=slot.split_s,
                rounded_phase_s=slot.split_s,
                start_offset_s=slot.start_s,
                intergreen_s=slot.intergreen_s,
                green_s=slot.green_s,
                is_stretch_phase=False,
            )
        )
    rows.sort(key=lambda r: int(r.phase_id.lstrip("Φ")))

    if row.offset_s:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "daejeon_coordination_offset_not_applied",
                f"plan ID {plan_id} states a 연동 of {row.offset_s:g} s, and it is NOT applied: "
                "the sheet never says what instant the offset counts from (controller master "
                "clock, subsystem reference, or the coordinated phase's start). Phase Φ1's green "
                "is placed at cycle time 0. For a stand-alone intersection this changes nothing; "
                "in a coordinated corridor it is a progression error Vissim runs without comment.",
                messages={
                    "ko": f"계획 {plan_id}의 연동값 {row.offset_s:g}초는 적용하지 않았습니다 — "
                    "기준 시점이 시트에 없습니다. 단독 교차로에는 영향이 없으나 연동 축에서는 "
                    "진행 오차가 됩니다.",
                    "en": f"Plan {plan_id}'s 연동 of {row.offset_s:g} s is NOT applied — the "
                    "sheet never states its reference instant. Harmless stand-alone, a "
                    "progression error in a coordinated corridor.",
                    "ja": f"計画 {plan_id} のオフセット {row.offset_s:g}秒 は適用していません — "
                    "基準時点がシートに記載されていません。系統制御では進行誤差になります。",
                },
                subject=subject,
                citation=CIT_SHEET_OBSERVED,
                context={"plan_id": plan_id, "offset_s": row.offset_s},
            )
        )

    bridged = SignalProgramData(
        provenance=Provenance(
            source_file=sheet.source_file,
            locator=f"PLAN TABLE 수준 {level_no} / ID {plan_id}",
            native_id=program_id,
            derived_by=derived_by,
        ),
        program_id=program_id,
        name=program.name,
        source_kind=ProgramSourceKind.DECLARED,
        cycle_time_s=cycle,
        offset_s=row.offset_s,
        phases=phase_records,
        time_of_day=program.time_of_day,
    )
    plan = FixedTimePlan(
        controller_id=str(sheet.intersection_no),
        source_program_id=program_id,
        program=bridged,
        complete_cycles=None,
        stretch_phase_id=None,
        average_cycle_s=None,
        plan_cycle_s=cycle,
        round_up_residual_s=None,
        rows=tuple(rows),
        issues=tuple(i for i in issues if i.severity is not IssueSeverity.INFO),
    )
    return plan, issues


# ---------------------------------------------------------------------------
# Batch entry points
# ---------------------------------------------------------------------------


def parse_pdf(pdf: bytes | str | Path, *, source_file: str | None = None) -> DaejeonSheet:
    """One PDF → one parsed sheet. Thin: text extraction, then the text parser."""
    name = source_file or (Path(pdf).name if not isinstance(pdf, bytes) else "<bytes>")
    return parse_sheet_text(read_sheet_text(pdf), source_file=name)


def parse_zip(archive: str | Path) -> list[DaejeonSheet]:
    """Every ``.pdf`` member of a zip, in name order.

    Member names are decoded defensively: a zip written without the UTF-8 flag
    carries Korean names in cp949, and ``zipfile`` will have decoded those bytes
    as cp437, turning every intersection name into mojibake that then travels
    into provenance.
    """
    out: list[DaejeonSheet] = []
    with zipfile.ZipFile(archive) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                continue
            out.append(parse_pdf(zf.read(info), source_file=_member_name(info)))
    return out


def _member_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for encoding in ("cp949", "euc-kr", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


# ---------------------------------------------------------------------------
# Coverage — the measurement, as a number
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """What a run actually achieved, counted rather than described.

    Three separate numbers, deliberately not collapsed into one:

    ``fully_parsed``
        the sheet read with nothing missing.
    ``bridge_blocked_only_by_amber``
        every scheduled program passed every check the bridge makes EXCEPT the
        yellow/all-red declaration — i.e. the sheet is held up by the one gap the
        *document* has, not by anything about the parse.
    ``bridge_ready``
        a plan was actually produced, which requires
        :attr:`DaejeonSettings.declared_amber_s` to be set by a reviewer. With
        the default settings this is 0, and that is the correct number.

    Conflating "we read the sheet" with "Vissim can run it" is exactly the
    conflation this module exists to avoid, so the table refuses to do it.
    """

    sheets: int = 0
    parsed: int = 0
    fully_parsed: int = 0
    partially_parsed: int = 0
    refused: int = 0
    controllers: int = 0
    programs: int = 0
    bridge_ready: int = 0
    bridge_blocked_only_by_amber: int = 0
    refusal_reasons: tuple[tuple[str, int], ...] = ()
    warning_reasons: tuple[tuple[str, int], ...] = ()
    phase_count_histogram: tuple[tuple[int, int], ...] = ()
    min_green_corroborations: int = 0
    min_green_checked: int = 0


def coverage(
    sheets: Iterable[DaejeonSheet], *, settings: DaejeonSettings | None = None
) -> Coverage:
    """Measure a batch. Pure counting — it runs the bridge but keeps whatever
    ``settings`` say, so the numbers describe the caller's actual configuration
    and not a probe's."""
    settings = settings or DEFAULT_SETTINGS

    sheets = list(sheets)
    refusals: dict[str, int] = {}
    warnings: dict[str, int] = {}
    phase_hist: dict[int, int] = {}
    parsed = full = partial = refused = controllers = programs = 0
    bridge_ready = amber_blocked = 0
    corroborated = checked = 0

    for sheet in sheets:
        for code in sheet.issue_codes(IssueSeverity.REFUSE):
            refusals[code] = refusals.get(code, 0) + 1
        for code in sheet.issue_codes(IssueSeverity.WARN):
            warnings[code] = warnings.get(code, 0) + 1
        if not sheet.ok:
            refused += 1
            continue
        parsed += 1
        if sheet.phase_count:
            phase_hist[sheet.phase_count] = phase_hist.get(sheet.phase_count, 0) + 1
        if sheet.fully_parsed:
            full += 1
        else:
            partial += 1
        if sheet.controller is not None:
            controllers += 1
            programs += len(sheet.controller.programs)

        # MIN. GREEN == 시간분할 − YELLOW(RED) is the evidence that YELLOW(RED)
        # is the whole clearance rather than the yellow alone. Counted once per
        # (sheet, phase) — a sheet with ten plans must not outvote one with two.
        for timing in sheet.phases:
            if timing.min_green_s is None or timing.yellow_red_s is None:
                continue
            splits = {
                row.splits_ring_a[timing.phase_no - 1]
                for row in sheet.plans
                if len(row.splits_ring_a) >= timing.phase_no
                and row.splits_ring_a[timing.phase_no - 1] is not None
            }
            if not splits:
                continue
            checked += 1
            if any(
                abs((s - timing.yellow_red_s) - timing.min_green_s) < 1e-6
                for s in splits
                if s is not None
            ):
                corroborated += 1

        produced = False
        blocked_only_by_amber = False
        for entry in {(e.level_no, e.plan_id) for e in sheet.tod_entries}:
            plan, plan_issues = to_fixed_time_plan(
                sheet, level_no=entry[0], plan_id=entry[1], settings=settings
            )
            if plan is not None:
                produced = True
                continue
            codes = {i.code for i in plan_issues if i.severity is IssueSeverity.REFUSE}
            if codes == {"daejeon_intergreen_split_unstated"}:
                blocked_only_by_amber = True
        if produced:
            bridge_ready += 1
        if blocked_only_by_amber:
            amber_blocked += 1

    return Coverage(
        sheets=len(sheets),
        parsed=parsed,
        fully_parsed=full,
        partially_parsed=partial,
        refused=refused,
        controllers=controllers,
        programs=programs,
        bridge_ready=bridge_ready,
        bridge_blocked_only_by_amber=amber_blocked,
        refusal_reasons=tuple(sorted(refusals.items(), key=lambda kv: (-kv[1], kv[0]))),
        warning_reasons=tuple(sorted(warnings.items(), key=lambda kv: (-kv[1], kv[0]))),
        phase_count_histogram=tuple(sorted(phase_hist.items())),
        min_green_corroborations=corroborated,
        min_green_checked=checked,
    )


__all__ = [
    "BRIDGE_REFUSE_CODES",
    "CIT_NEVER_INVENT",
    "CIT_S5421_TIMINGS_UNCHANGED",
    "CIT_SHEET_OBSERVED",
    "COLUMN_TOLERANCE",
    "DAEJEON_SIGNAL_ADAPTER",
    "DEFAULT_SETTINGS",
    "PARTIAL_CODES",
    "REFUSE_CODES",
    "REGION_JURISDICTION",
    "REQUIRED_SIGNAL_TABLE_ROWS",
    "SIGNAL_TABLE_ROWS",
    "WEEKDAYS",
    "Coverage",
    "DaejeonSettings",
    "DaejeonSheet",
    "DayAssignment",
    "LevelRow",
    "PhaseTiming",
    "PlanRow",
    "TodEntry",
    "coverage",
    "parse_pdf",
    "parse_sheet_text",
    "parse_zip",
    "read_sheet_text",
    "to_fixed_time_plan",
]

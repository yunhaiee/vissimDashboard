"""Format-neutral domain model for imported signal + count data (roadmap 0.7).

This module is the *core* of the adapter architecture required by TK
requirement R1.3 ("코어는 포맷 중립 내부 모델 ... 각 지역 포맷은 플러그인
어댑터"). Nothing in here knows what a SCATS Phase History file or a Daejeon
스마트교차로 export looks like: every regional adapter's job is to turn its own
files into exactly these objects, and every downstream consumer (genesis 3.1/
3.2, the SCATS→fixed-time module 1.6, the data-quality gate R2.5) reads only
these objects.

Three design rules run through the whole module. They exist because this
project's thesis is that *a refusal with a reason beats a completed job with
wrong numbers* — applied here one layer upstream of the Vissim edits:

1. **Absence is data.** Every field a real source may legitimately lack is
   ``Optional`` and ``None`` means exactly one thing: *the source did not
   carry this datum*. ``None`` never means zero, never means "default", and
   never means "we could not be bothered". An adapter that fills a field it
   did not read has committed the silent-wrong-numbers sin one layer earlier
   than Vissim does. Where the absence is material, the adapter also records
   *why* in :attr:`NeutralRecord.absent` and raises an ``ImportIssue``
   (see ``base.py``) — never a fabricated value.
2. **Everything cites its source.** Every record carries a required
   :class:`Provenance` (which file, which row/cell/native id). Anything
   downstream — a report table, a constraint message, an approval card — can
   therefore say where a number came from. A value that was *computed* rather
   than read must say so via :attr:`Provenance.derived_by`.
3. **Enums only where the domain is genuinely closed; a raw string always.**
   Turn types and compass bearings are closed. Vehicle classifications,
   control-type vocabulary and pedestrian protection schemes are *not* closed
   across countries, so those fields pair an optional normalised enum with a
   mandatory-ish ``*_raw``/``code`` string that preserves the source's own
   token verbatim. An adapter that cannot map its source token onto an enum
   member leaves the enum ``None`` and keeps the raw string — it must not
   pick "the closest" member.

Two views of a signal program coexist on purpose (see
:class:`SignalProgramData`): the *phase* view (SCATS-native: a sequence of
phases with durations) and the *signal-group window* view (Vissim-native: per
signal-group green windows inside the cycle). Sources supply one or the other
or both; **neither is ever synthesised from the other here**. Deriving one
from the other is the job of the SCATS→fixed-time module (roadmap 1.6), which
records its derivation in ``Provenance.derived_by``.

Non-COM by construction: this module is pure Pydantic + stdlib and must never
import ``vissim_com`` or require a Vissim licence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Closed domains (enums)
#
# Rule for adding a member: only add one when the concept is closed *in the
# domain*, not merely closed in the region we happen to be looking at. Every
# enum below is paired with a free-string field somewhere so that a source
# token we cannot map survives verbatim instead of being rounded to the
# nearest member.
# ---------------------------------------------------------------------------


class ControlType(str, Enum):
    """How a controller decides its timings.

    Deliberately has **no** ``UNKNOWN`` member: "the source did not say" is
    expressed by ``control_type=None`` plus ``control_type_raw`` holding the
    source's own word (SCATS "Masterlink", 대전 "감응"/"정주기", ...).
    Coordination is orthogonal to control type and lives in
    :class:`CoordinationData`, not here.
    """

    FIXED_TIME = "fixed_time"
    VEHICLE_ACTUATED = "vehicle_actuated"
    ADAPTIVE_SCATS = "adaptive_scats"
    ADAPTIVE_OTHER = "adaptive_other"
    UNSIGNALIZED = "unsignalized"


class ProgramSourceKind(str, Enum):
    """Where a :class:`SignalProgramData`'s numbers actually came from.

    This is a **required** field on every program because the difference is
    the difference between a fact and an estimate, and Vissim will happily
    run either without comment:

    * ``DECLARED`` — the source states these timings as the controller's
      configured program (e.g. the Signal Data spreadsheet's Timings tab).
    * ``OBSERVED_HISTORY`` — the timings are *observations* of what the
      adaptive controller actually did (SCATS Phase History / Event History).
      Averaging them into a fixed-time plan is roadmap 1.6's job, not the
      adapter's.
    * ``DERIVED`` — computed by us from other records. ``Provenance.derived_by``
      must name the algorithm and its authority (e.g. "MRWA Appendix-A A.2
      avg-phase-time").
    """

    DECLARED = "declared"
    OBSERVED_HISTORY = "observed_history"
    DERIVED = "derived"


class CoordinationPoint(str, Enum):
    """MRWA Appendix-A A.5 ``a,b^c`` notation: ``^`` = start-of-phase
    coordination, its absence = end-of-phase."""

    START_OF_PHASE = "start_of_phase"
    END_OF_PHASE = "end_of_phase"


class SignalGroupKind(str, Enum):
    """What a signal group serves. Closed: a group shows an aspect to
    vehicles, pedestrians or public transport."""

    VEHICLE = "vehicle"
    PEDESTRIAN = "pedestrian"
    PUBLIC_TRANSPORT = "public_transport"


class DetectorFunction(str, Enum):
    """What a detector is *for*. No ``OTHER`` member on purpose — an
    unmappable function stays ``None`` with ``function_raw`` preserved."""

    STOP_LINE = "stop_line"
    ADVANCE = "advance"
    COUNT = "count"
    QUEUE = "queue"
    PEDESTRIAN_PUSHBUTTON = "pedestrian_pushbutton"


class PedestrianProtectionType(str, Enum):
    """The six protection arrangements catalogued in MRWA Appendix-A A.3.

    These are WA's enumeration; the concepts (exclusive vs parallel vs
    arrow-protected) generalise, but another jurisdiction may well have an
    arrangement none of these names. The contract is explicit: an adapter that
    cannot map its source's arrangement onto a member leaves
    ``protection_type=None``, keeps ``protection_type_raw``, and emits an
    ``INFO`` issue. This enum is expected to gain members as regions land.
    """

    EXCLUSIVE_PHASE = "exclusive_phase"
    PARALLEL_TIME_CONTROLLED = "parallel_time_controlled"
    PARALLEL_FLASHING_YELLOW = "parallel_flashing_yellow"
    TIMED_RED_ARROW = "timed_red_arrow"
    RED_ARROW_WITH_FLASH = "red_arrow_with_flash"
    FULLY_CONTROLLED = "fully_controlled"


class TurnType(str, Enum):
    """Geometric turn, **not** opposed/permissive semantics.

    Deliberately side-of-road agnostic: in WA (drive-left) the opposed turn is
    the right turn, in Korea (drive-right) it is the left turn. Encoding
    "the opposed turn" here would make every neutral record mean something
    different per country. Which side the region drives on is stated once, on
    :attr:`SiteMetadata.drive_side`, and consumers combine the two.
    """

    LEFT = "left"
    THROUGH = "through"
    RIGHT = "right"
    U_TURN = "u_turn"


class CompassDirection(str, Enum):
    """Eight-point bearing of an intersection leg."""

    N = "N"
    NE = "NE"
    E = "E"
    SE = "SE"
    S = "S"
    SW = "SW"
    W = "W"
    NW = "NW"


class DriveSide(str, Enum):
    """Which side of the road the region drives on. Site-level, stated once."""

    LEFT = "left"
    RIGHT = "right"


class ClassificationScheme(str, Enum):
    """Which vehicle-classification *scheme* a count's class code belongs to.

    A closed set that we control: adding a region means adding a member (a
    deliberate code change, reviewed), never silently reusing a foreign
    scheme's codes. ``SOURCE_SPECIFIC`` is for a file that invents its own
    buckets; ``UNCLASSIFIED`` is for a file that gives one undifferentiated
    total.

    ``KOREA_SMART_INTERSECTION_6`` was added by roadmap 1.2 when the real
    Daejeon 스마트교차로 export turned out to break the count into **six**
    classes (화물 대형/중형/소형, 버스 대형/소형, 승용), not the twelve of the
    national survey convention ``KOREA_12_CLASS`` was placed for. The adapter
    contract's §7.1 instruction was followed exactly — *add a member, do not
    reuse another scheme's codes* — and the two Korean members stay separate
    because whether the six buckets aggregate the twelve is unverified (it is
    open question 4 to the client in the data-intake inventory).
    """

    AUSTROADS = "austroads"
    KOREA_12_CLASS = "korea_12_class"
    KOREA_SMART_INTERSECTION_6 = "korea_smart_intersection_6"
    SOURCE_SPECIFIC = "source_specific"
    UNCLASSIFIED = "unclassified"


class CanonicalVehicleClass(str, Enum):
    """Coarse cross-scheme bucket that *is* safely mappable.

    Deliberately coarse. Mapping Austroads class 7 onto a Korean 12-class code
    is a research question, not an import step; mapping either onto
    ``HEAVY_VEHICLE`` is not. Fine-grained fidelity is preserved by
    :attr:`VehicleClassRef.code` + :attr:`VehicleClassRef.scheme`, which are
    never lossy.
    """

    LIGHT_VEHICLE = "light_vehicle"
    HEAVY_VEHICLE = "heavy_vehicle"
    BUS = "bus"
    MOTORCYCLE = "motorcycle"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"
    ALL_VEHICLES = "all_vehicles"


class VolumeUnit(str, Enum):
    """Unit of a count value. Required, never defaulted: a PCU-weighted figure
    silently read as a vehicle count is precisely the class of error this
    layer exists to prevent."""

    VEHICLES = "vehicles"
    PCU = "pcu"


# ---------------------------------------------------------------------------
# Provenance and the record base
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """Where one neutral record came from. Required on every record.

    ``source_file`` is whatever identifier the import job knows the input by
    (a file name, an upload id, an API endpoint) — never a machine-specific
    absolute path, so provenance stays meaningful after the job directory is
    gone.

    Exactly one honesty rule is enforced: a record that was **read** must say
    *where* in the file (``locator`` and/or ``native_id``); a record that was
    **computed** must say *how* (``derived_by``). A record that says neither
    is a record nobody can audit, and is rejected.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_file: str = Field(min_length=1, description="File/upload the datum came from.")
    locator: str | None = Field(
        default=None,
        description='Where inside the file: "row 42", "Timings!C7", "record 128".',
    )
    native_id: str | None = Field(
        default=None, description="The source system's own id for this record, verbatim."
    )
    derived_by: str | None = Field(
        default=None,
        description=(
            "Non-None means the value was COMPUTED, not read. Name the algorithm "
            'and its authority, e.g. "MRWA Appendix-A A.2 avg-phase-time".'
        ),
    )
    note: str | None = None

    @model_validator(mode="after")
    def _read_records_must_point_somewhere(self) -> "Provenance":
        if self.derived_by is None and self.locator is None and self.native_id is None:
            raise ValueError(
                "provenance must cite its source: set locator and/or native_id for a "
                "value read from the file, or derived_by for a value computed from "
                "other records"
            )
        return self


class NeutralRecord(BaseModel):
    """Base for every neutral object: mandatory provenance + explicit absence.

    :attr:`absent` maps a field name of *this* model to a human-readable reason
    the source could not supply it. It is the machine-readable half of the
    "never invent" rule: an adapter that would otherwise have to guess writes
    down what is missing and why instead. The invariant is enforced — you
    cannot claim a field is absent while also giving it a value:

    >>> # absent={"cycle_time_s": "Link & Offset Plans tab not supplied"}
    >>> # is only valid while cycle_time_s is None (or an empty container).

    ``extra="forbid"`` throughout: a typo'd field name in an adapter is a bug
    we want to hear about at parse time, not a silently dropped datum.
    """

    model_config = ConfigDict(extra="forbid")

    provenance: Provenance
    absent: dict[str, str] = Field(
        default_factory=dict,
        description="field name -> why the source could not supply it.",
    )

    @model_validator(mode="after")
    def _absent_entries_must_be_truthful(self) -> "NeutralRecord":
        reserved = {"absent", "provenance"}
        known = set(type(self).model_fields) - reserved
        for name, reason in self.absent.items():
            if name not in known:
                raise ValueError(
                    f"absent[{name!r}] does not name a field of "
                    f"{type(self).__name__} (known: {sorted(known)})"
                )
            if not str(reason).strip():
                raise ValueError(f"absent[{name!r}] must give a non-empty reason")
            value = getattr(self, name)
            if value is not None and value != [] and value != {}:
                raise ValueError(
                    f"absent[{name!r}] claims the source lacks this field, but it "
                    f"holds {value!r}"
                )
        return self

    def mark_absent(self, field_name: str, reason: str) -> "NeutralRecord":
        """Record a material absence after construction (returns ``self``).

        Validates the claim on a copy *before* mutating, so a rejected claim
        leaves the record untouched rather than half-updated.
        """
        merged = dict(self.absent)
        merged[field_name] = reason
        candidate = self.model_copy(update={"absent": merged})
        type(self).model_validate(candidate.model_dump())  # raises if the claim is untrue
        self.absent = merged
        return self


# ---------------------------------------------------------------------------
# Small shared value objects
# ---------------------------------------------------------------------------


class TimeWindow(BaseModel):
    """A closed-open time span. ``end`` must be strictly after ``start``."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _end_after_start(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        return self

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


class PositionHint(BaseModel):
    """Whatever locational hint a source happens to carry — never more.

    Sources vary wildly: SCATS LMA drawings give a lane and a distance from the
    stop line, a spreadsheet may give nothing, a Korean export may give WGS84
    coordinates. All fields optional, but a hint with *nothing* in it is
    rejected — an empty hint should simply be ``None``. The CRS that ``x``/``y``
    are expressed in lives once on :attr:`SiteMetadata.coordinate_reference`.
    """

    model_config = ConfigDict(extra="forbid")

    x: float | None = None
    y: float | None = None
    link_ref: str | None = None
    lane_no: int | None = Field(default=None, ge=1)
    distance_from_stop_line_m: float | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _not_entirely_empty(self) -> "PositionHint":
        if all(
            getattr(self, name) is None
            for name in ("x", "y", "link_ref", "lane_no", "distance_from_stop_line_m", "description")
        ):
            raise ValueError("PositionHint carries no information — use None instead")
        return self


class ApproachRef(BaseModel):
    """One leg of an intersection, as the source identifies it.

    ``leg_id`` is required and is the source's own identifier (a leg number,
    an approach code, a street name used as a key). ``bearing`` is optional
    because plenty of sources name approaches without ever stating a compass
    direction, and guessing one from a street name is invention.
    """

    model_config = ConfigDict(extra="forbid")

    leg_id: str = Field(min_length=1)
    bearing: CompassDirection | None = None
    street_name: str | None = None
    raw: str | None = Field(default=None, description="The source's own approach label, verbatim.")


class MovementRef(BaseModel):
    """A turning movement out of an approach.

    The *from* leg is the record's :attr:`TurningCountData.approach` — it is
    not repeated here, so there is only ever one source of truth for it.

    ``is_approach_total`` marks the aggregate row many count files carry
    ("all movements from this approach"). It is an explicit flag rather than a
    magic enum member so the internal-consistency check in ``quality.py`` can
    key on it without ambiguity, and so that "total" can never be confused
    with "turn type unknown".
    """

    model_config = ConfigDict(extra="forbid")

    turn: TurnType | None = None
    turn_raw: str | None = Field(default=None, description="The source's own movement token.")
    to_leg_id: str | None = None
    to_bearing: CompassDirection | None = None
    is_approach_total: bool = False

    @model_validator(mode="after")
    def _totals_are_not_turns(self) -> "MovementRef":
        if self.is_approach_total and (self.turn is not None or self.to_leg_id is not None):
            raise ValueError(
                "an approach-total row cannot also name a turn or an exit leg"
            )
        return self


class VehicleClassRef(BaseModel):
    """A vehicle class as the source states it, plus an optional safe bucket.

    ``scheme`` + ``code`` are lossless and always present: the Austroads class
    "7", the Korean "중형화물차", the bespoke "HV" all survive verbatim.
    ``canonical`` is filled **only** when the mapping is defensible without
    guessing (see :class:`CanonicalVehicleClass`); an adapter that is unsure
    leaves it ``None`` and lets the quality gate report reduced class coverage.
    """

    model_config = ConfigDict(extra="forbid")

    scheme: ClassificationScheme
    code: str = Field(min_length=1, description="The source's class code, verbatim.")
    canonical: CanonicalVehicleClass | None = None
    label: str | None = Field(default=None, description="Human-readable class name, if given.")

    @model_validator(mode="after")
    def _unclassified_totals_are_consistent(self) -> "VehicleClassRef":
        if (
            self.scheme is ClassificationScheme.UNCLASSIFIED
            and self.canonical is not None
            and self.canonical is not CanonicalVehicleClass.ALL_VEHICLES
        ):
            raise ValueError(
                "scheme=unclassified can only map to canonical=all_vehicles "
                f"(got {self.canonical.value})"
            )
        return self


class CoordinationData(BaseModel):
    """Subsystem / offset-plan facts (MRWA Appendix-A A.5).

    ``raw`` keeps the source's own coordination notation (``a,b^c``) so nothing
    is lost when we only partly understand it.
    """

    model_config = ConfigDict(extra="forbid")

    subsystem: str | None = None
    coordinated_phase_id: str | None = None
    coordination_point: CoordinationPoint | None = None
    reference_site_id: str | None = None
    offsets_s: list[float] | None = Field(
        default=None,
        description="None = the source did not state offsets; [] = it stated none.",
    )
    plan_number: int | None = Field(
        default=None, description="Link plan number (MRWA: 1 night / 2 PM / 3 balanced / 4 AM)."
    )
    raw: str | None = None


# ---------------------------------------------------------------------------
# Signal-side neutral records
# ---------------------------------------------------------------------------


class SignalGroupWindow(NeutralRecord):
    """One signal group's green, positioned in the cycle when that is knowable.

    The Vissim-native view (``sgStart``/``sgEnd``). ``start_s``/``end_s`` are
    seconds from the cycle reference point; ``end_s`` < ``start_s`` is legal
    and means the window wraps the cycle boundary, which is why no ordering
    validator fires here.

    ``green_duration_s`` exists **separately** and is not redundant. MRWA
    Appendix-A A.1's Event History gives ``SGi=On`` / ``SGi=Off`` wall-clock
    timestamps: the *length* of every green falls straight out, but its
    *position in the cycle* does not — that needs the stretch-phase timeline to
    fix a cycle origin (A.2). A source can therefore tell us how long a group
    was green while telling us nothing about where in the cycle it sat, and the
    schema has to be able to say exactly that instead of forcing an adapter to
    fabricate a start time. (Schema correction made while mapping Appendix A —
    see docs/product/adapter-contract.md §"What Appendix A changed".)
    """

    signal_group_id: str = Field(min_length=1)
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    green_duration_s: float | None = Field(
        default=None,
        ge=0,
        description="Green length, when known independently of its cycle position.",
    )
    observation_count: int | None = Field(
        default=None, ge=0, description="How many observed activations this window summarises."
    )


class PhaseData(NeutralRecord):
    """One phase of a signal program (SCATS-native view).

    Durations are all optional and all independent: a Phase History file gives
    a total phase duration with no split into green/amber/all-red, and the
    split must **not** be back-computed from Austroads formulae at import time
    (that needs approach speed and clearance distance the count file does not
    have — MRWA Appendix-A A.3). Missing splits stay ``None`` with an entry in
    ``absent``.

    ``frequency`` and ``is_stretch_phase`` encode MRWA Appendix-A A.2's model of
    demand-dependent phasing: the stretch phase runs every cycle and defines
    the cycle boundary (frequency 1.0); other phases run in a fraction of
    cycles. They are the inputs roadmap 1.6 needs and are meaningless as
    guesses, so they are only ever set from observation.
    """

    phase_id: str = Field(min_length=1)
    name: str | None = None
    sequence_index: int | None = Field(default=None, ge=0)

    green_s: float | None = Field(default=None, ge=0)
    amber_s: float | None = Field(default=None, ge=0)
    all_red_s: float | None = Field(default=None, ge=0)
    intergreen_s: float | None = Field(
        default=None,
        ge=0,
        description="Stated intergreen. MRWA A.3: early cut-off + yellow + all-red.",
    )
    min_green_s: float | None = Field(default=None, ge=0)
    max_green_s: float | None = Field(default=None, ge=0)

    is_stretch_phase: bool | None = Field(
        default=None,
        description="MRWA A.2 stretch phase (runs every cycle, defines cycle bounds).",
    )
    frequency: float | None = Field(
        default=None,
        ge=0,
        description=(
            "MRWA A.2 f_i = occurrences / cycles. Stretch phase = 1.0. NOT capped at 1.0: "
            "Appendix-A A.4's repeat-right-turn phasing runs a phase twice per cycle, so "
            "f_i > 1 is a real, representable value."
        ),
    )
    observation_count: int | None = Field(default=None, ge=0)
    observed_total_duration_s: float | None = Field(
        default=None,
        ge=0,
        description="Summed activation time across the observation window (MRWA A.2 input).",
    )

    @model_validator(mode="after")
    def _min_max_green_ordered(self) -> "PhaseData":
        if (
            self.min_green_s is not None
            and self.max_green_s is not None
            and self.min_green_s > self.max_green_s
        ):
            raise ValueError(
                f"min_green_s ({self.min_green_s}) exceeds max_green_s ({self.max_green_s})"
            )
        return self


class SignalGroupData(NeutralRecord):
    """A signal group (SCATS SG / Vissim signal group).

    MRWA §5 signal-coding rule: signal-group numbering **must** match SCATS, so
    ``signal_group_id`` is the source's number as a string and is never
    renumbered by an adapter.

    ``phase_ids`` and ``movements`` are ``| None`` rather than defaulting to
    ``[]`` because empty and unknown are different facts: ``[]`` says the
    source declares this group serves no phase (a real, reportable oddity),
    ``None`` says the source never told us. Phase membership lives here and
    only here — :class:`PhaseData` deliberately does not carry the inverse
    list, so the two can never disagree; use
    :meth:`SignalProgramData.signal_group_ids_for_phase`.
    """

    signal_group_id: str = Field(min_length=1)
    name: str | None = None
    kind: SignalGroupKind | None = None
    phase_ids: list[str] | None = None
    movements: list[MovementRef] | None = None
    late_start_s: float | None = Field(
        default=None, ge=0, description="MRWA Appendix-A Timings tab: late start."
    )
    early_cut_off_s: float | None = Field(
        default=None, ge=0, description="MRWA Appendix-A Timings tab: early cut-off."
    )
    min_green_s: float | None = Field(default=None, ge=0)
    approach: ApproachRef | None = None


class PedestrianPhaseData(NeutralRecord):
    """A pedestrian movement's timings and protection arrangement.

    Field names follow MRWA Appendix-A A.3 exactly (walk / clearance-1 /
    clearance-2 / delay) because that is the vocabulary the source documents
    use; ``clearance_2_s`` exists separately because WA derives it by rule
    (intergreen − 1 s) while other jurisdictions state a single clearance.

    ``demand_frequency`` is Appendix-A p.11's ``count(Demand=Off Active=On) ÷
    complete cycles`` — an observation, never a default.
    """

    pedestrian_phase_id: str = Field(min_length=1)
    name: str | None = None
    signal_group_id: str | None = None
    phase_ids: list[str] | None = None

    walk_s: float | None = Field(default=None, ge=0)
    clearance_1_s: float | None = Field(default=None, ge=0)
    clearance_2_s: float | None = Field(default=None, ge=0)
    delay_s: float | None = Field(
        default=None, ge=0, description="Delay before walk when a prior all-red cannot clear."
    )

    protection_type: PedestrianProtectionType | None = None
    protection_type_raw: str | None = None
    protection_time_s: float | None = Field(
        default=None,
        ge=0,
        description="MRWA A.3: existing sites hold this in SCATS Special Times cell 11.",
    )
    crossing_length_m: float | None = Field(default=None, gt=0)
    demand_frequency: float | None = Field(
        default=None,
        ge=0,
        description=(
            "MRWA A.2 p.11: count(Walk j Demand=Off Active=On) / complete cycles. Uncapped "
            "for the same reason as PhaseData.frequency — a walk served twice in a cycle "
            "is representable, not a validation error."
        ),
    )


class DetectorData(NeutralRecord):
    """A detector as the source describes it.

    ``controller_id`` is optional because a standalone count station is a
    detector with no controller; ``channel`` is the port/detector number the
    controller addresses it by. Position is a *hint* (:class:`PositionHint`)
    and never a fabricated coordinate — genesis places detectors from MRWA §5
    defaults (2.0 m upstream of the stop line, 4.5 m long) as a *documented
    default at build time*, which is a different act from an adapter claiming
    the source said so.
    """

    detector_id: str = Field(min_length=1)
    name: str | None = None
    controller_id: str | None = None
    channel: str | None = Field(default=None, description="Controller port / detector number.")
    function: DetectorFunction | None = None
    function_raw: str | None = None
    signal_group_id: str | None = None
    phase_ids: list[str] | None = None
    position: PositionHint | None = None
    length_m: float | None = Field(default=None, gt=0)


class SignalProgramData(NeutralRecord):
    """One signal program / plan of a controller.

    Carries the two complementary views described in the module docstring:
    ``phases`` (SCATS-native) and ``signal_group_windows`` (Vissim-native).
    Both default to empty lists because "this program has no phases recorded"
    is a normal partial-import outcome; use ``absent`` to say why when it is
    material.

    :attr:`source_kind` is required — see :class:`ProgramSourceKind`. It is the
    single most important honesty flag in the schema: a fixed-time program
    *declared* by a controller and a fixed-time program *averaged out of six
    weeks of adaptive history* are different objects with different error bars,
    and Vissim will run both without a murmur.
    """

    program_id: str = Field(min_length=1)
    name: str | None = None
    source_kind: ProgramSourceKind
    cycle_time_s: float | None = Field(default=None, gt=0)
    offset_s: float | None = Field(default=None, ge=0)
    phases: list[PhaseData] = Field(default_factory=list)
    signal_group_windows: list[SignalGroupWindow] = Field(default_factory=list)
    observation_window: TimeWindow | None = Field(
        default=None, description="For OBSERVED_HISTORY programs: the period observed."
    )
    observed_cycle_count: int | None = Field(
        default=None, ge=0, description="MRWA A.2: complete stretch-to-stretch cycles counted."
    )
    time_of_day: str | None = Field(
        default=None, description="When the plan applies, as the source words it."
    )
    coordination: CoordinationData | None = None

    def phase_by_id(self, phase_id: str) -> PhaseData | None:
        for phase in self.phases:
            if phase.phase_id == phase_id:
                return phase
        return None

    def signal_group_ids_for_phase(
        self, phase_id: str, signal_groups: list["SignalGroupData"]
    ) -> list[str]:
        """Invert phase membership. Groups whose ``phase_ids`` is ``None``
        (unknown) are excluded — unknown is not membership."""
        return [
            group.signal_group_id
            for group in signal_groups
            if group.phase_ids is not None and phase_id in group.phase_ids
        ]


class SignalControllerData(NeutralRecord):
    """A signal controller and everything hanging off it.

    ``controller_id`` is always a string (TCS "359", a Korean 교차로 id, a
    zero-padded code) — never an int, because leading zeros are load-bearing in
    several real id schemes.

    Cycle *bounds* live here (MRWA Appendix-A: the Link & Offset Plans tab
    states subsystem, coordinated phase and cycle bounds) while each program
    carries its own cycle time; conflating the two would make "the cycle" mean
    two things.
    """

    controller_id: str = Field(min_length=1)
    name: str | None = None
    site_id: str | None = Field(default=None, description="Links back to SiteMetadata.site_id.")
    region: str | None = None

    control_type: ControlType | None = None
    control_type_raw: str | None = Field(
        default=None, description="The source's own control-type word, verbatim."
    )

    cycle_time_s: float | None = Field(
        default=None, gt=0, description="Nominal cycle, when the source states a single one."
    )
    cycle_min_s: float | None = Field(default=None, gt=0)
    cycle_max_s: float | None = Field(default=None, gt=0)

    programs: list[SignalProgramData] = Field(default_factory=list)
    signal_groups: list[SignalGroupData] = Field(default_factory=list)
    detectors: list[DetectorData] = Field(default_factory=list)
    pedestrian_phases: list[PedestrianPhaseData] = Field(default_factory=list)
    coordination: CoordinationData | None = None
    position: PositionHint | None = None

    @model_validator(mode="after")
    def _cycle_bounds_ordered(self) -> "SignalControllerData":
        if (
            self.cycle_min_s is not None
            and self.cycle_max_s is not None
            and self.cycle_min_s > self.cycle_max_s
        ):
            raise ValueError(
                f"cycle_min_s ({self.cycle_min_s}) exceeds cycle_max_s ({self.cycle_max_s})"
            )
        return self

    def program_by_id(self, program_id: str) -> SignalProgramData | None:
        for program in self.programs:
            if program.program_id == program_id:
                return program
        return None


# ---------------------------------------------------------------------------
# Demand-side neutral records
# ---------------------------------------------------------------------------


class TurningCountData(NeutralRecord):
    """One counted movement in one time interval.

    The shape MRWA's collection standard implies (op p.59; meso p.62 — 15-min
    profiles, flat profiles forbidden) and the shape R1.4's demand seeding
    consumes: one record per (site, approach, movement, class, interval). The
    interval is stored as an explicit start/end rather than a start plus an
    assumed 15 minutes, so that a 5-minute or 60-minute source is representable
    *as what it is* and the quality gate can say so instead of an adapter
    quietly re-binning.

    ``volume_unit`` has no default on purpose: a PCU-weighted figure read as a
    vehicle count is a wrong number nobody would notice.
    """

    site_id: str = Field(min_length=1)
    approach: ApproachRef
    movement: MovementRef
    vehicle_class: VehicleClassRef
    interval_start: datetime
    interval_end: datetime
    volume: float = Field(ge=0)
    volume_unit: VolumeUnit
    source_quality_flag: str | None = Field(
        default=None,
        description="The source's own flag for this datum (estimated / factored / suspect).",
    )

    @model_validator(mode="after")
    def _interval_is_positive(self) -> "TurningCountData":
        if self.interval_end <= self.interval_start:
            raise ValueError(
                f"interval_end ({self.interval_end}) must be after "
                f"interval_start ({self.interval_start})"
            )
        return self

    @property
    def interval_minutes(self) -> float:
        return (self.interval_end - self.interval_start).total_seconds() / 60.0


class DetectorCountData(NeutralRecord):
    """A volume counted by one detector over one interval.

    **This model exists because mapping MRWA Appendix-A falsified the original
    schema.** Appendix-A A.1 lists "Detector Volume Data" among the sources
    Main Roads supplies, and a detector volume has no turning movement: a
    stop-line loop counts what crossed it, not where it went. Forcing those
    records into :class:`TurningCountData` would have required every adapter to
    invent an approach and a movement — the precise sin this layer exists to
    prevent — so the neutral model grew a record type instead. See
    docs/product/adapter-contract.md §"What Appendix A changed".

    ``vehicle_class`` is optional here (unlike on :class:`TurningCountData`)
    because loop detectors overwhelmingly report an undifferentiated count, and
    ``None`` says so honestly.
    """

    site_id: str = Field(min_length=1)
    detector_id: str = Field(min_length=1)
    controller_id: str | None = None
    interval_start: datetime
    interval_end: datetime
    volume: float = Field(ge=0)
    volume_unit: VolumeUnit
    vehicle_class: VehicleClassRef | None = None
    occupancy_percent: float | None = Field(default=None, ge=0, le=100)
    source_quality_flag: str | None = None

    @model_validator(mode="after")
    def _interval_is_positive(self) -> "DetectorCountData":
        if self.interval_end <= self.interval_start:
            raise ValueError(
                f"interval_end ({self.interval_end}) must be after "
                f"interval_start ({self.interval_start})"
            )
        return self

    @property
    def interval_minutes(self) -> float:
        return (self.interval_end - self.interval_start).total_seconds() / 60.0


class SiteMetadata(NeutralRecord):
    """What we know about the site and the extraction itself.

    Two timestamps, deliberately distinct: ``source_extracted_at`` is what the
    *file* says about when it was produced (often absent), while ``imported_at``
    is when *we* parsed it (always known, so it defaults to now). Collapsing
    them would let an unknown become a plausible-looking known.

    ``drive_side`` is the one place side-of-road is recorded; see
    :class:`TurnType` for why the movement enum stays geometric.
    """

    site_id: str = Field(min_length=1)
    site_name: str | None = None
    source_system: str = Field(
        min_length=1, description='e.g. "SCATS", "대전 스마트교차로", "TOPIS".'
    )
    jurisdiction: str | None = Field(default=None, description='e.g. "WA, Australia", "대전광역시".')
    region: str | None = None
    drive_side: DriveSide | None = None
    timezone: str | None = Field(default=None, description="IANA name, when the source states one.")
    coordinate_reference: str | None = Field(
        default=None, description='CRS of any x/y hints, e.g. "EPSG:7850", "EPSG:5186".'
    )
    collection_period: TimeWindow | None = None
    source_extracted_at: datetime | None = Field(
        default=None, description="Extraction timestamp AS STATED BY THE SOURCE."
    )
    imported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str | None = None


__all__ = [
    "CanonicalVehicleClass",
    "ClassificationScheme",
    "CompassDirection",
    "ControlType",
    "CoordinationData",
    "CoordinationPoint",
    "DetectorCountData",
    "DetectorData",
    "DetectorFunction",
    "DriveSide",
    "MovementRef",
    "NeutralRecord",
    "ApproachRef",
    "PedestrianPhaseData",
    "PedestrianProtectionType",
    "PhaseData",
    "PositionHint",
    "ProgramSourceKind",
    "Provenance",
    "SignalControllerData",
    "SignalGroupData",
    "SignalGroupKind",
    "SignalGroupWindow",
    "SignalProgramData",
    "SiteMetadata",
    "TimeWindow",
    "TurnType",
    "TurningCountData",
    "VehicleClassRef",
    "VolumeUnit",
]


def neutral_field_path_exists(path: str) -> bool:
    """Resolve a dotted ``Model.field.subfield`` path against this module.

    Exists so an adapter's field map can be *checked* rather than merely
    documented: ``tests/test_dataimport_scats_wa.py`` asserts that every
    neutral path the SCATS adapter claims to write actually names a real field
    of a real model. That assertion is the falsification test for the whole
    contract — if MRWA Appendix-A's documented fields cannot be pointed at
    real schema fields, the schema is wrong.

    Traverses ``list[Model]`` element types transparently, so
    ``SignalControllerData.programs.phases.green_s`` resolves.
    """
    parts = path.split(".")
    if not parts:
        return False
    model = globals().get(parts[0])
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        return False
    current: Any = model
    for part in parts[1:]:
        if not (isinstance(current, type) and issubclass(current, BaseModel)):
            return False
        field = current.model_fields.get(part)
        if field is None:
            return False
        current = _unwrap_model_annotation(field.annotation)
    return True


def _unwrap_model_annotation(annotation: Any) -> Any:
    """Peel ``Optional[...]``/``list[...]`` down to a BaseModel subclass when
    there is one, otherwise return the annotation unchanged."""
    seen: list[Any] = [annotation]
    while seen:
        current = seen.pop()
        if isinstance(current, type) and issubclass(current, BaseModel):
            return current
        args = getattr(current, "__args__", ())
        seen.extend(arg for arg in args if arg is not type(None))
    return annotation

"""Neutral counts -> the existing vehicle-input / composition model (R1.4).

The last leg of roadmap 1.2's "대전 신호+카운트 → 중립 스키마 → 기존 신호/입력
모델": an adapter has produced :class:`~.schema.TurningCountData` records, and
something has to turn them into the parameters Prism's shipped edit verbs
already take.

Those verbs, and what this module produces for each::

    set_vehicle_input_volume(input_no, volume, interval_no=N)
        <- VehicleInputProfile.intervals[N-1].volume_vph
    add_vehicle_input(link_no, volume_vph, ...)
        <- VehicleInputProfile.intervals[0].volume_vph
    set_vehicle_composition_share(composition_no, veh_type_no, rel_flow)
        <- CompositionShare.rel_flow

Deliberately **not** imported: ``services.agent.tools_edit``. This module emits
plain values; the caller applies them through the approval-gated edit path like
any other write. Keeping the dependency one-way means demand seeding is
testable with no Vissim, no COM and no edit session — the same rule the rest of
``dataimport`` lives under.

Three conversions happen here and nowhere else, because each is a *consumer's*
decision that the contract forbids an adapter from making:

1. **Counts to a rate.** A vehicle input is veh/**h**; a count is vehicles per
   bin. ``volume_vph = volume × 60 / interval_minutes`` is applied here,
   explicitly, with the source figure and the bin length kept beside it on
   every interval so a report can show the arithmetic. An adapter that had
   silently stored a rate would have destroyed the distinction between "142
   vehicles in 15 minutes" and "142 vehicles per hour".
2. **Movements to an approach.** A vehicle input feeds a link; the demand on
   that link is every movement out of that approach. Summing is this module's
   job, and it is where the two kinds of *total* record in the neutral model
   become dangerous — an approach-total row and an all-class 합계 row are both
   sums of records that are also present, so adding them would double-count.
   Both are excluded, by the same predicates the quality gate uses
   (``MovementRef.is_approach_total`` and ``quality.is_all_class_total``), and
   a test proves the exclusion rather than trusting it.
3. **Counts to composition weights.** ``set_vehicle_composition_share`` takes a
   *raw weight* that Vissim normalises across the composition at run time, so
   the observed vehicle count per class is already the right number — no
   percentage arithmetic, no rounding, no residual to redistribute.

What this module refuses to do is as important as what it does. It will not
map a Korean class code onto a Vissim vehicle **type number**: that mapping is
a modelling decision about the target network, not a property of the count
data, and no file in the drop states it. :class:`CompositionShare` therefore
carries the class reference verbatim and leaves ``veh_type_no`` to the caller.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportIssue, ImportResult, IssueSeverity
from .quality import is_all_class_total
from .schema import TurningCountData, VehicleClassRef, VolumeUnit

CIT_15_MIN_PROFILE = (
    "MRWA Operational Modelling Guidelines v2.0 p.59; Mesoscopic Guidelines p.62 — "
    "demand must be profiled in 15-minute intervals; flat profiles prohibited."
)


class DemandInterval(BaseModel):
    """One time interval of one vehicle input, with its arithmetic attached.

    ``interval_no`` is 1-based to match ``set_vehicle_input_volume``'s own
    numbering, so a caller writes ``interval_no=interval.interval_no`` with no
    off-by-one to reason about.
    """

    model_config = ConfigDict(extra="forbid")

    interval_no: int = Field(ge=1)
    start: datetime
    end: datetime
    volume_vph: float = Field(ge=0)
    counted_volume: float = Field(ge=0, description="The summed source figure, before scaling.")
    interval_minutes: float = Field(gt=0)
    contributing_records: int = Field(ge=1)


class VehicleInputProfile(BaseModel):
    """The per-interval demand profile for one approach of one site.

    One of these becomes one Vissim vehicle input: ``add_vehicle_input`` for
    interval 1, then ``set_vehicle_input_volume(interval_no=...)`` for the
    rest. ``link_no`` is absent on purpose — which link carries this approach
    is a property of the network, not of the survey, and the neutral model has
    no way to know it.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: str
    approach_leg_id: str
    volume_unit: VolumeUnit
    intervals: list[DemandInterval] = Field(default_factory=list)

    @property
    def peak_volume_vph(self) -> float:
        return max((interval.volume_vph for interval in self.intervals), default=0.0)

    @property
    def total_counted(self) -> float:
        return sum(interval.counted_volume for interval in self.intervals)


class CompositionShare(BaseModel):
    """One vehicle class's weight inside a vehicle composition.

    ``rel_flow`` is the observed count, fed straight to
    ``set_vehicle_composition_share``: Vissim normalises relative flows across
    the composition itself, so handing it the raw counts is both simpler and
    lossless. ``share`` is the normalised fraction, for reports only.
    """

    model_config = ConfigDict(extra="forbid")

    vehicle_class: VehicleClassRef
    rel_flow: float = Field(ge=0)
    share: float = Field(ge=0, le=1)
    counted_records: int = Field(ge=1)


class CompositionProfile(BaseModel):
    """Every class weight for one site.

    Per-site rather than per-approach because a Vissim vehicle composition is
    shared by every input that references it; splitting composition by approach
    is a modelling choice that needs more than one composition object, and the
    caller makes it by calling with a narrower :class:`ImportResult`.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: str
    shares: list[CompositionShare] = Field(default_factory=list)

    @property
    def unmapped_class_codes(self) -> list[str]:
        """Classes with no canonical bucket — the caller must decide which
        Vissim vehicle type each becomes; the adapter declined to guess."""
        return sorted(
            share.vehicle_class.code
            for share in self.shares
            if share.vehicle_class.canonical is None
        )


class DemandSeeding(BaseModel):
    """Everything the demand seeder produced, plus why it left things out."""

    model_config = ConfigDict(extra="forbid")

    profiles: list[VehicleInputProfile] = Field(default_factory=list)
    compositions: list[CompositionProfile] = Field(default_factory=list)
    issues: list[ImportIssue] = Field(default_factory=list)

    @property
    def refusals(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.REFUSE]

    @property
    def ok(self) -> bool:
        return not self.refusals

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [
            issue.code
            for issue in self.issues
            if severity is None or issue.severity is severity
        ]

    def profile_for(self, site_id: str, approach_leg_id: str) -> VehicleInputProfile | None:
        for profile in self.profiles:
            if profile.site_id == site_id and profile.approach_leg_id == approach_leg_id:
                return profile
        return None


def seedable_counts(counts: Sequence[TurningCountData]) -> list[TurningCountData]:
    """The records a demand sum may include: no total of any kind.

    Two independent axes of "this row is a sum of other rows" exist in the
    neutral model and a consumer that misses either double-counts silently:

    * ``movement.is_approach_total`` — the approach axis, and
    * an all-class record (``quality.is_all_class_total``) — the class axis,
      which the Daejeon export's per-movement 합계 column populates.

    Exposed as a function, not inlined, so anything else that sums neutral
    counts (routing seeding in roadmap 3.2 next) shares one definition.
    """
    return [
        count
        for count in counts
        if not count.movement.is_approach_total and not is_all_class_total(count)
    ]


def _interval_minutes(count: TurningCountData) -> float:
    return (count.interval_end - count.interval_start).total_seconds() / 60.0


def build_demand_seeding(
    result: ImportResult,
    *,
    window: tuple[datetime, datetime] | None = None,
) -> DemandSeeding:
    """Turn an :class:`ImportResult`'s counts into vehicle-input parameters.

    ``window`` restricts to intervals starting inside ``[start, end)`` — the
    normal case, since a five-week survey seeds a one-hour model run. Selecting
    a window is the modeller's decision and is recorded as an INFO, never
    guessed from the data (a "peak" this module picked for itself would be a
    modelling assumption wearing an import's clothes).

    Always returns; problems are :class:`ImportIssue` values, and a REFUSE
    means the seeding must not be applied without a recorded human override —
    the same doctrine as the gate and the edit layer.
    """
    seeding = DemandSeeding()
    counts = list(result.counts)
    if window is not None:
        start, end = window
        counts = [count for count in counts if start <= count.interval_start < end]
        seeding.issues.append(
            ImportIssue(
                severity=IssueSeverity.INFO,
                code="demand_window_selected",
                message=(
                    f"Demand was seeded from intervals starting in "
                    f"[{start.isoformat()}, {end.isoformat()}); the rest of the survey was not "
                    "used. Which window represents the modelled period is the modeller's "
                    "decision and is recorded here, not inferred."
                ),
                context={"start": start.isoformat(), "end": end.isoformat()},
            )
        )

    if not counts:
        seeding.issues.append(
            ImportIssue(
                severity=IssueSeverity.REFUSE,
                code="demand_no_counts_in_window",
                message=(
                    "No turning counts are available for the requested window, so no vehicle "
                    "input can be seeded. An input written at zero would assert there is no "
                    "demand; there is no observation either way."
                ),
            )
        )
        return seeding

    pcu = [count for count in counts if count.volume_unit is not VolumeUnit.VEHICLES]
    if pcu:
        seeding.issues.append(
            ImportIssue(
                severity=IssueSeverity.REFUSE,
                code="demand_volume_unit_not_vehicles",
                message=(
                    f"{len(pcu)} count record(s) are not in vehicles (e.g. "
                    f"{pcu[0].volume_unit.value}). A vehicle input takes veh/h; converting a "
                    "PCU-weighted figure needs the PCU factors that weighted it, which the "
                    "import does not carry."
                ),
                context={"records": len(pcu), "units": sorted({c.volume_unit.value for c in pcu})},
            )
        )
        return seeding

    seedable = seedable_counts(counts)
    excluded = len(counts) - len(seedable)
    if excluded:
        seeding.issues.append(
            ImportIssue(
                severity=IssueSeverity.INFO,
                code="demand_totals_excluded_from_sum",
                message=(
                    f"{excluded} stated-total record(s) were excluded from the demand sum "
                    "(approach totals and all-class totals are sums of records that are also "
                    "present; including them would double-count)."
                ),
                context={"excluded_records": excluded, "summed_records": len(seedable)},
            )
        )
    if not seedable:
        seeding.issues.append(
            ImportIssue(
                severity=IssueSeverity.REFUSE,
                code="demand_only_totals_available",
                message=(
                    "Every count in the window is a stated total, so there is no per-movement "
                    "figure to sum without double-counting."
                ),
            )
        )
        return seeding

    seeding.profiles.extend(_build_profiles(seedable, seeding))
    seeding.compositions.extend(_build_compositions(seedable))
    _report_unmapped_classes(seeding)
    return seeding


def _build_profiles(
    counts: Sequence[TurningCountData], seeding: DemandSeeding
) -> list[VehicleInputProfile]:
    per_approach: dict[tuple[str, str], list[TurningCountData]] = defaultdict(list)
    for count in counts:
        per_approach[(count.site_id, count.approach.leg_id)].append(count)

    profiles: list[VehicleInputProfile] = []
    for (site_id, leg_id), records in sorted(per_approach.items()):
        lengths = sorted({round(_interval_minutes(record), 6) for record in records})
        if len(lengths) > 1:
            seeding.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="demand_mixed_interval_lengths",
                    message=(
                        f"Site {site_id} approach {leg_id} mixes interval lengths "
                        f"{lengths} minutes. Re-binning to a single profile is a decision with "
                        "consequences for the peak, so it is not made here."
                    ),
                    subject=f"site {site_id} / approach {leg_id}",
                    citation=CIT_15_MIN_PROFILE,
                    context={"interval_minutes": lengths},
                )
            )
            continue

        binned: dict[tuple[datetime, datetime], list[TurningCountData]] = defaultdict(list)
        for record in records:
            binned[(record.interval_start, record.interval_end)].append(record)

        intervals: list[DemandInterval] = []
        for number, ((start, end), bucket) in enumerate(sorted(binned.items()), start=1):
            minutes = (end - start).total_seconds() / 60.0
            counted = sum(record.volume for record in bucket)
            intervals.append(
                DemandInterval(
                    interval_no=number,
                    start=start,
                    end=end,
                    volume_vph=counted * 60.0 / minutes,
                    counted_volume=counted,
                    interval_minutes=minutes,
                    contributing_records=len(bucket),
                )
            )
        profiles.append(
            VehicleInputProfile(
                site_id=site_id,
                approach_leg_id=leg_id,
                volume_unit=records[0].volume_unit,
                intervals=intervals,
            )
        )
    return profiles


def _build_compositions(counts: Sequence[TurningCountData]) -> list[CompositionProfile]:
    per_site: dict[str, dict[tuple[str, str], list[TurningCountData]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for count in counts:
        key = (count.vehicle_class.scheme.value, count.vehicle_class.code)
        per_site[count.site_id][key].append(count)

    profiles: list[CompositionProfile] = []
    for site_id, by_class in sorted(per_site.items()):
        totals = {key: sum(r.volume for r in records) for key, records in by_class.items()}
        grand = sum(totals.values())
        shares: list[CompositionShare] = []
        for key in sorted(totals):
            records = by_class[key]
            shares.append(
                CompositionShare(
                    vehicle_class=records[0].vehicle_class,
                    rel_flow=totals[key],
                    share=(totals[key] / grand) if grand else 0.0,
                    counted_records=len(records),
                )
            )
        profiles.append(CompositionProfile(site_id=site_id, shares=shares))
    return profiles


def _report_unmapped_classes(seeding: DemandSeeding) -> None:
    unmapped = sorted(
        {code for profile in seeding.compositions for code in profile.unmapped_class_codes}
    )
    if not unmapped:
        return
    seeding.issues.append(
        ImportIssue(
            severity=IssueSeverity.INFO,
            code="demand_vehicle_type_mapping_required",
            message=(
                f"{len(unmapped)} class code(s) carry no canonical bucket "
                f"({', '.join(unmapped)}). Which Vissim vehicle type each becomes is a "
                "modelling decision about the target network — no file in the survey states "
                "it — so set_vehicle_composition_share's veh_type_no must be chosen by the "
                "modeller, not derived here."
            ),
            context={"codes": unmapped},
        )
    )


__all__ = [
    "CompositionProfile",
    "CompositionShare",
    "DemandInterval",
    "DemandSeeding",
    "VehicleInputProfile",
    "build_demand_seeding",
    "seedable_counts",
]

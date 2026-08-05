"""Data-quality gate over neutral records (TK requirement R2.5).

MRWA's own collection standards, expressed as checks that run on
``schema.py`` records and report through :class:`ImportIssue` — so the gate is
region-agnostic by construction: it never sees a SCATS file or a Daejeon
export, only the neutral model, and therefore protects adapter #3 exactly as
well as adapter #1.

The refuse/warn line
--------------------
Every check declares one of two tiers, and the line between them is a single
rule, applied consistently:

**REFUSE** when either

  (a) *the data contradicts itself*, so any number computed from it is wrong —
      overlapping intervals double-count, a stated approach total that
      disagrees with its own movement rows, a stated all-class total that
      disagrees with its own per-class rows, a signal group pointing at a phase
      that does not exist; or
  (b) *it fails an explicit numeric minimum stated by the governing
      authority* — the ≥3 h peak coverage rule being the one we have in hand.

**WARN** when the data is coherent and meets the minimums but is
*unrepresentative, unusual, or missing a recommended property* — a Monday
instead of a neutral Tue/Wed/Thu, 5-minute or hourly bins instead of the
recommended 15, gaps inside the collection period, totals-only class coverage,
implausibly high flow rates.

The distinction is deliberately *not* "how bad does it feel". (a) and (b) are
the two cases where proceeding silently produces a model that is wrong rather
than merely thin — the same doctrine as the edit layer, one stage earlier. A
REFUSE blocks by default and is overridable only by a recorded human decision;
a WARN rides along on the result and belongs in the report.

**INFO** carries no judgement: what was parsed, what was absent and why, and —
importantly — what we *cannot* check (we have no public-holiday calendar, so
we say so rather than pretending a weekday test is a neutral-day test).

Nothing here imports Vissim and nothing here needs a licence.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportIssue, ImportResult, IssueSeverity
from .schema import (
    CanonicalVehicleClass,
    ClassificationScheme,
    ProgramSourceKind,
    SignalControllerData,
    TurningCountData,
)

# ---------------------------------------------------------------------------
# Authority citations
#
# Where we hold a page number, it is here verbatim. Where we hold the rule but
# not yet the page (the collection minimums came to us via the meeting brief,
# not the PDF), the citation SAYS the page cite is pending rather than
# inventing one — the same honesty rule the adapters live under.
# ---------------------------------------------------------------------------

CIT_15_MIN = (
    "MRWA Operational Modelling Guidelines v2.0 p.59; Mesoscopic Guidelines p.62 — "
    "demand must be profiled in 15-minute intervals; flat profiles prohibited."
)
CIT_COLLECTION_MINIMUMS = (
    "MRWA Operational Modelling Guidelines v2.0 — survey collection minimums "
    "(≥3 h per peak period; neutral-day rule). Page cite pending; rule recorded in "
    "docs/meeting-brief-tk-mrwa.md §4."
)
CIT_HEAVY_VEHICLES = (
    "MRWA Operational Modelling Guidelines v2.0 p.19 — WA heavy vehicles differ from "
    "software defaults; class composition must be evidenced, not assumed."
)
CIT_SCATS_CODING = (
    "MRWA Operational Modelling Guidelines v2.0 §5 signal coding — signal-group "
    "numbering must match SCATS; SCATS fractional values always round up."
)
CIT_APPENDIX_A_A2 = (
    "MRWA Appendix A §A.2 (p.11) — the stretch phase defines cycle boundaries and "
    "Σ t̄ᵢ = c̄ over complete cycles."
)


class QualityThresholds(BaseModel):
    """Every number the gate judges by, in one place and overridable.

    Defaults are the authority's where an authority states one, and a
    deliberately generous engineering band where none does (the point of a
    sanity band is to catch a decimal-point error, not to second-guess a
    surveyor).
    """

    model_config = ConfigDict(extra="forbid")

    min_peak_coverage_h: float = Field(
        default=3.0, gt=0, description="MRWA: minimum survey coverage per peak period."
    )
    preferred_interval_minutes: float = Field(
        default=15.0, gt=0, description="MRWA-required profiling interval."
    )
    min_intervals_per_series: int = Field(
        default=12,
        ge=1,
        description="3 h at 15 min = 12 intervals; a movement thinner than this has holes.",
    )
    approach_total_tolerance_fraction: float = Field(
        default=0.02,
        ge=0,
        description="Movement rows may differ from a stated approach total by this share…",
    )
    approach_total_tolerance_absolute: float = Field(
        default=2.0, ge=0, description="…or this many vehicles, whichever is larger."
    )
    class_total_tolerance_fraction: float = Field(
        default=0.02,
        ge=0,
        description="Per-class rows may differ from a stated all-class total by this share…",
    )
    class_total_tolerance_absolute: float = Field(
        default=2.0, ge=0, description="…or this many vehicles, whichever is larger."
    )
    movement_rate_warn_vph: float = Field(
        default=3000.0, gt=0, description="Per-movement flow rate above which we flag a typo."
    )
    approach_rate_warn_vph: float = Field(
        default=9000.0, gt=0, description="Same, for approach-total rows."
    )
    cycle_sum_tolerance_s: float = Field(
        default=2.0,
        ge=0,
        description="Phase durations may miss the stated cycle by this much (SCATS round-up)…",
    )
    cycle_sum_tolerance_fraction: float = Field(
        default=0.05, ge=0, description="…or this share of the cycle, whichever is larger."
    )
    #: Neutral days per the MRWA rule as we hold it: mid-week, school term,
    #: no public holiday. We can only verify the weekday half — see
    #: ``neutral_day_unverifiable``.
    neutral_weekdays: tuple[int, ...] = Field(
        default=(1, 2, 3), description="Python weekday() values: Tue/Wed/Thu."
    )


@dataclass(frozen=True)
class QualityCheck:
    """One registered check. ``tiers`` documents which severities it can emit
    so the doc and the code cannot drift apart."""

    id: str
    title: str
    tiers: tuple[IssueSeverity, ...]
    citation: str | None
    fn: Callable[[ImportResult, QualityThresholds], list[ImportIssue]]


# ---------------------------------------------------------------------------
# Series helpers
#
# A "series" is one time series of counts: one movement of one class at one
# approach of one site. Interval regularity, gaps and minimum observations are
# all properties of a series, not of the file.
# ---------------------------------------------------------------------------

SeriesKey = tuple[str, str, str, str]


def movement_key(count: TurningCountData) -> str:
    """The movement half of :func:`series_key`, on its own.

    Shared with :func:`check_class_total_consistency`, which buckets along the
    *class* axis and therefore needs everything-but-the-class as its key."""
    if count.movement.is_approach_total:
        return "TOTAL"
    turn = count.movement.turn.value if count.movement.turn else (count.movement.turn_raw or "?")
    return f"{turn}->{count.movement.to_leg_id or ''}"


def is_all_class_total(count: TurningCountData) -> bool:
    """True when this record is the source's own *all classes* figure.

    Two spellings mean the same thing and both occur in real exports: an
    explicitly unclassified scheme (a file that only gives one undifferentiated
    number), and a classified scheme whose row is the stated total across its
    own classes (the Daejeon export's 합계 column). Either way the record is a
    *sum of the others*, so no consumer may add it to them."""
    return (
        count.vehicle_class.scheme is ClassificationScheme.UNCLASSIFIED
        or count.vehicle_class.canonical is CanonicalVehicleClass.ALL_VEHICLES
    )


def series_key(count: TurningCountData) -> SeriesKey:
    klass = f"{count.vehicle_class.scheme.value}:{count.vehicle_class.code}"
    return (count.site_id, count.approach.leg_id, movement_key(count), klass)


def _series_label(key: SeriesKey) -> str:
    site, approach, movement, klass = key
    return f"site {site} / approach {approach} / {movement} / class {klass}"


def _group_series(counts: Sequence[TurningCountData]) -> dict[SeriesKey, list[TurningCountData]]:
    grouped: dict[SeriesKey, list[TurningCountData]] = defaultdict(list)
    for count in counts:
        grouped[series_key(count)].append(count)
    for records in grouped.values():
        records.sort(key=lambda c: c.interval_start)
    return dict(grouped)


def _merge_intervals(
    spans: Iterable[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    ordered = sorted(spans)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# ---------------------------------------------------------------------------
# Count checks
# ---------------------------------------------------------------------------


def check_counts_present(result: ImportResult, thresholds: QualityThresholds) -> list[ImportIssue]:
    """INFO only. A signal-only import is a legitimate import; the count checks
    simply have nothing to say about it, and saying nothing silently would be
    indistinguishable from saying "all clear"."""
    if result.counts:
        return []
    detail = (
        " Detector counts are present but carry no movement, so routing cannot be seeded "
        "from them."
        if result.detector_counts
        else ""
    )
    return [
        ImportIssue(
            severity=IssueSeverity.INFO,
            code="no_count_data",
            message=(
                "No turning-count records were imported; the movement-level demand checks "
                "were skipped." + detail
            ),
        )
    ]


def check_interval_overlap(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """REFUSE. Two intervals of the same series that overlap double-count the
    same vehicles: every total derived from the series is wrong, and nothing
    downstream can detect it. This is refuse case (a) in its purest form."""
    issues: list[ImportIssue] = []
    for key, records in _group_series(result.counts).items():
        overlaps: list[str] = []
        for previous, current in zip(records, records[1:]):
            if current.interval_start < previous.interval_end:
                overlaps.append(
                    f"{previous.interval_start.isoformat()}–{previous.interval_end.isoformat()}"
                    f" overlaps {current.interval_start.isoformat()}–"
                    f"{current.interval_end.isoformat()}"
                )
        if overlaps:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="count_intervals_overlap",
                    message=(
                        f"{len(overlaps)} overlapping count interval(s) in {_series_label(key)} — "
                        "the same vehicles are counted twice and every derived total is wrong."
                    ),
                    subject=_series_label(key),
                    citation=CIT_15_MIN,
                    context={"overlaps": overlaps},
                )
            )
    return issues


def check_interval_regularity(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """WARN. Bin length and gaps.

    Non-15-minute bins and holes inside a series are *coherent* data — the
    numbers that are there are right. They are warn-worthy because MRWA
    requires 15-minute profiling (op p.59) and because a consumer that treats a
    gap as zero demand would be wrong; making the gap visible is exactly what
    stops that. Re-binning here would be the adapter sin one layer down.
    """
    issues: list[ImportIssue] = []
    for key, records in _group_series(result.counts).items():
        durations = sorted({round(record.interval_minutes, 6) for record in records})
        if len(durations) > 1:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="count_interval_duration_mixed",
                    message=(
                        f"{_series_label(key)} mixes interval lengths {durations} minutes; "
                        "a single profiling interval is required before the series can be "
                        "used as a demand profile."
                    ),
                    subject=_series_label(key),
                    citation=CIT_15_MIN,
                    context={"durations_minutes": durations},
                )
            )
        elif durations and durations[0] != thresholds.preferred_interval_minutes:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="count_interval_not_preferred_length",
                    message=(
                        f"{_series_label(key)} is binned in {durations[0]:g}-minute intervals, "
                        f"not the required {thresholds.preferred_interval_minutes:g}-minute "
                        "profile."
                    ),
                    subject=_series_label(key),
                    citation=CIT_15_MIN,
                    context={"interval_minutes": durations[0]},
                )
            )

        gaps = [
            f"{previous.interval_end.isoformat()}–{current.interval_start.isoformat()}"
            for previous, current in zip(records, records[1:])
            if current.interval_start > previous.interval_end
        ]
        if gaps:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="count_interval_gaps",
                    message=(
                        f"{len(gaps)} gap(s) inside {_series_label(key)} — these periods were "
                        "not observed and must not be read as zero demand."
                    ),
                    subject=_series_label(key),
                    citation=CIT_15_MIN,
                    context={"gaps": gaps},
                )
            )
    return issues


def check_peak_coverage(result: ImportResult, thresholds: QualityThresholds) -> list[ImportIssue]:
    """REFUSE below the authority minimum; INFO about what we cannot verify.

    Refuse case (b): MRWA states a numeric minimum of 3 h of survey per peak
    period. We measure the longest *contiguous* observed block per site per
    day — a site surveyed 07:00–10:00 and 16:00–19:00 passes, a site surveyed
    for one hour does not.

    We deliberately do not claim the block *is* a peak: nothing in the neutral
    model says which hours are peak at this site, so an INFO says so rather
    than a check quietly implying we checked.
    """
    issues: list[ImportIssue] = []
    per_site_day: dict[tuple[str, date], list[tuple[datetime, datetime]]] = defaultdict(list)
    for count in result.counts:
        per_site_day[(count.site_id, count.interval_start.date())].append(
            (count.interval_start, count.interval_end)
        )

    for (site_id, day), spans in sorted(per_site_day.items(), key=lambda item: (item[0][0], item[0][1])):
        blocks = _merge_intervals(spans)
        longest_h = max((end - start).total_seconds() / 3600.0 for start, end in blocks)
        if longest_h + 1e-9 < thresholds.min_peak_coverage_h:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="peak_coverage_below_minimum",
                    message=(
                        f"Site {site_id} on {day.isoformat()}: longest continuous survey block is "
                        f"{longest_h:.2f} h, below the required "
                        f"{thresholds.min_peak_coverage_h:g} h per peak period."
                    ),
                    subject=f"site {site_id} / {day.isoformat()}",
                    citation=CIT_COLLECTION_MINIMUMS,
                    context={
                        "longest_block_h": round(longest_h, 3),
                        "required_h": thresholds.min_peak_coverage_h,
                        "blocks": [
                            f"{start.isoformat()}–{end.isoformat()}" for start, end in blocks
                        ],
                    },
                )
            )

    if per_site_day:
        issues.append(
            ImportIssue(
                severity=IssueSeverity.INFO,
                code="peak_period_definition_unavailable",
                message=(
                    "Coverage was measured as the longest continuous observed block; the neutral "
                    "model carries no peak-period definition, so whether the block covers the "
                    "actual peak has NOT been verified."
                ),
                citation=CIT_COLLECTION_MINIMUMS,
            )
        )
    return issues


def check_neutral_day(result: ImportResult, thresholds: QualityThresholds) -> list[ImportIssue]:
    """WARN on non-mid-week survey dates; INFO on the half we cannot verify.

    Warn, not refuse: a Monday count is a real count of a real Monday. It is
    unrepresentative, which is a judgement for the modeller to record, not a
    contradiction that makes the numbers wrong.

    The neutral-day rule also excludes public and school holidays. We hold no
    calendar for either country, so we say we could not check rather than
    letting a weekday test masquerade as a neutral-day test.
    """
    issues: list[ImportIssue] = []
    days: dict[date, int] = {}
    for count in result.counts:
        days[count.interval_start.date()] = count.interval_start.weekday()
    if not days:
        return issues

    off_days = sorted(day for day, weekday in days.items() if weekday not in thresholds.neutral_weekdays)
    if off_days:
        issues.append(
            ImportIssue(
                severity=IssueSeverity.WARN,
                code="non_neutral_survey_day",
                message=(
                    f"{len(off_days)} survey date(s) fall outside the neutral mid-week window "
                    f"(Tue–Thu): {', '.join(day.isoformat() for day in off_days)}."
                ),
                citation=CIT_COLLECTION_MINIMUMS,
                context={"dates": [day.isoformat() for day in off_days]},
            )
        )
    issues.append(
        ImportIssue(
            severity=IssueSeverity.INFO,
            code="neutral_day_holiday_status_unverified",
            message=(
                "Public-holiday and school-term status could not be checked — no holiday "
                "calendar is supplied for this jurisdiction. Only the weekday half of the "
                "neutral-day rule was tested."
            ),
            citation=CIT_COLLECTION_MINIMUMS,
        )
    )
    return issues


def check_minimum_observations(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """WARN. A movement observed for fewer intervals than the site's minimum
    has holes the site-level coverage check cannot see. Coherent data,
    insufficient sample: warn."""
    issues: list[ImportIssue] = []
    for key, records in _group_series(result.counts).items():
        if len(records) < thresholds.min_intervals_per_series:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="series_observations_below_minimum",
                    message=(
                        f"{_series_label(key)} has {len(records)} interval(s), below the "
                        f"{thresholds.min_intervals_per_series} expected from "
                        f"{thresholds.min_peak_coverage_h:g} h at "
                        f"{thresholds.preferred_interval_minutes:g}-minute bins."
                    ),
                    subject=_series_label(key),
                    citation=CIT_COLLECTION_MINIMUMS,
                    context={"intervals": len(records)},
                )
            )
    return issues


def check_approach_total_consistency(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """REFUSE on a real mismatch, WARN inside rounding tolerance.

    When a source states both an approach total and the movement rows that
    compose it, the two are a self-check the source handed us. A material
    disagreement means at least one of them is wrong and nothing downstream can
    tell which — refuse case (a). A disagreement inside tolerance is the
    rounding the source's own processing introduced: worth recording, not worth
    blocking.
    """
    issues: list[ImportIssue] = []
    totals: dict[tuple[str, str, str, datetime, datetime], float] = {}
    parts: dict[tuple[str, str, str, datetime, datetime], float] = defaultdict(float)

    for count in result.counts:
        bucket = (
            count.site_id,
            count.approach.leg_id,
            f"{count.vehicle_class.scheme.value}:{count.vehicle_class.code}",
            count.interval_start,
            count.interval_end,
        )
        if count.movement.is_approach_total:
            totals[bucket] = totals.get(bucket, 0.0) + count.volume
        else:
            parts[bucket] += count.volume

    for bucket, stated in sorted(totals.items(), key=lambda item: str(item[0])):
        if bucket not in parts:
            continue
        summed = parts[bucket]
        difference = abs(stated - summed)
        tolerance = max(
            thresholds.approach_total_tolerance_absolute,
            stated * thresholds.approach_total_tolerance_fraction,
        )
        if difference == 0:
            continue
        site, leg, klass, start, end = bucket
        subject = f"site {site} / approach {leg} / class {klass} / {start.isoformat()}"
        severity = IssueSeverity.REFUSE if difference > tolerance else IssueSeverity.WARN
        code = (
            "approach_total_mismatch"
            if severity is IssueSeverity.REFUSE
            else "approach_total_rounding_difference"
        )
        issues.append(
            ImportIssue(
                severity=severity,
                code=code,
                message=(
                    f"{subject}: stated approach total {stated:g} vs movement sum {summed:g} "
                    f"(difference {difference:g}, tolerance {tolerance:g})."
                ),
                subject=subject,
                context={
                    "stated_total": stated,
                    "movement_sum": summed,
                    "difference": difference,
                    "tolerance": tolerance,
                },
            )
        )
    return issues


def check_class_total_consistency(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """REFUSE on a real mismatch, WARN inside rounding tolerance.

    The class-axis twin of :func:`check_approach_total_consistency`, and it
    exists because the first real Korean export has one: the Daejeon
    스마트교차로 workbook states, for every movement in every 15-minute bin,
    six per-class figures **and** their 합계. That is a self-check the source
    handed us, on the axis the approach check cannot see — the approach check
    buckets *by* class, so a class total and its parts never meet there.

    Region-agnostic by construction: the check knows nothing about Korea, only
    that a record flagged as an all-class total must equal the sum of the
    classified records sharing its site, approach, movement and interval. A
    material disagreement means at least one of the two is wrong and nothing
    downstream can tell which — refuse case (a). Inside tolerance it is the
    source's own rounding: recorded, not blocking.

    Silent when a bucket has only a total (that is
    :func:`check_class_coverage`'s "totals only" warning) or only parts
    (the source stated no total, so there is nothing to check against).
    """
    issues: list[ImportIssue] = []
    ClassBucket = tuple[str, str, str, datetime, datetime]
    totals: dict[ClassBucket, float] = {}
    parts: dict[ClassBucket, float] = defaultdict(float)
    part_codes: dict[ClassBucket, set[str]] = defaultdict(set)

    for count in result.counts:
        bucket: ClassBucket = (
            count.site_id,
            count.approach.leg_id,
            movement_key(count),
            count.interval_start,
            count.interval_end,
        )
        if is_all_class_total(count):
            totals[bucket] = totals.get(bucket, 0.0) + count.volume
        else:
            parts[bucket] += count.volume
            part_codes[bucket].add(count.vehicle_class.code)

    for bucket, stated in sorted(totals.items(), key=lambda item: str(item[0])):
        if bucket not in parts:
            continue
        summed = parts[bucket]
        difference = abs(stated - summed)
        if difference == 0:
            continue
        tolerance = max(
            thresholds.class_total_tolerance_absolute,
            stated * thresholds.class_total_tolerance_fraction,
        )
        site, leg, movement, start, _end = bucket
        subject = f"site {site} / approach {leg} / {movement} / {start.isoformat()}"
        severity = IssueSeverity.REFUSE if difference > tolerance else IssueSeverity.WARN
        code = (
            "class_total_mismatch"
            if severity is IssueSeverity.REFUSE
            else "class_total_rounding_difference"
        )
        issues.append(
            ImportIssue(
                severity=severity,
                code=code,
                message=(
                    f"{subject}: stated all-class total {stated:g} vs the sum of its "
                    f"{len(part_codes[bucket])} per-class row(s) {summed:g} (difference "
                    f"{difference:g}, tolerance {tolerance:g})."
                ),
                subject=subject,
                citation=CIT_HEAVY_VEHICLES,
                context={
                    "stated_total": stated,
                    "class_sum": summed,
                    "difference": difference,
                    "tolerance": tolerance,
                    "class_codes": sorted(part_codes[bucket]),
                },
            )
        )
    return issues


def check_class_coverage(result: ImportResult, thresholds: QualityThresholds) -> list[ImportIssue]:
    """WARN on totals-only data; INFO on unmapped class codes.

    Vehicle composition is a required model input and MRWA is explicit that WA
    heavy vehicles differ from software defaults (op p.19). Totals-only counts
    are perfectly good counts — they simply cannot seed a composition, and the
    modeller must source the split elsewhere. That is a judgement call, so:
    warn.
    """
    issues: list[ImportIssue] = []
    per_site: dict[str, list[TurningCountData]] = defaultdict(list)
    for count in result.counts:
        per_site[count.site_id].append(count)

    for site_id, records in sorted(per_site.items()):
        classified = [record for record in records if not is_all_class_total(record)]
        if not classified:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="class_coverage_totals_only",
                    message=(
                        f"Site {site_id} carries only unclassified totals; vehicle composition "
                        "(heavy-vehicle share in particular) cannot be seeded from this data."
                    ),
                    subject=f"site {site_id}",
                    citation=CIT_HEAVY_VEHICLES,
                )
            )
        unmapped = sorted(
            {
                f"{record.vehicle_class.scheme.value}:{record.vehicle_class.code}"
                for record in classified
                if record.vehicle_class.canonical is None
            }
        )
        if unmapped:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.INFO,
                    code="vehicle_class_unmapped",
                    message=(
                        f"Site {site_id}: {len(unmapped)} class code(s) were kept verbatim with no "
                        f"canonical mapping ({', '.join(unmapped)}). The adapter declined to guess."
                    ),
                    subject=f"site {site_id}",
                    context={"codes": unmapped},
                )
            )
    return issues


def check_volume_sanity(result: ImportResult, thresholds: QualityThresholds) -> list[ImportIssue]:
    """WARN. A generous band that catches a misplaced decimal or a
    minutes/hours unit slip, not an unusual-but-real flow."""
    issues: list[ImportIssue] = []
    for count in result.counts:
        hours = (count.interval_end - count.interval_start).total_seconds() / 3600.0
        if hours <= 0:
            continue
        rate = count.volume / hours
        limit = (
            thresholds.approach_rate_warn_vph
            if count.movement.is_approach_total
            else thresholds.movement_rate_warn_vph
        )
        if rate > limit:
            key = series_key(count)
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="volume_rate_implausible",
                    message=(
                        f"{_series_label(key)} at {count.interval_start.isoformat()}: "
                        f"{count.volume:g} {count.volume_unit.value} in {hours * 60:g} min = "
                        f"{rate:.0f}/h, above the {limit:.0f}/h plausibility band."
                    ),
                    subject=_series_label(key),
                    provenance=count.provenance,
                    context={"rate_per_hour": round(rate, 1), "limit_per_hour": limit},
                )
            )
    return issues


def check_detector_count_intervals(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """REFUSE on overlap, WARN on holes — the same two rules as turning counts,
    applied to the record type Appendix-A's Detector Volume Data lands in.

    Detector series are keyed by detector, not by movement, so they get their
    own codes rather than being folded into the movement checks: a reviewer
    looking at a refusal needs to know whether the double count is in the
    turning survey or in the loops.
    """
    issues: list[ImportIssue] = []
    grouped: dict[tuple[str, str], list[tuple[datetime, datetime]]] = defaultdict(list)
    for record in result.detector_counts:
        grouped[(record.site_id, record.detector_id)].append(
            (record.interval_start, record.interval_end)
        )

    for (site_id, detector_id), spans in sorted(grouped.items()):
        label = f"site {site_id} / detector {detector_id}"
        ordered = sorted(spans)
        overlaps = [
            f"{previous[0].isoformat()}–{previous[1].isoformat()} overlaps "
            f"{current[0].isoformat()}–{current[1].isoformat()}"
            for previous, current in zip(ordered, ordered[1:])
            if current[0] < previous[1]
        ]
        if overlaps:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="detector_count_intervals_overlap",
                    message=(
                        f"{len(overlaps)} overlapping detector interval(s) in {label} — the same "
                        "vehicles are counted twice."
                    ),
                    subject=label,
                    citation=CIT_15_MIN,
                    context={"overlaps": overlaps},
                )
            )
        gaps = [
            f"{previous[1].isoformat()}–{current[0].isoformat()}"
            for previous, current in zip(ordered, ordered[1:])
            if current[0] > previous[1]
        ]
        if gaps:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="detector_count_interval_gaps",
                    message=(
                        f"{len(gaps)} gap(s) in {label} — unobserved periods, not zero flow."
                    ),
                    subject=label,
                    citation=CIT_15_MIN,
                    context={"gaps": gaps},
                )
            )
        durations = sorted(
            {round((end - start).total_seconds() / 60.0, 6) for start, end in ordered}
        )
        if len(durations) > 1 or (
            durations and durations[0] != thresholds.preferred_interval_minutes
        ):
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="detector_count_interval_not_preferred_length",
                    message=(
                        f"{label} is binned in {durations} minute intervals, not a uniform "
                        f"{thresholds.preferred_interval_minutes:g}-minute profile."
                    ),
                    subject=label,
                    citation=CIT_15_MIN,
                    context={"durations_minutes": durations},
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Signal checks
# ---------------------------------------------------------------------------


def _controller_reference_problems(controller: SignalControllerData) -> list[str]:
    known_phases = {
        phase.phase_id for program in controller.programs for phase in program.phases
    }
    known_groups = {group.signal_group_id for group in controller.signal_groups}
    problems: list[str] = []

    for group in controller.signal_groups:
        for phase_id in group.phase_ids or []:
            if phase_id not in known_phases:
                problems.append(
                    f"signal group {group.signal_group_id} claims phase {phase_id!r}, "
                    "which no program declares"
                )
    for program in controller.programs:
        for window in program.signal_group_windows:
            if window.signal_group_id not in known_groups:
                problems.append(
                    f"program {program.program_id} has a green window for signal group "
                    f"{window.signal_group_id!r}, which the controller does not declare"
                )
    for detector in controller.detectors:
        if detector.signal_group_id and detector.signal_group_id not in known_groups:
            problems.append(
                f"detector {detector.detector_id} references signal group "
                f"{detector.signal_group_id!r}, which the controller does not declare"
            )
    for ped in controller.pedestrian_phases:
        if ped.signal_group_id and ped.signal_group_id not in known_groups:
            problems.append(
                f"pedestrian phase {ped.pedestrian_phase_id} references signal group "
                f"{ped.signal_group_id!r}, which the controller does not declare"
            )
    return problems


def check_signal_reference_integrity(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """REFUSE. A dangling reference is refuse case (a): the import contradicts
    itself, and a genesis build that resolved it by *creating* the missing
    object would be inventing network."""
    issues: list[ImportIssue] = []
    for controller in result.controllers:
        problems = _controller_reference_problems(controller)
        if problems:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="signal_reference_dangling",
                    message=(
                        f"Controller {controller.controller_id}: {len(problems)} dangling "
                        f"reference(s) — {problems[0]}"
                        + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
                    ),
                    subject=f"controller {controller.controller_id}",
                    citation=CIT_SCATS_CODING,
                    context={"problems": problems},
                )
            )
    return issues


def check_program_cycle_consistency(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """REFUSE on a material mismatch, WARN inside the SCATS round-up band.

    MRWA Appendix-A A.2: phase activation times sum to the cycle. A program
    whose phases do not add up to its own stated cycle is self-contradictory,
    and picking either side would be inventing. Small residues are expected —
    §5 requires SCATS fractional values to be rounded up, which is a
    systematic upward bias of well under a second per phase.

    Skipped entirely (and reported as INFO) when the phase durations are not
    all present: an incomplete program is a normal partial import, not a
    contradiction.
    """
    issues: list[ImportIssue] = []
    for controller in result.controllers:
        for program in controller.programs:
            if program.cycle_time_s is None or not program.phases:
                continue
            totals: list[float] = []
            incomplete = False
            for phase in program.phases:
                if phase.green_s is None:
                    incomplete = True
                    break
                clearance = phase.intergreen_s
                if clearance is None:
                    if phase.amber_s is None and phase.all_red_s is None:
                        incomplete = True
                        break
                    clearance = (phase.amber_s or 0.0) + (phase.all_red_s or 0.0)
                totals.append(phase.green_s + clearance)
            if incomplete:
                issues.append(
                    ImportIssue(
                        severity=IssueSeverity.INFO,
                        code="program_cycle_check_skipped",
                        message=(
                            f"Controller {controller.controller_id} program {program.program_id}: "
                            "cycle consistency not checked — at least one phase has no green or "
                            "clearance time in the source."
                        ),
                        subject=f"controller {controller.controller_id} / {program.program_id}",
                        citation=CIT_APPENDIX_A_A2,
                    )
                )
                continue

            summed = sum(totals)
            difference = abs(summed - program.cycle_time_s)
            tolerance = max(
                thresholds.cycle_sum_tolerance_s,
                program.cycle_time_s * thresholds.cycle_sum_tolerance_fraction,
            )
            if difference == 0:
                continue
            severity = IssueSeverity.REFUSE if difference > tolerance else IssueSeverity.WARN
            code = (
                "program_phase_sum_mismatch"
                if severity is IssueSeverity.REFUSE
                else "program_phase_sum_rounding"
            )
            issues.append(
                ImportIssue(
                    severity=severity,
                    code=code,
                    message=(
                        f"Controller {controller.controller_id} program {program.program_id}: "
                        f"phase durations sum to {summed:g} s against a stated cycle of "
                        f"{program.cycle_time_s:g} s (difference {difference:g} s, tolerance "
                        f"{tolerance:g} s)."
                    ),
                    subject=f"controller {controller.controller_id} / {program.program_id}",
                    citation=CIT_APPENDIX_A_A2,
                    context={
                        "phase_sum_s": summed,
                        "cycle_time_s": program.cycle_time_s,
                        "tolerance_s": tolerance,
                    },
                )
            )
    return issues


def check_controller_completeness(
    result: ImportResult, thresholds: QualityThresholds
) -> list[ImportIssue]:
    """WARN. A controller with no timings at all, and an observed-history
    program with no stated observation window (its representativeness cannot be
    judged, and MRWA A.2's cycle counting needs the window)."""
    issues: list[ImportIssue] = []
    for controller in result.controllers:
        if not controller.programs and not controller.signal_groups:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.WARN,
                    code="controller_without_timings",
                    message=(
                        f"Controller {controller.controller_id} was imported with no programs and "
                        "no signal groups — nothing about it can be modelled yet."
                    ),
                    subject=f"controller {controller.controller_id}",
                )
            )
        for program in controller.programs:
            if (
                program.source_kind is ProgramSourceKind.OBSERVED_HISTORY
                and program.observation_window is None
            ):
                issues.append(
                    ImportIssue(
                        severity=IssueSeverity.WARN,
                        code="observed_program_without_window",
                        message=(
                            f"Controller {controller.controller_id} program {program.program_id} "
                            "holds observed history but states no observation window; its "
                            "representativeness and cycle count cannot be assessed."
                        ),
                        subject=f"controller {controller.controller_id} / {program.program_id}",
                        citation=CIT_APPENDIX_A_A2,
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Registry + runner
# ---------------------------------------------------------------------------

QUALITY_CHECKS: tuple[QualityCheck, ...] = (
    QualityCheck(
        id="counts_present",
        title="Count data present",
        tiers=(IssueSeverity.INFO,),
        citation=None,
        fn=check_counts_present,
    ),
    QualityCheck(
        id="interval_overlap",
        title="Count intervals do not overlap",
        tiers=(IssueSeverity.REFUSE,),
        citation=CIT_15_MIN,
        fn=check_interval_overlap,
    ),
    QualityCheck(
        id="interval_regularity",
        title="15-minute interval regularity (bin length, gaps)",
        tiers=(IssueSeverity.WARN,),
        citation=CIT_15_MIN,
        fn=check_interval_regularity,
    ),
    QualityCheck(
        id="peak_coverage",
        title="≥3 h continuous coverage per peak",
        tiers=(IssueSeverity.REFUSE, IssueSeverity.INFO),
        citation=CIT_COLLECTION_MINIMUMS,
        fn=check_peak_coverage,
    ),
    QualityCheck(
        id="neutral_day",
        title="Neutral-day rule",
        tiers=(IssueSeverity.WARN, IssueSeverity.INFO),
        citation=CIT_COLLECTION_MINIMUMS,
        fn=check_neutral_day,
    ),
    QualityCheck(
        id="minimum_observations",
        title="Minimum observations per movement series",
        tiers=(IssueSeverity.WARN,),
        citation=CIT_COLLECTION_MINIMUMS,
        fn=check_minimum_observations,
    ),
    QualityCheck(
        id="approach_total_consistency",
        title="Approach totals equal their movement sums",
        tiers=(IssueSeverity.REFUSE, IssueSeverity.WARN),
        citation=None,
        fn=check_approach_total_consistency,
    ),
    QualityCheck(
        id="class_total_consistency",
        title="All-class totals equal their per-class sums",
        tiers=(IssueSeverity.REFUSE, IssueSeverity.WARN),
        citation=CIT_HEAVY_VEHICLES,
        fn=check_class_total_consistency,
    ),
    QualityCheck(
        id="class_coverage",
        title="Vehicle-class coverage",
        tiers=(IssueSeverity.WARN, IssueSeverity.INFO),
        citation=CIT_HEAVY_VEHICLES,
        fn=check_class_coverage,
    ),
    QualityCheck(
        id="volume_sanity",
        title="Flow-rate plausibility band",
        tiers=(IssueSeverity.WARN,),
        citation=None,
        fn=check_volume_sanity,
    ),
    QualityCheck(
        id="detector_count_intervals",
        title="Detector count interval regularity",
        tiers=(IssueSeverity.REFUSE, IssueSeverity.WARN),
        citation=CIT_15_MIN,
        fn=check_detector_count_intervals,
    ),
    QualityCheck(
        id="signal_reference_integrity",
        title="Signal cross-references resolve",
        tiers=(IssueSeverity.REFUSE,),
        citation=CIT_SCATS_CODING,
        fn=check_signal_reference_integrity,
    ),
    QualityCheck(
        id="program_cycle_consistency",
        title="Phase durations sum to the stated cycle",
        tiers=(IssueSeverity.REFUSE, IssueSeverity.WARN, IssueSeverity.INFO),
        citation=CIT_APPENDIX_A_A2,
        fn=check_program_cycle_consistency,
    ),
    QualityCheck(
        id="controller_completeness",
        title="Controllers carry usable timings",
        tiers=(IssueSeverity.WARN,),
        citation=None,
        fn=check_controller_completeness,
    ),
)


def run_quality_gate(
    result: ImportResult, thresholds: QualityThresholds | None = None
) -> list[ImportIssue]:
    """Run every check and return the issues, without mutating ``result``."""
    thresholds = thresholds or QualityThresholds()
    issues: list[ImportIssue] = []
    for check in QUALITY_CHECKS:
        issues.extend(check.fn(result, thresholds))
    return issues


def apply_quality_gate(
    result: ImportResult, thresholds: QualityThresholds | None = None
) -> ImportResult:
    """Run the gate and append its findings to ``result.issues``.

    Returns the same object so a caller can write
    ``result = apply_quality_gate(adapter.parse(files))``. After this call,
    ``result.ok`` answers "may this data be used without a recorded human
    override?".
    """
    result.add_issues(run_quality_gate(result, thresholds))
    return result


__all__ = [
    "CIT_15_MIN",
    "CIT_APPENDIX_A_A2",
    "CIT_COLLECTION_MINIMUMS",
    "CIT_HEAVY_VEHICLES",
    "CIT_SCATS_CODING",
    "QUALITY_CHECKS",
    "QualityCheck",
    "QualityThresholds",
    "apply_quality_gate",
    "is_all_class_total",
    "movement_key",
    "run_quality_gate",
    "series_key",
]

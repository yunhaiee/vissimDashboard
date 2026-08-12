"""Observed counts → a genesis draft's demand: inputs, compositions, routes.

Roadmap 3.2, **Genesis (c) 수요**. Fully TK-independent: every input is either
the draft this package built or the neutral counts unit 1.2 produced, and
nothing here imports Vissim, touches COM or needs a licence seat.

What this module is
-------------------
Unit 1.2's :mod:`services.dataimport.demand` converts neutral
:class:`~services.dataimport.schema.TurningCountData` into the parameters
Prism's shipped edit verbs take — per-approach 15-minute volume profiles in
veh/h, and per-site vehicle-class weights. It stops exactly there, and says so:
it "will not map a Korean class code onto a Vissim vehicle **type number**",
and its :class:`~services.dataimport.demand.VehicleInputProfile` has no
``link_no`` because "which link carries this approach is a property of the
network, not of the survey".

This module is that step. It takes a draft and an
:class:`~services.dataimport.base.ImportResult`, joins them through a
CALLER-SUPPLIED map, and produces the Vissim objects a demand-carrying network
needs:

* **vehicle inputs**, one per joined *entry* link, volume in veh/h with the
  counted figure and the bin length kept beside it (§5.5.4.1),
* **vehicle compositions**, one per site, relative flows straight from the
  observed class counts (§5.5.1),
* **static vehicle routing decisions**, one per joined approach that has
  connectors, one route per drafted turn, relative flows straight from the
  observed movement counts (§5.5.4.2),
* and §5.2.2 / §5.2.3 / §5.5 / §5.6.1.3 **compliance items** in the draft's own
  three-state style.

Nothing is re-derived. The per-interval rates and the class weights come from
``build_demand_seeding``; the per-movement weights come from the *same*
``seedable_counts`` predicate, which 1.2 exposed as a function precisely so
"anything else that sums neutral counts (routing seeding in roadmap 3.2 next)
shares one definition". Both axes of "this row is already a sum of other rows"
are therefore excluded here by construction rather than by a second copy of the
rule.

It is additive by construction. A draft with no demand never calls it, and the
emitter writes not one byte differently for one — pinned by
``test_genesis_demand.py::test_demand_less_draft_bytes_are_unchanged``.


The join — three caller-supplied maps, none of them guessed
-----------------------------------------------------------
Count site ids do not match draft link ids and never will: a Daejeon site id is
an intersection name taken from a file name, a draft link id is a number this
package assigned from a sorted OSM way id. The same problem U5/U10 solved with
``site_nodes`` and ``fixes.py`` solved with ``decision_movements``, and the same
answer — the join is an ARGUMENT.

``approach_links`` maps **site id → {approach leg id → draft link id}**. It is
required, and validated in BOTH directions before anything is built: every leg
the map names must exist in the counts, every link it names must exist in the
draft, no two legs may claim one link, and a site the caller mapped at all must
have *every* observed approach mapped (a half-mapped intersection would load
some of its demand and silently drop the rest, which is worse than none).
A site the caller did not map at all is out of the draft's scope: it is named
in :class:`DemandCoverage`, not refused.

``vehicle_types`` maps **observed class code → :class:`VehicleTypeRef`**, i.e.
onto a Vissim vehicle type number and a desired-speed distribution number. 1.2
already refused to derive this ("a modelling decision about the target network,
not a property of the count data, and no file in the drop states it") and §5.2.3
proves it right: MRWA wants the Austroads classification with Table 5-2's
acceleration series, Table 5-3's power/weight distributions and Table 5-4's
colours. None of that is in a turning count.

The turn↔connector join is NOT caller-supplied, because the draft already
states it: :attr:`~.draft.DraftConnector.turn` carries the same
``left``/``through``/``right`` vocabulary as
:class:`~services.dataimport.schema.TurnType`. A turn observed but not drafted,
and a turn drafted but not observed, are both NAMED in the coverage; a turn with
two connectors off one approach is a split this module refuses rather than
invents.


The 15-minute profile gap — MEASURED, and refused rather than invented
-----------------------------------------------------------------------
§5.5.4.1 asks for 15-minute demand profiles, and 1.2 produces them. This
emission cannot write them, for a reason that is a measurement rather than an
opinion.

A Vissim vehicle input's volume is keyed to a time interval by a two-token
reference, ``timeInt="1 0"``: the second token is a ``<timeInterval start=…>``
inside a ``<timeIntervalSet>``, and the first is that set's position in
``<timeIntervalSets>``. A profile is therefore a set with several
``<timeInterval>`` children and one ``<timeIntervalVehVolume>`` per child.
**No Vissim-saved network on this machine has a time-interval set with more
than one interval.** All 30 sets across the three saved references — and across
Vissim's own save of this generator's crossroads — carry exactly one
``<timeInterval start="0"/>``, so neither the repetition nor the unit of
``start`` (only the value 0 occurs, and 0 is 0 in every unit) has been
observed. Writing them would be emitter v1's mistake in a new place: v1 died of
an attribute set invented from a hand-written exemplar, and the whole of
:mod:`services.genesis.schema` exists because of it.

So the split is:

* the PLAN — :class:`VehicleInputPlan` — carries every interval, each with its
  counted figure, bin length and source window, because a report and a later
  emitter both need them;
* the EMISSION writes ONE interval. Which one is a modelling decision, so when
  the joined profile has more than one interval the caller must state
  :attr:`DemandSettings.single_interval_reduction` (``"peak"`` or ``"mean"``);
  absent, this module REFUSES rather than picking. The arithmetic of both is
  written out on :class:`VehicleInputPlan`, and every reduced input is named in
  an always-on disclosure and in a §5.5.4.1 ``not_checkable`` item.

Reinstating profiles needs one measurement — a Vissim save of a network with a
multi-interval ``VEHICLEINPUT`` set — and nothing else in this module changes.


The one reference this emission does not declare
------------------------------------------------
Every other reference the generated file makes resolves inside the file:
``vehComp`` points at a composition this module writes, ``link`` and
``destLink`` at links ``emit.py`` writes, ``linkSeq`` at connectors it writes,
and ``timeInt``/``relFlow`` at the ``<timeIntervalSets>`` written here. The
routing decisions are emitted with ``allVehTypes="true"``, which is the measured
form that carries NO ``<vehClasses>`` child, so no ``vehicleClasses`` reference
is made either.

``<vehicleCompositionRelativeFlow>`` is the exception: its ``vehType`` and
``desSpeedDistr`` are the caller's numbers, and this emission writes no
``<vehicleTypes>`` or ``<speedDistributions>`` container. It cannot: a Vissim
``<vehicleType>`` reaches ten further containers (colour, four accel/decel
function families, curve-speed, power, weight, occupancy and a 2D/3D model
distribution whose models name ``.u3dm`` files on the Vissim install), and
§5.2.3 says the values in them must be MRWA's, not Vissim's factory defaults.
Building that layer is not roadmap 3.2.

The consequence is stated everywhere a reader can meet it — an always-on
``genesis_demand_vehicle_types_not_declared_here`` INFO, a §5.2.3
``not_checkable`` compliance item, and a sentence in the emitted file's own
provenance comment — because an unresolved reference makes Vissim drop the
container silently and report a successful load, which is the exact failure
class this project exists to catch (measured twice on the emitter-v2 ladder).
**A demand-carrying draft is therefore valid only against a network that
declares the referenced vehicle types**, and the live open test of one is a
named follow-up, not something this offline module can certify.


What the repo's own readers recover — one named gap
---------------------------------------------------
``draft.py`` states the emitter's rule as "nothing is emitted that this repo's
parser cannot read back". Measured, on this module's own output, through
``services.inpx_parser``:

===================================== ====================================
recovered                             NOT recovered
===================================== ====================================
``vehicleInput`` no / name / link,    the input's **volume**, ``volType``
and its composition no, name, the     and ``timeInt``
distinct composition list and the
"varies by interval" flag
``vehicleComposition`` no → name      its ``relFlow`` / ``vehType`` /
                                      ``desSpeedDistr`` rows
``vehicleRoutingDecisionStatic`` no   the route's ``destPos`` and
/ name / approach link, and per       ``linkSeq``
route the no, name, ``destLink`` and
``relFlow`` (with the share derived
from it)                              the ``timeIntervalSets`` container
===================================== ====================================

So the **routing** arm round-trips end to end: ``parse_routing_decisions``
returns the seeded relative flows and computes the same per-route share this
module wrote. The **input volume** does not, because ``inpx_parser`` is the
map-geometry reader — it resolves a vehicle input to a point and a composition
label, which is all a map glyph needs — and reading the volume is not in its
job description today.

That is a gap in the READER, not a licence to write unverified numbers: the
volume is proved by a direct XML round trip in ``test_genesis_demand.py``
(emitted → parsed → equal to the counted figure), and teaching
``inpx_parser`` to surface ``timeIntervalVehVolume/@volume`` is a named
follow-up rather than something this module reaches into.


Where each number comes from — nothing here is invented
-------------------------------------------------------
* **Vehicle-input volume**: ``VehicleInputProfile.intervals[i].volume_vph``,
  which is 1.2's ``counted × 60 / bin minutes`` — the recalculation §5.5.4.1
  spells out with a worked example ("an analysed 15 minute flow is 200
  vehicles, then the value for the vehicle input must be 800").
* **Composition relative flow**: ``CompositionShare.rel_flow``, the raw observed
  count per class. Vissim normalises relative flows across a composition at run
  time, so the raw count IS the weight — no percentage, no rounding, no
  residual.
* **Route relative flow**: the raw observed volume of that turn, by the same
  argument. See :func:`route_share` for the identity that makes it exact.
* **Routing-decision position**: 0.0 m, the upstream end of the approach link —
  a property of the draft, not a number chosen from the air. §5.5.4.2 wants the
  marker "where motorists would likely begin to perform lane changes", which is
  an on-site observation a generated draft does not have, so the rule is graded
  ``not_checkable`` and the position is disclosed rather than dressed up.
* **Route destination position**: the exit link's own length.
* **volType**: ``EXACT``, because §5.5.4.1 recommends exact volumes "to minimise
  the discrepancies of model outputs between simulation runs with different seed
  numbers". Both values are Vissim's (NOD writes EXACT, LHT STOCHASTIC), which
  is why it is a setting and not a schema constant.
* **combineStaRoutDec**: ``true``, because §5.5.4.2 recommends activating it.
  Vissim's own saved networks all write ``false``, so this too is a setting
  carrying its citation rather than a measured default.

Determinism
-----------
Ids are assigned by ``enumerate(..., start=1)`` over sorted keys (sites by id,
inputs and decisions by approach link id, routes by connector id), elements are
written in id order, attributes in sorted order through the same ``_element``
the emitter uses, and every number goes through one formatter. Two runs from the
same draft and the same counts produce byte-identical files.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..dataimport.base import ImportIssue, ImportResult, IssueSeverity
from ..dataimport.demand import (
    DemandSeeding,
    VehicleInputProfile,
    build_demand_seeding,
    seedable_counts,
)
from ..dataimport.schema import TurningCountData
from . import schema
from .draft import (
    REASON_OUTSIDE_EMITTED_SUBSET,
    REASON_REQUIRES_SITE_OBSERVATION,
    ComplianceItem,
    ComplianceState,
    DraftConnector,
    DraftLink,
    DraftNetwork,
)
from .osm import EXTRACT

# ---------------------------------------------------------------------------
# Citations — §5.2.2, §5.2.3, §5.5 (Traffic Data) and §5.6.1.3
#
# Same contract as ``draft.py`` and ``signals.py``: every constant names the
# extract file and quotes it verbatim, so the citation test can mechanically
# compare each fragment against the document. A rule with no traceable line does
# not get written. The locator names a § and a doc page and never an in-text
# figure label — §5 mislabels nine of its own Tables as Figures, and §5.5.4.1's
# two worked examples are among the figures that did not survive extraction.
# ---------------------------------------------------------------------------

CIT_S522_TIME_PERIODS = (
    f"MRWA OMG v2.0 §5.2.2 Time Period Settings, doc p.181 ({EXTRACT}) — "
    "'The modelling should not only include the simulation assessment period, but also the "
    "warm-up and cool-down periods.'"
)

CIT_S523_VEHICLE_TYPES = (
    f"MRWA OMG v2.0 §5.2.3 Vehicle Types, doc p.182 ({EXTRACT}) — "
    "'Traffic surveys should contain information on the vehicle compositions to be modelled. "
    "Vehicles in Western Australia can be classified using the Austroads Vehicle Classification "
    "System.'"
)

CIT_S551_VEHICLE_COMPOSITION = (
    f"MRWA OMG v2.0 §5.5.1 Vehicle Composition, doc p.204 ({EXTRACT}) — "
    "'Vehicle composition used within the model should be determined using recent classified "
    "traffic counts and aligned with specific vehicle types (refer to Section 5.2.3).'"
)

CIT_S5541_VEHICLE_INPUTS = (
    f"MRWA OMG v2.0 §5.5.4.1 Vehicle Inputs, doc p.205 ({EXTRACT}) — "
    "'Traffic demands should generally be introduced using the vehicle inputs in network "
    "objects. Modellers should define peak period demands and include warm-up and cool-down "
    "periods.'"
)

CIT_S5541_FIFTEEN_MINUTE_INTERVALS = (
    f"MRWA OMG v2.0 §5.5.4.1 Vehicle Inputs, doc p.205 ({EXTRACT}) — "
    "'It is recommended for model category 1 (refer to Section 2.12.2.1) that traffic count "
    "data is analysed for 15 minute intervals and vehicle inputs are set for peak periods to "
    "enable a model to replicate observed on-site traffic flow accurately.'"
)

CIT_S5541_HOURLY_VOLUMES = (
    f"MRWA OMG v2.0 §5.5.4.1 Vehicle Inputs, doc p.206 ({EXTRACT}) — "
    "'It should be noted that vehicle inputs in Vissim are expressed in hourly volumes. "
    "Therefore, the analysed 15 minute interval traffic flows must be recalculated to one-hour "
    "traffic flows (for example, an analysed 15 minute flow is 200 vehicles, then the value for "
    "the vehicle input must be 800).'"
)

CIT_S5541_EXACT_VOLUMES = (
    f"MRWA OMG v2.0 §5.5.4.1 Vehicle Inputs, doc p.206 ({EXTRACT}) — "
    "'it is recommended that modellers use exact volumes to minimise the discrepancies of model "
    "outputs between simulation runs with different seed numbers.'"
)

CIT_S5542_VEHICLE_ROUTES = (
    f"MRWA OMG v2.0 §5.5.4.2 Vehicle Routes, doc p.207 ({EXTRACT}) — "
    "'Vehicle route decision markers should be set to mimic the point where motorists would "
    "likely begin to perform lane changes to reach their desired destination. To manage demands "
    "for trip purposes or vehicle types, the use of vehicle routes for individual vehicle "
    "classes is recommended.'"
)

CIT_S5542_COMBINE_DECISIONS = (
    f"MRWA OMG v2.0 §5.5.4.2 Vehicle Routes, doc p.207 ({EXTRACT}) — "
    "'It is generally recommended to activate Combine static routing decisions when static "
    "assignment is used.'"
)

CIT_S5613_DEMAND_INPUT_CHECK = (
    f"MRWA OMG v2.0 §5.6.1.3 Model Input Check, doc p.211 ({EXTRACT}) — "
    "'traffic demands (vehicle inputs or OD matrices) / vehicle routes'"
)


# ---------------------------------------------------------------------------
# The module's own vocabulary
# ---------------------------------------------------------------------------

#: Every code that produces NO demand at all. Kept as data so the module
#: docstring's "refuses and produces nothing" claim is testable, exactly as
#: ``signals.REFUSE_CODES`` is.
REFUSE_CODES: frozenset[str] = frozenset(
    {
        "genesis_demand_seeding_refused",
        "genesis_demand_no_approach_link_map",
        "genesis_demand_map_names_unknown_link",
        "genesis_demand_map_names_unknown_site",
        "genesis_demand_map_names_unknown_approach",
        "genesis_demand_duplicate_link_mapping",
        "genesis_demand_mapped_site_missing_approaches",
        "genesis_demand_no_vehicle_type_map",
        "genesis_demand_class_without_vehicle_type",
        "genesis_demand_vehicle_type_map_names_unknown_class",
        "genesis_demand_composition_shares_do_not_sum",
        "genesis_demand_zero_observed_total",
        "genesis_demand_profile_needs_interval_reduction",
        "genesis_demand_interval_length_mismatch",
        "genesis_demand_turn_has_multiple_connectors",
    }
)

#: How a multi-interval profile may be collapsed onto the single interval this
#: emission can write. Both are stated arithmetic, neither is a default.
REDUCTION_PEAK = "peak"
REDUCTION_MEAN = "mean"

#: Composition shares are normalised counts, so they must sum to 1. This is the
#: floating-point slack allowed before the sum is called broken — wide enough for
#: accumulated division error over a few hundred classes, far too narrow to hide
#: a missing class.
SHARE_SUM_TOLERANCE = 1e-6


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    *,
    messages: dict[str, str] | None = None,
    subject: str | None = None,
    citation: str | None = None,
    context: dict | None = None,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        messages=messages or {},
        subject=subject,
        citation=citation,
        context=context or {},
    )


# ---------------------------------------------------------------------------
# Settings and the caller-supplied vehicle-type reference
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VehicleTypeRef:
    """The target network's numbers for one observed vehicle class.

    Both fields are references OUT of this emission — see the module docstring's
    "The one reference this emission does not declare". ``des_speed_distr_no`` is
    required rather than defaulted because ``desSpeedDistr`` is a mandatory
    attribute of every ``<vehicleCompositionRelativeFlow>`` Vissim has ever
    saved on this machine, and a desired-speed distribution number picked here
    would be a speed limit invented by an import.
    """

    veh_type_no: int
    des_speed_distr_no: int


class DemandSettings(BaseModel):
    """Every choice this module makes that §5 or a measurement does not force.

    Same shape as ``signals.SignalSettings`` and ``fixed_time.ConversionSettings``:
    one place, overridable, each field carrying the reason it exists.
    """

    model_config = ConfigDict(extra="forbid")

    volume_type: str = Field(
        default=schema.VOL_TYPE_EXACT,
        description=(
            "§5.5.4.1: 'it is recommended that modellers use exact volumes'. Both values "
            "are Vissim's own (NOD writes EXACT on all 8 inputs, LHT STOCHASTIC on all 9), "
            "so this is a modelling choice with an authority, not a schema default."
        ),
    )
    combine_static_routing_decisions: bool = Field(
        default=True,
        description=(
            "§5.5.4.2: 'It is generally recommended to activate Combine static routing "
            "decisions when static assignment is used.' Vissim's own saved networks all "
            "write false, so the guideline is the authority here, not the measurement."
        ),
    )
    single_interval_reduction: str | None = Field(
        default=None,
        description=(
            "How to collapse a multi-interval profile onto the one interval this emission "
            "can write: 'peak' or 'mean'. No default — picking one would be a modelling "
            "assumption wearing an import's clothes, so a profiled source refuses without it."
        ),
    )
    expected_interval_minutes: float | None = Field(
        default=None,
        gt=0,
        description=(
            "When set, every joined approach's bins must be this long or the seeding "
            "refuses. Set it to 15.0 to make §5.5.4.1's 15-minute analysis a hard "
            "requirement rather than a hope."
        ),
    )
    routing_decision_pos_m: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Where the routing decision marker sits on its approach link. 0.0 is the link's "
            "upstream end — the earliest position the draft's own geometry offers, so every "
            "vehicle on the approach passes it. §5.5.4.2 wants the point where drivers begin "
            "changing lanes, which is an on-site observation, so that rule is graded "
            "not_checkable rather than approximated by this number."
        ),
    )

    @field_validator("volume_type")
    @classmethod
    def _volume_type_is_one_vissim_writes(cls, value: str) -> str:
        """``volType`` goes into the file verbatim, so it is checked HERE.

        Raising rather than reporting is right for this one: an unrecognised
        ``volType`` is a programming error in the caller, not a data problem —
        the two values are the only ones any Vissim-saved network on this
        machine contains, and writing a third would be inventing an enum member.
        """
        allowed = (schema.VOL_TYPE_EXACT, schema.VOL_TYPE_STOCHASTIC)
        if value not in allowed:
            raise ValueError(f"volume_type must be one of {allowed}, got {value!r}")
        return value


DEFAULT_SETTINGS = DemandSettings()


# ---------------------------------------------------------------------------
# The plan — what one draft's demand IS, before any XML exists
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DemandInterval:
    """One time interval of one vehicle input, with its arithmetic attached.

    A verbatim carry-over of 1.2's :class:`~services.dataimport.demand.
    DemandInterval` plus the flag saying whether this is the interval the file
    actually got. Nothing is recomputed: ``volume_vph`` is 1.2's number.
    """

    index: int
    start: datetime
    end: datetime
    volume_vph: float
    counted_volume: float
    interval_minutes: float
    contributing_records: int
    emitted: bool = False


@dataclass(frozen=True)
class VehicleInputPlan:
    """One ``<vehicleInput>``: one entry link, one composition, one volume.

    ``intervals`` is the WHOLE observed profile, in order, even though at most
    one of them carries ``emitted=True``. Keeping the rest is the difference
    between "this file writes one interval" and "this survey only had one".
    """

    no: int
    link_id: int
    site_id: str
    approach_leg_id: str
    composition_no: int
    intervals: tuple[DemandInterval, ...]
    reduction: str | None
    emitted_volume_vph: float

    @property
    def profiled(self) -> bool:
        """True when the SOURCE carried a profile — regardless of what the file
        got. The honest answer to "was this input profiled?" is about the survey."""
        return len(self.intervals) > 1

    @property
    def peak_volume_vph(self) -> float:
        return max((interval.volume_vph for interval in self.intervals), default=0.0)

    @property
    def total_counted(self) -> float:
        return sum(interval.counted_volume for interval in self.intervals)

    @property
    def total_minutes(self) -> float:
        return sum(interval.interval_minutes for interval in self.intervals)

    @property
    def mean_volume_vph(self) -> float:
        """The time-weighted mean rate: every vehicle counted, over the whole
        window, expressed per hour. Equal to the arithmetic mean of the per-bin
        rates when the bins are equal, which they are whenever 1.2 produced a
        profile at all (it refuses mixed bin lengths)."""
        minutes = self.total_minutes
        return (self.total_counted * 60.0 / minutes) if minutes else 0.0


@dataclass(frozen=True)
class CompositionFlow:
    """One vehicle class's weight inside one composition.

    ``rel_flow`` is the observed count, unmodified: Vissim normalises relative
    flows across the composition at run time, so handing it the raw counts is
    both simpler and lossless (1.2's argument, kept). ``share`` is the
    normalised fraction, for reports and for the sum check.
    """

    veh_type_no: int
    des_speed_distr_no: int
    rel_flow: float
    share: float
    class_code: str
    class_scheme: str
    counted_records: int


@dataclass(frozen=True)
class VehicleCompositionPlan:
    """One ``<vehicleComposition>`` — the class split observed at one site."""

    no: int
    name: str
    site_id: str
    flows: tuple[CompositionFlow, ...]

    @property
    def share_sum(self) -> float:
        return sum(flow.share for flow in self.flows)


@dataclass(frozen=True)
class RouteSeed:
    """One ``<vehicleRouteStatic>``: one drafted turn, weighted by its count."""

    no: int
    turn: str
    connector_id: int
    dest_link_id: int
    dest_pos_m: float
    rel_flow: float
    observed_volume: float
    share: float
    contributing_records: int


@dataclass(frozen=True)
class RoutingDecisionSeed:
    """One ``<vehicleRoutingDecisionStatic>`` on one joined approach link."""

    no: int
    link_id: int
    pos_m: float
    site_id: str
    approach_leg_id: str
    observed_total: float
    routes: tuple[RouteSeed, ...]


def _empty_coverage() -> dict[str, list[str]]:
    """Both directions of every join, built before any object is emitted.

    The same rule ``observations._empty_site_coverage`` states: it "always names
    both directions of the site-level join plus any conflicting map entry — it is
    built before any node is emitted, so it cannot be quietly skipped on a happy
    path".
    """
    return {
        "sites_observed": [],
        "sites_joined": [],
        "sites_without_link_map": [],
        "mapped_sites_absent_from_counts": [],
        "approaches_observed": [],
        "approaches_joined": [],
        "mapped_approaches_absent_from_counts": [],
        "mapped_links_absent_from_draft": [],
        "links_seeded_input_and_routing": [],
        "links_seeded_input_only": [],
        "links_seeded_routing_only": [],
        "links_seeded_neither": [],
        "classes_observed": [],
        "classes_without_vehicle_type": [],
        "mapped_classes_absent_from_counts": [],
        "movements_without_turn": [],
        "turns_observed_not_drafted": [],
        "turns_drafted_not_observed": [],
        "turns_with_multiple_connectors": [],
    }


@dataclass(frozen=True)
class DemandCoverage:
    """Who joined, who did not, and in which direction — never a silent drop."""

    entries: dict[str, list[str]] = field(default_factory=_empty_coverage)

    def add(self, key: str, value: str) -> None:
        bucket = self.entries[key]
        if value not in bucket:
            bucket.append(value)

    def sorted_entries(self) -> dict[str, list[str]]:
        return {key: sorted(value) for key, value in sorted(self.entries.items())}

    def counts(self) -> dict[str, int]:
        return {key: len(value) for key, value in sorted(self.entries.items())}


@dataclass(frozen=True)
class DraftDemand:
    """Everything one draft's demand layer contributes to the emission.

    All-or-nothing, like :class:`~.signals.NodeSignals`: ``inputs`` is empty
    exactly when a ``REFUSE`` issue is present. There is no partial demand —
    half an intersection's inputs is a network that looks loaded and is not.
    """

    inputs: tuple[VehicleInputPlan, ...] = ()
    compositions: tuple[VehicleCompositionPlan, ...] = ()
    decisions: tuple[RoutingDecisionSeed, ...] = ()
    issues: tuple[ImportIssue, ...] = ()
    compliance: tuple[ComplianceItem, ...] = ()
    coverage: DemandCoverage = field(default_factory=DemandCoverage)
    # ``default_factory`` rather than the constant itself: a pydantic model is
    # mutable, so a dataclass refuses it as a bare default.
    settings: DemandSettings = field(default_factory=lambda: DEFAULT_SETTINGS)

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
    def emitting(self) -> bool:
        """Whether there is anything to write. A refused demand contributes
        findings and NO objects; it never half-emits."""
        return self.ok and bool(self.inputs or self.compositions or self.decisions)

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [i.code for i in self.issues if severity is None or i.severity is severity]

    def input_for(self, link_id: int) -> VehicleInputPlan | None:
        for plan in self.inputs:
            if plan.link_id == link_id:
                return plan
        return None

    def decision_for(self, link_id: int) -> RoutingDecisionSeed | None:
        for seed in self.decisions:
            if seed.link_id == link_id:
                return seed
        return None


#: The empty demand layer. ``emit`` treats this and ``None`` identically.
NO_DEMAND = DraftDemand()


# ---------------------------------------------------------------------------
# Draft-side helpers — the same expressions ``build_draft`` used
# ---------------------------------------------------------------------------


def entry_links(draft: DraftNetwork) -> list[DraftLink]:
    """The links traffic ENTERS the network on, in the draft's own order.

    A link is an entry when no connector arrives on it. ``draft.build_draft``
    creates exactly one connector per (approach, exit) pair at every node it
    built, so "nothing arrives here" is the same statement as "this link's
    upstream end is not a modelled intersection" — which is what a network
    boundary is.

    ``draft._compliance`` computes a wider BOUNDARY list (entries *or* exits)
    for §5.3.2.1's queue-containment evidence; a vehicle input belongs only on
    the entry half, because putting one on an exit would inject traffic that
    the network has already carried.
    """
    arriving = {connector.to_link for connector in draft.connectors}
    return sorted(
        (link for link in draft.links if link.id not in arriving), key=lambda link: link.id
    )


def link_exits(draft: DraftNetwork, link_id: int) -> list[DraftConnector]:
    """The connectors leaving one link — the draft's own turn topology for it."""
    return sorted(
        (connector for connector in draft.connectors if connector.from_link == link_id),
        key=lambda connector: connector.id,
    )


def route_share(rel_flow: float, sibling_total: float) -> float:
    """Vissim's run-time share of one route: ``relFlow / Σ relFlow``.

    Written out because it is the identity the whole routing arm rests on, and
    because it reconciles this module with ``services.agent.fixes.py``'s
    closed form for the *repair* case.

    Seeding writes every sibling at once, so the raw observed volumes are
    already the right weights: with ``relFlow_t = v_t`` the share of turn *t*
    is ``v_t / Σv``, which is the observed split exactly — no scaling, no
    residual to redistribute.

    ``fixes.py`` faces the other problem — one route to change, siblings frozen
    — and derives ``new = sibling_total × p / (1 - p)``. The two agree, which is
    the check that neither is ad hoc: substitute the seeded state, where
    ``p = v_t / Σv`` and ``sibling_total = Σv - v_t``::

        (Σv - v_t) × (v_t / Σv) / (1 - v_t / Σv)
      = (Σv - v_t) × (v_t / Σv) / ((Σv - v_t) / Σv)
      = v_t

    i.e. applying the repair formula to demand this module seeded returns the
    value already written. A worked instance is in the test suite.
    """
    total = sibling_total + rel_flow
    return (rel_flow / total) if total else 0.0


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _movement_turn(count: TurningCountData) -> str | None:
    return count.movement.turn.value if count.movement.turn is not None else None


def _class_key(count: TurningCountData) -> str:
    return count.vehicle_class.code


@dataclass(frozen=True)
class ApproachLinkSiteCheck:
    """The site/leg half of the approach→link join's validation.

    Factored out of :func:`generate_demand` so a caller that has COUNTS but no
    DRAFT yet — ``routers/genesis.py``'s demand-input build route, which
    assembles a ``genesis.demand.v1`` document before any generation has run —
    can run the SAME check, with the SAME codes and the SAME wording, instead
    of inventing its own "unknown site" sentence that could drift from this
    one. The link-existence and duplicate-link checks stay inside
    :func:`generate_demand`: they need ``draft.links``, which a caller with no
    draft does not have (``services/genesis/side_inputs.py``'s docstring names
    this exact split — "does NOT check … whether link 7 exists … because the
    draft does not exist yet").
    """

    issues: tuple[ImportIssue, ...]
    unknown_sites: tuple[str, ...]
    unknown_approaches: tuple[str, ...]


def check_approach_link_sites(
    approach_links: Mapping[str, Mapping[str, int]],
    *,
    observed_sites: Sequence[str],
    observed_approaches: Mapping[str, set[str]],
) -> ApproachLinkSiteCheck:
    """Which mapped sites/approaches the counts do not contain, and why that
    refuses. Pure — no draft, no I/O — so it runs equally before or after a
    draft exists."""
    unknown_sites = sorted(set(approach_links) - set(observed_sites))
    unknown_approaches: list[str] = []
    for site_id in sorted(approach_links):
        for leg in sorted(approach_links[site_id]):
            label = f"{site_id}|{leg}"
            if site_id in observed_approaches and leg not in observed_approaches[site_id]:
                unknown_approaches.append(label)

    issues: list[ImportIssue] = []
    if unknown_sites:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_map_names_unknown_site",
                f"The approach→link map names site(s) {unknown_sites} that the counts do not "
                "contain. Either the map is another survey's or the window excluded them; "
                "either way an entry the caller meant to apply somewhere would be silently "
                "ignored.",
                messages={
                    "ko": f"매핑이 카운트에 없는 지점 {unknown_sites}을(를) 지정했습니다. "
                    "적용될 곳 없는 항목을 조용히 무시하지 않습니다.",
                    "en": f"The map names site(s) {unknown_sites} absent from the counts; an "
                    "entry meant to apply somewhere is never silently ignored.",
                    "ja": f"対応表がカウントに存在しない地点 {unknown_sites} を指定しています。"
                    "適用先のない項目を黙って無視しません。",
                },
                citation=CIT_S5541_VEHICLE_INPUTS,
                context={"unknown_sites": unknown_sites},
            )
        )
    if unknown_approaches:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_map_names_unknown_approach",
                f"The approach→link map names approach(es) {sorted(unknown_approaches)} that the "
                "counts do not contain.",
                messages={
                    "ko": f"매핑이 카운트에 없는 접근로 {sorted(unknown_approaches)}을(를) "
                    "지정했습니다.",
                    "en": f"The map names approach(es) {sorted(unknown_approaches)} absent from "
                    "the counts.",
                    "ja": f"対応表がカウントに存在しない流入路 {sorted(unknown_approaches)} を"
                    "指定しています。",
                },
                citation=CIT_S5541_VEHICLE_INPUTS,
                context={"unknown_approaches": sorted(unknown_approaches)},
            )
        )
    return ApproachLinkSiteCheck(
        issues=tuple(issues),
        unknown_sites=tuple(unknown_sites),
        unknown_approaches=tuple(unknown_approaches),
    )


def generate_demand(
    draft: DraftNetwork,
    result: ImportResult,
    *,
    approach_links: Mapping[str, Mapping[str, int]],
    vehicle_types: Mapping[str, VehicleTypeRef],
    window: tuple[datetime, datetime] | None = None,
    settings: DemandSettings | None = None,
) -> DraftDemand:
    """A draft's vehicle inputs, compositions and static routes from counts.

    ``approach_links`` maps **site id → {approach leg id → draft link id}** and
    ``vehicle_types`` maps **observed class code → :class:`VehicleTypeRef`**.
    Both are required and both are validated in both directions before anything
    is built — see the module docstring's "The join".

    ``window`` is handed straight to
    :func:`~services.dataimport.demand.build_demand_seeding`, which records the
    choice as an INFO rather than inferring a peak for itself. It also bounds
    the counts this module sums for the routing weights, so the two arms can
    never describe different hours.

    Returns a :class:`DraftDemand` **always** — the import layer's "report, do
    not raise" contract. ``result.ok`` is false and every collection is empty
    when any check refused.
    """
    settings = settings or DEFAULT_SETTINGS
    issues: list[ImportIssue] = []
    coverage = DemandCoverage()

    def _bail() -> DraftDemand:
        return DraftDemand(
            issues=tuple(issues), coverage=coverage, settings=settings
        )

    # -- 1. the 1.2 bridge must have produced a seeding at all ---------------
    seeding = build_demand_seeding(result, window=window)
    issues.extend(seeding.issues)
    if not seeding.ok:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_seeding_refused",
                "The counts→demand bridge refused, so there is nothing to seed a network "
                f"with (codes: {sorted(seeding.issue_codes(IssueSeverity.REFUSE))}). Emitting "
                "vehicle inputs from a refused seeding would put numbers nobody stands behind "
                "into a file that looks loaded.",
                messages={
                    "ko": "카운트→수요 변환이 거부되어 시드할 수요가 없습니다"
                    f"(코드: {sorted(seeding.issue_codes(IssueSeverity.REFUSE))}). 거부된 "
                    "시딩으로 차량 유입을 만들면 아무도 책임지지 않는 수치가 파일에 들어갑니다.",
                    "en": "The counts→demand bridge refused, so there is no demand to seed "
                    f"(codes: {sorted(seeding.issue_codes(IssueSeverity.REFUSE))}).",
                    "ja": "カウント→需要変換が拒否されたため、シードする需要がありません"
                    f"(コード: {sorted(seeding.issue_codes(IssueSeverity.REFUSE))})。",
                },
                citation=CIT_S5541_VEHICLE_INPUTS,
                context={"seeding_refusals": sorted(seeding.issue_codes(IssueSeverity.REFUSE))},
            )
        )
        return _bail()

    # The routing arm sums the SAME records the profile arm did: 1.2's window
    # predicate, then 1.2's summable predicate. Never a second definition.
    counts = list(result.counts)
    if window is not None:
        start, end = window
        counts = [c for c in counts if start <= c.interval_start < end]
    movement_counts = seedable_counts(counts)

    # -- 2. validate the approach→link map, exhaustively ---------------------
    observed_sites = sorted({profile.site_id for profile in seeding.profiles})
    observed_approaches: dict[str, set[str]] = defaultdict(set)
    for profile in seeding.profiles:
        observed_approaches[profile.site_id].add(profile.approach_leg_id)
    for site_id in observed_sites:
        coverage.add("sites_observed", site_id)
        for leg in sorted(observed_approaches[site_id]):
            coverage.add("approaches_observed", f"{site_id}|{leg}")

    if not approach_links:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_no_approach_link_map",
                "No approach→link map was supplied, so no observed approach can be attached to "
                f"a draft link. The counts cover {len(observed_sites)} site(s) "
                f"({observed_sites}); a count site id is a survey label and a draft link id is a "
                "number this package assigned from a sorted OSM way id, and guessing the join — "
                "by position, by ordering, by name similarity — produces a confidently wrong "
                "network.",
                messages={
                    "ko": f"접근로→링크 매핑이 없어 관측 접근로를 초안 링크에 붙일 수 없습니다"
                    f"(관측 지점: {observed_sites}). 조사 지점 id와 링크 id는 서로 다른 체계이며 "
                    "추측한 연결은 확신에 찬 오답을 만듭니다.",
                    "en": "No approach→link map was supplied, so no observed approach can be "
                    f"attached to a draft link (sites needing one: {observed_sites}).",
                    "ja": f"流入路→リンクの対応表がないため、観測流入路を草案リンクに接続できません"
                    f"(対象地点: {observed_sites})。推測による接続は誤った結果を生みます。",
                },
                citation=CIT_S5541_VEHICLE_INPUTS,
                context={"sites_needing_mapping": observed_sites},
            )
        )
        for site_id in observed_sites:
            coverage.add("sites_without_link_map", site_id)
        return _bail()

    link_by_id = {link.id: link for link in draft.links}
    site_check = check_approach_link_sites(
        approach_links, observed_sites=observed_sites, observed_approaches=observed_approaches
    )
    issues.extend(site_check.issues)
    unknown_approaches = list(site_check.unknown_approaches)
    unknown_links: list[str] = []
    seen_links: dict[int, str] = {}
    duplicate_links: list[str] = []
    for site_id in sorted(approach_links):
        for leg, link_id in sorted(approach_links[site_id].items()):
            label = f"{site_id}|{leg}"
            if link_id not in link_by_id:
                unknown_links.append(f"{label}->link {link_id}")
            if link_id in seen_links and seen_links[link_id] != label:
                duplicate_links.append(f"link {link_id} <- {seen_links[link_id]}, {label}")
            seen_links.setdefault(link_id, label)

    for site_id in site_check.unknown_sites:
        coverage.add("mapped_sites_absent_from_counts", site_id)
    for label in unknown_approaches:
        coverage.add("mapped_approaches_absent_from_counts", label)
    for label in unknown_links:
        coverage.add("mapped_links_absent_from_draft", label)

    if unknown_links:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_map_names_unknown_link",
                f"The approach→link map names link(s) this draft does not contain: "
                f"{sorted(unknown_links)}.",
                messages={
                    "ko": f"매핑이 초안에 없는 링크를 지정했습니다: {sorted(unknown_links)}.",
                    "en": f"The map names link(s) absent from this draft: {sorted(unknown_links)}.",
                    "ja": f"対応表が草案に存在しないリンクを指定しています: {sorted(unknown_links)}。",
                },
                citation=CIT_S5541_VEHICLE_INPUTS,
                context={"unknown_links": sorted(unknown_links)},
            )
        )
    if duplicate_links:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_duplicate_link_mapping",
                f"Two approaches claim the same draft link: {sorted(duplicate_links)}. One link "
                "carrying two approaches' demand would double it, and there is no arithmetic "
                "that makes that right.",
                messages={
                    "ko": f"두 접근로가 같은 링크를 가리킵니다: {sorted(duplicate_links)}. 한 "
                    "링크가 두 접근로의 수요를 받으면 수요가 두 배가 됩니다.",
                    "en": f"Two approaches claim the same draft link: {sorted(duplicate_links)}; "
                    "that link would carry double demand.",
                    "ja": f"2つの流入路が同じリンクを指しています: {sorted(duplicate_links)}。"
                    "そのリンクの需要が二重になります。",
                },
                citation=CIT_S5541_VEHICLE_INPUTS,
                context={"duplicate_links": sorted(duplicate_links)},
            )
        )

    # A site the caller mapped at all must be mapped COMPLETELY. A site not
    # mapped at all is simply outside the draft — named, not refused.
    partial: list[str] = []
    for site_id in observed_sites:
        mapped_legs = set(approach_links.get(site_id, {}))
        if not mapped_legs:
            coverage.add("sites_without_link_map", site_id)
            continue
        missing = sorted(observed_approaches[site_id] - mapped_legs)
        if missing:
            partial.extend(f"{site_id}|{leg}" for leg in missing)
        else:
            coverage.add("sites_joined", site_id)
    if partial:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_mapped_site_missing_approaches",
                f"Approach(es) {sorted(partial)} were observed at a site the map otherwise "
                "covers, but were given no link. A half-mapped intersection loads some of its "
                "demand and drops the rest, which is worse than loading none: the network runs, "
                "the totals are wrong, and nothing says so.",
                messages={
                    "ko": f"매핑된 지점의 접근로 {sorted(partial)}에 링크가 지정되지 않았습니다. "
                    "일부만 매핑된 교차로는 수요 일부만 반영되어 아무 경고 없이 총량이 틀립니다.",
                    "en": f"Approach(es) {sorted(partial)} at a mapped site have no link; a "
                    "half-mapped intersection loads part of its demand and silently drops the rest.",
                    "ja": f"対応済み地点の流入路 {sorted(partial)} にリンクがありません。"
                    "一部だけ対応した交差点は需要の一部のみ反映され、総量が静かに狂います。",
                },
                citation=CIT_S5541_VEHICLE_INPUTS,
                context={"unmapped_approaches": sorted(partial)},
            )
        )

    # -- 3. validate the class→vehicle-type map, exhaustively ----------------
    observed_classes = sorted(
        {share.vehicle_class.code for profile in seeding.compositions for share in profile.shares}
    )
    for code in observed_classes:
        coverage.add("classes_observed", code)
    if not vehicle_types:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_no_vehicle_type_map",
                "No class→vehicle-type map was supplied, so no vehicle composition can be "
                f"written. The counts carry {len(observed_classes)} class code(s) "
                f"({observed_classes}); §5.2.3 wants Austroads types with Table 5-2's "
                "acceleration series and Table 5-3's power/weight distributions, none of which "
                "is a property of a turning count.",
                messages={
                    "ko": f"차종 매핑이 없어 차량 구성을 만들 수 없습니다(관측 차종: "
                    f"{observed_classes}). §5.2.3의 차종 정의는 카운트 자료의 속성이 아닙니다.",
                    "en": "No class→vehicle-type map was supplied, so no vehicle composition can "
                    f"be written (observed classes: {observed_classes}).",
                    "ja": f"車種対応表がないため車両構成を作成できません(観測車種: "
                    f"{observed_classes})。§5.2.3 の車種定義はカウント資料の属性ではありません。",
                },
                citation=CIT_S523_VEHICLE_TYPES,
                context={"classes_needing_mapping": observed_classes},
            )
        )
        for code in observed_classes:
            coverage.add("classes_without_vehicle_type", code)
        return _bail()

    missing_classes = [code for code in observed_classes if code not in vehicle_types]
    unknown_classes = sorted(set(vehicle_types) - set(observed_classes))
    for code in missing_classes:
        coverage.add("classes_without_vehicle_type", code)
    for code in unknown_classes:
        coverage.add("mapped_classes_absent_from_counts", code)
    if missing_classes:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_class_without_vehicle_type",
                f"Observed class code(s) {missing_classes} have no Vissim vehicle type. Dropping "
                "them would silently shrink the composition and, because relative flows are raw "
                "counts, silently reweight every class that remains.",
                messages={
                    "ko": f"관측 차종 {missing_classes}에 대응하는 Vissim 차종 번호가 없습니다. "
                    "빼버리면 구성비가 조용히 재계산되어 남은 차종의 비중이 달라집니다.",
                    "en": f"Observed class code(s) {missing_classes} have no Vissim vehicle "
                    "type; dropping them would silently reweight every remaining class.",
                    "ja": f"観測車種 {missing_classes} に対応する Vissim 車種番号がありません。"
                    "除外すると残りの車種の構成比が静かに変わります。",
                },
                citation=CIT_S523_VEHICLE_TYPES,
                context={"classes_without_vehicle_type": missing_classes},
            )
        )
    if unknown_classes:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_demand_vehicle_type_map_names_unknown_class",
                f"The class→vehicle-type map names class code(s) {unknown_classes} the counts do "
                "not contain. An entry the caller meant to apply somewhere is never silently "
                "ignored.",
                messages={
                    "ko": f"차종 매핑이 카운트에 없는 코드 {unknown_classes}을(를) 지정했습니다.",
                    "en": f"The vehicle-type map names class code(s) {unknown_classes} absent "
                    "from the counts.",
                    "ja": f"車種対応表がカウントに存在しないコード {unknown_classes} を"
                    "指定しています。",
                },
                citation=CIT_S523_VEHICLE_TYPES,
                context={"unknown_classes": unknown_classes},
            )
        )

    if any(issue.severity is IssueSeverity.REFUSE for issue in issues):
        return _bail()

    # -- 4. compositions (§5.5.1) -------------------------------------------
    compositions = _build_compositions(seeding, vehicle_types, issues)
    composition_by_site = {plan.site_id: plan for plan in compositions}

    # -- 5. vehicle inputs (§5.5.4.1) ---------------------------------------
    inputs = _build_inputs(
        draft,
        seeding,
        approach_links,
        composition_by_site,
        settings,
        issues,
        coverage,
    )

    # -- 6. routing decisions (§5.5.4.2) ------------------------------------
    decisions = _build_decisions(
        draft, movement_counts, approach_links, link_by_id, settings, issues, coverage
    )

    _classify_seeded_links(draft, approach_links, link_by_id, inputs, decisions, coverage)

    if any(issue.severity is IssueSeverity.REFUSE for issue in issues):
        return _bail()

    # -- 7. always-on disclosures -------------------------------------------
    _disclosures(inputs, compositions, decisions, settings, issues)

    return DraftDemand(
        inputs=tuple(inputs),
        compositions=tuple(compositions),
        decisions=tuple(decisions),
        issues=tuple(issues),
        compliance=tuple(
            _compliance(inputs, compositions, decisions, settings, issues, coverage)
        ),
        coverage=coverage,
        settings=settings,
    )


def _build_compositions(
    seeding: DemandSeeding,
    vehicle_types: Mapping[str, VehicleTypeRef],
    issues: list[ImportIssue],
) -> list[VehicleCompositionPlan]:
    """One composition per site, numbered 1..N in ascending site id.

    Per-site rather than per-approach for 1.2's own reason: "a Vissim vehicle
    composition is shared by every input that references it; splitting
    composition by approach is a modelling choice that needs more than one
    composition object". Splitting it here would be making that choice for the
    modeller.
    """
    plans: list[VehicleCompositionPlan] = []
    for number, profile in enumerate(
        sorted(seeding.compositions, key=lambda p: p.site_id), start=1
    ):
        flows = tuple(
            CompositionFlow(
                veh_type_no=vehicle_types[share.vehicle_class.code].veh_type_no,
                des_speed_distr_no=vehicle_types[share.vehicle_class.code].des_speed_distr_no,
                rel_flow=share.rel_flow,
                share=share.share,
                class_code=share.vehicle_class.code,
                class_scheme=share.vehicle_class.scheme.value,
                counted_records=share.counted_records,
            )
            for share in sorted(profile.shares, key=lambda s: s.vehicle_class.code)
        )
        plan = VehicleCompositionPlan(
            no=number, name=profile.site_id, site_id=profile.site_id, flows=flows
        )
        if not flows or abs(plan.share_sum - 1.0) > SHARE_SUM_TOLERANCE:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "genesis_demand_composition_shares_do_not_sum",
                    f"Site {profile.site_id}'s class shares sum to {plan.share_sum!r} across "
                    f"{len(flows)} class(es), not 1. A composition whose parts do not make a "
                    "whole is not a composition — either a class was lost between the counts and "
                    "here, or the observed total was zero.",
                    messages={
                        "ko": f"지점 {profile.site_id}의 차종 구성비 합이 {plan.share_sum!r}로 "
                        "1이 아닙니다. 부분의 합이 전체가 아니면 구성이라 할 수 없습니다.",
                        "en": f"Site {profile.site_id}'s class shares sum to {plan.share_sum!r}, "
                        "not 1; a composition whose parts do not make a whole is not one.",
                        "ja": f"地点 {profile.site_id} の車種構成比の合計が {plan.share_sum!r} で "
                        "1 になりません。部分の和が全体にならない構成は構成ではありません。",
                    },
                    subject=f"site {profile.site_id}",
                    citation=CIT_S551_VEHICLE_COMPOSITION,
                    context={"share_sum": plan.share_sum, "classes": len(flows)},
                )
            )
        plans.append(plan)
    return plans


def _reduce(profile: VehicleInputPlan, reduction: str | None) -> float:
    if reduction == REDUCTION_MEAN:
        return profile.mean_volume_vph
    return profile.peak_volume_vph


def _build_inputs(
    draft: DraftNetwork,
    seeding: DemandSeeding,
    approach_links: Mapping[str, Mapping[str, int]],
    composition_by_site: Mapping[str, VehicleCompositionPlan],
    settings: DemandSettings,
    issues: list[ImportIssue],
    coverage: DemandCoverage,
) -> list[VehicleInputPlan]:
    """One vehicle input per joined ENTRY link, numbered by ascending link id."""
    entries = {link.id for link in entry_links(draft)}
    staged: list[tuple[int, VehicleInputProfile]] = []
    for profile in seeding.profiles:
        link_id = approach_links.get(profile.site_id, {}).get(profile.approach_leg_id)
        if link_id is None:
            continue
        coverage.add("approaches_joined", f"{profile.site_id}|{profile.approach_leg_id}")
        if link_id in entries:
            staged.append((link_id, profile))

    plans: list[VehicleInputPlan] = []
    for number, (link_id, profile) in enumerate(sorted(staged, key=lambda p: p[0]), start=1):
        subject = f"site {profile.site_id} / approach {profile.approach_leg_id} / link {link_id}"
        intervals = [
            DemandInterval(
                index=source.interval_no,
                start=source.start,
                end=source.end,
                volume_vph=source.volume_vph,
                counted_volume=source.counted_volume,
                interval_minutes=source.interval_minutes,
                contributing_records=source.contributing_records,
            )
            for source in profile.intervals
        ]

        if settings.expected_interval_minutes is not None:
            wrong = sorted(
                {
                    interval.interval_minutes
                    for interval in intervals
                    if abs(interval.interval_minutes - settings.expected_interval_minutes) > 1e-9
                }
            )
            if wrong:
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "genesis_demand_interval_length_mismatch",
                        f"{subject} was counted in {wrong} minute bins, but "
                        f"{settings.expected_interval_minutes:g}-minute bins were required. "
                        "Re-binning changes the peak, so it is not done here; the source is "
                        "reported as what it is.",
                        messages={
                            "ko": f"{subject}의 집계 구간이 {wrong}분으로, 요구된 "
                            f"{settings.expected_interval_minutes:g}분과 다릅니다. 재집계는 "
                            "첨두값을 바꾸므로 여기서 하지 않습니다.",
                            "en": f"{subject} was counted in {wrong} minute bins, not the "
                            f"required {settings.expected_interval_minutes:g}; re-binning "
                            "changes the peak and is not done here.",
                            "ja": f"{subject} の集計区間は {wrong} 分で、要求された "
                            f"{settings.expected_interval_minutes:g} 分と異なります。"
                            "再集計はピークを変えるため行いません。",
                        },
                        subject=subject,
                        citation=CIT_S5541_FIFTEEN_MINUTE_INTERVALS,
                        context={
                            "observed_interval_minutes": wrong,
                            "required_interval_minutes": settings.expected_interval_minutes,
                        },
                    )
                )
                continue

        reduction = settings.single_interval_reduction if len(intervals) > 1 else None
        if len(intervals) > 1 and reduction is None:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "genesis_demand_profile_needs_interval_reduction",
                    f"{subject} carries a {len(intervals)}-interval demand profile, but this "
                    "emission can write only one interval: no Vissim-saved network available to "
                    "this repo has a time-interval set with more than one interval, so the "
                    "structure that expresses a profile has never been measured. Which single "
                    "figure represents the modelled period is a modelling decision — set "
                    "DemandSettings.single_interval_reduction to 'peak' or 'mean' — and it is "
                    "not made here.",
                    messages={
                        "ko": f"{subject}은(는) {len(intervals)}개 구간 수요 프로파일을 갖지만 "
                        "이 출력은 한 구간만 쓸 수 있습니다(다구간 시간구간 집합이 실측된 "
                        "Vissim 저장 파일이 없음). 어떤 값을 대표로 쓸지는 모델러의 결정이므로 "
                        "single_interval_reduction('peak' 또는 'mean')을 지정하십시오.",
                        "en": f"{subject} carries a {len(intervals)}-interval profile but this "
                        "emission can write one interval (a multi-interval time-interval set is "
                        "unmeasured); set single_interval_reduction to 'peak' or 'mean'.",
                        "ja": f"{subject} は {len(intervals)} 区間の需要プロファイルを持ちますが、"
                        "この出力は1区間しか書けません(複数区間の時間区間集合は未計測)。"
                        "代表値の選択は modeller の判断です — single_interval_reduction を"
                        "'peak' か 'mean' に設定してください。",
                    },
                    subject=subject,
                    citation=CIT_S5541_FIFTEEN_MINUTE_INTERVALS,
                    context={"intervals": len(intervals)},
                )
            )
            continue
        if reduction is not None and reduction not in (REDUCTION_PEAK, REDUCTION_MEAN):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "genesis_demand_profile_needs_interval_reduction",
                    f"single_interval_reduction={reduction!r} is not one of "
                    f"{[REDUCTION_PEAK, REDUCTION_MEAN]}.",
                    messages={
                        "ko": f"single_interval_reduction={reduction!r}은(는) "
                        f"{[REDUCTION_PEAK, REDUCTION_MEAN]} 중 하나가 아닙니다.",
                        "en": f"single_interval_reduction={reduction!r} is not one of "
                        f"{[REDUCTION_PEAK, REDUCTION_MEAN]}.",
                        "ja": f"single_interval_reduction={reduction!r} は "
                        f"{[REDUCTION_PEAK, REDUCTION_MEAN]} のいずれでもありません。",
                    },
                    subject=subject,
                    citation=CIT_S5541_FIFTEEN_MINUTE_INTERVALS,
                    context={"single_interval_reduction": reduction},
                )
            )
            continue

        draft_plan = VehicleInputPlan(
            no=number,
            link_id=link_id,
            site_id=profile.site_id,
            approach_leg_id=profile.approach_leg_id,
            composition_no=composition_by_site[profile.site_id].no,
            intervals=tuple(intervals),
            reduction=reduction,
            emitted_volume_vph=0.0,
        )
        volume = _reduce(draft_plan, reduction)
        if volume <= 0.0:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "genesis_demand_zero_observed_total",
                    f"{subject} has an observed total of {draft_plan.total_counted:g} vehicles, "
                    "so the seeded volume is zero. A vehicle input written at zero asserts there "
                    "is no demand on this entry; the survey asserts nothing of the kind, it just "
                    "did not see any here.",
                    messages={
                        "ko": f"{subject}의 관측 총량이 {draft_plan.total_counted:g}대여서 유입량이 "
                        "0입니다. 0으로 쓰면 '수요 없음'을 주장하게 되지만 조사는 그런 주장을 "
                        "하지 않습니다.",
                        "en": f"{subject} has an observed total of "
                        f"{draft_plan.total_counted:g}; an input written at zero asserts there is "
                        "no demand, which the survey does not.",
                        "ja": f"{subject} の観測総量が {draft_plan.total_counted:g} 台のため流入量が "
                        "0 になります。0 と書くと「需要なし」の主張になりますが、調査はそう"
                        "述べていません。",
                    },
                    subject=subject,
                    citation=CIT_S5541_VEHICLE_INPUTS,
                    context={"counted_total": draft_plan.total_counted},
                )
            )
            continue

        emitted_index = _emitted_index(draft_plan, reduction)
        plans.append(
            VehicleInputPlan(
                no=number,
                link_id=link_id,
                site_id=profile.site_id,
                approach_leg_id=profile.approach_leg_id,
                composition_no=composition_by_site[profile.site_id].no,
                intervals=tuple(
                    DemandInterval(
                        index=interval.index,
                        start=interval.start,
                        end=interval.end,
                        volume_vph=interval.volume_vph,
                        counted_volume=interval.counted_volume,
                        interval_minutes=interval.interval_minutes,
                        contributing_records=interval.contributing_records,
                        emitted=(interval.index == emitted_index),
                    )
                    for interval in intervals
                ),
                reduction=reduction,
                emitted_volume_vph=volume,
            )
        )
    return plans


def _emitted_index(plan: VehicleInputPlan, reduction: str | None) -> int | None:
    """Which source interval the emitted figure IS, when it is one of them.

    ``peak`` and the single-interval case name a real interval; ``mean`` names
    none, and says so with ``None`` rather than pointing at whichever bin
    happened to be closest.
    """
    if reduction == REDUCTION_MEAN:
        return None
    peak = plan.peak_volume_vph
    for interval in plan.intervals:
        if interval.volume_vph == peak:
            return interval.index
    return None


def _build_decisions(
    draft: DraftNetwork,
    movement_counts: Sequence[TurningCountData],
    approach_links: Mapping[str, Mapping[str, int]],
    link_by_id: Mapping[int, DraftLink],
    settings: DemandSettings,
    issues: list[ImportIssue],
    coverage: DemandCoverage,
) -> list[RoutingDecisionSeed]:
    """One decision per joined approach that has connectors, one route per turn."""
    # (site, leg) -> turn -> [volume, records]
    observed: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0])
    )
    for count in movement_counts:
        turn = _movement_turn(count)
        key = (count.site_id, count.approach.leg_id)
        if turn is None:
            coverage.add(
                "movements_without_turn",
                f"{count.site_id}|{count.approach.leg_id}|{count.movement.turn_raw or '?'}",
            )
            continue
        bucket = observed[key][turn]
        bucket[0] += count.volume
        bucket[1] += 1

    staged: list[tuple[int, str, str, dict[str, list[float]]]] = []
    for site_id in sorted(approach_links):
        for leg, link_id in sorted(approach_links[site_id].items()):
            if link_id not in link_by_id:
                continue
            staged.append((link_id, site_id, leg, dict(observed.get((site_id, leg), {}))))

    seeds: list[RoutingDecisionSeed] = []
    number = 0
    for link_id, site_id, leg, by_turn in sorted(staged, key=lambda item: item[0]):
        connectors = link_exits(draft, link_id)
        if not connectors:
            continue
        subject = f"site {site_id} / approach {leg} / link {link_id}"

        drafted: dict[str, list[DraftConnector]] = defaultdict(list)
        for connector in connectors:
            drafted[connector.turn].append(connector)

        ambiguous = sorted(turn for turn, group in drafted.items() if len(group) > 1)
        if ambiguous:
            for turn in ambiguous:
                coverage.add("turns_with_multiple_connectors", f"{subject} / {turn}")
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "genesis_demand_turn_has_multiple_connectors",
                    f"{subject} has more than one connector for turn(s) {ambiguous}, so one "
                    "observed movement volume would have to be split across them. How a "
                    "surveyed turn divides between two drafted paths is a modelling decision "
                    "the counts do not state, and it is not invented here.",
                    messages={
                        "ko": f"{subject}의 회전 {ambiguous}에 커넥터가 둘 이상이어서 관측 "
                        "이동량을 나눠야 합니다. 분할 비율은 카운트에 없으므로 지어내지 않습니다.",
                        "en": f"{subject} has more than one connector for turn(s) {ambiguous}; "
                        "how one surveyed movement divides between them is not in the counts.",
                        "ja": f"{subject} の回転 {ambiguous} にコネクタが複数あり、観測移動量を"
                        "分割する必要があります。分割比はカウントに無いため作成しません。",
                    },
                    subject=subject,
                    citation=CIT_S5542_VEHICLE_ROUTES,
                    context={"ambiguous_turns": ambiguous},
                )
            )
            continue

        for turn in sorted(set(by_turn) - set(drafted)):
            coverage.add("turns_observed_not_drafted", f"{subject} / {turn}")
        for turn in sorted(set(drafted) - set(by_turn)):
            coverage.add("turns_drafted_not_observed", f"{subject} / {turn}")

        usable = sorted(set(by_turn) & set(drafted))
        if not usable:
            continue

        total = sum(by_turn[turn][0] for turn in usable)
        number += 1
        routes: list[RouteSeed] = []
        ordered = sorted(usable, key=lambda turn: drafted[turn][0].id)
        for route_no, turn in enumerate(ordered, start=1):
            connector = drafted[turn][0]
            volume = by_turn[turn][0]
            exit_link = link_by_id[connector.to_link]
            routes.append(
                RouteSeed(
                    no=route_no,
                    turn=turn,
                    connector_id=connector.id,
                    dest_link_id=connector.to_link,
                    dest_pos_m=round(exit_link.length_m, 3),
                    rel_flow=volume,
                    observed_volume=volume,
                    share=route_share(volume, total - volume),
                    contributing_records=int(by_turn[turn][1]),
                )
            )
        seeds.append(
            RoutingDecisionSeed(
                no=number,
                link_id=link_id,
                pos_m=settings.routing_decision_pos_m,
                site_id=site_id,
                approach_leg_id=leg,
                observed_total=total,
                routes=tuple(routes),
            )
        )

    unmatched = sorted(
        value
        for key in ("turns_observed_not_drafted", "turns_drafted_not_observed")
        for value in coverage.entries[key]
    )
    if unmatched:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "genesis_demand_turn_not_matched",
                f"{len(unmatched)} turn(s) exist on only one side of the join: "
                f"{unmatched}. An observed turn the draft does not have gets NO route (its "
                "vehicles would otherwise be reassigned to its siblings), and a drafted turn "
                "nobody counted gets no route either — a route written at zero and a route left "
                "out both mean 'no vehicles', but only one of them says so.",
                citation=CIT_S5542_VEHICLE_ROUTES,
                context={"unmatched_turns": unmatched},
            )
        )
    return seeds


def _classify_seeded_links(
    draft: DraftNetwork,
    approach_links: Mapping[str, Mapping[str, int]],
    link_by_id: Mapping[int, DraftLink],
    inputs: Sequence[VehicleInputPlan],
    decisions: Sequence[RoutingDecisionSeed],
    coverage: DemandCoverage,
) -> None:
    """Say, per mapped link, which of the two objects it actually received.

    A mapped link that is an interior approach gets routing but no input — its
    traffic entered upstream, and a second input there would double it. A mapped
    link that is an entry with no connectors gets an input but no routing. Both
    are legitimate; both are named, so neither can be mistaken for a bug or for
    a success.
    """
    with_input = {plan.link_id for plan in inputs}
    with_routing = {seed.link_id for seed in decisions}
    for site_id in sorted(approach_links):
        for leg, link_id in sorted(approach_links[site_id].items()):
            if link_id not in link_by_id:
                continue
            label = f"{site_id}|{leg}->link {link_id}"
            has_input, has_routing = link_id in with_input, link_id in with_routing
            if has_input and has_routing:
                coverage.add("links_seeded_input_and_routing", label)
            elif has_input:
                coverage.add("links_seeded_input_only", label)
            elif has_routing:
                coverage.add("links_seeded_routing_only", label)
            else:
                coverage.add("links_seeded_neither", label)


def _disclosures(
    inputs: Sequence[VehicleInputPlan],
    compositions: Sequence[VehicleCompositionPlan],
    decisions: Sequence[RoutingDecisionSeed],
    settings: DemandSettings,
    issues: list[ImportIssue],
) -> None:
    """What this module decided for itself, said out loud on every run."""
    issues.append(
        _issue(
            IssueSeverity.INFO,
            "genesis_demand_vehicle_types_not_declared_here",
            "The emitted vehicle compositions reference vehicle types and desired-speed "
            "distributions by number, and this emission declares NEITHER container: a Vissim "
            "<vehicleType> reaches ten further containers, and §5.2.3 requires MRWA's own "
            "Austroads types rather than Vissim's factory defaults. A demand-carrying file is "
            "therefore valid only against a network that already declares "
            f"{sorted({flow.veh_type_no for plan in compositions for flow in plan.flows})} and "
            f"{sorted({flow.des_speed_distr_no for plan in compositions for flow in plan.flows})}. "
            "An unresolved reference makes Vissim drop the container and report a successful "
            "load, so this is stated rather than assumed.",
            citation=CIT_S523_VEHICLE_TYPES,
            context={
                "veh_type_nos": sorted(
                    {flow.veh_type_no for plan in compositions for flow in plan.flows}
                ),
                "des_speed_distr_nos": sorted(
                    {flow.des_speed_distr_no for plan in compositions for flow in plan.flows}
                ),
            },
        )
    )
    issues.append(
        _issue(
            IssueSeverity.INFO,
            "genesis_demand_composition_is_per_site",
            f"{len(compositions)} vehicle composition(s) were written, one per counted site, "
            "shared by every input of that site. Splitting composition by approach needs more "
            "than one composition object and is a modelling choice, so it is not made here.",
            citation=CIT_S551_VEHICLE_COMPOSITION,
            context={"compositions": len(compositions)},
        )
    )
    if decisions:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "genesis_demand_routing_decision_at_link_start",
                f"{len(decisions)} routing decision(s) were placed at "
                f"{settings.routing_decision_pos_m:g} m on their approach link — the upstream end "
                "of the link, the earliest position the draft's geometry offers, so every vehicle "
                "on the approach passes the marker. §5.5.4.2 asks for the point where drivers "
                "begin changing lanes, which is an on-site observation a generated draft does not "
                "have.",
                citation=CIT_S5542_VEHICLE_ROUTES,
                context={
                    "decisions": len(decisions),
                    "pos_m": settings.routing_decision_pos_m,
                },
            )
        )
    reduced = [plan for plan in inputs if plan.profiled]
    if reduced:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "genesis_demand_profile_reduced_to_single_interval",
                f"{len(reduced)} input(s) were counted as a demand profile and written as ONE "
                f"interval by the '{settings.single_interval_reduction}' reduction: "
                + "; ".join(
                    f"link {plan.link_id}: {len(plan.intervals)} intervals "
                    f"(peak {plan.peak_volume_vph:g}, mean {plan.mean_volume_vph:g} veh/h) "
                    f"→ {plan.emitted_volume_vph:g} veh/h"
                    for plan in reduced
                )
                + ". The whole profile is kept on the plan; the file cannot carry it because no "
                "Vissim-saved network available to this repo has a multi-interval time-interval "
                "set to copy the structure from.",
                citation=CIT_S5541_FIFTEEN_MINUTE_INTERVALS,
                context={
                    "reduction": settings.single_interval_reduction,
                    "inputs": [plan.link_id for plan in reduced],
                },
            )
        )


# ---------------------------------------------------------------------------
# Compliance — §5.2.2, §5.2.3, §5.5.1, §5.5.4.1, §5.5.4.2, §5.6.1.3
# ---------------------------------------------------------------------------


def _compliance(
    inputs: Sequence[VehicleInputPlan],
    compositions: Sequence[VehicleCompositionPlan],
    decisions: Sequence[RoutingDecisionSeed],
    settings: DemandSettings,
    issues: Sequence[ImportIssue],
    coverage: DemandCoverage,
) -> list[ComplianceItem]:
    """The demand rules, graded applied / violated-named / not-checkable.

    Same three states, same shape and same renderer as ``draft._compliance`` —
    these items are appended to the draft's own report, not kept in a second one.
    """
    items: list[ComplianceItem] = []

    items.append(
        ComplianceItem(
            rule_id="5.2.2-time-period-settings",
            section="§5.2.2",
            doc_page=181,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S522_TIME_PERIODS,
            evidence={
                "counted_window_minutes": round(
                    max((plan.total_minutes for plan in inputs), default=0.0), 3
                ),
                "warm_up_cool_down_written": False,
            },
            detail={
                "ko": "웜업·쿨다운과 시뮬레이션 기간은 <simulation> 컨테이너의 값인데 이 출력은 "
                "그 컨테이너를 쓰지 않습니다(파서가 읽지 않는 객체는 내보내지 않는다는 규칙). "
                "관측 창의 길이만 근거로 제시합니다.",
                "en": "Warm-up, cool-down and the simulation period live in the <simulation> "
                "container, which this emission does not write (nothing is emitted that this "
                "repo's parser cannot read back). The counted window length is given as evidence.",
                "ja": "ウォームアップ・クールダウンと解析期間は <simulation> コンテナの値ですが、"
                "この出力はそのコンテナを書きません(パーサが読めないオブジェクトは出力しない方針)。"
                "観測窓の長さのみ根拠として示します。",
            },
        )
    )

    veh_types = sorted({flow.veh_type_no for plan in compositions for flow in plan.flows})
    items.append(
        ComplianceItem(
            rule_id="5.2.3-vehicle-types-declared",
            section="§5.2.3",
            doc_page=182,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S523_VEHICLE_TYPES,
            instances=len(veh_types),
            subjects=[f"vehType {no}" for no in veh_types],
            evidence={
                "veh_type_nos": veh_types,
                "vehicle_types_container_written": False,
                "class_codes": sorted(
                    {flow.class_code for plan in compositions for flow in plan.flows}
                ),
            },
            detail={
                "ko": "차종 번호는 호출자가 제공했고 이 파일은 <vehicleTypes>를 선언하지 않습니다. "
                "§5.2.3은 Austroads 분류와 표 5-2/5-3/5-4의 가속·중량·색상 분포를 요구하는데 "
                "그 값들은 교통량 조사의 속성이 아닙니다. 이 수요는 해당 차종을 이미 가진 "
                "네트워크에서만 유효합니다.",
                "en": "The vehicle type numbers are the caller's and this file declares no "
                "<vehicleTypes>. §5.2.3 requires the Austroads classification with Tables "
                "5-2/5-3/5-4's acceleration, power/weight and colour distributions, none of which "
                "is a property of a turning count. This demand is valid only against a network "
                "that already declares those types.",
                "ja": "車種番号は呼び出し側の値であり、このファイルは <vehicleTypes> を宣言しません。"
                "§5.2.3 は Austroads 分類と表 5-2/5-3/5-4 の加速度・重量・色分布を要求しますが、"
                "それらは交通量調査の属性ではありません。この需要は当該車種を持つネットワークで"
                "のみ有効です。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.5.1-composition-from-classified-counts",
            section="§5.5.1",
            doc_page=204,
            state=ComplianceState.APPLIED,
            citation=CIT_S551_VEHICLE_COMPOSITION,
            instances=len(compositions),
            subjects=[f"composition {plan.no} ({plan.site_id})" for plan in compositions],
            evidence={
                "sites": [plan.site_id for plan in compositions],
                "classes_per_site": [len(plan.flows) for plan in compositions],
                "rel_flow_is_raw_count": True,
            },
            detail={
                "ko": "차량 구성은 분류된 교통량 조사에서 그대로 만들었습니다. relFlow는 백분율이 "
                "아니라 관측 대수 원값이며, Vissim이 실행 시 구성 내부에서 정규화하므로 반올림도 "
                "잔차 재배분도 없습니다.",
                "en": "The composition is built from the classified counts themselves. relFlow is "
                "the raw observed count, not a percentage — Vissim normalises across the "
                "composition at run time, so there is no rounding and no residual to redistribute.",
                "ja": "車両構成は分類済み交通量調査からそのまま作成しました。relFlow は百分率では"
                "なく観測台数の生値で、Vissim が実行時に構成内で正規化するため丸めも残差配分も"
                "ありません。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.5.4.1-hourly-volume-recalculation",
            section="§5.5.4.1",
            doc_page=206,
            state=ComplianceState.APPLIED,
            citation=CIT_S5541_HOURLY_VOLUMES,
            instances=len(inputs),
            subjects=[f"input {plan.no} (link {plan.link_id})" for plan in inputs],
            evidence={
                "bin_minutes": sorted(
                    {
                        round(interval.interval_minutes, 6)
                        for plan in inputs
                        for interval in plan.intervals
                    }
                ),
                "volumes_vph": sorted({round(plan.emitted_volume_vph, 6) for plan in inputs}),
                "formula": "volume_vph = counted * 60 / bin_minutes",
            },
            detail={
                "ko": "구간 대수를 시간당 대수로 환산했습니다(대수 × 60 / 구간 분). 원 집계값과 "
                "구간 길이를 각 구간에 함께 보관하므로 리포트가 산식을 그대로 보일 수 있습니다.",
                "en": "Counted vehicles per bin are recalculated to hourly volumes (counted × 60 "
                "/ bin minutes). The source figure and the bin length are kept beside every rate, "
                "so a report can show the arithmetic.",
                "ja": "区間台数を時間当たり台数に換算しました(台数 × 60 / 区間分)。原集計値と"
                "区間長を各区間に保持するため、レポートで計算式をそのまま示せます。",
            },
        )
    )

    exact = settings.volume_type == schema.VOL_TYPE_EXACT
    items.append(
        ComplianceItem(
            rule_id="5.5.4.1-exact-volumes",
            section="§5.5.4.1",
            doc_page=206,
            state=ComplianceState.APPLIED if exact else ComplianceState.VIOLATED,
            citation=CIT_S5541_EXACT_VOLUMES,
            instances=len(inputs),
            evidence={"volType": settings.volume_type},
            detail={
                "ko": "볼륨 유형은 "
                + settings.volume_type
                + "입니다. §5.5.4.1은 시드 번호에 따른 결과 차이를 줄이기 위해 exact를 권고합니다.",
                "en": "The volume type is "
                + settings.volume_type
                + ". §5.5.4.1 recommends exact volumes to minimise run-to-run discrepancies "
                "between seed numbers.",
                "ja": "ボリューム種別は "
                + settings.volume_type
                + " です。§5.5.4.1 はシード番号による差を減らすため exact を推奨します。",
            },
        )
    )

    profiled = [plan for plan in inputs if plan.profiled]
    items.append(
        ComplianceItem(
            rule_id="5.5.4.1-fifteen-minute-profiles",
            section="§5.5.4.1",
            doc_page=205,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S5541_FIFTEEN_MINUTE_INTERVALS,
            instances=len(profiled),
            subjects=[f"input {plan.no} (link {plan.link_id})" for plan in profiled],
            evidence={
                "profiled_inputs": len(profiled),
                "intervals_observed": sorted({len(plan.intervals) for plan in inputs}),
                "intervals_written": 1,
                "reduction": settings.single_interval_reduction,
            },
            detail={
                "ko": "15분 프로파일은 계획에는 전부 남아 있지만 파일에는 한 구간만 들어갑니다: "
                "다구간 시간구간 집합을 가진 Vissim 저장 파일이 이 저장소에 없어 그 구조를 "
                "실측하지 못했고, 실측되지 않은 구조는 쓰지 않습니다(emitter v1의 교훈).",
                "en": "The 15-minute profile is kept in full on the plan but only one interval "
                "reaches the file: no Vissim-saved network available to this repo has a "
                "multi-interval time-interval set, so that structure has never been measured, and "
                "unmeasured structure is not written (the emitter v1 lesson).",
                "ja": "15分プロファイルは計画には全て残りますが、ファイルには1区間のみ入ります: "
                "複数区間の時間区間集合を持つ Vissim 保存ファイルが本リポジトリに無く、構造を"
                "計測できていないためです。未計測の構造は書きません(emitter v1 の教訓)。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.5.4.2-routing-decision-position",
            section="§5.5.4.2",
            doc_page=207,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_REQUIRES_SITE_OBSERVATION,
            citation=CIT_S5542_VEHICLE_ROUTES,
            instances=len(decisions),
            subjects=[f"decision {seed.no} (link {seed.link_id})" for seed in decisions],
            evidence={"pos_m": settings.routing_decision_pos_m},
            detail={
                "ko": "경로 결정 표지는 접근 링크의 상류 끝(0 m)에 두었습니다. §5.5.4.2가 요구하는 "
                "'운전자가 차로 변경을 시작하는 지점'은 현장 관측값이며 생성 초안에는 없습니다.",
                "en": "The routing decision marker sits at the upstream end of the approach link. "
                "§5.5.4.2's 'point where motorists would likely begin to perform lane changes' is "
                "an on-site observation a generated draft does not have.",
                "ja": "経路決定マーカーは流入リンクの上流端に配置しました。§5.5.4.2 が求める"
                "「運転者が車線変更を始める地点」は現地観測値であり、生成草案にはありません。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.5.4.2-combine-static-routing-decisions",
            section="§5.5.4.2",
            doc_page=207,
            state=(
                ComplianceState.APPLIED
                if settings.combine_static_routing_decisions
                else ComplianceState.VIOLATED
            ),
            citation=CIT_S5542_COMBINE_DECISIONS,
            instances=len(decisions),
            evidence={
                "combineStaRoutDec": settings.combine_static_routing_decisions,
                "vissim_saved_default": False,
            },
            detail={
                "ko": "combineStaRoutDec 값입니다. Vissim이 저장한 참조 네트워크는 모두 false를 "
                "쓰지만 §5.5.4.2는 정적 배정 시 활성화를 권고하므로 지침을 따랐습니다.",
                "en": "The combineStaRoutDec value. Every Vissim-saved reference writes false, but "
                "§5.5.4.2 recommends activating it for static assignment, so the guideline wins.",
                "ja": "combineStaRoutDec の値です。Vissim 保存の参照網は全て false ですが、"
                "§5.5.4.2 は静的配分時の有効化を推奨するため指針に従いました。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.5.4.2-routes-per-vehicle-class",
            section="§5.5.4.2",
            doc_page=207,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S5542_VEHICLE_ROUTES,
            instances=len(decisions),
            evidence={"allVehTypes": True, "vehicle_classes_container_written": False},
            detail={
                "ko": "경로는 전 차종 공통(allVehTypes=true)으로 씁니다. 차종별 경로는 "
                "<vehicleClasses>를 가리키는 참조가 필요한데 이 파일은 그 컨테이너를 선언하지 "
                "않으며, 끊어진 참조는 Vissim이 조용히 컨테이너를 버리게 만듭니다.",
                "en": "Routes are written for all vehicle types (allVehTypes=true). Per-class "
                "routes need a reference into a <vehicleClasses> container this file does not "
                "declare, and a dangling reference makes Vissim drop the container silently.",
                "ja": "経路は全車種共通(allVehTypes=true)で書きます。車種別経路は "
                "<vehicleClasses> への参照が必要ですが、このファイルはそのコンテナを宣言せず、"
                "参照が切れると Vissim はコンテナを静かに破棄します。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.6.1.3-demand-input-check",
            section="§5.6.1.3",
            doc_page=211,
            state=ComplianceState.APPLIED,
            citation=CIT_S5613_DEMAND_INPUT_CHECK,
            instances=len(inputs) + len(decisions),
            evidence={
                "vehicle_inputs": len(inputs),
                "vehicle_routes": sum(len(seed.routes) for seed in decisions),
                "routing_decisions": len(decisions),
                "od_matrices": 0,
            },
            detail={
                "ko": "§5.6.1.3의 12개 입력 점검 항목 중 '교통 수요(차량 유입)'와 '차량 경로' "
                "두 항목을 이 초안이 씁니다. OD 매트릭스는 쓰지 않습니다(정적 배정).",
                "en": "Two of §5.6.1.3's twelve model-input items — traffic demands (vehicle "
                "inputs) and vehicle routes — are now written by this draft. No OD matrix is "
                "written (static assignment).",
                "ja": "§5.6.1.3 の12項目のうち「交通需要(車両流入)」と「車両経路」の2項目を"
                "この草案が書きます。OD 行列は書きません(静的配分)。",
            },
        )
    )

    warned = sorted({issue.code for issue in issues if issue.severity is IssueSeverity.WARN})
    if warned:
        items.append(
            ComplianceItem(
                rule_id="5.5-demand-generation-warnings",
                section="§5.5",
                doc_page=204,
                state=ComplianceState.VIOLATED,
                citation=CIT_S5541_VEHICLE_INPUTS,
                instances=len(warned),
                subjects=warned,
                evidence=coverage.counts(),
                detail={
                    "ko": "수요 생성 중 발생한 경고를 여기서도 명시합니다 — 준수 리포트만 읽는 "
                    "사람도 놓치지 않도록.",
                    "en": "Warnings raised while generating the demand are named here too, so a "
                    "reader of the compliance report alone still sees them.",
                    "ja": "需要生成中に発生した警告をここでも明示します — 準拠レポートのみを"
                    "読む人にも伝わるように。",
                },
            )
        )
    return items


# ---------------------------------------------------------------------------
# XML element construction — used by ``emit.build_tree``
# ---------------------------------------------------------------------------


def _element(parent: ET.Element, tag: str, attrs: dict) -> ET.Element:
    """One ``SubElement`` with attributes in a FIXED (sorted) order — the same
    determinism rule, and the same reason, as ``emit._element``."""
    return ET.SubElement(parent, tag, {key: attrs[key] for key in sorted(attrs)})


def _num(value: float) -> str:
    """Positions in metres, 3 decimals — identical to ``emit._num`` so a route's
    ``destPos`` is formatted exactly like the link geometry it sits on."""
    text = f"{float(value):.3f}"
    return "0.000" if text == "-0.000" else text


def _amount(value: float) -> str:
    """A volume or a relative flow, as Vissim writes them.

    LHT and NOD write ``volume="485"`` and ``relFlow="0.5"`` — an integer when
    the number is one, a plain decimal otherwise, never scientific notation.
    Three decimals is the same budget every other number in this emission gets.
    """
    number = float(value)
    if number.is_integer():
        return str(int(number))
    text = f"{number:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def time_interval_sets_element() -> ET.Element:
    """``<timeIntervalSets>`` — all ten sets, one interval each, in Vissim order.

    Written in full rather than restricted to the two sets this emission keys
    on, because ``timeInt="1 0"`` and ``relFlow="2 0:…"`` reference a set by its
    POSITION in this list. See :data:`schema.TIME_INTERVAL_SET_ORDER`.
    """
    container = ET.Element("timeIntervalSets")
    for name in schema.TIME_INTERVAL_SET_ORDER:
        element = _element(container, "timeIntervalSet", {"no": name})
        intervals = ET.SubElement(element, "timeInts")
        _element(intervals, "timeInterval", {"start": schema.TIME_INTERVAL_START})
    return container


def vehicle_composition_element(parent: ET.Element, plan: VehicleCompositionPlan) -> None:
    element = _element(
        parent,
        "vehicleComposition",
        {**schema.VEHICLE_COMPOSITION_ATTRS, "no": str(plan.no), "name": plan.name},
    )
    flows = ET.SubElement(element, "vehCompRelFlows")
    for flow in sorted(plan.flows, key=lambda item: (item.veh_type_no, item.class_code)):
        _element(
            flows,
            "vehicleCompositionRelativeFlow",
            {
                **schema.VEHICLE_COMPOSITION_REL_FLOW_ATTRS,
                "vehType": str(flow.veh_type_no),
                "desSpeedDistr": str(flow.des_speed_distr_no),
                "relFlow": _amount(flow.rel_flow),
            },
        )


def vehicle_input_element(
    parent: ET.Element, plan: VehicleInputPlan, settings: DemandSettings
) -> None:
    element = _element(
        parent,
        "vehicleInput",
        {**schema.VEHICLE_INPUT_ATTRS, "no": str(plan.no), "link": str(plan.link_id)},
    )
    volumes = ET.SubElement(element, "timeIntVehVols")
    _element(
        volumes,
        "timeIntervalVehVolume",
        {
            **schema.TIME_INTERVAL_VEH_VOLUME_ATTRS,
            "timeInt": schema.TIME_INT_VEHICLE_INPUT,
            "vehComp": str(plan.composition_no),
            "volType": settings.volume_type,
            "volume": _amount(plan.emitted_volume_vph),
        },
    )


def routing_decision_element(
    parent: ET.Element, seed: RoutingDecisionSeed, settings: DemandSettings
) -> None:
    element = _element(
        parent,
        "vehicleRoutingDecisionStatic",
        {
            **schema.VEHICLE_ROUTING_DECISION_STATIC_ATTRS,
            "allVehTypes": schema.ALL_VEH_TYPES_TRUE,
            "combineStaRoutDec": (
                "true" if settings.combine_static_routing_decisions else "false"
            ),
            "no": str(seed.no),
            "link": str(seed.link_id),
            "pos": _num(seed.pos_m),
        },
    )
    routes = ET.SubElement(element, "vehRoutSta")
    for route in sorted(seed.routes, key=lambda item: item.no):
        route_element = _element(
            routes,
            "vehicleRouteStatic",
            {
                **schema.VEHICLE_ROUTE_STATIC_ATTRS,
                "no": str(route.no),
                "destLink": str(route.dest_link_id),
                "destPos": _num(route.dest_pos_m),
                "relFlow": (
                    f"{schema.REL_FLOW_KEY_VEHICLE_ROUTE_STATIC}{_amount(route.rel_flow)}"
                ),
            },
        )
        sequence = ET.SubElement(route_element, "linkSeq")
        # The connector is the whole path: a genesis route crosses exactly one
        # intersection, from the decision's approach link to the exit link named
        # by ``destLink``. Measured shape — the reference networks' linkSeq
        # entries are link and connector ids in travel order.
        _element(sequence, "intObjectRef", {"key": str(route.connector_id)})


def demand_containers(demand: DraftDemand | None) -> dict[str, ET.Element]:
    """The four demand containers, or ``{}`` when there is nothing to write.

    ``{}`` is the whole additivity guarantee: ``emit.build_tree`` merges this
    into its container map, so a demand-less draft — ``None``,
    :data:`NO_DEMAND`, or a demand that refused — contributes no key and the
    emitted bytes are exactly what they were before this module existed.
    """
    if demand is None or not demand.emitting:
        return {}

    containers: dict[str, ET.Element] = {"timeIntervalSets": time_interval_sets_element()}

    if demand.compositions:
        compositions = ET.Element("vehicleCompositions")
        for plan in sorted(demand.compositions, key=lambda item: item.no):
            vehicle_composition_element(compositions, plan)
        containers["vehicleCompositions"] = compositions

    if demand.inputs:
        inputs = ET.Element("vehicleInputs")
        for plan in sorted(demand.inputs, key=lambda item: item.no):
            vehicle_input_element(inputs, plan, demand.settings)
        containers["vehicleInputs"] = inputs

    if demand.decisions:
        decisions = ET.Element("vehicleRoutingDecisionsStatic")
        for seed in sorted(demand.decisions, key=lambda item: item.no):
            routing_decision_element(decisions, seed, demand.settings)
        containers["vehicleRoutingDecisionsStatic"] = decisions

    return containers


def demand_provenance_note(demand: DraftDemand | None) -> str:
    """The sentence the emitter adds to its provenance comment for a
    demand-carrying draft. Empty for one without, so the comment — and the
    file — is unchanged."""
    if demand is None or not demand.emitting:
        return ""
    veh_types = sorted({flow.veh_type_no for plan in demand.compositions for flow in plan.flows})
    routes = sum(len(seed.routes) for seed in demand.decisions)
    profiled = sum(1 for plan in demand.inputs if plan.profiled)
    profile_note = (
        f"{profiled} input(s) were counted as a demand profile and written as ONE interval "
        f"({demand.settings.single_interval_reduction}); a multi-interval time interval set is "
        "not measured on any Vissim-saved network available to this repo, so the structure that "
        "would carry a profile is not written. "
        if profiled
        else ""
    )
    return (
        f"Demand seeded from observed counts: {len(demand.inputs)} vehicle input(s), "
        f"{len(demand.compositions)} composition(s), {len(demand.decisions)} static routing "
        f"decision(s) with {routes} route(s), volType {demand.settings.volume_type}. "
        f"{profile_note}"
        f"THE COMPOSITIONS REFERENCE VEHICLE TYPES {veh_types} AND DESIRED-SPEED DISTRIBUTIONS "
        f"{sorted({flow.des_speed_distr_no for plan in demand.compositions for flow in plan.flows})} "
        "THAT THIS FILE DOES NOT DECLARE - open it only against a network that does, because an "
        "unresolved reference makes Vissim drop the container and still report a successful load. "
    )


__all__ = [
    "CIT_S522_TIME_PERIODS",
    "CIT_S523_VEHICLE_TYPES",
    "CIT_S551_VEHICLE_COMPOSITION",
    "CIT_S5541_EXACT_VOLUMES",
    "CIT_S5541_FIFTEEN_MINUTE_INTERVALS",
    "CIT_S5541_HOURLY_VOLUMES",
    "CIT_S5541_VEHICLE_INPUTS",
    "CIT_S5542_COMBINE_DECISIONS",
    "CIT_S5542_VEHICLE_ROUTES",
    "CIT_S5613_DEMAND_INPUT_CHECK",
    "DEFAULT_SETTINGS",
    "EXTRACT",
    "NO_DEMAND",
    "REDUCTION_MEAN",
    "REDUCTION_PEAK",
    "REFUSE_CODES",
    "SHARE_SUM_TOLERANCE",
    "ApproachLinkSiteCheck",
    "CompositionFlow",
    "DemandCoverage",
    "DemandInterval",
    "DemandSettings",
    "DraftDemand",
    "RouteSeed",
    "RoutingDecisionSeed",
    "VehicleCompositionPlan",
    "VehicleInputPlan",
    "VehicleTypeRef",
    "check_approach_link_sites",
    "demand_containers",
    "demand_provenance_note",
    "entry_links",
    "generate_demand",
    "link_exits",
    "route_share",
    "time_interval_sets_element",
]

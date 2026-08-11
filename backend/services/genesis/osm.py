"""Read a LOCAL ``.osm`` XML file into the drivable network it describes.

Roadmap row 2.3, Genesis (a), first stage: OSM in, a typed, provenance-carrying
description of ways/nodes/intersections out. ``draft.py`` turns that into links
and connectors; ``emit.py`` writes the ``.inpx``.

**Local file only.** Nothing here fetches anything — no Overpass, no HTTP, no
network of any kind. The input is a path to a ``.osm`` file the caller already
has. Parsing is ``xml.etree.ElementTree.iterparse`` in two streaming passes
(ways first, then only the nodes those ways actually reference), so a
metropolitan extract does not have to be resident in memory as a DOM. stdlib
only: no osmnx, no shapely, no pyproj.

Three refusals to invent nothing
--------------------------------
1. **Nothing is silently dropped.** Every way that is not carried into the
   network is recorded in :attr:`OsmExtract.skipped` with its id, its
   ``highway`` value (or the absence of one) and a machine-readable reason
   code. "The generator ignored 4 000 footways" is a fact the consumer gets to
   read, not a silence.
2. **Nothing is silently invented.** Every lane count carries a
   :class:`LaneProvenance` naming the OSM key it came from and its raw value.
   Where no tag exists, the highway-class default is used and the provenance is
   marked ``assumed=True`` — the generator has an opinion, and it says so on
   every single link rather than once in a footnote.
3. **Nothing is silently mirrored.** See below.

Left-hand traffic — MRWA §5.2.1.1
---------------------------------
> "Left-hand traffic should be adopted for Traffic regulations."
> — MRWA OMG v2.0 §5.2.1.1, doc p.180 (:data:`CIT_S5211_LEFT_HAND`)

An OSM way is a **centreline**. A two-way way therefore becomes *two*
directional links, one per direction of travel, and each has to be placed on
the correct side of that centreline. In left-hand traffic a driver keeps left,
so a direction's carriageway sits to the **left of its own direction of
travel** — which is :func:`left_normal`, the +90° (counter-clockwise) normal in
this module's +x-east/+y-north frame.

Worked, and hand-checkable: for a way running due **east**, ``(dx, dy) =
(1, 0)`` and ``left_normal = (0, +1)`` — north. Facing east, your left hand
points north; eastbound traffic is on the **north** side. The reverse direction
runs west, ``(−1, 0)``, whose left normal is ``(0, −1)`` — south. Under
right-hand traffic every one of those signs flips, and the resulting network
looks perfectly plausible while being a mirror image of the truth. That is the
classic silent-mirror bug, it is invisible in a rendering, and it is asserted
explicitly by sign in ``test_genesis_osm_draft.py`` §2 rather than trusted.

The geometric side-placement itself lives in :mod:`services.genesis.draft`,
because it needs the lane width (and the lane width has its own §5 citation).
This module owns the *directional split*: which directions of travel exist, and
how many lanes each one has.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

EXTRACT = "docs/product/reference/mrwa-section5-extract.md"

# ---------------------------------------------------------------------------
# Citations
#
# Same discipline as ``services/dataimport/fixed_time.py``: every CIT_* names
# the section AND the doc page inside the extract above, and quotes it
# verbatim, so a reviewer re-checks a rule against the source in one hop and a
# mechanical test can prove the quote is really there. Citations name TABLES
# and SECTIONS, never the guideline's in-text figure labels — the extract
# records nine measured cross-reference errors where the body text says
# "Figure N" and means "Table N".
# ---------------------------------------------------------------------------

CIT_S5211_LEFT_HAND = (
    f"MRWA OMG v2.0 §5.2.1.1 Vehicle Behaviour, doc p.180 ({EXTRACT}) — "
    "'Left-hand traffic should be adopted for Traffic regulations.'"
)

#: Quoted short of the sentence's "(as shown in Figure 5-2)" clause on purpose:
#: this project cites sections, tables and doc pages, never the guideline's
#: in-text figure labels, because nine of them are measurably wrong.
CIT_S5212_UNITS = (
    f"MRWA OMG v2.0 §5.2.1.2 Units, doc p.181 ({EXTRACT}) — "
    "'the metric system should be adopted'"
)


# ---------------------------------------------------------------------------
# The drivable allowlist
#
# An allowlist, not a denylist: an OSM tag vocabulary nobody controls will grow
# new values, and a denylist would quietly admit whatever it had not heard of.
# Anything not named here is skipped BY NAME (OsmExtract.skipped).
#
# The integer is this generator's default lanes PER DIRECTION for the class,
# used only when the way carries no usable lane tag. MRWA §5 states no
# highway-class lane defaults — §5 assumes a network is coded from survey and
# aerial imagery, not from OSM — so these are the GENERATOR's assumptions and
# every lane count derived from one is flagged ``assumed=True``.
# ---------------------------------------------------------------------------

#: **The `service` policy, stated.** ``highway=service`` — car-park aisles,
#: driveways, back lanes — is IN the allowlist below, and that decision now has
#: a consequence attached rather than being an accident of the table: a service
#: way is a carriageway like any other. It is split, offset, given connectors,
#: node-merged and given a cul-de-sac U-turn on exactly the same terms as a
#: residential street. The reasoning is that this generator has only three
#: honest options — model them, exclude them by name, or leave them in the file
#: as carriageways a vehicle can enter and never leave — and the third is the
#: one state that is indefensible, because a network that strands vehicles
#: reports confident wrong numbers rather than failing.
#:
#: A modeller who wants the strategic network without car-park circulation asks
#: for it: ``parse_osm_file(path, exclude_service=True)`` (CLI:
#: ``--exclude-service``) drops every service way as a NAMED skip
#: (:data:`SKIP_SERVICE_EXCLUDED_BY_POLICY`), so the exclusion is visible in the
#: compliance report and in ``skipped_summary()``. It is never the default,
#: because deleting real carriageways silently is the mirror image of inventing
#: them: MRWA §5.3.3's "minimise the number of links and connectors" is a coding
#: instruction, not a licence to delete roads the survey did not ask about.
SERVICE_HIGHWAY = "service"

DRIVABLE_HIGHWAY_DEFAULT_LANES: dict[str, int] = {
    "motorway": 2,
    "motorway_link": 1,
    "trunk": 2,
    "trunk_link": 1,
    "primary": 2,
    "primary_link": 1,
    "secondary": 1,
    "secondary_link": 1,
    "tertiary": 1,
    "tertiary_link": 1,
    "unclassified": 1,
    "residential": 1,
    "living_street": 1,
    "service": 1,
    "road": 1,
}

#: Reason codes for :class:`SkippedWay`. Stable strings; tests assert on them.
SKIP_NO_HIGHWAY_TAG = "no_highway_tag"
SKIP_HIGHWAY_NOT_DRIVABLE = "highway_not_drivable"
SKIP_AREA = "area_not_a_carriageway"
SKIP_TOO_FEW_NODES = "fewer_than_two_resolvable_nodes"
SKIP_ZERO_LENGTH = "zero_length_after_deduplication"
#: Only ever produced by an explicit ``exclude_service=True`` — see
#: :data:`SERVICE_HIGHWAY`. A policy exclusion is a named skip, never a silence.
SKIP_SERVICE_EXCLUDED_BY_POLICY = "service_excluded_by_policy"
#: A way that survived every test above and still ended up in a pocket of the
#: emitted network **nothing can enter and nothing can leave**. Produced by
#: :func:`services.genesis.draft.build_draft`'s isolation pass, never by
#: parsing, and always accompanied by an ``excluded_zones`` entry naming WHY the
#: pocket is unreachable. The detail string of each skip carries that reason, so
#: the exclusion is legible from the skip list alone.
SKIP_ISOLATED_ZONE = "isolated_zone_unreachable_in_the_source"

#: Reason codes for a ``type=restriction`` relation this module could not read.
#: Recorded rather than ignored: an unread restriction is a movement that stays
#: legal in the model although the map bans it, which is precisely the silent
#: over-permission this package exists to surface.
RESTRICTION_VIA_NOT_A_NODE = "restriction_via_is_not_a_node"
RESTRICTION_MEMBERS_INCOMPLETE = "restriction_members_incomplete"
RESTRICTION_KIND_UNKNOWN = "restriction_kind_not_understood"
RESTRICTION_WAY_NOT_KEPT = "restriction_names_a_way_this_network_does_not_contain"

#: The ``restriction=*`` values this generator enforces. ``no_*`` bans the named
#: movement; ``only_*`` bans every movement from that approach except the named
#: one. Both only ever REMOVE connectivity, which is why they are safe to apply
#: without a survey: the worst case of a misread restriction is a model that
#: forbids a legal turn, and that shows up as a routing failure rather than as a
#: plausible wrong number.
RESTRICTION_KINDS = frozenset(
    {
        "no_left_turn",
        "no_right_turn",
        "no_straight_on",
        "no_u_turn",
        "no_entry",
        "no_exit",
        "only_left_turn",
        "only_right_turn",
        "only_straight_on",
        "only_u_turn",
    }
)

#: Reason codes for :class:`OsmRefusal`.
REFUSE_FILE_MISSING = "osm_file_missing"
REFUSE_UNPARSEABLE = "osm_not_well_formed"
REFUSE_UNREADABLE = "osm_unreadable"
REFUSE_NO_DRIVABLE_WAYS = "osm_no_drivable_ways"

#: Provenance sources for a lane count.
LANE_SOURCE_TAG = "osm_tag"
LANE_SOURCE_TAG_SPLIT = "osm_tag_split_between_directions"
LANE_SOURCE_TURN_LANES = "osm_turn_lanes_tag"
LANE_SOURCE_CLASS_DEFAULT = "highway_class_default"

#: Values of ``oneway`` OSM treats as true / reversed.
_ONEWAY_TRUE = {"yes", "true", "1"}
_ONEWAY_REVERSED = {"-1", "reverse"}
_ONEWAY_FALSE = {"no", "false", "0"}

#: Tag keys whose presence declares a bus/PSV lane on the way. Consumed by
#: ``draft.py``'s §5.3.3.4 rule, which cannot code lane closures in this
#: emission subset and therefore reports the way by name.
BUS_LANE_KEYS = (
    "busway",
    "busway:left",
    "busway:right",
    "busway:both",
    "lanes:bus",
    "lanes:bus:forward",
    "lanes:bus:backward",
    "lanes:psv",
    "bus:lanes",
    "psv:lanes",
)

#: A ``turn:lanes`` entry equal to one of these is a lane dedicated to a single
#: movement. §5.3.3.3 recommends separate link coding for exactly that case.
DEDICATED_TURN_VALUES = {"left", "right", "merge_to_left", "merge_to_right", "reverse"}


def left_normal(dx: float, dy: float) -> tuple[float, float]:
    """Unit normal 90° counter-clockwise from ``(dx, dy)``.

    In this package's +x-east / +y-north frame that is a traveller's LEFT.
    Under MRWA §5.2.1.1 (:data:`CIT_S5211_LEFT_HAND`) that is also the side of
    the centreline their carriageway occupies. Due east ``(1, 0)`` → ``(0, 1)``
    = north; see the module docstring's worked check.

    A zero-length direction returns ``(0.0, 0.0)`` rather than raising: a
    degenerate segment is a data problem the caller reports, not a crash.
    """
    norm = math.hypot(dx, dy)
    if norm == 0.0:
        return 0.0, 0.0
    return -dy / norm, dx / norm


@dataclass(frozen=True)
class LaneProvenance:
    """Where one directional lane count came from.

    ``assumed`` is the whole point: ``True`` means no tag in the file said this
    and the generator supplied it. A consumer can filter a whole draft down to
    "what did you make up?" on this one boolean.
    """

    source: str
    osm_key: str
    raw_value: str
    assumed: bool
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "osm_key": self.osm_key,
            "raw_value": self.raw_value,
            "assumed": self.assumed,
            "note": self.note,
        }


@dataclass(frozen=True)
class WayDirection:
    """One direction of travel of one way: the thing that becomes one link."""

    forward: bool
    lanes: int
    provenance: LaneProvenance
    turn_lanes: tuple[str, ...] = ()

    @property
    def dedicated_turn_lanes(self) -> tuple[str, ...]:
        return tuple(v for v in self.turn_lanes if v in DEDICATED_TURN_VALUES)


@dataclass(frozen=True)
class OsmNode:
    id: int
    lat: float
    lon: float


@dataclass(frozen=True)
class OsmWay:
    """A kept, drivable way and everything read off it."""

    id: int
    name: str
    highway: str
    node_ids: tuple[int, ...]
    oneway: int  # +1 forward-only, -1 backward-only, 0 two-way
    oneway_provenance: LaneProvenance
    directions: tuple[WayDirection, ...]
    bus_lane_keys: tuple[str, ...] = ()
    maxspeed: str = ""
    #: Vertical position, verbatim from the file. Read for ONE purpose: the
    #: node merge in :mod:`services.genesis.connectivity` refuses to join two
    #: nodes whose ways disagree about it, because a freeway passes within
    #: centimetres of the road under it *in plan* and welding them together
    #: would connect a network to the one crossing it.
    layer: str = ""
    bridge: str = ""
    tunnel: str = ""
    #: ``noexit=yes`` — the mapper's own statement that the way dead-ends.
    #: Corroboration only: the cul-de-sac detector reads TOPOLOGY, because most
    #: real cul-de-sacs carry no such tag and a tag cannot create a junction.
    noexit: str = ""

    @property
    def is_oneway(self) -> bool:
        return self.oneway != 0

    @property
    def declares_bus_lane(self) -> bool:
        return bool(self.bus_lane_keys)

    @property
    def dedicated_turn_lanes(self) -> tuple[str, ...]:
        found: list[str] = []
        for direction in self.directions:
            found.extend(direction.dedicated_turn_lanes)
        return tuple(found)


@dataclass(frozen=True)
class SkippedWay:
    """A way the generator did not carry into the network — named, with why."""

    id: int
    reason: str
    highway: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "way_id": self.id,
            "reason": self.reason,
            "highway": self.highway,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class OsmRefusal:
    """The file could not become a network. Reported, never raised."""

    code: str
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class TurnRestriction:
    """One ``type=restriction`` relation, reduced to the movement it bans.

    Only the simple ``from`` way / ``via`` NODE / ``to`` way form is carried.
    The ``via``-way form (a restriction over a whole link, e.g. across a
    roundabout) describes a movement this generator's connector model has no
    single object for, so it is REFUSED by name rather than approximated —
    approximating it would either ban a legal turn or leave a banned one legal,
    and neither is visible in a rendering.
    """

    id: int
    kind: str
    from_way: int
    via_node: int
    to_way: int

    def as_dict(self) -> dict:
        return {
            "relation_id": self.id,
            "kind": self.kind,
            "from_way": self.from_way,
            "via_node": self.via_node,
            "to_way": self.to_way,
        }


@dataclass(frozen=True)
class RestrictionRefusal:
    """A restriction relation this module did not enforce, named with why."""

    id: int
    reason: str
    detail: str

    def as_dict(self) -> dict:
        return {"relation_id": self.id, "reason": self.reason, "detail": self.detail}


@dataclass
class OsmExtract:
    """Everything read out of one ``.osm`` file."""

    source: str
    ways: list[OsmWay] = field(default_factory=list)
    nodes: dict[int, OsmNode] = field(default_factory=dict)
    skipped: list[SkippedWay] = field(default_factory=list)
    refusals: list[OsmRefusal] = field(default_factory=list)
    ways_seen: int = 0
    #: ``(min_lat, min_lon, max_lat, max_lon)`` from the file's own ``<bounds>``
    #: element, or ``None`` when it has none. This is the DOWNLOAD WINDOW, which
    #: is a different fact from :meth:`bounds` (the extent of the geometry that
    #: came back) and is used for a different question: a way terminal outside
    #: the window is a road leaving the study area, not a cul-de-sac. Overpass
    #: returns whole ways, so such terminals are normal and common.
    declared_bounds: tuple[float, float, float, float] | None = None
    #: ``type=restriction`` relations reduced to bannable movements.
    restrictions: list[TurnRestriction] = field(default_factory=list)
    #: The ones that could not be, named. Never empty-by-silence.
    restriction_refusals: list[RestrictionRefusal] = field(default_factory=list)
    #: Nodes referenced by ways this generator does NOT model (footway, path,
    #: cycleway, …). A way terminal that is one of these is a dead end for a
    #: motor vehicle even though the file shows the corridor continuing — the
    #: distinction is reported separately by ``connectivity.dead_ends`` so a
    #: reviewer can disagree with it.
    non_drivable_node_ids: set[int] = field(default_factory=set)
    #: The same nodes, carrying WHICH classes touch them. ``non_drivable_node_ids``
    #: answers "does the corridor continue?"; this answers "as what?", and the
    #: difference is the whole content of an exclusion report: *"this pocket's
    #: only link to the network is a footway"* is a line a client can act on,
    #: *"this pocket is isolated"* is not. Values are the raw ``highway=*``
    #: strings, with ``""`` for a way that carries no ``highway`` tag at all.
    non_drivable_node_highways: dict[int, set[str]] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def bounds(self) -> tuple[float, float, float, float] | None:
        """(min_lat, min_lon, max_lat, max_lon) over the nodes of KEPT ways.

        Derived from the data rather than from the file's ``<bounds>`` element:
        an extract's declared bounds routinely describe the download window
        rather than the geometry actually inside it, and the projection origin
        has to sit at the centre of what is really there.
        """
        if not self.nodes:
            return None
        lats = [n.lat for n in self.nodes.values()]
        lons = [n.lon for n in self.nodes.values()]
        return min(lats), min(lons), max(lats), max(lons)

    def intersection_node_ids(self) -> set[int]:
        """Nodes shared by two or more kept ways — the junctions.

        A node touched by only one way is a shape vertex (or, at a way's end, a
        network boundary), not an intersection. A node a single way visits twice
        (a closed loop rejoining itself) IS one, which is why total OCCURRENCES
        are counted rather than distinct ways: two visits from one way and one
        visit from each of two ways are both junctions.
        """
        occurrences: dict[int, int] = {}
        for way in self.ways:
            for node_id in way.node_ids:
                occurrences[node_id] = occurrences.get(node_id, 0) + 1
        return {node_id for node_id, count in occurrences.items() if count >= 2}

    def skipped_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for skip in self.skipped:
            counts[skip.reason] = counts.get(skip.reason, 0) + 1
        return dict(sorted(counts.items()))

    def assumed_lane_count(self) -> int:
        return sum(
            1
            for way in self.ways
            for direction in way.directions
            if direction.provenance.assumed
        )

    def non_drivable_classes_at(self, node_ids: Iterable[int]) -> tuple[str, ...]:
        """The ``highway=*`` classes of NOT-modelled ways touching these nodes.

        Sorted and de-duplicated so a caller can put it straight into a report
        without re-deriving an order; ``""`` (a way with no ``highway`` tag) is
        rendered as ``no highway tag`` because an empty string in a reason
        string reads as a bug.
        """
        found: set[str] = set()
        for node_id in node_ids:
            found |= self.non_drivable_node_highways.get(node_id, set())
        return tuple(sorted(value or "no highway tag" for value in found))

    def without_ways(
        self, way_ids: Iterable[int], *, reason: str, detail_for: Callable[[int], str]
    ) -> "OsmExtract":
        """A copy of this extract with ``way_ids`` removed as NAMED skips.

        The mirror image of ``exclude_service``: a way this generator decides not
        to model leaves a :class:`SkippedWay` behind carrying the reason, so the
        exclusion appears in ``skipped_summary()`` and in the compliance report
        rather than as an absence a reader cannot notice.

        Three things are pruned with the ways, and each matters:

        * **nodes** no surviving way references — otherwise the extract's
          :meth:`bounds` (and therefore the projection origin, and therefore
          every coordinate in the emitted file) would still be sized by geometry
          that is no longer in the network;
        * **restrictions** naming a removed way, through the same path a clipped
          extract takes, so they are refused by name rather than silently
          retained against a way that no longer exists;
        * nothing else — ``non_drivable_node_ids`` and its class map describe
          ways this generator never modelled and are unaffected by which
          drivable ways survive.

        Never mutates ``self``: the caller keeps the original to diagnose
        against.
        """
        removing = set(way_ids)
        kept = [way for way in self.ways if way.id not in removing]
        skipped = list(self.skipped)
        for way in sorted(
            (way for way in self.ways if way.id in removing), key=lambda w: w.id
        ):
            skipped.append(SkippedWay(way.id, reason, way.highway, detail_for(way.id)))
        referenced = {node_id for way in kept for node_id in way.node_ids}
        reduced = OsmExtract(
            source=self.source,
            ways=kept,
            nodes={nid: node for nid, node in self.nodes.items() if nid in referenced},
            skipped=skipped,
            refusals=list(self.refusals),
            ways_seen=self.ways_seen,
            declared_bounds=self.declared_bounds,
            restrictions=list(self.restrictions),
            restriction_refusals=list(self.restriction_refusals),
            non_drivable_node_ids=set(self.non_drivable_node_ids),
            non_drivable_node_highways={
                nid: set(values) for nid, values in self.non_drivable_node_highways.items()
            },
        )
        _drop_restrictions_naming_absent_ways(reduced)
        return reduced


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------


def _tags(elem: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in elem.findall("tag"):
        key = tag.get("k")
        if key:
            out[key] = tag.get("v", "")
    return out


def _positive_int(raw: str | None) -> int | None:
    """A lane count OSM would accept: a bare positive integer.

    ``lanes=1.5`` (a real thing in OSM, meaning a single-track road) and
    ``lanes=2;3`` are deliberately NOT coerced — a value this module cannot read
    unambiguously falls through to the flagged class default, which is visible,
    rather than to a guess, which is not.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


def _turn_lane_values(raw: str | None) -> tuple[str, ...]:
    """``turn:lanes`` is ``|``-separated, one entry per lane, entries possibly
    empty (``left||through``) and possibly multi-valued (``through;left``).

    The entry COUNT is the lane count — that is the fact this module uses it
    for — and each entry is kept verbatim so §5.3.3.3's dedicated-movement rule
    can look for a lane whose only value is a turn.
    """
    if raw is None:
        return ()
    text = raw.strip()
    if not text:
        return ()
    return tuple(part.strip() for part in text.split("|"))


def _oneway_of(tags: dict[str, str], highway: str) -> tuple[int, LaneProvenance]:
    """(+1 forward / −1 backward / 0 two-way) and where that came from.

    Explicit ``oneway`` always wins. Absent it, two OSM conventions imply
    one-way — a roundabout ``junction`` and a ``motorway``/``motorway_link``
    class — and both are applied but flagged ``assumed=True``: they are
    community convention, not a statement in this file.
    """
    raw = tags.get("oneway")
    if raw is not None:
        value = raw.strip().lower()
        if value in _ONEWAY_TRUE:
            return 1, LaneProvenance(LANE_SOURCE_TAG, "oneway", raw, False)
        if value in _ONEWAY_REVERSED:
            return -1, LaneProvenance(
                LANE_SOURCE_TAG, "oneway", raw, False, "-1: way digitised against travel"
            )
        if value in _ONEWAY_FALSE:
            return 0, LaneProvenance(LANE_SOURCE_TAG, "oneway", raw, False)
        # An unreadable value ("reversible", "alternating") is not guessed at.
        return 0, LaneProvenance(
            LANE_SOURCE_CLASS_DEFAULT,
            "oneway",
            raw,
            True,
            "oneway value not understood; treated as two-way",
        )

    junction = tags.get("junction", "").strip().lower()
    if junction in {"roundabout", "circular"}:
        return 1, LaneProvenance(
            LANE_SOURCE_CLASS_DEFAULT,
            "junction",
            junction,
            True,
            "OSM convention: a roundabout/circular junction is one-way",
        )
    if highway in {"motorway", "motorway_link"}:
        return 1, LaneProvenance(
            LANE_SOURCE_CLASS_DEFAULT,
            "highway",
            highway,
            True,
            "OSM convention: motorway carriageways are one-way",
        )
    return 0, LaneProvenance(
        LANE_SOURCE_CLASS_DEFAULT, "oneway", "", True, "no oneway tag; treated as two-way"
    )


def _directional_lanes(
    tags: dict[str, str], highway: str, oneway: int
) -> tuple[WayDirection, ...]:
    """The directions this way carries, and the lane count + provenance of each.

    Precedence, most specific first:

    1. ``lanes:forward`` / ``lanes:backward`` — per-direction, unambiguous.
    2. ``turn:lanes:forward`` / ``turn:lanes:backward`` — the entry count is the
       lane count of that direction.
    3. ``lanes`` — the TOTAL across both directions on a two-way way (this is
       OSM's definition and getting it wrong doubles a network's capacity). On a
       one-way way it is that direction's count outright.
       * even total on a two-way → half each, ``assumed=False``: the split is
         arithmetic, not invention.
       * odd total on a two-way → ``ceil`` forward, ``floor`` backward, both
         flagged ``assumed=True``: OSM does not say which direction has the
         extra lane, so the generator's choice is a guess and says so.
    4. ``turn:lanes`` on a one-way way — the entry count.
    5. the highway-class default — always ``assumed=True``.
    """
    directions: list[WayDirection] = []
    turn_fwd = _turn_lane_values(tags.get("turn:lanes:forward"))
    turn_bwd = _turn_lane_values(tags.get("turn:lanes:backward"))
    turn_plain = _turn_lane_values(tags.get("turn:lanes"))
    class_default = DRIVABLE_HIGHWAY_DEFAULT_LANES.get(highway, 1)

    def resolve(suffix: str, turn_specific: tuple[str, ...]) -> tuple[int, LaneProvenance, tuple[str, ...]]:
        key = f"lanes:{suffix}"
        explicit = _positive_int(tags.get(key))
        if explicit is not None:
            return explicit, LaneProvenance(LANE_SOURCE_TAG, key, tags[key], False), turn_specific
        if turn_specific:
            return (
                len(turn_specific),
                LaneProvenance(
                    LANE_SOURCE_TURN_LANES,
                    f"turn:lanes:{suffix}",
                    tags[f"turn:lanes:{suffix}"],
                    False,
                    f"{len(turn_specific)} '|'-separated entries = {len(turn_specific)} lane(s)",
                ),
                turn_specific,
            )
        return -1, LaneProvenance(LANE_SOURCE_CLASS_DEFAULT, "highway", highway, True), turn_specific

    if oneway != 0:
        forward = oneway > 0
        suffix = "forward" if forward else "backward"
        turn_specific = turn_fwd if forward else turn_bwd
        lanes, prov, turns = resolve(suffix, turn_specific)
        if lanes < 0:
            total = _positive_int(tags.get("lanes"))
            if total is not None:
                lanes = total
                prov = LaneProvenance(
                    LANE_SOURCE_TAG, "lanes", tags["lanes"], False, "one-way: all lanes in the one direction"
                )
            elif turn_plain:
                lanes = len(turn_plain)
                turns = turn_plain
                prov = LaneProvenance(
                    LANE_SOURCE_TURN_LANES,
                    "turn:lanes",
                    tags["turn:lanes"],
                    False,
                    f"{len(turn_plain)} '|'-separated entries = {len(turn_plain)} lane(s)",
                )
            else:
                lanes = class_default
                prov = LaneProvenance(
                    LANE_SOURCE_CLASS_DEFAULT,
                    "highway",
                    highway,
                    True,
                    f"no lane tag; generator default for highway={highway}",
                )
        if not turns and turn_plain:
            # On a ONE-WAY way an unsuffixed ``turn:lanes`` describes the one
            # direction there is, whatever the lane COUNT came from. The count
            # and the turn values are two different reads of the same tag, and
            # only the count branches above: before this, a way tagged
            # ``lanes=4`` + ``turn:lanes=through|through|right|right`` reached
            # ``build_draft`` with its lane count from ``lanes`` and its turn
            # values thrown away, so the file's own statement of which lane goes
            # where was unavailable to the connector builder. On a TWO-WAY way
            # the unsuffixed tag is ambiguous (OSM does not say which direction
            # it describes) and is deliberately NOT used below.
            turns = turn_plain
        directions.append(WayDirection(forward=forward, lanes=lanes, provenance=prov, turn_lanes=turns))
        return tuple(directions)

    # Two-way.
    fwd_lanes, fwd_prov, fwd_turns = resolve("forward", turn_fwd)
    bwd_lanes, bwd_prov, bwd_turns = resolve("backward", turn_bwd)
    if fwd_lanes < 0 or bwd_lanes < 0:
        total = _positive_int(tags.get("lanes"))
        if total is not None:
            if total % 2 == 0:
                split_fwd = split_bwd = total // 2
                assumed = False
                note = f"lanes={total} is the two-way total; even split {split_fwd}/{split_bwd}"
            else:
                split_fwd = total // 2 + 1
                split_bwd = max(total // 2, 1)
                assumed = True
                note = (
                    f"lanes={total} is an ODD two-way total; OSM does not say which "
                    f"direction carries the extra lane — generator assigned "
                    f"{split_fwd} forward / {split_bwd} backward"
                )
            if fwd_lanes < 0:
                fwd_lanes = split_fwd
                fwd_prov = LaneProvenance(LANE_SOURCE_TAG_SPLIT, "lanes", tags["lanes"], assumed, note)
            if bwd_lanes < 0:
                bwd_lanes = split_bwd
                bwd_prov = LaneProvenance(LANE_SOURCE_TAG_SPLIT, "lanes", tags["lanes"], assumed, note)
        else:
            note = f"no lane tag; generator default for highway={highway}"
            if fwd_lanes < 0:
                fwd_lanes = class_default
                fwd_prov = LaneProvenance(LANE_SOURCE_CLASS_DEFAULT, "highway", highway, True, note)
            if bwd_lanes < 0:
                bwd_lanes = class_default
                bwd_prov = LaneProvenance(LANE_SOURCE_CLASS_DEFAULT, "highway", highway, True, note)

    directions.append(WayDirection(True, fwd_lanes, fwd_prov, fwd_turns))
    directions.append(WayDirection(False, bwd_lanes, bwd_prov, bwd_turns))
    return tuple(directions)


def _dedupe_consecutive(node_ids: list[int]) -> tuple[int, ...]:
    out: list[int] = []
    for node_id in node_ids:
        if not out or out[-1] != node_id:
            out.append(node_id)
    return tuple(out)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_osm_file(path: str | Path, *, exclude_service: bool = False) -> OsmExtract:
    """Read one local ``.osm`` file. Reports, never raises.

    Pass 1 streams ``<way>`` elements (classifying, lane-reading, and recording
    every skip by name), plus the ``<bounds>`` element and the
    ``type=restriction`` relations; pass 2 streams ``<node>`` elements and keeps
    only the ones a kept way references. Both passes clear each element as they
    go, so peak memory is the kept geometry rather than the whole document.

    ``exclude_service`` applies the opt-in half of the service-road policy
    documented at :data:`SERVICE_HIGHWAY`: every ``highway=service`` way becomes
    a named skip (:data:`SKIP_SERVICE_EXCLUDED_BY_POLICY`) instead of a
    carriageway. Default ``False`` — service ways are modelled and connected.
    """
    file_path = Path(path)
    extract = OsmExtract(source=str(file_path))

    if not file_path.exists() or file_path.is_dir():
        extract.refusals.append(
            OsmRefusal(
                REFUSE_FILE_MISSING,
                f"no such .osm file — {file_path}",
            )
        )
        return extract

    try:
        needed_nodes = _pass1_ways(file_path, extract, exclude_service=exclude_service)
    except ET.ParseError as exc:
        extract.refusals.append(
            OsmRefusal(REFUSE_UNPARSEABLE, f"{file_path.name}: not well-formed XML — {exc}")
        )
        return extract
    except OSError as exc:
        extract.refusals.append(
            OsmRefusal(REFUSE_UNREADABLE, f"{file_path.name}: could not be read — {exc}")
        )
        return extract

    if not extract.ways:
        extract.refusals.append(
            OsmRefusal(
                REFUSE_NO_DRIVABLE_WAYS,
                f"{file_path.name}: {extract.ways_seen} way(s) read, none drivable. "
                f"Skips by reason: {extract.skipped_summary() or '{}'}",
            )
        )
        return extract

    try:
        _pass2_nodes(file_path, extract, needed_nodes)
    except (ET.ParseError, OSError) as exc:  # pragma: no cover - pass 1 already read it
        extract.refusals.append(
            OsmRefusal(REFUSE_UNPARSEABLE, f"{file_path.name}: node pass failed — {exc}")
        )
        return extract

    _drop_ways_with_unresolvable_nodes(extract)
    _drop_restrictions_naming_absent_ways(extract)
    if not extract.ways:
        extract.refusals.append(
            OsmRefusal(
                REFUSE_NO_DRIVABLE_WAYS,
                f"{file_path.name}: every drivable way lost its geometry — "
                f"skips by reason: {extract.skipped_summary()}",
            )
        )
    return extract


def _pass1_ways(
    file_path: Path, extract: OsmExtract, *, exclude_service: bool = False
) -> set[int]:
    needed: set[int] = set()
    for _event, elem in ET.iterparse(str(file_path), events=("end",)):
        if elem.tag == "bounds":
            extract.declared_bounds = _declared_bounds(elem)
            elem.clear()
            continue
        if elem.tag == "relation":
            _read_restriction(elem, extract)
            elem.clear()
            continue
        if elem.tag != "way":
            if elem.tag == "node":
                elem.clear()
            continue
        extract.ways_seen += 1
        way_id = _int_attr(elem, "id")
        tags = _tags(elem)
        node_ids = _dedupe_consecutive(
            [nid for nid in (_int_attr(nd, "ref") for nd in elem.findall("nd")) if nid is not None]
        )
        elem.clear()

        if way_id is None:
            continue
        highway = tags.get("highway", "").strip()
        if not highway:
            _record_non_drivable(extract, node_ids, "")
            extract.skipped.append(
                SkippedWay(way_id, SKIP_NO_HIGHWAY_TAG, "", "way carries no highway=* tag")
            )
            continue
        if highway not in DRIVABLE_HIGHWAY_DEFAULT_LANES:
            _record_non_drivable(extract, node_ids, highway)
            extract.skipped.append(
                SkippedWay(
                    way_id,
                    SKIP_HIGHWAY_NOT_DRIVABLE,
                    highway,
                    f"highway={highway} is not in the drivable allowlist "
                    f"({len(DRIVABLE_HIGHWAY_DEFAULT_LANES)} values)",
                )
            )
            continue
        if exclude_service and highway == SERVICE_HIGHWAY:
            extract.skipped.append(
                SkippedWay(
                    way_id,
                    SKIP_SERVICE_EXCLUDED_BY_POLICY,
                    highway,
                    "highway=service excluded by an explicit exclude_service=True; the "
                    "default policy is to model and connect service ways",
                )
            )
            continue
        if tags.get("area", "").strip().lower() in {"yes", "true", "1"}:
            extract.skipped.append(
                SkippedWay(way_id, SKIP_AREA, highway, "area=yes — a surface, not a carriageway")
            )
            continue
        if len(node_ids) < 2:
            extract.skipped.append(
                SkippedWay(
                    way_id,
                    SKIP_TOO_FEW_NODES,
                    highway,
                    f"{len(node_ids)} distinct consecutive node reference(s) after deduplication",
                )
            )
            continue

        oneway, oneway_prov = _oneway_of(tags, highway)
        extract.ways.append(
            OsmWay(
                id=way_id,
                name=tags.get("name", ""),
                highway=highway,
                node_ids=node_ids,
                oneway=oneway,
                oneway_provenance=oneway_prov,
                directions=_directional_lanes(tags, highway, oneway),
                bus_lane_keys=tuple(k for k in BUS_LANE_KEYS if k in tags),
                maxspeed=tags.get("maxspeed", ""),
                layer=tags.get("layer", "").strip(),
                bridge=tags.get("bridge", "").strip(),
                tunnel=tags.get("tunnel", "").strip(),
                noexit=tags.get("noexit", "").strip(),
            )
        )
        needed.update(node_ids)
    return needed


def _record_non_drivable(
    extract: OsmExtract, node_ids: Iterable[int], highway: str
) -> None:
    """Remember both that a not-modelled way touches these nodes, and as what."""
    for node_id in node_ids:
        extract.non_drivable_node_ids.add(node_id)
        extract.non_drivable_node_highways.setdefault(node_id, set()).add(highway)


def _declared_bounds(elem: ET.Element) -> tuple[float, float, float, float] | None:
    values = [
        _float_attr(elem, name) for name in ("minlat", "minlon", "maxlat", "maxlon")
    ]
    if any(value is None for value in values):
        return None
    min_lat, min_lon, max_lat, max_lon = values  # type: ignore[misc]
    if min_lat > max_lat or min_lon > max_lon:
        return None
    return min_lat, min_lon, max_lat, max_lon


def _read_restriction(elem: ET.Element, extract: OsmExtract) -> None:
    """One ``<relation>`` → a :class:`TurnRestriction`, or a named refusal.

    Reads only ``type=restriction``; every other relation type is not this
    module's business and is passed over without comment. A restriction that IS
    one and cannot be reduced to (from way, via node, to way) is recorded in
    :attr:`OsmExtract.restriction_refusals` — never dropped — because a
    restriction that quietly fails to apply leaves a banned movement legal in
    the model, and Vissim will happily route traffic through it.
    """
    tags = _tags(elem)
    if tags.get("type", "").strip() != "restriction":
        return
    relation_id = _int_attr(elem, "id")
    if relation_id is None:
        return
    kind = (tags.get("restriction") or "").strip().lower()
    members = {"from": [], "via": [], "to": []}  # type: dict[str, list[tuple[str, int]]]
    for member in elem.findall("member"):
        role = (member.get("role") or "").strip()
        if role not in members:
            continue
        ref = _int_attr(member, "ref")
        if ref is None:
            continue
        members[role].append(((member.get("type") or "").strip(), ref))

    if kind not in RESTRICTION_KINDS:
        extract.restriction_refusals.append(
            RestrictionRefusal(
                relation_id,
                RESTRICTION_KIND_UNKNOWN,
                f"restriction={tags.get('restriction', '')!r} is not one of the "
                f"{len(RESTRICTION_KINDS)} values this generator enforces",
            )
        )
        return
    if len(members["from"]) != 1 or len(members["to"]) != 1 or len(members["via"]) != 1:
        extract.restriction_refusals.append(
            RestrictionRefusal(
                relation_id,
                RESTRICTION_MEMBERS_INCOMPLETE,
                "expected exactly one from / one via / one to member, got "
                f"{len(members['from'])}/{len(members['via'])}/{len(members['to'])}",
            )
        )
        return
    via_type, via_ref = members["via"][0]
    if via_type != "node":
        extract.restriction_refusals.append(
            RestrictionRefusal(
                relation_id,
                RESTRICTION_VIA_NOT_A_NODE,
                f"via member is a {via_type or 'nameless object'}; a via-way restriction "
                "spans several connectors and is not approximated here",
            )
        )
        return
    extract.restrictions.append(
        TurnRestriction(
            id=relation_id,
            kind=kind,
            from_way=members["from"][0][1],
            via_node=via_ref,
            to_way=members["to"][0][1],
        )
    )


def _pass2_nodes(file_path: Path, extract: OsmExtract, needed: set[int]) -> None:
    for _event, elem in ET.iterparse(str(file_path), events=("end",)):
        if elem.tag != "node":
            if elem.tag in {"way", "relation"}:
                elem.clear()
            continue
        node_id = _int_attr(elem, "id")
        lat = _float_attr(elem, "lat")
        lon = _float_attr(elem, "lon")
        elem.clear()
        if node_id is None or node_id not in needed:
            continue
        if lat is None or lon is None:
            continue
        extract.nodes[node_id] = OsmNode(node_id, lat, lon)


def _drop_ways_with_unresolvable_nodes(extract: OsmExtract) -> None:
    """A way whose nodes are not in the file is geometry we do not have.

    Kept as a NAMED skip rather than a shortened polyline: quietly dropping the
    missing vertices of a clipped extract shortens links by an unknown amount,
    and a shortened approach is exactly the kind of wrong number §5.3.2.1's
    queue-containment rule turns into a wrong answer.
    """
    kept: list[OsmWay] = []
    for way in extract.ways:
        resolvable = tuple(nid for nid in way.node_ids if nid in extract.nodes)
        if len(resolvable) < 2:
            extract.skipped.append(
                SkippedWay(
                    way.id,
                    SKIP_TOO_FEW_NODES,
                    way.highway,
                    f"{len(resolvable)} of {len(way.node_ids)} node(s) present in this file",
                )
            )
            continue
        if len(resolvable) != len(way.node_ids):
            extract.skipped.append(
                SkippedWay(
                    way.id,
                    SKIP_TOO_FEW_NODES,
                    way.highway,
                    f"way clipped: {len(way.node_ids) - len(resolvable)} referenced node(s) "
                    f"absent from this file — way dropped rather than shortened",
                )
            )
            continue
        kept.append(way)
    extract.ways = kept

    referenced = {nid for way in kept for nid in way.node_ids}
    extract.nodes = {nid: node for nid, node in extract.nodes.items() if nid in referenced}


def _drop_restrictions_naming_absent_ways(extract: OsmExtract) -> None:
    """A restriction over a way this network does not contain cannot be applied.

    That is not automatically an error — a restriction at the edge of an extract
    routinely names a way outside it — but it IS a movement ban the model does
    not carry, so it is moved into the refusal list by name rather than being
    silently dropped from the enforced set.
    """
    kept = {way.id for way in extract.ways}
    applicable: list[TurnRestriction] = []
    for restriction in extract.restrictions:
        missing = [
            way_id
            for way_id in (restriction.from_way, restriction.to_way)
            if way_id not in kept
        ]
        if missing:
            extract.restriction_refusals.append(
                RestrictionRefusal(
                    restriction.id,
                    RESTRICTION_WAY_NOT_KEPT,
                    f"restriction={restriction.kind} names way(s) {missing}, which this "
                    "network does not contain (outside the extract, or not drivable)",
                )
            )
            continue
        applicable.append(restriction)
    extract.restrictions = applicable


def _int_attr(elem: ET.Element, name: str) -> int | None:
    raw = elem.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float_attr(elem: ET.Element, name: str) -> float | None:
    raw = elem.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None

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
from dataclasses import dataclass, field, replace
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
#: Every node of the way — and every segment between them — lies outside the
#: bbox that was ASKED for. Overpass returns whole ways, so a query for a
#: 450 m square routinely brings back a way whose far end is a kilometre away
#: and whose near end never enters the square at all. Produced only by
#: :func:`trim_ways_to_requested_bbox`, i.e. only when the caller supplied a
#: :attr:`OsmExtract.requested_bbox`.
SKIP_OUTSIDE_REQUESTED_BBOX = "entirely_outside_the_requested_bbox"

#: Reason codes for a ``type=restriction`` relation this module could not read.
#: Recorded rather than ignored: an unread restriction is a movement that stays
#: legal in the model although the map bans it, which is precisely the silent
#: over-permission this package exists to surface.
RESTRICTION_VIA_NOT_A_NODE = "restriction_via_is_not_a_node"
RESTRICTION_MEMBERS_INCOMPLETE = "restriction_members_incomplete"
RESTRICTION_KIND_UNKNOWN = "restriction_kind_not_understood"
RESTRICTION_WAY_NOT_KEPT = "restriction_names_a_way_this_network_does_not_contain"
#: The ``via`` node of the relation was on a tail this generator cut off at the
#: requested bbox, so the junction the ban applies at is no longer in the
#: network. Refused BY NAME for the same reason as every other unapplied
#: restriction: a ban the model silently forgets is a movement that stays legal
#: in the model although the map forbids it.
RESTRICTION_VIA_NODE_TRIMMED = "restriction_via_node_trimmed_at_the_requested_bbox"

#: No node of the way lies inside the requested bbox, but a segment of it clips
#: the rectangle between two mapped vertices — Overpass's bbox filter returns a
#: way that merely INTERSECTS the box, so a road grazing a corner comes back
#: whole. Dropped, and counted apart from :data:`SKIP_OUTSIDE_REQUESTED_BBOX`
#: because the two are different facts: this one really does touch the study
#: area. See :func:`trim_ways_to_requested_bbox` for why it is dropped anyway.
SKIP_ONLY_GRAZES_REQUESTED_BBOX = "grazes_the_requested_bbox_with_no_node_inside_it"

#: Reason codes for :class:`WayTrim`.
#: A leading or trailing run of nodes outside the requested region was cut off and
#: the way now terminates ON its edge.
#:
#: The four codes on this page and the three ``SKIP_*_REQUESTED_BBOX`` codes above
#: keep the word "bbox" for a LASSO build too, and that is deliberate: they are the
#: stable vocabulary the census, the compliance report and the gate's own counts
#: are keyed by, so renaming them per shape would split every historical count in
#: two. Which shape a given build cut against is in the DETAIL string of each
#: record ("the requested polygon" / "the requested bbox") and in the boundary
#: label, both of which a reader sees beside the code.
TRIM_TAIL_CUT = "tail_outside_requested_bbox_cut"
#: The way leaves the requested bbox and comes back. Cutting it would either
#: delete a middle section that is genuinely inside the study area or split one
#: OSM way into two, and both are edits this v1 will not make silently — so the
#: way is kept WHOLE and named here.
TRIM_KEPT_WHOLE_REENTRANT = "leaves_and_re_enters_the_requested_bbox_kept_whole"

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


@dataclass(frozen=True)
class WayTrim:
    """One decision :func:`trim_ways_to_requested_bbox` took about one way.

    Emitted for the ways that were CUT and for the ways that were deliberately
    left whole — never only for the cuts. A geometry edit a reader cannot find
    is the same to them as a silent deletion, and "this way overhangs the study
    area by 1.4 km and was left whole" is a line a reviewer can act on, whereas
    the way's silent absence from the trim list is not.

    A way cut at both ends produces TWO records, one per end, because the two
    ends are two independent facts about two different roads leaving the study
    area. Ways dropped entirely leave a :class:`SkippedWay`
    (:data:`SKIP_OUTSIDE_REQUESTED_BBOX`) instead, so they reach the compliance
    report through the machinery every other skip already uses.
    """

    way_id: int
    highway: str
    reason: str
    #: ``"start"`` / ``"end"`` for a cut, ``"whole"`` for a way left intact.
    end: str
    nodes_cut: int
    #: How far past the new terminal this way used to reach: the straight-line
    #: distance from the terminal to the FARTHEST node that was cut, projected on
    #: the plane of the untrimmed extract. A report figure, not an input to any
    #: geometry — "this road ran 236 m outside the study area" is the sentence a
    #: reviewer needs, and it is deliberately not a polyline length, which would
    #: read as a claim about road length rather than about overhang.
    metres_cut: float
    #: The node synthesized on the bbox edge, or ``0`` where none was.
    synthesized_node_id: int
    detail: str

    def as_dict(self) -> dict:
        return {
            "way_id": self.way_id,
            "highway": self.highway,
            "reason": self.reason,
            "end": self.end,
            "nodes_cut": self.nodes_cut,
            "metres_cut": round(self.metres_cut, 3),
            "synthesized_node_id": self.synthesized_node_id,
            "detail": self.detail,
        }


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
    #: ``(min_lat, min_lon, max_lat, max_lon)`` — the window the CALLER ASKED
    #: FOR, which is a third fact again, different from both
    #: :attr:`declared_bounds` (what the file says it is) and :meth:`bounds`
    #: (the extent of what came back). Overpass answers a bbox query with WHOLE
    #: ways, so the geometry routinely overhangs the request by hundreds of
    #: metres — measured on this project's own Daejeon fetches: 100 m to 2300 m.
    #: The hull of that geometry is therefore NOT the study area, and using it
    #: as the model boundary put every legitimate boundary cut hundreds of
    #: metres inside the rectangle, where the 1 m band could not see it and the
    #: cut read as an interior dead end. Provenance-derived (the ``<!--
    #: prism-osm-fetch -->`` comment names ``requested_region`` as
    #: south/west/north/east; the mapping to this tuple's order is made
    #: explicitly by :func:`requested_bbox_from_provenance`) and ``None`` for
    #: any file that was not fetched by this app — an uploaded ``.osm`` has no
    #: request to remember, and the ladder falls back exactly as before.
    requested_bbox: tuple[float, float, float, float] | None = None
    #: The LASSO, as ``((lat, lon), …)``, when the region was drawn as a polygon
    #: rather than as a rectangle — and ``None`` for every rectangle fetch and
    #: every uploaded file. Set alongside :attr:`requested_bbox`, never instead of
    #: it: for a polygon fetch that bbox is the polygon's OWN bounding box
    #: (``osm_fetch.RegionPolygon.bounding_box``).
    #:
    #: **This field MOVES GEOMETRY.** When it is present,
    #: :func:`trim_ways_to_requested_bbox` cuts every overhanging tail at the RING
    #: rather than at the bounding box, so a way running into a concave notch is
    #: cut where it leaves the shape the user drew instead of dead-ending inside
    #: the notch as an interior finding. Losing it between transforms therefore
    #: does not merely downgrade a label any more — it silently re-trims the
    #: network against a bigger shape, which is why
    #: ``test_every_extract_reconstruction_carries_every_field_the_dataclass_declares``
    #: covers all three reconstruction sites.
    #:
    #: What it does NOT move: the boundary BAND. ``connectivity._boundary_rectangle``
    #: is still the bounding box (label
    #: :data:`~services.genesis.connectivity.BOUNDARY_SOURCE_REQUESTED_POLYGON_BBOX`),
    #: because the metre-distance test is rectangle arithmetic. That is a residual
    #: with no effect on cut terminals — they are classified by membership in
    #: :attr:`synthesized_node_ids`, not by distance — and it is stated in the
    #: compliance report rather than left to be inferred.
    requested_polygon: tuple[tuple[float, float], ...] | None = None
    #: Every decision :func:`trim_ways_to_requested_bbox` took, cuts and
    #: deliberate non-cuts alike. Empty whenever :attr:`requested_bbox` is None.
    way_trims: list[WayTrim] = field(default_factory=list)
    #: The nodes THIS GENERATOR put on the requested rectangle when it cut a
    #: tail. They are truncation MARKERS — "the road left the study area here" —
    #: and not places the source says anything meets, which is a distinction
    #: with teeth: ``connectivity.merge_junction_nodes`` refuses to weld one into
    #: a nearby real junction (:data:`
    #: ~services.genesis.connectivity.REFUSE_SYNTHESIZED_BOUNDARY_NODE`).
    #: Measured on a declared T-junction 6 m inside the edge with an unrelated
    #: way cut 6.3 m from it: the merge averaged the two positions, moved the
    #: boundary node **4.501 m off the rectangle** — against a 1.0 m band — so
    #: the terminal stopped being reported as a boundary exit at all, and it
    #: fabricated a turning movement between two roads the source never joins.
    synthesized_node_ids: frozenset[int] = field(default_factory=frozenset)

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
            # Carried, not re-derived: the request the caller made does not
            # change because this generator removed a pocket, and a rebuild that
            # forgot it would silently fall back to the hull of the SHRUNKEN
            # geometry — a different boundary on every isolation round.
            requested_bbox=self.requested_bbox,
            requested_polygon=self.requested_polygon,
            way_trims=list(self.way_trims),
            # Not filtered to the surviving nodes: a node this generator
            # synthesized stays a synthesized node for as long as it is
            # mentioned anywhere, and the merge's refusal must not depend on
            # which isolation round is asking.
            synthesized_node_ids=self.synthesized_node_ids,
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


def parse_osm_file(
    path: str | Path,
    *,
    exclude_service: bool = False,
    requested_bbox: tuple[float, float, float, float] | None = None,
    requested_polygon: tuple[tuple[float, float], ...] | None = None,
) -> OsmExtract:
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

    ``requested_bbox`` is ``(min_lat, min_lon, max_lat, max_lon)`` — the window
    the caller ASKED Overpass for. It is only RECORDED here
    (:attr:`OsmExtract.requested_bbox`); nothing about parsing changes, and the
    geometry is not touched. :func:`trim_ways_to_requested_bbox`, called once at
    :func:`services.genesis.draft.build_draft` entry, is what acts on it. That
    split is deliberate: the parse stays a faithful reading of the file, and the
    one geometric edit derived from the request happens at one seam every caller
    (jobs, tools, tests) goes through. ``None`` — an uploaded file, a fixture —
    leaves every downstream behaviour byte-identical to what it was before this
    parameter existed.

    ``requested_polygon`` is the LASSO the region was drawn as, when it was drawn
    as one. It is likewise only RECORDED here — but unlike the note above it is
    NOT inert downstream: :func:`trim_ways_to_requested_bbox` cuts against the
    RING when this is set, and against ``requested_bbox`` when it is not. Passing
    the ring without its bounding box, or the box without the ring, therefore
    describes two different study areas; the fetch writes both keys and
    :func:`requested_bbox_from_provenance` / :func:`requested_polygon_from_provenance`
    read them as the pair they are.
    """
    file_path = Path(path)
    extract = OsmExtract(
        source=str(file_path),
        requested_bbox=requested_bbox,
        requested_polygon=requested_polygon,
    )

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


# ---------------------------------------------------------------------------
# Trimming a whole-way extract back to the window that was asked for
#
# Overpass answers a bbox query with WHOLE ways: ask for a 450 m square and a
# way whose far end is 2.3 km away comes back entire (both numbers measured on
# this project's own Daejeon fetches). That is correct of Overpass and fatal for
# a boundary rule read off the geometry, because the hull of the RECEIVED
# geometry is then set by the longest tail and every legitimate boundary cut
# sits hundreds of metres inside it — far outside the 1 m
# ``connectivity.BOUNDARY_BAND_M`` — so the cut reads as an interior dead end,
# gets a reflecting-wall U-turn or an unfixable-one-way refusal, and the gate
# refuses the network for a fault that is an artifact of the download.
#
# The fix is to make the geometry match the request rather than to widen the
# band: cut each overhanging tail AT the requested region and put a node on it.
# A terminal ON the rectangle is 0.00 m from the boundary, so it lands inside the
# band by construction rather than by tuning.
#
# THE REQUESTED REGION IS THE SHAPE THAT WAS DRAWN, not its bounding box. For a
# rectangle fetch those are the same thing. For a LASSO they are not, and the
# difference was measured on a real L-shaped lasso: a way running into the L's
# notch is inside the box and outside the ring, so a bbox trim kept it whole and
# it dead-ended mid-notch — hundreds of metres from any edge, therefore an
# INTERIOR finding the gate must treat as a real fault, for road the user never
# asked for. So the inside test, the crossing solver and the graze test are each
# chosen by shape (``_inside_polygon`` / ``_polygon_crossing_point`` /
# ``_ring_crossing_parameters`` versus their rectangle siblings) and every rule
# below is written once for both. The user's decided policy, in one line: trim at
# the requested region, ignore the overhang.
#
# The metre BAND is the named residual — ``connectivity._boundary_rectangle`` is
# still a rectangle, and so are the beyond-the-box tripwire and the isolated-zone
# cause. It costs nothing on cut terminals (they classify by membership in
# ``synthesized_node_ids``, before any distance is read) and it is disclosed
# rather than hidden.
#
# Why this is an extract→extract transform in WGS84 and not a link edit: the
# projection origin is derived from the extract's own extent
# (``project.origin_for_bounds`` over ``OsmExtract.bounds()``), so removing
# geometry MOVES the origin and therefore every coordinate in the emitted file.
# The trim has to happen before anything is projected, or the numbers in the
# report are not the numbers in the file.
#
# What it will NOT do: split one OSM way into two. A way that leaves the window
# and comes back is kept WHOLE and named (:data:`TRIM_KEPT_WHOLE_REENTRANT`),
# because the alternatives are deleting a middle section that is genuinely
# inside the study area or inventing a second way id, and a v1 that silently
# does either is the failure this package exists to prevent.
# ---------------------------------------------------------------------------


def requested_bbox_from_provenance(
    provenance: dict | None,
) -> tuple[float, float, float, float] | None:
    """``{"south","west","north","east"}`` → ``(min_lat, min_lon, max_lat, max_lon)``.

    The two orderings are written out side by side ON PURPOSE. The fetch
    provenance names its window with compass words and this module's bbox tuples
    are ``(lat, lon, lat, lon)``; a mapping that lived only in a caller would be
    one transposition away from a study area rotated onto its side, and nothing
    downstream could tell.

    Returns ``None`` for anything that is not a complete, finite, correctly
    ordered region — an unannotated upload, a truncated comment, a
    hand-edited file. A caller with no window falls back to the behaviour it had
    before there was one.

    **Unchanged by polygon fetches, deliberately.** A lasso's provenance writes
    ``requested_region`` as the ring's OWN bounding box (``osm_fetch``), so this
    function keeps answering one question — "which RECTANGLE does this build
    have?" — and that rectangle is still what the boundary band, the
    beyond-the-box tripwire and the isolated-zone cause measure against. It is no
    longer what a lasso is TRIMMED against: the ring travels beside it, is read by
    :func:`requested_polygon_from_provenance`, and takes precedence in
    :func:`trim_ways_to_requested_bbox`.
    """
    if not isinstance(provenance, dict):
        return None
    region = provenance.get("requested_region")
    if not isinstance(region, dict):
        return None
    try:
        south = float(region["south"])
        west = float(region["west"])
        north = float(region["north"])
        east = float(region["east"])
    except (KeyError, TypeError, ValueError):
        return None
    values = (south, west, north, east)
    if not all(math.isfinite(v) for v in values):
        return None
    if south >= north or west >= east:
        return None
    return south, west, north, east


def requested_polygon_from_provenance(
    provenance: dict | None,
) -> tuple[tuple[float, float], ...] | None:
    """``{"requested_polygon": {"vertices": [[lat, lon], …]}}`` → a vertex tuple.

    The sibling of :func:`requested_bbox_from_provenance`, and deliberately a
    SEPARATE function rather than a second return value: the two answer different
    questions ("which rectangle is the band measured against?" and "which shape
    was drawn?") and a caller that has only one of them should be unable to
    pretend it has both.

    **Both now move geometry.** When this returns a ring, the trim cuts tails at
    THAT ring rather than at the bounding box beside it, so a reader that skipped
    this function would trim a lasso against a rectangle nobody drew — which is
    exactly the defect the polygon-native trim removed. It was label-only for one
    unit (`d5076f0`), and that sentence is deleted rather than softened.

    ``None`` for anything that is not at least three finite in-range vertices —
    a rectangle fetch (which writes no such key at all), an unannotated upload, a
    truncated comment. The ring is NOT re-validated for self-intersection here:
    ``osm_fetch.RegionPolygon`` refused that before the fetch happened, and a
    reader of a stored document is recovering what was asked for, not re-deciding
    whether to allow it.
    """
    if not isinstance(provenance, dict):
        return None
    payload = provenance.get("requested_polygon")
    if isinstance(payload, dict):
        payload = payload.get("vertices")
    if not isinstance(payload, (list, tuple)):
        return None
    vertices: list[tuple[float, float]] = []
    for pair in payload:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        try:
            lat = float(pair[0])
            lon = float(pair[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(lat) or not math.isfinite(lon):
            return None
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            return None
        vertices.append((lat, lon))
    if len(vertices) < 3:
        return None
    return tuple(vertices)


def _inside_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _clip_parameters(
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
    bbox: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    """Liang–Barsky in (lat, lon): the ``[t0, t1]`` of A→B inside ``bbox``.

    Straight in lat/lon is straight in projected metres — ``LocalTangentPlane``
    is affine and separable (x depends only on lon, y only on lat) — and the
    bbox edges are axis-aligned in both frames, so an intersection solved here
    is exactly the intersection the projected frame sees. That is what lets this
    run before the plane exists.

    ``None`` when the segment misses the rectangle entirely.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    t0, t1 = 0.0, 1.0
    for delta, low, high, start in (
        (lat_b - lat_a, min_lat, max_lat, lat_a),
        (lon_b - lon_a, min_lon, max_lon, lon_a),
    ):
        if delta == 0.0:
            if start < low or start > high:
                return None
            continue
        enter = (low - start) / delta
        leave = (high - start) / delta
        if enter > leave:
            enter, leave = leave, enter
        t0 = max(t0, enter)
        t1 = min(t1, leave)
        if t0 > t1:
            return None
    return t0, t1


def _crossing_point(
    inside: OsmNode, outside: OsmNode, bbox: tuple[float, float, float, float]
) -> tuple[float, float] | None:
    """Where the inside→outside segment leaves ``bbox``, ON the rectangle.

    Solved per axis and the SMALLEST crossing parameter taken, which is the edge
    nearest the inside node. That is the corner case stated explicitly: a
    segment leaving diagonally past a corner crosses two edge LINES within one
    segment, at two different points, and only the nearer one is on the
    rectangle. Snapping to the far one would put the terminal outside the study
    area, where the boundary band would not see it — the exact failure this
    whole trim exists to remove.

    The coordinate of the BINDING axis is then written EXACTLY to its edge value
    rather than left as the interpolation's residual, so the projected distance
    to that edge is exactly 0.0 and the band decision cannot turn on the last
    bit of a float. The free coordinate is clamped into range.

    ``None`` when the crossing coincides with the inside node — i.e. that node
    is already ON the edge — because synthesizing a second node at the same
    place would add a zero-length piece of polyline to say something the
    existing node already says.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    d_lat = outside.lat - inside.lat
    d_lon = outside.lon - inside.lon

    best_t: float | None = None
    best_axis = ""
    best_edge = 0.0
    for delta, start, low, high, axis in (
        (d_lat, inside.lat, min_lat, max_lat, "lat"),
        (d_lon, inside.lon, min_lon, max_lon, "lon"),
    ):
        if delta == 0.0:
            continue
        edge = high if delta > 0.0 else low
        t = (edge - start) / delta
        if t < 0.0 or t > 1.0:
            continue
        if best_t is None or t < best_t:
            best_t, best_axis, best_edge = t, axis, edge

    if best_t is None:  # pragma: no cover - the outside node IS outside
        return None
    if best_t <= 0.0:
        return None

    lat = inside.lat + best_t * d_lat
    lon = inside.lon + best_t * d_lon
    if best_axis == "lat":
        lat = best_edge
        lon = min(max(lon, min_lon), max_lon)
    else:
        lon = best_edge
        lat = min(max(lat, min_lat), max_lat)
    if (lat, lon) == (inside.lat, inside.lon):
        return None
    return lat, lon


#: How close to a ring edge counts as ON it, in DEGREES of perpendicular
#: distance. 1e-9° is ~0.1 mm at any latitude this generator will see — a
#: FLOAT-NOISE tolerance, not a geometric band. It exists because a slanted ring
#: edge cannot be written bit-exactly the way an axis-aligned bbox edge can (see
#: :func:`_polygon_crossing_point`), so "is this node on the ring?" has to be
#: asked with a tolerance or it would answer "no" for points the arithmetic put
#: there itself. Deliberately NOT ``project.MAX_PLANIMETRIC_ERROR_M``: that 1.0 m
#: is the boundary BAND, a question about how close a surveyed node has to be to
#: the edge to be read as leaving the model, and answering a float question with
#: a survey tolerance would silently widen the study area by a metre.
ON_RING_EPS_DEG = 1e-9


def _ring_edges(
    polygon: tuple[tuple[float, float], ...],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The closed ring's edges as ``((lat, lon), (lat, lon))`` pairs.

    The ring is stored OPEN (the first vertex is not repeated — that is the
    shape ``osm_fetch.RegionPolygon`` validated and the shape the provenance
    records), so the closing edge from the last vertex back to the first is
    added here rather than being assumed present.
    """
    return [(polygon[i], polygon[(i + 1) % len(polygon)]) for i in range(len(polygon))]


def _on_ring(lat: float, lon: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Is this point ON the ring, within :data:`ON_RING_EPS_DEG`?

    Perpendicular distance to the edge SEGMENT (not to its infinite line), so a
    point beyond an edge's end is only "on the ring" if it is on the neighbouring
    edge — which the caller checks, because every edge is tested.
    """
    for (a_lat, a_lon), (b_lat, b_lon) in _ring_edges(polygon):
        d_lat = b_lat - a_lat
        d_lon = b_lon - a_lon
        length = math.hypot(d_lat, d_lon)
        if length == 0.0:  # pragma: no cover - a duplicate vertex; harmless here
            if math.hypot(lat - a_lat, lon - a_lon) <= ON_RING_EPS_DEG:
                return True
            continue
        # Distance from the point to the segment, in degrees, via the projection
        # parameter clamped to [0, 1].
        t = ((lat - a_lat) * d_lat + (lon - a_lon) * d_lon) / (length * length)
        t = min(max(t, 0.0), 1.0)
        if math.hypot(lat - (a_lat + t * d_lat), lon - (a_lon + t * d_lon)) <= ON_RING_EPS_DEG:
            return True
    return False


def _inside_polygon(
    lat: float, lon: float, polygon: tuple[tuple[float, float], ...]
) -> bool:
    """Point-in-polygon by ray casting, BOUNDARY-INCLUSIVE.

    The polygon analogue of :func:`_inside_bbox`, and inclusive for the same
    reason that one is written with ``<=`` and ``>=``: a node lying exactly on
    the requested edge is a node the request contains, and the trim synthesizes
    its cut terminals ON the edge — so an exclusive test would classify this
    function's own output as outside and cut the same way twice.

    **The on-edge convention, stated rather than left to the arithmetic.** A
    point within :data:`ON_RING_EPS_DEG` (~0.1 mm) of any ring edge is INSIDE,
    checked before the ray cast and independently of it. That is what makes the
    answer stable for a point the crossing solver just placed on a slanted edge,
    where no float lands exactly on the line.

    The ray cast itself is the standard crossing-number test with the
    HALF-OPEN rule ``(a_lat > lat) != (b_lat > lat)``, and the two cases that rule
    exists for are the two this generator will actually meet on a hand-drawn
    lasso:

    * a HORIZONTAL edge satisfies neither side of the inequality, so it is
      skipped entirely instead of contributing an infinity when the ray is
      solved against it;
    * a VERTEX exactly at the ray's latitude belongs to the edge whose OTHER end
      is above it, so it is counted once rather than twice (which would flip
      parity back and report an interior point as outside) or zero times.

    The ray is cast in the +lon direction and the ring is treated as a plane
    polygon in (lat, lon). That is exact for the same reason the bbox kernels
    are: ``LocalTangentPlane`` is affine and separable, so a straight edge in
    degrees is a straight edge in projected metres and a point's side of it does
    not change under the projection.
    """
    if _on_ring(lat, lon, polygon):
        return True
    inside = False
    for (a_lat, a_lon), (b_lat, b_lon) in _ring_edges(polygon):
        if (a_lat > lat) != (b_lat > lat):
            crossing_lon = a_lon + (lat - a_lat) * (b_lon - a_lon) / (b_lat - a_lat)
            if lon < crossing_lon:
                inside = not inside
    return inside


def _ring_crossing_parameters(
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
    polygon: tuple[tuple[float, float], ...],
) -> list[float]:
    """Every ``t`` in ``[0, 1]`` where A→B meets the ring, unsorted.

    Segment-segment intersection in (lat, lon), solved with the standard
    cross-product parameterisation. Parallel pairs are skipped: a segment
    running ALONG a ring edge has no single crossing point to synthesize a node
    at, and the endpoints of that overlap are found by the neighbouring edges
    anyway.

    Edge parameters are accepted with a small tolerance at both ends so a
    segment passing exactly through a ring VERTEX is found on both of the edges
    that meet there rather than falling between them — the same crossing, so
    which one reports it does not matter.
    """
    d_lat = lat_b - lat_a
    d_lon = lon_b - lon_a
    out: list[float] = []
    for (e_lat, e_lon), (f_lat, f_lon) in _ring_edges(polygon):
        s_lat = f_lat - e_lat
        s_lon = f_lon - e_lon
        denom = d_lat * s_lon - d_lon * s_lat
        if denom == 0.0:
            continue
        q_lat = e_lat - lat_a
        q_lon = e_lon - lon_a
        t = (q_lat * s_lon - q_lon * s_lat) / denom
        u = (q_lat * d_lon - q_lon * d_lat) / denom
        if t < 0.0 or t > 1.0:
            continue
        if u < -1e-12 or u > 1.0 + 1e-12:
            continue
        out.append(t)
    return out


def _polygon_crossing_point(
    inside: OsmNode, outside: OsmNode, polygon: tuple[tuple[float, float], ...]
) -> tuple[float, float] | None:
    """Where the inside→outside segment leaves ``polygon``, ON the ring.

    The concave analogue of :func:`_crossing_point`'s corner rule, and it exists
    for the same reason: one segment can meet the ring SEVERAL times — out of a
    notch, back in, out again — and only the point where it LEAVES FIRST is where
    this way leaves the study area. Snapping to a later one would put the terminal
    on the far side of a notch, i.e. outside the region the user drew, which is
    the failure this whole trim exists to remove.

    "Leaves first" is not the same as "the smallest crossing parameter", and the
    difference is measured rather than theoretical: a segment starting at a
    REFLEX VERTEX and running ALONG the ring meets the ring at ``t = 0`` (the
    vertex it starts on) and again where it finally departs, and the smallest-t
    rule read the first of those as "this node is already on the edge" and cut
    188 m of on-boundary road off way 101 of the crossroads fixture — dropping the
    way entirely, because one node is not a carriageway. So the crossings are
    used as INTERVAL boundaries: the segment is sampled between consecutive
    crossings and the first interval whose midpoint is OUTSIDE marks the exit. A
    stretch running along the ring samples as inside (the inside test is
    boundary-inclusive), and a segment that merely touches a vertex without
    leaving is not an exit at all. Both are answers the smallest-t rule got wrong.

    **No bit-exact write here, and it does not matter.** The rectangle version
    writes the binding coordinate exactly to its edge value so the projected
    distance to that edge is exactly 0.0; a slanted ring edge has no such
    coordinate to write, and rounding the intersection would move the node OFF
    the edge rather than onto it. It is written as computed, at full float
    precision, and it is deterministic (a pure function of the two nodes and the
    ring). Nothing downstream needs it to be bit-exact: a synthesized terminal is
    classified as the model boundary by SET MEMBERSHIP in
    :attr:`OsmExtract.synthesized_node_ids` — ``connectivity.dead_ends`` reads
    that before it reads any distance — so the classification does not turn on
    the last bit of a float even though the distance band would.

    ``None`` when the exit coincides with the inside node, i.e. that node is
    already ON the ring and the segment leaves immediately: synthesizing a second
    node at the same place would add a zero-length piece of polyline to say what
    the existing node already says. "The same place" is judged with
    :data:`ON_RING_EPS_DEG`, not with ``==``, because that is the tolerance an
    intersection on a slanted edge can be trusted to.
    """
    d_lat = outside.lat - inside.lat
    d_lon = outside.lon - inside.lon
    crossings = _ring_crossing_parameters(
        inside.lat, inside.lon, outside.lat, outside.lon, polygon
    )
    bounds = sorted({0.0, 1.0} | {t for t in crossings if 0.0 < t < 1.0})
    exit_t: float | None = None
    for low, high in zip(bounds, bounds[1:]):
        middle = (low + high) / 2.0
        if not _inside_polygon(
            inside.lat + middle * d_lat, inside.lon + middle * d_lon, polygon
        ):
            exit_t = low
            break
    if exit_t is None:  # pragma: no cover - the outside node IS outside the ring
        return None
    if exit_t <= 0.0:
        return None
    lat = inside.lat + exit_t * d_lat
    lon = inside.lon + exit_t * d_lon
    if math.hypot(lat - inside.lat, lon - inside.lon) <= ON_RING_EPS_DEG:
        return None
    return lat, lon


def _inside_runs(flags: list[bool]) -> list[tuple[int, int]]:
    """The maximal runs of ``True`` in ``flags``, as inclusive ``(first, last)``."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


def trim_ways_to_requested_bbox(extract: OsmExtract) -> OsmExtract:
    """Cut every way's overhanging tails back to the region that was REQUESTED.

    Returns a NEW extract; never mutates the input, so a caller keeps the
    untrimmed one to diagnose against. With no ``requested_bbox`` the input is
    returned unchanged and *by identity*, which is what makes the no-bbox path
    byte-identical to the behaviour that existed before this function did.

    Which shape "the requested region" is
    -------------------------------------
    The POLYGON when the region was drawn as one
    (:attr:`OsmExtract.requested_polygon`), and the rectangle otherwise. That is
    the whole of this function's polygon awareness, and it is a swap of three
    kernels — the inside test, the crossing solver and the graze test — with
    every rule below stated in exactly the same words for both shapes.

    Before the polygon path existed, a lasso was trimmed against its BOUNDING
    BOX, and the failure that produced was measured on a real L-shaped lasso:
    a way running into the L's notch is inside the box and outside the ring, so
    it survived the trim whole and then DEAD-ENDED mid-notch, hundreds of metres
    from any edge — an interior finding the gate has to treat as a real fault,
    for road the user never asked for. The policy this implements is the user's:
    trim at the requested region, ignore the overhang.

    What a polygon does NOT change:

    * the rectangle path is untouched. With ``requested_polygon`` None the same
      three kernels run as before, so a rectangle request is byte-identical to
      what it was;
    * the boundary BAND (``connectivity.BOUNDARY_BAND_M`` against
      ``_boundary_rectangle``), the ``DEAD_END_BEYOND_REQUESTED_BBOX`` tripwire
      and the isolated-zone cause are still RECTANGLE arithmetic on
      ``requested_bbox``. They are a residual, named here rather than
      generalised, and a benign one for the cut terminals: those classify by set
      membership in :attr:`OsmExtract.synthesized_node_ids` before any distance
      is read, and the polygon is inside its own bounding box, so a terminal on
      the ring can never trip a tripwire about being outside the box. The
      residual is visible only for a terminal the SOURCE ends on the ring
      (nothing synthesized it, so the metre band judges it against the box) —
      it reads as an interior dead end, which is what it read as before this
      unit for every terminal in the notch;
    * one OSM way is still never split into two.

    Four outcomes per way, and every one of them is named:

    * any node inside → the leading and trailing runs of OUTSIDE nodes are CUT
      and a node is synthesized on the requested edge
      (:data:`TRIM_TAIL_CUT`, one :class:`WayTrim` per cut end). This is decided
      from the FIRST and LAST inside node, never from "is there exactly one run
      of them", so a way that leaves and re-enters still gets its overhanging
      tails cut;
    * an INTERIOR excursion — a bulge that leaves the region and comes back
      between two inside nodes — is kept whole and recorded
      (:data:`TRIM_KEPT_WHOLE_REENTRANT`). Cutting one would either delete a
      middle section the study area really does contain on both sides of, or
      split one OSM way into two ids. On a CONCAVE polygon this is the shape a
      notch produces most often: a way that runs across the mouth of the notch
      leaves the ring and comes back, and it is kept for the same reason a bbox
      excursion is;
    * no node inside at all, but a segment clipping the region → dropped
      (:data:`SKIP_ONLY_GRAZES_REQUESTED_BBOX`). Overpass's filter returns
      ways that merely INTERSECT the query area, so a road cutting a corner (or
      a notch) arrives whole with every vertex outside the study area. It cannot
      be trimmed:
      both of its terminals would have to be synthesized, and a carriageway all
      of whose vertices this generator invented is the mirror image of a
      carriageway it silently deleted. Keeping it whole is no better —
      *measured*: on job d31961abd950 that put a 144 m link entirely outside
      the study area into the network, joined to nothing, and the gate refused
      it as ``v3_orphan_link``. So it leaves by the named-skip door, and the
      skip says it touched the study area;
    * nothing of it inside and nothing crossing → dropped as a named skip
      (:data:`SKIP_OUTSIDE_REQUESTED_BBOX`).

    Way ids and tags are preserved in every case — only ``node_ids`` ever
    shrinks — because ``TurnRestriction`` matches ways by id and a re-id would
    silently un-enforce every ban naming a trimmed road.

    **Node sparsity is a limitation of this trim, and it is the same one for both
    shapes.** Runs are classified from NODES, so a single long segment whose two
    endpoints are both outside the region belongs entirely to its outside run
    even if the straight line between them passes through the study area. On a
    tail that segment is cut away with the rest of the run; on a way with no node
    inside at all it is the graze case above. Sub-segment surgery — splitting a
    run at an intersection nobody mapped — would invent BOTH terminals of the
    piece it kept, which is the invention the graze rule already refuses. A
    concave ring makes this reachable more often than a rectangle does (a notch
    is narrower than a study area), and it is named rather than papered over.

    **The invariant this function establishes**, and the reason the two rules
    above are stated in terms of the first and last inside node: after it runs,
    every way TERMINAL is inside or ON the requested region — exactly on it for a
    rectangle, on it to within :data:`ON_RING_EPS_DEG` for a polygon. Interior
    nodes may still be outside (a bulge), and that is fine — nothing classifies an
    interior node. Terminals are what ``connectivity.dead_ends`` reads, and its
    band test is ``min(x - minx, maxx - x, …) <= BOUNDARY_BAND_M``, which goes
    NEGATIVE outside the rectangle and is therefore satisfied at ANY distance
    out there. Measured on job d31961abd950 before this rule was stated this
    way: two re-entrant ways kept whole left terminals 23 m and 103 m OUTSIDE
    the study rectangle, and both were reported as ``model_boundary_correct`` —
    a cause the gate treats as benign. ``connectivity.dead_ends`` now also
    refuses to call an outside terminal a boundary (see
    :data:`~services.genesis.connectivity.DEAD_END_BEYOND_REQUESTED_BBOX`), so
    the invariant is defended from both sides rather than trusted from one.

    Synthesized node ids, and why they are safe
    -------------------------------------------
    ``base = max(every node id this extract mentions) + 1``, and the cuts take
    ``base``, ``base + 1``, … in the order they are visited: ways ascending by
    id, and within a way ``start`` before ``end``. Both halves matter. The
    OFFSET makes a collision with a real OSM id impossible by construction
    rather than by a probability argument — every real id is ``<= base - 1``.
    The ORDER is a pure function of the extract's contents, which are a pure
    function of the file, so two runs over the same ``.osm`` synthesize the same
    ids and the byte-identical-rebuild invariant holds with a bbox exactly as it
    does without one. (A hash of ``(way id, end)`` would also be deterministic
    but could collide with a real id, and OSM node ids are not bounded by
    anything this generator controls.)

    Ids are keyed by the SOURCE SEGMENT that was cut — the ordered pair
    ``(last inside node, first outside node)`` — not by the way. Two ways that
    share a crossing segment therefore share ONE synthesized node, and that is
    not an optimisation: it is the true topology. Both ways really do contain
    that segment, so they really do meet where it leaves the rectangle. Given
    two distinct nodes instead, they became two coincident terminals at the same
    point that the node merge then REFUSED to join (see
    :attr:`OsmExtract.synthesized_node_ids`), i.e. two disconnected stubs lying
    on top of each other.

    Every synthesized id is recorded in :attr:`OsmExtract.synthesized_node_ids`,
    which ``connectivity.merge_junction_nodes`` reads to refuse merging one into
    a real junction. A synthesized node is a truncation MARKER, not a surveyed
    place; averaging its position with a junction 6 m away would move it off the
    rectangle (measured 4.501 m off, against a 1.0 m band) and invent a turning
    movement the source never asserted.
    """
    bbox = extract.requested_bbox
    if bbox is None or extract.refused:
        # Refused short-circuits for the same reason ``merge_junction_nodes``
        # and ``without_ways`` do: there is no network here to cut, and a second
        # refusal appended to the first would say the same thing twice under a
        # different name.
        #
        # The RECTANGLE is what gates this, a ring alone is not, and that is a
        # named non-goal rather than an oversight: ``osm_fetch`` writes both keys
        # for every polygon fetch (the region record's ``region`` IS the ring's
        # bounding box), the boundary ladder needs the rectangle whatever the
        # trim does, and a polygon-without-a-box extract is a hand-built object
        # no road in this app produces. Trimming one would be an untested path
        # standing in for a case that does not occur.
        return extract

    # THE ONLY BRANCH ON SHAPE IN THIS FUNCTION. Three kernels are chosen here —
    # the inside test, the crossing solver, the graze test — and every rule below
    # is written once against whichever trio was picked, so a polygon build and a
    # rectangle build cannot drift into two different sets of semantics and a
    # rectangle build runs the identical kernels it ran before polygons existed.
    polygon = extract.requested_polygon
    #: The two words every human-readable string below is built from, so a
    #: polygon build's report never says "bbox" about a cut it made at a ring.
    #: The reason CODES are deliberately NOT parameterised — they are the stable
    #: vocabulary the census, the compliance report and the tests count by, and
    #: renaming them per shape would split every count in two.
    shape_word = "polygon" if polygon is not None else "bbox"
    if polygon is not None:
        region_name = "requested polygon"

        def is_inside(lat: float, lon: float) -> bool:
            return _inside_polygon(lat, lon, polygon)

        def crossing_of(inside: OsmNode, outside: OsmNode) -> tuple[float, float] | None:
            return _polygon_crossing_point(inside, outside, polygon)

        def segment_touches(a: OsmNode, b: OsmNode) -> bool:
            return bool(
                _ring_crossing_parameters(a.lat, a.lon, b.lat, b.lon, polygon)
            )
    else:
        region_name = "requested bbox"

        def is_inside(lat: float, lon: float) -> bool:
            return _inside_bbox(lat, lon, bbox)

        def crossing_of(inside: OsmNode, outside: OsmNode) -> tuple[float, float] | None:
            return _crossing_point(inside, outside, bbox)

        def segment_touches(a: OsmNode, b: OsmNode) -> bool:
            return _clip_parameters(a.lat, a.lon, b.lat, b.lon, bbox) is not None

    # Metres are for the REPORT only, so they are measured on the plane of the
    # untrimmed extract: it is deterministic, it exists here, and nothing
    # geometric downstream consumes it.
    from .project import origin_for_bounds  # noqa: PLC0415 - keeps osm.py leaf-ish

    source_bounds = extract.bounds()
    plane = origin_for_bounds(*source_bounds) if source_bounds else None

    def metres(a: OsmNode | tuple[float, float], b: OsmNode | tuple[float, float]) -> float:
        if plane is None:  # pragma: no cover - a parsed extract always has nodes
            return 0.0
        a_lat, a_lon = (a.lat, a.lon) if isinstance(a, OsmNode) else a
        b_lat, b_lon = (b.lat, b.lon) if isinstance(b, OsmNode) else b
        ax, ay = plane.forward(a_lat, a_lon)
        bx, by = plane.forward(b_lat, b_lon)
        return math.hypot(bx - ax, by - ay)

    mentioned = set(extract.nodes) | {nid for way in extract.ways for nid in way.node_ids}
    next_synthetic = (max(mentioned) + 1) if mentioned else 1

    nodes = dict(extract.nodes)
    kept: list[OsmWay] = []
    trims: list[WayTrim] = []
    dropped: list[OsmWay] = []
    grazing: list[OsmWay] = []
    too_short: list[OsmWay] = []
    #: ``(inside node id, outside node id)`` → the synthesized node on that
    #: segment. The key is the SOURCE SEGMENT, so two ways that share it share
    #: one node — see the docstring for why that is topology and not tidiness.
    synthesized_at: dict[tuple[int, int], int] = {}

    for way in sorted(extract.ways, key=lambda w: w.id):
        if any(nid not in nodes for nid in way.node_ids):
            # ``_drop_ways_with_unresolvable_nodes`` runs in the parser, so a
            # parsed extract never reaches this. A hand-built one might, and
            # geometry we do not have is not geometry to cut against.
            kept.append(way)  # pragma: no cover
            continue  # pragma: no cover
        flags = [is_inside(nodes[nid].lat, nodes[nid].lon) for nid in way.node_ids]
        runs = _inside_runs(flags)

        if len(runs) == 1 and runs[0] == (0, len(flags) - 1):
            kept.append(way)
            continue

        if not runs:
            crosses = any(
                segment_touches(nodes[nid_a], nodes[nid_b])
                for nid_a, nid_b in zip(way.node_ids, way.node_ids[1:])
            )
            (grazing if crosses else dropped).append(way)
            continue

        # The first and last INSIDE node bound what is kept. Everything between
        # them travels with the way — including a bulge that leaves the
        # region and comes back, which is an interior excursion and not a
        # tail. Everything before the first and after the last is a tail, and it
        # is cut whether or not the way re-enters, because a terminal outside
        # the region is one the boundary decision cannot honestly judge.
        first, last = runs[0][0], runs[-1][1]
        node_ids = list(way.node_ids[first : last + 1])
        trims_before = len(trims)
        if len(runs) > 1:
            trims.append(
                WayTrim(
                    way.id,
                    way.highway,
                    TRIM_KEPT_WHOLE_REENTRANT,
                    "whole",
                    0,
                    0.0,
                    0,
                    f"this way leaves the {region_name} and re-enters it "
                    f"({len(runs) - 1} interior excursion(s) between its first and last "
                    "node inside); those middle sections are kept whole, because cutting "
                    "one would either delete road the study area contains on both sides "
                    "of or split one OSM way into two ids. Its overhanging TAILS are cut "
                    "like any other way's",
                )
            )
        # "start" first, then "end" — the visit order the synthesized-id scheme
        # documents. The start cut prepends, so the end cut's append is still
        # the end of the list.
        for end, cut, neighbour_index in (
            ("start", list(range(first - 1, -1, -1)), first),
            ("end", list(range(last + 1, len(flags))), last),
        ):
            if not cut:
                continue
            inside_node = nodes[way.node_ids[neighbour_index]]
            outside_node = nodes[way.node_ids[cut[0]]]
            crossing = crossing_of(inside_node, outside_node)
            synthetic_id = 0
            terminal: OsmNode | tuple[float, float] = inside_node
            if crossing is not None:
                segment = (inside_node.id, outside_node.id)
                synthetic_id = synthesized_at.get(segment, 0)
                if not synthetic_id:
                    synthetic_id = next_synthetic
                    next_synthetic += 1
                    synthesized_at[segment] = synthetic_id
                    nodes[synthetic_id] = OsmNode(synthetic_id, crossing[0], crossing[1])
                terminal = crossing
                if end == "start":
                    node_ids.insert(0, synthetic_id)
                else:
                    node_ids.append(synthetic_id)
            overhang = max(
                metres(inside_node, nodes[way.node_ids[cut[-1]]])
                - metres(inside_node, terminal),
                0.0,
            )
            trims.append(
                WayTrim(
                    way.id,
                    way.highway,
                    TRIM_TAIL_CUT,
                    end,
                    len(cut),
                    overhang,
                    synthetic_id,
                    f"{len(cut)} node(s) of this way's {end} lay outside the "
                    f"{region_name}, overhanging it by {overhang:.1f} m; the way now "
                    "terminates "
                    + (
                        f"at node {synthetic_id}, synthesized ON the requested boundary"
                        if synthetic_id
                        else f"at node {inside_node.id}, which is already ON the "
                        "requested boundary"
                    ),
                )
            )
        if len(node_ids) < 2:
            # One node inside and no crossing to synthesize at either end: the
            # way touches the rectangle at a single mapped point. There is no
            # carriageway left to model, and a one-node way would break every
            # geometry pass downstream, so it leaves by the named-skip door
            # every other undrawable way already uses — and the trim records
            # written a moment ago are withdrawn with it, because "the way now
            # terminates at node N" is not true of a way that is not there.
            del trims[trims_before:]
            too_short.append(way)
            continue
        kept.append(replace(way, node_ids=tuple(node_ids)))

    skipped = list(extract.skipped)
    for way in sorted(dropped, key=lambda w: w.id):
        skipped.append(
            SkippedWay(
                way.id,
                SKIP_OUTSIDE_REQUESTED_BBOX,
                way.highway,
                f"no part of this way is inside the {shape_word} that was requested; "
                "Overpass returns whole ways, so a way entirely outside the window is a "
                "road the query reached past rather than a road of the study area",
            )
        )
    for way in sorted(grazing, key=lambda w: w.id):
        skipped.append(
            SkippedWay(
                way.id,
                SKIP_ONLY_GRAZES_REQUESTED_BBOX,
                way.highway,
                f"this way clips the {region_name} between two mapped vertices but has "
                "no node inside it, so every terminal a trim could give it would be one "
                "this generator invented; measured on the study area it cuts a corner "
                "of, and dropped rather than modelled from invented geometry alone",
            )
        )
    for way in sorted(too_short, key=lambda w: w.id):
        skipped.append(
            SkippedWay(
                way.id,
                SKIP_TOO_FEW_NODES,
                way.highway,
                f"only one node of this way is inside the {region_name} and it is "
                "already on the boundary, so the trim leaves no two points to draw a "
                "carriageway between",
            )
        )

    referenced = {nid for way in kept for nid in way.node_ids}
    trimmed = OsmExtract(
        source=extract.source,
        ways=kept,
        # Same pruning semantics as ``without_ways``: a node no surviving way
        # references would still size ``bounds()``, and ``bounds()`` is the
        # projection origin, i.e. every coordinate in the emitted file.
        nodes={nid: node for nid, node in nodes.items() if nid in referenced},
        skipped=skipped,
        refusals=list(extract.refusals),
        ways_seen=extract.ways_seen,
        declared_bounds=extract.declared_bounds,
        restrictions=list(extract.restrictions),
        restriction_refusals=list(extract.restriction_refusals),
        non_drivable_node_ids=set(extract.non_drivable_node_ids),
        non_drivable_node_highways={
            nid: set(values) for nid, values in extract.non_drivable_node_highways.items()
        },
        requested_bbox=extract.requested_bbox,
        # Carried AND consumed: when it is present it is the shape this transform
        # just cut against, and it is still what the boundary label reads to say
        # which shape was judged.
        requested_polygon=extract.requested_polygon,
        way_trims=list(extract.way_trims) + trims,
        synthesized_node_ids=extract.synthesized_node_ids
        | {nid for nid in synthesized_at.values() if nid in referenced},
    )
    _drop_restrictions_naming_absent_ways(trimmed)
    _drop_restrictions_whose_via_node_was_trimmed(trimmed, referenced, region_name)
    if not trimmed.ways:
        shape = (
            f"{len(polygon)} vertices, bounding box {bbox}"
            if polygon is not None
            else f"{bbox}"
        )
        trimmed.refusals.append(
            OsmRefusal(
                REFUSE_NO_DRIVABLE_WAYS,
                f"{Path(extract.source).name}: no drivable way survives the "
                f"{region_name} {shape}. Skips by reason: {trimmed.skipped_summary()}",
            )
        )
    return trimmed


def _drop_restrictions_whose_via_node_was_trimmed(
    extract: OsmExtract, referenced: set[int], region_name: str = "requested bbox"
) -> None:
    """A ban at a junction the trim removed is refused BY NAME, never forgotten.

    Same discipline as :func:`_drop_restrictions_naming_absent_ways`, and for
    the same reason: an unapplied restriction is a movement that stays legal in
    the model although the map forbids it, and the only unacceptable version of
    that is the one no report mentions.
    """
    applicable: list[TurnRestriction] = []
    for restriction in extract.restrictions:
        if restriction.via_node in referenced:
            applicable.append(restriction)
            continue
        extract.restriction_refusals.append(
            RestrictionRefusal(
                restriction.id,
                RESTRICTION_VIA_NODE_TRIMMED,
                f"restriction={restriction.kind} applies at node {restriction.via_node}, "
                f"which lay outside the {region_name} and was cut off with the tail it "
                "was on",
            )
        )
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

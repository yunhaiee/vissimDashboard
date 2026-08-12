"""Which OSM nodes are the same junction, and where a road really ends.

Roadmap row 2.3, Genesis (a). :mod:`services.genesis.osm` reads the file and
:mod:`services.genesis.draft` builds links and connectors; this module sits
between them and answers the three questions that decide whether the generated
network is *drivable*:

1. **Are these two nodes the same junction?** OSM is drawn by many hands. Two
   ways that meet on the ground routinely do not share a node, and one way
   routinely carries two junction nodes a few metres apart. Neither produces a
   connector, so the network silently breaks in two. :func:`merge_junction_nodes`
   joins them — under a tolerance that is *derived* (below), and with named
   refusals where the geometry says the ways genuinely do not meet.
2. **Is this the end of the road, or the edge of the download?** A way terminal
   a vehicle can reach and cannot leave is either a cul-de-sac (it must turn
   around) or the model boundary (it must leave). That is usually a terminal
   nobody else touches, but not always: where several ways all END at one point
   and every one of them is one-way *inbound*, the node is referenced twice and
   is still a place with no way out. :func:`dead_ends` decides from the
   topology and from the extract's own declared/derived bounds, never from
   proximity — see the note under :class:`DeadEnd`.
3. **Does the data forbid a movement?** :func:`restrictions_at` reports the OSM
   ``type=restriction`` relations that apply at a node, so the connector layer
   removes movements the map bans rather than steamrolling them.

Nothing here invents connectivity. Every function either joins objects the data
says are the same, or refuses **by name**. The refusal lists are outputs, not
logs: :class:`MergeReport` and every :class:`DeadEnd` carry a stable reason code
that ``backend/tools/genesis_connectivity_probe.py`` and the §5 compliance
report both read.

The node-merge tolerance, derived
---------------------------------
:data:`NODE_MERGE_TOLERANCE_M` = **7.0 m**, and it is an *upper bound on
sameness*, not an error budget:

    Two junctions of a modelled road network cannot be closer than one
    carriageway, because a junction is where carriageways meet. The narrowest
    carriageway this generator models is the one-lane-per-direction default of
    :data:`services.genesis.osm.DRIVABLE_HIGHWAY_DEFAULT_LANES`, which is
    ``2 × 3.5 m = 7.0 m`` wide (3.5 m is
    :data:`services.genesis.diff.NOMINAL_LANE_WIDTH_M`, the only lane width
    MRWA §5 prints — Table 5-7, doc p.195, quoted verbatim in
    ``draft.CIT_T57_LANE_WIDTH``). Two junction nodes closer together than that
    therefore cannot be two junctions. They are one junction, mapped twice.

The comparison is **strict** (``<``), so a pair exactly one carriageway apart —
the closest two *distinct* junctions can be — is not merged.

This is deliberately the same discipline as :mod:`services.genesis.benchmark`'s
22 m matching tolerance (14 m Helmert residual + 1 m projection budget + 7 m
centreline allowance): a number assembled from quantities this repository
already owns and can be re-derived from, rather than one chosen because it made
a count look better. The two are not the same kind of number and are not
interchangeable — the benchmark's is a *measurement error* budget for comparing
two independently-authored networks, this one is a *geometric* bound inside one.

What the tolerance does NOT license
-----------------------------------
Distance alone never merges anything. Four refusals fire first, each with its
own code:

* :data:`REFUSE_NO_JUNCTION_TO_RECONCILE` — **neither node is a junction the
  file already describes.** This is the rule that keeps the derivation above
  honest. "Two junctions cannot be closer than one carriageway" is a statement
  about *junctions*; it says nothing about two roads that merely END near each
  other. A merge may only reconcile a junction the source data already
  describes — it may never CREATE connectivity between ways that had none — so
  at least one node of every merged pair must be a node the source references
  from two or more kept ways. Two lone way-terminals 3.8 m apart are two roads
  that end near each other until some other evidence says otherwise, and
  welding them emits ordinary turn connectors between two streets that do not
  meet. Measured on the Murdoch extract: 2 of 97 clusters were exactly that.
  The refused pairs are reported, so "these two roads end within a carriageway
  of each other and were NOT joined" is a line a reviewer can act on rather
  than an invented road nothing names.
* :data:`REFUSE_GRADE_SEPARATED` — the ways disagree about
  ``layer``/``bridge``/``tunnel``. A freeway crossing a road passes within
  centimetres of it *in plan*; merging there would weld a network to the one
  passing under it. This check is not optional decoration: the Murdoch extract
  alone carries 59 ``layer`` and 22 ``bridge`` tags. It is evaluated **twice**:
  once on the candidate pair, and once on the whole cluster the union would
  produce. The second is not redundant. A node that legitimately carries BOTH
  signatures — a real ground/bridge transition point — intersects either grade,
  so a purely-ground node can be unioned into a cluster that already holds the
  elevated way while the far pair is never compared directly, because it
  exceeds the tolerance and so is never a candidate at all. The cluster form
  requires every member to share one common vertical position, which is the
  pairwise test generalised from two nodes to ``n``.
* :data:`REFUSE_CLUSTER_TOO_WIDE` — a chain of pairs each inside the tolerance
  can walk arbitrarily far. A cluster may not exceed
  :data:`MAX_CLUSTER_DIAMETER_M` (= 2 × the tolerance) end to end.
* :data:`REFUSE_WOULD_COLLAPSE_WAY` — every node of a way landed inside one
  cluster. Such a way is a stub shorter than the cluster cap whose two ends are
  the same junction: it is not a road *between* junctions, it is part of the
  junction, and it is recorded as a NAMED skip
  (:data:`services.genesis.osm.SKIP_ZERO_LENGTH`) rather than emitted as a
  zero-length link or deleted in silence. Only merge-eligible nodes are ever
  clustered, so a way carrying any interior shape vertex cannot reach this case.

Two carriageways of a dual road whose ends fall inside the tolerance *are*
merged, and that is safe for a different reason: the movement between them is
antiparallel, so ``draft.classify_turn`` labels it ``u_turn`` and
``build_draft`` suppresses it under §5.3.3 exactly as it does everywhere else.
The merge creates the junction; it does not create the median crossing.

Determinism
-----------
Candidate pairs are sorted by ``(distance, min id, max id)`` and unioned in that
order; the canonical id of a cluster is its lowest member id; cluster positions
are the mean of the members taken in id order. Same file in, same merge out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .diff import NOMINAL_LANE_WIDTH_M
from .osm import (
    DRIVABLE_HIGHWAY_DEFAULT_LANES,
    SKIP_ZERO_LENGTH,
    OsmExtract,
    OsmNode,
    OsmWay,
    SkippedWay,
    TurnRestriction,
)
from .project import MAX_PLANIMETRIC_ERROR_M, origin_for_bounds

# ---------------------------------------------------------------------------
# Tolerances. Each is derived in the module docstring; none is a bare literal.
# ---------------------------------------------------------------------------

#: The narrowest carriageway this generator models: one lane per direction at
#: the only lane width MRWA §5 prints (Table 5-7, doc p.195 — quoted verbatim in
#: ``draft.CIT_T57_LANE_WIDTH``; mirrored here through ``diff`` so the three
#: modules cannot drift apart).
NARROWEST_CARRIAGEWAY_M = 2.0 * NOMINAL_LANE_WIDTH_M

#: Two junction nodes closer together than one carriageway are one junction.
#: Strict ``<``: a pair exactly this far apart is left alone.
NODE_MERGE_TOLERANCE_M = NARROWEST_CARRIAGEWAY_M

#: A merged cluster may not span more than this end to end, so a chain of
#: individually-admissible pairs cannot walk across a junction and out the far
#: side. Two tolerances is the smallest cap that still admits a three-node
#: cluster whose outer pair straddles the tolerance.
MAX_CLUSTER_DIAMETER_M = 2.0 * NODE_MERGE_TOLERANCE_M

#: How close to the model's own boundary a way terminal must be before it counts
#: as "the road leaves the model" rather than "the road ends". The boundary is
#: whichever rung of :func:`_boundary_rectangle`'s ladder the extract can offer
#: — the REQUESTED bbox, else the file's declared ``<bounds>``, else the extent
#: of the kept geometry — and this band is the same on all three:
#: :data:`services.genesis.project.MAX_PLANIMETRIC_ERROR_M`, the projection's
#: own budget, because that is the smallest distance at which two points in this
#: frame are distinguishable at all.
#:
#: The band is deliberately NOT widened to cover a boundary that is in the wrong
#: place. A band big enough to swallow a 300 m overhang would also swallow every
#: real cul-de-sac within 300 m of the edge and turn it into a hole traffic
#: leaves through — so when the rectangle was wrong the RECTANGLE was fixed
#: (rung 1, plus the trim that puts the cut terminals exactly on it).
BOUNDARY_BAND_M = MAX_PLANIMETRIC_ERROR_M

#: Highway classes on which a cul-de-sac is surprising enough to be named in the
#: report. An arterial that dead-ends is far more often a break in the map than
#: a real turning head — but "surprising" is not "wrong", so these are emitted
#: WITH a warning (guardrail doctrine tier 2, ``docs/phase2/00-EXECUTION-GUIDE``
#: §4) rather than refused.
ARTERIAL_CLASSES = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
    }
)

# --- merge refusal codes ---------------------------------------------------

REFUSE_GRADE_SEPARATED = "grade_separated"
REFUSE_CLUSTER_TOO_WIDE = "cluster_diameter_exceeded"
REFUSE_WOULD_COLLAPSE_WAY = "would_collapse_a_way"
#: Neither node of the pair is a junction the source file already describes, so
#: joining them would invent a junction rather than reconcile one.
REFUSE_NO_JUNCTION_TO_RECONCILE = "no_junction_to_reconcile"
#: One side of the pair is a node THIS GENERATOR synthesized on the requested
#: bbox when it cut a tail (:attr:`services.genesis.osm.OsmExtract.
#: synthesized_node_ids`). See :func:`merge_junction_nodes` for the argument and
#: the measurement.
REFUSE_SYNTHESIZED_BOUNDARY_NODE = "synthesized_boundary_node"

# --- dead-end reason codes -------------------------------------------------

#: A way terminal no other kept way touches, inside the model, on a way that is
#: two-way there. A vehicle that drives in must be able to drive out.
DEAD_END_U_TURN = "cul_de_sac_u_turn"
#: Same, except the road continues as something this generator does not model
#: (a footway, a path, a track). A motor vehicle still cannot continue, so the
#: U-turn is still right — but the case is counted separately because the
#: continuation exists in the file and a reviewer may disagree.
DEAD_END_U_TURN_NON_DRIVABLE_CONTINUATION = "cul_de_sac_u_turn_non_drivable_continuation"
#: The terminal is on the model's boundary. The road continues past it in the
#: world; a vehicle reaching it should LEAVE the network, and a U-turn here
#: would turn the study boundary into a reflecting wall.
DEAD_END_MODEL_BOUNDARY = "model_boundary"
#: The terminal is strictly OUTSIDE the boundary rectangle. Not the same thing
#: as :data:`DEAD_END_MODEL_BOUNDARY` and deliberately not benign: a road that
#: leaves the model at its edge is correct, and a link end sitting in space
#: beyond the study area is a generator defect. The distinction has to be drawn
#: here because the band test is a SIGNED distance to the nearest edge —
#: ``min(x - minx, maxx - x, …)`` — which goes negative outside and so satisfies
#: ``<= BOUNDARY_BAND_M`` at any distance out there. Measured on job
#: d31961abd950: two re-entrant ways kept whole left terminals 23 m and 103 m
#: outside the rectangle and both were reported ``model_boundary_correct``, a
#: cause the R1.5 gate filters out as benign, so V3 passed over them in silence.
#:
#: **Scoped to the requested-bbox rung, and only there.** On that rung
#: ``osm.trim_ways_to_requested_bbox`` cuts every tail at the rectangle, so an
#: outside terminal is a defect in this generator's own geometry, the reason
#: should be unreachable, and if the trim ever regresses the gate must FAIL
#: loudly rather than pass quietly — which is exactly what this code is for. On
#: the declared-``<bounds>`` rung there is no trim and therefore no remedy: a
#: whole-way extract overhanging the window it declares is the normal shape of
#: such a file, and grading it a fault would permanently refuse uploads nothing
#: is wrong with. Those keep :data:`DEAD_END_MODEL_BOUNDARY` (with an honest
#: detail that never prints a negative distance). Trimming rung 2 as well is a
#: deliberate non-goal of this unit and a named follow-up.
DEAD_END_BEYOND_REQUESTED_BBOX = "terminal_outside_the_model_boundary"
#: A one-way way that ends nowhere. No U-turn is possible — there is only one
#: carriageway — so this is reported, never fixed. Usually a break in the map.
DEAD_END_ONE_WAY = "one_way_dead_end_unfixable"
#: An OSM ``type=restriction`` relation bans the U-turn at this node.
DEAD_END_U_TURN_RESTRICTED = "u_turn_banned_by_osm_restriction"

# --- dangling-approach cause codes -----------------------------------------
# A *dangling approach* is a built link no connector leaves: the place a vehicle
# departs the network in silence. These codes are the vocabulary the generator
# itself uses to attribute every one of them (``draft._dangling_approaches``,
# written into ``metadata['connectivity']``) AND the vocabulary
# ``backend/tools/genesis_connectivity_probe.py`` reports — one definition, so
# the tool and the generator cannot disagree about what a cause is called. They
# are stable strings: CI and the §5 compliance report both key off them.

#: The dead-end pass said a U-turn was due here and none was built. A bug.
DANGLING_CUL_DE_SAC = "cul_de_sac_u_turn_missing"
#: A one-way way ends with no second carriageway to turn into. Reported, never
#: repaired — inventing the reverse carriageway would be inventing a road.
DANGLING_ONE_WAY = "one_way_dead_end_unfixable"
#: The model's edge. Correct: a vehicle here is meant to leave the network.
DANGLING_MODEL_BOUNDARY = "model_boundary_correct"
#: Every onward movement was banned by an OSM turn restriction. Correct.
DANGLING_RESTRICTED = "every_movement_banned_by_a_turn_restriction"
#: The approach reaches a node other ways touch, and not one of them offers a
#: way out (they all END there, one-way inbound).
DANGLING_NO_EXIT_AT_JUNCTION = "junction_with_no_exit_link"
#: Exits exist at the node, but every movement onto them classified as a U-turn
#: and was suppressed under §5.3.3.
DANGLING_ALL_MOVEMENTS_U = "junction_where_every_movement_is_a_u_turn"
#: The approach's node belongs to no other way — a merge that did not happen.
DANGLING_UNMERGED_NEIGHBOUR = "unmerged_node_of_another_way"
#: The link ends OUTSIDE the model boundary — beyond the study area rather than
#: on its edge. Deliberately absent from :data:`BENIGN_DANGLING_CAUSES`: this is
#: a generator defect, not a road correctly leaving the model.
DANGLING_BEYOND_MODEL_BOUNDARY = "terminal_outside_the_model_boundary"
#: No cause could be established. Named rather than omitted: a dangling end the
#: generator cannot explain is the one a reviewer most needs to see.
DANGLING_UNATTRIBUTED = "unattributed"

#: Causes that are CORRECT — a real network is not expected to reach zero.
BENIGN_DANGLING_CAUSES = frozenset({DANGLING_MODEL_BOUNDARY, DANGLING_RESTRICTED})

#: A dead end the topology already named keeps that name when it turns out to
#: be dangling, so the two reports use one vocabulary.
DEAD_END_REASON_TO_DANGLING_CAUSE = {
    DEAD_END_MODEL_BOUNDARY: DANGLING_MODEL_BOUNDARY,
    DEAD_END_BEYOND_REQUESTED_BBOX: DANGLING_BEYOND_MODEL_BOUNDARY,
    DEAD_END_ONE_WAY: DANGLING_ONE_WAY,
    DEAD_END_U_TURN_RESTRICTED: DANGLING_RESTRICTED,
    DEAD_END_U_TURN: DANGLING_CUL_DE_SAC,
    DEAD_END_U_TURN_NON_DRIVABLE_CONTINUATION: DANGLING_CUL_DE_SAC,
}


# ---------------------------------------------------------------------------
# Node merging
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeMerge:
    """One cluster of OSM nodes declared to be a single junction."""

    canonical_id: int
    member_ids: tuple[int, ...]
    max_separation_m: float
    lat: float
    lon: float

    def as_dict(self) -> dict:
        return {
            "canonical_id": self.canonical_id,
            "member_ids": list(self.member_ids),
            "max_separation_m": round(self.max_separation_m, 3),
        }


@dataclass(frozen=True)
class MergeRefusal:
    """Two nodes inside the tolerance that were NOT merged, and why."""

    node_a: int
    node_b: int
    separation_m: float
    code: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "node_a": self.node_a,
            "node_b": self.node_b,
            "separation_m": round(self.separation_m, 3),
            "code": self.code,
            "detail": self.detail,
        }


@dataclass
class MergeReport:
    tolerance_m: float = NODE_MERGE_TOLERANCE_M
    merges: list[NodeMerge] = field(default_factory=list)
    refusals: list[MergeRefusal] = field(default_factory=list)
    #: original node id → canonical node id, for every node that moved.
    canonical: dict[int, int] = field(default_factory=dict)
    #: ways dropped because merging collapsed them below two distinct nodes.
    collapsed_way_ids: tuple[int, ...] = ()

    @property
    def merged_node_count(self) -> int:
        return sum(len(m.member_ids) for m in self.merges)

    def as_dict(self) -> dict:
        return {
            "tolerance_m": self.tolerance_m,
            "basis": TOLERANCE_BASIS,
            "clusters": [m.as_dict() for m in self.merges],
            "nodes_merged": self.merged_node_count,
            "refusals": [r.as_dict() for r in self.refusals],
            "collapsed_way_ids": list(self.collapsed_way_ids),
        }


TOLERANCE_BASIS = (
    f"{NODE_MERGE_TOLERANCE_M:g} m = 2 x {NOMINAL_LANE_WIDTH_M:g} m nominal lane width "
    "(MRWA OMG v2.0 Table 5-7, doc p.195 — the only lane width §5 prints), i.e. one "
    "carriageway of the narrowest road this generator models. Two junctions cannot be "
    "closer than one carriageway, so two junction nodes closer than this are one junction "
    "mapped twice. Strict '<': a pair exactly one carriageway apart is not merged. "
    "Distance never merges alone, and the derivation is about JUNCTIONS: at least one node "
    "of every merged pair must be a junction the file already declares, so a merge can only "
    "reconcile a junction described twice and can never create one between two roads that "
    "merely end near each other. Grade separation (layer/bridge/tunnel disagreement, checked "
    "on the pair AND on the whole cluster), cluster diameter and way collapse each refuse "
    "first, by name."
)


def _effective_layer(way_tags_layer: str) -> int:
    try:
        return int(str(way_tags_layer).strip())
    except (TypeError, ValueError):
        return 0


def _vertical_signature(way: OsmWay) -> tuple[int, bool, bool]:
    """(layer, is bridge, is tunnel) — the vertical position of a way.

    Two ways whose signatures differ are at different heights where they cross,
    and welding them together is the grade-separation bug this refusal exists
    to prevent.
    """
    return (
        _effective_layer(way.layer),
        way.bridge not in ("", "no"),
        way.tunnel not in ("", "no"),
    )


def _eligible_node_ids(extract: OsmExtract) -> set[int]:
    """Nodes that can take part in a merge: junctions and way terminals.

    Never a shape vertex. Merging is a statement about *junction identity*. Two
    shape vertices three metres apart on a bend are not a junction and joining
    them would only resample geometry, so they are excluded outright rather than
    being caught by a tolerance that happens not to reach them.

    Being eligible is NOT being mergeable. A lone way terminal is admitted here
    only so it can be reconciled INTO a junction the file already describes;
    :data:`REFUSE_NO_JUNCTION_TO_RECONCILE` refuses every pair in which neither
    side is such a junction, because two roads that merely end near each other
    are two roads. The distinction matters because the tolerance's whole
    derivation ("two junctions cannot be closer than one carriageway") is a
    statement about junctions and says nothing about arbitrary road ends.
    """
    occurrences: dict[int, int] = {}
    for way in extract.ways:
        for node_id in way.node_ids:
            occurrences[node_id] = occurrences.get(node_id, 0) + 1
    eligible = {node_id for node_id, count in occurrences.items() if count >= 2}
    for way in extract.ways:
        eligible.add(way.node_ids[0])
        eligible.add(way.node_ids[-1])
    return eligible


def _cluster_grade_conflict(
    members: list[int], ways_at: dict[int, list[OsmWay]]
) -> tuple[tuple, ...] | None:
    """The vertical positions of a proposed cluster, when they do not agree.

    Returns the members' signature sets (sorted, for the refusal message) when
    they share NO common vertical position, and ``None`` when they do. This is
    ``sig_a & sig_b`` generalised from two nodes to the whole cluster, and it is
    the only form that survives an intermediate node: a real ground/bridge
    transition point carries BOTH signatures, so it intersects either grade and
    lets a purely-ground node and a purely-elevated node end up in one cluster
    without the two ever being compared to each other.
    """
    signatures = [
        {_vertical_signature(w) for w in ways_at.get(node_id, ())} for node_id in members
    ]
    present = [s for s in signatures if s]
    if len(present) < 2 or set.intersection(*present):
        return None
    return tuple(tuple(sorted(s)) for s in present)


def merge_junction_nodes(extract: OsmExtract) -> tuple[OsmExtract, MergeReport]:
    """Join OSM nodes that describe one junction. Returns a NEW extract.

    The input extract is not mutated: ``draft.build_draft`` keeps the original so
    the report can say what the file contained as well as what was modelled.

    A no-op (no candidate pair inside the tolerance) returns an extract whose
    ways and nodes are the same objects, so a file with clean topology produces
    byte-identical output to what this generator produced before this function
    existed.
    """
    report = MergeReport()
    if extract.refused or not extract.ways or not extract.nodes:
        return extract, report

    bounds = extract.bounds()
    if bounds is None:  # pragma: no cover - a non-refused extract always has nodes
        return extract, report
    plane = origin_for_bounds(*bounds)
    xy = {
        node_id: plane.forward(node.lat, node.lon) for node_id, node in extract.nodes.items()
    }

    eligible = sorted(_eligible_node_ids(extract) & set(xy))
    #: The junctions the FILE declares, read before anything is merged. A merge
    #: may only reconcile one of these; it may never invent one. Taken from the
    #: source topology rather than from the growing union-find, so the rule does
    #: not depend on the order candidate pairs happen to be processed in.
    declared_junctions = extract.intersection_node_ids()
    ways_at: dict[int, list[OsmWay]] = {}
    for way in extract.ways:
        for node_id in set(way.node_ids):
            ways_at.setdefault(node_id, []).append(way)

    # Spatial hash so this is linear in the number of eligible nodes rather than
    # quadratic: a metropolitan extract has hundreds of thousands of them.
    cell = max(NODE_MERGE_TOLERANCE_M, 1.0)
    grid: dict[tuple[int, int], list[int]] = {}
    for node_id in eligible:
        x, y = xy[node_id]
        grid.setdefault((int(x // cell), int(y // cell)), []).append(node_id)

    pairs: list[tuple[float, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for node_id in eligible:
        x, y = xy[node_id]
        cx, cy = int(x // cell), int(y // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in grid.get((cx + dx, cy + dy), ()):
                    if other == node_id:
                        continue
                    key = (min(node_id, other), max(node_id, other))
                    if key in seen:
                        continue
                    seen.add(key)
                    distance = math.dist(xy[node_id], xy[other])
                    if distance < NODE_MERGE_TOLERANCE_M:
                        pairs.append((distance, key[0], key[1]))
    if not pairs:
        return extract, report

    pairs.sort()

    parent: dict[int, int] = {}

    def find(node_id: int) -> int:
        parent.setdefault(node_id, node_id)
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    members: dict[int, list[int]] = {}

    for distance, a, b in pairs:
        synthesized = sorted(
            {a, b} & set(extract.synthesized_node_ids)
        )
        if synthesized:
            # A node this generator put ON the requested rectangle to mark where
            # a road leaves the study area is not a surveyed place. The merge's
            # licence — "two junctions cannot be closer than one carriageway" —
            # is a statement about junctions the SOURCE describes, and it says
            # nothing about a truncation marker that happens to land near one.
            #
            # The no-junction guard above cannot catch this, because it passes
            # as soon as ONE side is a declared junction, and the dangerous case
            # is exactly a real junction 6 m inside the edge. Measured on that
            # construction: the cluster position is the MEAN of its members, so
            # the boundary node moved 4.501 m off the rectangle — against a
            # 1.0 m band — and three things went wrong at once. The terminal's
            # occurrences became 3, so ``dead_ends`` stopped seeing it as a
            # terminal at all and the boundary exit vanished from every report;
            # the network gained a turning movement between two roads the source
            # never joins; and neither was visible in any count.
            report.refusals.append(
                MergeRefusal(
                    a,
                    b,
                    distance,
                    REFUSE_SYNTHESIZED_BOUNDARY_NODE,
                    f"node(s) {synthesized} were synthesized by this generator on the "
                    "requested bbox to mark where a road leaves the study area — a "
                    "truncation marker, not an independently-mapped meeting point. "
                    "Merging one would move it off the model boundary and invent a "
                    "movement the source does not describe",
                )
            )
            continue

        sig_a = {_vertical_signature(w) for w in ways_at.get(a, ())}
        sig_b = {_vertical_signature(w) for w in ways_at.get(b, ())}
        if sig_a and sig_b and not (sig_a & sig_b):
            report.refusals.append(
                MergeRefusal(
                    a,
                    b,
                    distance,
                    REFUSE_GRADE_SEPARATED,
                    f"vertical position differs — (layer, bridge, tunnel) {sorted(sig_a)} "
                    f"vs {sorted(sig_b)}; these ways cross, they do not meet",
                )
            )
            continue

        if a not in declared_junctions and b not in declared_junctions:
            report.refusals.append(
                MergeRefusal(
                    a,
                    b,
                    distance,
                    REFUSE_NO_JUNCTION_TO_RECONCILE,
                    "neither node is a junction this file declares — both are lone way "
                    "terminals — so joining them would CREATE a junction rather than "
                    "reconcile one the data already describes twice. Two roads that end "
                    "near each other are two roads; if they really meet, the source data "
                    "has to say so by sharing a node",
                )
            )
            continue

        root_a, root_b = find(a), find(b)
        if root_a == root_b:
            continue
        group_a = members.get(root_a, [root_a])
        group_b = members.get(root_b, [root_b])
        merged = sorted(set(group_a) | set(group_b))
        conflict = _cluster_grade_conflict(merged, ways_at)
        if conflict is not None:
            report.refusals.append(
                MergeRefusal(
                    a,
                    b,
                    distance,
                    REFUSE_GRADE_SEPARATED,
                    "the CLUSTER this merge would make shares no vertical position — "
                    f"(layer, bridge, tunnel) per member {list(conflict)}. One member sits "
                    "at both grades, which is a real transition point, but a cluster is one "
                    "junction and a junction is at one height; joining these would weld a "
                    "network to the one passing over or under it",
                )
            )
            continue
        diameter = max(
            math.dist(xy[p], xy[q])
            for i, p in enumerate(merged)
            for q in merged[i + 1 :]
        )
        if diameter > MAX_CLUSTER_DIAMETER_M:
            report.refusals.append(
                MergeRefusal(
                    a,
                    b,
                    distance,
                    REFUSE_CLUSTER_TOO_WIDE,
                    f"merging would make a cluster {diameter:.2f} m across, over the "
                    f"{MAX_CLUSTER_DIAMETER_M:g} m cap",
                )
            )
            continue
        root = min(root_a, root_b)
        other = max(root_a, root_b)
        parent[other] = root
        members[root] = merged
        members.pop(other, None)

    clusters = {root: sorted(group) for root, group in members.items() if len(group) > 1}
    if not clusters:
        return extract, report

    canonical: dict[int, int] = {}
    for root, group in clusters.items():
        for node_id in group:
            canonical[node_id] = root

    def rewritten(way: OsmWay) -> tuple[int, ...]:
        out: list[int] = []
        for node_id in way.node_ids:
            mapped = canonical.get(node_id, node_id)
            if not out or out[-1] != mapped:
                out.append(mapped)
        return tuple(out)

    merged_nodes: dict[int, OsmNode] = {}
    for node_id, node in extract.nodes.items():
        if node_id in canonical and canonical[node_id] != node_id:
            continue
        merged_nodes[node_id] = node
    for root, group in clusters.items():
        lat = sum(extract.nodes[n].lat for n in group) / len(group)
        lon = sum(extract.nodes[n].lon for n in group) / len(group)
        merged_nodes[root] = OsmNode(root, lat, lon)
        report.merges.append(
            NodeMerge(
                canonical_id=root,
                member_ids=tuple(group),
                max_separation_m=max(
                    math.dist(xy[p], xy[q])
                    for i, p in enumerate(group)
                    for q in group[i + 1 :]
                ),
                lat=lat,
                lon=lon,
            )
        )
    report.merges.sort(key=lambda m: m.canonical_id)
    report.canonical = {k: v for k, v in sorted(canonical.items()) if k != v}

    new_ways: list[OsmWay] = []
    collapsed: list[int] = []
    for way in extract.ways:
        node_ids = rewritten(way)
        if len(node_ids) < 2:
            # Every node of this way landed in ONE cluster: the way is a stub
            # shorter than the cluster cap whose two ends are the same junction,
            # so it is not a road between junctions — it IS the junction, and
            # §5.3.3's "minimise the number of links and connectors" says so.
            # Recorded as a NAMED skip so it is never a silent deletion. Only
            # merge-eligible nodes (junctions and terminals) are ever clustered,
            # so a way with any interior shape vertex cannot reach this branch.
            collapsed.append(way.id)
            report.refusals.append(
                MergeRefusal(
                    node_ids[0] if node_ids else 0,
                    node_ids[0] if node_ids else 0,
                    0.0,
                    REFUSE_WOULD_COLLAPSE_WAY,
                    f"way {way.id} ({way.highway}) has every node inside one merged "
                    "junction; the way is dropped as a named skip rather than emitted "
                    "as a zero-length link",
                )
            )
            continue
        new_ways.append(way if node_ids == way.node_ids else replace(way, node_ids=node_ids))

    out = OsmExtract(
        source=extract.source,
        ways=new_ways,
        nodes=merged_nodes,
        skipped=list(extract.skipped),
        refusals=list(extract.refusals),
        ways_seen=extract.ways_seen,
        declared_bounds=extract.declared_bounds,
        restrictions=[
            replace(
                r,
                via_node=canonical.get(r.via_node, r.via_node),
            )
            for r in extract.restrictions
        ],
        restriction_refusals=list(extract.restriction_refusals),
        non_drivable_node_ids={
            canonical.get(n, n) for n in extract.non_drivable_node_ids
        },
        # Every field of OsmExtract is listed here deliberately, and
        # ``test_genesis_connectivity`` asserts mechanically that they all are.
        # ``non_drivable_node_highways`` was silently dropped by this
        # constructor from the day the field was added: harmless only by
        # accident (``diagnose_isolated_zones`` reads the PRE-merge extract, so
        # the empty map was never consulted), and the same defect class this
        # unit exists to kill. Its keys are node ids, so they are remapped
        # through the merge exactly as ``non_drivable_node_ids`` is — and the
        # values of two nodes that merged into one junction are UNIONED rather
        # than one of them winning, because both footways really do touch the
        # junction that now exists.
        non_drivable_node_highways=_remap_node_classes(
            extract.non_drivable_node_highways, canonical
        ),
        requested_bbox=extract.requested_bbox,
        way_trims=list(extract.way_trims),
        # Remapped like every other node-id-keyed field. It cannot actually
        # change — a synthesized node is never a merge candidate, by the refusal
        # above — but a field whose correctness rests on another rule staying
        # true is a field that breaks the day that rule moves.
        synthesized_node_ids=frozenset(
            canonical.get(n, n) for n in extract.synthesized_node_ids
        ),
    )
    for way_id in collapsed:
        out.skipped.append(
            SkippedWay(
                way_id,
                SKIP_ZERO_LENGTH,
                "",
                "every node collapsed into one junction by the node merge",
            )
        )
    report.collapsed_way_ids = tuple(collapsed)
    return out, report


def _remap_node_classes(
    classes: dict[int, set[str]], canonical: dict[int, int]
) -> dict[int, set[str]]:
    """``{node id: highway classes}`` with every key moved to its cluster root."""
    out: dict[int, set[str]] = {}
    for node_id, values in classes.items():
        out.setdefault(canonical.get(node_id, node_id), set()).update(values)
    return out


# ---------------------------------------------------------------------------
# Dead ends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadEnd:
    """One way terminal that no other kept way touches, and what to do there.

    Detected from **topology** — the terminal's node is referenced by exactly one
    kept way — and never from proximity. A pair of link ends 3.5 m apart is what
    a cul-de-sac looks like in the emitted file, but it is also what a mid-block
    pair looks like when the junction connectors failed for some other reason,
    and those two need opposite fixes. The topology tells them apart; the
    distance does not.
    """

    node_id: int
    way_id: int
    highway: str
    reason: str
    emit_u_turn: bool
    #: Named in the compliance report even when the U-turn is emitted: an
    #: arterial cul-de-sac is more often a break in the map than a turning head.
    warn_arterial: bool = False
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "way_id": self.way_id,
            "highway": self.highway,
            "reason": self.reason,
            "emit_u_turn": self.emit_u_turn,
            "warn_arterial": self.warn_arterial,
            "detail": self.detail,
        }


#: The three rungs of the boundary ladder, most authoritative first. Stable
#: strings: they are printed in every :class:`DeadEnd` detail, in the published
#: ``BOUNDARY_BAND_M`` assumption and in the draft metadata, and a reader
#: comparing two builds needs to be able to tell which boundary each used.
BOUNDARY_SOURCE_REQUESTED_BBOX = "requested bbox (fetch provenance)"
BOUNDARY_SOURCE_DECLARED_BOUNDS = "declared <bounds>"
BOUNDARY_SOURCE_HULL = "extent of the kept geometry"


def boundary_source(extract: OsmExtract) -> str:
    """Which rung of the ladder this extract's boundary comes from.

    Split out from :func:`_boundary_rectangle` so the label can be recorded in
    the draft metadata even for a build with no dead end at all — "which
    boundary did this network use?" is a question about the build, not about
    whether anything happened to sit on the edge.
    """
    if extract.requested_bbox is not None:
        return BOUNDARY_SOURCE_REQUESTED_BBOX
    if extract.declared_bounds is not None:
        return BOUNDARY_SOURCE_DECLARED_BOUNDS
    return BOUNDARY_SOURCE_HULL


def _boundary_rectangle(extract: OsmExtract, xy: dict[int, tuple[float, float]]):
    """The model's edge, in projected metres, and where it came from.

    A three-rung ladder, most authoritative first:

    1. **the bbox that was REQUESTED**, when the file carries this app's fetch
       provenance. This is the study area — the thing the modeller asked for —
       and it is believed over both alternatives below. Overpass answers a bbox
       query with WHOLE ways, so the geometry that comes back overhangs the
       request by whatever the longest tail happens to be (100 m to 2300 m,
       measured on this project's own fetches). Before this rung existed the
       hull of that geometry WAS the boundary, so every legitimate boundary cut
       sat hundreds of metres inside the rectangle, missed the 1 m band, and was
       classified as an interior dead end — which is why every bbox extraction
       this generator produced was refused by the gate's V3. The trim in
       :func:`services.genesis.osm.trim_ways_to_requested_bbox` then puts the
       cut terminals exactly ON this rectangle, so they are 0.00 m from it.
    2. the file's own declared ``<bounds>``, when it has one. Most Overpass
       exports on this machine do not; a JOSM save does.
    3. the extent of the kept geometry. The only boundary a bare file states,
       and only a terminal exactly ON it can be shown to be one.

    Rungs 1 and 2 are projected with the same plane construction, and it is the
    plane of the extract's OWN geometry (``origin_for_bounds(*extract.bounds())``)
    in both cases — the same plane :func:`dead_ends` projects the nodes with, so
    the comparison is between two quantities in one frame.

    How much to trust a terminal inside the band, on rung 1
    ------------------------------------------------------
    Two different things land inside the band on the requested-bbox rung, and
    they do not deserve equal confidence.

    * A terminal this generator SYNTHESIZED
      (:attr:`~services.genesis.osm.OsmExtract.synthesized_node_ids`) is 0.00 m
      from the rectangle **by construction** — the cut solves for the crossing
      and writes the binding coordinate exactly to the edge value. Its
      classification is as sound as the arithmetic.
    * Any OTHER terminal inside the band is one that a mapped OSM node happens
      to place within 1.0 m of the rectangle, and that is only as trustworthy as
      the rectangle itself. The requested bbox is a USER-SUPPLIED number, drawn
      on a map or typed to four decimals; unlike the hull and unlike a declared
      ``<bounds>``, nothing in the data derives it. A rectangle placed a metre
      differently would classify such a terminal the other way.

    This is stated rather than warned about on purpose: the second case is
    uncommon, the honest response to it is a reader who knows how the rectangle
    was chosen, and a per-terminal warning would bury the first case — which is
    the overwhelming majority and is exact — in noise.
    """
    declared = (
        extract.requested_bbox
        if extract.requested_bbox is not None
        else extract.declared_bounds
    )
    if declared is not None:
        min_lat, min_lon, max_lat, max_lon = declared
        plane = origin_for_bounds(*extract.bounds())
        corners = [
            plane.forward(min_lat, min_lon),
            plane.forward(min_lat, max_lon),
            plane.forward(max_lat, min_lon),
            plane.forward(max_lat, max_lon),
        ]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        return (min(xs), max(xs), min(ys), max(ys)), boundary_source(extract)
    xs = [p[0] for p in xy.values()]
    ys = [p[1] for p in xy.values()]
    return (min(xs), max(xs), min(ys), max(ys)), BOUNDARY_SOURCE_HULL


def _departs(way: OsmWay, node_id: int) -> bool:
    """Can a vehicle LEAVE ``node_id`` along this way?

    At a way's first node it can if the way runs forward; at its last node it
    can if the way runs backward; at any interior node the way passes through,
    so it always can. Direction matters: a one-way way that ENDS here offers no
    way out of here, which is the whole reason this function is not
    ``node_id in way.node_ids``.
    """
    if node_id == way.node_ids[0]:
        return any(direction.forward for direction in way.directions)
    if node_id == way.node_ids[-1]:
        return any(not direction.forward for direction in way.directions)
    return True


def _arrives(way: OsmWay, node_id: int) -> bool:
    """Can a vehicle travelling this way REACH ``node_id`` and have to stop?

    The mirror of :func:`_departs`. A one-way way's FIRST node is not a place a
    vehicle can arrive at along it, so it is not a trap however isolated it is.
    """
    if node_id == way.node_ids[-1] and any(d.forward for d in way.directions):
        return True
    if node_id == way.node_ids[0] and any(not d.forward for d in way.directions):
        return True
    return False


def _is_trapped_terminal(
    way: OsmWay, node_id: int, ways_at: dict[int, list[OsmWay]]
) -> bool:
    """A terminal several ways share, that still has no way out.

    ``occurrences == 1`` is the common dead end and used to be the only one this
    module could see. It is not the only one there is: where two or more ways
    all END at one point and every one of them is one-way *inbound*, the node is
    referenced twice or more and a vehicle that arrives there still cannot
    leave. Measured on the Murdoch extract, that blind spot hid 2 approaches
    that were silent vehicle traps — present in no report, and findable only by
    running a second tool that re-derived the answer for itself.
    """
    if not _arrives(way, node_id):
        return False
    others = [other for other in ways_at.get(node_id, ()) if other.id != way.id]
    return bool(others) and not any(_departs(other, node_id) for other in others)


def dead_ends(extract: OsmExtract) -> list[DeadEnd]:
    """Every way terminal a vehicle can reach and cannot leave, classified.

    Call this on the extract that :func:`merge_junction_nodes` returned: a
    terminal that the merge turned into a junction is no longer a dead end, and
    running the two in the other order would emit a U-turn at a junction.
    """
    out: list[DeadEnd] = []
    if extract.refused or not extract.ways:
        return out
    bounds = extract.bounds()
    if bounds is None:  # pragma: no cover
        return out
    plane = origin_for_bounds(*bounds)
    xy = {nid: plane.forward(n.lat, n.lon) for nid, n in extract.nodes.items()}
    (minx, maxx, miny, maxy), boundary_label = _boundary_rectangle(extract, xy)

    occurrences: dict[int, int] = {}
    ways_at: dict[int, list[OsmWay]] = {}
    for way in extract.ways:
        for node_id in way.node_ids:
            occurrences[node_id] = occurrences.get(node_id, 0) + 1
        for node_id in set(way.node_ids):
            ways_at.setdefault(node_id, []).append(way)

    banned_u_turns = {
        (r.from_way, r.via_node)
        for r in extract.restrictions
        if r.kind == "no_u_turn" and r.from_way == r.to_way
    }

    for way in sorted(extract.ways, key=lambda w: w.id):
        for node_id in (way.node_ids[0], way.node_ids[-1]):
            if node_id not in xy:
                continue
            # A node THIS GENERATOR synthesized on the requested rectangle is a
            # terminal however many ways end at it. The two tests below ask
            # "can a vehicle arrive here and not leave?", and at a shared
            # synthesized node the answer they compute is "no" — the sibling way
            # counts as an exit — so the node was skipped entirely, produced no
            # DeadEnd, and therefore never reached the ban list
            # ``draft._stranded_approach_connectors`` builds from this function's
            # output. Measured: every movement across such a node is antiparallel
            # (both ways carry the SAME source segment, so the approach and every
            # departure lie on one centreline), the junction pass suppressed them
            # all under §5.3.3, both approaches ended up stranded, and the
            # last-resort pass then emitted a same-way FWD→REV U-turn on EACH of
            # them — a reflecting wall at the study edge, the exact failure the
            # boundary band exists to prevent, and invisible to the gate.
            #
            # The fix belongs here rather than in that pass: this function is the
            # single source of truth for "may a U-turn be emitted at this way
            # terminal?", and a synthesized node is on the rectangle BY
            # CONSTRUCTION, so any way ending there is a road leaving the model
            # and any movement modelled there is fabricated.
            synthesized = node_id in extract.synthesized_node_ids
            # Two shapes of dead end, not one: a terminal nobody else touches,
            # and a terminal several ways share where every one of them is
            # one-way inbound. Both are places a vehicle arrives and cannot
            # leave; only the first was visible before.
            if (
                not synthesized
                and occurrences.get(node_id, 0) != 1
                and not _is_trapped_terminal(way, node_id, ways_at)
            ):
                continue
            x, y = xy[node_id]
            edge = min(x - minx, maxx - x, y - miny, maxy - y)
            arterial = way.highway in ARTERIAL_CLASSES
            if not synthesized and edge < 0.0:
                # STRICTLY outside, and therefore never "at the boundary" — the
                # band test below is a SIGNED distance, so outside the rectangle
                # it is satisfied 100 m out exactly as it is 0.1 m in.
                #
                # Which of the two answers that deserves depends on the RUNG,
                # because only one of them comes with a remedy. On the requested
                # bbox this generator cut every tail at the rectangle, so an
                # outside terminal is a defect in its own geometry and is
                # reported as one. On a declared ``<bounds>`` there is no trim
                # and no remedy: a whole-way extract overhanging the window it
                # declares is the NORMAL shape of such a file, it was read as a
                # (benign) boundary before this unit existed, and turning that
                # into a permanent refusal would refuse files nothing is wrong
                # with. Rung-2 trimming is a deliberate non-goal here and a
                # named follow-up, not an oversight. The hull rung cannot reach
                # this branch at all: it IS the extent of ``xy``, so every
                # ``edge`` there is >= 0 by construction.
                if extract.requested_bbox is not None:
                    out.append(
                        DeadEnd(
                            node_id,
                            way.id,
                            way.highway,
                            DEAD_END_BEYOND_REQUESTED_BBOX,
                            False,
                            warn_arterial=arterial,
                            detail=(
                                f"{-edge:.2f} m OUTSIDE the model boundary "
                                f"({boundary_label}); a terminal beyond the study area is "
                                "not a road leaving the model, it is geometry this "
                                "generator should have cut at the edge — reported as a "
                                "fault, never as a boundary"
                            ),
                        )
                    )
                    continue
                out.append(
                    DeadEnd(
                        node_id,
                        way.id,
                        way.highway,
                        DEAD_END_MODEL_BOUNDARY,
                        False,
                        detail=(
                            f"{-edge:.2f} m outside the {boundary_label} window — the "
                            "file's geometry overhangs the window it declares, which is "
                            "normal for a whole-way extract; the road continues outside "
                            "the model, so a vehicle here leaves it"
                        ),
                    )
                )
                continue
            if synthesized or edge <= BOUNDARY_BAND_M:
                out.append(
                    DeadEnd(
                        node_id,
                        way.id,
                        way.highway,
                        DEAD_END_MODEL_BOUNDARY,
                        False,
                        detail=(
                            f"{max(edge, 0.0):.2f} m from the model boundary "
                            f"({boundary_label}); the road continues outside the model, "
                            "so a vehicle here leaves it"
                            + (
                                " — and this node is one this generator SYNTHESIZED on "
                                "the rectangle, so it is a terminal for every way that "
                                "ends at it and no movement across it is one the source "
                                "describes"
                                if synthesized
                                else ""
                            )
                        ),
                    )
                )
                continue
            if way.is_oneway:
                out.append(
                    DeadEnd(
                        node_id,
                        way.id,
                        way.highway,
                        DEAD_END_ONE_WAY,
                        False,
                        warn_arterial=arterial,
                        detail=(
                            "one-way way ends with nothing to turn into; there is only one "
                            "carriageway, so no U-turn exists to emit. Usually a break in "
                            "the map — reported, not repaired"
                        ),
                    )
                )
                continue
            if (way.id, node_id) in banned_u_turns:
                out.append(
                    DeadEnd(
                        way_id=way.id,
                        node_id=node_id,
                        highway=way.highway,
                        reason=DEAD_END_U_TURN_RESTRICTED,
                        emit_u_turn=False,
                        detail="an OSM type=restriction relation bans the U-turn at this node",
                    )
                )
                continue
            non_drivable = node_id in extract.non_drivable_node_ids
            out.append(
                DeadEnd(
                    node_id,
                    way.id,
                    way.highway,
                    DEAD_END_U_TURN_NON_DRIVABLE_CONTINUATION
                    if non_drivable
                    else DEAD_END_U_TURN,
                    True,
                    warn_arterial=arterial,
                    detail=(
                        "the road continues only as a way this generator does not model "
                        "(footway, path, track), so a motor vehicle must still turn around"
                        if non_drivable
                        else "a vehicle that drives in must be able to drive out"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Turn restrictions
# ---------------------------------------------------------------------------


def suppressed_by_restriction(
    restrictions: list[TurnRestriction], via_node: int, from_way: int, to_way: int
) -> TurnRestriction | None:
    """The restriction that bans ``from_way → to_way`` at ``via_node``, if any.

    Both OSM families are honoured, and both only ever REMOVE a movement:

    * ``no_*`` — the named movement is banned.
    * ``only_*`` — everything except the named movement is banned.

    A relation this module could not read is never silently treated as absent:
    :attr:`OsmExtract.restriction_refusals` names it, and the §5 report counts
    them.
    """
    for restriction in restrictions:
        if restriction.via_node != via_node or restriction.from_way != from_way:
            continue
        if restriction.kind.startswith("no_") and restriction.to_way == to_way:
            return restriction
        if restriction.kind.startswith("only_") and restriction.to_way != to_way:
            return restriction
    return None


# ---------------------------------------------------------------------------
# Isolated zones — reachability, which a census of link ends cannot see
# ---------------------------------------------------------------------------

#: The pocket's only tie to the rest of the map is a way this generator does not
#: model — a footway, a path, a cycleway, a road still under construction. The
#: corridor exists for a pedestrian and does not exist for a car, so no vehicle
#: can reach these streets and none can leave them.
ZONE_NON_DRIVABLE_ACCESS = "access_only_via_ways_this_generator_does_not_model"
#: No way of ANY class in the source file joins this pocket to the network. The
#: severance is in the base map, and nothing this generator can read repairs it.
ZONE_SEVERED_IN_SOURCE = "severed_in_the_source_extract"
#: The pocket reaches the model's own edge, so its access road is outside the
#: window the extract was cut from. Not a defect in the map — a consequence of
#: where the study area was drawn.
ZONE_TRUNCATED_AT_BOUNDARY = "truncated_at_the_model_boundary"
#: A drivable way this generator DID model shares a node with the pocket, and the
#: pocket is still unreachable. That is not a severance in the source: the source
#: says these roads meet. Something in THIS generator dropped the movement, and
#: the honest response is to say so and leave the roads in the network, not to
#: delete evidence of our own bug. The reason exists to make that case
#: impossible to confuse with the three above; on the Murdoch extract it fires
#: zero times, which is what makes the other three safe to act on.
ZONE_SEVERED_BY_THIS_GENERATOR = "connected_in_the_source_but_not_in_this_network"

#: The reasons whose zones :func:`services.genesis.draft.build_draft` may
#: remove. Every one of them is a statement about the SOURCE. The fourth —
#: :data:`ZONE_SEVERED_BY_THIS_GENERATOR` — is deliberately absent.
EXCLUDABLE_ZONE_REASONS = frozenset(
    {ZONE_NON_DRIVABLE_ACCESS, ZONE_SEVERED_IN_SOURCE, ZONE_TRUNCATED_AT_BOUNDARY}
)


@dataclass(frozen=True)
class IsolatedZone:
    """One pocket of the emitted network nothing can enter and nothing can leave.

    **Why this is not the dangling-end question.** ``dead_ends`` and
    ``_dangling_approaches`` ask *"does a connector leave this link end?"*.
    Every link of every zone below answers yes: the pocket's streets connect to
    each other perfectly. The question that finds them is *"can a vehicle
    entering the model ever get here, and can one here ever get out?"*, and on
    the 2026-08-10 Murdoch generation the two questions disagreed by 150 links —
    a residential pocket and seven smaller ones, all scored healthy by a census
    of link ends, none of them reachable.

    **Why the answer is exclusion rather than a connector.** Every cause below
    is a statement about the SOURCE, not about this generator: the access road
    is a footway, or is not in the file at all, or left through the extract's
    edge. Adding a connector would invent a road, which is the one thing this
    package refuses to do (2026-08-10: two roads were invented exactly that way
    and shipped). Keeping the pocket would be worse than useless — it inflates
    every link, node and connector count, appears in every rendering, and
    simulates nothing, because no vehicle can be in it. So it is removed, and
    named: ``reason`` says why it is unreachable, ``detail`` says what the file
    shows there.
    """

    reason: str
    detail: str
    link_ids: tuple[int, ...]
    way_ids: tuple[int, ...]
    #: The lowest-id link's name — enough for a human to find the pocket on a map.
    representative: str
    #: Every distinct street name in the pocket, sorted. A pocket is identified
    #: by its streets, not by an id a reader cannot look up.
    street_names: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "links": len(self.link_ids),
            "link_ids": list(self.link_ids),
            "ways": len(self.way_ids),
            "way_ids": list(self.way_ids),
            "representative": self.representative,
            "street_names": list(self.street_names),
        }


def isolated_zones(links, connectors) -> list[tuple[int, ...]]:
    """Link-id groups nothing can enter and nothing can leave, largest first.

    Duck-typed on purpose — ``links`` need only carry ``id`` and ``connectors``
    only ``from_link``/``to_link`` — so this runs on the draft before anything is
    emitted and cannot drift from what ``build_draft`` actually built.

    The definition, stated so it can be compared against the R1.5 gate's V3
    (which asks the same question of the finished ``.inpx`` and must agree):

    * a **network entry** is a link no connector arrives at, a **network exit**
      a link no connector leaves — those are the places traffic enters and
      leaves the model;
    * a link is *unreachable* when no forward path from any entry reaches it,
      and *trapped* when no forward path from it reaches any exit;
    * a **zone** is a weakly-connected group of links every one of which is both.

    Both halves are required. A pocket that can be entered but not left is a
    trap, and a pocket that can be left but not entered is an orphan source;
    both are real faults, both are named elsewhere by the dangling census, and
    NEITHER is excluded here — a link a vehicle can reach is a link that
    simulates something. Only the pocket that fails both tests is inert.

    A network with NO entry and NO exit anywhere — every arm closed by a
    cul-de-sac U-turn — makes "unreachable from any entry" vacuously true of
    every link, so this function reports the WHOLE network as one zone. That is
    the honest answer to the question asked (nothing can be in any of it), and
    it is deliberately still reported: what must not happen is *removing* it,
    and that decision belongs to :func:`services.genesis.draft.build_draft`,
    which refuses to let the isolation pass empty a network. Measured
    2026-08-12: without that refusal a synthetic crossroads whose four arms end
    inside the requested study rectangle had all 8 of its links excluded as one
    pocket and the generation produced no network at all.
    """
    link_ids = [link.id for link in links]
    out_edges: dict[int, list[int]] = {lid: [] for lid in link_ids}
    in_edges: dict[int, list[int]] = {lid: [] for lid in link_ids}
    for connector in connectors:
        if connector.from_link in out_edges and connector.to_link in in_edges:
            out_edges[connector.from_link].append(connector.to_link)
            in_edges[connector.to_link].append(connector.from_link)

    entries = [lid for lid in link_ids if not in_edges[lid]]
    exits = [lid for lid in link_ids if not out_edges[lid]]
    reachable = _reachable_from(entries, out_edges)
    reaches_exit = _reachable_from(exits, in_edges)
    inert = {lid for lid in link_ids if lid not in reachable and lid not in reaches_exit}

    seen: set[int] = set()
    zones: list[tuple[int, ...]] = []
    for start in sorted(inert):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        members: list[int] = []
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbour in out_edges[current] + in_edges[current]:
                if neighbour in inert and neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        zones.append(tuple(sorted(members)))
    # Largest first, ties broken by lowest link id: a reader reads the biggest
    # finding first, and the order is a function of the data alone.
    zones.sort(key=lambda z: (-len(z), z[0]))
    return zones


def _reachable_from(seeds: list[int], edges: dict[int, list[int]]) -> set[int]:
    seen = set(seeds)
    stack = list(seeds)
    while stack:
        current = stack.pop()
        for neighbour in edges.get(current, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return seen


def diagnose_isolated_zones(links, connectors, extract: OsmExtract) -> list[IsolatedZone]:
    """:func:`isolated_zones`, with the cause of each read off the SOURCE extract.

    ``extract`` must be the extract as parsed — *before*
    :func:`merge_junction_nodes`, which rewrites node ids into cluster
    representatives. The evidence this function needs (which nodes a footway
    touches, where the model's edge is) is all stated in the original ids, and
    looking it up in merged ids would silently miss every zone whose contact
    node happened to be merged.

    Cause precedence, most specific first, because a zone can satisfy more than
    one and only the most specific tells a reader what to DO:

    1. **a way we DID model shares a node with it** — then the source says these
       roads meet and this generator lost the movement. Ranked first because it
       is the only cause that indicts us, and a pass that could delete its own
       bugs would be worse than no pass at all;
    2. **at the model boundary** — the access road left the extract window;
       nothing is wrong with the map, the study area is simply drawn here;
    3. **a non-drivable way touches it** — the corridor is in the file, as a
       footway/path/cycleway/construction; the map may be right (a pedestrian
       -only pocket) or missing a road, and the classes are named so a reviewer
       can tell which;
    4. **nothing touches it at all** — the severance is unqualified.
    """
    zones = isolated_zones(links, connectors)
    if not zones:
        return []

    by_id = {link.id: link for link in links}
    ways = {way.id: way for way in extract.ways}
    boundary = None
    bounds = extract.bounds()
    if bounds is not None and extract.nodes:
        plane = origin_for_bounds(*bounds)
        xy = {nid: plane.forward(node.lat, node.lon) for nid, node in extract.nodes.items()}
        boundary = (_boundary_rectangle(extract, xy)[0], xy)

    out: list[IsolatedZone] = []
    for member_ids in zones:
        members = [by_id[lid] for lid in member_ids]
        way_ids = tuple(sorted({link.osm_way_id for link in members}))
        node_ids = sorted(
            {nid for wid in way_ids for nid in ways[wid].node_ids if wid in ways}
        )
        names = tuple(
            sorted({_street_name(link.name) for link in members} - {""})
        )
        reason, detail = _zone_cause(extract, ways, set(way_ids), node_ids, boundary)
        out.append(
            IsolatedZone(
                reason=reason,
                detail=detail,
                link_ids=member_ids,
                way_ids=way_ids,
                representative=min(members, key=lambda l: l.id).name,
                street_names=names,
            )
        )
    return out


def _street_name(link_name: str) -> str:
    """``"Calder Way [FWD] w123s0"`` → ``"Calder Way"``; unnamed roads → ``""``.

    The generator names an unnamed way after its highway class, so a pocket of
    car-park aisles would otherwise be reported as being on a street called
    "service". Those are dropped rather than repeated: a list of class names is
    not a list of streets.
    """
    head = link_name.split(" [")[0].strip()
    return "" if head in DRIVABLE_HIGHWAY_DEFAULT_LANES else head


def _zone_cause(
    extract: OsmExtract,
    ways: dict[int, OsmWay],
    zone_way_ids: set[int],
    node_ids: list[int],
    boundary,
) -> tuple[str, str]:
    inside = set(node_ids)
    touching = sorted(
        {
            way.id
            for way in extract.ways
            if way.id not in zone_way_ids and inside.intersection(way.node_ids)
        }
    )
    if touching:
        named = ", ".join(f"way {wid} ({ways[wid].highway})" for wid in touching[:6])
        return (
            ZONE_SEVERED_BY_THIS_GENERATOR,
            "the source file shares a node between this pocket and "
            f"{len(touching)} way(s) this generator DID model ({named}), so the map "
            "says these roads meet — and the network still cannot reach the pocket, "
            "because every movement between them was suppressed (a turn restriction, "
            "an antiparallel classification, or a defect in this generator). NOT "
            "excluded: these are roads the source connects, and deleting them would "
            "delete the evidence",
        )
    if boundary is not None:
        (minx, maxx, miny, maxy), xy = boundary
        for node_id in node_ids:
            point = xy.get(node_id)
            if point is None:
                continue
            x, y = point
            if (
                abs(x - minx) <= BOUNDARY_BAND_M
                or abs(x - maxx) <= BOUNDARY_BAND_M
                or abs(y - miny) <= BOUNDARY_BAND_M
                or abs(y - maxy) <= BOUNDARY_BAND_M
            ):
                return (
                    ZONE_TRUNCATED_AT_BOUNDARY,
                    "the pocket reaches the model's own edge, so whatever connects it "
                    "to the network is outside the window this extract was cut from",
                )
    classes = extract.non_drivable_classes_at(node_ids)
    if classes:
        return (
            ZONE_NON_DRIVABLE_ACCESS,
            "no way this generator models touches this pocket at all; what the source "
            f"file does show joining it to anywhere is {', '.join(f'highway={c}' for c in classes)}"
            " — a corridor for a pedestrian and not a carriageway, so no motor vehicle "
            "can enter or leave",
        )
    return (
        ZONE_SEVERED_IN_SOURCE,
        "the source file shows nothing joining this pocket to anywhere — no modelled "
        "carriageway, and not so much as a footway: the severance is in the base map, "
        "not in this generator",
    )


__all__ = [
    "ARTERIAL_CLASSES",
    "BENIGN_DANGLING_CAUSES",
    "BOUNDARY_BAND_M",
    "BOUNDARY_SOURCE_DECLARED_BOUNDS",
    "BOUNDARY_SOURCE_HULL",
    "BOUNDARY_SOURCE_REQUESTED_BBOX",
    "DANGLING_ALL_MOVEMENTS_U",
    "DANGLING_CUL_DE_SAC",
    "DANGLING_MODEL_BOUNDARY",
    "DANGLING_NO_EXIT_AT_JUNCTION",
    "DANGLING_ONE_WAY",
    "DANGLING_RESTRICTED",
    "DANGLING_UNATTRIBUTED",
    "DANGLING_UNMERGED_NEIGHBOUR",
    "DANGLING_BEYOND_MODEL_BOUNDARY",
    "DEAD_END_REASON_TO_DANGLING_CAUSE",
    "DEAD_END_BEYOND_REQUESTED_BBOX",
    "DEAD_END_MODEL_BOUNDARY",
    "DEAD_END_ONE_WAY",
    "DEAD_END_U_TURN",
    "DEAD_END_U_TURN_NON_DRIVABLE_CONTINUATION",
    "DEAD_END_U_TURN_RESTRICTED",
    "DeadEnd",
    "EXCLUDABLE_ZONE_REASONS",
    "IsolatedZone",
    "MAX_CLUSTER_DIAMETER_M",
    "MergeRefusal",
    "MergeReport",
    "NARROWEST_CARRIAGEWAY_M",
    "NODE_MERGE_TOLERANCE_M",
    "NodeMerge",
    "REFUSE_CLUSTER_TOO_WIDE",
    "REFUSE_GRADE_SEPARATED",
    "REFUSE_NO_JUNCTION_TO_RECONCILE",
    "REFUSE_SYNTHESIZED_BOUNDARY_NODE",
    "REFUSE_WOULD_COLLAPSE_WAY",
    "TOLERANCE_BASIS",
    "ZONE_NON_DRIVABLE_ACCESS",
    "ZONE_SEVERED_BY_THIS_GENERATOR",
    "ZONE_SEVERED_IN_SOURCE",
    "ZONE_TRUNCATED_AT_BOUNDARY",
    "boundary_source",
    "dead_ends",
    "diagnose_isolated_zones",
    "isolated_zones",
    "merge_junction_nodes",
    "suppressed_by_restriction",
]

# ``DRIVABLE_HIGHWAY_DEFAULT_LANES`` is imported for the docstring's claim about
# the narrowest modelled carriageway; this assertion makes the claim mechanical.
assert min(DRIVABLE_HIGHWAY_DEFAULT_LANES.values()) == 1

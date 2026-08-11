"""Which modelled junction is this signal? — the SITE → Vissim NODE join.

Roadmap "real observed counts" unit, 2026-08. ``services/observations.py``'s
:func:`~services.observations.observed_side` takes a ``site_nodes`` map from the
CALLER and refuses without one, on the stated grounds that survey site ids are
not Vissim node ids and guessing the join "by position, by string similarity, by
ordering" would produce a confidently wrong comparison. That refusal is right.
This module is the ONE way to satisfy it without a human typing the pairs: it
derives the map **from coordinates both sides actually state**, and refuses by
name wherever they do not decide the answer.

What this is NOT
----------------
This is **not** the detector → movement map. That one needs the MRWA Appendix-A
LMA/LMB site drawing, which is not in this drop
(``adapters.scats_wa.build_turning_counts_from_detector_map`` measured it and
names the three files it ruled out), so movement-level lift stays refused. A
site → node map answers a strictly easier question — *which junction* — and it
therefore authorises exactly one kind of comparison:
:data:`COMPARISON_LEVEL`, a **site total against a node total**. Nothing here
makes a movement-level number available, and every model in this module carries
that label so a consumer cannot mistake one for the other.

The two frames and the bridge
-----------------------------
A SCATS ``Site`` tab states plain decimal-degree latitude/longitude. A Vissim
network states local metres with no EPSG and no projection metadata. They are
joined the way :mod:`services.genesis.benchmark` already joins the consultant
network to a generated draft — through this project's only georeference:

    Vissim local  --[ routers.geojson Helmert fit + Web-Mercator inverse ]-->
    WGS84         --[ project.LocalTangentPlane.forward about a stated origin ]-->
    common local metre frame  <-- the site's lat/lon lands here too

so the comparison is a plain Euclidean distance in metres, and the bridge's own
error is the first term of the tolerance below. ``helmert_bridge`` is reused
rather than reimplemented: it already turns a missing ``refPointMap`` /
``refPointNet`` anchor, or an unimportable ``routers.geojson``, into a REFUSE
finding instead of a zero-offset guess.

The tolerance, derived
----------------------
:data:`SITE_NODE_TOLERANCE_M` = 29.0 m, the sum of three terms, each a number
this repository already owns. Same construction discipline as
``benchmark.MATCH_TOLERANCE_M`` (22 m), a different third term because this is a
different question — a point against a junction centre, not a polyline against a
polyline:

========================== ======== ========================================
term                         metres  provenance
========================== ======== ========================================
georeference residual          14.0  ``diff.GEOREF_MAX_RESIDUAL_M`` —
                                     ``routers/geojson.py``'s own comment: the
                                     Helmert fit (2 offsets, 1 rotation, 2
                                     scales) to 9 surveyed intersection
                                     coordinates has a max residual of ~14 m.
                                     This is the FLOOR: the bridge itself moves
                                     points by up to this much, so no tolerance
                                     below it can be honest.
projection budget               1.0  ``project.MAX_PLANIMETRIC_ERROR_M`` — the
                                     budget the tangent plane's validity radius
                                     is solved for. The constant is used rather
                                     than any per-network measurement so the
                                     tolerance cannot silently shrink.
junction-centre allowance      14.0  2 x ``connectivity.NODE_MERGE_TOLERANCE_M``
                                     (itself 2 x ``NOMINAL_LANE_WIDTH_M``, the
                                     only lane width MRWA §5 prints, Table 5-7).
                                     SCATS records ONE coordinate for a signal
                                     site; a Vissim node centre is derived from
                                     where the member links cross the junction
                                     (``inpx_parser._node_center_from_link_segments``).
                                     These are not defined to be the same point,
                                     and each may sit up to one carriageway from
                                     the junction centre — on opposite sides in
                                     the worst case. An **allowance, not a
                                     measurement**, and named as such.
========================== ======== ========================================

Refusal-first, in three shapes
------------------------------
1. **No candidate.** The nearest node is beyond the tolerance -> the site is
   named in :attr:`SiteNodeMapping.refusals` with the distance and the node it
   was nearest to. Never "the closest one wins anyway".
2. **Ambiguous.** Two or more nodes are within the tolerance -> every one of
   them is named and NONE is picked. Forcing a pick here is precisely the
   silently-wrong join ``observed_side`` refuses to make.
3. **Conflict.** Two sites resolve to the same node -> both are refused and
   named. ``observed_side`` also catches this downstream, but a map that
   *contains* the conflict has already lost the information about which site
   was meant; catching it here keeps both site ids.

A site with no stated position is refused before any arithmetic
(``site_position_absent``), and a network whose nodes have no derivable centre
is excluded and counted rather than being treated as sitting at the origin —
``inpx_parser._node_center_from_link_segments`` returns ``(0, 0)`` when a node
declares no resolvable ``linkSegment``, and a node at the local origin would
otherwise be a magnet for every distant site.

Every match is REPORTED, never silent: the distance, the runner-up and the
runner-up's distance ride on :class:`SiteNodeMatch`, and a match whose spare
budget is smaller than the georeference residual is flagged
:attr:`~SiteNodeMatch.thin_margin` — inside the tolerance, but resting on the
separation to the runner-up rather than on the absolute distance. That is a
property of a 14 m georeference, not of the site, and it is the first thing that
would improve if the network were ever surveyed.

No COM, no Vissim, no network access. Reads one ``.inpx`` off disk.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from services.genesis.connectivity import NODE_MERGE_TOLERANCE_M
from services.genesis.diff import GEOREF_MAX_RESIDUAL_M, NOMINAL_LANE_WIDTH_M
from services.genesis.project import MAX_PLANIMETRIC_ERROR_M, LocalTangentPlane

# ---------------------------------------------------------------------------
# The tolerance. Derived in the module docstring; not a literal that looked
# about right.
# ---------------------------------------------------------------------------

#: Term 1 — the georeference floor. ``routers/geojson.py``'s Helmert fit.
GEOREF_RESIDUAL_M = GEOREF_MAX_RESIDUAL_M

#: Term 2 — the tangent plane's planimetric budget.
PROJECTION_BUDGET_M = MAX_PLANIMETRIC_ERROR_M

#: Term 3 — an allowance, not a measurement: the SCATS site coordinate and the
#: model's derived node centre may each sit one carriageway off the junction
#: centre, on opposite sides.
JUNCTION_CENTRE_ALLOWANCE_M = 2.0 * NODE_MERGE_TOLERANCE_M

#: 14.0 + 1.0 + 14.0 = 29.0 m.
SITE_NODE_TOLERANCE_M = GEOREF_RESIDUAL_M + PROJECTION_BUDGET_M + JUNCTION_CENTRE_ALLOWANCE_M

TOLERANCE_BASIS = (
    f"{GEOREF_RESIDUAL_M:g} m georeference residual (backend/routers/geojson.py: Helmert fit to "
    f"9 surveyed intersection coordinates, max residual ~{GEOREF_RESIDUAL_M:g} m; mirrored as "
    f"services.genesis.diff.GEOREF_MAX_RESIDUAL_M) + {PROJECTION_BUDGET_M:g} m tangent-plane "
    f"planimetric budget (services.genesis.project.MAX_PLANIMETRIC_ERROR_M) + "
    f"{JUNCTION_CENTRE_ALLOWANCE_M:g} m junction-centre allowance "
    f"(2 x services.genesis.connectivity.NODE_MERGE_TOLERANCE_M = 4 x "
    f"{NOMINAL_LANE_WIDTH_M:g} m nominal lane width, MRWA §5 Table 5-7; an allowance for "
    f"'SCATS states one point for the site, the model derives a node centre from its member "
    f"links', not a measurement) = {SITE_NODE_TOLERANCE_M:g} m."
)

#: What a map built here authorises, and nothing more. Carried on every model in
#: this module so a consumer never has to infer it.
COMPARISON_LEVEL = "site_total_vs_node_total"

COMPARISON_LEVEL_NOTE = (
    "A site -> node map answers 'which modelled junction is this signal'. It authorises a "
    "SITE-TOTAL against a NODE-TOTAL comparison and nothing finer: a SCATS detector number is "
    "local to its site and carries no turning movement, so no movement-level number becomes "
    "available from this map. Movement level needs the MRWA Appendix-A LMA/LMB site drawing, "
    "which adapters.scats_wa.build_turning_counts_from_detector_map measured to be absent from "
    "this drop and therefore refuses."
)


# ---------------------------------------------------------------------------
# Refusal codes
# ---------------------------------------------------------------------------

SITE_POSITION_ABSENT = "site_position_absent"
SITE_NODE_NO_CANDIDATE = "site_node_no_candidate_within_tolerance"
SITE_NODE_AMBIGUOUS = "site_node_ambiguous"
SITE_NODE_CONFLICT = "site_node_conflict"
NETWORK_UNREADABLE = "site_node_network_unreadable"
GEOREFERENCE_UNAVAILABLE = "site_node_georeference_unavailable"
NO_NODES_WITH_POSITION = "site_node_network_has_no_positioned_nodes"


# ---------------------------------------------------------------------------
# Inputs and results
# ---------------------------------------------------------------------------


class SitePosition(BaseModel):
    """One observation site's own stated coordinate.

    Decimal degrees. Built from a neutral :class:`~services.dataimport.schema.SiteMetadata`
    by :func:`site_positions_from_metadata`, which is the only place that knows
    the schema's ``position.x = longitude, position.y = latitude`` convention.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: str = Field(min_length=1)
    site_name: str | None = None
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)


class SiteNodeMatch(BaseModel):
    """One resolved site -> node pair, with the evidence that resolved it."""

    model_config = ConfigDict(extra="forbid")

    site_id: str
    site_name: str | None = None
    node_id: str
    node_name: str | None = None
    distance_m: float
    #: The next-nearest node and its distance — always reported, so "why this
    #: node and not another" is answerable without re-running anything.
    runner_up_node_id: str | None = None
    runner_up_distance_m: float | None = None
    #: Tolerance minus distance. How much budget the match did NOT need.
    margin_m: float
    #: ``margin_m`` is smaller than the georeference residual: the match is
    #: inside the tolerance, but its absolute distance is within the bridge's
    #: own error of the boundary, so it rests on the runner-up separation.
    thin_margin: bool
    #: How many times further away the runner-up is. ``None`` when this network
    #: has only one positioned node.
    separation_ratio: float | None = None
    comparison_level: str = COMPARISON_LEVEL


class SiteNodeRefusal(BaseModel):
    """A site (or the whole map) that could not be resolved, and why."""

    model_config = ConfigDict(extra="forbid")

    code: str
    site_id: str | None = None
    message: str
    messages: dict[str, str] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class SiteNodeMapping(BaseModel):
    """The map, its refusals, and the numbers both were decided by."""

    model_config = ConfigDict(extra="forbid")

    network: str
    tolerance_m: float = SITE_NODE_TOLERANCE_M
    tolerance_basis: str = TOLERANCE_BASIS
    comparison_level: str = COMPARISON_LEVEL
    comparison_level_note: str = COMPARISON_LEVEL_NOTE
    origin_lat_deg: float | None = None
    origin_lon_deg: float | None = None
    matched: list[SiteNodeMatch] = Field(default_factory=list)
    refusals: list[SiteNodeRefusal] = Field(default_factory=list)
    #: Every node the network offered a usable centre for.
    nodes_considered: list[str] = Field(default_factory=list)
    #: Nodes the network declares but whose centre could not be derived — named,
    #: never silently treated as sitting at the local origin.
    nodes_without_position: list[str] = Field(default_factory=list)
    #: The closest two positioned nodes in this network. The evidence that the
    #: tolerance cannot reach two junctions at once: when this exceeds twice the
    #: tolerance, no site can be ambiguous by construction.
    min_node_separation_m: float | None = None

    @property
    def site_nodes(self) -> dict[str, str]:
        """``{site_id: node_id}`` — the shape ``observations.observed_side`` and
        ``run_reality_check``'s ``site_nodes`` parameter take. Refused sites are
        simply absent, which is what makes them show up in that layer's own
        ``sites_without_node_mapping`` coverage rather than vanishing."""
        return {match.site_id: match.node_id for match in self.matched}

    @property
    def tolerance_cannot_reach_two_nodes(self) -> bool:
        """True when twice the tolerance is below the closest node pair — i.e.
        an ambiguous match is arithmetically impossible in this network, not
        merely absent from this run's results."""
        if self.min_node_separation_m is None:
            return False
        return 2.0 * self.tolerance_m < self.min_node_separation_m

    def refusal_codes(self) -> list[str]:
        return sorted({refusal.code for refusal in self.refusals})


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def site_positions_from_metadata(sites: Iterable[Any]) -> tuple[list[SitePosition], list[SiteNodeRefusal]]:
    """Neutral :class:`SiteMetadata` records -> :class:`SitePosition`.

    The ONE place that knows the schema's convention (``position.x`` is
    longitude, ``position.y`` is latitude — stated on ``SiteMetadata.position``
    and in the SCATS field map). A site whose ``position`` is absent, or which
    carries only half a pair, is refused by name rather than dropped: "this
    site has no coordinate" and "this site was not in the drop" are different
    facts and a caller must be able to tell them apart.
    """
    positions: list[SitePosition] = []
    refusals: list[SiteNodeRefusal] = []
    for site in sites:
        site_id = str(getattr(site, "site_id", "") or "")
        position = getattr(site, "position", None)
        lon = getattr(position, "x", None) if position is not None else None
        lat = getattr(position, "y", None) if position is not None else None
        if lat is None or lon is None:
            refusals.append(
                SiteNodeRefusal(
                    code=SITE_POSITION_ABSENT,
                    site_id=site_id or None,
                    message=(
                        f"site {site_id!r} states no latitude/longitude pair, so there is nothing "
                        "to compare against a node position. Refused rather than matched by name "
                        "or by ordering."
                    ),
                    messages={
                        "ko": f"'{site_id}' 지점에 위·경도가 없어 노드와 대조할 좌표가 없습니다. "
                        "이름이나 순서로 짝짓지 않고 거부합니다.",
                        "en": f"Site {site_id!r} states no latitude/longitude, so it cannot be "
                        "placed against a node. Refused rather than matched by name or order.",
                        "ja": f"'{site_id}' 地点に緯度・経度がなく、ノードと照合できません。"
                        "名前や順序で対応付けはせず拒否します。",
                    },
                    context={"has_position": position is not None},
                )
            )
            continue
        positions.append(
            SitePosition(
                site_id=site_id,
                site_name=getattr(site, "site_name", None),
                lat=float(lat),
                lon=float(lon),
            )
        )
    return positions, refusals


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


def _node_centres_in_plane(
    inpx_path: Path, plane: LocalTangentPlane
) -> tuple[dict[str, tuple[tuple[float, float], str | None]], list[str], list[SiteNodeRefusal]]:
    """``{node_id: ((x, y) in plane metres, node name)}`` for one ``.inpx``.

    Node centres come from :func:`services.inpx_parser.parse_network`, which
    derives a centre from the node's polygon when it has one and from the points
    where its member links cross the junction when it does not — the real
    consultant model is the second case for every node (it carries ``linkSegs``
    and no polygon at all; see ``benchmark.node_membership_findings``). A node
    that yields NEITHER also comes back at ``(0, 0)``, because that is the
    parser's "nothing to average" return — so ``(0, 0)`` is read as *unplaced*
    only when the node has no polygon either. A polygon centroid that lands on
    the local origin is a real position and is kept: reading the sentinel alone
    would silently drop a junction that happens to sit on the network origin,
    and the origin would then also become a magnet for every distant site.

    Residual, stated: a node with no polygon whose linkSegment touch points
    average to exactly ``(0.0, 0.0)`` is indistinguishable from an unplaced one
    here. It is excluded and named, which is the safe direction.
    """
    from services.genesis.benchmark import helmert_bridge  # lazy: pulls routers.geojson
    from services.inpx_parser import parse_network

    try:
        network = parse_network(str(inpx_path))
    except Exception as exc:  # noqa: BLE001 - any unreadable network is one refusal
        return (
            {},
            [],
            [
                SiteNodeRefusal(
                    code=NETWORK_UNREADABLE,
                    message=f"could not read the network at {inpx_path}: {exc}",
                    context={"network": str(inpx_path), "error": str(exc)},
                )
            ],
        )

    bridge, findings = helmert_bridge(inpx_path, plane, label=str(inpx_path))
    if bridge is None:
        return (
            {},
            [],
            [
                SiteNodeRefusal(
                    code=GEOREFERENCE_UNAVAILABLE,
                    message=(
                        "the network cannot be placed in a common frame with the sites: "
                        + "; ".join(finding.message for finding in findings)
                    ),
                    context={
                        "network": str(inpx_path),
                        "finding_codes": [finding.code for finding in findings],
                    },
                )
            ],
        )

    centres: dict[str, tuple[tuple[float, float], str | None]] = {}
    unpositioned: list[str] = []
    for node in network.get("nodes") or []:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        x = float(node.get("x") or 0.0)
        y = float(node.get("y") or 0.0)
        if x == 0.0 and y == 0.0 and not (node.get("polygon") or []):
            unpositioned.append(node_id)
            continue
        centres[node_id] = (bridge((x, y)), (node.get("name") or None))
    return centres, sorted(unpositioned), []


def _min_pairwise_separation(points: Sequence[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    best = math.inf
    for index, first in enumerate(points):
        for second in points[index + 1 :]:
            best = min(best, math.dist(first, second))
    return best


def map_sites_to_nodes(
    inpx_path: str | Path,
    sites: Sequence[SitePosition],
    *,
    tolerance_m: float = SITE_NODE_TOLERANCE_M,
    origin: tuple[float, float] | None = None,
) -> SiteNodeMapping:
    """Resolve each site to at most one Vissim node, or refuse it by name.

    ``origin`` is the tangent plane's ``(lat, lon)``. Defaulting it to the mean
    of the supplied site coordinates keeps every distance well inside the
    plane's validity radius without asking a caller to invent an origin; pass
    one explicitly to reproduce a map across different site sets.

    A site is ``matched`` only when exactly ONE positioned node lies within
    ``tolerance_m``. Zero -> :data:`SITE_NODE_NO_CANDIDATE`. Two or more ->
    :data:`SITE_NODE_AMBIGUOUS`, every candidate named and none chosen. Two
    sites landing on one node -> :data:`SITE_NODE_CONFLICT` for both.
    """
    path = Path(inpx_path)
    if origin is not None:
        lat0, lon0 = origin
    elif sites:
        lat0 = sum(site.lat for site in sites) / len(sites)
        lon0 = sum(site.lon for site in sites) / len(sites)
    else:
        lat0 = lon0 = 0.0
    plane = LocalTangentPlane(lat0_deg=lat0, lon0_deg=lon0)

    centres, unpositioned, refusals = _node_centres_in_plane(path, plane)
    mapping = SiteNodeMapping(
        network=str(path),
        tolerance_m=tolerance_m,
        origin_lat_deg=lat0,
        origin_lon_deg=lon0,
        nodes_considered=sorted(centres),
        nodes_without_position=unpositioned,
        refusals=list(refusals),
    )
    if refusals:
        return mapping
    if not centres:
        mapping.refusals.append(
            SiteNodeRefusal(
                code=NO_NODES_WITH_POSITION,
                message=(
                    f"{path.name} declares {len(unpositioned)} node(s) but no derivable centre for "
                    "any of them, so there is nothing to match a site against. A node whose centre "
                    "cannot be derived is not placed at the local origin."
                ),
                context={"nodes_without_position": unpositioned},
            )
        )
        return mapping

    mapping.min_node_separation_m = _min_pairwise_separation(
        [point for point, _name in centres.values()]
    )

    provisional: dict[str, SiteNodeMatch] = {}
    for site in sites:
        site_xy = plane.forward(site.lat, site.lon)
        ranked = sorted(
            (
                (math.dist(site_xy, point), node_id, name)
                for node_id, (point, name) in centres.items()
            ),
            key=lambda row: (row[0], row[1]),
        )
        within = [row for row in ranked if row[0] <= tolerance_m]

        if not within:
            nearest_distance, nearest_id, nearest_name = ranked[0]
            mapping.refusals.append(
                SiteNodeRefusal(
                    code=SITE_NODE_NO_CANDIDATE,
                    site_id=site.site_id,
                    message=(
                        f"site {site.site_id!r} has no modelled junction within {tolerance_m:g} m: "
                        f"the nearest node is {nearest_id} at {nearest_distance:.1f} m. The "
                        "nearest node does not win by default — either this signal is outside the "
                        "modelled area or the georeference does not hold here."
                    ),
                    messages={
                        "ko": f"'{site.site_id}' 지점 {tolerance_m:g} m 이내에 모델 교차로가 없습니다"
                        f"(가장 가까운 노드 {nearest_id}, {nearest_distance:.1f} m). 가장 가깝다는 "
                        "이유만으로 짝짓지 않습니다.",
                        "en": f"Site {site.site_id!r} has no modelled junction within "
                        f"{tolerance_m:g} m (nearest node {nearest_id} at "
                        f"{nearest_distance:.1f} m). Nearest does not win by default.",
                        "ja": f"'{site.site_id}' から {tolerance_m:g} m 以内にモデル交差点がありません"
                        f"(最近傍ノード {nearest_id}、{nearest_distance:.1f} m)。最近傍という理由"
                        "だけでは対応付けません。",
                    },
                    context={
                        "tolerance_m": tolerance_m,
                        "tolerance_basis": mapping.tolerance_basis,
                        "nearest_node_id": nearest_id,
                        "nearest_node_name": nearest_name,
                        "nearest_distance_m": round(nearest_distance, 2),
                    },
                )
            )
            continue

        if len(within) > 1:
            mapping.refusals.append(
                SiteNodeRefusal(
                    code=SITE_NODE_AMBIGUOUS,
                    site_id=site.site_id,
                    message=(
                        f"site {site.site_id!r} is within {tolerance_m:g} m of "
                        f"{len(within)} modelled junctions ("
                        + ", ".join(f"node {node_id} at {dist:.1f} m" for dist, node_id, _n in within)
                        + "). Every candidate is named and none is chosen — picking the closest "
                        "here would be the guessed join this map exists to avoid."
                    ),
                    messages={
                        "ko": f"'{site.site_id}' 지점이 {tolerance_m:g} m 이내에서 모델 교차로 "
                        f"{len(within)}곳과 겹칩니다. 후보를 모두 밝히고 어느 쪽도 고르지 않습니다.",
                        "en": f"Site {site.site_id!r} is within {tolerance_m:g} m of "
                        f"{len(within)} modelled junctions; all candidates are named and none is "
                        "chosen.",
                        "ja": f"'{site.site_id}' は {tolerance_m:g} m 以内で {len(within)} 箇所の"
                        "モデル交差点と重なります。候補をすべて示し、どれも選びません。",
                    },
                    context={
                        "tolerance_m": tolerance_m,
                        "candidates": [
                            {
                                "node_id": node_id,
                                "node_name": name,
                                "distance_m": round(dist, 2),
                            }
                            for dist, node_id, name in within
                        ],
                    },
                )
            )
            continue

        distance, node_id, node_name = within[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        margin = tolerance_m - distance
        provisional[site.site_id] = SiteNodeMatch(
            site_id=site.site_id,
            site_name=site.site_name,
            node_id=node_id,
            node_name=node_name,
            distance_m=round(distance, 2),
            runner_up_node_id=runner_up[1] if runner_up else None,
            runner_up_distance_m=round(runner_up[0], 2) if runner_up else None,
            margin_m=round(margin, 2),
            thin_margin=margin < GEOREF_RESIDUAL_M,
            separation_ratio=(
                round(runner_up[0] / distance, 2) if runner_up and distance > 0 else None
            ),
        )

    # Two sites on one node: both refused, both named. A map that merged them
    # would silently compare two intersections' traffic against one node.
    claimed: dict[str, list[str]] = {}
    for site_id, match in provisional.items():
        claimed.setdefault(match.node_id, []).append(site_id)
    for node_id, site_ids in sorted(claimed.items()):
        if len(site_ids) < 2:
            continue
        for site_id in sorted(site_ids):
            provisional.pop(site_id, None)
            mapping.refusals.append(
                SiteNodeRefusal(
                    code=SITE_NODE_CONFLICT,
                    site_id=site_id,
                    message=(
                        f"sites {', '.join(sorted(site_ids))} all resolve to node {node_id}. Two "
                        "count sites on one node would merge two intersections' traffic into one "
                        "comparison, so all of them are refused rather than one being preferred."
                    ),
                    messages={
                        "ko": f"{', '.join(sorted(site_ids))} 지점이 모두 노드 {node_id}로 "
                        "귀결됩니다. 두 교차로의 교통량을 한 노드에 합치게 되므로 모두 거부합니다.",
                        "en": f"Sites {', '.join(sorted(site_ids))} all resolve to node "
                        f"{node_id}; all are refused rather than one being preferred.",
                        "ja": f"{', '.join(sorted(site_ids))} がいずれもノード {node_id} に"
                        "対応します。二つの交差点を一つのノードに合算することになるため、すべて拒否します。",
                    },
                    context={"node_id": node_id, "site_ids": sorted(site_ids)},
                )
            )

    mapping.matched = [provisional[site_id] for site_id in sorted(provisional)]
    mapping.refusals.sort(key=lambda refusal: (refusal.code, refusal.site_id or ""))
    return mapping


def summarize(mapping: SiteNodeMapping) -> dict[str, Any]:
    """A compact, inspectable rendering — the shape a probe log or an evidence
    file wants. Distances and node ids only; no count of anything."""
    return {
        "network": mapping.network,
        "comparison_level": mapping.comparison_level,
        "tolerance_m": mapping.tolerance_m,
        "tolerance_basis": mapping.tolerance_basis,
        "min_node_separation_m": (
            round(mapping.min_node_separation_m, 2)
            if mapping.min_node_separation_m is not None
            else None
        ),
        "tolerance_cannot_reach_two_nodes": mapping.tolerance_cannot_reach_two_nodes,
        "nodes_considered": mapping.nodes_considered,
        "nodes_without_position": mapping.nodes_without_position,
        "matched": [
            {
                "site_id": match.site_id,
                "node_id": match.node_id,
                "distance_m": match.distance_m,
                "margin_m": match.margin_m,
                "thin_margin": match.thin_margin,
                "runner_up_node_id": match.runner_up_node_id,
                "runner_up_distance_m": match.runner_up_distance_m,
                "separation_ratio": match.separation_ratio,
            }
            for match in mapping.matched
        ],
        "refusals": [
            {"code": refusal.code, "site_id": refusal.site_id, "message": refusal.message}
            for refusal in mapping.refusals
        ],
        "site_nodes": mapping.site_nodes,
    }


__all__ = [
    "COMPARISON_LEVEL",
    "COMPARISON_LEVEL_NOTE",
    "GEOREFERENCE_UNAVAILABLE",
    "GEOREF_RESIDUAL_M",
    "JUNCTION_CENTRE_ALLOWANCE_M",
    "NETWORK_UNREADABLE",
    "NO_NODES_WITH_POSITION",
    "PROJECTION_BUDGET_M",
    "SITE_NODE_AMBIGUOUS",
    "SITE_NODE_CONFLICT",
    "SITE_NODE_NO_CANDIDATE",
    "SITE_NODE_TOLERANCE_M",
    "SITE_POSITION_ABSENT",
    "TOLERANCE_BASIS",
    "SiteNodeMapping",
    "SiteNodeMatch",
    "SiteNodeRefusal",
    "SitePosition",
    "map_sites_to_nodes",
    "site_positions_from_metadata",
    "summarize",
]

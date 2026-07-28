"""Network component search: "I know its name or number, where IS it?"

The gap this closes: a real production network (the active Murdoch Drive
dataset — 312 links, 216 conflict areas, 247 routes, 74 desired-speed
decisions) is far too big to find anything by panning the diagram. Users
routinely know a component's *identity* ("link 515", "TCS398", "South St")
and nothing about its *location*. :func:`search` turns the former into the
latter: a ranked, bounded list of hits, each carrying a ready-to-use
``focus`` — exactly the ``{elements, bbox}`` payload the approval-card focus
machinery already ships, so the ``/network`` page can zoom + flash a hit
with the URL codec (``frontend/lib/networkFocus.ts``) it already has.

**One coordinate resolver, not two.** Every hit's geometry is built with
``services.agent.approval_focus``'s own ``FocusAccumulator`` (imported through
that module's additive public surface), including its anchor-vs-extent
policy, its 50 m context margin, its 200 m span cap and its fan-out cap. Two
resolvers that disagreed about where conflict area 2140 is would be worse
than one — and the approval card and the search result are, for the same
object, meant to frame the identical view.

**Matching is deterministic and lexical**, in the same spirit as
``services.agent.tools_network.resolve_object`` (the agent's "network-RAG"
read verb): no embeddings, no morphological analysis, Korean and English
tokens treated identically. It is deliberately NOT the same function:
``resolve_object`` answers "which object did the LLM mean?" over the eight
object types a *write verb* can target, and returns a top-5 with a difflib
near-miss fallback; this answers "what should I show in a search box?" over
every *drawable* family (19 of them, including families no write verb
touches — PT stops, travel-time sections, queue counters) and must be
exhaustive, id-prefix-aware and rank-stable so a result list doesn't reorder
under the user as they type. Sharing one scorer would have forced one of the
two to lie about its job; the overlap is one ``.lower()`` and one ``in``.

**Never reparses per request.** The whole searchable index (shadow model +
resolved element geometry + prebuilt normalized rows) is built once per
(.inpx path, mtime) and cached — see :func:`_index_for`. A warm search over
the real network is pure string work over ~1.1k rows plus focus resolution
for at most ``limit`` hits.

**Bounded output, always.** ``limit`` is clamped into ``[1, MAX_LIMIT]``, and
the response carries the full ``total`` alongside an explicit ``truncated``
flag (the 2026-07-27 observation-flood lesson: never hand a consumer an
unbounded list, and never let it silently believe a truncated list is
complete).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from services.agent.approval_focus import (
    MAX_FOCUS_FANOUT,
    FocusAccumulator,
    crossing_anchor,
    network_element_geometry,
)
from services.agent.tools_network import build_shadow_model
from services.datasets import get_active_inpx_path

logger = logging.getLogger(__name__)

# Default page size for one search. Twenty rows is about what a search
# dropdown can show before it stops being scannable, and it is small enough
# that per-hit focus resolution (the only non-trivial per-request work) stays
# in the sub-millisecond range even for conflict areas, whose anchor has to be
# derived from two link polylines.
DEFAULT_LIMIT = 20

# Hard ceiling, enforced by clamping rather than by rejecting: a search box is
# a place where a stray ``limit=100000`` should return 50 rows, not a 422.
# Callers can always tell they were capped — ``truncated`` says so and
# ``total`` says by how much.
MAX_LIMIT = 50


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
#
# Lower rank sorts first. The brief's five tiers (exact id > exact name > name
# prefix > name substring > id prefix) are preserved in that relative order;
# one extra tier is interleaved, for a reason that list can't express:
#
#   * TYPE (the whole query is a class alias, e.g. "신호등" / "signal head")
#     just below EXACT_NAME — a class query is a browse, not a lookup, so it
#     must not outrank a real id/name hit, but it must beat incidental
#     substring noise. On this network "link" appears inside 216
#     conflict-area display names ("Link 515 vs Link 10969"); without this
#     tier, asking for links would return conflict areas.
#
# ID_PREFIX's position DEPENDS ON THE SHAPE OF THE QUERY, because one fixed
# position is wrong for one of the two ways people use this box.
#
#   * An ALL-DIGIT query is a lookup by number, and someone typing a link
#     number arrives at it one digit at a time. On this network that partial
#     number is also a substring of 216 conflict-area display names
#     ("Link 515 vs Link 10969"), so ranking id-prefix below name-substring
#     buries the links entirely: measured on the live model, `51` pushed the
#     first link to result 17, and `11` returned NO link at all inside the
#     first 20 rows. For digits, id-prefix therefore sorts ABOVE both name
#     tiers (RANK_ID_PREFIX_NUMERIC).
#   * A query containing anything else is a lookup by name, and cannot
#     id-prefix-match at all while ids stay numeric. Its position is near
#     moot; it stays LAST so that if alphanumeric ids ever appear, a partial
#     name hit -- evidence of intent to find a specific street -- still wins.
#
# Both tiers report the same wire-visible `id_prefix` match field: the
# distinction is a ranking policy, not a different kind of match.
RANK_EXACT_ID = 0
RANK_EXACT_NAME = 1
RANK_TYPE = 2
RANK_ID_PREFIX_NUMERIC = 3
RANK_NAME_PREFIX = 4
RANK_NAME_SUBSTRING = 5
RANK_ID_PREFIX = 6

# Wire-visible, English, machine-readable tag for WHY a row matched (the UI
# can highlight the matched field / group by tier). Not user-facing prose.
_MATCH_FIELD_BY_RANK = {
    RANK_EXACT_ID: "id",
    RANK_EXACT_NAME: "name",
    RANK_TYPE: "type",
    RANK_ID_PREFIX_NUMERIC: "id_prefix",
    RANK_NAME_PREFIX: "name_prefix",
    RANK_NAME_SUBSTRING: "name_substring",
    RANK_ID_PREFIX: "id_prefix",
}


def _norm(value: Any) -> str:
    """Case-folded, whitespace-trimmed comparison form for ids and names."""
    return str(value or "").strip().lower()


def _norm_alias(value: Any) -> str:
    """Comparison form for CLASS ALIASES only: additionally whitespace-free,
    so "signal head"/"signalhead" and "차량 입력"/"차량입력" are one alias
    rather than four. Deliberately not used for names — "south st" must still
    substring-match "South St EB", which whitespace removal would break."""
    return "".join(str(value or "").split()).lower()


# ---------------------------------------------------------------------------
# Focus builders — one per family, all funnelling through approval_focus's
# FocusAccumulator so search and the approval card frame the same view.
# ---------------------------------------------------------------------------


def _link_of(index: "_SearchIndex", raw_link_id: Any) -> dict[str, Any] | None:
    return index.links_by_id.get(str(raw_link_id or ""))


def _focus_link(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """A link is a genuinely extended target, like a route: no single point of
    interest exists, so this stays ANCHORLESS and the bbox is the polyline's
    own extent (uncapped, per the accumulator's anchorless branch)."""
    acc = FocusAccumulator()
    acc.add_link(obj)
    return acc


def _focus_node(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    acc = FocusAccumulator()
    node_id = str(obj.get("id", ""))
    acc.add_element("node", node_id, obj.get("name") or node_id)
    polygon = obj.get("polygon") or []
    if polygon:
        # A node polygon IS the junction footprint — bounded and meaningful on
        # its own, so it drives the extent directly.
        acc.add_points(polygon)
    else:
        # Most real nodes carry no polygon at all (all 6 on the active
        # network); inpx_parser derives a centre from the link segments that
        # cross them, which is a true point of interest -> anchor it.
        acc.add_anchor(obj.get("x"), obj.get("y"))
    return acc


def _focus_signal_controller(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """A signal controller has no geometry of its own — it is a controller,
    not a placed object — so its focus is its signal heads, exactly as
    ``approval_focus._resolve_signal_heads`` builds it for
    ``set_signal_cycle``/``set_signal_offset``."""
    acc = FocusAccumulator()
    sc_no = str(obj.get("sc_no", ""))
    acc.add_element("signal_controller", sc_no, obj.get("sc_name") or sc_no)
    heads = [h for h in index.elements.get("signal_heads", []) if str(h.get("sc") or "") == sc_no]
    for head in heads[:MAX_FOCUS_FANOUT]:
        acc.add_element("signal_head", head.get("id", ""), head.get("name") or head.get("id", ""))
        acc.add_anchor(head.get("x"), head.get("y"))
        acc.add_link(_link_of(index, head.get("link")))
    return acc


def _point_element_focus(
    index: "_SearchIndex", obj: dict[str, Any], elem_type: str, geometry_key: str | None
) -> FocusAccumulator:
    """The shared shape for every element placed at a point on a lane/link:
    the element's own resolved position ANCHORS the view, its host link is
    highlighted around it for context. ``geometry_key`` names the
    ``parse_network_elements`` list to look the position up in when the
    identity list is the shadow model's (vehicle inputs, des-speed decisions);
    ``None`` when ``obj`` already carries its own x/y."""
    acc = FocusAccumulator()
    obj_id = str(obj.get("id") or obj.get("no") or "")
    acc.add_element(elem_type, obj_id, obj.get("name") or obj_id)
    geo = obj if geometry_key is None else index.element_by_id.get(geometry_key, {}).get(obj_id)
    if geo is None:
        # Searchable but not flashable: the element exists in the model but
        # its link/lane/pos never resolved to a coordinate (inpx_parser drops
        # those rather than inventing null coordinates). bbox comes back None.
        return acc
    acc.add_anchor(geo.get("x"), geo.get("y"))
    if geo.get("end_x") is not None:
        # Length-bearing elements (reduced-speed areas, PT stops, detectors)
        # anchor on BOTH ends so the whole extent of the thing sits in view.
        acc.add_anchor(geo.get("end_x"), geo.get("end_y"))
    acc.add_link(_link_of(index, geo.get("link")))
    return acc


def _point_focus(elem_type: str, geometry_key: str | None = None) -> Callable[..., FocusAccumulator]:
    def build(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
        return _point_element_focus(index, obj, elem_type, geometry_key)

    return build


def _focus_conflict_area(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """Conflict areas have no geometry in the .inpx at all — they are a link
    pair — so the location is DERIVED by ``crossing_anchor``, the same
    approval_focus helper (and the same reason it exists: the union of two
    whole approach links' extents is effectively the entire network)."""
    acc = FocusAccumulator()
    ca_no = str(obj.get("no", ""))
    acc.add_element("conflict_area", ca_no, obj.get("name") or ca_no)
    link_a = _link_of(index, obj.get("link_a"))
    link_b = _link_of(index, obj.get("link_b"))
    acc.add_link(link_a)
    acc.add_link(link_b)
    if link_a is not None and link_b is not None:
        anchor = crossing_anchor(link_a.get("points"), link_b.get("points"))
        if anchor is not None:
            acc.add_anchor(anchor[0], anchor[1])
    return acc


def _focus_routing_decision(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """A routing decision sits on its approach link. The shadow model carries
    the link but not the decision's ``pos`` along it, so there is no honest
    single point to anchor on — this stays ANCHORLESS and frames the approach
    link, which is the object's real footprint anyway."""
    acc = FocusAccumulator()
    dec_no = str(obj.get("no", ""))
    acc.add_element("routing_decision", dec_no, obj.get("name") or dec_no)
    acc.add_link(_link_of(index, obj.get("link")))
    return acc


def _focus_route(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """Mirrors ``approval_focus._resolve_route_relative_flow`` verbatim: a
    route IS long, so it is anchorless and uncapped in span, with the same
    ``MAX_FOCUS_FANOUT`` cap on how many of its links are drawn and the same
    origin/destination fallback for a route whose ``link_seq`` is empty."""
    acc = FocusAccumulator()
    route_id = str(obj.get("id", ""))
    acc.add_element("route", route_id, route_id)
    link_seq = list(obj.get("link_seq") or [])
    for link_id in link_seq[:MAX_FOCUS_FANOUT]:
        acc.add_link(_link_of(index, link_id))
    if not link_seq:
        decision = index.element_by_id.get("routing_decisions", {}).get(str(obj.get("decision_no", "")))
        if decision is not None:
            acc.add_link(_link_of(index, decision.get("link")))
        acc.add_link(_link_of(index, obj.get("dest_link")))
    return acc


def _focus_vehicle_composition(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """A composition is a demand recipe, not a placed object — its focus is
    the vehicle inputs that USE it, same derivation as
    ``approval_focus._resolve_vehicle_composition_share``."""
    acc = FocusAccumulator()
    comp_no = str(obj.get("no", ""))
    acc.add_element("vehicle_composition", comp_no, obj.get("name") or comp_no)
    vi_by_id = index.element_by_id.get("vehicle_inputs", {})
    for vi_no in list(obj.get("referencing_vehicle_input_nos") or [])[:MAX_FOCUS_FANOUT]:
        item = vi_by_id.get(str(vi_no))
        if item is None:
            continue
        acc.add_element("vehicle_input", vi_no, item.get("name") or str(vi_no))
        acc.add_anchor(item.get("x"), item.get("y"))
        acc.add_link(_link_of(index, item.get("link")))
    return acc


def _focus_traveltime_section(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """Anchorless, like a route: a travel-time section is defined by two ends
    that can be a kilometre apart. Only the two ENDPOINTS feed the extent —
    the host links are named as elements but their (long) polylines are
    deliberately kept out of the bbox, which would otherwise stretch to
    whatever corridor they belong to."""
    acc = FocusAccumulator()
    section_id = str(obj.get("id", ""))
    acc.add_element("traveltime_section", section_id, obj.get("name") or section_id)
    for end_key in ("start", "end"):
        end = obj.get(end_key) or {}
        acc.add_point(end.get("x"), end.get("y"))
        link = _link_of(index, end.get("link"))
        if link is not None:
            acc.add_element("link", link.get("id", ""), link.get("name") or str(link.get("id", "")))
    return acc


def _focus_ped_area(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    acc = FocusAccumulator()
    area_id = str(obj.get("id", ""))
    acc.add_element("ped_area", area_id, obj.get("name") or area_id)
    acc.add_points(obj.get("polygon"))
    return acc


def _focus_none(index: "_SearchIndex", obj: dict[str, Any]) -> FocusAccumulator:
    """Families with no geometry anywhere in the model (speed distributions
    are pure data objects). Returns an empty accumulator, which resolves to
    ``bbox=None`` — searchable, not flashable, never an error."""
    return FocusAccumulator()


# ---------------------------------------------------------------------------
# Label detail — RAW parts only
# ---------------------------------------------------------------------------
#
# Every hit's ``label`` is a bag of raw model values plus machine tokens; the
# UI composes the human string with its own t()/i18n plumbing. Nothing in this
# module ever emits a localized noun, a unit, or a sentence — the language
# regression fixed in bd63874 came from exactly that shortcut.


def _detail_link(obj: dict[str, Any]) -> dict[str, Any]:
    return {"connector": bool(obj.get("is_connector")), "lanes": obj.get("lanes"), "length_m": obj.get("length")}


def _detail_signal_controller(obj: dict[str, Any]) -> dict[str, Any]:
    return {"controller_type": obj.get("controller_type"), "cycle_time_s": obj.get("cycle_time")}


def _detail_signal_head(obj: dict[str, Any]) -> dict[str, Any]:
    return {"link": obj.get("link"), "lane": obj.get("lane"), "sc": obj.get("sc"), "sg": obj.get("sg")}


def _detail_vehicle_input(obj: dict[str, Any]) -> dict[str, Any]:
    return {"link": obj.get("link"), "volume": obj.get("volume"), "interval_count": obj.get("interval_count")}


def _detail_conflict_area(obj: dict[str, Any]) -> dict[str, Any]:
    return {"link_a": obj.get("link_a"), "link_b": obj.get("link_b"), "status": obj.get("status")}


def _detail_routing_decision(obj: dict[str, Any]) -> dict[str, Any]:
    return {"link": obj.get("link"), "route_count": len(obj.get("routes") or [])}


def _detail_route(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_no": obj.get("decision_no"),
        "route_no": obj.get("route_no"),
        "dest_link": obj.get("dest_link"),
        "disconnected": obj.get("disconnected"),
    }


def _detail_vehicle_composition(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_count": len(obj.get("entries") or []),
        "referencing_vehicle_input_count": len(obj.get("referencing_vehicle_input_nos") or []),
    }


def _detail_lane_pos(obj: dict[str, Any]) -> dict[str, Any]:
    return {"link": obj.get("link"), "lane": obj.get("lane"), "pos_m": obj.get("pos")}


def _detail_traveltime_section(obj: dict[str, Any]) -> dict[str, Any]:
    start = obj.get("start") or {}
    end = obj.get("end") or {}
    return {"start_link": start.get("link"), "end_link": end.get("link")}


def _detail_empty(obj: dict[str, Any]) -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Family table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Family:
    """One searchable object family.

    ``source`` is ``"shadow:<key>"`` (the agent shadow model, which carries
    identity + attributes for everything a tool can address) or
    ``"elements:<key>"`` (``inpx_parser.parse_network_elements``, which
    carries resolved x/y for the drawn element classes). Families that exist
    in both are sourced from the shadow model for IDENTITY and from
    the matching ``parse_network_elements`` list for POSITION (the
    ``geometry_key`` their ``focus`` builder closes over), so an element whose
    link never resolved is still findable — it just comes back with
    ``bbox=None``.

    ``aliases`` are QUERY vocabulary (what a user might type to browse the
    class), normalized by :func:`_norm_alias`. They are matching input, never
    output: the ko/ja tokens here never appear in a response body. The
    frontend keeps its own ko/en display labels in
    ``lib/networkElements.ts``; this table deliberately does not mirror them
    (it needs synonyms and plurals that a label list must not contain).
    """

    type: str
    source: str
    id_field: str
    name_field: str
    aliases: tuple[str, ...]
    focus: Callable[["_SearchIndex", dict[str, Any]], FocusAccumulator]
    detail: Callable[[dict[str, Any]], dict[str, Any]] = _detail_empty


# Order is load-bearing: it is the stable tie-break WITHIN a rank (see
# :func:`search`), so the families a user most likely means come first.
_FAMILIES: tuple[_Family, ...] = (
    _Family(
        type="link",
        source="shadow:links",
        id_field="id",
        name_field="name",
        aliases=("link", "links", "connector", "connectors", "링크", "연결로", "リンク", "コネクタ"),
        focus=_focus_link,
        detail=_detail_link,
    ),
    _Family(
        type="node",
        source="shadow:nodes",
        id_field="id",
        name_field="name",
        aliases=("node", "nodes", "intersection", "노드", "교차로", "ノード", "交差点"),
        focus=_focus_node,
    ),
    _Family(
        type="signal_controller",
        source="shadow:signals",
        id_field="sc_no",
        name_field="sc_name",
        aliases=(
            "signal controller",
            "signal controllers",
            "controller",
            "signal",
            "신호기",
            "신호제어기",
            "제어기",
            "신호",
            "信号制御機",
        ),
        focus=_focus_signal_controller,
        detail=_detail_signal_controller,
    ),
    _Family(
        type="signal_head",
        source="elements:signal_heads",
        id_field="id",
        name_field="name",
        aliases=("signal head", "signal heads", "신호등", "신호 헤드", "信号灯", "信号ヘッド"),
        focus=_point_focus("signal_head"),
        detail=_detail_signal_head,
    ),
    _Family(
        type="vehicle_input",
        source="shadow:vehicle_inputs",
        id_field="no",
        name_field="name",
        aliases=("vehicle input", "vehicle inputs", "차량 입력", "교통량 입력", "유입량", "車両入力"),
        focus=_point_focus("vehicle_input", geometry_key="vehicle_inputs"),
        detail=_detail_vehicle_input,
    ),
    _Family(
        type="conflict_area",
        source="shadow:conflict_areas",
        id_field="no",
        name_field="name",
        aliases=("conflict area", "conflict areas", "conflict", "상충 영역", "상충구역", "충돌 영역", "競合エリア"),
        focus=_focus_conflict_area,
        detail=_detail_conflict_area,
    ),
    _Family(
        type="routing_decision",
        source="shadow:routing_decisions",
        id_field="no",
        name_field="name",
        aliases=("routing decision", "routing decisions", "routing", "경로 결정", "경로배분", "経路決定"),
        focus=_focus_routing_decision,
        detail=_detail_routing_decision,
    ),
    _Family(
        type="route",
        source="shadow:routes",
        id_field="id",
        # Routes have no ``name`` attribute in Vissim at all (verified on the
        # active network: 247 routes, zero names) — they are addressed by the
        # synthesized "{decision_no}.{route_no}" id. Pointing name_field at a
        # key that does not exist yields "" for every row, which the matcher
        # already treats as "no name to match", so this needs no special case.
        name_field="name",
        aliases=("route", "routes", "경로", "ルート"),
        focus=_focus_route,
        detail=_detail_route,
    ),
    _Family(
        type="des_speed_decision",
        source="shadow:des_speed_decisions",
        id_field="no",
        name_field="name",
        aliases=(
            "desired speed decision",
            "des speed decision",
            "desired speed",
            "희망속도 결정",
            "희망속도",
            "希望速度決定",
        ),
        focus=_point_focus("des_speed_decision", geometry_key="des_speed_decisions"),
        detail=_detail_lane_pos,
    ),
    _Family(
        type="reduced_speed_area",
        source="elements:reduced_speed_areas",
        id_field="id",
        name_field="name",
        aliases=("reduced speed area", "reduced speed areas", "reduced speed", "감속 구역", "감속구간", "減速エリア"),
        focus=_point_focus("reduced_speed_area"),
        detail=_detail_lane_pos,
    ),
    _Family(
        type="detector",
        source="elements:detectors",
        id_field="id",
        name_field="name",
        aliases=("detector", "detectors", "검지기", "루프검지기", "検知器"),
        focus=_point_focus("detector"),
        detail=_detail_lane_pos,
    ),
    _Family(
        type="data_collection_point",
        source="elements:data_collection_points",
        id_field="id",
        name_field="name",
        aliases=("data collection point", "data collection points", "data collection", "데이터 수집 지점", "データ収集地点"),
        focus=_point_focus("data_collection_point"),
        detail=_detail_lane_pos,
    ),
    _Family(
        type="queue_counter",
        source="elements:queue_counters",
        id_field="id",
        name_field="name",
        aliases=("queue counter", "queue counters", "queue", "대기행렬 카운터", "대기행렬", "待ち行列カウンタ"),
        focus=_point_focus("queue_counter"),
        detail=_detail_lane_pos,
    ),
    _Family(
        type="pt_stop",
        source="elements:pt_stops",
        id_field="id",
        name_field="name",
        aliases=("pt stop", "pt stops", "bus stop", "정류장", "버스정류장", "停留所"),
        focus=_point_focus("pt_stop"),
        detail=_detail_lane_pos,
    ),
    _Family(
        type="stop_sign",
        source="elements:stop_signs",
        id_field="id",
        name_field="name",
        aliases=("stop sign", "stop signs", "정지 표지", "정지표지판", "一時停止標識"),
        focus=_point_focus("stop_sign"),
        detail=_detail_lane_pos,
    ),
    _Family(
        type="traveltime_section",
        source="elements:traveltime_sections_geo",
        id_field="id",
        name_field="name",
        aliases=("travel time section", "travel time sections", "travel time", "통행시간 구간", "통행시간", "旅行時間区間"),
        focus=_focus_traveltime_section,
        detail=_detail_traveltime_section,
    ),
    _Family(
        type="ped_area",
        source="elements:ped_areas",
        id_field="id",
        name_field="name",
        aliases=("pedestrian area", "ped area", "보행자 구역", "보행공간", "歩行者エリア"),
        focus=_focus_ped_area,
    ),
    _Family(
        type="vehicle_composition",
        source="shadow:vehicle_compositions",
        id_field="no",
        name_field="name",
        aliases=("vehicle composition", "vehicle compositions", "composition", "차량 구성", "차종 구성", "車両構成"),
        focus=_focus_vehicle_composition,
        detail=_detail_vehicle_composition,
    ),
    _Family(
        type="des_speed_distribution",
        source="shadow:des_speed_distributions",
        id_field="no",
        name_field="name",
        # The one family with NO geometry anywhere in the model. Included on
        # purpose: a speed distribution is exactly the kind of thing a user
        # knows by number ("distribution 70"), and "searchable but not
        # flashable" (bbox=None) is a better answer than "not found".
        aliases=("speed distribution", "speed distributions", "desired speed distribution", "속도 분포", "희망속도 분포", "速度分布"),
        focus=_focus_none,
    ),
)

_FAMILY_BY_TYPE: dict[str, _Family] = {fam.type: fam for fam in _FAMILIES}

# {normalized alias: family type}. Built once at import; a duplicate alias
# across two families would silently shadow one of them, so the first
# registration wins and the collision is logged rather than raising at import
# time (a bad alias must never take the backend down).
_TYPE_BY_ALIAS: dict[str, str] = {}
for _fam in _FAMILIES:
    for _alias in (_fam.type, *_fam.aliases):
        _key = _norm_alias(_alias)
        if not _key:
            continue
        if _key in _TYPE_BY_ALIAS and _TYPE_BY_ALIAS[_key] != _fam.type:
            logger.warning(
                "network_search: alias %r claimed by both %s and %s; keeping %s",
                _alias,
                _TYPE_BY_ALIAS[_key],
                _fam.type,
                _TYPE_BY_ALIAS[_key],
            )
            continue
        _TYPE_BY_ALIAS[_key] = _fam.type


# ---------------------------------------------------------------------------
# Index (mtime-cached)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    """One searchable object, pre-normalized. ``seq`` is its global position
    in family order — the stable tie-break within a rank."""

    family: str
    seq: int
    obj_id: str
    name: str
    id_norm: str
    name_norm: str
    obj: dict[str, Any]


@dataclass
class _SearchIndex:
    shadow: dict[str, Any]
    elements: dict[str, Any]
    links_by_id: dict[str, dict[str, Any]]
    element_by_id: dict[str, dict[str, dict[str, Any]]]
    rows: list[_Row]
    rows_by_family: dict[str, list[_Row]] = field(default_factory=dict)


def _source_list(shadow: dict[str, Any], elements: dict[str, Any], source: str) -> list[dict[str, Any]]:
    kind, _, key = source.partition(":")
    container = shadow if kind == "shadow" else elements
    value = container.get(key)
    return value if isinstance(value, list) else []


def _build_index(inpx_path: Path) -> _SearchIndex:
    shadow = build_shadow_model(inpx_path)
    # ``network_element_geometry`` is approval_focus's own never-raising
    # wrapper around parse_network_elements; it reuses the shadow model's
    # already-parsed links rather than parsing them a second time, and returns
    # {} (not an exception) for an unreadable/corrupt .inpx.
    elements = network_element_geometry(shadow, inpx_path)

    links_by_id = {str(link.get("id", "")): link for link in shadow.get("links") or []}
    element_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for key, value in elements.items():
        if isinstance(value, list):
            element_by_id[key] = {str(item.get("id", "")): item for item in value}
    # Routes' empty-link_seq fallback needs its parent decision by number.
    element_by_id["routing_decisions"] = {
        str(dec.get("no", "")): dec for dec in shadow.get("routing_decisions") or []
    }

    rows: list[_Row] = []
    rows_by_family: dict[str, list[_Row]] = {}
    seq = 0
    for family in _FAMILIES:
        family_rows: list[_Row] = []
        for obj in _source_list(shadow, elements, family.source):
            obj_id = str(obj.get(family.id_field, "") or "")
            if not obj_id:
                continue
            name = str(obj.get(family.name_field, "") or "")
            row = _Row(
                family=family.type,
                seq=seq,
                obj_id=obj_id,
                name=name,
                id_norm=_norm(obj_id),
                name_norm=_norm(name),
                obj=obj,
            )
            seq += 1
            rows.append(row)
            family_rows.append(row)
        rows_by_family[family.type] = family_rows

    return _SearchIndex(
        shadow=shadow,
        elements=elements,
        links_by_id=links_by_id,
        element_by_id=element_by_id,
        rows=rows,
        rows_by_family=rows_by_family,
    )


@lru_cache(maxsize=4)
def _index_cached(inpx_path_str: str, mtime: float) -> _SearchIndex:
    del mtime  # cache-busting arg only — mirrors tools_network.build_shadow_model
    return _build_index(Path(inpx_path_str))


def _index_for(inpx_path: str | Path) -> _SearchIndex:
    """The searchable index for ``inpx_path``, rebuilt only when its mtime
    changes. ``build_shadow_model`` is itself mtime-cached, so a warm call
    here costs one ``os.path.getmtime`` and one dict lookup — nothing is
    reparsed per request."""
    path = Path(inpx_path)
    return _index_cached(str(path), os.path.getmtime(path))


def clear_cache() -> None:
    """Drop the index cache (tests; ops). The mtime key already handles real
    edits — this exists for fixtures that rewrite a file within one clock
    tick, where mtime can be indistinguishable."""
    _index_cached.cache_clear()


# ---------------------------------------------------------------------------
# Matching / ranking
# ---------------------------------------------------------------------------


def _rank_for(
    row: _Row,
    query_norm: str,
    alias_family: str | None,
    query_is_numeric: bool = False,
) -> int | None:
    """The best (lowest) rank at which ``row`` matches, or ``None``.

    Checked in BEST-to-WORST order (matching the RANK_* numeric order above),
    not the order a human would read the criteria in -- a row can satisfy more
    than one tier at once (e.g. id "50" on a row whose name also happens to
    start with "50"), and the first check that matches here is the one that
    wins, so the check order must be the priority order.

    ``query_is_numeric`` promotes the id-prefix tier above both name tiers;
    see the RANK_* block for why an all-digit query needs the opposite order
    from a spelled-out one."""
    if query_norm and row.id_norm == query_norm:
        return RANK_EXACT_ID
    if query_norm and row.name_norm and row.name_norm == query_norm:
        return RANK_EXACT_NAME
    if alias_family is not None and row.family == alias_family:
        return RANK_TYPE
    if query_is_numeric and query_norm and row.id_norm.startswith(query_norm):
        return RANK_ID_PREFIX_NUMERIC
    if query_norm and row.name_norm and row.name_norm.startswith(query_norm):
        return RANK_NAME_PREFIX
    if query_norm and row.name_norm and query_norm in row.name_norm:
        return RANK_NAME_SUBSTRING
    if query_norm and row.id_norm.startswith(query_norm):
        return RANK_ID_PREFIX
    return None


def _focus_for(index: _SearchIndex, family: _Family, obj: dict[str, Any]) -> dict[str, Any]:
    """``{elements, bbox}`` for one hit — the exact shape
    ``frontend/lib/networkFocus.ts``'s ``NetworkFocus`` consumes.

    **Never raises**, for the same reason ``approval_focus.resolve_focus``
    doesn't: a geometry bug must degrade a hit to "found but not flashable",
    never take out the search."""
    try:
        result = family.focus(index, obj).result()
    except Exception:  # noqa: BLE001 - a map bug must never break search
        logger.exception("network_search: focus resolution failed for %s %r", family.type, obj.get("id") or obj.get("no"))
        return {"elements": [], "bbox": None}
    return {"elements": result["elements"], "bbox": result["bbox"]}


def _hit(index: _SearchIndex, row: _Row, rank: int) -> dict[str, Any]:
    family = _FAMILY_BY_TYPE[row.family]
    return {
        "type": row.family,
        "id": row.obj_id,
        "name": row.name,
        # RAW parts only — the UI composes the human string (ko primary, en/ja
        # through its own t()). Never a localized noun from this module.
        "label": {
            "type": row.family,
            "id": row.obj_id,
            "name": row.name,
            "detail": {k: v for k, v in family.detail(row.obj).items() if v is not None},
        },
        "match": {"rank": rank, "field": _MATCH_FIELD_BY_RANK[rank]},
        "focus": _focus_for(index, family, row.obj),
    }


def search(
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    inpx_path: str | Path | None = None,
) -> dict[str, Any]:
    """Find network components whose id, name or class matches ``query``.

    Returns ``{"query", "total", "returned", "limit", "truncated",
    "results"}``. ``total`` is the count of ALL matching components, before
    the ``limit`` page; ``truncated`` is ``total > returned``. ``results`` is
    ranked (see the RANK_* constants) and, within a rank, stable in family
    order then model order — so a result list does not reshuffle under the
    user as they type another character.

    An empty or whitespace-only ``query`` returns zero results, deliberately:
    a blank search box must not dump the whole network (there are ~1.1k
    searchable components on the active model).

    ``inpx_path`` defaults to the active dataset
    (``services.datasets.get_active_inpx_path``).
    """
    if inpx_path is None:
        inpx_path = get_active_inpx_path()

    effective_limit = max(1, min(int(limit), MAX_LIMIT))
    query_norm = _norm(query)
    if not query_norm:
        return {
            "query": query or "",
            "total": 0,
            "returned": 0,
            "limit": effective_limit,
            "truncated": False,
            "results": [],
        }

    index = _index_for(inpx_path)
    alias_family = _TYPE_BY_ALIAS.get(_norm_alias(query))
    # Digits only -- the user is reaching for a number, so id-prefix hits
    # outrank incidental matches inside spelled-out names. See RANK_*.
    query_is_numeric = query_norm.isdigit()

    matches: list[tuple[int, int, _Row]] = []
    for row in index.rows:
        rank = _rank_for(row, query_norm, alias_family, query_is_numeric)
        if rank is None:
            continue
        matches.append((rank, row.seq, row))
    matches.sort(key=lambda item: (item[0], item[1]))

    page = matches[:effective_limit]
    results = [_hit(index, row, rank) for rank, _seq, row in page]
    return {
        "query": query or "",
        "total": len(matches),
        "returned": len(results),
        "limit": effective_limit,
        "truncated": len(matches) > len(results),
        "results": results,
    }


def searchable_families() -> list[dict[str, Any]]:
    """The family catalogue, for docs/debugging and for a UI that wants to
    offer type filters. ``aliases`` are query vocabulary, not display labels
    — a caller rendering these to a user would be re-introducing exactly the
    hardcoded-language bug the ``label`` shape exists to prevent."""
    return [
        {"type": fam.type, "source": fam.source, "id_field": fam.id_field, "name_field": fam.name_field, "aliases": list(fam.aliases)}
        for fam in _FAMILIES
    ]


__all__ = ["search", "searchable_families", "clear_cache", "DEFAULT_LIMIT", "MAX_LIMIT"]

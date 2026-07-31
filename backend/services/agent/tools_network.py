"""Read tools over the network shadow model: overview, lexical object
resolution, vehicle inputs, signal controllers, and conflict areas.

These are the first real tools registered through the M0.4 registry
(``services.agent.toolkit`` — see docs/phase2/02-ROADMAP.md M1.2); importing
this module registers them onto ``default_registry`` at decoration time.

``build_shadow_model`` assembles the EXTENDED shadow model M0.3's
constraints.py expects (see that module's docstring): links/nodes from
``services.inpx_parser.parse_network`` plus ``vehicle_inputs`` (parsed here
directly from the .inpx — no/name/link/volume/interval_count/volumes, volume
``None`` and interval_count ``0``/volumes ``[]`` when the demo file's
``<vehicleInput>`` carries no ``timeIntervalVehVolume`` child; v2 unit ⑤,
2026-07-24: ``volumes`` is now populated for real, one entry per time
interval — see ``_parse_vehicle_inputs``'s own docstring for the live-verified
findings),
``signals`` (from ``services.signal_parser.parse_signal_controllers``, mapped
down to the contract shape — ``controller_type`` is now populated for real
(v2 unit ①, 2026-07-24: ``signal_parser.py`` extracts the verbatim
``<signalController type="...">`` attribute, e.g. ``"FIXEDTIME"``/
``"FIXEDTIMESIMPLE"``/``"unknown"``). ``min_green_intergreen_sum`` remains
deliberately OMITTED, not set to ``None``: the parser still doesn't expose
it; see docs/phase2/DECISIONS.md's #5 gap entry. ``conflict_areas`` (v2 unit
④, 2026-07-24: parsed here directly from the .inpx — no/link_a/link_b/
link_pair/status/name — closing the M3.1 CATALOG-GAP so
``conflict_area_data_unavailable`` finally fires for real; see
:func:`_parse_conflict_areas`'s own docstring for the live-verified findings),
``routing_decisions`` and ``routes`` (v2 unit ⑥, 2026-07-24: parsed here
directly from ``<vehicleRoutingDecisionStatic>``/``<vehicleRouteStatic>`` —
``routing_decisions`` is the planner-facing nested shape (decision no/link,
each route's dest link/relative-flow profile/link sequence);  ``routes`` is
a FLAT, one-entry-per-route mirror of the same data, keyed by a synthesized
``"{decision_no}.{route_no}"`` id — this is the exact shape and field names
``constraints.py``'s dormant ``route_integrity_unverifiable`` (#31) and
``demand_scale_route_double_count_gap`` (#14) checks already expected from
their own CATALOG-GAP docstrings, so populating it here WAKES #31 for real
(``disconnected`` is genuinely computed from each route's ``link_seq``
against the network's own link ids) while #14 stays open (its
``manually_scaled_this_turn`` flag is turn-scoped ephemeral state with no
XML representation — see :func:`_flatten_routes`'s own docstring). See
:func:`_parse_routing_decisions`'s own docstring for the live-verified
findings), ``vehicle_compositions`` and ``vehicle_composition_entries`` (v2
unit ⑦, 2026-07-27: parsed here directly from ``<vehicleComposition>`` /
``<vehicleCompositionRelativeFlow>`` — ``vehicle_compositions`` is the
planner-facing nested shape (composition no/name, each vehicle type's
relative-flow weight + which vehicle inputs reference the composition);
``vehicle_composition_entries`` is a FLAT, one-entry-per-(composition,
vehType) mirror keyed by a synthesized ``"{composition_no}.{veh_type}"`` id
— the SAME "nested for the planner, flat for guardrails.py" split
:func:`_flatten_routes` established for routing decisions, since a
composition entry has no single-token identity of its own either (verified
live: ``IVehicleCompositionRelativeFlowContainer`` has no ``ItemByKey`` at
all). See :func:`_parse_vehicle_compositions`'s own docstring for the
live-verified findings), ``des_speed_decisions`` and
``des_speed_decision_entries`` (v2a unit ⑧, 2026-07-27: parsed here directly
from ``<desSpeedDecision>`` / ``<vehClassDesSpeedDistribution>`` — same
"nested for the planner, flat for guardrails.py" split as routes/vehicle-
composition entries, keyed by a synthesized ``"{decision_no}.{veh_class_no}"``
id; unlike RelFlow, there is NO shared-sum/renormalization semantics here at
all — each vehicle class's distribution assignment is fully independent. See
:func:`_parse_des_speed_decisions`'s own docstring for the live-verified
findings, including COM's fail-closed behavior on a bogus distribution
reference), and ``des_speed_distributions`` (flat no/name list, shared with
``set_reduced_speed_area``'s own distribution object type — see
:func:`_parse_speed_distributions`), and ``vehicle_types`` (QUEUE (v2a) unit:
parsed here directly from ``<vehicleType>`` — scalars (no/name/category/
capacity/cap_exact) plus every DISTRIBUTION/FUNCTION reference the type
carries, resolved to cheap stats (id+name, plus min/max/mean for weight/
power/length) and a flat ``"{field}_no"`` id per reference for guardrails.py.
The 8 referenced target collections (``model2d3d_distributions``,
``weight_distributions``, ``power_distributions``, ``des_accel_functions``,
``max_accel_functions``, ``des_decel_functions``, ``max_decel_functions``,
``curve_speed_functions``) are also exposed as their own flat shadow-model
lists, purely so constraints.py can validate a proposed write target. See
:func:`_parse_vehicle_types`'s own docstring for the live-verified findings,
including nullable references on PEDESTRIAN-category types), and
``time_distributions`` (QUEUE (Q3, v2a) unit 5: parsed here directly from
``<timeDistributions><timeDistribution>`` — no/name/type/mean_std_dev
plus, for EMPIRICAL-type entries, the ``durDistrDataPts`` curve; a genuinely
SHARED collection referenced by PT-stop dwell-time assignments,
per-vehicle-class dwell overrides, driving-behavior CC1/zipper-overtake time
(confirming 00-EXECUTION-GUIDE.md's own CC1 claim empirically), and further
sites with no shadow-model object of their own — NOT stop signs, disproving
this unit's own task-brief guess. See :func:`_parse_time_distributions`'s
own docstring for the live-verified findings).

``resolve_object`` is the "network-RAG" pattern (research-digest #10): a
small, deterministic lexical top-k retrieval over the shadow model's objects.
There is no morphological analysis (Korean tokens split on whitespace, same
as English) and no embeddings — scoring is exact match > substring > shared
lowercase tokens, in that tier order, ties broken by object id ascending.

Every tool here is planner-facing and takes NO file paths — the LLM must
never see or choose a path. ``set_active_inpx`` is the module-level session
context the M1.3 orchestrator swaps per session (defaulting to
``config.SIMULATION_INPX_PATH``, the demo network); tests use the same hook.
"""

from __future__ import annotations

import difflib
import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

import config
from services.agent.toolkit import tool
from services.inpx_parser import parse_network, vehicle_composition_names, vehicle_input_composition_fields
from services import signal_parser
from services.signal_parser import parse_signal_controllers, parse_signal_programs

ObjectType = Literal[
    "link",
    "node",
    "vehicle_input",
    "signal",
    "conflict_area",
    "routing_decision",
    "vehicle_composition",
    "des_speed_decision",
    "priority_rule",
    "vehicle_type",
]

# {object_type: {shadow_key, id_field, name_field}} — the one place that maps
# a planner-facing object_type onto the shadow model's (differently-named)
# id/name fields. Order here is the order get_network_overview renders in.
_OBJECT_TYPE_SPECS: dict[str, dict[str, str]] = {
    "link": {"shadow_key": "links", "id_field": "id", "name_field": "name"},
    "node": {"shadow_key": "nodes", "id_field": "id", "name_field": "name"},
    "vehicle_input": {"shadow_key": "vehicle_inputs", "id_field": "no", "name_field": "name"},
    "signal": {"shadow_key": "signals", "id_field": "sc_no", "name_field": "sc_name"},
    "conflict_area": {"shadow_key": "conflict_areas", "id_field": "no", "name_field": "name"},
    # v2 unit ⑥: keyed against the NESTED "routing_decisions" list (one entry
    # per decision, not per route — resolve_object/get_network_overview
    # resolve a decision by its own no/name; a specific ROUTE within a
    # decision is addressed by set_route_relative_flow's own
    # decision_no+route_no params, not through this generic object_type
    # machinery).
    "routing_decision": {"shadow_key": "routing_decisions", "id_field": "no", "name_field": "name"},
    # v2 unit ⑦: keyed against the NESTED "vehicle_compositions" list (one
    # entry per composition, not per vehType) -- resolve_object/
    # get_network_overview resolve a composition by its own no/name; a
    # specific ENTRY within a composition is addressed by
    # set_vehicle_composition_share's own composition_no+veh_type_no params,
    # not through this generic object_type machinery (same split as
    # "routing_decision" above).
    "vehicle_composition": {"shadow_key": "vehicle_compositions", "id_field": "no", "name_field": "name"},
    # v2a unit ⑧: keyed against the NESTED "des_speed_decisions" list (one
    # entry per decision, not per vehicle-class assignment) -- same split as
    # "routing_decision"/"vehicle_composition" above. Real ``name`` attribute
    # exists in Vissim's own object model (unlike conflict areas) but is
    # empirically ALWAYS blank (verified: 584/584 real decisions across every
    # example network that has any) -- resolve_object/get_network_overview
    # still work fine off "no" alone when name is empty, same as every other
    # object type here.
    "des_speed_decision": {"shadow_key": "des_speed_decisions", "id_field": "no", "name_field": "name"},
    # QUEUE 3 unit 2: keyed against the NESTED "priority_rules" list (one
    # entry per rule, not per conflict marker) -- same split as
    # "routing_decision"/"vehicle_composition"/"des_speed_decision" above. A
    # specific MARKER within a rule is addressed by
    # set_priority_rule_gap_headway's own priority_rule_no+marker_index
    # params, not through this generic object_type machinery. ``name`` is
    # empirically ALWAYS blank on real priority rules (verified live and
    # statically across every real network probed), same as des_speed_decision.
    "priority_rule": {"shadow_key": "priority_rules", "id_field": "no", "name_field": "name"},
    # QUEUE (v2a) unit: keyed against the FLAT "vehicle_types" list (a
    # vehicle type has a genuine single-token identity, its own "no" --
    # unlike routing_decision/vehicle_composition/des_speed_decision/
    # priority_rule above, there is no nested-vs-flat split needed here; a
    # SPECIFIC reference within a vehicle type is addressed by
    # set_vehicle_type_reference's own vehicle_type_no+field params, not
    # through this generic object_type machinery).
    "vehicle_type": {"shadow_key": "vehicle_types", "id_field": "no", "name_field": "name"},
    # QUEUE 3 unit 6: keyed against the FLAT "driving_behaviors" list (a
    # driving behavior has a genuine single-token identity, its own "no") --
    # a specific link-behavior-type ASSIGNMENT is addressed by
    # set_link_behavior_type_driving_behavior's own link_behavior_type_no
    # (+optional veh_class_no) params, not through this generic object_type
    # machinery, mirroring vehicle_type's own split above.
    "driving_behavior": {"shadow_key": "driving_behaviors", "id_field": "no", "name_field": "name"},
}

_TOP_K = 5
_OVERVIEW_LIST_LIMIT = 30

# Exact > substring > token-overlap tiers. Token overlap is a fraction of the
# query's own tokens matched (0, 1], so it can never reach or exceed the
# substring tier's constant score.
_SCORE_EXACT = 3.0
_SCORE_SUBSTRING = 2.0


# ---------------------------------------------------------------------------
# Active-network session context (no paths ever reach the planner)
# ---------------------------------------------------------------------------

_active_inpx_override: Path | None = None


def set_active_inpx(path: str | Path | None) -> None:
    """Override the .inpx every tool in this module reads.

    ``None`` reverts to ``config.SIMULATION_INPX_PATH`` (the demo default) —
    which is what every session uses today, since neither the M1.3
    orchestrator nor the M1.4 router calls this. It exists as a test/ops
    override (tests use it to point at fixtures) until per-session dataset
    selection lands for the agent.
    """
    global _active_inpx_override
    _active_inpx_override = Path(path) if path is not None else None


def _active_inpx_path() -> Path:
    if _active_inpx_override is not None:
        return _active_inpx_override
    return Path(config.SIMULATION_INPX_PATH)


# ---------------------------------------------------------------------------
# Shadow model builder — links/nodes + vehicle_inputs + signals, mtime-cached
# ---------------------------------------------------------------------------


def _coerce_float_or_none(raw: Any) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_time_interval_start_s(raw: str | None) -> float | None:
    """``timeInt`` is a two-token attribute, e.g. ``"1 1200000"`` -- the
    SECOND token is the interval's start time in MILLISECONDS (verified
    live, ``backend/tools/vehicle_input_interval_probe.py``: the network's
    own ``<timeIntervalSet no="VEHICLEINPUT"><timeInterval start="1200"/>``
    ground truth matched ``timeInt``'s second token, 1200000 ms, exactly,
    across every real Vissim 2026 example network probed). The FIRST token
    was observed as a constant ``"1"`` on every ``<timeIntervalVehVolume>``
    in every one of the 25 real example networks and is not otherwise
    documented; deliberately not interpreted here."""
    if not raw:
        return None
    parts = raw.split()
    if len(parts) < 2:
        return None
    try:
        return float(parts[-1]) / 1000.0
    except (TypeError, ValueError):
        return None


def _parse_vehicle_inputs(root: ET.Element) -> list[dict[str, Any]]:
    """{no, name, link, volume, interval_count, volumes, composition_no,
    composition_name} + (only when the composition varies across intervals)
    {composition_nos, composition_varies_by_interval} per <vehicleInput>.

    ``composition_*`` -- sibling-disambiguation unit (2026-07-28). The real
    client model puts THREE vehicle inputs on the same link at the same
    coordinate, one per vehicle class (Murdoch Drive: 51 inputs in 17 groups
    of 3, e.g. link 620 carries 107/207/307, all named "Jennalup ST_WB"), and
    until this unit NOTHING in the shadow model could tell them apart -- so
    ``get_vehicle_inputs``, ``resolve_object``, the constraint catalog and the
    approval card all saw three indistinguishable objects. The composition
    IS the distinguishing datum, and it was already being read once (see
    :func:`_xml_vehicle_composition_refs`) but only in the reverse direction.
    Shared roll-up logic lives in ``services.inpx_parser.
    vehicle_input_composition_fields`` (imported, not duplicated) so the map
    geometry and the shadow model can never disagree; see that function's
    docstring for the per-interval-``vehComp`` reality and how a
    composition that VARIES across intervals is represented honestly rather
    than collapsed to interval 1.

    **Omit-when-uniform (2026-07-28 fix, this-tool-only -- the shared
    ``vehicle_input_composition_fields`` helper and its OWN map-geometry
    caller, ``inpx_parser._parse_vehicle_inputs``, keep emitting all four
    fields unconditionally; only this agent-facing shape changes).**
    Measured against the real 51-input client model at the planner's 12000-
    char recent-observation budget: an unfiltered ``get_vehicle_inputs`` call
    cost 42336 envelope chars and only 15/51 inputs (every one class ``1``)
    survived truncation -- 207/307 never appeared at all. ~7.0KB of that was
    ``volumes[i]["composition_no"]`` repeated once per interval (306
    repetitions of a value 0/51 real inputs ever vary), and another ~3.4KB
    was ``composition_nos``/``composition_varies_by_interval`` restating the
    SAME non-varying fact as ``composition_no`` alone. Neither per-interval
    ``composition_no`` (below) nor the two ``_nos``/``varies`` fields are
    emitted when ``composition_varies_by_interval`` is False -- the input-
    level ``composition_no``/``composition_name`` already say everything
    there is to say in that (0/51, but schema-legal) case. When it DOES
    vary, all of it is represented -- collapsing to interval 1 would be
    exactly the silent lie this project exists to prevent, not a size
    optimization.

    ``volume``/``interval_count``/``volumes`` -- v2 unit ⑤ (2026-07-24):
    ``volumes`` is now populated for real, one entry per
    ``<timeIntervalVehVolume>`` child IN DOCUMENT ORDER --
    ``{interval_no, from_s, volume, continuous}``, ``interval_no`` 1-based.
    Verified live (``backend/tools/vehicle_input_interval_probe.py``): COM's
    own ``Volume(i)``/``Cont(i)`` 1-based index matched XML document order on
    every real example network probed (Border Crossing Breitenau.DE,
    Roundabout Schenectady.US, Toll Plaza Camden.US) -- Vissim always keeps
    document order == chronological start-time order, so no separate
    start-time sort is needed or attempted. ``interval_count`` (``len(``
    ``volumes)``) CLOSES constraint #13's
    (``vehicle_input_multi_interval_write_gap``) CATALOG-GAP -- see
    ``constraints.py``'s docstring for that check.

    ``volume`` stays the exact same top-level field it always was
    (backward-compat, unchanged since M1.2): interval 1's volume, or
    ``None`` when the element carries no ``<timeIntervalVehVolume>`` child at
    all (the demo file's case -- ``interval_count`` is ``0``, ``volumes`` is
    ``[]`` there, not absent).
    """
    comp_names = vehicle_composition_names(root)
    result: list[dict[str, Any]] = []
    for vi in root.findall(".//vehicleInput"):
        comp_fields = vehicle_input_composition_fields(vi, comp_names)
        varies = bool(comp_fields.get("composition_varies_by_interval"))
        intervals: list[dict[str, Any]] = []
        for idx, vol_el in enumerate(vi.findall(".//timeIntervalVehVolume"), start=1):
            interval: dict[str, Any] = {
                "interval_no": idx,
                "from_s": _parse_time_interval_start_s(vol_el.get("timeInt")),
                "volume": _coerce_float_or_none(vol_el.get("volume")),
                "continuous": (vol_el.get("cont") or "").strip().lower() == "true",
            }
            if varies:
                # Only worth a byte when it can actually disagree with the
                # input-level composition_no -- see this function's own
                # "Omit-when-uniform" docstring section.
                interval["composition_no"] = vol_el.get("vehComp") or ""
            intervals.append(interval)
        volume = intervals[0]["volume"] if intervals else None
        entry: dict[str, Any] = {
            "no": vi.get("no", ""),
            "name": vi.get("name", ""),
            "link": vi.get("link", ""),
            "volume": volume,
            "interval_count": len(intervals),
            "volumes": intervals,
            "composition_no": comp_fields["composition_no"],
            "composition_name": comp_fields["composition_name"],
        }
        if varies:
            entry["composition_nos"] = comp_fields["composition_nos"]
            entry["composition_varies_by_interval"] = True
        result.append(entry)
    return result


def _parse_conflict_areas(root: ET.Element) -> list[dict[str, Any]]:
    """{no, link_a, link_b, link_pair, status, name} per ``<conflictArea>``.

    v2 unit (4): closes the M3.1 CATALOG-GAP on ``shadow_model["conflict_areas"]``
    (docs/phase2/DECISIONS.md, ``conflict_area_data_unavailable``). Verified
    live against Vissim 2026 (``backend/tools/conflict_area_probe.py``, Urban
    Intersection Beijing.CN, 224 real conflict areas): the .inpx XML DOES
    serialize conflict areas -- ``no``/``linkA``/``linkB``/``status`` --
    matching the COM ``AttValue`` ids of the same names exactly (unlike the
    CycTm/NodeRes landmines). Conflict area NUMBERS are NOT stable identity
    across geometry edits (docs/phase2/00-EXECUTION-GUIDE.md SS3.2), so every
    entry also carries ``link_pair`` -- the unordered-in-spirit (but here
    always ``(link_a, link_b)`` ordered) pair callers should snapshot/key on
    instead of the number when comparing across edits, per the catalog's own
    "Do" guidance. ``name`` is a synthetic, display-only "Link A vs Link B"
    label (conflict areas have no name attribute of their own) so
    ``resolve_object``/``get_network_overview`` can list them usefully.

    The demo network's ``.inpx`` has no ``<conflictArea>`` elements at all
    (verified: zero matches), so this returns ``[]`` there -- not absent,
    not ``None`` -- which is exactly the "no passive areas" shape
    ``conflict_area_data_unavailable`` already treats as silent (``not
    conflict_areas`` is ``True`` for an empty list too).
    """
    result: list[dict[str, Any]] = []
    for ca in root.findall(".//conflictArea"):
        link_a = ca.get("linkA", "")
        link_b = ca.get("linkB", "")
        result.append(
            {
                "no": ca.get("no", ""),
                "link_a": link_a,
                "link_b": link_b,
                "link_pair": (link_a, link_b),
                "status": ca.get("status", ""),
                "name": f"Link {link_a} vs Link {link_b}",
            }
        )
    return result


def _parse_link_lanes(root: ET.Element) -> list[dict[str, Any]]:
    """FLAT ``{id, link_id, lane_index, width, blocked_vehicle_classes}`` per
    ``<lane>`` -- v2b unit (1)'s shadow-model seed for ``set_lane_closure``.

    Mirrors the routes/vehicle_composition_entries precedent: a lane has no
    single-token identity of its own in the .inpx (``<lane width="..."/>``,
    no ``no``/``id`` attribute at all -- confirmed against every real Vissim
    2026 example network), so ``lane_index`` is the SAME 1-based ordinal
    (document position within ``<lanes>``) live COM's own ``Lane.
    AttValue("Index")`` returns (``backend/tools/probe_lane_closure.py``) --
    the .inpx's document order and COM's ``Index`` numbering agree by
    construction, not by convention alone.

    ``blocked_vehicle_classes`` reads a real-but-previously-unparsed .inpx
    shape (verified live, same probe): a lane with ANY blocked class
    serializes as ``<lane ...><blockedVehClasses><intObjectRef key="10"/>
    ...</blockedVehClasses></lane>`` -- a CHILD ELEMENT, one ``<intObjectRef>``
    per blocked class, not a plain attribute (unlike ``conflictArea``'s
    ``status``). A lane with no closure omits ``<blockedVehClasses>``
    entirely (every real example network today), which is the empty-list
    case here -- ``[]``, never ``None``, so the target-persisted postwrite
    check's "not_persisted" semantics work exactly like ``status``/``volume``.
    """
    result: list[dict[str, Any]] = []
    for link in root.findall(".//links/link"):
        link_id = link.get("no", "")
        lanes_el = link.find("lanes")
        if lanes_el is None:
            continue
        for lane_index, lane in enumerate(lanes_el.findall("lane"), start=1):
            blocked_el = lane.find("blockedVehClasses")
            blocked = (
                [ref.get("key", "") for ref in blocked_el.findall("intObjectRef") if ref.get("key")]
                if blocked_el is not None
                else []
            )
            result.append(
                {
                    "id": f"{link_id}.{lane_index}",
                    "link_id": link_id,
                    "lane_index": lane_index,
                    "width": float(lane.get("width", "3.5") or 3.5),
                    "blocked_vehicle_classes": blocked,
                }
            )
    return result


# ---------------------------------------------------------------------------
# Q4 unit (2): measurement-infrastructure placement -- queue counters,
# data-collection points/measurements, travel-time sections. Shapes verified
# live/statically across every real Vissim 2026 example network that carries
# one (backend/tools/probe_measurement_infra.py, Toll Plaza Camden.US --
# FEET display -- primary; cross-checked against Beijing/Rome/Boise, both
# METERS-display): a queue counter is link+pos (no lane, mirrors the
# pre-existing ``_parse_link_pos_elements`` convention in inpx_parser.py); a
# data-collection point is the SAME lane-TOKEN+pos shape reducedSpeedArea/
# priorityRule already use (:func:`_parse_lane_token`); a data-collection
# MEASUREMENT is a separate, referencing object grouping 1..N points by id
# via ``<dataCollectionPoints><intObjectRef key="..."/></dataCollectionPoints>``
# (the SAME ``intObjectRef`` convention ``_parse_link_lanes`` already found
# for ``blockedVehClasses``); a travel-time section has two ``<start
# link="" pos=""/>``/``<end .../>`` child elements -- the FIRST of
# ``inpx_parser._resolve_tt_endpoint``'s three guessed shapes, now confirmed
# as the only one any real Vissim export actually uses.
#
# **pos is always METRES in the raw XML**, regardless of the network's own
# display unit -- the THIRD confirmation of the twice-documented display-unit
# landmine (docs/phase2/DECISIONS.md 2026-07-27/2026-07-31): COM
# ``AttValue('Pos')`` on these objects AND the native ``Add*`` methods' own
# ``Pos``/``StartPos``/``EndPos`` params both read/accept the network's
# ``NetPara.UnitLenShort`` display unit (measured ratio 3.280840 == 1/0.3048
# on a FEET network, both directions), but the SAVED .inpx attribute is
# always metres -- see ``vissim_com.VissimController._net_unit_length`` for
# the conversion helpers ``add_queue_counter``/etc. reuse. Parsing straight
# from XML here needs no conversion at all.
# ---------------------------------------------------------------------------


def _parse_queue_counters(root: ET.Element) -> list[dict[str, Any]]:
    """FLAT ``{no, name, link_id, pos}`` per ``<queueCounter>``."""
    result: list[dict[str, Any]] = []
    for el in root.findall(".//queueCounters/queueCounter"):
        result.append(
            {
                "no": el.get("no", ""),
                "name": el.get("name", "") or "",
                "link_id": el.get("link", "") or "",
                "pos": _coerce_float_or_none(el.get("pos")),
            }
        )
    return result


def _parse_data_collection_points(root: ET.Element) -> list[dict[str, Any]]:
    """FLAT ``{no, name, link_id, lane_index, pos}`` per
    ``<dataCollectionPoint>`` -- lane is the two-token ``"<linkNo> <laneNo>"``
    XML form (:func:`_parse_lane_token`), matching COM's own ``AddDataCollectionPoint``
    ``Lane`` param semantics (resolved through ``VissimController._lane_by_index``,
    itself indexed the SAME 1-based ordinal ``_parse_link_lanes`` already uses)."""
    result: list[dict[str, Any]] = []
    for el in root.findall(".//dataCollectionPoints/dataCollectionPoint"):
        link_id, lane_no = _parse_lane_token(el.get("lane"))
        result.append(
            {
                "no": el.get("no", ""),
                "name": el.get("name", "") or "",
                "link_id": link_id,
                "lane_index": int(lane_no) if lane_no.isdigit() else None,
                "pos": _coerce_float_or_none(el.get("pos")),
            }
        )
    return result


def _parse_data_collection_measurements(root: ET.Element) -> list[dict[str, Any]]:
    """FLAT ``{no, name, point_nos}`` per ``<dataCollectionMeasurement>`` --
    ``point_nos`` is every referenced ``<dataCollectionPoint>`` id, in
    document order (empty list = a measurement wired to zero points, a legal
    but useless-for-results state -- Vissim's own COM ``AddDataCollectionMeasurement``
    creates exactly this before ``add_data_collection_measurement`` wires
    membership via ``SetAttValue``)."""
    result: list[dict[str, Any]] = []
    for el in root.findall(".//dataCollectionMeasurements/dataCollectionMeasurement"):
        point_nos = [
            ref.get("key", "")
            for ref in el.findall("dataCollectionPoints/intObjectRef")
            if ref.get("key")
        ]
        result.append(
            {
                "no": el.get("no", ""),
                "name": el.get("name", "") or "",
                "point_nos": point_nos,
            }
        )
    return result


def _parse_travel_time_sections(root: ET.Element) -> list[dict[str, Any]]:
    """FLAT ``{no, name, from_link, from_pos, to_link, to_pos}`` per
    ``<vehicleTravelTimeMeasurement>``. Distinct from ``inpx_parser.py``'s
    identically-purposed ``traveltime_sections``/``traveltime_sections_geo``
    lists (a different module, geometry-resolved for the frontend map, not
    the agent's own preflight shadow model) -- deliberately NOT reused here
    to keep this module's own id-bearing/reference-integrity registry keys
    (``services.inpx_structure``) independent of the map-rendering module's
    schema evolution."""
    result: list[dict[str, Any]] = []
    for el in root.findall(".//vehicleTravelTimeMeasurements/vehicleTravelTimeMeasurement"):
        start = el.find("start")
        end = el.find("end")
        result.append(
            {
                "no": el.get("no", ""),
                "name": el.get("name", "") or "",
                "from_link": (start.get("link") or "") if start is not None else "",
                "from_pos": _coerce_float_or_none(start.get("pos")) if start is not None else None,
                "to_link": (end.get("link") or "") if end is not None else "",
                "to_pos": _coerce_float_or_none(end.get("pos")) if end is not None else None,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Q4 unit (4): signal-head + detector placement -- genesis milestone (b)'s
# "hands". Shapes verified live/statically
# (backend/tools/probe_signal_heads_detectors.py, static re-scan of all 25
# real Vissim 2026 example networks + live COM against Urban Intersection
# Beijing.CN/Toll Plaza Camden.US): a signal head is the SAME lane-TOKEN+pos
# shape reducedSpeedArea/priorityRule/dataCollectionPoint already use
# (:func:`_parse_lane_token`), PLUS one composite signal-group reference,
# ``sg="<sc_no> <sg_no>"`` (space-separated in the XML -- NOT the hyphenated
# ``"1-22"`` form live COM's own ``AttValue('SG')`` readback uses, same
# space-vs-hyphen split ``_parse_lane_token``'s own docstring already
# documents for ``Lane``). A detector is the same lane-TOKEN+pos+length shape
# (mirrors ``reducedSpeedArea``'s own ``with_length`` convention in
# ``inpx_parser.py``), PLUS two FLAT controller-wiring attributes, ``sc``
# (plain controller number) and ``portNo`` (port/channel number) -- resolving
# the pre-existing catalog note "port numbers bind detectors to external
# controllers" (constraints.py's #35 docstring) into an exact schema.
# ---------------------------------------------------------------------------


def _parse_signal_heads(root: ET.Element) -> list[dict[str, Any]]:
    """FLAT ``{no, name, link, lane_index, pos, sc, sg}`` per
    ``<signalHead>``. ``sc``/``sg`` split the single composite ``sg="<sc_no>
    <sg_no>"`` attribute -- a signal head carries NO separate controller
    attribute of its own (the signal group's own identity is already scoped
    to its controller)."""
    result: list[dict[str, Any]] = []
    for el in root.findall(".//signalHeads/signalHead"):
        link_id, lane_no = _parse_lane_token(el.get("lane"))
        sc_no, sg_no = _parse_lane_token(el.get("sg"))
        result.append(
            {
                "no": el.get("no", ""),
                "name": el.get("name", "") or "",
                "link": link_id,
                "lane_index": int(lane_no) if lane_no.isdigit() else None,
                "pos": _coerce_float_or_none(el.get("pos")),
                "sc": sc_no,
                "sg": sg_no,
            }
        )
    return result


def _parse_detectors(root: ET.Element) -> list[dict[str, Any]]:
    """FLAT ``{no, name, link, lane_index, pos, length, sc, port_no}`` per
    ``<detector>``. ``sc``/``port_no`` are the detector's controller-wiring
    attributes -- BOTH flat scalars (unlike a signal head's composite ``sg``
    token), empty/``None`` when the detector is unwired (never observed on a
    real example network, but not assumed either -- see
    ``vissim_com.VissimController.add_detector``'s own docstring)."""
    result: list[dict[str, Any]] = []
    for el in root.findall(".//detectors/detector"):
        link_id, lane_no = _parse_lane_token(el.get("lane"))
        port_raw = (el.get("portNo") or "").strip()
        result.append(
            {
                "no": el.get("no", ""),
                "name": el.get("name", "") or "",
                "link": link_id,
                "lane_index": int(lane_no) if lane_no.isdigit() else None,
                "pos": _coerce_float_or_none(el.get("pos")),
                "length": _coerce_float_or_none(el.get("length")),
                "sc": (el.get("sc") or "").strip(),
                "port_no": int(port_raw) if port_raw.isdigit() else None,
            }
        )
    return result


def _parse_evaluation_config(root: ET.Element) -> dict[str, Any]:
    """{present, net_perf_eval_act, link_eval_act, queue_eval_act,
    ln_chg_eval_act, eval_interval_s} from the .inpx's top-level
    ``<evaluation>`` element -- health-check unit (2026-07-28), closing the
    "is any evaluation actually ARMED" half of catalog §5's "Evaluations are
    not retroactive" that constraints.py's existing ``evaluation_not_
    configured_before_run`` (#15) could only ever answer POSTRUN, from a live
    job's ``run_result`` (see that constraint's own docstring -- it has no
    shadow-model path at all). This is the STATIC counterpart: the .inpx
    itself carries the evaluation-armed flags as plain attributes on one
    element, parseable with no COM and no run (verified against the real
    Urban Intersection Beijing.CN_31360 example:
    ``<evaluation netPerfEvalAct="true" linkEvalAct="..." queueEvalAct="..."
    lnChgEvalAct="..." evalInt="600" .../>``). ``present=False`` (every other
    key omitted) when the .inpx carries no ``<evaluation>`` element at all --
    genuinely true of the bundled demo network (``data/practice.inpx``), not
    a fabricated gap.
    """
    el = root.find(".//evaluation")
    if el is None:
        return {"present": False}

    def _bool_attr(attr: str) -> bool | None:
        raw = el.get(attr)
        if raw is None:
            return None
        return raw.strip().lower() == "true"

    return {
        "present": True,
        "net_perf_eval_act": _bool_attr("netPerfEvalAct"),
        "link_eval_act": _bool_attr("linkEvalAct"),
        "queue_eval_act": _bool_attr("queueEvalAct"),
        "ln_chg_eval_act": _bool_attr("lnChgEvalAct"),
        "eval_interval_s": _coerce_float_or_none(el.get("evalInt")),
    }


def _parse_rel_flow(raw: str | None) -> list[dict[str, Any]]:
    """[{interval_no, from_s, rel_flow}] from a ``<vehicleRouteStatic
    relFlow="...">`` attribute, one entry per referenced time interval, in
    document order.

    ``relFlow`` is a COMMA-separated list of ``"<const> <start_ms>:<value>"``
    fragments -- verified live and via a network-wide static scan across all
    25 real Vissim 2026 example networks
    (``backend/tools/routing_decision_probe.py``): Beijing's single-interval
    routes read ``"2 0:245"``; LRT Priority Luxembourg.LU's two-interval
    routes read ``"2 0:45, 2 5400000:45"`` -- both match the network's own
    ``<timeIntervalSet no="VEHICLEROUTESTATIC">`` start times (seconds)
    exactly once the millisecond token is divided by 1000, the SAME encoding
    convention vehicle inputs' ``timeInt`` uses (v2 unit ⑤). The leading
    constant token (``"2"`` on every fragment, every real example network,
    every interval) is left uninterpreted, matching the same precedent
    ``_parse_time_interval_start_s`` established for vehicle inputs' own
    constant leading ``"1"`` token.
    """
    if not raw:
        return []
    result: list[dict[str, Any]] = []
    for idx, fragment in enumerate(raw.split(","), start=1):
        fragment = fragment.strip()
        if not fragment:
            continue
        parts = fragment.split()
        if len(parts) < 2 or ":" not in parts[-1]:
            continue
        start_token, _, value_token = parts[-1].partition(":")
        start_ms = _coerce_float_or_none(start_token)
        result.append(
            {
                "interval_no": idx,
                "from_s": start_ms / 1000.0 if start_ms is not None else None,
                "rel_flow": _coerce_float_or_none(value_token),
            }
        )
    return result


def _parse_routing_decisions(root: ET.Element, link_ids: set[str]) -> list[dict[str, Any]]:
    """{no, link, name, routes: [{route_no, dest_link, dest_pos, rel_flow,
    interval_count, rel_flows, link_seq, disconnected}]} per
    ``<vehicleRoutingDecisionStatic>``.

    v2 unit ⑥ (2026-07-24). COM surface verified live
    (``backend/tools/routing_decision_probe.py``, Beijing.CN clone):
    ``Net.VehicleRoutingDecisionsStatic`` (``GetAll()`` count 12 == the XML
    element count) -> per-decision ``.VehRoutSta`` child collection (matches
    the XML child tag exactly; count matched per-decision route counts too)
    -> per-route ``AttValue('No'/'DestLink'/'DestPos')`` all read directly
    (plain attributes, spelled exactly as the XML). ``RelFlow`` has NO scalar
    COM form at all -- ``AttValue('RelFlow')`` (no index) RAISES ("Sub-
    attribute not specified"); only the INDEXED form works
    (``AttValue('RelFlow(1)')`` -> ``245.0``, matching the first fragment
    :func:`_parse_rel_flow` extracts from the same route's XML) -- relFlow is
    genuinely interval-indexed like Volume(i), confirmed both statically and
    live. A second live probe on a genuinely 2-interval route (LRT Priority
    Luxembourg.LU, decision 1 route 1) confirmed ``RelFlow(2)`` reads/writes
    exactly like ``RelFlow(1)`` with NO Cont-flag equivalent (every interval
    always carries an explicit value; unlike vehicle inputs there is nothing
    to "clear" first), and that ``RelFlow(3)`` on that 2-interval route FAILS
    CLOSED for both read and write ("Object 3 not found") -- matching
    Volume(i)'s own per-object-indexed fail-closed behavior (v2 unit ⑤), not
    a new data point.

    ``disconnected`` is REAL (v2 unit ⑥ closes #31's
    ``route_integrity_unverifiable`` CATALOG-GAP): every ``<vehicleRouteStatic>``
    carries a ``<linkSeq><intObjectRef key="..."/></linkSeq>`` child listing
    the link/connector ids the route traverses -- verified live that every
    key already matches a ``services.inpx_parser.parse_network`` link id
    (plain links and connectors share one id space) -- so a route is
    ``disconnected`` iff any of its own ``link_seq`` keys is NOT a real link
    id in ``link_ids`` (this network's own current links, passed in by the
    caller so the check is against THIS shadow model's own links, never a
    stale snapshot).

    ``rel_flow``/``interval_count``/``rel_flows`` mirror the ``volume``/
    ``interval_count``/``volumes`` shape v2 unit ⑤ established for vehicle
    inputs: ``rel_flow`` is interval 1's value (or ``None`` when ``relFlow``
    is empty/absent, which was never observed on a real network but is
    handled defensively), ``rel_flows`` is the full per-interval list.
    """
    result: list[dict[str, Any]] = []
    for dec in root.findall(".//vehicleRoutingDecisionStatic"):
        routes: list[dict[str, Any]] = []
        for r in dec.findall(".//vehicleRouteStatic"):
            rel_flows = _parse_rel_flow(r.get("relFlow"))
            rel_flow = rel_flows[0]["rel_flow"] if rel_flows else None
            link_seq = [ref.get("key", "") for ref in r.findall(".//linkSeq/intObjectRef")]
            disconnected = any(key not in link_ids for key in link_seq) if link_seq else False
            routes.append(
                {
                    "route_no": r.get("no", ""),
                    "dest_link": r.get("destLink", ""),
                    "dest_pos": _coerce_float_or_none(r.get("destPos")),
                    "rel_flow": rel_flow,
                    "interval_count": len(rel_flows),
                    "rel_flows": rel_flows,
                    "link_seq": link_seq,
                    "disconnected": disconnected,
                }
            )
        result.append(
            {
                "no": dec.get("no", ""),
                "link": dec.get("link", ""),
                "name": dec.get("name", ""),
                "routes": routes,
            }
        )
    return result


def _flatten_routes(routing_decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A flat, one-entry-per-route view of ``routing_decisions``, keyed by a
    synthesized ``"{decision_no}.{route_no}"`` ``id`` -- there is no
    single-token route identity in Vissim itself (a route's real identity is
    the (decision, route) pair; docs/phase2/DECISIONS.md's v2 unit ⑥ probe
    entry).

    This is the exact shape ``constraints.py``'s dormant CATALOG-GAP checks
    already expected from their OWN docstrings before this unit existed
    (``shadow_model["routes"]``, entries carrying ``id``/``disconnected``/
    ``manually_scaled_this_turn``) -- matching those field names, not
    inventing a new contract, is what wakes ``route_integrity_unverifiable``
    (#31) for real. Every route field (``rel_flow``/``rel_flows``/
    ``interval_count``/``link_seq``/``disconnected``) is carried through
    unchanged so ``guardrails.py``'s postwrite machinery (the
    nothing-else-moved diff + the target-persisted check) has a real,
    id-keyed object list to compare, exactly like every other write-tool
    target object class.

    ``manually_scaled_this_turn`` is HARD-CODED ``False`` here and stays that
    way until further notice: it is turn-scoped ephemeral state ("was this
    SPECIFIC route touched by set_route_relative_flow earlier in THIS SAME
    agent turn") with no representation anywhere in the .inpx this function
    parses -- only ``services.agent.orchestrator``'s turn loop could supply
    it (out of this unit's file scope per the task brief: orchestrator.py is
    not listed in scope, and threading per-turn "which routes did I already
    touch" state through preflight would be a real, orchestrator-level
    change, not a shadow-model parsing one). #14
    (``demand_scale_route_double_count_gap``) therefore stays open — see the
    drafted DECISIONS.md entry.
    """
    flat: list[dict[str, Any]] = []
    for dec in routing_decisions:
        for route in dec["routes"]:
            entry = dict(route)
            entry["id"] = f"{dec['no']}.{route['route_no']}"
            entry["decision_no"] = dec["no"]
            entry["manually_scaled_this_turn"] = False
            flat.append(entry)
    return flat


def _xml_vehicle_composition_refs(root: ET.Element) -> dict[str, list[str]]:
    """{composition_no: [vehicle_input_no, ...]} -- which vehicle inputs
    reference each composition, scanning EVERY interval's ``vehComp``
    attribute, not just interval 1 (v2 unit ⑦ probe finding: ``vehComp``
    lives on each ``<timeIntervalVehVolume>`` child, not once on
    ``<vehicleInput>`` itself, so a multi-interval input could in principle
    reference different compositions per interval). Order preserves each
    vehicle input's own first-referencing-interval order; a given input
    appears at most once per composition even if multiple intervals
    reference it."""
    refs: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for vi in root.findall(".//vehicleInput"):
        vi_no = vi.get("no", "")
        for tv in vi.findall(".//timeIntervalVehVolume"):
            comp_no = tv.get("vehComp")
            if not comp_no:
                continue
            already = seen.setdefault(comp_no, set())
            if vi_no in already:
                continue
            already.add(vi_no)
            refs.setdefault(comp_no, []).append(vi_no)
    return refs


def _parse_vehicle_compositions(root: ET.Element) -> list[dict[str, Any]]:
    """{no, name, entries: [{veh_type, des_speed_distr, rel_flow}],
    referencing_vehicle_input_nos} per ``<vehicleComposition>``.

    v2 unit ⑦ (2026-07-27). COM surface verified live
    (``backend/tools/probe_vehicle_compositions.py``, Urban Intersection
    Beijing.CN clone): ``Net.VehicleCompositions`` (``GetAll()`` count 3 ==
    the XML element count) -> per-composition ``.VehCompRelFlows`` child
    collection (matches the XML child tag ``vehCompRelFlows`` exactly, count
    matched per-composition entry counts too) -> per-entry
    ``AttValue('VehType'/'DesSpeedDistr'/'RelFlow')`` all read directly
    (plain attributes, spelled with Vissim's usual capitalize-each-word
    convention over the XML's own ``vehType``/``desSpeedDistr``/``relFlow``).
    ``RelFlow`` is SCALAR ONLY -- ``AttValue('RelFlow(1)')`` RAISES ("Too
    many sub-attributes: 1"), unlike route ``RelFlow`` (v2 unit ⑥) -- there
    is no time-interval dimension for vehicle-composition relative flows at
    all.

    **Entry identity: the vehType number, but with NO ``ItemByKey``.**
    ``<vehicleCompositionRelativeFlow>`` carries no ``no`` attribute of its
    own (``AttValue('No')`` raises "Could not find attribute with ID NO");
    the static scan (grepping all 21 real Vissim 2026 example networks that
    carry any vehicle compositions, 73 total) never found a duplicate
    ``vehType`` within one composition, so ``vehType`` is a safe entry key
    -- BUT ``IVehicleCompositionRelativeFlowContainer`` (confirmed via both
    the type-library scan and live ``hasattr``) exposes only
    ``GetAll``/``FilteredBy``/``GetFilteredSet``/
    ``AddVehicleCompositionRelativeFlow``/
    ``RemoveVehicleCompositionRelativeFlow``/``SetMulti*`` -- genuinely no
    ``ItemByKey``, unlike every other per-object collection this codebase
    has probed (conflict areas, routes, vehicle inputs, signal controllers
    all support it). ``FilteredBy('VehType=<n>')`` was also tried live and
    rejected by Vissim's own formula engine. ``vissim_com.
    VissimController._vehicle_composition_entry_by_veh_type`` therefore
    scans ``GetAll()`` and matches client-side.

    **Normalization semantics (the central empirical finding, verified
    live) -- CORRECTS a prior wrong assumption.** ``constraints.py``'s old
    forward-declared CATALOG-GAP placeholder (``vehicle_composition_share_
    redistribution_gap``, added before this unit, before any real COM
    verification existed) assumed vehicle-composition shares are
    "normalized to 100%" such that raising one class's share "automatically
    lowers other classes' shares" -- i.e. that Vissim renormalizes sibling
    entries ON WRITE. Verified FALSE live: writing composition no=1's
    vehType=100 entry from 0.85 to 5.85 on a real 4-entry composition left
    every sibling entry's OWN stored ``RelFlow`` (vehType 190/200/300, each
    0.05) completely UNCHANGED, both immediately after the write and after
    a ``SaveNetAs`` + fresh COM reload. ``RelFlow`` is a RAW weight -- EXACTLY
    like route ``RelFlow`` (v2 unit ⑥) -- and each vehicle type's ACTUAL
    simulated composition share is only computed at RUN time (and for
    display) as this entry's ``RelFlow`` divided by the SUM of every entry's
    ``RelFlow`` in the SAME composition. The fact that every real example
    network's authored values happen to already sum to 1.0 (e.g.
    0.85+0.05+0.05+0.05) is an AUTHORING CONVENTION for readability, not an
    enforced invariant -- the same "plausible-looking guess turned out
    wrong" class of landmine as ``CycTm``/``NodeRes`` in
    docs/phase2/DECISIONS.md. ``constraints.py``'s check was corrected in
    this same unit once this was verified (see that module's own comment at
    the check's registration).

    ``referencing_vehicle_input_nos`` is real (v2 unit ⑦ closes the "which
    vehicle inputs use this composition" gap needed for the impact
    warning): every vehicle input whose ``vehComp`` (on ANY of its
    intervals, not just interval 1 -- see :func:`_xml_vehicle_composition_refs`)
    names this composition's ``no``.
    """
    refs = _xml_vehicle_composition_refs(root)
    result: list[dict[str, Any]] = []
    for comp in root.findall(".//vehicleComposition"):
        no = comp.get("no", "")
        entries: list[dict[str, Any]] = []
        for rf in comp.findall(".//vehicleCompositionRelativeFlow"):
            entries.append(
                {
                    "veh_type": rf.get("vehType", ""),
                    "des_speed_distr": rf.get("desSpeedDistr", ""),
                    "rel_flow": _coerce_float_or_none(rf.get("relFlow")),
                }
            )
        result.append(
            {
                "no": no,
                "name": comp.get("name", ""),
                "entries": entries,
                "referencing_vehicle_input_nos": refs.get(no, []),
            }
        )
    return result


def _flatten_vehicle_composition_entries(vehicle_compositions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A flat, one-entry-per-(composition, vehType) view of
    ``vehicle_compositions``, keyed by a synthesized
    ``"{composition_no}.{veh_type}"`` ``id`` -- mirrors
    :func:`_flatten_routes`'s precedent (v2 unit ⑥) exactly, for the same
    reason: a composition entry has no single-token identity in Vissim
    itself (its real identity is the (composition, vehType) pair; see
    :func:`_parse_vehicle_compositions`'s docstring), so this is the shape
    ``guardrails.py``'s postwrite machinery (the nothing-else-moved diff +
    the target-persisted check) needs to compare a real, id-keyed object
    list, exactly like every other write-tool target object class.
    """
    flat: list[dict[str, Any]] = []
    for comp in vehicle_compositions:
        for entry in comp["entries"]:
            flat_entry = dict(entry)
            flat_entry["id"] = f"{comp['no']}.{entry['veh_type']}"
            flat_entry["composition_no"] = comp["no"]
            flat.append(flat_entry)
    return flat


def _parse_vehicle_class_names(root: ET.Element) -> dict[str, str]:
    """{no: name} from ``<vehicleClasses><vehicleClass no="X" name="Y">`` --
    used only to enrich :func:`_parse_des_speed_decisions`'s per-assignment
    ``veh_class_name`` field; not exposed as its own shadow-model key (no
    tool needs a standalone vehicle-class list yet)."""
    return {vc.get("no", ""): vc.get("name", "") for vc in root.findall(".//vehicleClasses/vehicleClass")}


#: Points shown per distribution on an UNFILTERED/filtered
#: get_des_speed_distributions call -- the real max observed across all 25
#: Vissim 2026 example networks' 559 real speedDistribution elements is 20
#: points (histogram: {2: 284, 3: 29, 4: 64, 5: 15, 6: 12, 8: 28, 9: 4, 10:
#: 21, 11: 44, 14: 22, 15: 22, 19: 7, 20: 7}) -- 30 comfortably covers every
#: real distribution with headroom, so truncation should never fire on real
#: data today; it exists as a bound, not a tuned-to-the-edge limit.
_SPEED_DISTRIBUTION_POINTS_CAP = 30

#: Bounded candidate-id list per reference category (des_speed_decisions /
#: vehicle_compositions / reduced_speed_areas) in each distribution's
#: ``referenced_by`` -- mirrors constraints.py's own ``_CANDIDATE_CAP``
#: reasoning (suggestion/reference lists reach LLM prompts; bounded envelopes
#: are a standing rule, QUEUE 3 unit 1's fix). The COUNT is always the real,
#: untruncated total; only the enumerated id list is capped.
_SPEED_DISTRIBUTION_REFERENCE_CAP = 10

#: Real, non-vehicle-composition/non-desSpeedDecision XML element types that
#: ALSO carry a ``desSpeedDistr`` attribute, found by a network-wide static
#: grep across all 25 real Vissim 2026 example networks (public-transport
#: lines, pedestrian composition relative flows, area-behavior-type time
#: profiles, dynamic-assignment per-vehicle-class distributions -- 434
#: combined occurrences: ptLine 159, pedestrianCompositionRelativeFlow 136,
#: vehClassDynAssignDesSpeedDistribution 112, areaBehaviorTypeElement 7).
#: None of these features have a shadow-model object representation of their
#: own (public transport, pedestrians, area behavior types, and dynamic
#: assignment are all out of this unit's scope) -- rather than silently
#: omitting them from the reference count entirely (dishonest: the task
#: brief's own "and anything else reference each distribution" ask), this
#: unit counts them GENERICALLY by tag name (no per-object id, since there is
#: no id-bearing shadow object to name) so the shared-collection warning's
#: total is honest even though these categories can't be enumerated by id.
_OTHER_SPEED_DISTRIBUTION_REFERENCE_TAGS = (
    "ptLine",
    "pedestrianCompositionRelativeFlow",
    "areaBehaviorTypeElement",
    "vehClassDynAssignDesSpeedDistribution",
)


def _parse_speed_distribution_points(distribution_el: ET.Element) -> list[dict[str, float | None]]:
    """[{speed_kmh, cum_fraction}, ...] per ``<speedDistrDatPts>
    <speedDistributionDataPoint fx="..." x="..."/></speedDistrDatPts>``, in
    document order.

    QUEUE 3 (v2a) unit 3 (2026-07-29). ``x`` is ALWAYS the speed in
    kilometers per hour in the .inpx REGARDLESS of the network's own
    ``netPara@unitSpeed`` display setting -- verified statically (a
    "MILESPERHOUR"-display network's own distribution named "55 mph" carries
    raw XML points x="72.42"/x="104.61", and 104.61 / 1.609344 = 65.0 mph,
    the distribution's own upper-bound label) AND live over COM
    (``backend/tools/probe_des_speed_curves.py``: writing X=40/X=80 via
    SetAttValue/ReplaceAll on that SAME MILESPERHOUR network persisted to the
    saved XML as x="64.37376"/x="128.74752" -- EXACTLY 40/80 *
    1.609344, the mph-to-km/h conversion factor). This parser reads straight
    from XML (never COM), so it always returns genuine km/h with no
    conversion needed -- the COM-side conversion this same finding requires
    lives entirely in ``vissim_com.VissimController.set_des_speed_
    distribution_points``/``get_des_speed_distribution_points`` (the write
    path), never here.

    ``fx`` is the cumulative fraction, unitless, verified live to need NO
    conversion regardless of display unit.
    """
    points: list[dict[str, float | None]] = []
    for pt in distribution_el.findall("speedDistrDatPts/speedDistributionDataPoint"):
        points.append(
            {
                "speed_kmh": _coerce_float_or_none(pt.get("x")),
                "cum_fraction": _coerce_float_or_none(pt.get("fx")),
            }
        )
    return points


def _bounded_reference_ids(ids: list[str]) -> dict[str, Any]:
    """{count, ids, truncated} for one ``referenced_by`` category -- ``count``
    is always the real, untruncated total; ``ids`` is capped at
    ``_SPEED_DISTRIBUTION_REFERENCE_CAP``."""
    capped = ids[:_SPEED_DISTRIBUTION_REFERENCE_CAP]
    return {"count": len(ids), "ids": capped, "truncated": len(ids) > len(capped)}


def _reduced_speed_area_distribution_refs(root: ET.Element) -> dict[str, list[str]]:
    """{distribution_no: [reduced_speed_area_no, ...]} from
    ``<reducedSpeedAreas><reducedSpeedArea><vehClassSpeedRed>
    <vehClassSpeedReduction desSpeedDistr="..."/>`` -- the SAME distribution
    object type ``set_reduced_speed_area`` (v2 unit ②) already injects into
    (see :func:`_parse_speed_distributions`'s own docstring), read back here
    ONLY for reference counting (this unit does not build a full
    reduced-speed-area shadow object list -- that is out of scope; a future
    unit reading reduced speed areas back from XML should replace this
    reference-only scan with a real parser feeding the same shadow key)."""
    refs: dict[str, list[str]] = {}
    for area in root.findall(".//reducedSpeedAreas/reducedSpeedArea"):
        area_no = area.get("no", "")
        if not area_no:
            continue
        for reduction in area.findall(".//vehClassSpeedReduction"):
            distr_no = (reduction.get("desSpeedDistr") or "").strip()
            if not distr_no:
                continue
            owners = refs.setdefault(distr_no, [])
            if area_no not in owners:
                owners.append(area_no)
    return refs


def _other_speed_distribution_reference_counts(root: ET.Element) -> dict[str, dict[str, int]]:
    """{distribution_no: {tag_name: count}} for every real XML element type
    (other than desSpeedDecision/vehicleCompositionRelativeFlow/
    reducedSpeedArea, all counted with their own id-bearing owners
    elsewhere) that ALSO carries a ``desSpeedDistr`` attribute -- see
    :data:`_OTHER_SPEED_DISTRIBUTION_REFERENCE_TAGS`'s own docstring for the
    static-scan evidence. Counted generically by tag (no owner id) since none
    of these features have a shadow-model object of their own."""
    counts: dict[str, dict[str, int]] = {}
    for tag in _OTHER_SPEED_DISTRIBUTION_REFERENCE_TAGS:
        for el in root.iter(tag):
            distr_no = (el.get("desSpeedDistr") or "").strip()
            if not distr_no:
                continue
            per_distr = counts.setdefault(distr_no, {})
            per_distr[tag] = per_distr.get(tag, 0) + 1
    return counts


def _parse_speed_distributions(
    root: ET.Element,
    des_speed_decisions: list[dict[str, Any]] | None = None,
    vehicle_compositions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """{no, name, points, point_count, points_truncated, referenced_by,
    reference_count} per ``<speedDistributions><speedDistribution>``.

    v2a unit ⑧ (2026-07-27) shipped {no, name}; QUEUE 3 (v2a) unit 3
    (2026-07-29) EXTENDS it in place with points + reference counts (per the
    task brief: "Check whether unit ⑧ already shipped a
    get_des_speed_distributions... If so, EXTEND it... rather than
    duplicating") -- the envelope shape stays backward-compatible (every
    existing {no, name} consumer still works; new keys are additive).

    This is the SAME distribution object type
    :func:`services.simulation_network_builder.inject_reduced_speed_area`
    already injects for ``set_reduced_speed_area`` (v2 unit ②) -- confirmed
    at the COM level too by unit ⑧'s own probe
    (``backend/tools/probe_des_speed_decisions.py``, Roundabout Billings.US):
    the collection is ``Net.SpeedDistributions`` (``Net.DesSpeedDistributions``
    does NOT exist -- ``AttributeError``), not a dedicated per-feature
    collection. Desired-speed decisions and reduced-speed areas share ONE
    distribution object type end to end, XML and COM both. ``no``/``name``
    match the .inpx XML attributes exactly (no CycTm/NodeRes-style landmine
    here); COM's own ``ItemByKey`` on this collection fails CLOSED for a
    bogus number (verified live), and so does ``SetAttValue`` on a
    desSpeedDecision entry given a bogus distribution number ("Object 999999
    of type Speed Distribution does not exist") -- COM already protects
    against a dangling reference at the write layer, but
    ``constraints.py``'s ``des_speed_distribution_not_found`` still refuses
    proactively (with a candidate list) so the failure is a structured
    refusal, not a raw COM error string, per the guardrail doctrine.

    **Reference counting (this unit's own addition, the "⑧ lesson" --
    ``SpeedDistributions is one shared collection"):** a distribution can be
    referenced by desired-speed decisions (``des_speed_decisions[].
    veh_class_assignments[].des_speed_distr_no``), vehicle compositions
    (``vehicle_compositions[].entries[].des_speed_distr`` -- VERIFIED live by
    a static grep, NOT assumed from the task brief's own parenthetical: every
    real ``<vehicleCompositionRelativeFlow>`` across all 25 example networks
    DOES carry a ``desSpeedDistr`` attribute, 305 total occurrences), reduced
    speed areas (:func:`_reduced_speed_area_distribution_refs`), and four
    further real XML element types with no shadow-model object of their own
    (:func:`_other_speed_distribution_reference_counts`). ``reference_count``
    is the grand total across every category; ``referenced_by`` is silent
    (all-zero categories) for an unreferenced distribution, matching the
    guardrail doctrine's "silent when there's nothing to warn about" norm.
    """
    des_speed_decisions = des_speed_decisions or []
    vehicle_compositions = vehicle_compositions or []

    decision_refs: dict[str, list[str]] = {}
    for dec in des_speed_decisions:
        for assignment in dec.get("veh_class_assignments", []):
            distr_no = str(assignment.get("des_speed_distr_no", "") or "").strip()
            if not distr_no:
                continue
            decision_refs.setdefault(distr_no, []).append(f"{dec.get('no', '')}.{assignment.get('veh_class_no', '')}")

    composition_refs: dict[str, list[str]] = {}
    for comp in vehicle_compositions:
        for entry in comp.get("entries", []):
            distr_no = str(entry.get("des_speed_distr", "") or "").strip()
            if not distr_no:
                continue
            composition_refs.setdefault(distr_no, []).append(f"{comp.get('no', '')}.{entry.get('veh_type', '')}")

    reduced_speed_area_refs = _reduced_speed_area_distribution_refs(root)
    other_ref_counts = _other_speed_distribution_reference_counts(root)

    result: list[dict[str, Any]] = []
    for d in root.findall(".//speedDistributions/speedDistribution"):
        no = d.get("no", "")
        all_points = _parse_speed_distribution_points(d)
        capped_points = all_points[:_SPEED_DISTRIBUTION_POINTS_CAP]

        dec_ids = decision_refs.get(no, [])
        comp_ids = composition_refs.get(no, [])
        rsa_ids = reduced_speed_area_refs.get(no, [])
        other_by_tag = other_ref_counts.get(no, {})
        other_total = sum(other_by_tag.values())

        result.append(
            {
                "no": no,
                "name": d.get("name", ""),
                "points": capped_points,
                "point_count": len(all_points),
                "points_truncated": len(all_points) > len(capped_points),
                "referenced_by": {
                    "des_speed_decisions": _bounded_reference_ids(dec_ids),
                    "vehicle_compositions": _bounded_reference_ids(comp_ids),
                    "reduced_speed_areas": _bounded_reference_ids(rsa_ids),
                    "other": {"count": other_total, "by_tag": other_by_tag},
                },
                "reference_count": len(dec_ids) + len(comp_ids) + len(rsa_ids) + other_total,
            }
        )
    return result


# ---------------------------------------------------------------------------
# get_time_distributions -- QUEUE (Q3, v2a) unit 5: dwell-time distributions
#
# Probed live (backend/tools/probe_dwell_time_distributions.py, "BRT
# Priority Texas.US" -- "Signalization RBC Boise.US", this unit's static
# scan's original richest-PT-network pick, RAISES on LoadNet itself, a NEW
# landmine; substituted per the "pick a different real example network"
# precedent every prior probe-first unit has set). ``Net.TimeDistributions``
# -> No/Name/Type/Mean/StdDev matching the .inpx XML spelling exactly (no
# CycTm-class landmine); ``Type`` is ``"NORMAL"`` (mean/stdDev drive
# sampling) or ``"EMPIRICAL"`` (a ``<durDistrDataPts>`` child of (x duration,
# fx cumulative-fraction) points drives sampling instead, the SAME shape
# unit 3's ``speedDistrDatPts`` already established). An EMPIRICAL element's
# own mean/stdDev are a FROZEN, live-verified NOT-recomputed-from-points
# informational summary -- editing them is refused, both by Vissim's own COM
# layer ("Attribute Mean cannot be changed") and proactively by this
# codebase (see ``set_time_distribution_normal``'s own docstring).
#
# THIS IS A GENUINELY SHARED COLLECTION (the "⑧ lesson" generalized again):
# a network-wide static scan across all 25 real Vissim 2026 example networks
# found SEVEN real reference sites, none of them "stop signs" (DISPROVING
# this unit's own task brief's example guess -- ``<stopSign>`` carries NO
# time-distribution reference of any kind on any real network scanned):
# ``ptLineStop``/``partialPTLineStop.dwellTmDistr`` (409+4, PT-stop dwell
# time), ``vehClassDwellTimeDistribution.dwellTmDistr`` (327, a nested
# PER-VEHICLE-CLASS override), ``drivingBehavior.w99cc1Distr``/
# ``zipperOvtTmDistr`` (81+, CONFIRMS 00-EXECUTION-GUIDE.md's "CC1 is a
# reference to a Time Distribution object" claim empirically), ``ptLine.
# entryTmDistr`` (22, PT network-entry wait -- NOT a dwell time),
# ``parkingLot``/``defRefsParkingLot.dirChgDurDist`` (parking direction-
# change duration), and ``area.tmDistr`` (pedestrian queue-area wait time) --
# the last four counted generically by tag (no shadow-model object of their
# own), same policy :func:`_other_speed_distribution_reference_counts`
# already established.
# ---------------------------------------------------------------------------

#: Points shown per distribution on an UNFILTERED/filtered
#: get_time_distributions call -- the real max observed across all 25
#: Vissim 2026 example networks' 30 real EMPIRICAL ``timeDistribution``
#: elements is 4 points; 30 mirrors ``_SPEED_DISTRIBUTION_POINTS_CAP``'s own
#: headroom reasoning for the sibling collection, not a tuned-to-the-edge
#: limit.
_TIME_DISTRIBUTION_POINTS_CAP = 30

#: (tag, attribute) pairs for real XML sites that reference a
#: timeDistribution number but have no shadow-model object of their own in
#: this codebase -- found by the network-wide static scan described in this
#: block's own module comment. Counted generically by tag (no owner id),
#: the SAME "honest total, no per-object enumeration" policy
#: :data:`_OTHER_SPEED_DISTRIBUTION_REFERENCE_TAGS` already established.
_OTHER_TIME_DISTRIBUTION_REFERENCE_SITES: tuple[tuple[str, str], ...] = (
    ("ptLine", "entryTmDistr"),
    ("parkingLot", "dirChgDurDist"),
    ("defRefsParkingLot", "dirChgDurDist"),
    ("area", "tmDistr"),
)


def _parse_time_distribution_points(distribution_el: ET.Element) -> list[dict[str, float | None]]:
    """[{duration_s, cum_fraction}, ...] per ``<durDistrDataPts>
    <durationDistributionDataPoint fx="..." x="..."/></durDistrDataPts>``, in
    document order. Only ``type="EMPIRICAL"`` ``<timeDistribution>`` elements
    carry this child; a NORMAL one returns ``[]``. Genuine seconds, no unit
    conversion needed -- live-verified (unlike unit 3's speed-in-mph
    landmine) that Vissim has no per-network "display unit" setting for
    durations."""
    points: list[dict[str, float | None]] = []
    for pt in distribution_el.findall("durDistrDataPts/durationDistributionDataPoint"):
        points.append(
            {
                "duration_s": _coerce_float_or_none(pt.get("x")),
                "cum_fraction": _coerce_float_or_none(pt.get("fx")),
            }
        )
    return points


def _pt_line_stop_dwell_refs(root: ET.Element) -> dict[str, list[str]]:
    """{distribution_no: ["{pt_line_no}.{pt_stop_no}", ...]} from every real
    ``<ptLineStop>``/``<partialPTLineStop dwellTmDistr="...">``, id-ed by its
    OWNER ``<ptLine>``'s own ``no`` (a ptLineStop carries no identity
    attribute of its own -- it references its stop via a ``ptStop``
    attribute instead)."""
    refs: dict[str, list[str]] = {}
    for pt_line in root.findall(".//ptLine"):
        line_no = pt_line.get("no", "")
        for stop_tag in ("ptLineStop", "partialPTLineStop"):
            for stop in pt_line.findall(f".//{stop_tag}"):
                distr_no = (stop.get("dwellTmDistr") or "").strip()
                if not distr_no:
                    continue
                owner_id = f"{line_no}.{stop.get('ptStop', '')}"
                owners = refs.setdefault(distr_no, [])
                if owner_id not in owners:
                    owners.append(owner_id)
    return refs


def _pt_line_stop_veh_class_override_refs(root: ET.Element) -> dict[str, list[str]]:
    """{distribution_no: ["{pt_line_no}.{pt_stop_no}.{veh_class}", ...]}
    from every real ``<vehClassDwellTimeDistribution dwellTmDistr="...">``,
    a PER-VEHICLE-CLASS dwell-time override nested inside a
    ptLineStop/partialPTLineStop."""
    refs: dict[str, list[str]] = {}
    for pt_line in root.findall(".//ptLine"):
        line_no = pt_line.get("no", "")
        for stop_tag in ("ptLineStop", "partialPTLineStop"):
            for stop in pt_line.findall(f".//{stop_tag}"):
                stop_no = stop.get("ptStop", "")
                for override in stop.findall(".//vehClassDwellTimeDistribution"):
                    distr_no = (override.get("dwellTmDistr") or "").strip()
                    if not distr_no:
                        continue
                    owner_id = f"{line_no}.{stop_no}.{override.get('vehClass', '')}"
                    owners = refs.setdefault(distr_no, [])
                    if owner_id not in owners:
                        owners.append(owner_id)
    return refs


def _driving_behavior_time_distribution_refs(root: ET.Element) -> dict[str, list[str]]:
    """{distribution_no: ["{driving_behavior_no}:cc1" | "...:zipper", ...]}
    from every real ``<drivingBehavior w99cc1Distr="..."
    zipperOvtTmDistr="...">`` -- CONFIRMS 00-EXECUTION-GUIDE.md's "CC1 is a
    reference to a Time Distribution object" claim empirically (w99cc1Distr
    genuinely resolves into this exact collection, verified live via
    backend/tools/probe_dwell_time_distributions.py's own scalar-attribute
    discovery)."""
    refs: dict[str, list[str]] = {}
    for db in root.findall(".//drivingBehavior"):
        db_no = db.get("no", "")
        for attr, label in (("w99cc1Distr", "cc1"), ("zipperOvtTmDistr", "zipper")):
            distr_no = (db.get(attr) or "").strip()
            if not distr_no:
                continue
            owner_id = f"{db_no}:{label}"
            owners = refs.setdefault(distr_no, [])
            if owner_id not in owners:
                owners.append(owner_id)
    return refs


def _other_time_distribution_reference_counts(root: ET.Element) -> dict[str, dict[str, int]]:
    """{distribution_no: {tag_name: count}} for every real XML element type
    (other than ptLineStop/partialPTLineStop/vehClassDwellTimeDistribution/
    drivingBehavior, all counted with their own id-bearing owners elsewhere)
    that ALSO carries a time-distribution reference -- see
    :data:`_OTHER_TIME_DISTRIBUTION_REFERENCE_SITES`'s own docstring for the
    static-scan evidence."""
    counts: dict[str, dict[str, int]] = {}
    for tag, attr in _OTHER_TIME_DISTRIBUTION_REFERENCE_SITES:
        for el in root.iter(tag):
            distr_no = (el.get(attr) or "").strip()
            if not distr_no:
                continue
            per_distr = counts.setdefault(distr_no, {})
            per_distr[tag] = per_distr.get(tag, 0) + 1
    return counts


def _parse_time_distributions(root: ET.Element) -> list[dict[str, Any]]:
    """{no, name, type, mean_std_dev, points, point_count, points_truncated,
    referenced_by, reference_count} per
    ``<timeDistributions><timeDistribution>``.

    QUEUE (Q3, v2a) unit 5 (2026-07-29+). See this section's own module
    comment for the full live-verified findings (collection name, attribute
    spellings, the EMPIRICAL-mean-is-frozen finding, and the "genuinely
    shared collection, NOT stop signs" reference-site scan).

    ``mean_std_dev`` bundles ``{mean_s, std_dev_s}`` as ONE compound field --
    mirroring ``_flatten_signal_program_splits``' ``green_window``/
    ``_flatten_priority_rule_markers``' ``gap_headway`` exactly, for the SAME
    reason: ``set_time_distribution_normal`` writes both in one call (either
    field alone, or both together) and ``guardrails.py``'s
    ``_WRITE_TARGET_FIELD_SPEC`` exempts ONE named ``value_field`` per tool,
    compared whole by the recursive numeric-tolerant ``_values_differ``.
    Deliberately NOT ALSO exposed as separate top-level keys (the
    ``gap_headway`` precedent's own documented reason: a duplicate top-level
    copy would sit OUTSIDE the exemption and false-positive as "something
    else moved" on every real write).

    ``reference_count`` is the grand total across every category;
    ``referenced_by`` is silent (all-zero categories) for an unreferenced
    distribution, matching :func:`_parse_speed_distributions`'s own norm.
    """
    pt_stop_refs = _pt_line_stop_dwell_refs(root)
    veh_class_override_refs = _pt_line_stop_veh_class_override_refs(root)
    driving_behavior_refs = _driving_behavior_time_distribution_refs(root)
    other_ref_counts = _other_time_distribution_reference_counts(root)

    result: list[dict[str, Any]] = []
    for d in root.findall(".//timeDistributions/timeDistribution"):
        no = d.get("no", "")
        all_points = _parse_time_distribution_points(d)
        capped_points = all_points[:_TIME_DISTRIBUTION_POINTS_CAP]

        pt_ids = pt_stop_refs.get(no, [])
        veh_class_ids = veh_class_override_refs.get(no, [])
        behavior_ids = driving_behavior_refs.get(no, [])
        other_by_tag = other_ref_counts.get(no, {})
        other_total = sum(other_by_tag.values())

        result.append(
            {
                "no": no,
                "name": d.get("name", ""),
                "type": d.get("type", ""),
                "mean_std_dev": {
                    "mean_s": _coerce_float_or_none(d.get("mean")),
                    "std_dev_s": _coerce_float_or_none(d.get("stdDev")),
                },
                "points": capped_points,
                "point_count": len(all_points),
                "points_truncated": len(all_points) > len(capped_points),
                "referenced_by": {
                    "pt_stops": _bounded_reference_ids(pt_ids),
                    "pt_vehicle_class_overrides": _bounded_reference_ids(veh_class_ids),
                    "driving_behaviors": _bounded_reference_ids(behavior_ids),
                    "other": {"count": other_total, "by_tag": other_by_tag},
                },
                "reference_count": len(pt_ids) + len(veh_class_ids) + len(behavior_ids) + other_total,
            }
        )
    return result


# ---------------------------------------------------------------------------
# get_driving_behaviors / duplicate_driving_behavior / set_driving_behavior_
# parameter / set_link_behavior_type_driving_behavior -- QUEUE 3 unit 6.
#
# A driving behavior is referenced NOT by links directly but by
# link-behavior-TYPES (a separate Net.LinkBehaviorTypes collection): each
# type carries a DEFAULT driving-behavior assignment plus zero or more
# per-vehicle-class overrides, and each LINK in turn references a
# link-behavior-type (never a driving behavior directly) via its own
# linkBehavType attribute. Live-verified (backend/tools/probe_driving_
# behaviors.py + two follow-up probe scripts) against "Roundabout
# Billings.US"; statically cross-checked against the real Murdoch production
# model (runtime/uploads/current/current.inpx -- known to raise on live COM
# load, like every prior QUEUE-3 unit's own finding, so this is XML-only):
# 15 real driving behaviors, 15 real link-behavior-types in a clean 1:1
# drivBehavDef mapping with ZERO per-class overrides anywhere, but only TWO
# of the 15 types are actually assigned to any link -- no=101 (293 links,
# EVERY named corridor: "Murdoch Dr NB/SB", "South St EB/WB", "Barry Marshal
# Pde NB/SB", "Robin Warren EB/WB", "Jennalup St EB/WB") and no=1 (19 links).
# Driving behavior #101 is therefore the single most shared calibration
# object in the real production model -- the live proof point for this
# unit's refuse-when-shared design.
# ---------------------------------------------------------------------------


def _link_behavior_type_link_counts(root: ET.Element) -> dict[str, int]:
    """{link_behavior_type_no: count} from every real ``<link
    linkBehavType="...">`` -- the blast-radius SIZE (how many links) behind
    each link-behavior-type's own driving-behavior assignment(s)."""
    counts: dict[str, int] = {}
    for link in root.findall(".//links/link"):
        lbt_no = (link.get("linkBehavType") or "").strip()
        if not lbt_no:
            continue
        counts[lbt_no] = counts.get(lbt_no, 0) + 1
    return counts


def _parse_link_behavior_types(root: ET.Element, link_counts: dict[str, int]) -> list[dict[str, Any]]:
    """{no, name, driving_behavior_default, overrides: [{veh_class_no,
    driving_behavior_no}], link_count} per ``<linkBehaviorTypes>
    <linkBehaviorType drivBehavDef="..."><vehClassDrivBehav>
    <vehClassDrivingBehavior vehClass="..." drivBehav="..."/></...>``.
    ``link_count`` is :func:`_link_behavior_type_link_counts`'s own tally for
    this type -- the real number of links whose driving-behavior physics
    change if this type's DEFAULT assignment is reassigned."""
    result: list[dict[str, Any]] = []
    for lbt in root.findall(".//linkBehaviorTypes/linkBehaviorType"):
        no = lbt.get("no", "")
        overrides: list[dict[str, Any]] = []
        for override in lbt.findall(".//vehClassDrivingBehavior"):
            veh_class_no = (override.get("vehClass") or "").strip()
            driving_behavior_no = (override.get("drivBehav") or "").strip()
            if not veh_class_no or not driving_behavior_no:
                continue
            overrides.append({"veh_class_no": veh_class_no, "driving_behavior_no": driving_behavior_no})
        result.append(
            {
                "no": no,
                "name": lbt.get("name", ""),
                "driving_behavior_default": (lbt.get("drivBehavDef") or "").strip(),
                "overrides": overrides,
                "link_count": link_counts.get(no, 0),
            }
        )
    return result


def _driving_behavior_link_behavior_type_refs(
    link_behavior_types: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """{driving_behavior_no: [{link_behavior_type_no, link_behavior_type_name,
    veh_class_no (``None`` for a DEFAULT assignment), link_count}, ...]} --
    the reference graph powering ``set_driving_behavior_parameter``'s
    refuse-when-shared check: a driving behavior is "shared" whenever this
    list has more than one entry (or, per the task's own design, is
    referenced at all -- see ``constraints.py``'s rewritten
    ``driving_behavior_shared_object_edit``)."""
    refs: dict[str, list[dict[str, Any]]] = {}
    for lbt in link_behavior_types:
        default_no = lbt["driving_behavior_default"]
        if default_no:
            refs.setdefault(default_no, []).append(
                {
                    "link_behavior_type_no": lbt["no"],
                    "link_behavior_type_name": lbt["name"],
                    "veh_class_no": None,
                    "link_count": lbt["link_count"],
                }
            )
        for override in lbt["overrides"]:
            refs.setdefault(override["driving_behavior_no"], []).append(
                {
                    "link_behavior_type_no": lbt["no"],
                    "link_behavior_type_name": lbt["name"],
                    "veh_class_no": override["veh_class_no"],
                    "link_count": lbt["link_count"],
                }
            )
    return refs


#: Consumer-list cap for a driving behavior's ``referenced_by`` -- mirrors
#: ``_SPEED_DISTRIBUTION_REFERENCE_CAP``'s own bounded-envelope reasoning
#: (suggestion/reference lists reach LLM prompts); the COUNT is always the
#: real, untruncated total.
_DRIVING_BEHAVIOR_CONSUMER_CAP = 10


def _bounded_driving_behavior_consumers(consumers: list[dict[str, Any]]) -> dict[str, Any]:
    """{count, consumers, truncated} -- like :func:`_bounded_reference_ids`
    but keeps each consumer's full dict (link_behavior_type_no/name/
    veh_class_no/link_count), not a bare id string, since the refuse-when-
    shared message must NAME every consumer (bounded) per this unit's own
    task brief."""
    capped = consumers[:_DRIVING_BEHAVIOR_CONSUMER_CAP]
    return {"count": len(consumers), "consumers": capped, "truncated": len(consumers) > len(capped)}


#: {"WIEDEMANN74": 74, "WIEDEMANN99": 99} -- every other real
#: ``carFollowModType`` (``"NOINTERACTION"`` on Footpath/pedestrian
#: behaviors, live-verified on both the real Murdoch model and "Roundabout
#: Billings.US") maps to ``None``. Matches ``constraints.py``'s pre-existing
#: ``car_following_model`` int contract (``driving_behavior_parameter_model_
#: mismatch`` already compares against literal ``74``/``99``).
_CAR_FOLLOW_MODEL_INTS: dict[str, int] = {"WIEDEMANN74": 74, "WIEDEMANN99": 99}

#: {canonical parameter name: XML attribute name} -- matches
#: ``vissim_com.VissimController.DRIVING_BEHAVIOR_PARAM_FIELDS``'s keys
#: exactly (the write whitelist); see that dict's own module comment for the
#: full live-verified per-field reasoning (why these 8, why not W99 CC0-CC9).
_DRIVING_BEHAVIOR_PARAM_XML_ATTRS: dict[str, str] = {
    "ax": "w74ax",
    "bx_add": "w74bxAdd",
    "bx_mult": "w74bxMult",
    "saf_dist_fact_ln_chg": "safDistFactLnChg",
    "coop_ln_chg_speed_diff": "coopLnChgSpeedDiff",
    "min_speed_for_lat": "minSpeedForLat",
    "lat_dist_driv_def": "latDistDrivDef",
    "lat_dist_stand_def": "latDistStandDef",
}


def _parse_driving_behaviors(
    root: ET.Element, link_behavior_types: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """{no, name, car_follow_mod_type, car_following_model, <8 whitelisted
    params>, referenced_by, reference_count} per ``<drivingBehaviors>
    <drivingBehavior>``.

    Vissim's .inpx storage is ALWAYS metric/canonical regardless of the
    network's own display unit (live-verified: a FEET-display network's own
    XML still stores ``w74ax="2"``, meters -- the display-unit conversion
    happens only at the COM ``AttValue`` boundary, see
    ``vissim_com.VissimController``'s own driving-behavior module comment)
    -- so these raw XML values are already genuine meters/km/h, no
    conversion needed here, mirroring ``_parse_speed_distribution_points``'/
    ``_parse_time_distribution_points``' own "XML is unit-native" finding.
    """
    refs_by_behavior = _driving_behavior_link_behavior_type_refs(link_behavior_types)

    result: list[dict[str, Any]] = []
    for d in root.findall(".//drivingBehaviors/drivingBehavior"):
        no = d.get("no", "")
        car_follow_mod_type = (d.get("carFollowModType") or "").strip().upper()
        consumers = refs_by_behavior.get(no, [])
        entry: dict[str, Any] = {
            "no": no,
            "name": d.get("name", ""),
            "car_follow_mod_type": car_follow_mod_type,
            "car_following_model": _CAR_FOLLOW_MODEL_INTS.get(car_follow_mod_type),
            "referenced_by": _bounded_driving_behavior_consumers(consumers),
            "reference_count": len(consumers),
        }
        for canonical, xml_attr in _DRIVING_BEHAVIOR_PARAM_XML_ATTRS.items():
            entry[canonical] = _coerce_float_or_none(d.get(xml_attr))
        result.append(entry)
    return result


def _flatten_link_behavior_type_assignments(
    link_behavior_types: list[dict[str, Any]], driving_behaviors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """A flat, one-entry-per-(link_behavior_type, target) view of
    ``link_behavior_types`` -- ONE ``"{lbt_no}.default"`` entry per type plus
    one ``"{lbt_no}.{veh_class_no}"`` entry per EXISTING per-vehicle-class
    override, mirroring ``_flatten_routes``' precedent (v2 unit ⑥) exactly:
    ``set_link_behavior_type_driving_behavior``'s target has no
    single-token identity of its own (a call picks EITHER the default OR one
    existing override via its own optional ``veh_class_no`` param) -- this
    is the shape ``guardrails.py``'s postwrite machinery needs.
    ``driving_behavior_name`` is a DERIVED display field (looked up from
    ``driving_behavior_no`` at parse time, the same "coupled_fields"
    situation ``set_des_speed_decision``'s own ``des_speed_distr_name``
    established) -- ``guardrails.py`` must exempt it alongside
    ``driving_behavior_no`` or a correct write false-positives as "something
    else moved".
    """
    names_by_no = {b["no"]: b["name"] for b in driving_behaviors}
    flat: list[dict[str, Any]] = []
    for lbt in link_behavior_types:
        default_no = lbt["driving_behavior_default"]
        flat.append(
            {
                "id": f"{lbt['no']}.default",
                "link_behavior_type_no": lbt["no"],
                "link_behavior_type_name": lbt["name"],
                "veh_class_no": None,
                "driving_behavior_no": default_no,
                "driving_behavior_name": names_by_no.get(default_no, ""),
                "link_count": lbt["link_count"],
            }
        )
        for override in lbt["overrides"]:
            flat.append(
                {
                    "id": f"{lbt['no']}.{override['veh_class_no']}",
                    "link_behavior_type_no": lbt["no"],
                    "link_behavior_type_name": lbt["name"],
                    "veh_class_no": override["veh_class_no"],
                    "driving_behavior_no": override["driving_behavior_no"],
                    "driving_behavior_name": names_by_no.get(override["driving_behavior_no"], ""),
                    "link_count": lbt["link_count"],
                }
            )
    return flat


# ---------------------------------------------------------------------------
# get_vehicle_types -- QUEUE (v2a) unit: vehicle type properties,
# SCALARS/REFERENCES only.
#
# Probed live (backend/tools/probe_vehicle_types.py, Roundabout Billings.US):
# ``Net.VehicleTypes`` -> a FLAT VehicleType object whose scalar attributes
# (No/Name/Category/Capacity/CapExact) and REFERENCE attributes
# (Model2D3DDistr/WeightDistr/PowerDistr/DesAccelFunc/MaxAccelFunc/
# DesDecelFunc/MaxDecelFunc/DesCurveSpeedFunc) ALL match the .inpx XML
# attribute spelling exactly -- no CycTm/NodeRes-style COM-id mismatch here.
# Every reference is independent (no shared-sum/renormalization semantics,
# verified live via a sibling-untouched check), and COM itself fails CLOSED
# on a bogus reference target. See ``vissim_com.VissimController.
# set_vehicle_type_reference``'s own module comment for the full write-side
# writeup (including the ONE genuine landmine: ``DesCurveSpeedFunc``'s
# attribute id keeps the "Curve" spelling but its TARGET collection is
# ``Net.CurvSpeedFuncs``, "Curv" -- never assume a reference id and its
# target collection share a spelling convention).
#
# **The catalog bullet's real writable surface, as the task brief predicted:**
# length/weight/power/accel/decel are NOT scalars on the type itself --
# ``model2D3DDistr``/``weightDistr``/``powerDistr``/``des*AccelFunc``/
# ``des*DecelFunc``/``desCurveSpeedFunc`` are all REFERENCES to a DIFFERENT
# object elsewhere (a 2D/3D model distribution, a weight/power distribution,
# an accel/decel/curve-speed FUNCTION). This unit ships exactly that
# reference-REASSIGNMENT surface (``set_vehicle_type_reference``) plus a
# read tool that resolves each reference to cheap, informational stats --
# never curve/model editing, which stays a documented probe report only (per
# the task's own explicit "do NOT implement curve editing" instruction).
#
# **Nullable-per-category, verified both statically (168 real vehicleType
# elements across all 25 example networks) and live:** every
# ``category="PEDESTRIAN"`` vehicle type carries NO ``weightDistr``/
# ``powerDistr``/``desCurveSpeedFunc`` attribute at all -- these three
# references are genuinely OPTIONAL per category, not always-populated. The
# parser below tolerates an absent reference cleanly (``None``), never
# assumes every vehicle type carries every field.
# ---------------------------------------------------------------------------


def _parse_model2d3d_lengths_m(root: ET.Element) -> dict[str, float]:
    """{model2D3D_no: length_m} from ``<models2D3D><model2D3D><model2D3DSegs>
    <model2D3DSegment length="..."/></model2D3DSegs></model2D3D></models2D3D>``.

    A vehicle SHAPE's own physical length, in METRES -- verified statically
    against a real MILESPERHOUR-display network (Roundabout Billings.US,
    ``netPara@unitLenShort="FEET"``): raw XML ``length`` values (e.g. ~5.97 m
    for a "LtTruck - Ford F150") are already plausible METRIC lengths
    regardless of the network's own display-unit setting, matching every
    other "XML always stores metric" precedent in this codebase (speed
    distribution ``x``, v2a unit ⑧'s desSpeedDecision ``Pos``) -- there is no
    ``unitWeight``/``unitPower`` OR per-length-attribute display-unit
    override for THIS attribute found in any real ``<netPara>`` scanned, so
    no conversion is applied or needed here.

    **Multi-segment approximation, flagged (not COM-verified):** a static
    scan across all 25 example networks found ``model2D3D`` shapes with
    1-95 segments (histogram: 422 single-segment, 36 two-segment, up to one
    95-segment shape -- almost certainly a long train/rail consist). This
    function SUMS every segment's own ``length`` as the shape's total
    physical length -- a reasonable engineering reading (sequential body
    sections chained end to end) for the common single/few-segment case, but
    genuinely UNVERIFIED for the rare many-segment (rail) case; this is a
    cheap, informational, READ-ONLY statistic never used to gate any write or
    refusal, so the approximation's risk is display-only. A segment with no
    parseable ``length`` contributes 0 to that shape's total (never raises,
    never drops the shape from the result)."""
    lengths: dict[str, float] = {}
    for model in root.findall(".//models2D3D/model2D3D"):
        no = model.get("no", "")
        if not no:
            continue
        total = 0.0
        for seg in model.findall("model2D3DSegs/model2D3DSegment"):
            total += _coerce_float_or_none(seg.get("length")) or 0.0
        lengths[no] = total
    return lengths


def _parse_model2d3d_distributions(root: ET.Element) -> dict[str, dict[str, Any]]:
    """{no: {no, name, length_min_m, length_max_m, length_mean_m,
    model_count}} from ``<model2D3DDistributions><model2D3DDistribution>
    <model2D3DDistrEl><model2D3DDistributionElement model2D3D=".."
    share=".."/></model2D3DDistrEl></model2D3DDistribution>
    </model2D3DDistributions>``.

    ``length_mean_m`` is SHARE-WEIGHTED, not a plain average -- verified
    statically that ``share`` values do NOT reliably sum to 1.0 across a
    distribution's own elements (e.g. a real 10-element "Car" distribution's
    shares sum to ~1.63), the SAME "raw weight, not a pre-normalized share"
    pattern this codebase has already found for route/vehicle-composition
    RelFlow -- so the mean here normalizes by the elements' own share SUM,
    not by assuming it is already 1. A shape id absent from
    :func:`_parse_model2d3d_lengths_m`'s lookup (should not happen on real
    data -- every ``model2D3D`` referenced by a distribution element is
    expected to exist) is skipped from the min/max/mean computation rather
    than crashing or silently contributing a 0-length phantom entry.
    """
    lengths = _parse_model2d3d_lengths_m(root)
    result: dict[str, dict[str, Any]] = {}
    for distr in root.findall(".//model2D3DDistributions/model2D3DDistribution"):
        no = distr.get("no", "")
        if not no:
            continue
        pairs: list[tuple[float, float]] = []
        for el in distr.findall("model2D3DDistrEl/model2D3DDistributionElement"):
            model_no = el.get("model2D3D", "")
            length_m = lengths.get(model_no)
            share = _coerce_float_or_none(el.get("share"))
            if length_m is None or share is None:
                continue
            pairs.append((length_m, share))
        share_sum = sum(share for _length, share in pairs)
        result[no] = {
            "no": no,
            "name": distr.get("name", ""),
            "length_min_m": min((length for length, _share in pairs), default=None),
            "length_max_m": max((length for length, _share in pairs), default=None),
            "length_mean_m": (
                round(sum(length * share for length, share in pairs) / share_sum, 3)
                if share_sum > 0
                else None
            ),
            "model_count": len(pairs),
        }
    return result


def _parse_generic_distribution_stats(
    root: ET.Element, section_tag: str, element_tag: str
) -> dict[str, dict[str, Any]]:
    """{no: {no, name, min, max, mean, point_count}} for a ``<distrDataPts>
    <distributionDataPoint fx="..." x="..."/></distrDataPts>``-shaped
    distribution -- the EXACT same point shape ``speedDistribution`` uses
    (:func:`_parse_speed_distribution_points`), reused here for
    ``weightDistribution``/``powerDistribution`` (verified statically: e.g.
    Beijing's ``weightDistribution no="1"`` is a plain 2-point
    ``[(fx=0,x=700), (fx=1,x=1500)]`` curve). Values are read straight from
    the XML in their native units (kg for weight, kW for power -- Vissim's
    ``<netPara>`` carries no ``unitWeight``/``unitPower`` display-unit
    attribute on ANY of the 25 real example networks scanned, unlike speed/
    length, so no display-unit conversion path exists to guard against).

    ``mean`` is the cumulative distribution's own probability-weighted mean
    (trapezoidal: each consecutive pair's fx-delta as its probability mass,
    the pair's own x-midpoint as its value) -- the standard way to summarize
    a CDF's central tendency, not a plain arithmetic average of the point
    x-values (which would overweight however many points happen to sit near
    one end of the curve). ``min``/``max`` are the plain min/max of every
    point's own ``x`` (correct regardless of whether the curve is monotonic).
    A distribution with fewer than 2 usable points reports ``mean=None``
    (nothing to weight between); ``min``/``max`` still resolve from whatever
    points exist, or ``None`` for a genuinely empty curve.
    """
    result: dict[str, dict[str, Any]] = {}
    for el in root.findall(f".//{section_tag}/{element_tag}"):
        no = el.get("no", "")
        if not no:
            continue
        points: list[tuple[float, float]] = []
        for pt in el.findall("distrDataPts/distributionDataPoint"):
            x = _coerce_float_or_none(pt.get("x"))
            fx = _coerce_float_or_none(pt.get("fx"))
            if x is None or fx is None:
                continue
            points.append((x, fx))

        mean = None
        if len(points) >= 2:
            mass = 0.0
            weighted = 0.0
            for (x0, fx0), (x1, fx1) in zip(points, points[1:]):
                weight = fx1 - fx0
                weighted += weight * (x0 + x1) / 2.0
                mass += weight
            mean = round(weighted / mass, 3) if mass > 0 else None

        result[no] = {
            "no": no,
            "name": el.get("name", ""),
            "min": min((x for x, _fx in points), default=None),
            "max": max((x for x, _fx in points), default=None),
            "mean": mean,
            "point_count": len(points),
        }
    return result


def _parse_function_names(root: ET.Element, section_tag: str, element_tag: str) -> dict[str, dict[str, Any]]:
    """{no: {no, name}} for one of the five accel/decel/curve-speed FUNCTION
    collections (``desAccelerationFunctions``/``maxAccelerationFunctions``/
    ``desDecelerationFunctions``/``maxDecelerationFunctions``/
    ``curvSpeedFuncs``) -- id + name ONLY. Each function's own curve (a
    speed-indexed acceleration/deceleration/curve-speed table, up to ~35
    points on real data) is deliberately NOT parsed here: this unit's task
    brief scopes function CURVE editing out entirely ("probe report only, do
    NOT implement curve editing") -- these are read-only REFERENCE TARGETS
    for :func:`_parse_vehicle_types`/``set_vehicle_type_reference`` to
    validate against and name, nothing more.
    """
    return {
        el.get("no", ""): {"no": el.get("no", ""), "name": el.get("name", "")}
        for el in root.findall(f".//{section_tag}/{element_tag}")
        if el.get("no")
    }


def _vehicle_type_composition_refs(vehicle_composition_entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    """{veh_type: [composition_no, ...]} from the ALREADY-FLATTENED
    ``vehicle_composition_entries`` (:func:`_flatten_vehicle_composition_entries`)
    -- no new XML parsing needed, since every entry already carries both
    ``veh_type`` and ``composition_no``. This is the reverse direction of the
    existing composition->vehicle-input reference count
    (:func:`_xml_vehicle_composition_refs`): "which compositions carry THIS
    vehicle type" is exactly what the shared-collection warn constraint (the
    "⑧/③ lesson" -- a vehicle type is referenced by composition relFlows)
    needs to name on the approval card."""
    refs: dict[str, list[str]] = {}
    for entry in vehicle_composition_entries:
        veh_type = str(entry.get("veh_type", ""))
        composition_no = str(entry.get("composition_no", ""))
        if not veh_type or not composition_no:
            continue
        owners = refs.setdefault(veh_type, [])
        if composition_no not in owners:
            owners.append(composition_no)
    return refs


def _resolve_vehicle_type_reference(
    ref_no: str | None, lookup: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """The referenced object's own cheap-stats dict from ``lookup`` (one of
    :func:`_parse_model2d3d_distributions`/:func:`_parse_generic_distribution_stats`/
    :func:`_parse_function_names`'s return values), or ``None`` when
    ``ref_no`` is absent (the live-verified PEDESTRIAN-category nullable-
    reference finding) OR -- defensively, should not happen on real data --
    the referenced no is not present in the target collection (a dangling
    reference the read tool should show as "unresolvable" rather than
    crash on)."""
    if not ref_no:
        return None
    resolved = lookup.get(ref_no)
    if resolved is None:
        return {"no": ref_no, "name": None, "unresolved": True}
    return resolved


def _parse_vehicle_types(
    root: ET.Element,
    vehicle_composition_entries: list[dict[str, Any]],
    model2d3d_distributions: dict[str, dict[str, Any]],
    weight_distributions: dict[str, dict[str, Any]],
    power_distributions: dict[str, dict[str, Any]],
    des_accel_functions: dict[str, dict[str, Any]],
    max_accel_functions: dict[str, dict[str, Any]],
    des_decel_functions: dict[str, dict[str, Any]],
    max_decel_functions: dict[str, dict[str, Any]],
    curve_speed_functions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """{no, name, category, capacity, cap_exact, references: {...},
    referencing_composition_nos, <field>_no per reference field} per
    ``<vehicleType>``.

    Every reference field carries BOTH a flat ``"{field}_no"`` string (or
    ``None``) -- the write-target/guardrails-comparable identity, matching
    the ``des_speed_distr_no`` naming convention v2a unit ⑧'s
    ``des_speed_decision_entries`` already established -- AND a resolved,
    richer dict under ``references["{field}"]`` (id + name + cheap stats,
    e.g. a weight distribution's min/max/mean; ``None`` when the field
    itself is absent). ``references`` is purely DERIVED from the flat
    ``*_no`` fields plus each target collection's own data (never written
    directly by any tool), so ``guardrails.py``'s ``_ALWAYS_EXEMPT_FIELDS``
    excludes it from the nothing-else-moved diff -- see that module's own
    comment at the exemption.

    ``referencing_composition_nos`` (:func:`_vehicle_type_composition_refs`,
    bounded via :func:`_bounded_reference_ids`) is the "which vehicle
    compositions carry this type" list the shared-collection warn constraint
    needs -- also purely derived, also exempt.
    """
    composition_refs = _vehicle_type_composition_refs(vehicle_composition_entries)

    reference_lookups: dict[str, dict[str, dict[str, Any]]] = {
        "model2d3d_distr": model2d3d_distributions,
        "weight_distr": weight_distributions,
        "power_distr": power_distributions,
        "des_accel_func": des_accel_functions,
        "max_accel_func": max_accel_functions,
        "des_decel_func": des_decel_functions,
        "max_decel_func": max_decel_functions,
        "des_curve_speed_func": curve_speed_functions,
    }
    # Vissim's own .inpx attribute name for each reference field, matching
    # vissim_com.VissimController.VEHICLE_TYPE_REFERENCE_FIELDS' COM ids
    # exactly in spelling (verified live, see that dict's own docstring) --
    # this is the XML-side mirror of the SAME field vocabulary.
    xml_attrs: dict[str, str] = {
        "model2d3d_distr": "model2D3DDistr",
        "weight_distr": "weightDistr",
        "power_distr": "powerDistr",
        "des_accel_func": "desAccelFunc",
        "max_accel_func": "maxAccelFunc",
        "des_decel_func": "desDecelFunc",
        "max_decel_func": "maxDecelFunc",
        "des_curve_speed_func": "desCurveSpeedFunc",
    }

    result: list[dict[str, Any]] = []
    for vt in root.findall(".//vehicleTypes/vehicleType"):
        no = vt.get("no", "")
        if not no:
            continue
        cap_exact_raw = vt.get("capExact")

        entry: dict[str, Any] = {
            "no": no,
            "name": vt.get("name", ""),
            "category": vt.get("category", ""),
            "capacity": _coerce_float_or_none(vt.get("capacity")),
            "cap_exact": None if cap_exact_raw is None else cap_exact_raw.strip().lower() == "true",
        }

        references: dict[str, Any] = {}
        for field, lookup in reference_lookups.items():
            ref_no = vt.get(xml_attrs[field])
            entry[f"{field}_no"] = ref_no
            references[field] = _resolve_vehicle_type_reference(ref_no, lookup)
        entry["references"] = references
        entry["referencing_composition_nos"] = _bounded_reference_ids(composition_refs.get(no, []))

        result.append(entry)
    return result


def _parse_lane_token(raw: str | None) -> tuple[str, str]:
    """``"<linkNo> <laneNo>"`` -> ``(link_no, lane_no)`` -- the SAME
    space-separated XML encoding every other lane-scoped element in this
    codebase uses (reducedSpeedArea, connector endpoints). NOT the COM-side
    hyphenated ``"6-1"`` form ``AttValue('Lane')`` returns (v2a unit ⑧ probe
    finding, backend/tools/probe_des_speed_decisions.py) -- irrelevant here
    since this parses straight from the XML, never from a live COM read."""
    if not raw:
        return "", ""
    parts = raw.split()
    if len(parts) < 2:
        return (parts[0], "") if parts else ("", "")
    return parts[0], parts[1]


def _parse_des_speed_decisions(root: ET.Element) -> list[dict[str, Any]]:
    """{no, name, link, lane, pos, veh_class_assignments: [{veh_class_no,
    veh_class_name, des_speed_distr_no, des_speed_distr_name}]} per
    ``<desSpeedDecision>``.

    v2a unit ⑧ (2026-07-27). COM surface verified live
    (``backend/tools/probe_des_speed_decisions.py``, Roundabout Billings.US
    clone -- Beijing.CN, every other v2-unit probe's default network, has
    ZERO desSpeedDecision elements): ``Net.DesSpeedDecisions`` (``GetAll()``
    count 5 == the XML element count) -> per-decision ``.VehClassDesSpeedDistr``
    child collection (matches the XML child tag ``vehClassDesSpeedDistr``
    exactly) -> per-entry ``AttValue('VehClass'/'DesSpeedDistr')``, BOTH
    SCALAR (unlike route RelFlow's indexed-only form; like vehicle-
    composition RelFlow's scalar-only form -- always verify, never assume
    from a prior unit's finding). A SECOND, direct sub-attributed form ALSO
    works for READING (``decision.AttValue('DesSpeedDistr(10)')`` -> the same
    value the child-collection entry reports) but was not verified for
    WRITING and is not used here -- the child-collection route is the one
    this unit proved end to end (set, read back, persist, fresh-reload).

    **Entry identity: the vehClass number, but with NO ``ItemByKey``** --
    the SAME landmine v2 unit ⑦ found for vehicle-composition entries
    (confirmed live via ``hasattr``): ``vissim_com.VissimController.
    _des_speed_decision_entry_by_veh_class`` scans ``GetAll()`` and matches
    client-side.

    **No shared-sum/renormalization semantics at all** (unlike route/
    vehicle-composition RelFlow): each vehicle class's distribution
    assignment is a fully independent value -- writing vehClass=10's
    DesSpeedDistr on a 2-class decision left vehClass=20's own assignment
    byte-identical, confirmed live both immediately after the write and
    after a ``SaveNetAs`` + fresh COM reload. There is no "raw weight" /
    "estimated share" narrative to surface here.

    **Fail-closed everywhere it matters, verified live:** a bogus attribute
    id, a bogus decision number (``ItemByKey``), and -- critically -- a
    bogus DISTRIBUTION number passed to ``SetAttValue`` on a real entry
    ("Object 999999 of type Speed Distribution does not exist") ALL raise.
    COM itself refuses a dangling distribution reference; this codebase's
    own ``des_speed_distribution_not_found`` preflight check exists so that
    refusal is structured (with candidates) rather than a raw COM error
    reaching the user.

    **``lane`` is a two-token ``"<linkNo> <laneNo>"`` XML attribute**
    (:func:`_parse_lane_token`); COM's own ``AttValue('Lane')`` returns a
    DIFFERENT, hyphenated ``"6-1"`` string form -- not used here, parsed
    straight from XML like every other object this module builds.
    ``pos`` also has a live COM/XML DISCREPANCY worth recording even though
    this unit never writes it: on this US-unit (imperial-display) example
    network, ``AttValue('Pos')`` read back ~3.28x the XML ``pos`` value
    (356.77 vs 108.744 -- the metres-to-feet ratio) -- Vissim's COM layer
    appears to report position in the network's DISPLAY unit while the
    .inpx always stores metres internally. ``pos`` here is parsed from the
    XML (metres, matching every other position-bearing field this codebase
    parses) and never read via COM, so this is a recorded landmine for any
    FUTURE position-writing tool, not a live bug in this one.

    **Pairing is a pure human convention, not a structural link** (matches
    a network-wide static scan finding, this module's own probe script
    docstring, and ``constraints.py``'s pre-existing ``speed_decision_
    unpaired`` CATALOG-GAP placeholder's own premise): ``name`` is blank on
    every real decision observed (584/584 across every example network that
    has any) -- there is no grouping attribute, naming convention, or any
    other machine-discoverable link between a "set" decision and its
    "restore" decision anywhere in the .inpx or over COM. A caller (human or
    LLM) must identify the restore decision itself; this codebase does not
    and will not try to infer pairs from geometry.
    """
    veh_class_names = _parse_vehicle_class_names(root)
    distr_names = {
        d.get("no", ""): d.get("name", "") for d in root.findall(".//speedDistributions/speedDistribution")
    }
    result: list[dict[str, Any]] = []
    for dec in root.findall(".//desSpeedDecision"):
        link_no, lane_no = _parse_lane_token(dec.get("lane"))
        assignments: list[dict[str, Any]] = []
        for entry in dec.findall(".//vehClassDesSpeedDistribution"):
            veh_class_no = entry.get("vehClass", "")
            distr_no = entry.get("desSpeedDistr", "")
            assignments.append(
                {
                    "veh_class_no": veh_class_no,
                    "veh_class_name": veh_class_names.get(veh_class_no, ""),
                    "des_speed_distr_no": distr_no,
                    "des_speed_distr_name": distr_names.get(distr_no, ""),
                }
            )
        result.append(
            {
                "no": dec.get("no", ""),
                "name": dec.get("name", ""),
                "link": link_no,
                "lane": lane_no,
                "pos": _coerce_float_or_none(dec.get("pos")),
                "veh_class_assignments": assignments,
            }
        )
    return result


def _parse_priority_rule_link_ref(el: ET.Element) -> tuple[str, str, str]:
    """(link_no, lane_no, link_ref) for a ``<priorityRule>`` /
    ``<conflictMarker>`` element -- the ONE place both link-reference forms
    those elements use are resolved:

    - ``lane="<linkNo> <laneNo>"`` -- the usual lane-scoped form
      (:func:`_parse_lane_token`) -> ``link_ref="lane"``;
    - ``intLink="<linkNo>"`` -- a link-scoped reference with NO lane
      qualifier at all -> ``link_ref="int_link"``, ``lane_no=""``. Observed
      on the real Murdoch model, rule no=143 + its single marker (the only
      ``intLink`` user among its 13 rules / 20 markers): ``intLink="613"``,
      and the number IS the regular ``<link no>`` numbering -- verified
      statically 2026-07-31: the model is pure micro (all 6 nodes
      ``useForMeso="false"``, so no separate meso internal-link numbering
      space exists), ``<link no="613">`` is unique (an ordinary 2-lane
      ~355 m link, not a connector), both the rule's pos (131.16) and the
      marker's (164.77) fall inside its polyline length, and the
      neighbouring rules 144/145 reference the SAME link lane-wise
      (``lane="613 1"``/``"613 2"``). The lane stays ``""`` -- genuinely
      lane-less in the XML, never guessed;
    - neither attribute -> all three ``""``.
    """
    lane_raw = el.get("lane")
    if lane_raw:
        link_no, lane_no = _parse_lane_token(lane_raw)
        return link_no, lane_no, "lane"
    int_link = (el.get("intLink") or "").strip()
    if int_link:
        return int_link, "", "int_link"
    return "", "", ""


def _parse_priority_rules(root: ET.Element) -> list[dict[str, Any]]:
    """{no, name, link, lane, link_ref, pos, sig_state, affect_driv_dir,
    slow_down_dist, conflict_markers: [{marker_index, link, lane, link_ref,
    pos, gap_time_s, headway_m, max_speed, effect_driv_dir}]} per
    ``<priorityRule>``.

    QUEUE 3 unit 2 (2026-07-29). COM surface verified live
    (``backend/tools/probe_priority_rules.py``, Roundabout Buehl.DE clone --
    the real Murdoch Drive production model has 13 real ``<priorityRule>``
    elements too, 20 conflict markers, the SAME attribute set confirmed
    statically, but ``VissimController.load_network`` itself RAISES on that
    file -- see the probe's own module comment; a pre-existing, unrelated
    landmine, not something this unit introduces): ``Net.PriorityRules``
    (``GetAll()`` count 61 == the XML element count) -> per-rule
    ``.ConflictMarkers`` child collection (matches the XML child tag exactly,
    count matched too) -> per-marker ``AttValue('MinGapTime'/'MinClear'/...)``,
    all plain scalar attributes.

    ``lane`` is parsed via the SAME "<linkNo> <laneNo>" two-token XML
    convention as des-speed-decisions/reduced-speed-areas
    (:func:`_parse_lane_token`); COM's own ``AttValue('Lane')`` returns a
    DIFFERENT hyphenated ``"10066-1"`` form (v2a unit ⑧'s Lane landmine,
    reconfirmed live here for priority rules too) -- not used, parsed
    straight from XML like every other object this module builds. A rule (or
    marker) can carry NO ``lane`` at all and reference its link through an
    ``intLink`` attribute instead (real Murdoch model, rule no=143 + its
    single marker) -- resolved by :func:`_parse_priority_rule_link_ref` into
    a real ``link`` with ``lane=""`` and ``link_ref="int_link"``, so the
    reference is never shown blank; see that helper's docstring for the
    static verification that ``intLink`` numbers ARE the regular link
    numbering.

    **``headway_m`` is a DISTANCE (metres), not a time**, despite the name
    -- see ``vissim_com.VissimController.set_priority_rule_gap_headway``'s
    own module comment for the full live-verified finding: Vissim's COM
    alias ``MinHdwy`` and the XML's own ``minClear`` attribute are THE SAME
    underlying value (Vissim's own error text calls it "Minimum clearance").
    Parsed here straight from the XML ``minClear`` attribute.

    ``marker_index`` is a SYNTHESIZED 1-based ordinal (document order) --
    conflict markers carry no identity attribute of their own at all (no
    ``no``, verified live: ``AttValue('No')`` raises) and Vissim's own
    ``ConflictMarkers`` collection has no ``ItemByKey`` (verified live) --
    see ``vissim_com.py``'s own comment block for the full landmine writeup.
    """
    result: list[dict[str, Any]] = []
    for pr in root.findall(".//priorityRule"):
        link_no, lane_no, link_ref = _parse_priority_rule_link_ref(pr)
        markers: list[dict[str, Any]] = []
        for idx, cm in enumerate(pr.findall(".//conflictMarker"), start=1):
            cm_link, cm_lane, cm_link_ref = _parse_priority_rule_link_ref(cm)
            markers.append(
                {
                    "marker_index": idx,
                    "link": cm_link,
                    "lane": cm_lane,
                    "link_ref": cm_link_ref,
                    "pos": _coerce_float_or_none(cm.get("pos")),
                    "gap_time_s": _coerce_float_or_none(cm.get("minGapTime")),
                    "headway_m": _coerce_float_or_none(cm.get("minClear")),
                    "max_speed": _coerce_float_or_none(cm.get("maxSpeed")),
                    "effect_driv_dir": cm.get("effectDrivDir", ""),
                }
            )
        result.append(
            {
                "no": pr.get("no", ""),
                "name": pr.get("name", ""),
                "link": link_no,
                "lane": lane_no,
                "link_ref": link_ref,
                "pos": _coerce_float_or_none(pr.get("pos")),
                "sig_state": pr.get("sigState", ""),
                "affect_driv_dir": pr.get("affectDrivDir", ""),
                "slow_down_dist": _coerce_float_or_none(pr.get("slowDownDist")),
                "conflict_markers": markers,
            }
        )
    return result


def _flatten_priority_rule_markers(priority_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A flat, one-entry-per-(rule, marker) view of ``priority_rules``, keyed
    by a synthesized ``"{rule_no}.{marker_index}"`` ``id`` -- mirrors
    :func:`_flatten_routes`/:func:`_flatten_vehicle_composition_entries`/
    :func:`_flatten_des_speed_decision_entries`'s established precedent, for
    the identical reason: a conflict marker has no single-token identity in
    Vissim itself (see :func:`_parse_priority_rules`'s docstring).

    ``gap_headway`` bundles ``{gap_time_s, headway_m}`` as ONE compound field
    -- mirroring :func:`_flatten_signal_program_splits`'s ``green_window``
    exactly, for the SAME reason: ``set_priority_rule_gap_headway`` writes
    both in one call (either field alone, or both together) and
    ``guardrails.py``'s ``_WRITE_TARGET_FIELD_SPEC`` exempts ONE named
    ``value_field`` per tool, compared whole by the recursive numeric-
    tolerant ``_values_differ``. ``gap_time_s``/``headway_m`` are
    DELIBERATELY not ALSO exposed as separate top-level keys here (unlike
    ``green_window``'s siblings ``amber_s``/``min_green_s``, which are
    genuinely different values) -- a duplicate top-level copy of either
    field would sit OUTSIDE the ``gap_headway`` exemption and false-positive
    as "something else moved" on every real write, the exact coupled-fields
    lesson v2a unit ⑧ already recorded.
    """
    flat: list[dict[str, Any]] = []
    for pr in priority_rules:
        for marker in pr["conflict_markers"]:
            flat.append(
                {
                    "id": f"{pr['no']}.{marker['marker_index']}",
                    "priority_rule_no": pr["no"],
                    "marker_index": marker["marker_index"],
                    "link": marker["link"],
                    "lane": marker["lane"],
                    "pos": marker["pos"],
                    "gap_headway": {
                        "gap_time_s": marker["gap_time_s"],
                        "headway_m": marker["headway_m"],
                    },
                    "max_speed": marker["max_speed"],
                    "effect_driv_dir": marker["effect_driv_dir"],
                }
            )
    return flat


def _flatten_des_speed_decision_entries(des_speed_decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A flat, one-entry-per-(decision, vehClass) view of
    ``des_speed_decisions``, keyed by a synthesized ``"{decision_no}.
    {veh_class_no}"`` ``id`` -- mirrors :func:`_flatten_routes`/
    :func:`_flatten_vehicle_composition_entries`'s established precedent
    exactly, for the identical reason: an assignment has no single-token
    identity in Vissim itself (its real identity is the (decision, vehClass)
    pair; see :func:`_parse_des_speed_decisions`'s docstring), so this is the
    shape ``guardrails.py``'s postwrite machinery (the nothing-else-moved
    diff + the target-persisted check) needs to compare a real, id-keyed
    object list.
    """
    flat: list[dict[str, Any]] = []
    for dec in des_speed_decisions:
        for assignment in dec["veh_class_assignments"]:
            entry = dict(assignment)
            entry["id"] = f"{dec['no']}.{assignment['veh_class_no']}"
            entry["decision_no"] = dec["no"]
            flat.append(entry)
    return flat


def _signal_to_contract_shape(info: dict[str, Any]) -> dict[str, Any]:
    """Map a signal_parser SignalInfo down to the M0.3 contract shape.

    ``controller_type`` is now populated for real (v2 unit ①): signal_parser
    extracts the verbatim ``<signalController type="...">`` attribute, so
    constraints.py #4 (``signal_controller_type_unsupported``) can finally
    distinguish COM-writable fixed-time controllers from RBC/VAP/Econolite
    ones. ``min_green_intergreen_sum`` remains OMITTED (not set to None) —
    signal_parser still doesn't expose it (DECISIONS.md #5 gap entry).
    """
    return {
        "sc_no": info["sc_no"],
        "sc_name": info["sc_name"],
        "cycle_time": info["cycle_time"],
        "controller_type": info.get("controller_type", "unknown"),
        "phases": info["phases"],
    }


def _flatten_signal_program_splits(signal_programs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A flat, one-entry-per-(controller, program, signal group) view of
    ``signal_programs``, keyed by a synthesized ``"{sc_no}.{prog_no}.
    {sg_no}"`` ``id`` — the FIRST three-part identity in this codebase, for
    the same reason routes/composition entries needed two parts: a green
    split's real identity is the (controller, program, signal group) triple
    (the SAME signal group carries a DIFFERENT green window in each program
    — Beijing's SG 1 is green 1–15 s in program 2 but wraps 130→15 s in
    program 1). ``green_window`` holds BOTH edges as one dict so
    ``guardrails.py``'s single ``value_field`` compare (recursive,
    numeric-tolerant ``_values_differ``) covers the whole split as one
    write target. ``None`` when the SG's command shape is not a simple
    one-green-window program (the write path refuses those)."""
    flat: list[dict[str, Any]] = []
    for controller in signal_programs:
        for prog in controller.get("programs", []):
            for split in prog.get("sg_splits", []):
                green_window = None
                if split.get("simple") and split.get("green_start_s") is not None:
                    green_window = {
                        "green_start_s": split["green_start_s"],
                        "green_end_s": split["green_end_s"],
                    }
                flat.append(
                    {
                        "id": f"{controller['sc_no']}.{prog['prog_no']}.{split['sg_no']}",
                        "sc_no": controller["sc_no"],
                        "program_no": prog["prog_no"],
                        "sg_no": split["sg_no"],
                        "sg_name": split.get("sg_name", ""),
                        "green_window": green_window,
                        "amber_s": split.get("amber_s"),
                        "min_green_s": split.get("min_green_s"),
                        "head_count": split.get("head_count", 0),
                        "cycle_s": prog.get("cycle_s"),
                        "active": str(prog.get("prog_no")) == str(controller.get("active_program_no") or ""),
                    }
                )
    return flat


def _build_shadow_model_uncached(inpx_path: Path) -> dict[str, Any]:
    net = parse_network(str(inpx_path))
    root = ET.parse(inpx_path).getroot()
    signals_by_sc_no = parse_signal_controllers(inpx_path)
    link_ids = {str(link.get("id", "")) for link in net["links"]}
    routing_decisions = _parse_routing_decisions(root, link_ids)
    vehicle_compositions = _parse_vehicle_compositions(root)
    des_speed_decisions = _parse_des_speed_decisions(root)
    signal_programs = parse_signal_programs(inpx_path)
    priority_rules = _parse_priority_rules(root)
    vehicle_composition_entries = _flatten_vehicle_composition_entries(vehicle_compositions)
    # QUEUE (v2a) unit: vehicle type properties. Each of the 8 reference
    # target collections is parsed ONCE here (not re-parsed once per vehicle
    # type inside _parse_vehicle_types) and exposed as its own top-level
    # shadow-model list too -- both so constraints.py's
    # vehicle_type_reference_target_not_found can validate a proposed
    # target_no against the correct collection for the action's own "field",
    # and so a future read tool could query them directly (not shipped this
    # unit -- see get_vehicle_types' own docstring for the scope cut).
    model2d3d_distributions = _parse_model2d3d_distributions(root)
    weight_distributions = _parse_generic_distribution_stats(root, "weightDistributions", "weightDistribution")
    power_distributions = _parse_generic_distribution_stats(root, "powerDistributions", "powerDistribution")
    des_accel_functions = _parse_function_names(root, "desAccelerationFunctions", "desAccelerationFunction")
    max_accel_functions = _parse_function_names(root, "maxAccelerationFunctions", "maxAccelerationFunction")
    des_decel_functions = _parse_function_names(root, "desDecelerationFunctions", "desDecelerationFunction")
    max_decel_functions = _parse_function_names(root, "maxDecelerationFunctions", "maxDecelerationFunction")
    curve_speed_functions = _parse_function_names(root, "curvSpeedFuncs", "curvSpeedFunc")
    # QUEUE 3 unit 6: link_behavior_types is parsed first -- both driving_
    # behaviors' own reference graph and the flattened assignments list need
    # it.
    link_behavior_types = _parse_link_behavior_types(root, _link_behavior_type_link_counts(root))
    driving_behaviors = _parse_driving_behaviors(root, link_behavior_types)
    link_behavior_type_assignments = _flatten_link_behavior_type_assignments(
        link_behavior_types, driving_behaviors
    )
    return {
        "links": net["links"],
        "nodes": net["nodes"],
        "vehicle_inputs": _parse_vehicle_inputs(root),
        "signals": [_signal_to_contract_shape(info) for info in signals_by_sc_no.values()],
        "conflict_areas": _parse_conflict_areas(root),
        # v2b unit (1): flat per-lane closure state, see _parse_link_lanes's
        # own docstring.
        "link_lanes": _parse_link_lanes(root),
        # Q4 unit (2): measurement-infrastructure placement -- queue
        # counters, data-collection points/measurements, travel-time
        # sections. See each parser's own docstring.
        "queue_counters": _parse_queue_counters(root),
        "data_collection_points": _parse_data_collection_points(root),
        "data_collection_measurements": _parse_data_collection_measurements(root),
        "travel_time_sections": _parse_travel_time_sections(root),
        # Q4 unit (4): signal-head + detector placement -- see each parser's
        # own docstring.
        "signal_heads": _parse_signal_heads(root),
        "detectors": _parse_detectors(root),
        "routing_decisions": routing_decisions,
        "routes": _flatten_routes(routing_decisions),
        "vehicle_compositions": vehicle_compositions,
        "vehicle_composition_entries": vehicle_composition_entries,
        # v2a unit ⑧
        "des_speed_decisions": des_speed_decisions,
        "des_speed_decision_entries": _flatten_des_speed_decision_entries(des_speed_decisions),
        "des_speed_distributions": _parse_speed_distributions(root, des_speed_decisions, vehicle_compositions),
        # v2a unit ⑨: fixed-time signal-program green splits — nested for the
        # planner (get_signal_programs), flat for guardrails.py (the
        # nothing-else-moved diff + target-persisted check), same split as
        # routes/composition/des-speed entries. See signal_parser.
        # parse_signal_programs's docstring for the storage-reality findings.
        "signal_programs": signal_programs,
        "signal_program_splits": _flatten_signal_program_splits(signal_programs),
        # health-check unit (2026-07-28): static evaluation-armed flags, see
        # _parse_evaluation_config's own docstring.
        "evaluation_config": _parse_evaluation_config(root),
        # QUEUE 3 unit 2 (2026-07-29): priority-rule conflict-marker
        # gap/headway parameters -- nested for the planner
        # (get_priority_rules), flat for guardrails.py (the nothing-else-
        # moved diff + target-persisted check), same split as
        # routes/composition/des-speed entries. See _parse_priority_rules's
        # own docstring for the live-verified findings.
        "priority_rules": priority_rules,
        "priority_rule_markers": _flatten_priority_rule_markers(priority_rules),
        # QUEUE (v2a) unit: vehicle type properties (scalars + references
        # only -- see _parse_vehicle_types' own docstring). The 8 target
        # collections are exposed as their own flat lists too, purely so
        # constraints.py's vehicle_type_reference_target_not_found can
        # validate a proposed target_no against the collection matching the
        # action's own "field" param.
        "vehicle_types": _parse_vehicle_types(
            root,
            vehicle_composition_entries,
            model2d3d_distributions,
            weight_distributions,
            power_distributions,
            des_accel_functions,
            max_accel_functions,
            des_decel_functions,
            max_decel_functions,
            curve_speed_functions,
        ),
        "model2d3d_distributions": list(model2d3d_distributions.values()),
        "weight_distributions": list(weight_distributions.values()),
        "power_distributions": list(power_distributions.values()),
        "des_accel_functions": list(des_accel_functions.values()),
        "max_accel_functions": list(max_accel_functions.values()),
        "des_decel_functions": list(des_decel_functions.values()),
        "max_decel_functions": list(max_decel_functions.values()),
        "curve_speed_functions": list(curve_speed_functions.values()),
        # QUEUE (Q3, v2a) unit 5: dwell-time distributions -- see
        # _parse_time_distributions' own docstring for the live-verified
        # findings (a genuinely shared collection, referenced by PT-stop
        # dwell assignments, driving-behavior CC1/zipper-overtake time,
        # parking direction-change duration, and pedestrian-area wait time
        # -- NOT stop signs, disproving this unit's own task brief).
        "time_distributions": _parse_time_distributions(root),
        # QUEUE 3 unit 6 (2026-07-31): driving-behavior duplicate-then-edit.
        "link_behavior_types": link_behavior_types,
        "driving_behaviors": driving_behaviors,
        "link_behavior_type_assignments": link_behavior_type_assignments,
    }


@lru_cache(maxsize=8)
def _build_shadow_model_cached(inpx_path_str: str, mtime: float) -> dict[str, Any]:
    del mtime  # cache-busting arg only, mirrors assistant_router.py's precedent
    return _build_shadow_model_uncached(Path(inpx_path_str))


def build_shadow_model(inpx_path: Path) -> dict[str, Any]:
    """Assemble the extended agent shadow model for ``inpx_path``, mtime-cached."""
    inpx_path = Path(inpx_path)
    mtime = os.path.getmtime(inpx_path)
    return _build_shadow_model_cached(str(inpx_path), mtime)


def _current_shadow_model() -> dict[str, Any]:
    return build_shadow_model(_active_inpx_path())


# ---------------------------------------------------------------------------
# get_network_overview
# ---------------------------------------------------------------------------


def _compact_object_list(
    objects: list[dict[str, Any]], id_field: str, name_field: str, *, limit: int = _OVERVIEW_LIST_LIMIT
) -> dict[str, Any]:
    items = [{"id": str(obj.get(id_field, "")), "name": str(obj.get(name_field, ""))} for obj in objects]
    return {
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


class GetNetworkOverviewParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_network_overview", tier="read", provider="inspect", params=GetNetworkOverviewParams)
def get_network_overview(params: GetNetworkOverviewParams) -> dict[str, Any]:
    """Return object counts and compact id/name lists for links, nodes, vehicle inputs, signals, conflict areas, routing decisions, and vehicle compositions."""
    del params  # no fields
    shadow = _current_shadow_model()
    return {
        spec["shadow_key"]: _compact_object_list(
            shadow.get(spec["shadow_key"], []), spec["id_field"], spec["name_field"]
        )
        for spec in _OBJECT_TYPE_SPECS.values()
    }


# ---------------------------------------------------------------------------
# resolve_object
# ---------------------------------------------------------------------------


def _score(query: str, obj_id: str, obj_name: str) -> float:
    """Deterministic lexical score: exact > substring > shared-token fraction.

    Korean and English are treated identically — tokens split on whitespace,
    no morphological analysis (per the roadmap's network-RAG pattern note).
    """
    query_norm = (query or "").strip().lower()
    if not query_norm:
        return 0.0

    id_norm = (obj_id or "").strip().lower()
    name_norm = (obj_name or "").strip().lower()

    if query_norm == id_norm or query_norm == name_norm:
        return _SCORE_EXACT
    if query_norm in id_norm or query_norm in name_norm:
        return _SCORE_SUBSTRING

    query_tokens = set(query_norm.split())
    if not query_tokens:
        return 0.0
    field_tokens = set(name_norm.split()) | set(id_norm.split())
    overlap = query_tokens & field_tokens
    if not overlap:
        return 0.0
    return round(len(overlap) / len(query_tokens), 4)


def _iter_candidate_objects(
    shadow: dict[str, Any], object_type: str | None
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """(id, name, object_type, object) tuples for every object of the
    requested type(s) — every type when ``object_type`` is None. The object
    itself rides along so :func:`_candidate_disambiguators` can pull
    type-specific fields off it without a second lookup."""
    object_types = [object_type] if object_type else list(_OBJECT_TYPE_SPECS.keys())
    pool: list[tuple[str, str, str, dict[str, Any]]] = []
    for otype in object_types:
        spec = _OBJECT_TYPE_SPECS[otype]
        for obj in shadow.get(spec["shadow_key"], []):
            pool.append(
                (str(obj.get(spec["id_field"], "")), str(obj.get(spec["name_field"], "")), otype, obj)
            )
    return pool


def _candidate_disambiguators(otype: str, obj: dict[str, Any]) -> dict[str, Any]:
    """Extra, type-specific fields carried on a ``resolve_object`` candidate
    purely so the caller can tell same-named objects apart.

    Sibling-disambiguation unit (2026-07-28): on the real client model a
    vehicle input's ``name`` is shared verbatim by all three inputs on its
    link (one per vehicle class -- e.g. 107/207/307 are ALL "Jennalup ST_WB"),
    so id+name+score alone gave the planner three identical-looking rows and
    no basis for choosing. ``link`` + the composition tell them apart. Only
    non-empty fields are attached, so the candidate shape stays exactly as it
    was for every object type that has nothing extra to say (and for a
    network whose inputs carry no ``vehComp`` at all, e.g. the synthetic demo
    file -- nothing is fabricated)."""
    if otype != "vehicle_input":
        return {}
    extra: dict[str, Any] = {}
    link = str(obj.get("link", "") or "")
    if link:
        extra["link"] = link
    composition_no = str(obj.get("composition_no", "") or "")
    if composition_no:
        extra["composition_no"] = composition_no
    composition_name = str(obj.get("composition_name", "") or "")
    if composition_name:
        extra["composition_name"] = composition_name
    if obj.get("composition_varies_by_interval"):
        # Honest about the schema's per-interval vehComp: no single
        # composition to name, so hand back every one it does reference.
        extra["composition_nos"] = list(obj.get("composition_nos") or [])
    return extra


#: Bounded envelope for _difflib_candidates' fallback list below (standing
#: rule -- suggestion lists reach LLM prompts, see the observation-budget
#: incident). Mirrors constraints.py's _CANDIDATE_CAP verbatim, including the
#: reasoning: the real Murdoch model shares one name across groups of 3
#: vehicle inputs (one per vehicle class -- e.g. 107/207/307 are all
#: "Jennalup ST_WB"), so a single matched name can legitimately expand to 3
#: owners. 10 comfortably fits that common single-group case with room for a
#: couple more matches before truncating; a rarer multi-group match may get
#: cut off mid-group, which is an acceptable trade-off for keeping the list
#: bounded.
#:
#: v2 queue unit 2 (2026-07-31) re-measured this cap after enriching every
#: candidate from a bare id string to a disambiguator dict (this unit's own
#: fix, below) -- suggestion lists reaching an LLM prompt is exactly the
#: "more data can subtract information" lesson the 2026-07-29 get_vehicle_
#: inputs cap-down (DECISIONS.md) already established, so the growth had to
#: be measured, not assumed safe. MEASURED (json.dumps, compact separators,
#: matching planner._render_observation's own encoding, 3-digit ids): 10
#: bare id strings is 61 bytes; 10 {id,name,object_type} dicts (no vehicle-
#: input extras) is 561 bytes; 10 FULL vehicle-input dicts (id+name+
#: object_type+link+composition_no+composition_name, using the REAL link-620
#: values -- "Jennalup ST_WB"/"Class2-5(SLK 1.47_7-8AM&3-4PM)") is ~1440
#: bytes. Against the 12000-char most-recent-observation budget
#: (_DEFAULT_RECENT_OBS_BUDGET, planner.py) that is 12% even in the worst
#: case -- nowhere near the 42KB-vs-12KB overage that forced
#: get_vehicle_inputs' own cap down. The cap stays 10: the per-candidate
#: growth is real (roughly 24x bare-id size at the worst case) but the
#: absolute numbers are small relative to the budget
#: that matters most (the observation the model is actively reasoning about
#: when it just made the failing call), so shrinking it would trade away the
#: disambiguation this unit exists to add for headroom the budget does not
#: need. Older, non-most-recent history entries render under the much
#: tighter 300-char snippet budget (_OBSERVATION_SNIPPET_CHARS) where a full
#: 10-entry enriched list WILL trigger _render_observation's existing item-
#: boundary truncation (fewer items + an explicit "N/M shown" marker) or its
#: compact-summary tiers -- by design, the same graceful, bounded
#: degradation every other oversized list in this codebase already goes
#: through; not a new failure mode.
_CANDIDATE_CAP = 10


def _difflib_candidates(
    query: str, pool: list[tuple[str, str, str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Fuzzy-match ``query`` against every object's id and name, always
    resolving a hit back to the owning object's id(s) -- returned as
    disambiguator dicts (id/name/object_type + type-specific extras), never
    bare id strings.

    Mirrors constraints.py's ``_check_object_not_found`` id-resolution
    behavior (that helper is private and tool-specific, so this is a
    deliberate mirror rather than an import — see the M1.2 brief).

    v2 queue unit 1 (2026-07-29): the sibling-disambiguation unit (2026-07-28,
    above in :func:`_candidate_disambiguators`) fixed resolve_object's HIT
    path -- a ranked match already returned every sibling. This MISS-path
    fallback still collapsed a fuzzy NAME match to its first-registered
    owner, because a name is not always unique: the real Murdoch model
    shares one name across groups of 3 vehicle inputs (one per vehicle
    class -- e.g. 107/207/307 are all "Jennalup ST_WB"). A fuzzy match
    against that shared name now resolves to EVERY owner, not just the
    first. id strings stay 1:1 with themselves (ids are unique); only name
    strings fan out to a list of every owner that shares them, built in pool
    order for a stable result.

    v2 queue unit 2 (2026-07-31, live-demo defect): fanning out to every
    owner id fixed WHICH ids came back, but they were still bare, anonymous
    strings. A live turn asked to resolve the typo "Jenalup STWB" against
    ``object_type="vehicle_input"``; the miss returned
    ``["107","207","307","111","211","311"]`` -- six ids with no way to
    tell the "Jennalup ST_WB" trio from the "Jennalup St" trio apart. The
    model re-issued the identical ``resolve_object`` call, hit the
    ``duplicate_call`` anti-loop refusal, repeated it four more times, and
    the turn died at the 6-iteration cap on an apology -- a turn-killing UX
    failure caused by an unhelpful-but-successful observation, not a
    hypothetical. Every candidate is now a dict carrying the SAME shape the
    HIT path already returns (id, name, object_type, plus whatever
    :func:`_candidate_disambiguators` adds for that object type -- reused
    here, not reimplemented), so a caller reading either path's candidates
    sees one consistent shape and the two trios above render distinguishably
    as ``{"id": "107", "name": "Jennalup ST_WB", ...}`` vs
    ``{"id": "111", "name": "Jennalup St", ...}``.
    """
    owner_ids_by_string: dict[str, list[str]] = {}
    id_claimed: set[str] = set()
    match_pool: list[str] = []
    entry_by_id: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for obj_id, name, otype, obj in pool:
        if obj_id and obj_id not in entry_by_id:
            entry_by_id[obj_id] = (name, otype, obj)
        if obj_id and obj_id not in owner_ids_by_string:
            match_pool.append(obj_id)
            owner_ids_by_string[obj_id] = [obj_id]
            id_claimed.add(obj_id)
        if name:
            if name not in owner_ids_by_string:
                match_pool.append(name)
                owner_ids_by_string[name] = [obj_id]
            elif name not in id_claimed and obj_id not in owner_ids_by_string[name]:
                owner_ids_by_string[name].append(obj_id)

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for match in difflib.get_close_matches(query, match_pool, n=_TOP_K):
        for owner_id in owner_ids_by_string[match]:
            if owner_id in seen_ids:
                continue
            seen_ids.add(owner_id)
            owner_name, owner_otype, owner_obj = entry_by_id.get(owner_id, ("", "", {}))
            candidates.append(
                {
                    "id": owner_id,
                    "name": owner_name,
                    "object_type": owner_otype,
                    **_candidate_disambiguators(owner_otype, owner_obj),
                }
            )
            if len(candidates) >= _CANDIDATE_CAP:
                break
        if len(candidates) >= _CANDIDATE_CAP:
            break
    return candidates


class ResolveObjectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    object_type: ObjectType | None = None


@tool(name="resolve_object", tier="read", provider="inspect", params=ResolveObjectParams)
def resolve_object(params: ResolveObjectParams) -> dict[str, Any]:
    """Lexically resolve a name/id query to ranked candidate network objects
    (top-5). On a miss, ``candidates`` are disambiguator dicts (id, name,
    object_type, plus type-specific fields like link/composition for
    vehicle inputs) -- never bare ids -- so same-named siblings (e.g. the
    real model's "Jennalup ST_WB"/"Jennalup St" vehicle-input trios) stay
    tellable apart on BOTH the hit and miss paths."""
    shadow = _current_shadow_model()
    pool = _iter_candidate_objects(shadow, params.object_type)

    scored = [
        {
            "id": obj_id,
            "name": name,
            "object_type": otype,
            "score": score,
            **_candidate_disambiguators(otype, obj),
        }
        for obj_id, name, otype, obj in pool
        if (score := _score(params.query, obj_id, name)) > 0
    ]
    scored.sort(key=lambda candidate: (-candidate["score"], candidate["id"]))
    top = scored[:_TOP_K]

    if not top:
        return {
            "found": False,
            "error_type": "object_not_found",
            "candidates": _difflib_candidates(params.query, pool),
        }
    return {"found": True, "candidates": top}


# ---------------------------------------------------------------------------
# get_vehicle_inputs / get_signal_controllers
#
# FIX 1 (2026-07-28) -- two compounding, pre-existing defects the sibling-
# disambiguation unit's new composition fields made undeniable. Both are
# fixed the same way get_conflict_areas' D1 live-demo fix was (2026-07-27):
# read that constraint's own comment block below before touching this one,
# same shape is followed here.
#
# (a) Payload bloat is fixed at the SOURCE (``_parse_vehicle_inputs``'s own
#     "Omit-when-uniform" docstring section above) -- nothing to do here.
#
# (b) No filters + document-order truncation. Measured live against the real
#     51-input Murdoch Drive model (backend/tools/... probe, scratch-only,
#     not committed): the .inpx declares every <vehicleInput> in
#     composition-MAJOR order -- ALL 17 class-"1" inputs first, THEN all 17
#     class-"2", THEN all 17 class-"3" -- so an item-boundary truncation that
#     keeps a document-order PREFIX (planner._render_observation's own
#     truncation, or any naive tool-side cap) keeps exactly ONE vehicle class
#     network-wide and drops every other class's inputs entirely. That is the
#     measured bug: an unfiltered call at the planner's 12000-char recent-
#     observation budget showed 15/51, every one class "1"; 207 and 307 (the
#     worked example's non-Class1 siblings) were not in the observation at
#     all. GetVehicleInputsParams gained input_no/link_no/composition_no
#     filters so the model CAN narrow (previously ``model_fields == {}}``,
#     making the truncation marker's "narrow your query" advice
#     unfollowable), and an unfiltered call now (1) reorders its OWN display
#     list (see :func:`_group_vehicle_inputs_by_link` -- the shadow model's
#     own ``vehicle_inputs`` order is untouched; every other consumer,
#     constraints.py/health_check.py/approval_focus.py, is unaffected) so a
#     shown PREFIX is a prefix of COMPLETE sibling groups instead of one
#     class, and (2) applies a measured default cap + explicit machine-
#     readable ``truncated`` flag + ``hint``, exactly like get_conflict_areas.
# ---------------------------------------------------------------------------

#: Item cap for an UNFILTERED get_vehicle_inputs call -- MEASURED (not
#: guessed) against the real 51-input model, post the per-item trimming
#: above: one item there averages ~550-600 chars (a multi-interval
#: ``volumes`` list is the dominant cost, unlike get_conflict_areas' much
#: flatter ~120 chars/item), so 40 (that constraint's own cap) would already
#: be ~22KB, nearly double the 12000-char recent-observation budget on its
#: own. 20 items (grouped by link -- see above) measured at 11391 envelope
#: chars including the truncated+hint fields, comfortably under budget with
#: margin, and -- the acceptance case -- still covers the worked example
#: (link 620, the 6th distinct link, is fully inside the first 20 grouped
#: items) with two items of slack besides.
_VEHICLE_INPUTS_DEFAULT_CAP = 20


class GetVehicleInputsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_no: str | None = Field(
        default=None,
        description="Exact vehicle input 'no' to return (narrows to at most one result).",
    )
    link_no: str | None = Field(
        default=None,
        description=(
            "Return every vehicle input on this link -- e.g. every sibling sharing one entry "
            "point (several vehicle inputs, one per vehicle class, commonly share a link)."
        ),
    )
    composition_no: str | None = Field(
        default=None,
        description="Return only vehicle inputs that feed this vehicle composition.",
    )


def _vehicle_input_matches_composition(vehicle_input: dict[str, Any], composition_no: str) -> bool:
    if str(vehicle_input.get("composition_no", "") or "") == composition_no:
        return True
    return composition_no in {str(n) for n in (vehicle_input.get("composition_nos") or [])}


def _group_vehicle_inputs_by_link(vehicle_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``vehicle_inputs`` REORDERED so every link's own inputs sit adjacent,
    in link first-appearance order -- this tool's OWN display order only
    (the shadow model's ``vehicle_inputs`` list keeps document order for
    every other consumer; nothing else reads this reordering). See the FIX 1
    comment block above for the measured reason this exists: document order
    on the real model is composition-class-major (every class-1 input, THEN
    every class-2, THEN every class-3), so a document-order prefix is
    class-blind; a link-grouped prefix is a prefix of COMPLETE sibling
    groups instead. Stable sort (Python's ``sorted``) preserves each link's
    own inputs in their original relative order (e.g. 107 before 207 before
    307), it only moves WHICH link's group comes next."""
    first_seen: dict[str, int] = {}
    for vi in vehicle_inputs:
        link = str(vi.get("link", "") or "")
        if link not in first_seen:
            first_seen[link] = len(first_seen)
    return sorted(vehicle_inputs, key=lambda vi: first_seen.get(str(vi.get("link", "") or ""), -1))


@tool(name="get_vehicle_inputs", tier="read", provider="inspect", params=GetVehicleInputsParams)
def get_vehicle_inputs(params: GetVehicleInputsParams) -> dict[str, Any]:
    """Return the network's vehicle input objects (no, name, link, volume, per-interval volumes, and the vehicle composition each one feeds -- composition_no/composition_name; several inputs commonly share ONE link and ONE name, one per vehicle class, and the composition is what tells them apart). Optionally filter by input_no (exact match), link_no (every vehicle input on that link -- e.g. every sibling at one entry point), or composition_no (every input feeding that composition). An unfiltered call on a large network returns only the first 20, GROUPED BY LINK so the shown prefix is a prefix of complete sibling groups rather than a single vehicle class (with an explicit truncated flag and a hint) -- narrow with these filters, especially link_no, rather than requesting the whole list."""
    vehicle_inputs = _current_shadow_model()["vehicle_inputs"]

    if params.input_no is not None:
        vehicle_inputs = [vi for vi in vehicle_inputs if str(vi.get("no")) == str(params.input_no)]
    if params.link_no is not None:
        vehicle_inputs = [vi for vi in vehicle_inputs if str(vi.get("link")) == str(params.link_no)]
    if params.composition_no is not None:
        vehicle_inputs = [
            vi for vi in vehicle_inputs if _vehicle_input_matches_composition(vi, str(params.composition_no))
        ]

    total = len(vehicle_inputs)
    filtered = params.input_no is not None or params.link_no is not None or params.composition_no is not None
    if filtered:
        # A filtered call is already expected to return a small, bounded
        # result -- the model asked for something specific, so no cap.
        return {"count": total, "vehicle_inputs": vehicle_inputs}

    capped = _group_vehicle_inputs_by_link(vehicle_inputs)[:_VEHICLE_INPUTS_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {"count": total, "vehicle_inputs": capped, "truncated": truncated}
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} vehicle inputs (grouped by link). Narrow with "
            "input_no, link_no, or composition_no to see a specific one instead of the whole list."
        )
    return result


class GetSignalControllersParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_signal_controllers", tier="read", provider="inspect", params=GetSignalControllersParams)
def get_signal_controllers(params: GetSignalControllersParams) -> dict[str, Any]:
    """Return the network's signal controller objects (id, name, cycle time, phases)."""
    del params  # no fields
    signals = _current_shadow_model()["signals"]
    return {"count": len(signals), "signals": signals}


# ---------------------------------------------------------------------------
# get_conflict_areas — v2 unit (4)
#
# D1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): an unfiltered call
# against a real (not the empty demo) network returns 224 conflict areas,
# ~27.6KB -- comfortably inside AGENT_OBS_RECENT_BUDGET's 12000-char default
# for the MOST RECENT history entry, but every other rendering of the same
# result (any older entry, and every subsequent turn's history window) hits
# the much smaller _OBSERVATION_SNIPPET_CHARS budget, where a 224-item list
# needs truncation. Rather than depend entirely on planner.py's renderer to
# cut a list the tool itself knows is unbounded, this now (a) accepts
# optional server-side filters so the model can narrow BEFORE the envelope
# is built, and (b) gives an unfiltered call a bounded default (full count +
# a capped item list + an explicit machine-readable ``truncated`` flag + a
# ``hint`` nudging the model to filter) instead of ever handing back the
# whole unbounded list.
# ---------------------------------------------------------------------------

#: Item cap for an UNFILTERED get_conflict_areas call -- comfortably inside
#: even the compact _OBSERVATION_SNIPPET_CHARS budget for a typical entry
#: shape (~120 chars/item; 40 items is well under the recent-entry budget
#: too), so a bounded default never itself needs planner-side truncation.
_CONFLICT_AREAS_DEFAULT_CAP = 40


class GetConflictAreasParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_area_no: str | None = Field(
        default=None,
        description="Exact conflict area 'no' to return (narrows to at most one result).",
    )
    link_no: str | None = Field(
        default=None,
        description=(
            "Return only conflict areas where this link participates (either side of "
            "link_pair)."
        ),
    )


def _conflict_area_matches_link(conflict_area: dict[str, Any], link_no: str) -> bool:
    return link_no in {str(x) for x in conflict_area.get("link_pair", ())}


@tool(name="get_conflict_areas", tier="read", provider="inspect", params=GetConflictAreasParams)
def get_conflict_areas(params: GetConflictAreasParams) -> dict[str, Any]:
    """Return the network's conflict areas (id, link pair, right-of-way status) -- conflict areas default to Passive (no right-of-way control, vehicles drive through each other) and this is how the agent sees which ones are Passive before deciding whether to fix any with set_conflict_area_status. Optionally filter by conflict_area_no (exact match) or link_no (either side of the pair) -- an unfiltered call on a large network returns only the first 40 (with an explicit truncated flag and a hint), so narrow with these filters rather than requesting the whole list."""
    conflict_areas = _current_shadow_model()["conflict_areas"]

    if params.conflict_area_no is not None:
        conflict_areas = [
            ca for ca in conflict_areas if str(ca.get("no")) == str(params.conflict_area_no)
        ]
    if params.link_no is not None:
        conflict_areas = [
            ca for ca in conflict_areas if _conflict_area_matches_link(ca, str(params.link_no))
        ]

    total = len(conflict_areas)
    filtered = params.conflict_area_no is not None or params.link_no is not None
    if filtered:
        # A filtered call is already expected to return a small, bounded
        # result -- the model asked for something specific, so no cap.
        return {"count": total, "conflict_areas": conflict_areas}

    capped = conflict_areas[:_CONFLICT_AREAS_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {
        "count": total,
        "conflict_areas": capped,
        "truncated": truncated,
    }
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} conflict areas. Narrow with "
            "conflict_area_no or link_no to see a specific one instead of the whole list."
        )
    return result


# ---------------------------------------------------------------------------
# get_routing_decisions — v2 unit ⑥
# ---------------------------------------------------------------------------


class GetRoutingDecisionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_routing_decisions", tier="read", provider="inspect", params=GetRoutingDecisionsParams)
def get_routing_decisions(params: GetRoutingDecisionsParams) -> dict[str, Any]:
    """Return the network's static vehicle routing decisions (id, origin link, and each alternative route's destination link + relative-flow profile) -- relative flow is a RAW weight, not a pre-computed percentage: Vissim derives each route's actual traffic share by normalizing its relative flow against the SUM of every route's relative flow in the SAME decision at simulation time, so use set_route_relative_flow to change one route's split rather than assuming the number itself is a proportion."""
    del params  # no fields
    routing_decisions = _current_shadow_model()["routing_decisions"]
    return {"count": len(routing_decisions), "routing_decisions": routing_decisions}


# ---------------------------------------------------------------------------
# get_vehicle_compositions — v2 unit ⑦
#
# BOUNDED OUTPUT from day one (the 2026-07-27 lesson, see get_conflict_areas
# above): an unfiltered call caps the COMPOSITIONS list (not each
# composition's own entries, which are naturally small -- a handful of
# vehicle types per composition on every real example network probed) at
# _VEHICLE_COMPOSITIONS_DEFAULT_CAP, with an explicit truncated flag + hint,
# and a composition_no filter to narrow.
# ---------------------------------------------------------------------------

#: Item cap for an UNFILTERED get_vehicle_compositions call, same discipline
#: as _CONFLICT_AREAS_DEFAULT_CAP -- each composition entry is small (no/
#: name/entries/referencing_vehicle_input_nos, entries themselves bounded to
#: a handful of vehicle types), so 40 compositions stays comfortably inside
#: even the compact snippet budget.
_VEHICLE_COMPOSITIONS_DEFAULT_CAP = 40


def _estimated_share_pct(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each entry plus its ``estimated_share_pct`` -- RelFlow is a raw
    weight (v2 unit ⑦ probe: Vissim does NOT renormalize sibling entries on
    write, exactly like route RelFlow), so the actual simulated composition
    share is only ever a DERIVED number: this entry's ``rel_flow`` divided
    by the sum of every entry's ``rel_flow`` in the SAME composition.
    ``None`` when the sum is not strictly positive or any value is missing
    (never guess)."""
    total = 0.0
    all_known = True
    for entry in entries:
        rel_flow = entry.get("rel_flow")
        if rel_flow is None:
            all_known = False
            break
        total += rel_flow
    out: list[dict[str, Any]] = []
    for entry in entries:
        share_pct: float | None = None
        rel_flow = entry.get("rel_flow")
        if all_known and rel_flow is not None and total > 0:
            share_pct = round((rel_flow / total) * 100, 1)
        out.append({**entry, "estimated_share_pct": share_pct})
    return out


class GetVehicleCompositionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition_no: str | None = Field(
        default=None,
        description="Exact vehicle composition 'no' to return (narrows to at most one result).",
    )


@tool(name="get_vehicle_compositions", tier="read", provider="inspect", params=GetVehicleCompositionsParams)
def get_vehicle_compositions(params: GetVehicleCompositionsParams) -> dict[str, Any]:
    """Return the network's vehicle compositions (id, name, and each vehicle type's relative-flow weight + estimated resulting share %) plus which vehicle inputs reference each composition -- relative flow (RelFlow) is a RAW weight, not a pre-computed percentage: Vissim derives each vehicle type's actual composition share by normalizing its relative flow against the SUM of every vehicle type's relative flow in the SAME composition at simulation time, so use set_vehicle_composition_share to change one vehicle type's split rather than assuming the number itself is a proportion. Optionally filter by composition_no (exact match) -- an unfiltered call on a large network returns only the first 40 (with an explicit truncated flag and a hint), so narrow with this filter rather than requesting the whole list."""
    vehicle_compositions = _current_shadow_model()["vehicle_compositions"]

    if params.composition_no is not None:
        vehicle_compositions = [
            comp for comp in vehicle_compositions if str(comp.get("no")) == str(params.composition_no)
        ]

    total = len(vehicle_compositions)
    filtered = params.composition_no is not None
    if not filtered:
        capped = vehicle_compositions[:_VEHICLE_COMPOSITIONS_DEFAULT_CAP]
        truncated = total > len(capped)
        vehicle_compositions = capped
    else:
        truncated = False

    rendered = [
        {**comp, "entries": _estimated_share_pct(comp["entries"])} for comp in vehicle_compositions
    ]

    result: dict[str, Any] = {"count": total, "vehicle_compositions": rendered}
    if not filtered:
        result["truncated"] = truncated
        if truncated:
            result["hint"] = (
                f"Showing {len(rendered)} of {total} vehicle compositions. Narrow with "
                "composition_no to see a specific one instead of the whole list."
            )
    return result


# ---------------------------------------------------------------------------
# get_des_speed_decisions / get_des_speed_distributions -- v2a unit ⑧
#
# BOUNDED OUTPUT from day one (the 2026-07-27 lesson): a single real network
# can carry hundreds of desSpeedDecision elements (Signalization RBC
# Boise.US: 283) -- an unfiltered call caps the list at
# _DES_SPEED_DECISIONS_DEFAULT_CAP with an explicit truncated flag + hint,
# and decision_no/link_no filters to narrow (link_no mirrors
# get_conflict_areas' own link_no filter -- decisions, like conflict areas,
# are lane/link-scoped objects a user might ask about "on link 6").
#
# TWO separate read tools, not one nested response, is a deliberate choice
# (the task brief left this open): distributions are a SHARED lookup table
# (also used by set_reduced_speed_area, v2 unit ②) the model only needs when
# it doesn't already know a valid distribution number to propose -- bundling
# the full distributions list into EVERY get_des_speed_decisions call would
# repeat that payload on every read even when the model already has it from
# an earlier turn (or already knows the number the user asked for), the same
# "don't pay for something you don't need every time" reasoning
# get_vehicle_inputs/get_signal_controllers already follow by being separate
# small tools rather than folding into get_network_overview.
# ---------------------------------------------------------------------------

#: Item cap for an UNFILTERED get_des_speed_decisions call -- Boise.US alone
#: has 283 real decisions; 40 stays comfortably inside even the compact
#: snippet budget for a typical entry shape (a handful of veh-class
#: assignments each).
_DES_SPEED_DECISIONS_DEFAULT_CAP = 40

#: Item cap for an UNFILTERED get_des_speed_distributions call -- every real
#: example network probed carries well under 100 speed distributions
#: (Roundabout Billings.US: 13; shared with pedestrian/reduced-speed-area
#: distributions), but the cap discipline is applied uniformly regardless.
_DES_SPEED_DISTRIBUTIONS_DEFAULT_CAP = 60


class GetDesSpeedDecisionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_no: str | None = Field(
        default=None,
        description="Exact desired-speed decision 'no' to return (narrows to at most one result).",
    )
    link_no: str | None = Field(
        default=None,
        description="Return only decisions positioned on this link.",
    )


@tool(name="get_des_speed_decisions", tier="read", provider="inspect", params=GetDesSpeedDecisionsParams)
def get_des_speed_decisions(params: GetDesSpeedDecisionsParams) -> dict[str, Any]:
    """Return the network's desired-speed decisions (id, link/lane/position, and each vehicle class's assigned speed-distribution) -- a desired-speed decision is PERMANENT once a vehicle passes it: assigning a slower distribution here stays in effect for the rest of that vehicle's trip unless a SECOND decision immediately downstream restores the original distribution (there is no structural link between the two in Vissim -- pairing is purely by matching link/position, so identify the restore decision yourself before calling set_des_speed_decision with restore_decision_no). Optionally filter by decision_no (exact match) or link_no -- an unfiltered call on a large network returns only the first 40 (with an explicit truncated flag and a hint), so narrow with these filters rather than requesting the whole list."""
    decisions = _current_shadow_model()["des_speed_decisions"]

    if params.decision_no is not None:
        decisions = [d for d in decisions if str(d.get("no")) == str(params.decision_no)]
    if params.link_no is not None:
        decisions = [d for d in decisions if str(d.get("link")) == str(params.link_no)]

    total = len(decisions)
    filtered = params.decision_no is not None or params.link_no is not None
    if filtered:
        return {"count": total, "des_speed_decisions": decisions}

    capped = decisions[:_DES_SPEED_DECISIONS_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {"count": total, "des_speed_decisions": capped, "truncated": truncated}
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} desired-speed decisions. Narrow with "
            "decision_no or link_no to see a specific one instead of the whole list."
        )
    return result


class GetDesSpeedDistributionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distribution_no: str | None = Field(
        default=None,
        description="Exact speed distribution 'no' to return (narrows to at most one result).",
    )


@tool(
    name="get_des_speed_distributions",
    tier="read",
    provider="inspect",
    params=GetDesSpeedDistributionsParams,
)
def get_des_speed_distributions(params: GetDesSpeedDistributionsParams) -> dict[str, Any]:
    """Return the network's speed distributions -- id, name, the cumulative curve's data points (speed_kmh, cum_fraction; capped at 30 per distribution with a points_truncated flag -- no real network has ever needed more), and referenced_by (how many desired-speed decisions, vehicle compositions, reduced-speed areas, and other network elements point at this distribution, each a {count, ids, truncated} bounded list, plus reference_count as the grand total -- editing a distribution's points affects EVERY one of these). These are the valid targets for set_des_speed_decision's des_speed_distr_no/restore_distr_no and set_des_speed_distribution_points' distribution_no; a number not in this list will be refused before it ever reaches Vissim. Shared with set_reduced_speed_area (one distribution object type, several features). Optionally filter by distribution_no -- an unfiltered call on a large network returns only the first 60 (with an explicit truncated flag and a hint)."""
    distributions = _current_shadow_model()["des_speed_distributions"]

    if params.distribution_no is not None:
        distributions = [d for d in distributions if str(d.get("no")) == str(params.distribution_no)]

    total = len(distributions)
    filtered = params.distribution_no is not None
    if filtered:
        return {"count": total, "des_speed_distributions": distributions}

    capped = distributions[:_DES_SPEED_DISTRIBUTIONS_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {"count": total, "des_speed_distributions": capped, "truncated": truncated}
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} speed distributions. Narrow with distribution_no "
            "to see a specific one instead of the whole list."
        )
    return result


# ---------------------------------------------------------------------------
# get_signal_programs -- v2a unit ⑨
#
# BOUNDED OUTPUT from day one (the 2026-07-27 lesson): a real network can
# carry many controllers (Karlsruhe 3D: 19), each with up to 22 signal
# groups per program and multiple programs -- an unfiltered call caps the
# controller list at _SIGNAL_PROGRAMS_DEFAULT_CAP with an explicit truncated
# flag + hint, and controller_no narrows to one.
# ---------------------------------------------------------------------------

#: Controller cap for an UNFILTERED get_signal_programs call. Beijing's one
#: controller alone renders 2 programs x 22 SGs; ten controllers of that
#: shape stay inside the recent-observation budget while still covering
#: every example network's realistic ask in one call.
_SIGNAL_PROGRAMS_DEFAULT_CAP = 10

_UNSUPPORTED_PROGRAM_NOTE = (
    "Signal program data is not inspectable for this controller type -- its control "
    "logic is external (e.g. a .vap/.prbc supply file) or not fixed-time; green splits "
    "cannot be read or edited here by design."
)

# ---------------------------------------------------------------------------
# Reason-code -> explanation (Murdoch Drive finding, 2026-07-28)
#
# An empty list or an implausible number must arrive at the planner WITH the
# reason attached, never as a bare blank or a bare figure. signal_parser
# emits the machine-readable code; these render it into something the answer
# can quote. Korean first per the project's user-facing-string rule -- this
# output is read by the model that writes the Korean answer, so the exact
# phrasing has to travel with the datum rather than be reinvented per answer.
# ---------------------------------------------------------------------------

_PROGRAMS_UNAVAILABLE_NOTES: dict[str, tuple[str, str]] = {
    signal_parser.PROGRAMS_NO_BLOB: (
        "이 제어기는 상세 신호 프로그램 데이터를 제공하지 않습니다: 내부 신호 프로그램 "
        "데이터(intSupplyData)가 .inpx에 없습니다.",
        "This controller provides no detailed signal-program data: it carries no embedded "
        "intSupplyData block in the .inpx.",
    ),
    signal_parser.PROGRAMS_BLOB_UNREADABLE: (
        "이 제어기는 상세 신호 프로그램 데이터를 제공하지 않습니다: 내장된 신호 프로그램 "
        "블록을 해독할 수 없습니다 (파일 손상 가능).",
        "This controller provides no detailed signal-program data: its embedded program "
        "block could not be decoded (possible file corruption).",
    ),
    signal_parser.PROGRAMS_BLOB_EMPTY: (
        "이 제어기는 상세 신호 프로그램 데이터를 제공하지 않습니다: 신호 프로그램 블록은 "
        "있으나 정의된 프로그램이 하나도 없습니다.",
        "This controller provides no detailed signal-program data: its program block exists "
        "but defines no program at all.",
    ),
}

_SG_SPLITS_UNAVAILABLE_NOTES: dict[str, tuple[str, str]] = {
    signal_parser.SG_SPLITS_STAGE_BASED: (
        "이 프로그램은 현시(stage) 기반이라 신호그룹별 녹색시간 구간을 따로 저장하지 "
        "않습니다. 대신 stage_sequence(현시 전환 시각과 각 현시의 신호그룹)를 확인하세요. "
        "녹색시간 값은 추정하지 않습니다.",
        "This program is STAGE-based: it stores a stage/interstage sequence, not per-signal-"
        "group green windows, so there is no green split to report. Read stage_sequence "
        "(transition instants + which signal groups each stage holds green) instead. Green "
        "windows are deliberately NOT estimated, and set_signal_green_split cannot target "
        "this program.",
    ),
}

_DATA_QUALITY_NOTES: dict[str, tuple[str, str]] = {
    signal_parser.DQ_CYCLE_ABOVE_TYPICAL_MAX: (
        "주기가 일반적인 도시 신호 범위를 크게 넘습니다 - 모델에 저장된 값 그대로이며, "
        "실제 운영 계획이 아닐 수 있습니다(더미/미완성 프로그램 가능).",
        "cycle far above the normal urban range -- reported exactly as the model stores it, "
        "but it may not be a real operating plan (possible stub/placeholder program).",
    ),
    signal_parser.DQ_CYCLE_BELOW_TYPICAL_MIN: (
        "주기가 일반적인 도시 신호 범위보다 짧습니다 - 값 확인이 필요합니다.",
        "cycle below the normal urban range -- worth confirming against the source timing data.",
    ),
    signal_parser.DQ_NEAR_PERMANENT_GREEN: (
        "한 신호그룹이 주기의 90% 이상 녹색입니다 - 사실상 상시 녹색으로, 교차로 운영 "
        "계획으로 보기 어렵습니다.",
        "one signal group holds green for 90%+ of the cycle -- effectively permanent green, "
        "not a plausible intersection operating plan.",
    ),
    signal_parser.DQ_SINGLE_SIGNAL_GROUP: (
        "이 프로그램에는 신호그룹이 하나뿐입니다.",
        "this program contains only one signal group.",
    ),
}

_CONTROLLER_FLAG_NOTES: dict[str, tuple[str, str]] = {
    signal_parser.CONTROLLER_NO_SIGNAL_HEADS: (
        "이 제어기를 참조하는 신호등(signal head)이 네트워크에 하나도 없습니다 - 프로그램을 "
        "수정해도 차량 거동에 아무 영향이 없습니다.",
        "no signal head in the network references this controller -- editing its programs "
        "would have no effect on any vehicle.",
    ),
}


def _annotate_signal_controller(controller: dict[str, Any]) -> dict[str, Any]:
    """Attach human-readable notes for every machine-readable reason code on
    a controller and its programs, leaving the codes themselves in place."""
    out = dict(controller)
    notes_ko: list[str] = []
    notes_en: list[str] = []

    reason = str(out.get("programs_unavailable_reason") or "")
    if not out.get("programs"):
        if reason in _PROGRAMS_UNAVAILABLE_NOTES:
            ko, en = _PROGRAMS_UNAVAILABLE_NOTES[reason]
            notes_ko.append(ko)
            notes_en.append(en)
        elif out.get("controller_type") not in ("FIXEDTIME", "FIXEDTIMESIMPLE"):
            notes_en.append(_UNSUPPORTED_PROGRAM_NOTE)
            notes_ko.append(
                "이 제어기는 상세 데이터를 제공하지 않습니다: 제어 로직이 외부 파일"
                "(.vap/.prbc 등)에 있거나 고정주기 방식이 아니어서 녹색시간을 읽거나 "
                "편집할 수 없습니다."
            )

    for flag in out.get("controller_flags", []) or []:
        pair = _CONTROLLER_FLAG_NOTES.get(str(flag))
        if pair:
            notes_ko.append(pair[0])
            notes_en.append(pair[1])

    programs: list[dict[str, Any]] = []
    for program in out.get("programs", []) or []:
        p = dict(program)
        p_notes_ko: list[str] = []
        p_notes_en: list[str] = []
        split_reason = str(p.get("sg_splits_unavailable_reason") or "")
        if not p.get("sg_splits") and split_reason in _SG_SPLITS_UNAVAILABLE_NOTES:
            ko, en = _SG_SPLITS_UNAVAILABLE_NOTES[split_reason]
            p_notes_ko.append(ko)
            p_notes_en.append(en)
        for code in p.get("data_quality", []) or []:
            pair = _DATA_QUALITY_NOTES.get(str(code))
            if pair:
                p_notes_ko.append(pair[0])
                p_notes_en.append(pair[1])
        if p_notes_ko:
            p["note_ko"] = " ".join(p_notes_ko)
        if p_notes_en:
            p["note"] = " ".join(p_notes_en)
        programs.append(p)
    if programs:
        out["programs"] = programs

    if notes_ko:
        out["note_ko"] = " ".join(notes_ko)
    if notes_en:
        out["note"] = " ".join(notes_en)
    return out


class GetSignalProgramsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller_no: str | None = Field(
        default=None,
        description="Exact signal controller 'no' to return (narrows to at most one result).",
    )


@tool(name="get_signal_programs", tier="read", provider="inspect", params=GetSignalProgramsParams)
def get_signal_programs(params: GetSignalProgramsParams) -> dict[str, Any]:
    """Return each signal controller's fixed-time signal PROGRAMS: program no/name/cycle_s/offset_s, active_program_no (the program Vissim actually runs -- a controller can carry several), and program_kind. Two program families exist and they report DIFFERENT things. (1) program_kind="signal_group"/"signal_group_simple": each signal group's green split {sg_no, sg_name, green_start_s, green_end_s, amber_s, min_green_s, head_count} -- green_start_s is when the group turns green, green_end_s when it switches to red (a wrapping window with start > end is valid, green spans the cycle boundary); these are the programs set_signal_green_split can edit. (2) program_kind="stage_based": the program is a STAGE sequence, so sg_splits is EMPTY BY DESIGN with sg_splits_unavailable_reason="STAGE_BASED_PROGRAM" -- read stage_sequence (transition_at_s + the stage taking over + the signal groups it holds green) and the controller's signal_groups roster instead; green windows are not stored and are never estimated, and green-split edits are not possible on these. Every program also carries data_quality: machine-readable flags (CYCLE_ABOVE_TYPICAL_MAX, NEAR_PERMANENT_GREEN, SINGLE_SIGNAL_GROUP_PROGRAM, ...) marking figures that are read correctly from the model but are NOT plausible operating plans -- report such a number only together with its caveat, never as plain fact. A controller with no programs always carries programs_unavailable_reason. note/note_ko spell out every code. Use this BEFORE set_signal_green_split: the write needs the exact (controller, program, signal group) triple and edits normally target the ACTIVE program. Optionally filter by controller_no -- an unfiltered call on a large network returns only the first 10 controllers (with an explicit truncated flag and a hint)."""
    controllers = _current_shadow_model()["signal_programs"]

    if params.controller_no is not None:
        controllers = [c for c in controllers if str(c.get("sc_no")) == str(params.controller_no)]

    total = len(controllers)
    filtered = params.controller_no is not None
    if not filtered:
        capped = controllers[:_SIGNAL_PROGRAMS_DEFAULT_CAP]
        truncated = total > len(capped)
        controllers = capped
    else:
        truncated = False

    rendered = [_annotate_signal_controller(c) for c in controllers]

    result: dict[str, Any] = {"count": total, "signal_programs": rendered}
    if not filtered:
        result["truncated"] = truncated
        if truncated:
            result["hint"] = (
                f"Showing {len(rendered)} of {total} signal controllers. Narrow with "
                "controller_no to see a specific one instead of the whole list."
            )
    return result


# ---------------------------------------------------------------------------
# get_priority_rules -- QUEUE 3 unit 2
#
# BOUNDED OUTPUT from day one (the 2026-07-27 lesson): a single real network
# can carry hundreds of priority rules (Signalization RBC Boise.US: 419) --
# an unfiltered call caps the list at _PRIORITY_RULES_DEFAULT_CAP with an
# explicit truncated flag + hint, and priority_rule_no/link_no filters to
# narrow (link_no mirrors get_conflict_areas'/get_des_speed_decisions' own
# link_no filter, matching either the rule's own link or any of its conflict
# markers' link -- a user asking "priority rules on link 6" plausibly means
# either side).
# ---------------------------------------------------------------------------

#: Item cap for an UNFILTERED get_priority_rules call -- Boise.US alone has
#: 419 real rules; 40 stays comfortably inside even the compact snippet
#: budget for a typical entry shape (rule fields + a handful of conflict
#: markers each).
_PRIORITY_RULES_DEFAULT_CAP = 40


class GetPriorityRulesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority_rule_no: str | None = Field(
        default=None,
        description="Exact priority rule 'no' to return (narrows to at most one result).",
    )
    link_no: str | None = Field(
        default=None,
        description=(
            "Return only priority rules positioned on this link, OR whose conflict marker(s) "
            "reference this link."
        ),
    )


def _priority_rule_matches_link(rule: dict[str, Any], link_no: str) -> bool:
    if str(rule.get("link", "")) == link_no:
        return True
    return any(str(m.get("link", "")) == link_no for m in rule.get("conflict_markers", []))


@tool(name="get_priority_rules", tier="read", provider="inspect", params=GetPriorityRulesParams)
def get_priority_rules(params: GetPriorityRulesParams) -> dict[str, Any]:
    """Return the network's priority rules (yield/stop points on a minor link) with each conflict marker's gap/headway parameters. gap_time_s is the critical gap (SECONDS) a yielding vehicle needs before crossing/merging. headway_m is Vissim's minimum-clearance parameter -- despite being called "headway" via Vissim's own MinHdwy COM alias, it is a spatial clearance in METRES (not seconds) kept from the conflicting vehicle; 0 is a common, legitimate value on both fields (no gap/clearance required at that point), not missing data. Use marker_index (1-based, per rule) with set_priority_rule_gap_headway to edit a specific marker. link_ref says how the rule/marker references its link: "lane" (the usual lane-scoped placement) or "int_link" (a link-wide reference with no lane qualifier -- lane is legitimately empty then, not missing data). Optionally filter by priority_rule_no (exact match) or link_no (the rule's own link, or either side of any of its conflict markers) -- an unfiltered call on a large network returns only the first 40 (with an explicit truncated flag and a hint), so narrow with these filters rather than requesting the whole list."""
    priority_rules = _current_shadow_model()["priority_rules"]

    if params.priority_rule_no is not None:
        priority_rules = [
            pr for pr in priority_rules if str(pr.get("no")) == str(params.priority_rule_no)
        ]
    if params.link_no is not None:
        priority_rules = [
            pr for pr in priority_rules if _priority_rule_matches_link(pr, str(params.link_no))
        ]

    total = len(priority_rules)
    filtered = params.priority_rule_no is not None or params.link_no is not None
    if filtered:
        return {"count": total, "priority_rules": priority_rules}

    capped = priority_rules[:_PRIORITY_RULES_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {"count": total, "priority_rules": capped, "truncated": truncated}
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} priority rules. Narrow with "
            "priority_rule_no or link_no to see a specific one instead of the whole list."
        )
    return result


# ---------------------------------------------------------------------------
# get_vehicle_types -- QUEUE (v2a) unit
#
# BOUNDED OUTPUT from day one (the 2026-07-27 lesson), same shape as every
# other get_* tool in this module -- an unfiltered call caps the list with an
# explicit truncated flag + hint, and vehicle_type_no narrows to one. Real
# networks carry far fewer vehicle types than desSpeedDecisions/conflict
# areas (168 total across all 25 example networks, max 7 on one network), so
# this cap is generous headroom, not a tuned-to-the-edge limit.
# ---------------------------------------------------------------------------

#: Item cap for an UNFILTERED get_vehicle_types call -- every real example
#: network probed carries at most 7 vehicle types; 40 is generous headroom
#: matching this module's other object-list caps' stated reasoning.
_VEHICLE_TYPES_DEFAULT_CAP = 40


class GetVehicleTypesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_type_no: str | None = Field(
        default=None,
        description="Exact vehicle type 'no' to return (narrows to at most one result).",
    )


@tool(name="get_vehicle_types", tier="read", provider="inspect", params=GetVehicleTypesParams)
def get_vehicle_types(params: GetVehicleTypesParams) -> dict[str, Any]:
    """Return the network's vehicle types -- id, name, category (CAR/HGV/BUS/BIKE/PEDESTRIAN/TRAM, read-only), capacity, and each DISTRIBUTION/FUNCTION reference this type points at: model2d3d_distr (the 2D/3D model shapes used to draw/size this type -- resolves to a length_min_m/length_max_m/length_mean_m summary), weight_distr and power_distr (resolve to min/max/mean), and des_accel_func/max_accel_func/des_decel_func/max_decel_func/des_curve_speed_func (the accel/decel/curve-speed FUNCTION curves this type follows -- resolve to id+name only; editing a function's own curve is not supported by this agent, only reassigning which function/distribution a type uses). Some references are legitimately absent on certain categories (e.g. PEDESTRIAN types carry no weight_distr/power_distr/des_curve_speed_func) -- a null reference is normal data, not an error. Also reports referencing_composition_nos: which vehicle compositions include this type -- editing any of this type's references changes behavior for every one of them at once. Use set_vehicle_type_reference(vehicle_type_no, field, target_no) to reassign a reference; field is one of model2d3d_distr/weight_distr/power_distr/des_accel_func/max_accel_func/des_decel_func/max_decel_func/des_curve_speed_func. Optionally filter by vehicle_type_no -- an unfiltered call on a large network returns only the first 40 (with an explicit truncated flag and a hint)."""
    vehicle_types = _current_shadow_model()["vehicle_types"]

    if params.vehicle_type_no is not None:
        vehicle_types = [vt for vt in vehicle_types if str(vt.get("no")) == str(params.vehicle_type_no)]

    total = len(vehicle_types)
    filtered = params.vehicle_type_no is not None
    if filtered:
        return {"count": total, "vehicle_types": vehicle_types}

    capped = vehicle_types[:_VEHICLE_TYPES_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {"count": total, "vehicle_types": capped, "truncated": truncated}
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} vehicle types. Narrow with vehicle_type_no "
            "to see a specific one instead of the whole list."
        )
    return result


#: Item cap for an UNFILTERED get_time_distributions call -- the real max
#: observed on one network across all 25 Vissim 2026 examples is 20
#: (BRT Priority Texas.US); 60 mirrors _DES_SPEED_DISTRIBUTIONS_DEFAULT_CAP's
#: own generous-headroom reasoning for the sibling collection.
_TIME_DISTRIBUTIONS_DEFAULT_CAP = 60


class GetTimeDistributionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_distribution_no: str | None = Field(
        default=None,
        description="Exact time-distribution 'no' to return (narrows to at most one result).",
    )


@tool(
    name="get_time_distributions",
    tier="read",
    provider="inspect",
    params=GetTimeDistributionsParams,
)
def get_time_distributions(params: GetTimeDistributionsParams) -> dict[str, Any]:
    """Return the network's time distributions -- id, name, type (NORMAL, driven by mean_std_dev, or EMPIRICAL, driven by a cumulative curve of points instead -- an EMPIRICAL distribution's OWN mean_std_dev is a frozen informational summary that does NOT drive its actual sampled behavior and cannot be edited), mean_std_dev ({mean_s, std_dev_s}, seconds), points (duration_s, cum_fraction; capped at 30 with a points_truncated flag; empty for NORMAL distributions), and referenced_by (how many PT-stop dwell-time assignments, per-vehicle-class dwell overrides, driving-behavior CC1/zipper-overtake-time assignments, and other network elements -- parking direction-change duration, PT-line entry wait time, pedestrian-area wait time -- point at this distribution, each a {count, ids, truncated} bounded list, plus reference_count as the grand total). This is a SHARED collection: editing one distribution's mean_s/std_dev_s or points changes behavior for EVERY referencing element at once. Use set_time_distribution_normal(time_distribution_no, mean_s, std_dev_s) on a NORMAL distribution, or set_time_distribution_points(time_distribution_no, points) on an EMPIRICAL one -- calling the wrong verb for a distribution's own type is refused. Optionally filter by time_distribution_no -- an unfiltered call on a large network returns only the first 60 (with an explicit truncated flag and a hint)."""
    distributions = _current_shadow_model()["time_distributions"]

    if params.time_distribution_no is not None:
        distributions = [
            d for d in distributions if str(d.get("no")) == str(params.time_distribution_no)
        ]

    total = len(distributions)
    filtered = params.time_distribution_no is not None
    if filtered:
        return {"count": total, "time_distributions": distributions}

    capped = distributions[:_TIME_DISTRIBUTIONS_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {"count": total, "time_distributions": capped, "truncated": truncated}
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} time distributions. Narrow with "
            "time_distribution_no to see a specific one instead of the whole list."
        )
    return result


#: Item cap for an UNFILTERED get_driving_behaviors call -- the real max
#: observed is 15 (both the real Murdoch production model and the richest
#: Vissim 2026 example networks probed); 60 mirrors _TIME_DISTRIBUTIONS_
#: DEFAULT_CAP's own generous-headroom reasoning.
_DRIVING_BEHAVIORS_DEFAULT_CAP = 60


class GetDrivingBehaviorsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior_no: str | None = Field(
        default=None,
        description="Exact driving-behavior 'no' to return (narrows to at most one result).",
    )


@tool(
    name="get_driving_behaviors",
    tier="read",
    provider="inspect",
    params=GetDrivingBehaviorsParams,
)
def get_driving_behaviors(params: GetDrivingBehaviorsParams) -> dict[str, Any]:
    """Return the network's driving behaviors -- id, name, car_follow_mod_type (WIEDEMANN74/WIEDEMANN99/NOINTERACTION/...), car_following_model (74/99/None -- which model's parameters actually apply), the 8 WRITABLE scalar params this agent supports (ax/bx_add/bx_mult -- Wiedemann-74 car-following, meters/dimensionless; saf_dist_fact_ln_chg/coop_ln_chg_speed_diff/min_speed_for_lat/lat_dist_driv_def/lat_dist_stand_def -- lane-change/lateral aggressiveness, dimensionless/km/h/meters -- all already converted to genuine canonical units, never the network's own raw display unit), and referenced_by (which link-behavior-types -- by number, name, an optional veh_class_no for a per-class override, and that type's own real link_count -- currently use this behavior as their DEFAULT or a per-vehicle-class override; bounded list, reference_count is the real untruncated total). NOT writable via this agent: W99 CC0-CC9 (a SEPARATE, pre-existing agency-policy restriction -- WSDOT/Maryland DOT guidance says use default for this parameter tier) and CC1 (a Time Distribution REFERENCE, not a scalar -- see get_time_distributions). Driving behaviors are GLOBAL, SHARED objects: a behavior referenced by more than one consumer (reference_count > 1, or even exactly 1 -- see set_driving_behavior_parameter) CANNOT be edited in place -- duplicate_driving_behavior(source_no, new_name) first, edit the zero-reference copy with set_driving_behavior_parameter, then wire it to its intended link-behavior-type with set_link_behavior_type_driving_behavior. Optionally filter by behavior_no -- an unfiltered call on a large network returns only the first 60 (with an explicit truncated flag and a hint)."""
    behaviors = _current_shadow_model()["driving_behaviors"]

    if params.behavior_no is not None:
        behaviors = [b for b in behaviors if str(b.get("no")) == str(params.behavior_no)]

    total = len(behaviors)
    filtered = params.behavior_no is not None
    if filtered:
        return {"count": total, "driving_behaviors": behaviors}

    capped = behaviors[:_DRIVING_BEHAVIORS_DEFAULT_CAP]
    truncated = total > len(capped)
    result: dict[str, Any] = {"count": total, "driving_behaviors": capped, "truncated": truncated}
    if truncated:
        result["hint"] = (
            f"Showing {len(capped)} of {total} driving behaviors. Narrow with "
            "behavior_no to see a specific one instead of the whole list."
        )
    return result

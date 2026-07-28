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
:func:`_parse_speed_distributions`).

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
from services.inpx_parser import parse_network
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
    """{no, name, link, volume, interval_count, volumes} per <vehicleInput>.

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
    result: list[dict[str, Any]] = []
    for vi in root.findall(".//vehicleInput"):
        intervals: list[dict[str, Any]] = []
        for idx, vol_el in enumerate(vi.findall(".//timeIntervalVehVolume"), start=1):
            intervals.append(
                {
                    "interval_no": idx,
                    "from_s": _parse_time_interval_start_s(vol_el.get("timeInt")),
                    "volume": _coerce_float_or_none(vol_el.get("volume")),
                    "continuous": (vol_el.get("cont") or "").strip().lower() == "true",
                }
            )
        volume = intervals[0]["volume"] if intervals else None
        result.append(
            {
                "no": vi.get("no", ""),
                "name": vi.get("name", ""),
                "link": vi.get("link", ""),
                "volume": volume,
                "interval_count": len(intervals),
                "volumes": intervals,
            }
        )
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


def _parse_speed_distributions(root: ET.Element) -> list[dict[str, Any]]:
    """{no, name} per ``<speedDistributions><speedDistribution>``.

    v2a unit ⑧ (2026-07-27). This is the SAME distribution object type
    :func:`services.simulation_network_builder.inject_reduced_speed_area`
    already injects for ``set_reduced_speed_area`` (v2 unit ②) -- confirmed
    at the COM level too by this unit's own probe
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
    """
    return [
        {"no": d.get("no", ""), "name": d.get("name", "")}
        for d in root.findall(".//speedDistributions/speedDistribution")
    ]


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
    return {
        "links": net["links"],
        "nodes": net["nodes"],
        "vehicle_inputs": _parse_vehicle_inputs(root),
        "signals": [_signal_to_contract_shape(info) for info in signals_by_sc_no.values()],
        "conflict_areas": _parse_conflict_areas(root),
        "routing_decisions": routing_decisions,
        "routes": _flatten_routes(routing_decisions),
        "vehicle_compositions": vehicle_compositions,
        "vehicle_composition_entries": _flatten_vehicle_composition_entries(vehicle_compositions),
        # v2a unit ⑧
        "des_speed_decisions": des_speed_decisions,
        "des_speed_decision_entries": _flatten_des_speed_decision_entries(des_speed_decisions),
        "des_speed_distributions": _parse_speed_distributions(root),
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


def _iter_candidate_objects(shadow: dict[str, Any], object_type: str | None) -> list[tuple[str, str, str]]:
    """(id, name, object_type) tuples for every object of the requested
    type(s) — every type when ``object_type`` is None."""
    object_types = [object_type] if object_type else list(_OBJECT_TYPE_SPECS.keys())
    pool: list[tuple[str, str, str]] = []
    for otype in object_types:
        spec = _OBJECT_TYPE_SPECS[otype]
        for obj in shadow.get(spec["shadow_key"], []):
            pool.append((str(obj.get(spec["id_field"], "")), str(obj.get(spec["name_field"], "")), otype))
    return pool


def _difflib_candidates(query: str, pool: list[tuple[str, str, str]]) -> list[str]:
    """Fuzzy-match ``query`` against every object's id and name, always
    resolving a hit back to the owning object's id.

    Mirrors constraints.py's ``_check_object_not_found`` id-resolution
    behavior (that helper is private and tool-specific, so this is a
    deliberate mirror rather than an import — see the M1.2 brief).
    """
    owner_id_by_string: dict[str, str] = {}
    match_pool: list[str] = []
    for obj_id, name, _otype in pool:
        if obj_id and obj_id not in owner_id_by_string:
            match_pool.append(obj_id)
            owner_id_by_string[obj_id] = obj_id
        if name and name not in owner_id_by_string:
            match_pool.append(name)
            owner_id_by_string[name] = obj_id

    candidates: list[str] = []
    seen_ids: set[str] = set()
    for match in difflib.get_close_matches(query, match_pool, n=_TOP_K):
        owner_id = owner_id_by_string[match]
        if owner_id in seen_ids:
            continue
        seen_ids.add(owner_id)
        candidates.append(owner_id)
    return candidates


class ResolveObjectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    object_type: ObjectType | None = None


@tool(name="resolve_object", tier="read", provider="inspect", params=ResolveObjectParams)
def resolve_object(params: ResolveObjectParams) -> dict[str, Any]:
    """Lexically resolve a name/id query to ranked candidate network objects (top-5)."""
    shadow = _current_shadow_model()
    pool = _iter_candidate_objects(shadow, params.object_type)

    scored = [
        {"id": obj_id, "name": name, "object_type": otype, "score": score}
        for obj_id, name, otype in pool
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
# ---------------------------------------------------------------------------


class GetVehicleInputsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_vehicle_inputs", tier="read", provider="inspect", params=GetVehicleInputsParams)
def get_vehicle_inputs(params: GetVehicleInputsParams) -> dict[str, Any]:
    """Return the network's vehicle input objects (id, name, link, volume)."""
    del params  # no fields
    vehicle_inputs = _current_shadow_model()["vehicle_inputs"]
    return {"count": len(vehicle_inputs), "vehicle_inputs": vehicle_inputs}


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
    """Return the network's speed distributions (id, name) -- these are the valid targets for set_des_speed_decision's des_speed_distr_no and restore_distr_no params; a number not in this list will be refused before it ever reaches Vissim. Shared with set_reduced_speed_area (one distribution object type, two features). Optionally filter by distribution_no -- an unfiltered call on a large network returns only the first 60 (with an explicit truncated flag and a hint)."""
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


class GetSignalProgramsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller_no: str | None = Field(
        default=None,
        description="Exact signal controller 'no' to return (narrows to at most one result).",
    )


@tool(name="get_signal_programs", tier="read", provider="inspect", params=GetSignalProgramsParams)
def get_signal_programs(params: GetSignalProgramsParams) -> dict[str, Any]:
    """Return each signal controller's fixed-time signal PROGRAMS with per-signal-group green splits: program no/name/cycle_s, active_program_no (the program Vissim actually runs -- a controller can carry several), and each signal group's {sg_no, sg_name, green_start_s, green_end_s, amber_s, min_green_s, head_count}. green_start_s is when the group turns green and green_end_s is when it switches to red (a wrapping window with start > end is valid -- green spans the cycle boundary). Use this BEFORE set_signal_green_split: the write needs the exact (controller, program, signal group) triple, and edits normally target the ACTIVE program. Only FIXEDTIME/FIXEDTIMESIMPLE controllers carry inspectable programs; other types report an empty list with a note. Optionally filter by controller_no -- an unfiltered call on a large network returns only the first 10 controllers (with an explicit truncated flag and a hint)."""
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

    rendered = [
        (
            {**c, "note": _UNSUPPORTED_PROGRAM_NOTE}
            if not c.get("programs") and c.get("controller_type") not in ("FIXEDTIME", "FIXEDTIMESIMPLE")
            else c
        )
        for c in controllers
    ]

    result: dict[str, Any] = {"count": total, "signal_programs": rendered}
    if not filtered:
        result["truncated"] = truncated
        if truncated:
            result["hint"] = (
                f"Showing {len(rendered)} of {total} signal controllers. Narrow with "
                "controller_no to see a specific one instead of the whole list."
            )
    return result

"""Approval-card mini-map focus resolver (v2 UX unit, 2026-07-28).

Live-demo finding this closes: an approval card describes its target in TEXT
ONLY (e.g. "conflict area '3' is between link 3 and link 12") -- the user
could not tell WHICH intersection #3 was on the network and approved
essentially blind. This module resolves the pending write's params into a
small, bounded, machine-readable "what does this touch on the map" hint
(:func:`resolve_focus`'s return shape below); ``routers/network.py``'s
existing geometry endpoint (or the frontend's already-fetched copy of it)
supplies the actual coordinates for anything this module names, so the wire
payload here stays cheap (ids/labels + one bbox, never raw polylines).

**Reuses, does not duplicate, the write-target identity tables.**
``guardrails.py``'s ``_WRITE_TARGET_FIELD_SPEC``/``_PAIRED_EXTRA_TARGET_SPEC``/
``_target_id_for_spec`` already encode -- per write tool -- which of the
proposed action's own params identify its target object (a single
``id_param``, a two-part ``id_param_parts`` tuple for routes/vehicle-
composition-entries/des-speed-entries, or the three-part
``"{sc}.{program}.{sg}"`` triple for signal green splits; see that module's
own table for the full rationale). Importing those private names directly
(rather than re-deriving the same param-name knowledge here, which drifted
into a real bug class for the guardrails/constraints split itself per that
module's own docstrings) is a deliberate exception to the "mirror, don't
reach into a sibling module's private table" convention those docstrings
otherwise establish -- there the two tables serve genuinely different
consumers (constraints.py's fuzzy object_not_found matcher vs. guardrails.py's
exact diff-exemption bookkeeping) and mirroring kept them independently
correct; here this module and guardrails.py want the EXACT SAME "which
params name the target" answer, so importing is the one that can't drift.

**Never raises.** :func:`resolve_focus` wraps all resolution in a broad
``except Exception`` and degrades to the "no focus" shape (``bbox=None``)
rather than ever propagating -- the task's hard rule: a refused edit with a
clear reason beats a completed edit with wrong numbers, but ALSO a completed
(or even merely proposed) edit must never be blocked by a map-drawing bug.
The caller (``orchestrator.py``) attaches whatever this returns to the
pending approval unconditionally.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from services.agent.guardrails import (
    _PAIRED_EXTRA_TARGET_SPEC,
    _WRITE_TARGET_FIELD_SPEC,
    _target_id_for_spec,
)
from services.inpx_parser import parse_network_elements

logger = logging.getLogger(__name__)

# Degenerate-bbox floor, in metres -- a single-point target (e.g. one vehicle
# input, one signal head) has zero native span; without a floor the
# frontend's fit-to-bbox math would divide by (max - min) == 0, exactly what
# NetworkViewer.tsx's own `spanX = xMax - xMin || 1` guard exists to prevent
# for the same reason. 15m is a few lanes' width -- enough that the marker
# itself sits comfortably inside the fitted view even before the frontend's
# OWN "generous padding" (task step 5) is added on top.
_MIN_SPAN_M = 15.0

# Fan-out cap for one-to-many targets (a vehicle composition can be
# referenced by dozens of vehicle inputs on a large real network; a route's
# link_seq is usually short but not bounded). More than this many focus
# points buys the caption nothing -- this is a small orientation thumbnail,
# not a full inventory -- and risks a bbox so wide the "which element
# changed" framing this whole unit exists for is lost. Mirrors
# constraints.py's own `_capped_join` precedent: cap the ENUMERATED/DRAWN
# thing, never silently drop it from anywhere the full data is load-bearing
# (nothing here is -- the map is the only consumer of this list).
_MAX_FANOUT_ELEMENTS = 12


def _no_focus(note: str) -> dict[str, Any]:
    return {"elements": [], "bbox": None, "coordinate_note": note}


def _index_by(objects: list[dict[str, Any]] | None, field: str) -> dict[str, dict[str, Any]]:
    return {str(obj.get(field, "")): obj for obj in (objects or [])}


class _FocusAccumulator:
    """Collects focus elements (deduped by ``(type, id)``, insertion order)
    and the running bbox over every point/polyline fed in — the one place
    every per-tool resolver below funnels its findings through, so the
    dedup/bbox/degenerate-span logic exists exactly once."""

    def __init__(self) -> None:
        self.elements: list[dict[str, str]] = []
        self._seen: set[tuple[str, str]] = set()
        self._x_min: float | None = None
        self._x_max: float | None = None
        self._y_min: float | None = None
        self._y_max: float | None = None

    def add_element(self, elem_type: str, elem_id: Any, label: str | None) -> None:
        elem_id_str = str(elem_id or "")
        if not elem_id_str:
            return
        key = (elem_type, elem_id_str)
        if key in self._seen:
            return
        self._seen.add(key)
        self.elements.append({"type": elem_type, "id": elem_id_str, "label": label or elem_id_str})

    def add_point(self, x: Any, y: Any) -> None:
        if x is None or y is None:
            return
        try:
            x_f, y_f = float(x), float(y)
        except (TypeError, ValueError):
            return
        self._x_min = x_f if self._x_min is None else min(self._x_min, x_f)
        self._x_max = x_f if self._x_max is None else max(self._x_max, x_f)
        self._y_min = y_f if self._y_min is None else min(self._y_min, y_f)
        self._y_max = y_f if self._y_max is None else max(self._y_max, y_f)

    def add_points(self, points: Any) -> None:
        for point in points or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                self.add_point(point[0], point[1])

    def add_link(self, link: dict[str, Any] | None) -> None:
        """Record a link both as a drawn focus element AND its polyline's
        extent -- "the focus is the link polyline(s) plus a point if
        available" for every link-referencing target (task brief)."""
        if not link:
            return
        self.add_element("link", link.get("id", ""), link.get("name") or str(link.get("id", "")))
        self.add_points(link.get("points"))

    def _bbox(self) -> dict[str, float] | None:
        if self._x_min is None or self._y_min is None:
            return None
        x_min, x_max = self._x_min, self._x_max
        y_min, y_max = self._y_min, self._y_max
        if (x_max - x_min) < _MIN_SPAN_M:
            pad = (_MIN_SPAN_M - (x_max - x_min)) / 2.0
            x_min, x_max = x_min - pad, x_max + pad
        if (y_max - y_min) < _MIN_SPAN_M:
            pad = (_MIN_SPAN_M - (y_max - y_min)) / 2.0
            y_min, y_max = y_min - pad, y_max + pad
        return {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max}

    def result(self) -> dict[str, Any]:
        bbox = self._bbox()
        if bbox is None:
            return _no_focus("no_target_geometry")
        return {"elements": self.elements, "bbox": bbox, "coordinate_note": ""}


def _links_index(shadow_model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _index_by(shadow_model.get("links"), "id")


# ---------------------------------------------------------------------------
# Per-tool resolvers. Each takes (params, shadow_model, elements) and returns
# a populated _FocusAccumulator -- never None, never raises (the one
# `try/except` boundary is resolve_focus itself, below).
# ---------------------------------------------------------------------------


def _resolve_vehicle_input_volume(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    spec = _WRITE_TARGET_FIELD_SPEC["set_vehicle_input_volume"]
    input_no = str(params.get(spec["id_param"], ""))
    acc = _FocusAccumulator()
    item = next((e for e in elements.get("vehicle_inputs", []) if str(e.get("id")) == input_no), None)
    if item is None:
        return acc
    acc.add_element("vehicle_input", input_no, item.get("name") or input_no)
    acc.add_point(item.get("x"), item.get("y"))
    acc.add_link(_links_index(shadow_model).get(str(item.get("link", ""))))
    return acc


def _resolve_signal_heads(
    params: dict[str, Any],
    shadow_model: dict[str, Any],
    elements: dict[str, Any],
    *,
    sc_param: str = "sc_no",
    sg_no: str | None = None,
) -> _FocusAccumulator:
    """``set_signal_cycle``/``set_signal_offset`` (whole controller, every
    signal head under ``sc_no``) and ``set_signal_green_split`` (one signal
    group only, ``sg_no`` given) share this: every signal-head-bearing edit's
    focus is "the controller's signal heads" (task brief), scoped to one
    signal group when the edit is that specific."""
    sc_no = str(params.get(sc_param, ""))
    acc = _FocusAccumulator()
    if not sc_no:
        return acc
    links_idx = _links_index(shadow_model)
    for head in elements.get("signal_heads", []):
        if str(head.get("sc") or "") != sc_no:
            continue
        if sg_no is not None and str(head.get("sg") or "") != sg_no:
            continue
        acc.add_element("signal_head", head.get("id", ""), head.get("name") or head.get("id", ""))
        acc.add_point(head.get("x"), head.get("y"))
        acc.add_link(links_idx.get(str(head.get("link", ""))))
    return acc


def _resolve_signal_green_split(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    spec = _WRITE_TARGET_FIELD_SPEC["set_signal_green_split"]
    sc_param, _program_param, sg_param = spec["id_param_parts"]
    sg_no = str(params.get(sg_param, ""))
    return _resolve_signal_heads(params, shadow_model, elements, sc_param=sc_param, sg_no=sg_no)


def _resolve_conflict_area(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    del elements
    spec = _WRITE_TARGET_FIELD_SPEC["set_conflict_area_status"]
    ca_no = str(params.get(spec["id_param"], ""))
    acc = _FocusAccumulator()
    conflict_area = next(
        (ca for ca in shadow_model.get("conflict_areas", []) if str(ca.get("no")) == ca_no), None
    )
    if conflict_area is None:
        return acc
    acc.add_element("conflict_area", ca_no, conflict_area.get("name") or ca_no)
    links_idx = _links_index(shadow_model)
    acc.add_link(links_idx.get(str(conflict_area.get("link_a", ""))))
    acc.add_link(links_idx.get(str(conflict_area.get("link_b", ""))))
    return acc


def _resolve_reduced_speed_area(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    """Not in ``_WRITE_TARGET_FIELD_SPEC`` at all (XML-injection tool, no
    read-back-comparable existing field -- see that table's own docstring),
    but IS in ``constraints.py``'s ``_TOOL_TARGET_SPEC`` object_not_found
    table (``{object_class: "links", param: "link_id"}``). The target here
    literally IS a link id already -- no id-lookup indirection needed at
    all, so no import from that table buys anything a bare ``"link_id"``
    read doesn't already give byte-identically."""
    del elements
    link_id = str(params.get("link_id", ""))
    acc = _FocusAccumulator()
    acc.add_link(_links_index(shadow_model).get(link_id))
    return acc


def _resolve_route_relative_flow(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    del elements
    spec = _WRITE_TARGET_FIELD_SPEC["set_route_relative_flow"]
    route_id = _target_id_for_spec(spec, {"params": params})
    acc = _FocusAccumulator()
    route = next((r for r in shadow_model.get("routes", []) if str(r.get("id")) == route_id), None)
    if route is None:
        return acc
    links_idx = _links_index(shadow_model)
    link_seq = list(route.get("link_seq") or [])
    for link_id in link_seq[:_MAX_FANOUT_ELEMENTS]:
        acc.add_link(links_idx.get(str(link_id)))
    if not link_seq:
        # Disconnected/unresolvable route (constraints.py's own
        # `route_integrity_unverifiable`) -- link_seq is empty, but the
        # decision's own origin link and/or the route's declared
        # destination link often still resolve. Falling back to whichever
        # of those two DOES beats showing nothing for a route whose shape
        # is unknown but whose endpoints partially aren't.
        decision_param, _route_param = spec["id_param_parts"]
        decision_no = str(params.get(decision_param, ""))
        decision = next(
            (d for d in shadow_model.get("routing_decisions", []) if str(d.get("no")) == decision_no),
            None,
        )
        if decision is not None:
            acc.add_link(links_idx.get(str(decision.get("link", ""))))
        acc.add_link(links_idx.get(str(route.get("dest_link", ""))))
    return acc


def _resolve_vehicle_composition_share(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    spec = _WRITE_TARGET_FIELD_SPEC["set_vehicle_composition_share"]
    composition_param, _veh_type_param = spec["id_param_parts"]
    composition_no = str(params.get(composition_param, ""))
    acc = _FocusAccumulator()
    composition = next(
        (c for c in shadow_model.get("vehicle_compositions", []) if str(c.get("no")) == composition_no),
        None,
    )
    if composition is None:
        return acc
    links_idx = _links_index(shadow_model)
    vi_by_id = _index_by(elements.get("vehicle_inputs"), "id")
    # A composition has no geometry of its own -- it is a demand recipe, not
    # a placed object -- so its focus is every vehicle input that actually
    # USES it (task brief's general "derive coordinates via what references
    # the target" pattern, same shape as des-speed decisions' own link/pos).
    for vi_no in list(composition.get("referencing_vehicle_input_nos") or [])[:_MAX_FANOUT_ELEMENTS]:
        item = vi_by_id.get(str(vi_no))
        if item is None:
            continue
        acc.add_element("vehicle_input", vi_no, item.get("name") or str(vi_no))
        acc.add_point(item.get("x"), item.get("y"))
        acc.add_link(links_idx.get(str(item.get("link", ""))))
    return acc


def _resolve_des_speed_decision(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    spec = _WRITE_TARGET_FIELD_SPEC["set_des_speed_decision"]
    acc = _FocusAccumulator()
    links_idx = _links_index(shadow_model)
    dsd_by_id = _index_by(elements.get("des_speed_decisions"), "id")

    def _add_decision(decision_no: Any) -> None:
        item = dsd_by_id.get(str(decision_no or ""))
        if item is None:
            return
        acc.add_element("des_speed_decision", decision_no, item.get("name") or str(decision_no))
        acc.add_point(item.get("x"), item.get("y"))
        acc.add_link(links_idx.get(str(item.get("link", ""))))

    decision_param, _veh_class_param = spec["id_param_parts"]
    _add_decision(params.get(decision_param))

    # v2a unit ⑧'s paired restore target (see _PAIRED_EXTRA_TARGET_SPEC in
    # guardrails.py) -- a SECOND, independent object this same action can
    # touch. Shown alongside the primary target exactly when the action
    # itself carries it (the same `trigger_param` truthiness test
    # guardrails.py's own diff-exemption machinery uses).
    extra_spec = _PAIRED_EXTRA_TARGET_SPEC.get("set_des_speed_decision")
    if extra_spec is not None:
        restore_decision_no = params.get(extra_spec["trigger_param"])
        if restore_decision_no:
            _add_decision(restore_decision_no)
    return acc


_RESOLVERS: dict[str, Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], _FocusAccumulator]] = {
    "set_vehicle_input_volume": _resolve_vehicle_input_volume,
    "set_signal_cycle": _resolve_signal_heads,
    "set_signal_offset": _resolve_signal_heads,
    "set_conflict_area_status": _resolve_conflict_area,
    "set_reduced_speed_area": _resolve_reduced_speed_area,
    "set_route_relative_flow": _resolve_route_relative_flow,
    "set_vehicle_composition_share": _resolve_vehicle_composition_share,
    "set_des_speed_decision": _resolve_des_speed_decision,
    "set_signal_green_split": _resolve_signal_green_split,
    # set_sim_duration / set_random_seed / run_simulation: deliberately
    # ABSENT -- a global setting or a whole-network run has no single
    # network-element target to focus on (see _WRITE_TARGET_FIELD_SPEC's own
    # docstring: a tool absent there gets no diff exemption for the same
    # "no single object-list target" reason). Falls through to
    # "unsupported_tool" below, exactly the graceful-omission path the task
    # brief calls for -- never a special case here.
}


def _elements_for(shadow_model: dict[str, Any], inpx_path: str | Path) -> dict[str, Any]:
    """``parse_network_elements`` (signal heads / vehicle inputs / desired-
    speed decisions, resolved to x/y -- ``services/inpx_parser.py``) against
    ``inpx_path``, reusing ``shadow_model["links"]`` (already parsed once by
    ``tools_network.build_shadow_model`` for THIS SAME working copy) rather
    than re-parsing links a second time. Never raises -- callers get ``{}``
    on any parse failure, which every resolver above already treats as
    "nothing found" (empty ``.get(..., [])`` reads), not a crash."""
    try:
        root = ET.parse(inpx_path).getroot()
    except (OSError, ET.ParseError):
        logger.warning("approval_focus: could not parse %s for element geometry", inpx_path)
        return {}
    return parse_network_elements(root, shadow_model.get("links"))


def _resolve_focus_unguarded(
    tool: str,
    params: dict[str, Any],
    *,
    shadow_model: dict[str, Any],
    inpx_path: str | Path | None,
) -> dict[str, Any]:
    resolver = _RESOLVERS.get(tool)
    if resolver is None:
        return _no_focus("unsupported_tool")
    if inpx_path is None:
        return _no_focus("no_working_copy")
    elements = _elements_for(shadow_model, inpx_path)
    accumulator = resolver(params, shadow_model, elements)
    return accumulator.result()


def resolve_focus(
    tool: str,
    params: dict[str, Any],
    *,
    shadow_model: dict[str, Any] | None,
    inpx_path: str | Path | None,
) -> dict[str, Any]:
    """The pending write's map-focus hint: ``{"elements": [...], "bbox": ...
    | None, "coordinate_note": str}``.

    ``elements`` is a list of ``{"type", "id", "label"}`` -- ids the frontend
    re-resolves against the network geometry it already fetches for the
    ``/network`` page (this module never ships raw coordinates itself, to
    keep the approval payload small and avoid a second, drifting copy of the
    same geometry data). ``type`` is one of ``"link"``/``"conflict_area"``/
    ``"signal_head"``/``"vehicle_input"``/``"des_speed_decision"`` --
    ``"conflict_area"`` entries carry no geometry of their own (conflict
    areas have none) and exist only so the frontend's caption can name the
    conflict area itself alongside the two links it draws.

    ``bbox`` is ``None`` exactly when nothing resolved (unsupported tool, no
    working copy yet, or a target id genuinely absent from THIS network) --
    the frontend's documented contract is to render no map at all in that
    case, never an error. ``coordinate_note`` is a short, English,
    NOT-user-facing diagnostic tag (``"unsupported_tool"``,
    ``"no_working_copy"``, ``"no_target_geometry"``, ``"resolver_error"``,
    or ``""`` on success) -- logs/debugging only; the frontend's own caption
    text is built from ``elements``/the action's own params, not this field.

    **Never raises.** Any exception anywhere in resolution (a malformed
    param, an unexpected shadow-model shape, a corrupt .inpx) is caught here
    and logged; the caller gets the same "no focus" shape as a cleanly
    unsupported tool. An approval must always be usable even when the map
    can't be built.
    """
    try:
        return _resolve_focus_unguarded(
            tool, params, shadow_model=shadow_model or {}, inpx_path=inpx_path
        )
    except Exception:
        logger.exception(
            "approval_focus: resolution raised for tool=%r; degrading to no-focus", tool
        )
        return _no_focus("resolver_error")


__all__ = ["resolve_focus"]

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

# --- Anchor span policy (zoom defect, 2026-07-28) -------------------------
#
# ROOT CAUSE this closes: `add_link` fed every focus link's ENTIRE polyline
# extent into the running bbox, and for a conflict area between two long
# approach links the union of those extents is effectively the whole network.
# Live-measured on the bundled Beijing example, conflict area 3 (links 3+12):
# the link-extent union is 45.3 x 115.7 m, which the frontend's isotropic fit
# blows up to ~840 x 370 m of visible network (the network itself is only
# 1188 x 851 m) -- the actual conflict point rendered as a dot in the centre.
#
# The fix separates "what is HIGHLIGHTED" (unchanged: `elements`, links
# included, so the frontend still draws them for context) from "what drives
# the BBOX" (now the ANCHOR: the actual point of interest). See
# `_FocusAccumulator.add_anchor`.
#
# Context margin added around the anchor bbox on every side. A signalized
# urban junction's own footprint is ~45-60 m across (live-measured: the
# Beijing example's SC1 signal heads span 45.1 x 59.1 m), so 50 m of margin
# puts about one junction-width of approach road on each side of a
# single-point anchor -- a 100 m window, i.e. the junction plus its stop
# lines and a queue's worth of every approach. Enough surrounding network to
# recognise WHICH intersection this is (the whole reason the mini-map
# exists) without the target shrinking back into a speck; anything more
# context-hungry than that is what the "네트워크에서 보기" button is for.
#
# NOTE this makes an anchored bbox SQUARE for a single-point anchor, which
# matters as much as the size: both consumers fit isotropically, so a long
# thin bbox (the old link-extent union for conflict area 3 was 45 x 116 m,
# aspect 2.6) makes the fit pad the short axis out by that same factor. The
# old CA-3 map showed 841 x 370 m of an 1188 x 851 m network for that reason.
_ANCHOR_CONTEXT_M = 50.0

# Hard upper bound on an anchored focus's span, per axis. Roughly one
# intersection plus its approaches: wide enough to hold a large junction and
# its stop lines, narrow enough that the next intersection along a typical
# 100-200 m urban block does not compete for attention. Anchorless
# (extent-based) focuses -- routes, and the route fallback's origin/dest
# links -- are NOT capped: a route genuinely IS long, and cropping it would
# misrepresent the target rather than merely zoom it.
_MAX_SPAN_M = 200.0

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
    """Collects focus elements (deduped by ``(type, id)``, insertion order),
    the running EXTENT over every point/polyline fed in, and — separately —
    the running ANCHOR bbox over the actual point(s) of interest. The one
    place every per-tool resolver below funnels its findings through, so the
    dedup/anchor/span logic exists exactly once.

    Two independent accumulations, deliberately:

    * ``add_point``/``add_points``/``add_link`` feed the EXTENT. It is only
      the fallback bbox source, for targets that are genuinely extended and
      have no single meaningful point (routes).
    * ``add_anchor`` feeds the ANCHOR bbox — the conflict point, the vehicle
      input's own position, the signal head's own position. When ANY anchor
      exists it, not the extent, drives the bbox (see ``_bbox``).

    ``elements`` is unaffected either way: a focus link stays in the list and
    stays highlighted for context even when it contributes nothing to the
    bbox. Only the bbox math changed."""

    def __init__(self) -> None:
        self.elements: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str]] = set()
        self._x_min: float | None = None
        self._x_max: float | None = None
        self._y_min: float | None = None
        self._y_max: float | None = None
        self._ax_min: float | None = None
        self._ax_max: float | None = None
        self._ay_min: float | None = None
        self._ay_max: float | None = None

    def add_element(
        self, elem_type: str, elem_id: Any, label: str | None, extra: dict[str, Any] | None = None
    ) -> None:
        """Record one focus element. ``extra`` merges OPTIONAL, type-specific
        disambiguation fields onto it (sibling-disambiguation unit,
        2026-07-28) — empty/None values are dropped rather than shipped as
        nulls, so every element type that has nothing extra to say keeps the
        exact ``{type, id, label}`` shape it always had, and a network that
        lacks the datum never carries an empty placeholder."""
        elem_id_str = str(elem_id or "")
        if not elem_id_str:
            return
        key = (elem_type, elem_id_str)
        if key in self._seen:
            return
        self._seen.add(key)
        element: dict[str, Any] = {"type": elem_type, "id": elem_id_str, "label": label or elem_id_str}
        for field, value in (extra or {}).items():
            if value in (None, "", [], {}):
                continue
            element[field] = value
        self.elements.append(element)

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

    def add_anchor(self, x: Any, y: Any) -> None:
        """The actual point of interest — what the user is being asked to
        look AT, as opposed to the extended geometry drawn around it for
        context. Also feeds the extent (an anchor is a real coordinate, and
        keeping the two consistent means a resolver never has to call both)."""
        if x is None or y is None:
            return
        try:
            x_f, y_f = float(x), float(y)
        except (TypeError, ValueError):
            return
        self.add_point(x_f, y_f)
        self._ax_min = x_f if self._ax_min is None else min(self._ax_min, x_f)
        self._ax_max = x_f if self._ax_max is None else max(self._ax_max, x_f)
        self._ay_min = y_f if self._ay_min is None else min(self._ay_min, y_f)
        self._ay_max = y_f if self._ay_max is None else max(self._ay_max, y_f)

    def add_link(self, link: dict[str, Any] | None) -> None:
        """Record a link both as a drawn focus element AND its polyline's
        extent -- "the focus is the link polyline(s) plus a point if
        available" for every link-referencing target (task brief)."""
        if not link:
            return
        self.add_element("link", link.get("id", ""), link.get("name") or str(link.get("id", "")))
        self.add_points(link.get("points"))

    @staticmethod
    def _anchored_axis(lo: float, hi: float) -> tuple[float, float]:
        """One axis of an anchored bbox: the anchors' own span, plus
        ``_ANCHOR_CONTEXT_M`` of surrounding network on each side, clamped to
        ``_MAX_SPAN_M`` about the anchors' centre.

        The clamp deliberately eats the CONTEXT margin first and never the
        anchors themselves: ``max(_MAX_SPAN_M, native)`` means a focus whose
        anchors genuinely spread wider than the cap (a signal controller with
        far-flung heads, a vehicle composition referenced by inputs across the
        network) keeps every anchor inside the view rather than being
        centre-cropped with half its targets off-screen. Capping is a
        zoom policy, not a licence to hide a target."""
        native = hi - lo
        centre = (lo + hi) / 2.0
        span = min(native + 2.0 * _ANCHOR_CONTEXT_M, max(_MAX_SPAN_M, native))
        span = max(span, _MIN_SPAN_M)
        return centre - span / 2.0, centre + span / 2.0

    def _bbox(self) -> dict[str, float] | None:
        if self._ax_min is not None and self._ay_min is not None:
            # ANCHORED: the point(s) of interest drive the view, NOT the
            # extent of whatever long links happen to be highlighted around
            # them (the zoom defect this whole anchor concept closes).
            x_min, x_max = self._anchored_axis(self._ax_min, self._ax_max)
            y_min, y_max = self._anchored_axis(self._ay_min, self._ay_max)
            return {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max}
        if self._x_min is None or self._y_min is None:
            return None
        # ANCHORLESS fallback: a genuinely extended target (a route IS long)
        # keeps the original whole-extent bbox, uncapped. Only the degenerate
        # floor applies.
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
# Polyline geometry for anchor derivation. Plain float math, no numpy/shapely
# (neither is a backend dependency, and both would be absurd for two
# handfuls of segments): a Vissim link polyline is typically 2-10 points, so
# the O(n*m) segment sweeps below are a few dozen operations per resolve.
# ---------------------------------------------------------------------------

_GEOM_EPS = 1e-9


def _clean_points(points: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for point in points or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            out.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    return out


def _segment_intersection(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> tuple[float, float] | None:
    """Proper (within-both-segments) intersection of two 2D segments, or
    ``None``. Parallel/collinear segments return ``None`` -- collinear
    overlap is not a *crossing* point, and the closest-approach fallback
    handles it correctly anyway (distance 0 anywhere along the overlap)."""
    (x1, y1), (x2, y2) = a1, a2
    (x3, y3), (x4, y4) = b1, b2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < _GEOM_EPS:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    if not (0.0 <= t <= 1.0 and 0.0 <= u <= 1.0):
        return None
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _closest_point_on_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= _GEOM_EPS:
        return a
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq
    t = min(1.0, max(0.0, t))
    return (a[0] + t * dx, a[1] + t * dy)


def _crossing_anchor(
    points_a: Any, points_b: Any
) -> tuple[float, float] | None:
    """The single point where two link polylines actually meet -- a conflict
    area's real location, as opposed to the union of two whole approach
    links' extents (which is what used to drive the bbox, and is why the map
    read as "the entire network with a dot in the middle").

    Strict segment-segment intersection when the polylines cross (152 of the
    Beijing example's 224 conflict areas do); otherwise the midpoint of the
    closest approach between them -- CONNECTORS OFTEN DO NOT STRICTLY
    INTERSECT the link they conflict with (the other 72 Beijing conflict
    areas; live-measured closest-approach distances there: min 0.0 m, median
    1.1 m, max 6.2 m -- i.e. a fraction of a lane width, so the midpoint is
    the conflict point to well within the map's own resolution).

    Returns ``None`` only when either polyline has fewer than 2 usable
    points; the caller then falls back to the extent-based bbox."""
    pa = _clean_points(points_a)
    pb = _clean_points(points_b)
    if len(pa) < 2 or len(pb) < 2:
        return None

    hits: list[tuple[float, float]] = []
    for i in range(len(pa) - 1):
        for j in range(len(pb) - 1):
            hit = _segment_intersection(pa[i], pa[i + 1], pb[j], pb[j + 1])
            if hit is not None:
                hits.append(hit)
    if hits:
        # Multiple crossings (a connector that weaves across a link) -> their
        # centroid, which is still inside the junction the pair conflicts in.
        return (sum(h[0] for h in hits) / len(hits), sum(h[1] for h in hits) / len(hits))

    best: tuple[float, tuple[float, float], tuple[float, float]] | None = None

    def _consider(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float], flip: bool) -> None:
        nonlocal best
        q = _closest_point_on_segment(p, a, b)
        dist = ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5
        if best is None or dist < best[0]:
            best = (dist, q, p) if flip else (dist, p, q)

    for i in range(len(pa) - 1):
        for j in range(len(pb) - 1):
            _consider(pa[i], pb[j], pb[j + 1], False)
            _consider(pa[i + 1], pb[j], pb[j + 1], False)
            _consider(pb[j], pa[i], pa[i + 1], True)
            _consider(pb[j + 1], pa[i], pa[i + 1], True)
    if best is None:
        return None
    _dist, p, q = best
    return ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)


def _point_along_polyline(points: Any, distance_m: Any) -> tuple[float, float] | None:
    """The coordinate ``distance_m`` along a link polyline from its start,
    clamped to the polyline's own ends. Lets a position-addressed target
    (``set_reduced_speed_area``'s ``from_m``/``length_m``) anchor on its own
    location instead of on the host link's whole extent."""
    pts = _clean_points(points)
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0]
    try:
        target = float(distance_m)
    except (TypeError, ValueError):
        return pts[0]
    if target <= 0:
        return pts[0]
    travelled = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        if seg <= _GEOM_EPS:
            continue
        if travelled + seg >= target:
            t = (target - travelled) / seg
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        travelled += seg
    return pts[-1]


# ---------------------------------------------------------------------------
# Per-tool resolvers. Each takes (params, shadow_model, elements) and returns
# a populated _FocusAccumulator -- never None, never raises (the one
# `try/except` boundary is resolve_focus itself, below).
# ---------------------------------------------------------------------------


#: Coordinate rounding (metres) used to decide "these two vehicle inputs are
#: drawn on the SAME pixel". inpx_parser places every vehicle input at its
#: link's start point (pos 0), so real siblings are bit-identical, not merely
#: close -- the tolerance exists only so a float round-trip can never split a
#: genuine stack, never to merge two inputs a user could actually distinguish
#: on screen.
_SAME_POINT_DECIMALS = 3

#: Cap on the ``siblings`` list shipped per focus element. This module ships
#: ids, not geometry, deliberately (see resolve_focus's docstring) -- three is
#: the real-world number on the client model (one input per vehicle class) and
#: eight is the same display cap constraints._capped_join uses, so the payload
#: cannot grow with network size.
_MAX_FOCUS_SIBLINGS = 8


def _same_point_vehicle_input_siblings(
    elements: dict[str, Any], item: dict[str, Any], input_no: str
) -> list[str]:
    """Ids of the OTHER vehicle inputs drawn at the same coordinate as
    ``item``, in element order, capped at ``_MAX_FOCUS_SIBLINGS``.

    Why this exists (sibling-disambiguation unit, 2026-07-28): the real
    client model puts three vehicle inputs — one per vehicle class — on the
    same link at the same exact (x, y), so the approval card's mini-map draws
    one dot for what are three separate editable objects and the network
    map's tooltip names whichever one happens to be painted last. The focus
    payload is the only channel that knows WHICH one is being written, so it
    is the right place to also say which ones it is stacked on top of."""
    x = item.get("x")
    y = item.get("y")
    if x is None or y is None:
        return []
    try:
        key = (round(float(x), _SAME_POINT_DECIMALS), round(float(y), _SAME_POINT_DECIMALS))
    except (TypeError, ValueError):
        return []
    siblings: list[str] = []
    for other in elements.get("vehicle_inputs", []):
        other_id = str(other.get("id", ""))
        if not other_id or other_id == input_no:
            continue
        try:
            other_key = (
                round(float(other.get("x")), _SAME_POINT_DECIMALS),
                round(float(other.get("y")), _SAME_POINT_DECIMALS),
            )
        except (TypeError, ValueError):
            continue
        if other_key != key:
            continue
        siblings.append(other_id)
        if len(siblings) >= _MAX_FOCUS_SIBLINGS:
            break
    return siblings


def _resolve_vehicle_input_volume(
    params: dict[str, Any], shadow_model: dict[str, Any], elements: dict[str, Any]
) -> _FocusAccumulator:
    spec = _WRITE_TARGET_FIELD_SPEC["set_vehicle_input_volume"]
    input_no = str(params.get(spec["id_param"], ""))
    acc = _FocusAccumulator()
    item = next((e for e in elements.get("vehicle_inputs", []) if str(e.get("id")) == input_no), None)
    if item is None:
        return acc
    # `label` (the input's NAME) is shared verbatim by every sibling on the
    # real client model -- all three of link 620's inputs are "Jennalup ST_WB"
    # -- so the name ALONE cannot disambiguate. `composition` is the datum
    # that can, and `siblings` names the other objects hiding under the same
    # pixel so the card can say so instead of leaving the user to guess.
    # Both are omitted when unknown (add_element drops empty values), so a
    # network without compositions ships the unchanged {type, id, label}.
    acc.add_element(
        "vehicle_input",
        input_no,
        item.get("name") or input_no,
        extra={
            "composition": item.get("composition_name") or "",
            "composition_no": item.get("composition_no") or "",
            "composition_varies_by_interval": bool(item.get("composition_varies_by_interval")) or None,
            "siblings": _same_point_vehicle_input_siblings(elements, item, input_no),
        },
    )
    # The vehicle input's OWN point anchors the view -- its host link, added
    # below for context, is often the full length of an approach.
    acc.add_anchor(item.get("x"), item.get("y"))
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
        # Each head's own stop-line position anchors the view; the heads of
        # one controller cluster on its junction (live-measured on the
        # Beijing example: SC1's 52 heads span 45.1 x 59.1 m), so the
        # anchored bbox IS that junction rather than every approach's length.
        acc.add_anchor(head.get("x"), head.get("y"))
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
    link_a = links_idx.get(str(conflict_area.get("link_a", "")))
    link_b = links_idx.get(str(conflict_area.get("link_b", "")))
    acc.add_link(link_a)
    acc.add_link(link_b)
    # THE case this whole anchor concept exists for. A conflict area has no
    # geometry of its own in the .inpx -- it is just a link pair -- so its
    # location has to be DERIVED: where the two polylines actually cross (or
    # come closest). Without this the bbox was the union of two whole
    # approach links, i.e. effectively the network. Both links stay in
    # `elements` and stay highlighted; only the bbox changed.
    if link_a is not None and link_b is not None:
        anchor = _crossing_anchor(link_a.get("points"), link_b.get("points"))
        if anchor is not None:
            acc.add_anchor(anchor[0], anchor[1])
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
    link = _links_index(shadow_model).get(link_id)
    acc.add_link(link)
    # The area being injected is POSITION-ADDRESSED ([from_m, from_m +
    # length_m) along the link), so it has a real location of its own even
    # though it does not exist in the .inpx yet -- anchor on its two ends
    # rather than on the host link's whole length.
    if link is not None:
        try:
            from_m = float(params.get("from_m", 0) or 0)
            length_m = float(params.get("length_m", 0) or 0)
        except (TypeError, ValueError):
            from_m, length_m = 0.0, 0.0
        for distance in (from_m, from_m + max(length_m, 0.0)):
            point = _point_along_polyline(link.get("points"), distance)
            if point is not None:
                acc.add_anchor(point[0], point[1])
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
    # DELIBERATELY ANCHORLESS. A route is a genuinely extended target -- its
    # whole point is the path from origin to destination -- so no single
    # point of interest exists, and cropping it to one junction would
    # misrepresent what is being edited rather than merely zoom it. This is
    # the (only) resolver whose bbox can exceed `_MAX_SPAN_M`; live-measured
    # on the Beijing example, its routes span 190-240 m.
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
        acc.add_anchor(item.get("x"), item.get("y"))
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
        acc.add_anchor(item.get("x"), item.get("y"))
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

    **Optional per-type disambiguation fields** (sibling-disambiguation unit,
    2026-07-28). An element may carry extra keys BEYOND ``type``/``id``/
    ``label``; every one of them is omitted, never nulled, when its datum is
    absent, so the base shape above is still exactly what a consumer gets for
    any element type or any network that has nothing extra to say. Currently
    only ``"vehicle_input"`` adds any, and it adds these:

    * ``composition`` -- the vehicle composition's NAME (e.g. ``"Class1"``).
      The reason this field exists: on the real client model ``label`` (the
      input's own name) is shared VERBATIM by every input on the link -- all
      three of link 620's inputs are named ``"Jennalup ST_WB"`` -- so the
      label alone cannot tell 107 from 207 from 307. The composition can.
    * ``composition_no`` -- the raw ``vehComp`` number behind that name, for
      a network that numbers compositions but does not name them.
    * ``composition_varies_by_interval`` -- ``True`` (and only ever ``True``;
      omitted otherwise) when this input's intervals reference DIFFERENT
      compositions, in which case ``composition``/``composition_no`` are
      absent because there is no single honest answer.
    * ``siblings`` -- ids of the other vehicle inputs drawn at the SAME
      coordinate, e.g. ``["207", "307"]``, capped at ``_MAX_FOCUS_SIBLINGS``.
      Ids only, in keeping with this module's ids-not-geometry rule.

    So the real payload for the worked case is::

        {"type": "vehicle_input", "id": "107", "label": "Jennalup ST_WB",
         "composition": "Class1", "composition_no": "1",
         "siblings": ["207", "307"]}

    ``bbox`` is the region worth LOOKING AT, in raw network metres -- not
    the extent of everything in ``elements``. For a target with a real point
    of interest (a conflict area's crossing point, a vehicle input, a signal
    head, a des-speed decision, a reduced-speed area's own span) it is that
    ANCHOR plus ``_ANCHOR_CONTEXT_M`` of surrounding network, capped at
    ``_MAX_SPAN_M`` per axis; the highlighted links around it can and
    routinely do extend well outside it. Only a genuinely extended,
    anchorless target (a route's ``link_seq``) still gets the whole-extent
    bbox, uncapped. See ``_FocusAccumulator`` for the full policy.

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


# ---------------------------------------------------------------------------
# Additive shared surface (network component search, 2026-07-28)
# ---------------------------------------------------------------------------
#
# ``services/network_search.py`` answers a DIFFERENT question ("where is the
# thing the user typed?") but must draw the SAME map as an approval card does,
# with the same anchor/context/cap policy -- two coordinate resolvers that
# disagree about where conflict area 2140 is would be worse than none. Rather
# than reach into this module's private names (or, worse, re-derive the anchor
# math), the pieces search needs are re-exported here under public names.
#
# Purely additive aliases: no existing resolver, constant or behaviour changes,
# and ``resolve_focus`` is untouched. Anything NOT listed here stays private on
# purpose -- the per-write-tool resolvers are keyed to ``guardrails.py``'s
# write-target tables and mean nothing to a search query.
FocusAccumulator = _FocusAccumulator
crossing_anchor = _crossing_anchor
network_element_geometry = _elements_for
MAX_FOCUS_FANOUT = _MAX_FANOUT_ELEMENTS

__all__ = [
    "resolve_focus",
    "FocusAccumulator",
    "crossing_anchor",
    "network_element_geometry",
    "MAX_FOCUS_FANOUT",
]

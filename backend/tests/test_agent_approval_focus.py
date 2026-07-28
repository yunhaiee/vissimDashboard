"""Tests for ``services.agent.approval_focus`` — the approval-card mini-map
focus resolver (v2 UX unit, 2026-07-28): given a pending write tool + its
proposed params, resolve a bounded, machine-readable "what does this touch
on the map" hint (``{"elements", "bbox", "coordinate_note"}``).

Three fixtures, matching the task brief's "demo data + a real example .inpx
fixture" instruction and this repo's own established precedent
(test_agent_health_check.py's docstring):

  1. The bundled synthetic demo network (``data/practice.inpx``) — zero
     conflict areas/signal programs/vehicle compositions/des-speed decisions
     (verified empirically), but real links + a real vehicle input, enough
     to exercise ``set_vehicle_input_volume``/``set_reduced_speed_area`` and
     the shared bbox/dedup machinery with no COM.
  2. The real Urban Intersection Beijing.CN_31360 example network — 224 real
     conflict areas, 28 real routes, 52 real signal heads, real vehicle
     compositions — for every write verb the demo network can't exercise.
     Skipped when the example isn't present on this machine (same
     ``pytest.mark.skipif`` precedent as test_agent_health_check.py /
     test_signal_parser.py's own Beijing fixtures).
  3. A hand-built minimal .inpx (tmp_path) for ``set_des_speed_decision``'s
     paired primary+restore target — neither the demo nor the Beijing
     example network has any desired-speed decisions at all (verified
     empirically), so this is the only way to exercise it end to end.

Plus: the graceful no-coordinates path, bbox sanity (contains the target,
non-degenerate), and the "a resolver exception cannot break approval"
guarantee — all fixture-independent.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from services.agent import approval_focus
from services.agent.approval_focus import resolve_focus
from services.agent.tools_network import build_shadow_model
from services.inpx_parser import parse_network

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"
_BEIJING_INPX = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "com-smoke"
    / "Urban Intersection Beijing.CN_31360"
    / "Intersection Beijing.inpx"
)
_requires_beijing = pytest.mark.skipif(
    not _BEIJING_INPX.is_file(), reason=f"real example network not present: {_BEIJING_INPX}"
)


def _demo_shadow_model() -> dict[str, Any]:
    return build_shadow_model(DEMO_INPX)


def _beijing_shadow_model() -> dict[str, Any]:
    return build_shadow_model(_BEIJING_INPX)


def _contains(bbox: dict[str, float], x: float, y: float) -> bool:
    return bbox["x_min"] <= x <= bbox["x_max"] and bbox["y_min"] <= y <= bbox["y_max"]


def _assert_non_degenerate(bbox: dict[str, float]) -> None:
    assert bbox["x_max"] > bbox["x_min"]
    assert bbox["y_max"] > bbox["y_min"]


def _element_keys(focus: dict[str, Any]) -> set[tuple[str, str]]:
    return {(e["type"], e["id"]) for e in focus["elements"]}


def _spans(bbox: dict[str, float]) -> tuple[float, float]:
    return bbox["x_max"] - bbox["x_min"], bbox["y_max"] - bbox["y_min"]


def _centre(bbox: dict[str, float]) -> tuple[float, float]:
    return (bbox["x_min"] + bbox["x_max"]) / 2.0, (bbox["y_min"] + bbox["y_max"]) / 2.0


def _fitted_span(bbox: dict[str, float]) -> float:
    return max(_spans(bbox))


# Nominal viewport width/height ratio, only for _fitted_view_area below.
_NOMINAL_VIEWPORT_ASPECT = 1.6


def _fitted_view_area(bbox: dict[str, float]) -> float:
    """How much network is ACTUALLY VISIBLE, in m², when this bbox is fitted
    into a viewport of ``_NOMINAL_VIEWPORT_ASPECT``.

    Both map consumers (ApprovalFocusMap's thumbnail, NetworkViewer's
    fit-to-bbox) scale ISOTROPICALLY, so the more demanding axis sets the
    zoom and the other one is padded out by however much it falls short.
    That is why a long thin bbox is so much worse than its raw spans look:
    conflict area 3's old 45 x 116 m link-extent union has aspect 2.6, so
    fitting it pads the x axis out by that factor. Raw x/y spans hide this
    entirely; this is the number the user experiences."""
    span_x, span_y = _spans(bbox)
    scale = min(_NOMINAL_VIEWPORT_ASPECT / span_x, 1.0 / span_y)
    return (_NOMINAL_VIEWPORT_ASPECT / scale) * (1.0 / scale)


def _extent_bbox(points: list[Any]) -> dict[str, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {"x_min": min(xs), "y_min": min(ys), "x_max": max(xs), "y_max": max(ys)}


def _distance_to_polyline(point: tuple[float, float], polyline: list[Any]) -> float:
    best = float("inf")
    for i in range(len(polyline) - 1):
        a = (float(polyline[i][0]), float(polyline[i][1]))
        b = (float(polyline[i + 1][0]), float(polyline[i + 1][1]))
        q = approval_focus._closest_point_on_segment(point, a, b)
        best = min(best, ((point[0] - q[0]) ** 2 + (point[1] - q[1]) ** 2) ** 0.5)
    return best


# ---------------------------------------------------------------------------
# (a) demo network — set_vehicle_input_volume, set_reduced_speed_area
# ---------------------------------------------------------------------------


def test_vehicle_input_volume_focus_against_demo_network():
    shadow_model = _demo_shadow_model()
    net = parse_network(str(DEMO_INPX))
    vi = next(e for e in net["elements"]["vehicle_inputs"] if e["id"] == "1")
    link = next(lk for lk in net["links"] if lk["id"] == vi["link"])

    focus = resolve_focus(
        "set_vehicle_input_volume",
        {"input_no": "1", "volume": 600},
        shadow_model=shadow_model,
        inpx_path=DEMO_INPX,
    )

    assert focus["bbox"] is not None
    assert focus["coordinate_note"] == ""
    assert ("vehicle_input", "1") in _element_keys(focus)
    assert ("link", vi["link"]) in _element_keys(focus)

    _assert_non_degenerate(focus["bbox"])
    assert _contains(focus["bbox"], vi["x"], vi["y"])
    # ANCHORED (zoom defect, 2026-07-28): the vehicle input's own point, not
    # the host link's whole extent, drives the bbox -- so it is centred on
    # the input and spans exactly the context margin on each side. The link
    # stays in `elements` (asserted above) and stays drawn for context; it
    # simply no longer sets the zoom.
    cx, cy = _centre(focus["bbox"])
    assert cx == pytest.approx(vi["x"], abs=1e-6)
    assert cy == pytest.approx(vi["y"], abs=1e-6)
    assert _spans(focus["bbox"]) == pytest.approx(
        (2 * approval_focus._ANCHOR_CONTEXT_M, 2 * approval_focus._ANCHOR_CONTEXT_M)
    )
    assert link["points"]  # sanity: the context link really does have geometry


def test_vehicle_input_volume_focus_unknown_id_degrades_gracefully():
    focus = resolve_focus(
        "set_vehicle_input_volume",
        {"input_no": "999-bogus", "volume": 600},
        shadow_model=_demo_shadow_model(),
        inpx_path=DEMO_INPX,
    )
    assert focus == {"elements": [], "bbox": None, "coordinate_note": "no_target_geometry"}


def test_reduced_speed_area_focus_highlights_only_the_target_link():
    shadow_model = _demo_shadow_model()
    link = next(lk for lk in shadow_model["links"] if lk["id"] == "101")

    focus = resolve_focus(
        "set_reduced_speed_area",
        {"link_id": "101", "speed_kmh": 30, "from_m": 0, "length_m": 50},
        shadow_model=shadow_model,
        inpx_path=DEMO_INPX,
    )

    assert focus["bbox"] is not None
    assert _element_keys(focus) == {("link", "101")}
    # ANCHORED on the injected area's OWN span along the link -- [from_m,
    # from_m + length_m], not the link's whole length.
    start = approval_focus._point_along_polyline(link["points"], 0)
    end = approval_focus._point_along_polyline(link["points"], 50)
    assert _contains(focus["bbox"], *start)
    assert _contains(focus["bbox"], *end)


def test_reduced_speed_area_focus_follows_from_m_down_the_link():
    """Two areas on the SAME link at different positions must produce
    DIFFERENT bboxes -- proof the anchor is the area's own position and not
    (as before) the host link's extent, which is identical for both."""
    shadow_model = _demo_shadow_model()
    near = resolve_focus(
        "set_reduced_speed_area",
        {"link_id": "101", "speed_kmh": 30, "from_m": 0, "length_m": 10},
        shadow_model=shadow_model,
        inpx_path=DEMO_INPX,
    )
    far = resolve_focus(
        "set_reduced_speed_area",
        {"link_id": "101", "speed_kmh": 30, "from_m": 120, "length_m": 10},
        shadow_model=shadow_model,
        inpx_path=DEMO_INPX,
    )
    assert near["bbox"] is not None and far["bbox"] is not None
    assert _centre(near["bbox"]) != _centre(far["bbox"])


# ---------------------------------------------------------------------------
# (b) Beijing fixture — every verb the demo network can't exercise
# ---------------------------------------------------------------------------


@_requires_beijing
def test_conflict_area_focus_against_beijing_highlights_both_links():
    shadow_model = _beijing_shadow_model()
    ca = next(c for c in shadow_model["conflict_areas"] if c["no"] == "3")
    assert (ca["link_a"], ca["link_b"]) == ("3", "12")  # matches the task brief's own example

    focus = resolve_focus(
        "set_conflict_area_status",
        {"conflict_area_no": "3", "status": "AHASRIGHTOFWAY"},
        shadow_model=shadow_model,
        inpx_path=_BEIJING_INPX,
    )

    assert focus["bbox"] is not None
    keys = _element_keys(focus)
    assert ("conflict_area", "3") in keys
    assert ("link", "3") in keys
    assert ("link", "12") in keys
    _assert_non_degenerate(focus["bbox"])


@_requires_beijing
def test_conflict_area_bbox_is_anchored_on_the_crossing_not_the_link_extents():
    """The zoom defect (2026-07-28), against the REAL Beijing fixture.

    Conflict area 3 is between link 3 (a 45 m east-west stub) and link 12 (a
    115 m north-south approach). Feeding both links' whole extents into the
    bbox -- what ``add_link`` used to do -- produced a 45 x 116 m box whose
    ISOTROPIC fit blows out to most of the 1188 x 851 m network, rendering
    the actual conflict point as a dot in the centre. The bbox must now be
    anchored on where the two polylines actually meet."""
    shadow_model = _beijing_shadow_model()
    links_by_id = {lk["id"]: lk for lk in shadow_model["links"]}
    focus = resolve_focus(
        "set_conflict_area_status",
        {"conflict_area_no": "3", "status": "AHASRIGHTOFWAY"},
        shadow_model=shadow_model,
        inpx_path=_BEIJING_INPX,
    )
    bbox = focus["bbox"]
    assert bbox is not None

    old = _extent_bbox(links_by_id["3"]["points"] + links_by_id["12"]["points"])

    # (a) The anchor lands ON the conflict: within a lane width of BOTH links.
    anchor = _centre(bbox)
    for link_id in ("3", "12"):
        assert _distance_to_polyline(anchor, links_by_id[link_id]["points"]) < 3.5

    # (b) The conflict point is now CENTRED. Under the old extent-union bbox
    # it sat 57 m off centre -- 0.8 m from the box's own bottom edge, i.e.
    # pinned to the very rim of the map it was supposed to be the subject of.
    assert _centre(bbox) == pytest.approx(anchor)
    old_centre = _centre(old)
    assert ((anchor[0] - old_centre[0]) ** 2 + (anchor[1] - old_centre[1]) ** 2) ** 0.5 > 40.0
    assert anchor[1] - old["y_min"] < 2.0  # it really was on the old bbox's rim

    # (c) ...and the view is dramatically smaller once the isotropic fit both
    # consumers apply is accounted for (the old box's 2.6:1 aspect made the
    # fit pad it out by that factor again).
    assert _fitted_view_area(bbox) < _fitted_view_area(old) / 1.3
    assert _fitted_span(bbox) <= approval_focus._MAX_SPAN_M

    # (d) Both links are STILL highlighted (only the bbox math changed) even
    # though they extend well outside the view.
    assert ("link", "3") in _element_keys(focus)
    assert ("link", "12") in _element_keys(focus)
    assert not all(_contains(bbox, px, py) for px, py in links_by_id["12"]["points"])


@_requires_beijing
def test_every_beijing_conflict_area_focus_is_bounded_by_the_span_cap():
    """Sweep of all 224 real conflict areas: the anchored bbox is bounded by
    _MAX_SPAN_M everywhere, while the old link-extent union blew past it on
    19 of them (worst: 385 m for conflict area 378, whose map therefore
    showed ~2800 x 1230 m of a 1188 x 851 m network -- i.e. the whole thing,
    padded)."""
    shadow_model = _beijing_shadow_model()
    links_by_id = {lk["id"]: lk for lk in shadow_model["links"]}
    cap = approval_focus._MAX_SPAN_M
    resolved = 0
    old_over_cap = 0
    for conflict_area in shadow_model["conflict_areas"]:
        link_a = links_by_id.get(str(conflict_area["link_a"]))
        link_b = links_by_id.get(str(conflict_area["link_b"]))
        if not link_a or not link_b:
            continue
        focus = resolve_focus(
            "set_conflict_area_status",
            {"conflict_area_no": conflict_area["no"], "status": "PASSIVE"},
            shadow_model=shadow_model,
            inpx_path=_BEIJING_INPX,
        )
        assert focus["bbox"] is not None
        resolved += 1
        assert _fitted_span(focus["bbox"]) <= cap
        if _fitted_span(_extent_bbox(link_a["points"] + link_b["points"])) > cap:
            old_over_cap += 1
    assert resolved > 200  # sanity: the fixture really does have ~224 of them
    assert old_over_cap > 0  # ...and the cap really does bite on real data


def test_conflict_area_anchor_falls_back_to_closest_approach_when_polylines_miss(tmp_path: Path):
    """Connectors routinely do NOT strictly intersect the link they conflict
    with (72 of the Beijing example's 224 conflict areas), so the crossing
    resolver falls back to the midpoint of the two polylines' closest
    approach. Built synthetically because a *guaranteed* non-intersecting
    pair is the point of the test."""
    inpx_path = tmp_path / "near_miss.inpx"
    inpx_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network><links>'
        '<link no="1" name="EW"><geometry><linkPolyPts>'
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
        "</linkPolyPts></geometry><lanes><lane width=\"3.5\"/></lanes></link>"
        '<link no="2" name="NS"><geometry><linkPolyPts>'
        '<linkPolyPoint x="50" y="4" zOffset="0"/><linkPolyPoint x="50" y="200" zOffset="0"/>'
        "</linkPolyPts></geometry><lanes><lane width=\"3.5\"/></lanes></link>"
        "</links></network>",
        encoding="utf-8",
    )
    shadow_model = {
        "links": parse_network(str(inpx_path))["links"],
        "conflict_areas": [{"no": "77", "link_a": "1", "link_b": "2", "status": "PASSIVE"}],
    }

    focus = resolve_focus(
        "set_conflict_area_status",
        {"conflict_area_no": "77", "status": "AHASRIGHTOFWAY"},
        shadow_model=shadow_model,
        inpx_path=inpx_path,
    )

    assert focus["bbox"] is not None
    # The polylines never touch (they miss by 4 m); the anchor is the
    # midpoint of that closest approach: (50, 2).
    assert _centre(focus["bbox"]) == pytest.approx((50.0, 2.0))
    # NOT the link-extent union's centre (which would sit at (50, 100), 98 m
    # up the north-south approach and nowhere near the conflict).
    assert _centre(focus["bbox"]) != pytest.approx((50.0, 100.0))


def test_conflict_area_anchor_uses_the_true_crossing_when_polylines_do_intersect(tmp_path: Path):
    inpx_path = tmp_path / "crossing.inpx"
    inpx_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network><links>'
        '<link no="1" name="EW"><geometry><linkPolyPts>'
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
        "</linkPolyPts></geometry><lanes><lane width=\"3.5\"/></lanes></link>"
        '<link no="2" name="NS"><geometry><linkPolyPts>'
        '<linkPolyPoint x="30" y="-50" zOffset="0"/><linkPolyPoint x="30" y="250" zOffset="0"/>'
        "</linkPolyPts></geometry><lanes><lane width=\"3.5\"/></lanes></link>"
        "</links></network>",
        encoding="utf-8",
    )
    shadow_model = {
        "links": parse_network(str(inpx_path))["links"],
        "conflict_areas": [{"no": "88", "link_a": "1", "link_b": "2", "status": "PASSIVE"}],
    }
    focus = resolve_focus(
        "set_conflict_area_status",
        {"conflict_area_no": "88", "status": "AHASRIGHTOFWAY"},
        shadow_model=shadow_model,
        inpx_path=inpx_path,
    )
    assert focus["bbox"] is not None
    assert _centre(focus["bbox"]) == pytest.approx((30.0, 0.0))
    # 300 m of north-south link, but the anchored view is one junction wide.
    assert _fitted_span(focus["bbox"]) <= approval_focus._MAX_SPAN_M


@_requires_beijing
def test_route_relative_flow_focus_uses_link_seq():
    shadow_model = _beijing_shadow_model()
    route = next(r for r in shadow_model["routes"] if r["id"] == "1.1")
    assert route["link_seq"]  # sanity: this route's shape is known

    focus = resolve_focus(
        "set_route_relative_flow",
        {"decision_no": "1", "route_no": "1", "rel_flow": 300, "interval_no": 1},
        shadow_model=shadow_model,
        inpx_path=_BEIJING_INPX,
    )

    assert focus["bbox"] is not None
    keys = _element_keys(focus)
    for link_id in route["link_seq"]:
        assert ("link", link_id) in keys


@_requires_beijing
def test_vehicle_composition_share_focus_uses_referencing_vehicle_inputs():
    shadow_model = _beijing_shadow_model()
    composition = next(c for c in shadow_model["vehicle_compositions"] if c["no"] == "1")
    assert composition["referencing_vehicle_input_nos"]  # sanity

    focus = resolve_focus(
        "set_vehicle_composition_share",
        {"composition_no": "1", "veh_type_no": "100", "rel_flow": 1.0},
        shadow_model=shadow_model,
        inpx_path=_BEIJING_INPX,
    )

    assert focus["bbox"] is not None
    keys = _element_keys(focus)
    for vi_no in composition["referencing_vehicle_input_nos"]:
        assert ("vehicle_input", vi_no) in keys


@_requires_beijing
def test_vehicle_composition_share_focus_no_referencing_inputs_degrades_gracefully():
    focus = resolve_focus(
        "set_vehicle_composition_share",
        {"composition_no": "999-bogus", "veh_type_no": "100", "rel_flow": 1.0},
        shadow_model=_beijing_shadow_model(),
        inpx_path=_BEIJING_INPX,
    )
    assert focus["bbox"] is None
    assert focus["elements"] == []


@_requires_beijing
def test_signal_cycle_focus_highlights_every_head_under_the_controller():
    shadow_model = _beijing_shadow_model()
    net = parse_network(str(_BEIJING_INPX))
    heads_sc1 = [h for h in net["elements"]["signal_heads"] if h["sc"] == "1"]
    assert heads_sc1  # sanity: SC1 has real heads

    focus = resolve_focus(
        "set_signal_cycle",
        {"sc_no": "1", "cycle_s": 130},
        shadow_model=shadow_model,
        inpx_path=_BEIJING_INPX,
    )

    assert focus["bbox"] is not None
    keys = _element_keys(focus)
    for head in heads_sc1:
        assert ("signal_head", head["id"]) in keys
    _assert_non_degenerate(focus["bbox"])


@_requires_beijing
def test_signal_offset_focus_same_as_signal_cycle():
    shadow_model = _beijing_shadow_model()
    focus = resolve_focus(
        "set_signal_offset", {"sc_no": "1", "offset_s": 0}, shadow_model=shadow_model, inpx_path=_BEIJING_INPX
    )
    assert focus["bbox"] is not None
    assert any(e["type"] == "signal_head" for e in focus["elements"])


@_requires_beijing
def test_signal_green_split_focus_scopes_to_one_signal_group():
    shadow_model = _beijing_shadow_model()
    split = next(s for s in shadow_model["signal_program_splits"] if s["id"] == "1.1.1")
    assert split["sg_no"] == "1"

    focus_split = resolve_focus(
        "set_signal_green_split",
        {"sc_no": "1", "program_no": "1", "sg_no": "1", "green_start_s": 10, "green_end_s": 20},
        shadow_model=shadow_model,
        inpx_path=_BEIJING_INPX,
    )
    focus_controller = resolve_focus(
        "set_signal_cycle", {"sc_no": "1", "cycle_s": 130}, shadow_model=shadow_model, inpx_path=_BEIJING_INPX
    )

    assert focus_split["bbox"] is not None
    split_heads = [e for e in focus_split["elements"] if e["type"] == "signal_head"]
    controller_heads = [e for e in focus_controller["elements"] if e["type"] == "signal_head"]
    assert split_heads  # at least one head for this signal group
    # Scoped to ONE signal group -> strictly fewer (or equal, if the
    # controller has exactly one SG) heads than the whole-controller focus.
    assert len(split_heads) <= len(controller_heads)


# ---------------------------------------------------------------------------
# (c) set_des_speed_decision — synthetic fixture (no real example network has
# any desired-speed decisions), primary + optional paired restore target.
# ---------------------------------------------------------------------------


def _write_minimal_des_speed_inpx(tmp_path: Path) -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><network><links>'
        '<link no="101" name="Straight"><geometry><linkPolyPts>'
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
        '</linkPolyPts></geometry><lanes><lane width="3.5"/></lanes></link>'
        "</links>"
        "<desSpeedDecisions>"
        '<desSpeedDecision no="10" name="Slow" lane="101 1" pos="20"/>'
        '<desSpeedDecision no="20" name="Restore" lane="101 1" pos="80"/>'
        "</desSpeedDecisions>"
        "</network>"
    )
    path = tmp_path / "des_speed.inpx"
    path.write_text(xml, encoding="utf-8")
    return path


def test_des_speed_decision_focus_primary_only(tmp_path: Path):
    inpx_path = _write_minimal_des_speed_inpx(tmp_path)
    shadow_model = {"links": parse_network(str(inpx_path))["links"]}

    focus = resolve_focus(
        "set_des_speed_decision",
        {"decision_no": "10", "veh_class_no": "10", "des_speed_distr_no": "5"},
        shadow_model=shadow_model,
        inpx_path=inpx_path,
    )

    assert focus["bbox"] is not None
    keys = _element_keys(focus)
    assert ("des_speed_decision", "10") in keys
    assert ("des_speed_decision", "20") not in keys
    assert ("link", "101") in keys
    assert _contains(focus["bbox"], 20.0, 0.0)


def test_des_speed_decision_focus_primary_and_restore(tmp_path: Path):
    inpx_path = _write_minimal_des_speed_inpx(tmp_path)
    shadow_model = {"links": parse_network(str(inpx_path))["links"]}

    focus = resolve_focus(
        "set_des_speed_decision",
        {
            "decision_no": "10",
            "veh_class_no": "10",
            "des_speed_distr_no": "5",
            "restore_decision_no": "20",
            "restore_distr_no": "5",
        },
        shadow_model=shadow_model,
        inpx_path=inpx_path,
    )

    assert focus["bbox"] is not None
    keys = _element_keys(focus)
    assert ("des_speed_decision", "10") in keys
    assert ("des_speed_decision", "20") in keys
    _assert_non_degenerate(focus["bbox"])
    assert _contains(focus["bbox"], 20.0, 0.0)
    assert _contains(focus["bbox"], 80.0, 0.0)


# ---------------------------------------------------------------------------
# (d) graceful degradation: unsupported tools, no working copy, exceptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["set_sim_duration", "set_random_seed", "run_simulation", "get_vehicle_inputs", "totally_unknown_tool"])
def test_tools_with_no_single_target_get_unsupported_tool_note(tool: str):
    focus = resolve_focus(tool, {}, shadow_model=_demo_shadow_model(), inpx_path=DEMO_INPX)
    assert focus == {"elements": [], "bbox": None, "coordinate_note": "unsupported_tool"}


def test_no_working_copy_degrades_gracefully_without_raising():
    focus = resolve_focus(
        "set_vehicle_input_volume", {"input_no": "1", "volume": 600}, shadow_model=_demo_shadow_model(), inpx_path=None
    )
    assert focus == {"elements": [], "bbox": None, "coordinate_note": "no_working_copy"}


def test_none_shadow_model_still_resolves_via_independent_element_reparse():
    """``shadow_model=None`` (the real fallback ``orchestrator.py`` passes
    when no ``ArtifactManager`` exists yet) must not raise -- and, since
    element geometry (`_elements_for`) reparses links from the .inpx itself
    whenever the shadow model doesn't supply them, the target vehicle input's
    OWN point still resolves; only the extra "link" element (which needs
    shadow_model["links"] for the id lookup) is missing."""
    focus = resolve_focus(
        "set_vehicle_input_volume", {"input_no": "1", "volume": 600}, shadow_model=None, inpx_path=DEMO_INPX
    )
    assert focus["bbox"] is not None
    assert ("vehicle_input", "1") in _element_keys(focus)
    assert ("link", "101") not in _element_keys(focus)


def test_corrupt_inpx_path_degrades_gracefully_without_raising(tmp_path: Path):
    bogus = tmp_path / "not-real-xml.inpx"
    bogus.write_text("this is not xml at all {{{", encoding="utf-8")
    focus = resolve_focus(
        "set_vehicle_input_volume", {"input_no": "1", "volume": 600}, shadow_model=_demo_shadow_model(), inpx_path=bogus
    )
    assert focus == {"elements": [], "bbox": None, "coordinate_note": "no_target_geometry"}


def test_a_resolver_exception_cannot_break_approval(monkeypatch: pytest.MonkeyPatch):
    """The hard rule: NEVER fail the approval flow because the map can't be
    built. Rigs a resolver that raises and proves resolve_focus still
    returns the graceful "no focus" shape instead of propagating."""

    def _boom(params: Any, shadow_model: Any, elements: Any) -> Any:
        raise RuntimeError("simulated resolver bug")

    monkeypatch.setitem(approval_focus._RESOLVERS, "set_vehicle_input_volume", _boom)

    focus = resolve_focus(
        "set_vehicle_input_volume", {"input_no": "1", "volume": 600}, shadow_model=_demo_shadow_model(), inpx_path=DEMO_INPX
    )
    assert focus == {"elements": [], "bbox": None, "coordinate_note": "resolver_error"}


def test_missing_params_cannot_break_approval():
    """Malformed/missing params (e.g. a planner emitting the wrong shape)
    must degrade, never raise, mirroring guardrails.py's own defensive
    `.get(..., "")` reads throughout _WRITE_TARGET_FIELD_SPEC consumers."""
    focus = resolve_focus(
        "set_signal_green_split", {}, shadow_model=_demo_shadow_model(), inpx_path=DEMO_INPX
    )
    assert focus == {"elements": [], "bbox": None, "coordinate_note": "no_target_geometry"}


# ---------------------------------------------------------------------------
# (e) degenerate-bbox floor: a single-point target still yields a usable,
# non-degenerate bbox (task brief: "bbox sanity ... non-degenerate").
# ---------------------------------------------------------------------------


def test_single_point_target_bbox_is_still_non_degenerate(tmp_path: Path):
    inpx_path = _write_minimal_des_speed_inpx(tmp_path)
    shadow_model = {"links": parse_network(str(inpx_path))["links"]}

    focus = resolve_focus(
        "set_des_speed_decision",
        {"decision_no": "10", "veh_class_no": "10", "des_speed_distr_no": "5"},
        shadow_model=shadow_model,
        inpx_path=inpx_path,
    )
    _assert_non_degenerate(focus["bbox"])


# ---------------------------------------------------------------------------
# (f) span policy (zoom defect, 2026-07-28): the _MIN_SPAN_M floor, the
# _MAX_SPAN_M cap on anchored focuses, and the deliberately UNCAPPED
# anchorless (extent-based) path.
# ---------------------------------------------------------------------------


def test_min_span_floor_still_guards_a_degenerate_anchorless_bbox():
    """The floor is the frontend's divide-by-zero guard, so it must survive
    on the path that can still produce a zero-span bbox: anchorless."""
    acc = approval_focus._FocusAccumulator()
    acc.add_point(10.0, 10.0)
    bbox = acc.result()["bbox"]
    assert _spans(bbox) == pytest.approx((approval_focus._MIN_SPAN_M, approval_focus._MIN_SPAN_M))


def test_anchored_span_is_capped_at_one_intersections_worth():
    acc = approval_focus._FocusAccumulator()
    # Anchors 150 m apart: 150 + 2*60 of context would be 270 m, capped to
    # _MAX_SPAN_M. The cap eats the CONTEXT margin, never the anchors.
    acc.add_anchor(0.0, 0.0)
    acc.add_anchor(150.0, 0.0)
    bbox = acc.result()["bbox"]
    assert _spans(bbox)[0] == pytest.approx(approval_focus._MAX_SPAN_M)
    assert _contains(bbox, 0.0, 0.0)
    assert _contains(bbox, 150.0, 0.0)


def test_anchored_span_never_crops_an_anchor_that_is_itself_wider_than_the_cap():
    """A fan-out target CAN legitimately spread wider than one intersection
    (a vehicle composition referenced by inputs across the network; a signal
    controller with far-flung heads). Capping there would hide half the
    targets, which is strictly worse than a wide view -- so the cap floors
    out at the anchors' own span."""
    acc = approval_focus._FocusAccumulator()
    acc.add_anchor(0.0, 0.0)
    acc.add_anchor(900.0, 0.0)
    bbox = acc.result()["bbox"]
    assert _spans(bbox)[0] == pytest.approx(900.0)
    assert _contains(bbox, 0.0, 0.0)
    assert _contains(bbox, 900.0, 0.0)


def test_a_link_extent_never_widens_an_anchored_bbox():
    """The defect in one assertion: an anchor plus a 10 km link must produce
    the anchor's view, not the link's."""
    acc = approval_focus._FocusAccumulator()
    acc.add_anchor(0.0, 0.0)
    acc.add_link({"id": "1", "name": "long", "points": [[-5000.0, 0.0], [5000.0, 0.0]]})
    bbox = acc.result()["bbox"]
    assert _spans(bbox)[0] == pytest.approx(2 * approval_focus._ANCHOR_CONTEXT_M)
    assert ("link", "1") in {(e["type"], e["id"]) for e in acc.result()["elements"]}


@_requires_beijing
def test_route_focus_stays_extent_based_and_uncapped():
    """Routes are the anchorless case: a route IS long, so cropping it to
    one intersection would misrepresent the target. Its bbox must still be
    exactly the union of its link_seq's extents (i.e. unchanged by this
    unit), even when that exceeds _MAX_SPAN_M."""
    shadow_model = _beijing_shadow_model()
    links_by_id = {lk["id"]: lk for lk in shadow_model["links"]}
    route = next(r for r in shadow_model["routes"] if r["id"] == "1.1")

    focus = resolve_focus(
        "set_route_relative_flow",
        {"decision_no": "1", "route_no": "1", "rel_flow": 300, "interval_no": 1},
        shadow_model=shadow_model,
        inpx_path=_BEIJING_INPX,
    )

    seq_points = [p for lid in route["link_seq"] for p in links_by_id[str(lid)]["points"]]
    expected = _extent_bbox(seq_points)
    assert focus["bbox"]["x_min"] == pytest.approx(expected["x_min"])
    assert focus["bbox"]["x_max"] == pytest.approx(expected["x_max"])
    assert _fitted_span(focus["bbox"]) > approval_focus._MAX_SPAN_M / 2


@_requires_beijing
def test_signal_controller_focus_is_anchored_on_its_heads_not_their_links():
    shadow_model = _beijing_shadow_model()
    net = parse_network(str(_BEIJING_INPX))
    heads = [h for h in net["elements"]["signal_heads"] if h["sc"] == "1" and h.get("x") is not None]

    focus = resolve_focus(
        "set_signal_cycle", {"sc_no": "1", "cycle_s": 130}, shadow_model=shadow_model, inpx_path=_BEIJING_INPX
    )
    bbox = focus["bbox"]
    assert bbox is not None
    # Every head stays inside the view, and the view is one junction wide --
    # even though the focus also highlights every approach link they sit on.
    for head in heads:
        assert _contains(bbox, head["x"], head["y"])
    assert _fitted_span(bbox) <= approval_focus._MAX_SPAN_M
    assert any(e["type"] == "link" for e in focus["elements"])

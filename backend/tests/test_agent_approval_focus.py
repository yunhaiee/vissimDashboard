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
    for px, py in link["points"]:
        assert _contains(focus["bbox"], px, py)


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
    for px, py in link["points"]:
        assert _contains(focus["bbox"], px, py)


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

    links_by_id = {lk["id"]: lk for lk in shadow_model["links"]}
    for link_id in ("3", "12"):
        for px, py in links_by_id[link_id]["points"]:
            assert _contains(focus["bbox"], px, py)


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

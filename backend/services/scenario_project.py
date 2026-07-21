"""Scenario management helpers for applying VISSIM .trax modifications."""
from __future__ import annotations

from copy import deepcopy
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from services.datasets import get_active_modifications_dir, get_active_project_db_path, get_active_scenarios_dir

SCENARIO_COLORS = {
    "S000001": "#4e79a7",
    "S000003": "#f28e2b",
    "S000004": "#59a14f",
}


def list_project_scenarios() -> list[dict]:
    project_db_path = get_active_project_db_path()
    if not project_db_path.exists():
        return _list_scenarios_from_results()

    conn = sqlite3.connect(project_db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT NO, NAME, MODIFICATIONS FROM SCENARIO ORDER BY NO")
        rows = cur.fetchall()
    finally:
        conn.close()

    scenarios = []
    for number, name, modifications in rows:
        scenario_id = f"S{int(number):06d}"
        scenarios.append({
            "id": scenario_id,
            "name": name or scenario_id,
            "color": SCENARIO_COLORS.get(scenario_id, "#64748b"),
            "number": int(number),
            "modifications": _parse_modifications(modifications),
        })
    return scenarios


def get_project_scenario(scenario_id: str) -> dict | None:
    for scenario in list_project_scenarios():
        if scenario["id"] == scenario_id:
            return scenario
    return None


def can_build_scenario_tree(scenario_id: str | None) -> bool:
    """True when ``scenario_id`` resolves to a scenario in the active project.

    Decides whether to apply scenario modifications instead of parsing the base
    network. Keyed on the active project (vissimpdb / Scenarios dir), NOT the
    inpx filename, so it works for uploaded datasets (``current.inpx``) and not
    only the bundled ``practice.inpx`` demo.
    """
    return bool(scenario_id) and get_project_scenario(scenario_id) is not None


def _list_scenarios_from_results() -> list[dict]:
    scenarios_dir = get_active_scenarios_dir()
    if not scenarios_dir.exists():
        return []

    scenarios = []
    for scenario_dir in sorted(scenarios_dir.iterdir()):
        if not scenario_dir.is_dir():
            continue
        scenario_id = scenario_dir.name
        if not scenario_id.startswith("S") or not scenario_id[1:].isdigit():
            continue
        scenarios.append({
            "id": scenario_id,
            "name": scenario_id,
            "color": SCENARIO_COLORS.get(scenario_id, "#64748b"),
            "number": int(scenario_id[1:]),
            "modifications": [],
        })
    return scenarios


def build_scenario_tree(base_inpx: Path, scenario_id: str | None) -> ET.ElementTree:
    tree = ET.parse(base_inpx)
    if not scenario_id:
        return tree

    scenario = get_project_scenario(scenario_id)
    if scenario is None:
        raise ValueError(f"Unknown scenario_id: {scenario_id}")

    modifications_dir = get_active_modifications_dir()
    root = tree.getroot()
    for modification_no in scenario["modifications"]:
        _apply_modification(root, modifications_dir / f"M{modification_no:06d}.trax")
    return tree


def _apply_modification(root: ET.Element, modification_path: Path) -> None:
    mod_tree = ET.parse(modification_path)
    mod_root = mod_tree.getroot()

    for section in list(mod_root):
        container = root.find(section.tag)
        if container is None:
            container = ET.SubElement(root, section.tag)
        _merge_element(container, section)


def _merge_element(target: ET.Element, patch: ET.Element) -> None:
    target.attrib.update(patch.attrib)
    if not list(patch) and not patch.attrib:
        for child in list(target):
            target.remove(child)
        return
    if _should_replace_children(patch):
        for child in list(target):
            target.remove(child)
        for child in list(patch):
            target.append(_materialize_element(child))
        return

    for child in list(patch):
        if child.tag == "overwritten":
            for existing_child in list(target):
                target.remove(existing_child)
            for overwritten_child in list(child):
                target.append(_materialize_element(overwritten_child))
        elif child.tag == "deleted":
            for deleted_child in list(child):
                existing = _find_by_identity(target, deleted_child)
                if existing is not None:
                    target.remove(existing)
        elif child.tag == "added":
            for added_child in list(child):
                _insert_added_child(target, _materialize_element(added_child))
        elif child.tag == "modified":
            if child.attrib and not list(child):
                target.attrib.update(child.attrib)
                continue
            for modified_child in list(child):
                existing = _find_by_identity(target, modified_child)
                if existing is None:
                    _insert_added_child(target, _materialize_element(modified_child))
                else:
                    _merge_element(existing, modified_child)
        else:
            existing = _find_by_identity(target, child)
            if existing is None:
                _insert_added_child(target, _materialize_element(child))
            else:
                _merge_element(existing, child)


def _materialize_element(source: ET.Element) -> ET.Element:
    element = ET.Element(source.tag, dict(source.attrib))
    for child in list(source):
        if child.tag in {"added", "deleted", "modified", "overwritten"}:
            _merge_element(element, child)
        else:
            element.append(_materialize_element(child))
    return element


def _find_by_identity(container: ET.Element, candidate: ET.Element) -> ET.Element | None:
    candidate_no = candidate.get("no")
    if candidate_no is not None:
        for child in list(container):
            if child.tag == candidate.tag and child.get("no") == candidate_no:
                return child

    candidate_index = candidate.get("index")
    if candidate_index is not None and candidate_index.isdigit():
        same_tag_children = [child for child in list(container) if child.tag == candidate.tag]
        index = int(candidate_index) - 1
        if 0 <= index < len(same_tag_children):
            return same_tag_children[index]

    candidate_lane = candidate.get("lane")
    candidate_pos = candidate.get("pos")
    if candidate_lane is not None and candidate_pos is not None:
        for child in list(container):
            if (
                child.tag == candidate.tag
                and child.get("lane") == candidate_lane
                and child.get("pos") == candidate_pos
            ):
                return child

    same_tag_children = [child for child in list(container) if child.tag == candidate.tag]
    if len(same_tag_children) == 1:
        return same_tag_children[0]
    return None


def _parse_modifications(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _should_replace_children(element: ET.Element) -> bool:
    children = list(element)
    if not children:
        return False
    if len(children) == 1:
        return False
    if any(child.tag in {"added", "deleted", "modified"} for child in children):
        return False
    tags = {child.tag for child in children}
    if len(tags) != 1:
        return False
    return all(not _has_identity(child) for child in children)


def _has_identity(element: ET.Element) -> bool:
    if element.get("no") is not None:
        return True
    if element.get("index") is not None:
        return True
    return element.get("lane") is not None and element.get("pos") is not None


def _insert_added_child(target: ET.Element, child: ET.Element) -> None:
    child_index = child.get("index")
    if child_index is not None and child_index.isdigit():
        same_tag_children = [element for element in list(target) if element.tag == child.tag]
        desired_index = int(child_index) - 1
        if 0 <= desired_index < len(same_tag_children):
            anchor = same_tag_children[desired_index]
            target.insert(list(target).index(anchor), child)
            return
    target.append(child)

"""Build standalone simulation INPX files with runtime incident overrides."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from services.scenario_project import build_scenario_tree

MOTOR_VEHICLE_CLASSES = ("10", "19", "20")
DEFAULT_DECEL = "2"


def build_simulation_network(
    *,
    source_inpx: Path,
    job_id: str,
    scenario_id: str | None,
    speed_limits: list[dict],
) -> Path:
    tree = build_scenario_tree(source_inpx, scenario_id)
    root = tree.getroot()

    output_dir = Path(__file__).resolve().parent.parent.parent / "runtime" / "simulations" / job_id
    results_dir = output_dir / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    _make_network_standalone(root, results_dir)
    _apply_speed_limits(root, speed_limits)

    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")

    output_path = output_dir / f"{source_inpx.stem}.{job_id}.interactive.inpx"
    tree.write(output_path, encoding="UTF-8", xml_declaration=True)
    return output_path


def _make_network_standalone(root: ET.Element, results_dir: Path) -> None:
    net_para = root.find(".//netPara")
    if net_para is not None:
        net_para.set("underScenMngm", "false")

    simulation = root.find(".//simulation")
    if simulation is not None:
        # Interactive runs should finish quickly and produce a single result set.
        simulation.set("numRuns", "1")
        simulation.set("simRes", "2")
        simulation.set("useMaxSimSpeed", "true")

    evaluation = root.find(".//evaluation")
    if evaluation is not None:
        evaluation.set("evalOutDir", str(results_dir))
        evaluation.set("keepPrevResults", "DELETEALL")


def _apply_speed_limits(root: ET.Element, speed_limits: list[dict]) -> None:
    if not speed_limits:
        return

    links = _index_links(root)
    speed_distributions = root.find(".//speedDistributions")
    if speed_distributions is None:
        raise RuntimeError("speedDistributions section not found in source INPX")

    reduced_speed_areas = root.find(".//reducedSpeedAreas")
    if reduced_speed_areas is None:
        raise RuntimeError("reducedSpeedAreas section not found in source INPX")

    next_speed_distribution_no = _next_number(speed_distributions, "speedDistribution")
    next_reduced_speed_area_no = _next_number(reduced_speed_areas, "reducedSpeedArea")
    distribution_by_speed: dict[int, str] = {}

    for incident in speed_limits:
        link_no = str(incident["link_id"])
        speed_limit = int(incident["speed_limit"])
        duration = max(int(incident["duration"]), 1)
        link = links.get(link_no)
        if link is None:
            continue

        distribution_no = distribution_by_speed.get(speed_limit)
        if distribution_no is None:
            distribution_no = str(next_speed_distribution_no)
            _append_speed_distribution(speed_distributions, distribution_no, speed_limit)
            distribution_by_speed[speed_limit] = distribution_no
            next_speed_distribution_no += 1

        lane_count = _lane_count(link)
        link_length = max(_link_length(link), 0.1)
        for lane_no in range(1, lane_count + 1):
            _append_reduced_speed_area(
                reduced_speed_areas,
                no=str(next_reduced_speed_area_no),
                link_no=link_no,
                lane_no=str(lane_no),
                length=link_length,
                duration=duration,
                distribution_no=distribution_no,
            )
            next_reduced_speed_area_no += 1


def _append_speed_distribution(container: ET.Element, distribution_no: str, speed_limit: int) -> None:
    distribution = ET.SubElement(
        container,
        "speedDistribution",
        {
            "name": f"Interactive {speed_limit} km/h",
            "no": distribution_no,
        },
    )
    points = ET.SubElement(distribution, "speedDistrDatPts")
    low = max(speed_limit - 0.1, 0.0)
    high = speed_limit + 0.1
    ET.SubElement(points, "speedDistributionDataPoint", {"fx": "0", "x": f"{low:.1f}"})
    ET.SubElement(points, "speedDistributionDataPoint", {"fx": "1", "x": f"{high:.1f}"})


def _append_reduced_speed_area(
    container: ET.Element,
    *,
    no: str,
    link_no: str,
    lane_no: str,
    length: float,
    duration: int,
    distribution_no: str,
) -> None:
    area = ET.SubElement(
        container,
        "reducedSpeedArea",
        {
            "anmFlag": "false",
            "lane": f"{link_no} {lane_no}",
            "length": f"{length:.6f}",
            "name": f"interactive_speed_limit_link_{link_no}",
            "no": no,
            "pos": "0",
            "timeFrom": "0",
            "timeTo": str(duration),
        },
    )
    relation = ET.SubElement(area, "vehClassSpeedRed")
    for vehicle_class in MOTOR_VEHICLE_CLASSES:
        ET.SubElement(
            relation,
            "vehClassSpeedReduction",
            {
                "decel": DEFAULT_DECEL,
                "desSpeedDistr": distribution_no,
                "vehClass": vehicle_class,
            },
        )


def _index_links(root: ET.Element) -> dict[str, ET.Element]:
    links: dict[str, ET.Element] = {}
    links_container = root.find(".//links")
    if links_container is None:
        return links
    for link in links_container.findall("link"):
        link_no = link.get("no")
        if link_no:
            links[link_no] = link
    return links


def _lane_count(link: ET.Element) -> int:
    lanes = link.find("lanes")
    if lanes is None:
        return 1
    return max(len(lanes.findall("lane")), 1)


def _link_length(link: ET.Element) -> float:
    geometry = link.find("geometry")
    if geometry is None:
        return 0.0
    points = geometry.find("linkPolyPts")
    if points is None:
        return 0.0

    coords: list[tuple[float, float]] = []
    for point in points.findall("linkPolyPoint"):
        x = float(point.get("x", "0"))
        y = float(point.get("y", "0"))
        coords.append((x, y))

    total = 0.0
    for idx in range(1, len(coords)):
        dx = coords[idx][0] - coords[idx - 1][0]
        dy = coords[idx][1] - coords[idx - 1][1]
        total += math.hypot(dx, dy)
    return total


def _next_number(container: ET.Element, child_tag: str) -> int:
    numbers = []
    for child in container.findall(child_tag):
        value = child.get("no")
        if value and value.isdigit():
            numbers.append(int(value))
    return (max(numbers) if numbers else 0) + 1

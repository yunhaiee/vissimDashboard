"""Build standalone simulation INPX files with runtime incident overrides.

Also home to :func:`inject_reduced_speed_area` / :func:`reparse_reduced_speed_area`
(v2 unit (2)): the ``reducedSpeedArea``/``speedDistribution`` element-building
logic below was written first for :func:`build_simulation_network`'s interactive
incident overrides; ``services.agent.tools_edit.set_reduced_speed_area`` reuses
the SAME ``_append_speed_distribution``/``_append_reduced_speed_area`` element
builders (generalized with a few extra keyword params -- ``pos``/``time_from``/
``time_to``/``name``/``precision``/``margin_kmh`` -- so both callers share one
XML-shape source of truth instead of two near-duplicate builders drifting
apart) rather than duplicating them, per the M2.3 report's drafted
DECISIONS.md entry. The two call sites differ only in WHAT they inject: an
interactive incident covers a link's FULL length on every lane for a bounded
SIMULATION-TIME window (``timeTo=duration``); an agent-injected area covers an
arbitrary [from_m, from_m+length_m) slice on every lane, permanently
(``timeTo=2147483647`` -- INT32_MAX, PTV's own "no time restriction" sentinel,
verified against a real Vissim 2026 example network's exported .inpx: see the
inject function's docstring).
"""
from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from services.scenario_project import build_scenario_tree

MOTOR_VEHICLE_CLASSES = ("10", "19", "20")
DEFAULT_DECEL = "2"

#: PTV's own "no time restriction" sentinel for a reducedSpeedArea's timeTo
#: (INT32_MAX) -- verified against a real Vissim 2026 example network's
#: exported .inpx (Air Taxi Vertiport, every non-incident reducedSpeedArea
#: carries timeFrom="0" timeTo="2147483647"). Used by
#: :func:`inject_reduced_speed_area` for a PERMANENT (not time-boxed) area --
#: the interactive-incident path above keeps using a real duration instead.
PERMANENT_TIME_TO = 2147483647

#: Decimal precision (both formatting and the numeric-tolerance budget it
#: must clear) for agent-injected reducedSpeedArea/speedDistribution
#: attributes. Chosen so that format -> disk -> reparse round-trip noise
#: (at most 0.5 * 10**-_INJECTED_PRECISION per attribute, a plain fixed-point
#: rounding bound, independent of magnitude for the sub-kilometer/sub-200kph
#: values this tool ever sees) stays comfortably under
#: ``guardrails._values_differ``'s / constraints.py's shared 1e-9 float
#: tolerance -- the same design move DECISIONS.md's 2026-07-23 "float
#: tolerance" entry made for Vissim's OWN SaveNetAs noise, applied here to
#: OUR serialization instead of Vissim's.
_INJECTED_PRECISION = 9

#: Vissim desired-speed distributions need a non-degenerate (low < high)
#: range; a bare scalar speed is represented as a tight +/-margin_kmh band
#: around the target, mirrared from the interactive-incident path's existing
#: +/-0.1 convention (there hardcoded via the 0.1/-0.1 literals in
#: ``_append_speed_distribution``'s old body).
_SPEED_MARGIN_KMH = 0.1


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
            _append_speed_distribution(
                speed_distributions,
                distribution_no,
                speed_limit,
                name=f"Interactive {speed_limit} km/h",
            )
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
                pos=0.0,
                length=link_length,
                time_from=0,
                time_to=duration,
                distribution_no=distribution_no,
                name=f"interactive_speed_limit_link_{link_no}",
            )
            next_reduced_speed_area_no += 1


def _append_speed_distribution(
    container: ET.Element,
    distribution_no: str,
    speed_kmh: float,
    *,
    name: str,
    margin_kmh: float = 0.1,
    precision: int = 1,
) -> None:
    """Append one ``<speedDistribution>`` with a tight [speed-margin,
    speed+margin] two-point range (Vissim distributions cannot be a bare
    scalar -- constraints.py's ``speed_limit_edit_ambiguous``/catalog SS2 "The
    main calibration parameter isn't a number" note applies to desired-speed
    objects generally, not just CC1).

    ``margin_kmh``/``precision`` default to the values the ORIGINAL
    interactive-incident caller always used (0.1 km/h margin, one decimal
    place) so that call site's output is byte-for-byte unchanged by this
    generalization. ``inject_reduced_speed_area`` below passes a much higher
    ``precision`` (see ``_INJECTED_PRECISION``'s docstring) because it needs
    the two points to round-trip through disk accurately enough for
    ``guardrails.py``'s 1e-9 float-tolerance postwrite compare -- the
    interactive-incident path has no such compare (nothing reparses its
    output for verification), so the coarser default is harmless there.
    """
    distribution = ET.SubElement(
        container,
        "speedDistribution",
        {"name": name, "no": distribution_no},
    )
    points = ET.SubElement(distribution, "speedDistrDatPts")
    low = max(speed_kmh - margin_kmh, 0.0)
    high = speed_kmh + margin_kmh
    ET.SubElement(points, "speedDistributionDataPoint", {"fx": "0", "x": f"{low:.{precision}f}"})
    ET.SubElement(points, "speedDistributionDataPoint", {"fx": "1", "x": f"{high:.{precision}f}"})


def _append_reduced_speed_area(
    container: ET.Element,
    *,
    no: str,
    link_no: str,
    lane_no: str,
    pos: float,
    length: float,
    time_from: int,
    time_to: int,
    distribution_no: str,
    name: str,
    precision: int = 6,
) -> None:
    """Append one ``<reducedSpeedArea>`` (+ its ``vehClassSpeedRed`` motor-
    vehicle-class relation) on one lane of ``link_no``.

    ``precision`` defaults to 6 (the ORIGINAL hardcoded ``.6f`` the
    interactive-incident caller always used for ``length``, with ``pos``
    previously a hardcoded literal ``"0"`` string -- now ``f"{0.0:.6f}"`` ==
    ``"0.000000"``, numerically identical to Vissim, which parses the
    attribute as a float, not a literal string). ``inject_reduced_speed_area``
    below passes ``_INJECTED_PRECISION`` (9) for its own tolerance-budget
    reasons (see that constant's docstring).
    """
    area = ET.SubElement(
        container,
        "reducedSpeedArea",
        {
            "anmFlag": "false",
            "lane": f"{link_no} {lane_no}",
            "length": f"{length:.{precision}f}",
            "name": name,
            "no": no,
            "pos": f"{pos:.{precision}f}",
            "timeFrom": str(time_from),
            "timeTo": str(time_to),
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


# ---------------------------------------------------------------------------
# v2 unit (2): agent-tool XML injection (set_reduced_speed_area)
#
# services.agent.tools_edit.set_reduced_speed_area's write path. Unlike
# build_simulation_network above (which builds a brand-new standalone file
# under runtime/simulations/<job_id>/), this injects DIRECTLY onto an
# existing file in place -- the caller's own working.inpx (an
# ArtifactManager-owned session work-dir path, per
# docs/phase2/00-EXECUTION-GUIDE.md rule 4: never the source dataset).
# ---------------------------------------------------------------------------

#: ``<network>``'s direct children always appear in this exact order in a
#: real Vissim-exported .inpx -- verified against the FULL top-level child
#: tag listing of two independent real 2026 example networks (Air Taxi
#: Vertiport, Urban Intersection Beijing.CN): plain case-sensitive
#: alphabetical sort of the tag name. A section a given network has never
#: used (e.g. Beijing has no ``reducedSpeedAreas`` at all -- see
#: ``inject_reduced_speed_area``'s docstring) is OMITTED entirely rather than
#: present-but-empty, so a section this tool needs may have to be created,
#: and doing so at the wrong position risks LoadNet order-sensitive
#: validation. This constant intentionally does NOT enumerate every possible
#: tag (that would be a second, drifting schema) -- see
#: :func:`_get_or_create_top_level_section` for how it is actually used (a
#: pure alphabetical comparison against whatever siblings already exist, not
#: a lookup into this list).
_TOP_LEVEL_SECTION_ORDER_NOTE = (
    "network's direct children sort alphabetically by tag name in a real "
    "Vissim export; see _get_or_create_top_level_section"
)


def _get_or_create_top_level_section(root: ET.Element, tag: str) -> ET.Element:
    """Return ``root``'s existing direct-child ``<tag>`` element, or create
    and insert an empty one at the alphabetically-correct position among
    ``root``'s CURRENT direct children (matching real Vissim exports -- see
    :data:`_TOP_LEVEL_SECTION_ORDER_NOTE` and
    :func:`inject_reduced_speed_area`'s docstring for the live finding this
    fixes). Only ever looks at direct children of ``root`` (``root.find(tag)``,
    not ``.//tag``) -- a top-level section is never nested.
    """
    existing = root.find(tag)
    if existing is not None:
        return existing

    insert_at = len(list(root))
    for index, child in enumerate(root):
        if child.tag > tag:
            insert_at = index
            break
    section = ET.Element(tag)
    root.insert(insert_at, section)
    return section


def inject_reduced_speed_area(
    inpx_path: Path | str,
    *,
    link_no: str,
    speed_kmh: float,
    from_m: float,
    length_m: float,
) -> dict[str, Any]:
    """Inject a PERMANENT, spatially-bounded reducedSpeedArea covering every
    lane of ``link_no`` over ``[from_m, from_m + length_m)``, in place onto
    ``inpx_path`` (parsed, mutated, and written back to that SAME path --
    never a new file, unlike ``build_simulation_network``).

    "Permanent" here means no simulation-TIME restriction (``timeFrom="0"``,
    ``timeTo=PERMANENT_TIME_TO``) -- this is what makes a reduced-speed area
    the SAFE speed-editing verb per constraints.py's ``speed_decision_unpaired``
    catalog note (SS2 "A speed decision is permanent unless you undo it"): a
    desired-speed DECISION with no restoring pair leaves the entire rest of
    the network slowed indefinitely, whereas a reducedSpeedArea is bounded by
    ``pos``/``length`` REGARDLESS of its time window -- the network outside
    ``[from_m, from_m+length_m)`` on ``link_no`` is never touched no matter
    how long the area stays active. See
    ``services.agent.tools_edit.set_reduced_speed_area``'s own docstring for
    the planner-facing framing of this distinction.

    Writes via a sibling temp file + ``os.replace`` (mirrors
    ``VissimEditSession.persist()``'s two-step convention in edit_session.py)
    so a mid-write crash can never leave ``inpx_path`` half-written.

    Returns ``{"area_nos": [...], "distribution_no": str, "lane_count": int,
    "written_value": {"link_id", "speed_kmh", "from_m", "length_m"}}`` --
    ``written_value`` is pre-rounded to ``_INJECTED_PRECISION`` decimals, the
    SAME rounding the persisted XML attributes carry, so a caller comparing
    it against :func:`reparse_reduced_speed_area`'s output needs no separate
    tolerance reasoning of its own (both sides already agree to the digit).

    Raises:
        ValueError: ``link_no`` is not a link in ``inpx_path`` (should not
            happen in production -- ``constraints.py``'s ``object_not_found``
            preflight check already confirms the link exists in the shadow
            model before this tool ever dispatches; kept defensive rather
            than assumed, same stance as ``tools_edit._coerce_id``).

    Note on ``speedDistributions``/``reducedSpeedAreas``: an EARLIER version
    of this function raised ``RuntimeError`` when either section was
    missing, mirroring ``_apply_speed_limits``'s existing guard. LIVE-VERIFIED
    WRONG against the real "Urban Intersection Beijing.CN" 2026 example (this
    function's own live acceptance script,
    ``backend/tools/reduced_speed_area_live_check.py``): Vissim omits a
    top-level section ENTIRELY when the feature has never been used in that
    network, rather than exporting it as an empty container -- Beijing has
    ``<speedDistributions>`` (from its signal timing) but genuinely no
    ``<reducedSpeedAreas>`` element at all. Both sections are now created via
    :func:`_get_or_create_top_level_section` when absent, inserted at the
    ALPHABETICALLY-SORTED position Vissim's own exporter always uses for
    ``<network>``'s direct children (verified against two independent real
    2026 example networks' full child-tag listings) -- not merely appended
    before ``</network>``, in case LoadNet performs order-sensitive
    validation. See the M2.3 report's drafted DECISIONS.md entry.
    """
    inpx_path = Path(inpx_path)
    tree = ET.parse(inpx_path)
    root = tree.getroot()

    links = _index_links(root)
    link = links.get(link_no)
    if link is None:
        raise ValueError(f"inject_reduced_speed_area: link {link_no!r} not found in {inpx_path}")

    speed_distributions = _get_or_create_top_level_section(root, "speedDistributions")
    reduced_speed_areas = _get_or_create_top_level_section(root, "reducedSpeedAreas")

    pos = round(float(from_m), _INJECTED_PRECISION)
    length = round(float(length_m), _INJECTED_PRECISION)
    speed_kmh = round(float(speed_kmh), _INJECTED_PRECISION)

    distribution_no = str(_next_number(speed_distributions, "speedDistribution"))
    _append_speed_distribution(
        speed_distributions,
        distribution_no,
        speed_kmh,
        name=f"agent_reduced_speed_link_{link_no}_{distribution_no}",
        margin_kmh=_SPEED_MARGIN_KMH,
        precision=_INJECTED_PRECISION,
    )

    lane_count = _lane_count(link)
    next_area_no = _next_number(reduced_speed_areas, "reducedSpeedArea")
    area_nos: list[str] = []
    for lane_no in range(1, lane_count + 1):
        area_no = str(next_area_no)
        _append_reduced_speed_area(
            reduced_speed_areas,
            no=area_no,
            link_no=link_no,
            lane_no=str(lane_no),
            pos=pos,
            length=length,
            time_from=0,
            time_to=PERMANENT_TIME_TO,
            distribution_no=distribution_no,
            name=f"agent_reduced_speed_link_{link_no}",
            precision=_INJECTED_PRECISION,
        )
        area_nos.append(area_no)
        next_area_no += 1

    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    tmp_path = inpx_path.with_name(inpx_path.stem + "_rsa_saving" + inpx_path.suffix)
    tree.write(tmp_path, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp_path, inpx_path)

    return {
        "area_nos": area_nos,
        "distribution_no": distribution_no,
        "lane_count": lane_count,
        "written_value": {
            "link_id": link_no,
            "speed_kmh": speed_kmh,
            "from_m": pos,
            "length_m": length,
        },
    }


def reparse_reduced_speed_area(inpx_path: Path | str, area_nos: list[str]) -> dict[str, Any]:
    """The other half of :func:`inject_reduced_speed_area`'s verification
    story: a FRESH ``ET.parse`` of ``inpx_path`` straight from disk (never
    the in-memory tree :func:`inject_reduced_speed_area` already wrote --
    that would only prove the Python object is correct, not that the SAVED
    FILE is) that finds every one of ``area_nos`` by its ``no`` attribute,
    confirms each is attached to the lane ``lane_no`` its ``lane`` attribute
    starts with, resolves its ``vehClassSpeedRed`` speed distribution, and
    derives the represented speed as the midpoint of that distribution's two
    ``speedDistrDatPts`` points.

    This IS the tool's whole read-back proof: there is no COM getter for an
    XML-injected object (see ``set_reduced_speed_area``'s docstring) --
    "element present after a fresh re-parse + attributes match" is the
    read-back, standing in for the COM set-then-get pattern every other M2
    write tool uses.

    Raises:
        RuntimeError: any ``area_nos`` entry, its distribution, or that
            distribution's two data points are missing after the save --
            i.e. the write did not actually reach disk the way it claimed
            to. Raising (never returning a partial/inconsistent dict) is
            deliberate: this is a genuine corruption signal, stronger than
            an ordinary guardrail refusal, and should surface as
            ``tool_execution_error`` (see tools_edit.py's module docstring,
            "Failure shape").
    """
    root = ET.parse(inpx_path).getroot()
    reduced_speed_areas = root.find(".//reducedSpeedAreas")
    speed_distributions = root.find(".//speedDistributions")
    if reduced_speed_areas is None or speed_distributions is None:
        raise RuntimeError(
            f"reparse_reduced_speed_area: reducedSpeedAreas/speedDistributions section "
            f"missing from {inpx_path} after save"
        )

    areas_by_no = {a.get("no"): a for a in reduced_speed_areas.findall("reducedSpeedArea")}
    distributions_by_no = {d.get("no"): d for d in speed_distributions.findall("speedDistribution")}

    if not area_nos:
        raise RuntimeError("reparse_reduced_speed_area: area_nos must be non-empty")

    link_ids: set[str] = set()
    pos_values: list[float] = []
    length_values: list[float] = []
    speed_values: list[float] = []

    for area_no in area_nos:
        area = areas_by_no.get(area_no)
        if area is None:
            raise RuntimeError(
                f"reparse_reduced_speed_area: injected area no={area_no!r} not found in "
                f"{inpx_path} after save"
            )
        lane = area.get("lane", "")
        link_id = lane.split(" ", 1)[0] if lane else ""
        link_ids.add(link_id)
        pos_values.append(round(float(area.get("pos", "nan")), _INJECTED_PRECISION))
        length_values.append(round(float(area.get("length", "nan")), _INJECTED_PRECISION))

        relation = area.find("vehClassSpeedRed/vehClassSpeedReduction")
        if relation is None:
            raise RuntimeError(
                f"reparse_reduced_speed_area: area no={area_no!r} has no vehClassSpeedReduction "
                f"after save"
            )
        distr_no = relation.get("desSpeedDistr")
        distribution = distributions_by_no.get(distr_no)
        if distribution is None:
            raise RuntimeError(
                f"reparse_reduced_speed_area: speedDistribution no={distr_no!r} (referenced by "
                f"area no={area_no!r}) not found in {inpx_path} after save"
            )
        points = distribution.findall("speedDistrDatPts/speedDistributionDataPoint")
        xs = sorted(float(p.get("x")) for p in points if p.get("x") is not None)
        if len(xs) < 2:
            raise RuntimeError(
                f"reparse_reduced_speed_area: speedDistribution no={distr_no!r} has fewer than "
                f"2 data points after save"
            )
        speed_values.append(round((xs[0] + xs[-1]) / 2, _INJECTED_PRECISION))

    return {
        "link_id": link_ids.pop() if len(link_ids) == 1 else sorted(link_ids),
        "speed_kmh": speed_values[0],
        "from_m": pos_values[0],
        "length_m": length_values[0],
        "lane_count": len(area_nos),
    }

"""Tests for services/simulation_network_builder.py.

Two things are exercised here:

- The v2 unit (2) additions -- ``inject_reduced_speed_area`` (write) and
  ``reparse_reduced_speed_area`` (the fresh-from-disk readback that is this
  tool's whole verification story; see ``services.agent.tools_edit.
  set_reduced_speed_area``'s docstring) -- against a small synthetic .inpx
  fixture built by hand (NOT ``data/practice.inpx``: that demo dataset has no
  ``<speedDistributions>``/``<reducedSpeedAreas>`` sections at all -- a real,
  intentionally-tracked gap; see the M2.3 report's drafted DECISIONS.md
  entry). No Vissim COM anywhere in this file.
- A light regression check that generalizing ``_append_speed_distribution``/
  ``_append_reduced_speed_area`` (to let the new functions above reuse them)
  did not change ``_apply_speed_limits``'s pre-existing output for its own
  caller, ``build_simulation_network`` -- the only place these two private
  helpers were used before this unit, with zero prior test coverage.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services.simulation_network_builder import (
    build_simulation_network,
    inject_reduced_speed_area,
    reparse_reduced_speed_area,
)

# A minimal but real-shaped .inpx: <links> (one 2-lane link), plus the two
# sections a real Vissim-exported .inpx always carries (even empty) but the
# synthetic demo dataset does not.
_SYNTHETIC_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network version="2400">
  <links>
    <link no="101" name="Main St EB approach">
      <geometry>
        <linkPolyPts>
          <linkPolyPoint x="0" y="0" zOffset="0"/>
          <linkPolyPoint x="280" y="0" zOffset="0"/>
        </linkPolyPts>
      </geometry>
      <lanes>
        <lane width="3.5"/>
        <lane width="3.5"/>
      </lanes>
    </link>
    <link no="102" name="Single lane">
      <geometry>
        <linkPolyPts>
          <linkPolyPoint x="0" y="0" zOffset="0"/>
          <linkPolyPoint x="100" y="0" zOffset="0"/>
        </linkPolyPts>
      </geometry>
      <lanes>
        <lane width="3.5"/>
      </lanes>
    </link>
  </links>
  <speedDistributions></speedDistributions>
  <reducedSpeedAreas></reducedSpeedAreas>
</network>
"""

_NO_SECTIONS_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network version="2400">
  <links>
    <link no="101" name="Main St EB approach">
      <lanes><lane width="3.5"/></lanes>
    </link>
  </links>
</network>
"""


@pytest.fixture()
def synthetic_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_SYNTHETIC_INPX, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# inject_reduced_speed_area
# ---------------------------------------------------------------------------


def test_inject_creates_one_area_per_lane_sharing_one_distribution(synthetic_inpx: Path):
    injected = inject_reduced_speed_area(
        synthetic_inpx, link_no="101", speed_kmh=40.0, from_m=5.0, length_m=20.0
    )

    assert injected["lane_count"] == 2
    assert len(injected["area_nos"]) == 2
    assert injected["written_value"] == {
        "link_id": "101",
        "speed_kmh": 40.0,
        "from_m": 5.0,
        "length_m": 20.0,
    }

    root = ET.parse(synthetic_inpx).getroot()
    areas = root.findall(".//reducedSpeedAreas/reducedSpeedArea")
    assert len(areas) == 2
    lanes = sorted(a.get("lane") for a in areas)
    assert lanes == ["101 1", "101 2"]
    for area in areas:
        assert area.get("pos") == "5.000000000"
        assert area.get("length") == "20.000000000"
        # Permanent: no simulation-time restriction (PTV's own INT32_MAX
        # sentinel, verified against a real Vissim 2026 example network).
        assert area.get("timeFrom") == "0"
        assert area.get("timeTo") == "2147483647"

    distributions = root.findall(".//speedDistributions/speedDistribution")
    assert len(distributions) == 1
    assert distributions[0].get("no") == injected["distribution_no"]


def test_inject_single_lane_link_creates_one_area(synthetic_inpx: Path):
    injected = inject_reduced_speed_area(
        synthetic_inpx, link_no="102", speed_kmh=30.0, from_m=0.0, length_m=10.0
    )

    assert injected["lane_count"] == 1
    assert len(injected["area_nos"]) == 1


def test_inject_second_call_advances_numbering_and_never_clobbers_the_first(
    synthetic_inpx: Path,
):
    first = inject_reduced_speed_area(
        synthetic_inpx, link_no="101", speed_kmh=40.0, from_m=5.0, length_m=20.0
    )
    second = inject_reduced_speed_area(
        synthetic_inpx, link_no="102", speed_kmh=50.0, from_m=0.0, length_m=10.0
    )

    assert set(first["area_nos"]) & set(second["area_nos"]) == set()
    assert first["distribution_no"] != second["distribution_no"]

    root = ET.parse(synthetic_inpx).getroot()
    assert len(root.findall(".//reducedSpeedAreas/reducedSpeedArea")) == 3  # 2 + 1
    assert len(root.findall(".//speedDistributions/speedDistribution")) == 2


def test_inject_unknown_link_raises_value_error(synthetic_inpx: Path):
    with pytest.raises(ValueError, match="999"):
        inject_reduced_speed_area(
            synthetic_inpx, link_no="999", speed_kmh=40.0, from_m=0.0, length_m=10.0
        )

    # nothing was written on a refused injection
    root = ET.parse(synthetic_inpx).getroot()
    assert root.findall(".//reducedSpeedAreas/reducedSpeedArea") == []


def test_inject_creates_missing_sections_instead_of_raising(tmp_path: Path):
    # Live-verified finding (backend/tools/reduced_speed_area_live_check.py
    # against the real Beijing example): a real Vissim-exported .inpx OMITS
    # a top-level section entirely when the network has never used that
    # feature, rather than exporting an empty container -- an earlier
    # version of this function raised RuntimeError here; see
    # inject_reduced_speed_area's docstring for the full story.
    path = tmp_path / "no_sections.inpx"
    path.write_text(_NO_SECTIONS_INPX, encoding="utf-8")

    injected = inject_reduced_speed_area(
        path, link_no="101", speed_kmh=40.0, from_m=0.0, length_m=10.0
    )

    assert injected["lane_count"] == 1
    root = ET.parse(path).getroot()
    assert root.find("speedDistributions") is not None
    assert root.find("reducedSpeedAreas") is not None
    assert len(root.findall(".//reducedSpeedAreas/reducedSpeedArea")) == 1

    readback = reparse_reduced_speed_area(path, injected["area_nos"])
    assert readback["speed_kmh"] == pytest.approx(40.0, abs=1e-9)


def test_inject_creates_missing_sections_at_the_alphabetically_correct_position(
    tmp_path: Path,
):
    # Mirrors the real Beijing example's shape (verified via
    # `python -c "ET.parse(...).getroot()"` against the live example --
    # see inject_reduced_speed_area's docstring): a network that HAS
    # speedDistributions (from its signal timing) but genuinely has NO
    # reducedSpeedAreas element at all, with siblings correctly sorted on
    # both alphabetical sides of where "reducedSpeedAreas" belongs
    # (links < netPara < reducedSpeedAreas < signalControllers <
    # speedDistributions < vehicleInputs).
    inpx = """<?xml version="1.0" encoding="UTF-8"?>
<network version="2400">
  <links>
    <link no="101" name="X"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <netPara northDir="0"/>
  <signalControllers></signalControllers>
  <speedDistributions></speedDistributions>
  <vehicleInputs></vehicleInputs>
</network>
"""
    path = tmp_path / "beijing_shaped.inpx"
    path.write_text(inpx, encoding="utf-8")

    inject_reduced_speed_area(path, link_no="101", speed_kmh=40.0, from_m=0.0, length_m=10.0)

    root = ET.parse(path).getroot()
    tags = [child.tag for child in root]
    assert tags == sorted(tags)  # still alphabetically sorted after insertion
    assert tags.index("reducedSpeedAreas") == tags.index("netPara") + 1  # right after netPara
    assert tags.index("reducedSpeedAreas") < tags.index("signalControllers")


def test_inject_reuses_an_existing_section_rather_than_creating_a_duplicate(
    synthetic_inpx: Path,
):
    # synthetic_inpx already has BOTH sections (as empty containers) --
    # confirm no second <reducedSpeedAreas>/<speedDistributions> is ever
    # created alongside the real one.
    inject_reduced_speed_area(
        synthetic_inpx, link_no="101", speed_kmh=40.0, from_m=0.0, length_m=10.0
    )

    root = ET.parse(synthetic_inpx).getroot()
    assert len(root.findall("reducedSpeedAreas")) == 1
    assert len(root.findall("speedDistributions")) == 1


def test_inject_writes_in_place_no_stray_temp_file_left_behind(synthetic_inpx: Path):
    inject_reduced_speed_area(
        synthetic_inpx, link_no="101", speed_kmh=40.0, from_m=5.0, length_m=20.0
    )

    siblings = list(synthetic_inpx.parent.iterdir())
    assert siblings == [synthetic_inpx]  # the "_rsa_saving" temp file was replaced away


# ---------------------------------------------------------------------------
# reparse_reduced_speed_area — the fresh-from-disk readback
# ---------------------------------------------------------------------------


def test_reparse_matches_injected_values_within_tolerance(synthetic_inpx: Path):
    injected = inject_reduced_speed_area(
        synthetic_inpx, link_no="101", speed_kmh=45.37, from_m=12.5, length_m=30.25
    )

    readback = reparse_reduced_speed_area(synthetic_inpx, injected["area_nos"])

    assert readback["link_id"] == "101"
    assert readback["speed_kmh"] == pytest.approx(45.37, abs=1e-9)
    assert readback["from_m"] == pytest.approx(12.5, abs=1e-9)
    assert readback["length_m"] == pytest.approx(30.25, abs=1e-9)
    assert readback["lane_count"] == 2


def test_reparse_is_a_genuinely_fresh_read_not_the_in_memory_tree(synthetic_inpx: Path):
    # Mutate the file on disk AFTER injection (simulating some other process
    # having touched it) and confirm reparse reflects the NEW disk content,
    # not anything cached from the write step.
    injected = inject_reduced_speed_area(
        synthetic_inpx, link_no="101", speed_kmh=40.0, from_m=5.0, length_m=20.0
    )
    text = synthetic_inpx.read_text(encoding="utf-8")
    text = text.replace('pos="5.000000000"', 'pos="9.000000000"')
    synthetic_inpx.write_text(text, encoding="utf-8")

    readback = reparse_reduced_speed_area(synthetic_inpx, injected["area_nos"])

    assert readback["from_m"] == pytest.approx(9.0, abs=1e-9)


def test_reparse_missing_area_raises_runtime_error(synthetic_inpx: Path):
    injected = inject_reduced_speed_area(
        synthetic_inpx, link_no="101", speed_kmh=40.0, from_m=5.0, length_m=20.0
    )
    # Corrupt the file: delete one of the injected areas, simulating a write
    # that did not fully reach disk.
    text = synthetic_inpx.read_text(encoding="utf-8")
    tree = ET.ElementTree(ET.fromstring(text))
    root = tree.getroot()
    areas = root.find(".//reducedSpeedAreas")
    victim = areas.findall("reducedSpeedArea")[0]
    areas.remove(victim)
    tree.write(synthetic_inpx, encoding="UTF-8", xml_declaration=True)

    with pytest.raises(RuntimeError, match="not found"):
        reparse_reduced_speed_area(synthetic_inpx, injected["area_nos"])


def test_reparse_empty_area_nos_raises_runtime_error(synthetic_inpx: Path):
    with pytest.raises(RuntimeError, match="non-empty"):
        reparse_reduced_speed_area(synthetic_inpx, [])


# ---------------------------------------------------------------------------
# Regression: generalizing the shared builders did not change
# build_simulation_network's own (pre-existing, previously untested) output.
# ---------------------------------------------------------------------------


def test_build_simulation_network_speed_limits_output_unchanged_by_the_refactor(
    tmp_path: Path,
):
    # build_simulation_network writes under <repo>/runtime/simulations/<job_id>/
    # (services/simulation_network_builder.py's own path convention, resolved
    # relative to the module file, not the cwd -- there is no existing
    # SIMULATIONS_ROOT-style module constant to monkeypatch, unlike
    # simulation_runs.py's SIMULATION_ROOT). A distinctive, throwaway job_id
    # plus explicit cleanup keeps this test from leaving debris behind
    # instead of reaching for a path-construction monkeypatch.
    import shutil

    source = tmp_path / "source.inpx"
    source.write_text(_SYNTHETIC_INPX, encoding="utf-8")
    job_id = "test-shared-builder-regression"
    # <repo_root>/runtime/simulations/<job_id> -- three parents up from THIS
    # test file (backend/tests/...py -> backend/tests -> backend -> repo
    # root), matching simulation_network_builder.py's own
    # Path(__file__).resolve().parent.parent.parent from backend/services/....
    output_dir = Path(__file__).resolve().parents[2] / "runtime" / "simulations" / job_id
    shutil.rmtree(output_dir, ignore_errors=True)

    try:
        output_path = build_simulation_network(
            source_inpx=source,
            job_id=job_id,
            scenario_id=None,
            speed_limits=[{"link_id": "101", "speed_limit": 40, "duration": 90}],
        )
        root = ET.parse(output_path).getroot()
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    areas = root.findall(".//reducedSpeedAreas/reducedSpeedArea")
    assert len(areas) == 2  # link 101 has 2 lanes
    for area in areas:
        # pos is now "0.000000" (formatted) instead of the old literal "0"
        # string -- numerically identical (Vissim parses it as a float), the
        # one deliberate, harmless string-shape change from generalizing the
        # helper's pos parameter (see _append_reduced_speed_area's docstring).
        assert float(area.get("pos")) == 0.0
        assert float(area.get("length")) == pytest.approx(280.0, abs=1e-6)
        assert area.get("timeFrom") == "0"
        assert area.get("timeTo") == "90"
        assert area.get("name") == "interactive_speed_limit_link_101"

    distributions = root.findall(".//speedDistributions/speedDistribution")
    assert len(distributions) == 1
    assert distributions[0].get("name") == "Interactive 40 km/h"
    points = distributions[0].findall(".//speedDistributionDataPoint")
    xs = sorted(float(p.get("x")) for p in points)
    assert xs == pytest.approx([39.9, 40.1], abs=1e-9)  # unchanged .1f/0.1-margin behavior

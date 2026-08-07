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
- Phase 0.1's ``scale_vehicle_input_volumes`` (the demand-stress sweep's
  isolation primitive, ``services.agent.tools_sweep``) -- pure ElementTree,
  no Vissim COM here either.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services.simulation_network_builder import (
    build_simulation_network,
    inject_reduced_speed_area,
    reparse_reduced_speed_area,
    scale_vehicle_input_volumes,
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


# ---------------------------------------------------------------------------
# scale_vehicle_input_volumes -- Phase 0.1's demand-stress sweep isolation
# primitive (services.agent.tools_sweep.run_demand_stress_sweep)
# ---------------------------------------------------------------------------

_VEHICLE_INPUTS_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network version="2400">
  <links>
    <link no="101" name="L1"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleInputs>
    <vehicleInput no="1" name="VI1" link="101">
      <timeIntVehVols>
        <timeIntervalVehVolume timeInt="1 0" volume="1000" cont="false" vehComp="1"/>
      </timeIntVehVols>
    </vehicleInput>
    <vehicleInput no="2" name="VI2" link="101">
      <timeIntVehVols>
        <timeIntervalVehVolume timeInt="1 0" volume="500" cont="true" vehComp="1"/>
        <timeIntervalVehVolume timeInt="1 1200000" volume="800" cont="true" vehComp="1"/>
        <timeIntervalVehVolume timeInt="1 1800000" volume="200" cont="false" vehComp="1"/>
      </timeIntVehVols>
    </vehicleInput>
    <vehicleInput no="3" name="VI3_no_volume" link="101"/>
  </vehicleInputs>
</network>
"""


@pytest.fixture()
def vehicle_inputs_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "source.inpx"
    path.write_text(_VEHICLE_INPUTS_INPX, encoding="utf-8")
    return path


def _volumes_by_input(root: ET.Element) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for vi in root.findall(".//vehicleInput"):
        result[vi.get("no")] = [
            float(v.get("volume")) for v in vi.findall(".//timeIntervalVehVolume")
        ]
    return result


def test_scale_never_touches_source_inpx(vehicle_inputs_inpx: Path, tmp_path: Path):
    before_bytes = vehicle_inputs_inpx.read_bytes()
    output_path = tmp_path / "scaled.inpx"

    scale_vehicle_input_volumes(vehicle_inputs_inpx, output_path, scale_percent=10.0)

    assert vehicle_inputs_inpx.read_bytes() == before_bytes
    assert output_path.exists()
    assert output_path != vehicle_inputs_inpx


def test_scale_all_inputs_multiplies_every_interval(vehicle_inputs_inpx: Path, tmp_path: Path):
    output_path = tmp_path / "scaled.inpx"
    summary = scale_vehicle_input_volumes(vehicle_inputs_inpx, output_path, scale_percent=10.0)

    assert summary["factor"] == pytest.approx(1.10)
    assert sorted(summary["scaled_input_nos"]) == ["1", "2"]
    assert summary["empty_input_nos"] == ["3"]
    assert summary["unmatched_input_nos"] == []
    assert summary["base_total_veh_h"] == pytest.approx(1000 + 500 + 800 + 200)
    assert summary["scaled_total_veh_h"] == pytest.approx((1000 + 500 + 800 + 200) * 1.10)

    root = ET.parse(output_path).getroot()
    volumes = _volumes_by_input(root)
    assert volumes["1"] == pytest.approx([1100.0])
    # ALL intervals of a multi-interval input scale together.
    assert volumes["2"] == pytest.approx([550.0, 880.0, 220.0])
    assert volumes["3"] == []  # nothing to scale, nothing invented


def test_scale_negative_percent_shrinks_volumes(vehicle_inputs_inpx: Path, tmp_path: Path):
    output_path = tmp_path / "scaled.inpx"
    summary = scale_vehicle_input_volumes(vehicle_inputs_inpx, output_path, scale_percent=-25.0)

    assert summary["factor"] == pytest.approx(0.75)
    root = ET.parse(output_path).getroot()
    volumes = _volumes_by_input(root)
    assert volumes["1"] == pytest.approx([750.0])


def test_scale_percent_zero_is_a_no_op_on_values(vehicle_inputs_inpx: Path, tmp_path: Path):
    output_path = tmp_path / "scaled.inpx"
    scale_vehicle_input_volumes(vehicle_inputs_inpx, output_path, scale_percent=0.0)

    root = ET.parse(output_path).getroot()
    volumes = _volumes_by_input(root)
    assert volumes["1"] == pytest.approx([1000.0])
    assert volumes["2"] == pytest.approx([500.0, 800.0, 200.0])


def test_scale_minus_100_percent_zeroes_out_demand(vehicle_inputs_inpx: Path, tmp_path: Path):
    output_path = tmp_path / "scaled.inpx"
    scale_vehicle_input_volumes(vehicle_inputs_inpx, output_path, scale_percent=-100.0)

    root = ET.parse(output_path).getroot()
    volumes = _volumes_by_input(root)
    assert volumes["1"] == pytest.approx([0.0])
    assert volumes["2"] == pytest.approx([0.0, 0.0, 0.0])


def test_scale_input_nos_filter_leaves_other_inputs_untouched(
    vehicle_inputs_inpx: Path, tmp_path: Path
):
    output_path = tmp_path / "scaled.inpx"
    summary = scale_vehicle_input_volumes(
        vehicle_inputs_inpx, output_path, scale_percent=10.0, input_nos=["1"]
    )

    assert summary["scaled_input_nos"] == ["1"]
    assert summary["empty_input_nos"] == []  # "3" was never matched, not "empty"
    assert summary["unmatched_input_nos"] == []

    root = ET.parse(output_path).getroot()
    volumes = _volumes_by_input(root)
    assert volumes["1"] == pytest.approx([1100.0])
    assert volumes["2"] == pytest.approx([500.0, 800.0, 200.0])  # untouched


def test_scale_input_nos_naming_a_nonexistent_input_is_reported_unmatched(
    vehicle_inputs_inpx: Path, tmp_path: Path
):
    output_path = tmp_path / "scaled.inpx"
    summary = scale_vehicle_input_volumes(
        vehicle_inputs_inpx, output_path, scale_percent=10.0, input_nos=["1", "999"]
    )

    assert summary["scaled_input_nos"] == ["1"]
    assert summary["unmatched_input_nos"] == ["999"]


def test_scale_input_nos_matching_only_the_empty_input_reports_it_as_empty_not_unmatched(
    vehicle_inputs_inpx: Path, tmp_path: Path
):
    output_path = tmp_path / "scaled.inpx"
    summary = scale_vehicle_input_volumes(
        vehicle_inputs_inpx, output_path, scale_percent=10.0, input_nos=["3"]
    )

    assert summary["scaled_input_nos"] == []
    assert summary["empty_input_nos"] == ["3"]
    assert summary["unmatched_input_nos"] == []


def test_scale_output_path_parent_created_if_missing(vehicle_inputs_inpx: Path, tmp_path: Path):
    output_path = tmp_path / "nested" / "dir" / "scaled.inpx"
    scale_vehicle_input_volumes(vehicle_inputs_inpx, output_path, scale_percent=5.0)
    assert output_path.exists()


# ---------------------------------------------------------------------------
# _make_network_standalone -- the EXEMPLAR, pinned unchanged.
#
# This is the interactive run path's normalization, and it is the reference
# services/agent/state.py's clone-time normalizations mirror (underScenMngm
# 2026-07-31, numRuns 2026-08-07 -- the latter after a live run of the real
# consultant model produced five result databases for one requested seed).
# The agent-path fix must not have moved anything here: these assertions
# exist so a future edit that "unifies" the two paths has to say so out loud.
#
# Called directly on an in-memory tree: build_simulation_network writes into
# the repo's real runtime/ tree, and none of this needs a file to exist.
# ---------------------------------------------------------------------------

_STANDALONE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network version="2400">
  <netPara northDir="0" underScenMngm="true"/>
  <simulation numRuns="5" randSeed="42" randSeedIncr="123" simPeriod="5400" simRes="10"/>
  <evaluation evalOutDir="C:\\somewhere\\else" keepPrevResults="KEEPALL"/>
  <links><link no="101"><lanes><lane width="3.5"/></lanes></link></links>
</network>
"""


def test_make_network_standalone_still_pins_num_runs_and_the_rest(tmp_path: Path):
    from services.simulation_network_builder import _make_network_standalone

    root = ET.fromstring(_STANDALONE_INPX)
    results_dir = tmp_path / "results"

    _make_network_standalone(root, results_dir)

    simulation = root.find(".//simulation")
    # The line this whole fix is about: the interactive path has always
    # produced a single result set, and still does.
    assert simulation.get("numRuns") == "1"
    assert simulation.get("simRes") == "2"
    assert simulation.get("useMaxSimSpeed") == "true"
    # Untouched simulation parameters stay untouched.
    assert simulation.get("randSeed") == "42"
    assert simulation.get("randSeedIncr") == "123"
    assert simulation.get("simPeriod") == "5400"

    assert root.find(".//netPara").get("underScenMngm") == "false"
    evaluation = root.find(".//evaluation")
    assert evaluation.get("evalOutDir") == str(results_dir)
    assert evaluation.get("keepPrevResults") == "DELETEALL"


def test_make_network_standalone_tolerates_a_network_without_a_simulation_element(
    tmp_path: Path,
):
    """Pre-existing behaviour, pinned: the sections are optional and their
    absence is a no-op, never an AttributeError."""
    from services.simulation_network_builder import _make_network_standalone

    root = ET.fromstring(_NO_SECTIONS_INPX)

    _make_network_standalone(root, tmp_path / "results")

    assert root.find(".//simulation") is None
    assert root.find(".//netPara") is None
    assert root.find(".//evaluation") is None

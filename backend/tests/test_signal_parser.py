"""Tests for signal_parser.py's controller_type extraction (v2 unit ①).

Empirically verified against all 25 real PTV Vissim 2026 "Examples Demo"
networks (2026-07-24, see the drafted DECISIONS.md entry in the task report):
``<signalController>``'s own ``type`` attribute carries the controller family
verbatim -- FIXEDTIME/FIXEDTIMESIMPLE/VAP/RINGBARRIERCONTROLLER/
RAILWAYCROSSING/PEDESTRIANCROSSING/TWOSTAGECONTROLLER. ``intSupplyData`` is
populated ONLY on FIXEDTIME controllers in every real network sampled; every
other real type carries ``type`` but no ``intSupplyData`` at all, so
``_parse_one_sc`` returns a type-only, timing-blind ``SignalInfo`` for those
rather than dropping them (which would make FIXEDTIMESIMPLE structurally
invisible to the shadow model and defeat the M2.1 CycTm finding that its
cycle/offset ARE COM-writable). The synthetic demo .inpx's bare
``<signalController no="1" name="..."/>`` (no type, no intSupplyData) must
stay invisible -- pinned by test_agent_read_tools.py's empty-shadow-signals
contract, which this file does not touch.
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services.signal_parser import parse_signal_controllers

# ---------------------------------------------------------------------------
# Synthetic XML fixtures
# ---------------------------------------------------------------------------

_INNER_SC_XML = '<sc version="1" id="1" name=""><sgs><signalGroup no="1"/></sgs></sc>'
_VALID_ISD = base64.b64encode(_INNER_SC_XML.encode("utf-8")).decode("ascii")


def _network_xml(signal_controller_tags: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<network>"
        f"<signalControllers>{signal_controller_tags}</signalControllers>"
        "</network>"
    )


def _write_inpx(tmp_path: Path, xml_text: str) -> Path:
    path = tmp_path / "net.inpx"
    path.write_text(xml_text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# controller_type extraction — synthetic
# ---------------------------------------------------------------------------


def test_controller_type_extracted_when_type_and_intsupplydata_both_present(tmp_path: Path):
    xml_text = _network_xml(
        f'<signalController no="1" name="SC1" type="FIXEDTIME" intSupplyData="{_VALID_ISD}"/>'
    )
    inpx = _write_inpx(tmp_path, xml_text)

    signals = parse_signal_controllers(inpx)

    assert "1" in signals
    assert signals["1"]["controller_type"] == "FIXEDTIME"


def test_controller_type_extracted_for_type_present_without_intsupplydata(tmp_path: Path):
    """FIXEDTIMESIMPLE (and every other real non-FIXEDTIME type observed) never
    carries intSupplyData in real Vissim data -- must still surface as a
    type-only entry, not be dropped."""
    xml_text = _network_xml('<signalController no="11" name="Terminal entrance door" type="FIXEDTIMESIMPLE"/>')
    inpx = _write_inpx(tmp_path, xml_text)

    signals = parse_signal_controllers(inpx)

    assert "11" in signals
    info = signals["11"]
    assert info["controller_type"] == "FIXEDTIMESIMPLE"
    assert info["cycle_time"] == 0
    assert info["phases"] == []
    assert info["num_signal_groups"] == 0


def test_controller_type_extracted_for_non_com_writable_real_type(tmp_path: Path):
    xml_text = _network_xml('<signalController no="12" name="Speed gate West" type="RAILWAYCROSSING"/>')
    inpx = _write_inpx(tmp_path, xml_text)

    signals = parse_signal_controllers(inpx)

    assert signals["12"]["controller_type"] == "RAILWAYCROSSING"


def test_controller_type_unknown_when_type_attribute_blank(tmp_path: Path):
    xml_text = _network_xml(f'<signalController no="1" name="SC1" type="" intSupplyData="{_VALID_ISD}"/>')
    inpx = _write_inpx(tmp_path, xml_text)

    signals = parse_signal_controllers(inpx)

    assert signals["1"]["controller_type"] == "unknown"


def test_signal_controller_invisible_when_both_type_and_intsupplydata_absent(tmp_path: Path):
    """Matches make_demo_data.py's synthetic <signalController no="1" name="..."/>
    shape exactly -- must stay invisible (test_agent_read_tools.py pins the
    resulting empty shadow-model 'signals' list for the demo dataset)."""
    xml_text = _network_xml('<signalController no="1" name="SC1 Central &amp; Main"/>')
    inpx = _write_inpx(tmp_path, xml_text)

    signals = parse_signal_controllers(inpx)

    assert signals == {}


def test_controller_type_never_guessed_from_name_or_other_attributes(tmp_path: Path):
    """A signal controller with a name that LOOKS like a type must not leak
    that into controller_type -- only the real "type" attribute counts."""
    xml_text = _network_xml('<signalController no="1" name="FIXEDTIME lookalike"/>')
    inpx = _write_inpx(tmp_path, xml_text)

    signals = parse_signal_controllers(inpx)

    assert signals == {}  # no type, no intSupplyData -> invisible, not "guessed"


# ---------------------------------------------------------------------------
# controller_type extraction — real PTV Vissim 2026 example networks
# (skipped when the runtime/com-smoke corpus isn't present on this machine —
# CLAUDE.md: Windows dev box + Mac laptop both used, keep scripts portable)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BEIJING_INPX = (
    _REPO_ROOT / "runtime" / "com-smoke" / "Urban Intersection Beijing.CN_31360" / "Intersection Beijing.inpx"
)
_AIR_TAXI_INPX = (
    _REPO_ROOT / "runtime" / "com-smoke" / "Air Taxi Vertiport_42580" / "Air Taxi Vertiport.inpx"
)

_requires_beijing = pytest.mark.skipif(
    not _BEIJING_INPX.is_file(), reason=f"real example network not present: {_BEIJING_INPX}"
)
_requires_air_taxi = pytest.mark.skipif(
    not _AIR_TAXI_INPX.is_file(), reason=f"real example network not present: {_AIR_TAXI_INPX}"
)


@_requires_beijing
def test_real_beijing_network_sc1_is_fixedtime():
    signals = parse_signal_controllers(_BEIJING_INPX)

    assert signals["1"]["controller_type"] == "FIXEDTIME"
    # Beijing SC1 is program-driven (M2.1 CycTm finding) but still parses a
    # real cycle from the opaque intSupplyData blob.
    assert signals["1"]["cycle_time"] > 0


@_requires_air_taxi
def test_real_air_taxi_network_has_fixedtimesimple_and_railwaycrossing():
    signals = parse_signal_controllers(_AIR_TAXI_INPX)

    types = {sc_no: info["controller_type"] for sc_no, info in signals.items()}
    # SC 11/14/21 are FIXEDTIMESIMPLE (COM-writable per M2.1); 12/13 are
    # RAILWAYCROSSING (not COM-writable) -- all five are still VISIBLE with
    # their real type, none silently dropped.
    assert types["11"] == "FIXEDTIMESIMPLE"
    assert types["14"] == "FIXEDTIMESIMPLE"
    assert types["21"] == "FIXEDTIMESIMPLE"
    assert types["12"] == "RAILWAYCROSSING"
    assert types["13"] == "RAILWAYCROSSING"


@_requires_air_taxi
def test_real_air_taxi_fixedtimesimple_controllers_have_no_com_readable_cycle():
    """Per the M2.1 DECISIONS.md finding: these controllers carry no
    intSupplyData, so cycle_time is the 0 sentinel -- genuinely parser-blind,
    not a bug in this extraction."""
    signals = parse_signal_controllers(_AIR_TAXI_INPX)

    assert signals["11"]["cycle_time"] == 0
    assert signals["11"]["phases"] == []



# ---------------------------------------------------------------------------
# parse_signal_programs (v2a unit 9) -- synthetic fixtures mirroring the two
# real storage shapes verified live by backend/tools/probe_signal_programs.py:
# FIXEDTIME (Vissig program embedded in the base64 intSupplyData blob, cmd
# display 3 = green-on / 1 = red-on, begins in ms, progNo selects the active
# program) and FIXEDTIMESIMPLE (plain endRed/endGreen signalGroup attributes).
# ---------------------------------------------------------------------------

from services.signal_parser import parse_signal_programs  # noqa: E402

_PROGRAM_BLOB_XML = (
    '<sc version="202001" id="1" name="" checkSum="344017207">'
    "<sgs>"
    '<sg id="1" name="01" defaultSignalSequence="3"/>'
    '<sg id="2" name="02" defaultSignalSequence="3"/>'
    "</sgs>"
    "<intergreenmatrices/>"
    "<progs>"
    '<prog id="1" cycletime="130000" offset="0" name="Signal program">'
    "<sgs>"
    '<sg sg_id="1" signal_sequence="3">'
    '<cmds><cmd display="1" begin="15000"/><cmd display="3" begin="130000"/></cmds>'
    '<fixedstates><fixedstate display="2" duration="1000"/>'
    '<fixedstate display="4" duration="3000"/></fixedstates>'
    "</sg>"
    '<sg sg_id="2" signal_sequence="3">'
    '<cmds><cmd display="3" begin="88000"/><cmd display="1" begin="130000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="3000"/></fixedstates>'
    "</sg>"
    "</sgs>"
    "</prog>"
    '<prog id="2" cycletime="130000" offset="0" name="Test">'
    "<sgs>"
    '<sg sg_id="1" signal_sequence="3">'
    '<cmds><cmd display="3" begin="1000"/><cmd display="1" begin="15000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="3000"/></fixedstates>'
    "</sg>"
    "</sgs>"
    "</prog>"
    "</progs>"
    "</sc>"
)
_PROGRAM_ISD = base64.b64encode(_PROGRAM_BLOB_XML.encode("utf-8")).decode("ascii")


def _program_network_xml() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<network>"
        "<signalControllers>"
        f'<signalController no="1" name="Main" type="FIXEDTIME" progNo="2" intSupplyData="{_PROGRAM_ISD}">'
        '<sgs><signalGroup no="1" name="North"/><signalGroup no="2" name="East"/></sgs>'
        "</signalController>"
        '<signalController no="2" name="Gate" type="FIXEDTIMESIMPLE" progNo="1" cycTm="999">'
        '<sgs><signalGroup no="1" name="" amber="0" redAmber="0" endGreen="0" endRed="998" minGreen="5"/></sgs>'
        "</signalController>"
        '<signalController no="3" name="Ext" type="VAP" progNo="1"/>'
        "</signalControllers>"
        '<signalHeads>'
        '<signalHead no="1" sg="1 1" lane="10 1"/>'
        '<signalHead no="2" sg="1 1" lane="10 2"/>'
        '<signalHead no="3" sg="1 2" lane="11 1"/>'
        "</signalHeads>"
        "</network>"
    )


def test_parse_signal_programs_fixedtime_blob(tmp_path: Path):
    inpx = _write_inpx(tmp_path, _program_network_xml())

    controllers = {c["sc_no"]: c for c in parse_signal_programs(inpx)}

    sc1 = controllers["1"]
    assert sc1["controller_type"] == "FIXEDTIME"
    assert sc1["active_program_no"] == "2"
    progs = {p["prog_no"]: p for p in sc1["programs"]}
    assert set(progs) == {"1", "2"}
    assert progs["1"]["cycle_s"] == 130.0

    # Program 1 SG 1 WRAPS: green-on at 130 s, red-on at 15 s.
    p1_splits = {s["sg_no"]: s for s in progs["1"]["sg_splits"]}
    assert p1_splits["1"]["green_start_s"] == 130.0
    assert p1_splits["1"]["green_end_s"] == 15.0
    assert p1_splits["1"]["amber_s"] == 3.0
    assert p1_splits["1"]["red_amber_s"] == 1.0
    assert p1_splits["1"]["simple"] is True
    # SG names come from the OUTER <signalGroup name="..."> map.
    assert p1_splits["1"]["sg_name"] == "North"
    assert p1_splits["1"]["head_count"] == 2
    assert p1_splits["2"]["green_start_s"] == 88.0
    assert p1_splits["2"]["head_count"] == 1
    # min-green is never claimed for blob programs (unproven datum).
    assert p1_splits["1"]["min_green_s"] is None

    # Program 2 (the ACTIVE one) SG 1: green 1..15 s.
    p2_splits = {s["sg_no"]: s for s in progs["2"]["sg_splits"]}
    assert p2_splits["1"]["green_start_s"] == 1.0
    assert p2_splits["1"]["green_end_s"] == 15.0


def test_parse_signal_programs_fixedtimesimple_plain_attributes(tmp_path: Path):
    inpx = _write_inpx(tmp_path, _program_network_xml())

    controllers = {c["sc_no"]: c for c in parse_signal_programs(inpx)}

    sc2 = controllers["2"]
    assert sc2["controller_type"] == "FIXEDTIMESIMPLE"
    assert len(sc2["programs"]) == 1
    prog = sc2["programs"][0]
    assert prog["prog_no"] == "1"
    assert prog["cycle_s"] == 999.0
    split = prog["sg_splits"][0]
    # endRed = green start (end of red), endGreen = green end.
    assert split["green_start_s"] == 998.0
    assert split["green_end_s"] == 0.0
    assert split["min_green_s"] == 5.0
    assert split["simple"] is True


def test_parse_signal_programs_external_controller_reports_empty_programs(tmp_path: Path):
    inpx = _write_inpx(tmp_path, _program_network_xml())

    controllers = {c["sc_no"]: c for c in parse_signal_programs(inpx)}

    assert controllers["3"]["controller_type"] == "VAP"
    assert controllers["3"]["programs"] == []


def test_parse_signal_programs_non_simple_cmd_shape_flagged_not_guessed(tmp_path: Path):
    # TWO green windows for one SG -- must surface simple=False with None
    # edges, never a guessed single window.
    blob = (
        '<sc id="9"><progs><prog id="1" cycletime="60000">'
        '<sgs><sg sg_id="1">'
        '<cmds><cmd display="3" begin="0"/><cmd display="1" begin="10000"/>'
        '<cmd display="3" begin="30000"/><cmd display="1" begin="40000"/></cmds>'
        "</sg></sgs></prog></progs></sc>"
    )
    isd = base64.b64encode(blob.encode("utf-8")).decode("ascii")
    xml_text = _network_xml(
        f'<signalController no="9" type="FIXEDTIME" progNo="1" intSupplyData="{isd}"/>'
    )
    inpx = _write_inpx(tmp_path, xml_text)

    controllers = {c["sc_no"]: c for c in parse_signal_programs(inpx)}
    split = controllers["9"]["programs"][0]["sg_splits"][0]
    assert split["simple"] is False
    assert split["green_start_s"] is None
    assert split["green_end_s"] is None


def test_parse_signal_programs_undecodable_blob_reports_empty_programs(tmp_path: Path):
    xml_text = _network_xml(
        '<signalController no="7" type="FIXEDTIME" progNo="1" intSupplyData="!!!notbase64!!!"/>'
    )
    inpx = _write_inpx(tmp_path, xml_text)

    controllers = {c["sc_no"]: c for c in parse_signal_programs(inpx)}
    assert controllers["7"]["programs"] == []


# ---------------------------------------------------------------------------
# STAGE-BASED programs — the Murdoch Drive (Perth) production-model finding
# (2026-07-28). The structures below are ANONYMISED but structurally
# identical to the client's `SouthStreet_Base.inpx`: SC 6 "TCS398_MurdochDr"
# (8 stage programs, 6 stages, 8 interstage programs) and SC 9 "TCS1111"
# (the degenerate 375 s stub). Real ids, real cycle times, real interstage
# instants, generic names — the client's network itself is NOT committed.
#
# The landmine these pin: `<stageProg><interstage display="N">` names the
# INTERSTAGE PROGRAM N, not stage N. Reading it as a stage id mis-attributed
# 3 of SC 6's 5 phases to the wrong signal groups, dropped a stage entirely,
# and produced a phase with NO signal groups at all on a sibling controller
# (display="6" against a 5-stage controller).
# ---------------------------------------------------------------------------

from services.signal_parser import (  # noqa: E402
    CONTROLLER_NO_SIGNAL_HEADS,
    DQ_CYCLE_ABOVE_TYPICAL_MAX,
    DQ_NEAR_PERMANENT_GREEN,
    DQ_SINGLE_SIGNAL_GROUP,
    PROGRAMS_BLOB_EMPTY,
    SG_SPLITS_STAGE_BASED,
)

#: SC 6's real stage set: A=SG1,2  D=SG7,8  E=SG5,6  G=SG3,4  G1=SG1,3
#: D2=SG6,8 — matching the project engineer's own AM workbook phase ->
#: signal-group table exactly (A:1,2 / D:7,8 / E:5,6 / G:3,4).
_STAGE_BLOB_STAGES = (
    "<stages>"
    '<stage id="1" name="A"><activations>'
    '<activation sg_id="1" activation="ON"/><activation sg_id="2" activation="ON"/>'
    '<activation sg_id="3" activation="OFF"/><activation sg_id="4" activation="OFF"/>'
    '<activation sg_id="5" activation="OFF"/><activation sg_id="6" activation="OFF"/>'
    '<activation sg_id="7" activation="OFF"/><activation sg_id="8" activation="OFF"/>'
    "</activations></stage>"
    '<stage id="2" name="D"><activations>'
    '<activation sg_id="7" activation="ON"/><activation sg_id="8" activation="ON"/>'
    "</activations></stage>"
    '<stage id="3" name="E"><activations>'
    '<activation sg_id="5" activation="ON"/><activation sg_id="6" activation="ON"/>'
    "</activations></stage>"
    '<stage id="4" name="G"><activations>'
    '<activation sg_id="3" activation="ON"/><activation sg_id="4" activation="ON"/>'
    "</activations></stage>"
    '<stage id="5" name="G1"><activations>'
    '<activation sg_id="1" activation="ON"/><activation sg_id="3" activation="ON"/>'
    "</activations></stage>"
    '<stage id="6" name="D2"><activations>'
    '<activation sg_id="6" activation="ON"/><activation sg_id="8" activation="ON"/>'
    "</activations></stage>"
    "</stages>"
)

#: The real interstage table. Note ids 5..8: their NUMBERS collide with stage
#: ids 5/6 while meaning something completely different, which is exactly why
#: the old stage-id reading looked plausible on the first few phases.
_STAGE_BLOB_INTERSTAGES = (
    "<interstageProgs>"
    '<interstageProg id="1" cycletime="14000" fromStage="1" toStage="2"/>'
    '<interstageProg id="2" cycletime="16000" fromStage="2" toStage="3"/>'
    '<interstageProg id="3" cycletime="14000" fromStage="3" toStage="4"/>'
    '<interstageProg id="4" cycletime="16000" fromStage="4" toStage="1"/>'
    '<interstageProg id="5" cycletime="16000" fromStage="4" toStage="5"/>'
    '<interstageProg id="6" cycletime="16000" fromStage="5" toStage="1"/>'
    '<interstageProg id="7" cycletime="16000" fromStage="2" toStage="6"/>'
    '<interstageProg id="8" cycletime="16000" fromStage="6" toStage="3"/>'
    "</interstageProgs>"
)

#: Two of the eight real stage programs: id 11 (ACTIVE, progNo="11") and 12.
#: Their instants are verbatim from the production model.
_STAGE_BLOB_PROGS = (
    "<stageProgs>"
    '<stageProg id="11" cycletime="131000" offset="0" name="BaseAM"><interstages>'
    '<interstage display="1" begin="37000"/><interstage display="2" begin="57000"/>'
    '<interstage display="3" begin="92000"/><interstage display="5" begin="121000"/>'
    '<interstage display="6" begin="127000"/>'
    "</interstages></stageProg>"
    '<stageProg id="12" cycletime="143000" offset="0" name="BasePM"><interstages>'
    '<interstage display="1" begin="38000"/><interstage display="7" begin="61000"/>'
    '<interstage display="8" begin="72000"/><interstage display="3" begin="104000"/>'
    '<interstage display="5" begin="130000"/><interstage display="6" begin="134000"/>'
    "</interstages></stageProg>"
    "</stageProgs>"
)

_STAGE_BLOB = (
    '<sc version="202001" id="6" name="" interstagesUsingMinDurations="true" '
    'checkSum="466269441">'
    "<sgs>"
    '<sg id="1" name="SG1"/><sg id="2" name="SG2"/><sg id="3" name="SG3"/>'
    '<sg id="4" name="SG4"/><sg id="5" name="SG5"/><sg id="6" name="SG6"/>'
    '<sg id="7" name="SG7"/><sg id="8" name="SG8"/>'
    "</sgs>"
    "<progs/>"  # EMPTY on every stage-based controller -- the silent blank
    + _STAGE_BLOB_STAGES
    + _STAGE_BLOB_INTERSTAGES
    + _STAGE_BLOB_PROGS
    + "</sc>"
)

#: The controller's OUTER <signalGroup> list. The min-green values are
#: verbatim from the model and match the engineer's workbook per phase
#: (A=8, D=6, E=8, G=6); endRed/endGreen are all zero on a stage-based
#: controller, which is why the FIXEDTIMESIMPLE reader cannot help here.
_STAGE_OUTER_SGS = "".join(
    '<signalGroup no="%d" name="SG%d" amber="5" redAmber="0" minGreen="%d" '
    'endRed="0" endGreen="0"/>' % (no, no, mg)
    for no, mg in ((1, 8), (2, 8), (3, 6), (4, 6), (5, 8), (6, 8), (7, 6), (8, 6))
)


def _stage_network_xml(*, prog_no: str = "11", heads: bool = True) -> str:
    isd = base64.b64encode(_STAGE_BLOB.encode("utf-8")).decode("ascii")
    head_tags = ""
    if heads:
        head_tags = (
            "<signalHeads>"
            + "".join(
                '<signalHead no="%d" sg="6 %d" lane="10 1"/>' % (600 + n, n)
                for n in range(1, 9)
            )
            + "</signalHeads>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?><network>'
        "<signalControllers>"
        '<signalController no="6" name="SC-Alpha" type="FIXEDTIME" progNo="%s" '
        'cycTm="0" offset="0" progFile="" supplyFile1="" intSupplyData="%s">'
        "<sgs>%s</sgs>"
        "</signalController>"
        "</signalControllers>%s</network>" % (prog_no, isd, _STAGE_OUTER_SGS, head_tags)
    )


def test_stage_based_controller_reports_every_stage_program(tmp_path: Path):
    """Before the fix this controller reported ``programs: []`` -- a silent
    blank on 5 of the 6 controllers of a real production model."""
    inpx = _write_inpx(tmp_path, _stage_network_xml())

    controller = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["6"]

    assert controller["controller_type"] == "FIXEDTIME"
    assert controller["active_program_no"] == "11"
    programs = {p["prog_no"]: p for p in controller["programs"]}
    assert set(programs) == {"11", "12"}
    assert programs["11"]["name"] == "BaseAM"
    assert programs["11"]["cycle_s"] == 131.0  # 131000 ms -> 131 s, not 131000
    assert programs["12"]["cycle_s"] == 143.0
    assert programs["11"]["program_kind"] == "stage_based"


def test_stage_based_program_labels_its_empty_sg_splits_with_a_reason(tmp_path: Path):
    """An empty split list must never arrive bare: a stage program stores no
    per-signal-group green window, and saying so IS the answer."""
    inpx = _write_inpx(tmp_path, _stage_network_xml())

    program = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["6"]["programs"][0]

    assert program["sg_splits"] == []
    assert program["sg_splits_unavailable_reason"] == SG_SPLITS_STAGE_BASED


def test_stage_sequence_resolves_interstage_ids_not_stage_ids(tmp_path: Path):
    """THE bug. ``display`` is an interstageProg id and its ``toStage`` is the
    stage taking over. Reading it as a stage id yields A/D/E/G1/D2 -- wrong on
    three of five, with stage G lost entirely."""
    inpx = _write_inpx(tmp_path, _stage_network_xml())

    controller = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["6"]
    program = {p["prog_no"]: p for p in controller["programs"]}["11"]
    steps = program["stage_sequence"]

    assert [s["transition_at_s"] for s in steps] == [37.0, 57.0, 92.0, 121.0, 127.0]
    assert [s["to_stage"] for s in steps] == ["D", "E", "G", "G1", "A"]
    # Every transition's target is the next transition's source: a closed
    # stage cycle. This is what proves the interstage-id reading correct --
    # all 28 stage programs in the real model satisfy it.
    assert [s["from_stage"] for s in steps] == ["A", "D", "E", "G", "G1"]
    assert [s["signal_groups"] for s in steps] == [
        ["7", "8"],
        ["5", "6"],
        ["3", "4"],
        ["1", "3"],
        ["1", "2"],
    ]


def test_stage_phases_use_the_progno_active_program_not_the_first(tmp_path: Path):
    """A Murdoch controller carries up to EIGHT stage programs
    (Base/DoMin/TMP1/TMP2 x AM/PM); only ``progNo`` says which one runs."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    first = parse_signal_controllers(_write_inpx(dir_a, _stage_network_xml()))
    second = parse_signal_controllers(_write_inpx(dir_b, _stage_network_xml(prog_no="12")))

    assert first["6"]["cycle_time"] == 131  # stageProgs[0] AND progNo=11
    assert second["6"]["cycle_time"] == 143  # progNo=12, NOT stageProgs[0]
    assert [p["start"] for p in second["6"]["phases"]] == [38, 61, 72, 104, 130, 134]


def test_stage_phases_attribute_signal_groups_to_the_right_stage(tmp_path: Path):
    """Cross-checked against the engineer's own SCATS workbook for this
    intersection's AM peak: intervals 41/20/35/29 (+6 for G1) s vs measured
    green+yellow+all-red 40.9/19.5/34.6/33.9 s, and the intervals sum to the
    131 s cycle exactly. ``duration`` is the stage INTERVAL (its introducing
    interstage + its green), not green alone -- see _parse_stage_program_
    phases for why green alone is not statically derivable."""
    inpx = _write_inpx(tmp_path, _stage_network_xml())

    phases = parse_signal_controllers(inpx)["6"]["phases"]

    assert [(p["start"], p["duration"], p["signal_group_ids"]) for p in phases] == [
        (37, 20, ["7", "8"]),   # stage D
        (57, 35, ["5", "6"]),   # stage E
        (92, 29, ["3", "4"]),   # stage G  <- lost entirely by the old reading
        (121, 6, ["1", "3"]),   # stage G1
        (127, 41, ["1", "2"]),  # stage A, wrapping the cycle boundary
    ]
    assert sum(p["duration"] for p in phases) == 131


def test_stage_phase_never_serves_an_empty_signal_group_list(tmp_path: Path):
    """A sibling controller's ``display="6"`` against a 5-stage controller
    produced a phase serving NO signal groups -- a plan that cannot exist."""
    inpx = _write_inpx(tmp_path, _stage_network_xml())

    for info in parse_signal_controllers(inpx).values():
        for phase in info["phases"]:
            assert phase["signal_group_ids"], "phase at %ss serves no SG" % phase["start"]


def test_stage_based_controller_publishes_a_signal_group_roster(tmp_path: Path):
    inpx = _write_inpx(tmp_path, _stage_network_xml())

    controller = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["6"]
    roster = {sg["sg_no"]: sg for sg in controller["signal_groups"]}

    assert len(roster) == 8
    # min greens match the engineer's workbook per phase: A=8, D=6, E=8, G=6
    assert roster["1"]["min_green_s"] == 8.0
    assert roster["1"]["green_in_stages"] == ["A", "G1"]
    assert roster["7"]["min_green_s"] == 6.0 and roster["7"]["green_in_stages"] == ["D"]
    assert roster["5"]["min_green_s"] == 8.0 and roster["5"]["green_in_stages"] == ["E"]
    assert roster["3"]["min_green_s"] == 6.0
    assert roster["3"]["green_in_stages"] == ["G", "G1"]
    assert roster["1"]["head_count"] == 1


def test_controller_with_no_signal_heads_is_flagged(tmp_path: Path):
    """A real one exists in the Murdoch model (SC 99, 'Murdoch-Farrington
    Link'): full programs, zero signal heads -- editing it is a guaranteed
    no-op, exactly the silent failure this project exists to surface."""
    inpx = _write_inpx(tmp_path, _stage_network_xml(heads=False))

    controller = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["6"]

    assert controller["controller_flags"] == [CONTROLLER_NO_SIGNAL_HEADS]


# ---------------------------------------------------------------------------
# The 375 s stub -- a REAL program, correctly read, that is not an operable
# plan. Verbatim structure of Murdoch SC 9 "TCS1111".
# ---------------------------------------------------------------------------

_STUB_BLOB = (
    '<sc version="202001" id="9" name="" checkSum="2447889904">'
    '<sgs><sg id="1" name="SG1"/></sgs>'
    "<progs>"
    '<prog id="11" cycletime="375000" offset="0" name="BaseAM"><sgs>'
    '<sg sg_id="1" signal_sequence="7">'
    '<cmds><cmd display="3" begin="0"/><cmd display="1" begin="357000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="4000"/></fixedstates>'
    "</sg></sgs></prog>"
    '<prog id="12" cycletime="80000" offset="0" name="BasePM"><sgs>'
    '<sg sg_id="1" signal_sequence="7">'
    '<cmds><cmd display="3" begin="0"/><cmd display="1" begin="55000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="4000"/></fixedstates>'
    "</sg></sgs></prog>"
    "</progs><stageProgs/></sc>"
)


def _stub_network_xml() -> str:
    isd = base64.b64encode(_STUB_BLOB.encode("utf-8")).decode("ascii")
    return (
        '<?xml version="1.0" encoding="utf-8"?><network><signalControllers>'
        '<signalController no="9" name="SC-Stub" type="FIXEDTIME" progNo="11" '
        'intSupplyData="%s"><sgs><signalGroup no="1" name="SG1" amber="5" '
        'minGreen="10" endRed="0" endGreen="0"/></sgs></signalController>'
        "</signalControllers>"
        '<signalHeads><signalHead no="1111011" sg="9 1" lane="772 1"/>'
        '<signalHead no="1111012" sg="9 1" lane="772 2"/></signalHeads>'
        "</network>" % isd
    )


def test_implausible_program_is_reported_verbatim_but_flagged(tmp_path: Path):
    """375 s cycle, ONE signal group green 0 -> 357 s. The number is exactly
    what the .inpx stores and is NOT altered or hidden -- but it ships with
    machine-readable flags so no answer can quote it as plain fact."""
    inpx = _write_inpx(tmp_path, _stub_network_xml())

    controller = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["9"]
    programs = {p["prog_no"]: p for p in controller["programs"]}

    assert programs["11"]["cycle_s"] == 375.0
    assert programs["11"]["sg_splits"][0]["green_end_s"] == 357.0
    assert set(programs["11"]["data_quality"]) == {
        DQ_CYCLE_ABOVE_TYPICAL_MAX,
        DQ_SINGLE_SIGNAL_GROUP,
        DQ_NEAR_PERMANENT_GREEN,
    }
    # The plausible sibling program must NOT be flagged for cycle or green.
    assert programs["12"]["data_quality"] == [DQ_SINGLE_SIGNAL_GROUP]


def test_progno_active_program_is_marked_on_the_stub_controller(tmp_path: Path):
    """progs[0] HAPPENS to be the active program here -- which is precisely
    why the known progs[0]-vs-progNo gap did not cause the 375 s report."""
    inpx = _write_inpx(tmp_path, _stub_network_xml())

    controller = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["9"]

    assert controller["active_program_no"] == "11"
    assert controller["programs"][0]["prog_no"] == "11"


def test_controller_with_no_program_at_all_says_why(tmp_path: Path):
    blob = '<sc version="202001" id="5"><sgs/><progs/><stageProgs/></sc>'
    isd = base64.b64encode(blob.encode("utf-8")).decode("ascii")
    inpx = _write_inpx(
        tmp_path,
        _network_xml(
            '<signalController no="5" type="FIXEDTIME" progNo="1" intSupplyData="%s"/>' % isd
        ),
    )

    controller = {c["sc_no"]: c for c in parse_signal_programs(inpx)}["5"]

    assert controller["programs"] == []
    assert controller["programs_unavailable_reason"] == PROGRAMS_BLOB_EMPTY


_TWO_PROG_BLOB = (
    '<sc version="202001" id="1" name="">'
    '<sgs><sg id="1" name="SG1"/></sgs>'
    "<progs>"
    '<prog id="1" cycletime="130000" name="First"><sgs>'
    '<sg sg_id="1"><cmds><cmd display="3" begin="100000"/><cmd display="1" begin="120000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="3000"/></fixedstates></sg>'
    "</sgs></prog>"
    '<prog id="2" cycletime="90000" name="Active"><sgs>'
    '<sg sg_id="1"><cmds><cmd display="3" begin="1000"/><cmd display="1" begin="15000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="3000"/></fixedstates></sg>'
    "</sgs></prog>"
    "</progs></sc>"
)


def test_phases_follow_progno_not_the_first_signal_group_program(tmp_path: Path):
    """The <progs> twin of the stage-program fix (chip task_eeca6837).
    Urban Intersection Beijing.CN really does run progNo="2" of progs {1, 2}
    -- live-proven by the SimBreakAt/SigState oracle in
    backend/tools/probe_signal_programs.py -- so reading progs[0] reported a
    plan the simulation never runs."""
    isd = base64.b64encode(_TWO_PROG_BLOB.encode("utf-8")).decode("ascii")
    inpx = _write_inpx(
        tmp_path,
        _network_xml(
            '<signalController no="1" type="FIXEDTIME" progNo="2" intSupplyData="%s"/>' % isd
        ),
    )

    info = parse_signal_controllers(inpx)["1"]

    assert info["cycle_time"] == 90       # program 2's cycle, not program 1's 130
    assert info["phases"][0]["start"] == 1  # program 2's green, not program 1's 100
    assert info["num_phases"] == 1


def test_phases_fall_back_to_the_first_program_when_progno_names_nothing(tmp_path: Path):
    """A malformed/absent progNo must degrade to the previous behavior, not
    to an empty phase list -- a blank plan is worse than a stale one here."""
    isd = base64.b64encode(_TWO_PROG_BLOB.encode("utf-8")).decode("ascii")
    inpx = _write_inpx(
        tmp_path,
        _network_xml(
            '<signalController no="1" type="FIXEDTIME" progNo="99" intSupplyData="%s"/>' % isd
        ),
    )

    info = parse_signal_controllers(inpx)["1"]

    assert info["cycle_time"] == 130
    assert info["phases"][0]["start"] == 100

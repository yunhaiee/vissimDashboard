"""Unit #70 — the Vissig document a time-of-day controller writes.

Two obligations, and the first one is the reason the feature is allowed to exist
at all: a controller that did NOT opt in must emit the bytes it emitted before
this unit. That is asserted against a golden captured from the pre-#70 code
path, not against the current code's own output.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

import pytest

from services.genesis import schema
from services.genesis.signals import (
    AlternateProgram,
    NodeSignals,
    SignalGroupPlan,
    TodPrograms,
    build_int_supply_data,
    build_vissig_document,
)

#: The exact document the single-program path produced before unit #70, for a
#: controller with two groups. Captured by running ``build_vissig_document`` at
#: 8e5eeb5 (the commit this unit branched from) and pasted verbatim; the point
#: of a golden is that it does NOT re-derive itself from the code under test.
GOLDEN_SINGLE_PROGRAM = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<sc id="7" name="" frequency="1" defaultIntergreenMatrix="0">\r\n'
    "  <sgs>\r\n"
    '    <sg id="1" name="" defaultSignalSequence="3">\r\n'
    "      <defaultDurations>\r\n"
    '        <defaultDuration display="2" duration="0" />\r\n'
    '        <defaultDuration display="4" duration="3000" />\r\n'
    '        <defaultDuration display="1" duration="0" />\r\n'
    '        <defaultDuration display="3" duration="0" />\r\n'
    "      </defaultDurations>\r\n"
    "    </sg>\r\n"
    '    <sg id="2" name="" defaultSignalSequence="3">\r\n'
    "      <defaultDurations>\r\n"
    '        <defaultDuration display="2" duration="0" />\r\n'
    '        <defaultDuration display="4" duration="3000" />\r\n'
    '        <defaultDuration display="1" duration="0" />\r\n'
    '        <defaultDuration display="3" duration="0" />\r\n'
    "      </defaultDurations>\r\n"
    "    </sg>\r\n"
    "  </sgs>\r\n"
    "  <intergreenmatrices />\r\n"
    "  <progs>\r\n"
    '    <prog id="1" cycletime="60000" switchpoint="0" offset="0" intergreens="0" '
    'name="Signal program">\r\n'
    "      <sgs>\r\n"
    '        <sg sg_id="1" signal_sequence="3">\r\n'
    "          <cmds>\r\n"
    '            <cmd display="3" begin="0" />\r\n'
    '            <cmd display="1" begin="28000" />\r\n'
    "          </cmds>\r\n"
    "          <fixedstates>\r\n"
    '            <fixedstate display="2" duration="0" />\r\n'
    '            <fixedstate display="4" duration="3000" />\r\n'
    "          </fixedstates>\r\n"
    "        </sg>\r\n"
    '        <sg sg_id="2" signal_sequence="3">\r\n'
    "          <cmds>\r\n"
    '            <cmd display="3" begin="30000" />\r\n'
    '            <cmd display="1" begin="58000" />\r\n'
    "          </cmds>\r\n"
    "          <fixedstates>\r\n"
    '            <fixedstate display="2" duration="0" />\r\n'
    '            <fixedstate display="4" duration="3000" />\r\n'
    "          </fixedstates>\r\n"
    "        </sg>\r\n"
    "      </sgs>\r\n"
    "    </prog>\r\n"
    "  </progs>\r\n"
    "  <stages />\r\n"
    "  <interstageProgs />\r\n"
    "  <stageProgs />\r\n"
    "</sc>"
)


def _group(no: int, link_id: int, green_start_s: float, green_s: float) -> SignalGroupPlan:
    return SignalGroupPlan(
        no=no,
        approach_link_id=link_id,
        approach_name=f"approach {link_id}",
        phase_id=f"Φ{no}",
        green_start_s=green_start_s,
        green_s=green_s,
        amber_s=3.0,
        all_red_s=2.0,
        red_amber_s=0.0,
        min_green_s=5.0,
        lanes=2,
    )


def _node(cycle_s: float = 60.0, tod: TodPrograms | None = None, **kwargs) -> NodeSignals:
    groups = kwargs.pop(
        "groups", (_group(1, 101, 0.0, 25.0), _group(2, 102, 30.0, 25.0))
    )
    return NodeSignals(
        controller_no=7,
        node_id=7,
        node_name="원신흥",
        cycle_s=cycle_s,
        groups=groups,
        heads=(),
        detectors=(),
        issues=(),
        compliance=(),
        tod=tod,
    )


# ---------------------------------------------------------------------------
# Additivity — the byte-identity obligation
# ---------------------------------------------------------------------------


def test_a_controller_without_tod_is_byte_identical_to_the_pre_unit_golden():
    assert build_vissig_document(_node()) == GOLDEN_SINGLE_PROGRAM


def test_the_default_node_signals_carries_no_tod():
    """Additivity at the type level: every existing constructor call, none of
    which passes ``tod``, keeps producing a single-program controller."""
    assert _node().tod is None


def test_a_controller_without_tod_writes_no_schedule_element_at_all():
    document = build_vissig_document(_node())
    assert "dailyProgList" not in document
    assert document.count("<prog ") == 1


def test_int_supply_data_round_trips_the_golden():
    blob = build_int_supply_data(_node())
    assert base64.b64decode(blob).decode("utf-8") == GOLDEN_SINGLE_PROGRAM


# ---------------------------------------------------------------------------
# The TOD document
# ---------------------------------------------------------------------------


def _tod_node(emit_schedule_element: bool = True) -> NodeSignals:
    alternate = AlternateProgram(
        prog_id=2,
        program_key="L2-P3",
        cycle_s=90.0,
        groups=(_group(1, 101, 0.0, 40.0), _group(2, 102, 45.0, 40.0)),
    )
    return _node(
        tod=TodPrograms(
            entries=((7 * 3600, 1), (17 * 3600, 2)),
            alternates=(alternate,),
            day_plan_no=1,
            emit_schedule_element=emit_schedule_element,
        )
    )


def test_the_confirmed_plan_stays_program_1():
    """progNo is "1" on every controller this package writes, so program 1 must
    remain the plan the human confirmed. If Vissim ignored the schedule, the run
    would be today's network rather than an arbitrary sibling."""
    assert schema.SIGNAL_CONTROLLER_ATTRS["progNo"] == "1"
    root = ET.fromstring(build_vissig_document(_tod_node()))
    progs = root.findall("./progs/prog")
    assert progs[0].get("id") == "1"
    assert progs[0].get("cycletime") == "60000"  # the primary's cycle, not the alternate's


def test_each_plan_becomes_its_own_program():
    root = ET.fromstring(build_vissig_document(_tod_node()))
    progs = root.findall("./progs/prog")
    assert [p.get("id") for p in progs] == ["1", "2"]
    assert [p.get("cycletime") for p in progs] == ["60000", "90000"]


def test_the_primary_program_body_is_unchanged_by_the_presence_of_an_alternate():
    """An alternate must not perturb the confirmed program's own bytes."""
    plain = build_vissig_document(_node())
    with_tod = build_vissig_document(_tod_node())
    primary_block = plain[plain.index("  <progs>") : plain.index("  </progs>")]
    assert primary_block in with_tod


def test_the_schedule_element_is_the_last_child_of_sc():
    """Measured position: the consultant network's 6 Vissim-authored controllers
    all carry <dailyProgLists> after <stageProgs>."""
    root = ET.fromstring(build_vissig_document(_tod_node()))
    assert [child.tag for child in root][-1] == "dailyProgLists"


def test_the_schedule_names_every_switch_point_in_milliseconds():
    root = ET.fromstring(build_vissig_document(_tod_node()))
    entries = root.findall("./dailyProgLists/dailyProgList/dailyProg")
    assert [e.get("from") for e in entries] == ["25200000", "61200000"]
    assert [e.get("prog_id") for e in entries] == ["1", "2"]
    assert schema.VISSIG_MS_PER_S == 1000


def test_the_emitted_schedule_carries_the_s7_record():
    """The C2/S6 idiom: the emitted element points at the record that produced
    it, so a reader of the FILE — not just of the source — knows where its shape
    came from. Re-anchored when S7 was settled on 2026-08-26: the marker now
    states the finding rather than an open question, and still names the probe
    that would re-verify it after a Vissim upgrade."""
    document = build_vissig_document(_tod_node())
    assert "S7 SETTLED 2026-08-26" in document
    assert "genesis_tod_live_check.py" in document
    assert "<!--" in document and "-->" in document


def test_the_emitted_marker_does_not_describe_an_open_assumption():
    """S7 is closed; a file that still called it an assumption would misreport
    the one thing this marker exists to say."""
    document = build_vissig_document(_tod_node())
    assert "ASSUMPTION S7" not in document


def test_turning_the_schedule_element_off_keeps_the_programs_and_empties_the_container():
    """The escape hatch S7 no longer needs, kept for a FUTURE Vissim that stops
    accepting the schedule: several programs (measured, live-verified) and an
    empty container — the exact shape every Vissim-authored controller on disk
    carries."""
    document = build_vissig_document(_tod_node(emit_schedule_element=False))
    root = ET.fromstring(document)
    assert [p.get("id") for p in root.findall("./progs/prog")] == ["1", "2"]
    assert root.find("./dailyProgLists") is not None
    assert list(root.find("./dailyProgLists")) == []
    assert "S7 SETTLED" not in document


def test_the_tod_document_is_well_formed_and_keeps_vissim_s_line_endings():
    document = build_vissig_document(_tod_node())
    ET.fromstring(document)  # raises if malformed
    assert "\r\n" in document
    assert "\n\n" not in document.replace("\r\n", "\n\n") .replace("\n\n", "\r\n")


@pytest.mark.parametrize("emit", [True, False])
def test_the_declaration_sgs_still_describes_the_primary_groups_only(emit):
    """The controller declares its groups ONCE. Every program's sg_id must be a
    subset of that declaration or it references a group Vissim never saw."""
    root = ET.fromstring(build_vissig_document(_tod_node(emit_schedule_element=emit)))
    declared = {sg.get("id") for sg in root.findall("./sgs/sg")}
    for prog in root.findall("./progs/prog"):
        used = {sg.get("sg_id") for sg in prog.findall("./sgs/sg")}
        assert used <= declared

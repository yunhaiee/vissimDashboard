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

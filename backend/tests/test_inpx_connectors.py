"""Tests for Phase 3's connector from/to link topology in inpx_parser.py.

``_parse_links`` has always set a boolean ``is_connector`` per link (True
when the ``<link>`` carries a ``fromLinkEndPt``/``toLinkEndPt`` child) but
never read which link ids those endpoints reference. This file covers the
new ``from_link``/``to_link``/``from_pos``/``to_pos`` fields it now also
extracts — inline XML fixtures for the two attribute schemas Vissim uses
(``lane="linkNo laneNo"`` and an explicit ``link=`` attribute) plus
malformed/missing-attribute cases, and a parse of the real (regenerated)
demo network to confirm end-to-end behaviour.

No COM, no network — see docs/phase2/DECISIONS.md's dated entry for why the
schema is inferred from Vissim convention rather than verified against a
real connector-bearing .inpx (none exists on this machine).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import services.inpx_parser as inpx_parser

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


def _links_from_xml(links_inner_xml: str) -> list[dict]:
    """Parse a ``<network><links>...</links></network>`` fragment into link dicts."""
    xml = f'<?xml version="1.0" encoding="UTF-8"?><network><links>{links_inner_xml}</links></network>'
    root = ET.fromstring(xml)
    return inpx_parser._parse_links(root)


# ---------------------------------------------------------------------------
# (a) lane="linkNo laneNo" form — Vissim's normal connector schema
# ---------------------------------------------------------------------------


def test_connector_lane_attribute_form_extracts_from_to_link_and_pos():
    links = _links_from_xml(
        '<link no="301" name="Connector">'
        "<geometry><linkPolyPts>"
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="10" y="0" zOffset="0"/>'
        "</linkPolyPts></geometry>"
        '<lanes><lane width="3.5"/></lanes>'
        '<fromLinkEndPt lane="1234 1" pos="221.9"/>'
        '<toLinkEndPt lane="5678 2" pos="3.5"/>'
        "</link>"
    )

    assert len(links) == 1
    link = links[0]
    assert link["is_connector"] is True
    assert link["from_link"] == "1234"
    assert link["to_link"] == "5678"
    assert link["from_pos"] == 221.9
    assert link["to_pos"] == 3.5


# ---------------------------------------------------------------------------
# (b) explicit link= attribute form — takes precedence over lane= when present
# ---------------------------------------------------------------------------


def test_connector_explicit_link_attribute_form_takes_precedence_over_lane():
    links = _links_from_xml(
        '<link no="302" name="Connector with explicit link attr">'
        '<lanes><lane width="3.5"/></lanes>'
        '<fromLinkEndPt link="111" lane="999 1" pos="50.0"/>'
        '<toLinkEndPt link="222" lane="888 3" pos="0.0"/>'
        "</link>"
    )

    assert len(links) == 1
    link = links[0]
    assert link["is_connector"] is True
    # The explicit link= attribute wins over the (deliberately different,
    # bogus) lane= token, proving precedence rather than coincidental match.
    assert link["from_link"] == "111"
    assert link["to_link"] == "222"
    assert link["from_pos"] == 50.0
    assert link["to_pos"] == 0.0


# ---------------------------------------------------------------------------
# (c) malformed / missing attributes -> None, never raises
# ---------------------------------------------------------------------------


def test_connector_missing_pos_attribute_yields_none_pos_without_raising():
    links = _links_from_xml(
        '<link no="303" name="No pos">'
        '<lanes><lane width="3.5"/></lanes>'
        '<fromLinkEndPt lane="42 1"/>'
        '<toLinkEndPt lane="43 1"/>'
        "</link>"
    )

    link = links[0]
    assert link["is_connector"] is True
    assert link["from_link"] == "42"
    assert link["to_link"] == "43"
    assert link["from_pos"] is None
    assert link["to_pos"] is None


def test_connector_malformed_pos_attribute_yields_none_without_raising():
    links = _links_from_xml(
        '<link no="304" name="Bad pos">'
        '<lanes><lane width="3.5"/></lanes>'
        '<fromLinkEndPt lane="42 1" pos="not-a-number"/>'
        '<toLinkEndPt lane="43 1" pos=""/>'
        "</link>"
    )

    link = links[0]
    assert link["from_pos"] is None
    assert link["to_pos"] is None


def test_connector_empty_or_missing_lane_attribute_yields_none_link_without_raising():
    links = _links_from_xml(
        '<link no="305" name="Empty lane">'
        '<lanes><lane width="3.5"/></lanes>'
        '<fromLinkEndPt lane="" pos="10.0"/>'
        "<toLinkEndPt/>"
        "</link>"
    )

    link = links[0]
    assert link["is_connector"] is True  # both elements are present, even if empty
    assert link["from_link"] is None
    assert link["to_link"] is None
    assert link["from_pos"] == 10.0
    assert link["to_pos"] is None


def test_non_connector_link_has_none_from_to_fields():
    links = _links_from_xml(
        '<link no="101" name="Plain link">'
        "<geometry><linkPolyPts>"
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="10" y="0" zOffset="0"/>'
        "</linkPolyPts></geometry>"
        '<lanes><lane width="3.5"/><lane width="3.5"/></lanes>'
        "</link>"
    )

    link = links[0]
    assert link["is_connector"] is False
    assert link["from_link"] is None
    assert link["to_link"] is None
    assert link["from_pos"] is None
    assert link["to_pos"] is None


# ---------------------------------------------------------------------------
# (d) the regenerated demo network — end-to-end, real file
# ---------------------------------------------------------------------------


def test_demo_network_has_two_connectors_with_correct_from_to_topology():
    """data/practice.inpx (regenerated by tools/make_demo_data.py) carries two
    connectors mirroring real MOVEMENTS entries: 301 bridges Node 1's EB
    through movement (101 -> 140), 302 bridges Node 2's (140 -> 208)."""
    net = inpx_parser.parse_network(str(DEMO_INPX))
    links = net["links"]

    connectors = {link["id"]: link for link in links if link["is_connector"]}
    assert set(connectors.keys()) == {"301", "302"}

    assert connectors["301"]["from_link"] == "101"
    assert connectors["301"]["to_link"] == "140"
    assert connectors["301"]["from_pos"] == 280.0
    assert connectors["301"]["to_pos"] == 0.0

    assert connectors["302"]["from_link"] == "140"
    assert connectors["302"]["to_link"] == "208"
    assert connectors["302"]["from_pos"] == 360.0
    assert connectors["302"]["to_pos"] == 0.0

    non_connectors = [link for link in links if not link["is_connector"]]
    assert len(non_connectors) == 14
    for link in non_connectors:
        assert link["from_link"] is None
        assert link["to_link"] is None
        assert link["from_pos"] is None
        assert link["to_pos"] is None

    assert len(links) == 16

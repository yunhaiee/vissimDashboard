"""Tests for the topology-only structural network diff (roadmap R1.6 / row 2.3).

Three layers, in increasing order of realism:

1. **Hand-built minimal networks** — inline XML small enough that the expected
   diff is obvious by inspection.
2. **The synthetic demo network** (``data/practice.inpx``, produced by
   ``tools/make_demo_data.py``) — the house rule that non-COM code must work
   against demo data.
3. **The real customer network** — a mutation round-trip: copy the real
   ``.inpx``, apply a KNOWN change set, and assert the report recovers exactly
   that set and nothing else, in both directions (no misses, no phantoms).
   Skipped with a clear reason on a machine that has no uploaded workspace,
   which is the only test here that can skip.

Plus the edge cases the unit is required to survive: an empty network, a
network with no connectors, an id present in one file and absent in the other,
and malformed input — which must refuse clearly rather than crash or, worse,
report an empty diff.

No COM, no Vissim, no licence seat, no network access.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from services.genesis.diff import (
    DiffSeverity,
    StructuralDiff,
    diff_network_files,
    diff_topologies,
    load_topology,
    render_diff_report,
    topology_from_root,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_INPX = REPO_ROOT / "data" / "practice.inpx"


def _real_inpx() -> Path | None:
    """The real customer network, if this machine has one uploaded.

    Discovered by glob rather than hard-coded workspace id: the id is
    machine-local. Read-only — every test copies it before touching it, and
    nothing here ever writes inside ``runtime/``.
    """
    matches = sorted(REPO_ROOT.glob("runtime/workspaces/*/uploads/current/current.inpx"))
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Minimal-network builders
# ---------------------------------------------------------------------------


# Node membership is GEOMETRIC — updated 2026-08-06, after the live check
# measured that no Vissim-saved network anywhere contains the ``<linkSegment>``
# element these fixtures used to declare membership with. Vissim never writes
# one, so ``inpx_parser._node_link_ids`` read an element that does not exist and
# returned an empty set for every node of every network; it now computes
# membership by testing each link's polyline against the node's polygon.
#
# These builders keep the SAME declarative API — ``_node("10", ["9"])`` still
# says "node 10 contains link 9" — and realise it in geometry instead:
#
# * every link and connector gets one identifying vertex at ``(slot, 0)``,
#   where ``slot`` is derived from its id and is unique; its remaining vertices
#   sit far below (``y = -100``) where no node polygon reaches;
# * a node's polygon is a COMB — a base bar under the whole slot range at
#   ``y ∈ [-2, -1]`` with a tooth rising to ``y = +1`` over each member's slot.
#   A comb is a simple polygon, and it contains exactly the identifying
#   vertices of the members and no others, for ANY subset — including the empty
#   one, which is the bare bar and contains nothing.
_SLOT_SPACING = 10.0
_TOOTH_HALF_WIDTH = 1.0


def _slot(no: str) -> float:
    return int(no) * _SLOT_SPACING


def _link(no: str, *, lanes: int = 1, points: str | None = None) -> str:
    x = _slot(no)
    pts = points or f'<linkPolyPoint x="{x}" y="0"/><linkPolyPoint x="{x}" y="-100"/>'
    lane_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
    return (
        f'<link no="{no}" name="link {no}">'
        f"<geometry><linkPolyPts>{pts}</linkPolyPts></geometry>"
        f"<lanes>{lane_xml}</lanes>"
        f"</link>"
    )


def _connector(no: str, from_link: str, to_link: str, *, lanes: int = 1) -> str:
    x = _slot(no)
    lane_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
    return (
        f'<link no="{no}" name="conn {no}">'
        f'<fromLinkEndPt link="{from_link}" pos="90"/>'
        f'<toLinkEndPt link="{to_link}" pos="0"/>'
        f'<geometry><linkPolyPts><linkPolyPoint x="{x}" y="0"/>'
        f'<linkPolyPoint x="{x}" y="-50"/></linkPolyPts></geometry>'
        f"<lanes>{lane_xml}</lanes>"
        f"</link>"
    )


def _node(no: str, member_links: list[str]) -> str:
    """A node whose polygon contains exactly ``member_links`` and nothing else."""
    slots = sorted(_slot(lid) for lid in member_links)
    left = (slots[0] if slots else 0.0) - 5.0
    right = (slots[-1] if slots else 0.0) + 5.0
    # Anticlockwise: along the bottom of the bar, then back along the top
    # deviating up over each member's slot.
    corners: list[tuple[float, float]] = [(left, -2.0), (right, -2.0)]
    for x in reversed(slots):
        corners.extend(
            [
                (x + _TOOTH_HALF_WIDTH, -1.0),
                (x + _TOOTH_HALF_WIDTH, 1.0),
                (x - _TOOTH_HALF_WIDTH, 1.0),
                (x - _TOOTH_HALF_WIDTH, -1.0),
            ]
        )
    corners.append((left, -1.0))
    points = "".join(f'<point x="{x}" y="{y}"/>' for x, y in corners)
    return (
        f'<node no="{no}" name="node {no}">'
        f"<geometry><points>{points}</points></geometry>"
        f"</node>"
    )


def _signal_head(no: str, link: str, lane: int = 1, pos: float = 80.0) -> str:
    return f'<signalHead no="{no}" lane="{link} {lane}" pos="{pos}" sg="1 1"/>'


def _network(*, links: str = "", nodes: str = "", signal_heads: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<network>"
        f"<links>{links}</links>"
        f"<nodes>{nodes}</nodes>"
        f"<signalHeads>{signal_heads}</signalHeads>"
        "</network>"
    )


def _topology(xml: str, label: str):
    return topology_from_root(ET.fromstring(xml), label=label)


def _write(tmp_path: Path, name: str, xml: str) -> Path:
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return path


#: A two-link + one-connector network with one node and one signal head.
BASE_XML = _network(
    links=_link("1", lanes=2) + _link("2", lanes=1) + _connector("9", "1", "2"),
    nodes=_node("10", ["9"]),
    signal_heads=_signal_head("100", "1"),
)


# ---------------------------------------------------------------------------
# (1) Hand-built minimal networks — expected diff obvious by inspection
# ---------------------------------------------------------------------------


def test_identical_minimal_networks_produce_an_empty_diff():
    diff = diff_topologies(_topology(BASE_XML, "a"), _topology(BASE_XML, "b"))

    assert diff.ok is True
    assert diff.change_count == 0
    assert diff.has_changes is False
    assert diff.links.added == [] and diff.links.removed == []
    assert diff.links.lane_count_changed == []
    assert diff.connectors.added == [] and diff.connectors.removed == []
    assert diff.connectors.rewired == []
    assert diff.reclassified == []
    assert all(not node.changed for node in diff.node_movements)
    assert diff.finding_codes(DiffSeverity.REFUSE) == []
    assert diff.finding_codes(DiffSeverity.WARN) == []


def test_added_and_removed_links_are_reported_on_the_right_side():
    target = _network(
        links=_link("2", lanes=1) + _link("3", lanes=1) + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]),
    )
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    assert diff.links.removed == ["1"]
    assert diff.links.added == ["3"]
    assert diff.connectors.added == [] and diff.connectors.removed == []
    assert diff.change_count == 2


def test_lane_count_change_is_reported_with_both_counts():
    target = BASE_XML.replace(_link("1", lanes=2), _link("1", lanes=4))
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    assert [(c.link_id, c.base_lanes, c.target_lanes) for c in diff.links.lane_count_changed] == [
        ("1", 2, 4)
    ]
    assert diff.links.lane_count_changed[0].delta == 2
    assert diff.links.added == [] and diff.links.removed == []
    assert diff.change_count == 1


def test_connector_removal_and_addition():
    target = _network(
        links=_link("1", lanes=2) + _link("2", lanes=1) + _connector("8", "2", "1"),
        nodes=_node("10", ["8"]),
    )
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    assert diff.connectors.removed == ["9"]
    assert diff.connectors.added == ["8"]
    node = diff.node_movements[0]
    assert node.node_id == "10"
    assert [m.connector_id for m in node.removed] == ["9"]
    assert [m.connector_id for m in node.added] == ["8"]
    assert node.base_movement_count == 1 and node.target_movement_count == 1


def test_connector_rewire_names_the_endpoint_that_moved():
    target = BASE_XML.replace(_connector("9", "1", "2"), _connector("9", "2", "1"))
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    assert diff.connectors.added == [] and diff.connectors.removed == []
    assert len(diff.connectors.rewired) == 1
    rewire = diff.connectors.rewired[0]
    assert rewire.connector_id == "9"
    assert rewire.changed_endpoints == ["from", "to"]
    assert (rewire.base_from_link, rewire.target_from_link) == ("1", "2")
    assert (rewire.base_to_link, rewire.target_to_link) == ("2", "1")
    # the node sees the same rewire, so a per-node consumer needs no join
    assert [r.connector_id for r in diff.node_movements[0].rewired] == ["9"]


def test_single_endpoint_rewire_reports_only_that_endpoint():
    target = BASE_XML.replace(_connector("9", "1", "2"), _connector("9", "1", "1"))
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    assert diff.connectors.rewired[0].changed_endpoints == ["to"]


def test_connector_lane_change_is_not_silently_swallowed():
    target = BASE_XML.replace(_connector("9", "1", "2"), _connector("9", "1", "2", lanes=3))
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    assert [(c.link_id, c.base_lanes, c.target_lanes) for c in diff.connectors.lane_count_changed] == [
        ("9", 1, 3)
    ]
    assert diff.links.lane_count_changed == []


def test_movements_per_node_counts_only_connectors_the_node_declares():
    base = _network(
        links=(
            _link("1") + _link("2") + _link("3")
            + _connector("9", "1", "2") + _connector("11", "1", "3")
        ),
        nodes=_node("10", ["9", "11", "1"]),
    )
    target = _network(
        links=_link("1") + _link("2") + _link("3") + _connector("9", "1", "2"),
        nodes=_node("10", ["9", "1"]),
    )
    diff = diff_topologies(_topology(base, "base"), _topology(target, "target"))

    node = diff.node_movements[0]
    # link "1" is a node member but is not a connector, so it is not a movement
    assert node.base_movement_count == 2
    assert node.target_movement_count == 1
    assert [m.connector_id for m in node.removed] == ["11"]
    assert node.removed[0].from_link == "1" and node.removed[0].to_link == "3"


def test_node_present_on_only_one_side_is_distinguished_from_an_empty_node():
    base = _network(
        links=_link("1") + _link("2") + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]) + _node("20", ["9"]),
    )
    target = _network(
        links=_link("1") + _link("2") + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]) + _node("30", []),
    )
    diff = diff_topologies(_topology(base, "base"), _topology(target, "target"))

    by_node = {n.node_id: n for n in diff.node_movements}
    assert by_node["20"].present_in_base is True and by_node["20"].present_in_target is False
    assert by_node["30"].present_in_base is False and by_node["30"].present_in_target is True
    # an emptied node is not the same finding as a deleted node
    assert by_node["30"].target_movement_count == 0
    assert by_node["20"].changed and by_node["30"].changed


def test_signalized_approach_coverage_tracks_the_approach_gaining_a_signal():
    target = _network(
        links=_link("1", lanes=2) + _link("2", lanes=1) + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]),
        signal_heads="",
    )
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    coverage = diff.signal_coverage[0]
    assert coverage.node_id == "10"
    assert coverage.base_approaches == ["1"] and coverage.base_signalized == ["1"]
    assert coverage.target_approaches == ["1"] and coverage.target_signalized == []
    assert coverage.base_coverage == 1.0 and coverage.target_coverage == 0.0
    assert coverage.no_longer_signalized == ["1"]
    assert "signalized_approach_lost" in diff.finding_codes(DiffSeverity.WARN)


def test_newly_signalized_approach_is_reported_without_a_warning():
    base = _network(
        links=_link("1", lanes=2) + _link("2", lanes=1) + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]),
    )
    diff = diff_topologies(_topology(base, "base"), _topology(BASE_XML, "target"))

    coverage = diff.signal_coverage[0]
    assert coverage.newly_signalized == ["1"]
    assert coverage.no_longer_signalized == []
    assert "signalized_approach_lost" not in diff.finding_codes()


def test_link_becoming_a_connector_is_one_reclassification_not_two_phantoms():
    target = _network(
        links=_link("1", lanes=2) + _link("2", lanes=1) + _link("9"),
        nodes=_node("10", ["9"]),
        signal_heads=_signal_head("100", "1"),
    )
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    assert [(r.link_id, r.base_kind, r.target_kind) for r in diff.reclassified] == [
        ("9", "connector", "link")
    ]
    assert diff.links.added == [] and diff.links.removed == []
    assert diff.connectors.added == [] and diff.connectors.removed == []
    assert diff.change_count == 2  # the reclassification + the node losing its movement


def test_dangling_connector_reference_warns_on_the_side_that_has_it():
    target = _network(
        links=_link("1", lanes=2) + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]),
        signal_heads=_signal_head("100", "1"),
    )
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    dangling = [f for f in diff.warnings if f.code == "connector_reference_dangling"]
    assert len(dangling) == 1
    assert dangling[0].subject == "target"
    assert dangling[0].context["problems"] == ["connector 9 to-link 2"]


def test_approach_whose_link_was_deleted_is_still_counted_and_flagged():
    """Coverage reads what the connectors claim, not what survives — dropping a
    dangling approach would make a broken network score better than a sound
    one. The incoherence is reported by id instead."""
    target = _network(
        links=_link("2", lanes=1) + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]),
    )
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    coverage = diff.signal_coverage[0]
    assert coverage.target_approaches == ["1"]  # link 1 is gone, the claim is not
    assert coverage.target_signalized == []  # …and it can carry no signal head
    assert coverage.no_longer_signalized == ["1"]
    assert diff.links.removed == ["1"]
    dangling = [f for f in diff.warnings if f.code == "connector_reference_dangling"]
    assert dangling[0].context["problems"] == ["connector 9 from-link 1"]


def test_signal_head_the_parser_could_not_resolve_is_declared_not_dropped():
    """The parser skips a signal head whose link/lane/pos will not resolve.
    Such a head would silently vanish from coverage, so the shortfall is
    counted and warned about instead of being invisible."""
    target = _network(
        links=_link("1", lanes=2) + _link("2", lanes=1) + _connector("9", "1", "2"),
        nodes=_node("10", ["9"]),
        signal_heads=_signal_head("100", "1") + _signal_head("101", "77"),  # link 77 doesn't exist
    )
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(target, "target"))

    warn = [f for f in diff.warnings if f.code == "signal_heads_unresolved"]
    assert len(warn) == 1
    assert warn[0].subject == "target"
    assert warn[0].context == {"declared": 2, "resolved": 1}
    assert diff.target.signal_head_count == 1


def test_numeric_ids_sort_numerically_not_lexically():
    base = _network(links=_link("2") + _link("10") + _link("100"))
    empty = _network(links="")
    diff = diff_topologies(_topology(base, "base"), _topology(empty, "target"))

    assert diff.links.removed == ["2", "10", "100"]


# ---------------------------------------------------------------------------
# Position: the load-bearing constraint of this unit
# ---------------------------------------------------------------------------


def test_position_is_never_reported_and_the_scope_is_stated():
    diff = diff_topologies(_topology(BASE_XML, "a"), _topology(BASE_XML, "b"))

    assert diff.position.reported is False
    assert diff.position.max_residual_m == 14.0
    assert diff.position.scale_term == 1.1782
    assert diff.position.residual_lane_widths == 4.0
    assert "topology_only_scope" in diff.finding_codes(DiffSeverity.INFO)


def test_geometry_only_change_is_zero_structural_changes_plus_a_stated_warn_band():
    # Move link 1's FAR vertex only. Its identifying vertex at (slot, 0) — the
    # one node membership is read from — stays put, so this is a pure geometry
    # change with the topology, including node membership, untouched.
    moved = BASE_XML.replace(
        _link("1", lanes=2),
        _link(
            "1",
            lanes=2,
            points=f'<linkPolyPoint x="{_slot("1")}" y="0"/><linkPolyPoint x="140" y="-75"/>',
        ),
    )
    assert moved != BASE_XML  # the replace actually bit

    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(moved, "target"))

    assert diff.change_count == 0  # topology is identical, and we say so
    assert diff.position.geometry_differs_count == 1
    warn = [f for f in diff.warnings if f.code == "geometry_differs_not_measured"]
    assert len(warn) == 1
    # the band is stated, and no delta is
    assert "14" in warn[0].message and "1.1782" in warn[0].message
    assert warn[0].context["links"] == 1


def test_structured_payload_carries_no_coordinates_at_all():
    """A diff that leaked coordinates would let a consumer compute exactly the
    positional delta this unit refuses to report."""
    diff = diff_topologies(_topology(BASE_XML, "base"), _topology(BASE_XML, "target"))
    payload = json.loads(diff.model_dump_json())

    def _walk(obj, path=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                assert key not in {"x", "y", "points", "polygon", "coordinates", "geometry"}, (
                    f"coordinate-bearing key {key!r} at {path}"
                )
                _walk(value, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

    _walk(payload)


# ---------------------------------------------------------------------------
# (2) Edge cases — empty, connector-less, and malformed input
# ---------------------------------------------------------------------------


def test_empty_network_warns_but_still_reports_the_truthful_diff(tmp_path):
    base = _write(tmp_path, "base.inpx", BASE_XML)
    empty = _write(tmp_path, "empty.inpx", _network(links=""))

    diff = diff_network_files(base, empty)

    assert diff.ok is True
    assert "network_empty" in diff.finding_codes(DiffSeverity.WARN)
    assert diff.links.removed == ["1", "2"]
    assert diff.connectors.removed == ["9"]
    assert diff.target.link_count == 0


def test_two_empty_networks_diff_to_nothing(tmp_path):
    a = _write(tmp_path, "a.inpx", _network(links=""))
    b = _write(tmp_path, "b.inpx", _network(links=""))

    diff = diff_network_files(a, b)

    assert diff.ok is True and diff.change_count == 0
    assert diff.finding_codes(DiffSeverity.WARN).count("network_empty") == 2


def test_network_with_no_connectors_has_no_movements_and_no_coverage():
    base = _network(links=_link("1") + _link("2"), nodes=_node("10", ["1", "2"]))
    target = _network(links=_link("1") + _link("2", lanes=2), nodes=_node("10", ["1", "2"]))
    diff = diff_topologies(_topology(base, "base"), _topology(target, "target"))

    assert diff.base.connector_count == 0
    assert diff.base.movement_count == 0
    node = diff.node_movements[0]
    assert node.base_movement_count == 0 and node.target_movement_count == 0
    assert node.changed is False
    coverage = diff.signal_coverage[0]
    assert coverage.base_approaches == [] and coverage.base_coverage is None
    assert [(c.link_id, c.base_lanes, c.target_lanes) for c in diff.links.lane_count_changed] == [
        ("2", 1, 2)
    ]


def test_malformed_xml_refuses_clearly_and_reports_no_sections(tmp_path):
    good = _write(tmp_path, "good.inpx", BASE_XML)
    broken = _write(tmp_path, "broken.inpx", "<network><links><link no=")

    diff = diff_network_files(good, broken, base_label="good", target_label="broken")

    assert diff.refused is True and diff.ok is False
    assert diff.finding_codes(DiffSeverity.REFUSE) == ["network_unparseable"]
    assert diff.refusals[0].subject == "broken"
    # NOT an empty diff: there is no empty change list to misread
    assert diff.links is None and diff.connectors is None
    assert diff.node_movements is None and diff.signal_coverage is None
    assert diff.base is None and diff.target is None
    assert diff.change_count == 0  # guarded by .ok, never a "no changes" claim


def test_missing_file_refuses(tmp_path):
    good = _write(tmp_path, "good.inpx", BASE_XML)

    diff = diff_network_files(good, tmp_path / "nope.inpx")

    assert diff.refused is True
    assert diff.finding_codes(DiffSeverity.REFUSE) == ["network_file_missing"]


def test_directory_instead_of_file_refuses(tmp_path):
    good = _write(tmp_path, "good.inpx", BASE_XML)
    (tmp_path / "adir").mkdir()

    diff = diff_network_files(good, tmp_path / "adir")

    assert diff.refused is True
    assert diff.finding_codes(DiffSeverity.REFUSE) == ["network_file_missing"]


def test_well_formed_xml_that_is_not_a_vissim_network_refuses(tmp_path):
    good = _write(tmp_path, "good.inpx", BASE_XML)
    other = _write(tmp_path, "other.inpx", "<?xml version='1.0'?><rss><channel/></rss>")

    diff = diff_network_files(good, other, target_label="other")

    assert diff.refused is True
    assert diff.finding_codes(DiffSeverity.REFUSE) == ["network_not_recognised"]
    assert "not a Vissim network" in diff.refusals[0].message
    assert diff.links is None


def test_both_files_bad_reports_both_refusals(tmp_path):
    a = _write(tmp_path, "a.inpx", "<network><links>")
    b = tmp_path / "missing.inpx"

    diff = diff_network_files(a, b)

    assert diff.refused is True
    assert set(diff.finding_codes(DiffSeverity.REFUSE)) == {
        "network_unparseable",
        "network_file_missing",
    }


def test_duplicate_link_ids_warn_rather_than_pass_unnoticed(tmp_path):
    dupe = _write(
        tmp_path, "dupe.inpx", _network(links=_link("1") + _link("1", lanes=3) + _link("2"))
    )

    topology, findings = load_topology(dupe)

    assert topology is not None
    assert [f.code for f in findings] == ["duplicate_link_ids"]
    assert findings[0].context["ids"] == ["1"]
    assert topology.links["1"]["lanes"] == 3  # last wins, and we said so


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_refusal_rendering_says_refused_and_denies_being_an_empty_diff(tmp_path):
    good = _write(tmp_path, "good.inpx", BASE_XML)
    broken = _write(tmp_path, "broken.inpx", "<network><links><link no=")

    text = render_diff_report(diff_network_files(good, broken, target_label="broken"))

    assert "REFUSED" in text
    assert "NOT an empty diff" in text
    assert "network_unparseable" in text


def test_report_rendering_contains_every_section_and_the_position_stance():
    target = BASE_XML.replace(_link("1", lanes=2), _link("1", lanes=4))
    diff = diff_topologies(_topology(BASE_XML, "reference"), _topology(target, "generated"))

    text = render_diff_report(diff, title="Murdoch benchmark diff")

    assert text.startswith("Murdoch benchmark diff")
    for section in (
        "Totals",
        "Links",
        "Connectors",
        "Movements per node",
        "Signalized-approach coverage",
        "Position",
        "Findings",
    ):
        assert section in text
    assert "reported: no" in text
    assert "1: 2->4" in text
    assert "TOTAL structural changes: 1" in text


def test_identical_networks_render_as_structurally_identical():
    text = render_diff_report(diff_topologies(_topology(BASE_XML, "a"), _topology(BASE_XML, "b")))

    assert "TOTAL structural changes: 0" in text
    assert "structurally identical" in text


def test_long_lists_are_truncated_but_never_silently():
    base = _network(links="".join(_link(str(i), lanes=2) for i in range(30)))
    target = _network(links="".join(_link(str(i), lanes=3) for i in range(30)))
    diff = diff_topologies(_topology(base, "base"), _topology(target, "target"))

    text = render_diff_report(diff)

    assert len(diff.links.lane_count_changed) == 30  # the payload keeps them all
    assert "(+10 more)" in text  # …and the rendering admits what it dropped


def test_incomplete_diff_object_renders_a_complaint_rather_than_crashing():
    """A hand-built or deserialised object with no sections and no refusal flag
    must not be rendered as "no changes"."""
    text = render_diff_report(StructuralDiff())

    assert "INCOMPLETE" in text
    assert "TOTAL structural changes" not in text


# ---------------------------------------------------------------------------
# Mutation harness — shared by the demo-network and real-network round-trips
# ---------------------------------------------------------------------------


def _mutate(
    src: Path,
    dst: Path,
    *,
    delete_links: list[str],
    delete_connectors: list[str],
    lane_changes: list[str],
    rewires: list[tuple[str, str]],
) -> dict:
    """Apply a known change set to a copy of ``src`` and return what was done.

    Returned shape is exactly what the diff is asserted to recover, so the
    round-trip is a literal equality rather than a hand-restated expectation.
    """
    tree = ET.parse(src)
    links_el = tree.getroot().find(".//links")
    by_no = {el.get("no"): el for el in links_el.findall("link")}
    injected: dict = {
        "removed_links": [],
        "removed_connectors": [],
        "lane_changes": [],
        "rewires": [],
    }

    for no in delete_links:
        links_el.remove(by_no[no])
        injected["removed_links"].append(no)
    for no in delete_connectors:
        links_el.remove(by_no[no])
        injected["removed_connectors"].append(no)

    for no in lane_changes:
        lanes_el = by_no[no].find("lanes")
        lane_els = lanes_el.findall("lane")
        before = len(lane_els)
        if before >= 2:
            lanes_el.remove(lane_els[-1])
            after = before - 1
        else:
            lanes_el.append(copy.deepcopy(lane_els[0]))
            after = before + 1
        injected["lane_changes"].append({"link": no, "before": before, "after": after})

    for no, new_from in rewires:
        endpoint = by_no[no].find("fromLinkEndPt")
        if endpoint.get("link") is not None:
            before = endpoint.get("link")
            endpoint.set("link", new_from)
        else:  # lane="LINK LANE" form
            tokens = (endpoint.get("lane") or "").split()
            before = tokens[0]
            tokens[0] = new_from
            endpoint.set("lane", " ".join(tokens))
        injected["rewires"].append({"connector": no, "before": before, "after": new_from})

    tree.write(dst, encoding="UTF-8", xml_declaration=True)
    return injected


def _recovered(diff) -> dict:
    return {
        "removed_links": diff.links.removed,
        "removed_connectors": diff.connectors.removed,
        "lane_changes": [
            {"link": c.link_id, "before": c.base_lanes, "after": c.target_lanes}
            for c in diff.links.lane_count_changed + diff.connectors.lane_count_changed
        ],
        "rewires": [
            {
                "connector": r.connector_id,
                "before": r.base_from_link,
                "after": r.target_from_link,
            }
            for r in diff.connectors.rewired
        ],
    }


def _plan_mutation(path: Path) -> dict:
    """Pick mutation targets deterministically from whatever the file contains.

    Chosen by sorted id so the plan is reproducible, and chosen so the rewire
    target is neither the connector's current from-link nor its to-link (which
    would collapse the connector into a degenerate self-attachment and make the
    expectation harder to read than the code under test).
    """
    root = ET.parse(path).getroot()
    from services.inpx_parser import _parse_links

    links = _parse_links(root)
    plain = sorted(
        (lk for lk in links if not lk["is_connector"]), key=lambda lk: int(lk["id"])
    )
    connectors = sorted(
        (lk for lk in links if lk["is_connector"]), key=lambda lk: int(lk["id"])
    )
    delete_links = [lk["id"] for lk in plain[:3]]
    # Leave at least one connector standing to rewire — the synthetic demo
    # network only has two, and a plan that deleted both would make the rewire
    # half of the round-trip vacuously pass on it.
    delete_count = 2 if len(connectors) >= 4 else max(len(connectors) - 1, 0)
    delete_connectors = [lk["id"] for lk in connectors[:delete_count]]
    survivors = [lk["id"] for lk in plain if lk["id"] not in delete_links]
    lane_changes = [lk["id"] for lk in plain if lk["id"] in survivors][:4]

    rewires: list[tuple[str, str]] = []
    for connector in connectors[delete_count:]:
        if len(rewires) == 2:
            break
        if connector["id"] in delete_connectors:
            continue
        candidate = next(
            (
                s
                for s in survivors
                if s not in (connector["from_link"], connector["to_link"])
            ),
            None,
        )
        if candidate is not None:
            rewires.append((connector["id"], candidate))
    return {
        "delete_links": delete_links,
        "delete_connectors": delete_connectors,
        "lane_changes": lane_changes,
        "rewires": rewires,
    }


# ---------------------------------------------------------------------------
# (3a) Synthetic demo network
# ---------------------------------------------------------------------------


def test_demo_network_self_diff_is_empty():
    diff = diff_network_files(DEMO_INPX, DEMO_INPX, base_label="demo", target_label="demo-copy")

    assert diff.ok is True
    assert diff.change_count == 0
    assert diff.base.link_count == diff.target.link_count > 0
    assert diff.finding_codes(DiffSeverity.WARN) == []


def test_demo_network_mutation_round_trip_recovers_exactly_the_injected_set(tmp_path):
    plan = _plan_mutation(DEMO_INPX)
    mutated = tmp_path / "demo-mutated.inpx"
    injected = _mutate(
        DEMO_INPX,
        mutated,
        delete_links=plan["delete_links"],
        delete_connectors=plan["delete_connectors"],
        lane_changes=plan["lane_changes"],
        rewires=plan["rewires"],
    )

    diff = diff_network_files(DEMO_INPX, mutated, base_label="demo", target_label="demo-mutated")

    # non-vacuity: every dimension of the round-trip carries at least one edit
    assert injected["removed_links"] and injected["removed_connectors"]
    assert injected["lane_changes"] and injected["rewires"]

    assert _recovered(diff) == injected
    # and nothing else: no phantoms in the other direction
    assert diff.links.added == [] and diff.connectors.added == []
    assert diff.reclassified == []


# ---------------------------------------------------------------------------
# (3b) The real customer network — the acceptance evidence
# ---------------------------------------------------------------------------

REAL_INPX = _real_inpx()
requires_real_network = pytest.mark.skipif(
    REAL_INPX is None,
    reason="no uploaded customer network on this machine (runtime/workspaces/*/uploads/current/current.inpx)",
)


@requires_real_network
def test_real_network_self_diff_control_is_empty(tmp_path):
    """The control: the same network against itself must produce nothing."""
    local = tmp_path / "current.inpx"
    shutil.copyfile(REAL_INPX, local)

    diff = diff_network_files(local, local, base_label="murdoch", target_label="murdoch-copy")

    assert diff.ok is True
    assert diff.change_count == 0 and diff.has_changes is False
    assert diff.links.added == [] and diff.links.removed == []
    assert diff.links.lane_count_changed == []
    assert diff.connectors.added == [] and diff.connectors.removed == []
    assert diff.connectors.rewired == [] and diff.connectors.lane_count_changed == []
    assert diff.reclassified == []
    assert all(not node.changed for node in diff.node_movements)
    assert all(not cov.changed for cov in diff.signal_coverage)
    assert diff.position.geometry_differs_count == 0
    assert diff.finding_codes(DiffSeverity.WARN) == []
    # the real network is the 312-link Murdoch model
    assert diff.base.link_count == diff.base.plain_link_count + diff.base.connector_count
    assert diff.base.connector_count > 0 and diff.base.node_count > 0


@requires_real_network
def test_real_network_mutation_round_trip_recovers_exactly_the_injected_set(tmp_path):
    """The acceptance evidence: a known change set against the real network,
    recovered with zero misses and zero phantoms."""
    original = tmp_path / "original.inpx"
    shutil.copyfile(REAL_INPX, original)
    plan = _plan_mutation(original)
    mutated = tmp_path / "mutated.inpx"
    injected = _mutate(
        original,
        mutated,
        delete_links=plan["delete_links"],
        delete_connectors=plan["delete_connectors"],
        lane_changes=plan["lane_changes"],
        rewires=plan["rewires"],
    )

    diff = diff_network_files(
        original, mutated, base_label="murdoch-original", target_label="murdoch-mutated"
    )

    assert len(injected["removed_links"]) == 3
    assert len(injected["removed_connectors"]) == 2
    assert len(injected["lane_changes"]) == 4
    assert len(injected["rewires"]) == 2

    # no misses
    assert _recovered(diff) == injected
    # no phantoms
    assert diff.links.added == []
    assert diff.connectors.added == []
    assert diff.reclassified == []
    assert diff.base.link_count - diff.target.link_count == 5
    # the deletions leave real dangling references behind, and we say so rather
    # than pretending the mutated network is coherent
    assert "connector_reference_dangling" in diff.finding_codes(DiffSeverity.WARN)
    # position is still not reported, on the real network as everywhere else
    assert diff.position.reported is False


@requires_real_network
def test_real_network_report_renders(tmp_path):
    local = tmp_path / "current.inpx"
    shutil.copyfile(REAL_INPX, local)

    text = render_diff_report(
        diff_network_files(local, local, base_label="reference", target_label="generated"),
        title="Murdoch benchmark diff report v1",
    )

    assert "Murdoch benchmark diff report v1" in text
    assert "Signalized-approach coverage" in text
    assert "TOTAL structural changes: 0" in text

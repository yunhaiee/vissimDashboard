"""Tests for the R1.5 validation gate — roadmap 3.3, ``services/genesis/gate.py``.

The gate's whole value is that it cannot be tricked into a green light, so the
tests are written the same way round: for every REFUSE-tier check there is a
synthetic network that MUST fail it, and for every check there is a network that
passes it, so a check that stopped running would flip a test rather than sit
silently green.

Three properties get their own family, because they are what makes this a gate
rather than a report:

* **A check that cannot run is never a pass** (§1). ``CheckResult.build`` is the
  only place the three states are resolved and the precedence FAIL >
  NOT_CHECKABLE > PASS lives there; §1 pins it and pins the verdict rules that
  ride on it, including "an offline run can never report an unqualified PASS".
* **A vacuous population is a gap, not a pass** (§6, §7, §10). Zero signal heads
  does not mean every signalised approach has one; zero vehicle inputs does not
  mean demand is covered. These are the shape of the two silent failures this
  repository has already paid for, and each has a test that would catch a
  regression to "no objects, therefore fine".
* **V1's evidence must be about THIS file** (§2). A load oracle for a different
  build is not weak evidence, it is none.

Fixtures are synthetic XML written here, except §11 which runs the gate over the
REAL emitter's output (a 4-way crossroads through ``osm`` → ``draft`` → ``emit``)
so the hand-written fixtures cannot drift away from what the generator actually
writes. No client geometry and no client-derived number appears anywhere in this
file.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services.genesis.gate import (
    CheckClause,
    CheckResult,
    CheckState,
    CheckTier,
    GateFinding,
    GateGap,
    GateVerdict,
    LoadOracle,
    Provenance,
    render_report,
    run_gate,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
GATE_CLI = BACKEND_ROOT / "tools" / "genesis_validation_gate.py"


# ---------------------------------------------------------------------------
# Synthetic network builder
#
# Only tags ``services.genesis.emit`` actually writes, so the fixtures live
# inside the same reference-table scope V2 was measured against. A fixture that
# used a foreign tag would make every V2 assertion below read NOT_CHECKABLE —
# which is exactly the behaviour §3 pins deliberately in one test.
# ---------------------------------------------------------------------------

_SUPPORT = """  <displayTypes><displayType no="1" name="Road gray"/></displayTypes>
  <drivingBehaviors>
    <drivingBehavior no="1" name="Urban" w99cc1Distr="2" zipperOvtTmDistr="200"/>
  </drivingBehaviors>
  <timeDistributions>
    <timeDistribution mean="0.9" no="2" stdDev="0" type="NORMAL"/>
    <timeDistribution mean="3" no="200" stdDev="0.5" type="NORMAL"/>
  </timeDistributions>
  <laneMarkingTypes><laneMarkingType name="Default" no="1"/></laneMarkingTypes>
  <levels><level name="Base" no="1" zCoord="0"/></levels>
  <linkBehaviorTypes>
    <linkBehaviorType drivBehavDef="1" name="Urban" no="1"/>
  </linkBehaviorTypes>
"""


def _points(points: list[tuple[float, float]]) -> str:
    inner = "".join(
        f'<linkPolyPoint x="{x}" y="{y}" zOffset="0"/>' for x, y in points
    )
    return f"<geometry><linkPolyPts>{inner}</linkPolyPts></geometry>"


def link(
    no: int,
    points: list[tuple[float, float]],
    *,
    lanes: int = 1,
    name: str | None = None,
    lane_change_distance: float | None = 200.0,
) -> str:
    """One plain link: ``lanes`` lanes, the given polyline, the emitter's refs."""
    lane_xml = "".join('<lane markingType="1" width="3.5"/>' for _ in range(lanes))
    lnchg = (
        f' lnChgDist="{lane_change_distance:g}"' if lane_change_distance is not None else ""
    )
    return (
        f'<link displayType="1" level="1" linkBehavType="1"{lnchg} '
        f'name="{name or f"link {no}"}" no="{no}">'
        f"{_points(points)}<lanes>{lane_xml}</lanes></link>"
    )


def connector(
    no: int,
    from_link: int,
    to_link: int,
    points: list[tuple[float, float]],
    *,
    lane_pairs: list[tuple[int, int]] | None = None,
    from_pos: float = 100.0,
    to_pos: float = 0.0,
    name: str | None = None,
) -> str:
    """One connector, with an explicit lane mapping (default: lane 1 to lane 1)."""
    pairs = lane_pairs or [(1, 1)]
    lane_xml = "".join(
        '<lane markingType="1">'
        f'<fromLanes><objectRef key="{from_link} {a}"/></fromLanes>'
        f'<toLanes><objectRef key="{to_link} {b}"/></toLanes>'
        "</lane>"
        for a, b in pairs
    )
    return (
        f'<link displayType="1" linkBehavType="1" lnChgDist="200" '
        f'name="{name or f"connector {no}"}" no="{no}">'
        f'<fromLinkEndPt link="{from_link}" pos="{from_pos}"/>'
        f"{_points(points)}<lanes>{lane_xml}</lanes>"
        f'<toLinkEndPt link="{to_link}" pos="{to_pos}"/></link>'
    )


def node(no: int, polygon: list[tuple[float, float]]) -> str:
    inner = "".join(f'<point x="{x}" y="{y}"/>' for x, y in polygon)
    return f'<node name="node {no}" no="{no}"><geometry><points>{inner}</points></geometry></node>'


def network(
    *,
    links: list[str],
    nodes: list[str] | None = None,
    extra: str = "",
    support: str = _SUPPORT,
) -> str:
    node_xml = f"<nodes>{''.join(nodes)}</nodes>" if nodes else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<network version="1403" vissimVersion="2026.00-09">\n'
        f"{support}"
        f"<links>{''.join(links)}</links>{node_xml}{extra}\n"
        "</network>\n"
    )


def write(tmp_path: Path, xml: str, name: str = "net.inpx") -> Path:
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8", newline="")
    return path


def straight_pair(*, a_lanes: int = 1, b_lanes: int = 1, a_len: float = 100.0) -> list[str]:
    """Link 1 → connector 10001 → link 2. The smallest drivable network."""
    return [
        link(1, [(0.0, 0.0), (a_len, 0.0)], lanes=a_lanes),
        link(2, [(a_len + 4.0, 0.0), (a_len + 104.0, 0.0)], lanes=b_lanes),
        connector(
            10001,
            1,
            2,
            [(a_len, 0.0), (a_len + 4.0, 0.0)],
            lane_pairs=[(1, 1)],
            from_pos=a_len,
        ),
    ]


def gate(path: Path, **kwargs):
    return run_gate(path, **kwargs)


def codes(result, check_id: str) -> list[str]:
    return [f.code for f in result.check(check_id).findings]


def gap_codes(result, check_id: str) -> list[str]:
    return [g.code for g in result.check(check_id).gaps]


# ---------------------------------------------------------------------------
# §1 The state machine — the reason this is a gate
# ---------------------------------------------------------------------------


def test_a_check_with_no_findings_and_no_gaps_passes():
    result = CheckResult.build("V2", CheckTier.REFUSE)
    assert result.state is CheckState.PASS


def test_a_gap_alone_is_not_checkable_never_a_pass():
    result = CheckResult.build("V2", CheckTier.REFUSE, gaps=[GateGap(code="x")])
    assert result.state is CheckState.NOT_CHECKABLE
    assert result.state is not CheckState.PASS


def test_a_finding_beats_a_gap_because_a_proven_fault_is_a_definite_answer():
    result = CheckResult.build(
        "V3",
        CheckTier.REFUSE,
        findings=[GateFinding(code="f", subject="1")],
        gaps=[GateGap(code="x")],
    )
    assert result.state is CheckState.FAIL


def test_a_check_is_never_a_pass_over_a_clause_that_could_not_run():
    """The module's own doctrine, enforced in ``build`` instead of trusted.

    V5 and V6 both reported ``PASS`` with a ``NOT_CHECKABLE`` clause printed
    directly underneath, because each had appended its "could not certify this"
    gap to the CLAUSE's list and not to the check's — a self-contradictory report
    that anything checking ``state == PASS`` reads as an unqualified pass."""
    result = CheckResult.build(
        "V5",
        CheckTier.REFUSE,
        clauses=[
            CheckClause(code="a", state=CheckState.PASS),
            CheckClause(code="b", state=CheckState.NOT_CHECKABLE),
        ],
    )
    assert result.state is CheckState.NOT_CHECKABLE

    failed = CheckResult.build(
        "V5",
        CheckTier.REFUSE,
        clauses=[CheckClause(code="a", state=CheckState.FAIL)],
    )
    assert failed.state is CheckState.FAIL


def test_a_warn_clause_inside_a_refuse_check_never_fails_it():
    """R1.5 gives V6 one warn clause (capacity) inside a refuse-tier check."""
    result = CheckResult.build(
        "V6", CheckTier.REFUSE, warnings=[GateFinding(code="w", subject="1")]
    )
    assert result.state is CheckState.PASS
    assert len(result.warnings) == 1


def test_a_not_checkable_refuse_tier_check_blocks_the_gate(tmp_path):
    """The whole design rule, end to end: no V1 evidence, so no unqualified pass."""
    path = write(tmp_path, network(links=straight_pair()))
    result = gate(path)
    assert result.check("V1").state is CheckState.NOT_CHECKABLE
    assert result.verdict is GateVerdict.INCOMPLETE
    assert result.verdict is not GateVerdict.PASS
    assert "V1" in [c.id for c in result.blocking]


def test_a_refuse_tier_fault_outranks_an_unrunnable_check(tmp_path):
    xml = network(
        links=[
            link(1, [(0.0, 0.0), (100.0, 0.0)]),
            link(2, [(200.0, 0.0), (300.0, 0.0)]),  # orphan: nothing connects it
            connector(10001, 1, 2, [(100.0, 0.0), (200.0, 0.0)], from_pos=100.0),
        ]
    )
    # Give link 2 an orphan sibling so V3 fails while V1/V5/V6 are unrunnable.
    xml = xml.replace(
        "</links>", link(3, [(0.0, 500.0), (100.0, 500.0)]) + "</links>"
    )
    result = gate(write(tmp_path, xml))
    assert result.check("V3").state is CheckState.FAIL
    assert result.verdict is GateVerdict.REFUSED


def test_warn_tier_findings_never_change_the_verdict(tmp_path):
    """A duplicated polyline fails V7, which is warn tier; the verdict must not
    become REFUSED because of it."""
    links = straight_pair()
    links.append(link(3, [(0.0, 0.0), (100.0, 0.0)], name="twin of link 1"))
    links.append(connector(10002, 3, 2, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0))
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V7").state is CheckState.FAIL
    assert "v7_links_share_one_polyline" in codes(result, "V7")
    assert result.verdict is not GateVerdict.REFUSED


def test_an_unreadable_file_is_incomplete_not_a_fault(tmp_path):
    """'Not a Vissim network' and 'a network with a fault' are different answers."""
    path = tmp_path / "broken.inpx"
    path.write_text("<network><notlinks/></network>", encoding="utf-8")
    result = gate(path)
    assert result.verdict is GateVerdict.INCOMPLETE
    assert {c.state for c in result.checks} == {CheckState.NOT_CHECKABLE}
    assert gap_codes(result, "V3") == ["gate_network_unreadable"]


def test_the_result_is_deterministic(tmp_path):
    path = write(tmp_path, network(links=straight_pair()))
    first = json.dumps(gate(path).as_dict(), sort_keys=True, ensure_ascii=False)
    second = json.dumps(gate(path).as_dict(), sort_keys=True, ensure_ascii=False)
    assert first == second


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_the_report_renders_in_all_three_languages(tmp_path, language):
    path = write(tmp_path, network(links=straight_pair()))
    text = render_report(gate(path), language=language)
    assert "V1" in text and "V9" in text
    assert len(text.splitlines()) > 20


# ---------------------------------------------------------------------------
# §2 V1 — the load oracle, and the empty-load trap it exists for
# ---------------------------------------------------------------------------


def _probe_state(network_path: Path, **overrides) -> dict:
    """One run in ``genesis_open_live_check.py``'s own state.json shape."""
    com = {
        "network": str(network_path),
        "load_ok": True,
        "save_ok": True,
        "err_after_load": {"files": [], "new_files": []},
        "com_topology": {
            "link_collection_count": 3,
            "is_conn_counts": {"plain_links": 2, "connectors": 1},
            "nodes_count": 0,
        },
        "version": {"att_values": {"Version": "PTV Vissim Test"}},
    }
    com.update(overrides)
    return {
        "runs": {
            "run_a": {
                "label": "run_a",
                "started_utc": "2026-08-11T00:00:00+00:00",
                "network": str(network_path),
                "run": {"com": com},
            }
        }
    }


def test_v1_without_evidence_is_not_checkable(tmp_path):
    path = write(tmp_path, network(links=straight_pair()))
    result = gate(path)
    assert result.check("V1").state is CheckState.NOT_CHECKABLE
    assert gap_codes(result, "V1") == ["v1_no_live_evidence"]


def test_v1_passes_when_a_real_vissim_loaded_this_exact_file(tmp_path):
    path = write(tmp_path, network(links=straight_pair()))
    oracle, reason = LoadOracle.from_probe_state(_probe_state(path))
    assert reason is None
    result = gate(path, load_oracle=oracle)
    assert result.check("V1").state is CheckState.PASS
    assert result.check("V1").evidence["com_link_collection_count"] == 3


def test_v1_refuses_evidence_about_a_different_file(tmp_path):
    """A load oracle for another build is not weak evidence, it is none."""
    path = write(tmp_path, network(links=straight_pair()))
    other = write(tmp_path, network(links=straight_pair(a_len=120.0)), name="other.inpx")
    oracle, _ = LoadOracle.from_probe_state(_probe_state(other))
    result = gate(path, load_oracle=oracle)
    assert result.check("V1").state is CheckState.NOT_CHECKABLE
    assert gap_codes(result, "V1") == ["v1_oracle_names_another_network"]


def test_v1_fails_when_vissim_refused_the_load(tmp_path):
    path = write(tmp_path, network(links=straight_pair()))
    oracle, _ = LoadOracle.from_probe_state(
        _probe_state(path, load_ok=False, load_error="The network file could not be loaded.")
    )
    result = gate(path, load_oracle=oracle)
    assert result.check("V1").state is CheckState.FAIL
    assert codes(result, "V1") == ["v1_load_refused"]


def test_v1_catches_the_empty_load_vissim_reports_as_success(tmp_path):
    """MEASURED 2026-08-06: a file stripped of its support containers returns
    ``load_ok=True`` with ``Net.Links.Count == 0``. A load oracle that stops at
    "no exception" certifies exactly that."""
    path = write(tmp_path, network(links=straight_pair()))
    oracle, _ = LoadOracle.from_probe_state(
        _probe_state(
            path,
            com_topology={
                "link_collection_count": 0,
                "is_conn_counts": {"plain_links": 0, "connectors": 0},
                "nodes_count": 0,
            },
        )
    )
    result = gate(path, load_oracle=oracle)
    assert result.check("V1").state is CheckState.FAIL
    assert "v1_link_count_mismatch" in codes(result, "V1")
    assert "v1_plain_link_count_mismatch" in codes(result, "V1")


def test_v1_without_an_object_census_is_not_checkable_not_a_pass(tmp_path):
    path = write(tmp_path, network(links=straight_pair()))
    oracle, _ = LoadOracle.from_probe_state(_probe_state(path, com_topology={}))
    result = gate(path, load_oracle=oracle)
    assert result.check("V1").state is CheckState.NOT_CHECKABLE
    assert "v1_no_object_census" in gap_codes(result, "V1")


def test_v1_fails_when_vissim_wrote_an_err_file(tmp_path):
    path = write(tmp_path, network(links=straight_pair()))
    oracle, _ = LoadOracle.from_probe_state(
        _probe_state(
            path,
            err_after_load={
                "files": [{"name": "net.err", "bytes": 12, "text": "bad lane ref"}],
                "new_files": ["net.err"],
            },
        )
    )
    result = gate(path, load_oracle=oracle)
    assert result.check("V1").state is CheckState.FAIL
    assert "v1_err_file_written" in codes(result, "V1")


def test_load_oracle_reports_why_a_state_file_yields_nothing(tmp_path):
    oracle, reason = LoadOracle.from_probe_state({"runs": {}})
    assert oracle is None and reason == "no_runs_in_state"
    oracle, reason = LoadOracle.from_probe_state(
        {"runs": {"a": {"started_utc": "x", "run": {"com": {}}}}}
    )
    assert oracle is None and reason == "run_carries_no_load_result:a"


# ---------------------------------------------------------------------------
# §3 V2 — reference integrity
# ---------------------------------------------------------------------------


def test_v2_passes_on_a_file_whose_references_all_resolve(tmp_path):
    result = gate(write(tmp_path, network(links=straight_pair())))
    assert result.check("V2").state is CheckState.PASS
    assert result.check("V2").evidence["references_checked"] > 0


def test_v2_fails_on_a_reference_two_hops_from_the_geometry(tmp_path):
    """The measured killer: an undeclared ``w99cc1Distr`` took the whole
    ``drivingBehaviors`` container, then ``linkBehaviorType``, then every link —
    with ``load_ok=True`` and no ``.err``."""
    xml = network(links=straight_pair()).replace('w99cc1Distr="2"', 'w99cc1Distr="999"')
    result = gate(write(tmp_path, xml))
    assert result.check("V2").state is CheckState.FAIL
    assert "v2_unresolved_reference" in codes(result, "V2")


def test_v2_fails_on_a_connector_endpoint_that_names_no_link(tmp_path):
    links = straight_pair()
    links[2] = connector(10001, 1, 77, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0)
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V2").state is CheckState.FAIL
    assert "v2_connector_endpoint_dangling" in codes(result, "V2")


def test_v2_fails_on_a_lane_reference_outside_the_link(tmp_path):
    links = straight_pair()
    links[2] = connector(
        10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], lane_pairs=[(3, 1)], from_pos=100.0
    )
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V2").state is CheckState.FAIL
    assert "v2_lane_index_out_of_range" in codes(result, "V2")


def test_v2_refuses_to_claim_integrity_for_a_tag_it_never_measured(tmp_path):
    """A file carrying an element outside the emitter's measured inventory is a
    reference surface this table was never built against — NOT_CHECKABLE, not a
    pass."""
    xml = network(links=straight_pair(), extra="<parkingLots><parkingLot no='1'/></parkingLots>")
    result = gate(write(tmp_path, xml))
    assert result.check("V2").state is CheckState.NOT_CHECKABLE
    assert "v2_reference_table_not_measured_for_tag" in gap_codes(result, "V2")


# -- the same element name, seven different referents ------------------------
#
# ``<intObjectRef key="…"/>`` is Vissim's generic "this set contains that
# object" element. Its referent is named by the CONTAINER, and a table keyed on
# the element name alone resolves every one of them against whichever id space
# it happened to be measured on. This fixture is that shape, authored
# synthetically: five containers, five different referents, and — deliberately —
# ids that COLLIDE across the id spaces, so a context-blind resolver both
# fabricates failures and grades some references against the wrong universe.


def _multi_context_refs() -> str:
    """Five ``<intObjectRef>`` containers whose referents are five different
    object classes.

    The ids are chosen so the fixture DISCRIMINATES. Links are 1, 2 and 10001;
    every non-``<linkSeq>`` reference below names 4, 5, 6 or 7 — valid in its own
    id space and in NO other, least of all the link table. A context-blind,
    link-keyed resolver (the shape this table had until 2026-08-11) therefore
    fabricates a finding for every one of them, so "all four resolve" is a
    statement about context-awareness rather than about ids that happen to be
    valid everywhere."""
    return (
        "<vehicleClasses><vehicleClass name='HGV' no='7'>"
        "<vehTypes><intObjectRef key='4'/></vehTypes></vehicleClass></vehicleClasses>"
        "<pedestrianTypes><pedestrianType name='ped' no='5'/></pedestrianTypes>"
        "<pedestrianClasses><pedestrianClass name='pc' no='5'>"
        "<pedTypes><intObjectRef key='5'/></pedTypes></pedestrianClass></pedestrianClasses>"
        "<wttFiles><intObjectRef key='6'/></wttFiles>"
        "<vehicleRoutingDecisionsStatic>"
        "<vehicleRoutingDecisionStatic link='1' name='' no='1' pos='10'>"
        "<vehRoutSta><vehicleRouteStatic destLink='2' name='' no='1' relFlow='1'>"
        "<linkSeq><intObjectRef key='10001'/><intObjectRef key='2'/></linkSeq>"
        "</vehicleRouteStatic></vehRoutSta>"
        "</vehicleRoutingDecisionStatic></vehicleRoutingDecisionsStatic>"
        "<lanes><lane><blockedVehClasses><intObjectRef key='7'/></blockedVehClasses></lane></lanes>"
    )


def _multi_context_support() -> str:
    return (
        "<vehicleTypes><vehicleType category='CAR' colorDistr1='1' name='Car' no='4'/>"
        "</vehicleTypes>"
        "<colorDistributions><colorDistribution name='c' no='1'/></colorDistributions>"
        "<wttFile name='wtt' no='6'/>"
    )


def test_v2_resolves_the_same_element_name_against_the_container_that_names_it(tmp_path):
    """Every reference in the fixture resolves — because each is judged against
    the id space its OWN container names. Keyed on the element name alone,
    ``<vehTypes>``/``<pedTypes>``/``<wttFiles>``/``<blockedVehClasses>`` would
    all be resolved against the LINK table, which is the defect that put 253
    fabricated findings on a real consultant model — and none of the four ids
    here is a link id, so that resolver would fabricate four findings."""
    xml = network(
        links=straight_pair(),
        extra=_multi_context_refs(),
        support=_SUPPORT + _multi_context_support(),
    )
    result = gate(write(tmp_path, xml))
    assert codes(result, "V2") == []
    assert result.check("V2").evidence["unmeasured_reference_contexts"] == 0


def test_v2_never_grades_a_reference_against_the_wrong_id_space(tmp_path):
    """The other half of the same defect, and the quieter one.

    ``<vehTypes><intObjectRef key='1'/>`` is a TRUE COLLISION: 1 is not a vehicle
    type in this file (the only one is 4) and it IS link 1, so a link-keyed
    resolver calls the reference resolved and says nothing — the failure mode
    credited with 3 silent false negatives on the real consultant model. The
    earlier version of this test used ``key='9'``, which is not valid in ANY id
    space the file declares, so the pre-fix resolver would have failed it too and
    the test only ever proved that the ERROR MESSAGE names the right target."""
    collision = _multi_context_refs().replace(
        "<vehTypes><intObjectRef key='4'/>", "<vehTypes><intObjectRef key='1'/>"
    )
    passing = gate(
        write(
            tmp_path,
            network(
                links=straight_pair(),
                extra=_multi_context_refs(),
                support=_SUPPORT + _multi_context_support(),
            ),
            name="ok.inpx",
        )
    )
    assert codes(passing, "V2") == []

    xml = network(
        links=straight_pair(),
        extra=collision,
        support=_SUPPORT + _multi_context_support(),
    )
    result = gate(write(tmp_path, xml, name="bad.inpx"))
    finding = next(f for f in result.check("V2").findings if f.code == "v2_unresolved_reference")
    assert finding.context["target"] == "vehicleType"
    assert finding.context["value"] == "1"
    assert finding.subject.startswith("vehTypes/intObjectRef")
    # The collision is real: the same value IS a declared link in this file.
    assert "1" in {el.get("no") for el in ET.fromstring(xml).findall(".//links/link")}


def test_v2_names_a_reference_context_it_has_not_measured_instead_of_guessing(tmp_path):
    """``<intObjectRef>`` is a reference by construction, so an occurrence under
    a container the table has never measured is a reference we KNOW we cannot
    resolve. It is named and left unresolved — never resolved against the
    nearest plausible id space."""
    xml = network(
        links=straight_pair(),
        extra="<evalGrps><intObjectRef key='1'/></evalGrps>",
    )
    result = gate(write(tmp_path, xml))
    assert "v2_reference_context_not_measured" in gap_codes(result, "V2")
    gap = next(g for g in result.check("V2").gaps if g.code == "v2_reference_context_not_measured")
    assert gap.context["contexts"] == ["evalGrps/intObjectRef/@key"]
    assert codes(result, "V2") == []
    assert result.check("V2").state is CheckState.NOT_CHECKABLE


def test_v2_names_a_measured_attribute_appearing_under_an_unmeasured_parent(tmp_path):
    """``@destLink`` is a measured reference surface under two containers. Under
    a third it is the same attribute in a context nobody measured, and the check
    says so rather than assuming the referent carries over."""
    xml = network(
        links=straight_pair(),
        extra="<vehRoutPart><vehicleRouteStatic destLink='2' name='' no='1'/></vehRoutPart>",
    )
    result = gate(write(tmp_path, xml))
    gap = next(g for g in result.check("V2").gaps if g.code == "v2_reference_context_not_measured")
    assert "vehRoutPart/vehicleRouteStatic/@destLink" in gap.context["contexts"]


def test_v2_resolves_an_object_class_vissim_renamed_between_schema_versions(tmp_path):
    """``desSpeedDistr`` points at ``<speedDistribution>`` in a Vissim 2026 file
    and at ``<desSpeedDistribution>`` in a 2024 one — the same object class, two
    spellings. Measured over 804 real files that declare one: 379 carry only the
    2024 spelling, 425 only the 2026 spelling, NOT ONE carries both, so the union
    cannot launder a dangling reference. Keyed on the 2026 spelling alone (as it
    was), V2 fabricated one finding per composition flow on every 2024 file."""
    demand_2024 = (
        "<vehicleTypes><vehicleType category='CAR' name='Car' no='1'/></vehicleTypes>"
        "<desSpeedDistributions><desSpeedDistribution name='50' no='50'/>"
        "</desSpeedDistributions>"
        "<vehicleCompositions><vehicleComposition name='c1' no='1'><vehCompRelFlows>"
        "<vehicleCompositionRelativeFlow desSpeedDistr='50' relFlow='1.0' vehType='1'/>"
        "</vehCompRelFlows></vehicleComposition></vehicleCompositions>"
        "<vehicleInputs><vehicleInput link='1' name='' no='1'><timeIntVehVols>"
        "<timeIntervalVehVolume timeInt='1 0' vehComp='1' volume='100'/>"
        "</timeIntVehVols></vehicleInput></vehicleInputs>"
    )
    result = gate(write(tmp_path, network(links=straight_pair(), extra=demand_2024)))
    assert [f.code for f in result.check("V2").findings] == []


def test_v2_still_refuses_a_speed_distribution_neither_spelling_declares(tmp_path):
    """The union is two spellings of one class, not an amnesty."""
    demand = (
        "<vehicleTypes><vehicleType category='CAR' name='Car' no='1'/></vehicleTypes>"
        "<desSpeedDistributions><desSpeedDistribution name='50' no='50'/>"
        "</desSpeedDistributions>"
        "<vehicleCompositions><vehicleComposition name='c1' no='1'><vehCompRelFlows>"
        "<vehicleCompositionRelativeFlow desSpeedDistr='99' relFlow='1.0' vehType='1'/>"
        "</vehCompRelFlows></vehicleComposition></vehicleCompositions>"
        "<vehicleInputs><vehicleInput link='1' name='' no='1'><timeIntVehVols>"
        "<timeIntervalVehVolume timeInt='1 0' vehComp='1' volume='100'/>"
        "</timeIntVehVols></vehicleInput></vehicleInputs>"
    )
    result = gate(write(tmp_path, network(links=straight_pair(), extra=demand)))
    finding = next(f for f in result.check("V2").findings if f.code == "v2_unresolved_reference")
    assert finding.context["target"] == "speedDistribution | desSpeedDistribution"


def test_v2_names_only_the_attributes_it_has_reason_to_call_references(tmp_path):
    """The unmeasured-context list has to stay actionable. ``@vehComp`` is a
    measured reference surface and is named under an unmeasured parent;
    ``@timeInt`` is published as deliberately unresolvable with its reason, and
    ``@volume`` is a number. Naming every numeric attribute would bury the short
    list under noise — the probe's own output is the evidence, where
    ``occupancyDistribution/@mean`` resolves unanimously across 188 files and is
    a mean."""
    xml = network(
        links=straight_pair(),
        extra="<odd><timeIntervalVehVolume timeInt='1 0' vehComp='1' volume='100'/></odd>",
    )
    result = gate(write(tmp_path, xml))
    gap = next(g for g in result.check("V2").gaps if g.code == "v2_reference_context_not_measured")
    assert gap.context["contexts"] == ["odd/timeIntervalVehVolume/@vehComp"]


def test_losing_the_generator_census_grades_the_same_defect_the_same_way(tmp_path):
    """The safety argument for provenance scoping, pinned at the strength that
    matters.

    The old version of this test asserted only ``verdict is not PASS``, which is
    a property of the ``GateVerdict`` enum and NOT the property the pipeline
    turns on: ``genesis_jobs.gate_and_publish`` gates registration on ``verdict
    is REFUSED``, so INCOMPLETE — exactly what a lenient run produced — still
    became the workspace's live dataset. The guarantee is therefore stated as
    'the same defect, the same verdict, census or no census', and
    ``test_genesis_jobs.py`` pins the publication half at its own seam."""
    xml = network(
        links=straight_pair(),
        nodes=[node(1, [(95.0, -10.0), (110.0, -10.0), (110.0, 10.0), (95.0, 10.0)])],
        extra=_signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)]) + _demand(),
        support=_SUPPORT + _DEMAND_SUPPORT,
    )
    path = write(tmp_path, xml)
    without_census = gate(path)
    # The ONLY refuse-tier fault in this file is the headless signal group, so
    # the verdicts below are about provenance and nothing else.
    assert [c.id for c in without_census.refuse_tier if c.state is CheckState.FAIL] == ["V5"]
    assert without_census.provenance.state is Provenance.UNKNOWN
    assert without_census.check("V5").state is CheckState.FAIL
    assert without_census.verdict is GateVerdict.REFUSED

    with_census = gate(path, draft_metadata=_census([], xml))
    assert with_census.provenance.state is Provenance.OURS
    assert with_census.check("V5").state is CheckState.FAIL
    assert with_census.verdict is GateVerdict.REFUSED


def test_the_report_shows_which_sub_questions_ran(tmp_path):
    path = write(tmp_path, network(links=straight_pair(), extra=_demand()))
    text = render_report(gate(path), language="en")
    assert "[PASS] no orphan links and no unreachable or trapped zone" in text
    assert "[NOT CHECKABLE] every point traffic departs at is the model boundary" in text


def test_v2_reports_which_of_its_own_clauses_ran(tmp_path):
    """The whole file resolves; only the surface-measured clause is grey."""
    xml = network(links=straight_pair(), extra="<parkingLots><parkingLot no='1'/></parkingLots>")
    result = gate(write(tmp_path, xml))
    states = {c.code: c.state for c in result.check("V2").clauses}
    assert states["v2_references_resolve"] is CheckState.PASS
    assert states["v2_surface_fully_measured"] is CheckState.NOT_CHECKABLE


# ---------------------------------------------------------------------------
# §4 V3 — connectivity
# ---------------------------------------------------------------------------


def _census(entries: list[dict], network_xml: str) -> dict:
    """A generator census that DESCRIBES the network it is handed.

    The gate admits a ``*.genesis.json`` only when the link, connector and node
    ids it names are this file's own — a census about another build is not weak
    evidence about this one, it is none, the same rule V1 applies to its load
    oracle. So a fixture that wants to be graded as OURS has to identify itself,
    and the ids are read off the fixture rather than hand-listed, so a test that
    changes its network cannot quietly stop being ours.
    """
    root = ET.fromstring(network_xml)
    links: list[dict] = []
    connectors: list[dict] = []
    for element in root.findall(".//links/link"):
        is_connector = (
            element.find("fromLinkEndPt") is not None
            or element.find("toLinkEndPt") is not None
        )
        (connectors if is_connector else links).append({"id": int(element.get("no"))})
    return {
        "links": links,
        "connectors": connectors,
        "nodes": [{"id": int(e.get("no"))} for e in root.findall(".//nodes/node")],
        "metadata": {"connectivity": {"dangling_approaches": entries}},
    }


#: What ``_demand()``'s composition points at. Without it V2 refuses the file
#: for two dangling references, and a test asserting REFUSED would pass for a
#: reason it never meant to exercise — which is exactly how a provenance test
#: can look green while the switch under it is broken.
_DEMAND_SUPPORT = (
    "<speedDistributions><speedDistribution name='50' no='50'/></speedDistributions>"
    "<vehicleTypes><vehicleType category='CAR' name='Car' no='1'/></vehicleTypes>"
)


def _foreign(extra: str = "") -> str:
    """Whatever makes a fixture PROVABLY not this generator's output.

    ``<conflictAreas>`` is a top-level container ``services.genesis.emit`` has no
    code to write (it is not in ``schema.CONTAINER_ORDER``, and ``build_tree``
    raises if it ever builds one that is not), which is the positive evidence
    :class:`Provenance` requires before it will soften a refuse-tier clause.
    Every real consultant model carries 20-odd of them; the first one this gate
    was pointed at carries 21.
    """
    return f"{extra}<conflictAreas><conflictArea no='1' status='PASSIVE'/></conflictAreas>"


def test_v3_passes_when_traffic_can_get_in_through_and_out(tmp_path):
    """A pass needs BOTH halves: the census that says where departures are
    legitimate, and a vehicle input to ask the reachability question about."""
    xml = network(links=straight_pair(), extra=_demand())
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert result.check("V3").state is CheckState.PASS


def test_v3_with_no_vehicle_inputs_cannot_answer_its_own_first_clause(tmp_path):
    xml = network(links=straight_pair())
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert result.check("V3").state is CheckState.NOT_CHECKABLE
    assert gap_codes(result, "V3") == ["v3_no_vehicle_inputs"]


def test_v3_reports_the_clause_that_ran_even_when_another_could_not(tmp_path):
    """On a file with no generator census V3 is NOT_CHECKABLE — and its
    reachability half still ran in full and EARNED its answer. Reporting the
    whole check as one grey box threw that away. The check's own state is
    unchanged: a clause that could not run never becomes a pass, only a visible
    one."""
    path = write(tmp_path, network(links=straight_pair(), extra=_demand()))
    result = gate(path)
    check = result.check("V3")
    assert check.state is CheckState.NOT_CHECKABLE
    states = {c.code: c.state for c in check.clauses}
    assert states["v3_reachability"] is CheckState.PASS
    assert states["v3_inputs_reach_an_exit"] is CheckState.PASS
    assert states["v3_departures_are_at_the_boundary"] is CheckState.NOT_CHECKABLE


def test_v3_reachability_clause_fails_with_the_check(tmp_path):
    links = straight_pair()
    links.append(link(3, [(0.0, 400.0), (100.0, 400.0)], name="island"))
    xml = network(links=links)
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    states = {c.code: c.state for c in result.check("V3").clauses}
    assert states["v3_reachability"] is CheckState.FAIL
    assert states["v3_departures_are_at_the_boundary"] is CheckState.PASS


def test_v3_fails_on_an_orphan_link(tmp_path):
    links = straight_pair()
    links.append(link(3, [(0.0, 400.0), (100.0, 400.0)], name="island"))
    xml = network(links=links)
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert result.check("V3").state is CheckState.FAIL
    assert "v3_orphan_link" in codes(result, "V3")


def test_v3_fails_on_a_zone_nothing_reaches_and_nothing_leaves(tmp_path):
    """Two links joined to each other in a ring and to nothing else. Every link
    has a connector in AND a connector out, so no dangling-end census can see
    it — only reachability can. This is the fault the gate found on the real
    Murdoch output that four earlier units did not."""
    links = straight_pair()
    links += [
        link(3, [(0.0, 400.0), (100.0, 400.0)], name="pocket a"),
        link(4, [(104.0, 400.0), (204.0, 400.0)], name="pocket b"),
        connector(10002, 3, 4, [(100.0, 400.0), (104.0, 400.0)], from_pos=100.0),
        connector(10003, 4, 3, [(204.0, 400.0), (0.0, 400.0)], from_pos=100.0),
    ]
    xml = network(links=links)
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert result.check("V3").state is CheckState.FAIL
    assert "v3_isolated_zone" in codes(result, "V3")
    finding = next(f for f in result.check("V3").findings if f.code == "v3_isolated_zone")
    assert finding.context["links"] == 2
    assert result.check("V3").evidence["links_in_a_broken_zone"] == 2


def test_v3_without_the_generator_census_cannot_judge_departures(tmp_path):
    result = gate(write(tmp_path, network(links=straight_pair())))
    assert result.check("V3").state is CheckState.NOT_CHECKABLE
    assert "v3_no_generator_census" in gap_codes(result, "V3")


def test_v3_fails_on_a_departure_the_generator_itself_called_a_trap(tmp_path):
    xml = network(links=straight_pair())
    census = _census(
        [
            {"link_id": 2, "link": "Some Street [FWD]", "cause": "one_way_dead_end_unfixable"},
            {"link_id": 9, "link": "Edge Road [FWD]", "cause": "model_boundary_correct"},
        ],
        xml,
    )
    result = gate(write(tmp_path, xml), draft_metadata=census)
    assert result.check("V3").state is CheckState.FAIL
    departures = [f for f in result.check("V3").findings if f.code == "v3_silent_departure_point"]
    assert [f.subject for f in departures] == ["Some Street [FWD]"]


def test_v3_fails_when_a_vehicle_input_can_reach_no_exit(tmp_path):
    links = [
        link(1, [(0.0, 0.0), (100.0, 0.0)]),
        link(2, [(104.0, 0.0), (204.0, 0.0)]),
        connector(10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0),
        connector(10002, 2, 1, [(204.0, 0.0), (0.0, 0.0)], from_pos=100.0),
    ]
    demand = (
        "<vehicleCompositions><vehicleComposition name='c' no='1'><vehCompRelFlows>"
        "<vehicleCompositionRelativeFlow desSpeedDistr='50' relFlow='1' vehType='1'/>"
        "</vehCompRelFlows></vehicleComposition></vehicleCompositions>"
        "<vehicleInputs><vehicleInput link='1' name='' no='1'><timeIntVehVols>"
        "<timeIntervalVehVolume timeInt='1 0' vehComp='1' volume='100'/>"
        "</timeIntVehVols></vehicleInput></vehicleInputs>"
    )
    xml = network(links=links, extra=demand)
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert "v3_input_cannot_reach_an_exit" in codes(result, "V3")


# ---------------------------------------------------------------------------
# §5 V4 — lane continuity
# ---------------------------------------------------------------------------


def test_v4_passes_when_every_lane_is_mapped_and_nothing_is_dropped(tmp_path):
    result = gate(write(tmp_path, network(links=straight_pair())))
    assert result.check("V4").state is CheckState.PASS


def test_v4_fails_on_a_lane_index_the_link_does_not_have(tmp_path):
    links = straight_pair(a_lanes=2)
    links[2] = connector(
        10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], lane_pairs=[(1, 1), (2, 2)], from_pos=100.0
    )
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V4").state is CheckState.FAIL
    assert "v4_lane_index_outside_link" in codes(result, "V4")


def test_v4_fails_when_a_connector_maps_more_lanes_than_the_link_has(tmp_path):
    """Three lanes mapped off a two-lane link: the connector joins lanes that
    are not there."""
    links = straight_pair(a_lanes=2, b_lanes=3)
    links[2] = connector(
        10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], lane_pairs=[(1, 1), (2, 2), (2, 3)],
        from_pos=100.0,
    )
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V4").state is CheckState.FAIL
    finding = next(
        f for f in result.check("V4").findings if f.code == "v4_more_mapped_lanes_than_link_has"
    )
    assert finding.context == {
        "side": "from",
        "mapped_lanes": 3,
        "link": "1",
        "link_lanes": 2,
    }


def test_v4_accepts_a_connector_that_widens_where_the_road_gains_a_lane(tmp_path):
    """Two lanes in, three out: the third connector lane carries no
    ``<fromLanes>`` because it BEGINS at the connector. PTV's own 2026 example
    library ships this shape in files named for it ("Inside Merge 2+2 to 3
    Lanes"), so a connector wider than one of its links is a lane gain, not a
    defect — the clause this replaced manufactured 33 findings on one real
    consultant model, every one of them this."""
    links = straight_pair(a_lanes=2, b_lanes=3)
    lane_xml = (
        '<lane markingType="1"><fromLanes><objectRef key="1 1"/></fromLanes>'
        '<toLanes><objectRef key="2 1"/></toLanes></lane>'
        '<lane markingType="1"><fromLanes><objectRef key="1 2"/></fromLanes>'
        '<toLanes><objectRef key="2 2"/></toLanes></lane>'
        '<lane markingType="1"><toLanes><objectRef key="2 3"/></toLanes></lane>'
    )
    links[2] = (
        '<link displayType="1" linkBehavType="1" lnChgDist="200" name="widening" no="10001">'
        '<fromLinkEndPt link="1" pos="100.0"/>'
        f'{_points([(100.0, 0.0), (104.0, 0.0)])}<lanes>{lane_xml}</lanes>'
        '<toLinkEndPt link="2" pos="0.0"/></link>'
    )
    result = gate(write(tmp_path, network(links=links)))
    assert "v4_more_mapped_lanes_than_link_has" not in codes(result, "V4")
    assert result.check("V4").state is CheckState.PASS


def test_v4_fails_when_a_dropped_lane_has_no_runway(tmp_path):
    """Link 1 is 2 lanes and 50 m long; only lane 1 leaves it, and the file
    itself declares a lane change needs 200 m. 50 m of road exists, so traffic
    in lane 2 cannot merge — in Vissim it waits and is then deleted, silently."""
    links = straight_pair(a_lanes=2, a_len=50.0)
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V4").state is CheckState.FAIL
    finding = next(f for f in result.check("V4").findings if f.code == "v4_lane_drop_without_runway")
    assert finding.context["dropped_lanes"] == [2]
    assert finding.context["required_m"] == 200.0
    assert finding.context["available_m"] == 50.0


def test_v4_accepts_a_dropped_lane_that_has_its_runway(tmp_path):
    """Same fault shape, 250 m of road: the drop is a real road feature, not a
    trap, and the gate must not refuse it."""
    links = straight_pair(a_lanes=2, a_len=250.0)
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V4").state is CheckState.PASS
    assert result.check("V4").evidence["lane_drops"] == 1


def test_v4_counts_upstream_links_towards_the_runway(tmp_path):
    """The runway is not one link's length: 3 x 80 m of road upstream of the
    drop is 240 m and clears the 200 m the file asks for."""
    links = [
        link(1, [(0.0, 0.0), (80.0, 0.0)]),
        link(2, [(84.0, 0.0), (164.0, 0.0)]),
        link(3, [(168.0, 0.0), (248.0, 0.0)], lanes=2),
        link(4, [(252.0, 0.0), (352.0, 0.0)]),
        connector(10001, 1, 2, [(80.0, 0.0), (84.0, 0.0)], from_pos=80.0),
        connector(10002, 2, 3, [(164.0, 0.0), (168.0, 0.0)], from_pos=80.0),
        connector(10003, 3, 4, [(248.0, 0.0), (252.0, 0.0)], from_pos=80.0),
    ]
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V4").state is CheckState.PASS
    # ... and one link short of it, the same drop fails.
    short = [
        link(3, [(168.0, 0.0), (248.0, 0.0)], lanes=2),
        link(4, [(252.0, 0.0), (352.0, 0.0)]),
        connector(10003, 3, 4, [(248.0, 0.0), (252.0, 0.0)], from_pos=80.0),
    ]
    result = gate(write(tmp_path, network(links=short), "short.inpx"))
    assert "v4_lane_drop_without_runway" in codes(result, "V4")


def test_v4_reports_an_exhausted_runway_search_rather_than_a_lower_bound(tmp_path):
    """A partial search produces a LOWER BOUND, and grading a lower bound
    against a threshold invents findings. The budget is reported instead."""
    from services.genesis.gate import GateSettings

    links = [
        link(1, [(0.0, 0.0), (80.0, 0.0)]),
        link(2, [(84.0, 0.0), (164.0, 0.0)]),
        link(3, [(168.0, 0.0), (248.0, 0.0)], lanes=2),
        link(4, [(252.0, 0.0), (352.0, 0.0)]),
        connector(10001, 1, 2, [(80.0, 0.0), (84.0, 0.0)], from_pos=80.0),
        connector(10002, 2, 3, [(164.0, 0.0), (168.0, 0.0)], from_pos=80.0),
        connector(10003, 3, 4, [(248.0, 0.0), (252.0, 0.0)], from_pos=80.0),
    ]
    path = write(tmp_path, network(links=links))
    assert gate(path).check("V4").state is CheckState.PASS
    result = gate(path, settings=GateSettings(lane_change_search_budget=1))
    assert result.check("V4").state is CheckState.NOT_CHECKABLE
    assert "v4_runway_search_budget_exhausted" in gap_codes(result, "V4")


# ---------------------------------------------------------------------------
# §6 V5 — signal/geometry consistency
# ---------------------------------------------------------------------------


def _signals(*, controller: int = 1, groups: tuple[int, ...] = (1,), heads: list[tuple] = ()) -> str:
    group_xml = "".join(
        f'<signalGroup amber="3" minGreen="10" name="" no="{g}" type="NORMAL"/>' for g in groups
    )
    head_xml = "".join(
        f'<signalHead lane="{link_id} {lane}" localNo="{no}" name="" no="{no}" '
        f'pos="{pos}" sg="{sc} {sg}"/>'
        for no, link_id, lane, pos, sc, sg in heads
    )
    return (
        f'<signalControllers><signalController cycTm="60" name="" no="{controller}">'
        f"<sgs>{group_xml}</sgs></signalController></signalControllers>"
        f"<signalHeads>{head_xml}</signalHeads>"
    )


def test_v5_with_no_signal_layer_is_not_checkable_never_a_pass(tmp_path):
    """Zero signal heads does not mean every signalised approach has one."""
    result = gate(write(tmp_path, network(links=straight_pair())))
    assert result.check("V5").state is CheckState.NOT_CHECKABLE
    assert gap_codes(result, "V5") == ["v5_no_signal_layer"]


def test_v5_passes_when_every_group_is_on_a_head_and_every_head_has_a_controller(tmp_path):
    xml = network(
        links=straight_pair(),
        nodes=[node(1, [(95.0, -10.0), (110.0, -10.0), (110.0, 10.0), (95.0, 10.0)])],
        extra=_signals(groups=(1,), heads=[(1, 1, 1, 100.0, 1, 1)]),
    )
    result = gate(write(tmp_path, xml))
    assert result.check("V5").state is CheckState.PASS


def test_v5_fails_on_a_head_whose_controller_is_not_in_the_file(tmp_path):
    xml = network(
        links=straight_pair(), extra=_signals(groups=(1,), heads=[(1, 1, 1, 100.0, 9, 1)])
    )
    result = gate(write(tmp_path, xml))
    assert result.check("V5").state is CheckState.FAIL
    assert "v5_head_without_controller" in codes(result, "V5")


def test_v5_fails_on_a_signal_group_no_head_displays_in_a_network_we_built(tmp_path):
    """A phase that controls nothing: green comes and goes and no vehicle sees
    it. In phasing THIS generator imported that is a contradiction of the
    import, so it refuses."""
    xml = network(
        links=straight_pair(), extra=_signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)])
    )
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert result.check("V5").state is CheckState.FAIL
    assert "v5_signal_group_without_head" in codes(result, "V5")


def test_v5_only_warns_about_a_headless_group_in_a_network_we_did_not_build(tmp_path):
    """The same defect in a file PROVEN to be somebody else's. PTV's OWN 2026
    example library has 304 of 1,387 signal groups displayed by no head —
    pedestrian phases, spare stages, imported rosters — so it cannot be a proven
    fault in a model we did not build. Named in full, never dropped, and never a
    refusal; and the check does not call itself a PASS, because it could not
    certify the question it was asked."""
    xml = network(
        links=straight_pair(),
        extra=_foreign(_signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)])),
    )
    result = gate(write(tmp_path, xml))
    assert result.provenance.state is Provenance.FOREIGN
    assert codes(result, "V5") == []
    assert [f.code for f in result.check("V5").warnings] == ["v5_signal_group_without_head"]
    states = {c.code: c.state for c in result.check("V5").clauses}
    assert states["v5_heads_resolve"] is CheckState.PASS
    assert states["v5_coverage_of_the_imported_phasing"] is CheckState.NOT_CHECKABLE
    assert result.check("V5").state is CheckState.NOT_CHECKABLE


def test_v5_refuses_a_headless_group_when_nothing_proves_whose_network_it_is(tmp_path):
    """The census went missing, and NOTHING in the file says it is not ours.

    Deciding provenance by the mere absence of a ``*.genesis.json`` failed OPEN:
    the same defect that REFUSES with the census present became a warning
    without it, the verdict went REFUSED -> INCOMPLETE, and
    ``gate_and_publish`` registers everything that is not REFUSED. An unproven
    provenance is graded at the strict tier for exactly that reason."""
    xml = network(
        links=straight_pair(), extra=_signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)])
    )
    result = gate(write(tmp_path, xml))
    assert result.provenance.state is Provenance.UNKNOWN
    assert "v5_signal_group_without_head" in codes(result, "V5")
    assert result.check("V5").state is CheckState.FAIL
    # The headless group is the file's only refuse-tier fault.
    assert [c.id for c in result.refuse_tier if c.state is CheckState.FAIL] == ["V5"]
    assert result.verdict is GateVerdict.REFUSED


def test_a_census_about_another_build_never_makes_a_file_ours(tmp_path):
    """The identity half of the same rule, in the other direction.

    Before 2026-08-11 provenance was ``bool(draft_metadata)``: any same-named
    sidecar, about any network, made a file "ours" — so a foreign model that
    happened to sit beside a stale ``*.genesis.json`` collected refusals meant
    for our own imports, and our own file with a sidecar from an EARLIER build
    had V3's departure clause graded against link ids that no longer exist."""
    xml = network(
        links=straight_pair(),
        extra=_foreign(_signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)])),
    )
    other = network(links=straight_pair(a_len=120.0) + [link(7, [(0.0, 9.0), (50.0, 9.0)])])
    result = gate(write(tmp_path, xml), draft_metadata=_census([], other))
    assert result.provenance.census == "describes_another_network"
    assert result.provenance.state is Provenance.FOREIGN
    assert [f.code for f in result.check("V5").warnings] == ["v5_signal_group_without_head"]
    assert "v3_census_is_not_about_this_network" in gap_codes(result, "V3")


def test_a_census_with_no_elements_cannot_identify_anything(tmp_path):
    """A sidecar that names no link, connector or node proves nothing about
    which file it belongs to — so it is not admitted, and the file falls back to
    the STRICT tier rather than to the lenient one."""
    xml = network(
        links=straight_pair(), extra=_signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)])
    )
    stub = {"metadata": {"connectivity": {"dangling_approaches": []}}}
    result = gate(write(tmp_path, xml), draft_metadata=stub)
    assert result.provenance.census == "declares_no_elements"
    assert result.provenance.state is Provenance.UNKNOWN
    assert result.check("V5").state is CheckState.FAIL


def test_our_own_provenance_comment_vetoes_a_foreign_verdict(tmp_path):
    """``emit.build_tree`` writes its provenance comment into every file this
    generator produces, so a file that carries one is not somebody else's
    however many containers it has gained since. The absence of the comment
    proves nothing in the other direction — Vissim's own ``SaveNetAs`` drops
    comments, measured on this repository's round-tripped Murdoch — which is
    why it can only ever VETO a foreign verdict, never establish one."""
    xml = network(
        links=straight_pair(),
        extra=_foreign(_signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)])),
    ).replace(
        "<links>",
        "<!-- Generated by Prism services.genesis (roadmap 2.3 Genesis (a)) -->\n<links>",
        1,
    )
    result = gate(write(tmp_path, xml))
    assert result.provenance.generator_marker is True
    assert result.provenance.foreign_containers == ["conflictAreas"]
    assert result.provenance.state is Provenance.UNKNOWN
    assert "v5_signal_group_without_head" in codes(result, "V5")


def test_the_provenance_decision_is_carried_where_a_reader_will_see_it(tmp_path):
    """An invisible tier switch makes the same verdict word mean two different
    things. The decision rides on the result, in the evidence of every check
    whose tier it changed, and on the front page of the report."""
    xml = network(
        links=straight_pair(),
        extra=_foreign(
            _signals(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)]) + _demand()
        ),
    )
    result = gate(write(tmp_path, xml))
    assert result.provenance.state is Provenance.FOREIGN
    assert result.provenance.foreign_containers == ["conflictAreas"]
    assert result.as_dict()["provenance"]["state"] == "foreign"
    assert result.check("V5").evidence["coverage_tier"] == "warn"
    assert result.check("V6").evidence["zero_volume_tier"] == "warn"
    text = render_report(result, language="en")
    assert "Provenance: NOT built by this generator" in text


def test_v5_still_refuses_a_head_naming_a_missing_controller_in_any_file(tmp_path):
    """Provenance scopes the COVERAGE clauses only. A head pointing at a
    controller the file does not declare is broken in anybody's model."""
    xml = network(
        links=straight_pair(), extra=_signals(groups=(1,), heads=[(1, 1, 1, 100.0, 9, 1)])
    )
    result = gate(write(tmp_path, xml))
    assert result.check("V5").state is CheckState.FAIL
    assert "v5_head_without_controller" in codes(result, "V5")


def test_v5_fails_on_an_approach_that_meets_no_head_anywhere_in_the_node(tmp_path):
    """Two approaches into one node, one head. Traffic on the south arm crosses
    the whole junction without ever meeting a stop line."""
    links = [
        link(1, [(0.0, 0.0), (100.0, 0.0)], name="west approach"),
        link(2, [(50.0, -100.0), (50.0, -4.0)], name="south approach"),
        link(3, [(104.0, 0.0), (204.0, 0.0)], name="east exit"),
        connector(10001, 1, 3, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0),
        connector(10002, 2, 3, [(50.0, -4.0), (104.0, 0.0)], from_pos=96.0),
    ]
    xml = network(
        links=links,
        nodes=[node(1, [(96.0, -12.0), (112.0, -12.0), (112.0, 12.0), (96.0, 12.0)])],
        extra=_signals(groups=(1,), heads=[(1, 1, 1, 100.0, 1, 1)]),
    )
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert result.check("V5").state is CheckState.FAIL
    findings = [
        f for f in result.check("V5").findings
        if f.code == "v5_approach_reaches_no_head_in_node"
    ]
    assert [f.subject for f in findings] == ["2 (south approach)"]


def test_v5_does_not_count_an_interior_leg_of_the_node_as_an_approach(tmp_path):
    """The stop line is on the ARM; the junction interior is meshed with a short
    leg that carries no head of its own and leads out of the node. A derivation
    that calls every link feeding a node connector an "approach" reports that
    leg as unsignalised — 35 of the 47 such links on the first real consultant
    model are interior legs exactly like it. Traffic already passed the stop
    line to get there."""
    links = [
        link(1, [(0.0, 0.0), (100.0, 0.0)], name="west arm"),
        link(2, [(104.0, 0.0), (140.0, 0.0)], name="interior leg"),
        link(3, [(144.0, 0.0), (244.0, 0.0)], name="east exit"),
        connector(10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0),
        connector(10002, 2, 3, [(140.0, 0.0), (144.0, 0.0)], from_pos=36.0),
    ]
    xml = network(
        links=links,
        nodes=[node(1, [(96.0, -12.0), (150.0, -12.0), (150.0, 12.0), (96.0, 12.0)])],
        extra=_signals(groups=(1,), heads=[(1, 1, 1, 100.0, 1, 1)]),
    )
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert "v5_approach_reaches_no_head_in_node" not in codes(result, "V5")
    assert result.check("V5").state is CheckState.PASS


def test_v5_finds_a_branch_inside_the_node_that_never_meets_a_head(tmp_path):
    """The case the unconditional interior exclusion DELETED before the walk ran.

    One true arm P branches inside the junction: P -> ARM -> EXIT carries the
    node's one head, and P -> RING -> STUB carries none anywhere, forward or
    back. P itself passes — it reaches the head down the other branch — and RING
    used to be dropped from the check outright for being the ``to_link`` of a
    same-node connector, so V5 read PASS with zero findings and zero warnings
    while a whole branch crossed the junction unsignalised. RING has NO head
    upstream of it inside the node, so it is not an innocent interior leg and it
    is checked; PTV's own ``Mainz - Static Routes`` has exactly this shape, and
    the consultant model has five of them."""
    links = [
        link(1, [(0.0, 0.0), (100.0, 0.0)], name="P the only arm"),
        link(2, [(104.0, -20.0), (140.0, -20.0)], name="RING"),
        link(3, [(104.0, 20.0), (140.0, 20.0)], name="ARM"),
        link(4, [(144.0, -20.0), (244.0, -20.0)], name="STUB"),
        link(5, [(144.0, 20.0), (244.0, 20.0)], name="EXIT"),
        connector(10001, 1, 2, [(100.0, 0.0), (104.0, -20.0)], from_pos=100.0),
        connector(10002, 1, 3, [(100.0, 0.0), (104.0, 20.0)], from_pos=100.0),
        connector(10003, 2, 4, [(140.0, -20.0), (144.0, -20.0)], from_pos=36.0),
        connector(10004, 3, 5, [(140.0, 20.0), (144.0, 20.0)], from_pos=36.0),
    ]
    xml = network(
        links=links,
        nodes=[node(1, [(96.0, -40.0), (150.0, -40.0), (150.0, 40.0), (96.0, 40.0)])],
        extra=_signals(groups=(1,), heads=[(1, 5, 1, 10.0, 1, 1)]),
    )
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    findings = [
        f for f in result.check("V5").findings
        if f.code == "v5_approach_reaches_no_head_in_node"
    ]
    assert [f.subject for f in findings] == ["2 (RING)"]
    assert result.check("V5").state is CheckState.FAIL
    # V5 is the only refuse-tier fault, so the refusal IS this branch.
    assert [c.id for c in result.refuse_tier if c.state is CheckState.FAIL] == ["V5"]
    assert result.verdict is GateVerdict.REFUSED


def test_v5_accepts_a_stop_line_on_an_interior_leg_of_the_node(tmp_path):
    """The head sits one connector INSIDE the junction rather than on the arm —
    a modelling convention this generator does not use and a real consultant
    model does. Traffic entering on the arm still meets it, so there is no
    defect; the clause this replaced called all 12 of one node's member links
    approaches and reported 8 interior legs as unsignalised."""
    links = [
        link(1, [(0.0, 0.0), (100.0, 0.0)], name="west approach"),
        link(2, [(104.0, 0.0), (140.0, 0.0)], name="interior leg", lanes=1),
        link(3, [(144.0, 0.0), (244.0, 0.0)], name="east exit"),
        connector(10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0),
        connector(10002, 2, 3, [(140.0, 0.0), (144.0, 0.0)], from_pos=36.0),
    ]
    xml = network(
        links=links,
        nodes=[node(1, [(96.0, -12.0), (150.0, -12.0), (150.0, 12.0), (96.0, 12.0)])],
        extra=_signals(groups=(1,), heads=[(1, 2, 1, 30.0, 1, 1)]),
    )
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert "v5_approach_reaches_no_head_in_node" not in codes(result, "V5")


# ---------------------------------------------------------------------------
# §7 V6 — demand coverage
# ---------------------------------------------------------------------------


def _demand(
    *,
    inputs: list[tuple] = ((1, 1, 1, 100),),
    compositions: list[tuple] = ((1, [(1, 50, 1.0)]),),
    decisions: list[tuple] = (),
) -> str:
    comp_xml = "".join(
        f"<vehicleComposition name='c{no}' no='{no}'><vehCompRelFlows>"
        + "".join(
            f"<vehicleCompositionRelativeFlow desSpeedDistr='{sd}' relFlow='{rf}' vehType='{vt}'/>"
            for vt, sd, rf in flows
        )
        + "</vehCompRelFlows></vehicleComposition>"
        for no, flows in compositions
    )
    input_xml = "".join(
        f"<vehicleInput link='{link_id}' name='' no='{no}'><timeIntVehVols>"
        f"<timeIntervalVehVolume timeInt='1 0' vehComp='{comp}' volume='{volume}'/>"
        "</timeIntVehVols></vehicleInput>"
        for no, link_id, comp, volume in inputs
    )
    decision_xml = "".join(
        f"<vehicleRoutingDecisionStatic link='{link_id}' name='' no='{no}' pos='{pos}'/>"
        for no, link_id, pos in decisions
    )
    return (
        f"<vehicleCompositions>{comp_xml}</vehicleCompositions>"
        f"<vehicleInputs>{input_xml}</vehicleInputs>"
        + (
            f"<vehicleRoutingDecisionsStatic>{decision_xml}</vehicleRoutingDecisionsStatic>"
            if decision_xml
            else ""
        )
    )


def test_v6_with_no_demand_layer_is_not_checkable_never_a_pass(tmp_path):
    """A network with no demand simulates nothing; 'every input is covered' is
    vacuously true of no inputs."""
    result = gate(write(tmp_path, network(links=straight_pair())))
    assert result.check("V6").state is CheckState.NOT_CHECKABLE
    assert gap_codes(result, "V6") == ["v6_no_demand_layer"]


def test_v6_passes_on_a_covered_input(tmp_path):
    xml = network(links=straight_pair(), extra=_demand())
    result = gate(write(tmp_path, xml))
    assert result.check("V6").state is CheckState.PASS


def test_v6_fails_when_the_composition_is_not_in_the_file(tmp_path):
    xml = network(links=straight_pair(), extra=_demand(inputs=[(1, 1, 7, 100)]))
    result = gate(write(tmp_path, xml))
    assert result.check("V6").state is CheckState.FAIL
    assert "v6_input_composition_missing" in codes(result, "V6")


def test_v6_fails_on_an_input_that_generates_nothing_in_a_layer_we_built(tmp_path):
    xml = network(links=straight_pair(), extra=_demand(inputs=[(1, 1, 1, 0)]))
    result = gate(write(tmp_path, xml), draft_metadata=_census([], xml))
    assert result.check("V6").state is CheckState.FAIL
    assert "v6_input_volume_is_zero" in codes(result, "V6")


def test_v6_only_warns_about_a_zero_input_in_a_layer_we_did_not_build(tmp_path):
    """A spreadsheet-driven demand build declares the same input on every entry
    link and leaves the volume at 0 where the count was 0; PTV's own 2026
    example library carries 60 of them among 1,116 inputs. Named, never a
    refusal — on a file PROVEN to be somebody else's."""
    xml = network(links=straight_pair(), extra=_foreign(_demand(inputs=[(1, 1, 1, 0)])))
    result = gate(write(tmp_path, xml))
    assert result.provenance.state is Provenance.FOREIGN
    assert codes(result, "V6") == []
    assert [f.code for f in result.check("V6").warnings] == ["v6_input_volume_is_zero"]


def test_v6_refuses_a_zero_input_when_nothing_proves_whose_layer_it_is(tmp_path):
    """Same file without the foreign container: nothing says it is not ours, so
    the refuse tier holds. A demotion earned by MISSING evidence is how a
    refuse-tier defect reaches the workspace as a published dataset."""
    xml = network(
        links=straight_pair(),
        extra=_demand(inputs=[(1, 1, 1, 0)]),
        support=_SUPPORT + _DEMAND_SUPPORT,
    )
    result = gate(write(tmp_path, xml))
    assert result.provenance.state is Provenance.UNKNOWN
    assert "v6_input_volume_is_zero" in codes(result, "V6")
    # The zero volume is the file's only refuse-tier fault.
    assert [c.id for c in result.refuse_tier if c.state is CheckState.FAIL] == ["V6"]
    assert result.verdict is GateVerdict.REFUSED


def test_v6_fails_on_a_composition_whose_relative_flows_sum_to_zero(tmp_path):
    xml = network(
        links=straight_pair(), extra=_demand(compositions=[(1, [(1, 50, 0.0), (2, 50, 0.0)])])
    )
    result = gate(write(tmp_path, xml))
    assert "v6_composition_relflow_sum_zero" in codes(result, "V6")


def test_v6_fails_when_traffic_reaches_a_branch_before_a_routing_decision(tmp_path):
    """Vissim does not refuse traffic that arrives at a branch uncovered — it
    invents the split and reports it with the confidence of an observed one."""
    links = [
        link(1, [(0.0, 0.0), (100.0, 0.0)], name="approach"),
        link(2, [(104.0, 4.0), (204.0, 4.0)], name="left exit"),
        link(3, [(104.0, -4.0), (204.0, -4.0)], name="right exit"),
        connector(10001, 1, 2, [(100.0, 0.0), (104.0, 4.0)], from_pos=100.0),
        connector(10002, 1, 3, [(100.0, 0.0), (104.0, -4.0)], from_pos=100.0),
    ]
    xml = network(links=links, extra=_demand())
    result = gate(write(tmp_path, xml))
    assert result.check("V6").state is CheckState.FAIL
    assert "v6_input_without_routing_coverage" in codes(result, "V6")

    covered = network(links=links, extra=_demand(decisions=[(1, 1, 10.0)]))
    assert gate(write(tmp_path, covered, "covered.inpx")).check("V6").state is CheckState.PASS


def test_v6_capacity_is_a_warning_that_rides_on_the_result(tmp_path):
    """R1.5: refuse on structure, WARN on capacity. Over-capacity demand is
    legal, runs, and lies — it must not refuse a build on its own."""
    xml = network(links=straight_pair(), extra=_demand(inputs=[(1, 1, 1, 5000)]))
    result = gate(write(tmp_path, xml))
    check = result.check("V6")
    assert check.state is CheckState.PASS
    assert [w.code for w in check.warnings] == ["v6_link_demand_over_capacity"]
    assert check.evidence["rough_lane_capacity_veh_per_h"] == 1900


def _multi_interval_demand(volumes: list[float]) -> str:
    """One input on link 1 carrying one volume per 15-minute interval — the
    shape every real peak-hour model uses and this generator never emits."""
    intervals = "".join(
        f"<timeIntervalVehVolume timeInt='1 {900_000 * i}' vehComp='1' volume='{v}'/>"
        for i, v in enumerate(volumes)
    )
    return (
        "<vehicleCompositions><vehicleComposition name='c1' no='1'><vehCompRelFlows>"
        "<vehicleCompositionRelativeFlow desSpeedDistr='50' relFlow='1.0' vehType='1'/>"
        "</vehCompRelFlows></vehicleComposition></vehicleCompositions>"
        f"<vehicleInputs><vehicleInput link='1' name='' no='1'><timeIntVehVols>{intervals}"
        "</timeIntVehVols></vehicleInput></vehicleInputs>"
    )


def test_v6_compares_capacity_against_the_peak_interval_not_the_sum_of_them(tmp_path):
    """``volume`` is a RATE that applies DURING one interval, so six intervals
    of 1,500 veh/h is 1,500 veh/h of demand, not 9,000. This generator emits one
    interval per input, which made sum and max the same number and hid the
    difference until a real model with six 15-minute intervals was reported at
    11,893 veh/h on a three-lane road."""
    xml = network(
        links=straight_pair(a_lanes=1),
        extra=_multi_interval_demand([1500.0] * 6),
    )
    result = gate(write(tmp_path, xml))
    assert [w.code for w in result.check("V6").warnings] == []
    assert result.check("V6").evidence["demand_time_intervals"] == 6


def test_v6_still_warns_when_one_interval_on_its_own_is_over_capacity(tmp_path):
    """The same six intervals with one genuine peak. Taking the max must not
    mean taking nothing."""
    xml = network(
        links=straight_pair(a_lanes=1),
        extra=_multi_interval_demand([1500.0, 1500.0, 2500.0, 1500.0, 1500.0, 1500.0]),
    )
    result = gate(write(tmp_path, xml))
    warning = next(
        w for w in result.check("V6").warnings if w.code == "v6_link_demand_over_capacity"
    )
    assert warning.context["volume_vph"] == 2500.0
    assert warning.context["interval"] == "1 1800000"
    assert warning.context["intervals"] == 6


def test_v6_capacity_uses_the_same_constant_the_edit_time_guardrail_does():
    from services.agent.constraints import _ROUGH_LANE_CAPACITY_VEH_PER_H
    from services.genesis.gate import ROUGH_LANE_CAPACITY_VEH_PER_H

    assert ROUGH_LANE_CAPACITY_VEH_PER_H is _ROUGH_LANE_CAPACITY_VEH_PER_H


# ---------------------------------------------------------------------------
# §8 V7 — geometry sanity (warn)
# ---------------------------------------------------------------------------


def test_v7_fails_on_a_zero_length_link(tmp_path):
    links = straight_pair()
    links.append(link(3, [(300.0, 0.0), (300.0, 0.0)], name="zero"))
    result = gate(write(tmp_path, network(links=links)))
    assert result.check("V7").state is CheckState.FAIL
    assert "v7_zero_length_link" in codes(result, "V7")


def test_v7_fails_on_a_link_below_the_generators_own_floor(tmp_path):
    links = straight_pair()
    links.append(link(3, [(300.0, 0.0), (301.5, 0.0)], name="stub"))
    result = gate(write(tmp_path, network(links=links)))
    assert "v7_link_below_generator_floor" in codes(result, "V7")
    finding = next(
        f for f in result.check("V7").findings if f.code == "v7_link_below_generator_floor"
    )
    assert finding.context["floor_m"] == 2.0


def test_v7_names_both_halves_it_cannot_run(tmp_path):
    result = gate(write(tmp_path, network(links=straight_pair())))
    assert set(gap_codes(result, "V7")) == {
        "v7_no_reference_network",
        "v7_no_desired_speed_decisions",
    }


def test_v7_with_a_reference_still_delegates_rather_than_restating_benchmark(tmp_path):
    """One question, one answer: ``benchmark.py`` owns geometric correspondence
    and its 22 m tolerance; the gate names the delegation instead of grading it
    a second time."""
    path = write(tmp_path, network(links=straight_pair()))
    other = write(tmp_path, network(links=straight_pair(a_len=120.0)), "ref.inpx")
    result = gate(path, reference_inpx=other)
    assert "v7_reference_comparison_delegated" in gap_codes(result, "V7")
    assert "v7_no_reference_network" not in gap_codes(result, "V7")


def test_v2_publishes_the_reference_kinds_it_deliberately_does_not_resolve(tmp_path):
    result = gate(write(tmp_path, network(links=straight_pair())))
    stated = result.check("V2").evidence["reference_kinds_not_resolved"]
    assert any(s.startswith("signalController/@intSupplyData — ") for s in stated)
    assert any(s.startswith("timeIntervalVehVolume/@timeInt — ") for s in stated)


# ---------------------------------------------------------------------------
# §9 V8 — conflict coverage (warn)
# ---------------------------------------------------------------------------


def test_v8_counts_crossing_movements_and_refuses_to_grade_right_of_way(tmp_path):
    """Two movements through one node that cross. The count is the evidence;
    the right-of-way is not in any .inpx, because Vissim derives conflict areas
    from the geometry at load time and writes none of them back."""
    links = [
        link(1, [(-100.0, 0.0), (-4.0, 0.0)], name="west approach"),
        link(2, [(4.0, 0.0), (104.0, 0.0)], name="east exit"),
        link(3, [(0.0, -100.0), (0.0, -4.0)], name="south approach"),
        link(4, [(0.0, 4.0), (0.0, 104.0)], name="north exit"),
        connector(10001, 1, 2, [(-4.0, 0.0), (4.0, 0.0)], from_pos=96.0),
        connector(10002, 3, 4, [(0.0, -4.0), (0.0, 4.0)], from_pos=96.0),
    ]
    xml = network(
        links=links,
        nodes=[node(1, [(-6.0, -6.0), (6.0, -6.0), (6.0, 6.0), (-6.0, 6.0)])],
    )
    result = gate(write(tmp_path, xml))
    check = result.check("V8")
    assert check.evidence["crossing_movement_pairs"] == 1
    assert check.evidence["nodes_with_an_unsignalised_crossing"] == 1
    assert check.state is CheckState.NOT_CHECKABLE
    assert "v8_right_of_way_not_in_file" in gap_codes(result, "V8")


def test_v8_does_not_call_a_shared_endpoint_a_crossing(tmp_path):
    """Two movements that merge onto one exit touch; they do not cross."""
    links = [
        link(1, [(-100.0, 10.0), (-4.0, 10.0)]),
        link(2, [(-100.0, -10.0), (-4.0, -10.0)]),
        link(3, [(4.0, 0.0), (104.0, 0.0)]),
        connector(10001, 1, 3, [(-4.0, 10.0), (4.0, 0.0)], from_pos=96.0),
        connector(10002, 2, 3, [(-4.0, -10.0), (4.0, 0.0)], from_pos=96.0),
    ]
    xml = network(
        links=links, nodes=[node(1, [(-6.0, -12.0), (6.0, -12.0), (6.0, 12.0), (-6.0, 12.0)])]
    )
    result = gate(write(tmp_path, xml))
    assert result.check("V8").evidence["crossing_movement_pairs"] == 0


# ---------------------------------------------------------------------------
# §10 V9 — measurement readiness (warn)
# ---------------------------------------------------------------------------


def test_v9_with_no_stop_lines_is_not_checkable_never_a_pass(tmp_path):
    result = gate(write(tmp_path, network(links=straight_pair())))
    assert result.check("V9").state is CheckState.NOT_CHECKABLE
    assert "v9_no_stop_lines" in gap_codes(result, "V9")


def test_v9_fails_on_a_stop_line_with_no_queue_counter(tmp_path):
    xml = network(
        links=straight_pair(), extra=_signals(groups=(1,), heads=[(1, 1, 1, 100.0, 1, 1)])
    )
    result = gate(write(tmp_path, xml))
    assert result.check("V9").state is CheckState.FAIL
    assert "v9_stop_line_without_queue_counter" in codes(result, "V9")


def test_v9_accepts_a_stop_line_that_is_measured(tmp_path):
    extra = _signals(groups=(1,), heads=[(1, 1, 1, 100.0, 1, 1)]) + (
        "<queueCounters><queueCounter link='1' name='' no='1' pos='100'/></queueCounters>"
    )
    result = gate(write(tmp_path, network(links=straight_pair(), extra=extra)))
    assert [f.code for f in result.check("V9").findings] == []
    # The corridor half is still a gap: nothing declares which corridors matter.
    assert "v9_no_corridor_definition" in gap_codes(result, "V9")


# ---------------------------------------------------------------------------
# §11 The real emitter, and the CLI
# ---------------------------------------------------------------------------


def _crossroads_inpx(tmp_path: Path) -> tuple[Path, dict]:
    """A network through the REAL generator: ``osm`` → ``draft`` → ``emit``."""
    from services.genesis.draft import build_draft
    from services.genesis.emit import emit_inpx
    from services.genesis.osm import parse_osm_file

    from tests.test_genesis_osm_draft import crossroads_osm

    osm_path = tmp_path / "cross.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    draft = build_draft(parse_osm_file(osm_path), name="cross")
    assert not draft.refused
    path = emit_inpx(draft, tmp_path / "cross.inpx")
    return Path(path), draft.model_dump(mode="json")


def test_the_real_emitters_output_passes_reference_integrity_and_lane_continuity(tmp_path):
    """The anti-drift test: the hand-written fixtures above cannot wander away
    from what the generator writes, because this one is the generator's output."""
    path, metadata = _crossroads_inpx(tmp_path)
    result = gate(path, draft_metadata=metadata)
    assert result.check("V2").state is CheckState.PASS, codes(result, "V2")
    assert result.check("V4").state is CheckState.PASS, codes(result, "V4")
    assert result.check("V2").evidence["references_checked"] > 0


def test_a_geometry_only_generation_can_never_be_an_unqualified_pass(tmp_path):
    """It has no signals, no demand and no live-Vissim evidence, and the gate
    says all three rather than any of them silently passing."""
    path, metadata = _crossroads_inpx(tmp_path)
    result = gate(path, draft_metadata=metadata)
    assert result.verdict is not GateVerdict.PASS
    assert result.check("V1").state is CheckState.NOT_CHECKABLE
    assert result.check("V5").state is CheckState.NOT_CHECKABLE
    assert result.check("V6").state is CheckState.NOT_CHECKABLE


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE_CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(BACKEND_ROOT),
    )


def _run_cli_bytes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE_CLI), *args], capture_output=True, cwd=str(BACKEND_ROOT)
    )


def test_cli_exit_code_separates_incomplete_from_refused(tmp_path):
    clean = write(tmp_path, network(links=straight_pair()), "clean.inpx")
    proc = _run_cli("--inpx", str(clean), "--lang", "en")
    assert proc.returncode == 3, proc.stderr  # INCOMPLETE — no V1 evidence
    assert "INCOMPLETE" in proc.stdout

    links = straight_pair()
    links.append(link(3, [(0.0, 400.0), (100.0, 400.0)], name="island"))
    broken_xml = network(links=links)
    broken = write(tmp_path, broken_xml, "broken.inpx")
    (tmp_path / "broken.genesis.json").write_text(
        json.dumps(_census([], broken_xml)), encoding="utf-8"
    )
    proc = _run_cli("--inpx", str(broken), "--lang", "en")
    assert proc.returncode == 2, proc.stderr  # REFUSED — V3 proved a fault
    assert "REFUSED" in proc.stdout
    assert "genesis.json" in proc.stderr


def test_cli_usage_error_is_its_own_exit_code(tmp_path):
    proc = _run_cli("--inpx", str(tmp_path / "nope.inpx"))
    assert proc.returncode == 1


def test_cli_json_is_machine_clean_and_deterministic(tmp_path):
    path = write(tmp_path, network(links=straight_pair()), "clean.inpx")
    first = _run_cli("--inpx", str(path), "--json")
    second = _run_cli("--inpx", str(path), "--json")
    assert first.stdout == second.stdout
    payload = json.loads(first.stdout)
    assert [c["id"] for c in payload["checks"]] == [f"V{n}" for n in range(1, 10)]
    assert payload["verdict"] == "incomplete"


def test_cli_output_is_utf8_wherever_it_runs(tmp_path):
    """MEASURED on this project 2026-08-10, one tool earlier: every em dash in
    these messages IS cp1252-encodable, so ``errors="replace"`` never fires and
    a redirected ``--json`` came out as valid JSON in cp1252 that
    ``json.load(open(path, encoding="utf-8"))`` could not read."""
    path = write(tmp_path, network(links=straight_pair()), "clean.inpx")
    for extra in ((), ("--json",)):
        proc = _run_cli_bytes("--inpx", str(path), *extra)
        proc.stdout.decode("utf-8")  # raises if the console encoding leaked in
        proc.stderr.decode("utf-8")
    payload = json.loads(_run_cli_bytes("--inpx", str(path), "--json").stdout.decode("utf-8"))
    assert payload["verdict"] == "incomplete"

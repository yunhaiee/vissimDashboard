"""Unit #42 — a world gap that is the WORLD's gets a normal treatment.

Unit #36 split a V3 connectivity refusal into two worlds; unit #35 gave the
world-gap half an ask-first question — *does the real road continue, and to
where?* — and that question has no honest answer when the road genuinely ends.
Measured on the real job ``2712071437d0``: ``service [FWD] w1294917882s0`` is a
6.5 m stub into 갤러리아백화점's car park, the source declares nothing departing
its far node, and the ONE candidate #35 can offer is 9.7 m away on a different
road (둔산로31번길). Attesting to it would put false geometry in the model, and
refusing forever leaves the artifact refused forever. That finding was, measured
here in the counterfactual, the LAST V3 finding on that job which unit #38's
converter fix cannot clear: a plain regeneration takes V3 from 10 to 1, and the
1 is the ramp.

So the third disposition: a human who checked the site CONFIRMS the road ends,
the confirmed way is excluded BY NAME, and the generation is re-derived without
it through unit #38's own machine. The doctrine each family below guards:

* §1 the disposition is MEASURED, and its first door is the EVIDENCE HIERARCHY —
  a gap the SOURCE testifies to can never be confirmed away, because a
  declaration this system can re-read is not erased by a claim it cannot check;
* §2 the exclusion's SCOPE is exactly the confirmed way, and what would come
  loose with it is measured and named BEFORE the signature, never discovered in
  the sibling's census afterwards;
* §3 the source is evidence — the exclusion is a declarative parameter and the
  copied ``source.osm`` stays sha-identical to the parent's;
* §4 the exclusion is a LEDGER ENTRY (B5-1's rule): who confirmed it, when, and
  their own words land in the census and the compliance report;
* §5 the judge is untouched — V3 stops firing because the way is gone, which is
  the only honest way for a finding to go away;
* §6 unit #38's four doors are the SAME code, not near-copies, so this path
  cannot be a way around them;
* §7 the HTTP surface: 202, 404, 409-by-code, no anonymous callers, and no field
  a caller could use to widen the exclusion or forge attribution.

No COM anywhere: an exclusion re-derivation is an ordinary generation (pure
Python), so the runner here is the real one and the gate is the real gate.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces
from services.agent import fixes as agent_fixes
from services.agent import genesis_fixes
from services.genesis.osm import SKIP_CONFIRMED_REAL_WORLD_DEAD_END, parse_osm_file

from tests.test_genesis_gate import (
    _DEMAND_SUPPORT as demand_support,
)
from tests.test_genesis_gate import _SUPPORT as gate_support
from tests.test_genesis_gate import _demand as demand_layer
from tests.test_genesis_gate import link, network, straight_pair
from tests.test_genesis_jobs import _seed_job
from tests.test_genesis_regenerate_job import _pre_fix_parent

_CODE = "v3_silent_departure_point"

#: The approach link's own way and the node its census entry names as dangling —
#: the shape ``gate._check_v3`` reads, mirroring unit #35's own fixture.
_WAY_IN = 9007
_NODE = 5002
#: A second way the source declares departing that node, for the TESTIMONY case.
_WAY_ON = 9008


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as ``test_genesis_fix_v3_connector.py``'s fixture of the same
    name — duplicated rather than imported (this repo's convention only
    cross-imports plain helper FUNCTIONS between genesis test modules)."""
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield tmp_path / "workspaces"
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def client(workspace_root: Path) -> TestClient:
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# The refusable artifact + census (hand-written: the real generator no longer
# emits a V3-refusable network, which is B5-1's own result)
# ---------------------------------------------------------------------------


def _v3_network(*, with_twin: bool = False) -> str:
    links = list(straight_pair()) + [
        link(3, [(0.0, 200.0), (100.0, 200.0)], name="approach 3"),
        link(4, [(104.0, 200.0), (204.0, 200.0)], name="continuation 4"),
    ]
    if with_twin:
        links.append(link(6, [(0.0, 400.0), (100.0, 400.0)], name="approach 6"))
        links.append(link(7, [(104.0, 400.0), (204.0, 400.0)], name="continuation 7"))
    return network(links=links, extra=demand_layer(), support=gate_support + demand_support)


def _census_link(
    link_id: int,
    way_id: int,
    points: list[list[float]],
    *,
    from_node: int,
    to_node: int,
) -> dict[str, Any]:
    return {
        "id": link_id,
        "name": f"link {link_id}",
        "osm_way_id": way_id,
        "segment_index": 0,
        "forward": True,
        "lanes": 1,
        "lane_width_m": 3.5,
        "points": points,
        "lane_provenance": {},
        "highway": "residential",
        "from_node_osm_id": from_node,
        "to_node_osm_id": to_node,
        "side_offset_m": 0.0,
    }


def _census_connector(connector_id: int, from_link: int, to_link: int) -> dict[str, Any]:
    return {
        "id": connector_id,
        "name": f"connector {connector_id}",
        "node_osm_id": 1,
        "node_id": 1,
        "from_link": from_link,
        "to_link": to_link,
        "from_pos_m": 100.0,
        "to_pos_m": 0.0,
        "lanes": 1,
        "lane_width_m": 3.5,
        "points": [[100.0, 0.0], [104.0, 0.0]],
        "turn": "through",
        "heading_change_deg": 0.0,
        "lane_change": "same",
    }


def _dangling(link_id: int, name: str, node_osm_id: int = _NODE) -> dict[str, Any]:
    return {
        "cause": "junction_with_no_exit_link",
        "dead_end_reason": None,
        "link": name,
        "link_id": link_id,
        "node_osm_id": node_osm_id,
    }


def _census(
    *,
    with_twin: bool = False,
    approach_way: int = _WAY_IN,
    continuation_begins_at_the_node: bool = True,
) -> dict[str, Any]:
    links = [
        _census_link(1, 9001, [[0.0, 0.0], [100.0, 0.0]], from_node=1001, to_node=1002),
        _census_link(2, 9002, [[104.0, 0.0], [204.0, 0.0]], from_node=1002, to_node=1003),
        _census_link(3, approach_way, [[0.0, 200.0], [100.0, 200.0]], from_node=5001, to_node=_NODE),
        _census_link(
            4,
            _WAY_ON,
            [[104.0, 200.0], [204.0, 200.0]],
            from_node=_NODE if continuation_begins_at_the_node else 5099,
            to_node=5003,
        ),
    ]
    dangling = [_dangling(3, "approach 3")]
    if with_twin:
        links.append(
            _census_link(6, 9107, [[0.0, 400.0], [100.0, 400.0]], from_node=6001, to_node=6002)
        )
        links.append(
            _census_link(7, 9108, [[104.0, 400.0], [204.0, 400.0]], from_node=6002, to_node=6003)
        )
        dangling.append(_dangling(6, "approach 6", node_osm_id=6002))
    return {
        "name": "seeded",
        "source_osm": "seed.osm",
        "metadata": {"connectivity": {"dangling_approaches": dangling}},
        "links": links,
        "connectors": [_census_connector(10001, 1, 2)],
        "nodes": [],
    }


# ---------------------------------------------------------------------------
# The source .osm — REAL geometry, because the sibling job runs the REAL
# pipeline over these very bytes. The main corridor is a two-way road through
# four nodes; the STUB (way _WAY_IN) leaves its middle node and dead-ends, which
# is the Galleria ramp's own shape.
# ---------------------------------------------------------------------------


def _osm_node(node_id: int, lat: float, lon: float) -> str:
    return f'  <node id="{node_id}" lat="{lat}" lon="{lon}"/>\n'


def _osm_way(way_id: int, node_ids: list[int], *, highway: str = "residential") -> str:
    nds = "".join(f'    <nd ref="{n}"/>\n' for n in node_ids)
    return f'  <way id="{way_id}">\n{nds}    <tag k="highway" v="{highway}"/>\n  </way>\n'


def _osm(*elements: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="unit42-test">\n' + "".join(elements) + "</osm>\n"
    )


#: The corridor's own nodes, ~110 m apart in latitude.
_CORRIDOR_NODES = (
    _osm_node(4001, 36.3500, 127.3800)
    + _osm_node(4002, 36.3510, 127.3800)
    + _osm_node(5001, 36.3520, 127.3800)
    + _osm_node(4004, 36.3530, 127.3800)
)
_STUB_NODES = _osm_node(_NODE, 36.3520, 127.3812)
_ON_NODES = _osm_node(5003, 36.3520, 127.3824)

#: WORLD GAP: the stub (way 9007) leaves node 5001 and ends at node 5002, which
#: no other drivable way touches. The Galleria shape.
_SOURCE_GAP = _osm(
    _CORRIDOR_NODES + _STUB_NODES,
    _osm_way(9001, [4001, 4002, 5001, 4004]),
    _osm_way(_WAY_IN, [5001, _NODE]),
)

#: SOURCE TESTIMONY: way 9008 carries on past node 5002 — a gap the source
#: itself denies, and the one case a confirmation may never erase.
_SOURCE_TESTIFIES = _osm(
    _CORRIDOR_NODES + _STUB_NODES + _ON_NODES,
    _osm_way(9001, [4001, 4002, 5001, 4004]),
    _osm_way(_WAY_IN, [5001, _NODE]),
    _osm_way(_WAY_ON, [_NODE, 5003]),
)

#: COLLATERAL: the stub is a BRIDGE. Way 9009 hangs off the stub's MIDDLE node
#: (5010), never its far end — so node 5002 is still a world gap the source
#: shares, and excluding way 9007 cuts 9009 loose from everything else. The
#: attachment has to be mid-way: a way departing the DANGLING node would make
#: the source testify, and a testified gap can never be confirmed away at all.
_SOURCE_BRIDGE = _osm(
    _CORRIDOR_NODES
    + _STUB_NODES
    + _osm_node(5010, 36.3520, 127.3806)
    + _osm_node(5005, 36.3526, 127.3806),
    _osm_way(9001, [4001, 4002, 5001, 4004]),
    _osm_way(_WAY_IN, [5001, 5010, _NODE]),
    _osm_way(9009, [5010, 5005]),
)

#: A SECOND dead-end stub off the corridor's other end, for the chaining
#: family: two independent world gaps in one source, so a confirmation can
#: follow a confirmation.
_SOURCE_TWO_GAPS = _osm(
    _CORRIDOR_NODES + _STUB_NODES + _osm_node(6002, 36.3510, 127.3812),
    _osm_way(9001, [4001, 4002, 5001, 4004]),
    _osm_way(_WAY_IN, [5001, _NODE]),
    _osm_way(9107, [4002, 6002]),
)

#: ONE drivable way in the whole file, and it is the finding's own.
_SOURCE_SINGLETON = _osm(
    _osm_node(5001, 36.3520, 127.3800) + _STUB_NODES,
    _osm_way(_WAY_IN, [5001, _NODE]),
)

#: REFUTER (2026-08-18). TWO stubs share node 5010, and pocket 9009 hangs off
#: that shared node. While BOTH stubs are in the network, excluding either cuts
#: nothing loose — the other still holds 9009 on. Once way 9007 is excluded,
#: 9107 is the pocket's ONLY remaining attachment, so confirming 9107 on that
#: sibling strands 9009. The source keeps declaring 9007 (a source is evidence
#: and is never edited), so a measurement that reads the source alone reports
#: the severance as zero.
_SOURCE_SHARED_HINGE = _osm(
    _CORRIDOR_NODES
    + _STUB_NODES
    + _osm_node(5010, 36.3515, 127.3806)
    + _osm_node(5005, 36.3526, 127.3806)
    + _osm_node(6002, 36.3510, 127.3812),
    _osm_way(9001, [4001, 4002, 5001, 4004]),
    _osm_way(_WAY_IN, [4002, 5010, 6002]),
    _osm_way(9107, [5001, 5010, _NODE]),
    _osm_way(9009, [5010, 5005]),
)

#: REFUTER (2026-08-18). TWO disjoint stubs, so a source that still declares two
#: drivable ways can be a NETWORK of one once the first is excluded. Node 5002
#: is touched by way 9107 alone, so the finding's gap stays the world's.
_SOURCE_TWO_DISJOINT_STUBS = _osm(
    _osm_node(5001, 36.3520, 127.3800)
    + _osm_node(7001, 36.3480, 127.3800)
    + _osm_node(6001, 36.3500, 127.3800)
    + _STUB_NODES,
    _osm_way(_WAY_IN, [5001, 7001]),
    _osm_way(9107, [6001, _NODE]),
)

#: REFUTER (2026-08-18). The generation models ONE way; the other is a service
#: way its ``exclude_service`` option never modelled. The source's own drivable
#: count is two, and the network's is one.
_SOURCE_SERVICE_PADDED = _osm(
    _osm_node(5001, 36.3520, 127.3800)
    + _STUB_NODES
    + _osm_node(7001, 36.3400, 127.3800)
    + _osm_node(7002, 36.3400, 127.3812),
    _osm_way(_WAY_IN, [5001, _NODE]),
    _osm_way(9500, [7001, 7002], highway="service"),
)


def _attach_source_osm(record: dict[str, Any], source_xml: str) -> dict[str, Any]:
    """Store a source ``.osm`` the way a real generation does — including the
    RECORDED digest, because unit #38's sha door is one this path must pass
    rather than dodge."""
    path = genesis_jobs.job_dir(record["id"]) / "source.osm"
    path.write_text(source_xml, encoding="utf-8", newline="")
    record["source_osm"] = {
        "filename": "source.osm",
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "stored_as": "source.osm",
    }
    genesis_jobs._upsert(record)
    return record


def _seed_parent(
    source_xml: str | None,
    *,
    name: str = "v3_parent",
    with_twin: bool = False,
    approach_way: int = _WAY_IN,
    continuation_begins_at_the_node: bool = True,
    gated: bool = True,
) -> dict[str, Any]:
    record = _seed_job(
        _v3_network(with_twin=with_twin),
        census=_census(
            with_twin=with_twin,
            approach_way=approach_way,
            continuation_begins_at_the_node=continuation_begins_at_the_node,
        ),
        name=name,
    )
    if source_xml is not None:
        _attach_source_osm(record, source_xml)
    if not gated:
        return genesis_jobs.get_job(record["id"])
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _finding(link_id: int = 3, subject: str = "approach 3") -> dict[str, Any]:
    return {
        "code": _CODE,
        "subject": subject,
        "context": {"cause": "junction_with_no_exit_link", "link_id": link_id},
    }


def _derive(job_id: str, finding: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """The deriver, reached the way the service layer reaches it: through the
    real testimony derivation, so the two can never disagree about which world
    a finding is in."""
    draft = genesis_jobs.job_draft_or_none(job_id)
    finding = finding or _finding()
    testimony = genesis_jobs.derive_v3_source_testimony(
        _CODE, finding, draft=draft, job_id=job_id
    )
    return genesis_jobs.derive_v3_dead_end_confirmation(
        finding,
        draft=draft,
        job_id=job_id,
        testimony=testimony["source_testimony"],
        testimony_provenance=testimony["provenance"],
        **kwargs,
    )


def _disposition(client: TestClient, job_id: str, subject: str = "approach 3") -> dict[str, Any]:
    listed = client.get(f"/api/genesis/jobs/{job_id}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    entries = [
        e for e in listed.json()["findings"] if e["code"] == _CODE and e["subject"] == subject
    ]
    assert len(entries) == 1, entries
    return entries[0]["disposition"]


def _confirm(parent_id: str, **kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "finding_code": _CODE,
        "subject": "approach 3",
        "basis": "구글맵 현장 확인 — 백화점 내부 순환로",
        "acknowledged_collateral_way_ids": [],
        "confirmed_by": "yunha",
    }
    payload.update(kwargs)
    return genesis_jobs.create_dead_end_exclusion_job(parent_id, **payload)


# ---------------------------------------------------------------------------
# §1 The measurement, and the EVIDENCE HIERARCHY that is its first door
# ---------------------------------------------------------------------------


def test_a_measured_world_gap_offers_a_confirmation_naming_exactly_one_way(
    workspace_root: Path,
):
    """The offer is the SCOPE a human signs for: one way, the census links that
    carry it, its metres, and what the source says comes loose with it."""
    parent = _seed_parent(_SOURCE_GAP)
    derived = _derive(parent["id"])
    offer = derived["dead_end_confirmation"]

    assert offer is not None
    assert offer["way_id"] == _WAY_IN
    assert offer["node_id"] == _NODE
    # EXACTLY the confirmed way's own links -- never the roads it touches. The
    # census also declares link 4 on way 9008 beginning at the same node, and it
    # is deliberately absent from this list.
    assert offer["link_ids"] == ["3"]
    assert offer["links"] == [
        {"link_id": "3", "name": "link 3", "lanes": 1, "length_m": 100.0}
    ]
    assert offer["excluded_length_m"] == 100.0
    assert offer["collateral_way_ids"] == []
    assert offer["collateral"] == []
    assert derived["provenance"]["source_field"] == "way/nd + links[].osm_way_id"
    assert derived["provenance"]["link_id"] == "3"
    assert "reason_code" not in derived["provenance"]


def test_a_gap_the_source_testifies_to_can_never_be_confirmed_away(workspace_root: Path):
    """THE load-bearing refusal. The source declares the road continues; a human
    confirmation is a claim this system can never re-check, and letting the
    second erase the first would be inventing-by-subtraction."""
    parent = _seed_parent(_SOURCE_TESTIFIES)
    derived = _derive(parent["id"])

    assert derived["dead_end_confirmation"] is None
    assert derived["provenance"]["reason_code"] == "source_testifies_the_connection"
    detail = derived["provenance"]["detail"]
    assert f"way(s) {_WAY_ON}" in detail
    assert f"node {_NODE}" in detail
    assert "cannot delete road the source declares" in detail


def test_anything_but_a_measured_world_gap_refuses_by_a_named_reason(workspace_root: Path):
    """No confirmation without a MEASUREMENT that the source shares the gap —
    an unreadable source, a way the source does not hold, and an unaddressable
    finding each answer with their own code rather than a shrug."""
    # (a) no source artifact at all -> unit #36 never reached a world gap.
    no_source = _seed_parent(None)
    quiet = _derive(no_source["id"])
    assert quiet["dead_end_confirmation"] is None
    assert quiet["provenance"]["reason_code"] == "world_gap_not_measured"
    assert "source_artifact_missing" in quiet["provenance"]["detail"]

    # (b) the finding names no addressable link.
    parent = _seed_parent(_SOURCE_GAP, name="unaddressable")
    nameless = _derive(parent["id"], {"code": _CODE, "subject": "no id here", "context": {}})
    assert nameless["provenance"]["reason_code"] == "world_gap_not_measured"

    # (c) the census names a way the SOURCE does not declare. The testimony
    # deriver answers ``way_not_in_source`` first, so the confirmation cannot be
    # offered for a way an exclusion parameter would name and never find.
    stranger = _seed_parent(_SOURCE_GAP, name="stranger", approach_way=424242)
    absent = _derive(stranger["id"])
    assert absent["dead_end_confirmation"] is None
    assert absent["provenance"]["reason_code"] == "world_gap_not_measured"
    assert "way_not_in_source" in absent["provenance"]["detail"]


def test_collateral_names_what_the_source_says_would_come_loose(workspace_root: Path):
    """The honesty clause: a confirmed road can be a BRIDGE, and excluding it
    then strands what hangs off it. Measured from the source's own adjacency and
    shown BEFORE the signature, never discovered in the sibling's census."""
    parent = _seed_parent(_SOURCE_BRIDGE)
    offer = _derive(parent["id"])["dead_end_confirmation"]

    assert offer is not None
    assert offer["collateral_way_ids"] == [9009]
    assert offer["collateral"] == [{"way_id": 9009, "name": ""}]
    # The severance is stated as a count too, so a reader can see how much of
    # the source's own component survives.
    assert offer["source_component_ways"] == 3
    assert offer["surviving_component_ways"] == 1


def test_an_exclusion_that_would_empty_the_network_refuses(workspace_root: Path):
    """B5-1's own isolation pass refuses to delete a whole network; a human's
    confirmation gets the same answer, before a job directory exists."""
    parent = _seed_parent(_SOURCE_SINGLETON)
    derived = _derive(parent["id"])

    assert derived["dead_end_confirmation"] is None
    assert derived["provenance"]["reason_code"] == "exclusion_would_empty_the_network"


def _already_excluded(way_id: int, subject: str = "an earlier finding") -> dict[str, Any]:
    """The record shape a job carries after a confirmation it INHERITED — what a
    sibling-of-a-sibling looks like, and what ``create_regenerate_job`` carries
    forward under ``inherited_exclusions``."""
    return {
        "way_id": way_id,
        "reason": SKIP_CONFIRMED_REAL_WORLD_DEAD_END,
        "finding_code": _CODE,
        "subject": subject,
        "parent_link_ids": ["9"],
        "node_id": 6002,
        "collateral_way_ids": [],
        "basis": "an earlier site check",
        "confirmed_by": "someone",
        "confirmed_at": "2026-08-17T00:00:00+00:00",
    }


def test_collateral_is_measured_on_the_network_this_job_holds(workspace_root: Path):
    """REFUTER FIND, closed. The severance was measured against the SOURCE, and
    a source is never edited — so on a sibling that already carries a confirmed
    exclusion, the removed road was still in the graph, still holding up a pocket
    the sibling had already let go of.

    Exploited on ``_SOURCE_SHARED_HINGE``: with way 9007 already excluded, way
    9107 is pocket 9009's only remaining attachment, and confirming 9107 measured
    "this exclusion cuts nothing else loose" while the re-derivation stranded it.
    A person signing for a scope they were shown WRONG is the completed-edit-with-
    wrong-numbers this unit exists to refuse, and ``collateral_not_acknowledged``
    could not catch it: the compare re-derives with the same blind measurement.
    """
    parent = _seed_parent(_SOURCE_SHARED_HINGE, approach_way=9107)
    genesis_jobs._update(parent["id"], excluded_ways=[_already_excluded(_WAY_IN)])

    offer = _derive(parent["id"])["dead_end_confirmation"]
    assert offer is not None
    assert offer["collateral_way_ids"] == [9009], (
        "the pocket the already-excluded way used to hold on must be NAMED"
    )
    # ...and the acknowledgement the pre-fix screen would have sent is refused.
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"], acknowledged_collateral_way_ids=[])
    assert refused.value.code == "genesis_dead_end_not_confirmable"
    assert "collateral_not_acknowledged" in refused.value.reasons[0]

    # The lineage says WHICH graph the number was measured on -- a severance
    # count a later reader cannot reproduce is not a measurement.
    job = _confirm(parent["id"], acknowledged_collateral_way_ids=[9009])
    derivation = job["derived_from"]["dead_end_derivation"]
    assert derivation["measured_without_way_ids"] == [_WAY_IN]
    assert job["excluded_ways"][-1]["collateral_way_ids"] == [9009]


def test_an_ordinary_measurement_names_no_graph_narrowing(workspace_root: Path):
    """Presence is the fact here too: a parent that declared no exclusion is
    measured on its whole source and its provenance says nothing about it, so an
    ordinary derivation is byte-for-byte the one it always was."""
    parent = _seed_parent(_SOURCE_BRIDGE)
    provenance = _derive(parent["id"])["provenance"]
    assert "measured_without_way_ids" not in provenance


@pytest.mark.parametrize(
    ("source", "approach_way", "record_updates", "expect_detail"),
    [
        pytest.param(
            _SOURCE_TWO_DISJOINT_STUBS,
            9107,
            {"excluded_ways": [_already_excluded(_WAY_IN)]},
            "the source it re-reads declares 1 drivable way(s)",
            id="a chain of exclusions",
        ),
        pytest.param(
            _SOURCE_SERVICE_PADDED,
            _WAY_IN,
            {"options": {"exclude_service": True}},
            "every one of this job's 1 link(s) rides it",
            id="an option the source count cannot see",
        ),
    ],
)
def test_emptying_the_network_cannot_be_bought_past_the_source_count(
    workspace_root: Path,
    source: str,
    approach_way: int,
    record_updates: dict[str, Any],
    expect_detail: str,
):
    """REFUTER FIND, closed. The guard counted what the SOURCE declares, and the
    network is not that: ``exclude_service``, the requested-region trim and a
    chain of confirmations each leave ways in the source the generation does not
    hold. Both exploits reached the exact opaque failure this refusal exists to
    say instead of -- a created job directory and ``draft_refused: osm_no_nodes:
    no node coordinates to project``. So the network is now measured too."""
    parent = _seed_parent(source, approach_way=approach_way)
    # A census FAITHFUL to what the generation modelled: a service way the job
    # excluded is not in it, which is exactly what the source count cannot see.
    if "options" in record_updates:
        census_path = genesis_jobs.job_dir(parent["id"]) / "v3_parent.genesis.json"
        census = json.loads(census_path.read_text(encoding="utf-8"))
        census["links"] = [link for link in census["links"] if link["id"] == 3]
        census["connectors"] = []
        census_path.write_text(json.dumps(census), encoding="utf-8")
    genesis_jobs._update(parent["id"], **record_updates)

    derived = _derive(parent["id"])
    assert derived["dead_end_confirmation"] is None
    assert derived["provenance"]["reason_code"] == "exclusion_would_empty_the_network"
    assert expect_detail in derived["provenance"]["detail"]

    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"])
    assert refused.value.code == "genesis_dead_end_not_confirmable"
    assert "exclusion_would_empty_the_network" in refused.value.reasons[0]


@pytest.mark.parametrize(
    ("confirmation", "expected"),
    [
        ({"basis": "", "confirmed_by": "yunha", "confirmed_at": "2026-08-18T00:00:00Z"},
         "confirmation_without_basis"),
        ({"basis": "checked it", "confirmed_by": "", "confirmed_at": "2026-08-18T00:00:00Z"},
         "confirmation_without_attribution"),
        ({"basis": "checked it", "confirmed_by": "yunha", "confirmed_at": ""},
         "confirmation_without_attribution"),
        ({"basis": "checked it", "confirmed_by": "yunha", "confirmed_at": "2026-08-18T00:00:00Z",
          "way_id": 9001},
         "confirmation_way_mismatch"),
        ({"basis": "checked it", "confirmed_by": "yunha", "confirmed_at": "2026-08-18T00:00:00Z",
          "acknowledged_collateral_way_ids": [9009]},
         "collateral_not_acknowledged"),
    ],
)
def test_an_incomplete_confirmation_is_refused_never_completed(
    workspace_root: Path, confirmation: dict[str, Any], expected: str
):
    """Each missing or mismatched piece has its OWN name, and none of them is
    filled in on a human's behalf — completing an attribution would make this
    system the confirmer."""
    parent = _seed_parent(_SOURCE_GAP)
    derived = _derive(parent["id"], confirmation=confirmation)

    assert derived["dead_end_confirmation"] is None
    assert derived["provenance"]["reason_code"] == expected


def test_a_confirmation_may_omit_the_way_but_never_name_a_different_one(workspace_root: Path):
    """The way is DERIVED from the finding's own census record. Omitting it is
    the HTTP surface's own shape (there is no field), and asserting a different
    one is the defence-in-depth this keeps for every other caller."""
    parent = _seed_parent(_SOURCE_GAP)
    complete = {
        "basis": "site check",
        "confirmed_by": "yunha",
        "confirmed_at": "2026-08-18T00:00:00Z",
    }
    omitted = _derive(parent["id"], confirmation=complete)
    assert omitted["dead_end_confirmation"]["confirmed"]["way_id"] == _WAY_IN
    assert omitted["dead_end_confirmation"]["confirmed"]["basis"] == "site check"
    assert omitted["dead_end_confirmation"]["confirmed"]["confirmed_by"] == "yunha"

    asserted = _derive(parent["id"], confirmation={**complete, "way_id": _WAY_IN})
    assert asserted["dead_end_confirmation"]["confirmed"]["way_id"] == _WAY_IN


# ---------------------------------------------------------------------------
# §2 The job: one declarative parameter, the whole attribution, no bypass
# ---------------------------------------------------------------------------


def test_the_job_carries_the_exclusion_declaratively_with_its_whole_attribution(
    workspace_root: Path,
):
    parent = _seed_parent(_SOURCE_GAP)
    record = _confirm(parent["id"])

    assert record["status"] == genesis_jobs.STATUS_QUEUED
    assert record["derived_from"]["kind"] == genesis_jobs.DEAD_END_EXCLUSION_LINEAGE_KIND
    assert record["derived_from"]["job_id"] == parent["id"]
    # The parent's own options ride verbatim: a re-derivation that quietly
    # changed exclude_service would be answering a different question.
    assert record["options"] == parent["options"]
    assert record["side_inputs"] is None

    (exclusion,) = record["excluded_ways"]
    assert exclusion["way_id"] == _WAY_IN
    assert exclusion["reason"] == SKIP_CONFIRMED_REAL_WORLD_DEAD_END
    assert exclusion["finding_code"] == _CODE
    assert exclusion["subject"] == "approach 3"
    # NAMED as the parent's: removing a way renumbers every link after it, so
    # these address the network the confirmer was looking at, never this job's.
    assert exclusion["parent_link_ids"] == ["3"]
    assert "link_ids" not in exclusion
    assert exclusion["collateral_way_ids"] == []
    assert exclusion["basis"] == "구글맵 현장 확인 — 백화점 내부 순환로"
    assert exclusion["confirmed_by"] == "yunha"
    assert exclusion["confirmed_at"]
    # The lineage carries the same record AND the derivation that produced it,
    # so a reader can see what was measured before a person was asked.
    assert record["derived_from"]["exclusions"] == [exclusion]
    assert record["derived_from"]["dead_end_derivation"]["way_id"] == _WAY_IN
    assert record["derived_from"]["findings"] == [
        {
            "code": _CODE,
            "subject": "approach 3",
            "derivation_reason_code": "source_shares_the_gap",
        }
    ]


def test_the_source_is_evidence_the_parent_is_byte_untouched(workspace_root: Path):
    parent = _seed_parent(_SOURCE_GAP)
    parent_dir = genesis_jobs.job_dir(parent["id"])
    before = {p.name: p.read_bytes() for p in parent_dir.iterdir() if p.is_file()}

    record = _confirm(parent["id"])

    after = {p.name: p.read_bytes() for p in parent_dir.iterdir() if p.is_file()}
    assert after == before, "a sibling job may only READ its parent"
    # The copy is proven equal to what the parent recorded -- the exclusion is a
    # PARAMETER, not an edit to the bytes.
    assert record["source_osm"]["sha256"] == parent["source_osm"]["sha256"]
    assert record["derived_from"]["source_sha256"] == parent["source_osm"]["sha256"]
    assert record["derived_from"]["parent_source_sha256"] == parent["source_osm"]["sha256"]
    child_source = genesis_jobs.job_dir(record["id"]) / "source.osm"
    assert child_source.read_bytes() == (parent_dir / "source.osm").read_bytes()


def test_unit_38s_four_doors_are_shared_code_not_a_near_copy(workspace_root: Path):
    """A path that relaxed one of the regeneration's own refusals for itself
    would be exactly the bypass #38's docstring warns about — so the codes and
    the sentences are literally #38's, reached through one function."""
    # (a) a parent with no verdict.
    ungated = _seed_parent(_SOURCE_GAP, name="ungated", gated=False)
    with pytest.raises(genesis_jobs.GenesisRefused) as not_gated:
        _confirm(ungated["id"])
    assert not_gated.value.code == "genesis_regenerate_parent_not_gated"

    # (b) a parent composed with side inputs -- a re-derivation is geometry only.
    composed = _seed_parent(_SOURCE_GAP, name="composed")
    genesis_jobs._update(composed["id"], side_inputs=["demand"])
    with pytest.raises(genesis_jobs.GenesisRefused) as side:
        _confirm(composed["id"])
    assert side.value.code == "genesis_regenerate_parent_has_side_inputs"

    # (c) the stored bytes no longer hash to the recorded digest.
    drifted = _seed_parent(_SOURCE_GAP, name="drifted")
    (genesis_jobs.job_dir(drifted["id"]) / "source.osm").write_text(
        _SOURCE_TESTIFIES, encoding="utf-8", newline=""
    )
    with pytest.raises(genesis_jobs.GenesisRefused) as sha:
        _confirm(drifted["id"])
    assert sha.value.code == "genesis_regenerate_source_sha_mismatch"

    # (d) no stored source at all.
    sourceless = _seed_parent(None, name="sourceless")
    with pytest.raises(genesis_jobs.GenesisRefused) as missing:
        _confirm(sourceless["id"])
    assert missing.value.code == "genesis_regenerate_source_missing"


def test_no_job_directory_survives_a_refused_confirmation(workspace_root: Path):
    """Every door above closes BEFORE a job id is minted, so a refusal leaves
    nothing on disk to be reconciled later."""
    parent = _seed_parent(_SOURCE_TESTIFIES)
    before = {p.name for p in genesis_jobs.jobs_root().iterdir()}
    with pytest.raises(genesis_jobs.GenesisRefused):
        _confirm(parent["id"])
    assert {p.name for p in genesis_jobs.jobs_root().iterdir()} == before


def test_a_finding_the_parents_verdict_never_made_refuses_by_name(workspace_root: Path):
    parent = _seed_parent(_SOURCE_GAP)
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"], subject="a road nobody diagnosed")
    assert refused.value.code == "genesis_dead_end_finding_not_in_parent_verdict"


def test_a_duplicated_code_without_a_subject_refuses_rather_than_guessing(workspace_root: Path):
    """Unit #41 measured the real state this guards (V3 emits the same
    (code, subject) twice on the live job), and the answer is #30's own — the
    SAME code, unrenamed, because it is a statement about addressing a finding
    rather than about which treatment is being applied."""
    parent = _seed_parent(_SOURCE_GAP, with_twin=True)
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"], subject=None)
    assert refused.value.code == "genesis_fix_finding_ambiguous_without_subject"


def test_a_testified_finding_cannot_be_confirmed_away_at_the_job_door_either(
    workspace_root: Path,
):
    """The evidence hierarchy is not merely a rendering: the create path
    re-derives the measurement and refuses, quoting it."""
    parent = _seed_parent(_SOURCE_TESTIFIES)
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"])
    assert refused.value.code == "genesis_dead_end_not_confirmable"
    assert "source_testifies_the_connection" in refused.value.reasons[0]


def test_a_confirmation_with_no_basis_refuses_at_the_job_door(workspace_root: Path):
    parent = _seed_parent(_SOURCE_GAP)
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"], basis="   ")
    assert refused.value.code == "genesis_dead_end_not_confirmable"
    assert "confirmation_without_basis" in refused.value.reasons[0]


def test_an_unacknowledged_severance_refuses_at_the_job_door(workspace_root: Path):
    """The reader must have SEEN what they are removing. A stale screen's empty
    acknowledgement against a real severance is refused, never reconciled."""
    parent = _seed_parent(_SOURCE_BRIDGE)
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"], acknowledged_collateral_way_ids=[])
    assert refused.value.code == "genesis_dead_end_not_confirmable"
    assert "collateral_not_acknowledged" in refused.value.reasons[0]

    # ...and it goes through once the severance is acknowledged, with the
    # acknowledged ways recorded in the ledger entry.
    record = _confirm(parent["id"], acknowledged_collateral_way_ids=[9009])
    assert record["excluded_ways"][0]["collateral_way_ids"] == [9009]


def test_a_basis_too_long_for_the_ledger_refuses_rather_than_being_cut_in_half(
    workspace_root: Path,
):
    """The ledger quotes a confirmer's own words VERBATIM. Silently truncating
    them would make the record quote somebody saying something they did not."""
    parent = _seed_parent(_SOURCE_GAP)
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"], basis="가" * (genesis_jobs.DEAD_END_BASIS_MAX_CHARS + 1))
    assert refused.value.code == "genesis_dead_end_not_confirmable"
    assert "confirmation_basis_too_long" in refused.value.reasons[0]


def test_a_chain_of_confirmations_accumulates_and_never_reverses_one(workspace_root: Path):
    """REFUTER FIND, closed. A second confirmation used to build its record from
    the parent's ``options`` alone — so confirming a second dead end on a
    sibling silently RE-INTRODUCED the way the first confirmer took
    responsibility for removing. An accountable decision reversed by a path that
    never mentions it is exactly what this unit exists to prevent."""
    # ONE parent carrying two independent world gaps (link 3 on way 9007, link 6
    # on way 9107), each with its own dangling census entry.
    parent = _seed_parent(_SOURCE_TWO_GAPS, with_twin=True)
    first = _confirm(parent["id"], subject="approach 3")
    assert [e["way_id"] for e in first["excluded_ways"]] == [_WAY_IN]

    # The SECOND confirmation is made against a parent that already carries the
    # first (the shape a sibling-of-a-sibling has). It must ACCUMULATE.
    genesis_jobs._update(parent["id"], excluded_ways=first["excluded_ways"])
    chained = _confirm(parent["id"], subject="approach 6")

    assert [e["way_id"] for e in chained["excluded_ways"]] == [_WAY_IN, 9107], (
        "the parent's own exclusion leads and the new one appends"
    )
    assert chained["derived_from"]["confirmed"]["way_id"] == 9107
    assert [e["way_id"] for e in chained["derived_from"]["exclusions"]] == [_WAY_IN, 9107]
    # The first confirmer's own words survive the chain verbatim.
    assert chained["excluded_ways"][0]["basis"] == "구글맵 현장 확인 — 백화점 내부 순환로"


def test_confirming_the_same_way_twice_refuses_by_name(workspace_root: Path):
    parent = _seed_parent(_SOURCE_TWO_GAPS, with_twin=True)
    first = _confirm(parent["id"], subject="approach 3")
    genesis_jobs._update(parent["id"], excluded_ways=first["excluded_ways"])
    with pytest.raises(genesis_jobs.GenesisRefused) as refused:
        _confirm(parent["id"], subject="approach 3")
    assert refused.value.code == "genesis_dead_end_already_excluded"


def test_a_plain_regeneration_carries_a_confirmed_exclusion_forward_and_names_it(
    tmp_path: Path, workspace_root: Path
):
    """REFUTER FIND, closed. Unit #38's regeneration claims "the same source"
    — and a confirmed dead end is one of its inputs. Dropping it would silently
    reverse a named person's decision, invisibly, under a claim of sameness."""
    # A REAL regeneration-eligible parent (unit #38's own fixture: a source
    # declaring one way id twice, generated by the pre-fix converter), standing
    # in for a job that had itself been re-derived with a confirmed exclusion.
    parent = _pre_fix_parent(tmp_path)
    exclusion = {
        "way_id": _WAY_IN,
        "reason": SKIP_CONFIRMED_REAL_WORLD_DEAD_END,
        "finding_code": _CODE,
        "subject": "approach 3",
        "parent_link_ids": ["3"],
        "node_id": _NODE,
        "collateral_way_ids": [],
        "basis": "site check",
        "confirmed_by": "yunha",
        "confirmed_at": "2026-08-18T00:00:00+00:00",
    }
    genesis_jobs._update(parent["id"], excluded_ways=[exclusion])

    regenerated = genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")

    assert regenerated["excluded_ways"] == [exclusion], "a human's exclusion is not reversed"
    assert regenerated["derived_from"]["inherited_exclusions"] == [exclusion], (
        "...and the lineage SAYS so -- a network missing a road the source declares must be "
        "explainable from the record alone"
    )


def test_an_ordinary_regeneration_gains_no_exclusion_key(tmp_path: Path, workspace_root: Path):
    """Presence is the fact here too: a parent that declared no exclusion
    produces a record byte-for-byte the one unit #38 always produced."""
    parent = _pre_fix_parent(tmp_path, name="plain")
    regenerated = genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")

    assert "excluded_ways" not in regenerated
    assert "inherited_exclusions" not in regenerated["derived_from"]


# ---------------------------------------------------------------------------
# §3 The run: the exclusion applies, and it applies as a LEDGER ENTRY
# ---------------------------------------------------------------------------


def test_the_re_derivation_removes_the_way_and_writes_the_whole_ledger(workspace_root: Path):
    """End to end through the REAL pipeline and the REAL gate: the confirmed way
    is gone from the sibling's network, and WHY it is gone is legible from the
    census and the compliance report alone (B5-1's rule for an exclusion)."""
    parent = _seed_parent(_SOURCE_GAP)
    record = _confirm(parent["id"])
    genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])
    child = genesis_jobs.get_job(record["id"])

    assert child["status"] in genesis_jobs.GATED_STATUSES, child
    child_draft = genesis_jobs.job_draft_or_none(record["id"])
    assert child_draft is not None
    # The way is gone -- and the corridor it hung off is NOT.
    assert not any(int(l.osm_way_id) == _WAY_IN for l in child_draft.links)
    assert any(int(l.osm_way_id) == 9001 for l in child_draft.links)

    ledger = [
        s for s in child_draft.skipped_ways if s["reason"] == SKIP_CONFIRMED_REAL_WORLD_DEAD_END
    ]
    assert len(ledger) == 1, child_draft.skipped_ways
    detail = ledger[0]["detail"]
    assert ledger[0]["way_id"] == _WAY_IN
    assert "yunha" in detail
    assert _CODE in detail
    assert "approach 3" in detail
    assert "구글맵 현장 확인 — 백화점 내부 순환로" in detail, "the basis is quoted VERBATIM"

    compliance = next(genesis_jobs.job_dir(record["id"]).glob("*.compliance.md")).read_text(
        encoding="utf-8"
    )
    assert SKIP_CONFIRMED_REAL_WORLD_DEAD_END in compliance
    assert str(_WAY_IN) in compliance


def test_a_severance_the_exclusion_causes_buys_no_pass_from_the_judge(workspace_root: Path):
    """Spec item 3, end to end, and the guarantee is stronger than the ledger:
    **a confirmation cannot make the gate say yes.**

    A confirmed way that was carrying other roads leaves them stranded, and the
    sibling is judged for that by the SAME unmodified gate — it comes back
    REFUSED, naming the very ways the pre-flight measurement warned about. So
    the worst case of a confirmation is a refused sibling a reader can throw
    away, never a green artifact with a hole in it. Measured on this fixture:
    excluding way 9007 removes the network's only junction, so every remaining
    link is an orphan and V3 says so about all four of them.

    (The B5-1 isolation pass is the OTHER outcome — it removes and names an
    unreachable pocket where it finds one. Which of the two fires is the
    pipeline's own measurement, and this test pins the part that must hold in
    both: the severance is never silently accepted.)"""
    parent = _seed_parent(_SOURCE_BRIDGE)
    record = _confirm(parent["id"], acknowledged_collateral_way_ids=[9009])
    assert record["excluded_ways"][0]["collateral_way_ids"] == [9009]

    genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])
    child = genesis_jobs.get_job(record["id"])
    child_draft = genesis_jobs.job_draft_or_none(record["id"])
    assert child_draft is not None

    by_way = {skip["way_id"]: skip["reason"] for skip in child_draft.skipped_ways}
    assert by_way.get(_WAY_IN) == SKIP_CONFIRMED_REAL_WORLD_DEAD_END

    assert child["status"] == genesis_jobs.STATUS_REFUSED, (
        "an exclusion that strands roads must not produce a passing artifact"
    )
    raw = json.loads(
        (genesis_jobs.job_dir(record["id"]) / genesis_jobs.GATE_JSON_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    subjects = [f["subject"] for _c, _o, f in genesis_jobs.iter_stored_gate_findings(raw)]
    assert any("w9009" in s for s in subjects), (
        f"the ways the pre-flight warned about must be NAMED in the verdict: {subjects}"
    )
    # ...and the confirmed way is not among them, because it is not in the
    # network any more -- which is the whole point of the treatment.
    assert not any(f"w{_WAY_IN}" in s for s in subjects)


def test_the_judge_is_untouched_the_finding_goes_because_the_way_does(workspace_root: Path):
    """V3 stops firing for the excluded way because the way is not in the
    network — never because a verdict was rewritten. The parent's own stored
    verdict is byte-identical afterwards, and still names the finding."""
    parent = _seed_parent(_SOURCE_GAP)
    stored = genesis_jobs.job_dir(parent["id"]) / genesis_jobs.GATE_JSON_FILENAME
    before = stored.read_bytes()

    record = _confirm(parent["id"])
    genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])

    assert stored.read_bytes() == before
    raw = json.loads(before.decode("utf-8"))
    assert any(
        f["code"] == _CODE and f["subject"] == "approach 3"
        for _c, _o, f in genesis_jobs.iter_stored_gate_findings(raw)
    ), "the parent's verdict still says what it said"

    child_raw = json.loads(
        (genesis_jobs.job_dir(record["id"]) / genesis_jobs.GATE_JSON_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    child_way_ids = {
        int(l.osm_way_id) for l in genesis_jobs.job_draft_or_none(record["id"]).links
    }
    assert _WAY_IN not in child_way_ids
    # Whatever the sibling's own verdict is, it is the UNMODIFIED gate's: no
    # finding is suppressed, and V3's own check still ran.
    assert {c["id"] for c in child_raw["checks"]} >= {"V3"}


def test_a_declared_way_the_parse_does_not_hold_refuses_rather_than_generating(
    workspace_root: Path,
):
    """"The exclusion I declared did not apply" is precisely the silent
    difference between what a job claims and what it did. Unreachable through
    the real create path (the way was verified against the same source), and
    refused anyway."""
    parent = _seed_parent(_SOURCE_GAP)
    record = _confirm(parent["id"])
    tampered = dict(record["excluded_ways"][0])
    tampered["way_id"] = 424242
    genesis_jobs._update(record["id"], excluded_ways=[tampered])

    genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])
    child = genesis_jobs.get_job(record["id"])

    assert child["status"] == genesis_jobs.STATUS_ERROR
    assert child["refusal"]["code"] == "dead_end_excluded_way_not_in_source"
    assert "424242" in child["refusal"]["reasons"][0]
    assert child["artifact"] is None


def test_an_ordinary_generation_is_untouched_by_the_exclusion_step(workspace_root: Path):
    """A job with no declared exclusion gets the SAME extract object back — the
    only acceptable cost of adding a step to the shared runner."""
    parent = _seed_parent(_SOURCE_GAP)
    extract = parse_osm_file(genesis_jobs.job_dir(parent["id"]) / "source.osm")
    assert (
        genesis_jobs._apply_declared_exclusions("nobody", {"id": "nobody"}, extract) is extract
    )
    assert (
        genesis_jobs._apply_declared_exclusions("nobody", {"excluded_ways": []}, extract)
        is extract
    )


# ---------------------------------------------------------------------------
# §4 The registry: the invitation rides the refusal, and the tier never moves
# ---------------------------------------------------------------------------


def test_the_invitation_rides_the_refusal_in_three_languages_without_moving_the_tier(
    workspace_root: Path, client: TestClient
):
    parent = _seed_parent(_SOURCE_GAP)
    disposition = _disposition(client, parent["id"])

    # TIER UNCHANGED -- byte-for-byte the shape unit #35's own world-gap test
    # pins (the mapping's declared tier, its verb named, and NOTHING to apply).
    # Unit #38's REGENERATE_WITH_FIX is the only enrichment in this family that
    # moves a status, and this one deliberately is not it: confirming is a human
    # act that starts a NEW job, and an entry that read as a half-applied fix
    # would be inviting exactly the click this unit exists to make accountable.
    assert disposition["status"] == agent_fixes.AUTOMATABLE  # the mapping's declared tier
    assert disposition["status"] != genesis_fixes.REGENERATE_WITH_FIX
    assert disposition["verb"] == "add_connector"
    assert disposition["params"] is None  # ...and NOTHING to apply: not actionable
    assert disposition["requires"] == []
    assert disposition["reason_code"] == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED

    offer = disposition["dead_end_confirmation"]
    assert offer["way_id"] == _WAY_IN
    assert offer["link_ids"] == ["3"]
    assert "dead_end_refusal" not in disposition

    # Both questions ride the SAME refusal -- the road may continue to a
    # candidate, or it may end -- and only a person can say which.
    assert disposition["attestation"]["candidates"]
    ko, en, ja = (disposition["rationale"][lang] for lang in ("ko", "en", "ja"))
    assert "실제 도로가 여기서 이어집니까?" in ko
    assert "아니면 실제로 여기서 도로가 끝납니까?" in ko
    assert f"way {_WAY_IN}" in ko and "명명 제외" in ko
    assert "Does the real road continue here?" in en
    assert "Or does the road really END here?" in en
    assert "excluded BY NAME" in en and "not edited" in en
    assert "ledger entry, never a silent deletion" in en
    assert "それとも実際にここで道路が終わりますか?" in ja
    # The zero-severance case is a MEASUREMENT, stated, not an absence.
    assert "새로 끊기는 도로는 없습니다" in ko
    assert "cuts nothing else loose" in en


def test_a_severance_is_named_in_the_invitation_itself(workspace_root: Path, client: TestClient):
    parent = _seed_parent(_SOURCE_BRIDGE)
    disposition = _disposition(client, parent["id"])

    assert disposition["dead_end_confirmation"]["collateral_way_ids"] == [9009]
    assert "way 9009도 함께 끊깁니다" in disposition["rationale"]["ko"]
    assert "also cut loose way(s) 9009" in disposition["rationale"]["en"]


def test_the_hierarchy_refusal_surfaces_as_prose_AND_as_data(
    workspace_root: Path, client: TestClient
):
    """A reader who wants to erase a gap the source declares is told why they
    may not — never left to wonder where the option went.

    Reached on a testified finding whose CONNECTOR could not be built (the
    census holds no link carrying the testified way that begins at the node), so
    the entry is still a refusal a reader is looking at options on. A testified
    finding that DOES reach a connector is a live proposal, and a card offering
    a treatment has no dead end to discuss."""
    parent = _seed_parent(_SOURCE_TESTIFIES, continuation_begins_at_the_node=False)
    disposition = _disposition(client, parent["id"])

    assert "dead_end_confirmation" not in disposition
    assert disposition["params"] is None, "still a refusal, not a proposal"
    assert disposition["dead_end_refusal"]["reason_code"] == "source_testifies_the_connection"
    assert "cannot delete road the source declares" in disposition["dead_end_refusal"]["detail"]
    assert "증거 위계" in disposition["rationale"]["ko"]
    assert "Evidence hierarchy" in disposition["rationale"]["en"]
    assert "証拠の階層" in disposition["rationale"]["ja"]
    # ...and it is a SENTENCE, not an offer: nothing here can be pressed.
    assert "아니면 실제로 여기서 도로가 끝납니까?" not in disposition["rationale"]["ko"]


def test_a_quiet_measured_reason_gets_no_third_sentence_and_no_key(
    workspace_root: Path, client: TestClient
):
    """``world_gap_not_measured`` deliberately has no clause: unit #36's own
    testimony sentence already says it in the same card, and a third repetition
    is noise a reader learns to skip. The payload key and the prose agree by
    construction — the reason table decides both."""
    parent = _seed_parent(None)
    disposition = _disposition(client, parent["id"])

    assert "dead_end_confirmation" not in disposition
    assert "dead_end_refusal" not in disposition
    assert "실세계 막다른 길" not in disposition["rationale"]["ko"]


# ---------------------------------------------------------------------------
# §5 The HTTP surface
# ---------------------------------------------------------------------------


def test_the_route_stamps_the_confirmer_and_offers_no_field_to_forge_one(
    workspace_root: Path, client: TestClient
):
    parent = _seed_parent(_SOURCE_GAP)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/dead-end-confirmation",
        json={
            "finding_code": _CODE,
            "subject": "approach 3",
            "basis": "구글맵 현장 확인",
            "acknowledged_collateral_way_ids": [],
        },
        headers=admin_headers(),
    )
    assert response.status_code == 202, response.text
    record = response.json()
    (exclusion,) = record["excluded_ways"]
    assert exclusion["confirmed_by"] == "suite-admin"
    assert exclusion["confirmed_at"]
    assert exclusion["way_id"] == _WAY_IN
    assert record["derived_from"]["requested_by"] == "suite-admin"
    assert record["submitted_by"] == "suite-admin"

    # Drain the daemon thread before the workspace fixture tears the root down --
    # the same discipline unit #38's own 202 test follows.
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        status = client.get(f"/api/genesis/jobs/{record['id']}").json()["status"]
        if status in genesis_jobs.TERMINAL_STATUSES:
            break
        time.sleep(0.02)
    else:  # pragma: no cover - a wedged runner is the failure, not a flake
        pytest.fail("the re-derivation did not reach a terminal status in time")
    assert status in genesis_jobs.GATED_STATUSES

    # There is nowhere in the body to name a confirmer, a timestamp, or -- the
    # one this unit adds -- the way to remove. Each is rejected by the model
    # itself, so a caller cannot widen an exclusion or attribute it to somebody
    # else even by trying.
    for forged in ("confirmed_by", "confirmed_at", "way_id"):
        rejected = client.post(
            f"/api/genesis/jobs/{parent['id']}/dead-end-confirmation",
            json={
                "finding_code": _CODE,
                "subject": "approach 3",
                "basis": "x",
                "acknowledged_collateral_way_ids": [],
                forged: "somebody else",
            },
            headers=admin_headers(),
        )
        assert rejected.status_code == 422, (forged, rejected.text)


def test_the_route_refuses_by_code_and_never_anonymously(
    workspace_root: Path, client: TestClient
):
    parent = _seed_parent(_SOURCE_TESTIFIES)
    body = {
        "finding_code": _CODE,
        "subject": "approach 3",
        "basis": "site check",
        "acknowledged_collateral_way_ids": [],
    }

    anonymous = client.post(
        f"/api/genesis/jobs/{parent['id']}/dead-end-confirmation", json=body
    )
    assert anonymous.status_code == 401, anonymous.text

    missing = client.post(
        "/api/genesis/jobs/deadbeef0000/dead-end-confirmation", json=body, headers=admin_headers()
    )
    assert missing.status_code == 404, missing.text

    refused = client.post(
        f"/api/genesis/jobs/{parent['id']}/dead-end-confirmation",
        json=body,
        headers=admin_headers(),
    )
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "genesis_dead_end_not_confirmable"
    assert "source_testifies_the_connection" in detail["reasons"][0]

    empty_basis = client.post(
        f"/api/genesis/jobs/{parent['id']}/dead-end-confirmation",
        json={**body, "basis": ""},
        headers=admin_headers(),
    )
    assert empty_basis.status_code == 422, "a confirmation with no basis is not a claim"

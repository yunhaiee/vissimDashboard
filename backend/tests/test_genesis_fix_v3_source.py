"""Unit #36 — the SOURCE testifies the connection the network lost.

Every V3 connectivity finding is (correctly) ``NOT_AUTOMATABLE`` under
``CONNECTIVITY_IS_NOT_INVENTED`` — but that refusal covers two very different
worlds: the source ``.osm`` itself declares no onward way (the WORLD ends
there), or the source declares two ways sharing a node and the PIPELINE lost
the connection between parse and emission. ``genesis_jobs.derive_v3_source_testimony``
reads the job's own stored source at the finding's location and answers which,
with provenance; the registry's V3 builder folds the measured answer into the
refusal's rationale VERBATIM; the fixes listing surfaces the testimony dict.
The tier NEVER changes with the answer — the unit's value is the named split,
not a re-tier.

Measured on the real refused jobs as of 2026-08-15 (28 v3 findings, all
``v3_silent_departure_point``, across 5 jobs in 2 workspaces): 5 source-
testified / 23 source-shares-the-gap / 0 source-missing / 0 unmappable —
confirming B5-1's "genuine world-gap" reading for most, and correcting it for
the 5 whose connection the source declares (e.g. job b3ef6abb9a85's
service way 1232703541 continuing past node 4941371397).

Fixture shape follows ``tests/test_genesis_fix_v7_polyline.py`` exactly: a
private tmp workspace per test, hand-written parent networks through
``_seed_job`` + the REAL gate where a stored verdict is needed, a hand-written
source ``.osm`` parsed by the REAL ``services.genesis.osm`` parser, no COM
anywhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces
from services.agent import fixes as agent_fixes
from services.agent import genesis_fixes
from services.genesis.draft import DraftNetwork

from tests.test_genesis_gate import (
    _DEMAND_SUPPORT as demand_support,
)
from tests.test_genesis_gate import _SUPPORT as gate_support
from tests.test_genesis_gate import _demand as demand_layer
from tests.test_genesis_gate import link, network, straight_pair
from tests.test_genesis_jobs import _seed_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as ``test_genesis_fix_jobs.py``'s fixture of the same name —
    duplicated rather than imported across test modules (the repo's existing
    convention only cross-imports plain helper FUNCTIONS)."""
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
# Network / census / source fixtures
# ---------------------------------------------------------------------------

_ORPHAN_CODE = "v3_orphan_link"
_SILENT_CODE = "v3_silent_departure_point"

#: The questioned end: draft link 7 travels way 9007 FWD and ends at node 5002.
_WAY = 9007
_NODE = 5002


def _orphan_network() -> str:
    """``straight_pair`` (drivable, so demand checks stay quiet) plus link 7
    with no connector at all — the real gate refuses it ``v3_orphan_link``
    with subject ``"7 (link 7)"``, no census scaffolding required."""
    links = list(straight_pair()) + [link(7, [(0.0, 300.0), (100.0, 300.0)])]
    return network(links=links, extra=demand_layer(), support=gate_support + demand_support)


def _census_link(
    link_id: int,
    way_id: int,
    *,
    from_node: int,
    to_node: int,
    forward: bool = True,
) -> dict[str, Any]:
    """One VALID ``DraftLink`` record whose OSM provenance the test controls."""
    return {
        "id": link_id,
        "name": f"link {link_id}",
        "osm_way_id": way_id,
        "segment_index": 0,
        "forward": forward,
        "lanes": 1,
        "lane_width_m": 3.5,
        "points": [[0.0, 300.0], [100.0, 300.0]],
        "lane_provenance": {},
        "highway": "residential",
        "from_node_osm_id": from_node,
        "to_node_osm_id": to_node,
        "side_offset_m": 0.0,
    }


def _census(
    link_entries: list[dict[str, Any]],
    *,
    dangling: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A census that VALIDATES as a real ``DraftNetwork``, so
    ``job_draft_or_none`` parses it and the deriver can walk
    ``links[]`` and ``metadata.connectivity.dangling_approaches``."""
    return {
        "name": "seeded",
        "source_osm": "seed.osm",
        "metadata": {"connectivity": {"dangling_approaches": dangling or []}},
        "links": link_entries,
    }


def _osm_node(node_id: int, lat: float, lon: float) -> str:
    return f'  <node id="{node_id}" lat="{lat}" lon="{lon}"/>\n'


def _osm_way(
    way_id: int,
    node_ids: list[int],
    *,
    oneway: str | None = None,
    highway: str = "residential",
    junction: str | None = None,
) -> str:
    nds = "".join(f'    <nd ref="{n}"/>\n' for n in node_ids)
    tags = f'    <tag k="highway" v="{highway}"/>\n'
    if oneway is not None:
        tags += f'    <tag k="oneway" v="{oneway}"/>\n'
    if junction is not None:
        tags += f'    <tag k="junction" v="{junction}"/>\n'
    return f'  <way id="{way_id}">\n{nds}{tags}  </way>\n'


def _osm(*elements: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="unit36-test">\n' + "".join(elements) + "</osm>\n"
    )


#: Three real-shaped nodes around the questioned end.
_NODES = (
    _osm_node(5001, 36.3500, 127.3800)
    + _osm_node(_NODE, 36.3510, 127.3810)
    + _osm_node(5003, 36.3520, 127.3820)
)

#: The source declares the connection: way 9007 ends at node 5002, and
#: two-way way 9008 departs from that same node.
_SOURCE_TESTIFIES = _osm(_NODES, _osm_way(_WAY, [5001, _NODE]), _osm_way(9008, [_NODE, 5003]))

#: The source shares the gap: way 9007 ends at node 5002 and nothing else
#: touches it.
_SOURCE_GAP = _osm(_NODES, _osm_way(_WAY, [5001, _NODE]))


def _attach_source_osm(record: dict[str, Any], source_xml: str) -> None:
    """Store a source ``.osm`` on a seeded job the way a real generation does
    — the file under the job dir, the record's ``stored_as`` naming it (the
    unit-#17 resolution ``artifact_path`` reads)."""
    path = genesis_jobs.job_dir(record["id"]) / "source.osm"
    path.write_text(source_xml, encoding="utf-8")
    record["source_osm"] = {
        "filename": "source.osm",
        "bytes": path.stat().st_size,
        "sha256": "",
        "stored_as": "source.osm",
    }
    genesis_jobs._upsert(record)


def _seed_v3_refused_parent(
    census: dict[str, Any], source_xml: str | None, *, name: str = "v3_parent"
) -> dict[str, Any]:
    record = _seed_job(_orphan_network(), census=census, name=name)
    if source_xml is not None:
        _attach_source_osm(record, source_xml)
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _orphan_entry(client: TestClient, job_id: str) -> dict[str, Any]:
    listed = client.get(f"/api/genesis/jobs/{job_id}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    entries = [
        e
        for e in listed.json()["findings"]
        if e["code"] == _ORPHAN_CODE and e["subject"] == "7 (link 7)"
    ]
    assert len(entries) == 1, entries
    return entries[0]


# ---------------------------------------------------------------------------
# §0 The code tuple mirrors the registry (drift protection for the deriver)
# ---------------------------------------------------------------------------


def test_v3_code_tuple_mirrors_the_fix_registry():
    """Every code the deriver answers for is a registered NOT_AUTOMATABLE
    ``CONNECTIVITY_IS_NOT_INVENTED`` mapping carrying the testimony builder,
    and every registered v3 code is one the deriver answers for — a new v3
    code cannot silently fall between."""
    for code in genesis_jobs.V3_CONNECTIVITY_FINDING_CODES:
        mapping = genesis_fixes.get_genesis_fix(code)
        assert mapping is not None, code
        assert mapping.status == agent_fixes.NOT_AUTOMATABLE, code
        assert mapping.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED, code
        assert mapping.verb is None, code
        assert mapping.builder is not None, code
    registered_v3 = {
        mapping.code
        for mapping in genesis_fixes.get_genesis_fix_registry()
        if mapping.code.startswith("v3_")
    }
    assert registered_v3 == set(genesis_jobs.V3_CONNECTIVITY_FINDING_CODES)


# ---------------------------------------------------------------------------
# §1 The source testifies: the pipeline lost a declared connection
# ---------------------------------------------------------------------------


def test_source_testified_connection_flows_into_the_rendered_refusal(
    workspace_root: Path, client: TestClient
):
    parent = _seed_v3_refused_parent(
        _census([_census_link(7, _WAY, from_node=5001, to_node=_NODE)]), _SOURCE_TESTIFIES
    )

    # The deriver itself: exact ids, with provenance naming its source.
    draft = genesis_jobs.job_draft_or_none(parent["id"])
    finding = {"code": _ORPHAN_CODE, "subject": "7 (link 7)", "context": {}}
    derivation = genesis_jobs.derive_v3_source_testimony(
        _ORPHAN_CODE, finding, draft=draft, job_id=parent["id"]
    )
    testimony = derivation["source_testimony"]
    assert testimony is not None
    assert testimony["source_artifact"] == "source_osm"
    assert testimony["way_id"] == _WAY
    assert testimony["node_id"] == _NODE
    assert testimony["connected_way_ids"] == [9008]
    assert testimony["coordinates"] == {"lat": 36.3510, "lon": 127.3810}
    assert derivation["provenance"]["source_field"] == "way/nd"
    assert derivation["provenance"]["link_id"] == "7"
    assert derivation["provenance"]["node_source"] == "draft_link_departure_end"

    # The route's real rendering: the refusal STANDS (tier untouched), and its
    # rationale states the testimony verbatim; the testimony dict is surfaced.
    entry = _orphan_entry(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["status"] == agent_fixes.NOT_AUTOMATABLE
    assert disposition["verb"] is None
    assert disposition["params"] is None
    assert disposition["requires"] == []
    assert disposition["reason_code"] == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    assert "ways 9008 share node 5002" in disposition["rationale"]["en"]
    assert "the gap is the pipeline's, not the world's" in disposition["rationale"]["en"]
    assert disposition["source_testimony"]["connected_way_ids"] == [9008]
    assert disposition["source_testimony"]["node_id"] == _NODE


# ---------------------------------------------------------------------------
# §2 The source shares the gap: the world ends where the source says it does
# ---------------------------------------------------------------------------


def test_world_gap_renders_the_measured_shared_gap(workspace_root: Path, client: TestClient):
    parent = _seed_v3_refused_parent(
        _census([_census_link(7, _WAY, from_node=5001, to_node=_NODE)]), _SOURCE_GAP
    )

    draft = genesis_jobs.job_draft_or_none(parent["id"])
    finding = {"code": _ORPHAN_CODE, "subject": "7 (link 7)", "context": {}}
    derivation = genesis_jobs.derive_v3_source_testimony(
        _ORPHAN_CODE, finding, draft=draft, job_id=parent["id"]
    )
    assert derivation["source_testimony"] is None
    provenance = derivation["provenance"]
    assert provenance["reason_code"] == "source_shares_the_gap"
    assert f"way {_WAY} ends at node {_NODE}" in provenance["detail"]

    entry = _orphan_entry(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["status"] == agent_fixes.NOT_AUTOMATABLE
    assert disposition["reason_code"] == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    assert "no drivable way departs node 5002" in disposition["rationale"]["en"]
    assert "the road ends where the source declares it ends" in disposition["rationale"]["en"]
    assert "source_testimony" not in disposition  # key-ABSENT: presence is the fact


# ---------------------------------------------------------------------------
# §3 Direction is honesty: a oneway INTO the node is not a connection, and a
# way CONTINUING past the node in the direction of travel is one
# ---------------------------------------------------------------------------


def test_oneway_into_the_node_stays_a_world_gap(workspace_root: Path):
    """The one-way dead end that dominates the real jobs (23/28): other
    carriageways touch the node, but every oneway declaration points INTO it
    — the source offers no departure, so the source shares the gap."""
    record = _seed_job(
        _orphan_network(),
        census=_census([_census_link(7, _WAY, from_node=5001, to_node=_NODE)]),
        name="oneway_in",
    )
    _attach_source_osm(
        record,
        _osm(_NODES, _osm_way(_WAY, [5001, _NODE]), _osm_way(9008, [5003, _NODE], oneway="yes")),
    )
    finding = {"code": _ORPHAN_CODE, "subject": "7 (link 7)", "context": {}}
    derivation = genesis_jobs.derive_v3_source_testimony(
        _ORPHAN_CODE, finding, draft=genesis_jobs.job_draft_or_none(record["id"]), job_id=record["id"]
    )
    assert derivation["source_testimony"] is None
    provenance = derivation["provenance"]
    assert provenance["reason_code"] == "source_shares_the_gap"
    assert "1 other drivable way(s) touch node 5002" in provenance["detail"]
    assert "points into the node" in provenance["detail"]


def test_own_way_continuing_past_the_node_testifies(workspace_root: Path):
    """The real testified case's shape (job b3ef6abb9a85: way 1232703541
    continues past node 4941371397): the census's own dangling entry names
    the node, and the source declares the SAME way carries on beyond it in
    the direction of travel — a connection the pipeline lost, not a U-turn."""
    dangling = [
        {
            "cause": "junction_with_no_exit_link",
            "dead_end_reason": None,
            "link": "link 7",
            "link_id": 7,
            "node_osm_id": _NODE,
        }
    ]
    record = _seed_job(
        _orphan_network(),
        census=_census([_census_link(7, _WAY, from_node=5001, to_node=999)], dangling=dangling),
        name="own_way_on",
    )
    _attach_source_osm(record, _osm(_NODES, _osm_way(_WAY, [5001, _NODE, 5003])))
    finding = {
        "code": _SILENT_CODE,
        "subject": "link 7",
        "context": {"cause": "junction_with_no_exit_link", "link_id": 7},
    }
    derivation = genesis_jobs.derive_v3_source_testimony(
        _SILENT_CODE, finding, draft=genesis_jobs.job_draft_or_none(record["id"]), job_id=record["id"]
    )
    testimony = derivation["source_testimony"]
    assert testimony is not None
    assert testimony["connected_way_ids"] == [_WAY]  # the way ITSELF continues
    # The census's own node, not the draft link's (deliberately wrong) end.
    assert testimony["node_id"] == _NODE
    assert derivation["provenance"]["node_source"] == "connectivity_census"

    # And the registry folds BOTH the census cause and the testimony into the
    # refusal — tier untouched.
    proposal = genesis_fixes.propose_genesis_fix(
        _SILENT_CODE,
        finding=finding,
        targets={
            "source_testimony": testimony,
            "source_testimony_provenance": derivation["provenance"],
        },
    )
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    assert "junction_with_no_exit_link" in proposal.rationale_en  # cause, verbatim
    assert f"ways {_WAY} share node {_NODE}" in proposal.rationale_en
    assert proposal.evidence["source_testimony"] == testimony
    assert proposal.evidence["source_testimony_provenance"]["source_field"] == "way/nd"


# ---------------------------------------------------------------------------
# §4 Honest nothings: missing source, unmappable finding — named, no crash
# ---------------------------------------------------------------------------


def test_missing_source_artifact_names_itself_and_the_listing_survives(
    workspace_root: Path, client: TestClient
):
    """``_seed_job``'s default record carries no ``stored_as`` for its source
    — exactly a fix job's shape (``source_osm: None``). The deriver answers
    ``source_artifact_missing`` and the listing still renders the refusal
    with the measured sentence."""
    parent = _seed_v3_refused_parent(
        _census([_census_link(7, _WAY, from_node=5001, to_node=_NODE)]), None
    )
    finding = {"code": _ORPHAN_CODE, "subject": "7 (link 7)", "context": {}}
    derivation = genesis_jobs.derive_v3_source_testimony(
        _ORPHAN_CODE, finding, draft=genesis_jobs.job_draft_or_none(parent["id"]), job_id=parent["id"]
    )
    assert derivation["source_testimony"] is None
    assert derivation["provenance"]["reason_code"] == "source_artifact_missing"

    entry = _orphan_entry(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["status"] == agent_fixes.NOT_AUTOMATABLE
    assert "source .osm is absent or unreadable" in disposition["rationale"]["en"]
    assert "source_testimony" not in disposition


def test_unmappable_findings_name_their_reason():
    """No job, no workspace — the deriver's mapping ladder alone. Each rung
    that cannot hold answers by name, never by crash."""
    draft = DraftNetwork.model_validate(
        _census([_census_link(7, _WAY, from_node=5001, to_node=_NODE)])
    )

    # No addressable link id at all (a non-numeric subject, no context).
    nameless = {"code": _SILENT_CODE, "subject": "이름뿐인 링크", "context": {}}
    derivation = genesis_jobs.derive_v3_source_testimony(
        _SILENT_CODE, nameless, draft=draft, job_id=None
    )
    assert derivation["source_testimony"] is None
    assert derivation["provenance"]["reason_code"] == "no_draft_record"

    # A link id the census has no record for.
    unknown = {"code": _SILENT_CODE, "subject": "link 42", "context": {"link_id": 42}}
    derivation = genesis_jobs.derive_v3_source_testimony(
        _SILENT_CODE, unknown, draft=draft, job_id=None
    )
    assert derivation["provenance"]["reason_code"] == "no_draft_record"

    # No census at all.
    derivation = genesis_jobs.derive_v3_source_testimony(
        _SILENT_CODE, unknown, draft=None, job_id=None
    )
    assert derivation["provenance"]["reason_code"] == "no_draft_census"


def test_way_or_node_absent_from_the_source_names_itself(workspace_root: Path):
    """The census can name a way or node the source (parsed drivably, with
    the parser's own rules) does not declare — each is its own named reason,
    distinct from the measured world-gap."""
    record = _seed_job(
        _orphan_network(),
        census=_census(
            [
                _census_link(7, 9999, from_node=5001, to_node=_NODE),  # way not in source
                _census_link(8, _WAY, from_node=5001, to_node=6666),  # node not in source
            ]
        ),
        name="absent_ids",
    )
    _attach_source_osm(record, _SOURCE_GAP)
    draft = genesis_jobs.job_draft_or_none(record["id"])

    derivation = genesis_jobs.derive_v3_source_testimony(
        _ORPHAN_CODE,
        {"code": _ORPHAN_CODE, "subject": "7 (link 7)", "context": {}},
        draft=draft,
        job_id=record["id"],
    )
    assert derivation["provenance"]["reason_code"] == "way_not_in_source"
    assert "way 9999" in derivation["provenance"]["detail"]

    derivation = genesis_jobs.derive_v3_source_testimony(
        _ORPHAN_CODE,
        {"code": _ORPHAN_CODE, "subject": "8 (link 8)", "context": {}},
        draft=draft,
        job_id=record["id"],
    )
    assert derivation["provenance"]["reason_code"] == "node_not_in_source"
    assert "node 6666" in derivation["provenance"]["detail"]


# ---------------------------------------------------------------------------
# §5 A bare-registry caller (no service layer, no targets) keeps the exact
# pre-#36 answer — the builder degrades to _no_fix, cause quoting intact
# ---------------------------------------------------------------------------


def test_registry_alone_without_targets_answers_exactly_as_before():
    finding = {
        "code": _SILENT_CODE,
        "subject": "도안동로 [FWD] w45473814s1",
        "context": {"cause": "one_way_dead_end_unfixable", "link_id": 2},
    }
    proposal = genesis_fixes.propose_for_gate_finding(finding)
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    assert "one_way_dead_end_unfixable" in proposal.rationale_en
    assert "Measured" not in proposal.rationale_en  # no derivation record, no claim
    assert "source_testimony" not in proposal.evidence


# ---------------------------------------------------------------------------
# §6 Direction honesty at the parser's own semantics — reversed oneway,
# implied roundabout oneway, drivability classes, and duplicate way ids
# ---------------------------------------------------------------------------


def _derive_for_source(source_xml: str, *, name: str) -> dict[str, Any]:
    """Seed one un-gated job whose link 7 (way 9007, FWD, ending at node
    5002) questions ``source_xml``, and return the deriver's answer for it —
    the shared shape of every §6 probe."""
    record = _seed_job(
        _orphan_network(),
        census=_census([_census_link(7, _WAY, from_node=5001, to_node=_NODE)]),
        name=name,
    )
    _attach_source_osm(record, source_xml)
    finding = {"code": _ORPHAN_CODE, "subject": "7 (link 7)", "context": {}}
    return genesis_jobs.derive_v3_source_testimony(
        _ORPHAN_CODE,
        finding,
        draft=genesis_jobs.job_draft_or_none(record["id"]),
        job_id=record["id"],
    )


def test_reversed_oneway_direction_decides_testimony(workspace_root: Path):
    """``oneway=-1`` means travel AGAINST node order. Digitised ``[5003,
    5002]`` it DEPARTS node 5002 (toward 5003) — testimony; digitised
    ``[5002, 5003]`` it only ARRIVES there — the source shares the gap."""
    departing = _derive_for_source(
        _osm(_NODES, _osm_way(_WAY, [5001, _NODE]), _osm_way(9008, [5003, _NODE], oneway="-1")),
        name="rev_departs",
    )
    assert departing["source_testimony"] is not None
    assert departing["source_testimony"]["connected_way_ids"] == [9008]

    arriving = _derive_for_source(
        _osm(_NODES, _osm_way(_WAY, [5001, _NODE]), _osm_way(9008, [_NODE, 5003], oneway="-1")),
        name="rev_arrives",
    )
    assert arriving["source_testimony"] is None
    assert arriving["provenance"]["reason_code"] == "source_shares_the_gap"
    assert "1 other drivable way(s) touch node 5002" in arriving["provenance"]["detail"]


def test_roundabout_implied_oneway_is_inherited_from_the_parser(workspace_root: Path):
    """``junction=roundabout`` with no ``oneway`` tag is one-way FORWARD by
    OSM convention — ``parse_osm_file``'s own reading, inherited here rather
    than re-derived. Digitised ``[5002, 5003]`` the ring departs the node —
    testimony; digitised ``[5003, 5002]`` it only feeds INTO it — gap."""
    departing = _derive_for_source(
        _osm(
            _NODES,
            _osm_way(_WAY, [5001, _NODE]),
            _osm_way(9008, [_NODE, 5003], junction="roundabout"),
        ),
        name="ring_departs",
    )
    assert departing["source_testimony"] is not None
    assert departing["source_testimony"]["connected_way_ids"] == [9008]

    arriving = _derive_for_source(
        _osm(
            _NODES,
            _osm_way(_WAY, [5001, _NODE]),
            _osm_way(9008, [5003, _NODE], junction="roundabout"),
        ),
        name="ring_arrives",
    )
    assert arriving["source_testimony"] is None
    assert arriving["provenance"]["reason_code"] == "source_shares_the_gap"


def test_footway_continuation_never_testifies_but_service_does(workspace_root: Path):
    """Drivability is the parser's call, inherited: a ``highway=footway``
    continuation is NOT a connection a motor vehicle lost (false-positive-
    safe — it never enters the drivable index, only the gap detail), while a
    ``highway=service`` way at the same node IS."""
    footway = _derive_for_source(
        _osm(
            _NODES,
            _osm_way(_WAY, [5001, _NODE]),
            _osm_way(9008, [_NODE, 5003], highway="footway"),
        ),
        name="footway_on",
    )
    assert footway["source_testimony"] is None
    provenance = footway["provenance"]
    assert provenance["reason_code"] == "source_shares_the_gap"
    assert "no other drivable way touches that node" in provenance["detail"]
    assert "the corridor continues only as footway" in provenance["detail"]

    service = _derive_for_source(
        _osm(
            _NODES,
            _osm_way(_WAY, [5001, _NODE]),
            _osm_way(9008, [_NODE, 5003], highway="service"),
        ),
        name="service_on",
    )
    assert service["source_testimony"] is not None
    assert service["source_testimony"]["connected_way_ids"] == [9008]


def test_duplicate_way_id_last_declaration_wins_deterministically(workspace_root: Path):
    """A real Overpass export can declare one way id twice (seen in job
    b3ef6abb9a85's source). The index's promise is LAST-declared-in-file
    wins, deterministically — proven in both orientations with DIVERGING
    bodies: only when the LAST body touches-and-departs the node does the
    source testify."""
    away_nodes = _osm_node(6001, 36.3600, 127.3900) + _osm_node(6002, 36.3610, 127.3910)
    touching = _osm_way(9008, [_NODE, 5003])
    elsewhere = _osm_way(9008, [6001, 6002])

    last_touches = _derive_for_source(
        _osm(_NODES, away_nodes, _osm_way(_WAY, [5001, _NODE]), elsewhere, touching),
        name="dup_last_touches",
    )
    assert last_touches["source_testimony"] is not None
    assert last_touches["source_testimony"]["connected_way_ids"] == [9008]

    last_elsewhere = _derive_for_source(
        _osm(_NODES, away_nodes, _osm_way(_WAY, [5001, _NODE]), touching, elsewhere),
        name="dup_last_elsewhere",
    )
    assert last_elsewhere["source_testimony"] is None
    assert last_elsewhere["provenance"]["reason_code"] == "source_shares_the_gap"

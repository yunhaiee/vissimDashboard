"""Unit #39 — a V4 runway shortage LEARNS whether the boundary did it.

``v4_lane_drop_without_runway``'s refusal used to say, honestly, "there is no
evidence this shortfall traces to the requested model boundary" — because
nothing MEASURED that attribution: the only existing boundary check read the
DEPARTURE-side dangling census for the finding's own link, and a runway
shortage's boundary sits at the corridor's upstream ENTRY end, which that
census never records (the marker for a trimmed entry end lives in
``metadata['boundary']['way_trims']``'s synthesized nodes instead).
``genesis_jobs.derive_v4_boundary_attribution`` measures it: the judge's own
upstream runway walk re-traced over the draft census (cross-checked against
the verdict's own ``available_m`` — a disagreement is a named reason, never a
silently trusted side), the corridor's upstream terminus classified against
the census's own boundary markers, and the SOURCE asked — through unit #36's
own cached index — how much road it declares beyond. The registry's V4
builder folds the measured answer into the refusal's rationale; the fixes
listing surfaces the attribution dict. The tier NEVER changes with the
answer — the boundary-extension treatment is unit #40's, not this one's.

Measured on the motivating real finding (workspace bfc69251bd04, job
5e550c739aed, link 90 "둔산로 [FWD] w1416532929s0", available 52.15 m /
required 200.0 m): boundary_attributed via the way-trim marker at synthesized
node 13690449814 — the source declares 127.06 m of way 1416532929 beyond the
boundary and is then itself cut at the same lasso (node 13017285953 outside
the requested polygon), i.e. ``continuation_beyond_extract``.

Fixture shape follows ``tests/test_genesis_fix_v3_source.py`` exactly: a
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

from tests.test_genesis_gate import link, network, straight_pair
from tests.test_genesis_jobs import _seed_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as ``test_genesis_fix_v3_source.py``'s fixture of the same
    name — duplicated rather than imported across test modules (the repo's
    existing convention only cross-imports plain helper FUNCTIONS)."""
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

_CODE = "v4_lane_drop_without_runway"
_SUBJECT = "1 (link 1)"

#: The corridor: draft link 1 travels way 9007 FWD; its upstream entry is the
#: node under test. ``straight_pair(a_lanes=2, a_len=50.0)`` makes the REAL
#: gate refuse exactly this finding (required 200.0, available 50.0 — the
#: same fixture ``test_genesis_gate.test_v4_fails_when_a_dropped_lane_has_no_runway``
#: pins), so the deficit every test below attributes is 150.0 m.
_WAY = 9007
_BOUNDARY_NODE = 999001  # synthesized by the (hand-written) trim record
_DEFICIT = 150.0


def _runway_short_network() -> str:
    return network(links=straight_pair(a_lanes=2, a_len=50.0))


def _census_link(
    link_id: int,
    way_id: int,
    *,
    from_node: int,
    to_node: int,
    points: list[list[float]] | None = None,
    forward: bool = True,
) -> dict[str, Any]:
    """One VALID ``DraftLink`` record whose OSM provenance and length the
    test controls. The default polyline is 50.0 m — the judge's own
    ``available_m`` for the seeded network, so the re-trace cross-check
    agrees unless a test deliberately breaks it."""
    return {
        "id": link_id,
        "name": f"link {link_id}",
        "osm_way_id": way_id,
        "segment_index": 0,
        "forward": forward,
        "lanes": 2,
        "lane_width_m": 3.5,
        "points": points or [[0.0, 0.0], [50.0, 0.0]],
        "lane_provenance": {},
        "highway": "secondary",
        "from_node_osm_id": from_node,
        "to_node_osm_id": to_node,
        "side_offset_m": 0.0,
    }


def _census(
    link_entries: list[dict[str, Any]],
    *,
    way_trims: list[dict[str, Any]] | None = None,
    dangling: list[dict[str, Any]] | None = None,
    requested_bbox: dict[str, float] | None = None,
) -> dict[str, Any]:
    """A census that VALIDATES as a real ``DraftNetwork``, carrying the two
    boundary vocabularies the deriver classifies against — the trim records
    (entry-side synthesized nodes) and the dangling census (departure-side
    ``model_boundary_correct`` witnesses) — in their real recorded shapes."""
    boundary: dict[str, Any] = {"way_trims": way_trims or []}
    if requested_bbox is not None:
        boundary["requested_bbox_wgs84"] = requested_bbox
    return {
        "name": "seeded",
        "source_osm": "seed.osm",
        "metadata": {
            "connectivity": {"dangling_approaches": dangling or []},
            "boundary": boundary,
        },
        "links": link_entries,
    }


def _way_trim(
    *,
    way_id: int = _WAY,
    end: str = "start",
    synthesized_node_id: int = _BOUNDARY_NODE,
    metres_cut: float = 60.0,
    nodes_cut: int = 1,
) -> dict[str, Any]:
    """One ``metadata['boundary']['way_trims']`` record in the generator's
    own recorded shape (verified against job 5e550c739aed's real census)."""
    return {
        "way_id": way_id,
        "end": end,
        "synthesized_node_id": synthesized_node_id,
        "metres_cut": metres_cut,
        "nodes_cut": nodes_cut,
        "highway": "secondary",
        "reason": "tail_outside_requested_bbox_cut",
        "detail": f"{nodes_cut} node(s) of this way's {end} lay outside the requested polygon",
    }


def _osm_node(node_id: int, lat: float, lon: float) -> str:
    return f'  <node id="{node_id}" lat="{lat}" lon="{lon}"/>\n'


def _osm_way(
    way_id: int,
    node_ids: list[int],
    *,
    name: str | None = None,
    oneway: str | None = None,
    highway: str = "secondary",
) -> str:
    nds = "".join(f'    <nd ref="{n}"/>\n' for n in node_ids)
    tags = f'    <tag k="highway" v="{highway}"/>\n'
    if name is not None:
        tags += f'    <tag k="name" v="{name}"/>\n'
    if oneway is not None:
        tags += f'    <tag k="oneway" v="{oneway}"/>\n'
    return f'  <way id="{way_id}">\n{nds}{tags}  </way>\n'


def _osm(*elements: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="unit39-test">\n' + "".join(elements) + "</osm>\n"
    )


def _attach_source_osm(record: dict[str, Any], source_xml: str) -> None:
    """Store a source ``.osm`` on a seeded job the way a real generation does
    — the file under the job dir, the record's ``stored_as`` naming it."""
    path = genesis_jobs.job_dir(record["id"]) / "source.osm"
    path.write_text(source_xml, encoding="utf-8")
    record["source_osm"] = {
        "filename": "source.osm",
        "bytes": path.stat().st_size,
        "sha256": "",
        "stored_as": "source.osm",
    }
    genesis_jobs._upsert(record)


def _seed_v4_refused_parent(
    census: dict[str, Any], source_xml: str | None, *, name: str = "v4_parent"
) -> dict[str, Any]:
    record = _seed_job(_runway_short_network(), census=census, name=name)
    if source_xml is not None:
        _attach_source_osm(record, source_xml)
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _lane_drop_entry(client: TestClient, job_id: str) -> dict[str, Any]:
    listed = client.get(f"/api/genesis/jobs/{job_id}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    entries = [
        e
        for e in listed.json()["findings"]
        if e["code"] == _CODE and e["subject"] == _SUBJECT
    ]
    assert len(entries) == 1, entries
    return entries[0]


def _finding() -> dict[str, Any]:
    """The seeded network's own finding, exactly as the gate stores it."""
    return {
        "code": _CODE,
        "subject": _SUBJECT,
        "context": {"lanes": 2, "dropped_lanes": [2], "required_m": 200.0, "available_m": 50.0},
    }


def _derive(job_id: str) -> dict[str, Any]:
    return genesis_jobs.derive_v4_boundary_attribution(
        _CODE, _finding(), draft=genesis_jobs.job_draft_or_none(job_id), job_id=job_id
    )


# ---------------------------------------------------------------------------
# §1 Boundary-attributed: the corridor ends at a trim-synthesized node and
# the source declares the road continues beyond it — following the same way
# and then the same road NAME through a junction — with at least the deficit
# ---------------------------------------------------------------------------


def test_boundary_attributed_when_the_source_declares_enough_road_beyond(
    workspace_root: Path, client: TestClient
):
    """The trim record contributes its own measured 60.0 m of cut road, and
    the walk continues past the way's source end onto the SAME-NAMED way
    9006 (a junction does not end a road that continues through it) —
    together clearing the 150.0 m deficit."""
    census = _census(
        [_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=5001)],
        way_trims=[_way_trim(metres_cut=60.0)],
    )
    # 4999 -> 5000 is ~111 m of "Grand Ave"; way 9007's own start (5000) was
    # cut at the boundary, so the source still declares the full way.
    source = _osm(
        _osm_node(4999, 36.3600, 127.3800),
        _osm_node(5000, 36.3610, 127.3800),
        _osm_node(5001, 36.3615, 127.3800),
        _osm_way(_WAY, [5000, 5001], name="Grand Ave", oneway="yes"),
        _osm_way(9006, [4999, 5000], name="Grand Ave", oneway="yes"),
    )
    parent = _seed_v4_refused_parent(census, source)

    derivation = _derive(parent["id"])
    attribution = derivation["boundary_attribution"]
    assert attribution is not None
    assert attribution["status"] == "boundary_attributed"
    assert attribution["way_id"] == _WAY
    assert attribution["boundary_node"] == _BOUNDARY_NODE
    assert attribution["deficit_m"] == _DEFICIT
    assert attribution["boundary_marker"] == "way_trim"
    continuation = attribution["continuation_evidence"]
    assert continuation["meets_deficit"] is True
    assert continuation["source_length_available_m"] > _DEFICIT
    assert continuation["continuation_way_ids"] == [9006]
    assert "continuation_beyond_extract" not in continuation
    provenance = derivation["provenance"]
    assert provenance["traced_available_m"] == 50.0
    assert provenance["terminus_link_id"] == "1"
    assert provenance["source_field"] == "metadata.boundary.way_trims + way/nd"

    # The route's real rendering: tier untouched, boundary reason named, the
    # measured clause in the rationale, the attribution dict surfaced.
    entry = _lane_drop_entry(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["status"] == agent_fixes.NOT_AUTOMATABLE
    assert disposition["verb"] is None
    assert disposition["reason_code"] == genesis_fixes.RUNWAY_ENDS_AT_REQUESTED_BOUNDARY
    assert "placed ON the requested model boundary" in disposition["rationale"]["en"]
    assert (
        "the shortage belongs to the model boundary, not to the road"
        in disposition["rationale"]["en"]
    )
    assert "There is no evidence" not in disposition["rationale"]["en"]
    assert disposition["boundary_attribution"]["boundary_node"] == _BOUNDARY_NODE
    assert disposition["boundary_attribution"]["continuation_evidence"]["meets_deficit"] is True


# ---------------------------------------------------------------------------
# §2 Beyond-extract: the source itself is cut at the same lasso — it cannot
# testify past its own edge, and THAT is named, still boundary-attribution
# ---------------------------------------------------------------------------


def test_source_clipped_at_the_same_boundary_is_named_beyond_extract(
    workspace_root: Path, client: TestClient
):
    """The real link-90 shape: the trim declares 60.0 m of cut road, the way's
    source end (node 5000) lies OUTSIDE the requested region, and only a
    differently named cross-street arrives there — the extract cannot testify
    past its own edge, which is itself boundary-attribution evidence that the
    MODEL's edge (not the road) ends at the boundary."""
    census = _census(
        [_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=5001)],
        way_trims=[_way_trim(metres_cut=60.0)],
        # Node 5000 (lat 36.3610) is north of the requested region's edge.
        requested_bbox={
            "min_lat": 36.3480,
            "min_lon": 127.3700,
            "max_lat": 36.3605,
            "max_lon": 127.3900,
        },
    )
    source = _osm(
        _osm_node(4998, 36.3611, 127.3795),
        _osm_node(5000, 36.3610, 127.3800),
        _osm_node(5001, 36.3600, 127.3800),
        _osm_way(_WAY, [5000, 5001], name="Grand Ave", oneway="yes"),
        _osm_way(9005, [4998, 5000], name="Cross St", oneway="yes"),
    )
    parent = _seed_v4_refused_parent(census, source)

    derivation = _derive(parent["id"])
    attribution = derivation["boundary_attribution"]
    assert attribution is not None
    assert attribution["status"] == "boundary_attributed"
    continuation = attribution["continuation_evidence"]
    assert continuation["continuation_beyond_extract"] is True
    assert continuation["extract_edge_node"] == 5000
    assert continuation["meets_deficit"] is False
    assert continuation["source_length_available_m"] == 60.0
    # The differently named arrival is recorded, measured, and NOT walked.
    assert continuation["other_arriving_way_ids"] == [9005]

    entry = _lane_drop_entry(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["status"] == agent_fixes.NOT_AUTOMATABLE
    assert disposition["reason_code"] == genesis_fixes.RUNWAY_ENDS_AT_REQUESTED_BOUNDARY
    assert "cannot testify past its own edge" in disposition["rationale"]["en"]
    assert "the MODEL's edge, not the road" in disposition["rationale"]["en"]
    assert (
        disposition["boundary_attribution"]["continuation_evidence"][
            "continuation_beyond_extract"
        ]
        is True
    )


# ---------------------------------------------------------------------------
# §3 The dangling-approach witness: an untrimmed boundary end, marked by the
# departure-side census at the SAME node, with the way itself continuing
# upstream in the source
# ---------------------------------------------------------------------------


def test_dangling_model_boundary_marker_attributes_via_the_source_walk(
    workspace_root: Path,
):
    """No trim record: the corridor's entry node 888 is a REAL source node
    that the dangling census marks ``model_boundary_correct`` (the reverse
    carriageway's witness). The source's own way 9007 continues upstream
    through it (700 -> 888, ~167 m), clearing the deficit — the own-way
    continuation counts only in the direction of travel."""
    dangling = [
        {
            "cause": "model_boundary_correct",
            "dead_end_reason": "model_boundary",
            "link": "link 77",
            "link_id": 77,
            "node_osm_id": 888,
        }
    ]
    census = _census(
        [_census_link(1, _WAY, from_node=888, to_node=5001)], dangling=dangling
    )
    source = _osm(
        _osm_node(700, 36.3600, 127.3800),
        _osm_node(888, 36.3615, 127.3800),
        _osm_node(5001, 36.3620, 127.3800),
        _osm_way(_WAY, [700, 888, 5001], name="Grand Ave"),
    )
    record = _seed_job(_runway_short_network(), census=census, name="dangling_marker")
    _attach_source_osm(record, source)

    derivation = _derive(record["id"])
    attribution = derivation["boundary_attribution"]
    assert attribution is not None
    assert attribution["status"] == "boundary_attributed"
    assert attribution["boundary_marker"] == "dangling_approach"
    assert attribution["boundary_node"] == 888
    continuation = attribution["continuation_evidence"]
    assert continuation["meets_deficit"] is True
    assert continuation["continuation_way_ids"] == [_WAY]  # the way ITSELF continues
    assert continuation["measured_from"] == "source_osm"
    assert "way_trim" not in attribution


# ---------------------------------------------------------------------------
# §4 Interior: the corridor ends inside the model and the source confirms —
# the honest opposite, same tier
# ---------------------------------------------------------------------------


def test_interior_dead_end_the_source_confirms_renders_the_honest_opposite(
    workspace_root: Path, client: TestClient
):
    census = _census([_census_link(1, _WAY, from_node=5001, to_node=5002)])
    # Way 9007 BEGINS at the corridor's entry node 5001: nothing arrives
    # there, and no other drivable way touches it.
    source = _osm(
        _osm_node(5001, 36.3600, 127.3800),
        _osm_node(5002, 36.3605, 127.3800),
        _osm_way(_WAY, [5001, 5002], name="Grand Ave", oneway="yes"),
    )
    parent = _seed_v4_refused_parent(census, source)

    derivation = _derive(parent["id"])
    attribution = derivation["boundary_attribution"]
    assert attribution is not None
    assert attribution["status"] == "interior"
    assert attribution["terminus_node"] == 5001
    assert attribution["source_confirms"] is True
    assert "genuinely ends" in attribution["reason"]

    entry = _lane_drop_entry(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["status"] == agent_fixes.NOT_AUTOMATABLE
    assert disposition["reason_code"] == genesis_fixes.NO_TOOL_WITHOUT_FABRICATION
    assert "ends in the model's INTERIOR" in disposition["rationale"]["en"]
    assert "the deficit is the road's, not the model boundary's" in disposition["rationale"]["en"]
    # The measurement REPLACES the old "no evidence" sentence — an interior
    # verdict is evidence, and saying "no evidence" beside it would be false.
    assert "There is no evidence" not in disposition["rationale"]["en"]
    assert disposition["boundary_attribution"]["status"] == "interior"


def test_interior_with_source_declaring_upstream_ways_names_them(workspace_root: Path):
    """The corridor ends at an interior node the source says MORE about: a
    drivable way arrives there that the draft does not carry. Still interior
    (the shortage is not the boundary's), with the ways named."""
    census = _census([_census_link(1, _WAY, from_node=5001, to_node=5002)])
    source = _osm(
        _osm_node(4999, 36.3595, 127.3800),
        _osm_node(5001, 36.3600, 127.3800),
        _osm_node(5002, 36.3605, 127.3800),
        _osm_way(_WAY, [5001, 5002], name="Grand Ave", oneway="yes"),
        _osm_way(9004, [4999, 5001], name="Feeder Rd", oneway="yes"),
    )
    record = _seed_job(_runway_short_network(), census=census, name="interior_lost")
    _attach_source_osm(record, source)

    derivation = _derive(record["id"])
    attribution = derivation["boundary_attribution"]
    assert attribution is not None
    assert attribution["status"] == "interior"
    assert attribution["source_confirms"] is False
    assert attribution["source_upstream_way_ids"] == [9004]

    proposal = genesis_fixes.propose_genesis_fix(
        _CODE,
        finding=_finding(),
        targets={
            "boundary_attribution": attribution,
            "boundary_attribution_provenance": derivation["provenance"],
        },
    )
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.NO_TOOL_WITHOUT_FABRICATION
    assert "9004" in proposal.rationale_en
    assert "not the requested boundary's" in proposal.rationale_en


# ---------------------------------------------------------------------------
# §5 Trace-vs-judge disagreement: a named reason, never an invented
# attribution — and the other honest nothings
# ---------------------------------------------------------------------------


def test_trace_disagreeing_with_the_judge_names_itself_and_attributes_nothing():
    """The census claims 500 m of runway where the judge measured 50 m: the
    re-trace reports the disagreement by name instead of trusting either
    side, and the registry folds the named reason into the pre-#39 refusal."""
    draft = DraftNetwork.model_validate(
        _census(
            [
                _census_link(
                    1,
                    _WAY,
                    from_node=_BOUNDARY_NODE,
                    to_node=5001,
                    points=[[0.0, 0.0], [500.0, 0.0]],
                )
            ],
            way_trims=[_way_trim()],
        )
    )
    derivation = genesis_jobs.derive_v4_boundary_attribution(
        _CODE, _finding(), draft=draft, job_id=None
    )
    assert derivation["boundary_attribution"] is None
    provenance = derivation["provenance"]
    assert provenance["reason_code"] == "trace_disagrees_with_judge"
    assert "disagree" in provenance["detail"]

    proposal = genesis_fixes.propose_genesis_fix(
        _CODE,
        finding=_finding(),
        targets={"boundary_attribution_provenance": provenance},
    )
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.NO_TOOL_WITHOUT_FABRICATION
    # The pre-#39 honest sentence STAYS (nothing was measured to replace it),
    # and the derivation's named reason is appended, with both numbers.
    assert "There is no evidence" in proposal.rationale_en
    assert "the two measurements disagree" in proposal.rationale_en
    assert "50.0" in proposal.rationale_en
    assert genesis_fixes.RUNWAY_ENDS_AT_REQUESTED_BOUNDARY != proposal.reason_code


def test_unanswerable_rungs_name_their_reason():
    """Each rung that cannot hold answers by name, never by crash — and
    never by an invented attribution."""
    draft = DraftNetwork.model_validate(
        _census([_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=5001)])
    )

    # No usable judge measurement in the finding's own context.
    unmeasured = {"code": _CODE, "subject": _SUBJECT, "context": {"dropped_lanes": [2]}}
    derivation = genesis_jobs.derive_v4_boundary_attribution(
        _CODE, unmeasured, draft=draft, job_id=None
    )
    assert derivation["boundary_attribution"] is None
    assert derivation["provenance"]["reason_code"] == "judge_measurement_missing"

    # No census at all.
    derivation = genesis_jobs.derive_v4_boundary_attribution(
        _CODE, _finding(), draft=None, job_id=None
    )
    assert derivation["provenance"]["reason_code"] == "no_draft_census"

    # A finding whose subject addresses no census link.
    unknown = {
        "code": _CODE,
        "subject": "42 (link 42)",
        "context": {"required_m": 200.0, "available_m": 50.0},
    }
    derivation = genesis_jobs.derive_v4_boundary_attribution(
        _CODE, unknown, draft=draft, job_id=None
    )
    assert derivation["provenance"]["reason_code"] == "no_draft_record"

    # Boundary marked, but no source and no trim measurement to speak from.
    dangling = [
        {"cause": "model_boundary_correct", "node_osm_id": _BOUNDARY_NODE, "link_id": 9}
    ]
    marked = DraftNetwork.model_validate(
        _census(
            [_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=5001)],
            dangling=dangling,
        )
    )
    derivation = genesis_jobs.derive_v4_boundary_attribution(
        _CODE, _finding(), draft=marked, job_id=None
    )
    assert derivation["boundary_attribution"] is None
    assert derivation["provenance"]["reason_code"] == "source_artifact_missing"


# ---------------------------------------------------------------------------
# §6 A bare-registry caller (no service layer, no targets) keeps the exact
# pre-#39 answer — no measured claim without a measurement
# ---------------------------------------------------------------------------


def test_registry_alone_without_targets_answers_exactly_as_before():
    proposal = genesis_fixes.propose_genesis_fix(_CODE, finding=_finding(), targets={})
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.NO_TOOL_WITHOUT_FABRICATION
    assert "There is no evidence" in proposal.rationale_en
    assert "Measured" not in proposal.rationale_en  # no derivation record, no claim
    assert "boundary_attribution" not in proposal.evidence

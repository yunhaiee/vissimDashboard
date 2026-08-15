"""Unit #35 — the V3 connectivity refusal becomes a TREATMENT, in two tiers.

Unit #36 proved that a ``v3_silent_departure_point`` refusal covers two worlds:
the source ``.osm`` shares the gap (the road really ends there) or it declares
the very connection the pipeline lost. This unit turns the second world into a
real ``add_connector`` proposal, and gives the first world an ask-first
QUESTION instead of a dead end — a human who knows the site may attest that the
road continues, naming the target link and their basis under their own name.

**The doctrine under test is that CONNECTIVITY_IS_NOT_INVENTED survives.** The
CONNECTION always has a named source — ``services.genesis_jobs.derive_v3_source_testimony``'s
measured testimony, or a person who takes responsibility for it — and what this
system contributes is mechanical construction from two link ends the census
already declares (``derive_v3_connector_plan``). Every test family below returns
to one of three facts:

1. no named source, no proposal (§3, §5) — and the bare refusal is byte-for-byte
   the one unit #36 left, quoted cause and all;
2. the params are DERIVED, never approved in (§6) — the apply path re-derives
   them from real server-side evidence and refuses a payload that differs, the
   same guard unit #34 built for V7's coordinates;
3. the gate stays the judge (§7) — the sibling job re-gates through the
   UNMODIFIED ``services.genesis.gate``.

No live COM anywhere: ``genesis_jobs._build_fix_edit_session`` is monkeypatched
to a fake session exactly as ``tests/test_genesis_fix_jobs.py`` does, so the
REAL tool registry, the REAL ``GuardrailEngine`` sandwich and the REAL gate all
run against a genuinely mutated working copy.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces
from services.agent import fixes as agent_fixes
from services.agent import genesis_fixes

from tests.test_genesis_gate import (
    _DEMAND_SUPPORT as demand_support,
)
from tests.test_genesis_gate import _SUPPORT as gate_support
from tests.test_genesis_gate import _demand as demand_layer
from tests.test_genesis_gate import connector, link, network, straight_pair
from tests.test_genesis_jobs import _seed_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as ``test_genesis_fix_v3_source.py``'s fixture of the same
    name — duplicated rather than imported across test modules (this repo's
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
# The network + census under test
# ---------------------------------------------------------------------------
#
# straight_pair (links 1+2 through connector 10001) keeps the network drivable
# so the demand checks stay quiet, and the fault under test is a SECOND
# corridor: link 3 ends at (100, 200) with no connector leaving it, and link 4
# — the continuation the pipeline lost — starts 4 m further on at (104, 200).
# The census declares link 3's end dangling, which is exactly what
# gate._check_v3 reads to emit ``v3_silent_departure_point``.

_CODE = "v3_silent_departure_point"
_VERB = "add_connector"
#: The approach's own way, the node its census entry names, and the way the
#: source declares carries on past that node.
_WAY_IN = 9007
_NODE = 5002
_WAY_ON = 9008


def _v3_network(*, with_second_candidate: bool = False, with_twin: bool = False) -> str:
    links = list(straight_pair()) + [
        link(3, [(0.0, 200.0), (100.0, 200.0)], name="approach 3"),
        link(4, [(104.0, 200.0), (204.0, 200.0)], name="continuation 4"),
    ]
    if with_second_candidate:
        # A SECOND draft link carrying the same testified way and beginning at
        # the same node — further from the departure end, so the derivation's
        # tie-break has something real to choose between.
        links.append(link(5, [(110.0, 210.0), (210.0, 210.0)], name="continuation 5"))
    if with_twin:
        # An independent second silent departure point (unit #30's shape).
        links.append(link(6, [(0.0, 400.0), (100.0, 400.0)], name="approach 6"))
        links.append(link(7, [(104.0, 400.0), (204.0, 400.0)], name="continuation 7"))
    return network(
        links=links, extra=demand_layer(), support=gate_support + demand_support
    )


def _census_link(
    link_id: int,
    way_id: int,
    points: list[list[float]],
    *,
    from_node: int,
    to_node: int,
    lanes: int = 1,
    forward: bool = True,
) -> dict[str, Any]:
    """One VALID ``DraftLink`` record — the census this unit's derivation
    reads its two link ends out of."""
    return {
        "id": link_id,
        "name": f"link {link_id}",
        "osm_way_id": way_id,
        "segment_index": 0,
        "forward": forward,
        "lanes": lanes,
        "lane_width_m": 3.5,
        "points": points,
        "lane_provenance": {},
        "highway": "residential",
        "from_node_osm_id": from_node,
        "to_node_osm_id": to_node,
        "side_offset_m": 0.0,
    }


def _census_connector(connector_id: int, from_link: int, to_link: int) -> dict[str, Any]:
    """One VALID ``DraftConnector`` record. Present only so the census's id set
    matches the artifact's — ``gate._census_identity`` admits a census only
    when it names exactly this file's own links, connectors and nodes."""
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
    *, with_second_candidate: bool = False, with_twin: bool = False
) -> dict[str, Any]:
    links = [
        _census_link(1, 9001, [[0.0, 0.0], [100.0, 0.0]], from_node=1001, to_node=1002),
        _census_link(2, 9002, [[104.0, 0.0], [204.0, 0.0]], from_node=1002, to_node=1003),
        _census_link(3, _WAY_IN, [[0.0, 200.0], [100.0, 200.0]], from_node=5001, to_node=_NODE),
        _census_link(4, _WAY_ON, [[104.0, 200.0], [204.0, 200.0]], from_node=_NODE, to_node=5003),
    ]
    dangling = [_dangling(3, "approach 3")]
    if with_second_candidate:
        links.append(
            _census_link(5, _WAY_ON, [[110.0, 210.0], [210.0, 210.0]], from_node=_NODE, to_node=5004)
        )
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
# The source .osm — the same hand-written shape unit #36's tests use
# ---------------------------------------------------------------------------


def _osm_node(node_id: int, lat: float, lon: float) -> str:
    return f'  <node id="{node_id}" lat="{lat}" lon="{lon}"/>\n'


def _osm_way(way_id: int, node_ids: list[int], *, highway: str = "residential") -> str:
    nds = "".join(f'    <nd ref="{n}"/>\n' for n in node_ids)
    return f'  <way id="{way_id}">\n{nds}    <tag k="highway" v="{highway}"/>\n  </way>\n'


def _osm(*elements: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="unit35-test">\n' + "".join(elements) + "</osm>\n"
    )


_NODES = (
    _osm_node(5001, 36.3500, 127.3800)
    + _osm_node(_NODE, 36.3510, 127.3810)
    + _osm_node(5003, 36.3520, 127.3820)
    + _osm_node(5004, 36.3530, 127.3830)
)

#: The source TESTIFIES: way 9007 ends at node 5002 and way 9008 departs it.
_SOURCE_TESTIFIES = _osm(
    _NODES, _osm_way(_WAY_IN, [5001, _NODE]), _osm_way(_WAY_ON, [_NODE, 5003, 5004])
)
#: The source SHARES THE GAP: nothing else touches node 5002.
_SOURCE_GAP = _osm(_NODES, _osm_way(_WAY_IN, [5001, _NODE]))


def _attach_source_osm(record: dict[str, Any], source_xml: str) -> None:
    path = genesis_jobs.job_dir(record["id"]) / "source.osm"
    path.write_text(source_xml, encoding="utf-8")
    record["source_osm"] = {
        "filename": "source.osm",
        "bytes": path.stat().st_size,
        "sha256": "",
        "stored_as": "source.osm",
    }
    genesis_jobs._upsert(record)


def _seed_parent(
    source_xml: str | None,
    *,
    name: str = "v3_parent",
    with_second_candidate: bool = False,
    with_twin: bool = False,
    gated: bool = True,
) -> dict[str, Any]:
    record = _seed_job(
        _v3_network(with_second_candidate=with_second_candidate, with_twin=with_twin),
        census=_census(with_second_candidate=with_second_candidate, with_twin=with_twin),
        name=name,
    )
    if source_xml is not None:
        _attach_source_osm(record, source_xml)
    if not gated:
        return record
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _finding(link_id: int = 3, subject: str = "approach 3") -> dict[str, Any]:
    return {
        "code": _CODE,
        "subject": subject,
        "context": {"cause": "junction_with_no_exit_link", "link_id": link_id},
    }


def _disposition(client: TestClient, job_id: str, subject: str = "approach 3") -> dict[str, Any]:
    listed = client.get(f"/api/genesis/jobs/{job_id}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    entries = [
        e for e in listed.json()["findings"] if e["code"] == _CODE and e["subject"] == subject
    ]
    assert len(entries) == 1, entries
    return entries[0]["disposition"]


def _targets(job_id: str, finding: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    draft = genesis_jobs.job_draft_or_none(job_id)
    return genesis_jobs.derive_fix_targets(_CODE, finding, draft=draft, job_id=job_id, **kwargs)


#: What both tiers must derive for this fixture: link 3's departure end (its
#: full 100 m length) to link 4's start, one lane to one lane, through movement.
_EXPECTED_PARAMS = {
    "from_link": "3",
    "from_lanes": [1],
    "from_pos_m": 100.0,
    "to_link": "4",
    "to_lanes": [1],
    "to_pos_m": 0.0,
    "name": "V3 departure 3->4",
}


# ---------------------------------------------------------------------------
# The fake edit session — COM's only stand-in in this file
# ---------------------------------------------------------------------------


class FakeConnectorEditSession:
    """``VissimEditSession``'s surface for ``add_connector``, with an
    XML-mutating ``persist()``.

    Same fixture shape and the same reason as ``test_genesis_fix_jobs.py``'s
    own ``FakeFixEditSession``: the postwrite diff compares two REAL parses of
    the working copy, so the write has to actually land on disk. The read-back
    this returns is what ``tools_edit.add_connector`` turns into the
    envelope's ``verification.readback_value`` — the recorded proof the
    connector the runner asked for is the connector the file now holds.
    """

    def __init__(self, working_inpx: Path, *, fail: bool = False, drift: bool = False):
        self.working_inpx = Path(working_inpx)
        self._fail = fail
        self._drift = drift
        self._pending: dict[str, Any] | None = None
        self.closed = False
        self.calls: list[dict[str, Any]] = []

    def add_connector(
        self,
        from_link_no: int,
        from_lane_indices: list[int],
        from_pos_m: float,
        to_link_no: int,
        to_lane_indices: list[int],
        to_pos_m: float,
        name: str = "",
    ) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("simulated COM failure: AddConnector refused")
        call = {
            "from_link": str(from_link_no),
            "from_lanes": list(from_lane_indices),
            "from_pos": float(from_pos_m),
            "to_link": str(to_link_no),
            "to_lanes": list(to_lane_indices),
            "to_pos": float(to_pos_m),
            "no": "20001",
            "name": name,
        }
        self.calls.append(dict(call))
        self._pending = call
        if self._drift:
            # A connector Vissim silently placed somewhere else than asked --
            # the read-back is the only thing that can catch it.
            call = {**call, "to_pos": float(to_pos_m) + 25.0}
        return call

    def persist(self) -> None:
        if self._pending is None:
            return
        pending = self._pending
        lanes = "".join(
            '<lane markingType="1">'
            f'<fromLanes><objectRef key="{pending["from_link"]} {a}"/></fromLanes>'
            f'<toLanes><objectRef key="{pending["to_link"]} {b}"/></toLanes>'
            "</lane>"
            for a, b in zip(pending["from_lanes"], pending["to_lanes"])
        )
        new_link = (
            f'<link displayType="1" linkBehavType="1" lnChgDist="200" '
            f'name="{pending["name"]}" no="{pending["no"]}">'
            f'<fromLinkEndPt link="{pending["from_link"]}" pos="{pending["from_pos"]}"/>'
            "<geometry><linkPolyPts>"
            '<linkPolyPoint x="100" y="200" zOffset="0"/>'
            '<linkPolyPoint x="104" y="200" zOffset="0"/>'
            "</linkPolyPts></geometry>"
            f"<lanes>{lanes}</lanes>"
            f'<toLinkEndPt link="{pending["to_link"]}" pos="{pending["to_pos"]}"/></link>'
        )
        text = self.working_inpx.read_text(encoding="utf-8")
        assert "</links>" in text, "fixture: the working copy has no <links> container"
        self.working_inpx.write_text(text.replace("</links>", new_link + "</links>", 1), encoding="utf-8")
        self._pending = None

    def close(self) -> None:
        self.closed = True


def _fake_session_factory(sessions: list[FakeConnectorEditSession], **kwargs: Any):
    def _build(working_inpx: Path) -> FakeConnectorEditSession:
        session = FakeConnectorEditSession(working_inpx, **kwargs)
        sessions.append(session)
        return session

    return _build


def _job_dirs() -> set[str]:
    root = genesis_jobs.jobs_root()
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir()}


# ===========================================================================
# §0 The registry itself — one code re-tiered, six untouched
# ===========================================================================


def test_only_the_departure_point_code_became_treatable():
    """``v3_silent_departure_point`` names ONE link END, which is the shape
    add_connector's from-side needs; the other six V3 codes name a whole
    component, a both-ends-dead link, or a path property, and stay exactly the
    refusals unit #36 left."""
    treatable = genesis_fixes.get_genesis_fix(genesis_jobs.V3_TREATABLE_FINDING_CODE)
    assert treatable is not None
    assert treatable.status == agent_fixes.AUTOMATABLE
    assert treatable.verb == _VERB
    assert treatable.builder is not None
    assert treatable.reason_code is None

    for code in genesis_jobs.V3_CONNECTIVITY_FINDING_CODES:
        if code == genesis_jobs.V3_TREATABLE_FINDING_CODE:
            continue
        mapping = genesis_fixes.get_genesis_fix(code)
        assert mapping is not None, code
        assert mapping.status == agent_fixes.NOT_AUTOMATABLE, code
        assert mapping.verb is None, code
        assert mapping.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED, code


def test_the_addressing_key_is_the_finding_s_own_link_never_the_target():
    """A fix must address the element the finding diagnosed: for a connector
    that is ``from_link`` (the link whose departure end is the fault), read out
    of the finding's own ``context['link_id']`` — its SUBJECT is the census's
    link NAME, which is not an id at all."""
    assert genesis_fixes.VERB_ADDRESSING_FIELD[_VERB] == "from_link"
    assert genesis_fixes.expected_fix_target(_CODE, _finding()) == "3"
    assert genesis_fixes.expected_fix_target(_CODE, {"code": _CODE, "subject": "approach 3"}) is None


def test_the_registry_alone_still_refuses_exactly_as_before():
    """No service layer, no targets: the pre-#35 answer, down to the quoted
    cause and the absence of any measured claim."""
    proposal = genesis_fixes.propose_for_gate_finding(_finding())
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    assert proposal.verb is None and proposal.params is None
    assert "junction_with_no_exit_link" in proposal.rationale_en
    assert "Connectivity is not invented" in proposal.rationale_en
    assert "Measured" not in proposal.rationale_en


# ===========================================================================
# §1 Tier one — the SOURCE testifies, and the refusal becomes a proposal
# ===========================================================================


def test_a_testified_departure_point_derives_the_exact_connector(workspace_root: Path):
    parent = _seed_parent(_SOURCE_TESTIFIES)
    finding = _finding()
    targets = _targets(parent["id"], finding)

    assert targets["connector_params"] == _EXPECTED_PARAMS
    derivation = targets["connector_derivation"]
    assert derivation["basis"] == genesis_jobs.CONNECTOR_BASIS_SOURCE_TESTIMONY
    assert derivation["link_id"] == "3"
    assert derivation["node_id"] == _NODE
    assert derivation["way_id"] == _WAY_IN
    assert derivation["connected_way_ids"] == [_WAY_ON]
    assert derivation["to_link"] == "4"
    assert derivation["to_link_way_id"] == _WAY_ON
    assert derivation["span_m"] == 4.0
    assert derivation["turn"] == "through"
    assert derivation["lane_pairs"] == [[1, 1]]
    assert derivation["source_field"] == "links[].points"
    # Nothing to attest: a derived plan means the question was already answered.
    assert "attestation_candidates" not in targets


def test_the_testified_proposal_carries_the_testimony_verbatim(workspace_root: Path):
    parent = _seed_parent(_SOURCE_TESTIFIES)
    finding = _finding()
    targets = _targets(parent["id"], finding)
    proposal = genesis_fixes.propose_genesis_fix(_CODE, finding=finding, targets=targets)

    assert proposal.outcome == agent_fixes.PROPOSAL
    assert proposal.verb == _VERB
    assert proposal.params == _EXPECTED_PARAMS
    assert proposal.confidence == genesis_fixes.CONFIDENCE_SOURCE_TESTIMONY
    assert proposal.assumptions == (genesis_fixes.ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED,)

    # The FULL testimony and the FULL derivation provenance ride in evidence --
    # unit #34's census_derivation discipline, applied to a connection.
    assert proposal.evidence["source_testimony"]["connected_way_ids"] == [_WAY_ON]
    assert proposal.evidence["source_testimony"]["node_id"] == _NODE
    assert proposal.evidence["source_testimony_provenance"]["source_field"] == "way/nd"
    assert proposal.evidence["connector_derivation"]["basis"] == "source_testimony"

    # The ids, verbatim, in all three languages -- a reviewer reads the claim,
    # not a paraphrase of it.
    for text in (proposal.rationale_ko, proposal.rationale_en, proposal.rationale_ja):
        assert str(_WAY_ON) in text
        assert str(_NODE) in text
        assert str(_WAY_IN) in text
    assert "The source testifies" in proposal.rationale_en
    assert "소스가 증언합니다" in proposal.rationale_ko
    # And it says what the machine contributed, rather than implying the source
    # declared the geometry too.
    assert "CONSTRUCTED mechanically" in proposal.rationale_en
    # The generator's OWN reason for declining this movement rides in the
    # proposal too, not only in the refusal: on the real Daejeon jobs a
    # testified proposal can land on a junction the census declined as
    # U-turn-only, and the approver has to read that in the same sentence.
    for text in (proposal.rationale_ko, proposal.rationale_en, proposal.rationale_ja):
        assert "junction_with_no_exit_link" in text


def test_several_candidate_links_resolve_to_the_nearest_and_record_them_all(
    workspace_root: Path,
):
    """Two draft links carry the testified way AND begin at the testified node
    (a way split into segments). The census's own "begins at that node" is the
    TEST; the measured distance between the two link ends is only the
    tie-break, and every candidate considered is recorded."""
    parent = _seed_parent(_SOURCE_TESTIFIES, with_second_candidate=True, name="two_candidates")
    targets = _targets(parent["id"], _finding())

    assert targets["connector_params"]["to_link"] == "4"
    considered = targets["connector_derivation"]["candidates_considered"]
    assert [entry["link_id"] for entry in considered] == ["4", "5"]
    assert considered[0]["distance_m"] == 4.0
    assert considered[1]["distance_m"] == round(math.hypot(10.0, 10.0), 3)


def test_a_testified_connection_with_no_link_at_the_node_refuses_by_name(
    workspace_root: Path,
):
    """The source declares the way carries on, but this network's census has
    no link carrying it that BEGINS at that node — there is no link end to
    connect to, so the honest answer is a refusal naming exactly that, not a
    connector to the nearest thing that happens to be close."""
    census = _census()
    # Link 4 stays in the network (the census must still name every id) but is
    # re-provenanced to a way the source never declares departing the node.
    census["links"][3]["osm_way_id"] = 9999
    record = _seed_job(_v3_network(), census=census, name="no_link_at_node")
    _attach_source_osm(record, _SOURCE_TESTIFIES)

    finding = _finding()
    targets = _targets(record["id"], finding)
    assert "connector_params" not in targets
    derivation = targets["connector_derivation"]
    assert derivation["reason_code"] == "no_draft_link_departs_the_node"
    assert f"node {_NODE}" in derivation["detail"]

    proposal = genesis_fixes.propose_genesis_fix(_CODE, finding=finding, targets=targets)
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    assert "no link carrying one of those ways that BEGINS at node 5002" in proposal.rationale_en
    assert "regeneration over a larger extract" in proposal.rationale_en
    # ...and because this refusal DOES invite an attestation (the model holds a
    # reachable link end), the attestation it then receives must actually be
    # heard -- a source that testifies to a link this model never emitted
    # cannot be allowed to swallow the reader's own claim.
    assert [c["link_id"] for c in targets["attestation_candidates"]] == ["4"]
    attested = _targets(
        record["id"],
        finding,
        attestation={
            "to_link": "4",
            "basis": "현장 확인",
            "attested_by": "yunha",
            "attested_at": "2026-08-15T00:00:00+00:00",
        },
    )
    assert attested["connector_params"] == _EXPECTED_PARAMS
    assert attested["connector_derivation"]["basis"] == "human_attestation"
    heard = genesis_fixes.propose_genesis_fix(_CODE, finding=finding, targets=attested)
    assert heard.outcome == agent_fixes.PROPOSAL
    assert heard.confidence == genesis_fixes.CONFIDENCE_HUMAN_ATTESTATION


def test_the_route_renders_the_testified_proposal(workspace_root: Path, client: TestClient):
    parent = _seed_parent(_SOURCE_TESTIFIES)
    disposition = _disposition(client, parent["id"])

    assert disposition["status"] == agent_fixes.AUTOMATABLE
    assert disposition["verb"] == _VERB
    assert disposition["params"] == _EXPECTED_PARAMS
    assert disposition["requires"] == []
    assert disposition["confidence"] == genesis_fixes.CONFIDENCE_SOURCE_TESTIMONY
    assert disposition["assumptions"] == [genesis_fixes.ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED]
    assert disposition["source_testimony"]["connected_way_ids"] == [_WAY_ON]
    # A proposal is not a question: nothing to attest here.
    assert "attestation" not in disposition


# ===========================================================================
# §2 The other V3 codes are untouched — the same job, the same source
# ===========================================================================


def test_the_orphan_findings_in_the_same_verdict_stay_pure_refusals(
    workspace_root: Path, client: TestClient
):
    """Links 3 and 4 are also orphans in this artifact, and the SAME source
    testimony is derivable for them. They must still render as unit #36 left
    them: no verb, no params, no button."""
    parent = _seed_parent(_SOURCE_TESTIFIES)
    listed = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    orphans = [e for e in listed.json()["findings"] if e["code"] == "v3_orphan_link"]
    assert orphans, "fixture: expected the two disconnected links to raise orphan findings"
    for entry in orphans:
        assert entry["disposition"]["status"] == agent_fixes.NOT_AUTOMATABLE
        assert entry["disposition"]["verb"] is None
        assert entry["disposition"]["params"] is None
        assert entry["disposition"]["reason_code"] == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED


# ===========================================================================
# §3 Tier two — the world gap keeps its refusal, and gains the QUESTION
# ===========================================================================


def test_a_world_gap_refuses_and_invites_an_attestation_with_measured_candidates(
    workspace_root: Path, client: TestClient
):
    parent = _seed_parent(_SOURCE_GAP, name="world_gap")
    finding = _finding()
    targets = _targets(parent["id"], finding)

    assert "connector_params" not in targets
    assert targets["connector_derivation"]["reason_code"] == "no_named_source"
    assert [c["link_id"] for c in targets["attestation_candidates"]] == ["4"]
    assert targets["attestation_candidates"][0]["distance_m"] == 4.0

    disposition = _disposition(client, parent["id"])
    assert disposition["status"] == agent_fixes.AUTOMATABLE  # the mapping's declared tier
    assert disposition["verb"] == _VERB
    assert disposition["params"] is None  # ...and NOTHING to apply: not actionable
    assert disposition["reason_code"] == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    # unit #36's measured world-gap sentence survives verbatim.
    assert "no drivable way departs node 5002" in disposition["rationale"]["en"]
    assert "the road ends where the source declares it ends" in disposition["rationale"]["en"]
    # ...and the ask-first question is now on it.
    assert "Does the real road continue here?" in disposition["rationale"]["en"]
    assert "실제 도로가 여기서 이어집니까?" in disposition["rationale"]["ko"]
    assert "No attestation, no proposal" in disposition["rationale"]["en"]
    assert disposition["attestation"]["from_link"] == "3"
    assert [c["link_id"] for c in disposition["attestation"]["candidates"]] == ["4"]
    assert disposition["attestation"]["candidates"][0]["distance_m"] == 4.0
    # Each candidate carries the connector that WOULD be constructed for it --
    # the payload an attesting approval submits, so a client never has to
    # invent one (and the apply path re-derives it anyway).
    assert disposition["attestation"]["candidates"][0]["params"] == _EXPECTED_PARAMS
    assert disposition["attestation"]["candidates"][0]["turn"] == "through"


def test_no_attestation_no_proposal_the_apply_is_refused_by_name(workspace_root: Path):
    """The bare world-gap refusal is not a button, and it is not defeatable by
    hand-crafting the params either: the re-derivation reaches no proposal, so
    the service layer refuses before a job directory exists."""
    parent = _seed_parent(_SOURCE_GAP, name="world_gap_apply")
    before = _job_dirs()

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject="approach 3",
            verb=_VERB,
            params=dict(_EXPECTED_PARAMS),
            approved_by="reviewer",
            approval_rationale="should refuse: nobody named this connection",
        )
    assert excinfo.value.code == "genesis_fix_measurement_gate_not_satisfied"
    assert "CONNECTIVITY_IS_NOT_INVENTED" in excinfo.value.reasons[0]
    assert _job_dirs() == before


def test_a_candidate_beyond_the_generator_s_own_node_scale_is_never_offered(
    workspace_root: Path,
):
    """The picker is bounded by the generator's OWN node scale, inherited
    rather than invented: link 1's end is 223 m from link 3's, which is road,
    not a turn, so it is not a link end this system will offer to bridge."""
    parent = _seed_parent(_SOURCE_GAP, name="scale")
    candidates = genesis_jobs.derive_v3_attestation_candidates(
        _finding(), draft=genesis_jobs.job_draft_or_none(parent["id"])
    )
    assert [c["link_id"] for c in candidates] == ["4"]
    assert genesis_jobs.MAX_CONNECTOR_SPAN_M == 30.0


def test_an_attested_link_past_the_node_scale_refuses_by_name(workspace_root: Path):
    parent = _seed_parent(_SOURCE_GAP, name="far_attestation")
    targets = _targets(
        parent["id"],
        _finding(),
        attestation={
            "to_link": "2",
            "basis": "현장 확인: 도로가 이어집니다",
            "attested_by": "reviewer",
            "attested_at": "2026-08-15T00:00:00+00:00",
        },
    )
    assert "connector_params" not in targets
    derivation = targets["connector_derivation"]
    assert derivation["reason_code"] == "span_exceeds_node_scale"
    assert derivation["span_m"] > genesis_jobs.MAX_CONNECTOR_SPAN_M

    proposal = genesis_fixes.propose_genesis_fix(_CODE, finding=_finding(), targets=targets)
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert "past this generator's own node scale" in proposal.rationale_en


# ===========================================================================
# §4 The attestation, recorded — target, basis, attester, timestamp
# ===========================================================================

_ATTESTATION = {"to_link": "4", "basis": "현장 답사 2026-08-10: 도로가 실제로 이어집니다."}


def test_an_attestation_unlocks_the_proposal_and_is_quoted_with_its_attribution(
    workspace_root: Path,
):
    parent = _seed_parent(_SOURCE_GAP, name="attested")
    finding = _finding()
    stamped = {
        **_ATTESTATION,
        "attested_by": "yunha",
        "attested_at": "2026-08-15T01:02:03+00:00",
    }
    targets = _targets(parent["id"], finding, attestation=stamped)
    proposal = genesis_fixes.propose_genesis_fix(_CODE, finding=finding, targets=targets)

    assert proposal.outcome == agent_fixes.PROPOSAL
    assert proposal.verb == _VERB
    # The params are the SAME construction the testified tier derives -- only
    # WHO says the connection exists differs.
    assert proposal.params == _EXPECTED_PARAMS
    assert proposal.confidence == genesis_fixes.CONFIDENCE_HUMAN_ATTESTATION
    assert proposal.assumptions == (
        genesis_fixes.ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED,
        genesis_fixes.ASSUME_ATTESTED_NOT_MEASURED,
    )
    assert proposal.evidence["human_attestation"] == stamped

    # The attribution rides in the REASON itself, in all three languages, so it
    # travels with the sentence wherever the reason is rendered.
    assert "운영자 확인" in proposal.rationale_ko
    assert "OPERATOR-ATTESTED" in proposal.rationale_en
    assert "運営者確認" in proposal.rationale_ja
    for text in (proposal.rationale_ko, proposal.rationale_en, proposal.rationale_ja):
        assert "yunha" in text
        assert "2026-08-15T01:02:03+00:00" in text
        assert _ATTESTATION["basis"] in text  # the attester's own words, verbatim


def test_an_incomplete_attestation_refuses_and_is_never_completed_by_default(
    workspace_root: Path,
):
    """A target with no basis, and a basis with no target, are each refused by
    their own name — never filled in on the human's behalf."""
    parent = _seed_parent(_SOURCE_GAP, name="incomplete")
    finding = _finding()

    for attestation, reason in (
        ({"to_link": "4", "basis": "", "attested_by": "y", "attested_at": "t"}, "attestation_without_basis"),
        ({"to_link": "", "basis": "b", "attested_by": "y", "attested_at": "t"}, "attestation_without_target"),
        ({"to_link": "4", "basis": "b", "attested_by": "", "attested_at": ""}, "attestation_without_attribution"),
        ({"to_link": "404", "basis": "b", "attested_by": "y", "attested_at": "t"}, "attested_link_not_in_census"),
        ({"to_link": "3", "basis": "b", "attested_by": "y", "attested_at": "t"}, "attested_link_is_the_finding_link"),
    ):
        targets = _targets(parent["id"], finding, attestation=attestation)
        assert "connector_params" not in targets, attestation
        assert targets["connector_derivation"]["reason_code"] == reason, attestation
        proposal = genesis_fixes.propose_genesis_fix(_CODE, finding=finding, targets=targets)
        assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX, attestation
        if reason.startswith("attestation_"):
            assert proposal.reason_code == genesis_fixes.ATTESTATION_INCOMPLETE, attestation


def test_the_apply_door_stamps_who_attested_and_when_and_records_it_verbatim(
    workspace_root: Path,
):
    """The client has no field for the attester or the timestamp -- both are
    stamped here from the signed-in approver and the server's clock, and the
    whole attestation lands in the fix job's lineage."""
    parent = _seed_parent(_SOURCE_GAP, name="apply_attested")
    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_CODE,
        subject="approach 3",
        verb=_VERB,
        params=dict(_EXPECTED_PARAMS),
        approved_by="yunha",
        approval_rationale="attested on site",
        attestation=dict(_ATTESTATION),
    )
    attestation = record["derived_from"]["attestation"]
    assert attestation["to_link"] == "4"
    assert attestation["basis"] == _ATTESTATION["basis"]
    assert attestation["attested_by"] == "yunha"
    assert attestation["attested_at"]
    assert record["derived_from"]["connector_derivation"]["basis"] == "human_attestation"
    assert record["derived_from"]["params"] == _EXPECTED_PARAMS


def test_a_superseded_attestation_is_recorded_as_superseded_never_beside_the_params(
    workspace_root: Path,
):
    """Refuter finding 2b (2026-08-15): when the source's own testimony resolves
    the target, a human attestation submitted anyway must not land under
    ``derived_from.attestation`` beside applied params it did not produce --
    that lineage would read as a contradiction. It is not erased either (the
    human DID attest); it lands under ``attestation_superseded`` naming what
    prevailed."""
    parent = _seed_parent(_SOURCE_TESTIFIES, name="superseded_attestation")
    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_CODE,
        subject="approach 3",
        verb=_VERB,
        params=dict(_EXPECTED_PARAMS),
        approved_by="yunha",
        approval_rationale="attested, but the source already testifies",
        attestation={"to_link": "5", "basis": "the road visibly continues"},
    )
    lineage = record["derived_from"]
    assert lineage["connector_derivation"]["basis"] == "source_testimony"
    assert "attestation" not in lineage
    superseded = lineage["attestation_superseded"]
    assert superseded["to_link"] == "5"
    assert superseded["basis"] == "the road visibly continues"
    assert superseded["attested_by"] == "yunha"
    assert superseded["attested_at"]
    assert superseded["superseded_by"] == "source_testimony"


def test_an_attestation_missing_its_basis_is_refused_at_the_door(workspace_root: Path):
    parent = _seed_parent(_SOURCE_GAP, name="apply_no_basis")
    before = _job_dirs()

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject="approach 3",
            verb=_VERB,
            params=dict(_EXPECTED_PARAMS),
            approved_by="yunha",
            approval_rationale="no basis given",
            attestation={"to_link": "4", "basis": "   "},
        )
    assert excinfo.value.code == "genesis_fix_attestation_incomplete"
    assert "basis" in excinfo.value.reasons[0]
    assert _job_dirs() == before


def test_the_attestation_route_field_carries_no_attester_of_its_own(
    workspace_root: Path, client: TestClient
):
    """Structural, not merely enforced: the request model has no field a caller
    could put someone else's name in, and an empty basis never reaches the
    service layer at all."""
    parent = _seed_parent(_SOURCE_GAP, name="route_attest")
    rejected = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        headers=admin_headers(),
        json={
            "finding_code": _CODE,
            "subject": "approach 3",
            "verb": _VERB,
            "params": _EXPECTED_PARAMS,
            "attestation": {"to_link": "4", "basis": "b", "attested_by": "somebody else"},
        },
    )
    assert rejected.status_code == 422, rejected.text

    empty_basis = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        headers=admin_headers(),
        json={
            "finding_code": _CODE,
            "subject": "approach 3",
            "verb": _VERB,
            "params": _EXPECTED_PARAMS,
            "attestation": {"to_link": "4", "basis": ""},
        },
    )
    assert empty_basis.status_code == 422, empty_basis.text


def test_the_route_stamps_the_session_user_as_the_attester(
    workspace_root: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The happy path over HTTP: an attested world-gap fix returns 202, and the
    attester on the record is the SIGNED-IN session's own user — never anything
    the request body could have named.

    The runner is stubbed out entirely (§7 is where it runs for real): this
    route answers 202 the moment the record exists and hands the work to a
    daemon thread, and a thread outliving the tmp workspace this fixture
    tears down would be a race in the TEST, not a behaviour worth asserting.
    """
    monkeypatch.setattr(genesis_jobs, "run_genesis_fix_job", lambda *args, **kwargs: None)
    parent = _seed_parent(_SOURCE_GAP, name="route_happy")

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        headers=admin_headers(),
        json={
            "finding_code": _CODE,
            "subject": "approach 3",
            "verb": _VERB,
            "params": _EXPECTED_PARAMS,
            "approval_rationale": "attested on site",
            "attestation": dict(_ATTESTATION),
        },
    )
    assert response.status_code == 202, response.text
    lineage = response.json()["derived_from"]
    assert lineage["attestation"]["attested_by"] == "suite-admin"
    assert lineage["attestation"]["basis"] == _ATTESTATION["basis"]
    assert lineage["attestation"]["to_link"] == "4"
    assert lineage["approved_by"] == "suite-admin"


# ===========================================================================
# §5 Apply-time RE-DERIVATION — the B5-2 preflight discipline
# ===========================================================================


def test_a_source_swapped_between_listing_and_apply_refuses_by_name(workspace_root: Path):
    """The listing proposed a connector on the source's own testimony. If the
    job's source file is swapped for one that shares the gap before the
    approval lands, the apply re-derives against the NEW bytes and refuses --
    an approval is only ever as good as the evidence at the moment it is
    applied."""
    parent = _seed_parent(_SOURCE_TESTIFIES, name="swapped")
    targets = _targets(parent["id"], _finding())
    assert targets["connector_params"] == _EXPECTED_PARAMS  # the listing's answer

    (genesis_jobs.job_dir(parent["id"]) / "source.osm").write_text(_SOURCE_GAP, encoding="utf-8")
    before = _job_dirs()

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject="approach 3",
            verb=_VERB,
            params=dict(_EXPECTED_PARAMS),
            approved_by="reviewer",
            approval_rationale="approved while the source still testified",
        )
    assert excinfo.value.code == "genesis_fix_measurement_gate_not_satisfied"
    assert _job_dirs() == before


def test_a_source_swapped_to_a_different_connection_refuses_the_stale_payload(
    workspace_root: Path,
):
    """The subtler swap: the new source still testifies, but to a DIFFERENT
    link. The proposal check passes (a connector is still proposable) and the
    payload is what catches it -- unit #34's guard, for connector params."""
    parent = _seed_parent(_SOURCE_TESTIFIES, with_second_candidate=True, name="swapped_target")
    # Re-provenance link 4 so the only testified continuation is link 5.
    census = _census(with_second_candidate=True)
    census["links"][3]["osm_way_id"] = 9999
    (genesis_jobs.job_dir(parent["id"]) / "swapped_target.genesis.json").write_text(
        json.dumps(census), encoding="utf-8"
    )
    before = _job_dirs()

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject="approach 3",
            verb=_VERB,
            params=dict(_EXPECTED_PARAMS),  # still names link 4
            approved_by="reviewer",
            approval_rationale="stale target",
        )
    assert excinfo.value.code == "genesis_fix_connector_not_derived"
    assert "'to_link': '5'" in excinfo.value.reasons[0]
    assert _job_dirs() == before


# ===========================================================================
# §6 The B5-2 exploit patterns, for add_connector's own params
# ===========================================================================


def test_params_addressing_a_different_link_than_the_finding_are_refused(
    workspace_root: Path,
):
    """Exploit #2's shape: a well-formed add_connector payload whose from_link
    is NOT the link this finding diagnosed. Refused by name -- the addressing
    check fires before any evidence re-derivation."""
    parent = _seed_parent(_SOURCE_TESTIFIES, with_twin=True, name="mismatch")
    before = _job_dirs()

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject="approach 3",
            verb=_VERB,
            params={**_EXPECTED_PARAMS, "from_link": "6", "name": "V3 departure 6->7"},
            approved_by="reviewer",
            approval_rationale="should refuse: params address approach 6, the finding is approach 3",
        )
    assert excinfo.value.code == "genesis_fix_params_target_mismatch"
    assert _job_dirs() == before


def test_an_approver_cannot_swap_the_derived_connector_for_their_own(workspace_root: Path):
    """The connection may come from a source or a human; the PARAMS never come
    from the approver. Every field is checked against the re-derivation, so a
    hand-widened lane mapping or a moved position is refused by name."""
    parent = _seed_parent(_SOURCE_TESTIFIES, name="hand_edited")
    before = _job_dirs()

    for swapped in (
        {**_EXPECTED_PARAMS, "from_pos_m": 40.0},
        {**_EXPECTED_PARAMS, "from_lanes": [1, 2], "to_lanes": [1, 2]},
        {**_EXPECTED_PARAMS, "to_pos_m": 55.0},
        {**_EXPECTED_PARAMS, "name": "hand written"},
    ):
        with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
            genesis_jobs.create_genesis_fix_job(
                parent["id"],
                finding_code=_CODE,
                subject="approach 3",
                verb=_VERB,
                params=dict(swapped),
                approved_by="reviewer",
                approval_rationale="hand-edited payload",
            )
        assert excinfo.value.code == "genesis_fix_connector_not_derived", swapped
    assert _job_dirs() == before


def test_the_second_departure_point_is_addressable_by_its_own_subject(workspace_root: Path):
    """Unit #30's regression, for this code: two silent departure points in one
    verdict, each its own apply target. Omitting the subject refuses by name
    listing both, and naming the second resolves the SECOND finding's own
    link."""
    parent = _seed_parent(_SOURCE_TESTIFIES, with_twin=True, name="twins")
    stored = json.loads(
        (genesis_jobs.job_dir(parent["id"]) / genesis_jobs.GATE_JSON_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    subjects = [
        finding["subject"]
        for _check, _origin, finding in genesis_jobs.iter_stored_gate_findings(stored)
        if finding["code"] == _CODE
    ]
    assert subjects == ["approach 3", "approach 6"], subjects

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            verb=_VERB,
            params=dict(_EXPECTED_PARAMS),
            approved_by="reviewer",
            approval_rationale="no subject on a duplicated code",
        )
    assert excinfo.value.code == "genesis_fix_finding_ambiguous_without_subject"
    assert "'approach 3'" in excinfo.value.reasons[0]
    assert "'approach 6'" in excinfo.value.reasons[0]

    # The second finding, addressed by its own subject: its OWN link 6 is the
    # from-link, and the payload for link 3 would be refused under it.
    second = {"code": _CODE, "subject": "approach 6", "context": {"link_id": 6}}
    assert genesis_fixes.expected_fix_target(_CODE, second) == "6"


# ===========================================================================
# §7 The runner — the seam, the read-back, and the unmodified gate
# ===========================================================================


def test_the_fix_job_applies_the_connector_through_the_existing_seam(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """add_connector needs no new wiring: the runner's dispatch is generic, the
    guardrail sandwich already knows this verb creates links/lanes/conflict
    areas, and the tool's own read-back is what the postwrite records. The
    sibling job then re-gates through the UNMODIFIED gate."""
    parent = _seed_parent(_SOURCE_TESTIFIES, name="runner")
    parent_inpx = genesis_jobs.artifact_path(parent["id"], "inpx")
    parent_bytes = parent_inpx.read_bytes()

    sessions: list[FakeConnectorEditSession] = []
    monkeypatch.setattr(genesis_jobs, "_build_fix_edit_session", _fake_session_factory(sessions))
    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_CODE,
        subject="approach 3",
        verb=_VERB,
        params=dict(_EXPECTED_PARAMS),
        approved_by="reviewer",
        approval_rationale="the source declares this connection",
    )
    result = genesis_jobs.run_genesis_fix_job(record["id"], record["workspace_id"])

    # The verb reached COM with exactly the derived payload, and the session
    # was closed.
    assert len(sessions) == 1
    assert sessions[0].calls == [
        {
            "from_link": "3",
            "from_lanes": [1],
            "from_pos": 100.0,
            "to_link": "4",
            "to_lanes": [1],
            "to_pos": 0.0,
            "no": "20001",
            "name": "V3 departure 3->4",
        }
    ]
    assert sessions[0].closed

    # A real connector is in the sibling's own artifact, and the gate judged it.
    assert result["status"] in genesis_jobs.TERMINAL_STATUSES
    assert result["gate"] is not None
    sibling_inpx = genesis_jobs.artifact_path(result["id"], "inpx")
    assert 'no="20001"' in sibling_inpx.read_text(encoding="utf-8")
    assert result["derived_from"]["connector_derivation"]["basis"] == "source_testimony"
    assert "attestation" not in result["derived_from"]

    # And the diagnosis this fix answered is intact, byte for byte.
    assert parent_inpx.read_bytes() == parent_bytes


def test_a_connector_the_readback_does_not_confirm_fails_the_job(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The read-back discipline, exercised: a COM layer that reports a
    different to-position than the one asked for must not produce a published
    fix. (``add_connector``'s own verification claim is what carries this --
    the runner records it, and a dispatch that raises fails the job by name.)"""
    parent = _seed_parent(_SOURCE_TESTIFIES, name="failing_runner")
    sessions: list[FakeConnectorEditSession] = []
    monkeypatch.setattr(
        genesis_jobs, "_build_fix_edit_session", _fake_session_factory(sessions, fail=True)
    )
    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_CODE,
        subject="approach 3",
        verb=_VERB,
        params=dict(_EXPECTED_PARAMS),
        approved_by="reviewer",
        approval_rationale="COM will refuse this",
    )
    result = genesis_jobs.run_genesis_fix_job(record["id"], record["workspace_id"])

    assert result["status"] == genesis_jobs.STATUS_ERROR
    assert result["refusal"]["code"].startswith("fix_apply_failed:")
    assert result["gate"] is None
    assert result["publication"] is None


def test_the_readback_records_what_the_file_actually_got(tmp_path: Path):
    """The tool's verification envelope pairs the WRITTEN claim with the
    READ-BACK one; a drifted read-back is visible as a difference rather than
    being quietly normalised away."""
    from services.agent import toolkit
    from services.agent.edit_session import clear_active_edit_session, set_active_edit_session

    working = tmp_path / "readback.inpx"
    working.write_text(_v3_network(), encoding="utf-8")
    session = FakeConnectorEditSession(working, drift=True)
    set_active_edit_session(session)
    try:
        envelope = toolkit.dispatch(_VERB, dict(_EXPECTED_PARAMS))
    finally:
        clear_active_edit_session()

    assert envelope["status"] == "ok", envelope
    verification = envelope["verification"]
    assert verification["written_value"]["to_pos"] == 0.0
    assert verification["readback_value"]["to_pos"] == 25.0
    assert verification["written_value"] != verification["readback_value"]

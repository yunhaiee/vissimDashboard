"""Unit #54: the human-named demand-target store, and the ways it refuses.

A named mapping is the product's own admission that ``fixes.
ASSUME_INPUT_DRIVES_NODE_TOTAL`` cannot be proven from the network — so the
properties worth executing are the ones that keep the store from LAUNDERING that
assumption into something that reads like a derivation:

* **The server stamps who and when.** The store takes the namer as an argument
  and the clock from itself; the ROUTE takes the namer from the session and
  forbids the field on the body (pinned in ``tests/test_calibration_endpoint.py``).
  Between them there is no path from a request body to a name on a record.
* **The triple must be REAL.** A person may choose BETWEEN the inputs that
  really feed a node's counted approaches; they may not invent one. The
  candidate list is passed in from the derivation, and a triple that is not on
  it is refused by name.
* **A reason is mandatory.** An empty one is a tick.
* **The claim is bound to the network's OWN BYTES**, not to
  ``datasets.dataset_revision`` — #53's measured trap, restated: uploading a
  count workbook moves that string and must not expire a claim about topology.
* **A revocation is an event, not a delete.**

No COM, no Vissim, no network call: every test here runs against a few bytes in
a tmp directory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services import datasets, demand_target_map, workspaces


# ===========================================================================
# Fixtures — one tmp workspace, one tiny "network", one candidate list
# ===========================================================================


#: What ``reality_check_targets`` emits for a node with two input-bearing
#: approach links. The shape matters: this is the object the store checks a
#: modeller's choice against, so a drift in its keys would be a silent
#: acceptance of anything.
CANDIDATES = [
    {"vehicle_input_no": "113", "link_no": "617", "current_volume": 569.4, "name": "Murdoch NB"},
    {"vehicle_input_no": "213", "link_no": "617", "current_volume": 120.0, "name": "Murdoch NB"},
    {"vehicle_input_no": "109", "link_no": "620", "current_volume": 400.0, "name": "South St"},
]


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The same redirection ``tests/test_calibration_attestation_store.py`` uses."""
    root = tmp_path / "workspaces"
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    # The repo ships a demo network at ``data/practice.inpx`` and
    # ``get_active_inpx_path`` falls back to it. Point that fallback at nothing
    # so "no network" is a state these tests can reach, and so the hashes below
    # are of bytes this file wrote.
    monkeypatch.setattr(datasets, "INPX_PATH", tmp_path / "no-such-demo.inpx")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield root
    finally:
        workspaces.reset_current_workspace_id(token)


def _write_network(content: bytes) -> str:
    path = datasets.active_inpx_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


@pytest.fixture()
def network(workspace_root: Path) -> str:
    return _write_network(b"<network>one</network>")


def _name(
    *,
    node_id: str = "8",
    vehicle_input_no: str = "113",
    link_no: str = "617",
    reason: str = "링크 617이 노드 8 유입의 71.9%, 인풋 113이 그 중 69.0%",
    by: str = "yunha",
    candidates=CANDIDATES,
):
    return demand_target_map.record_mapping(
        node_id=node_id,
        vehicle_input_no=vehicle_input_no,
        link_no=link_no,
        reason=reason,
        named_by=by,
        candidates=candidates,
    )


# ===========================================================================
# What the server stamps, and what a caller may say
# ===========================================================================


def test_the_store_stamps_the_clock_and_trims_the_namer(network: str):
    record = _name(by="  yunha  ")
    assert record["named_by"] == "yunha"
    # Not asserted against a fixed value — asserted to EXIST and to be the
    # server's ISO stamp, because the only wrong answer here is a client's.
    assert record["named_at"] and record["named_at"].startswith("20")
    assert (record["node_id"], record["vehicle_input_no"], record["link_no"]) == ("8", "113", "617")
    assert record["inpx_sha256"] == network
    assert record["stale"] is False
    assert record["revoked"] is False

    stored = json.loads(demand_target_map.registry_path().read_text(encoding="utf-8"))
    on_disk = stored["mappings"][record["id"]]
    assert on_disk["kind"] == demand_target_map.DEMAND_TARGET_DOC_KIND
    assert on_disk["workspace_id"] == workspaces.current_workspace_id()
    assert on_disk["history"] == [
        {
            "event": "named",
            "at": record["named_at"],
            "by": "yunha",
            "reason": "링크 617이 노드 8 유입의 71.9%, 인풋 113이 그 중 69.0%",
        }
    ]


def test_a_mapping_nobody_can_be_named_for_is_refused(network: str):
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(by="   ")
    assert caught.value.code == "demand_target_no_namer"
    assert caught.value.status == 401
    assert set(caught.value.messages) == {"ko", "en", "ja"}


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_an_empty_reason_is_refused_by_name(network: str, reason: str):
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(reason=reason)
    assert caught.value.code == "demand_target_reason_required"
    assert demand_target_map.list_mappings() == []


def test_an_over_long_reason_is_refused_rather_than_truncated(network: str):
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(reason="가" * (demand_target_map.MAX_REASON_CHARS + 1))
    assert caught.value.code == "demand_target_reason_too_long"


# ===========================================================================
# THE RULE: a mapping must be checkable, or it is not recorded
# ===========================================================================


def test_an_input_no_candidate_list_vouches_for_is_refused_by_name(network: str):
    """The whole reason this store may exist rather than being a free-text note.

    Input 999 is not in the shadow model, so no evidence anywhere could later
    confirm or contradict "999 drives node 8". Recording it would produce a
    demand target indistinguishable, downstream, from one the network itself
    vouched for.
    """
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(vehicle_input_no="999")
    assert caught.value.code == "demand_target_unverifiable_triple"
    assert caught.value.status == 422
    assert set(caught.value.messages) == {"ko", "en", "ja"}
    # The refusal SAYS what could have been chosen, so the screen can recover.
    assert {entry["vehicle_input_no"] for entry in caught.value.detail["candidates"]} == {
        "113",
        "213",
        "109",
    }
    assert demand_target_map.list_mappings() == []


def test_a_real_input_on_the_wrong_link_is_refused_too(network: str):
    """Input 113 exists and link 620 exists, but 113 does not feed 620.

    The TRIPLE is what is checked, not each half: a pair that is individually
    plausible and jointly false is exactly the mapping a per-field check would
    let through, and it is the one that would put a demand edit on the wrong
    approach.
    """
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(vehicle_input_no="113", link_no="620")
    assert caught.value.code == "demand_target_unverifiable_triple"


def test_a_link_written_differently_is_still_the_same_link(network: str):
    """``"617.0"`` must not read as a different link from ``"617"``.

    The same canonicalization ``fixes.entrance_measurement`` applies, applied
    here for a sharper reason: a mismatch would not merely lose a mapping, it
    would make a mapping the picker offered un-recordable, and the modeller
    would have no way to tell why.
    """
    record = _name(link_no="617.0")
    assert record["link_no"] == "617"


def test_no_candidate_list_at_all_is_a_named_refusal(network: str):
    """``None`` means the server could not compute candidates for this node —
    the node's rows are not OBJECT_IDs, or the network could not be read. That
    is a refusal, never a licence to record whatever was asked for."""
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(candidates=None)
    assert caught.value.code == "demand_target_no_candidates"
    assert caught.value.status == 409


def test_a_node_with_an_EMPTY_candidate_list_can_name_nothing(network: str):
    """Empty is not ``None`` and not permissive: a node no vehicle input feeds
    has nothing to choose from, and the refusal names the (empty) list."""
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(candidates=[])
    assert caught.value.code == "demand_target_unverifiable_triple"
    assert caught.value.detail["candidates"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"node_id": "  "},
        {"vehicle_input_no": ""},
        {"link_no": "   "},
    ],
)
def test_an_incomplete_triple_is_refused_before_anything_else(network: str, kwargs):
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(**kwargs)
    assert caught.value.code == "demand_target_incomplete_triple"


def test_an_absurdly_long_token_is_refused(network: str):
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name(node_id="8" * (demand_target_map.MAX_TOKEN_CHARS + 1))
    assert caught.value.code == "demand_target_token_too_long"


# ===========================================================================
# The staleness key — the .inpx's own bytes, and nothing else
# ===========================================================================


def test_the_mapping_goes_stale_when_the_network_changes(network: str):
    record = _name()
    assert demand_target_map.index_payload()["8"]["stale"] is False
    assert "8" in demand_target_map.fresh_index_payload()

    _write_network(b"<network>two: an approach was rebuilt</network>")
    assert demand_target_map.index_payload()["8"]["stale"] is True
    # ...and the DERIVATION's input drops it entirely.
    assert demand_target_map.fresh_index_payload() == {}

    # And BACK: the key is the content, not a timestamp.
    _write_network(b"<network>one</network>")
    assert demand_target_map.index_payload()["8"]["stale"] is False
    assert demand_target_map.index_payload()["8"]["id"] == record["id"]


def test_uploading_observed_counts_does_not_expire_a_mapping(network: str):
    """#53's measured trap, restated for this store: ``dataset_revision`` folds
    ``real_counts.xlsx`` into the same string as the network, so keying on it
    would expire every mapping the first time somebody uploaded counts. Which
    input feeds which link is a fact about TOPOLOGY."""
    _name()
    workbook = datasets.active_real_counts_excel()
    workbook.parent.mkdir(parents=True, exist_ok=True)
    workbook.write_bytes(b"PK\x03\x04 pretend workbook")
    assert demand_target_map.index_payload()["8"]["stale"] is False


def test_an_unreadable_network_reads_as_stale_never_as_fresh(network: str):
    _name()
    datasets.active_inpx_path().unlink()
    assert demand_target_map.active_inpx_sha256() is None
    assert demand_target_map.index_payload()["8"]["stale"] is True
    assert demand_target_map.fresh_index_payload() == {}


def test_a_mapping_with_no_network_to_bind_to_is_refused(workspace_root: Path):
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        _name()
    assert caught.value.code == "demand_target_no_network"
    assert caught.value.status == 409
    assert demand_target_map.list_mappings() == []


# ===========================================================================
# Revocation — append-only, and evidence stops immediately
# ===========================================================================


def test_revoking_appends_a_second_event_and_deletes_nothing(network: str):
    record = _name()
    revoked = demand_target_map.revoke_mapping(
        record["id"], revoked_by="minji", reason="상류 배분을 다시 보니 617이 아니었음"
    )

    assert revoked["revoked"] is True
    assert revoked["revoked_by"] == "minji"
    assert [event["event"] for event in revoked["history"]] == ["named", "revoked"]
    assert revoked["history"][0]["by"] == "yunha"

    # Still THERE (the audit view)...
    assert [item["id"] for item in demand_target_map.list_mappings()] == [record["id"]]
    # ...and no longer EVIDENCE (what the derivation reads).
    assert demand_target_map.index_payload() == {}
    assert demand_target_map.fresh_index_payload() == {}


def test_a_revoked_mapping_can_be_replaced_by_a_new_one(network: str):
    first = _name()
    demand_target_map.revoke_mapping(first["id"], revoked_by="minji", reason="아니었음")
    second = _name(vehicle_input_no="109", link_no="620", reason="다시 보니 620쪽")
    index = demand_target_map.index_payload()
    assert index["8"]["id"] == second["id"]
    assert index["8"]["vehicle_input_no"] == "109"
    assert {item["id"] for item in demand_target_map.list_mappings()} == {
        first["id"],
        second["id"],
    }


def test_revoking_twice_is_refused_rather_than_recorded_twice(network: str):
    record = _name()
    demand_target_map.revoke_mapping(record["id"], revoked_by="minji", reason="아니었음")
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        demand_target_map.revoke_mapping(record["id"], revoked_by="x", reason="또 아니었음")
    assert caught.value.code == "demand_target_already_revoked"
    assert [
        event["event"] for event in demand_target_map.get_mapping(record["id"])["history"]
    ] == ["named", "revoked"]


def test_revoking_requires_its_own_reason(network: str):
    record = _name()
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        demand_target_map.revoke_mapping(record["id"], revoked_by="minji", reason="  ")
    assert caught.value.code == "demand_target_reason_required"
    assert demand_target_map.get_mapping(record["id"])["revoked"] is False


def test_revoking_requires_a_namer_too(network: str):
    record = _name()
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        demand_target_map.revoke_mapping(record["id"], revoked_by="  ", reason="아니었음")
    assert caught.value.code == "demand_target_no_revoker"
    assert caught.value.status == 401


@pytest.mark.parametrize("bad", ["deadbeefcafe", "not-an-id", ""])
def test_revoking_an_unknown_id_is_a_named_refusal(network: str, bad: str):
    with pytest.raises(demand_target_map.DemandTargetRefused) as caught:
        demand_target_map.revoke_mapping(bad, revoked_by="minji", reason="x")
    assert caught.value.code == "demand_target_not_found"
    assert caught.value.status == 404


# ===========================================================================
# The index the derivation reads
# ===========================================================================


def test_the_index_carries_the_newest_mapping_per_node(
    network: str, monkeypatch: pytest.MonkeyPatch
):
    """Two mappings on one node is a normal state (a person changes their mind),
    and the derivation must read exactly one of them — the latest."""
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    older = _name(reason="처음 지정")
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T11:00:00+00:00")
    newer = _name(vehicle_input_no="213", reason="다시 지정")

    index = demand_target_map.index_payload()
    assert list(index) == ["8"]
    assert index["8"]["id"] == newer["id"]
    assert index["8"]["vehicle_input_no"] == "213"
    assert {item["id"] for item in demand_target_map.list_mappings()} == {older["id"], newer["id"]}


# ===========================================================================
# THE NODE'S LATEST WORD — and a withdrawal is a word
#
# AUTHORITY: the refuter, 2026-08-24, reproduced deterministically with a mocked
# clock. "The newest UNREVOKED record" silently RESURRECTS a superseded claim,
# which is the opposite of what withdrawing the record that superseded it means
# — and it contradicted this store's own docstring. Every delivered test until
# then had at most one un-revoked record standing per node, so the path had no
# cover at all.
# ===========================================================================


def test_withdrawing_the_latest_mapping_does_not_resurrect_the_one_it_replaced(
    network: str, monkeypatch: pytest.MonkeyPatch
):
    """F1(a) — THE HOLE, executed. Name A, name B, withdraw B: NOTHING stands.

    A is not "the mapping that stands" merely because it is the newest survivor:
    nobody has re-affirmed it since B replaced it, and the person who withdrew B
    was withdrawing the node's answer, not restoring an older one on its author's
    behalf. The clock is mocked to two distinct seconds so this test fails for
    the SELECTION rule and never for the tie-break.
    """
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    a = _name(vehicle_input_no="113", reason="A: 617이 대부분")
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T11:00:00+00:00")
    b = _name(vehicle_input_no="213", reason="B: 다시 보니 213")
    assert demand_target_map.index_payload()["8"]["id"] == b["id"]

    demand_target_map.revoke_mapping(b["id"], revoked_by="minji", reason="B도 아니었음")

    assert demand_target_map.index_payload() == {}
    assert demand_target_map.fresh_index_payload() == {}
    # Nothing was destroyed to get there — both records are still the audit view.
    assert {item["id"] for item in demand_target_map.list_mappings()} == {a["id"], b["id"]}


def test_getting_the_older_mapping_back_means_re_naming_it(
    network: str, monkeypatch: pytest.MonkeyPatch
):
    """F1(b) — the explicit re-affirmation path, and it is the ONLY one.

    Resurrection is not something this store does on somebody's behalf; it is a
    NEW record with fresh server stamps and a fresh stated reason, which is the
    only form in which this product accepts a claim at all.
    """
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    a = _name(vehicle_input_no="113", reason="A")
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T11:00:00+00:00")
    b = _name(vehicle_input_no="213", reason="B")
    demand_target_map.revoke_mapping(b["id"], revoked_by="minji", reason="B 아니었음")

    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T13:00:00+00:00")
    again = _name(vehicle_input_no="113", by="minji", reason="A가 맞았다 — 다시 확인")

    standing = demand_target_map.index_payload()["8"]
    assert standing["vehicle_input_no"] == "113"
    # THE NEW record, not the old one waking up: a different id, a different
    # author and the server's own later stamp.
    assert standing["id"] == again["id"] != a["id"]
    assert standing["named_by"] == "minji"
    assert standing["named_at"] == "2026-08-24T13:00:00+00:00"


def test_withdrawing_a_superseded_mapping_leaves_the_standing_one_alone(
    network: str, monkeypatch: pytest.MonkeyPatch
):
    """F1(c) — the other order. Name A, withdraw A, name B: B stands.

    The mirror of the case above, and the reason a revocation must NOT allocate
    a new sequence number: if it did, withdrawing A would move A to the front and
    leave this node with nothing while B was sitting there un-withdrawn.
    """
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    a = _name(vehicle_input_no="113", reason="A")
    demand_target_map.revoke_mapping(a["id"], revoked_by="minji", reason="A 아니었음")
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T11:00:00+00:00")
    b = _name(vehicle_input_no="213", reason="B")

    standing = demand_target_map.index_payload()["8"]
    assert standing["id"] == b["id"]
    assert standing["vehicle_input_no"] == "213"

    # ...and withdrawing the SUPERSEDED record after the fact changes nothing.
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T12:00:00+00:00")
    third = _name(vehicle_input_no="109", link_no="620", reason="C")
    demand_target_map.revoke_mapping(b["id"], revoked_by="minji", reason="B도 아니었음")
    assert demand_target_map.index_payload()["8"]["id"] == third["id"]


def test_two_records_in_the_same_second_are_ordered_by_append_not_by_id(
    network: str, monkeypatch: pytest.MonkeyPatch
):
    """F2 — the tie-break defect, executed.

    ``_now_iso`` truncates to seconds, so two records written inside one second
    carry the SAME ``named_at``; the old key then fell through to comparing two
    random ``uuid4`` fragments, and the refuter measured ``"zzzz…"`` beating
    ``"aaaa…"`` regardless of which was appended first.

    Both records are appended for real, through the real write path, and only
    their IDS are then forced into the adversarial order — the LATER record
    getting the alphabetically smaller one. So this test fails the moment
    selection consults an id or a timestamp again, and it cannot pass by
    accident of which uuid the machine happened to mint.
    """
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    first = _name(vehicle_input_no="113", reason="먼저")
    second = _name(vehicle_input_no="213", reason="나중")
    assert first["named_at"] == second["named_at"], "fixture: both inside one second"
    assert second["seq"] == first["seq"] + 1

    late_id, early_id = "a" * 12, "f" * 12
    path = demand_target_map.registry_path()
    stored = json.loads(path.read_text(encoding="utf-8"))
    renamed = {}
    for record in stored["mappings"].values():
        new_id = early_id if record["id"] == first["id"] else late_id
        renamed[new_id] = {**record, "id": new_id}
    stored["mappings"] = renamed
    path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    assert early_id > late_id, "fixture: the LATER record now has the SMALLER id"

    standing = demand_target_map.index_payload()["8"]
    assert standing["id"] == late_id
    assert standing["vehicle_input_no"] == "213"
    # The audit view uses the same total order, so it cannot list the two the
    # other way round from the claim that stands.
    assert [item["id"] for item in demand_target_map.list_mappings()] == [late_id, early_id]


def test_the_sequence_counter_survives_a_reread_and_never_reissues_a_number(network: str):
    first = _name(vehicle_input_no="113")
    second = _name(vehicle_input_no="213")
    assert (first["seq"], second["seq"]) == (1, 2)

    stored = json.loads(demand_target_map.registry_path().read_text(encoding="utf-8"))
    assert stored["sequence"] == 2

    # A registry whose counter was lost (or hand-edited downwards) must still
    # allocate ABOVE every record it holds — reissuing a number would make two
    # records tie on the one key that has to be total.
    stored["sequence"] = 0
    demand_target_map.registry_path().write_text(
        json.dumps(stored, ensure_ascii=False), encoding="utf-8"
    )
    third = _name(vehicle_input_no="109", link_no="620")
    assert third["seq"] == 3
    assert demand_target_map.index_payload()["8"]["id"] == third["id"]


def test_two_nodes_get_two_entries(network: str):
    _name(node_id="8")
    _name(
        node_id="4",
        vehicle_input_no="109",
        link_no="620",
        candidates=[{"vehicle_input_no": "109", "link_no": "620", "current_volume": 1.0}],
    )
    assert sorted(demand_target_map.index_payload()) == ["4", "8"]


def test_one_workspace_cannot_see_anothers_mappings(network: str):
    _name()
    other = workspaces.create_workspace("다른 프로젝트")
    with workspaces.bind_workspace(other["id"]):
        _write_network(b"<network>other project</network>")
        assert demand_target_map.list_mappings() == []
        assert demand_target_map.index_payload() == {}
        _name(node_id="3", vehicle_input_no="109", link_no="620", by="someone-else")
        assert [item["node_id"] for item in demand_target_map.list_mappings()] == ["3"]
    assert [item["node_id"] for item in demand_target_map.list_mappings()] == ["8"]


# ===========================================================================
# The registry file itself
# ===========================================================================


def test_a_corrupt_registry_is_quarantined_with_its_bytes_intact(network: str):
    _name()
    path = demand_target_map.registry_path()
    path.write_bytes(b"{ this is not json")

    assert demand_target_map.list_mappings() == []
    quarantined = sorted(path.parent.glob(f"{path.name}.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"{ this is not json"
    disclosure = demand_target_map.registry_health_status()
    assert disclosure["code"] == "registry_quarantined"
    assert set(disclosure["messages"]) == {"ko", "en", "ja"}


def test_the_store_lives_beside_the_step_attestations_not_under_genesis(network: str):
    _name()
    directory = demand_target_map.demand_targets_dir()
    workspace = workspaces.workspace_dir(workspaces.current_workspace_id())
    assert directory.relative_to(workspace).parts == ("calibration", "demand-targets")
    assert "genesis" not in directory.relative_to(workspace).parts
    assert demand_target_map.registry_path().is_file()

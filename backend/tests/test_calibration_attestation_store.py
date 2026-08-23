"""Unit #53: the signed human attestation store, and the four ways it refuses.

A signature is the only evidence in this product a machine did not produce, so
the properties worth executing are the ones that keep it from becoming a tick:

* **The server stamps who and when.** The store takes the attester as an
  argument and the clock from itself; the ROUTE takes the attester from the
  session and forbids the field on the body (pinned in
  ``tests/test_calibration_endpoint.py``). Between them there is no path from a
  request body to a name on a record.
* **A reason is mandatory.** An empty one is refused by name, in three
  languages, before anything is written.
* **Only the nine steps with no machine evidence path may be signed.** ①⑥⑦⑫
  are refused by name — each of them has an artifact that can prove or refuse
  it, and a signature beside a measurement is two opinions about one network.
* **The claim is bound to the network's OWN BYTES.** Not to
  ``datasets.dataset_revision``, which folds the observed workbook into the same
  string (the trap ``tools/calibration_loop_live_check.py`` measured on
  2026-08-07) and would therefore expire every signature in a project the first
  time somebody uploaded counts. Both directions are executed below: a changed
  ``.inpx`` flips ``stale``, and a changed WORKBOOK does not.
* **A revocation is an event, not a delete.** Both events survive in the
  record's history, and the withdrawn claim stops being evidence immediately.

No COM, no Vissim, no network call: every test here runs against a few bytes in
a tmp directory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services import calibration_attestation, calibration_rail, datasets, workspaces


# ===========================================================================
# Fixtures — one tmp workspace, one tiny "network"
# ===========================================================================


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The same redirection ``tests/test_calibration_endpoint.py`` uses."""
    root = tmp_path / "workspaces"
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    # The repo ships a demo network at ``data/practice.inpx`` and
    # ``get_active_inpx_path`` falls back to it. Point that fallback at nothing
    # so "no network" is a state these tests can actually reach, and so the
    # hashes below are of bytes this file wrote.
    monkeypatch.setattr(datasets, "INPX_PATH", tmp_path / "no-such-demo.inpx")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield root
    finally:
        workspaces.reset_current_workspace_id(token)


def _write_network(content: bytes) -> str:
    """Put ``content`` where the active dataset's ``.inpx`` lives; return its sha."""
    path = datasets.active_inpx_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


@pytest.fixture()
def network(workspace_root: Path) -> str:
    return _write_network(b"<network>one</network>")


def _sign(step: int = 2, *, reason: str = "현장에서 속도 제한을 확인함", by: str = "yunha"):
    return calibration_attestation.record_attestation(
        step_number=step, reason=reason, attested_by=by
    )


# ===========================================================================
# What the server stamps, and what a caller may say
# ===========================================================================


def test_the_store_stamps_the_clock_and_trims_the_attester(network: str):
    record = _sign(by="  yunha  ")
    assert record["attested_by"] == "yunha"
    # Not asserted against a fixed value — asserted to EXIST and to be the
    # server's ISO stamp, because the only wrong answer here is a client's.
    assert record["attested_at"] and record["attested_at"].startswith("20")
    assert record["inpx_sha256"] == network
    assert record["stale"] is False
    assert record["revoked"] is False

    stored = json.loads(calibration_attestation.registry_path().read_text(encoding="utf-8"))
    on_disk = stored["attestations"][record["id"]]
    assert on_disk["kind"] == calibration_attestation.ATTESTATION_DOC_KIND
    assert on_disk["workspace_id"] == workspaces.current_workspace_id()
    assert on_disk["history"] == [
        {
            "event": "attested",
            "at": record["attested_at"],
            "by": "yunha",
            "reason": "현장에서 속도 제한을 확인함",
        }
    ]


def test_a_signature_nobody_can_be_named_for_is_refused(network: str):
    with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
        _sign(by="   ")
    assert caught.value.code == "calibration_attestation_no_attester"
    assert caught.value.status == 401
    assert set(caught.value.messages) == {"ko", "en", "ja"}


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_an_empty_reason_is_refused_by_name(network: str, reason: str):
    """A signature with no stated reason is a tick, and a tick is what this
    whole codebase is arranged against."""
    with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
        _sign(reason=reason)
    assert caught.value.code == "calibration_attestation_reason_required"
    assert calibration_attestation.list_attestations() == []


def test_an_over_long_reason_is_refused_rather_than_truncated(network: str):
    with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
        _sign(reason="가" * (calibration_attestation.MAX_REASON_CHARS + 1))
    assert caught.value.code == "calibration_attestation_reason_too_long"


# ===========================================================================
# WHICH steps may be signed
# ===========================================================================


@pytest.mark.parametrize("step", calibration_rail.ATTESTABLE_STEPS)
def test_every_step_with_no_machine_evidence_path_can_be_signed(network: str, step: int):
    record = _sign(step)
    assert record["step_number"] == step


@pytest.mark.parametrize("step", [1, 6, 7, 12])
def test_a_step_that_carries_machine_evidence_cannot_be_signed(network: str, step: int):
    """①⑥⑦⑫ are refused BY NAME, never accepted-and-ignored.

    Each has an artifact that answers — the generation gate / model inspection,
    the 현시도 attestations, ``verify_applied_row`` twice — so a signature there
    would sit beside a measurement, and the first time the two disagreed this
    screen would have to pick one.
    """
    with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
        _sign(step)
    assert caught.value.code == "calibration_attestation_step_not_attestable"
    assert caught.value.status == 400
    assert caught.value.detail["attestable_steps"] == list(calibration_rail.ATTESTABLE_STEPS)
    assert calibration_attestation.list_attestations() == []


@pytest.mark.parametrize("step", [0, 14, -3, 99])
def test_a_step_number_that_is_not_a_step_at_all_is_refused(network: str, step: int):
    with pytest.raises(calibration_attestation.StepAttestationRefused):
        _sign(step)


def test_the_model_refuses_an_unsignable_step_even_when_built_directly(network: str):
    """The validator is on the RECORD, not only on the entry function — so a
    future second writer cannot bypass the rule by not calling this one."""
    with pytest.raises(ValueError):
        calibration_attestation.StepAttestation(
            id="a" * 12,
            workspace_id="w",
            step_number=1,
            attested_by="yunha",
            attested_at="2026-08-23T00:00:00+00:00",
            reason="r",
            inpx_sha256="0" * 64,
        )


# ===========================================================================
# The staleness key — the .inpx's own bytes, and nothing else
# ===========================================================================


def test_the_signature_goes_stale_when_the_network_changes(network: str):
    record = _sign()
    assert calibration_attestation.index_payload()[2]["stale"] is False

    _write_network(b"<network>two: a lane was added</network>")
    assert calibration_attestation.index_payload()[2]["stale"] is True

    # And BACK: the key is the content, not a timestamp. Restoring the exact
    # bytes restores the claim, because the claim was true of those bytes.
    _write_network(b"<network>one</network>")
    assert calibration_attestation.index_payload()[2]["stale"] is False
    assert calibration_attestation.index_payload()[2]["id"] == record["id"]


def test_uploading_observed_counts_does_not_expire_a_signature(network: str):
    """THE TRAP THIS KEY EXISTS TO AVOID (``tools/calibration_loop_live_check.py``,
    MEASURED 2026-08-07): ``datasets.dataset_revision`` folds ``real_counts.xlsx``
    into the SAME string as the network, so keying staleness on it would
    mass-expire every signature in a project the moment somebody uploaded count
    data. Observed counts cannot change what a human confirmed about the
    geometry, the speeds or the lane use.
    """
    _sign()
    workbook = datasets.active_real_counts_excel()
    workbook.parent.mkdir(parents=True, exist_ok=True)
    workbook.write_bytes(b"PK\x03\x04 pretend workbook")
    # The revision string DOES move (that is the trap); the signature does not.
    assert calibration_attestation.index_payload()[2]["stale"] is False


def test_an_unreadable_network_reads_as_stale_never_as_fresh(network: str):
    """Unknown is not fresh — the same rule step ⑨ applies to a missing count."""
    _sign()
    datasets.active_inpx_path().unlink()
    assert calibration_attestation.active_inpx_sha256() is None
    assert calibration_attestation.index_payload()[2]["stale"] is True


def test_a_signature_with_no_network_to_bind_to_is_refused(workspace_root: Path):
    with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
        _sign()
    assert caught.value.code == "calibration_attestation_no_network"
    assert caught.value.status == 409
    assert calibration_attestation.list_attestations() == []


# ===========================================================================
# Revocation — append-only, and evidence stops immediately
# ===========================================================================


def test_revoking_appends_a_second_event_and_deletes_nothing(network: str):
    record = _sign()
    revoked = calibration_attestation.revoke_attestation(
        record["id"], revoked_by="minji", reason="네트워크를 다시 봤더니 아니었음"
    )

    assert revoked["revoked"] is True
    assert revoked["revoked_by"] == "minji"
    assert revoked["revoked_reason"] == "네트워크를 다시 봤더니 아니었음"
    # BOTH events, in order, with the original signing event untouched.
    assert [event["event"] for event in revoked["history"]] == ["attested", "revoked"]
    assert revoked["history"][0]["by"] == "yunha"
    assert revoked["history"][0]["reason"] == "현장에서 속도 제한을 확인함"

    # The record is still THERE (this is the audit view)...
    listed = calibration_attestation.list_attestations()
    assert [item["id"] for item in listed] == [record["id"]]
    # ...and it is no longer EVIDENCE (this is what the rail reads).
    assert calibration_attestation.index_payload() == {}


def test_a_revoked_signature_can_be_replaced_by_a_new_one(network: str):
    first = _sign()
    calibration_attestation.revoke_attestation(first["id"], revoked_by="minji", reason="아니었음")
    second = _sign(reason="다시 확인함")
    index = calibration_attestation.index_payload()
    assert index[2]["id"] == second["id"]
    assert {item["id"] for item in calibration_attestation.list_attestations()} == {
        first["id"],
        second["id"],
    }


def test_revoking_twice_is_refused_rather_than_recorded_twice(network: str):
    record = _sign()
    calibration_attestation.revoke_attestation(record["id"], revoked_by="minji", reason="아니었음")
    with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
        calibration_attestation.revoke_attestation(
            record["id"], revoked_by="someone", reason="또 아니었음"
        )
    assert caught.value.code == "calibration_attestation_already_revoked"
    stored = calibration_attestation.get_attestation(record["id"])
    assert [event["event"] for event in stored["history"]] == ["attested", "revoked"]


def test_revoking_requires_its_own_reason(network: str):
    record = _sign()
    with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
        calibration_attestation.revoke_attestation(record["id"], revoked_by="minji", reason="  ")
    assert caught.value.code == "calibration_attestation_reason_required"
    assert calibration_attestation.get_attestation(record["id"])["revoked"] is False


def test_revoking_an_unknown_id_is_a_named_refusal(network: str):
    for bad in ["deadbeefcafe", "not-an-id", ""]:
        with pytest.raises(calibration_attestation.StepAttestationRefused) as caught:
            calibration_attestation.revoke_attestation(bad, revoked_by="minji", reason="x")
        assert caught.value.code == "calibration_attestation_not_found"
        assert caught.value.status == 404


# ===========================================================================
# The index the rail reads
# ===========================================================================


def test_the_index_carries_the_newest_signature_per_step(
    network: str, monkeypatch: pytest.MonkeyPatch
):
    """Two signatures on one step is a normal state (a person re-confirms after
    a change), and the rail must read exactly one of them — the latest."""
    monkeypatch.setattr(calibration_attestation, "_now_iso", lambda: "2026-08-23T09:00:00+00:00")
    older = _sign(reason="처음 확인")
    monkeypatch.setattr(calibration_attestation, "_now_iso", lambda: "2026-08-23T11:00:00+00:00")
    newer = _sign(reason="다시 확인")

    index = calibration_attestation.index_payload()
    assert list(index) == [2]
    assert index[2]["id"] == newer["id"]
    assert index[2]["reason"] == "다시 확인"
    # Nothing was destroyed to get there.
    assert {item["id"] for item in calibration_attestation.list_attestations()} == {
        older["id"],
        newer["id"],
    }


def test_the_index_is_keyed_by_step_number_as_an_int(network: str):
    _sign(9)
    _sign(13)
    index = calibration_attestation.index_payload()
    assert sorted(index) == [9, 13]
    assert all(isinstance(key, int) for key in index)


def test_one_workspace_cannot_see_anothers_signatures(network: str):
    _sign()
    other = workspaces.create_workspace("다른 프로젝트")
    with workspaces.bind_workspace(other["id"]):
        _write_network(b"<network>other project</network>")
        assert calibration_attestation.list_attestations() == []
        assert calibration_attestation.index_payload() == {}
        _sign(step=3, by="someone-else")
        assert [item["step_number"] for item in calibration_attestation.list_attestations()] == [3]
    # Back in the first project: unchanged, and the other's signature invisible.
    assert [item["step_number"] for item in calibration_attestation.list_attestations()] == [2]


# ===========================================================================
# The registry file itself
# ===========================================================================


def test_a_corrupt_registry_is_quarantined_with_its_bytes_intact(network: str):
    _sign()
    path = calibration_attestation.registry_path()
    path.write_bytes(b"{ this is not json")

    # Reading degrades to "no signatures" rather than raising at the screen...
    assert calibration_attestation.list_attestations() == []
    # ...and the corrupt bytes are preserved beside the fresh registry.
    quarantined = sorted(path.parent.glob(f"{path.name}.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"{ this is not json"
    disclosure = calibration_attestation.registry_health_status()
    assert disclosure["code"] == "registry_quarantined"
    assert set(disclosure["messages"]) == {"ko", "en", "ja"}


def test_the_store_lives_beside_the_workspaces_other_stores_not_under_genesis(network: str):
    _sign()
    directory = calibration_attestation.attestations_dir()
    workspace = workspaces.workspace_dir(workspaces.current_workspace_id())
    assert directory.relative_to(workspace).parts == ("calibration", "step-attestations")
    assert "genesis" not in directory.relative_to(workspace).parts
    assert calibration_attestation.registry_path().is_file()

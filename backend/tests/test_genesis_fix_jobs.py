"""Roadmap B5-2 stage 2 — the FIX JOB: clone a refused generation's draft,
apply ONE approved verb via COM on the job-queue thread pattern, and re-gate
the result as a NEW SIBLING job whose ``derived_from`` points at the parent.

**"거부는 진단" (a refusal IS the diagnosis) is the doctrine under test.** The
parent job's artifact and ``gate.json`` are the "before" evidence of that
diagnosis, so the single fact every test family below returns to is: nothing
a fix job does may change a single byte under the PARENT's own work
directory. §1 pins that directly (a full directory walk, sha before == after)
and every other family re-checks it in passing.

No live COM anywhere in this file — :func:`genesis_jobs._build_fix_edit_session`
is monkeypatched to a fake session exactly the way
``test_calibration_loop_e2e.py``'s ``_build_edit_session`` seam is faked, so
the REAL tool registry, the REAL ``GuardrailEngine`` preflight/postwrite
sandwich and the REAL R1.5 gate all run — only the COM connection itself is
fake, and its ``persist()`` really mutates the working copy's XML so the
guardrail's postwrite diff sees a genuine before/after change.

**The one concrete fix this file exercises.** A ``v6_input_volume_is_zero``
vehicle input (one ``<timeIntervalVehVolume ... volume="0"/>`` interval,
composition already wired) repaired by ``set_vehicle_input_volume`` — chosen
over the more obvious ``v6_input_without_volume`` (no interval element at
all) specifically because introducing a volume interval from NOTHING would
also introduce a fresh ``vehComp`` reference that did not exist in the
"before" shadow model, which trips the postwrite guardrail's own
"nothing-else-moved" diff for a reason that has nothing to do with this
unit's own correctness. Starting from an existing zero-volume interval keeps
the diff to exactly the field ``set_vehicle_input_volume`` is meant to touch.

Two parent networks, one fixed difference:

* ``_v6_zero_volume_network(with_island=True)`` — the zero-volume input PLUS
  an unreachable "island" link (V3's unconditional fault, provenance-blind).
  The fix repairs V6 but V3 is untouched, so the sibling job's re-gate stays
  REFUSED — §2's family.
* ``_v6_zero_volume_network(with_island=False)`` — the zero-volume input
  alone. The fix is the network's ONLY refuse-tier fault, so the sibling
  job's re-gate reaches a non-refused (``provisional``) verdict and inherits
  ``gate_and_publish``'s auto-registration — §3's family.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from services import datasets, genesis_jobs, workspaces
from services.genesis.gate import GateVerdict

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
    """A private workspaces root for one test, with its default workspace bound.

    Same shape as ``test_genesis_jobs.py``'s own fixture of the same name —
    duplicated rather than imported across test modules (this repo's existing
    convention only cross-imports plain helper FUNCTIONS between genesis test
    files, e.g. ``network``/``link``/``straight_pair`` below, never fixtures).
    """
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
# Network fixtures
# ---------------------------------------------------------------------------


def _v6_zero_volume_network(*, with_island: bool) -> str:
    """A drivable pair with vehicle input 1 stuck at 0 veh/h -- V6 refuses
    with ``v6_input_volume_is_zero`` regardless of provenance (an unproven
    file grades exactly as strictly as a proven one; only a proven-FOREIGN
    file is lenient here, and nothing in this module ever declares one).

    ``with_island=True`` adds a link nothing can enter or leave (V3's own
    unconditional fault, ``tests/test_genesis_jobs.py``'s ``_island_network``
    pattern) so the network carries a SECOND, unrelated refuse-tier fault the
    ``set_vehicle_input_volume`` fix never touches.
    """
    links = list(straight_pair())
    if with_island:
        links.append(link(999, [(0.0, 400.0), (60.0, 400.0)], name="island"))
    return network(
        links=links,
        extra=demand_layer(inputs=[(1, 1, 1, 0)]),
        support=gate_support + demand_support,
    )


_FINDING_CODE = "v6_input_volume_is_zero"
_VERB = "set_vehicle_input_volume"
_PARAMS = {"input_no": "1", "volume": 500.0, "interval_no": 1}


def _seed_refused_fix_parent(*, with_island: bool, name: str = "parent") -> dict[str, Any]:
    """A REFUSED job whose artifact is :func:`_v6_zero_volume_network`,
    gated for real (mirrors ``test_genesis_jobs.py``'s own
    ``_seed_job`` + ``gate_and_publish`` pattern for a hand-written,
    refusable artifact)."""
    record = _seed_job(_v6_zero_volume_network(with_island=with_island), name=name)
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _v6_two_zero_volume_inputs_network() -> str:
    """TWO independent drivable pairs, EACH with its OWN zero-volume vehicle
    input on its OWN link -- ``v6_input_volume_is_zero`` fires TWICE, once
    per input. ``gate._check_v6``'s own numeric-aware sort over vehicle
    inputs means a finding LOOKUP KEYED BY CODE ALONE resolves to input 1's
    finding first; input 2's own finding exists in the SAME verdict too.

    Exactly the shape fix-round exploit #2 needs: a caller can submit params
    addressing input 2 while a finding_code-only lookup names input 1.
    """
    second_pair = [
        link(3, [(0.0, 400.0), (100.0, 400.0)]),
        link(4, [(104.0, 400.0), (204.0, 400.0)]),
        connector(10002, 3, 4, [(100.0, 400.0), (104.0, 400.0)], lane_pairs=[(1, 1)], from_pos=100.0),
    ]
    links = list(straight_pair()) + second_pair
    return network(
        links=links,
        extra=demand_layer(inputs=[(1, 1, 1, 0), (2, 3, 1, 0)]),
        support=gate_support + demand_support,
    )


def _seed_two_input_refused_parent(name: str = "two_input_parent") -> dict[str, Any]:
    record = _seed_job(_v6_two_zero_volume_inputs_network(), name=name)
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _seed_v3_only_refused_parent(name: str = "v3_only_parent") -> dict[str, Any]:
    """A REFUSED parent with an island fault (V3) and an otherwise CLEAN
    demand layer -- no V6 issue anywhere in its verdict at all. Fix-round
    exploit #3's fixture: a v6 finding_code applied against THIS parent must
    be refused because the finding genuinely is not there, not because of
    any other unrelated fault."""
    record = _seed_job(
        network(
            links=list(straight_pair()) + [link(999, [(0.0, 400.0), (60.0, 400.0)], name="island")],
            extra=demand_layer(),
            support=gate_support + demand_support,
        ),
        name=name,
    )
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    assert "V3" in result["gate"]["blocking"]
    assert "V6" not in result["gate"]["blocking"]
    return result


#: A hand-built ``services.observations.observed_side``-shaped node carrying
#: an OBJECT_ID-keyed movement whose FROM-link is link "1" -- the exact
#: shape ``entrance_measurement``'s own docstring uses as its worked example
#: (node 8, link 617, 649 observed vehicles), with the link renumbered to "1"
#: to match this file's own fixtures. Real ``services.observations`` data can
#: never produce this shape today (its rows are always "<leg>|<turn>"-keyed,
#: never a Vissim OBJECT_ID -- ``genesis_jobs.derive_v6_node_evidence``'s own
#: docstring names this pre-existing, out-of-scope gap), so every test that
#: needs a MEASURED entrance monkeypatches ``genesis_jobs.derive_v6_node_evidence``
#: directly to return this -- the same seam pattern this file already uses
#: for COM (``_build_fix_edit_session``).
_MEASURED_LINK_1_NODE_EVIDENCE = {
    "node_id": "8",
    "node_name": "측정된 교차로",
    "total_real": 649,
    "total_sim_basam": None,
    "count_basis": "turning_counts",
    "directions": [{"direction": "8-1-100.0-1-150.0", "real": 649, "sim_basam": 0}],
}


def _fake_v6_node_evidence(matching_link: str = "1"):
    def _lookup(link_id: str):
        return _MEASURED_LINK_1_NODE_EVIDENCE if link_id == matching_link else None

    return _lookup


def _seed_passing_parent(name: str = "clean") -> dict[str, Any]:
    """A non-refused (provisional) job -- for §4's "parent not refused" case,
    and for registering a real active dataset before §2's "untouched"
    assertion so that assertion is checking something, not the empty
    default."""
    record = _seed_job(
        network(
            links=straight_pair(),
            extra=demand_layer(),
            support=gate_support + demand_support,
        ),
        name=name,
    )
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_PROVISIONAL, result
    return result


# ---------------------------------------------------------------------------
# The fake edit session -- COM's only stand-in in this file
# ---------------------------------------------------------------------------


class FakeFixEditSession:
    """``VissimEditSession``'s surface for ``set_vehicle_input_volume``, with
    an XML-mutating ``persist()`` -- same fixture shape and the same reason as
    ``test_calibration_loop_e2e.py``'s ``FakeEditSession``: a real postwrite
    diff needs a real change on disk to compare against, not a mocked no-op.
    """

    def __init__(self, working_inpx: Path, *, readback: float | None = None, fail: bool = False):
        self.working_inpx = Path(working_inpx)
        self._readback = readback
        self._fail = fail
        self._pending: dict[int, float] = {}
        self.closed = False

    def set_vehicle_input_volume(self, input_no: int, volume: float) -> None:
        if self._fail:
            raise RuntimeError("simulated COM failure: SetAttValue refused")
        self._pending[input_no] = volume

    def get_vehicle_input_volume(self, input_no: int) -> float:
        return self._pending.get(input_no, self._readback if self._readback is not None else 0.0)

    def persist(self) -> None:
        if not self._pending:
            return
        text = self.working_inpx.read_text(encoding="utf-8")
        for input_no, volume in self._pending.items():
            pattern = (
                f"<vehicleInput link='1' name='' no='{input_no}'><timeIntVehVols>"
                f"<timeIntervalVehVolume timeInt='1 0' vehComp='1' volume='0'/>"
            )
            replacement = (
                f"<vehicleInput link='1' name='' no='{input_no}'><timeIntVehVols>"
                f"<timeIntervalVehVolume timeInt='1 0' vehComp='1' volume='{volume}'/>"
            )
            assert pattern in text, f"fixture: could not locate zero-volume input {input_no}"
            text = text.replace(pattern, replacement, 1)
        self.working_inpx.write_text(text, encoding="utf-8")
        self._pending.clear()

    def close(self) -> None:
        self.closed = True


def _fake_session_factory(*, readback: float | None = None, fail: bool = False):
    def _build(working_inpx: Path) -> FakeFixEditSession:
        return FakeFixEditSession(working_inpx, readback=readback, fail=fail)

    return _build


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _run_fix(
    monkeypatch: pytest.MonkeyPatch,
    parent_job_id: str,
    *,
    readback: float | None = 500.0,
    fail: bool = False,
    finding_code: str = _FINDING_CODE,
    verb: str = _VERB,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``create_genesis_fix_job`` + ``run_genesis_fix_job``, synchronously,
    with a faked edit session -- the fix-job equivalent of
    ``test_genesis_jobs.py``'s own ``_generate`` helper."""
    monkeypatch.setattr(
        genesis_jobs, "_build_fix_edit_session", _fake_session_factory(readback=readback, fail=fail)
    )
    record = genesis_jobs.create_genesis_fix_job(
        parent_job_id,
        finding_code=finding_code,
        verb=verb,
        params=dict(params or _PARAMS),
        approved_by="reviewer",
        approval_rationale="V6 zero-volume repaired from a sibling entrance's observed count.",
    )
    return genesis_jobs.run_genesis_fix_job(record["id"], record["workspace_id"])


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _work_dir_shas(work_dir: Path) -> dict[str, str]:
    """``{relative path: sha256}`` for every file under ``work_dir``, for a
    whole-directory byte-immutability check."""
    shas: dict[str, str] = {}
    for path in sorted(work_dir.rglob("*")):
        if path.is_file():
            shas[str(path.relative_to(work_dir))] = _sha256_bytes(path.read_bytes())
    return shas


def _job_dirs(workspace_root: Path) -> set[str]:
    """Every job directory id currently on disk in the bound workspace --
    used to prove a refused ``create_genesis_fix_job`` call created NONE."""
    del workspace_root  # kept in the signature for readability at call sites
    root = genesis_jobs.jobs_root()
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir()}


# ===========================================================================
# §1 End-to-end + parent immutability -- the doctrine itself
# ===========================================================================


def test_fix_job_runs_end_to_end_and_leaves_the_parent_byte_unchanged(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    parent = _seed_refused_fix_parent(with_island=True)
    parent_work_dir = genesis_jobs.job_dir(parent["id"])
    before_shas = _work_dir_shas(parent_work_dir)
    assert before_shas, "the parent must have at least its artifact and gate files on disk"

    result = _run_fix(monkeypatch, parent["id"])

    # A genuine sibling: its own id, its own gate.json, a real verdict.
    assert result["id"] != parent["id"]
    assert result["status"] in genesis_jobs.TERMINAL_STATUSES
    assert result["gate"] is not None
    sibling_work_dir = genesis_jobs.job_dir(result["id"])
    assert (sibling_work_dir / genesis_jobs.GATE_JSON_FILENAME).is_file()
    stored_gate = json.loads(
        (sibling_work_dir / genesis_jobs.GATE_JSON_FILENAME).read_text(encoding="utf-8")
    )
    assert stored_gate["verdict"] in {v.value for v in GateVerdict}

    # The doctrine: not one byte under the PARENT's work dir moved.
    after_shas = _work_dir_shas(parent_work_dir)
    assert after_shas == before_shas, "the parent's work dir changed -- the diagnosis was destroyed"


def test_fix_job_lineage_is_byte_anchored_to_the_parent_artifact(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    parent = _seed_refused_fix_parent(with_island=True)
    parent_inpx = genesis_jobs.artifact_path(parent["id"], "inpx")
    parent_sha = _sha256_bytes(parent_inpx.read_bytes())

    result = _run_fix(monkeypatch, parent["id"])

    derived = result["derived_from"]
    assert derived["job_id"] == parent["id"]
    assert derived["finding_code"] == _FINDING_CODE
    assert derived["verb"] == _VERB
    assert derived["approved_by"] == "reviewer"
    assert derived["parent_sha256"] == parent_sha
    assert derived["approved_at"]


# ===========================================================================
# §2 A re-gate that REFUSES -- the active dataset is untouched
# ===========================================================================


def test_a_fix_whose_regate_still_refuses_never_touches_the_active_dataset(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The island link is unrelated to the V6 fix, so the sibling job's
    re-gate stays REFUSED (V3) even after the volume is repaired -- and a
    dataset that was ALREADY registered before this fix job ran must survive
    it byte-for-byte, exactly as an ordinary refused generation never touches
    the workspace's active dataset (test_genesis_jobs.py's own §3)."""
    registered_first = _seed_passing_parent(name="already_live")
    active_before = datasets.get_active_dataset()
    assert active_before["source"] != "default"

    parent = _seed_refused_fix_parent(with_island=True)
    result = _run_fix(monkeypatch, parent["id"])

    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    assert result["publication"] is None
    assert "V3" in result["gate"]["blocking"]

    active_after = datasets.get_active_dataset()
    assert active_after["filename"] == active_before["filename"]
    assert active_after["revision"] == active_before["revision"]


# ===========================================================================
# §3 A re-gate that PASSES -- inherited auto-publish, same as any generation
# ===========================================================================


def test_a_fix_whose_regate_passes_registers_the_dataset_like_any_generation(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without the island link, the V6 volume fix is the network's ONLY
    refuse-tier fault -- the sibling job's re-gate reaches a non-refused
    verdict, and ``gate_and_publish``'s auto-registration (inherited,
    unmodified, exactly as the roadmap unit's spec requires) makes it the
    workspace's active dataset."""
    parent = _seed_refused_fix_parent(with_island=False)
    result = _run_fix(monkeypatch, parent["id"])

    assert result["status"] != genesis_jobs.STATUS_REFUSED, result
    assert result["status"] in (genesis_jobs.STATUS_PROVISIONAL, genesis_jobs.STATUS_PUBLISHED)
    assert result["publication"] is not None

    sibling_inpx = genesis_jobs.artifact_path(result["id"], "inpx")
    active = datasets.get_active_dataset()
    assert active["source"] == "uploaded"
    assert _sha256_bytes(Path(active["path"]).read_bytes()) == _sha256_bytes(sibling_inpx.read_bytes())
    assert result["publication"]["artifact_sha256"] == _sha256_bytes(sibling_inpx.read_bytes())


# ===========================================================================
# §4 derived_from survives the listing route -- key-absent vs. present
# ===========================================================================


def test_derived_from_is_absent_on_ordinary_jobs_and_present_on_fix_jobs(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
):
    ordinary = _seed_passing_parent(name="ordinary")
    parent = _seed_refused_fix_parent(with_island=True, name="fix_parent")
    fix_job = _run_fix(monkeypatch, parent["id"])

    listed = client.get("/api/genesis/jobs")
    assert listed.status_code == 200, listed.text
    by_id = {job["id"]: job for job in listed.json()["jobs"]}

    assert "derived_from" not in by_id[ordinary["id"]]
    assert "derived_from" not in by_id[parent["id"]]
    assert "derived_from" in by_id[fix_job["id"]]
    assert by_id[fix_job["id"]]["derived_from"]["job_id"] == parent["id"]
    assert by_id[fix_job["id"]]["derived_from"]["finding_code"] == _FINDING_CODE

    # And direct reads agree with the listing.
    direct = client.get(f"/api/genesis/jobs/{fix_job['id']}")
    assert direct.status_code == 200, direct.text
    assert direct.json()["derived_from"]["parent_sha256"] == fix_job["derived_from"]["parent_sha256"]


# ===========================================================================
# §5 Named refusals at CREATE time -- no job directory either way
# ===========================================================================


def test_a_fix_job_on_a_non_refused_parent_is_refused_by_name(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    parent = _seed_passing_parent()
    before = _job_dirs(workspace_root)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            verb=_VERB,
            params=dict(_PARAMS),
            approved_by="reviewer",
            approval_rationale="should never get this far",
        )
    assert excinfo.value.code == "genesis_fix_parent_not_refused"

    after = _job_dirs(workspace_root)
    assert after == before, "a refused fix-job creation must not create a job directory"


def test_a_stale_verb_is_refused_by_name_before_a_job_dir_exists(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    parent = _seed_refused_fix_parent(with_island=True)
    before = _job_dirs(workspace_root)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            verb="this_verb_does_not_exist_anymore",
            params={"input_no": "1"},
            approved_by="reviewer",
            approval_rationale="the proposal went stale",
        )
    assert excinfo.value.code == "genesis_fix_verb_no_longer_registered"

    after = _job_dirs(workspace_root)
    assert after == before, "a stale-verb refusal must not create a job directory"


def test_stale_params_are_refused_the_same_named_way(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The verb itself still resolves, but the approved params no longer
    validate against its live schema (e.g. a required field the model has
    since gained) -- same named refusal as an unregistered verb, because
    both mean the same thing: this approved proposal has gone stale."""
    parent = _seed_refused_fix_parent(with_island=True)
    before = _job_dirs(workspace_root)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            verb=_VERB,
            params={"input_no": "1", "volume": "not-a-number", "interval_no": 1},
            approved_by="reviewer",
            approval_rationale="stale params",
        )
    assert excinfo.value.code == "genesis_fix_verb_no_longer_registered"

    after = _job_dirs(workspace_root)
    assert after == before


# ===========================================================================
# §6 A failing verb dispatch -- errored by name, parent unchanged, nothing
#    registered
# ===========================================================================


def test_a_failing_dispatch_errors_the_job_and_leaves_everything_else_alone(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    registered_first = _seed_passing_parent(name="already_live_2")
    active_before = datasets.get_active_dataset()

    parent = _seed_refused_fix_parent(with_island=True)
    parent_work_dir = genesis_jobs.job_dir(parent["id"])
    before_shas = _work_dir_shas(parent_work_dir)

    result = _run_fix(monkeypatch, parent["id"], fail=True)

    assert result["status"] == genesis_jobs.STATUS_ERROR
    assert result["refusal"]["code"].startswith("fix_apply_failed:")
    assert result["gate"] is None
    assert result["publication"] is None

    after_shas = _work_dir_shas(parent_work_dir)
    assert after_shas == before_shas

    active_after = datasets.get_active_dataset()
    assert active_after["filename"] == active_before["filename"]
    assert active_after["revision"] == active_before["revision"]


# ===========================================================================
# §7 Fix round (adversarial pass) -- three proven exploits, refused BY NAME
# at the SERVICE layer, so every caller (HTTP or otherwise) is covered.
# ===========================================================================


def test_create_fix_job_refuses_a_finding_code_absent_from_the_parent_verdict(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exploit #3: a v6 finding_code against a V3-only parent (no V6 fault
    anywhere in ITS verdict) must be refused BY NAME rather than minting a
    fix job for a diagnosis the parent's own gate never made."""
    parent = _seed_v3_only_refused_parent()
    before = _job_dirs(workspace_root)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            verb=_VERB,
            params=dict(_PARAMS),
            approved_by="reviewer",
            approval_rationale="should refuse: this parent's verdict never named this finding",
        )
    assert excinfo.value.code == "genesis_fix_finding_not_in_parent_verdict"

    after = _job_dirs(workspace_root)
    assert after == before, "a finding-not-in-verdict refusal must not create a job directory"


def test_create_fix_job_refuses_params_that_address_a_different_object_than_the_finding(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exploit #2: v6_input_volume_is_zero fires on BOTH inputs 1 and 2 in
    this parent's verdict. The approval names input 1's finding EXPLICITLY
    (by its subject -- unit #30 made a subject-less duplicate refuse before
    this check is ever reached, see §8) while the params address input 2:
    refused BY NAME rather than silently rewriting a DIFFERENT vehicle input
    than the one the addressed finding actually diagnosed."""
    parent = _seed_two_input_refused_parent()
    before = _job_dirs(workspace_root)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            subject="1",
            verb=_VERB,
            params={"input_no": "2", "volume": 500.0, "interval_no": 1},
            approved_by="reviewer",
            approval_rationale="should refuse: params address input 2, the addressed finding is input 1",
        )
    assert excinfo.value.code == "genesis_fix_params_target_mismatch"

    after = _job_dirs(workspace_root)
    assert after == before, "a params/subject mismatch refusal must not create a job directory"


def test_create_fix_job_refuses_a_measured_entrance_the_measurement_gate(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exploit #1 (service layer): an entrance whose FROM-link IS observed
    (measured) must refuse BY NAME, even though the pre-fix-round code would
    have let a well-formed params payload straight through -- the service
    layer's real-evidence re-derivation is the authoritative gate.

    Real ``services.observations`` data cannot itself produce a MEASURED
    verdict today (see ``genesis_jobs.derive_v6_node_evidence``'s own
    docstring for the named, pre-existing gap), so this monkeypatches that
    ONE function directly -- the same seam this file already uses for COM --
    to supply the evidence a genuinely joinable entrance really would carry,
    and proves the REFUSAL WIRING downstream of it.
    """
    parent = _seed_refused_fix_parent(with_island=True)
    before = _job_dirs(workspace_root)

    monkeypatch.setattr(genesis_jobs, "derive_v6_node_evidence", _fake_v6_node_evidence())

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            verb=_VERB,
            params=dict(_PARAMS),
            approved_by="reviewer",
            approval_rationale="attempted overwrite of a measured entrance",
        )
    assert excinfo.value.code == "genesis_fix_measurement_gate_not_satisfied"
    assert "INPUT_VOLUME_IS_OBSERVED_DATA" in excinfo.value.reasons[0]

    after = _job_dirs(workspace_root)
    assert after == before, "a measurement-gate refusal must not create a job directory"


def test_the_happy_path_still_works_unverified_entrance_plus_user_supplied_volume(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Fix-round item 6, made explicit as its OWN test (the pre-existing §1/§3
    tests already exercise this end-to-end, but this one pins the SPECIFIC
    claim the fix round could have broken): an entrance derive_v6_node_evidence
    genuinely cannot join (no ingested observation set at all, in this test's
    workspace) stays UNVERIFIED, and the approver's own submitted volume
    still reaches a real proposal matching the submitted verb -- the
    measurement-gate re-derivation does not turn into a second, silent
    approval requirement for the ordinary case."""
    parent = _seed_refused_fix_parent(with_island=True)

    result = _run_fix(monkeypatch, parent["id"])
    assert result["derived_from"]["finding_code"] == _FINDING_CODE
    assert result["derived_from"]["verb"] == _VERB
    assert result["status"] in genesis_jobs.TERMINAL_STATUSES
    assert result["gate"] is not None


# ===========================================================================
# §8 Unit #30 -- duplicate-coded findings, individually addressable by
# subject. B5-2's recorded limit was that `_finding_in_stored_gate` resolved
# by code alone to the FIRST match, so only the first of two same-coded
# findings was treatable and a fix aimed at the second silently landed on the
# first's identity. (code, subject) is now the address.
# ===========================================================================


def _stored_gate_subjects(job_id: str, code: str) -> list[str]:
    """Every subject in this job's STORED gate.json carrying ``code`` -- read
    through the same ``iter_stored_gate_findings`` walk the lookup itself
    uses, so the test addresses findings by exactly the identity the service
    resolves."""
    raw = json.loads(
        (genesis_jobs.job_dir(job_id) / genesis_jobs.GATE_JSON_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    return [
        finding["subject"]
        for _check_id, _origin, finding in genesis_jobs.iter_stored_gate_findings(raw)
        if finding["code"] == code
    ]


def test_the_second_duplicate_finding_is_addressable_by_its_own_subject(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The SECOND of two same-coded findings, addressed by subject, mints a
    fix job whose params and lineage derive from THAT finding -- the exact
    call that pre-#30 refused ``genesis_fix_params_target_mismatch`` because
    the code-only lookup had landed on input 1's finding."""
    parent = _seed_two_input_refused_parent()
    subjects = _stored_gate_subjects(parent["id"], _FINDING_CODE)
    assert subjects == ["1", "2"], subjects  # a V6 finding's subject IS the input number

    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_FINDING_CODE,
        subject="2",
        verb=_VERB,
        params={"input_no": "2", "volume": 500.0, "interval_no": 1},
        approved_by="reviewer",
        approval_rationale="input 2 addressed by its own subject",
    )

    # The addressing cross-check (exploit #2's fix) accepted input 2's params
    # -- proof the lookup resolved finding 2, not the numerically-first one.
    assert record["derived_from"]["subject"] == "2"
    assert record["derived_from"]["params"]["input_no"] == "2"
    assert record["derived_from"]["finding_code"] == _FINDING_CODE


def test_omitting_subject_on_a_duplicated_code_refuses_by_name_listing_the_candidates(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Subject omitted while the code appears twice: the NEW named refusal,
    listing BOTH candidate subjects, and no job directory -- never a silent
    resolve to the first match (these params would have been accepted for
    input 1 pre-#30)."""
    parent = _seed_two_input_refused_parent()
    before = _job_dirs(workspace_root)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            verb=_VERB,
            params=dict(_PARAMS),
            approved_by="reviewer",
            approval_rationale="should refuse: two findings carry this code, none was named",
        )
    assert excinfo.value.code == "genesis_fix_finding_ambiguous_without_subject"
    assert "'1'" in excinfo.value.reasons[0]
    assert "'2'" in excinfo.value.reasons[0]

    after = _job_dirs(workspace_root)
    assert after == before, "an ambiguity refusal must not create a job directory"


def test_a_wrong_subject_refuses_not_in_parent_verdict_and_names_what_was_looked_for(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """A subject the verdict does not carry under this code is the SAME
    "no diagnosis to answer" refusal an absent code gets -- and the message
    names the (code, subject) pair that was looked for."""
    parent = _seed_two_input_refused_parent()
    before = _job_dirs(workspace_root)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_FINDING_CODE,
            subject="7",
            verb=_VERB,
            params=dict(_PARAMS),
            approved_by="reviewer",
            approval_rationale="should refuse: no finding with this subject exists",
        )
    assert excinfo.value.code == "genesis_fix_finding_not_in_parent_verdict"
    assert "'7'" in excinfo.value.reasons[0]

    after = _job_dirs(workspace_root)
    assert after == before


def test_a_single_match_code_without_subject_still_works_and_records_the_resolved_subject(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Backward compatibility: a subject-less create against a code that
    appears exactly ONCE keeps working (every pre-#30 caller), and the record
    still stores the RESOLVED finding's subject -- lineage precision is not
    conditional on the caller having supplied it."""
    parent = _seed_refused_fix_parent(with_island=True)

    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_FINDING_CODE,
        verb=_VERB,
        params=dict(_PARAMS),
        approved_by="reviewer",
        approval_rationale="single match; no subject needed",
    )
    assert record["derived_from"]["subject"] == "1"
    assert record["derived_from"]["finding_code"] == _FINDING_CODE


def test_an_old_fix_record_without_a_stored_subject_still_loads(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """A registry record written before ``derived_from.subject`` existed
    (key-ABSENT, the same discipline the field itself follows) must keep
    reading fine -- no migration, no KeyError, the rest of the lineage
    intact."""
    parent = _seed_refused_fix_parent(with_island=True)
    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_FINDING_CODE,
        subject="1",
        verb=_VERB,
        params=dict(_PARAMS),
        approved_by="reviewer",
        approval_rationale="then aged into a pre-#30 record",
    )

    aged = json.loads(json.dumps(record))
    del aged["derived_from"]["subject"]
    genesis_jobs._upsert(aged)

    reread = genesis_jobs.get_job(record["id"])
    assert "subject" not in reread["derived_from"]
    assert reread["derived_from"]["finding_code"] == _FINDING_CODE
    assert reread["derived_from"]["verb"] == _VERB

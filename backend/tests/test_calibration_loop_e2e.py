"""Unit 2.1 — the approval-loop closure demonstration (Phase 2 exit clause 1).

**Read this file as the demo script.** Every piece below is already proven
somewhere else in the suite; what has never been shown until now is the WHOLE
chain as one artifact, in order, with nothing hand-written in the middle:

    run evidence (U1)  ->  reality check (U5)  ->  fix proposal (U4)
        ->  the agent proposes that verb  ->  the approval PAUSE
        ->  an AUTHENTICATED reviewer approves (U7/U8/U9)
        ->  the edit applies  ->  the audit DOCX (1.3) tells the whole story,
            with the reviewer's name and the machine's own rationale on it.

The six stages of :func:`test_the_whole_calibration_loop_ends_in_one_signed_document`
are numbered in comments and are meant to be read top to bottom.

WHAT IS REAL AND WHAT IS FAKED
------------------------------
Real: the dataset (``tools/make_demo_data.py``, unit U4a), the counts loader
(``validation.load_base_workbook``), the run evidence collector
(``tools_run._collect_run_evidence``), the registry tool
(``run_reality_check``), the mapper (``fixes.map_node_counts``), the whole
``/agent/query/stream`` -> ``pending_approval.json`` -> ``/agent/approve``
flow with its guardrails, the account + password hash + session token + the
role gate on the approve endpoint, the ledger append and the DOCX render.

Faked, at exactly the two seams every other endpoint test uses and no others:
``_default_provider`` (the planner LLM) and ``_build_edit_session`` (Vissim
COM). ``FakeEditSession.persist()`` really mutates the working copy's XML so
``guardrails.py``'s postwrite target-persisted check positively VERIFIES the
write instead of silently degrading — the same fixture shape, and the same
reason, as ``test_agent_approval_endpoint.py``'s.

THE CONVENTION THIS FILE DOCUMENTS
----------------------------------
A proposal carries a machine-generated ``rationale`` (ko/en/ja, with its GEH
numbers in it); an approval carries the human's ``reason`` (1.3's field, which
becomes the audit document's 근거 column). Nothing in the backend copies one
into the other, and it deliberately does not: the 근거 column is what a HUMAN
signed, so the human's client is what puts text there. The documented
convention — exercised at stage 4 — is that the caller passes the proposal's
rationale as the approve ``reason``, so the reviewer signs off on the
machine's stated evidence rather than on a blank line.

MEASURED CAVEAT, AND WHAT UNIT D4 DID TO IT (2026-08-12). Before D4 this
proposal's rationale was 235 chars in ko and 222 in ja — inside the 300-char
``MAX_APPROVAL_REASON_CHARS`` — but 398 in en, so only an English session had
to abridge. D4 appends the demand-ESTIMATE policy sentence (this entrance could
not be verified as measured; if you know the real count, reject this and supply
it), which put ko at 385 and ja at 365: every language over the old cap, and
the first thing an abridgement drops is exactly that caveat.

So the cap was raised to 600 (routers/assistant.py carries the reasoning; its
job is keeping a BLOB out of the ledger, which 600 still does). ko and ja are
therefore signed VERBATIM again, as this file's convention always intended.
**en, at 647, still overflows** — the same caveat this file has recorded since
before D4, now with one more sentence in it. All three numbers are measured and
ASSERTED at stage 4 below rather than described here, so this paragraph cannot
rot into a false claim: if en ever fits, that assertion fails and says so.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from routers import auth as auth_router
from services import audit_trail, auth, datasets, results_evidence, validation
from services.agent import fixes, reality_check, tools_metrics
from services.agent.toolkit import dispatch
from services.agent.tools_network import build_shadow_model
from services.agent.tools_run import _collect_run_evidence
from tools import make_demo_data

#: Clears ``auth.MIN_PASSWORD_CHARS`` and exists only inside the session-scoped
#: tmp database ``tests/conftest.py`` points ``services.db.DB_PATH`` at.
PASSWORD = "calibration-loop-password"

#: The reviewer who signs this demo off, and the modeler who may not (stage 6).
REVIEWER = ("loop-reviewer", "reviewer", "이윤하")
MODELER = ("loop-modeler", "modeler", "김모델")

#: How the reviewer's name renders in the DOCX's 승인자 column: the username is
#: printed alongside because display names are not unique (audit_trail.py).
REVIEWER_CELL = "이윤하 (loop-reviewer · 검토자)"


# ===========================================================================
# STAGE 1 fixtures — the dataset, the run, the observed counts
# ===========================================================================


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Unit U4a's fixture dataset, generated into a pytest tmp root.

    ``--out`` is mandatory here: a default run would rebuild the repo's live
    ``data/`` demo dataset (make_demo_data.py's own module docstring says what
    that changes about a machine somebody is demoing from).
    """
    out = tmp_path_factory.mktemp("calibration-demo-data")
    argv = sys.argv
    sys.argv = ["make_demo_data.py", "--out", str(out)]
    try:
        make_demo_data.main()
    finally:
        sys.argv = argv
    return out


@pytest.fixture(scope="module")
def single_seed_run_dir(demo_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """ONE result database — the shape a ``run_simulation`` with default seeds
    produces.

    Deliberately not the scenario's three: U1's ``read_movement_class_rows``
    SUMS VEHS across every .db it is handed, so a 3-seed directory carries
    three runs' worth of vehicles, and ``build_reality_check_report`` refuses
    that outright rather than manufacturing a 3x demand error.
    """
    work = tmp_path_factory.mktemp("calibration-run-work")
    shutil.copy(
        demo_dir / "Scenarios" / "S000001" / "S000001.results" / "run_001.db",
        work / "run_001.db",
    )
    return work


@pytest.fixture(scope="module")
def run_result(demo_dir: Path, single_seed_run_dir: Path) -> dict[str, Any]:
    """U1's REAL evidence collector over that database — no fabricated dict."""
    return _collect_run_evidence(
        single_seed_run_dir,
        demo_dir / "practice.inpx",
        duration_s=3600,
        evaluation_configured=False,
    )


@pytest.fixture(scope="module")
def observed(demo_dir: Path) -> dict[str, Any]:
    """The observed side, through the REAL loader."""
    return validation.load_base_workbook(str(demo_dir / "real_counts.xlsx"))


# ===========================================================================
# STAGE 2 fixtures — the reality check, reached the way the agent reaches it
# ===========================================================================


@pytest.fixture(scope="module")
def reality_check_report(demo_dir: Path, single_seed_run_dir: Path) -> dict[str, Any]:
    """``dispatch("run_reality_check", ...)`` — the registry tool path.

    The planner would send exactly these params after a ``get_vehicle_inputs``
    read: node 1's demand is driven by vehicle input 1, whose current volume is
    the demand its approach carries (the sum of its three movements' base
    volumes, 990 veh/h). Node 2 gets no target at all — which is the honest
    state of a first pass, and is why its answer comes back as a refusal that
    says what it could not grade rather than as an invented routing fix.

    ``pytest.MonkeyPatch`` is driven by hand because this fixture is
    module-scoped (the dataset generation above is slow enough to be worth
    doing once) and the ``monkeypatch`` fixture is function-scoped.
    """
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: single_seed_run_dir)
        mp.setattr(datasets, "get_active_inpx_path", lambda: demo_dir / "practice.inpx")
        mp.setattr(
            datasets, "get_active_real_counts_excel", lambda: demo_dir / "real_counts.xlsx"
        )
        envelope = dispatch(
            "run_reality_check",
            {
                "scenario_id": "S000001",
                "period": "AM",
                "targets": [
                    {
                        "node_id": "1",
                        "vehicle_input_no": "1",
                        "vehicle_input_current_volume": 990.0,
                    }
                ],
            },
        )
    finally:
        mp.undo()
    assert envelope["status"] == "ok", envelope
    return envelope["data"]["reality_check"]


def _node(report: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(entry for entry in report["nodes"] if entry["node_id"] == node_id)


@pytest.fixture(scope="module")
def demand_proposal(reality_check_report: dict[str, Any]) -> dict[str, Any]:
    """Node 1's outcome: the concrete fix this whole file carries to a signature."""
    return _node(reality_check_report, "1")["outcome"]


@pytest.fixture(scope="module")
def reconciling_refusal(reality_check_report: dict[str, Any]) -> dict[str, Any]:
    """Node 2's outcome: the model already reproduces what was counted there."""
    return _node(reality_check_report, "2")["outcome"]


# ===========================================================================
# STAGE 3+ fixtures — the agent endpoint, its two seams, the HTTP client
# ===========================================================================


class FakeStructuredProvider:
    """Canned (action, meta) queue — see test_agent_endpoint.py's identical
    fixture for the full rationale."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str = "확인합니다", **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


class FakeEditSession:
    """VissimEditSession's surface for this one write verb, with an
    XML-mutating ``persist()``.

    Same fixture shape, and the same reason, as
    test_agent_approval_endpoint.py's: the demo network's ``<vehicleInput>``
    elements are self-closing with no ``<timeIntervalVehVolume>`` child, so
    persist() injects one exactly as a real Vissim SaveNetAs would materialize
    it — which is what lets ``guardrails.py``'s postwrite target-persisted
    check positively verify the write instead of silently degrading.
    """

    def __init__(self, working_inpx: Path, *, readback: float) -> None:
        self.working_inpx = Path(working_inpx)
        self._readback = readback
        self._pending: dict[int, float] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.persist_calls = 0
        self.closed = False

    def set_vehicle_input_volume(self, input_no: int, volume: float) -> None:
        self.calls.append(("set_vehicle_input_volume", input_no, volume))
        self._pending[input_no] = volume

    def get_vehicle_input_volume(self, input_no: int) -> float:
        return self._readback

    def persist(self) -> None:
        self.persist_calls += 1
        if not self._pending:
            return
        text = self.working_inpx.read_text(encoding="utf-8")
        for input_no, volume in self._pending.items():
            pattern = re.compile(rf'(<vehicleInput no="{input_no}"[^>]*?)/>')
            replacement = rf'\1><timeIntervalVehVolume volume="{volume}"/></vehicleInput>'
            text, count = pattern.subn(replacement, text, count=1)
            assert count == 1, f"fixture: could not locate vehicleInput no={input_no}"
        self.working_inpx.write_text(text, encoding="utf-8")
        self._pending.clear()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _pin_active_dataset_to_the_demo_network(monkeypatch: pytest.MonkeyPatch, demo_dir: Path):
    """The agent edits the SAME network the reality check graded.

    Every other approval test pins ``data/practice.inpx``; this one must pin
    the generated fixture instead, or the proposal's ``input_no`` would be
    checked against a different network than the one that produced the miss.
    """
    monkeypatch.setattr(
        assistant_endpoint, "get_active_inpx_path", lambda: demo_dir / "practice.inpx"
    )


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    from services.agent.edit_session import clear_active_edit_session

    clear_active_edit_session()
    yield
    clear_active_edit_session()


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers (kept local rather than cross-imported, matching the stated
# convention of test_agent_approval_endpoint.py / test_agent_approval_authz.py)
# ---------------------------------------------------------------------------


def _stream_events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _login(client: TestClient, account: tuple[str, str, str]) -> str:
    """Create the account if needed, then log in through the REAL endpoint."""
    username, role, display_name = account
    existing = auth.get_user_by_username(username)
    if existing is None:
        auth.create_user(username, PASSWORD, role, display_name=display_name)
    else:
        assert existing["role"] == role
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == role
    return response.cookies[auth_router.SESSION_COOKIE_NAME]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _propose_the_proposed_verb(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, proposal: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    """One propose turn whose planned action IS the proposal, verbatim.

    The planner queue is built from ``proposal["params"]`` by unpacking rather
    than by retyping the numbers, so this can never drift from what U5/U4
    actually produced. Three entries: the proposing turn's action, the approved
    re-run's action, and its answer.

    The turn is fired UNAUTHENTICATED on purpose — U8 gates the DECISION, not
    the proposal.
    """
    provider = FakeStructuredProvider(
        [
            _action(proposal["verb"], summary="실측 대조 결과에 따른 수요 보정 제안", **proposal["params"]),
            _action(proposal["verb"], summary="승인된 변경을 적용합니다", **proposal["params"]),
            _action("respond", summary="변경을 완료했습니다", answer="차량 입력 1의 볼륨을 조정했습니다."),
        ]
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)
    response = client.post(
        "/api/assistant/agent/query/stream",
        json={
            "message": "실측 대조에서 나온 수요 보정 제안을 적용해줘",
            "response_language": "ko",
        },
    )
    assert response.status_code == 200
    events = _stream_events(response)
    done = events[-1]
    assert done["type"] == "done"
    assert done["response"]["kind"] == "approval_request"
    approval_event = next(e for e in events if e["type"] == "approval_request")
    return done["response"]["session_id"], approval_event["approval_id"], approval_event


def _ledger_records(session_id: str) -> list[dict[str, Any]]:
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    return audit_trail.read_records(work_dir / f"trace_{session_id}.jsonl")


def _confirm_record(session_id: str, stage: str) -> dict[str, Any]:
    records = [
        r for r in _ledger_records(session_id)
        if r.get("kind") == "user_confirm" and (r.get("payload") or {}).get("stage") == stage
    ]
    assert len(records) == 1, f"expected exactly one {stage!r} record, got {len(records)}"
    return records[0]["payload"]


def _decision_records(session_id: str) -> list[dict[str, Any]]:
    """Only the approve/decline records — the propose turn legitimately leaves
    ``plan``/``preflight``/``user_confirm(stage="requested")`` behind."""
    return [
        r for r in _ledger_records(session_id)
        if r.get("kind") == "user_confirm"
        and (r.get("payload") or {}).get("stage") in ("approved", "declined")
    ]


def _document(client: TestClient, session_id: str, language: str = "ko"):
    from docx import Document

    response = client.get(
        f"/api/agent/audit/{session_id}/export.docx", params={"language": language}
    )
    assert response.status_code == 200
    return Document(BytesIO(response.content))


def _table_rows(table) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


def _all_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in _table_rows(table):
            parts.extend(row)
    return "\n".join(parts)


# ===========================================================================
# THE DEMONSTRATION
# ===========================================================================


def test_the_whole_calibration_loop_ends_in_one_signed_document(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    run_result: dict[str, Any],
    observed: dict[str, Any],
    reality_check_report: dict[str, Any],
    demand_proposal: dict[str, Any],
    reconciling_refusal: dict[str, Any],
):
    # -----------------------------------------------------------------------
    # STAGE 1 — the evidence. A real run and real observed counts.
    # -----------------------------------------------------------------------
    # The modelled side is THIS run's own movement table (U1), not the
    # workbook's pasted ``Vehs(All)`` column; the observed side is the real
    # loader's output, with no warnings.
    assert run_result["evidence"][results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE].startswith(
        "PRESENT("
    )
    assert len(run_result["movement_volumes"]) == len(make_demo_data.MOVEMENTS)
    assert observed["warnings"] == []
    assert "AM" in observed["periods"]

    # -----------------------------------------------------------------------
    # STAGE 2 — the reality check. One proposal, one refusal, both evidenced.
    # -----------------------------------------------------------------------
    assert reality_check_report["status"] == reality_check.CHECKED
    assert [entry["node_id"] for entry in reality_check_report["proposals"]] == ["1"]
    assert [entry["node_id"] for entry in reality_check_report["refusals"]] == ["2"]

    # The tool graded the run collected at stage 1 — same modelled total, to
    # the vehicle, summed from the same movement rows.
    node_1_modelled = sum(
        row["vehs"]
        for object_id, row in run_result["movement_volumes"].items()
        if row["node"] == "1" and str(object_id) != "1"
    )
    assert _node(reality_check_report, "1")["modelled_total"] == pytest.approx(node_1_modelled)

    # (a) THE PROPOSAL: a demand miss, with the arithmetic that produced it.
    # As generated (make_demo_data.py pins the digits in its own docstring):
    # observed 3150 vs modelled 2738.5 -> GEH 7.58 >= 5.0, and the fix is
    # 990 * 3150 / 2738.5 = 1138.76 veh/h on vehicle input 1.
    assert demand_proposal["outcome"] == fixes.PROPOSAL
    assert demand_proposal["scope"] == fixes.DEMAND_LEVEL
    assert demand_proposal["verb"] == "set_vehicle_input_volume"
    # UNIT D4: the arithmetic is unchanged and so is the verb, but the label
    # is not — this entrance could not be verified as measured (the demo
    # workbook records turns as tokens, not as link-bearing OBJECT_IDs), so
    # what a reviewer is asked to approve is a NAMED demand estimate.
    assert demand_proposal["confidence"] == fixes.CONFIDENCE_DEMAND_ESTIMATE
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in demand_proposal["assumptions"]
    assert (
        demand_proposal["evidence"]["target"]["entrance_measurement"]["status"]
        == fixes.ENTRANCE_UNVERIFIED
    )
    evidence = demand_proposal["evidence"]
    assert evidence["node_total_geh"] >= validation.GEH_GOOD_LT
    assert 7.0 <= evidence["node_total_geh"] <= 8.0, evidence
    assert demand_proposal["params"]["volume"] == round(
        evidence["target"]["current_volume"]
        * evidence["observed_total"]
        / evidence["modelled_total"],
        2,
    )
    # The rationale STATES that arithmetic, in all three languages.
    for language in ("ko", "en", "ja"):
        text = demand_proposal["rationale"][language]
        assert str(evidence["node_total_geh"]) in text, language
        assert str(demand_proposal["params"]["volume"]) in text, language

    # (b) THE REFUSAL: node 2 reconciles, so nothing is proposed for it — and
    # the refusal discloses the movements it could NOT judge rather than
    # counting them as good.
    assert reconciling_refusal["outcome"] == fixes.REFUSAL
    assert reconciling_refusal["reason_code"] == fixes.MODEL_REPRODUCES_OBSERVED
    assert reconciling_refusal["verb"] is None and reconciling_refusal["params"] is None
    assert reconciling_refusal["evidence"]["node_total_geh"] < validation.GEH_GOOD_LT
    assert reconciling_refusal["evidence"]["ungradable_movement_n"] > 0
    for language in ("ko", "en", "ja"):
        assert "5.0" in reconciling_refusal["rationale"][language], language
    # Coverage is honest about the third node too: counted in the field, absent
    # from this run, named rather than dropped by a silent inner join.
    assert reality_check_report["coverage"]["observed_nodes_absent_from_run"] == [
        str(make_demo_data.OBSERVED_ONLY_NODE)
    ]

    # -----------------------------------------------------------------------
    # STAGE 3 — the agent invokes the proposed verb, and the loop PAUSES.
    # -----------------------------------------------------------------------
    session_id, approval_id, approval_event = _propose_the_proposed_verb(
        client, monkeypatch, demand_proposal
    )

    # The pending approval's params ARE the proposal's params. This is the
    # join the whole exit clause turns on: what a human is about to sign is
    # the number the evidence produced, not a number a planner reworded.
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    pending_path = work_dir / "pending_approval.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending["approval_id"] == approval_id
    assert pending["actions"][0]["tool"] == demand_proposal["verb"]
    assert pending["actions"][0]["params"] == demand_proposal["params"]
    assert approval_event["actions"][0]["params"] == demand_proposal["params"]
    # Nothing has been written yet: the pause is a real pause.
    assert _decision_records(session_id) == []

    # -----------------------------------------------------------------------
    # STAGE 4 — an AUTHENTICATED reviewer approves, carrying the rationale.
    # -----------------------------------------------------------------------
    # The convention this file documents (see the module docstring): the caller
    # passes the proposal's machine rationale as the approve `reason`, so the
    # human-visible 근거 column carries the evidence the decision was made on.
    #
    # MEASURED, not described — the numbers are asserted so the module
    # docstring's caveat cannot rot into a false claim. Unit D4's policy
    # sentence lengthened every rationale, and the cap was raised 300 -> 600 in
    # answer (routers/assistant.py says why): ko and ja now fit and are signed
    # VERBATIM; en still does not, which is the same overflow this file has
    # recorded since before D4.
    lengths = {lang: len(text) for lang, text in demand_proposal["rationale"].items()}
    cap = assistant_endpoint.MAX_APPROVAL_REASON_CHARS
    assert lengths["ko"] <= cap and lengths["ja"] <= cap, lengths
    assert lengths["en"] > cap, (
        "the en rationale now fits too — delete the en caveat from the module docstring"
    )
    rationale = demand_proposal["rationale"]["ko"]
    assert len(rationale) <= cap

    fake_session = FakeEditSession(
        work_dir / "working.inpx", readback=demand_proposal["params"]["volume"]
    )
    monkeypatch.setattr(
        assistant_endpoint, "_build_edit_session", lambda working_path: fake_session
    )

    token = _login(client, REVIEWER)
    approve = client.post(
        "/api/assistant/agent/approve",
        json={
            "session_id": session_id,
            "approval_id": approval_id,
            "approved": True,
            "reason": rationale,
        },
        headers=_bearer(token),
    )

    assert approve.status_code == 200
    assert _stream_events(approve)[-1]["response"]["kind"] == "answer"

    # The edit really dispatched, was persisted, and the session was closed.
    assert fake_session.persist_calls == 1
    assert fake_session.closed is True
    assert ("set_vehicle_input_volume", 1, demand_proposal["params"]["volume"]) in fake_session.calls
    # ... and the SAVED working copy carries the value (the demo network's
    # input 1 has no volume at all before this write, so a lost edit shows up
    # as an absent value, not merely a different number).
    saved = build_shadow_model(work_dir / "working.inpx")
    assert next(v["volume"] for v in saved["vehicle_inputs"] if v["no"] == "1") == (
        demand_proposal["params"]["volume"]
    )
    # Single-use: the pending approval is consumed.
    assert not pending_path.exists()

    # The ledger records WHO decided, WHAT they authorized and WHY.
    reviewer = auth.get_user_by_username(REVIEWER[0])
    approved_record = _confirm_record(session_id, "approved")
    assert approved_record["actor"] == {
        "user_id": reviewer["id"],
        "username": REVIEWER[0],
        "display_name": REVIEWER[2],
        "role": "reviewer",
    }
    assert approved_record["params"] == demand_proposal["params"]
    assert approved_record["reason"] == rationale

    # -----------------------------------------------------------------------
    # STAGE 5 — ONE document tells the whole story.
    # -----------------------------------------------------------------------
    summary = client.get(f"/api/agent/audit/{session_id}/summary")
    assert summary.status_code == 200
    assert summary.json()["changes"] == 1
    assert summary.json()["applied"] == 1
    assert summary.json()["refusals"] == 0
    assert summary.json()["with_rationale"] == 1

    doc = _document(client, session_id)
    changes = _table_rows(doc.tables[0])
    assert changes[0] == [
        "#", "시각(UTC)", "대상", "항목", "변경 전 → 변경 후", "도구", "검증", "승인자", "근거",
    ]
    assert len(changes) == 2, "exactly one change row — the approved write"
    row = changes[1]
    # (a) the change, before -> after, with its unit. The target names the
    # INTERVAL as well as the input: ``interval_no`` is an identifier by the
    # audit trail's own naming rule (audit_trail._is_id_param), and the
    # proposal's own params say interval 1 — which is the interval the counts
    # were measured in, not a default nobody chose.
    assert row[2] == "input_no=1, interval_no=1"
    assert row[3] == "volume"
    assert row[4] == f"기록 없음 → {demand_proposal['params']['volume']} veh/h"
    assert row[5] == "set_vehicle_input_volume"
    assert row[6].startswith("검증 통과")
    # (b) the 승인자 cell: the reviewer who actually logged in
    assert row[7] == REVIEWER_CELL
    assert "기록 없음(인증 이전)" not in _all_text(doc)
    # (c) the 근거 cell: the proposal's own rationale VERBATIM — GEH numbers,
    # arithmetic, and D4's demand-ESTIMATE caveat, none of them abridged away.
    assert row[8] == rationale
    assert f"GEH {demand_proposal['evidence']['node_total_geh']}" in row[8]
    assert demand_proposal["evidence"]["arithmetic"] in row[8]
    assert "수요 추정" in row[8]

    # (d) THE ABSENCE THAT IS THE POINT: node 2's refusal produced no edit, so
    # it appears nowhere as a change. A refused node that quietly showed up in
    # the change table would be the exact lie this loop exists to prevent.
    text = _all_text(doc)
    assert len(doc.tables) == 1, "no refusal table: nothing was refused, rolled back or declined"
    assert "거부·롤백·거절된 변경이 없습니다." in text
    assert "요약: 적용 1건 · 롤백/실패 0건 · 거부·거절 0건 · 근거 기입 1건." in text
    assert "set_route_relative_flow" not in text
    assert "노드 2" not in text
    assert reconciling_refusal["rationale"]["ko"] not in text
    assert fixes.MODEL_REPRODUCES_OBSERVED not in text

    # (e) the same ledger in English: the column headers translate, the
    # reviewer's signed rationale does NOT — it is a human's text, quoted.
    english = _document(client, session_id, language="en")
    english_rows = _table_rows(english.tables[0])
    assert english_rows[0][7:9] == ["Approver", "Rationale"]
    assert english_rows[1][7] == "이윤하 (loop-reviewer · reviewer)"
    assert english_rows[1][8] == rationale


# ===========================================================================
# STAGE 6 — the demo survives an interruption
# ===========================================================================


def test_a_modeler_cannot_close_the_loop_and_the_reviewer_still_can(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    demo_dir: Path,
    demand_proposal: dict[str, Any],
    auth_headers,
):
    """The same proposal, interrupted: a modeler clicks approve and is refused.

    The proposal must survive that refusal — a gate that ate the approval it
    rejected would turn one mis-authenticated click into a silently lost
    calibration step, and the demo would dead-end with nothing to show. Then
    the reviewer finishes the job and the document is the same document.

    ``auth_headers`` is U8's shared conftest helper (bearer channel, no cookie);
    the reviewer logs in through the real endpoint as in stage 4, so both
    authentication channels are exercised against this one gate.
    """
    session_id, approval_id, _ = _propose_the_proposed_verb(
        client, monkeypatch, demand_proposal
    )
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    pending_path = work_dir / "pending_approval.json"
    pending_before = pending_path.read_bytes()

    refused = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
        headers=auth_headers(MODELER[0], MODELER[1], display_name=MODELER[2]),
    )

    assert refused.status_code == 403
    # The 403 NAMES the roles — the modeler needs to know who to ask.
    assert "reviewer, admin" in refused.json()["detail"]
    # Nothing consumed, nothing recorded: a refused decision is not a decision.
    assert pending_path.read_bytes() == pending_before
    assert _decision_records(session_id) == []
    # ... and the network is untouched: the working copy is still byte-identical
    # to the clone the propose turn made of the active dataset.
    assert (work_dir / "working.inpx").read_bytes() == (
        demo_dir / "practice.inpx"
    ).read_bytes()

    # ... and the reviewer completes the very same approval.
    fake_session = FakeEditSession(
        work_dir / "working.inpx", readback=demand_proposal["params"]["volume"]
    )
    monkeypatch.setattr(
        assistant_endpoint, "_build_edit_session", lambda working_path: fake_session
    )
    rationale = demand_proposal["rationale"]["ko"]
    token = _login(client, REVIEWER)
    approve = client.post(
        "/api/assistant/agent/approve",
        json={
            "session_id": session_id,
            "approval_id": approval_id,
            "approved": True,
            "reason": rationale,
        },
        headers=_bearer(token),
    )

    assert approve.status_code == 200
    assert not pending_path.exists()
    assert _confirm_record(session_id, "approved")["actor"]["username"] == REVIEWER[0]

    row = _table_rows(_document(client, session_id).tables[0])[1]
    assert row[2] == "input_no=1, interval_no=1"
    assert row[7] == REVIEWER_CELL
    assert row[8] == rationale

"""Unit #50: ``/api/calibration`` — the calibration workspace's read side.

Four GETs, and the properties worth pinning are the ones that make them safe
to point a screen at:

* **Nothing here writes.** In particular ``GET /pending-approvals`` reads the
  single-use approval token WITHOUT spending it: a viewer that consumed an
  approval by looking at it would break the approve leg for the human who
  actually has to decide. Pinned by reading twice and then checking the file is
  still on disk.
* **A refusal is an answer, not a 500.** Scoring a run that does not exist
  comes back 200 carrying ``reality_check``'s own reason code, because the
  screen has to render the sentence, not an HTTP status.
* **The workspace boundary holds**, on the one route that enumerates sessions.
* **The rail degrades rather than fails** when an artifact is unreadable.

Nothing here starts a simulation, opens COM, or edits the active dataset.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import admin_headers
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_router
from services import calibration_attestation, calibration_rail, workspaces
from services.agent import saturation_flow


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspaces"
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield root
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the agent-session store the same way the assistant router's own
    tests do — by name, on the module the listing imports its helpers from."""
    root = tmp_path / "agent_sessions"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(assistant_router, "SESSIONS_ROOT", root)
    return root


@pytest.fixture()
def client(workspace_root: Path):
    with TestClient(main.app) as test_client:
        yield test_client


def _seed_pending(
    root: Path, session_id: str, workspace_id: str, *, approval_id: str = "a1"
) -> Path:
    work_dir = root / session_id
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "session_meta.json").write_text(
        json.dumps({"workspace_id": workspace_id}), encoding="utf-8"
    )
    path = work_dir / "pending_approval.json"
    path.write_text(
        json.dumps(
            {
                "approval_id": approval_id,
                "created_at": 1000.0,
                "message": "노드 8의 수요를 조정해줘",
                "language": "ko",
                "signatures": ["sig"],
                "actions": [
                    {
                        "tool": "set_vehicle_input_volume",
                        "params": {"input_no": "113", "volume": 569.41},
                        "summary": "vehicle input 113 -> 569.41",
                    }
                ],
                "warnings": [{"code": "W1", "message": "shared object"}],
                "focus": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# GET /api/calibration/rail
# ---------------------------------------------------------------------------


def test_rail_returns_thirteen_steps_and_never_invents_a_completion(client: TestClient):
    """The screen's skeleton, over HTTP, on the suite's demo dataset.

    The demo network was never calibrated through this product, so the one
    assertion that matters is not which steps are green — it is that a step
    reaching DONE has a named source behind it. A source of ``none`` is the
    manual-area marker and it must never accompany a positive status.
    """
    response = client.get("/api/calibration/rail", headers=admin_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert [step["number"] for step in body["steps"]] == list(range(1, 14))
    for step in body["steps"]:
        if step["status"] in calibration_rail.POSITIVE_STATUSES:
            assert step["source"] != "none", (
                f"step {step['number']} is {step['status']} with no evidence source"
            )
        if step["status"] == calibration_rail.DONE:
            # DONE still means an ARTIFACT answered, so it can never land on a
            # step this product measures nothing for. #53's CONFIRMED_BY_PERSON
            # deliberately CAN (that is the whole point of it), and is checked
            # by the clause above instead: it must still name its source, which
            # for a signature is the attestation store.
            assert step["manual_area"] is False
    assert set(body["sources"]) == {
        "gate",
        "health",
        "phase_diagrams",
        "step_attestations",
        "shadow_model",
        "reality_check",
        "scenario_id",
    }


def test_rail_without_a_scenario_reports_the_run_steps_as_unscored(client: TestClient):
    body = client.get("/api/calibration/rail", headers=admin_headers()).json()
    steps = {step["number"]: step for step in body["steps"]}
    assert body["sources"]["reality_check"] is False
    for number in (10, 11):
        assert steps[number]["status"] == calibration_rail.NO_EVIDENCE


def test_rail_survives_an_unscorable_scenario_instead_of_500ing(client: TestClient):
    """A run id nobody recognises is missing evidence, not a server fault — the
    rail still renders and the other eleven steps still answer."""
    response = client.get(
        "/api/calibration/rail",
        params={"scenario_id": "SIM_does_not_exist"},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["steps"]) == 13


def test_rail_carries_the_guidelines_citation_and_its_ordering_principles(client: TestClient):
    body = client.get("/api/calibration/rail", headers=admin_headers()).json()
    assert body["citation"].startswith("op §2.12.1")
    assert body["principles"]
    for principle in body["principles"]:
        assert principle["text"] and principle["citation"]


# ---------------------------------------------------------------------------
# GET /api/calibration/applied-rows
# ---------------------------------------------------------------------------


def test_applied_rows_reports_verify_applied_rows_verdict_verbatim(client: TestClient):
    """#44's function, over HTTP, for the first time.

    The verdict dict is passed through untouched — including ``why``, which is
    the sentence that names a partially applied set. The route is allowed to
    COUNT the verdicts and nothing else.
    """
    response = client.get("/api/calibration/applied-rows", headers=admin_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["citation"].startswith("op §5.2.5.1")
    for entry in body["behaviors"]:
        verdict = entry["verdict"]
        assert verdict["status"] in saturation_flow.ROW_VERIFICATION_STATUSES
        assert set(verdict) >= {
            "status",
            "applied",
            "row",
            "per_parameter_rows",
            "standstill_matches_guideline",
            "why",
            "citation",
        }
    assert body["summary"]["default_coincident_row_pcu"] == 2050.0


def test_applied_rows_summary_counts_agree_with_the_verdicts_it_returned(client: TestClient):
    body = client.get("/api/calibration/applied-rows", headers=admin_headers()).json()
    if not body["available"]:
        pytest.skip("no readable network in this checkout")
    statuses = [entry["verdict"]["status"] for entry in body["behaviors"]]
    assert body["summary"]["readable"] == sum(
        1 for status in statuses if status != saturation_flow.ROW_UNREADABLE
    )
    assert body["summary"]["on_published_row"] == statuses.count(saturation_flow.ROW_MATCHED)
    assert body["summary"]["off_published_row"] == statuses.count(saturation_flow.ROW_UNMATCHED)


# ---------------------------------------------------------------------------
# GET /api/calibration/reality-check
# ---------------------------------------------------------------------------


def test_reality_check_requires_a_scenario(client: TestClient):
    response = client.get("/api/calibration/reality-check", headers=admin_headers())
    assert response.status_code == 422


def test_reality_check_answers_an_unknown_run_with_a_reason_code_not_an_error(
    client: TestClient,
):
    """``reality_check``'s refusals are DATA. A 404 here would force the screen
    to invent a sentence for a state the service already has words for."""
    response = client.get(
        "/api/calibration/reality-check",
        params={"scenario_id": "SIM_does_not_exist"},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] != "CHECKED"
    assert body["reason_code"]
    for language in ("ko", "en", "ja"):
        assert body["message"][language].strip()


# ---------------------------------------------------------------------------
# GET /api/calibration/pending-approvals
# ---------------------------------------------------------------------------


def test_pending_approvals_lists_this_workspaces_pauses(
    client: TestClient, sessions_root: Path
):
    workspace_id = workspaces.default_workspace_id()
    _seed_pending(sessions_root, "aaaaaaaaaaaa", workspace_id)
    response = client.get("/api/calibration/pending-approvals", headers=admin_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["pending"]) == 1
    entry = body["pending"][0]
    assert entry["session_id"] == "aaaaaaaaaaaa"
    assert entry["approval_id"] == "a1"
    assert entry["actions"][0]["tool"] == "set_vehicle_input_volume"
    assert entry["warnings"][0]["code"] == "W1"
    # Carried so this route and the NDJSON approval_request event have one
    # shape, exactly as the stream's own payload documents.
    assert entry["assumptions"] == []
    assert entry["car_following_set"] is None


def test_listing_a_pending_approval_does_not_spend_it(
    client: TestClient, sessions_root: Path
):
    """The property that makes this route safe to poll.

    The pending file is a single-use token: ``POST /api/assistant/agent/approve``
    deletes it on read, and a replay 404s. A viewer that consumed it would
    silently destroy the approval the human is standing in front of.
    """
    workspace_id = workspaces.default_workspace_id()
    path = _seed_pending(sessions_root, "bbbbbbbbbbbb", workspace_id)
    first = client.get("/api/calibration/pending-approvals", headers=admin_headers()).json()
    second = client.get("/api/calibration/pending-approvals", headers=admin_headers()).json()
    assert first["pending"] == second["pending"]
    assert path.exists(), "listing the pending approval consumed it"


def test_pending_approvals_will_not_show_another_workspaces_pause(
    client: TestClient, sessions_root: Path
):
    """Fail-closed, the same predicate the assistant router uses — imported
    rather than re-derived, because a second implementation of this check is
    the divergence that produced a measured fail-open hole once already."""
    _seed_pending(sessions_root, "cccccccccccc", "some-other-workspace")
    body = client.get("/api/calibration/pending-approvals", headers=admin_headers()).json()
    assert body["pending"] == []


def test_a_sidecar_less_session_created_after_the_backfill_is_denied(
    client: TestClient, sessions_root: Path
):
    """The listing inherits ``session_workspace_matches``' real semantics —
    including the pre-1.4 upgrade path, which is subtler than "no sidecar means
    no access" and is worth having written down where a reader of #50 will see
    it.

    ``_ensure_legacy_sessions_backfilled`` runs once per sessions root and
    claims the sidecar-less population it finds AT THAT MOMENT for the default
    workspace. So an owned session is seeded first and listed once, which fixes
    the legacy set (empty); the sidecar-less session created afterwards is then
    denied — the case that matters, because it is the only one a live system
    can still produce.
    """
    _seed_pending(sessions_root, "eeeeeeeeeeee", workspaces.default_workspace_id())
    seeded = client.get("/api/calibration/pending-approvals", headers=admin_headers()).json()
    assert [entry["session_id"] for entry in seeded["pending"]] == ["eeeeeeeeeeee"]

    work_dir = sessions_root / "dddddddddddd"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "pending_approval.json").write_text(
        json.dumps({"approval_id": "x", "actions": []}), encoding="utf-8"
    )
    body = client.get("/api/calibration/pending-approvals", headers=admin_headers()).json()
    assert [entry["session_id"] for entry in body["pending"]] == ["eeeeeeeeeeee"]


def test_pending_approvals_is_empty_not_an_error_when_nothing_is_waiting(
    client: TestClient, sessions_root: Path
):
    body = client.get("/api/calibration/pending-approvals", headers=admin_headers()).json()
    assert body["pending"] == []
    assert body["workspace_id"]


# ---------------------------------------------------------------------------
# The shape of the router itself
# ---------------------------------------------------------------------------


#: The ONLY non-GET route this router is allowed to carry, and the unit that put
#: it there. #50 shipped a strictly read-only surface; #51's live runner added
#: one control, and this list is what stops a second one arriving unnoticed.
_ALLOWED_MUTATIONS = {
    "/api/calibration/parameter-search/start": "#51 live runner",
    "/api/calibration/step-attestations": "#53 signed human attestation",
    "/api/calibration/step-attestations/{attestation_id}/revoke": "#53 revocation",
}


def test_the_calibration_router_exposes_no_write_verb_but_the_named_controls():
    """Approval STILL goes through the endpoint that already owns it.

    #50's rule was "no write path at all", and it held until 2026-08-21. #51's
    live runner needed exactly one control — a POST that ENQUEUES A JOB and
    writes to no network; the winner it eventually produces still goes through
    ``POST /api/assistant/agent/approve`` with its own identity check. #53 adds
    two more, and they write something a HUMAN said rather than something a
    machine will do: a step attestation and its revocation. Neither can approve
    an edit and neither returns a status — the rail re-derives every step from
    the store on the next read.

    So the rule stays an ALLOWLIST rather than a blanket: a fourth mutating
    route on this router, or an approval leg growing here, fails by name.
    """
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/calibration"):
            continue
        methods = set(getattr(route, "methods", set()))
        if methods <= {"GET", "HEAD"}:
            continue
        assert path in _ALLOWED_MUTATIONS, (
            f"{path} exposes a write verb this surface has not been granted; "
            "the calibration screen approves through /api/assistant/agent/approve"
        )
        assert methods <= {"POST"}, f"{path} may only be a POST"

    # ...and the one that IS allowed is really registered, so the allowlist can
    # never quietly outlive the route it describes.
    registered = {
        getattr(route, "path", "")
        for route in main.app.routes
        if "POST" in set(getattr(route, "methods", set()))
    }
    assert set(_ALLOWED_MUTATIONS) <= registered


def test_the_start_route_refuses_a_body_that_carries_a_verdict(client: TestClient):
    """The gate is server-derived; there is no way to send one in.

    ``extra="forbid"`` on the request model turns an invented ``preconditions``
    into a 422 at the door rather than a field that silently does nothing — a
    caller must be able to tell the difference.
    """
    response = client.post(
        "/api/calibration/parameter-search/start",
        json={"scenario_id": "SIM_x", "preconditions": {"ok": True}},
        headers=admin_headers(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/calibration/step-attestations (#53)
#
# The one write on this router that records something a HUMAN said. The route's
# whole job is to make the record accountable, so every test here is about a
# way it could stop being: an anonymous signature, a name from the body, a
# status decided at the POST, or a signature on a step a machine already judges.
# ---------------------------------------------------------------------------


@pytest.fixture()
def network(workspace_root: Path) -> str:
    """A tiny active ``.inpx`` for this workspace — the bytes a signature binds
    to. Written rather than mocked, because the sha the store stamps has to be
    the sha of a file that really exists for the staleness rule to mean
    anything."""
    from services import datasets

    path = datasets.active_inpx_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<network>calibration endpoint test</network>")
    return str(path)


def _sign(client: TestClient, *, step: int = 2, reason: str = "현장에서 확인함", **kwargs):
    return client.post(
        "/api/calibration/step-attestations",
        json={"step_number": step, "reason": reason},
        headers=kwargs.pop("headers", admin_headers()),
        **kwargs,
    )


def test_an_anonymous_caller_cannot_sign_for_a_step(client: TestClient, network: str):
    """A confirmation nobody can be named for is not one — so it is a 401
    before any of the store's own rules are consulted."""
    response = client.post(
        "/api/calibration/step-attestations",
        json={"step_number": 2, "reason": "현장에서 확인함"},
    )
    assert response.status_code == 401


def test_an_anonymous_caller_cannot_revoke_either(client: TestClient, network: str):
    created = _sign(client).json()
    response = client.post(
        f"/api/calibration/step-attestations/{created['id']}/revoke",
        json={"reason": "아니었음"},
    )
    assert response.status_code == 401
    # And the claim still stands, which is the point: an anonymous caller must
    # not be able to strip a named person's signature off a project.
    rail = client.get("/api/calibration/rail", headers=admin_headers()).json()
    step = next(item for item in rail["steps"] if item["number"] == 2)
    assert step["status"] == calibration_rail.CONFIRMED_BY_PERSON


def test_the_attester_comes_from_the_session_and_a_body_cannot_claim_one(
    client: TestClient, network: str
):
    """``extra="forbid"`` means an invented ``attested_by`` is a 422 at the
    door, not a field that quietly does nothing — the same door
    ``ParameterSearchStartRequest`` closes on an invented gate verdict."""
    for forged in ({"attested_by": "somebody-else"}, {"attested_at": "1999-01-01T00:00:00Z"}):
        response = client.post(
            "/api/calibration/step-attestations",
            json={"step_number": 2, "reason": "현장에서 확인함", **forged},
            headers=admin_headers(),
        )
        assert response.status_code == 422, forged

    recorded = _sign(client).json()
    assert recorded["attested_by"] == "suite-admin"
    assert recorded["attested_at"].startswith("20")


def test_the_post_records_the_claim_and_decides_no_status(client: TestClient, network: str):
    """The response carries the RECORD and no status word at all.

    A status here would be a second place a step's state is decided; the rail
    re-derives it from the store on the next read, and that read is what the
    screen shows.
    """
    body = _sign(client).json()
    assert set(body) >= {"id", "step_number", "attested_by", "attested_at", "reason", "stale"}
    assert "status" not in body
    assert body["stale"] is False

    rail = client.get("/api/calibration/rail", headers=admin_headers()).json()
    step = next(item for item in rail["steps"] if item["number"] == 2)
    assert step["status"] == calibration_rail.CONFIRMED_BY_PERSON
    assert step["source"] == "calibration_attestation"
    assert step["attestation"]["id"] == body["id"]
    assert rail["sources"]["step_attestations"] is True


def test_an_empty_reason_is_a_named_refusal_not_a_bare_400(client: TestClient, network: str):
    response = _sign(client, reason="   ")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason_code"] == "calibration_attestation_reason_required"
    assert set(detail["message"]) == {"ko", "en", "ja"}


@pytest.mark.parametrize("step", [1, 6, 7, 12])
def test_a_step_with_machine_evidence_is_refused_by_name(
    client: TestClient, network: str, step: int
):
    response = _sign(client, step=step)
    assert response.status_code == 400
    assert response.json()["detail"]["reason_code"] == "calibration_attestation_step_not_attestable"


def test_revoking_returns_the_withdrawn_record_and_the_rail_falls_back(
    client: TestClient, network: str
):
    created = _sign(client).json()
    revoked = client.post(
        f"/api/calibration/step-attestations/{created['id']}/revoke",
        json={"reason": "다시 보니 아니었음"},
        headers=admin_headers(),
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True
    assert [event["event"] for event in revoked.json()["history"]] == ["attested", "revoked"]

    rail = client.get("/api/calibration/rail", headers=admin_headers()).json()
    step = next(item for item in rail["steps"] if item["number"] == 2)
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert step["attestation"] is None


def test_revoking_requires_a_reason_on_the_body(client: TestClient, network: str):
    created = _sign(client).json()
    response = client.post(
        f"/api/calibration/step-attestations/{created['id']}/revoke",
        json={},
        headers=admin_headers(),
    )
    assert response.status_code == 422


def test_revoking_twice_is_a_named_409_over_http(client: TestClient, network: str):
    """The store's refusal reaches the caller as its own status and code.

    AUTHORITY: the refuter, 2026-08-24 — all four of these paths were probed
    functionally and found correct, and NONE of them had a test. A refusal that
    is right today and reachable only through a mapping nobody pins is a 500
    (or a silent 200) one refactor away, and the whole point of a named refusal
    is that a UI can render the sentence rather than translate a status code.
    """
    created = _sign(client).json()
    first = client.post(
        f"/api/calibration/step-attestations/{created['id']}/revoke",
        json={"reason": "아니었음"},
        headers=admin_headers(),
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/calibration/step-attestations/{created['id']}/revoke",
        json={"reason": "또 아니었음"},
        headers=admin_headers(),
    )
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["reason_code"] == "calibration_attestation_already_revoked"
    assert set(detail["message"]) == {"ko", "en", "ja"}


@pytest.mark.parametrize("attestation_id", ["deadbeefcafe", "not-an-id", "0" * 32])
def test_revoking_an_unknown_attestation_is_a_named_404(
    client: TestClient, network: str, attestation_id: str
):
    """404 and not 400, for all three shapes of "no such record".

    A malformed id and an id that simply is not here must be indistinguishable:
    telling the two apart would let a caller enumerate which ids exist in a
    project they cannot otherwise read.
    """
    response = client.post(
        f"/api/calibration/step-attestations/{attestation_id}/revoke",
        json={"reason": "x"},
        headers=admin_headers(),
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["reason_code"] == "calibration_attestation_not_found"


def test_an_over_long_reason_is_a_named_422_on_both_acts(client: TestClient, network: str):
    """Refused, never truncated — a signature whose stated reason was silently
    cut in half is a claim its author did not make."""
    too_long = "가" * (calibration_attestation.MAX_REASON_CHARS + 1)

    signing = _sign(client, reason=too_long)
    assert signing.status_code == 422
    assert signing.json()["detail"]["reason_code"] == "calibration_attestation_reason_too_long"

    created = _sign(client).json()
    revoking = client.post(
        f"/api/calibration/step-attestations/{created['id']}/revoke",
        json={"reason": too_long},
        headers=admin_headers(),
    )
    assert revoking.status_code == 422
    assert revoking.json()["detail"]["reason_code"] == "calibration_attestation_reason_too_long"
    # And the claim still stands: a refused revocation withdrew nothing.
    rail = client.get("/api/calibration/rail", headers=admin_headers()).json()
    step = next(item for item in rail["steps"] if item["number"] == 2)
    assert step["status"] == calibration_rail.CONFIRMED_BY_PERSON


def test_a_whitespace_only_reason_on_a_REVOCATION_is_refused(client: TestClient, network: str):
    """``{"reason": "   "}`` clears pydantic (it is a string) and is caught by
    the store's own strip — so this is the case a body-shape check alone would
    let through, on the act that REMOVES a named person's claim."""
    created = _sign(client).json()
    response = client.post(
        f"/api/calibration/step-attestations/{created['id']}/revoke",
        json={"reason": "   \n\t "},
        headers=admin_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "calibration_attestation_reason_required"
    assert set(response.json()["detail"]["message"]) == {"ko", "en", "ja"}
    # Nothing was withdrawn.
    rail = client.get("/api/calibration/rail", headers=admin_headers()).json()
    step = next(item for item in rail["steps"] if item["number"] == 2)
    assert step["status"] == calibration_rail.CONFIRMED_BY_PERSON
    assert step["attestation"]["id"] == created["id"]


def test_a_signature_does_not_leak_into_another_workspace(
    client: TestClient, network: str, workspace_root: Path
):
    """The rail is workspace-scoped, and so is the signature behind it."""
    other = workspaces.create_workspace("다른 프로젝트")
    _sign(client)
    rail = client.get(
        "/api/calibration/rail",
        headers={**admin_headers(), "X-Workspace-Id": other["id"]},
    ).json()
    step = next(item for item in rail["steps"] if item["number"] == 2)
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert step["attestation"] is None

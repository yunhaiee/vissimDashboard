"""Unit #51 (live runner) — a search that a human can START and the product REMEMBERS.

What was missing before this unit, in one sentence: ``parameter_search`` could run a
search only from a hand-built harness, so ``runner_requirement()['wired']`` was False
and every downstream unit's tests had to hand the broker a search a test had built.
These tests are about the seam that closes — ``POST /api/calibration/parameter-search/
start`` → a job thread → the engine → ``accept_winner`` → ``propose`` →
``searched_behaviour_broker.remember`` → a later product turn that finds it.

WHAT IS REAL HERE AND WHAT IS FAKED
-----------------------------------
Real: the route, its server-side gate derivation, ``calibration_rail.derive_rail``,
``parameter_search``'s engine/holdout/proposal, the broker, the assistant turn, the
write gate and the constraint engine.

Faked, both at boundaries this repo already fakes: the LLM provider (a canned action
queue) and the RUNNER — ``parameter_search.SyntheticRunner`` over the same known GEH
surface ``test_agent_parameter_search`` searches. **No live COM runs in this file and
none should.** ``VissimSearchRunner``'s COM sequence is thin glue over
``routers/simulations._run_agent_simulation_job_locked``'s measured discipline and is
covered by inspection plus the live acceptance run; what IS covered here is every
decision that is not a COM call — the availability probe, the None-preserving GEH
extraction, the budget clamp, the named refusals, the workspace binding and the
remember/propose seam.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from routers import calibration as calibration_endpoint
from services import workspaces
from services.agent import car_following_broker
from services.agent import parameter_search as ps
from services.agent import search_job, search_runner
from services.agent import searched_behaviour_broker as broker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_agent_parameter_search import (  # noqa: E402
    APPLIED_OK,
    BASELINE,
    NODES,
    _report,
    _surface,
)
from test_agent_searched_product_path import (  # noqa: E402
    BEHAVIOR_NO,
    CONSENTED,
    DEMO_INPX,
    SHARED_OBJECT,
    FakeStructuredProvider,
    _events,
    _respond,
    _target_behavior,
    _turn,
    _write_action,
)

#: The same reference graph #51-c injects (three link-behavior-types on behaviour 2, so
#: the consent radius is COMPLETE and the exception is available at all), with ONE
#: difference: the three searched parameters start at ``BASELINE`` rather than at values
#: the published bands would clip. The clip is real and correct; pinning the start point
#: here just makes this file's search the SAME search the #51-b fixture proved, so a
#: failure means the wiring broke rather than that a start point drifted.
_INJECTED = """  <linkBehaviorTypes>
    <linkBehaviorType no="10" name="Urban arterial" drivBehavDef="2"/>
    <linkBehaviorType no="11" name="Urban collector" drivBehavDef="2"/>
    <linkBehaviorType no="12" name="Urban local" drivBehavDef="2"/>
    <linkBehaviorType no="13" name="Freeway" drivBehavDef="3"/>
  </linkBehaviorTypes>
  <drivingBehaviors>
    <drivingBehavior no="2" name="Urban (motorized)" carFollowModType="WIEDEMANN74" \
w74ax="2" w74bxAdd="2" w74bxMult="3" safDistFactLnChg="0.25" latDistDrivDef="0.2" \
latDistStandDef="0.1"/>
  </drivingBehaviors>
</network>"""

#: A second searchable behaviour, so "which one did you mean" has a real answer to
#: refuse over.
_INJECTED_TWO = _INJECTED.replace(
    "  </drivingBehaviors>",
    '    <drivingBehavior no="3" name="Freeway (free flow)" carFollowModType="WIEDEMANN74"'
    ' w74ax="2" w74bxAdd="2" w74bxMult="3" safDistFactLnChg="0.25" latDistDrivDef="0.2"'
    ' latDistStandDef="0.1"/>\n  </drivingBehaviors>',
)

SCENARIO_ID = "SIM_test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate():
    """Both broker species and the job registry, so a leak anywhere is visible."""
    broker.reset()
    car_following_broker.reset()
    search_job.reset()
    yield
    broker.reset()
    car_following_broker.reset()
    search_job.reset()


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _network(tmp_path: Path, injected: str) -> Path:
    text = DEMO_INPX.read_text(encoding="utf-8")
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "calibration.inpx"
    path.write_text(text.replace("</network>", injected), encoding="utf-8")
    return path


@pytest.fixture()
def network(tmp_path: Path) -> Path:
    path = _network(tmp_path, _INJECTED)
    target = _target_behavior(path)
    assert target["reference_count"] == 3, "fixture: the radius must be nameable in full"
    for parameter in ps.SEARCHABLE_PARAMETERS:
        assert target[parameter] == BASELINE[parameter]
    return path


def _synthetic_factory(cost_s: float = 30.0, *, seen: list[str] | None = None):
    """A runner factory the job can be given instead of the COM one.

    ``seen`` collects the workspace bound on the thread at the moment a candidate
    runs — the property that would silently break if the job stopped re-binding it
    (ContextVars do not cross ``threading.Thread`` boundaries).
    """

    def factory(plan, work_dir, on_candidate):
        runner = ps.SyntheticRunner(surface=_surface, cost_s=cost_s)
        inner = runner.run

        def run(vector, seed, node_ids):
            if seen is not None:
                seen.append(workspaces.current_workspace_id())
            outcome = inner(vector, seed, node_ids)
            on_candidate(
                {
                    "index": runner.calls,
                    "seed": seed,
                    "params": dict(vector),
                    "ok": outcome.ok,
                    "reason": outcome.reason,
                    "elapsed_s": outcome.elapsed_s,
                }
            )
            return outcome

        runner.run = run  # type: ignore[method-assign]
        return runner

    return factory


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch, network: Path):
    """Everything the route reads, pointed at evidence that OPENS the gate.

    Each patch replaces one gathering helper on the router — the same seams
    ``routers/calibration.py`` uses to read artifacts — never the gate itself:
    ``search_job.evaluate_start`` and ``parameter_search.evaluate_preconditions``
    run for real over what these return, which is the whole point.
    """
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: network)
    monkeypatch.setattr(calibration_endpoint, "get_active_inpx_path", lambda: network)
    monkeypatch.setattr(calibration_endpoint, "_gate_for_active_dataset", lambda language: None)
    monkeypatch.setattr(calibration_endpoint, "_health_report", lambda: None)
    # Step ⑥ DONE, from the rail's OWN deriver over a confirmed 현시도 review —
    # not by patching the rail, so SIGNAL_STEP_UNVERIFIED is really cleared.
    monkeypatch.setattr(
        calibration_endpoint, "_phase_diagram_index", lambda: [{"confirmed": True}]
    )
    monkeypatch.setattr(calibration_endpoint, "_applied_rows", lambda shadow: APPLIED_OK)
    monkeypatch.setattr(
        calibration_endpoint,
        "_reality_check",
        lambda scenario_id, **kwargs: _report() if scenario_id else None,
    )
    # This box may or may not carry pywin32; the tests must mean the same thing on
    # the Windows dev box and the Mac laptop, so availability is pinned.
    monkeypatch.setattr(search_job, "runner_availability", lambda: (True, None))
    return network


def _open_session(client, monkeypatch) -> str:
    events = _turn(client, monkeypatch, [_respond("네.")], message="상태를 알려 주세요")
    return events[-1]["response"]["session_id"]


def _start(client, session_id: str | None = None, **body: Any):
    payload: dict[str, Any] = {"scenario_id": SCENARIO_ID, **body}
    if session_id:
        payload["session_id"] = session_id
    return client.post("/api/calibration/parameter-search/start", json=payload)


def _await_job(job_id: str, *, timeout_s: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        record = search_job.get_job(job_id)
        assert record is not None, "the job record vanished while the thread ran"
        if record["status"] not in (search_job.QUEUED, search_job.RUNNING):
            return record
        time.sleep(0.02)
    raise AssertionError(f"parameter-search job {job_id} did not finish within {timeout_s}s")


def _codes(response) -> set[str]:
    return {item["code"] for item in response.json()["refusals"]}


# ===========================================================================
# 1. The gate is the SERVER's, and the client cannot send one
# ===========================================================================


def test_a_body_carrying_a_verdict_is_refused_at_the_door(client, monkeypatch, wired):
    """``extra="forbid"`` is load-bearing: an invented gate is a 422, not a no-op field.

    A field the model merely IGNORED would be worse than one it rejects — the caller
    would have no way to tell that its ``preconditions`` did nothing.
    """
    for forged in ({"preconditions": {"ok": True}}, {"ok": True}, {"force": True}):
        response = client.post(
            "/api/calibration/parameter-search/start",
            json={"scenario_id": SCENARIO_ID, **forged},
        )
        assert response.status_code == 422, forged


def test_the_gate_is_re_derived_from_server_evidence_not_from_the_request(
    client, monkeypatch, wired
):
    """A perfectly-formed request over evidence that REFUSES still refuses, by name.

    MUTATION: a route that trusted anything the caller said about preconditions —
    or that cached the last ``/rail`` answer instead of re-deriving — passes the
    happy path below and fails here.
    """
    session_id = _open_session(client, monkeypatch)
    monkeypatch.setattr(calibration_endpoint, "_applied_rows", lambda shadow: [])

    response = _start(client, session_id)
    assert response.status_code == 200
    body = response.json()
    assert body["started"] is False and body["job"] is None
    assert ps.TABLE_5_6_NOT_APPLIED in _codes(response)


def test_every_refusal_is_collected_not_short_circuited(client, monkeypatch, wired):
    """Three doors shut at once names all three — ``evaluate_preconditions``'s rule,
    kept across the two refusal families this route merges."""
    monkeypatch.setattr(calibration_endpoint, "_applied_rows", lambda shadow: [])
    monkeypatch.setattr(search_job, "runner_availability", lambda: (False, "no pywin32 here"))

    codes = _codes(_start(client))
    assert {
        search_job.RUNNER_NOT_WIRED,
        search_job.NO_AGENT_SESSION,
        ps.TABLE_5_6_NOT_APPLIED,
    } <= codes


def test_a_scored_run_is_required_before_anything(client, monkeypatch, wired):
    monkeypatch.setattr(calibration_endpoint, "_reality_check", lambda scenario_id, **kw: None)
    assert ps.NO_REALITY_CHECK in _codes(_start(client))


# ===========================================================================
# 2. Start refusals this unit owns
# ===========================================================================


def test_a_start_with_no_conversation_is_refused_by_name(client, monkeypatch, wired):
    """An hour of Vissim whose winner nothing can propose is refused BEFORE the hour.

    The broker is keyed by session and ``remember`` answers ``None`` without one, so
    a search started with no session would run to completion and then evaporate.
    """
    response = _start(client)
    assert response.status_code == 200
    assert response.json()["started"] is False
    assert search_job.NO_AGENT_SESSION in _codes(response)
    for language in ("ko", "en", "ja"):
        sentence = next(
            item["why"][language]
            for item in response.json()["refusals"]
            if item["code"] == search_job.NO_AGENT_SESSION
        )
        assert sentence.strip()


def test_a_session_this_workspace_does_not_own_is_refused(client, monkeypatch, wired):
    """Fail-closed, through the ONE ownership predicate. A session id that names
    nothing is indistinguishable from one that names another project's."""
    assert search_job.SESSION_NOT_IN_WORKSPACE in _codes(_start(client, "0" * 12))


def test_an_unwired_machine_refuses_rather_than_pretending(client, monkeypatch, wired):
    monkeypatch.setattr(search_job, "runner_availability", lambda: (False, "not Windows"))
    session_id = _open_session(client, monkeypatch)
    codes = _codes(_start(client, session_id))
    assert search_job.RUNNER_NOT_WIRED in codes


def test_several_searchable_behaviours_are_a_question_not_a_guess(
    client, monkeypatch, tmp_path, wired
):
    """The system does not pick one of several behaviours on a modeller's behalf.

    Every candidate run and the final write inherit that choice, so a guess here is a
    guess in every number the search produces.
    """
    two = _network(tmp_path / "two", _INJECTED_TWO)
    monkeypatch.setattr(calibration_endpoint, "get_active_inpx_path", lambda: two)
    session_id = _open_session(client, monkeypatch)

    response = _start(client, session_id)
    assert search_job.BEHAVIOUR_NOT_CHOSEN in _codes(response)
    offered = {item["no"] for item in response.json()["behaviours"]}
    assert offered == {"2", "3"}, "the refusal must NAME what could have been picked"

    # ...and naming one starts it.
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory())
    started = _start(client, session_id, behavior_no="3")
    assert started.json()["started"] is True
    assert started.json()["job"]["behavior_no"] == "3"
    _await_job(started.json()["job"]["id"])


def test_a_behaviour_that_is_not_there_is_named(client, monkeypatch, wired):
    session_id = _open_session(client, monkeypatch)
    assert search_job.BEHAVIOUR_NOT_FOUND in _codes(_start(client, session_id, behavior_no="99"))


def test_one_search_at_a_time(client, monkeypatch, wired):
    """One Vissim, one search. A second start while one runs is a named refusal."""
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s=1.0))
    session_id = _open_session(client, monkeypatch)

    first = _start(client, session_id)
    assert first.json()["started"] is True
    job_id = first.json()["job"]["id"]
    second = _start(client, session_id)
    if second.json()["started"] is False:
        assert search_job.SEARCH_ALREADY_RUNNING in _codes(second)
    _await_job(job_id)


def _bare_plan(tmp_path: Path, *, workspace_id: str) -> search_job.StartPlan:
    return search_job.StartPlan(
        preconditions={"ok": True, "split": {"calibration": NODES[:4], "holdout": NODES[4:]}},
        behaviour={"no": "2", "name": "Urban", **BASELINE},
        baseline_vector=dict(BASELINE),
        scoring=search_runner.ScoringContext(),
        budget_s=60.0,
        budget_clamped=False,
        duration_s=600,
        duration_clamped=False,
        session_id="s",
        workspace_id=workspace_id,
        source_inpx=tmp_path / "nope.inpx",
        scenario_id=None,
    )


def test_a_thread_that_dies_before_it_starts_still_leaves_a_status(monkeypatch, tmp_path):
    """MEASURED 2026-08-21: a job that never reached a terminal status sat at
    QUEUED forever, and because ``_claim`` treats QUEUED as live it wedged that
    workspace's start button permanently — one malformed id and the feature is
    gone until the process restarts.

    MUTATION: moving the workspace bind outside a handler reproduces it exactly,
    and this test hangs on the very ``_claim`` it is checking.
    """
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s=1.0))
    plan = _bare_plan(tmp_path, workspace_id="not a workspace id")
    record = search_job.start(plan, work_root=tmp_path / "work")
    assert record is not None
    finished = _await_job(record["id"], timeout_s=10.0)
    assert finished["status"] == search_job.FAILED
    assert "WorkspaceNotFoundError" in (finished["error"] or "")
    # ...and the door is open again, which is the half that actually matters.
    assert search_job.start(_bare_plan(tmp_path, workspace_id="not a workspace id")) is not None


def test_a_thread_that_never_starts_does_not_wedge_the_workspace(monkeypatch, tmp_path):
    """THE REFUTER'S SECOND REPRODUCTION, 2026-08-21 — the sibling of the QUEUED wedge.

    ``_claim`` commits the QUEUED record BEFORE ``thread.start()``. A
    ``RuntimeError("can't start new thread")`` (thread exhaustion) therefore used
    to leave QUEUED in ``_JOBS`` with nothing that would ever advance it — and
    since ``_claim`` treats QUEUED as live, that workspace's start button was
    dead until the process restarted, over a screen reading "search running"
    forever about a thread that never existed.

    The half that actually matters is the LAST assertion: the door opens again.
    """
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s=1.0))

    def cannot_start(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(search_job.threading.Thread, "start", cannot_start)
    workspace = workspaces.default_workspace_id()
    record = search_job.start(_bare_plan(tmp_path, workspace_id=workspace), work_root=tmp_path / "w")

    assert record is not None
    assert record["status"] == search_job.FAILED
    assert record["terminal_reason"] == search_job.JOB_NOT_STARTED
    assert "can't start new thread" in (record["error"] or "")
    assert search_job.job_card(record)["live"] is False

    # THE WEDGE: with threads working again, a new search must be startable.
    monkeypatch.undo()
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s=1.0))
    again = search_job.start(_bare_plan(tmp_path, workspace_id=workspace), work_root=tmp_path / "w2")
    assert again is not None and again["status"] == search_job.QUEUED
    _await_job(again["id"], timeout_s=30.0)


def test_the_route_reports_a_job_that_never_started_as_not_started(client, monkeypatch, wired):
    """``started: true`` over a thread that does not exist is the worse half of
    that failure — the screen would poll a search nobody is running."""
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s=1.0))
    session_id = _open_session(client, monkeypatch)

    def cannot_start(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(search_job.threading.Thread, "start", cannot_start)
    response = _start(client, session_id)
    assert response.status_code == 200
    body = response.json()
    assert body["started"] is False
    assert search_job.JOB_NOT_STARTED in _codes(response)
    assert body["job"]["status"] == search_job.FAILED and body["job"]["live"] is False


def test_the_claim_is_atomic_not_merely_checked(monkeypatch, tmp_path):
    """The check that MATTERS happens under the registry's own lock.

    ``evaluate_start`` also asks "is one running?", but two POSTs arriving
    together would both find nothing and both enqueue — and a second Vissim
    instance driven from one process is the configuration this codebase has never
    verified. So this drives ``start`` directly, twice, with no gate in between:
    the second claim must come back ``None``.

    MUTATION: an insert that does not check inside the lock returns a second
    record here and this fails.
    """
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s=1.0))
    plan = _bare_plan(tmp_path, workspace_id=workspaces.default_workspace_id())
    first = search_job.start(plan, work_root=tmp_path / "work")
    assert first is not None
    assert search_job.start(plan, work_root=tmp_path / "work") is None
    _await_job(first["id"], timeout_s=30.0)

    # ...and once it is finished, the door opens again.
    assert search_job.start(plan, work_root=tmp_path / "work") is not None


# ===========================================================================
# 3. The budget is CLAMPED, in both directions
# ===========================================================================


@pytest.mark.parametrize(
    "requested,expected",
    [
        (10, search_job.MIN_BUDGET_S),
        (10_000_000, search_job.MAX_BUDGET_S),
        (None, ps.DEFAULT_BUDGET_S),
    ],
)
def test_the_budget_is_clamped_to_sane_bounds(requested, expected):
    budget, clamped = search_job.clamp_budget_s(requested)
    assert budget == expected
    assert clamped is (requested is not None and float(requested) != expected)


def test_an_unreadable_budget_is_the_default_never_an_error():
    for value in ("nonsense", float("nan"), float("inf"), None):
        assert search_job.clamp_budget_s(value)[0] == ps.DEFAULT_BUDGET_S


def test_the_route_clamps_what_it_enqueues(client, monkeypatch, wired):
    """MUTATION: a route that passed ``budget_s`` through unclamped hands the engine
    a ceiling of days, and this is where that shows."""
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s=1.0))
    session_id = _open_session(client, monkeypatch)

    response = _start(client, session_id, budget_s=10_000_000)
    body = response.json()
    assert body["started"] is True
    assert body["budget"]["clamped"] is True
    assert body["job"]["budget_s"] == search_job.MAX_BUDGET_S
    assert search_job.get_job(body["job"]["id"])["budget_s"] == search_job.MAX_BUDGET_S
    _await_job(body["job"]["id"])


# ===========================================================================
# 4. THE SEAM: a search this product ran, remembered, and proposed
# ===========================================================================


def _finished_search(client, monkeypatch, *, cost_s: float = 30.0, seen=None):
    monkeypatch.setattr(search_job, "runner_factory", _synthetic_factory(cost_s, seen=seen))
    session_id = _open_session(client, monkeypatch)
    response = _start(client, session_id)
    assert response.json()["started"] is True, response.json()
    record = _await_job(response.json()["job"]["id"])
    return session_id, record


def test_a_finished_search_is_remembered_and_the_next_turn_finds_it(
    client, monkeypatch, wired
):
    """THE UNIT, end to end, with the REAL job thread — never a direct remember() call.

    Route → thread → engine → accept_winner → propose → broker.remember → a later
    product turn whose write the broker recognises, with a live re-minted grant on
    the approval card.
    """
    session_id, record = _finished_search(client, monkeypatch)

    assert record["status"] == search_job.COMPLETE
    assert record["search_status"] == ps.STATUS_COMPLETE
    assert record["acceptance_code"] == ps.HOLDOUT_PASSED
    assert record["remembered"] is True and record["search_id"]
    assert record["candidates_evaluated"] > 0
    assert record["seed"] == search_job.SEARCH_SEED
    assert record["holdout_seed"] == search_job.HOLDOUT_SEED

    # The broker holds it — in THIS session, keyed by the id the job reported.
    remembered = broker.recall(record["search_id"], session_id=session_id)
    assert remembered is not None
    assert remembered["result"]["status"] == ps.STATUS_COMPLETE

    # ...and a product turn that proposes one of its writes gets the grant.
    params = dict(remembered["proposal"]["actions"][0]["params"])
    assert params["behavior_no"] == BEHAVIOR_NO
    events = _turn(
        client,
        monkeypatch,
        [_write_action(params)],
        session_id=session_id,
        message="탐색 승자를 적용해 주세요",
    )
    card = next(event for event in events if event["type"] == "approval_request")
    constraint_ids = {warning["constraint_id"] for warning in card["warnings"]}
    assert CONSENTED in constraint_ids
    assert SHARED_OBJECT not in constraint_ids
    assert card["searched_search"]["search_id"] == record["search_id"]
    assert card["searched_search"]["grant"]["reason"] == broker.GRANTED


def test_the_job_thread_runs_inside_the_submitting_workspace(client, monkeypatch, wired):
    """ContextVars do not cross threads. Every candidate must still run in the
    project that asked for the search — the trap ``_run_agent_simulation_job``
    documents at its own seam."""
    seen: list[str] = []
    expected = workspaces.default_workspace_id()
    _finished_search(client, monkeypatch, seen=seen)
    assert seen and set(seen) == {expected}


def test_the_winner_is_snapshotted_after_the_holdout_ran(client, monkeypatch, wired):
    """``accept_winner`` appends the two HOLDOUT rows to the LIVE state, and
    ``grant_from_search`` re-derives acceptance FROM the ledger — so a result
    captured before acceptance carries a ledger that cannot support the grant.

    MUTATION: snapshotting ``search.result()`` before ``accept_winner`` leaves the
    broker with nothing to remember, and ``remembered`` comes back False.
    """
    session_id, record = _finished_search(client, monkeypatch)
    remembered = broker.recall(record["search_id"], session_id=session_id)
    ledger = remembered["result"]["state"]["ledger"]
    holdout_rows = [row for row in ledger if row["node_set"] == "HOLDOUT"]
    assert len(holdout_rows) == 2
    assert {row["seed"] for row in holdout_rows} == {search_job.HOLDOUT_SEED}


# ===========================================================================
# 5. A PARTIAL search reaches nothing that can mint
# ===========================================================================


def test_a_budget_exhausted_search_never_reaches_the_broker(client, monkeypatch, wired):
    """The invariant #51-b's docstring names: no file is an input to minting, and a
    partial search is not remembered at all.

    TWO LAYERS, asserted separately on purpose. ``grant_from_search`` already
    refuses a non-COMPLETE status, so a job that DID call ``remember`` on a
    partial search would still store nothing — which means an outcome-only test
    cannot tell "this unit does not try" from "the layer below said no".
    MEASURED 2026-08-21: adding a ``_remember`` call to the partial branch left
    every outcome assertion below green. So the call itself is spied on, and the
    outcome is checked as well; the belt is not evidence about the braces.
    """
    calls: list[Any] = []
    real_remember = broker.remember
    monkeypatch.setattr(
        broker,
        "remember",
        lambda **kwargs: (calls.append(kwargs), real_remember(**kwargs))[1],
    )
    session_id, record = _finished_search(client, monkeypatch, cost_s=2500.0)

    assert calls == [], "a partial search must not be offered to the broker at all"
    assert record["status"] == search_job.PARTIAL
    assert record["search_status"] == ps.STATUS_PARTIAL_BUDGET_EXHAUSTED
    assert record["remembered"] is False and record["search_id"] is None

    # Nothing in this session, under any id.
    assert broker.recall(record["search_id"], session_id=session_id) is None
    assert broker.search_id_covering(
        {"tool": ps.SEARCH_TOOL, "params": {"behavior_no": BEHAVIOR_NO}},
        session_id=session_id,
    ) is None

    # ...so the ordinary shared-object refusal is what a write meets.
    events = _turn(
        client,
        monkeypatch,
        [
            _write_action(
                {"behavior_no": BEHAVIOR_NO, "parameter": "saf_dist_fact_ln_chg", "value": 0.34}
            )
        ],
        session_id=session_id,
        message="적용해 주세요",
    )
    card = next((event for event in events if event["type"] == "approval_request"), None)
    if card is not None:
        assert card["searched_search"] is None
        assert CONSENTED not in {w["constraint_id"] for w in card["warnings"]}


def test_the_partial_state_is_audit_only_and_says_so(client, monkeypatch, wired):
    """The state IS written, and its NAME says what it is not.

    Resume-from-disk is a NAMED non-feature: the engine supports it, this unit
    deliberately does not, and nothing reads the artifact back.
    """
    _, record = _finished_search(client, monkeypatch, cost_s=2500.0)
    path = Path(record["partial_state_path"])
    assert path.name == "state.PARTIAL.NOT-MINTABLE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["resume_from_disk"]["supported"] is False
    assert payload["NOT_MINTABLE"].strip()
    assert search_job.RESUME_FROM_DISK["supported"] is False


# ===========================================================================
# 6. The runner's own decisions — the ones that are not COM calls
# ===========================================================================


def test_an_ungraded_node_stays_none_and_never_becomes_a_passing_zero():
    """GEH 0 is the BEST obtainable score, so a missing statistic rendered as 0.0
    would put an ungradable node at the front of ``best()`` and propose a write on
    evidence that does not exist.

    MUTATION: ``measured.get(node, 0.0)`` passes every other test in this file.
    """
    report = {
        "status": "CHECKED",
        "nodes": [
            {"node_id": "3", "node_total_geh": 4.5},
            {"node_id": "4", "node_total_geh": None},
            {"node_id": "5"},
            {"node_id": "6", "node_total_geh": "not a number"},
        ],
    }
    values = search_runner.geh_by_node(report, ["3", "4", "5", "6", "7"])
    assert values == {"3": 4.5, "4": None, "5": None, "6": None, "7": None}
    # Spelled out as well as compared: `{"4": 0.0} == {"4": False}` is True in
    # Python, so an equality assertion alone is not proof that no zero crept in.
    assert [node for node, value in values.items() if value is None] == ["4", "5", "6", "7"]


def test_an_ungradable_report_is_a_named_refusal_not_a_score():
    assert search_runner.report_refusal({"status": "CHECKED", "nodes": []}) is None
    assert search_runner.report_refusal({"nodes": []}) is None
    refusal = search_runner.report_refusal(
        {"status": "NOT_CHECKABLE", "reason_code": "RUN_NOT_FOUND"}
    )
    assert refusal and refusal.startswith(search_runner.SCORE_NOT_CHECKABLE)
    assert "RUN_NOT_FOUND" in refusal


def test_the_runner_refuses_before_any_com_when_the_machine_cannot(monkeypatch, tmp_path):
    """The first thing ``run`` does is ask whether this machine can run Vissim at
    all, and a "no" is a named outcome rather than an exception."""
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (False, "no pywin32"))
    runner = search_runner.VissimSearchRunner(
        source_inpx=tmp_path / "missing.inpx",
        work_dir=tmp_path / "work",
        behavior_no=2,
        duration_s=600,
    )
    outcome = runner.run(BASELINE, 42, NODES)
    assert outcome.ok is False
    assert outcome.reason.startswith(search_runner.RUNNER_NOT_AVAILABLE)
    assert outcome.geh_by_node == {}


def test_a_missing_source_network_is_named_not_crashed(monkeypatch, tmp_path):
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (True, None))
    runner = search_runner.VissimSearchRunner(
        source_inpx=tmp_path / "missing.inpx",
        work_dir=tmp_path / "work",
        behavior_no=2,
        duration_s=600,
    )
    outcome = runner.run(BASELINE, 42, NODES)
    assert outcome.ok is False
    assert outcome.reason.startswith(search_runner.SOURCE_NETWORK_MISSING)


# ---------------------------------------------------------------------------
# 6b. The runner's FILESYSTEM and SQLITE halves — offline, and really executed
#
# Added 2026-08-21 after the refuter demonstrated that the module docstring
# claimed coverage this file did not have. Every test below runs the REAL method
# against a real temp directory; only ``_drive_vissim`` (the one COM method) is
# ever stubbed, which is why it is one method.
# ---------------------------------------------------------------------------


def _runner(tmp_path: Path, **kwargs: Any) -> search_runner.VissimSearchRunner:
    return search_runner.VissimSearchRunner(
        source_inpx=kwargs.pop("source_inpx", tmp_path / "source.inpx"),
        work_dir=kwargs.pop("work_dir", tmp_path / "work"),
        behavior_no=kwargs.pop("behavior_no", 2),
        duration_s=kwargs.pop("duration_s", 600),
        **kwargs,
    )


def _sqlite_with(path: Path, tables: list[str]) -> Path:
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        for table in tables:
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return path


class _FakeVissim:
    """A stand-in for the ONE COM object the non-COM steps touch.

    Not a proof that COM works — it proves what ``_apply`` and
    ``_pin_single_run`` DECIDE about whatever COM hands back, which is the half
    that is this repository's own logic.
    """

    def __init__(self, *, readback=None, raises=None, num_runs=1, num_runs_raises=False):
        self.readback = readback or (lambda parameter, value: {parameter: value})
        self.raises = raises
        self.num_runs = num_runs
        self.num_runs_raises = num_runs_raises
        self.calls: list[tuple[int, str, float]] = []
        self.vissim = self

    # -- the two attributes _pin_single_run reaches through -----------------
    @property
    def Simulation(self):  # noqa: N802 — COM's own casing
        return self

    def SetAttValue(self, name, value):  # noqa: N802
        if self.num_runs_raises:
            raise RuntimeError("COM said no")
        if name == "NumRuns":
            self.num_runs = value

    def AttValue(self, name):  # noqa: N802
        if self.num_runs_raises:
            raise RuntimeError("COM said no")
        return self.num_runs

    def set_driving_behavior_parameter(self, behavior_no, parameter, value):
        self.calls.append((behavior_no, parameter, value))
        if self.raises:
            raise RuntimeError(self.raises)
        return self.readback(parameter, value)


def test_the_candidate_directory_layout_is_per_candidate_and_per_seed(tmp_path):
    """The seed is in the NAME because ``accept_winner`` re-runs the winner at an
    unseen seed: without it the holdout run lands in the calibration run's
    directory and its database is read as that run's."""
    source = tmp_path / "source.inpx"
    source.write_text("<network/>", encoding="utf-8")
    runner = _runner(tmp_path, source_inpx=source)

    run_inpx, failure = runner._candidate_copy(7, 4243, source)
    assert failure is None
    assert run_inpx.parent.name == "cand_0007_seed_4243"
    assert run_inpx.name == "source.inpx"
    assert run_inpx.read_text(encoding="utf-8") == "<network/>"
    # The SOURCE is untouched — the rule CLAUDE.md states for every clone.
    assert source.read_text(encoding="utf-8") == "<network/>"


def test_the_clone_failure_is_named_not_raised(tmp_path):
    """A work dir that is really a FILE is an ordinary operating condition and
    must cost one candidate, never the search."""
    source = tmp_path / "source.inpx"
    source.write_text("<network/>", encoding="utf-8")
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")

    runner = _runner(tmp_path, source_inpx=source, work_dir=blocked)
    _, failure = runner._candidate_copy(1, 42, source)
    assert failure is not None
    assert failure.startswith(search_runner.CANDIDATE_WORKSPACE_FAILED)

    # ...and through run(), it is a named outcome rather than an exception.
    outcome = runner.run(BASELINE, 42, NODES)
    assert outcome.ok is False
    assert outcome.reason.startswith(search_runner.CANDIDATE_WORKSPACE_FAILED)


def test_the_stale_result_sweep_really_sweeps_and_reaches_subdirectories(tmp_path):
    """``_run_completed`` reads recursively, so the sweep must too — a stale
    ``.db`` one level down would be FOUND by the check and MISSED by the sweep,
    and a prior attempt's result would be read as this candidate's."""
    source = tmp_path / "source.inpx"
    source.write_text("<network/>", encoding="utf-8")
    runner = _runner(tmp_path, source_inpx=source)

    run_dir = runner._candidate_dir(1, 42)
    (run_dir / "nested").mkdir(parents=True)
    top = _sqlite_with(run_dir / "old.db", ["SIMULATIONRUN"])
    nested = _sqlite_with(run_dir / "nested" / "older.db", ["SIMULATIONRUN"])
    keep = run_dir / "notes.txt"
    keep.write_text("keep me", encoding="utf-8")

    run_inpx, failure = runner._candidate_copy(1, 42, source)
    assert failure is None
    assert not top.exists() and not nested.exists()
    assert keep.exists(), "the sweep removes result databases, not the directory"
    assert run_inpx.exists()


def test_a_corrupt_result_database_is_a_named_failure_not_an_exception(tmp_path):
    """THE REFUTER'S REPRODUCTION, 2026-08-21.

    A half-flushed ``.db`` — a killed run, a full disk, a file the antivirus is
    still holding — made ``sqlite3`` raise straight out of ``run()``, through
    ``ParameterSearch._evaluate`` (which does not catch either) and into the
    job's outer handler, which marks the job FAILED and writes NO partial-state
    artifact. One bad file threw away every measured ledger row the search had
    already bought.

    Two shapes of corruption, because they fail at different sqlite3 calls: a
    file of garbage bytes, and a valid header followed by truncation.
    """
    runner = _runner(tmp_path)
    run_dir = tmp_path / "results"
    run_dir.mkdir()

    garbage = run_dir / "garbage.db"
    garbage.write_bytes(b"this is not a database, it is 41 bytes of noise")
    reason = search_runner.VissimSearchRunner._run_completed(run_dir)
    assert reason is not None and reason.startswith(search_runner.RUN_DID_NOT_COMPLETE)
    assert "garbage.db" in reason

    garbage.unlink()
    truncated = run_dir / "truncated.db"
    valid = _sqlite_with(tmp_path / "valid.db", ["SIMULATIONRUN"])
    truncated.write_bytes(valid.read_bytes()[:24])
    reason = search_runner.VissimSearchRunner._run_completed(run_dir)
    assert reason is not None and reason.startswith(search_runner.RUN_DID_NOT_COMPLETE)

    assert runner is not None  # constructed to prove the static method is reachable as one


def test_a_corrupt_database_reaches_run_as_a_named_outcome(tmp_path, monkeypatch):
    """The same reproduction driven through ``run()`` end to end, with the ONE
    COM method stubbed — so every non-COM step really executes.

    MUTATION: un-wrapping the sqlite read in ``_run_completed`` makes this raise
    instead of returning, which is exactly what the refuter demonstrated.
    """
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (True, None))
    source = tmp_path / "source.inpx"
    source.write_text("<network/>", encoding="utf-8")
    runner = _runner(tmp_path, source_inpx=source)

    def fake_drive(index, seed, run_inpx, vector):
        # What a finished-but-corrupt Vissim run leaves behind.
        (run_inpx.parent / "corrupt.db").write_bytes(b"\x00\x01 not a database")
        return None

    monkeypatch.setattr(runner, "_drive_vissim", fake_drive)
    outcome = runner.run(BASELINE, 42, NODES)
    assert outcome.ok is False
    assert outcome.reason.startswith(search_runner.RUN_DID_NOT_COMPLETE)
    assert outcome.geh_by_node == {}
    assert outcome.elapsed_s >= 0.0


def test_a_finished_run_needs_a_simulationrun_row(tmp_path):
    """The artifact is SIMULATIONRUN — never ``SimSec``, which resets to 0 the
    moment a run ends. Same check, same reason, as the agent run."""
    run_dir = tmp_path / "results"
    run_dir.mkdir()
    assert search_runner.VissimSearchRunner._run_completed(run_dir).startswith(
        search_runner.RUN_DID_NOT_COMPLETE
    )

    _sqlite_with(run_dir / "no-run.db", ["VEHICLENETWORKPERFORMANCEMEASUREMENT"])
    reason = search_runner.VissimSearchRunner._run_completed(run_dir)
    assert reason is not None and "SIMULATIONRUN" in reason

    (run_dir / "no-run.db").unlink()
    _sqlite_with(run_dir / "done.db", ["SimulationRun", "MOVEMENT"])
    assert search_runner.VissimSearchRunner._run_completed(run_dir) is None, (
        "the table-name comparison is case-folded, as Vissim's own casing varies"
    )


def test_the_readback_verdict_catches_a_silent_non_write(tmp_path):
    """Vissim accepts an out-of-range ``SetAttValue`` SILENTLY and leaves the old
    value in place. A candidate scored on the values it MEANT to set rather than
    the ones the model holds is a whole search built on a lie."""
    runner = _runner(tmp_path)
    stale = _FakeVissim(readback=lambda parameter, value: {parameter: 0.9})

    reason = runner._apply(stale, {"saf_dist_fact_ln_chg": 0.34})
    assert reason is not None and reason.startswith(search_runner.PARAMETER_NOT_APPLIED)
    assert "0.34" in reason and "0.9" in reason

    # An absent field in the read-back is the same verdict — not an exception,
    # and certainly not "assume it landed".
    missing = _FakeVissim(readback=lambda parameter, value: {})
    assert runner._apply(missing, {"saf_dist_fact_ln_chg": 0.34}).startswith(
        search_runner.PARAMETER_NOT_APPLIED
    )


def test_a_write_vissim_refuses_outright_is_the_same_named_verdict(tmp_path):
    """``set_driving_behavior_parameter`` RAISES for a value past a floor/ceiling.
    From this runner's side that is the same event as a silent non-write — the
    model does not hold the number this candidate would be scored on — so it gets
    the same code, and the search loses one candidate rather than dying."""
    runner = _runner(tmp_path)
    reason = runner._apply(_FakeVissim(raises="value out of range"), dict(BASELINE))
    assert reason is not None and reason.startswith(search_runner.PARAMETER_NOT_APPLIED)
    assert "value out of range" in reason


def test_the_vector_is_written_one_field_at_a_time_and_every_one_is_read_back(tmp_path):
    """op §2.12.1's "change one parameter at a time" is a WRITE discipline here:
    three fields, three calls, each verified before the next."""
    runner = _runner(tmp_path, behavior_no=2)
    ctrl = _FakeVissim()
    assert runner._apply(ctrl, dict(BASELINE)) is None
    assert [call[1] for call in ctrl.calls] == sorted(BASELINE)
    assert all(call[0] == 2 for call in ctrl.calls)
    assert len(ctrl.calls) == len(BASELINE)


def test_the_readback_tolerates_a_unit_round_trip_but_not_a_stale_value(tmp_path):
    """The setter converts metres to the network's display unit and back, so an
    exact compare would refuse every candidate on a feet-based network. 1e-3 is
    four times finer than the narrowest published band's own resolution and still
    catches a write Vissim silently refused (which comes back as the OLD value,
    never as a 0.001 drift)."""
    runner = _runner(tmp_path)
    drift = _FakeVissim(readback=lambda parameter, value: {parameter: value + 5e-4})
    assert runner._apply(drift, {"lat_dist_stand_def": 0.25}) is None

    coarse = _FakeVissim(readback=lambda parameter, value: {parameter: value + 5e-2})
    assert runner._apply(coarse, {"lat_dist_stand_def": 0.25}) is not None


def test_num_runs_is_pinned_and_a_failure_to_pin_is_named(tmp_path):
    """MEASURED 2026-08-07 by the agent runner: a real consultant export carries
    ``numRuns="5"``, RunContinuous honours it, and ONE requested run came back as
    five databases whose volumes the readers SUMMED. Refused by returning a
    reason rather than raising — a search has a ledger behind it to protect."""
    pin = search_runner.VissimSearchRunner._pin_single_run
    assert pin(_FakeVissim(num_runs=5), 1, 42) is None, "setting it to 1 must succeed"

    stuck = _FakeVissim(num_runs=5)
    stuck.SetAttValue = lambda name, value: None  # the set is silently ignored
    reason = pin(stuck, 1, 42)
    assert reason is not None and reason.startswith(search_runner.NUM_RUNS_NOT_PINNED)
    assert "5" in reason

    refused = pin(_FakeVissim(num_runs_raises=True), 1, 42)
    assert refused is not None and refused.startswith(search_runner.NUM_RUNS_NOT_PINNED)


def test_run_never_raises_even_when_a_step_grows_a_new_one(tmp_path, monkeypatch):
    """The last-resort net. Not an excuse for the named failures above — it is
    what keeps the "never raises" contract true for a step edited later, because
    an exception here does not lose a candidate, it loses the whole search."""
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (True, None))
    source = tmp_path / "source.inpx"
    source.write_text("<network/>", encoding="utf-8")
    runner = _runner(tmp_path, source_inpx=source)

    def explode(*args, **kwargs):
        raise MemoryError("something nobody planned for")

    monkeypatch.setattr(runner, "_candidate_copy", explode)
    outcome = runner.run(BASELINE, 42, NODES)
    assert outcome.ok is False
    assert outcome.reason.startswith(search_runner.RUN_FAILED)
    assert "MemoryError" in outcome.reason
    assert runner.calls == 1, "a candidate that blew up still consumed a slot"


def test_a_failed_candidate_still_reports_progress(tmp_path, monkeypatch):
    """The job's progress line advances on failures too — a search whose every
    candidate is failing must look like it is doing something, and the count is
    how a user learns to stop it."""
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (False, "no"))
    seen: list[dict[str, Any]] = []
    runner = _runner(tmp_path, on_candidate=seen.append)
    runner.run(BASELINE, 42, NODES)
    assert len(seen) == 1 and seen[0]["ok"] is False
    assert seen[0]["index"] == 1


def test_a_progress_callback_that_raises_cannot_kill_a_search(tmp_path, monkeypatch):
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (False, "no"))

    def boom(_progress):
        raise RuntimeError("the UI exploded")

    outcome = _runner(tmp_path, on_candidate=boom).run(BASELINE, 42, NODES)
    assert outcome.ok is False, "the outcome survives a broken progress line"


def test_the_observation_window_is_carried_not_re_resolved():
    """``runner_requirement`` is explicit: a candidate is scored against the SAME
    observation set and period the preconditions were evaluated on. The context is
    frozen and passed through, so the two cannot drift."""
    scoring = search_runner.ScoringContext(
        period="AM", observation_set_id="obs-1", observation_period="07:00-08:00"
    )
    assert scoring.as_kwargs() == {
        "period": "AM",
        "observation_set_id": "obs-1",
        "observation_period": "07:00-08:00",
    }
    with pytest.raises(Exception):
        scoring.period = "PM"  # type: ignore[misc] — frozen on purpose


# ===========================================================================
# 7. The wired flag is a MEASUREMENT, honest in both directions
# ===========================================================================


def test_the_runner_requirement_reports_this_machines_answer(monkeypatch):
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (True, None))
    wired_here = ps.runner_requirement()
    assert wired_here["wired"] is True
    assert wired_here["why_not_here"] is None
    assert wired_here["implementation"] == search_runner.RUNNER_IMPLEMENTATION

    monkeypatch.setattr(
        search_runner, "runner_availability", lambda: (False, "not Windows.")
    )
    unwired = ps.runner_requirement()
    assert unwired["wired"] is False
    assert "not Windows." in unwired["why_not_here"]
    # The SPEC survives either way — an unwired card still says what running one takes.
    assert wired_here["requires"] == unwired["requires"] == list(ps.RUNNER_REQUIRES)


def test_the_trigger_card_carries_the_same_measurement(monkeypatch):
    from test_agent_parameter_search import _open_gate

    monkeypatch.setattr(search_runner, "runner_availability", lambda: (False, "not Windows."))
    assert ps.trigger_card(_open_gate())["runner"]["wired"] is False
    monkeypatch.setattr(search_runner, "runner_availability", lambda: (True, None))
    assert ps.trigger_card(_open_gate())["runner"]["wired"] is True


# ===========================================================================
# 8. The rail carries the job, in both of its states
# ===========================================================================


def test_the_rail_carries_the_job_even_after_success_relocks_the_gate(
    client, monkeypatch, wired
):
    """A search that WORKS closes the door behind it — every node passes, so the gate
    refuses with GEH_ALREADY_PASSES and there is no card any more.

    MUTATION: a ``search_job`` block that lived only on the card erases the
    completion at the exact moment it arrives, and this is where that shows.
    """
    session_id, record = _finished_search(client, monkeypatch)

    rail = client.get(f"/api/calibration/rail?scenario_id={SCENARIO_ID}").json()
    track = next(step for step in rail["steps"] if step["number"] == 12)["tracks"][0]
    assert track["locked"] is False and track["card"] is not None
    assert track["search_job"]["id"] == record["id"]
    assert track["card"]["search_job"]["id"] == record["id"]
    assert track["search_job"]["remembered"] is True
    for language in ("ko", "en", "ja"):
        assert track["search_job"]["headline"][language].strip()

    # Now the ladder passes everywhere — the gate shuts, and the job must survive it.
    passing = _report(nodes=[{"node_id": node, "node_total_geh": 2.0} for node in NODES])
    monkeypatch.setattr(
        calibration_endpoint, "_reality_check", lambda scenario_id, **kw: passing
    )
    relocked = client.get(f"/api/calibration/rail?scenario_id={SCENARIO_ID}").json()
    track = next(step for step in relocked["steps"] if step["number"] == 12)["tracks"][0]
    assert track["locked"] is True and track["card"] is None
    assert track["search_job"]["id"] == record["id"]
    assert session_id


def test_the_rail_offers_the_behaviour_choice_the_server_will_not_make(
    client, monkeypatch, wired
):
    rail = client.get(f"/api/calibration/rail?scenario_id={SCENARIO_ID}").json()
    track = next(step for step in rail["steps"] if step["number"] == 12)["tracks"][0]
    assert [item["no"] for item in track["behaviours"]] == ["2"]
    assert track["default_behavior_no"] == "2", "one candidate is not a choice to make"


def test_one_workspaces_job_is_invisible_to_another(client, monkeypatch, wired):
    """A job from another project must be indistinguishable from one that does not
    exist — the same rule every listing on this router follows."""
    _, record = _finished_search(client, monkeypatch)
    assert search_job.latest_job("some-other-workspace") is None

    found = client.get(f"/api/calibration/parameter-search/jobs/{record['id']}")
    assert found.status_code == 200 and found.json()["id"] == record["id"]
    assert client.get("/api/calibration/parameter-search/jobs/nope").status_code == 404


def test_the_job_card_says_nothing_the_job_record_did_not(client, monkeypatch, wired):
    """Every word on the card is the server's, and the client renders them.

    ``live`` in particular is a FIELD: the moment the client derives it from
    ``status`` it owns a status vocabulary, and a sixth status would read as
    finished.
    """
    _, record = _finished_search(client, monkeypatch)
    card = search_job.job_card(record)
    assert card["live"] is False
    assert card["status"] == search_job.COMPLETE
    for key in ("status_text", "headline"):
        assert {"ko", "en", "ja"} == set(card[key])
    assert search_job.job_card(None) is None
    assert search_job.job_card({}) is None

    queued = search_job.job_card({**record, "status": search_job.RUNNING})
    assert queued["live"] is True


# ===========================================================================
# 9. The provider fixture is used by every turn above; keep the import honest
# ===========================================================================


def test_the_fake_provider_is_the_only_llm_in_this_file():
    assert FakeStructuredProvider is not None
    assert _events is not None

"""Unit #51-c — the SEARCHED-parameter exception, reached through the product path.

``test_agent_searched_behaviour_grant.py`` proves #51-b's exception works when the
gates are constructed by hand. This file proves the thing that was missing after it:
that the shipping endpoints can carry a finished search from a proposal to a human's
click and out the other side as one dispatched write. Every step goes through the real
routes —

    POST /api/assistant/agent/query/stream   (the write pauses)
      -> searched_behaviour_broker.search_id_covering  (which search names this write?)
      -> approval_request + searched_search + pending_approval.json
    POST /api/assistant/agent/approve        (a human's click)
      -> PreapprovedWriteGate + chain_grant_providers + a RE-MINTED grant
      -> the real constraint engine
      -> dispatch, once

— with the real ``GuardrailEngine``, the real gates, the real
``parameter_search``/``searched_behaviour_grant`` and a real parsed shadow model. Two
things are faked, both at boundaries this repo already fakes: the LLM provider (a canned
action queue) and the Vissim COM edit session. No live COM is touched.

WHERE THE SEARCH COMES FROM, stated honestly
--------------------------------------------
No live runner ships in this build (``parameter_search.runner_requirement()['wired']``
is ``False``), so nothing inside a product turn runs a search. The search here is a REAL
one — the synthetic-surface engine run end to end by
``test_agent_searched_behaviour_grant._finished`` — handed to the broker exactly the way
a runner or the harness would hand it over: ``remember(**record, session_id=...)``. That
is the seam this unit wires; everything downstream of it is the product's own code.

The forgery legs are as much the point as the happy path. A ``pending_approval.json``
edited to name a search the process never finished — or to name a search under the OTHER
species' key — must produce a NAMED REFUSAL, not a write.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from services.agent import car_following_broker, car_following_grant, saturation_flow
from services.agent import parameter_search as ps
from services.agent import searched_behaviour_broker as broker
from services.agent import searched_behaviour_grant as sbg
from services.agent.tools_network import build_shadow_model

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_agent_searched_behaviour_grant import _finished  # noqa: E402

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"

#: The behaviour the search targets. Referenced by three link-behavior-types, all of
#: them named, so the radius is COMPLETE — the only situation the exception is
#: available in at all.
BEHAVIOR_NO = "2"

SHARED_OBJECT = "driving_behavior_shared_object_edit"
CONSENTED = "searched_behaviour_shared_edit_consented"

#: The three lateral/lane-change fields #51 searches, present on the injected
#: behaviours so a write can be read back out of the working copy.
_INJECTED = """  <linkBehaviorTypes>
    <linkBehaviorType no="10" name="Urban arterial" drivBehavDef="2"/>
    <linkBehaviorType no="11" name="Urban collector" drivBehavDef="2"/>
    <linkBehaviorType no="12" name="Urban local" drivBehavDef="2"/>
    <linkBehaviorType no="13" name="Freeway" drivBehavDef="3"/>
  </linkBehaviorTypes>
  <drivingBehaviors>
    <drivingBehavior no="2" name="Urban (motorized)" carFollowModType="WIEDEMANN74" \
w74ax="2" w74bxAdd="2" w74bxMult="3" safDistFactLnChg="0.6" latDistDrivDef="1" \
latDistStandDef="1"/>
    <drivingBehavior no="3" name="Freeway (free flow)" carFollowModType="WIEDEMANN74" \
w74ax="2" w74bxAdd="2" w74bxMult="3" safDistFactLnChg="0.6" latDistDrivDef="1" \
latDistStandDef="1"/>
  </drivingBehaviors>
</network>"""


@pytest.fixture()
def calibration_inpx(tmp_path: Path) -> Path:
    """``practice.inpx`` plus a real, parseable shared-behaviour reference graph."""
    text = DEMO_INPX.read_text(encoding="utf-8")
    assert "<drivingBehaviors>" not in text, "fixture assumption changed: practice.inpx has behaviours"
    path = tmp_path / "calibration.inpx"
    path.write_text(text.replace("</network>", _INJECTED), encoding="utf-8")

    target = _target_behavior(path)
    # The premises of everything below, checked at their source.
    assert target["reference_count"] == 3
    assert sbg.consent_radius(target)["complete"] is True
    for parameter in ps.SEARCHABLE_PARAMETERS:
        assert target[parameter] is not None, f"{parameter} must be readable for a write to land"
    return path


def _target_behavior(inpx: Path) -> dict[str, Any]:
    shadow = build_shadow_model(inpx)
    return next(b for b in shadow["driving_behaviors"] if b["no"] == BEHAVIOR_NO)


class FakeStructuredProvider:
    """Canned ``(action, meta)`` queue — the fixture shape every approval test uses."""

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


def _action(tool_name: str, *, summary: str, **params: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


def _respond(answer: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _action("respond", summary="보고합니다", answer=answer)


def _write_action(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return _action(
        ps.SEARCH_TOOL,
        summary=f"탐색 승자 {params['parameter']} = {params['value']}",
        behavior_no=params["behavior_no"],
        parameter=params["parameter"],
        value=params["value"],
    )


class FakeEditSession:
    """Duck-types ``VissimEditSession`` for the writes this file dispatches.

    ``persist()`` really rewrites the attribute in the working copy's XML, because
    ``guardrails.postwrite``'s target-persisted check reads the file back — a fake that
    only flipped a flag would let a write "succeed" without the value ever landing.
    """

    _XML_ATTR = {
        "ax": "w74ax",
        "bx_add": "w74bxAdd",
        "bx_mult": "w74bxMult",
        "saf_dist_fact_ln_chg": "safDistFactLnChg",
        "lat_dist_driv_def": "latDistDrivDef",
        "lat_dist_stand_def": "latDistStandDef",
    }

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = Path(working_inpx)
        self.calls: list[tuple[Any, ...]] = []
        self.persist_calls = 0
        self.closed = False
        self._pending: dict[tuple[int, str], float] = {}

    def set_driving_behavior_parameter(
        self, behavior_no: int, parameter: str, value: float
    ) -> dict[str, Any]:
        self.calls.append(("set_driving_behavior_parameter", behavior_no, parameter, value))
        self._pending[(behavior_no, parameter)] = value
        return {parameter: value}

    def persist(self) -> None:
        self.persist_calls += 1
        if not self._pending:
            return
        text = self.working_inpx.read_text(encoding="utf-8")
        for (behavior_no, parameter), value in self._pending.items():
            attr = self._XML_ATTR[parameter]
            pattern = re.compile(rf'(<drivingBehavior no="{behavior_no}"[^>]*?){attr}="[^"]*"')
            text, count = pattern.subn(rf'\g<1>{attr}="{value}"', text, count=1)
            assert count == 1, f"fixture: could not rewrite {attr} on behaviour {behavior_no}"
        self.working_inpx.write_text(text, encoding="utf-8")
        self._pending.clear()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _isolate_brokers():
    """BOTH species' stores, so a leak in either direction is visible."""
    broker.reset()
    car_following_broker.reset()
    yield
    broker.reset()
    car_following_broker.reset()


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch, calibration_inpx: Path):
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: calibration_inpx)
    return calibration_inpx


def _events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _turn(client, monkeypatch, queue, *, session_id: str | None = None, message: str = "확인해 주세요"):
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: FakeStructuredProvider(queue))
    body: dict[str, Any] = {"message": message, "response_language": "ko"}
    if session_id:
        body["session_id"] = session_id
    response = client.post("/api/assistant/agent/query/stream", json=body)
    assert response.status_code == 200
    return _events(response)


def _open_session(client, monkeypatch) -> str:
    """One ordinary turn, so a session (and its ownership sidecar) exists.

    A search is remembered against a SESSION, and a session only exists once a turn has
    made one — which is exactly the order a live runner would meet: the modeller is in a
    conversation, the search finishes, and its winner is proposed into that same session.
    """
    events = _turn(client, monkeypatch, [_respond("네.")], message="상태를 알려 주세요")
    return events[-1]["response"]["session_id"]


def _seed_search(session_id: str, inpx: Path, *, search_id: str = "s-e2e") -> dict[str, Any]:
    """A REAL finished search, handed to the broker the way a runner would hand it over."""
    record = _finished(_target_behavior(inpx), search_id=search_id)
    remembered = broker.remember(**record, session_id=session_id)
    assert remembered == search_id
    return record


def _propose_then_card(client, monkeypatch, wired, *, action_index: int = 0):
    session_id = _open_session(client, monkeypatch)
    record = _seed_search(session_id, wired)
    params = dict(record["proposal"]["actions"][action_index]["params"])
    events = _turn(
        client,
        monkeypatch,
        [_write_action(params)],
        session_id=session_id,
        message="탐색 승자를 적용해 주세요",
    )
    card = next((e for e in events if e["type"] == "approval_request"), None)
    assert card is not None, f"no approval card was built; events={[e['type'] for e in events]}"
    return record, params, card, session_id


# ---------------------------------------------------------------------------
# 1. The product path, end to end
# ---------------------------------------------------------------------------


def test_the_turn_reaches_the_card_with_the_search_and_its_grant(client, monkeypatch, wired):
    """A remembered search -> an approval card carrying it, with a live re-minted grant."""
    record, params, card, session_id = _propose_then_card(client, monkeypatch, wired)

    # The guardrail stood down WITH A SENTENCE rather than going quiet: the warn-tier
    # finding naming the radius is what the human is being asked to read.
    constraint_ids = {w["constraint_id"] for w in card["warnings"]}
    assert CONSENTED in constraint_ids
    assert SHARED_OBJECT not in constraint_ids

    payload = card["searched_search"]
    assert payload["status"] == "PROPOSED"
    assert payload["search_id"] == "s-e2e"
    assert payload["behavior_no"] == BEHAVIOR_NO
    assert payload["grant"]["reason"] == broker.GRANTED
    assert payload["grant"]["available"] is True
    # Only the parameters the winner MOVED, and the value this card is asking about is
    # one of them.
    assert params["parameter"] in payload["searched_parameters"]
    assert payload["expected_after_apply"][params["parameter"]] == params["value"]
    # The evidence, in the three places the decision requires it: the rank sentence, the
    # ledger it came out of, and the holdout that accepted it.
    assert payload["evidence_rank"] == sbg.EVIDENCE_RANK
    assert payload["ledger"]["search_id"] == "s-e2e" and payload["ledger"]["rows"] > 0
    assert payload["holdout"]["accepted"] is True
    assert payload["holdout"]["unseen_seed"] == 99
    assert payload["consent_radius"]["complete"] is True
    assert payload["consent_radius_phrase"]

    # BOTH paths, because since #51-b they genuinely differ — and the resolutions carry
    # the user's own answer rather than an open question.
    paths = payload["preflight_forecast"]["paths"]
    assert paths[ps.PATH_DIRECT_EDIT]["expected_to_refuse"] is True
    assert paths[ps.PATH_APPROVED_SET]["expected_to_refuse"] is False
    resolutions = {item["key"]: item for item in payload["preflight_forecast"]["resolutions"]}
    assert resolutions["widen_exception"]["status"] == "DECIDED"
    assert resolutions["localize_duplicate"]["status"] == "REJECTED_BY_USER"
    for item in resolutions.values():
        assert {"ko", "en", "ja"} <= set(item)

    # Nothing about the grant itself reaches the wire — only the verdict.
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "signatures" not in serialized
    assert sbg.PROVENANCE_KIND not in serialized
    assert session_id
    assert record["result"]["status"] == ps.STATUS_COMPLETE


def test_pending_approval_file_carries_a_reference_and_nothing_more(client, monkeypatch, wired):
    """The file gets an opaque search id. Not a grant, not a ledger, not a radius."""
    _, _, card, session_id = _propose_then_card(client, monkeypatch, wired)

    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    pending = json.loads((work_dir / "pending_approval.json").read_text(encoding="utf-8"))

    assert pending["searched_search_id"] == card["searched_search"]["search_id"]
    text = json.dumps(pending, ensure_ascii=False)
    # Everything a grant is made of stays out of the file. If a future edit starts
    # persisting the grant, this is the assertion that fails.
    assert "consent_radius" not in text
    assert "ledger" not in text
    assert "holdout" not in text
    assert sbg.PROVENANCE_KIND not in text


def test_approving_the_card_dispatches_the_write_through_the_re_minted_grant(
    client, monkeypatch, wired, approver_headers
):
    """A human's click -> a grant re-minted from process memory -> exactly one dispatch."""
    _, params, card, session_id = _propose_then_card(client, monkeypatch, wired)
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)

    fake_session = FakeEditSession(work_dir / "working.inpx")
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda p: fake_session)
    monkeypatch.setattr(
        assistant_endpoint,
        "_default_provider",
        lambda: FakeStructuredProvider([_write_action(params), _respond("적용했습니다.")]),
    )

    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": card["approval_id"], "approved": True},
        headers=approver_headers,
    )
    assert response.status_code == 200
    done = _events(response)[-1]
    assert done["response"]["kind"] == "answer"

    # The write really dispatched, and really landed in the working copy.
    assert (
        "set_driving_behavior_parameter",
        int(BEHAVIOR_NO),
        params["parameter"],
        params["value"],
    ) in fake_session.calls
    assert fake_session.persist_calls == 1
    saved = _target_behavior(work_dir / "working.inpx")
    assert saved[params["parameter"]] == params["value"]

    write_steps = [s for s in done["response"]["steps"] if s["tool"] == ps.SEARCH_TOOL]
    assert write_steps and all(s["status"] == "ok" for s in write_steps)


def test_declining_the_card_forgets_the_search(client, monkeypatch, wired, approver_headers):
    """A human who said no is the clearest statement that this grant should stop existing."""
    _, _, card, session_id = _propose_then_card(client, monkeypatch, wired)
    assert broker.recall("s-e2e", session_id=session_id) is not None

    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": card["approval_id"], "approved": False},
        headers=approver_headers,
    )
    assert response.status_code == 200
    assert broker.recall("s-e2e", session_id=session_id) is None
    assert (
        broker.mint("s-e2e", {}, session_id=session_id)["reason"] == broker.UNKNOWN_SEARCH
    )


def test_the_pending_listing_rebuilds_the_same_panel(client, monkeypatch, wired):
    """The calibration screen reads the same pause, and gets the same search payload.

    ⑫ is where #50-b puts a ``set_driving_behavior_parameter`` proposal, so this is the
    listing the station panel actually renders from.
    """
    _, _, card, session_id = _propose_then_card(client, monkeypatch, wired)

    listing = client.get("/api/calibration/pending-approvals")
    assert listing.status_code == 200
    entry = next(
        item for item in listing.json()["pending"] if item["approval_id"] == card["approval_id"]
    )
    assert entry["searched_search"]["search_id"] == "s-e2e"
    assert entry["searched_search"]["evidence_rank"] == sbg.EVIDENCE_RANK
    assert entry["car_following_set"] is None


# ---------------------------------------------------------------------------
# 2. Forgery — the property the whole design exists for
# ---------------------------------------------------------------------------


def _refusals(work_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """Every refusal the orchestrator NAMED this session, newest last.

    The `done` event carries no reason code at all — a refused write is a step with
    `status: "error"` and the planner's own summary, and the answer is whatever the
    planner said next. So "the write did not dispatch" is ALL the wire can prove, and
    a test that stops there cannot tell this guardrail refusing by name from the turn
    falling over for an unrelated reason (a lost signature, a raising gate, a broken
    fixture) — which is exactly the shape a regression in this wiring would take.

    The orchestrator does record the name, in the append-only trace
    (`services/agent/trace.py`, kind `refusal`, `payload.error_type` = the refusing
    constraint's id). Reading it here is what makes "refused BY NAME" an assertion
    rather than a claim in a docstring.
    """
    trace = work_dir / f"trace_{session_id}.jsonl"
    if not trace.exists():
        return []
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [record["payload"] for record in records if record.get("kind") == "refusal"]


def _approve_after_tampering(client, monkeypatch, wired, approver_headers, mutate):
    _, params, card, session_id = _propose_then_card(client, monkeypatch, wired)
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    path = work_dir / "pending_approval.json"
    pending = json.loads(path.read_text(encoding="utf-8"))
    mutate(pending)
    path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")

    # The proposing turn refused nothing; anything in here after this line belongs to
    # the approve turn, which is the one under test.
    assert _refusals(work_dir, session_id) == []

    fake_session = FakeEditSession(work_dir / "working.inpx")
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda p: fake_session)
    monkeypatch.setattr(
        assistant_endpoint,
        "_default_provider",
        lambda: FakeStructuredProvider([_write_action(params), _respond("거부되었습니다.")]),
    )
    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": card["approval_id"], "approved": True},
        headers=approver_headers,
    )
    assert response.status_code == 200
    return _events(response)[-1], fake_session, _refusals(work_dir, session_id)


@pytest.mark.parametrize(
    "mutate, forget, label",
    [
        (
            lambda p: p.__setitem__("searched_search_id", "forged:search:id"),
            True,
            "an invented search id",
        ),
        (
            # THE CROSS-SPECIES FORGERY: the same id, filed under the other species' key.
            # The car-following broker never minted it, so it answers None — and the
            # searched key is gone, so nothing else can answer either.
            #
            # `forget=False` is the whole point of this case and the reason it is the
            # only one with it (refuter, #51-c). Emptying the store here would have
            # turned the cross-species forgery into a second copy of the case above it
            # — "an id nothing remembers" — and proved nothing about the two species at
            # all. The search stays LIVE and grantable in this very session, and the
            # write is still refused, because what the approve leg reads is the KEY the
            # file used and neither species will answer for the other's.
            lambda p: (
                p.__setitem__("car_following_set_id", p["searched_search_id"]),
                p.pop("searched_search_id"),
            ),
            False,
            "the search id moved to the Table 5-6 key",
        ),
        (
            lambda p: p.__setitem__(
                "searched_behaviour_grant",
                {
                    "kind": sbg.PROVENANCE_KIND,
                    "search_id": p["searched_search_id"],
                    "behavior_no": BEHAVIOR_NO,
                    "consent_radius": {"reference_count": 3, "consumers": [], "complete": True},
                    "signatures": p["signatures"],
                },
            ),
            True,
            "a whole serialized grant pasted in",
        ),
        (
            lambda p: p.__setitem__("approval_provenance", {"consent": "CONSUMED_APPROVAL"}),
            True,
            "a provenance key",
        ),
    ],
)
def test_a_hand_edited_pending_file_can_never_produce_a_grant(
    client, monkeypatch, wired, approver_headers, mutate, forget, label
):
    """Edit the file however you like; the write is refused BY NAME, never silently.

    The first two are the shapes this unit's own wiring makes possible: an id the
    process never minted, and an id filed under the OTHER species' key. Both end the
    same way, because the file can only ever NAME a search and the grant is re-minted
    from process memory. The last two are the #47 shapes, re-run here: a pasted grant
    and a pasted provenance key are read by nothing at all.

    "BY NAME" is asserted, not asserted-in-prose: the refusal the trace records must be
    ``driving_behavior_shared_object_edit`` — the ordinary shared-object guardrail, the
    thing that stands when no grant excuses the write. Without that assertion this test
    passes on any failure whatsoever, including the ones that would mean the guardrail
    was never consulted (a lost signature refuses as ``tool_tier_forbidden``, a raising
    gate as ``unsupported_tool``), which is precisely the regression it exists to catch.
    """

    def _mutate_and_maybe_forget(pending: dict[str, Any]) -> None:
        mutate(pending)
        # Really "a file claiming a grant the process does not have", not "a file
        # pointing at one it still does" — for the shapes that leave a live id behind,
        # the store is emptied so only the file's claim remains. NOT for the
        # cross-species case: see its own note in the parameter list.
        if forget:
            broker.reset()

    done, fake_session, refusals = _approve_after_tampering(
        client, monkeypatch, wired, approver_headers, _mutate_and_maybe_forget
    )

    assert fake_session.calls == [], f"{label}: a forged grant reached COM"
    assert fake_session.persist_calls == 0
    write_steps = [s for s in done["response"]["steps"] if s["tool"] == ps.SEARCH_TOOL]
    assert write_steps, f"{label}: the write never even reached the gate"
    assert all(s["status"] != "ok" for s in write_steps), f"{label}: the write was allowed"
    # ... and refused by THIS name, which is the difference between "the exception did
    # not apply" and "the turn fell over".
    assert [item["error_type"] for item in refusals] == [SHARED_OBJECT], (
        f"{label}: expected the shared-object guardrail to refuse by name, got {refusals!r}"
    )
    assert all(item["phase"] == "preflight" for item in refusals), f"{label}: refused too late"


def test_one_session_cannot_spend_another_sessions_search(client, monkeypatch, wired):
    """Searches are keyed by session, so a search finished in one is not a grant in another."""
    _, _, card, session_id = _propose_then_card(client, monkeypatch, wired)
    search_id = card["searched_search"]["search_id"]

    assert broker.recall(search_id, session_id=session_id) is not None
    assert broker.recall(search_id, session_id="deadbeefcafe") is None
    assert (
        broker.mint(search_id, {}, session_id="deadbeefcafe")["reason"] == broker.UNKNOWN_SEARCH
    )


def test_a_table_5_6_write_in_the_same_session_is_not_covered_by_the_search(
    client, monkeypatch, wired
):
    """The two species stay disjoint on the router's own question, not just in the grant.

    ``search_id_covering`` answers by canonical signature — the same comparison the grant
    makes — so a Table 5-6 write proposed in a session that also holds a finished search
    gets NO searched reference on its card, and therefore none in its pending file.
    """
    session_id = _open_session(client, monkeypatch)
    _seed_search(session_id, wired)
    table_action = {
        "tool": ps.SEARCH_TOOL,
        "params": {"behavior_no": BEHAVIOR_NO, "parameter": "ax", "value": 2.5},
    }
    assert broker.search_id_covering(table_action, session_id=session_id) is None
    # ... and neither does a searched parameter at a DIFFERENT value than the winner.
    assert (
        broker.search_id_covering(
            {
                "tool": ps.SEARCH_TOOL,
                "params": {
                    "behavior_no": BEHAVIOR_NO,
                    "parameter": ps.SEARCHABLE_PARAMETERS[0],
                    "value": 0.999,
                },
            },
            session_id=session_id,
        )
        is None
    )
    # The Table 5-6 species is untouched by any of this.
    assert car_following_grant.PROVENANCE_KIND != sbg.PROVENANCE_KIND
    assert saturation_flow.PATH_APPROVED_SET == ps.PATH_APPROVED_SET

"""Unit #47 — the Table 5-6 exception, reached through the REAL product path.

``test_agent_car_following_grant.py`` proves the exception works when the gates
are constructed by hand. This file proves the thing that was actually missing:
that an assistant turn can reach it. Every step here goes through the shipping
endpoints —

    POST /api/assistant/agent/query/stream   (propose)
      -> propose_car_following_from_saturation_flow   (the set is minted)
      -> set_driving_behavior_parameter               (the gate pauses)
      -> approval_request + pending_approval.json
    POST /api/assistant/agent/approve        (a human's click)
      -> PreapprovedWriteGate + a RE-MINTED grant
      -> the real constraint engine
      -> dispatch, once

— with the real ``GuardrailEngine``, the real ``ProposeWriteGate``/
``PreapprovedWriteGate``, the real ``saturation_flow`` builder and a real parsed
shadow model. Only two things are faked, both at the boundaries this repo already
fakes: the LLM provider (a canned action queue, exactly as
``test_agent_approval_endpoint.py`` does) and the Vissim COM edit session. No
live COM is touched.

The forgery leg is the point of the file as much as the happy path: a
``pending_approval.json`` edited to name a grant it was never given must produce
a NAMED REFUSAL, not a write.

The network
-----------
``data/practice.inpx`` carries no ``<drivingBehaviors>`` at all, so the fixture
copies it and injects two W74 behaviours plus three link-behavior-types that
reference one of them. That gives a genuinely SHARED behaviour whose reference
graph is fully named — which is the only situation the exception is available in
(``car_following_grant.consent_radius(...)["complete"]``) and therefore the only
one worth wiring an end-to-end test through.

The diagnosis
-------------
``build_reality_check_report`` is monkeypatched to return
``test_agent_geh_ladder._report()`` — a REAL report from the real comparator over
synthetic run/observed evidence, whose node 5 really does land on
``geh_ladder.CAPACITY_LIMIT``. What is stubbed is the run's result databases, not
the ladder: the rung the tool reads is still one the server computed.
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
from services.agent import tools_calibration
import services.agent.tools_metrics as tools_metrics
from services.agent.tools_network import build_shadow_model

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_agent_geh_ladder import _NODE_ID, _ONE_CONGESTED, _report  # noqa: E402

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"

#: The behaviour the proposals target. Shared by three link-behavior-types, all
#: of them named, so the radius is COMPLETE and the exception is available.
BEHAVIOR_NO = "2"

SHARED_OBJECT = "driving_behavior_shared_object_edit"
CONSENTED = "car_following_shared_edit_consented"

_INJECTED = """  <linkBehaviorTypes>
    <linkBehaviorType no="10" name="Urban arterial" drivBehavDef="2"/>
    <linkBehaviorType no="11" name="Urban collector" drivBehavDef="2"/>
    <linkBehaviorType no="12" name="Urban local" drivBehavDef="2"/>
    <linkBehaviorType no="13" name="Freeway" drivBehavDef="3"/>
  </linkBehaviorTypes>
  <drivingBehaviors>
    <drivingBehavior no="2" name="Urban (motorized)" carFollowModType="WIEDEMANN74" \
w74ax="2" w74bxAdd="2" w74bxMult="3"/>
    <drivingBehavior no="3" name="Freeway (free flow)" carFollowModType="WIEDEMANN74" \
w74ax="2" w74bxAdd="2" w74bxMult="3"/>
  </drivingBehaviors>
</network>"""


@pytest.fixture()
def calibration_inpx(tmp_path: Path) -> Path:
    """``practice.inpx`` plus a real, parseable shared-behaviour reference graph."""
    text = DEMO_INPX.read_text(encoding="utf-8")
    assert "<drivingBehaviors>" not in text, "fixture assumption changed: practice.inpx now has behaviours"
    path = tmp_path / "calibration.inpx"
    path.write_text(text.replace("</network>", _INJECTED), encoding="utf-8")

    shadow = build_shadow_model(path)
    target = next(b for b in shadow["driving_behaviors"] if b["no"] == BEHAVIOR_NO)
    # The premise of everything below, checked at its source: shared, W74, and
    # nameable in full. If any of the three stops holding, the tests that follow
    # would go green (or red) for the wrong reason.
    assert target["reference_count"] == 3
    assert target["car_following_model"] == 74
    assert car_following_grant.consent_radius(target)["complete"] is True
    return path


class FakeStructuredProvider:
    """Canned ``(action, meta)`` queue — the same fixture shape
    ``test_agent_approval_endpoint.py`` uses."""

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


def _propose_action() -> tuple[dict[str, Any], dict[str, Any]]:
    return _action(
        "propose_car_following_from_saturation_flow",
        summary="측정된 포화교통류로 Table 5-6 제안을 만듭니다",
        diagnosis={"scenario_id": "run-1"},
        node_id=_NODE_ID,
        behavior_no=BEHAVIOR_NO,
        measurement={
            "saturation_flow_pcu": 2000,
            "method": "stop-line discharge headway, on-site (MRWA Appendix B §B.3)",
            "attribution": "Dochak survey team",
            "measured_at": "2026-08-14 07:30-08:30",
            "samples": 8,
            "vehicles_per_sample": 11,
        },
    )


def _write_action(parameter: str, value: float) -> tuple[dict[str, Any], dict[str, Any]]:
    return _action(
        "set_driving_behavior_parameter",
        summary=f"주행 행태 {BEHAVIOR_NO}의 {parameter}를 {value}로 설정",
        behavior_no=BEHAVIOR_NO,
        parameter=parameter,
        value=value,
    )


class FakeEditSession:
    """Duck-types ``VissimEditSession`` for the one write this file dispatches.

    ``persist()`` really rewrites the attribute in the working copy's XML,
    because ``guardrails.postwrite``'s target-persisted check reads the file
    back — a fake that only flipped a flag would let the write "succeed" without
    the value ever landing, which is the outcome this file must be able to tell
    apart from a real one.
    """

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = Path(working_inpx)
        self.calls: list[tuple[Any, ...]] = []
        self.persist_calls = 0
        self.closed = False
        self._pending: dict[tuple[int, str], float] = {}
        self._xml_attr = {"ax": "w74ax", "bx_add": "w74bxAdd", "bx_mult": "w74bxMult"}

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
            attr = self._xml_attr[parameter]
            pattern = re.compile(rf'(<drivingBehavior no="{behavior_no}"[^>]*?){attr}="[^"]*"')
            text, count = pattern.subn(rf'\g<1>{attr}="{value}"', text, count=1)
            assert count == 1, f"fixture: could not rewrite {attr} on behaviour {behavior_no}"
        self.working_inpx.write_text(text, encoding="utf-8")
        self._pending.clear()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _isolate_broker():
    car_following_broker.reset()
    yield
    car_following_broker.reset()


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch, calibration_inpx: Path):
    """Pin the dataset and stub the RUN's result databases (never the ladder)."""
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: calibration_inpx)
    monkeypatch.setattr(
        tools_calibration, "build_reality_check_report", lambda *a, **k: _report()
    )
    return calibration_inpx


def _events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _propose(client, monkeypatch, queue) -> list[dict[str, Any]]:
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: FakeStructuredProvider(queue))
    response = client.post(
        "/api/assistant/agent/query/stream",
        json={
            "message": "정지선 포화교통류를 1950 pcu로 측정했습니다. 차량추종 보정을 제안해 주세요.",
            "response_language": "ko",
        },
    )
    assert response.status_code == 200
    return _events(response)


def _propose_then_card(client, monkeypatch, parameter: str, value: float):
    events = _propose(
        client, monkeypatch, [_propose_action(), _write_action(parameter, value)]
    )
    card = next((e for e in events if e["type"] == "approval_request"), None)
    assert card is not None, f"no approval card was built; events={[e['type'] for e in events]}"
    session_id = events[-1]["response"]["session_id"]
    return events, card, session_id


# ---------------------------------------------------------------------------
# 1. The product path, end to end
# ---------------------------------------------------------------------------


def test_the_assistant_turn_reaches_the_card_with_the_set_and_its_grant(
    client, monkeypatch, wired
):
    """Proposal tool -> approval card, carrying the SET and a live grant.

    Two things had to be true for the card to exist at all, and neither was
    before this unit: the proposal set had to be mintable from a product turn,
    and the card's own preflight had to be non-blocking (a refuse-tier finding
    there means the human is never asked — the dead end DECISIONS 2026-08-18
    recorded).
    """
    events, card, session_id = _propose_then_card(client, monkeypatch, "ax", 2.5)

    # The card exists, and the guardrail STOOD DOWN with a sentence rather than
    # going quiet: the warn-tier finding naming the radius is what the human is
    # being asked to read.
    constraint_ids = {w["constraint_id"] for w in card["warnings"]}
    assert CONSENTED in constraint_ids
    assert SHARED_OBJECT not in constraint_ids

    # ... and the set rode along beside it.
    payload = card["car_following_set"]
    assert payload["status"] == "PROPOSED"
    assert payload["set_members"] == ["ax", "bx_add", "bx_mult"]
    assert payload["behavior_no"] == BEHAVIOR_NO
    # The 1950 pcu row's own two columns (2000 pcu rounds DOWN to it), plus
    # §5.2.5.1's flat standstill.
    assert payload["expected_after_apply"] == {"ax": 2.5, "bx_add": 2.25, "bx_mult": 3.25}
    assert payload["grant"]["reason"] == car_following_broker.GRANTED
    assert payload["grant"]["available"] is True
    # The measurement is cited, not asserted.
    assert payload["measurement"]["attribution"] == "Dochak survey team"
    assert payload["measurement"]["provenance"] == saturation_flow.OBSERVED
    # Both bracketing rows survive, so rounding DOWN is auditable.
    assert payload["lookup"]["status"] == saturation_flow.BRACKETED
    assert payload["lookup"]["bracket_lower"]["saturation_flow_pcu"] == 1950
    assert payload["lookup"]["bracket_upper"]["saturation_flow_pcu"] == 2050
    assert payload["applied_row"]["saturation_flow_pcu"] == 1950
    # The forecast says the same thing on both paths the modeller can take.
    paths = payload["preflight_forecast"]["paths"]
    assert paths[saturation_flow.PATH_DIRECT_EDIT]["expected_to_refuse"] is True
    assert paths[saturation_flow.PATH_APPROVED_SET]["expected_to_refuse"] is False
    # The radius, as one printable clause, complete.
    assert payload["consent_radius"]["complete"] is True
    assert payload["consent_radius_phrase"] == "3 consumer(s) / 0 link(s)"
    # Three rationales, three languages each, straight from the builder.
    assert len(payload["proposals"]) == 3
    for proposal in payload["proposals"]:
        assert set(proposal["rationale"]) == {"ko", "en", "ja"}

    # Nothing about the grant itself is on the wire — only the verdict.
    assert "signatures" not in json.dumps(payload)
    assert session_id


def test_pending_approval_file_carries_a_reference_and_nothing_more(
    client, monkeypatch, wired
):
    """The file gets an opaque set id. Not a grant, not a radius, not a value."""
    _, card, session_id = _propose_then_card(client, monkeypatch, "ax", 2.5)

    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    pending = json.loads((work_dir / "pending_approval.json").read_text(encoding="utf-8"))

    set_id = pending["car_following_set_id"]
    assert set_id == card["car_following_set"]["set_id"]
    # Everything a grant is made of stays out of the file. If a future edit
    # starts persisting the grant, this is the assertion that fails.
    assert "consent_radius" not in json.dumps(pending)
    assert "saturation_flow_pcu" not in json.dumps(pending)
    assert car_following_grant.PROVENANCE_KIND not in json.dumps(pending)


def test_approving_the_card_dispatches_the_write_through_the_re_minted_grant(
    client, monkeypatch, wired, approver_headers
):
    """A human's click -> a grant re-minted from memory -> one dispatch.

    This is the leg that was unreachable: before #47 the approve endpoint built
    its ``PreapprovedWriteGate`` with ``grant=None``, so the constraint engine
    saw no provenance and refused the very write the card had just been approved
    for.
    """
    _, card, session_id = _propose_then_card(client, monkeypatch, "ax", 2.5)
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)

    fake_session = FakeEditSession(work_dir / "working.inpx")
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda p: fake_session)
    monkeypatch.setattr(
        assistant_endpoint,
        "_default_provider",
        lambda: FakeStructuredProvider(
            [
                _write_action("ax", 2.5),
                _action("respond", summary="적용했습니다", answer="ax를 2.5로 적용했습니다."),
            ]
        ),
    )

    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": card["approval_id"], "approved": True},
        headers=approver_headers,
    )
    assert response.status_code == 200
    events = _events(response)
    done = events[-1]
    assert done["response"]["kind"] == "answer"

    # The write really dispatched, and really landed.
    assert ("set_driving_behavior_parameter", 2, "ax", 2.5) in fake_session.calls
    assert fake_session.persist_calls == 1
    saved = build_shadow_model(work_dir / "working.inpx")
    assert next(b for b in saved["driving_behaviors"] if b["no"] == BEHAVIOR_NO)["ax"] == 2.5

    # ... and the write step was not a refusal.
    write_steps = [
        s for s in done["response"]["steps"] if s["tool"] == "set_driving_behavior_parameter"
    ]
    assert write_steps and all(s["status"] == "ok" for s in write_steps)


# ---------------------------------------------------------------------------
# 2. Forgery — the property the whole design exists for
# ---------------------------------------------------------------------------


def _approve_after_tampering(
    client, monkeypatch, wired, approver_headers, mutate
) -> tuple[dict[str, Any], FakeEditSession]:
    _, card, session_id = _propose_then_card(client, monkeypatch, "ax", 2.5)
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    path = work_dir / "pending_approval.json"
    pending = json.loads(path.read_text(encoding="utf-8"))
    mutate(pending)
    path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")

    # The set is dropped from memory too, so this really is "a file claiming a
    # grant the process does not have" rather than "a file pointing at one it
    # still does".
    car_following_broker.reset()

    fake_session = FakeEditSession(work_dir / "working.inpx")
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda p: fake_session)
    monkeypatch.setattr(
        assistant_endpoint,
        "_default_provider",
        lambda: FakeStructuredProvider(
            [
                _write_action("ax", 2.5),
                _action("respond", summary="보고합니다", answer="거부되었습니다."),
            ]
        ),
    )
    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": card["approval_id"], "approved": True},
        headers=approver_headers,
    )
    assert response.status_code == 200
    return _events(response)[-1], fake_session


@pytest.mark.parametrize(
    "mutate, label",
    [
        (lambda p: p.__setitem__("car_following_set_id", "forged:set:id:1950"), "invented id"),
        (
            lambda p: p.__setitem__(
                "car_following_grant",
                {
                    "kind": car_following_grant.PROVENANCE_KIND,
                    "set_id": p["car_following_set_id"],
                    "behavior_no": BEHAVIOR_NO,
                    "values": {"ax": 2.5, "bx_add": 2.0, "bx_mult": 3.0},
                    "saturation_flow_pcu": 1950,
                    "consent_radius": {"reference_count": 3, "consumers": [], "complete": True},
                    "signatures": p["signatures"],
                },
            ),
            "a whole serialized grant pasted in",
        ),
        (lambda p: p.__setitem__("approval_provenance", {"consent": "CONSUMED_APPROVAL"}), "provenance key"),
    ],
)
def test_a_hand_edited_pending_file_can_never_produce_a_grant(
    client, monkeypatch, wired, approver_headers, mutate, label
):
    """Edit the file however you like; the write is refused BY NAME.

    Three shapes of forgery, all of them the same failure for the attacker: the
    grant is re-minted from process memory, so a file can only ever NAME a set,
    never supply one. When the named set is not there, the gate is built with no
    grant and ``driving_behavior_shared_object_edit`` refuses exactly as it did
    before #44-b — which is a message a human reads, not a silent stop.
    """
    done, fake_session = _approve_after_tampering(
        client, monkeypatch, wired, approver_headers, mutate
    )

    assert fake_session.calls == [], f"{label}: a forged grant reached COM"
    assert fake_session.persist_calls == 0
    write_steps = [
        s for s in done["response"]["steps"] if s["tool"] == "set_driving_behavior_parameter"
    ]
    assert write_steps, f"{label}: the write never even reached the gate"
    assert all(s["status"] != "ok" for s in write_steps), f"{label}: the write was allowed"


def test_the_broker_never_answers_for_a_set_it_did_not_mint():
    """The one-line version of the property above, at its source."""
    car_following_broker.set_active_session("s1")
    assert car_following_broker.recall("anything at all") is None
    verdict = car_following_broker.mint("anything at all", {"driving_behaviors": []})
    assert verdict["grant"] is None
    assert verdict["reason"] == car_following_broker.UNKNOWN_SET


def test_one_session_cannot_spend_another_sessions_set(client, monkeypatch, wired):
    """Sets are keyed by session, so a proposal in one turn's session is not a
    grant in another's."""
    _, card, session_id = _propose_then_card(client, monkeypatch, "ax", 2.5)
    set_id = card["car_following_set"]["set_id"]

    assert car_following_broker.recall(set_id, session_id=session_id) is not None
    assert car_following_broker.recall(set_id, session_id="deadbeefcafe") is None
    assert (
        car_following_broker.mint(set_id, {}, session_id="deadbeefcafe")["reason"]
        == car_following_broker.UNKNOWN_SET
    )


# ---------------------------------------------------------------------------
# 3. The rung is the server's, not the planner's
# ---------------------------------------------------------------------------


def test_the_tool_has_no_parameter_that_could_assert_a_rung():
    """Structural: no field NAMES a rung, so none can be asserted outright.

    ``propose_car_following_parameters`` refuses any rung that is not
    CAPACITY_LIMIT, and that refusal is only worth anything while the rung is
    computed rather than supplied. This pins the absence.

    It pins the absence and nothing more (refuter, #47). This test once carried
    the sentence "there is no field to lie in", and that was a claim about the
    NESTED reality-check arguments this assertion never looks at:
    ``targets[].node_control`` is a caller assertion the ladder's last branch
    reads, and withholding it moved a node off SIGNAL_TIMING and onto
    CAPACITY_LIMIT. The behavioural half of that premise now lives in
    ``test_a_concentrated_delay_node_will_not_be_treated_until_its_control_is_declared``;
    what is pinned here is only that no argument spells "rung".
    """
    fields = set(tools_calibration.ProposeCarFollowingParams.model_fields)
    assert fields == {"diagnosis", "node_id", "behavior_no", "measurement", "behavior_scope"}
    assert not {f for f in fields if "rung" in f or "diagnos" in f} - {"diagnosis"}
    # And "diagnosis" is the reality-check ARGUMENTS, not a diagnosis payload.
    nested = tools_calibration.ProposeCarFollowingParams.model_fields["diagnosis"].annotation
    assert "rung" not in set(nested.model_fields)
    # The one nested argument the derivation does depend on, named here so that
    # a future field of the same kind cannot be added silently.
    assert "node_control" in set(
        tools_metrics.RealityCheckNodeTarget.model_fields
    ), "the caller-declared control is what the refusal below exists for"


def test_a_node_that_is_not_capacity_limited_gets_a_refusal_not_a_proposal(
    monkeypatch, calibration_inpx
):
    """The premise check, exercised through the tool rather than the builder."""
    import services.agent.tools_network as tools_network

    monkeypatch.setattr(tools_network, "_active_inpx_override", calibration_inpx)
    # A report whose node reconciles: the ladder returns ROUTING_SPLIT there.
    monkeypatch.setattr(
        tools_calibration,
        "build_reality_check_report",
        lambda *a, **k: _report(observed_total=1400.0, observed_per_approach=350.0),
    )
    params = tools_calibration.ProposeCarFollowingParams(
        diagnosis={"scenario_id": "run-1"},
        node_id=_NODE_ID,
        behavior_no=BEHAVIOR_NO,
        measurement={
            "saturation_flow_pcu": 1950,
            "method": "on-site",
            "attribution": "team",
            "samples": 8,
            "vehicles_per_sample": 11,
        },
    )
    result = tools_calibration.propose_car_following_from_saturation_flow(params)
    payload = result["car_following_proposal"]

    assert payload["status"] == "REFUSED"
    assert payload["reason_code"] == saturation_flow.RUNG_IS_NOT_CAPACITY_LIMIT
    assert payload["grant_available"] is False
    assert set(payload["rationale"]) == {"ko", "en", "ja"}
    # Nothing was remembered, so nothing can later be approved off it.
    assert car_following_broker.recall(payload.get("set_id"), session_id="whatever") is None


def _params_with_control(node_control: str | None) -> tools_calibration.ProposeCarFollowingParams:
    target: dict[str, Any] = {
        "node_id": _NODE_ID,
        "vehicle_input_no": "9",
        "vehicle_input_current_volume": 500.0,
        "vehicle_input_link_no": "501",
    }
    if node_control is not None:
        target["node_control"] = node_control
    return tools_calibration.ProposeCarFollowingParams(
        diagnosis={"scenario_id": "run-1", "targets": [target]},
        node_id=_NODE_ID,
        behavior_no=BEHAVIOR_NO,
        measurement={
            "saturation_flow_pcu": 2000,
            "method": "stop-line discharge headway, on-site",
            "attribution": "Dochak survey team",
            "samples": 8,
            "vehicles_per_sample": 11,
        },
    )


def test_a_concentrated_delay_node_will_not_be_treated_until_its_control_is_declared(
    monkeypatch, calibration_inpx
):
    """The rung is the server's — but ONE of its inputs is the caller's (refuter, #47).

    ``geh_ladder.classify_node``'s last branch is ``concentrated and control ==
    CONTROL_SIGNALIZED``, and ``control`` comes from
    ``RunRealityCheckParams.targets[].node_control``. Measured on ONE run's
    evidence (delay on 1 of 4 approaches at node 5), through the real extraction
    (``tools_metrics._reality_check_node_control``) and the real ladder:

    * declaring ``"signalized"`` → SIGNAL_TIMING → refused;
    * OMITTING the field → CAPACITY_LIMIT, a proposal, and a mintable grant.

    So the premise §5.2.5.1's whole treatment rests on was obtainable by saying
    nothing — the same shape as the two guards this repo has un-shipped. The
    omission is now refused by name, and MRWA's own step order is why: step 6
    (signal timing) precedes step 7, so a node that MIGHT be signalised is not
    handed step 7's remedy.
    """
    import services.agent.tools_network as tools_network

    monkeypatch.setattr(tools_network, "_active_inpx_override", calibration_inpx)
    # A thin shim over the REAL node_control extraction and the REAL ladder, so
    # the only thing that differs between the three legs is the caller's word.
    monkeypatch.setattr(
        tools_calibration,
        "build_reality_check_report",
        lambda scenario_id, *, targets=None, **kw: _report(
            delays=_ONE_CONGESTED,
            node_control=tools_metrics._reality_check_node_control(targets),
        ),
    )
    car_following_broker.set_active_session("s-control")

    signalised = tools_calibration.propose_car_following_from_saturation_flow(
        _params_with_control("signalized")
    )["car_following_proposal"]
    assert signalised["status"] == "REFUSED"
    assert signalised["reason_code"] == saturation_flow.RUNG_IS_NOT_CAPACITY_LIMIT
    assert signalised["rung"] == "SIGNAL_TIMING"

    withheld = tools_calibration.propose_car_following_from_saturation_flow(
        _params_with_control(None)
    )["car_following_proposal"]
    assert withheld["status"] == "REFUSED"
    assert withheld["reason_code"] == tools_calibration.NODE_CONTROL_UNDECLARED
    # The rung it WOULD have ridden on is reported, so the refusal is readable
    # rather than mysterious — and the caveat that disqualified it is named.
    assert withheld["rung"] == "CAPACITY_LIMIT"
    assert "NODE_CONTROL_UNKNOWN" in withheld["diagnosis"]["missing_evidence"]
    assert withheld["grant_available"] is False
    assert set(withheld["rationale"]) == {"ko", "en", "ja"}
    # Nothing was remembered, so no approval can be spent against it later.
    assert car_following_broker.set_id_covering(
        {
            "tool": car_following_grant.GRANT_TOOL,
            "params": {"behavior_no": BEHAVIOR_NO, "parameter": "ax", "value": 2.5},
        }
    ) is None

    # A node whose delay is spread across every approach carries no such caveat,
    # so the treatment is unaffected — the refusal is scoped to the ambiguity,
    # not bolted onto the rung.
    monkeypatch.setattr(
        tools_calibration,
        "build_reality_check_report",
        lambda scenario_id, *, targets=None, **kw: _report(
            node_control=tools_metrics._reality_check_node_control(targets)
        ),
    )
    spread = tools_calibration.propose_car_following_from_saturation_flow(
        _params_with_control(None)
    )["car_following_proposal"]
    assert spread["status"] == "PROPOSED"
    assert spread["rung"] == "CAPACITY_LIMIT"


def test_a_remembered_set_is_an_island_no_reference_can_reach(monkeypatch, calibration_inpx):
    """The grant is re-minted from LIVE objects, so nothing else may hold one.

    Measured before this was closed (refuter, #47): ``remember(props)`` followed
    by ``props[0].params["value"] = 99.0`` made the re-minted grant cover
    ``ax=99.0`` and STOP covering the ``ax=2.5`` the human was shown. No product
    path mutated them — but ``recall`` handed the same objects to the card
    builder, whose payload shares their nested ``evidence`` dicts and goes into a
    response body, so the property was "nobody happens to write to this" rather
    than "nobody can". Both boundaries copy now.
    """
    shadow = build_shadow_model(calibration_inpx)
    behavior = next(b for b in shadow["driving_behaviors"] if b["no"] == BEHAVIOR_NO)
    proposals = saturation_flow.propose_car_following_parameters(
        rung={"rung": "CAPACITY_LIMIT", "evidence": {"node_id": _NODE_ID}},
        measurement=saturation_flow.observed_measurement(
            saturation_flow_pcu=2000,
            method="on-site stop-line survey",
            attribution="Dochak survey team",
            samples=8,
            vehicles_per_sample=11,
        ),
        behavior={
            "no": behavior["no"],
            "name": behavior.get("name"),
            "car_following_model": behavior.get("car_following_model"),
            "reference_count": behavior.get("reference_count", 0),
            "referenced_by": behavior.get("referenced_by") or {},
        },
        shadow_model=shadow,
    )
    car_following_broker.set_active_session("s-island")
    set_id = car_following_broker.remember(proposals)
    assert set_id is not None
    ax_write = {
        "tool": car_following_grant.GRANT_TOOL,
        "params": {"behavior_no": BEHAVIOR_NO, "parameter": "ax", "value": 2.5},
    }

    # (a) the objects the CALLER still holds
    proposals[0].params["value"] = 99.0
    proposals[0].evidence["expected_after_apply"]["ax"] = 99.0
    proposals[0].evidence["consent_radius"]["consumers"].clear()

    # (b) the objects a READER is handed back
    recalled = car_following_broker.recall(set_id)
    assert recalled is not None
    recalled[1].params["value"] = 77.0
    recalled[0].evidence["consent_radius"]["reference_count"] = 999

    verdict = car_following_broker.mint(set_id, shadow)
    assert verdict["reason"] == car_following_broker.GRANTED
    grant = verdict["grant"]
    assert grant.values == {"ax": 2.5, "bx_add": 2.25, "bx_mult": 3.25}
    assert grant.covers(ax_write) is True
    assert grant.consent_radius["reference_count"] == 3
    assert (
        grant.covers({**ax_write, "params": {**ax_write["params"], "value": 99.0}}) is False
    )


def test_the_card_says_which_set_members_are_already_in_the_model(
    client, monkeypatch, wired
):
    """The set is approved one member per card, so "not yet" goes stale.

    Before this (refuter, #47) the panel had no field that could tell a member
    that had ALREADY landed from one that had not, and labelled every member it
    was not currently asking about "미승인 / not yet approved" — false from the
    moment the first of the three is applied, on the one card whose whole job is
    to keep a partly-applied row visible. ``current_values`` is read off the same
    live shadow model the re-mint derives its radius from.
    """
    _, card, session_id = _propose_then_card(client, monkeypatch, "ax", 2.5)
    payload = card["car_following_set"]
    # Nothing has been applied yet: the fixture's behaviour is at the .inpx's
    # own values, none of which is the proposed one.
    assert payload["current_values"] == {"ax": 2.0, "bx_add": 2.0, "bx_mult": 3.0}
    assert payload["expected_after_apply"] == {"ax": 2.5, "bx_add": 2.25, "bx_mult": 3.25}

    # ... and once one member lands, the card for the next one says so.
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    landed = build_shadow_model(work_dir / "working.inpx")
    for entry in landed["driving_behaviors"]:
        if entry["no"] == BEHAVIOR_NO:
            entry["ax"] = 2.5
    after = assistant_endpoint._car_following_card_payload(
        payload["set_id"], session_id, landed
    )
    assert after["current_values"] == {"ax": 2.5, "bx_add": 2.0, "bx_mult": 3.0}
    # The set itself did not move -- only the report of where the model is.
    assert after["expected_after_apply"] == payload["expected_after_apply"]


def test_a_measurement_without_provenance_asks_for_one_instead_of_proposing(
    monkeypatch, calibration_inpx
):
    """A refusal you can turn off by asserting you passed it is not a refusal."""
    import services.agent.tools_network as tools_network

    monkeypatch.setattr(tools_network, "_active_inpx_override", calibration_inpx)
    monkeypatch.setattr(tools_calibration, "build_reality_check_report", lambda *a, **k: _report())
    params = tools_calibration.ProposeCarFollowingParams(
        diagnosis={"scenario_id": "run-1"},
        node_id=_NODE_ID,
        behavior_no=BEHAVIOR_NO,
        measurement={"saturation_flow_pcu": 1950},
    )
    payload = tools_calibration.propose_car_following_from_saturation_flow(params)[
        "car_following_proposal"
    ]

    assert payload["status"] == "REFUSED"
    assert payload["reason_code"] == saturation_flow.MEASUREMENT_PROVENANCE_MISSING
    assert payload["grant_available"] is False


# ---------------------------------------------------------------------------
# 4. Unit #43's diagnosis, on the wire
# ---------------------------------------------------------------------------


def test_a_reality_check_step_now_carries_the_diagnosis_ladder(client, monkeypatch, wired):
    """Before #47 the report was dropped before the HTTP wire.

    ``orchestrator``'s ``step_payload`` is the only per-step thing the stream
    carries, and it was ``{summary, tool, status}`` — the tool envelope's
    ``data`` (which holds the whole reality-check report, ``diagnosis``
    included) never left the server. So unit #43's ladder existed in the trace,
    in the audit ledger and woven into the answer prose, and nowhere a user
    could see it as structure. This is the graft that fixes that, in the same
    shape ``run_simulation``'s trajectory hint already used.
    """
    import services.agent.tools_metrics as tools_metrics

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", lambda *a, **k: _report())
    events = _propose(
        client,
        monkeypatch,
        [
            _action("run_reality_check", summary="실행을 실측과 대조합니다", scenario_id="run-1"),
            _action("respond", summary="보고합니다", answer="노드 5는 용량 한계입니다."),
        ],
    )

    step = next(e for e in events if e["type"] == "step" and e["tool"] == "run_reality_check")
    diagnosis = step["diagnosis"]

    assert diagnosis["by_rung"] == {"CAPACITY_LIMIT": [_NODE_ID]}
    assert diagnosis["truncated"] is False
    assert diagnosis["total"] == 1

    node = diagnosis["nodes"][0]
    assert node["node_id"] == _NODE_ID
    assert node["rung"] == "CAPACITY_LIMIT"
    # Three languages on both of the sentences a modeller acts on ...
    assert set(node["why"]) == {"ko", "en", "ja"}
    assert set(node["next_action"]["text"]) == {"ko", "en", "ja"}
    # ... and the MRWA step that authorises the next action, which is what makes
    # "measure the saturation flow" a citation rather than an opinion.
    assert node["next_action"]["code"] == "MEASURE_SATURATION_FLOW"
    assert node["next_action"]["mrwa_step"] == 7
    # The numbers that established the miss ride along; the nested per-node
    # congestion/outcome blocks deliberately do not.
    assert node["node_total_geh"] is not None
    assert node["geh_threshold"] is not None
    assert "evidence" not in node and "outcome" not in node

    # The `done` envelope carries the same block, so a client that only reads
    # `done` is not left without it.
    done_step = next(
        s for s in events[-1]["response"]["steps"] if s["tool"] == "run_reality_check"
    )
    assert done_step["diagnosis"] == diagnosis


def test_the_diagnosis_key_is_absent_rather_than_empty_when_there_is_nothing_to_say(
    client, monkeypatch, wired
):
    """A node the model reproduces has ``diagnosis: None`` and is skipped; a
    report with no diagnosable node produces no key at all, so a renderer never
    has to distinguish "empty" from "not asked"."""
    import services.agent.tools_metrics as tools_metrics

    monkeypatch.setattr(
        tools_metrics,
        "build_reality_check_report",
        lambda *a, **k: {
            "status": "CHECKED",
            "message": {"ko": "차이 없음", "en": "no gap", "ja": "差なし"},
            "nodes": [],
            "diagnosis": {"by_rung": {}},
        },
    )
    events = _propose(
        client,
        monkeypatch,
        [
            _action("run_reality_check", summary="대조합니다", scenario_id="run-1"),
            _action("respond", summary="보고합니다", answer="차이가 없습니다."),
        ],
    )
    step = next(e for e in events if e["type"] == "step" and e["tool"] == "run_reality_check")
    assert "diagnosis" not in step
    # ... and the `done` copy of the same step agrees.
    done_step = next(
        s for s in events[-1]["response"]["steps"] if s["tool"] == "run_reality_check"
    )
    assert "diagnosis" not in done_step


def test_declining_the_card_forgets_the_set(client, monkeypatch, wired, approver_headers):
    """A human said no, so the grant stops existing.

    The pending file is single-use and already consumed by this point, so the
    set could not have been spent through it either way — but the store is the
    only place a grant lives, and "no" is the clearest possible statement that
    this one should not.
    """
    _, card, session_id = _propose_then_card(client, monkeypatch, "ax", 2.5)
    set_id = card["car_following_set"]["set_id"]
    assert car_following_broker.recall(set_id, session_id=session_id) is not None

    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": card["approval_id"], "approved": False},
        headers=approver_headers,
    )
    assert response.status_code == 200

    assert car_following_broker.recall(set_id, session_id=session_id) is None
    assert (
        car_following_broker.mint(set_id, {}, session_id=session_id)["reason"]
        == car_following_broker.UNKNOWN_SET
    )

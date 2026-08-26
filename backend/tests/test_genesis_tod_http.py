"""Unit #70 — a refused 시간대 is VISIBLE to the client that asked for it.

The defect these tests exist for: every ``tod_*`` finding is minted in
``side_inputs.build_signal_assignments``, which lands it in
``ComposedLayers.issues`` — and ``summary()`` exported only ``compose_refusals``
from there. So a reviewer who ticked 시간대 자동 전환 and got no switching received
a job record byte-identical to one where they had never asked. The findings were
written, serialised into ``layers.json``, and never reachable in the summary a
client actually fetches.

Asserted at the ROUTE, over HTTP, on the response body of the job-detail
endpoint the browser already polls — not on ``ComposedLayers`` in isolation,
because the whole failure was that the object was right and the wire was empty.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import admin_headers
from services.genesis import side_inputs as si

from tests.test_genesis_job_layers import (  # noqa: F401 - imported fixtures
    APPROACH_PHASES,
    _await_terminal,
    _stage,
    client,
    crossroads_osm_file,
    signals_document,
    workspace_root,
)


def _excel_plan(level_no: int, plan_id: int, time_of_day):
    """A typed plan over the crossroads' own phases A/B/C."""
    return {
        "source": "excel_upload",
        "source_file": "원신흥.xlsx",
        "intersection_name": "원신흥",
        "level_no": level_no,
        "plan_id": plan_id,
        "time_of_day": time_of_day,
        "declared_amber_s": 3.0,
        "phases": [
            {"phase_id": "A", "green_s": 30.0, "clearance_s": 5.0},
            {"phase_id": "B", "green_s": 20.0, "clearance_s": 5.0},
            {"phase_id": "C", "green_s": 15.0, "clearance_s": 5.0},
        ],
    }


def _typed_signals_document(time_of_day, *, additional_plans=None, tod=True) -> dict:
    assignment = {
        "node_id": 1,
        "plan": _excel_plan(1, 1, time_of_day),
        "approach_phases": {str(k): v for k, v in APPROACH_PHASES.items()},
    }
    if tod:
        assignment["tod"] = {
            "enabled": True,
            "additional_plans": additional_plans or [],
        }
    return {"kind": si.SIGNALS_DOC_KIND, "assignments": [assignment]}


def _run(client: TestClient, crossroads_osm_file: Path, document: dict) -> dict:
    headers = admin_headers()
    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    assert staged.status_code == 200, staged.text
    token = _stage(client, "signals", document, headers)
    started = client.post(
        "/api/genesis/jobs",
        json={
            "upload_token": staged.json()["token"],
            "name": "tod",
            "signals_token": token,
        },
        headers=headers,
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    _await_terminal(client, job_id)
    # The job-detail route the browser polls, not the in-process record.
    detail = client.get(f"/api/genesis/jobs/{job_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    return detail.json()


def _warnings(record: dict) -> list[dict]:
    return record["layers"]["compose_warnings"]


# ---------------------------------------------------------------------------
# The acceptance sentence
# ---------------------------------------------------------------------------


def test_an_unreadable_tod_phrase_reaches_the_client_as_text(
    client: TestClient, crossroads_osm_file: Path
):
    """Opt in, give the plan a 시간대 nobody can read, and the REASON must be
    readable over HTTP — not a code, not only in an artifact nobody fetches."""
    record = _run(client, crossroads_osm_file, _typed_signals_document("TOD2"))

    codes = [w["code"] for w in _warnings(record)]
    assert "tod_phrase_states_no_clock" in codes
    assert "tod_schedule_no_entries" in codes

    text = " ".join(w["message"] for w in _warnings(record))
    assert "시각" in text  # "names a TOD plan but no clock time", in ko
    assert "TOD2" in text  # the offending segment, quoted back


def test_the_generation_still_succeeds_and_still_says_what_it_could_not_do(
    client: TestClient, crossroads_osm_file: Path
):
    """The anti-poison rule, end to end: the network is produced AND the absence
    is named. A refusal here would have cost the reviewer the whole network."""
    record = _run(client, crossroads_osm_file, _typed_signals_document("TOD2"))
    assert record["refusal"] is None
    assert record["layers"]["signals"]["controllers"] == 1
    assert record["layers"]["compose_refusals"] == []
    assert _warnings(record)


def test_a_readable_schedule_reports_switching_and_no_complaint(
    client: TestClient, crossroads_osm_file: Path
):
    document = _typed_signals_document(
        "07:00", additional_plans=[_excel_plan(1, 2, "17:00")]
    )
    record = _run(client, crossroads_osm_file, document)
    tod = record["layers"]["signals"]["tod"]
    assert tod["controllers"] == 1
    assert tod["programs"] == {"1": 2}
    assert tod["switch_points"] == {"1": ["00:00", "07:00", "17:00"]}
    # S7 settled 2026-08-26 by live SaveNetAs roundtrip; the client is told so,
    # with the evidence rather than a bare boolean.
    assert tod["schedule_element_verified"] is True
    assert tod["schedule_element_verified_on"] == "2026-08-26"
    assert [w["code"] for w in _warnings(record)] == []


def test_one_unreadable_sibling_costs_only_itself_over_http(
    client: TestClient, crossroads_osm_file: Path
):
    """The intersection keeps the schedule its readable plans support, and the
    dropped one is named alongside — never intersection poison."""
    document = _typed_signals_document(
        "07:00",
        additional_plans=[_excel_plan(1, 2, "17:00"), _excel_plan(1, 3, "TOD2")],
    )
    record = _run(client, crossroads_osm_file, document)
    assert record["layers"]["signals"]["tod"]["programs"] == {"1": 2}
    codes = [w["code"] for w in _warnings(record)]
    assert "tod_phrase_states_no_clock" in codes
    assert any("L1-P3" in w["message"] for w in _warnings(record))


def test_an_unsupported_route_says_so_over_http(
    client: TestClient, crossroads_osm_file: Path
):
    """observed_history cannot carry several programs; asking must produce a
    named absence rather than silence."""
    document = signals_document()
    document["assignments"][0]["tod"] = {"enabled": True}
    record = _run(client, crossroads_osm_file, document)
    assert "tod_route_not_supported" in [w["code"] for w in _warnings(record)]
    assert "tod" not in record["layers"]["signals"]


# ---------------------------------------------------------------------------
# Additivity of the new key
# ---------------------------------------------------------------------------


def test_a_generation_that_never_opted_in_carries_an_empty_warning_list(
    client: TestClient, crossroads_osm_file: Path
):
    record = _run(client, crossroads_osm_file, signals_document())
    assert _warnings(record) == []
    assert "tod" not in record["layers"]["signals"]


def test_layers_json_still_agrees_with_the_summary_the_route_returns(
    client: TestClient, crossroads_osm_file: Path
):
    """``layers.json`` embeds ``summary()``; the two must not drift apart now
    that the summary carries a key the artifact's own issue list also covers."""
    headers = admin_headers()
    record = _run(client, crossroads_osm_file, _typed_signals_document("TOD2"))
    artifact = client.get(
        f"/api/genesis/jobs/{record['id']}/artifact/layers_json", headers=headers
    )
    assert artifact.status_code == 200
    payload = artifact.json()
    assert payload["summary"] == record["layers"]
    warned = {i["code"] for i in payload["issues"] if i["severity"] == "warn"}
    assert {w["code"] for w in _warnings(record)} <= warned


def test_the_json_the_route_returns_is_serialisable_as_sent(
    client: TestClient, crossroads_osm_file: Path
):
    """A new key that broke JSON encoding would 500 the whole job screen."""
    record = _run(client, crossroads_osm_file, _typed_signals_document("TOD2"))
    json.dumps(record)

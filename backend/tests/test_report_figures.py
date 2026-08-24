"""Track R unit R-2 — ``services/report/figures.py`` + ``GET /api/report/figures``.

Every generator is tested at the SAME seam ``routers/calibration.py``'s own
tests already patch: the reader function itself, on the module it lives on
(``tools_metrics.build_reality_check_report``, ``audit_trail.read_ledger``,
``tools_network.build_shadow_model`` + ``saturation_flow.verify_applied_row``,
``genesis_jobs.list_jobs`` + ``genesis_jobs.gate_payload``). No test stands up
a real Vissim run, a real DB-backed dataset, or a real genesis job -- this
module reads NOTHING itself (its own docstring's whole point), so patching
its readers is patching the actual I/O boundary, not a shortcut around it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from services import datasets, genesis_jobs
from services.agent import mrwa_reference, saturation_flow, tools_network
from services.agent import tools_metrics
from services.audit_trail import Approver, AuditLedger, ChangeEntry
from services import audit_trail as audit_trail_module
from services.report import figures, spine

BASE_TS = datetime(2026, 8, 4, 9, 0, 0, tzinfo=timezone.utc).timestamp()

REVIEWER = Approver(user_id=7, username="yunha", display_name="이윤하", role="reviewer")


# ---------------------------------------------------------------------------
# 1. GEH 노드 표
# ---------------------------------------------------------------------------


def _fake_reality_check_checked(scenario_id, **kwargs):
    assert kwargs.get("targets") is None, "geh_node_table must not derive/pass edit targets"
    return {
        "status": "CHECKED",
        "geh_threshold": 5.0,
        "message": {"ko": "확인됨", "en": "checked", "ja": "確認済み"},
        "nodes": [
            {
                "node_id": "1",
                "node_name": "Alpha",
                "observed_total": 1000.0,
                "modelled_total": 1010.0,
                "node_total_geh": 0.315,
            },
            {
                "node_id": "2",
                "node_name": "Bravo",
                "observed_total": 1000.0,
                "modelled_total": 1200.0,
                "node_total_geh": 6.03,
            },
        ],
    }


def test_geh_node_table_golden_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _fake_reality_check_checked)

    fig = figures.geh_node_table("scn-1", period="AM Peak")

    assert fig["absent"] is False
    assert fig["figure_id"] == figures.GEH_NODE_TABLE_ID
    assert fig["spine_section_id"] == "statistical_comparison"
    assert fig["citation"] == spine.CIT_2_12_2_STATISTICAL_COMPARISON
    assert [c["key"] for c in fig["columns"]] == [
        "node_id", "node_name", "observed", "modelled", "geh", "pass_fail",
    ]
    rows = {row["node_id"]: row for row in fig["rows"]}
    assert rows["1"]["pass_fail"] == "PASS"
    assert rows["1"]["geh"] == 0.315
    assert rows["2"]["pass_fail"] == "FAIL"
    assert rows["2"]["observed"] == 1000.0
    assert rows["2"]["modelled"] == 1200.0
    assert fig["source_caption"]["run_id"] == "scn-1"
    assert fig["source_caption"]["reader"] == (
        "services.agent.tools_metrics.build_reality_check_report"
    )


def test_geh_node_table_undefined_geh_is_neither_pass_nor_fail(monkeypatch: pytest.MonkeyPatch):
    def fake(scenario_id, **kwargs):
        return {
            "status": "CHECKED",
            "geh_threshold": 5.0,
            "nodes": [
                {
                    "node_id": "9",
                    "node_name": "Zulu",
                    "observed_total": None,
                    "modelled_total": None,
                    "node_total_geh": None,
                }
            ],
        }

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", fake)
    fig = figures.geh_node_table("scn-1")
    assert fig["rows"][0]["pass_fail"] == "UNDEFINED"


def test_geh_node_table_absent_without_scenario_id():
    fig = figures.geh_node_table(None)
    assert fig == {
        "figure_id": figures.GEH_NODE_TABLE_ID,
        "spine_section_id": "statistical_comparison",
        "absent": True,
        "reason": fig["reason"],
    }
    assert "rows" not in fig and "columns" not in fig


def test_geh_node_table_absent_on_not_checkable_carries_the_reports_own_message(
    monkeypatch: pytest.MonkeyPatch,
):
    message = {"ko": "실행 결과가 없습니다", "en": "no run result", "ja": "実行結果がありません"}

    def fake(scenario_id, **kwargs):
        return {"status": "NOT_CHECKABLE", "reason_code": "RUN_NOT_FOUND", "message": message}

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", fake)
    fig = figures.geh_node_table("missing-run")
    assert fig["absent"] is True
    assert fig["reason"] == message
    assert "rows" not in fig


def test_geh_node_table_absent_when_the_reader_raises_names_the_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """DEFECT 2: a crashed reader must not 500 the whole bundle, and its
    reason must be DISTINGUISHABLE from "no evidence" -- it names the
    exception class and message rather than reusing a no-run/no-counts
    sentence."""

    def fake_raises(scenario_id, **kwargs):
        raise RuntimeError("result db is locked")

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", fake_raises)
    fig = figures.geh_node_table("scn-1")

    assert fig["absent"] is True
    assert "rows" not in fig
    assert "RuntimeError" in fig["reason"]["en"]
    assert "result db is locked" in fig["reason"]["en"]
    assert "RuntimeError" in fig["reason"]["ko"]
    assert "RuntimeError" in fig["reason"]["ja"]
    # Not the same sentence as "no scenario_id" or "not checkable" -- a raised
    # reader is a different fact from an absent one.
    no_scenario_reason = figures.geh_node_table(None)["reason"]
    assert fig["reason"]["en"] != no_scenario_reason["en"]


# ---------------------------------------------------------------------------
# 2. 승인 이력 표
# ---------------------------------------------------------------------------


def _ledger_with_two_changes() -> AuditLedger:
    verbatim_reason = "  현장 실측 대비 보정 — 트리밍하지 말 것.\n두 번째 줄."
    applied = ChangeEntry(
        seq=1,
        ts=BASE_TS,
        tool="set_vehicle_input_volume",
        change_type="set",
        target="vehicle_input 113",
        attribute="volume",
        after="569.41",
        before=None,
        outcome="applied",
        summary="vehicle input 113 -> 569.41",
        reason=verbatim_reason,
        postwrite_recorded=True,
        postwrite_findings=(),
        postwrite_disabled=(),
        warnings=(),
        error_type=None,
        message=None,
        approver=REVIEWER,
    )
    unauthenticated = ChangeEntry(
        seq=2,
        ts=BASE_TS + 5.0,
        tool="set_route_relative_flow",
        change_type="set",
        target="decision 3 / route 2",
        attribute="rel_flow",
        after="42.0",
        before="38.0",
        outcome="applied",
        summary="route 2 rel_flow 38.0 -> 42.0",
        reason=None,
        postwrite_recorded=False,
        postwrite_findings=(),
        postwrite_disabled=(),
        warnings=(),
        error_type=None,
        message=None,
        approver=None,
    )
    return AuditLedger(
        session_id="sess-1",
        changes=(applied, unauthenticated),
        refusals=(),
        record_count=6,
        source_filename="trace_sess-1.jsonl",
    )


def test_approval_history_table_golden_path(monkeypatch: pytest.MonkeyPatch):
    ledger = _ledger_with_two_changes()
    monkeypatch.setattr(audit_trail_module, "read_ledger", lambda session_id, **kw: ledger)

    fig = figures.approval_history_table("sess-1")

    assert fig["absent"] is False
    assert fig["spine_section_id"] == "parameter_adjustment_record"
    assert fig["citation"] == spine.CIT_2_12_2_PARAMETER_RECORD
    assert len(fig["rows"]) == 2

    first, second = fig["rows"]
    # VERBATIM: not trimmed, not summarised, not re-worded.
    assert first["reason"] == ledger.changes[0].reason
    assert first["who"] == {
        "user_id": 7, "username": "yunha", "display_name": "이윤하", "role": "reviewer",
    }
    assert "set_vehicle_input_volume" in first["what"]

    assert second["reason"] is None
    assert second["who"] is None
    assert fig["source_caption"]["session_id"] == "sess-1"
    assert fig["source_caption"]["reader"] == "services.audit_trail.read_ledger"


def test_approval_history_table_absent_without_session_id():
    fig = figures.approval_history_table(None)
    assert fig["absent"] is True
    assert "rows" not in fig


def test_approval_history_table_absent_when_no_ledger(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(audit_trail_module, "read_ledger", lambda session_id, **kw: None)
    fig = figures.approval_history_table("ghost-session")
    assert fig["absent"] is True
    assert "ghost-session" in fig["reason"]["en"]


def test_approval_history_table_absent_when_the_reader_raises_names_the_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """DEFECT 2: the refuter's own reproduction -- monkeypatching read_ledger
    to raise used to 500 the whole /api/report/figures bundle. Must now be a
    named absence, and the reason must name the exception rather than reusing
    the "no ledger" sentence (those are different facts)."""

    def fake_raises(session_id, **kwargs):
        raise ValueError("torn trace file")

    monkeypatch.setattr(audit_trail_module, "read_ledger", fake_raises)
    fig = figures.approval_history_table("sess-1")

    assert fig["absent"] is True
    assert "rows" not in fig
    assert "ValueError" in fig["reason"]["en"]
    assert "torn trace file" in fig["reason"]["en"]
    assert "ValueError" in fig["reason"]["ko"]
    assert "ValueError" in fig["reason"]["ja"]

    monkeypatch.setattr(audit_trail_module, "read_ledger", lambda session_id, **kw: None)
    no_ledger_reason = figures.approval_history_table("ghost-session-2")["reason"]
    assert fig["reason"]["en"] != no_ledger_reason["en"]


def test_approval_history_table_empty_but_real_ledger_is_not_an_absence(
    monkeypatch: pytest.MonkeyPatch,
):
    empty = AuditLedger(session_id="sess-2", changes=(), refusals=(), record_count=0)
    monkeypatch.setattr(audit_trail_module, "read_ledger", lambda session_id, **kw: empty)
    fig = figures.approval_history_table("sess-2")
    assert fig["absent"] is False
    assert fig["rows"] == []


# ---------------------------------------------------------------------------
# 3. Table 5-6 적용 표
# ---------------------------------------------------------------------------


def _behavior(no: str, name: str) -> dict:
    return {
        "no": no,
        "name": name,
        "reference_count": 1,
        "car_following_model": "Wiedemann 99",
        "ax": 2.0,
        "bx_add": 2.0,
        "bx_mult": 3.0,
    }


def test_table_5_6_applied_table_golden_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    inpx = tmp_path / "active.inpx"
    inpx.write_text("<network/>", encoding="utf-8")
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: inpx)

    behaviors = [_behavior("1", "Freeway"), _behavior("2", "Local")]
    monkeypatch.setattr(
        tools_network, "build_shadow_model", lambda path: {"driving_behaviors": behaviors}
    )

    calls: list[str] = []

    def fake_verify(behavior, **kwargs):
        calls.append(behavior["no"])
        if behavior["no"] == "1":
            return {
                "status": saturation_flow.ROW_MATCHED,
                "applied": {"ax": 2.0, "bx_add": 2.0, "bx_mult": 3.0},
                "row": {"saturation_flow_pcu": 1630.0, "additive": 2.0, "multiplicative": 3.0},
                "standstill_matches_guideline": True,
                "citation": mrwa_reference.TABLE_5_6_CITATION,
                "why": "matches",
            }
        return {
            "status": saturation_flow.ROW_UNMATCHED,
            "applied": {"ax": 2.5, "bx_add": 2.25, "bx_mult": 3.75},
            "row": None,
            "standstill_matches_guideline": False,
            "citation": mrwa_reference.TABLE_5_6_CITATION,
            "why": "no row",
        }

    monkeypatch.setattr(saturation_flow, "verify_applied_row", fake_verify)

    fig = figures.table_5_6_applied_table()

    assert calls == ["1", "2"], "every behaviour must be graded through the named reader"
    assert fig["absent"] is False
    assert fig["spine_section_id"] == "calibration"
    assert fig["citation"] == mrwa_reference.TABLE_5_6_CITATION
    rows = {row["behavior_no"]: row for row in fig["rows"]}
    assert rows["1"]["matched_row_pcu"] == 1630.0
    assert rows["1"]["standstill_verdict"] is True
    assert rows["1"]["status"] == saturation_flow.ROW_MATCHED
    assert rows["2"]["matched_row_pcu"] is None
    assert rows["2"]["standstill_verdict"] is False


def test_table_5_6_applied_table_absent_with_no_readable_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: tmp_path / "does-not-exist.inpx")
    fig = figures.table_5_6_applied_table()
    assert fig["absent"] is True
    assert "NO_READABLE_NETWORK" in fig["reason"]["ko"]
    assert "rows" not in fig


def test_table_5_6_applied_table_zero_behaviors_is_not_an_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    inpx = tmp_path / "active.inpx"
    inpx.write_text("<network/>", encoding="utf-8")
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: inpx)
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: {"driving_behaviors": []})
    fig = figures.table_5_6_applied_table()
    assert fig["absent"] is False
    assert fig["rows"] == []


# ---------------------------------------------------------------------------
# 4. 게이트 판정 요약
# ---------------------------------------------------------------------------


def _gate_checks() -> list[dict]:
    return [
        {
            "id": "V1", "tier": "refuse", "state": "PASS",
            "findings": [], "findings_omitted": 0,
            "warnings": [], "warnings_omitted": 0, "gaps": [],
        },
        {
            "id": "V2", "tier": "warn", "state": "FAIL",
            "findings": [{"code": "x"}], "findings_omitted": 2,
            "warnings": [], "warnings_omitted": 0, "gaps": [],
        },
    ]


def test_gate_verdict_summary_golden_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_dataset", lambda: {"revision": "rev-1"})

    list_jobs_calls: list[int] = []

    def fake_list_jobs():
        list_jobs_calls.append(1)
        return [
            {"id": "job-old", "publication": {"dataset_revision": "rev-0"}},
            {"id": "job-1", "publication": {"dataset_revision": "rev-1"}},
        ]

    gate_payload_calls: list[tuple[str, str]] = []

    def fake_gate_payload(job_id, language="ko"):
        gate_payload_calls.append((job_id, language))
        return {
            "job_id": job_id,
            "checks": _gate_checks(),
            "live_evidence": {"basis": "operator_attested", "attested_by": "yunha"},
        }

    monkeypatch.setattr(genesis_jobs, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(genesis_jobs, "gate_payload", fake_gate_payload)

    fig = figures.gate_verdict_summary(language="ko")

    assert list_jobs_calls, "list_jobs must actually be called (pin the call)"
    assert gate_payload_calls == [("job-1", "ko")], "must resolve the job matching the ACTIVE revision"
    assert fig["absent"] is False
    assert fig["spine_section_id"] == "limitations"
    assert fig["citation"] == spine.CIT_2_12_2_LIMITATIONS

    rows = {row["check_id"]: row for row in fig["rows"]}
    assert rows["V1"]["attestation_basis"] == "operator_attested"
    assert rows["V1"]["finding_count"] == 0
    assert rows["V2"]["attestation_basis"] is None
    assert rows["V2"]["finding_count"] == 3  # 1 shown + 2 omitted, never silently dropped
    assert fig["source_caption"]["run_id"] == "job-1"


def test_gate_verdict_summary_absent_without_a_dataset_revision(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_dataset", lambda: {"revision": None})
    fig = figures.gate_verdict_summary()
    assert fig["absent"] is True
    assert "rows" not in fig


def test_gate_verdict_summary_absent_when_no_job_matches_the_revision(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(datasets, "get_active_dataset", lambda: {"revision": "rev-9"})
    monkeypatch.setattr(genesis_jobs, "list_jobs", lambda: [{"id": "j", "publication": {"dataset_revision": "other"}}])
    fig = figures.gate_verdict_summary()
    assert fig["absent"] is True


# ---------------------------------------------------------------------------
# Cross-cutting: spine ids, caption completeness, JSON round-trip, timestamps
# ---------------------------------------------------------------------------

_REAL_SPINE_IDS = {section.id for section in spine.REGULATION_SECTIONS}


def test_every_figure_names_a_real_spine_section_id():
    for section_id in (
        figures.SECTION_STATISTICAL_COMPARISON,
        figures.SECTION_PARAMETER_ADJUSTMENT_RECORD,
        figures.SECTION_CALIBRATION,
        figures.SECTION_LIMITATIONS,
    ):
        assert section_id in _REAL_SPINE_IDS


def test_an_invented_spine_section_id_is_refused():
    with pytest.raises(KeyError):
        figures._section("not_a_real_section")


@pytest.fixture()
def all_four_golden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Patch every reader so all four generators return a REAL (non-absent)
    figure, for the cross-cutting sweeps below."""
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _fake_reality_check_checked)

    ledger = _ledger_with_two_changes()
    monkeypatch.setattr(audit_trail_module, "read_ledger", lambda session_id, **kw: ledger)

    inpx = tmp_path / "active.inpx"
    inpx.write_text("<network/>", encoding="utf-8")
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: inpx)
    monkeypatch.setattr(
        tools_network, "build_shadow_model", lambda path: {"driving_behaviors": [_behavior("1", "Freeway")]}
    )
    monkeypatch.setattr(
        saturation_flow,
        "verify_applied_row",
        lambda behavior, **kw: {
            "status": saturation_flow.ROW_MATCHED,
            "applied": {"ax": 2.0, "bx_add": 2.0, "bx_mult": 3.0},
            "row": {"saturation_flow_pcu": 1630.0, "additive": 2.0, "multiplicative": 3.0},
            "standstill_matches_guideline": True,
            "citation": mrwa_reference.TABLE_5_6_CITATION,
            "why": "matches",
        },
    )

    monkeypatch.setattr(datasets, "get_active_dataset", lambda: {"revision": "rev-1"})
    monkeypatch.setattr(
        genesis_jobs, "list_jobs", lambda: [{"id": "job-1", "publication": {"dataset_revision": "rev-1"}}]
    )
    monkeypatch.setattr(
        genesis_jobs,
        "gate_payload",
        lambda job_id, language="ko": {"job_id": job_id, "checks": _gate_checks(), "live_evidence": None},
    )

    return figures.all_figures(scenario_id="scn-1", session_id="sess-1")


_CAPTION_KEYS = {"run_id", "session_id", "dataset_revision", "generated_at", "reader"}


@pytest.mark.parametrize(
    "index",
    [0, 1, 2, 3],
    ids=["geh", "approval_history", "table_5_6", "gate_verdict"],
)
def test_source_caption_is_complete_on_every_real_figure(all_four_golden, index):
    fig = all_four_golden[index]
    assert fig["absent"] is False
    caption = fig["source_caption"]
    assert set(caption) == _CAPTION_KEYS
    assert caption["generated_at"], "generated_at must be populated, never blank"
    assert caption["reader"], "reader must name the function that produced the data"


@pytest.mark.parametrize(
    "index",
    [0, 1, 2, 3],
    ids=["geh", "approval_history", "table_5_6", "gate_verdict"],
)
def test_declared_column_keys_match_every_rows_keys(all_four_golden, index):
    """DEFECT 1 (refuter-proven test gap): a column's declared ``key`` must be
    the exact key every row uses for that cell. Nothing else in this suite
    pinned this -- the refuter renamed ``_APPROVAL_COLUMNS``' "who" to
    "approver", ``_TABLE_5_6_COLUMNS``' "matched_row_pcu" to "row_pcu", and
    ``_GATE_COLUMNS``' "attestation_basis" to "attestation" and the full run
    stayed green each time, because every OTHER assertion in this file reads
    row dicts by their OWN keys directly rather than by cross-referencing the
    declared column list. A renamed key renders as a blank cell in any
    join-by-key consumer (a DOCX table builder, a frontend table renderer)
    with no error anywhere -- silent, not loud -- which is exactly the failure
    mode this test exists to make loud.
    """
    fig = all_four_golden[index]
    assert fig["rows"], "fixture must produce at least one row to pin the binding"
    column_keys = {column["key"] for column in fig["columns"]}
    for row in fig["rows"]:
        assert set(row.keys()) == column_keys, (
            f"{fig['figure_id']}: row keys {sorted(row.keys())} != declared column keys "
            f"{sorted(column_keys)}"
        )


@pytest.mark.parametrize(
    "index",
    [0, 1, 2, 3],
    ids=["geh", "approval_history", "table_5_6", "gate_verdict"],
)
def test_generated_at_is_timezone_aware(all_four_golden, index):
    fig = all_four_golden[index]
    parsed = datetime.fromisoformat(fig["source_caption"]["generated_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


@pytest.mark.parametrize(
    "index",
    [0, 1, 2, 3],
    ids=["geh", "approval_history", "table_5_6", "gate_verdict"],
)
def test_every_figure_round_trips_through_json(all_four_golden, index):
    fig = all_four_golden[index]
    assert json.loads(json.dumps(fig)) == fig


def test_all_figures_degrades_the_missing_one_without_failing_the_rest(
    monkeypatch: pytest.MonkeyPatch,
):
    """No session_id -> the approval table is a named absence, the other
    three are unaffected (mirrors calibration_rail's own "degrade one, not
    all" contract)."""
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _fake_reality_check_checked)
    monkeypatch.setattr(datasets, "get_active_dataset", lambda: {"revision": None})
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: Path("does-not-exist.inpx"))

    result = figures.all_figures(scenario_id="scn-1", session_id=None)
    by_id = {fig["figure_id"]: fig for fig in result}
    assert by_id[figures.GEH_NODE_TABLE_ID]["absent"] is False
    assert by_id[figures.APPROVAL_HISTORY_TABLE_ID]["absent"] is True
    assert by_id[figures.TABLE_5_6_APPLIED_ID]["absent"] is True
    assert by_id[figures.GATE_VERDICT_SUMMARY_ID]["absent"] is True


def test_all_figures_degrades_when_a_reader_raises_not_just_when_it_returns_none(
    monkeypatch: pytest.MonkeyPatch,
):
    """DEFECT 2, at the gathering level: the refuter's reproduction was
    exactly this -- monkeypatch ``read_ledger`` to RAISE (not return
    ``None``) and confirm the bundle still comes back as four figures, with
    only the approval-history one absent, rather than the whole call
    exploding. ``read_ledger`` is gathered SECOND in ``all_figures``' list
    literal, right after the GEH table, which is exactly the position the
    refuter's report says took the other three down with it."""

    def read_ledger_raises(session_id, **kwargs):
        raise RuntimeError("workspace lock held")

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _fake_reality_check_checked)
    monkeypatch.setattr(audit_trail_module, "read_ledger", read_ledger_raises)
    monkeypatch.setattr(datasets, "get_active_dataset", lambda: {"revision": None})
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: Path("does-not-exist.inpx"))

    result = figures.all_figures(scenario_id="scn-1", session_id="sess-1")
    by_id = {fig["figure_id"]: fig for fig in result}
    assert len(result) == 4
    assert by_id[figures.GEH_NODE_TABLE_ID]["absent"] is False
    assert by_id[figures.APPROVAL_HISTORY_TABLE_ID]["absent"] is True
    assert "RuntimeError" in by_id[figures.APPROVAL_HISTORY_TABLE_ID]["reason"]["en"]
    # The two figures gathered AFTER the crashing one still arrived.
    assert by_id[figures.TABLE_5_6_APPLIED_ID]["absent"] is True  # no readable network, named
    assert by_id[figures.GATE_VERDICT_SUMMARY_ID]["absent"] is True  # no revision, named


# ---------------------------------------------------------------------------
# Route level
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_route_returns_four_figures_with_no_query_params(client: TestClient):
    response = client.get("/api/report/figures")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [fig["figure_id"] for fig in body["figures"]] == [
        figures.GEH_NODE_TABLE_ID,
        figures.APPROVAL_HISTORY_TABLE_ID,
        figures.TABLE_5_6_APPLIED_ID,
        figures.GATE_VERDICT_SUMMARY_ID,
    ]
    # No scenario_id/session_id on the demo dataset -> at minimum the GEH and
    # approval-history tables are named absences, never a 4xx.
    assert body["figures"][0]["absent"] is True
    assert body["figures"][1]["absent"] is True


def test_route_stays_200_when_a_reader_raises(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
):
    """DEFECT 2, over HTTP: the refuter's exact reproduction was monkeypatching
    ``read_ledger`` to raise and getting a bare 500 for the whole route. Must
    now stay 200 with a named absence for the one figure whose reader failed."""

    def read_ledger_raises(session_id, **kwargs):
        raise RuntimeError("workspace lock held")

    monkeypatch.setattr(audit_trail_module, "read_ledger", read_ledger_raises)

    response = client.get("/api/report/figures", params={"session_id": "sess-1"})
    assert response.status_code == 200, response.text
    figs = {fig["figure_id"]: fig for fig in response.json()["figures"]}
    assert figs[figures.APPROVAL_HISTORY_TABLE_ID]["absent"] is True
    assert "RuntimeError" in figs[figures.APPROVAL_HISTORY_TABLE_ID]["reason"]["en"]
    # Every other figure in the bundle still rendered.
    assert set(figs) == {
        figures.GEH_NODE_TABLE_ID,
        figures.APPROVAL_HISTORY_TABLE_ID,
        figures.TABLE_5_6_APPLIED_ID,
        figures.GATE_VERDICT_SUMMARY_ID,
    }


def test_route_is_unauthenticated_like_its_neighbors_on_this_router(client: TestClient):
    """No Authorization header, no 401/403 — parity with GET /spine and
    GET /data/{block_type} on this same router, and with
    GET /api/calibration/reality-check (test_auth_core.py's route-scope test
    names that whole GET-side of /api/calibration as staying open)."""
    response = client.get("/api/report/figures")
    assert response.status_code == 200


def test_route_golden_path_through_all_four_readers(monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path):
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _fake_reality_check_checked)
    ledger = _ledger_with_two_changes()
    monkeypatch.setattr(audit_trail_module, "read_ledger", lambda session_id, **kw: ledger)
    inpx = tmp_path / "active.inpx"
    inpx.write_text("<network/>", encoding="utf-8")
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: inpx)
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: {"driving_behaviors": []})
    monkeypatch.setattr(datasets, "get_active_dataset", lambda: {"revision": "rev-1"})
    monkeypatch.setattr(
        genesis_jobs, "list_jobs", lambda: [{"id": "job-1", "publication": {"dataset_revision": "rev-1"}}]
    )
    monkeypatch.setattr(
        genesis_jobs,
        "gate_payload",
        lambda job_id, language="ko": {"job_id": job_id, "checks": _gate_checks(), "live_evidence": None},
    )

    response = client.get(
        "/api/report/figures",
        params={"scenario_id": "scn-1", "session_id": "sess-1"},
    )
    assert response.status_code == 200, response.text
    figs = response.json()["figures"]
    assert all(fig["absent"] is False for fig in figs)

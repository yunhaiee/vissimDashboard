"""Phase 1.3 (audit-trail export v1): backend/services/audit_trail.py.

Every fixture here is a SYNTHETIC trace record stream shaped exactly like the
one ``services/agent/orchestrator.py`` writes (``plan`` -> ``preflight`` ->
``action_start`` -> ``action_end`` -> ``postwrite``, plus ``refusal`` /
``warning`` / ``user_confirm`` records where the real loop writes them), so
nothing here needs live Vissim, a dataset, or a real session directory. The
record shapes are copied from the orchestrator's own ``tracer.write(...)``
call sites; see that module around the write/run dispatch branches.

DOCX assertions read the produced document back with python-docx (the same
library that wrote it) rather than pattern-matching bytes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest

from services import audit_trail

# 2026-08-04 09:00:00 UTC, and one second per step after that -- fixed so
# every rendered timestamp in these tests is deterministic.
BASE_TS = datetime(2026, 8, 4, 9, 0, 0, tzinfo=timezone.utc).timestamp()
PINNED_GENERATED_AT = datetime(2026, 8, 4, 10, 30, 0, tzinfo=timezone.utc)


class _Ledger:
    """Builds a trace record stream the way the orchestrator does, one step at
    a time, with monotonically increasing timestamps."""

    def __init__(self, session_id: str = "abc123def456") -> None:
        self.session_id = session_id
        self.records: list[dict] = []
        self._t = BASE_TS
        self._iteration = 0

    def _write(self, kind: str, payload: dict) -> "_Ledger":
        self._t += 1.0
        self.records.append(
            {"ts": self._t, "session_id": self.session_id, "kind": kind, "payload": payload}
        )
        return self

    # -- orchestrator-shaped record groups ---------------------------------

    def plan(
        self,
        tool: str | None,
        params: dict | None = None,
        summary: str = "",
        *,
        iteration: int | None = None,
    ) -> "_Ledger":
        """``iteration`` pins this plan record's iteration number instead of
        taking the next one from the monotonic counter — the escape hatch the
        multi-turn collision fixture needs, because a REAL ledger's numbers are
        not monotonic (the orchestrator restarts the counter at 0 on every turn
        while appending to the same file). Default behaviour is unchanged."""
        if iteration is None:
            self._iteration += 1
        else:
            self._iteration = iteration
        return self._write(
            "plan",
            {
                "iteration": self._iteration,
                "kind": "tool" if tool else "respond",
                "tool": tool,
                "summary": summary,
                "params": params or {},
                "meta": {"mode": "fake"},
            },
        )

    def preflight(self, tool: str, *, blocking: bool = False, findings: list[str] | None = None) -> "_Ledger":
        return self._write(
            "preflight",
            {
                "iteration": self._iteration,
                "tool": tool,
                "blocking": blocking,
                "disabled": [],
                "findings": findings or [],
            },
        )

    def action(self, tool: str, params: dict, *, status: str = "ok", error_type: str | None = None) -> "_Ledger":
        self._write("action_start", {"iteration": self._iteration, "tool": tool, "params": params})
        return self._write(
            "action_end",
            {"iteration": self._iteration, "tool": tool, "status": status, "error_type": error_type},
        )

    def postwrite(self, tool: str, *, blocking: bool = False, findings: list[str] | None = None) -> "_Ledger":
        return self._write(
            "postwrite",
            {
                "iteration": self._iteration,
                "tool": tool,
                "blocking": blocking,
                "disabled": [],
                "findings": findings if findings is not None else ["write_verification_failed"],
            },
        )

    def postrun(self, tool: str) -> "_Ledger":
        return self._write(
            "postrun",
            {"iteration": self._iteration, "tool": tool, "blocking": False, "disabled": [], "findings": []},
        )

    def refusal(self, tool: str, *, phase: str | None, error_type: str, message: str, tier: str | None = None) -> "_Ledger":
        payload = {"iteration": self._iteration, "tool": tool, "error_type": error_type, "message": message}
        if phase is not None:
            payload["phase"] = phase
        if tier is not None:
            payload["tier"] = tier
        return self._write("refusal", payload)

    def warning(self, tool: str, phase: str, messages: list[str]) -> "_Ledger":
        return self._write(
            "warning",
            {
                "iteration": self._iteration,
                "tool": tool,
                "phase": phase,
                "findings": [f"c{i}" for i, _ in enumerate(messages)],
                "messages": messages,
            },
        )

    def approval_requested(self, tool: str, params: dict) -> "_Ledger":
        return self._write(
            "user_confirm",
            {"stage": "requested", "iteration": self._iteration, "tool": tool, "params": params, "warnings": []},
        )

    def approved(self, tool: str, params: dict, reason: str | None) -> "_Ledger":
        return self._write(
            "user_confirm",
            {"stage": "approved", "approval_id": "aid", "tool": tool, "params": params, "summary": "", "reason": reason},
        )

    def declined(self, tool: str, params: dict, reason: str | None) -> "_Ledger":
        return self._write(
            "user_confirm",
            {"stage": "declined", "approval_id": "aid", "tool": tool, "params": params, "summary": "", "reason": reason},
        )

    # -- composites --------------------------------------------------------

    def applied_write(
        self,
        tool: str,
        params: dict,
        *,
        summary: str = "",
        reason: str | None = None,
        with_approval: bool = True,
        warnings: list[str] | None = None,
    ) -> "_Ledger":
        """The full real-world shape of an approved write: the proposal turn
        pauses, the human approves (recorded with their reason), the approve
        turn replays the same plan and dispatches it."""
        if with_approval:
            self.plan(tool, params, summary).preflight(tool).approval_requested(tool, params)
            self.approved(tool, params, reason)
        self.plan(tool, params, summary).preflight(tool)
        if warnings:
            self.warning(tool, "preflight", warnings)
        self.action(tool, params).postwrite(tool)
        return self

    def build(self) -> audit_trail.AuditLedger:
        return audit_trail.build_ledger(
            self.session_id, self.records, source_filename=f"trace_{self.session_id}.jsonl"
        )


def _doc_from(ledger: audit_trail.AuditLedger, language: str = "ko"):
    from docx import Document

    return Document(
        BytesIO(audit_trail.build_audit_trail_docx(ledger, language=language, generated_at=PINNED_GENERATED_AT))
    )


def _table_rows(table) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


def _all_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in _table_rows(table):
            parts.extend(row)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 1. Ledger folding: what counts as a parameter change
# ---------------------------------------------------------------------------


def test_applied_write_becomes_one_change_entry_with_target_attribute_and_unit():
    ledger = (
        _Ledger()
        .applied_write(
            "set_vehicle_input_volume",
            {"input_no": "1", "volume": 600.0},
            summary="교통량을 600으로 변경",
            reason="TIA 2026 시나리오 B 수요 반영",
        )
        .build()
    )

    assert len(ledger.changes) == 1
    entry = ledger.changes[0]
    assert entry.seq == 1
    assert entry.tool == "set_vehicle_input_volume"
    assert entry.change_type == "set"
    assert entry.target == "input_no=1"
    assert entry.attribute == "volume"
    assert entry.after == "600.0 veh/h"
    assert entry.before is None
    assert entry.outcome == "applied"
    assert entry.reason == "TIA 2026 시나리오 B 수요 반영"
    assert entry.postwrite_recorded is True
    assert entry.when == "2026-08-04 09:00:07Z"
    assert ledger.refusals == ()
    assert ledger.applied_count == 1
    assert ledger.reasoned_count == 1


def test_read_tier_actions_are_never_audited_as_changes():
    # A read tool dispatches with action_start/action_end but NEVER a
    # preflight record -- that absence is the classifier.
    ledger = (
        _Ledger()
        .plan("get_vehicle_inputs", {}, "현황 확인")
        .action("get_vehicle_inputs", {})
        .build()
    )
    assert ledger.changes == ()
    assert ledger.refusals == ()
    assert ledger.record_count == 3


def test_simulation_run_is_gated_but_is_not_a_parameter_change():
    ledger = (
        _Ledger()
        .plan("run_simulation", {"duration_s": 300}, "시뮬레이션 실행")
        .preflight("run_simulation")
        .action("run_simulation", {"duration_s": 300})
        .postrun("run_simulation")
        .build()
    )
    assert ledger.changes == ()


def test_failed_run_dispatch_is_excluded_by_tool_name_when_no_postrun_exists():
    ledger = (
        _Ledger()
        .plan("run_simulation", {"duration_s": 300}, "실행")
        .preflight("run_simulation")
        .action("run_simulation", {"duration_s": 300}, status="error", error_type="tool_execution_error")
        .build()
    )
    assert ledger.changes == ()


@pytest.mark.parametrize("tool", ["run_simulation", "run_demand_stress_sweep", "load_check"])
def test_no_failed_run_tier_dispatch_is_a_change_including_the_unprefixed_load_check(tool):
    """All THREE run-tier tools, not just the ``run_``-prefixed two.

    ``load_check`` (services/agent/tools_check.py, ``tier="run"``) loads the
    network in a dedicated Vissim instance and takes one step; it is gated like
    a run because it costs a licence seat, but it changes no parameter. Its
    failed dispatch leaves a preflight record, ``status="error"``, and neither
    a postwrite nor a postrun record — the exact shape the change classifier
    falls back to the tool NAME for. The ``run_`` prefix alone does not cover
    it, so a simulation check that changed nothing would land in the parameter
    change table; ``audit_trail._RUN_TIER_TOOLS`` is what keeps it out.
    """
    ledger = (
        _Ledger()
        .plan(tool, {}, "확인")
        .preflight(tool)
        .action(tool, {}, status="error", error_type="tool_execution_error")
        .build()
    )

    assert ledger.changes == ()
    assert ledger.refusals == ()
    assert ledger.is_empty is True


def test_multi_edit_session_numbers_rows_in_ledger_order_and_chains_before_values():
    ledger = (
        _Ledger()
        .applied_write("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, reason="1차 보정")
        .applied_write("set_signal_cycle", {"sc_no": "3", "cycle": 90}, reason="주기 상향")
        .applied_write("set_vehicle_input_volume", {"input_no": "1", "volume": 750.0}, reason="2차 보정")
        .applied_write("set_vehicle_input_volume", {"input_no": "2", "volume": 400.0}, with_approval=False)
        .build()
    )

    assert [(e.seq, e.tool, e.target, e.before, e.after) for e in ledger.changes] == [
        (1, "set_vehicle_input_volume", "input_no=1", None, "600.0 veh/h"),
        (2, "set_signal_cycle", "sc_no=3", None, "90 s"),
        (3, "set_vehicle_input_volume", "input_no=1", "600.0 veh/h", "750.0 veh/h"),
        (4, "set_vehicle_input_volume", "input_no=2", None, "400.0 veh/h"),
    ]
    # The 4th write never went through an approval, so it has no rationale --
    # and it must not steal one of the earlier approvals' reasons.
    assert [e.reason for e in ledger.changes] == ["1차 보정", "주기 상향", "2차 보정", None]


def test_colliding_iteration_numbers_across_turns_neither_merge_rows_nor_break_chaining():
    """A multi-turn session appends every turn to ONE trace file, and the
    orchestrator restarts its iteration counter at 0 on each turn — so a real
    ledger's iteration numbers are NOT monotonic and NOT unique (the shape
    below, a plan at a higher iteration followed by a plan back at 0, is the
    majority case in runtime/agent_sessions).

    ``build_ledger`` therefore folds on ``plan`` record boundaries and never
    keys on ``iteration``. This fixture is what makes that a tested property
    rather than a docstring claim: keying on the iteration id would merge the
    third write into the first (2 rows, wrong values) instead of yielding three
    distinct rows whose before-values chain straight across the turn boundary.
    """
    builder = _Ledger()

    def write(tool: str, params: dict, summary: str, *, iteration: int) -> None:
        builder.plan(tool, params, summary, iteration=iteration)
        builder.preflight(tool)
        builder.action(tool, params)
        builder.postwrite(tool)

    tool = "set_vehicle_input_volume"
    # -- turn 1: two iterations, numbered 0 then 1 -------------------------
    write(tool, {"input_no": "1", "volume": 600.0}, "1차 보정", iteration=0)
    write(tool, {"input_no": "1", "volume": 700.0}, "2차 보정", iteration=1)
    # -- turn 2: the counter restarts, so this plan collides with the first -
    write(tool, {"input_no": "1", "volume": 800.0}, "3차 보정", iteration=0)

    # The collision really is in the record stream being folded.
    assert [r["payload"]["iteration"] for r in builder.records if r["kind"] == "plan"] == [0, 1, 0]

    built = builder.build()

    assert len(built.changes) == 3
    assert [(e.seq, e.summary, e.before, e.after, e.outcome) for e in built.changes] == [
        (1, "1차 보정", None, "600.0 veh/h", "applied"),
        (2, "2차 보정", "600.0 veh/h", "700.0 veh/h", "applied"),
        # Chained from turn 1's LAST write of the same (tool, target,
        # attribute) even though this row's iteration id repeats turn 1's
        # first: the fold key is the plan boundary, not the number.
        (3, "3차 보정", "700.0 veh/h", "800.0 veh/h", "applied"),
    ]
    assert built.applied_count == 3
    # Per-iteration attribution: each row keeps its own action_start timestamp.
    assert len({e.ts for e in built.changes}) == 3
    assert [e.when for e in built.changes] == [
        "2026-08-04 09:00:03Z",
        "2026-08-04 09:00:08Z",
        "2026-08-04 09:00:13Z",
    ]


def test_legacy_ledger_without_any_approval_records_still_exports():
    """Ledgers written before the reason field existed have no
    ``stage="approved"`` records at all — every rationale cell is simply
    empty, and nothing else degrades."""
    ledger = (
        _Ledger()
        .applied_write("set_signal_offset", {"sc_no": "2", "offset": 12}, with_approval=False)
        .applied_write("set_signal_cycle", {"sc_no": "2", "cycle": 100}, with_approval=False)
        .build()
    )
    assert len(ledger.changes) == 2
    assert [e.reason for e in ledger.changes] == [None, None]
    assert ledger.reasoned_count == 0

    rows = _table_rows(_doc_from(ledger).tables[0])
    assert rows[1][-1] == "-"
    assert rows[2][-1] == "-"


# ---------------------------------------------------------------------------
# 2. Refusals — the selling point
# ---------------------------------------------------------------------------


def test_preflight_refusal_is_recorded_with_its_reason_and_never_as_a_change():
    ledger = (
        _Ledger()
        .plan("set_signal_cycle", {"sc_no": "3", "cycle": 400}, "주기를 400으로")
        .preflight("set_signal_cycle", blocking=True, findings=["signal_cycle_out_of_range"])
        .refusal(
            "set_signal_cycle",
            phase="preflight",
            error_type="guardrail_refused",
            message="주기 400s는 허용 범위(30~180s)를 벗어납니다.",
        )
        .build()
    )

    assert ledger.changes == ()
    assert len(ledger.refusals) == 1
    refusal = ledger.refusals[0]
    assert refusal.phase == "preflight"
    assert refusal.tool == "set_signal_cycle"
    assert refusal.target == "sc_no=3"
    assert refusal.error_type == "guardrail_refused"
    assert "허용 범위" in refusal.message


def test_refused_then_retried_keeps_both_the_refusal_and_the_applied_retry():
    ledger = _Ledger()
    ledger.plan("set_signal_cycle", {"sc_no": "3", "cycle": 400}, "주기를 400으로").preflight(
        "set_signal_cycle", blocking=True, findings=["signal_cycle_out_of_range"]
    ).refusal(
        "set_signal_cycle",
        phase="preflight",
        error_type="guardrail_refused",
        message="주기 400s는 허용 범위를 벗어납니다.",
    )
    ledger.applied_write("set_signal_cycle", {"sc_no": "3", "cycle": 120}, reason="가드레일 범위 내로 재설정")
    built = ledger.build()

    assert [e.after for e in built.changes] == ["120 s"]
    assert built.changes[0].reason == "가드레일 범위 내로 재설정"
    assert [(r.phase, r.tool) for r in built.refusals] == [("preflight", "set_signal_cycle")]


def test_postwrite_rollback_appears_as_a_rolled_back_change_and_a_refusal_row():
    ledger = _Ledger()
    ledger.plan("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, "적용")
    ledger.preflight("set_vehicle_input_volume")
    ledger.action("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0})
    ledger.postwrite("set_vehicle_input_volume", blocking=True, findings=["write_verification_failed"])
    ledger.refusal(
        "set_vehicle_input_volume",
        phase="postwrite",
        error_type="guardrail_refused",
        message="기록된 값과 판독 값이 다릅니다. 변경을 되돌렸습니다.",
    )
    built = ledger.build()

    assert len(built.changes) == 1
    assert built.changes[0].outcome == "rolled_back"
    assert built.applied_count == 0
    assert [(r.phase, r.error_type) for r in built.refusals] == [("postwrite", "guardrail_refused")]
    # The rolled-back value must NOT become the next change's "before".
    assert built.changes[0].before is None


def test_failed_dispatch_is_a_failed_change_carrying_its_error_type():
    ledger = _Ledger()
    ledger.plan("set_signal_cycle", {"sc_no": "3", "cycle": "abc"}, "적용")
    ledger.preflight("set_signal_cycle")
    ledger.action("set_signal_cycle", {"sc_no": "3", "cycle": "abc"}, status="error", error_type="invalid_params")
    ledger.refusal(
        "set_signal_cycle", phase="dispatch", error_type="invalid_params", message="cycle: value is not a valid integer"
    )
    built = ledger.build()

    assert len(built.changes) == 1
    assert built.changes[0].outcome == "failed"
    assert built.changes[0].error_type == "invalid_params"
    assert built.applied_count == 0
    # A failed change is never a refusal-table row (it WAS dispatched), so its
    # verification cell has to carry why it did not land.
    assert built.refusals == ()
    assert _table_rows(_doc_from(built).tables[0])[1][6] == (
        "적용 실패 (invalid_params) · cycle: value is not a valid integer"
    )


def test_declined_approval_is_recorded_with_the_reviewers_reason():
    ledger = _Ledger()
    ledger.plan("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, "제안")
    ledger.preflight("set_vehicle_input_volume")
    ledger.approval_requested("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0})
    ledger.declined("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, "관측 자료와 불일치")
    built = ledger.build()

    assert built.changes == ()
    assert len(built.refusals) == 1
    assert built.refusals[0].phase == "declined"
    assert built.refusals[0].reason == "관측 자료와 불일치"
    assert built.refusals[0].target == "input_no=1"


def test_approval_pause_alone_produces_no_row_so_approved_changes_are_never_double_counted():
    ledger = _Ledger()
    ledger.plan("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, "제안")
    ledger.preflight("set_vehicle_input_volume")
    ledger.approval_requested("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0})
    built = ledger.build()

    assert built.changes == ()
    assert built.refusals == ()
    assert built.is_empty is True


# ---------------------------------------------------------------------------
# 3. add / remove / composite verbs and unit derivation
# ---------------------------------------------------------------------------


def test_add_and_remove_verbs_render_as_object_creation_and_deletion():
    ledger = (
        _Ledger()
        .applied_write(
            "add_vehicle_input", {"link_no": 5, "volume_vph": 800.0, "name": "AM peak"}, with_approval=False
        )
        .applied_write("remove_detector", {"no": 12}, with_approval=False)
        .applied_write(
            "apply_edit_plan",
            {"steps": [{"tool": "set_signal_cycle", "params": {"sc_no": "3", "cycle": 100}}]},
            with_approval=False,
        )
        .build()
    )

    add_entry, remove_entry, batch_entry = ledger.changes
    assert batch_entry.change_type == "batch"
    assert batch_entry.attribute == "복합 편집"
    assert batch_entry.after.startswith("steps=")
    assert add_entry.change_type == "add"
    assert add_entry.attribute == "객체 생성"
    assert add_entry.target == "link_no=5"
    assert "volume_vph=800.0" in add_entry.after and "name=AM peak" in add_entry.after
    assert remove_entry.change_type == "remove"
    assert remove_entry.attribute == "객체 삭제"
    assert remove_entry.target == "no=12"
    assert remove_entry.after == "-"


@pytest.mark.parametrize(
    "name,unit",
    [
        ("volume", "veh/h"),
        ("volume_vph", "veh/h"),
        ("volume_pph", "ped/h"),
        ("cycle", "s"),
        ("offset", "s"),
        ("seconds", "s"),
        ("green_start_s", "s"),
        ("pos_m", "m"),
        ("lane_width_m", "m"),
        ("speed_kmh", "km/h"),
        ("lane_count", ""),
        ("closed", ""),
    ],
)
def test_units_are_derived_from_the_registry_naming_convention(name, unit):
    assert audit_trail.unit_for(name) == unit


def test_a_verb_whose_params_are_all_object_references_renders_without_a_fake_value():
    """``set_link_behavior_type_driving_behavior`` only wires existing objects
    together — every param is an identifier, so there is no value to show.
    (Shape taken from a real session ledger, runtime/agent_sessions.)"""
    ledger = _Ledger().applied_write(
        "set_link_behavior_type_driving_behavior",
        {"link_behavior_type_no": 101, "driving_behavior_no": 104, "veh_class_no": 10},
        with_approval=False,
    ).build()

    entry = ledger.changes[0]
    assert entry.target == "link_behavior_type_no=101, driving_behavior_no=104, veh_class_no=10"
    assert (entry.attribute, entry.after) == ("-", "-")
    # Never "기록 없음 → -": the target column already carries the whole story.
    assert _table_rows(_doc_from(ledger).tables[0])[1][4] == "-"


def test_long_param_values_are_elided_not_dropped():
    points = [[float(i), float(i)] for i in range(60)]
    ledger = _Ledger().applied_write(
        "set_des_speed_distribution_points", {"distribution_no": 4, "points": points}, with_approval=False
    ).build()

    entry = ledger.changes[0]
    assert entry.after.endswith("...")
    assert len(entry.after) <= 180
    assert entry.target == "distribution_no=4"


# ---------------------------------------------------------------------------
# 4. Reading a ledger file off disk
# ---------------------------------------------------------------------------


def test_read_ledger_reads_a_session_dir_and_skips_a_torn_line(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent_sessions"
    session_id = "abc123def456"
    session_dir = root / session_id
    session_dir.mkdir(parents=True)
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", root)

    builder = _Ledger(session_id).applied_write(
        "set_signal_cycle", {"sc_no": "3", "cycle": 120}, reason="근거 한 줄"
    )
    lines = [json.dumps(record, ensure_ascii=False) for record in builder.records]
    # A process killed mid-append leaves exactly this: one torn final line.
    lines.insert(3, '{"ts": 1.0, "kind": "plan", "payl')
    (session_dir / f"trace_{session_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    ledger = audit_trail.read_ledger(session_id)
    assert ledger is not None
    assert ledger.session_id == session_id
    assert ledger.source_filename == f"trace_{session_id}.jsonl"
    assert len(ledger.changes) == 1
    assert ledger.changes[0].reason == "근거 한 줄"


def test_read_ledger_returns_none_for_unknown_or_traversing_session_ids(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent_sessions"
    root.mkdir()
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", root)

    assert audit_trail.read_ledger("abc123def456") is None  # no such session dir
    assert audit_trail.read_ledger("../../etc") is None
    assert audit_trail.read_ledger("") is None
    assert audit_trail.read_ledger("NOTHEX!!") is None


def test_read_ledger_returns_none_when_the_session_has_no_trace_file(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent_sessions"
    (root / "abc123def456").mkdir(parents=True)
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", root)

    assert audit_trail.read_ledger("abc123def456") is None


# ---------------------------------------------------------------------------
# 5. DOCX rendering
# ---------------------------------------------------------------------------


def test_docx_section_has_both_tables_with_the_documented_columns():
    ledger = _Ledger()
    ledger.applied_write("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, reason="수요 반영")
    ledger.plan("set_signal_cycle", {"sc_no": "3", "cycle": 400}, "주기 400")
    ledger.preflight("set_signal_cycle", blocking=True, findings=["signal_cycle_out_of_range"])
    ledger.refusal(
        "set_signal_cycle", phase="preflight", error_type="guardrail_refused", message="허용 범위를 벗어납니다."
    )
    doc = _doc_from(ledger.build())

    assert len(doc.tables) == 2
    changes, refusals = _table_rows(doc.tables[0]), _table_rows(doc.tables[1])
    assert changes[0] == ["#", "시각(UTC)", "대상", "항목", "변경 전 → 변경 후", "도구", "검증", "근거"]
    assert changes[1] == [
        "1",
        "2026-08-04 09:00:07Z",
        "input_no=1",
        "volume",
        "기록 없음 → 600.0 veh/h",
        "set_vehicle_input_volume",
        "검증 통과 · 검사 1건",
        "수요 반영",
    ]
    assert refusals[0] == ["#", "시각(UTC)", "도구", "대상", "단계", "사유", "근거"]
    assert refusals[1][2:6] == ["set_signal_cycle", "sc_no=3", "사전 검사", "허용 범위를 벗어납니다."]


def test_docx_carries_the_section_heading_meta_summary_and_before_value_footnote():
    ledger = _Ledger().applied_write(
        "set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, reason="수요 반영"
    ).build()
    text = _all_text(_doc_from(ledger))

    assert "파라미터 변경 기록 및 근거" in text
    assert f"세션 {ledger.session_id}" in text
    assert f"레저 trace_{ledger.session_id}.jsonl" in text
    assert "생성 시각 2026-08-04 10:30:00Z" in text
    assert "요약: 적용 1건 · 롤백/실패 0건 · 거부·거절 0건 · 근거 기입 1건." in text
    assert "'변경 전' 값은 레저에 직접 기록되지 않으므로" in text


def test_docx_reports_postwrite_warnings_alongside_the_verdict():
    ledger = _Ledger().applied_write(
        "set_vehicle_input_volume",
        {"input_no": "1", "volume": 600.0},
        with_approval=False,
        warnings=["MRWA Table 5-9: gap 값이 권장 범위 상한에 근접합니다."],
    ).build()

    row = _table_rows(_doc_from(ledger).tables[0])[1]
    assert row[6] == "검증 통과 · 검사 1건 · 경고 1건"


def test_docx_for_an_empty_ledger_states_that_nothing_changed():
    ledger = audit_trail.build_ledger("abc123def456", [], source_filename="trace_abc123def456.jsonl")
    text = _all_text(_doc_from(ledger))

    assert "이 세션에는 적용된 파라미터 변경 기록이 없습니다." in text
    assert "거부·롤백·거절된 변경이 없습니다." in text


def test_docx_renders_en_and_ja_through_the_same_language_tables():
    ledger = _Ledger().applied_write(
        "set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, with_approval=False
    ).build()

    en = _doc_from(ledger, "en")
    # The report style renders Heading 1 in ALL CAPS (report_builder.
    # _section_heading) -- Korean/Japanese are unaffected by .upper().
    assert "PARAMETER CHANGE RECORD AND RATIONALE" in _all_text(en)
    assert _table_rows(en.tables[0])[0][4] == "Before → After"
    assert _table_rows(en.tables[0])[1][4] == "not recorded → 600.0 veh/h"

    ja = _doc_from(ledger, "ja")
    assert "パラメータ変更記録と根拠" in _all_text(ja)
    assert _table_rows(ja.tables[0])[0][1] == "時刻(UTC)"

    # An unknown code falls back to Korean (pick_language_text's contract).
    assert "파라미터 변경 기록 및 근거" in _all_text(_doc_from(ledger, "de"))


def test_docx_content_is_deterministic_for_the_same_ledger():
    """Same ledger in, same rendered content out — the report is
    regeneratable. (Byte equality is NOT claimed: python-docx saves through
    zipfile, which stamps members with the current local time.)"""
    ledger = _Ledger()
    ledger.applied_write("set_vehicle_input_volume", {"input_no": "1", "volume": 600.0}, reason="수요 반영")
    ledger.plan("set_signal_cycle", {"sc_no": "3", "cycle": 400}, "주기 400")
    ledger.preflight("set_signal_cycle", blocking=True)
    ledger.refusal("set_signal_cycle", phase="preflight", error_type="guardrail_refused", message="범위 초과")
    built = ledger.build()

    first, second = _all_text(_doc_from(built)), _all_text(_doc_from(built))
    assert first == second
    # ... and re-folding the same records yields the same ledger, too.
    assert _all_text(_doc_from(ledger.build())) == first


def test_section_can_be_appended_to_an_existing_document_with_a_table_counter():
    from docx import Document

    doc = Document()
    doc.add_paragraph("기존 보고서 본문")
    next_number = audit_trail.add_audit_trail_section(
        doc,
        _Ledger().applied_write("set_signal_cycle", {"sc_no": "3", "cycle": 120}, with_approval=False).build(),
        generated_at=PINNED_GENERATED_AT,
        table_number_start=4,
    )

    assert next_number == 6
    text = _all_text(doc)
    assert "기존 보고서 본문" in text
    assert "표 4 - 승인·적용된 파라미터 변경" in text
    assert "표 5 - 거부·롤백·거절 기록" in text
